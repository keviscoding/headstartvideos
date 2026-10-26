"""Edit a learned style after it's saved, without studying the channel again.

A creator asked whether a learned recipe can be changed or has to be deleted and made again. What a style's videos
read from its file can be changed here: its name, its description (what the channel's videos are about, also in its
writer's prompts), the creative direction the writer and the director follow (script_notes and director_notes, the
notes the engine gives them), its recurring character's words, and the options Create starts from (which switches
are on, pictures a minute, subtitles). Each save keeps the version before it (output/cr-style-edits), and the study
that saved the style keeps owning it. Studying it again with other references is a new study: a new style, this one
kept as it is.

No model, no paid service: a file is edited.
"""
from __future__ import annotations

import copy
import json
import re
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

_STYLE = re.compile(r"cr_clone_[a-z0-9_-]{1,40}\Z")
MAX_NOTES = 10                       # the engine reads the first ten of each (make_video._script_craft, director.py)
MAX_NOTE = 400
FEATURE_LABELS = {"youtube": "Real footage (YouTube)", "stock": "Stock footage", "ai_images": "Generated pictures",
                  "depth": "Cut-out movement", "objects": "Objects and cut-outs", "motion": "Motion graphics",
                  "vox": "Explainer graphics", "maps": "Maps", "spotlight": "Photos of who and what is named",
                  "headlines": "Articles and screenshots", "sound": "Sound design", "avatar": "A presenter on camera",
                  "threed": "3D scenes"}
STILL_FINISHING = ("The study that made this style is still finishing it (checking how close its test pictures come "
                   "to your references). Edit it in a few minutes; nothing was changed.")


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _path(home, name) -> Path:
    if not isinstance(name, str) or not _STYLE.fullmatch(name):
        raise ValueError("Choose one of your learned styles.")
    root = (Path(home) / "styles").resolve()
    path = root / (name + ".json")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise LookupError("That learned style isn't installed.")
    return path


def _style(home, name) -> tuple:
    path = _path(home, name)
    style = _read(path)
    if not isinstance(style.get("cr_style_clone"), dict) or style.get("name") != name:
        raise ValueError("Only styles learned from a channel can be edited here.")
    return path, style


def _notes(value) -> list:
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        raise ValueError("Write the creative direction one note per line.")
    lines = [" ".join(str(x).split()) for x in value if str(x).strip()]
    if len(lines) > MAX_NOTES or any(len(x) > MAX_NOTE for x in lines):
        raise ValueError(f"Keep the creative direction to {MAX_NOTES} notes of up to {MAX_NOTE} characters each.")
    return lines


def _pictures(style) -> dict | None:
    """The pictures a minute this style may draw: around the pace it was learned at (kept on the first edit), so
    edit after edit never drifts below its quality floor."""
    import cr_media
    pacing = style.get("pacing") or {}
    learned = pacing.get("learned_photo_every_s") or pacing.get("photo_every_s")
    span = cr_media.picture_range({**style, "pacing": {**pacing, "photo_every_s": learned}})
    if not span:
        return None
    every = float(pacing.get("photo_every_s") or 0)
    return {"floor": span[0], "default": span[1], "ceiling": span[2], "value": round(60.0 / every, 1) if every else span[1]}


def _study(home, style) -> dict:
    """What the study that made this style was given, for studying it again with other references."""
    import cr_style_clone
    clone = style.get("cr_style_clone") or {}
    operation = str(clone.get("operation_id") or "")
    try:
        spec = _read(cr_style_clone._operation(home, operation) / "operation.json").get("spec") or {}
    except RuntimeError:
        spec = {}
    return {"operation_id": operation if spec else "", "title": spec.get("title", ""), "brief": spec.get("brief", ""),
            "base_style": spec.get("base_style") or clone.get("base_style") or "",
            "reference_urls": spec.get("reference_urls") or ([spec["reference_url"]] if spec.get("reference_url") else []),
            "visual_mode": spec.get("visual_mode") or clone.get("visual_mode") or "mixed"}


def editable(home, name) -> dict:
    """What can be changed in a learned style, as it is now."""
    path, style = _style(home, name)
    look = style.get("look") if isinstance(style.get("look"), dict) else {}
    features = style.get("features") if isinstance(style.get("features"), dict) else {}
    offered = [k for k in features.get("offered") or [] if isinstance(k, str)]
    on = features.get("on") if isinstance(features.get("on"), list) else offered
    history = Path(home) / "output" / "cr-style-edits" / name
    return {"style": name, "name": str(style.get("label") or name), "tagline": str(style.get("tagline") or ""),
            "description": str(style.get("description") or ""),
            "script_notes": [str(x) for x in style.get("script_notes") or []][:MAX_NOTES],
            "director_notes": [str(x) for x in style.get("director_notes") or []][:MAX_NOTES],
            "character": str(look.get("character") or ""), "has_character_picture": bool(look.get("character_image")),
            "features": {"offered": [{"key": k, "label": FEATURE_LABELS.get(k, k)} for k in offered],
                         "on": [k for k in on if k in offered]},
            "pictures": _pictures(style), "subtitles": not (look.get("captions") or {}).get("off", False),
            "study": _study(home, style), "versions_kept": len(list(history.glob("*.json"))) if history.is_dir() else 0,
            "use_url": "/?cr-preset=" + name}


