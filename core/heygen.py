"""
HeyGen API integration for AI avatar video generation.
Uses the v2 Studio API for multi-scene avatar videos.
Callers pass api_key (BYOK) — falls back to config.HEYGEN_KEY for CLI/admin.
"""

from __future__ import annotations
import time
import httpx
from dataclasses import dataclass
from pathlib import Path
from config import HEYGEN_KEY, HEYGEN_API

POLL_INTERVAL = 10
MAX_WAIT = 1200  # Multi-scene long scripts can take longer than 10 min
HEYGEN_MAX_CHARS_PER_SCENE = 4800  # Hard limit is 5000; leave headroom


@dataclass
class AvatarVideo:
    video_id: str
    status: str         # pending | processing | completed | failed
    video_url: str = ""
    duration: float = 0
    error: str = ""


def _resolve_key(api_key: str | None = None) -> str:
    key = (api_key or HEYGEN_KEY or "").strip()
    if not key:
        raise ValueError(
            "HeyGen API key required — add yours in Settings → Integrations, "
            "or set HEYGEN_KEY for local/admin use."
        )
    return key


def _headers(api_key: str | None = None) -> dict:
    return {
        "X-Api-Key": _resolve_key(api_key),
        "Content-Type": "application/json",
    }


def list_avatars(api_key: str | None = None) -> list[dict]:
    """Fetch available avatars from HeyGen (public + account private)."""
    resp = httpx.get(
        f"{HEYGEN_API}/v2/avatars",
        headers=_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    avatars = data.get("data", {}).get("avatars", [])
    return [
        {
            "avatar_id": a.get("avatar_id", ""),
            "avatar_name": a.get("avatar_name", "") or a.get("name", ""),
            "preview_url": a.get("preview_image_url", "") or a.get("preview_url", ""),
            "gender": a.get("gender", "") or "",
            "default_voice_id": a.get("default_voice_id", "") or "",
        }
        for a in avatars
        if a.get("avatar_id")
    ]


def list_voices(api_key: str | None = None) -> list[dict]:
    """Fetch available voices from HeyGen."""
    resp = httpx.get(
        f"{HEYGEN_API}/v2/voices",
        headers=_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    voices = data.get("data", {}).get("voices", [])
    return [
        {
            "voice_id": v.get("voice_id", ""),
            "display_name": v.get("display_name", "") or v.get("name", ""),
            "language": v.get("language", ""),
            "gender": v.get("gender", ""),
            "preview_audio": v.get("preview_audio", "") or v.get("preview_audio_url", ""),
        }
        for v in voices
        if v.get("voice_id")
    ]


def test_api_key(api_key: str) -> bool:
    """Return True if the key can list avatars."""
    try:
        list_avatars(api_key=api_key)
        return True
    except Exception:
        return False


def _chunk_script_for_heygen(script_text: str, max_chars: int = HEYGEN_MAX_CHARS_PER_SCENE) -> list[str]:
    """Split narration into scenes under HeyGen's 5000-char per-scene limit."""
    text = (script_text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Prefer sentence boundaries; fall back to hard wraps.
    import re
    parts = re.split(r'(?<=[.!?])\s+', text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > max_chars:
            if buf:
                chunks.append(buf.strip())
                buf = ""
            for i in range(0, len(part), max_chars):
                chunks.append(part[i:i + max_chars])
            continue
        candidate = f"{buf} {part}".strip() if buf else part
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            chunks.append(buf.strip())
            buf = part
    if buf.strip():
        chunks.append(buf.strip())

    if len(chunks) > 50:
        # HeyGen allows max 50 scenes — merge overflow into last allowed scenes
        head, tail = chunks[:49], chunks[49:]
        merged = " ".join(tail)
        # Re-chunk overflow if still huge
        while merged and len(head) < 50:
            head.append(merged[:max_chars])
            merged = merged[max_chars:]
        chunks = head[:50]
    return chunks


def create_avatar_video(
    script_text: str,
    avatar_id: str,
    voice_id: str,
    width: int = 1920,
    height: int = 1080,
    caption: bool = False,
    background: dict | None = None,
    api_key: str | None = None,
) -> AvatarVideo:
    """
    Create an avatar video from a script using HeyGen v2 Studio API.
    Long scripts are split into multiple scenes (max 5000 chars each).
    """
    chunks = _chunk_script_for_heygen(script_text)
    if not chunks:
        return AvatarVideo(video_id="", status="failed", error="Script is empty")

    video_inputs = []
    for chunk in chunks:
        scene = {
            "character": {
                "type": "avatar",
                "avatar_id": avatar_id,
                "avatar_style": "normal",
            },
            "voice": {
                "type": "text",
                "input_text": chunk,
                "voice_id": voice_id,
            },
        }
        if background:
            scene["background"] = background
        video_inputs.append(scene)

    print(f"[heygen] Creating video with {len(video_inputs)} scene(s), "
          f"{len(script_text)} chars total")

    payload = {
        "video_inputs": video_inputs,
        "dimension": {"width": width, "height": height},
        "caption": caption,
    }

    resp = httpx.post(
        f"{HEYGEN_API}/v2/video/generate",
        headers=_headers(api_key),
        json=payload,
        timeout=60,
    )
    data = resp.json()

    if resp.status_code != 200 or data.get("error"):
        err = data.get("error", {})
        err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        print(f"[heygen] Error creating video: {err_msg}")
        return AvatarVideo(
            video_id="",
            status="failed",
            error=err_msg,
        )

    video_id = data.get("data", {}).get("video_id", "")
    print(f"[heygen] Video created: {video_id}")
    return AvatarVideo(video_id=video_id, status="pending")


def create_avatar_video_with_audio(
    audio_url: str,
    avatar_id: str,
    width: int = 1920,
    height: int = 1080,
    api_key: str | None = None,
) -> AvatarVideo:
    """Create an avatar video from an audio URL (lip-sync mode)."""
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "audio",
                    "audio_url": audio_url,
                },
            }
        ],
        "dimension": {"width": width, "height": height},
    }

    resp = httpx.post(
        f"{HEYGEN_API}/v2/video/generate",
        headers=_headers(api_key),
        json=payload,
        timeout=30,
    )
    data = resp.json()

    if resp.status_code != 200 or data.get("error"):
        err = data.get("error", {})
        err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        print(f"[heygen] Error creating video: {err_msg}")
        return AvatarVideo(video_id="", status="failed", error=err_msg)

    video_id = data.get("data", {}).get("video_id", "")
    print(f"[heygen] Video created (audio mode): {video_id}")
    return AvatarVideo(video_id=video_id, status="pending")


