"""Ranking Shorts: Gemini watches clips for the Studio's ranking recipe, on ChannelRecipe's key.

The recipe finds the moments a ranking Short is made of: it searches for about 30 candidate videos, has each one
watched for its moments, picks five, and has those looked at closely for the sound design. The instructions come
from the Studio (the recipe's own, private); this service watches and meters.

Two ways of looking:
- scout: a whole candidate (up to 4 minutes) at 1 frame a second and low resolution, the cheap first pass;
- detail: a short window (up to 40 s) at 4 frames a second and full resolution, for the picks.

Paid for by a monthly allowance of watches on each Studio plan (plans.ranking_watch_limit). Past it, a Studio may
send use_credits and each further watch costs WATCH_CREDITS of the account's cloud credits, which customers top up
through billing; the Studio shows that before it asks. A failed watch is not counted and its credits come back.
A watch of a public video with the same instructions is shared: the second account gets it free and instantly.
Every call records its tokens and cost; a global daily spend cap stops runaway bills.

TikTok search: the recipe also looks for clips on TikTok (keyword search through ScrapeCreators, on ChannelRecipe's
key, SCRAPECREATORS_API_KEY; about $0.002 a search). Without the key it answers 503 and the recipe searches YouTube
only. Each account may run TIKTOK_DAILY searches a day.

Cloud text removal: a clip whose burned-in text is too big for the Studio's own painter (a caption plate across the
action) can be cleaned by PiAPI's video remove-subtitle model (temporal, about $0.006 a second, on ChannelRecipe's
key, PIAPI_API_KEY). The clip is stored privately for an hour, PiAPI fetches it by a signed link, and it is deleted
when PiAPI is done. Without the key it answers 503 and the Studio paints the text out itself.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from webapp import database as db
from webapp import plans
from webapp import style_watch as sw
from webapp.frontier_studio import is_admin

STALE_S = 900
TIKTOK_DAILY = 300                       # TikTok searches an account may run a day (a Short needs about 4-8)
TIKTOK_URL = "https://api.scrapecreators.com/v1/tiktok/search/keyword"
PIAPI_URL = "https://api.piapi.ai/api/v1/task"
CLEAN_DAILY = 80                         # cloud cleans an account may run a day (a Short needs at most 5)
CLEAN_MAX_BYTES = 60 * 1024 * 1024       # PiAPI's limit
_cleans: dict[str, dict] = {}
_clean_used: dict[tuple[int, str], int] = {}
_tiktok_used: dict[tuple[int, str], int] = {}
_tiktok_lock = threading.Lock()
WATCH_CREDITS = 0.02                    # cloud credits for one watch past the monthly allowance
MODES = {"scout": {"fps": 1, "low_res": True, "max_s": 240},
         "detail": {"fps": 4, "low_res": False, "max_s": 40}}
_ready = set()
_ready_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ranking-watch")
_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_QUESTIONS: dict[str, str] = {}

# The account's credits, swappable in tests.
credits_of = lambda user_id: float((db.get_user_by_id(user_id) or {}).get("credits") or 0)   # noqa: E731
deduct = db.deduct_credits
refund = db.refund_credits


def initialize():
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS ranking_watch_jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, source TEXT NOT NULL, start_s INTEGER NOT NULL,
                end_s INTEGER NOT NULL, mode TEXT NOT NULL, question_sha TEXT NOT NULL, model TEXT NOT NULL,
                status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
                cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0,
                credits DOUBLE PRECISION NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_watch_same ON ranking_watch_jobs"
                         "(source,start_s,end_s,mode,question_sha,model,status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_watch_user ON ranking_watch_jobs(user_id,created_at)")
        _ready.add(key)


def month_start(now: float | None = None) -> float:
    t = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
    return datetime(t.year, t.month, 1, tzinfo=timezone.utc).timestamp()


def _used(conn, user_id: int, now: float) -> int:
    """This month's watches that count: started by this account, not served from the cache, not failed."""
    row = conn.execute(db._q("""SELECT COUNT(*) AS n FROM ranking_watch_jobs WHERE user_id=? AND cached=0
        AND status<>'failed' AND credits=0 AND created_at>=?"""), (user_id, month_start(now))).fetchone()
    return int(row["n"] or 0)


