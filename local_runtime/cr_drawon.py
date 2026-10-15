"""Whiteboard drawing: a drawn style's pictures draw themselves on as a hand with a marker traces the lines, then the
colour comes in (look.reveal = "draw").

Whiteboard channels are watched as live drawing: a hand draws each picture's lines, the marker squeaking, before the
picture settles (Gemini read the references as 76-86% animated). The same drawings held still read as a slideshow.
Each still gets a short clip beside it (images/scene_NN.anim.mp4) that the engine plays as a real shot and holds on
its last frame for the rest of its words:

  lines   the dark strokes appear group by group in reading order, each group traced left to right
  hand    a hand with a marker (drawon_art/hand.png) follows the newest line, its tip on it
  colour  the rest of the picture fades in once the lines are down, while the hand leaves
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOME = Path(__file__).resolve().parent
HAND = HOME / "drawon_art" / "hand.png"
HAND_TIP = (3, 155)                 # the marker's tip in hand.png
W, H, FPS = 1920, 1080, 30
INK = 110                           # darker than this is a line
FILL_S, HOLD_S = 0.45, 0.3
WORDS_PER_S = 2.8


def wanted(look) -> bool:
    return str((look or {}).get("reveal") or "").lower() == "draw"


def _fit(img):
    """The still as the engine frames it: scaled to cover 1920x1080, centred."""
    from PIL import Image
    img = img.convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((max(W, round(img.width * s)), max(H, round(img.height * s))), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((left, top, left + W, top + H))


def draw_seconds(words: int) -> float:
    """How long the lines take: about half of the time the picture's words are spoken, 1 to 2.6 s."""
    return round(min(2.6, max(1.0, 0.55 * max(1.5, words / WORDS_PER_S))), 2)


