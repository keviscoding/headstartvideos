"""A learned style can be edited after it is saved, and an installed style can be tried for a few seconds first."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_clone as clone  # noqa: E402
import cr_style_edit as edit  # noqa: E402
import cr_style_sample as sample  # noqa: E402

OPERATION = "op_" + "4" * 24
NAME = "cr_clone_" + "4" * 12
LEARNED = {"name": NAME, "label": "What If Stories", "tagline": "What if, told small",
           "description": "Explainer stories about what-if ideas, told with stick figures.",
           "prompts": {"outline": "CHANNEL: Explainer stories about what-if ideas, told with stick figures.\nPlan it."},
           "script_notes": ["Talk straight to the viewer."], "director_notes": ["Cut every two seconds."],
           "look": {"character": "THE CAST — Dot (host): a glossy black sphere head.\nMo: a tall friend.",
                    "character_image": "styles/learned/x/character.png", "captions": {"off": True},
                    "still_suffix": " Flat low-poly 3D render.", "style_image": "styles/learned/x/style.jpg"},
           "pacing": {"photo_every_s": 2.0},
           "features": {"offered": ["ai_images", "maps", "motion", "sound"], "on": ["ai_images", "sound"],
                        "channel_on": ["ai_images", "sound"]},
           "cr_style_clone": {"operation_id": OPERATION, "base_style": "stories", "learned_v2": {"look_id": ""}}}


@pytest.fixture
def home(tmp_path):
    (tmp_path / "styles").mkdir()
    path = tmp_path / "styles" / (NAME + ".json")
    path.write_text(json.dumps(LEARNED))
    (tmp_path / "styles" / "documentary.json").write_text(json.dumps({"name": "documentary", "label": "Documentary"}))
    folder = clone._folder(tmp_path) / OPERATION
    folder.mkdir(parents=True)
    clone._write(folder / "operation.json", {
        "operation_id": OPERATION, "state": "complete", "style_sha256": clone._file_hash(path),
        "spec": {"style_id": NAME, "title": "What if the sun went out", "brief": "Keep it light",
                 "base_style": "stories", "reference_url": "https://www.youtube.com/@ifology/videos",
                 "references": [{"id": "abcdefghijk"}]}})
    for rel in ("styles/learned/x/style.jpg", "styles/learned/x/character.png"):
        (tmp_path / "assets" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "assets" / rel).write_bytes(b"picture")
    return tmp_path


def test_a_learned_style_shows_what_can_be_changed_and_what_studied_it(home):
    got = edit.editable(home, NAME)
    assert got["name"] == "What If Stories" and got["script_notes"] == ["Talk straight to the viewer."]
    assert got["features"]["on"] == ["ai_images", "sound"] and got["subtitles"] is False
    assert got["pictures"] == {"floor": 15.0, "default": 30.0, "ceiling": 30.0, "value": 30.0}
    assert got["study"]["title"] == "What if the sun went out" and got["study"]["reference_urls"]
    assert got["has_character_picture"] and got["versions_kept"] == 0
    with pytest.raises(ValueError):
        edit.editable(home, "documentary")                  # a recipe Studio ships is updated with Studio, not here
    with pytest.raises(LookupError):
        edit.editable(home, "cr_clone_missing")


def test_saving_changes_keeps_the_last_version_and_the_study_still_owns_the_style(home):
    out = edit.save(home, NAME, {"name": "Tiny What Ifs", "description": "Tiny what-if stories for kids.",
                                 "script_notes": "Speak to a ten-year-old.\n\nEnd on a question.",
                                 "director_notes": ["Hold each picture a little longer."],
                                 "features_on": ["sound", "maps"], "pictures_per_min": 20, "subtitles": True,
                                 "character": "THE CAST — Pip: a small robot.", "forget_character_picture": True})
    assert out["saved"] and Path(out["kept"]).is_file() and json.loads(Path(out["kept"]).read_text()) == LEARNED
    style = json.loads((home / "styles" / (NAME + ".json")).read_text())
    assert style["label"] == "Tiny What Ifs" and style["script_notes"] == ["Speak to a ten-year-old.", "End on a question."]
    assert style["prompts"]["outline"].startswith("CHANNEL: Tiny what-if stories for kids."), "its writer follows it too"
    assert style["features"]["on"] == ["maps", "sound"] and style["features"]["channel_on"] == ["maps", "sound"]
    assert style["pacing"]["photo_every_s"] == 3.0 and style["look"]["captions"]["off"] is False
    assert "character_image" not in style["look"] and style["look"]["style_image"]
    saved = json.loads((clone._folder(home) / OPERATION / "operation.json").read_text())
    assert clone._published(home, saved), "the edited style is still the study's saved style"
    assert edit.editable(home, NAME)["versions_kept"] == 1


@pytest.mark.parametrize("changes", [{}, {"features_on": ["threed"]}, {"pictures_per_min": 99},
                                     {"name": ""}, {"script_notes": ["x" * 500]}, {"subtitles": "yes"}])
def test_edits_outside_what_the_style_offers_are_refused_and_change_nothing(home, changes):
    before = (home / "styles" / (NAME + ".json")).read_bytes()
    with pytest.raises(ValueError):
        edit.save(home, NAME, changes)
    assert (home / "styles" / (NAME + ".json")).read_bytes() == before


def test_which_styles_can_be_tried_for_a_few_seconds_and_what_the_others_would_need():
    assert sample.offer(LEARNED)["can"] and sample.offer(LEARNED)["cost_usd"] <= 0.10
    assert sample.offer({"pacing": {"photo_every_s": 5.5}})["can"], "a story recipe draws a picture for every line"
    for style, words in (({"features": {"on": ["youtube", "maps"]}}, "footage"),
                         ({"features": {"on": ["threed", "maps"]}}, "3D"),
                         ({"look": {"visuals": "cr_animated2d"}, "pacing": {"photo_every_s": 3}}, "animated in code"),
                         ({"shorts": {"pipeline": "zack3d"}}, "whole Short")):
        offer = sample.offer(style)
        assert not offer["can"] and words in offer["why_not"] and offer["cost_usd"] == 0


def test_a_sample_draws_two_pictures_in_the_style_reads_the_line_and_is_never_paid_twice(home):
    drawn, hosted, read = [], [], []

    def generate(prompt, dest, label, ref_urls):
        drawn.append((prompt, list(ref_urls)))
        Path(dest).write_bytes(b"jpeg")

    def host(path):
        hosted.append(Path(path).name)
        return "https://files.example/" + Path(path).name

    def voice(text, dest):
        read.append(text)
        Path(dest).with_suffix(".mp3").write_bytes(b"mp3")
        return Path(dest).with_suffix(".mp3")

    def make(stills, audio, out):
        assert [p.name for p in stills] == ["still_0.jpg", "still_1.jpg"] and audio.name == "voice.mp3"
        Path(out).write_bytes(b"mp4")
    done = sample.start(home, NAME, LEARNED, "  how bees build their homes ", generate=generate, host=host,
                        voice=voice, make=make, thread=False)
    assert done["state"] == "done" and done["video_url"].endswith("/video.mp4") and done["voiced"]
    assert hosted == ["style.jpg", "character.png"] and read == ["how bees build their homes"]
    first, refs = drawn[0]
    assert first.startswith("STYLE REFERENCE") and "how bees build their homes" in first
    assert "glossy black sphere head" in first and "Mo:" not in first and first.endswith("Flat low-poly 3D render.")
    assert refs == ["https://files.example/style.jpg", "https://files.example/character.png"] and len(drawn) == 2
    again = sample.start(home, NAME, LEARNED, "how bees build their homes", generate=generate, host=host,
                         voice=voice, make=make, thread=False)
    assert again["sample_id"] == done["sample_id"] and len(drawn) == 2, "the same sample is never drawn twice"


def test_a_sample_that_stops_says_why_and_keeps_what_was_paid(home):
    def generate(prompt, dest, label, ref_urls):
        if dest.name == "still_1.jpg":
            raise RuntimeError("Kie said the balance is too low")
        Path(dest).write_bytes(b"jpeg")
    out = sample.start(home, NAME, LEARNED, "volcanoes", generate=generate, host=lambda p: "u", voice=None,
                       make=lambda *a: None, thread=False)
    assert out["state"] == "failed" and "balance is too low" in out["message"] and "kept" in out["message"]
    assert (sample.folder(home, out["sample_id"]) / "still_0.jpg").is_file()
    with pytest.raises(ValueError):
        sample.start(home, "documentary", {"features": {"on": ["youtube"]}}, "volcanoes", generate=generate,
                     host=str, thread=False)
    with pytest.raises(ValueError):
        sample.folder(home, "../escape")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg isn't installed here")
def test_the_ken_burns_sample_is_rendered_on_this_computer_at_the_voices_length(tmp_path):
    from PIL import Image
    stills = []
    for k, colour in enumerate(((200, 60, 60), (60, 120, 200))):
        stills.append(tmp_path / f"still_{k}.jpg")
        Image.new("RGB", (1376, 768), colour).save(stills[-1])
    voice = tmp_path / "voice.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", str(voice)],
                   check=True)
    out = sample.render(stills, voice, tmp_path / "sample.mp4")
    assert 6.0 <= sample._duration(out) <= 7.5
    silent = sample.render(stills, None, tmp_path / "silent.mp4")
    assert abs(sample._duration(silent) - sample.SECONDS) < 0.3


def test_pictures_a_minute_stay_around_the_pace_the_style_was_learned_at_edit_after_edit(home):
    edit.save(home, NAME, {"pictures_per_min": 15})
    edit.save(home, NAME, {"pictures_per_min": 15})               # the floor of the learned pace, not of the last edit
    got = edit.editable(home, NAME)["pictures"]
    assert got == {"floor": 15.0, "default": 30.0, "ceiling": 30.0, "value": 15.0}
    with pytest.raises(ValueError):
        edit.save(home, NAME, {"pictures_per_min": 8})


REAL = {**LEARNED, "pacing": {"photo_every_s": 5.5},
        # a real learned style: its look switched on AI pictures and cut-out movement without offering them here
        "features": {"offered": ["maps", "motion", "sound"], "on": ["ai_images", "sound"], "channel_on": ["depth"],
                     "auto_own": ["ai_images"]}}


def test_a_rename_leaves_the_switches_and_the_pace_exactly_as_they_were(home):
    path = home / "styles" / (NAME + ".json")
    path.write_text(json.dumps(REAL))
    clone._write(clone._folder(home) / OPERATION / "operation.json", {
        **json.loads((clone._folder(home) / OPERATION / "operation.json").read_text()),
        "style_sha256": clone._file_hash(path)})
    shown = edit.editable(home, NAME)
    assert shown["features"]["on"] == ["sound"] and shown["pictures"]["value"] == 10.9
    edit.save(home, NAME, {"name": "Renamed"})
    style = json.loads(path.read_text())
    assert style["label"] == "Renamed"
    assert json.dumps(style["features"]) == json.dumps(REAL["features"]) and style["pacing"] == {"photo_every_s": 5.5}
    # what the page showed, sent back as it was: nothing it didn't offer is dropped and the pace stays exact
    edit.save(home, NAME, {"features_on": shown["features"]["on"], "pictures_per_min": shown["pictures"]["value"],
                           "tagline": "Small"})
    style = json.loads(path.read_text())
    assert style["features"] == REAL["features"] and style["pacing"] == {"photo_every_s": 5.5}
    edit.save(home, NAME, {"features_on": ["maps"]})              # an offered switch changed: the rest keep theirs
    style = json.loads(path.read_text())
    assert style["features"]["on"] == ["maps", "ai_images"] and style["features"]["channel_on"] == ["maps", "depth"]
    saved = json.loads((clone._folder(home) / OPERATION / "operation.json").read_text())
    assert clone._published(home, saved)


def test_an_edit_waits_while_the_study_is_still_finishing_the_style(home):
    path = home / "styles" / (NAME + ".json")
    before = path.read_bytes()
    folder = clone._folder(home) / OPERATION
    with clone._lock(folder):                      # the study checks the likeness, then records completion
        with pytest.raises(RuntimeError) as stopped:
            edit.save(home, NAME, {"name": "Too soon"})
    assert "still finishing" in str(stopped.value) and "in a few minutes" in str(stopped.value)
    assert path.read_bytes() == before and not (home / "output" / "cr-style-edits").exists()
    edit.save(home, NAME, {"name": "Now"})                     # once it's done, the edit is saved and still owned
    saved = json.loads((folder / "operation.json").read_text())
    assert json.loads(path.read_text())["label"] == "Now" and clone._published(home, saved)


@pytest.mark.parametrize("name", ["kinetic", "workshop", "avatar", "avatar-premium", "hustle", "documentary"])
def test_styles_not_made_of_generated_pictures_are_never_shown_as_two_ai_stills(name):
    style = {"name": name, "features": {"on": ["ai_images", "motion", "youtube"]}, "pacing": {"photo_every_s": 4}}
    offer = sample.offer(style, name=name)
    assert not offer["can"] and offer["why_not"] and offer["cost_usd"] == 0
    assert sample.offer(style, name="stories")["can"], "a story recipe's pictures are what its videos are made of"


def test_a_learned_style_is_tried_first_only_when_its_channel_is_mostly_generated_pictures(home):
    footage = {"pictures": {"sources": {"families": {"footage": .7, "generated": .2, "graphic": .1}}}}
    drawn = {"pictures": {"sources": {"families": {"generated": .8, "graphic": .2}}}}
    told = sample.offer(LEARNED, name=NAME, profile=footage)
    assert not told["can"] and "real footage" in told["why_not"] and "20%" in told["why_not"]
    assert sample.offer(LEARNED, name=NAME, profile=drawn)["can"]
    look_id = "lk_" + "5" * 24
    clone._write(clone._folder(home) / "looks" / look_id / "look.json", {"profile": footage})
    youtube = {**LEARNED, "cr_style_clone": {**LEARNED["cr_style_clone"], "learned_v2": {"look_id": look_id}}}
    with pytest.raises(ValueError) as refused:                 # the sample route reads the same look
        sample.start(home, NAME, youtube, "volcanoes", generate=lambda *a, **k: None, host=str, thread=False)
    assert "real footage" in str(refused.value)


def test_the_sample_record_is_saved_while_the_page_reads_it_and_never_shows_a_folder(home, monkeypatch):
    real_replace, refused = Path.replace, []

    def replace(self, target):                 # Windows: the page's poll has sample.json open for a moment
        if Path(target).name == "sample.json" and not refused:
            refused.append(target)
            raise PermissionError(13, "Permission denied", str(target))
        return real_replace(self, target)
    monkeypatch.setattr(Path, "replace", replace)
    out = sample.start(home, NAME, LEARNED, "volcanoes", generate=lambda p, d, l, ref_urls: Path(d).write_bytes(b"j"),
                       host=lambda p: "u", voice=None, make=lambda s, a, o: Path(o).write_bytes(b"mp4"), thread=False)
    assert refused and out["state"] == "done"
    said = sample._said(PermissionError(13, "Permission denied", "C:\\Users\\Kevin\\Studio\\sample.json"))
    assert said == "Permission denied (sample.json)" and "Kevin" not in said
