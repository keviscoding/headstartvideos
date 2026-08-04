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
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-800:]
        raise RuntimeError(f"ffmpeg failed: {err}")


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
    """Multi-color title words (yellow / white / cyan rotation)."""
    words = (text or "").split()
    if not words:
        return ""
    colors = ["&H0015CCFA", "&H00FFFFFF", "&H00EED322"]  # yellow, white, cyan (BGR)
    hl = (highlight or "").strip().lower()
    out = []
    for i, w in enumerate(words):
        c = "&H0015CCFA" if hl and w.lower() == hl else colors[i % len(colors)]
        out.append(f"{{\\c{c}}}{_esc_ass(w)}")
    return " ".join(out)


def generate_ass(
    output_path: Path,
    clips: list[dict[str, Any]],
    durations: list[float],
    title: dict[str, Any] | None,
    *,
    style_preset: str = "viral",
    layout: dict[str, Any] | None = None,
) -> Path:
    """Write ASS overlay for viral (title bar + rank line) or classic (left stack)."""
    lo = layout or {}
    viral = (style_preset or "viral").lower() != "classic"
    font = _font_name()
    title_text = ((title or {}).get("text") or "").strip()
    highlight = ((title or {}).get("highlightWord") or "").strip()

    offsets = [0.0]
    for i in range(len(durations) - 1):
        offsets.append(offsets[-1] + durations[i])
    total = offsets[-1] + (durations[-1] if durations else 0)

    title_fs = int(lo.get("titleFontSize") or (52 if viral else 48))
    num_size = int(lo.get("numSize") or 50)
    list_x_pct = float(lo.get("listXPercent") or 5)
    title_y_pct = float(lo.get("titleYPercent") or 6)
    line_spacing = int(lo.get("lineSpacing") or 65)
    list_x = int((list_x_pct / 100) * RANK_W)
    title_y = 70 if viral else int((title_y_pct / 100) * RANK_H)
    bar_h = 210 if viral else 0
    rank_y = bar_h + 36 if viral else 0

    yellow = "&H0015CCFA"
    white = "&H00FFFFFF"
    dim = "&H00888888"
    done = "&H0000AACC"

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
        f"Style: Title,{font},{title_fs},{white},&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{'5' if viral else '4'},2,8,20,20,{title_y},1",
        f"Style: RankLine,{font},56,{white},&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,0,8,40,40,{rank_y},1",
        f"Style: RankLineYellow,{font},56,{yellow},&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,0,8,40,40,{rank_y},1",
        f"Style: NumDim,{font},{num_size},{dim},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1",
        f"Style: NumActive,{font},{num_size + 6},{yellow},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,4,2,7,0,0,0,1",
        f"Style: NumDone,{font},{num_size},{done},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1",
        f"Style: Label,{font},32,{white},&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2,1,7,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    t0 = ass_time(0)
    t_end = ass_time(total)

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
                tt = f"{before}{{\\c{yellow}}}{hl}{{\\c{white}}}{after}"
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
        label = _esc_ass(str(clip.get("label") or f"#{num}"))
        ts, te = ass_time(start), ass_time(end)

        if viral:
            # Centered "N. LABEL" under title bar for the active clip
            lines.append(
                f"Dialogue: 3,{ts},{te},RankLineYellow,,0,0,0,,"
                f"{{\\an8\\pos(540,{rank_y})}}{num}. {label}"
            )
        else:
            # Left number stack: dim before, active now, done after
            for j, other in enumerate(clips):
                on = numbers[j]
                oy = number_y.get(on, list_start_y)
                if j < i:
                    style = "NumDone"
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

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


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
    try:
        r = subprocess.run(
            [_ffmpeg_bin(), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=30,
        )
        return f" {name} " in f" {r.stdout} " or f" {name}\n" in r.stdout or f" {name}\t" in r.stdout
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
            f"drawtext=text='{title_text}':fontsize=48:fontcolor=yellow:"
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

    prog("Concatenating…")
    concat_list = work / "concat.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for p in norm_paths:
            # ffmpeg concat demuxer — escape single quotes
            esc = str(p).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")
    concat_mp4 = work / "concat.mp4"
    _run([
        _ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-c", "copy", str(concat_mp4),
    ], timeout=300)

    prog("Burning ranking overlay…")
    ass_path = work / "overlay.ass"
    generate_ass(
        ass_path, clips, durations, title,
        style_preset=style_preset, layout=layout,
    )
    subtitled = work / "subtitled.mp4"
    burned = False
    if _ffmpeg_has_filter("subtitles") or _ffmpeg_has_filter("ass"):
        burn_ass = "overlay.ass"
        filt = "subtitles" if _ffmpeg_has_filter("subtitles") else "ass"
        try:
            _run([
                _ffmpeg_bin(), "-y", "-i", str(concat_mp4.resolve()),
                "-vf", f"{filt}={burn_ass}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-r", str(RANK_FPS),
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(subtitled.resolve()),
            ], timeout=600, cwd=str(work))
            burned = True
        except RuntimeError as e:
            print(f"[ranking] {filt} burn failed, drawtext fallback: {e}")
    if not burned:
        try:
            _burn_drawtext_fallback(
                concat_mp4, subtitled, clips, durations, title,
                style_preset=style_preset,
            )
            burned = True
        except RuntimeError as e:
            print(f"[ranking] drawtext overlay unavailable ({e}); shipping concat without burn")
            shutil.copy2(concat_mp4, subtitled)

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
        work_dir=work / "build",
        output_path=out,
        progress=progress_callback,
    )
    return result
