"""
Trial watermark — burns a small "channelrecipe.com" bug into the bottom-right
of a rendered video. Applied to FREE/trial renders only; paid renders stay
pristine (their clean channels are our proof library).

Implementation note: we render the mark as a transparent PNG with PIL and
composite it with ffmpeg's `overlay` filter (a core filter, always available),
rather than `drawtext` (which requires an ffmpeg built with libfreetype and is
missing on many builds). This keeps the watermark reliable in production.

Spec: bottom-right, inset ~3% from edges, ~3.8% of frame height, ~68% opacity,
subtle shadow for legibility, never an intro bumper, never over the caption zone.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

from config import VIDEO_HEIGHT
from core.text_overlay import _SYSTEM_FONT

WATERMARK_TEXT = "channelrecipe.com"


def _render_watermark_png(text: str, frame_height: int) -> str:
    """Render the watermark text to a transparent PNG; return its path."""
    font_size = max(20, int(frame_height * 0.038))
    try:
        font = ImageFont.truetype(_SYSTEM_FONT, font_size) if _SYSTEM_FONT else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    pad = max(4, font_size // 6)
    tmp = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ox, oy = pad - bbox[0], pad - bbox[1]
    # Soft shadow for legibility over bright footage, then the mark at ~68%.
    draw.text((ox + 2, oy + 2), text, font=font, fill=(0, 0, 0, 150))
    draw.text((ox, oy), text, font=font, fill=(255, 255, 255, 174))

    fd, path = tempfile.mkstemp(suffix=".png", prefix="cr_wm_")
    os.close(fd)
    img.save(path)
    return path


def apply_watermark(input_path: str, output_path: str | None = None, text: str = WATERMARK_TEXT) -> str:
    """Return a path to a watermarked copy of `input_path`.

    Raises RuntimeError if ffmpeg fails so the caller can fall back to the
    clean render rather than shipping nothing.
    """
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_wm{ext or '.mp4'}"

    wm_png = _render_watermark_png(text, VIDEO_HEIGHT)
    try:
        overlay = (
            "[0:v][1:v]overlay="
            "x=main_w-overlay_w-(main_w*0.03):"
            "y=main_h-overlay_h-(main_h*0.03)"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", wm_png,
            "-filter_complex", overlay,
            "-c:a", "copy",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(f"watermark ffmpeg failed: {result.stderr[-500:]}")
        return output_path
    finally:
        try:
            os.remove(wm_png)
        except OSError:
            pass
