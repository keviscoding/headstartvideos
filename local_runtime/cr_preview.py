"""Read-only, local screenshots from the studio's actual videos and studied kits.

The screenshot cache never downloads media, executes a model, renders a scene,
or changes a project. Only the existing local video paths may reach FFmpeg.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.parse import quote

_STYLE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}\Z")
_OPERATION = re.compile(r"op_[a-f0-9]{24}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_VIDEO = {".mp4", ".webm", ".mov", ".m4v"}
_IMAGE = {".jpg", ".jpeg", ".png"}
_WORKERS = threading.BoundedSemaphore(2)
_CACHE_FILES = 160
_CACHE_BYTES = 32 * 1024 * 1024
_MAX_IMAGE = 1024 * 1024


class Unavailable(Exception):
    """There is no existing actual screenshot to show for this item."""


class Busy(Exception):
    """The bounded local screenshot workers are occupied."""


@dataclass(frozen=True)
class Source:
    path: Path
    label: str
    video_url: str | None = None


def _read(path, limit=1024 * 1024):
    try:
        if Path(path).stat().st_size > limit:
            return {}
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _file(path, root, extensions):
    """A regular file inside the specified real local root, never a URL."""
    try:
        path, root = Path(path), Path(root)
        # A replaced output/assets/cache root must not become a file server for
        # another directory. The caller always supplies home/<fixed directory>.
        if root.is_symlink() or path.is_symlink():
            return None
        resolved = path.resolve(strict=True)
        if (resolved.suffix.lower() not in extensions or not resolved.is_file()
                or not resolved.is_relative_to(root.absolute())
                or resolved.stat().st_size == 0):
            return None
        return resolved
    except (OSError, ValueError, RuntimeError):
        return None


def project_source(studio, home, slug):
    """Resolve the same final movie as the original /f/video/<slug> route."""
    try:
        home = Path(home).resolve()
        found = studio.mv.find_job(slug)
        job = Path(found).resolve(strict=True) if found else None
        if job is None or not job.is_dir() or not job.is_relative_to(home / "output"):
            return None
        video = _file(studio.mv.final_video(job), home / "output", _VIDEO)
        return Source(video, "Video screenshot", "/f/video/" + quote(slug, safe="")) if video else None
    except (OSError, ValueError, RuntimeError, AttributeError):
        return None


def latest_outputs(studio, home):
    """One directory-only scan, reused for all recipes in a catalogue read."""
    home = Path(home).resolve()
    result, dates = {}, {}
    try:
        jobs = studio.mv.list_jobs()
    except (OSError, ValueError, RuntimeError, AttributeError):
        return result
    # A catalogue request does no media decoding and its supplemental lookup is
    # bounded even if an old studio accumulates thousands of working folders.
    for found in list(jobs)[:1000]:
        try:
            job = Path(found).resolve(strict=True)
            if not job.is_dir() or not job.is_relative_to(home / "output"):
                continue
            style_path = _file(job / "style.txt", home / "output", {".txt"})
            if style_path is None or style_path.stat().st_size > 500:
                continue
            style = style_path.read_text(encoding="utf-8").strip()
            if not _STYLE.fullmatch(style):
                continue
            video = _file(studio.mv.final_video(job), home / "output", _VIDEO)
            if video and video.stat().st_mtime_ns > dates.get(style, -1):
                dates[style] = video.stat().st_mtime_ns
                result[style] = Source(video, "From your video", "/f/video/" + quote(job.name, safe=""))
        except (OSError, ValueError, RuntimeError, UnicodeError):
            continue
    return result


@lru_cache(maxsize=128)
def _verified_frame(path, size, modified, expected):
    """Validate a packaged screenshot once per immutable file identity."""
    file = Path(path)
    return size <= _MAX_IMAGE and hashlib.sha256(file.read_bytes()).hexdigest() == expected


def _packaged_frame(home, style):
    root = Path(home).resolve() / "cr_recipe_frames"
    manifest = _file(root / "manifest.json", root, {".json"})
    if manifest is None:
        return None
    data = _read(manifest)
    frames = data.get("frames") if isinstance(data.get("frames"), dict) else {}
    row = frames.get(style)
    # Only labelled style references (schema 2). Retired engine sample frames
    # (schema 1) are never shown, even if an old file is still on disk.
    # A labelled style reference credits its channel; a ready-made recipe's own picture was drawn by Studio.
    if (data.get("schema") != 2 or not isinstance(row, dict)
            or row.get("kind") not in ("style_reference", "studio_sample") or row.get("file") != style + ".jpg"):
        return None
    expected, channel = row.get("sha256"), str(row.get("channel") or "").strip()[:60]
    if not isinstance(expected, str) or not _SHA.fullmatch(expected) or (row["kind"] == "style_reference" and not channel):
        return None
    path = _file(root / row["file"], root, {".jpg"})
    if path is None:
        return None
    try:
        stat = path.stat()
        if _verified_frame(str(path), stat.st_size, stat.st_mtime_ns, expected):
            return Source(path, "Style reference · " + channel if row["kind"] == "style_reference"
                          else "Made with ChannelRecipe Studio")
    except OSError:
        pass
    return None


def _learned_sample(home, info):
    """The sample a creator chose for a learned style (look.style_image under assets/styles/learned/), or None."""
    rel = str(((info.get("look") or {}) if isinstance(info.get("look"), dict) else {}).get("style_image") or "")
    if not rel.startswith("styles/learned/") or ".." in rel:
        return None
    root = Path(home).resolve() / "assets"
    path = _file(root / rel, root / "styles" / "learned", {".jpg", ".png"})
    if path is None or path.stat().st_size > _MAX_IMAGE:
        return None
    return Source(path, "Your style sample")


def _study_frame(home, style, info, bridge):
    """Show generated validation frames, never reference-board/source frames."""
    import cr_style_clone as clone
    details = info.get("cr_style_clone")
    if not isinstance(details, dict) or not _OPERATION.fullmatch(str(details.get("operation_id") or "")):
        return None
    try:
        folder, saved = clone._saved(home, details["operation_id"], bridge)
        folder = Path(folder).resolve()
        if (not folder.is_relative_to(Path(home).resolve() / "output/cr-style-clone")
                or saved.get("state") not in {"complete", "complete_local"}
                or (saved.get("spec") or {}).get("style_id") != style
                or not clone._published(home, saved)):
            return None
        checkpoint = _read(folder / "validated-checkpoint.json")
        record = checkpoint.get("record") or {}
        if (not isinstance(record, dict) or record.get("browser") is not True
                or checkpoint.get("spec_sha256") != clone._hash(saved["spec"])
                or not isinstance(checkpoint.get("assets"), dict)):
            return None
        # Bind the frames to the saved, successfully browser-tested kit. Never
        # surface a reference picture or a half-written/replaced graphics kit.
        for name in ("genkit/kit.js", "genkit/kit.json"):
            path = _file(folder / name, folder, {Path(name).suffix})
            if path is None or clone._file_hash(path) != checkpoint["assets"].get(name):
                return None
        critique = record.get("critique") or {}
        if not isinstance(critique, dict):
            return None
        if critique.get("after") and not critique.get("reverted"):
            version = "frames_v3"
        elif not (folder / "reflearn/frames_v2").exists() and not (folder / "reflearn/frames_v3").exists():
            version = "frames_v1"
        else:
            # Older checkpoints do not retain the accepted repair's frame set.
            # Ambiguous preview provenance is less useful than an honest gap.
            return None
        types = record.get("types") or []
        if not isinstance(types, list):
            return None
        # Prefer a full semantic scene over a timecard when the kit contains it;
        # all candidates still come from its accepted real browser capture.
        priority = [name for name in ("diorama", "scene", "environment", "doorway", "overhead") if name in types]
        priority += [name for name in types if name not in priority]
        for name in priority[:40]:
            if not isinstance(name, str) or not _STYLE.fullmatch(name):
                continue
            path = _file(folder / "reflearn" / version / (name + "_1.jpg"), folder, {".jpg"})
            if path and path.stat().st_size <= _MAX_IMAGE:
                return Source(path, "Animation preview")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return None
    return None


def recipe_source(studio, home, style, bridge, *, latest=None):
    """Choose an exact style's own video, learned-style frame or style reference."""
    if not isinstance(style, str) or not _STYLE.fullmatch(style):
        return None
    styles = getattr(studio.styles, "STYLES", {})
    info = styles.get(style)
    if not isinstance(info, dict):
        return None
    outputs = latest if latest is not None else latest_outputs(studio, home)
    # Your own latest video in this recipe first, then a learned style's own
    # frames, then the recipe's labelled style reference. The retired engine
    # sample films are no longer used for previews.
    return (outputs.get(style) or _learned_sample(home, info) or _study_frame(home, style, info, bridge)
            or _packaged_frame(home, style))


