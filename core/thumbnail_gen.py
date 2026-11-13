"""
Thumbnail generation using Atlas Cloud Nano Banana models.

Workflow:
1. Optional: upload channel screenshot refs → edit endpoint
2. Else / fallback: text-to-image
3. Poll prediction, download PNG

IMAGE_OTHER is a known intermittent Gemini image failure — we retry and
fall through cheaper/more stable Atlas models instead of surfacing 500s.

The website asks for these inside one request, and Cloudflare cuts a request at 100 s with its own HTML page, which
Thumbnail Studio showed as "Unexpected token '<', <!DOCTYPE ... is not valid JSON" (customers 2026-11-06 and
2026-11-13). So the options are made at the same time, and every Atlas call fits in one budget (BUDGET_S).
"""

from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import ATLASCLOUD_KEY

ATLAS_BASE = "https://api.atlascloud.ai/api/v1"

BUDGET_S = 75.0         # one request's thumbnails, uploads included; the rest of Cloudflare's 100 s stages the files
MIN_TRY_S = 12.0        # less time left than this: don't start another model
POLL_S = 3.0
TOO_LONG = "Thumbnails took too long this time. Try again in a moment."

# Prefer cheap + reliable developer tiers. Pro @ 2k was throwing IMAGE_OTHER overnight.
_T2I_MODELS = [
    "google/nano-banana-2-lite/text-to-image-developer",  # ~$0.028 @ 1k
    "google/nano-banana-2/text-to-image-developer",
    "google/nano-banana-pro/text-to-image-developer",
]
_EDIT_MODELS = [
    "google/nano-banana-2-lite/edit-developer",
    "google/nano-banana-2/edit-developer",
    "google/nano-banana-pro/edit-developer",
    "google/nano-banana-pro/edit",
]

THUMBNAIL_PROMPT_TEMPLATE = """\
You are a YouTube thumbnail designer. I've provided reference thumbnails from \
a specific YouTube channel. Study them carefully and learn:
- Visual style (colors, contrast, saturation, lighting)
- Text placement, typography, and font style
- Layout and composition patterns
- Use of faces, expressions, and imagery
- Overall aesthetic and branding

Now generate a NEW YouTube thumbnail for this video title:
"{title}"

{extra_instructions}

CRITICAL REQUIREMENTS:
- Match the visual style of the reference thumbnails EXACTLY
- Make it eye-catching and click-worthy
- Use bold, readable text if the reference style uses text
- 16:9 aspect ratio, high resolution
- The thumbnail MUST look like it belongs on the same channel as the references
- Do NOT copy the reference thumbnails — create a new design in the same style"""


def _left(deadline: float | None) -> float:
    """Seconds left before the deadline (time.monotonic()); no deadline, no limit."""
    return float("inf") if deadline is None else deadline - time.monotonic()


def _all_at_once(fn, items: list) -> list:
    """fn(item) for every item at the same time: the results in order, an exception in place of a failed one."""
    def guarded(item):
        try:
            return fn(item)
        except Exception as e:                                          # noqa: BLE001 - handed back to the caller
            return e
    if len(items) <= 1:
        return [guarded(item) for item in items]
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return list(pool.map(guarded, items))


def _get_headers() -> dict:
    key = ATLASCLOUD_KEY
    if not key:
        raise ValueError(
            "ATLASCLOUD_KEY not set. Add your Atlas Cloud API key in Settings."
        )
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _upload_media(file_path: str, deadline: float | None = None) -> str:
    """Upload a local file to Atlas Cloud and return its public URL."""
    import httpx

    key = ATLASCLOUD_KEY
    if not key:
        raise ValueError("ATLASCLOUD_KEY not set")

    with open(file_path, "rb") as f:
        resp = httpx.post(
            f"{ATLAS_BASE}/model/uploadMedia",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (Path(file_path).name, f)},
            timeout=max(1.0, min(60.0, _left(deadline))),
        )

    if resp.status_code != 200:
        print(f"[thumbnail] Upload failed ({resp.status_code}): {resp.text[:200]}")
        raise RuntimeError(f"Media upload failed: HTTP {resp.status_code}")

    raw = resp.json()
    inner = raw.get("data", raw)
    url = (
        inner.get("download_url", "")
        or inner.get("url", "")
        or inner.get("file_url", "")
    )

    if not url:
        import re
        text = str(raw)
        m = re.search(r'https?://\S+', text)
        if m:
            url = m.group(0).rstrip("',\"}")

    if not url:
        raise RuntimeError(f"No URL in upload response: {raw}")

    print(f"[thumbnail] Uploaded {Path(file_path).name} -> {url[:80]}...")
    return url


