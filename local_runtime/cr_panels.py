"""Your own comic panels as a video's pictures (manga, manhwa and webtoon recaps).

A recipe whose look has "panels": true shows the creator's own pages instead of
drawing pictures. The creator uploads the panels (images, or one .zip/.cbz) on
the create page; the run carries the set's id (spec "cr_panels") into the job.
Then, for that job only:

  - long webtoon strips are cut at their blank gutters into separate panels;
  - Claude reads the panels in order (vision, a few at a time) into a panel log,
    which the outline and the script are written from (or the creator's own
    script is used as it is);
  - Claude ties each stretch of the narration to the panel it describes, word
    for word, so every panel lands on its own words in the story timeline;
  - each panel is framed for 16:9 over a blurred copy of itself.

No picture is generated, so panels cost nothing on the picture account.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

HOME = Path(__file__).resolve().parent
ROOT = HOME / "uploads" / "panels"
MAX_PANELS = 400
MAX_FILE = 25 * 1024 * 1024
MAX_ZIP = 400 * 1024 * 1024
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SET_ID = re.compile(r"ps_[a-f0-9]{20}")
W, H = 1920, 1080
BATCH = 8                       # panels per vision call


def _natural(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _images_in(filename: str, data: bytes) -> list:
    """[(name, bytes)] from one uploaded file: an image, or a zip/cbz of images (in name order)."""
    ext = Path(filename or "").suffix.lower()
    if ext in IMAGE_EXT:
        if len(data) > MAX_FILE:
            raise ValueError(f"{Path(filename).name} is larger than 25 MB.")
        return [(Path(filename).name, data)]
    if ext in (".zip", ".cbz"):
        if len(data) > MAX_ZIP:
            raise ValueError("Keep a panel archive under 400 MB.")
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                name = info.filename
                if info.is_dir() or "__MACOSX" in name or Path(name).name.startswith("."):
                    continue
                if Path(name).suffix.lower() not in IMAGE_EXT:
                    continue
                if info.file_size > MAX_FILE:
                    raise ValueError(f"{Path(name).name} in the archive is larger than 25 MB.")
                out.append((name, archive.read(info)))
        return sorted(out, key=lambda r: _natural(r[0]))
    raise ValueError(f"{Path(filename or 'This file').name} is not a picture or a .zip/.cbz of pictures.")


def _pieces(img) -> list:
    """A tall webtoon strip cut at its blank gutters into panels; any other picture as it is."""
    w, h = img.size
    if h <= w * 1.6:
        return [img]
    import numpy as np
    g = np.asarray(img.convert("L").resize((max(1, w // 4), h)), dtype=np.float32)
    flat = (g.std(axis=1) < 4.0)                    # a row of one colour: a gutter
    pieces, start, run = [], 0, 0
    min_piece = max(int(w * 0.35), 120)
    for y in range(h):
        run = run + 1 if flat[y] else 0
        if run == 24 and y - 24 - start >= min_piece:  # a gutter at least 24 px tall ends a panel
            pieces.append((start, y - 24))
            start = y
    if h - start >= min_piece:
        pieces.append((start, h))
    if len(pieces) <= 1:
        # no gutters: cut into slices about one screen tall
        step = int(w * 1.1)
        pieces = [(y, min(h, y + step)) for y in range(0, h, step) if min(h, y + step) - y >= min_piece // 2]
    out = []
    for a, b in pieces:
        rows = flat[a:b]
        # trim the blank margins of each panel
        top = next((i for i, f in enumerate(rows) if not f), 0)
        bottom = len(rows) - next((i for i, f in enumerate(rows[::-1]) if not f), 0)
        if bottom - top >= min_piece // 2:
            out.append(img.crop((0, a + top, w, a + bottom)))
    return out or [img]


def save(files, root: Path = None) -> dict:
    """Save one upload ([(filename, bytes)]) as a panel set. Returns its summary."""
    from PIL import Image, ImageOps
    root = Path(root or ROOT)
    images = []
    for filename, data in files:
        images.extend(_images_in(filename, data))
    if not images:
        raise ValueError("Choose your panels: pictures, or a .zip/.cbz of pictures.")
    digest = hashlib.sha256()
    for name, data in images:
        digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
    set_id = "ps_" + digest.hexdigest()[:20]
    folder = root / set_id
    summary = folder / "set.json"
    if summary.is_file():
        return json.loads(summary.read_text(encoding="utf-8"))
    tmp = root / (set_id + ".tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, data in images:
        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        except Exception as e:                                        # noqa: BLE001 - say which file
            raise ValueError(f"{Path(name).name} could not be read as a picture.") from e
        for piece in _pieces(img):
            count += 1
            if count > MAX_PANELS:
                raise ValueError(f"Use up to {MAX_PANELS} panels in one video.")
            if max(piece.size) > 2400:
                piece.thumbnail((2400, 2400))
            piece.save(tmp / f"{count:04d}.jpg", quality=92)
    info = {"id": set_id, "count": count, "uploaded": len(images), "created": time.time(),
            "name": Path(images[0][0]).stem[:60] + (f" and {len(images) - 1} more" if len(images) > 1 else "")}
    (tmp / "set.json").write_text(json.dumps(info, indent=1))
    if folder.exists():
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        return json.loads(summary.read_text(encoding="utf-8"))
    tmp.rename(folder)
    return info


def panel_set(set_id: str, root: Path = None) -> dict:
    if not isinstance(set_id, str) or not SET_ID.fullmatch(set_id):
        raise ValueError("Upload your panels again.")
    folder = Path(root or ROOT) / set_id
    try:
        info = json.loads((folder / "set.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Those panels are not on this computer any more. Upload them again.") from None
    info["files"] = sorted(str(p) for p in folder.glob("[0-9][0-9][0-9][0-9].jpg"))
    return info


def recent(root: Path = None, limit: int = 12) -> list:
    folder = Path(root or ROOT)
    rows = []
    for f in folder.glob("ps_*/set.json") if folder.is_dir() else []:
        try:
            rows.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda r: -float(r.get("created") or 0))[:limit]


def _trim(img):
    """The panel without the page's plain margin around it: from each side inward, rows and columns that are almost
    all light (or all dark) go, so a faint line at the image's edge doesn't keep a white strip; never more than a third
    of a side, so the art itself is never cut away."""
    import numpy as np
    a = np.asarray(img.convert("L"), dtype=np.uint8)
    h, w = a.shape

    def margin(line) -> bool:
        return bool((line >= 200).mean() >= 0.97 or (line <= 40).mean() >= 0.97)
    top, bottom, left, right = 0, h, 0, w
    while top < h // 3 and margin(a[top]):
        top += 1
    while bottom > h - h // 3 and margin(a[bottom - 1]):
        bottom -= 1
    while left < w // 3 and margin(a[top:bottom, left]):
        left += 1
    while right > w - w // 3 and margin(a[top:bottom, right - 1]):
        right -= 1
    return img if (left, top, right, bottom) == (0, 0, w, h) else img.crop((left, top, right, bottom))


def frame(src: Path, dest: Path) -> Path:
    """The panel on a 16:9 frame: whole (its page margin trimmed), as large as fits, over a darkened, blurred copy."""
    from PIL import Image, ImageFilter, ImageEnhance
    img = _trim(Image.open(src).convert("RGB"))
    w, h = img.size
    scale = max(W / w, H / h)
    bg = img.resize((int(w * scale) + 1, int(h * scale) + 1))
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.45)
    fit = min((W * 0.94) / w, (H * 0.94) / h)
    fg = img.resize((max(1, int(w * fit)), max(1, int(h * fit))), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    bg.save(dest, quality=92)
    return dest


# ── reading the panels, and tying the narration to them (Claude, inside the video job) ──────────────────────

READ = """These are panels [A] to [B] of a comic chapter (manga, manhwa or webtoon), in reading order. For EACH panel,
say in one or two plain sentences what happens in it: who is there (use names when the comic gives them), what they
do or say (the gist of any speech bubble), and the feeling. Never invent anything a panel does not show.
Return ONLY a JSON array: [{"panel": <number>, "what": "<what happens>"}]"""

TIE = """Below is a recap narration and the comic panels it retells, in order (what each panel shows). Split the
narration into consecutive stretches, IN ORDER, and give each stretch the panel it describes. Copy the narration
WORD FOR WORD: laid end to end, the stretches are the whole narration with nothing skipped or changed. Panels go
forward only (never back); skip a panel the narration does not cover; a long stretch may span one panel only.
Return ONLY a JSON array: [{"panel": <number>, "text": "<the narration for it, copied exactly>"}]

