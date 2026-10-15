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
    assert ov["look"]["still_suffix"].endswith("Avoid: photographs, 3D renders, gradients.")
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
    assert art.overrides(out)["look"]["still_suffix"].endswith("Avoid: photorealism, no 3D.")


def test_a_candid_photo_style_never_asks_for_glossy_studio_pictures():
    glossy = ("Photorealistic phone photos of an older butcher in a home kitchen, commercial supermarket lighting, "
              "crisp focus, glossy finish, cinematic lighting, warm colours, everyday clutter on the counters.")
    out = art.check({**ANSWER, "still_suffix": glossy}, banned=[], candid=True)
    text = out["still_suffix"].lower()
    assert not any(w in text for w in ("commercial", "crisp", "glossy", "cinematic"))
    assert "natural light" in text and "deep focus" in text and "everyday clutter" in text
    assert "commercial" in art.check({**ANSWER, "still_suffix": glossy}, banned=[])["still_suffix"], \
        "only candid styles are rewritten"


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
