"""Studio Cloud: a Studio's farm renders on ChannelRecipe's machines (Modal), paid from the cloud balance.

The engine already splits a video in two (its README-RENDER-FARM): the Studio does the waiting half (script, voice,
stills, the director's plan) and a rented machine does the computing half (footage, graphics, encode). On its own
that means a RunPod account, a storage bucket and an Anthropic key per creator. Studio Cloud replaces all three:

- **Machines.** The server asks the Modal control endpoint (cloud/modal_app.py) to start one machine per video.
  CPU for most styles, an L4 GPU when the style renders real 3D.
- **Storage.** Each creator gets a folder in our Spaces bucket (studio-cloud/u<id>/). Nobody outside the server
  holds a storage key: the Studio and the machine ask this module to sign one upload or download at a time, and it
  only signs keys inside that creator's folder. Big files never pass through the server.
- **Claude.** A machine has no Claude Code login. Its Claude calls come here (the engine's OpenLux relay setting
  points at /relay), wait in a queue, and the creator's own Studio answers them through its Claude Code, so Claude
  stays on the creator's subscription exactly as when rendering at home.
- **Money.** Every job reserves its maximum before a machine starts, and settles what it used (per machine-second)
  when it ends. Never more than the reservation: the machine is stopped at that limit.

Keys a creator's render needs (voice, pictures, footage) come with the start request and go straight to the
machine; they are never stored here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import secrets
import threading
import time
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from webapp import cloud_wallet as wallet
from webapp import database as db

# What a customer pays per machine-minute, in cents. Modal's list prices (2026-10-22): the CPU machine
# (8 physical cores, 32 GiB) costs about $0.63/h and the GPU machine (L4 + 4 cores, 16 GiB) about $1.12/h.
MACHINES = {
    "cpu": {"cents_per_min": 2, "label": "Cloud machine (16 vCPU)", "per_video_minute": 3.0},
    "gpu": {"cents_per_min": 4, "label": "Cloud machine with a GPU (NVIDIA L4)", "per_video_minute": 7.0},
}
STARTUP_MINUTES = 6            # boot, code download, installing anything new
MAX_MINUTES = 240              # the engine's own watchdog default
MIN_CHARGE_CENTS = 5
RELAY_WAIT_S = 20 * 60         # how long a machine's Claude question waits for the creator's Studio
RELAY_CLAIM_S = 10 * 60        # a claimed question nobody answered goes back in the queue
PUBLIC_URL = os.getenv("CR_PUBLIC_URL", "https://channelrecipe.com").rstrip("/")

# The settings a machine may be given (the engine's farm/config.py RENDER_KEYS plus the job's own), nothing else.
RENDER_KEYS = {
    "ALGROW_API_KEY", "WAVESPEED_API_KEY", "KIE_API_KEY", "GEMINI_API_KEY", "PEXELS_API_KEY", "AI33_API_KEY",
    "AI33_VOICE_ID", "ALGROW_VOICE_ID", "ALGROW_TTS_MODEL", "ALGROW_IMAGE_MODEL", "WAVESPEED_VOICE",
    "WAVESPEED_IMAGE_MODEL", "VOICE_PROVIDER", "IMAGE_PROVIDER", "CLAUDE_SCRIPT_MODEL", "CLAUDE_SCRIPT_BODY_MODEL",
    "CLAUDE_UTILITY_MODEL", "KIE_IMAGE_MODEL", "GEMINI_MODEL", "WHISPER_MODEL", "VIDEO_W", "VIDEO_H", "FPS",
    "NUM_AI_IMAGES", "FOOTAGE_GRADE", "YT_SLEEP", "YT_WORKERS", "YTDLP_PLAYER_CLIENT", "YTDLP_ARGS", "YTDLP_PROXY",
    "AI_IMG_WORKERS", "DL_WORKERS", "RENDER_WORKERS", "FFMPEG_THREADS", "SOURCE_LINKS", "SIRAY_API_KEY",
    "EPIDEMIC_API_KEY", "EPIDEMIC", "ELEVENLABS_API_KEY", "ELEVENLABS_MODEL",
    "CLAUDE_PROVIDER", "SCRIPT_PROVIDER", "CLAUDE_FALLBACK", "PYTHONUNBUFFERED", "DEBIAN_FRONTEND",
    "FARM_JOB_KEY", "FARM_SLUG", "FARM_TITLE", "FARM_CODE_URL", "FARM_ASSETS_URL", "FARM_STATUS_URL",
}
SIGNED_URL_KEYS = ("FARM_CODE_URL", "FARM_ASSETS_URL", "FARM_STATUS_URL")

_ready = set()
_ready_lock = threading.Lock()


def initialize():
    wallet.initialize()
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS cloud_jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, slug TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '', machine TEXT NOT NULL, status TEXT NOT NULL,
                call_id TEXT NOT NULL DEFAULT '', token_hash TEXT NOT NULL, reserved_cents INTEGER NOT NULL,
                max_minutes INTEGER NOT NULL, cents_per_min INTEGER NOT NULL, used_seconds DOUBLE PRECISION,
                charged_cents INTEGER, error TEXT NOT NULL DEFAULT '', created_at DOUBLE PRECISION NOT NULL,
                started_at DOUBLE PRECISION, ended_at DOUBLE PRECISION, checked_at DOUBLE PRECISION)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cloud_jobs_user ON cloud_jobs(user_id,created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cloud_jobs_token ON cloud_jobs(token_hash)")
            conn.execute("""CREATE TABLE IF NOT EXISTS cloud_relay (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, user_id INTEGER NOT NULL, request TEXT NOT NULL,
                status TEXT NOT NULL, answer TEXT, error TEXT NOT NULL DEFAULT '', created_at DOUBLE PRECISION NOT NULL,
                claimed_at DOUBLE PRECISION, answered_at DOUBLE PRECISION)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cloud_relay_user ON cloud_relay(user_id,status,created_at)")
        _ready.add(key)


