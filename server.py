"""home_wrapped: your home's year, found in Home Assistant, painted as Wrapped story slides.

Nothing to set up beyond Home Assistant Core and an AI key. The widget looks
for itself:

* Energy, for the whole year, from long-term statistics: what the Energy
  dashboard is set up with (``energy/get_prefs``): solar, grid import and
  export, gas, water and the individual devices, month by month.
* Climate, for the whole year, from long-term statistics: every temperature
  sensor, placed in its room through the area registry.
* Life at home, from recorder history: doors opened, hours of light, hours of
  telly, days nobody was home (``zone.home``) and the busiest rooms (motion and
  occupancy sensors by area). Home Assistant keeps ten days of that by
  default, so the widget keeps its own daily ledger from the day it is
  installed, backfilled with whatever history there is; those figures say
  "since" until the ledger reaches back to 1 January.

From what it finds it builds a story of slides (an intro, the big numbers,
the top five, a leaderboard, a personality and the summary), and GPT Image
paints each slide whole, words and numbers included, from the real figures.
A slide is repainted every week (or month); the figures it was painted from
can be printed along its foot. Before the first painting, without a key, or
before Home Assistant has anything to say, the bundled sample slides show,
marked as samples.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app import artwork
from flask import current_app

PLUGIN_ID = "home_wrapped"
HERE = Path(__file__).resolve().parent
SAMPLE_DIR = HERE / "static" / "samples"
PALETTES = ("wrapped", "spectra", "gray")
SLIDES = ("intro", "number", "top", "leaderboard", "personality", "summary", "story")
FACTS_TTL_S = 3 * 3600
BACKFILL_DAYS_PER_FETCH = 45

_LOCK = threading.Lock()


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


# ----------------------------------------------------------------------
# Talking to Home Assistant
# ----------------------------------------------------------------------


class HA:
    """REST through Home Assistant Core, and the websocket API for what REST
    does not offer (long-term statistics, the Energy setup, the registries)."""

    def __init__(self, core: Any) -> None:
        self.core = core

    def states(self) -> list[dict[str, Any]]:
        return list(self.core.get_states())

    def history(self, ids: list[str], start: datetime, end: datetime) -> list[list[dict]]:
        if not ids:
            return []
        q = urllib.parse.urlencode(
            {
                "filter_entity_id": ",".join(ids),
                "end_time": end.astimezone().isoformat(),
                "minimal_response": "",
                "no_attributes": "",
            }
        )
        when = urllib.parse.quote(start.astimezone().isoformat())
        return list(self.core.request_json(f"/api/history/period/{when}?{q}", timeout=30) or [])

    def ws(self, requests: list[dict[str, Any]]) -> list[Any]:
        """Send each request on one websocket connection and return the
        results in order (None for a request Home Assistant refused)."""
        from websockets.sync.client import connect

        url = re.sub(r"^http", "ws", self.core.base_url()) + "/api/websocket"
        ssl_ctx = None
        if url.startswith("wss"):
            import ssl

            ssl_ctx = self.core._ssl_context() or ssl.create_default_context()
        out: list[Any] = []
        with connect(url, ssl=ssl_ctx, open_timeout=10, max_size=None) as sock:
            json.loads(sock.recv(timeout=10))  # auth_required
            sock.send(json.dumps({"type": "auth", "access_token": self.core.token()}))
            if json.loads(sock.recv(timeout=10)).get("type") != "auth_ok":
                raise RuntimeError("Home Assistant refused the access token")
            for i, req in enumerate(requests, start=1):
                sock.send(json.dumps({"id": i, **req}))
                while True:
                    msg = json.loads(sock.recv(timeout=60))
                    if msg.get("id") == i and msg.get("type") == "result":
                        out.append(msg.get("result") if msg.get("success") else None)
                        break
        return out


# ----------------------------------------------------------------------
# Numbers
# ----------------------------------------------------------------------

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip


def _plain(n: float) -> str:
    if abs(n - round(n)) < 1e-9 or abs(n) >= 100:
        return f"{round(n):,}"
    return f"{n:,.1f}"


def energy_text(kwh: float) -> tuple[str, str]:
    if abs(kwh) >= 1000:
        return f"{kwh / 1000:,.1f}", "MWh"
    return _plain(kwh), "kWh"


def to_kwh(value: float, unit: str) -> float:
    return value * {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0, "gwh": 1e6}.get(unit.lower(), 1.0)


def fact(name: str, value: str, unit: str = "", raw: float = 0.0, since: str = "") -> dict:
    return {"name": name, "value": value, "unit": unit, "raw": raw, "since": since}


def ranked(rows: list[tuple[str, float, str, str]], share: bool) -> list[dict[str, Any]]:
    """(name, raw, value, unit) rows, biggest first, five at most; with
    ``share`` each shows its percentage of the whole list."""
    rows = sorted((r for r in rows if r[1] > 0), key=lambda r: r[1], reverse=True)
    total = sum(r[1] for r in rows) or 1.0
    out = []
    for name, raw, value, unit in rows[:5]:
        if share:
            value, unit = f"{round(100 * raw / total)}%", ""
        out.append({"name": name, "value": value, "unit": unit, "raw": raw})
    return out


def since_text(first: date, year: int) -> str:
    if first <= date(year, 1, 1):
        return ""
    return f"since {first.day} {MONTHS[first.month - 1][:3]}"


def when_of(v: Any) -> datetime:
    """A statistics or history timestamp (epoch ms or ISO) as local naive time."""
    if isinstance(v, int | float):
        return datetime.fromtimestamp(v / 1000)
    parsed = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


# ----------------------------------------------------------------------
# Energy and climate: long-term statistics
# ----------------------------------------------------------------------


def energy_ids(prefs: dict[str, Any]) -> dict[str, list[str]]:
    """The statistics the Energy dashboard is set up with, by role."""
    roles: dict[str, list[str]] = {"solar": [], "import": [], "export": [], "gas": [], "water": []}
    for src in prefs.get("energy_sources") or []:
        kind = src.get("type")
        if kind == "solar" and src.get("stat_energy_from"):
            roles["solar"].append(src["stat_energy_from"])
        elif kind == "grid":
            for f in src.get("flow_from") or []:
                if f.get("stat_energy_from"):
                    roles["import"].append(f["stat_energy_from"])
            for f in src.get("flow_to") or []:
                if f.get("stat_energy_to"):
                    roles["export"].append(f["stat_energy_to"])
            if src.get("stat_energy_from"):
                roles["import"].append(src["stat_energy_from"])
            if src.get("stat_energy_to"):
                roles["export"].append(src["stat_energy_to"])
        elif kind in ("gas", "water") and src.get("stat_energy_from"):
            roles[kind].append(src["stat_energy_from"])
    return roles


def energy_facts(ha: HA, names: dict[str, str], year: int, now: datetime) -> dict[str, Any]:
    (prefs,) = ha.ws([{"type": "energy/get_prefs"}])
    if not prefs:
        return {}
    roles = energy_ids(prefs)
    devices = [
        (d["stat_consumption"], d.get("name") or "")
        for d in prefs.get("device_consumption") or []
        if d.get("stat_consumption")
    ]
    ids = sorted({i for v in roles.values() for i in v} | {d for d, _ in devices})
    if not ids:
        return {}
    meta, stats = ha.ws(
        [
            {"type": "recorder/get_statistics_metadata", "statistic_ids": ids},
            {
                "type": "recorder/statistics_during_period",
                "start_time": datetime(year, 1, 1).astimezone().isoformat(),
                "end_time": now.astimezone().isoformat(),
                "statistic_ids": ids,
                "period": "month",
                "types": ["change"],
            },
        ]
    )
    units = {
        m["statistic_id"]: str(m.get("statistics_unit_of_measurement") or "") for m in meta or []
    }
    stats = stats or {}

    def months(sid: str) -> dict[int, float]:
        out: dict[int, float] = {}
        for row in stats.get(sid) or []:
            m = when_of(row.get("start")).month
            out[m] = out.get(m, 0.0) + float(row.get("change") or 0.0)
        return out

    def kwh(sid: str) -> dict[int, float]:
        return {m: to_kwh(v, units.get(sid, "kWh")) for m, v in months(sid).items()}

    def total(role: str) -> float:
        return sum(sum(kwh(sid).values()) for sid in roles[role])

    facts: dict[str, Any] = {}
    lists: dict[str, Any] = {}
    solar, imp, exp = total("solar"), total("import"), total("export")
    if solar > 0:
        facts["solar"] = fact("Solar generated", *energy_text(solar), raw=solar)
        by_month: dict[int, float] = {}
        for sid in roles["solar"]:
            for m, v in kwh(sid).items():
                by_month[m] = by_month.get(m, 0.0) + v
        rows = [(MONTHS[m - 1], v, *energy_text(v)) for m, v in by_month.items()]
        lists["sunniest"] = {"title": "Sunniest months", "rows": ranked(rows, False)}
    used = imp + solar - exp
    if imp > 0:
        facts["grid"] = fact("Grid import", *energy_text(imp), raw=imp)
    if used > 0:
        facts["used"] = fact("Energy used", *energy_text(used), raw=used)
        if solar > 0:
            pct = max(0.0, min(100.0, 100 * (1 - imp / used)))
            facts["self"] = fact("Self-powered", f"{round(pct)}%", raw=pct)
    if exp > 0:
        facts["export"] = fact("Sent to the grid", *energy_text(exp), raw=exp)
    for role, label in (("gas", "Gas used"), ("water", "Water used")):
        amount = sum(sum(months(sid).values()) for sid in roles[role])
        if amount > 0:
            facts[role] = fact(label, _plain(amount), units.get(roles[role][0], ""), raw=amount)
    rows = []
    for sid, label in devices:
        v = sum(kwh(sid).values())
        name = label or names.get(sid) or sid.split(".")[-1].replace("_", " ").title()
        rows.append((name, v, *energy_text(v)))
    if rows:
        lists["devices"] = {"title": "Biggest users", "rows": ranked(rows, False)}
    return {"facts": facts, "lists": lists}


def climate_facts(
    ha: HA, states: list[dict[str, Any]], areas: dict[str, str], year: int, now: datetime
) -> dict[str, Any]:
    temps = [
        s["entity_id"]
        for s in states
        if str(s.get("entity_id", "")).startswith("sensor.")
        and (s.get("attributes") or {}).get("device_class") == "temperature"
        and (s.get("attributes") or {}).get("state_class") == "measurement"
        and areas.get(s["entity_id"])
    ]
    if not temps:
        return {}
    first = next(s for s in states if s["entity_id"] == temps[0])
    unit = str((first.get("attributes") or {}).get("unit_of_measurement") or "°C")
    (days,) = ha.ws(
        [
            {
                "type": "recorder/statistics_during_period",
                "start_time": datetime(year, 1, 1).astimezone().isoformat(),
                "end_time": now.astimezone().isoformat(),
                "statistic_ids": temps,
                "period": "day",
                "types": ["mean", "min", "max"],
            }
        ]
    )
    by_area: dict[str, list[float]] = {}
    hot: tuple[datetime | None, float] = (None, -1e9)
    cold: tuple[datetime | None, float] = (None, 1e9)
    for sid in temps:
        for row in (days or {}).get(sid) or []:
            when = when_of(row.get("start"))
            if row.get("mean") is not None:
                by_area.setdefault(areas[sid], []).append(float(row["mean"]))
            if row.get("max") is not None and float(row["max"]) > hot[1]:
                hot = (when, float(row["max"]))
            if row.get("min") is not None and float(row["min"]) < cold[1]:
                cold = (when, float(row["min"]))
    facts: dict[str, Any] = {}
    lists: dict[str, Any] = {}
    means = {a: sum(v) / len(v) for a, v in by_area.items() if v}
    if len(means) >= 2:
        rows = [(a, m, f"{m:.1f}", unit) for a, m in means.items()]
        lists["warmest"] = {"title": "Warmest rooms", "rows": ranked(rows, False)}
    if hot[0] is not None:
        label = f"Hottest day, {hot[0].day} {MONTHS[hot[0].month - 1]}"
        facts["hottest"] = fact(label, f"{hot[1]:.1f}", unit, raw=hot[1])
    if cold[0] is not None:
        label = f"Coldest day, {cold[0].day} {MONTHS[cold[0].month - 1]}"
        facts["coldest"] = fact(label, f"{cold[1]:.1f}", unit, raw=cold[1])
    return {"facts": facts, "lists": lists}


# ----------------------------------------------------------------------
# Life at home: recorder history kept in a daily ledger
# ----------------------------------------------------------------------


def trackers(states: list[dict[str, Any]], areas: dict[str, str]) -> dict[str, list[str]]:
    """The entities each tally is drawn from."""
    out: dict[str, list[str]] = {"doors": [], "lights": [], "tv": [], "motion": [], "zone": []}
    for s in states:
        eid = str(s.get("entity_id") or "")
        attrs = s.get("attributes") or {}
        dc = attrs.get("device_class")
        name = f" {str(attrs.get('friendly_name') or eid).lower()} "
        if eid.startswith("binary_sensor.") and dc in ("door", "garage_door", "opening"):
            if "window" not in name:
                out["doors"].append(eid)
        elif eid.startswith("binary_sensor.") and dc in ("motion", "occupancy", "presence"):
            if areas.get(eid):
                out["motion"].append(eid)
        elif eid.startswith("light.") and not attrs.get("entity_id"):
            out["lights"].append(eid)  # not a light group, or it would count twice
        elif eid.startswith("media_player.") and (
            dc == "tv" or " tv " in name or "television" in name
        ):
            out["tv"].append(eid)
        elif eid == "zone.home":
            out["zone"].append(eid)
    return out


def on_seconds(rows: list[dict], on: set[str], start: datetime, end: datetime) -> float:
    total, cur, since = 0.0, None, start
    for r in rows:
        when = when_of(r.get("last_changed") or r.get("last_updated") or start.isoformat())
        when = min(max(when, start), end)
        if cur in on:
            total += (when - since).total_seconds()
        cur, since = str(r.get("state")), when
    if cur in on:
        total += (end - since).total_seconds()
    return total


def rises(rows: list[dict], on: set[str]) -> int:
    n, prev = 0, None
    for r in rows:
        st = str(r.get("state"))
        if st in on and prev is not None and prev not in on:
            n += 1
        prev = st
    return n


def tally_day(ha: HA, track: dict[str, list[str]], areas: dict[str, str], day: date) -> dict:
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    by_id: dict[str, list[dict]] = {}
    for rows in ha.history([i for v in track.values() for i in v], start, end):
        if rows and rows[0].get("entity_id"):
            by_id[rows[0]["entity_id"]] = rows
    rooms: dict[str, int] = {}
    for eid in track["motion"]:
        n = rises(by_id.get(eid, []), {"on"})
        if n:
            rooms[areas[eid]] = rooms.get(areas[eid], 0) + n
    tv_on = {"on", "playing", "paused", "idle"}
    return {
        "doors": sum(rises(by_id.get(e, []), {"on"}) for e in track["doors"]),
        "lights_h": round(
            sum(on_seconds(by_id.get(e, []), {"on"}, start, end) for e in track["lights"]) / 3600, 2
        ),
        "tv_h": round(
            sum(on_seconds(by_id.get(e, []), tv_on, start, end) for e in track["tv"]) / 3600, 2
        ),
        "away_h": round(
            sum(on_seconds(by_id.get(e, []), {"0"}, start, end) for e in track["zone"]) / 3600, 2
        ),
        "rooms": rooms,
        "seen": bool(by_id),
    }


def update_ledger(
    ha: HA, path: Path, track: dict[str, list[str]], areas: dict[str, str], today: date
) -> dict[str, Any]:
    """Add the finished days the ledger lacks, newest first (the history most
    at risk of being purged), back to 1 January or as far as Home Assistant
    still remembers; a bounded number per fetch so a first run stays quick."""
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}
    days: dict[str, Any] = ledger.setdefault("days", {})
    floor = ledger.get("empty_before") or ""
    jan1 = date(today.year, 1, 1)
    missing = [
        d
        for d in (jan1 + timedelta(days=i) for i in range((today - jan1).days))
        if d.isoformat() not in days and d.isoformat() >= floor
    ]
    for done, d in enumerate(reversed(missing)):
        if done >= BACKFILL_DAYS_PER_FETCH:
            break
        row = tally_day(ha, track, areas, d)
        if not row.pop("seen"):
            # Nothing that old is left in Home Assistant; nothing older will be either.
            ledger["empty_before"] = (d + timedelta(days=1)).isoformat()
            break
        days[d.isoformat()] = row
    for k in [k for k in days if not k.startswith(str(today.year))]:
        days.pop(k)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger), encoding="utf-8")
    except OSError:
        pass
    return ledger


def life_facts(ledger: dict[str, Any], track: dict[str, list[str]], year: int) -> dict[str, Any]:
    days = ledger.get("days") or {}
    if not days:
        return {}
    since = since_text(min(date.fromisoformat(k) for k in days), year)
    tot = {
        k: sum(float(v.get(k) or 0) for v in days.values())
        for k in ("doors", "lights_h", "tv_h", "away_h")
    }
    facts: dict[str, Any] = {}
    lists: dict[str, Any] = {}
    if track["doors"] and tot["doors"]:
        facts["doors"] = fact(
            "Door openings", f"{round(tot['doors']):,}", raw=tot["doors"], since=since
        )
        facts["doors"]["per_day"] = round(tot["doors"] / len(days), 1)
    if track["lights"] and tot["lights_h"]:
        lights = round(tot["lights_h"])
        facts["lights"] = fact("Hours of light", f"{lights:,}", "hours", raw=lights, since=since)
    if track["tv"] and tot["tv_h"]:
        tv = round(tot["tv_h"])
        facts["tv"] = fact("Hours of telly", f"{tv:,}", "hours", raw=tv, since=since)
    if track["zone"]:
        away = round(tot["away_h"] / 24, 1)
        unit = "day" if away == 1 else "days"
        facts["away"] = fact("Days nobody was home", _plain(away), unit, raw=away, since=since)
    rooms: dict[str, int] = {}
    for v in days.values():
        for a, c in (v.get("rooms") or {}).items():
            rooms[a] = rooms.get(a, 0) + int(c)
    if len(rooms) >= 2:
        rows = ranked([(a, c, str(c), "") for a, c in rooms.items()], True)
        lists["rooms"] = {"title": "Top rooms", "rows": rows, "since": since}
    return {"facts": facts, "lists": lists}


# ----------------------------------------------------------------------
# The year, gathered
# ----------------------------------------------------------------------

FACT_ORDER = (
    "solar", "self", "doors", "lights", "tv", "away", "used",
    "grid", "export", "hottest", "coldest", "gas", "water",
)  # fmt: skip
LIST_ORDER = ("rooms", "devices", "warmest", "sunniest")


def areas_of(ha: HA) -> tuple[dict[str, str], dict[str, str]]:
    """Each entity's room (its own area, else its device's) and display name."""
    area_list, entities, devices = ha.ws(
        [
            {"type": "config/area_registry/list"},
            {"type": "config/entity_registry/list"},
            {"type": "config/device_registry/list"},
        ]
    )
    area_name = {a["area_id"]: a["name"] for a in area_list or []}
    dev_area = {d["id"]: d.get("area_id") for d in devices or []}
    out: dict[str, str] = {}
    names: dict[str, str] = {}
    for e in entities or []:
        aid = e.get("area_id") or dev_area.get(e.get("device_id"))
        if aid in area_name:
            out[e["entity_id"]] = area_name[aid]
        if e.get("name"):
            names[e["entity_id"]] = e["name"]
    return out, names


def gather(ha: HA, data_dir: Path, now: datetime) -> dict[str, Any]:
    """Everything found this year; a part that fails is noted, not fatal."""
    year = now.year
    states = ha.states()
    names = {
        s["entity_id"]: str((s.get("attributes") or {}).get("friendly_name") or "") for s in states
    }
    notes: list[str] = []
    facts: dict[str, Any] = {}
    lists: dict[str, Any] = {}
    try:
        areas, reg_names = areas_of(ha)
        names.update(reg_names)
    except Exception as err:
        areas = {}
        notes.append(f"rooms: {err}")
    parts = (
        ("energy", lambda: energy_facts(ha, names, year, now)),
        ("climate", lambda: climate_facts(ha, states, areas, year, now)),
        (
            "history",
            lambda: life_facts(
                update_ledger(ha, data_dir / "ledger.json", track, areas, now.date()), track, year
            ),
        ),
    )
    track = trackers(states, areas)
    for part, fn in parts:
        try:
            got = fn()
            facts.update(got.get("facts") or {})
            lists.update(got.get("lists") or {})
        except Exception as err:
            notes.append(f"{part}: {err}")
    return {
        "facts": [dict(facts[k], key=k) for k in FACT_ORDER if k in facts][:6],
        "lists": [dict(lists[k], key=k) for k in LIST_ORDER if k in lists][:2],
        "notes": notes,
        "at": now.timestamp(),
    }


def cached_gather(ha: HA, data_dir: Path, now: datetime) -> dict[str, Any]:
    path = data_dir / "year.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if now.timestamp() - float(cached.get("at") or 0) < FACTS_TTL_S:
            return cached
    except (OSError, ValueError):
        pass
    with _LOCK:
        got = gather(ha, data_dir, now)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(got), encoding="utf-8")
        except OSError:
            pass
    return got


# ----------------------------------------------------------------------
# The personality
# ----------------------------------------------------------------------


def personality(facts: list[dict[str, Any]]) -> dict[str, str]:
    """A name for the year, chosen by rules from what was found."""
    f = {x["key"]: x for x in facts}
    if "self" in f and f["self"]["raw"] >= 50:
        return {
            "name": "The Sun Harvester",
            "why": f"{f['self']['value']} of your power came from your own roof.",
        }
    if "away" in f and f["away"]["raw"] >= 40:
        return {
            "name": "The Globetrotter",
            "why": f"Nobody was home for {f['away']['value']} days.",
        }
    if "doors" in f and f["doors"].get("per_day", 0) >= 15:
        return {"name": "Grand Central", "why": f"The doors opened {f['doors']['value']} times."}
    if "tv" in f and f["tv"]["raw"] >= 900:
        return {"name": "The Couch Captain", "why": f"{f['tv']['value']} hours of telly."}
    if "away" in f and f["away"]["raw"] <= 7:
        return {"name": "The Homebody", "why": "Somebody was almost always home."}
    if "lights" in f:
        return {"name": "The Lighthouse", "why": f"{f['lights']['value']} hours of light."}
    return {"name": "The Steady Home", "why": "A calm year, well kept."}


# ----------------------------------------------------------------------
# The slides
# ----------------------------------------------------------------------

LOOK = (
    "A story slide from an annual year-in-review, in the visual language of the big music "
    "streaming app's yearly recap (no logos, no brand names, no app interface): a thin "
    "segmented story progress bar across the very top with {segments} segments, the first "
    "{done} filled; huge bold condensed sans-serif type stacked and set big, some words "
    "rotated or overlapping a shape; one or two big abstract organic shapes (a blob, a "
    "squiggle, a starburst or concentric rings); flat saturated colour, high contrast, "
    "playful and confident, subtle print grain, generous space around the type. Flat, "
    "front-on, full-bleed. Spell every word exactly as given and use exactly these numbers, "
    "with no other numbers anywhere. Keep the bottom tenth of the slide as a plain band of "
    "the background colour with nothing at all in it: no type, no shapes, no illustration."
)
PALETTE_WORDS = {
    "wrapped": "deep black and dark purple grounds with electric lime green, hot pink, "
    "tangerine and bright violet, white type",
    "spectra": "flat bright red, lemon yellow, royal blue and leaf green with orange, on "
    "black or white, black or white type",
    "gray": "strictly greyscale: black, white and three flat greys, halftone dots, no colour",
}
LONG = {"h": "hours"}


def fig(x: dict[str, Any]) -> str:
    return f"{x['value']} {LONG.get(x['unit'], x['unit'])}".strip()


def label(x: dict[str, Any]) -> str:
    return f"{x['name']} {x['since']}".strip() if x.get("since") else x["name"]


def numbered(rows: list[dict[str, Any]], values: bool) -> str:
    return ", ".join(
        f"'{i + 1} {r['name']}" + (f" {fig(r)}'" if values else "'") for i, r in enumerate(rows)
    )


def story(year: dict[str, Any]) -> list[tuple[str, int]]:
    """The slides in order: intro, every big number, the lists, the
    personality, the summary."""
    seq: list[tuple[str, int]] = [("intro", 0)]
    seq += [("number", i) for i in range(len(year["facts"]))]
    if year["lists"]:
        seq.append(("top", 0))
    if len(year["lists"]) > 1:
        seq.append(("leaderboard", 1))
    if year["facts"]:
        seq.append(("personality", 0))
    seq.append(("summary", 0))
    return seq


def slide_prompt(p: dict[str, Any]) -> str:
    facts, lists = p["facts"], p["lists"]
    t = f"'{p['title']} {p['year']}'"
    home = p.get("home") or "a cosy house"
    pets = p.get("pets") or ""
    hero = f"{home} at night with warm lit windows" + (
        f" and {pets} in the windows" if pets else ""
    )
    kind, i = p["slide"], p["index"]
    if kind == "intro":
        body = (
            f"The INTRO slide: 'Your' small, then {t} huge and stacked, then 'is here.' and a "
            f"small line 'A year in the life of your home.' A flat illustration of {hero}."
        )
    elif kind == "number":
        x = facts[i]
        rate = f", and a small line 'That's {x['per_day']} a day.'" if x.get("per_day") else ""
        body = (
            f"A BIG-NUMBER slide: '{label(x)}' as the heading, then '{x['value']}' enormous, "
            f"then '{LONG.get(x['unit'], x['unit'])}' under it{rate}. A cheerful flat "
            f"illustration of something that stands for '{x['name']}'. Small {t} in a corner."
        )
    elif kind in ("top", "leaderboard"):
        lst = lists[i]
        head = f"Your {lst['title'].lower()}" + (f" {lst['since']}" if lst.get("since") else "")
        if kind == "top":
            body = (
                f"A TOP FIVE slide: '{head}' in huge stacked type, then a numbered list where "
                f"number 1 is much bigger than the rest: {numbered(lst['rows'], True)}. Small "
                f"{t} in a corner."
            )
        else:
            body = (
                f"A LEADERBOARD slide: the heading '{head}', then thick horizontal bars of "
                "decreasing length, longest first, each with its name and number: "
                f"{numbered(lst['rows'], True)}. Small {t} in a corner."
            )
    elif kind == "personality":
        per = p["personality"]
        who = home + (f" with {pets}" if pets else "")
        body = (
            f"A PERSONALITY slide: small 'Your home's personality is', then '{per['name']}' "
            f"huge inside a big starburst, then the line '{per['why']}'. A playful flat "
            f"illustration of {who}. Small {t} in a corner."
        )
    else:
        stats = " and ".join(f"'{label(x)}' '{fig(x)}'" for x in facts[:2])
        cols = "; ".join(
            f"'{lst['title']}' then {numbered(lst['rows'], False)}" for lst in lists[:2]
        )
        body = (
            f"The final SUMMARY slide, the one people share: {t} at the top, a square flat "
            f"illustration of {hero}, then two columns of numbered lists: {cols}. Then two "
            f"big stats side by side: {stats}."
        )
    shape = "landscape" if p["land"] else "portrait"
    look = LOOK.format(segments=p["segments"], done=p["position"] + 1)
    return f"{look} A {shape} slide. Palette: {PALETTE_WORDS[p['palette']]}. {body}"


def figures(p: dict[str, Any]) -> list[str]:
    """The numbers a slide carries, for the strip along its foot."""
    kind, i = p["slide"], p["index"]
    if kind == "number":
        return [f"{label(p['facts'][i])} {fig(p['facts'][i])}"]
    if kind in ("top", "leaderboard"):
        return [f"{r['name']} {fig(r)}" for r in p["lists"][i]["rows"]]
    if kind == "summary":
        return [f"{label(x)} {fig(x)}" for x in p["facts"][:2]]
    if kind == "personality":
        return [p["personality"]["why"]]
    return []


def slide_size(cell_w: int, cell_h: int) -> tuple[int, int]:
    """The cell's shape with the long side at 1600, in multiples of 16."""
    if cell_w > 0 and cell_h > 0:
        if cell_w >= cell_h:
            return 1600, max(480, round(1600 * cell_h / cell_w / 16) * 16)
        return max(480, round(1600 * cell_w / cell_h / 16) * 16), 1600
    return 1200, 1600


