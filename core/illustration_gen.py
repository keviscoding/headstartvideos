"""
Illustration Generator -- AI-generated illustrations for animated explainer videos.

Body / style-ref: ERNIE Image Turbo via Atlas (FREE), with GPT Image 2 fallback
(~$0.005/img) when Baidu rate-limits under parallel load.
Hook concepts (first ~30s): Nano Banana premium (~$0.028), then ERNIE→GPT if that fails.

ERNIE has a ~500 char prompt limit, so body uses compact prompts.
"""

from __future__ import annotations
import os
import re
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from core.atlas_runtime import get_atlas_key

# Compact prompts for ERNIE (~500 char hard limit). Keep style short so the
# SCENE description (setting + props) gets most of the budget.
# IMPORTANT: Do NOT put the phrase "NO text / letters / captions" in the prompt —
# ERNIE often paints that ban as a literal caption box (seen in Explainer V2).
STYLE_SHORT = (
    "Hand-drawn stick-figure cartoon, thick black outlines, flat muted colors. "
    "Pictures only — blank signs, blank books, blank screens, blank clock faces. "
    "Full subjects inside frame with 15% margin."
)

CHARACTER_SHORT = (
    "One black stick figure only: round white head, 2 dot eyes, thin body, "
    "EXACTLY 2 arms + 2 hands + 2 legs. No clothes, no mannequin, no extra people."
)

