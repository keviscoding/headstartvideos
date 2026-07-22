"""
Visual QC for ChannelRecipe cooks.

Preferred path: Gemini Files API (full mp4) when ALLOW_GOOGLE_GEMINI=1 works.
Fallback (production today): Groq transcript + sampled frames via Atlas multimodal.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from config import GEMINI_KEY, GEMINI_TEXT_MODEL

ProgressFn = Callable[[str], None] | None

CRITIQUE_PROMPT = """\
You are a senior YouTube documentary / faceless B-roll editor doing ruthless QC.

Watch / inspect the video evidence carefully. The intended topic and upload kit are below.
Catch every visual failure a viewer would feel — especially irrelevant B-roll, slow swap
rate, demographic mismatch, and story gaps.

INTENDED TITLE:
{title}

UPLOAD DESCRIPTION (intent):
{description}

TAGS / CONTEXT:
{tags}

Respond with ONLY valid JSON (no markdown fences):
{{
  "overall_score": 0-100,
  "verdict": "one blunt sentence",
  "primary_failures": ["short bullets"],
  "pacing": {{
    "avg_shot_hold_sec_estimate": number,
    "target_hold_sec_for_this_niche": number,
    "too_static": true/false,
    "notes": "..."
  }},
  "scenes": [
    {{
      "start_sec": 0,
      "end_sec": 12,
      "what_is_on_screen": "literal description",
      "what_narration_likely_needs": "what the VO/story needs visually here",
      "relevance": "match" | "weak" | "mismatch" | "dead",
      "issues": ["specific faults"],
      "severity": "critical" | "major" | "minor" | "ok",
      "fix": "replace_broll" | "cut_shorter" | "insert_cutaway" | "keep" | "regenerate_segment",
      "better_visual": "concrete alternative B-roll that would land"
    }}
  ],
  "swap_rate_verdict": "too_slow" | "ok" | "too_fast",
  "demographic_fit": "how well visuals match the stated audience",
  "story_arc_gaps": ["beats promised by title/desc that never appear visually"],
  "must_fix_before_ship": ["ordered list of highest-leverage fixes"]
}}

