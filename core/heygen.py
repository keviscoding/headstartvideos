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


def sanitize_api_key(raw: str) -> str:
    """Strip paste junk (Bearer prefix, quotes, zero-width chars, whitespace)."""
    key = (raw or "").strip()
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\xa0"):
        key = key.replace(ch, "")
    key = key.strip().strip('"').strip("'")
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if key.lower().startswith("x-api-key:"):
        key = key.split(":", 1)[1].strip()
    # Collapse accidental newlines/spaces from password managers / chat apps
    key = "".join(key.split())
    return key


def _resolve_key(api_key: str | None = None) -> str:
    key = sanitize_api_key(api_key or HEYGEN_KEY or "")
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


def test_api_key(api_key: str) -> tuple[bool, str]:
    """
    Validate a HeyGen API key.

    Uses the official account probe first (`/v3/users/me`, then legacy
    `/v1/user/me`). Listing avatars used to be the only check, which false-
    failed when the key was valid but the avatars call timed out or the
    account had API access without avatar catalog permissions.
    Returns (ok, error_message).
    """
    key = sanitize_api_key(api_key)
    if not key:
        return False, "Paste your HeyGen API key first."
    if len(key) < 16:
        return False, "That doesn’t look like a full HeyGen API key. Paste the whole token."

    headers = {"X-Api-Key": key, "Content-Type": "application/json"}
    probes = (
        f"{HEYGEN_API}/v3/users/me",
        f"{HEYGEN_API}/v1/user/me",
        f"{HEYGEN_API}/v2/avatars",
    )
    last_err = "HeyGen rejected that key."
    for url in probes:
        try:
            resp = httpx.get(url, headers=headers, timeout=25)
        except httpx.TimeoutException:
            last_err = "HeyGen timed out while checking the key. Try again in a moment."
            continue
        except Exception as e:
            last_err = f"Could not reach HeyGen ({e}). Try again."
            continue

        body = (resp.text or "").strip()
        snippet = body.replace("\n", " ")[:180]
        if resp.status_code in (401, 403):
            return False, (
                "HeyGen says this API key is invalid or revoked. "
                "Generate a new key at app.heygen.com → API (sidebar), then paste it here."
            )
        if resp.status_code == 200:
            return True, ""
        # 429 / 5xx: key might still be fine — don't hard-fail on rate limits
        if resp.status_code == 429:
            return True, "Key looks accepted, but HeyGen is rate-limiting right now."
        if resp.status_code >= 500:
            last_err = f"HeyGen is having trouble ({resp.status_code}). Try again shortly."
            continue
        last_err = snippet or f"HeyGen returned HTTP {resp.status_code}."
    return False, last_err


def list_avatars(api_key: str | None = None) -> list[dict]:
    """Fetch selectable HeyGen avatars (looks) for the picker.

    `/v2/avatars` hangs/times out for many accounts while `/v2/voices` still
    works — that is exactly the "voices load, avatars fail" failure mode.
    v3 exposes *looks*; the look `id` is what video create expects as
    `avatar_id` (not the group id).
    """
    headers = _headers(api_key)
    looks: list[dict] = []

    # Prefer classic studio talking-heads, then fill with photo/digital twins.
    for avatar_type in ("studio_avatar", "photo_avatar", "digital_twin"):
        token = None
        for _ in range(4):  # up to 200 looks per type
            params: dict[str, str | int] = {
                "limit": 50,
                "ownership": "public",
                "avatar_type": avatar_type,
            }
            if token:
                params["token"] = token
            try:
                resp = httpx.get(
                    f"{HEYGEN_API}/v3/avatars/looks",
                    headers=headers,
                    params=params,
                    timeout=30,
                )
            except httpx.TimeoutException as e:
                print(f"[heygen] v3 looks timeout ({avatar_type}): {e}")
                break
            if resp.status_code != 200:
                print(
                    f"[heygen] v3 looks HTTP {resp.status_code} ({avatar_type}): "
                    f"{(resp.text or '')[:160]}"
                )
                break
            payload = resp.json() if resp.content else {}
            batch = payload.get("data") or []
            if not isinstance(batch, list):
                break
            looks.extend(b for b in batch if isinstance(b, dict))
            if not payload.get("has_more"):
                break
            token = payload.get("next_token")
            if not token:
                break
        if len(looks) >= 80:
            break

    # One card per character — keep the first look of each group.
    by_group: dict[str, dict] = {}
    for look in looks:
        look_id = (look.get("id") or "").strip()
        if not look_id:
            continue
        group = (look.get("group_id") or look_id).strip()
        if group in by_group:
            continue
        status = (look.get("status") or "completed").lower()
        if status and status not in ("completed", "active", ""):
            continue
        by_group[group] = {
            "avatar_id": look_id,
            "avatar_name": look.get("name") or look_id,
            "preview_url": look.get("preview_image_url") or "",
            "gender": look.get("gender") or "",
            "default_voice_id": look.get("default_voice_id") or "",
            "avatar_type": look.get("avatar_type") or "",
        }

    avatars = list(by_group.values())
    if avatars:
        return avatars

    # Last resort: legacy v2 (often times out — keep a short fuse).
    try:
        resp = httpx.get(
            f"{HEYGEN_API}/v2/avatars",
            headers=headers,
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json()
        legacy = data.get("data", {}).get("avatars", [])
        return [
            {
                "avatar_id": a.get("avatar_id", ""),
                "avatar_name": a.get("avatar_name", "") or a.get("name", ""),
                "preview_url": a.get("preview_image_url", "") or a.get("preview_url", ""),
                "gender": a.get("gender", "") or "",
                "default_voice_id": a.get("default_voice_id", "") or "",
                "avatar_type": "legacy_v2",
            }
            for a in legacy
            if a.get("avatar_id")
        ]
    except Exception as e:
        print(f"[heygen] v2 avatars fallback failed: {e}")
        raise RuntimeError(
            "Could not load HeyGen avatars. Paste an avatar/look ID below, "
            "or retry in a moment."
        ) from e


def list_voices(api_key: str | None = None) -> list[dict]:
    """Fetch available voices from HeyGen."""
    # v2 voices still works and is what the UI already expects.
    try:
        resp = httpx.get(
            f"{HEYGEN_API}/v2/voices",
            headers=_headers(api_key),
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        voices = data.get("data", {}).get("voices", [])
        out = [
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
        if out:
            return out
    except Exception as e:
        print(f"[heygen] v2 voices failed, trying v3: {e}")

    # v3 fallback (different field names / pagination)
    resp = httpx.get(
        f"{HEYGEN_API}/v3/voices",
        headers=_headers(api_key),
        params={"limit": 50},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    voices = data.get("data") or []
    return [
        {
            "voice_id": v.get("id") or v.get("voice_id") or "",
            "display_name": v.get("name") or v.get("display_name") or "",
            "language": v.get("language") or "",
            "gender": v.get("gender") or "",
            "preview_audio": v.get("preview_audio_url") or v.get("preview_audio") or "",
        }
        for v in voices
        if (v.get("id") or v.get("voice_id"))
    ]


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