@contextmanager
def _study_done(home, style):
    """Held while an edit is saved: the study that saved this style holds the same lock while it checks the likeness
    and records completion, then writes its record (with the style's hash) from what it read before. An edit in
    between would be lost and the style no longer the study's, so it waits until the study is done."""
    import cr_style_clone
    try:
        folder = cr_style_clone._operation(home, str((style.get("cr_style_clone") or {}).get("operation_id") or ""))
    except RuntimeError:
        folder = None
    with ExitStack() as held:
        if folder is not None and folder.is_dir():
            try:
                held.enter_context(cr_style_clone._lock(folder))
            except RuntimeError:
                raise RuntimeError(STILL_FINISHING) from None
        yield


def save(home, name, changes: dict) -> dict:
    """Apply the creator's changes to a learned style. The previous version is kept; nothing else is touched."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Change something before saving.")
    with _study_done(home, _style(home, name)[1]):
        return _save(home, name, changes)


def _save(home, name, changes: dict) -> dict:
    import cr_style_clone
    path, style = _style(home, name)                         # as the study left it
    before = cr_style_clone._file_hash(path)
    out = copy.deepcopy(style)
    look = out.setdefault("look", {})
    if "name" in changes:
        label = " ".join(str(changes["name"] or "").split())
        if not 1 <= len(label) <= 80:
            raise ValueError("Give the style a name of up to 80 characters.")
        out["label"] = label
    if "tagline" in changes:
        out["tagline"] = " ".join(str(changes["tagline"] or "").split())[:160]
    if "description" in changes:
        text = " ".join(str(changes["description"] or "").split())
        if not 1 <= len(text) <= 600:
            raise ValueError("Describe what the channel's videos are about in up to 600 characters.")
        old = str(style.get("description") or "")
        # the writer's prompts carry the description as the channel's own words: they follow it too
        if old and isinstance(out.get("prompts"), dict):
            out["prompts"] = {k: v.replace(old, text) if isinstance(v, str) else v for k, v in out["prompts"].items()}
        out["description"] = text
    for key in ("script_notes", "director_notes"):
        if key in changes:
            out[key] = _notes(changes[key])
    if "character" in changes:
        words = str(changes["character"] or "").strip()
        if len(words) > 3000:
            raise ValueError("Keep the character's description under 3,000 characters.")
        look["character"] = words
    if changes.get("forget_character_picture") is True:
        look.pop("character_image", None)          # the file stays; new words are drawn from the words alone
    if "features_on" in changes:
        features = out.setdefault("features", {})
        offered = [k for k in features.get("offered") or [] if isinstance(k, str)]
        wanted = changes["features_on"]
        if not isinstance(wanted, list) or any(k not in offered for k in wanted):
            raise ValueError("Choose options this style offers: " + ", ".join(offered) + ".")
        # a switch the style turns on without offering it (its look's own choice, as autostyle adds AI pictures) is
        # never shown here, so it keeps its state: only the offered ones are the creator's to change, and the ticks
        # shown, sent back as they were, change nothing
        now = features.get("on") if isinstance(features.get("on"), list) else offered
        if set(wanted) != {k for k in now if k in offered}:
            for key in ("on", "channel_on"):
                if key == "on" or isinstance(features.get(key), list):
                    was = features.get(key) if isinstance(features.get(key), list) else offered
                    features[key] = [k for k in offered if k in wanted] + [k for k in was if k not in offered]
    if "pictures_per_min" in changes:
        span = _pictures(style)
        value = changes["pictures_per_min"]
        if not span or isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not span["floor"] <= float(value) <= span["ceiling"]:
            raise ValueError("Choose pictures a minute between the style's floor and ceiling."
                             if span else "This style doesn't draw a set number of pictures a minute.")
        if round(float(value), 1) != span["value"]:         # the pace shown, sent back unchanged, stays exact
            pacing = out.setdefault("pacing", {})
            pacing.setdefault("learned_photo_every_s", pacing.get("photo_every_s"))
            pacing["photo_every_s"] = round(60.0 / float(value), 2)
    if "subtitles" in changes:
        if not isinstance(changes["subtitles"], bool):
            raise ValueError("Choose subtitles on or off.")
        look.setdefault("captions", {})["off"] = not changes["subtitles"]
    if out == style:
        return editable(home, name)
    kept = Path(home) / "output" / "cr-style-edits" / name / (time.strftime("%Y%m%d-%H%M%S") + f"-{before[:8]}.json")
    cr_style_clone._write(kept, style)
    cr_style_clone._write(path, out)
    _owned(home, out, path, before)
    return {**editable(home, name), "saved": True, "kept": str(kept)}


def _owned(home, style, path, before):
    """The study that saved this style keeps owning it (its style hash follows the edit), as a chosen sample does."""
    import cr_style_clone
    operation = str((style.get("cr_style_clone") or {}).get("operation_id") or "")
    try:
        folder = cr_style_clone._operation(home, operation)
    except RuntimeError:
        return
    saved = _read(folder / "operation.json")
    if saved.get("style_sha256") != before or (saved.get("spec") or {}).get("style_id") != style.get("name"):
        return
    saved["style_sha256"] = cr_style_clone._file_hash(path)
    if saved.get("finish_receipt"):
        saved["finish_receipt"]["artifacts"][0]["sha256"] = saved["style_sha256"]
    cr_style_clone._write(folder / "ready-style.json", style)
    cr_style_clone._write(folder / "operation.json", saved)
