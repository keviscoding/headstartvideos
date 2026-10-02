"""
Frontier Video Assembler - build final video with Ken Burns, captions, grade.

Assembles the motion timeline into a finished video with:
1. Ken Burns zooms on AI stills and Pexels photos (in/out/hold)
2. Pexels videos scaled to fill
3. ASS subtitle burn-in with kinetic word-level sync
4. Color grading matching Frontier style
5. Optional dust overlay and vignette
6. Audio mix
"""

from __future__ import annotations
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from core.frontier_motion_planner import MotionSegment


_FFMPEG_CANDIDATES = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
    "ffmpeg",
]
_FFPROBE_CANDIDATES = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
    "/opt/homebrew/bin/ffprobe",
    "ffprobe",
]


def _ffmpeg_bin() -> str:
    import shutil
    for c in _FFMPEG_CANDIDATES:
        if c in ("ffmpeg", "ffprobe"):
            continue
        if Path(c).exists():
            return c
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_bin() -> str:
    import shutil
    for c in _FFPROBE_CANDIDATES:
        if c in ("ffmpeg", "ffprobe"):
            continue
        if Path(c).exists():
            return c
    return shutil.which("ffprobe") or "ffprobe"


def _escape_ass_filter_path(path: Path) -> str:
    """Escape path for ffmpeg ass= filter (spaces/colons safe)."""
    s = str(path.resolve())
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    s = s.replace("[", "\\[")
    s = s.replace("]", "\\]")
    s = s.replace(",", "\\,")
    s = s.replace(";", "\\;")
    return s



