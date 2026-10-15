"""Your own AI presenter: a photo of the person who talks to camera in your videos.

The presenter library (avatar.py, assets/avatars/avatars.json) ships with Studio.
A creator's own presenters live beside it in assets/avatars/my/ (their own list,
so an update never touches them) and are listed first. The lip-synced talking
shots (Kling avatar on Kie) and the pictures of the presenter doing what is said
use the photo exactly as for a built-in presenter.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

HOME = Path(__file__).resolve().parent
MY_DIR = HOME / "assets" / "avatars" / "my"
MANIFEST = MY_DIR / "my.json"
MAX_PRESENTERS = 30


def rows(folder: Path = None) -> list:
    folder = Path(folder or MY_DIR)
    try:
        data = json.loads((folder / "my.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for r in data if isinstance(data, list) else []:
        f = folder / str(r.get("file") or "")
        if isinstance(r, dict) and r.get("id") and f.is_file() and f.parent == folder:
            out.append({"id": r["id"], "gender": r.get("gender", ""), "niches": r.get("niches") or [],
                        "file": str(f), "persona": r.get("persona", ""), "room": r.get("room", ""), "mine": True,
                        "name": r.get("name", "")})
    return out


def save(data: bytes, *, name: str, gender: str, persona: str = "", room: str = "", folder: Path = None) -> dict:
    """Add a presenter from a photo. Returns its row."""
    from PIL import Image, ImageOps
    folder = Path(folder or MY_DIR)
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("Give your presenter a name.")
    if gender not in ("male", "female"):
        raise ValueError("Choose the voice your presenter speaks with: male or female.")
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Use a photo under 20 MB.")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:                                            # noqa: BLE001
        raise ValueError("That file could not be read as a photo.") from e
    if min(img.size) < 400:
        raise ValueError("Use a sharper photo: at least 400 pixels on its short side.")
    img.thumbnail((1600, 1600))
    existing = rows(folder)
    if len(existing) >= MAX_PRESENTERS:
        raise ValueError(f"You have {MAX_PRESENTERS} presenters. Remove one before adding another.")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "presenter"
    ident = f"my-{slug}-{hashlib.sha256(data).hexdigest()[:6]}"
    folder.mkdir(parents=True, exist_ok=True)
    img.save(folder / f"{ident}.png")
    persona = " ".join(str(persona or "").split())[:600] or \
        f"{name}, exactly as they look in the reference photo: the same face, hair, age and clothes"
    row = {"id": ident, "name": name, "gender": gender, "niches": [], "file": f"{ident}.png", "persona": persona,
           "room": " ".join(str(room or "").split())[:400]}
    manifest = [r for r in _raw(folder) if r.get("id") != ident] + [row]
    tmp = folder / "my.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / "my.json")
    return next(r for r in rows(folder) if r["id"] == ident)


def remove(ident: str, folder: Path = None) -> bool:
    folder = Path(folder or MY_DIR)
    manifest = _raw(folder)
    keep = [r for r in manifest if r.get("id") != ident]
    if len(keep) == len(manifest):
        return False
    (folder / "my.json").write_text(json.dumps(keep, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / f"{ident}.png").unlink(missing_ok=True)
    return True


def _raw(folder: Path) -> list:
    try:
        data = json.loads((folder / "my.json").read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def install():
    """List the creator's presenters first in Studio's presenter library."""
    try:
        import avatar
    except Exception:                                                   # noqa: BLE001 - no presenter module here
        return
    if getattr(avatar.library, "_cr_mine", False):
        return
    original = avatar.library

    def library(gender: str = "") -> list:
        g = (gender or "").strip().lower()
        mine = [r for r in rows() if g not in ("male", "female") or r["gender"] == g]
        return mine + original(gender)
    library._cr_mine = True
    avatar.library = library