RULES:
- Cover the full runtime with contiguous scenes (roughly 8–20 scenes for a ~3–4 min video).
- Be specific and literal about what you see (shop names, objects, gender, era).
- "mismatch" if B-roll is fashion-adjacent but wrong (e.g. men's tailoring for women's 80s vintage shopping).
- "dead" if decorative filler (flowers, empty hangers, measuring tapes) that does not advance the story.
- Prefer many short relevant shots over long holds of weak stock.
- If B-roll barely changes while VO moves to a new idea, mark pacing failure.
"""


def google_video_qc_enabled() -> bool:
    return (os.getenv("ALLOW_GOOGLE_GEMINI", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _parse_json_loose(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _wait_file_active(client, file_obj, *, timeout_sec: float = 300.0, progress: ProgressFn = None):
    name = getattr(file_obj, "name", None) or ""
    t0 = time.time()
    while True:
        f = client.files.get(name=name)
        state = getattr(getattr(f, "state", None), "name", None) or str(getattr(f, "state", ""))
        if state in ("ACTIVE", "FileState.ACTIVE"):
            return f
        if state in ("FAILED", "FileState.FAILED"):
            raise RuntimeError(f"Gemini file processing failed: {f}")
        if time.time() - t0 > timeout_sec:
            raise TimeoutError(f"Gemini file not ACTIVE after {timeout_sec}s (state={state})")
        if progress:
            progress(f"Gemini processing upload… ({state})")
        time.sleep(2.0)


def _critique_via_gemini_file(
    path: Path,
    *,
    title: str,
    description: str,
    tags: str,
    model: str | None,
    progress: ProgressFn,
) -> dict[str, Any]:
    from google import genai

    client = genai.Client(api_key=GEMINI_KEY)
    model_id = (model or GEMINI_TEXT_MODEL or "gemini-2.5-flash").strip()
    if progress:
        progress(f"Uploading {path.name} to Gemini ({path.stat().st_size // 1_000_000}MB)…")
    uploaded = client.files.upload(file=str(path))
    uploaded = _wait_file_active(client, uploaded, progress=progress)
    if progress:
        progress("Gemini watching video — writing scene critique…")
    prompt = CRITIQUE_PROMPT.format(
        title=title or path.stem,
        description=description or "(none provided)",
        tags=tags or "(none)",
    )
    response = client.models.generate_content(
        model=model_id,
        contents=[uploaded, prompt],
    )
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty critique")
    data = _parse_json_loose(text)
    data["_meta"] = {
        "video_path": str(path),
        "model": model_id,
        "title": title,
        "bytes": path.stat().st_size,
        "method": "gemini_files_api",
    }
    try:
        client.files.delete(name=uploaded.name)
    except Exception:
        pass
    return data


def _extract_frames(path: Path, out_dir: Path, *, every_sec: float = 3.0) -> list[Path]:
    import subprocess

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.jpg"):
        old.unlink()
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-vf", f"fps=1/{every_sec}",
        "-q:v", "4",
        str(out_dir / "f_%03d.jpg"),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return sorted(out_dir.glob("f_*.jpg"))


def _transcribe_for_qc(path: Path, work_dir: Path) -> str:
    import subprocess
    from core.segmenter import _transcribe_groq

    wav = work_dir / "audio.wav"
    if not wav.exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000",
                str(wav),
            ],
            check=True,
            capture_output=True,
        )
    words = _transcribe_groq(str(wav))
    if not words:
        return "(no speech transcribed)"
    chunks: list[str] = []
    cur: list[str] = []
    cur_start = float(words[0]["start"])
    for w in words:
        cur.append(w["word"])
        if float(w["end"]) - cur_start >= 12 or w is words[-1]:
            chunks.append(f"[{cur_start:.1f}-{float(w['end']):.1f}] {' '.join(cur)}")
            cur = []
            cur_start = float(w["end"])
    text = "\n".join(chunks)
    (work_dir / "transcript.txt").write_text(text, encoding="utf-8")
    return text


def _critique_via_atlas_frames(
    path: Path,
    *,
    title: str,
    description: str,
    tags: str,
    work_dir: Path,
    progress: ProgressFn,
    frame_every_sec: float = 3.0,
    max_frames: int = 16,
) -> dict[str, Any]:
    import httpx
    from core.atlas_llm import ATLAS_LLM_BASE, ATLAS_TEXT_MODEL, _atlas_key, _extract_atlas_message_text

    if progress:
        progress("Extracting frames + transcript for Atlas vision QC…")
    frames_dir = work_dir / "frames"
    frames = _extract_frames(path, frames_dir, every_sec=frame_every_sec)
    if not frames:
        raise RuntimeError("No frames extracted for QC")
    if len(frames) > max_frames:
        step = len(frames) / max_frames
        frames = [frames[int(i * step)] for i in range(max_frames)]
    transcript = _transcribe_for_qc(path, work_dir)

    prompt = CRITIQUE_PROMPT.format(
        title=title or path.stem,
        description=description or "(none provided)",
        tags=tags or "(none)",
    )
    prompt += (
        "\n\nVO TRANSCRIPT (timestamped):\n"
        + transcript
        + "\n\nSampled frames follow (labels are approximate timestamps)."
    )

    parts: list[dict] = [{"type": "text", "text": prompt}]
    for fp in frames:
        m = re.search(r"f_(\d+)", fp.stem)
        idx = int(m.group(1)) if m else 1
        t = (idx - 1) * frame_every_sec
        b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
        parts.append({"type": "text", "text": f"FRAME ~{t:.0f}s:"})
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    key = _atlas_key()
    if not key:
        raise RuntimeError("ATLASCLOUD_KEY required for frame-based QC fallback")

    if progress:
        progress(f"Atlas watching {len(frames)} frames + transcript…")
    body = {
        "model": ATLAS_TEXT_MODEL,
        "messages": [{"role": "user", "content": parts}],
        "max_tokens": 8192,
    }
    with httpx.Client(timeout=240) as client:
        resp = client.post(
            f"{ATLAS_LLM_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Atlas vision QC {resp.status_code}: {resp.text[:400]}")
        payload = resp.json()
    msg = ((payload.get("choices") or [{}])[0].get("message")) or {}
    text = _extract_atlas_message_text(msg)
    if not text:
        raise RuntimeError("Atlas vision QC returned empty content")
    data = _parse_json_loose(text)
    data["_meta"] = {
        "video_path": str(path),
        "model": ATLAS_TEXT_MODEL,
        "title": title,
        "bytes": path.stat().st_size,
        "method": "atlas_frames_plus_transcript",
        "frames": len(frames),
        "work_dir": str(work_dir),
    }
    return data


def critique_local_video(
    video_path: str | Path,
    *,
    title: str = "",
    description: str = "",
    tags: str = "",
    script: str = "",
    model: str | None = None,
    progress: ProgressFn = None,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Critique a local MP4. Tries Gemini Files API first; falls back to Atlas frames."""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))

    wd = Path(work_dir) if work_dir else (path.parent / f"qc_{path.stem}")
    wd.mkdir(parents=True, exist_ok=True)

    duration_sec = 0.0
    try:
        import subprocess
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        duration_sec = float((probe.stdout or "0").strip() or 0)
    except Exception:
        duration_sec = 0.0

    data: dict[str, Any] | None = None
    if google_video_qc_enabled() and GEMINI_KEY:
        try:
            data = _critique_via_gemini_file(
                path,
                title=title,
                description=description,
                tags=tags,
                model=model,
                progress=progress,
            )
        except Exception as e:
            if progress:
                progress(f"Gemini file QC failed ({e}); falling back to Atlas frames…")

    if data is None:
        data = _critique_via_atlas_frames(
            path,
            title=title,
            description=description,
            tags=tags,
            work_dir=wd,
            progress=progress,
        )

    tr_path = wd / "transcript.txt"
    if tr_path.is_file():
        data.setdefault("_meta", {})["transcript_text"] = tr_path.read_text(encoding="utf-8")
    data.setdefault("_meta", {})["duration_sec"] = duration_sec

    if (script or "").strip():
        data = apply_harsh_qc_gates(data, script=script, duration_sec=duration_sec)

    if progress:
        progress(
            f"QC done — score {data.get('overall_score', '?')}/100, "
            f"{len(data.get('scenes') or [])} scenes"
            + (
                f", completeness={data.get('completeness', {}).get('severity')}"
                if data.get("completeness")
                else ""
            )
        )
    return data


