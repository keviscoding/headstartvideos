"""Stopped videos carry on by themselves when that is clearly free, or with ONE honest approval, and never pay twice.

Customers' videos stopped during the night and waited for a person ("my claude have to help ur platform not to stop
process", ALEX; "always some kind of issue that stopped during the night that I need to requeue in the morning",
DeeDub, 2026-10-26). A failed Claude step was a dead end outside a hidden box on the setup page, making the same title
again paid for everything again, and nothing carried a stopped video on after Studio restarted. This module is the one
place that decides, for a stopped video:

- diagnose(): what stopped it and what carrying it on involves: each stuck Claude step (its kind, error code, a plain
  reason, whether Claude usage may already have gone on it, the reset Claude reported) and each stuck paid media task
  (sent with no answer, still being made, failed), plus `key`, a digest of exactly that set.
- approve(): the creator's one yes (recover_video in Claude Code, the stopped card on the Studio page): ChannelRecipe
  allows one more attempt for exactly those Claude steps, the media receipts that need a yes are kept under their own
  names (the next run may then send that task again, which the creator was told can pay twice), and the video resumes,
  reusing every completed step and receipt. Nothing else is ever run again.
- safe_to_resume(job): whether Studio may carry a video on by itself: its last run stopped on this computer (Studio
  closed or crashed, a render, ffmpeg or the graphics browser, a killed worker) with no Claude step cut off or failed,
  no uncertain media receipt and fewer than AUTO_LIMIT automatic resumes. A usage-limit stop only with the creator's
  up-front choice (CARRY_ON), once Claude's reported reset has passed.
- twins(): a stopped video of the same title, style and minutes as a new Create: Studio offers Resume first.
- Watcher: when Studio is idle, carries on a video safe_to_resume allows.

A yes while another video is made joins the queue (cr_queue) like any Resume and carries on in its turn; a video
waiting or held in the queue is never treated as stopped. The queue can call safe_to_resume(job) for its own
decisions.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
PROJECT = re.compile(r"crv_[a-f0-9]{24}\Z")
STAGE = re.compile(r"cc_[a-f0-9]{64}\Z")
_SLUG = re.compile(r"[\w.\-]{1,200}\Z")

AUTO_LIMIT = 2                         # automatic resumes of one video, ever
AUTO_WINDOW = 24 * 3600                # a stop older than this waits for the creator
USAGE_MARGIN = 120                     # seconds after Claude's reported reset before carrying on by itself
RETRY_BACKOFF = 600                    # an automatic resume Studio refused is tried again after this
RUN_FILE = "cr-run.json"               # {"started", "studio_pid", "run_id", "ended"?, "state"?, "category"?, ...}
AUTO_FILE = "cr-auto-resume.json"      # {"count", "runs": [{"at", "why"}], "last_try"?, "refused"?}
CARRY_ON = "cr-carry-on.json"          # {"after_usage_reset": true, "at"}: the creator's up-front choice
CANCELLED = "cr-cancelled.json"        # {"at", "why"}: the creator cancelled it (running or queued) or removed it from a
                                       # hold; Studio never carries it on by itself until the creator starts it again
# a stop on this computer that a second run can get past (cr_failure categories)
LOCAL_CATEGORIES = ("crash", "out_of_memory", "tool_error", "studio_closed")

KINDS = {"text": "a Claude writing step", "vision": "a Claude step that looks at pictures",
         "research": "a Claude research step"}
# error code -> (what happened, may Claude usage have gone on it: yes | maybe | unclear | no, what that means)
STAGE_WORDS = {
    "usage_limit": ("Claude stopped at your plan's usage limit.", "unclear",
                    "Whether Claude counts a refusal at the limit against your usage isn't confirmed; if the limit "
                    "came part-way through, some usage went on it."),
    "timeout": ("Claude took too long on it and was stopped.", "yes",
                "Claude worked on it until the time limit, so some of your Claude usage went on it."),
    "network_interrupted": ("Claude's connection dropped mid-answer (or the computer went to sleep).", "yes",
                            "Claude had started, so some of your Claude usage went on it."),
    "cli_failed": ("Claude Code ended without an answer.", "maybe",
                   "Claude may have used some of your Claude usage before it ended."),
    "auth_missing": ("Claude Code was signed out.", "no", "Claude didn't start, so none of your usage went on it."),
    "authentication_required": ("Claude Code was signed out.", "no",
                                "Claude didn't start, so none of your usage went on it."),
    "model_unavailable": ("Your Claude account couldn't use the chosen model.", "no",
                          "Claude didn't start, so none of your usage went on it."),
    "cancelled": ("The video was cancelled while Claude worked on this step.", "yes",
                  "Claude had started, so some of your Claude usage went on it."),
    "": ("This step was cut off mid-way: Studio closed, the computer slept, or the video worker stopped.", "maybe",
         "Claude may have used some of your Claude usage on it before it was cut off."),
}
SPENT = {"yes": True, "maybe": True, "unclear": True, "no": False}


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────────

def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, data) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _mtime(path) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _when(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def title_key(title) -> str:
    return " ".join(str(title or "").casefold().split())


def saved_project(job) -> dict:
    """The video's saved Claude Code project (cr-project.json), or {} for anything else."""
    saved = _read(Path(job) / "cr-project.json")
    project_id = saved.get("id")
    if (not isinstance(project_id, str) or not PROJECT.fullmatch(project_id)
            or saved.get("brain_mode") != "claudecode" or saved.get("purpose", "video") != "video"):
        return {}
    return saved


