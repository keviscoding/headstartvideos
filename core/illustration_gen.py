"""
Illustration Generator -- AI-generated illustrations for animated explainer videos.

Primary: ERNIE Image Turbo via Atlas Cloud (FREE, native 16:9 @ 1376x768).
Fallback 1: FLUX Schnell via Atlas Cloud ($0.003/image, fast + reliable).
Fallback 2: Nano Banana 2 Lite via Gemini API ($0.034/image).

ERNIE has a ~500 char prompt limit before upstream errors, so we use
compact prompts for ERNIE and full-length prompts for FLUX/Gemini.
"""

from __future__ import annotations
import os
import time
import base64
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from config import GEMINI_KEY, ATLASCLOUD_KEY

_IMAGE_MODELS = [
    "gemini-3.1-flash-lite-image",
    "gemini-3.1-flash-image",
]

# Compact prompts for ERNIE (under 500 chars total with scene desc)
STYLE_SHORT = (
    "Simple hand-drawn cartoon, thick black outlines, flat muted colors, "
    "minimalist, no text, no letters, no words. "
    "IMPORTANT: Keep all subjects fully inside the frame with large margins. "
    "Nothing touching or cut off at any edge. Leave 15% padding on all sides."
)

CHARACTER_SHORT = (
    "Simple black stick figure, round white head, dot eyes, "
    "thin line body. Exactly TWO arms, TWO hands, TWO legs. "
    "Full body visible, not cropped."
)

# Full prompts for FLUX/Gemini (longer, more detailed)
STYLE_PREFIX = (
    "Simple hand-drawn cartoon illustration in the style of a whiteboard "
    "animation or doodle explainer video. Thick black outlines on everything. "
    "Flat muted colors with no gradients or shading. Minimalist and clean. "
    "CRITICAL: Absolutely zero text, zero letters, zero words, zero numbers, "
    "zero labels, zero captions, zero watermarks anywhere in the image. "
    "Do not write on books, scrolls, signs, or any objects. Leave all surfaces blank. "
    "Wide 16:9 landscape composition. Keep all subjects fully inside the frame "
    "with generous margins — nothing cut off or touching any edge."
)

CHARACTER_PREFIX = (
    "The main character is ALWAYS drawn EXACTLY the same way every single time: "
    "a simple black stick figure with very thin straight black lines for arms and legs. "
    "EXACTLY two arms, two hands, two legs — never three or more of any limb. "
    "Small oval black hands, a perfectly round white circle head with a thick black outline, "
    "two small identical round black dots for eyes symmetrically placed in the center "
    "of the face (both eyes must be the same size and shape), "
    "and a small simple curved line for mouth. "
    "The head is about 1/4 of body height. The body is a single straight vertical black line. "
    "Arms are angled lines from the middle of the body line. "
    "Legs are two angled lines from the bottom of the body line. "
    "The character has NO hair, NO clothing, NO accessories, NO skin color fill — "
    "just pure black lines on white circle head."
)

BACKGROUND_PALETTES = {
    "warm_earth": "Background is warm beige (#D4C5A9) with subtle tan tones.",
    "cool_blue": "Background is muted steel blue (#7B9BAA) with gray undertones.",
    "nature_green": "Background is muted olive green (#8B9A6B) with earthy tones.",
    "dark_serious": "Background is dark charcoal brown (#3A3232) with somber tones.",
    "clean_white": "Background is clean off-white (#F2F0EB) with light gray.",
    "golden_warm": "Background is warm golden amber (#C4A35A) with rich warmth.",
    "dusty_rose": "Background is muted dusty rose (#B8938A) with gentle warmth.",
}

BACKGROUND_PALETTES_SHORT = {
    "warm_earth": "Warm beige background.",
    "cool_blue": "Muted steel blue background.",
    "nature_green": "Muted olive green background.",
    "dark_serious": "Dark charcoal brown background.",
    "clean_white": "Clean off-white background.",
    "golden_warm": "Warm golden amber background.",
    "dusty_rose": "Muted dusty rose background.",
}


