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


def test_a_connection_that_never_opened_is_tried_again_and_creates_one_task(hub, monkeypatch):
    creates = []
    def create(url, **kw):
        creates.append(kw)
        if len(creates) < 3:
            raise media.requests.ConnectTimeout("the provider did not answer the connection")
        return response({"code": 200, "data": {"taskId": "task-9"}})
    monkeypatch.setattr(media.requests, "post", create)
    monkeypatch.setattr(media.requests, "get", lambda url, **kw: response({"code": 200, "data": {
        "state": "success", "resultJson": json.dumps({"resultUrls": ["https://files.example/i.png"]})}}))
    assert media.run("images", {"prompt": "Example"}, hub / "i.png") == "https://files.example/i.png"
    assert len(creates) == 3


@pytest.mark.parametrize("error", ["ReadTimeout", "aborted"])
def test_a_request_that_may_have_arrived_is_never_sent_twice(hub, monkeypatch, error):
    calls = []
    def create(*a, **kw):
        calls.append(kw)
        if error == "ReadTimeout":
            raise media.requests.ReadTimeout("no answer")
        raise media.requests.ConnectionError("('Connection aborted.', RemoteDisconnected('closed'))")
    monkeypatch.setattr(media.requests, "post", create)
    with pytest.raises(media.AccountStop, match="interrupted"):
        media.run("images", {"prompt": "Example"}, hub / "u.png")
    assert len(calls) == 1
    receipt = json.loads(next((hub / ".cr-tasks").glob("u.png-*.json")).read_text())
    assert receipt["status"] == "submitting"
    assert receipt.get("submit_error", "ReadTimeout") in ("ReadTimeout", "ConnectionError")


def test_a_refusal_says_which_code_and_keeps_it_with_the_receipt(hub, monkeypatch):
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: response({"code": 500, "msg": "upstream wording"}))
    with pytest.raises(media.AccountStop, match=r"refused this request \(code 500\)") as stop:
        media.run("avatars", {"image_url": "https://i", "audio_url": "https://a"}, hub / "avatar.mp4")
    assert "upstream" not in str(stop.value)
    receipt = json.loads(next((hub / ".cr-tasks").glob("avatar.mp4-*.json")).read_text())
    assert "code 500" in receipt["submit_error"] and receipt["status"] == "submitting"


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
    said = []
    media.avatar_clip("pruna", "https://files.example/face", "https://files.example/voice", hub / "presenter.mp4",
                      log=said.append)
    assert calls[3][1]["model"] == media.PROVIDERS[provider]["models"]["avatars"], \
        "a recipe's other presenter model speaks through Kling Standard instead of stopping the video"
    assert "Kling Standard" in said[0]


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


def test_accepted_voice_upstream_blank_id_failure_is_explained_without_retry(hub, monkeypatch):
    creates = []
    monkeypatch.setattr(media.requests, "post", lambda *a, **k:creates.append(k) or
        response({"code":200,"data":{"taskId":"valid-saved-task"}}))
    monkeypatch.setattr(media.requests, "get", lambda *a, **k:response({"code":200,"data":{
        "state":"fail","failCode":"422","failMsg":"generate playground failed, task id is blank",
        "creditsConsumed":0.0}}))
    for _ in range(2):
        with pytest.raises(media.TaskFailed) as failed:
            media.run("narration", {"text":"Original script","voice":"Kore"}, hub / "voice.wav")
        assert "CR saved a valid task ID" in str(failed.value)
        assert "Kie reported 0 credits consumed" in str(failed.value)
        assert "No retry was submitted" in str(failed.value)
    assert len(creates) == 1
    record = json.loads(next((hub / ".cr-tasks").glob("*.json")).read_text())
    assert record["id"] == "valid-saved-task" and record["status"] == "failed"


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


