"""
Frontier Pipeline -- 1:1 Whop Frontier port for Channel Recipe.

Produces Frontier-style videos matching Whop Frontier visual output:
1. Word-level alignment (faster-whisper) for SRT timing
2. Atlas gpt-image-2 AI stills with scene prompts
3. Pexels stock video/photo b-roll filling motion gaps
4. Word-locked motion plan - stills own spoken spans
5. Ken Burns zooms on stills/photos
6. ASS kinetic captions with accent color flash
7. Color grade + dust overlay + vignette matching Frontier look

This is the full Frontier experience, not a slideshow.
"""

from __future__ import annotations
import os
import time
import json
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR


def run_frontier_pipeline(
    script: str,
    voiceover_path: str,
    output_name: str = "frontier_video.mp4",
    style_preset: str = "jung",  # Default to Jung style
    niche_profile: dict | None = None,
    caption_style: str = "",
    caption_accent: str = "#C9B896",  # Frontier warm gold
    caption_font_size: str = "Medium",
    caption_position: str = "Bottom",
    progress_callback=None,
    lite_mode: bool = False,
    image_quality: str = "standard",
    color_grade: str = "eq=brightness=-0.06:saturation=0.62:contrast=1.10,colortemperature=temperature=5200",  # Jung look EXACT from jung.json
    add_dust: bool = True,
    add_vignette: bool = True,
) -> dict:
    """
    Run the full Frontier pipeline matching Whop Frontier 1:1.
    
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
    from core import frontier_atlas, frontier_pexels, frontier_motion_planner, frontier_assembler

    timing: dict[str, float] = {}

    def _log(msg: str):
        print(f"[frontier] {msg}")
        if progress_callback:
            progress_callback(msg)

    # --- Job directory ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = str(OUTPUT_DIR / f"frontier_{timestamp}")
    os.makedirs(job_dir, exist_ok=True)
    assets_dir = os.path.join(job_dir, "stills")
    pexels_dir = os.path.join(job_dir, "pexels")
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(pexels_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # STEP 1: Word-level alignment (Whisper for SRT)
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

    # Save SRT for subtitle burn-in
    srt_path = os.path.join(job_dir, "subtitles.srt")
    _write_srt(all_words, srt_path)

    # ------------------------------------------------------------------
    # STEP 2: Generate Atlas gpt-image-2 scene prompts
    # ------------------------------------------------------------------
    _log("Step 2/6: Generating scene prompts (Atlas LLM)...")
    t0 = time.time()

    # ~10 stills for a typical 8-10 min video
    still_count = max(8, min(15, len(script.split()) // 150))
    
    scene_prompts = frontier_atlas.generate_scene_prompts(
        script=script,
        scene_count=still_count,
        style_suffix=" Cinematic still, natural light, shallow depth of field, filmic grain, muted colour, no text",
    )

    timing["scene_prompts"] = time.time() - t0
    _log(f"  {len(scene_prompts)} scene prompts generated ({timing['scene_prompts']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 3: Generate AI stills with Atlas gpt-image-2
    # ------------------------------------------------------------------
    _log(f"Step 3/6: Generating {len(scene_prompts)} AI stills (Atlas gpt-image-2)...")
    t0 = time.time()

    def _on_gen_progress(completed, total):
        _log(f"  Stills: {completed}/{total}")

    workers = 6 if lite_mode else 12
    still_results = frontier_atlas.generate_frontier_stills(
        scene_prompts=scene_prompts,
        output_dir=assets_dir,
        max_workers=workers,
        progress_callback=_on_gen_progress,
    )

    timing["still_generation"] = time.time() - t0
    successes = sum(1 for r in still_results if r["success"])
    _log(f"  {successes}/{len(scene_prompts)} stills generated "
         f"({timing['still_generation']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 4: Fetch Pexels stock b-roll
    # ------------------------------------------------------------------
    _log("Step 4/6: Fetching Pexels stock video/photo b-roll...")
    t0 = time.time()

    niche_hint = (niche_profile or {}).get("name", "")
    pexels_keywords = frontier_pexels.generate_pexels_keywords(
        script=script,
        count=14,
        niche_hint=niche_hint,
    )

    pexels_videos, pexels_photos = frontier_pexels.fetch_pexels_assets(
        keywords=pexels_keywords,
        output_dir=pexels_dir,
        video_count=24,
        photo_count=12,
    )

    timing["pexels"] = time.time() - t0
    _log(f"  {len(pexels_videos)} videos, {len(pexels_photos)} photos "
         f"({timing['pexels']:.1f}s)")

    # ------------------------------------------------------------------
    # STEP 5: Build word-locked motion plan
    # ------------------------------------------------------------------
    _log("Step 5/6: Planning motion timeline (stills own spoken spans)...")
    t0 = time.time()

    # Get audio duration
    audio_dur = _get_audio_duration(voiceover_path)

    # Map stills to their spoken time spans based on sentence timing
    ai_stills = []
    for i, (result, scene) in enumerate(zip(still_results, scene_prompts)):
        if result["success"] and result["path"]:
            # Find matching sentence time for this scene's text
            scene_text = scene.get("text", "")[:100].lower()
            
            # Match to closest sentence
            best_match = None
            best_score = 0
            for sent_idx, sent_time in enumerate(sentence_times):
                # SentenceTimestamp is a dataclass with .text, .start_sec, .end_sec
                sent_text = sent_time.text.lower() if hasattr(sent_time, 'text') else ""
                # Simple word overlap score
                scene_words = set(scene_text.split())
                sent_words = set(sent_text.split())
                if scene_words and sent_words:
                    score = len(scene_words & sent_words) / len(scene_words | sent_words)
                    if score > best_score:
                        best_score = score
                        best_match = sent_time
            
            if best_match:
                ai_stills.append({
                    "path": result["path"],
                    "start_sec": best_match.start_sec,
                    "end_sec": best_match.end_sec,
                    "text": scene_text,
                })
            else:
                # Fallback: spread evenly across timeline
                start_sec = (i / len(scene_prompts)) * audio_dur
                end_sec = ((i + 1) / len(scene_prompts)) * audio_dur
                ai_stills.append({
                    "path": result["path"],
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "text": scene_text,
                })

    motion_segments = frontier_motion_planner.plan_motion_timeline(
        ai_stills=ai_stills,
        pexels_videos=pexels_videos,
        pexels_photos=pexels_photos,
        total_duration_sec=audio_dur,
        zoom_strategy="alternate",  # in, out, in, out...
        still_pad_sec=0.5,          # Reduced from 0.8 - tighter still holds
        still_min_hold_sec=3.0,     # Reduced from 3.5 - allow shorter stills
        still_max_hold_sec=6.0,     # Reduced from 10.0 - more Pexels gaps (Whop density)
        first_still_min_hold_sec=6.0,  # Kinetic hook 6–10s (Kevis); style JSON may override
    )

    timing["motion_plan"] = time.time() - t0
    _log(f"  {len(motion_segments)} motion segments planned "
         f"({timing['motion_plan']:.1f}s)")

    # Save motion plan
    motion_plan_path = os.path.join(job_dir, "motion_plan.json")
    with open(motion_plan_path, "w") as f:
        json.dump(
            frontier_motion_planner.export_motion_plan_json(motion_segments),
            f,
            indent=2,
        )

    # ------------------------------------------------------------------
    # STEP 5.5: Generate motion graphics cards (collage GFX lane)
    # ------------------------------------------------------------------
    _log("Step 5.5/7: Generating motion graphics cards (collage lane)...")
    t0_gfx = time.time()
    
    gfx_cards = []
    try:
        from core import frontier_motion_graphics
        
        # Extract transcript sentences for semantic GFX titles
        transcript_sentences = [sent.text for sent in sentence_times]
        
        # Plan GFX insertions per jung.json pacing (~every 30s)
        gfx_specs = frontier_motion_graphics.plan_gfx_insertions(
            total_duration_sec=audio_dur,
            transcript_sentences=transcript_sentences,
        )
        
        _log(f"  Rendering {len(gfx_specs)} collage cards...")
        
        gfx_dir = os.path.join(job_dir, "gfx_cards")
        os.makedirs(gfx_dir, exist_ok=True)
        
        gfx_paths = frontier_motion_graphics.render_gfx_lane(
            gfx_specs=gfx_specs,
            output_dir=gfx_dir,
        )
        
        # Add GFX cards to motion segments
        for spec, path in zip(gfx_specs, gfx_paths):
            if path and path.exists():
                gfx_cards.append({
                    "type": "motion_gfx",
                    "path": str(path),
                    "start_sec": spec["start_sec"],
                    "end_sec": spec["end_sec"],
                    "title": spec["title"],
                    "subtitle": spec.get("subtitle", ""),
                    "skin": spec["skin"],
                })
        
        _log(f"  {len(gfx_cards)} GFX cards rendered successfully ({time.time() - t0_gfx:.1f}s)")
        
    except ImportError as e:
        _log(f"  WARNING: Motion graphics module not available: {e}")
        _log("  Proceeding without GFX cards (motion_graphics score will be lower)")
    except Exception as e:
        _log(f"  WARNING: Failed to generate GFX cards: {e}")
        _log("  Proceeding without GFX cards")

    timing["motion_gfx"] = time.time() - t0_gfx
    
    # Merge GFX cards into motion timeline
    if gfx_cards:
        _log(f"  Merging {len(gfx_cards)} GFX cards into motion timeline...")
        
        # Convert GFX cards to MotionSegment objects
        from core.frontier_motion_planner import MotionSegment
        
        gfx_segments = []
        for card in gfx_cards:
            gfx_seg = MotionSegment(
                type="motion_gfx",
                path=card["path"],
                start_sec=card["start_sec"],
                end_sec=card["end_sec"],
                zoom="hold",  # No Ken Burns on GFX
                text=card.get("title", ""),
            )
            gfx_segments.append(gfx_seg)
        
        # Insert GFX segments into timeline (sorted by start time)
        all_segments = motion_segments + gfx_segments
        all_segments.sort(key=lambda s: s.start_sec)
        
        # Trim overlapping segments (GFX takes priority, shorten surrounding segments)
        final_segments = []
        for seg in all_segments:
            if seg.type == "motion_gfx":
                # GFX card - keep full duration, trim any overlaps
                final_segments.append(seg)
            else:
                # Regular segment - trim if it overlaps with GFX
                trimmed = True
                for gfx in gfx_segments:
                    if seg.start_sec < gfx.end_sec and seg.end_sec > gfx.start_sec:
                        # Overlaps with GFX - trim or split
                        if seg.start_sec < gfx.start_sec < seg.end_sec:
                            # Trim end before GFX
                            seg.end_sec = gfx.start_sec
                        elif seg.start_sec < gfx.end_sec < seg.end_sec:
                            # Trim start after GFX
                            seg.start_sec = gfx.end_sec
                
                # Only keep if duration > 1s after trimming
                if seg.end_sec - seg.start_sec > 1.0:
                    final_segments.append(seg)
        
        motion_segments = final_segments
        _log(f"  Timeline now has {len(motion_segments)} segments (including GFX)")

    # ------------------------------------------------------------------
    # STEP 6: Assemble final video
    # ------------------------------------------------------------------
    _log("Step 6/7: Assembling video (Ken Burns, captions, grade, dust, vignette)...")
    t0 = time.time()

    output_path = os.path.join(job_dir, output_name)

    # Get dust overlay path if it exists
    dust_path = None
    if add_dust:
        # Try multiple locations: repo root, package install, relative to this file
        possible_paths = [
            Path(__file__).parent.parent / "assets" / "overlay_dust.mp4",
            Path("/workspace/assets/overlay_dust.mp4"),  # Absolute fallback
            Path.cwd() / "assets" / "overlay_dust.mp4",  # CWD fallback
        ]
        for possible_dust in possible_paths:
            if possible_dust.exists():
                dust_path = str(possible_dust)
                _log(f"  Found dust overlay: {dust_path}")
                break
        
        if not dust_path:
            _log(f"  WARNING: Dust overlay not found at any of: {[str(p) for p in possible_paths]}")

    result = frontier_assembler.assemble_frontier_video(
        motion_segments=motion_segments,
        voiceover_path=voiceover_path,
        subtitle_path=srt_path,
        output_path=output_path,
        color_grade=color_grade,
        add_dust=add_dust and dust_path is not None,
        add_vignette=add_vignette,
        dust_overlay_path=dust_path,
        zoom_amount=0.28,
        word_timings=all_words,  # Pass Whisper word timings for kinetic captions
        progress_callback=_log,
    )

    timing["assembly"] = time.time() - t0
    _log(f"  Assembly complete ({timing['assembly']:.1f}s)")

    # ------------------------------------------------------------------
    # Return results
    # ------------------------------------------------------------------
    type_counts = {
        "ai_stills": sum(1 for seg in motion_segments if seg.type == "ai_still"),
        "pexels_videos": sum(1 for seg in motion_segments if seg.type == "pexels_video"),
        "pexels_photos": sum(1 for seg in motion_segments if seg.type == "pexels_photo"),
    }

    total_time = sum(timing.values())
    _log(f"✓ Frontier pipeline complete ({total_time:.1f}s total)")

    return {
        "output_path": output_path,
        "job_dir": job_dir,
        "slots": [seg.to_dict() for seg in motion_segments],
        "type_counts": type_counts,
        "timing": timing,
    }


def _write_srt(words: list[dict], output_path: str):
    """Write word timings to SRT subtitle file."""
    if not words:
        return
    
    # Group words into subtitle chunks (~10 words or 5 seconds)
    chunks = []
    current_chunk = []
    chunk_start = words[0]["start"]
    
    for word in words:
        current_chunk.append(word["word"])
        
        # Break chunk on punctuation or length
        if (len(current_chunk) >= 10 or 
            word["end"] - chunk_start >= 5.0 or
            word["word"].strip()[-1:] in ".!?"):
            
            chunks.append({
                "start": chunk_start,
                "end": word["end"],
                "text": " ".join(current_chunk),
            })
            current_chunk = []
            if words.index(word) + 1 < len(words):
                chunk_start = words[words.index(word) + 1]["start"]
    
    if current_chunk:
        chunks.append({
            "start": chunk_start,
            "end": words[-1]["end"],
            "text": " ".join(current_chunk),
        })
    
    # Write SRT
    with open(output_path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks, 1):
            start_time = _format_srt_time(chunk["start"])
            end_time = _format_srt_time(chunk["end"])
            f.write(f"{i}\n")
            f.write(f"{start_time} --> {end_time}\n")
            f.write(f"{chunk['text']}\n\n")


def _format_srt_time(seconds: float) -> str:
    """Format seconds to SRT timestamp: 00:00:01,000"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    import subprocess
    
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[frontier] Could not get audio duration: {e}")
        return 60.0  # Fallback


def _estimate_word_timestamps(script: str, audio_path: str) -> list[dict]:
    """
    Fallback: estimate word timestamps when Whisper fails.
    Assumes ~150 words per minute speaking rate.
    """
    duration = _get_audio_duration(audio_path)
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


# Keep old helper functions for backwards compatibility
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

