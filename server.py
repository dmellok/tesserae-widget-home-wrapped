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

    def history(
        self, ids: list[str], start: datetime, end: datetime, *, attributes: bool = False
    ) -> list[list[dict]]:
        """State history; ``attributes`` keeps every row's attributes (an
        automation's runs are only in its last_triggered)."""
        if not ids:
            return []
        args = {"filter_entity_id": ",".join(ids), "end_time": end.astimezone().isoformat()}
        if not attributes:
            args |= {"minimal_response": "", "no_attributes": ""}
        q = urllib.parse.urlencode(args)
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


# Temperatures that are not a room's: a printer's hot end, an oven, a chip.
NOT_A_ROOM = re.compile(
    r"printer|nozzle|hot ?end|extruder|heat ?bed|heated bed|print bed|chamber|filament|"
    r"\bcpu\b|\bgpu\b|\bsoc\b|chip|processor|\bcore\b|mainboard|motherboard|internal|device temp|"
    r"battery|oven|fridge|freezer|refrigerator|water|boiler|tank|cylinder|pool|spa|hot ?tub|"
    r"probe|grill|bbq|smoker|meat|kettle|coffee|server|\bnas\b|disk|\bssd\b|\bhdd\b|router|"
    r"inverter|motor|compressor|pipe|flow|return|exhaust|radiator",
    re.IGNORECASE,
)
ROOM_MEAN_C = (-15.0, 35.0)  # a year's average, anywhere people live
DAY_LIMIT_C = (-50.0, 55.0)  # a single day, indoors or out


def _to_c(value: float, unit: str) -> float:
    return (value - 32) * 5 / 9 if "f" in unit.lower() else value


def climate_facts(
    ha: HA, states: list[dict[str, Any]], areas: dict[str, str], year: int, now: datetime
) -> dict[str, Any]:
    """The warmest rooms and the year's extremes, from the sensors that read a
    room or the weather: diagnostics, appliances by name, and anything whose
    year does not look like a room's are left out."""
    unit_of: dict[str, str] = {}
    temps = []
    for s in states:
        eid = str(s.get("entity_id", ""))
        attrs = s.get("attributes") or {}
        name = f"{eid} {attrs.get('friendly_name') or ''}"
        if (
            eid.startswith("sensor.")
            and attrs.get("device_class") == "temperature"
            and attrs.get("state_class") == "measurement"
            and areas.get(eid)
            and not NOT_A_ROOM.search(name.replace("_", " "))
        ):
            temps.append(eid)
            unit_of[eid] = str(attrs.get("unit_of_measurement") or "°C")
    if not temps:
        return {}
    unit = unit_of[temps[0]]
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
        rows = (days or {}).get(sid) or []
        u = unit_of[sid]
        means = [float(r["mean"]) for r in rows if r.get("mean") is not None]
        if not means or not ROOM_MEAN_C[0] <= _to_c(sum(means) / len(means), u) <= ROOM_MEAN_C[1]:
            continue  # a year no room has: an appliance the names did not give away
        for row in rows:
            when = when_of(row.get("start"))
            if row.get("mean") is not None:
                by_area.setdefault(areas[sid], []).append(float(row["mean"]))
            hi, lo = row.get("max"), row.get("min")
            if hi is not None and DAY_LIMIT_C[0] <= _to_c(float(hi), u) <= DAY_LIMIT_C[1]:
                if float(hi) > hot[1]:
                    hot = (when, float(hi))
            if lo is not None and DAY_LIMIT_C[0] <= _to_c(float(lo), u) <= DAY_LIMIT_C[1]:
                if float(lo) < cold[1]:
                    cold = (when, float(lo))
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


# Binary sensors whose flips are housekeeping, not life at home.
DULL_BINARY = {
    "connectivity", "problem", "update", "battery", "battery_charging", "plug", "power",
    "running", "safety", "tamper", "light", "moving", "vibration", "heat", "cold", "moisture",
}  # fmt: skip


def trackers(
    states: list[dict[str, Any]],
    areas: dict[str, str],
    skip: set[str] | frozenset = frozenset(),
    picks: list[str] | tuple = (),
) -> dict[str, list[str]]:
    """The entities each tally is drawn from. ``skip`` is what the registry
    marks as a device's own setting or reading, or hidden: a plug's child
    lock or indicator light is no switch anybody flicks. ``picks`` are the
    entities a panel asked for by name, counted whatever they are."""
    out: dict[str, list[str]] = {
        "doors": [], "lights": [], "tv": [], "motion": [], "zone": [],
        "switches": [], "locks": [], "covers": [], "binary": [], "toggles": [],
        "picked": [],
    }  # fmt: skip
    # A picked sensor with statistics is read from those, back to 1 January.
    stat = {
        str(s.get("entity_id") or "")
        for s in states
        if not (s.get("attributes") or {}).get("state_class")
        or not str(s.get("entity_id")).startswith("sensor.")
    }
    out["picked"] = [p for p in dict.fromkeys(picks) if p in stat]
    for s in states:
        eid = str(s.get("entity_id") or "")
        if eid in skip:
            continue
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
        elif eid.startswith(("switch.", "fan.")) and not attrs.get("entity_id"):
            out["switches"].append(eid)
        elif eid.startswith("lock."):
            out["locks"].append(eid)
        elif eid.startswith("cover.") and dc in ("garage", "gate", "door"):
            out["covers"].append(eid)
        elif eid.startswith("binary_sensor.") and dc not in DULL_BINARY:
            out["binary"].append(eid)  # a window, a pet door, a bed, a litter box in use
        elif eid.startswith("input_boolean."):
            out["toggles"].append(eid)
    return out


