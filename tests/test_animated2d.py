"""Animated 2D channels (cr_animated2d): each channel's own renderer, written by Claude during the study, drawn in code.

A @DartAnimates clone came out as AI stills with camera moves. Studio 101 asked and gated it; Studio 103 animated stick
figures with a fixed kit; Studio 104 has Claude write each channel's renderer on the kit (any 2D-animated channel), judge
its test shots against the channel's own frames within a budget, save it versioned with the style, and only cast and
choreograph per video. Renderers run sandboxed: no network, no files, nothing silently swapped."""
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
sys.path.insert(0, str(ROOT / "tests"))
import cr_animated2d as a2d  # noqa: E402
import cr_style_map as cm  # noqa: E402
import test_style_map as tsm  # noqa: E402

PRIVATE_KIT = ROOT / "data" / "frontier" / "animated2d"

# a renderer written the way Claude writes one: its own drawing, its own vocabulary, nothing but the Kit
BEAN = r"""
Kit.renderer({
  name: "bean-shorts", version: 1,
  manifest: {
    cast: { describe: "smooth white bean-shaped characters", traits: { color: "colour", band: { color: "colour" } } },
    actions: { place: "{x: number, face: left|right}", hop: "{height: number}: a little hop", lean: "leans in" },
    shots: ["wide", "close"], fx: { glow: "{dur: number}: a red glow" }, notes: "x from -3 to 3",
  },
  plates: { draw: false },
  prepare(scene, Kit) {
    for (const a of scene.actions) if (a.move === "hop") Kit.sound(a.at, "jump", 0);
    const bgs = {};
    for (const [id, set] of Object.entries(scene.sets || {})) {
      const bg = document.createElement("canvas"); bg.width = 1920; bg.height = 1080;
      const g = bg.getContext("2d"); g.fillStyle = ((set || {}).sky || ["#101010"])[0]; g.fillRect(0, 0, 1920, 1080);
      for (let k = 0; k < Math.min(60, (set || {}).crowd || 0); k++) { g.fillStyle = "#777777"; g.fillRect(40 + k * 30, 500, 16, 60); }
      bgs[id] = bg;
    }
    return { bgs, scene };
  },
  frame(ctx, t, s, Kit) {
    const shot = (s.scene.shots || []).find((x) => t >= x.start && t < (x.end ?? 1e9)) || (s.scene.shots || [])[0] || {};
    const bg = s.bgs[shot.set] || Object.values(s.bgs)[0];
    ctx.fillStyle = "#101010"; ctx.fillRect(0, 0, 1920, 1080);
    if (bg) ctx.drawImage(bg, 0, 0);
    let i = 0;
    for (const [id, ch] of Object.entries(s.scene.cast)) {
      const hop = s.scene.actions.find((a) => a.who === id && a.move === "hop" && t >= a.at && t < a.at + 0.6);
      const y = 700 - (hop ? Math.sin((t - hop.at) / 0.6 * Math.PI) * 120 : 0);
      ctx.fillStyle = ch.color || "#eeeeee";
      ctx.beginPath(); ctx.ellipse(700 + i * 500, y, 90, 160, 0, 0, Math.PI * 2); ctx.fill();
      if (ch.band) { ctx.fillStyle = ch.band.color; ctx.fillRect(610 + i * 500, y - 90, 180, 24); }
      i++;
    }
  },
});
"""
BEAN_SCENE = {"duration": 3, "cast": {"bean": {"name": "Bean", "color": "#f2f2f2", "band": {"color": "#d0302a"}}},
              "sets": {"void": {}}, "shots": [{"start": 0, "framing": "wide"}],
              "actions": [{"at": 0, "who": "bean", "move": "place", "x": 0}, {"at": 1, "who": "bean", "move": "hop"}],
              "stills": [0.5, 1.3, 2.5]}


def dart(**over):
    p = tsm.animated(**over)
    p["pictures"]["art_style"] = tsm.axis("Minimalist 2D stick figure animation: flat coloured limb puppets over softly "
                                          "blurred flat urban backgrounds, neon energy auras, speed lines, high-contrast "
                                          "monochrome impact frames.")
    p["pictures"]["characters"] = tsm.axis([
        {"name": "red_boxer", "look": "Red stick figure with round head and boxing gloves", "role": "character",
         "screen_share": 0.9},
        {"name": "blue_boxer", "look": "Teal stick figure with boxing gloves and boots", "role": "character",
         "screen_share": 0.1}])
    return p


@pytest.fixture
def kit(monkeypatch):
    monkeypatch.setattr(a2d, "available", lambda home=None: True)


@pytest.fixture
def home(tmp_path):
    """A Studio folder with the private kit, when it is here (the kit is never in the public repository)."""
    if not PRIVATE_KIT.is_dir():
        pytest.skip("the animated 2D kit is private (data/frontier/animated2d)")
    pytest.importorskip("playwright.sync_api")
    shutil.copytree(PRIVATE_KIT, tmp_path / a2d.KIT, ignore=shutil.ignore_patterns("sounds"))
    return tmp_path


def _chromium(fn):
    try:
        return fn()
    except a2d.AnimationStopped:
        raise
    except Exception as error:                              # noqa: BLE001 - no browser on this machine
        if "Executable doesn't exist" in str(error) or "playwright install" in str(error):
            pytest.skip("Local Chromium is unavailable")
        raise


# ── which channels, and the route ───────────────────────────────────────────────────────────────────────────────

def test_every_channel_that_animates_its_characters_fits_and_its_conventions_are_read():
    ok, seen = a2d.fits(dart())
    assert ok and seen.startswith("stick figures")
    bean = tsm.animated(sources={"drawing": 0.9})
    bean["pictures"]["art_style"] = tsm.axis("Smooth white bean-shaped characters with black outlines on black.")
    assert a2d.fits(bean) == (True, "smooth white bean-shaped characters with black outlines on black"), \
        "Claude writes a renderer for any drawn cast, not only stick figures"
    spec = a2d.from_profile(dart())
    assert spec["blur"] == 5 and [c["color"] for c in spec["cast"]] == ["#e0625a", "#3fb59e"]
    assert all(a2d._chroma(h) < 0.12 for h in spec["sky"] + spec["floor"]), "a fighter's red is never the sky"


def test_the_animated_route_says_what_writing_the_animation_costs(kit):
    plan = cm.plan_style(dart(), tsm.STYLES)
    question = next(q for q in plan["questions"] if q["id"] == "made")
    assert question["options"][0] == cm.MADE_CODE and question["labels"][0].startswith("Written in code")
    assert "renderer" not in question["labels"][0], "plain words on the page; the saved answer stays as it was"
    assert not any(q["id"] == "animation" for q in plan["questions"]), "one question decides how videos are made"
    route = plan["route"]
    assert route["engine"] == "animated2d" and "medium_gap" not in route
    # the long paragraph is said once (the route), the time with it; the question stays short
    assert "Claude Code writes this channel's own animation" in route["why"]
    assert "10 to 25 minutes of Claude usage for the whole study" in route["why"] and "at most 46" in route["why"]
    assert "minutes" not in question["ask"] and route["why"] not in question["ask"]
    # a background picture is an opt-in fact, said once, per picture: never "$0.02 a minute"
    assert "$0.02" not in route["why"] and plan["cost"]["usd_per_min"] == 0.0
    assert plan["cost"]["plate_usd"] == 0.02 and "only if" in plan["cost"]["note"]
    assert "drawn or generated" not in plan["base_for"] and "drawn in code" in plan["base_for"]
    look = plan["overrides"]["look"]
    assert look["visuals"] == a2d.VISUALS and plan["overrides"]["features"]["on"] == ["sound"]
    assert plan["overrides"]["pacing"]["clips_per_min"] == 0.0 and plan["cost"]["clips_per_min"] == 0
    told = cm.plan_style(dart(), tsm.STYLES, answers={"animation": cm.ANIMATED_PICTURES})
    assert told["route"]["engine"] == "pictures" and "visuals" not in told["overrides"]["look"]
    was = cm.plan_style(dart(), tsm.STYLES, answers={"animation": cm.ANIMATED_CODE_WAS})
    assert was["route"]["engine"] == "animated2d", "a look answered in 103's words still means animated"


def test_without_the_kit_the_creator_is_told_before_anything_is_paid(monkeypatch):
    monkeypatch.setattr(a2d, "available", lambda home=None: False)
    assert cm.plan_style(dart(), tsm.STYLES)["route"]["medium_gap"] == cm.ANIMATION_GAP


# ── what Claude writes per video, checked against the renderer's own vocabulary ────────────────────────────────

def test_the_cast_keeps_only_the_traits_its_renderer_declares():
    stick = {"color": "colour", "hair": {"style": ["none", "spiky_up"], "color": "colour"},
             "marks": [{"kind": ["cheek_stripes"], "color": "colour"}], "extra_arms": "bool", "height": "number"}
    cast = a2d.clean_cast({"Gojo Satoru": {"name": "Gojo", "color": "teal", "hair": {"style": "spiky up", "color": "white"},
                                           "marks": ["tattoo", {"kind": "cheek_stripes"}], "laser": "yes"},
                           "sukuna": {"color": "#E0625A", "extra_arms": True, "height": 9}}, stick)
    g = cast["gojo_satoru"]
    assert g == {"name": "Gojo", "color": "#3fb59e", "hair": {"style": "spiky_up", "color": "#eeeeee"},
                 "marks": [{"kind": "cheek_stripes"}]}, "an unknown trait or word is dropped, never invented"
    assert cast["sukuna"]["extra_arms"] is True and cast["sukuna"]["height"] == 1.6
    bean = a2d.clean_cast({"bean": {"color": "white", "band": {"color": "red"}, "hair": "spiky"}},
                          {"color": "colour", "band": {"color": "colour"}})
    assert bean["bean"] == {"name": "bean", "color": "#eeeeee", "band": {"color": "#e0625a"}}
    with pytest.raises(a2d.AnimationStopped):
        a2d.clean_cast({}, stick)


def test_the_plan_keeps_the_renderers_own_actions_inside_its_stretch_and_covers_it_with_shots():
    man = {"actions": {"hop": "a hop", "lean": "leans"}, "shots": ["wide", "close"], "fx": {"glow": "a glow"}}
    cast, sets = {"bean": {}, "fox": {}}, {"void": {"prompt": "x"}, "room": {"prompt": "y"}}
    raw = {"shots": [{"start": 50.0, "set": "void", "framing": "close:bean"}, {"start": 50.3, "framing": "two"},
                     {"start": 53, "set": "room", "framing": "medium:fox", "move": "spin"}],
           "actions": [{"at": 50.2, "who": "bean", "move": "hop", "height": 1.5, "mood": "happy", "deep": {"no": 1}},
                       {"at": 51, "who": "fox", "move": "lean", "target": "bean"},
                       {"at": 52, "who": "fox", "move": "punch"}, {"at": 52, "who": "toji", "move": "hop"},
                       {"at": 99, "who": "bean", "move": "hop", "target": "bean"}],
           "fx": [{"kind": "glow", "at": 51.2, "dur": 1}, {"kind": "shake", "at": 51}]}
    plan = a2d.clean_plan(raw, cast, sets, 50.0, 56.0, man)
    shots = plan["shots"]
    assert shots[0]["start"] == 0 and shots[-1]["end"] == 6.0
    assert all(b["start"] == a["end"] for a, b in zip(shots, shots[1:])), "no gap between shots"
    assert [s["framing"] for s in shots] == ["close:bean", "wide"], "framings the renderer knows, merged and named"
    moves = [(x["who"], x["move"]) for x in plan["actions"] if x["move"] != "place"]
    assert moves == [("bean", "hop"), ("fox", "lean"), ("bean", "hop")], "only the renderer's actions and cast"
    hop = plan["actions"][2]
    assert hop["height"] == 1.5 and hop["mood"] == "happy" and "deep" not in hop, "the action's own simple parameters"
    assert plan["actions"][-1]["at"] == 6.0 and "target" not in plan["actions"][-1]
    assert {x["who"] for x in plan["actions"] if x["move"] == "place"} == {"bean", "fox"}, "everyone is placed"
    assert plan["fx"] == [{"kind": "glow", "at": 1.2, "dur": 1.0}]


