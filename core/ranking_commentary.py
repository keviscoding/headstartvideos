"""Ranking AI commentary: Gemini watches each clip, then xAI TTS speaks the line.

Style is distilled from successful Keyos-style ranking transcripts (short casual
reactions), not hard-coded per clip. The model must react to what it sees.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

ProgressCb = Callable[[str], None]

# Vision only (watch the clip) via Atlas Gemini. Never used for TTS.
# 3.1-flash-lite is the reliable default on Atlas; 3.5-flash often truncates at low max_tokens.
RANKING_VISION_MODELS = (
    "google/gemini-3.1-flash-lite",
    "google/gemini-2.5-flash",
    "google/gemini-3.5-flash",
)

# Distilled from Keyos Ranks transcripts — vibe only (never copy verbatim).
_STYLE_REACT_EXAMPLES = [
    "She didn't expect that",
    "That was close",
    "He didn't expect that",
    "Bro's about to get in trouble",
    "She didn't see it coming",
    "That's got to hurt",
    "Where was bro aiming",
    "Poor guy didn't expect it",
    "He miscalculated",
    "Little bro celebration went wrong",
]

# Old hook intros + sticky canned fallbacks — never speak these.
_BANNED_LINE_RE = re.compile(
    r"(?i)^\s*("
    r"these are the .+ moments?.*"
    r"|these are the moments you need to see"
    r"|bro is so cooked"
    r"|bro is cooked"
    r"|subscribe before the next one hits"
    r")\s*[.!]?\s*$"
)

_META_BLEED_RE = re.compile(
    r"(?i)\b("
    r"output\s*format|specific\s*constraint|return\s*only|valid\s*json|"
    r"spoken\s*voiceover|rank(?:ing)?\s*title|never\s*read|do\s*not\s*(?:say|use|guess)|"
    r"critical|system\s*prompt|assistant|instructions?|"
    r"watch\s+it\s+carefully|on-screen\s+rank|clip\s*filename|"
    r"examples?\s+of\s+vibe|invent\s+a\s+fresh"
    r")\b"
)


def _normalize_rank_label(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9 !?]", " ", str(label or ""))
    s = re.sub(r"\s+", " ", s).strip().upper()
    if not s:
        return "MOMENT"
    if _is_junk_label(s):
        return "MOMENT"
    words = s.split()[:4]
    return " ".join(words)[:24]


def _normalize_line_key(line: str) -> str:
    s = re.sub(r"[^a-z0-9\s]", " ", str(line or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _is_banned_line(line: str) -> bool:
    s = str(line or "").strip()
    if not s:
        return True
    if _BANNED_LINE_RE.match(s):
        return True
    if re.search(r"(?i)^\s*these are the\b", s):
        return True
    key = _normalize_line_key(s)
    for ex in _STYLE_REACT_EXAMPLES:
        if key == _normalize_line_key(ex):
            return True
    return False


def _strip_meta_prefixes(text: str) -> str:
    s = str(text or "").strip().strip("\"'`")
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s).strip()
    # Drop / scrub common model preface lines without discarding the spoken bit.
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    kept = []
    for ln in lines:
        low = ln.lower()
        if _META_BLEED_RE.search(ln):
            # Keep trailing content after a meta label: "Output format: bro folded"
            cleaned = _META_BLEED_RE.sub(" ", ln)
            cleaned = re.sub(r"(?i)^\s*(line|commentary|voice|spoken|reaction|label)\s*[:=]\s*", "", cleaned)
            cleaned = re.sub(r"^[\s:.\-–—]+", "", cleaned).strip()
            if cleaned and not _META_BLEED_RE.search(cleaned):
                kept.append(cleaned)
            continue
        if low.startswith(("here is", "here's", "sure,", "okay,", "json")):
            continue
        kept.append(ln)
    if kept:
        s = " ".join(kept)
    s = re.sub(
        r"(?i)^\s*(line|commentary|voice|spoken|reaction)\s*[:=]\s*",
        "",
        s,
    ).strip()
    return s.strip("\"'")


def _parse_line_and_label(raw: str, fallback_line: str, fallback_label: str) -> dict[str, str]:
    text = _strip_meta_prefixes(raw)
    if not text:
        return {"line": fallback_line, "label": _normalize_rank_label(fallback_label)}
    try:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            obj = json.loads(m.group(0))
            line = str(
                obj.get("line") or obj.get("commentary") or obj.get("voice") or ""
            ).strip()
            lab = str(
                obj.get("label") or obj.get("rankLabel") or obj.get("title") or ""
            ).strip()
            line = _strip_meta_prefixes(line)
            if line and not _is_junk_line(line) and not _is_banned_line(line):
                return {
                    "line": line,
                    "label": _normalize_rank_label(lab or fallback_label),
                }
    except Exception:
        pass
    parts = re.split(r"\s*\|\|\s*|\s*\|\s*", text)
    if len(parts) >= 2:
        line = _strip_meta_prefixes(parts[0])
        if line and not _is_junk_line(line) and not _is_banned_line(line):
            return {
                "line": line,
                "label": _normalize_rank_label(" ".join(parts[1:]) or fallback_label),
            }
    # Plain prose — take first short sentence only
    sentence = re.split(r"[\n.]", text)[0].strip()
    sentence = _strip_meta_prefixes(sentence)
    if (
        sentence
        and not _is_junk_line(sentence)
        and not _is_banned_line(sentence)
        and len(sentence.split()) <= 16
    ):
        return {"line": sentence, "label": _normalize_rank_label(fallback_label)}
    return {"line": fallback_line, "label": _normalize_rank_label(fallback_label)}


def _is_junk_label(label: str) -> bool:
    s = re.sub(r"\s+", " ", str(label or "")).strip()
    if not s or len(s) < 2:
        return True
    if _META_BLEED_RE.search(s):
        return True
    low = s.lower()
    if any(x in low for x in ("output", "format", "constraint", "json", "prompt", "role")):
        return True
    return False


def _is_junk_line(line: str) -> bool:
    s = re.sub(r"\s+", " ", str(line or "").lower()).strip()
    if not s or len(s) < 2:
        return True
    if s.startswith("http"):
        return True
    if "{" in s or "}" in s or "```" in s:
        return True
    if _META_BLEED_RE.search(s):
        return True
    if re.search(r"\b(json|markdown|hashtag|emoji|voiceover script)\b", s):
        return True
    # Too long = model essay / instructions, not a ranking beat
    if len(s.split()) > 16:
        return True
    if _is_banned_line(s):
        return True
    return False


def _is_repetitive_line(line: str, prior_lines: list[str], label: str) -> bool:
    s = re.sub(r"[^a-z0-9\s]", " ", str(line or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return True
    lab = re.sub(r"[^a-z0-9\s]", " ", str(label or "").lower())
    lab = re.sub(r"\s+", " ", lab).strip()
    if lab and (s == lab or s == f"the {lab}" or (s.startswith(lab) and len(s.split()) <= len(lab.split()) + 2)):
        return True
    words = s.split()
    for p in prior_lines or []:
        q = re.sub(r"[^a-z0-9\s]", " ", str(p or "").lower())
        q = re.sub(r"\s+", " ", q).strip()
        if not q:
            continue
        if s == q:
            return True
        pw = q.split()
        if len(words) >= 3 and len(pw) >= 3 and words[:3] == pw[:3]:
            return True
    return False


def _role_prompt(
    role: str,
    ranking_title: str,
    clip_number: int,
    total_clips: int,
    prior_lines: list[str],
) -> str:
    avoid = ""
    if prior_lines:
        avoid = "\nDo not repeat or paraphrase: " + json.dumps(prior_lines[-6:])

    examples = "; ".join(f'"{e}"' for e in _STYLE_REACT_EXAMPLES[:8])
    if role == "cta":
        role_line = (
            "FINAL clip (#1) — one short reaction to the visible action, "
            "optionally ending with a tiny subscribe nudge (max 12 words)."
        )
    else:
        role_line = (
            f"Clip #{clip_number} of {total_clips} — one punchy live reaction "
            "to what is on screen RIGHT NOW (3–10 words). "
            "Do NOT introduce the ranking. Do NOT say “these are the … moments”."
        )

    return f"""You write short ranking-video commentary like successful YouTube Shorts channels.