# ── the machines ───────────────────────────────────────────────────────────────────────────────────────────────

def control_ready() -> bool:
    return bool(os.getenv("CR_CLOUD_CONTROL_URL") and os.getenv("CR_CLOUD_CONTROL_KEY"))


def _control(path: str, body: dict, timeout: float = 60) -> dict:
    """The Modal control endpoint (cloud/modal_app.py): spawn, status, cancel."""
    if not control_ready():
        raise HTTPException(503, "Studio Cloud isn't switched on yet.")
    try:
        r = requests.post(os.environ["CR_CLOUD_CONTROL_URL"].rstrip("/") + path, json=body, timeout=timeout,
                          headers={"Authorization": "Bearer " + os.environ["CR_CLOUD_CONTROL_KEY"]})
    except requests.RequestException as e:
        raise HTTPException(502, f"The cloud machines didn't answer ({type(e).__name__}). Try again shortly.")
    if r.status_code != 200:
        raise HTTPException(502, f"The cloud machines refused ({r.status_code}). Try again shortly.")
    return r.json()


def public(user: dict) -> bool:
    """Studio Cloud is open to everyone with Studio once STUDIO_CLOUD_PUBLIC=1; until then ChannelRecipe's team."""
    from webapp.frontier_studio import is_admin
    return os.getenv("STUDIO_CLOUD_PUBLIC", "") == "1" or is_admin(user)


def quote(minutes: float, machine: str) -> dict:
    """What a video should cost on a machine, and the most it can (the reservation, and the machine's limit)."""
    if machine not in MACHINES:
        raise HTTPException(400, "Choose the cpu or gpu machine.")
    m = MACHINES[machine]
    minutes = max(0.5, min(float(minutes or 10), 120.0))
    expected = STARTUP_MINUTES + m["per_video_minute"] * minutes
    most = min(MAX_MINUTES, math.ceil(expected * 2 + 10))
    return {"machine": machine, "label": m["label"], "cents_per_min": m["cents_per_min"],
            "expected_minutes": round(expected), "max_minutes": most,
            "expected_cents": max(MIN_CHARGE_CENTS, math.ceil(expected * m["cents_per_min"])),
            "max_cents": max(MIN_CHARGE_CENTS, most * m["cents_per_min"])}


