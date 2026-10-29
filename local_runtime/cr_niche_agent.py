"""The niche agent's own tools (`/niche-agent` in Claude Code): free YouTube reads, the outlier maths, the owner's
teaching and the creator's saved ideas.

Everything here is free. YouTube is read the way a logged-out browser reads it, through Studio's own yt-dlp
(cr_ytdlp.py: public pages only, no key, no sign-in, never cookies), paced, cached for hours, and it stops for half an
hour the moment YouTube asks this computer to prove it isn't a bot (never bypassed). Nothing here spends money or
starts a video: a saved idea only fills in Create.

- teaching: the owner's rules and examples (ChannelRecipe's server serves the newest; Studio keeps the copy it shipped
  with, niche_teaching/). The numbers the checks use sit in a ```json block in rules.md, so changing a threshold there
  reaches every creator without a release.
- search(): YouTube's own filtered search ("this month, by views"), many queries at once, grouped by channel.
- channel_stats(): one read of a channel's Videos (or Shorts) tab gives every upload's views and YouTube's own "3 weeks
  ago" dates; the first upload and the proof video get their exact dates from their own pages. channel_numbers() turns
  that into the rule fields: first upload, uploads in 30 days, median and recent average views, banger:flop, the best
  video's views ÷ subscribers, the one-hit test, and a pass/fail list against the rules.
- look_sheet(): the newest thumbnails in one picture, for the "is it AI slop, can ChannelRecipe make it" look.
- ideas: <Studio folder>/ideas/ideas.json, shown in Create under Saved ideas; using one fills Create in.
- install(): Studio's routes for the page and the tools (saved ideas, the teaching, the niche database's breakouts).

Standard library only at import (the studio tool server imports it); Flask is imported inside install().
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path(__file__).resolve().parent
PORT = 7860

# ── the numbers (rules.md's ```json block overrides these; these are the same values, for a missing file) ─────

DEFAULTS = {
    "long": {"banger_views": 50_000, "flop_views": 5_000, "flop_share_of_median": 0.10, "min_banger_flop_ratio": 7,
             "great_banger_flop_ratio": 10, "max_first_upload_days": 60, "great_first_upload_days": 30,
             "min_uploads_30d": 3, "min_median_views": 30_000, "preferred_max_subs": 15_000, "max_subs": 50_000,
             "min_views_per_sub": 20, "hero_views_per_sub": 100, "one_hit_top_share": 0.60, "min_bangers": 2,
             "judge_after_days": 7, "window_days": 60, "search_min_views": 30_000, "min_minutes": 4},
    "shorts": {"confirmed": False, "banger_views": 500_000, "flop_views": 20_000, "flop_share_of_median": 0.10,
               "min_banger_flop_ratio": 7, "great_banger_flop_ratio": 10, "max_first_upload_days": 60,
               "great_first_upload_days": 30, "min_uploads_30d": 6, "min_median_views": 200_000,
               "preferred_max_subs": 50_000, "max_subs": 50_000, "min_views_per_sub": 20, "hero_views_per_sub": 100,
               "one_hit_top_share": 0.60, "min_bangers": 2, "judge_after_days": 3, "window_days": 60,
               "search_min_views": 300_000, "min_minutes": 0},
}
TAB_LIMIT = 200                    # uploads read from one tab: a channel with more is old, whatever its first video
MAX_QUERIES = 8                    # searches in one yt_search call
MAX_CHANNELS = 6                   # channels in one yt_channel_stats call
SEARCH_HOURS = 6
CHANNEL_HOURS = 12
WALL_SECONDS = 30 * 60             # after YouTube's "confirm you're not a bot": stop reading for half an hour
PACE_SECONDS = 1.0                 # between two YouTube reads from this computer
MAX_IDEAS = 300
WALL = ("YouTube asked this computer to confirm it isn't a bot, so the niche agent stopped reading YouTube for half an "
        "hour (it never signs in or uses cookies to get past that). The niche database still works; try the live "
        "checks again later.")
# YouTube's own search filters: sorted by view count or relevance, uploaded today/this week/this month/any time
SEARCH_FILTERS = {("views", "today"): "CAMSBAgCEAE%3D", ("views", "week"): "CAMSBAgDEAE%3D",
                  ("views", "month"): "CAMSBAgEEAE%3D", ("views", "year"): "CAMSBAgFEAE%3D",
                  ("views", "any"): "CAMSAhAB", ("relevance", "today"): "EgQIAhAB",
                  ("relevance", "week"): "EgQIAxAB", ("relevance", "month"): "EgQIBBAB",
                  ("relevance", "year"): "EgQIBRAB", ("relevance", "any"): "EgIQAQ%3D%3D"}
CHANNEL_SEARCH = "EgIQAg%3D%3D"
_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
_IDEA = re.compile(r"id_[a-f0-9]{12}\Z")
_HANDLE = re.compile(r"@[A-Za-z0-9_.\-]{2,100}\Z")


class YouTubeError(RuntimeError):
    """YouTube couldn't be read (a plain sentence for the creator)."""


class Wall(YouTubeError):
    """YouTube's bot check: stop reading for a while, never bypass it."""


# ── teaching ──────────────────────────────────────────────────────────────────────────────────────────────────