# ── Studio 109: the renderer's vocabulary, exactly (the owner's Windows test, 2026-10-26) ───────────────────────────

HUD_MAN = {"actions": {"hud": {"does": "the day panel at the top left", "params": {"day": "number", "clock": "text:8",
                                                                                  "temp": "number (degrees)"},
                               "adds": "writes DAY before the day and °C after the temperature"},
                       "board": {"does": "a whiteboard", "params": {"title": "text:24", "show": "sketch",
                                                                    "items": "texts:12", "side": ["left", "right"]}},
                       "talk": "{line: text:30, to: id}: says a line to someone"},
           "fx": {"flash": "{dur: number, color: colour}: a flash"}, "shots": ["wide", "close"]}


def test_every_parameter_is_checked_against_the_type_its_renderer_declared():
    """The bunker video's HUD read "DAY DAY 1" and "18°C°": the renderer wrote DAY and °C itself and the director sent
    them too. A plan's parameters now take their declared types."""
    raw = {"shots": [{"start": 0, "framing": "wide", "set": "bunker"}],
           "actions": [{"at": 0.5, "who": "you", "move": "hud", "day": "DAY 1", "clock": "6:42 PM tonight", "temp": "18°C"},
                       {"at": 1, "who": "you", "move": "board", "title": "Someone has already done the math for us",
                        "show": {"kind": "line", "trend": "down", "values": ["412", 300, "x"], "bogus": 1},
                        "items": ["412 meals", "30 days", 7], "side": "Right"},
                       {"at": 2, "who": "you", "move": "talk", "line": "We have food for one day and a half at most", "to": "mara"},
                       {"at": 3, "who": "you", "move": "talk", "line": "Hi", "to": "nobody"}],
           "fx": [{"kind": "flash", "at": 1, "dur": "0.2 s", "color": "white"}]}
    plan = a2d.clean_plan(raw, {"you": {"name": "You"}, "mara": {"name": "Mara"}}, {"bunker": {"prompt": "x"}}, 0, 10, HUD_MAN)
    hud, board, talk, bad = plan["actions"][-4:]
    assert hud["day"] == 1 and hud["temp"] == 18 and hud["clock"] == "6:42 PM", "numbers bare, text within its length"
    assert board["title"] == "Someone has already done" and len(board["title"]) <= 24, "cut on a whole word, no ellipsis"
    assert board["show"] == {"kind": "line", "values": [412.0, 300.0], "trend": "down"} and board["side"] == "right"
    assert board["items"] == ["412 meals", "30 days", "7"]
    assert talk["line"] == "We have food for one day and a" and talk["to"] == "mara" and "to" not in bad
    assert plan["fx"][0]["dur"] == 0.2 and plan["fx"][0]["color"] == "#eeeeee"


def test_an_old_renderers_parameters_take_the_form_its_author_used():
    """A renderer saved before 109 says nothing of types: how its own test scenes call each action does."""
    man = {"actions": {"hud": "{day, time, temp}: the HUD: DAY n, a clock, the temperature", "sign": "{text}: a sign"},
           "examples": {"actions": {"hud": {"day": 1, "time": "6:42 PM", "temp": 18}}}}
    raw = {"actions": [{"at": 0, "who": "a", "move": "hud", "day": "Day 3", "time": "7:00 PM", "temp": "21°C"},
                       {"at": 1, "who": "a", "move": "sign", "text": "CAPACITY 40"}]}
    hud, sign = a2d.clean_plan(raw, {"a": {"name": "A"}}, {"s": {}}, 0, 5, man)["actions"][-2:]
    assert (hud["day"], hud["time"], hud["temp"]) == (3, "7:00 PM", 21) and sign["text"] == "CAPACITY 40"
    plain = a2d.clean_plan(raw, {"a": {"name": "A"}}, {"s": {}}, 0, 5, {"actions": man["actions"]})["actions"][-2]
    assert plain["day"] == "Day 3", "without an example nothing is guessed"
    long = a2d.clean_plan({"actions": [{"at": 0, "who": "a", "move": "sign", "text": "word " * 30}]}, {"a": {}},
                          {"s": {}}, 0, 5, man)["actions"][-1]
    assert 50 <= len(long["text"]) <= 60, "untyped long words are kept whole up to 60 characters, never dropped"


def test_the_director_reads_each_parameters_type_what_the_renderer_adds_and_its_authors_example():
    man = {**HUD_MAN, "examples": {"actions": {"hud": {"day": 1, "clock": "6:42 PM", "temp": 18}}}}
    lines = "\n".join(a2d.vocabulary_lines(man, "actions"))
    assert "- hud {day: a bare number, clock: text, at most 8 characters, temp: a bare number (degrees)}: the day panel" in lines
    assert "[the renderer itself writes: writes DAY before the day and °C after the temperature; never send that]" in lines
    assert 'e.g. {"move": "hud", "day": 1, "clock": "6:42 PM", "temp": 18}' in lines
    assert "- talk {line: text, at most 30 characters, to: a cast id}: says a line to someone" in lines
    assert "- flash {dur: a bare number, color: #rrggbb}: a flash" in "\n".join(a2d.vocabulary_lines(man, "fx"))


def test_places_keep_what_they_are_and_a_renderer_that_draws_its_own_takes_new_ones_from_the_director():
    sets = a2d.clean_sets({"Bunker Hall": {"prompt": "a concrete bunker hall", "inside": True, "walls": "grey",
                                           "light": "harsh strip lights", "crowd": "about 80 people",
                                           "props": ["steel hatch", "sign: CAPACITY 40", "", {"x": 1}],
                                           "sky": ["#111111", "#222222"], "floor_colors": ["#333333", "#444444"]}})
    assert sets["bunker_hall"] == {"prompt": "a concrete bunker hall", "sky": ["#111111", "#222222"],
                                   "floor_colors": ["#333333", "#444444"], "inside": True, "walls": "#9a9da3",
                                   "light": "harsh strip lights", "props": ["steel hatch", "sign: CAPACITY 40"], "crowd": 80}
    raw = {"shots": [{"start": 0, "set": "bunker_hall", "framing": "wide", "crowd": 100},
                     {"start": 2, "set": "hatch", "framing": "wide"}],
           "sets": {"hatch": {"prompt": "the round steel hatch at the top of a ladder", "inside": True},
                    "bunker_hall": {"prompt": "never replaces a designed place"}, "unused": {"prompt": "x"}}}
    plan = a2d.clean_plan(raw, {"a": {}}, sets, 0, 5, {}, new_sets=2)
    assert [s["set"] for s in plan["shots"]] == ["bunker_hall", "hatch"] and plan["shots"][0]["crowd"] == 100
    assert list(plan["sets"]) == ["hatch"], "only the new places its shots use"
    film = a2d.film_spec(plan, {"cast": {"a": {}}, "sets": sets}, {}, {})
    assert film["sets"]["hatch"]["inside"] is True and film["sets"]["bunker_hall"]["props"][1] == "sign: CAPACITY 40"
    plates = a2d.clean_plan(raw, {"a": {}}, sets, 0, 5, {})
    assert "sets" not in plates and plates["shots"][1]["set"] == "bunker_hall", "a picture per place is paid: no new ones"


def test_the_channels_recurring_characters_are_in_every_video():
    """The study's test shots had the channel's red-cap mascot; the video didn't."""
    man = {"cast": {"traits": {"color": "colour", "cap": "colour"}, "recurring": {"you": {"name": "You", "color": "#3f6fb0",
                                                                                         "cap": "#d0302a"}}}}
    cast = a2d.clean_cast({"mara": {"name": "Mara", "color": "red"}}, man["cast"]["traits"], a2d.recurring(man))
    assert list(cast) == ["you", "mara"] and cast["you"] == {"name": "You", "color": "#3f6fb0", "cap": "#d0302a",
                                                             "recurring": True}
    again = a2d.clean_cast({"you": {"name": "You", "color": "#3f6fb0"}}, man["cast"]["traits"], a2d.recurring(man))
    assert again == {"you": {"name": "You", "color": "#3f6fb0", "recurring": True}}, "the casting's own entry stands"
    assert "keep these ids and traits" in a2d.recurring_words(man, {})
    spec = a2d.from_profile(dart())
    assert spec["cast"][0]["share"] == 0.9
    words = a2d.recurring_words({}, spec)
    assert "red_boxer: Red stick figure" in words and "90%" in words and "blue_boxer" not in words
    assert a2d.recurring_words({}, {}).startswith("none")


def test_a_plan_that_holds_repeats_or_idles_is_sent_back_in_the_directors_terms():
    """The bunker video held one still figure for long stretches, repeated shots and left out the channel's mascot."""
    shots = [{"start": 0.0, "end": 9.0, "set": "hall", "framing": "wide", "cast": ["mara"]},
             {"start": 9.0, "end": 11.0, "set": "hall", "framing": "close:mara", "cast": ["mara"]},
             {"start": 11.0, "end": 14.0, "set": "hall", "framing": "close:mara", "cast": ["mara"]}]
    plan = {"duration": 14.0, "shots": shots, "fx": [],
            "actions": [{"at": 0, "who": "mara", "move": "place"}, {"at": 7.5, "who": "mara", "move": "wave"}]}
    cast = {"cast": {"you": {"name": "You", "recurring": True}, "mara": {"name": "Mara"}}}
    notes = a2d.plan_notes(plan, cast, {"shot_s": 2.0}, t0=60.0)
    assert notes == ["60.0 s to 69.0 s: one shot holds 9 s on the same view; cut on the words to another framing, place "
                     "or person",
                     "71.0 s: this shot repeats the one before it (the same place, framing and people)",
                     "60.0 s to 67.5 s: nobody does anything for 8 s; give the people on screen actions that show the words",
                     "67.5 s to 74.0 s: nobody does anything for 6 s; give the people on screen actions that show the words",
                     "60.0 s to 74.0 s: the channel's recurring character (you) is in none of the shots"]
    busy = {"duration": 6.0, "fx": [], "shots": [{"start": 0, "end": 3, "set": "hall", "framing": "wide", "cast": ["you"]},
                                                  {"start": 3, "end": 6, "set": "pantry", "framing": "wide", "cast": ["you"]}],
            "actions": [{"at": t, "who": "you", "move": "walk"} for t in (0.5, 2.5, 4.5)]}
    assert a2d.plan_notes(busy, cast, {}) == []


