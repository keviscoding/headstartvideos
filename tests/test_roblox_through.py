"""Roblox Story Shorts: people go through doors and windows, and never stand in a house (the private Shorts source in
data/frontier/shorts, where this checkout has it). A customer's door story (Discord, 2026-11-05): "clipping issues ... and
characters just standing in the door". Stand-ins only: no Claude, voice or render runs."""
import ast
import math
import re
import types
from pathlib import Path

import pytest

ROBLOX = Path(__file__).resolve().parents[1] / "data" / "frontier" / "shorts" / "roblox" / "make_roblox.py"
pytestmark = pytest.mark.skipif(not ROBLOX.is_file(), reason="the private Shorts source is not in this checkout")

FUNCTIONS = {"through_of", "through_stage", "keep_out_of_houses", "stage_through", "outside_buildings"}
CONSTANTS = {"WALK_THROUGH", "WINDOW_X", "WINDOW_SILL", "THROUGH_WORDS", "BUILDING_MIN", "BUILDING_TALL", "ON_ITS_FRONT"}
HOME = {"kind": "landmark.house", "at": [0.0, -14.16], "heading": 180, "style": "suburban"}
FRONT = -14.16 + 4.2


@pytest.fixture
def rb():
    tree = ast.parse(ROBLOX.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    consts = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in n.targets)]
    assert {n.name for n in body} == FUNCTIONS, "make_roblox's through functions moved: check this test"
    logged = []
    space = {"re": re, "math": math, "SR": types.SimpleNamespace(HOUSE_D=8.4, HOUSE_W=12.6), "log": lambda *a: logged.append(a)}
    exec(compile(ast.Module(body=consts + body, type_ignores=[]), str(ROBLOX), "exec"), space)
    return types.SimpleNamespace(**space, logged=logged)


@pytest.mark.parametrize("words, want", [
    ("Mom climbs in through the window with the groceries.", ("in", "window")),
    ("I walked out the door.", ("out", "door")),
    ("Grandpa shuffled out of his room through the door.", ("out", "door")),
    ("I climbed in.", None),
    ("My sister opened the door.", None),
    ("The mailman slides each letter through a crack.", None),
])
def test_the_words_say_who_goes_through_what(rb, words, want):
    assert rb.through_of({}, words) == want


def test_the_stagers_choice_comes_first(rb):
    assert rb.through_of({"through": {"way": "out", "what": "window"}}, "I walked in the door") == ("out", "window")
    assert rb.through_of({"through": "in"}) == ("in", "door")
    assert rb.through_of({"through": {"way": "sideways"}}) is None


def test_walking_in_crosses_the_real_door_which_swings_open(rb):
    got = rb.through_stage("in", "door", 3.0, HOME, 0.6)
    assert got["at"][0] == 0.0 and got["at"][1] > FRONT and got["heading"] == 0
    assert got["at"][1] - got["distance"] < FRONT, "they end up past the door, in the hall"
    assert got["door"][0] == [0, 0] and got["door"][-1][1] == 90
    assert got["camera"]["side"] == "back" and got["camera"]["follow"] is False


def test_walking_out_starts_in_the_hall_and_faces_the_street(rb):
    got = rb.through_stage("out", "door", 3.0, HOME, 0.6)
    assert got["at"][1] < FRONT and got["heading"] == 180 and got["at"][1] + got["distance"] > FRONT
    assert got["camera"]["side"] == "front"


def test_a_window_is_crossed_at_sill_height_at_the_real_window(rb):
    got = rb.through_stage("in", "window", 3.0, HOME, 0.6)
    assert got["at"][0] == -3.4 and got["door"] is None
    assert got["actions"][-1]["action"] == "crawl" and got["actions"][-1]["height"] >= 0.8
    assert got["at"][1] - got["distance"] < FRONT


def test_nobody_stands_in_a_house_or_walks_into_one(rb):
    stuck = {"ref": "me", "at": [3.0, -11.0], "heading": 180}
    assert rb.keep_out_of_houses(stuck, [HOME], 0.6) and stuck["at"][1] > FRONT + 0.6
    walker = {"ref": "me", "at": [2.0, -7.0], "heading": 0, "distance": 8.0}
    assert rb.keep_out_of_houses(walker, [HOME], 0.6)
    assert walker["at"][1] - walker["distance"] >= FRONT + 0.5
    clear = {"ref": "me", "at": [2.0, -7.0], "heading": 90, "distance": 2.0}
    assert rb.keep_out_of_houses(clear, [HOME], 0.6) == "" and clear["distance"] == 2.0