A short VIDEO (or frames) of ONE clip is attached. Watch the action carefully.
React ONLY to what is visibly happening — people, motion, outcome, objects.

Ranking topic (context only — never speak it, never quote it): {ranking_title!r}
This is clip #{clip_number} of {total_clips} in a countdown (#1 is last).

STYLE (learn the vibe — invent a FRESH line for THIS footage):
- Spoken lines are SHORT: usually 3–10 words.
- Casual, reactive, specific to the moment (not generic hype).
- Vibe examples (do NOT copy these words): {examples}

ROLE: {role_line}

HARD RULES:
- Comment on visible action only.
- Never open with an intro / cold-open / “these are the …” framing — not even on the first clip.
- Never guess off-screen backstory.
- Never say "ranking number", read file names, or recite instructions.
- No hashtags, emojis, markdown, or stage directions like [laughter].
- Never mention JSON, prompts, formats, constraints, or output rules.
- Never reuse example phrases verbatim.

Reply with ONLY this JSON object (no other text):
{{"line":"<spoken words>","label":"<1-4 word ALL CAPS tag of the action>"}}
{avoid}"""


def _ffmpeg() -> str:
    import os
    import shutil
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"


def _make_vision_sample(
    clip_path: Path,
    *,
    start_time: float = 0.0,
    end_time: float | None,
    out_path: Path,
) -> Path | None:
    """Short scaled silent sample so Gemini can watch the action."""
    ss = max(0.0, float(start_time or 0) + 0.25)
    if end_time is not None and float(end_time) > float(start_time or 0):
        avail = max(0.5, float(end_time) - ss)
        dur = min(4.2, avail)
    else:
        dur = 4.2
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(), "-y",
        "-ss", f"{ss:.3f}", "-i", str(clip_path),
        "-t", f"{dur:.3f}",
        "-vf", "scale=480:-2",
        "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        str(out_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if r.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 500:
            return out_path
    except Exception as e:
        print(f"[ranking_commentary] vision sample failed: {e}")
    return None


def _extract_vision_frames(sample_path: Path, work: Path, n: int = 5) -> list[Path]:
    frames: list[Path] = []
    for i in range(n):
        t = 0.15 + i * 0.75
        fp = work / f"vf_{sample_path.stem}_{i}.jpg"
        cmd = [
            _ffmpeg(), "-y", "-ss", f"{t:.2f}", "-i", str(sample_path),
            "-vframes", "1", "-q:v", "3", str(fp),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and fp.is_file() and fp.stat().st_size > 200:
                frames.append(fp)
        except Exception:
            pass
    return frames


def _atlas_vision_comment(prompt: str, sample_path: Path, frames_dir: Path) -> str:
    """Send clip frames to Atlas Gemini (vision only — never TTS)."""
    import httpx
    from core.atlas_llm import ATLAS_LLM_BASE, _atlas_key, _extract_atlas_message_text

    key = _atlas_key()
    if not key:
        raise RuntimeError("ATLASCLOUD_KEY missing for ranking vision")

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _post(payload: dict) -> str:
        with httpx.Client(timeout=75) as client:
            resp = client.post(
                f"{ATLAS_LLM_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Atlas vision {resp.status_code}: {resp.text[:350]}")
            data = resp.json()
        msg = ((data.get("choices") or [{}])[0].get("message")) or {}
        text = (_extract_atlas_message_text(msg) or "").strip()
        if not text:
            raise RuntimeError("Atlas vision returned empty content")
        return text

    frames = _extract_vision_frames(sample_path, frames_dir)
    if not frames:
        raise RuntimeError("No frames extracted for vision")

    parts: list[dict[str, Any]] = [
        {"type": "text", "text": prompt + "\n\nFrames from the clip follow. Describe what you see."},
    ]
    for fp in frames:
        b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    last_err: Exception | None = None
    for model in RANKING_VISION_MODELS:
        try:
            text = _post({
                "model": model,
                "messages": [{"role": "user", "content": parts}],
                "max_tokens": 256,
                "temperature": 0.9,
            })
            # Truncated JSON from flash models looks like '{"line":"' — reject & retry.
            if not text or text.count("{") != text.count("}") or '"line"' not in text:
                raise RuntimeError(f"truncated/invalid vision JSON from {model}: {text[:120]!r}")
            print(f"[ranking_commentary] vision ok model={model} chars={len(text)}")
            return text
        except Exception as e:
            last_err = e
            print(f"[ranking_commentary] vision model {model} failed: {e}")

    # Last resort: short video_url payload with flash-lite
    try:
        raw = sample_path.read_bytes()
        if len(raw) <= 3 * 1024 * 1024:
            video_b64 = base64.b64encode(raw).decode("ascii")
            text = _post({
                "model": RANKING_VISION_MODELS[0],
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "video_url",
                            "video_url": {"url": f"data:video/mp4;base64,{video_b64}"},
                        },
                    ],
                }],
                "max_tokens": 256,
                "temperature": 0.9,
            })
            if text and '"line"' in text:
                return text
            raise RuntimeError(f"video_url invalid JSON: {text[:120]!r}")
    except Exception as e:
        last_err = e
        print(f"[ranking_commentary] video_url vision failed: {e}")

    raise RuntimeError(f"Ranking vision failed: {last_err}")


def _tts_safe_text(line: str) -> str:
    s = re.sub(r"\s+", " ", str(line or "")).strip()
    s = re.sub(r"[^\w\s'.,!?\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:160]


def _tts_line(line: str, voice_name: str, out_path: Path) -> Path | None:
    """Speak the line via Atlas xAI TTS only (Gemini is vision-only)."""
    text = _tts_safe_text(line)
    if not text or _is_junk_line(text):
        print(f"[ranking_commentary] TTS refused junk/empty line={line!r}")
        return None

    try:
        from core.atlas_runtime import get_atlas_key
        from core.voiceover_gen import _atlas_tts_chunk, _ATLAS_VOICE_MAP
    except Exception as e:
        print(f"[ranking_commentary] Atlas TTS import failed: {e}")
        return None

    if not get_atlas_key():
        print("[ranking_commentary] ATLASCLOUD_KEY missing for Atlas TTS")
        return None

    primary = (
        _ATLAS_VOICE_MAP.get(voice_name)
        or _ATLAS_VOICE_MAP.get((voice_name or "").lower())
        or "rex"
    )
    voices = [primary]
    for v in ("rex", "leo", "eve"):
        if v not in voices:
            voices.append(v)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    for atlas_voice in voices:
        try:
            if out_path.is_file():
                try:
                    out_path.unlink()
                except Exception:
                    pass
            _atlas_tts_chunk(text, atlas_voice, str(out_path))
            if out_path.is_file() and out_path.stat().st_size > 500:
                print(f"[ranking_commentary] Atlas xAI TTS ok voice={atlas_voice}")
                return out_path
        except Exception as e:
            print(f"[ranking_commentary] Atlas TTS failed voice={atlas_voice}: {e}")
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
    require_audio: bool = True,
) -> list[dict[str, Any]]:
    """
    Returns list of {clipIndex, line, label, audioPath, wordTimings, duration}.

    When require_audio=True (default), raises if any clip fails TTS — so we never
    ship silent "text-only" commentary cooks.
    """
    def prog(msg: str) -> None:
        if progress:
            progress(msg)
        print(f"[ranking_commentary] {msg}")

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    samples = work / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    prior: list[str] = []
    n = len(clips)

    for i, clip in enumerate(clips):
        # Every clip is a short on-screen reaction — no cold-open intro on clip 1.
        role = "cta" if i == n - 1 else "react"
        rank = int(clip.get("number") or (n - i))
        user_label = str(clip.get("label") or "").strip()
        # Ignore placeholder "#3" style labels as "user typed"
        if re.fullmatch(r"#?\d+", user_label or ""):
            user_label = ""
        label = user_label or f"#{rank}"
        prog(
            "Writing subscribe CTA…" if role == "cta"
            else f"Watching clip {i + 1}/{n}…"
        )
        parsed: dict[str, str] | None = None
        vision_ok = False
        last_err = "no usable vision line"
        clip_path = Path(str(clip.get("path") or ""))
        if not clip_path.is_file():
            raise RuntimeError(f"AI commentary missing clip file for clip {i + 1}/{n}")

        sample = _make_vision_sample(
            clip_path,
            start_time=float(clip.get("startTime") or 0),
            end_time=clip.get("endTime"),
            out_path=samples / f"vision_{i:02d}.mp4",
        )
        if not sample:
            raise RuntimeError(
                f"AI commentary could not sample clip {i + 1}/{n} for vision. "
                "Try again — credits are refunded if the cook fails."
            )

        for attempt in range(2):
            prompt = _role_prompt(role, ranking_title, rank, n, prior)
            if attempt == 1:
                prompt += (
                    "\nRETRY: previous line was unusable. "
                    "Describe the single most obvious visible action in 3–8 words."
                )
            try:
                raw = _atlas_vision_comment(prompt, sample, samples)
                candidate = _parse_line_and_label(raw, "", label)
                cand_line = (candidate.get("line") or "").strip()
                if (
                    cand_line
                    and not _is_junk_line(cand_line)
                    and not _is_banned_line(cand_line)
                    and not _is_repetitive_line(
                        cand_line, prior, candidate.get("label") or ""
                    )
                ):
                    parsed = candidate
                    vision_ok = True
                    break
                last_err = f"rejected line {cand_line!r}"
                print(f"[ranking_commentary] clip {i}: {last_err}")
            except Exception as e:
                last_err = str(e)
                print(f"[ranking_commentary] clip {i} vision failed: {e}")

        if not parsed or not vision_ok:
            raise RuntimeError(
                f"AI commentary could not read clip {i + 1}/{n} ({last_err}). "
                "Try again — credits are refunded if the cook fails."
            )

        # Prefer user-typed label; only apply AI tag when clean + vision succeeded
        if user_label:
            parsed["label"] = _normalize_rank_label(user_label)
        elif parsed.get("label") and not _is_junk_label(parsed["label"]):
            clips[i]["label"] = parsed["label"]
        else:
            parsed["label"] = _normalize_rank_label(label)

        line = (parsed.get("line") or "").strip()
        if _is_junk_line(line) or _is_banned_line(line):
            raise RuntimeError(
                f"AI commentary produced an unusable line on clip {i + 1}/{n}. "
                "Try again — credits are refunded if the cook fails."
            )
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
        else:
            # Never leave orphan karaoke / white-card text without voice
            print(f"[ranking_commentary] clip {i}: TTS missing for line={line!r}")
            if require_audio:
                raise RuntimeError(
                    f"AI commentary voiceover failed on clip {i + 1}/{n}. "
                    "Try again in a moment — credits are refunded if the cook fails."
                )
            line = ""

        results.append({
            "clipIndex": i,
            "line": line,
            "label": parsed.get("label") or label,
            "audioPath": audio_path,
            "wordTimings": word_timings,
            "duration": duration,
            "visionOk": vision_ok,
        })

    ok = sum(1 for r in results if r.get("audioPath"))
    prog(f"Commentary ready ({ok}/{n} with voice)")
    if require_audio and ok < n:
        raise RuntimeError(
            f"AI commentary incomplete ({ok}/{n} voiceovers). Cook aborted so you aren't charged for silent text."
        )
    return results
