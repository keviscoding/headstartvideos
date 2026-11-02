"""A style study uses one of the plan's style clones only when the creator keeps its style (owner, 2026-11-02: "only
styles they keep"; a creator wrote that every retry of a clone that missed used up another one).

A study begun by Studio 124 or later ends with its style on trial: the creator looks at it, tries a sample, improves it
in Style Lab, then Keeps it (ChannelRecipe counts the clone, cr_bridge "style-clones/keep") or Discards it (Studio's
trash, nothing counted; Restore brings it back, still on trial). Making a first video with it keeps it. The graphics
copy of a pictures style counts through the study it came from. The state lives in the study's operation.json, never
in the style file (its hash is the study's receipt). A style saved by an earlier Studio has no trial: it already
counted. The study's begin tells ChannelRecipe which style it makes, so a video begun with it is kept there too.
Standard library only: the studio tool server (cr_studio_tools) reads it."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

KEEPS_FROM = 124                                       # the server's KEEPS_ITS_STYLES
LEARNED = re.compile(r"cr_clone_[a-z0-9_-]{1,60}\Z")   # the server's LEARNED_STYLE
_OPERATION = re.compile(r"op_[a-f0-9]{24}\Z")


def _release(version) -> int:
    match = re.search(r"\.(\d+)$", str(version or ""))
    return int(match.group(1)) if match else 0


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _begun_version(saved: dict):
    proof = saved.get("proof") if isinstance(saved.get("proof"), dict) else {}
    return saved.get("begun_version") or proof.get("local_version")


def begins_on_trial(saved: dict) -> bool:
    """A new style study's style starts on trial: begun by Studio 124+ (the release it was begun on, which
    ChannelRecipe counts it by, not the one it was prepared on), a study of a channel (never a change asked of a
    style; the graphics copy of a pictures style counts through its source study instead)."""
    return (_release(_begun_version(saved)) >= KEEPS_FROM
            and not saved.get("renderer_improve") and not saved.get("graphics_of"))


def begin_options(saved: dict) -> dict:
    """What a study's begin tells ChannelRecipe: the style it makes, and a graphics copy's source style."""
    if saved.get("renderer_improve"):
        return {}
    out = {}
    style = str((saved.get("spec") or {}).get("style_id") or "")
    if LEARNED.fullmatch(style):
        out["learns_style"] = style
        source = str(saved.get("source_style") or "")
        if saved.get("graphics_of") and LEARNED.fullmatch(source):
            out["counts_with"] = source
    return out


def start_trial(saved: dict) -> None:
    """At the study's first save of its style (cr_style_clone._publish): on trial until kept or discarded."""
    if "keep" not in saved and begins_on_trial(saved):
        saved["keep"] = {"state": "trial", "since": time.time()}


def on_trial(saved: dict) -> bool:
    keep = saved.get("keep") if isinstance(saved.get("keep"), dict) else {}
    return keep.get("state") == "trial"


def held(path: Path, value):
    """An operation.json about to be written, with a keep recorded on disk meanwhile (a keep while the likeness check
    ran, say): a kept style is never written back to trial."""
    if not isinstance(value, dict) or not on_trial(value):
        return value
    disk = _read(path).get("keep")
    if isinstance(disk, dict) and disk.get("state") == "kept":
        return {**value, "keep": disk}
    return value