@dataclass
class GeneratedIllustration:
    concept_id: int
    image_path: str
    model_used: str
    generation_time_sec: float
    success: bool
    error: str = ""


def build_prompt(
    illustration_desc: str,
    background_mood: str = "warm_earth",
    has_character: bool = True,
) -> str:
    """Build a full generation prompt (used for FLUX/Gemini)."""
    parts = [STYLE_PREFIX]
    if has_character:
        parts.append(CHARACTER_PREFIX)
    palette = BACKGROUND_PALETTES.get(background_mood, BACKGROUND_PALETTES["warm_earth"])
    parts.append(palette)
    parts.append(f"Scene: {illustration_desc}")
    return " ".join(parts)


def _build_short_prompt(
    illustration_desc: str,
    background_mood: str = "warm_earth",
    has_character: bool = True,
) -> str:
    """Build a compact prompt for ERNIE (under ~450 chars)."""
    parts = [STYLE_SHORT]
    if has_character:
        parts.append(CHARACTER_SHORT)
    palette = BACKGROUND_PALETTES_SHORT.get(background_mood, "Warm beige background.")
    parts.append(palette)
    parts.append(illustration_desc)
    return " ".join(parts)


def _crop_to_16_9(image_path: str):
    """Crop a square image to 16:9 by trimming top and bottom."""
    try:
        from PIL import Image
        img = Image.open(image_path)
        w, h = img.size
        target_h = int(w * 9 / 16)
        if h > target_h:
            top = (h - target_h) // 2
            img = img.crop((0, top, w, top + target_h))
            img.save(image_path)
    except Exception:
        pass


def generate_single_illustration(
    prompt: str,
    output_path: str,
    style_ref_path: str | None = None,
    short_prompt: str | None = None,
) -> GeneratedIllustration:
    """
    Generate a single illustration.
    Chain: ERNIE Turbo (free) -> FLUX Schnell ($0.003) -> Gemini NB2 Lite ($0.034).
    """
    if ATLASCLOUD_KEY:
        # Try ERNIE Image Turbo first (FREE, native 16:9)
        ernie_prompt = short_prompt or prompt
        result = _generate_via_ernie_turbo(ernie_prompt, output_path)
        if result.success:
            return result

        # Fallback to FLUX Schnell ($0.003, fast + reliable)
        result = _generate_via_flux_schnell(prompt, output_path)
        if result.success:
            return result

    # Last resort: Gemini Nano Banana
    from google import genai
    from google.genai import types

    if not GEMINI_KEY:
        return GeneratedIllustration(
            concept_id=-1, image_path="", model_used="",
            generation_time_sec=0, success=False, error="No API keys available"
        )

    client = genai.Client(api_key=GEMINI_KEY)
    contents = []
    if style_ref_path and os.path.exists(style_ref_path):
        with open(style_ref_path, "rb") as f:
            ref_bytes = f.read()
        contents.append(types.Part.from_bytes(data=ref_bytes, mime_type="image/png"))
        contents.append(types.Part.from_text(
            text=f"Match the art style of this reference image exactly. {prompt}"
        ))
    else:
        contents.append(types.Part.from_text(text=prompt))

    for model_name in _IMAGE_MODELS:
        t0 = time.time()
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                ),
            )
            if not response.candidates:
                continue
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(part.inline_data.data)
                    return GeneratedIllustration(
                        concept_id=-1, image_path=output_path,
                        model_used=model_name,
                        generation_time_sec=time.time() - t0, success=True,
                    )
        except Exception as e:
            err = str(e)
            if "not found" in err.lower() or "not supported" in err.lower():
                continue
            print(f"  [illustration_gen] {model_name} failed: {err[:100]}")

    return GeneratedIllustration(
        concept_id=-1, image_path="", model_used="",
        generation_time_sec=0, success=False, error="All models failed"
    )


