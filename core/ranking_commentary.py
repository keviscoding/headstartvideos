"""Ranking AI commentary: Gemini one-liners + Atlas TTS + char-weighted karaoke timings."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

ProgressCb = Callable[[str], None]


def _normalize_rank_label(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9 !?]", " ", str(label or ""))
    s = re.sub(r"\s+", " ", s).strip().upper()
    if not s:
        return "MOMENT"
    words = s.split()[:4]
    return " ".join(words)[:24]


def _fallback_for_role(clip: dict, rank: int, total: int, role: str) -> dict[str, str]:
    label = _normalize_rank_label(clip.get("label") or f"#{rank}")
    if role == "hook":
        return {"line": "Watch this — you need to see it", "label": label}
    if role == "cta":
        return {"line": "Subscribe before this goes wrong", "label": label}
    return {"line": f"Number {rank} hits different", "label": label}


def _parse_line_and_label(raw: str, fallback_line: str, fallback_label: str) -> dict[str, str]:
    text = str(raw or "").strip().strip("\"'")
    if not text:
        return {"line": fallback_line, "label": _normalize_rank_label(fallback_label)}
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            obj = json.loads(m.group(0))
            line = str(obj.get("line") or obj.get("commentary") or obj.get("voice") or "").strip()
            lab = str(obj.get("label") or obj.get("rankLabel") or obj.get("title") or "").strip()
            if line:
                return {
                    "line": line.strip("\"'"),
                    "label": _normalize_rank_label(lab or fallback_label),
                }
    except Exception:
        pass
    parts = re.split(r"\s*\|\|\s*|\s*\|\s*", text)
    if len(parts) >= 2:
        return {
            "line": parts[0].strip("\"'").strip(),
            "label": _normalize_rank_label(" ".join(parts[1:]) or fallback_label),
        }
    return {"line": text, "label": _normalize_rank_label(fallback_label)}


def _role_prompt(
    role: str,
    ranking_title: str,
    clip_number: int,
    total_clips: int,
    prior_lines: list[str],
    label: str,
) -> str:
    avoid = ""
    if prior_lines:
        avoid = "\nDo NOT repeat or paraphrase any of these earlier lines: " + json.dumps(prior_lines[-4:])
    if role == "hook":
        job = "Write a cold-open hook (under 12 words) that makes viewers stay."
    elif role == "cta":
        job = "Write a subscribe CTA for the #1 reveal (under 12 words)."
    else:
        job = f"Write a short reaction for rank #{clip_number} of {total_clips} (under 10 words)."
    return f"""You are a viral YouTube Shorts ranking narrator.
Ranking title (context only — do NOT read it aloud): "{ranking_title}"
Clip rank label: "{label}"
{job}
Return ONLY JSON: {{"line":"...","label":"SHORT RANK LABEL"}}
Label max 4 words, ALL CAPS.{avoid}"""


def _gemini_line(prompt: str) -> str:
    try:
        from core.atlas_llm import generate_text
        return (generate_text(prompt, max_tokens=120, temperature=0.9) or "").strip()
    except Exception as e:
        print(f"[ranking_commentary] text gen failed: {e}")
        return ""


def _tts_line(line: str, voice_name: str, out_path: Path) -> Path | None:
    """Synthesize a short line via Atlas TTS. Returns wav path or None."""
    try:
        from core.atlas_runtime import get_atlas_key
        from core.voiceover_gen import _atlas_tts_chunk, _ATLAS_VOICE_MAP
    except Exception as e:
        print(f"[ranking_commentary] TTS import failed: {e}")
        return None

    if not get_atlas_key():
        return None

    atlas_voice = _ATLAS_VOICE_MAP.get(voice_name) or _ATLAS_VOICE_MAP.get(
        (voice_name or "").lower()
    ) or "leo"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _atlas_tts_chunk(line, atlas_voice, str(out_path))
        if out_path.is_file() and out_path.stat().st_size > 500:
            return out_path
    except Exception as e:
        print(f"[ranking_commentary] TTS failed: {e}")
    return None


def _char_weighted_timings(line: str, duration: float) -> list[dict[str, Any]]:
    words = [w for w in re.split(r"\s+", (line or "").strip()) if w]
    if not words:
        return []
    weights = [max(1, len(re.sub(r"[^a-zA-Z0-9]", "", w)) or 1) for w in words]
    total_w = sum(weights) or 1
    t = 0.0
    out = []
    for w, wt in zip(words, weights):
        span = max(0.08, duration * (wt / total_w))
        out.append({"word": w, "start": t, "end": t + span})
        t += span
    if out:
        out[-1]["end"] = duration
    return out


def generate_ranking_commentary(
    clips: list[dict[str, Any]],
    ranking_title: str,
    *,
    voice_name: str = "Kore",
    work_dir: str | Path,
    progress: ProgressCb | None = None,
) -> list[dict[str, Any]]:
    """
    Returns list of {clipIndex, line, label, audioPath, wordTimings, duration}.
    Mutates clip labels when Gemini returns a better short rank label.
    """
    def prog(msg: str) -> None:
        if progress:
            progress(msg)
        print(f"[ranking_commentary] {msg}")

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    prior: list[str] = []
    n = len(clips)

    for i, clip in enumerate(clips):
        role = "hook" if i == 0 else ("cta" if i == n - 1 else "react")
        rank = int(clip.get("number") or (n - i))
        label = str(clip.get("label") or f"#{rank}")
        prog(
            "Writing cold-open hook…" if role == "hook"
            else ("Writing subscribe CTA…" if role == "cta"
                  else f"Writing commentary for clip {i + 1}/{n}…")
        )
        fb = _fallback_for_role(clip, rank, n, role)
        try:
            raw = _gemini_line(_role_prompt(role, ranking_title, rank, n, prior, label))
            parsed = _parse_line_and_label(raw, fb["line"], fb["label"])
        except Exception as e:
            print(f"[ranking_commentary] clip {i} text failed: {e}")
            parsed = fb

        if parsed.get("label") and clips[i] is not None:
            clips[i]["label"] = parsed["label"]

        line = (parsed.get("line") or fb["line"]).strip()
        prior.append(line)

        audio_path = None
        duration = 0.0
        word_timings: list[dict[str, Any]] = []
        prog(f"Voiceover for clip {i + 1}/{n}…")
        wav = work / f"vo_{i:02d}.wav"
        tts = _tts_line(line, voice_name or "Kore", wav)
        if tts:
            audio_path = str(tts)
            try:
                from core.ranking_pipeline import probe_duration
                duration = probe_duration(tts) or 2.0
            except Exception:
                duration = 2.0
            word_timings = _char_weighted_timings(line, duration)

        results.append({
            "clipIndex": i,
            "line": line,
            "label": parsed.get("label") or label,
            "audioPath": audio_path,
            "wordTimings": word_timings,
            "duration": duration,
        })

    return results
