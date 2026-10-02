"""
Frontier Pipeline -- AI faceless video factory.

Produces Frontier-style videos using:
1. Word-level alignment (faster-whisper)
2. Concept segmentation (LLM splits script into visual concepts)
3. AI image generation (Atlas/ERNIE for stills)
4. Ken Burns rendering (subtle zoom/pan on each still)
5. Assembly with voiceover + optional captions

This is a simplified pipeline similar to animated_explainer but configured
for the Frontier style/workflow.
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
    from core.segmenter import align_script_to_audio, split_sentences
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
        niche_hint=niche_hint,
        target_clip_duration=4.0,
    )

    timing["segmentation"] = time.time() - t0
    _log(f"  {len(concepts)} concepts ({timing['segmentation']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 3: Generate AI stills for each concept
    # ------------------------------------------------------------------
    _log("Step 3/6: Generating AI stills (Frontier style)...")
    t0 = time.time()

    # Determine which concepts are hook (first few seconds)
    hook_concepts = [c for c in concepts if c.start_sec <= HOOK_CUTOFF_SEC]
    body_concepts = [c for c in concepts if c.start_sec > HOOK_CUTOFF_SEC]

    _log(f"  {len(hook_concepts)} hook concepts, {len(body_concepts)} body concepts")

    # Generate style reference for consistency
    style_ref_path = None
    if not lite_mode and image_quality == "high":
        try:
            style_ref_path = illustration_gen.generate_style_reference(
                script=script,
                output_dir=assets_dir,
                style_preset=style_preset,
            )
            if style_ref_path:
                _log(f"  Style reference created: {Path(style_ref_path).name}")
        except Exception as e:
            _log(f"  Style reference skipped: {e}")

    # Generate images for all concepts
    all_concepts = hook_concepts + body_concepts
    slots = []
    
    for i, concept in enumerate(all_concepts):
        _log(f"  Generating image {i+1}/{len(all_concepts)}: {concept.text[:60]}...")
        
        try:
            img_path = illustration_gen.generate_illustration(
                concept=concept,
                output_dir=assets_dir,
                index=i,
                style_ref_path=style_ref_path,
                style_preset=style_preset,
                is_hook=(concept in hook_concepts),
                lite_mode=lite_mode,
                image_quality=image_quality,
            )
            
            if img_path and os.path.exists(img_path):
                slots.append({
                    "concept": concept,
                    "asset_path": img_path,
                    "asset_type": "ai_image",
                    "start_sec": concept.start_sec,
                    "end_sec": concept.end_sec,
                    "duration": concept.end_sec - concept.start_sec,
                })
            else:
                _log(f"  WARNING: Image generation failed for concept {i}")
        except Exception as e:
            _log(f"  ERROR generating image {i}: {e}")

    timing["image_generation"] = time.time() - t0
    _log(f"  Generated {len(slots)} images ({timing['image_generation']:.1f}s)")

    if not slots:
        raise RuntimeError("No images were generated — cannot proceed")

    # ------------------------------------------------------------------
    # STEP 4: Ken Burns rendering
    # ------------------------------------------------------------------
    _log("Step 4/6: Rendering Ken Burns effects...")
    t0 = time.time()

    from core.ken_burns import render_ken_burns_clip

    for i, slot in enumerate(slots):
        try:
            clip_path = os.path.join(clips_dir, f"clip_{i:03d}.mp4")
            render_ken_burns_clip(
                image_path=slot["asset_path"],
                output_path=clip_path,
                duration=slot["duration"],
                style="subtle",
            )
            slot["clip_path"] = clip_path
            _log(f"  Rendered clip {i+1}/{len(slots)}")
        except Exception as e:
            _log(f"  ERROR rendering clip {i}: {e}")
            raise

    timing["ken_burns"] = time.time() - t0
    _log(f"  Ken Burns complete ({timing['ken_burns']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 5: Assembly
    # ------------------------------------------------------------------
    _log("Step 5/6: Assembling final video...")
    t0 = time.time()

    output_path = os.path.join(job_dir, output_name)

    build_video(
        slots=slots,
        voiceover_path=voiceover_path,
        output_path=output_path,
        all_words=all_words,
        caption_style=caption_style,
        caption_accent=caption_accent,
        caption_font_size=caption_font_size,
        caption_position=caption_position,
    )

    timing["assembly"] = time.time() - t0
    _log(f"  Assembly complete ({timing['assembly']:.1f}s)")

    # ------------------------------------------------------------------
    # Return results
    # ------------------------------------------------------------------
    type_counts = {
        "ai_image": len([s for s in slots if s.get("asset_type") == "ai_image"]),
    }

    total_time = sum(timing.values())
    _log(f"✓ Frontier pipeline complete ({total_time:.1f}s total)")

    return {
        "output_path": output_path,
        "job_dir": job_dir,
        "slots": slots,
        "type_counts": type_counts,
        "timing": timing,
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