def _generate_via_ernie_turbo(prompt: str, output_path: str) -> GeneratedIllustration:
    """Generate via ERNIE Image Turbo on Atlas Cloud — FREE, native 16:9."""
    import httpx

    ATLAS_BASE = "https://api.atlascloud.ai/api/v1"
    t0 = time.time()

    try:
        resp = httpx.post(
            f"{ATLAS_BASE}/model/generateImage",
            headers={
                "Authorization": f"Bearer {ATLASCLOUD_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "baidu/ERNIE-Image-Turbo/text-to-image",
                "prompt": prompt,
                "size": "1376x768",
                "n": 1,
                "use_pe": True,
                "num_inference_steps": 8,
                "guidance_scale": 1,
            },
            timeout=30,
        )
        data = resp.json()
        pred_id = None
        if "data" in data and isinstance(data["data"], dict):
            pred_id = data["data"].get("id")
        if not pred_id:
            pred_id = data.get("id") or data.get("prediction_id")
        if not pred_id:
            return GeneratedIllustration(
                concept_id=-1, image_path="", model_used="",
                generation_time_sec=time.time() - t0, success=False,
                error="No prediction ID from ERNIE"
            )

        for _ in range(25):
            time.sleep(2)
            poll = httpx.get(
                f"{ATLAS_BASE}/model/prediction/{pred_id}",
                headers={"Authorization": f"Bearer {ATLASCLOUD_KEY}"},
                timeout=15,
            )
            inner = poll.json().get("data", poll.json())
            status = str(inner.get("status", "")).lower()

            if status in ("succeeded", "completed", "done"):
                outputs = inner.get("outputs") or inner.get("output") or []
                if isinstance(outputs, list) and outputs:
                    img_url = outputs[0]
                elif isinstance(outputs, str):
                    img_url = outputs
                else:
                    break

                img_resp = httpx.get(img_url, timeout=30, follow_redirects=True)
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(img_resp.content)

                return GeneratedIllustration(
                    concept_id=-1, image_path=output_path,
                    model_used="ernie-turbo",
                    generation_time_sec=time.time() - t0, success=True,
                )

            if status in ("failed", "error", "cancelled"):
                break

    except Exception as e:
        print(f"  [illustration_gen] ERNIE Turbo failed: {e}")

    return GeneratedIllustration(
        concept_id=-1, image_path="", model_used="",
        generation_time_sec=time.time() - t0, success=False,
        error="ERNIE Turbo failed"
    )


def _generate_via_flux_schnell(prompt: str, output_path: str) -> GeneratedIllustration:
    """Fallback: FLUX Schnell on Atlas Cloud — $0.003/image, fast + reliable."""
    import httpx

    ATLAS_BASE = "https://api.atlascloud.ai/api/v1"
    t0 = time.time()

    try:
        resp = httpx.post(
            f"{ATLAS_BASE}/model/generateImage",
            headers={
                "Authorization": f"Bearer {ATLASCLOUD_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "black-forest-labs/flux-schnell",
                "prompt": prompt,
            },
            timeout=30,
        )
        data = resp.json()
        pred_id = None
        if "data" in data and isinstance(data["data"], dict):
            pred_id = data["data"].get("id")
        if not pred_id:
            pred_id = data.get("id") or data.get("prediction_id")
        if not pred_id:
            return GeneratedIllustration(
                concept_id=-1, image_path="", model_used="",
                generation_time_sec=time.time() - t0, success=False,
                error="No prediction ID from FLUX"
            )

        for _ in range(20):
            time.sleep(1.5)
            poll = httpx.get(
                f"{ATLAS_BASE}/model/prediction/{pred_id}",
                headers={"Authorization": f"Bearer {ATLASCLOUD_KEY}"},
                timeout=15,
            )
            inner = poll.json().get("data", poll.json())
            status = str(inner.get("status", "")).lower()

            if status in ("succeeded", "completed", "done"):
                outputs = inner.get("output") or inner.get("outputs") or []
                if isinstance(outputs, list) and outputs:
                    img_url = outputs[0]
                elif isinstance(outputs, str):
                    img_url = outputs
                else:
                    break

                img_resp = httpx.get(img_url, timeout=30, follow_redirects=True)
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(img_resp.content)

                _crop_to_16_9(output_path)

                return GeneratedIllustration(
                    concept_id=-1, image_path=output_path,
                    model_used="flux-schnell",
                    generation_time_sec=time.time() - t0, success=True,
                )

            if status in ("failed", "error", "cancelled"):
                break

    except Exception as e:
        print(f"  [illustration_gen] FLUX Schnell failed: {e}")

    return GeneratedIllustration(
        concept_id=-1, image_path="", model_used="",
        generation_time_sec=time.time() - t0, success=False,
        error="FLUX Schnell failed"
    )


