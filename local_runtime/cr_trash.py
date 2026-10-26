"""Studio's trash: a deleted video or learned style waits in output/.cr-trash for 30 days, then goes for good.

Delete never removes anything at once. It moves what belongs to the video (its working folder under
output/<channel>/_work/, and the folder its finished video was published to) or the learned style's recipe file
(styles/cr_clone_*.json) into one trash entry, with a note of where each piece came from, so Restore puts it back.

- Only Studio's own output and styles folders are touched. Every path is checked to be inside them, a symlink is never
  moved or followed, and a move is a rename on the same drive (never a copy and delete).
- A video that is being made, waiting in the queue or being edited in the timeline is refused, and so is a style a
  video is being made in or whose study is still running. Built-in recipes can't be deleted.
- Entries older than KEEP_DAYS are emptied when Studio starts and whenever the trash is listed.

    video_plan / style_plan    what would go and what is kept (nothing changes)
    delete_video / delete_style
    entries / restore / empty_old
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import sys
import time

FOLDER = ".cr-trash"
KEEP_DAYS = 30
ENTRY = re.compile(r"[vs]\d{8}-\d{6}-[a-f0-9]{6}\Z")
LEARNED = re.compile(r"cr_clone_[a-zA-Z0-9_-]{1,64}\Z")
SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}\Z")
STUDIO_THUMB = re.compile(r"thumbnail(?:_\d{1,2})?\.(?:png|jpg)\Z", re.I)   # the engine's own thumbnails
AREAS = ("output", "styles")
VIDEO_KEPT = ["Your voice library and saved recordings",
              "Your styles, characters and presenters",
              "Your other videos"]
STYLE_KEPT_LATER = ["Your voice library and saved recordings",
                    "The study Studio made of the channel (its notes and frames)",
                    "Your plan's count of style clones stays as it is"]
SIZE_FILES = 50_000                                      # stop counting a folder's size after this many files


class Refused(ValueError):
    """Why this can't be deleted or restored right now. Nothing was changed."""


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────

def root(home) -> Path:
    return Path(home) / "output" / FOLDER


def _base(home, area: str) -> Path:
    return Path(home) / area


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _inside(path, base) -> bool:
    try:
        return Path(path).resolve().is_relative_to(Path(base).resolve())
    except (OSError, RuntimeError):
        return False


def _relative(path: Path, base: Path) -> str:
    """path under base as a POSIX string (on a moved or linked drive the resolved paths are compared)."""
    try:
        return Path(path).relative_to(base).as_posix()
    except ValueError:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total, seen = 0, 0
    for folder, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            seen += 1
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
            if seen >= SIZE_FILES:
                return total
    return total


def _megabytes(size: int) -> str:
    mb = size / 1e6
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB" if mb >= 10 else f"{max(mb, 0.1):.1f} MB"


def _studio_lock(studio):
    return getattr(studio, "LOCK", None) or contextlib.nullcontext()


def _move(src: Path, dest: Path) -> None:
    """A rename that tries again for a moment: on Windows a video open in a player can't be moved. A move to another
    drive is refused rather than copied (the trash lives on the same drive as Studio's output)."""
    for attempt in range(3):
        try:
            os.replace(src, dest)
            return
        except OSError as error:
            if error.errno == errno.EXDEV:
                raise Refused("This is on a different drive from Studio's output folder, so it can't go to Studio's "
                              "trash. Nothing was deleted.") from None
            if attempt == 2:
                raise
            time.sleep(0.4 * (attempt + 1))


def _rmtree(path: Path) -> None:
    """Remove a trash entry for good (only ever called on an entry inside the trash). A read-only file (Windows) or
    folder is made writable and tried again; whatever still can't go is left for the next time."""
    def retry(function, target, _info):
        try:
            os.chmod(os.path.dirname(target), stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            if not os.path.islink(target):
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if os.path.isdir(target) else 0))
            function(target)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:                                                    # pragma: no cover - Python 3.10/3.11
        shutil.rmtree(path, onerror=retry)


def _trash(home) -> Path:
    folder = root(home)
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise Refused("Studio's trash folder (output/.cr-trash) isn't an ordinary folder. Nothing was deleted.")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _new_entry(home, kind: str) -> Path:
    folder = _trash(home)
    while True:
        entry = folder / f"{kind}{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        try:
            entry.mkdir()
            return entry
        except FileExistsError:
            continue


