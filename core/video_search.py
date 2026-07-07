"""
Video search module for cinematic B-roll.

Searches Pexels and Pixabay video APIs, downloads clips, trims to
the required duration, and optionally verifies with Gemini VLM.
"""

from __future__ import annotations
import asyncio
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from config import (
    PEXELS_KEY, PIXABAY_KEY, GEMINI_KEY,
    VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS,
)


@dataclass
class VideoResult:
    url: str
    preview_url: str
    width: int
    height: int
    duration: float
    source: str
    tags: str = ""
    score: float = 0.0


async def search_pexels_video(
    client: httpx.AsyncClient, query: str, limit: int = 5
) -> list[VideoResult]:
    """Search Pexels video API."""
    if not PEXELS_KEY:
        return []
    headers = {"Authorization": PEXELS_KEY}
    params = {
        "query": query,
        "per_page": str(limit),
        "orientation": "landscape",
        "size": "medium",
    }
    try:
        resp = await client.get(
            "https://api.pexels.com/videos/search",
            params=params, headers=headers, timeout=15,
        )
        data = resp.json()
    except Exception as e:
        print(f"  [pexels-video] Error: {e}")
        return []

    results: list[VideoResult] = []
    for vid in data.get("videos", []):
        files = vid.get("video_files", [])
        best = _pick_best_file(files)
        if not best:
            continue
        preview = vid.get("video_pictures", [{}])[0].get("picture", "")
        results.append(VideoResult(
            url=best["link"],
            preview_url=preview,
            width=best.get("width", 1920),
            height=best.get("height", 1080),
            duration=vid.get("duration", 0),
            source="pexels",
            tags=vid.get("url", ""),
        ))
    return results


async def search_pixabay_video(
    client: httpx.AsyncClient, query: str, limit: int = 5
) -> list[VideoResult]:
    """Search Pixabay video API."""
    if not PIXABAY_KEY:
        return []
    params = {
        "key": PIXABAY_KEY,
        "q": query,
        "per_page": str(limit),
        "video_type": "film",
        "safesearch": "true",
    }
    try:
        resp = await client.get(
            "https://pixabay.com/api/videos/",
            params=params, timeout=15,
        )
        data = resp.json()
    except Exception as e:
        print(f"  [pixabay-video] Error: {e}")
        return []

    results: list[VideoResult] = []
    for hit in data.get("hits", []):
        videos = hit.get("videos", {})
        large = videos.get("large", {})
        medium = videos.get("medium", {})
        chosen = large if large.get("url") else medium
        if not chosen.get("url"):
            continue
        results.append(VideoResult(
            url=chosen["url"],
            preview_url=hit.get("userImageURL", ""),
            width=chosen.get("width", 1920),
            height=chosen.get("height", 1080),
            duration=hit.get("duration", 0),
            source="pixabay",
            tags=hit.get("tags", ""),
        ))
    return results


def _pick_best_file(files: list[dict]) -> dict | None:
    """Pick the best quality video file that's HD or close to it."""
    candidates = sorted(
        [f for f in files if f.get("link") and f.get("width", 0) >= 640],
        key=lambda f: abs(f.get("width", 0) - 1920),
    )
    return candidates[0] if candidates else None


async def search_videos(
    query: str, limit: int = 5
) -> list[VideoResult]:
    """Search both Pexels and Pixabay for videos."""
    async with httpx.AsyncClient(timeout=20) as client:
        tasks = [
            search_pexels_video(client, query, limit),
            search_pixabay_video(client, query, limit),
        ]
        all_results = await asyncio.gather(*tasks)
        merged = [r for batch in all_results for r in batch]
    return merged


async def search_videos_multi(
    queries: list[str], limit_per_query: int = 4
) -> list[VideoResult]:
    """Search multiple queries in parallel, deduplicate by URL."""
    async with httpx.AsyncClient(timeout=20) as client:
        tasks = []
        for q in queries:
            tasks.append(search_pexels_video(client, q, limit_per_query))
            tasks.append(search_pixabay_video(client, q, limit_per_query))
        all_batches = await asyncio.gather(*tasks)

    seen: set[str] = set()
    results: list[VideoResult] = []
    for batch in all_batches:
        for r in batch:
            if r.url not in seen:
                seen.add(r.url)
                results.append(r)
    return results


