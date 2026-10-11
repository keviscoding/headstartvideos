"""Owner-supplied model accounts. Never read platform generation credentials."""
import json
from pathlib import Path

import requests
from fastapi import HTTPException

PROVIDERS = json.loads((Path(__file__).parent / "frontier/providers.json").read_text())


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
            raise HTTPException(502, f"{label} could not complete this request (HTTP {res.status_code}). No other account was used.")
        data = res.json()
        if provider == "kie":
            candidate = (data.get("choices") or [{}])[0]
            if candidate.get("finish_reason") not in (None, "stop"):
                raise HTTPException(502, f"{label} stopped before completing this answer. Review this project's usage before restarting it.")
            text = candidate.get("message", {}).get("content", "")
            usage = data.get("usage", {})
            incoming, outgoing = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        else:
            candidate = (data.get("candidates") or [{}])[0]
            if candidate.get("finishReason") not in (None, "STOP"):
                raise HTTPException(502, f"{label} stopped before completing this answer. Review this project's usage before restarting it.")
            text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
            usage = data.get("usageMetadata", {})
            incoming = int(usage.get("promptTokenCount", 0))
            outgoing = int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0))
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(502, "The model returned no usable answer. Check your project before retrying.")
        return text, incoming, outgoing
    except requests.RequestException:
        raise HTTPException(502, "The provider connection was interrupted. Check the recorded request before retrying.")
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(502, f"{label} returned an unreadable response.")
