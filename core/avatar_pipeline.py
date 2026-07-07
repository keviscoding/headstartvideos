"""
Avatar pipeline: orchestrates the avatar + illustration B-roll recipe.
Flow: script -> segment -> HeyGen avatar -> illustration gen/search ->
      Ken Burns -> interleave avatar & illustration clips -> assemble.
"""

from __future__ import annotations
import asyncio
import subprocess
import time
from pathlib import Path

from core.segmenter import segment_script_with_audio, segment_script_no_audio
from core.query_gen import generate_queries
from core.image_search import search_batch, download_image
from core.image_gen import generate_batch, build_illustration_prompt
from core.ranker import rank_and_pick
from core.ken_burns import render_all_clips
from core.assembler import build_video
from core.heygen import (
    create_avatar_video,
    wait_for_completion,
    download_video,
)
from config import OUTPUT_DIR, VIDEO_FPS


def _extract_clip(
    source_video: str,
    output_path: str,
    start_sec: float,
    end_sec: float,
) -> bool:
    """Extract a time-ranged clip from the avatar video using ffmpeg.
    Forces output to VIDEO_FPS to match Ken Burns clips for concatenation."""
    duration = end_sec - start_sec
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.3f}",
        "-i", source_video,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-r", str(VIDEO_FPS),
        "-an",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except Exception:
        return False