def _spent_today(conn) -> float:
    row = conn.execute(db._q("SELECT COALESCE(SUM(cost_usd),0) AS s FROM ranking_watch_jobs WHERE created_at>?"),
                       (time.time() - 86400,)).fetchone()
    return float(row["s"] or 0)


def allowance(user: dict) -> dict:
    """What the account has left this month, and what a watch past it costs."""
    initialize()
    limit = plans.ranking_watch_limit(user.get("plan"), is_admin=is_admin(user))
    with db._conn() as conn:
        used = _used(conn, user["id"], time.time())
    start = datetime.fromtimestamp(month_start(), tz=timezone.utc)
    resets = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)
    return {"used": used, "limit": limit, "remaining": None if limit is None else max(0, limit - used),
            "watch_credits": WATCH_CREDITS, "credits": round(credits_of(user["id"]), 2),
            "resets_at": resets.isoformat()}


def _row(job_id: str) -> dict | None:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM ranking_watch_jobs WHERE id=?"), (job_id,)).fetchone()
    return dict(row) if row else None


def public(row: dict, admin: bool = False) -> dict:
    out = {"job": row["id"], "status": row["status"], "mode": row["mode"], "start": row["start_s"],
           "end": row["end_s"], "cached": bool(row["cached"]), "credits": round(float(row.get("credits") or 0), 4)}
    if not row["source"].startswith("file:"):
        out["video_id"] = row["source"]
    if admin:
        out["usage"] = {"tokens_in": row.get("tokens_in", 0), "tokens_out": row.get("tokens_out", 0),
                        "cost_usd": round(float(row.get("cost_usd") or 0), 5)}
    if row["status"] == "done":
        out["log"] = json.loads(row["result_json"] or "{}")
    if row["status"] == "failed":
        out["error"] = row["error"] or "Gemini could not watch this clip."
    return out


def _run(job_id: str, data: bytes = b"", mime: str = "video/mp4"):
    row = _row(job_id)
    question = _QUESTIONS.pop(job_id, "")
    if not row or row["status"] != "running":
        return
    mode = MODES[row["mode"]]
    try:
        uri = sw.upload_file(data, mime) if data else ""
        log, tin, tout = sw.watch_once("" if data else row["source"], row["start_s"], row["end_s"], file_uri=uri,
                                       mime=mime, question=question, fps=mode["fps"], low_res=mode["low_res"])
        values = ("done", json.dumps(log), "", tin, tout, sw.cost(tin, tout))
    except Exception as exc:                                    # noqa: BLE001 - recorded on the job
        print(f"[ranking-watch] {row['source']} {row['start_s']}-{row['end_s']}: {exc}", flush=True)
        values = ("failed", "", str(exc)[:300], 0, 0, 0.0)
    with db._conn() as conn:                                    # unless a poll already gave it up as stale
        changed = conn.execute(db._q("""UPDATE ranking_watch_jobs SET status=?, result_json=?, error=?, tokens_in=?,
            tokens_out=?, cost_usd=?, updated_at=? WHERE id=? AND status='running'"""),
            (*values, time.time(), job_id)).rowcount
    if changed and values[0] == "failed" and float(row.get("credits") or 0) > 0:
        refund(row["user_id"], float(row["credits"]))           # a watch that failed is never paid for


