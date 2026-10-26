"""Style Lab: the creator's Claude Code works on a style's renderer like on any code project (start a workspace,
preview, check against the channel's frames, save), and the result plugs back into the style. The first 3D study of
@gneef saved 2D canvas code into a 3D style: every video would have failed. The lab's preview says so before anything
is saved, and a renderer is saved only as it was previewed."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
sys.path.insert(0, str(ROOT / "tests"))
import cr_animated2d as a2d  # noqa: E402
import cr_style_lab as lab  # noqa: E402
from test_animated3d import BOX, _chromium, home3d  # noqa: E402,F401 - the fixture


def _style(home, name="cr_clone_box", dims=3, renderer=None):
    spec = {"dims": dims, "width": 540, "height": 960, "captions": True, "sky": ["#88aadd", "#ccddee"],
            **({"renderer": renderer} if renderer else {})}
    style = {"name": name, "label": "Box world", "look": {"visuals": a2d.VISUALS3D if dims == 3 else a2d.VISUALS,
                                                          "animated2d": spec}, "cr_style_clone": {}}
    (home / "styles").mkdir(parents=True, exist_ok=True)
    (home / "styles" / f"{name}.json").write_text(json.dumps(style), encoding="utf-8")
    return style


def _kit_prompts(monkeypatch):
    monkeypatch.setattr(a2d, "available3d", lambda home=None: True)
    monkeypatch.setattr(a2d, "available", lambda home=None: True)
    monkeypatch.setattr(a2d, "_prompt3d", lambda name, home=None: f"[3d {name}]")
    monkeypatch.setattr(a2d, "_prompt", lambda name, home=None: f"[2d {name}]")


def test_a_lab_starts_from_the_style_as_saved_and_refuses_what_it_cant_work_on(tmp_path, monkeypatch):
    _kit_prompts(monkeypatch)
    (tmp_path / "assets/styles/learned/cr_clone_box").mkdir(parents=True)
    (tmp_path / "assets/styles/learned/cr_clone_box/renderer-v1.js").write_text(BOX, encoding="utf-8")
    _style(tmp_path, renderer={"file": "styles/learned/cr_clone_box/renderer-v1.js", "version": 1,
                               "sha256": hashlib.sha256(BOX.encode()).hexdigest()})
    got = lab.start(tmp_path, "cr_clone_box")
    folder = Path(got["workspace"])
    assert (folder / "renderer.js").read_text() == BOX, "the style's own renderer, unchanged"
    assert got["kit"] == "3D (three.js)" and got["size"] == "540x960" and got["saved_version"] == 1
    scene = json.loads((folder / "scene.json").read_text())
    assert scene["captions"] is True and scene["shots"] and scene["words"], "a starter scene when the study left none"
    assert "[3d AUTHORING.md]" in (folder / "GUIDE.md").read_text() and "style_lab" in (folder / "GUIDE.md").read_text()
    (folder / "renderer.js").write_text("// my edit")
    assert (Path(lab.start(tmp_path, "cr_clone_box")["renderer"])).read_text() == "// my edit", "work is kept"
    again = lab.start(tmp_path, "cr_clone_box", fresh=True)
    assert Path(again["renderer"]).read_text() == BOX and list((tmp_path / lab.LAB).glob("cr_clone_box.old-*"))
    for bad in ("../styles", "Nope", ""):
        with pytest.raises(lab.LabError):
            lab.start(tmp_path, bad)
    (tmp_path / "styles" / "pictures.json").write_text(json.dumps({"name": "pictures", "look": {}}))
    with pytest.raises(lab.LabError, match="draws pictures"):
        lab.start(tmp_path, "pictures")
    with pytest.raises(lab.LabError, match="Preview this renderer first"):
        lab.check(tmp_path, "cr_clone_box", judge=lambda r, c: pytest.fail("no judge before a preview"))
    with pytest.raises(lab.LabError, match="Preview this exact renderer"):
        lab.save(tmp_path, "cr_clone_box")


def test_the_lab_takes_the_studys_frames_without_their_black_bars(tmp_path):
    from PIL import Image
    boxed = Image.new("RGB", (480, 270), (0, 0, 0))
    boxed.paste((90, 160, 60), (164, 0, 316, 270))           # a 9:16 Short inside a 16:9 frame
    boxed.save(tmp_path / "boxed.jpg")
    lab._unbox(tmp_path / "boxed.jpg", tmp_path / "out.jpg")
    w, h = Image.open(tmp_path / "out.jpg").size
    assert abs(w - 152) <= 2 and h == 270
    night = Image.new("RGB", (480, 270), (4, 4, 6))
    night.save(tmp_path / "night.jpg")
    lab._unbox(tmp_path / "night.jpg", tmp_path / "night_out.jpg")
    assert Image.open(tmp_path / "night_out.jpg").size == (480, 270), "a dark frame is not cropped to nothing"


def test_preview_check_and_save_plug_the_renderer_back_into_the_style(home3d, monkeypatch):
    monkeypatch.setattr(a2d, "available3d", lambda home=None: True)
    _style(home3d)
    got = lab.start(home3d, "cr_clone_box")
    folder = Path(got["workspace"])
    (folder / "renderer.js").write_text(BOX, encoding="utf-8")
    (folder / "scene.json").write_text(json.dumps({"duration": 3, "cast": {"b": {"name": "B"}}, "sets": {"s": {}},
                                                   "shots": [{"start": 0, "set": "s"}], "stills": [0.5, 2.0],
                                                   "actions": [{"at": 0, "who": "b", "move": "place"}],
                                                   "words": [{"w": "hello", "t": 0.2}]}))
    shown = _chromium(lambda: lab.preview(home3d, "cr_clone_box", seconds=1))
    assert shown["ok"], shown["problems"]
    assert shown["pure"] == 0.0 and shown["frame_ms"] < lab.FRAME_MS and len(shown["stills"]) == 2
    assert Path(shown["video"]).is_file() and ["pop"] == [s[1] for s in shown["sounds"]]
    (folder / "refs").mkdir(exist_ok=True)
    for name in ("shown_0", "shown_1", "held_0"):                 # stand-ins for the channel's frames
        (folder / "refs" / f"{name}.jpg").write_bytes(Path(shown["stills"][0]).read_bytes())
    judged = []

    def judge(references, candidates):
        judged.append(len(candidates))
        if len(candidates) == 1:                            # the channel's own held-out frame, once
            return {"candidates": [{"overall": 7.5}]}
        return {"candidates": [{"overall": 6.5, "characters": 6}] * len(candidates), "dealbreakers": ["boxes"],
                "fixes": ["round it"]}
    verdict = lab.check(home3d, "cr_clone_box", judge=judge)
    assert verdict["bar"] == 6.0 and verdict["overall"] == 6.5 and verdict["passed"] and verdict["dealbreakers"] == ["boxes"]
    assert lab.check(home3d, "cr_clone_box", judge=judge)["bar"] == 6.0 and judged == [1, 2, 2], "the bar is set once"
    saved = _chromium(lambda: lab.save(home3d, "cr_clone_box"))
    style = json.loads((home3d / "styles/cr_clone_box.json").read_text())
    record = style["look"]["animated2d"]["renderer"]
    assert saved["version"] == 1 and record["state"] == "passed" and record["dims"] == 3 and record["from"] == "style_lab"
    assert record["sha256"] == hashlib.sha256(BOX.encode()).hexdigest() and record["manifest"]["name"] == "box-world"
    assert a2d.renderer_for(style, home3d)[0] == BOX, "videos draw exactly what was previewed and saved"
    assert _chromium(lambda: lab.save(home3d, "cr_clone_box"))["version"] == 1, "the same code is the same version"
    (folder / "renderer.js").write_text(BOX + "\n// changed")
    with pytest.raises(lab.LabError, match="Preview this exact renderer"):
        lab.save(home3d, "cr_clone_box")


def test_a_renderer_that_keeps_state_between_frames_or_draws_2d_code_is_refused(home3d, monkeypatch):
    monkeypatch.setattr(a2d, "available3d", lambda home=None: True)
    _style(home3d)
    folder = Path(lab.start(home3d, "cr_clone_box")["workspace"])
    (folder / "scene.json").write_text(json.dumps({"duration": 4, "cast": {}, "sets": {"s": {}},
                                                   "shots": [{"start": 0, "set": "s"}], "stills": [1.0]}))
    counting = BOX.replace("s.box.rotation.y = t;", "s.n = (s.n || 0) + 1; s.box.rotation.y = s.n * 0.7;")
    (folder / "renderer.js").write_text(counting)
    shown = _chromium(lambda: lab.preview(home3d, "cr_clone_box", seconds=1))
    assert not shown["ok"] and any("pure function" in p for p in shown["problems"]), shown["problems"]
    canvas = ("Kit.renderer({name: 'flat', manifest: {actions: {place: '{}'}}, frame(ctx, t) { ctx.fillStyle = '#fff'; "
              "ctx.fillRect(0, 0, 10, 10); }});")
    (folder / "renderer.js").write_text(canvas)
    shown = _chromium(lambda: lab.preview(home3d, "cr_clone_box", seconds=1))
    assert not shown["ok"] and any("fillRect" in p for p in shown["problems"]), "the first gneef study's mistake"
    assert json.loads((folder / "lab.json").read_text())["history"][-1]["ok"] is False


def test_words_too_small_to_read_at_video_size_are_a_problem_claude_fixes(home3d, monkeypatch):
    """A customer's cloned style: "nearly every overlay is too small to read at video size". Words a renderer writes on
    screen are measured where they are drawn, and too small ones are a problem to fix before saving."""
    monkeypatch.setattr(a2d, "available3d", lambda home=None: True)
    _style(home3d)
    folder = Path(lab.start(home3d, "cr_clone_box")["workspace"])
    (folder / "scene.json").write_text(json.dumps({"duration": 3, "cast": {}, "sets": {"s": {}}, "captions": True,
                                                   "shots": [{"start": 0, "set": "s"}], "stills": [1.0],
                                                   "words": [{"w": "hello", "t": 0.2}]}))
    tiny = BOX.replace("Kit.caption(view.overlay, Kit.wordsAt(t, 1));",
                       "Kit.caption(view.overlay, Kit.wordsAt(t, 1)); view.overlay.font = '9px Arial'; "
                       "view.overlay.fillText('source: NASA', 20, 40);")
    (folder / "renderer.js").write_text(tiny)
    shown = _chromium(lambda: lab.preview(home3d, "cr_clone_box", seconds=1))
    assert not shown["ok"] and shown["smallest_text"]["text"] == "source: NASA"
    assert any("too small to read" in p and "at least 14 px" in p for p in shown["problems"]), shown["problems"]
    (folder / "renderer.js").write_text(BOX)
    shown = _chromium(lambda: lab.preview(home3d, "cr_clone_box", seconds=1))
    assert shown["ok"] and shown["smallest_text"]["px"] >= 14, "the captions read"
    assert a2d.small_text({"px": 30, "text": "x"}, {"width": 1080, "height": 1920}) == ""
    assert "at least 28 px" in a2d.small_text({"px": 20, "text": "x", "t": 1}, {"width": 1080, "height": 1920})


def test_a_renderer_composited_over_pictures_is_judged_on_its_characters_or_over_real_backdrops(home3d, monkeypatch):
    """Lucas the Spider composites CG characters over garden photography: every draft of its study lost points for a
    background that, in the videos, is a picture drawn for each place. The judge now sets a stand-in background aside,
    and Style Lab previews over the workspace's own pictures (plates/<set id>.jpg) when it has them."""
    from PIL import Image
    assert a2d.uses_plates("plates: { draw: true, prompt: 'macro garden photography' },")
    assert not a2d.uses_plates("plates: { draw: false },") and not a2d.uses_plates("// no plates")
    monkeypatch.setattr(a2d, "available3d", lambda home=None: True)
    _style(home3d)
    folder = Path(lab.start(home3d, "cr_clone_box")["workspace"])
    (folder / "scene.json").write_text(json.dumps({"duration": 3, "cast": {}, "sets": {"s": {"prompt": "a patio"}},
                                                   "shots": [{"start": 0, "set": "s"}], "stills": [1.0]}))
    composited = BOX.replace("plates: { draw: false },", "plates: { draw: true, prompt: 'garden photography' },") \
        .replace("view.gl.setClearColor(0x88aadd);", "if (Kit.plates.s) s.world.background = Kit.plates.s; "
                                                       "view.gl.setClearColor(0x88aadd);")
    (folder / "renderer.js").write_text(composited)
    (folder / "refs").mkdir(exist_ok=True)
    for name in ("shown_0", "shown_1"):
        Image.new("RGB", (54, 96), (60, 140, 60)).save(folder / "refs" / f"{name}.jpg")
    asked = []
    judge = lambda refs, cands, **kw: asked.append(kw) or {"candidates": [{"overall": 6}]}  # noqa: E731
    shown = _chromium(lambda: lab.preview(home3d, "cr_clone_box", seconds=1))
    assert shown["ok"] and shown["plates"] == []
    lab.check(home3d, "cr_clone_box", judge=judge)
    assert asked[-1] == {"plates": True}, "a stand-in background is set aside"
    (folder / "plates").mkdir()
    Image.new("RGB", (540, 960), (200, 60, 40)).save(folder / "plates" / "s.jpg")
    shown = _chromium(lambda: lab.preview(home3d, "cr_clone_box", seconds=1))
    assert shown["ok"] and shown["plates"] == ["s"]
    from PIL import ImageStat
    r, g, b = ImageStat.Stat(Image.open(shown["stills"][0]).convert("RGB")).mean
    assert r > g and r > b, "the test shot is composited over the workspace's picture"
    lab.check(home3d, "cr_clone_box", judge=judge)
    assert asked[-1] == {}, "over a real backdrop the background is judged too"
