"""Keep the original Kie footage watcher on its documented chat route.

This is a transport compatibility adapter for media shot logging. It does not
change the creative brain, source searches, watch windows, uploaded files,
shot prompts, or the original footage selection and review process.

Kie's Gemini 3.8 Flash chat documentation supports uploaded video URLs in the
same ``image_url`` envelope used by the original watcher:
https://docs.kie.ai/market/gemini/gemini-3-8-flash-openai
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path
import re
import threading


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

    def __init__(self, original):
        self._original = original

    def __getattr__(self, name):
        return getattr(self._original, name)

    def post(self, url, *args, **kwargs):
        if url == LEGACY_ENDPOINT and _original_watch_payload(kwargs.get("json")):
            url = WATCH_ENDPOINT
            kwargs["timeout"] = _watch_timeout(kwargs.get("timeout"))
        # Forward the exact object and all original upload, auth, and response
        # behavior; the original watcher owns parsing and recovery.
        return self._original.post(url, *args, **kwargs)


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
        facade = _WatcherRequests(original)
        module.requests = facade
    source = getattr(module, "__file__", None)
    _install_search_diagnostics(module, home or (Path(source).resolve().parent if source else None))
    return facade