def _prune(root):
    try:
        entries = [(p.stat().st_mtime_ns, p.stat().st_size, p)
                   for p in root.glob("*.jpg") if not p.is_symlink() and p.is_file()]
        total, count = sum(row[1] for row in entries), len(entries)
        for _, size, path in sorted(entries):
            if count <= _CACHE_FILES and total <= _CACHE_BYTES:
                break
            path.unlink(missing_ok=True)
            total, count = total - size, count - 1
    except OSError:
        pass


def screenshot(home, source, *, run=None):
    """Return a real JPEG frame; at most two decoders, ten seconds each total."""
    if source.path.suffix.lower() in _IMAGE:
        if source.path.stat().st_size > _MAX_IMAGE:
            raise Unavailable()
        return source.path
    if source.path.suffix.lower() not in _VIDEO:
        raise Unavailable()
    run = run or subprocess.run
    home = Path(home).resolve()
    root = home / ".cr-workspace-previews"
    if root.is_symlink():
        raise Unavailable()
    stat = source.path.stat()
    identity = f"frame-v2\0{source.path}\0{stat.st_size}\0{stat.st_mtime_ns}\0{stat.st_ino}"
    name = hashlib.sha256(identity.encode()).hexdigest() + ".jpg"
    cached = _file(root / name, root, {".jpg"})
    if cached and cached.stat().st_size <= _MAX_IMAGE:
        return cached
    if not _WORKERS.acquire(timeout=2):
        raise Busy()
    temporary = None
    try:
        cached = _file(root / name, root, {".jpg"})
        if cached and cached.stat().st_size <= _MAX_IMAGE:
            return cached
        root.mkdir(mode=0o700, exist_ok=True)
        if root.resolve() != home / ".cr-workspace-previews":
            raise Unavailable()
        temporary = root / ("." + secrets.token_hex(12) + ".jpg")
        deadline = time.monotonic() + 10
        # eight seconds in shows the story rather than a dark cold open; short films fall back to 1 s, then 0 s
        for second in (8, 1, 0):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-protocol_whitelist", "file,pipe", "-threads", "1", "-ss", str(second), "-i", str(source.path),
                "-frames:v", "1", "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
                "-an", "-threads", "1", "-q:v", "3", "-f", "image2", str(temporary)]
            try:
                result = run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=remaining, check=False)
            except (OSError, subprocess.TimeoutExpired):
                break
            if result.returncode == 0 and temporary.is_file():
                raw = temporary.read_bytes()
                if 4 <= len(raw) <= _MAX_IMAGE and raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9"):
                    os.chmod(temporary, 0o600)
                    os.replace(temporary, root / name)
                    _prune(root)
                    return root / name
        raise Unavailable()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _WORKERS.release()