def test_the_swept_frames_find_a_frozen_picture_words_off_the_frame_or_over_others_and_shots_that_look_alike():
    moves = [(k * 0.25, 0.0 if 2.0 <= k * 0.25 <= 5.5 else 0.01 if k * 0.25 > 7 else 1.2) for k in range(1, 40)]
    sig_a, sig_b = [100] * 144, [100] * 143 + [104]
    look = {"moves": moves, "sigs": {0.75: sig_a, 3.0: sig_a, 6.0: sig_b, 8.5: [10] * 144},
            "texts": [{"kind": "over", "text": "DAY DAY 1", "other": "6:42 PM", "t": 0.5},
                      {"kind": "over", "text": "DAY DAY 1", "other": "6:42 PM", "t": 0.75},
                      {"kind": "off", "text": "a title sliding in", "t": 2.0}],
            "overflow": [{"text": "until then, the bunker decides who stays", "t": 6.0}]}
    plan = {"duration": 10.0, "shots": [{"start": 0, "end": 2, "set": "hall", "framing": "wide"},
                                        {"start": 2, "end": 4, "set": "pantry", "framing": "wide"},
                                        {"start": 4, "end": 8, "set": "pantry", "framing": "close:you"},
                                        {"start": 8, "end": 10, "set": "hatch", "framing": "wide"}]}
    notes = a2d.frame_notes(look, plan)
    assert "1.8 s to 5.5 s: nothing on screen moves (a frozen picture); give someone an action there" in notes
    assert len([n for n in notes if "frozen" in n]) == 1, "a figure that only breathes (a tiny change) is not frozen"
    assert any('"DAY DAY 1" are drawn over "6:42 PM"' in n for n in notes)
    assert not any("sliding" in n for n in notes), "words off the frame in one sampled frame are a slide-in, not a fault"
    assert any("until then, the bunker decides who stays\" don't fit their box" in n for n in notes)
    assert "2.0 s: this shot looks the same as the one before it" in notes and \
        "4.0 s: this shot looks the same as the one before it" in notes and len([n for n in notes if "same" in n]) == 2


def test_long_narration_is_choreographed_in_even_stretches_between_lines():
    lines = [(i * 5.0, i * 5.0 + 4.5, f"line {i}", []) for i in range(20)]
    parts = a2d.chunks(lines, 100.0)
    assert [round(p[0]) for p in parts] == [0, 50], "even stretches, never a stub of a few seconds"
    short = [(i * 4.5, i * 4.5 + 4.0, f"line {i}", []) for i in range(11)]
    assert len(a2d.chunks(short, 49.0)) == 1, "a minute's narration is one scene"


def test_sound_marks_play_the_kits_recordings_or_studios_cues():
    assert a2d.sound_file("punch") == "file:sfx/animated2d/punch.mp3"
    assert a2d.sound_file("riser") == "riser" and a2d.sound_file("../../etc") == ""


def test_renderer_code_that_reaches_beyond_the_kit_is_refused_in_plain_words():
    assert a2d.check_code(BEAN) == []
    for bad in ("fetch('https://x')", "new XMLHttpRequest()", "eval('1')", "new Function('x')", "localStorage.x = 1",
                "import('x')", "new WebSocket('ws://x')"):
        problems = a2d.check_code(bad + ";Kit.renderer({})")
        assert problems and "may not" in problems[0], bad
    assert "never calls Kit.renderer" in " ".join(a2d.check_code("const x = 1;"))
    assert "under" in a2d.check_code("x" * (a2d.RENDERER_MAX + 1) + "Kit.renderer(")[0]
    names = ["Dart", "DartAnimates"]
    assert "own name" in a2d.check_code(BEAN + ';ctx.fillText("DartAnimates", 1700, 50);', names)[0], \
        "the channel's watermark is in its frames, never in its renderer"
    assert a2d.check_code(BEAN + ';const look = "standard dash";', names) == []
    assert a2d.check_code(BEAN.replace('"bean-shorts"', '"dart-stick-fights"'), names) == [], "its own name is fine"


def test_the_bar_is_the_channels_own():
    assert a2d.bar_for(8.5) == 7.0 and a2d.bar_for(6.0) == 5.0 and a2d.bar_for(None) == 5.5


# ── the release ships the private kit ───────────────────────────────────────────────────────────────────────────

def test_the_builder_ships_the_kit_and_its_sounds(tmp_path):
    from scripts import build_frontier_admin as builder
    kit_dir = tmp_path / "data/frontier/animated2d"
    (kit_dir / "sounds").mkdir(parents=True)
    for name in ("core.js", "rig.js", "draw.js", "moves.js", "fx.js", "film.js", "stage.js", "bind.js", "example-stick.js",
                 "example-own.js", "cast.txt", "director.txt", "build.txt", "review.txt", "AUTHORING.md"):
        (kit_dir / name).write_text("x", encoding="utf-8")
    (kit_dir / "sounds" / "punch.mp3").write_bytes(b"mp3")
    (kit_dir / "cast-108.txt").write_text("x", encoding="utf-8")      # an earlier prompt, for videos resumed after an update
    shipped = {dest for _, dest in builder.private_animated2d_resources(tmp_path)}
    assert "animated2d/cast-108.txt" in shipped
    assert {"animated2d/core.js", "animated2d/stage.js", "animated2d/build.txt", "animated2d/AUTHORING.md",
            "assets/sfx/animated2d/punch.mp3"} <= shipped
    (kit_dir / "notes.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected"):
        builder.private_animated2d_resources(tmp_path)
    (kit_dir / "notes.md").unlink()
    (kit_dir / "core.js").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        builder.private_animated2d_resources(tmp_path)


# ── the sandbox, the build loop, saving (with the private kit and Chromium) ────────────────────────────────────

def test_a_renderer_runs_with_no_way_out(home):
    sneaky = (r"""window.__got = 0; window.__refused = 0;
document.addEventListener("securitypolicyviolation", () => { window.__refused++; });
try { window["fe" + "tch"]("https://example.com/x").then(() => { window.__got++; }).catch(() => {}); } catch (e) {}
const img = new Image(); img.onload = () => { window.__got++; }; img.src = "https://example.com/p.png";
""" + BEAN)
    assert a2d.check_code(sneaky) == [], "obfuscated: only the page itself can stop it"

    def run():
        box = a2d._Sandbox(a2d.page({**BEAN_SCENE, "width": 640, "height": 360}, sneaky, home), 640, 360)
        try:
            time.sleep(1.0)
            return box.page.evaluate("[window.__got, window.__refused]"), box.blocked, box.error()
        finally:
            box.close()
    (got, refused), blocked, err = _chromium(run)
    assert got == 0 and refused >= 1 and err == "", "nothing reaches the network; the page refuses it itself"
    collide = "const POSES = {}; const MOVES = 1;\n" + BEAN
    assert _chromium(lambda: a2d.render_stills({**BEAN_SCENE, "stills": [1]}, home / "own", collide, home=home)), \
        "a renderer's own names never collide with the kit's"
    with pytest.raises(a2d.AnimationStopped, match="nope is not defined"):
        _chromium(lambda: a2d.render_stills({**BEAN_SCENE, "stills": [1]}, home / "load", "nope.x = 1;\n" + BEAN,
                                            home=home))
    with pytest.raises(a2d.AnimationStopped, match="boom"):
        _chromium(lambda: a2d.render_stills({**BEAN_SCENE, "stills": [1]}, home / "bad",
                                            BEAN.replace("if (bg) ctx.drawImage(bg, 0, 0);", "throw new Error('boom');"),
                                            home=home))


def _answer(code, scene):
    return f"```javascript\n{code}\n```\n```json\n{json.dumps(scene)}\n```"


def _frames(folder, n=6):
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for k in range(n):
        p = folder / f"ref_{k}.jpg"
        Image.new("RGB", (320, 180), (20 + k * 10, 20, 20)).save(p)
        out.append(p)
    return out


def test_claude_writes_the_renderer_its_shots_are_judged_and_it_is_revised_until_it_reaches_the_bar(home, tmp_path):
    refs = _frames(tmp_path / "refs")
    drafts = [_answer("fetch('x'); Kit.renderer({})", BEAN_SCENE),
              _answer(BEAN, BEAN_SCENE),
              _answer(BEAN.replace("#101010", "#000000"), BEAN_SCENE)]
    seen, judged = [], []

    def see(prompt, images):
        seen.append((prompt, images))
        return drafts[len(seen) - 1]

    def judge(references, candidates):
        judged.append(candidates)
        if candidates == [str(refs[-1])]:
            return {"candidates": [{"overall": 8.5}], "dealbreakers": ["the channel's own frame: never shown"]}
        score = 4 if len(judged) == 2 else 7.5
        return {"candidates": [{"overall": score, "characters": score}] * 3,
                "dealbreakers": ["the bean's band sits too low"], "fixes": ["raise the band"]}
    result = _chromium(lambda: a2d.build(tmp_path / "op", {"sky": ["#111111", "#222222"]}, {"art": "beans"}, see=see,
                                         judge=judge, references=refs, home=home, log=lambda m: None))
    assert result["state"] == "passed" and result["round"] == 3 and result["overall"] == 7.5 and result["bar"] == 7.0
    assert "may not" in seen[1][0], "the refused draft's reasons go back to Claude"
    assert "band sits too low" in seen[2][0] and len(seen[2][1]) == 5 + 3 + 5, "the judge's words and the last shots go back"
    assert [Path(p).name for p in seen[2][1][-5:]] == ["inside_0.jpg", "outside_0.jpg", "crowd_0.jpg", "era_0.jpg",
                                                      "props_0.jpg"], \
        "with Studio's probes: the test scene in a bunker room, on a beach, with a crowd, in 1815 and with other props"
    assert "on a sunny beach, with a crowd, with that crowd in 1815, then in the bunker room with other props" in seen[2][0]
    assert not any("never shown" in prompt for prompt, _ in seen), "the held-out frame's notes are not Claude's to fix"
    assert len(judged) == 3, "the channel's own frame is scored once, on its own"
    assert result["manifest"]["name"] == "bean-shorts" and (tmp_path / "op/renderer/renderer.js").is_file()
    assert result["sha256"] == hashlib.sha256((tmp_path / "op/renderer/renderer.js").read_bytes()).hexdigest()
    again = a2d.build(tmp_path / "op", {"sky": ["#111111", "#222222"]}, {"art": "beans"},
                      see=lambda p, i: pytest.fail("never asked twice"), judge=judge, references=refs, home=home)
    assert again["state"] == "passed"


def test_a_renderer_that_never_reaches_the_bar_is_saved_as_below_it(home, tmp_path):
    refs = _frames(tmp_path / "refs")
    judge = lambda references, candidates: {"candidates": [{"overall": 8 if len(candidates) == 1 else 4}]  # noqa: E731
                                            * len(candidates)}
    result = _chromium(lambda: a2d.build(tmp_path / "op", {}, {}, see=lambda p, i: _answer(BEAN, BEAN_SCENE),
                                         judge=judge, references=refs, home=home, rounds=2, log=lambda m: None))
    assert result["state"] == "below_bar" and result["overall"] == 4 and result["bar"] == 6.5
    nothing = a2d.build(tmp_path / "op2", {}, {}, see=lambda p, i: "I can't", judge=judge, references=refs,
                        home=home, rounds=2, log=lambda m: None)
    assert nothing["state"] == "failed"


