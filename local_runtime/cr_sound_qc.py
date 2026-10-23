"""The sound check on every finished video: measured, never judged by ear or by a model.

Loudness (integrated LUFS and true peak, the ffmpeg EBU R128 meter) and the
music bed in the narration's pauses (how much of each pause has music or
ambience, and how far under the voice peak it sits), measured the way Studio
measures a reference channel. A learned style keeps its references' numbers
(look.sound.target), so the check says how close the video came. Saved as
job/sound_qc.json and shown on the project.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

LUFS_TARGET = -14.0
MEASURE_S = 360.0


def loudness(video) -> dict:
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(video), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=1800)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", tail)
    tp = re.search(r"Peak:\s*(-?[\d.]+)\s*dBFS", tail)
    return {"lufs": float(i.group(1)) if i else None, "true_peak": float(tp.group(1)) if tp else None}


def bed(video) -> dict:
    import cr_style_measure
    s = cr_style_measure.measure_sound(video, MEASURE_S) or {}
    return {"bed_in_pauses_db": s.get("bed_in_pauses_db"), "pauses_filled": s.get("filled_pauses_share")}


def verdict(qc: dict, target: dict) -> list:
    """Plain lines for the project page."""
    lines = []
    lufs, peak = qc.get("lufs"), qc.get("true_peak")
    if lufs is not None:
        ok = abs(lufs - LUFS_TARGET) <= 1.0 and (peak is None or peak <= -0.9)
        lines.append(("ok" if ok else "check", f"Loudness {lufs:.1f} LUFS, peak {peak:.1f} dB"
                      + ("" if ok else f" (YouTube plays at {LUFS_TARGET:.0f} LUFS, peak under -1 dB)")))
    filled, bed_db = qc.get("pauses_filled"), qc.get("bed_in_pauses_db")
    if filled is not None and bed_db is not None:
        text = f"Music or ambience in {round(filled * 100)}% of the pauses, about {abs(bed_db):.0f} dB under the voice"
        want_f, want_db = (target or {}).get("pauses_filled"), (target or {}).get("bed_in_pauses_db")
        if want_f is not None and want_db is not None:
            close = abs(filled - want_f) <= 0.2 and abs(bed_db - want_db) <= 6
            text += f" (the references: {round(want_f * 100)}%, about {abs(want_db):.0f} dB)"
            lines.append(("ok" if close else "check", text))
        else:
            lines.append(("ok", text))
    return [{"state": s, "text": t} for s, t in lines]


def check(job, target: dict = None, log=print) -> dict:
    job = Path(job)
    video = job / "video.mp4"
    if not video.is_file():
        return {}
    out = job / "sound_qc.json"
    mark = f"{video.stat().st_size}:{video.stat().st_mtime_ns}"
    try:
        saved = json.loads(out.read_text(encoding="utf-8"))
        if saved.get("video") == mark:
            return saved
    except (OSError, ValueError):
        pass
    qc = {**loudness(video), **bed(video)}
    qc["lines"] = verdict(qc, target or {})
    qc["video"] = mark
    out.write_text(json.dumps(qc, indent=1))
    log("sound check: " + "; ".join(row["text"] for row in qc["lines"]))
    return qc


def install(engine):
    """Measure every finished video just before it is published under its title."""
    if getattr(engine, "_cr_sound_qc", False):
        return
    engine._cr_sound_qc = True
    original = engine.publish_named_copy

    def publish_named_copy(job, title, fresh=""):
        try:
            style = (Path(job) / "style.txt").read_text(encoding="utf-8").strip()
            look = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}).get("look") or {}
            check(job, ((look.get("sound") or {}) if isinstance(look, dict) else {}).get("target"), engine.log)
        except Exception as e:                                          # noqa: BLE001 - a check, never a blocker
            engine.log(f"sound check: not measured ({type(e).__name__}: {str(e)[:80]})")
        return original(job, title, fresh)

    engine.publish_named_copy = publish_named_copy
