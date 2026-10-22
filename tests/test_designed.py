"""Designed 3D (local_runtime/cr_designed.py, Studio 78): a channel whose designed cast the 3D engine builds itself.
@ifologyfilms was cloned onto faceless mannequins, then drawn as pictures; now its ball-headed cast is rebuilt in 3D
from a cast spec. Pure data: no model, no network, no renderer."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_designed as D  # noqa: E402


def profile(art="", characters=(), recognisable=()):
    return {"pictures": {"art_style": {"value": art}, "characters": {"value": list(characters)}},
            "recognisable": list(recognisable)}


IFOLOGY = profile("Minimalist low-poly 3D stick figure character design with pitch-black round head and white facial "
                  "features, cel-shaded with clean solid colours and simple architectural backgrounds.",
                  [{"name": "protagonist", "look": "black ball head, yellow baseball cap, green hoodie, black skinny limbs",
                    "role": "character", "screen_share": 0.789},
                   {"name": "runner", "look": "black spherical head with blue round glasses, yellow sweatshirt, black "
                    "pants", "role": "character", "screen_share": 0.087}],
                  ["Solid black sphere heads with expressive white minimalist facial decals"])


@pytest.mark.parametrize("words,fits", [
    ("Low-poly 3D: figures with black ball heads and small white eyes, bright jackets.", True),
    ("3D stickman animation: figures with thick black ball heads, coloured zip-up jackets, skinny black legs.", True),
    ("Minimalist 3D stickmen: matte-white sphere heads on thin capsule limbs, floating black eyebrows.", True),
    ("Roblox-style blocky avatars with yellow block heads and decal faces.", False),
    ("Bubbly chibi characters with huge heads and tiny bodies, spiky brown hair.", False),
    ("Exaggerated 3D caricatures of footballers: huge heads, beards and tattoos.", False),
    ("A small white rounded robot with a black visor showing two glowing eyes.", False),
    ("Bright high-key preschool CG: toddler figures with outsized round heads.", False),
    ("Figures with no hair or nose: black sphere heads and stick legs.", True),
])
def test_the_kit_builds_ball_heads_on_stick_limbs_and_nothing_it_would_get_wrong(words, fits):
    plan, why = D.fits(profile(words))
    assert bool(plan) is fits, why
    assert why


def test_one_characters_outfit_is_read_from_the_watched_words():
    assert D.character_look("black ball head, yellow baseball cap, green hoodie, black skinny limbs") == {
        "hat": {"kind": "cap", "color": "#E8B320"}, "top": "hoodie", "color": "#3E8C4A"}
    assert D.character_look("black spherical head with blue round glasses, yellow sweatshirt, black pants") == {
        "glasses": True, "top": "sweater", "color": "#E8B320"}
    assert D.character_look("Spherical black head, dot eyes, black pants") == {}, "no garment, no guess"


def test_the_cast_spec_from_what_studio_watched():
    spec = D.from_profile(IFOLOGY)
    assert spec["plan"] == "orb" and spec["head"]["color"] == "#070708" and spec["face"]["set"] == "ring"
    assert spec["cast"][0] == {"name": "protagonist", "role": "lead", "top": "hoodie", "color": "#3E8C4A",
                               "hat": {"kind": "cap", "color": "#E8B320"}}, "the lead is the one most on screen"
    assert spec["palette"][0] == "#3E8C4A" and all(c.startswith("#") for c in spec["palette"])
    white = D.from_profile(profile("Stickmen with matte-white sphere heads and thin limbs."))
    assert white["head"]["color"] == "#F2F0EA" and white["face"]["color"] == "#141416", "dark marks on a white head"


def test_every_value_of_a_cast_spec_is_checked():
    spec = D.clean({"plan": "blob", "head": {"size": 9, "color": "not a colour", "rim_width": -1},
                    "face": {"set": "laser", "size": "x"}, "palette": ["#zzz", "red", "#2f5ba8", 3],
                    "details": ["hood", "rocket"], "sets": ["designed_room", "moon"], "extra": "dropped",
                    "cast": [{"name": "Dot <b>", "top": "cape", "color": "teal", "hat": {"kind": "wizard"}, "glasses": "yes"},
                             "not a row"]})
    assert spec["plan"] == "orb" and spec["head"]["size"] == 0.34 and spec["head"]["color"] == "#070708"
    assert spec["head"]["rim_width"] == 0.0 and spec["face"]["set"] == "ring" and spec["face"]["size"] == 1.15
    assert spec["palette"] == ["#D2382D", "#2F5BA8"] and spec["details"] == ["hood"] and spec["sets"] == ["designed_room"]
    assert "extra" not in spec
    assert spec["cast"] == [{"name": "Dot b", "role": "lead", "top": "jacket", "color": "#1FA5A0"}]


def test_the_engine_reads_the_body_plan_never_the_cast_list():
    arch = D.archetype(D.from_profile(IFOLOGY))
    assert set(arch) == set(D.ENGINE_KEYS) and "cast" not in arch and "sets" not in arch


def test_a_designed_video_is_stamped_scene_by_scene():
    spec = D.from_profile(IFOLOGY)
    video = {"cast": [{"id": "you", "kit": "modern_casual"}, {"id": "mum", "colors": {"coat": "#AA3344"}},
                      {"id": "kid", "designed": {"top": "tshirt", "color": "#E86FA5"}}, {"id": "dad"}],
             "scenes": [{"id": "s1", "look": {"preset": "designed"}, "world": {"biome": "temperate"}},
                        {"id": "s2", "look": {"preset": "designed"}, "world": {"biome": "designed_track"}},
                        {"id": "s3", "look": {"preset": "designed"}, "world": {"biome": "suburb_street", "seed": 4}},
                        {"id": "s4", "look": {"preset": "documentary"}, "world": {"biome": "temperate"}}]}
    out = D.stamp(video, spec)
    assert out["cast"][0]["designed"] == {"top": "hoodie", "color": "#3E8C4A", "hat": {"kind": "cap", "color": "#E8B320"}}
    assert "designed" not in out["cast"][1], "the director's own colours stand (the engine maps them)"
    assert out["cast"][2]["designed"] == {"top": "tshirt", "color": "#E86FA5"}, "the director's own block stands"
    assert out["cast"][3]["designed"]["color"].startswith("#")
    s1, s2, s3, s4 = out["scenes"]
    assert s1["look"]["designed"]["plan"] == "orb" and s1["world"]["biome"] == "designed_park"
    assert s2["world"]["biome"] == "designed_track" and s3["world"] == {"biome": "designed_street", "seed": 4}
    assert "designed" not in s4["look"] and s4["world"]["biome"] == "temperate", "another look is untouched"
    assert video["scenes"][0]["world"]["biome"] == "temperate", "the board itself is never changed"


def test_the_director_hears_the_channels_cast():
    lines = D.cast_lines(D.from_profile(IFOLOGY))
    assert "THE CHANNEL'S DESIGNED CAST" in lines and "the main character" in lines
    assert '"designed": {"top": "hoodie", "color": "#3E8C4A", "hat": {"kind": "cap", "color": "#E8B320"}}' in lines


def test_the_likeness_checks_three_shots_are_rendered_in_the_channels_design():
    video = D.test_video(D.from_profile(IFOLOGY))
    assert [s["id"] for s in video["scenes"]] == ["talk", "run", "react"]
    assert all(s["look"]["preset"] == "designed" and s["look"]["designed"]["plan"] == "orb" for s in video["scenes"])
    assert video["cast"][0]["designed"]["top"] == "hoodie" and video["cast"][1]["designed"]["top"] == "sweater"
    assert all(s["world"]["biome"].startswith("designed_") for s in video["scenes"])
    json.dumps(video)


def test_the_kit_is_found_only_in_a_studio_that_has_it(tmp_path):
    assert not D.engine_ready(tmp_path)
    kit = tmp_path / D.KIT
    kit.parent.mkdir(parents=True)
    kit.write_text("export const MODULES = [];")
    assert D.engine_ready(tmp_path)


def test_a_realistic_room_becomes_the_designed_room():
    video = {"cast": [], "scenes": [{"id": "s", "look": {"preset": "designed"}, "world": {"biome": "interior"},
                                     "structures": [{"kind": "interior.office", "at": [0, 0]}, {"kind": "prop.sign"}]}]}
    scene = D.stamp(video, {})["scenes"][0]
    assert scene["world"] == {"biome": "designed_room", "furnish": "office"}
    assert scene["structures"] == [{"kind": "prop.sign"}]


def test_the_director_is_taught_readable_words_phones_and_the_channels_colours():
    """the first test video's eye chart was blank boxes, its phone a grey card on the floor, a Galileo shot sepia"""
    look = json.loads((Path(__file__).parents[1] / "scripts" / "studio_designed_look.json").read_text(encoding="utf-8"))
    assert "designed.board" in look["director"] and "EYE CHART is style chart" in look["cookbook"]
    assert "action phone" in look["director"] and "PHONES IN HAND" in look["cookbook"]
    assert "never sepia" in look["director"]
