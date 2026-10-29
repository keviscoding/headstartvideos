"""Vertical Shorts on every route: a Shorts channel cloned with Studio's ready-made pictures, footage or
3D engine instead of code once came out as a regular 16:9 video. Its style now asks for 9:16 like the code routes,
its pictures are drawn tall, captions sit above the Shorts buttons, and what the engine draws only for wide frames is
fitted whole across the middle or left out (cr_vertical)."""
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
sys.path.insert(0, str(ROOT / "tests"))
import cr_media  # noqa: E402
import cr_style_map as cm  # noqa: E402
import cr_vertical  # noqa: E402
import test_style_map as tsm  # noqa: E402

TALL = tsm.profile({"generated": 0.9, "graphic": 0.1}, format__vertical=tsm.axis(1.0))


# ── the plan ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_shorts_channel_made_without_code_is_vertical_too(monkeypatch):
    import cr_animated2d
    monkeypatch.setattr(cr_animated2d, "available", lambda home=None: True)      # code is offered, and declined
    other = cm.plan_style(TALL, tsm.STYLES, answers={"made": cm.MADE_OTHER})
    assert other["route"]["engine"] not in ("animated2d", "animated3d")
    assert other["overrides"]["look"]["aspect"] == "9:16", "Studio's ready-made pictures make vertical Shorts too"
    row = next(r for r in other["settings"] if r["path"] == "look.aspect")
    assert row["label"] == "Format" and "pictures drawn tall" in row["value"]
    assert other["router"] == cm.ROUTER >= 111, "looks saved by an older router are planned again"
    wide = cm.plan_style(tsm.profile({"generated": 0.9, "graphic": 0.1}), tsm.STYLES, answers={"made": cm.MADE_OTHER})
    assert "aspect" not in wide["overrides"].get("look", {}), "a channel of long videos stays 16:9"


def test_every_ready_made_route_of_a_shorts_channel_is_vertical(monkeypatch):
    import cr_animated2d
    monkeypatch.setattr(cr_animated2d, "available", lambda home=None: True)      # code is offered, and declined
    footage = tsm.profile({"footage": 0.7, "graphic": 0.3}, format__vertical=tsm.axis(1.0))
    for base in ("stories", "documentary", "jung", "3d", "nocturne", "avatar-premium"):
        plan = cm.plan_style(footage, tsm.STYLES, base=base, answers={"made": cm.MADE_OTHER})
        assert plan["overrides"]["look"]["aspect"] == "9:16", base
    no_code = cm.plan_style(TALL, tsm.STYLES, code=False)            # a study that wrote no renderer
    assert no_code["overrides"]["look"]["aspect"] == "9:16"


def test_the_made_question_says_both_ways_are_vertical_for_a_shorts_channel(monkeypatch):
    import cr_animated2d
    monkeypatch.setattr(cr_animated2d, "available", lambda home=None: True)      # this Studio has the 2D kit
    ask = next(q for q in cm.plan_style(TALL, tsm.STYLES)["questions"] if q["id"] == "made")["ask"]
    assert "Either way they're vertical Shorts" in ask
    wide = next(q for q in cm.plan_style(tsm.profile({"generated": 0.9}), tsm.STYLES)["questions"] if q["id"] == "made")
    assert "vertical" not in wide["ask"]


def test_the_saved_style_keeps_9_16_over_the_art_directors_words(tmp_path, monkeypatch):
    import cr_style_clone
    import cr_style_look
    home = tmp_path / "home"
    folder = home / "output" / "cr-style-clone" / ("op_" + "a" * 24)
    folder.mkdir(parents=True)
    look_dir = tmp_path / "look"
    look_dir.mkdir()
    (look_dir / "look.json").write_text(__import__("json").dumps({"state": "done", "profile": TALL}))
    (folder / "art-checkpoint.json").write_text(__import__("json").dumps(
        {"art": {"overrides": {"look": {"aspect": "16:9", "captions": {"size": 50}}}}}))
    monkeypatch.setattr(cr_style_look, "folder", lambda home, look_id: look_dir)
    monkeypatch.setattr(cr_style_clone, "_installed_styles", lambda home: tsm.STYLES)
    monkeypatch.setattr(cr_style_clone, "_studio_home", lambda folder: home)
    spec = {"look_id": "lk_x", "base_style": "stories", "answers": {"made": cm.MADE_OTHER}}
    learned = cr_style_clone._apply_learned(folder, spec, dict(tsm.STYLES["stories"]), lambda m: None)
    assert learned["look"]["aspect"] == "9:16" and learned["look"]["captions"]["size"] == 50
    assert learned["look"].get("visuals") not in ("cr_animated2d", "cr_animated3d")