def test_a_style_picture_model_goes_first_unless_the_creator_chose_another(hub, monkeypatch):
    refs = ["https://files.example/style.png"]
    creates = kie_tasks(monkeypatch, ["success", "success", "success"])
    media.image("A kitchen", hub / "styled.png", ref_urls=refs, model="nano-banana-2-1")
    assert creates[-1]["model"] == "nano-banana-2-1" and creates[-1]["input"]["image_input"] == refs
    # the current job's style (look.image_model) works the same way, and the default model stays the backup
    monkeypatch.setitem(media._style_model, "id", "nano-banana-2-1")
    media.image("A street", hub / "job.png", ref_urls=refs)
    assert creates[-1]["model"] == "nano-banana-2-1"
    assert media.image_plan({"model": "google/nano-banana"}, prefer="nano-banana-2-1")[:2] == \
        ["nano-banana-2-1", "google/nano-banana"]
    # a model the creator picked in Setup wins over the style; an unknown style model is ignored
    assert media.image_plan({"model": "gpt-image-2"}, prefer="nano-banana-2-1")[0] == "gpt-image-2"
    assert media.image_plan({}, prefer="no-such-model")[0] == "google/nano-banana"
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": "kie", "tasks": sorted(media.TASKS)},
                                   "images": {"provider": "kie", "model": "gpt-image-2", "fallbacks": []}})
    media.image("A kitchen", hub / "chosen.png", ref_urls=refs)
    assert creates[-1]["model"] == "gpt-image-2-image-to-image"


def test_pictures_run_four_at_a_time_and_other_tasks_two():
    assert media._slots["images"]._value == 4 and media._other_slots._value == 2


def _voice_engine():
    words = [SimpleNamespace(word="Hello", start=0.1, end=0.4), SimpleNamespace(word="world.", start=0.5, end=0.8)]
    return SimpleNamespace(log=lambda *_: None, _get_whisper=lambda: SimpleNamespace(
        transcribe=lambda *a, **k: ([SimpleNamespace(words=words)], None)),
        _srt_ts=lambda n: "00:00:00," + str(int(n * 1000)).zfill(3), _script_words_into_srt=lambda *a: None)


KIE_INTERNAL = {"state": "fail", "failCode": "500", "failMsg": "Internal Error, Please try again later", "creditsConsumed": 0}


def test_uncharged_kie_narration_internal_error_is_tried_again(hub, monkeypatch):
    creates = kie_tasks(monkeypatch, [KIE_INTERNAL, "success"])
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    pauses = []
    monkeypatch.setattr(media.time, "sleep", lambda s: pauses.append(s))
    media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")
    assert len(creates) == 2 and (hub / "audio.mp3").read_bytes() == b"generated media"
    assert 30 in pauses
    receipts = sorted(p.name for p in (hub / "audio-hub-voice" / ".cr-tasks").glob("*.json"))
    assert len(receipts) == 2 and any(".failed-" in r for r in receipts)        # the failed receipt is kept


def test_narration_stops_after_three_uncharged_failures_and_resume_tries_again(hub, monkeypatch):
    creates = kie_tasks(monkeypatch, [KIE_INTERNAL] * 3 + ["success"])
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    with pytest.raises(media.TaskFailed, match="Kie charged nothing for it.*Resume saved project"):
        media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")
    assert len(creates) == 3
    media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")   # the creator's Resume
    assert len(creates) == 4


def test_resume_tries_again_when_kie_did_not_say_what_the_failed_voice_cost(hub, monkeypatch):
    unknown = {"state": "fail", "failCode": "500", "failMsg": "Internal Error, Please try again later"}
    creates = kie_tasks(monkeypatch, [unknown, "success"])
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    with pytest.raises(media.TaskFailed, match="press Resume saved project") as failed:
        media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")
    assert "charged nothing" not in str(failed.value) and len(creates) == 1        # no automatic retry
    media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")   # the creator's Resume
    assert len(creates) == 2


@pytest.mark.parametrize("state", [
    {"state": "fail", "failCode": "500", "failMsg": "Internal Error, Please try again later", "creditsConsumed": 1.2},
    {"state": "fail", "failCode": "422", "failMsg": "Flagged as sensitive", "creditsConsumed": 0},
    {"state": "fail", "failCode": "402", "failMsg": "Insufficient credits", "creditsConsumed": 0},
    {"state": "fail", "failCode": "422", "failMsg": "generate playground failed, task id is blank", "creditsConsumed": 0},
    {"state": "fail", "failCode": "400", "failMsg": "Text too long", "creditsConsumed": 0}])
def test_other_narration_failures_never_submit_again(hub, monkeypatch, state):
    creates = kie_tasks(monkeypatch, [state, "success"])
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    for _ in range(2):
        with pytest.raises(media.TaskFailed):
            media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")
    assert len(creates) == 1


