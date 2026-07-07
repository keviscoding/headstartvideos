"""
Thumbnail generation using Atlas Cloud's Nano Banana Pro (Gemini 3 Pro Image).

Workflow:
1. User uploads channel page screenshots as reference images
2. Reference images are uploaded to Atlas Cloud via uploadMedia
3. System sends reference image URLs + prompt to Nano Banana Pro edit endpoint
4. System polls for result and downloads generated thumbnail

Atlas Cloud API:
  - POST /api/v1/model/uploadMedia   -> upload local file, get public URL
  - POST /api/v1/model/generateImage -> submit generation task
  - GET  /api/v1/model/prediction/{id} -> poll for result
"""

from __future__ import annotations
import time
from pathlib import Path

from config import ATLASCLOUD_KEY, GEMINI_KEY

ATLAS_BASE = "https://api.atlascloud.ai/api/v1"

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

_NANO_BANANA_MODELS = [
    "google/nano-banana-pro/edit",
    "google/nano-banana-pro/text-to-image",
]


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


def generate_thumbnails(
    title: str,
    reference_image_paths: list[str],
    style_prompt: str = "",
    model: str = "",
    num_images: int = 1,
    output_dir: str = "",
) -> list[str]:
    """
    Generate thumbnails using Atlas Cloud's Nano Banana Pro with reference images.

    1. Uploads reference images to get public URLs
    2. Sends them with the prompt to the edit endpoint
    3. Polls for result and downloads the generated image
    """
    import httpx

    if not ATLASCLOUD_KEY:
        print("[thumbnail] No Atlas Cloud key, falling back to Gemini direct")
        return _fallback_gemini(title, reference_image_paths, style_prompt, num_images, output_dir)

    ref_urls = []
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

    if not ref_urls:
        print("[thumbnail] No reference images uploaded, using text-to-image mode")
        return _generate_text_only(title, style_prompt, num_images, output_dir)

    extra = f"Additional style instructions: {style_prompt}" if style_prompt else ""
    prompt_text = THUMBNAIL_PROMPT_TEMPLATE.format(title=title, extra_instructions=extra)

    out_dir = Path(output_dir) if output_dir else Path("output/thumbnails")
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_paths: list[str] = []

    for i in range(num_images):
        try:
            payload = {
                "model": "google/nano-banana-pro/edit",
                "prompt": prompt_text,
                "images": ref_urls,
                "aspect_ratio": "16:9",
                "resolution": "2k",
                "output_format": "png",
                "enable_base64_output": False,
            }

            print(f"[thumbnail] Generating thumbnail {i + 1}/{num_images} "
                  f"with {len(ref_urls)} reference(s)...")

            resp = httpx.post(
                f"{ATLAS_BASE}/model/generateImage",
                headers=_get_headers(),
                json=payload,
                timeout=30,
            )

            if resp.status_code != 200:
                print(f"[thumbnail] Generate request failed: HTTP {resp.status_code} - {resp.text[:200]}")
                continue

            raw = resp.json()
            inner = raw.get("data", raw)
            prediction_id = inner.get("id", "")

            if not prediction_id:
                outputs = inner.get("outputs", [])
                if outputs:
                    out_path = out_dir / f"thumb_{i + 1:02d}.png"
                    _download_image(outputs[0], str(out_path))
                    generated_paths.append(str(out_path))
                    continue
                print(f"[thumbnail] No prediction ID in response: {raw}")
                continue

            print(f"[thumbnail] Polling prediction {prediction_id}...")
            result = _poll_prediction(prediction_id)

            outputs = result.get("outputs", [])
            output_url = outputs[0] if outputs else ""

            if not output_url:
                output = result.get("output", "")
                if isinstance(output, list):
                    output_url = output[0] if output else ""
                elif isinstance(output, str):
                    output_url = output

            if output_url and output_url.startswith("http"):
                out_path = out_dir / f"thumb_{i + 1:02d}.png"
                _download_image(output_url, str(out_path))
                generated_paths.append(str(out_path))
                print(f"[thumbnail] Generated: {out_path.name}")
            else:
                print(f"[thumbnail] No output URL in result: {result}")

        except Exception as e:
            print(f"[thumbnail] Error generating thumbnail {i + 1}: {e}")

    return generated_paths


