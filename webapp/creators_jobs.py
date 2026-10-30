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

def view(j: dict) -> dict:
    result = None
    if j["status"] == "done" and j.get("output_key"):
        r = j.get("result") or {}
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", (j.get("take_name") or "reel").rsplit(".", 1)[0])[:60] or "reel"
        result = {"video_url": signed_get(j["output_key"]),
                  "download_url": signed_get(j["output_key"], filename=f"{name}-{j['look']}.mp4"),
                  "poster_url": signed_get(j["poster_key"]) if j.get("poster_key") else "",
                  "take_url": signed_get(j["take_key"], 6 * 3600),
                  "seconds": r.get("seconds"), "review": r.get("review"), "summary": r.get("summary") or {},
                  "changes": r.get("changes"), "rendered": r.get("rendered", True)}
    return {"id": j["id"], "look": j["look"], "look_name": LOOK_NAMES.get(j["look"], j["look"]), "status": j["status"],
            "stage": j["stage"], "stages": j["stages"], "progress": j["progress"] if j["status"] != "done" else 1.0,
            "take_name": j.get("take_name") or "", "free": bool(j.get("free")), "result": result,
            "kind": j.get("kind") or "edit", "parent": j.get("parent") or "",
            "in_line": store.ahead_of(j) + 1 if j["status"] == "queued" and not j.get("machine_id") else 0,
            "error": j.get("error") or "", "created_at": j["created_at"], "started_at": j.get("started_at"),
            "ended_at": j.get("ended_at")}


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
          "CREATORS_FLY_CPUS", "CREATORS_FLY_MEMORY_MB")


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
    email: bool = True


class PhoneRequest(BaseModel):
    phone: str = Field(max_length=32)
    code: str = Field(default="", max_length=12)


class NotifyRequest(BaseModel):
    on: bool = True


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
                "phone_required": phone_required(), "max_clips": MAX_CLIPS,
                # what the app promises at "Start": measured on the edit machines (handoffs/creators-hosted.md)
                "typical_minutes": int(_env("CREATORS_TYPICAL_MINUTES", "7") or 7)}

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
        j = store.create(user["id"], req.look, take, name, clips, free=not admin, notify=req.email)
        if store.running_count() < max_running() and store.take_slot(j["id"]):
            launch(j["id"])                          # a free slot: its machine starts now; else it waits in line
        j = store.get(j["id"])
        if j["status"] == "failed":
            raise HTTPException(503, "We couldn't start an edit machine. Try again in a minute.")
        return view(j)

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
