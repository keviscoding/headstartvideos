"""The 3D look chosen in Create applies to that video only: white studio, colour film or the recipe's own."""
import json
import sys
import types
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
