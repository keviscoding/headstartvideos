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
    actions: { place: "{x, face}", hop: "{height}: a little hop", lean: "leans in" },
    shots: ["wide", "close"], fx: { glow: "{dur}: a red glow" }, notes: "x from -3 to 3",
  },
  plates: { draw: false },
  prepare(scene, Kit) {
    for (const a of scene.actions) if (a.move === "hop") Kit.sound(a.at, "jump", 0);
    const bg = document.createElement("canvas"); bg.width = 1920; bg.height = 1080;
    const g = bg.getContext("2d"); g.fillStyle = "#101010"; g.fillRect(0, 0, 1920, 1080);
    return { bg, scene };
  },
  frame(ctx, t, s, Kit) {
    ctx.drawImage(s.bg, 0, 0);
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
    assert "minutes of Claude usage" in question["ask"] and question["options"][0] == cm.MADE_CODE
    assert not any(q["id"] == "animation" for q in plan["questions"]), "one question decides how videos are made"
    route = plan["route"]
    assert route["engine"] == "animated2d" and "medium_gap" not in route
    assert "Claude Code writes this channel's own renderer" in route["why"] and "$0.02" in route["why"]
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
                 "example-own.js", "cast.txt", "director.txt", "build.txt", "AUTHORING.md"):
        (kit_dir / name).write_text("x", encoding="utf-8")
    (kit_dir / "sounds" / "punch.mp3").write_bytes(b"mp3")
    shipped = {dest for _, dest in builder.private_animated2d_resources(tmp_path)}
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
                                            BEAN.replace("ctx.drawImage(s.bg, 0, 0);", "throw new Error('boom');"),
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
    assert "band sits too low" in seen[2][0] and len(seen[2][1]) == 5 + 3, "the judge's words and the last shots go back"
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
    assert "- hop: {height}: a little hop" in director_prompt and "x from -3 to 3" in director_prompt
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
