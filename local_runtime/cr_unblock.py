"""Windows' "downloaded from the internet" mark on Studio's own files.

Extract All copies a downloaded ZIP's mark (the Mark of the Web: a small Zone.Identifier stream beside each file) onto
every file it extracts. Windows then showed "Open File - Security Warning ... Unknown Publisher" each time Start on
Windows.cmd was opened, from a double-click and from Claude Code's start alike (the owner's Windows test, 2026-10-26).
clear() takes that mark off the files Studio itself shipped, the ones cr-files.json names, the way PowerShell's
Unblock-File does (it deletes that one stream): never a file's contents, and never the creator's videos, .venv, tools
or anything else in the folder. The launcher runs it on its first start, before the libraries install, and Studio on
every start: an in-app update never replaces a running Windows launcher, so older folders get it from Studio.
Elsewhere it does nothing (macOS asks about its own quarantine flag once, then remembers)."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

HOME = Path(__file__).resolve().parent
STREAM = ":Zone.Identifier"
ALWAYS = ("Start on Windows.cmd", "cr-files.json")


def shipped(home: Path = HOME) -> list:
    """Studio's own files in this folder (cr-files.json; the launcher too, even when that list can't be read)."""
    try:
        files = json.loads((Path(home) / "cr-files.json").read_text(encoding="utf-8")).get("files") or {}
    except (OSError, ValueError, AttributeError):
        files = {}
    names = list(ALWAYS) + [name for name in files if isinstance(name, str) and name not in ALWAYS]
    # only plain paths inside this folder: no parent steps, no drive letters, no streams of their own, and no
    # backslashes (Windows reads one as a folder step the POSIX check above can't see)
    return [name for name in names
            if name and ":" not in name and "\\" not in name and not PurePosixPath(name).is_absolute()
            and ".." not in PurePosixPath(name).parts]


def _inside(home: Path, name: str):
    """The file a shipped name stands for, only when it's a real file inside this folder: never through a link or a
    Windows junction (to anywhere else on the computer)."""
    root = Path(home).resolve()
    path = root.joinpath(*PurePosixPath(name).parts)
    folder = root
    for part in PurePosixPath(name).parts:
        folder = folder / part
        if os.path.islink(folder) or getattr(os.path, "isjunction", lambda _p: False)(folder):
            return None
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_relative_to(root) and resolved.is_file() else None


def clear(home: Path = HOME, *, windows: bool | None = None, remove=None) -> int:
    """Take the mark off Studio's own files; how many had one. Quiet and quick (about 1,900 small deletes that mostly
    find nothing), and never raises."""
    if not ((os.name == "nt") if windows is None else windows):
        return 0
    remove, cleared = remove or os.remove, 0
    for name in shipped(home):
        try:
            path = _inside(home, name)
            if path is None:
                continue
            remove(str(path) + STREAM)
            cleared += 1
        except OSError:
            pass        # no mark (every file, once cleared), a disk without streams (FAT32, exFAT), or no such file
        except Exception:                                      # noqa: BLE001 - a launcher step: never stop Studio
            pass
    return cleared


if __name__ == "__main__":
    clear()
