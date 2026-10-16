"""The 3D look chosen in Create applies to that video only: white studio, colour film or the recipe's own."""
import json
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_threed_look as look  # noqa: E402


@pytest.fixture
def threed(monkeypatch):
    mod = types.ModuleType("threed")
    mod.SB = types.SimpleNamespace(PRESETS=["documentary", "white_studio", "noir"])
    mod._cfg = lambda engine, style: {"preset": "white_studio", "share": 0.85}
    seen = []

    def _add_to_timeline(segs, srt, job, style, force, engine, total):
        seen.append(mod._cfg(engine, style)["preset"])
        return segs
    mod._add_to_timeline = _add_to_timeline
    monkeypatch.setitem(sys.modules, "threed", mod)
    look.install()
    return mod, seen


def job(tmp_path, name, choice):
    j = tmp_path / name
    j.mkdir()
    (j / "run_spec.json").write_text(json.dumps({"cr_threed_look": choice} if choice is not None else {}))
    return j


def test_a_video_gets_the_3d_look_its_creator_chose(tmp_path, threed):
    mod, seen = threed
    colour, own = job(tmp_path, "colour", "documentary"), job(tmp_path, "own", None)
    for j in (colour, own):
        (j / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nOne\n")
    # the engine hands the subtitle file before the job folder
    mod._add_to_timeline([], colour / "subs.srt", colour, "cr-3d", False, None, 60.0)
    mod._add_to_timeline([], own / "subs.srt", own, "cr-3d", False, None, 60.0)
    assert seen == ["documentary", "white_studio"], "the recipe's own look when nothing was chosen"
    assert mod._cfg(None, "cr-3d")["preset"] == "white_studio", "outside a video the recipe's look stands"


def test_only_known_looks_are_accepted():
    assert look.clean("") == "" and look.clean("white_studio") == "white_studio"
    with pytest.raises(ValueError, match="white studio or colour"):
        look.clean("neon")


def test_studio_colour_paints_the_clean_studio_models_in_the_creators_palette():
    assert look.clean("studio_color") == "studio_color"
    runtime = Path(__file__).parents[1] / "local_runtime"
    page = (runtime / "cr_workspace.html").read_text()
    assert 'value="studio:warm ivory white surfaces, signal yellow accents, deep charcoal shadows"' in page
    assert ">Colour film · Night blue and signal orange<" in page, "night palettes stay colour film"
    script = (runtime / "cr_workspace.js").read_text()
    assert "v.startsWith('studio:') ? 'studio_color' : 'documentary'" in script
    assert "palette.startsWith('studio:') ? palette.slice(7) : palette" in script, "the director gets the colours alone"




def test_a_studio_colour_recipe_paints_in_its_own_palette_unless_the_creator_chose_one(tmp_path, monkeypatch):
    mod = types.ModuleType("threed")
    mod.SB = types.SimpleNamespace(PRESETS=["documentary", "white_studio", "studio_color"])
    mod._cfg = lambda engine, style: {"preset": "studio_color"}
    extra = {"text": ""}
    engine = types.SimpleNamespace(
        STYLE_INFO={"cr-3d": {"look": {"threed": {"preset": "studio_color", "palette": "bone white, deep crimson"}}},
                    "cr-plain3d": {"look": {"threed": {"preset": "studio_color"}}},
                    "plain": {"look": {}}},
        _extra_block=lambda: extra["text"])
    seen = []

    def _add_to_timeline(segs, srt, job, style, force, eng, total):
        # the director plans each section on a worker thread
        with ThreadPoolExecutor(max_workers=1) as ex:
            seen.append(ex.submit(engine._extra_block).result())
    mod._add_to_timeline = _add_to_timeline
    monkeypatch.setitem(sys.modules, "threed", mod)
    look.install(engine)

    def run(name, style, choice=None, text=""):
        j = job(tmp_path, name, choice)
        (j / "style.txt").write_text(style + "\n")
        extra["text"] = text
        mod._add_to_timeline([], None, j, style, False, None, 60.0)
        return seen[-1]

    assert "3D SCENE PALETTE: every 3D scene uses only bone white, deep crimson;" in run("own", "cr-3d")
    assert "uses only warm ivory white surfaces, signal yellow accents" in run("default", "cr-plain3d")
    mine = "\n\nEXTRA\n3D SCENE PALETTE: every 3D scene uses only sand, teal; keep it minimal and contrasting.\n"
    assert run("mine", "cr-3d", text=mine) == mine, "the creator's palette from Create wins"
    assert "3D SCENE PALETTE" not in run("white", "cr-3d", choice="white_studio"), "white studio stays white"
    assert "3D SCENE PALETTE" not in run("plain", "plain"), "other looks keep their own colours"
    extra["text"] = ""
    assert engine._extra_block() == "", "outside a video nothing is added"
    look.install(engine)
    assert run("again", "cr-3d").count("3D SCENE PALETTE") == 1, "installing twice adds the line once"
