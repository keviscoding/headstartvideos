"""
Ranking & Countdown short-form assembler (9:16).

Ports the ViewHunt RankingAssembler FFmpeg core: normalize → concat → ASS burn.
Runs on Fly cook Machines via cook_runner (recipe=ranking_countdown).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

RANK_W = 1080
RANK_H = 1920
RANK_FPS = 30

ProgressCb = Callable[[str], None]


def _ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"


def _run(cmd: list[str], *, timeout: int = 600, cwd: str | None = None) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg timed out after {timeout}s: {' '.join(cmd[:6])}…") from e
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-800:]
        raise RuntimeError(f"ffmpeg failed: {err}")


def _escape_ffmpeg_filter_path(path: Path | str) -> str:
    """Escape an absolute path for ffmpeg ass=/subtitles= filter args."""
    s = str(Path(path).resolve()).replace("\\", "/")
    # Windows drive letters; also harmless on Unix for any literal colons
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    s = s.replace(",", "\\,")
    s = s.replace("[", "\\[").replace("]", "\\]")
    return s


def _ass_fontsdir() -> str | None:
    """Prefer DejaVu on the cook image so fontconfig does not hang scanning."""
    for candidate in (
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/dejavu-core"),
        Path("/System/Library/Fonts/Supplemental"),
    ):
        if candidate.is_dir():
            return str(candidate)
    return None


def _burn_ass_overlay(concat_mp4: Path, ass_path: Path, subtitled: Path, *, work: Path) -> None:
    """Burn ASS via libass. Prefer `ass=` + absolute path + fontsdir (ViewHunt-style)."""
    escaped = _escape_ffmpeg_filter_path(ass_path)
    fontsdir = _ass_fontsdir()
    # Prefer ass= (libass direct). Fall back to subtitles= with same path.
    candidates: list[str] = []
    if _ffmpeg_has_filter("ass"):
        filt = f"ass={escaped}"
        if fontsdir:
            filt += f":fontsdir={_escape_ffmpeg_filter_path(fontsdir)}"
        candidates.append(filt)
    if _ffmpeg_has_filter("subtitles"):
        filt = f"subtitles={escaped}"
        if fontsdir:
            filt += f":fontsdir={_escape_ffmpeg_filter_path(fontsdir)}"
        candidates.append(filt)
    if not candidates:
        raise RuntimeError("ffmpeg has neither ass nor subtitles filter")

    last_err: Exception | None = None
    for filt in candidates:
        try:
            # Cap burn time — shared-cpu should finish a ~2min short in well under this.
            # On hang (fontconfig), fall through to drawtext instead of waiting 10 minutes.
            _run([
                _ffmpeg_bin(), "-y",
                "-i", str(concat_mp4.resolve()),
                "-vf", filt,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-r", str(RANK_FPS),
                "-c:a", "copy",
                "-movflags", "+faststart",
                "-threads", "2",
                str(subtitled.resolve()),
            ], timeout=180)
            if subtitled.is_file() and subtitled.stat().st_size > 1000:
                return
            last_err = RuntimeError("burn produced empty output")
        except Exception as e:
            last_err = e
            print(f"[ranking] ASS burn attempt failed ({filt[:48]}…): {e}")
            try:
                subtitled.unlink(missing_ok=True)
            except OSError:
                pass
    raise RuntimeError(str(last_err) if last_err else "ASS burn failed")


def probe_duration(path: str | Path) -> float:
    try:
        r = subprocess.run(
            [
                _ffprobe_bin(), "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
        return float((r.stdout or "").strip() or 0) or 0.0
    except Exception:
        return 0.0


def probe_video(path: str | Path) -> dict[str, float | int]:
    try:
        r = subprocess.run(
            [
                _ffprobe_bin(), "-v", "quiet", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration",
                "-show_entries", "format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
        info = json.loads(r.stdout or "{}")
        stream = (info.get("streams") or [{}])[0]
        fmt = info.get("format") or {}
        dur = float(stream.get("duration") or fmt.get("duration") or 0) or 0.0
        return {
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "duration": dur,
        }
    except Exception:
        return {"width": 0, "height": 0, "duration": 0.0}


def _font_name() -> str:
    """ASS Fontname that exists on cook image (DejaVu) or macOS (Arial)."""
    linux = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if linux.is_file():
        return "DejaVu Sans"
    mac = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
    if mac.is_file():
        return "Arial"
    return "Sans"


def ass_time(sec: float) -> str:
    s = max(0.0, float(sec))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    rem = s % 60
    return f"{h}:{m:02d}:{rem:05.2f}"


def _esc_ass(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def _format_viral_title(text: str, highlight: str | None = None) -> str:
    """
    Multi-color viral title (ViewHunt / OzyRanks): UPPERCASE, pink/white/yellow/cyan,
    soft wrap every 3 words, highlight word forced yellow.
    """
    words = [w for w in re.split(r"\s+", str(text or "").strip()) if w]
    if not words:
        return ""
    white = "&H00FFFFFF"
    pink = "&H00B672F4"
    yellow = "&H0015CCFA"
    cyan = "&H00EED322"
    hl = (highlight or "").strip().lower()
    n = len(words)
    parts: list[str] = []
    for i, w in enumerate(words):
        if hl and w.lower() == hl:
            color = yellow
        elif i == 0:
            color = white
        elif i == n - 1 and n > 2:
            color = cyan
        elif i < int((n * 0.4) + 0.999):  # ceil(n*0.4)
            color = pink
        else:
            color = yellow
        parts.append(f"{{\\c{color}}}{_esc_ass(w.upper())}")
        if (i + 1) % 3 == 0 and i < n - 1:
            parts.append("\\N")
        elif i < n - 1:
            parts.append(" ")
    return "".join(parts)


def _overlay_is_viral(style_preset: str) -> bool:
    """Map style presets to viral vs classic overlay (ViewHunt STYLE_PRESETS)."""
    sp = (style_preset or "viral").strip().lower()
    return sp not in ("classic", "minimal", "checkered")


def _effective_overlay_viral(
    style_preset: str,
    commentary_lines: list[dict[str, Any]] | None = None,
) -> bool:
    """Respect the user's style preset — commentary must not force Viral Shorts."""
    return _overlay_is_viral(style_preset)