def start(user: dict, source: str, start_s: float, end_s: float, mode: str, question: str, *, use_credits=False,
          data: bytes = b"", mime: str = "video/mp4", submit=None) -> dict:
    """A cached log, the running job for the same watch, or a new watch charged to the allowance (or credits)."""
    initialize()
    if mode not in MODES:
        raise HTTPException(400, "Choose scout or detail.")
    question = (question or "").strip()
    if not question:
        raise HTTPException(400, "Say what to look for.")
    a, b = int(max(0, start_s)), int(end_s)
    if not 2 <= b - a <= MODES[mode]["max_s"]:
        raise HTTPException(400, f"Watch between 2 and {MODES[mode]['max_s']} seconds at a time in {mode} mode.")
    if not sw._key():
        raise HTTPException(503, "Clip watching isn't available right now.")
    now, name = time.time(), sw.model()
    qsha = hashlib.sha256(question.encode()).hexdigest()[:20]
    admin = is_admin(user)
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("SELECT pg_advisory_xact_lock(726346121)")
        same = conn.execute(db._q("""SELECT * FROM ranking_watch_jobs WHERE source=? AND start_s=? AND end_s=? AND
            mode=? AND question_sha=? AND model=? AND (status='done' OR (status='running' AND updated_at>?))
            ORDER BY status='done' DESC, created_at DESC LIMIT 1"""), (source, a, b, mode, qsha, name, now - STALE_S)).fetchone()
        if same and same["status"] == "done":                   # watched before: free and instant for everyone
            job = "rw_" + secrets.token_hex(10)
            conn.execute(db._q("""INSERT INTO ranking_watch_jobs (id,user_id,source,start_s,end_s,mode,question_sha,
                model,status,result_json,cached,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'done',?,1,?,?)"""),
                (job, user["id"], source, a, b, mode, qsha, name, same["result_json"], now, now))
            return public({**dict(same), "id": job, "cached": 1, "credits": 0}, admin)
        if same:                                                # being watched right now
            return public({**dict(same), "credits": 0}, admin)
        limit = plans.ranking_watch_limit(user.get("plan"), is_admin=admin)
        charge = 0.0
        if limit is not None and _used(conn, user["id"], now) >= limit:
            if not use_credits:
                raise HTTPException(402, f"You've used this month's {limit} clip watches. Each further watch costs "
                                         f"{WATCH_CREDITS} credits; top up credits or wait for next month.")
            charge = WATCH_CREDITS
        if _spent_today(conn) >= sw._env_float("RANKING_WATCH_DAILY_USD", 40.0):
            raise HTTPException(503, "Clip watching is busy today. Try again later.")
        job = "rw_" + secrets.token_hex(10)
        conn.execute(db._q("""INSERT INTO ranking_watch_jobs (id,user_id,source,start_s,end_s,mode,question_sha,model,
            status,credits,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'running',?,?,?)"""),
            (job, user["id"], source, a, b, mode, qsha, name, charge, now, now))
    if charge and not deduct(user["id"], charge):
        with db._conn() as conn:
            conn.execute(db._q("UPDATE ranking_watch_jobs SET status='failed', error=?, credits=0, updated_at=? WHERE id=?"),
                         ("Not enough credits.", time.time(), job))
        raise HTTPException(402, "Not enough credits for more clip watches. Top up credits to continue.")
    _QUESTIONS[job] = question
    (submit or _pool.submit)(_run, job, data, mime)
    return public(_row(job), admin)


def get(user: dict, job_id: str) -> dict:
    initialize()
    row = _row(job_id) if isinstance(job_id, str) and job_id.startswith("rw_") else None
    # Any Studio account may poll a job it was given (a shared watch of a public clip); an upload's job only its owner.
    if not row or (row["source"].startswith("file:") and row["user_id"] != user["id"] and not is_admin(user)):
        raise HTTPException(404, "That clip watch wasn't found.")
    if row["status"] == "running" and time.time() - row["updated_at"] > STALE_S:
        with db._conn() as conn:
            changed = conn.execute(db._q("""UPDATE ranking_watch_jobs SET status='failed', error=?, updated_at=?
                WHERE id=? AND status='running'"""), ("The watch stopped before it finished. Start it again.",
                                                       time.time(), job_id)).rowcount
        if changed and float(row.get("credits") or 0) > 0:
            refund(row["user_id"], float(row["credits"]))
        row = _row(job_id)
    return public(row, is_admin(user))


