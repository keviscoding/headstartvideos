"""Looking at references before a study, the art director, and the study applying what was learned."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_art as art  # noqa: E402
import cr_style_clone as clone  # noqa: E402
import cr_style_look as look  # noqa: E402

VIDEO, OTHER = "abcdefghijk", "mnopqrstuvw"
STICK_LOG = {"narration": "voiceover",
             "shots": [{"t": "0:00", "len_s": 3, "source": "stick_figure", "style": "flat vector, bold outlines",
                        "motion": "still", "polish": "clean", "who": "Pip", "audio": "narration", "text": "none"}],
             "music": {"share": 0.1, "role": "none"}, "sfx": {"density": "sparse", "kinds": ["pop"]},
             "voice": {"gender": "male", "age": "adult", "tone": "dry", "pace": "fast"},
             "look": {"grade": "bright"}, "art_style": "Stick figures with bold outlines on flat colour backgrounds.",
             "recurring": [{"name": "Pip", "look": "stick figure in a red cap", "role": "mascot"}],
             "recognisable": ["red-cap stick mascot"], "cta": []}


@pytest.fixture
def home(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "stories.json").write_text(json.dumps({
        "name": "stories", "look": {"character": "THE MAIN CHARACTER is the man", "character_image": "characters/x.png"},
        "features": {"offered": ["depth"], "on": ["depth"]}, "pacing": {"photo_every_s": 5.5}}))
    (tmp_path / "styles" / "documentary.json").write_text(json.dumps({"name": "documentary", "look": {},
                                                                      "features": {"offered": ["youtube"]}}))
    previews = clone._folder(tmp_path) / ".previews"
    previews.mkdir(parents=True)
    rows = [{"id": v, "title": "A video", "channel": "Pip Explains", "duration_seconds": 600.0, "eligible": True}
            for v in (VIDEO, OTHER)]
    (previews / ("pv_" + "a" * 64 + ".json")).write_text(json.dumps({"candidates": rows, "created_at": time.time(), "reference_url": "https://www.youtube.com/@pip/videos"}))
    return tmp_path


def fake_measure(path, start=0.0, keyframes=False):
    import numpy as np
    shots = [{"t0": start + i * 2.0, "t1": start + i * 2.0 + 2.0, "in": "cut", "kind": "still", "pops": 0}
             for i in range(20)]
    out = {"schema": 1, "start": start, "seconds": 40.0, "shots": shots,
           "edit": {"median_shot_s": 2.0, "motion_share": {"still": 1.0}},
           "look": {"luma": 200, "palette": [], "flat": 0.6},
           "sound": {"seconds": 40, "speech_s": 30, "filled_pauses_share": 0.0, "bed_in_pauses_db": -60}}
    if keyframes:
        out["_keyframes"] = [(start, np.zeros((72, 128, 3), np.uint8))]
    return out


def run_look(home, **over):
    started = look.start(home, "pv_" + "a" * 64, [VIDEO], spawn=lambda h, i: None)
    calls = []

    def call(path, payload=None):
        calls.append(path)
        return {"job": "sw_1", "status": "done", "log": STICK_LOG} if payload is not None else {}

    def download(vid, a, b, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"0" * 60_000)
        return dest
    kwargs = dict(call=call, download=download, transcript=lambda v, w: [(1.0, "you wake up in a world of stick men")],
                  measure=fake_measure)
    kwargs.update(over)
    return started, look.run(home, started["look_id"], **kwargs), calls


def test_a_look_measures_watches_and_proposes_a_recipe_without_claude(home):
    started, done, calls = run_look(home)
    assert started["state"] == "running" and done["state"] == "done"
    assert done["mapping"]["base"] == "stories"
    assert done["profile"]["pictures"]["art_style"]["value"].startswith("Stick figures")
    assert any(r["label"] == "A new picture every" for r in done["mapping"]["settings"])
    assert calls and all(c.startswith("style/watch") for c in calls), "only ChannelRecipe's watcher is called"
    folder = look.folder(home, done["look_id"])
    assert (folder / "sheets" / f"{VIDEO}.jpg").is_file()
    assert sorted(p.name for p in (folder / "work" / "media").iterdir()) == [f"{VIDEO}_0_180.mp4", f"{VIDEO}_270_360.mp4"]


def test_the_same_references_reuse_a_finished_look(home):
    _, done, _ = run_look(home)
    spawned = []
    again = look.start(home, "pv_" + "a" * 64, [VIDEO], spawn=lambda h, i: spawned.append(i))
    assert again["look_id"] == done["look_id"] and again["state"] == "done" and not spawned


def test_a_failed_download_is_reported_and_the_rest_carries_on(home):
    def broken(vid, a, b, dest):
        raise RuntimeError("Sign in to confirm you're not a bot")
    _, done, _ = run_look(home, download=broken)
    assert done["state"] == "done", "Gemini's watching still gives a profile"
    assert done["profile"]["edit"]["median_shot_s"]["value"] is None
    assert len(done["not_measured"]) == 1 and "none of it could be measured" in done["not_measured"][0]
    assert "asked this computer to sign in" in done["not_measured"][0]


def test_a_download_that_fails_once_is_tried_again(home):
    tries = []

    def flaky(vid, a, b, dest):
        tries.append(a)
        if tries.count(a) == 1:
            raise RuntimeError("ERROR: ffmpeg exited with code 8")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"0" * 60_000)
        return dest
    _, done, _ = run_look(home, download=flaky)
    assert done["not_measured"] == [] and done["profile"]["edit"]["median_shot_s"]["value"] == 2.0
    assert len(tries) == 4, "each of the two parts once more, never more"


def test_only_previewed_references_can_be_looked_at(home):
    with pytest.raises(ValueError):
        look.start(home, "pv_" + "a" * 64, ["zzzzzzzzzzz"], spawn=lambda h, i: None)
    with pytest.raises(ValueError):
        look.start(home, "pv_" + "b" * 64, [VIDEO], spawn=lambda h, i: None)
    with pytest.raises(ValueError):
        look.folder(home, "../escape")


def test_a_look_that_stopped_says_so(home):
    started = look.start(home, "pv_" + "a" * 64, [VIDEO], spawn=lambda h, i: None)
    path = look.folder(home, started["look_id"]) / "look.json"
    saved = json.loads(path.read_text())
    saved["updated_at"] = time.time() - 1000
    path.write_text(json.dumps(saved))
    assert look.status(home, started["look_id"])["state"] == "failed"


# ── the art director ─────────────────────────────────────────────────────────────────────────────────────

ANSWER = {"description": "Dry history explainers drawn as stick figures by Pip Explains.", "tagline": "History, drawn",
          "still_suffix": " ".join(["Flat vector stick figures with bold black outlines and round white heads,"] * 4),
          "avoid": "photographs, 3D renders, gradients",
          "cast": "THE CAST — Toby: a stick figure in a red cap and a blue scarf.",
          "mascot": {"name": "Toby", "look": "stick figure in a red cap", "role": "host"},
          "video_motion": "figures slide as flat cut-outs; the background never moves",
          "script_voice": ["Second person, present tense.", "Deadpan jokes after hard facts."],
          "editor_notes": ["A new picture every sentence."], "hook_notes": "Open on the absurd moment.",
          "body_notes": "Steady cuts every two seconds."}


def test_the_art_directors_answer_becomes_style_settings_without_the_references_name():
    out = art.direct(lambda prompt, images: json.dumps(ANSWER), {"recognisable": []}, {"settings": []},
                     title="Why it sucks to be a Viking", brief="", base="stories", excerpt="", sheets=[],
                     banned=["Pip Explains"], log=lambda m: None)
    ov = out["overrides"]
    assert "Pip Explains" not in json.dumps(ov)
    assert ov["look"]["still_suffix"].endswith("Avoid: photographs, 3D renders, gradients." + art.LETTERING)
    assert "Toby (host)" in ov["look"]["character"] and ov["script_notes"][0] == "Second person, present tense."
    assert ov["pacing"]["hook_notes"] == "Open on the absurd moment."


def test_a_thin_or_broken_answer_changes_nothing():
    for raw in ("not json", json.dumps({**ANSWER, "still_suffix": "flat"})):
        assert art.direct(lambda p, i: raw, {}, {}, title="t", brief="", base="stories", excerpt="", sheets=[],
                          banned=[], log=lambda m: None) == {}


def test_the_prompt_carries_the_profile_and_the_candid_rule_for_photos():
    mapping = {"settings": [], "notes": [{"for": "still_suffix", "polish_rule": "candid: everyday phone"}]}
    text = art.prompt({"recognisable": ["older butcher host"], "pictures": {"polish": {"value": "clean"}}}, mapping,
                      title="7 cheap cuts", brief="", base="avatar-premium", excerpt="I've been a butcher 40 years")
    assert "older butcher host" in text and "everyday phone-camera" in text and "7 cheap cuts" in text


# ── the study applying it ─────────────────────────────────────────────────────────────────────────────────

def test_a_study_after_a_look_applies_the_learned_settings_and_the_art_directors_words(home):
    _, done, _ = run_look(home)
    folder = clone._folder(home) / ("op_" + "c" * 24)
    folder.mkdir(parents=True)
    spec = {"look_id": done["look_id"], "base_style": "stories", "answers": {}, "references": [{"id": VIDEO}],
            "title": "t", "brief": ""}
    material = clone._look_material(folder, spec)
    assert set(material["media"]) == {VIDEO} and material["channels"] == ["Pip Explains"]
    clone._art_director(folder, spec, material, lambda p, i: json.dumps(ANSWER), lambda m: None)
    assert (folder / "art-checkpoint.json").is_file()
    clone._art_director(folder, spec, material, lambda p, i: pytest.fail("never asked twice"), lambda m: None)
    built = {"name": "cr_clone_x", "look": {"character": "THE MAIN CHARACTER is the man",
                                            "character_image": "characters/x.png"},
             "pacing": {"photo_every_s": 5.5}, "cr_style_clone": {"operation_id": folder.name}}
    style = clone._apply_learned(folder, spec, built, lambda m: None)
    assert style["pacing"]["photo_every_s"] == 2.0
    assert "character_image" not in style["look"] and "Toby" in style["look"]["character"]
    assert style["look"]["still_suffix"].startswith(" Flat vector stick figures")
    assert style["cr_style_clone"]["learned_v2"]["art_director"] is True
    assert built["pacing"]["photo_every_s"] == 5.5, "the built style is not changed in place"


def test_a_saved_or_improved_study_keeps_how_it_was_approved_and_its_renderer(home, monkeypatch):
    """The 107 review's blocker: Save completed style and Improve graphics run finish_original, which plans the study
    again. A study with no renderer (approved before 107, or instead of code) was planned as code: a documentary drew
    the kit's stick fights. A coded style's improved copy lost its renderer the same way."""
    import hashlib
    import cr_animated2d as a2d
    import cr_style_map as cm
    import cr_style_refine as refine
    monkeypatch.setattr(a2d, "available", lambda home=None: True)
    monkeypatch.setattr(a2d, "available3d", lambda home=None: True)
    _, done, _ = run_look(home)
    folder = clone._folder(home) / ("op_" + "d" * 24)
    folder.mkdir(parents=True)
    spec = {"look_id": done["look_id"], "base_style": "stories", "answers": {}, "references": [{"id": VIDEO}],
            "title": "t", "brief": ""}
    built = {"name": "cr_clone_x", "look": {}, "cr_style_clone": {"operation_id": folder.name}}
    kept = clone._apply_learned(folder, spec, built, lambda m: None)
    assert kept["look"].get("visuals") not in a2d.PLUGINS
    assert not kept["cr_style_clone"]["learned_v2"]["route"].get("coded"), "the way it was approved: pictures"
    coded = clone._apply_learned(folder, {**spec, "renderer": "2d"}, built, lambda m: None)
    assert coded["look"]["visuals"] in a2d.PLUGINS and coded["cr_style_clone"]["learned_v2"]["route"]["coded"]
    assert coded["look"]["animated2d"]["renderer"]["state"] == "missing"
    full = {**built, "features": {"offered": ["youtube", "stock", "ai_images", "sound", "avatar"], "on": ["youtube"]}}
    only = clone._apply_learned(folder, {**spec, "renderer": "2d"}, full, lambda m: None)["features"]
    assert only["offered"] == ["sound"] and only["on"] == ["sound"], "a coded style offers sound design only"
    with pytest.raises(a2d.AnimationStopped):
        a2d.renderer_for(coded, home)                       # never the kit's stick fights in its place
    # Improve graphics on a coded style: the copy carries the source's renderer as last saved (Style Lab's included)
    code = "Kit.renderer({name: 'pip', frame() {}});"
    source = home / "assets" / "styles" / "learned" / "cr_clone_src"
    source.mkdir(parents=True)
    (source / "renderer-v2.js").write_text(code)
    record = {"file": "styles/learned/cr_clone_src/renderer-v2.js", "sha256": hashlib.sha256(code.encode()).hexdigest(),
              "version": 2, "state": "passed", "from": "style_lab", "manifest": {"name": "pip"}}
    (home / "styles").mkdir(exist_ok=True)
    original = {"spec": {**spec, "style_id": "cr_clone_src", "renderer": "2d"}}
    clone._write(home / "styles" / "cr_clone_src.json", {"look": {"visuals": a2d.VISUALS, "animated2d": {"renderer": record}}})
    copy_spec = json.loads(json.dumps(original["spec"]))
    carried = refine._renderer_source(home, original, copy_spec)
    assert carried == record and copy_spec["renderer"] == "2d"
    clone._write(folder / "renderer-carried.json", {"schema": 1, "record": carried})
    improved = clone._apply_learned(folder, copy_spec, {**built, "name": "cr_clone_new"}, lambda m: None)
    got = improved["look"]["animated2d"]["renderer"]
    assert got["file"] == "styles/learned/cr_clone_new/renderer-v1.js" and got["sha256"] == record["sha256"]
    assert a2d.renderer_for(improved, home)[0] == code
    # pictures chosen instead of its renderer stay chosen in the improved copy
    clone._write(home / "styles" / "cr_clone_src.json", {"look": {}, "cr_style_clone": {"pictures_instead": {
        "answers": {"made": cm.MADE_OTHER, "animation": cm.ANIMATED_PICTURES}}}})
    copy_spec = json.loads(json.dumps(original["spec"]))
    assert refine._renderer_source(home, original, copy_spec) is None
    assert "renderer" not in copy_spec and copy_spec["answers"]["made"] == cm.MADE_OTHER
    (folder / "renderer-carried.json").unlink()
    pictures = clone._apply_learned(folder, copy_spec, built, lambda m: None)
    assert pictures["look"].get("visuals") not in a2d.PLUGINS


def test_the_study_spec_checks_the_look_and_the_answers(home, monkeypatch):
    _, done, _ = run_look(home)
    payload = {"preview_id": "pv_" + "a" * 64, "reference_ids": [VIDEO], "base_style": "stories", "label": "Mine",
               "title": "A video", "acknowledge_costs": True, "look_id": done["look_id"], "answers": {}}
    assert clone._spec(home, payload)["look_id"] == done["look_id"]
    with pytest.raises(ValueError):
        clone._spec(home, {**payload, "reference_ids": [OTHER]})
    with pytest.raises(ValueError):
        clone._spec(home, {**payload, "answers": {"host": "Make me rich"}})
    with pytest.raises(ValueError):
        clone._spec(home, {**{k: v for k, v in payload.items() if k != "look_id"}, "answers": {"music": "No music"}})


def test_a_3d_channel_is_asked_how_its_3d_is_made_and_a_medium_studio_cant_make_is_accepted_in_words(home):
    """An @ifologyfilms clone drew stills of characters the channel animates in 3D, and was called fixed. Now the look
    screen asks how the 3D is made (Studio's guess first), the answer decides, and drawn pictures in place of 3D
    animation start only when the creator ticks that they understand."""
    import cr_style_map
    _, done, _ = run_look(home)
    path = look.folder(home, done["look_id"]) / "look.json"
    saved = json.loads(path.read_text())
    saved["profile"]["pictures"]["sources"]["families"] = {"3d": 0.85, "graphic": 0.15}
    saved["profile"]["pictures"]["art_style"]["value"] = "Low-poly 3D: black sphere-headed stick figures, dot eyes."
    saved["profile"]["pictures"]["people_3d"] = {"value": {"designed": 1.0}, "confidence": 0.7, "from": ["watched"]}
    saved.pop("mapping", None)
    path.write_text(json.dumps(saved))
    built = look.status(home, done["look_id"])["mapping"]
    assert built["route"]["engine"] == "designed" and "medium_gap" not in built["route"], \
        "Studio 79 builds ball-headed casts in 3D: nothing to accept"
    payload = {"preview_id": "pv_" + "a" * 64, "reference_ids": [VIDEO], "base_style": "stories", "label": "Mine",
               "title": "A video", "acknowledge_costs": True, "look_id": done["look_id"], "answers": {},
               "visual_mode": "mixed"}
    assert clone._spec(home, payload)["look_id"] == done["look_id"], "pictures by the creator's own choice: no gate"
    saved["profile"]["pictures"]["art_style"]["value"] = "Roblox-style blocky avatars with yellow block heads, decal faces."
    path.write_text(json.dumps(saved))
    shown = look.status(home, done["look_id"])["mapping"]
    question = next(q for q in shown["questions"] if q["id"] == "medium")
    assert question["options"][0] == cr_style_map.MEDIUM_DESIGNED, "Studio's guess first"
    assert "can't animate these characters in 3D yet" in shown["route"]["medium_gap"]
    payload = {"preview_id": "pv_" + "a" * 64, "reference_ids": [VIDEO], "base_style": "stories", "label": "Mine",
               "title": "A video", "acknowledge_costs": True, "look_id": done["look_id"], "answers": {},
               "visual_mode": "mixed"}
    with pytest.raises(ValueError, match="Tick that you understand"):
        clone._spec(home, payload)
    assert clone._spec(home, {**payload, "accept_medium": True})["accept_medium"] is True
    faceless = {**payload, "answers": {"medium": cr_style_map.MEDIUM_FACELESS}}
    assert clone._spec(home, faceless)["look_id"] == done["look_id"], "the creator's own answer needs no gate"
    graphics = {**payload, "visual_mode": "reference_only"}
    assert clone._spec(home, graphics)["look_id"] == done["look_id"], "graphics only draws no pictures: no gate"



def test_a_channel_that_animates_its_drawn_characters_is_asked_and_pictures_in_their_place_are_accepted_in_words(home):
    """A @DartAnimates clone (stick-figure fights) made AI stills with camera moves: the look was right, and nothing
    moved. Now the look screen asks (Studio's guess first), and pictures in place of animation start only when the
    creator ticks that they understand, or chose pictures themselves."""
    import cr_style_map
    _, done, _ = run_look(home)
    path = look.folder(home, done["look_id"]) / "look.json"
    saved = json.loads(path.read_text())
    saved["profile"]["pictures"]["sources"].update(value={"stick_figure": 0.88, "text_card": 0.04},
                                                   families={"generated": 0.88, "graphic": 0.04})
    saved["profile"]["motion"]["classes"] = {"value": {"moving": 0.93, "still": 0.07}, "confidence": 0.96,
                                             "from": ["measured", "watched"]}
    saved["profile"]["format"]["narration"] = {"value": "none", "confidence": 0.8, "from": ["watched"]}
    saved["mapping"]["router"] = 79                       # planned by an older Studio: planned again
    path.write_text(json.dumps(saved))
    shown = look.status(home, done["look_id"])["mapping"]
    question = next(q for q in shown["questions"] if q["id"] == "animation")
    assert question["options"][0] == cr_style_map.ANIMATED_CODE, "Studio's guess first"
    assert shown["route"]["medium_gap"] == cr_style_map.ANIMATION_GAP
    payload = {"preview_id": "pv_" + "a" * 64, "reference_ids": [VIDEO], "base_style": "stories", "label": "Mine",
               "title": "Gojo vs Sukuna", "acknowledge_costs": True, "look_id": done["look_id"], "answers": {},
               "visual_mode": "mixed"}
    with pytest.raises(ValueError, match="Tick that you understand"):
        clone._spec(home, payload)
    with pytest.raises(ValueError, match="Tick that you understand"):
        clone._spec(home, {**payload, "answers": {"animation": cr_style_map.ANIMATED_CODE}})
    assert clone._spec(home, {**payload, "accept_medium": True})["accept_medium"] is True
    pictures = {**payload, "answers": {"animation": cr_style_map.ANIMATED_PICTURES}}
    assert clone._spec(home, pictures)["answers"]["animation"] == cr_style_map.ANIMATED_PICTURES, \
        "the creator's own answer needs no gate"

# ── style samples and the character sheet ─────────────────────────────────────────────────────────────────

def saved_v2_style(home, look_id, cast="THE CAST — Toby: stick figure in a red cap."):
    import cr_style_anchor  # noqa: F401
    op = "op_" + "d" * 24
    folder = clone._folder(home) / op
    folder.mkdir(parents=True)
    (home / "styles" / "cr_clone_toby.json").write_text(json.dumps({
        "name": "cr_clone_toby", "look": {"still_suffix": " Flat vector stick figures.", "character": cast},
        "cr_style_clone": {"operation_id": op, "learned_v2": {"look_id": look_id}}}))
    clone._write(folder / "operation.json", {"operation_id": op, "spec": {"style_id": "cr_clone_toby"}})
    frames = look.folder(home, look_id) / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / f"{VIDEO}_0.jpg").write_bytes(b"jpg")
    return op


def test_samples_are_drawn_from_reference_frames_and_the_chosen_one_becomes_the_style_drawing(home):
    import cr_style_anchor as anchor
    _, done, _ = run_look(home)
    op = saved_v2_style(home, done["look_id"])
    drawn, uploaded = [], []

    def generate(prompt, dest, label, ref_urls=None):
        drawn.append((prompt, ref_urls))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"picture")

    def upload(path):
        uploaded.append(Path(path).name)
        return "https://files.example/" + Path(path).name
    state = anchor.start(home, op, generate=generate, upload=upload, thread=False)
    assert state["samples"] == [0, 1, 2] and len(drawn) == 3
    assert all(p.startswith(anchor.STYLE_NOTE) and p.endswith("Flat vector stick figures.") for p, _ in drawn)
    assert all(r == ["https://files.example/" + f"{VIDEO}_0.jpg"] for _, r in drawn)
    chosen = anchor.choose(home, op, 1, generate=generate, upload=upload)
    style = json.loads((home / "styles" / "cr_clone_toby.json").read_text())
    assert style["look"]["style_image"] == "styles/learned/cr_clone_toby/style.jpg"
    assert style["look"]["character_image"] == "styles/learned/cr_clone_toby/character.png"
    assert drawn[-1][0].startswith(anchor.STYLE_NOTE + anchor.SHEET) and "Toby" in drawn[-1][0]
    assert chosen["chosen"] == 1 and chosen["style_image"] and chosen["character"]
    saved = json.loads((clone._folder(home) / op / "operation.json").read_text())
    assert saved["style_sha256"] == clone._file_hash(home / "styles" / "cr_clone_toby.json"), \
        "the study still recognises its saved style"


def test_a_style_without_a_cast_gets_no_character_sheet_and_unknown_samples_are_refused(home):
    import cr_style_anchor as anchor
    _, done, _ = run_look(home)
    op = saved_v2_style(home, done["look_id"], cast="")
    gen = lambda prompt, dest, label, ref_urls=None: Path(dest).parent.mkdir(parents=True, exist_ok=True) or Path(dest).write_bytes(b"x")
    anchor.start(home, op, generate=gen, upload=lambda p: "https://f/x.jpg", thread=False)
    with pytest.raises(ValueError):
        anchor.choose(home, op, 7, generate=gen, upload=lambda p: "https://f/x.jpg")
    anchor.choose(home, op, 0, generate=lambda *a, **k: pytest.fail("no cast, no sheet"), upload=lambda p: "u")
    assert "character_image" not in json.loads((home / "styles" / "cr_clone_toby.json").read_text())["look"]


def test_samples_and_the_sheet_are_drawn_with_the_style_picture_model(home):
    import cr_style_anchor as anchor
    _, done, _ = run_look(home)
    op = saved_v2_style(home, done["look_id"])
    path = home / "styles" / "cr_clone_toby.json"
    style = json.loads(path.read_text())
    style["look"]["image_model"] = "nano-banana-2-1"
    path.write_text(json.dumps(style))
    models = []

    def gen(prompt, dest, label, ref_urls=None, model=None):
        models.append(model)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"x")
    anchor.start(home, op, generate=gen, upload=lambda p: "https://f/x.jpg", thread=False)
    anchor.choose(home, op, 0, generate=gen, upload=lambda p: "https://f/x.jpg")
    assert models == ["nano-banana-2-1"] * 4, "three samples and the character sheet"


def test_a_creator_who_uploads_their_own_character_gets_no_drawn_sheet(home):
    import io
    import cr_style_anchor as anchor
    from PIL import Image
    _, done, _ = run_look(home)
    op = saved_v2_style(home, done["look_id"])
    path = clone._folder(home) / op / "operation.json"
    saved = json.loads(path.read_text())
    saved["spec"]["answers"] = {"host": "Yes, I'll upload mine"}
    path.write_text(json.dumps(saved))
    gen = lambda prompt, dest, label, ref_urls=None: Path(dest).parent.mkdir(parents=True, exist_ok=True) or Path(dest).write_bytes(b"x")
    anchor.start(home, op, generate=gen, upload=lambda p: "https://f/x.jpg", thread=False)
    chosen = anchor.choose(home, op, 0, generate=lambda *a, **k: pytest.fail("no sheet: they upload theirs"),
                           upload=lambda p: "u")
    assert chosen["upload_character"] and not chosen["character"] and "upload your character" in chosen["message"]
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), (240, 240, 240)).save(buf, "PNG")
    done_ = anchor.upload_character(home, op, buf.getvalue())
    style = json.loads((home / "styles" / "cr_clone_toby.json").read_text())
    assert done_["character"] and style["look"]["character_image"] == "styles/learned/cr_clone_toby/character.png"
    assert style["look"]["character"].startswith("THE CAST — Toby: exactly the character in the character reference")
    with pytest.raises(ValueError, match="larger picture"):
        tiny = io.BytesIO()
        Image.new("RGB", (100, 100)).save(tiny, "PNG")
        anchor.upload_character(home, op, tiny.getvalue())


def test_samples_are_only_for_styles_saved_by_a_new_study(home):
    import cr_style_anchor as anchor
    with pytest.raises(RuntimeError):
        anchor.status(home, "op_" + "e" * 24)


def test_a_long_picture_style_is_cut_at_a_sentence_and_the_avoid_list_reads_cleanly():
    long = " ".join(f"Sentence {i} describes the flat vector style in some detail." for i in range(40))
    out = art.check({**ANSWER, "still_suffix": long, "avoid": "No photorealism, no 3D"}, banned=[])
    assert out["still_suffix"].endswith(".") and len(out["still_suffix"]) <= 1400
    suffix = art.overrides(out)["look"]["still_suffix"]
    assert "Avoid: photorealism, no 3D." in suffix
    assert suffix.endswith(art.LETTERING) and "never an invented caption, date" in suffix, \
        "an archival picture never gets a made-up caption or date"


def test_the_art_director_gives_the_channel_its_own_fallback_b_roll():
    out = art.check({**ANSWER, "broll": ["counting cash close up", "bank building exterior", "x", "Pip Explains logo shot"]},
                    banned=["Pip Explains"])
    assert out["broll"] == ["counting cash close up", "bank building exterior", "the reference logo shot"]
    assert art.overrides(out)["stock_queries"] == out["broll"]


def test_a_candid_photo_style_never_asks_for_glossy_studio_pictures():
    glossy = ("Photorealistic phone photos of an older butcher in a home kitchen, commercial supermarket lighting, "
              "crisp focus, glossy finish, cinematic lighting, warm colours, everyday clutter on the counters.")
    out = art.check({**ANSWER, "still_suffix": glossy}, banned=[], candid=True)
    text = out["still_suffix"].lower()
    assert not any(w in text for w in ("commercial", "crisp", "glossy", "cinematic"))
    assert "natural light" in text and "deep focus" in text and "everyday clutter" in text
    assert "commercial" in art.check({**ANSWER, "still_suffix": glossy}, banned=[])["still_suffix"], \
        "only candid styles are rewritten"
    negated = ("Everyday phone photos of a home kitchen, mixed natural window light and ordinary overhead light, "
               "uneven exposure, no studio light, no shallow bokeh, deep focus throughout.")
    text = art.check({**ANSWER, "still_suffix": negated}, banned=[], candid=True)["still_suffix"]
    assert "no studio light" in text and "no natural light" not in text, "a negated phrase is never turned around"


def test_people_are_never_asked_for_as_lone_hands_or_backs():
    answer = {**ANSWER, "people": "Real, ordinary adults with natural proportions. Bystanders are often shown from "
                                  "behind or in part, such as hands or a back."}
    out = art.check(answer, banned=[], candid=True)
    assert out["people"] == "Real, ordinary adults with natural proportions."
    assert "lone limbs" in art.ART_PROMPT


def test_presenter_time_counts_talking_video_not_pictures_of_the_presenter():
    import cr_style_profile as prof
    log = {"shots": [{"len_s": 4, "source": "ai_video", "motion": "live_action", "who": "Frank",
                      "what": "butcher talking to camera"},
                     {"len_s": 6, "source": "ai_video", "motion": "live_action", "who": "Frank",
                      "what": "butcher slicing liver on board"},
                     {"len_s": 5, "source": "presenter", "motion": "still", "who": "Frank",
                      "what": "smiling butcher looking at camera"},
                     {"len_s": 5, "source": "ai_image", "motion": "still", "who": "", "what": "stew in a pot"}]}
    assert prof.talking_share(log, 20) == 0.2
    assert prof.talking_share({"shots": []}, 20) is None


def test_styles_built_on_a_niche_recipe_get_neutral_writing_and_storyboard_prompts(home):
    import cr_style_prompts as sp
    _, done, _ = run_look(home)
    folder = clone._folder(home) / ("op_" + "f" * 24)
    folder.mkdir(parents=True)
    spec = {"look_id": done["look_id"], "base_style": "stories", "answers": {}, "references": [{"id": VIDEO}],
            "title": "t", "brief": ""}
    clone._write(folder / "art-checkpoint.json", {"art": {"art": {"craft": "story"},
                                                          "overrides": {"description": "Dry stick-figure history."}}})
    built = {"name": "x", "prompts": {"outline": "money stories in levels", "script": "balances add up"},
             "look": {"scene_prompt": "a banking app showing -$480"}, "auto": {"v": 2}}
    style = clone._apply_learned(folder, spec, built, lambda m: None)
    assert style["prompts"]["outline"] == sp.OUTLINE.replace("[INSERT CHANNEL HERE]", "Dry stick-figure history.")
    assert "CHANNEL: Dry stick-figure history." in style["prompts"]["script"]
    assert style["look"]["scene_prompt"] == sp.SCENE and style["script_craft"] == "story"
    assert "auto" not in style
    assert "[INSERT TITLE HERE]" in sp.OUTLINE and "[INSERT TITLE HERE]" not in sp.SCRIPT
    assert "[INSERT OUTLINE HERE]" in sp.SCRIPT and "[INSERT TARGET LENGTH]" in sp.SCRIPT
    assert "[INSERT COUNT HERE]" in sp.SCENE and "[INSERT SCRIPT HERE]" in sp.SCENE
    assert sp.overrides("documentary") == {}, "format recipes keep their own writing"


def test_a_learned_style_names_its_kit_relative_to_the_studio_folder(tmp_path):
    import cr_style_clone
    home = tmp_path / "ChannelRecipe studio"
    style = {"look": {"genkit": {"manifest": str(home / "output/cr-style-clone/op_1/genkit/kit.json")},
                      "style_image": "styles/learned/x/style.jpg"},
             "auto": {"genome": {"learned": {"board": str(home / "output/cr-style-clone/op_1/reflearn/board.jpg")}}},
             "elsewhere": "/Users/someone/file.png"}
    home.mkdir()
    out = cr_style_clone._portable(style, home)
    assert out["look"]["genkit"]["manifest"] == "output/cr-style-clone/op_1/genkit/kit.json"
    assert out["auto"]["genome"]["learned"]["board"] == "output/cr-style-clone/op_1/reflearn/board.jpg"
    assert out["look"]["style_image"] == "styles/learned/x/style.jpg" and out["elsewhere"] == "/Users/someone/file.png"


def test_a_style_that_plays_real_clips_asks_its_script_to_mark_them():
    import cr_style_prompts as sp
    style = {"look": {"soundbites": {"quotes": 13}}, "prompts": {"outline": "Plan the beats.", "script": "Write it."}}
    sp.with_original_sound(style)
    assert "[CLIP: who says what, where]" in style["prompts"]["outline"]
    assert '"[[clip: who says what, in a few words]]"' in style["prompts"]["script"]
    before = dict(style["prompts"])
    sp.with_original_sound(style)
    assert style["prompts"] == before, "added once"
    plain = {"look": {"soundbites": {"soundups": 1}}, "prompts": {"outline": "Plan.", "script": "Write."}}
    assert sp.with_original_sound(plain)["prompts"] == {"outline": "Plan.", "script": "Write."}


def test_a_style_saved_with_an_older_storyboard_prompt_draws_with_todays():
    import cr_style_prompts as sp
    old = "You are the storyboard artist for a faceless YouTube video.\n\nCHANNEL: [INSERT CHANNEL HERE]\n\nDraw frames."
    style = {"look": {"scene_prompt": old}}
    engine = type("E", (), {})()
    engine.STYLE_INFO = {"stick": style, "builtin": {"look": {"scene_prompt": "A recipe's own storyboard prompt."}}}
    engine._scene_prompts = lambda s: (engine.STYLE_INFO[s]["look"]["scene_prompt"], "", "")
    sp.install(engine)
    assert "never a lone arm" in engine._scene_prompts("stick")[0]
    assert engine._scene_prompts("builtin")[0] == "A recipe's own storyboard prompt."


def test_a_learned_style_draws_with_the_storyboard_its_channel_needs():
    import cr_style_prompts as sp
    engine = type("E", (), {})()
    engine.STYLE_INFO = {"explainer": {"look": {"storyboard": "explain"}}, "story": {"look": {"storyboard": "literal"}},
                         "essay": {"look": {}}, "builtin": {"look": {"scene_prompt": "A recipe's own storyboard prompt."}}}
    engine._scene_prompts = lambda s: (engine.STYLE_INFO[s]["look"].get("scene_prompt") or "ENGINE ATMOSPHERE", "", "")
    sp.install(engine)
    explain = engine._scene_prompts("explainer")[0]
    assert explain.startswith("You are the storyboard artist") and "EXPLAIN, NEVER DECORATE" in explain
    assert "with a symbol" not in explain, "an explainer acts ideas out instead"
    literal = engine._scene_prompts("story")[0]
    assert "show the" in literal and "real moment" in literal and "EXPLAIN" not in literal
    assert engine._scene_prompts("essay")[0] == "ENGINE ATMOSPHERE", "a channel that never asked keeps the engine's"
    assert engine._scene_prompts("builtin")[0] == "A recipe's own storyboard prompt."


def test_a_3d_look_saved_before_gemini_judged_its_faces_is_watched_again_and_every_look_is_replanned(home):
    """A customer's ifology look saved on Studio 72 or older kept its old plan for 30 days: mannequins again."""
    _, done, _ = run_look(home)
    path = look.folder(home, done["look_id"]) / "look.json"
    saved = json.loads(path.read_text())
    saved["mapping"] = {"base": "3d", "route": {"engine": "threed"}}                   # an older Studio's plan (no router)
    path.write_text(json.dumps(saved))
    assert look.status(home, done["look_id"])["mapping"]["base"] == "stories", "planned by this Studio"
    saved["profile"]["pictures"]["sources"]["families"] = {"3d": 0.8, "graphic": 0.2}
    saved["profile"]["pictures"]["people_3d"] = {"value": {}, "confidence": 0, "from": []}
    path.write_text(json.dumps(saved))
    spawned = []
    look.start(home, "pv_" + "a" * 64, [VIDEO], spawn=lambda h, i: spawned.append(i))
    assert spawned == [done["look_id"]], "watched again, so Gemini judges the faces"
    saved["profile"]["pictures"]["people_3d"] = {"value": {"designed": 1.0}, "confidence": 0.7, "from": ["watched"]}
    saved.update(state="done")
    path.write_text(json.dumps(saved))
    look.start(home, "pv_" + "a" * 64, [VIDEO], spawn=lambda h, i: spawned.append(i))
    assert len(spawned) == 1, "a judged look is reused"


def test_the_art_director_measures_a_designed_cast_into_the_3d_engines_cast_spec():
    """Studio 79: on the designed route the art director's same call returns the cast spec the engine builds the
    channel's people from (checked value by value), and it lands in look.threed.designed."""
    mapping = {"settings": [], "route": {"engine": "designed", "why": "ball heads"}}
    text = art.prompt({"recognisable": []}, mapping, title="What if", brief="", base="3d", excerpt="")
    assert '"cast_3d"' in text and "ring" in text and "designed_track" in text
    answer = {**ANSWER, "cast_3d": {"plan": "orb", "head": {"size": 0.25, "color": "#050505", "rim": "#F5E5C0"},
                                    "face": {"set": "ring", "color": "#ffffff"}, "palette": ["#E8B320", "nope"],
                                    "cast": [{"name": "Pip Explains kid", "role": "lead", "top": "hoodie",
                                              "color": "#3E8C4A", "hat": {"kind": "cap", "color": "#E8B320"}}]}}
    out = art.direct(lambda prompt, images: json.dumps(answer), {"recognisable": []}, mapping, title="t", brief="",
                     base="3d", excerpt="", sheets=[], banned=["Pip Explains"], log=lambda m: None)
    spec = out["overrides"]["look"]["threed"]["designed"]
    assert spec["head"]["color"] == "#050505" and spec["palette"] == ["#E8B320"] and spec["from"] == "art director"
    assert spec["cast"][0]["name"] == "the reference kid" and spec["cast"][0]["hat"] == {"kind": "cap", "color": "#E8B320"}
    plain = art.direct(lambda prompt, images: json.dumps(ANSWER), {"recognisable": []}, {"settings": []}, title="t",
                       brief="", base="stories", excerpt="", sheets=[], banned=[], log=lambda m: None)
    assert "threed" not in plain["overrides"]["look"], "only a designed route gets a cast spec"


def test_an_animation_under_the_channels_bar_is_said_and_pictures_are_used_only_when_the_creator_asks(home):
    """Claude's renderer for an animated channel is judged against the channel's own frames: the creator is told its
    score and bar plainly, and Use pictures instead re-plans the saved style as pictures in their look, never by
    itself."""
    import cr_style_map
    _, done, _ = run_look(home)
    bridge = SimpleNamespace(load_settings=lambda: {"key": "cr_mcp_test", "url": "https://channelrecipe.com"})
    op = "op_" + "e" * 24
    folder = clone._folder(home) / op
    folder.mkdir(parents=True)
    style = {"name": "cr_clone_dart", "look": {"visuals": "cr_animated2d", "animated2d": {"renderer": {
        "state": "below_bar", "overall": 5.2, "bar": 6.5, "version": 1}}}, "cost": "Your voice only.",
        "cr_style_clone": {"operation_id": op, "learned_v2": {"look_id": done["look_id"],
                                                              "route": {"engine": "animated2d"}}}}
    target = home / "styles" / "cr_clone_dart.json"
    target.write_text(json.dumps(style))
    clone._write(folder / "operation.json", {
        "operation_id": op, "owner": clone._owner(bridge), "state": "complete", "phase": "complete",
        "spec": {"style_id": "cr_clone_dart", "look_id": done["look_id"], "base_style": "stories", "answers": {},
                 "reference_url": "https://www.youtube.com/@pip/videos", "references": [{"id": VIDEO}],
                 "budget_minutes": 22, "max_calls": 40, "label": "Dart", "title": "Gojo vs Sukuna", "brief": "",
                 "minutes": 1, "visual_mode": "mixed", "preview_id": "pv_" + "a" * 64, "name": "cr_clone_dart"},
        "style_sha256": clone._file_hash(target)})
    told = clone.renderer_verdict(style)
    assert told["offer_pictures"] and "5.2/10" in told["say"] and "6.5" in told["say"]
    assert "Use pictures instead" in told["say"]
    passed = clone.renderer_verdict({"look": {"visuals": "cr_animated2d", "animated2d": {"renderer": {
        "state": "passed", "overall": 7.1, "bar": 6.5}}}})
    assert "offer_pictures" not in passed and "7.1/10" in passed["say"]
    assert clone.renderer_verdict({"look": {}}) == {}, "a pictures style has no animation to report"
    shown = clone.status(home, op, bridge)
    # the verdict is said once, in the renderer panel (and the tools' what_to_tell_them); the headline is short
    assert shown["renderer"]["state"] == "below_bar" and shown["message"] == "Your style is saved."
    assert shown["renderer"]["say"] == told["say"] and shown["renderer"]["score"] == 5.2
    assert told["say"].count("/10") == 1, "one score"
    clone.use_pictures(home, op, bridge)
    after = json.loads(target.read_text())
    assert after["look"].get("visuals") != "cr_animated2d" and "animated2d" not in after["look"] and "cost" not in after
    assert after["cr_style_clone"]["pictures_instead"]["renderer"] == {"state": "below_bar", "overall": 5.2, "bar": 6.5}
    assert after["cr_style_clone"]["learned_v2"]["route"]["engine"] == "pictures"
    assert clone.renderer_verdict(after)["state"] == "pictures"
    saved = json.loads((folder / "operation.json").read_text())
    assert saved["style_sha256"] == clone._file_hash(target), "the study still recognises its saved style"
    assert cr_style_map.ANIMATED_PICTURES  # the plan used is the creator's "pictures" answer


def test_a_finished_look_suggests_the_closest_installed_styles_and_whether_one_can_be_tried_first(home):
    _, done, _ = run_look(home)
    rows = look.status(home, done["look_id"])["similar"]
    assert rows and rows[0]["id"] == "stories" and rows[0]["name"] == "2D Life Stories"
    assert rows[0]["use_url"] == "/?cr-preset=stories" and rows[0]["preview_url"].endswith("/stories.jpg")
    assert rows[0]["sample"]["can"] and rows[0]["sample"]["cost_usd"] <= 0.10
    footage = next((r for r in rows if r["id"] == "documentary"), None)
    assert footage is None or not footage["sample"]["can"] and "footage" in footage["sample"]["why_not"]
    assert done["shorts"] is False


def test_a_suggestion_that_breaks_never_stops_the_look(home, monkeypatch):
    import cr_style_map
    _, done, _ = run_look(home)
    monkeypatch.setattr(cr_style_map, "similar", lambda *a, **k: 1 / 0)
    again = look.status(home, done["look_id"])
    assert again["state"] == "done" and again["similar"] == []


def test_a_look_says_which_video_it_is_on_moves_only_forward_and_downloads_the_next_one_while_measuring(home,
                                                                                                         monkeypatch):
    """The owner's Windows test: 9 minutes for 3 references, the bar back to 10% for every video, no "video 2 of 3" and
    no time left; each section was re-encoded (--force-keyframes-at-cuts) and downloaded only after the last was
    measured."""
    import threading
    started = look.start(home, "pv_" + "a" * 64, [VIDEO, OTHER], spawn=lambda h, i: None)
    writes, events = [], []
    real_write = look._write

    def write(path, value):
        if Path(path).name == "look.json":
            writes.append(dict(value))
        return real_write(path, value)
    monkeypatch.setattr(look, "_write", write)
    clock = {"t": 1000.0}

    def now():
        clock["t"] += 10                                         # every step "takes" ten seconds
        return clock["t"]
    second_started = threading.Event()

    def download(vid, a, b, dest):
        events.append(("download", vid, a))
        if vid == OTHER:
            second_started.set()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"0" * 60_000)
        return dest

    def measure(path, start=0.0, keyframes=False):
        if VIDEO in Path(path).name:
            # the next video is downloaded while this one is measured (it waits here for at most 5 s otherwise)
            assert second_started.wait(5), "the next video's download had not started while this one was measured"
        events.append(("measure", Path(path).name))
        return fake_measure(path, start, keyframes)
    call = lambda path, payload=None: ({"job": "sw_1", "status": "done", "log": STICK_LOG} if payload is not None
                                       else {})
    done = look.run(home, started["look_id"], call=call, download=download, measure=measure, now=now,
                    transcript=lambda v, w: [(1.0, "you wake up in a world of stick men")])
    assert done["state"] == "done"
    assert [e[1] for e in events if e[0] == "download"] == [VIDEO, VIDEO, OTHER, OTHER], "in the order measured"
    running = [w for w in writes if w.get("state") == "running" and w.get("phase") in ("download", "measure")]
    assert {w["video"] for w in running} == {1, 2} and all(w["videos"] == 2 for w in running)
    assert any(w["message"].startswith("Video 2 of 2 · ") for w in running)
    percents = [w["percent"] for w in writes if "percent" in w]
    assert percents == sorted(percents) and percents[-1] == 100, "the bar only moves forward"
    assert any(w.get("left_s") for w in running), "an honest time left, measured from the steps so far"
    first = next(w for w in running if w.get("left_s"))
    assert look.status(home, started["look_id"])["percent"] == 100
    assert first["left_s"] > 0


def test_a_section_is_copied_as_youtube_sends_it_never_re_encoded(home, monkeypatch):
    import cr_style_clone
    ran = []

    class Done:
        stderr = ""
    monkeypatch.setattr(cr_style_clone, "_ytdlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(look.subprocess, "run", lambda command, **kw: ran.append(command) or Done())
    dest = home / "media" / "x_0_180.mp4"
    dest.parent.mkdir()
    (home / "media" / "x_0_180.mp4").write_bytes(b"0" * 60_000)
    assert look._download("abcdefghijk", 0, 180, dest) == dest
    assert "--force-keyframes-at-cuts" not in ran[0] and "*0-180" in ran[0]


def test_a_finished_look_offers_example_titles_built_from_the_channels_own_titles_without_claude(home):
    """The owner's Windows test: "An example video you want to make" was required, with a generic placeholder and no
    ideas; the owner left Studio for two minutes to ask Claude Code. A look now offers ideas from the channel's titles."""
    ideas = look.title_ideas(["100 Strangers, 1 Bunker: Who Survives 30 Days #shorts",
                              "The Kid Who Lived In A Mall For 7 Years | Nobody Gets In"])
    assert 3 <= len(ideas) <= 5 and len(set(i.lower() for i in ideas)) == len(ideas)
    assert ideas[0] != "100 Strangers, 1 Bunker: Who Survives 30 Days", "a new take first, the channel's own after"
    assert all("#" not in i and "|" not in i for i in ideas)
    assert any("In An Island" in i or "In A " in i for i in ideas) and not any("In A Island" in i for i in ideas)
    assert look.title_ideas(["Born In 1945"]) == ["Born In 1945"], "a year is kept"
    assert look.title_ideas([]) == []
    _, done, calls = run_look(home)
    assert done["title_ideas"] and calls and all(c.startswith("style/watch") for c in calls), "no model is asked"


def test_a_style_written_in_code_is_never_offered_a_picture_every_picture_copies(home):
    """The owner's test: "Choose the picture style every video keeps" (AI samples on the creator's image account) was
    offered for a coded style, whose renderer draws every frame."""
    import cr_style_anchor as anchor
    _, done, _ = run_look(home)
    op = saved_v2_style(home, done["look_id"])
    assert anchor.status(home, op)["offered"] is True, "a style made with pictures keeps it"
    path = home / "styles" / "cr_clone_toby.json"
    style = json.loads(path.read_text())
    style["look"]["visuals"] = "cr_animated2d"
    path.write_text(json.dumps(style))
    assert anchor.status(home, op)["offered"] is False
    with pytest.raises(RuntimeError, match="written in code"):
        anchor.start(home, op, generate=lambda *a, **k: pytest.fail("no picture is drawn"), upload=lambda p: "u",
                     thread=False)


def test_claude_code_is_given_the_looks_time_left_and_title_ideas(monkeypatch):
    import cr_studio_tools as tools
    answers = {"running": {"state": "running", "percent": 40, "message": "Video 2 of 3 · Measuring", "left_s": 200},
               "done": {"state": "done", "mapping": {"questions": [], "route": {"engine": "animated2d", "why": "x"}},
                        "title_ideas": ["50 Strangers, 1 Prison: Who Survives 24 Days"]}}
    for state, answer in answers.items():
        monkeypatch.setattr(tools, "call", lambda method, path, timeout=30, answer=answer: answer)
        got = tools.look_progress("lk_" + "a" * 24)
        if state == "running":
            assert got["minutes_left"] == 3 and got["message"].startswith("Video 2 of 3")
        else:
            assert got["title_ideas"] == answer["title_ideas"] and "title_ideas" in got["next"]
