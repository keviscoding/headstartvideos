"""How much Claude a video has used so far, in plain words, from what Studio itself recorded.

Swapnil (Claude Pro, 2026-10-29) couldn't tell how far his 18-minute 3D video had got or how much Claude it still
needed. Claude Code does not say how much of a plan's allowance is left, so Studio never shows a quota: only what it
counted itself.
- calls: each Claude step of the video's project (its cr-claude-cache files: finished, failed or running); minutes:
  the time those calls took (each call's "seconds" from Studio 112; earlier calls count without a time). Calls run
  side by side, so this is Claude time, not the time on the clock.
- 3D: the review's round and "up to about M review calls left" (cr_threed_resume's progress: the calls of this round not yet
  answered, plus each later round at most this round's shots; rounds end early when every shot passes).
- overall (Swapnil, the same day: "Show the Claude credit/token consumption in the app per project & overall"): the
  same counts across every video, for today, the last 7 days and all time, and by video. Tokens are what Claude Code
  reported for each call (cache files from Studio 115 on: "tokens"); older calls count without them. Claude Code
  never says how much of a plan's allowance is left, so this is use, never a percentage of a limit. Studio's own
  Claude steps only: a creator's own chats in Claude Code and style studies aren't in these files.
"""
from __future__ import annotations

import json
import time
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
    at = data.get("at")
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}

    def count(*names):
        return sum(int(tokens[n]) for n in names if isinstance(tokens.get(n), (int, float))
                   and not isinstance(tokens.get(n), bool) and tokens[n] >= 0)
    value = (str(data.get("state") or ""), float(seconds) if isinstance(seconds, (int, float)) else None,
             # when: the call's own time (Studio 115 on), else the file's last change
             float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0 else st.st_mtime,
             # tokens in (the prompt, cached or not) and out (what Claude wrote), or None when not reported
             (count("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"), count("output_tokens"))
             if tokens else None)
    _seen[str(path)] = (mark, value)
    return value


def so_far(home, project_id) -> dict:
    """{"calls", "timed", "minutes", "running"} for one project's Claude steps."""
    out = {"calls": 0, "timed": 0, "minutes": 0, "running": 0, "tokens_in": 0, "tokens_out": 0, "tokens_counted": 0}
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
        state, took = got[0], got[1]
        out["calls"] += 1
        out["running"] += state == "running"
        if took is not None:
            out["timed"] += 1
            seconds += took
        if got[3]:
            out["tokens_counted"] += 1
            out["tokens_in"] += got[3][0]
            out["tokens_out"] += got[3][1]
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
        if use["tokens_counted"]:
            said += f", {_short(use['tokens_in'] + use['tokens_out'])} tokens"
        parts.append(said + ".")
    if three and not three.get("finished") and three.get("rounds"):
        left = three["left"]
        parts.append(f"3D review: round {three['round']} of {three['rounds']}, up to about {left} review "
                     f"call{'' if left == 1 else 's'} left (fewer if the shots pass sooner).")
    return {**use, "threed": three, "line": " ".join(parts)}


def _blank() -> dict:
    return {"calls": 0, "minutes": 0, "seconds": 0.0, "timed": 0, "tokens_in": 0, "tokens_out": 0, "tokens_counted": 0}


def _add(total: dict, got) -> None:
    state, took, _at, tokens = got
    total["calls"] += 1
    if took is not None:
        total["timed"] += 1
        total["seconds"] += took
    if tokens:
        total["tokens_counted"] += 1
        total["tokens_in"] += tokens[0]
        total["tokens_out"] += tokens[1]


def _done(total: dict) -> dict:
    total["minutes"] = round(total.pop("seconds") / 60)
    return total


def overall(home, names=None, now=None, limit=50) -> dict:
    """Studio's Claude use across every video: {"today", "week", "all": counts, "videos": [counts + slug/title, the
    most used first, at most `limit`], "line", "note"}. names: project id -> {"slug", "title"} (videos deleted since
    keep their counts, by id). Today is since this computer's midnight; week is the last 7 days."""
    now = time.time() if now is None else now
    midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    week = now - 7 * 86400
    names = names or {}
    totals = {"today": _blank(), "week": _blank(), "all": _blank()}
    videos = []
    try:
        folders = sorted(p for p in (Path(home) / "output" / "cr-claude-cache").iterdir() if p.is_dir())
    except OSError:
        folders = []
    for folder in folders:
        row, last = _blank(), 0.0
        try:
            paths = sorted(folder.glob("cc_*.json"))
        except OSError:
            continue
        for path in paths:
            got = _call(path)
            if not got or not got[0]:
                continue
            _add(row, got)
            _add(totals["all"], got)
            last = max(last, got[2])
            if got[2] >= week:
                _add(totals["week"], got)
            if got[2] >= midnight:
                _add(totals["today"], got)
        if row["calls"]:
            known = names.get(folder.name) or {}
            videos.append({**_done(row), "project_id": folder.name, "slug": known.get("slug") or "",
                           "title": known.get("title") or "A deleted video", "last_at": round(last)})
    videos.sort(key=lambda v: (-v["calls"], -v["last_at"]))
    out = {k: _done(v) for k, v in totals.items()}
    out["videos"] = videos[:limit]
    out["video_count"] = len(videos)
    out["line"] = line(out)
    out["note"] = ("Counted by Studio from its own Claude steps for your videos. Claude Code doesn't say how much of "
                   "your plan's allowance is left, so this shows what Studio used, not a percentage. Your own chats "
                   "in Claude Code and style studies aren't included. Tokens are counted from Studio 115 on.")
    return out


def words_for(total: dict) -> str:
    n = total["calls"]
    if not n:
        return "no Claude calls"
    text = f"{n} call{'' if n == 1 else 's'}"
    if total["timed"]:
        text += f", about {max(1, total['minutes'])} min of Claude time"
    if total["tokens_counted"]:
        text += f", {_short(total['tokens_in'] + total['tokens_out'])} tokens"
    return text


def _short(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e3 else str(n)


def line(out: dict) -> str:
    return (f"Claude used by Studio: today {words_for(out['today'])}; last 7 days {words_for(out['week'])}; "
            f"all time {words_for(out['all'])} across {out['video_count']} video{'' if out['video_count'] == 1 else 's'}.")
