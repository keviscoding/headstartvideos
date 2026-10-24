"""A recipe's character, changed for one video: describe a new one, or upload a picture of them.

How the engine decides what a story recipe's character looks like (make_video.py):
- **the words**: the recipe's cast (look.character) goes into the storyboard prompt with "copy it WORD FOR WORD into
  every image prompt". Create's Character box replaces those words for one video (job spec `character`).
- **the picture**: the recipe's character picture (look.character_image) rides along as the reference on EVERY still,
  after "take their face, hair, skin, clothes and build from it". A picture picked for the video (job spec
  `character_image`, a file in assets/characters) replaces it.

The picture is what keeps a face the same from one still to the next. A creator who wanted another character could
change the words (the Character box, or Extra instructions: the storyboard follows them), but the recipe's picture of
its OLD character stayed attached to every still, so the image model had two different people to follow and nothing
kept the new face the same (Discord, 2026-10-24: "I tried this but it didn't work"). Tested on 2026-10-24 with Kie's
nano-banana edit on 2D Stories and Stick Figure Stories (character-fix/live-2026-10-24): clear new words won over the
old picture in single stills, so the old picture is not always the reason a change fails. Shorts hosts and 3D figures
are built another way (local_runtime/CLAUDE.md).

Now new words without a picture of their own draw a character sheet from those words, once, in the recipe's own
drawing style, and that sheet goes with every still (one picture, about $0.02 on the creator's image account; the same
words and recipe reuse it). Only recipes that carry a character picture are changed: elsewhere the words already
decide. Uploaded pictures are saved beside the engine's own (assets/characters) with words that point at the picture.
"""
from __future__ import annotations

import hashlib
import io
import re
import threading
from pathlib import Path

HOME = Path(__file__).resolve().parent
EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
MAX_WORDS = 4000                   # the engine keeps the first 4,000 characters of a video's cast
SHEET = ("A character sheet on a plain light background: the same character three times, front view, three-quarter "
         "view and full body, neutral pose, no text, no labels. The character: ")
# The reference is the recipe's drawing (its style drawing, else its own character picture): the image model copies
# a reference's subject unless it is told twice not to.
STYLE_NOTE = ("STYLE REFERENCE: the reference image shows the exact drawing style to copy: the same kind of line, "
              "shading, colour, proportions and finish. Do NOT copy its character, face, hair, clothes, setting or "
              "text; draw the NEW character described below in that style. ")
UPLOADED = ("THE CAST: the main character is exactly the character in the character reference image, with the same "
            "face, hair, body, colours and clothes in every picture they appear in.")
_lock = threading.Lock()


def norm(text) -> str:
    """Words compared without case, spacing or dash and quote styles (a textarea turns them into its own)."""
    text = "\n".join(text) if isinstance(text, (list, tuple)) else str(text or "")
    text = text.translate(str.maketrans({"—": "-", "–": "-", "‘": "'", "’": "'",
                                         "“": '"', "”": '"'}))
    return " ".join(text.lower().split())


def is_new(words, own) -> bool:
    """Whether a video's character words replace the recipe's own (the textarea keeps only the first 4,000)."""
    words = norm(words)
    if not words:
        return False
    own = "\n".join(own) if isinstance(own, (list, tuple)) else str(own or "")
    return words not in (norm(own), norm(own[:MAX_WORDS]))


def lead_words(text) -> str:
    """The words that name the character, for a file name: without the engine's cast preamble."""
    text = re.sub(r"^\s*(THE CAST\s*[:—-]\s*|THE MAIN CHARACTER is\s+)", "", str(text or ""), flags=re.I)
    return " ".join(re.findall(r"[a-z0-9]+", text.lower())[:6])


def _stem(text, fallback="character") -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40].strip("-") or fallback