# The states each domain is "on" in: an event is a rise into one of them.
TV_ON = {"on", "playing", "paused", "idle"}
DOMAIN_ON = {
    "light": {"on"},
    "switch": {"on"},
    "fan": {"on"},
    "input_boolean": {"on"},
    "binary_sensor": {"on"},
    "cover": {"open", "opening"},
    "lock": {"unlocked", "unlocking", "open"},
    "media_player": TV_ON,
    "person": {"home"},
    "device_tracker": {"home"},
    "script": {"on"},
    "siren": {"on"},
    "vacuum": {"cleaning"},
    "lawn_mower": {"mowing"},
    "timer": {"active"},
    "climate": {"heat", "cool", "heat_cool", "auto", "dry", "fan_only"},
    "water_heater": {"on", "eco", "electric", "gas", "heat_pump", "high_demand", "performance"},
    "humidifier": {"on"},
    "valve": {"open", "opening"},
    "alarm_control_panel": {
        "armed_home", "armed_away", "armed_night", "armed_vacation", "armed_custom_bypass",
    },
}  # fmt: skip
GONE = {"unavailable", "unknown", ""}
EVENT_KINDS = (
    "lights",
    "switches",
    "doors",
    "covers",
    "locks",
    "tv",
    "motion",
    "binary",
    "toggles",
)


def on_states(eid: str) -> set[str]:
    return DOMAIN_ON.get(eid.split(".", 1)[0], set())


def event_ids(track: dict[str, list[str]]) -> list[str]:
    """Every entity whose rises the ledger counts, once each."""
    ids = [e for k in EVENT_KINDS for e in track.get(k, [])] + track.get("picked", [])
    return list(dict.fromkeys(ids))


def track_hash(track: dict[str, list[str]]) -> str:
    return hashlib.sha1(",".join(sorted(event_ids(track))).encode()).hexdigest()[:10]


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


def changes(rows: list[dict]) -> int:
    """State changes, not counting a drop out or back from unavailable: a
    button pressed, a scene set, an event fired, a counter stepping."""
    n, prev = 0, None
    for r in rows:
        st = str(r.get("state") if r.get("state") is not None else "")
        if st in GONE:
            continue
        if prev is not None and st != prev:
            n += 1
        prev = st
    return n


def runs(rows: list[dict]) -> int:
    """An automation's runs: each new last_triggered."""
    seen = [
        (r.get("attributes") or {}).get("last_triggered")
        for r in rows
        if (r.get("attributes") or {}).get("last_triggered")
    ]
    return sum(1 for a, b in zip(seen, seen[1:], strict=False) if a != b)


def count_of(eid: str, rows: list[dict]) -> int:
    domain = eid.split(".", 1)[0]
    if domain == "automation":
        return runs(rows)
    on = on_states(eid)
    return rises(rows, on) if on else changes(rows)


def tally_day(ha: HA, track: dict[str, list[str]], areas: dict[str, str], day: date) -> dict:
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    by_id: dict[str, list[dict]] = {}
    ids = list(dict.fromkeys(i for v in track.values() for i in v))
    autos = [i for i in ids if i.startswith("automation.")]
    for rows in ha.history([i for i in ids if i not in autos], start, end):
        if rows and rows[0].get("entity_id"):
            by_id[rows[0]["entity_id"]] = rows
    for rows in ha.history(autos, start, end, attributes=True):
        if rows and rows[0].get("entity_id"):
            by_id[rows[0]["entity_id"]] = rows
    rooms: dict[str, int] = {}
    for eid in track["motion"]:
        n = rises(by_id.get(eid, []), {"on"})
        if n:
            rooms[areas[eid]] = rooms.get(areas[eid], 0) + n
    tv_on = TV_ON
    events: dict[str, int] = {}
    for eid in event_ids(track):
        n = count_of(eid, by_id.get(eid, []))
        if n:
            events[eid] = n
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
        "events": events,
        "set": track_hash(track),
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
    # A day tallied for a different set of entities (one picked since, or kept
    # before per-entity events were counted) is tallied again while Home
    # Assistant still has it.
    now_set = track_hash(track)
    missing = [
        d
        for d in (jan1 + timedelta(days=i) for i in range((today - jan1).days))
        if days.get(d.isoformat(), {}).get("set") != now_set and d.isoformat() >= floor
    ]
    since: dict[str, str] = ledger.setdefault("since", {})
    for done, d in enumerate(reversed(missing)):
        if done >= BACKFILL_DAYS_PER_FETCH:
            break
        row = tally_day(ha, track, areas, d)
        if not row.pop("seen"):
            # Nothing that old is left in Home Assistant; nothing older will be either.
            ledger["empty_before"] = (d + timedelta(days=1)).isoformat()
            break
        days[d.isoformat()] = row
        for eid in event_ids(track):
            if d.isoformat() < since.get(eid, "9999"):
                since[eid] = d.isoformat()
    for k in [k for k in days if not k.startswith(str(today.year))]:
        days.pop(k)
    for eid in [e for e, d in since.items() if not d.startswith(str(today.year))]:
        since.pop(eid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger), encoding="utf-8")
    except OSError:
        pass
    return ledger


def _the(name: str) -> str:
    return name[4:] if name.lower().startswith("the ") else name