# Full prompts for premium hook Nano Banana
STYLE_PREFIX = (
    "Simple hand-drawn cartoon illustration in the style of a whiteboard "
    "animation or doodle explainer video. Thick black outlines on everything. "
    "Flat muted colors with no gradients or shading. Minimalist and clean. "
    "Show pictures of settings and objects only — never write words, letters, "
    "or digits on any surface. Books, signs, chalkboards, phones, and clocks "
    "stay blank (empty dials, no numerals). No speech bubbles with writing. "
    "Wide 16:9 landscape composition. Keep all subjects fully inside the frame "
    "with generous margins — nothing cut off or touching any edge. "
    "Fill the scene with concrete SETTING + PROPS that tell the story "
    "(environment, objects, landmarks) — never a lone figure on empty ground."
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
    "The character has NO hair, NO clothing, NO accessories, NO skin color fill, "
    "NO mannequin joints, NO second human — "
    "just pure black lines on white circle head. Never draw clothed people."
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
    """Build a full generation prompt (used for premium Nano Banana hooks)."""
    parts = [STYLE_PREFIX]
    if has_character:
        parts.append(CHARACTER_PREFIX)
    palette = BACKGROUND_PALETTES.get(background_mood, BACKGROUND_PALETTES["warm_earth"])
    parts.append(palette)
    parts.append(f"Scene: {_sanitize_scene_desc(illustration_desc)}")
    return " ".join(parts)


def _sanitize_scene_desc(illustration_desc: str) -> str:
    """Strip narration leaks and instruction-echo bait from scene prompts."""
    scene = (illustration_desc or "").strip()
    if (scene.startswith('"') and scene.endswith('"')) or (
        scene.startswith("'") and scene.endswith("'")
    ):
        scene = scene[1:-1].strip()
    # Drop lines that look like spoken narration / captions
    scene = re.sub(
        r"(?i)\b(no text|letters?|numbers?|captions?|labels?|watermarks?)\b[^.]{0,40}",
        "",
        scene,
    )
    scene = re.sub(r"\s+", " ", scene).strip(" .;")
    return scene


def looks_like_text_slide(path: str) -> bool:
    """Heuristic: caption box with horizontal ink bands (V2 failure modes)."""
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        g = Image.open(path).convert("L").resize((320, 180))
    except Exception:
        return False
    w, h = g.size
    crop = g.crop((int(w * 0.12), int(h * 0.18), int(w * 0.88), int(h * 0.82)))
    cw, ch = crop.size
    pixels = list(crop.getdata())
    if not pixels:
        return False
    bright = sum(1 for p in pixels if p >= 210) / len(pixels)
    dark = sum(1 for p in pixels if p <= 70) / len(pixels)
    mid = sum(1 for p in pixels if 40 <= p <= 200) / len(pixels)

    def _banded(ink_thresh_lo: int, ink_thresh_hi: int) -> bool:
        row_ink = []
        for y in range(ch):
            row = pixels[y * cw : (y + 1) * cw]
            row_ink.append(
                sum(1 for p in row if ink_thresh_lo <= p <= ink_thresh_hi) / cw
            )
        textish = sum(1 for v in row_ink if 0.04 <= v <= 0.55)
        if textish < max(6, ch // 10):
            return False
        flips = 0
        prev = row_ink[0] >= 0.04
        for v in row_ink[1:]:
            cur = v >= 0.04
            if cur != prev:
                flips += 1
                prev = cur
        return flips >= 8

    # Pale caption card with dark lettering
    if bright >= 0.22 and dark >= 0.035 and _banded(0, 90):
        return True
    # Dark scene with a white instruction/caption box (V2 sc12_022)
    if bright >= 0.08 and mid >= 0.15 and _banded(0, 100):
        # Extra: require a contiguous bright blob (the box)
        bright_rows = 0
        for y in range(ch):
            row = pixels[y * cw : (y + 1) * cw]
            if sum(1 for p in row if p >= 200) / cw >= 0.25:
                bright_rows += 1
        if bright_rows >= max(8, ch // 8) and _banded(0, 110):
            return True
    return False


def looks_like_empty_prop_scene(path: str) -> bool:
    """True when the still is basically a lone stick figure / blank (V2 placeholders)."""
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError:
        return False
    try:
        im = Image.open(path).convert("RGB").resize((240, 135))
    except Exception:
        return False
    edges = im.convert("L").filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    # Very low edge energy ⇒ empty / placeholder
    return float(stat.mean[0]) < 6.5


def _build_short_prompt(
    illustration_desc: str,
    background_mood: str = "warm_earth",
    has_character: bool = True,
) -> str:
    """Build a compact prompt for ERNIE (hard ~500 char limit).

    Scene description is prioritized — style/character are shortened first.
    """
    palette = BACKGROUND_PALETTES_SHORT.get(background_mood, "Warm beige background.")
    scene = _sanitize_scene_desc(illustration_desc)

    prefix_parts = [STYLE_SHORT]
    if has_character:
        prefix_parts.append(CHARACTER_SHORT)
    prefix_parts.append(palette)
    prefix = " ".join(prefix_parts)
    # Reserve budget for scene; if over limit, trim style before scene.
    budget = 490
    if len(prefix) + 1 + len(scene) > budget:
        # Prefer dropping character line over gutting the scene
        prefix_parts = [STYLE_SHORT, palette]
        prefix = " ".join(prefix_parts)
    room = max(80, budget - len(prefix) - 1)
    if len(scene) > room:
        scene = scene[: room - 1].rsplit(" ", 1)[0] + "…"
    return f"{prefix} {scene}".strip()


def generate_single_illustration(
    prompt: str,
    output_path: str,
    style_ref_path: str | None = None,
    short_prompt: str | None = None,
) -> GeneratedIllustration:
    """Body/style stills: throttled ERNIE, then GPT Image 2 (cheap) fallback."""
    t0 = time.time()
    if not get_atlas_key():
        return GeneratedIllustration(
            concept_id=-1, image_path="", model_used="",
            generation_time_sec=0, success=False,
            error="ATLASCLOUD_KEY not set",
        )

    ernie_prompt = short_prompt or prompt
    result = _generate_via_ernie_turbo(ernie_prompt, output_path)
    if result.success:
        return result

    # Prefer GPT Image 2 Developer over Nano Banana lite — ~$0.005/img vs ~$0.028.
    from core.atlas_llm import generate_hq_image_file

    fallback_prompt = (prompt or ernie_prompt or "")[:1200]
    if generate_hq_image_file(fallback_prompt, output_path):
        print(
            f"  [illustration_gen] ERNIE failed ({(result.error or '')[:80]}) — "
            f"used GPT Image 2 fallback → {Path(output_path).name}"
        )
        return GeneratedIllustration(
            concept_id=-1, image_path=output_path,
            model_used="fallback/gpt-image-2-developer",
            generation_time_sec=time.time() - t0, success=True,
        )

    return GeneratedIllustration(
        concept_id=-1, image_path="", model_used="",
        generation_time_sec=time.time() - t0, success=False,
        error=result.error or "ERNIE + GPT Image 2 fallback failed",
    )


def _generate_via_ernie_turbo(prompt: str, output_path: str) -> GeneratedIllustration:
    """Generate via shared throttled ERNIE helper (retries + concurrency cap)."""
    from core.atlas_llm import generate_ernie_image_file_detailed

    t0 = time.time()
    ok, err = generate_ernie_image_file_detailed(prompt, output_path)
    if ok:
        return GeneratedIllustration(
            concept_id=-1, image_path=output_path,
            model_used="ernie-turbo",
            generation_time_sec=time.time() - t0, success=True,
        )
    return GeneratedIllustration(
        concept_id=-1, image_path="", model_used="",
        generation_time_sec=time.time() - t0, success=False,
        error=err or "ERNIE Turbo failed",
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
    """Premium hook stills via Atlas Nano Banana (was Google Gemini image)."""
    from core.atlas_llm import generate_image_file, has_atlas, ATLAS_PREMIUM_IMAGE_MODEL

    t0 = time.time()
    if has_atlas() and generate_image_file(
        prompt,
        output_path,
        model=ATLAS_PREMIUM_IMAGE_MODEL,
        aspect_ratio="16:9",
    ):
        return GeneratedIllustration(
            concept_id=-1,
            image_path=output_path,
            model_used=f"premium/{ATLAS_PREMIUM_IMAGE_MODEL}",
            generation_time_sec=time.time() - t0,
            success=True,
        )

    # If Nano Banana fails, fall back to free ERNIE (never FLUX)
    return generate_single_illustration(prompt, output_path, style_ref_path)


def _generate_hq(prompt: str, output_path: str) -> GeneratedIllustration:
    """All stills via GPT Image 2 Developer (paid HQ cooks)."""
    from core.atlas_llm import generate_hq_image_file, has_atlas

    t0 = time.time()
    if has_atlas() and generate_hq_image_file(prompt, output_path):
        return GeneratedIllustration(
            concept_id=-1,
            image_path=output_path,
            model_used="hq/gpt-image-2-developer",
            generation_time_sec=time.time() - t0,
            success=True,
        )
    # Fall back to ERNIE so a single HQ failure doesn't blank the slot
    return generate_single_illustration(prompt, output_path)


def generate_batch(
    concepts: list,
    output_dir: str,
    style_ref_path: str = "",
    max_workers: int = 8,
    progress_callback=None,
    hook_cutoff_sec: float = 30.0,
    image_quality: str = "standard",
) -> list[GeneratedIllustration]:
    """Generate illustrations for all concepts in parallel.

    standard: Nano Banana hooks + ERNIE body (GPT Image 2 if ERNIE fails).
    high: GPT Image 2 Developer for every still (Pro HQ cooks).
    """
    from core.concept_segmenter import BACKGROUND_MOODS

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    hq = (image_quality or "standard").strip().lower() in ("high", "hq", "pro")

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

        if hq:
            result = _generate_hq(full_prompt, out_path)
        else:
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

        # V2 audit: caption boxes + instruction-echo text slides → one regen
        if result.success and result.image_path and (
            looks_like_text_slide(result.image_path)
            or looks_like_empty_prop_scene(result.image_path)
        ):
            topic = (getattr(concept, "section_topic", "") or "").strip()
            scene = _sanitize_scene_desc(concept.illustration_prompt or "")
            if topic and topic.lower() not in scene.lower():
                scene = f"{scene}. Setting: {topic}".strip(". ")
            if not scene:
                scene = topic or "stick figure in a concrete place with 3 props"
            retry_desc = (
                f"{scene} Include tunnel/earth/moon props from the setting — not empty ground."
            )
            retry_path = out_path.replace(".png", "_fixtext.png")
            if hq:
                retry = _generate_hq(build_prompt(retry_desc, concept.background_mood, concept.has_character), retry_path)
            else:
                retry_short = (
                    f"{STYLE_SHORT} {CHARACTER_SHORT if concept.has_character else ''} "
                    f"{BACKGROUND_PALETTES_SHORT.get(concept.background_mood, 'Warm beige background.')} "
                    f"{retry_desc}"
                ).strip()
                retry = generate_single_illustration(
                    prompt=retry_short,
                    output_path=retry_path,
                    short_prompt=retry_short[:490],
                )
            if retry.success and retry.image_path and not looks_like_text_slide(retry.image_path):
                try:
                    import shutil
                    shutil.copyfile(retry.image_path, out_path)
                    retry.image_path = out_path
                except Exception:
                    pass
                result = retry

        result.concept_id = concept.id
        return concept.id, result

    hook_n = sum(1 for c in concepts if c.start_sec < hook_cutoff_sec)
    body_n = total - hook_n
    mode_label = "HQ GPT-Image-2-Dev" if hq else f"{hook_n} premium hook + {body_n} economy body"
    print(f"[illustration_gen] Generating {total} illustrations "
          f"(workers={max_workers}) | {mode_label}")
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
    # GPT Image 2 Developer @ low/1536x864 ≈ $0.005 (Atlas promo often ~$0.0049)
    # Nano Banana lite hooks ≈ $0.028. ERNIE body ≈ $0.
    gpt_n = sum(v for k, v in model_counts.items() if "gpt-image" in k or "hq/" in k)
    premium_n = sum(
        v for k, v in model_counts.items()
        if ("premium" in k or "nano-banana" in k) and "gpt-image" not in k
    )
    est = gpt_n * 0.005 + premium_n * 0.028
    print(f"[illustration_gen] Batch complete: {successes}/{total} succeeded "
          f"in {elapsed:.1f}s | Models: {model_counts} | Est. cost: ${est:.3f}")

    ordered = [results_map[c.id] for c in concepts if c.id in results_map]
    return ordered