def check_status(video_id: str, api_key: str | None = None) -> AvatarVideo:
    """Check the rendering status of a HeyGen video."""
    resp = httpx.get(
        f"{HEYGEN_API}/v1/video_status.get",
        params={"video_id": video_id},
        headers=_headers(api_key),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})

    return AvatarVideo(
        video_id=video_id,
        status=data.get("status", "unknown"),
        video_url=data.get("video_url", ""),
        duration=data.get("duration", 0),
        error=data.get("error", "") or "",
    )


def wait_for_completion(
    video_id: str,
    poll_interval: int = POLL_INTERVAL,
    timeout: int = MAX_WAIT,
    progress_callback=None,
    api_key: str | None = None,
) -> AvatarVideo:
    """Poll HeyGen until the video is completed or fails."""
    start = time.time()
    while time.time() - start < timeout:
        result = check_status(video_id, api_key=api_key)
        elapsed = time.time() - start
        if progress_callback:
            progress_callback(f"HeyGen status: {result.status} ({elapsed:.0f}s)")
        else:
            print(f"[heygen] Status: {result.status} ({elapsed:.0f}s)")

        if result.status == "completed":
            return result
        if result.status == "failed":
            raise RuntimeError(f"HeyGen video failed: {result.error}")

        time.sleep(poll_interval)

    raise TimeoutError(f"HeyGen video {video_id} timed out after {timeout}s")


def download_video(video_url: str, output_path: str) -> str:
    """Download the rendered avatar video to a local path."""
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        resp = client.get(video_url)
        resp.raise_for_status()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(resp.content)
    print(f"[heygen] Downloaded avatar video: {output_path}")
    return output_path