def teaching_folder(home=None):
    """The copy of the teaching Studio shipped with (niche_teaching/), or the repository's own in a checkout."""
    home = Path(home or HOME)
    for folder in (home / "niche_teaching", home.parent / "niche_agent" / "teaching"):
        if (folder / "rules.md").is_file():
            return folder
    return None


def parse_examples(text: str) -> list:
    out = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def thresholds(rules_text: str = "") -> dict:
    """DEFAULTS with rules.md's ```json block on top (only known keys, only numbers and booleans)."""
    out = {fmt: dict(values) for fmt, values in DEFAULTS.items()}
    match = re.search(r"```json\s*(\{.*?\})\s*```", str(rules_text or ""), re.S)
    if not match:
        return out
    try:
        block = json.loads(match.group(1))
    except ValueError:
        return out
    for fmt, values in (block.items() if isinstance(block, dict) else []):
        if fmt in out and isinstance(values, dict):
            for key, value in values.items():
                if key in out[fmt] and isinstance(value, (int, float)) and not isinstance(value, bool) \
                        and not isinstance(out[fmt][key], bool):
                    out[fmt][key] = value
                elif key in out[fmt] and isinstance(value, bool) and isinstance(out[fmt][key], bool):
                    out[fmt][key] = value
    return out


def bundled_teaching(home=None) -> dict:
    folder = teaching_folder(home)
    if folder is None:
        return {"rules": "", "examples": [], "source": "none"}
    rules = (folder / "rules.md").read_text(encoding="utf-8")
    try:
        examples = parse_examples((folder / "examples.jsonl").read_text(encoding="utf-8"))
    except OSError:
        examples = []
    return {"rules": rules, "examples": examples, "source": "bundled"}


def _state(home=None) -> Path:
    return Path(home or HOME) / ".cr-claude-code"


def cached_teaching(home=None):
    try:
        data = json.loads((_state(home) / "niche-teaching.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rules"), str) and data["rules"] else None


def save_teaching(home, data: dict) -> None:
    _write_json(_state(home) / "niche-teaching.json", {**data, "saved_at": time.time()})


def local_teaching(home=None) -> dict:
    """The last copy the server sent (kept by Studio), else the one Studio shipped with."""
    cached = cached_teaching(home)
    if cached:
        return {**cached, "source": "saved copy from ChannelRecipe"}
    return bundled_teaching(home)


def current_thresholds(home=None) -> dict:
    return thresholds(local_teaching(home).get("rules") or "")


# ── reading YouTube (free, paced, cached) ─────────────────────────────────────────────────────────────────────

_pace_lock = threading.Lock()
_last_read = [0.0]


def ytdlp_command(home=None) -> list:
    """Studio's own yt-dlp, beside this file (it trusts this computer's certificates like the browser, cr_trust)."""
    wrapper = HOME / "cr_ytdlp.py"
    return [sys.executable, str(wrapper)] if wrapper.is_file() else [sys.executable, "-m", "yt_dlp"]


def _wall_file(home=None) -> Path:
    return _state(home) / "youtube-wall.json"


def walled(home=None) -> bool:
    try:
        until = float(json.loads(_wall_file(home).read_text(encoding="utf-8")).get("until") or 0)
    except (OSError, ValueError, AttributeError):
        return False
    return time.time() < until


def _mark_wall(home=None) -> None:
    _write_json(_wall_file(home), {"until": time.time() + WALL_SECONDS, "at": time.time()})


def run_ytdlp(args: list, *, home=None, timeout: float = 120, runner=None) -> str:
    """One yt-dlp read; its standard output. Paced, and stopped for half an hour at YouTube's bot check."""
    if walled(home):
        raise Wall(WALL)
    with _pace_lock:
        wait = _last_read[0] + PACE_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_read[0] = time.monotonic()
    command = ytdlp_command(home) + ["--no-warnings", "--ignore-no-formats-error", *args]
    try:
        if runner is not None:
            done = runner(command)
        else:
            done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout, cwd=str(Path(home or HOME)),
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise YouTubeError("YouTube took too long to answer. Try again in a minute.") from None
    except OSError:
        raise YouTubeError("Studio's YouTube reader couldn't start. Run setup_check.") from None
    err = (done.stderr or "").lower()
    if "confirm you" in err and "bot" in err or "sign in to confirm" in err:
        _mark_wall(home)
        raise Wall(WALL)
    if "no module named yt_dlp" in err or "no module named 'yt_dlp'" in err:
        raise YouTubeError("Studio isn't installed in this folder yet (its YouTube reader is part of the install). "
                           "Run setup_check.")
    if done.returncode != 0 and not (done.stdout or "").strip():
        if "429" in err or "too many requests" in err:
            raise YouTubeError("YouTube said too many requests. Wait a few minutes and try again.")
        last = [line for line in (done.stderr or "").splitlines() if "ERROR" in line][-1:] or ["no answer"]
        raise YouTubeError("YouTube couldn't be read: " + last[0].replace("ERROR:", "").strip()[:200])
    return done.stdout or ""


