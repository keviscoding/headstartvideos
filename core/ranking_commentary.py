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

# Vision only (watch the clip) — cheap Gemini Flash via Atlas. Never used for TTS.
RANKING_VISION_MODELS = (
    "google/gemini-3.5-flash",
    "google/gemini-2.5-flash",
    "google/gemini-3.1-flash-lite",
)

# Distilled from Keyos Ranks transcripts — vibe examples only (never copy verbatim).
_STYLE_INTRO_EXAMPLES = [
    "These are the funniest fishing moments.",
    "These are the best lightning strike moments.",
    "These are the dumbest criminal moments.",
    "These are the best clear tape pranks.",
]
_STYLE_REACT_EXAMPLES = [
    "She didn't expect that",
    "Bro is so cooked",
    "That was close",
    "He didn't expect that",
    "Bro's about to get in trouble",
    "She didn't see it coming",
    "That's got to hurt",
    "Where was bro aiming",
    "Poor guy didn't expect it",
    "Bro got silenced",
    "He miscalculated",
    "Little bro celebration went wrong",
]

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


def _fallback_for_role(clip: dict, rank: int, total: int, role: str) -> dict[str, str]:
    """Last-resort lines — only used when vision+TTS path cannot produce a line."""
    label = _normalize_rank_label(clip.get("label") or f"#{rank}")
    if role == "hook":
        return {"line": "These are the moments you need to see", "label": label}
    if role == "cta":
        return {"line": "Subscribe before the next one hits", "label": label}
    # Prefer variety over the old repeating "that was wild" loop.
    reactions = list(_STYLE_REACT_EXAMPLES)
    return {
        "line": reactions[(max(1, rank) - 1) % len(reactions)],
        "label": label,
    }


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
            if line and not _is_junk_line(line):
                return {
                    "line": line,
                    "label": _normalize_rank_label(lab or fallback_label),
                }
    except Exception:
        pass
    parts = re.split(r"\s*\|\|\s*|\s*\|\s*", text)
    if len(parts) >= 2:
        line = _strip_meta_prefixes(parts[0])
        if line and not _is_junk_line(line):
            return {
                "line": line,
                "label": _normalize_rank_label(" ".join(parts[1:]) or fallback_label),
            }
    # Plain prose — take first short sentence only
    sentence = re.split(r"[\n.]", text)[0].strip()
    sentence = _strip_meta_prefixes(sentence)
    if sentence and not _is_junk_line(sentence) and len(sentence.split()) <= 16:
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
    intro_ex = "; ".join(f'"{e}"' for e in _STYLE_INTRO_EXAMPLES[:3])

    return f"""You write short ranking-video commentary like successful YouTube Shorts channels.

A short VIDEO (or frames) of ONE clip is attached. Watch the action carefully.
React ONLY to what is visibly happening — people, motion, outcome, objects.

Ranking topic (context only — never speak it, never quote it): {ranking_title!r}
This is clip #{clip_number} of {total_clips} in a countdown (#1 is last).

STYLE (learn the vibe — invent a FRESH line for THIS footage):
- Spoken lines are SHORT: usually 3–10 words (hook/CTA may be up to 12).
- Casual, reactive, specific to the moment (not generic hype).
- React examples (vibe only): {examples}
- Intro vibe examples: {intro_ex}

ROLE: {"COLD-OPEN HOOK — short intro like the vibe examples (you may paraphrase the topic as “These are the … moments”) PLUS a quick reaction to what is on screen." if role == "hook" else ("FINAL clip (#1) — quick subscribe CTA tied to the visible action." if role == "cta" else f"MID-RANK reaction for #{clip_number} — one punchy live reaction. Do not say subscribe.")}

HARD RULES:
- Comment on visible action only (hook may also name the ranking theme in one short intro sentence).
- Never guess off-screen backstory.
- Never say "ranking number", read file names, or recite instructions.
- No hashtags, emojis, markdown, or stage directions like [laughter].
- Never mention JSON, prompts, formats, constraints, or output rules.

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
            return _post({
                "model": model,
                "messages": [{"role": "user", "content": parts}],
                "max_tokens": 160,
                "temperature": 0.9,
            })
        except Exception as e:
            last_err = e
            print(f"[ranking_commentary] vision model {model} failed: {e}")

    # Last resort: short video_url payload with first model
    try:
        raw = sample_path.read_bytes()
        if len(raw) <= 3 * 1024 * 1024:
            video_b64 = base64.b64encode(raw).decode("ascii")
            return _post({
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
                "max_tokens": 160,
                "temperature": 0.9,
            })
    except Exception as e:
        last_err = e
        print(f"[ranking_commentary] video_url vision failed: {e}")

    raise RuntimeError(f"Ranking vision failed: {last_err}")


def _tts_safe_text(line: str) -> str:
    s = re.sub(r"\s+", " ", str(line or "")).strip()
    s = re.sub(r"[^\w\s'.,!?\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:160]


def _openai_api_key() -> str:
    import os
    import config as _cfg
    return (
        (os.getenv("OPENAI_API_KEY") or "").strip()
        or (getattr(_cfg, "OPENAI_API_KEY", "") or "").strip()
    )


def _openai_voice_id(voice_name: str) -> str:
    # ViewHunt ranking voice map (Gemini UI names → OpenAI TTS voices)
    mapping = {
        "kore": "nova", "puck": "onyx", "charon": "echo", "fenrir": "fable",
        "aoede": "shimmer", "leda": "nova", "orus": "onyx", "zephyr": "alloy",
        "nova": "nova", "onyx": "onyx", "echo": "echo", "fable": "fable",
        "shimmer": "shimmer", "alloy": "alloy",
    }
    return mapping.get((voice_name or "").strip().lower(), "nova")


def _tts_openai(line: str, voice_name: str, out_path: Path) -> Path | None:
    """OpenAI tts-1-hd — ViewHunt's ranking VO path (not Gemini)."""
    key = _openai_api_key()
    if not key:
        return None
    try:
        import httpx
    except Exception as e:
        print(f"[ranking_commentary] OpenAI TTS import failed: {e}")
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Prefer wav; fall back to mp3 then ffmpeg-convert if needed
    for fmt, suffix in (("wav", ".wav"), ("mp3", ".mp3")):
        dest = out_path if out_path.suffix.lower() == suffix else out_path.with_suffix(suffix)
        try:
            with httpx.Client(timeout=90) as client:
                r = client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "tts-1-hd",
                        "voice": _openai_voice_id(voice_name),
                        "input": line,
                        "speed": 1.2,
                        "response_format": fmt,
                    },
                )
            if r.status_code >= 400:
                raise RuntimeError(f"OpenAI TTS {r.status_code}: {r.text[:240]}")
            dest.write_bytes(r.content)
            if dest.stat().st_size < 400:
                raise RuntimeError("OpenAI TTS empty audio")
            if dest.resolve() != out_path.resolve():
                # Normalize to the requested wav path for the mixer
                subprocess.run(
                    [_ffmpeg(), "-y", "-i", str(dest), "-ar", "24000", "-ac", "1", str(out_path)],
                    capture_output=True, text=True, timeout=60,
                )
                if out_path.is_file() and out_path.stat().st_size > 400:
                    return out_path
                return dest if dest.is_file() else None
            return out_path
        except Exception as e:
            print(f"[ranking_commentary] OpenAI TTS ({fmt}) failed: {e}")
    return None


