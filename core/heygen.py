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
MAX_WAIT = 3600  # Long multi-scene scripts often need 20–45+ min
HEYGEN_MAX_CHARS_PER_SCENE = 4800  # Hard limit is 5000; leave headroom
HEYGEN_MAX_SCENES = 50


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

    if len(chunks) > HEYGEN_MAX_SCENES:
        # HeyGen allows max 50 scenes — merge overflow into last allowed scenes
        head, tail = chunks[: HEYGEN_MAX_SCENES - 1], chunks[HEYGEN_MAX_SCENES - 1 :]
        merged = " ".join(tail)
        # Re-chunk overflow if still huge
        while merged and len(head) < HEYGEN_MAX_SCENES:
            head.append(merged[:max_chars])
            merged = merged[max_chars:]
        chunks = head[:HEYGEN_MAX_SCENES]
    return chunks


def _normalize_background(background: dict | str | None) -> dict | None:
    """Accept a color hex or HeyGen background object."""
    if not background:
        return None
    if isinstance(background, str):
        color = background.strip()
        if not color:
            return None
        if not color.startswith("#"):
            color = f"#{color}"
        return {"type": "color", "value": color}
    if isinstance(background, dict):
        # v2 used {"type":"color","value":"#fff"}; v3 studio docs use "color"
        out = dict(background)
        if out.get("type") == "color":
            color = (out.get("color") or out.get("value") or "").strip()
            if color and not color.startswith("#"):
                color = f"#{color}"
            if color:
                # Send both keys — v2 expects value, v3 studio accepts color
                return {"type": "color", "color": color, "value": color}
        return out
    return None


def _voice_settings_payload(
    voice_speed: float | None = None,
    voice_pitch: float | None = None,
) -> dict | None:
    settings: dict = {}
    if voice_speed is not None:
        settings["speed"] = max(0.5, min(1.5, float(voice_speed)))
    if voice_pitch is not None:
        settings["pitch"] = max(-50.0, min(50.0, float(voice_pitch)))
    return settings or None


def _engine_payload(engine: str | None) -> dict | None:
    raw = (engine or "").strip().lower().replace("-", "_")
    if not raw or raw in ("default", "avatar_iv", "iv"):
        return None  # HeyGen defaults to Avatar IV
    if raw in ("avatar_v", "v", "v5"):
        return {"type": "avatar_v"}
    if raw in ("avatar_iii", "iii", "v3", "3"):
        return {"type": "avatar_iii"}
    if raw.startswith("avatar_"):
        return {"type": raw}
    return {"type": f"avatar_{raw}"} if raw else None


def _aspect_to_dimensions(aspect_ratio: str) -> tuple[int, int]:
    ar = (aspect_ratio or "16:9").strip()
    if ar == "9:16":
        return 1080, 1920
    if ar == "1:1":
        return 1080, 1080
    return 1920, 1080


def normalize_heygen_scenes(
    scenes: list[dict] | None,
    *,
    script_text: str = "",
    avatar_id: str = "",
    voice_id: str = "",
) -> list[dict]:
    """Normalize user/API scenes or fall back to auto-chunked avatar scenes."""
    out: list[dict] = []
    if scenes:
        for raw in scenes[:HEYGEN_MAX_SCENES]:
            if not isinstance(raw, dict):
                continue
            stype = (raw.get("type") or "avatar").strip().lower()
            if stype in ("avatar", "avatar_video"):
                text = (raw.get("script") or raw.get("text") or "").strip()
                if not text:
                    continue
                out.append({
                    "type": "avatar",
                    "script": text[:HEYGEN_MAX_CHARS_PER_SCENE],
                    "avatar_id": (raw.get("avatar_id") or avatar_id or "").strip(),
                    "voice_id": (raw.get("voice_id") or voice_id or "").strip(),
                    "background": raw.get("background"),
                    "image_url": "",
                })
            elif stype == "image":
                url = (raw.get("image_url") or raw.get("url") or "").strip()
                text = (raw.get("script") or raw.get("text") or "").strip()
                if not url:
                    continue
                out.append({
                    "type": "image",
                    "script": text[:HEYGEN_MAX_CHARS_PER_SCENE],
                    "avatar_id": "",
                    "voice_id": (raw.get("voice_id") or voice_id or "").strip(),
                    "background": None,
                    "image_url": url,
                    "duration": raw.get("duration"),
                })
            elif stype == "video":
                url = (raw.get("video_url") or raw.get("url") or "").strip()
                if not url:
                    continue
                out.append({
                    "type": "video",
                    "script": (raw.get("script") or "").strip()[:HEYGEN_MAX_CHARS_PER_SCENE],
                    "avatar_id": "",
                    "voice_id": (raw.get("voice_id") or voice_id or "").strip(),
                    "background": None,
                    "image_url": "",
                    "video_url": url,
                })
    if out:
        return out

    chunks = _chunk_script_for_heygen(script_text)
    return [
        {
            "type": "avatar",
            "script": chunk,
            "avatar_id": avatar_id,
            "voice_id": voice_id,
            "background": None,
            "image_url": "",
        }
        for chunk in chunks
    ]


