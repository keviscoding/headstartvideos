#!/usr/bin/env python3
"""frontier_motion_graphics.py — Collage / cutout motion GFX for CR Frontier.

Whop motion.py renders collage/scatter/pillars via Playwright. This module is a
Pillow + ffmpeg port that pastes **real Whop cutout PNGs** (frontier-gfx-kit/
or assets/whop-gfx/) onto collage_dark / noir grounds — not text-only cards.
"""
from __future__ import annotations

import logging
import math
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

logger = logging.getLogger(__name__)

SKINS = {
    "collage_dark": {
        "bg": "#0d0d10",
        "ink": "#ece5d6",
        "ink_soft": "#9a9184",
        "warn": "#b5544a",
        "accent": ["#ece5d6", "#b5544a", "#7d9a86", "#c2a35f", "#7f8fa6"],
    },
    "noir": {
        "bg": "#171310",
        "ink": "#F4EDDF",
        "ink_soft": "#B3A78F",
        "warn": "#E05545",
        "accent": ["#E8A33D", "#E05545", "#2F8F83", "#D9C9A6", "#8A6A2F"],
    },
}

PACING = {
    "graphic_every_min": 0.5,
    "graphic_ratio": 0.35,
    "graphic_dur_s": 5.0,
    "graphic_max_s": 8.0,
}

FPS = 24
W, H = 1920, 1080
TAPE = (210, 190, 140)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _assets_root() -> Path:
    for root in (
        _repo_root() / "frontier-gfx-kit",
        _repo_root() / "assets" / "whop-gfx",
        _repo_root() / "assets",
    ):
        if (root / "cutouts").is_dir():
            return root
    return _repo_root() / "frontier-gfx-kit"


def list_cutouts() -> List[Path]:
    d = _assets_root() / "cutouts"
    if not d.is_dir():
        return []
    return sorted(d.glob("*.png"))


def _ffmpeg() -> str:
    for c in (
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "ffmpeg",
    ):
        if c == "ffmpeg":
            return shutil.which("ffmpeg") or "ffmpeg"
        if Path(c).exists():
            return c
    return "ffmpeg"


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return (0, 0, 0)