def _tts_atlas_xai(line: str, voice_name: str, out_path: Path) -> Path | None:
    """Atlas Cloud xAI TTS — ChannelRecipe's standard voiceover path (not Gemini)."""
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
            _atlas_tts_chunk(line, atlas_voice, str(out_path))
            if out_path.is_file() and out_path.stat().st_size > 500:
                print(f"[ranking_commentary] Atlas xAI TTS ok voice={atlas_voice}")
                return out_path
        except Exception as e:
            print(f"[ranking_commentary] Atlas TTS failed voice={atlas_voice}: {e}")
    return None


def _tts_line(line: str, voice_name: str, out_path: Path) -> Path | None:
    """Speak the line. Never Gemini TTS — OpenAI first (ViewHunt), then Atlas xAI."""
    text = _tts_safe_text(line)
    if not text or _is_junk_line(text):
        print(f"[ranking_commentary] TTS refused junk/empty line={line!r}")
        return None

    # Prefer OpenAI when keyed (same as ViewHunt ranking). Else Atlas xAI.
    tts = _tts_openai(text, voice_name or "Kore", out_path)
    if tts:
        return tts
    return _tts_atlas_xai(text, voice_name or "Kore", out_path)


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
        role = "hook" if i == 0 else ("cta" if i == n - 1 else "react")
        rank = int(clip.get("number") or (n - i))
        user_label = str(clip.get("label") or "").strip()
        # Ignore placeholder "#3" style labels as "user typed"
        if re.fullmatch(r"#?\d+", user_label or ""):
            user_label = ""
        label = user_label or f"#{rank}"
        prog(
            "Writing cold-open hook…" if role == "hook"
            else ("Writing subscribe CTA…" if role == "cta"
                  else f"Watching clip {i + 1}/{n}…")
        )
        fb = _fallback_for_role(clip, rank, n, role)
        parsed = dict(fb)
        vision_ok = False
        clip_path = Path(str(clip.get("path") or ""))
        if clip_path.is_file():
            sample = _make_vision_sample(
                clip_path,
                start_time=float(clip.get("startTime") or 0),
                end_time=clip.get("endTime"),
                out_path=samples / f"vision_{i:02d}.mp4",
            )
            if sample:
                try:
                    raw = _atlas_vision_comment(
                        _role_prompt(role, ranking_title, rank, n, prior),
                        sample,
                        samples,
                    )
                    candidate = _parse_line_and_label(raw, fb["line"], fb["label"])
                    if (
                        not _is_junk_line(candidate["line"])
                        and not _is_repetitive_line(
                            candidate["line"], prior, candidate.get("label") or ""
                        )
                    ):
                        parsed = candidate
                        vision_ok = True
                    else:
                        print(
                            f"[ranking_commentary] clip {i}: rejected vision line "
                            f"{candidate.get('line')!r}; using fallback"
                        )
                except Exception as e:
                    print(f"[ranking_commentary] clip {i} vision failed: {e}")
            else:
                print(f"[ranking_commentary] clip {i}: no vision sample; using fallback")
        else:
            print(f"[ranking_commentary] clip {i}: missing path {clip_path}")

        # Prefer user-typed label; only apply AI tag when clean + vision succeeded
        if user_label:
            parsed["label"] = _normalize_rank_label(user_label)
        elif vision_ok and parsed.get("label") and not _is_junk_label(parsed["label"]):
            clips[i]["label"] = parsed["label"]
        else:
            parsed["label"] = _normalize_rank_label(label)

        line = (parsed.get("line") or fb["line"]).strip()
        if _is_junk_line(line):
            line = fb["line"]
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
