"""
Atlas Cloud LLM + image helpers (OpenAI-compatible chat + generateImage).

We route former Google Gemini workloads through Atlas so a denied Google
project does not take down cooks. Prefer ATLASCLOUD_KEY; optional GEMINI_KEY
fallback only when Atlas is unset.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

import config

ATLAS_LLM_BASE = "https://api.atlascloud.ai/v1"
ATLAS_MEDIA_BASE = "https://api.atlascloud.ai/api/v1"

# Cheap text default — titles/scripts/segmenter (not gemini-3.5).
ATLAS_TEXT_MODEL = os.getenv(
    "ATLAS_TEXT_MODEL",
    "google/gemini-3.1-flash-lite",
)
# Explainer hook stills ONLY (~$0.028/pic). Never use for cinematic/body.
ATLAS_PREMIUM_IMAGE_MODEL = os.getenv(
    "ATLAS_PREMIUM_IMAGE_MODEL",
    "google/nano-banana-2-lite/text-to-image-developer",
)
ERNIE_IMAGE_MODEL = "baidu/ERNIE-Image-Turbo/text-to-image"
HQ_IMAGE_MODEL = getattr(
    config,
    "HQ_IMAGE_MODEL",
    "openai/gpt-image-2-developer/text-to-image",
)


def _atlas_key() -> str:
    return (getattr(config, "ATLASCLOUD_KEY", "") or "").strip()


def has_atlas() -> bool:
    return bool(_atlas_key())


def generate_text(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 8192,
    temperature: float | None = None,
    system: str | None = None,
) -> str:
    """
    Text completion via Atlas Cloud. Google GenAI only if ALLOW_GOOGLE_GEMINI=1
    (our Google project is access-denied — do not use it for cooks).
    """
    model = (model or getattr(config, "GEMINI_TEXT_MODEL", None) or "gemini-3.1-flash-lite").strip()
    if has_atlas():
        atlas_model = (
            getattr(config, "ATLAS_TEXT_MODEL", None)
            or ATLAS_TEXT_MODEL
        )
        # Explicit Atlas ids win; Google short names → configured cheap Atlas model
        if model.startswith("google/"):
            atlas_model = model
        return _atlas_chat(
            prompt,
            model=atlas_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )
    allow_google = (os.getenv("ALLOW_GOOGLE_GEMINI", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if allow_google:
        return _google_text(prompt, model=model, max_tokens=max_tokens, system=system)
    raise RuntimeError(
        "ATLASCLOUD_KEY required for text generation "
        "(Google Gemini is disabled; set ALLOW_GOOGLE_GEMINI=1 to override)"
    )


def _atlas_chat(
    prompt: str,
    *,
    model: str,
    max_tokens: int,
    temperature: float | None,
    system: str | None,
) -> str:
    key = _atlas_key()
    if not key:
        raise RuntimeError("ATLASCLOUD_KEY not configured")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: dict = {
        "model": model or ATLAS_TEXT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        body["temperature"] = temperature

    with httpx.Client(timeout=180) as client:
        resp = client.post(
            f"{ATLAS_LLM_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        ctype = (resp.headers.get("content-type") or "").lower()
        text_body = resp.text or ""
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Atlas LLM {resp.status_code}: {text_body[:400]}"
            )
        if "application/json" not in ctype or text_body.lstrip().startswith("<!"):
            raise RuntimeError(
                f"Atlas LLM returned non-JSON ({ctype or 'unknown'}): {text_body[:200]}"
            )
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(
                f"Atlas LLM JSON parse failed: {e}; body={text_body[:200]}"
            ) from e

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Atlas LLM empty response: {str(data)[:300]}")
    msg = choices[0].get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text:
        raise RuntimeError("Atlas LLM returned empty content")
    return text


def _google_text(
    prompt: str,
    *,
    model: str,
    max_tokens: int,
    system: str | None,
) -> str:
    from google import genai
    from google.genai import types

    key = (getattr(config, "GEMINI_KEY", "") or "").strip()
    if not key:
        raise RuntimeError(
            "Neither ATLASCLOUD_KEY nor GEMINI_KEY configured for text generation"
        )
    client = genai.Client(api_key=key)
    contents = prompt if not system else f"{system}\n\n{prompt}"
    resp = client.models.generate_content(
        model=model,
        contents=[{"role": "user", "parts": [{"text": contents}]}],
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        try:
            parts = resp.candidates[0].content.parts
            text = "".join(getattr(p, "text", "") or "" for p in parts).strip()
        except Exception:
            text = ""
    if not text:
        raise RuntimeError("Google Gemini returned empty text")
    return text


def generate_image_file(
    prompt: str,
    output_path: str,
    *,
    model: str | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "1k",
    timeout_sec: float = 90,
) -> bool:
    """
    Text-to-image via Atlas generateImage (Nano Banana / etc.).
    Returns True on success.
    """
    key = _atlas_key()
    if not key:
        return False

    model = model or ATLAS_PREMIUM_IMAGE_MODEL
    t0 = time.time()
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{ATLAS_MEDIA_BASE}/model/generateImage",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                    "enable_base64_output": False,
                    "enable_sync_mode": False,
                },
            )
            data = resp.json()
            pred_id = None
            if isinstance(data.get("data"), dict):
                pred_id = data["data"].get("id")
            pred_id = pred_id or data.get("id") or data.get("prediction_id")
            if not pred_id:
                print(f"[atlas] generateImage no id: {str(data)[:200]}")
                return False

            while time.time() - t0 < timeout_sec:
                time.sleep(1.5)
                poll = client.get(
                    f"{ATLAS_MEDIA_BASE}/model/prediction/{pred_id}",
                    headers={"Authorization": f"Bearer {key}"},
                )
                inner = poll.json().get("data", poll.json())
                status = str(inner.get("status", "")).lower()
                if status in ("succeeded", "completed", "done"):
                    outputs = inner.get("outputs") or inner.get("output") or []
                    if isinstance(outputs, str):
                        img_url = outputs
                    elif isinstance(outputs, list) and outputs:
                        img_url = outputs[0]
                    else:
                        return False
                    img = client.get(img_url, follow_redirects=True, timeout=60)
                    img.raise_for_status()
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img.content)
                    print(
                        f"[atlas] image ok model={model} "
                        f"{time.time() - t0:.1f}s → {Path(output_path).name}"
                    )
                    return True
                if status in ("failed", "error", "cancelled"):
                    print(f"[atlas] generateImage failed: {inner}")
                    return False
    except Exception as e:
        print(f"[atlas] generateImage error: {e}")
        return False
    print(f"[atlas] generateImage timeout after {timeout_sec}s")
    return False


def generate_hq_image_file(
    prompt: str,
    output_path: str,
    *,
    timeout_sec: float = 120,
) -> bool:
    """
    GPT Image 2 Developer (Atlas) — HQ explainer / cinematic stills.

    Cost-locked to the cheapest settings Atlas allows for 16:9:
      quality=low, size=1024x576 (exact 16:9 at 1K floor — not 2K 2048x1152, not 4K).
    """
    key = _atlas_key()
    if not key:
        return False

    model = (getattr(config, "HQ_IMAGE_MODEL", None) or HQ_IMAGE_MODEL).strip()
    # Hard-lock — never read env for these; higher tiers cost more per token.
    quality = "low"
    size = "1024x576"
    clipped = (prompt or "").strip()
    if not clipped:
        return False

    t0 = time.time()
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{ATLAS_MEDIA_BASE}/model/generateImage",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "prompt": clipped,
                    "quality": quality,
                    "size": size,
                    "output_format": "jpeg",
                    "moderation": "low",
                    "enable_base64_output": False,
                    "enable_sync_mode": False,
                },
            )
            data = resp.json()
            pred_id = None
            if isinstance(data.get("data"), dict):
                pred_id = data["data"].get("id")
            pred_id = pred_id or data.get("id") or data.get("prediction_id")
            if not pred_id:
                print(f"[atlas] HQ image no id: {str(data)[:240]}")
                return False

            while time.time() - t0 < timeout_sec:
                time.sleep(1.5)
                poll = client.get(
                    f"{ATLAS_MEDIA_BASE}/model/prediction/{pred_id}",
                    headers={"Authorization": f"Bearer {key}"},
                )
                inner = poll.json().get("data", poll.json())
                status = str(inner.get("status", "")).lower()
                if status in ("succeeded", "completed", "done"):
                    outputs = inner.get("outputs") or inner.get("output") or []
                    if isinstance(outputs, str):
                        img_url = outputs
                    elif isinstance(outputs, list) and outputs:
                        img_url = outputs[0]
                    else:
                        return False
                    img = client.get(img_url, follow_redirects=True, timeout=60)
                    img.raise_for_status()
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img.content)
                    print(
                        f"[atlas] HQ image ok model={model} q={quality} size={size} "
                        f"{time.time() - t0:.1f}s → {Path(output_path).name}"
                    )
                    return True
                if status in ("failed", "error", "cancelled"):
                    print(f"[atlas] HQ image failed: {inner}")
                    return False
    except Exception as e:
        print(f"[atlas] HQ image error: {e}")
        return False
    print(f"[atlas] HQ image timeout after {timeout_sec}s")
    return False


def generate_ernie_image_file(
    prompt: str,
    output_path: str,
    *,
    timeout_sec: float = 60,
) -> bool:
    """
    Free ERNIE Image Turbo via Atlas — default for cinematic / body / avatar stills.
    Prompt is truncated (~490 chars) to avoid upstream ERNIE errors.
    """
    key = _atlas_key()
    if not key:
        return False

    clipped = (prompt or "").strip()[:490]
    if not clipped:
        return False

    t0 = time.time()
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{ATLAS_MEDIA_BASE}/model/generateImage",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": ERNIE_IMAGE_MODEL,
                    "prompt": clipped,
                    "size": "1376x768",
                    "n": 1,
                    "use_pe": True,
                    "num_inference_steps": 8,
                    "guidance_scale": 1,
                },
            )
            data = resp.json()
            pred_id = None
            if isinstance(data.get("data"), dict):
                pred_id = data["data"].get("id")
            pred_id = pred_id or data.get("id") or data.get("prediction_id")
            if not pred_id:
                print(f"[atlas] ERNIE no id: {str(data)[:200]}")
                return False

            while time.time() - t0 < timeout_sec:
                time.sleep(1.5)
                poll = client.get(
                    f"{ATLAS_MEDIA_BASE}/model/prediction/{pred_id}",
                    headers={"Authorization": f"Bearer {key}"},
                )
                inner = poll.json().get("data", poll.json())
                status = str(inner.get("status", "")).lower()
                if status in ("succeeded", "completed", "done"):
                    outputs = inner.get("outputs") or inner.get("output") or []
                    if isinstance(outputs, str):
                        img_url = outputs
                    elif isinstance(outputs, list) and outputs:
                        img_url = outputs[0]
                    else:
                        return False
                    img = client.get(img_url, follow_redirects=True, timeout=60)
                    img.raise_for_status()
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img.content)
                    if Path(output_path).stat().st_size < 1000:
                        return False
                    print(
                        f"[atlas] ERNIE ok {time.time() - t0:.1f}s → "
                        f"{Path(output_path).name}"
                    )
                    return True
                if status in ("failed", "error", "cancelled"):
                    print(f"[atlas] ERNIE failed: {inner}")
                    return False
    except Exception as e:
        print(f"[atlas] ERNIE error: {e}")
        return False
    print(f"[atlas] ERNIE timeout after {timeout_sec}s")
    return False