def _named_exactly(path: Path) -> bool:
    """True when the name on disk is exactly this one. Mac and Windows folders ignore letter case, so MY-VIDEO would
    otherwise find my-video and slip past the checks that compare names with what is being made."""
    try:
        return path.name in os.listdir(path.parent)
    except OSError:
        return False


def _remove_empty(entry: Path) -> None:
    """The entry's own note and the folders left empty after a restore or a rollback; never anything with files."""
    (entry / "entry.json").unlink(missing_ok=True)
    (entry / "entry.json.tmp").unlink(missing_ok=True)
    for folder, dirs, _files in sorted(os.walk(entry, followlinks=False), key=lambda row: -len(row[0])):
        with contextlib.suppress(OSError):
            os.rmdir(folder)


def _put_back(moved: list) -> bool:
    """Undo moves made so far (newest first). True when every one went back."""
    ok = True
    for src, dest in reversed(moved):
        try:
            _move(dest, src)
        except (OSError, Refused):
            ok = False
    return ok


def _put_away(home, kind: str, record: dict, moves: list) -> Path:
    """Move each (name in the entry, source) into a new trash entry; on any failure, put back what moved."""
    entry = _new_entry(home, kind)
    items = []
    for name, area, src in moves:
        items.append({"area": area, "path": _relative(src, _base(home, area)), "to": name})
    record = {"schema": 1, "id": entry.name, "kind": "video" if kind == "v" else "style", **record,
              "deleted_at": time.time(), "items": items, "state": "moving"}
    _write(entry / "entry.json", record)                 # first, so a Studio closed mid-move can still restore it
    moved = []
    try:
        for (name, _area, src) in moves:
            dest = entry / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            _move(src, dest)
            moved.append((src, dest))
    except (OSError, Refused) as error:
        if not _put_back(moved):                         # the entry keeps its note: Recently deleted puts the rest back
            raise Refused("Studio couldn't finish moving it to the trash, and couldn't put every part back. What "
                          "moved is in Recently deleted: Restore it there.") from None
        _remove_empty(entry)
        if isinstance(error, Refused):
            raise
        raise Refused("Studio couldn't move it to the trash. If the video is open in a player, a folder window or "
                      "the timeline editor's tab, close it and try again. Nothing was deleted.") from None
    record["state"] = "done"
    _write(entry / "entry.json", record)
    return entry


def _until(deleted_at: float) -> float:
    return float(deleted_at) + KEEP_DAYS * 86400


# ── videos ────────────────────────────────────────────────────────────────────────────────────────────────────

def _title(job: Path) -> str:
    try:
        lines = (job / "title.txt").read_text(encoding="utf-8").splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()[:300]
    except OSError:
        pass
    saved = _read(job / "cr-project.json")
    spec = saved.get("job_spec") if isinstance(saved.get("job_spec"), dict) else {}
    title = spec.get("title") or saved.get("title")
    return str(title).splitlines()[0][:300] if isinstance(title, str) and title.strip() else job.name


def _video_busy(studio, job: Path) -> str:
    """'' or what this video is doing that a delete must wait for."""
    busy = _slug_busy(studio, job.name)
    if busy:
        return busy
    editor = _read(job / "_timeline" / "state.json")
    tasks = [t for t in editor.get("tasks") or [] if isinstance(t, dict)]
    chat = [m for m in editor.get("chat") or [] if isinstance(m, dict)]
    if any(t.get("state") in ("queued", "working") for t in tasks) or any(m.get("pending") for m in chat):
        return "still has timeline edits being made. Delete it once they finish."
    return ""


def _slug_busy(studio, slug: str) -> str:
    """'' or what Studio is doing with a video of this name: made, queued or on hold."""
    with _studio_lock(studio):
        running = (getattr(studio, "RUNNING", None) or {}).get("slug") == slug
        queued = any(isinstance(spec, dict) and spec.get("slug") == slug for spec in getattr(studio, "QUEUE", None) or [])
        state = ((getattr(studio, "JOBS", None) or {}).get(slug) or {}).get("state")
    worker = getattr(getattr(studio, "_cr_video_workers", None), "current", None)
    if running or state == "running" or (isinstance(worker, dict) and worker.get("slug") == slug):
        return "is being made right now. Cancel it or let it finish, then delete it."
    if queued or state == "queued":
        return "is waiting in the queue. Remove it from the queue or let it finish, then delete it."
    held = getattr(getattr(studio, "_cr_queue", None), "held", None) or []
    if state == "held" or any(isinstance(spec, dict) and spec.get("slug") == slug for spec in held):
        return "is on hold in the queue. Carry it on or remove it first."
    return ""