def _charge(seconds: float, cents_per_min: int) -> int:
    return max(MIN_CHARGE_CENTS, math.ceil(max(0.0, seconds) / 60 * cents_per_min))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _job(job_id: str, user_id: int | None = None) -> dict:
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM cloud_jobs WHERE id=?"), (job_id,)).fetchone()
    if not row or (user_id is not None and int(row["user_id"]) != int(user_id)):
        raise HTTPException(404, "No such cloud job.")
    return dict(row)


def _update(job_id: str, **fields):
    sets = ",".join(f"{k}=?" for k in fields)
    with db._conn() as conn:
        conn.execute(db._q(f"UPDATE cloud_jobs SET {sets} WHERE id=?"), (*fields.values(), job_id))


def _job_view(j: dict) -> dict:
    return {"id": j["id"], "slug": j["slug"], "title": j["title"], "machine": j["machine"], "status": j["status"],
            "reserved_usd": round(j["reserved_cents"] / 100, 2), "max_minutes": j["max_minutes"],
            "price_per_hr": round(j["cents_per_min"] * 60 / 100, 2),
            "charged_usd": None if j["charged_cents"] is None else round(j["charged_cents"] / 100, 2),
            "minutes": None if j["used_seconds"] is None else round(j["used_seconds"] / 60, 1),
            "error": j["error"], "created_at": j["created_at"], "started_at": j["started_at"],
            "ended_at": j["ended_at"]}


def _finish(j: dict, status: str, seconds: float | None = None, error: str = "") -> dict:
    """End a job once: stop counting, charge what it used (never past the reservation), give the rest back."""
    if j["status"] in ("done", "failed", "cancelled", "refused"):
        return j
    now = time.time()
    wall = now - float(j["started_at"] or now)
    used = min(wall, float(seconds)) if seconds is not None and seconds > 0 else wall
    used = min(used, j["max_minutes"] * 60)
    cents = _charge(used, j["cents_per_min"]) if j["started_at"] else 0
    settled = wallet.settle(j["user_id"], j["id"], cents, note=f"{j['machine']} render, {used / 60:.1f} min")
    charged = settled["charged"] if settled["charged"] is not None else cents
    _update(j["id"], status=status, ended_at=now, used_seconds=used, charged_cents=charged, error=error[:500])
    with db._conn() as conn:                    # nobody is left to answer questions for a job that has ended
        conn.execute(db._q("UPDATE cloud_relay SET status='expired' WHERE job_id=? AND status IN ('pending','claimed')"),
                     (j["id"],))
    return _job(j["id"])


def refresh(j: dict, force: bool = False) -> dict:
    """Ask Modal how a running job is doing (at most every 10 s), and settle it when it has ended."""
    if j["status"] != "running" or not j["call_id"]:
        return j
    now = time.time()
    if not force and j["checked_at"] and now - float(j["checked_at"]) < 10:
        return j
    _update(j["id"], checked_at=now)
    try:
        st = _control("/status", {"call_id": j["call_id"]}, timeout=30)
    except HTTPException:
        st = {"state": "running"}
    state = st.get("state")
    if state == "done":
        res = st.get("result") or {}
        return _finish(j, "done" if res.get("ok") else "failed", res.get("seconds"),
                       "" if res.get("ok") else str(res.get("error") or "the render did not finish"))
    if state in ("failed", "cancelled"):
        return _finish(j, state, None, str(st.get("error") or ""))
    if now - float(j["started_at"] or now) > j["max_minutes"] * 60 + 300:
        try:                                        # past its limit and Modal still says running: stop it
            _control("/cancel", {"call_id": j["call_id"]}, timeout=30)
        except HTTPException:
            pass
        return _finish(j, "failed", None, "stopped at its time limit")
    return _job(j["id"])


def _sweep_loop():
    """Settle jobs nobody is polling (a Studio that closed mid-render still gets its unused money back)."""
    while True:
        time.sleep(120)
        try:
            initialize()
            with db._conn() as conn:
                rows = conn.execute(db._q("SELECT * FROM cloud_jobs WHERE status='running'")).fetchall()
            for row in rows:
                refresh(dict(row))
        except Exception:                           # noqa: BLE001 - a sweep must never take the server down
            pass


# ── storage: one signed request at a time, only inside the creator's folder ──────────────────────────────────

def _prefix(user_id: int) -> str:
    return f"studio-cloud/u{int(user_id)}/"


