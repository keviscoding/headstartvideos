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


def normalize_sample(src_path: str, out_dir: str | Path | None = None) -> str:
    """Convert to mono 24k WAV and trim to MAX_SAMPLE_SEC."""
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="fish_sample_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "sample.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", src_path,
            "-t", str(MAX_SAMPLE_SEC),
            "-ar", "24000", "-ac", "1",
            str(out),
        ],
        capture_output=True, check=True, timeout=120,
    )
    dur = _probe_duration(str(out))
    if dur < MIN_SAMPLE_SEC:
        raise ValueError(
            f"Voice sample too short ({dur:.1f}s). Use at least {MIN_SAMPLE_SEC}s of clear speech."
        )
    return str(out)


def extract_youtube_audio(youtube_url: str, out_dir: str | Path | None = None) -> str:
    """Download audio from a YouTube URL (requires yt-dlp)."""
    url = (youtube_url or "").strip()
    if not re.search(r"(youtube\.com|youtu\.be)/", url, re.I):
        raise ValueError("Provide a YouTube video URL (watch or youtu.be).")
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="fish_yt_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(out_dir / "yt_audio.%(ext)s")
    try:
        subprocess.run(
            [
                "yt-dlp", "-f", "bestaudio/best",
                "--no-playlist",
                "-x", "--audio-format", "wav",
                "--audio-quality", "0",
                "-o", out_tmpl,
                url,
            ],
            capture_output=True, text=True, check=True, timeout=180,
        )
    except FileNotFoundError:
        raise RuntimeError("yt-dlp is not installed on this server")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or str(e))[-400:]
        raise ValueError(f"Could not extract audio from YouTube: {err}")
    wavs = list(out_dir.glob("yt_audio*.wav")) + list(out_dir.glob("*.wav"))
    if not wavs:
        raise ValueError("YouTube download produced no audio file")
    return normalize_sample(str(wavs[0]), out_dir)


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
    """Generate speech with a Fish reference_id (JSON path)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty script")
    # Prefer msgpack-free JSON for fewer deps; Fish accepts JSON for reference_id.
    resp = httpx.post(
        f"{FISH_API}/v1/tts",
        headers={
            **_headers(),
            "Content-Type": "application/json",
            "model": _tts_model(),
        },
        json={
            "text": text[:4500],
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
