"""Ranking AI commentary: Atlas gemini-3.5-flash watches clip video + xAI TTS."""
from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

ProgressCb = Callable[[str], None]

# Vision + punchy reactions — same class as ViewHunt / OzyRanks shorts.
RANKING_VISION_MODEL = "google/gemini-3.5-flash"


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
    reactions = [
        "bro what",
        "that was wild",
        "no way",
        "absolute cinema",
        "that hurt to watch",
        "pause. replay that.",
    ]
    return {
        "line": reactions[(max(1, rank) - 1) % len(reactions)],
        "label": label,
    }


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


def _is_junk_line(line: str) -> bool:
    s = re.sub(r"\s+", " ", str(line or "").lower()).strip()
    if not s or len(s) < 2:
        return True
    if s.startswith("http"):
        return True
    if "{" in s or "}" in s or "```" in s:
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
        avoid = "\nDo NOT repeat or paraphrase any of these earlier lines: " + json.dumps(prior_lines[-4:])
    shared = f"""You are a viral YouTube Shorts ranking narrator (OzyRanks / countdown style).
A short VIDEO CLIP is attached — watch it carefully.

Ranking title (context ONLY — NEVER read it aloud, NEVER quote it): "{ranking_title}"
On-screen rank number: #{clip_number} of {total_clips} (countdown; #1 is last).

CRITICAL — comment ONLY on what you SEE in the video:
- Describe / react to visible action, people, objects, motion, outcome
- Do NOT guess off-screen context
- Do NOT use clip filenames, upload titles, or the ranking title as the spoken line
- Do NOT say "ranking", "number {clip_number}", "these are", or read labels aloud
- No hashtags, emojis, or markdown

Return ONLY valid JSON:
{{"line":"<spoken voiceover>","label":"<1-4 word ALL CAPS rank tag from the action>"}}
{avoid}"""

    if role == "hook":
        return shared + """

ROLE: COLD-OPEN HOOK on the first clip (highest number).
"line" = intrigue / reaction to WHAT IS HAPPENING (4–12 words).
Examples of vibe (invent a fresh line for THIS footage):
- "He'll never do this again."
- "Bro is about to regret everything."
label = short tag for this moment."""

    if role == "cta":
        return shared + """

ROLE: FINAL CLIP (#1) — subscribe CTA tied to the ON-SCREEN action.
"line" must urge subscribe/follow using something VISIBLE about to happen (6–14 words).
Examples of vibe:
- "Subscribe before he cuts the rope if you're fast."
- "Hit subscribe before this goes wrong."
label = short tag for the #1 moment."""

    return shared + f"""

ROLE: MID-RANK reaction for #{clip_number}.
"line" = one punchy live reaction (3–10 words) to visible action.
Style: "bro folded", "where did her shoes go?", "that was close"
Do NOT say subscribe (save that for #1).
label = short tag for this moment."""


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
    """Short scaled silent sample so Gemini can watch the action (ViewHunt-style)."""
    ss = max(0.0, float(start_time or 0) + 0.35)
    if end_time is not None and float(end_time) > float(start_time or 0):
        avail = max(0.5, float(end_time) - ss)
        dur = min(3.8, avail)
    else:
        dur = 3.8
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(), "-y",
        "-ss", f"{ss:.3f}", "-i", str(clip_path),
        "-t", f"{dur:.3f}",
        "-vf", "scale=480:-2",
        "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        str(out_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if r.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 500:
            return out_path
    except Exception as e:
        print(f"[ranking_commentary] vision sample failed: {e}")
    return None


