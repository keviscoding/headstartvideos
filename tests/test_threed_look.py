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


def test_a_designed_video_carries_the_channels_cast_into_every_scene(tmp_path, monkeypatch):
    """Studio 79: a recipe in the designed look (look.threed.designed) stamps every video.json the engine renders, and
    the director's creative direction names the channel's cast; any other look is untouched."""
    mod = types.ModuleType("threed")
    mod.SB = types.SimpleNamespace(PRESETS=["documentary", "designed"], look_id=lambda p: p if p in ("designed",) else "")
    mod._cfg = lambda engine, style: {"preset": "designed"}
    mod._video = lambda board, sub=None, preset=None: {"cast": board.get("cast", []), "scenes": [
        {"id": "s1", "look": {"preset": preset}, "world": {"biome": "temperate"}}]}
    rendered = []

    def _add_to_timeline(segs, srt, job, style, force, engine, total):
        rendered.append(mod._video({"cast": [{"id": "you"}]}, preset="designed"))
        rendered.append(mod._video({"cast": [{"id": "you"}]}, preset="documentary"))
        return segs
    mod._add_to_timeline = _add_to_timeline
    monkeypatch.setitem(sys.modules, "threed", mod)
    spec = {"plan": "orb", "cast": [{"name": "Dot", "role": "lead", "top": "hoodie", "color": "#3E8C4A"}]}
    engine = types.SimpleNamespace(STYLE_INFO={"cr_clone_if": {"look": {"threed": {"preset": "designed", "designed": spec}}}},
                                   _extra_block=lambda: "")
    look.install(engine)
    j = job(tmp_path, "if", None)
    (j / "style.txt").write_text("cr_clone_if")
    mod._add_to_timeline([], j / "subs.srt", j, "cr_clone_if", False, engine, 60.0)
    designed, plain = rendered
    assert designed["scenes"][0]["look"]["designed"]["plan"] == "orb"
    assert designed["scenes"][0]["world"]["biome"] == "designed_park"
    assert designed["cast"][0]["designed"] == {"top": "hoodie", "color": "#3E8C4A"}
    assert "designed" not in plain["scenes"][0]["look"] and plain["scenes"][0]["world"]["biome"] == "temperate"
    look._current.job = j
    try:
        assert "THE CHANNEL'S DESIGNED CAST" in engine._extra_block()
    finally:
        look._current.job = None


def test_a_short_video_gets_two_3d_review_rounds_unless_its_recipe_set_its_own(tmp_path, monkeypatch):
    mod = types.ModuleType("threed")
    mod.SB = types.SimpleNamespace(PRESETS=["documentary", "white_studio"])
    mod._cfg = lambda engine, style: {"preset": "white_studio", "review_rounds": 3}
    rounds = []

    def _add_to_timeline(segs, srt, job, style, force, engine, total):
        rounds.append(mod._cfg(engine, style)["review_rounds"])
        return segs
    mod._add_to_timeline = _add_to_timeline
    monkeypatch.setitem(sys.modules, "threed", mod)
    look.install()
    engine = types.SimpleNamespace(STYLE_INFO={"own-rounds": {"look": {"threed": {"review_rounds": 3}}}})
    for name, mins, style in (("short", 2, "cr-3d"), ("long", 8, "cr-3d"), ("unknown", None, "cr-3d"),
                              ("short-own", 2, "own-rounds")):
        j = tmp_path / name
        j.mkdir()
        (j / "run_spec.json").write_text(json.dumps({"minutes": mins} if mins is not None else {}))
        mod._add_to_timeline([], j, j, style, False, engine, 60.0)
    assert rounds == [2, 3, 3, 3], "2 minutes: two rounds; 8 minutes, an unknown length or a recipe's own: as set"



def test_quicker_3d_review_chosen_in_create_means_one_round_even_over_the_recipes_own(tmp_path, monkeypatch):
    mod = types.ModuleType("threed")
    mod.SB = types.SimpleNamespace(PRESETS=["documentary", "white_studio"])
    mod._cfg = lambda engine, style: {"preset": "white_studio", "review_rounds": 3}
    rounds = []

    def _add_to_timeline(segs, srt, job, style, force, engine, total):
        rounds.append(mod._cfg(engine, style)["review_rounds"])
        return segs
    mod._add_to_timeline = _add_to_timeline
    monkeypatch.setitem(sys.modules, "threed", mod)
    look.install()
    engine = types.SimpleNamespace(STYLE_INFO={"own-rounds": {"look": {"threed": {"review_rounds": 3}}}})
    for name, spec, style in (("quick-long", {"minutes": 8, "cr_threed_quick": True}, "cr-3d"),
                              ("quick-own", {"minutes": 8, "cr_threed_quick": True}, "own-rounds"),
                              ("not-quick", {"minutes": 8, "cr_threed_quick": "yes"}, "cr-3d")):
        j = tmp_path / name
        j.mkdir()
        (j / "run_spec.json").write_text(json.dumps(spec))
        mod._add_to_timeline([], j, j, style, False, engine, 60.0)
    assert rounds == [1, 1, 3], "only a real yes counts"


def test_claude_codes_make_videos_can_ask_for_quicker_3d_review():
    """Both ways: the Studio page's Quicker 3D review switch and Claude Code's make_videos send the same choice."""
    import cr_studio_tools as tools
    row = {"name": "cr-3d", "label": "3D", "features": {"offered": ["threed"], "on": ["threed"]}}
    quick = tools.video_payload(row, ["A title"], 2, {"threed": True}, {"quicker_3d_review": True})
    assert quick["threed_quick"] is True
    assert tools.video_payload(row, ["A title"], 2, {"threed": True}, {})["threed_quick"] is False
    assert tools.video_payload(row, ["A title"], 2, {"threed": False}, {"quicker_3d_review": True})["threed_quick"] is False