def plan(img):
    """(order, paper): the line pixels' flat indices in drawing order, and the paper colour.

    Lines are grouped into strokes (dilated, connected), the groups drawn from left to right, each group's time
    share by its size, and within a group the pixels traced left to right."""
    import cv2
    import numpy as np
    a = np.asarray(img, dtype=np.uint8)
    gray = (a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114)
    ink = gray < INK
    light = a[gray > 200]
    paper = np.median(light, axis=0).astype(np.uint8) if len(light) > 1000 else np.array([250, 250, 250], np.uint8)
    small = cv2.resize(ink.astype(np.uint8), (W // 2, H // 2), interpolation=cv2.INTER_NEAREST)
    groups = cv2.dilate(small, np.ones((13, 13), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(groups, connectivity=8)
    labels = cv2.resize(labels.astype(np.int32), (W, H), interpolation=cv2.INTER_NEAREST)
    ys, xs = np.nonzero(ink)
    lab = labels[ys, xs]
    keys = sorted(range(1, n), key=lambda k: (cents[k][0], cents[k][1]))       # left to right, as a hand draws
    weight = {k: float(np.sqrt(max(1, stats[k][cv2.CC_STAT_AREA]))) for k in keys}
    total = sum(weight.values()) or 1.0
    start, at = {}, 0.0
    for k in keys:
        start[k] = at
        at += weight[k] / total
    t = np.ones(len(xs), dtype=np.float32)
    for k in keys:
        sel = lab == k
        if not sel.any():
            continue
        x0, y0 = stats[k][cv2.CC_STAT_LEFT] * 2, stats[k][cv2.CC_STAT_TOP] * 2
        w, h = max(2, stats[k][cv2.CC_STAT_WIDTH] * 2), max(2, stats[k][cv2.CC_STAT_HEIGHT] * 2)
        local = 0.8 * (xs[sel] - x0) / w + 0.2 * (ys[sel] - y0) / h
        t[sel] = start[k] + (weight[k] / total) * np.clip(local, 0, 1)
    order = np.argsort(t, kind="stable")
    return (ys[order] * W + xs[order]).astype(np.int64), paper


def render(still: Path, dest: Path, words: int = 8) -> Path:
    """The still drawing itself on, as dest (an MP4 the engine plays as the picture's shot)."""
    import numpy as np
    from PIL import Image
    img = _fit(Image.open(still))
    orig = np.asarray(img, dtype=np.uint8)
    order, paper = plan(img)
    draw_s = draw_seconds(words)
    n_draw, n_fill, n_hold = int(draw_s * FPS), int(FILL_S * FPS), int(HOLD_S * FPS)
    hand = Image.open(HAND).convert("RGBA") if HAND.is_file() else None
    flat_o = orig.reshape(-1, 3)
    canvas = np.empty_like(orig)
    canvas[:] = paper
    flat_c = canvas.reshape(-1, 3)
    tmp = dest.with_name(dest.stem + ".part.mp4")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-color_range", "tv", "-r", str(FPS), str(tmp)],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    tip = np.array([W * 0.3, H * 0.4])
    done = 0

    def frame(base, at):
        """The canvas with the hand's marker tip at `at` (the part of the hand inside the frame)."""
        if hand is None:
            return base
        x, y = int(at[0]) - HAND_TIP[0], int(at[1]) - HAND_TIP[1]
        if x >= W or y >= H or x + hand.width <= 0 or y + hand.height <= 0:
            return base
        out = Image.fromarray(base)
        out.paste(hand, (x, y), hand)
        return np.asarray(out)
    try:
        for f in range(n_draw):
            p = (f + 1) / n_draw
            upto = int(len(order) * p)
            new = order[done:upto]
            if len(new):
                flat_c[new] = flat_o[new]
                last = new[-max(1, len(new) // 4):]          # the newest ink: where the marker is now
                tip = 0.6 * tip + 0.4 * np.array([float(np.mean(last % W)), float(np.mean(last // W))])
            done = upto
            enc.stdin.write(frame(canvas, tip).tobytes())
        flat_c[order[done:]] = flat_o[order[done:]]
        away = np.array([W + 80.0, H + 260.0])
        for f in range(n_fill):
            q = (f + 1) / n_fill
            q = q * q * (3 - 2 * q)
            mix = (canvas.astype(np.float32) * (1 - q) + orig.astype(np.float32) * q).astype(np.uint8)
            enc.stdin.write(frame(mix, tip + (away - tip) * q).tobytes())
        for _ in range(n_hold):
            enc.stdin.write(orig.tobytes())
        enc.stdin.close()
        if enc.wait(timeout=600):
            raise RuntimeError("the drawing could not be encoded: " + enc.stderr.read().decode()[-200:])
    except BaseException:
        enc.kill()
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return dest


def _words(job: Path) -> dict:
    """{scene index: words its picture is spoken over}, from the story's scenes (astro_scenes.json)."""
    try:
        items = json.loads((Path(job) / "astro_scenes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    clips = len(list((Path(job) / "clip_src").glob("vsrc_*.jpg")))
    return {k - clips: len(str(it.get("text") or "").split()) for k, it in enumerate(items)
            if isinstance(it, dict) and k >= clips}


def draw_all(job, photos, log=print, workers: int = 3) -> int:
    """Every story still that has no fresh drawing clip gets one. Returns how many were drawn."""
    words = _words(Path(job))
    todo = []
    for p in photos or []:
        m = re.search(r"scene_(\d+)\.(?:jpg|png)$", str(p or ""))
        if not m:
            continue
        still = Path(p)
        anim = still.with_suffix(".anim.mp4")
        if anim.is_file() and anim.stat().st_mtime >= still.stat().st_mtime:
            continue
        todo.append((still, anim, words.get(int(m.group(1)), 8)))
    if not todo:
        return 0
    log(f"  whiteboard: drawing {len(todo)} picture(s) on, line by line")
    lock, made = threading.Lock(), [0]

    def one(job_):
        still, anim, n = job_
        try:
            render(still, anim, n)
            with lock:
                made[0] += 1
        except Exception as e:                                       # noqa: BLE001 - the still is shown as before
            log(f"  whiteboard: {still.name} stays a still ({type(e).__name__}: {str(e)[:100]})")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, todo))
    return made[0]


def cues(plan_, look) -> list:
    """[(second, sound, dB)]: the marker squeaking while each picture draws itself on."""
    if not wanted(look):
        return []
    out, t = [], 0.0
    for kind, path, _leak, dur in plan_ or []:
        anim = Path(str(path)).with_suffix(".anim.mp4")
        if kind == "photo" and re.search(r"scene_\d+\.(?:jpg|png)$", str(path)) and anim.is_file():
            out.append((round(t + 0.05, 2), "scribble", -21.0))
        t += float(dur)
    return out


def install(engine):
    """Draw a whiteboard style's pictures on before the timeline is cut; the marker joins the mixer's cues."""
    if getattr(engine, "_cr_drawon", False):
        return
    engine._cr_drawon = True
    original = engine.generate_astrology_visuals

    def visuals(*args, **kwargs):
        out = original(*args, **kwargs)
        try:
            job = kwargs.get("job") or next((a for a in args if isinstance(a, Path)), None)
            style = (Path(job) / "style.txt").read_text(encoding="utf-8").strip() if job else ""
            look = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}).get("look") or {}
            if job and wanted(look):
                photos = out[0] if isinstance(out, tuple) else out
                draw_all(job, photos, getattr(engine, "log", print))
        except Exception as e:                                       # noqa: BLE001 - the stills stand
            getattr(engine, "log", print)(f"  whiteboard: skipped ({type(e).__name__}: {str(e)[:100]})")
        return out
    engine.generate_astrology_visuals = visuals
    try:
        import sfx
    except ImportError:
        return
    original_cues = sfx.cues

    def cues_(plan_, job=None, style=None, look=None):
        out = original_cues(plan_, job, style, look)
        the_look = look if isinstance(look, dict) else (sfx._v2_look(style) if style else {})
        extra = cues(plan_, the_look)
        return sorted(out + extra) if extra else out
    sfx.cues = cues_
