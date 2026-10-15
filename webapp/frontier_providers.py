"""Owner-supplied model accounts. Never read platform generation credentials."""
import json
import re
import time
from pathlib import Path

import requests
from fastapi import HTTPException
from urllib3.exceptions import ReadTimeoutError

PROVIDERS = json.loads((Path(__file__).parent / "frontier/providers.json").read_text())
READ_TIMEOUT = 180
STREAM_WINDOW = 180
CLAUDE_STREAM_WINDOW = 600


class ProviderFailure(HTTPException):
    """Safe diagnostics only: never retain provider bodies, prompts or keys."""
    def __init__(self, message, code, **metadata):
        super().__init__(502, message)
        self.diagnostics = {"code": code, "message": message, **metadata}


class ProviderBusy(ProviderFailure):
    """The provider turned the request away before doing any work (rate limited or overloaded).

    Nothing was generated or charged, so the same request may simply be sent again later.
    """
    def __init__(self, label, status, retry_after):
        super().__init__(f"{label} is busy right now (HTTP {status}). Studio will try again shortly.",
                         "upstream_busy", upstream_http=status)
        self.retry_after = retry_after


def _retry_after(res, default=20):
    try:
        seconds = int(float((getattr(res, "headers", {}) or {}).get("retry-after", default)))
    except (TypeError, ValueError):
        seconds = default
    return max(5, min(120, seconds))


def brain_model(provider, model):
    """The verified model contract, shared with setup and request validation."""
    return next((m for m in PROVIDERS.get(provider, {}).get("brain_models", []) if m["id"] == model), None)


def supports_research(provider, model):
    info = brain_model(provider, model)
    return bool(info and info.get("research"))


def is_claude(model):
    return model.startswith("claude-") or model.startswith("anthropic/claude-")


def stream_window(model):
    return CLAUDE_STREAM_WINDOW if is_claude(model) else STREAM_WINDOW


def _usage(data, provider):
    usage = data.get("usage" if provider == "kie" else "usageMetadata", {}) or {}
    incoming = int(usage.get("prompt_tokens" if provider == "kie" else "promptTokenCount", 0) or 0)
    outgoing = int(usage.get("completion_tokens" if provider == "kie" else "candidatesTokenCount", 0) or 0)
    if provider != "kie":
        thoughts = max(0, int(usage.get("thoughtsTokenCount", 0) or 0))
    else:
        thoughts = max(0, int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0))
    answer = max(0, outgoing if provider != "kie" else outgoing - thoughts)
    return {"input_tokens": max(0, incoming), "output_tokens": answer + thoughts,
            "answer_tokens": answer, "thinking_tokens": thoughts, "usage_reported": bool(usage)}


def thinking_config(model):
    """Match Frontier's limited-thinking helpers without unsupported off settings.

    Gemini 3.8 supports low/medium/high, not minimal or fully disabled thinking.
    Leave older/custom models alone: their control parameters differ.
    """
    name = model.removeprefix("google/")
    if name.startswith("gemini-3") and "image" not in name:
        return {"thinkingConfig": {"thinkingLevel": "low"}}
    return {}


def _stream_result(response, started, usage):
    """Consume one native Gemini SSE request; never return a partial answer.

    Usage in these chunks is cumulative, not a bill for each chunk. Ignore
    keep-alives and thought text. Retain reported usage if the stream fails.
    """
    text = []
    reason = None
    size = 0
    event = []
    def consume(lines):
        nonlocal reason, size
        data = json.loads("\n".join(lines))
        if not isinstance(data, dict) or "error" in data:
            raise ValueError("Invalid provider event")
        reported = _usage(data, "gemini")
        for field in ("input_tokens", "answer_tokens", "thinking_tokens"):
            usage[field] = max(usage.get(field, 0), reported[field])
        usage["output_tokens"] = usage.get("answer_tokens", 0) + usage.get("thinking_tokens", 0)
        usage["usage_reported"] = usage.get("usage_reported", False) or reported["usage_reported"]
        candidate = (data.get("candidates") or [{}])[0]
        if candidate.get("finishReason"):
            reason = candidate["finishReason"]
        for part in candidate.get("content", {}).get("parts", []):
            if part.get("thought"):
                continue
            fragment = part.get("text", "")
            if not isinstance(fragment, str):
                raise ValueError("Invalid provider text")
            size += len(fragment)
            if size > 1_000_000:
                raise ValueError("Provider answer too large")
            text.append(fragment)
    for raw in response.iter_lines(chunk_size=1024):
        if time.monotonic() - started >= STREAM_WINDOW:
            raise ProviderFailure("The model took too long to finish. Open Local studio in CR to review this request.",
                                  "generation_timeout", timeout_seconds=STREAM_WINDOW, **usage)
        if len(raw) > 2_000_000:
            raise ValueError("Provider event too large")
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line:
            if event:
                consume(event)
                event = []
        elif line.startswith("data:"):
            event.append(line[5:].lstrip())
            if sum(map(len, event)) > 2_000_000:
                raise ValueError("Provider event too large")
    if event:
        consume(event)
    if not reason:
        raise ProviderFailure("The provider stopped sending an answer before it was complete. Open Local studio in CR to review this request.",
                              "incomplete_stream", **usage)
    return "".join(text), reason