def job_title(job, saved=None) -> str:
    saved = saved if saved is not None else saved_project(job)
    spec = saved.get("job_spec") if isinstance(saved.get("job_spec"), dict) else {}
    try:
        lines = (Path(job) / "title.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    return str((lines[0] if lines else "") or spec.get("title") or saved.get("title") or "").strip()


def live_state(studio, slug, project_id=None) -> str:
    """'running', 'queued' or 'held' when Studio is making this video now, or it waits in the queue (cr_queue, held
    there too), else ''."""
    jobs = getattr(studio, "JOBS", None) or {}
    state = str((jobs.get(slug) or {}).get("state") or "")
    if state in ("running", "queued", "held"):
        return state
    keeper = getattr(studio, "_cr_queue", None)
    try:
        if project_id and keeper is not None and keeper.has_project(project_id):
            return "queued"
    except Exception:                                          # noqa: BLE001 - only a view of the queue
        pass
    if (getattr(studio, "RUNNING", None) or {}).get("slug") == slug:
        return "running"
    lock = getattr(studio, "LOCK", None)
    queue = getattr(studio, "QUEUE", None) or []
    try:
        if lock is not None:
            with lock:
                waiting = [dict(s) for s in queue if isinstance(s, dict)]
        else:
            waiting = [dict(s) for s in queue if isinstance(s, dict)]
    except Exception:                                          # noqa: BLE001 - only a view of the queue
        waiting = []
    if any(s.get("slug") == slug for s in waiting):
        return "queued"
    return ""


def effective_state(studio, slug, saved) -> str:
    """The video's state: the live job's, else its saved project's. A saved "running" or "queued" with no live job is
    stale (the project was running when it began, and its Studio has since closed): that video has stopped."""
    live = live_state(studio, slug, (saved or {}).get("id"))
    if live:
        return live
    state = str(((getattr(studio, "JOBS", None) or {}).get(slug) or {}).get("state") or "")
    if state:
        return state
    saved_state = str((saved or {}).get("status") or "")
    return "stopped" if saved_state in ("running", "queued") else saved_state


def stopped_jobs(studio):
    """(job folder, saved project) for each video that stopped before finishing: the rule of the Projects list's
    stopped cards (cr_workspace /cr-workspace/stopped)."""
    import cr_media
    engine = getattr(studio, "mv", None)
    for job in (engine.list_jobs() if hasattr(engine, "list_jobs") else []):
        try:
            job = Path(job)
            if not _SLUG.fullmatch(job.name):
                continue
            presenter = (job / cr_media.PRESENTER_FAILED).is_file()
            if engine.final_video(job).is_file() and not presenter:
                continue
            saved = saved_project(job)
            if not saved:
                continue
            if effective_state(studio, job.name, saved) in ("done", "queued", "running", "held"):
                continue
            yield job, saved
        except (OSError, ValueError, TypeError, AttributeError):
            continue


# ── what the last run did (run_owned_spec writes RUN_FILE around each run) ──────────────────────────────────────────

def run_started(job, spec) -> None:
    """Before a video's run: when it started, by which Studio. A run that never reaches run_ended was cut off with
    its Studio (closed, crashed, the computer turned off)."""
    try:
        _write(Path(job) / RUN_FILE, {"started": time.time(), "studio_pid": os.getpid(),
                                      "run_id": str((spec or {}).get("cr_video_run_id") or "")})
        (Path(job) / CANCELLED).unlink(missing_ok=True)       # started again (Resume, Carry on): no longer cancelled
        if (spec or {}).get("cr_carry_on_after_reset") is True:
            _write(Path(job) / CARRY_ON, {"after_usage_reset": True, "at": time.time()})
    except OSError:
        pass                                                   # a bookkeeping file never stops a video


def run_ended(job, state, failure=None) -> None:
    """After a video's run: how it ended, and the kind of stop (cr_failure's record) when it stopped."""
    try:
        record = _read(Path(job) / RUN_FILE)
        failure = failure if isinstance(failure, dict) else {}
        record.update(ended=time.time(), state=str((state or {}).get("state") or ""),
                      category=str(failure.get("category") or ""), step=str(failure.get("failed_step") or ""),
                      provider=str(failure.get("provider") or ""))
        _write(Path(job) / RUN_FILE, record)
        if record["state"] == "done":
            # a finished video needs no kept 3D review pictures (cr_threed_resume; the answers are small and stay)
            import shutil
            shutil.rmtree(Path(job) / "threed" / "review_keep" / "previews", ignore_errors=True)
    except OSError:
        pass


def last_stop(job, home=None, *, studio_pid=None) -> dict:
    """{"kind": local | cancelled | other | interrupted_here | unknown, "category", "at"} for the video's last run."""
    job = Path(job)
    run = _read(job / RUN_FILE)
    if not run.get("started"):
        return {"kind": "unknown", "category": "", "at": 0.0}
    if not run.get("ended"):
        if int(run.get("studio_pid") or 0) == int(studio_pid or os.getpid()):
            return {"kind": "interrupted_here", "category": "", "at": float(run["started"])}
        # Studio ended with it: the worker's progress file is saved twice a second, so its time is when it stopped
        at = 0.0
        run_id = str(run.get("run_id") or "")
        if home is not None and re.fullmatch(r"video_[a-f0-9]{24}", run_id):
            at = _mtime(Path(home) / "output" / "cr-video-workers" / run_id / "progress.json")
        return {"kind": "local", "category": "studio_closed", "at": at or _mtime(job / RUN_FILE)}
    category = str(run.get("category") or "")
    if run.get("state") == "cancelled" or category == "cancelled":
        return {"kind": "cancelled", "category": "cancelled", "at": float(run["ended"])}
    if run.get("state") == "done":
        return {"kind": "done", "category": "", "at": float(run["ended"])}
    return {"kind": "local" if category in LOCAL_CATEGORIES else "other", "category": category,
            "at": float(run["ended"]), "provider": str(run.get("provider") or ""), "step": str(run.get("step") or "")}


# ── Claude steps ──────────────────────────────────────────────────────────────────────────────────────────────────

_STATE_HEAD = re.compile(rb'"state"\s*:\s*"(\w+)"')


def claude_caches(home, project_id) -> dict:
    """stage id -> the local cache of each Claude step of this project that isn't plainly complete (running,
    failed, failed_local, complete_local). A complete step's file starts with its state, so only the head is read."""
    folder = Path(home) / "output" / "cr-claude-cache" / project_id
    out = {}
    try:
        paths = list(folder.glob("cc_*.json"))
    except OSError:
        return out
    for path in paths:
        if not STAGE.fullmatch(path.stem):
            continue
        try:
            with open(path, "rb") as f:
                head = f.read(512)
        except OSError:
            continue
        found = _STATE_HEAD.search(head)
        if found and found.group(1) == b"complete":
            continue
        data = _read(path)
        if data and data.get("state") != "complete":
            out[path.stem] = data
    return out


def stage_words(code, status) -> dict:
    """The plain words for one stuck Claude step."""
    code = str(code or "")
    if status == "authorized" or code not in STAGE_WORDS:
        said, spent, note = STAGE_WORDS[""] if status == "authorized" or not code else STAGE_WORDS["cli_failed"]
    else:
        said, spent, note = STAGE_WORDS[code]
    return {"reason": said, "may_have_spent": SPENT[spent], "spent": spent, "spent_note": note}


def stuck_stages(home, project_id, server_stages=None, *, allowance=None, now=None, claude_ready=None) -> list:
    """Each Claude step of this project that stopped and would stop the video again: the server's failed,
    authorized (cut off) and approved-but-unused (retry_allowed) steps, merged with this computer's caches (a server
    that can't be reached still leaves the local picture)."""
    now = time.time() if now is None else now
    local = claude_caches(home, project_id)
    rows = {}
    for stage in server_stages or []:
        if not isinstance(stage, dict) or stage.get("status") not in ("failed", "authorized", "retry_allowed"):
            continue
        sid = str(stage.get("stage_id") or "")
        cache = local.get(sid) or {}
        status = stage["status"]
        if status == "authorized" and cache.get("state") == "complete_local":
            continue                                           # Claude finished it; the resume records the result
        if status == "authorized" and cache.get("state") == "failed_local":
            status = "failed"                                  # its failure is saved here, not yet on ChannelRecipe
        rows[sid] = {"stage_id": sid, "status": status, "kind": str(stage.get("kind") or ""),
                     "model_role": str(stage.get("model_role") or ""),
                     "error_code": str(stage.get("error_code") or cache.get("error_code") or ""),
                     "attempts": int(stage.get("attempts") or 1), "can_retry": bool(stage.get("can_retry")),
                     "minutes": round(float((stage.get("usage") or {}).get("duration_seconds") or 0) / 60, 1),
                     "label": str(cache.get("label") or ""),
                     # cr_claude marks a step "running" here before Claude Code starts: without it, it never started
                     "never_started": status == "authorized" and cache.get("state") != "running"}
    if server_stages is None:
        for sid, cache in local.items():
            if cache.get("state") in ("running", "failed", "failed_local"):
                rows[sid] = {"stage_id": sid, "status": "authorized" if cache["state"] == "running" else "failed",
                             "kind": "", "model_role": "", "error_code": str(cache.get("error_code") or ""),
                             "attempts": 0, "can_retry": None, "minutes": 0, "label": str(cache.get("label") or "")}
    out = []
    for row in rows.values():
        row.update(stage_words(row["error_code"], row["status"]))
        row["step"] = KINDS.get(row["kind"], "a Claude step") + (" (Opus)" if row["model_role"] == "visual" else
                                                                 " (Sonnet)" if row["model_role"] == "general" else "")
        fix = ""
        if row["status"] == "retry_allowed":
            row.update(reason="One more attempt is already approved for this step; it hasn't run yet.",
                       may_have_spent=False, spent="no", spent_note="Resuming runs it.")
        elif row.get("never_started"):
            row.update(reason="ChannelRecipe allowed this step, but its answer didn't reach this computer, so Claude "
                              "never started it.", may_have_spent=False, spent="no",
                       spent_note="None of your Claude usage went on it.")
        elif row["error_code"] in ("auth_missing", "authentication_required") and claude_ready is False:
            fix = "Sign in to Claude Code again (run claude in a terminal, then /login), then carry on."
        elif row["error_code"] == "model_unavailable":
            fix = "Check your Claude models in Setup & accounts, then carry on."
        elif row["can_retry"] is False:
            fix = ("This step has had every attempt it can have, so it can't be tried again. The video can only be "
                   "made again as a new video (which pays again for everything).")
        if row["error_code"] == "usage_limit" and (allowance or {}).get("reset_at"):
            row["reset_at"], row["reset_label"] = allowance["reset_at"], allowance.get("reset_label") or ""
            row["reset_passed"] = now >= float(allowance["reset_at"])
        row["fix_first"] = fix
        row["needs_approval"] = row["status"] in ("failed", "authorized") and not fix
        out.append(row)
    out.sort(key=lambda r: r["stage_id"])
    return out


# ── paid media tasks (cr_media receipts) ──────────────────────────────────────────────────────────────────────────

def _task_of(model) -> str:
    import cr_media
    for info in cr_media.PROVIDERS.values():
        if not isinstance(info, dict):
            continue
        for task, value in (info.get("models") or {}).items():
            if value == model:
                return {"avatar_pro": "avatars", "image_edit": "images"}.get(task, task)
        if any(model == c.get("id") for c in info.get("narration_models") or [] if isinstance(c, dict)):
            return "narration"
        if any(model in (c.get("id"), c.get("generate"), c.get("edit")) for c in info.get("image_models") or []
               if isinstance(c, dict)):
            return "images"
    return "media"


TASK_WORDS = {"images": "picture", "narration": "voice", "motion": "moving clip", "avatars": "presenter",
              "media": "media"}


def receipts(job):
    """(path, receipt) for every paid media task receipt in the video's folder, leaving out uploads and the receipts
    set aside before (.uncertain-…, .failed-…: kept, never read by cr_media.run again)."""
    try:
        folders = [p for p in Path(job).rglob(".cr-tasks") if p.is_dir()]
    except OSError:
        return
    for folder in folders:
        for path in sorted(folder.glob("*.json")):
            if path.name.startswith("up-") or ".uncertain-" in path.name or ".failed-" in path.name:
                continue
            data = _read(path)
            # cr_media's paid tasks (each names its provider); cr_score's music cues skip a cue they can't settle
            if data.get("status") and data.get("provider"):
                yield path, data


def stuck_media(job, *, since=0.0, message="") -> list:
    """Each paid media task of this video that isn't finished: sent with no answer (uncertain), accepted and still
    being made (checking: free), or failed. A failed one is listed only when it stopped the video: it failed during the
    last run and the stop names its kind of task (cr_media's "failed this images task"), newest of that kind. A picture
    or clip the engine skipped and went on without (cr_media.skippable) never holds the video up."""
    import cr_media
    job = Path(job)
    voice_stop = (job / cr_media.VOICE_FAILED).is_file()
    presenter_stop = (job / cr_media.PRESENTER_FAILED).is_file()
    out, failed = [], []
    for path, r in receipts(job):
        status = r.get("status")
        if status == "complete":
            continue
        provider = str(r.get("provider") or "kie")
        task = _task_of(r.get("model"))
        if (task == "narration" and voice_stop) or (task == "avatars" and presenter_stop):
            continue                                           # the voice and presenter choices own these
        label = (cr_media.PROVIDERS.get(provider) or {}).get("label", provider)
        what = TASK_WORDS.get(task, "media")
        row = {"receipt": path.relative_to(job).as_posix(), "task": task, "provider": provider,
               "credits": r.get("credits"), "at": _mtime(path), "do_first": ""}
        if status == "submitting" and not r.get("id"):
            row.update(state="uncertain", free=False, may_have_spent=True, money=True, needs_approval=True,
                       pays_twice=True, reason=f"{label} was sent a {what} request but no clear answer came back, so it may have "
                              f"started and been charged. Check its task log; trying again can pay for it twice.")
        elif r.get("id") and status in ("pending", "generated", "submitting"):
            row.update(state="checking", free=True, may_have_spent=True, money=True, needs_approval=False,
                       pays_twice=False, reason=f"A {what} task {label} accepted was still being made (or its file didn't download). "
                              "Carrying on checks the same task; nothing is paid again.")
        elif status == "failed":
            if _mtime(path) < since - 5:
                continue
            category = str(r.get("failure_category") or cr_media.failure_category(r.get("failure_code"),
                                                                                  r.get("failure_message") or ""))
            free = cr_media.fresh_attempt_ok(provider, task, r, asked=True)
            row.update(state="failed", category=category, free=bool(free), money=True, pays_twice=False)
            if r.get("rejected"):
                fix = {"balance": f"Top up your {label} balance, then carry on.",
                       "rate_limit": f"{label} asked Studio to slow down: wait a few minutes, then carry on.",
                       "moderation": f"{label} refused the words of this {what}: change the video's instructions.",
                       "access": f"Reconnect your {label} key in Setup & accounts, then carry on."}.get(category, "")
                row.update(may_have_spent=False, needs_approval=False, do_first=fix,
                           reason=f"{label} refused this {what} request before making anything, so nothing was "
                                  "charged." + (" Carrying on sends it again; that can't pay twice." if free else ""))
            elif free:
                row.update(may_have_spent=False, needs_approval=False,
                           reason=f"{label} failed this {what} on its side and charged nothing. Carrying on tries it "
                                  "again; that can't pay twice.")
            else:
                charged = r.get("credits")
                said = (f"charged {charged} credits" if isinstance(charged, (int, float)) and charged
                        else "didn't say what it charged" if charged is None else "charged nothing")
                row.update(may_have_spent=charged != 0, needs_approval=True, pays_twice=charged != 0,
                           reason=f"{label} failed this {what} task and {said}. Trying again "
                                  + ("pays for it again." if charged != 0 else "makes a new one."))
            failed.append(row)
            continue
        else:
            continue
        out.append(row)
    for task in dict.fromkeys(r["task"] for r in failed):
        named = (r"image generation stopped|\bimages? (?:task|request)\b" if task == "images"
                 else rf"\b{re.escape(task)} (?:task|request)\b")
        if re.search(named, message or "", re.I):
            out.append(max((r for r in failed if r["task"] == task), key=lambda r: r["at"]))
    return out


# ── the whole picture ────────────────────────────────────────────────────────────────────────────────────────────

def _key(stages, media) -> str:
    wanted = sorted([s["stage_id"] for s in stages if s.get("needs_approval")]
                    + [m["receipt"] for m in media if m.get("needs_approval")])
    return hashlib.sha256(json.dumps(wanted).encode()).hexdigest()[:12] if wanted else ""


def carry_on(job) -> bool:
    return _read(Path(job) / CARRY_ON).get("after_usage_reset") is True


def auto_record(job) -> dict:
    record = _read(Path(job) / AUTO_FILE)
    record["count"] = int(record.get("count") or 0)
    return record


def diagnose(home, job, *, server_stages=None, allowance=None, message="", claude_ready=None, now=None) -> dict:
    """What stopped this video and what carrying it on involves (see the module's first lines)."""
    job, now = Path(job), time.time() if now is None else now
    saved = saved_project(job)
    project_id = saved.get("id", "")
    run = _read(job / RUN_FILE)
    since = float(run.get("started") or 0)
    stages = stuck_stages(home, project_id, server_stages, allowance=allowance, now=now,
                          claude_ready=claude_ready) if project_id else []
    media = stuck_media(job, since=since, message=message)
    stop = last_stop(job, home)
    stopped_on = ("claude_stage" if stages else "media" if media else "local" if stop["kind"] == "local" else "")
    needs = [s for s in stages if s["needs_approval"]] + [m for m in media if m.get("needs_approval")]
    fixes = [r["fix_first"] for r in stages if r.get("fix_first")]          # blocking: nothing can carry it on yet
    advice = list(dict.fromkeys(m["do_first"] for m in media if m.get("do_first")))   # free once it's done
    spent_claude = any(s["may_have_spent"] for s in stages if s["needs_approval"])
    spent_money = any(m.get("may_have_spent") and m.get("needs_approval") for m in media)
    usage = [s for s in stages if s["error_code"] == "usage_limit" and s["needs_approval"]]
    reset = next((s for s in usage if s.get("reset_at")), None)
    out = {"slug": job.name, "project_id": project_id, "title": job_title(job, saved), "stopped_on": stopped_on,
           "stop": stop["category"] or stop["kind"], "stopped_at": _when(stop["at"]),
           "claude_stages": stages, "media": media, "needs_approval": bool(needs) and not fixes,
           "fix_first": fixes, "do_first": advice, "key": _key(stages, media),
           "free_to_resume": not needs and not fixes,
           "carry_on_after_reset": carry_on(job), "auto_resumes": auto_record(job)["count"], "auto_limit": AUTO_LIMIT}
    if reset:
        out.update(reset_at=reset["reset_at"], reset_label=reset.get("reset_label") or "",
                   reset_passed=bool(reset.get("reset_passed")))
    out["groups"] = stage_groups(stages)
    if usage:
        import cr_allowance
        threed = any(s.get("label", "").startswith("3D picture review") for s in stages)
        out["usage_line"] = cr_allowance.stopped_message(out.get("reset_label") or None, threed=threed)
    out["what_to_tell_them"] = _tell(out, spent_claude, spent_money, usage)
    return out


# one bullet per kind of stopped step (Swapnil's card, 2026-10-29, repeated "A Claude step that looks at pictures
# (Opus): Claude stopped at your plan's usage limit..." once per call, four times)
GROUP_NAMES = {"text": "Claude writing", "vision": "Claude picture checks", "research": "Claude research"}
GROUP_WORDS = {"usage_limit": "stopped at your Claude plan's limit",
               "timeout": "ran out of time and {was} stopped",
               "network_interrupted": "lost Claude's connection mid-answer",
               "cli_failed": "ended without an answer",
               "auth_missing": "stopped: Claude Code was signed out",
               "authentication_required": "stopped: Claude Code was signed out",
               "model_unavailable": "couldn't use the chosen model",
               "cancelled": "{was} cut off when the video was cancelled",
               "": "{was} cut off mid-way (Studio closed, the computer slept, or the video worker stopped)"}


def stage_groups(stages) -> list:
    """The stuck Claude steps grouped by what they are and what happened, each in one plain line with a count:
    "3D picture review, round 1: 3 calls stopped at your Claude plan's limit." The spent note follows once."""
    groups = {}
    for s in stages or []:
        name = s.get("label") or (GROUP_NAMES.get(s.get("kind"), "Claude steps") + (
            " (Opus)" if s.get("model_role") == "visual" else " (Sonnet)" if s.get("model_role") == "general" else ""))
        if s.get("status") == "retry_allowed" or s.get("never_started"):
            what = s.get("reason", "")
        else:
            what = GROUP_WORDS.get(s.get("error_code") if s.get("status") != "authorized" else "",
                                   GROUP_WORDS["cli_failed"])
        row = groups.setdefault((name, what), {"name": name, "count": 0, "what": what, "error_code": s.get("error_code", ""),
                                                "spent_note": s.get("spent_note", ""), "needs_approval": False,
                                                "stage_ids": []})
        row["count"] += 1
        row["needs_approval"] = row["needs_approval"] or bool(s.get("needs_approval"))
        row["stage_ids"].append(s.get("stage_id"))
    out = []
    for row in groups.values():
        n = row["count"]
        what = row["what"].format(was="was" if n == 1 else "were")
        if row["what"] in GROUP_WORDS.values():
            row["line"] = f"{row['name']}: {n} call{'' if n == 1 else 's'} {what}."
        else:
            row["line"] = f"{row['name']} ({n} call{'' if n == 1 else 's'}): {what}"
        out.append(row)
    return out


def _tell(d, spent_claude, spent_money, usage) -> str:
    if d["fix_first"]:
        return " ".join(dict.fromkeys(d["fix_first"]))
    if not d["needs_approval"]:
        first = (" ".join(d["do_first"]) + " ") if d.get("do_first") else ""
        if d["media"]:
            return first + ("It can then carry on as it is" if first else "It can carry on as it is") + \
                ": the unfinished paid tasks are checked or sent again only where that can't pay twice."
        return "It can carry on as it is: everything already made is reused and nothing is paid twice."
    parts = []
    n = sum(1 for s in d["claude_stages"] if s["needs_approval"])
    if n:
        parts.append(f"{n} Claude step{'s' if n > 1 else ''} stopped and need{'s' if n == 1 else ''} one more "
                     "attempt" + (": some of your Claude usage may already have gone on "
                                  + ("it" if n == 1 else "them") + ", and trying again uses more" if spent_claude
                                  else "") + ".")
    m = sum(1 for x in d["media"] if x.get("needs_approval"))
    if m:
        parts.append(f"{m} paid media task{'s' if m > 1 else ''} got no clear result" +
                     (": trying again can pay for it a second time" if spent_money else "") + ".")
    if usage and d.get("usage_line"):
        parts.append(d["usage_line"].split(". ", 1)[-1])        # "When your Claude limit resets (...), press Try..."
        if d.get("reset_label") and not d.get("reset_passed"):
            parts.append("Studio carries it on by itself then, as you chose." if d.get("carry_on_after_reset") else
                         "You can carry it on where it stopped then, or now if you added usage.")
    parts.append("Everything else already made is reused. Nothing runs again unless you say yes.")
    return " ".join(parts)


# ── the creator's yes ─────────────────────────────────────────────────────────────────────────────────────────────

class Refused(RuntimeError):
    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data or {}


def set_aside(job, receipt_rel, kind) -> str:
    """A receipt the creator said yes to is kept under its own name, so the next run may send that task again."""
    job = Path(job)
    path = (job / receipt_rel).resolve()
    if job.resolve() not in path.parents or path.parent.name != ".cr-tasks" or path.suffix != ".json":
        raise Refused("That receipt isn't this video's.")
    target = path.with_name(path.stem + f".{kind}-{int(time.time() * 1000)}.json")
    path.replace(target)
    return target.name


def approve(bridge, job, diag, *, key=None) -> dict:
    """Allow exactly the retries in diag (one more attempt per stuck Claude step on ChannelRecipe; the media receipts
    that need a yes set aside). Never resumes by itself: the caller does, so the whole action is one decision."""
    if diag.get("needs_approval") and not key:
        raise Refused("Check what stopped this video first: approving needs the recovery key it shows, so only what "
                      "you were told is tried again.", {"recovery": diag})
    if key and key != diag.get("key"):
        raise Refused("What stopped this video changed since you looked. Check it again before approving.",
                      {"recovery": diag})
    if diag.get("fix_first"):
        raise Refused(" ".join(dict.fromkeys(diag["fix_first"])), {"recovery": diag})
    approved, set_aside_rows = [], []
    for stage in diag.get("claude_stages") or []:
        if not stage.get("needs_approval"):
            continue
        try:
            bridge._mcp_tool("local_studio_retry_stage", {"project_id": diag["project_id"],
                                                          "stage_id": stage["stage_id"], "acknowledge_costs": True})
        except Exception as error:                             # noqa: BLE001 - said plainly, nothing more is changed
            detail = str(error) if error.__class__.__name__ == "WorkflowError" else ""
            raise Refused(("ChannelRecipe didn't allow another attempt for " + stage["step"] + ". "
                           + (detail + " " if detail else "") + "Nothing else was changed.").strip(),
                          {"approved": approved, "recovery": diag}) from None
        approved.append(stage["stage_id"])
    for row in diag.get("media") or []:
        if row.get("needs_approval"):
            set_aside_rows.append(set_aside(job, row["receipt"], "uncertain" if row["state"] == "uncertain"
                                            else "failed"))
    return {"approved_stages": approved, "set_aside": set_aside_rows}


# ── Studio carrying on by itself ──────────────────────────────────────────────────────────────────────────────────

UNCHECKED = "its saved Claude steps can't be checked right now"

def mark_cancelled(job, why) -> None:
    """The creator cancelled this video (Cancel while it was made or waited, or Remove from a hold)."""
    try:
        if job is not None and Path(job).is_dir():
            _write(Path(job) / CANCELLED, {"at": time.time(), "why": str(why)[:120]})
    except OSError:
        pass


def cancelled(job, home=None, *, since=0.0) -> bool:
    """Whether the creator cancelled this video after its last run started: its own cancel mark, or the video
    worker's cancelled.json for this folder (a cancel made while it waited in the queue writes only that)."""
    job = Path(job)
    if (job / CANCELLED).is_file():
        return True
    if home is None:
        return False
    try:
        marks = list((Path(home) / "output" / "cr-video-workers").glob("video_*/cancelled.json"))
    except OSError:
        return False
    for mark in marks:
        data = _read(mark)
        if data.get("slug") == job.name and float(data.get("stopped_at") or _mtime(mark)) >= since - 5:
            return True
    return False


def engine_voice(job, saved) -> bool:
    """Whether this video's voice is made by the engine on the creator's own account (ElevenLabs on their key, or
    another voice service the engine calls itself): no receipts there, so nothing tells which parts were already paid
    for. cr_media's Kie voices keep receipts; the creator's own recording pays nothing."""
    import cr_media
    spec = saved.get("job_spec") if isinstance(saved.get("job_spec"), dict) else {}
    if spec.get("byo_audio"):
        return False
    if str(spec.get("style") or saved.get("style") or "").startswith("cr-shorts"):
        # Shorts read their lines on the creator's ElevenLabs key when one is set (shorts/ranking/voice.py)
        return bool(os.getenv("ELEVENLABS_API_KEY", "").strip())
    chosen = cr_media.job_voice(job)
    if chosen.get("provider") in ("own", "kie", "fish"):
        return False
    if cr_media.fish_voice(job, str(spec.get("style") or saved.get("style") or "")):
        return False                                           # Fish Audio parts keep receipts too (cr_fish)
    if chosen.get("provider") == "original":
        return True
    try:
        return not cr_media.active("narration")
    except Exception:                                          # noqa: BLE001 - unknown: treated as the engine's
        return True


def voice_unfinished(job, saved) -> bool:
    """Its voice may be part-way made: no finished narration yet, and its script (or, for a Short, its folder) is
    there."""
    job = Path(job)
    spec = saved.get("job_spec") if isinstance(saved.get("job_spec"), dict) else {}
    if str(spec.get("style") or saved.get("style") or "").startswith("cr-shorts"):
        return True                                            # a Short's voice files are its pipeline's own
    if (job / "audio.mp3").is_file() and (job / "subs.srt").is_file():
        return False
    return (job / "script.txt").is_file()


def queue_on_hold(studio) -> str:
    """Why Studio's queue (cr_queue) holds its videos for a stop every video would hit (no balance, the disk full,
    Claude's limit), or "": Studio starts nothing by itself while it does."""
    keeper = getattr(studio, "_cr_queue", None)
    if keeper is None:
        return ""
    try:
        state = keeper.status()
    except Exception:                                          # noqa: BLE001
        return ""
    if state.get("held") or state.get("reason"):
        return str(state.get("reason") or "the queue is on hold")
    return ""


def _auto_plan(job, *, home=None, server_stages=None, allowance=None, live="", now=None, studio_pid=None) -> dict:
    """{"safe", "why", "approve": [stage ids the creator's up-front choice covers]} for carrying this video on now."""
    home = Path(home or HOME)
    now = time.time() if now is None else now
    job = Path(job)
    no = lambda why: {"safe": False, "why": why, "approve": []}    # noqa: E731
    if live:
        return no("it is being made now")
    saved = saved_project(job)
    if not saved:
        return no("it isn't a saved video of this Studio")
    record = auto_record(job)
    if record["count"] >= AUTO_LIMIT:
        return no(f"Studio already carried it on by itself {AUTO_LIMIT} times")
    if now - float(record.get("last_try") or 0) < RETRY_BACKOFF:
        return no("Studio tried to carry it on a few minutes ago")
    stop = last_stop(job, home, studio_pid=studio_pid)
    if stop["kind"] in ("unknown", "done", "interrupted_here"):
        return no("there's no record of a stop on this computer")
    run = _read(job / RUN_FILE)
    if stop["kind"] == "cancelled" or cancelled(job, home, since=float(run.get("started") or 0)):
        return no("it was cancelled")
    usage_stop = stop["category"] == "usage_limit"
    if stop["at"] and now - stop["at"] > AUTO_WINDOW and not usage_stop:
        return no("it stopped more than a day ago")
    if stop["kind"] != "local" and not usage_stop:
        return no(f"it stopped on something that needs you ({stop['category'] or 'unknown'})")
    if voice_unfinished(job, saved) and engine_voice(job, saved):
        # the coordinator, 2026-10-26: until the engine is proven safe here, a voice on the creator's own account
        # (no receipts) is carried on only by the creator's Resume
        return no("its voice was being made on your own voice account, which keeps no receipts: press Resume yourself")
    import cr_media
    for path, r in receipts(job):
        if r.get("status") == "submitting" and not r.get("id"):
            return no("a paid media task got no clear answer, so it needs your yes")
        if r.get("status") == "failed":
            # cr_media sends a failed task again when a run meets it, as the creator's Resume (asked): a run Studio
            # starts by itself must never be where that happens unless Studio may do it on its own too
            provider, task = str(r.get("provider") or "kie"), _task_of(r.get("model"))
            if (cr_media.fresh_attempt_ok(provider, task, r, asked=True)
                    and not cr_media.fresh_attempt_ok(provider, task, r)):
                return no("a paid task's failure needs your yes to try it again")
    if server_stages is None:
        return no(UNCHECKED)
    stages = stuck_stages(home, saved["id"], server_stages, allowance=allowance, now=now)
    # a usage-limit refusal, failed or already allowed one more attempt (by this choice, when a resume then failed)
    usage = [s for s in stages if s["error_code"] == "usage_limit" and s["status"] in ("failed", "retry_allowed")]
    others = [s for s in stages if s not in usage]
    if others:
        cut = any(s["status"] == "authorized" for s in others)
        return no("a Claude step was cut off mid-way, so running it again needs your yes" if cut
                  else "an approved attempt waits for you to resume it" if all(s["status"] == "retry_allowed"
                                                                               for s in others)
                  else "a Claude step failed, so trying it again needs your yes")
    if usage:
        if not carry_on(job):
            return no("Claude's usage limit stopped it, and carrying on after the reset wasn't chosen")
        reset = next((s.get("reset_at") for s in usage if s.get("reset_at")), None)
        if not reset:
            return no("Claude didn't say when its usage resets")
        if now < float(reset) + USAGE_MARGIN:
            return no("Claude's usage hasn't reset yet")
        if now - float(reset) > AUTO_WINDOW:
            return no("Claude's usage reset more than a day ago")
        if any(s["fix_first"] for s in usage):
            return no(next(s["fix_first"] for s in usage if s["fix_first"]))
        return {"safe": True, "why": "Claude's usage reset, as you chose",
                "approve": [s["stage_id"] for s in usage if s["status"] == "failed"]}
    if usage_stop:
        return no("Claude's usage limit stopped it")
    words = {"studio_closed": "Studio closed while it was being made", "crash": "a program it uses crashed",
             "out_of_memory": "the computer ran out of memory", "tool_error": "a video tool on this computer failed"}
    return {"safe": True, "why": words.get(stop["category"], "it stopped on this computer"), "approve": []}


def safe_to_resume(job, *, home=None, server_stages=None, allowance=None, live="", now=None, studio_pid=None):
    """(True, why) when Studio may carry this video on by itself without paying twice; else (False, why)."""
    plan = _auto_plan(job, home=home, server_stages=server_stages, allowance=allowance, live=live, now=now,
                      studio_pid=studio_pid)
    return plan["safe"], plan["why"]


def note_auto(job, why, *, ok=True, error="") -> int:
    record = auto_record(job)
    if ok:
        record["count"] += 1
        record.setdefault("runs", []).append({"at": time.time(), "why": why})
        record.pop("refused", None)
    else:
        record.update(refused=str(error)[:300])
    record["last_try"] = time.time()
    try:
        _write(Path(job) / AUTO_FILE, record)
    except OSError:
        pass
    return record["count"]


# ── the same video again ───────────────────────────────────────────────────────────────────────────────────────────

RANKING = re.compile(r"\[\[ranking=(\{.*?\})\]\]", re.S)


def _ranking(extra) -> str:
    found = RANKING.search(str(extra or ""))
    if not found:
        return ""
    try:
        return json.dumps(json.loads(found.group(1)), sort_keys=True)
    except ValueError:
        return found.group(1)


def _recording(ref) -> str:
    """A narration recording as an upload's id: Create sends the id, a saved job_spec holds the path the run route
    resolved it to (uploads/voiceovers/<id>), on Windows with backslashes. "" for none."""
    return re.split(r"[\\/]", str(ref or "").strip())[-1]


def twins(studio, titles, style, minutes, *, extra="", recording=None) -> list:
    """For each title, the newest stopped video of this Studio with the same title, style and minutes (and, for a
    Ranking Short, the same countdown choices): Studio offers to resume it before starting again. recording: the new
    video's own narration recording ("" for an AI voice; None: not compared). A stopped video narrated another way is
    not the same video: resuming it would quietly drop the recording, or keep one the creator no longer asked for."""
    wanted = {title_key(t): str(t).strip() for t in titles if title_key(t)}
    if not wanted:
        return []
    ranking = _ranking(extra)
    found = {}
    for job, saved in stopped_jobs(studio):
        spec = saved.get("job_spec") if isinstance(saved.get("job_spec"), dict) else {}
        key = title_key(job_title(job, saved))
        if key not in wanted or str(spec.get("style") or saved.get("style") or "") != str(style or ""):
            continue
        try:
            if int(spec.get("minutes") or 0) != int(minutes or 0):
                continue
        except (TypeError, ValueError):
            continue
        if _ranking(spec.get("extra")) != ranking:
            continue
        if recording is not None and _recording(spec.get("byo_audio")) != _recording(recording):
            continue
        at = _mtime(job / "cr-project.json")
        if key not in found or at > found[key]["at"]:
            found[key] = {"title": wanted[key], "slug": job.name, "id": "video:" + job.name,
                          "project_id": saved["id"], "at": at, "stopped": _when(at)}
    return [{k: v for k, v in row.items() if k != "at"} for row in found.values()]


# ── routes and the watcher ────────────────────────────────────────────────────────────────────────────────────────

def _message(studio, home, job, project_id) -> str:
    """The video's last stop message: the live job's, else what its finish record or worker saved."""
    state = (getattr(studio, "JOBS", {}) or {}).get(job.name) or {}
    if state.get("error"):
        return str(state["error"])
    report = _read(job / ".cr-finish.json") or _read(job / ".cr-finish-pending.json")
    failure = report.get("failure") if isinstance(report.get("failure"), dict) else {}
    return str(failure.get("message") or "")


def install(studio, activity, *, token, home=None, bridge=None, start_watcher=True):
    from flask import jsonify, request
    home = Path(home or HOME)
    if bridge is None:
        import cr_bridge as bridge
    app = studio.app

    def find(slug):
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or slug in (".", ".."):
            raise Refused("Unavailable video.")
        found = studio.mv.find_job(slug) if hasattr(studio.mv, "find_job") else None
        job = Path(found).resolve() if found is not None else None
        if job is None or not job.is_dir() or not saved_project(job):
            raise Refused("This isn't a saved video of this Studio.")
        return job

    def allowance_now():
        try:
            import cr_claude
            local = cr_claude.status()
            return local.get("allowance") or {}, bool(local.get("ready"))
        except Exception:                                      # noqa: BLE001 - only an extra detail
            return {}, None

    def server_stages(project_id, sync):
        if sync:
            try:
                import cr_claude
                cr_claude.sync_receipts(bridge)                # records results already saved here, never runs Claude
            except Exception:                                  # noqa: BLE001
                pass
        try:
            return bridge.project_progress(project_id).get("stages") or []
        except Exception:                                      # noqa: BLE001 - the local picture still stands
            return None

    def look(job, *, sync=False, server=True):
        saved = saved_project(job)
        stages = server_stages(saved["id"], sync) if server else None
        message = _message(studio, home, job, saved["id"])
        diag = diagnose(home, job, server_stages=stages, message=message)
        allowance = {}
        if any(s["error_code"] in ("usage_limit", "auth_missing", "authentication_required")
               for s in diag["claude_stages"]):
            # Claude Code's own check (usually answered from its 5-minute cache): the reset it reported, signed in
            allowance, ready = allowance_now()
            diag = diagnose(home, job, server_stages=stages, allowance=allowance, claude_ready=ready, message=message)
        diag["server_checked"] = stages is not None
        diag["live"] = live_state(studio, job.name, saved["id"])
        try:
            import cr_media
            diag["finished"] = (studio.mv.final_video(job).is_file()
                                and not (job / cr_media.PRESENTER_FAILED).is_file())
        except Exception:                                      # noqa: BLE001
            diag["finished"] = False
        plan = _auto_plan(job, home=home, server_stages=stages, allowance=allowance, live=diag["live"])
        diag["auto"] = {"safe": plan["safe"], "why": plan["why"], "count": diag["auto_resumes"], "limit": AUTO_LIMIT}
        try:
            diag["claude_use"] = claude_use(job)
        except Exception:                                      # noqa: BLE001 - only an extra detail
            pass
        return diag

    def claude_use(job):
        import cr_claude_use
        return cr_claude_use.summary(home, job, saved_project(job).get("id"))

    def resume(project_id):
        client = app.test_client()
        answer = client.post("/cr-submit-project", json={"project_id": project_id}, base_url="http://127.0.0.1:7860",
                             headers={"X-CR-Local-Token": token})
        data = answer.get_json(silent=True) or {}
        if not 200 <= answer.status_code < 300:
            raise Refused(str(data.get("error") or "Studio couldn't carry this video on."), data)
        return data

    @app.get("/cr-workspace/claude-use/<slug>")
    def cr_claude_use_route(slug):
        """What this video has used of Claude so far (the progress and stopped cards); never a plan's quota."""
        try:
            return jsonify(claude_use(find(slug)))
        except Refused as error:
            return jsonify(error=str(error)), 404
        except Exception:                                      # noqa: BLE001
            return jsonify(error="Studio couldn't read this video's Claude use."), 500

    @app.get("/cr-workspace/recovery/<slug>")
    def cr_recovery(slug):
        try:
            job = find(slug)
            return jsonify(look(job))
        except Refused as error:
            return jsonify(error=str(error)), 404
        except Exception:                                      # noqa: BLE001
            return jsonify(error="Studio couldn't read this video's saved work."), 500

    @app.post("/cr-workspace/recovery/<slug>")
    def cr_recover_video(slug):
        """The creator's one yes: allow exactly the listed retries, then carry the video on (or as soon as Studio is
        free, when another video is being made)."""
        if not activity.lock.acquire(blocking=False):
            return jsonify(error="Finish the current studio request first."), 409
        try:
            data = request.get_json(silent=True) or {}
            free_only = data.get("only_if_free") is True       # a plain Resume: never approves anything
            if data.get("acknowledge_costs") is not True and not free_only:
                return jsonify(error="Read what trying again involves and accept it first."), 400
            job = find(slug)
            diag = look(job, sync=True)
            if diag["live"]:
                return jsonify(error="This video is being made now.", recovery=diag), 409
            if free_only and (diag["needs_approval"] or diag["fix_first"]):
                return jsonify(error=(" ".join(dict.fromkeys(diag["fix_first"])) if diag["fix_first"] else
                                      "This video stopped on a step that needs your yes to try again. Open it in "
                                      "Projects to see what that involves."), recovery=diag), 409
            when = "now" if free_only else str(data.get("when") or "now")
            if when == "after_reset":
                usage = [s for s in diag["claude_stages"] if s["needs_approval"]]
                if not usage or any(s["error_code"] != "usage_limit" for s in usage) \
                        or any(m.get("needs_approval") for m in diag["media"]):
                    return jsonify(error="Only a video Claude's usage limit stopped can wait for the reset.",
                                   recovery=diag), 409
                if data.get("key") != diag["key"]:
                    raise Refused("What stopped this video changed since you looked, or you didn't see it: check it "
                                  "again, then choose.", {"recovery": diag})
                _write(job / CARRY_ON, {"after_usage_reset": True, "at": time.time()})
                when_said = diag.get("reset_label") or "Claude's usage resets"
                return jsonify(scheduled=True, recovery=look(job),
                               message=f"Studio carries this video on by itself after {when_said}, reusing everything "
                                       "already made. Keep Studio open.")
            if (diag.get("reset_at") and not diag.get("reset_passed") and data.get("added_usage") is not True
                    and diag["needs_approval"]):
                return jsonify(error=f"Claude said your usage resets at {diag.get('reset_label')}. Trying before then "
                                     "uses up an attempt. Wait for it, or say you added usage.", recovery=diag), 409
            done = approve(bridge, job, diag, key=data.get("key")) if diag["needs_approval"] or diag["fix_first"] \
                else {"approved_stages": [], "set_aside": []}
            # while another video is made it joins the queue (cr_queue) and carries on in its turn
            busy = activity.busy()
            queued = resume(diag["project_id"])
            rows = queued.get("queued") or []
            place = rows[0] if rows and isinstance(rows[0], dict) else {}
            if place.get("held"):
                said = ("Approved. It waits on hold with the other videos (" + str(place["held"]).rstrip(".")
                        + "): Carry on in Projects starts them again. Everything already made is reused.")
            elif busy or place.get("ahead"):
                said = ("Approved. It joins the queue and carries on in its turn, reusing everything already made."
                        if done["approved_stages"] or done["set_aside"] else
                        "It joins the queue and carries on in its turn, reusing everything already made.")
            else:
                said = "Carrying on from where it stopped. Everything already made is reused."
            return jsonify(approved=done, resumed=True, waiting=bool(busy or place.get("ahead") or place.get("held")),
                           queued=rows, message=said)
        except Refused as error:
            return jsonify(error=str(error), **error.data), 409
        except Exception:                                      # noqa: BLE001
            return jsonify(error="Studio couldn't carry this video on. Nothing more was approved."), 500
        finally:
            activity.lock.release()

    @app.post("/cr-workspace/resume-first")
    def cr_resume_first():
        data = request.get_json(silent=True) or {}
        titles = data.get("titles") if isinstance(data.get("titles"), list) else str(data.get("title") or "").split("\n")
        try:
            matches = twins(studio, titles, data.get("style"), data.get("minutes"), extra=data.get("extra") or "")
        except Exception:                                      # noqa: BLE001 - never in the way of a new video
            matches = []
        return jsonify(matches=matches, others=[t for t in titles if str(t).strip()
                                                and title_key(t) not in {title_key(m["title"]) for m in matches}])

    watch_cancels(studio)
    watcher = Watcher(studio, activity, home, bridge, resume)
    studio._cr_recover = watcher
    if start_watcher:
        watcher.start()
    return watcher


def watch_cancels(studio) -> None:
    """A cancel the creator makes marks the video's folder (CANCELLED): Cancel while it is made or waits in the queue
    (cr_video_cancel), and Remove on the queue's hold (cr_queue.drop, also carry_on_queue(remove=true)). Wrapped
    here, so neither module changes; each wrapper runs the original first and never fails because of the mark."""
    def folder(slug):
        try:
            found = studio.mv.find_job(slug) if hasattr(studio.mv, "find_job") else None
            return Path(found) if found is not None else None
        except Exception:                                      # noqa: BLE001
            return None

    workers = getattr(studio, "_cr_video_workers", None)
    if workers is not None and not getattr(workers.cancel, "_cr_recover", False):
        original_cancel = workers.cancel

        def cancel(slug, run_id):
            result = original_cancel(slug, run_id)
            if isinstance(result, dict) and result.get("cancelled"):
                mark_cancelled(folder(slug), "cancelled")
            return result
        cancel._cr_recover = True
        workers.cancel = cancel
    keeper = getattr(studio, "_cr_queue", None)
    if keeper is not None and not getattr(keeper.drop, "_cr_recover", False):
        original_drop = keeper.drop

        def drop(*args, **kwargs):
            try:
                with keeper.lock:
                    slugs = [str(spec.get("slug") or "") for spec in list(keeper.held) if isinstance(spec, dict)]
            except Exception:                                  # noqa: BLE001
                slugs = []
            removed = original_drop(*args, **kwargs)
            for slug in slugs:
                if slug:
                    mark_cancelled(folder(slug), "removed from the queue")
            return removed
        drop._cr_recover = True
        keeper.drop = drop


class Watcher:
    """Carries a stopped video on when Studio is idle and safe_to_resume allows it. One a minute at most; a resume
    Studio refused waits RETRY_BACKOFF."""

    def __init__(self, studio, activity, home, bridge, resume, *, interval=60, first=20):
        self.studio, self.activity, self.home, self.bridge, self.resume = studio, activity, Path(home), bridge, resume
        self.interval, self.first = interval, first
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, daemon=True, name="cr-recover")
            self.thread.start()

    def _loop(self):
        if self.stopped.wait(self.first):
            return
        while True:
            try:
                self.tick()
            except Exception:                                  # noqa: BLE001 - never stops Studio
                pass
            if self.stopped.wait(self.interval):
                return

    def tick(self, now=None):
        """One look: returns (slug, why) for the video it carried on, else None."""
        try:
            if (self.activity.busy() or getattr(self.studio, "_cr_restart_for_update", False)
                    or queue_on_hold(self.studio)):
                return None                                    # the queue's holds start nothing unasked (cr_queue)
        except Exception:                                      # noqa: BLE001
            return None
        now = time.time() if now is None else now
        jobs = sorted(stopped_jobs(self.studio), key=lambda pair: -_mtime(pair[0] / "cr-project.json"))
        for job, saved in jobs:
            # this computer's own records first; ChannelRecipe is asked only about a video that passes them
            live = live_state(self.studio, job.name, saved["id"])
            if _auto_plan(job, home=self.home, live=live, now=now)["why"] != UNCHECKED:
                continue
            try:
                stages = self.bridge.project_progress(saved["id"]).get("stages") or []
            except Exception:                                  # noqa: BLE001 - asked again next minute
                continue
            allowance = {}
            if any(s.get("error_code") == "usage_limit" for s in stages if isinstance(s, dict)):
                try:
                    import cr_claude
                    allowance = cr_claude.status().get("allowance") or {}
                except Exception:                              # noqa: BLE001
                    allowance = {}
            plan = _auto_plan(job, home=self.home, server_stages=stages, allowance=allowance,
                              live=live, now=now)
            if not plan["safe"]:
                continue
            started = float(_read(job / RUN_FILE).get("started") or 0)
            if cancelled(job, self.home, since=started) or live_state(self.studio, job.name, saved["id"]):
                continue                                       # the last look before acting: cancelled, or on its way
            try:
                for sid in plan["approve"]:
                    # the creator chose this before the stop (CARRY_ON): one more attempt for a usage-limit refusal
                    self.bridge._mcp_tool("local_studio_retry_stage", {"project_id": saved["id"], "stage_id": sid,
                                                                       "acknowledge_costs": True})
                self.resume(saved["id"])
            except Exception as error:                         # noqa: BLE001
                note_auto(job, plan["why"], ok=False, error=error)
                return None
            note_auto(job, plan["why"])
            try:
                import cr_claude_code
                cr_claude_code.notify("Video carrying on", f"\"{job_title(job, saved) or job.name}\": {plan['why']}. "
                                                           "Studio carried it on by itself, reusing everything made.")
            except Exception:                                  # noqa: BLE001
                pass
            return job.name, plan["why"]
        return None
