"""Windows' 260-character path limit, checked in setup.

Windows stops at 260 characters in a file's path unless long paths are turned on (LongPathsEnabled, an administrator's
setting Studio never changes). A Studio folder deep in a creator's profile plus a video's own folders went past it,
and a creator's collages stopped (2026-10-24). Setup now says when this folder leaves too little room, and Studio's
own receipts no longer repeat a picture's long name (cr_media.task_receipt)."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath

LIMIT = 259                     # MAX_PATH is 260 characters with the closing NUL
SLUG = 59                       # a video's working folder name at its longest (the engine's slugify: 52, "-", 6;
                                # Studio's own attempt folders, cr_attempts.new_slug, stay inside it)
STYLE_NAME = 24                 # a learned style's own name (cr_style_clone); its folder is cr-clone-<name>
TEMPORARY = "x" * 8 + ".tmp"    # what a temporary file adds to the name it stands for (tempfile, cr_manimgx._write)


def inside_video() -> list:
    """The deepest files a video makes inside its working folder, on this computer's naming (measured from the engine,
    finished videos and Fast mode's own tests, 2026-10-24): a VOX collage picture (a 40-character picture id) and the
    receipt Studio keeps beside it, and Fast mode's deepest recovery files and type checks."""
    import cr_manimgx as gx
    import cr_media
    collage = PurePosixPath("vox_scenes", "m00", "vox", "assets", "x" * 40 + "_raw.png")
    scene = PurePosixPath("threed", gx.cache_name(), gx.key_folder("0" * 64))
    deepest_recovery = max((gx.recovery_name(kind) for kind in ("runtime_api", "coverage_text", "manifest_json",
                                                                  "native_stage", "procedural_pixels")), key=len)
    return [collage, collage.parent / ".cr-tasks" / cr_media.receipt_name(collage.name, "0" * 64),
            scene / deepest_recovery / ("." + "compiler-correction-response-1.txt" + TEMPORARY),
            scene / deepest_recovery / (".ty-" if gx.SHORT_NAMES else ".cr-typing-") / (".scene-corrected.py" + TEMPORARY),
            scene / gx.recovery_name("runtime_api") / gx.recovery_name("validation") / ".compiler-corrected.json" ]


def style_folder(style_id: str) -> str:
    """The folder a style's videos go in, by the engine's own rule (make_video.style_folder)."""
    return re.sub(r"[^a-z0-9.-]+", "-", str(style_id).lower()).strip("-.") or "videos"


def deepest(home=None) -> int:
    """The longest path, in characters below the Studio folder, a video can make: its style's folder (the longest
    installed style, or a new learned one), its working folder, and the deepest file inside that."""
    styles = ["cr_clone_" + "x" * STYLE_NAME]
    if home is not None:
        styles += [path.stem for path in (Path(home) / "styles").glob("*.json")]
    folder = max((style_folder(style) for style in styles), key=len)
    return (len(str(PurePosixPath("output", folder, "_work"))) + 1 + SLUG + 1
            + max(len(str(path)) for path in inside_video()))


def title_problem(folder: str, style: str, titles, youtube_filename) -> str:
    """A finished video moves into its own folder named after its title, beside its description file named after it
    too (the engine's youtube_filename, up to 120 characters), and "(" + 8 characters + ")" when another video of the
    title is there: the one path that grows with the title. "" when every title fits below `folder`."""
    longest = 0
    for title in titles:
        stem = PurePosixPath(youtube_filename(title)).stem
        longest = max(longest, len(stem))
    fixed = len(folder) + len(f"/output/{style_folder(style)}/") + len(" (12345678)/") + len(" - description.txt")
    if fixed + 2 * longest <= LIMIT:
        return ""
    most = max(10, (LIMIT - fixed) // 2)
    return (f"Windows stops at 260 characters in a file's path, and a finished video's files are named after its "
            f"title: in this Studio folder a title can be up to about {most} characters. Shorten the title, or move "
            f"the whole ChannelRecipe studio folder somewhere shorter, such as C:\\ChannelRecipe studio.")


def title_room(folder: str, home=None) -> int:
    """The longest title-named file stem every style's finished video has room for below `folder`: its folder and its
    description file both carry the stem (title_problem), in the longest style folder this Studio can have."""
    styles = ["cr_clone_" + "x" * STYLE_NAME]
    if home is not None:
        styles += [path.stem for path in (Path(home) / "styles").glob("*.json")]
    longest = max((style_folder(style) for style in styles), key=len)
    fixed = len(folder) + len(f"/output/{longest}/") + len(" (12345678)/") + len(" - description.txt")
    return max(10, (LIMIT - fixed) // 2)


def install(engine, home, *, windows=None, long_paths=None, folder=None):
    """On Windows without long paths, a finished video's file name is cut to fit this Studio folder (its title stays
    whole everywhere else). Before 109 a long title stopped Create with "Shorten the title" (the owner's Windows test,
    2026-10-26: a 112-character title in a Downloads folder had room for 56)."""
    original = getattr(engine, "youtube_filename", None)
    if original is None or getattr(original, "_cr_paths", False):
        return
    if not (os.name == "nt" if windows is None else windows):
        return
    if (long_paths_enabled() if long_paths is None else long_paths):
        return
    most = title_room(folder or str(Path(home).resolve()), home)

    def youtube_filename(title):
        name = original(title)
        stem, dot, ext = name.rpartition(".")
        if not dot or len(stem) <= most:
            return name
        # a short mark of the whole title, so two long titles cut alike ("... Part 1", "... Part 2") never share a name
        mark = hashlib.sha1(str(title).encode("utf-8")).hexdigest()[:6]
        return (stem[:max(1, most - 7)].rstrip(" .") or "video") + " " + mark + "." + ext
    youtube_filename._cr_paths = True
    engine.youtube_filename = youtube_filename


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
