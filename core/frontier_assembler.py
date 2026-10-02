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
    vignette_strength: float = 0.55,
    flash_times: list[float] | None = None,
    leak_strength: float = 0.0,  # Kevis: no bright lightleak disk (0=off/soft)
    use_lightleak: bool = False,  # True only if style explicitly wants Whop leak
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
        vignette_strength: 0..1 edge crush (Whop ~0.7)
        flash_times: Optional soft chapter commas (default: ≤2-frame @15%; no lightleak disk)
        use_lightleak: Opt-in Whop circular leak (Kevis: "annoying" — DEFAULT: False, prefer soft dissolve)
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

    # White-haze chapter commas (Whop flash dissolves — pic changes w/o rushing cuts)
    # Kevis: "light-leak chapter flash is annoying" — DEFAULT: OFF (prefer soft dissolve)
    # Pass flash_times=[] explicitly to enable (not recommended for Jung/Frontier)
    if flash_times:
        if progress_callback:
            progress_callback(f"Applying {len(flash_times)} chapter flashes...")
        flashed = temp_dir / "flashed.mp4"
        _apply_white_flashes(concat_path, list(flash_times), flashed, leak_strength=leak_strength, use_lightleak=use_lightleak)
        concat_path = flashed
    
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
        _apply_vignette(subtitled_path, vignette_path, strength=vignette_strength)
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
            srt, tmp_ass, word_timings=word_timings, center=False,
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