def _cache_file(home, kind: str, key: str) -> Path:
    return _state(home) / "niche-cache" / f"{kind}-{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def _cached(home, kind: str, key: str, hours: float):
    try:
        data = json.loads(_cache_file(home, kind, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("value") if isinstance(data, dict) and time.time() - float(data.get("at") or 0) < hours * 3600 \
        else None


def _keep(home, kind: str, key: str, value) -> None:
    path = _cache_file(home, kind, key)
    _write_json(path, {"at": time.time(), "key": key, "value": value})
    try:                                                     # tidy: cached reads older than three days go
        for old in path.parent.glob("*.json"):
            if time.time() - old.stat().st_mtime > 3 * 86400:
                old.unlink()
    except OSError:
        pass


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".cr-tmp")
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=1)
    temporary.replace(path)


def _day(ts) -> str | None:
    try:
        return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _ymd(value) -> str | None:
    value = str(value or "")
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if re.fullmatch(r"\d{8}", value) else None


def entry_row(e: dict) -> dict | None:
    """One flat-playlist entry, kept small: id, title, views, seconds, YouTube's approximate date, channel."""
    if not isinstance(e, dict) or not _ID.fullmatch(str(e.get("id") or "")):
        return None
    if e.get("live_status") in ("is_upcoming", "is_live"):
        return None
    views = e.get("view_count")
    return {"id": e["id"], "title": str(e.get("title") or "")[:200],
            "views": int(views) if isinstance(views, (int, float)) else None,
            "seconds": int(e["duration"]) if isinstance(e.get("duration"), (int, float)) else None,
            "date": _day(e.get("timestamp") or e.get("release_timestamp")),
            "channel": str(e.get("channel") or e.get("uploader") or "")[:120],
            "channel_id": str(e.get("channel_id") or "") if _CHANNEL_ID.fullmatch(str(e.get("channel_id") or "")) else ""}


def compact(n) -> str:
    if not isinstance(n, (int, float)):
        return "?"
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= size:
            text = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return text + unit
    return str(int(n))


# ── search ────────────────────────────────────────────────────────────────────────────────────────────────────

def search_url(query: str, window: str = "month", sort: str = "views") -> str:
    code = SEARCH_FILTERS.get((sort, window)) or SEARCH_FILTERS[("views", "month")]
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query) + "&sp=" + code


def keep_search_rows(rows: list, *, fmt: str, min_views: int, min_minutes: float) -> list:
    out = []
    for r in rows:
        if not r or not r.get("views") or r["views"] < min_views:
            continue
        seconds = r.get("seconds")
        if fmt == "long" and seconds is not None and seconds < max(181, min_minutes * 60):
            continue
        if fmt == "shorts" and (seconds is None or seconds > 180):
            continue
        out.append(r)
    return out


def group_by_channel(rows: list, today: dt.date) -> list:
    groups: dict = {}
    for r in rows:
        key = r.get("channel_id") or r.get("channel") or "?"
        g = groups.setdefault(key, {"channel": r.get("channel"), "channel_id": r.get("channel_id"),
                                    "channel_url": f"https://www.youtube.com/channel/{r['channel_id']}"
                                    if r.get("channel_id") else "", "videos": 0, "best_views": 0, "total_views": 0,
                                    "newest": None, "queries": []})
        g["videos"] += 1
        g["best_views"] = max(g["best_views"], r["views"])
        g["total_views"] += r["views"]
        if r.get("date") and (g["newest"] is None or r["date"] > g["newest"]):
            g["newest"] = r["date"]
        for q in r.get("queries", []):
            if q not in g["queries"]:
                g["queries"].append(q)
    out = sorted(groups.values(), key=lambda g: (-g["best_views"], -g["videos"]))
    for g in out:
        g["queries"] = g["queries"][:4]
        g["newest_days_ago"] = (today - dt.date.fromisoformat(g["newest"])).days if g["newest"] else None
    return out


def search(queries: list, *, window: str = "month", sort: str = "views", fmt: str = "long", min_views=None,
           per_query: int = 30, home=None, today=None, runner=None) -> dict:
    today = today or dt.date.today()
    th = current_thresholds(home)[fmt]
    min_views = int(min_views if min_views is not None else th["search_min_views"])
    queries = [str(q).strip()[:120] for q in queries if str(q).strip()][:MAX_QUERIES]
    if not queries:
        raise ValueError("Give one or more search phrases (queries).")
    per_query = max(5, min(int(per_query or 30), 60))
    found: dict = {}
    notes = []
    for q in queries:
        key = f"{q}|{window}|{sort}|{per_query}"
        rows = _cached(home, "search", key, SEARCH_HOURS)
        if rows is None:
            try:
                out = run_ytdlp(["--flat-playlist", "-J", "--playlist-end", str(per_query), "--extractor-args",
                                 "youtubetab:approximate_date", search_url(q, window, sort)], home=home, runner=runner)
                data = json.loads(out or "{}")
            except Wall:
                raise
            except (YouTubeError, ValueError) as error:
                notes.append(f"{q!r}: {error}")
                continue
            rows = [r for r in (entry_row(e) for e in data.get("entries") or []) if r]
            _keep(home, "search", key, rows)
        for r in keep_search_rows(rows, fmt=fmt, min_views=min_views, min_minutes=th["min_minutes"]):
            row = found.setdefault(r["id"], {**r, "url": "https://youtu.be/" + r["id"], "queries": []})
            row["queries"].append(q)
    videos = sorted(found.values(), key=lambda r: -r["views"])
    for v in videos:
        v["days_ago"] = (today - dt.date.fromisoformat(v["date"])).days if v.get("date") else None
        v["minutes"] = round(v["seconds"] / 60, 1) if v.get("seconds") else None
    return {"window": window, "sort": sort, "format": fmt, "min_views": min_views, "queries": queries,
            "videos": videos[:80], "channels": group_by_channel(videos, today)[:40], "notes": notes,
            "dates": "YouTube's own 'x weeks ago' dates (approximate); yt_channel_stats reads exact ones"}


