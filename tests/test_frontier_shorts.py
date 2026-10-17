import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cr_shorts", ROOT / "local_runtime" / "cr_shorts.py")
shorts = importlib.util.module_from_spec(spec)
sys.modules["cr_shorts"] = shorts
spec.loader.exec_module(shorts)


def test_a_shorts_recipe_is_marked_by_its_format():
    assert shorts.is_short({"format": "short", "shorts": {"pipeline": "zack3d"}})
    assert not shorts.is_short({"format": "long"})
    assert not shorts.is_short(None)


def test_the_length_travels_at_the_front_of_the_extra_instructions():
    assert shorts.split_extra("[[short_seconds=45]] Keep it eerie.") == (45, "Keep it eerie.")
    assert shorts.split_extra("No marker here") == (None, "No marker here")
    assert shorts.split_extra("") == (None, "")


def test_the_length_comes_from_the_page_then_the_recipe_and_stays_in_range():
    dark = {"format": "short", "shorts": {"pipeline": "zack3d", "seconds": 45}}
    assert shorts.seconds_of(dark, {}, "") == 45                          # the recipe's own length
    assert shorts.seconds_of(dark, {}, "[[short_seconds=25]] x") == 25    # the Create page's choice wins
    assert shorts.seconds_of(dark, {}, "[[short_seconds=900]]") == 60     # never past a Short
    assert shorts.seconds_of(dark, {}, "[[short_seconds=3]]") == 15
    assert shorts.seconds_of({"format": "short"}, {"short_seconds": "bad"}, "") == 25


def test_a_studio_without_the_pipelines_or_the_pack_says_what_is_missing(tmp_path):
    missing = shorts.ready(tmp_path)
    assert any("pipelines" in m for m in missing) and any("asset pack" in m for m in missing)
    for folder, script in shorts.PIPELINES.values():
        (tmp_path / "shorts" / folder).mkdir(parents=True, exist_ok=True)
        (tmp_path / "shorts" / folder / script).write_text("")
    index = tmp_path / "shorts" / "rbx" / "r3d" / "engine" / "skins" / "rbx" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text("{}")
    assert shorts.ready(tmp_path) == []