def _claude_stream_result(response, started, usage):
    """Read native Messages SSE without returning a partial answer or reasoning.

    Message usage is cumulative. Claude's output token total already includes
    thinking; the API does not promise a separate thinking-token count. Cache
    reads and writes are input tokens too, and remain separate in diagnostics.
    No client tool execution, paid continuation, or model fallback happens here.
    """
    text, blocks, open_blocks = [], {}, set()
    started_message = stopped_message = False
    reason, tool_error = None, False
    size, event, event_size = 0, [], 0

    def record_usage(value):
        if not isinstance(value, dict):
            raise ValueError("Invalid Claude usage")
        for incoming, field in (("input_tokens", "uncached_input_tokens"),
                                ("cache_read_input_tokens", "cache_read_input_tokens"),
                                ("cache_creation_input_tokens", "cache_creation_input_tokens"),
                                ("output_tokens", "output_tokens")):
            if incoming in value:
                number = value[incoming]
                if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                    raise ValueError("Invalid Claude token count")
                usage[field] = max(usage.get(field, 0), number)
                usage["usage_reported"] = True
        usage["input_tokens"] = sum(usage.get(field, 0) for field in
                                    ("uncached_input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        server_tools = value.get("server_tool_use") or {}
        searches = server_tools.get("web_search_requests")
        if isinstance(searches, int) and not isinstance(searches, bool) and searches >= 0:
            usage["web_search_requests"] = max(usage.get("web_search_requests", 0), searches)

    def append_text(value):
        nonlocal size
        if not isinstance(value, str):
            raise ValueError("Invalid Claude text")
        size += len(value)
        if size > 1_000_000:
            raise ValueError("Claude answer too large")
        text.append(value)

    def consume(lines):
        nonlocal started_message, stopped_message, reason, tool_error
        data = json.loads("\n".join(lines))
        if not isinstance(data, dict):
            raise ValueError("Invalid Claude event")
        kind = data.get("type")
        if kind == "error":
            error_type = (data.get("error") or {}).get("type")
            safe_type = error_type if error_type in {"invalid_request_error", "authentication_error", "permission_error",
                                                     "not_found_error", "rate_limit_error", "overloaded_error", "api_error"} else "other"
            raise ProviderFailure("The model stopped while sending this answer. Open Local studio in CR to review this request.",
                                  "upstream_stream_error", upstream_error_type=safe_type, **usage)
        if kind == "ping":
            return
        if stopped_message:
            raise ValueError("Claude event after completion")
        if kind == "message_start":
            if started_message:
                raise ValueError("Duplicate Claude message")
            message = data.get("message") or {}
            if message.get("type") != "message" or message.get("role") != "assistant":
                raise ValueError("Invalid Claude message")
            started_message = True
            record_usage(message.get("usage") or {})
        elif kind == "content_block_start":
            index, block = data.get("index"), data.get("content_block") or {}
            if not started_message or not isinstance(index, int) or index < 0 or index in blocks:
                raise ValueError("Invalid Claude content block")
            blocks[index] = block.get("type")
            open_blocks.add(index)
            if block.get("type") == "text":
                append_text(block.get("text", ""))
            elif block.get("type") == "web_search_tool_result":
                content = block.get("content")
                if isinstance(content, dict) and content.get("type") == "web_search_tool_result_error":
                    tool_error = True
        elif kind == "content_block_delta":
            index, delta = data.get("index"), data.get("delta") or {}
            if index not in open_blocks:
                raise ValueError("Claude delta without an open block")
            if delta.get("type") == "text_delta":
                if blocks[index] != "text":
                    raise ValueError("Claude text in a non-text block")
                append_text(delta.get("text", ""))
            # Thinking, signatures, tool JSON, and citation deltas stay private.
        elif kind == "content_block_stop":
            index = data.get("index")
            if index not in open_blocks:
                raise ValueError("Claude block did not start")
            open_blocks.remove(index)
        elif kind == "message_delta":
            if not started_message:
                raise ValueError("Claude message did not start")
            delta = data.get("delta") or {}
            if delta.get("stop_reason"):
                reason = delta["stop_reason"]
            record_usage(data.get("usage") or {})
        elif kind == "message_stop":
            if not started_message or open_blocks:
                raise ValueError("Claude message ended with incomplete blocks")
            stopped_message = True
        # Ignore future event types, as the native API versioning policy requires.

    for raw in response.iter_lines(chunk_size=1024):
        if time.monotonic() - started >= CLAUDE_STREAM_WINDOW:
            raise ProviderFailure("The model took too long to finish. Open Local studio in CR to review this request.",
                                  "generation_timeout", timeout_seconds=CLAUDE_STREAM_WINDOW, **usage)
        if len(raw) > 2_000_000:
            raise ValueError("Claude event too large")
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line:
            if event:
                consume(event)
                event, event_size = [], 0
        elif line.startswith("data:"):
            fragment = line[5:].lstrip()
            event.append(fragment)
            event_size += len(fragment)
            if event_size > 2_000_000:
                raise ValueError("Claude event too large")
    if event:
        consume(event)
    if not stopped_message or not reason:
        raise ProviderFailure("The provider stopped sending an answer before it was complete. Open Local studio in CR to review this request.",
                              "incomplete_stream", **usage)
    if tool_error:
        raise ProviderFailure("The web search could not finish. Open Local studio in CR to review this request.",
                              "research_failed", **usage)
    return "".join(text), reason


def check_key(provider, key, model):
    label = PROVIDERS[provider]["label"]
    if provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + model
        headers = {"x-goog-api-key": key}
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/models/" + model
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        url = ("https://api.kie.ai/anthropic/v1/models" if provider == "kie" and is_claude(model) else
               "https://api.kie.ai/api/v1/chat/credit" if provider == "kie"
               else "https://api.atlascloud.ai/v1/models")
        headers = {"Authorization": "Bearer " + key}
    res = None
    try:
        res = requests.get(url, headers=headers, timeout=(10, 20), allow_redirects=False)
        if res.status_code != 200:
            raise HTTPException(400, f"{label} could not verify this key (HTTP {res.status_code}). Your existing connection was kept.")
        data = res.json()
        if provider == "gemini" and "generateContent" not in data.get("supportedGenerationMethods", []):
            raise HTTPException(400, "Choose a Gemini model that can generate text and understand images.")
        if provider == "anthropic" and data.get("id") != model:
            raise HTTPException(400, "This Claude model is not available on your Anthropic account.")
        if provider == "kie" and not is_claude(model) and data.get("code") != 200:
            raise HTTPException(400, "Kie.ai did not accept this key. Check its API access and balance.")
        if (provider == "atlas" or provider == "kie" and is_claude(model)) and model not in {m.get("id") for m in data.get("data", [])}:
            raise HTTPException(400, f"This model is not available on your {label} account.")
    except requests.RequestException:
        raise HTTPException(503, f"{label} could not be reached. Your existing connection was kept.")
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(502, f"{label} returned an unreadable account check.")
    finally:
        if res is not None and callable(getattr(res, "close", None)):
            res.close()


def generate(provider, key, model, parts, kind, max_tokens):
    label = PROVIDERS[provider]["label"]
    claude = is_claude(model)
    if claude:
        info = brain_model(provider, model)
        if not info or info.get("protocol") != "anthropic":
            raise ProviderFailure("Choose a supported Claude model in Local studio before starting this request.", "unsupported_model")
        if kind == "research" and not supports_research(provider, model):
            raise ProviderFailure(f"{label}'s Claude route does not provide verified web search. Connect Anthropic or select a model with verified web search for Frontier's research steps.",
                                  "research_unsupported", usage_reported=False)
        url = {"anthropic": "https://api.anthropic.com/v1/messages",
               "kie": "https://api.kie.ai/claude/v1/messages",
               "atlas": "https://api.atlascloud.ai/v1/messages"}[provider]
        headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"} if provider == "anthropic"
                   else {"Authorization": "Bearer " + key, "anthropic-version": "2023-06-01"})
        content = [{"type": "text", "text": p["text"]} if "text" in p else
                   {"type": "image", "source": {"type": "base64", "media_type": p["inlineData"]["mimeType"],
                                                   "data": p["inlineData"]["data"]}} for p in parts]
        payload = {"model": model, "messages": [{"role": "user", "content": content}],
                   "max_tokens": min(32768, max(128, max_tokens)), "stream": True}
        # Keep Claude's own reasoning defaults. Opus 5.5 always thinks and
        # rejects disabled thinking; Gemini-only low settings do not apply.
        if kind == "research":
            payload["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    elif provider == "kie":
        # Kie's Gemini models have model-specific OpenAI-compatible paths.
        slug = model.replace(".", "-")
        url = f"https://api.kie.ai/{slug}-openai/v1/chat/completions"
        content = [{"type": "text", "text": p["text"]} if "text" in p else
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + p["inlineData"]["data"]}}
                   for p in parts]
        payload = {"messages": [{"role": "user", "content": content}], "stream": False,
                   "max_tokens": max_tokens, "include_thoughts": False, "reasoning_effort": "low"}
        if kind == "research":
            payload["tools"] = [{"type": "function", "function": {"name": "googleSearch"}}]
        headers = {"Authorization": "Bearer " + key}
    else:
        base = ("https://api.atlascloud.ai" if provider == "atlas"
                else "https://generativelanguage.googleapis.com")
        url = base + "/v1beta/models/" + model + ":streamGenerateContent?alt=sse"
        headers = {"Authorization": "Bearer " + key} if provider == "atlas" else {"x-goog-api-key": key}
        payload = {"contents": [{"role": "user", "parts": parts}],
                   "generationConfig": {"maxOutputTokens": max_tokens, **thinking_config(model)}}
        if kind == "research":
            payload["tools"] = [{"google_search": {}}]
    started = time.monotonic()
    res = None
    usage = {"usage_reported": False}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=(15, READ_TIMEOUT),
                            allow_redirects=False, stream=claude or provider != "kie")
        if res.status_code in (429, 503, 529):
            raise ProviderBusy(label, res.status_code, _retry_after(res))
        if res.status_code != 200:
            raise ProviderFailure(f"{label} could not complete this request (HTTP {res.status_code}). No other account was used.",
                                  "upstream_http", upstream_http=res.status_code)
        headers = getattr(res, "headers", {})
        provider_id = headers.get("x-request-id", headers.get("request-id", ""))
        if isinstance(provider_id, str) and re.fullmatch(r"[a-zA-Z0-9_\-]{1,100}", provider_id):
            usage["provider_request_id"] = provider_id
        if claude:
            text, reason = _claude_stream_result(res, started, usage)
        elif provider == "kie":
            data = res.json()
            usage.update(_usage(data, provider))
            candidate = (data.get("choices") or [{}])[0]
            reason = candidate.get("finish_reason")
            text = candidate.get("message", {}).get("content", "")
        else:
            text, reason = _stream_result(res, started, usage)
        good_reasons = ("end_turn", "stop_sequence") if claude else (None, "stop" if provider == "kie" else "STOP")
        if reason not in good_reasons:
            safe_reason = reason if reason in {"MAX_TOKENS", "length", "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "content_filter",
                                             "max_tokens", "refusal", "tool_use", "pause_turn", "model_context_window_exceeded"} else "OTHER"
            raise ProviderFailure(f"{label} stopped before completing this answer. Open Local studio in CR to review this request.",
                                  "truncated" if reason in ("MAX_TOKENS", "length", "max_tokens") else "stopped", finish_reason=safe_reason, **usage)
        if not isinstance(text, str) or not text.strip():
            raise ProviderFailure("The model returned no usable answer. Open Local studio in CR to review this request.", "empty_answer", **usage)
        return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    except requests.ReadTimeout:
        raise ProviderFailure(f"{label} did not respond within {READ_TIMEOUT} seconds. Open Local studio in CR to review this request.",
                              "read_timeout", timeout_seconds=READ_TIMEOUT,
                              elapsed_seconds=round(time.monotonic() - started, 2), **usage) from None
    except requests.ConnectionError as error:
        # requests wraps a timeout during iter_lines in ConnectionError.
        timed_out = any(isinstance(arg, ReadTimeoutError) for arg in error.args)
        if timed_out:
            raise ProviderFailure(f"{label} stopped responding for {READ_TIMEOUT} seconds. Open Local studio in CR to review this request.",
                                  "read_timeout", timeout_seconds=READ_TIMEOUT,
                                  elapsed_seconds=round(time.monotonic() - started, 2), **usage) from None
        raise ProviderFailure("The provider connection was interrupted. Open Local studio in CR to review this request.",
                              "network_interrupted", elapsed_seconds=round(time.monotonic() - started, 2), **usage) from None
    except requests.RequestException:
        raise ProviderFailure("The provider connection was interrupted. Open Local studio in CR to review this request.",
                              "network_interrupted", elapsed_seconds=round(time.monotonic() - started, 2), **usage) from None
    except (ValueError, TypeError, AttributeError):
        raise ProviderFailure(f"{label} returned an unreadable response. Open Local studio in CR to review this request.",
                              "unreadable", elapsed_seconds=round(time.monotonic() - started, 2), **usage) from None
    except ProviderFailure as error:
        error.diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 2)
        raise
    finally:
        if res is not None:
            res.close()
