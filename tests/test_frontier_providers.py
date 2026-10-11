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


def claude_events(text="Finished answer", reason="end_turn", complete=True, cached=True):
    usage = {"input_tokens": 12, "output_tokens": 1}
    if cached:
        usage.update(cache_read_input_tokens=3, cache_creation_input_tokens=4)
    events = [
        {"type": "message_start", "message": {"type": "message", "role": "assistant", "usage": usage}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Private reasoning"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": reason}, "usage": {"output_tokens": 20}},
        {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 20}},
    ]
    if complete:
        events.append({"type": "message_stop"})
    return events


CLAUDE_ROUTES = [
    ("anthropic", "claude-sonnet-5-5", "https://api.anthropic.com/v1/messages"),
    ("anthropic", "claude-opus-5-5", "https://api.anthropic.com/v1/messages"),
    ("kie", "claude-sonnet-5-5", "https://api.kie.ai/claude/v1/messages"),
    ("kie", "claude-opus-5-5", "https://api.kie.ai/claude/v1/messages"),
    ("atlas", "anthropic/claude-sonnet-5.5-ccmax", "https://api.atlascloud.ai/v1/messages"),
    ("atlas", "anthropic/claude-opus-5.5", "https://api.atlascloud.ai/v1/messages"),
]


@pytest.mark.parametrize("provider,model,url", CLAUDE_ROUTES)
def test_verified_claude_models_use_native_messages_vision_and_complete_stream(monkeypatch, provider, model, url):
    response = Response(*claude_events())
    calls = []
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: calls.append((a, k)) or response)
    parts = [{"text": "Original private stage prompt"}, {"inlineData": {"mimeType": "image/png", "data": "original-png"}}]
    result = providers.generate(provider, "owner-private-key", model, parts, "vision", 50000)
    assert result == ("Finished answer", 19, 20) and response.closed
    assert len(calls) == 1
    args, options = calls[0]
    assert args[0] == url and options["stream"] is True and options["allow_redirects"] is False
    assert options["timeout"] == (15, 180)
    payload = options["json"]
    assert payload == {"model": model, "max_tokens": 32768, "stream": True, "messages": [{"role": "user", "content": [
        {"type": "text", "text": parts[0]["text"]},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "original-png"}},
    ]}]}
    assert options["headers"]["anthropic-version"] == "2023-06-01"
    assert options["headers"].get("x-api-key") == ("owner-private-key" if provider == "anthropic" else None)
    assert options["headers"].get("Authorization") == (None if provider == "anthropic" else "Bearer owner-private-key")
    assert "Private reasoning" not in result[0]


@pytest.mark.parametrize("provider,model,url", CLAUDE_ROUTES)
def test_claude_catalog_preserves_gateway_specific_ids_and_research_support(provider, model, url):
    entry = providers.brain_model(provider, model)
    assert entry["protocol"] == "anthropic" and entry["vision"] is True
    assert providers.supports_research(provider, model) is (provider == "anthropic")
    assert providers.stream_window(model) == 600
    assert providers.PROVIDERS[provider]["default_brain"] in {m["id"] for m in providers.PROVIDERS[provider]["brain_models"]}
    assert providers.PROVIDERS[provider]["default_visual_brain"] in {m["id"] for m in providers.PROVIDERS[provider]["brain_models"]}


def test_anthropic_research_uses_native_server_search_and_keeps_stage_prompt(monkeypatch):
    response = Response(*claude_events())
    result, calls = send(monkeypatch, response, "anthropic", "claude-sonnet-5-5", "research")
    assert result == ("Finished answer", 19, 20)
    payload = calls[0][1]["json"]
    assert payload["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    assert payload["messages"][0]["content"][0]["text"] == "Private input"
    assert "tool_choice" not in payload and "thinking" not in payload and "output_config" not in payload


@pytest.mark.parametrize("provider,model", [("kie", "claude-opus-5-5"), ("atlas", "anthropic/claude-opus-5.5")])
def test_gateway_research_is_rejected_before_any_paid_request(monkeypatch, provider, model):
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: pytest.fail("No paid submission is allowed"))
    with pytest.raises(providers.ProviderFailure) as error:
        providers.generate(provider, "private-key", model, [{"text": "private prompt"}], "research", 8000)
    assert error.value.diagnostics["code"] == "research_unsupported"
    assert error.value.diagnostics["usage_reported"] is False
    assert "private" not in json.dumps(error.value.diagnostics)


