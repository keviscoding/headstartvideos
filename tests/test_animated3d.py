"""Animated 3D (Studio 105): a 3D channel's characters rendered by the channel's own renderer, which the creator's Claude
Code writes during the study on the 3D kit (three.js in the same locked page as the 2D renderers, with WebGL). A 3D
Shorts clone once came out as an AI-drawn town: pictures in place of 3D are now only ever the creator's own choice."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
sys.path.insert(0, str(ROOT / "tests"))
import cr_animated2d as a2d  # noqa: E402
import cr_style_map as cm  # noqa: E402
import test_style_map as tsm  # noqa: E402
from test_style_look import home  # noqa: E402,F401 - the fixture

PRIVATE_2D = ROOT / "data" / "frontier" / "animated2d"
PRIVATE_3D = ROOT / "data" / "frontier" / "animated3d"
ENGINES = [Path(p) for p in [os.environ.get("CR_THREED_ENGINE", ""),
                             str(Path.home() / "Downloads" / "ChannelRecipe studio" / "threed" / "engine")] if p]

BOX = r"""
Kit.renderer({
  name: "box-world", version: 1,
  manifest: { cast: { describe: "a red box", traits: { color: "colour" } }, actions: { place: "{x}", spin: "{}" } },
  plates: { draw: false },
  prepare(scene, Kit) {
    const T = Kit.THREE, world = new T.Scene(), camera = new T.PerspectiveCamera(45, scene.width / scene.height, 0.1, 50);
    world.add(new T.HemisphereLight(0xffffff, 0x333333, 2));
    const box = new T.Mesh(new T.BoxGeometry(1, 1, 1), Kit.toon.material("#d0302a")); world.add(box);
    camera.position.set(0, 1.2, 4); camera.lookAt(0, 0, 0);
    Kit.sound(0.5, "pop", 0);
    return { world, camera, box };
  },
  frame(view, t, s, Kit) {
    view.gl.setClearColor(0x88aadd); s.box.rotation.y = t;
    view.gl.render(s.world, s.camera);
    Kit.caption(view.overlay, Kit.wordsAt(t, 1));
  },
});
"""
SCENE = {"dims": 3, "duration": 3, "width": 540, "height": 960, "cast": {"b": {"name": "B"}}, "sets": {"s": {}},
         "shots": [{"start": 0, "set": "s"}], "actions": [{"at": 0, "who": "b", "move": "place", "x": 0}],
         "words": [{"w": "hello", "t": 0.2}], "stills": [1.0]}


@pytest.fixture
def home3d(tmp_path):
    """A Studio folder with both private kits and the 3D engine's three.js (never in the public repository)."""
    engine = next((e for e in ENGINES if (e / "vendor" / "three" / "three.module.js").is_file()), None)
    if not PRIVATE_2D.is_dir() or not PRIVATE_3D.is_dir() or engine is None:
        pytest.skip("the animated 3D kit and Studio's 3D engine are private")
    pytest.importorskip("playwright.sync_api")
    shutil.copytree(PRIVATE_2D, tmp_path / a2d.KIT, ignore=shutil.ignore_patterns("sounds"))
    shutil.copytree(PRIVATE_3D, tmp_path / a2d.KIT3D)
    (tmp_path / "threed").mkdir()
    (tmp_path / "threed" / "engine").symlink_to(engine, target_is_directory=True)
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


# ── the read-only kit origin ────────────────────────────────────────────────────────────────────────────────────

def test_a_3d_page_reads_only_its_kit_from_this_studio(tmp_path):
    (tmp_path / "animated3d").mkdir()
    (tmp_path / "animated3d" / "core3d.js").write_text("export const ok = 1;")
    (tmp_path / "secret.txt").write_text("no")
    (tmp_path / ".env").write_text("KEY=no")
    body, kind = a2d._kit_file(tmp_path, "kit/core3d.js", "<html>")
    assert body == b"export const ok = 1;" and kind == "text/javascript"
    assert a2d._kit_file(tmp_path, "index.html", "<html>")[0] == b"<html>"
    for bad in ("kit/../secret.txt", "kit/../.env", "kit/core3d.py", "other/core3d.js", "kit/", "../secret.txt"):
        assert a2d._kit_file(tmp_path, bad, "<html>") == (None, ""), bad