def test_a_judge_that_never_answers_saves_the_renderer_unjudged_never_as_zero(home, tmp_path, monkeypatch):
    """A customer's two studies (2026-10-27) were saved as 0/10 against a bar of 5.5: their plan's style checks were
    used up by the looks, so every likeness call was refused, and each refusal was counted as a score of 0."""
    refs = _frames(tmp_path / "refs")
    # a draft that draws what every video needs (the test bean has few actions and no eras: not what this is about)
    monkeypatch.setattr(a2d, "vocabulary_gaps", lambda man: "")
    monkeypatch.setattr(a2d, "ERA_SHOWS", -1)
    monkeypatch.setattr(a2d, "PROPS_SHOW", -1)
    seen, logged = [], []

    def refused(references, candidates):
        raise RuntimeError("You've used today's style checks. Try again tomorrow.")

    def see(prompt, images):
        seen.append(prompt)
        return _answer(BEAN, BEAN_SCENE)
    result = _chromium(lambda: a2d.build(tmp_path / "op", {}, {}, see=see, judge=refused, references=refs, home=home,
                                         log=logged.append))
    assert result["state"] == "unjudged" and result["overall"] is None and result["characters"] is None
    assert "today's style checks" in result["why"] and result["bar"] == 5.5
    assert len(seen) == a2d.MIN_ROUNDS, "without scores, the build stops once a draft draws what every video needs"
    assert "could not score your test shots" in seen[1] and "0/10 overall" not in seen[1], "never told it scored 0"
    assert not any("0/10" in m for m in logged) and any("no likeness score" in m for m in logged)
    assert (tmp_path / "op/renderer/renderer.js").is_file(), "never block a clone: the renderer is kept"
    style = {"name": "cr_clone_bean", "look": {"visuals": "cr_animated2d", "animated2d": {}}}
    a2d.attach(style, tmp_path / "op", home)
    saved = style["look"]["animated2d"]["renderer"]
    assert saved["state"] == "unjudged" and saved["overall"] is None and "today's style checks" in saved["why"]
    again = a2d.build(tmp_path / "op", {}, {}, see=lambda p, i: pytest.fail("never asked twice"), judge=refused,
                      references=refs, home=home)
    assert again["state"] == "unjudged", "a finished build is kept as it is"


def test_a_scored_draft_ranks_over_an_unscored_one(home, tmp_path):
    refs = _frames(tmp_path / "refs")
    answers = iter([None, 4.0, None, None])                 # the held-out frame unscored, then draft 1 only

    def judge(references, candidates):
        score = next(answers)
        if score is None:
            raise RuntimeError("Style checking couldn't answer right now. Try again in a little while.")
        return {"candidates": [{"overall": score, "characters": score}] * len(candidates)}
    result = _chromium(lambda: a2d.build(tmp_path / "op", {}, {}, see=lambda p, i: _answer(BEAN, BEAN_SCENE),
                                         judge=judge, references=refs, home=home, rounds=3, log=lambda m: None))
    assert result["state"] == "below_bar" and result["round"] == 1 and result["overall"] == 4.0


def test_a_first_draft_over_the_bar_is_still_revised_once_and_the_bar_comes_from_the_channels_usual_frames(home,
                                                                                                         tmp_path):
    refs = _frames(tmp_path / "refs", 9)
    seen, controls = [], []

    def judge(references, candidates):
        if len(candidates) == 2:                            # the held-out frames: an odd one and a usual one
            controls.append(candidates)
            return {"candidates": [{"overall": 3.0}, {"overall": 8.0}]}
        score = 7.0 if len(seen) == 1 else 6.8
        return {"candidates": [{"overall": score, "characters": score}] * 3, "fixes": ["thinner limbs"]}

    def see(prompt, images):
        seen.append(prompt)
        return _answer(BEAN, BEAN_SCENE)
    result = _chromium(lambda: a2d.build(tmp_path / "op", {}, {}, see=see, judge=judge, references=refs, home=home,
                                         log=lambda m: None, names=["Bean Shorts"]))
    assert controls == [[str(refs[-2]), str(refs[-1])]] and result["control"] == 8.0 and result["bar"] == 6.5
    assert len(seen) == 2 and "clears the bar" in seen[1] and "thinner limbs" in seen[1], "revised once from the notes"
    assert result["state"] == "passed" and result["round"] == 1 and result["overall"] == 7.0, "the better draft is kept"
    assert "Bean Shorts" in seen[0], "Claude is told the channel's name it must never draw"


# a fade out that never lets go: every frame after it is black (a real renderer's fault, Day One Films 2026-10-24)
STUCK = BEAN.replace("      i++;\n    }\n  },", "      i++;\n    }\n    if ((s.scene.fx || []).some((f) => f.kind === "
                     "'fade' && t >= f.at)) { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, 1920, 1080); }\n  },")
STRESS = {**BEAN_SCENE, "duration": 8, "fx": [{"kind": "fade", "at": 2, "dir": "out"}, {"kind": "fade", "at": 3, "dir": "in"}]}


def test_blank_stretches_mid_film_are_faults_and_a_final_fade_is_not():
    samples = [(k * 0.25, 0.0 if 27 <= k * 0.25 < 55.8 else 20.0) for k in range(227)]
    assert a2d.blank_runs(samples, 56.56) == [(27.0, 56.0)]
    fade_pair = [(k * 0.25, 0.0 if 25.3 <= k * 0.25 < 26.9 else 20.0) for k in range(227)]
    assert a2d.blank_runs(fade_pair, 56.56) == [], "a fade out and back in is the director's own"
    ending = [(k * 0.25, 0.0 if k * 0.25 >= 54 else 20.0) for k in range(227)]
    assert a2d.blank_runs(ending, 56.56) == [], "a fade out at the end is the film's ending"


def test_a_renderer_whose_picture_goes_blank_in_its_stress_scene_is_sent_back(home, tmp_path):
    assert STUCK != BEAN
    samples = _chromium(lambda: a2d.sweep({**STRESS, "width": 1920, "height": 1080}, STUCK, home=home))
    assert a2d.blank_runs(samples, 8) == [(2.0, 8.0)]
    refs = _frames(tmp_path / "refs")
    drafts = [_answer(STUCK, {**BEAN_SCENE, "stress": STRESS}), _answer(BEAN, {**BEAN_SCENE, "stress": STRESS})]
    seen = []

    def see(prompt, images):
        seen.append(prompt)
        return drafts[len(seen) - 1]
    judge = lambda references, candidates: {"candidates": [{"overall": 8 if len(candidates) == 1 else 7}]  # noqa: E731
                                            * len(candidates)}
    result = _chromium(lambda: a2d.build(tmp_path / "op", {}, {}, see=see, judge=judge, references=refs, home=home,
                                         log=lambda m: None))
    assert "went blank from 2 s to 8 s (after place at 0 s, hop at 1 s, fade out at 2 s)" in seen[1], \
        "Studio's frame review goes back to Claude"
    assert result["state"] == "passed" and result["round"] == 2


def test_the_example_claude_is_shown_draws_its_own_world_and_passes_studios_frame_review(home):
    """Claude is shown a neutral renderer (its own characters and places), never the stick fights as the way to draw."""
    code = (home / a2d.KIT / a2d.EXAMPLE_OWN).read_text(encoding="utf-8")
    assert a2d.check_code(code) == [] and "Kit.film" not in code and "Kit.draw" not in code
    scene = {"duration": 9, "width": 1920, "height": 1080,
             "cast": {"pip": {"name": "Pip", "color": "#f0c040", "mood": "happy", "size": 1.1}},
             "sets": {"park": {"sky": ["#9fd0f0", "#e8f4fb"], "floor_colors": ["#5aa060"]}},
             "shots": [{"start": 0, "end": 5, "set": "park", "framing": "wide"}, {"start": 5, "set": "park",
                                                                                   "framing": "close"}],
             "actions": [{"at": 0, "who": "pip", "move": "place", "x": -2, "face": "right"},
                         {"at": 0.5, "who": "pip", "move": "walk", "to": 1}, {"at": 3, "who": "pip", "move": "jump"},
                         {"at": 4, "who": "pip", "move": "wave"}, {"at": 5, "who": "pip", "move": "mood", "mood": "sad"}],
             "fx": [{"kind": "caption", "at": 1, "text": "Hello", "dur": 1.5}, {"kind": "shake", "at": 3.6, "amount": 12},
                    {"kind": "flash", "at": 3.6, "dur": 0.2}, {"kind": "fade", "at": 5.5, "dir": "out"},
                    {"kind": "fade", "at": 6.5, "dir": "in"}]}
    samples = _chromium(lambda: a2d.sweep(scene, code, home=home))
    assert a2d.blank_runs(samples, 9) == [] and min(x for t, x in samples if t > 7.2) > 4, "a fade out lets go"
    man = _chromium(lambda: a2d.manifest(code, home))
    assert set(man["actions"]) >= {"place", "walk", "jump"} and man["plates"]["draw"] is False
    assert "never its look" in a2d._prompt("build.txt", home) or "never its look" in a2d._prompt("AUTHORING.md", home)


ONE_PLACE = r"""
Kit.renderer({
  name: "one-place", version: 1,
  manifest: { cast: { describe: "a block", traits: { color: "colour" } },
              actions: { place: "{x}", sign: { does: "a sign at the top right", params: { text: "text:40" } } } },
  plates: { draw: false },
  prepare(scene, Kit) { return { scene }; },
  frame(ctx, t, s, Kit) {
    ctx.fillStyle = "#335533"; ctx.fillRect(0, 0, 1920, 1080);
    ctx.fillStyle = "#eeeeee"; ctx.fillRect(900 + Math.sin(t * 3) * 20, 500, 100, 300);
    const sign = s.scene.actions.find((a) => a.move === "sign" && t >= a.at);
    if (sign) { ctx.fillStyle = "#000"; ctx.font = "120px sans-serif"; ctx.fillText(String(sign.text || ""), 1300, 200); }
  },
});
"""


def test_a_draft_that_knows_only_its_own_place_is_sent_back_with_what_every_video_needs(home, tmp_path):
    """The bunker video: a renderer that knew only the reference's places drew a bunker as supermarket aisles, 4 people
    for 100 strangers, and board text off the board. The build now draws every draft somewhere new, with a crowd and
    with every text parameter at its longest."""
    scene = {"duration": 3, "cast": {"a": {"name": "A"}}, "sets": {"street": {"prompt": "a street"}},
             "shots": [{"start": 0, "set": "street", "framing": "wide"}], "stills": [1.0],
             "actions": [{"at": 0, "who": "a", "move": "place", "x": 0}, {"at": 0, "who": "a", "move": "sign", "text": "OPEN"}]}
    man = _chromium(lambda: a2d.manifest(ONE_PLACE, home))
    got = _chromium(lambda: a2d.probe(scene, {}, ONE_PLACE, tmp_path / "probe", home=home, man=man,
                                      channel={"cast": [{"name": "you", "look": "a red-cap stick figure", "share": 0.8}]},
                                      stress={**scene, "width": 1920, "height": 1080}))
    issues = "\n".join(got["issues"])
    assert got["problems"] == [] and [k for k, _ in got["pictures"]] == ["inside", "outside", "crowd", "era", "props"]
    assert "came out the same in both" in issues and "crowd: 40 drew nobody more" in issues
    assert "with other props (wooden shoring beams" in issues and "never a stock prop in its place" in issues
    assert "your renderer has only 1 actions (sign): every video is made of them" in issues, "one move repeats"
    assert "with each text parameter at its longest (sign.text)" in issues and "run off the edge of the frame" in issues
    assert "these parameters say no type: place.x" in issues
    assert "you (a red-cap stick figure) in 80% of its shots" in issues and "manifest.cast.recurring" in issues


def test_the_probes_advice_ranks_drafts_but_never_keeps_a_study_going(home, tmp_path):
    """The coordinator's review: advice (a place drawn alike, no crowd, untyped parameters) pushed studies to 4 drafts
    and the 24-minute cap, past "usually 5 to 15 minutes". It goes back once, ranks the drafts, and is saved."""
    refs = _frames(tmp_path / "refs")
    scene = {"duration": 3, "cast": {"a": {"name": "A"}}, "sets": {"street": {"prompt": "a street"}},
             "shots": [{"start": 0, "set": "street", "framing": "wide"}], "stills": [1.0],
             "actions": [{"at": 0, "who": "a", "move": "place", "x": 0}]}
    seen = []

    def see(prompt, images):
        seen.append(prompt)
        return _answer(ONE_PLACE, scene)
    judge = lambda references, candidates: {"candidates": [{"overall": 8 if len(candidates) == 1 else 7}]  # noqa: E731
                                            * len(candidates)}
    result = _chromium(lambda: a2d.build(tmp_path / "op", {}, {"cast": [{"name": "x", "look": "y", "share": 0.2}]},
                                         see=see, judge=judge, references=refs, home=home, log=lambda m: None))
    assert len(seen) == 2 and result["state"] == "passed", "two drafts, as before the probes"
    assert "came out the same in both" in seen[1] and any("came out the same" in i for i in result["issues"])
    assert "places" not in result["manifest"], "it never showed it can draw any place"


