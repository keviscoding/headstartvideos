"""
Segments a script into B-roll slots aligned to voiceover audio timestamps.
Uses faster-whisper for local speech-to-text with word-level timing.

Two alignment modes:
  - Proportional (legacy): distributes by character count -- fast, approximate
  - Word-aligned (cinematic): fuzzy-matches script sentences to Whisper words
    for precise per-sentence timing
"""

from __future__ import annotations
import os
import re
import random
from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass
class BRollSlot:
    id: int
    text: str
    start_sec: float
    end_sec: float
    duration_sec: float


@dataclass
class SentenceTimestamp:
    text: str
    start_sec: float
    end_sec: float
    word_timestamps: list[dict] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on .!? boundaries."""
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation for fuzzy matching."""
    return re.sub(r'[^\w\s]', '', text.lower()).strip()


def _transcribe_groq(audio_path: str) -> list[dict]:
    """Transcribe via Groq's hosted Whisper (large-v3-turbo). Fast, cheap, better accuracy."""
    import config
    from groq import Groq

    client = Groq(api_key=config.GROQ_API_KEY)
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=f,
            model=config.GROQ_WHISPER_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["word"],
            language="en",
            temperature=0.0,
        )

    words = []
    for w in (result.words or []):
        word_text = w.word if isinstance(w, dict) is False else w.get("word", "")
        start = w.start if not isinstance(w, dict) else w.get("start", 0)
        end = w.end if not isinstance(w, dict) else w.get("end", 0)
        if hasattr(w, "word"):
            word_text, start, end = w.word, w.start, w.end
        words.append({"word": word_text.strip(), "start": float(start), "end": float(end)})
    return words


