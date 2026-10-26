"""Keep the original Kie footage watcher on its documented chat route.

This is a transport compatibility adapter for media shot logging. It does not
change the creative brain, source searches, watch windows, uploaded files,
shot prompts, or the original footage selection and review process.

Kie's Gemini 3.8 Flash chat documentation supports uploaded video URLs in the
same ``image_url`` envelope used by the original watcher:
https://docs.kie.ai/market/gemini/gemini-3-8-flash-openai
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import wraps
import json
import math
import os
from pathlib import Path
import re
import threading
import time


_diagnostic_lock = threading.Lock()


LEGACY_ENDPOINT = "https://api.kie.ai/gemini-2.5-flash/v1/chat/completions"
WATCH_ENDPOINT = "https://api.kie.ai/gemini-3-8-flash-openai/v1/chat/completions"


# Kie's watcher answers a short watch copy in 10-25 s when it works (measured 2026-10-22), but sometimes it never
# answers: the original waits 900 s a try, three tries, so one stuck video held a footage search for most of an hour.
# Five minutes a try is still far more than a working answer needs, and a stuck one now fails over to the next video.
WATCH_TIMEOUT_S = 300


def _watch_timeout(timeout):
    if isinstance(timeout, (int, float)):
        return (15, min(float(timeout), WATCH_TIMEOUT_S))
    if isinstance(timeout, tuple) and len(timeout) == 2 and isinstance(timeout[1], (int, float)):
        return (timeout[0], min(float(timeout[1]), WATCH_TIMEOUT_S))
    return (15, WATCH_TIMEOUT_S)


def _original_watch_payload(payload):
    """Match only the original one-shot video chat, without an explicit model.

    The current route selects its model by URL. An explicitly selected model
    belongs to its caller, so a future/custom watcher must keep that choice.
    """
    if not isinstance(payload, dict) or payload.get("model") not in (None, ""):
        return False
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        return False
    message = messages[0]
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 2:
        return False
    media, prompt = content
    return (isinstance(media, dict) and media.get("type") == "image_url"
            and isinstance(media.get("image_url"), dict)
            and isinstance(media["image_url"].get("url"), str)
            and isinstance(prompt, dict) and prompt.get("type") == "text"
            and isinstance(prompt.get("text"), str))


class _WatcherRequests:
    """Facade scoped to the original youtube module, never global requests."""

    _cr_kie_watch_route = True

    def __init__(self, original, module=None):
        self._original = original
        self._module = module

    def __getattr__(self, name):
        return getattr(self._original, name)

    def post(self, url, *args, **kwargs):
        watch = url == LEGACY_ENDPOINT and _original_watch_payload(kwargs.get("json"))
        if watch:
            url = WATCH_ENDPOINT
            kwargs["timeout"] = _watch_timeout(kwargs.get("timeout"))
        # Forward the exact object and all original upload, auth, and response
        # behavior; the original watcher owns parsing and recovery.
        try:
            return self._original.post(url, *args, **kwargs)
        except Exception as error:
            # The watcher's breaker (youtube._KIE_DOWN: three failed videos in a row and the search goes on without
            # Kie) counted only error answers: a watch that never answered went past it, so a stuck watcher cost
            # five minutes on every video, all search long. It counts now.
            errors = getattr(self._original, "exceptions", None)
            kinds = tuple(k for k in (getattr(errors, "Timeout", None), getattr(errors, "ConnectionError", None))
                          if isinstance(k, type))
            down = getattr(self._module, "_KIE_DOWN", None)
            if watch and kinds and isinstance(error, kinds) and isinstance(down, dict):
                down["fails"] = int(down.get("fails") or 0) + 1
            raise


def _raw_entries(stdout):
    count = 0
    for line in (stdout if isinstance(stdout, str) else "").splitlines():
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and entry.get("id"):
                count += 1
        except (ValueError, TypeError):
            pass
    return count


def _error_category(stderr):
    # Store only a fixed category, never stderr, URLs, queries, cookie paths,
    # credentials, or the titles/channel names returned by the search.
    text = (stderr if isinstance(stderr, str) else "").lower()
    if re.search(r"not a bot|bot.?check|captcha", text):
        return "bot_check"
    if re.search(r"sign.?in|log.?in|authentication required|cookies required", text):
        return "sign_in_required"
    if "429" in text or "too many requests" in text:
        return "rate_limited"
    if re.search(r"timed? ?out|timeout|connection|network|unable to download|temporary failure|name or service not known|http error 5\d\d", text):
        return "network"
    return "unknown"


class _SearchSubprocess:
    """Observe only ytsearch inside the original search function on this thread."""

    def __init__(self, original, state):
        self._original, self._state = original, state

    def __getattr__(self, name):
        return getattr(self._original, name)

    def run(self, *args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        record = getattr(self._state, "record", None)
        selected = (record is not None and isinstance(command, (list, tuple))
                    and any(isinstance(arg, str) and re.match(r"^ytsearch\d+:", arg) for arg in command))
        if not selected:
            return self._original.run(*args, **kwargs)
        record["transport"] = "yt-dlp"
        try:
            result = self._original.run(*args, **kwargs)
        except self._original.TimeoutExpired:
            record.update(outcome="timeout", category="network", returncode=None, raw_entries=0)
            raise
        record.update(returncode=result.returncode, raw_entries=_raw_entries(result.stdout))
        if result.returncode:
            record.update(outcome="failed_exit", category=_error_category(result.stderr))
        return result


def _write_diagnostic(path, record):
    try:
        with _diagnostic_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        # Optional observations must never change a search or stop a film.
        pass


def _install_search_diagnostics(module, home):
    original = getattr(module, "search", None)
    subprocess = getattr(module, "subprocess", None)
    if (not home or not callable(original) or getattr(original, "_cr_search_diagnostics", False)
            or subprocess is None or not callable(getattr(subprocess, "run", None))):
        return
    state = threading.local()
    module.subprocess = _SearchSubprocess(subprocess, state)
    path = Path(home) / ".cr-logs" / "footage-search-diagnostics.jsonl"

    @wraps(original)
    def search(*args, **kwargs):
        prior = getattr(state, "record", None)
        record = {"at": datetime.now(timezone.utc).isoformat(), "transport": "other_search_service"}
        state.record = record
        try:
            rows = original(*args, **kwargs)
            record["accepted_entries"] = len(rows) if isinstance(rows, list) else 0
            if "outcome" not in record:
                raw = record.get("raw_entries")
                record["outcome"] = ("no_raw_entries" if raw == 0 else
                                     "filtered_out" if raw is not None and not record["accepted_entries"] else
                                     "accepted" if record["accepted_entries"] else "empty_other_service")
            return rows
        finally:
            state.record = prior
            _write_diagnostic(path, record)
    search._cr_search_diagnostics = True
    module.search = search


def install(module=None, *, home=None):
    if module is None:
        try:
            import youtube as module
        except ModuleNotFoundError as error:
            if error.name == "youtube":
                return None
            raise
    original = getattr(module, "requests", None)
    if original is None or not callable(getattr(original, "post", None)):
        return None
    facade = original
    if not getattr(original, "_cr_kie_watch_route", False):
        facade = _WatcherRequests(original, module)
        module.requests = facade
    source = getattr(module, "__file__", None)
    _install_search_diagnostics(module, home or (Path(source).resolve().parent if source else None))
    _install_budget(module)
    return facade


# ── how much one video's footage research may watch ───────────────────────────────────────────────────────────────
# A customer's 10-minute video spent 22 extra hours "watching hundreds and hundreds of YouTube clips" (2026-10-25). The
# engine watches every candidate of every stretch up front: two stretches a minute, up to six videos each (three per
# search, two searches), a long video in several pieces (each a watch of its own), then spares for the stretches that
# found little and Internet Archive films; and a watcher that never answered cost five minutes a time. Each video now
# has a budget scaled to its length: how many videos are watched (every stretch's best first, round by round, so none
# is left without while another watches its sixth) and how long the watching may take before the video goes on with
# what it found. Videos watched before (the catalog) cost nothing and never count.
VIDEOS_PER_MIN, VIDEOS_MIN, VIDEOS_MAX = 5, 24, 150    # videos watched for one minute of narration, at least, at most
STOP_PER_MIN, STOP_MIN, STOP_MAX = 4, 20, 120         # minutes of watching for one minute of narration, ...
SPARE_SHARE = 6                                       # a sixth of the videos stays for the second round and the Archive
# Measured on the owner's Mac (2026-10-19, two footage videos): 28 videos watched in 16 minutes and 29 in 14, the whole
# footage step 21 minutes each (searching, choosing and cutting around the watching).
RATE = (1.5, 3.0)                                     # videos watched a minute, slowest to fastest
AROUND_MIN = (5, 10)                                  # minutes of searching, choosing and cutting around it
# Watchers that take a link watch more at once; the ones that need a copy uploaded first (Kie, WaveSpeed) keep a home
# connection's upload in mind (the engine watches three at a time with them, five with the others).
WORKERS_LINK, WORKERS_UPLOAD = 6, 4
LINK_WATCHERS = ("gemini", "openlux", "algrow")
PROGRESS_EVERY_S = 45.0

_budget_lock = threading.RLock()
_budget = {}                                          # each video's footage search running, by its youtube/ folder


def _bkey(work) -> str:
    """The key of a video's footage search: its youtube/ folder (the engine's `work` and `ydir`)."""
    return os.path.normcase(os.path.abspath(str(work)))


def budget(minutes) -> dict:
    """What one video's footage research may watch, and about how long it takes, for a video of `minutes`."""
    m = max(1.0, float(minutes or 0))
    videos = int(min(VIDEOS_MAX, max(VIDEOS_MIN, round(VIDEOS_PER_MIN * m))))
    stop = int(min(STOP_MAX, max(STOP_MIN, round(STOP_PER_MIN * m))))
    lo = AROUND_MIN[0] + math.ceil(videos / RATE[1])
    hi = min(stop + AROUND_MIN[1], AROUND_MIN[1] + math.ceil(videos / RATE[0]))
    return {"minutes": int(round(m)), "videos": videos, "stop_minutes": stop, "typical_minutes": [lo, hi],
            "most_minutes": stop + AROUND_MIN[1]}