def _clean_key(key: str) -> str:
    key = str(key or "").lstrip("/")
    if not key or ".." in key.split("/") or "\\" in key or len(key) > 600:
        raise HTTPException(400, "That storage name isn't allowed.")
    return key


def _storage():
    from webapp import storage
    if not storage.is_remote():
        raise HTTPException(503, "Storage isn't configured.")
    _k, _s, bucket, _r, endpoint = storage._spaces_creds()
    return storage._get_client(), bucket, endpoint


def storage_op(user_id: int, op: str, key: str = "", method: str = "GET", seconds: int = 3600,
               content_type: str = "") -> dict:
    client, bucket, _endpoint = _storage()
    base = _prefix(user_id)
    if op == "list":
        prefix = base + (_clean_key(key) if key else "")
        items, token = [], None
        while len(items) < 5000:
            kw = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            page = client.list_objects_v2(**kw)
            for o in page.get("Contents") or []:
                items.append({"key": o["Key"][len(base):], "size": int(o.get("Size") or 0),
                              "modified": o["LastModified"].timestamp() if o.get("LastModified") else 0})
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return {"items": items}
    full = base + _clean_key(key)
    if op == "exists":
        try:
            client.head_object(Bucket=bucket, Key=full)
            return {"exists": True}
        except Exception:                           # noqa: BLE001 - botocore raises a ClientError for a 404
            return {"exists": False}
    if op == "delete":
        client.delete_object(Bucket=bucket, Key=full)
        return {"ok": True}
    if op == "presign":
        method = method.upper()
        if method not in ("GET", "PUT"):
            raise HTTPException(400, "Sign a GET or a PUT.")
        params = {"Bucket": bucket, "Key": full}
        if method == "PUT" and content_type:
            params["ContentType"] = content_type
        url = client.generate_presigned_url("get_object" if method == "GET" else "put_object", Params=params,
                                            ExpiresIn=max(60, min(int(seconds or 3600), 12 * 3600)))
        return {"url": url}
    raise HTTPException(400, "Unknown storage operation.")


def _signed_key(url: str, user_id: int) -> str:
    """The storage key a signed URL points at, if it is ours and inside this creator's folder; else ''."""
    from webapp import storage
    _k, _s, bucket, _r, endpoint = storage._spaces_creds()
    host = urlparse(endpoint).hostname or ""
    u = urlparse(url or "")
    if u.scheme != "https":
        return ""
    if u.hostname == f"{bucket}.{host}":
        key = u.path.lstrip("/")
    elif u.hostname == host and u.path.startswith(f"/{bucket}/"):
        key = u.path[len(bucket) + 2:]
    else:
        return ""
    return key if key.startswith(_prefix(user_id)) and ".." not in key.split("/") else ""


# ── starting a job ──────────────────────────────────────────────────────────────────────────────────────────

def pod_env(user_id: int, job_id: str, token: str, env: dict, max_minutes: int) -> dict:
    """The machine's environment: the creator's render settings that are allowed, the job's own signed links
    (checked to be ours and theirs), and the routes back here for storage and Claude."""
    out = {}
    for k, v in (env or {}).items():
        if k in RENDER_KEYS and isinstance(v, str) and len(v) < 20000:
            out[k] = v
    for k in SIGNED_URL_KEYS:
        if out.get(k) and not _signed_key(out[k], user_id):
            raise HTTPException(400, f"{k} must be a link this account's Studio Cloud storage signed.")
    if not out.get("FARM_JOB_KEY"):
        raise HTTPException(400, "The job has no spec.")
    out["FARM_JOB_KEY"] = _clean_key(out["FARM_JOB_KEY"])
    out.update({
        "CLAUDE_PROVIDER": "anthropic", "SCRIPT_PROVIDER": "anthropic", "CLAUDE_FALLBACK": "",
        "CLAUDE_RELAY": "openlux", "OPENLUX_BASE_URL": f"{PUBLIC_URL}/api/studio/cloud/relay",
        "OPENLUX_API_KEY": token, "FARM_BROKER_URL": f"{PUBLIC_URL}/api/studio/cloud/pod", "FARM_JOB_TOKEN": token,
        "FARM_MAX_MINUTES": str(max_minutes), "CR_CLOUD_JOB": job_id, "PYTHONUNBUFFERED": "1",
    })
    out.setdefault("AI_IMG_WORKERS", "4")
    return out