def study_of(home, style: str):
    """(folder, saved) of the study that made this style, or (None, {}) for a recipe or an older Studio's style."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", str(style or "")):
        return None, {}
    data = _read(Path(home) / "styles" / (str(style) + ".json"))
    clone = data.get("cr_style_clone") if isinstance(data.get("cr_style_clone"), dict) else {}
    operation = str(clone.get("operation_id") or "")
    if not _OPERATION.fullmatch(operation):
        return None, {}
    folder = Path(home) / "output" / "cr-style-clone" / operation
    saved = _read(folder / "operation.json")
    spec = saved.get("spec") if isinstance(saved.get("spec"), dict) else {}
    if saved.get("operation_id") != operation or spec.get("style_id") != style:
        return None, {}
    return folder, saved


def counting_study(home, style: str):
    """(folder, saved) of the study whose keep this style's use counts: its own, or for the graphics copy of a pictures
    style, the study it came from (found by its operation, so a discarded source still counts once)."""
    folder, saved = study_of(home, style)
    source = str(saved.get("graphics_of") or "")
    if not _OPERATION.fullmatch(source):
        return folder, saved
    origin = Path(home) / "output" / "cr-style-clone" / source
    found = _read(origin / "operation.json")
    return (origin, found) if found.get("operation_id") == source else (None, {})


def style_on_trial(home, style: str) -> bool:
    try:
        return on_trial(counting_study(home, style)[1])
    except Exception:                                    # noqa: BLE001 - a broken style file never breaks the library
        return False


def state(saved: dict, usage: dict | None = None) -> dict:
    """What the study's page and the tools say about keeping its style."""
    keep = saved.get("keep") if isinstance(saved.get("keep"), dict) else {}
    if keep.get("state") == "kept":
        return {"state": "kept", "say": "You kept this style. It used one of your plan's style clones."}
    if keep.get("state") != "trial":
        return {}
    left = (usage or {}).get("remaining")
    count = "" if left is None else f" You have {left} left."
    return {"state": "trial",
            "say": "This style is on trial. Look at it, try a sample or improve it, then keep it: keeping it (or making "
                   f"your first video with it) uses one of your plan's style clones.{count} If it doesn't match the "
                   "channel, discard it and it costs nothing from your plan."}


def keep(home, style: str, bridge, *, why: str = "kept") -> dict:
    """Keep a style on trial: ChannelRecipe counts its clone (refused, with the plan's upgrade message, when every clone
    is used), then the study records it, changing nothing else in its saved state. A style already kept says so."""
    import cr_style_clone
    if not (Path(home) / "styles" / (str(style) + ".json")).is_file():
        raise ValueError("This style isn't in your Style library. Restore it from Recently deleted first.")
    folder, saved = counting_study(home, style)
    if not on_trial(saved):
        return {"kept": True, "already": True}
    answer = bridge.call("style-clones/keep", {"project_id": saved.get("project_id", "")})
    current = _read(folder / "operation.json")             # as it is now: other work may have saved it meanwhile
    current["keep"] = {**(current.get("keep") or {}), "state": "kept", "kept_at": time.time(), "why": why}
    cr_style_clone._write(folder / "operation.json", current)
    return {"kept": True, "style_clones": (answer or {}).get("style_clones") or {}}


def discard(studio, home, style: str) -> dict:
    """Discard a style on trial: into Studio's trash like any deleted style (Restore brings it back, still on trial),
    and nothing is counted (cr_trash.delete_style says so)."""
    import cr_trash
    if not style_on_trial(home, style):
        raise ValueError("Only a style on trial can be discarded for free. Delete it from the Style library instead.")
    return cr_trash.delete_style(studio, home, style)


def before_video(home, style: str, bridge) -> None:
    """A video made with a style on trial keeps it first (one style clone); refused before anything is queued, with
    the plan's message and the way out, or what else went wrong."""
    if not style_on_trial(home, style):
        return
    try:
        keep(home, style, bridge, why="first video")
    except (RuntimeError, ValueError) as error:
        said = str(error)
        if "style clone" in said:
            raise ValueError(f"{said} This style is on trial: making a video with it keeps it, which needs a free "
                             "style clone. Discard a style you don't use, or upgrade.") from None
        if "wasn't found on your account" in said:
            raise ValueError("This style was studied on another ChannelRecipe account, so it can be kept only there. "
                             "Connect that account in setup, or study the channel again on this one.") from None
        raise ValueError(f"This style is on trial: making a video with it keeps it, and ChannelRecipe couldn't record "
                         f"that ({said.rstrip('.')}). Nothing was queued; try again in a moment.") from None
