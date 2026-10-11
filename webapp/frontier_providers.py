"""Owner-supplied model accounts. Never read platform generation credentials."""
import json
import re
from pathlib import Path

import requests
from fastapi import HTTPException

PROVIDERS = json.loads((Path(__file__).parent / "frontier/providers.json").read_text())


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
        outgoing += int(usage.get("thoughtsTokenCount", 0) or 0)
    return {"input_tokens": max(0, incoming), "output_tokens": max(0, outgoing), "usage_reported": bool(usage)}


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
                   "max_tokens": max_tokens, "include_thoughts": False}
        if kind == "research":
            payload["tools"] = [{"type": "function", "function": {"name": "googleSearch"}}]
        headers = {"Authorization": "Bearer " + key}
    else:
        base = ("https://api.atlascloud.ai" if provider == "atlas"
                else "https://generativelanguage.googleapis.com")
        url = base + "/v1beta/models/" + model + ":generateContent"
        headers = {"Authorization": "Bearer " + key} if provider == "atlas" else {"x-goog-api-key": key}
        payload = {"contents": [{"role": "user", "parts": parts}],
                   "generationConfig": {"maxOutputTokens": max_tokens}}
        if kind == "research":
            payload["tools"] = [{"google_search": {}}]
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=(15, 180), allow_redirects=False)
        if res.status_code != 200:
            raise ProviderFailure(f"{label} could not complete this request (HTTP {res.status_code}). No other account was used.",
                                  "upstream_http", upstream_http=res.status_code)
        data = res.json()
        usage = _usage(data, provider)
        headers = getattr(res, "headers", {})
        provider_id = headers.get("x-request-id", headers.get("request-id", ""))
        if isinstance(provider_id, str) and re.fullmatch(r"[a-zA-Z0-9_\-]{1,100}", provider_id):
            usage["provider_request_id"] = provider_id
        if provider == "kie":
            candidate = (data.get("choices") or [{}])[0]
            reason = candidate.get("finish_reason")
            text = candidate.get("message", {}).get("content", "")
        else:
            candidate = (data.get("candidates") or [{}])[0]
            reason = candidate.get("finishReason")
            text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
        if reason not in (None, "stop" if provider == "kie" else "STOP"):
            safe_reason = reason if reason in {"MAX_TOKENS", "length", "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "content_filter"} else "OTHER"
            raise ProviderFailure(f"{label} stopped before completing this answer. Open Local studio in CR to review this request.",
                                  "truncated" if reason in ("MAX_TOKENS", "length") else "stopped", finish_reason=safe_reason, **usage)
        if not isinstance(text, str) or not text.strip():
            raise ProviderFailure("The model returned no usable answer. Open Local studio in CR to review this request.", "empty_answer", **usage)
        return text, usage["input_tokens"], usage["output_tokens"]
    except requests.RequestException:
        raise ProviderFailure("The provider connection was interrupted. Open Local studio in CR to review this request.", "network_interrupted")
    except (ValueError, TypeError, AttributeError):
        raise ProviderFailure(f"{label} returned an unreadable response. Open Local studio in CR to review this request.", "unreadable")