def start_job(user: dict, machine: str, minutes: float, env: dict, slug: str = "", title: str = "") -> dict:
    """Reserve the most the job may cost, then start its machine. Nothing starts when the balance is short."""
    initialize()
    if not public(user):
        raise HTTPException(403, "Studio Cloud is in testing with ChannelRecipe's team.")
    q = quote(minutes, machine)
    job_id = "cj_" + secrets.token_hex(10)
    token = "crj_" + secrets.token_urlsafe(32)
    payload_env = pod_env(user["id"], job_id, token, env, q["max_minutes"])
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO cloud_jobs (id,user_id,slug,title,machine,status,token_hash,reserved_cents,
            max_minutes,cents_per_min,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""),
            (job_id, user["id"], slug[:120], title[:200], machine, "starting", _hash(token), q["max_cents"],
             q["max_minutes"], q["cents_per_min"], time.time()))
    if not wallet.reserve(user["id"], job_id, q["max_cents"], note=f"{machine} render, up to {q['max_minutes']} min"):
        _update(job_id, status="refused", error="balance too low")
        have = wallet.balance_cents(user["id"])
        raise HTTPException(402, f"This render can cost up to ${q['max_cents'] / 100:.2f} (usually about "
                                 f"${q['expected_cents'] / 100:.2f}); your Studio Cloud balance is ${have / 100:.2f}. "
                                 "Top up, then start it again. Nothing was charged.")
    try:
        call = _control("/spawn", {"machine": machine, "job_id": job_id, "env": payload_env,
                                   "max_seconds": q["max_minutes"] * 60})
    except HTTPException as e:
        wallet.settle(user["id"], job_id, 0, note="the machine didn't start")
        _update(job_id, status="failed", error=str(e.detail)[:300], ended_at=time.time(), charged_cents=0)
        raise
    _update(job_id, status="running", call_id=str(call.get("call_id") or ""), started_at=time.time())
    return _job_view(_job(job_id)) | {"quote": q}


def cancel(j: dict) -> dict:
    if j["status"] != "running":
        return j
    try:
        _control("/cancel", {"call_id": j["call_id"]}, timeout=30)
    except HTTPException:
        pass
    return _finish(j, "cancelled")


# ── the Claude relay ────────────────────────────────────────────────────────────────────────────────────────

def _pod_job(request: Request) -> dict:
    token = request.headers.get("x-api-key", "")
    auth = request.headers.get("authorization", "")
    if not token and auth.startswith("Bearer "):
        token = auth[7:]
    if not token.startswith("crj_"):
        raise HTTPException(401, "This is a Studio Cloud machine's address.")
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM cloud_jobs WHERE token_hash=?"), (_hash(token),)).fetchone()
    if not row or row["status"] != "running":
        raise HTTPException(401, "That cloud job has ended.")
    return dict(row)


def relay_enqueue(j: dict, body: dict) -> str:
    rid = "cq_" + secrets.token_hex(10)
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO cloud_relay (id,job_id,user_id,request,status,created_at)
            VALUES (?,?,?,?,?,?)"""), (rid, j["id"], j["user_id"], json.dumps(body), "pending", time.time()))
    return rid


def relay_result(rid: str) -> dict | None:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT status,answer,error FROM cloud_relay WHERE id=?"), (rid,)).fetchone()
    if not row or row["status"] in ("pending", "claimed"):
        return None
    return {"status": row["status"], "answer": row["answer"] or "", "error": row["error"] or ""}


def relay_next(user_id: int) -> dict | None:
    """The oldest question a creator's machines are waiting on, claimed for their Studio to answer."""
    initialize()
    now = time.time()
    with db._conn() as conn:
        conn.execute(db._q("UPDATE cloud_relay SET status='pending', claimed_at=NULL WHERE user_id=? AND "
                           "status='claimed' AND claimed_at<?"), (user_id, now - RELAY_CLAIM_S))
        row = conn.execute(db._q("SELECT id,job_id,request FROM cloud_relay WHERE user_id=? AND status='pending' "
                                 "ORDER BY created_at LIMIT 1"), (user_id,)).fetchone()
        if not row:
            return None
        cur = conn.cursor()
        cur.execute(db._q("UPDATE cloud_relay SET status='claimed', claimed_at=? WHERE id=? AND status='pending'"),
                    (now, row["id"]))
        if cur.rowcount <= 0:
            return None
    return {"id": row["id"], "job_id": row["job_id"], "request": json.loads(row["request"])}