def critique_to_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# Visual QC — {data.get('_meta', {}).get('title') or 'video'}",
        "",
        f"**Score:** {data.get('overall_score', '?')}/100",
        f"**Verdict:** {data.get('verdict', '')}",
        "",
        "## Primary failures",
    ]
    for p in data.get("primary_failures") or []:
        lines.append(f"- {p}")
    pacing = data.get("pacing") or {}
    if isinstance(pacing, dict):
        lines += [
            "",
            "## Pacing",
            f"- Estimated avg hold: {pacing.get('avg_shot_hold_sec_estimate')}s",
            f"- Target hold: {pacing.get('target_hold_sec_for_this_niche')}s",
            f"- Too static: {pacing.get('too_static')}",
            f"- Swap rate: {data.get('swap_rate_verdict')}",
            f"- Notes: {pacing.get('notes', '')}",
        ]
    else:
        lines += ["", "## Pacing", str(pacing), f"- Swap rate: {data.get('swap_rate_verdict')}"]
    lines += [
        "",
        f"**Demographic fit:** {data.get('demographic_fit', '')}",
        "",
        "## Story arc gaps",
    ]
    for g in data.get("story_arc_gaps") or []:
        lines.append(f"- {g}")
    lines += ["", "## Scenes"]
    for s in data.get("scenes") or []:
        lines += [
            "",
            f"### {s.get('start_sec')}s–{s.get('end_sec')}s — {s.get('severity')} / {s.get('relevance')}",
            f"- On screen: {s.get('what_is_on_screen')}",
            f"- Needs: {s.get('what_narration_likely_needs')}",
            f"- Fix: `{s.get('fix')}` → {s.get('better_visual')}",
        ]
        issues = s.get("issues") or []
        if isinstance(issues, str):
            issues = [issues]
        for iss in issues:
            lines.append(f"  - issue: {iss}")
    lines += ["", "## Must fix before ship"]
    for i, m in enumerate(data.get("must_fix_before_ship") or [], 1):
        lines.append(f"{i}. {m}")
    comp = data.get("completeness") or {}
    if comp:
        lines += [
            "",
            "## Completeness (script vs finished video)",
            f"- Severity: {comp.get('severity')}",
            f"- Coverage: {comp.get('coverage_ratio')} "
            f"({comp.get('actual_sec')}s actual / {comp.get('expected_sec')}s expected)",
            f"- Notes: {comp.get('notes')}",
        ]
    lines.append("")
    return "\n".join(lines)


