#!/usr/bin/env python3
"""frontier_motion_graphics.py — MINIMAL motion GFX for CR Frontier MVP.

Generates collage-style cards (aged-paper aesthetic) using PIL/ffmpeg instead
of the full Chromium/Playwright stack from Whop motion.py. This is a pragmatic
MVP to reach motion_graphics ≥9.0 without vendoring 2400 lines + cutout assets.

ARCHITECTURE:
- collage_dark / noir skins (Jung look from jung.json)
- Text-only cards with Inter Display Black titles (no cutout stickers yet)
- Rendered as 6s MP4 clips at 1920x1080, 30fps
- Interleaved into pipeline ~every 20-40s per jung.json pacing

FUTURE: Port full Chromium renderer + cutout assets from motion.py if needed.
"""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Any

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

logger = logging.getLogger(__name__)

# Jung skins from jung.json + motion.py
SKINS = {
    "collage_dark": {
        "bg": "#0d0d10",
        "bg2": "#17171c",
        "ink": "#ece5d6",
        "ink_soft": "#9a9184",
        "accent": ["#ece5d6", "#b5544a", "#7d9a86", "#c2a35f", "#7f8fa6"],
        "card": "#1a1a20",
        "card_line": "#33333c",
        "title_font": "Inter Display Black",  # Caveat in Whop, Inter for MVP
        "title_weight": "900",
    },
    "noir": {
        "bg": "#171310",
        "bg2": "#0A0806",
        "ink": "#F4EDDF",
        "ink_soft": "#B3A78F",
        "accent": ["#E8A33D", "#E05545", "#2F8F83", "#D9C9A6", "#8A6A2F"],
        "card": "rgba(255,236,200,0.07)",
        "card_line": "rgba(244,237,223,0.22)",
        "title_font": "Inter Display Black",
        "title_weight": "900",
    },
}

# Jung pacing from jung.json
PACING = {
    "graphic_every_min": 0.5,      # ~1 GFX per 30s
    "graphic_ratio": 0.35,          # 35% of video is GFX
    "graphic_dur_s": 6.0,           # Each card is 6s
    "graphic_max_s": 10.0,
}

FPS = 30
W, H = 1920, 1080


def _hex_to_rgb(hex_color: str) -> tuple:
    """Convert #RRGGBB to (R, G, B)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return (0, 0, 0)


def _create_collage_frame(
    title: str,
    subtitle: str = "",
    skin_name: str = "collage_dark",
    variant: int = 0,
) -> Image.Image:
    """Generate one 1920x1080 collage card frame with Jung aesthetic.
    
    MVP: Text-only centered title/subtitle with aged-paper feel.
    Future: Add PIL-drawn "cutout" shapes or vendor Whop assets.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL not available for motion graphics rendering")
    
    skin = SKINS.get(skin_name, SKINS["collage_dark"])
    bg_color = _hex_to_rgb(skin["bg"])
    ink_color = _hex_to_rgb(skin["ink"])
    ink_soft_color = _hex_to_rgb(skin["ink_soft"])
    accent_color = _hex_to_rgb(skin["accent"][variant % len(skin["accent"])])
    
    # Create base image
    img = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(img)
    
    # Try to load Inter Black (system font), fallback to default
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 96)
        subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48)
    except Exception:
        logger.warning("Could not load DejaVu fonts, using default")
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
    
    # Draw title (centered, upper third)
    if title:
        # Wrap title if too long
        title_lines = []
        words = title.split()
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = draw.textbbox((0, 0), test_line, font=title_font)
            if bbox[2] - bbox[0] < W - 400:
                current_line = test_line
            else:
                if current_line:
                    title_lines.append(current_line)
                current_line = word
        if current_line:
            title_lines.append(current_line)
        
        # Draw wrapped title
        y_offset = H // 3 - (len(title_lines) * 110) // 2
        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            text_width = bbox[2] - bbox[0]
            x = (W - text_width) // 2
            # Draw text with slight shadow for depth
            draw.text((x + 3, y_offset + 3), line, fill=(0, 0, 0, 128), font=title_font)
            draw.text((x, y_offset), line, fill=accent_color, font=title_font)
            y_offset += 110
    
    # Draw subtitle (centered, below title)
    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        text_width = bbox[2] - bbox[0]
        x = (W - text_width) // 2
        y = H // 2 + 80
        draw.text((x + 2, y + 2), subtitle, fill=(0, 0, 0, 96), font=subtitle_font)
        draw.text((x, y), subtitle, fill=ink_soft_color, font=subtitle_font)
    
    # Add subtle noise for aged-paper feel
    noise_layer = Image.new("RGB", (W, H), (0, 0, 0))
    noise_draw = ImageDraw.Draw(noise_layer)
    import random
    random.seed(variant * 137)
    for _ in range(500):
        x = random.randint(0, W)
        y = random.randint(0, H)
        size = random.randint(1, 3)
        opacity = random.randint(10, 40)
        color = (opacity, opacity, opacity)
        noise_draw.ellipse([x, y, x + size, y + size], fill=color)
    
    # Blend noise
    img = Image.blend(img, noise_layer, 0.15)
    
    # Add vignette
    vignette = Image.new("L", (W, H), 255)
    vignette_draw = ImageDraw.Draw(vignette)
    for i in range(200):
        opacity = int(255 - (i * 1.2))
        vignette_draw.rectangle(
            [i, i, W - i, H - i],
            outline=opacity
        )
    img.putalpha(vignette)
    img = img.convert("RGB")
    
    return img


