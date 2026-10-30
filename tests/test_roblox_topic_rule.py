"""Roblox Story Shorts wear a world rule (a chest timer, a sign above the head...) only when the topic states one. Twin
(Discord, 2026-10-30): "a countdown timer or a sign above them, even when I never asked for one and the title has nothing
to do with a countdown". The director quotes the topic's words for its rule; a rule the topic never states is asked for
again once, and never worn. Stand-ins only: no Claude call, no voice, no render."""
import ast
import json
import re
import types
from pathlib import Path

import pytest

ROBLOX_DIR = Path(__file__).resolve().parents[1] / "data" / "frontier" / "shorts" / "roblox"
ROBLOX = ROBLOX_DIR / "make_roblox.py"
pytestmark = pytest.mark.skipif(not ROBLOX.is_file(), reason="the private Shorts source is not in this checkout")

FUNCTIONS = {"invented_rule", "director", "norm"}
CONSTANTS = {"RULE_WORDS_SKIP"}


def roblox(tmp_path, topic, answers, notes="", own="", heard_text=""):
    """make_roblox's director with a stand-in Claude that answers each call with the next plan in answers"""
    tree = ast.parse(ROBLOX.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in n.targets)]
    assert {n.name for n in body} == FUNCTIONS, "make_roblox's director functions moved: check this test"
    prompts = []

    def claude(prompt):
        prompts.append(prompt)
        return json.dumps(answers.pop(0))
    env = {"CR_SHORTS_EXTRA": notes} if notes else {}
    space = {"os": types.SimpleNamespace(environ=env), "re": re, "json": json, "OUT": tmp_path, "TOPIC": topic,
             "SECONDS": 55, "HERE": ROBLOX_DIR, "OWN_VOICE": own, "SETS": types.SimpleNamespace(ROOM_DOC={}, OUTDOOR={}),
             "target_words": lambda: 240, "own_block": lambda: "", "language_block": lambda: "",
             "own_heard": lambda: {"text": heard_text, "words": []}, "fit_to_recording": lambda plan, words: plan,
             "log": lambda *a: None, "claude": claude}
    exec(compile(ast.Module(body=consts + body, type_ignores=[]), str(ROBLOX), "exec"), space)
    return types.SimpleNamespace(**space), prompts


def plan(wear):
    return {"title": "t", "wear": wear, "narration": "One shot.", "shots": [{"text": "One shot."}], "locations": []}


TIMER = {"kind": "screen", "slot": "chest", "format": "time", "what": "time left"}


def test_an_ordinary_story_never_gets_a_made_up_timer(tmp_path):
    rb, prompts = roblox(tmp_path, "My little brother stole my bike and I had to get it back",
                         [plan(dict(TIMER, from_topic="everyone has a timer")), plan({"kind": "none"})])
    out = rb.director()
    assert out["wear"] == {"kind": "none"}
    assert len(prompts) == 2 and "states no such world rule" in prompts[1]


def test_still_made_up_after_asking_again_means_nothing_is_worn(tmp_path):
    rb, prompts = roblox(tmp_path, "My best friend moved away",
                         [plan(dict(TIMER)), plan({"kind": "billboard", "what": "a sign", "from_topic": "a sign above"})])
    assert rb.director()["wear"] == {"kind": "none"}
    assert len(prompts) == 2


def test_the_topics_own_rule_is_kept_on_the_first_answer(tmp_path):
    wear = dict(TIMER, kind="screen", format="comma", from_topic="everyone only has 1,000,000 steps")
    rb, prompts = roblox(tmp_path, "In this town, everyone only has 1,000,000 steps in their whole life", [plan(wear)])
    assert rb.director()["wear"] == wear
    assert len(prompts) == 1


def test_the_rule_may_come_from_the_creators_notes_or_recording(tmp_path):
    rb, _ = roblox(tmp_path, "My grandma's secret", [plan(dict(TIMER, from_topic="everyone sees a timer over their head"))],
                   notes="In this world everyone sees a timer over their head counting to their soulmate.")
    assert rb.director()["wear"]["kind"] == "screen"
    (tmp_path / "own").mkdir()
    rb, _ = roblox(tmp_path / "own", "Untitled", [plan({"kind": "flame", "from_topic": "flame above their head"})],
                   own="voice.m4a", heard_text="In this world, everyone has a flame above their head. Mine went out.")
    assert rb.director()["wear"]["kind"] == "flame"


def test_a_hindi_topics_rule_is_matched_in_its_own_words(tmp_path):
    rb, prompts = roblox(tmp_path, "इस दुनिया में हर किसी के सिर पर एक टाइमर है",
                         [plan(dict(TIMER, from_topic="हर किसी के सिर पर एक टाइमर"))])
    assert rb.director()["wear"]["kind"] == "screen" and len(prompts) == 1


def test_the_director_prompt_says_the_topic_decides(tmp_path):
    text = (ROBLOX_DIR / "director_roblox.md").read_text(encoding="utf-8")
    assert "THE TOPIC DECIDES THE RULE" in text and '"from_topic"' in text
