"""The website's AI calls run on Kie when Atlas is out (core/kie_site.py).

Atlas Cloud ran out of credit on 2026-11-13 and every Classic step that used it failed: titles, scripts, voiceover,
thumbnails, storyboard pictures (a customer, 2026-11-15: "Channelrecipe classic is down"). The owner: "use Kie.AI
instead of atlas". These pin that each Atlas entry point hands its request to Kie, in Kie's shape, and that a
customer's own Atlas key, or SITE_AI_PROVIDER=atlas, keeps Atlas."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import kie_site  # noqa: E402


class _Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content = status, body if body is not None else {}, content
        self.text = json.dumps(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class _Kie:
    """A stand-in Kie: tasks finish on the first poll; `balance` False refuses every request with Kie's 402."""

    def __init__(self, balance=True, chat_answers=None):
        self.balance, self.tasks, self.posts = balance, [], []
        self.chat_answers = list(chat_answers or [])

    def post(self, url, **kw):
        self.posts.append((url, kw))
        if not self.balance:
            return _Resp(200, {"code": 402, "msg": "Credits insufficient"})
        if url.endswith("/chat/completions"):
            answer = self.chat_answers.pop(0) if self.chat_answers else {
                "choices": [{"message": {"content": "A title"}, "finish_reason": "stop"}]}
            return answer if isinstance(answer, _Resp) else _Resp(200, answer)
        if url == kie_site.UPLOAD_URL:
            return _Resp(200, {"code": 200, "data": {"downloadUrl": "https://kie.example/ref.png"}})
        if url.endswith("/createTask"):
            self.tasks.append(kw["json"])
            return _Resp(200, {"code": 200, "data": {"taskId": f"t{len(self.tasks)}"}})
        raise AssertionError(url)

    def get(self, url, **kw):
        if url.endswith("/recordInfo"):
            return _Resp(200, {"code": 200, "data": {"state": "success", "resultJson": json.dumps(
                {"resultUrls": [f"https://kie.example/{kw['params']['taskId']}.png"]})}})
        return _Resp(200, content=b"file-bytes")


@pytest.fixture
def kie(monkeypatch):
    fake = _Kie()
    monkeypatch.setattr(config, "KIE_API_KEY", "kie-test-key")
    monkeypatch.setattr(config, "SITE_AI_PROVIDER", "kie")
    monkeypatch.setattr(kie_site.httpx, "post", fake.post)
    monkeypatch.setattr(kie_site.httpx, "get", fake.get)
    monkeypatch.setattr(kie_site, "POLL_S", 0)
    return fake


def test_active_only_with_a_key_and_never_for_a_customers_own_atlas_key(kie, monkeypatch):
    from core.atlas_runtime import use_atlas_key
    assert kie_site.active()
    with use_atlas_key("customer-own-atlas-key"):
        assert not kie_site.active()
    monkeypatch.setattr(config, "SITE_AI_PROVIDER", "atlas")
    assert not kie_site.active()
    monkeypatch.setattr(config, "SITE_AI_PROVIDER", "kie")
    monkeypatch.setattr(config, "KIE_API_KEY", "")
    assert not kie_site.active()


def test_titles_and_scripts_text_goes_to_kie(kie, monkeypatch):
    from core import atlas_llm
    monkeypatch.setattr(atlas_llm, "_atlas_chat", lambda *a, **k: pytest.fail("Atlas was called"))
    assert atlas_llm.generate_text("Write a title", system="You write titles", max_tokens=100) == "A title"
    url, kw = kie.posts[0]
    assert url == "https://api.kie.ai/gemini-3-8-flash-openai/v1/chat/completions"
    assert kw["json"]["messages"][0] == {"role": "system", "content": "You write titles"}
    assert "model" not in kw["json"] and kw["headers"]["Authorization"] == "Bearer kie-test-key"


def test_text_retries_a_busy_kie_and_says_provider_balance_when_out(kie, monkeypatch):
    monkeypatch.setattr(kie_site.time, "sleep", lambda s: None)
    kie.chat_answers = [_Resp(503, {}), {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}]
    assert kie_site.chat("hi") == "ok"
    kie.balance = False
    with pytest.raises(kie_site.KieError, match="provider balance"):
        kie_site.chat("hi")


