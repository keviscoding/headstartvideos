"""Customer wallet routing and paid-task resumption contracts."""
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
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": "kie", "tasks": sorted(media.TASKS)}})
    monkeypatch.setenv("KIE_API_KEY", "customer-test-key")
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "customer-atlas-test-key")
    monkeypatch.setattr(media.time, "sleep", lambda *_: None)
    monkeypatch.setattr(media, "download", lambda url, dest: Path(dest).write_bytes(b"generated media"))
    return tmp_path


def response(body, status=200):
    return SimpleNamespace(status_code=status, json=lambda: body)


@pytest.mark.parametrize("provider", ["kie", "atlas"])
def test_resume_uses_original_task_after_interrupted_poll(hub, monkeypatch, provider):
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": provider, "tasks": sorted(media.TASKS)}})
    creates, polls = [], []
    def create(url, **kw):
        creates.append((url, kw))
        return response({"code": 200 if provider == "kie" else 0, "data": {"taskId" if provider == "kie" else "id": "task-123"}})
    monkeypatch.setattr(media.requests, "post", create)
    def poll(url, **kw):
        polls.append((url, kw))
        return response({"code": 200, "data": {"state": "success", "resultJson": json.dumps({"resultUrls": ["https://files.example/image.png"]})}}) if provider == "kie" else response({"data": {"status": "completed", "outputs": ["https://files.example/image.png"]}})
    dest = hub / "image.png"
    with pytest.raises(media.AccountStop, match="still pending"):
        media.run("images", {"prompt": "Example"}, dest, timeout_s=0)
    monkeypatch.setattr(media.requests, "get", poll)
    media.run("images", {"prompt": "Example"}, dest)
    media.run("images", {"prompt": "Example"}, dest)
    assert len(creates) == len(polls) == 1
    assert "task-123" in polls[0][0] or polls[0][1]["params"] == {"taskId": "task-123"}
    assert creates[0][1]["headers"]["Authorization"] == "Bearer " + ("customer-test-key" if provider == "kie" else "customer-atlas-test-key")
    receipts = list((hub / ".cr-tasks").glob("*.json"))
    assert len(receipts) == 1 and "customer" not in receipts[0].read_text()


def test_uncertain_submission_never_resubmits_or_changes_wallet(hub, monkeypatch):
    calls = []
    def create(*a, **kw):
        calls.append(kw)
        raise media.requests.ReadTimeout("do not expose upstream details")
    monkeypatch.setattr(media.requests, "post", create)
    with pytest.raises(media.AccountStop, match="interrupted"):
        media.run("images", {"prompt": "Example"}, hub / "test.png")
    with pytest.raises(media.AccountStop, match="uncertain"):
        media.run("images", {"prompt": "Example"}, hub / "test.png")
    assert len(calls) == 1


def test_kie_http_200_error_stops_without_paid_fallback(hub, monkeypatch):
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: response({"code": 429, "msg": "upstream secret"}))
    with pytest.raises(media.AccountStop, match="refused") as error:
        media.run("images", {"prompt": "Example"}, hub / "test.png")
    assert "secret" not in str(error.value) and not isinstance(error.value, Exception)


@pytest.mark.parametrize("provider", ["kie", "atlas"])
def test_image_refs_motion_and_presenter_use_correct_protocol(hub, monkeypatch, provider):
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": provider, "tasks": sorted(media.TASKS)}})
    calls = []
    monkeypatch.setattr(media, "run", lambda *a, **k: calls.append((a, k)) or "https://files.example/output")
    media.image("Draw", hub / "image.png", ref_urls=["https://files.example/reference"])
    media.motion("https://files.example/frame", "Move", hub / "clip.mp4")
    media.avatar_clip("kling-pro", "https://files.example/face", "https://files.example/voice", hub / "presenter.mp4")
    assert calls[0][1]["model"] == media.PROVIDERS[provider]["models"]["image_edit"]
    assert calls[0][0][1]["image_urls" if provider == "kie" else "images"]
    assert ("image_url" if provider == "kie" else "image") in calls[1][0][1]
    assert calls[2][1]["model"] == media.PROVIDERS[provider]["models"]["avatar_pro"]
    with pytest.raises(media.AccountStop, match="original service"):
        media.avatar_clip("pruna", "image", "audio", hub / "presenter.mp4")