class TikTokSearch(BaseModel):
    query: str = Field(..., min_length=2, max_length=120)


def tiktok_search(user: dict, query: str) -> dict:
    """TikTok videos for a search, most relevant first: [{id, url, title, channel, views, dur}]."""
    import requests
    key = os.getenv("SCRAPECREATORS_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "TikTok search is not set up yet.")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _tiktok_lock:
        used = _tiktok_used.get((int(user["id"]), day), 0)
        if used >= TIKTOK_DAILY and not is_admin(user):
            raise HTTPException(429, "Today's TikTok searches are used up. YouTube search still works.")
        _tiktok_used[(int(user["id"]), day)] = used + 1
    try:
        r = requests.get(TIKTOK_URL, params={"query": query.strip(), "trim": "true"}, headers={"x-api-key": key},
                         timeout=(10, 40))
        body = r.json() if r.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        body = {}
    if not body.get("search_item_list") and not body.get("success"):
        raise HTTPException(502, "TikTok search didn't answer. YouTube search still works.")
    videos, seen = [], set()
    for item in body.get("search_item_list") or []:
        a = (item or {}).get("aweme_info") or {}
        vid = str(a.get("aweme_id") or "")
        if not vid.isdigit() or vid in seen:
            continue
        seen.add(vid)
        author = str((a.get("author") or {}).get("unique_id") or "")
        dur = float((a.get("video") or {}).get("duration") or 0)
        videos.append({"id": vid, "url": a.get("share_url") or f"https://www.tiktok.com/@{author}/video/{vid}",
                       "title": str(a.get("desc") or "")[:300], "channel": author,
                       "views": int((a.get("statistics") or {}).get("play_count") or 0),
                       "dur": round(dur / 1000 if dur > 1000 else dur, 1)})
    return {"videos": videos}


class CleanRequest(BaseModel):
    data_b64: str
    region: dict | None = None            # {"x", "y", "width", "height"} in the clip's own pixels; None: found by PiAPI


def clean_start(user: dict, data_b64: str, region: dict | None) -> dict:
    """One clip sent to PiAPI's remove-subtitle model: the job to ask about."""
    import requests
    from webapp import storage
    key = os.getenv("PIAPI_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Cloud text removal is not set up yet.")
    if len(data_b64) > CLEAN_MAX_BYTES * 4 // 3 + 8:
        raise HTTPException(400, "Send a clip under 60 MB.")
    try:
        data = base64.b64decode(data_b64, validate=True)
    except ValueError:
        raise HTTPException(400, "The clip could not be read.") from None
    if not data:
        raise HTTPException(400, "The clip is empty.")
    if region is not None:
        try:
            region = {k: int(region[k]) for k in ("x", "y", "width", "height")}
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "A region needs x, y, width and height.") from None
        if min(region.values()) < 0 or not region["width"] or not region["height"]:
            raise HTTPException(400, "That region is empty.")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _tiktok_lock:
        used = _clean_used.get((int(user["id"]), day), 0)
        if used >= CLEAN_DAILY and not is_admin(user):
            raise HTTPException(429, "Today's cloud cleans are used up. Studio cleans clips itself meanwhile.")
        _clean_used[(int(user["id"]), day)] = used + 1
    object_key = f"private/ranking-clean/{hashlib.sha256(data).hexdigest()[:24]}.mp4"
    client = storage._make_client()                           # private: PiAPI reads it by a signed link
    client.put_object(Bucket=storage._spaces_creds()[2], Key=object_key, Body=data, ContentType="video/mp4")
    link = storage.presigned_get_url(object_key, 3600)
    try:
        r = requests.post(PIAPI_URL, headers={"x-api-key": key}, timeout=(10, 60), json={
            "model": "Qubico/video-toolkit", "task_type": "remove-subtitle",
            "input": {"video": link, **({"region": region} if region else {})}})
        task = ((r.json() or {}).get("data") or {}).get("task_id") if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        task = None
    if not task:
        storage.delete_key(object_key)
        raise HTTPException(502, "Cloud text removal didn't start. Studio cleans the clip itself.")
    job = secrets.token_hex(10)
    _cleans[job] = {"user": int(user["id"]), "task": task, "key": object_key, "status": "running",
                    "video_url": "", "error": "", "t": time.time()}
    return {"job": job, "status": "running"}