# ── the pictures ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_pictures_that_fill_a_tall_frame_are_drawn_tall_and_thumbnails_stay_wide(monkeypatch):
    monkeypatch.setenv("VIDEO_W", "1080")
    monkeypatch.setenv("VIDEO_H", "1920")
    for label in ("ai image 3", "picture 1", "video-still 2", "plate street"):
        assert cr_media.aspect(label) == "9:16", label
    for label in ("thumbnail 1", "cut-out figure", "object (a lamp)", "newspaper Times", "presenter set-up 1", ""):
        assert cr_media.aspect(label) == "16:9", label
    choice = cr_media.image_model("google/nano-banana")
    assert cr_media.image_input(choice, "A street", [], "picture 4")[1]["aspect_ratio"] == "9:16"
    monkeypatch.delenv("VIDEO_W")
    monkeypatch.delenv("VIDEO_H")
    assert cr_media.aspect("picture 1") == "16:9", "every other video as before"


# ── the engine in a tall frame ─────────────────────────────────────────────────────────────────────────────────────

def test_captions_are_sized_and_placed_for_a_phone():
    cap = cr_vertical.phone_captions({"size": 48, "upper": True, "line_chars": 34, "margin_v": 96}, 1080, 1920,
                                     73, 44, 96)
    assert cap["size"] == 67 and cap["line_chars"] == 18 and cap["margin_v"] == 538
    assert 18 * 0.68 * 67 <= 1080 - 2 * cr_vertical.SIDE, "a full line fits between the sides"
    assert cr_vertical.phone_captions({}, 1080, 1920, 73, 44, 96)["size"] == 92, "the engine's default, capped"
    assert cr_vertical.phone_captions({"size": 110}, 1080, 1920, 73, 44, 96)["size"] == 110, "already big: kept"
    assert cr_vertical.phone_captions({"off": True}, 1080, 1920, 73, 44, 96) == {"off": True}


def _engine(width, height):
    calls = []
    engine = SimpleNamespace(VIDEO_W=width, VIDEO_H=height, STYLE_CAPTIONS={"s": {"size": 48, "line_chars": 34}},
                             log=lambda m: calls.append(("log", m)))

    def _build_burn_ass(srt, out_ass, mute_ranges=None, center=False, style="", captions=True):
        calls.append(("captions", dict(engine.STYLE_CAPTIONS.get(style) or {})))
        return out_ass

    def _depth_dlc(plan, style, job, candidates):
        calls.append(("depth",))
        return "photofx", {1, 2}
    engine._build_burn_ass = _build_burn_ass
    engine._depth_dlc = _depth_dlc
    engine._v2_look = lambda style: {"fx": {"frame": "full"}, "textfx": {"x": 1}, "accent": ["#fff"]}
    engine._overlay_ass_labels = lambda job, mute, style: (["Style: OvlName"], ["Dialogue: label"])
    engine._render_photo_segment = lambda image, dur, out, **kw: calls.append(("photo", Path(image).name))
    engine._render_video_segment = lambda src, dur, out, leak=False, white_fade=0.0, aged=False, framed="", idx=0, \
        drift=True, grade=True: calls.append(("video", Path(src).name, drift))
    engine._render_held_scene = lambda src, dur, out, src_dur, hold_start=0.0: calls.append(("held", Path(src).name))
    engine._render_cardtext_segment = lambda src, dur, out, line: calls.append(("card", line))
    return engine, calls


