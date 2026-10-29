"""How much Claude a video has used so far, in plain words, from what Studio itself recorded.

Swapnil (Claude Pro, 2026-10-29) couldn't tell how far his 18-minute 3D video had got or how much Claude it still
needed. Claude Code does not say how much of a plan's allowance is left, so Studio never shows a quota: only what it
counted itself.
- calls: each Claude step of the video's project (its cr-claude-cache files: finished, failed or running); minutes:
  the time those calls took (each call's "seconds" from Studio 112; earlier calls count without a time). Calls run
  side by side, so this is Claude time, not the time on the clock.
- 3D: the review's round and "about M review calls left" (cr_threed_resume's progress: the calls of this round not yet
  answered, plus each later round at most this round's shots; rounds end early when every shot passes).
"""
from __future__ import annotations

import json
from pathlib import Path

_seen: dict = {}            # path -> ((mtime_ns, size), (state, seconds)): each cache file is read once per change


def _call(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    mark = (st.st_mtime_ns, st.st_size)
    hit = _seen.get(str(path))
    if hit and hit[0] == mark:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    seconds = data.get("seconds")
    value = (str(data.get("state") or ""), float(seconds) if isinstance(seconds, (int, float)) else None)
    _seen[str(path)] = (mark, value)
    return value


def so_far(home, project_id) -> dict:
    """{"calls", "timed", "minutes", "running"} for one project's Claude steps."""
    out = {"calls": 0, "timed": 0, "minutes": 0, "running": 0}
    if not project_id:
        return out
    seconds = 0.0
    try:
        paths = sorted((Path(home) / "output" / "cr-claude-cache" / str(project_id)).glob("cc_*.json"))
    except OSError:
        return out
    for path in paths:
        got = _call(path)
        if not got or not got[0]:
            continue
        state, took = got
        out["calls"] += 1
        out["running"] += state == "running"
        if took is not None:
            out["timed"] += 1
            seconds += took
    out["minutes"] = round(seconds / 60)
    return out


def threed_review(job) -> dict:
    """The 3D review's round and calls left, or {} when this video has no 3D review under way."""
    try:
        import cr_threed_resume
        progress = json.loads((Path(job) / "threed" / cr_threed_resume.KEEP / "progress.json")
                              .read_text(encoding="utf-8"))
        return cr_threed_resume.calls_left(progress) if isinstance(progress, dict) else {}
    except (OSError, ValueError, TypeError, ImportError):
        return {}


def summary(home, job, project_id) -> dict:
    """{"calls", "minutes", ..., "threed": {...}, "line": the plain sentence for the cards}."""
    use = so_far(home, project_id)
    three = threed_review(job)
    parts = []
    if use["calls"]:
        n = use["calls"]
        said = f"Claude so far for this video: {n} call{'' if n == 1 else 's'}"
        if use["timed"] and use["timed"] == n:
            said += f", about {max(1, use['minutes'])} min of Claude time"
        elif use["timed"]:
            said += f", about {max(1, use['minutes'])} min of Claude time for the {use['timed']} Studio timed"
        parts.append(said + ".")
    if three and not three.get("finished") and three.get("rounds"):
        left = three["left"]
        parts.append(f"3D review: round {three['round']} of {three['rounds']}, about {left} review "
                     f"call{'' if left == 1 else 's'} left (fewer if the shots pass sooner).")
    return {**use, "threed": three, "line": " ".join(parts)}
