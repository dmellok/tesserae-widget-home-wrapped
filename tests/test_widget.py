"""home_wrapped: the manifest, number formatting, ranking, the card cycle, the
pictures (bundled or painted) and fetch() for a sample year and real entities.
No network: Home Assistant and the painter are faked."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from datetime import datetime
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


def _state(eid: str, state: str, unit: str = "") -> dict:
    attrs = {"unit_of_measurement": unit} if unit else {}
    return {"entity_id": eid, "state": state, "attributes": attrs}


STATES = [
    _state("counter.kettle", "1214"),
    _state("sensor.solar_year", "4108.4", "kWh"),
    _state("sensor.lights_year", "172260", "min"),
    _state("sensor.kitchen", "410", "h"),
    _state("sensor.lounge", "230", "h"),
    _state("sensor.study", "360", "h"),
    _state("counter.door", "3402"),
    _state("counter.fridge", "1977"),
    _state("sensor.broken", "unavailable"),
]

OPTIONS = {
    "stats": "Kettle boils = counter.kettle\nSolar generated = sensor.solar_year\n"
    "Hours of lights = sensor.lights_year\nBroken = sensor.broken\nnot a line",
    "rows_a": "Kitchen = sensor.kitchen\nLounge = sensor.lounge\nStudy = sensor.study",
    "rows_b": "Front door = counter.door\nFridge = counter.fridge\nKettle = counter.kettle",
}


class FakeCore:
    def get_states(self):
        return STATES

    def entity_choices(self):
        return []

    def coerce_error(self, err):
        return str(err)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["PLUGIN_REGISTRY"] = {"ha_core": SimpleNamespace(server_module=FakeCore())}
    with app.app_context():
        yield app


def test_manifest_and_bundled_files():
    m = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert m["kind"] == "widget" and len(m["description"]) <= 280
    names = [o["name"] for o in m["cell_options"]]
    assert len(names) == len(set(names))
    for f in ("anton-normal-400.woff2", "inter_tight-normal-400_900.woff2"):
        assert (ROOT / "static" / "fonts" / f).exists()
    for theme_id, theme in server.THEMES.items():
        assert (server.ART_DIR / f"hero-{theme_id}.jpg").exists()
        for name, *_ in theme["stats"]:
            assert (server.ART_DIR / server.bundled_name("spot", theme_id, name)).exists(), name


def test_units_roll_up():
    assert server.fmt(4108.4, "kWh") == ("4.1", "MWh")
    assert server.fmt(812, "kWh") == ("812", "kWh")
    assert server.fmt(812000, "Wh") == ("812", "kWh")
    assert server.fmt(172260, "min") == ("2,871", "h")
    assert server.fmt(1, "d") == ("1", "day")
    assert server.fmt(63.4, "%") == ("63%", "")
    assert server.fmt(21.43, "°C") == ("21.4", "°C")
    assert server.fmt(1214, "") == ("1,214", "")


def test_lists_rank_and_share():
    rows = [server.item("A", 230, "h"), server.item("B", 410, "h"), server.item("C", 0, "h")]
    out = server.ranked(rows, share=True)
    assert [r["name"] for r in out] == ["B", "A"]
    assert out[0]["value"] == "64%" and out[0]["bar"] == 1.0
    plain = server.ranked([server.item("A", 3402, ""), server.item("B", 1977, "")], share=False)
    assert plain[1]["value"] == "1,977" and plain[1]["bar"] == round(1977 / 3402, 4)


def test_cycle_moves_hourly_through_every_card():
    seen = set()
    for h in range(12):
        seen.add(server.card_for("cycle", datetime(2026, 11, 5, h), 3, 0))
    assert {c for c, _ in seen} == {"summary", "top", "number", "leaderboard", "trading"}
    assert {i for c, i in seen if c == "number"} == {0, 1, 2}
    assert server.card_for("number", datetime.now(), 3, 9) == ("number", 2)


def test_sample_year_uses_bundled_pictures(app):
    d = server.fetch({}, {}, ctx={})
    assert d["sample"] and d["card"] == "summary"
    assert d["stats"][0]["value"] == "1,214" and d["list_a"][0]["value"] == "41%"
    assert d["pic"] == {"file": "hero-home.jpg"}
    n = server.fetch({"card": "number", "stat": 3, "theme": "energy"}, {}, ctx={})
    assert n["stats"][2]["name"] == "Grid import"
    assert n["pic"] == {"file": "spot-grid-import.jpg"}


def test_real_entities(app):
    d = server.fetch({**OPTIONS, "card": "leaderboard"}, {}, ctx={})
    assert not d["sample"]
    names = [s["name"] for s in d["stats"]]
    assert names == ["Kettle boils", "Solar generated", "Hours of lights"]
    assert d["stats"][1]["value"] == "4.1" and d["stats"][1]["unit"] == "MWh"
    assert d["list_a"][0]["name"] == "Kitchen" and d["list_a"][0]["value"] == "41%"
    assert d["list_b"][0]["name"] == "Front door" and d["list_b"][0]["value"] == "3,402"


def test_prompts_name_the_subject_and_palette():
    p = server.hero_prompt("home", "sea", "a weatherboard cottage", "a ginger cat")
    assert "a weatherboard cottage" in p and "a ginger cat" in p and "royal blue" in p
    assert "no text" in p
    spot = server.spot_prompt("Treats", "gray")
    assert "'Treats'" in spot and "greyscale" in spot


def _jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "navy").save(buf, "JPEG")
    return buf.getvalue()


def test_own_home_is_painted_once_then_shown(app, tmp_path):
    seen: dict = {}

    def fake_generate(self, model, prompt="", *, refs=(), aspect="4:3", retries=1, **kw):
        seen.update(prompt=prompt, aspect=aspect, refs=len(refs), **kw)
        return _jpeg()

    ctx = {"data_dir": str(tmp_path), "cell_id": "c1"}
    opts = {"home": "a weatherboard cottage", "pets": "a ginger cat"}
    with patch.object(server.artwork.fal.FalClient, "generate", fake_generate):
        first = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
        assert first["pic"].get("file") == "hero-home.jpg"  # bundled while it paints
        key = server.art_key("hero", "home", "primary", opts["home"], opts["pets"], "")
        for _ in range(100):
            if server.ART.path(key, tmp_path) is not None:
                break
            time.sleep(0.05)
        second = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
    assert "a weatherboard cottage" in seen["prompt"] and seen["aspect"] == "1:1"
    assert seen["quality"] == "high" and seen["size"] == (1024, 1024) and seen["refs"] == 1
    assert second["pic"]["url"].startswith("data:image/")


def test_without_a_key_other_palettes_fall_back_to_bundled(app, tmp_path):
    with patch.object(server.artwork.fal, "painting_key", return_value=""):
        d = server.fetch(
            {"palette": "sunset"}, {}, ctx={"data_dir": str(tmp_path), "cell_id": "c1"}
        )
    assert d["pic"]["file"] == "hero-home.jpg"


def test_card_prompt_carries_every_figure():
    d = server.sample(server.THEMES["home"])
    p = {
        "card": "summary",
        "index": 0,
        "title": "Home Wrapped",
        "year": 2026,
        "theme": "home",
        "palette": "primary",
        "card_name": "Our House",
        "list_a_title": "Top rooms",
        "list_b_title": "Top appliances",
        "home": "",
        "pets": "",
        "land": False,
        **d,
    }
    text = server.card_prompt(p)
    for needle in ("'Home Wrapped 2026'", "'1 Kitchen'", "'5 Washer'", "'1,214'", "'3,402'"):
        assert needle in text
    p.update(card="number", index=5, rate="")
    assert "'81'" in server.card_prompt(p) and "'days'" in server.card_prompt(p)
    p.update(card="top")
    assert "'1 Kitchen 41%'" in server.card_prompt(p)


def test_card_size_follows_the_cell():
    assert server.card_size(False, 1200, 1600) == (1200, 1600)
    assert server.card_size(True, 800, 480) == (1600, 960)
    assert server.card_size(False, 0, 0) == (1200, 1600)


def test_painted_mode_paints_then_shows_the_card(app, tmp_path):
    seen: dict = {}

    def fake_generate(self, model, prompt="", *, refs=(), aspect="4:3", retries=1, **kw):
        seen.update(model=model, prompt=prompt, refs=len(refs), **kw)
        return _jpeg()

    ctx = {"data_dir": str(tmp_path), "cell_id": "c9", "cell_w": 1200, "cell_h": 1600}
    opts = {**OPTIONS, "render": "painted", "card": "top"}
    with patch.object(server.artwork.fal.FalClient, "generate", fake_generate):
        first = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
        assert "painted" not in first and first["paint_state"] in ("painting", "waiting", "ready")
        for _ in range(100):
            if (tmp_path / "last").exists() or seen:
                break
            time.sleep(0.05)
        time.sleep(0.2)
        second = server.fetch(opts, {"fal_api_key": "k"}, ctx=ctx)
    assert seen["model"] == "gpt_image_t2i" and seen["refs"] == 0
    assert seen["quality"] == "high" and seen["size"] == (1200, 1600)
    assert "'1 Kitchen 41%'" in seen["prompt"]
    assert second["painted"]["url"].startswith("data:image/")
    assert second["painted"]["figures"][0] == "Kitchen 41%"


def test_sample_year_never_paints(app, tmp_path):
    with patch.object(server.artwork.fal.FalClient, "generate", side_effect=AssertionError):
        d = server.fetch(
            {"render": "painted"}, {"fal_api_key": "k"}, ctx={"data_dir": str(tmp_path)}
        )
    assert "painted" not in d and d["sample"]