def paint(job: artwork.Job) -> tuple[bytes, dict[str, Any]]:
    p = job.payload
    aspect = "4:3" if p["land"] else "3:4"
    raw = job.fal.generate(
        "gpt_image_t2i", slide_prompt(p), aspect=aspect, quality="high", size=tuple(p["size"])
    )
    return raw, {
        "credit": "Painted by GPT Image",
        "figures": figures(p),
        "painted": date.today().isoformat(),
    }


ART = artwork.Collection(
    PLUGIN_ID,
    "slides",
    make=paint,
    label="Home Wrapped",
    title="Painting the slides",
    default_cents=200,
    typical_cents=20,
    parallel=2,
    per_preview=1,
)


# ----------------------------------------------------------------------
# fetch
# ----------------------------------------------------------------------


def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip()
    return v if v in allowed else default


def period_of(cadence: str, now: datetime) -> str:
    return f"m{now.month:02d}" if cadence == "monthly" else f"w{now.isocalendar()[1]:02d}"


def _last_path(data_dir: Path, cell: str, slide: str, index: int) -> Path:
    name = re.sub(r"[^a-z0-9_-]+", "_", f"{cell or 'default'}-{slide}-{index}".lower())[:80]
    return data_dir / "last" / f"{name}.json"


def remember(data_dir: Path, cell: str, slide: str, index: int, key: str) -> None:
    path = _last_path(data_dir, cell, slide, index)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": key}), encoding="utf-8")
    except OSError:
        pass