def relay_answer(user_id: int, rid: str, text: str = "", error: str = "") -> bool:
    initialize()
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute(db._q("UPDATE cloud_relay SET status=?, answer=?, error=?, answered_at=? WHERE id=? AND user_id=? "
                          "AND status IN ('pending','claimed')"),
                    ("error" if error else "answered", text, error[:500], time.time(), rid, user_id))
        return cur.rowcount > 0


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


def _message(text: str, model: str) -> dict:
    return {"id": "msg_" + secrets.token_hex(8), "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}


async def _wait_answer(rid: str, until: float):
    while time.time() < until:
        res = await run_in_threadpool(relay_result, rid)
        if res:
            return res
        await asyncio.sleep(1.0)
    return None


def relay_response(j: dict, body: dict):
    """Answer a machine's Anthropic Messages request with the creator's Studio's reply.

    Streaming requests get pings while they wait (so neither the proxy in front of us nor the engine's read timeout
    gives up) and then the reply as one text block. Plain requests wait quietly for 90 s; after that the response is
    committed and kept alive with leading whitespace, which JSON allows."""
    rid = relay_enqueue(j, body)
    model = str(body.get("model") or "claude")
    until = time.time() + RELAY_WAIT_S

    if body.get("stream"):
        async def stream():
            yield _sse({"type": "message_start", "message": {**_message("", model), "content": []}})
            yield _sse({"type": "ping"})
            res = None
            while res is None and time.time() < until:
                res = await _wait_answer(rid, min(until, time.time() + 10))
                if res is None:
                    yield _sse({"type": "ping"})
            if not res or res["status"] != "answered":
                why = (res or {}).get("error") or "timed out waiting for your Studio"
                yield _sse({"type": "error", "error": {"type": "overloaded_error",
                                                       "message": f"Studio relay: {why}; try again"}})
                return
            yield _sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            yield _sse({"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": res["answer"]}})
            yield _sse({"type": "content_block_stop", "index": 0})
            yield _sse({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 0}})
            yield _sse({"type": "message_stop"})
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return _PlainRelay(rid, model, until)


class _PlainRelay:
    """Decided after the first 90 s: a quick answer is an ordinary JSON response (or a 503 the engine retries);
    a slow one becomes a 200 kept alive with whitespace."""
    def __init__(self, rid: str, model: str, until: float):
        self.rid, self.model, self.until = rid, model, until

    async def respond(self):
        res = await _wait_answer(self.rid, time.time() + 90)
        if res is not None:
            if res["status"] == "answered":
                return JSONResponse(_message(res["answer"], self.model))
            return JSONResponse({"type": "error", "error": {"type": "overloaded_error",
                                                            "message": res["error"] or "Studio relay failed"}},
                                status_code=503)

        async def rest():
            r = None
            while r is None and time.time() < self.until:
                yield " "
                r = await _wait_answer(self.rid, min(self.until, time.time() + 10))
            if r and r["status"] == "answered":
                yield json.dumps(_message(r["answer"], self.model))
            else:
                yield json.dumps({"type": "error", "content": [], "error": {
                    "type": "overloaded_error", "message": (r or {}).get("error") or "timed out waiting for your Studio"}})
        return StreamingResponse(rest(), media_type="application/json")


# ── routes ──────────────────────────────────────────────────────────────────────────────────────────────────

class QuoteRequest(BaseModel):
    minutes: float = 10
    machine: str = "cpu"


class StartRequest(BaseModel):
    machine: str = "cpu"
    minutes: float = 10
    slug: str = ""
    title: str = ""
    env: dict = Field(default_factory=dict)


class StorageRequest(BaseModel):
    op: str
    key: str = ""
    method: str = "GET"
    seconds: int = 3600
    content_type: str = ""


class Grant(BaseModel):
    email: str
    usd: float
    note: str = ""


class RelayAnswer(BaseModel):
    text: str = ""
    error: str = ""