def clean_get(user: dict, job: str) -> dict:
    import requests
    from webapp import storage
    j = _cleans.get(job)
    if not j or j["user"] != int(user["id"]):
        raise HTTPException(404, "That clean was not found.")
    if j["status"] == "running":
        try:
            r = requests.get(f"{PIAPI_URL}/{j['task']}", headers={"x-api-key": os.getenv("PIAPI_API_KEY", "")},
                             timeout=(10, 30))
            data = (r.json() or {}).get("data") or {}
        except (requests.RequestException, ValueError):
            data = {}
        status = str(data.get("status") or "")
        if status == "completed" and (data.get("output") or {}).get("video_url"):
            j.update(status="done", video_url=data["output"]["video_url"])
        elif status in ("failed", "canceled") or time.time() - j["t"] > 1800:
            j.update(status="failed", error=str((data.get("error") or {}).get("message") or "PiAPI could not clean it"))
        if j["status"] != "running":
            storage.delete_key(j["key"])
    return {"status": j["status"], "video_url": j["video_url"], "error": j["error"]}


class WatchRequest(BaseModel):
    video_id: str
    start: float = 0
    end: float
    mode: str = "scout"
    question: str = Field(..., max_length=20000)
    use_credits: bool = False


class UploadWatchRequest(BaseModel):
    data_b64: str
    mime: str = "video/mp4"
    start: float = 0
    end: float
    mode: str = "scout"
    question: str = Field(..., max_length=20000)
    use_credits: bool = False


def make_router(access) -> APIRouter:
    """`access` is the Studio dependency (a Studio plan, by session or Studio key)."""
    router = APIRouter(prefix="/api/studio/ranking", tags=["ChannelRecipe Studio"])
    no_store = {"Cache-Control": "private, no-store"}

    @router.get("/allowance")
    def get_allowance(user=Depends(access)):
        from fastapi.responses import JSONResponse
        return JSONResponse(allowance(user), headers=no_store)

    @router.post("/watch")
    def post_watch(req: WatchRequest, user=Depends(access)):
        if not _VIDEO.fullmatch(req.video_id or ""):
            raise HTTPException(400, "Choose a YouTube video to watch.")
        return start(user, req.video_id, req.start, req.end, req.mode, req.question, use_credits=req.use_credits)

    @router.post("/watch-upload")
    def post_watch_upload(req: UploadWatchRequest, user=Depends(access)):
        """A creator's own clip (the recipe's "my clips" mode), sent as a light copy."""
        if req.mime not in sw.UPLOAD_MIMES or len(req.data_b64) > sw.MAX_UPLOAD_BYTES * 4 // 3 + 8:
            raise HTTPException(400, f"Send an MP4, WebM or MOV copy under {sw.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        try:
            data = base64.b64decode(req.data_b64, validate=True)
        except ValueError:
            raise HTTPException(400, "The clip could not be read.") from None
        if not data:
            raise HTTPException(400, "The clip is empty.")
        source = "file:" + hashlib.sha256(data).hexdigest()[:20]
        return start(user, source, req.start, req.end, req.mode, req.question, use_credits=req.use_credits,
                     data=data, mime=req.mime)

    @router.get("/watch/{job_id}")
    def get_watch(job_id: str, user=Depends(access)):
        return get(user, job_id)

    @router.post("/tiktok-search")
    def post_tiktok_search(req: TikTokSearch, user=Depends(access)):
        return tiktok_search(user, req.query)

    @router.post("/clean")
    def post_clean(req: CleanRequest, user=Depends(access)):
        return clean_start(user, req.data_b64, req.region)

    @router.get("/clean/{job}")
    def get_clean(job: str, user=Depends(access)):
        return clean_get(user, job)

    return router
