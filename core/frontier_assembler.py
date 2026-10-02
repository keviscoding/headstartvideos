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
    zoom_amount: float = 0.15,
    word_timings: list[dict] | None = None,
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
    
    # Burn subtitles with ASS style (with word-level kinetic if available)
    subtitled_path = temp_dir / "subtitled.mp4"
    _burn_subtitles(
        video_path=concat_path,
        subtitle_path=subtitle_path,
        output_path=subtitled_path,
        word_timings=word_timings,
    )
    
    # Apply dust overlay if requested
    if add_dust and dust_overlay_path and Path(dust_overlay_path).exists():
        if progress_callback:
            progress_callback("Adding dust overlay...")
        
        dust_path = temp_dir / "with_dust.mp4"
        _apply_dust_overlay(subtitled_path, dust_overlay_path, dust_path)
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
    
    if segment.type == "pexels_video":
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
                "ffmpeg", "-y",
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
                "ffmpeg", "-y",
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
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(input_path),
            "-t", str(duration),
            "-vf", vf,
            "-r", str(fps),  # Normalize FPS
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_path),
        ]
    
    subprocess.run(cmd, check=True, capture_output=True)


def _ken_burns_filter(
    zoom_type: Literal["in", "out", "hold"],
    duration: float,
    fps: int,
    width: int,
    height: int,
    zoom_amount: float = 0.20,  # Increased from 0.15 to 0.20 for more visible zoom
) -> str:
    """Generate ffmpeg filter for Ken Burns zoom effect (minimum 12% delta)."""
    if zoom_type == "hold":
        # No zoom, just scale to fit
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    
    # Ensure minimum visible zoom
    zoom_amount = max(zoom_amount, 0.12)
    
    total_frames = int(duration * fps)
    
    # Ken Burns: zoom from 1.0 to (1.0 + zoom_amount)
    if zoom_type == "in":
        # Zoom in: start at 1.0, end at 1.0 + zoom_amount
        start_zoom = 1.0
        end_zoom = 1.0 + zoom_amount
    else:  # zoom_type == "out"
        # Zoom out: start at 1.0 + zoom_amount, end at 1.0
        start_zoom = 1.0 + zoom_amount
        end_zoom = 1.0
    
    # Use zoompan filter for smooth zoom
    # Linear zoom formula: interpolate from start_zoom to end_zoom over total_frames
    zoom_expr = f"'if(lte(on,1),{start_zoom},{start_zoom}+({end_zoom}-{start_zoom})*(on-1)/({total_frames}-1))'"
    
    # Scale input larger to allow zoom headroom
    input_scale = int(width * 1.5)  # 50% larger for zoom headroom
    
    return (
        f"scale={input_scale}:-1:force_original_aspect_ratio=increase,"
        f"zoompan=z={zoom_expr}:d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
    )


def _concatenate_segments(segment_paths: list[Path], output_path: Path, fps: int):
    """Concatenate segment videos into one."""
    concat_file = output_path.parent / "concat_list.txt"
    
    with open(concat_file, "w") as f:
        for seg_path in segment_paths:
            f.write(f"file '{seg_path.absolute()}'\n")
    
    cmd = [
        "ffmpeg", "-y",
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
):
    """Burn ASS subtitles with kinetic style using libass filter."""
    # If input is already ASS, use it directly; otherwise convert SRT to ASS
    if subtitle_path.suffix.lower() == ".ass":
        ass_path = subtitle_path
    else:
        # Convert SRT to ASS with Frontier style (with word-level karaoke if available)
        ass_path = subtitle_path.parent / (subtitle_path.stem + "_frontier.ass")
        _convert_srt_to_ass(subtitle_path, ass_path, word_timings=word_timings)
    
    # Burn with libass filter (handles \k karaoke tags correctly)
    # CRITICAL: Use ass= filter, NOT subtitles= (subtitles doesn't support ASS karaoke)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"ass={str(ass_path)}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        str(output_path),
    ]
    
    subprocess.run(cmd, check=True, capture_output=True)