def _poll_atlas_prediction(
    prediction_id: str, output_path: str, t0: float, timeout: int = 60
) -> GeneratedIllustration:
    """Poll Atlas Cloud for a completed prediction."""
    import httpx

    ATLAS_BASE = "https://api.atlascloud.ai/api/v1"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            resp = httpx.get(
                f"{ATLAS_BASE}/model/prediction/{prediction_id}",
                headers={"Authorization": f"Bearer {ATLASCLOUD_KEY}"},
                timeout=15,
            )
            data = resp.json().get("data", resp.json())
            status = str(data.get("status", "")).lower()

            if status in ("succeeded", "completed", "done"):
                outputs = data.get("output", data.get("outputs", []))
                if isinstance(outputs, list) and outputs:
                    img_url = outputs[0]
                elif isinstance(outputs, str):
                    img_url = outputs
                else:
                    break

                img_resp = httpx.get(img_url, timeout=30, follow_redirects=True)
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(img_resp.content)
                return GeneratedIllustration(
                    concept_id=-1, image_path=output_path,
                    model_used="atlas/nano-banana-2-lite",
                    generation_time_sec=time.time() - t0, success=True,
                )

            if status in ("failed", "error", "cancelled"):
                break

        except Exception:
            pass

        time.sleep(2)

    return GeneratedIllustration(
        concept_id=-1, image_path="", model_used="",
        generation_time_sec=time.time() - t0, success=False,
        error="Atlas prediction polling timed out"
    )


def generate_style_reference(
    output_dir: str,
    style_preset: str = "default",
) -> str:
    """Generate a style reference image for consistent character look."""
    ref_path = os.path.join(output_dir, "style_reference.png")

    if os.path.exists(ref_path):
        return ref_path

    prompt = (
        f"{STYLE_PREFIX} {CHARACTER_PREFIX} "
        "The stick figure is standing in the center of the frame, "
        "facing slightly to the right, with a neutral curious expression "
        "(small 'o' mouth, slightly raised eyebrow dots). "
        "The background is a warm beige (#D4C5A9). "
        "The character takes up about 40% of the frame height. "
        "This is a character reference sheet showing the art style."
    )

    short = (
        f"{STYLE_SHORT} {CHARACTER_SHORT} Warm beige background. "
        "The stick figure stands in the center with a curious expression."
    )

    result = generate_single_illustration(prompt, ref_path, short_prompt=short)
    if result.success:
        print(f"[illustration_gen] Style reference generated: {ref_path}")
        return ref_path

    print(f"[illustration_gen] Style reference generation failed, proceeding without")
    return ""