def _transcribe_local(audio_path: str, model_size: str = "base") -> list[dict]:
    """Transcribe locally with faster-whisper. Fallback when Groq is unavailable."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, compute_type="int8")
    segments_iter, info = model.transcribe(audio_path, word_timestamps=True, language="en")

    words = []
    for seg in segments_iter:
        for w in (seg.words or []):
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    return words


def align_script_to_audio(
    script: str,
    audio_path: str,
    model_size: str = "base",
) -> tuple[list[SentenceTimestamp], list[dict]]:
    """
    Align script sentences to actual audio timing using Whisper word timestamps.

    Returns (sentence_timestamps, all_words) where each sentence has precise
    start/end times from the audio, plus the raw word list for downstream use.
    """
    import config

    # Prefer Groq (large-v3-turbo, ~5s, zero CPU). Local whisper melts the web
    # dyno under load — only allowed when ALLOW_LOCAL_WHISPER=1 (dev).
    allow_local = os.getenv("ALLOW_LOCAL_WHISPER", "").strip() in ("1", "true", "yes")
    app_env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").lower()
    is_prod = app_env in ("production", "prod") or bool(os.getenv("DATABASE_URL", "").strip())

    if config.GROQ_API_KEY:
        try:
            print("[segmenter] Using Groq Whisper API for alignment...")
            words = _transcribe_groq(audio_path)
            print(f"[segmenter] Groq returned {len(words)} words")
        except Exception as e:
            if is_prod and not allow_local:
                raise RuntimeError(
                    f"Groq Whisper failed and local whisper is disabled in production: {e}"
                ) from e
            print(f"[segmenter] Groq failed ({e}), falling back to local whisper...")
            words = _transcribe_local(audio_path, model_size)
    else:
        if is_prod and not allow_local:
            raise RuntimeError(
                "GROQ_API_KEY is required in production. Local Whisper would freeze the server under load."
            )
        words = _transcribe_local(audio_path, model_size)

    if not words:
        return [], words

    sentences = split_sentences(script)
    if not sentences:
        return [], words

    whisper_text_norm = " ".join(w["word"] for w in words).lower()
    whisper_words_lower = [w["word"].lower() for w in words]

    sentence_times: list[SentenceTimestamp] = []
    word_cursor = 0

    for sent_idx, sent in enumerate(sentences):
        sent_words_norm = _normalize(sent).split()
        if not sent_words_norm:
            continue

        best_start = word_cursor
        best_score = 0.0

        search_end = min(word_cursor + len(sent_words_norm) * 3, len(words))

        for start_i in range(word_cursor, min(search_end, len(words))):
            end_i = min(start_i + len(sent_words_norm) + 2, len(words))
            candidate = " ".join(whisper_words_lower[start_i:end_i])
            sent_joined = " ".join(sent_words_norm)
            score = SequenceMatcher(None, sent_joined, candidate).ratio()

            if score > best_score:
                best_score = score
                best_start = start_i

        match_end = min(best_start + len(sent_words_norm), len(words))

        if best_score < 0.3 and sent_idx > 0:
            prev = sentence_times[-1]
            start_sec = prev.end_sec
            end_sec = start_sec + len(sent) / 15.0
            end_sec = min(end_sec, words[-1]["end"])
        else:
            start_sec = words[best_start]["start"]
            end_sec = words[min(match_end, len(words)) - 1]["end"]
            word_cursor = match_end

        matched_words = words[best_start:match_end] if best_score >= 0.3 else []

        sentence_times.append(SentenceTimestamp(
            text=sent,
            start_sec=start_sec,
            end_sec=end_sec,
            word_timestamps=matched_words,
        ))

    if sentence_times:
        sentence_times[-1].end_sec = words[-1]["end"]

    return sentence_times, words


def segment_script_with_audio(
    script: str,
    audio_path: str,
    swap_rate: str = "medium",
    model_size: str = "base",
    use_word_align: bool = False,
) -> list[BRollSlot]:
    from config import SWAP_RATE_PRESETS

    min_dur, max_dur = SWAP_RATE_PRESETS.get(swap_rate, (5, 10))

    if use_word_align:
        sentence_times, words = align_script_to_audio(audio_path=audio_path, script=script, model_size=model_size)
        if sentence_times:
            times = [(st.start_sec, st.end_sec) for st in sentence_times]
            sentences = [st.text for st in sentence_times]
            slots = _group_into_slots(sentences, times, min_dur, max_dur)
            return slots

    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, compute_type="int8")
    segments_iter, info = model.transcribe(
        audio_path, word_timestamps=True, language="en"
    )

    words: list[dict] = []
    for seg in segments_iter:
        for w in (seg.words or []):
            words.append({
                "word": w.word.strip(),
                "start": w.start,
                "end": w.end,
            })

    if not words:
        return _fallback_segment(script, swap_rate)

    total_audio_dur = words[-1]["end"] if words else 0
    sentences = split_sentences(script)

    sentence_times = _distribute_sentences_evenly(sentences, total_audio_dur)

    slots = _group_into_slots(sentences, sentence_times, min_dur, max_dur)
    return slots


def _distribute_sentences_evenly(
    sentences: list[str], total_duration: float
) -> list[tuple[float, float]]:
    """Distribute sentences proportionally by character count (legacy mode)."""
    total_chars = sum(len(s) for s in sentences)
    if total_chars == 0:
        return [(0.0, total_duration)] * len(sentences)

    times: list[tuple[float, float]] = []
    current = 0.0

    for sent in sentences:
        proportion = len(sent) / total_chars
        duration = proportion * total_duration
        times.append((current, current + duration))
        current += duration

    return times


def _group_into_slots(
    sentences: list[str],
    sentence_times: list[tuple[float, float]],
    min_dur: float,
    max_dur: float,
) -> list[BRollSlot]:
    """
    Group sentences into B-roll slots. Enforces:
    - Each slot is at least min_dur seconds
    - Each slot is at most max_dur seconds (hard cap, splits if needed)
    - Duration varies randomly within range to avoid robotic feel
    """
    slots: list[BRollSlot] = []
    slot_id = 0
    group_text: list[str] = []
    group_start: float | None = None
    group_end: float = 0.0

    for sent, (s_start, s_end) in zip(sentences, sentence_times):
        if group_start is None:
            group_start = s_start
        group_text.append(sent)
        group_end = s_end

        group_dur = group_end - group_start
        target = random.uniform(min_dur, max_dur)

        if group_dur >= target:
            slots.append(BRollSlot(
                id=slot_id,
                text=" ".join(group_text),
                start_sec=group_start,
                end_sec=group_end,
                duration_sec=group_dur,
            ))
            slot_id += 1
            group_text = []
            group_start = None

    if group_text and group_start is not None:
        dur = group_end - group_start
        slots.append(BRollSlot(
            id=slot_id,
            text=" ".join(group_text),
            start_sec=group_start,
            end_sec=group_end,
            duration_sec=dur,
        ))

    slots = _enforce_max_duration(slots, max_dur)
    return slots


def _enforce_max_duration(
    slots: list[BRollSlot], max_dur: float
) -> list[BRollSlot]:
    """Split any slot that exceeds max_dur into smaller chunks."""
    result: list[BRollSlot] = []
    new_id = 0

    for slot in slots:
        if slot.duration_sec <= max_dur * 1.5:
            result.append(BRollSlot(
                id=new_id,
                text=slot.text,
                start_sec=slot.start_sec,
                end_sec=slot.end_sec,
                duration_sec=slot.duration_sec,
            ))
            new_id += 1
        else:
            sentences = split_sentences(slot.text)
            if len(sentences) <= 1:
                result.append(BRollSlot(
                    id=new_id,
                    text=slot.text,
                    start_sec=slot.start_sec,
                    end_sec=slot.end_sec,
                    duration_sec=slot.duration_sec,
                ))
                new_id += 1
                continue

            chunk_dur = slot.duration_sec / len(sentences)
            for i, sent in enumerate(sentences):
                start = slot.start_sec + i * chunk_dur
                end = start + chunk_dur
                result.append(BRollSlot(
                    id=new_id,
                    text=sent,
                    start_sec=start,
                    end_sec=end,
                    duration_sec=chunk_dur,
                ))
                new_id += 1

    return result


def segment_script_no_audio(
    script: str,
    total_duration: float,
    swap_rate: str = "medium",
) -> list[BRollSlot]:
    from config import SWAP_RATE_PRESETS
    min_dur, max_dur = SWAP_RATE_PRESETS.get(swap_rate, (5, 10))
    sentences = split_sentences(script)
    times = _distribute_sentences_evenly(sentences, total_duration)
    return _group_into_slots(sentences, times, min_dur, max_dur)


def _fallback_segment(script: str, swap_rate: str) -> list[BRollSlot]:
    """Fallback when whisper fails -- assume 150 WPM narration speed."""
    words = script.split()
    total_dur = len(words) / 2.5
    return segment_script_no_audio(script, total_dur, swap_rate)