def run_avatar_pipeline(
    script: str,
    avatar_id: str,
    voice_id: str,
    voiceover_path: str | None = None,
    output_name: str = "final_video.mp4",
    swap_rate: str = "medium",
    style: str = "auto",
    avatar_ratio: float = 0.5,
    use_ai_images: bool = True,
    niche_profile: dict | None = None,
    background: dict | None = None,
    progress_callback=None,
) -> dict:
    """
    Full avatar pipeline: script -> avatar video + illustration B-roll -> final MP4.

    avatar_ratio: fraction of slots that show the avatar (0.0-1.0).
        0.5 means every other slot alternates between avatar and illustration.
    use_ai_images: if True, generate illustrations with AI; if False, search stock.
    """
    timings = {}
    job_dir = OUTPUT_DIR / f"avatar_job_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)
    images_dir = job_dir / "images"
    images_dir.mkdir(exist_ok=True)
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    def _log(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[avatar_pipeline] {msg}")

    # --- Step 1: Generate avatar video via HeyGen ---
    _log("Step 1/7: Generating avatar video via HeyGen...")
    t0 = time.time()
    avatar_result = create_avatar_video(
        script_text=script,
        avatar_id=avatar_id,
        voice_id=voice_id,
        background=background,
    )
    if avatar_result.status == "failed":
        raise RuntimeError(f"HeyGen video creation failed: {avatar_result.error}")

    completed = wait_for_completion(
        avatar_result.video_id,
        progress_callback=lambda msg: _log(f"  {msg}"),
    )
    avatar_path = str(job_dir / "avatar_full.mp4")
    download_video(completed.video_url, avatar_path)
    timings["heygen"] = time.time() - t0
    _log(f"  -> Avatar video ready ({timings['heygen']:.1f}s)")

    # --- Step 2: Segment script ---
    _log("Step 2/7: Segmenting script...")
    t0 = time.time()
    if voiceover_path:
        slots = segment_script_with_audio(script, voiceover_path, swap_rate)
    else:
        avatar_dur = completed.duration or 60
        slots = segment_script_no_audio(script, avatar_dur, swap_rate)
    timings["segment"] = time.time() - t0
    _log(f"  -> {len(slots)} slots ({timings['segment']:.1f}s)")

    # --- Step 3: Decide avatar vs illustration for each slot ---
    avatar_slots = []
    illustration_slots = []
    for i, slot in enumerate(slots):
        is_avatar = (i % max(1, round(1 / avatar_ratio))) == 0 if avatar_ratio < 1.0 else True
        if is_avatar:
            avatar_slots.append(slot)
        else:
            illustration_slots.append(slot)
    _log(f"  -> {len(avatar_slots)} avatar slots, {len(illustration_slots)} illustration slots")

    # --- Step 4: Extract avatar clips ---
    _log("Step 3/7: Extracting avatar clips...")
    t0 = time.time()
    avatar_clip_paths = {}
    for slot in avatar_slots:
        clip_path = str(clips_dir / f"avatar_{slot.id:04d}.mp4")
        success = _extract_clip(avatar_path, clip_path, slot.start_sec, slot.end_sec)
        if success:
            avatar_clip_paths[slot.id] = clip_path
        else:
            _log(f"  Warning: failed to extract avatar clip for slot {slot.id}")
    timings["extract"] = time.time() - t0
    _log(f"  -> {len(avatar_clip_paths)} avatar clips ({timings['extract']:.1f}s)")

    # --- Step 5: Generate illustration images ---
    _log("Step 4/7: Generating illustration queries...")
    t0 = time.time()
    if illustration_slots:
        seg_dicts = [{"id": s.id, "text": s.text} for s in illustration_slots]
        queries = generate_queries(seg_dicts, full_script=script, niche_profile=niche_profile)
        query_map = {q.slot_id: q for q in queries}
    else:
        queries = []
        query_map = {}
    timings["query_gen"] = time.time() - t0

    _log("Step 5/7: Generating/searching illustration images...")
    t0 = time.time()
    illustration_images = {}

    if use_ai_images and illustration_slots:
        niche_style = (niche_profile or {}).get("visual_style")
        gen_prompts = []
        for slot in illustration_slots:
            q = query_map.get(slot.id)
            if q:
                prompt = build_illustration_prompt(
                    narration=slot.text,
                    subject=q.subject,
                    era=q.era,
                    tone=q.tone,
                    format_hint=q.format_hint,
                    niche_style=niche_style,
                )
            else:
                prompt = build_illustration_prompt(narration=slot.text)
            gen_prompts.append({"id": slot.id, "prompt": prompt})

        gen_results = generate_batch(
            gen_prompts, str(images_dir),
            progress_callback=lambda done, total: _log(f"  Generating image {done}/{total}..."),
        )
        illustration_images.update(gen_results)

    # Fall back to stock search for any slots that AI generation missed
    missing_slots = [s for s in illustration_slots if s.id not in illustration_images]
    if missing_slots:
        _log(f"  Searching stock for {len(missing_slots)} missing illustrations...")
        search_slots_data = [
            {
                "id": q.slot_id,
                "query": q.primary_query,
                "fallback_query": q.fallback_query,
                "source_hint": q.source_hint,
                "style_hint": q.style_hint,
                "entity_type": q.entity_type,
                "subject": q.subject,
                "era": q.era,
                "tone": q.tone,
                "format_hint": q.format_hint,
            }
            for s in missing_slots
            if (q := query_map.get(s.id))
        ]
        if search_slots_data:
            results_by_slot = asyncio.run(search_batch(search_slots_data))
            used_urls: set[str] = set()
            for slot in missing_slots:
                candidates = results_by_slot.get(slot.id, [])
                q = query_map.get(slot.id)
                best = rank_and_pick(
                    candidates,
                    style_hint=q.style_hint if q else "neutral",
                    used_urls=used_urls,
                    entity_type=q.entity_type if q else "mood",
                    source_hint=q.source_hint if q else "any",
                    subject=q.subject if q else "",
                    tone=q.tone if q else "",
                )
                if best:
                    img_path = str(images_dir / f"slot_{slot.id:04d}.jpg")
                    dl_url = best.thumb_url or best.url
                    if asyncio.run(download_image(dl_url, img_path)):
                        used_urls.add(dl_url)
                        illustration_images[slot.id] = img_path

    timings["illustrations"] = time.time() - t0
    _log(f"  -> {len(illustration_images)} illustrations ({timings['illustrations']:.1f}s)")

    # --- Step 6: Ken Burns on illustration images ---
    _log("Step 6/7: Rendering Ken Burns clips for illustrations...")
    t0 = time.time()
    kb_clips = []
    for slot in illustration_slots:
        img_path = illustration_images.get(slot.id)
        if img_path:
            kb_clips.append({
                "id": slot.id,
                "image_path": img_path,
                "duration_sec": slot.duration_sec,
            })

    if kb_clips:
        kb_paths = render_all_clips(
            kb_clips, str(clips_dir),
            max_workers=4,
            progress_callback=lambda done, total: _log(f"  Rendering {done}/{total}..."),
        )
        illust_clip_paths = {}
        for clip_data, path in zip(kb_clips, kb_paths):
            if path:
                illust_clip_paths[clip_data["id"]] = path
    else:
        illust_clip_paths = {}
    timings["ken_burns"] = time.time() - t0

    # --- Step 7: Interleave and assemble ---
    _log("Step 7/7: Interleaving and assembling final video...")
    t0 = time.time()

    ordered_clips = []
    for slot in slots:
        if slot.id in avatar_clip_paths:
            ordered_clips.append(avatar_clip_paths[slot.id])
        elif slot.id in illust_clip_paths:
            ordered_clips.append(illust_clip_paths[slot.id])

    if not ordered_clips:
        raise RuntimeError("No clips to assemble")

    output_path = str(job_dir / output_name)
    audio_source = voiceover_path or avatar_path
    slot_dicts = [
        {"text": s.text, "start_sec": s.start_sec, "end_sec": s.end_sec}
        for s in slots
    ]
    build_video(ordered_clips, audio_source, slot_dicts, output_path)
    timings["assembly"] = time.time() - t0
    _log(f"  -> Done ({timings['assembly']:.1f}s)")

    total_time = sum(timings.values())
    _log(f"\nTotal avatar pipeline time: {total_time:.1f}s")

    return {
        "output_path": output_path,
        "job_dir": str(job_dir),
        "slots": [
            {"id": s.id, "text": s.text, "start": s.start_sec, "end": s.end_sec}
            for s in slots
        ],
        "avatar_slots": len(avatar_slots),
        "illustration_slots": len(illustration_slots),
        "timing": timings,
    }