def assert_script_completeness(
    *,
    script: str,
    video_duration_sec: float,
    transcript_text: str = "",
    wpm: float = 150.0,
) -> dict[str, Any]:
    """
    Harsh completeness gate: did the finished video cover the full script?

    Fail when audio/video is well under expected script duration, or when the
    transcript stops before the script's final paragraph.
    """
    words = len((script or "").split())
    expected_sec = (words / max(80.0, wpm)) * 60.0 if words else 0.0
    actual = float(video_duration_sec or 0)
    coverage = (actual / expected_sec) if expected_sec > 0 else 1.0

    script_tail = " ".join((script or "").split()[-24:]).lower()
    transcript_l = (transcript_text or "").lower()
    # Prefer a distinctive mid-tail phrase
    tail_probe = script_tail[:60] if script_tail else ""
    tail_present = bool(tail_probe) and tail_probe[:32] in transcript_l

    # Duration is the hard gate. Tail matching is advisory — Whisper/scene
    # transcripts often miss exact wording even when the full VO is present.
    if coverage < 0.55:
        severity = "critical"
        passed = False
    elif coverage < 0.85:
        severity = "major"
        passed = False
    elif words >= 200 and not tail_present and coverage < 0.95:
        severity = "major"
        passed = False
    else:
        severity = "ok" if (tail_present or coverage >= 0.95) else "minor"
        passed = True

    if passed:
        notes = (
            "PASS — duration aligns with script"
            + ("" if tail_present else " (ending phrase not confirmed in transcript)")
        )
    else:
        notes = (
            f"FAIL — video is {actual / 60:.1f} min but script implies "
            f"~{expected_sec / 60:.1f} min ({int(coverage * 100)}% coverage). "
            "Likely VO truncation (e.g. old Fish 4500-char silent cap)."
        )

    return {
        "passed": passed,
        "severity": severity,
        "script_words": words,
        "expected_sec": round(expected_sec, 1),
        "actual_sec": round(actual, 1),
        "coverage_ratio": round(coverage, 3),
        "script_tail_present_in_transcript": tail_present,
        "notes": notes,
    }


def apply_harsh_qc_gates(data: dict[str, Any], *, script: str = "", duration_sec: float = 0.0) -> dict[str, Any]:
    """Cap scores and inject completeness when the finished video is truncated."""
    transcript_bits = []
    for s in data.get("scenes") or []:
        if s.get("what_is_on_screen"):
            transcript_bits.append(str(s.get("what_narration_likely_needs") or ""))
    # Prefer real transcript file content if attached
    tr = (data.get("_meta") or {}).get("transcript_text") or ""
    completeness = assert_script_completeness(
        script=script,
        video_duration_sec=duration_sec or float((data.get("_meta") or {}).get("duration_sec") or 0),
        transcript_text=tr,
    )
    data["completeness"] = completeness
    if not completeness.get("passed"):
        try:
            score = float(data.get("overall_score") or 0)
        except Exception:
            score = 0
        # Truncated videos cannot score above 15 on a harsh scale
        data["overall_score"] = min(score, 15)
        fails = list(data.get("primary_failures") or [])
        note = completeness.get("notes") or "Script incomplete in finished video"
        if note not in fails:
            fails.insert(0, note)
        data["primary_failures"] = fails
        must = list(data.get("must_fix_before_ship") or [])
        fix = "Re-generate voiceover with full script (Fish voices now chunk; do not use truncated VO)"
        if fix not in must:
            must.insert(0, fix)
        data["must_fix_before_ship"] = must
        data["verdict"] = (
            f"CRITICAL: incomplete video ({completeness.get('coverage_ratio', 0):.0%} of script). "
            + str(data.get("verdict") or "")
        ).strip()
    return data