def scenes_need_studio_only(scenes: list[dict] | None) -> bool:
    """True when the user mixed image/video scenes — skip CR illustration interleave."""
    return any(
        isinstance(s, dict) and (s.get("type") or "").lower() in ("image", "video")
        for s in (scenes or [])
    )


def _build_v3_studio_scenes(
    scenes: list[dict],
    *,
    default_background: dict | None,
    voice_settings: dict | None,
    engine: dict | None,
    motion_prompt: str | None,
    expressiveness: str | None,
) -> list[dict]:
    built: list[dict] = []
    for scene in scenes:
        stype = scene.get("type") or "avatar"
        if stype == "avatar":
            inp: dict = {
                "type": "avatar",
                "avatar_id": scene["avatar_id"],
                "script": scene["script"],
                "voice_id": scene["voice_id"],
            }
            bg = _normalize_background(scene.get("background") or default_background)
            if bg:
                # v3 studio background uses color key
                inp["background"] = {"type": "color", "color": bg.get("color") or bg.get("value")}
            if voice_settings:
                inp["voice_settings"] = voice_settings
            if engine:
                inp["engine"] = engine
            if motion_prompt:
                inp["motion_prompt"] = motion_prompt.strip()
            if expressiveness:
                inp["expressiveness"] = expressiveness
            built.append({"type": "avatar_video", "input": inp})
        elif stype == "image":
            img: dict = {
                "type": "image",
                "source": {"type": "url", "url": scene["image_url"]},
            }
            if scene.get("script") and scene.get("voice_id"):
                img["script"] = scene["script"]
                img["voice_id"] = scene["voice_id"]
                if voice_settings:
                    img["voice_settings"] = voice_settings
            else:
                dur = scene.get("duration")
                img["duration"] = float(dur) if dur else 3.0
            built.append(img)
        elif stype == "video":
            vid: dict = {
                "type": "video",
                "source": {"type": "url", "url": scene.get("video_url") or ""},
            }
            if scene.get("script") and scene.get("voice_id"):
                vid["script"] = scene["script"]
                vid["voice_id"] = scene["voice_id"]
            built.append(vid)
    return built


def _build_v2_video_inputs(
    scenes: list[dict],
    *,
    default_background: dict | None,
) -> list[dict]:
    """v2 only supports avatar speaking scenes — drop image/video."""
    inputs: list[dict] = []
    for scene in scenes:
        if (scene.get("type") or "avatar") != "avatar":
            continue
        item = {
            "character": {
                "type": "avatar",
                "avatar_id": scene["avatar_id"],
                "avatar_style": "normal",
            },
            "voice": {
                "type": "text",
                "input_text": scene["script"],
                "voice_id": scene["voice_id"],
            },
        }
        bg = _normalize_background(scene.get("background") or default_background)
        if bg:
            item["background"] = {"type": "color", "value": bg.get("value") or bg.get("color")}
        inputs.append(item)
    return inputs


def _parse_create_response(resp: httpx.Response) -> AvatarVideo:
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {}
    if resp.status_code != 200 or data.get("error"):
        err = data.get("error", {})
        err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        if not err_msg or err_msg == "{}":
            err_msg = (resp.text or "")[:240] or f"HTTP {resp.status_code}"
        return AvatarVideo(video_id="", status="failed", error=err_msg)
    video_id = (data.get("data") or {}).get("video_id", "") or ""
    if not video_id:
        return AvatarVideo(video_id="", status="failed", error="HeyGen returned no video_id")
    return AvatarVideo(video_id=video_id, status="pending")