def recall(data_dir: Path, cell: str, slide: str, index: int) -> str:
    try:
        raw = _last_path(data_dir, cell, slide, index).read_text(encoding="utf-8")
        return str(json.loads(raw).get("key") or "")
    except (OSError, ValueError):
        return ""


def sample_file(slide: str, palette: str, land: bool) -> str:
    """The bundled sample slide nearest to what was asked for."""
    kind = "summary" if slide == "story" else slide
    shape = "land" if land else "port"
    for name in (f"{kind}-{palette}-{shape}", f"{kind}-{palette}-port", f"{kind}-wrapped-port"):
        if (SAMPLE_DIR / f"{name}.jpg").exists():
            return f"{name}.jpg"
    return "summary-wrapped-port.jpg"


def choose(slide: str, year: dict[str, Any], stat: int, now: datetime) -> tuple[str, int]:
    seq = story(year)
    if slide == "story":
        return seq[int(now.timestamp() // 3600) % len(seq)]
    if slide == "number":
        if not year["facts"]:
            return "summary", 0
        return "number", max(0, min(stat, len(year["facts"]) - 1))
    if slide in ("top", "leaderboard"):
        if not year["lists"]:
            return "summary", 0
        return slide, 1 if slide == "leaderboard" and len(year["lists"]) > 1 else 0
    if slide == "personality" and not year["facts"]:
        return "summary", 0
    return slide, 0


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    now = datetime.now()
    palette = _pick(options.get("palette"), PALETTES, "wrapped")
    slide = _pick(options.get("slide"), SLIDES, "summary")
    title = str(options.get("title") or "").strip() or "Home Wrapped"
    cell_w, cell_h = int(ctx.get("cell_w") or 0), int(ctx.get("cell_h") or 0)
    land = bool(cell_w and cell_h and cell_w > cell_h * 1.25)
    out: dict[str, Any] = {
        "strip": options.get("show_strip") is not False,
        "sample": sample_file(slide, palette, land),
    }

    core = _core()
    if core is None or not core.is_configured():
        out["note"] = "A sample. Set up Home Assistant Core to paint your own year."
        return out
    if not ctx.get("data_dir"):
        return out
    art = ART.open(ctx, settings)
    if not art.key:
        out["note"] = "A sample. Add an AI key under Settings › Generative AI to paint yours."
        return out

    data_dir = art.data_dir
    try:
        year = cached_gather(HA(core), data_dir, now)
    except Exception as err:
        out["note"] = f"Home Assistant: {core.coerce_error(err)}"
        return out
    if not year["facts"] and not year["lists"]:
        out["note"] = (
            "Nothing to wrap yet: no energy, climate or activity history in Home Assistant."
        )
        return out

    try:
        stat = int(options.get("stat") or 1) - 1
    except (TypeError, ValueError):
        stat = 0
    kind, index = choose(slide, year, stat, now)
    seq = story(year)
    position = seq.index((kind, index)) if (kind, index) in seq else len(seq) - 1

    size = slide_size(cell_w, cell_h)
    home = str(options.get("home") or "").strip()
    pets = str(options.get("pets") or "").strip()
    digest = hashlib.sha1(json.dumps([title, palette, home, pets, size]).encode()).hexdigest()[:10]
    period = period_of(str(options.get("cadence") or "weekly"), now)
    key = f"{kind}-{index}-{now.year}-{period}-{digest}"
    payload = {
        "slide": kind,
        "index": index,
        "title": title,
        "year": now.year,
        "palette": palette,
        "land": land,
        "size": list(size),
        "home": home,
        "pets": pets,
        "facts": year["facts"],
        "lists": year["lists"],
        "personality": personality(year["facts"]),
        "segments": len(seq),
        "position": position,
    }
    got = art.request(key, payload, title=f"{title}: the {kind} slide")
    if got.ready:
        remember(data_dir, art.cell, kind, index, key)
    shown = key if got.ready else recall(data_dir, art.cell, kind, index)
    if shown and ART.path(shown, data_dir) is not None:
        meta = ART.meta(shown, data_dir)
        out["painted"] = {
            "url": art.url(shown, px=1600),
            "figures": meta.get("figures") or [],
            "when": str(meta.get("painted") or ""),
        }
    elif got.state in ("painting", "waiting", "held"):
        out["note"] = "Painting your slide. It replaces this sample when it is ready."
    else:
        notes = art.notes()
        out["note"] = notes[0] if notes else "A sample."
    art.finish(ctx)
    out.update(art.live())
    return out