def test_a_quote_says_the_claude_time_the_frame_check_adds():
    import cr_studio_tools as tools
    q = {"titles": 1, "minutes": 3, "where": "this_computer", "style_name": "Beans", "expected_total_usd": 0.1,
         "media": {"per_video_usd": 0.1}, "frame_check": {"on": True, "adds_minutes": [3, 8]}}
    assert "The frame check adds about 3 to 8 minutes of Claude time a video" in tools.summary(q)
    assert "frame check" not in tools.summary({**q, "frame_check": {"on": False, "adds_minutes": [3, 8]}})


def test_every_place_carries_its_era_to_the_renderer_its_plate_and_the_director():
    """A Waterloo script was drawn with modern helmeted infantry, a 1944 tunnel escape with filing cabinets in a forest
    (a customer, 2026-10-27): each place now says when it is, and the renderer dresses it for that time."""
    sets = a2d.clean_sets({"field": {"prompt": "a muddy field at Waterloo", "era": "June 1815, the Napoleonic Wars " * 4,
                                     "props": ["cannon", "powder barrels"]},
                           "today": {"prompt": "a parking lot"}})
    assert sets["field"]["era"].startswith("June 1815") and len(sets["field"]["era"]) <= a2d.ERA_MAX
    assert "era" not in sets["today"], "a place without a time says none"
    film = a2d.film_spec({"duration": 2, "shots": [], "actions": [], "fx": []}, {"cast": {}, "sets": sets}, {}, {})
    assert film["sets"]["field"]["era"] == sets["field"]["era"]
    assert "(June 1815" in a2d.plate_prompt(sets["field"], {}) and "(" not in a2d.plate_prompt(sets["today"], {})
    assert '"era": "June 1815' in a2d._places_words(sets)


def test_a_renderer_with_a_few_actions_and_framings_is_told_its_videos_repeat():
    man = {"actions": {"place": {"does": "x"}, "walk": {"does": "w"}, "point": {"does": "p"}, "turn": {"does": "t"}},
           "shots": ["wide", "close:<id>", "close:<id>"]}
    said = a2d.vocabulary_gaps(man)
    assert "only 3 actions (walk, point, turn) and 2 framings (close, wide)" in said and "repeat" in said
    full = {"actions": {n: {"does": n} for n in ("walk", "run", "crawl", "dig", "carry", "fight", "fall", "talk")},
            "shots": ["wide", "medium:<id>", "close:<id>", "ground"]}
    assert a2d.vocabulary_gaps(full) == "" and a2d.vocabulary_gaps({}) == ""
    assert a2d.probe_words(["round-1/shot_0.jpg", "inside_0.jpg", "era_0.jpg"]) == \
        "in an underground bunker room, then with that crowd in 1815"


def test_a_stretch_that_loops_is_planned_again(home):
    found = {"notes": [], "seen": [{"at": 12.0, "kind": "REPEAT", "fix": "director",
                                    "what": "the same wide walk as two moments before"}]}
    fix, faults = a2d.replan_for(found, {"plates": {"draw": True}})
    assert fix and "repeat: the same wide walk" in fix[0] and faults == []
    era = {"notes": [], "seen": [{"at": 3.0, "kind": "ERA", "fix": "renderer", "what": "modern helmets in 1815"}]}
    assert a2d.replan_for(era, {}) == ([], []), "the renderer's own fault is said after the video, never re-planned"
    review = (home / a2d.KIT / "review.txt").read_text(encoding="utf-8")
    assert "- ERA:" in review and "- PROP:" in review and "- REPEAT:" in review


def test_the_creators_brief_reaches_the_renderer_without_changing_the_builds_key(home, tmp_path):
    refs = _frames(tmp_path / "refs")
    seen = []

    def see(prompt, images):
        seen.append(prompt)
        return "I can't"
    a2d.build(tmp_path / "op", {}, {}, see=see, judge=lambda r, c: {}, references=refs, home=home, rounds=1,
              log=lambda m: None, brief="Recurring ground-level battle shots with detailed units")
    assert "Recurring ground-level battle shots with detailed units" in seen[0] and "[BRIEF]" not in seen[0]
    again = a2d.build(tmp_path / "op", {}, {}, see=lambda p, i: pytest.fail("never asked twice"), judge=lambda r, c: {},
                      references=refs, home=home, rounds=1, log=lambda m: None)
    assert again["state"] == "failed", "the same build, found by its key"
    for kit in (a2d.KIT, a2d.KIT3D) if (home / a2d.KIT3D).is_dir() else (a2d.KIT,):
        guide = (home / kit / "AUTHORING.md").read_text(encoding="utf-8")
        assert "**Any time.**" in guide and "**Props from their words.**" in guide and "**A whole video's range.**" in guide
    assert set(a2d.earlier("cast.txt", home)) >= {(home / a2d.KIT / "cast-110.txt").read_text(encoding="utf-8")}, \
        "a video cast under Studio 110 resumes without asking Claude again"


def test_the_example_claude_is_shown_meets_what_every_video_needs(home, tmp_path):
    """The example renderer Claude learns the contract from draws any place, a crowd, typed parameters and words that
    fit their box: Studio's probes find nothing to send back."""
    code = (home / a2d.KIT / a2d.EXAMPLE_OWN).read_text(encoding="utf-8")
    scene = {"duration": 6, "cast": {"pip": {"name": "Pip", "color": "#f0c040", "hat": True}},
             "sets": {"park": {"prompt": "a park", "sky": ["#9fd0f0", "#e8f4fb"], "floor_colors": ["#5aa060", "#4a9050"]}},
             "shots": [{"start": 0, "end": 6, "set": "park", "framing": "wide", "cast": ["pip"]}], "stills": [2.0],
             "actions": [{"at": 0, "who": "pip", "move": "place", "x": -2, "face": "right"},
                         {"at": 0.5, "who": "pip", "move": "board", "title": "412 meals", "note": "30 days",
                          "show": {"kind": "line", "trend": "down"}}],
             "fx": [{"kind": "day", "at": 0, "day": 1}]}
    man = _chromium(lambda: a2d.manifest(code, home))
    got = _chromium(lambda: a2d.probe(scene, {}, code, tmp_path / "probe", home=home, man=man,
                                      channel={"cast": [{"name": "Pip", "look": "a yellow round-headed figure", "share": 0.9}]},
                                      stress={**scene, "width": 1920, "height": 1080}))
    assert got["problems"] == [] and got["issues"] == []
    assert a2d.untyped(man) == [] and a2d.recurring(man) == {"pip": {"name": "Pip", "color": "#f0c040", "hat": True}}


def test_the_frames_claude_sees_span_the_channels_videos():
    import cr_style_clone
    frames = [Path(f"{v}_{k}.jpg") for v in ("aaa", "_b_c", "ddd") for k in range(4)]
    assert [p.stem for p in cr_style_clone._by_video(frames)[:4]] == ["aaa_0", "_b_c_0", "ddd_0", "aaa_1"]


def test_the_renderer_is_saved_versioned_with_the_style_and_never_used_changed(home, tmp_path):
    folder = tmp_path / "op"
    (folder / "renderer").mkdir(parents=True)
    (folder / "renderer" / "renderer.js").write_text(BEAN, encoding="utf-8")
    digest = hashlib.sha256(BEAN.encode()).hexdigest()
    (folder / "renderer" / "build.json").write_text(json.dumps({"state": "below_bar", "sha256": digest, "overall": 5,
                                                                "bar": 6.5, "manifest": {"name": "bean-shorts"}}))
    style = a2d.attach({"name": "cr_clone_beans", "look": {"visuals": "cr_animated2d"}}, folder, home)
    saved = style["look"]["animated2d"]["renderer"]
    assert saved["file"] == "styles/learned/cr_clone_beans/renderer-v1.js" and saved["sha256"] == digest
    assert saved["state"] == "below_bar" and saved["version"] == 1
    assert "$0.02" in a2d.cost_note(style), "a renderer that takes pictures of its places says what they cost"
    assert a2d.cost_note({"look": {"animated2d": {"renderer": {"manifest": {"plates": {"draw": False}}}}}}) \
        .startswith("Your voice only"), "one that draws every place costs only its voice"
    again = a2d.attach({"name": "cr_clone_beans", "look": {}}, folder, home)
    assert again["look"]["animated2d"]["renderer"]["version"] == 1, "the same code is the same version"
    code, man = a2d.renderer_for(style, home)
    assert code == BEAN and man["name"] == "bean-shorts"
    assert a2d.renderer_problem(style, home) == ""
    (home / "assets" / saved["file"]).write_text(BEAN + "// edited", encoding="utf-8")
    assert "changed on disk" in a2d.renderer_problem(style, home)
    with pytest.raises(a2d.AnimationStopped, match="changed on disk"):
        a2d.renderer_for(style, home)
    failed = {"look": {"visuals": "cr_animated2d", "animated2d": {"renderer": {"state": "failed", "why": "x"}}}}
    assert "Use pictures instead" in a2d.renderer_problem(failed, home)


def test_a_renderer_saved_on_windows_is_not_changed_on_disk(kit, tmp_path):
    """The owner's Windows test (2026-10-26): before 109, Windows wrote a saved renderer with \\r\\n line ends and the
    readiness check hashed its bytes, so every coded style read "changed on disk" right after its study. Needs no
    private kit or browser, so Windows CI runs it."""
    home = tmp_path / "studio"
    code = BEAN
    assert "\n" in code
    digest = hashlib.sha256(code.encode()).hexdigest()
    folder = tmp_path / "op"
    (folder / "renderer").mkdir(parents=True)
    (folder / "renderer" / "renderer.js").write_text(code, encoding="utf-8", newline="")
    (folder / "renderer" / "build.json").write_text(json.dumps({"state": "passed", "sha256": digest,
                                                                "manifest": {"name": "bean-shorts"}}))
    style = a2d.attach({"name": "cr_clone_winbeans", "look": {"visuals": "cr_animated2d"}}, folder, home)
    saved = home / "assets" / style["look"]["animated2d"]["renderer"]["file"]
    assert saved.read_bytes() == code.encode(), "saved exactly as written, on every computer"
    saved.write_bytes(code.replace("\n", "\r\n").encode())          # as a Studio before 109 saved it on Windows
    assert a2d.renderer_problem(style, home) == ""
    assert a2d.renderer_for(style, home)[0] == code
    again = a2d.attach({"name": "cr_clone_winbeans", "look": {}}, folder, home)
    assert again["look"]["animated2d"]["renderer"]["version"] == 1, "the Windows copy is the same version"
    saved.write_bytes(b"\xff\xfe not text")
    assert "changed on disk" in a2d.renderer_problem(style, home)