_COLOR_MAP = {
    "yellow": {"active": "&H0015CCFA", "done": "&H0000AACC", "hl": "&H0015CCFA"},
    "cyan": {"active": "&H00EED322", "done": "&H00B59A0E", "hl": "&H00EED322"},
    "green": {"active": "&H0099D334", "done": "&H006E9A1A", "hl": "&H0099D334"},
    "red": {"active": "&H007171F8", "done": "&H004040C4", "hl": "&H007171F8"},
    "pink": {"active": "&H00B672F4", "done": "&H008A4AC4", "hl": "&H00B672F4"},
    "orange": {"active": "&H003C92FB", "done": "&H00206AC8", "hl": "&H003C92FB"},
    "white": {"active": "&H00FFFFFF", "done": "&H00CCCCCC", "hl": "&H00FFFFFF"},
}


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


def create_white_card(output_path: Path, duration_seconds: float) -> Path:
    """Solid white 1080x1920 card for viral mid-clip commentary beats."""
    dur = max(0.8, float(duration_seconds or 2))
    _run([
        _ffmpeg_bin(),
        "-f", "lavfi", "-i", f"color=c=white:s={RANK_W}x{RANK_H}:r={RANK_FPS}:d={dur:.3f}",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-r", str(RANK_FPS), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        str(output_path),
    ], timeout=120)
    return output_path