def video_plan(studio, home, slug: str) -> dict:
    """What deleting this video moves to the trash and what it keeps. Raises LookupError when it isn't here."""
    home = Path(home)
    output = home / "output"
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or slug in (".", ".."):
        raise LookupError("That video isn't on this computer any more.")
    found = studio.mv.find_job(slug)
    job = Path(found) if found is not None else None
    if (job is None or job.is_symlink() or not job.is_dir() or not _inside(job, output) or job.name != slug
            or not _named_exactly(job) or job.resolve() == output.resolve() or _inside(job, root(home))):
        raise LookupError("That video isn't on this computer any more.")
    moves = [("job", "output", job)]
    published, others = [], []
    if job.parent.name == "_work":
        try:
            main = Path(studio.mv.main_dir(job))
        except Exception:                                  # noqa: BLE001 - no published folder to find
            main = None
        if (main is not None and main != job and main.is_dir() and not main.is_symlink()
                and main.name != "_work" and not main.name.startswith(".") and _inside(main, output)
                and main.resolve().parent == job.resolve().parent.parent):
            try:
                owner = (main / ".channelrecipe-video").read_text(encoding="utf-8").strip()
            except OSError:
                owner = ""
            if owner == job.name:                        # the folder this video published to, and only it
                moves.append(("video", "output", main))
                published = [p for p in main.iterdir() if p.is_file() and not p.name.startswith(".")]
                saved = _read(job / "published.json")
                made = {n for n in [saved.get("video")] + list(saved.get("files") or []) if isinstance(n, str)}
                # Studio names its files after the title, like the folder ("Title.mp4", "Title - description.txt")
                stem = Path(saved["video"]).stem if isinstance(saved.get("video"), str) and saved["video"] else main.name
                others = sorted(p.name for p in main.iterdir()   # what the creator keeps there goes too: say so
                                if not p.name.startswith(".") and p.name not in made and not (
                                    p.is_file() and (STUDIO_THUMB.fullmatch(p.name) or (
                                        stem and p.name.startswith(stem)
                                        and p.suffix.lower() in (".mp4", ".txt", ".jpg", ".png")))))
            else:                                        # a shared title folder: only the files this video put there
                saved = _read(job / "published.json")
                names = [saved.get("video")] + list(saved.get("files") or [])
                for name in dict.fromkeys(n for n in names if isinstance(n, str) and n and Path(n).name == n):
                    path = main / name
                    if path.is_file() and not path.is_symlink():
                        moves.append(("video/" + name, "output", path))
                        published.append(path)
    try:
        video = Path(studio.mv.final_video(job))
    except Exception:                                      # noqa: BLE001
        video = job / "video.mp4"
    finished = video.is_file()
    goes = []
    if finished:
        goes.append(f"The finished video: {video.name} ({_megabytes(video.stat().st_size)})")
    extras = [p for p in published if p != video]
    if extras:
        goes.append("Its YouTube description and thumbnails")
    work = _size(job) - (video.stat().st_size if finished and video.parent == job else 0)
    goes.append(f"Its working files: script, voice, pictures, footage and timeline edits ({_megabytes(max(work, 0))})")
    if others:                                           # files the creator keeps in the video's folder go with it
        shown = ", ".join(others[:3]) + (f" and {len(others) - 3} more" if len(others) > 3 else "")
        goes.append(f"Other files in its folder that Studio didn't make: {shown}")
    return {"kind": "video", "id": slug, "title": _title(job), "finished": finished, "goes": goes,
            "kept": list(VIDEO_KEPT), "keep_days": KEEP_DAYS, "blocked": _video_busy(studio, job), "moves": moves}


def delete_video(studio, home, slug: str) -> dict:
    plan = video_plan(studio, home, slug)
    if plan["blocked"]:
        raise Refused(f"“{plan['title']}” {plan['blocked']}")
    entry = _put_away(home, "v", {"name": plan["title"], "slug": slug, "goes": plan["goes"]}, plan["moves"])
    with _studio_lock(studio):                           # gone from Projects: the live lists forget it too
        jobs = getattr(studio, "JOBS", None)
        if isinstance(jobs, dict):
            jobs.pop(slug, None)
        history = getattr(studio, "HISTORY", None)
        if isinstance(history, list):
            history[:] = [s for s in history if s != slug]
    return done(plan, entry)


# ── learned styles ────────────────────────────────────────────────────────────────────────────────────────────

