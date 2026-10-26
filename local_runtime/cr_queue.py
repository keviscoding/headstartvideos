"""The video queue: submit several videos, of any recipes, and walk away. Studio makes them one at a time.

Frontier's queue runs one video after another and a failed video never stops the next. Customers could not use it: a
second Create was refused while anything ran ("Finish your current video before starting another"), so they sat and
waited to press Create again (48 videos in 14 days were started by hand within 15 minutes of the one before finishing).
Now every Create, Resume and Claude Code make_videos joins the queue, and this keeps a long queue from breaking:

- A stop that every video waiting would hit the same way lets each start and fail in turn. The first such stop now
  puts the videos still waiting on hold with its reason in plain words. Shared stops, from the stopped video's failure
  record (cr_failure): Claude's usage limit, Claude Code signed out or its model unavailable, an account's key refused
  (Kie.ai, ElevenLabs), a balance run out, the disk full, the computer out of memory, Studio's setup. A stop of that
  video's own (a title, one picture refused, a renderer bug, a timeout) never holds the rest. A Ranking Short's own
  shared stops (the month's clip watches used up, clip watching down, YouTube asking this computer to slow down, a
  missing voice engine) hold the Ranking Shorts waiting; its Claude stops hold every video. Nothing of a held video
  was made or charged; Carry on checks each one's setup again and queues it, Remove drops them. A video submitted while
  the queue is on hold for a shared stop joins the hold, so it never runs into the same wall either.
- The queue lived only in memory: a Studio closed mid-queue forgot every video that had not started. What is waiting
  is saved (output/.cr-queue.json) whenever it changes, and a Studio that opens to a saved list puts those videos on
  hold rather than starting them unasked.

Only plain video specs are kept (never an editor's timeline render), and only ones that survive a JSON round trip.
Each keeps its own setup proof (cr_setup_proof), checked when it was submitted, so a later submission never changes it.
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
# ...and of those, the ones every video would stop on, not only the Ranking Shorts: Claude itself
RANKING_HOLDS_ALL = ("claude_usage_limit", "claude_auth", "claude_missing", "claude_model")
CLOSED = "Studio closed before these started."
# a Claude stage's own stop code (cr_claude) that every video needing Claude would stop on too
_CLAUDE_STAGE = {"usage_limit": "usage_limit", "auth_missing": "auth", "authentication_required": "auth",
                 "model_unavailable": "auth"}
# the server's setup refusals at a project's start (webapp frontier_studio._setup_issues): every video stops on them
_SETUP = re.compile(r"(?i)download the release required|install claude code and sign in|verify both selected claude "
                    r"models|review and acknowledge your claude|finish the local video-tools|install the javascript "
                    r"runtime|choose supported writing")
_ACCOUNT = {"kie": "Kie.ai", "elevenlabs": "ElevenLabs", "claude": "Claude Code", "channelrecipe": "ChannelRecipe",
            "other": "A connected account"}


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


def shared_kind(record, *, stage: str = "", message: str = "") -> str:
    """The kind of stop every video waiting would hit too, from a stopped video's failure record (cr_failure) and its
    Claude stage's stop code: usage_limit, auth, balance, disk_full, out_of_memory or setup. "" for a stop of the
    video's own: a title, one picture or narration refused, a renderer bug, a timeout, a rate limit, a network blip
    (each of those can work for the next video)."""
    record = record if isinstance(record, dict) else {}
    category, provider = str(record.get("category") or ""), str(record.get("provider") or "none")
    if stage in _CLAUDE_STAGE:
        return _CLAUDE_STAGE[stage]
    if category == "usage_limit" and provider in ("claude", "none"):
        return "usage_limit"                                   # (the month's clip watches are the Ranking Shorts')
    if category == "auth" and provider != "youtube":           # (YouTube's "sign in to confirm": the Shorts' own)
        return "auth"
    if category == "missing_tool" and provider == "claude":
        return "auth"                                          # Claude Code not installed
    if category in ("balance", "disk_full", "out_of_memory"):
        return category
    if _SETUP.search(str(message or "")):
        return "setup"
    return ""


def plain_reason(kind: str, provider: str = "none") -> str:
    """What happened and what to do, in the creator's words, for the hold notice."""
    account = _ACCOUNT.get(provider, "A connected account")
    if kind == "usage_limit":
        return "Claude's usage limit was reached, and every video waiting needs Claude. Carry on once it resets."
    if kind == "auth" and provider in ("claude", "none"):
        return ("Claude Code needs signing in again, or its model isn't available. Check Setup & accounts, then "
                "Carry on.")
    if kind == "auth":
        return f"{account} stopped accepting Studio's key. Check it in Setup & accounts, then Carry on."
    if kind == "balance":
        return {"kie": "Your Kie.ai balance ran out. Top it up, then Carry on.",
                "elevenlabs": "Your ElevenLabs credits ran out. Top them up, then Carry on."}.get(
            provider, f"{account}'s balance ran out. Top it up, then Carry on.")
    if kind == "disk_full":
        return ("The disk Studio's videos go on is full. Free up space (empty the Bin, delete old downloads), then "
                "Carry on.")
    if kind == "out_of_memory":
        return "This computer ran out of memory. Close other apps, then Carry on."
    return "Studio's setup needs attention. Open Setup & accounts, then Carry on."


