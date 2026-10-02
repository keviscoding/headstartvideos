"""
Frontier Pipeline -- AI faceless video factory.

Produces Frontier-style videos using:
1. Word-level alignment (faster-whisper)
2. Concept segmentation (LLM splits script into visual concepts)
3. AI image generation (Atlas/ERNIE for stills)
4. Single-pass slideshow assembly (images + voiceover)

This is a simplified pipeline similar to animated_explainer but configured
for the Frontier style/workflow. Uses direct image slideshow rather than
Ken Burns to match the existing explainer pattern.
"""

from __future__ import annotations
import os
import time
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR


def run_frontier_pipeline(
    script: str,
    voiceover_path: str,
    output_name: str = "frontier_video.mp4",
    style_preset: str = "default",
    niche_profile: dict | None = None,
    caption_style: str = "",
    caption_accent: str = "#00BFFF",
    caption_font_size: str = "Medium",
    caption_position: str = "Bottom",
    progress_callback=None,
    lite_mode: bool = False,
    image_quality: str = "standard",
) -> dict:
    """
    Run the Frontier pipeline.

    Returns a dict compatible with the standard pipeline output format:
    {
        "output_path": str,
        "job_dir": str,
        "slots": list[dict],
        "type_counts": dict,
        "timing": dict,
    }
    """
    from core.segmenter import align_script_to_audio
    from core.concept_segmenter import segment_into_concepts, HOOK_CUTOFF_SEC
    from core import illustration_gen
    from core.assembler import build_video

    timing: dict[str, float] = {}

    def _log(msg: str):
        print(f"[frontier] {msg}")
        if progress_callback:
            progress_callback(msg)

    # --- Job directory ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = str(OUTPUT_DIR / f"frontier_{timestamp}")
    os.makedirs(job_dir, exist_ok=True)
    assets_dir = os.path.join(job_dir, "illustrations")
    os.makedirs(assets_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # STEP 1: Word-level alignment
    # ------------------------------------------------------------------
    _log("Step 1/5: Aligning script to audio (word-level)...")
    t0 = time.time()

    sentence_times, all_words = align_script_to_audio(
        script=script,
        audio_path=voiceover_path,
        model_size="base",
    )

    if not all_words:
        _log("WARNING: Whisper returned no words, falling back to estimation")
        all_words = _estimate_word_timestamps(script, voiceover_path)

    timing["alignment"] = time.time() - t0
    _log(f"  Got {len(all_words)} words, {len(sentence_times)} sentences "
         f"({timing['alignment']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 2: Concept segmentation
    # ------------------------------------------------------------------
    _log("Step 2/5: Segmenting into visual concepts...")
    t0 = time.time()

    niche_hint = ""
    if niche_profile:
        niche_hint = niche_profile.get("name", "")

    concepts = segment_into_concepts(
        script=script,
        all_words=all_words,
        style_preset=style_preset,
        niche_hint=niche_hint,
        lite_mode=lite_mode,
        hq_mode=(image_quality or "").strip().lower() in ("high", "hq", "pro"),
    )

    timing["segmentation"] = time.time() - t0
    _log(f"  {len(concepts)} concepts ({timing['segmentation']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 3: Generate style reference (optional)
    # ------------------------------------------------------------------
    _log("Step 3/5: Generating style reference...")
    t0 = time.time()

    style_ref_dir = os.path.join(job_dir, "style")
    os.makedirs(style_ref_dir, exist_ok=True)

    style_ref_path = illustration_gen.generate_style_reference(
        output_dir=style_ref_dir,
        style_preset=style_preset,
    )

    timing["style_ref"] = time.time() - t0
    _log(f"  Style ref: {'generated' if style_ref_path else 'skipped'} "
         f"({timing['style_ref']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 4: Generate AI stills for each concept
    # ------------------------------------------------------------------
    hq = (image_quality or "").strip().lower() in ("high", "hq", "pro")
    hook_count = sum(1 for c in concepts if c.start_sec < HOOK_CUTOFF_SEC)
    body_count = len(concepts) - hook_count
    
    if hq:
        _log(f"Step 4/5: Generating {len(concepts)} HQ illustrations "
             f"(GPT Image 2 Developer)...")
    else:
        _log(f"Step 4/5: Generating {len(concepts)} illustrations "
             f"({hook_count} premium hook + {body_count} economy body)...")
    t0 = time.time()

    def _on_gen_progress(completed, total):
        _log(f"  Illustrations: {completed}/{total}")

    import config as _cfg
    # Lite (trial) cooks: fewer parallel gens so one box stays healthy under queue
    default_workers = getattr(_cfg, "ILLUSTRATION_WORKERS", 16)
    lite_workers = getattr(_cfg, "ILLUSTRATION_WORKERS_LITE", 6)
    workers = lite_workers if lite_mode else default_workers
    if lite_mode:
        _log(f"  Lite mode — using {workers} illustration workers")
    
    results = illustration_gen.generate_batch(
        concepts=concepts,
        output_dir=assets_dir,
        style_ref_path=style_ref_path,
        max_workers=workers,
        progress_callback=_on_gen_progress,
        hook_cutoff_sec=HOOK_CUTOFF_SEC,
        image_quality=image_quality,
    )

    timing["illustration_gen"] = time.time() - t0
    successes = sum(1 for r in results if r.success)
    _log(f"  {successes}/{len(concepts)} illustrations generated "
         f"({timing['illustration_gen']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 5: Prepare images and assemble video
    # ------------------------------------------------------------------
    _log("Step 5/5: Assembling video...")
    t0 = time.time()

    image_paths: list[str] = []
    image_durations: list[float] = []
    slot_dicts: list[dict] = []

    failed_n = 0
    for i, (concept, result) in enumerate(zip(concepts, results)):
        if not result.success or not os.path.exists(result.image_path or ""):
            err_hint = (result.error or "unknown")[:90]
            _log(f"  WARNING: Concept {i} failed ({err_hint}) — retrying once")
            retry_desc = concept.illustration_prompt or concept.text[:80]
            if concept.section_topic and concept.section_topic.lower() not in retry_desc.lower():
                retry_desc = f"{retry_desc}. Setting: {concept.section_topic}"
            retry_path = os.path.join(assets_dir, f"illustration_{concept.id:04d}_retry.png")
            retry_prompt = illustration_gen.build_prompt(
                retry_desc,
                concept.background_mood,
                concept.has_character,
            )
            if hq:
                retry = illustration_gen._generate_hq(retry_prompt, retry_path)
            else:
                retry = illustration_gen.generate_single_illustration(
                    prompt=retry_prompt,
                    output_path=retry_path,
                    short_prompt=illustration_gen._build_short_prompt(
                        retry_desc,
                        concept.background_mood,
                        concept.has_character,
                    ),
                )
            if retry.success and os.path.exists(retry.image_path):
                results[i] = retry
                img_path = retry.image_path
            else:
                failed_n += 1
                _log(f"  WARNING: Concept {i} still failed — silent placeholder")
                placeholder_path = os.path.join(assets_dir, f"placeholder_{i:04d}.png")
                _create_placeholder(placeholder_path, concept.background_mood)
                img_path = placeholder_path
        else:
            img_path = result.image_path

        _normalize_image(img_path, 1920, 1080)
        image_paths.append(img_path)
        image_durations.append(concept.duration_sec)
        slot_dicts.append({
            "id": concept.id,
            "text": concept.text,
            "start_sec": concept.start_sec,
            "end_sec": concept.end_sec,
        })

    if failed_n:
        _log(f"  {len(image_paths)} images prepared — {failed_n} placeholders")
    else:
        _log(f"  {len(image_paths)} images prepared")

    if not image_paths:
        raise RuntimeError("No images were prepared successfully")

    output_path = os.path.join(job_dir, output_name)

    build_video(
        clip_paths=[],  # Empty for slideshow mode
        voiceover_path=voiceover_path,
        slots=slot_dicts,
        output_path=output_path,
        progress_callback=_log,
        image_paths=image_paths,
        durations=image_durations,
    )

    timing["assembly"] = time.time() - t0
    _log(f"  Assembly complete ({timing['assembly']:.1f}s)")

    # ------------------------------------------------------------------
    # Return results
    # ------------------------------------------------------------------
    type_counts = {
        "illustrations": successes,
        "placeholders": len(concepts) - successes,
    }

    total_time = sum(timing.values())
    _log(f"✓ Frontier pipeline complete ({total_time:.1f}s total)")

    # Build mood distribution for summary
    mood_counts: dict[str, int] = {}
    for c in concepts:
        mood_counts[c.background_mood] = mood_counts.get(c.background_mood, 0) + 1

    return {
        "output_path": output_path,
        "job_dir": job_dir,
        "slots": slot_dicts,
        "type_counts": type_counts,
        "timing": timing,
        "mood_counts": mood_counts,
    }


def _estimate_word_timestamps(script: str, audio_path: str) -> list[dict]:
    """
    Fallback: estimate word timestamps when Whisper fails.
    Assumes ~150 words per minute speaking rate.
    """
    import subprocess
    
    # Get audio duration
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        duration = float((probe.stdout or "0").strip() or 0)
    except Exception:
        duration = 60.0

    words = script.split()
    if not words:
        return []

    time_per_word = duration / len(words)
    result = []
    
    for i, word in enumerate(words):
        start = i * time_per_word
        end = (i + 1) * time_per_word
        result.append({
            "word": word,
            "start": start,
            "end": end,
        })
    
    return result


def _normalize_image(img_path: str, target_w: int, target_h: int) -> None:
    """Normalize image to target resolution (same as explainer_pipeline)."""
    import subprocess
    from pathlib import Path
    
    if not os.path.exists(img_path):
        return
    
    temp_out = str(Path(img_path).parent / f"_norm_{Path(img_path).name}")
    cmd = [
        "ffmpeg", "-y", "-i", img_path,
        "-vf", f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
               f"crop={target_w}:{target_h}",
        "-frames:v", "1",
        temp_out,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)
        if os.path.exists(temp_out) and os.path.getsize(temp_out) > 1000:
            os.replace(temp_out, img_path)
        elif os.path.exists(temp_out):
            os.remove(temp_out)
    except Exception as e:
        print(f"[frontier] normalize failed: {e}")
        if os.path.exists(temp_out):
            os.remove(temp_out)


def _create_placeholder(path: str, mood: str) -> None:
    """Create a colored placeholder image (same as explainer_pipeline)."""
    from PIL import Image, ImageDraw
    
    # Map mood to color
    mood_colors = {
        "warm_earth": "#D4C5A9",
        "cool_blue": "#B0C4DE",
        "nature_green": "#8FBC8F",
        "dark_serious": "#696969",
        "clean_white": "#F5F5F5",
        "golden_warm": "#DAA520",
        "dusty_rose": "#C9A9A9",
    }
    
    bg_color = mood_colors.get(mood, "#D4C5A9")
    
    img = Image.new("RGB", (1920, 1080), bg_color)
    draw = ImageDraw.Draw(img)
    
    # Draw a simple shape to indicate placeholder
    draw.rectangle([860, 490, 1060, 590], fill="#FFFFFF", outline="#000000", width=4)
    
    img.save(path)