def _extract_vision_frames(sample_path: Path, work: Path, n: int = 4) -> list[Path]:
    frames: list[Path] = []
    for i in range(n):
        t = 0.2 + i * 0.85
        fp = work / f"vf_{sample_path.stem}_{i}.jpg"
        cmd = [
            _ffmpeg(), "-y", "-ss", f"{t:.2f}", "-i", str(sample_path),
            "-vframes", "1", "-q:v", "4", str(fp),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and fp.is_file() and fp.stat().st_size > 200:
                frames.append(fp)
        except Exception:
            pass
    return frames


def _atlas_vision_comment(prompt: str, sample_path: Path, frames_dir: Path) -> str:
    """Send clip video (or frames) to Atlas google/gemini-3.5-flash."""
    import httpx
    from core.atlas_llm import ATLAS_LLM_BASE, _atlas_key, _extract_atlas_message_text

    key = _atlas_key()
    if not key:
        raise RuntimeError("ATLASCLOUD_KEY missing for ranking vision")

    raw = sample_path.read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("Vision sample too large")

    # Prefer native video payload; fall back to sampled frames.
    video_b64 = base64.b64encode(raw).decode("ascii")
    content_video: list[dict[str, Any]] = [
        {"type": "text", "text": prompt},
        {
            "type": "video_url",
            "video_url": {"url": f"data:video/mp4;base64,{video_b64}"},
        },
    ]
    body = {
        "model": RANKING_VISION_MODEL,
        "messages": [{"role": "user", "content": content_video}],
        "max_tokens": 220,
        "temperature": 0.85,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _post(payload: dict) -> str:
        with httpx.Client(timeout=60) as client:
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

    try:
        return _post(body)
    except Exception as e:
        print(f"[ranking_commentary] video_url path failed ({e}); trying frames")

    frames = _extract_vision_frames(sample_path, frames_dir)
    if not frames:
        raise RuntimeError(f"No frames for vision fallback: {e}")
    parts: list[dict[str, Any]] = [
        {"type": "text", "text": prompt + "\n\n(Frames sampled from the clip follow.)"},
    ]
    for fp in frames:
        b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    return _post({
        "model": RANKING_VISION_MODEL,
        "messages": [{"role": "user", "content": parts}],
        "max_tokens": 220,
        "temperature": 0.85,
    })


def _tts_line(line: str, voice_name: str, out_path: Path) -> Path | None:
    """Synthesize via Atlas xAI TTS (same path as other recipes)."""
    try:
        from core.atlas_runtime import get_atlas_key
        from core.voiceover_gen import _atlas_tts_chunk, _ATLAS_VOICE_MAP
    except Exception as e:
        print(f"[ranking_commentary] TTS import failed: {e}")
        return None

    if not get_atlas_key():
        return None

    atlas_voice = (
        _ATLAS_VOICE_MAP.get(voice_name)
        or _ATLAS_VOICE_MAP.get((voice_name or "").lower())
        or "rex"
    )
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
    Mutates clip labels when vision returns a better short rank label (unless
    the user already typed one).
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
        label = user_label or f"#{rank}"
        prog(
            "Writing cold-open hook…" if role == "hook"
            else ("Writing subscribe CTA…" if role == "cta"
                  else f"Watching clip {i + 1}/{n}…")
        )
        fb = _fallback_for_role(clip, rank, n, role)
        parsed = fb
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
                    parsed = _parse_line_and_label(raw, fb["line"], fb["label"])
                    if _is_junk_line(parsed["line"]) or _is_repetitive_line(
                        parsed["line"], prior, parsed.get("label") or ""
                    ):
                        parsed["line"] = fb["line"]
                except Exception as e:
                    print(f"[ranking_commentary] clip {i} vision failed: {e}")
                    parsed = fb
            else:
                print(f"[ranking_commentary] clip {i}: no vision sample; using fallback")
        else:
            print(f"[ranking_commentary] clip {i}: missing path {clip_path}")

        # Prefer user-typed label over AI tag
        if user_label:
            parsed["label"] = _normalize_rank_label(user_label)
        elif parsed.get("label") and clips[i] is not None:
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
        else:
            # No orphan karaoke / white-card text without voice
            line = ""
            print(f"[ranking_commentary] clip {i}: TTS missing — skipping VO/karaoke")

        results.append({
            "clipIndex": i,
            "line": line,
            "label": parsed.get("label") or label,
            "audioPath": audio_path,
            "wordTimings": word_timings,
            "duration": duration,
        })

    ok = sum(1 for r in results if r.get("audioPath"))
    prog(f"Commentary ready ({ok}/{n} with voice)")
    return results