def _load_font(size: int, cursive: bool = False):
    fonts_dir = _assets_root() / "fonts"
    candidates = []
    if cursive:
        candidates += [
            Path("/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"),
            Path("/System/Library/Fonts/Supplemental/Chalkboard.ttc"),
            Path("/Library/Fonts/Comic Sans MS.ttf"),
        ]
    candidates += [
        fonts_dir / "InterDisplay-Black.ttf",
        fonts_dir / "Inter-ExtraBold.ttf",
        fonts_dir / "Inter-Black.ttf",
        fonts_dir / "Inter-Bold.ttf",
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for fp in candidates:
        try:
            if fp.exists():
                return ImageFont.truetype(str(fp), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _paper_card(w: int, h: int, rng: random.Random) -> Image.Image:
    tex_path = _assets_root() / "textures" / "paper_earth.jpg"
    if tex_path.exists():
        tex = Image.open(tex_path).convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
    else:
        base = (232, 220, 186)
        tex = Image.new("RGB", (w, h), base)
        px = tex.load()
        for y in range(0, h, 3):
            for x in range(0, w, 3):
                n = rng.randint(-16, 10)
                px[x, y] = (
                    max(0, min(255, base[0] + n)),
                    max(0, min(255, base[1] + n - 2)),
                    max(0, min(255, base[2] + n - 6)),
                )
    out = tex.convert("RGBA")
    od = ImageDraw.Draw(out)
    od.rectangle([0, 0, w - 1, h - 1], outline=(90, 70, 40, 100), width=3)
    return out


def _make_sticker(cutout: Path, target_h: int, rng: random.Random, with_card: bool = True) -> Image.Image:
    img = Image.open(cutout).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    ar = img.width / max(1, img.height)
    th = target_h
    tw = max(40, int(th * ar))
    img = img.resize((tw, th), Image.Resampling.LANCZOS)
    shadow = Image.new("RGBA", (tw + 40, th + 40), (0, 0, 0, 0))
    sh = img.split()[-1].point(lambda p: int(p * 0.45))
    shadow.paste((0, 0, 0, 180), (20, 24), sh)
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    sticker = Image.new("RGBA", (tw + 40, th + 40), (0, 0, 0, 0))
    sticker = Image.alpha_composite(sticker, shadow)
    sticker.paste(img, (20, 12), img)
    if not with_card:
        return sticker
    pad = int(th * 0.14)
    cw, ch = tw + pad * 2 + 40, th + pad * 2 + 40
    card = _paper_card(cw, ch, rng)
    layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
    sx = (cw - sticker.width) // 2
    sy = (ch - sticker.height) // 2
    layer.paste(sticker, (sx, sy), sticker)
    return Image.alpha_composite(card, layer)


def _paste(base: Image.Image, overlay: Image.Image, xy, opacity: float = 1.0) -> Image.Image:
    if opacity < 0.99:
        a = overlay.split()[-1].point(lambda p: int(p * opacity))
        overlay = overlay.copy()
        overlay.putalpha(a)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(overlay, xy, overlay)
    return Image.alpha_composite(base, layer)


def _rotate(im: Image.Image, angle: float) -> Image.Image:
    return im.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)


def _wrap(draw, text, font, max_w):
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _text_block(draw, text, xy, font, fill, max_w):
    lines = _wrap(draw, text, font, max_w)
    x, y = xy
    line_h = int(getattr(font, "size", 40) * 1.15)
    for i, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        lx = x - lw / 2
        draw.text((lx + 2, y + i * line_h + 2), line, font=font, fill=(0, 0, 0, 160))
        draw.text((lx, y + i * line_h), line, font=font, fill=fill)


def _ground(skin: dict, rng: random.Random) -> Image.Image:
    tex = _assets_root() / "textures" / "background_dark.png"
    bg = _hex_to_rgb(skin["bg"])
    if tex.exists():
        g = Image.open(tex).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
        g = ImageEnhance.Brightness(g).enhance(0.55)
        g = ImageEnhance.Contrast(g).enhance(1.15)
    else:
        g = Image.new("RGB", (W, H), bg)
    grain = Image.new("RGB", (W, H), (0, 0, 0))
    gp = grain.load()
    for y in range(0, H, 4):
        for x in range(0, W, 4):
            n = rng.randint(0, 18)
            gp[x, y] = (n, n, n)
    grain = grain.resize((W, H), Image.Resampling.BILINEAR)
    g = Image.blend(g, grain, 0.08)
    return g.convert("RGBA")


def _ease_out_back(t: float) -> float:
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _seg(t, start, dur):
    if dur <= 0:
        return 1.0
    return max(0.0, min(1.0, (t - start) / dur))


def compose_collage_frame(
    title: str,
    subtitle: str = "",
    items: Optional[List[Dict[str, str]]] = None,
    cutout_paths: Optional[List[Path]] = None,
    skin_name: str = "collage_dark",
    template: str = "collage",
    t: float = 2.5,
    seed: int = 42,
) -> Image.Image:
    """Compose one collage/scatter/pillars frame with real Whop cutouts."""
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL not available for motion graphics rendering")

    skin = SKINS.get(skin_name, SKINS["collage_dark"])
    ink = _hex_to_rgb(skin["ink"])
    ink_soft = _hex_to_rgb(skin["ink_soft"])
    warn = _hex_to_rgb(skin.get("warn", "#b5544a"))
    rng = random.Random(seed)
    base = _ground(skin, rng)

    cuts = list(cutout_paths or [])
    if not cuts:
        lib = list_cutouts()
        if not lib:
            raise FileNotFoundError(
                "No cutouts in frontier-gfx-kit/cutouts — unpack Whop kit first"
            )
        picks = lib[:]
        rng.shuffle(picks)
        need = 8 if template == "scatter" else (3 if template == "pillars" else 3)
        cuts = picks[:need]

    items = items or []
    if not items:
        items = [
            {"label": "the shadow", "text": "what you refuse"},
            {"label": "the persona", "text": "the mask that stuck"},
            {"label": "the self", "text": "the whole you"},
        ]

    title_font = _load_font(68, cursive=True)
    hand_font = _load_font(42, cursive=True)
    soft_font = _load_font(32, cursive=True)

    p_title = _ease_out_back(_seg(t, 0.12, 0.55))
    if p_title > 0.01 and title:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(layer)
        _text_block(td, title, (W // 2, int(70 + (1 - p_title) * -18)), title_font, (*ink, int(255 * p_title)), W - 320)
        base = Image.alpha_composite(base, layer)

    if template == "scatter":
        N = min(8, len(cuts))
        for i in range(N):
            a = (i / max(1, N)) * math.tau + 0.4
            rx, ry = W * 0.40, H * 0.36
            sw = 112 + (i % 3) * 26
            delay = 0.25 + i * 0.14
            q = _ease_out_back(_seg(t, delay, 0.62))
            settle = _seg(t, delay + 0.7, 0.8)
            bob = math.sin(t * 1.25 + delay * 3) * 4 * settle
            dx = (1 - q) * (190 if math.cos(a) >= 0 else -190)
            x = int(W / 2 + math.cos(a) * rx - sw / 2 + dx)
            y = int(H / 2 + math.sin(a) * ry - sw / 2 + bob)
            sticker = _rotate(_make_sticker(cuts[i], sw, rng, True), (1 if i % 2 else -1) * (4 + i * 1.6) * q)
            op = _seg(t, delay, 0.28)
            if op > 0.01:
                base = _paste(base, sticker, (x - (sticker.width - sw) // 2, y - (sticker.height - sw) // 2), op)
        if t > 1.4:
            ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rd = ImageDraw.Draw(ring)
            op = int(200 * min(1.0, (t - 1.4) / 0.5))
            rd.ellipse([W * 0.28, H * 0.32, W * 0.72, H * 0.68], outline=(*warn, op), width=6)
            base = Image.alpha_composite(base, ring)
        if subtitle:
            sp = _seg(t, 2.0, 0.5)
            sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sl)
            _text_block(sd, subtitle, (W // 2, H - 140), soft_font, (*ink_soft, int(255 * sp)), W - 520)
            base = Image.alpha_composite(base, sl)
        return base.convert("RGB").convert("RGBA")

    if template == "pillars":
        n = max(1, min(3, len(cuts), len(items) or 3))
        cuts, items = cuts[:n], (items + [{"label": "", "text": ""}] * n)[:n]
        ph = int(H * 0.40)
        sample = Image.open(cuts[0]).convert("RGBA")
        ar = sample.width / max(1, sample.height)
        pw = max(120, min(280, int(ph / max(0.3, ar * 0.55 if ar > 1.5 else ar))))
        gap = int(W * 0.075)
        tw = n * pw + (n - 1) * gap
        x0 = (W - tw) // 2
        base_y = int(H * 0.70)
        fl = _seg(t, 0.3, 0.5)
        fl_l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(fl_l).rectangle(
            [x0 - 110, base_y, x0 - 110 + int((tw + 220) * fl), base_y + 6],
            fill=(*ink, int(180 * fl)),
        )
        base = Image.alpha_composite(base, fl_l)
        for i in range(n):
            d = 0.55 + i * 0.3
            p = _ease_out_back(_seg(t, d, 0.85))
            sway = math.sin(t * 0.8 + i) * 0.25 * _seg(t, d + 0.9, 0.6)
            col = _rotate(_make_sticker(cuts[i], ph, rng, False).resize((pw, ph), Image.Resampling.LANCZOS), sway)
            dy = int((1 - p) * ph * 0.55)
            x = x0 + i * (pw + gap)
            base = _paste(base, col, (x - (col.width - pw) // 2, base_y - ph + dy), _seg(t, d, 0.3))
            lp = _seg(t, d + 0.5, 0.45)
            if lp > 0.01 and items[i].get("label"):
                ll = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ld = ImageDraw.Draw(ll)
                cx = x + pw // 2
                _text_block(ld, items[i]["label"], (cx, base_y + 30), hand_font, (*ink, int(255 * lp)), min(380, pw + gap - 24))
                if items[i].get("text"):
                    _text_block(ld, items[i]["text"], (cx, base_y + 92), soft_font, (*ink_soft, int(255 * lp)), min(380, pw + gap - 24))
                base = Image.alpha_composite(base, ll)
        return base

    # collage default
    n = max(2, min(4, len(cuts), len(items) or 3))
    cuts = cuts[:n]
    items = (items + [{"label": "", "text": ""}] * n)[:n]
    col_w = {1: 760, 2: 520, 3: 400}.get(n, 320)
    gap = 120 if n <= 2 else 60
    tw = n * col_w + (n - 1) * gap
    x0 = (W - tw) // 2
    y = int(H * 0.30)
    target_h = {1: 420, 2: 380, 3: 300}.get(n, 260)
    rots = [-4.5, 3.2, -2.4, 4.1]
    dirs = ["down", "left", "up", "right"]
    for i in range(n):
        delay = 0.5 + i * 0.34
        q = _ease_out_back(_seg(t, delay, 0.62))
        settle = _seg(t, delay + 0.7, 0.8)
        bob = math.sin(t * 1.25 + delay * 3) * 4 * settle
        direction = dirs[i % 4]
        dx = dy = 0
        if direction == "down":
            dy = int((1 - q) * 150)
        elif direction == "up":
            dy = int(-(1 - q) * 150)
        elif direction == "left":
            dx = int(-(1 - q) * 190)
        else:
            dx = int((1 - q) * 190)
        sticker = _rotate(_make_sticker(cuts[i], target_h, rng, True), rots[i] * q)
        cx = x0 + i * (col_w + gap) + col_w // 2
        op = _seg(t, delay, 0.28)
        if op > 0.01:
            base = _paste(base, sticker, (int(cx - sticker.width / 2 + dx), int(y + dy + bob)), op)
        if op > 0.3:
            tape_l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            td = ImageDraw.Draw(tape_l)
            twape = int(sticker.width * 0.34)
            td.rectangle(
                [cx + dx - twape // 2, y - 10 + dy - 18, cx + dx + twape // 2, y - 10 + dy + 18],
                fill=(*TAPE, 210),
            )
            base = Image.alpha_composite(base, tape_l)
        ly = y + target_h + int(target_h * 0.28) + 40
        lp = _seg(t, delay + 0.45, 0.45)
        if lp > 0.01:
            ll = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(ll)
            if items[i].get("label"):
                _text_block(ld, items[i]["label"], (cx, ly), hand_font, (*ink, int(255 * lp)), col_w)
            if items[i].get("text"):
                _text_block(
                    ld,
                    items[i]["text"],
                    (cx, ly + 58),
                    soft_font,
                    (*ink_soft, int(255 * lp)),
                    col_w,
                )
            base = Image.alpha_composite(base, ll)

    if subtitle:
        sp = _seg(t, 1.5, 0.5)
        if sp > 0.01:
            sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sl)
            _text_block(sd, subtitle, (W // 2, H - 90), soft_font, (*warn, int(255 * sp)), W - 400)
            base = Image.alpha_composite(base, sl)
    return base


def render_collage_card(
    title: str,
    subtitle: str = "",
    skin_name: str = "collage_dark",
    duration_sec: float = 5.0,
    output_path: Path = None,
    template: str = "collage",
    items: Optional[List[Dict[str, str]]] = None,
    cutout_paths: Optional[List[Path]] = None,
    seed: int = 42,
) -> Path:
    """Render an animated collage/scatter/pillars card (real cutouts) to MP4."""
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL not available for motion graphics")
    if output_path is None:
        output_path = Path(tempfile.mktemp(suffix="_collage.mp4"))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    duration_sec = max(3.0, min(8.0, float(duration_sec)))
    n_frames = max(2, int(round(duration_sec * FPS)))
    tmp = Path(tempfile.mkdtemp(prefix="frontier_gfx_"))
    try:
        for k in range(n_frames):
            t = k / FPS
            frame = compose_collage_frame(
                title=title,
                subtitle=subtitle,
                items=items,
                cutout_paths=cutout_paths,
                skin_name=skin_name,
                template=template,
                t=t,
                seed=seed,
            )
            zoom = 1.0 + 0.03 * (t / duration_sec)
            zw, zh = int(W * zoom), int(H * zoom)
            frame = frame.convert("RGB").resize((zw, zh), Image.Resampling.LANCZOS)
            left, top = (zw - W) // 2, (zh - H) // 2
            frame.crop((left, top, left + W, top + H)).save(tmp / f"f_{k:05d}.jpg", quality=92)

        cmd = [
            _ffmpeg(), "-y",
            "-framerate", str(FPS),
            "-i", str(tmp / "f_%05d.jpg"),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            str(output_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Motion GFX encode failed: {(r.stderr or '')[-1200:]}")
        logger.info("Rendered collage card with cutouts: %s (%.1fs)", output_path, duration_sec)
        return output_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def plan_gfx_insertions(
    total_duration_sec: float,
    transcript_sentences: List[str] = None,
) -> List[Dict[str, Any]]:
    """Plan 2–4 mid-timeline collage cards (denser on short smokes)."""
    cards = []
    dur = min(PACING["graphic_dur_s"], PACING["graphic_max_s"])
    # Short videos: force 3–4 cards so motion_graphics actually reads on screen
    if total_duration_sec < 90:
        n = 4 if total_duration_sec >= 40 else 3
    else:
        n = max(2, int(total_duration_sec * PACING["graphic_ratio"] / dur))
        n = min(n, 12)

    templates = ["collage", "scatter", "pillars", "collage"]
    default_items = [
        [
            {"label": "the hollow", "text": "where silence sits"},
            {"label": "the ridge", "text": "what you climb alone"},
            {"label": "the hearth", "text": "warmth you refuse"},
        ],
        [],
        [
            {"label": "attention", "text": "stay with it"},
            {"label": "honesty", "text": "name the cost"},
            {"label": "return", "text": "come back daily"},
        ],
        [
            {"label": "persona", "text": "who they need"},
            {"label": "shadow", "text": "who you bury"},
        ],
    ]
    default_titles = [
        ("three things the body already knows", "before the mind invents a story"),
        ("it is not a breakdown", "it is a process asking to be named"),
        ("what this rests on", ""),
        ("the mask that stuck", "take it off without burning the room"),
    ]

    for i in range(n):
        center = total_duration_sec * (0.14 + 0.72 * (i + 0.5) / n)
        start = max(0.5, center - dur / 2)
        end = min(total_duration_sec - 0.2, start + dur)
        start = max(0.4, end - dur)
        skin = "collage_dark" if i % 3 < 2 else "noir"
        title, subtitle = default_titles[i % len(default_titles)]
        if transcript_sentences:
            idx = int((center / total_duration_sec) * len(transcript_sentences))
            idx = max(0, min(idx, len(transcript_sentences) - 1))
            words = transcript_sentences[idx].split()
            if len(words) >= 4:
                title = " ".join(words[:5]).lower()
        cards.append({
            "start_sec": start,
            "end_sec": end,
            "title": title,
            "subtitle": subtitle,
            "skin": skin,
            "variant": i,
            "template": templates[i % len(templates)],
            "items": default_items[i % len(default_items)],
            "seed": 11 + i * 11,
        })
    logger.info("Planned %d GFX cards for %.1fs video", len(cards), total_duration_sec)
    return cards


def render_gfx_lane(
    gfx_specs: List[Dict[str, Any]],
    output_dir: Path,
) -> List[Path]:
    """Render all GFX cards with real cutouts."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    n_cut = len(list_cutouts())
    logger.info("Cutout library: %d PNGs under %s", n_cut, _assets_root() / "cutouts")
    for i, spec in enumerate(gfx_specs):
        out = output_dir / f"gfx_card_{i:03d}.mp4"
        try:
            path = render_collage_card(
                title=spec["title"],
                subtitle=spec.get("subtitle", ""),
                skin_name=spec.get("skin", "collage_dark"),
                duration_sec=spec["end_sec"] - spec["start_sec"],
                output_path=out,
                template=spec.get("template", "collage"),
                items=spec.get("items"),
                seed=int(spec.get("seed", 40 + i)),
            )
            rendered.append(path)
        except Exception as e:
            logger.error("Failed to render GFX card %d: %s", i, e)
            rendered.append(None)
    return rendered


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("cutouts", len(list_cutouts()))
    test = render_collage_card(
        title="three things the body already knows",
        subtitle="before the mind invents a story",
        skin_name="collage_dark",
        template="collage",
        duration_sec=4.0,
        output_path=Path("/tmp/test_collage_cutouts.mp4"),
        items=[
            {"label": "the hollow", "text": "where silence sits"},
            {"label": "the ridge", "text": "what you climb alone"},
            {"label": "the hearth", "text": "warmth you refuse"},
        ],
    )
    print("Rendered", test, test.stat().st_size)
