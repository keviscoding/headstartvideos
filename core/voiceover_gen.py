"""
Voiceover generation via Atlas Cloud xAI TTS only.

No Gemini TTS fallback — if Atlas fails, we surface a clear error.
"""

from __future__ import annotations
import subprocess
import tempfile
import time
from pathlib import Path

from config import ATLASCLOUD_KEY

# Display names for Gradio / legacy callers
VOICES = {
    "leo": "Authoritative narrator",
    "rex": "Professional",
    "sal": "Neutral / versatile",
    "ara": "Warm conversational",
    "eve": "Energetic upbeat",
    "78a495fdbb39": "James — engaging",
    "96819d0bd28d": "Daniel — mature",
    "f8cf5c2c78d4": "Grace — clear",
    "79f3a8b96d43": "Claire — steady",
}

# Any UI / legacy name → Atlas xAI voice_id
_ATLAS_VOICE_MAP = {
    "leo": "leo", "rex": "rex", "sal": "sal", "ara": "ara", "eve": "eve",
    "78a495fdbb39": "78a495fdbb39",  # James
    "96819d0bd28d": "96819d0bd28d",  # Daniel
    "f8cf5c2c78d4": "f8cf5c2c78d4",  # Grace
    "79f3a8b96d43": "79f3a8b96d43",  # Claire
    "Charon": "leo", "Kore": "rex", "Gacrux": "rex", "Schedar": "leo",
    "Puck": "eve", "Fenrir": "eve", "Zephyr": "ara", "Aoede": "ara",
    "Sulafat": "ara", "Leda": "ara", "Orus": "leo", "Rasalgethi": "leo",
    "James": "78a495fdbb39", "Daniel": "96819d0bd28d",
    "Grace": "f8cf5c2c78d4", "Claire": "79f3a8b96d43",
    "Leo": "leo", "Rex": "rex", "Sal": "sal", "Ara": "ara", "Eve": "eve",
}

VOICE_CHOICES = [f"{name} -- {desc}" for name, desc in VOICES.items()]

# Kept for API compatibility (style_preset arg); xAI TTS uses voice_id primarily
STYLE_PRESETS = {
    "Narrator": "",
    "Storyteller": "",
    "Energetic": "",
    "Calm": "",
    "Custom": "",
}

ATLAS_MAX_CHARS = 2500  # Keep chunks small — Atlas stalls on 5k+ char requests
ATLAS_BASE = "https://api.atlascloud.ai/api/v1"


def _chunk_script(script: str, max_chars: int = ATLAS_MAX_CHARS) -> list[str]:
    """Split a long script into chunks at sentence boundaries."""
    if len(script) <= max_chars:
        return [script]

    import re
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    chunks: list[str] = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = sent
        else:
            current = f"{current} {sent}" if current else sent

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _concat_wavs(wav_paths: list[str], output_path: str) -> str:
    """Concatenate multiple WAV files using ffmpeg."""
    if len(wav_paths) == 1:
        import shutil
        shutil.copy2(wav_paths[0], output_path)
        Path(wav_paths[0]).unlink(missing_ok=True)
        return output_path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in wav_paths:
            abs_p = str(Path(p).resolve())
            f.write(f"file '{abs_p}'\n")
        list_path = f.name

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c:a", "pcm_s16le",
        str(Path(output_path).resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    Path(list_path).unlink(missing_ok=True)

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"ffmpeg concat failed (code {result.returncode}): {stderr[:500]}")

    for p in wav_paths:
        Path(p).unlink(missing_ok=True)
    return output_path


def _download_audio(url: str, out_path: str) -> None:
    import httpx
    if not url:
        raise RuntimeError("Atlas TTS returned no audio URL")

    with httpx.stream("GET", url, timeout=90, follow_redirects=True) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)

    if Path(out_path).stat().st_size < 1000:
        raise RuntimeError("Atlas TTS audio file too small / empty")

    # Normalize to PCM WAV for consistent concat
    tmp = out_path + ".tmp.wav"
    cmd = ["ffmpeg", "-y", "-i", out_path, "-c:a", "pcm_s16le", "-ar", "24000", "-ac", "1", tmp]
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    if result.returncode == 0 and Path(tmp).exists():
        Path(out_path).unlink(missing_ok=True)
        Path(tmp).rename(out_path)
    elif Path(tmp).exists():
        Path(tmp).unlink(missing_ok=True)

    if Path(out_path).stat().st_size < 1000:
        raise RuntimeError("Atlas TTS audio normalize failed")


def _poll_budget_seconds(text_len: int) -> float:
    """How long to wait for one Atlas chunk — scales with text length."""
    # ~0.02s per char of speech synthesis + network overhead, min 45s, max 4 min
    return min(240.0, max(45.0, 30.0 + text_len * 0.025))


