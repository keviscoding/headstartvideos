"""Animated 2D characters (cr_animated2d): a channel's stick-figure cast rigged as puppets and animated in code.

A @DartAnimates clone came out as AI stills with camera moves. Studio 101 asked and gated it; Studio 102 animates them:
the route, the plan Claude writes and how it is checked, the private kit's packaging, the likeness check's drawn test
shots, and the plugin end to end with a stand-in engine (the kit itself renders when its private files are here)."""
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
sys.path.insert(0, str(ROOT / "tests"))
import cr_animated2d as a2d  # noqa: E402
import cr_style_map as cm  # noqa: E402
import test_style_map as tsm  # noqa: E402

PRIVATE_KIT = ROOT / "data" / "frontier" / "animated2d"


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


# ── which channels it builds, and the route ─────────────────────────────────────────────────────────────────────

def test_stick_figure_channels_fit_and_their_conventions_are_read():
    ok, seen = a2d.fits(dart())
    assert ok and seen.startswith("stick figures")
    spec = a2d.from_profile(dart())
    assert spec["blur"] == 5 and spec["impact_frames"] is True
    assert [c["color"] for c in spec["cast"]] == ["#e0625a", "#3fb59e"], "colours read from the watched words"
    assert all(a2d._chroma(h) < 0.12 for h in spec["sky"] + spec["floor"]), "a fighter's red is never the sky"
    bean = tsm.animated(sources={"drawing": 0.9})
    bean["pictures"]["art_style"] = tsm.axis("Smooth white bean-shaped characters with black outlines on black.")
    assert a2d.fits(bean) == (False, "")


def test_a_dartanimates_clone_is_animated_in_code_when_the_kit_is_here(kit):
    plan = cm.plan_style(dart(), tsm.STYLES)
    assert plan["route"]["engine"] == "animated2d" and "medium_gap" not in plan["route"]
    look = plan["overrides"]["look"]
    assert look["visuals"] == a2d.VISUALS and look["animated2d"]["schema"] == a2d.SCHEMA
    assert plan["overrides"]["features"]["on"] == ["sound"] and plan["overrides"]["features"]["offered"] == ["sound"]
    assert plan["overrides"]["pacing"]["clips_per_min"] == 0.0, "no AI video is paid for"
    assert "plates" in plan["needs"] and "style_image" not in plan["needs"]
    assert plan["cost"]["plates_per_video"] == 4 and plan["cost"]["clips_per_min"] == 0
    styled = cm.apply(tsm.STYLES["stories"], plan)
    assert styled["look"]["visuals"] == "cr_animated2d"
    told = cm.plan_style(dart(), tsm.STYLES, answers={"animation": cm.ANIMATED_PICTURES})
    assert told["route"]["engine"] == "pictures" and "visuals" not in told["overrides"]["look"]


def test_drawn_characters_the_kit_cannot_rig_are_still_gated(kit):
    film = tsm.animated(0.69, {"drawing": 0.9}, narration="none")
    film["pictures"]["art_style"] = tsm.axis("Smooth white bean-shaped characters with black outlines on black.")
    route = cm.plan_style(film, tsm.STYLES)["route"]
    assert route["engine"] == "pictures" and route["medium_gap"] == cm.ANIMATION_GAP_KIND


def test_without_the_kit_the_creator_is_told_before_anything_is_paid(monkeypatch):
    monkeypatch.setattr(a2d, "available", lambda home=None: False)
    assert cm.plan_style(dart(), tsm.STYLES)["route"]["medium_gap"] == cm.ANIMATION_GAP


# ── what Claude writes, checked ─────────────────────────────────────────────────────────────────────────────────

def test_the_cast_keeps_only_what_the_kit_draws():
    cast = a2d.clean_cast({"Gojo Satoru": {"name": "Gojo", "color": "teal", "hair": {"style": "spiky up", "color": "white"},
                                           "eyes": "blindfold", "marks": ["tattoo", {"kind": "cheek_stripes"}],
                                           "weapon": "laser", "gloves": "none", "scarf": {"color": "#aa0000"}},
                           "sukuna": {"color": "#E0625A", "extra_arms": True, "height": 9}})
    g = cast["gojo_satoru"]
    assert g["color"] == "#3fb59e" and g["hair"] == {"style": "spiky_up", "color": "#eeeeee"}
    assert g["eyes"]["style"] == "blindfold" and g["marks"] == [{"kind": "cheek_stripes", "color": "#1a1a1a"}]
    assert g["weapon"]["kind"] == "none" and "gloves" not in g and g["scarf"] == {"color": "#aa0000"}
    assert cast["sukuna"]["extra_arms"] is True and cast["sukuna"]["height"] == 1.4
    with pytest.raises(a2d.AnimationStopped):
        a2d.clean_cast({})