@pytest.mark.parametrize("reason,complete,code", [
    ("end_turn", False, "incomplete_stream"),
    (None, True, "incomplete_stream"),
    ("max_tokens", True, "truncated"),
    ("refusal", True, "stopped"),
    ("tool_use", True, "stopped"),
    ("pause_turn", True, "stopped"),
])
def test_claude_incomplete_tool_or_refused_answers_never_return_partial_text(monkeypatch, reason, complete, code):
    response = Response(*claude_events("Private partial answer", reason, complete))
    with pytest.raises(providers.ProviderFailure) as error:
        send(monkeypatch, response, "anthropic", "claude-opus-5-5")
    details = error.value.diagnostics
    assert response.closed and details["code"] == code
    assert details["input_tokens"] == 19 and details["output_tokens"] == 20
    assert details["cache_read_input_tokens"] == 3 and details["cache_creation_input_tokens"] == 4
    assert "Private partial" not in json.dumps(details) and "Private reasoning" not in json.dumps(details)


def test_claude_stream_error_keeps_reported_usage_without_provider_private_body(monkeypatch):
    events = claude_events()
    response = Response(*events[:7], {"type": "error", "error": {"type": "overloaded_error", "message": "Private provider failure"}})
    with pytest.raises(providers.ProviderFailure) as error:
        send(monkeypatch, response, "kie", "claude-sonnet-5-5")
    details = error.value.diagnostics
    assert response.closed and details["code"] == "upstream_stream_error" and details["upstream_error_type"] == "overloaded_error"
    assert details["input_tokens"] == 19 and details["output_tokens"] == 1
    assert "Private provider failure" not in json.dumps(details) and "Private reasoning" not in json.dumps(details)


def test_claude_search_failure_is_not_a_successful_fact_check(monkeypatch):
    events = claude_events()
    events[7:7] = [
        {"type": "content_block_start", "index": 2, "content_block": {"type": "web_search_tool_result", "content": {
            "type": "web_search_tool_result_error", "error_code": "too_many_requests"}}},
        {"type": "content_block_stop", "index": 2},
    ]
    response = Response(*events)
    with pytest.raises(providers.ProviderFailure) as error:
        send(monkeypatch, response, "anthropic", "claude-sonnet-5-5", "research")
    assert response.closed and error.value.diagnostics["code"] == "research_failed"
    assert error.value.diagnostics["output_tokens"] == 20


def test_claude_broken_block_sequence_is_not_accepted_as_a_finished_answer(monkeypatch):
    events = claude_events()
    del events[6]  # a text block remained open when message_stop arrived
    response = Response(*events)
    with pytest.raises(providers.ProviderFailure) as error:
        send(monkeypatch, response, "anthropic", "claude-sonnet-5-5")
    assert response.closed and error.value.diagnostics["code"] == "unreadable"


def test_claude_total_stream_limit_is_distinct_from_gemini_limit(monkeypatch):
    times = iter([0, 599, 601, 601])
    monkeypatch.setattr(providers.time, "monotonic", lambda: next(times))
    response = Response(*claude_events())
    with pytest.raises(providers.ProviderFailure) as error:
        send(monkeypatch, response, "anthropic", "claude-opus-5-5")
    assert response.closed and error.value.diagnostics["code"] == "generation_timeout"
    assert error.value.diagnostics["timeout_seconds"] == 600
    assert providers.stream_window("gemini-3.8-flash") == 180


@pytest.mark.parametrize("provider,model,url,body", [
    ("anthropic", "claude-sonnet-5-5", "https://api.anthropic.com/v1/models/claude-sonnet-5-5", {"id": "claude-sonnet-5-5"}),
    ("kie", "claude-opus-5-5", "https://api.kie.ai/anthropic/v1/models", {"data": [{"id": "claude-opus-5-5"}]}),
    ("atlas", "anthropic/claude-opus-5.5", "https://api.atlascloud.ai/v1/models", {"data": [{"id": "anthropic/claude-opus-5.5"}]}),
])
def test_claude_account_checks_only_read_available_model_metadata(monkeypatch, provider, model, url, body):
    calls, closed = [], []
    response = SimpleNamespace(status_code=200, json=lambda: body, close=lambda: closed.append(True))
    monkeypatch.setattr(providers.requests, "get", lambda *a, **k: calls.append((a, k)) or response)
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: pytest.fail("Key check cannot generate anything"))
    providers.check_key(provider, "private-owner-key", model)
    assert len(calls) == 1 and calls[0][0][0] == url and closed == [True]
    assert calls[0][1]["timeout"] == (10, 20) and calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["headers"].get("x-api-key") == ("private-owner-key" if provider == "anthropic" else None)


def test_claude_key_check_rejects_unavailable_model_without_using_a_different_one(monkeypatch):
    response = SimpleNamespace(status_code=200, json=lambda: {"data": [{"id": "claude-sonnet-5-5"}]}, close=lambda: None)
    monkeypatch.setattr(providers.requests, "get", lambda *a, **k: response)
    with pytest.raises(providers.HTTPException) as error:
        providers.check_key("kie", "private-owner-key", "claude-opus-5-5")
    assert error.value.status_code == 400 and "not available" in error.value.detail
