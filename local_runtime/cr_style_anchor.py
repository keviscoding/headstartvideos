"""Pick the picture style every video keeps: samples drawn in the learned style, and a character sheet.

A learned style's pictures follow its written prompt; words alone let the look
drift from one picture to the next. Studio's image step can also copy the style
of one drawing on every picture (look.style_image) and keep a character's face
from one picture (look.character_image). This module draws three samples of the
learned style (each from a reference frame, never copying its subject), lets
the creator choose one, and draws a character sheet in that style for the
channel's recurring character or host.

Paid pictures on the creator's Kie account (about $0.02 each), only after the
creator approves; Studio's image routing records every task, so nothing is
generated twice.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
# Ordinary full-frame scenes in plain modern settings. The image model copies a reference's composition as well as
# its style: a split "style sheet" made every later picture half blank, and a sample with an arm reaching in from the
# edge filled a video with disembodied arms. So samples are whole, ordinary, complete pictures, people fully in frame.
SUBJECTS = [
    "A full-frame everyday scene: three people in plain modern clothes talking on an ordinary street corner with a "
    "tree and a bench, daytime, everyone fully in frame from head to toe. No text, no logos.",
    "A full-frame everyday scene: one person in plain modern clothes sitting at a kitchen table with a mug, a window "
    "behind them, the whole person in frame. No text, no logos.",
    "A full-frame everyday scene: two people in plain modern clothes in a simple living room, one surprised and one "
    "explaining, both fully in frame. No text, no logos.",
]
SHEET = ("A character sheet on a plain light background: the same character three times, front view, three-quarter "
         "view and full body, neutral pose, no text, no labels. The character: ")
STYLE_NOTE = ("STYLE REFERENCE: the reference image shows the exact drawing style to copy: the same kind of line, "
              "shading, colour and finish. Do NOT copy its subject, characters, era, setting, text or logos; draw the "
              "subject described below in that style, with people in plain modern clothes and no historical, "
              "religious or cultural symbols. ")
_jobs = {}
_lock = threading.Lock()


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _learned(home, operation_id):
    """(operation folder, saved operation, style path, style) for a saved v2 study."""
    import cr_style_clone
    folder = cr_style_clone._operation(home, operation_id)
    saved = _read(folder / "operation.json")
    style_path = Path(home) / "styles" / (saved.get("spec", {}).get("style_id", "") + ".json")
    style = _read(style_path)
    learned = (style.get("cr_style_clone") or {}).get("learned_v2") or {}
    if not saved or not style or style.get("cr_style_clone", {}).get("operation_id") != folder.name or not learned:
        raise RuntimeError("Choose a style saved by a recent style study.")
    return folder, saved, style_path, style


def _model(style) -> dict:
    """{"model": the style's picture model} so samples are drawn the way its videos will be, or {}."""
    model = str((style.get("look") or {}).get("image_model") or "")
    return {"model": model} if model else {}


def _frames(home, style) -> list:
    import cr_style_look
    look_id = ((style.get("cr_style_clone") or {}).get("learned_v2") or {}).get("look_id")
    if not look_id:
        return []
    return sorted(cr_style_look.folder(home, look_id).glob("frames/*.jpg"))


def _wants_upload(saved: dict) -> bool:
    """The creator answered that they will upload their own host or mascot."""
    return str(((saved.get("spec") or {}).get("answers") or {}).get("host") or "").startswith("Yes, I'll upload")


def status(home, operation_id) -> dict:
    folder, saved, _, style = _learned(home, operation_id)
    state = _read(folder / "anchors" / "anchors.json")
    with _lock:
        running = operation_id in _jobs
    look = style.get("look") or {}
    return {"state": "running" if running else state.get("state", "none"), "message": state.get("message", ""),
            "samples": [i for i, _ in enumerate(state.get("samples") or []) if (folder / "anchors" /
                                                                                    f"sample_{i}.jpg").is_file()],
            "chosen": state.get("chosen"), "character": bool(look.get("character_image")),
            "has_cast": bool(str(look.get("character") or "").strip()),
            "style_image": bool(look.get("style_image")), "cost_usd": round(0.02 * (len(SUBJECTS) + 1), 2),
            "upload_character": _wants_upload(saved), "offered": not _coded(style)}


def _coded(style) -> bool:
    """A style written in code: its renderer draws every picture, so a picture every picture copies has no use (and
    would push AI pictures on a coded style)."""
    return ((style or {}).get("look") or {}).get("visuals") in ("cr_animated2d", "cr_animated3d")


def start(home, operation_id, *, generate=None, upload=None, thread=True) -> dict:
    """Draw the samples (approved by the caller). Runs in the background unless thread=False."""
    folder, _, _, style = _learned(home, operation_id)
    if _coded(style):
        raise RuntimeError("This style is written in code: its own animation draws every picture, so it has no picture "
                           "style to choose. Improve it on its page instead.")
    frames = _frames(home, style)
    if not frames:
        raise RuntimeError("This style was saved without reference frames. Look at the references again to make samples.")
    with _lock:
        if operation_id in _jobs:
            return status(home, operation_id)
        _jobs[operation_id] = True

    def work():
        try:
            import cr_media
            gen = generate or cr_media.image
            put = upload or (lambda path: cr_media.upload(path, task="images"))
            suffix = str((style.get("look") or {}).get("still_suffix") or "")
            refs = [put(frames[min(k, len(frames) - 1)]) for k in range(len(SUBJECTS))]
            _write(folder / "anchors" / "anchors.json", {"state": "running", "message": "Drawing three samples.",
                                                         "samples": SUBJECTS})
            model = _model(style)
            for k, subject in enumerate(SUBJECTS):
                gen(STYLE_NOTE + subject + suffix, folder / "anchors" / f"sample_{k}.jpg", f"style sample {k + 1}",
                    ref_urls=[refs[k]], **model)
            _write(folder / "anchors" / "anchors.json", {"state": "done", "samples": SUBJECTS, "at": time.time(),
                                                         "message": "Choose the sample that looks most like your channel."})
        except BaseException as exc:                            # noqa: BLE001 - shown to the creator, nothing retried
            _write(folder / "anchors" / "anchors.json", {"state": "failed", "samples": SUBJECTS,
                                                         "message": "The samples stopped: " + str(exc)[:200]})
        finally:
            with _lock:
                _jobs.pop(operation_id, None)
    if thread:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()
    return status(home, operation_id)


def choose(home, operation_id, index: int, *, generate=None, upload=None) -> dict:
    """Keep one sample as the style's drawing, and draw the character sheet in it when the style has a cast."""
    folder, saved, style_path, style = _learned(home, operation_id)
    sample = folder / "anchors" / f"sample_{int(index)}.jpg"
    if not isinstance(index, int) or isinstance(index, bool) or not sample.is_file():
        raise ValueError("Choose one of the drawn samples.")
    name = re.sub(r"[^a-z0-9_-]", "", style["name"])
    assets = Path(home) / "assets"
    target = assets / "styles" / "learned" / name / "style.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sample, target)
    style.setdefault("look", {})["style_image"] = target.relative_to(assets).as_posix()
    cast = str(style["look"].get("character") or "").strip()
    # the sheet draws the first recurring character (the host or mascot), not a crowd
    first = re.split(r"\n|(?<=\.)\s+(?=[A-Z][\w' -]{1,40}(?: \([^)]*\))?:)", re.sub(r"^THE CAST\s*[—-]\s*", "", cast))[0]
    if first.strip():
        cast = first.strip()
    if cast and not _wants_upload(saved):         # a creator who uploads their own character gets no drawn sheet
        import cr_media
        gen = generate or cr_media.image
        put = upload or (lambda path: cr_media.upload(path, task="images"))
        sheet = target.with_name("character.png")
        gen(STYLE_NOTE + SHEET + cast[:1500], sheet, "character sheet", ref_urls=[put(target)], **_model(style))
        style["look"]["character_image"] = sheet.relative_to(assets).as_posix()
    _write(style_path, style)
    import cr_style_clone
    saved["style_sha256"] = cr_style_clone._file_hash(style_path)      # the study still owns its saved style
    cr_style_clone._write(folder / "operation.json", saved)
    state = _read(folder / "anchors" / "anchors.json")
    state.update(chosen=int(index), message="Every picture in this style now copies the drawing you chose."
                 + (" Now upload your character." if _wants_upload(saved) and not style["look"].get("character_image")
                    else ""))
    _write(folder / "anchors" / "anchors.json", state)
    return status(home, operation_id)


def upload_character(home, operation_id, data: bytes) -> dict:
    """The creator's own host or mascot as the style's character reference: every picture keeps them."""
    from PIL import Image, ImageOps
    import io
    folder, saved, style_path, style = _learned(home, operation_id)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Use a picture under 20 MB.")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGBA")
    except Exception as e:                                              # noqa: BLE001
        raise ValueError("That file could not be read as a picture.") from e
    if min(img.size) < 256:
        raise ValueError("Use a larger picture of your character: at least 256 pixels on its short side.")
    img.thumbnail((1600, 1600))
    name = re.sub(r"[^a-z0-9_-]", "", style["name"])
    assets = Path(home) / "assets"
    target = assets / "styles" / "learned" / name / "character.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target)
    look = style.setdefault("look", {})
    look["character_image"] = target.relative_to(assets).as_posix()
    who = re.match(r"(?:THE CAST\s*[—-]\s*)?([A-Z][\w' -]{1,40}?)\s*(?:\(|:)", str(look.get("character") or ""))
    label = who.group(1).strip() if who else "The host"
    look["character"] = (f"THE CAST — {label}: exactly the character in the character reference image, with the same "
                         "face, body, colours and clothes in every picture they appear in.")
    _write(style_path, style)
    import cr_style_clone
    saved["style_sha256"] = cr_style_clone._file_hash(style_path)
    cr_style_clone._write(folder / "operation.json", saved)
    state = _read(folder / "anchors" / "anchors.json")
    state.update(message="Your character is in this style now: every picture keeps them.")
    _write(folder / "anchors" / "anchors.json", state)
    return status(home, operation_id)
