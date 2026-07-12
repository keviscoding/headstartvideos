"""
AI image generation for illustration-style B-roll / avatar slots.
ERNIE Image Turbo via Atlas only (free) — never Nano Banana / FLUX here.
"""

from __future__ import annotations
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor


def generate_image(
    prompt: str,
    output_path: str,
    aspect_ratio: str = "16:9",
) -> bool:
    """Generate a single image via ERNIE. Returns True if successful."""
    from core.atlas_llm import generate_ernie_image_file, has_atlas

    if not has_atlas():
        return False
    return generate_ernie_image_file(prompt, output_path)


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

    work = []
    for p in prompts:
        sid = p["id"]
        path = str(out_dir / f"slot_{sid}.png")
        work.append((sid, p["prompt"], path, aspect_ratio))

    results: dict[int, str] = {}
    done = 0
    total = len(work)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for sid, ok in pool.map(_gen_one, work):
            done += 1
            if ok:
                results[sid] = str(out_dir / f"slot_{sid}.png")
            if progress_callback:
                progress_callback(done, total)

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
