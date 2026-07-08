"""
Voiceover generation using Gemini TTS.

Uses the generateContent API with response_modalities=["AUDIO"].
Supports 30 prebuilt voices, style direction via director's notes,
and automatic chunking for long scripts.
"""

from __future__ import annotations
import subprocess
import tempfile
import wave
from pathlib import Path

from config import GEMINI_KEY

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

VOICE_CHOICES = [f"{name} -- {desc}" for name, desc in VOICES.items()]

STYLE_PRESETS = {
    "Narrator": (
        "### DIRECTOR'S NOTES\n"
        "Style: Calm, measured, authoritative narrator. Clear diction, "
        "steady pacing. Think documentary voiceover -- informative but engaging.\n"
        "Pacing: Moderate, with natural pauses between sentences for emphasis.\n"
    ),
    "News Anchor": (
        "### DIRECTOR'S NOTES\n"
        "Style: Professional broadcast journalist. Crisp, confident delivery "
        "with gravitas. Neutral accent, clear enunciation.\n"
        "Pacing: Brisk but measured. Slight emphasis on key facts and names.\n"
    ),
    "Storyteller": (
        "### DIRECTOR'S NOTES\n"
        "Style: Dramatic storyteller. Engaging, emotionally expressive, building "
        "tension and releasing it. Draws the listener in like a campfire tale.\n"
        "Pacing: Varied -- speeds up during action, slows during reveals and "
        "dramatic moments. Uses pauses for suspense.\n"
    ),
    "Energetic": (
        "### DIRECTOR'S NOTES\n"
        "Style: High-energy YouTube presenter. Enthusiastic, fast-paced, "
        "infectious excitement. The \"vocal smile\" should be audible.\n"
        "Pacing: Fast with punchy delivery. Short sentences hit hard.\n"
    ),
    "Custom": "",
}

MAX_CHUNK_CHARS = 1500

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


def _chunk_script(script: str) -> list[str]:
    """Split a long script into chunks at sentence boundaries."""
    if len(script) <= MAX_CHUNK_CHARS:
        return [script]

    import re
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    chunks: list[str] = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) + 1 > MAX_CHUNK_CHARS and current:
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
    """Build the full TTS prompt with director's notes + transcript.

    For chunks 2+, prev_chunk_tail provides the last ~200 chars of the
    previous chunk so the TTS model maintains consistent vocal style.
    """
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
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name,
                    )
                )
            ),
        ),
    )

    part = response.candidates[0].content.parts[0]
    return part.inline_data.data


def generate_voiceover(
    script: str,
    voice: str = "Charon",
    style_preset: str = "Narrator",
    custom_notes: str = "",
    model: str = "",
    output_dir: str = "",
) -> str:
    """
    Generate voiceover audio from script text using Gemini TTS.

    Returns path to the generated WAV file.
    """
    from google import genai

    if not GEMINI_KEY:
        raise ValueError("GEMINI_KEY not set. Add it in Settings.")

    client = genai.Client(api_key=GEMINI_KEY)

    voice_name = voice.split(" -- ")[0].strip() if " -- " in voice else voice
    style_notes = custom_notes if style_preset == "Custom" else STYLE_PRESETS.get(style_preset, "")

    out_dir = Path(output_dir) if output_dir else Path("output/voiceovers")
    out_dir.mkdir(parents=True, exist_ok=True)

    models_to_try = ([model] if model else []) + _TTS_MODELS
    seen = set()
    models_to_try = [m for m in models_to_try if m and m not in seen and not seen.add(m)]

    chunks = _chunk_script(script)

    working_model = models_to_try[0]

    def _gen_chunk(i: int, chunk: str) -> tuple[int, str]:
        """Generate a single TTS chunk with retries + model fallback."""
        prompt = _build_prompt(chunk, style_notes)
        print(f"[voiceover] Generating chunk {i + 1}/{len(chunks)} "
              f"({len(chunk)} chars, voice={voice_name})...")
        retries = 3
        for attempt in range(retries):
            for mi, try_model in enumerate(models_to_try if attempt == 0 else [working_model]):
                try:
                    audio_data = _tts_generate(client, try_model, prompt, voice_name)
                    if len(audio_data) < 1000:
                        print(f"[voiceover] WARNING: chunk {i+1} audio very small ({len(audio_data)} bytes)")
                    chunk_path = str(out_dir / f"_chunk_{i:03d}.wav")
                    _write_wav(chunk_path, audio_data)
                    print(f"[voiceover] Chunk {i + 1} complete ({len(audio_data)} bytes, model={try_model})")
                    return i, chunk_path
                except Exception as e:
                    if mi < len(models_to_try) - 1 and attempt == 0:
                        print(f"[voiceover] Model {try_model} failed: {e}, trying next model...")
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

    final_size = Path(output_path).stat().st_size
    print(f"[voiceover] Complete: {output_path} ({final_size} bytes)")
    return str(Path(output_path).resolve())