def create_avatar_video(
    script_text: str,
    avatar_id: str,
    voice_id: str,
    width: int = 1920,
    height: int = 1080,
    caption: bool = False,
    background: dict | str | None = None,
    api_key: str | None = None,
    *,
    scenes: list[dict] | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "1080p",
    voice_speed: float | None = None,
    voice_pitch: float | None = None,
    engine: str | None = None,
    motion_prompt: str | None = None,
    expressiveness: str | None = None,
    title: str | None = None,
) -> AvatarVideo:
    """
    Create an avatar/studio video via HeyGen.

    Prefers v3 `POST /v3/videos` with `type: studio` (multi-scene + knobs).
    Falls back to legacy v2 `POST /v2/video/generate` when v3 rejects the body.
    """
    norm_scenes = normalize_heygen_scenes(
        scenes, script_text=script_text, avatar_id=avatar_id, voice_id=voice_id,
    )
    if not norm_scenes:
        return AvatarVideo(video_id="", status="failed", error="Script is empty")

    bg = _normalize_background(background)
    voice_settings = _voice_settings_payload(voice_speed, voice_pitch)
    engine_obj = _engine_payload(engine)
    ar = (aspect_ratio or "16:9").strip() or "16:9"
    res = (resolution or "1080p").strip() or "1080p"
    if width == 1920 and height == 1080 and ar:
        width, height = _aspect_to_dimensions(ar)

    print(
        f"[heygen] Creating video with {len(norm_scenes)} scene(s), "
        f"aspect={ar} res={res} caption={bool(caption)}"
    )

    v3_scenes = _build_v3_studio_scenes(
        norm_scenes,
        default_background=bg,
        voice_settings=voice_settings,
        engine=engine_obj,
        motion_prompt=motion_prompt,
        expressiveness=expressiveness,
    )
    caption_obj = None
    if caption:
        caption_obj = {"file_format": "srt", "style": "default"}

    v3_payload: dict = {
        "type": "studio",
        "scenes": v3_scenes,
        "aspect_ratio": ar,
        "resolution": res,
    }
    if title:
        v3_payload["title"] = title
    if caption_obj:
        v3_payload["caption"] = caption_obj

    try:
        resp = httpx.post(
            f"{HEYGEN_API}/v3/videos",
            headers=_headers(api_key),
            json=v3_payload,
            timeout=60,
        )
        result = _parse_create_response(resp)
        if result.status != "failed":
            print(f"[heygen] Video created (v3 studio): {result.video_id}")
            return result
        print(f"[heygen] v3 studio create failed ({result.error}); trying v2 fallback")
    except Exception as e:
        print(f"[heygen] v3 studio request error: {e}; trying v2 fallback")

    # v2 fallback — avatar scenes only
    video_inputs = _build_v2_video_inputs(norm_scenes, default_background=bg)
    if not video_inputs:
        return AvatarVideo(
            video_id="",
            status="failed",
            error="HeyGen v3 create failed and v2 cannot render image/video scenes",
        )
    v2_payload = {
        "video_inputs": video_inputs,
        "dimension": {"width": width, "height": height},
        "caption": bool(caption),
    }
    resp = httpx.post(
        f"{HEYGEN_API}/v2/video/generate",
        headers=_headers(api_key),
        json=v2_payload,
        timeout=60,
    )
    result = _parse_create_response(resp)
    if result.status == "failed":
        print(f"[heygen] Error creating video: {result.error}")
        return result
    print(f"[heygen] Video created (v2): {result.video_id}")
    return result


def create_avatar_video_with_audio(
    audio_url: str,
    avatar_id: str,
    width: int = 1920,
    height: int = 1080,
    api_key: str | None = None,
) -> AvatarVideo:
    """Create an avatar video from an audio URL (lip-sync mode)."""
    # Prefer v3 single-avatar mode
    try:
        resp = httpx.post(
            f"{HEYGEN_API}/v3/videos",
            headers=_headers(api_key),
            json={
                "type": "avatar",
                "avatar_id": avatar_id,
                "audio_url": audio_url,
                "aspect_ratio": "16:9" if width >= height else "9:16",
                "resolution": "1080p",
            },
            timeout=30,
        )
        result = _parse_create_response(resp)
        if result.status != "failed":
            print(f"[heygen] Video created (v3 audio): {result.video_id}")
            return result
    except Exception as e:
        print(f"[heygen] v3 audio create failed: {e}")

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
    result = _parse_create_response(resp)
    if result.status == "failed":
        print(f"[heygen] Error creating video: {result.error}")
    else:
        print(f"[heygen] Video created (audio mode): {result.video_id}")
    return result


