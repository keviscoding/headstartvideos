"""
Ken Burns effect generator using ffmpeg zoompan filter.
Renders each image as a video clip with randomized zoom/pan.
Supports parallel rendering across multiple CPU cores.
"""

from __future__ import annotations
import subprocess
import random
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS


EFFECTS = [
    "zoom_in_center",
    "zoom_out_center",
    "pan_left_to_right",
    "pan_right_to_left",
    "zoom_in_drift_right",
    "zoom_in_drift_left",
]


def _build_zoompan_filter(effect: str, duration_sec: float) -> str:
    """Build the ffmpeg zoompan filter string for a given effect."""
    total_frames = int(duration_sec * VIDEO_FPS)
    d = total_frames
    w, h = VIDEO_WIDTH, VIDEO_HEIGHT

    if effect == "zoom_in_center":
        return (
            f"zoompan=z='min(zoom+0.0008,1.2)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={w}x{h}:fps={VIDEO_FPS}"
        )
    elif effect == "zoom_out_center":
        return (
            f"zoompan=z='if(eq(on,1),1.2,max(zoom-0.0008,1.0))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={w}x{h}:fps={VIDEO_FPS}"
        )
    elif effect == "pan_left_to_right":
        return (
            f"zoompan=z=1.15:"
            f"x='(iw/zoom-iw)*on/{d}':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={w}x{h}:fps={VIDEO_FPS}"
        )
    elif effect == "pan_right_to_left":
        return (
            f"zoompan=z=1.15:"
            f"x='(iw/zoom-iw)*(1-on/{d})':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={w}x{h}:fps={VIDEO_FPS}"
        )
    elif effect == "zoom_in_drift_right":
        return (
            f"zoompan=z='min(zoom+0.0006,1.15)':"
            f"x='(iw/zoom-iw)*on/{d}*0.3':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={w}x{h}:fps={VIDEO_FPS}"
        )
    elif effect == "zoom_in_drift_left":
        return (
            f"zoompan=z='min(zoom+0.0006,1.15)':"
            f"x='(iw/zoom-iw)*(1-on/{d}*0.3)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={w}x{h}:fps={VIDEO_FPS}"
        )
    else:
        return _build_zoompan_filter("zoom_in_center", duration_sec)


def render_clip(
    image_path: str,
    output_path: str,
    duration_sec: float,
    effect: str,
) -> bool:
    """Render a single image as a Ken Burns video clip."""
    zoompan = _build_zoompan_filter(effect, duration_sec)

    vf = (
        f"scale={VIDEO_WIDTH * 2}:{VIDEO_HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH * 2}:{VIDEO_HEIGHT * 2},"
        f"{zoompan},"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-vf", vf,
        "-t", f"{duration_sec:.2f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-an",
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0
    except Exception:
        return False


def pick_effects(count: int) -> list[str]:
    """Pick effects ensuring no two consecutive clips use the same one."""
    chosen: list[str] = []
    for i in range(count):
        available = [e for e in EFFECTS if not chosen or e != chosen[-1]]
        chosen.append(random.choice(available))
    return chosen


def render_all_clips(
    clips: list[dict],
    output_dir: str,
    max_workers: int = 4,
    progress_callback=None,
) -> list[str]:
    """
    Render all clips in parallel.
    clips: [{"id": int, "image_path": str, "duration_sec": float}, ...]
    Returns: list of output video paths in order.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    effects = pick_effects(len(clips))
    tasks: list[dict] = []

    for clip, effect in zip(clips, effects):
        out_path = str(out_dir / f"clip_{clip['id']:04d}.mp4")
        tasks.append({
            "image_path": clip["image_path"],
            "output_path": out_path,
            "duration_sec": clip["duration_sec"],
            "effect": effect,
            "id": clip["id"],
        })

    output_map: dict[int, str] = {}
    completed = 0

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                render_clip,
                t["image_path"], t["output_path"],
                t["duration_sec"], t["effect"]
            ): t
            for t in tasks
        }

        for future in as_completed(futures):
            task = futures[future]
            success = future.result()
            if success:
                output_map[task["id"]] = task["output_path"]
            completed += 1
            if progress_callback:
                progress_callback(completed, len(tasks))

    # Return paths in the original task order
    return [output_map[t["id"]] for t in tasks if t["id"] in output_map]
