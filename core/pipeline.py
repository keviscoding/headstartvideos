"""
Main pipeline: orchestrates segmentation, query generation,
image search, ranking, Ken Burns rendering, and final assembly.

Includes a concept-fallback: when entity-based search returns no
good match (person/event not found), falls back to a Pexels-only
concept search using the fallback_query.
"""

from __future__ import annotations
import asyncio
import time
from pathlib import Path

from core.segmenter import segment_script_with_audio, segment_script_no_audio
from core.query_gen import generate_queries
from core.image_search import search_batch, search_pexels, download_image
from core.ranker import rank_and_pick
from core.ken_burns import render_all_clips
from core.assembler import build_video
from config import OUTPUT_DIR, WIKIMEDIA_USER_AGENT


def run_pipeline(
    script: str,
    voiceover_path: str,
    output_name: str = "final_video.mp4",
    swap_rate: str = "medium",
    style: str = "auto",
    niche_profile: dict | None = None,
    caption_style: str = "Clean",
    caption_accent: str = "#00BFFF",
    caption_font_size: str = "Medium",
    caption_position: str = "Bottom",
    progress_callback=None,
) -> dict:
    """
    Full pipeline: script + voiceover -> finished MP4.
    Returns {"output_path": str, "slots": list, "images": list, "timing": dict}
    """
    timings = {}
    job_dir = OUTPUT_DIR / f"job_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)
    images_dir = job_dir / "images"
    images_dir.mkdir(exist_ok=True)
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    def _log(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[pipeline] {msg}")

    # --- Step 1: Segment ---
    _log("Step 1/6: Segmenting script with audio timestamps...")
    t0 = time.time()
    slots = segment_script_with_audio(script, voiceover_path, swap_rate)
    timings["segment"] = time.time() - t0
    _log(f"  -> {len(slots)} B-roll slots ({timings['segment']:.1f}s)")

    # --- Step 2: Generate queries ---
    _log("Step 2/6: Generating image search queries (LLM)...")
    t0 = time.time()
    seg_dicts = [{"id": s.id, "text": s.text} for s in slots]
    queries = generate_queries(seg_dicts, full_script=script, niche_profile=niche_profile)
    timings["query_gen"] = time.time() - t0
    _log(f"  -> {len(queries)} queries ({timings['query_gen']:.1f}s)")

    # --- Step 3: Search images ---
    _log("Step 3/6: Searching images (Wikimedia + Pexels)...")
    t0 = time.time()
    search_slots = [
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
        for q in queries
    ]
    results_by_slot = asyncio.run(search_batch(search_slots))
    timings["search"] = time.time() - t0
    total_results = sum(len(v) for v in results_by_slot.values())
    _log(f"  -> {total_results} total results ({timings['search']:.1f}s)")

    # --- Step 4: Rank and download ---
    _log("Step 4/6: Ranking images and downloading...")
    t0 = time.time()
    used_urls: set[str] = set()
    query_map = {q.slot_id: q for q in queries}
    selected_images = []

    for slot in slots:
        candidates = results_by_slot.get(slot.id, [])
        q = query_map.get(slot.id)
        style_hint = q.style_hint if q else "neutral"
        entity_type = q.entity_type if q else "mood"
        source_hint = q.source_hint if q else "any"
        if style != "auto":
            style_hint = style

        subject = q.subject if q else ""
        tone = q.tone if q else ""

        best = rank_and_pick(
            candidates, style_hint, used_urls,
            entity_type=entity_type, source_hint=source_hint,
            subject=subject, tone=tone,
        )

        # Concept-fallback: if entity not found in any result, try
        # Pexels with the fallback_query (generic visual concept)
        if best is None and q and q.fallback_query:
            _log(f"  Slot {slot.id}: entity not matched, trying concept fallback...")
            import httpx
            async def _concept_search():
                async with httpx.AsyncClient(
                    headers={"User-Agent": WIKIMEDIA_USER_AGENT},
                ) as client:
                    return await search_pexels(client, q.fallback_query)
            concept_results = asyncio.run(_concept_search())
            if concept_results:
                best = rank_and_pick(
                    concept_results, style_hint, used_urls,
                    entity_type="mood", source_hint="stock",
                    subject="", tone=tone,
                )

        if best:
            img_path = str(images_dir / f"slot_{slot.id:04d}.jpg")
            dl_url = best.thumb_url or best.url
            success = asyncio.run(download_image(dl_url, img_path))
            if success:
                used_urls.add(dl_url)
                selected_images.append({
                    "slot_id": slot.id,
                    "image_path": img_path,
                    "source": best.source,
                    "title": best.title,
                    "url": best.url,
                })
                continue

        _create_fallback_image(
            str(images_dir / f"slot_{slot.id:04d}.jpg"),
            slot.text[:80],
        )
        selected_images.append({
            "slot_id": slot.id,
            "image_path": str(images_dir / f"slot_{slot.id:04d}.jpg"),
            "source": "fallback",
            "title": "No image found",
            "url": "",
        })

    timings["download"] = time.time() - t0
    found = sum(1 for img in selected_images if img["source"] != "fallback")
    _log(f"  -> {found}/{len(slots)} images found ({timings['download']:.1f}s)")

    # --- Step 5: Ken Burns rendering ---
    _log("Step 5/6: Rendering Ken Burns clips...")
    t0 = time.time()

    kb_clips = []
    for slot, img in zip(slots, selected_images):
        kb_clips.append({
            "id": slot.id,
            "image_path": img["image_path"],
            "duration_sec": slot.duration_sec,
        })

    def _kb_progress(done, total):
        _log(f"  Rendering clip {done}/{total}...")

    clip_paths = render_all_clips(
        kb_clips, str(clips_dir),
        max_workers=4,
        progress_callback=_kb_progress,
    )
    timings["ken_burns"] = time.time() - t0
    _log(f"  -> {len(clip_paths)} clips rendered ({timings['ken_burns']:.1f}s)")

    # --- Step 6: Final assembly ---
    _log("Step 6/6: Assembling final video...")
    t0 = time.time()
    output_path = str(job_dir / output_name)
    slot_dicts = [
        {"text": s.text, "start_sec": s.start_sec, "end_sec": s.end_sec}
        for s in slots
    ]
    build_video(
        clip_paths, voiceover_path, slot_dicts, output_path,
        progress_callback=_log,
    )
    timings["assembly"] = time.time() - t0
    _log(f"  -> Done ({timings['assembly']:.1f}s)")

    total_time = sum(timings.values())
    _log(f"\nTotal pipeline time: {total_time:.1f}s")

    return {
        "output_path": output_path,
        "job_dir": str(job_dir),
        "slots": [
            {"id": s.id, "text": s.text, "start": s.start_sec, "end": s.end_sec}
            for s in slots
        ],
        "images": selected_images,
        "timing": timings,
    }


def _align_text_overlays_to_words(scenes, all_words, _log=None):
    """
    Shift text overlay start times so they align with the actual word
    timestamp rather than the beginning of the scene's sentence.

    E.g. if overlay_text is "2023" and the scene starts at 50.0s but
    the word "2023" is spoken at 55.3s, shift the overlay to 55.3s.
    """
    if not all_words:
        return scenes

    import re

    words_lower = [(w["word"].lower().strip(".,!?;:'\"()-"), w) for w in all_words]

    for scene in scenes:
        if scene.asset_type != "text_overlay" or not scene.overlay_text:
            continue

        target = scene.overlay_text.strip().lower()
        target_parts = target.split()

        best_time = None

        for i, (wl, wd) in enumerate(words_lower):
            if wd["start"] < scene.start_sec - 1.0 or wd["start"] > scene.end_sec + 2.0:
                continue

            if len(target_parts) == 1:
                if re.sub(r'[^a-z0-9]', '', wl) == re.sub(r'[^a-z0-9]', '', target):
                    best_time = wd["start"]
                    break
            else:
                chunk = " ".join(w[0] for w in words_lower[i:i + len(target_parts)])
                if re.sub(r'[^a-z0-9 ]', '', chunk) == re.sub(r'[^a-z0-9 ]', '', target):
                    best_time = wd["start"]
                    break

        if best_time is not None and best_time > scene.start_sec + 0.5:
            old_start = scene.start_sec
            scene.start_sec = max(best_time - 0.3, scene.start_sec)
            scene.duration_sec = scene.end_sec - scene.start_sec
            if _log:
                _log(f"  [text-align] \"{scene.overlay_text}\" shifted "
                     f"{old_start:.1f}s -> {scene.start_sec:.1f}s")

    return scenes


def run_cinematic_pipeline(
    script: str,
    voiceover_path: str,
    output_name: str = "final_video.mp4",
    swap_rate: str = "medium",
    style: str = "auto",
    niche_profile: dict | None = None,
    caption_style: str = "Clean",
    caption_accent: str = "#00BFFF",
    caption_font_size: str = "Medium",
    caption_position: str = "Bottom",
    progress_callback=None,
) -> dict:
    """
    Cinematic pipeline: script + voiceover -> multi-asset video.

    Uses:
      1. Word-level alignment for precise timing
      2. LLM scene planner for asset type decisions
      3. Asset router with video/image/AI/text and VLM verification
      4. Final assembly with captions
    """
    timings = {}
    job_dir = OUTPUT_DIR / f"cine_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)

    def _log(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[cinematic] {msg}")

    # --- Step 1: Word-level alignment ---
    _log("Step 1/5: Aligning script to audio (word-level)...")
    t0 = time.time()

    from core.segmenter import align_script_to_audio

    sentence_times, all_words = align_script_to_audio(
        script=script,
        audio_path=voiceover_path,
        model_size="base",
    )

    if not sentence_times:
        _log("  Word alignment failed, falling back to proportional...")
        from core.segmenter import split_sentences, _distribute_sentences_evenly
        sentences = split_sentences(script)
        import subprocess
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", voiceover_path],
            capture_output=True, text=True, timeout=10,
        )
        total_dur = float(probe.stdout.strip()) if probe.stdout.strip() else 60.0
        proportional = _distribute_sentences_evenly(sentences, total_dur)
        sentence_times_dicts = [
            {"text": s, "start": t[0], "end": t[1]}
            for s, t in zip(sentences, proportional)
        ]
    else:
        sentence_times_dicts = [
            {"text": st.text, "start": st.start_sec, "end": st.end_sec}
            for st in sentence_times
        ]

    timings["alignment"] = time.time() - t0
    _log(f"  -> {len(sentence_times_dicts)} sentences aligned ({timings['alignment']:.1f}s)")

    # --- Step 2: LLM Scene Planning ---
    _log("Step 2/5: Planning scenes with LLM (DirectorScore)...")
    t0 = time.time()

    from core.scene_planner import plan_scenes

    style_notes = ""
    if style != "auto":
        style_notes = f"Preferred visual style: {style}"

    scenes = plan_scenes(
        script=script,
        sentence_timestamps=sentence_times_dicts,
        niche_profile=niche_profile,
        style_notes=style_notes,
    )

    timings["planning"] = time.time() - t0
    _log(f"  -> {len(scenes)} scenes planned ({timings['planning']:.1f}s)")

    # --- Step 2b: Fix text overlay timing using word-level timestamps ---
    scenes = _align_text_overlays_to_words(scenes, all_words, _log)

    # --- Step 3: Resolve assets ---
    _log("Step 3/5: Resolving assets (video/image/AI/text)...")
    t0 = time.time()

    from core.asset_router import resolve_all_scenes

    assets = resolve_all_scenes(
        scenes=scenes,
        job_dir=str(job_dir),
        niche_profile=niche_profile,
        progress_callback=_log,
    )

    timings["assets"] = time.time() - t0
    type_counts = {}
    for a in assets:
        type_counts[a.asset_type] = type_counts.get(a.asset_type, 0) + 1
    _log(f"  -> {len(assets)} assets resolved: {type_counts} ({timings['assets']:.1f}s)")

    # --- Step 4: Assembly ---
    _log("Step 4/5: Assembling final video...")
    t0 = time.time()

    clip_paths = [a.clip_path for a in assets]
    slot_dicts = [
        {"text": s.text, "start_sec": s.start_sec, "end_sec": s.end_sec}
        for s in scenes
    ]
    output_path = str(job_dir / output_name)

    build_video(
        clip_paths, voiceover_path, slot_dicts, output_path,
        progress_callback=_log,
    )

    timings["assembly"] = time.time() - t0
    _log(f"  -> Assembled ({timings['assembly']:.1f}s)")

    # --- Step 5: Summary ---
    total_time = sum(timings.values())
    _log(f"\nTotal cinematic pipeline time: {total_time:.1f}s")

    return {
        "output_path": output_path,
        "job_dir": str(job_dir),
        "slots": [
            {"id": s.id, "text": s.text, "start": s.start_sec, "end": s.end_sec}
            for s in scenes
        ],
        "assets": [
            {
                "scene_id": a.scene_id,
                "asset_type": a.asset_type,
                "source": a.source,
                "query": a.query_used,
                "vlm_score": a.vlm_score,
            }
            for a in assets
        ],
        "type_counts": type_counts,
        "timing": timings,
    }


def _create_fallback_image(path: str, text: str):
    """Create a simple dark image with text as fallback."""
    from PIL import Image, ImageDraw, ImageFont
    from config import VIDEO_WIDTH, VIDEO_HEIGHT

    img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (15, 15, 15))
    draw = ImageDraw.Draw(img)
    from core.text_overlay import _SYSTEM_FONT
    try:
        font = ImageFont.truetype(_SYSTEM_FONT, 36) if _SYSTEM_FONT else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (VIDEO_WIDTH - tw) // 2
    y = (VIDEO_HEIGHT - th) // 2
    draw.text((x, y), text, fill=(120, 120, 120), font=font)
    img.save(path, "JPEG", quality=90)