# ── channels ──────────────────────────────────────────────────────────────────────────────────────────────────

def channel_base(text: str):
    """(channel URL, video id) for what the creator gave: a UC id, @handle, channel link, or a video link."""
    text = str(text or "").strip()
    if _CHANNEL_ID.fullmatch(text):
        return f"https://www.youtube.com/channel/{text}", None
    if _HANDLE.fullmatch(text):
        return "https://www.youtube.com/" + text, None
    if not re.match(r"https?://", text) and re.match(r"(www\.|m\.)?(youtube\.com|youtu\.be)/", text):
        text = "https://" + text
    parsed = urllib.parse.urlsplit(text)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be" and _ID.fullmatch(parsed.path.strip("/")):
        return None, parsed.path.strip("/")
    if host.endswith("youtube.com"):
        parts = [p for p in parsed.path.split("/") if p]
        query = urllib.parse.parse_qs(parsed.query)
        if parts[:1] == ["watch"] and _ID.fullmatch((query.get("v") or [""])[0]):
            return None, query["v"][0]
        if parts[:1] in (["shorts"], ["live"]) and len(parts) > 1 and _ID.fullmatch(parts[1]):
            return None, parts[1]
        if parts[:1] == ["channel"] and len(parts) > 1 and _CHANNEL_ID.fullmatch(parts[1]):
            return f"https://www.youtube.com/channel/{parts[1]}", None
        if parts and parts[0].startswith("@"):
            return "https://www.youtube.com/" + urllib.parse.quote(parts[0], safe="@._-"), None
        if parts[:1] in (["c"], ["user"]) and len(parts) > 1:
            return f"https://www.youtube.com/{parts[0]}/{urllib.parse.quote(parts[1])}", None
    return None, None


def read_tab(base: str, tab: str, *, home=None, runner=None) -> dict:
    out = run_ytdlp(["--flat-playlist", "-J", "--playlist-end", str(TAB_LIMIT), "--extractor-args",
                     "youtubetab:approximate_date", base.rstrip("/") + "/" + tab], home=home, runner=runner,
                    timeout=180)
    try:
        data = json.loads(out or "{}")
    except ValueError:
        raise YouTubeError("YouTube's answer for that channel couldn't be read.") from None
    rows = [r for r in (entry_row(e) for e in data.get("entries") or []) if r]
    cid = str(data.get("channel_id") or "")
    subs = data.get("channel_follower_count")
    return {"channel": str(data.get("channel") or data.get("uploader") or "")[:120],
            "channel_id": cid if _CHANNEL_ID.fullmatch(cid) else "",
            "handle": str(data.get("uploader_id") or "")[:100] if str(data.get("uploader_id") or "").startswith("@")
            else "", "subs": int(subs) if isinstance(subs, (int, float)) else None, "entries": rows}


def exact_date(video_id: str, *, home=None, runner=None) -> str | None:
    if not _ID.fullmatch(str(video_id or "")):
        return None
    out = run_ytdlp(["--skip-download", "--print", "%(upload_date)s", "https://www.youtube.com/watch?v=" + video_id],
                    home=home, runner=runner, timeout=90)
    return _ymd((out.strip().splitlines() or [""])[0])


def video_channel(video_id: str, *, home=None, runner=None) -> str | None:
    out = run_ytdlp(["--skip-download", "--print", "%(channel_id)s", "https://www.youtube.com/watch?v=" + video_id],
                    home=home, runner=runner, timeout=90)
    cid = (out.strip().splitlines() or [""])[0]
    return cid if _CHANNEL_ID.fullmatch(cid) else None


def find_channel(name: str, *, home=None, runner=None):
    out = run_ytdlp(["--flat-playlist", "--playlist-end", "1", "--print", "%(channel_id)s",
                     "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(name) + "&sp="
                     + CHANNEL_SEARCH], home=home, runner=runner, timeout=90)
    cid = (out.strip().splitlines() or [""])[0]
    return cid if _CHANNEL_ID.fullmatch(cid) else None


def _age(date: str | None, today: dt.date):
    return (today - dt.date.fromisoformat(date)).days if date else None


