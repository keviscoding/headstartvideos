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
    valid = [p for p in clip_paths if p and os.path.isfile(p) and os.path.getsize(p) > 500]
    if not valid:
        print("[assembler] concat called with no valid clip files")
        return False

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        for path in valid:
            abs_path = os.path.abspath(path)
            # Escape single quotes for concat demuxer
            safe = abs_path.replace("'", r"'\''")
            f.write(f"file '{safe}'\n")
        list_path = f.name

    # Clips are all produced with identical codec/params, so we can stream-copy
    # (join without re-encoding) — near-instant and, crucially, the final
    # assemble pass re-encodes everything anyway, so re-encoding here is wasted
    # CPU that was timing out on small instances.
    copy_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        result = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            try:
                os.unlink(list_path)
            except OSError:
                pass
            return True
        print(f"[assembler] copy-concat failed, falling back to re-encode: {result.stderr[-300:]}")
    except Exception as e:
        print(f"[assembler] copy-concat exception, falling back to re-encode: {e}")

    # Fallback: clips weren't uniform — normalize with a fast re-encode.
    reencode_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-r", str(VIDEO_FPS),
        "-pix_fmt", "yuv420p",
        "-threads", "0",
        "-an",
        output_path,
    ]
    try:
        result = subprocess.run(reencode_cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"[assembler] concat failed: {result.stderr[-500:]}")
        ok = result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000
        return ok
    except Exception as e:
        print(f"[assembler] concat exception: {e}")
        return False
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


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
        "-preset", "veryfast",
        "-crf", "20",
        "-threads", "0",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0:
            print(f"[assembler] ffmpeg stderr: {result.stderr[-500:]}")
        return result.returncode == 0
    except Exception as e:
        print(f"[assembler] Exception: {e}")
        return False


def _concat_file_line(path: str) -> str:
    """Escape a path for ffmpeg concat demuxer (single-quoted)."""
    abs_path = os.path.abspath(path).replace("'", r"'\''")
    return f"file '{abs_path}'\n"


def _slideshow_from_images(
    image_paths: list[str],
    durations: list[float],
    voiceover_path: str,
    output_path: str,
    bg_music_path: str | None = None,
    *,
    with_audio: bool = True,
) -> bool:
    """One-pass ffmpeg: images + durations (+ optional audio) → video.

    Uses the concat demuxer with `duration` per image, avoiding the need to
    encode each image into a separate clip first (eliminates 3 encode passes).
    """
    if not image_paths or not durations or len(image_paths) != len(durations):
        print("[assembler] slideshow: empty or mismatched image/duration lists")
        return False

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for img, dur in zip(image_paths, durations):
            f.write(_concat_file_line(img))
            f.write(f"duration {max(0.04, float(dur)):.4f}\n")
        # ffmpeg concat demuxer needs the last file repeated without duration
        f.write(_concat_file_line(image_paths[-1]))
        list_path = f.name

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", list_path,
    ]
    if with_audio:
        cmd += ["-i", voiceover_path]
        if bg_music_path:
            cmd += [
                "-i", bg_music_path,
                "-filter_complex",
                "[1:a]volume=1.0[vo];[2:a]volume=0.08[bg];[vo][bg]amix=inputs=2:duration=first[aout]",
                "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},format=yuv420p",
                "-map", "0:v", "-map", "[aout]",
            ]
        else:
            cmd += [
                "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},format=yuv420p",
                "-map", "0:v", "-map", "1:a",
            ]
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-r", str(VIDEO_FPS), "-threads", "0",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        # Silent video segment (used for chunked assembly of long explainers).
        cmd += [
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},format=yuv420p",
            "-map", "0:v",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-r", str(VIDEO_FPS), "-threads", "0",
            "-an", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ]

    try:
        # Long explainers (200+ scenes) need more wall time.
        timeout = min(3600, max(1200, 30 + len(image_paths) * 3))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"[assembler] slideshow ffmpeg stderr: {result.stderr[-800:]}")
            return False
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        print(f"[assembler] slideshow exception: {e}")
        return False
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