def estimate_text(minutes) -> str:
    """The Create page's and the quote's words for it."""
    b = budget(minutes)
    lo, hi = b["typical_minutes"]
    return (f"Research footage adds about {lo} to {hi} minutes to a {b['minutes']}-minute video: Studio watches up to "
            f"{b['videos']} YouTube videos on your video-model account to find its clips. It stops watching after "
            f"{b['stop_minutes']} minutes and goes on with what it found.")


def _minutes_of(engine, script: str, style: str) -> float:
    wpm = 0.0
    try:
        wpm = float((getattr(engine, "STYLE_WPM", {}) or {}).get(style) or 0)
    except (TypeError, ValueError):
        pass
    return len(str(script or "").split()) / max(60.0, wpm or 150.0)


def _cached(module, work, vid) -> bool:
    safe = getattr(module, "_safe", lambda v: re.sub(r"[^A-Za-z0-9_.-]+", "_", str(v))[:110])
    return (Path(work) / "catalog" / f"{safe(vid)}.json").is_file()


def _log(b: dict, msg: str) -> None:
    try:
        getattr(b.get("engine"), "log", print)(msg)
    except Exception:                                  # noqa: BLE001 - a progress line only
        pass


def _progress(b: dict, force: bool = False) -> None:
    now = time.monotonic()
    if not force and now - b["said"] < PROGRESS_EVERY_S and b["done"] % 5:
        return
    b["said"] = now
    took = (now - b["t0"]) / 60
    _log(b, f"  YouTube: watched {b['done']} of {max(b['planned'], b['done'])} videos ({took:.0f} min so far; this "
            f"video stops watching at {b['plan']['stop_minutes']} min)")