def _poll_prediction(prediction_id: str, timeout: int = 120) -> dict:
    """Poll Atlas Cloud for prediction result."""
    import httpx

    url = f"{ATLAS_BASE}/model/prediction/{prediction_id}"
    headers = {"Authorization": f"Bearer {ATLASCLOUD_KEY}"}

    start = time.time()
    while time.time() - start < timeout:
        resp = httpx.get(url, headers=headers, timeout=max(1.0, min(30.0, timeout - (time.time() - start))))
        if resp.status_code != 200:
            print(f"[thumbnail] Poll error: HTTP {resp.status_code}")
            time.sleep(POLL_S)
            continue

        raw = resp.json()
        data = raw.get("data", raw)
        status = data.get("status", "").lower()

        if status in ("succeeded", "completed", "done"):
            return data
        elif status in ("failed", "error", "canceled"):
            error_msg = data.get("error", data.get("logs", "Unknown error"))
            raise RuntimeError(f"Generation failed: {error_msg}")

        time.sleep(POLL_S)

    raise TimeoutError(f"Prediction {prediction_id} timed out after {timeout}s")


def _is_transient_image_error(err: Exception) -> bool:
    try:
        from core.atlas_llm import is_atlas_image_transient_error
        if is_atlas_image_transient_error(err):
            return True
    except Exception:
        pass
    msg = str(err).upper()
    return any(
        token in msg
        for token in (
            "IMAGE_OTHER",
            "NO_IMAGE",
            "IMAGE_SAFETY",
            "NO PARTS FOUND",
            "TIMEOUT",
            "503",
            "429",
            "RATE",
            "PARSE UPSTREAM",
            "INVALID CHARACTER",
        )
    )


def _output_url(result: dict) -> str:
    outputs = result.get("outputs") or result.get("output") or []
    if isinstance(outputs, str):
        return outputs if outputs.startswith("http") else ""
    if isinstance(outputs, list) and outputs:
        first = outputs[0]
        return first if isinstance(first, str) and first.startswith("http") else ""
    return ""


def _submit_and_download(
    payload: dict,
    out_path: Path,
    *,
    label: str,
    deadline: float | None = None,
) -> bool:
    import httpx

    if _left(deadline) < MIN_TRY_S:
        raise TimeoutError(f"{label}: no time left in this request")
    resp = httpx.post(
        f"{ATLAS_BASE}/model/generateImage",
        headers=_get_headers(),
        json=payload,
        timeout=min(30.0, _left(deadline)),
    )
    if resp.status_code != 200:
        body = (resp.text or "")[:300]
        print(f"[thumbnail] {label} submit failed: HTTP {resp.status_code} - {body}")
        if resp.status_code == 402 or "insufficient balance" in body.lower():
            raise RuntimeError("Atlas insufficient balance")
        raise RuntimeError(f"Submit failed: HTTP {resp.status_code}")

    raw = resp.json()
    inner = raw.get("data", raw)
    prediction_id = inner.get("id", "")

    if not prediction_id:
        outputs = inner.get("outputs") or []
        if isinstance(outputs, list) and outputs:
            _download_image(outputs[0], str(out_path))
            return True
        raise RuntimeError(f"No prediction ID: {str(raw)[:200]}")

    print(f"[thumbnail] {label} polling {prediction_id}...")
    result = _poll_prediction(prediction_id, timeout=min(120.0, _left(deadline) - 5.0))     # 5 s to download it
    url = _output_url(result)
    if not url:
        raise RuntimeError(f"No output URL: {str(result)[:200]}")
    _download_image(url, str(out_path), timeout=max(1.0, min(30.0, _left(deadline))))
    print(f"[thumbnail] Generated: {out_path.name} via {payload.get('model')}")
    return True