def _generate_premium(prompt: str, output_path: str, style_ref_path: str | None = None) -> GeneratedIllustration:
    """Generate via Gemini NB2 Lite (premium quality for hook zone)."""
    from google import genai
    from google.genai import types

    if not GEMINI_KEY:
        return generate_single_illustration(prompt, output_path, style_ref_path)

    client = genai.Client(api_key=GEMINI_KEY)
    contents = []
    if style_ref_path and os.path.exists(style_ref_path):
        with open(style_ref_path, "rb") as f:
            ref_bytes = f.read()
        contents.append(types.Part.from_bytes(data=ref_bytes, mime_type="image/png"))
        contents.append(types.Part.from_text(
            text=f"Match the art style of this reference image exactly. {prompt}"
        ))
    else:
        contents.append(types.Part.from_text(text=prompt))

    for model_name in _IMAGE_MODELS:
        t0 = time.time()
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                ),
            )
            if not response.candidates:
                continue
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(part.inline_data.data)
                    return GeneratedIllustration(
                        concept_id=-1, image_path=output_path,
                        model_used=f"premium/{model_name}",
                        generation_time_sec=time.time() - t0, success=True,
                    )
        except Exception as e:
            err = str(e)
            if "not found" in err.lower() or "not supported" in err.lower():
                continue
            print(f"  [illustration_gen] premium {model_name} failed: {err[:100]}")

    return generate_single_illustration(prompt, output_path, style_ref_path)


def generate_batch(
    concepts: list,
    output_dir: str,
    style_ref_path: str = "",
    max_workers: int = 8,
    progress_callback=None,
    hook_cutoff_sec: float = 30.0,
) -> list[GeneratedIllustration]:
    """Generate illustrations for all concepts in parallel.

    Hook concepts (start_sec < hook_cutoff_sec) use premium Gemini NB2 Lite.
    Body concepts use the ERNIE→FLUX→Gemini fallback chain.
    """
    from core.concept_segmenter import BACKGROUND_MOODS

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    total = len(concepts)
    results_map: dict[int, GeneratedIllustration] = {}
    completed = 0
    lock = threading.Lock()

    def _generate_one(concept) -> tuple[int, GeneratedIllustration]:
        full_prompt = build_prompt(
            illustration_desc=concept.illustration_prompt,
            background_mood=concept.background_mood,
            has_character=concept.has_character,
        )
        out_path = os.path.join(output_dir, f"illustration_{concept.id:04d}.png")

        is_hook = concept.start_sec < hook_cutoff_sec

        if is_hook:
            result = _generate_premium(
                prompt=full_prompt,
                output_path=out_path,
                style_ref_path=style_ref_path if style_ref_path else None,
            )
        else:
            short_prompt = _build_short_prompt(
                illustration_desc=concept.illustration_prompt,
                background_mood=concept.background_mood,
                has_character=concept.has_character,
            )
            result = generate_single_illustration(
                prompt=full_prompt,
                output_path=out_path,
                style_ref_path=style_ref_path if style_ref_path else None,
                short_prompt=short_prompt,
            )

        result.concept_id = concept.id
        return concept.id, result

    hook_n = sum(1 for c in concepts if c.start_sec < hook_cutoff_sec)
    body_n = total - hook_n
    print(f"[illustration_gen] Generating {total} illustrations "
          f"(workers={max_workers}) | {hook_n} premium (hook) + {body_n} economy (body)")
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_generate_one, c): c.id
            for c in concepts
        }

        for future in as_completed(futures):
            cid, result = future.result()
            results_map[cid] = result
            with lock:
                completed += 1
                status = f"OK ({result.model_used})" if result.success else "FAIL"
                if completed % 5 == 0 or completed == total:
                    elapsed = time.time() - t0
                    print(f"  [illustration_gen] {completed}/{total} done "
                          f"({elapsed:.1f}s elapsed)")
                if progress_callback:
                    progress_callback(completed, total)

    elapsed = time.time() - t0
    model_counts: dict[str, int] = {}
    for r in results_map.values():
        if r.success:
            model_counts[r.model_used] = model_counts.get(r.model_used, 0) + 1

    successes = sum(model_counts.values())
    flux_cost = model_counts.get("flux-schnell", 0) * 0.003
    premium_cost = sum(v for k, v in model_counts.items() if "premium" in k) * 0.034
    total_cost = flux_cost + premium_cost
    print(f"[illustration_gen] Batch complete: {successes}/{total} succeeded "
          f"in {elapsed:.1f}s | Models: {model_counts} | Est. cost: ${total_cost:.3f}")

    ordered = [results_map[c.id] for c in concepts if c.id in results_map]
    return ordered
