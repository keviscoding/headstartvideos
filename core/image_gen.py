"""
AI image generation for illustration-style B-roll.
Uses Gemini's image generation model (gemini-2.5-flash-image) to create
images from visual-attribute prompts. Falls back to stock search if
image generation is unavailable.
"""

from __future__ import annotations
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from config import GEMINI_KEY


def generate_image(
    prompt: str,
    output_path: str,
    aspect_ratio: str = "16:9",
) -> bool:
    """
    Generate a single image using Gemini's image generation model.
    Returns True if successful, False if generation failed or is unavailable.
    """
    from google import genai
    from google.genai import types

    if not GEMINI_KEY:
        return False

    client = genai.Client(api_key=GEMINI_KEY)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                ),
            ),
        )

        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                image_bytes = part.inline_data.data
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(image_bytes)
                print(f"  [image_gen] Generated: {Path(output_path).name}")
                return True

        print(f"  [image_gen] No image in response for: {prompt[:60]}")
        return False

    except Exception as e:
        print(f"  [image_gen] Error: {e}")
        return False


def _gen_one(args: tuple) -> tuple[int, bool]:
    """Worker for parallel generation."""
    slot_id, prompt, output_path, aspect_ratio = args
    success = generate_image(prompt, output_path, aspect_ratio)
    return slot_id, success


def generate_batch(
    prompts: list[dict],
    output_dir: str,
    aspect_ratio: str = "16:9",
    max_workers: int = 3,
    progress_callback=None,
) -> dict[int, str]:
    """
    Generate images for multiple slots in parallel.
    prompts: [{"id": int, "prompt": str}, ...]
    Returns: {slot_id: output_path} for successful generations.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for p in prompts:
        out_path = str(out_dir / f"gen_{p['id']:04d}.png")
        tasks.append((p["id"], p["prompt"], out_path, aspect_ratio))

    results: dict[int, str] = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_gen_one, t): t for t in tasks}
        for future in futures:
            slot_id, success = future.result()
            if success:
                results[slot_id] = str(out_dir / f"gen_{slot_id:04d}.png")
            completed += 1
            if progress_callback:
                progress_callback(completed, len(tasks))

    print(f"  [image_gen] Generated {len(results)}/{len(prompts)} images")
    return results


def build_illustration_prompt(
    narration: str,
    subject: str = "",
    era: str = "",
    tone: str = "",
    format_hint: str = "",
    niche_style: dict | None = None,
) -> str:
    """
    Build an image generation prompt from visual attributes.
    Optimized for creating illustration-style B-roll images.
    """
    parts = []

    style_desc = "photorealistic illustration"
    if niche_style:
        palette = niche_style.get("palette", "")
        grain = niche_style.get("grain", "")
        if palette:
            parts.append(f"{palette} color palette")
        if grain and grain != "clean":
            parts.append(f"{grain} texture")

    if format_hint:
        style_desc = format_hint

    parts.insert(0, f"A {style_desc}")

    if subject:
        parts.append(f"depicting {subject}")
    else:
        parts.append(f"depicting: {narration[:100]}")

    if era and era != "modern":
        parts.append(f"set in the {era}")

    if tone:
        parts.append(f"with a {tone} mood")

    parts.append("high quality, detailed, landscape orientation, 16:9 aspect ratio")

    return ", ".join(parts)