def sheet_path(home, style, words) -> Path:
    """Where a drawn sheet for these words in this recipe lives (assets/characters, so Create lists it too)."""
    digest = hashlib.sha1((str(style) + "\n" + norm(words)).encode("utf-8")).hexdigest()[:6]
    return Path(home) / "assets" / "characters" / f"{_stem(lead_words(words), 'my-character')}-{digest}.png"


def _asset(home, rel):
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        return None
    path = Path(rel) if Path(rel).is_absolute() else Path(home) / "assets" / rel
    return path if path.is_file() else None


def style_reference(home, look: dict):
    """The picture whose drawing style a new character is drawn in: the recipe's style drawing, else its own
    character picture, else the frame it aims at. None when it has none."""
    for key in ("style_image", "character_image", "reference"):
        path = _asset(home, (look or {}).get(key))
        if path is not None:
            return path
    return None


def item(home, path) -> dict:
    """A saved character as Create lists it (the engine's /api/characters shape)."""
    path = Path(path)
    side = path.with_suffix(".txt")
    return {"rel": f"characters/{path.name}", "file": path.name, "src": f"/charimg/{path.name}",
            "label": re.sub(r"-[0-9a-f]{6}$", "", path.stem).replace("-", " ").strip() or "character",
            "channel": "", "mtime": int(path.stat().st_mtime) if path.exists() else 0,
            "text": side.read_text(encoding="utf-8").strip() if side.is_file() else ""}


def draw(home, style, words, look, *, generate, host, log=print) -> Path:
    """The character sheet for these words, drawn once in the recipe's style. `generate(prompt, dest, label,
    ref_urls=[...])` draws one picture; `host(path)` puts a reference online and returns its link."""
    words = str(words or "").strip()[:MAX_WORDS]
    if not words:
        raise ValueError("Describe the character first.")
    dest = sheet_path(home, style, words)
    with _lock:                                          # a double click or two stills never pay twice
        if dest.is_file():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        ref = style_reference(home, look)
        url = host(ref) if ref is not None else ""
        suffix = " " + str((look or {}).get("still_suffix") or "").strip()
        prompt = (STYLE_NOTE if url else "") + SHEET + words[:1500] + suffix.rstrip()
        part = dest.with_name(dest.stem + ".part.png")
        try:
            generate(prompt, part, "character sheet", ref_urls=[url] if url else [])
            if not part.is_file() or part.stat().st_size < 1000:
                raise RuntimeError("the picture service returned no picture")
            dest.with_suffix(".txt").write_text(words + "\n", encoding="utf-8")
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)
        log(f"  character: drew {dest.name} from your description, in this recipe's style")
        return dest


def save_upload(home, data: bytes, filename: str = "", words: str = "") -> Path:
    """A picture of the creator's own character, saved beside the engine's (assets/characters) with its words: the
    creator's, else words that point at the picture."""
    from PIL import Image, ImageOps
    if not data:
        raise ValueError("Choose a picture of your character.")
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("That picture is over 20 MB. Export a smaller one.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as error:                                     # noqa: BLE001
        raise ValueError("Studio can't read that file as a picture. Use PNG, JPG or WebP.") from error
    if min(img.size) < 256:
        raise ValueError("Use a larger picture of your character: at least 256 pixels on its short side.")
    img = img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img
    img.thumbnail((1600, 1600))                                    # plenty for a reference, quick to put online
    folder = Path(home) / "assets" / "characters"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{_stem(Path(filename or '').stem)}-{hashlib.sha1(data).hexdigest()[:6]}.png"
    img.save(dest, "PNG", optimize=True)
    words = str(words or "").strip()[:MAX_WORDS]
    dest.with_suffix(".txt").write_text((words or UPLOADED) + "\n", encoding="utf-8")
    return dest


def replaces_cast(engine, style) -> bool:
    """This video's words replace its recipe's cast: they match neither the look it runs in nor the recipe Create
    showed (a variant or a look designed for the title runs under its own name, from its base recipe)."""
    info = (getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}
    names = [style, info.get("variant_of"), (info.get("auto") or {}).get("base") if isinstance(info.get("auto"), dict) else None]
    casts = getattr(engine, "STYLE_CHARACTER", {}) or {}
    words = getattr(engine, "_CHARACTER", "")
    return all(is_new(words, casts.get(name)) for name in names if name) and bool(norm(words))


def wants_drawn(engine, style) -> bool:
    """This video describes a new character, picked no picture for them, and its recipe carries a picture of its OLD
    character that would otherwise go with every still."""
    if str(getattr(engine, "_CHARACTER_IMAGE", "") or "").strip() or getattr(engine, "_CAST_FROM_AVATAR", False):
        return False
    if not (getattr(engine, "STYLE_CHARACTER_IMAGE", {}) or {}).get(style):
        return False
    return replaces_cast(engine, style)


def _look(engine, style) -> dict:
    look = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}).get("look") or {}
    return look if isinstance(look, dict) else {}


