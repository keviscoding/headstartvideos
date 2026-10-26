"""archive.org that isn't answering is skipped for the rest of the video, after two failed calls, with one line.

Customer ALEX (Studio 104, 2026-10-26): archive.org didn't respond at all, and the footage step kept trying search
after search for 20+ minutes. The engine's archiveorg.py (private, read only) asks the Internet Archive each query
up to six ways (archiveorg._variants), then reads each candidate's file list, every call with a 45-second timeout,
and catches every failure so a search "is never worth the video": nothing ever told it the Archive was down.

Here every archive.org call goes through one breaker (archiveorg._get, the module's only network call): two calls in
a row that fail or time out (no answer, a refused or reset connection, a 5xx, a page that isn't the Archive's JSON)
switch archive.org off for the rest of this video's footage search, with one progress line; later calls fail at
once, which the engine already treats as "nothing found". A 4xx (one missing item) is an answer, not an outage, and
any good answer resets the count. Each video's footage search starts with the Archive on again (cr_watch's
fetch_clips wrapper calls reset), and its Archive films are not downloaded while it is off.
"""
from __future__ import annotations

from functools import wraps
import http.client
import threading
import urllib.error

FAILS_TO_SKIP = 2
SKIPPED = "archive.org isn't answering, so this video goes on without it"
_lock = threading.Lock()
_state = {"fails": 0, "skipped": False, "engine": None}


def reset(engine=None) -> None:
    """A new video's footage search: the Archive is asked again."""
    with _lock:
        _state.update(fails=0, skipped=False, engine=engine)


def skipped() -> bool:
    with _lock:
        return bool(_state["skipped"])


def _say(message: str) -> None:
    engine = _state.get("engine")
    try:
        getattr(engine, "log", print)(message)
    except Exception:                                          # noqa: BLE001 - a progress line only
        pass


def _failed(error: BaseException) -> None:
    with _lock:
        _state["fails"] += 1
        trip = _state["fails"] >= FAILS_TO_SKIP and not _state["skipped"]
        if trip:
            _state["skipped"] = True
    if trip:
        kind = "timed out" if "timed out" in str(error).lower() or isinstance(error, TimeoutError) else "failed"
        _say(f"  Internet Archive: archive.org isn't answering ({FAILS_TO_SKIP} calls in a row {kind}); skipping it "
             f"for the rest of this video. Its footage comes from YouTube and the other sources.")


def install(module=None):
    """Wrap the engine's archiveorg._get once (in Studio and each video's worker). Returns the module or None."""
    if module is None:
        try:
            import archiveorg as module
        except ModuleNotFoundError as error:
            if error.name == "archiveorg":
                return None
            raise
    original = getattr(module, "_get", None)
    if not callable(original) or getattr(original, "_cr_breaker", False):
        return module

    @wraps(original)
    def _get(url, timeout=45.0, *args, **kwargs):
        with _lock:
            if _state["skipped"]:
                raise RuntimeError(SKIPPED)
        try:
            answer = original(url, timeout, *args, **kwargs)
        except urllib.error.HTTPError as error:
            if error.code >= 500:
                _failed(error)
            raise
        except (OSError, ValueError, http.client.HTTPException) as error:   # no answer, refused, reset, not JSON
            _failed(error)
            raise
        with _lock:
            _state["fails"] = 0
        return answer
    _get._cr_breaker = True
    module._get = _get
    return module