def assemble_frontier_video(
    motion_segments: list[MotionSegment],
    voiceover_path: str | Path,
    subtitle_path: str | Path,
    output_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    color_grade: str = "",
    add_dust: bool = False,
    add_vignette: bool = False,
    dust_overlay_path: str | Path | None = None,
    zoom_amount: float = 0.28,
    word_timings: list[dict] | None = None,
    no_sub_ranges: list[tuple[float, float]] | None = None,
    no_dust_ranges: list[tuple[float, float]] | None = None,
    dust_strength: float = 1.0,
    progress_callback=None,
) -> dict:
    """
    Assemble final Frontier video from motion timeline.
    
    Args:
        motion_segments: List of MotionSegment objects in timeline order
        voiceover_path: Path to audio file
        subtitle_path: Path to .srt subtitle file
        output_path: Where to save final video
        width, height, fps: Video dimensions and framerate
        color_grade: ffmpeg color filter string (empty = no grade)
        add_dust: Apply dust overlay
        add_vignette: Apply dark vignette
        dust_overlay_path: Path to dust overlay video (if add_dust=True)
        zoom_amount: Ken Burns zoom factor (0.1-0.3)
        word_timings: Optional list of {word, start, end} dicts for kinetic captions
        no_sub_ranges: Mute burned captions over these [(start,end)] GFX windows
        no_dust_ranges: Suppress dust in these windows (usually same as GFX)
        dust_strength: 1.0 = baseline; >1 boosts dust brightness + film grain
        progress_callback: Optional callback(message: str)
    
    Returns:
        dict with keys:
            - output_path: str
            - duration_sec: float
            - segment_count: int
    """
    if progress_callback:
        progress_callback("Building video segments...")
    
    voiceover_path = Path(voiceover_path)
    subtitle_path = Path(subtitle_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Get audio duration
    audio_duration = _get_duration(voiceover_path)
    
    # Build individual segment videos
    temp_dir = Path(tempfile.mkdtemp(prefix="frontier_assemble_"))
    segment_paths = []
    
    for i, seg in enumerate(motion_segments):
        seg_path = temp_dir / f"seg_{i:04d}.mp4"
        
        _build_segment_video(
            segment=seg,
            output_path=seg_path,
            width=width,
            height=height,
            fps=fps,
            zoom_amount=zoom_amount,
            color_grade=color_grade,
        )
        
        segment_paths.append(seg_path)
        
        if progress_callback and (i + 1) % 5 == 0:
            progress_callback(f"Rendered {i + 1}/{len(motion_segments)} segments...")
    
    if progress_callback:
        progress_callback("Concatenating segments...")
    
    # Concatenate segments
    concat_path = temp_dir / "concat.mp4"
    _concatenate_segments(segment_paths, concat_path, fps)
    
    if progress_callback:
        progress_callback("Burning in subtitles...")
    
    # Mute captions (and dust) over full-screen motion_gfx — Whop no_sub_ranges
    gfx_ranges = [
        (float(s.start_sec), float(s.end_sec))
        for s in motion_segments
        if getattr(s, "type", "") == "motion_gfx"
    ]
    mute_subs = list(no_sub_ranges) if no_sub_ranges is not None else list(gfx_ranges)
    mute_dust = list(no_dust_ranges) if no_dust_ranges is not None else list(gfx_ranges)

    # Burn subtitles with ASS style (with word-level kinetic if available)
    subtitled_path = temp_dir / "subtitled.mp4"
    _burn_subtitles(
        video_path=concat_path,
        subtitle_path=subtitle_path,
        output_path=subtitled_path,
        word_timings=word_timings,
        no_sub_ranges=mute_subs,
    )
    
    # Apply dust overlay if requested
    if add_dust and dust_overlay_path and Path(dust_overlay_path).exists():
        if progress_callback:
            progress_callback("Adding dust overlay...")
        
        dust_path = temp_dir / "with_dust.mp4"
        _apply_dust_overlay(
            subtitled_path,
            dust_overlay_path,
            dust_path,
            no_dust_ranges=mute_dust,
            dust_strength=dust_strength,
        )
        subtitled_path = dust_path
    
    # Apply vignette if requested
    if add_vignette:
        if progress_callback:
            progress_callback("Adding vignette...")
        
        vignette_path = temp_dir / "with_vignette.mp4"
        _apply_vignette(subtitled_path, vignette_path)
        subtitled_path = vignette_path
    
    if progress_callback:
        progress_callback("Mixing audio...")
    
    # Mix with voiceover audio
    _mix_audio(
        video_path=subtitled_path,
        audio_path=voiceover_path,
        output_path=output_path,
    )
    
    # Cleanup temp files
    try:
        import shutil
        shutil.rmtree(temp_dir)
    except:
        pass
    
    return {
        "output_path": str(output_path),
        "duration_sec": audio_duration,
        "segment_count": len(motion_segments),
    }


def _build_segment_video(
    segment: MotionSegment,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    zoom_amount: float,
    color_grade: str,
):
    """Build a single segment video with Ken Burns zoom or static scale."""
    duration = segment.duration
    input_path = Path(segment.path)
    
    if not input_path.exists():
        raise FileNotFoundError(f"Segment asset not found: {input_path}")
    
    if segment.type == "motion_gfx":
        # Motion graphics card: pre-rendered MP4, just trim/normalize
        cmd = [
            _ffmpeg_bin(), "-y",
            "-i", str(input_path),
            "-t", str(duration),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "-r", str(fps),  # Normalize FPS
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-an",  # No audio
            str(output_path),
        ]
    
    elif segment.type == "pexels_video":
        # Video: scale to fill, loop if needed, normalize FPS
        vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
        
        if color_grade:
            vf += f",{color_grade}"
        
        # Loop video if segment is longer than clip
        video_dur = _get_duration(input_path)
        if video_dur < duration:
            # Need to loop
            loop_count = int(duration / video_dur) + 1
            cmd = [
                _ffmpeg_bin(), "-y",
                "-stream_loop", str(loop_count),
                "-i", str(input_path),
                "-t", str(duration),
                "-vf", vf,
                "-r", str(fps),  # Normalize FPS
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-an",  # No audio
                str(output_path),
            ]
        else:
            cmd = [
                _ffmpeg_bin(), "-y",
                "-ss", "0",
                "-i", str(input_path),
                "-t", str(duration),
                "-vf", vf,
                "-r", str(fps),  # Normalize FPS
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-an",
                str(output_path),
            ]
    
    else:
        # Still or photo: Ken Burns zoom
        zoom_filter = _ken_burns_filter(
            zoom_type=segment.zoom,
            duration=duration,
            fps=fps,
            width=width,
            height=height,
            zoom_amount=zoom_amount,
        )
        
        vf = zoom_filter
        if color_grade:
            vf += f",{color_grade}"
        
        cmd = [
            _ffmpeg_bin(), "-y",
            "-loop", "1",
            "-i", str(input_path),
            "-t", str(duration),
            "-vf", vf,
            "-r", str(fps),  # Normalize FPS
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_path),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"segment encode failed: {(result.stderr or '')[-1500:]}")