def install(engine, home=None):
    """The engine's still step asks character_image_path(style) for the picture and _character_refs(style) for its
    link: a video with new words and no picture of its own gets its drawn sheet from both."""
    home = Path(home or getattr(engine, "HERE", HOME))
    original_path = getattr(engine, "character_image_path", None)
    original_refs = getattr(engine, "_character_refs", None)
    if original_path is None or original_refs is None or getattr(original_path, "_cr_character", False):
        return
    say = lambda line: getattr(engine, "log", print)(line)

    def character_image_path(style):
        if wants_drawn(engine, style):
            # drawn when the stills ask for its link, so a video whose stills are all made draws nothing
            return sheet_path(home, style, engine._CHARACTER)
        return original_path(style)

    def _character_refs(style):
        if not wants_drawn(engine, style):
            return original_refs(style)
        try:
            path = draw(home, style, engine._CHARACTER, _look(engine, style),
                        generate=lambda prompt, dest, label, ref_urls: engine.image_gen(
                            prompt, dest, label, ref_urls=ref_urls, kie_create_fn=getattr(engine, "_kie_create", None),
                            stills=True),
                        host=engine.host_image, log=say)
        except BaseException as error:                  # noqa: BLE001 - the stills still follow the words
            if isinstance(error, KeyboardInterrupt):
                raise
            say(f"  ⚠ your character couldn't be drawn ({str(error)[:120]}): the stills follow your description "
                "alone, without the recipe's picture")
            return []
        url = engine.host_image(path)
        return [url] if url else []

    character_image_path._cr_character = True
    engine.character_image_path = character_image_path
    engine._character_refs = _character_refs
    _with_threed(engine)


def _with_threed(engine):
    """A 3D scene's figures come from the 3D director's cast, which hears the creator's extra instructions but not
    the cast: inside a video's 3D work (cr_threed_look knows the job) a new character is added to them."""
    original = getattr(engine, "_extra_block", None)
    if original is None or getattr(original, "_cr_character", False):
        return

    def _extra_block():
        out = original() or ""
        try:
            import cr_threed_look
            job = cr_threed_look._job()
        except Exception:                                # noqa: BLE001 - no 3D here
            job = None
        style = str(getattr(engine, "_STYLE_NOW", "") or "")
        words = str(getattr(engine, "_CHARACTER", "") or "").strip()
        if not job or not words or getattr(engine, "_CAST_FROM_AVATAR", False) or "THE MAIN CHARACTER OF THIS VIDEO" in out:
            return out
        if not replaces_cast(engine, style):
            return out
        line = ("THE MAIN CHARACTER OF THIS VIDEO (the creator chose them for this video: they replace the channel's "
                "usual main character and outfit in every scene, as closely as the 3D figures allow): " + words[:1500])
        return (out.rstrip("\n") + "\n" + line + "\n") if out else (
            "\n\nEXTRA INSTRUCTIONS FROM THE CREATOR FOR THIS VIDEO — follow them wherever they apply:\n" + line + "\n")
    _extra_block._cr_character = True
    engine._extra_block = _extra_block
