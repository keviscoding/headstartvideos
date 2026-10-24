"""Kie's finished files through ChannelRecipe when a creator's network cuts every connection to Kie's file host
(aiquickdraw.com, cut during the handshake), and plain reasons when a video worker dies."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_relay  # noqa: E402
import cr_video_cancel  # noqa: E402

FILE = "https://tempfile.aiquickdraw.com/r/voice-123.mp3"


@pytest.fixture
def relay(tmp_path, monkeypatch):
    sent = []

    def fake_send(self, request, **kwargs):
        sent.append(request)
        if (urlsplit(request.url).hostname or "").endswith("aiquickdraw.com"):
            raise requests.exceptions.SSLError("EOF occurred in violation of protocol (_ssl.c:1000)")
        response = requests.Response()
        response.status_code, response._content, response.url = 200, b"the voice", request.url
        return response
    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    monkeypatch.setattr(cr_relay, "BLOCKED_FILE", tmp_path / ".cr-relay-hosts.json")
    monkeypatch.setitem(sys.modules, "cr_bridge", SimpleNamespace(load_settings=lambda: {
        "key": "cr_mcp_" + "k" * 24, "url": "https://channelrecipe.com"}))
    cr_relay.install()
    return sent


def test_a_cut_connection_to_kies_file_host_comes_through_channelrecipe(relay):
    got = requests.get(FILE, headers={"Authorization": "Bearer kie-secret"}, timeout=5)
    assert got.content == b"the voice"
    direct, via = relay
    assert direct.url == FILE
    assert via.url.startswith("https://channelrecipe.com/api/studio/media-relay?url=")
    assert via.headers["Authorization"] == "Bearer cr_mcp_" + "k" * 24, "Kie's own key never goes to ChannelRecipe"
    assert cr_relay.blocked("tempfile.aiquickdraw.com")
    requests.get(FILE.replace("voice-123", "picture-7"), timeout=5)
    assert len(relay) == 3 and "media-relay" in relay[-1].url, "the rest of the video goes straight through"


def test_other_hosts_and_uploads_are_never_relayed(relay):
    requests.get("https://api.kie.ai/api/v1/jobs/recordInfo", timeout=5)
    with pytest.raises(requests.exceptions.SSLError):
        requests.post(FILE, data=b"x", timeout=5)
    assert [r.url for r in relay] == ["https://api.kie.ai/api/v1/jobs/recordInfo", FILE]
    assert not cr_relay.blocked("tempfile.aiquickdraw.com")


def test_without_a_connected_account_the_original_error_stands(relay, monkeypatch):
    monkeypatch.setitem(sys.modules, "cr_bridge", SimpleNamespace(load_settings=lambda: {}))
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(FILE, timeout=5)


@pytest.mark.parametrize("tail,code,reason", [
    ("whisper: loading model 'base' (CPU, int8)…\nOMP: Error #15: Initializing libiomp5md.dll, but found libiomp5md.dll "
     "already initialized.", 3, "maths library clashed"),
    ("images: generating 295 stills\nMemoryError", 1, "ran out of memory"),
    ("whisper: loading model 'base' (CPU, int8)…", 3221225477, "a library it uses crashed"),
    ("whisper: loading model 'base' (CPU, int8)…", -4, "processor instruction"),
    ("Your voiceover is not there any more: take.m4a", 1, "exit code 1")])
def test_a_worker_that_dies_says_why(tmp_path, tail, code, reason):
    log = tmp_path / "worker.log"
    log.write_text("=== 2/3  Voiceover + story pictures ===\n" + tail + "\n", encoding="utf-8")
    message = cr_video_cancel.crash_message(log, code)
    assert reason in message and "Resume saved project" in message
    if "voiceover is not there" in tail:
        assert "Last message: Your voiceover is not there any more" in message
    assert "whisper: loading" not in message, "progress lines are not the reason"
