"""One picture Kie can't make stops only that picture, as the engine intends ("One image failing must NOT abort a whole
video"). Studio's AccountStop is a BaseException, so it went past every such handler in the engine: a refusal on every
model, or Kie's "422 generate playground failed, task id is blank" (2 of the owner's 266 pictures), stopped the whole
video. A 6-minute stick-figure story draws about 140 pictures; cr-stick-stories failed 13 of 21 times (census,
2026-10-25), always after its Claude work, in the pictures-and-render phase."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_media as media


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_native_images", None)
    monkeypatch.setattr(media.cr_bridge, "SETTINGS", tmp_path / ".cr-connection.json")
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": "kie", "tasks": sorted(media.TASKS)},
                                   "images": {"provider": "kie", "model": "google/nano-banana", "fallbacks": ["gpt-image-2"]}})
    monkeypatch.setenv("KIE_API_KEY", "customer-test-key")
    monkeypatch.setattr(media.time, "sleep", lambda *_: None)
    monkeypatch.setattr(media, "download", lambda url, dest: Path(dest).write_bytes(b"generated media"))
    return tmp_path


def response(body, status=200):
    return SimpleNamespace(status_code=status, json=lambda: body)


BLANK = {"state": "fail", "failCode": "422", "failMsg": "generate playground failed, task id is blank", "creditsConsumed": 0}
SENSITIVE = {"state": "fail", "failCode": "422", "failMsg": "Flagged as sensitive", "creditsConsumed": 0}


def kie(monkeypatch, outcome):
    """Kie answering each picture by its prompt: outcome(prompt, model) -> a failed state or None for success."""
    tasks, creates = {}, []
    def create(url, **kw):
        creates.append(kw["json"])
        tasks["t" + str(len(creates))] = outcome(kw["json"]["input"]["prompt"], kw["json"]["model"])
        return response({"code": 200, "data": {"taskId": "t" + str(len(creates))}})
    def poll(url, **kw):
        state = tasks[kw["params"]["taskId"]]
        return response({"code": 200, "data": state or {
            "state": "success", "resultJson": json.dumps({"resultUrls": ["https://files.example/p.png"]})}})
    monkeypatch.setattr(media.requests, "post", create)
    monkeypatch.setattr(media.requests, "get", poll)
    return creates


def story_stills(make, folder, prompts):
    """The engine's story loop (make_video.generate_astrology_visuals: gen_still), reduced: a RuntimeError skips the
    picture; more than a third missing stops the story."""
    photos = []
    for i, prompt in enumerate(prompts):
        dest = folder / f"scene_{i:02d}.jpg"
        try:
            make(prompt, dest, f"picture {i + 1}")
            photos.append(dest)
        except RuntimeError:
            photos.append(None)
    missing = sum(1 for p in photos if p is None)
    if missing > max(1, int(len(photos) * 0.34)):
        sys.exit(f"{missing} of {len(photos)} story pictures could not be made")
    return photos


def test_a_picture_kie_cannot_make_is_skipped_and_the_story_goes_on(hub, monkeypatch):
    creates = kie(monkeypatch, lambda prompt, model: BLANK if prompt == "The keeper drops the lamp" else None)
    picture = media.skippable(media.image)
    prompts = ["The keeper climbs the stairs", "The keeper drops the lamp", "Ships in the fog", "Dawn at last"]
    photos = story_stills(picture, hub, prompts)
    assert [p is not None for p in photos] == [True, False, True, True]
    assert len(creates) == 4, "the failed picture is not tried again (Kie's 422 'task id is blank' is the creator's call)"
    # Resume: nothing is paid again, the failed picture is skipped again
    again = story_stills(picture, hub, prompts)
    assert len(creates) == 4 and [p is not None for p in again] == [True, False, True, True]


def test_a_picture_refused_by_every_model_is_skipped_after_each_model_tried_once(hub, monkeypatch):
    creates = kie(monkeypatch, lambda prompt, model: SENSITIVE if "battle" in prompt else None)
    with pytest.raises(media.PictureFailed, match="after 2 selected models"):
        media.skippable(media.image)("A bloody battle", hub / "scene.jpg")
    assert [c["model"] for c in creates] == ["google/nano-banana", "gpt-image-2-text-to-image"]
    # a thumbnail (the engine catches Exception and leaves it out) no longer takes the video with it
    try:
        media.skippable(media.image)("A bloody battle, thumbnail", hub / "thumb.png")
    except Exception as left_out:                                        # noqa: BLE001 - what the engine's handler sees
        assert isinstance(left_out, media.PictureFailed)


def test_too_many_missing_pictures_still_stop_the_story(hub, monkeypatch):
    kie(monkeypatch, lambda prompt, model: BLANK)
    with pytest.raises(SystemExit, match="4 of 4 story pictures"):
        story_stills(media.skippable(media.image), hub, ["a", "b", "c", "d"])


@pytest.mark.parametrize("failed", [{"state": "fail", "failCode": "402", "failMsg": "Insufficient credits"},
                                    {"state": "fail", "failCode": "401", "failMsg": "Unauthorized"}])
def test_an_empty_or_disconnected_account_still_stops_the_video(hub, monkeypatch, failed):
    kie(monkeypatch, lambda prompt, model: failed)
    with pytest.raises(media.AccountStop):
        story_stills(media.skippable(media.image), hub, ["a"])


def test_an_uncertain_picture_still_stops_the_video(hub, monkeypatch):
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: (_ for _ in ()).throw(media.requests.ReadTimeout()))
    with pytest.raises(media.AccountStop, match="interrupted"):
        story_stills(media.skippable(media.image), hub, ["a"])
    kie(monkeypatch, lambda prompt, model: {"state": "generating"})
    original = media.run
    monkeypatch.setattr(media, "run", lambda *a, **kw: original(*a, timeout_s=0, **kw))
    with pytest.raises(media.AccountStop, match="still pending"):
        story_stills(media.skippable(media.image), hub / "pending", ["b"])


def test_the_engine_meets_skippable_pictures_and_clips(hub, monkeypatch):
    """install() gives the engine's picture and clip calls the skippable versions."""
    engine = SimpleNamespace(image_gen=None, _image_provider=lambda: "kie", generate_voiceover=lambda *a, **k: None,
                             _kie_still_with_url=None, host_image=None, _kie_upload_image=None, _kie_video_gen=None)
    calls = []
    monkeypatch.setattr(media, "image", lambda *a, **k: calls.append("image") or
                        (_ for _ in ()).throw(media.ModelsExhausted("Image generation stopped after 5 selected models.")))
    for name in ("make_voiceover", "wrap_whisper"):
        monkeypatch.setattr(media, name, lambda *a, **k: (lambda *x, **y: None))
    try:
        media.install(engine)
    except Exception:                                                     # noqa: BLE001 - engine modules absent here
        pass
    with pytest.raises(media.PictureFailed):
        engine.image_gen("A picture", hub / "a.jpg", "picture 1")


def test_a_clip_kie_cannot_animate_is_skipped_and_shows_its_first_frame(hub, monkeypatch):
    kie(monkeypatch, lambda prompt, model: {"state": "fail", "failCode": "500", "failMsg": "Internal error"})
    with pytest.raises(media.PictureFailed, match="failed this motion task"):
        media.skippable(media.native_motion)("https://files.example/frame.jpg", "A slow push in", hub / "clip_00.mp4")
    assert "skippable(native_motion)" in Path(media.__file__).read_text(encoding="utf-8"), "the engine's clip call"
