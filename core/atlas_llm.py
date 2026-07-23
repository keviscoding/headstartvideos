"""
Atlas Cloud LLM + image helpers (OpenAI-compatible chat + generateImage).

We route former Google Gemini workloads through Atlas so a denied Google
project does not take down cooks. Prefer ATLASCLOUD_KEY; optional GEMINI_KEY
fallback only when Atlas is unset.
"""
from __future__ import annotations

import os
import threading
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

# Baidu ERNIE rate-limits hard under parallel load ("访问过于频繁" / HTML parse
# errors). Cap concurrency process-wide so cooks don't blank half the video.
_ERNIE_MAX_CONCURRENT = max(1, int(os.getenv("ERNIE_MAX_CONCURRENT", "3")))
_ERNIE_MAX_RETRIES = max(1, int(os.getenv("ERNIE_MAX_RETRIES", "3")))
_ernie_slots = threading.Semaphore(_ERNIE_MAX_CONCURRENT)
_ernie_cooldown_lock = threading.Lock()
_ernie_cooldown_until = 0.0


def _atlas_key() -> str:
    from core.atlas_runtime import get_atlas_key
    return get_atlas_key()


def _ernie_is_rate_limit(err: str) -> bool:
    e = (err or "").lower()
    return (
        "过于频繁" in (err or "")
        or "too frequent" in e
        or "rate limit" in e
        or "ratelimit" in e
        or "invalid character '<'" in e
        or "parse upstream" in e
        or "访问过于" in (err or "")
    )


def _ernie_wait_cooldown() -> None:
    global _ernie_cooldown_until
    with _ernie_cooldown_lock:
        until = _ernie_cooldown_until
    delay = until - time.time()
    if delay > 0:
        time.sleep(min(delay, 20.0))


def _ernie_trip_cooldown(seconds: float = 8.0) -> None:
    global _ernie_cooldown_until
    with _ernie_cooldown_lock:
        _ernie_cooldown_until = max(_ernie_cooldown_until, time.time() + seconds)


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
        # Explicit Atlas ids win; bare Gemini short names → configured Atlas model
        if model.startswith("google/"):
            atlas_model = model
        elif "gemini" in model.lower() and "/" not in model:
            pass  # keep atlas_model default
        elif "/" in model:
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


def _extract_atlas_message_text(msg: dict) -> str:
    """Pull assistant text from OpenAI-compatible and Gemini-shaped Atlas payloads."""
    if not isinstance(msg, dict):
        return ""

    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    # Some models return content as a list of parts: [{"type":"text","text":"..."}]
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str) and part.strip():
                chunks.append(part.strip())
            elif isinstance(part, dict):
                t = part.get("text") or part.get("content") or ""
                if isinstance(t, str) and t.strip():
                    chunks.append(t.strip())
        if chunks:
            return "\n".join(chunks).strip()

    for key in (
        "reasoning_content",
        "reasoning",
        "output_text",
        "text",
        "refusal",
    ):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    return ""


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

    # Gemini-via-Atlas sometimes burns the whole max_tokens budget on hidden
    # thinking and returns finish_reason=length with an empty/missing message.
    # Retry once with a larger cap before failing the cook.
    attempts = [max(256, int(max_tokens))]
    if max_tokens < 16384:
        attempts.append(min(16384, max(max_tokens * 2, 12288)))

    last_err = "Atlas LLM returned empty content"
    with httpx.Client(timeout=180) as client:
        for attempt_tokens in attempts:
            body: dict = {
                "model": model or ATLAS_TEXT_MODEL,
                "messages": messages,
                "max_tokens": attempt_tokens,
            }
            if temperature is not None:
                body["temperature"] = temperature

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
                last_err = f"Atlas LLM empty response: {str(data)[:300]}"
                continue
            choice0 = choices[0] if isinstance(choices[0], dict) else {}
            msg = choice0.get("message") or {}
            text = _extract_atlas_message_text(msg)
            if not text:
                # Some gateways put the answer on the choice itself
                text = _extract_atlas_message_text(choice0)
            if text:
                if attempt_tokens != attempts[0]:
                    print(
                        f"[atlas] LLM ok after empty/length retry "
                        f"(max_tokens {attempts[0]}→{attempt_tokens})"
                    )
                return text

            finish = choice0.get("finish_reason") or choice0.get("native_finish_reason") or ""
            usage = data.get("usage") or {}
            last_err = (
                f"Atlas LLM returned empty content "
                f"(finish_reason={finish!r} model={body.get('model')} "
                f"max_tokens={attempt_tokens} usage={usage})"
            )
            # Only worth retrying when the model hit the length wall / omitted message.
            if str(finish).lower() not in ("length", "max_tokens", ""):
                break
            print(f"[atlas] {last_err} — retrying with more tokens")

    raise RuntimeError(last_err)


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