def test_kie_motion_cost_choice_and_pending_task_are_preserved_on_resume(hub, monkeypatch):
    creates = []
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: creates.append(k["json"]) or
                        response({"code": 200, "data": {"taskId": "motion-task"}}))
    monkeypatch.setattr(media.requests, "get", lambda *a, **k: (_ for _ in ()).throw(media.requests.ReadTimeout()))
    clock = iter(range(0, 12000, 2000))
    monkeypatch.setattr(media.time, "monotonic", lambda: next(clock))
    for _ in range(2):
        with pytest.raises(media.AccountStop, match="pending"):
            media.motion("https://files.example/frame", "A gentle move", hub / "motion.mp4")
    assert creates == [{"model": "bytedance/v1-pro-fast-image-to-video", "input": {
        "prompt": "A gentle move", "image_url": "https://files.example/frame", "resolution": "720p", "duration": "5"}}]


def test_native_kie_motion_does_not_switch_unselected_tasks_or_an_explicit_hub(hub, monkeypatch):
    settings = {"costs_accepted": True, "media_hub": {"provider": "atlas", "tasks": ["images"]}}
    media.cr_bridge.save_settings(settings)
    observed = []
    monkeypatch.setattr(media, "run", lambda task, inputs, dest, **k: observed.append((media.account(task)[0], inputs)))
    media.native_motion("https://files.example/frame", "Move", hub / "clip.mp4")
    assert observed[-1][0] == "kie" and observed[-1][1]["duration"] == "5"
    assert not media.active("motion") and media.profile("images")["provider"] == "atlas"
    settings["media_hub"]["tasks"].append("motion")
    media.cr_bridge.save_settings(settings)
    media.native_motion("https://files.example/frame", "Move", hub / "other.mp4")
    assert observed[-1][0] == "atlas" and observed[-1][1]["image"] == "https://files.example/frame"


def test_native_presenter_receives_positional_motion_when_replacement_is_off(hub, monkeypatch):
    media.cr_bridge.save_settings({"costs_accepted": True, "narration": {"provider": "kie"}})
    calls = []
    def native(provider, image, audio, dest, log=print, motion=""):
        calls.append((provider, image, audio, dest, log, motion))
        return dest
    wrapped = media.route_avatar(native)
    logger = lambda *_: None
    assert wrapped("pruna", "portrait", "voice", hub / "clip.mp4", logger, "Turn toward camera") == hub / "clip.mp4"
    assert calls[-1] == ("pruna", "portrait", "voice", hub / "clip.mp4", logger, "Turn toward camera")
    wrapped("pruna", "portrait", "voice", hub / "clip.mp4", motion="Look up")
    assert calls[-1][-1] == "Look up"


def test_narration_is_bounded_and_timings_come_from_audio(hub, monkeypatch):
    assert all(len(s) <= 2400 for s in media._chunks("A" * 10001))
    calls = []
    monkeypatch.setattr(media, "run", lambda task, inputs, dest, **k: calls.append(inputs) or Path(dest).write_bytes(b"audio"))
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    words = [SimpleNamespace(word="Hello", start=0.1, end=0.4), SimpleNamespace(word="world.", start=0.5, end=0.8)]
    engine = SimpleNamespace(log=lambda *_: None, _get_whisper=lambda: SimpleNamespace(transcribe=lambda *a, **k: ([SimpleNamespace(words=words)], None)),
                             _srt_ts=lambda n: "00:00:00," + str(int(n * 1000)).zfill(3), _script_words_into_srt=lambda *a: None)
    media.narrate(engine, "Hello world.", hub / "audio.mp3", hub / "subs.srt", voice_id="old-private-clone")
    assert calls[0]["voice"] == media.PROVIDERS["kie"]["default_voice"]
    assert "00:00:00,100 --> 00:00:00,800" in (hub / "subs.srt").read_text()


def test_no_media_generation_before_spending_agreement(hub, monkeypatch):
    media.cr_bridge.save_settings({"media_hub": {"provider": "kie", "tasks": sorted(media.TASKS)}})
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: pytest.fail("No paid calls before agreement"))
    with pytest.raises(media.AccountStop, match="spending agreement"):
        media.run("images", {}, hub / "image.png")