def channel_numbers(entries: list, subs, *, today: dt.date, exact: dict | None = None, th: dict,
                    capped: bool = False) -> dict:
    """The rule fields for one channel tab (entries newest first, from read_tab), checked against th (one format's
    thresholds). exact: {video id: exact YYYY-MM-DD}; every other date is YouTube's approximate one."""
    exact = exact or {}
    rows = []
    for e in entries:
        if e.get("views") is None:
            continue
        date = exact.get(e["id"]) or e.get("date")
        rows.append({**e, "date": date, "exact": e["id"] in exact, "age": _age(date, today)})
    out = {"subscribers": subs, "uploads_listed": len(rows), "more_than_listed": bool(capped)}
    if not rows:
        return {**out, "checks": [], "passed": False, "failed": ["no public uploads on this tab"], "bonuses": []}
    oldest = rows[-1]
    first = None if capped else oldest
    out["first_upload"] = first["date"] if first else None
    out["first_upload_exact"] = bool(first and first["exact"])
    out["channel_age_days"] = first["age"] if first else None
    out["uploads_30d"] = sum(1 for r in rows if r["age"] is not None and r["age"] <= 30)
    judged = [r for r in rows if r["age"] is None or r["age"] >= th["judge_after_days"]]
    recent = judged[:15]
    median = statistics.median([r["views"] for r in recent]) if recent else 0
    newest10 = [r["views"] for r in judged[:10]]
    if len(newest10) >= 5:
        newest10 = sorted(newest10)[1:-1]
    out["median_views"] = int(median)
    out["recent_avg_views"] = int(sum(newest10) / len(newest10)) if newest10 else 0
    window = [r for r in judged if r["age"] is not None and r["age"] <= th["window_days"]] or judged[:12]
    flop_line = max(th["flop_views"], th["flop_share_of_median"] * median)
    bangers = sum(1 for r in window if r["views"] >= th["banger_views"])
    flops = sum(1 for r in window if r["views"] < flop_line)
    out.update(bangers=bangers, flops=flops, banger_flop=f"{bangers}:{flops}",
               banger_flop_window_days=th["window_days"], too_new_to_judge=len(rows) - len(judged))
    pool = [r for r in rows if r["age"] is not None and r["age"] <= th["window_days"]] or rows[:30]
    best = max(pool, key=lambda r: r["views"])
    per_sub = round(best["views"] / subs, 1) if subs else None
    out["best_video"] = {"title": best["title"], "url": "https://youtu.be/" + best["id"], "views": best["views"],
                         "date": best["date"], "date_exact": best["exact"], "days_ago": best["age"],
                         "views_per_sub": per_sub,
                         "times_median": round(best["views"] / median, 1) if median else None}
    total = sum(r["views"] for r in rows) or 1
    top_share = max(r["views"] for r in rows) / total
    all_bangers = sum(1 for r in rows if r["views"] >= th["banger_views"])
    one_hit = top_share >= th["one_hit_top_share"] or all_bangers < th["min_bangers"]
    out["top_video_share"] = round(top_share, 2)
    out["one_hit"] = one_hit
    minutes = [r["seconds"] / 60 for r in rows[:10] if r.get("seconds")]
    out["typical_minutes"] = round(statistics.median(minutes), 1) if minutes else None
    ratio_ok = bangers >= 1 and (flops == 0 or bangers / flops >= th["min_banger_flop_ratio"])
    checks = [
        ("first upload within %d days" % th["max_first_upload_days"],
         first is not None and first["age"] is not None and first["age"] <= th["max_first_upload_days"],
         (f"{first['date']} ({first['age']} days{'' if first['exact'] else ', approximate'})" if first and first["date"]
          else f"older: more than {len(rows)} uploads" if capped else "unknown")),
        ("%d+ uploads in 30 days" % th["min_uploads_30d"], out["uploads_30d"] >= th["min_uploads_30d"],
         str(out["uploads_30d"])),
        ("banger:flop %d:1 or better" % th["min_banger_flop_ratio"], ratio_ok,
         f"{bangers}:{flops} over the last {th['window_days']} days (banger {compact(th['banger_views'])}+, "
         f"flop under {compact(flop_line)})"),
        ("median views %s+" % compact(th["min_median_views"]), median >= th["min_median_views"], compact(median)),
        ("under %s subscribers" % compact(th["max_subs"]), subs is not None and subs < th["max_subs"],
         compact(subs) if subs is not None else "hidden"),
        ("best video %d× its subscribers" % th["min_views_per_sub"],
         per_sub is not None and per_sub >= th["min_views_per_sub"], f"{per_sub}×" if per_sub is not None else "?"),
        ("not a one-hit", not one_hit,
         f"top video {round(top_share * 100)}% of the views, {all_bangers} bangers in all"),
    ]
    out["checks"] = [{"rule": rule, "ok": bool(ok), "value": value} for rule, ok, value in checks]
    out["failed"] = [f"{c['rule']}: {c['value']}" for c in out["checks"] if not c["ok"]]
    out["passed"] = not out["failed"]
    bonuses = []
    if first and first["age"] is not None and first["age"] <= th["great_first_upload_days"]:
        bonuses.append(f"started {first['age']} days ago")
    if bangers and (flops == 0 or bangers / flops >= th["great_banger_flop_ratio"]) and bangers >= 3:
        bonuses.append(f"banger:flop {bangers}:{flops}")
    if subs is not None and subs < th["preferred_max_subs"]:
        bonuses.append(f"small: {compact(subs)} subscribers")
    if per_sub is not None and per_sub >= th["hero_views_per_sub"]:
        bonuses.append(f"hero outlier: {per_sub}× subscribers")
    out["bonuses"] = bonuses
    out["videos"] = [{"title": r["title"], "url": "https://youtu.be/" + r["id"], "views": r["views"],
                      "date": r["date"], "exact": r["exact"], "days_ago": r["age"],
                      "minutes": round(r["seconds"] / 60, 1) if r.get("seconds") else None} for r in rows[:12]]
    return out