def test_the_plan_keeps_known_moves_inside_its_stretch_and_covers_it_with_shots():
    cast = {"gojo": {}, "sukuna": {}}
    sets = {"street": {"prompt": "x"}, "void": {"prompt": "y"}}
    raw = {"shots": [{"start": 50.0, "set": "street", "framing": "close:gojo"}, {"start": 50.3, "framing": "two"},
                     {"start": 53, "set": "void", "framing": "medium:nobody", "move": "spin"}],
           "actions": [{"at": 50.2, "who": "gojo", "move": "charge", "dur": 2},
                       {"at": 51, "who": "sukuna", "move": "jab", "target": "gojo"},
                       {"at": 52, "who": "sukuna", "move": "moonwalk"}, {"at": 52, "who": "toji", "move": "jab"},
                       {"at": 99, "who": "gojo", "move": "blast", "target": "gojo"}],
           "fx": [{"kind": "impact_frame", "at": 51.2}, {"kind": "fireworks", "at": 51}]}
    plan = a2d.clean_plan(raw, cast, sets, 50.0, 56.0)
    assert plan["duration"] == 6.0
    shots = plan["shots"]
    assert shots[0]["start"] == 0 and shots[-1]["end"] == 6.0
    assert all(b["start"] == a["end"] for a, b in zip(shots, shots[1:])), "no gap between shots"
    assert shots[-1]["set"] == "void" and shots[-1]["framing"] == "medium:gojo" and shots[-1]["move"] == "static"
    moves = [(x["who"], x["move"], x["at"]) for x in plan["actions"]]
    assert ("gojo", "charge", 0.2) in moves and ("sukuna", "jab", 1.0) in moves
    assert not any(m == "moonwalk" or w == "toji" for w, m, _ in moves)
    blast = next(x for x in plan["actions"] if x["move"] == "blast")
    assert blast["at"] == 6.0 and "target" not in blast, "clamped to the stretch, never aimed at itself"
    assert {x["who"] for x in plan["actions"] if x["move"] == "place"} == {"gojo", "sukuna"}, "everyone is placed"
    assert plan["fx"] == [{"kind": "impact_frame", "at": 1.2}]


def test_long_narration_is_choreographed_in_stretches_between_lines():
    lines = [(i * 5.0, i * 5.0 + 4.5, f"line {i}", []) for i in range(20)]
    parts = a2d.chunks(lines, 100.0)
    assert [round(p[0]) for p in parts] == [0, 50], "even stretches, never a stub of a few seconds"
    assert parts[-1][1] == 100.0 and sum(len(p[2]) for p in parts) == 20
    short = [(i * 4.5, i * 4.5 + 4.0, f"line {i}", []) for i in range(11)]
    assert len(a2d.chunks(short, 49.0)) == 1, "a minute's narration is one scene"


def test_sound_marks_play_the_kits_own_recordings():
    assert a2d.sound_file("punch") == "file:sfx/animated2d/punch.mp3"
    assert a2d.sound_file("riser") == "riser" and a2d.sound_file("laser") == ""


def test_an_answer_that_is_not_json_stops_with_its_reason():
    with pytest.raises(a2d.AnimationStopped, match="incomplete"):
        a2d._json("I think Gojo wins")
    assert a2d._json('```json\n{"cast": {}}\n```') == {"cast": {}}


# ── the release ships the private kit ───────────────────────────────────────────────────────────────────────────