def _slideshow_chunked(
    image_paths: list[str],
    durations: list[float],
    voiceover_path: str,
    output_path: str,
    bg_music_path: str | None = None,
    *,
    chunk_size: int = 50,
    progress_callback=None,
) -> bool:
    """Assemble long slideshows in chunks, then concat + mux audio."""
    n = len(image_paths)
    if n <= chunk_size:
        return _slideshow_from_images(
            image_paths, durations, voiceover_path, output_path, bg_music_path
        )

    out_dir = Path(output_path).parent
    segment_paths: list[str] = []
    try:
        for start in range(0, n, chunk_size):
            end = min(n, start + chunk_size)
            seg = str(out_dir / f"_slideshow_seg_{start:04d}.mp4")
            if progress_callback:
                progress_callback(f"Assembling scenes {start + 1}–{end} of {n}...")
            ok = _slideshow_from_images(
                image_paths[start:end],
                durations[start:end],
                voiceover_path,
                seg,
                None,
                with_audio=False,
            )
            if not ok:
                print(f"[assembler] chunked slideshow failed on scenes {start + 1}–{end}")
                return False
            segment_paths.append(seg)

        if progress_callback:
            progress_callback("Stitching scene segments...")
        video_only = str(out_dir / "_slideshow_video.mp4")
        if not concatenate_clips(segment_paths, video_only):
            print("[assembler] chunked concat of silent segments failed")
            return False

        # Mux voiceover (+ optional bg) onto the silent video.
        if bg_music_path:
            filter_complex = (
                "[1:a]volume=1.0[vo];"
                "[2:a]volume=0.08[bg];"
                "[vo][bg]amix=inputs=2:duration=first[aout]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", video_only,
                "-i", voiceover_path,
                "-i", bg_music_path,
                "-filter_complex", filter_complex,
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                "-movflags", "+faststart",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", video_only,
                "-i", voiceover_path,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                "-movflags", "+faststart",
                output_path,
            ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            print(f"[assembler] chunked mux stderr: {result.stderr[-800:]}")
            return False
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    finally:
        for p in segment_paths + [str(out_dir / "_slideshow_video.mp4")]:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


def build_video(
    clip_paths: list[str],
    voiceover_path: str,
    slots: list[dict],
    output_path: str,
    bg_music_path: str | None = None,
    progress_callback=None,
    image_paths: list[str] | None = None,
    durations: list[float] | None = None,
    **_kwargs,
) -> str:
    """
    Full assembly → final MP4.

    If `image_paths` + `durations` are provided, uses a single-pass slideshow
    encode (images → video + audio in one ffmpeg call). Long jobs retry in
    chunks. Legacy clip_paths path is only used when clips already exist.
    """
    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Image slideshow path (explainer / preferred) ---
    if image_paths and durations:
        if progress_callback:
            progress_callback("Assembling video...")

        ok = _slideshow_from_images(
            image_paths, durations, voiceover_path, output_path, bg_music_path
        )
        if not ok and len(image_paths) > 40:
            print("[assembler] Single-pass failed — retrying chunked slideshow")
            if progress_callback:
                progress_callback("Retrying assembly in chunks...")
            ok = _slideshow_chunked(
                image_paths,
                durations,
                voiceover_path,
                output_path,
                bg_music_path,
                progress_callback=progress_callback,
            )

        if ok:
            return output_path

        # Only fall back to legacy when we actually have rendered clips.
        if clip_paths:
            print("[assembler] Slideshow failed, falling back to legacy multi-pass")
            return build_video(
                clip_paths=clip_paths, voiceover_path=voiceover_path,
                slots=slots, output_path=output_path,
                bg_music_path=bg_music_path,
                progress_callback=progress_callback,
                image_paths=None, durations=None,
            )
        raise RuntimeError(
            f"Failed to assemble video from {len(image_paths)} scenes. "
            "Try a shorter script, or contact support if this keeps happening."
        )

    # --- Legacy multi-pass path ---
    if not clip_paths:
        raise RuntimeError("Failed to concatenate clips: no clips were rendered")
    if progress_callback:
        progress_callback("Concatenating clips...")

    concat_path = str(out_dir / "_concat.mp4")
    if not concatenate_clips(clip_paths, concat_path):
        raise RuntimeError("Failed to concatenate clips")

    if progress_callback:
        progress_callback("Assembling final video...")

    sub_path = str(out_dir / "_subtitles.ass")
    generate_ass_subtitles(slots, sub_path)

    if not assemble_final(
        concat_path, voiceover_path, sub_path, output_path, bg_music_path
    ):
        raise RuntimeError("Failed to assemble final video")

    Path(concat_path).unlink(missing_ok=True)

    return output_path