def one_line(name: str, n: dict) -> str:
    best = n.get("best_video") or {}
    age = n.get("channel_age_days")
    return (f"{name} · {compact(n.get('subscribers'))} subs · "
            + (f"first upload {n.get('first_upload')} ({age} d)" if age is not None else "older channel")
            + f" · {n.get('uploads_30d', 0)} uploads in 30 d · banger:flop {n.get('banger_flop', '?')} · median "
            f"{compact(n.get('median_views'))} · recent avg {compact(n.get('recent_avg_views'))} · best "
            f"{compact(best.get('views'))} ({best.get('views_per_sub') or '?'}× subs)")


def channel_stats(channels: list, *, tab: str = "videos", home=None, today=None, runner=None) -> dict:
    today = today or dt.date.today()
    fmt = "shorts" if tab == "shorts" else "long"
    th = current_thresholds(home)[fmt]
    wanted = [str(c).strip() for c in channels if str(c).strip()][:MAX_CHANNELS]
    if not wanted:
        raise ValueError("Give one or more channels: a link, an @handle, a UC… id, or a video link.")
    results, notes = [], []
    for given in wanted:
        try:
            results.append(_one_channel(given, tab, th, home=home, today=today, runner=runner))
        except Wall:
            notes.append(WALL)
            break
        except YouTubeError as error:
            results.append({"given": given, "error": str(error)})
    note = ("Shorts numbers are a proposal the owner hasn't confirmed yet: say so when you use them."
            if fmt == "shorts" and not th.get("confirmed") else "")
    return {"tab": tab, "format": fmt, "channels": results, "notes": notes + ([note] if note else []),
            "dates": "first upload and the best video: exact dates from their own pages; the rest are YouTube's "
                     "'x weeks ago' dates (approximate)"}


def _one_channel(given: str, tab: str, th: dict, *, home, today, runner) -> dict:
    base, video = channel_base(given)
    matched_by_name = False
    if base is None and video:
        cid = video_channel(video, home=home, runner=runner)
        if not cid:
            raise YouTubeError(f"Couldn't find the channel of {given}.")
        base = f"https://www.youtube.com/channel/{cid}"
    elif base is None:
        cid = find_channel(given, home=home, runner=runner)
        if not cid:
            raise YouTubeError(f"No YouTube channel found for {given!r}. Give its link.")
        base, matched_by_name = f"https://www.youtube.com/channel/{cid}", True
    key = f"{base}|{tab}"
    saved = _cached(home, "channel", key, CHANNEL_HOURS)
    if saved is None:
        data = read_tab(base, tab, home=home, runner=runner)
        exact = {}
        rows = [r for r in data["entries"] if r.get("views") is not None]
        if rows:
            oldest = rows[-1]
            approx_age = _age(oldest.get("date"), today)
            if len(data["entries"]) < TAB_LIMIT and (approx_age is None or approx_age <= 150):
                exact[oldest["id"]] = exact_date(oldest["id"], home=home, runner=runner)
            if approx_age is None or approx_age <= 150:            # a young channel: its proof video's real date too
                recent = [r for r in rows if (_age(r.get("date"), today) or 0) <= th["window_days"]] or rows[:30]
                best = max(recent, key=lambda r: r["views"])
                if best["id"] not in exact:
                    exact[best["id"]] = exact_date(best["id"], home=home, runner=runner)
        saved = {**data, "exact": {k: v for k, v in exact.items() if v}, "capped": len(data["entries"]) >= TAB_LIMIT,
                 "read": today.isoformat()}
        _keep(home, "channel", key, saved)
        if saved["channel_id"]:
            _keep(home, "channel", f"https://www.youtube.com/channel/{saved['channel_id']}|{tab}", saved)
    numbers = channel_numbers(saved["entries"], saved.get("subs"), today=today, exact=saved.get("exact"), th=th,
                              capped=saved.get("capped", False))
    name = saved.get("channel") or given
    link = ("https://www.youtube.com/" + saved["handle"]) if saved.get("handle") else \
        (f"https://www.youtube.com/channel/{saved['channel_id']}" if saved.get("channel_id") else base)
    return {"channel": name, "channel_id": saved.get("channel_id"), "url": link, "given": given,
            **({"matched_by_name": True} if matched_by_name else {}), "summary": one_line(name, numbers),
            "read_on": saved.get("read"), **numbers}


# ── the look: newest thumbnails in one picture ─────────────────────────────────────────────────────────────────

def _fetch(url: str, timeout: float = 20) -> bytes:
    try:
        import cr_trust                                       # this computer's certificates, like the browser
        cr_trust.install()
    except Exception:                                         # noqa: BLE001 - Python's own certificates then
        pass
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000)


