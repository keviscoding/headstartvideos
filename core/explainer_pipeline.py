"""
Animated Explainer Pipeline -- End-to-end video generation using AI illustrations.

Produces Mack-style animated explainer documentaries:
1. Word-level alignment (faster-whisper)
2. Concept segmentation (LLM splits script into visual concepts at word boundaries)
3. Style reference generation (consistent character/art style)
4. Parallel illustration generation (FLUX Schnell $0.003/img, NB2 Lite fallback)
5. Ken Burns rendering (subtle zoom/pan on each illustration)
6. Assembly with voiceover + optional captions
"""

from __future__ import annotations
import os
import time
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR


def run_explainer_pipeline(
    script: str,
    voiceover_path: str,
    output_name: str = "explainer_video.mp4",
    style_preset: str = "default",
    niche_profile: dict | None = None,
    caption_style: str = "",
    caption_accent: str = "#00BFFF",
    caption_font_size: str = "Medium",
    caption_position: str = "Bottom",
    progress_callback=None,
) -> dict:
    """
    Run the full animated explainer pipeline.

    Returns a dict compatible with the cinematic pipeline output format:
    {
        "output_path": str,
        "job_dir": str,
        "slots": list[dict],
        "type_counts": dict,
        "timing": dict,
    }
    """
    from core.segmenter import align_script_to_audio, split_sentences
    from core.concept_segmenter import segment_into_concepts, HOOK_CUTOFF_SEC
    from core import illustration_gen
    from core.assembler import build_video

    timing: dict[str, float] = {}

    def _log(msg: str):
        print(f"[explainer] {msg}")
        if progress_callback:
            progress_callback(msg)

    # --- Job directory ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = str(OUTPUT_DIR / f"explainer_{timestamp}")
    os.makedirs(job_dir, exist_ok=True)
    assets_dir = os.path.join(job_dir, "illustrations")
    clips_dir = os.path.join(job_dir, "clips")
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(clips_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # STEP 1: Word-level alignment
    # ------------------------------------------------------------------
    _log("Step 1/6: Aligning script to audio (word-level)...")
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
    _log("Step 2/6: Segmenting into visual concepts...")
    t0 = time.time()

    niche_hint = ""
    if niche_profile:
        niche_hint = niche_profile.get("name", "")

    concepts = segment_into_concepts(
        script=script,
        all_words=all_words,
        style_preset=style_preset,
        niche_hint=niche_hint,
    )

    timing["segmentation"] = time.time() - t0
    _log(f"  {len(concepts)} concepts planned ({timing['segmentation']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 3: Style reference
    # ------------------------------------------------------------------
    _log("Step 3/6: Generating style reference...")
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
    # STEP 4: Illustration generation (premium hook + economy body)
    # ------------------------------------------------------------------
    hook_count = sum(1 for c in concepts if c.start_sec < HOOK_CUTOFF_SEC)
    body_count = len(concepts) - hook_count
    _log(f"Step 4/6: Generating {len(concepts)} illustrations "
         f"({hook_count} premium hook + {body_count} economy body)...")
    t0 = time.time()

    def _on_gen_progress(completed, total):
        _log(f"  Illustrations: {completed}/{total}")

    results = illustration_gen.generate_batch(
        concepts=concepts,
        output_dir=assets_dir,
        style_ref_path=style_ref_path,
        max_workers=8,
        progress_callback=_on_gen_progress,
        hook_cutoff_sec=HOOK_CUTOFF_SEC,
    )

    # Quality gate: disabled for now — was causing excessive false positives
    # with flat-color illustration styles, adding 30-60s of unnecessary regen.
    # TODO: re-enable with smarter check once we confirm it's actually needed.
    # for i, (concept, result) in enumerate(zip(concepts, results)):
    #     if result.success and result.image_path:
    #         if not _passes_quality_check(result.image_path):
    #             _log(f"  Concept {i}: failed quality check, regenerating...")
    #             retry = illustration_gen.generate_single_illustration(
    #                 prompt=illustration_gen.build_prompt(
    #                     concept.illustration_prompt,
    #                     concept.background_mood,
    #                     concept.has_character,
    #                 ),
    #                 output_path=result.image_path,
    #                 short_prompt=illustration_gen._build_short_prompt(
    #                     concept.illustration_prompt,
    #                     concept.background_mood,
    #                     concept.has_character,
    #                 ),
    #             )
    #             if retry.success:
    #                 results[i] = retry

    timing["illustration_gen"] = time.time() - t0
    successes = sum(1 for r in results if r.success)
    _log(f"  {successes}/{len(concepts)} illustrations generated "
         f"({timing['illustration_gen']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 5: Render static clips (no Ken Burns for explainer style)
    # ------------------------------------------------------------------
    _log("Step 5/6: Rendering static clips...")
    t0 = time.time()

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _render_one(i: int, concept, result):
        if not result.success or not os.path.exists(result.image_path):
            placeholder_path = os.path.join(assets_dir, f"placeholder_{i:04d}.png")
            _create_placeholder(placeholder_path, concept.text)
            img_path = placeholder_path
        else:
            img_path = result.image_path
        clip_path = os.path.join(clips_dir, f"clip_{i:04d}.mp4")
        ok = _render_static_clip(image_path=img_path, output_path=clip_path, duration_sec=concept.duration_sec)
        return i, clip_path, (ok and os.path.exists(clip_path))

    # Encode clips in parallel — each is an independent ffmpeg subprocess, so this
    # scales with available CPU cores instead of running one at a time.
    workers = min(len(concepts), max(2, (os.cpu_count() or 2)))
    rendered: dict[int, tuple[str, bool]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_render_one, i, c, r) for i, (c, r) in enumerate(zip(concepts, results))]
        done = 0
        for fut in as_completed(futures):
            i, clip_path, ok = fut.result()
            rendered[i] = (clip_path, ok)
            done += 1
            if done % 5 == 0 or done == len(concepts):
                _log(f"  Clips rendered: {done}/{len(concepts)}")

    clip_paths: list[str] = []
    slot_dicts: list[dict] = []
    for i, concept in enumerate(concepts):
        clip_path, ok = rendered.get(i, ("", False))
        if ok:
            clip_paths.append(clip_path)
            slot_dicts.append({
                "id": concept.id,
                "text": concept.text,
                "start_sec": concept.start_sec,
                "end_sec": concept.end_sec,
            })
        else:
            _log(f"  WARNING: Clip render failed for concept {i}")

    timing["render"] = time.time() - t0
    _log(f"  {len(clip_paths)} clips rendered ({timing['render']:.1f}s)")

    if not clip_paths:
        raise RuntimeError("No clips were rendered successfully")

    # ------------------------------------------------------------------
    # STEP 6: Assembly
    # ------------------------------------------------------------------
    _log("Step 6/6: Assembling final video...")
    t0 = time.time()

    output_path = os.path.join(job_dir, output_name)

    build_video(
        clip_paths=clip_paths,
        voiceover_path=voiceover_path,
        slots=slot_dicts,
        output_path=output_path,
        caption_style=caption_style,
        caption_accent=caption_accent,
        caption_font_size=caption_font_size,
        caption_position=caption_position,
        progress_callback=_log,
    )

    timing["assembly"] = time.time() - t0
    total_time = sum(timing.values())
    _log(f"  Assembly complete ({timing['assembly']:.1f}s)")
    _log(f"\nTotal pipeline time: {total_time:.1f}s")

    # Build mood distribution for summary
    mood_counts: dict[str, int] = {}
    for c in concepts:
        mood_counts[c.background_mood] = mood_counts.get(c.background_mood, 0) + 1

    return {
        "output_path": output_path,
        "job_dir": job_dir,
        "slots": slot_dicts,
        "type_counts": {
            "illustrations": successes,
            "placeholders": len(concepts) - successes,
            "moods": mood_counts,
        },
        "timing": timing,
    }


def _normalize_image(image_path: str, width: int = 1920, height: int = 1080):
    """Force-resize any image to exactly WxH, stretching slightly if needed."""
    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        w, h = img.size
        target_ratio = width / height
        src_ratio = w / h

        if abs(src_ratio - target_ratio) < 0.03:
            img = img.resize((width, height), Image.LANCZOS)
        else:
            if src_ratio > target_ratio:
                new_w = int(h * target_ratio)
                left = (w - new_w) // 2
                img = img.crop((left, 0, left + new_w, h))
            else:
                new_h = int(w / target_ratio)
                top = (h - new_h) // 2
                img = img.crop((0, top, w, top + new_h))
            img = img.resize((width, height), Image.LANCZOS)

        img.save(image_path)
    except Exception:
        pass


def _render_static_clip(
    image_path: str,
    output_path: str,
    duration_sec: float,
    width: int = 1920,
    height: int = 1080,
) -> bool:
    """Render a static image as a video clip — no zoom/pan, just display."""
    import subprocess

    _normalize_image(image_path, width, height)

    try:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", image_path,
            "-t", str(duration_sec),
            "-vf", f"scale={width}:{height},format=yuv420p",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-r", "24",
            "-an",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception:
        return False


def _passes_quality_check(image_path: str) -> bool:
    """Basic quality gate — reject solid-color blanks and corrupt files only."""
    try:
        if not os.path.exists(image_path):
            return False

        file_size = os.path.getsize(image_path)
        if file_size < 5000:
            return False

        from PIL import Image
        import random
        img = Image.open(image_path).convert("RGB")
        w, h = img.size

        if w < 200 or h < 200:
            return False

        # Sample pixels spread across the whole image (not just top-left)
        total = w * h
        pixels = img.load()
        step = max(1, total // 400)
        sample = []
        for idx in range(0, total, step):
            px, py = idx % w, idx // w
            sample.append(pixels[px, py])

        if len(sample) < 20:
            return True

        r_vals = [p[0] for p in sample]
        g_vals = [p[1] for p in sample]
        b_vals = [p[2] for p in sample]
        r_range = max(r_vals) - min(r_vals)
        g_range = max(g_vals) - min(g_vals)
        b_range = max(b_vals) - min(b_vals)

        # Only reject truly solid/blank images (all channels within 10)
        if r_range < 10 and g_range < 10 and b_range < 10:
            return False

        return True
    except Exception:
        return False


def _estimate_word_timestamps(script: str, voiceover_path: str) -> list[dict]:
    """Fallback: estimate word timestamps assuming ~2.5 words/second."""
    import subprocess
    import json as _json

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", voiceover_path],
            capture_output=True, text=True, timeout=10,
        )
        info = _json.loads(result.stdout)
        total_dur = float(info["format"]["duration"])
    except Exception:
        words = script.split()
        total_dur = len(words) / 2.5

    words = script.split()
    if not words:
        return []

    dur_per_word = total_dur / len(words)
    result = []
    for i, w in enumerate(words):
        result.append({
            "word": w,
            "start": i * dur_per_word,
            "end": (i + 1) * dur_per_word,
        })
    return result


def _create_placeholder(path: str, text: str):
    """Create a simple colored placeholder image when illustration gen fails."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        from core.text_overlay import _SYSTEM_FONT

        img = Image.new("RGB", (1920, 1080), color=(212, 197, 169))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(_SYSTEM_FONT, 36) if _SYSTEM_FONT else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        short_text = text[:80] + "..." if len(text) > 80 else text
        draw.text((960, 540), short_text, fill=(60, 60, 60), font=font, anchor="mm")
        img.save(path)
    except Exception:
        from PIL import Image
        img = Image.new("RGB", (1920, 1080), color=(212, 197, 169))
        img.save(path)