def test_the_likeness_check_of_an_animated_style_is_its_renderers_build(tmp_path):
    import cr_style_likeness as like
    from PIL import Image
    folder = tmp_path / "op"
    (folder / "renderer" / "round-2").mkdir(parents=True)
    Image.new("RGB", (64, 36), (200, 50, 50)).save(folder / "renderer" / "round-2" / "shot_0.jpg")
    (folder / "renderer" / "build.json").write_text(json.dumps({
        "state": "passed", "round": 2, "overall": 7.2, "characters": 6.8, "bar": 6.5, "control": 8.0,
        "stills": ["round-2/shot_0.jpg"], "rounds": [{"round": 2, "dealbreakers": ["thinner limbs"]}]}))
    style = {"name": "cr_clone_x", "look": {"visuals": "cr_animated2d", "animated2d": {"schema": 1},
                                             "image_model": "nano-banana-2-1", "still_suffix": " x"}}
    result = like.run(folder, {"look_id": "lk_x"}, style, lambda m: None, home=tmp_path,
                      generate=lambda *a, **k: pytest.fail("no picture is paid for"),
                      call=lambda *a, **k: pytest.fail("nothing is judged again"))
    assert result["state"] == "done" and result["verdict"] == "match" and result["cost_usd"] == 0.0
    assert result["dealbreakers"] == ["thinner limbs"] and (folder / "likeness" / "r0_0.jpg").is_file()
    assert "style_image" not in like.apply_saved(folder, json.loads(json.dumps(style)), tmp_path)["look"]


def test_an_unjudged_build_is_a_skipped_likeness_check_never_a_score(tmp_path):
    import cr_style_likeness as like
    folder = tmp_path / "op"
    (folder / "renderer").mkdir(parents=True)
    (folder / "renderer" / "build.json").write_text(json.dumps({
        "state": "unjudged", "round": 2, "overall": None, "bar": 5.5,
        "why": "the likeness check couldn't run: You've used today's style checks. Try again tomorrow."}))
    style = {"name": "cr_clone_x", "look": {"visuals": "cr_animated2d", "animated2d": {"schema": 1}}}
    result = like.run(folder, {"look_id": "lk_x"}, style, lambda m: None, home=tmp_path,
                      call=lambda *a, **k: pytest.fail("nothing is judged again"))
    assert result["state"] == "skipped" and "style checks for today were used up" in result["why"]


# ── the plugin, end to end, with a stand-in engine ──────────────────────────────────────────────────────────────

class Engine:
    MOTION_WORKERS = 1

    def __init__(self, style, look):
        self.calls, self.prompts = [], []
        self.STYLE_INFO = {style: {"look": look}}
        self.cast = {"cast": {"bean": {"name": "Bean", "color": "white", "band": {"color": "red"}, "hat": "top"}},
                     "sets": {"void": {"prompt": "a black void"}}}
        self.direction = {"shots": [{"start": 0, "framing": "wide"}],
                          "actions": [{"at": 0, "who": "bean", "move": "place", "x": 0},
                                      {"at": 0.8, "who": "bean", "move": "hop", "height": 1},
                                      {"at": 1.2, "who": "bean", "move": "punch"}]}

    def step(self, msg):
        self.calls.append(("step", msg))

    def log(self, msg):
        pass

    def _extra_block(self):
        return ""

    def claude(self, prompt, model, max_tokens=0, provider=None):
        self.prompts.append(prompt)
        return json.dumps(self.cast if "designer of a" in prompt else self.direction)

    def image_gen(self, prompt, dest, label="", ref_urls=None):
        self.calls.append(("plate", label))

    def _parse_srt_full(self, text):
        return [(0.0, 1.2, "The bean wakes up"), (1.2, 2.4, "and hops for joy")]

    def _word_clock(self, job, srt):
        return {}

    def _audio_dur(self, mp3):
        return 2.4

    def _save_motion_plan(self, job, timeline, segments):
        self.calls.append(("plan", timeline))

    def _render_astro_segments(self, timeline, job, force, white_fade=0.0, style=""):
        return [p for _, p, _, _ in timeline]

    def _concat_segments(self, rendered, out):
        self.calls.append(("concat", rendered))

    def _with_sound_design(self, job, mp3, timeline, style):
        return mp3

    def _final_mux(self, job, mp3, srt, burn_subs, **kw):
        self.calls.append(("mux", kw))

    def _keep_clean_visual(self, job, style):
        pass