def _poll_interval_for_elapsed(elapsed: float, base: int = POLL_INTERVAL) -> float:
    """Adaptive poll: snappy early, gentler after 10 minutes."""
    if elapsed < 600:
        return float(base)
    if elapsed < 1800:
        return float(max(base, 20))
    return float(max(base, 30))


def check_status(video_id: str, api_key: str | None = None) -> AvatarVideo:
    """Check the rendering status of a HeyGen video (v3, then legacy v1)."""
    headers = _headers(api_key)

    # v3
    try:
        resp = httpx.get(
            f"{HEYGEN_API}/v3/videos/{video_id}",
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            payload = resp.json() if resp.content else {}
            data = payload.get("data") or payload
            status = (data.get("status") or "unknown").lower()
            # Normalize aliases
            if status in ("success", "done"):
                status = "completed"
            err = data.get("error") or data.get("error_message") or ""
            if isinstance(err, dict):
                err = err.get("message") or str(err)
            return AvatarVideo(
                video_id=video_id,
                status=status,
                video_url=data.get("video_url") or data.get("url") or "",
                duration=float(data.get("duration") or 0),
                error=str(err or ""),
            )
    except Exception as e:
        print(f"[heygen] v3 status failed, trying v1: {e}")

    resp = httpx.get(
        f"{HEYGEN_API}/v1/video_status.get",
        params={"video_id": video_id},
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    err = data.get("error", "") or ""
    if isinstance(err, dict):
        err = err.get("message") or str(err)
    return AvatarVideo(
        video_id=video_id,
        status=data.get("status", "unknown"),
        video_url=data.get("video_url", ""),
        duration=data.get("duration", 0) or 0,
        error=str(err),
    )


def wait_for_completion(
    video_id: str,
    poll_interval: int = POLL_INTERVAL,
    timeout: int = MAX_WAIT,
    progress_callback=None,
    api_key: str | None = None,
) -> AvatarVideo:
    """Poll HeyGen until the video is completed or fails.

    On wall-clock timeout, does one final status check — if HeyGen finished
    just after our last poll, we still succeed. Otherwise raises TimeoutError
    with the video id so the user can recover from HeyGen Recents.
    """
    start = time.time()
    last: AvatarVideo | None = None
    while True:
        elapsed = time.time() - start
        if elapsed >= timeout:
            break
        last = check_status(video_id, api_key=api_key)
        if progress_callback:
            mins = elapsed / 60.0
            hint = ""
            if elapsed >= 600:
                hint = " — long scripts often need 15–45+ min"
            progress_callback(
                f"HeyGen status: {last.status} ({mins:.0f}m){hint}"
            )
        else:
            print(f"[heygen] Status: {last.status} ({elapsed:.0f}s)")

        if last.status == "completed":
            return last
        if last.status == "failed":
            raise RuntimeError(f"HeyGen video failed: {last.error}")

        sleep_for = _poll_interval_for_elapsed(elapsed, poll_interval)
        # Don't sleep past the timeout window
        remaining = timeout - (time.time() - start)
        if remaining <= 0:
            break
        time.sleep(min(sleep_for, remaining))

    # Final recovery check — render may have completed after last poll
    try:
        final = check_status(video_id, api_key=api_key)
    except Exception as e:
        print(f"[heygen] final status check failed: {e}")
        final = last
    if final and final.status == "completed":
        if progress_callback:
            progress_callback("HeyGen status: completed (recovered after wait window)")
        return final
    if final and final.status == "failed":
        raise RuntimeError(f"HeyGen video failed: {final.error}")

    raise TimeoutError(
        f"HeyGen video {video_id} timed out after {timeout}s "
        f"(still {getattr(final, 'status', 'processing')}). "
        f"It may still finish in HeyGen — check Recents for {video_id}, "
        f"or retry the cook once it shows completed."
    )


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