PANELS:
[LOG]

NARRATION:
[SCRIPT]"""


def _json_list(text: str) -> list:
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", str(text or "").strip())
    try:
        value = json.loads(text)
    except ValueError:
        a, b = text.find("["), text.rfind("]")
        try:
            value = json.loads(text[a:b + 1]) if 0 <= a < b else []
        except ValueError:
            value = []
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def read_panels(files: list, see, cache: Path, log=print) -> list:
    """[{panel, what}] for every panel, read a few at a time; cached so a resumed job never reads twice."""
    saved = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
    rows = {int(r["panel"]): r for r in saved.get("panels", []) if isinstance(r, dict) and "panel" in r}
    for a in range(0, len(files), BATCH):
        batch = list(range(a + 1, min(len(files), a + BATCH) + 1))
        if all(n in rows for n in batch):
            continue
        answer = _json_list(see(READ.replace("[A]", str(batch[0])).replace("[B]", str(batch[-1])),
                                [files[n - 1] for n in batch]))
        for r in answer:
            try:
                n = int(r.get("panel"))
            except (TypeError, ValueError):
                continue
            if n in batch and str(r.get("what") or "").strip():
                rows[n] = {"panel": n, "what": str(r["what"]).strip()[:400]}
        for n in batch:
            rows.setdefault(n, {"panel": n, "what": ""})
        cache.write_text(json.dumps({"panels": [rows[k] for k in sorted(rows)]}, indent=1))
        log(f"  panels: read {batch[-1]}/{len(files)}")
    return [rows[k] for k in sorted(rows)]


def sources_block(log_rows: list) -> str:
    lines = "\n".join(f"Panel {r['panel']}: {r['what']}" for r in log_rows if r.get("what"))
    return ("\n\nTHE CHAPTER'S PANELS, in reading order (what each one shows):\n" + lines +
            "\n\nRetell exactly this story, in this order, as the narration of the video. Every event, name and "
            "line of dialogue comes from these panels; never invent what they do not show. The pictures will be "
            "these panels, so the narration follows them from the first to the last.\n")


def tie(script: str, log_rows: list, ask) -> list:
    """[{panel, text}] in order, every word of the script once (checked; falls back to an even split)."""
    words = script.split()
    log_text = "\n".join(f"Panel {r['panel']}: {r['what']}" for r in log_rows)
    rows = _json_list(ask(TIE.replace("[LOG]", log_text[:60000]).replace("[SCRIPT]", script)))
    out, last = [], 0
    for r in rows:
        try:
            n = int(r.get("panel"))
        except (TypeError, ValueError):
            continue
        text = " ".join(str(r.get("text") or "").split())
        if n >= last and 1 <= n <= len(log_rows) and text:
            out.append({"panel": n, "text": text})
            last = n
    joined = " ".join(r["text"] for r in out).split()
    norm = lambda ws: [re.sub(r"[^a-z0-9]", "", w.lower()) for w in ws]
    if not out or abs(len(joined) - len(words)) > max(8, len(words) * 0.08) or \
            sum(1 for a, b in zip(norm(joined), norm(words)) if a == b) < len(words) * 0.85:
        # the model did not copy the narration faithfully: spread it over the panels evenly instead
        n = len(log_rows)
        per = max(1, len(words) // max(1, n))
        out = []
        for i in range(n):
            chunk = words[i * per:(i + 1) * per] if i < n - 1 else words[i * per:]
            if chunk:
                out.append({"panel": i + 1, "text": " ".join(chunk)})
    return out


# ── wiring into the engine ──────────────────────────────────────────────────────────────────────────────────

def job_panels(job) -> dict:
    """The panel set a job was started with ({} for every other job)."""
    try:
        spec = json.loads((Path(job) / "run_spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    set_id = spec.get("cr_panels") if isinstance(spec, dict) else None
    if not set_id:
        return {}
    return panel_set(set_id)


def visuals(engine, script: str, job, log=print) -> tuple:
    """The job's stills from its panels: each framed, each on the narration that tells it."""
    job = Path(job)
    ps = job_panels(job)
    files = ps["files"]
    rows = read_panels(files, lambda p, imgs: engine.claude_vision(p, imgs, max_tokens=4000),
                       job / "panel_log.json", log)
    tied_f = job / "panel_story.json"
    tied = json.loads(tied_f.read_text(encoding="utf-8")) if tied_f.is_file() else None
    if not tied:
        tied = tie(script, rows, lambda prompt: engine.claude(prompt, engine.SCRIPT_MODEL, max_tokens=16000,
                                                              provider=getattr(engine, "SCRIPT_PROVIDER", None)))
        tied_f.write_text(json.dumps(tied, indent=1))
    img_dir = job / "images"
    items, photos = [], []
    for k, r in enumerate(tied):
        dest = img_dir / f"scene_{k:02d}.jpg"
        if not dest.exists():
            frame(Path(files[r["panel"] - 1]), dest)
        items.append({"text": r["text"], "prompt": f"panel {r['panel']}", "panel": r["panel"]})
        photos.append(dest)
    (job / "astro_scenes.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    log(f"panels: {len(photos)} of your {len(files)} panels, each on the words that tell it")
    return photos, []


def install(engine):
    """Panel jobs read their panels for the script and show them as the pictures."""
    if getattr(engine, "_cr_panels", False):
        return
    engine._cr_panels = True
    original_sources = engine._sources_block
    original_visuals = engine.generate_astrology_visuals

    def sources_block_(job, title):
        block = original_sources(job, title)
        if not job_panels(job):
            return block
        rows = read_panels(job_panels(job)["files"], lambda p, imgs: engine.claude_vision(p, imgs, max_tokens=4000),
                           Path(job) / "panel_log.json", engine.log)
        return block + sources_block(rows)

    def visuals_(script, job, force, style="", *args, **kwargs):
        if job_panels(job):
            return visuals(engine, script, job, engine.log)
        return original_visuals(script, job, force, style, *args, **kwargs)
    visuals_._cr_style = getattr(original_visuals, "_cr_style", False)
    engine._sources_block = sources_block_
    engine.generate_astrology_visuals = visuals_
