"""Your call to action in any video: what you promote, its link as a QR code, its picture, 1 to 3 mentions.

The creator fills "Call to action" on the create page (what it is, its link,
optionally a cover or screenshot, how many mentions). For that video:

  - the script mentions it in the narrator's own voice, tied to what is being
    said, at about 40% and 70% of the way through and in the closing lines
    (the first n of those), never in the first three minutes; each mention
    says the exact words "link on screen";
  - the finished video shows a card (the picture, the QR code, what it is) on
    the words "link on screen", fading in and out;
  - the description opens with the link.

Nothing changes for a video without a call to action.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

HOME = Path(__file__).resolve().parent
UPLOADS = HOME / "uploads" / "cta"
CUE = "link on screen"
PLACES = (0.40, 0.70, None)             # None: the closing lines
FIRST_S = 180.0                         # never in the first three minutes
CARD_S = 8.0
IMAGE_ID = re.compile(r"cta_[a-f0-9]{20}")


# ── the settings a video is started with ─────────────────────────────────────────────────────────────────────

def clean(raw) -> dict:
    """The validated call to action from the create page ({} when the video has none). Raises ValueError."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Fill in your call to action again.")
    what = " ".join(str(raw.get("what") or "").split())
    link = str(raw.get("link") or "").strip()
    if not 3 <= len(what) <= 160:
        raise ValueError("Say what you are promoting in a few words (up to 160 characters).")
    if not re.fullmatch(r"https?://[^\s<>\"']{3,190}", link):
        raise ValueError("Give your call to action a full link, starting with https://.")
    import cr_qr
    cr_qr.matrix(link)                                   # a link the QR code can hold
    try:
        count = int(raw.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    out = {"what": what, "link": link, "count": max(1, min(3, count))}
    image = str(raw.get("image") or "")
    if image:
        if not IMAGE_ID.fullmatch(image) or not (UPLOADS / f"{image}.jpg").is_file():
            raise ValueError("Upload your call-to-action picture again.")
        out["image"] = image
    return out


def save_image(data: bytes, root: Path = None) -> str:
    """A cover or screenshot for the card. Returns its id."""
    from PIL import Image, ImageOps
    import io
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("Use a picture under 15 MB.")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:                                            # noqa: BLE001
        raise ValueError("That file could not be read as a picture.") from e
    img.thumbnail((1200, 1200))
    ident = "cta_" + hashlib.sha256(data).hexdigest()[:20]
    folder = Path(root or UPLOADS)
    folder.mkdir(parents=True, exist_ok=True)
    img.save(folder / f"{ident}.jpg", quality=90)
    return ident


def of_job(job) -> dict:
    try:
        spec = json.loads((Path(job) / "run_spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cfg = spec.get("cr_cta") if isinstance(spec, dict) else None
    return cfg if isinstance(cfg, dict) and cfg.get("link") else {}


# ── the script ─────────────────────────────────────────────────────────────────────────────────────────────

def places(count: int, minutes: float) -> list:
    """Where the mentions go, as words of the narration: the first `count` of 40%, 70% and the end that fall after
    the first three minutes, so a spot too early moves to the next one (a short video keeps only the closing one)."""
    allowed = ["in the closing lines, before the goodbye" if frac is None else f"about {int(frac * 100)}% of the way through"
               for frac in PLACES if frac is None or minutes * frac * 60 >= FIRST_S]
    return allowed[:max(1, min(3, count))]


def script_block(cfg: dict, minutes: float) -> str:
    where = places(cfg["count"], minutes)
    lines = [f"\n\nCALL TO ACTION — the creator promotes this in the video: {cfg['what']}.",
             f"Mention it {len(where)} time(s), in the narrator's own voice and tied to what is being said at that "
             "moment: " + "; ".join(f"mention {i + 1} {w}" for i, w in enumerate(where)) + ".",
             f"Never in the first three minutes. Each mention is under 35 words, says plainly why it helps the "
             f"viewer, and contains the exact words \"{CUE}\". No price, no hard sell; everything else in the "
             "script stays as it would be."]
    return "\n".join(lines) + "\n"


# ── the card on the finished video ─────────────────────────────────────────────────────────────────────────

def mention_times(job, count: int, total: float) -> list:
    """Seconds where the narrator says the cue (word timings), else the planned places."""
    job = Path(job)
    words = []
    try:
        words = [(str(w).lower().strip(".,!?;:\"'"), float(a)) for w, a, _ in
                 json.loads((job / "words_heard.json").read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        pass
    cue = CUE.split()
    found = []
    for i in range(len(words) - len(cue) + 1):
        if [w for w, _ in words[i:i + len(cue)]] == cue:
            t = words[i][1]
            if (t >= min(FIRST_S, total * 0.6) or total < FIRST_S + CARD_S) and all(abs(t - f) > CARD_S for f in found):
                found.append(t)
    if not found:
        for frac in PLACES[:count]:
            t = total - CARD_S - 4 if frac is None else total * frac
            if t >= min(FIRST_S, total - CARD_S - 4):
                found.append(t)
    return sorted(found)[:count] or [max(0.0, total - CARD_S - 4)]


def card(cfg: dict, dest: Path, root: Path = None) -> Path:
    """The card: picture, what it is and the QR code, on a rounded panel in the lower right of a 1920x1080 frame."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    import cr_qr
    W, H = 1920, 1080
    pw, ph, pad = 940, 300, 26
    x0, y0 = W - pw - 64, H - ph - 84
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0 + 6, y0 + 10, x0 + pw + 6, y0 + ph + 10), radius=30,
                                             fill=(0, 0, 0, 110))
    frame.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
    ImageDraw.Draw(frame).rounded_rectangle((x0, y0, x0 + pw, y0 + ph), radius=28, fill=(255, 255, 255, 246))
    inner = ph - 2 * pad
    qr = Image.open(cr_qr.png(cfg["link"], dest.with_suffix(".qr.png"), scale=8, border=2)).convert("RGB")
    qr = qr.resize((inner, inner), Image.NEAREST)
    frame.paste(qr, (x0 + pw - pad - inner, y0 + pad))
    left = x0 + pad
    if cfg.get("image"):
        pic = Image.open(Path(root or UPLOADS) / f"{cfg['image']}.jpg").convert("RGB")
        pic.thumbnail((int(inner * 0.75), inner))
        frame.paste(pic, (left, y0 + (ph - pic.height) // 2))
        left += pic.width + 24
    right = x0 + pw - pad - inner - 24
    draw = ImageDraw.Draw(frame)
    fonts = HOME / "assets" / "fonts"

    def font(name, size):
        try:
            return ImageFont.truetype(str(fonts / name), size)
        except OSError:
            return ImageFont.load_default()

    def wrap(text, f, width):
        lines, line = [], ""
        for w in text.split():
            trial = (line + " " + w).strip()
            if draw.textlength(trial, font=f) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = w
        return lines + ([line] if line else [])
    width = right - left
    for size in (36, 32, 28, 24):                       # the largest size that fits the whole description
        big = font("Inter-Bold.ttf", size)
        lines = wrap(cfg["what"], big, width)
        if len(lines) * int(size * 1.25) <= inner - 44:
            break
    small = font("Inter-Bold.ttf", 22)
    label = wrap("Scan the code, or use the link below", small, width)
    ly = y0 + ph - pad - len(label) * 28
    block = len(lines[:6]) * int(big.size * 1.25)
    y = y0 + pad + max(4, (ly - 16 - (y0 + pad) - block) // 2)       # the description centred above the label
    for ln in lines[:6]:
        draw.text((left, y), ln, font=big, fill=(20, 20, 24, 255))
        y += int(big.size * 1.25)
    for ln in label:
        draw.text((left, ly), ln, font=small, fill=(95, 95, 105, 255))
        ly += 28
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.save(dest)
    return dest


def overlay(video: Path, card_png: Path, times: list, out: Path) -> Path:
    """The card over the video at each time, fading in and out; the audio is copied untouched."""
    inputs, chain, last = ["-i", str(video)], [], "[0:v]"
    for i, t in enumerate(times):
        inputs += ["-loop", "1", "-t", f"{CARD_S:.2f}", "-i", str(card_png)]
        chain.append(f"[{i + 1}:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1,"
                     f"fade=t=out:st={CARD_S - 0.5:.2f}:d=0.5:alpha=1,setpts=PTS+{t:.3f}/TB[c{i}]")
        chain.append(f"{last}[c{i}]overlay=0:0:eof_action=pass:enable='between(t,{t:.3f},{t + CARD_S:.3f})'[v{i}]")
        last = f"[v{i}]"
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chain), "-map", last, "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
           "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=7200)
    return out


def finish(job, log=print) -> bool:
    """The card on the job's finished video (once) and the link in its description."""
    job = Path(job)
    cfg = of_job(job)
    video = job / "video.mp4"
    if not cfg or not video.is_file():
        return False
    stamp = job / "cta.json"
    mark = f"{video.stat().st_size}:{video.stat().st_mtime_ns}"
    done = json.loads(stamp.read_text()) if stamp.is_file() else {}
    if done.get("video") != mark:
        total = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                      str(video)], capture_output=True, text=True, check=True).stdout.strip() or 0)
        times = mention_times(job, int(cfg.get("count") or 1), total)
        png = card(cfg, job / "cta" / "card.png")
        tmp = job / "video.cta.mp4"
        overlay(video, png, times, tmp)
        tmp.replace(video)
        stamp.write_text(json.dumps({"video": f"{video.stat().st_size}:{video.stat().st_mtime_ns}", "at": times}))
        log(f"call to action: the card at {', '.join(f'{t:.0f}s' for t in times)}")
    desc = job / "youtube.txt"
    head = f"{cfg['what']}: {cfg['link']}\n\n"
    text = desc.read_text(encoding="utf-8") if desc.is_file() else ""
    if not text.startswith(head):
        desc.write_text(head + text, encoding="utf-8")
    return True


# ── wiring into the engine ──────────────────────────────────────────────────────────────────────────────────

_current = {"cfg": {}, "minutes": 10.0}


def install(engine):
    if getattr(engine, "_cr_cta", False):
        return
    engine._cr_cta = True
    original_script, original_extra, original_publish = (engine.generate_script, engine._extra_block,
                                                          engine.publish_named_copy)

    def generate_script(title, minutes, job, force, style=""):
        _current.update(cfg=of_job(job), minutes=float(minutes or 10))
        try:
            return original_script(title, minutes, job, force, style=style)
        finally:
            _current.update(cfg={})

    def extra_block(*args, **kwargs):
        block = original_extra(*args, **kwargs)
        return block + script_block(_current["cfg"], _current["minutes"]) if _current["cfg"] else block

    def publish_named_copy(job, title, fresh=""):
        try:
            finish(job, engine.log)
        except Exception as e:                                         # noqa: BLE001 - the video is still the video
            engine.log(f"call to action: the card could not be added ({type(e).__name__}: {str(e)[:100]})")
        return original_publish(job, title, fresh)

    engine.generate_script, engine._extra_block, engine.publish_named_copy = generate_script, extra_block, publish_named_copy
