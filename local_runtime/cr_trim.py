"""Trim a finished video without AI: a new start, a new end and, if wanted, one part cut out of the middle.

thatguygab (Discord, 2026-10-30): "Sometimes I just want to trim the video to match the VO but I need to prompt it
which takes even longer." The timeline editor only moves cuts and fills ranges, and Shorts don't open in it. This cuts
the finished file itself on this computer with FFmpeg: no Claude usage, no money.

- Every cut is frame-exact: the kept parts are re-encoded (picture and sound trimmed together, so the voice stays in
  sync, and captions burned into the picture stay with it).
- The untrimmed video is kept in the video's working folder (cr-trim/original.mp4) and Undo puts it back as it was.
- A second trim cuts the original again (the kept parts are composed in the original's time), so trims never stack
  re-encodes.
- A trim belongs to the exact file it made: when the video is rendered again (timeline), the old trim is forgotten
  and its original is replaced by the new render at the next trim.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

BACKUP_DIR = "cr-trim"                    # in the video's working folder
ORIGINAL = "original.mp4"
RECORD = "trim.json"
MIN_KEEP_S = 1.0                          # a trim keeps at least this much video
MIN_CUT_S = 0.05                          # a smaller change is no change
CRF = "18"                                # visually the same as the render it replaces
TIMEOUT_S = 3 * 60 * 60                   # a long video on a slow PC; the run is in the background anyway
REPLACE_TRIES = 10                        # Windows: a player can hold the file a moment longer

_runs: dict = {}                          # str(job) -> the trim running or last run for it
_guard = threading.Lock()


class Refused(ValueError):
    """A trim or undo that can't be done, said plainly for the creator."""


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def probe(video: Path) -> dict:
    """Length, frame rate and whether the video has sound."""
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,r_frame_rate",
                "-of", "json", str(video)], timeout=60)
    data = json.loads(out.stdout or "{}")
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        raise Refused("FFmpeg couldn't read this video's length.") from None
    streams = data.get("streams") or []
    rate = next((s.get("r_frame_rate") for s in streams if s.get("codec_type") == "video"), "") or ""
    return {"duration": duration, "audio": any(s.get("codec_type") == "audio" for s in streams), "rate": rate}


def kept(duration: float, start, end, remove=None) -> list:
    """The parts kept, in seconds of this video: [start, end] minus the removed range."""
    try:
        start, end = float(start), float(end)
        cut = None if remove in (None, [], ()) else (float(remove[0]), float(remove[1]))
    except (TypeError, ValueError, IndexError):
        raise Refused("Set the start and end as times in the video.") from None
    start, end = max(0.0, start), min(duration, end)
    if end - start < MIN_KEEP_S:
        raise Refused("The end must come at least a second after the start.")
    parts = [(start, end)]
    if cut is not None:
        a, b = max(start, min(cut)), min(end, max(cut))
        if b - a >= MIN_CUT_S:
            parts = [(start, a), (b, end)]
            parts = [(x, y) for x, y in parts if y - x >= MIN_CUT_S]
    if sum(y - x for x, y in parts) < MIN_KEEP_S:
        raise Refused("That would leave less than a second of video.")
    if len(parts) == 1 and parts[0][0] < MIN_CUT_S and duration - parts[0][1] < MIN_CUT_S:
        raise Refused("Nothing to trim: move the start or the end first.")
    return [(round(x, 3), round(y, 3)) for x, y in parts]


def compose(previous: list, parts: list) -> list:
    """Parts of a trimmed video, given in its own time, as parts of the original it was cut from."""
    out = []
    for x, y in parts:
        offset = 0.0
        for a, b in previous:
            length = b - a
            lo, hi = max(x, offset), min(y, offset + length)
            if hi - lo > 1e-6:
                out.append((round(a + lo - offset, 3), round(a + hi - offset, 3)))
            offset += length
    merged = []
    for a, b in out:
        if merged and abs(merged[-1][1] - a) < 1e-3:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def command(src: Path, dst: Path, parts: list, audio: bool) -> list:
    """The FFmpeg call that keeps these parts of src, re-encoded so every cut is exact and the sound stays in sync."""
    graph, joins = [], []
    for i, (a, b) in enumerate(parts):
        graph.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        joins.append(f"[v{i}]")
        if audio:
            graph.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
            joins.append(f"[a{i}]")
    graph.append("".join(joins) + f"concat=n={len(parts)}:v=1:a={1 if audio else 0}[v]" + ("[a]" if audio else ""))
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
            "-i", str(src), "-filter_complex", ";".join(graph), "-map", "[v]"]
    if audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    return args + ["-c:v", "libx264", "-preset", "veryfast", "-crf", CRF, "-pix_fmt", "yuv420p",
                   "-map_metadata", "0", "-movflags", "+faststart", str(dst)]