async def download_video(url: str, path: str) -> bool:
    """Download a video file."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                with open(path, "wb") as f:
                    f.write(resp.content)
                return True
        except Exception as e:
            print(f"  [download] Error: {e}")
    return False


def trim_video(
    input_path: str,
    output_path: str,
    duration_sec: float,
    target_w: int = VIDEO_WIDTH,
    target_h: int = VIDEO_HEIGHT,
) -> bool:
    """
    Trim and re-encode a video clip to exact duration and resolution.
    Applies scale + crop to hit 1920x1080 regardless of source aspect ratio.
    """
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},"
        f"fps={VIDEO_FPS},"
        f"format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", f"{duration_sec:.2f}",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "22",
        "-an",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"  [trim] ffmpeg error: {result.stderr[-300:]}")
        return result.returncode == 0
    except Exception as e:
        print(f"  [trim] Exception: {e}")
        return False


def vlm_verify(
    video_path: str,
    scene_description: str,
    threshold: float = 0.6,
) -> tuple[bool, float, str]:
    """
    Use Gemini Flash as a VLM to verify a video matches the scene intent.
    Extracts a frame, sends to Gemini with the description, gets a 0-1 score.
    
    Returns (passed, score, reason).
    """
    if not GEMINI_KEY:
        return True, 1.0, "No API key, skipping verification"

    frame_path = _extract_frame(video_path)
    if not frame_path:
        return True, 0.5, "Could not extract frame"

    try:
        from google import genai
        from google.genai import types
        import base64

        client = genai.Client(api_key=GEMINI_KEY)

        with open(frame_path, "rb") as f:
            img_bytes = f.read()
        img_b64 = base64.b64encode(img_bytes).decode()

        prompt = (
            f"You are a strict quality gate for a documentary video editor. "
            f"Rate how well this image matches the scene description.\n\n"
            f"SCENE: {scene_description}\n\n"
            f"STRICT REJECTION RULES (score 0.0 if ANY apply):\n"
            f"- Image contains prominent readable text (words, signs, titles, "
            f"labels like 'Love', 'Success', book titles) NOT in the description\n"
            f"- Image is from a completely different subject, era, or culture\n"
            f"- Image is a tourist card, postcard, stereoscope, or novelty item\n"
            f"- Image is blurry, out of focus, or too low quality\n"
            f"- Image shows modern digital graphics (Matrix style, neon, etc.) "
            f"when scene describes historical content\n"
            f"- ATMOSPHERE MISMATCH: If the scene describes 'darkness', 'deep "
            f"ocean', 'pitch black', etc., reject bright sunlit imagery. If the "
            f"scene describes underwater/deep sea, reject surface water, land, "
            f"recreational scuba divers in sunlit caves, or above-water scenery\n\n"
            f"SCORING:\n"
            f"0.0 = any rejection rule triggered\n"
            f"0.3 = vaguely related but wrong specific subject\n"
            f"0.6 = related topic but not quite right\n"
            f"0.8 = good contextual match\n"
            f"1.0 = perfect match\n\n"
            f"Reply with ONLY a JSON object: "
            f'{{"score": <0.0 to 1.0>, "reason": "<brief explanation>"}}'
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                        {"text": prompt},
                    ],
                }
            ],
        )

        import json, re
        text = response.text.strip()
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text)
        score = float(result.get("score", 0.5))
        reason = result.get("reason", "")

        return score >= threshold, score, reason

    except Exception as e:
        print(f"  [vlm] Verification error: {e}")
        return True, 0.5, f"Verification error: {e}"
    finally:
        if frame_path and os.path.exists(frame_path):
            os.unlink(frame_path)


def _extract_frame(video_path: str, at_sec: float = 1.0) -> str | None:
    """Extract a single frame from a video for VLM verification."""
    fd, frame_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(at_sec),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "3",
        frame_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and os.path.getsize(frame_path) > 0:
            return frame_path
    except Exception:
        pass
    if os.path.exists(frame_path):
        os.unlink(frame_path)
    return None