def _style_busy(studio, home, style: str) -> str:
    with _studio_lock(studio):
        jobs = dict(getattr(studio, "JOBS", None) or {})
        queue = list(getattr(studio, "QUEUE", None) or [])
    if any(isinstance(j, dict) and j.get("style") == style and j.get("state") in ("running", "queued", "held")
           for j in jobs.values()):
        return "A video in this style is being made right now. Delete the style once it has finished."
    if any(isinstance(s, dict) and s.get("style") == style for s in queue):
        return "A video in this style is waiting in the queue. Delete the style once it has been made."
    held = getattr(getattr(studio, "_cr_queue", None), "held", None) or []
    if any(isinstance(s, dict) and s.get("style") == style for s in held):
        return "A video in this style is on hold in the queue. Carry it on or remove it first."
    studies = Path(home) / "output" / "cr-style-clone"
    for record in (studies.glob("op_*/operation.json") if studies.is_dir() else []):
        saved = _read(record)
        if (saved.get("spec") or {}).get("style_id") == style and saved.get("state") in ("prepared", "running"):
            return "Its style study is still running. Delete the style once the study has finished."
    return ""


def _videos_in(studio, style: str) -> int:
    try:
        jobs = studio.mv.list_jobs() if hasattr(studio.mv, "list_jobs") else []
    except Exception:                                      # noqa: BLE001 - only for the wording
        return 0
    count = 0
    for job in jobs:
        try:
            if (Path(job) / "style.txt").read_text(encoding="utf-8").strip() == style:
                count += 1
        except OSError:
            continue
    return count


def style_plan(studio, home, style: str) -> dict:
    """What deleting this learned style moves to the trash and what it keeps. Built-in recipes are refused."""
    home = Path(home)
    styles = home / "styles"
    if not isinstance(style, str) or not LEARNED.fullmatch(style):
        raise Refused("Only styles you cloned can be deleted. Studio's own recipes stay in your library.")
    path = styles / (style + ".json")
    if path.is_symlink() or not path.is_file() or not _inside(path, styles) or not _named_exactly(path):
        raise LookupError("That style isn't in your library any more.")
    data = _read(path)
    if data.get("builtin") is True or not isinstance(data.get("cr_style_clone"), dict):
        raise Refused("Only styles you cloned can be deleted. Studio's own recipes stay in your library.")
    label = str(data.get("label") or style)[:120]
    made = _videos_in(studio, style)
    kept = ([f"The {made} video{'' if made == 1 else 's'} you made with it: {'it stays' if made == 1 else 'they stay'} "
             "in Projects"] if made else ["Videos you made with it stay in Projects"]) + STYLE_KEPT_LATER
    return {"kind": "style", "id": style, "title": label,
            "goes": [f"The style “{label}”: its look, prompts and settings, from your Style library"],
            "kept": kept, "keep_days": KEEP_DAYS, "blocked": _style_busy(studio, home, style),
            "moves": [(style + ".json", "styles", path)]}


def _reload_styles(studio) -> None:
    styles = getattr(studio, "styles", None)
    try:
        if hasattr(styles, "load_all"):
            styles.load_all()
        if hasattr(styles, "apply_to_engine") and getattr(studio, "motion", None) is not None:
            styles.apply_to_engine(studio.mv, studio.motion)
    except Exception:                                      # noqa: BLE001 - the catalogue reloads on its next read
        pass


def delete_style(studio, home, style: str) -> dict:
    plan = style_plan(studio, home, style)
    if plan["blocked"]:
        raise Refused(plan["blocked"])
    entry = _put_away(home, "s", {"name": plan["title"], "style": style, "goes": plan["goes"]}, plan["moves"])
    _reload_styles(studio)
    return done(plan, entry)


def public(plan: dict) -> dict:
    """A plan as the page and the tools read it: never the paths on this computer."""
    return {k: v for k, v in plan.items() if k != "moves"}


def done(plan: dict, entry: Path) -> dict:
    until = _until(_read(entry / "entry.json").get("deleted_at") or time.time())
    return {"deleted": True, "kind": plan["kind"], "id": plan["id"], "title": plan["title"], "trash_id": entry.name,
            "goes": plan["goes"], "kept": plan["kept"], "empties_at": until,
            "message": f"Moved to Studio's trash. It waits there for {KEEP_DAYS} days (until "
                       f"{time.strftime('%d %b %Y', time.localtime(until))}): Restore in Recently deleted brings it "
                       "back."}


# ── the trash ─────────────────────────────────────────────────────────────────────────────────────────────────

