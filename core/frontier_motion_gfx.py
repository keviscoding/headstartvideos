"""
Frontier Motion GFX — Pillow collage/cutout cards approximating Whop collage_dark.

Whop Frontier renders collage/scatter/pillars via Playwright HTML. Channel Recipe
ports a practical Pillow + ffmpeg path so motion_graphics can reach Jung density
without a browser: dark aged notebook ground, paper-carded cutout stickers,
handwritten-style cream labels, drop-in + settle animation, 3–6s MP4 segments.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

W, H = 1920, 1080
FPS = 24

# Jung collage_dark palette (Whop skins)
BG = (13, 13, 16)
BG2 = (23, 23, 28)
INK = (236, 229, 214)
INK_SOFT = (154, 145, 132)
WARN = (181, 84, 74)
GOOD = (125, 154, 134)
GOLD = (194, 163, 95)
CARD = (26, 26, 32)
CARD_LINE = (51, 51, 60)
TAPE = (210, 190, 140)

TemplateName = Literal["collage", "scatter", "pillars"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _assets_root() -> Path:
    """Prefer Whop cutouts: assets/whop-gfx (symlink) or frontier-gfx-kit/."""
    for root in (
        _repo_root() / "assets" / "whop-gfx",
        _repo_root() / "frontier-gfx-kit",
        _repo_root() / "assets",
    ):
        if (root / "cutouts").is_dir():
            return root
    return _repo_root() / "assets"


def _ffmpeg() -> str:
    for c in (
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "ffmpeg",
    ):
        if c == "ffmpeg" or Path(c).exists():
            which = shutil.which("ffmpeg") if c == "ffmpeg" else c
            if which:
                return which
    return "ffmpeg"


def _load_font(size: int, cursive: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    root = _assets_root()
    fonts = _repo_root() / "assets" / "whop-gfx" / "fonts"
    candidates = []
    if cursive:
        # Prefer anything handwritten-ish; fall back to Inter
        candidates += [
            Path("/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"),
            Path("/System/Library/Fonts/Supplemental/Chalkboard.ttc"),
            Path("/Library/Fonts/Comic Sans MS.ttf"),
            Path("/System/Library/Fonts/Supplemental/Brush Script.ttf"),
        ]
    candidates += [
        fonts / "InterDisplay-Black.ttf",
        fonts / "Inter-ExtraBold.ttf",
        fonts / "Inter-Black.ttf",
        fonts / "Inter-Bold.ttf",
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for p in candidates:
        try:
            if p.exists():
                return ImageFont.truetype(str(p), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def list_cutouts() -> list[Path]:
    d = _assets_root() / "cutouts"
    if not d.is_dir():
        return []
    return sorted(d.glob("*.png"))


def _paper_card(w: int, h: int, rng: random.Random) -> Image.Image:
    """Aged paper rectangle for sticker backing (collage_dark card trick)."""
    tex_path = _assets_root() / "textures" / "paper_earth.jpg"
    if tex_path.exists():
        tex = Image.open(tex_path).convert("RGB")
        tex = tex.resize((w, h), Image.Resampling.LANCZOS)
    else:
        # Procedural warm paper
        base = (232, 220, 186)
        tex = Image.new("RGB", (w, h), base)
        px = tex.load()
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                n = rng.randint(-18, 12)
                r = max(0, min(255, base[0] + n))
                g = max(0, min(255, base[1] + n - 2))
                b = max(0, min(255, base[2] + n - 6))
                px[x, y] = (r, g, b)
                if x + 1 < w:
                    px[x + 1, y] = (r, g, b)
                if y + 1 < h:
                    px[x, y + 1] = (r, g, b)
                    if x + 1 < w:
                        px[x + 1, y + 1] = (r, g, b)
    # Soft vignette on card
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([0, 0, w - 1, h - 1], outline=(90, 70, 40, 90), width=3)
    out = tex.convert("RGBA")
    out = Image.alpha_composite(out, overlay)
    return out


def _paste_rgba(base: Image.Image, overlay: Image.Image, xy: tuple[int, int], opacity: float = 1.0):
    if opacity < 0.99:
        a = overlay.split()[-1].point(lambda p: int(p * opacity))
        overlay = overlay.copy()
        overlay.putalpha(a)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(overlay, xy, overlay)
    return Image.alpha_composite(base, layer)


def _rotate_rgba(im: Image.Image, angle: float) -> Image.Image:
    return im.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)


def _make_sticker(
    cutout: Path,
    target_h: int,
    rng: random.Random,
    with_card: bool = True,
) -> Image.Image:
    img = Image.open(cutout).convert("RGBA")
    # Trim near-transparent margins lightly
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    ar = img.width / max(1, img.height)
    th = target_h
    tw = max(40, int(th * ar))
    img = img.resize((tw, th), Image.Resampling.LANCZOS)
    # Soft drop shadow
    shadow = Image.new("RGBA", (tw + 40, th + 40), (0, 0, 0, 0))
    sh = img.split()[-1].point(lambda p: int(p * 0.45))
    shadow.paste((0, 0, 0, 180), (20, 24), sh)
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    sticker = Image.new("RGBA", (tw + 40, th + 40), (0, 0, 0, 0))
    sticker = Image.alpha_composite(sticker, shadow)
    sticker.paste(img, (20, 12), img)

    if with_card:
        pad = int(th * 0.14)
        cw, ch = tw + pad * 2 + 40, th + pad * 2 + 40
        card = _paper_card(cw, ch, rng)
        # Center sticker on card
        sx = (cw - sticker.width) // 2
        sy = (ch - sticker.height) // 2
        card = Image.alpha_composite(card, Image.new("RGBA", card.size, (0, 0, 0, 0)))
        # paste sticker
        layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
        layer.paste(sticker, (sx, sy), sticker)
        card = Image.alpha_composite(card, layer)
        return card
    return sticker


def _draw_tape(draw: ImageDraw.ImageDraw, cx: int, cy: int, w: int, rot_hint: float):
    # Approximate tape as a flat rectangle (rotation baked into frame later via sticker)
    x0, y0 = cx - w // 2, cy - 18
    draw.rectangle([x0, y0, x0 + w, y0 + 36], fill=(*TAPE, 210))
    draw.rectangle([x0, y0, x0 + w, y0 + 36], outline=(160, 140, 90, 120), width=1)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = cur + " " + w
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font,
    fill,
    max_w: int,
    align: str = "center",
):
    lines = _wrap(draw, text, font, max_w)
    x, y = xy
    line_h = int(font.size * 1.15) if hasattr(font, "size") else 40
    for i, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        lx = x - lw / 2 if align == "center" else x
        # Soft shadow for cream on black
        draw.text((lx + 2, y + i * line_h + 2), line, font=font, fill=(0, 0, 0, 160))
        draw.text((lx, y + i * line_h), line, font=font, fill=fill)


def _ground(rng: random.Random) -> Image.Image:
    tex = _assets_root() / "textures" / "background_dark.png"
    if tex.exists():
        g = Image.open(tex).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
        # Ensure near-black Jung notebook
        g = ImageEnhance.Brightness(g).enhance(0.55)
        g = ImageEnhance.Contrast(g).enhance(1.15)
    else:
        g = Image.new("RGB", (W, H), BG)
        px = g.load()
        for y in range(H):
            for x in range(0, W, 3):
                n = rng.randint(-6, 6)
                v = max(0, min(30, BG[0] + n + (y // 80)))
                px[x, y] = (v, v, min(34, v + 2))
    # Film grain
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


def _seg(t: float, start: float, dur: float) -> float:
    if dur <= 0:
        return 1.0
    return max(0.0, min(1.0, (t - start) / dur))


def compose_frame(
    template: TemplateName,
    title: str,
    items: list[dict],
    cutout_paths: list[Path],
    t: float,
    duration: float,
    rng: random.Random,
    subtitle: str = "",
) -> Image.Image:
    """Compose one RGBA frame at time t (seconds)."""
    base = _ground(rng)
    draw = ImageDraw.Draw(base)
    title_font = _load_font(72 if template != "scatter" else 64, cursive=True)
    hand_font = _load_font(44, cursive=True)
    soft_font = _load_font(34, cursive=True)

    # Title fade/slide
    p_title = _ease_out_back(_seg(t, 0.12, 0.55))
    title_y = int(70 + (1 - p_title) * -18)
    if p_title > 0.01:
        # temporary opacity via alpha layer
        title_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(title_layer)
        _text_block(td, title or "—", (W // 2, title_y), title_font, (*INK, int(255 * p_title)), W - 320)
        base = Image.alpha_composite(base, title_layer)
        draw = ImageDraw.Draw(base)

    n = max(1, min(len(cutout_paths), len(items) if items else len(cutout_paths), 4))
    cuts = cutout_paths[:n]
    its = (items + [{"label": "", "text": ""}] * n)[:n]
    rots = [-4.5, 3.2, -2.4, 4.1]
    dirs = ["down", "left", "up", "right"]

    if template == "scatter":
        # Center title already drawn; ring of stickers
        N = min(8, len(cutout_paths))
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
            sticker = _make_sticker(cutout_paths[i], sw, rng, with_card=True)
            sticker = _rotate_rgba(sticker, (1 if i % 2 else -1) * (4 + i * 1.6) * q)
            op = max(0.0, min(1.0, _seg(t, delay, 0.28)))
            if op > 0.01:
                # center paste
                px = x - (sticker.width - sw) // 2
                py = y - (sticker.height - sw) // 2
                base = _paste_rgba(base, sticker, (px, py), opacity=op)
        # Scribble ring approx
        if t > 1.4:
            ring_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rd = ImageDraw.Draw(ring_layer)
            op = int(200 * min(1.0, (t - 1.4) / 0.5))
            bbox = [W * 0.28, H * 0.32, W * 0.72, H * 0.68]
            rd.ellipse(bbox, outline=(*WARN, op), width=6)
            rd.ellipse([bbox[0] - 8, bbox[1] + 6, bbox[2] + 8, bbox[3] - 4], outline=(*WARN, op // 2), width=3)
            base = Image.alpha_composite(base, ring_layer)
        if subtitle:
            sub_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sub_layer)
            sp = _seg(t, 2.0, 0.5)
            _text_block(sd, subtitle, (W // 2, H - 140), soft_font, (*INK_SOFT, int(255 * sp)), W - 520)
            base = Image.alpha_composite(base, sub_layer)
        return base.convert("RGB").convert("RGBA")

    if template == "pillars":
        n = max(1, min(3, n))
        cuts = cutout_paths[:n]
        its = its[:n]
        ph = int(H * 0.40)
        # Use cutout aspect for width estimate
        sample = Image.open(cuts[0]).convert("RGBA")
        ar = sample.width / max(1, sample.height)
        pw = int(ph / max(0.3, ar * 0.55 if ar > 1.5 else ar))
        pw = max(120, min(280, pw))
        gap = int(W * 0.075)
        tw = n * pw + (n - 1) * gap
        x0 = (W - tw) // 2
        base_y = int(H * 0.70)
        # Floor line
        fl = _seg(t, 0.3, 0.5)
        fl_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fld = ImageDraw.Draw(fl_layer)
        flw = int((tw + 220) * fl)
        fld.rectangle([x0 - 110, base_y, x0 - 110 + flw, base_y + 6], fill=(*INK, int(180 * fl)))
        base = Image.alpha_composite(base, fl_layer)
        for i in range(n):
            d = 0.55 + i * 0.3
            p = _ease_out_back(_seg(t, d, 0.85))
            sway = math.sin(t * 0.8 + i) * 0.25 * _seg(t, d + 0.9, 0.6)
            col = _make_sticker(cuts[i], ph, rng, with_card=False)
            # Fit to pillar box
            col = col.resize((pw, ph), Image.Resampling.LANCZOS)
            col = _rotate_rgba(col, sway)
            dy = int((1 - p) * ph * 0.55)
            x = x0 + i * (pw + gap)
            y = base_y - ph + dy
            op = _seg(t, d, 0.3)
            base = _paste_rgba(base, col, (x - (col.width - pw) // 2, y), opacity=op)
            # Labels
            it = its[i]
            lp = _seg(t, d + 0.5, 0.45)
            if lp > 0.01 and it.get("label"):
                ll = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ld = ImageDraw.Draw(ll)
                cx = x + pw // 2
                _text_block(ld, it["label"], (cx, base_y + 30), hand_font, (*INK, int(255 * lp)), min(380, pw + gap - 24))
                if it.get("text"):
                    _text_block(ld, it["text"], (cx, base_y + 92), soft_font, (*INK_SOFT, int(255 * lp)), min(380, pw + gap - 24))
                base = Image.alpha_composite(base, ll)
        return base

    # Default: collage (2–4 stickers in a row)
    col_w = {1: 760, 2: 520, 3: 400}.get(n, 320)
    gap = 120 if n <= 2 else 60
    tw = n * col_w + (n - 1) * gap
    x0 = (W - tw) // 2
    y = int(H * 0.20 if n == 1 else H * 0.30)
    target_h = {1: 420, 2: 380, 3: 300}.get(n, 260)
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
        sticker = _make_sticker(cuts[i], target_h, rng, with_card=True)
        sticker = _rotate_rgba(sticker, rots[i] * q)
        cx = x0 + i * (col_w + gap) + col_w // 2
        x = int(cx - sticker.width / 2 + dx)
        yy = int(y + dy + bob)
        op = _seg(t, delay, 0.28)
        if op > 0.01:
            base = _paste_rgba(base, sticker, (x, yy), opacity=op)
        # Tape
        if op > 0.3:
            tape_l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            td = ImageDraw.Draw(tape_l)
            _draw_tape(td, cx + dx, y - 10 + dy, int(sticker.width * 0.34), rots[i])
            base = Image.alpha_composite(base, tape_l)
        # Labels under sticker
        ly = y + target_h + int(target_h * 0.28) + 40
        lp = _seg(t, delay + 0.45, 0.45)
        it = its[i]
        if lp > 0.01:
            ll = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(ll)
            if it.get("label"):
                _text_block(ld, it["label"], (cx, ly), hand_font, (*INK, int(255 * lp)), col_w)
            if it.get("text"):
                _text_block(
                    ld,
                    it["text"],
                    (cx, ly + (76 if n == 1 else 58)),
                    soft_font,
                    (*INK_SOFT, int(255 * lp)),
                    col_w,
                )
            base = Image.alpha_composite(base, ll)

    if subtitle:
        sp = _seg(t, 1.5, 0.5)
        if sp > 0.01:
            sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sl)
            _text_block(sd, subtitle, (W // 2, H - 90), soft_font, (*WARN, int(255 * sp)), W - 400)
            base = Image.alpha_composite(base, sl)
    return base


def render_motion_gfx_mp4(
    output_path: str | Path,
    template: TemplateName = "collage",
    title: str = "",
    items: list[dict] | None = None,
    subtitle: str = "",
    duration_sec: float = 5.0,
    cutout_paths: list[str | Path] | None = None,
    seed: int = 42,
    fps: int = FPS,
) -> Path:
    """
    Render a short collage/scatter/pillars motion card to MP4.

    items: list of {label, text} dicts (2–4 for collage, up to 3 for pillars).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    items = items or []
    cuts = [Path(p) for p in (cutout_paths or [])]
    if not cuts:
        library = list_cutouts()
        if not library:
            raise FileNotFoundError(
                "No cutouts found under assets/whop-gfx/cutouts — unpack whop-gfx-pack first"
            )
        # Deterministic pick
        picks = library[:]
        rng.shuffle(picks)
        need = 8 if template == "scatter" else (3 if template == "pillars" else max(2, min(4, len(items) or 3)))
        cuts = picks[:need]
    # Ensure items exist for collage
    if not items:
        defaults = [
            {"label": "the shadow", "text": "what you refuse to see"},
            {"label": "the persona", "text": "the mask that stuck"},
            {"label": "the self", "text": "the whole you"},
        ]
        items = defaults[: max(2, min(3, len(cuts)))]

    duration_sec = max(3.0, min(8.0, float(duration_sec)))
    n_frames = max(2, int(round(duration_sec * fps)))
    tmp = Path(tempfile.mkdtemp(prefix="frontier_gfx_"))
    try:
        for k in range(n_frames):
            t = k / fps
            frame = compose_frame(
                template=template,
                title=title,
                items=items,
                cutout_paths=cuts,
                t=t,
                duration=duration_sec,
                rng=random.Random(seed),  # stable ground grain
                subtitle=subtitle,
            )
            # Scale settle: subtle overall zoom for life
            zoom = 1.0 + 0.03 * (t / duration_sec)
            zw, zh = int(W * zoom), int(H * zoom)
            frame = frame.convert("RGB").resize((zw, zh), Image.Resampling.LANCZOS)
            left = (zw - W) // 2
            top = (zh - H) // 2
            frame = frame.crop((left, top, left + W, top + H))
            frame.save(tmp / f"f_{k:05d}.jpg", quality=92)

        cmd = [
            _ffmpeg(),
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(tmp / "f_%05d.jpg"),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            str(output_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"gfx encode failed: {(r.stderr or '')[-1200:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return output_path


def default_appalachian_scenes() -> list[dict]:
    """2–4 Jung-ish collage cards tuned for the Appalachian ~43s smoke."""
    return [
        {
            "template": "collage",
            "title": "three things the body already knows",
            "subtitle": "before the mind invents a story",
            "duration": 5.0,
            "items": [
                {"label": "the hollow", "text": "where silence sits"},
                {"label": "the ridge", "text": "what you climb alone"},
                {"label": "the hearth", "text": "warmth you refuse"},
            ],
            "seed": 11,
        },
        {
            "template": "scatter",
            "title": "it is not a breakdown",
            "subtitle": "it is a process asking to be named",
            "duration": 4.5,
            "items": [],
            "seed": 22,
        },
        {
            "template": "pillars",
            "title": "what this rests on",
            "subtitle": "",
            "duration": 5.0,
            "items": [
                {"label": "attention", "text": "stay with it"},
                {"label": "honesty", "text": "name the cost"},
                {"label": "return", "text": "come back daily"},
            ],
            "seed": 33,
        },
        {
            "template": "collage",
            "title": "the mask that stuck",
            "subtitle": "take it off without burning the room",
            "duration": 4.5,
            "items": [
                {"label": "persona", "text": "who they need"},
                {"label": "shadow", "text": "who you bury"},
            ],
            "seed": 44,
        },
    ]


def render_scene_batch(
    out_dir: str | Path,
    scenes: list[dict] | None = None,
) -> list[dict]:
    """Render a batch of motion gfx cards. Returns list of {path, duration, template, title}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scenes = scenes or default_appalachian_scenes()
    results = []
    for i, sc in enumerate(scenes):
        out = out_dir / f"gfx_{i:02d}_{sc.get('template', 'collage')}.mp4"
        render_motion_gfx_mp4(
            output_path=out,
            template=sc.get("template", "collage"),
            title=sc.get("title", ""),
            items=sc.get("items") or [],
            subtitle=sc.get("subtitle", ""),
            duration_sec=float(sc.get("duration", 5.0)),
            seed=int(sc.get("seed", 40 + i)),
        )
        results.append(
            {
                "path": str(out),
                "duration": float(sc.get("duration", 5.0)),
                "template": sc.get("template", "collage"),
                "title": sc.get("title", ""),
            }
        )
    return results


if __name__ == "__main__":
    import sys

    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "output/gfx_smoke")
    batch = render_scene_batch(dest)
    for b in batch:
        print(b["path"], b["duration"], b["template"])
