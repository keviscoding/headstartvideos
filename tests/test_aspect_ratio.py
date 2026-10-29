"""Aspect ratio (Mac7834 and Altt, Discord 2026-10-29).

1. Studio's pages showed a vertical Short cropped to a wide band of its middle (a 16:9 card with object-fit cover);
   the downloaded file was right. The browser check is in test_frontier_workspace_browser; here, the CSS contracts.
2. A clone of a vertical Shorts channel said "Canvas 16:9 landscape" and made wide videos: styles learned before
   Studio 111 through Studio's own engines were saved without look.aspect and no update changed them, and a look
   without a single still fell back to wide. Old styles are upgraded once at start; a look with no stills of a
   channel listed as Shorts is vertical by its listing.
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "local_runtime"
sys.path.insert(0, str(RUNTIME))
import cr_style_clone as clone  # noqa: E402
import cr_style_look as look  # noqa: E402

LOOK = "lk_" + "a" * 24
OPERATION = "op_" + "b" * 24


def _portrait(folder, n=4, size=(540, 960)):
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", size, "#334455").save(folder / f"abcdefghijk_{i}.jpg")


# ── the look ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_look_without_stills_of_a_shorts_channel_is_vertical_by_its_listing(tmp_path):
    shorts = [{"id": "abcdefghijk", "short": True}, {"id": "mnopqrstuvw", "short": True}]
    assert look.measured_vertical(tmp_path, shorts) == (1.0, "listed")
    assert look.measured_vertical(tmp_path, shorts[:1] + [{"id": "x"}]) == (None, ""), "long videos say nothing"
    assert look.measured_vertical(tmp_path, []) == (None, "")
    _portrait(tmp_path / "frames")
    assert look.measured_vertical(tmp_path, []) == (1.0, "measured"), "frames always win over the listing"
    _portrait(tmp_path / "frames", size=(960, 540))
    assert look.measured_vertical(tmp_path, shorts) == (0.0, "measured")


def test_an_old_finished_look_gets_the_vertical_measure_when_it_is_reused(tmp_path):
    path = tmp_path / "look"
    _portrait(path / "frames")
    saved = {"state": "done", "profile": {"format": {"narration": {"value": "voiceover"}}}, "references": []}
    assert look.fill_vertical(path, saved)
    assert saved["profile"]["format"]["vertical"]["value"] == 1.0
    assert json.loads((path / "look.json").read_text())["profile"]["format"]["vertical"]["from"] == ["measured"]
    assert not look.fill_vertical(path, saved), "measured once"
    assert not look.fill_vertical(tmp_path / "x", {"state": "running", "profile": {}})


def test_a_reused_look_saved_before_the_measure_plans_a_shorts_channel_vertical(tmp_path, monkeypatch):
    refs = [{"id": "abcdefghijk", "title": "A Short", "short": True, "duration_seconds": 50.0, "eligible": True}]
    previews = clone._folder(tmp_path) / ".previews"
    previews.mkdir(parents=True)
    (previews / ("pv_" + "a" * 64 + ".json")).write_text(json.dumps({"candidates": refs, "created_at": time.time()}))
    first = look.start(tmp_path, "pv_" + "a" * 64, ["abcdefghijk"], spawn=lambda h, i: None)
    path = look.folder(tmp_path, first["look_id"])
    saved = json.loads((path / "look.json").read_text())
    saved.update(state="done", finished_at=time.time(), profile={"format": {}})           # saved by Studio 105
    (path / "look.json").write_text(json.dumps(saved))
    look.start(tmp_path, "pv_" + "a" * 64, ["abcdefghijk"], spawn=lambda h, i: pytest.fail("reused, not looked again"))
    profile = json.loads((path / "look.json").read_text())["profile"]
    import cr_style_map
    assert cr_style_map.vertical(profile), "no stills, but every reference is a Short"


# ── styles saved before Studio 111 ───────────────────────────────────────────────────────────────────────────────────

def _home(tmp_path, *, vertical=1.0, look_extra=None, style_extra=None, frames=False, refs=None):
    home = tmp_path / "studio"
    (home / "styles").mkdir(parents=True)
    folder = look.folder(home, LOOK)
    profile = {"format": {}} if vertical is None else {"format": {"vertical": {"value": vertical}}}
    clone._write(folder / "look.json", {"state": "done", "look_id": LOOK, "profile": profile,
                                        "references": refs or []})
    if frames:
        _portrait(folder / "frames")
    style = {"name": "cr_style_roblox_mac", "label": "Roblox Animation Storys Mac", "look": dict(look_extra or {}),
             "cr_style_clone": {"operation_id": OPERATION,
                                "learned_v2": {"look_id": LOOK, "base": "stories",
                                               "settings": [{"area": "Look", "label": "Picture style"}]}},
             **(style_extra or {})}
    path = home / "styles" / "cr_style_roblox_mac.json"
    clone._write(path, style)
    op = clone._operation(home, OPERATION)
    clone._write(op / "operation.json", {"state": "complete_local", "style_sha256": clone._file_hash(path),
                                         "spec": {"style_id": "cr_style_roblox_mac"},
                                         "finish_receipt": {"artifacts": [{"sha256": clone._file_hash(path)}]}})
    return home, path


def _style(path):
    return json.loads(Path(path).read_text())


def test_a_shorts_clone_saved_16_9_before_111_becomes_vertical_once_and_stays_its_studys(tmp_path):
    home, path = _home(tmp_path)
    assert clone.vertical_upgrade(home) == ["cr_style_roblox_mac"]
    style = _style(path)
    assert style["look"]["aspect"] == "9:16"
    assert any(r["label"] == "Format" for r in style["cr_style_clone"]["learned_v2"]["settings"]), "its study page says so"
    kept = list((home / "output" / "cr-style-edits" / "cr_style_roblox_mac").glob("*-before-9x16.json"))
    assert len(kept) == 1 and "aspect" not in json.loads(kept[0].read_text())["look"], "the old file is kept"
    saved = json.loads((clone._operation(home, OPERATION) / "operation.json").read_text())
    assert clone._published(home, saved), "the study still owns its style (its hash followed the change)"
    assert clone.vertical_upgrade(home) == [], "once"
    # what Create says and what the render uses
    import cr_video_cancel
    studio = SimpleNamespace(styles=SimpleNamespace(STYLES={"cr_style_roblox_mac": style}))
    assert cr_video_cancel.video_size(studio, "cr_style_roblox_mac") == {"VIDEO_W": "1080", "VIDEO_H": "1920"}


def test_a_look_saved_before_the_measure_is_read_from_its_own_frames_or_listing(tmp_path):
    home, path = _home(tmp_path / "a", vertical=None, frames=True)
    assert clone.vertical_upgrade(home) == ["cr_style_roblox_mac"]
    home, path = _home(tmp_path / "b", vertical=None, refs=[{"id": "abcdefghijk", "short": True}])
    assert clone.vertical_upgrade(home) == ["cr_style_roblox_mac"]
    home, path = _home(tmp_path / "c", vertical=None)
    assert clone.vertical_upgrade(home) == [] and "aspect" not in _style(path)["look"], "nothing says vertical"


@pytest.mark.parametrize("case", ["wide channel", "already 9:16", "a Shorts recipe", "coded, renderer draws wide"])
def test_styles_that_are_right_already_are_left_alone(tmp_path, case):
    import cr_animated2d
    plugin = next(iter(cr_animated2d.PLUGINS))
    home, path = _home(tmp_path, **{
        "wide channel": dict(vertical=0.0),
        "already 9:16": dict(look_extra={"aspect": "9:16"}),
        "a Shorts recipe": dict(style_extra={"family": "Shorts"}),
        "coded, renderer draws wide": dict(look_extra={"visuals": plugin,
                                                       "animated2d": {"width": 1920, "height": 1080}}),
    }[case])
    before = path.read_bytes()
    assert clone.vertical_upgrade(home) == []
    assert path.read_bytes() == before


def test_a_coded_style_whose_renderer_draws_tall_says_9_16_too(tmp_path):
    import cr_animated2d
    plugin = next(iter(cr_animated2d.PLUGINS))
    home, path = _home(tmp_path, look_extra={"visuals": plugin, "animated2d": {"width": 1080, "height": 1920}})
    assert clone.vertical_upgrade(home) == ["cr_style_roblox_mac"]
    assert _style(path)["look"]["aspect"] == "9:16"


def test_a_style_its_study_is_still_finishing_waits_for_the_next_start(tmp_path):
    """The study holds its lock while it checks the likeness and then records the style's hash from what it read: a
    change in between would leave the style no longer its study's."""
    home, path = _home(tmp_path)
    before = path.read_bytes()
    with clone._lock(clone._operation(home, OPERATION)):
        assert clone.vertical_upgrade(home) == []
    assert path.read_bytes() == before and not (home / "output" / "cr-style-edits").exists()
    assert clone.vertical_upgrade(home) == ["cr_style_roblox_mac"], "the next start"