def test_the_builder_ships_the_kit_and_its_sounds(tmp_path):
    from scripts import build_frontier_admin as builder
    kit_dir = tmp_path / "data/frontier/animated2d"
    (kit_dir / "sounds").mkdir(parents=True)
    for name in ("rig.js", "draw.js", "moves.js", "fx.js", "film.js", "cast.txt", "director.txt"):
        (kit_dir / name).write_text("x", encoding="utf-8")
    (kit_dir / "sounds" / "punch.mp3").write_bytes(b"mp3")
    shipped = {dest for _, dest in builder.private_animated2d_resources(tmp_path)}
    assert "animated2d/film.js" in shipped and "assets/sfx/animated2d/punch.mp3" in shipped
    (kit_dir / "notes.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected"):
        builder.private_animated2d_resources(tmp_path)
    (kit_dir / "notes.md").unlink()
    (kit_dir / "film.js").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        builder.private_animated2d_resources(tmp_path)
    assert builder.private_animated2d_resources(tmp_path / "nowhere") == []


# ── the likeness check draws its test shots with the kit ────────────────────────────────────────────────────────

def _jpeg(path, colour):
    from PIL import Image
    Image.new("RGB", (320, 180), colour).save(path, quality=80)
    return path


def test_the_likeness_check_renders_animated_styles_and_pays_for_no_picture(tmp_path, monkeypatch):
    import cr_style_likeness as like
    style = {"name": "cr_clone_dart", "look": {"visuals": "cr_animated2d", "animated2d": a2d.from_profile(dart()),
                                               "still_suffix": " Flat vector.", "image_model": "nano-banana-2-1"}}
    assert like.wanted(style) == ""
    shots = []
    for k in range(6):
        shots.append(_jpeg(tmp_path / f"frame_{k}.jpg", (90 + k * 20, 120, 140)))
    rendered = []

    def render(home, st, out_dir, timeout_s=None):
        out = []
        for k in range(3):
            out.append(_jpeg(Path(out_dir) / f"r0_{k}.jpg", (200, 80 + k * 30, 80)))
        rendered.append(st["look"]["visuals"])
        return out
    asked = []

    def call(path, body):
        asked.append(body["context"])
        return {"candidates": [{"overall": 7, "characters": 7}] * 3 + [{"overall": 7}], "dealbreakers": [], "fixes": []}
    monkeypatch.setattr(like, "frames", lambda home, look_id: shots)
    result = like.run(tmp_path / "op", {"look_id": "lk_x"}, style, lambda m: None, home=tmp_path, call=call,
                      render=render, generate=lambda *a, **k: pytest.fail("no picture is paid for"))
    assert rendered == ["cr_animated2d"] and result["state"] == "done" and result["cost_usd"] == 0
    assert "2D animation engine" in asked[0]
    saved = like.apply_saved(tmp_path / "op", json.loads(json.dumps(style)), tmp_path)
    assert "style_image" not in saved["look"], "a kit frame never becomes a drawing to copy"


# ── the plugin, end to end, with a stand-in engine ──────────────────────────────────────────────────────────────

CASTING = {"cast": {"gojo": {"name": "Gojo", "color": "#3fb59e", "hair": {"style": "spiky_up", "color": "#f4f6f8"},
                             "eyes": {"style": "blindfold"}, "aura": {"color": "#59c8ff"}},
                    "sukuna": {"name": "Sukuna", "color": "#e0625a", "hair": {"style": "spiky_back", "color": "#f2a6b4"},
                               "marks": [{"kind": "cheek_stripes"}], "aura": {"color": "#ff3b3b"}}},
           "sets": {"ruins": {"prompt": "a ruined city street at dusk", "sky": ["#8d939b", "#c3c7cc"],
                              "floor_colors": ["#5c6067", "#474b51"]},
                    "rubble": {"prompt": "the same street in rubble after the blast"}}}
DIRECTION = {"shots": [{"start": 0, "set": "ruins", "framing": "wide"}, {"start": 1.2, "set": "ruins", "framing": "two"}],
             "actions": [{"at": 0, "who": "gojo", "move": "place", "x": -1.8, "face": "right"},
                         {"at": 0, "who": "sukuna", "move": "place", "x": 1.8, "face": "left"},
                         {"at": 0.3, "who": "sukuna", "move": "dash", "target": "gojo"},
                         {"at": 0.9, "who": "sukuna", "move": "cross", "target": "gojo"},
                         {"at": 1.6, "who": "gojo", "move": "uppercut", "target": "sukuna"}],
             "fx": [{"kind": "shake", "at": 1.85, "amount": 12}]}


