"""
Thumbnail generation using Atlas Cloud Nano Banana models.

Workflow:
1. Optional: upload channel screenshot refs → edit endpoint
2. Else / fallback: text-to-image
3. Poll prediction, download PNG

IMAGE_OTHER is a known intermittent Gemini image failure — we retry and
fall through cheaper/more stable Atlas models instead of surfacing 500s.
"""

from __future__ import annotations
import time
from pathlib import Path

from config import ATLASCLOUD_KEY

ATLAS_BASE = "https://api.atlascloud.ai/api/v1"

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


def _upload_media(file_path: str) -> str:
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
            timeout=60,
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
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"[thumbnail] Poll error: HTTP {resp.status_code}")
            time.sleep(3)
            continue

        raw = resp.json()
        data = raw.get("data", raw)
        status = data.get("status", "").lower()

        if status in ("succeeded", "completed", "done"):
            return data
        elif status in ("failed", "error", "canceled"):
            error_msg = data.get("error", data.get("logs", "Unknown error"))
            raise RuntimeError(f"Generation failed: {error_msg}")

        time.sleep(3)

    raise TimeoutError(f"Prediction {prediction_id} timed out after {timeout}s")


def _is_transient_image_error(err: Exception) -> bool:
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
) -> bool:
    import httpx

    resp = httpx.post(
        f"{ATLAS_BASE}/model/generateImage",
        headers=_get_headers(),
        json=payload,
        timeout=30,
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
    result = _poll_prediction(prediction_id)
    url = _output_url(result)
    if not url:
        raise RuntimeError(f"No output URL: {str(result)[:200]}")
    _download_image(url, str(out_path))
    print(f"[thumbnail] Generated: {out_path.name} via {payload.get('model')}")
    return True


def generate_thumbnails(
    title: str,
    reference_image_paths: list[str],
    style_prompt: str = "",
    model: str = "",
    num_images: int = 1,
    output_dir: str = "",
) -> list[str]:
    """Generate thumbnails with reference images (edit), falling back to T2I."""
    if not ATLASCLOUD_KEY:
        raise ValueError("ATLASCLOUD_KEY is not set")

    ref_urls = []
    atlas_balance_error = False
    for ref_path in reference_image_paths:
        if not Path(ref_path).exists():
            print(f"[thumbnail] Reference not found: {ref_path}")
            continue
        try:
            url = _upload_media(ref_path)
            if url and url.startswith("http"):
                ref_urls.append(url)
        except Exception as e:
            print(f"[thumbnail] Failed to upload {ref_path}: {e}")
            if "402" in str(e) or "insufficient" in str(e).lower():
                atlas_balance_error = True

    if not ref_urls:
        print("[thumbnail] No reference images uploaded, using text-to-image mode")
        try:
            paths = _generate_text_only(title, style_prompt, num_images, output_dir)
            if paths:
                return paths
        except Exception as e:
            print(f"[thumbnail] Atlas T2I failed: {e}")
            atlas_balance_error = atlas_balance_error or "balance" in str(e).lower()
        if atlas_balance_error:
            raise RuntimeError("Thumbnail service unavailable (provider balance). Try again later.")
        return []

    extra = f"Additional style instructions: {style_prompt}" if style_prompt else ""
    prompt_text = THUMBNAIL_PROMPT_TEMPLATE.format(title=title, extra_instructions=extra)

    out_dir = Path(output_dir) if output_dir else Path("output/thumbnails")
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_paths: list[str] = []
    models = ([model] if model else []) + [m for m in _EDIT_MODELS if m != model]

    for i in range(num_images):
        out_path = out_dir / f"thumb_{i + 1:02d}.png"
        ok = False
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
                _submit_and_download(payload, out_path, label=f"edit/{edit_model}")
                generated_paths.append(str(out_path))
                ok = True
                break
            except Exception as e:
                last_err = e
                print(f"[thumbnail] edit failed ({edit_model}): {e}")
                if "insufficient balance" in str(e).lower():
                    atlas_balance_error = True
                    break
                if not _is_transient_image_error(e) and "Submit failed" not in str(e):
                    # still try next model — Atlas model availability varies
                    continue
                time.sleep(1.0)
        if not ok:
            print(f"[thumbnail] All edit models failed ({last_err}); trying T2I")
            try:
                t2i = _generate_text_only(title, style_prompt, 1, str(out_dir))
                if t2i:
                    # rename first result into expected slot if needed
                    generated_paths.extend(t2i)
            except Exception as e:
                print(f"[thumbnail] T2I fallback failed: {e}")

    if generated_paths:
        return generated_paths

    if atlas_balance_error:
        raise RuntimeError("Thumbnail service unavailable (provider balance). Try again later.")
    return generated_paths


def _generate_text_only(
    title: str,
    style_prompt: str = "",
    num_images: int = 1,
    output_dir: str = "",
) -> list[str]:
    """Generate thumbnail without reference images (text-to-image mode)."""
    style = style_prompt or "modern, bold, eye-catching YouTube style"
    base_prompt = (
        f"Generate a YouTube thumbnail for a video titled: \"{title}\". "
        f"Style: {style}. "
        "Make it eye-catching, high contrast, bold readable title text if appropriate, "
        "16:9 aspect ratio, professional quality. No watermarks, no real brand logos."
    )

    out_dir = Path(output_dir) if output_dir else Path("output/thumbnails")
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_paths: list[str] = []

    for i in range(num_images):
        out_path = out_dir / f"thumb_{i + 1:02d}.png"
        last_err: Exception | None = None
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
                print(f"[thumbnail] T2I {i + 1}/{num_images} model={model} attempt={attempt + 1}")
                _submit_and_download(payload, out_path, label=f"t2i/{model}")
                generated_paths.append(str(out_path))
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"[thumbnail] T2I failed ({model}): {e}")
                if "insufficient balance" in str(e).lower():
                    raise RuntimeError("Atlas insufficient balance") from e
                time.sleep(0.8)
        if last_err and str(out_path) not in generated_paths:
            # Don't abort the whole batch — try remaining slots
            print(f"[thumbnail] Giving up on slot {i + 1}: {last_err}")

    if not generated_paths and last_err:
        raise RuntimeError(str(last_err)) from last_err
    return generated_paths


def _download_image(url: str, output_path: str):
    """Download an image from URL to local path."""
    import httpx

    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    Path(output_path).write_bytes(resp.content)


def generate_thumbnail_no_refs(
    title: str,
    style_description: str = "",
    model: str = "",
    output_dir: str = "",
    count: int = 2,
) -> list[str]:
    """Generate a thumbnail without reference images."""
    if not ATLASCLOUD_KEY:
        raise ValueError("ATLASCLOUD_KEY is not set")
    paths = _generate_text_only(title, style_description, count, output_dir)
    if paths:
        return paths
    raise RuntimeError(
        "Thumbnail generation failed after retries — try a simpler title/style, "
        "or check Atlas balance at https://www.atlascloud.ai/console/billing"
    )