def _build_karaoke_dialogues(
    line: str,
    word_timings: list[dict[str, Any]] | None,
    base_offset: float,
    span_duration: float,
    *,
    on_white: bool = False,
) -> list[str]:
    """Per-word ASS dialogues (ViewHunt buildKaraokeASS) — not a single \\k line."""
    base = float(base_offset or 0)
    span = max(0.2, float(span_duration or 2))
    style_main = "ComSubWhite" if on_white else "ComSub"
    style_alt = "ComSubWhiteAlt" if on_white else "ComSub"
    pop = (
        "{\\an5\\pos(540,1040)\\t(0,70,\\fscx114\\fscy114)\\t(70,120,\\fscx100\\fscy100)}"
        if on_white
        else "{\\an5\\pos(540,1040)}"
    )
    timings = word_timings or _char_weighted_timings(line, span)
    out: list[str] = []
    if timings:
        for i, wt in enumerate(timings):
            w = str(wt.get("word") or "").strip()
            if not w:
                continue
            w_start = base + max(0.0, float(wt.get("start") or 0))
            w_end = base + max(
                w_start - base + 0.04,
                float(wt["end"]) if wt.get("end") is not None else float(wt.get("start") or 0) + 0.12,
            )
            if w_start > base + span:
                break
            for ni in range(i + 1, len(timings)):
                nw = str(timings[ni].get("word") or "").strip()
                if not nw:
                    continue
                next_start = base + max(0.0, float(timings[ni].get("start") or 0))
                w_end = min(w_end, next_start - 0.01)
                break
            w_end = min(w_end, base + span)
            if w_end <= w_start:
                w_end = w_start + 0.04
            style = style_alt if (on_white and i % 3 == 1) else style_main
            out.append(
                f"Dialogue: 4,{ass_time(w_start)},{ass_time(w_end)},{style},,0,0,0,,"
                f"{pop}{_esc_ass(w.upper())}"
            )
        return out

    words = [w for w in re.split(r"\s+", str(line or "").strip()) if w]
    if not words:
        return out
    weights = [max(1, len(re.sub(r"[^a-zA-Z0-9']", "", w)) or 1) for w in words]
    total = sum(weights) or 1
    t = 0.0
    for wi, w in enumerate(words):
        slice_d = (weights[wi] / total) * span
        ws, we = base + t, base + t + slice_d
        st = style_alt if (on_white and wi % 3 == 1) else style_main
        out.append(
            f"Dialogue: 4,{ass_time(ws)},{ass_time(we)},{st},,0,0,0,,"
            f"{pop}{_esc_ass(w.upper())}"
        )
        t += slice_d
    return out


