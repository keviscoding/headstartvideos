"""Roblox Story Shorts narrating the creator's own script (the private Shorts source in data/frontier/shorts, where this
checkout has it). A customer's door story (Discord, 2026-11-05): "studio completely ignored the script". make_roblox now
takes the words from CR_RBX_SCRIPT: the director is told they are the narration, its shots are re-cut on exactly those
words whatever it answers, the script sets the length, and another script plans again. Stand-ins only: no Claude, voice
or render runs."""
import ast
import json
import re
import types
from pathlib import Path

import pytest

ROBLOX_DIR = Path(__file__).resolve().parents[1] / "data" / "frontier" / "shorts" / "roblox"
ROBLOX = ROBLOX_DIR / "make_roblox.py"
pytestmark = pytest.mark.skipif(not ROBLOX.is_file(), reason="the private Shorts source is not in this checkout")

FUNCTIONS = {"own_script", "script_sig", "own_reset", "own_sig", "target_words", "own_block", "fit_to_recording", "norm",
             "director", "invented_rule"}
CONSTANTS = {"OWN_FILES", "OWN_MARK", "RULE_WORDS_SKIP"}
SCRIPT = ("In this town, every door gets smaller each time you open it. You see, a new door is as big as a barn. "
          "Mom climbs in through the window with the groceries. My little sister Pip opened the fridge once.")


def roblox(tmp_path, script=SCRIPT, answers=None):
    tree = ast.parse(ROBLOX.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in n.targets)]
    assert {n.name for n in body} == FUNCTIONS, "make_roblox's script functions moved: check this test"
    pasted = tmp_path / "own_script.txt"
    pasted.write_text(script + "\n", encoding="utf-8")
    prompts, logged = [], []

    def claude(prompt):
        prompts.append(prompt)
        return json.dumps(answers.pop(0))
    space = {"os": __import__("os"), "re": re, "json": json, "Path": Path, "OUT": tmp_path, "OWN_VOICE": "",
             "OWN_SCRIPT": str(pasted), "TOPIC": "Here's a world rule. Write a 60-second story.", "SECONDS": 55, "WPS": 4.35,
             "HERE": ROBLOX_DIR, "SETS": types.SimpleNamespace(ROOM_DOC={}, OUTDOOR={}), "language_block": lambda *a: "",
             "own_heard": lambda: {"text": "", "words": []}, "log": lambda *a: logged.append(a), "claude": claude,
             "LANG_CODE": "en"}
    exec(compile(ast.Module(body=consts + body, type_ignores=[]), str(ROBLOX), "exec"), space)
    return types.SimpleNamespace(**space), prompts, pasted


def test_the_director_is_told_the_words_and_its_own_story_cannot_get_through(tmp_path):
    invented = {"title": "Ray", "narration": "My name is Ray and our front door was dying.", "wear": {"kind": "none"},
                "shots": [{"text": "My name is Ray", "loc": "a"}, {"text": "and our front door was dying.", "loc": "b"}]}
    rb, prompts, _ = roblox(tmp_path, answers=[invented])
    plan = rb.director()
    assert "THE NARRATION IS ALREADY WRITTEN" in prompts[0] and prompts[0].rstrip().endswith(SCRIPT) or SCRIPT in prompts[0]
    assert plan["narration"] == SCRIPT
    assert " ".join(s["text"] for s in plan["shots"]) == SCRIPT, "the shots say exactly the creator's words"
    assert "Ray" not in plan["narration"]


def test_a_good_plan_keeps_its_cuts_on_the_exact_words(tmp_path):
    first, second = SCRIPT.split(" You see, ")
    good = {"title": "Doors", "narration": SCRIPT, "wear": {"kind": "none"},
            "shots": [{"text": first, "loc": "a"}, {"text": "You see, " + second, "loc": "b"}]}
    rb, _, _ = roblox(tmp_path, answers=[good])
    plan = rb.director()
    assert [s["text"] for s in plan["shots"]] == [first, "You see, " + second]


def test_the_script_sets_the_word_count_and_a_world_rule_it_states_is_the_creators(tmp_path):
    rb, _, _ = roblox(tmp_path)
    assert rb.target_words() == len(SCRIPT.split())
    worn = {"wear": {"kind": "sign", "what": "door size", "from_topic": "every door gets smaller"}}
    assert rb.invented_rule(worn) == ""


def test_another_script_is_planned_again_and_the_same_one_carries_on(tmp_path):
    rb, _, pasted = roblox(tmp_path)
    rb.own_reset()
    (tmp_path / "plan.json").write_text("{}")
    (tmp_path / "voice.json").write_text("{}")
    rb.own_reset()
    assert (tmp_path / "plan.json").exists(), "the same words carry on from the saved work"
    pasted.write_text("Different words entirely, and a different story from the last one.", encoding="utf-8")
    rb.own_reset()
    assert not (tmp_path / "plan.json").exists() and not (tmp_path / "voice.json").exists()


def test_a_short_planned_from_its_title_is_planned_again_when_a_script_is_added(tmp_path):
    rb, _, _ = roblox(tmp_path)
    (tmp_path / "plan.json").write_text("{}")
    rb.own_reset()
    assert not (tmp_path / "plan.json").exists()


def test_main_sets_the_length_from_the_script_before_the_director():
    main = ROBLOX.read_text(encoding="utf-8").split("def main():", 1)[1]
    assert main.index("own_reset()") < main.index("len(own_script().split()) / WPS") < main.index("director(h)")