def _ken_burns_filter(
    zoom_type: Literal["in", "out", "hold"],
    duration: float,
    fps: int,
    width: int,
    height: int,
    zoom_amount: float = 0.28,
) -> str:
    """Generate ffmpeg zoompan Ken Burns (hold gets subtle push-in so nothing is static)."""
    # Remap hold -> subtle zoom-in (Frontier never fully freezes)
    if zoom_type == "hold":
        zoom_type = "in"
        zoom_amount = max(0.12, zoom_amount * 0.45)

    zoom_amount = max(float(zoom_amount), 0.12)
    total_frames = max(2, int(round(duration * fps)))

    if zoom_type == "out":
        start_zoom = 1.0 + zoom_amount
        end_zoom = 1.0
    else:
        start_zoom = 1.0
        end_zoom = 1.0 + zoom_amount

    zoom_expr = f"{start_zoom}+({end_zoom}-{start_zoom})*(on/{total_frames})"
    scale_w = int(width * 2.0)
    scale_h = int(height * 2.0)

    return (
        f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
        f"crop={scale_w}:{scale_h},"
        f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={total_frames}:s={width}x{height}:fps={fps},"
        f"setsar=1"
    )


def _concatenate_segments(segment_paths: list[Path], output_path: Path, fps: int):
    """Concatenate segment videos into one."""
    concat_file = output_path.parent / "concat_list.txt"
    
    with open(concat_file, "w") as f:
        for seg_path in segment_paths:
            f.write(f"file '{seg_path.absolute()}'\n")
    
    cmd = [
        _ffmpeg_bin(), "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(output_path),
    ]
    
    subprocess.run(cmd, check=True, capture_output=True)
    concat_file.unlink()


def _burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    word_timings: list[dict] | None = None,
    no_sub_ranges: list[tuple[float, float]] | None = None,
):
    """Burn ASS subtitles with kinetic style using libass filter.
    
    Args:
        video_path: Input video
        subtitle_path: .srt or .ass subtitle file
        output_path: Output video with burned subs
        word_timings: Optional word-level timings for kinetic
        no_sub_ranges: List of (start_sec, end_sec) ranges to mute captions
                       (e.g. during motion_gfx cards to prevent overlap)
    """
    import tempfile as _tempfile
    ff = _ffmpeg_bin()
    # Always materialize ASS into a space-free temp path for safe ass= filter
    tmp_ass = Path(_tempfile.mkdtemp(prefix="frontier_ass_")) / "captions.ass"

    if subtitle_path.suffix.lower() == ".ass" and not word_timings:
        raw = subtitle_path.read_text(encoding="utf-8")
        # Un-escape any wrongly escaped overrides from older runs
        tmp_ass.write_text(raw.replace("\\{", "{").replace("\\}", "}"), encoding="utf-8")
    else:
        srt = subtitle_path
        if srt.suffix.lower() != ".srt":
            sibling = srt.parent / "subtitles.srt"
            if sibling.exists():
                srt = sibling
        _convert_srt_to_ass(
            srt, tmp_ass, word_timings=word_timings, center=True,
            no_sub_ranges=no_sub_ranges,
        )

    # Punch holes in ASS over GFX windows when source was already .ass
    if no_sub_ranges and subtitle_path.suffix.lower() == ".ass" and not word_timings:
        _mute_ass_dialogue_ranges(tmp_ass, no_sub_ranges)

    # Safety: never leave escaped override braces
    body = tmp_ass.read_text(encoding="utf-8")
    if "\\{\\k" in body or "\\{k" in body:
        body = body.replace("\\{", "{").replace("\\}", "}")
        tmp_ass.write_text(body, encoding="utf-8")

    esc = _escape_ass_filter_path(tmp_ass)
    cmd = [
        ff, "-y",
        "-i", str(video_path),
        "-vf", f"ass={esc}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ass burn failed: {(result.stderr or '')[-1500:]}")