def test_separate_narration_uses_its_wallet_without_changing_images(hub, monkeypatch):
    media.cr_bridge.save_settings({"costs_accepted": True,
        "media_hub": {"provider": "atlas", "tasks": sorted(media.TASKS)},
        "narration": {"provider": "kie", "voice": "chosen-voice"}})
    calls = []
    monkeypatch.setattr(media.requests, "post", lambda url, **kw: calls.append((url, kw)) or
                        response({"code": 200, "data": {"taskId": "voice-task"}}))
    monkeypatch.setattr(media.requests, "get", lambda *a, **kw: response({"code": 200,
        "data": {"state": "success", "resultJson": json.dumps({"resultUrls": ["https://files.example/voice.mp3"]})}}))
    media.run("narration", {"text": "Hello", "voice": "chosen-voice"}, hub / "voice.mp3")
    assert calls[0][0] == "https://api.kie.ai/api/v1/jobs/createTask"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer customer-test-key"
    assert calls[0][1]["json"]["model"] == "elevenlabs/text-to-speech-turbo-2-5"
    assert media.profile("images")["provider"] == "atlas"
    assert media.profile("narration")["voice"] == "chosen-voice"


def test_individual_voice_override_and_inherited_hub_narration(hub):
    settings = media.cr_bridge.load_settings()
    settings["narration"] = {"provider": "original"}
    media.cr_bridge.save_settings(settings)
    assert not media.active("narration") and media.active("images")
    settings.pop("narration")
    media.cr_bridge.save_settings(settings)
    assert media.active("narration") and media.profile("narration")["provider"] == "kie"


def test_gemini_voice_protocol_and_wav_conversion_preserve_script(hub, monkeypatch):
    media.cr_bridge.save_settings({"costs_accepted": True,
        "narration": {"provider": "kie", "model": "google/gemini-3-8-flash-tts", "voice": "Kore"}})
    calls, conversions = [], []
    def task(task, inputs, dest, **kwargs):
        calls.append((inputs, Path(dest), kwargs))
        Path(dest).write_bytes(b"provider-wav")
    def convert(args, **kwargs):
        conversions.append(args)
        Path(args[-1]).write_bytes(b"converted-mp3")
    monkeypatch.setattr(media, "run", task)
    monkeypatch.setattr(media.subprocess, "run", convert)
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    words = [SimpleNamespace(word="Exact", start=0.1, end=0.4), SimpleNamespace(word="script.", start=0.5, end=0.8)]
    engine = SimpleNamespace(log=lambda *_: None, _get_whisper=lambda: SimpleNamespace(
        transcribe=lambda *a, **k: ([SimpleNamespace(words=words)], None)),
        _srt_ts=lambda n: "00:00:00," + str(int(n * 1000)).zfill(3), _script_words_into_srt=lambda *a: None)
    media.narrate(engine, "Exact script.", hub / "audio.mp3", hub / "subs.srt")
    inputs, dest, kwargs = calls[0]
    assert inputs["speakers"] == [{"speaker_id": "Speaker 1", "voice_name": "Kore"}]
    assert inputs["dialogue_turns"] == [{"speaker_id": "Speaker 1", "text": "Exact script."}]
    assert kwargs["model"] == "google/gemini-3-8-flash-tts" and dest.suffix == ".wav"
    assert len(conversions) == 1 and (hub / "audio.mp3").read_bytes() == b"converted-mp3"
    assert "00:00:00,100 --> 00:00:00,800" in (hub / "subs.srt").read_text()


def test_failed_voice_retains_provider_reason_and_credits_without_key(hub, monkeypatch):
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: response({"code": 200, "data": {"taskId": "failed-voice"}}))
    monkeypatch.setattr(media.requests, "get", lambda *a, **k: response({"code": 200, "data": {
        "state": "fail", "failCode": "500", "failMsg": "Internal error customer-test-key", "creditsConsumed": 0}}))
    with pytest.raises(media.AccountStop, match=r"Internal error \[redacted\]"):
        media.run("narration", {"text": "Hello", "voice": "example"}, hub / "voice.mp3")
    receipt = next((hub / ".cr-tasks").glob("*.json")).read_text()
    assert "customer-test-key" not in receipt and json.loads(receipt)["credits"] == 0


def test_refreshing_upload_url_does_not_duplicate_pending_paid_task(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: calls.append(k) or response({"code": 200, "data": {"taskId": "existing-task"}}))
    media._references["https://files.example/old-upload"] = {"upload_sha256": "content-digest"}
    media._references["https://files.example/new-upload"] = {"upload_sha256": "content-digest"}
    for url in ("https://files.example/old-upload", "https://files.example/new-upload"):
        with pytest.raises(media.AccountStop, match="still pending"):
            media.run("motion", {"input_urls": [url]}, hub / "clip.mp4", timeout_s=0)
    assert len(calls) == 1


