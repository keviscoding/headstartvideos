"""
Asset Router -- dispatches each scene from the DirectorScore to the
correct handler (video search, image search, AI image gen, text overlay)
with a verify-retry-fallback loop.

KEY RULES:
  1. NEVER use a plain black/solid color background. Every frame must have
     visual content.
  2. AI image generation is a FIRST-CLASS fallback, not a last resort.
     If stock video AND stock image both fail, AI gen MUST trigger.
  3. Text overlays are for 1-3 impactful words only (dates, stats, reveals).
  4. Static video clips get automatic Ken Burns zoom applied.

Fallback chain:
  stock_video -> stock_image -> ai_image -> (text_overlay is ONLY for
  scenes explicitly planned as text) -> ai_image_emergency
"""

from __future__ import annotations
import asyncio
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from config import (
    VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS,
)
from core.atlas_runtime import get_atlas_key


def _generate_ernie_cinematic(prompt: str, output_path: str) -> bool:
    """Try ERNIE Image Turbo for cinematic images — FREE via Atlas Cloud."""
    if not get_atlas_key():
        return False
    from core.atlas_llm import generate_ernie_image_file
    return generate_ernie_image_file((prompt or "")[:490], output_path)

MAX_STATIC_CLIP_SEC = 4.0


@dataclass
class ResolvedAsset:
    scene_id: int
    asset_type: str
    file_path: str
    clip_path: str
    duration_sec: float
    source: str
    query_used: str = ""
    vlm_score: float = -1.0


import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


