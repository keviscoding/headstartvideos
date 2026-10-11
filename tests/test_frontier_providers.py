"""Complete streamed answers, bounded waits and safe owner-account diagnostics."""
import json
from types import SimpleNamespace

import pytest
import requests
from urllib3.exceptions import ReadTimeoutError

from webapp import frontier_providers as providers


def chunk(text="", reason=None, **usage):
    candidate = {"content": {"parts": [{"text": text}]}}
    if reason:
        candidate["finishReason"] = reason
    data = {"candidates": [candidate]}
    if usage:
        data["usageMetadata"] = usage
    return data


class Response:
    def __init__(self, *events, status=200):
        self.events, self.status_code, self.closed = events, status, False
        self.headers = {"x-request-id": "safe-provider-id"}

    def iter_lines(self, **kwargs):
        yield b": keep-alive"
        yield b""
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield ("data: " + json.dumps(event)).encode()
            yield b""

    def close(self):
        self.closed = True


def send(monkeypatch, response, provider="gemini", model="gemini-3.8-flash", kind="text"):
    calls = []
    monkeypatch.setattr(providers.requests, "post", lambda *args, **kw: calls.append((args, kw)) or response)
    result = providers.generate(provider, "owner-private-key", model, [{"text": "Private input"}], kind, 8000)
    return result, calls


@pytest.mark.parametrize("provider,model", [("gemini", "gemini-3.8-flash"), ("atlas", "google/gemini-3.8-flash")])
def test_native_stream_assembles_answer_and_counts_cumulative_usage_once(monkeypatch, provider, model):
    thinking = {"candidates": [{"content": {"parts": [{"text": "Private thinking", "thought": True}]}}],
                "usageMetadata": {"promptTokenCount": 12, "thoughtsTokenCount": 9}}
    response = Response(thinking, chunk("First ", promptTokenCount=12, candidatesTokenCount=3),
                        chunk("second.", "STOP", promptTokenCount=12, candidatesTokenCount=5, thoughtsTokenCount=9))
    result, calls = send(monkeypatch, response, provider, model, "research")
    assert result == ("First second.", 12, 14) and response.closed
    assert len(calls) == 1
    args, options = calls[0]
    assert args[0].endswith(":streamGenerateContent?alt=sse") and options["stream"] is True
    assert options["timeout"] == (15, 180) and options["allow_redirects"] is False
    assert options["json"]["tools"] == [{"google_search": {}}]
    assert options["json"]["generationConfig"] == {"maxOutputTokens": 8000, "thinkingConfig": {"thinkingLevel": "low"}}
    assert options["headers"] == ({"x-goog-api-key": "owner-private-key"} if provider == "gemini" else {"Authorization": "Bearer owner-private-key"})


def test_kie_low_effort_does_not_hide_reasoning_charges(monkeypatch):
    response = SimpleNamespace(status_code=200, headers={}, close=lambda: None, json=lambda: {
        "choices": [{"message": {"content": "Answer"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 7}}})
    result, calls = send(monkeypatch, response, "kie")
    assert result == ("Answer", 10, 20)
    assert calls[0][1]["json"]["reasoning_effort"] == "low"


@pytest.mark.parametrize("ending,code", [(None, "incomplete_stream"), ("MAX_TOKENS", "truncated"), ("SAFETY", "stopped")])
def test_incomplete_or_blocked_stream_never_returns_partial_script(monkeypatch, ending, code):
    response = Response(chunk("Private partial script", ending, promptTokenCount=4, candidatesTokenCount=6, thoughtsTokenCount=8))
    with pytest.raises(providers.ProviderFailure) as error:
        send(monkeypatch, response)
    details = error.value.diagnostics
    assert response.closed and details["code"] == code
    assert (details["input_tokens"], details["answer_tokens"], details["thinking_tokens"], details["output_tokens"]) == (4, 6, 8, 14)
    assert "Private partial" not in json.dumps(details) and "owner-private" not in json.dumps(details)


def test_stream_timeout_is_distinguished_and_keeps_reported_usage(monkeypatch):
    wrapped = requests.ConnectionError(ReadTimeoutError(None, "secret provider URL", "private timeout"))
    response = Response(chunk("Private partial", promptTokenCount=4, candidatesTokenCount=6), wrapped)
    with pytest.raises(providers.ProviderFailure) as error:
        send(monkeypatch, response)
    details = error.value.diagnostics
    assert response.closed and details["code"] == "read_timeout" and details["timeout_seconds"] == 180
    assert details["output_tokens"] == 6 and details["elapsed_seconds"] >= 0
    assert "secret provider URL" not in json.dumps(details) and "Private partial" not in json.dumps(details)


def test_no_first_response_timeout_is_one_submission_with_safe_diagnostics(monkeypatch):
    calls = []
    def fail(*a, **k):
        calls.append(k)
        raise requests.ReadTimeout("private key and provider URL")
    monkeypatch.setattr(providers.requests, "post", fail)
    with pytest.raises(providers.ProviderFailure) as error:
        providers.generate("gemini", "private-key", "gemini-3.8-flash", [{"text": "private prompt"}], "research", 8000)
    assert len(calls) == 1 and error.value.diagnostics["code"] == "read_timeout"
    assert error.value.diagnostics["usage_reported"] is False
    assert "private" not in json.dumps(error.value.diagnostics)


def test_continuously_active_stream_also_has_a_time_limit(monkeypatch):
    times = iter([0, 0, 0, 181, 181])
    monkeypatch.setattr(providers.time, "monotonic", lambda: next(times))
    response = Response(chunk("partial"), chunk("remainder", "STOP"))
    with pytest.raises(providers.ProviderFailure) as error:
        send(monkeypatch, response)
    assert response.closed and error.value.diagnostics["code"] == "generation_timeout"


def test_usage_only_final_event_is_not_counted_twice(monkeypatch):
    response = Response(chunk("Answer", "STOP", promptTokenCount=5, candidatesTokenCount=7),
                        {"usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 7, "thoughtsTokenCount": 3}})
    assert send(monkeypatch, response)[0] == ("Answer", 5, 10)


@pytest.mark.parametrize("model", ["gemini-2.5-flash", "gemini-3.1-flash-lite-image"])
def test_older_or_image_model_does_not_receive_unsupported_settings(monkeypatch, model):
    response = Response(chunk("Answer", "STOP"))
    result, calls = send(monkeypatch, response, model=model)
    assert result[0] == "Answer"
    assert calls[0][1]["json"]["generationConfig"] == {"maxOutputTokens": 8000}


def test_unreadable_stream_and_upstream_http_close_connection(monkeypatch):
    for response, code in [(Response({"error": {"message": "private provider error"}}), "unreadable"),
                           (Response(status=503), "upstream_http")]:
        with pytest.raises(providers.ProviderFailure) as error:
            send(monkeypatch, response)
        assert response.closed and error.value.diagnostics["code"] == code
        assert "private provider error" not in json.dumps(error.value.diagnostics)