def test_failed_download_cannot_mark_old_asset_as_new_task_output(hub, monkeypatch):
    dest = hub / "image.png"
    dest.write_bytes(b"old unrelated image")
    creates = []
    monkeypatch.setattr(media.requests, "post", lambda *a, **kw: creates.append(kw) or response({"code": 200, "data": {"taskId": "image-task"}}))
    monkeypatch.setattr(media.requests, "get", lambda *a, **k: response({"code": 200, "data": {"state": "success", "resultJson": json.dumps({"resultUrls": ["https://files.example/new-image"]})}}))
    def failed(*a, **kw):
        raise media.AccountStop("Download stopped")
    monkeypatch.setattr(media, "download", failed)
    with pytest.raises(media.AccountStop, match="Download stopped"):
        media.run("images", {"prompt": "new image"}, dest)
    monkeypatch.setattr(media, "download", lambda url, dest: Path(dest).write_bytes(b"correct new image"))
    media.run("images", {"prompt": "new image"}, dest)
    assert dest.read_bytes() == b"correct new image" and len(creates) == 1


def test_atlas_accepts_direct_prediction_shape(hub, monkeypatch):
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": "atlas", "tasks": ["images"]}})
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: response({"id": "atlas-task", "status": "processing"}))
    monkeypatch.setattr(media.requests, "get", lambda *a, **k: response({"id": "atlas-task", "status": "completed", "outputs": ["https://files.example/atlas.png"]}))
    assert media.run("images", {"prompt": "Image"}, hub / "atlas.png") == "https://files.example/atlas.png"