def _image_payload_for_atlas(image: str | Path) -> str:
    """URL, data-URI, or local path → Atlas `image` field value."""
    s = str(image or "").strip()
    if not s:
        raise ValueError("image is required")
    if s.startswith(("http://", "https://", "data:")):
        return s
    path = Path(s)
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {s}")
    import base64
    raw = path.read_bytes()
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("Image exceeds 10MB Atlas limit")
    ext = path.suffix.lower()
    mime = "image/jpeg"
    if ext == ".png":
        mime = "image/png"
    elif ext == ".webp":
        mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _is_transient_http_err(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return (
        "timed out" in msg
        or "timeout" in msg
        or "temporarily" in msg
        or "connection reset" in msg
        or "connecterror" in name
        or "readtimeout" in name
    )


def generate_video_file(
    prompt: str,
    image: str | Path,
    output_path: str | Path,
    *,
    model: str | None = None,
    duration: int = 5,
    resolution: str | None = None,
    generate_audio: bool | None = None,
    aspect_ratio: str = "16:9",
    camera_fixed: bool = False,
    seed: int = -1,
    last_image: str | Path | None = None,
    timeout_sec: float = 720,
    max_attempts: int = 3,
) -> bool:
    """
    Image-to-video via Atlas generateVideo (Seedance etc.).
    Returns True on success; writes MP4 to output_path.

    Seedance often stays "processing" for minutes; Atlas poll GETs also
    flake with read timeouts. We retry transient network errors and keep
    polling the same prediction instead of aborting the scene.
    """
    key = _atlas_key()
    if not key:
        print("[atlas] generateVideo: no ATLASCLOUD_KEY")
        return False

    model = (model or getattr(config, "ATLAS_I2V_MODEL", None) or
             "bytedance/seedance-v1.5-pro/image-to-video-fast").strip()
    resolution = (resolution or getattr(config, "ATLAS_I2V_RESOLUTION", None) or "720p").strip()
    if generate_audio is None:
        generate_audio = bool(getattr(config, "ATLAS_I2V_GENERATE_AUDIO", True))
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 5
    duration = max(4, min(12, duration))
    attempts = max(1, int(max_attempts or 1))
    wall = max(120.0, float(timeout_sec or 720))

    clipped = (prompt or "").strip() or "Subtle natural motion, characters breathe and blink"
    try:
        image_val = _image_payload_for_atlas(image)
    except Exception as e:
        print(f"[atlas] generateVideo bad image: {e}")
        return False

    body: dict = {
        "model": model,
        "prompt": clipped,
        "image": image_val,
        "duration": duration,
        "resolution": resolution,
        "generate_audio": bool(generate_audio),
        "camera_fixed": bool(camera_fixed),
        "aspect_ratio": aspect_ratio or "16:9",
        "seed": seed if seed is not None else -1,
    }
    if last_image:
        try:
            body["last_image"] = _image_payload_for_atlas(last_image)
        except Exception as e:
            print(f"[atlas] generateVideo last_image skipped: {e}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    # Generous read timeout — Atlas occasionally stalls mid-response under load
    client_timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
    last_err = ""

    for attempt in range(1, attempts + 1):
        t0 = time.time()
        pred_id = None
        try:
            with httpx.Client(timeout=client_timeout) as client:
                # Create prediction (retry soft failures inside this attempt)
                for create_try in range(1, 4):
                    try:
                        resp = client.post(
                            f"{ATLAS_MEDIA_BASE}/model/generateVideo",
                            headers=headers,
                            json=body,
                        )
                    except Exception as e:
                        last_err = f"create timed out/network: {e}"
                        print(f"[atlas] generateVideo create try {create_try}: {e}")
                        if create_try < 3 and _is_transient_http_err(e):
                            time.sleep(2.0 * create_try)
                            continue
                        raise
                    data = resp.json() if resp.content else {}
                    if resp.status_code in (429, 500, 502, 503, 504):
                        last_err = f"create HTTP {resp.status_code}: {str(data)[:200]}"
                        print(f"[atlas] generateVideo {last_err}")
                        if create_try < 3:
                            time.sleep(3.0 * create_try)
                            continue
                        break
                    if resp.status_code >= 400:
                        last_err = f"create HTTP {resp.status_code}: {str(data)[:300]}"
                        print(f"[atlas] generateVideo {last_err}")
                        break
                    if isinstance(data.get("data"), dict):
                        pred_id = data["data"].get("id")
                    pred_id = pred_id or data.get("id") or data.get("prediction_id")
                    if not pred_id:
                        last_err = f"no prediction id: {str(data)[:240]}"
                        print(f"[atlas] generateVideo {last_err}")
                    break

                if not pred_id:
                    if attempt < attempts:
                        time.sleep(2.0 * attempt)
                        continue
                    return False

                sleep_s = 2.0
                while time.time() - t0 < wall:
                    time.sleep(sleep_s)
                    sleep_s = min(6.0, sleep_s + 0.35)
                    try:
                        poll = client.get(
                            f"{ATLAS_MEDIA_BASE}/model/prediction/{pred_id}",
                            headers={"Authorization": f"Bearer {key}"},
                            timeout=httpx.Timeout(connect=20.0, read=90.0, write=30.0, pool=20.0),
                        )
                    except Exception as e:
                        # Transient poll flake — keep waiting on the same prediction
                        last_err = f"poll: {e}"
                        print(f"[atlas] generateVideo poll retry ({pred_id[:8]}…): {e}")
                        if _is_transient_http_err(e):
                            sleep_s = min(8.0, sleep_s + 1.0)
                            continue
                        raise
                    try:
                        inner = poll.json().get("data", poll.json())
                    except Exception as e:
                        last_err = f"poll bad json: {e}"
                        print(f"[atlas] generateVideo {last_err}")
                        continue
                    if not isinstance(inner, dict):
                        continue
                    status = str(inner.get("status", "")).lower()
                    if status in ("succeeded", "completed", "done"):
                        outputs = inner.get("outputs") or inner.get("output") or []
                        if isinstance(outputs, str):
                            vid_url = outputs
                        elif isinstance(outputs, list) and outputs:
                            first = outputs[0]
                            vid_url = first if isinstance(first, str) else (
                                (first or {}).get("url") or (first or {}).get("video") or ""
                            )
                        elif isinstance(outputs, dict):
                            vid_url = outputs.get("url") or outputs.get("video") or ""
                        else:
                            last_err = f"no outputs: {str(inner)[:240]}"
                            print(f"[atlas] generateVideo {last_err}")
                            break
                        if not vid_url:
                            last_err = "empty video url"
                            break
                        try:
                            vid = client.get(
                                vid_url,
                                follow_redirects=True,
                                timeout=httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0),
                            )
                            vid.raise_for_status()
                        except Exception as e:
                            last_err = f"download: {e}"
                            print(f"[atlas] generateVideo download retry: {e}")
                            if _is_transient_http_err(e):
                                continue
                            break
                        with open(out, "wb") as f:
                            f.write(vid.content)
                        if out.stat().st_size < 1000:
                            last_err = "empty download"
                            print("[atlas] generateVideo empty download")
                            break
                        print(
                            f"[atlas] video ok model={model} {duration}s "
                            f"{time.time() - t0:.1f}s attempt={attempt} → {out.name}"
                        )
                        return True
                    if status in ("failed", "error", "cancelled"):
                        err = inner.get("error") or inner.get("message") or inner
                        last_err = f"provider {status}: {err}"
                        print(f"[atlas] generateVideo failed: {err}")
                        break
                else:
                    last_err = f"timeout after {wall:.0f}s (pred={pred_id})"
                    print(f"[atlas] generateVideo {last_err}")
        except Exception as e:
            last_err = str(e)
            print(f"[atlas] generateVideo error attempt {attempt}/{attempts}: {e}")
            if attempt < attempts and _is_transient_http_err(e):
                time.sleep(2.0 * attempt)
                continue
            if attempt < attempts:
                time.sleep(2.0 * attempt)
                continue
            return False

        if attempt < attempts:
            print(f"[atlas] generateVideo retrying scene ({attempt}/{attempts}): {last_err}")
            time.sleep(2.0 * attempt)
            continue
        print(f"[atlas] generateVideo giving up: {last_err}")
        return False

    print(f"[atlas] generateVideo giving up: {last_err}")
    return False


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

    Cost-locked to the cheapest *valid* settings Atlas allows for 16:9:
      quality=low, size=1536x864 (exact 16:9 at 1K long-edge — not 2K/4K).
      Note: 1024x576 is rejected by GPT Image 2 (below min pixel budget).
    """
    key = _atlas_key()
    if not key:
        return False

    model = (getattr(config, "HQ_IMAGE_MODEL", None) or HQ_IMAGE_MODEL).strip()
    # Hard-lock — never read env for these; higher tiers cost more per token.
    quality = "low"
    size = "1536x864"
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
    timeout_sec: float = 75,
) -> bool:
    """
    Free ERNIE Image Turbo via Atlas — default for cinematic / body / avatar stills.

    Prompt is truncated (~490 chars). Concurrent calls are capped and rate-limit
    errors are retried with backoff — unrestricted parallelism was blanking
    explainer cooks (Baidu "访问过于频繁" / upstream HTML parse failures).
    """
    ok, _err = generate_ernie_image_file_detailed(
        prompt, output_path, timeout_sec=timeout_sec,
    )
    return ok


def generate_ernie_image_file_detailed(
    prompt: str,
    output_path: str,
    *,
    timeout_sec: float = 75,
) -> tuple[bool, str]:
    """Like generate_ernie_image_file but returns (ok, error_message)."""
    key = _atlas_key()
    if not key:
        return False, "ATLASCLOUD_KEY not set"

    clipped = (prompt or "").strip()[:490]
    if not clipped:
        return False, "empty prompt"

    last_err = "ERNIE failed"
    for attempt in range(_ERNIE_MAX_RETRIES):
        _ernie_wait_cooldown()
        acquired = _ernie_slots.acquire(timeout=120)
        if not acquired:
            return False, "ERNIE concurrency queue timeout"
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
                if resp.status_code >= 400:
                    last_err = f"ERNIE HTTP {resp.status_code}: {(resp.text or '')[:180]}"
                    if _ernie_is_rate_limit(last_err) or resp.status_code in (429, 503):
                        _ernie_trip_cooldown(6.0 + attempt * 4.0)
                        time.sleep(2.0 + attempt * 2.0)
                        continue
                    return False, last_err

                data = resp.json()
                pred_id = None
                if isinstance(data.get("data"), dict):
                    pred_id = data["data"].get("id")
                pred_id = pred_id or data.get("id") or data.get("prediction_id")
                if not pred_id:
                    last_err = f"ERNIE no id: {str(data)[:200]}"
                    return False, last_err

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
                            last_err = "ERNIE completed with no outputs"
                            break
                        img = client.get(img_url, follow_redirects=True, timeout=60)
                        img.raise_for_status()
                        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(output_path, "wb") as f:
                            f.write(img.content)
                        if Path(output_path).stat().st_size < 1000:
                            last_err = "ERNIE image too small"
                            break
                        if attempt > 0:
                            print(
                                f"[atlas] ERNIE ok after retry {attempt + 1} "
                                f"{time.time() - t0:.1f}s → {Path(output_path).name}"
                            )
                        else:
                            print(
                                f"[atlas] ERNIE ok {time.time() - t0:.1f}s → "
                                f"{Path(output_path).name}"
                            )
                        return True, ""
                    if status in ("failed", "error", "cancelled"):
                        last_err = str(inner.get("error") or inner)[:240]
                        print(f"[atlas] ERNIE failed (attempt {attempt + 1}): {last_err}")
                        if _ernie_is_rate_limit(last_err):
                            _ernie_trip_cooldown(8.0 + attempt * 4.0)
                            break  # retry outer loop
                        return False, last_err
                else:
                    last_err = f"ERNIE timeout after {timeout_sec}s"
        except Exception as e:
            last_err = f"ERNIE error: {e}"
            print(f"[atlas] {last_err}")
            if _ernie_is_rate_limit(last_err):
                _ernie_trip_cooldown(8.0 + attempt * 4.0)
        finally:
            _ernie_slots.release()

        if attempt + 1 < _ERNIE_MAX_RETRIES:
            time.sleep(2.0 + attempt * 3.0)

    return False, last_err