def generate_ass(
    output_path: Path,
    clips: list[dict[str, Any]],
    durations: list[float],
    title: dict[str, Any] | None,
    *,
    style_preset: str = "viral",
    layout: dict[str, Any] | None = None,
    color_palette: str = "yellow",
    checkered_mode: bool = False,
    commentary_lines: list[dict[str, Any]] | None = None,
    subtitle_font: str | None = None,
    subtitle_y: float | None = None,
    subtitle_color: str = "yellow",
    timeline: dict[str, Any] | None = None,
    force_viral: bool | None = None,
) -> Path:
    """Write ASS overlay for viral/classic ranking + optional commentary karaoke."""
    lo = layout or {}
    tl = timeline or {}
    viral = bool(force_viral) if force_viral is not None else _overlay_is_viral(style_preset)
    font = _font_name()
    title_text = ((title or {}).get("text") or "").strip()
    highlight = ((title or {}).get("highlightWord") or "").strip()
    colors = _COLOR_MAP.get((color_palette or "yellow").lower(), _COLOR_MAP["yellow"])
    white_ass = "&H00FFFFFF"
    sub_colors = {k: v["active"] for k, v in _COLOR_MAP.items()}
    subtitle_ass = sub_colors.get((subtitle_color or "yellow").lower(), sub_colors["yellow"])

    clip_offsets_tl = tl.get("clipOffsets")
    voice_offsets_tl = tl.get("voiceOffsets") or {}
    white_meta = tl.get("whiteMeta") or {}

    if isinstance(clip_offsets_tl, list) and len(clip_offsets_tl) == len(durations):
        offsets = [float(x) for x in clip_offsets_tl]
    else:
        offsets = [0.0]
        for i in range(len(durations) - 1):
            offsets.append(offsets[-1] + durations[i])
    total = offsets[-1] + (durations[-1] if durations else 0)
    for _k, w in white_meta.items():
        if isinstance(w, dict):
            end_w = float(w.get("offset") or 0) + float(w.get("duration") or 0)
            if end_w > total:
                total = end_w

    title_fs = int(lo.get("titleFontSize") or (52 if viral else 48))
    if viral:
        title_fs = max(title_fs, 52)
    num_size = int(lo.get("numSize") or 50)
    list_x_pct = float(lo.get("listXPercent") or 5)
    title_y_pct = float(lo.get("titleYPercent") or 6)
    line_spacing = int(lo.get("lineSpacing") or 65)
    list_x = int((list_x_pct / 100) * RANK_W)
    title_y = 70 if viral else int((title_y_pct / 100) * RANK_H)
    bar_h = 210 if viral else 0
    rank_y = bar_h + 36 if viral else 0
    sub_font = subtitle_font or font
    sub_size = 68 if viral else 52
    sub_outline = 6 if viral else 3

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {RANK_W}",
        f"PlayResY: {RANK_H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: TitleBar,{font},20,&H00000000,&H000000FF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Title,{font},{title_fs},{white_ass},&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{'5' if viral else '4'},2,8,20,20,{title_y},1",
        f"Style: RankLine,{font},56,{white_ass},&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,0,8,40,40,{rank_y},1",
        f"Style: RankLineYellow,{font},56,{colors['active']},&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,0,8,40,40,{rank_y},1",
        f"Style: NumDim,{font},{num_size},&H00888888,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1",
        f"Style: NumActive,{font},{num_size + 6},{colors['active']},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,4,2,7,0,0,0,1",
        f"Style: NumDone,{font},{num_size},{colors['done']},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1",
        f"Style: NumDoneAlt,{font},{num_size},{white_ass},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1",
        f"Style: Label,{font},32,{white_ass},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2,1,7,0,0,0,1",
        f"Style: ComSub,{sub_font},{sub_size},{subtitle_ass},&H000000FF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{sub_outline},2,5,40,40,0,1",
        f"Style: ComSubWhite,{sub_font},92,{subtitle_ass},&H000000FF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,8,3,5,40,40,0,1",
        f"Style: ComSubWhiteAlt,{sub_font},92,&H00EED322,&H000000FF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,8,3,5,40,40,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    t0 = ass_time(0)
    t_end = ass_time(total)
    hl_color = colors["hl"]

    if viral and bar_h > 0:
        lines.append(
            f"Dialogue: 0,{t0},{t_end},TitleBar,,0,0,0,,"
            f"{{\\p1\\bord0\\shad0\\1c&H000000&\\1a&H00&}}"
            f"m 0 0 l {RANK_W} 0 {RANK_W} {bar_h} 0 {bar_h}{{\\p0}}"
        )

    if title_text:
        if viral:
            tt = _format_viral_title(title_text, highlight)
            lines.append(
                f"Dialogue: 2,{t0},{t_end},Title,,0,0,0,,"
                f"{{\\an8\\pos(540,{title_y})\\b1}}{tt}"
            )
        else:
            tt = _esc_ass(title_text)
            if highlight and highlight.lower() in title_text.lower():
                idx = title_text.lower().index(highlight.lower())
                before = _esc_ass(title_text[:idx])
                hl = _esc_ass(title_text[idx: idx + len(highlight)])
                after = _esc_ass(title_text[idx + len(highlight):])
                tt = f"{before}{{\\c{hl_color}}}{hl}{{\\c{white_ass}}}{after}"
            lines.append(f"Dialogue: 2,{t0},{t_end},Title,,0,0,0,,{tt}")

    numbers = [int(c.get("number") or (len(clips) - i)) for i, c in enumerate(clips)]
    sorted_nums = sorted(numbers)
    list_h = len(sorted_nums) * line_spacing
    list_start_y = max(400, int(960 - list_h / 2))
    number_y = {n: list_start_y + i * line_spacing for i, n in enumerate(sorted_nums)}
    label_x = list_x + 80

    for i, clip in enumerate(clips):
        start = offsets[i]
        end = start + durations[i]
        num = numbers[i]
        raw_label = str(clip.get("label") or f"#{num}").strip()
        label = _esc_ass(raw_label.upper() if viral else raw_label)
        ts, te = ass_time(start), ass_time(end)

        if viral:
            # #1 (last clip) yellow; others white — ViewHunt pattern
            style = "RankLineYellow" if i == len(clips) - 1 else "RankLine"
            lines.append(
                f"Dialogue: 3,{ts},{te},{style},,0,0,0,,"
                f"{{\\an8\\pos(540,{rank_y})\\fad(120,80)}}{num}. {label}"
            )
        else:
            for j, _other in enumerate(clips):
                on = numbers[j]
                oy = number_y.get(on, list_start_y)
                if j < i:
                    style = "NumDoneAlt" if (checkered_mode and j % 2 == 1) else "NumDone"
                elif j == i:
                    style = "NumActive"
                else:
                    style = "NumDim"
                lines.append(
                    f"Dialogue: 1,{ts},{te},{style},,0,0,0,,"
                    f"{{\\pos({list_x},{oy})}}{on}"
                )
            lines.append(
                f"Dialogue: 2,{ts},{te},Label,,0,0,0,,"
                f"{{\\pos({label_x},{number_y.get(num, list_start_y)})}}{label}"
            )

    for c_line in commentary_lines or []:
        idx = int(c_line.get("clipIndex") or 0)
        if idx < 0 or idx >= len(offsets):
            continue
        line_text = (c_line.get("line") or "").strip()
        if not line_text:
            continue
        audio_path = c_line.get("audioPath")
        if not audio_path or not Path(str(audio_path)).is_file():
            continue
        on_white = idx in white_meta or str(idx) in white_meta
        wm = white_meta.get(idx) or white_meta.get(str(idx)) or {}
        if idx in voice_offsets_tl:
            c_start = float(voice_offsets_tl[idx])
        elif str(idx) in voice_offsets_tl:
            c_start = float(voice_offsets_tl[str(idx)])
        else:
            c_start = offsets[idx]
        tts_dur = float(c_line.get("duration") or 0) or 0.0
        if tts_dur <= 0:
            tts_dur = probe_duration(audio_path) or 2.2
        if on_white:
            c_dur = float(wm.get("duration") or 0) or max(1.1, min(4.5, tts_dur + 0.12))
        else:
            c_dur = max(0.8, min(float(durations[idx]), tts_dur + 0.15 if tts_dur else 2.5))
        lines.extend(
            _build_karaoke_dialogues(
                line_text,
                c_line.get("wordTimings"),
                c_start,
                c_dur,
                on_white=on_white,
            )
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def mix_commentary_audio(
    video_path: Path,
    commentary_lines: list[dict[str, Any]],
    durations: list[float],
    output_path: Path,
    work_dir: Path,
    *,
    voice_offsets: dict[Any, float] | None = None,
) -> None:
    """Duck bed audio and mix commentary VO at voiceOffsets (white card or clip start)."""
    usable = []
    for c in commentary_lines or []:
        ap = Path(str(c.get("audioPath") or ""))
        if ap.is_file() and (c.get("line") or "").strip():
            usable.append(c)
    if not usable:
        shutil.copy2(video_path, output_path)
        return

    offsets = [0.0]
    for i in range(len(durations) - 1):
        offsets.append(offsets[-1] + durations[i])
    vo_map = voice_offsets or {}

    cmd = [_ffmpeg_bin(), "-y", "-i", str(video_path)]
    for c in usable:
        cmd.extend(["-i", str(c["audioPath"])])

    filters = []
    labels = []
    for i, c in enumerate(usable):
        idx = int(c.get("clipIndex") or 0)
        if idx in vo_map:
            start_sec = float(vo_map[idx])
        elif str(idx) in vo_map:
            start_sec = float(vo_map[str(idx)])
        else:
            start_sec = offsets[idx] if idx < len(offsets) else 0.0
        delay_ms = int(max(0.0, start_sec) * 1000)
        filters.append(
            f"[{i + 1}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"volume=2.4,alimiter=limit=0.97:level=false,"
            f"adelay={delay_ms}|{delay_ms}[c{i}]"
        )
        labels.append(f"[c{i}]")
    filters.append(
        "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=0.7[bed]"
    )
    if len(labels) == 1:
        filters.append(f"{labels[0]}anull[cmix]")
    else:
        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0[cmix]"
        )
    filters.append(
        "[bed][cmix]sidechaincompress=threshold=0.012:ratio=18:attack=12:release=220:makeup=1[ducked]"
    )
    filters.append("[ducked]volume=0.75[ducked2]")
    filters.append(
        "[ducked2][cmix]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    cmd.extend([
        "-filter_complex", ";".join(filters),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ])
    try:
        _run(cmd, timeout=600, cwd=str(work_dir))
    except RuntimeError as e:
        print(f"[ranking] commentary mix failed ({e}); shipping without VO mix")
        shutil.copy2(video_path, output_path)