def test_voiceover_reads_each_site_voice_with_its_gemini_stand_in(kie, monkeypatch, tmp_path):
    from core import voiceover_gen
    monkeypatch.setattr(voiceover_gen, "_atlas_tts_chunk", lambda *a: pytest.fail("Atlas was called"))
    saved = []
    monkeypatch.setattr(voiceover_gen, "_download_audio", lambda url, out: (saved.append(url),
                                                                             Path(out).write_bytes(b"w" * 2000)))
    monkeypatch.setattr(voiceover_gen, "_concat_wavs", lambda paths, out: Path(out).write_bytes(b"done") or out)
    script = ("This is one sentence of a long narration. " * 150).strip()          # ~6,300 chars: three chunks
    path = voiceover_gen.generate_voiceover(script, voice="78a495fdbb39", output_dir=str(tmp_path))
    assert Path(path).read_bytes() == b"done" and len(saved) == len(kie.tasks) == 3
    first = kie.tasks[0]
    assert first["model"] == "google/gemini-3-8-flash-tts"
    assert first["input"]["speakers"][0]["voice_name"] == "Puck"                     # James
    assert {kie_site.gemini_voice(v) for v in ("leo", "Ara", "eve", "96819d0bd28d", "Leo -- Authoritative narrator")} \
        == {"Charon", "Sulafat", "Laomedeia", "Gacrux"}


def test_voiceover_out_of_kie_credit_is_a_503_that_names_kie(kie, tmp_path):
    from core import voiceover_gen
    from webapp import provider_alerts
    kie.balance = False
    with pytest.raises(RuntimeError, match="provider balance") as stop:
        voiceover_gen.generate_voiceover("Short line to read.", voice="ara", output_dir=str(tmp_path))
    assert provider_alerts.which(str(stop.value))[0] == "Kie"


def test_thumbnails_and_storyboard_pictures_keep_their_family_and_references(kie, tmp_path):
    from core import thumbnail_gen as tg
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"png")
    url = tg._upload_media(str(ref))
    assert url == "https://kie.example/ref.png"
    tg._submit_and_download({"model": "google/nano-banana-2-lite/edit-developer", "prompt": "a thumbnail",
                             "images": [url], "aspect_ratio": "16:9", "resolution": "1k"},
                            tmp_path / "a.png", label="edit")
    tg._submit_and_download({"model": "openai/gpt-image-2-developer/text-to-image", "prompt": "a cast member",
                             "aspect_ratio": "1:1"}, tmp_path / "b.png", label="cast t2i")
    edit, cast = kie.tasks
    assert edit["model"] == "nano-banana-2-1" and edit["input"]["image_input"] == [url]
    assert edit["input"]["aspect_ratio"] == "16:9" and edit["input"]["resolution"] == "1K"
    assert cast["model"] == "gpt-image-2-text-to-image" and cast["input"]["aspect_ratio"] == "1:1"
    assert (tmp_path / "a.png").read_bytes() == b"file-bytes"


def test_thumbnail_out_of_kie_credit_says_so(kie, tmp_path):
    from core import thumbnail_gen as tg
    kie.balance = False
    with pytest.raises(RuntimeError, match="provider balance: Kie.ai"):
        tg.generate_thumbnail_no_refs("A title", output_dir=str(tmp_path), count=2)


def test_cook_stills_go_to_kie_fast_model(kie, tmp_path):
    from core import atlas_llm
    ok, err = atlas_llm.generate_ernie_image_file_detailed("a misty forest at dawn", str(tmp_path / "s.png"))
    assert ok and not err
    assert atlas_llm.generate_hq_image_file("an explainer still", str(tmp_path / "h.png"))
    fast, hq = kie.tasks
    assert fast["model"] == "google/nano-banana" and fast["input"]["image_size"] == "16:9"
    assert hq["model"] == "gpt-image-2-text-to-image"


def test_vision_calls_move_to_kie_in_the_same_shape(kie, monkeypatch):
    body = {"model": "google/gemini-3.1-flash-lite", "max_tokens": 50,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:x"}}]}]}
    url, headers, sent = kie_site.openai_target("https://api.atlascloud.ai/v1/chat/completions", {"a": 1}, body)
    assert url.startswith("https://api.kie.ai/") and "model" not in sent and sent["messages"] == body["messages"]
    monkeypatch.setattr(config, "SITE_AI_PROVIDER", "atlas")
    assert kie_site.openai_target("u", {"a": 1}, body) == ("u", {"a": 1}, body)


def test_a_failed_kie_task_is_an_error_not_a_wait(kie, monkeypatch, tmp_path):
    monkeypatch.setattr(kie_site.httpx, "get", lambda url, **kw: _Resp(200, {"code": 200, "data": {
        "state": "fail", "failCode": "500", "failMsg": "generation failed"}}))
    with pytest.raises(kie_site.KieError, match="generation failed"):
        kie_site.image("x", tmp_path / "x.png")
