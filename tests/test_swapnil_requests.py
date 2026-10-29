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
    # ...even when the engine's estimate isn't Kie's $0.02 (another image service's key on this computer): a creator
    # who never chose a model keeps the quote they had
    kept = media.price_catalog({"prices": {"picture": 0.027, "features": {"ai_images": {"per_min": 0.0675}}}})
    assert kept["prices"]["picture"] == 0.027 and kept["prices"]["features"]["ai_images"]["per_min"] == 0.0675
    settings.save_settings({"images": {"provider": "kie", "fallbacks": []}})
    assert media.price_catalog({"prices": {"picture": 0.027, "features": {}}})["prices"]["picture"] == 0.027
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
    # the backups ticked now stay ticked (a creator's own saved choice is never replaced by Studio's usual list)
    assert "querySelectorAll('input:checked')].map(el=>el.value).filter(id=>id!==$('image-model').value).slice(0,4)" in page


# ── 2. Claude used across every video ──────────────────────────────────────────────────────────────────────────────

import cr_claude  # noqa: E402
import cr_claude_use  # noqa: E402

A, B = "crv_" + "a" * 24, "crv_" + "b" * 24


def _cache(home, project, n, **data):
    folder = home / "output" / "cr-claude-cache" / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"cc_{n:04d}.json"
    path.write_text(json.dumps({"state": "complete", **data}), encoding="utf-8")
    return path


def test_overall_counts_today_the_week_and_all_time_by_video(tmp_path):
    now = time.time()
    tokens = {"input_tokens": 1000, "cache_read_input_tokens": 9000, "output_tokens": 2000}
    _cache(tmp_path, A, 1, seconds=120, at=now - 60, tokens=tokens)              # today
    _cache(tmp_path, A, 2, seconds=60, at=now - 3 * 86400, tokens=tokens)        # this week
    old = _cache(tmp_path, B, 1, seconds=30)                                     # before Studio 115: no at, no tokens
    os.utime(old, (now - 30 * 86400, now - 30 * 86400))
    _cache(tmp_path, B, 2, state="failed", seconds=5, at=now - 40 * 86400)
    out = cr_claude_use.overall(tmp_path, {A: {"slug": "rome--a", "title": "Why Rome fell"}}, now=now)
    assert (out["today"]["calls"], out["week"]["calls"], out["all"]["calls"]) == (1, 2, 4)
    assert out["today"]["tokens_in"] == 10000 and out["today"]["tokens_out"] == 2000
    assert out["all"]["tokens_counted"] == 2 and out["all"]["minutes"] == 4
    first, second = out["videos"]
    assert first["title"] == "Why Rome fell" and first["slug"] == "rome--a" and first["calls"] == 2
    assert second["title"] == "A deleted video" and second["tokens_counted"] == 0
    assert out["line"].startswith("Claude used by Studio: today 1 call, about 2 min of Claude time, 12K tokens;")
    assert "not a percentage" in out["note"] and "%" not in out["line"]


def test_overall_with_no_calls_yet(tmp_path):
    out = cr_claude_use.overall(tmp_path, {})
    assert out["all"]["calls"] == 0 and out["videos"] == [] and "no Claude calls" in out["line"]


def test_one_videos_line_says_its_tokens(tmp_path):
    _cache(tmp_path, A, 1, seconds=90, at=time.time(), tokens={"input_tokens": 1500, "output_tokens": 500})
    use = cr_claude_use.summary(tmp_path, tmp_path / "no-job", A)
    assert use["line"] == "Claude so far for this video: 1 call, about 2 min of Claude time, 2K tokens."


def test_each_finished_call_keeps_when_and_its_tokens(tmp_path, monkeypatch):
    import subprocess
    import types
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {"ready": True, "account_fingerprint": "a"})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (None, "test"))
    bridge = types.SimpleNamespace(HOME=tmp_path, load_settings=lambda: {"brain_mode": "claudecode", "costs_accepted": True},
                                   current_project=lambda: {"id": A},
                                   authorize_stage=lambda s: {"execute": True, "authorization_token": "t" * 32,
                                                              "selected_model": cr_claude.GENERAL_MODEL},
                                   finish_stage=lambda r: None)
    answer = json.dumps({"type": "result", "subtype": "success", "result": "ok",
                         "usage": {"input_tokens": 9, "output_tokens": 11, "cache_read_input_tokens": 100}})
    run = lambda args, **k: subprocess.CompletedProcess(args, 0, answer, "")
    runtime = cr_claude.Runtime(types.SimpleNamespace(), bridge, "/fake/claude", run)
    before = time.time() - 1
    runtime.run(["/fake/claude", "--print", "--output-format", "json"], input="prompt")
    [cache] = list((tmp_path / "output" / "cr-claude-cache").rglob("*.json"))
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["state"] == "complete" and saved["at"] >= before
    assert saved["tokens"] == {"input_tokens": 9, "output_tokens": 11, "cache_read_input_tokens": 100}