def test_a_3d_renderer_draws_in_webgl_with_no_way_out(home3d):
    sneaky = (r"""window.__got = 0; window.__kit = 0; window.__refused = 0;
document.addEventListener("securitypolicyviolation", () => { window.__refused++; });
const img = new Image(); img.onload = () => { window.__got++; }; img.src = "https://example.com/p.png";
window["fe" + "tch"]("https://example.com/x").then(() => { window.__got++; }).catch(() => {});
window["fe" + "tch"]("https://kit.local/kit/core3d.js").then((r) => { window.__kit = r.status; }).catch(() => {});
""" + BOX)
    assert a2d.check_code(sneaky) == []

    def run():
        box = a2d._Sandbox(a2d.page(SCENE, sneaky, home3d), 540, 960, gl=True, home=home3d)
        try:
            import time
            time.sleep(1.0)
            return box.page.evaluate("[window.__got, window.__kit, window.__refused]"), box.blocked, box.error()
        finally:
            box.close()
    (got, kit, refused), blocked, err = _chromium(run)
    assert got == 0 and err == "" and refused >= 2, "nothing leaves the page: its own policy refuses it"
    assert not [b for b in blocked if not b.startswith("https://example.com")], blocked
    assert kit == 200, "the kit itself is readable"
    stills = _chromium(lambda: a2d.render_stills(SCENE, home3d / "stills", BOX, home=home3d))
    from PIL import Image
    assert Image.open(stills[0]).size == (540, 960) and not a2d._blank(stills[0])
    assert _chromium(lambda: a2d.manifest(BOX, home3d, dims=3))["dims"] == 3


def test_the_3d_example_claude_is_shown_draws_a_vertical_short_and_passes_studios_frame_review(home3d):
    code = (home3d / a2d.KIT3D / "example3d.js").read_text(encoding="utf-8")
    assert a2d.check_code(code) == []
    scene = {"dims": 3, "duration": 8, "width": 1080, "height": 1920, "captions": True,
             "words": [{"w": w, "t": i * 0.4} for i, w in enumerate("in this world everyone can jump".split())],
             "cast": {"bo": {"name": "Bo", "shirt": "#e0462f", "mood": "happy"}, "al": {"name": "Al"}},
             "sets": {"park": {"sky": ["#8ec5f0"], "floor_colors": ["#6fb35a"]}},
             "shots": [{"start": 0, "end": 4, "set": "park", "framing": "wide"},
                       {"start": 4, "set": "park", "framing": "medium:bo"}],
             "actions": [{"at": 0, "who": "bo", "move": "place", "x": -1}, {"at": 0, "who": "al", "move": "place", "x": 1},
                         {"at": 0.5, "who": "bo", "move": "walk", "to": 0}, {"at": 2, "who": "al", "move": "jump"}],
             "fx": [{"kind": "fade", "at": 5, "dir": "out"}, {"kind": "fade", "at": 6, "dir": "in"}]}
    samples = _chromium(lambda: a2d.sweep(scene, code, home=home3d))
    assert a2d.blank_runs(samples, 8) == [] and min(x for t, x in samples if t > 6.8) > 4


# ── routing: any 3D channel with characters ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def kit3d(monkeypatch):
    monkeypatch.setattr(a2d, "available3d", lambda home=None: True)


def _blocky(**over):
    return tsm.profile({"3d": 0.85, "graphic": 0.15}, {"3d_render": 0.85}, **{**tsm.BLOCKY, **over})


def test_a_3d_channel_with_characters_is_rendered_by_its_own_renderer_not_drawn(kit3d):
    plan = cm.plan_style(_blocky(), tsm.STYLES)
    route = plan["route"]
    assert route["engine"] == "animated3d" and "medium_gap" not in route and "three.js" in route["why"]
    assert plan["base"] == "stories", "a narrated story recipe carries the renderer, never the mannequin engine"
    look = plan["overrides"]["look"]
    assert look["visuals"] == a2d.VISUALS3D and look["animated2d"]["dims"] == 3
    assert (look["animated2d"]["width"], look["animated2d"]["height"]) == (1920, 1080) and "aspect" not in look
    assert plan["overrides"]["pacing"]["clips_per_min"] == 0.0 and plan["cost"]["usd_per_min"] == 0.0
    assert "style_image" not in plan["needs"] and "threed" not in (look.get("threed") or {})
    question = next(q for q in plan["questions"] if q["id"] == "made")
    assert question["options"] == [cm.MADE_CODE, cm.MADE_OTHER] and "writes this channel's own renderer" in question["ask"]
    assert "AI pictures drawn in their 3D look" in question["ask"], "what they'd get instead, said honestly"
    ifology = cm.plan_style(tsm.profile({"3d": 0.85, "graphic": 0.15}, {"3d_render": 0.85}, **tsm.IFOLOGY), tsm.STYLES)
    assert ifology["route"]["engine"] == "animated3d", "a designed cast too: the kit is the renderer's helper now"


