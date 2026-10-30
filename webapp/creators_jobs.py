"""Hosted edits for channelrecipe.com/creators: the web server's whole part (phase 1, 2026-10-30).

The web server does no editing (owner rule: keep it fast). It signs uploads, creates a job row and serves its status:

- **Upload.** The creator's phone or browser PUTs the raw take (and any B-roll clips of their own) straight to our
  Spaces bucket with a link signed here for that one key and size. Files never pass through this server.
- **Job.** POST /jobs checks the take is theirs, the free-edit and phone gates, then inserts a `creator_jobs` row
  (webapp/creators_store.py) and starts one ephemeral Fly Machine for it (the cook pattern: webapp/fly_bridge.py).
  The machine runs `python -m webapp.creators_worker <job>`, which edits (creators.hosted.pipeline) and writes its
  stages, the Reel's key and its cost into the row.
- **Status.** GET /jobs/{id} reads the row; a job whose machine stopped answering is failed here (and its free edit
  given back).

Switches (DigitalOcean env):
- CREATORS_HOSTED=1 turns it on; CREATORS_PUBLIC=1 opens it to every signed-in account (until then admins only).
- FLY_CREATORS_IMAGE: the worker image (Dockerfile.creators); CREATORS_FLY_CPUS / CREATORS_FLY_MEMORY_MB size it.
- CREATORS_KIE_KEY: Kie, for the Suno bed. CREATORS_DAILY_MAX: free edits per day across everyone (a spend cap);
  CREATORS_MAX_RUNNING: edits at once across everyone (machines, and the database's ~22 shared connections);
  past it a job waits in line and the dispatcher (every 15 s, on each web container) starts it when a slot frees.
- CREATORS_PHONE_REQUIRED=1: a verified phone before the first free edit (the provider isn't wired yet).
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import quote

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from webapp.creators_spec import LOOK_NAMES, MAX_ROUNDS, MAX_SECONDS
from webapp import creators_store as store

FREE_EDITS = 2
MAX_TAKE_BYTES = 2 * 1024 ** 3            # 5 min of 4K phone video is ~1.9 GB
MAX_CLIP_BYTES = 500 * 1024 ** 2
MAX_CLIPS = 10
REEL_LINK = re.compile(r"https?://((www|m|vm|vt)\.)?(tiktok\.com/|instagram\.com/([A-Za-z0-9._]{1,30}/)?(reel|reels|p)/"
                       r"|youtube\.com/(shorts/|watch\?)"
                       r"|youtu\.be/)\S+$")              # what creators.learn.fetch (yt-dlp) can open
# The creator's own profile, for B-roll from their latest posts (owner's Loom 2026-10-30, P1-23 / P2-01). TikTok
# profiles list and download from our Fly machines with yt-dlp; Instagram answers them 429 / "require_login" (tested
# from sjc, 2026-10-30), so Instagram profiles go through Scrape Creators (SCRAPECREATORS_API_KEY, the owner's key:
# listed in 14 s, files fetched on the machine) and are refused plainly when that key isn't set.
TIKTOK_PROFILE = re.compile(r"(?:https?://)?(?:(?:www|m)\.)?tiktok\.com/@([A-Za-z0-9._]{2,40})/?(?:\?\S*)?$", re.I)
INSTAGRAM_PROFILE = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/(?!reels?/|p/|tv/|stories/)([A-Za-z0-9._]{1,30})/?"
                               r"(?:\?\S*)?$", re.I)
UPLOAD_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/x-m4v": ".m4v", "video/webm": ".webm"}
LINK_SECONDS = 7 * 86400                  # the Reel's download link
# A review round's files (creators/web/REVIEW.md): frames and drawings as PNG, voice notes as webm/m4a.
NOTE_FILE = re.compile(r"notes/[A-Za-z0-9_-]{1,80}(_frame|_drawing)?\.(png|webm|m4a|mp4|ogg|wav)")
MAX_NOTE_FILE = 20 * 1024 ** 2
MAX_NOTES_BYTES = 80 * 1024 ** 2
MAX_NOTES = 40
STALE_S = 10 * 60                         # a running machine that hasn't written for this long has stopped
START_S = 6 * 60                          # a started machine that hasn't taken its job in this long never will
LINE_S = 40 * 60                          # the longest a job waits in line for a machine
DISPATCH_S = 15                           # how often the web server starts waiting jobs and sweeps stalled ones
MAX_EDIT_S = 45 * 60                      # the machine's own limit (the worker stops itself)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def hosted_on() -> bool:
    return _env("CREATORS_HOSTED") == "1"


def is_admin(user: dict | None) -> bool:
    from webapp.frontier_studio import is_admin as _is_admin
    return _is_admin(user)


def allowed(user: dict | None) -> bool:
    return bool(user) and (_env("CREATORS_PUBLIC") == "1" or is_admin(user))


def phone_required() -> bool:
    return _env("CREATORS_PHONE_REQUIRED") == "1"


def prefix(user_id: int) -> str:
    return f"{_env('CREATORS_PREFIX', 'creators')}/u{int(user_id)}/"


def _storage():
    from webapp import storage
    if not storage.is_remote():
        raise HTTPException(503, "Storage isn't configured.")
    _k, _s, bucket, _r, _e = storage._spaces_creds()
    return storage._get_client(), bucket


def signed_get(key: str, seconds: int = LINK_SECONDS, filename: str = "") -> str:
    client, bucket = _storage()
    params = {"Bucket": bucket, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
    return client.generate_presigned_url("get_object", Params=params, ExpiresIn=int(seconds))


def _head(key: str) -> dict | None:
    client, bucket = _storage()
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception:                               # noqa: BLE001 - botocore raises a ClientError for a 404
        return None


def _own_key(user_id: int, key: str) -> str:
    key = str(key or "")
    if not key.startswith(prefix(user_id)) or ".." in key.split("/") or "\\" in key or len(key) > 400:
        raise HTTPException(400, "That upload isn't yours.")
    return key


# ── the job, as the app sees it ───────────────────────────────────────────────────────────────────────────────

def download_name(j: dict) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", (j.get("take_name") or "reel").rsplit(".", 1)[0])[:60] or "reel"
    return f"{name}-{j['look']}.mp4"


def view(j: dict) -> dict:
    result = None
    if j["status"] == "done" and j.get("output_key"):
        r = j.get("result") or {}
        result = {"video_url": signed_get(j["output_key"]),
                  "download_url": signed_get(j["output_key"], filename=download_name(j)),
                  "poster_url": signed_get(j["poster_key"]) if j.get("poster_key") else "",
                  "take_url": signed_get(j["take_key"], 6 * 3600),
                  "seconds": r.get("seconds"), "review": r.get("review"), "summary": r.get("summary") or {},
                  "changes": r.get("changes"), "rendered": r.get("rendered", True)}
    style = learned_style(j)
    progress = 1.0 if j["status"] == "done" else float(j.get("progress") or 0)
    if j["status"] in LIVE and j.get("started_at"):                 # an active stage fills with its usual time
        progress = max(progress, store.progress_of(j.get("stages") or [], time.time()))
    return {"id": j["id"], "look": j["look"], "look_name": style.get("name") or LOOK_NAMES.get(j["look"], j["look"]),
            "style_name": style.get("name") or "", "style_error": style.get("error") or "",
            "refs": j.get("refs") or [], "profile": j.get("profile") or "", "status": j["status"],
            "expected_s": expected_s(j, typical_minutes()),
            "stage": j["stage"], "stages": j["stages"], "progress": round(progress, 3),
            "take_name": j.get("take_name") or "", "take_key": j.get("take_key") or "",    # theirs: "Try another look"
            "free": bool(j.get("free")), "result": result,
            "kind": j.get("kind") or "edit", "parent": j.get("parent") or "",
            "in_line": store.ahead_of(j) + 1 if j["status"] == "queued" and not j.get("machine_id") else 0,
            "error": j.get("error") or "", "created_at": j["created_at"], "started_at": j.get("started_at"),
            "ended_at": j.get("ended_at")}


def learned_style(j: dict) -> dict:
    """The style learned from the job's pasted Reels: in the finished summary, or recorded by the worker as soon as it
    learned it (store.set_style), so the editing screen names it while the edit runs."""
    r = j.get("result") or {}
    return (r.get("summary") or {}).get("style") or r.get("style") or {}


def check_alive(j: dict) -> dict:
    """Fail a job whose machine never started or went quiet, and stop the machine. Free edits come back with it
    (store.free_used doesn't count failed jobs)."""
    if j["status"] in store.TERMINAL:
        return j
    now = time.time()
    quiet = now - float(j.get("heartbeat_at") or j["created_at"])
    in_line = j["status"] == "queued" and not j.get("machine_id")
    if in_line and time.time() - float(j["created_at"]) > LINE_S:
        reason = "We were too busy to start this one. Nothing was charged: try again."
    elif not in_line and j["status"] in ("queued", "starting") and quiet > START_S:
        reason = "No edit machine picked this up. Nothing was charged: try again."
    elif j["status"] == "running" and (quiet > STALE_S or now - float(j.get("started_at") or now) > MAX_EDIT_S + 300):
        reason = "The edit machine stopped answering. Nothing was charged: try again."
    else:
        return j
    store.finish(j["id"], "failed", error=reason)
    if j.get("machine_id") and j["machine_id"] != "pending" and not j["machine_id"].startswith("local-"):
        stop_machine(j["machine_id"])
    return store.get(j["id"])


# ── projects: the creators home (big-frontier/dashboard-research.md) ─────────────────────────────────────────────
# One take = one project = one card. Its first edit and every notes round after it (the `root` chain) are the
# project's versions v1, v2, v3...; a round that failed or was cancelled gets no number.

LIVE = ("queued", "starting", "running")


def typical_minutes() -> int:
    return int(_env("CREATORS_TYPICAL_MINUTES", "7") or 7)


STYLE_S = 90                              # learning a pasted Reel's style first (Fly: 1-3 min; cached ones are instant)
# The stages' weights don't track time early on (music and B-roll finish fast, the render is half the wait), so an
# early pace promises too little time; it's trusted only from here (the render well under way).
PACE_FROM = 0.6


def expected_s(j: dict, typical: int) -> float:
    """How long this edit usually takes on our machines: the typical edit, plus learning a style when it has Reel
    links whose style isn't learned yet."""
    learning = bool(j.get("refs")) and (j.get("kind") or "edit") == "edit" and not learned_style(j).get("name")
    return typical * 60 + (STYLE_S if learning else 0)


def eta(j: dict, typical: int) -> tuple[int | None, str]:
    """(minutes left, note) for a running edit, as the editing screen and the home say it. The usual time left,
    unless the job's own pace (late in the edit) says it's ahead; past the usual time it never adds minutes back:
    "almost" (done soon),
    then "late" (taking a little longer than usual). The page also holds the lowest minutes it showed."""
    if j["status"] not in LIVE:
        return None, ""
    total = expected_s(j, typical)
    if not j.get("started_at"):
        return max(1, round(total / 60)), ""
    elapsed = max(0.0, time.time() - float(j["started_at"]))
    p = max(float(j.get("progress") or 0), store.progress_of(j.get("stages") or [], time.time()))
    left = total - elapsed
    if p >= PACE_FROM:                        # the job's own pace, once the render is under way and it's ahead
        left = min(left, elapsed / p - elapsed)
    if left > 50:
        return max(1, round(left / 60)), ""
    return None, "almost" if elapsed < total * 1.25 else "late"


def minutes_left(j: dict, typical: int) -> int | None:
    return eta(j, typical)[0]


def _stem(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").rsplit(".", 1)[0].replace("_", " ")).strip()


def _ref_style(refs: list) -> str:
    """While an edit learns pasted Reels' style: whose ("@handle's style" from a TikTok link or a profile)."""
    prof = next((m for r in refs if isinstance(r, str) and (m := INSTAGRAM_PROFILE.match(r))), None)
    if prof:
        return f"@{prof.group(1)}'s style"
    m = next((re.search(r"/@([A-Za-z0-9._]{2,40})", r) for r in refs if isinstance(r, str) and "/@" in r), None)
    return f"@{m.group(1)}'s style" if m else "A pasted Reel's style" if refs else ""


def project_view(chain: list[dict], typical: int) -> dict:
    """A project as the home shows it. `chain` is its jobs in any order (check_alive already applied)."""
    chain = sorted(chain, key=lambda j: j["created_at"])
    first = next((j for j in chain if not j.get("parent")), chain[0])
    root = first.get("root") or first["id"]
    latest = chain[-1]
    kept = [j for j in chain if j["status"] not in ("failed", "cancelled")]
    number = {j["id"]: k + 1 for k, j in enumerate(kept)}
    done = [j for j in kept if j["status"] == "done" and j.get("output_key")]
    shown = done[-1] if done else None
    summary = ((shown or {}).get("result") or {}).get("summary") or {}
    title = (first.get("title") or "").strip() or summary.get("title") or summary.get("hook") \
        or _stem(first.get("take_name") or "") or "Your Reel"
    learned = next((s for s in (((j.get("result") or {}).get("summary") or {}).get("style") for j in reversed(done))
                    if s and s.get("name")), {})                 # a style learned from Reel links (view() says it too)
    look_name = learned.get("name") or (not done and (learned_style(latest).get("name")
                                                      or _ref_style(latest.get("refs") or []))) \
        or LOOK_NAMES.get(latest["look"], latest["look"])
    live = latest["status"] in LIVE
    in_line = store.ahead_of(latest) + 1 if latest["status"] == "queued" and not latest.get("machine_id") else 0
    if live:
        state = "making" if latest.get("kind") == "notes" else "queued" if in_line else "editing"
    else:
        state = "ready" if latest["status"] == "done" else "failed"
    version = number.get(latest["id"]) or len(kept) + 1          # what the state talks about ("Making v3")
    out = {"id": root, "title": title, "renamed": bool((first.get("title") or "").strip()),
           "look": latest["look"], "look_name": look_name,
           "state": state, "status": latest["status"], "kind": latest.get("kind") or "edit",
           "progress": 1.0 if latest["status"] == "done" else float(latest.get("progress") or 0),
           "in_line": in_line, "minutes_left": minutes_left(latest, typical), "eta_note": eta(latest, typical)[1],
           "notify": bool(latest.get("notify")),
           "error": (latest.get("error") or "") if state == "failed" else "",
           "version": version, "ready_version": number[shown["id"]] if shown else 0,
           "versions": [{"id": j["id"], "n": number[j["id"]], "status": j["status"], "kind": j.get("kind") or "edit",
                         "created_at": j["created_at"]} for j in kept],
           "latest_id": latest["id"], "open_id": shown["id"] if shown else latest["id"],
           "poster_url": "", "download_url": "", "take_url": "", "seconds": None,
           "created_at": first["created_at"],
           "updated_at": max(float(j.get("ended_at") or j.get("started_at") or j["created_at"]) for j in chain)}
    if shown:
        out.update(poster_url=signed_get(shown["poster_key"]) if shown.get("poster_key") else "",
                   download_url=signed_get(shown["output_key"], filename=download_name(shown)),
                   seconds=(shown.get("result") or {}).get("seconds"))
    elif live and latest.get("take_key"):                      # still on its first edit: the raw take is the thumb
        out["take_url"] = signed_get(latest["take_key"], 6 * 3600)
    return out


def projects_for(user_id: int) -> list[dict]:
    """The creator's projects, most recently active first: one query, grouped here by `root`."""
    groups: dict[str, list[dict]] = {}
    for j in store.project_rows(user_id):
        if j["status"] in LIVE:
            j = check_alive(j)
        groups.setdefault(j.get("root") or j["id"], []).append(j)
    typical = typical_minutes()
    projects = [project_view(chain, typical) for chain in groups.values()]
    return sorted(projects, key=lambda p: p["updated_at"], reverse=True)


def max_running() -> int:
    return max(1, int(_env("CREATORS_MAX_RUNNING", "4") or 4))


def launch(job_id: str) -> None:
    """Give a claimed job its machine (or fail it plainly)."""
    try:
        mid = start_local(job_id) if _env("CREATORS_RUN_LOCAL") == "1" else spawn(job_id)
        store.update(job_id, machine_id=mid, status="starting", heartbeat_at=time.time())
    except Exception as e:                           # noqa: BLE001 - no machine: say so; the free edit comes back
        print(f"[creators] no machine for {job_id}: {e}")
        store.finish(job_id, "failed", error="We couldn't start an edit machine. Nothing was charged: try again "
                                             "in a minute.")


def dispatch_once() -> int:
    """Start waiting jobs while there are free slots, and fail stalled ones nobody is polling. Returns how many
    jobs got a machine."""
    store.initialize()
    for j in store.stale(STALE_S):
        check_alive(j)
    started = 0
    free = max_running() - store.running_count()
    for j in store.waiting(max(0, free)):
        if store.take_slot(j["id"]):
            launch(j["id"])
            started += 1
    return started


_dispatcher: list = []


def _dispatch_loop():
    while True:
        time.sleep(DISPATCH_S)
        try:
            if hosted_on():
                dispatch_once()
        except Exception as e:                       # noqa: BLE001 - a sweep must never take the server down
            print(f"[creators] dispatch: {type(e).__name__}: {e}")


# ── the machines ──────────────────────────────────────────────────────────────────────────────────────────────

INJECT = ("DATABASE_URL", "SPACES_KEY", "SPACES_SECRET", "SPACES_BUCKET", "SPACES_REGION", "SPACES_ENDPOINT",
          "SPACES_CDN_ENDPOINT", "GEMINI_API_KEY", "GEMINI_STYLE_MODEL", "RESEND_KEY", "FROM_EMAIL", "SENTRY_DSN",
          "CREATORS_PREFIX", "CR_PUBLIC_URL", "ADMIN_EMAILS", "CREATORS_RENDER_WORKERS", "CREATORS_FLY_CPU_KIND",
          "CREATORS_FLY_CPUS", "CREATORS_FLY_MEMORY_MB",
          "SCRAPECREATORS_API_KEY", "SCRAPECREATORS_CREDIT_USD")   # Instagram profiles' posts (creators.hosted.profile)


def machine_env(job_id: str) -> dict[str, str]:
    """What one edit machine needs, copied from this server's environment: the job's row, storage, and the paid
    services on our keys (Claude, Gemini, Kie). Nothing else."""
    from webapp.fly_bridge import MACHINE_DB_POOL_MAX
    env = {k: v for k in INJECT if (v := _env(k))}
    env["ANTHROPIC_API_KEY"] = _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_KEY")
    env["KIE_API_KEY"] = _env("CREATORS_KIE_KEY") or _env("KIE_API_KEY")
    # stock B-roll for the creators-broll finder (free APIs; the site already has a Pexels key)
    env["PEXELS_API_KEY"] = _env("PEXELS_API_KEY") or _env("PEXELS_KEY")
    env["PIXABAY_API_KEY"] = _env("PIXABAY_API_KEY") or _env("PIXABAY_KEY")
    env.update({"CREATORS_CLAUDE": "api", "DB_POOL_MAX": MACHINE_DB_POOL_MAX, "APP_ENV": "fly-creators",
                "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required",
                "AWS_RESPONSE_CHECKSUM_VALIDATION": "when_required", "PYTHONUNBUFFERED": "1",
                "CREATORS_MAX_EDIT_S": str(MAX_EDIT_S),
                # no GPU on the machine: treatments are baked with ffmpeg, not drawn per frame (creators/hosted/bake.py)
                "CREATORS_BAKE_TREATMENTS": "1", "CREATORS_THREADS": _env("CREATORS_FLY_CPUS", "8")})
    return {k: v for k, v in env.items() if v}


def spawn(job_id: str) -> str:
    """One ephemeral Fly Machine for this job: it edits, then exits and is destroyed. Returns its id, or raises."""
    from webapp import fly_bridge
    app = _env("FLY_CREATORS_APP") or _env("FLY_COOK_APP", "channelrecipe-cook")
    image = _env("FLY_CREATORS_IMAGE")
    if not image:
        raise RuntimeError("FLY_CREATORS_IMAGE isn't set")
    env = machine_env(job_id)
    try:                                            # the ready email's unsubscribe link, signed here (lifecycle_email)
        from webapp import lifecycle_email
        row = store.get(job_id)
        if row and lifecycle_email._link_key():
            env["CREATORS_UNSUB_URL"] = lifecycle_email.unsubscribe_url(int(row["user_id"]))
    except Exception as e:                          # noqa: BLE001 - no link, no email; the edit still runs
        print(f"[creators] unsubscribe link for {job_id}: {type(e).__name__}")
    for need in ("DATABASE_URL", "SPACES_KEY", "SPACES_SECRET", "SPACES_BUCKET", "ANTHROPIC_API_KEY"):
        if not env.get(need):
            raise RuntimeError(f"{need} missing on the web server")
    body = {"region": _env("FLY_CREATORS_REGION") or _env("FLY_COOK_REGION", "sjc"),
            "config": {"image": image, "auto_destroy": True, "restart": {"policy": "no"},
                       "guest": {"cpu_kind": _env("CREATORS_FLY_CPU_KIND", "performance"),
                                 "cpus": int(_env("CREATORS_FLY_CPUS", "8")),
                                 "memory_mb": int(_env("CREATORS_FLY_MEMORY_MB", "16384"))},
                       "env": env, "metadata": {"creator_job": job_id},
                       "init": {"cmd": ["python", "-m", "webapp.creators_worker", job_id]}}}
    try:
        created = fly_bridge._request("POST", f"/v1/apps/{app}/machines", body)
    except RuntimeError as e:                       # one retry: the registry can lag a fresh image, Fly can blip
        if "→ 4" in str(e) and "manifest" not in str(e):
            raise
        time.sleep(5)
        created = fly_bridge._request("POST", f"/v1/apps/{app}/machines", body)
    mid = str((created or {}).get("id") or "")
    if not mid:
        raise RuntimeError("Fly returned no machine id")
    try:
        fly_bridge._request("POST", f"/v1/apps/{app}/machines/{mid}/start", {})
    except Exception as e:                          # noqa: BLE001 - usually already starting
        print(f"[creators] start after create ({mid}): {e}")
    return mid


def stop_machine(machine_id: str) -> None:
    from webapp import fly_bridge
    app = _env("FLY_CREATORS_APP") or _env("FLY_COOK_APP", "channelrecipe-cook")
    try:
        fly_bridge._request("DELETE", f"/v1/apps/{app}/machines/{machine_id}?force=true")
    except Exception as e:                          # noqa: BLE001 - an auto-destroyed machine is already gone
        print(f"[creators] stop {machine_id}: {e}")


def start_local(job_id: str) -> str:
    """Development only (CREATORS_RUN_LOCAL=1): the worker as a child of this server, never in production."""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    log = open(root / "output" / f"{job_id}.log", "w") if (root / "output").is_dir() else subprocess.DEVNULL
    proc = subprocess.Popen([sys.executable, "-m", "webapp.creators_worker", job_id], cwd=str(root), stdout=log,
                            stderr=subprocess.STDOUT, env=dict(os.environ, **machine_env(job_id)))
    return f"local-{proc.pid}"


# ── requests ──────────────────────────────────────────────────────────────────────────────────────────────────

class UploadRequest(BaseModel):
    kind: str = Field(pattern="^(take|broll)$")
    name: str = Field(max_length=200)
    size: int = Field(gt=0)
    type: str = Field(default="video/mp4", max_length=60)


class JobRequest(BaseModel):
    take_key: str = Field(max_length=400)
    take_name: str = Field(default="", max_length=200)
    look: str = Field(max_length=40)
    broll_keys: list[str] = Field(default_factory=list, max_length=MAX_CLIPS)
    refs: list[str] = Field(default_factory=list, max_length=3)      # Reel links: the edit learns their style first
    profile: str = Field(default="", max_length=300)                  # their TikTok profile, for B-roll from their posts
    email: bool = True


class PhoneRequest(BaseModel):
    phone: str = Field(max_length=32)
    code: str = Field(default="", max_length=12)


class NotifyRequest(BaseModel):
    on: bool = True


class RenameRequest(BaseModel):
    title: str = Field(default="", max_length=200)


def scrape_key() -> str:
    return _env("SCRAPECREATORS_API_KEY")


def profile_kinds() -> list[str]:
    """The profile links our edit machines can open for B-roll."""
    return ["tiktok", "instagram"] if scrape_key() else ["tiktok"]


def ref_link(raw: str) -> str:
    """A link whose style an edit learns: a Reel, a TikTok or a Short as pasted, or a creator's profile (their most
    watched recent Reels are learned, creators.hosted.profile.top) in the form the edit machine opens."""
    raw = (raw or "").strip()
    if len(raw) <= 300 and REEL_LINK.match(raw):
        return raw
    if len(raw) <= 300 and (TIKTOK_PROFILE.match(raw) or INSTAGRAM_PROFILE.match(raw)):
        return profile_link(raw)
    raise HTTPException(400, "That isn't a link we can learn from. Paste a TikTok, a Reel or a Short, or the creator's "
                             "TikTok or Instagram profile.")


def profile_link(raw: str) -> str:
    """The creator's profile link as the edit machine opens it ("" for none). Raises a 400 the app shows as it is."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    m = TIKTOK_PROFILE.match(raw)
    if m:
        return f"https://www.tiktok.com/@{m.group(1)}"
    m = INSTAGRAM_PROFILE.match(raw)
    if m and "instagram" in profile_kinds():
        return f"https://www.instagram.com/{m.group(1)}"
    if m:
        raise HTTPException(400, "Instagram profiles are coming soon: Instagram doesn't let our edit machines open "
                                 "them yet. Paste your TikTok profile, or add your own clips.")
    raise HTTPException(400, "That isn't a TikTok or Instagram profile link. It looks like tiktok.com/@yourname or "
                             "instagram.com/yourname." if scrape_key() else
                        "That isn't a TikTok profile link. It looks like tiktok.com/@yourname.")


# ── a pasted Reel's preview: its picture and whose it is (owner's Loom 2026-10-30, P1-21 / P1-22) ────────────────
# One short request to the platform (3 s at most), never yt-dlp or a download on this server. TikTok's oEmbed no
# longer carries the cover or the author (2026-10-30), so TikTok and Instagram are read from the Reel's own page;
# YouTube from its oEmbed. The picture comes back small and inline, so the page never hotlinks a signed CDN URL that
# expires or refuses other sites. Anything that fails gives the plain card ("thumb": "").

PREVIEW_TIMEOUT = (2.0, 3.0)
PREVIEW_IMG_HOSTS = ("tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "ibyteimg.com", "ytimg.com",
                     "cdninstagram.com", "fbcdn.net")
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 "
              "Safari/537.36")
SHARE_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
_previews: dict[str, tuple[float, dict]] = {}      # a small per-process cache; nothing relies on it (2 web instances)


def _meta(html: str, name: str) -> str:
    import html as H
    m = re.search(r'<meta[^>]+(?:property|name)="' + re.escape(name) + r'"[^>]+content="([^"]*)"', html) or \
        re.search(r'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="' + re.escape(name) + r'"', html)
    return H.unescape(m.group(1)).strip() if m else ""


def _thumb(url: str) -> str:
    """The picture as a small portrait JPEG data URL (a landscape card is cropped to its middle), or ""."""
    import base64
    import io
    from urllib.parse import urlparse

    import requests
    from PIL import Image

    host = (urlparse(url).hostname or "").lower()
    if not url.startswith("https://") or not any(host == h or host.endswith("." + h) for h in PREVIEW_IMG_HOSTS):
        return ""
    r = requests.get(url, timeout=PREVIEW_TIMEOUT, headers={"User-Agent": BROWSER_UA})
    data = r.content if r.ok and r.headers.get("Content-Type", "image/").startswith("image/") else b""
    if not data or len(data) > 3 * 1024 ** 2:
        return ""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if w > h * 0.8:                                       # landscape or square: the middle, 3:4
        cw = int(h * 0.75)
        im = im.crop(((w - cw) // 2, 0, (w - cw) // 2 + cw, h))
    im.thumbnail((240, 320))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


SCRAPE_CALLS = [0]                                # Scrape Creators calls this process made (a credit each)


def _scrape_preview(kind: str, url: str, out: dict) -> list[str]:
    """Fill the author, handle and title from Scrape Creators when the Reel's own page gave nothing (Instagram rate-
    limits datacenter addresses). Returns candidate pictures. Only on a miss, so it costs few credits."""
    import requests
    path = "/v1/instagram/post" if kind == "instagram" else "/v2/tiktok/video"
    try:
        SCRAPE_CALLS[0] += 1
        r = requests.get("https://api.scrapecreators.com" + path, params={"url": url}, timeout=(2.0, 6.0),
                         headers={"x-api-key": scrape_key()})
        d = r.json() if r.status_code == 200 else {}
    except Exception as e:                                # noqa: BLE001
        print(f"[creators] reel preview via Scrape Creators ({kind}): {type(e).__name__}")
        return []
    if kind == "instagram":
        m = ((d.get("data") or {}).get("xdt_shortcode_media")) or {}
        owner = m.get("owner") or {}
        cap = (((m.get("edge_media_to_caption") or {}).get("edges") or [{}])[0].get("node") or {}).get("text") or ""
        out.update(author=out["author"] or str(owner.get("full_name") or owner.get("username") or ""),
                   handle=out["handle"] or str(owner.get("username") or ""), title=out["title"] or str(cap))
        return [x for x in (m.get("thumbnail_src"), m.get("display_url")) if x]
    a = d.get("aweme_detail") or {}
    au, v = a.get("author") or {}, a.get("video") or {}
    out.update(author=out["author"] or str(au.get("nickname") or ""), handle=out["handle"] or str(au.get("unique_id") or ""),
               title=out["title"] or str(a.get("desc") or ""))
    return [u for f in ("cover", "origin_cover") for u in ((v.get(f) or {}).get("url_list") or [])][:4]


def reel_preview(url: str) -> dict:
    """{"url", "platform", "author", "handle", "title", "thumb"} for a pasted Reel link; empty fields when the
    platform didn't say."""
    import json as J

    import requests

    url = (url or "").strip()
    hit = _previews.get(url)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    kind = "tiktok" if "tiktok.com" in url else "instagram" if "instagram.com" in url else "youtube"
    out = {"url": url, "platform": {"tiktok": "TikTok", "instagram": "Instagram Reel"}.get(kind, "YouTube Short"),
           "author": "", "handle": "", "title": "", "thumb": ""}
    pic = ""
    try:
        if kind == "youtube":
            vid = re.search(r"(?:shorts/|v=|youtu\.be/)([A-Za-z0-9_-]{6,20})", url)
            if vid:
                r = requests.get("https://www.youtube.com/oembed", timeout=PREVIEW_TIMEOUT,
                                 params={"url": f"https://www.youtube.com/watch?v={vid.group(1)}", "format": "json"})
                d = r.json() if r.ok else {}
                out.update(author=str(d.get("author_name") or ""), title=str(d.get("title") or ""))
                h = re.search(r"/@([A-Za-z0-9._-]+)", str(d.get("author_url") or ""))
                out["handle"] = h.group(1) if h else ""
                pic = str(d.get("thumbnail_url") or "")
        elif kind == "tiktok":
            r = requests.get(url, timeout=PREVIEW_TIMEOUT, headers={"User-Agent": BROWSER_UA})
            m = re.search(r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', r.text, re.S) \
                if r.ok else None
            item = {}
            if m:
                scope = J.loads(m.group(1)).get("__DEFAULT_SCOPE__") or {}
                item = ((scope.get("webapp.video-detail") or {}).get("itemInfo") or {}).get("itemStruct") or {}
            if item:
                a, v = item.get("author") or {}, item.get("video") or {}
                out.update(author=str(a.get("nickname") or ""), handle=str(a.get("uniqueId") or ""),
                           title=str(item.get("desc") or ""))
                pic = str(v.get("cover") or v.get("originCover") or "")
            else:                                         # the share card a link preview gets
                r = requests.get(url, timeout=PREVIEW_TIMEOUT, headers={"User-Agent": SHARE_UA})
                page = r.text if r.ok else ""
                out["author"] = _meta(page, "og:title").replace("TikTok · ", "").replace("TikTok", "").strip(" ·")
                pic = _meta(page, "og:image")
            h = re.search(r"/@([A-Za-z0-9._]+)", url)
            out["handle"] = out["handle"] or (h.group(1) if h else "")
        else:
            code = re.search(r"instagram\.com/(?:[A-Za-z0-9._]+/)?(?:reel|reels|p)/([A-Za-z0-9_-]{5,40})", url)
            if code:
                r = requests.get(f"https://www.instagram.com/reel/{code.group(1)}/", timeout=PREVIEW_TIMEOUT,
                                 headers={"User-Agent": SHARE_UA})
                page = r.text if r.ok else ""
                title = _meta(page, "og:title")                    # 'Dylan Mouzakki on Instagram: "…"'
                out["author"] = title.split(" on Instagram", 1)[0].strip() if " on Instagram" in title else ""
                h = re.search(r"instagram\.com/([A-Za-z0-9._]+)/(?:reel|p)/", _meta(page, "og:url")) or \
                    re.search(r" - ([A-Za-z0-9._]+) on ", _meta(page, "og:description"))
                out["handle"] = h.group(1) if h else ""
                cap = re.search(r':\s*"(.*)', title, re.S)
                out["title"] = cap.group(1).rstrip('"').strip() if cap else ""
                pic = _meta(page, "og:image")
    except Exception as e:                                # noqa: BLE001 - a preview is a nicety: the plain card
        print(f"[creators] reel preview ({kind}): {type(e).__name__}")
    pics = [pic] if pic else []
    if kind != "youtube" and not (out["author"] and pic) and scrape_key():
        pics += _scrape_preview(kind, url, out)       # the platform turned us away: Scrape Creators (a credit)
    for candidate in pics:
        try:
            out["thumb"] = _thumb(candidate)
        except Exception as e:                            # noqa: BLE001 - a format we can't read (HEIC): the next
            print(f"[creators] reel preview picture ({kind}): {type(e).__name__}")
        if out["thumb"]:
            break
    out["title"] = re.sub(r"\s+", " ", out["title"]).strip()[:120]
    out["author"], out["handle"] = out["author"][:60], out["handle"][:40]
    if out["author"] or out["thumb"]:
        if len(_previews) > 500:
            _previews.clear()
        _previews[url] = (time.time(), out)
    return out


def make_router(current_user) -> APIRouter:
    router = APIRouter(prefix="/api/creators")
    if not _dispatcher and _env("CREATORS_DISPATCH", "1") == "1":
        import threading
        _dispatcher.append(threading.Thread(target=_dispatch_loop, daemon=True, name="creators-dispatch"))
        _dispatcher[0].start()

    def need_user(user=Depends(current_user)) -> dict:
        if not user:
            raise HTTPException(401, "Sign in to continue.")
        if not hosted_on():
            raise HTTPException(503, "Editing isn't switched on yet.")
        if not allowed(user):
            raise HTTPException(403, "Editing opens soon. We'll email you when it does.")
        return user

    def free_left(user: dict) -> int | None:
        return None if is_admin(user) else max(0, FREE_EDITS - store.free_used(user["id"]))

    @router.get("/config")
    def get_config():
        return {"hosted": hosted_on(), "google_client_id": _env("GOOGLE_CLIENT_ID"), "max_seconds": MAX_SECONDS,
                "looks": [{"key": k, "name": v} for k, v in LOOK_NAMES.items()], "free_edits": FREE_EDITS,
                "phone_required": phone_required(), "max_clips": MAX_CLIPS, "profiles": profile_kinds(),
                # what the app promises at "Start": measured on the edit machines (handoffs/creators-hosted.md)
                "typical_minutes": typical_minutes()}

    @router.get("/me")
    def get_me(user=Depends(current_user)):
        if not user:
            return {"user": None}
        out = {"user": {"email": user.get("email"), "admin": is_admin(user)}, "allowed": allowed(user) and hosted_on()}
        if out["allowed"]:
            store.initialize()
            out.update(free_left=free_left(user), phone_verified=store.phone_verified(user["id"]),
                       jobs=[view(check_alive(j)) for j in store.list_for(user["id"], 10)])
        return out

    @router.post("/uploads")
    def post_upload(req: UploadRequest, user=Depends(need_user)):
        limit = MAX_TAKE_BYTES if req.kind == "take" else MAX_CLIP_BYTES
        if req.size > limit:
            raise HTTPException(413, "That file is too big. Takes up to 2 GB (5 minutes), clips up to 500 MB.")
        ctype = req.type if req.type in UPLOAD_TYPES else "video/mp4"
        ext = UPLOAD_TYPES.get(ctype, ".mp4")
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", req.name.rsplit(".", 1)[0]).strip("-")[:60] or req.kind
        key = f"{prefix(user['id'])}up/{int(time.time())}-{os.urandom(4).hex()}/{req.kind}-{stem}{ext}"
        client, bucket = _storage()
        url = client.generate_presigned_url("put_object", ExpiresIn=3 * 3600, Params={
            "Bucket": bucket, "Key": key, "ContentType": ctype, "ContentLength": int(req.size)})
        return {"key": key, "url": url, "method": "PUT", "headers": {"Content-Type": ctype}}

    @router.post("/jobs")
    def post_job(req: JobRequest, user=Depends(need_user)):
        if req.look not in LOOK_NAMES:
            raise HTTPException(400, "Pick one of the looks.")
        refs = [ref_link(r) for r in req.refs if r.strip()]
        profile = profile_link(req.profile)
        take = _own_key(user["id"], req.take_key)
        clips = [_own_key(user["id"], k) for k in req.broll_keys]
        head = _head(take)
        if not head:
            raise HTTPException(400, "We couldn't find your upload. Try uploading it again.")
        if int(head.get("ContentLength") or 0) > MAX_TAKE_BYTES:
            raise HTTPException(413, "That take is too big.")
        store.initialize()
        if store.running_for(user["id"]):
            raise HTTPException(409, "One edit at a time: your last one is still going.")
        admin = is_admin(user)
        left = free_left(user)
        if not admin:
            if left is not None and left <= 0:
                raise HTTPException(402, "You've used your 2 free edits.")
            if phone_required() and not store.phone_verified(user["id"]):
                raise HTTPException(403, "Verify your phone number to start your free edits.")
        cap = int(_env("CREATORS_DAILY_MAX", "40") or 40)
        if not admin and _today_count() >= cap:
            raise HTTPException(503, "We're at today's limit for free edits. Try again tomorrow.")
        name = req.take_name or take.rsplit("/", 1)[-1]
        j = store.create(user["id"], req.look, take, name, clips, free=not admin, notify=req.email, refs=refs,
                         profile=profile)
        if store.running_count() < max_running() and store.take_slot(j["id"]):
            launch(j["id"])                          # a free slot: its machine starts now; else it waits in line
        j = store.get(j["id"])
        if j["status"] == "failed":
            raise HTTPException(503, "We couldn't start an edit machine. Try again in a minute.")
        return view(j)

    @router.get("/reel-preview")
    def get_reel_preview(url: str = "", user=Depends(current_user)):
        """A pasted Reel's picture and creator, for the link's row and the upload's "Editing in" row. Signed-in
        only; a plain `def`, so its one short request runs in a worker thread, never on the event loop."""
        if not user:
            raise HTTPException(401, "Sign in to continue.")
        url = url.strip()
        if len(url) > 300 or not REEL_LINK.match(url):
            raise HTTPException(400, "That isn't a TikTok, Reel or Short link.")
        return reel_preview(url)

    @router.get("/jobs")
    def get_jobs(user=Depends(need_user)):
        return {"jobs": [view(check_alive(j)) for j in store.list_for(user["id"])]}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, user=Depends(need_user)):
        j = store.get(job_id, user["id"])
        if not j:
            raise HTTPException(404, "No such edit.")
        return view(check_alive(j))

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, user=Depends(need_user)):
        j = store.get(job_id, user["id"])
        if not j:
            raise HTTPException(404, "No such edit.")
        if j["status"] not in store.TERMINAL:
            store.finish(job_id, "cancelled", error="Stopped.")
            if j.get("machine_id") and not j["machine_id"].startswith("local-"):
                stop_machine(j["machine_id"])
        return view(store.get(job_id))

    # ── the home: projects (one per take, its notes rounds as versions) ──
    @router.get("/projects")
    def get_projects(user=Depends(need_user)):
        return {"projects": projects_for(user["id"])}

    def own_project(root: str, user: dict) -> tuple[str, list[dict]]:
        """The project's root id and its jobs; 404 when it isn't this creator's (or is deleted)."""
        chain = store.project_chain(root, user["id"])
        top = chain[0].get("root") or chain[0]["id"] if chain else ""
        if top and top != root:                       # any version's id names its project
            root, chain = top, store.project_chain(top, user["id"])
        if not chain:
            raise HTTPException(404, "No such Reel.")
        return root, chain

    @router.patch("/projects/{root}")
    def rename_project(root: str, req: RenameRequest, user=Depends(need_user)):
        """Rename a project (an empty title goes back to the one we wrote)."""
        root, _ = own_project(root, user)
        store.set_title(root, user["id"], re.sub(r"\s+", " ", req.title).strip()[:80])
        return project_view(own_project(root, user)[1], typical_minutes())

    @router.delete("/projects/{root}")
    def delete_project(root: str, user=Depends(need_user)):
        """Soft delete: gone from the home, files kept (a Trash view comes later)."""
        root, chain = own_project(root, user)
        if any(j["status"] in LIVE for j in chain):
            raise HTTPException(409, "It's still editing. You can delete it once it's done.")
        store.soft_delete(root, user["id"])
        return {"ok": True, "id": root}

    @router.post("/jobs/{job_id}/notify")
    def notify_job(job_id: str, req: NotifyRequest, user=Depends(need_user)):
        if not store.get(job_id, user["id"]):
            raise HTTPException(404, "No such edit.")
        store.update(job_id, notify=1 if req.on else 0)
        return {"ok": True, "notify": req.on}

    @router.post("/jobs/{job_id}/notes")
    async def post_notes(job_id: str, request: Request, user=Depends(need_user)):
        """A review round (creators/web/REVIEW.md): the creator's notes on a finished version, as multipart (a
        "notes" JSON part, then each file under its path). Starts the next version as a child job. Only the body is
        read here, on the event loop; the database, Spaces and Fly work runs in a worker thread (start_round)."""
        form = await request.form()
        part = form.get("notes")                     # a text field, or (review.js) a notes.json Blob part
        raw = (await part.read()).decode("utf-8", "replace") if hasattr(part, "read") else str(part or "")
        try:
            doc = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "The notes didn't arrive whole. Try again.") from None
        notes = doc.get("notes") if isinstance(doc, dict) else None
        if not isinstance(notes, list) or not notes or len(notes) > MAX_NOTES:
            raise HTTPException(400, f"Send between 1 and {MAX_NOTES} notes.")
        files, total = {}, 0
        for name, part in form.multi_items():
            if name == "notes" or not hasattr(part, "read"):
                continue
            if not NOTE_FILE.fullmatch(name):
                raise HTTPException(400, "A note's file has a name we don't take.")
            data = await part.read()
            total += len(data)
            if len(data) > MAX_NOTE_FILE or total > MAX_NOTES_BYTES:
                raise HTTPException(413, "The notes' recordings are too big. Keep each one under a minute.")
            files[name] = (data, getattr(part, "content_type", "") or "application/octet-stream")
        return await run_in_threadpool(start_round, job_id, user, doc, files)

    def start_round(job_id: str, user: dict, doc: dict, files: dict) -> dict:
        parent = store.get(job_id, user["id"])
        if not parent:
            raise HTTPException(404, "No such edit.")
        if parent["status"] != "done":
            raise HTTPException(409, "That edit isn't finished yet.")
        root = parent.get("root") or parent["id"]
        if not is_admin(user) and store.rounds(root) >= MAX_ROUNDS:
            raise HTTPException(402, f"You've used the {MAX_ROUNDS} rounds of notes on this Reel.")
        if store.running_for(user["id"]):
            raise HTTPException(409, "One edit at a time: your last one is still going.")
        j = store.create(user["id"], parent["look"], parent["take_key"], parent.get("take_name") or "",
                         parent["broll_keys"], free=False, notify=False, kind="notes", parent=parent["id"], root=root)
        base = parent["take_key"].split("/up/")[0] + f"/jobs/{j['id']}/"
        try:
            client, bucket = _storage()
            client.put_object(Bucket=bucket, Key=base + "notes.json", Body=json.dumps(doc).encode(),
                              ContentType="application/json")
            for name, (data, ctype) in files.items():
                client.put_object(Bucket=bucket, Key=base + name, Body=data, ContentType=ctype)
        except Exception as e:                       # noqa: BLE001 - say so now; the round isn't counted
            print(f"[creators] notes round {j['id']} couldn't store its notes: {e}")
            store.finish(j["id"], "failed", error="We couldn't save your notes. They're still here: try again.")
            raise HTTPException(503, "We couldn't save your notes. They're still here: try again.") from None
        if store.running_count() < max_running() and store.take_slot(j["id"]):
            launch(j["id"])
        j = store.get(j["id"])
        if j["status"] == "failed":
            raise HTTPException(503, "We couldn't start this round. Your notes are still here: try again.")
        return view(j)

    @router.post("/phone/start")
    def phone_start(req: PhoneRequest, user=Depends(need_user)):
        from webapp import creators_phone as phone
        try:
            phone.start(user["id"], req.phone)
        except phone.PhoneError as e:
            raise HTTPException(400, str(e)) from None
        except Exception as e:                       # noqa: BLE001 - the provider's own trouble
            print(f"[creators] phone start: {type(e).__name__}: {e}")
            raise HTTPException(502, "We couldn't send a text just now. Try again in a minute.") from None
        return {"ok": True}

    @router.post("/phone/check")
    def phone_check(req: PhoneRequest, user=Depends(need_user)):
        from webapp import creators_phone as phone
        try:
            ok = phone.check(user["id"], req.phone, req.code)
        except phone.PhoneError as e:
            raise HTTPException(400, str(e)) from None
        except Exception as e:                       # noqa: BLE001
            print(f"[creators] phone check: {type(e).__name__}: {e}")
            raise HTTPException(502, "We couldn't check that code just now. Try again in a minute.") from None
        if not ok:
            raise HTTPException(400, "That code didn't match. Check the text, or send a new one.")
        return {"ok": True, "phone_verified": True}

    return router


