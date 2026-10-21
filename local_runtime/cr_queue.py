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
# work/error.json codes (shorts/ranking/make_ranking.py) that every waiting Short would stop on too. Claude's limits
# only (claude_format, claude_timeout and claude_other belong to one topic's answer: the next topic may well work).
SHARED = ("out_of_watches", "watch_service", "claude_usage_limit", "claude_auth", "claude_missing", "claude_model",
          "claude_network", "youtube_", "voice_missing", "broken")
CLOSED = "Studio closed before these started."


def is_ranking(spec) -> bool:
    return "[[ranking=" in str((spec or {}).get("extra") or "")


def _kept(spec):
    """The spec as the saved list keeps it (plain JSON: Frontier's `mix` tuple becomes a list), or None for one that
    is not a plain video (an editor's timeline render) or will not serialise."""
    if not isinstance(spec, dict) or spec.get("timeline") or not spec.get("slug") or not spec.get("title"):
        return None
    try:
        return json.loads(json.dumps(spec))
    except (TypeError, ValueError):
        return None


def _revived(spec) -> dict:
    if isinstance(spec.get("mix"), list):
        spec["mix"] = tuple(spec["mix"])                       # as api_run made it
    return spec


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
        self.home = Path(home)

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
        """One save at a time, in order (self.lock is held through the write; the Studio's LOCK only while copying
        the lists), so an older snapshot never lands after a newer one."""
        with self.lock:
            with self._studio_lock():
                waiting = list(self._queue)
                held = list(self.held)
                reason, code = self.reason, self.code
            data = {"saved_at": time.time(), "waiting": [k for k in map(_kept, waiting) if k],
                    "held": [k for k in map(_kept, held) if k], "reason": reason, "code": code}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(f".cr-queue.{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                pass                                           # a full or read-only disk must never stop a video

    def _cancelled(self, spec) -> bool:
        """A queued video the creator cancelled (cr_video_cancel writes cancelled.json in its worker folder)."""
        run_id = spec.get("cr_video_run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            return False
        return (self.home / "output" / "cr-video-workers" / run_id / "cancelled.json").exists()

    def _threed(self, spec) -> bool:
        old = self._jobs.get(spec.get("slug")) or {}
        if "threed" in old:
            return bool(old["threed"])
        on = getattr(self.studio, "_threed_on", None)
        try:
            return bool(on(spec.get("style") or "", spec.get("features"))) if callable(on) else False
        except Exception:                                      # noqa: BLE001 - a progress bar's detail
            return False

    def restore(self):
        """At start: what the last Studio had waiting or on hold goes on hold here (never started unasked)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        waiting = [_revived(s) for s in data.get("waiting") or [] if _kept(s) and not self._cancelled(s)]
        held = [_revived(s) for s in data.get("held") or [] if _kept(s) and not self._cancelled(s)]
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

    def _job(self, spec, state):
        return {"lines": [], "state": state, "error": None, "done": state != "queued", "title": spec["title"],
                "steps": spec.get("steps") or [], "minutes": spec.get("minutes") or 1, "style": spec.get("style") or "",
                "threed": self._threed(spec)}

    def _mark_held(self, spec):
        self._jobs[spec["slug"]] = self._job(spec, "held")

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
            specs = [s for s in specs if not self._cancelled(s)]
            for spec in specs:
                self._jobs[spec["slug"]] = self._job(spec, "queued")
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

        @app.after_request
        def cr_queue_cleared(response):
            from flask import request
            if request.path == "/api/queue/clear" and request.method == "POST" and response.status_code < 400:
                self.save()                                    # the engine's Clear queue: nothing waits any more
            return response


def install(studio, home):
    keeper = getattr(studio, "_cr_queue", None)
    if keeper is None:
        keeper = studio._cr_queue = Keeper(studio, home)
        keeper.install_routes()
        keeper.restore()
    return keeper