def test_a_3d_shorts_channel_is_rendered_vertical_with_its_captions_at_its_own_pace(kit3d):
    plan = cm.plan_style(_blocky(format__vertical=tsm.axis(1.0), edit__median_shot_s=tsm.axis(1.5)), tsm.STYLES)
    look = plan["overrides"]["look"]
    spec = look["animated2d"]
    assert look["aspect"] == "9:16" and (spec["width"], spec["height"]) == (1080, 1920) and spec["captions"] is True
    assert spec["shot_s"] == 1.5 and "1 to 2.5 s" in a2d.pace_words(spec)
    assert "vertical Shorts" in plan["route"]["why"]


def test_pictures_in_place_of_3d_are_only_the_creators_choice_and_a_hand_picked_3d_recipe_is_kept(kit3d):
    for answers in ({"made": cm.MADE_OTHER}, {"medium": cm.MEDIUM_PICTURES}):      # (and a look answered before 106)
        chose = cm.plan_style(_blocky(), tsm.STYLES, answers=answers)
        assert chose["route"]["engine"] == "drawn" and "you chose pictures" in chose["route"]["why"]
        assert "medium_gap" not in chose["route"] and chose["overrides"]["look"].get("visuals") != a2d.VISUALS3D
    recon = tsm.profile({"3d": 0.85}, {"3d_render": 0.85}, pictures__art_style=tsm.RECONSTRUCTION)
    assert cm.plan_style(recon, tsm.STYLES)["route"]["engine"] == "animated3d", \
        "faceless reconstructions are written in code too (a tabletop wargame went to the mannequin engine)"
    assert cm.plan_style(recon, tsm.STYLES, answers={"made": cm.MADE_OTHER})["route"]["engine"] == "threed"
    picked = cm.plan_style(_blocky(), tsm.STYLES, base="3d")
    assert picked["route"]["engine"] != "animated3d", "the creator picked the 3D recipe themselves"


def test_every_channel_is_written_in_code_unless_the_creator_chooses_otherwise(kit3d, monkeypatch):
    """The owner (2026-10-26): "we're promising them that they can clone anything... remove the AI pictures route,
    unless they choose so." An AI-picture channel, a footage documentary, a faceless 3D reconstruction channel and a
    Roblox Shorts channel all get their own Claude-written renderer; Studio's ready-made way only on their word."""
    monkeypatch.setattr(a2d, "available", lambda home=None: True)
    drawn = tsm.profile({"generated": 0.9, "graphic": 0.1})
    crime = tsm.profile({"footage": 0.8, "graphic": 0.2}, {"bodycam_cctv": 0.45, "archival_footage": 0.35})
    faceless = tsm.profile({"3d": 0.6, "graphic": 0.4}, pictures__art_style=tsm.RECONSTRUCTION)
    for prof, kit, instead in ((drawn, "animated2d", "pictures"), (crime, "animated2d", "pictures"),
                               (faceless, "animated3d", "threed"), (_blocky(), "animated3d", "drawn")):
        plan = cm.plan_style(prof, tsm.STYLES)
        assert plan["route"]["engine"] == kit and plan["route"]["coded"] and "medium_gap" not in plan["route"]
        look = plan["overrides"]["look"]
        assert look["visuals"] == (a2d.VISUALS3D if kit == "animated3d" else a2d.VISUALS)
        assert plan["overrides"]["pacing"]["clips_per_min"] == 0.0 and "style_image" not in plan["needs"]
        assert [q["id"] for q in plan["questions"] if q["id"] in ("made", "medium", "animation")] == ["made"]
        q = next(q for q in plan["questions"] if q["id"] == "made")
        assert q["options"] == [cm.MADE_CODE, cm.MADE_OTHER] and "Studio recommends code" in q["ask"]
        other = cm.plan_style(prof, tsm.STYLES, answers={"made": cm.MADE_OTHER})
        assert other["route"]["engine"] == instead and other["overrides"]["look"].get("visuals") not in a2d.PLUGINS
    assert "real footage isn't part of a style written in code" in \
        next(q for q in cm.plan_style(crime, tsm.STYLES)["questions"] if q["id"] == "made")["ask"]
    assert cm.plan_style(faceless, tsm.STYLES, base="3d")["route"]["engine"] == "threed", "the 3D recipe picked by hand"
    tall = cm.plan_style(tsm.profile({"generated": 0.9, "graphic": 0.1}, format__vertical=tsm.axis(1.0)), tsm.STYLES)
    spec = tall["overrides"]["look"]["animated2d"]
    assert tall["overrides"]["look"]["aspect"] == "9:16" and (spec["width"], spec["height"]) == (1080, 1920) \
        and spec["captions"] is True, "a Shorts channel written in 2D is vertical with its captions"
    monkeypatch.setattr(a2d, "available", lambda home=None: False)
    monkeypatch.setattr(a2d, "available3d", lambda home=None: False)
    old = cm.plan_style(drawn, tsm.STYLES)
    assert old["route"]["engine"] == "pictures" and not any(q["id"] == "made" for q in old["questions"]), \
        "a Studio without the kits routes as before"