def test_a_long_plain_text_call_keeps_its_tokens_and_its_receipt(tmp_path, monkeypatch):
    """A long text answer is read from Claude Code's stream (cr_claude long_text): its usage was in the stream but the
    plain answer has none, so these calls counted without tokens. Kept locally; the receipt is unchanged."""
    import subprocess
    import types
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {"ready": True, "account_fingerprint": "a"})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (cr_claude.LONG_ANSWER_TOKENS, "test"))
    receipts = []
    bridge = types.SimpleNamespace(HOME=tmp_path, load_settings=lambda: {"brain_mode": "claudecode", "costs_accepted": True},
                                   current_project=lambda: {"id": A},
                                   authorize_stage=lambda s: {"execute": True, "authorization_token": "t" * 32,
                                                              "selected_model": cr_claude.GENERAL_MODEL},
                                   finish_stage=receipts.append)
    stream = json.dumps({"type": "result", "subtype": "success", "result": "the whole kit",
                         "usage": {"input_tokens": 7, "output_tokens": 30000, "cache_read_input_tokens": 50}}) + "\n"
    seen = []
    run = lambda args, **k: (seen.append(list(args)), subprocess.CompletedProcess(args, 0, stream, ""))[1]
    runtime = cr_claude.Runtime(types.SimpleNamespace(), bridge, "/fake/claude", run)
    out = runtime.run(["/fake/claude", "--print", "--output-format", "text"], input="prompt")
    assert "stream-json" in seen[0] and out.stdout == "the whole kit"
    [cache] = list((tmp_path / "output" / "cr-claude-cache").rglob("*.json"))
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["tokens"] == {"input_tokens": 7, "output_tokens": 30000, "cache_read_input_tokens": 50}
    assert not any(k.endswith("_tokens") for k in receipts[0]["usage"])        # what the server gets: as before


def test_a_partial_token_sum_says_how_many_calls_it_covers(tmp_path):
    _cache(tmp_path, A, 1, seconds=60, at=time.time(), tokens={"input_tokens": 1500, "output_tokens": 500})
    _cache(tmp_path, A, 2, seconds=60, at=time.time())                     # a plain-text answer: no usage reported
    use = cr_claude_use.summary(tmp_path, tmp_path / "no-job", A)
    assert use["line"].endswith("2K tokens (reported for 1 of the 2 calls).")
    assert "(reported for 1 of the 2 calls)" in cr_claude_use.overall(tmp_path, {})["line"]
    assert "t.tokens_counted < t.calls" in JS


def test_projects_and_claude_code_show_the_overall_use(studio, monkeypatch):
    assert 'id="crw-claude-overall"' in HTML and "api('/cr-workspace/claude-use')" in JS
    recover = (ROOT / "local_runtime/cr_recover.py").read_text(encoding="utf-8")
    assert '@app.get("/cr-workspace/claude-use")' in recover
    import cr_claude_code as ccc
    assert "claude_usage" in ccc.FREE_TOOLS
    answer = {"today": {"calls": 1}, "week": {"calls": 2}, "all": {"calls": 3}, "line": "Claude used by Studio: …",
              "note": "not a percentage", "videos": [{"title": str(i)} for i in range(20)]}
    original = studio.__call__
    monkeypatch.setattr(tools, "call", lambda m, p, b=None, **k: answer if p == "/cr-workspace/claude-use"
                        else original(m, p, b, **k))
    out = ok("claude_usage", {"videos": 3})
    assert len(out["videos"]) == 3 and "not a percentage" in out["what_to_tell_them"]


# ── 3. recipes move on hover ───────────────────────────────────────────────────────────────────────────────────────

import cr_recipe_clips as clips  # noqa: E402


def test_every_recipe_loop_is_a_small_public_moodboard_clip():
    folder = ROOT / "webapp/static/moodboard"
    for recipe, name in clips.CLIPS.items():
        path = folder / (name + ".mp4")
        assert path.is_file(), f"{recipe}: {path} is gone; Studio's recipe cards play it from channelrecipe.com"
        assert path.stat().st_size < 300 * 1024, "keep the hover download light"
    assert "cr-manga-recaps" not in clips.CLIPS          # those panels may belong to a real webtoon
    import cr_presentation
    assert set(clips.CLIPS) <= set(cr_presentation.RECIPES)


def test_a_ready_made_recipe_plays_our_loop_and_a_creators_style_its_own_video():
    ours = clips.clip_for("cr-stick-stories", "/f/video/mine--a")
    assert ours == {"url": "https://channelrecipe.com/static/moodboard/gladiator-sold.mp4", "start": 0.0, "end": 0.0,
                    "kind": "recipe", "vertical": False}
    assert clips.clip_for("cr-shorts-3d")["vertical"] is True
    mine = clips.clip_for("cr_clone_abc", "/f/video/my-video--abc")
    assert mine == {"url": "/f/video/my-video--abc", "start": 8.0, "end": 14.0, "kind": "yours", "vertical": False}
    assert clips.clip_for("cr_clone_abc") is None and clips.clip_for("documentary", "https://elsewhere/x.mp4") is None
    assert "documentary" in clips.missing(["documentary", "cr-stick-stories"])


def test_create_and_the_gallery_play_the_clip_only_when_asked():
    workspace = (ROOT / "local_runtime/cr_workspace.py").read_text(encoding="utf-8")
    assert 'row["preview_clip"] = cr_recipe_clips.clip_for(' in workspace
    assert "function recipeClip(" in JS and "v.preload = 'none'" in JS and "v.muted = true" in JS
    assert "['mouseenter', true], ['focus', true], ['mouseleave', false], ['blur', false]" in JS
    assert "prefers-reduced-motion: reduce" in JS
    gallery = (ROOT / "local_runtime/cr_gallery.html").read_text(encoding="utf-8")
    assert "r.preview_clip && r.preview_clip.url" in gallery and "v.preload = 'none'" in gallery
    # hover is a mouse's only: a phone's tap sends a mouseenter just before its click, so the tap played and stopped it
    assert "addEventListener('mouseenter'" not in gallery and "e.pointerType === 'mouse'" in gallery
    import cr_claude_code as ccc
    row = {"name": "cr-stick-stories", "label": "Stick", "preview_clip": clips.clip_for("cr-stick-stories")}
    assert ccc.card(row)["preview_clip"]["url"].endswith("gladiator-sold.mp4")
    assert tools.clip_link(clips.clip_for("x", "/f/video/a--b")).endswith("/f/video/a--b#t=8,14")
    assert tools.clip_link({"url": "javascript:alert(1)"}) == ""
