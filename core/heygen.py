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
MAX_WAIT = 600


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
    The avatar speaks the full script as a single scene.
    """
    scene = {
        "character": {
            "type": "avatar",
            "avatar_id": avatar_id,
            "avatar_style": "normal",
        },
        "voice": {
            "type": "text",
            "input_text": script_text,
            "voice_id": voice_id,
        },
    }

    if background:
        scene["background"] = background

    payload = {
        "video_inputs": [scene],
        "dimension": {"width": width, "height": height},
        "caption": caption,
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