def make_review_router(current_user) -> APIRouter:
    """Whisper for the review's voice notes, right after each recording. The page is /creators/app.html and posts
    to its relative "api/review/transcribe", so the route sits under /creators (registered before that static
    mount). Groq's Whisper does the work (an API call, nothing heavy on this server)."""
    router = APIRouter()

    async def transcribe(request: Request, user=Depends(current_user)):
        if not user or not hosted_on() or not allowed(user):
            raise HTTPException(401, "Sign in to continue.")
        body = await request.body()
        if not body or len(body) > MAX_NOTE_FILE:
            raise HTTPException(413, "That recording is too long. Keep a note under a minute.")
        ctype = (request.headers.get("content-type") or "audio/webm").split(";")[0].strip()
        ext = {"audio/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/mpeg": "mp3",
               "audio/ogg": "ogg", "audio/wav": "wav"}.get(ctype, "webm")
        key = _env("GROQ_API_KEY")
        if not key:
            raise HTTPException(503, "Transcription isn't switched on. Your note is kept as recorded.")

        def ask():
            from groq import Groq
            res = Groq(api_key=key, timeout=60).audio.transcriptions.create(
                file=(f"note.{ext}", body), model=_env("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"),
                response_format="json", language="en", temperature=0.0)
            return str(getattr(res, "text", "") or "").strip()

        try:
            return {"text": await run_in_threadpool(ask)}
        except Exception as e:                       # noqa: BLE001 - the note keeps its recording either way
            print(f"[creators] transcribe failed: {type(e).__name__}")
            raise HTTPException(502, "We couldn't write that one out. Your note is kept as recorded.") from None

    router.add_api_route("/creators/api/review/transcribe", transcribe, methods=["POST"])
    router.add_api_route("/api/review/transcribe", transcribe, methods=["POST"])
    return router


def _today_count() -> int:
    from webapp import database as db
    since = time.time() - 86400
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT COUNT(*) AS n FROM creator_jobs WHERE created_at > ? AND free=1 "
                                 "AND kind='edit'"),
                           (since,)).fetchone()
    return int(row["n"] if row else 0)
