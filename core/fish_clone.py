"""
Fish Audio voice clone — rights-gated only.

Creates a persistent private Fish TTS model from a short reference sample.
Requires explicit consent that the caller owns the voice or has written permission.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

import config

FISH_API = "https://api.fish.audio"
# Soft cap: Fish recommends clean 10–30s; accept up to 60s after trim.
MAX_SAMPLE_SEC = 45
MIN_SAMPLE_SEC = 5


def _tts_model() -> str:
    return (getattr(config, "FISH_TTS_MODEL", None) or "s2.1-pro-free").strip() or "s2.1-pro-free"


def clone_enabled() -> bool:
    """True when Fish key is present and VOICE_CLONE_ENABLED is not forced off."""
    if not (getattr(config, "FISH_API_KEY", "") or "").strip():
        return False
    return bool(getattr(config, "VOICE_CLONE_ENABLED", False))


def _headers() -> dict:
    key = (getattr(config, "FISH_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("FISH_API_KEY is not configured")
    return {"Authorization": f"Bearer {key}"}


def _probe_duration(path: str) -> float:
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


# Screen recordings / phone videos are the easy path when YouTube is blocked.
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac", ".webm"}


def normalize_sample(src_path: str, out_dir: str | Path | None = None) -> str:
    """Pull audio (from audio OR video/screen recording), mono 24k WAV, trim to MAX_SAMPLE_SEC."""
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="fish_sample_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "sample.wav"
    src = Path(src_path)
    # -vn drops video; -map 0:a:0? takes the first audio track when present.
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vn",
        "-map", "0:a:0?",
        "-t", str(MAX_SAMPLE_SEC),
        "-ar", "24000", "-ac", "1",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not out.is_file() or out.stat().st_size < 1000:
        err = (result.stderr or result.stdout or "").strip()[-240:]
        kind = "screen recording / video" if src.suffix.lower() in _VIDEO_EXTS else "file"
        raise ValueError(
            f"Could not extract speech from that {kind}. "
            f"Use a clip with clear spoken audio (at least {MIN_SAMPLE_SEC}s). "
            f"{err}"
        )
    dur = _probe_duration(str(out))
    if dur < MIN_SAMPLE_SEC:
        raise ValueError(
            f"Voice sample too short ({dur:.1f}s). Use at least {MIN_SAMPLE_SEC}s of clear speech "
            f"(screen recordings and YouTube imports are auto-trimmed to {MAX_SAMPLE_SEC}s)."
        )
    return str(out)


def _yt_dlp_err_text(stderr: str, stdout: str) -> str:
    """Pull the useful ERROR line out of noisy yt-dlp/ffmpeg dumps."""
    blob = "\n".join(x for x in (stderr or "", stdout or "") if x)
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    err_lines = [ln for ln in lines if "ERROR:" in ln or "Sign in to confirm" in ln]
    if err_lines:
        return err_lines[-1][-300:]
    return (lines[-1] if lines else "YouTube download failed")[-300:]


def _yt_bot_blocked(err: str) -> bool:
    e = (err or "").lower()
    return any(
        s in e
        for s in (
            "sign in to confirm",
            "not a bot",
            "cookies-from-browser",
            "confirm you're not a bot",
            "confirm you’re not a bot",
            "login required",
            "http error 403",
        )
    )


def extract_youtube_audio(youtube_url: str, out_dir: str | Path | None = None) -> str:
    """Download audio from a YouTube URL (requires yt-dlp).

    YouTube often blocks datacenter IPs. We try several player clients, then
    optional cookies from YOUTUBE_COOKIES_FILE. If still blocked, raise a clear
    upload-fallback error (no raw yt-dlp wiki dump in the UI).
    """
    url = (youtube_url or "").strip()
    if not re.search(r"(youtube\.com|youtu\.be)/", url, re.I):
        raise ValueError("Provide a YouTube video URL (watch or youtu.be).")
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="fish_yt_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(out_dir / "yt_audio.%(ext)s")

    cookies = (getattr(config, "YOUTUBE_COOKIES_FILE", "") or "").strip()
    cookie_args: list[str] = []
    if cookies and Path(cookies).is_file():
        cookie_args = ["--cookies", cookies]

    # android / ios often bypass the web "not a bot" interstitial for audio.
    client_attempts = [
        "android,ios,mweb",
        "android",
        "ios",
        "mweb",
        "tv_embedded",
        "",  # default client last
    ]

    last_err = ""
    try:
        for clients in client_attempts:
            for p in out_dir.glob("yt_audio*"):
                p.unlink(missing_ok=True)
            cmd = [
                "yt-dlp",
                "-f", "bestaudio/best",
                "--no-playlist",
                "--no-warnings",
                "-x", "--audio-format", "wav",
                "--audio-quality", "0",
                "-o", out_tmpl,
                *cookie_args,
            ]
            if clients:
                cmd.extend(["--extractor-args", f"youtube:player_client={clients}"])
            cmd.append(url)
            try:
                subprocess.run(
                    cmd, capture_output=True, text=True, check=True, timeout=180,
                )
            except subprocess.CalledProcessError as e:
                last_err = _yt_dlp_err_text(e.stderr or "", e.stdout or "")
                print(f"[fish_clone] yt-dlp clients={clients or 'default'} failed: {last_err}")
                continue

            wavs = list(out_dir.glob("yt_audio*.wav")) + list(out_dir.glob("*.wav"))
            if wavs:
                return normalize_sample(str(wavs[0]), out_dir)
            last_err = "YouTube download produced no audio file"
    except FileNotFoundError:
        raise RuntimeError("yt-dlp is not installed on this server") from None

    if _yt_bot_blocked(last_err):
        raise ValueError(
            "YouTube blocked the download (bot check). "
            "Upload a short WAV/MP3 of the voice instead — that works reliably."
        )
    raise ValueError(
        f"Could not extract audio from YouTube. "
        f"Upload a WAV/MP3 sample instead. ({last_err[:180]})"
    )


def create_voice_model(
    sample_path: str,
    *,
    title: str,
    description: str = "",
) -> dict:
    """POST /model — persistent private fast clone. Returns Fish model payload."""
    sample = normalize_sample(sample_path)
    title = (title or "My voice").strip()[:80] or "My voice"
    with open(sample, "rb") as fh:
        files = {"voices": ("sample.wav", fh, "audio/wav")}
        data = {
            "type": "tts",
            "title": title,
            "description": (description or "ChannelRecipe rights-gated clone")[:200],
            "visibility": "private",
            "train_mode": "fast",
        }
        resp = httpx.post(
            f"{FISH_API}/model",
            headers=_headers(),
            data=data,
            files=files,
            timeout=120,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Fish clone failed ({resp.status_code}): {resp.text[:400]}")
    payload = resp.json()
    model_id = payload.get("_id") or payload.get("id")
    if not model_id:
        raise RuntimeError(f"Fish clone returned no model id: {payload}")
    return {
        "fish_model_id": str(model_id),
        "title": title,
        "state": payload.get("state") or "trained",
        "raw": payload,
    }


def tts_with_clone(text: str, fish_model_id: str, output_path: str) -> str:
    """Generate speech with a Fish reference_id (JSON path).

    Callers must pass chunks ≤ ~4000 chars. We refuse silent truncation —
    that previously cut ~8 min scripts down to ~3.5 min without error.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty script")
    FISH_HARD_MAX = 4500
    if len(text) > FISH_HARD_MAX:
        raise ValueError(
            f"Fish TTS chunk too long ({len(text)} chars; max {FISH_HARD_MAX}). "
            "Chunk the script before calling tts_with_clone."
        )
    # Prefer msgpack-free JSON for fewer deps; Fish accepts JSON for reference_id.
    resp = httpx.post(
        f"{FISH_API}/v1/tts",
        headers={
            **_headers(),
            "Content-Type": "application/json",
            "model": _tts_model(),
        },
        json={
            "text": text,
            "reference_id": fish_model_id,
            "format": "wav",
        },
        timeout=180,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Fish TTS failed ({resp.status_code}): {resp.text[:400]}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(resp.content)
    return output_path