def _lead(first: date, year: int) -> str:
    if first <= date(year, 1, 1):
        return f"In {year}"
    return f"Since {first.day} {MONTHS[first.month - 1][:3]}"


def wording(eid: str, dc: str, name: str, *, stat: bool = False) -> tuple[str, str]:
    """How a thing's count reads: the line along the foot (with {n}) and the
    heading for its big-number slide. ``stat`` is a counting sensor's rise
    from long-term statistics, not a tally of its changes."""
    low = name.lower()
    domain = eid.split(".", 1)[0]
    if domain == "sensor" and stat:
        if "litter" in low or "toilet" in low:
            return f"the {name} was used {{n}} times!", f"{name} visits"
        return f"the {name} went up by {{n}}!", name
    if "litter" in low or "toilet" in low:
        return f"the {name} was used {{n}} times!", f"{name} visits"
    if "feeder" in low:
        return f"the {name} served {{n}} meals!", f"{name} meals"
    if "doorbell" in low:
        return f"the {name} rang {{n}} times!", f"{name} rings"
    if domain in ("person", "device_tracker"):
        return f"{name} came home {{n}} times!", f"Times {name} came home"
    if domain in ("light", "switch", "fan", "input_boolean"):
        return f"you flicked the {name} on {{n}} times!", f"{name} flicked on"
    if domain == "cover" or dc in ("door", "garage_door", "opening", "window", "garage"):
        return f"the {name} opened {{n}} times!", f"{name} openings"
    if domain == "lock":
        return f"you unlocked the {name} {{n}} times!", f"{name} unlocks"
    if domain == "media_player":
        return f"the {name} went on {{n}} times!", f"{name} switched on"
    if dc in ("motion", "occupancy", "presence"):
        return f"the {name} spotted someone {{n}} times!", f"{name} sightings"
    plain = {
        "button": ("you pressed the {name} {n} times!", "{name} presses"),
        "input_button": ("you pressed the {name} {n} times!", "{name} presses"),
        "event": ("the {name} went off {n} times!", "{name} events"),
        "scene": ("you set the {name} scene {n} times!", "{name} scene"),
        "script": ("the {name} script ran {n} times!", "{name} script runs"),
        "automation": ("the {name} automation ran {n} times!", "{name} automation runs"),
        "counter": ("the {name} counted up {n} times!", "{name} count"),
        "vacuum": ("the {name} went out cleaning {n} times!", "{name} cleans"),
        "lawn_mower": ("the {name} went out mowing {n} times!", "{name} mows"),
        "climate": ("the {name} came on {n} times!", "{name} switched on"),
        "water_heater": ("the {name} came on {n} times!", "{name} switched on"),
        "alarm_control_panel": ("you armed the {name} {n} times!", "{name} armed"),
        "timer": ("the {name} timer started {n} times!", "{name} timers"),
        "valve": ("the {name} opened {n} times!", "{name} openings"),
    }
    if domain in plain:
        line, head = plain[domain]
        return line.replace("{name}", name), head.replace("{name}", name)
    if on_states(eid):
        return f"the {name} turned on {{n}} times!", f"{name} turned on"
    return f"the {name} changed {{n}} times!", f"{name} changes"


# Counters that are housekeeping, not life at home.
DULL_COUNTER = re.compile(
    r"uptime|reboot|restart|reconnect|error|fail|rssi|signal|packet|byte|traffic|update|"
    r"firmware|battery|memory|\bcpu\b|disk|download|upload|data|messages?\b|sent|received",
    re.IGNORECASE,
)
COUNT_UNITS = {
    "", "times", "time", "cycles", "cycle", "uses", "visits", "count", "meals", "x",
    "cups", "flushes", "presses", "rings", "steps", "feeds", "portions",
}  # fmt: skip
PLAIN_UNITS = {"", "times", "time", "count", "x"}  # said by the sentence already


def counter_ids(
    states: list[dict[str, Any]], skip: set[str], picks: list[str] | tuple = ()
) -> tuple[list[str], set[str]]:
    """Sensors that count up (state_class total or total_increasing), whose
    year is in long-term statistics from 1 January: the ones the widget
    would choose itself (counts of something, not energy or data), and any
    a panel picked."""
    auto: set[str] = set()
    picked = set(picks)
    ids = []
    for s in states:
        eid = str(s.get("entity_id") or "")
        attrs = s.get("attributes") or {}
        if not eid.startswith("sensor.") or attrs.get("state_class") not in (
            "total",
            "total_increasing",
        ):
            continue
        unit = str(attrs.get("unit_of_measurement") or "").strip().lower()
        name = f"{eid} {attrs.get('friendly_name') or ''}".replace("_", " ")
        if (
            eid not in skip
            and not attrs.get("device_class")
            and unit in COUNT_UNITS
            and not DULL_COUNTER.search(name)
        ):
            auto.add(eid)
        if eid in auto or eid in picked:
            ids.append(eid)
    return ids, auto