_sweeper = []


def make_router(access) -> APIRouter:
    router = APIRouter(prefix="/api/studio/cloud", tags=["ChannelRecipe Studio"])
    if not _sweeper and os.getenv("CR_CLOUD_SWEEP", "1") == "1":
        _sweeper.append(threading.Thread(target=_sweep_loop, daemon=True, name="cloud-sweep"))
        _sweeper[0].start()

    @router.get("/status")
    def get_status(user=Depends(access)):
        ok = control_ready() and public(user)
        return {"available": ok, "balance_usd": round(wallet.balance_cents(user["id"]) / 100, 2),
                "chunks_usd": list(wallet.CHUNKS_USD), "topup_url": f"{PUBLIC_URL}/app#billing",
                "machines": {k: {"label": v["label"], "price_per_hr": round(v["cents_per_min"] * 0.6, 2)}
                             for k, v in MACHINES.items()},
                "why": "" if ok else ("Studio Cloud is in testing with ChannelRecipe's team." if control_ready()
                                      else "Studio Cloud isn't switched on yet.")}

    @router.post("/quote")
    def post_quote(req: QuoteRequest, user=Depends(access)):
        return quote(req.minutes, req.machine)

    @router.post("/jobs")
    def post_job(req: StartRequest, user=Depends(access)):
        return start_job(user, req.machine, req.minutes, req.env, req.slug, req.title)

    @router.get("/jobs")
    def get_jobs(user=Depends(access)):
        initialize()
        with db._conn() as conn:
            rows = conn.execute(db._q("SELECT * FROM cloud_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 50"),
                                (user["id"],)).fetchall()
        return {"jobs": [_job_view(refresh(dict(r))) for r in rows]}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, user=Depends(access)):
        return _job_view(refresh(_job(job_id, user["id"])))

    @router.delete("/jobs/{job_id}")
    def delete_job(job_id: str, user=Depends(access)):
        return _job_view(cancel(_job(job_id, user["id"])))

    @router.post("/storage")
    def post_storage(req: StorageRequest, user=Depends(access)):
        if not public(user):
            raise HTTPException(403, "Studio Cloud is in testing with ChannelRecipe's team.")
        return storage_op(user["id"], req.op, req.key, req.method, req.seconds, req.content_type)

    @router.get("/relay/next")
    async def get_relay_next(wait: int = 20, user=Depends(access)):
        until = time.time() + max(0, min(wait, 25))
        while True:
            item = await run_in_threadpool(relay_next, user["id"])
            if item or time.time() >= until:
                return {"question": item}
            await asyncio.sleep(1.0)

    @router.post("/relay/{rid}/answer")
    def post_relay_answer(rid: str, req: RelayAnswer, user=Depends(access)):
        return {"ok": relay_answer(user["id"], rid, req.text, req.error)}

    @router.post("/admin/credit")
    def post_credit(req: Grant, user=Depends(access)):
        from webapp.frontier_studio import is_admin
        if not is_admin(user):
            raise HTTPException(403, "Only ChannelRecipe's team can add balance.")
        target = db.get_user_by_email(req.email.strip().lower())
        if not target:
            raise HTTPException(404, "No account with that email.")
        cents = round(float(req.usd) * 100)
        if not 1 <= cents <= 10000:
            raise HTTPException(400, "Add between $0.01 and $100.")
        wallet.grant(target["id"], cents, (req.note or "from ChannelRecipe")[:120] + f" (by {user['email']})")
        return {"balance_usd": round(wallet.balance_cents(target["id"]) / 100, 2)}

    # ── what a machine calls, with its job token ──
    @router.post("/pod/storage")
    def post_pod_storage(req: StorageRequest, request: Request):
        j = _pod_job(request)
        return storage_op(j["user_id"], req.op, req.key, req.method, req.seconds, req.content_type)

    @router.post("/relay/v1/messages")
    async def post_relay_messages(request: Request):
        j = await run_in_threadpool(_pod_job, request)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Send an Anthropic Messages request.")
        if not isinstance(body, dict) or not body.get("messages"):
            raise HTTPException(400, "Send an Anthropic Messages request.")
        out = relay_response(j, body)
        return await out.respond() if isinstance(out, _PlainRelay) else out

    return router