def _generate_text_only(
    title: str,
    style_prompt: str = "",
    num_images: int = 1,
    output_dir: str = "",
) -> list[str]:
    """Generate thumbnail without reference images (text-to-image mode)."""
    import httpx

    style = style_prompt or "modern, bold, eye-catching YouTube style"
    prompt = (
        f"Generate a YouTube thumbnail for a video titled: \"{title}\". "
        f"Style: {style}. "
        "Make it eye-catching, high contrast, bold text if appropriate, "
        "16:9 aspect ratio, professional quality."
    )

    out_dir = Path(output_dir) if output_dir else Path("output/thumbnails")
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_paths = []

    for i in range(num_images):
        try:
            payload = {
                "model": "google/nano-banana-pro/text-to-image",
                "prompt": prompt,
                "aspect_ratio": "16:9",
                "resolution": "2k",
                "output_format": "png",
                "enable_base64_output": False,
            }

            resp = httpx.post(
                f"{ATLAS_BASE}/model/generateImage",
                headers=_get_headers(),
                json=payload,
                timeout=30,
            )

            if resp.status_code != 200:
                print(f"[thumbnail] T2I request failed: HTTP {resp.status_code}")
                continue

            raw = resp.json()
            inner = raw.get("data", raw)
            prediction_id = inner.get("id", "")
            if not prediction_id:
                continue

            result = _poll_prediction(prediction_id)

            outputs = result.get("outputs", [])
            output_url = outputs[0] if outputs else ""

            if output_url and output_url.startswith("http"):
                out_path = out_dir / f"thumb_{i + 1:02d}.png"
                _download_image(output_url, str(out_path))
                generated_paths.append(str(out_path))
                print(f"[thumbnail] Generated: {out_path.name}")

        except Exception as e:
            print(f"[thumbnail] T2I error: {e}")

    return generated_paths


def _download_image(url: str, output_path: str):
    """Download an image from URL to local path."""
    import httpx

    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    Path(output_path).write_bytes(resp.content)


def _fallback_gemini(
    title: str,
    reference_image_paths: list[str],
    style_prompt: str = "",
    num_images: int = 1,
    output_dir: str = "",
) -> list[str]:
    """Fallback to direct Gemini API if Atlas Cloud key is not available."""
    from google import genai
    from google.genai import types

    if not GEMINI_KEY:
        raise ValueError("Neither ATLASCLOUD_KEY nor GEMINI_KEY is set")

    client = genai.Client(api_key=GEMINI_KEY)

    extra = f"Additional style instructions: {style_prompt}" if style_prompt else ""
    prompt_text = THUMBNAIL_PROMPT_TEMPLATE.format(title=title, extra_instructions=extra)

    contents = []
    for ref_path in reference_image_paths:
        ref = Path(ref_path)
        if not ref.exists():
            continue
        img_bytes = ref.read_bytes()
        mime = "image/png"
        if ref.suffix.lower() in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif ref.suffix.lower() == ".webp":
            mime = "image/webp"
        contents.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))

    contents.append(types.Part.from_text(text=prompt_text))

    out_dir = Path(output_dir) if output_dir else Path("output/thumbnails")
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_paths: list[str] = []
    config = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])

    for i in range(num_images):
        try:
            response = client.models.generate_content(
                model="gemini-3-pro-image",
                contents=contents,
                config=config,
            )
            if (response.candidates
                    and response.candidates[0].content
                    and response.candidates[0].content.parts):
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                        out_path = out_dir / f"thumb_{i + 1:02d}.png"
                        out_path.write_bytes(part.inline_data.data)
                        generated_paths.append(str(out_path))
                        print(f"[thumbnail] Gemini fallback generated: {out_path.name}")
                        break
        except Exception as e:
            print(f"[thumbnail] Gemini fallback error: {e}")

    return generated_paths


def generate_thumbnail_no_refs(
    title: str,
    style_description: str = "",
    model: str = "",
    output_dir: str = "",
) -> list[str]:
    """Generate a thumbnail without reference images."""
    if ATLASCLOUD_KEY:
        return _generate_text_only(title, style_description, 1, output_dir)
    return _fallback_gemini(title, [], style_description, 1, output_dir)
