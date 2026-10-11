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


class ProviderFailure(HTTPException):
    """Safe diagnostics only: never retain provider bodies, prompts or keys."""
    def __init__(self, message, code, **metadata):
        super().__init__(502, message)
        self.diagnostics = {"code": code, "message": message, **metadata}


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


def check_key(provider, key, model):
    label = PROVIDERS[provider]["label"]
    if provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + model
        headers = {"x-goog-api-key": key}
    else:
        url = ("https://api.kie.ai/api/v1/chat/credit" if provider == "kie"
               else "https://api.atlascloud.ai/v1/models")
        headers = {"Authorization": "Bearer " + key}
    try:
        res = requests.get(url, headers=headers, timeout=(10, 20), allow_redirects=False)
        if res.status_code != 200:
            raise HTTPException(400, f"{label} could not verify this key (HTTP {res.status_code}). Your existing connection was kept.")
        data = res.json()
        if provider == "gemini" and "generateContent" not in data.get("supportedGenerationMethods", []):
            raise HTTPException(400, "Choose a Gemini model that can generate text and understand images.")
        if provider == "kie" and data.get("code") != 200:
            raise HTTPException(400, "Kie.ai did not accept this key. Check its API access and balance.")
        if provider == "atlas" and model not in {m.get("id") for m in data.get("data", [])}:
            raise HTTPException(400, "This model is not available on your Atlas Cloud account.")
    except requests.RequestException:
        raise HTTPException(503, f"{label} could not be reached. Your existing connection was kept.")
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(502, f"{label} returned an unreadable account check.")


def generate(provider, key, model, parts, kind, max_tokens):
    label = PROVIDERS[provider]["label"]
    if provider == "kie":
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
                            allow_redirects=False, stream=provider != "kie")
        if res.status_code != 200:
            raise ProviderFailure(f"{label} could not complete this request (HTTP {res.status_code}). No other account was used.",
                                  "upstream_http", upstream_http=res.status_code)
        headers = getattr(res, "headers", {})
        provider_id = headers.get("x-request-id", headers.get("request-id", ""))
        if isinstance(provider_id, str) and re.fullmatch(r"[a-zA-Z0-9_\-]{1,100}", provider_id):
            usage["provider_request_id"] = provider_id
        if provider == "kie":
            data = res.json()
            usage.update(_usage(data, provider))
            candidate = (data.get("choices") or [{}])[0]
            reason = candidate.get("finish_reason")
            text = candidate.get("message", {}).get("content", "")
        else:
            text, reason = _stream_result(res, started, usage)
        if reason not in (None, "stop" if provider == "kie" else "STOP"):
            safe_reason = reason if reason in {"MAX_TOKENS", "length", "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "content_filter"} else "OTHER"
            raise ProviderFailure(f"{label} stopped before completing this answer. Open Local studio in CR to review this request.",
                                  "truncated" if reason in ("MAX_TOKENS", "length") else "stopped", finish_reason=safe_reason, **usage)
        if not isinstance(text, str) or not text.strip():
            raise ProviderFailure("The model returned no usable answer. Open Local studio in CR to review this request.", "empty_answer", **usage)
        return text, usage["input_tokens"], usage["output_tokens"]
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