def test_one_broken_style_never_stops_the_others(tmp_path, monkeypatch):
    home, path = _home(tmp_path)
    clone._write(home / "styles" / "cr_style_aaa.json", {
        "name": "cr_style_aaa", "look": {}, "cr_style_clone": {"learned_v2": {"look_id": "lk_" + "c" * 24}}})
    real = look.folder

    def folder(home_, look_id):
        if look_id != LOOK:
            raise RuntimeError("Keep reference looks in this studio's output folder.")
        return real(home_, look_id)
    monkeypatch.setattr(look, "folder", folder)
    assert clone.vertical_upgrade(home) == ["cr_style_roblox_mac"]


def test_a_look_measured_at_start_keeps_its_measure(tmp_path):
    home, path = _home(tmp_path, vertical=None, frames=True)
    assert clone.vertical_upgrade(home) == ["cr_style_roblox_mac"]
    saved = json.loads((look.folder(home, LOOK) / "look.json").read_text())
    assert saved["profile"]["format"]["vertical"]["value"] == 1.0, "the next start doesn't open every still again"


def test_studio_upgrades_old_shorts_clones_before_the_engine_reads_its_styles():
    source = (RUNTIME / "cr_studio.py").read_text()
    upgrade, engine = source.index("cr_style_clone.vertical_upgrade(HOME)"), source.index("import app as studio")
    assert upgrade < engine


# ── the pages ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_finished_videos_are_never_cropped_on_studios_pages():
    css = (RUNTIME / "cr_workspace.css").read_text()
    assert ".crw-project-card .crw-card-art>video.crw-video-preview{object-fit:contain" in css
    assert ".crw-output>video.is-tall{aspect-ratio:9/16" in css
    js = (RUNTIME / "cr_workspace.js").read_text()
    assert "classList.toggle('is-tall',v.videoHeight>v.videoWidth*1.1)" in js
    import cr_ui
    html = ("<html><head><style>.card video{width:100%;aspect-ratio:16/9;object-fit:cover}</style></head>"
            '<body><div id="voBar"></div><button id="go">Go</button></body></html>')
    out = cr_ui.transform_html(html)
    assert out.index(".card video{object-fit:contain}") > out.index("object-fit:cover"), \
        "the classic library card shows a vertical video whole (the theme comes after the page's own styles)"