def test_uncharged_image_failures_keep_their_own_model_fallbacks(hub, monkeypatch):
    receipt = {"status": "failed", "id": "t", "credits": 0, "failure_code": "500", "failure_message": "Internal error"}
    assert media.fresh_attempt_ok("kie", "narration", receipt)
    assert not media.fresh_attempt_ok("kie", "images", receipt)
    assert not media.fresh_attempt_ok("atlas", "narration", receipt)
    assert not media.fresh_attempt_ok("kie", "narration", {**receipt, "rejected": True})
    assert not media.fresh_attempt_ok("kie", "narration", {**receipt, "id": None})
    assert not media.fresh_attempt_ok("kie", "narration", {**receipt, "credits": True})
    assert not media.fresh_attempt_ok("kie", "narration", {**receipt, "credits": None})
    assert media.fresh_attempt_ok("kie", "narration", {**receipt, "credits": None}, asked=True)
    assert not media.fresh_attempt_ok("kie", "narration", {**receipt, "credits": 3}, asked=True)


GEMINI = "google/gemini-3-8-flash-tts"


def test_a_stopped_voice_is_recorded_and_the_video_can_be_voiced_with_gemini_through_kie(hub, monkeypatch):
    """A creator's Kie ElevenLabs voice answered "Internal Error" three times; Studio could only offer waiting. The
    stop is recorded for the choices, and a Gemini choice voices that video with the same Kie key."""
    media.cr_bridge.save_settings({"costs_accepted": True, "narration": {"provider": "kie"}})
    creates = kie_tasks(monkeypatch, [KIE_INTERNAL] * 3 + ["success"])
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    monkeypatch.setattr(media.subprocess, "run", lambda args, **k: Path(args[-1]).write_bytes(b"mp3"))  # Gemini: wav
    with pytest.raises(media.TaskFailed, match="voice this video another way"):
        media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")
    failed = json.loads((hub / media.VOICE_FAILED).read_text())
    assert failed["provider"] == "kie" and failed["charged"] == 0 and "Internal Error" in failed["reason"]
    (hub / media.VOICE_CHOICE).write_text(json.dumps({"provider": "kie", "model": GEMINI, "voice": "Charon"}))
    media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")
    asked = creates[-1]
    assert asked["model"] == GEMINI and asked["input"]["speakers"][0]["voice_name"] == "Charon"
    assert media.profile("narration") == {"provider": "kie", "tasks": ["narration"]}, "Setup's choice is unchanged"


def test_a_gemini_choice_pays_with_kie_even_when_setup_narrates_elsewhere(hub, monkeypatch):
    media.cr_bridge.save_settings({"costs_accepted": True, "narration": {"provider": "original"}})
    creates = kie_tasks(monkeypatch, ["success"])
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    monkeypatch.setattr(media.subprocess, "run", lambda args, **k: Path(args[-1]).write_bytes(b"mp3"))  # Gemini: wav
    (hub / media.VOICE_CHOICE).write_text(json.dumps({"provider": "kie", "model": GEMINI, "voice": "Kore"}))
    media.narrate(_voice_engine(), "Hello world.", hub / "audio.mp3", hub / "subs.srt")
    assert creates[0]["model"] == GEMINI and creates[0]["input"]["speakers"][0]["voice_name"] == "Kore"
    assert media.profile("narration") == {}, "the choice routes only while that video's voice is made"


def test_a_voice_choice_routes_this_video_only(hub, monkeypatch):
    calls = []
    engine = SimpleNamespace(log=lambda *_: None, _split_script_by_minutes=lambda s, m: [s],
                             _speed_voice=lambda *a: None, _regroup_word_srt=lambda *a: calls.append("regroup"),
                             _denoise_voice=lambda mp3, style: mp3)
    original = lambda script, job, force, avatar_min, style: calls.append(("engine", Path(job).name)) or ("a", "s")
    voiceover = media.make_voiceover(engine, original)
    job = hub / "video-one"
    job.mkdir()
    (job / media.VOICE_FAILED).write_text("{}")
    (job / media.VOICE_CHOICE).write_text(json.dumps({"provider": "original"}))
    assert voiceover("Hello world.", job) == ("a", "s") and calls == [("engine", "video-one")]
    assert not (job / media.VOICE_FAILED).exists(), "a voice that worked clears the stop"
    owned = []
    monkeypatch.setattr(media, "own_recording", lambda eng, script, j, f, force: owned.append((j.name, f)) or ("m", "t"))
    (job / media.VOICE_CHOICE).write_text(json.dumps({"provider": "own", "file": "my-take-1a2b3c4d.mp3"}))
    assert voiceover("Hello world.", job) == ("m", "t") and owned == [("video-one", "my-take-1a2b3c4d.mp3")]
    assert media.job_voice(hub / "another-video") == {}
    (job / media.VOICE_CHOICE).write_text(json.dumps({"provider": "kie", "model": "no/such-model"}))
    assert media.job_voice(job) == {}, "an unknown model falls back to Setup's voice"