def counter_things(
    ha: HA,
    ids: list[str],
    auto: set[str],
    states: list[dict[str, Any]],
    names: dict[str, str],
    year: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """Each counter's rise this year, from long-term statistics."""
    if not ids:
        return []
    (stats,) = ha.ws(
        [
            {
                "type": "recorder/statistics_during_period",
                "start_time": datetime(year, 1, 1).astimezone().isoformat(),
                "end_time": now.astimezone().isoformat(),
                "statistic_ids": ids,
                "period": "day",
                "types": ["change"],
            }
        ]
    )
    attrs_of = {str(s.get("entity_id")): s.get("attributes") or {} for s in states}
    out = []
    for eid in ids:
        rows = [r for r in (stats or {}).get(eid) or [] if r.get("change") is not None]
        total = sum(float(r["change"]) for r in rows)
        if not rows or total <= 0:
            continue
        first = min(when_of(r.get("start")).date() for r in rows)
        unit = str(attrs_of.get(eid, {}).get("unit_of_measurement") or "")
        if unit.lower() in PLAIN_UNITS:
            unit = ""
        out.append(
            thing(eid, "", names, total, first, year, auto=eid in auto, unit=unit, stat=True)
        )
    return out


def measure_ids(states: list[dict[str, Any]], picks: list[str] | tuple) -> list[str]:
    """Picked sensors that measure rather than count (a temperature, a power
    draw): their year's average and peak are in long-term statistics."""
    picked = set(picks)
    return [
        str(s.get("entity_id"))
        for s in states
        if str(s.get("entity_id")) in picked
        and str(s.get("entity_id")).startswith("sensor.")
        and (s.get("attributes") or {}).get("state_class") == "measurement"
    ]


def measure_things(
    ha: HA,
    ids: list[str],
    states: list[dict[str, Any]],
    names: dict[str, str],
    year: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """Each picked measurement's average and peak this year."""
    if not ids:
        return []
    (stats,) = ha.ws(
        [
            {
                "type": "recorder/statistics_during_period",
                "start_time": datetime(year, 1, 1).astimezone().isoformat(),
                "end_time": now.astimezone().isoformat(),
                "statistic_ids": ids,
                "period": "month",
                "types": ["mean", "max"],
            }
        ]
    )
    attrs_of = {str(s.get("entity_id")): s.get("attributes") or {} for s in states}
    out = []
    for eid in ids:
        rows = [r for r in (stats or {}).get(eid) or [] if r.get("mean") is not None]
        if not rows:
            continue
        mean = sum(float(r["mean"]) for r in rows) / len(rows)
        peak = max(float(r.get("max") if r.get("max") is not None else r["mean"]) for r in rows)
        first = min(when_of(r.get("start")).date() for r in rows)
        unit = str(attrs_of.get(eid, {}).get("unit_of_measurement") or "")
        name = _the(names.get(eid) or eid.split(".", 1)[-1].replace("_", " ").capitalize())
        avg, top = f"{mean:,.1f}", f"{peak:,.1f}"
        sp = " " if unit and unit[0].isalnum() else ""
        out.append(
            {
                "id": eid,
                "name": f"{name}, on average",
                "label": name,
                "stat": "measure",
                "n": mean,
                "count": avg,
                "unit": unit,
                "since": since_text(first, year),
                "text": f"{_lead(first, year)}, the {name} averaged {avg}{sp}{unit} "
                f"and peaked at {top}{sp}{unit}!",
                "auto": False,
            }
        )
    return out


def thing(
    eid: str,
    dc: str,
    names: dict[str, str],
    n: float,
    first: date,
    year: int,
    *,
    auto: bool,
    unit: str = "",
    stat: bool = False,
) -> dict[str, Any]:
    name = _the(names.get(eid) or eid.split(".", 1)[-1].replace("_", " ").capitalize())
    line, head = wording(eid, dc, name, stat=stat)
    count = _plain(n)
    shown = f"{count} {unit}".strip() if stat else count
    return {
        "id": eid,
        "name": head,
        "label": name,
        "stat": "count" if stat else "",
        "n": n,
        "count": count,
        "unit": unit if stat else "times",
        "since": since_text(first, year),
        "text": f"{_lead(first, year)}, " + line.format(n=shown),
        "auto": auto,
    }


def ledger_things(
    ledger: dict[str, Any],
    track: dict[str, list[str]],
    names: dict[str, str],
    dclass: dict[str, str],
    year: int,
) -> list[dict[str, Any]]:
    """Each entity's count from the ledger, from the first day it was counted."""
    days = {k: v for k, v in (ledger.get("days") or {}).items() if "events" in v}
    if not days:
        return []
    first_day = min(days)
    since = ledger.get("since") or {}
    counts: dict[str, int] = {}
    for v in days.values():
        for eid, n in (v.get("events") or {}).items():
            counts[eid] = counts.get(eid, 0) + int(n)
    auto = {e for k in EVENT_KINDS for e in track.get(k, [])}
    tracked = set(event_ids(track))
    out = []
    for eid, n in counts.items():
        if eid not in tracked or n < 2:
            continue
        first = date.fromisoformat(since.get(eid) or first_day)
        out.append(thing(eid, dclass.get(eid, ""), names, n, first, year, auto=eid in auto))
    return out


PETS = "{pets}"  # filled in by the prompt from the Pets option, or "a cat"


def scene_of(eid: str, dc: str, name: str, area: str, unit: str, stat: str) -> str:
    """A picture of the thing being counted, in its own room, for the painter:
    a hand on the Hall light switch, the cat stepping out of the litter box."""
    low = name.lower()
    domain = eid.split(".", 1)[0]
    where = f" in the {area}" if area else " at home"
    if "litter" in low or ("toilet" in low and "cat" in low):
        return f"{PETS} stepping proudly out of a litter box{where}"
    if "feeder" in low or "feed" in low:
        return f"{PETS} waiting eagerly by an automatic pet feeder{where}"
    if "flap" in low or "pet door" in low or "cat door" in low or "dog door" in low:
        return f"{PETS} pushing through a pet flap{where}"
    if "doorbell" in low:
        return "a finger pressing a doorbell at the front door, sound lines ringing out"
    if stat == "measure":
        if dc == "temperature":
            return f"a big thermometer{where}, rising and falling through the year"
        if dc in ("power", "energy", "current"):
            return f"a glowing plug and a spinning electricity meter{where}"
        if dc in ("humidity", "moisture"):
            return f"fat water droplets and a humidity dial{where}"
        return f"a big dial gauge reading {unit or 'the ' + name}{where}"
    if stat == "count":
        return f"a chunky tally counter clicking up for the {name}{where}"
    if domain in ("person", "device_tracker"):
        return f"{name} arriving home through the front door, waving"
    if domain == "light":
        return f"the {name} glowing warmly{where}, a hand on its light switch"
    if domain in ("switch", "input_boolean"):
        return f"a hand flicking the {name} switch{where}"
    if domain == "fan":
        return f"the {name} spinning{where}, a breeze ruffling things"
    if dc == "window":
        return f"the {name} swinging open{where}, curtains blowing"
    if domain == "cover" or dc in ("door", "garage_door", "opening", "garage"):
        return f"the {name} swinging open{where}"
    if domain == "lock":
        return f"a key turning in the {name}, a little click"
    if domain == "media_player":
        return f"the {name} glowing{where}, a comfy sofa in front of it"
    if dc in ("motion", "occupancy", "presence"):
        return f"someone strolling past a little motion sensor{where}"
    if domain == "vacuum":
        return f"a robot vacuum zooming across the floor{where}"
    if domain == "lawn_mower":
        return "a robot mower trimming stripes into the lawn"
    if domain in ("climate", "water_heater", "humidifier"):
        return f"a thermostat and a heat pump{where}, warm and cool air swirling"
    if domain == "alarm_control_panel":
        return "an alarm keypad by the front door being armed, a shield glowing"
    if domain in ("button", "input_button"):
        return f"a big round button labelled '{name}' being pressed{where}"
    if domain == "scene":
        return f"a room{where} changing mood as the {name} scene is set"
    if domain in ("script", "automation"):
        return f"a cheerful little robot pulling levers around the house to run {name}"
    if domain == "event":
        return f"a burst of sparks and sound lines marking the {name}{where}"
    if domain == "timer":
        return f"a kitchen timer ticking{where}"
    return f"the {name}{where}"


def events_of(things: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The lines for the band along a slide's foot, biggest first, a dozen at
    most: "In 2026, you flicked the Kitchen light on 8,678 times!" """
    picked = sorted((t for t in things if t["auto"]), key=lambda t: t["n"], reverse=True)
    return [{"text": t["text"], "n": t["count"]} for t in picked[:12]]


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


def areas_of(ha: HA) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Each entity's room (its own area, else its device's), its display name,
    and the entities that are a device's own setting or reading, or hidden."""
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
    skip: set[str] = set()
    for e in entities or []:
        aid = e.get("area_id") or dev_area.get(e.get("device_id"))
        if e.get("entity_category") in ("diagnostic", "config") or e.get("hidden_by"):
            aid = None  # a device's own reading (a chip's temperature), not the room's
            skip.add(e["entity_id"])
        if aid in area_name:
            out[e["entity_id"]] = area_name[aid]
        if e.get("name"):
            names[e["entity_id"]] = e["name"]
    return out, names, skip


def load_picks(data_dir: Path) -> list[str]:
    """Every entity any panel has picked, so the shared gather counts them all."""
    try:
        cells = json.loads((data_dir / "picks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(dict.fromkeys(e for ids in cells.values() for e in ids))


def save_picks(data_dir: Path, cell: str, picks: list[str]) -> bool:
    """Note a panel's picks; True when that adds an entity not counted before."""
    path = data_dir / "picks.json"
    try:
        cells = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cells = {}
    before = {e for ids in cells.values() for e in ids}
    if cells.get(cell or "default") == picks:
        return False
    cells[cell or "default"] = picks
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cells), encoding="utf-8")
    except OSError:
        pass
    return bool(set(picks) - before)


def gather(ha: HA, data_dir: Path, now: datetime) -> dict[str, Any]:
    """Everything found this year; a part that fails is noted, not fatal."""
    year = now.year
    states = ha.states()
    picks = load_picks(data_dir)
    dclass = {
        str(s.get("entity_id")): str((s.get("attributes") or {}).get("device_class") or "")
        for s in states
    }
    names = {
        s["entity_id"]: str((s.get("attributes") or {}).get("friendly_name") or "") for s in states
    }
    notes: list[str] = []
    facts: dict[str, Any] = {}
    lists: dict[str, Any] = {}
    skip: set[str] = set()
    try:
        areas, reg_names, skip = areas_of(ha)
        names.update(reg_names)
    except Exception as err:
        areas = {}
        notes.append(f"rooms: {err}")
    ledger: dict[str, Any] = {}
    things: list[dict[str, Any]] = []

    def history() -> dict[str, Any]:
        ledger.update(update_ledger(ha, data_dir / "ledger.json", track, areas, now.date()))
        things.extend(ledger_things(ledger, track, names, dclass, year))
        return life_facts(ledger, track, year)

    def counters() -> dict[str, Any]:
        ids, auto = counter_ids(states, skip, picks)
        things.extend(counter_things(ha, ids, auto, states, names, year, now))
        things.extend(measure_things(ha, measure_ids(states, picks), states, names, year, now))
        return {}

    parts = (
        ("energy", lambda: energy_facts(ha, names, year, now)),
        ("climate", lambda: climate_facts(ha, states, areas, year, now)),
        ("history", history),
        ("counters", counters),
    )
    track = trackers(states, areas, skip, picks)
    for part, fn in parts:
        try:
            got = fn()
            facts.update(got.get("facts") or {})
            lists.update(got.get("lists") or {})
        except Exception as err:
            notes.append(f"{part}: {err}")
    for t in things:
        t["scene"] = scene_of(
            t["id"], dclass.get(t["id"], ""), t["label"], areas.get(t["id"], ""),
            t["unit"], t["stat"],
        )  # fmt: skip
    things.sort(key=lambda t: t["n"], reverse=True)
    # A counter read from 1 January (a litter box, a feeder) earns a big number of its own.
    full_year = [t for t in things if t["auto"] and t["id"].startswith("sensor.")][:2]
    return {
        "facts": [dict(facts[k], key=k) for k in FACT_ORDER if k in facts][:6]
        + [thing_fact(t) for t in full_year],
        "lists": [dict(lists[k], key=k) for k in LIST_ORDER if k in lists][:2],
        "things": things[:80],
        "events": events_of(things),
        "asked": picks,
        "notes": notes,
        "at": now.timestamp(),
    }


def thing_fact(t: dict[str, Any]) -> dict[str, Any]:
    f = fact(t["name"], t["count"], t["unit"], raw=t["n"], since=t["since"])
    return dict(f, key=t["id"], scene=t.get("scene") or "")


def for_cell(year: dict[str, Any], picks: list[str]) -> dict[str, Any]:
    """The year as one panel sees it. With picks, they are all that is drawn
    (each a big number, painted in its own room, and a top list of those
    counted in times) and all that runs along the foot. Until a pick has
    been counted the year shows as it is, so the slide is never empty."""
    if not picks:
        return year
    by_id = {t["id"]: t for t in year.get("things") or []}
    chosen = [by_id[p] for p in picks if p in by_id]
    if not chosen:
        return year
    times = [t for t in chosen if t["unit"] == "times"]
    lists = []
    if len(times) >= 2:
        rows = ranked(
            [(t.get("label") or t["name"], t["n"], t["count"], "times") for t in times], False
        )
        since = next((t["since"] for t in times if t["since"]), "")
        lists = [{"title": "Most-used things", "rows": rows, "since": since, "key": "picked"}]
    return {
        **year,
        "facts": [thing_fact(t) for t in chosen][:8],
        "lists": lists,
        "events": [{"text": t["text"], "n": t["count"]} for t in chosen],
        "picked": True,
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
    litter = next((x for x in facts if "litter" in x["name"].lower()), None)
    if litter:
        return {
            "name": "The Cat Palace",
            "why": f"The litter box was used {litter['value']} times.",
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
    "with no other numbers anywhere."
)
BAND = (
    " Keep the bottom eighth of the slide as a plain band of the background colour with "
    "nothing at all in it: no type, no shapes, no illustration."
)
NO_BAND = " Fill the slide right down to its bottom edge, with no empty band along the foot."
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
    scenes = [x["scene"].replace(PETS, pets or "a cat") for x in facts if x.get("scene")]
    if p.get("picks") and scenes:
        # Only what was picked is drawn: the house cut away to show each, room by room.
        hero = f"{home} cut away like a doll's house, showing " + "; ".join(scenes[:4])
    kind, i = p["slide"], p["index"]
    if kind == "intro":
        body = (
            f"The INTRO slide: 'Your' small, then {t} huge and stacked, then 'is here.' and a "
            f"small line 'A year in the life of your home.' A flat illustration of {hero}."
        )
    elif kind == "number":
        x = facts[i]
        rate = f", and a small line 'That's {x['per_day']} a day.'" if x.get("per_day") else ""
        pic = (
            x["scene"].replace(PETS, pets or "a cat")
            if x.get("scene")
            else f"something that stands for '{x['name']}'"
        )
        body = (
            f"A BIG-NUMBER slide: '{label(x)}' as the heading, then '{x['value']}' enormous, "
            f"then '{LONG.get(x['unit'], x['unit'])}' under it{rate}. A cheerful flat "
            f"illustration of {pic}. Small {t} in a corner."
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
    elif lists:
        stats = " and ".join(f"'{label(x)}' '{fig(x)}'" for x in facts[:2])
        cols = "; ".join(
            f"'{lst['title']}' then {numbered(lst['rows'], False)}" for lst in lists[:2]
        )
        many = "two columns of numbered lists" if len(lists) > 1 else "a numbered list"
        body = (
            f"The final SUMMARY slide, the one people share: {t} at the top, a square flat "
            f"illustration of {hero}, then {many}: {cols}. Then two "
            f"big stats side by side: {stats}."
        )
    else:
        stats = ", ".join(f"'{label(x)}' '{fig(x)}'" for x in facts[:4])
        body = (
            f"The final SUMMARY slide, the one people share: {t} at the top, a square flat "
            f"illustration of {hero}, then big stats in a grid: {stats}."
        )
    shape = "landscape" if p["land"] else "portrait"
    look = LOOK.format(segments=p["segments"], done=p["position"] + 1)
    look += BAND if p.get("band", True) else NO_BAND
    return f"{look} A {shape} slide. Palette: {PALETTE_WORDS[p['palette']]}. {body}"


def figures(p: dict[str, Any]) -> list[str]:
    """The numbers a slide carries, for the strip along its foot."""
    kind, i = p["slide"], p["index"]
    if kind == "number":
        return [f"{label(p['facts'][i])} {fig(p['facts'][i])}"]
    if kind in ("top", "leaderboard"):
        return [f"{r['name']} {fig(r)}" for r in p["lists"][i]["rows"]]
    if kind == "summary":
        return [f"{label(x)} {fig(x)}" for x in p["facts"][: 2 if p["lists"] else 4]]
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


def choices(name: str) -> list[dict[str, str]]:
    """The editor's picker: every entity in Home Assistant. Each is counted
    the way it reads best: a switch or a door by the times it came on, a
    button or a scene by its presses, an automation by its runs, a counting
    sensor by its rise and a measuring one by its average and peak, both
    from 1 January."""
    core = _core()
    if name != "entity" or core is None:
        return []
    return core.entity_choices()


def parse_picks(raw: Any) -> list[str]:
    if isinstance(raw, list):
        ids = [str(x).strip() for x in raw]
    else:
        ids = re.split(r"[\s,]+", str(raw or "").strip())
    return list(dict.fromkeys(i for i in ids if "." in i))


SAMPLE_EVENT = {"text": "In 2026, you flicked the kitchen light on 8,678 times!", "n": "8,678"}


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    now = datetime.now()
    palette = _pick(options.get("palette"), PALETTES, "wrapped")
    slide = _pick(options.get("slide"), SLIDES, "summary")
    title = str(options.get("title") or "").strip() or "Home Wrapped"
    cell_w, cell_h = int(ctx.get("cell_w") or 0), int(ctx.get("cell_h") or 0)
    land = bool(cell_w and cell_h and cell_w > cell_h * 1.25)
    band = options.get("foot_band") is not False
    out: dict[str, Any] = {
        "strip": options.get("show_strip") is not False,
        "band": band,
        "sample": sample_file(slide, palette, land),
        "palette": palette,
        "event": SAMPLE_EVENT,
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
    picks = parse_picks(options.get("entities"))
    if save_picks(data_dir, art.cell, picks):
        (data_dir / "year.json").unlink(missing_ok=True)  # count the new ones now
    try:
        year = for_cell(cached_gather(HA(core), data_dir, now), picks)
    except Exception as err:
        out["note"] = f"Home Assistant: {core.coerce_error(err)}"
        return out
    events = year.get("events") or []
    # A new one each hour, so a panel left on one slide still has news along its foot.
    out["event"] = events[int(now.timestamp() // 3600) % len(events)] if events else None
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
    # Only a slide painted without the band adds to the key, so those painted with it keep theirs.
    keyed = [title, palette, home, pets, size] + ([] if band else ["full"]) + sorted(picks)
    digest = hashlib.sha1(json.dumps(keyed).encode()).hexdigest()[:10]
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
        "band": band,
        "picks": picks,
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


# ----------------------------------------------------------------------
# The admin sheet: /plugins/home_wrapped/
# ----------------------------------------------------------------------

SLIDE_NAMES = {
    "intro": "Intro",
    "number": "Big number",
    "top": "Top five",
    "leaderboard": "Leaderboard",
    "personality": "Personality",
    "summary": "Summary",
}


def _plugin_context() -> tuple[Path, dict[str, Any]]:
    """The widget's data folder and its settings, as the host keeps them."""
    plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
    store = current_app.config.get("SETTINGS_STORE")
    settings: dict[str, Any] = {}
    if store is not None and plugin is not None:
        settings = store.get_for_runtime("plugins", PLUGIN_ID, plugin.manifest.get("settings", []))
    return Path(plugin.data_dir), settings


def sheet(data_dir: Path, now: datetime) -> dict[str, Any]:
    """Every slide painted, newest period first, and what Home Assistant gave."""
    periods = {f"{now.year}-w{now.isocalendar()[1]:02d}", f"{now.year}-m{now.month:02d}"}
    errors = {e["key"]: e for e in ART.errors(data_dir)}
    slides = []
    for e in ART.listing(data_dir):
        parts = e["key"].split("-")
        if len(parts) < 4:
            continue
        kind, index, period = parts[0], parts[1], f"{parts[2]}-{parts[3]}"
        meta = e.get("meta") or {}
        payload = meta.get("payload") or {}
        slides.append(
            {
                "key": e["key"],
                "file": e.get("file") or "",
                "kind": SLIDE_NAMES.get(kind, kind.title()),
                "index": index,
                "period": period,
                "current": period in periods,
                "painted": str(meta.get("painted") or ""),
                "figures": meta.get("figures") or [],
                "palette": str(payload.get("palette") or ""),
                "size": "x".join(str(n) for n in payload.get("size") or []),
                "versions": e.get("versions") or [],
                "error": (errors.get(e["key"]) or {}).get("why") or e.get("error") or "",
                "made": e.get("made") or 0,
            }
        )
    order = list(SLIDE_NAMES.values())
    slides.sort(key=lambda s: (s["kind"] in order and order.index(s["kind"]), s["index"]))
    slides.sort(key=lambda s: (s["current"], s["period"]), reverse=True)  # stable: story order kept
    try:
        year = json.loads((data_dir / "year.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        year = {}
    try:
        days = sorted(
            json.loads((data_dir / "ledger.json").read_text(encoding="utf-8")).get("days") or {}
        )
    except (OSError, ValueError):
        days = []
    return {
        "slides": slides,
        "year": year,
        "found_at": datetime.fromtimestamp(year["at"]).strftime("%-d %b %H:%M")
        if year.get("at")
        else "",
        "ledger": {
            "days": len(days),
            "first": days[0] if days else "",
            "last": days[-1] if days else "",
        },
    }


def fresh_payload(old: dict[str, Any], year: dict[str, Any]) -> dict[str, Any]:
    """A kept slide's payload with today's figures in it."""
    year = for_cell(year, list(old.get("picks") or []))
    kind = str(old.get("slide") or "summary")
    index = int(old.get("index") or 0)
    if kind == "number":
        index = max(0, min(index, len(year["facts"]) - 1))
    elif kind in ("top", "leaderboard"):
        index = max(0, min(index, len(year["lists"]) - 1))
    seq = story(year)
    if (kind, index) not in seq:
        kind, index = "summary", 0
    return {
        **old,
        "slide": kind,
        "index": index,
        "facts": year["facts"],
        "lists": year["lists"],
        "personality": personality(year["facts"]),
        "segments": len(seq),
        "position": seq.index((kind, index)),
    }


def blueprint() -> Any:
    """Mounted by the plugin loader at /plugins/home_wrapped/: every slide
    painted, with Repaint, Repaint with today's figures and Delete, and what
    Home Assistant gave."""
    from flask import Blueprint, flash, redirect, render_template, request, url_for

    bp = Blueprint("home_wrapped_admin", __name__, template_folder="templates")

    def back() -> Any:
        return redirect(url_for("home_wrapped_admin.index"))

    @bp.get("/")
    def index() -> str:
        data_dir, settings = _plugin_context()
        return render_template(
            "home_wrapped/index.html",
            **sheet(data_dir, datetime.now()),
            spent=artwork.dollars(ART.budget_used(data_dir)),
            cap=artwork.dollars(ART.cap(settings)),
            has_key=bool(artwork.fal.painting_key(settings)),
            paintings_href=url_for("artwork.index", plugin_id=PLUGIN_ID, name="slides"),
        )

    @bp.get("/slide/<path:filename>")
    def slide_file(filename: str) -> Any:
        from flask import abort, send_from_directory

        data_dir, _ = _plugin_context()
        if ".." in filename or filename.startswith("/"):
            abort(404)
        return send_from_directory(ART.folder(data_dir), filename, max_age=600)

    @bp.post("/<key>/repaint")
    def repaint(key: str) -> Any:
        data_dir, settings = _plugin_context()
        fal_key = artwork.fal.painting_key(settings)
        if not fal_key:
            flash("No AI key yet: add one under Settings › Generative AI.", "warn")
            return back()
        if request.form.get("fresh") == "1":
            core = _core()
            meta = ART.meta(key, data_dir)
            old = meta.get("payload") or {}
            if core is None or not core.is_configured() or not old:
                flash("Today's figures cannot be read: Home Assistant Core is not set up.", "warn")
                return back()
            try:
                (data_dir / "year.json").unlink(missing_ok=True)
                year = cached_gather(HA(core), data_dir, datetime.now())
            except Exception as err:
                flash(f"Home Assistant: {core.coerce_error(err)}", "warn")
                return back()
            if not year["facts"] and not year["lists"]:
                flash("Home Assistant has nothing to wrap right now.", "warn")
                return back()
            payload = fresh_payload(old, year)
            title = str(meta.get("title") or key)
            ART.clear_error(key, data_dir)
            if ART.start(
                ART.job(key, payload, title, data_dir, fal_key, origin="admin"), ART.cap(settings)
            ):
                flash(
                    "Painting it again with today's figures; the current one is kept as an earlier version.",
                    "ok",
                )
            else:
                flash("It is already painting.", "warn")
        elif ART.repaint(key, data_dir, fal_key, settings):
            flash(
                "Painting it again with the same figures; the current one is kept as an earlier version.",
                "ok",
            )
        else:
            flash(
                "It cannot be painted again: it is already painting, or nothing is known about it.",
                "warn",
            )
        return back()

    @bp.post("/<key>/delete")
    def delete(key: str) -> Any:
        data_dir, _ = _plugin_context()
        ART.delete(key, data_dir, versions=request.form.get("versions") == "1")
        flash("Deleted. The panel paints it again on its next refresh.", "ok")
        return back()

    @bp.post("/<key>/version/<stem>")
    def use_version(key: str, stem: str) -> Any:
        data_dir, _ = _plugin_context()
        if ART.use_version(stem, data_dir):
            flash("That earlier version is back on the panel.", "ok")
        return back()

    @bp.post("/refresh")
    def refresh() -> Any:
        data_dir, _ = _plugin_context()
        core = _core()
        if core is None or not core.is_configured():
            flash("Home Assistant Core is not set up.", "warn")
            return back()
        try:
            (data_dir / "year.json").unlink(missing_ok=True)
            cached_gather(HA(core), data_dir, datetime.now())
            flash(
                "Read Home Assistant again. New slides use these figures; painted ones keep theirs until repainted.",
                "ok",
            )
        except Exception as err:
            flash(f"Home Assistant: {core.coerce_error(err)}", "warn")
        return back()

    return bp
