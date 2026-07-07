"""
Final video assembler. Concatenates Ken Burns clips, overlays voiceover
audio, burns in subtitles, and exports the final MP4.
"""

from __future__ import annotations
import os
import subprocess
import tempfile
from pathlib import Path
from config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS


def generate_ass_subtitles(
    slots: list[dict],
    output_path: str,
) -> str:
    """
    Generate an ASS subtitle file from B-roll slots.
    slots: [{"text": str, "start_sec": float, "end_sec": float}, ...]
    """
    def _fmt_time(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    header = f"""[Script Info]
Title: B-Roll Video
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,58,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,2,0,2,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]
    for slot in slots:
        start = _fmt_time(slot["start_sec"])
        end = _fmt_time(slot["end_sec"])
        text = slot["text"].replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"
        )

    content = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path


def concatenate_clips(
    clip_paths: list[str],
    output_path: str,
) -> bool:
    """Concatenate video clips using ffmpeg concat demuxer."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        for path in clip_paths:
            abs_path = os.path.abspath(path)
            f.write(f"file '{abs_path}'\n")
        list_path = f.name

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-r", str(VIDEO_FPS),
        "-pix_fmt", "yuv420p",
        "-an",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"[assembler] concat failed: {result.stderr[-500:]}")
        return result.returncode == 0
    except Exception as e:
        print(f"[assembler] concat exception: {e}")
        return False


def _check_ass_filter() -> bool:
    """Check if ffmpeg has the ass subtitle filter available."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-filters"], capture_output=True, text=True, timeout=5
        )
        return " ass " in r.stdout or "ass=" in r.stdout
    except Exception:
        return False


def assemble_final(
    concat_video: str,
    voiceover_path: str,
    subtitle_path: str,
    output_path: str,
    bg_music_path: str | None = None,
) -> bool:
    """
    Merge concatenated video with voiceover audio and burned-in subtitles.
    Optionally mix in background music at low volume.
    """
    inputs = ["-i", concat_video, "-i", voiceover_path]
    filter_parts = []

    if bg_music_path:
        inputs.extend(["-i", bg_music_path])
        filter_parts.append(
            "[1:a]volume=1.0[vo];"
            "[2:a]volume=0.08[bg];"
            "[vo][bg]amix=inputs=2:duration=first[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = "1:a"

    has_ass = _check_ass_filter()

    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)

    if has_ass and subtitle_path:
        import shutil
        tmp_sub = os.path.join(tempfile.gettempdir(), f"_vf_subtitles_{os.getpid()}.ass")
        shutil.copy2(subtitle_path, tmp_sub)
        sub_filter = f"ass={tmp_sub}"
    else:
        sub_filter = None

    if filter_parts:
        full_filter = ";".join(filter_parts)
        cmd.extend(["-filter_complex", full_filter])
        if sub_filter:
            cmd.extend(["-vf", sub_filter])
        cmd.extend(["-map", "0:v", "-map", audio_map])
    else:
        if sub_filter:
            cmd.extend(["-vf", sub_filter])
        cmd.extend(["-map", "0:v", "-map", audio_map])

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        output_path,
    ])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"[assembler] ffmpeg stderr: {result.stderr[-500:]}")
        return result.returncode == 0
    except Exception as e:
        print(f"[assembler] Exception: {e}")
        return False


def build_video(
    clip_paths: list[str],
    voiceover_path: str,
    slots: list[dict],
    output_path: str,
    bg_music_path: str | None = None,
    caption_style: str = "Clean",
    caption_accent: str = "#00BFFF",
    caption_font_size: str = "Medium",
    caption_position: str = "Bottom",
    progress_callback=None,
) -> str:
    """
    Full assembly: concat clips -> overlay audio -> burn subtitles -> export.
    Returns the path to the final MP4.
    """
    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback("Concatenating clips...")

    concat_path = str(out_dir / "_concat.mp4")
    if not concatenate_clips(clip_paths, concat_path):
        raise RuntimeError("Failed to concatenate clips")

    if progress_callback:
        progress_callback("Generating subtitles...")

    sub_path = str(out_dir / "_subtitles.ass")
    generate_ass_subtitles(slots, sub_path)

    if progress_callback:
        progress_callback("Assembling final video...")

    no_caption_output = str(out_dir / "_no_captions.mp4")

    if not assemble_final(
        concat_path, voiceover_path, sub_path, no_caption_output, bg_music_path
    ):
        raise RuntimeError("Failed to assemble final video")

    Path(concat_path).unlink(missing_ok=True)

    if caption_style and caption_style != "None":
        if progress_callback:
            progress_callback(f"Burning {caption_style} captions...")
        try:
            from core.captions import burn_captions_simple
            burn_captions_simple(
                video_path=no_caption_output,
                output_path=output_path,
                caption_style=caption_style,
                accent_color=caption_accent,
                font_size=caption_font_size,
                position=caption_position,
            )
            if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
                Path(no_caption_output).unlink(missing_ok=True)
            else:
                print("[assembler] Caption output empty, using ASS-subtitled version")
                Path(no_caption_output).rename(output_path)
        except Exception as e:
            print(f"[assembler] Caption burning failed ({e}), using ASS-subtitled version")
            if Path(no_caption_output).exists():
                if Path(output_path).exists():
                    Path(output_path).unlink(missing_ok=True)
                Path(no_caption_output).rename(output_path)
    else:
        Path(no_caption_output).rename(output_path)

    return output_path
