"""Long batches that never break: up to 15 Ranking Shorts queued at once, made one at a time.

Frontier's queue runs one video after another and a failed video never stops the next. Two things made a long batch
of Ranking Shorts fragile, and this keeps both from happening:

- A stop that every Short in the batch would hit the same way (the month's clip watches used up, ChannelRecipe's
  clip watching down, Claude's usage limit, YouTube asking this computer to slow down, a missing voice engine) let
  each waiting Short start, search and fail in turn. The first such stop now puts the Ranking Shorts still waiting on
  hold with its reason. Nothing of theirs was searched, watched or charged; Carry on queues them again, Remove drops
  them.
- The queue lived only in memory: a Studio closed mid-batch forgot every title that had not started. What is waiting
  is saved (output/.cr-queue.json) whenever it changes, and a Studio that opens to a saved list puts those titles on
  hold rather than starting them unasked.

Only plain video specs are kept (never an editor's timeline render), and only ones that survive a JSON round trip.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from pathlib import Path

MAX_RANKING_BATCH = 15
# work/error.json codes (shorts/ranking/make_ranking.py) that every waiting Short would stop on too
SHARED = ("out_of_watches", "watch_service", "claude_", "youtube_", "voice_missing", "broken")
CLOSED = "Studio closed before these started."


def is_ranking(spec) -> bool:
    return "[[ranking=" in str((spec or {}).get("extra") or "")


def _keepable(spec) -> bool:
    if not isinstance(spec, dict) or spec.get("timeline") or not spec.get("slug") or not spec.get("title"):
        return False
    try:
        return json.loads(json.dumps(spec)) == spec
    except (TypeError, ValueError):
        return False


def shared_stop(job) -> tuple:
    """(reason, code) when a Short stopped on something every Short waiting behind it would stop on too; else
    ("", "")."""
    try:
        got = json.loads((Path(job) / "short" / "error.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "", ""
    code = str((got or {}).get("code") or "") if isinstance(got, dict) else ""
    if not any(code == s or (s.endswith("_") and code.startswith(s)) for s in SHARED):
        return "", ""
    return str(got.get("message") or "").strip() or "The Short before them stopped.", code


RANKING_MARK = re.compile(r"\[\[ranking=(\{.*?\})\]\]", re.S)


def with_credits(spec) -> dict:
    """The spec with its Ranking options set to use credits once the month's clip watches run out (Carry on using
    credits): the creator's own choice, made on the hold notice, never turned on for them."""
    extra = str(spec.get("extra") or "")
    found = RANKING_MARK.search(extra)
    if not found:
        return spec
    try:
        options = json.loads(found.group(1))
    except ValueError:
        return spec
    if not isinstance(options, dict):
        return spec
    options["use_credits"] = True
    mark = "[[ranking=" + json.dumps(options) + "]]"
    return {**spec, "extra": extra[:found.start()] + mark + extra[found.end():]}