def test_installed_but_broken_video_tool_is_not_reported_ready(monkeypatch):
    import cr_studio
    monkeypatch.setattr(cr_studio.shutil, "which", lambda name: "/installed/" + name)
    monkeypatch.setattr(cr_studio.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    assert not cr_studio.tool_ready("ffmpeg")
    monkeypatch.setattr(cr_studio.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert cr_studio.tool_ready("ffmpeg")


def kie_tasks(monkeypatch, states):
    creates = []
    def create(url, **kw):
        creates.append(kw["json"])
        return response({"code": 200, "data": {"taskId": "task-" + str(len(creates))}})
    def poll(url, **kw):
        idx = int(kw["params"]["taskId"].split("-")[1]) - 1
        state = states[idx]
        return response({"code": 200, "data": state if isinstance(state, dict) else {
            "state": "success", "resultJson": json.dumps({"resultUrls": ["https://files.example/image.png"]})}})
    monkeypatch.setattr(media.requests, "post", create)
    monkeypatch.setattr(media.requests, "get", poll)
    return creates


def test_confirmed_moderation_falls_back_with_references_and_resume_never_pays_again(hub, monkeypatch):
    creates = kie_tasks(monkeypatch, [{"state": "fail", "failCode": "422", "failMsg": "Flagged as sensitive", "creditsConsumed": 0}, "success"])
    dest = hub / "scene.png"
    refs = ["https://files.example/character.png"]
    media.image("A childhood memory", dest, ref_urls=refs)
    media.image("A childhood memory", dest, ref_urls=refs)
    assert [c["model"] for c in creates] == ["google/nano-banana-edit", "nano-banana-2-1"]
    assert creates[0]["input"]["image_urls"] == creates[1]["input"]["image_input"] == refs
    assert creates[0]["input"]["prompt"] == creates[1]["input"]["prompt"]
    assert len(list((hub / ".cr-tasks").glob("*.json"))) == 2


def test_terminal_model_failure_uses_each_selected_model_once_and_stops(hub, monkeypatch):
    creates = kie_tasks(monkeypatch, [{"state": "fail", "failCode": "500", "failMsg": "Internal error", "creditsConsumed": 0}] * 5)
    for _ in range(2):
        with pytest.raises(media.AccountStop, match="after 5 selected models"):
            media.image("Example", hub / "scene.png")
    assert len(creates) == 5 and len({c["model"] for c in creates}) == 5


@pytest.mark.parametrize("code,reason", [("402", "Insufficient credits"), ("401", "Missing permission"),
                                          ("429", "Rate limit"), ("422", "Invalid aspect ratio"), ("cancelled", "Flagged as sensitive")])
def test_account_and_validation_failures_never_fallback(hub, monkeypatch, code, reason):
    creates = kie_tasks(monkeypatch, [{"state": "fail", "failCode": code, "failMsg": reason}])
    with pytest.raises(media.AccountStop):
        media.image("Example", hub / "scene.png")
    assert len(creates) == 1


def test_uncertain_image_creation_does_not_try_other_models(hub, monkeypatch):
    calls = []
    def create(*a, **kw):
        calls.append(kw)
        raise media.requests.ReadTimeout()
    monkeypatch.setattr(media.requests, "post", create)
    for _ in range(2):
        with pytest.raises(media.AccountStop):
            media.image("Example", hub / "scene.png")
    assert len(calls) == 1


def test_known_create_rejection_can_fallback_but_ambiguous_500_cannot(hub, monkeypatch):
    creates = kie_tasks(monkeypatch, ["success", "success"])
    original = media.requests.post
    def reject_first(*a, **kw):
        if kw["json"]["model"] == "google/nano-banana":
            return response({"code": 422, "msg": "Flagged as sensitive"})
        return original(*a, **kw)
    monkeypatch.setattr(media.requests, "post", reject_first)
    media.image("Example", hub / "known.png")
    assert len(creates) == 1 and creates[0]["model"] == "nano-banana-2-1"
    calls = []
    monkeypatch.setattr(media.requests, "post", lambda *a, **kw: calls.append(kw) or response({"code": 500}, status=500))
    with pytest.raises(media.AccountStop):
        media.image("Example", hub / "uncertain.png")
    assert len(calls) == 1


def test_pending_or_failed_transfer_does_not_create_a_fallback(hub, monkeypatch):
    creates = kie_tasks(monkeypatch, ["success"])
    monkeypatch.setattr(media, "download", lambda *a: (_ for _ in ()).throw(media.AccountStop("Transfer stopped")))
    with pytest.raises(media.AccountStop, match="Transfer stopped"):
        media.image("Example", hub / "scene.png")
    assert len(creates) == 1


def test_pending_image_keeps_its_task_id_and_does_not_fallback(hub, monkeypatch):
    creates = kie_tasks(monkeypatch, [{"state": "processing"}])
    original = media.run
    monkeypatch.setattr(media, "run", lambda *a, **kw: original(*a, timeout_s=0, **kw))
    for _ in range(2):
        with pytest.raises(media.AccountStop, match="still pending"):
            media.image("Example", hub / "scene.png")
    assert len(creates) == 1


def test_image_fallbacks_can_be_disabled_and_independent_kie_images_keep_voice_wallet(hub, monkeypatch):
    media.cr_bridge.save_settings({"costs_accepted": True,
        "media_hub": {"provider": "atlas", "tasks": ["images", "narration"]},
        "images": {"provider": "kie", "model": "gpt-image-2", "fallbacks": []}})
    assert media.account("images")[0] == "kie" and media.account("narration")[0] == "atlas"
    creates = kie_tasks(monkeypatch, [{"state": "fail", "failCode": "500", "failMsg": "Internal error"}])
    with pytest.raises(media.AccountStop, match="after 1 selected models"):
        media.image("Example", hub / "scene.png")
    assert len(creates) == 1 and creates[0]["model"] == "gpt-image-2-text-to-image"
    assert creates[0]["input"] == {"prompt": "Example", "aspect_ratio": "16:9", "resolution": "1K", "background": "opaque"}


def test_native_kie_images_work_without_a_hub_and_voice_does_not_choose_images(hub, monkeypatch):
    media.cr_bridge.save_settings({"costs_accepted": True, "narration": {"provider": "kie"}})
    monkeypatch.setattr(media, "_native_images", lambda: "wavespeed")
    assert not media.active("images")
    monkeypatch.setattr(media, "_native_images", lambda: "kie")
    creates = kie_tasks(monkeypatch, ["success"])
    media.image("Example", hub / "native.png")
    assert len(creates) == 1 and creates[0]["model"] == "google/nano-banana"


@pytest.mark.parametrize("family", ["google/nano-banana", "nano-banana-2", "nano-banana-2-1", "gpt-image-2", "gpt-image-2-5-flare"])
def test_supported_image_models_preserve_reference_edits(family):
    choice = media.image_model(family)
    refs = ["https://files.example/ref.png"]
    model, inputs = media.image_input(choice, "Example", refs)
    assert model == choice["edit"] and inputs[choice["reference_field"]] == refs
    assert inputs["aspect_ratio"] == "16:9"
    assert media.image_model(choice["generate"]) == media.image_model(choice["edit"]) == choice