def look_sheet(channel: str = "", video_ids=None, *, tab: str = "videos", home=None, runner=None,
               fetch=None) -> dict:
    """The newest thumbnails (up to 6) in one picture on this computer, for Claude to look at with its Read tool."""
    fetch = fetch or _fetch
    titles = {}
    ids = [v for v in (video_ids or []) if _ID.fullmatch(str(v))][:6]
    name = ""
    if not ids and channel:
        stats = channel_stats([channel], tab=tab, home=home, runner=runner)["channels"][0]
        if stats.get("error"):
            raise YouTubeError(stats["error"])
        name = stats.get("channel") or channel
        for v in stats.get("videos", [])[:6]:
            vid = v["url"].rsplit("/", 1)[-1]
            ids.append(vid)
            titles[vid] = v["title"]
    if not ids:
        raise ValueError("Give a channel, or up to 6 video ids.")
    folder = _state(home) / "niche-looks"
    folder.mkdir(parents=True, exist_ok=True)
    pictures = []
    for vid in ids:
        path = folder / f"{vid}.jpg"
        if not path.is_file():
            try:
                path.write_bytes(fetch(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"))
            except Exception:                                 # noqa: BLE001 - one missing thumbnail is fine
                continue
        pictures.append(path)
    if not pictures:
        raise YouTubeError("The thumbnails couldn't be downloaded.")
    sheet = None
    try:
        from PIL import Image
        cell = (320, 180)
        cols = 3 if len(pictures) > 4 else 2 if len(pictures) > 1 else 1
        rows_n = (len(pictures) + cols - 1) // cols
        canvas = Image.new("RGB", (cols * cell[0], rows_n * cell[1]), (12, 12, 16))
        for i, path in enumerate(pictures):
            with Image.open(path) as picture:
                canvas.paste(picture.convert("RGB").resize(cell), ((i % cols) * cell[0], (i // cols) * cell[1]))
        digest = hashlib.sha256("|".join(p.stem for p in pictures).encode()).hexdigest()[:12]
        sheet = folder / f"sheet-{digest}.jpg"
        canvas.save(sheet, quality=82)
    except Exception:                                         # noqa: BLE001 - the single pictures still work
        sheet = None
    return {"channel": name, "sheet": str(sheet) if sheet else None, "pictures": [str(p) for p in pictures],
            "titles": [titles.get(p.stem, "") for p in pictures],
            "next": "Look at the picture (Read the sheet's path). Is it AI imagery (AI faces, AI film stills, "
                    "slow zooms on AI pictures)? What is it made of (code 3D, 2D/vector, stick, maps, data viz, "
                    "screen, footage)? Which ChannelRecipe style makes it, or would a clone?"}


# ── saved ideas ───────────────────────────────────────────────────────────────────────────────────────────────

def ideas_file(home=None) -> Path:
    return Path(home or HOME) / "ideas" / "ideas.json"


def load_ideas(home=None) -> list:
    try:
        data = json.loads(ideas_file(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("ideas") if isinstance(data, dict) else data
    return [r for r in rows if isinstance(r, dict) and _IDEA.fullmatch(str(r.get("id") or ""))] \
        if isinstance(rows, list) else []


def _text(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "")) if str(value).strip() else None
    except ValueError:
        return None


PROOF_TEXT = {"video_url": 300, "video_title": 200, "channel": 120, "channel_url": 300, "first_upload": 20,
              "banger_flop": 20}
PROOF_NUMBERS = ("views", "subs", "views_per_sub", "channel_age_days", "recent_avg_views", "median_views")


def clean_idea(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Each idea is an object with at least a title.")
    title = _text(raw.get("title"), 280)
    if not title:
        raise ValueError("Each idea needs a title.")
    fmt = "shorts" if str(raw.get("format") or "").lower().startswith("short") else "long"
    minutes = _number(raw.get("minutes"))
    proof_in = raw.get("proof") if isinstance(raw.get("proof"), dict) else {}
    proof = {k: _text(proof_in.get(k), n) for k, n in PROOF_TEXT.items() if proof_in.get(k)}
    proof.update({k: _number(proof_in.get(k)) for k in PROOF_NUMBERS if _number(proof_in.get(k)) is not None})
    idea = {"title": title, "niche": _text(raw.get("niche"), 160), "format": fmt,
            "minutes": int(min(60, max(1, round(minutes)))) if minutes and fmt == "long" else None,
            "style": _text(raw.get("style"), 140), "recipe": _text(raw.get("recipe"), 300),
            "twist": _text(raw.get("twist"), 600), "thumbnail": _text(raw.get("thumbnail"), 200),
            "risk": _text(raw.get("risk"), 400), "notes": _text(raw.get("notes"), 1500), "proof": proof}
    idea["id"] = "id_" + hashlib.sha256(title.casefold().encode()).hexdigest()[:12]
    return idea


def save_ideas(ideas: list, *, home=None) -> dict:
    if not isinstance(ideas, list) or not ideas:
        raise ValueError("Give the ideas to save (a list, each with a title).")
    cleaned = [clean_idea(i) for i in ideas[:20]]
    rows = {r["id"]: r for r in load_ideas(home)}
    now = time.time()
    for idea in cleaned:
        before = rows.get(idea["id"]) or {}
        rows[idea["id"]] = {**before, **idea, "status": "saved" if before.get("status") != "used" else "used",
                            "saved_at": before.get("saved_at") or now, "updated_at": now, "source": "niche-agent"}
    kept = sorted(rows.values(), key=lambda r: -float(r.get("saved_at") or 0))
    if len(kept) > MAX_IDEAS:                                 # dismissed ones go first, then the oldest
        kept = sorted(kept, key=lambda r: (r.get("status") == "dismissed", -float(r.get("saved_at") or 0)))[:MAX_IDEAS]
    _write_json(ideas_file(home), {"ideas": kept})
    return {"saved": [{"id": i["id"], "title": i["title"]} for i in cleaned], "total": len(kept)}


def set_idea_status(idea_id: str, status: str, *, home=None) -> dict:
    if not _IDEA.fullmatch(str(idea_id or "")):
        raise ValueError("Use an idea id from list_ideas (id_…).")
    if status not in ("saved", "used", "dismissed"):
        raise ValueError("Status is saved, used or dismissed.")
    rows = load_ideas(home)
    for row in rows:
        if row["id"] == idea_id:
            row["status"], row["updated_at"] = status, time.time()
            if status == "used":
                row["used_at"] = time.time()
            _write_json(ideas_file(home), {"ideas": rows})
            return row
    raise LookupError("That idea isn't saved here. list_ideas shows the saved ones.")


def list_ideas(status: str = "", *, home=None) -> list:
    rows = load_ideas(home)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows


def extra_instructions(idea: dict) -> str:
    """What Create's Extra instructions get from a saved idea: the twist and the proof, never a command."""
    proof = idea.get("proof") or {}
    lines = []
    if idea.get("twist"):
        lines.append("Angle: " + idea["twist"])
    if proof.get("video_title") or proof.get("video_url"):
        views = f", {compact(proof['views'])} views" if proof.get("views") else ""
        lines.append(f"Proven format to follow: \"{proof.get('video_title') or ''}\" by "
                     f"{proof.get('channel') or 'a small channel'}{views} ({proof.get('video_url') or ''}).")
    if idea.get("thumbnail"):
        lines.append("Thumbnail: " + idea["thumbnail"])
    if idea.get("notes"):
        lines.append(idea["notes"])
    return "\n".join(lines)[:4000]


# ── Studio's routes ───────────────────────────────────────────────────────────────────────────────────────────

_TEACHING_MEMO = {"at": 0.0, "data": None}
TEACHING_SECONDS = 600


def server_teaching(home, call) -> dict:
    """The newest teaching from ChannelRecipe (cached 10 minutes), else the saved copy, else the shipped one."""
    if _TEACHING_MEMO["data"] and time.time() - _TEACHING_MEMO["at"] < TEACHING_SECONDS:
        return _TEACHING_MEMO["data"]
    try:
        data = call("niche-agent/teaching")
        rules = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(rules, str) or not rules.strip():
            raise ValueError("empty")
        examples = data.get("examples") if isinstance(data.get("examples"), list) else []
        fresh = {"rules": rules, "examples": [e for e in examples if isinstance(e, dict)],
                 "updated": data.get("updated"), "version": data.get("version"), "source": "ChannelRecipe (newest)"}
        save_teaching(home, fresh)
        _TEACHING_MEMO.update(at=time.time(), data=fresh)
        return fresh
    except Exception:                                         # noqa: BLE001 - offline, an older server: the copies
        return local_teaching(home)


BREAKOUT_ARGS = {"q": 200, "max_age_days": 4, "max_subs": 9, "min_recent_avg": 12, "min_ratio": 8, "limit": 3,
                 "sort": 20}


def breakout_query(args) -> str:
    keep = {}
    for key, limit in BREAKOUT_ARGS.items():
        value = str(args.get(key) or "").strip()[:limit]
        if value:
            keep[key] = value
    return urllib.parse.urlencode(keep)


def install(studio, token: str, home=None) -> None:
    """Saved ideas for Create, and the niche agent's teaching and breakouts for the studio tools."""
    from flask import jsonify, request
    home = Path(home or HOME).resolve()
    app = studio.app

    def local_access():
        if request.host not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"} or request.headers.get("Origin") not in {
                None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}:
            raise PermissionError("Open the studio on localhost.")

    def answer(fn):
        try:
            local_access()
            response = jsonify(fn())
            status = 200
        except PermissionError as error:
            response, status = jsonify(error=str(error)), 403
        except LookupError as error:
            response, status = jsonify(error=str(error)), 404
        except ValueError as error:
            response, status = jsonify(error=str(error)), 400
        except RuntimeError as error:
            response, status = jsonify(error=str(error)), 503
        except Exception as error:                            # noqa: BLE001
            app.logger.error("Niche agent request %s failed", request.path, exc_info=error)
            response, status = jsonify(error="Studio couldn't answer this. Nothing changed."), 503
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response, status

    def bridge_call(path):
        import cr_bridge
        return cr_bridge.call(path)

    @app.get("/cr-ideas")
    def cr_ideas_list():
        def run():
            rows = [r for r in list_ideas(home=home) if r.get("status") != "dismissed"]
            return {"ideas": [{**r, "extra": extra_instructions(r)} for r in rows]}
        return answer(run)

    @app.post("/cr-ideas/<idea_id>")
    def cr_ideas_status(idea_id):                             # the local token is checked for every POST
        body = request.get_json(silent=True) or {}
        return answer(lambda: {"idea": set_idea_status(idea_id, str(body.get("status") or ""), home=home)})

    @app.get("/cr-niche-agent/teaching")
    def cr_niche_teaching():
        return answer(lambda: server_teaching(home, bridge_call))

    @app.get("/cr-niche-agent/breakouts")
    def cr_niche_breakouts():
        def run():
            try:
                return bridge_call("niche-agent/breakouts?" + breakout_query(request.args))
            except RuntimeError as error:
                raise RuntimeError(f"The niche database couldn't be read: {error}") from None
        return answer(run)