def _said(text, limit=220) -> str:
    """The stopped video's own message, short: its first sentences up to `limit` characters."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 60 else cut.rstrip() + "…"


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
        # who the hold covers: "all" (a shared stop: every video submitted now joins it), "ranking" (a Ranking Short's
        # own: the Ranking Shorts), "" (a closed Studio, or a video whose own check failed: new videos run)
        self.scope = ""
        self.why: dict = {}                                    # slug -> why that video is on hold
        self.running = None                                    # the spec being made now
        # Carry on's check (cr_studio): specs -> {slug: why it still can't start}, after giving each that can a fresh
        # setup proof. None: Carry on queues them as they are.
        self.check = None
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
                reason, code, scope, why = self.reason, self.code, self.scope, dict(self.why)
            kept = [k for k in map(_kept, held) if k]
            data = {"saved_at": time.time(), "waiting": [k for k in map(_kept, waiting) if k], "held": kept,
                    "reason": reason, "code": code, "scope": scope,
                    "why": {s["slug"]: why[s["slug"]] for s in kept if why.get(s["slug"])}}
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
        why = data.get("why") if isinstance(data.get("why"), dict) else {}
        with self.lock, self._studio_lock():
            known = {s["slug"] for s in self._queue} | {s["slug"] for s in self.held}
            fresh = [s for s in held + waiting if s["slug"] not in known]
            for spec in fresh:
                self._mark_held(spec)
            self.held += fresh
            if fresh:
                self.reason = CLOSED if waiting else (str(data.get("reason") or "") or CLOSED)
                self.code = "" if waiting else str(data.get("code") or "")
                self.scope = "" if waiting or data.get("scope") not in ("all", "ranking") else data["scope"]
                for spec in fresh:
                    self.why[spec["slug"]] = CLOSED if waiting else str(why.get(spec["slug"]) or self.reason)
        self.save()

    # ── adding videos ──────────────────────────────────────────────────────────────────────────────────────────────
    def _covers(self, spec) -> bool:
        """Whether a video submitted now joins the hold: the queue is on hold for a stop it would hit too."""
        if not self.held or spec.get("timeline"):
            return False
        return self.scope == "all" or (self.scope == "ranking" and is_ranking(spec))

    def _places(self) -> dict:
        """slug -> {position (1 = the next to start), ahead (videos before it, the one being made included)}."""
        running = 1 if (getattr(self.studio, "RUNNING", None) or {}).get("slug") else 0
        return {s["slug"]: {"position": i + 1, "ahead": i + running} for i, s in enumerate(self._queue)
                if isinstance(s, dict) and s.get("slug")}

    def add(self, specs, jobs=None) -> dict:
        """Publish newly submitted videos (cr_submission): into the waiting line, or on hold with the others when the
        queue is on hold for a stop they would hit too. -> slug: {position, ahead} or {held: why}."""
        specs = [s for s in specs or [] if isinstance(s, dict)]
        with self.lock, self._studio_lock():
            self._jobs.update(jobs or {})
            hold = [s for s in specs if self._covers(s)]
            go = [s for s in specs if not any(s is h for h in hold)]
            self._queue.extend(go)
            for spec in hold:
                self._mark_held(spec)
                self.why[spec["slug"]] = self.reason
            self.held += hold
            places = self._places()
        self.save()
        return {s["slug"]: {"held": self.why.get(s["slug"]) or self.reason} if any(s is h for h in hold)
                else places.get(s["slug"], {"position": 0, "ahead": 0}) for s in specs}

    def has_project(self, project_id) -> bool:
        """A saved project already being made, waiting or on hold (a second Resume would make it twice)."""
        if not project_id:
            return False
        with self.lock, self._studio_lock():
            specs = list(self._queue) + list(self.held) + ([self.running] if self.running else [])
        return any(isinstance(s, dict) and s.get("cr_project_id") == project_id for s in specs)

    def ahead_gb(self) -> float:
        """What the videos before a new one (the one being made, the waiting, the held) still need on the disk, in GB:
        each one's measured need less what its working folder already holds (cr_machine)."""
        import cr_machine
        with self.lock, self._studio_lock():
            specs = [s for s in ([self.running] if self.running else []) + list(self._queue) + list(self.held)
                     if isinstance(s, dict) and not s.get("timeline") and s.get("slug")]
        if not specs:
            return 0.0
        find = getattr(getattr(self.studio, "mv", None), "find_job", None)
        total = 0.0
        for spec in specs:
            threed = self._threed(spec) or spec.get("render_backend") == "manimgx"
            need = cr_machine.need_gb(spec.get("minutes") or 1, 1, threed) - cr_machine.NEED_BASE_GB
            try:
                job = find(spec["slug"]) if callable(find) else None
            except Exception:                                  # noqa: BLE001 - not found: nothing saved to count
                job = None
            total += max(0.0, need - (cr_machine.folder_gb(job) if job else 0.0))
        return round(total, 1)

    # ── around each video ──────────────────────────────────────────────────────────────────────────────────────────
    def started(self, spec):
        try:
            with self.lock:
                self.running = spec
            self.save()                                        # the dispatcher has taken it off the waiting list
        except Exception:                                      # noqa: BLE001 - never in the way of a video
            pass

    def finished(self, spec, job_of):
        """After a video: one that stopped on a shared cause holds the videos behind it (a Ranking Short's own shared
        stops hold the Ranking Shorts)."""
        try:
            with self.lock:
                if self.running is spec:
                    self.running = None
            state = self._jobs.get(spec.get("slug")) or {}
            if state.get("state") in ("error", "failed") and not spec.get("timeline"):
                job = job_of()
                reason, code, scope = "", "", ""
                if is_ranking(spec):
                    reason, code = shared_stop(job)
                    scope = "all" if code in RANKING_HOLDS_ALL else "ranking" if reason else ""
                if not reason:
                    reason, code = self.shared_cause(spec, state, job)
                    scope = "all" if reason else ""
                if reason:
                    self.hold(reason, code, scope)
            self.save()
        except Exception:                                      # noqa: BLE001 - never in the way of a video
            pass

    def shared_cause(self, spec, state, job) -> tuple:
        """(reason, code) when this video stopped on something every video waiting would stop on too; ("", "")
        for a stop of its own."""
        import cr_failure
        try:
            project_id = str(spec.get("cr_project_id") or "")
            if not project_id and job is not None:
                project_id = str(json.loads((Path(job) / "cr-project.json").read_text(encoding="utf-8")).get("id")
                                 or "")
        except (OSError, ValueError, AttributeError):
            project_id = ""
        message = str(state.get("error") or "")
        record = cr_failure.for_job(self.home, state, spec, job, project_id)
        try:
            stage = cr_failure._claude_stop(self.home, project_id, float(state.get("started") or 0)
                                            or time.time() - 6 * 3600)
        except Exception:                                      # noqa: BLE001 - only a finer reason
            stage = ""
        kind = shared_kind(record, stage=stage, message=message)
        if not kind:
            return "", ""
        provider = "claude" if stage in _CLAUDE_STAGE else str(record.get("provider") or "none")
        title = str(spec.get("title") or state.get("title") or "").strip()
        said = _said(message)
        reason = plain_reason(kind, provider)
        if said:
            reason += f" “{title[:80]}” stopped on it: {said}" if title else f" The video before them said: {said}"
        return reason, kind

    def _job(self, spec, state):
        return {"lines": [], "state": state, "error": None, "done": state != "queued", "title": spec["title"],
                "steps": spec.get("steps") or [], "minutes": spec.get("minutes") or 1, "style": spec.get("style") or "",
                "threed": self._threed(spec)}

    def _mark_held(self, spec):
        self._jobs[spec["slug"]] = self._job(spec, "held")

    def hold(self, reason, code="", scope="all"):
        """Put the videos waiting on hold: every plain video (scope "all") or only the Ranking Shorts ("ranking")."""
        with self.lock, self._studio_lock():
            take = [s for s in self._queue if not s.get("timeline") and (scope == "all" or is_ranking(s))]
            if take:
                self._queue[:] = [s for s in self._queue if not any(s is t for t in take)]
                for spec in take:
                    self._mark_held(spec)
                    self.why[spec["slug"]] = reason
                self.held += take
            if take or self.held:
                # the newest stop says why (for every held video it would stop too); a shared stop covers every
                # video submitted from now on
                self.reason, self.code = reason, code
                self.scope = "all" if scope == "all" or self.scope == "all" else scope
                for spec in self.held:
                    if scope == "all" or is_ranking(spec):
                        self.why[spec["slug"]] = reason
        self.save()
        return len(take)

    def hold_ranking(self, reason, code=""):
        return self.hold(reason, code, "ranking")

    # ── what the creator does about it ──────────────────────────────────────────────────────────────────────────────
    def resume(self, use_credits=False):
        """Carry on: each held video's setup is checked again (self.check), and the ones ready go back in the waiting
        line in their order; one that still can't start stays on hold with its own reason."""
        with self.lock, self._studio_lock():
            specs, self.held = self.held, []
            why, self.why = self.why, {}
            self.reason, self.code, self.scope = "", "", ""
        if use_credits:
            specs = [with_credits(s) if is_ranking(s) else s for s in specs]
        specs = [s for s in specs if not self._cancelled(s)]
        problems = {}
        if specs and callable(self.check):
            try:
                problems = dict(self.check(specs) or {})
            except Exception:                                  # noqa: BLE001 - not checked: nothing starts
                problems = {s["slug"]: "Studio couldn't check its setup just now. Try Carry on again in a minute."
                            for s in specs}
        go = [s for s in specs if s["slug"] not in problems]
        stay = [s for s in specs if s["slug"] in problems]
        with self.lock, self._studio_lock():
            for spec in go:
                self._jobs[spec["slug"]] = self._job(spec, "queued")
            self._queue.extend(go)
            for spec in stay:
                self._mark_held(spec)
                self.why[spec["slug"]] = str(problems[spec["slug"]])
            if stay:
                self.held = stay + self.held                   # still first in line
                self.reason, self.code = str(problems[stay[0]["slug"]]), "setup"
        self.save()
        if go and callable(getattr(self.studio, "_ensure_dispatcher", None)):
            self.studio._ensure_dispatcher()
        return len(go)

    def drop(self):
        with self.lock, self._studio_lock():
            specs, self.held, self.reason, self.code, self.scope = self.held, [], "", "", ""
            self.why = {}
            for spec in specs:
                if (self._jobs.get(spec["slug"]) or {}).get("state") == "held":
                    self._jobs.pop(spec["slug"], None)
        self.save()
        return len(specs)

    def status(self):
        with self.lock, self._studio_lock():
            return {"held": [{"slug": s["slug"], "title": s["title"], "ranking": is_ranking(s),
                              "reason": self.why.get(s["slug"]) or self.reason} for s in self.held],
                    "reason": self.reason, "code": self.code, "waiting": len(self._queue),
                    "max_ranking_batch": MAX_RANKING_BATCH}

    def queue_rows(self, body):
        """The engine's /api/queue answer with each waiting video's place in line and the held ones (state held,
        with why), after the waiting ones."""
        if not isinstance(body, dict) or not isinstance(body.get("jobs"), list):
            return body
        with self.lock, self._studio_lock():
            places = self._places()
            held = [{"slug": s["slug"], "title": s["title"], "state": "held",
                     "reason": self.why.get(s["slug"]) or self.reason} for s in self.held]
        live, done = [], []
        held_slugs = {h["slug"] for h in held}
        for job in body["jobs"]:
            if not isinstance(job, dict):
                continue
            if job.get("state") in ("running", "queued"):
                if job.get("state") == "queued" and job.get("slug") in places:
                    job.update(places[job["slug"]])
                live.append(job)
            elif job.get("slug") not in held_slugs:            # an earlier run of a project now on hold: the hold says
                done.append(job)
        held = [h for h in held if h["slug"] not in {j.get("slug") for j in live}]
        return {**body, "jobs": live + held + done, "held": len(held)}

    def install_routes(self):
        from flask import jsonify
        app = self.studio.app

        def no_store(response):
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.get("/cr-queue")
        def cr_queue_status():
            from flask import request
            extra = {"ahead_gb": self.ahead_gb()} if request.args.get("ahead") == "1" else {}
            return no_store(jsonify(**self.status(), **extra))

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
            elif request.path == "/api/queue" and request.method == "GET" and response.status_code == 200 \
                    and response.mimetype == "application/json":
                try:
                    response.set_data(json.dumps(self.queue_rows(response.get_json())))
                except Exception:                              # noqa: BLE001 - the engine's own answer stands
                    pass
            return response


def install(studio, home):
    keeper = getattr(studio, "_cr_queue", None)
    if keeper is None:
        keeper = studio._cr_queue = Keeper(studio, home)
        keeper.install_routes()
        keeper.restore()
    return keeper