def trim(needs: list, room: int, free=()) -> list:
    """Each stretch's videos cut to the budget: every stretch's best video first, then every stretch's second, and so
    on, until `room` videos are chosen (videos already watched, `free`, cost nothing and always stay). The rest
    become the stretch's spares, which the engine's second round may still watch. Returns the videos to watch."""
    lists = [list(x.get("videos") or []) for x in needs]
    chosen, depth = [], 0
    keep = set(free)
    while len(chosen) < room and any(depth < len(row) for row in lists):
        for row in lists:
            if depth < len(row) and row[depth] not in keep:
                keep.add(row[depth])
                chosen.append(row[depth])
                if len(chosen) >= room:
                    break
        depth += 1
    for x, row in zip(needs, lists):
        x["videos"] = [v for v in row if v in keep]
        x["spare"] = [v for v in row if v not in keep] + [v for v in x.get("spare") or [] if v not in row]
    return chosen


def _install_budget(module):
    """Wrap the engine's footage search (fetch_clips), its watch plan (plan_focus) and a video's watch (shots)."""
    fetch, plan_focus, shots = (getattr(module, n, None) for n in ("fetch_clips", "plan_focus", "shots"))
    if not (callable(fetch) and callable(plan_focus) and callable(shots)) or getattr(fetch, "_cr_budget", False):
        return

    @wraps(fetch)
    def fetch_clips(engine, script, job, style, force, *args, **kwargs):
        plan = budget(_minutes_of(engine, script, style))
        try:
            cfg = module.settings(engine, style)
            watcher = module.analyst(cfg.get("watcher"))
        except Exception:                              # noqa: BLE001 - the engine reads them again itself
            cfg, watcher = None, ""
        # what this video watched before (a run that stopped halfway) is in its catalog: free now, and counted
        catalog = Path(job) / "youtube" / "catalog"
        before = len(list(catalog.glob("*.json"))) if catalog.is_dir() else 0
        b = {"engine": engine, "plan": plan, "cfg": cfg, "ydir": Path(job) / "youtube", "t0": time.monotonic(),
             "said": 0.0, "watched": before, "new": 0, "done": 0, "planned": 0, "rounds": 0, "failed": {},
             "refused": 0, "workers": WORKERS_LINK if watcher in LINK_WATCHERS else WORKERS_UPLOAD}
        key = _bkey(b["ydir"])
        with _budget_lock:
            before = _budget.get(key)
            _budget[key] = b
        try:
            return fetch(engine, script, job, style, force, *args, **kwargs)
        finally:
            with _budget_lock:
                if before is None:
                    _budget.pop(key, None)
                else:
                    _budget[key] = before
            if b["new"]:
                _log(b, f"  YouTube: {b['new']} video(s) watched in {(time.monotonic() - b['t0']) / 60:.0f} min "
                        f"({b['watched']} of this video's {plan['videos']})" + (
                            f"; {b['refused']} left unwatched by the budget" if b["refused"] else ""))
    fetch_clips._cr_budget = True

    @wraps(plan_focus)
    def focus(engine, title, needs, videos, work, *args, **kwargs):
        b = _budget.get(_bkey(work))
        if b is None:
            return plan_focus(engine, title, needs, videos, work, *args, **kwargs)
        b["rounds"] += 1
        if b["rounds"] == 1:
            # the first round: every stretch's candidates, cut to the budget (a sixth kept for the later rounds)
            every = list(dict.fromkeys(v for x in needs for v in x.get("videos") or []))
            free = [v for v in every if _cached(module, work, v)]
            room = max(0, b["plan"]["videos"] - b["plan"]["videos"] // SPARE_SHARE - b["watched"])
            if len(every) - len(free) > room:
                trim(needs, room, free)
                want = set(v for x in needs for v in x["videos"])
                videos = [v for v in videos if v.get("id") in want]
                _log(b, f"  YouTube: watching {len(want) - len(free)} of the {len(every) - len(free)} new videos found "
                        f"(this {b['plan']['minutes']}-minute video's budget is {b['plan']['videos']}; it stops "
                        f"watching after {b['plan']['stop_minutes']} min)")
            todo = list(dict.fromkeys(v for x in needs for v in x["videos"]))
        else:
            todo = [v.get("id") for v in videos]
        plan = plan_focus(engine, title, needs, videos, work, *args, **kwargs)
        todo = [v for v in todo if v and plan.get(v) != [] and not _cached(module, work, v)]
        with _budget_lock:
            b["planned"] += min(len(todo), max(0, b["plan"]["videos"] - b["watched"]))
        if todo and b.get("cfg") is not None:
            # watched here, more at once than the engine's own loop; it then finds each one in the catalog
            def one(vid):
                try:
                    module.shots(vid, work, plan.get(vid), cfg=b["cfg"])
                except Exception:                      # noqa: BLE001 - the engine's loop says why, once
                    pass
            with ThreadPoolExecutor(max_workers=b["workers"]) as ex:
                list(ex.map(one, todo))
        return plan

    @wraps(shots)
    def watch(vid, work, *args, **kwargs):
        b = _budget.get(_bkey(work))
        if b is None or _cached(module, work, vid):
            return shots(vid, work, *args, **kwargs)
        with _budget_lock:
            if vid in b["failed"]:
                raise RuntimeError(b["failed"][vid])           # tried once in this search: never watched twice
            took = (time.monotonic() - b["t0"]) / 60
            if b["watched"] >= b["plan"]["videos"] or took >= b["plan"]["stop_minutes"]:
                b["refused"] += 1
                raise RuntimeError(f"not watched: this video's footage budget is used ({b['watched']} videos, "
                                   f"{took:.0f} min)")
            b["watched"] += 1
            b["new"] += 1
        try:
            return shots(vid, work, *args, **kwargs)
        except Exception as e:
            with _budget_lock:
                b["failed"][vid] = str(e)[:300] or type(e).__name__
            raise
        finally:
            with _budget_lock:
                b["done"] += 1
                _progress(b, force=b["done"] >= b["planned"])

    module.fetch_clips, module.plan_focus, module.shots = fetch_clips, focus, watch
