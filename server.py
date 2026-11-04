"""home_wrapped: the year so far from Home Assistant as year-in-review cards.

Every number is the state of one Home Assistant entity that already holds the
year to date (a counter, or a Utility Meter helper with a yearly cycle), so a
single ``get_states`` call covers a card and nothing depends on how long the
recorder keeps history. The client sets every word and number, so they are
always exact and current; every picture is painted by GPT Image.

Pictures are a hero (the house, the pets) for the summary and the trading
card, and a spot (one object) for each stat's big-number card. The sample
year's pictures are bundled (``static/art``), painted in the primary palette.
With an AI key a picture is painted for whatever the card needs that is not
bundled: another palette, a home or pets of your own, a stat of your own. Each
is painted once and kept, the bundled picture of the same kind as the style
reference, so they all match.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app import artwork
from flask import current_app

PLUGIN_ID = "home_wrapped"
HERE = Path(__file__).resolve().parent
ART_DIR = HERE / "static" / "art"
MODEL = "gpt_image"
ART_SIZE = (1024, 1024)

PALETTES = ("primary", "sunset", "sea", "gray")
CARDS = ("summary", "number", "top", "leaderboard", "trading", "cycle")
MAX_STATS = 6
MAX_ROWS = 5

# What each theme says, paints and shows before it is set up. A sample row is
# (name, value, unit); units follow ``fmt``.
THEMES: dict[str, dict[str, Any]] = {
    "home": {
        "title": "Home Wrapped",
        "card_name": "Our House",
        "list_a": "Top rooms",
        "list_b": "Top appliances",
        "home": "a cosy two-storey house",
        "pets": "two cats",
        "hero": "{home} at night seen from the front, warm light in the windows, {pets} "
        "looking out of the windows, a crescent moon and a few stars",
        "stats": [
            ("Kettle boils", 1214, ""),
            ("Front door openings", 3402, ""),
            ("Solar generated", 4108, "kWh"),
            ("Hours of lights", 2871, "h"),
            ("Washing loads", 156, ""),
            ("Days away", 81, "d"),
        ],
        "rows_a": [
            ("Kitchen", 410, ""),
            ("Living room", 230, ""),
            ("Study", 170, ""),
            ("Hallway", 110, ""),
            ("Bedroom", 80, ""),
        ],
        "share_a": True,
        "rows_b": [
            ("Front door", 3402, ""),
            ("Fridge", 1977, ""),
            ("Kettle", 1214, ""),
            ("Microwave", 688, ""),
            ("Washer", 156, ""),
        ],
        "share_b": False,
    },
    "energy": {
        "title": "Energy Wrapped",
        "card_name": "Our Power",
        "list_a": "Biggest users",
        "list_b": "Sunniest months",
        "home": "a two-storey house",
        "pets": "",
        "hero": "{home} at dusk with solar panels on the roof and an electric car plugged in "
        "to charge in the driveway, a big setting sun",
        "stats": [
            ("Solar generated", 4108, "kWh"),
            ("Self-powered", 63, "%"),
            ("Grid import", 2412, "kWh"),
            ("EV charged", 1860, "kWh"),
            ("Battery cycles", 214, ""),
            ("Heat pump", 3210, "kWh"),
        ],
        "rows_a": [
            ("Heat pump", 3210, "kWh"),
            ("EV charger", 1860, "kWh"),
            ("Oven", 640, "kWh"),
            ("Dryer", 410, "kWh"),
            ("Kettle", 205, "kWh"),
        ],
        "share_a": False,
        "rows_b": [
            ("December", 578, "kWh"),
            ("January", 520, "kWh"),
            ("November", 480, "kWh"),
            ("February", 450, "kWh"),
            ("October", 400, "kWh"),
        ],
        "share_b": False,
    },
    "pets": {
        "title": "Pets Wrapped",
        "card_name": "Our Pets",
        "list_a": "Favourite spots",
        "list_b": "Cat flap trips",
        "home": "a cosy house",
        "pets": "a tabby cat and a black cat",
        "hero": "{pets} sitting together on a rug beside a cat flap and a full food bowl, "
        "charming and cosy, seen from the front",
        "stats": [
            ("Meals served", 1095, ""),
            ("Cat flap trips", 2318, ""),
            ("Litter box visits", 1642, ""),
            ("Treats", 412, ""),
            ("Hours of zoomies", 37, "h"),
            ("Vet visits", 3, ""),
        ],
        "rows_a": [
            ("Sofa", 1840, "h"),
            ("Bed", 1210, "h"),
            ("Window sill", 760, "h"),
            ("Laundry basket", 410, "h"),
            ("Keyboard", 95, "h"),
        ],
        "share_a": True,
        "rows_b": [
            ("Miso", 1402, ""),
            ("Pepper", 916, ""),
        ],
        "share_b": False,
    },
}

# Colour words for the painter, per palette.
PALETTE_WORDS: dict[str, str] = {
    "primary": "flat bright red, lemon yellow, royal blue and leaf green with orange, white "
    "and black",
    "sunset": "flat red, orange and lemon yellow with black and white",
    "sea": "flat royal blue and leaf green with white, black and a little lemon yellow",
    "gray": "strictly greyscale: black, white and three flat greys with fine halftone dots, "
    "no colour at all",
}

STYLE = (
    "Flat bold simple shapes like a calm year-in-review story card, subtle risograph grain, "
    "no text, no letters, no numbers, no border, no frame."
)

_UNAVAILABLE = {"unavailable", "unknown", "none", ""}
_TO_KWH = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0, "gwh": 1_000_000.0}
_TO_HOURS = {"s": 1 / 3600, "sec": 1 / 3600, "min": 1 / 60, "h": 1.0, "hr": 1.0, "hours": 1.0}
_TO_DAYS = {"d": 1.0, "day": 1.0, "days": 1.0}


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices()
    return []


# ----------------------------------------------------------------------
# Numbers
# ----------------------------------------------------------------------


def _plain(n: float) -> str:
    if abs(n - round(n)) < 1e-9 or abs(n) >= 100:
        return f"{round(n):,}"
    return f"{n:,.1f}"


def fmt(value: float, unit: str) -> tuple[str, str]:
    """A number and the unit to print beside it, from the entity's own unit:
    energy rolls up to MWh, time to hours, days stay days, a share keeps its %."""
    u = unit.strip().lower()
    if u in _TO_KWH:
        kwh = value * _TO_KWH[u]
        if kwh >= 1000:
            return f"{kwh / 1000:,.1f}", "MWh"
        return _plain(kwh), "kWh"
    if u in _TO_HOURS:
        return _plain(value * _TO_HOURS[u]), "h"
    if u in _TO_DAYS:
        return _plain(value), "day" if round(value) == 1 else "days"
    if u == "%":
        return f"{round(value)}%", ""
    return _plain(value), unit.strip()


_LINE = re.compile(r"^\s*([^=]+?)\s*=\s*([a-z_]+\.\w+)\s*$")


def parse_lines(raw: Any, limit: int) -> list[tuple[str, str]]:
    """``Name = sensor.entity`` per line; anything else is ignored."""
    out: list[tuple[str, str]] = []
    for line in str(raw or "").splitlines():
        m = _LINE.match(line)
        if m:
            out.append((m.group(1), m.group(2)))
        if len(out) >= limit:
            break
    return out


def _number(state: dict[str, Any] | None) -> float | None:
    if state is None:
        return None
    raw = str(state.get("state") or "").strip()
    if raw.lower() in _UNAVAILABLE:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _unit(state: dict[str, Any]) -> str:
    return str((state.get("attributes") or {}).get("unit_of_measurement") or "")


def item(name: str, value: float, unit: str) -> dict[str, Any]:
    v, u = fmt(value, unit)
    # ``count``: a plain tally (no unit), the only kind that gets "That's 3.3 a day."
    return {"name": name, "value": v, "unit": u, "raw": value, "count": not unit.strip()}


def ranked(rows: list[dict[str, Any]], share: bool) -> list[dict[str, Any]]:
    """Biggest first, each with ``bar`` (its length against the top row); with
    ``share`` the number shown is its percentage of the list's total."""
    rows = sorted((r for r in rows if r["raw"] > 0), key=lambda r: r["raw"], reverse=True)
    total = sum(r["raw"] for r in rows) or 1.0
    top = rows[0]["raw"] if rows else 1.0
    for r in rows:
        r["bar"] = round(r["raw"] / top, 4)
        if share:
            r["value"], r["unit"] = f"{round(100 * r['raw'] / total)}%", ""
    return rows[:MAX_ROWS]