class Keeper:
    def __init__(self, studio, home):
        self.studio = studio
        self.path = Path(home) / "output" / ".cr-queue.json"
        self.held: list = []
        self.reason = ""
        self.code = ""
        self.lock = threading.RLock()

    @property
    def _queue(self) -> list:
        return getattr(self.studio, "QUEUE", None) if isinstance(getattr(self.studio, "QUEUE", None), list) else []

    @property
    def _jobs(self) -> dict:
        return getattr(self.studio, "JOBS", None) if isinstance(getattr(self.studio, "JOBS", None), dict) else {}

    def _studio_lock(self):
        return getattr(self.studio, "LOCK", None) or contextlib.nullcontext()

    # ── saved list ──────────────────────────────────────────────────────────────────────────────────────────────────
    def save(self):
        with self.lock, self._studio_lock():
            waiting = [s for s in self._queue if _keepable(s)]
            held = [s for s in self.held if _keepable(s)]
            reason, code = self.reason, self.code
        data = {"saved_at": time.time(), "waiting": waiting, "held": held, "reason": reason, "code": code}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass                                               # a full or read-only disk must never stop a video

    def restore(self):
        """At start: what the last Studio had waiting or on hold goes on hold here (never started unasked)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        waiting = [s for s in data.get("waiting") or [] if _keepable(s)]
        held = [s for s in data.get("held") or [] if _keepable(s)]
        with self.lock, self._studio_lock():
            known = {s["slug"] for s in self._queue} | {s["slug"] for s in self.held}
            fresh = [s for s in held + waiting if s["slug"] not in known]
            for spec in fresh:
                self._mark_held(spec)
            self.held += fresh
            if fresh:
                self.reason = CLOSED if waiting else (str(data.get("reason") or "") or CLOSED)
                self.code = "" if waiting else str(data.get("code") or "")
        self.save()

    # ── around each video ──────────────────────────────────────────────────────────────────────────────────────────
    def started(self, spec):
        try:
            self.save()                                        # the dispatcher has taken it off the waiting list
        except Exception:                                      # noqa: BLE001 - never in the way of a video
            pass

    def finished(self, spec, job_of):
        """After a video: a Ranking Short that stopped on a shared cause holds the Ranking Shorts behind it."""
        try:
            state = (self._jobs.get(spec.get("slug")) or {}).get("state")
            if is_ranking(spec) and state in ("error", "failed"):
                reason, code = shared_stop(job_of())
                if reason:
                    self.hold_ranking(reason, code)
            self.save()
        except Exception:                                      # noqa: BLE001 - never in the way of a video
            pass

    def _mark_held(self, spec):
        self._jobs[spec["slug"]] = {"lines": [], "state": "held", "error": None, "done": True,
                                          "title": spec["title"], "steps": spec.get("steps") or [],
                                          "minutes": spec.get("minutes") or 1, "style": spec.get("style") or ""}

    def hold_ranking(self, reason, code=""):
        with self.lock, self._studio_lock():
            take = [s for s in self._queue if is_ranking(s) and not s.get("timeline")]
            if not take:
                return 0
            self._queue[:] = [s for s in self._queue if not any(s is t for t in take)]
            for spec in take:
                self._mark_held(spec)
            self.held += take
            self.reason, self.code = reason, code
        self.save()
        return len(take)

    # ── what the creator does about it ──────────────────────────────────────────────────────────────────────────────
    def resume(self, use_credits=False):
        with self.lock, self._studio_lock():
            specs, self.held, self.reason, self.code = self.held, [], "", ""
            if use_credits:
                specs = [with_credits(s) if is_ranking(s) else s for s in specs]
            for spec in specs:
                self._jobs[spec["slug"]] = {"lines": [], "state": "queued", "error": None, "done": False,
                                                  "title": spec["title"], "steps": spec.get("steps") or [],
                                                  "minutes": spec.get("minutes") or 1, "style": spec.get("style") or ""}
            self._queue.extend(specs)
        self.save()
        if specs and callable(getattr(self.studio, "_ensure_dispatcher", None)):
            self.studio._ensure_dispatcher()
        return len(specs)

    def drop(self):
        with self.lock, self._studio_lock():
            specs, self.held, self.reason, self.code = self.held, [], "", ""
            for spec in specs:
                if (self._jobs.get(spec["slug"]) or {}).get("state") == "held":
                    self._jobs.pop(spec["slug"], None)
        self.save()
        return len(specs)

    def status(self):
        with self.lock, self._studio_lock():
            return {"held": [{"slug": s["slug"], "title": s["title"], "ranking": is_ranking(s)} for s in self.held],
                    "reason": self.reason, "code": self.code, "waiting": len(self._queue),
                    "max_ranking_batch": MAX_RANKING_BATCH}

    def install_routes(self):
        from flask import jsonify
        app = self.studio.app

        def no_store(response):
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.get("/cr-queue")
        def cr_queue_status():
            return no_store(jsonify(self.status()))

        @app.post("/cr-queue/resume")
        def cr_queue_resume():
            from flask import request
            credits = (request.get_json(silent=True) or {}).get("use_credits") is True
            return no_store(jsonify(resumed=self.resume(use_credits=credits), **self.status()))

        @app.post("/cr-queue/drop")
        def cr_queue_drop():
            return no_store(jsonify(removed=self.drop(), **self.status()))


def install(studio, home):
    keeper = getattr(studio, "_cr_queue", None)
    if keeper is None:
        keeper = studio._cr_queue = Keeper(studio, home)
        keeper.install_routes()
        keeper.restore()
    return keeper
