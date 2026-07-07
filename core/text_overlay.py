"""
Text overlay clip generator for cinematic B-roll.

Renders impactful text interstitials with textured backgrounds --
never plain black. Text overlays show 1-3 high-impact words with
cinematic styling, not full sentences.
"""

from __future__ import annotations
import math
import os
import random
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS


def _find_system_font() -> str:
    """Find the best available sans-serif font across platforms."""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",              # macOS
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",  # Linux (Docker)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",          # Linux fallback
        "C:\\Windows\\Fonts\\arial.ttf",                     # Windows
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",         # Arch Linux
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


_SYSTEM_FONT = _find_system_font()


STYLES = {
    "cinematic": {
        "fg": (240, 240, 240),
        "accent": (200, 170, 90),
        "font_size": 96,
        "sub_font_size": 36,
        "font_name": _SYSTEM_FONT,
        "texture": "dark_grain",
    },
    "bold": {
        "fg": (255, 255, 255),
        "accent": (255, 60, 60),
        "font_size": 110,
        "sub_font_size": 40,
        "font_name": _SYSTEM_FONT,
        "texture": "dark_vignette",
    },
    "minimal": {
        "fg": (220, 220, 220),
        "accent": (100, 180, 255),
        "font_size": 80,
        "sub_font_size": 32,
        "font_name": _SYSTEM_FONT,
        "texture": "paper",
    },
}