def test_a_wide_video_runs_exactly_as_before(monkeypatch):
    engine, calls = _engine(1920, 1080)
    monkeypatch.setattr(cr_vertical, "fit", lambda *a: pytest.fail("a wide video fits nothing"))
    cr_vertical.install(engine)
    engine._build_burn_ass("subs.srt", "out.ass", None, False, "s")
    assert calls[-1] == ("captions", {"size": 48, "line_chars": 34})
    assert engine._depth_dlc([], "s", None, []) == ("photofx", {1, 2})
    assert "fx" in engine._v2_look("s") and engine._overlay_ass_labels(None, [], "s")[1] == ["Dialogue: label"]
    engine._render_video_segment(Path("kit_00.mp4"), 4.0, Path("seg.mp4"), drift=False)
    engine._render_cardtext_segment(Path("p.jpg"), 3.0, Path("seg.mp4"), "BIG LINE")
    assert ("video", "kit_00.mp4", False) in calls and ("card", "BIG LINE") in calls


def test_in_a_tall_frame_wide_only_parts_are_fitted_or_left_out(monkeypatch):
    engine, calls = _engine(1080, 1920)
    fitted = []
    monkeypatch.setattr(cr_vertical, "fit", lambda src, w, h: fitted.append((Path(src).name, w, h))
                        or Path(src).with_name(Path(src).stem + ".tall.mp4"))
    cr_vertical.install(engine)
    cr_vertical.install(engine)                                         # installed once
    engine._build_burn_ass("subs.srt", "out.ass", None, False, "s")
    assert calls[-1] == ("captions", {"size": 67, "line_chars": 21, "margin_v": 538})
    assert engine.STYLE_CAPTIONS["s"] == {"size": 48, "line_chars": 34}, "the style's own captions are put back"
    engine._build_burn_ass("subs.srt", "out.ass", style="t")
    assert calls[-1][1]["size"] == 92 and "t" not in engine.STYLE_CAPTIONS, "a style without captions settings"
    engine._build_burn_ass("subs.srt", "out.ass", None, True, "s")
    assert calls[-1] == ("captions", {"size": 48, "line_chars": 34}), "centred captions keep their own place"
    assert engine._depth_dlc([], "s", None, []) == (None, set()) and ("depth",) not in calls, "never paid for"
    assert engine._v2_look("s") == {"accent": ["#fff"]}
    assert engine._overlay_ass_labels(None, [], "s") == ([], [])
    engine._render_video_segment(Path("kit_00.mp4"), 4.0, Path("seg.mp4"), drift=False)
    engine._render_video_segment(Path("clip.mp4"), 4.0, Path("seg.mp4"))   # footage keeps filling the frame
    engine._render_held_scene(Path("vox_00.mp4"), 6.0, Path("seg.mp4"), 4.0)
    assert fitted == [("kit_00.mp4", 1080, 1920), ("vox_00.mp4", 1080, 1920)]
    assert ("video", "kit_00.tall.mp4", False) in calls and ("video", "clip.mp4", True) in calls \
        and ("held", "vox_00.tall.mp4") in calls
    engine._render_cardtext_segment(Path("p.jpg"), 3.0, Path("seg.mp4"), "BIG LINE")
    assert ("photo", "p.jpg") in calls and not any(c[0] == "card" for c in calls), "a punch card shows its picture"
    said = [c[1] for c in calls if c[0] == "log"]
    assert len(said) == len(set(said)) and any("left out" in m for m in said), "each said once"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs FFmpeg")
def test_a_wide_scene_is_fitted_whole_over_a_blurred_copy(tmp_path):
    wide = tmp_path / "kit_00.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=1",
                    "-pix_fmt", "yuv420p", str(wide)], check=True)
    out = cr_vertical.fit(wide, 180, 320)
    assert out != wide and out.exists() and cr_vertical._size(out) == (180, 320)
    again = out.stat().st_mtime_ns
    assert cr_vertical.fit(wide, 180, 320) == out and out.stat().st_mtime_ns == again, "made once"
    assert cr_vertical.fit(out, 180, 320) == out, "a tall source is used as it is"
    assert cr_vertical.fit(tmp_path / "missing.mp4", 180, 320) == tmp_path / "missing.mp4"