def test_a_2d_shorts_channel_written_in_code_is_built_vertical(tmp_path, monkeypatch):
    import cr_style_clone
    import cr_style_likeness
    folder = tmp_path / "home" / "output" / "cr-style-clone" / ("op_" + "e" * 24)
    folder.mkdir(parents=True)
    got = {}
    monkeypatch.setattr(cr_style_likeness, "frames", lambda home, look_id: tsm_frames(tmp_path / "look", 9))
    monkeypatch.setattr(cr_style_likeness, "flat_graphic", lambda p: False)
    monkeypatch.setattr(a2d, "build", lambda folder, spec, channel, **kw: got.update(spec=spec, channel=channel)
                        or {"state": "passed"})
    tall = tsm.profile({"generated": 0.9, "graphic": 0.1}, format__vertical=tsm.axis(1.0))
    cr_style_clone._renderer(folder, {"look_id": "lk_x", "answers": {}, "renderer": "2d"}, {"profile": tall},
                             see=None, log=lambda m: None)
    assert (got["spec"]["width"], got["spec"]["height"]) == (1080, 1920) and "dims" not in got["spec"]
    assert "Shorts" in got["channel"]["format"]


def test_without_the_3d_kit_a_3d_channel_is_routed_as_before(monkeypatch):
    monkeypatch.setattr(a2d, "available3d", lambda home=None: False)
    assert cm.plan_style(_blocky(), tsm.STYLES)["route"]["engine"] == "drawn"


# ── the study, the style, the video ─────────────────────────────────────────────────────────────────────────────

def test_the_study_writes_a_3d_renderer_at_the_channels_size(tmp_path, monkeypatch):
    import cr_style_clone
    import cr_style_likeness
    folder = tmp_path / "home" / "output" / "cr-style-clone" / ("op_" + "b" * 24)
    folder.mkdir(parents=True)
    frames = tsm_frames(tmp_path / "look", 9)
    got = {}
    monkeypatch.setattr(cr_style_likeness, "frames", lambda home, look_id: frames)
    monkeypatch.setattr(cr_style_likeness, "flat_graphic", lambda p: False)
    monkeypatch.setattr(a2d, "build", lambda folder, spec, channel, **kw: got.update(spec=spec, channel=channel, **kw)
                        or {"state": "passed"})
    tall = _blocky(format__vertical=tsm.axis(1.0))
    cr_style_clone._renderer(folder, {"look_id": "lk_x", "answers": {}, "renderer": "3d",
                                      "reference_url": "https://www.youtube.com/@gneef/shorts"},
                             {"profile": tall, "references": [{"channel": "gneef"}]}, see=None, log=lambda m: None)
    assert got["spec"]["dims"] == 3 and (got["spec"]["width"], got["spec"]["height"]) == (1080, 1920)
    assert got["channel"]["medium"] == "real 3D (three.js)" and "Shorts" in got["channel"]["format"]
    assert got["names"] == ["gneef"]


def _roblox_look(home):
    """A finished look at a Roblox-style 3D story channel (vertical Shorts), from test_style_look's references."""
    import cr_style_look as look
    import test_style_look as tsl
    _, done, _ = tsl.run_look(home)
    path = look.folder(home, done["look_id"]) / "look.json"
    saved = json.loads(path.read_text())
    pictures = saved["profile"]["pictures"]
    pictures["sources"]["families"] = {"3d": 0.85, "graphic": 0.15}
    pictures["art_style"]["value"] = "Roblox-style blocky avatars with yellow block heads, decal faces."
    saved["profile"].setdefault("format", {})["vertical"] = tsm.axis(1.0)
    saved.pop("mapping", None)
    path.write_text(json.dumps(saved))
    return done["look_id"], json.loads(path.read_text())