def _run_async(coro):
    """Run an async coroutine safely from any thread."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

_used_video_urls: set[str] = set()
_used_image_urls: set[str] = set()
_dedup_lock = threading.Lock()

PARALLEL_BATCH_SIZE = 4


def resolve_all_scenes(
    scenes: list,
    job_dir: str,
    niche_profile: dict | None = None,
    progress_callback=None,
) -> list[ResolvedAsset]:
    """Resolve every scene to a concrete video clip, in parallel batches."""
    global _used_video_urls, _used_image_urls
    _used_video_urls = set()
    _used_image_urls = set()

    job = Path(job_dir)
    assets_dir = job / "assets"
    clips_dir = job / "clips"
    assets_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    results_map: dict[int, ResolvedAsset] = {}

    def _resolve_one(idx: int, scene) -> tuple[int, ResolvedAsset]:
        label = f"Scene {scene.id} ({scene.asset_type}, {scene.duration_sec:.1f}s)"
        print(f"[router] {label}: {scene.text[:60]}...")

        asset = _resolve_single(scene, assets_dir, clips_dir, niche_profile)

        if asset.asset_type == "stock_video":
            _enforce_motion(asset)

        _enforce_exact_duration(asset, scene.duration_sec)

        score_str = f" (VLM={asset.vlm_score:.2f})" if asset.vlm_score >= 0 else ""
        print(f"  -> {asset.asset_type} from {asset.source}{score_str}")
        return idx, asset

    ai_scenes = [(i, s) for i, s in enumerate(scenes) if s.asset_type == "ai_image"]
    stock_scenes = [(i, s) for i, s in enumerate(scenes) if s.asset_type != "ai_image"]

    for batch_label, scene_list in [("stock", stock_scenes), ("ai", ai_scenes)]:
        for batch_start in range(0, len(scene_list), PARALLEL_BATCH_SIZE):
            batch = scene_list[batch_start:batch_start + PARALLEL_BATCH_SIZE]
            if progress_callback:
                done = batch_start
                total = len(scene_list)
                ids = [s.id for _, s in batch]
                progress_callback(
                    f"Finding footage & images — {done + 1}–{min(done + len(batch), total)} of {total} "
                    f"(scenes {ids})..."
                )

            with ThreadPoolExecutor(max_workers=min(len(batch), PARALLEL_BATCH_SIZE)) as pool:
                futures = {
                    pool.submit(_resolve_one, idx, scene): idx
                    for idx, scene in batch
                }
                for future in as_completed(futures):
                    try:
                        idx, asset = future.result()
                        results_map[idx] = asset
                        if progress_callback:
                            finished = sum(1 for i, _ in scene_list if i in results_map)
                            progress_callback(
                                f"Finding footage & images — {finished} of {len(scene_list)} ready..."
                            )
                    except Exception as e:
                        idx = futures[future]
                        scene = scenes[idx]
                        print(f"  [router] Scene {scene.id} failed: {e}")
                        results_map[idx] = _handle_text_overlay_fallback(
                            scene, clips_dir
                        )

    return [results_map[i] for i in range(len(scenes))]


def _enforce_exact_duration(asset: ResolvedAsset, target_sec: float):
    """
    Verify clip duration matches target. Re-encode if drift > 0.1s.
    Prevents timeline desync from accumulating across scenes.
    """
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", asset.clip_path],
            capture_output=True, text=True, timeout=10,
        )
        actual = float(probe.stdout.strip()) if probe.stdout.strip() else 0
        drift = abs(actual - target_sec)
        if drift > 0.1:
            print(f"  [duration] Clip {asset.scene_id}: {actual:.2f}s vs target "
                  f"{target_sec:.2f}s (drift {drift:.2f}s), re-encoding...")
            fixed_path = asset.clip_path.replace(".mp4", "_fixed.mp4")
            cmd = [
                "ffmpeg", "-y",
                "-i", asset.clip_path,
                "-t", f"{target_sec:.3f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                "-an", "-pix_fmt", "yuv420p",
                fixed_path,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                os.replace(fixed_path, asset.clip_path)
            else:
                Path(fixed_path).unlink(missing_ok=True)
    except Exception as e:
        print(f"  [duration] Check failed: {e}")


def _resolve_single(
    scene,
    assets_dir: Path,
    clips_dir: Path,
    niche_profile: dict | None,
) -> ResolvedAsset:
    """Try to resolve a single scene through the correct fallback chain."""
    if scene.asset_type == "text_overlay":
        result = _handle_text_overlay(scene, clips_dir)
        if result:
            return result

    handlers = _get_visual_chain(scene.asset_type)

    for handler_type in handlers:
        try:
            result = _try_handler(handler_type, scene, assets_dir, clips_dir, niche_profile)
            if result:
                return result
        except Exception as e:
            print(f"  [router] {handler_type} failed for scene {scene.id}: {e}")

    emergency = _handle_ai_image_emergency(scene, assets_dir, clips_dir)
    if emergency:
        return emergency

    return _handle_text_overlay_fallback(scene, clips_dir)


def _get_visual_chain(preferred: str) -> list[str]:
    """
    Visual-only handler chain. Text overlay is NOT in this chain --
    it's only used for explicitly planned text scenes.
    """
    visual_chain = ["stock_video", "stock_image", "ai_image"]
    if preferred in visual_chain:
        idx = visual_chain.index(preferred)
        return visual_chain[idx:] + visual_chain[:idx]
    return visual_chain


def _try_handler(
    handler_type: str,
    scene,
    assets_dir: Path,
    clips_dir: Path,
    niche_profile: dict | None,
) -> ResolvedAsset | None:
    if handler_type == "stock_video":
        return _handle_stock_video(scene, assets_dir, clips_dir)
    elif handler_type == "stock_image":
        return _handle_stock_image(scene, assets_dir, clips_dir, niche_profile)
    elif handler_type == "ai_image":
        return _handle_ai_image(scene, assets_dir, clips_dir)
    return None


def _handle_stock_video(
    scene, assets_dir: Path, clips_dir: Path
) -> ResolvedAsset | None:
    global _used_video_urls
    from core.video_search import (
        search_videos_multi, download_video, trim_video, vlm_verify,
    )

    queries = scene.search_queries or [scene.text[:80]]
    print(f"  [video] Searching: {queries[:2]}")

    results = _run_async(search_videos_multi(queries, limit_per_query=5))
    if not results:
        print(f"  [video] No results found")
        return None

    with _dedup_lock:
        results = [r for r in results if r.url not in _used_video_urls]
    if not results:
        print(f"  [video] All results already used in earlier scenes")
        return None

    results.sort(key=lambda r: abs(r.duration - scene.duration_sec))

    for attempt, vid in enumerate(results[:6]):
        raw_path = str(assets_dir / f"scene_{scene.id:03d}_v{attempt}_raw.mp4")
        success = _run_async(download_video(vid.url, raw_path))
        if not success:
            continue

        passed, score, reason = vlm_verify(raw_path, scene.text)
        print(f"  [vlm] Score {score:.2f}: {reason[:60]}")

        if not passed and attempt < 4:
            os.unlink(raw_path)
            continue

        clip_path = str(clips_dir / f"clip_{scene.id:04d}.mp4")
        if trim_video(raw_path, clip_path, scene.duration_sec):
            with _dedup_lock:
                _used_video_urls.add(vid.url)
            return ResolvedAsset(
                scene_id=scene.id,
                asset_type="stock_video",
                file_path=raw_path,
                clip_path=clip_path,
                duration_sec=scene.duration_sec,
                source=vid.source,
                query_used=queries[0] if queries else "",
                vlm_score=score,
            )
        if os.path.exists(raw_path):
            os.unlink(raw_path)

    return None


def _handle_stock_image(
    scene, assets_dir: Path, clips_dir: Path,
    niche_profile: dict | None,
) -> ResolvedAsset | None:
    global _used_image_urls
    from core.image_search import search_batch, download_image
    from core.ranker import rank_and_pick
    from core.ken_burns import render_clip, pick_effects

    queries = scene.search_queries or [scene.text[:80]]
    has_named_entity = bool(re.search(
        r'(?:the\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}',
        scene.text
    )) or bool(re.search(r'\b[A-Z]{2,}\b', scene.text))
    slot = {
        "id": scene.id,
        "query": queries[0] if queries else scene.text[:80],
        "fallback_query": queries[1] if len(queries) > 1 else "",
        "source_hint": "wikimedia" if has_named_entity else "any",
        "style_hint": "neutral",
        "entity_type": "named_entity" if has_named_entity else "mood",
        "subject": scene.text[:60],
        "era": "",
        "tone": "",
        "format_hint": "",
    }

    results_by_slot = _run_async(search_batch([slot]))
    candidates = results_by_slot.get(scene.id, [])
    with _dedup_lock:
        candidates = [c for c in candidates if c.url not in _used_image_urls]

    if not candidates:
        return None

    with _dedup_lock:
        best = rank_and_pick(
            candidates, "neutral", _used_image_urls,
            entity_type="named_entity" if has_named_entity else "mood",
            source_hint="wikimedia" if has_named_entity else "any",
            subject=scene.text[:60], tone="",
        )
    if not best:
        return None

    with _dedup_lock:
        _used_image_urls.add(best.url)
        if best.thumb_url:
            _used_image_urls.add(best.thumb_url)

    img_path = str(assets_dir / f"scene_{scene.id:03d}.jpg")
    dl_url = best.thumb_url or best.url
    success = _run_async(download_image(dl_url, img_path))
    if not success:
        return None

    effect = pick_effects(1)[0]
    clip_path = str(clips_dir / f"clip_{scene.id:04d}.mp4")
    if render_clip(img_path, clip_path, scene.duration_sec, effect):
        return ResolvedAsset(
            scene_id=scene.id,
            asset_type="stock_image",
            file_path=img_path,
            clip_path=clip_path,
            duration_sec=scene.duration_sec,
            source=best.source,
            query_used=slot["query"],
        )
    return None


def _handle_ai_image(
    scene, assets_dir: Path, clips_dir: Path
) -> ResolvedAsset | None:
    """
    Cinematic AI stills: ERNIE by default; GPT Image 2 Developer when HQ cook.
    """
    if not get_atlas_key():
        return None

    from core.ken_burns import render_clip, pick_effects
    from core.image_quality_ctx import is_hq
    from core.atlas_llm import generate_hq_image_file, generate_ernie_image_file

    if scene.ai_prompt:
        prompt = scene.ai_prompt
    else:
        text_lower = scene.text.lower()
        if any(kw in text_lower for kw in ("manuscript", "parchment", "drawing",
               "illustration", "diagram", "medieval", "ancient text")):
            prompt = (
                f"A page from an ancient medieval manuscript showing a "
                f"hand-drawn illustration related to: {scene.text[:200]}. "
                f"Aged yellowed parchment, faded ink, medieval style. "
                f"Cinematic macro photography of the manuscript page."
            )
        else:
            prompt = (
                f"Create a cinematic documentary still image. "
                f"The scene depicts: {scene.text[:200]}. "
                f"Style: dramatic lighting, shallow depth of field, "
                f"16:9 widescreen composition, documentary photography."
            )

    NO_TEXT_SUFFIX = (
        " IMPORTANT: Do NOT include any text, letters, words, fonts, titles, "
        "labels, watermarks, or captions in the image. Generate clean art only."
    )
    if "do not include" not in prompt.lower() and "no text" not in prompt.lower():
        prompt = prompt.rstrip(".") + "." + NO_TEXT_SUFFIX

    img_path = str(assets_dir / f"scene_{scene.id:03d}_ai.png")
    if is_hq():
        print(f"  [ai] Generating HQ image (GPT Image 2 Dev): {prompt[:80]}...")
        ok = generate_hq_image_file(prompt, img_path)
        source = "gpt-image-2-developer"
        if not ok:
            print("  [ai] HQ failed — falling back to ERNIE")
            ok = generate_ernie_image_file(prompt, img_path) or _generate_ernie_cinematic(prompt, img_path)
            source = "ernie"
    else:
        print(f"  [ai] Generating image (ERNIE): {prompt[:80]}...")
        ok = _generate_ernie_cinematic(prompt, img_path)
        source = "ernie"
        if not ok:
            print("  [ai] ERNIE failed — falling back to GPT Image 2")
            ok = generate_hq_image_file(prompt, img_path)
            source = "gpt-image-2-fallback"

    if ok:
        effect = pick_effects(1)[0]
        clip_path = str(clips_dir / f"clip_{scene.id:04d}.mp4")
        if render_clip(img_path, clip_path, scene.duration_sec, effect):
            print(f"  [ai] Generated with {source}")
            return ResolvedAsset(
                scene_id=scene.id, asset_type="ai_image",
                file_path=img_path, clip_path=clip_path,
                duration_sec=scene.duration_sec, source=source,
                query_used=prompt[:80],
            )

    return None


def _handle_ai_image_emergency(
    scene, assets_dir: Path, clips_dir: Path
) -> ResolvedAsset | None:
    """Emergency AI still — ERNIE, then cheap GPT Image 2."""
    if not get_atlas_key():
        return None

    from core.ken_burns import render_clip
    from core.atlas_llm import generate_hq_image_file

    simple_prompt = (
        f"A moody, dark, cinematic photograph related to: "
        f"{scene.text[:100]}. "
        f"Dramatic shadows, atmospheric, documentary style."
    )

    print(f"  [ai-emergency] Trying ERNIE with simplified prompt...")

    try:
        img_path = str(assets_dir / f"scene_{scene.id:03d}_emergency.png")
        ok = _generate_ernie_cinematic(simple_prompt, img_path)
        source = "ernie_emergency"
        if not ok:
            print("  [ai-emergency] ERNIE failed — GPT Image 2 fallback")
            ok = generate_hq_image_file(simple_prompt, img_path)
            source = "gpt-image-2-emergency"
        if ok:
            clip_path = str(clips_dir / f"clip_{scene.id:04d}.mp4")
            if render_clip(img_path, clip_path, scene.duration_sec, "zoom_in_center"):
                return ResolvedAsset(
                    scene_id=scene.id,
                    asset_type="ai_image",
                    file_path=img_path,
                    clip_path=clip_path,
                    duration_sec=scene.duration_sec,
                    source=source,
                    query_used=simple_prompt[:60],
                )
    except Exception as e:
        print(f"  [ai-emergency] Failed: {e}")

    return None


def _handle_text_overlay(
    scene, clips_dir: Path
) -> ResolvedAsset | None:
    """Generate a cinematic text overlay with textured background."""
    from core.text_overlay import render_text_clip

    text = scene.overlay_text or scene.text[:40]
    clip_path = str(clips_dir / f"clip_{scene.id:04d}.mp4")

    success = render_text_clip(
        text=text,
        output_path=clip_path,
        duration_sec=scene.duration_sec,
        style="cinematic",
        effect="fade",
    )
    if success:
        return ResolvedAsset(
            scene_id=scene.id,
            asset_type="text_overlay",
            file_path=clip_path,
            clip_path=clip_path,
            duration_sec=scene.duration_sec,
            source="text",
            query_used=text,
        )
    return None


def _handle_text_overlay_fallback(
    scene, clips_dir: Path
) -> ResolvedAsset:
    """
    Absolute last resort: textured background text overlay.
    This should rarely trigger -- AI image gen should catch most cases.
    """
    from core.text_overlay import render_text_clip

    words = scene.text.split()
    if len(words) > 5:
        key_words = _extract_impact_words(scene.text)
        text = key_words
    else:
        text = scene.text

    clip_path = str(clips_dir / f"clip_{scene.id:04d}.mp4")

    render_text_clip(
        text=text,
        output_path=clip_path,
        duration_sec=scene.duration_sec,
        style="cinematic",
        effect="fade",
    )

    return ResolvedAsset(
        scene_id=scene.id,
        asset_type="text_overlay",
        file_path=clip_path,
        clip_path=clip_path,
        duration_sec=scene.duration_sec,
        source="text_fallback",
        query_used=text,
    )


def _extract_impact_words(text: str) -> str:
    """
    Extract 1-3 high-impact words for text overlay.
    Prioritizes: numbers/dates > proper nouns > dramatic adjectives > strong nouns.
    """
    import re as _re

    numbers = _re.findall(r'\b\d[\d,]*\b', text)
    if numbers:
        return numbers[0]

    date_patterns = _re.findall(r'\b(?:1[0-9]{3}|2[0-9]{3})\b', text)
    if date_patterns:
        return date_patterns[0]

    dramatic = [
        "never", "unexpected", "secret", "hidden", "shocking", "impossible",
        "unprecedented", "unknown", "forbidden", "ancient", "mysterious",
        "discovered", "revealed", "transformed", "challenged", "changed",
    ]

    words = text.split()
    proper_nouns = [w.strip(".,!?;:\"'()-") for w in words
                    if w[0].isupper() and len(w) > 2 and w.lower() not in
                    {"the", "but", "and", "for", "this", "that", "what", "they"}]

    if proper_nouns and len(proper_nouns) <= 3:
        return " ".join(proper_nouns[:2]).upper()

    for word in words:
        clean = word.strip(".,!?;:\"'()-").lower()
        if clean in dramatic:
            return clean.upper()

    strong_nouns = []
    skip = {
        "the", "a", "an", "is", "are", "was", "were", "that", "this",
        "and", "but", "or", "for", "of", "in", "on", "to", "with",
        "has", "have", "had", "been", "their", "they", "what", "which",
        "who", "it", "its", "from", "no", "not", "one", "we", "our",
        "about", "would", "could", "should", "than", "then", "them",
        "by", "before", "after", "something", "everything", "being",
        "those", "these", "ever", "never",
    }
    for w in words:
        clean = w.strip(".,!?;:\"'()-").lower()
        if clean not in skip and len(clean) >= 4:
            strong_nouns.append(w.strip(".,!?;:\"'()-"))

    if len(strong_nouns) >= 2:
        return " ".join(strong_nouns[:2]).upper()
    elif strong_nouns:
        return strong_nouns[0].upper()

    return text.split()[0].upper() if text.split() else "..."


def _enforce_motion(asset: ResolvedAsset):
    """
    If a stock video clip is static, apply a lightweight crop-based
    zoom rather than the expensive zoompan filter.
    Skipped for short clips -- only worth it for 4+ second static shots.
    """
    if not asset.clip_path or not os.path.exists(asset.clip_path):
        return

    if asset.duration_sec < MAX_STATIC_CLIP_SEC:
        return

    if _is_static_clip(asset.clip_path):
        print(f"  [motion] Static clip detected, adding subtle zoom...")
        _apply_light_zoom(asset.clip_path, asset.duration_sec)


def _is_static_clip(clip_path: str) -> bool:
    """
    Detect near-static stock clips. Disabled for now: a prior stub always
    returned True for clips >= 2s, forcing a 30s ffmpeg re-encode on every
    stock clip and making cinematic cooks appear stuck on "Resolving scenes".
    Prefer finishing cooks; re-enable with a real frame-diff when needed.
    """
    return False


def _apply_light_zoom(clip_path: str, duration_sec: float):
    """
    Apply a lightweight crop-based zoom: start at 95% crop, end at 100%.
    Much faster than zoompan because it doesn't re-render every frame.
    """
    tmp_path = clip_path + ".tmp.mp4"

    vf = (
        f"scale=iw*1.05:ih*1.05,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
        f"'(iw-{VIDEO_WIDTH})/2*(1-t/{duration_sec:.2f})':"
        f"'(ih-{VIDEO_HEIGHT})/2*(1-t/{duration_sec:.2f})',"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "22",
        "-an",
        tmp_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            os.replace(tmp_path, clip_path)
            print(f"  [motion] Light zoom applied")
        else:
            print(f"  [motion] Zoom skipped: {result.stderr[-150:]}")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except Exception as e:
        print(f"  [motion] Skipped: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