def _chunk_caption_words(text: str, max_words: int = 6) -> list[str]:
    """Whop beds: 2–6 words visible — split long cues for caption-step density."""
    words = [w for w in text.split() if w.strip()]
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def _convert_srt_to_ass(
    srt_path: Path,
    ass_path: Path,
    accent_color: str = "&H6ED7F5&",  # Whop #F5D76E yellow
    word_timings: list[dict] | None = None,
    center: bool = False,
    no_sub_ranges: list[tuple[float, float]] | None = None,
    first_caption_max_sec: float = 2.0,
):
    """
    Convert SRT to ASS with kinetic karaoke captions (accent from style; default Whop yellow).

    Word highlight uses \1c + \t transforms (white → warm gold → white), NOT bare \k.
    Bottom-centre (Alignment=2) on beds per Dissect formula; fade + heavy outline/shadow.
    Pass center=True only for rare mid-frame experiments — not default.
    ASS colors are &HBBGGRR&. Accent Whop yellow #F5D76E -> &H6ED7F5&.

    Args:
        no_sub_ranges: Mute/clip cues over motion_gfx windows (Whop never-overlay).
        first_caption_max_sec: Formula "first caption ≤2.0s" — if the first cue starts
            AFTER this, pull it forward so the hook gets text by 2.0s. Early cues (0.0)
            are left alone (already compliant).
    """
    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    font = "Inter ExtraBold"
    size = 78 if center else 73
    align = 5 if center else 2
    marginv = 0 if center else 96
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

    def _sec_to_srt(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        if ms >= 1000:
            s += 1
            ms -= 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    events = []
    dropped = 0
    blocks = [b.strip() for b in srt_content.strip().split("\n\n") if b.strip()]
    cue_idx = 0

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
        if not plain:
            continue

        # Formula: first caption must appear by ≤2.0s (pull late cues forward only)
        if cue_idx == 0 and st > first_caption_max_sec:
            shift = st - first_caption_max_sec
            st = first_caption_max_sec
            en = max(st + 0.35, en - shift)
        cue_idx += 1

        # Caption-step density: ≤6 words per on-screen line (Whop beds)
        pieces = _chunk_caption_words(plain, max_words=6)
        if not pieces:
            continue
        span = max(0.35, (en - st) / len(pieces))
        for pi, piece in enumerate(pieces):
            pst = st + pi * span
            pen = min(en, pst + span)
            frags = _audible_fragments(pst, pen) if mute else [(pst, pen)]
            if not frags:
                dropped += 1
                continue
            for fst, fen in frags:
                start_s = _sec_to_srt(fst)
                end_s = _sec_to_srt(fen)
                start_ass = _srt_time_to_ass(start_s)
                end_ass = _srt_time_to_ass(end_s)
                if word_timings:
                    body = _add_kinetic_color_flash(piece, start_s, end_s, word_timings, accent_color)
                else:
                    body = _add_kinetic_color_flash_estimated(piece, start_s, end_s, accent_color)
                fad = "{" + "\\" + "fad(120,120)}"
                events.append(
                    f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,"
                    f"{fad}{body}"
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


def _seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


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


def _apply_white_flashes(
    video_path: Path,
    flash_times: list[float],
    output_path: Path,
    flash_dur: float = 0.95,
    peak_alpha: float = 0.85,
    lightleak_path: str | Path | None = None,
    leak_strength: float = 0.0,
    use_lightleak: bool = False,
):
    """Chapter commas between beds.

<<<<<<< HEAD
    Kevis HARD (2026-10-02): bright circular lightleak disk is annoying — default OFF.
    Soft path: ≤2 frames (~1/15–1/30s) white dissolve at ≤15% brightness.
    Legacy Whop lightleak only when use_lightleak=True and leak_strength>0.
    
    Leak plate is radially vignetted so the burn reads as a disk/coma, not a flat full-frame brightness wipe.
    Falls back to brightness pulse only if lightleak.mp4 is missing (legacy).
    """
    if not flash_times:
        import shutil
        shutil.copy2(video_path, output_path)
        return

    dur = _get_duration(video_path)

    # --- Soft dissolve (default): Kevis-approved ---
    if not use_lightleak or float(leak_strength) <= 0.0:
        # ~2 frames at 30fps → 1/15s half-window each side of t0
        half = 1.0 / 30.0
        peak = min(0.15, max(0.0, float(peak_alpha) if peak_alpha <= 0.15 else 0.15))
        parts = []
        for t0 in flash_times:
            a = max(0.0, float(t0) - half)
            b = min(dur, float(t0) + half)
            parts.append("between(t\\,%.4f\\,%.4f)" % (a, b))
        enables = "+".join(parts)
        vf = (
            "eq=brightness='if(%s\\,%.3f\\,0)':eval=frame"
            % (enables, peak)
        )
        cmd = [
            _ffmpeg_bin(), "-y", "-i", str(video_path), "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "copy", "-t", f"{dur:.4f}", str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"soft chapter dissolve failed: {(result.stderr or '')[-1200:]}")
        return

    # --- Legacy lightleak path (opt-in only) ---
    import tempfile as _tf
    import json as _json
    candidates = []
    if lightleak_path:
        candidates.append(Path(lightleak_path))
    root = Path(__file__).resolve().parent.parent
    candidates += [
        root / "assets" / "lightleak.mp4",
        root / "frontier-gfx-kit" / "textures" / "lightleak.mp4",
        root / "assets" / "whop-gfx" / "textures" / "lightleak.mp4",
    ]
    leak = next((p for p in candidates if p.exists()), None)
    if leak is None:
        # fall through to soft
        return _apply_white_flashes(
            video_path, flash_times, output_path,
            leak_strength=0.0, use_lightleak=False, peak_alpha=0.15,
        )

    tmp = Path(_tf.mkdtemp(prefix="frontier_leak_"))
    plate = tmp / "leak_plate.mp4"
    ff = _ffmpeg_bin()
    fp = str(Path(ff).with_name("ffprobe")) if "/" in ff else "ffprobe"
    if not Path(fp).exists():
        fp = "ffprobe"
    probe = subprocess.run(
        [fp, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video_path)],
        capture_output=True, text=True,
    )
    try:
        st = (_json.loads(probe.stdout or "{}").get("streams") or [{}])[0]
        vw = int(st.get("width") or 1920)
        vh = int(st.get("height") or 1080)
    except Exception:
        vw, vh = 1920, 1080
    subprocess.run([
        _ffmpeg_bin(), "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={vw}x{vh}:d={dur:.3f}:r=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(plate),
    ], check=True, capture_output=True)

    s = max(0.30, min(0.95, float(leak_strength)))
    clip_dur = min(float(flash_dur), 0.95)
    current = plate
    for i, t0 in enumerate(flash_times):
        start_t = max(0.0, float(t0) - 0.08)
        nxt = tmp / f"plate_{i:02d}.mp4"
        fc = (
            f"[0:v]format=gbrp[base];"
            f"[1:v]scale={vw}:{vh}:force_original_aspect_ratio=increase,"
            f"crop={vw}:{vh},fps=30,setsar=1,"
            f"colorchannelmixer=rr={min(0.95, s*1.15):.3f}:gg={s:.3f}:bb={s*0.75:.3f},"
            f"vignette=PI/3.2:mode=forward:eval=init,"
            f"eq=brightness=0.08:contrast=1.15,"
            f"tpad=start_duration={start_t:.3f}:start_mode=add:color=black,"
            f"tpad=stop_duration={dur:.3f}:color=black,format=gbrp[lk];"
            f"[base][lk]blend=all_mode=screen:shortest=1,format=yuv420p[v]"
        )
        cmd = [
            _ffmpeg_bin(), "-y",
            "-i", str(current),
            "-t", f"{clip_dur:.3f}", "-i", str(leak),
            "-filter_complex", fc,
            "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-t", f"{dur:.4f}",
            str(nxt),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"leak plate flash {i} failed: {(result.stderr or '')[-1000:]}")
        current = nxt

    fc = (
        "[0:v]format=gbrp[base];"
        "[1:v]format=gbrp[lk];"
        "[base][lk]blend=all_mode=screen:shortest=1,format=yuv420p[v]"
    )
    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", str(video_path),
        "-i", str(current),
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
        raise RuntimeError(f"lightleak composite failed: {(result.stderr or '')[-1200:]}")
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass



def _apply_dust_overlay(
    video_path: Path,
    dust_path: Path,
    output_path: Path,
    no_dust_ranges: list[tuple[float, float]] | None = None,
    dust_strength: float = 1.0,
):
    """Apply dust overlay with RGB screen blend (Whop path — YUV screen tints magenta).

    dust_strength > 1.0 brightens the dust plate + raises film grain (style grit; Jung ~2.2).
    no_dust_ranges: black-gate the dust during GFX windows (screen w/ black = noop).
    """
    dur = _get_duration(video_path)
    dust = Path(dust_path)
    alt = dust.with_name("overlay_dust_1080.mp4")
    if alt.exists():
        dust = alt
    strength = max(0.5, min(2.5, float(dust_strength)))
    # Brighten dust plate so screen-blend reads denser; grain scales with strength
    # Whop grit: prefer fine grain over blown plate brightness
    bright = 0.035 * (strength - 1.0)
    contrast = 1.0 + 0.12 * (strength - 1.0)
    grain = int(round(10 + 8 * (strength - 1.0)))  # 10 @1.0 → ~18 @2.0
    grain = max(8, min(22, grain))
    # Match dust plate + gate to program frame size (style-agnostic res)
    import json as _json
    _ff = _ffmpeg_bin()
    fp = str(Path(_ff).with_name("ffprobe")) if "/" in _ff else "ffprobe"
    if not Path(fp).exists():
        fp = "ffprobe"
    probe = subprocess.run(
        [fp, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video_path)],
        capture_output=True, text=True,
    )
    try:
        st = (_json.loads(probe.stdout or "{}").get("streams") or [{}])[0]
        dw = int(st.get("width") or 1920)
        dh = int(st.get("height") or 1080)
    except Exception:
        dw, dh = 1920, 1080
    gate = ""
    if no_dust_ranges:
        expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in no_dust_ranges)
        gate = (
            f",drawbox=x=0:y=0:w={dw}:h={dh}:color=black:t=fill:enable='{expr}'"
        )
    dust_chain = (
        f"scale={dw}:{dh}:force_original_aspect_ratio=increase,"
        f"crop={dw}:{dh},fps=30,setsar=1,"
        f"eq=brightness={bright:.4f}:contrast={contrast:.4f},"
        f"format=gbrp{gate}[dust]"
    )
    fc = (
        f"[1:v]{dust_chain};"
        "[0:v]format=gbrp[base];"
        f"[base][dust]blend=all_mode=screen:shortest=1,format=yuv420p,"
        f"noise=alls={grain}:allf=t+u,"
        f"eq=saturation={max(0.82, 0.95 - 0.08*(strength-1.0)):.3f}:contrast=1.03[v]"
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
    # Whop uses PI/4 exactly at high strength; mild → slightly wider
    if strength >= 0.65:
        angle = math.pi / 4.0
    else:
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


def _mix_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    target_lufs: float = -16.0,
    add_music: bool = False,
):
    """Mix video with voiceover audio, normalized to target LUFS.
    
    Args:
        video_path: Input video (silent or with temp audio)
        audio_path: Voiceover audio file
        output_path: Final output with mixed audio
        target_lufs: Target integrated loudness (default -16 LUFS per formula)
        add_music: Whether to mix background music (default False for Jung/Whop)
    """
    # Two-pass loudness normalization to -16 LUFS (formula rule)
    # Pass 1: Measure integrated loudness
    measure_cmd = [
        _ffmpeg_bin(),
        "-i", str(audio_path),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f", "null",
        "-"
    ]
    
    import json as _json
    result = subprocess.run(measure_cmd, capture_output=True, text=True)
    
    # Extract measured loudness from stderr (ffmpeg outputs JSON to stderr)
    try:
        # Parse loudnorm JSON output from stderr
        stderr_lines = result.stderr.split('\n')
        json_start = None
        for i, line in enumerate(stderr_lines):
            if '{' in line and '"input_i"' in result.stderr[result.stderr.index(line):]:
                json_start = i
                break
        
        if json_start is not None:
            json_block = '\n'.join(stderr_lines[json_start:])
            json_block = json_block[json_block.index('{'):json_block.rindex('}')+1]
            loudness_data = _json.loads(json_block)
            
            measured_i = loudness_data.get("input_i", "-16.0")
            measured_tp = loudness_data.get("input_tp", "-1.5")
            measured_lra = loudness_data.get("input_lra", "11.0")
            measured_thresh = loudness_data.get("input_thresh", "-26.0")
            
            # Pass 2: Apply normalization with measured values
            audio_filter = (
                f"loudnorm=I=-16:TP=-1.5:LRA=11:"
                f"measured_I={measured_i}:"
                f"measured_TP={measured_tp}:"
                f"measured_LRA={measured_lra}:"
                f"measured_thresh={measured_thresh}:"
                f"linear=true:print_format=summary"
            )
        else:
            # Fallback to single-pass if measurement fails
            audio_filter = "loudnorm=I=-16:TP=-1.5:LRA=11"
    except:
        # Fallback to single-pass if parsing fails
        audio_filter = "loudnorm=I=-16:TP=-1.5:LRA=11"
    
    # Mix video + normalized audio (no music for Jung/Whop per formula)
    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-filter_complex", f"[1:a]{audio_filter}[a]",
        "-map", "0:v",
        "-map", "[a]",
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