def normalize_clip(src: Path, dst: Path, *, start: float = 0.0, end: float | None = None) -> float:
    """Scale/crop to 1080x1920, 30fps, H.264 + AAC. Returns duration."""
    info = probe_video(src)
    src_dur = float(info["duration"] or 0)
    ss = max(0.0, float(start or 0))
    if end is not None and float(end) > ss:
        dur = max(0.3, float(end) - ss)
    else:
        dur = max(0.3, src_dur - ss) if src_dur > 0 else 5.0

    vf = (
        f"scale={RANK_W}:{RANK_H}:force_original_aspect_ratio=increase,"
        f"crop={RANK_W}:{RANK_H},fps={RANK_FPS},"
        f"setsar=1"
    )
    cmd = [
        _ffmpeg_bin(), "-y",
        "-ss", f"{ss:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100",
        "-r", str(RANK_FPS),
        "-movflags", "+faststart",
        str(dst),
    ]
    _run(cmd, timeout=300)
    return probe_duration(dst) or dur


def _ffmpeg_has_filter(name: str) -> bool:
    """True if ffmpeg lists an exact filter name (avoid substring hits like 'ass' in 'allpass')."""
    try:
        r = subprocess.run(
            [_ffmpeg_bin(), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=30,
        )
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            # Typical: ".S ass  V->V  Render ASS subtitles…"
            if len(parts) >= 2 and parts[1] == name:
                return True
        return False
    except Exception:
        return False


def _burn_drawtext_fallback(
    concat_mp4: Path,
    subtitled: Path,
    clips: list[dict[str, Any]],
    durations: list[float],
    title: dict[str, Any] | None,
    *,
    style_preset: str,
) -> None:
    """Overlay title + active rank line with drawtext when libass is unavailable."""
    title_text = ((title or {}).get("text") or "Ranking").replace(":", "\\:").replace("'", "")
    offsets = [0.0]
    for i in range(len(durations) - 1):
        offsets.append(offsets[-1] + durations[i])
    # Stack enable expressions for each clip's label
    parts = [
        f"drawbox=x=0:y=0:w={RANK_W}:h=180:color=black@1:t=fill",
        (
            f"drawtext=text='{title_text.upper()}':fontsize=48:fontcolor=yellow:"
            f"x=(w-text_w)/2:y=40:borderw=3:bordercolor=black"
        ),
    ]
    for i, clip in enumerate(clips):
        start = offsets[i]
        end = start + durations[i]
        num = int(clip.get("number") or (len(clips) - i))
        label = str(clip.get("label") or f"#{num}").replace(":", "\\:").replace("'", "")
        text = f"{num}. {label}"
        parts.append(
            f"drawtext=text='{text}':fontsize=56:fontcolor=yellow:"
            f"x=(w-text_w)/2:y=220:borderw=4:bordercolor=black:"
            f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
        )
    vf = ",".join(parts)
    _run([
        _ffmpeg_bin(), "-y", "-i", str(concat_mp4),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-r", str(RANK_FPS),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(subtitled),
    ], timeout=600)


def assemble_ranking_video(
    clips: list[dict[str, Any]],
    *,
    title: dict[str, Any] | None = None,
    style_preset: str = "viral",
    layout: dict[str, Any] | None = None,
    color_palette: str = "yellow",
    checkered_mode: bool = False,
    commentary_lines: list[dict[str, Any]] | None = None,
    subtitle_font: str | None = None,
    subtitle_y: float | None = None,
    subtitle_color: str = "yellow",
    work_dir: str | Path,
    output_path: str | Path,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """
    Assemble ranking short.

    clips: [{ path, number, label, startTime?, endTime? }] in playback order
           (highest number first, #1 last).
    """
    def prog(msg: str) -> None:
        if progress:
            progress(msg)
        print(f"[ranking] {msg}")

    if not clips:
        raise ValueError("Need at least one clip to assemble")

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    prog(f"Normalizing {len(clips)} clip(s)…")
    norm_paths: list[Path] = []
    durations: list[float] = []
    for i, clip in enumerate(clips):
        src = Path(str(clip.get("path") or ""))
        if not src.is_file():
            raise FileNotFoundError(f"Clip missing: {src}")
        dst = work / f"norm-{i:02d}.mp4"
        dur = normalize_clip(
            src, dst,
            start=float(clip.get("startTime") or 0),
            end=clip.get("endTime"),
        )
        norm_paths.append(dst)
        durations.append(dur)
        prog(f"Clip {i + 1}/{len(clips)} ready ({dur:.1f}s)")

    # Attach TTS durations for karaoke / mix / white cards
    lines_out = list(commentary_lines or [])
    for c in lines_out:
        ap = c.get("audioPath")
        if ap and not c.get("duration"):
            c["duration"] = probe_duration(ap)

    viral = _effective_overlay_viral(style_preset, lines_out)
    use_white = viral and bool(lines_out)

    clip_offsets: list[float] = []
    voice_offsets: dict[int, float] = {}
    white_meta: dict[int, dict[str, float]] = {}
    concat_entries: list[Path] = []

    cursor = 0.0
    n = len(norm_paths)
    for i, np in enumerate(norm_paths):
        is_mid = i > 0 and i < n - 1
        mid_line = next((c for c in lines_out if int(c.get("clipIndex") or -1) == i), None)
        has_mid_vo = bool(
            use_white
            and is_mid
            and mid_line
            and (mid_line.get("line") or "").strip()
            and Path(str(mid_line.get("audioPath") or "")).is_file()
        )
        if has_mid_vo:
            tts = float(mid_line.get("duration") or 0) or 2.0
            w_dur = max(1.1, min(4.5, tts + 0.12))
            white_path = work / f"white-{i:02d}.mp4"
            prog(f"White card before clip {i + 1}…")
            create_white_card(white_path, w_dur)
            concat_entries.append(white_path)
            voice_offsets[i] = cursor
            white_meta[i] = {"offset": cursor, "duration": w_dur}
            cursor += w_dur
        concat_entries.append(np)
        clip_offsets.append(cursor)
        if not has_mid_vo:
            voice_offsets[i] = cursor
        cursor += durations[i]

    prog("Concatenating…")
    concat_list = work / "concat.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for p in concat_entries:
            esc = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")
    concat_mp4 = work / "concat.mp4"
    _run([
        _ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-c", "copy", str(concat_mp4),
    ], timeout=300)

    timeline = {
        "clipOffsets": clip_offsets,
        "voiceOffsets": voice_offsets,
        "whiteMeta": white_meta,
    }

    prog("Burning ranking overlay…")
    ass_path = work / "overlay.ass"
    generate_ass(
        ass_path, clips, durations, title,
        style_preset=style_preset,
        layout=layout,
        color_palette=color_palette,
        checkered_mode=checkered_mode,
        commentary_lines=lines_out,
        subtitle_font=subtitle_font,
        subtitle_y=subtitle_y,
        subtitle_color=subtitle_color,
        timeline=timeline,
        force_viral=viral,
    )
    subtitled = work / "subtitled.mp4"
    burned = False
    try:
        _burn_ass_overlay(concat_mp4, ass_path, subtitled, work=work)
        burned = True
    except Exception as e:
        print(f"[ranking] ASS burn failed, drawtext fallback: {e}")
    if not burned:
        try:
            _burn_drawtext_fallback(
                concat_mp4, subtitled, clips, durations, title,
                style_preset="viral" if viral else style_preset,
            )
            burned = True
        except Exception as e:
            print(f"[ranking] drawtext overlay unavailable ({e}); shipping concat without burn")
            shutil.copy2(concat_mp4, subtitled)

    if lines_out and any(Path(str(c.get("audioPath") or "")).is_file() for c in lines_out):
        prog("Mixing commentary voiceover…")
        mixed = work / "mixed.mp4"
        mix_commentary_audio(
            subtitled, lines_out, durations, mixed, work,
            voice_offsets=voice_offsets,
        )
        shutil.copy2(mixed, out)
    else:
        shutil.copy2(subtitled, out)

    final_dur = probe_duration(out)
    prog(f"Ranking video ready ({final_dur:.1f}s)")
    return {
        "output_path": str(out),
        "duration": final_dur,
        "clip_count": len(clips),
        "width": RANK_W,
        "height": RANK_H,
    }


def run_ranking_pipeline(
    *,
    clips: list[dict[str, Any]],
    title: dict[str, Any] | None = None,
    style_preset: str = "viral",
    layout: dict[str, Any] | None = None,
    color_palette: str = "yellow",
    checkered_mode: bool = False,
    commentary_lines: list[dict[str, Any]] | None = None,
    subtitle_font: str | None = None,
    subtitle_y: float | None = None,
    subtitle_color: str = "yellow",
    output_name: str = "ranking_video.mp4",
    work_dir: str | Path | None = None,
    progress_callback: ProgressCb | None = None,
) -> dict[str, Any]:
    """Entry used by cook_runner."""
    root = Path(__file__).resolve().parent.parent
    stamp = int(time.time())
    work = Path(work_dir) if work_dir else (root / "output" / "ranking" / f"job_{stamp}")
    out = work / output_name
    result = assemble_ranking_video(
        clips,
        title=title,
        style_preset=style_preset,
        layout=layout,
        color_palette=color_palette,
        checkered_mode=checkered_mode,
        commentary_lines=commentary_lines,
        subtitle_font=subtitle_font,
        subtitle_y=subtitle_y,
        subtitle_color=subtitle_color,
        work_dir=work / "build",
        output_path=out,
        progress=progress_callback,
    )
    return result
