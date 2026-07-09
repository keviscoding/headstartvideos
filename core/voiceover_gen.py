"""
Voiceover generation.

Primary: Atlas Cloud xAI TTS (fast, ~sub-second latency, up to 15k chars).
Fallback: Gemini TTS with chunking for long scripts.
"""

from __future__ import annotations
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from config import GEMINI_KEY, ATLASCLOUD_KEY

VOICES = {
    "Zephyr": "Bright",
    "Puck": "Upbeat",
    "Charon": "Informative",
    "Kore": "Firm",
    "Fenrir": "Excitable",
    "Leda": "Youthful",
    "Orus": "Firm",
    "Aoede": "Breezy",
    "Callirrhoe": "Easy-going",
    "Autonoe": "Bright",
    "Enceladus": "Breathy",
    "Iapetus": "Clear",
    "Umbriel": "Easy-going",
    "Algieba": "Smooth",
    "Despina": "Smooth",
    "Erinome": "Clear",
    "Gacrux": "Mature",
    "Pulcherrima": "Forward",
    "Rasalgethi": "Informative",
    "Laomedeia": "Upbeat",
    "Achernar": "Soft",
    "Schedar": "Even",
    "Vindemiatrix": "Gentle",
    "Sadachbia": "Lively",
    "Sadaltager": "Knowledgeable",
    "Sulafat": "Warm",
}

# Map Gemini-style voice names → Atlas xAI voice_ids
_ATLAS_VOICE_MAP = {
    "Charon": "leo",       # authoritative narrator
    "Kore": "rex",         # firm / professional
    "Gacrux": "rex",
    "Schedar": "leo",
    "Puck": "eve",         # upbeat
    "Fenrir": "eve",
    "Zephyr": "ara",       # warm
    "Aoede": "ara",
    "Sulafat": "ara",
    "Leda": "ara",
    "Orus": "leo",
    "Rasalgethi": "leo",
}

VOICE_CHOICES = [f"{name} -- {desc}" for name, desc in VOICES.items()]

STYLE_PRESETS = {
    "Narrator": (
        "### DIRECTOR'S NOTES\n"
        "Speak in a clear, authoritative documentary narrator voice with "
        "steady pacing. Think documentary voiceover -- informative but engaging.\n"
    ),
    "Storyteller": (
        "### DIRECTOR'S NOTES\n"
        "Warm, engaging storyteller. Slightly conversational, with natural "
        "pauses for emphasis. Draw the listener in.\n"
    ),
    "Energetic": (
        "### DIRECTOR'S NOTES\n"
        "Upbeat and energetic. Faster pacing, enthusiastic delivery. "
        "Great for listicles and viral content.\n"
    ),
    "Calm": (
        "### DIRECTOR'S NOTES\n"
        "Calm, measured, soothing. Slow and deliberate pacing. "
        "Ideal for educational or meditative content.\n"
    ),
    "Custom": "",
}

MAX_CHUNK_CHARS = 1500
ATLAS_MAX_CHARS = 14000  # xAI TTS limit is 15k; leave headroom

_TTS_MODELS = [
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
]


def _write_wav(path: str, pcm_data: bytes, rate: int = 24000) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm_data)


def _chunk_script(script: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
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


def _build_prompt(script_text: str, style_notes: str, prev_chunk_tail: str = "") -> str:
    """Build the full TTS prompt with director's notes + transcript."""
    parts = []
    if style_notes:
        parts.append(style_notes.strip())
    if prev_chunk_tail:
        parts.append(
            "#### STYLE CONTINUITY\n"
            "Continue narrating in the exact same voice style, pitch, pace, "
            "and tone as the previous segment which ended with:\n"
            f'"{prev_chunk_tail}"'
        )
    parts.append("#### TRANSCRIPT")
    parts.append(script_text)
    return "\n\n".join(parts)


def _tts_generate(client, model: str, prompt: str, voice_name: str) -> bytes:
    """Call Gemini TTS via generateContent and return raw audio bytes."""
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            ),
        ),
    )
    part = response.candidates[0].content.parts[0]
    return part.inline_data.data


# ---------------------------------------------------------------------------
# Atlas Cloud xAI TTS (fast path)
# ---------------------------------------------------------------------------
def _atlas_tts_chunk(text: str, voice_id: str, out_path: str) -> bool:
    """Generate one audio chunk via Atlas xAI TTS. Returns True on success."""
    import httpx

    if not ATLASCLOUD_KEY:
        return False

    ATLAS_BASE = "https://api.atlascloud.ai/api/v1"
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

    try:
        r = httpx.post(f"{ATLAS_BASE}/model/generateAudio", headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json().get("data") or r.json()
        pred_id = data.get("id")
        if not pred_id:
            outputs = data.get("outputs") or []
            if outputs:
                return _download_audio(outputs[0], out_path)
            print(f"[voiceover] Atlas TTS: no prediction id: {r.text[:200]}")
            return False

        for _ in range(40):
            time.sleep(0.5)
            pr = httpx.get(f"{ATLAS_BASE}/model/prediction/{pred_id}", headers=headers, timeout=30)
            pr.raise_for_status()
            pdata = pr.json().get("data") or pr.json()
            status = pdata.get("status", "")
            if status == "completed":
                outputs = pdata.get("outputs") or []
                if not outputs:
                    print("[voiceover] Atlas TTS completed but no outputs")
                    return False
                url = outputs[0] if isinstance(outputs[0], str) else (
                    outputs[0].get("url") or outputs[0].get("audio")
                )
                return _download_audio(url, out_path)
            if status in ("failed", "timeout", "error"):
                print(f"[voiceover] Atlas TTS failed: {status} {pdata}")
                return False
        print("[voiceover] Atlas TTS poll timed out")
        return False
    except Exception as e:
        print(f"[voiceover] Atlas TTS error: {e}")
        return False


def _download_audio(url: str, out_path: str) -> bool:
    import httpx
    if not url:
        return False
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=True) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        if Path(out_path).stat().st_size < 1000:
            return False
        tmp = out_path + ".tmp.wav"
        cmd = ["ffmpeg", "-y", "-i", out_path, "-c:a", "pcm_s16le", "-ar", "24000", "-ac", "1", tmp]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode == 0 and Path(tmp).exists():
            Path(out_path).unlink(missing_ok=True)
            Path(tmp).rename(out_path)
        return Path(out_path).stat().st_size > 1000
    except Exception as e:
        print(f"[voiceover] Audio download failed: {e}")
        return False


