"""A style study uses one of the plan's style clones only when the creator keeps its style (owner, 2026-11-02: "only
styles they keep"; a creator wrote that every retry of a clone that missed used up another one).

A study begun by Studio 124 or later ends with its style on trial: the creator looks at it, tries a sample, improves it
in Style Lab, then Keeps it (ChannelRecipe counts the clone, cr_bridge "style-clones/keep") or Discards it (Studio's
trash, nothing counted). Making a first video with it keeps it. The state lives in the study's operation.json, never in
the style file (its hash is the study's receipt). A style saved by an earlier Studio has no trial: it already counted."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

KEEPS_FROM = 124                                       # the server's KEEPS_ITS_STYLES


def _release(version) -> int:
    match = re.search(r"\.(\d+)$", str(version or ""))
    return int(match.group(1)) if match else 0


def begins_on_trial(saved: dict) -> bool:
    """A new style study's style starts on trial: begun by Studio 124+, and a study of a channel (a change asked of a
    style, or the graphics study of a pictures style, never counted)."""
    return (_release((saved.get("proof") or {}).get("local_version")) >= KEEPS_FROM
            and not saved.get("renderer_improve") and not saved.get("graphics_of"))


def start_trial(saved: dict) -> None:
    """At the study's first save of its style (cr_style_clone._publish): on trial until kept or discarded."""
    if "keep" not in saved and begins_on_trial(saved):
        saved["keep"] = {"state": "trial", "since": time.time()}


def on_trial(saved: dict) -> bool:
    return (saved.get("keep") or {}).get("state") == "trial"


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def study_of(home, style: str):
    """(folder, saved) of the study that made this style, or (None, {}) for a recipe or an older Studio's style.
    Standard library only: the studio tool server (cr_studio_tools) reads it too."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", str(style or "")):
        return None, {}
    data = _read(Path(home) / "styles" / (str(style) + ".json"))
    operation = str((data.get("cr_style_clone") or {}).get("operation_id") or "")
    if not re.fullmatch(r"op_[a-f0-9]{24}", operation):
        return None, {}
    folder = Path(home) / "output" / "cr-style-clone" / operation
    saved = _read(folder / "operation.json")
    if saved.get("operation_id") != operation or (saved.get("spec") or {}).get("style_id") != style:
        return None, {}
    return folder, saved


def style_on_trial(home, style: str) -> bool:
    return on_trial(study_of(home, style)[1])


def state(saved: dict, usage: dict | None = None) -> dict:
    """What the study's page and the tools say about keeping its style."""
    keep = saved.get("keep") or {}
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
    is used), then the study records it. A style with no trial is already kept: nothing is sent."""
    import cr_style_clone
    folder, saved = study_of(home, style)
    if not on_trial(saved):
        return {"kept": True, "already": True}
    answer = bridge.call("style-clones/keep", {"project_id": saved.get("project_id", "")})
    saved["keep"] = {**saved["keep"], "state": "kept", "kept_at": time.time(), "why": why}
    cr_style_clone._write(folder / "operation.json", saved)
    return {"kept": True, "style_clones": (answer or {}).get("style_clones") or {}}


def discard(studio, home, style: str) -> dict:
    """Discard a style on trial: into Studio's trash like any deleted style (Restore brings it back, still on trial),
    and nothing is counted (cr_trash.delete_style says so)."""
    import cr_trash
    if not style_on_trial(home, style):
        raise ValueError("Only a style on trial can be discarded for free. Delete it from the Style library instead.")
    return cr_trash.delete_style(studio, home, style)


def before_video(home, style: str, bridge) -> None:
    """A video made with a style on trial keeps it first (one style clone); refused before anything is queued when the
    plan has none left, with the plan's message and the way out."""
    if not style_on_trial(home, style):
        return
    try:
        keep(home, style, bridge, why="first video")
    except RuntimeError as error:
        raise ValueError(f"{error} This style is on trial: making a video with it keeps it, which needs a free style "
                         "clone. Discard a style you don't use, or upgrade.") from None