def test_a_3d_study_is_briefed_with_the_3d_kit_from_its_start(home, kit3d, monkeypatch):
    """The first @gneef study stored renderer: True, the 3D was lost on the way, and Claude wrote four flat canvas drafts
    for a three.js channel (4.3/10). The spec now says "3d" from the start, an older True is read from the route, and
    the build is given the 3D guide at the Short's size."""
    import cr_style_clone as clone
    import cr_style_likeness
    import test_style_look as tsl
    look_id, looked = _roblox_look(home)
    payload = {"preview_id": "pv_" + "a" * 64, "reference_ids": [tsl.VIDEO], "base_style": "stories", "label": "Mine",
               "title": "A video", "acknowledge_costs": True, "look_id": look_id, "answers": {}, "visual_mode": "mixed"}
    spec = clone._spec(home, payload)
    assert spec["renderer"] == "3d"
    assert clone.renderer_dims(spec, looked, home) == 3
    assert clone.renderer_dims({**spec, "renderer": True}, looked, home) == 3, "a study started before the fix"
    pictures = {**spec, "renderer": True, "answers": {"medium": cm.MEDIUM_PICTURES}}
    assert clone.renderer_dims(pictures, looked, home) == 2
    assert clone._spec(home, {**payload, "visual_mode": "reference_only"}).get("renderer") is None
    # the build itself: the 3D guide and example, never the 2D one
    for kit, files in ((a2d.KIT, ("build.txt", "AUTHORING.md")), (a2d.KIT3D, ("AUTHORING.md", "example3d.js"))):
        (home / kit).mkdir(exist_ok=True)
        for name in files:
            (home / kit / name).write_text(f"[{kit}/{name}]" + (" [AUTHORING] [EXAMPLE] [CHANNEL]" if name == "build.txt"
                                                                else ""), encoding="utf-8")
    monkeypatch.setattr(cr_style_likeness, "frames", lambda h, i: tsm_frames(home / "frames", 9))
    monkeypatch.setattr(cr_style_likeness, "flat_graphic", lambda p: False)
    folder = clone._folder(home) / ("op_" + "d" * 24)
    (folder / "renderer").mkdir(parents=True)
    (folder / "renderer" / "round-1.txt").write_text("```javascript\n// 2D canvas draft\n```", encoding="utf-8")
    prompts = []
    import cr_bridge
    monkeypatch.setattr(cr_bridge, "call", lambda path, payload: {})
    result = clone._renderer(folder, spec, looked, lambda prompt, images: prompts.append(prompt) or "", lambda m: None)
    assert result["state"] == "failed" and len(prompts) == 1, "the stale 2D draft is not this build's answer"
    assert f"[{a2d.KIT3D}/AUTHORING.md]" in prompts[0] and f"[{a2d.KIT3D}/example3d.js]" in prompts[0]
    assert f"[{a2d.KIT}/AUTHORING.md]" not in prompts[0] and "real 3D (three.js)" in prompts[0]
    assert (folder / "renderer" / "key").read_text() == result["key"]
    assert list((folder / "renderer").glob("old-*/round-1.txt")), "the old draft is kept aside, not deleted"


