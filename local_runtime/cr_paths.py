"""Windows' 260-character path limit, checked in setup.

Windows stops at 260 characters in a file's path unless long paths are turned on (LongPathsEnabled, an administrator's
setting Studio never changes). A Studio folder deep in a creator's profile plus a video's own folders went past it,
and a creator's collages stopped (2026-10-24). Setup now says when this folder leaves too little room, and Studio's
own receipts no longer repeat a picture's long name (cr_media.task_receipt)."""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

LIMIT = 259                     # MAX_PATH is 260 characters with the closing NUL
SLUG = 59                       # a video's working folder name at its longest (the engine's slugify: 52, "-", 6)
STYLE_NAME = 24                 # a learned style's own name (cr_style_clone); its folder is cr-clone-<name>
# The deepest file a video makes inside its working folder: a VOX collage picture (a 40-character picture id) and the
# receipt Studio keeps beside it (measured from the engine and from finished videos, 2026-10-24).
COLLAGE = PurePosixPath("vox_scenes", "m00", "vox", "assets", "x" * 40 + "_raw.png")


def style_folder(style_id: str) -> str:
    """The folder a style's videos go in, by the engine's own rule (make_video.style_folder)."""
    return re.sub(r"[^a-z0-9.-]+", "-", str(style_id).lower()).strip("-.") or "videos"


def deepest(home=None) -> int:
    """The longest path, in characters below the Studio folder, a video can make: its style's folder (the longest
    installed style, or a new learned one), its working folder, and a collage picture's receipt inside that."""
    import cr_media
    styles = ["cr_clone_" + "x" * STYLE_NAME]
    if home is not None:
        styles += [path.stem for path in (Path(home) / "styles").glob("*.json")]
    folder = max((style_folder(style) for style in styles), key=len)
    receipt = COLLAGE.parent / ".cr-tasks" / cr_media.receipt_name(COLLAGE.name, "0" * 64)
    inside = max(len(str(COLLAGE)), len(str(receipt)))
    return len(str(PurePosixPath("output", folder, "_work"))) + 1 + SLUG + 1 + inside


def long_paths_enabled():
    """Windows' own long-path setting (read only); None on a Mac or Linux, which have no such limit."""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def status(home, *, windows=None, long_paths=None) -> dict | None:
    """Setup's Windows paths row: whether this Studio folder leaves room for a video's deepest file. None off Windows.
    A note, never a stop: most videos stay well inside the deepest case."""
    if not (os.name == "nt" if windows is None else windows):
        return None
    folder = str(Path(home).resolve())
    need = deepest(home)
    room = LIMIT - len(folder) - 1 - need
    enabled = long_paths_enabled() if long_paths is None else long_paths
    result = {"ready": True, "folder": folder, "folder_length": len(folder), "deepest": need, "room": room,
              "long_paths": enabled, "message": ""}
    if room < 0 and not enabled:
        result["message"] = (
            f"Windows stops at 260 characters in a file's path, and this Studio folder ({len(folder)} characters) "
            f"leaves too little room for the files a video makes inside it (up to {need} more), so a video can stop "
            f"halfway. Move the whole ChannelRecipe studio folder somewhere shorter, such as C:\\ChannelRecipe studio "
            f"(close Studio first; your videos and settings move with it), or ask whoever manages this computer to "
            f"turn on Windows long paths. Studio never changes that setting itself.")
    return result


def diagnostics_line(result) -> str:
    if not result:
        return ""
    return "Windows paths: folder %d characters, videos need up to %d more, long paths %s%s" % (
        result["folder_length"], result["deepest"], "on" if result["long_paths"] else "off",
        "" if result["room"] >= 0 or result["long_paths"] else " (too deep)")