def _generate_texture(texture_type: str, w: int, h: int) -> Image.Image:
    """Generate a textured background -- never plain black."""
    img = Image.new("RGB", (w, h), (12, 12, 18))

    if texture_type == "dark_grain":
        pixels = img.load()
        for y in range(h):
            for x in range(0, w, 2):
                noise = random.randint(-8, 8)
                base = 12 + int(6 * math.sin(x * 0.003) * math.cos(y * 0.005))
                v = max(0, min(30, base + noise))
                pixels[x, y] = (v, v, v + 3)
                if x + 1 < w:
                    pixels[x + 1, y] = (v, v, v + 3)
        img = img.filter(ImageFilter.GaussianBlur(radius=0.8))

        draw = ImageDraw.Draw(img)
        vignette_strength = 80
        for i in range(vignette_strength):
            alpha = int(255 * (1 - i / vignette_strength) * 0.4)
            draw.rectangle(
                [i, i, w - i - 1, h - i - 1],
                outline=(0, 0, 0),
            )

    elif texture_type == "dark_vignette":
        draw = ImageDraw.Draw(img)
        cx, cy = w // 2, h // 2
        max_r = math.sqrt(cx**2 + cy**2)
        for r in range(int(max_r), 0, -3):
            factor = (r / max_r) ** 1.5
            c = int(18 * (1 - factor))
            draw.ellipse(
                [cx - r, cy - r, cx + r, cy + r],
                fill=(c, c, c + 2),
            )

    elif texture_type == "paper":
        pixels = img.load()
        for y in range(h):
            for x in range(0, w, 2):
                noise = random.randint(-5, 5)
                base = 25 + int(4 * math.sin(x * 0.01 + y * 0.007))
                v = max(10, min(40, base + noise))
                pixels[x, y] = (v + 3, v + 2, v)
                if x + 1 < w:
                    pixels[x + 1, y] = (v + 3, v + 2, v)
        img = img.filter(ImageFilter.GaussianBlur(radius=1.0))

    else:
        draw = ImageDraw.Draw(img)
        for i in range(0, 60):
            c = max(0, 15 - i // 4)
            draw.rectangle([i, i, w - i, h - i], outline=(c, c, c + 2))

    return img


def render_text_clip(
    text: str,
    output_path: str,
    duration_sec: float,
    style: str = "cinematic",
    effect: str = "fade",
    subtitle: str = "",
) -> bool:
    """
    Render a cinematic text interstitial as MP4.
    Text should be 1-3 impactful words, not full sentences.
    Background is always textured, never plain black.
    """
    s = STYLES.get(style, STYLES["cinematic"])

    try:
        font = ImageFont.truetype(s["font_name"], s["font_size"])
    except Exception:
        font = ImageFont.load_default()

    try:
        sub_font = ImageFont.truetype(s["font_name"], s["sub_font_size"])
    except Exception:
        sub_font = ImageFont.load_default()

    total_frames = max(int(duration_sec * VIDEO_FPS), 1)
    bg_texture = _generate_texture(s.get("texture", "dark_grain"), VIDEO_WIDTH, VIDEO_HEIGHT)

    tmpdir = tempfile.mkdtemp(prefix="text_overlay_")

    try:
        _render_cinematic(tmpdir, text, subtitle, total_frames, font, sub_font, s, bg_texture)
        success = _frames_to_video(tmpdir, output_path, total_frames)
        return success
    finally:
        for f in Path(tmpdir).glob("*.png"):
            f.unlink()
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def _render_cinematic(
    tmpdir: str, text: str, subtitle: str,
    total_frames: int, font, sub_font, style: dict,
    bg_texture: Image.Image,
):
    """
    Cinematic reveal: fade in with subtle zoom on textured background.
    Impactful words pop in, hold, then slightly drift.
    """
    fade_in = min(int(total_frames * 0.2), total_frames)
    hold_start = fade_in
    fade_out_start = max(int(total_frames * 0.85), hold_start + 1)

    for frame_idx in range(total_frames):
        if frame_idx < fade_in:
            alpha = frame_idx / max(fade_in, 1)
        elif frame_idx >= fade_out_start:
            alpha = 1.0 - (frame_idx - fade_out_start) / max(total_frames - fade_out_start, 1)
        else:
            alpha = 1.0

        alpha = max(0.0, min(1.0, alpha))

        zoom = 1.0 + 0.02 * (frame_idx / max(total_frames, 1))
        _draw_cinematic_frame(
            tmpdir, frame_idx, text, subtitle,
            font, sub_font, style, bg_texture,
            alpha, zoom,
        )


def _draw_cinematic_frame(
    tmpdir: str, frame_idx: int,
    text: str, subtitle: str,
    font, sub_font, style: dict,
    bg_texture: Image.Image,
    alpha: float, zoom: float,
):
    """Draw a single cinematic text frame with textured background."""
    if zoom != 1.0:
        zw = int(VIDEO_WIDTH / zoom)
        zh = int(VIDEO_HEIGHT / zoom)
        left = (VIDEO_WIDTH - zw) // 2
        top = (VIDEO_HEIGHT - zh) // 2
        cropped = bg_texture.crop((left, top, left + zw, top + zh))
        img = cropped.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)
    else:
        img = bg_texture.copy()

    draw = ImageDraw.Draw(img)

    if text and alpha > 0:
        fg = tuple(int(c * alpha) for c in style["fg"])
        accent = tuple(int(c * alpha) for c in style["accent"])

        lines = _wrap_text(text.upper(), font, draw, VIDEO_WIDTH - 300)
        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_heights.append(bbox[3] - bbox[1])

        total_text_h = sum(line_heights) + (len(lines) - 1) * 16
        y = (VIDEO_HEIGHT - total_text_h) // 2

        for line, lh in zip(lines, line_heights):
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            x = (VIDEO_WIDTH - tw) // 2

            shadow_offset = 3
            shadow_color = tuple(int(c * alpha * 0.3) for c in (0, 0, 0))
            draw.text((x + shadow_offset, y + shadow_offset), line,
                       fill=shadow_color, font=font)
            draw.text((x, y), line, fill=fg, font=font)
            y += lh + 16

        bar_y = VIDEO_HEIGHT // 2 + total_text_h // 2 + 30
        bar_w = min(160, VIDEO_WIDTH // 6)
        bar_alpha = int(alpha * 255)
        draw.rectangle(
            [(VIDEO_WIDTH // 2 - bar_w // 2, bar_y),
             (VIDEO_WIDTH // 2 + bar_w // 2, bar_y + 2)],
            fill=accent,
        )

        if subtitle and alpha > 0.8:
            sub_alpha = min(1.0, (alpha - 0.8) / 0.2)
            sub_fg = tuple(int(c * sub_alpha) for c in accent)
            bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
            tw = bbox[2] - bbox[0]
            x = (VIDEO_WIDTH - tw) // 2
            draw.text((x, VIDEO_HEIGHT - 160), subtitle, fill=sub_fg, font=sub_font)

    frame_path = os.path.join(tmpdir, f"frame_{frame_idx:06d}.png")
    img.save(frame_path)


def _wrap_text(text: str, font, draw, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines or [text]


def _frames_to_video(tmpdir: str, output_path: str, total_frames: int) -> bool:
    """Assemble PNG frames into MP4 with ffmpeg."""
    pattern = os.path.join(tmpdir, "frame_%06d.png")
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(VIDEO_FPS),
        "-i", pattern,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-an",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except Exception as e:
        print(f"[text_overlay] ffmpeg error: {e}")
        return False