def _out_of_balance(err) -> bool:
    return isinstance(err, Exception) and ("insufficient balance" in str(err).lower() or "402" in str(err))


def _finish(results: list, deadline: float | None) -> list[str]:
    """The slots that made a picture, in order; none: the clearest reason (provider balance, time, the last error)."""
    paths = [r for r in results if isinstance(r, str)]
    if paths:
        return paths
    errors = [r for r in results if isinstance(r, Exception)]
    if any(_out_of_balance(e) for e in errors):
        raise RuntimeError("Thumbnail service unavailable (provider balance). Try again later.")
    if errors and (_left(deadline) < MIN_TRY_S or any(isinstance(e, TimeoutError) for e in errors)):
        print("[thumbnail] out of time for this request")
        raise RuntimeError(TOO_LONG)
    if errors:
        raise RuntimeError(str(errors[-1])) from errors[-1]
    return []


def generate_thumbnails(
    title: str,
    reference_image_paths: list[str],
    style_prompt: str = "",
    model: str = "",
    num_images: int = 1,
    output_dir: str = "",
    budget_s: float | None = BUDGET_S,
) -> list[str]:
    """Generate thumbnails with reference images (edit), falling back to T2I. The options are made at the same time,
    and everything fits in budget_s (None: no limit)."""
    if not ATLASCLOUD_KEY:
        raise ValueError("ATLASCLOUD_KEY is not set")
    deadline = None if budget_s is None else time.monotonic() + budget_s

    refs = []
    for ref_path in reference_image_paths:
        if Path(ref_path).exists():
            refs.append(ref_path)
        else:
            print(f"[thumbnail] Reference not found: {ref_path}")
    ref_urls = []
    atlas_balance_error = False
    for ref_path, url in zip(refs, _all_at_once(lambda p: _upload_media(p, deadline), refs)):
        if isinstance(url, Exception):
            print(f"[thumbnail] Failed to upload {ref_path}: {url}")
            atlas_balance_error = atlas_balance_error or _out_of_balance(url)
        elif url and url.startswith("http"):
            ref_urls.append(url)

    if not ref_urls:
        print("[thumbnail] No reference images uploaded, using text-to-image mode")
        if atlas_balance_error:
            raise RuntimeError("Thumbnail service unavailable (provider balance). Try again later.")
        return _generate_text_only(title, style_prompt, num_images, output_dir, deadline=deadline)

    extra = f"Additional style instructions: {style_prompt}" if style_prompt else ""
    prompt_text = THUMBNAIL_PROMPT_TEMPLATE.format(title=title, extra_instructions=extra)

    out_dir = Path(output_dir) if output_dir else Path("output/thumbnails")
    out_dir.mkdir(parents=True, exist_ok=True)

    models = ([model] if model else []) + [m for m in _EDIT_MODELS if m != model]

    def one(i: int) -> str:
        out_path = out_dir / f"thumb_{i + 1:02d}.png"
        last_err: Exception | None = None
        for edit_model in models:
            payload = {
                "model": edit_model,
                "prompt": prompt_text,
                "images": ref_urls,
                "aspect_ratio": "16:9",
                "resolution": "1k",
                "output_format": "png",
                "enable_base64_output": False,
            }
            try:
                print(
                    f"[thumbnail] edit {i + 1}/{num_images} "
                    f"model={edit_model} refs={len(ref_urls)}"
                )
                _submit_and_download(payload, out_path, label=f"edit/{edit_model}", deadline=deadline)
                return str(out_path)
            except Exception as e:
                last_err = e
                print(f"[thumbnail] edit failed ({edit_model}): {e}")
                if _out_of_balance(e) or _left(deadline) < MIN_TRY_S:
                    break
                if not _is_transient_image_error(e) and "Submit failed" not in str(e):
                    # still try next model — Atlas model availability varies
                    continue
                time.sleep(1.0)
        if _out_of_balance(last_err):
            raise last_err
        print(f"[thumbnail] All edit models failed ({last_err}); trying T2I")
        return _t2i_one(_t2i_prompt(title, style_prompt), out_path, f"{i + 1}/{num_images}", deadline)

    return _finish(_all_at_once(one, list(range(num_images))), deadline)


