#!/usr/bin/env python3
"""frontier_motion_graphics.py — Collage / cutout motion GFX for CR Frontier.

Whop motion.py renders collage/scatter/pillars/opener/photonote via Playwright.
This module is a Pillow + ffmpeg port that pastes **real Whop cutout PNGs**
(frontier-gfx-kit/ or assets/whop-gfx/) onto collage_dark / noir grounds —
not text-only cards.
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
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

logger = logging.getLogger(__name__)

SKINS = {
    "collage_dark": {
        "bg": "#0d0d10",
        "ink": "#ece5d6",          # cream
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

# Jung pacing: ~35% GFX ratio. Short smokes use more cards / shorter holds.
PACING = {
    "graphic_every_min": 0.5,
    # Whop Dissect: designed GFX ≈ 20–25% runtime (v5 35% caused bed rush)
    "graphic_ratio": 0.22,
    "graphic_dur_s": 4.2,   # v10: fewer longer cards → mean shot back to 5–9
    "graphic_max_s": 4.5,
    # After kinetic opener (~8s first cut): first GFX ~10s (was 14 after 16.9 lock)
    "first_gfx_min_sec": 10.0,
    "min_gap_between_gfx_sec": 9.0,
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



# Keyword → cutout stems (script-matched stickers; per-style packs may replace list)
CUTOUT_KEYWORDS: Dict[str, List[str]] = {
    "dark": ["vintage_moon", "vintage moon on hand", "vintage_eye", "vintage_cloud"],
    "night": ["vintage_moon", "vintage moon on hand", "vintage_alarm"],
    "moon": ["vintage_moon", "vintage moon on hand"],
    "forest": ["vintage_tiger", "vintage_collage_bird_dove", "sisyphus_pushing_boulder_sketch", "vintage_moon", "ancient_pilar"],
    "woods": ["vintage_tiger", "vintage_collage_bird_dove", "sisyphus_pushing_boulder_sketch"],
    "mountain": ["sisyphus_pushing_boulder_sketch", "ancient_pilar", "vintage_cloud"],
    "appalachian": ["sisyphus_pushing_boulder_sketch", "vintage_cloud", "ancient_pilar"],
    "trail": ["sisyphus_pushing_boulder_sketch", "vintage_boot", "vintage_key"],
    "map": ["vintage_open_book", "vintage_hand writing", "vintage_information"],
    "maps": ["vintage_open_book", "vintage_hand writing", "vintage_information"],
    "call": ["vintage_man_talking_microfon", "vintage_angry_woman_yelling", "vintage_man_mad"],
    "calls": ["vintage_man_talking_microfon", "vintage_angry_woman_yelling", "vintage_man_mad"],
    "name": ["vintage_open_book", "vintage_information", "vintage_hand writing"],
    "answer": ["vintage_lock", "vintage_key", "vintage_man_talking_microfon"],
    "voice": ["vintage_man_talking_microfon", "vintage_angry_woman_yelling"],
    "learn": ["vintage_open_book", "bible", "vintage_stack_of_books", "vintage_brain"],
    "learns": ["vintage_open_book", "bible", "vintage_stack_of_books", "vintage_brain"],
    "alone": ["vintage_alarm", "sisyphus_pushing_boulder_sketch", "vintage_heartbroken"],
    "light": ["vintage_moon", "vintage_eye", "vintage_star"],
    "listen": ["vintage_man_talking_microfon", "vintage_ear", "vintage_eye"],
    "listening": ["vintage_man_talking_microfon", "vintage_eye"],
    "friend": ["vintage_heart", "vintage_couple_kissing", "vintage_heartbroken"],
    "body": ["vintage_heart", "vintage_brain", "vintage_eye"],
    "brain": ["vintage_brain", "vintage_eye"],
    "turn": ["vintage_recycle", "vintage_key"],
    "speak": ["vintage_man_talking_microfon", "vintage_angry_woman_yelling"],
    "speaks": ["vintage_man_talking_microfon", "vintage_angry_woman_yelling"],
    "soft": ["vintage_cloud", "vintage_dove", "vintage_heart"],
    "familiar": ["vintage_heart", "vintage_key", "vintage_home"],
    "rule": ["vintage_information", "bible", "vintage_open_book", "vintage_lock"],
    "print": ["vintage_hand writing", "vintage_open_book"],
    "walk": ["sisyphus_pushing_boulder_sketch", "vintage_boot"],
    "walking": ["sisyphus_pushing_boulder_sketch"],
    "look": ["vintage_eye", "vintage_magnifying_glass_hand"],
    "back": ["vintage_recycle", "vintage_eye"],
    "shadow": ["vintage_moon", "vintage_eye", "vintage_man_mad"],
    "mask": ["vintage_lock", "vintage_key", "vintage_eye"],
    "persona": ["vintage_man_mad", "vintage_angry_woman_yelling"],
    "self": ["vintage_mirror", "vintage_eye", "vintage_heart"],
    "jesus": ["jesus", "vintage_jesus", "bible", "jesus and disciples drawing", "rising jesus drawing"],
    "bible": ["bible", "vintage_open_book", "vintage_church"],
    "church": ["vintage_church", "bible", "ancient_pilar"],
    "fear": ["vintage_man_mad", "vintage_angry_woman_yelling", "vintage_eye"],
    "wait": ["vintage_alarm", "vintage_moon"],
    "waits": ["vintage_alarm", "vintage_moon"],
    "old": ["vintage_alarm", "ancient_pilar", "vintage_open_book"],
    "timer": ["vintage_alarm"],
    "timers": ["vintage_alarm"],
}


def pick_cutouts_for_text(
    text: str,
    need: int = 3,
    seed: int = 42,
    pool: Optional[List[Path]] = None,
) -> List[Path]:
    """Prefer cutouts whose filenames match keywords in title/script text.

    Falls back to seeded shuffle of the remainder so we always fill `need`.
    """
    lib = list(pool) if pool is not None else list_cutouts()
    if not lib:
        return []
    rng = random.Random(seed)
    words = set()
    for raw in (text or "").lower().replace("-", " ").replace("'", " ").split():
        w = "".join(ch for ch in raw if ch.isalnum())
        if len(w) >= 3:
            words.add(w)
            if w.endswith("s") and len(w) > 4:
                words.add(w[:-1])
    scored: List[tuple] = []
    for p in lib:
        stem = p.stem.lower()
        score = 0
        matched = []
        for w in words:
            stems = CUTOUT_KEYWORDS.get(w, [])
            for s in stems:
                if s.lower() in stem or stem in s.lower():
                    score += 3
                    matched.append(w)
                    break
            # direct filename token hit
            if w in stem.replace("_", " "):
                score += 2
                matched.append(w)
        scored.append((score, rng.random(), p, matched))
    scored.sort(key=lambda t: (-t[0], t[1]))
    picked: List[Path] = []
    used = set()
    for score, _, p, matched in scored:
        if p in used:
            continue
        if score > 0 or len(picked) < need:
            picked.append(p)
            used.add(p)
            if score > 0:
                logger.info("cutout match %s ← %s (score=%d)", p.name, matched, score)
        if len(picked) >= need:
            break
    # If still short, fill randomly from unused
    if len(picked) < need:
        rest = [p for p in lib if p not in used]
        rng.shuffle(rest)
        picked.extend(rest[: max(0, need - len(picked))])
    return picked[:need]



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
    """Prefer Caveat Bold (Jung collage_dark), then system handwritten."""
    fonts_dir = _assets_root() / "fonts"
    candidates = []
    if cursive:
        candidates += [
            fonts_dir / "Caveat-Bold.ttf",
            fonts_dir / "Caveat-Bold-static.ttf",
            fonts_dir / "Caveat[wght].ttf",
            fonts_dir / "PermanentMarker-Regular.ttf",
            Path("/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"),
            Path("/System/Library/Fonts/Supplemental/ChalkboardSE.ttc"),
            Path("/System/Library/Fonts/Supplemental/Chalkduster.ttf"),
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
            if fp.exists() and fp.stat().st_size > 1000:
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


def _photo_frame(src: Path, pw: int, ph: int, rng: random.Random, gray: float = 0.4) -> Image.Image:
    """Taped aged-paper photo frame (opener / photonote)."""
    img = Image.open(src).convert("RGB")
    img = ImageOps.fit(img, (pw - 32, ph - 32), Image.Resampling.LANCZOS)
    if gray > 0:
        g = ImageOps.grayscale(img).convert("RGB")
        img = Image.blend(img, g, gray)
        img = ImageEnhance.Contrast(img).enhance(1.08)
    pad = 16
    card = _paper_card(pw, ph, rng)
    layer = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    photo = img.convert("RGBA")
    layer.paste(photo, (pad, pad), photo)
    framed = Image.alpha_composite(card, layer)
    # drop shadow
    shadow = Image.new("RGBA", (pw + 40, ph + 40), (0, 0, 0, 0))
    sh = Image.new("RGBA", (pw, ph), (0, 0, 0, 160))
    shadow.paste(sh, (18, 22), sh)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    out = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
    out = Image.alpha_composite(out, shadow)
    out.paste(framed, (8, 4), framed)
    return out


def _paste(base: Image.Image, overlay: Image.Image, xy, opacity: float = 1.0) -> Image.Image:
    if opacity < 0.99:
        a = overlay.split()[-1].point(lambda p: int(p * opacity))
        overlay = overlay.copy()
        overlay.putalpha(a)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(overlay, (int(xy[0]), int(xy[1])), overlay)
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


def _text_block(draw, text, xy, font, fill, max_w, align="center"):
    lines = _wrap(draw, text, font, max_w)
    x, y = xy
    line_h = int(getattr(font, "size", 40) * 1.18)
    for i, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        if align == "left":
            lx = x
        else:
            lx = x - lw / 2
        draw.text((lx + 2, y + i * line_h + 2), line, font=font, fill=(0, 0, 0, 160))
        draw.text((lx, y + i * line_h), line, font=font, fill=fill)
    return len(lines) * line_h


def _dashed_ellipse(draw, bbox, fill, width=5, dash=18, gap=12):
    """Approximate a scribbled ring with short arcs."""
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    # walk the ellipse in dash/gap pattern
    step = 0.04
    t = 0.0
    drawing = True
    acc = 0.0
    pts = []
    while t < math.tau + step:
        x = cx + rx * math.cos(t)
        y = cy + ry * math.sin(t)
        # arc length approx
        dt_len = math.hypot(rx * math.sin(t), ry * math.cos(t)) * step
        acc += dt_len
        if drawing:
            pts.append((x, y))
            if acc >= dash:
                if len(pts) >= 2:
                    draw.line(pts, fill=fill, width=width, joint="curve")
                pts = []
                drawing = False
                acc = 0.0
        else:
            if acc >= gap:
                drawing = True
                acc = 0.0
                pts = [(x, y)]
        t += step
    if drawing and len(pts) >= 2:
        draw.line(pts, fill=fill, width=width, joint="curve")


def _draw_arrow(draw, x0, y0, x1, y1, fill, width=5):
    draw.line([(x0, y0), (x1, y1)], fill=fill, width=width)
    ang = math.atan2(y1 - y0, x1 - x0)
    ah = 22
    for da in (2.5, -2.5):
        ax = x1 - ah * math.cos(ang + da * 0.35)
        ay = y1 - ah * math.sin(ang + da * 0.35)
        draw.line([(x1, y1), (ax, ay)], fill=fill, width=width)


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
    g = Image.blend(g, grain, 0.14)
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
    image_path: Optional[Path] = None,
) -> Image.Image:
    """Compose one collage/scatter/pillars/opener/photonote frame with real cutouts."""
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
        need = {"scatter": 8, "pillars": 3, "opener": 6, "photonote": 1, "collage": 3}.get(template, 3)
        # Script-matched stickers from title + subtitle (+ items if already provided)
        label_blob = " ".join([title or "", subtitle or ""])
        if items:
            label_blob += " " + " ".join(
                str(it.get("label", "")) + " " + str(it.get("text", "")) for it in items
            )
        cuts = pick_cutouts_for_text(label_blob, need=need, seed=seed, pool=lib)

    items = items or []
    if not items:
        items = [
            {"label": "the shadow", "text": "what you refuse"},
            {"label": "the persona", "text": "the mask that stuck"},
            {"label": "the self", "text": "the whole you"},
        ]

    title_font = _load_font(72 if template in ("scatter", "opener") else 64, cursive=True)
    hand_font = _load_font(44, cursive=True)
    soft_font = _load_font(34, cursive=True)

    # ── SCATTER: title centered INSIDE scribbled ring, stickers around ──
    if template == "scatter":
        # Title first so we know its block height for centering
        p_title = _ease_out_back(_seg(t, 0.9, 0.7))
        title_block_h = 0
        if title and p_title > 0.01:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            td = ImageDraw.Draw(layer)
            # Measure wrap
            probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            lines = _wrap(probe, title, title_font, W - 640)
            line_h = int(getattr(title_font, "size", 72) * 1.18)
            title_block_h = len(lines) * line_h
            ty = int(H * 0.5 - title_block_h / 2)
            _text_block(td, title, (W // 2, ty), title_font, (*ink, int(255 * min(1.0, p_title * 1.2))), W - 640)
            # slight scale pop via opacity only (Pillow)
            base = Image.alpha_composite(base, layer)

        # Dashed scribble ring around the title
        if t > 1.4:
            ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rd = ImageDraw.Draw(ring)
            op = int(220 * min(1.0, (t - 1.4) / 0.5))
            rx = min(W * 0.30, 420)
            ry = max(110, (title_block_h or 120) * 0.85)
            _dashed_ellipse(
                rd,
                [W / 2 - rx, H / 2 - ry, W / 2 + rx, H / 2 + ry],
                (*warn, op),
                width=7,
                dash=20,
                gap=12,
            )
            base = Image.alpha_composite(base, ring)

        N = min(8, len(cuts))
        for i in range(N):
            a = (i / max(1, N)) * math.tau + 0.4
            rx, ry = W * 0.40, H * 0.36
            sw = 112 + (i % 3) * 26
            delay = 0.25 + i * 0.14
            q = _ease_out_back(_seg(t, delay, 0.48))
            settle = _seg(t, delay + 0.7, 0.8)
            bob = math.sin(t * 1.85 + delay * 2.6) * 9 * settle
            dx = (1 - q) * (240 if math.cos(a) >= 0 else -240)
            x = int(W / 2 + math.cos(a) * rx - sw / 2 + dx)
            y = int(H / 2 + math.sin(a) * ry - sw / 2 + bob)
            sticker = _rotate(_make_sticker(cuts[i], sw, rng, True), (1 if i % 2 else -1) * (4 + i * 1.6) * q)
            op = _seg(t, delay, 0.28)
            if op > 0.01:
                base = _paste(base, sticker, (x - (sticker.width - sw) // 2, y - (sticker.height - sw) // 2), op)
        if subtitle:
            sp = _seg(t, 2.0, 0.45)
            if sp > 0.01:
                sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                sd = ImageDraw.Draw(sl)
                _text_block(sd, subtitle, (W // 2, H - 120), soft_font, (*ink_soft, int(255 * sp)), W - 520)
                base = Image.alpha_composite(base, sl)
        return base

    # ── PILLARS: three columns rise from a floor line ──
    if template == "pillars":
        p_title = _ease_out_back(_seg(t, 0.12, 0.5))
        if p_title > 0.01 and title:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            td = ImageDraw.Draw(layer)
            _text_block(td, title, (W // 2, int(70 + (1 - p_title) * -16)), title_font, (*ink, int(255 * p_title)), W - 300)
            base = Image.alpha_composite(base, layer)

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
            raw = _make_sticker(cuts[i], ph, rng, False)
            # scaleY rise illusion via height lerp
            cur_h = max(20, int(ph * (0.55 + 0.45 * p)))
            col_img = raw.resize((pw, cur_h), Image.Resampling.LANCZOS)
            col = _rotate(col_img, sway)
            dy = int((1 - p) * ph * 0.55)
            x = x0 + i * (pw + gap)
            base = _paste(base, col, (x - (col.width - pw) // 2, base_y - cur_h + dy), _seg(t, d, 0.3))
            lp = _seg(t, d + 0.5, 0.45)
            if lp > 0.01 and items[i].get("label"):
                ll = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ld = ImageDraw.Draw(ll)
                cx = x + pw // 2
                _text_block(ld, items[i]["label"], (cx, base_y + 30), hand_font, (*ink, int(255 * lp)), min(380, pw + gap - 24))
                if items[i].get("text"):
                    _text_block(ld, items[i]["text"], (cx, base_y + 92), soft_font, (*ink_soft, int(255 * lp)), min(380, pw + gap - 24))
                base = Image.alpha_composite(base, ll)
        if subtitle:
            sp = _seg(t, 1.9, 0.45)
            if sp > 0.01:
                sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                sd = ImageDraw.Draw(sl)
                _text_block(sd, subtitle, (W // 2, min(H - 80, base_y + 170)), soft_font, (*warn, int(255 * sp)), W - 440)
                base = Image.alpha_composite(base, sl)
        return base

    # ── OPENER: portrait (optional) + edge stickers + title write-on ──
    if template == "opener":
        photo = Path(image_path) if image_path else None
        if photo is None or not photo.exists():
            # fall back: use a tall cutout as "portrait" stand-in is weak;
            # prefer first still-like cutout with person words, else skip photo
            photo = None

        px = py = pw = ph = 0
        if photo and photo.exists():
            pw = int(W * 0.26)
            ph = int(pw * 1.2)
            px = int(W * 0.5 - pw / 2)
            py = int(H * 0.18)
            d = 0.18
            q = _ease_out_back(_seg(t, d, 0.8))
            op = _seg(t, d, 0.35)
            if op > 0.01:
                framed = _photo_frame(photo, pw, ph, rng, gray=0.45)
                framed = _rotate(framed, -7 + 4.8 * q)
                dy = int((1 - q) * -160)
                base = _paste(base, framed, (px - (framed.width - pw) // 2, py + dy - (framed.height - ph) // 2), op)
                # tape strips
                if op > 0.3:
                    tape_l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    td = ImageDraw.Draw(tape_l)
                    twape = int(pw * 0.42)
                    td.rectangle(
                        [px + pw * 0.3 - twape // 2, py - 18 + dy, px + pw * 0.3 + twape // 2, py + 14 + dy],
                        fill=(*TAPE, 210),
                    )
                    base = Image.alpha_composite(base, tape_l)
                # scribble ring
                if t > 1.2:
                    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    rd = ImageDraw.Draw(ring)
                    op_r = int(200 * min(1.0, (t - 1.2) / 0.4))
                    _dashed_ellipse(
                        rd,
                        [px + pw * 0.05, py + ph * 0.05, px + pw * 0.95, py + ph * 0.95],
                        (*warn, op_r),
                        width=5,
                    )
                    base = Image.alpha_composite(base, ring)

        # stickers from edges
        N = min(6, len(cuts))
        for i in range(N):
            left = i % 2 == 0
            sw = 104 + (i % 3) * 22
            delay = 0.35 + i * 0.16
            q = _ease_out_back(_seg(t, delay, 0.6))
            yy = H * 0.16 + (i % 3) * H * 0.24 + (i % 2) * 40
            xx_target = (W * 0.06 + (i % 2) * 40) if left else (W * 0.94 - sw - (i % 2) * 40)
            dx = (1 - q) * (-220 if left else 220)
            sticker = _rotate(_make_sticker(cuts[i], sw, rng, True), ( -1 if left else 1) * (5 + i * 2) * q)
            op = _seg(t, delay, 0.28)
            if op > 0.01:
                base = _paste(base, sticker, (int(xx_target + dx) - (sticker.width - sw) // 2, int(yy) - (sticker.height - sw) // 2), op)

        # title under photo / centered
        p = _seg(t, 1.05, 0.8)
        if p > 0.01 and title:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            td = ImageDraw.Draw(layer)
            probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            lines = _wrap(probe, title, title_font, W - 400)
            line_h = int(getattr(title_font, "size", 72) * 1.16)
            th = len(lines) * line_h
            if photo and photo.exists():
                ty = int(py + ph + 58 + (1 - p) * 30)
            else:
                ty = int(H * 0.40 - th / 2 + (1 - p) * 30)
            _text_block(td, title, (W // 2, ty), title_font, (*ink, int(255 * p)), W - 400)
            # underline marker on last line
            if p > 0.7:
                last_w = probe.textlength(lines[-1], font=title_font) if lines else 200
                uy = ty + (len(lines) - 1) * line_h + line_h * 0.85
                ImageDraw.Draw(layer).rectangle(
                    [W / 2 - last_w * 0.46, uy, W / 2 + last_w * 0.46, uy + 10],
                    fill=(*warn, int(200 * min(1.0, (p - 0.7) / 0.3))),
                )
            base = Image.alpha_composite(base, layer)
            if subtitle:
                sp = _seg(t, 1.7, 0.45)
                if sp > 0.01:
                    sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    sd = ImageDraw.Draw(sl)
                    _text_block(sd, subtitle, (W // 2, ty + th + 50), soft_font, (*ink_soft, int(255 * sp)), W - 480)
                    base = Image.alpha_composite(base, sl)
        return base

    # ── PHOTONOTE: taped photo left + handwritten annotation + arrow ──
    if template == "photonote":
        photo = Path(image_path) if image_path else None
        if photo is None or not photo.exists():
            # use first cutout as faux "photo" on paper — better than blank
            if cuts:
                photo = cuts[0]
        if photo and Path(photo).exists():
            pw = int(W * 0.40)
            ph = int(pw * 1.16)
            x = int(W * 0.09)
            y = int(H * 0.5 - ph / 2)
            d = 0.25
            q = _ease_out_back(_seg(t, d, 0.7))
            op = _seg(t, d, 0.3)
            if op > 0.01:
                # if cutout PNG (transparent), composite onto paper differently
                try:
                    probe = Image.open(photo)
                    is_cutout = probe.mode in ("RGBA", "LA", "P") and "cutouts" in str(photo)
                except Exception:
                    is_cutout = False
                if is_cutout:
                    framed = _make_sticker(Path(photo), int(ph * 0.85), rng, True)
                    framed = _rotate(framed, -2.4 * q)
                else:
                    framed = _photo_frame(Path(photo), pw, ph, rng, gray=0.35)
                    framed = _rotate(framed, -2.4 * q)
                dy = int((1 - q) * 70)
                base = _paste(base, framed, (x - (framed.width - pw) // 2, y + dy - (framed.height - ph) // 2), op)
                if op > 0.3:
                    tape_l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    td = ImageDraw.Draw(tape_l)
                    twape = int(pw * 0.4)
                    td.rectangle([x + pw * 0.3 - twape // 2, y - 20 + dy, x + pw * 0.3 + twape // 2, y + 12 + dy], fill=(*TAPE, 210))
                    td.rectangle([x + pw * 0.34 - twape // 2, y + ph - 10 + dy, x + pw * 0.34 + twape // 2, y + ph + 18 + dy], fill=(*TAPE, 200))
                    base = Image.alpha_composite(base, tape_l)

            tx = x + pw + 120
            p = _seg(t, 0.7, 0.6)
            if p > 0.01 and title:
                layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                td = ImageDraw.Draw(layer)
                _text_block(td, title, (tx, y + 30), title_font, (*ink, int(255 * p)), W - tx - 140, align="left")
                # shift from right
                # (approx via opacity; positional slide omitted for speed)
                base = Image.alpha_composite(base, layer)
                # arrow
                if t > 1.0:
                    al = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    ad = ImageDraw.Draw(al)
                    aop = int(230 * min(1.0, (t - 1.0) / 0.4))
                    _draw_arrow(ad, tx - 30, y + ph * 0.36, tx - 150, y + ph * 0.36 + 40, (*warn, aop), width=6)
                    base = Image.alpha_composite(base, al)
            if subtitle:
                sp = _seg(t, 1.3, 0.45)
                if sp > 0.01:
                    sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    sd = ImageDraw.Draw(sl)
                    _text_block(sd, subtitle, (tx, y + 200), soft_font, (*ink_soft, int(255 * sp)), W - tx - 160, align="left")
                    base = Image.alpha_composite(base, sl)
        else:
            # no photo — degrade to collage
            template = "collage"
        if template == "photonote":
            return base

    # ── COLLAGE default ──
    p_title = _ease_out_back(_seg(t, 0.12, 0.55))
    if p_title > 0.01 and title:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(layer)
        _text_block(td, title, (W // 2, int(70 + (1 - p_title) * -18)), title_font, (*ink, int(255 * p_title)), W - 320)
        base = Image.alpha_composite(base, layer)

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
    image_path: Optional[Path] = None,
) -> Path:
    """Render an animated collage/scatter/pillars/opener/photonote card to MP4."""
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL not available for motion graphics")
    if output_path is None:
        output_path = Path(tempfile.mktemp(suffix="_collage.mp4"))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    duration_sec = max(2.4, min(PACING["graphic_max_s"], float(duration_sec)))
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
                image_path=image_path,
            )
            zoom = 1.0 + 0.03 * (t / max(0.01, duration_sec))
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
        logger.info("Rendered %s card: %s (%.1fs)", template, output_path, duration_sec)
        return output_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def plan_gfx_insertions(
    total_duration_sec: float,
    transcript_sentences: List[str] = None,
    word_timings: List[dict] = None,  # NEW: Whisper word-level timings for VO-locking
    still_paths: Optional[List[Path]] = None,
    first_gfx_min_sec: Optional[float] = None,
    graphic_ratio: Optional[float] = None,
    vo_sync_lag_tolerance_sec: float = 0.3,  # Kevis: GFX must appear ≤0.3s after keyword spoken
) -> List[Dict[str, Any]]:
    """Plan Whop-paced mid-timeline GFX cards (~20–25% ratio) with VO-keyword-locking.

    **Kevis mandate**: Cards illustrating a line MUST appear when that line is spoken (≤0.3s lag).
    Late cards (after spoken span ends) fail timing/MG scoring.
    
    VO-locking strategy:
    1. Extract keywords from transcript sentences (nouns, verbs, key phrases)
    2. Find keyword spoken time from Whisper word_timings
    3. Place GFX card start_sec at keyword_time (or keyword_time - 0.1s for anticipation)
    4. Enforce ≤0.3s lag between keyword spoken and card visible
    5. Fallback to time-based if word_timings unavailable
    
    Short smokes (~40–60s): 2 cards @ ~3.4s after the opener hard cut (≥12–14s),
    never chopping the hook bed. Longer cooks: ~1 card / 30–35s.
    """
    cards = []
    target_ratio = float(graphic_ratio if graphic_ratio is not None else PACING["graphic_ratio"])
    first_min = float(first_gfx_min_sec if first_gfx_min_sec is not None else PACING.get("first_gfx_min_sec", 14.0))
    gap_min = float(PACING.get("min_gap_between_gfx_sec", 8.0))
    if total_duration_sec < 90:
        gap_min = min(gap_min, 6.0)
    dur = max(2.8, min(PACING["graphic_dur_s"], PACING["graphic_max_s"]))

    # Card count from ratio, but never before first_min; prefer fewer longer cards
    # Floor near first_gfx_min (was hard 12.0 from long-opener era)
    usable_start = max(first_min, 8.0)
    usable_end = total_duration_sec - 1.5
    usable = max(1.0, usable_end - usable_start)
    max_by_ratio = max(1, int(round(total_duration_sec * target_ratio / dur)))
    max_by_gap = max(1, int(usable / (dur + gap_min)) + 1)
    # Whop: ≥1 card / ~35s; short smokes aim 2–3 cards inside 20–25% ratio
    n = min(max_by_ratio, max_by_gap, 3 if total_duration_sec < 90 else 8)
    # Prefer hitting ≥20% when room exists
    while n < max_by_gap and n < (3 if total_duration_sec < 90 else 8):
        if ((n + 1) * dur) / max(0.01, total_duration_sec) <= 0.26:
            n += 1
        else:
            break
    # Ensure ratio stays in 20–25% band when possible
    while n > 1 and (n * dur) / max(0.01, total_duration_sec) > 0.26:
        n -= 1
    if n < 1:
        n = 1

    templates = ["collage", "scatter", "pillars", "photonote", "opener"]
    # Prefer collage thesis first (Whop ~0:34), then scatter/pillars — opener is weaker late
    
    # VO-keyword-locking: extract keywords from transcript for GFX timing
    vo_anchors = []  # List of (keyword, spoken_time_sec, sentence_idx)
    if word_timings and transcript_sentences:
        # Build word lookup: {word.lower(): [(start_sec, end_sec), ...]}
        word_lookup = {}
        for wt in word_timings:
            w = wt.get("word", "").lower().strip()
            if w and len(w) > 2:  # Skip short words
                if w not in word_lookup:
                    word_lookup[w] = []
                word_lookup[w].append((wt.get("start", 0), wt.get("end", 0)))
        
        # Extract keywords from each sentence (nouns, verbs, important words)
        import re
        for idx, sentence in enumerate(transcript_sentences):
            words = re.findall(r'\b\w+\b', sentence.lower())
            # Find substantive words (length >4, not common stop words)
            stop_words = {'this', 'that', 'these', 'those', 'with', 'from', 'have', 'been', 'will', 'would', 'could', 'should'}
            keywords = [w for w in words if len(w) > 4 and w not in stop_words]
            
            # For each keyword, find its spoken time
            for kw in keywords[:3]:  # Up to 3 keywords per sentence
                if kw in word_lookup and word_lookup[kw]:
                    # Use first occurrence of keyword in this sentence's time range
                    spoken_time = word_lookup[kw][0][0]  # start_sec of first match
                    vo_anchors.append((kw, spoken_time, idx, sentence))
    
    logger.info(f"VO-anchors extracted: {len(vo_anchors)} keywords for {n} GFX cards")
    default_items = [
        [
            {"label": "do not answer", "text": "not once"},
            {"label": "the woods learn", "text": "your voice"},
            {"label": "keep walking", "text": "do not look back"},
        ],
        [],
        [
            {"label": "alone", "text": "when light is low"},
            {"label": "listening", "text": "for a friend"},
            {"label": "spoken", "text": "soft. familiar."},
        ],
        [],
        [],
    ]
    default_titles = [
        ("three things the body already knows", "before the mind invents a story"),
        ("it is not a breakdown", "it is a process asking to be named"),
        ("what this rests on", "stay with it"),
        ("old timers say it waits", "until you are already listening"),
        ("if the forest calls your name", "do not answer"),
    ]
    stills = [Path(p) for p in (still_paths or []) if Path(p).exists()]

    for i in range(n):
        # VO-locked placement: use keyword anchor if available, fallback to time-based
        if vo_anchors and i < len(vo_anchors):
            keyword, spoken_time, sent_idx, sentence = vo_anchors[i * len(vo_anchors) // n]  # Spread anchors across n cards
            # Place card START at spoken_time (or slightly before for anticipation)
            start = max(usable_start, spoken_time - 0.1)  # 0.1s anticipation
            end = min(usable_end, start + dur)
            start = max(usable_start, end - dur)
            
            # Kevis rule: GFX must appear ≤0.3s after keyword spoken
            lag = start - spoken_time
            if lag > vo_sync_lag_tolerance_sec:
                logger.warning(f"GFX card {i} late: {lag:.2f}s lag after keyword '{keyword}' @ {spoken_time:.1f}s (FAIL vo_gfx_sync)")
                # Adjust to meet tolerance
                start = spoken_time + vo_sync_lag_tolerance_sec
                end = min(usable_end, start + dur)
            
            # Extract title from keyword + sentence
            words = sentence.split()
            if len(words) >= 4:
                title = " ".join(words[:6]).lower()
            else:
                title = keyword
        else:
            # Fallback: time-based placement (original logic)
            center = usable_start + usable * (i + 0.5) / n
            start = max(usable_start, center - dur / 2)
            end = min(usable_end, start + dur)
            start = max(usable_start, end - dur)
            
            # Extract title from transcript if available
            if transcript_sentences:
                idx = int((center / total_duration_sec) * len(transcript_sentences))
                idx = max(0, min(idx, len(transcript_sentences) - 1))
                words = transcript_sentences[idx].split()
                if len(words) >= 4:
                    title = " ".join(words[:6]).lower()
                else:
                    title, subtitle = default_titles[i % len(default_titles)]
            else:
                title, subtitle = default_titles[i % len(default_titles)]
        
        # Enforce minimum gap between cards
        if cards and start < cards[-1]["end_sec"] + gap_min:
            start = cards[-1]["end_sec"] + gap_min
            end = min(usable_end, start + dur)
            if end - start < 2.4:
                continue
        skin = "collage_dark" if i % 3 < 2 else "noir"
        title, subtitle = default_titles[i % len(default_titles)]
        tpl = templates[i % len(templates)]
        if transcript_sentences:
            idx = int((center / total_duration_sec) * len(transcript_sentences))
            idx = max(0, min(idx, len(transcript_sentences) - 1))
            words = transcript_sentences[idx].split()
            if len(words) >= 4:
                title = " ".join(words[:6]).lower()
        image = None
        if tpl in ("opener", "photonote") and stills:
            image = str(stills[i % len(stills)])
        cards.append({
            "start_sec": start,
            "end_sec": end,
            "title": title,
            "subtitle": subtitle,
            "skin": skin,
            "variant": i,
            "template": tpl,
            "items": default_items[i % len(default_items)],
            "seed": 11 + i * 11,
            "image_path": image,
        })
    gfx_dur = sum(c["end_sec"] - c["start_sec"] for c in cards)
    logger.info(
        "Planned %d GFX cards for %.1fs video (%.1fs GFX = %.0f%%) first≥%.1fs",
        len(cards), total_duration_sec, gfx_dur,
        100.0 * gfx_dur / max(0.01, total_duration_sec),
        usable_start,
    )
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
            img = spec.get("image_path")
            tpl = spec.get("template", "collage")
            need = {"scatter": 8, "pillars": 3, "opener": 6, "photonote": 1, "collage": 3}.get(tpl, 3)
            blob = " ".join([
                str(spec.get("title", "")),
                str(spec.get("subtitle", "")),
            ] + [
                str(it.get("label", "")) + " " + str(it.get("text", ""))
                for it in (spec.get("items") or [])
            ])
            cuts = pick_cutouts_for_text(blob, need=need, seed=int(spec.get("seed", 40 + i)))
            path = render_collage_card(
                title=spec["title"],
                subtitle=spec.get("subtitle", ""),
                skin_name=spec.get("skin", "collage_dark"),
                duration_sec=spec["end_sec"] - spec["start_sec"],
                output_path=out,
                template=tpl,
                items=spec.get("items"),
                cutout_paths=cuts,
                seed=int(spec.get("seed", 40 + i)),
                image_path=Path(img) if img else None,
            )
            rendered.append(path)
        except Exception as e:
            logger.error("Failed to render GFX card %d (%s): %s", i, spec.get("template"), e)
            rendered.append(None)
    return rendered


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("cutouts", len(list_cutouts()))
    for tpl in ("collage", "scatter", "pillars", "opener", "photonote"):
        test = render_collage_card(
            title="if the forest calls your name",
            subtitle="do not answer",
            skin_name="collage_dark",
            template=tpl,
            duration_sec=3.0,
            output_path=Path(f"/tmp/test_{tpl}.mp4"),
            items=[
                {"label": "the hollow", "text": "where silence sits"},
                {"label": "the ridge", "text": "what you climb alone"},
                {"label": "the hearth", "text": "warmth you refuse"},
            ],
        )
        print("Rendered", tpl, test, test.stat().st_size)