def _generate_via_atlas(script: str, voice_name: str, out_dir: Path) -> str | None:
    """Try Atlas xAI TTS for the full script. Returns path or None."""
    if not ATLASCLOUD_KEY:
        return None

    voice_id = _ATLAS_VOICE_MAP.get(voice_name, "leo")
    chunks = _chunk_script(script, max_chars=ATLAS_MAX_CHARS)
    print(f"[voiceover] Trying Atlas xAI TTS ({len(chunks)} chunk(s), voice={voice_id})...")

    wav_paths = []
    for i, chunk in enumerate(chunks):
        chunk_path = str(out_dir / f"_atlas_{i:03d}.wav")
        ok = _atlas_tts_chunk(chunk, voice_id, chunk_path)
        if not ok:
            for p in wav_paths:
                Path(p).unlink(missing_ok=True)
            Path(chunk_path).unlink(missing_ok=True)
            return None
        wav_paths.append(chunk_path)
        print(f"[voiceover] Atlas chunk {i + 1}/{len(chunks)} done")

    output_path = str(out_dir / "voiceover.wav")
    _concat_wavs(wav_paths, output_path)
    return str(Path(output_path).resolve())


def _generate_via_gemini(
    script: str,
    voice_name: str,
    style_notes: str,
    model: str,
    out_dir: Path,
) -> str:
    """Gemini TTS fallback with parallel chunking."""
    from google import genai

    if not GEMINI_KEY:
        raise ValueError("No TTS provider available. Set ATLASCLOUD_KEY or GEMINI_KEY.")

    client = genai.Client(api_key=GEMINI_KEY)
    models_to_try = ([model] if model else []) + _TTS_MODELS
    seen = set()
    models_to_try = [m for m in models_to_try if m and m not in seen and not seen.add(m)]
    chunks = _chunk_script(script)
    working_model = models_to_try[0]

    def _gen_chunk(i: int, chunk: str) -> tuple[int, str]:
        prompt = _build_prompt(chunk, style_notes)
        print(f"[voiceover] Gemini chunk {i + 1}/{len(chunks)} ({len(chunk)} chars)...")
        retries = 3
        for attempt in range(retries):
            for mi, try_model in enumerate(models_to_try if attempt == 0 else [working_model]):
                try:
                    audio_data = _tts_generate(client, try_model, prompt, voice_name)
                    chunk_path = str(out_dir / f"_chunk_{i:03d}.wav")
                    _write_wav(chunk_path, audio_data)
                    print(f"[voiceover] Gemini chunk {i + 1} complete (model={try_model})")
                    return i, chunk_path
                except Exception as e:
                    if mi < len(models_to_try) - 1 and attempt == 0:
                        print(f"[voiceover] Model {try_model} failed: {e}, trying next...")
                        continue
                    if attempt < retries - 1:
                        print(f"[voiceover] Attempt {attempt + 1} failed: {e}, retrying...")
                        break
                    raise RuntimeError(
                        f"Failed to generate voiceover chunk {i + 1} after {retries} attempts: {e}"
                    )
        raise RuntimeError(f"Failed to generate chunk {i + 1}")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as ex:
        futures = {ex.submit(_gen_chunk, i, c): i for i, c in enumerate(chunks)}
        for fut in as_completed(futures):
            idx, path = fut.result()
            results[idx] = path

    wav_paths = [results[i] for i in range(len(chunks))]
    output_path = str(out_dir / "voiceover.wav")
    _concat_wavs(wav_paths, output_path)
    return str(Path(output_path).resolve())


def generate_voiceover(
    script: str,
    voice: str = "Charon",
    style_preset: str = "Narrator",
    custom_notes: str = "",
    model: str = "",
    output_dir: str = "",
) -> str:
    """
    Generate voiceover audio from script text.

    Tries Atlas xAI TTS first (fast), falls back to Gemini TTS.
    Returns path to the generated WAV file.
    """
    voice_name = voice.split(" -- ")[0].strip() if " -- " in voice else voice
    style_notes = custom_notes if style_preset == "Custom" else STYLE_PRESETS.get(style_preset, "")

    out_dir = Path(output_dir) if output_dir else Path("output/voiceovers")
    out_dir.mkdir(parents=True, exist_ok=True)

    atlas_path = _generate_via_atlas(script, voice_name, out_dir)
    if atlas_path:
        print(f"[voiceover] Complete via Atlas: {atlas_path} ({Path(atlas_path).stat().st_size} bytes)")
        return atlas_path

    print("[voiceover] Atlas unavailable — falling back to Gemini TTS")
    path = _generate_via_gemini(script, voice_name, style_notes, model, out_dir)
    print(f"[voiceover] Complete via Gemini: {path} ({Path(path).stat().st_size} bytes)")
    return path
