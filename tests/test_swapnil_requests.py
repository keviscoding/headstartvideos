"""Swapnil's requests (Discord, 2026-10-29): skip YouTube thumbnails to save Kie credits, Nano Banana Pro (Gemini 3 Pro
Image) as a Kie image model with its price in quotes, Claude used across all videos, and moving recipe previews."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
sys.path.insert(0, str(ROOT / "tests"))
import cr_media as media  # noqa: E402
import cr_studio_tools as tools  # noqa: E402
from test_studio_claude_code import FakeStudio, ok  # noqa: E402,F401

JS = (ROOT / "local_runtime/cr_workspace.js").read_text(encoding="utf-8")
HTML = (ROOT / "local_runtime/cr_workspace.html").read_text(encoding="utf-8")


@pytest.fixture
def studio(monkeypatch, tmp_path):
    fake = FakeStudio()
    monkeypatch.setattr(tools, "call", fake)
    monkeypatch.setattr(tools, "STATE", tmp_path / "state")
    monkeypatch.setattr(tools, "thumbnail", lambda row: "")
    return fake


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(media.cr_bridge, "SETTINGS", tmp_path / ".cr-connection.json")
    return media.cr_bridge


# ── 1. no YouTube thumbnails ────────────────────────────────────────────────────────────────────────────────────

def test_thumbnails_off_drop_from_the_quote_and_from_the_video(studio):
    on = ok("quote", {"style": "documentary", "minutes": 10})
    off = ok("quote", {"style": "documentary", "minutes": 10, "options": {"thumbnails": False}})
    assert on["media"]["thumbnails"]["count"] == 2 and on["media"]["thumbnails"]["usd"] == 0.04
    assert off["media"]["thumbnails"]["count"] == 0 and off["media"]["thumbnails"]["usd"] == 0
    assert round(on["media"]["per_video_usd"] - off["media"]["per_video_usd"], 2) == 0.04
    assert "2 YouTube thumbnails" in on["summary"] and "no YouTube thumbnails" in off["summary"]
    assert on["quote_id"] != off["quote_id"]           # a yes to one cost never starts the other
    out = ok("make_videos", {"titles": ["Why Rome fell"], "style": "documentary", "minutes": 10,
                             "where": "this_computer", "quote_id": off["quote_id"], "options": {"thumbnails": False}})
    assert out["started"] is True
    assert "thumbnail" not in studio.posted("/api/run")[0]["steps"]


def test_more_thumbnails_cost_more_and_a_short_makes_none(studio):
    four = ok("quote", {"style": "documentary", "minutes": 10, "options": {"thumbnail_count": 4}})
    assert four["media"]["thumbnails"] == {**four["media"]["thumbnails"], "count": 4, "usd": 0.08}
    short = ok("quote", {"style": "cr-shorts-3d"})
    assert short["media"]["thumbnails"]["count"] == 0 and "thumbnail" not in short["summary"]


def test_the_usual_quote_keeps_its_id():
    """The default (2 thumbnails) hashes as before, so a quote shown just before an update still starts."""
    import hashlib
    row = {"name": "documentary"}
    key = json.dumps([row["name"], 1, 10, "this_computer", ["youtube"]])
    assert "q_" + hashlib.sha256(key.encode()).hexdigest()[:16] == _quote_id(row, thumbnails=2)
    assert _quote_id(row, thumbnails=0) != _quote_id(row, thumbnails=2)


def _quote_id(row, thumbnails, monkeypatch=None):
    import tempfile
    saved = tools.STATE
    tools.STATE = Path(tempfile.mkdtemp())
    try:
        cat = {"prices": {"features": {"youtube": {"per_min": 0.03}}, "voice": {"per_min": 0}}}
        original = tools.queue_line
        tools.queue_line = lambda: {"before": 0, "on_hold": False, "ahead_gb": 0}
        try:
            return tools.quote_for(cat, row, titles=1, minutes=10, where="this_computer", features={"youtube": True},
                                   thumbnails=thumbnails)["quote_id"]
        finally:
            tools.queue_line = original
    finally:
        tools.STATE = saved


def test_create_says_the_thumbnails_with_their_cost_and_turns_them_off_in_one_click():
    assert 'id="crw-summary-thumbs"' in HTML and 'id="crw-summary-thumbs-toggle"' in HTML
    assert "Make YouTube thumbnails" in HTML
    assert "$('crw-summary-thumbs-toggle').addEventListener('click'" in JS
    # remembered with the other choices, and the video's steps leave the thumbnail out when it's off
    assert "thumbnails: checked('crw-thumbnails')" in JS
    assert "if(checked('crw-thumbnails'))steps.push('thumbnail')" in JS
    # Projects cards show a frame of the video, never a thumbnail file
    assert "/cr-workspace/preview/project/" in JS


# ── 4b. Nano Banana Pro (Gemini 3 Pro Image) on Kie ────────────────────────────────────────────────────────────────

def test_nano_banana_pro_is_a_kie_model_with_its_reference_pictures():
    choice = media.image_model("nano-banana-pro")
    assert choice["label"].startswith("Nano Banana Pro") and "Gemini 3 Pro Image" in choice["label"]
    model, inputs = media.image_input(choice, "A castle", ["https://files.example/ref.png"])
    assert model == "nano-banana-pro" and inputs["image_input"] == ["https://files.example/ref.png"]
    assert inputs["resolution"] == "1K" and inputs["output_format"] == "png" and inputs["aspect_ratio"] == "16:9"
    # never one of Studio's automatic backups: the dearest model runs only when the creator picks it
    assert "nano-banana-pro" not in media.PROVIDERS["kie"]["image_fallbacks"]


def test_the_quote_prices_pictures_at_the_creators_model(settings):
    settings.save_settings({"images": {"provider": "kie", "model": "nano-banana-pro", "fallbacks": []}})
    data = media.price_catalog({"prices": {"picture": 0.02, "features": {"ai_images": {"per_min": 0.05, "note": "x"},
                                                                         "youtube": {"per_min": 0.03}}}})
    assert data["picture_price"] == {"usd": 0.09, "model": "nano-banana-pro",
                                     "label": "Nano Banana Pro (Gemini 3 Pro Image)"}
    assert data["prices"]["picture"] == 0.09 and data["prices"]["features"]["ai_images"]["per_min"] == 0.225
    assert data["prices"]["features"]["youtube"]["per_min"] == 0.03
    # the default model keeps the engine's own estimates
    settings.save_settings({"images": {"provider": "kie", "model": "google/nano-banana", "fallbacks": []}})
    same = media.price_catalog({"prices": {"picture": 0.02, "features": {"ai_images": {"per_min": 0.05}}}})
    assert same["prices"]["features"]["ai_images"]["per_min"] == 0.05 and same["picture_price"]["usd"] == 0.02
    # another image service: the engine's estimate, untouched
    settings.save_settings({})
    other = media.price_catalog({"prices": {"picture": 0.027, "features": {}}})
    assert other["picture_price"]["usd"] == 0.027 and other["prices"]["picture"] == 0.027


def test_thumbnails_are_quoted_at_the_creators_picture_price(studio, monkeypatch):
    from test_studio_claude_code import CATALOG
    monkeypatch.setitem(CATALOG, "picture_price", {"usd": 0.09, "model": "nano-banana-pro", "label": "Pro"})
    q = ok("quote", {"style": "documentary", "minutes": 10})
    assert q["media"]["thumbnails"]["usd"] == 0.18


def test_setup_lists_prices_and_never_ticks_every_model_as_a_backup():
    page = (ROOT / "local_runtime/cr_setup.html").read_text(encoding="utf-8")
    assert "function imageChoiceText" in page and "usd_per_image" in page
    assert "image_models?.map(m=>m.id))" not in page   # six models were five backups: over Setup's limit of four
    assert "image_fallbacks||[]).filter(id=>id!==$('image-model').value).slice(0,4)" in page