def _entry(home, entry_id: str) -> Path:
    if not isinstance(entry_id, str) or not ENTRY.fullmatch(entry_id):
        raise LookupError("That item isn't in Studio's trash.")
    entry = root(home) / entry_id
    if root(home).is_symlink() or entry.is_symlink() or not entry.is_dir():
        raise LookupError("That item isn't in Studio's trash.")
    return entry


def _deleted_at(entry: Path) -> float:
    value = _read(entry / "entry.json").get("deleted_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return entry.stat().st_mtime
    except OSError:
        return time.time()


def empty_old(home, now: float | None = None) -> int:
    """Empty the entries older than KEEP_DAYS for good. Only entries Studio made, inside its own trash folder."""
    folder = root(home)
    if folder.is_symlink() or not folder.is_dir():
        return 0
    now = time.time() if now is None else now
    gone = 0
    try:
        candidates = list(folder.iterdir())
    except OSError:
        return 0
    for entry in candidates:
        if not ENTRY.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        if now >= _until(_deleted_at(entry)):
            with contextlib.suppress(OSError):
                _rmtree(entry)
                gone += 1
    return gone


def entries(home) -> list:
    """What is in the trash, newest first."""
    empty_old(home)
    folder = root(home)
    if folder.is_symlink() or not folder.is_dir():
        return []
    rows = []
    for entry in folder.iterdir():
        if not ENTRY.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        saved = _read(entry / "entry.json")
        if saved.get("kind") not in ("video", "style"):
            continue
        deleted = _deleted_at(entry)
        rows.append({"id": entry.name, "kind": saved["kind"], "name": str(saved.get("name") or "")[:300],
                     "deleted_at": deleted, "empties_at": _until(deleted),
                     "days_left": max(0, int((_until(deleted) - time.time()) // 86400))})
    rows.sort(key=lambda row: -row["deleted_at"])
    return rows


def _restorable(home, entry: Path, item) -> tuple:
    """(source in the trash, where it goes back) for one recorded piece; ValueError when the record is malformed."""
    if not isinstance(item, dict) or item.get("area") not in AREAS:
        raise ValueError("bad item")
    rel, to = PurePosixPath(str(item.get("path") or "")), PurePosixPath(str(item.get("to") or ""))
    for part in (rel, to):
        if not part.parts or part.is_absolute() or any(p in ("", ".", "..") for p in part.parts) or "\\" in str(part):
            raise ValueError("bad path")
    base = _base(home, item["area"])
    src, dest = entry.joinpath(*to.parts), base.joinpath(*rel.parts)
    if not _inside(dest.parent, base) or not _inside(src, entry):
        raise ValueError("outside")
    if item["area"] == "output" and _inside(dest, root(home)):
        raise ValueError("into the trash")
    return src, dest


def restore(studio, home, entry_id: str) -> dict:
    """Put a deleted video or style back where it was. Refused when something new took its place."""
    home = Path(home)
    entry = _entry(home, entry_id)
    saved = _read(entry / "entry.json")
    slug = saved.get("slug")
    if saved.get("kind") == "video" and isinstance(slug, str) and slug and _slug_busy(studio, slug):
        raise Refused(f"A video with the same name {_slug_busy(studio, slug).split('. ')[0]}, so "
                      f"“{saved.get('name') or entry_id}” can't be put back yet. Nothing was changed.")
    pieces = []
    for item in saved.get("items") or []:
        try:
            src, dest = _restorable(home, entry, item)
        except ValueError:
            raise Refused("This item's record in Studio's trash can't be read, so it can't be put back.") from None
        if not (src.exists() or src.is_symlink()):
            continue
        if dest.exists() or dest.is_symlink():
            what = "A video with the same title" if saved.get("kind") == "video" else "A style with the same name"
            raise Refused(f"{what} is in its place again, so “{saved.get('name') or entry_id}” can't be put back. "
                          "Nothing was changed.")
        pieces.append((src, dest))
    if not pieces:
        raise Refused("There's nothing left in this item to put back.")
    moved = []
    try:
        for src, dest in pieces:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _move(src, dest)
            moved.append((src, dest))
    except (OSError, Refused):
        _put_back(moved)
        raise Refused("Studio couldn't put it back. Close any window showing Studio's output folder and try "
                      "again. It's still in the trash.") from None
    _remove_empty(entry)
    if saved.get("kind") == "style":
        _reload_styles(studio)
    return {"restored": True, "kind": saved.get("kind"), "title": saved.get("name") or "",
            "id": saved.get("slug") or saved.get("style") or ""}