def _t2i_prompt(title: str, style_prompt: str = "") -> str:
    style = style_prompt or "modern, bold, eye-catching YouTube style"
    return (
        f"Generate a YouTube thumbnail for a video titled: \"{title}\". "
        f"Style: {style}. "
        "Make it eye-catching, high contrast, bold readable title text if appropriate, "
        "16:9 aspect ratio, professional quality. No watermarks, no real brand logos."
    )


def _t2i_one(base_prompt: str, out_path: Path, slot: str, deadline: float | None) -> str:
    """One text-to-image thumbnail into out_path, trying each model in turn; raises the last error."""
    last_err: Exception = RuntimeError("No thumbnail model tried")
    for attempt, model in enumerate(_T2I_MODELS):
        # Slight prompt jitter on retries helps IMAGE_OTHER intermittency
        prompt = base_prompt if attempt == 0 else (
            base_prompt + f" Variation {attempt + 1}: cinematic lighting, clean composition."
        )
        payload = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "resolution": "1k",
            "output_format": "png",
            "enable_base64_output": False,
        }
        try:
            print(f"[thumbnail] T2I {slot} model={model} attempt={attempt + 1}")
            _submit_and_download(payload, out_path, label=f"t2i/{model}", deadline=deadline)
            return str(out_path)
        except Exception as e:
            last_err = e
            print(f"[thumbnail] T2I failed ({model}): {e}")
            if _out_of_balance(e):
                raise RuntimeError("Atlas insufficient balance") from e
            if _left(deadline) < MIN_TRY_S:
                break
            time.sleep(0.8)
    print(f"[thumbnail] Giving up on slot {slot}: {last_err}")
    raise last_err


def _generate_text_only(
    title: str,
    style_prompt: str = "",
    num_images: int = 1,
    output_dir: str = "",
    deadline: float | None = None,
) -> list[str]:
    """Generate thumbnails without reference images (text-to-image mode), all at the same time."""
    base_prompt = _t2i_prompt(title, style_prompt)
    out_dir = Path(output_dir) if output_dir else Path("output/thumbnails")
    out_dir.mkdir(parents=True, exist_ok=True)
    return _finish(_all_at_once(lambda i: _t2i_one(base_prompt, out_dir / f"thumb_{i + 1:02d}.png",
                                                   f"{i + 1}/{num_images}", deadline),
                                list(range(num_images))), deadline)


def _download_image(url: str, output_path: str, timeout: float = 30):
    """Download an image from URL to local path."""
    import httpx

    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    Path(output_path).write_bytes(resp.content)


def generate_thumbnail_no_refs(
    title: str,
    style_description: str = "",
    model: str = "",
    output_dir: str = "",
    count: int = 2,
    budget_s: float | None = BUDGET_S,
) -> list[str]:
    """Generate a thumbnail without reference images, the options at the same time, within budget_s."""
    if not ATLASCLOUD_KEY:
        raise ValueError("ATLASCLOUD_KEY is not set")
    deadline = None if budget_s is None else time.monotonic() + budget_s
    paths = _generate_text_only(title, style_description, count, output_dir, deadline=deadline)
    if paths:
        return paths
    raise RuntimeError(
        "Thumbnail generation failed after retries — try a simpler title/style, "
        "or check Atlas balance at https://www.atlascloud.ai/console/billing"
    )