def test_every_video_casts_and_directs_in_the_saved_renderers_own_vocabulary_and_draws_with_it(home, tmp_path,
                                                                                                monkeypatch):
    folder = tmp_path / "op"
    (folder / "renderer").mkdir(parents=True)
    (folder / "renderer" / "renderer.js").write_text(BEAN, encoding="utf-8")
    man = _chromium(lambda: a2d.manifest(BEAN, home))
    (folder / "renderer" / "build.json").write_text(json.dumps({"state": "passed", "overall": 7, "bar": 6.5,
        "sha256": hashlib.sha256(BEAN.encode()).hexdigest(), "manifest": man}))
    style = a2d.attach({"name": "cr_clone_beans", "look": {"visuals": "cr_animated2d", "animated2d": {}}}, folder, home)
    monkeypatch.setattr(a2d, "HERE", home)
    job = tmp_path / "job"
    job.mkdir()
    (job / "script.txt").write_text("The bean wakes up and hops for joy.", encoding="utf-8")
    (job / "title.txt").write_text("A Bean's Morning\n", encoding="utf-8")
    (job / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,200\nThe bean wakes up\n", encoding="utf-8")
    engine = Engine("cr_clone_beans", style["look"])
    import cr_claude
    monkeypatch.setattr(cr_claude, "VISUAL_MODEL", "opus", raising=False)
    a2d.prepare(engine, "The bean wakes up and hops for joy.", job, "cr_clone_beans", False)
    final = a2d.assemble(engine, job, job / "audio.mp3", job / "subs.srt", "cr_clone_beans", False, True)
    assert final == job / "video.mp4"
    assert not [c for c in engine.calls if c[0] == "plate"], "this renderer draws its own places: no picture is paid"
    casting = json.loads((job / "animated2d" / "casting.json").read_text())
    assert casting["cast"]["bean"] == {"name": "Bean", "color": "#eeeeee", "band": {"color": "#e0625a"}}
    cast_prompt, director_prompt = engine.prompts
    assert '"band"' in cast_prompt and "smooth white bean-shaped" in cast_prompt
    assert "- hop {height: a bare number}: a little hop" in director_prompt and "x from -3 to 3" in director_prompt
    film = json.loads((job / "animated2d" / "film-000.json").read_text())
    assert [a["move"] for a in film["actions"]] == ["place", "hop"], "an action the renderer lacks is dropped"
    clip = Path(next(c for c in engine.calls if c[0] == "plan")[1][0][1])
    assert clip.is_file() and clip.stat().st_size > 10000
    assert json.loads(clip.with_suffix(".sfx.json").read_text()) == [[0.8, "file:sfx/animated2d/jump.mp3", 0.0]]
    assert json.loads((job / "animated2d" / "delivery.json").read_text())["renderer"] == "bean-shorts"
    # the frame review before the real render: a renderer that leaves the screen empty stops the video unpublished
    monkeypatch.setattr(a2d, "blank_runs", lambda samples, duration, step=0.25: [(0.5, 4.0)])
    with pytest.raises(a2d.AnimationStopped, match="would show nothing from 0:00 to 0:04.*Nothing was drawn"):
        a2d.assemble(engine, job, job / "audio.mp3", job / "subs.srt", "cr_clone_beans", True, True)


def _bean_video(home, tmp_path, monkeypatch, places=None, seen=None):
    """A saved BEAN style (places "any": it passed the build's any-place probe) and a 2.4 s video of it, its engine
    answering Claude's frame look with `seen`."""
    folder = tmp_path / "op"
    (folder / "renderer").mkdir(parents=True)
    (folder / "renderer" / "renderer.js").write_text(BEAN, encoding="utf-8")
    man = _chromium(lambda: a2d.manifest(BEAN, home))
    if places:
        man["places"] = places
    (folder / "renderer" / "build.json").write_text(json.dumps({"state": "passed", "overall": 7, "bar": 6.5,
        "sha256": hashlib.sha256(BEAN.encode()).hexdigest(), "manifest": man}))
    style = a2d.attach({"name": "cr_clone_beans", "look": {"visuals": "cr_animated2d", "animated2d": {}}}, folder, home)
    monkeypatch.setattr(a2d, "HERE", home)
    job = tmp_path / "job"
    job.mkdir()
    (job / "script.txt").write_text("The bean wakes up and hops for joy.", encoding="utf-8")
    (job / "title.txt").write_text("A Bean's Morning\n", encoding="utf-8")
    (job / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,200\nThe bean wakes up\n", encoding="utf-8")
    engine = Engine("cr_clone_beans", style["look"])
    engine.looked = []

    def claude_vision(prompt, images, model=None, max_tokens=2000):
        engine.looked.append((prompt, images))
        return json.dumps({"problems": seen or []})
    engine.claude_vision = claude_vision
    import cr_claude
    monkeypatch.setattr(cr_claude, "VISUAL_MODEL", "opus", raising=False)
    return style, job, engine


WRONG_PLACE = {"moment": 1, "kind": "PLACE", "fix": "director", "what": "a void, not the kitchen"}
STRAY_BAND = {"moment": 1, "kind": "STRAY", "fix": "renderer", "what": "a red band floats"}


def test_each_stretch_is_looked_at_before_it_is_drawn_and_re_planned_once(home, tmp_path, monkeypatch):
    """Nothing judged the bunker video's frames. Claude now looks at a few frames of each stretch before it is drawn
    (Claude usage only): what the director can clearly fix is re-planned once; what only the renderer draws wrong is said."""
    style, job, engine = _bean_video(home, tmp_path, monkeypatch, places="any", seen=[WRONG_PLACE, STRAY_BAND])
    a2d.assemble(engine, job, job / "audio.mp3", job / "subs.srt", "cr_clone_beans", False, True)
    looked = engine.looked
    assert len(looked) == 1 and len(looked[0][1]) == 1 and Path(looked[0][1][0]).is_file()
    assert 'shot in "void"' in looked[0][0] and "The bean wakes up" in looked[0][0]
    fix_prompt = engine.prompts[-1]
    assert len(engine.prompts) == 3 and "A CHECK OF YOUR PLAN" in fix_prompt and "place: a void, not the kitchen" in fix_prompt
    assert "red band" not in fix_prompt, "what only the renderer draws wrong is not the director's to fix"
    review = json.loads((job / "animated2d" / "delivery.json").read_text())["review"]
    assert review[0]["replanned"] and review[0]["renderer"] == ["1.2 s: stray: a red band floats"]
    assert any("Improve this style" in m for k, m in engine.calls if k == "step")
    again = Engine("cr_clone_beans", style["look"])
    again.claude_vision = lambda *a, **k: pytest.fail("a stretch is looked at once")
    a2d.assemble(again, job, job / "audio.mp3", job / "subs.srt", "cr_clone_beans", False, True)
    assert again.prompts == [], "a resumed video asks Claude nothing again"


def test_a_wrong_place_a_renderer_cant_draw_or_a_minor_finding_is_never_re_planned(home, tmp_path, monkeypatch):
    """The coordinator's review: a renderer written before 109 draws only its own places, so a wrong-place re-plan
    buys nothing (it is said as the renderer's to fix); a finding that isn't clear (a still moment) isn't re-planned."""
    _, job, engine = _bean_video(home, tmp_path, monkeypatch, seen=[WRONG_PLACE])
    a2d.assemble(engine, job, job / "audio.mp3", job / "subs.srt", "cr_clone_beans", False, True)
    assert len(engine.prompts) == 2 and len(engine.looked) == 1, "looked at, never re-planned"
    review = json.loads((job / "animated2d" / "delivery.json").read_text())["review"][0]
    assert not review["replanned"] and "draws only the places it was written with" in review["renderer"][0]
    _, job2, engine2 = _bean_video(home, tmp_path / "b", monkeypatch, places="any",
                                   seen=[{"moment": 1, "kind": "STILL", "fix": "director", "what": "a still moment"}])
    a2d.assemble(engine2, job2, job2 / "audio.mp3", job2 / "subs.srt", "cr_clone_beans", False, True)
    assert len(engine2.prompts) == 2, "a still moment alone isn't worth a re-plan"


def test_the_frame_check_can_be_switched_off_for_studio_or_for_one_video(home, tmp_path, monkeypatch):
    style, job, engine = _bean_video(home, tmp_path, monkeypatch, places="any", seen=[WRONG_PLACE])
    assert a2d.frame_check_default(home) is True
    a2d.set_frame_check(home, False)
    assert json.loads((home / a2d.PREFS).read_text()) == {"animation_frame_check": False}
    a2d.assemble(engine, job, job / "audio.mp3", job / "subs.srt", "cr_clone_beans", False, True)
    assert engine.looked == [] and len(engine.prompts) == 2, "off: no look, no re-plan, the free checks still run"
    assert json.loads((job / "animated2d" / "review-000.json").read_text())["checked"] is False
    a2d.set_frame_check(home, True)
    (job / "run_spec.json").write_text(json.dumps({"cr_frame_check": "off"}), encoding="utf-8")
    assert a2d.frame_check_for(job, home) is False, "this video's own choice wins over Studio's setting"
    (job / "run_spec.json").write_text(json.dumps({}), encoding="utf-8")
    assert a2d.frame_check_for(job, home) is False, "and is kept for its Resumes"
    import cr_studio_tools as tools
    row = {"name": "cr_clone_beans", "label": "Beans", "frame_check": {"on": True, "per_minute": [1.0, 2.5]}}
    assert tools.video_payload(row, ["A"], 3, {}, {"frame_check": False})["frame_check"] is False
    assert "frame_check" not in tools.video_payload({**row, "frame_check": None}, ["A"], 3, {}, {"frame_check": False})


def test_an_unreadable_re_plan_keeps_the_plan_that_passed_the_checks(home, tmp_path, monkeypatch):
    """The coordinator's review: a malformed re-plan answer used to stop the video for good (its saved reply made every
    Resume stop the same way)."""
    style, job, engine = _bean_video(home, tmp_path, monkeypatch, places="any", seen=[WRONG_PLACE])
    asked = engine.claude
    engine.claude = lambda prompt, model, max_tokens=0, provider=None: (
        "Sorry, I can't." if "A CHECK OF YOUR PLAN" in prompt else asked(prompt, model, max_tokens, provider))
    final = a2d.assemble(engine, job, job / "audio.mp3", job / "subs.srt", "cr_clone_beans", False, True)
    assert final == job / "video.mp4"
    rec = json.loads((job / "animated2d" / "review-000.json").read_text())
    assert rec["final"] == "first" and "incomplete" in rec["fix_refused"]
    again = Engine("cr_clone_beans", style["look"])
    again.claude_vision = lambda *a, **k: pytest.fail("never looked at twice")
    a2d.assemble(again, job, job / "audio.mp3", job / "subs.srt", "cr_clone_beans", False, True)
    assert again.prompts == [], "a Resume keeps the checked plan and asks nothing"


def test_a_video_stopped_under_an_earlier_studio_reuses_its_cast_places_pictures_and_directions(home, tmp_path,
                                                                                                monkeypatch):
    """REQUIRED by the coordinator: a video stopped under 108 and resumed under 109 re-cast (cast.txt is part of its
    key) and paid for up to 6 new background pictures. The kit keeps the earlier prompts (cast-108.txt,
    director-108.txt) so a Resume finds its saved answers."""
    plated = BEAN.replace("plates: { draw: false }", "plates: { prompt: \"flat\" }")
    folder = tmp_path / "op"
    (folder / "renderer").mkdir(parents=True)
    (folder / "renderer" / "renderer.js").write_text(plated, encoding="utf-8")
    man = _chromium(lambda: a2d.manifest(plated, home))
    (folder / "renderer" / "build.json").write_text(json.dumps({"state": "passed", "sha256": hashlib.sha256(
        plated.encode()).hexdigest(), "manifest": man}))
    style = a2d.attach({"name": "cr_clone_plates", "look": {"visuals": "cr_animated2d", "animated2d": {}}}, folder, home)
    monkeypatch.setattr(a2d, "HERE", home)
    (home / a2d.KIT / "cast-107.txt").write_text("THE CASTING PROMPT OF AN EARLIER STUDIO", encoding="utf-8")
    (home / a2d.KIT / "director-107.txt").write_text("THE DIRECTOR PROMPT OF AN EARLIER STUDIO", encoding="utf-8")
    job = tmp_path / "job"
    (job / "animated2d" / "plates").mkdir(parents=True)
    script = "The bean wakes up and hops for joy."
    engine = Engine("cr_clone_plates", style["look"])
    code, pinned = a2d._style_renderer(engine, "cr_clone_plates", job)
    spec = a2d._spec(engine, "cr_clone_plates")
    # what the earlier Studio saved: its casting (keyed with its own cast.txt) and the picture of its one place
    cast_spec = pinned.get("cast") or {}
    asked = {"script": script, "prompt": "THE CASTING PROMPT OF AN EARLIER STUDIO", "brief": "", "cast": cast_spec,
             "spec": {k: v for k, v in spec.items() if k != "renderer"}}
    casting = {"key": a2d._digest(asked), "cast": {"bean": {"name": "Bean", "color": "#eeeeee"}},
               "sets": {"kitchen": {"prompt": "a kitchen"}}}
    (job / "animated2d" / "casting.json").write_text(json.dumps(casting), encoding="utf-8")
    from PIL import Image
    plate = job / "animated2d" / "plates" / f"kitchen-{a2d._digest(a2d.plate_prompt(casting['sets']['kitchen'], spec, pinned))[:10]}.jpg"
    Image.effect_noise((320, 180), 60).convert("RGB").save(plate, quality=95)
    assert plate.stat().st_size > 5000
    engine.claude = lambda *a, **k: pytest.fail("a Resume after an update never casts again")
    a2d.prepare(engine, script, job, "cr_clone_plates", False)
    assert not [c for c in engine.calls if c[0] == "plate"], "nothing paid again: its picture is reused"
    # a stretch it directed: found by the earlier director.txt (no examples in its docs then)
    part = (0.0, 2.4, [(0.0, 1.2, "The bean wakes up", [("The", 0.0), ("bean", 0.3), ("wakes", 0.6), ("up", 0.9)])])
    beats = a2d.beats_text(part[2])
    docs = {"actions": pinned.get("actions") or {}, "shots": pinned.get("shots") or [], "fx": pinned.get("fx") or {},
            "notes": pinned.get("notes") or ""}
    key = a2d._digest({"beats": beats, "cast": casting["cast"], "sets": casting["sets"], "spec": spec,
                       "prompt": "THE DIRECTOR PROMPT OF AN EARLIER STUDIO", "previous": "", "t": [0.0, 2.4], "docs": docs})
    (job / "animated2d" / "director-000.json").write_text(json.dumps({"key": key}), encoding="utf-8")
    (job / "animated2d" / "director-000.txt").write_text(json.dumps(engine.direction), encoding="utf-8")
    plan = a2d.direct(engine, job, casting, spec, part, 0, "", "A Bean's Morning", pinned)
    assert [a["move"] for a in plan["actions"]] == ["place", "hop"]


def test_a_studio_before_109_runs_every_renderer_with_the_new_kit(home, monkeypatch):
    """The coordinator's review: old code with the new kit stopped every coded style ("STAGE is not defined"). A
    Studio before 109 doesn't load stage.js; its renderers draw as before."""
    monkeypatch.setattr(a2d, "KIT_FILES", tuple(f for f in a2d.KIT_FILES if f != "stage.js"))
    stills = _chromium(lambda: a2d.render_stills({**BEAN_SCENE, "stills": [1.3]}, home / "old", BEAN, home=home))
    assert stills and not a2d._blank(stills[0])


# Studio 108's Kit.caption (the 3D kit's, line for line): renderers saved before 109 must draw exactly as they did
CAPTION_108 = r"""
function caption108(ctx, text, o = {}) {
  if (!text) return;
  const W = ctx.canvas.width, H = ctx.canvas.height, size = (o.size || 0.07) * Math.min(W, H) * (H > W ? 1.25 : 1);
  ctx.save();
  ctx.font = `${o.weight || 900} ${size}px ${o.font || 'Arial Black, Arial, sans-serif'}`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
  const x = W * (o.x ?? 0.5), y = H * (o.y ?? 0.7), s = o.upper === false ? text : text.toUpperCase();
  ctx.lineWidth = size * (o.stroke ?? 0.16); ctx.strokeStyle = o.strokeColor || '#000000'; ctx.strokeText(s, x, y);
  ctx.fillStyle = o.color || '#ffffff'; ctx.fillText(s, x, y);
  ctx.restore();
}
"""
# the calls of a real 3D clone (the gneef renderer v5): word captions at the bottom, popups moved and scaled
CAPTION_CALLS = r"""
  const W = ctx.canvas.width, H = ctx.canvas.height;
  ctx.fillStyle = "#4a6a8a"; ctx.fillRect(0, 0, W, H);
  if (t < 1) cap(ctx, "growth", { y: 0.7, size: 0.064, weight: 900, font: "Arial, Helvetica, sans-serif", upper: false, stroke: 0.2 });
  else if (t < 2) { ctx.save(); ctx.translate(W / 2, H * 0.2); ctx.scale(1.08, 1.08);
    cap(ctx, "10 lies = stone leg", { x: 0, y: 0, size: 0.06, color: "#ffe14a", upper: true }); ctx.restore(); }
  else cap(ctx, "hello there", {});
"""


def _caption_renderer(use_kit: bool) -> str:
    return (CAPTION_108 + "Kit.renderer({ name: 'captions', version: 1, manifest: { actions: { place: '{x}' } }, "
            "plates: { draw: false }, frame(ctx, t, s, Kit) { const cap = " + ("Kit.caption" if use_kit else "caption108")
            + ";\n" + CAPTION_CALLS + "} });")


def _same(a, b) -> bool:
    from PIL import Image, ImageChops
    return ImageChops.difference(Image.open(a).convert("RGB"), Image.open(b).convert("RGB")).getbbox() is None


def test_kit_caption_draws_exactly_as_studio_108_did(home, tmp_path):
    """The coordinator's BLOCKER: the 109 kit's Kit.caption clipped a real 3D clone's popups off the frame ("10 LIES =
    STO..."). It draws as 108 did; only a line wider than an untransformed frame is shrunk, and stroke 0 is no outline."""
    scene = {"duration": 3, "width": 1920, "height": 1080, "cast": {}, "sets": {"s": {}}, "shots": [{"start": 0}],
             "actions": [], "stills": [0.5, 1.5, 2.5]}
    new = _chromium(lambda: a2d.render_stills(scene, tmp_path / "new", _caption_renderer(True), home=home))
    old = _chromium(lambda: a2d.render_stills(scene, tmp_path / "old", _caption_renderer(False), home=home))
    assert [_same(a, b) for a, b in zip(new, old)] == [True, True, True]
    wide = {**scene, "stills": [0.5]}
    long = _caption_renderer(True).replace('"growth"', '"' + "a very long line of words " * 6 + '"')
    words = {}
    look = {}
    _chromium(lambda: a2d.sweep({**wide, "duration": 1}, long, home=home, text=words, look=look))
    assert not [r for r in look["texts"] if r["kind"] == "off"], "a line wider than the frame fits it"


def test_a_style_from_before_renderers_keeps_the_kits_stick_fights(home, monkeypatch):
    monkeypatch.setattr(a2d, "HERE", home)
    code, man = _chromium(lambda: a2d.renderer_for({"look": {"visuals": "cr_animated2d", "animated2d": {}}}))
    assert code == a2d.example(home) and man["name"] == "stick-fights" and "uppercut" in man["actions"]


def test_a_look_with_one_reference_borrows_the_studys_own_frames_to_hold_some_out(tmp_path, monkeypatch):
    """One reference gives the look only 4 frames, too few to hold any out: the study's own frames of it fill the set,
    so the bar is still set by the channel's own frames (never a fixed number)."""
    from PIL import Image
    import cr_style_clone
    import cr_style_likeness
    folder = tmp_path / "home" / "output" / "cr-style-clone" / ("op_" + "a" * 24)
    look = _frames(tmp_path / "look", 4)
    watched = folder / "reflearn" / "ref_frames"
    watched.mkdir(parents=True)
    for k in range(12):
        im = Image.new("RGB", (480, 270), (0, 0, 0))
        if k not in (0, 3):                                 # k00 and k03: black frames between shots
            im.paste((200, 200, 190), (100 + k * 10, 80, 220 + k * 10, 230))
        im.save(watched / f"k{k:02d}.jpg")
    got = {}
    monkeypatch.setattr(cr_style_likeness, "frames", lambda home, look_id: look)
    monkeypatch.setattr(cr_style_likeness, "flat_graphic", lambda p: False)
    monkeypatch.setattr(a2d, "build", lambda folder, spec, channel, **kw: got.update(kw) or {"state": "passed"})
    cr_style_clone._renderer(folder, {"look_id": "lk_x", "answers": {}, "reference_url": "https://www.youtube.com/"
                                      "watch?v=8atK0pF4oJY"}, {"profile": dart(), "references": [
                                          {"channel": "Day One Films"}]}, see=None, log=lambda m: None)
    refs = [Path(p).name for p in got["references"]]
    assert refs[:4] == [p.name for p in look] and len(refs) == 9 and "k00.jpg" not in refs and "k03.jpg" not in refs
    assert got["names"] == ["Day One Films"]


# ── the 107 review: never drawn with anything else, nothing written outside, nothing sent out ────────────────────

def test_a_style_studied_without_a_renderer_keeps_the_way_it_was_approved(kit):
    """The 107 review's blocker: Save completed style and Improve graphics plan a finished study again. One approved
    before 107 wrote no renderer and has no `made` answer: planned as code, a documentary became stick figures."""
    drawn = tsm.profile({"generated": 0.9, "graphic": 0.1})
    assert cm.plan_style(drawn, tsm.STYLES)["route"].get("coded")
    kept = cm.plan_style(drawn, tsm.STYLES, code=False)
    assert kept["route"]["engine"] == "pictures" and kept["overrides"]["look"].get("visuals") not in a2d.PLUGINS
    stick = cm.plan_style(dart(), tsm.STYLES, code=False)
    assert stick["route"]["engine"] == "animated2d" and not stick["route"].get("coded"), \
        "a stick-fight channel from before 104 is still animated the way it was approved"
    other = cm.plan_style(dart(), tsm.STYLES, code=False, answers={"made": cm.MADE_OTHER})
    assert other["route"]["engine"] == "pictures", "pictures chosen instead of code stay chosen"


def test_a_style_written_in_code_is_never_drawn_with_the_kits_stick_fights(tmp_path, monkeypatch):
    coded = {"look": {"visuals": a2d.VISUALS, "animated2d": {}},
             "cr_style_clone": {"learned_v2": {"route": {"engine": "animated2d", "coded": True}}}}
    with pytest.raises(a2d.AnimationStopped, match="no renderer saved"):
        a2d.renderer_for(coded, tmp_path)
    monkeypatch.setattr(a2d, "available", lambda home=None: True)
    assert "no renderer saved" in a2d.renderer_problem(coded, tmp_path)
    missing = {"look": {"visuals": a2d.VISUALS,
                        "animated2d": {"renderer": {"state": "missing", "why": "the study wrote no working renderer"}}}}
    with pytest.raises(a2d.AnimationStopped, match="no working renderer"):
        a2d.renderer_for(missing, tmp_path)
    assert "couldn't write a working animation" in a2d.renderer_problem(missing, tmp_path)
    assert a2d.renderer_problem({"look": {"animated2d": {}}}, tmp_path) == "", "a style from before 104 keeps its kit"


def test_an_improved_copy_keeps_its_sources_renderer_as_last_saved(tmp_path):
    code = "Kit.renderer({name: 'mine', frame() {}});"
    digest = hashlib.sha256(code.encode()).hexdigest()
    source = tmp_path / "assets" / "styles" / "learned" / "cr_clone_src"
    source.mkdir(parents=True)
    (source / "renderer-v3.js").write_text(code)
    record = {"file": "styles/learned/cr_clone_src/renderer-v3.js", "sha256": digest, "version": 3, "state": "passed",
              "from": "style_lab", "manifest": {"name": "mine"}}
    style = a2d.carry({"name": "cr_clone_new", "look": {"animated2d": {}}}, record, tmp_path)
    got = style["look"]["animated2d"]["renderer"]
    assert got["file"] == "styles/learned/cr_clone_new/renderer-v1.js" and got["sha256"] == digest
    assert got["carried_from"] == record["file"] and got["state"] == "passed"
    assert a2d.renderer_for(style, tmp_path) == (code, {"name": "mine"}), "its own copy: the source may be deleted"
    failed = a2d.carry({"name": "cr_clone_new2", "look": {}}, {"state": "failed", "why": "no draft drew"}, tmp_path)
    assert failed["look"]["animated2d"]["renderer"] == {"state": "failed", "why": "no draft drew"}
    (source / "renderer-v3.js").write_text(code + "// changed")
    with pytest.raises(a2d.AnimationStopped, match="missing or changed"):
        a2d.carry({"name": "cr_clone_new3"}, record, tmp_path)


def test_a_video_keeps_the_renderer_it_started_with(tmp_path, monkeypatch):
    """The 107 review: a Style Lab save in the middle of a video (or before resume_video) switched its renderer under
    the cast it had designed, and a changed plates.prompt drew its quoted backdrops again."""
    from types import SimpleNamespace
    monkeypatch.setattr(a2d, "HERE", tmp_path)
    folder = tmp_path / "assets" / "styles" / "learned" / "cr_clone_x"
    folder.mkdir(parents=True)

    def version(n):
        code = f"Kit.renderer({{name: 'x', version: {n}, frame() {{}}}});"
        (folder / f"renderer-v{n}.js").write_text(code)
        return code, {"file": f"styles/learned/cr_clone_x/renderer-v{n}.js", "version": n, "state": "passed",
                      "sha256": hashlib.sha256(code.encode()).hexdigest(), "manifest": {"name": "x", "version": n}}
    first, record = version(1)
    look = {"visuals": a2d.VISUALS, "animated2d": {"renderer": record}}
    engine = SimpleNamespace(STYLE_INFO={"cr_clone_x": {"look": look}}, _extra_block=lambda: "", step=lambda m: None)
    job = tmp_path / "job"
    assert a2d._style_renderer(engine, "cr_clone_x", job) == (first, record["manifest"])
    monkeypatch.setattr(a2d, "_prompt", lambda name, home=None: "cast [SCRIPT]")
    asked = []
    engine.claude = lambda prompt, model, **kw: asked.append(prompt) or json.dumps(
        {"cast": {"bean": {"name": "Bean", "color": "#ff0000"}}, "sets": {"street": {"prompt": "a street"}}})
    cast = a2d.cast_and_sets(engine, "a script", job, "cr_clone_x", "t", record["manifest"])
    second, look["animated2d"]["renderer"] = version(2)              # Style Lab saves a new version mid-video
    assert a2d._style_renderer(engine, "cr_clone_x", job)[0] == first, "the version the video started with"
    assert a2d.cast_and_sets(engine, "a script", job, "cr_clone_x", "t", record["manifest"]) == cast and len(asked) == 1
    assert a2d._style_renderer(engine, "cr_clone_x", tmp_path / "next")[0] == second, "new videos use the new one"
    (job / "animated2d" / "renderer.js").write_text(first + "//")
    with pytest.raises(a2d.AnimationStopped, match="changed on disk"):
        a2d._style_renderer(engine, "cr_clone_x", job)


def test_a_renderers_sounds_are_checked_again_before_anything_is_written(tmp_path, monkeypatch):
    """The 107 review: the page's answer is the renderer's to shape (it can replace Object.entries or RegExp before
    the page's own checks run), so Python checks every name, type and size again, and writes only inside out_dir."""
    import base64
    from types import SimpleNamespace
    pcm = base64.b64encode(b"\x00\x10" * 4410).decode()
    made = {"thud": {"pcm": pcm, "rate": 44100, "peak": 0.5},
            "../../escape": {"pcm": pcm, "rate": 44100, "peak": 0.5},
            "/tmp/absolute": {"pcm": pcm, "rate": 44100, "peak": 0.5},
            "Loud": {"pcm": pcm, "rate": 44100, "peak": 0.5},
            "long": {"pcm": base64.b64encode(b"\x00\x10" * int(44100 * 4.2)).decode(), "rate": 44100, "peak": 0.5},
            "rate": {"pcm": pcm, "rate": "44100", "peak": 0.5},
            "junk": {"pcm": "not base64!", "rate": 44100, "peak": 0.5},
            "row": "not a sound"}

    class Box:
        def __init__(self, *a, **k):
            self.page = SimpleNamespace(evaluate=lambda script: made)

        def error(self):
            return ""

        def close(self):
            pass
    monkeypatch.setattr(a2d, "_Sandbox", Box)
    monkeypatch.setattr(a2d, "page", lambda *a, **k: "")
    out = tmp_path / "a" / "b" / "out"
    files, problems = a2d.make_sounds("code", out)
    assert set(files) == {"thud"} and [p.name for p in out.iterdir()] == ["thud.wav"]
    assert [p.name for p in tmp_path.rglob("*.wav")] == ["thud.wav"], "nothing written anywhere else"
    assert len(problems) == len(made) - 1 and any("longer than 4 s" in p for p in problems)


def test_a_renderer_has_no_webrtc_or_name_lookup_way_out(home):
    """The 107 review: requests are refused, but WebRTC (UDP to a STUN server) and DNS lookups are side channels a
    renderer could use. The sandbox's browser sends no UDP and looks up no names."""
    assert set(a2d.NO_NET_ARGS) == {"--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                                    "--host-resolver-rules=MAP * ~NOTFOUND"}
    sneaky = (r"""window.__cands = null;
(async () => {
  try {
    const pc = new RTCPeerConnection({iceServers: [{urls: "stun:127.0.0.1:9"}]}), got = [];
    pc.createDataChannel("x");
    pc.onicecandidate = (e) => { if (e.candidate && e.candidate.candidate) got.push(e.candidate.candidate); };
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise((r) => setTimeout(r, 1500));
    window.__cands = got;
  } catch (e) { window.__cands = ["refused: " + e]; }
})();
""" + BEAN)

    def run():
        box = a2d._Sandbox(a2d.page({**BEAN_SCENE, "width": 640, "height": 360}, sneaky, home), 640, 360)
        try:
            for _ in range(75):
                cands = box.page.evaluate("window.__cands")
                if cands is not None:
                    return cands
                time.sleep(0.2)
            return ["no answer"]
        finally:
            box.close()
    cands = _chromium(run)
    assert not any("typ" in c for c in cands), cands