def _signature(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def record(job: Path, video: Path) -> dict:
    """This video's trim, when the video is still the file that trim made (else {})."""
    saved = _read(Path(job) / BACKUP_DIR / RECORD)
    backup = Path(job) / BACKUP_DIR / ORIGINAL
    try:
        if saved and backup.is_file() and saved.get("out") == _signature(Path(video)):
            return saved
    except OSError:
        pass
    return {}


def _replace(src: Path, dst: Path):
    """os.replace, waiting briefly on Windows for a player to let go of the file; across drives, a move."""
    for attempt in range(REPLACE_TRIES):
        try:
            if src.stat().st_dev == dst.parent.stat().st_dev:
                os.replace(src, dst)
            else:
                shutil.move(str(src), str(dst))
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise Refused("Another program is using the video file. Close it there, then try again.") from None
            time.sleep(0.5)


def status(job: Path, video: Path) -> dict:
    """What the viewer needs: the length now, whether it is trimmed (Undo), and a trim running or failed."""
    job, video = Path(job), Path(video)
    with _guard:
        run = dict(_runs.get(str(job)) or {})
    saved = record(job, video)
    out = {"trimmed": bool(saved), "running": run.get("state") == "running",
           "progress": run.get("progress", 0.0), "error": run.get("error") if run.get("state") == "failed" else ""}
    if saved:
        out.update(original_duration=saved.get("original_duration"), parts=saved.get("parts"))
    if video.is_file() and not out["running"]:
        try:
            out["duration"] = round(probe(video)["duration"], 3)
        except (Refused, OSError, ValueError, subprocess.SubprocessError):
            pass
    return out


def start(job: Path, video: Path, start_s, end_s, remove=None, *, wait=False) -> dict:
    """Check the trim and start it in the background (or wait for it, for tests and Claude Code tools)."""
    job, video = Path(job), Path(video)
    if not video.is_file():
        raise Refused("This video has no finished file to trim.")
    with _guard:
        if (_runs.get(str(job)) or {}).get("state") == "running":
            raise Refused("This video is already being trimmed.")
    info = probe(video)
    parts = kept(info["duration"], start_s, end_s, remove)
    saved = record(job, video)
    source = job / BACKUP_DIR / ORIGINAL if saved else video
    if saved:
        parts = compose([tuple(p) for p in saved.get("parts") or []], parts)
        info = probe(source)
    with _guard:
        if (_runs.get(str(job)) or {}).get("state") == "running":
            raise Refused("This video is already being trimmed.")
        _runs[str(job)] = {"state": "running", "progress": 0.0, "at": time.time()}
    work = threading.Thread(target=_trim, args=(job, video, source, parts, info, bool(saved)), daemon=True,
                            name="cr-trim")
    work.start()
    if wait:
        work.join()
    return {"started": True, "parts": parts, "length": round(sum(b - a for a, b in parts), 3)}


def _trim(job: Path, video: Path, source: Path, parts: list, info: dict, again: bool):
    part = video.with_name(video.stem + ".cr-trim.part.mp4")
    total = sum(b - a for a, b in parts) or 1.0
    try:
        proc = subprocess.Popen(command(source, part, parts, info["audio"]), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        errors = []
        reader = threading.Thread(target=lambda: errors.extend(proc.stderr), daemon=True)
        reader.start()
        began = time.time()
        for line in proc.stdout:                                   # -progress: key=value lines
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit():
                with _guard:
                    _runs[str(job)]["progress"] = round(min(0.99, int(value) / 1e6 / total), 3)
            if time.time() - began > TIMEOUT_S:
                proc.kill()
        proc.wait()
        reader.join(timeout=5)
        if proc.returncode != 0 or not part.is_file() or part.stat().st_size < 1000:
            tail = " ".join("".join(errors).split())[-300:]
            raise Refused("FFmpeg couldn't cut this video" + (f": {tail}" if tail else "."))
        backups = job / BACKUP_DIR
        backups.mkdir(exist_ok=True)
        if not again:                                              # the untrimmed video is kept as it was
            _replace(video, backups / ORIGINAL)
        try:
            _replace(part, video)
        except Exception:
            if not again:
                _replace(backups / ORIGINAL, video)
            raise
        (backups / RECORD).write_text(json.dumps({
            "parts": parts, "original_duration": round(info["duration"], 3), "video": video.name,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"), "out": _signature(video)}, indent=1), encoding="utf-8")
        with _guard:
            _runs[str(job)] = {"state": "done", "progress": 1.0, "at": time.time()}
    except Exception as error:                                     # the video is left exactly as it was
        part.unlink(missing_ok=True)
        message = str(error) if isinstance(error, Refused) else f"The trim stopped: {error}"
        with _guard:
            _runs[str(job)] = {"state": "failed", "progress": 0.0, "error": message[:400], "at": time.time()}


def undo(job: Path, video: Path) -> dict:
    """Put the untrimmed video back."""
    job, video = Path(job), Path(video)
    with _guard:
        if (_runs.get(str(job)) or {}).get("state") == "running":
            raise Refused("Wait for the trim to finish, then undo it.")
    saved = record(job, video)
    if not saved:
        raise Refused("This video has no trim to undo (it may have been rendered again since).")
    _replace(job / BACKUP_DIR / ORIGINAL, video)
    (job / BACKUP_DIR / RECORD).unlink(missing_ok=True)
    with _guard:
        _runs.pop(str(job), None)
    return {"undone": True, "duration": saved.get("original_duration")}
