"""The picture check on every finished video: one that came out black stops instead of being handed over.

A Windows customer's cloned F1 documentary finished twice as "done" and was a black screen with the narration over it
(Studio 132, 2026-11-09): nothing between the render and the published file looked at the picture. Before a finished
video is published under its title, its keyframes are read (a second or two, even for an hour-long film) and the
frames with almost nothing lit are timed. A video black for most of its length is set aside as video.black.mp4, the
black stretches and the shots planned there go to the log (Copy diagnostics carries it), and the run stops with the
reason, so a Resume assembles the picture again instead of publishing the same file. A check that can't measure
never holds a video back.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

W, H = 64, 36
LIT = 40                # a pixel brighter than this (0-255) shows something; video black is 16
LIT_SHARE = 0.03        # a frame with less of it lit is black: a caption line over black lights about 2% of the frame
STOP_SHARE = 0.6        # black for this share of its length, a video stops (a title card or a fade lasts seconds)
MIN_FRAMES = 8          # fewer keyframes than this (a short video): one frame a second instead


def _grab(video, every_s: float, sync: list) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-nostats", "-v", "info"] + ([] if every_s else ["-skip_frame", "nokey"])
                          + ["-i", str(video), "-an", "-vf",
                             (f"fps=1/{every_s:g}," if every_s else "") + f"scale={W}:{H},format=gray,showinfo"]
                          + sync + ["-f", "rawvideo", "-"], capture_output=True, timeout=900)


def frames(video, every_s: float = 0.0) -> list:
    """[(seconds, lit share)] of the video's keyframes, or of one frame every `every_s` seconds."""
    import numpy as np
    r = _grab(video, every_s, ["-fps_mode", "passthrough"])
    if r.returncode and b"fps_mode" in r.stderr:                    # an FFmpeg older than 5.1
        r = _grab(video, every_s, ["-vsync", "passthrough"])
    times = [float(t) for t in re.findall(rb"pts_time:\s*(-?[\d.]+)", r.stderr)]
    data = np.frombuffer(r.stdout, dtype=np.uint8)
    n = min(len(times), data.size // (W * H))
    if not n:
        return []
    lit = (data[:n * W * H].reshape(n, W * H) > LIT).mean(axis=1)
    return [(times[i], float(lit[i])) for i in range(n)]


def duration(video) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def black_stretches(rows: list, total: float) -> list:
    """[(start, end)] seconds: each sampled frame stands for the time until the next one."""
    out = []
    for k, (t, lit) in enumerate(rows):
        if lit >= LIT_SHARE:
            continue
        end = rows[k + 1][0] if k + 1 < len(rows) else max(total, t)
        if out and abs(out[-1][1] - t) < 1e-6:
            out[-1] = (out[-1][0], end)
        else:
            out.append((t, end))
    return out


def measure(video) -> dict:
    total = duration(video)
    rows = frames(video)
    if len(rows) < MIN_FRAMES:
        rows = frames(video, 1.0)
    total = total or (rows[-1][0] + 1.0 if rows else 0.0)
    black = black_stretches(rows, total)
    seconds = sum(b - a for a, b in black)
    return {"seconds": round(total, 2), "frames": len(rows), "black_s": round(seconds, 2),
            "black": [[round(a, 2), round(b, 2)] for a, b in black][:50],
            "ok": not rows or not total or seconds < STOP_SHARE * total}


def _video_of(job: Path, fresh: str = "") -> Path:
    return job / fresh if fresh and fresh != "video.mp4" and (job / fresh).is_file() else job / "video.mp4"


def check(job, fresh: str = "", log=print) -> dict:
    """The finished video's picture, measured once per file (picture_qc.json)."""
    job = Path(job)
    video = _video_of(job, fresh)
    if not video.is_file():
        return {}
    out = job / "picture_qc.json"
    mark = f"{video.name}:{video.stat().st_size}:{video.stat().st_mtime_ns}"
    try:
        saved = json.loads(out.read_text(encoding="utf-8"))
        if saved.get("video") == mark:
            return saved
    except (OSError, ValueError):
        pass
    qc = {**measure(video), "video": mark}
    out.write_text(json.dumps(qc, indent=1), encoding="utf-8")
    log(f"picture check: black for {qc['black_s']:.0f} s of {qc['seconds']:.0f} s ({qc['frames']} frames looked at)")
    return qc


def _clock(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _shots_at(job: Path, black: list) -> list:
    """The planned shots that play in the black stretches (plan.json: [kind, file, leak, seconds] in timeline order)."""
    try:
        plan = json.loads((job / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names, at = [], 0.0
    for entry in plan if isinstance(plan, list) else []:
        try:
            name, dur = Path(str(entry[1] or "")).name, float(entry[3])
        except (TypeError, ValueError, IndexError):
            continue
        if name and any(a < at + dur and at < b for a, b in black) and name not in names:
            names.append(name)
        at += dur
    return names


def stop(job, qc: dict, fresh: str = "", log=print) -> None:
    """A black video goes aside (video.black.mp4) with what was planned there in the log, and the run stops."""
    job = Path(job)
    black = [tuple(x) for x in qc.get("black") or []]
    log("picture check: black " + ", ".join(f"{_clock(a)}-{_clock(b)}" for a, b in black[:12]))
    shots = _shots_at(job, black)
    if shots:
        log(f"picture check: shots planned there ({len(shots)}): " + ", ".join(shots[:15]))
    aside = job / "video.black.mp4"
    aside.unlink(missing_ok=True)
    _video_of(job, fresh).replace(aside)
    share = round(100 * qc["black_s"] / max(qc["seconds"], 1e-6))
    raise RuntimeError(f"The finished video came out black for {_clock(qc['black_s'])} of its {_clock(qc['seconds'])} "
                       f"({share}%), so Studio set it aside instead of handing it over. Press Resume to make the "
                       "picture again. If it stops here again, press Copy diagnostics and send it to ChannelRecipe "
                       "support.")


def install(engine):
    """Look at every finished video just before it is published under its title."""
    if getattr(engine, "_cr_picture_qc", False):
        return
    engine._cr_picture_qc = True
    original = engine.publish_named_copy

    def publish_named_copy(job, title, fresh=""):
        try:
            qc = check(job, fresh, engine.log)
        except Exception as e:                                          # noqa: BLE001 - a check, never a blocker
            engine.log(f"picture check: not measured ({type(e).__name__}: {str(e)[:80]})")
            qc = {}
        if qc and not qc.get("ok", True):
            stop(job, qc, fresh, engine.log)
        return original(job, title, fresh)

    engine.publish_named_copy = publish_named_copy