def sample(theme: dict[str, Any]) -> dict[str, Any]:
    return {
        "stats": [item(*r) for r in theme["stats"]],
        "list_a": ranked([item(*r) for r in theme["rows_a"]], theme["share_a"]),
        "list_b": ranked([item(*r) for r in theme["rows_b"]], theme["share_b"]),
    }


def read(
    options: dict[str, Any], theme: dict[str, Any], states: list[dict[str, Any]]
) -> dict[str, Any]:
    by_id = {str(s.get("entity_id")): s for s in states}

    def items(lines: list[tuple[str, str]]) -> list[dict[str, Any]]:
        out = []
        for name, eid in lines:
            st = by_id.get(eid)
            v = _number(st)
            if st is not None and v is not None:
                out.append(item(name, v, _unit(st)))
        return out

    return {
        "stats": items(parse_lines(options.get("stats"), MAX_STATS)),
        "list_a": ranked(items(parse_lines(options.get("rows_a"), 20)), theme["share_a"]),
        "list_b": ranked(items(parse_lines(options.get("rows_b"), 20)), theme["share_b"]),
    }


def card_for(card: str, now: datetime, n_stats: int, stat: int) -> tuple[str, int]:
    """The card to draw and, for a number card, which stat. ``cycle`` moves on
    each hour through the summary, the lists, every stat and the trading card."""
    if card != "cycle":
        return card, max(0, min(stat, n_stats - 1))
    seq = [("summary", 0), ("top", 0)]
    seq += [("number", i) for i in range(max(1, n_stats))]
    seq += [("leaderboard", 0), ("trading", 0)]
    return seq[int(now.timestamp() // 3600) % len(seq)]


# ----------------------------------------------------------------------
# Pictures
# ----------------------------------------------------------------------


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "x"


def hero_prompt(theme_id: str, palette: str, home: str, pets: str) -> str:
    t = THEMES[theme_id]
    subject = t["hero"].format(home=home or t["home"], pets=pets or t["pets"])
    return (
        f"A square illustration: {subject}. If pets are named, show exactly those animals, "
        f"each once, and no others. Palette: {PALETTE_WORDS[palette]}. {STYLE}"
    )


# Subjects for names the painter reads the wrong way.
SPOT_HINTS: dict[str, str] = {
    "grid import": "an electricity pylon with power lines",
    "hours of zoomies": "a cat running fast with speed lines behind it",
    "self-powered": "a house with solar panels and a lightning bolt",
}


def spot_prompt(name: str, palette: str) -> str:
    hint = SPOT_HINTS.get(name.strip().lower())
    subject = f"'{name}' (show {hint})" if hint else f"'{name}'"
    return (
        "A square illustration of ONE friendly object, large and centred on a plain flat "
        f"background, that stands for {subject} in a home's year (for 'Kettle boils', a "
        "steaming kettle; for 'Days away', a suitcase), drawn with a little character. "
        f"Palette: {PALETTE_WORDS[palette]}. {STYLE}"
    )


def bundled_name(kind: str, theme_id: str, name: str = "") -> str:
    """The bundled picture's file under static/art, if the sample year has one."""
    return f"hero-{theme_id}.jpg" if kind == "hero" else f"spot-{slug(name)}.jpg"


def _ref(kind: str, theme_id: str) -> str:
    path = ART_DIR / ("hero-home.jpg" if kind == "hero" else "spot-kettle-boils.jpg")
    if kind == "hero" and (ART_DIR / f"hero-{theme_id}.jpg").exists():
        path = ART_DIR / f"hero-{theme_id}.jpg"
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


# ----------------------------------------------------------------------
# Painted cards: GPT Image sets the whole card, words and numbers included
# ----------------------------------------------------------------------

CALM = (
    "In the visual language of an annual music-streaming year-in-review story card (no "
    "logos, no brand names), calm: a clear simple grid, generous empty space, huge bold "
    "condensed sans-serif type as the hero, only one or two playful shapes (a single "
    "starburst, one squiggle or one circle), nothing crowded, subtle risograph print grain. "
    "Flat, front-on, full-bleed, no phone or device frame. Spell every word exactly as given "
    "and use exactly these numbers, with no other numbers anywhere. Keep the bottom "
    "sixteenth of the card as a plain band of the background colour with nothing in it."
)
LONG = {"h": "hours"}


def _fig(it: dict[str, Any]) -> str:
    unit = LONG.get(it["unit"], it["unit"])
    return f"{it['value']} {unit}".strip()


def _numbered(rows: list[dict[str, Any]], with_value: bool) -> str:
    return ", ".join(
        f"'{i + 1} {r['name']}" + (f" {_fig(r)}'" if with_value else "'")
        for i, r in enumerate(rows)
    )


def card_prompt(p: dict[str, Any]) -> str:
    t = THEMES[p["theme"]]
    hero = t["hero"].format(home=p["home"] or t["home"], pets=p["pets"] or t["pets"])
    shape = "landscape" if p["land"] else "portrait"
    head = f"'{p['title']} {p['year']}'"
    stats, a, b = p["stats"], p["list_a"], p["list_b"]
    card = p["card"]
    if card == "summary":
        blocks = " and ".join(f"'{s['name']}' '{_fig(s)}'" for s in stats[:2])
        body = (
            f"A SUMMARY card: the title {head} at the top, a large illustration of {hero} under "
            f"it, then two columns of lists: left '{p['list_a_title']}' then "
            f"{_numbered(a, False)}; right '{p['list_b_title']}' then {_numbered(b, False)}. "
            f"Then two big stat blocks side by side: {blocks}."
        )
    elif card == "number":
        it = stats[p["index"]]
        hint = SPOT_HINTS.get(it["name"].strip().lower())
        subject = hint or f"something that stands for '{it['name']}'"
        rate = p.get("rate") or ""
        body = (
            f"A single BIG-NUMBER card: small {head} in a corner, the words '{it['name']}', then "
            f"'{it['value']}' enormous, then '{LONG.get(it['unit'], it['unit'])}'"
            + (f", and a small line '{rate}'" if rate else "")
            + f". A cheerful illustration of {subject}, with a little character."
        )
    elif card == "top":
        body = (
            f"A TOP FIVE card: heading 'Your {p['list_a_title'].lower()}' in huge stacked type, "
            f"then a numbered list where number 1 is much bigger: {_numbered(a, True)}. "
            f"Small {head} in a corner."
        )
    elif card == "leaderboard":
        body = (
            f"A LEADERBOARD card: small {head}, the heading '{p['list_b_title']}', then thick "
            "horizontal bars of decreasing length, longest first, each labelled with its name "
            f"and number: {_numbered(b, True)}."
        )
    else:
        lines = ", ".join(f"'{s['name']} {_fig(s)}'" for s in stats)
        body = (
            f"A TRADING CARD: one big collectible card on a coloured background, thick border "
            f"and rounded corners, named '{p['card_name']}' with '{p['year']}' small beside it, "
            f"an illustration of {hero}, then a stats table: {lines}. At the bottom "
            f"'{p['title']}'."
        )
    return f"{CALM} A {shape} card. Palette: {PALETTE_WORDS[p['palette']]}. {body}"


def card_size(land: bool, cell_w: int, cell_h: int) -> tuple[int, int]:
    """The painting's size: the cell's shape with the long side at 1600."""
    if cell_w > 0 and cell_h > 0:
        if cell_w >= cell_h:
            return 1600, max(480, round(1600 * cell_h / cell_w))
        return max(480, round(1600 * cell_w / cell_h)), 1600
    return (1600, 1200) if land else (1200, 1600)


def _paint_card(job: artwork.Job) -> tuple[bytes, dict[str, Any]]:
    p = job.payload
    prompt, size = card_prompt(p), tuple(p["size"])
    aspect = "4:3" if p["land"] else "3:4"
    if "gpt_image_t2i" in artwork.fal.MODELS:
        raw = job.fal.generate("gpt_image_t2i", prompt, aspect=aspect, quality="high", size=size)
    else:
        # A Tesserae before 0.502 has GPT Image only as an edit: the bundled hero as reference.
        raw = job.fal.generate(MODEL, prompt, refs=[_ref("hero", p["theme"])], aspect=aspect)
    return raw, {
        "credit": "Painted by GPT Image",
        "kind": "card",
        "figures": p["figures"],
        "painted": datetime.now().date().isoformat(),
    }


def paint(job: artwork.Job) -> tuple[bytes, dict[str, Any]]:
    p = job.payload
    if p["kind"] == "card":
        return _paint_card(job)
    if p["kind"] == "hero":
        prompt = hero_prompt(p["theme"], p["palette"], p["home"], p["pets"])
    else:
        prompt = spot_prompt(p["name"], p["palette"])
    prompt = (
        "Use the attached picture as the STYLE reference only (its flat shapes, grain and "
        "level of detail), not its subject or colours. " + prompt
    )
    refs = [_ref(p["kind"], p["theme"])]
    try:
        raw = job.fal.generate(
            MODEL, prompt, refs=refs, aspect="1:1", quality="high", size=ART_SIZE
        )
    except TypeError:
        # A Tesserae before 0.501 takes no quality or size.
        raw = job.fal.generate(MODEL, prompt, refs=refs, aspect="1:1")
    return raw, {"credit": "Illustration by GPT Image", "kind": p["kind"]}


ART = artwork.Collection(
    PLUGIN_ID,
    "pictures",
    make=paint,
    label="Home Wrapped",
    title="Painting the pictures",
    default_cents=200,
    typical_cents=20,
    parallel=2,
    per_preview=2,
)


def art_key(kind: str, theme_id: str, palette: str, *parts: str) -> str:
    digest = hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:10]
    return f"{kind}-{theme_id}-{palette}-{digest}"


def picture(
    art: artwork.Art | None,
    kind: str,
    theme_id: str,
    palette: str,
    *,
    home: str = "",
    pets: str = "",
    name: str = "",
) -> dict[str, Any]:
    """Where the client finds a picture: ``url`` (a painting), ``file`` (a
    bundled one, under static/art) or neither; plus the painting's state."""
    own = bool(home or pets) if kind == "hero" else False
    file = bundled_name(kind, theme_id, name)
    has_file = (ART_DIR / file).exists()
    if not own and has_file and palette == "primary":
        return {"file": file}
    if art is not None and art.key:
        key = art_key(kind, theme_id, palette, home, pets, name)
        payload = {
            "kind": kind,
            "theme": theme_id,
            "palette": palette,
            "home": home,
            "pets": pets,
            "name": name,
        }
        got = art.request(key, payload, title=f"{kind}: {name or home or theme_id}, {palette}")
        if got.ready:
            return {"url": art.url(key, px=1024), "state": "ready"}
        fallback = {"file": file} if has_file else {}
        return {**fallback, "state": got.state}
    return {"file": file} if has_file else {}


# ----------------------------------------------------------------------
# fetch
# ----------------------------------------------------------------------


def period_of(cadence: str, now: datetime) -> str:
    return f"m{now.month:02d}" if cadence == "monthly" else f"w{now.isocalendar()[1]:02d}"


def _last(data_dir: Path, cell: str, card: str, index: int) -> Path:
    name = re.sub(r"[^a-z0-9_-]+", "_", f"{cell or 'default'}-{card}-{index}".lower())[:80]
    return data_dir / "last" / f"{name}.json"


def remember(data_dir: Path, cell: str, card: str, index: int, key: str) -> None:
    path = _last(data_dir, cell, card, index)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": key}), encoding="utf-8")
    except OSError:
        pass


def recall(data_dir: Path, cell: str, card: str, index: int) -> str:
    try:
        raw = _last(data_dir, cell, card, index).read_text(encoding="utf-8")
        return str(json.loads(raw).get("key") or "")
    except (OSError, ValueError):
        return ""


def per_day(it: dict[str, Any], now: datetime) -> str:
    """ "That's 3.3 a day." for a plain tally; nothing for anything with a unit."""
    if not it.get("count"):
        return ""
    rate = it["raw"] / max(1, now.timetuple().tm_yday)
    if rate < 0.1:
        return ""
    return f"That's {rate:.1f} a day." if rate < 10 else f"That's {round(rate):,} a day."


def figures(out: dict[str, Any]) -> list[str]:
    """The numbers a painted card was given, for the strip along its foot."""
    if out["card"] == "number":
        it = out["stats"][out["index"]]
        return [f"{it['name']} {_fig(it)}"]
    if out["card"] == "top":
        return [f"{r['name']} {_fig(r)}" for r in out["list_a"]]
    if out["card"] == "leaderboard":
        return [f"{r['name']} {_fig(r)}" for r in out["list_b"]]
    if out["card"] == "summary":
        return [f"{s['name']} {_fig(s)}" for s in out["stats"][:2]]
    return [f"{s['name']} {_fig(s)}" for s in out["stats"]]


def painted_card(
    art: artwork.Art,
    options: dict[str, Any],
    out: dict[str, Any],
    ctx: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """The painted card for this cell, or the last one while the next paints."""
    cell_w, cell_h = int(ctx.get("cell_w") or 0), int(ctx.get("cell_h") or 0)
    land = cell_w > cell_h * 1.25 if cell_w and cell_h else False
    size = card_size(land, cell_w, cell_h)
    texts = [
        out["title"],
        out["theme"],
        out["palette"],
        out["card_name"],
        out["list_a_title"],
        out["list_b_title"],
        options.get("home") or "",
        options.get("pets") or "",
        options.get("stats") or "",
        options.get("rows_a") or "",
        options.get("rows_b") or "",
    ]
    digest = hashlib.sha1(json.dumps([texts, size]).encode()).hexdigest()[:10]
    period = period_of(str(options.get("cadence") or "weekly"), now)
    key = f"card-{out['card']}-{out['index']}-{now.year}-{period}-{digest}"
    payload = {
        "kind": "card",
        "land": land,
        "size": list(size),
        "home": str(options.get("home") or "").strip(),
        "pets": str(options.get("pets") or "").strip(),
        "figures": figures(out),
        "rate": per_day(out["stats"][out["index"]], now) if out["card"] == "number" else "",
        **{
            k: out[k]
            for k in (
                "card",
                "index",
                "title",
                "year",
                "theme",
                "palette",
                "card_name",
                "list_a_title",
                "list_b_title",
                "stats",
                "list_a",
                "list_b",
            )
        },
    }
    got = art.request(key, payload, title=f"{out['title']}: {out['card']} card, {period}")
    if got.ready:
        remember(art.data_dir, art.cell, out["card"], out["index"], key)
    shown = key if got.ready else recall(art.data_dir, art.cell, out["card"], out["index"])
    res: dict[str, Any] = {"paint_state": got.state}
    if shown and ART.path(shown, art.data_dir) is not None:
        meta = ART.meta(shown, art.data_dir)
        res["painted"] = {
            "url": art.url(shown, px=1600),
            "figures": meta.get("figures") or [],
            "when": str(meta.get("painted") or ""),
        }
    return res


def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip()
    return v if v in allowed else default


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    now = datetime.now()
    theme_id = _pick(options.get("theme"), tuple(THEMES), "home")
    theme = THEMES[theme_id]
    palette = _pick(options.get("palette"), PALETTES, "primary")
    title = str(options.get("title") or "").strip() or theme["title"]
    configured = any(str(options.get(k) or "").strip() for k in ("stats", "rows_a", "rows_b"))

    if configured:
        core = _core()
        if core is None:
            return {"error": "Install the Home Assistant Core plugin to use this widget."}
        try:
            states = core.get_states()
        except Exception as err:
            return {"error": core.coerce_error(err)}
        data = read(options, theme, states)
    else:
        data = sample(theme)

    try:
        stat = int(options.get("stat") or 1) - 1
    except (TypeError, ValueError):
        stat = 0
    card, index = card_for(
        _pick(options.get("card"), CARDS, "summary"), now, len(data["stats"]), stat
    )

    out: dict[str, Any] = {
        "title": title,
        "year": now.year,
        "theme": theme_id,
        "palette": palette,
        "card": card,
        "index": index,
        "card_name": str(options.get("card_name") or "").strip() or theme["card_name"],
        "list_a_title": str(options.get("list_a_title") or "").strip() or theme["list_a"],
        "list_b_title": str(options.get("list_b_title") or "").strip() or theme["list_b"],
        "grain": options.get("grain") is not False,
        "sample": not configured,
        **data,
    }

    art = ART.open(ctx, settings) if ctx.get("data_dir") else None
    home = str(options.get("home") or "").strip()
    pets = str(options.get("pets") or "").strip()
    out["strip"] = options.get("show_strip") is not False
    painted = str(options.get("render") or "live") == "painted"
    if (
        painted
        and configured
        and art is not None
        and art.key
        and (card != "number" or data["stats"])
    ):
        out.update(painted_card(art, options, out, ctx, now))
    if "painted" not in out and card in ("summary", "trading"):
        out["pic"] = picture(art, "hero", theme_id, palette, home=home, pets=pets)
    elif "painted" not in out and card == "number" and data["stats"]:
        out["pic"] = picture(art, "spot", theme_id, palette, name=data["stats"][index]["name"])
    if art is not None:
        out["notes"] = art.notes()
        art.finish(ctx)
        out.update(art.live())
    return out