def _convert_srt_to_ass(
    srt_path: Path,
    ass_path: Path,
    accent_color: str = "&H96B8C9&",
    word_timings: list[dict] | None = None,
    center: bool = True,
    no_sub_ranges: list[tuple[float, float]] | None = None,
):
    """
    Convert SRT to ASS with Whop Jung kinetic captions.

    Word highlight uses \\1c + \\t transforms (white → warm gold → white), NOT bare \\k.
    Centered (Alignment=5) by default to match Jung; fade + heavy outline/shadow.
    ASS colors are &HBBGGRR&. Accent #C9B896 (warm gold) -> &H96B8C9&.
    
    Args:
        no_sub_ranges: List of (start_sec, end_sec) ranges to mute captions
                       (e.g. during motion_gfx cards to prevent caption/GFX overlap)
    """
    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    # Prefer Inter ExtraBold (Whop); fall back to Montserrat ExtraBold if missing
    font = "Inter ExtraBold"
    size = 78 if center else 73
    align = 5 if center else 2
    marginv = 0 if center else 96
    # Heavy outline+shadow like Whop (scaled for ~73-78px)
    k = size / 56.0
    outline = round(5.2 * k, 2)
    shadow = round(4.0 * k, 2)

    ass_header = f"""[Script Info]
Title: Frontier Subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00121212,&H64000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},{align},80,80,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    mute = list(no_sub_ranges or [])

    def _audible_fragments(st: float, en: float):
        """Pieces of [st, en] outside mute/GFX windows (Whop)."""
        ranges = [(float(st), float(en))]
        for ms, me in mute:
            nxt = []
            for a, b in ranges:
                if b <= ms or a >= me:
                    nxt.append((a, b))
                    continue
                if a < ms:
                    nxt.append((a, ms))
                if me < b:
                    nxt.append((me, b))
            ranges = nxt
        return [(a, b) for a, b in ranges if b - a >= 0.25]

    events = []
    dropped = 0
    blocks = [b.strip() for b in srt_content.strip().split("\n\n") if b.strip()]

    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        timing_line = lines[1]
        text_lines = lines[2:]
        if " --> " not in timing_line:
            continue
        start, end = timing_line.split(" --> ")
        st = _srt_time_to_seconds(start.strip())
        en = _srt_time_to_seconds(end.strip())
        plain = " ".join(text_lines).strip()
        frags = _audible_fragments(st, en) if mute else [(st, en)]
        if not frags:
            dropped += 1
            continue
        for fst, fen in frags:
            def _sec_to_srt(sec: float) -> str:
                h = int(sec // 3600)
                m = int((sec % 3600) // 60)
                s = int(sec % 60)
                ms = int(round((sec - int(sec)) * 1000))
                if ms >= 1000:
                    s += 1
                    ms -= 1000
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            start_s = _sec_to_srt(fst)
            end_s = _sec_to_srt(fen)
            start_ass = _srt_time_to_ass(start_s)
            end_ass = _srt_time_to_ass(end_s)
            if word_timings:
                body = _add_kinetic_color_flash(plain, start_s, end_s, word_timings, accent_color)
            else:
                body = _add_kinetic_color_flash_estimated(plain, start_s, end_s, accent_color)
            events.append(
                f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,"
                f"{{\\fad(170,170)}}{body}"
            )

    ass_path.write_text(ass_header + "\n".join(events) + "\n", encoding="utf-8")


def _word_flash_tag(word: str, on_ms: int, off_ms: int, accent: str, cue_ms: int) -> str:
    """One word: white → gold pop → white (Whop Jung \\1c+\\t grammar)."""
    on = max(0, int(on_ms))
    off = max(on + 40, int(off_ms))
    off = min(off, max(on + 40, int(cue_ms)))
    gold_end = min(off, on + 90)
    back_end = min(int(cue_ms), off + 90)
    w = word.replace("{", "(").replace("}", ")")
    # Build with concatenation so % / braces never confuse printf
    return (
        "{\\1c&HFFFFFF&"
        f"\\t({on},{gold_end},\\1c{accent}\\fscx108\\fscy108)"
        f"\\t({off},{back_end},\\1c&HFFFFFF&\\fscx100\\fscy100)"
        f"}}{w}"
    )


def _add_kinetic_color_flash(
    subtitle_text: str,
    sub_start: str,
    sub_end: str,
    word_timings: list[dict],
    accent_color: str,
) -> str:
    """Whop Jung: each spoken word flashes warm gold via \\1c + \\t, with a tiny pop."""
    sub_start_sec = _srt_time_to_seconds(sub_start)
    sub_end_sec = _srt_time_to_seconds(sub_end)
    cue_dur_ms = max(1, int((sub_end_sec - sub_start_sec) * 1000))

    subtitle_words = []
    for wt in word_timings:
        ws = wt["start"]
        if sub_start_sec - 0.05 <= ws < sub_end_sec:
            subtitle_words.append(wt)

    if not subtitle_words:
        return _add_kinetic_color_flash_estimated(subtitle_text, sub_start, sub_end, accent_color)

    chunks = []
    for wt in subtitle_words:
        word = wt["word"].strip()
        on = int((wt["start"] - sub_start_sec) * 1000)
        off = int((wt["end"] - sub_start_sec) * 1000)
        chunks.append(_word_flash_tag(word, on, off, accent_color, cue_dur_ms))
    return " ".join(chunks)


def _add_kinetic_color_flash_estimated(
    subtitle_text: str,
    sub_start: str,
    sub_end: str,
    accent_color: str,
) -> str:
    """Fallback: share cue duration by word length (Whop estimate)."""
    sub_start_sec = _srt_time_to_seconds(sub_start)
    sub_end_sec = _srt_time_to_seconds(sub_end)
    dur = max(0.2, sub_end_sec - sub_start_sec)
    cue_ms = int(dur * 1000)
    words = [w for w in subtitle_text.split() if w.strip()]
    if not words:
        return subtitle_text.replace("{", "(").replace("}", ")")
    weights = [max(2, len(w)) for w in words]
    tot = float(sum(weights)) or 1.0
    marks, acc = [], 0.0
    for wgt in weights:
        marks.append(dur * 0.92 * (acc / tot))
        acc += wgt
    marks.append(dur * 0.92)
    chunks = []
    for wi, w in enumerate(words):
        on = int(marks[wi] * 1000)
        off = int(marks[wi + 1] * 1000)
        chunks.append(_word_flash_tag(w, on, off, accent_color, cue_ms))
    return " ".join(chunks)


def _srt_time_to_seconds(srt_time: str) -> float:
    """Convert SRT timestamp to seconds."""
    if "," in srt_time:
        time_part, ms_part = srt_time.split(",")
        h, m, s = map(int, time_part.split(":"))
        ms = int(ms_part)
        return h * 3600 + m * 60 + s + ms / 1000.0
    # ASS-ish
    if "." in srt_time:
        time_part, frac = srt_time.split(".")
        h, m, s = map(int, time_part.split(":"))
        return h * 3600 + m * 60 + s + int(frac.ljust(3, "0")[:3]) / 1000.0
    h, m, s = map(int, srt_time.split(":"))
    return h * 3600 + m * 60 + s

def _srt_time_to_ass(srt_time: str) -> str:
    """Convert SRT timestamp to ASS format."""
    # SRT: 00:00:01,000
    # ASS: 0:00:01.00
    if "," in srt_time:
        time_part, ms_part = srt_time.split(",")
        h, m, s = time_part.split(":")
        # Drop last digit of milliseconds (ASS uses centiseconds)
        cs = ms_part[:2]
        return f"{int(h)}:{m}:{s}.{cs}"
    return srt_time



def _mute_ass_dialogue_ranges(ass_path: Path, mute_ranges: list[tuple[float, float]]) -> None:
    """Clip existing ASS Dialogue lines around mute windows (for pre-built .ass)."""
    if not mute_ranges:
        return
    body = ass_path.read_text(encoding="utf-8")
    out_lines = []
    for line in body.splitlines():
        if not line.startswith("Dialogue:"):
            out_lines.append(line)
            continue
        # Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        try:
            prefix, text = line.split(",,", 1)
            parts = prefix.split(",")
            start_ass, end_ass = parts[1], parts[2]
            st = _srt_time_to_seconds(start_ass.replace(".", ","))
            # ASS uses H:MM:SS.cs — handle via existing helper after normalizing
            st = _ass_time_to_seconds(start_ass)
            en = _ass_time_to_seconds(end_ass)
        except Exception:
            out_lines.append(line)
            continue
        ranges = [(st, en)]
        for ms, me in mute_ranges:
            nxt = []
            for a, b in ranges:
                if b <= ms or a >= me:
                    nxt.append((a, b)); continue
                if a < ms: nxt.append((a, ms))
                if me < b: nxt.append((me, b))
            ranges = nxt
        for a, b in ranges:
            if b - a < 0.25:
                continue
            # Rebuild Dialogue with new times, keep rest of prefix + text
            new_prefix = ",".join([parts[0], _seconds_to_ass(a), _seconds_to_ass(b)] + parts[3:])
            out_lines.append(f"{new_prefix},,{text}")
    ass_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def _ass_time_to_seconds(ass_time: str) -> float:
    """ASS H:MM:SS.cs → seconds."""
    ass_time = ass_time.strip()
    if "." in ass_time:
        time_part, frac = ass_time.split(".", 1)
        h, m, s = map(int, time_part.split(":"))
        cs = int(frac.ljust(2, "0")[:2])
        return h * 3600 + m * 60 + s + cs / 100.0
    h, m, s = map(int, ass_time.split(":"))
    return h * 3600 + m * 60 + s


def _seconds_to_ass(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        s += 1
        cs -= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def _apply_dust_overlay(
    video_path: Path,
    dust_path: Path,
    output_path: Path,
    no_dust_ranges: list[tuple[float, float]] | None = None,
    dust_strength: float = 1.0,
):
    """Apply dust overlay with RGB screen blend (Whop path — YUV screen tints magenta).

    dust_strength > 1.0 brightens the dust plate + raises film grain for Jung grit.
    no_dust_ranges: black-gate the dust during GFX windows (screen w/ black = noop).
    """
    dur = _get_duration(video_path)
    dust = Path(dust_path)
    alt = dust.with_name("overlay_dust_1080.mp4")
    if alt.exists():
        dust = alt
    strength = max(0.5, min(2.5, float(dust_strength)))
    # Brighten dust plate so screen-blend reads denser; grain scales with strength
    bright = 0.06 * (strength - 1.0)
    contrast = 1.0 + 0.18 * (strength - 1.0)
    grain = int(round(8 + 10 * (strength - 1.0)))  # 8 @1.0 → 18 @2.0
    grain = max(6, min(24, grain))
    gate = ""
    if no_dust_ranges:
        expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in no_dust_ranges)
        gate = (
            f",drawbox=x=0:y=0:w=1920:h=1080:color=black:t=fill:enable='{expr}'"
        )
    dust_chain = (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,fps=30,setsar=1,"
        f"eq=brightness={bright:.4f}:contrast={contrast:.4f},"
        f"format=gbrp{gate}[dust]"
    )
    fc = (
        f"[1:v]{dust_chain};"
        "[0:v]format=gbrp[base];"
        f"[base][dust]blend=all_mode=screen:shortest=1,format=yuv420p,"
        f"noise=alls={grain}:allf=t+u[v]"
    )
    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(dust),
        "-filter_complex", fc,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "copy",
        "-t", f"{dur:.4f}",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"dust overlay failed: {(result.stderr or '')[-1500:]}")


def _apply_vignette(video_path: Path, output_path: Path, strength: float = 0.55):
    """Darker edge vignette matching Whop (angle=PI/4). Avoid tiny angles (iris wipe)."""
    import math
    # Whop uses PI/4. strength 0..1 maps PI/3.2 (mild) → PI/4.5 (deeper)
    angle = (math.pi / 3.2) - strength * ((math.pi / 3.2) - (math.pi / 4.5))
    angle = max(math.pi / 4.8, min(math.pi / 3.0, angle))
    ff = _ffmpeg_bin()
    cmd = [
        ff, "-y",
        "-i", str(video_path),
        "-vf", f"vignette=angle={angle}:mode=forward:eval=init",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"vignette failed: {(result.stderr or '')[-1200:]}")


def _mix_audio(video_path: Path, audio_path: Path, output_path: Path):
    """Mix video with voiceover audio."""
    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]
    
    subprocess.run(cmd, check=True, capture_output=True)


def _get_duration(path: Path) -> float:
    """Get duration of audio/video file in seconds."""
    cmd = [
        _ffprobe_bin(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except:
        return 0.0