def test_a_designed_cast_keeps_its_renderer_on_the_recipe_the_look_recommended(home, kit3d):
    """An @ifologyfilms-type cast was promised Claude's 3D renderer on the look screen, then drawn as AI pictures: the
    study passes the recommended base back, and that counted as a picture recipe picked by hand."""
    import cr_style_clone as clone
    import cr_style_look as look
    import test_style_look as tsl
    profile = tsm.profile({"3d": 0.85, "graphic": 0.15}, {"3d_render": 0.85}, **tsm.IFOLOGY)
    plan = cm.plan_style(profile, tsm.STYLES)
    again = cm.plan_style(profile, tsm.STYLES, base=plan["base"])
    assert plan["route"]["engine"] == again["route"]["engine"] == "animated3d" and plan["base"] == "stories"
    assert again["overrides"]["look"]["visuals"] == a2d.VISUALS3D
    picked = next(b for b in tsm.STYLES if b not in ("stories", "3d", "nocturne"))
    assert cm.plan_style(profile, tsm.STYLES, base=picked)["route"]["engine"] == "animated3d", \
        "a recipe picked by hand carries the narration; the pictures are still written in code"
    assert cm.plan_style(profile, tsm.STYLES, answers={"made": cm.MADE_OTHER})["route"]["engine"] == "designed"
    look_id, _ = _roblox_look(home)
    path = look.folder(home, look_id) / "look.json"
    saved = json.loads(path.read_text())
    saved["profile"]["pictures"]["art_style"]["value"] = tsm.IFOLOGY["pictures__art_style"]["value"]
    saved["profile"]["pictures"]["people_3d"] = {"value": {"designed": 1.0}, "confidence": 0.7, "from": ["watched"]}
    path.write_text(json.dumps(saved))
    payload = {"preview_id": "pv_" + "a" * 64, "reference_ids": [tsl.VIDEO], "base_style": "stories", "label": "Mine",
               "title": "A video", "acknowledge_costs": True, "look_id": look_id, "answers": {}, "visual_mode": "mixed"}
    assert clone._spec(home, payload)["renderer"] == "3d"


def test_a_renderer_study_carries_on_when_its_graphics_browser_was_busy(tmp_path):
    """Lucas the Spider's first study stopped after 8 minutes of Claude work: another render held the graphics browser,
    so its graphics kit was checked in a stand-in browser. Where the channel's own renderer draws the pictures, that no
    longer stops the study; on any other route it still does, with the reason."""
    import cr_style_clone as clone
    (tmp_path / "study.log").write_text("  learn: validate \u2014 DOM mock (the render lock is held (another render)): "
                                        "9/9 types ok\n")
    mock = {"kit": "macro_garden", "browser": False, "wants": {}}
    kept = clone._graphics_kit(tmp_path, mock, renders=True)
    assert kept["kit"] == "macro_garden" and "another video" in kept["browser_skipped"]
    with pytest.raises(clone.StudyStopped, match="another video was using the graphics browser"):
        clone._graphics_kit(tmp_path, mock, renders=False)
    with pytest.raises(clone.StudyStopped, match="did not produce a graphics kit"):
        clone._graphics_kit(tmp_path, {"kit": None}, renders=False)
    real = {"kit": "macro_garden", "browser": True}
    assert clone._graphics_kit(tmp_path, real, renders=False) is real
    (tmp_path / "genkit").mkdir()
    (tmp_path / "genkit/kit.json").write_text(json.dumps({"name": "macro_garden", "types": {"a": 1, "b": 2, "c": 3}}))
    (tmp_path / "genkit/kit.js").write_text("// kit")
    assert "genkit/kit.js" in clone._validated_assets(tmp_path, kept)
    with pytest.raises(RuntimeError):
        clone._validated_assets(tmp_path, mock)


def test_a_renderer_study_never_depends_on_the_motion_graphics_learner(tmp_path):
    """The owner (2026-10-26): the renderer build must not depend on the motion-graphics learner's gate. On a renderer
    route a learner that wrote no kit still leaves a style (its renderer draws everything); elsewhere it still stops."""
    from types import SimpleNamespace
    import cr_style_clone as clone
    kept = clone._graphics_kit(tmp_path, {}, renders=True)
    assert kept["kit"] is None and kept["no_kit"] and clone._validated_assets(tmp_path, kept) == {}
    with pytest.raises(clone.StudyStopped, match="did not produce a graphics kit"):
        clone._graphics_kit(tmp_path, {}, renders=False)
    home = tmp_path / "home"
    folder = home / "output" / "cr-style-clone" / ("op_" + "f" * 24)
    folder.mkdir(parents=True)
    (home / "styles").mkdir()
    attached = []
    designer = SimpleNamespace(build=lambda genome, info, base, **kw: {"name": "x", "look": {}},
                               save=lambda folder, style: None,
                               genkit=lambda style, folder, log, invalidate: attached.append(1) or False)
    learner = SimpleNamespace(around=lambda *a, **k: None, summary=lambda rec: {"kit": rec.get("kit")})
    spec = {"base_style": "stories", "minutes": 1, "title": "t", "style_id": "cr_clone_ffffffffffff", "label": "L",
            "references": [{"id": "abcdefghijk"}], "visual_mode": "mixed"}
    style = clone.finish_original(SimpleNamespace(STYLE_INFO={}), folder, spec, {"genome": {}}, kept, lambda p: None,
                                  learner=learner, designer=designer, log=lambda m: None)
    assert style["name"] == "cr_clone_ffffffffffff" and attached == [], "no kit to attach, and none swapped in"
    assert json.loads((folder / "study-result.json").read_text())["kit"] is None


