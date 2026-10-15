"""A running video survives brief outages (deploys, Wi-Fi/DNS drops) without duplicate work.

Only failures that happened before any request reached ChannelRecipe are
retried. Anything that may have reached the server keeps the existing
no-automatic-retry rule.
"""
import sys
from pathlib import Path

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NameResolutionError, NewConnectionError, ProtocolError

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_bridge  # noqa: E402
import cr_mcp  # noqa: E402


def refused():
    reason = NewConnectionError(None, "Failed to establish a new connection: [Errno 61] Connection refused")
    return requests.exceptions.ConnectionError(MaxRetryError(None, "/mcp", reason=reason))


def dns_failure():
    reason = NameResolutionError("channelrecipe.com", None, OSError("nodename nor servname provided"))
    return requests.exceptions.ConnectionError(MaxRetryError(None, "/mcp", reason=reason))


def reset_after_send():
    return requests.exceptions.ConnectionError(ProtocolError("Connection aborted.", ConnectionResetError(54)))


@pytest.mark.parametrize("make", [refused, dns_failure, lambda: requests.exceptions.ConnectTimeout("connect timed out")])
def test_connect_failures_never_reached_the_server(make):
    assert cr_mcp.never_reached_server(make())


@pytest.mark.parametrize("error", [reset_after_send(), requests.exceptions.ReadTimeout("read timed out"),
                                   requests.exceptions.HTTPError("503")])
def test_anything_that_may_have_reached_the_server_is_not_retried(error):
    assert not cr_mcp.never_reached_server(error)
    calls, sleeps = [], []

    def send():
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        cr_mcp.request_with_connect_retry(send, sleep=sleeps.append)
    assert len(calls) == 1 and sleeps == []


def test_a_deploy_blip_is_ridden_out_with_back_off():
    attempts, sleeps = iter([refused(), dns_failure(), "ok"]), []

    def send():
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    assert cr_mcp.request_with_connect_retry(send, sleep=sleeps.append) == "ok"
    assert sleeps == [2, 4]


def test_a_long_outage_still_stops_with_the_saved_work_message(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise refused()

    monkeypatch.setattr(cr_mcp.requests, "post", post)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="Could not connect"):
        cr_mcp._message("https://channelrecipe.com/mcp", {}, {"jsonrpc": "2.0", "id": 2, "method": "tools/call"})
    assert len(calls) == len(cr_mcp.CONNECT_RETRY_DELAYS) + 1


def test_bridge_calls_retry_connect_failures_too(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"version": "studio"}

    attempts = iter([refused(), Response()])

    def request(*args, **kwargs):
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cr_bridge.requests, "request", request)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = {"url": "https://channelrecipe.com", "key": "cr_mcp_" + "x" * 20}
    assert cr_bridge.call("status", settings=settings)["version"] == "studio"


class Reply:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("not JSON (a gateway page)")
        return self._body


def brain_settings():
    return {"url": "https://channelrecipe.com", "key": "cr_mcp_" + "x" * 20}


def test_brain_requests_ride_out_gateway_cuts_busy_providers_and_long_answers(monkeypatch):
    replies = iter([Reply(504), Reply(409, {"detail": "still"}, {"X-CR-Request-State": "running", "Retry-After": "5"}),
                    Reply(429, {"detail": "busy"}, {"Retry-After": "7"}), Reply(200, {"text": "an answer"})])
    sent, sleeps = [], []
    monkeypatch.setattr(cr_bridge.requests, "request", lambda *a, **k: sent.append(k["json"]) or next(replies))
    result = cr_bridge._call("brain", {"request_id": "r1"}, brain_settings(), patient=True, sleep=sleeps.append)
    assert result["text"] == "an answer"
    assert sleeps == [10, 5, 7]
    assert sent == [{"request_id": "r1"}] * 4, "always the same request: the server collects, never repeats"


def test_brain_requests_stop_waiting_after_their_patience(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(cr_bridge.requests, "request", lambda *a, **k: Reply(429, {"detail": "The brain is busy."}, {"Retry-After": "60"}))

    def sleep(seconds):
        now[0] += seconds

    with pytest.raises(RuntimeError, match="busy"):
        cr_bridge._call("brain", {"request_id": "r1"}, brain_settings(), patient=True, sleep=sleep, clock=lambda: now[0])
    assert now[0] >= cr_bridge.BRAIN_PATIENCE_S


def test_other_requests_keep_their_short_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(cr_bridge.requests, "request", lambda *a, **k: calls.append(1) or Reply(429, {"detail": "busy"}))
    with pytest.raises(RuntimeError):
        cr_bridge._call("status", None, brain_settings(), sleep=lambda s: None)
    assert len(calls) == 4
    with pytest.raises(RuntimeError, match="could not answer"):
        monkeypatch.setattr(cr_bridge.requests, "request", lambda *a, **k: Reply(504))
        cr_bridge._call("status", None, brain_settings(), sleep=lambda s: None)


def test_a_studio_sends_a_few_model_requests_at_a_time():
    assert cr_bridge.BRAIN_AT_ONCE == 3 and cr_bridge._brain_slots._value == 3
