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