class Engine:
    MOTION_WORKERS = 1

    def __init__(self, job, style):
        self.job, self.calls, self.prompts = job, [], []
        self.STYLE_INFO = {style: {"look": {"visuals": "cr_animated2d", "animated2d": a2d.from_profile(dart())}}}

    def step(self, msg):
        self.calls.append(("step", msg))

    def log(self, msg):
        pass

    def _extra_block(self):
        return ""

    def claude(self, prompt, model, max_tokens=0, provider=None):
        self.prompts.append(prompt)
        return json.dumps(CASTING if "character designer" in prompt else DIRECTION)

    def image_gen(self, prompt, dest, label="", ref_urls=None):
        assert "nobody in it" in prompt
        self.calls.append(("plate", label, bool(ref_urls), prompt.startswith("STYLE REFERENCE")))
        from PIL import Image
        Image.new("RGB", (1280, 720), (150, 154, 160)).save(dest, quality=80)

    def _parse_srt_full(self, text):
        return [(0.0, 1.2, "Sukuna rushes in and lands a heavy cross"), (1.2, 2.4, "but Gojo answers with an uppercut")]

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


def test_the_plugin_casts_draws_plates_choreographs_renders_and_hands_the_film_to_the_engine(tmp_path, monkeypatch):
    if not PRIVATE_KIT.is_dir():
        pytest.skip("the animated 2D kit is private (data/frontier/animated2d)")
    pytest.importorskip("playwright.sync_api")
    home = tmp_path / "studio"
    shutil.copytree(PRIVATE_KIT, home / a2d.KIT, ignore=shutil.ignore_patterns("sounds"))
    monkeypatch.setattr(a2d, "HERE", home)
    job = tmp_path / "job"
    job.mkdir()
    (job / "script.txt").write_text("Sukuna rushes in and lands a heavy cross, but Gojo answers with an uppercut.",
                                    encoding="utf-8")
    (job / "title.txt").write_text("Gojo vs Sukuna\n", encoding="utf-8")
    (job / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,200\nSukuna rushes in\n", encoding="utf-8")
    engine = Engine(job, "cr_clone_dart")
    import cr_claude
    import cr_media
    monkeypatch.setattr(cr_claude, "VISUAL_MODEL", "opus", raising=False)
    monkeypatch.setattr(cr_media, "upload", lambda path, task="": "https://kie.example/" + Path(path).name)
    try:
        a2d.prepare(engine, "Sukuna rushes in...", job, "cr_clone_dart", False)
        final = a2d.assemble(engine, job, job / "audio.mp3", job / "subs.srt", "cr_clone_dart", False, True)
    except a2d.AnimationStopped as error:
        if "Chromium" in str(error):
            pytest.skip("Local Chromium is unavailable: " + str(error))
        raise
    assert final == job / "video.mp4"
    casting = json.loads((job / "animated2d" / "casting.json").read_text())
    assert set(casting["cast"]) == {"gojo", "sukuna"} and list((job / "animated2d" / "plates").glob("ruins-*.jpg"))
    plates = [c for c in engine.calls if c[0] == "plate"]
    assert plates[0][1:] == ("plate ruins", False, False) and plates[1][1:] == ("plate rubble", True, True), \
        "the first place is drawn alone, the next copies its drawing style"
    assert sum("character designer" in p for p in engine.prompts) == 1, "cast once: prepare's design is reused"
    timeline = next(c[1] for c in engine.calls if c[0] == "plan")
    clip = Path(timeline[0][1])
    assert clip.is_file() and clip.stat().st_size > 10000
    marks = json.loads(clip.with_suffix(".sfx.json").read_text())
    names = [m[1] for m in marks]
    assert "file:sfx/animated2d/punch_heavy.mp3" in names or "file:sfx/animated2d/punch.mp3" in names
    assert any(c[0] == "mux" for c in engine.calls)
    # a second run reuses everything: no new Claude call, no new render
    calls = len(engine.prompts)
    a2d.assemble(engine, job, job / "audio.mp3", job / "subs.srt", "cr_clone_dart", False, True)
    assert len(engine.prompts) == calls
