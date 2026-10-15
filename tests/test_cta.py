"""Your call to action in any video (cr_cta): checked settings, script placement, the card, the description."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_cta as cta  # noqa: E402

CFG = {"what": "My free PDF guide to cheap cuts", "link": "https://example.com/guide", "count": 2}


def test_settings_are_checked_before_any_work(tmp_path, monkeypatch):
    assert cta.clean(None) == {}
    assert cta.clean(CFG) == CFG
    with pytest.raises(ValueError, match="https://"):
        cta.clean(dict(CFG, link="example.com"))
    with pytest.raises(ValueError, match="few words"):
        cta.clean(dict(CFG, what="x"))
    assert cta.clean(dict(CFG, count=9))["count"] == 3
    monkeypatch.setattr(cta, "UPLOADS", tmp_path)
    with pytest.raises(ValueError, match="picture again"):
        cta.clean(dict(CFG, image="cta_" + "a" * 20))
    import io
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), (200, 30, 30)).save(buf, "PNG")
    image = cta.save_image(buf.getvalue(), tmp_path)
    assert cta.clean(dict(CFG, image=image))["image"] == image


def test_mentions_never_land_in_the_first_three_minutes():
    assert cta.places(3, 20) == ["about 40% of the way through", "about 70% of the way through",
                                 "in the closing lines, before the goodbye"]
    assert cta.places(2, 6) == ["about 70% of the way through", "in the closing lines, before the goodbye"], \
        "40% of six minutes is under three minutes, so that mention moves later and the count is kept"
    assert cta.places(1, 20) == ["about 40% of the way through"]
    assert cta.places(1, 1) == ["in the closing lines, before the goodbye"]
    block = cta.script_block(CFG, 12)
    assert "mention 1 about 40%" in block and "mention 2 about 70%" in block and '"link on screen"' in block


def test_the_card_lands_on_the_words_link_on_screen(tmp_path):
    words = [["so", 100.0, 100.2], ["the", 199.0, 199.1], ["link", 200.0, 200.2], ["on", 200.2, 200.3],
             ["screen.", 200.3, 200.6], ["link", 400.0, 400.2], ["on", 400.2, 400.3], ["screen", 400.3, 400.5]]
    (tmp_path / "words_heard.json").write_text(json.dumps(words))
    assert cta.mention_times(tmp_path, 2, 600.0) == [200.0, 400.0]
    assert cta.mention_times(tmp_path / "none", 2, 600.0) == [240.0, 420.0], "the planned places when unsaid"
    assert cta.mention_times(tmp_path / "none", 2, 360.0) == [252.0, 360.0 - cta.CARD_S - 4], \
        "an early place moves on to the closing lines, as the script was told"


def test_a_finished_video_gets_its_card_and_description_once(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "run_spec.json").write_text(json.dumps({"cr_cta": dict(CFG, count=1)}))
    (job / "words_heard.json").write_text(json.dumps([["link", 2.0, 2.2], ["on", 2.2, 2.3], ["screen", 2.3, 2.6]]))
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=12:r=10", "-f", "lavfi",
                    "-i", "sine=f=220:d=12", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(job / "video.mp4")], check=True)
    (job / "youtube.txt").write_text("Chapters\n0:00 Start\n")
    assert cta.finish(job, log=lambda m: None)
    def frame(t):
        out = job / f"f{t}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(job / "video.mp4"), "-frames:v", "1",
                        str(out)], check=True)
        return Image.open(out).convert("RGB")
    assert frame(5).getpixel((1500, 900))[0] > 200, "the white card on screen while it is mentioned"
    assert frame(11.5).getpixel((1500, 900))[0] < 40, "and gone after"
    assert (job / "youtube.txt").read_text().startswith("My free PDF guide to cheap cuts: https://example.com/guide\n\n")
    stamp = (job / "cta.json").read_text()
    cta.finish(job, log=lambda m: pytest.fail("the card is added once"))
    assert (job / "cta.json").read_text() == stamp and (job / "youtube.txt").read_text().count("example.com") == 1


def test_the_script_asks_for_the_mentions_only_in_videos_that_have_one(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "run_spec.json").write_text(json.dumps({"cr_cta": CFG}))
    seen = []
    engine = type("E", (), {})()
    engine.log = lambda m: None
    engine._extra_block = lambda: "EXTRA"
    engine.generate_script = lambda title, minutes, job, force, style="": seen.append(engine._extra_block()) or "s"
    engine.publish_named_copy = lambda job, title, fresh="": job
    cta.install(engine)
    engine.generate_script("t", 12, job, False)
    engine.generate_script("t", 12, tmp_path / "other", False)
    assert "CALL TO ACTION" in seen[0] and seen[1] == "EXTRA"


def test_story_recipes_offer_a_picture_count_with_a_quality_floor():
    import cr_media
    assert cr_media.picture_range({"pacing": {"photo_every_s": 3.0}}) == (10.0, 20.0, 30.0)
    assert cr_media.picture_range({"pacing": {"photo_every_s": 12.0}}) == (4.0, 5.0, 7.5)
    assert cr_media.picture_range({"pacing": {}}) is None