def test_an_own_recording_becomes_the_voice_timed_and_spelled_as_the_script(hub, monkeypatch):
    import types
    recording = hub / "uploads" / "my-take.mp3"
    recording.parent.mkdir()
    recording.write_bytes(b"my voice")
    monkeypatch.setitem(sys.modules, "voices", types.SimpleNamespace(resolve=lambda ref: recording if ref == "my-take.mp3" else None))
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    spelled = []
    engine = _voice_engine()
    engine._script_words_into_srt = lambda srt, words: spelled.append(words)
    engine._regroup_word_srt = lambda srt: None
    job = hub / "video-two"
    job.mkdir()
    (job / media.VOICE_FAILED).write_text("{}")
    mp3, srt = media.own_recording(engine, "Hello world.", job, "my-take.mp3")
    assert mp3.read_bytes() == b"my voice" and "00:00:00,100 --> 00:00:00,800" in srt.read_text()
    assert spelled == ["Hello world."] and not (job / media.VOICE_FAILED).exists()
    assert not (job / "voice.own").exists(), "the engine's own mark is never set (a normal Resume would delete it)"
    with pytest.raises(media.AccountStop, match="isn't there any more"):
        media.own_recording(engine, "Hello world.", job, "gone.mp3")


# Uploads on a slow connection (a creator's footage uploads failed every time, 2026-10-24): an upload is free, so a
# dropped or timed-out one is sent again, with time for its size, and a plain message when it still fails.

def upload_attempts(monkeypatch, answers):
    posts = []

    def post(url, **kw):
        posts.append({"url": url, "timeout": kw["timeout"], "file": kw["files"]["file"][1].read()})
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(media.requests, "post", post)
    return posts


KIE_UPLOADED = response({"code": 200, "data": {"downloadUrl": "https://tempfile.redpandaai.co/clip.mp4"}})


def test_a_dropped_upload_is_sent_again_and_succeeds(hub, monkeypatch):
    clip = hub / "clip.mp4"
    clip.write_bytes(b"x" * 1000)
    waits = []
    monkeypatch.setattr(media.time, "sleep", lambda s: waits.append(s))
    posts = upload_attempts(monkeypatch, [
        media.requests.exceptions.ConnectionError("('Connection aborted.', TimeoutError('The write operation timed out'))"),
        response({}, status=502), KIE_UPLOADED])
    assert media.upload(clip, task="motion") == "https://tempfile.redpandaai.co/clip.mp4"
    assert len(posts) == 3 and all(p["file"] == b"x" * 1000 for p in posts), "the whole file each time"
    assert waits == [10, 30]
    assert media.upload(clip, task="motion") == "https://tempfile.redpandaai.co/clip.mp4" and len(posts) == 3, \
        "a finished upload is reused, not sent again"


def test_a_big_clip_gets_time_for_its_size():
    assert media.upload_timeout(1000) == (15, 180), "never under three minutes"
    assert media.upload_timeout(50 * 1024 * 1024) == (15, 50 * 32 + 120), "50 MB at 32 KB a second, then the answer"
    assert media.upload_timeout(10 * 1024 ** 3) == (15, 3600), "at most an hour"


def test_an_upload_that_never_finishes_says_why_in_plain_words(hub, monkeypatch):
    clip = hub / "big.mp4"
    clip.write_bytes(b"y" * (3 * 1024 * 1024))
    posts = upload_attempts(monkeypatch, [media.requests.exceptions.ReadTimeout("read timed out")] * 3)
    with pytest.raises(media.AccountStop) as stop:
        media.upload(clip, task="motion")
    message = str(stop.value)
    assert len(posts) == 3 and posts[0]["timeout"] == media.upload_timeout(3 * 1024 * 1024)
    assert "big.mp4 (3.0 MB)" in message and "didn't finish after 3 tries" in message
    assert "too slow to finish in time" in message and "Nothing was charged" in message
    assert "Resume saved project" in message and "faster or wired connection" in message


def test_a_refused_upload_is_not_sent_again(hub, monkeypatch):
    clip = hub / "ref.png"
    clip.write_bytes(b"png")
    posts = upload_attempts(monkeypatch, [response({}, status=401)])
    with pytest.raises(media.AccountStop, match="HTTP 401"):
        media.upload(clip, task="images")
    assert len(posts) == 1, "a refusal is the account, not the connection"