def _shot(rb, text, through=None, others=(), objs=()):
    cast = [{"id": "me", **({"through": through} if through else {})}]
    items = [{"ref": "me", "at": [0.5, -6.0], "heading": 180, "actions": [{"t": 0, "action": "idle"}], "r6": {"body": "kid"}}]
    for ref, at in others:
        cast.append({"id": ref})
        items.append({"ref": ref, "at": at, "heading": 180, "actions": [], "r6": {}})
    allobjs = [dict(HOME)] + list(objs)
    cam = rb.stage_through({"text": text, "cast": cast}, {"street": "suburb"}, items, allobjs, {"target": "me", "size": "cu", "side": "front", "with": "x"}, 3.0, "s01")
    return items, allobjs, cam


def test_a_shot_of_someone_walking_out_opens_the_door_and_clears_the_way(rb):
    prop = {"kind": "stage.prop", "id": "big_door", "mount": "wall", "at": [0.0, FRONT - 0.07]}
    far_prop = {"kind": "stage.prop", "id": "lamp", "mount": "floor", "at": [9.0, -6.0]}
    bush = {"kind": "flora.bush", "at": [1.0, -7.0]}
    items, objs, cam = _shot(rb, "I walked out the door.", others=[("mom", [0.2, -9.0])], objs=[prop, far_prop, bush])
    me, mom = items
    assert me["heading"] == 180 and me["at"][1] < FRONT and me["distance"] > 1
    assert next(o for o in objs if o["kind"] == "landmark.house")["door"][-1][1] == 90
    assert prop not in objs and bush not in objs and far_prop in objs, "the door prop and the bush go, for this shot only"
    assert abs(mom["at"][0] - me["at"][0]) >= 1.2, "nobody waits in the doorway"
    assert cam["size"] == "wide" and cam["follow"] is False and "with" not in cam and cam["target"] == "me"


def test_a_shot_with_no_crossing_keeps_its_camera_and_a_room_is_left_alone(rb):
    items, objs, cam = _shot(rb, "I looked at the door.")
    assert cam["size"] == "cu" and "door" not in objs[0]
    item = {"ref": "me", "at": [0.0, -11.0], "heading": 0, "actions": [], "r6": {}}
    same = rb.stage_through({"text": "I walked in the door.", "cast": [{"id": "me"}]}, {"room": "kitchen"}, [item], [], {"target": "me"}, 3.0, "s02")
    assert same == {"target": "me"} and item["at"] == [0.0, -11.0]


def test_a_custom_buildings_door_goes_on_its_front_and_nobody_stands_inside_it(rb):
    """The proof of that same story: Grandpa, and his door prop, at the centre of his 6 m house: shots of a blurred wall."""
    house = {"kind": "stage.prop", "id": "old_house", "mount": "floor", "at": [-2.88, 0.685], "heading": 0}
    door = {"kind": "stage.prop", "id": "gus_door", "mount": "surface", "at": [-2.88, 0.685], "heading": 0}
    hill = {"kind": "stage.prop", "id": "hill_rise", "mount": "floor", "at": [-4.5, 1.0], "heading": 0}
    cat = {"kind": "stage.prop", "id": "cat", "mount": "floor", "at": [20.0, 0.0], "heading": 0}
    gus = {"ref": "gus", "at": [-2.88, 0.459], "heading": 0, "actions": [], "r6": {}, "contact": {"with": "gus_door"}}   # (touching it)
    far = {"ref": "me", "at": [9.0, 0.0], "heading": 0, "actions": [], "r6": {}}
    dressed = {"old_house": {"size": [6.2, 6.2, 6.2]}, "gus_door": {"size": [0.7, 2.6, 0.1]}, "hill_rise": {"size": [8, 0.84, 4]}, "cat": {"size": [0.2, 0.3, 0.3]}}
    rb.outside_buildings([gus, far], [house, door, hill, cat], dressed, "s34")
    assert door["at"] == [-2.88, round(0.685 + 3.1 + 0.06, 3)], "on the front face"
    assert gus["at"][1] > 0.685 + 3.1 + 0.6, "in front of the house, in front of its door"
    assert far["at"] == [9.0, 0.0] and cat["at"] == [20.0, 0.0] and hill["at"] == [-4.5, 1.0], "a low hill isn't a building"


def test_the_stagers_through_survives_its_own_filter():
    assert '"contact", "through")' in ROBLOX.read_text(encoding="utf-8")