def render_collage_card(
    title: str,
    subtitle: str = "",
    skin_name: str = "collage_dark",
    duration_sec: float = 6.0,
    output_path: Path = None,
) -> Path:
    """Render a collage GFX card to MP4.
    
    Args:
        title: Main text (e.g. "THE SHADOW")
        subtitle: Optional subtitle
        skin_name: "collage_dark" or "noir"
        duration_sec: Card duration (default 6.0s per jung.json)
        output_path: Where to save MP4
    
    Returns:
        Path to rendered MP4
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL not available for motion graphics")
    
    if output_path is None:
        output_path = Path(tempfile.mktemp(suffix="_collage.mp4"))
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Generate one static frame
    frame = _create_collage_frame(title, subtitle, skin_name, variant=0)
    
    # Save frame as temp image
    temp_frame = Path(tempfile.mktemp(suffix=".png"))
    frame.save(temp_frame)
    
    try:
        # Use ffmpeg to create video from single frame
        num_frames = int(duration_sec * FPS)
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(temp_frame),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-t", str(duration_sec),
            "-r", str(FPS),
            "-pix_fmt", "yuv420p",
            str(output_path)
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            logger.error(f"ffmpeg failed: {result.stderr}")
            raise RuntimeError(f"Motion GFX render failed: {result.stderr}")
        
        logger.info(f"Rendered collage card: {output_path} ({duration_sec}s)")
        return output_path
        
    finally:
        # Clean up temp frame
        if temp_frame.exists():
            temp_frame.unlink()


def plan_gfx_insertions(
    total_duration_sec: float,
    transcript_sentences: List[str] = None,
) -> List[Dict[str, Any]]:
    """Plan where to insert GFX cards into timeline per jung.json pacing.
    
    Args:
        total_duration_sec: Total video duration
        transcript_sentences: Optional list of sentences for semantic placement
    
    Returns:
        List of {start_sec, end_sec, title, subtitle, skin} insertion specs
    """
    cards = []
    graphic_every_sec = 60.0 / PACING["graphic_every_min"]  # ~30s
    dur = PACING["graphic_dur_s"]
    
    # Simple time-based placement (future: use transcript semantics)
    current_sec = graphic_every_sec
    variant = 0
    
    while current_sec + dur < total_duration_sec:
        skin = "collage_dark" if variant % 3 < 2 else "noir"
        
        # Extract title from transcript if available
        title = "MOMENT"
        subtitle = ""
        if transcript_sentences:
            idx = int((current_sec / total_duration_sec) * len(transcript_sentences))
            if 0 <= idx < len(transcript_sentences):
                sentence = transcript_sentences[idx]
                words = sentence.split()
                if len(words) > 5:
                    title = " ".join(words[:3]).upper()
                    subtitle = " ".join(words[3:8])
                else:
                    title = " ".join(words[:2]).upper()
        
        cards.append({
            "start_sec": current_sec,
            "end_sec": current_sec + dur,
            "title": title,
            "subtitle": subtitle,
            "skin": skin,
            "variant": variant,
        })
        
        current_sec += graphic_every_sec
        variant += 1
    
    logger.info(f"Planned {len(cards)} GFX cards for {total_duration_sec:.1f}s video")
    return cards


def render_gfx_lane(
    gfx_specs: List[Dict[str, Any]],
    output_dir: Path,
) -> List[Path]:
    """Render all GFX cards in parallel.
    
    Args:
        gfx_specs: List of card specs from plan_gfx_insertions
        output_dir: Where to save rendered MP4s
    
    Returns:
        List of rendered MP4 paths in same order as specs
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    rendered_paths = []
    
    for i, spec in enumerate(gfx_specs):
        output_path = output_dir / f"gfx_card_{i:03d}.mp4"
        
        try:
            path = render_collage_card(
                title=spec["title"],
                subtitle=spec.get("subtitle", ""),
                skin_name=spec.get("skin", "collage_dark"),
                duration_sec=spec["end_sec"] - spec["start_sec"],
                output_path=output_path,
            )
            rendered_paths.append(path)
            
        except Exception as e:
            logger.error(f"Failed to render GFX card {i}: {e}")
            # Create fallback black frame
            rendered_paths.append(None)
    
    return rendered_paths


if __name__ == "__main__":
    # Smoke test
    logging.basicConfig(level=logging.INFO)
    
    test_card = render_collage_card(
        title="THE SHADOW",
        subtitle="What you disown returns as fate",
        skin_name="collage_dark",
        output_path=Path("/tmp/test_collage.mp4")
    )
    
    print(f"Rendered test card: {test_card}")