def test_renderer_drafts_keep_claude_codes_own_effort(monkeypatch):
    """Studio's other Opus stages ask Claude for medium effort (106); writing a whole renderer keeps the CLI's own,
    which every renderer so far was measured with, for the renderer step's own calls only (never process-wide)."""
    import os
    from contextlib import contextmanager
    import cr_claude
    import cr_style_clone as clone
    asked = []

    @contextmanager
    def call_effort(level):
        asked.append(level)
        yield
    monkeypatch.setattr(cr_claude, "call_effort", call_effort, raising=False)
    before = dict(os.environ)
    with clone._renderer_effort():
        assert dict(os.environ) == before, "nothing process-wide changes"
    assert asked == ["default"]
    monkeypatch.delattr(cr_claude, "call_effort")
    with clone._renderer_effort():                         # before 106: no stage asks for an effort at all
        pass


def test_a_3d_renderers_plates_are_backdrops_in_the_videos_shape():
    """Lucas the Spider composites CG characters over real garden photography: a 3D renderer that asks for plates gets
    backdrops for 3D characters in its own look, vertical for a Shorts channel, never "a 2D animation, wide 16:9"."""
    man = {"plates": {"draw": True, "prompt": "photoreal macro garden photography, shallow depth of field"}}
    tall = a2d.plate_prompt({"prompt": "a flower pot on a patio"}, {"dims": 3, "width": 1080, "height": 1920}, man)
    assert "composited over" in tall and "9:16" in tall and "photoreal macro garden" in tall and "2D" not in tall
    wide = a2d.plate_prompt({"prompt": "a street"}, {"width": 1920, "height": 1080}, None)
    assert "2D animation" in wide and "16:9" in wide, "2D renderers' plates as before"


def test_a_study_judges_a_renderer_composited_over_pictures_on_its_characters(home3d, tmp_path):
    composited = BOX.replace("plates: { draw: false },", "plates: { draw: true, prompt: 'garden photography' },")
    scene = {**SCENE, "stills": [0.5, 1.5, 2.5]}
    answer = "```javascript\n" + composited + "\n```\n```json\n" + json.dumps(scene) + "\n```"
    refs = tsm_frames(tmp_path / "refs", 9)
    asked = []

    def judge(references, candidates, **kw):
        asked.append(kw)
        return {"candidates": [{"overall": 7}] * len(candidates)}
    result = _chromium(lambda: a2d.build(tmp_path / "op", {"dims": 3, "width": 540, "height": 960}, {}, see=lambda p, i:
                                         answer, judge=judge, references=refs, home=home3d, rounds=2, log=lambda m: None))
    assert result["state"] == "passed" and asked[0] == {} and asked[1:] == [{"plates": True}] * 2, \
        "the channel's own frames are judged as they are; the drafts' stand-in backgrounds are set aside"


def test_music_heard_but_not_measured_still_gets_the_channels_own_score():
    """@gneef's clone had no music: its Shorts' quiet bed under constant speech couldn't be measured, so the style fell
    back to a stock library this Studio couldn't reach. Music the look heard is the channel's own score now."""
    sound = {"music": {"value": "story score", "share": 1.0, "level": None, "bed_in_pauses_db": None,
                       "genres": ["upbeat electronic, dramatic orchestral tension"], "moods": ["playful", "tense", "heroic"],
                       "instruments": ["synthesizer", "strings", "percussion"], "under_voice": "quiet", "beat": "driving"},
             "sfx": {"value": "sparse", "kinds": ["stone thud", "cartoon scream", "countdown beep"]}}
    plan = cm.plan_style(tsm.profile({"generated": 0.9}, **{"sound__music": sound["music"], "sound__sfx": sound["sfx"]}),
                         tsm.STYLES)
    snd = plan["overrides"]["look"]["sound"]
    assert snd["music"] is True and snd["music_rel_db"] == -22.0 and snd["score"]["instruments"][:2] == ["synthesizer", "strings"]
    assert snd["score"]["genres"] == ["upbeat electronic, dramatic orchestral tension"] and snd["score"]["beat"] == "driving"
    silent = cm.plan_style(tsm.profile({"generated": 0.9}, **{"sound__music": {"value": "none", "level": None, "share": 0}}),
                           tsm.STYLES)["overrides"]["look"]["sound"]
    assert "score" not in silent, "no music heard, none invented"


