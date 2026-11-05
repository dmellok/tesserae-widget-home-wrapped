"""home_wrapped: finding the year in a fake Home Assistant (energy and climate from
long-term statistics, life at home from history into the ledger), the story, the
personality, the prompts and fetch() from sample to painted slide. No network."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]


def _load():
    name = "_tesserae_plugins.home_wrapped.server"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


server = _load()
NOW = datetime(2026, 11, 5, 9, 0)


def _ms(d: datetime) -> float:
    return d.timestamp() * 1000


STATES = [
    {"entity_id": "sensor.lounge_temp", "state": "21", "attributes": {"device_class": "temperature", "state_class": "measurement", "unit_of_measurement": "°C"}},
    {"entity_id": "sensor.bed_temp", "state": "19", "attributes": {"device_class": "temperature", "state_class": "measurement", "unit_of_measurement": "°C"}},
    {"entity_id": "binary_sensor.front_door", "state": "off", "attributes": {"device_class": "door", "friendly_name": "Front door"}},
    {"entity_id": "binary_sensor.bathroom_window", "state": "off", "attributes": {"device_class": "window", "friendly_name": "Bathroom window"}},
    {"entity_id": "binary_sensor.kitchen_motion", "state": "off", "attributes": {"device_class": "motion"}},
    {"entity_id": "binary_sensor.lounge_motion", "state": "off", "attributes": {"device_class": "motion"}},
    {"entity_id": "light.kitchen", "state": "off", "attributes": {}},
    {"entity_id": "light.all", "state": "off", "attributes": {"entity_id": ["light.kitchen"]}},
    {"entity_id": "media_player.lounge_tv", "state": "off", "attributes": {"device_class": "tv"}},
    {"entity_id": "zone.home", "state": "2", "attributes": {}},
    {"entity_id": "switch.hall_light", "state": "off", "attributes": {"friendly_name": "Hall light switch"}},
    {"entity_id": "switch.plug_child_lock", "state": "off", "attributes": {}},
    {"entity_id": "lock.front", "state": "locked", "attributes": {"friendly_name": "Front door lock"}},
    {"entity_id": "cover.garage", "state": "closed", "attributes": {"device_class": "garage", "friendly_name": "Garage door"}},
]  # fmt: skip

PREFS = {
    "energy_sources": [
        {"type": "solar", "stat_energy_from": "sensor.solar"},
        {"type": "grid", "flow_from": [{"stat_energy_from": "sensor.import"}], "flow_to": [{"stat_energy_to": "sensor.export"}]},
    ],
    "device_consumption": [{"stat_consumption": "sensor.ev"}, {"stat_consumption": "sensor.oven", "name": "Oven"}],
}  # fmt: skip


def _months(per_month: list[float]) -> list[dict]:
    return [{"start": _ms(datetime(2026, m + 1, 1)), "change": v} for m, v in enumerate(per_month)]


class FakeHA:
    def __init__(self) -> None:
        self.history_calls = 0

    def states(self):
        return STATES

    def ws(self, requests):
        out = []
        for r in requests:
            t = r["type"]
            if t == "energy/get_prefs":
                out.append(PREFS)
            elif t == "recorder/get_statistics_metadata":
                out.append(
                    [
                        {"statistic_id": i, "statistics_unit_of_measurement": "kWh"}
                        for i in r["statistic_ids"]
                    ]
                )
            elif t == "recorder/statistics_during_period" and r["period"] == "month":
                out.append(
                    {
                        "sensor.solar": _months(
                            [300, 320, 280, 200, 150, 120, 130, 180, 260, 360, 400]
                        ),
                        "sensor.import": _months([150] * 11),
                        "sensor.export": _months([100] * 11),
                        "sensor.ev": _months([160] * 11),
                        "sensor.oven": _months([50] * 11),
                    }
                )
            elif t == "recorder/statistics_during_period" and r["period"] == "day":
                out.append(
                    {
                        "sensor.lounge_temp": [
                            {
                                "start": _ms(datetime(2026, 1, 20)),
                                "mean": 24.0,
                                "min": 20.0,
                                "max": 34.5,
                            },
                            {
                                "start": _ms(datetime(2026, 7, 9)),
                                "mean": 18.0,
                                "min": 9.5,
                                "max": 20.0,
                            },
                        ],
                        "sensor.bed_temp": [
                            {
                                "start": _ms(datetime(2026, 7, 9)),
                                "mean": 16.0,
                                "min": 6.8,
                                "max": 18.0,
                            }
                        ],
                    }
                )
            elif t == "config/area_registry/list":
                out.append(
                    [
                        {"area_id": "k", "name": "Kitchen"},
                        {"area_id": "l", "name": "Lounge"},
                        {"area_id": "b", "name": "Bedroom"},
                    ]
                )
            elif t == "config/entity_registry/list":
                out.append(
                    [
                        {"entity_id": "sensor.lounge_temp", "area_id": "l"},
                        {"entity_id": "sensor.bed_temp", "device_id": "d1"},
                        {"entity_id": "binary_sensor.kitchen_motion", "area_id": "k"},
                        {"entity_id": "binary_sensor.lounge_motion", "area_id": "l"},
                        {"entity_id": "sensor.ev", "name": "EV charger"},
                        {"entity_id": "light.kitchen", "name": "Kitchen light"},
                        {"entity_id": "switch.plug_child_lock", "entity_category": "config"},
                    ]
                )
            elif t == "config/device_registry/list":
                out.append([{"id": "d1", "area_id": "b"}])
            else:
                out.append(None)
        return out

    def history(self, ids, start, end):
        self.history_calls += 1
        if start.date() < date(2026, 10, 26):
            return []  # the recorder only goes back ten days

        def rows(eid, seq):
            return [
                {
                    "entity_id": eid,
                    "state": s,
                    "last_changed": (start + timedelta(hours=h)).isoformat(),
                }
                for h, s in seq
            ]

        return [
            rows("binary_sensor.front_door", [(0, "off"), (7, "on"), (7.1, "off"), (17, "on"), (17.1, "off")]),
            rows("binary_sensor.kitchen_motion", [(0, "off"), (7, "on"), (8, "off"), (18, "on"), (19, "off"), (20, "on"), (21, "off")]),
            rows("binary_sensor.lounge_motion", [(0, "off"), (19, "on"), (22, "off")]),
            rows("light.kitchen", [(0, "off"), (18, "on"), (23, "off")]),
            rows("media_player.lounge_tv", [(0, "off"), (19, "on"), (21, "off")]),
            rows("zone.home", [(0, "2"), (9, "0"), (15, "1")]),
            rows("switch.hall_light", [(0, "off")] + [(h, s) for i in range(6) for h, s in ((7 + 2 * i, "on"), (7.5 + 2 * i, "off"))]),
            rows("switch.plug_child_lock", [(0, "off"), (1, "on"), (2, "off"), (3, "on"), (4, "off"), (5, "on")]),
            rows("lock.front", [(0, "locked"), (8, "unlocking"), (8.01, "unlocked"), (8.1, "locked")]),
            rows("cover.garage", [(0, "closed"), (7, "opening"), (7.02, "open"), (7.2, "closing"), (7.3, "closed")]),
        ]  # fmt: skip


@pytest.fixture
def year(tmp_path):
    return server.gather(FakeHA(), tmp_path, NOW)


def test_energy_is_found_in_the_energy_setup(year):
    f = {x["key"]: x for x in year["facts"]}
    assert f["solar"]["value"] == "2.7" and f["solar"]["unit"] == "MWh"  # 2,700 kWh
    assert f["self"]["value"] == "49%"  # used 1650+2700-1100 = 3250, 1650 of it imported
    lists = {x["key"]: x for x in year["lists"]}
    assert lists["devices"]["rows"][0]["name"] == "EV charger"
    assert lists["devices"]["rows"][1]["name"] == "Oven"


def test_climate_finds_rooms_and_the_extremes(tmp_path):
    got = server.climate_facts(
        FakeHA(), STATES, {"sensor.lounge_temp": "Lounge", "sensor.bed_temp": "Bedroom"}, 2026, NOW
    )
    assert got["facts"]["hottest"]["name"] == "Hottest day, 20 January"
    assert got["facts"]["hottest"]["value"] == "34.5"
    assert got["facts"]["coldest"]["name"] == "Coldest day, 9 July"
    assert got["lists"]["warmest"]["rows"][0]["name"] == "Lounge"


def test_trackers_pick_the_right_entities():
    t = server.trackers(
        STATES, {"binary_sensor.kitchen_motion": "Kitchen", "binary_sensor.lounge_motion": "Lounge"}
    )
    assert t["doors"] == ["binary_sensor.front_door"]  # not the window
    assert t["lights"] == ["light.kitchen"]  # not the group
    assert t["tv"] == ["media_player.lounge_tv"] and t["zone"] == ["zone.home"]
    assert sorted(t["motion"]) == ["binary_sensor.kitchen_motion", "binary_sensor.lounge_motion"]


def test_ledger_backfills_what_history_has_and_says_since(year, tmp_path):
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert min(ledger["days"]) == "2026-10-26" and max(ledger["days"]) == "2026-11-04"
    assert ledger["empty_before"] == "2026-10-26"
    f = {x["key"]: x for x in year["facts"]}
    assert f["doors"]["value"] == "20" and f["doors"]["since"] == "since 26 Oct"  # 2 a day, 10 days
    assert f["lights"]["value"] == "50" and f["tv"]["value"] == "20"
    assert f["away"]["value"] == "2.5"  # 6 h a day for 10 days
    rooms = {x["key"]: x for x in year["lists"]}["rooms"]
    assert rooms["rows"][0]["name"] == "Kitchen" and rooms["rows"][0]["value"] == "75%"


def test_ledger_does_not_ask_again_for_what_is_gone(tmp_path):
    ha = FakeHA()
    track = server.trackers(STATES, {})
    server.update_ledger(ha, tmp_path / "l.json", track, {}, NOW.date())
    first = ha.history_calls
    server.update_ledger(ha, tmp_path / "l.json", track, {}, NOW.date())
    assert ha.history_calls == first  # nothing new, nothing older to try


def test_story_and_personality(year):
    seq = server.story(year)
    assert seq[0] == ("intro", 0) and seq[-1] == ("summary", 0)
    assert ("number", 0) in seq and ("top", 0) in seq and ("leaderboard", 1) in seq
    per = server.personality(year["facts"])
    assert per["name"] == "The Homebody"  # 49% self-powered, 2.5 days away
    assert (
        server.personality([{"key": "self", "raw": 63, "value": "63%"}])["name"]
        == "The Sun Harvester"
    )


def test_prompts_carry_the_figures(year):
    base = {
        "title": "Home Wrapped", "year": 2026, "palette": "wrapped", "land": False, "home": "",
        "pets": "two cats", "facts": year["facts"], "lists": year["lists"],
        "personality": server.personality(year["facts"]), "segments": 10, "position": 3,
    }  # fmt: skip
    s = server.slide_prompt({**base, "slide": "summary", "index": 0})
    assert "'Home Wrapped 2026'" in s and "'Solar generated' '2.7 MWh'" in s and "two cats" in s
    assert "10 segments, the first 4 filled" in s and "electric lime" in s
    n = server.slide_prompt({**base, "slide": "number", "index": 2})
    assert "'Door openings since 26 Oct'" in n and "'20'" in n and "That's 2.0 a day" in n
    t = server.slide_prompt({**base, "slide": "top", "index": 0})
    assert "'1 Kitchen 75%'" in t
    assert server.figures({**base, "slide": "number", "index": 0}) == ["Solar generated 2.7 MWh"]


def test_slide_size_follows_the_cell():
    assert server.slide_size(1200, 1600) == (1200, 1600)
    assert server.slide_size(800, 480) == (1600, 960)
    assert server.slide_size(0, 0) == (1200, 1600)


class FakeCore:
    def __init__(self, configured=True):
        self.configured = configured

    def is_configured(self):
        return self.configured

    def coerce_error(self, err):
        return str(err)


@pytest.fixture
def app():
    def make(configured=True):
        a = Flask(__name__)
        a.config["PLUGIN_REGISTRY"] = {
            "ha_core": SimpleNamespace(server_module=FakeCore(configured))
        }
        return a

    return make


def test_samples_until_set_up(app, tmp_path):
    with app(False).app_context():
        d = server.fetch({}, {}, ctx={"data_dir": str(tmp_path)})
    assert "painted" not in d and "Home Assistant" in d["note"]
    assert (server.SAMPLE_DIR / d["sample"]).exists()
    with app().app_context(), patch.object(server.artwork.fal, "painting_key", return_value=""):
        d = server.fetch({}, {}, ctx={"data_dir": str(tmp_path)})
    assert "AI key" in d["note"]


def _jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (60, 80), "black").save(buf, "JPEG")
    return buf.getvalue()


def test_paints_the_slide_then_shows_it(app, tmp_path):
    seen: dict = {}

    def fake_generate(self, model, prompt="", *, refs=(), aspect="4:3", retries=1, **kw):
        seen.update(model=model, prompt=prompt, **kw)
        return _jpeg()

    year = server.gather(FakeHA(), tmp_path, NOW)
    ctx = {"data_dir": str(tmp_path), "cell_id": "c1", "cell_w": 1200, "cell_h": 1600}
    with (
        app().app_context(),
        patch.object(server, "cached_gather", return_value=year),
        patch.object(server.artwork.fal.FalClient, "generate", fake_generate),
    ):
        first = server.fetch({"slide": "top"}, {"fal_api_key": "k"}, ctx=ctx)
        assert "painted" not in first
        for _ in range(100):
            if seen:
                break
            time.sleep(0.05)
        time.sleep(0.2)
        second = server.fetch({"slide": "top"}, {"fal_api_key": "k"}, ctx=ctx)
    assert (
        seen["model"] == "gpt_image_t2i"
        and seen["quality"] == "high"
        and seen["size"] == (1200, 1600)
    )
    assert "'1 Kitchen 75%'" in seen["prompt"]
    assert second["painted"]["url"].startswith("data:image/")
    assert second["painted"]["figures"][0] == "Kitchen 75%"


def test_manifest():
    m = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert m["kind"] == "widget" and len(m["description"]) <= 280
    names = [o["name"] for o in m["cell_options"]]
    assert len(names) == len(set(names))
    assert (ROOT / "static" / "fonts" / "inter_tight-normal-400_900.woff2").exists()


def test_fresh_payload_brings_todays_figures(year):
    old = {"slide": "number", "index": 9, "title": "T", "palette": "gray", "size": [1200, 1600],
           "facts": [], "lists": [], "segments": 1, "position": 0}  # fmt: skip
    p = server.fresh_payload(old, year)
    assert p["index"] == len(year["facts"]) - 1  # clamped to what exists now
    assert p["facts"] == year["facts"] and p["palette"] == "gray" and p["size"] == [1200, 1600]
    assert p["position"] == server.story(year).index(("number", p["index"]))


def test_admin_sheet_lists_slides_and_the_year(year, tmp_path):
    server.ART.keep(
        "top-0-2026-w45-abc", tmp_path, _jpeg(),
        {"title": "x", "payload": {"palette": "wrapped", "size": [1200, 1600]}, "figures": ["Kitchen 75%"], "painted": "2026-11-05"},
    )  # fmt: skip
    server.ART.keep("intro-0-2026-w40-abc", tmp_path, _jpeg(), {"title": "y", "payload": {}})
    (tmp_path / "year.json").write_text(json.dumps(year))
    s = server.sheet(tmp_path, NOW)
    assert [x["kind"] for x in s["slides"]] == ["Top five", "Intro"]
    assert s["slides"][0]["current"] and not s["slides"][1]["current"]
    assert s["slides"][0]["figures"] == ["Kitchen 75%"] and s["slides"][0]["size"] == "1200x1600"
    assert s["ledger"]["days"] == 10 and s["ledger"]["first"] == "2026-10-26"
    assert s["year"]["facts"][0]["key"] == "solar"


def test_blueprint_is_mounted(app):
    bp = server.blueprint()
    assert bp.name == "home_wrapped_admin"
    assert (ROOT / "templates" / "home_wrapped" / "index.html").exists()


class HotHA(FakeHA):
    """A 3D printer's hot end and a NAS disk sit in rooms, and an appliance
    with an innocent name runs hot all year."""

    def ws(self, requests):
        r = requests[0]
        if r["type"] == "recorder/statistics_during_period" and r["period"] == "day":
            day = {"start": _ms(datetime(2026, 2, 1)), "mean": 21.0, "min": 18.0, "max": 31.0}
            return [
                {
                    "sensor.study_temp": [day],
                    "sensor.prusa_nozzle_temperature": [{**day, "mean": 120.0, "max": 225.0}],
                    "sensor.study_gizmo": [{**day, "mean": 70.0, "max": 90.0}],
                    "sensor.lounge_temp": [{**day, "max": 300.0}],  # a glitch on a real room
                }
            ]
        return super().ws(requests)


def test_hot_appliances_are_not_rooms():
    def temp(eid, name):
        return {
            "entity_id": eid,
            "state": "20",
            "attributes": {
                "device_class": "temperature",
                "state_class": "measurement",
                "unit_of_measurement": "°C",
                "friendly_name": name,
            },
        }

    states = [
        temp("sensor.study_temp", "Study temperature"),
        temp("sensor.prusa_nozzle_temperature", "Prusa nozzle temperature"),
        temp("sensor.study_gizmo", "Gizmo"),
        temp("sensor.lounge_temp", "Lounge temperature"),
    ]
    areas = {s["entity_id"]: "Study" for s in states} | {"sensor.lounge_temp": "Lounge"}
    got = server.climate_facts(HotHA(), states, areas, 2026, NOW)
    assert (
        got["facts"]["hottest"]["value"] == "31.0"
    )  # not 225 (nozzle), 90 (gizmo) or 300 (glitch)


def test_diagnostic_sensors_get_no_room():
    class RegHA(FakeHA):
        def ws(self, requests):
            out = super().ws(requests)
            out[1] = out[1] + [
                {
                    "entity_id": "sensor.nas_disk_temp",
                    "area_id": "k",
                    "entity_category": "diagnostic",
                }
            ]
            return out

    areas, _, skip = server.areas_of(RegHA())
    assert "sensor.nas_disk_temp" in skip
    assert "sensor.nas_disk_temp" not in areas and areas["sensor.lounge_temp"] == "Lounge"


def test_events_name_the_busiest_things(year):
    ev = year["events"]
    assert ev[0] == {
        "text": "Since 26 Oct, you flicked the Hall light switch on 60 times!",
        "n": "60",
    }
    texts = [e["text"] for e in ev]
    assert "Since 26 Oct, the Front door opened 20 times!" in texts
    assert "Since 26 Oct, you flicked the Kitchen light on 10 times!" in texts
    assert "Since 26 Oct, you unlocked the Front door lock 10 times!" in texts
    assert "Since 26 Oct, the Garage door opened 10 times!" in texts
    assert "Since 26 Oct, the Lounge tv went on 10 times!" in texts
    assert not any(
        "child lock" in t.lower() for t in texts
    )  # a config switch, not one anybody flicks


def test_events_say_in_the_year_once_the_ledger_reaches_january():
    track = {"lights": ["light.a"]}
    ledger = {
        "days": {
            "2026-01-01": {"events": {"light.a": 8000}},
            "2026-06-01": {"events": {"light.a": 678}},
        }
    }
    got = server.events_of(ledger, track, {"light.a": "The Porch light"}, 2026)
    assert got == [{"text": "In 2026, you flicked the Porch light on 8,678 times!", "n": "8,678"}]


def test_kept_days_without_events_are_tallied_again(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"days": {"2026-11-04": {"doors": 1, "rooms": {}}}}))
    track = server.trackers(STATES, {})
    ledger = server.update_ledger(FakeHA(), path, track, {}, NOW.date())
    assert ledger["days"]["2026-11-04"]["events"]["binary_sensor.front_door"] == 2


def test_the_band_carries_an_event(app, tmp_path):
    with app(False).app_context():
        d = server.fetch({"palette": "spectra"}, {}, ctx={"data_dir": str(tmp_path)})
    assert d["event"]["n"] in d["event"]["text"] and d["palette"] == "spectra"
    year = server.gather(FakeHA(), tmp_path, NOW)
    with (
        app().app_context(),
        patch.object(server, "cached_gather", return_value=year),
        patch.object(server.artwork.fal, "painting_key", return_value=""),
    ):
        d = server.fetch({}, {}, ctx={"data_dir": str(tmp_path)})
    assert d["event"] == server.SAMPLE_EVENT  # no key yet: still a sample


def test_prompt_keeps_the_band_clear(year):
    p = server.slide_prompt({
        "title": "Home Wrapped", "year": 2026, "palette": "gray", "land": False, "home": "", "pets": "",
        "facts": year["facts"], "lists": year["lists"], "personality": server.personality(year["facts"]),
        "segments": 3, "position": 0, "slide": "intro", "index": 0,
    })  # fmt: skip
    assert "bottom eighth" in p


def test_the_band_can_be_switched_off(year, app, tmp_path):
    base = {
        "title": "Home Wrapped", "year": 2026, "palette": "gray", "land": False, "home": "", "pets": "",
        "facts": year["facts"], "lists": year["lists"], "personality": server.personality(year["facts"]),
        "segments": 3, "position": 0, "slide": "intro", "index": 0,
    }  # fmt: skip
    off = server.slide_prompt({**base, "band": False})
    assert "bottom eighth" not in off and "right down to its bottom edge" in off
    with app(False).app_context():
        d = server.fetch({"foot_band": False}, {}, ctx={"data_dir": str(tmp_path)})
    assert d["band"] is False
    m = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    strip = next(o for o in m["cell_options"] if o["name"] == "show_strip")
    assert strip["show_if"] == {"foot_band": True}