def _convert_srt_to_ass(
    srt_path: Path,
    ass_path: Path,
    accent_color: str = "&H96B8C9&",
    word_timings: list[dict] | None = None,
):
    """
    Convert SRT to ASS with Frontier kinetic caption style.
    
    If word_timings provided, creates word-level karaoke effects where each
    word flashes in accent color as it's spoken (Frontier's signature style).
    
    ASS colors are in &HBBGGRR& format (BGR, not RGB).
    Default accent: #C9B896 (warm gold) -> &H96B8C9&
    
    Args:
        srt_path: Input SRT file
        ass_path: Output ASS file  
        accent_color: ASS color for word flash (&HBBGGRR& format)
        word_timings: Optional list of {word, start, end} dicts from Whisper
    """
    # Read SRT
    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()
    
    ass_header = f"""[Script Info]
Title: Frontier Subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Inter ExtraBold,73,&HFFFFFF&,{accent_color},&H000000&,&H64000000&,1,0,0,0,100,100,0,0,1,3.4,0,2,20,20,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    # Parse SRT blocks
    events = []
    blocks = [b.strip() for b in srt_content.strip().split("\n\n") if b.strip()]
    
    for block in blocks:
        lines = block.split("\n")
        if len(lines) >= 3:
            timing_line = lines[1]
            text_lines = lines[2:]
            
            if " --> " not in timing_line:
                continue
            
            start, end = timing_line.split(" --> ")
            start_ass = _srt_time_to_ass(start.strip())
            end_ass = _srt_time_to_ass(end.strip())
            
            text = " ".join(text_lines).strip()
            
            # If we have word timings, create kinetic karaoke effect
            if word_timings:
                text = _add_kinetic_karaoke(text, start, end, word_timings, accent_color)
            else:
                # No karaoke: escape plain text braces that would be interpreted as overrides
                text = text.replace("{", "\\{").replace("}", "\\}")
            
            # DO NOT escape after adding karaoke - {\k...} tags must remain literal
            
            events.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}")
    
    ass_content = ass_header + "\n".join(events)
    
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)


def _add_kinetic_karaoke(
    subtitle_text: str,
    sub_start: str,
    sub_end: str,
    word_timings: list[dict],
    accent_color: str,
) -> str:
    r"""
    Add word-level karaoke effect using ASS \k tags.
    
    Each word gets a \k duration tag, and we use color override to flash
    the accent color as each word is spoken (Frontier's kinetic style).
    
    The \k tag duration is in centiseconds (100ths of a second).
    Frontier uses precise word-level timing derived from Whisper alignment.
    
    Returns ASS-formatted text with \k tags and color overrides.
    """
    # Convert SRT times to seconds
    sub_start_sec = _srt_time_to_seconds(sub_start)
    sub_end_sec = _srt_time_to_seconds(sub_end)
    
    # Find words that fall in this subtitle's time range
    subtitle_words = []
    for word_timing in word_timings:
        word_start = word_timing["start"]
        # Include words that start before subtitle end
        if sub_start_sec <= word_start < sub_end_sec:
            subtitle_words.append(word_timing)
    
    if not subtitle_words:
        # No word timings in this range, return plain text
        return subtitle_text
    
    # Build text with \k tags
    # \k<duration> makes the next syllable/word light up with karaoke timing
    # The accent color flashes on each word as it's spoken
    result = ""
    
    for i, word_timing in enumerate(subtitle_words):
        word = word_timing["word"].strip()
        word_start = word_timing["start"]
        word_end = word_timing["end"]
        
        # Duration in centiseconds for \k tag
        # This is how long the word is highlighted in the accent color
        duration_cs = max(1, int((word_end - word_start) * 100))
        
        # Jung/Frontier style: Each word flashes gold as spoken
        # The \k tag controls the karaoke sweep duration
        # The color override makes it appear in accent during that duration
        result += f"{{\\k{duration_cs}\\c{accent_color}}}{word} "
    
    return result.strip()


def _srt_time_to_seconds(srt_time: str) -> float:
    """Convert SRT timestamp to seconds."""
    # SRT format: 00:00:01,000
    if "," in srt_time:
        time_part, ms_part = srt_time.split(",")
        h, m, s = map(int, time_part.split(":"))
        ms = int(ms_part)
        return h * 3600 + m * 60 + s + ms / 1000.0
    return 0.0


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


def _apply_dust_overlay(video_path: Path, dust_path: Path, output_path: Path):
    """Apply dust overlay with screen blend."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(dust_path),
        "-filter_complex",
        "[0:v][1:v]blend=all_mode=screen:all_opacity=0.3[v]",
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-t", str(_get_duration(video_path)),
        str(output_path),
    ]
    
    subprocess.run(cmd, check=True, capture_output=True)


def _apply_vignette(video_path: Path, output_path: Path, strength: float = 0.35):
    """Apply dark vignette around edges."""
    vignette_filter = (
        f"vignette=angle=PI/4:mode=forward:eval=frame:"
        f"a={strength}:x0=0.5:y0=0.5"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", vignette_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        str(output_path),
    ]
    
    subprocess.run(cmd, check=True, capture_output=True)


def _mix_audio(video_path: Path, audio_path: Path, output_path: Path):
    """Mix video with voiceover audio."""
    cmd = [
        "ffmpeg", "-y",
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
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except:
        return 0.0