def test_a_coded_styles_cost_says_its_own_music():
    three = {"look": {"animated2d": {"dims": 3, "renderer": {"manifest": {"plates": {"draw": False}}}},
                      "sound": {"music": True, "score": {"genres": ["electronic"]}}}}
    assert a2d.cost_note(three) == ("Your voice, plus its own music, about $0.12 a piece (one or two a short video): "
                                    "every scene is rendered in 3D on this computer.")
    three["look"]["sound"]["music"] = False
    assert a2d.cost_note(three) == "Your voice only: every scene is rendered in 3D on this computer."


def test_round_files_belong_to_their_build(tmp_path):
    out = tmp_path / "renderer"
    (out / "round-1").mkdir(parents=True)
    (out / "round-1.txt").write_text("draft for key aaaa")
    (out / "control.json").write_text(json.dumps({"key": "aaaa"}))
    a2d._own_rounds(out, "aaaa")
    assert (out / "round-1.txt").is_file() and (out / "key").read_text() == "aaaa", "a build from before the key file"
    a2d._own_rounds(out, "bbbb")
    assert not (out / "round-1.txt").exists() and (out / "old-aaaa" / "round-1.txt").is_file()
    assert (out / "old-aaaa" / "round-1").is_dir() and (out / "key").read_text() == "bbbb"
    (out / "round-1.txt").write_text("draft for key bbbb")
    a2d._own_rounds(out, "bbbb")
    assert (out / "round-1.txt").read_text() == "draft for key bbbb"


def tsm_frames(folder, n):
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for k in range(n):
        p = folder / f"vid{k % 3}_{k // 3}.jpg"
        Image.new("RGB", (180, 320), (40 + k * 10, 60, 90)).save(p)
        out.append(p)
    return out


def test_a_vertical_style_renders_its_videos_1080_by_1920():
    import cr_video_cancel
    from types import SimpleNamespace
    studio = SimpleNamespace(styles=SimpleNamespace(STYLES={"cr_clone_short": {"look": {"aspect": "9:16"}},
                                                            "cr_clone_long": {"look": {}}}))
    assert cr_video_cancel.video_size(studio, "cr_clone_short") == {"VIDEO_W": "1080", "VIDEO_H": "1920"}
    assert cr_video_cancel.video_size(studio, "cr_clone_long") == {} == cr_video_cancel.video_size(studio, "nope")


def test_a_3d_style_never_falls_back_to_the_2d_kit(monkeypatch, tmp_path):
    info = {"look": {"visuals": a2d.VISUALS3D, "animated2d": {"dims": 3}}}
    monkeypatch.setattr(a2d, "available3d", lambda home=None: False)
    assert "3D kit" in a2d.renderer_problem(info, tmp_path)
    monkeypatch.setattr(a2d, "available3d", lambda home=None: True)
    assert "no 3D renderer yet" in a2d.renderer_problem(info, tmp_path)
    with pytest.raises(a2d.AnimationStopped, match="no 3D renderer"):
        a2d.renderer_for(info, tmp_path)
    assert a2d.cost_note({"look": {"animated2d": {"dims": 3, "renderer": {"manifest": {"plates": {"draw": False}}}}}}) \
        == "Your voice only: every scene is rendered in 3D on this computer."
    film = a2d.film_spec({"duration": 4, "shots": [], "actions": [], "fx": []}, {"cast": {}, "sets": {}},
                         {"dims": 3, "captions": True}, {}, [], size=(1080, 1920))
    assert (film["dims"], film["width"], film["height"], film["captions"]) == (3, 1080, 1920, True)


def test_the_builder_ships_the_3d_kit(tmp_path):
    from scripts import build_frontier_admin as builder
    kit = tmp_path / "data/frontier/animated3d"
    kit.mkdir(parents=True)
    for name in ("core3d.js", "example3d.js", "AUTHORING.md"):
        (kit / name).write_text("x", encoding="utf-8")
    assert {dest for _, dest in builder.private_animated3d_resources(tmp_path)} == {
        "animated3d/core3d.js", "animated3d/example3d.js", "animated3d/AUTHORING.md"}
    (kit / "notes.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected"):
        builder.private_animated3d_resources(tmp_path)
    (kit / "notes.md").unlink()
    (kit / "example3d.js").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        builder.private_animated3d_resources(tmp_path)
    assert json.dumps(builder.ANIMATED3D_FILES)