def _atlas_tts_chunk(text: str, voice_id: str, out_path: str) -> None:
    """Generate one audio chunk via Atlas xAI TTS. Raises on failure."""
    import httpx

    if not ATLASCLOUD_KEY:
        raise RuntimeError("ATLASCLOUD_KEY not configured. Add it in Settings / DigitalOcean env.")

    headers = {
        "Authorization": f"Bearer {ATLASCLOUD_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "xai/tts-v1",
        "text": text,
        "language": "en",
        "voice_id": voice_id,
        "codec": "wav",
        "sample_rate": 24000,
    }

    r = httpx.post(f"{ATLAS_BASE}/model/generateAudio", headers=headers, json=payload, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"Atlas TTS request failed ({r.status_code}): {r.text[:400]}")

    data = r.json().get("data") or r.json()
    pred_id = data.get("id")
    if not pred_id:
        outputs = data.get("outputs") or []
        if outputs:
            url = outputs[0] if isinstance(outputs[0], str) else (
                outputs[0].get("url") or outputs[0].get("audio")
            )
            _download_audio(url, out_path)
            return
        raise RuntimeError(f"Atlas TTS: no prediction id: {r.text[:300]}")

    # Poll until completed — budget scales with chunk length
    budget = _poll_budget_seconds(len(text))
    deadline = time.time() + budget
    last_status = data.get("status", "processing")
    poll_interval = 0.75
    print(f"[voiceover] Polling prediction {pred_id} (budget {budget:.0f}s, {len(text)} chars)...")

    while time.time() < deadline:
        time.sleep(poll_interval)
        pr = httpx.get(f"{ATLAS_BASE}/model/prediction/{pred_id}", headers=headers, timeout=30)
        if pr.status_code >= 400:
            raise RuntimeError(f"Atlas TTS poll failed ({pr.status_code}): {pr.text[:300]}")
        pdata = pr.json().get("data") or pr.json()
        last_status = pdata.get("status", "")
        if last_status == "completed":
            outputs = pdata.get("outputs") or []
            if not outputs:
                raise RuntimeError("Atlas TTS completed but returned no audio outputs")
            url = outputs[0] if isinstance(outputs[0], str) else (
                outputs[0].get("url") or outputs[0].get("audio")
            )
            _download_audio(url, out_path)
            return
        if last_status in ("failed", "timeout", "error"):
            err = pdata.get("error") or pdata
            raise RuntimeError(f"Atlas TTS generation failed ({last_status}): {err}")
        # Back off slightly as we wait longer
        poll_interval = min(2.0, poll_interval + 0.1)

    raise RuntimeError(
        f"Atlas TTS timed out after {budget:.0f}s waiting for audio "
        f"(last status={last_status}, chars={len(text)}, pred={pred_id})"
    )


def generate_voiceover(
    script: str,
    voice: str = "leo",
    style_preset: str = "Narrator",
    custom_notes: str = "",
    model: str = "",
    output_dir: str = "",
) -> str:
    """
    Generate voiceover audio from script text using Atlas xAI TTS only.

    Returns path to the generated WAV file. Raises RuntimeError on failure.
    """
    if not (script or "").strip():
        raise ValueError("Script is empty — nothing to narrate.")
    if not ATLASCLOUD_KEY:
        raise RuntimeError("ATLASCLOUD_KEY not configured. Voiceover requires Atlas Cloud.")

    voice_name = voice.split(" -- ")[0].strip() if " -- " in voice else voice
    voice_id = _ATLAS_VOICE_MAP.get(voice_name, _ATLAS_VOICE_MAP.get(voice_name.lower(), "leo"))

    out_dir = Path(output_dir) if output_dir else Path("output/voiceovers")
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks = _chunk_script(script.strip(), max_chars=ATLAS_MAX_CHARS)
    print(f"[voiceover] Atlas xAI TTS — {len(chunks)} chunk(s), voice={voice_id}, total_chars={len(script)}")

    wav_paths: list[str] = [""] * len(chunks)

    def _run_one(i: int, chunk: str) -> tuple[int, str]:
        chunk_path = str(out_dir / f"_atlas_{i:03d}.wav")
        print(f"[voiceover] Chunk {i + 1}/{len(chunks)} ({len(chunk)} chars)...")
        _atlas_tts_chunk(chunk, voice_id, chunk_path)
        print(f"[voiceover] Chunk {i + 1}/{len(chunks)} done")
        return i, chunk_path

    try:
        # Parallelize chunks for speed (Atlas handles concurrent requests well)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        workers = min(3, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_run_one, i, c) for i, c in enumerate(chunks)]
            for fut in as_completed(futures):
                idx, path = fut.result()
                wav_paths[idx] = path

        output_path = str(out_dir / "voiceover.wav")
        _concat_wavs(wav_paths, output_path)
        final = str(Path(output_path).resolve())
        print(f"[voiceover] Complete via Atlas: {final} ({Path(final).stat().st_size} bytes)")
        return final
    except Exception:
        for p in wav_paths:
            if p:
                Path(p).unlink(missing_ok=True)
        raise
