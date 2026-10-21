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

Built to keep working with many customers at once (the 2026-10-21 scale audit):
- Every job lives in the database, its instructions included, and the process holding it heartbeats it. A deploy or
  a crash no longer strands watches 'running' for 15 minutes (counted against the allowance, credits kept, identical
  requests attached to a dead job): a job whose holder stopped heartbeating is picked up again by the next process,
  or given up and refunded. An upload whose bytes died with the old process answers 404 "That clip watch wasn't
  found.", which makes the Studio send the clip again (common.watch re-POSTs once; the server dedupes).
- Watches run fairly: each account has a few running at once (more only while nobody else waits), so one account's
  scouting can't fill every worker and stall every other customer's Short.
- Spend is recorded for every call Google bills, failed answers included. The daily brake is sized from what the
  customers' plans entitle them to (never below RANKING_WATCH_DAILY_USD), one account may use only a share of it,
  each refusal says which one it is (X-CR-Reason), and crossing 50/80/100% of a cap is logged and mailed to the team.
- Uploads are read with a size guard before parsing, kept on disk (not in memory) until a worker takes them, and
  deleted from Gemini's file service once watched.
- Routes run on their own thread limits, so slow searches and cleans never take the website's request threads.

TikTok and YouTube search: the recipe also searches through ScrapeCreators (on ChannelRecipe's key,
SCRAPECREATORS_API_KEY; about $0.002 a search). Without the key both answer 503 "not set up" and the Studio searches
YouTube with yt-dlp. Identical searches are answered from a 6-hour cache, each account has a daily number of searches,
and a global daily spend cap bounds the bill; all of it lives in the database, so a deploy resets nothing.

Cloud text removal: a clip whose burned-in text is too big for the Studio's own painter (a caption plate across the
action) can be cleaned by PiAPI's video remove-subtitle model (temporal, about $0.006 a second, on ChannelRecipe's
key, PIAPI_API_KEY). The clip is stored privately under its own job's name, PiAPI fetches it by a signed link, and it
is deleted when PiAPI is done (or the job is abandoned). Jobs live in the database. Without the key it answers 503
and the Studio paints the text out itself.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import weakref
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from webapp import database as db
from webapp import plans
from webapp import style_watch as sw
from webapp.frontier_studio import is_admin


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


_env_float = sw._env_float

STALE_S = 900                            # the Studio waits 15 minutes for a watch (common.watch wait_s), then gives up
HEARTBEAT_S = 30                         # how often the process holding a job says it is still alive
LOST_S = 120                             # a queued or running job unheard of this long was lost with its process
MAX_ATTEMPTS = 3                         # a job picked up again this often (it keeps dying) is given up and refunded
WINDOW_SLACK_S = 2                       # a client's rounding (a 240.4 s window sent as 241 s) is trimmed, not refused
# Workers: about 36 watches a minute at 6 (scenario timings, 10-20 s a watch); 12 is about 0.8M tokens a minute,
# inside Gemini Flash's paid tier. Uploading to Gemini happens inside a job, so it counts.
WORKERS = max(2, _env_int("RANKING_WATCH_WORKERS", 12))
ACCOUNT_RUNNING = max(1, _env_int("RANKING_WATCH_ACCOUNT_RUNNING", 4))      # a Short scouts 4 at a time
ACCOUNT_RUNNING_MAX = max(ACCOUNT_RUNNING, _env_int("RANKING_WATCH_ACCOUNT_RUNNING_MAX", 8))   # only while idle
ACCOUNT_WAITING = max(4, _env_int("RANKING_WATCH_ACCOUNT_WAITING", 40))     # queued + running per account
QUEUE_MAX = max(WORKERS, _env_int("RANKING_WATCH_QUEUE_MAX", 400))          # waiting in this process, all accounts
TOKENS_PER_S = {"scout": 100, "detail": 1100}   # Gemini's video tokens a second at each mode's fps and resolution
BODY_SLACK = 64 * 1024                   # the JSON around an upload's base64 (the question is up to 20,000 characters)
UPLOAD_INFLIGHT_BYTES = max(64, _env_int("RANKING_UPLOAD_INFLIGHT_MB", 256)) * 1024 * 1024
SPOOL = Path(os.getenv("RANKING_SPOOL_DIR") or (Path(tempfile.gettempdir()) / "cr-ranking-uploads"))

TIKTOK_DAILY = _env_int("RANKING_TIKTOK_DAILY", 300)                 # searches an account may run a day (4-8 a Short)
YOUTUBE_SEARCH_DAILY = _env_int("RANKING_YOUTUBE_SEARCH_DAILY", 500)  # a three-round Short runs about 40
SEARCH_CACHE_S = 6 * 3600                # the same search within 6 hours is answered from the cache, free
TIKTOK_URL = "https://api.scrapecreators.com/v1/tiktok/search/keyword"
YOUTUBE_URL = "https://api.scrapecreators.com/v1/youtube/search"
PIAPI_URL = "https://api.piapi.ai/api/v1/task"
CLEAN_DAILY = _env_int("RANKING_CLEAN_DAILY", 80)                    # cloud cleans an account may run a day (5 a Short)
CLEAN_MAX_BYTES = 60 * 1024 * 1024       # PiAPI's limit
CLEAN_GIVE_UP_S = 1800
WATCH_CREDITS = 0.02                     # cloud credits for one watch past the monthly allowance
MODES = {"scout": {"fps": 1, "low_res": True, "max_s": 240},
         "detail": {"fps": 4, "low_res": False, "max_s": 40}}
_ready = set()
_ready_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="ranking-watch")
_REAL_POOL = _pool
_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")

LOST_ERROR = "The clip was lost when ChannelRecipe restarted. Send it again."
NOT_FOUND = "That clip watch wasn't found."      # common.watch POSTs again once on this text (CONTRACT.md)
STOPPED = "The watch stopped before it finished. Start it again."
# Matched by common.TRANSIENT ("channelrecipe could not answer"), so the Studio backs off and asks again for a few
# minutes; if it still can't, the Short stops with this message, which then reads right too.
BUSY_RETRY = ("ChannelRecipe could not answer right now: clip watching is very busy. Your progress is saved; "
              "make the Short again in a few minutes.")

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
            for column, kind in (("mime", "TEXT NOT NULL DEFAULT 'video/mp4'"), ("file_uri", "TEXT NOT NULL DEFAULT ''"),
                                 ("file_name", "TEXT NOT NULL DEFAULT ''"), ("owner", "TEXT NOT NULL DEFAULT ''"),
                                 ("heartbeat_at", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
                                 ("started_at", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
                                 ("attempts", "INTEGER NOT NULL DEFAULT 0")):
                db._ensure_column(conn, "ranking_watch_jobs", column, kind)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_watch_same ON ranking_watch_jobs"
                         "(source,start_s,end_s,mode,question_sha,model,status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_watch_user ON ranking_watch_jobs(user_id,created_at)")
            # the daily spend sums and the lost-job sweep read these (a full-table scan per start, before)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_watch_created ON ranking_watch_jobs(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_watch_live ON ranking_watch_jobs(status,heartbeat_at)")
            # the instructions, once per text: a job picked up after a restart is watched with exactly the same ones
            conn.execute("""CREATE TABLE IF NOT EXISTS ranking_watch_questions (
                sha TEXT PRIMARY KEY, question TEXT NOT NULL, used_at DOUBLE PRECISION NOT NULL)""")
            # searches and cloud cleans: per-account days, spend, the search cache and clean jobs, kept across deploys
            conn.execute("""CREATE TABLE IF NOT EXISTS ranking_calls (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, key_sha TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                task TEXT NOT NULL DEFAULT '', object_key TEXT NOT NULL DEFAULT '',
                cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_calls_user ON ranking_calls(user_id,kind,created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_calls_kind ON ranking_calls(kind,created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_calls_key ON ranking_calls(kind,key_sha,created_at)")
        _ready.add(key)


def month_start(now: float | None = None) -> float:
    t = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
    return datetime(t.year, t.month, 1, tzinfo=timezone.utc).timestamp()


def day_start(now: float | None = None) -> float:
    t = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
    return datetime(t.year, t.month, t.day, tzinfo=timezone.utc).timestamp()


def _used(conn, user_id: int, now: float) -> int:
    """This month's watches that count: started by this account, not served from the cache, not paid with credits,
    finished or still being watched by a live process. A job whose process died stops counting at once."""
    row = conn.execute(db._q("""SELECT COUNT(*) AS n FROM ranking_watch_jobs WHERE user_id=? AND cached=0
        AND credits=0 AND created_at>=? AND (status='done' OR (status IN ('queued','running') AND heartbeat_at>?))"""),
        (user_id, month_start(now), now - LOST_S)).fetchone()
    return int(row["n"] or 0)


def _spent_today(conn, user_id: int | None = None) -> float:
    """Dollars of watching in the last 24 hours (a job still running counts its estimate until Google's bill is in)."""
    sql = "SELECT COALESCE(SUM(cost_usd),0) AS s FROM ranking_watch_jobs WHERE created_at>?"
    args: tuple = (time.time() - 86400,)
    if user_id is not None:
        sql, args = sql + " AND user_id=?", args + (user_id,)
    row = conn.execute(db._q(sql), args).fetchone()
    return float(row["s"] or 0)


def _estimate(mode: str, seconds: int) -> float:
    return sw.cost(int(seconds * TOKENS_PER_S.get(mode, 1100)), 1500)


_cap_cache = {"t": 0.0, "usd": 0.0}


def daily_cap() -> float:
    """Today's watching brake in dollars: what the customers' plans entitle them to in a day, with headroom, never
    below RANKING_WATCH_DAILY_USD (default $40) and never above RANKING_WATCH_DAILY_USD_MAX when that is set. A fixed
    $40 covered about 60-100 Shorts a day for every customer together, then failed all of them at once."""
    floor = _env_float("RANKING_WATCH_DAILY_USD", 40.0)
    if time.time() - _cap_cache["t"] > 600:
        entitled = 0.0
        try:
            with db._conn() as conn:
                rows = conn.execute("SELECT plan, COUNT(*) AS n FROM users WHERE plan <> 'free' GROUP BY plan").fetchall()
            entitled = sum(int(r["n"] or 0) * (plans.ranking_watch_limit(r["plan"]) or 0) for r in rows) / 30
        except Exception as exc:                                # noqa: BLE001 - the floor still holds
            print(f"[ranking-cap] could not size the cap from plans: {exc}", flush=True)
        _cap_cache.update(t=time.time(), usd=entitled * _env_float("RANKING_WATCH_USD_PER_WATCH", 0.015)
                          * _env_float("RANKING_WATCH_CAP_HEADROOM", 1.5))
    cap = max(floor, _cap_cache["usd"])
    ceiling = _env_float("RANKING_WATCH_DAILY_USD_MAX", 0.0)
    return min(cap, ceiling) if ceiling > 0 else cap


def account_share() -> float:
    """The part of a day's cap one account may use (an account scripted, leaked or simply busy can't stop everyone)."""
    return min(1.0, max(0.01, _env_float("RANKING_ACCOUNT_SHARE", 0.25)))


# ── alerts: the team hears about a cap before customers do ──────────────────────────────────────────────────────────
_alerted: set = set()
_alert_lock = threading.Lock()


def _alert(kind: str, spent: float, cap: float):
    """Log (and mail the admins, and tell Sentry) when today's spend of `kind` crosses 50%, 80% and 100% of its cap,
    once each a day."""
    if cap <= 0:
        return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for share in (1.0, 0.8, 0.5):
        if spent < share * cap:
            continue
        with _alert_lock:
            if (kind, day, share) in _alerted:
                return
            _alerted.add((kind, day, share))
        text = (f"Ranking Shorts {kind}: ${spent:.2f} spent in 24 h, {int(share * 100)}% of today's ${cap:.2f} cap"
                + (". New requests are refused until spend ages out; raise the cap's env if this is real demand."
                   if share >= 1 else "."))
        print(f"[ranking-cap] ALERT {text}", flush=True)
        threading.Thread(target=_tell_team, args=(text,), daemon=True).start()
        return


def _tell_team(text: str):
    try:
        import sentry_sdk
        if sentry_sdk.is_initialized():
            sentry_sdk.capture_message(text, level="warning")
    except Exception:                                           # noqa: BLE001
        pass
    try:
        import config
        from webapp import email_service
        resend = email_service._get_client()
        for to in list(getattr(config, "ADMIN_EMAILS", []) or []) if resend else []:
            resend.Emails.send({"from": email_service.FROM_EMAIL, "to": [to], "subject": "Ranking Shorts spend alert",
                                "html": f"<p>{text}</p>"})
    except Exception as exc:                                    # noqa: BLE001
        print(f"[ranking-cap] could not mail the alert: {exc}", flush=True)


def allowance(user: dict) -> dict:
    """What the account has left this month, and what a watch past it costs."""
    initialize()
    _ensure_supervisor()
    limit = plans.ranking_watch_limit(user.get("plan"), is_admin=is_admin(user))
    with db._conn() as conn:
        used = _used(conn, user["id"], time.time())
    start = datetime.fromtimestamp(month_start(), tz=timezone.utc)
    resets = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)
    return {"used": used, "limit": limit, "remaining": None if limit is None else max(0, limit - used),
            "watch_credits": WATCH_CREDITS, "credits": round(credits_of(user["id"]), 2),
            "resets_at": resets.isoformat(),
            # what one Short takes (scenario runs: 26-52 scouts plus 5-9 close looks), so pages can say "about N left"
            "per_short": {"low": 30, "high": 60}}


def _row(job_id: str) -> dict | None:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM ranking_watch_jobs WHERE id=?"), (job_id,)).fetchone()
    return dict(row) if row else None


def public(row: dict, admin: bool = False) -> dict:
    # A job waiting its turn reads "running": the Studio polls while the status is "running" (common.watch).
    status = "running" if row["status"] == "queued" else row["status"]
    out = {"job": row["id"], "status": status, "mode": row["mode"], "start": row["start_s"],
           "end": row["end_s"], "cached": bool(row["cached"]), "credits": round(float(row.get("credits") or 0), 4)}
    if row["status"] == "queued":
        out["queued"] = True
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


def _question(sha: str) -> str:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT question FROM ranking_watch_questions WHERE sha=?"), (sha,)).fetchone()
    return str(row["question"]) if row else ""


def _spool_path(job_id: str) -> Path:
    return SPOOL / f"{job_id}.bin"


# ── the fair dispatcher: jobs this process holds, run a few per account at a time ───────────────────────────────────
_INSTANCE = secrets.token_hex(6)
_dispatch_lock = threading.Lock()
_waiting: "OrderedDict[int, deque]" = OrderedDict()      # account -> its jobs waiting, accounts in turn order
_running: dict[int, int] = {}
_held: dict[str, int] = {}                                # job -> account: every job this process holds (heartbeated)
_busy = {"n": 0}


def _reset_dispatch():
    """Forget every job this process holds (tests simulate a restart with it)."""
    with _dispatch_lock:
        _waiting.clear()
        _running.clear()
        _held.clear()
        _busy["n"] = 0


def _queued_here() -> int:
    with _dispatch_lock:
        return sum(len(q) for q in _waiting.values())


def _enqueue(job_id: str, user_id: int):
    with _dispatch_lock:
        if job_id in _held:
            return
        _held[job_id] = user_id
        _waiting.setdefault(user_id, deque()).append(job_id)
    _pump()


def _next_locked():
    """The next job: accounts under their fair share first, in turn; while workers would sit idle, an account may run
    more, up to its hard limit."""
    for limit in (ACCOUNT_RUNNING, ACCOUNT_RUNNING_MAX):
        for uid in list(_waiting):
            q = _waiting[uid]
            if q and _running.get(uid, 0) < limit:
                job = q.popleft()
                if q:
                    _waiting.move_to_end(uid)
                else:
                    del _waiting[uid]
                return uid, job
    return None


def _pump():
    while True:
        with _dispatch_lock:
            if _busy["n"] >= WORKERS:
                return
            nxt = _next_locked()
            if not nxt:
                return
            uid, job = nxt
            _busy["n"] += 1
            _running[uid] = _running.get(uid, 0) + 1
        try:
            _pool.submit(_work, job, uid)
        except RuntimeError:                                    # shutting down: the next process picks the job up
            _release(job, uid, pump=False)
            return


def _release(job_id: str, uid: int, pump: bool = True):
    with _dispatch_lock:
        _busy["n"] = max(0, _busy["n"] - 1)
        _running[uid] = max(0, _running.get(uid, 0) - 1)
        if not _running[uid]:
            del _running[uid]
        _held.pop(job_id, None)
    if pump:
        _pump()


def _work(job_id: str, uid: int):
    try:
        _run(job_id)
    except Exception as exc:                                    # noqa: BLE001 - a worker must never die silently
        print(f"[ranking-watch] {job_id}: {exc}", flush=True)
    finally:
        _release(job_id, uid)


def _finish(job_id: str, row: dict, status: str, result: str, error: str, tin: int, tout: int, cost: float) -> bool:
    """Record a job's end. Google's bill is recorded whatever happened (a job given up meanwhile still cost what it
    cost); the outcome only while the job is still this process's to finish. A failed watch's credits come back."""
    with db._conn() as conn:
        conn.execute(db._q("UPDATE ranking_watch_jobs SET tokens_in=?, tokens_out=?, cost_usd=? WHERE id=?"),
                     (tin, tout, cost, job_id))
        changed = conn.execute(db._q("""UPDATE ranking_watch_jobs SET status=?, result_json=?, error=?, updated_at=?
            WHERE id=? AND status IN ('queued','running') AND owner=?"""),
            (status, result, error, time.time(), job_id, _INSTANCE)).rowcount
    if changed and status == "failed" and float(row.get("credits") or 0) > 0:
        refund(row["user_id"], float(row["credits"]))           # a watch that failed is never paid for
    return bool(changed)


def _run(job_id: str):
    row = _row(job_id)
    if not row or row["status"] not in ("queued", "running") or row.get("owner") != _INSTANCE:
        return                                                  # finished, given up, or another process's now
    now = time.time()
    with db._conn() as conn:
        if not conn.execute(db._q("""UPDATE ranking_watch_jobs SET status='running', started_at=?, heartbeat_at=?
                WHERE id=? AND status IN ('queued','running') AND owner=?"""), (now, now, job_id, _INSTANCE)).rowcount:
            return
    question = _question(row["question_sha"])
    if not question:
        _finish(job_id, row, "failed", "", STOPPED, 0, 0, 0.0)
        return
    mode = MODES[row["mode"]]
    mime = row.get("mime") or "video/mp4"
    uri, name = row.get("file_uri") or "", row.get("file_name") or ""
    spool = _spool_path(job_id)
    tin = tout = 0
    try:
        if row["source"].startswith("file:") and not uri:
            data = spool.read_bytes()                           # gone: the process that had it was restarted
            got = sw.upload(data, mime)
            del data
            uri, name = got["uri"], got.get("name") or ""
            with db._conn() as conn:                            # a restart mid-watch reuses this upload
                conn.execute(db._q("UPDATE ranking_watch_jobs SET file_uri=?, file_name=? WHERE id=?"),
                             (uri, name, job_id))
        log, tin, tout = sw.watch_once("" if uri else row["source"], row["start_s"], row["end_s"], file_uri=uri,
                                       mime=mime, question=question, fps=mode["fps"], low_res=mode["low_res"])
        outcome = ("done", json.dumps(log), "")
    except FileNotFoundError:
        outcome = ("failed", "", LOST_ERROR)
    except sw.WatchFailed as exc:                               # answered and billed, but unusable
        print(f"[ranking-watch] {row['source']} {row['start_s']}-{row['end_s']}: {exc}", flush=True)
        tin, tout = exc.tokens_in, exc.tokens_out
        outcome = ("failed", "", str(exc)[:300])
    except Exception as exc:                                    # noqa: BLE001 - recorded on the job
        print(f"[ranking-watch] {row['source']} {row['start_s']}-{row['end_s']}: {exc}", flush=True)
        outcome = ("failed", "", str(exc)[:300])
    finally:
        spool.unlink(missing_ok=True)
        if name:
            sw.delete_file(name)                                # Gemini keeps uploads 2 days against a quota
    _finish(job_id, row, *outcome, tin, tout, sw.cost(tin, tout))


def _adopt(row: dict) -> str:
    """A queued or running job whose process stopped heartbeating (a deploy, a crash): claimed by this process and
    queued again ("adopted"), or given up and refunded ("failed"; "lost" for an upload whose bytes are gone, which
    the Studio is told is not found, so it sends the clip again). "" when another process got there first."""
    now = time.time()
    attempts = int(row.get("attempts") or 0) + 1
    upload_gone = row["source"].startswith("file:") and not row.get("file_uri") and not _spool_path(row["id"]).is_file()
    if now - float(row["created_at"]) > STALE_S or attempts > MAX_ATTEMPTS:
        verdict, error = "failed", STOPPED
    elif upload_gone:
        verdict, error = "lost", LOST_ERROR
    else:
        verdict, error = "adopted", ""
    with db._conn() as conn:
        if verdict == "adopted":
            changed = conn.execute(db._q("""UPDATE ranking_watch_jobs SET status='queued', owner=?, heartbeat_at=?,
                attempts=? WHERE id=? AND status IN ('queued','running') AND heartbeat_at<?"""),
                (_INSTANCE, now, attempts, row["id"], now - LOST_S)).rowcount
        else:                                                   # a job that never started cost nothing
            changed = conn.execute(db._q("""UPDATE ranking_watch_jobs SET status='failed', error=?, updated_at=?,
                cost_usd=CASE WHEN started_at>0 THEN cost_usd ELSE 0 END
                WHERE id=? AND status IN ('queued','running') AND heartbeat_at<?"""),
                (error, now, row["id"], now - LOST_S)).rowcount
    if not changed:
        return ""
    if verdict == "adopted":
        print(f"[ranking-watch] picked up {row['id']} again (attempt {attempts})", flush=True)
        _enqueue(row["id"], int(row["user_id"]))
    elif float(row.get("credits") or 0) > 0:
        refund(row["user_id"], float(row["credits"]))
    return verdict


# ── the supervisor: heartbeats, lost jobs, abandoned cleans ─────────────────────────────────────────────────────────
_supervisor = {"thread": None, "pruned": 0.0}
_supervisor_lock = threading.Lock()


def _ensure_supervisor():
    """Started by the first ranking request a process serves (a deploy's first poll recovers what the old process
    left), and only in the real server (tests swap the pool and drive everything by hand)."""
    if _supervisor["thread"] is not None or _pool is not _REAL_POOL:
        return
    with _supervisor_lock:
        if _supervisor["thread"] is None:
            _supervisor["thread"] = threading.Thread(target=_supervise, name="ranking-supervisor", daemon=True)
            _supervisor["thread"].start()


def start_supervisor():
    """For the server's startup, so jobs a deploy stranded are picked up even before anyone polls."""
    _ensure_supervisor()


def _supervise():
    while True:
        try:
            _tick()
        except Exception as exc:                                # noqa: BLE001 - try again next beat
            print(f"[ranking-watch] supervisor: {exc}", flush=True)
        time.sleep(HEARTBEAT_S)


def _tick():
    initialize()
    now = time.time()
    with _dispatch_lock:
        held = list(_held)
    with db._conn() as conn:
        for i in range(0, len(held), 200):
            chunk = held[i:i + 200]
            conn.execute(db._q("UPDATE ranking_watch_jobs SET heartbeat_at=? WHERE id IN (" + ",".join("?" * len(chunk))
                               + ")"), (now, *chunk))
        lost = [dict(r) for r in conn.execute(db._q("""SELECT * FROM ranking_watch_jobs WHERE status IN
            ('queued','running') AND heartbeat_at<? ORDER BY created_at LIMIT 200"""), (now - LOST_S,)).fetchall()]
    for row in lost:
        if row["id"] not in held:
            _adopt(row)
    _sweep_cleans()
    if now - _supervisor["pruned"] > 3600:
        _supervisor["pruned"] = now
        with db._conn() as conn:                                # instructions are only needed while a job may restart
            conn.execute(db._q("DELETE FROM ranking_watch_questions WHERE used_at<?"), (now - 2 * 86400,))
        for f in SPOOL.glob("*.bin") if SPOOL.is_dir() else []:
            try:
                if now - f.stat().st_mtime > 2 * STALE_S:
                    f.unlink()
            except OSError:
                pass


# ── starting and polling a watch ────────────────────────────────────────────────────────────────────────────────────
def _lock(conn, user_id: int, key: str):
    """The account's allowance and this exact watch are decided one request at a time (per account and per watch, not
    one lock for the whole site)."""
    if not db.IS_PG:
        conn.execute("BEGIN IMMEDIATE")
        return
    conn.execute("SELECT pg_advisory_xact_lock(726346122, %s::int)", (int(hashlib.sha256(key.encode()).hexdigest()[:7], 16),))
    conn.execute("SELECT pg_advisory_xact_lock(726346121, %s::int)", (int(user_id) % 2147483647,))


def _refuse(status: int, detail: str, reason: str, retry_after: int):
    raise HTTPException(status, detail, headers={"Retry-After": str(retry_after), "X-CR-Reason": reason})


def start(user: dict, source: str, start_s: float, end_s: float, mode: str, question: str, *, use_credits=False,
          data: bytes = b"", mime: str = "video/mp4") -> dict:
    """A cached log, the running job for the same watch, or a new watch charged to the allowance (or credits)."""
    initialize()
    _ensure_supervisor()
    if mode not in MODES:
        raise HTTPException(400, "Choose scout or detail.")
    question = (question or "").strip()
    if not question:
        raise HTTPException(400, "Say what to look for.")
    a, b = int(max(0, start_s)), int(end_s)
    longest = MODES[mode]["max_s"]
    if longest < b - a <= longest + WINDOW_SLACK_S:
        b = a + longest                                         # rounding slop: watch the longest window allowed
    if not 2 <= b - a <= longest:
        raise HTTPException(400, f"Watch between 2 and {longest} seconds at a time in {mode} mode.")
    if not sw._key():
        raise HTTPException(503, "Clip watching isn't available right now.")
    now, name = time.time(), sw.model()
    qsha = hashlib.sha256(question.encode()).hexdigest()[:20]
    admin, uid, is_file = is_admin(user), int(user["id"]), source.startswith("file:")
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO ranking_watch_questions (sha, question, used_at) VALUES (?,?,?)
            ON CONFLICT(sha) DO UPDATE SET used_at=excluded.used_at"""), (qsha, question, now))
    same_key = f"{source}|{a}|{b}|{mode}|{qsha}|{name}"
    cap, estimate = daily_cap(), _estimate(mode, b - a)        # before the lock: it may need its own connection
    with _dispatch_lock:
        held = set(_held)
    with db._conn() as conn:
        _lock(conn, uid, same_key)
        args = (source, a, b, mode, qsha, name)
        # A lost job for the same watch is replaced by this one, not run twice. It is marked lost, not stopped: the
        # Studio that asked for it (perhaps another account's, for a shared public clip) is then told "not found" on
        # its next poll and asks once more, which lands on this job (or its cached answer). A stopped job made that
        # Studio drop the candidate while this one finished.
        superseded = [dict(r) for r in conn.execute(db._q("""SELECT * FROM ranking_watch_jobs WHERE source=? AND
            start_s=? AND end_s=? AND mode=? AND question_sha=? AND model=? AND status IN ('queued','running')
            AND heartbeat_at<?"""), (*args, now - LOST_S)).fetchall() if r["id"] not in held]
        for row in superseded:
            conn.execute(db._q("""UPDATE ranking_watch_jobs SET status='failed', error=?, updated_at=?,
                cost_usd=CASE WHEN started_at>0 THEN cost_usd ELSE 0 END WHERE id=?"""), (LOST_ERROR, now, row["id"]))
        # someone's upload being watched right now is theirs alone: another account couldn't poll it (404)
        same = conn.execute(db._q("""SELECT * FROM ranking_watch_jobs WHERE source=? AND start_s=? AND end_s=? AND
            mode=? AND question_sha=? AND model=? AND (status='done' OR (status IN ('queued','running')
            AND heartbeat_at>? AND (?=0 OR user_id=?))) ORDER BY status='done' DESC, created_at DESC LIMIT 1"""),
            (*args, now - LOST_S, int(is_file), uid)).fetchone()
        if same and same["status"] == "done":                   # watched before: free and instant for everyone
            job = "rw_" + secrets.token_hex(10)
            conn.execute(db._q("""INSERT INTO ranking_watch_jobs (id,user_id,source,start_s,end_s,mode,question_sha,
                model,status,result_json,cached,created_at,updated_at,heartbeat_at) VALUES
                (?,?,?,?,?,?,?,?,'done',?,1,?,?,?)"""),
                (job, uid, source, a, b, mode, qsha, name, same["result_json"], now, now, now))
            result = public({**dict(same), "id": job, "cached": 1, "credits": 0}, admin)
        elif same:                                              # being watched right now
            result = public({**dict(same), "credits": 0}, admin)
        else:
            result = None
            limit = plans.ranking_watch_limit(user.get("plan"), is_admin=admin)
            charge = 0.0
            if limit is not None and _used(conn, uid, now) >= limit:
                if not use_credits:
                    raise HTTPException(402, f"You've used this month's {limit} clip watches. Each further watch costs "
                                             f"{WATCH_CREDITS} credits; top up credits or wait for next month.")
                charge = WATCH_CREDITS
            if not admin:
                waiting = conn.execute(db._q("""SELECT COUNT(*) AS n FROM ranking_watch_jobs WHERE user_id=? AND
                    created_at>? AND status IN ('queued','running') AND heartbeat_at>?"""),
                    (uid, now - 86400, now - LOST_S)).fetchone()["n"]
                if int(waiting or 0) >= ACCOUNT_WAITING:
                    _refuse(429, BUSY_RETRY, "account-busy", 15)
            if _queued_here() >= QUEUE_MAX:
                _refuse(429, BUSY_RETRY, "queue-full", 15)
            spent = _spent_today(conn)
            if spent + estimate > cap:
                _alert("watching", spent + estimate, cap)
                _refuse(503, "Clip watching is busy today across ChannelRecipe (today's watching budget is used). "
                             "Your progress is saved and nothing was charged; make the Short again in a few hours.",
                        "global-cap", 3600)
            if not admin and _spent_today(conn, uid) + estimate > account_share() * cap:
                print(f"[ranking-cap] account {uid} reached its share of today's watching "
                      f"(${account_share() * cap:.2f})", flush=True)
                _refuse(503, "Clip watching is busy today for this account: it has used its share of today's "
                             "watching. Your progress is saved and nothing was charged; make the Short again tomorrow.",
                        "account-share", 3600 * 6)
            job = "rw_" + secrets.token_hex(10)
            conn.execute(db._q("""INSERT INTO ranking_watch_jobs (id,user_id,source,start_s,end_s,mode,question_sha,model,
                status,credits,created_at,updated_at,mime,owner,heartbeat_at,cost_usd) VALUES
                (?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?)"""),
                (job, uid, source, a, b, mode, qsha, name, charge, now, now, mime, _INSTANCE, now, estimate))
    for row in superseded:
        if float(row.get("credits") or 0) > 0:
            refund(row["user_id"], float(row["credits"]))
    if result is not None:
        return result
    _alert("watching", spent + estimate, cap)
    if charge and not deduct(uid, charge):
        with db._conn() as conn:
            conn.execute(db._q("""UPDATE ranking_watch_jobs SET status='failed', error=?, credits=0, cost_usd=0,
                updated_at=? WHERE id=?"""), ("Not enough credits.", time.time(), job))
        raise HTTPException(402, "Not enough credits for more clip watches. Top up credits to continue.")
    if data:
        try:                                                    # on disk, not in memory, until a worker takes it
            SPOOL.mkdir(parents=True, exist_ok=True)
            tmp = _spool_path(job).with_suffix(".part")
            tmp.write_bytes(data)
            os.replace(tmp, _spool_path(job))
        except OSError as exc:
            print(f"[ranking-watch] could not keep the upload for {job}: {exc}", flush=True)
            with db._conn() as conn:
                conn.execute(db._q("""UPDATE ranking_watch_jobs SET status='failed', error=?, cost_usd=0, updated_at=?
                    WHERE id=?"""), (STOPPED, time.time(), job))
            if charge:
                refund(uid, charge)
            raise HTTPException(503, "Clip watching isn't available right now.") from None
    _enqueue(job, uid)
    return public(_row(job), admin)


def get(user: dict, job_id: str) -> dict:
    initialize()
    _ensure_supervisor()
    row = _row(job_id) if isinstance(job_id, str) and job_id.startswith("rw_") else None
    # Any Studio account may poll a job it was given (a shared watch of a public clip); an upload's job only its owner.
    if not row or (row["source"].startswith("file:") and row["user_id"] != user["id"] and not is_admin(user)):
        raise HTTPException(404, NOT_FOUND)
    with _dispatch_lock:
        mine = job_id in _held
    if row["status"] in ("queued", "running") and not mine and float(row.get("heartbeat_at") or 0) < time.time() - LOST_S:
        _adopt(row)                                             # its process died: pick it up (or give it up) now
        row = _row(job_id) or row
    if row["status"] == "failed" and row["error"] == LOST_ERROR:
        raise HTTPException(404, NOT_FOUND)                     # the Studio sends the clip again (deduped)
    return public(row, is_admin(user))


# ── searches and cloud cleans: per-account days, a global spend cap, the cache ──────────────────────────────────────
SERVICES = {
    # kind: (label for messages, per-account daily count attr, global daily USD env and default, unit cost env)
    "tiktok": ("TikTok searches", "TIKTOK_DAILY", ("RANKING_TIKTOK_DAILY_USD", 10.0)),
    "youtube": ("YouTube searches", "YOUTUBE_SEARCH_DAILY", ("RANKING_YOUTUBE_SEARCH_DAILY_USD", 10.0)),
    "clean": ("cloud cleans", "CLEAN_DAILY", ("RANKING_CLEAN_DAILY_USD", 25.0)),
}
FALLBACK = {"tiktok": "YouTube search still works.", "youtube": "Studio searches YouTube itself meanwhile.",
            "clean": "Studio cleans clips itself meanwhile."}
_breaker: dict[str, dict] = {}
_breaker_lock = threading.Lock()


def _service_cap(kind: str) -> float:
    env, default = SERVICES[kind][2]
    return _env_float(env, default)


def _admit(kind: str, user: dict, estimate: float):
    """Refuse a search or clean when this account has used today's (429, the client stops asking) or ChannelRecipe has
    used today's budget for it (503 "busy today"). Counted from the database, so a deploy resets nothing."""
    label, attr, _ = SERVICES[kind]
    if is_admin(user):
        daily = None
    else:
        daily = int(globals()[attr])
    now, uid = time.time(), int(user["id"])
    with db._conn() as conn:
        mine = conn.execute(db._q("""SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS s FROM ranking_calls
            WHERE user_id=? AND kind=? AND status<>'failed' AND created_at>=?"""), (uid, kind, day_start(now))).fetchone()
        spent = float(conn.execute(db._q("""SELECT COALESCE(SUM(cost_usd),0) AS s FROM ranking_calls WHERE kind=?
            AND created_at>?"""), (kind, now - 86400)).fetchone()["s"] or 0)
    cap = _service_cap(kind)
    # Retry-After 1: the Studio's bridge asks a 429 again three times, sleeping Retry-After each time; a daily
    # limit won't lift in seconds, so those retries should cost the Short 3 s, not a minute.
    if daily is not None and int(mine["n"] or 0) >= daily:
        _refuse(429, f"Today's {label} are used up. {FALLBACK[kind]}", "account-daily", 1)
    if spent + estimate > cap:
        _alert(kind, spent + estimate, cap)
        _refuse(503, f"ChannelRecipe's {label} are busy today. {FALLBACK[kind]}", "global-cap", 3600)
    if daily is not None and float(mine["s"] or 0) + estimate > account_share() * cap:
        _refuse(429, f"Today's {label} are used up. {FALLBACK[kind]}", "account-share", 1)
    _alert(kind, spent + estimate, cap)


def _breaker_open(kind: str) -> bool:
    with _breaker_lock:
        return time.time() < _breaker.get(kind, {}).get("until", 0)


def _breaker_note(kind: str, ok: bool):
    """Five failures in a row and ScrapeCreators or PiAPI is left alone for a minute (every Short would otherwise
    wait 40 s a search on a service that is down)."""
    with _breaker_lock:
        b = _breaker.setdefault(kind, {"fails": 0, "until": 0.0})
        if ok:
            b.update(fails=0, until=0.0)
            return
        b["fails"] += 1
        if b["fails"] >= 5:
            b.update(fails=0, until=time.time() + 60)
            print(f"[ranking-{kind}] five failures in a row: paused for a minute", flush=True)


def _record(kind: str, user: dict, status: str, *, key_sha="", result=None, cost=0.0, error="", task="",
            object_key="", job=None) -> str:
    job = job or f"r{kind[0]}_" + secrets.token_hex(10)
    now = time.time()
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO ranking_calls (id,user_id,kind,key_sha,status,result_json,error,task,object_key,
            cost_usd,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""),
            (job, int(user["id"]), kind, key_sha, status, json.dumps(result) if result is not None else "", error[:300],
             task, object_key, float(cost), now, now))
    return job


def _cached(kind: str, key_sha: str):
    with db._conn() as conn:
        row = conn.execute(db._q("""SELECT result_json FROM ranking_calls WHERE kind=? AND key_sha=? AND status='done'
            AND created_at>? ORDER BY created_at DESC LIMIT 1"""), (kind, key_sha, time.time() - SEARCH_CACHE_S)).fetchone()
    try:
        return json.loads(row["result_json"]) if row and row["result_json"] else None
    except ValueError:
        return None


def _search(kind: str, user: dict, query: str, variant: str, fetch) -> dict:
    """One ScrapeCreators search through the cache, the account's day, the global cap and the breaker."""
    key = os.getenv("SCRAPECREATORS_API_KEY", "").strip()
    label = {"tiktok": "TikTok search", "youtube": "YouTube search"}[kind]
    if not key:
        raise HTTPException(503, f"{label} is not set up yet. {FALLBACK[kind]}")
    query = re.sub(r"\s+", " ", query or "").strip()
    key_sha = hashlib.sha256(f"{kind}|{variant}|{query.lower()}".encode()).hexdigest()[:24]
    initialize()
    hit = _cached(kind, key_sha)
    if hit is not None:                                         # the same search lately: free, and not counted
        return {"videos": hit, "cached": True}
    if _breaker_open(kind):
        raise HTTPException(503, f"{label} isn't answering right now. {FALLBACK[kind]}")
    unit = _env_float("RANKING_SCRAPECREATORS_USD", 0.002)
    _admit(kind, user, unit)
    try:
        videos, credits = fetch(key, query)
    except Exception as exc:                                    # noqa: BLE001 - any upstream trouble: the client falls back
        print(f"[ranking-{kind}] {query!r}: {exc}", flush=True)
        _breaker_note(kind, False)
        _record(kind, user, "failed", key_sha=key_sha, error=str(exc))
        raise HTTPException(502, f"{label} didn't answer. {FALLBACK[kind]}") from None
    _breaker_note(kind, True)
    _record(kind, user, "done", key_sha=key_sha, result=videos, cost=unit * max(1, credits))
    return {"videos": videos}


class TikTokSearch(BaseModel):
    query: str = Field(..., min_length=2, max_length=120)


def _tiktok_fetch(key: str, query: str):
    import requests
    # The full answer (the documented shape: search_item_list[].aweme_info). With trim=true every search came back
    # empty in production: the trimmed items are shaped differently and none parsed (2026-10-21). Both shapes read.
    r = requests.get(TIKTOK_URL, params={"query": query}, headers={"x-api-key": key}, timeout=(10, 40))
    body = r.json() if r.status_code == 200 else {}
    items = body.get("search_item_list") or body.get("videos") or body.get("data") or []
    if not items and not body.get("success"):
        raise RuntimeError(f"HTTP {r.status_code}")
    videos, seen = [], set()
    for item in items if isinstance(items, list) else []:
        item = item if isinstance(item, dict) else {}
        a = item.get("aweme_info") if isinstance(item.get("aweme_info"), dict) else item
        vid = str(a.get("aweme_id") or a.get("id") or a.get("video_id") or "")
        if not vid.isdigit() or vid in seen:
            continue
        seen.add(vid)
        au = a.get("author") if isinstance(a.get("author"), dict) else {}
        author = str(au.get("unique_id") or au.get("uniqueId") or a.get("author_unique_id") or
                     (a.get("author") if isinstance(a.get("author"), str) else "") or "")
        st = a.get("statistics") if isinstance(a.get("statistics"), dict) else (a.get("stats") or {})
        vd = a.get("video") if isinstance(a.get("video"), dict) else {}
        dur = float(vd.get("duration") or a.get("duration") or 0)
        videos.append({"id": vid, "url": a.get("share_url") or a.get("url") or f"https://www.tiktok.com/@{author}/video/{vid}",
                       "title": str(a.get("desc") or a.get("description") or a.get("title") or "")[:300], "channel": author,
                       "views": _count(st.get("play_count") if st.get("play_count") is not None else
                                       st.get("playCount") if st.get("playCount") is not None else a.get("play_count")),
                       "dur": round(dur / 1000 if dur > 1000 else dur, 1)})
    return videos, int(body.get("credits_charged") or 1)


def tiktok_search(user: dict, query: str) -> dict:
    """TikTok videos for a search, most relevant first: [{id, url, title, channel, views, dur}]."""
    return _search("tiktok", user, query, "", _tiktok_fetch)


class YouTubeSearch(BaseModel):
    query: str = Field(..., min_length=2, max_length=120)
    shorts: bool = False


def _count(value) -> int:
    """A view count from ScrapeCreators: viewCountInt, or a text like "1.2M views"."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    m = re.search(r"([\d.,]+)\s*([KMB]?)", str(value or ""), re.I)
    if not m:
        return 0
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(n * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()])


def _length(item: dict) -> float:
    try:
        if item.get("lengthSeconds") not in (None, ""):
            return float(item["lengthSeconds"])
    except (TypeError, ValueError):
        pass
    parts = re.findall(r"\d+", str(item.get("lengthText") or ""))
    return float(sum(int(p) * 60 ** i for i, p in enumerate(reversed(parts[-3:])))) if parts else 0.0


def _youtube_items(body: dict, shorts: bool) -> list:
    """ScrapeCreators' answer in the contract's shape. A length it doesn't give is null, never 0: Shorts in a search
    usually come without one, and the Studio reads null as "about 45 s" for a Short but drops a 0 s video, so every
    Short of a search went missing without a word (the server had answered, so yt-dlp never ran instead)."""
    lists = [body.get("shorts"), body.get("videos")] if shorts else [body.get("videos"), body.get("shorts")]
    items = [it for group in lists for it in (group or []) if isinstance(it, dict)]
    for shelf in body.get("shelves") or []:                     # Shorts shelves inside a video search
        items += [it for it in (shelf or {}).get("items") or [] if isinstance(it, dict)]
    out, seen = [], set()
    for it in items:
        vid = str(it.get("id") or "")
        if not _VIDEO.fullmatch(vid) or vid in seen:
            continue
        seen.add(vid)
        ch = it.get("channel") if isinstance(it.get("channel"), dict) else {}
        is_short = str(it.get("type") or "").lower() == "short" or "/shorts/" in str(it.get("url") or "")
        length = _length(it)
        out.append({"id": vid, "title": str(it.get("title") or "")[:300],
                    "channel": str(ch.get("title") or ch.get("handle") or "")[:120],
                    "channel_id": str(ch.get("id") or "")[:64],
                    "dur": round(length, 1) if math.isfinite(length) and length > 0 else None,
                    "views": _count(it.get("viewCountInt") if it.get("viewCountInt") is not None
                                    else it.get("viewCountText")),
                    "shorts": is_short})
    return out


def youtube_search(user: dict, query: str, shorts: bool = False) -> dict:
    """YouTube videos (or Shorts) for a search, most relevant first: [{id, title, channel, channel_id, dur, views,
    shorts}] (dur null when ScrapeCreators doesn't say). The Studio falls back to yt-dlp on any error, so this only
    spares customers' computers YouTube's bot wall; it is never required."""
    def fetch(key, q):
        import requests
        r = requests.get(YOUTUBE_URL, params={"query": q, "type": "shorts" if shorts else "videos",
                                              "sortBy": "relevance"}, headers={"x-api-key": key}, timeout=(10, 40))
        body = r.json() if r.status_code == 200 else {}
        if not isinstance(body, dict) or body.get("success") is False or not (
                body.get("videos") or body.get("shorts") or body.get("shelves") or body.get("success")):
            raise RuntimeError(f"HTTP {r.status_code}")
        return _youtube_items(body, shorts), int(body.get("credits_charged") or 1)
    return _search("youtube", user, query, "shorts" if shorts else "videos", fetch)


class CleanRequest(BaseModel):
    data_b64: str
    region: dict | None = None            # {"x", "y", "width", "height"} in the clip's own pixels; None: found by PiAPI


def _clip_seconds(data: bytes) -> float | None:
    """The clip's length by ffprobe (PiAPI bills by the second), or None when it can't be read here."""
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(data)
            path = f.name
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30).stdout
        return float(out.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    finally:
        if path:
            Path(path).unlink(missing_ok=True)


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
    longest = _env_float("RANKING_CLEAN_MAX_S", 30.0)
    seconds = _clip_seconds(data)
    if seconds is not None and seconds > longest + 0.5:          # a Short's clip is 3-12 s; billing is per second
        raise HTTPException(400, f"Send a clip of at most {int(longest)} seconds for cloud text removal.")
    estimate = (seconds if seconds else longest) * _env_float("RANKING_CLEAN_USD_PER_S", 0.006)
    initialize()
    if _breaker_open("clean"):
        raise HTTPException(503, "Cloud text removal isn't answering right now. Studio cleans the clip itself.")
    _admit("clean", user, estimate)
    job = "rc_" + secrets.token_hex(10)
    object_key = f"private/ranking-clean/{job}.mp4"          # its own name: two accounts' identical clips never share
    client = storage._make_client()                           # private: PiAPI reads it by a signed link
    client.put_object(Bucket=storage._spaces_creds()[2], Key=object_key, Body=data, ContentType="video/mp4")
    del data
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
        _breaker_note("clean", False)
        _record("clean", user, "failed", error="PiAPI did not start", job=job)
        raise HTTPException(502, "Cloud text removal didn't start. Studio cleans the clip itself.")
    _breaker_note("clean", True)
    _record("clean", user, "running", task=str(task), object_key=object_key, cost=estimate, job=job)
    return {"job": job, "status": "running"}


def _clean_row(job: str) -> dict | None:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM ranking_calls WHERE id=? AND kind='clean'"), (job,)).fetchone()
    return dict(row) if row else None


def _clean_end(row: dict, status: str, video_url: str = "", error: str = "") -> bool:
    from webapp import storage
    with db._conn() as conn:
        changed = conn.execute(db._q("""UPDATE ranking_calls SET status=?, result_json=?, error=?, updated_at=?,
            cost_usd=CASE WHEN ?='failed' THEN 0 ELSE cost_usd END WHERE id=? AND status='running'"""),
            (status, json.dumps({"video_url": video_url}) if video_url else "", error[:300], time.time(), status,
             row["id"])).rowcount
    if changed and row.get("object_key"):
        try:
            storage.delete_key(row["object_key"])
        except Exception as exc:                                # noqa: BLE001 - swept again later
            print(f"[ranking-clean] could not delete {row['object_key']}: {exc}", flush=True)
    return bool(changed)


def clean_get(user: dict, job: str) -> dict:
    import requests
    initialize()
    row = _clean_row(job) if isinstance(job, str) else None
    if not row or int(row["user_id"]) != int(user["id"]):
        raise HTTPException(404, "That clean was not found.")
    if row["status"] == "running":
        try:
            r = requests.get(f"{PIAPI_URL}/{row['task']}", headers={"x-api-key": os.getenv("PIAPI_API_KEY", "")},
                             timeout=(10, 30))
            data = (r.json() or {}).get("data") or {}
        except (requests.RequestException, ValueError):
            data = {}
        status = str(data.get("status") or "")
        if status == "completed" and (data.get("output") or {}).get("video_url"):
            _clean_end(row, "done", video_url=data["output"]["video_url"])
        elif status in ("failed", "canceled") or time.time() - float(row["created_at"]) > CLEAN_GIVE_UP_S:
            _clean_end(row, "failed", error=str((data.get("error") or {}).get("message") or "PiAPI could not clean it"))
        row = _clean_row(job) or row
    try:
        url = json.loads(row["result_json"]).get("video_url", "") if row["result_json"] else ""
    except ValueError:
        url = ""
    return {"status": row["status"], "video_url": url, "error": row["error"]}


def _sweep_cleans():
    """Cleans nobody asked about again (the Short stopped, the Studio closed): given up, their private copy deleted."""
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(db._q("""SELECT * FROM ranking_calls WHERE kind='clean' AND
            status='running' AND created_at<? LIMIT 100"""), (time.time() - CLEAN_GIVE_UP_S,)).fetchall()]
    for row in rows:
        _clean_end(row, "failed", error="Nobody was waiting for this clean any more.")


# ── routes ──────────────────────────────────────────────────────────────────────────────────────────────────────────
class WatchRequest(BaseModel):
    video_id: str
    start: float = 0
    end: float
    mode: str = "scout"
    question: str = Field(..., max_length=20000)
    use_credits: bool = False


class UploadWatchRequest(BaseModel):
    """What /watch-upload takes (read by hand with a size guard, so a huge body is refused before it is parsed)."""
    data_b64: str
    mime: str = "video/mp4"
    start: float = 0
    end: float
    mode: str = "scout"
    question: str = Field(..., max_length=20000)
    use_credits: bool = False


_limiters: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
LIMITS = {"quick": ("RANKING_ROUTE_THREADS", 32), "slow": ("RANKING_SLOW_ROUTE_THREADS", 16)}


async def _call(kind: str, fn, *args, **kwargs):
    """Blocking work on the ranking routes' own thread limit: FastAPI's def routes share 40 threads with the whole
    website, and slow searches or cleans could fill them all."""
    import asyncio
    import anyio
    loop = asyncio.get_running_loop()
    per_loop = _limiters.setdefault(loop, {})
    if kind not in per_loop:
        name, default = LIMITS[kind]
        per_loop[kind] = anyio.CapacityLimiter(max(2, _env_int(name, default)))
    return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs), limiter=per_loop[kind])


_inflight = {"bytes": 0}
_inflight_lock = threading.Lock()


@asynccontextmanager
async def _upload_room(request: Request, limit: int, too_big: str):
    """Room for one upload's body in this process's memory budget: a size it declares over the limit is refused
    before a byte is read (413), and when many arrive at once the client is asked to wait (each 24 MB upload once
    took about 130 MB of server memory, and 20 at once could have the whole website killed)."""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        raise HTTPException(413, too_big)
    need = declared or limit
    with _inflight_lock:
        if _inflight["bytes"] and _inflight["bytes"] + need > UPLOAD_INFLIGHT_BYTES:
            raise HTTPException(429, BUSY_RETRY, headers={"Retry-After": "10", "X-CR-Reason": "uploads-busy"})
        _inflight["bytes"] += need
    try:
        yield
    finally:
        with _inflight_lock:
            _inflight["bytes"] = max(0, _inflight["bytes"] - need)


async def _read_json(request: Request, limit: int, too_big: str) -> dict:
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, too_big)
    try:
        body = json.loads(buf)
    except ValueError:
        raise HTTPException(400, "The request could not be read.") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "The request could not be read.")
    return body


def _field(body: dict, name: str, kind, default=None, required=False):
    value = body.get(name, default)
    if value is None and required:
        raise HTTPException(422, f"{name} is missing.")
    if kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(422, f"{name} must be a number.")
        return float(value)
    if kind is bool:
        return bool(value)
    if not isinstance(value, str):
        raise HTTPException(422, f"{name} must be text.")
    return value


def make_router(access) -> APIRouter:
    """`access` is the Studio dependency (a Studio plan, by session or Studio key)."""
    router = APIRouter(prefix="/api/studio/ranking", tags=["ChannelRecipe Studio"])
    no_store = {"Cache-Control": "private, no-store"}

    @router.get("/allowance")
    async def get_allowance(user=Depends(access)):
        return JSONResponse(await _call("quick", allowance, user), headers=no_store)

    @router.post("/watch")
    async def post_watch(req: WatchRequest, user=Depends(access)):
        if not _VIDEO.fullmatch(req.video_id or ""):
            raise HTTPException(400, "Choose a YouTube video to watch.")
        return await _call("quick", start, user, req.video_id, req.start, req.end, req.mode, req.question,
                           use_credits=req.use_credits)

    @router.post("/watch-upload")
    async def post_watch_upload(request: Request, user=Depends(access)):
        """A creator's own clip (the recipe's "my clips" mode), sent as a light copy."""
        too_big = f"Send an MP4, WebM or MOV copy under {sw.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        limit = sw.MAX_UPLOAD_BYTES * 4 // 3 + BODY_SLACK
        async with _upload_room(request, limit, too_big):
            body = await _read_json(request, limit, too_big)
            b64 = _field(body, "data_b64", str, required=True)
            mime = _field(body, "mime", str, "video/mp4")
            question = _field(body, "question", str, required=True)
            if len(question) > 20000:
                raise HTTPException(422, "question is too long.")
            if mime not in sw.UPLOAD_MIMES or len(b64) > sw.MAX_UPLOAD_BYTES * 4 // 3 + 8:
                raise HTTPException(400, too_big)
            try:
                data = base64.b64decode(b64, validate=True)
            except ValueError:
                raise HTTPException(400, "The clip could not be read.") from None
            del b64, body["data_b64"]
            if not data:
                raise HTTPException(400, "The clip is empty.")
            source = "file:" + hashlib.sha256(data).hexdigest()[:20]
            return await _call("quick", start, user, source, _field(body, "start", float, 0),
                               _field(body, "end", float, required=True), _field(body, "mode", str, "scout"),
                               question, use_credits=_field(body, "use_credits", bool, False), data=data, mime=mime)

    @router.get("/watch/{job_id}")
    async def get_watch(job_id: str, user=Depends(access)):
        return await _call("quick", get, user, job_id)

    @router.post("/tiktok-search")
    async def post_tiktok_search(req: TikTokSearch, user=Depends(access)):
        return await _call("slow", tiktok_search, user, req.query)

    @router.post("/youtube-search")
    async def post_youtube_search(req: YouTubeSearch, user=Depends(access)):
        return await _call("slow", youtube_search, user, req.query, req.shorts)

    @router.post("/clean")
    async def post_clean(request: Request, user=Depends(access)):
        too_big = "Send a clip under 60 MB."
        limit = CLEAN_MAX_BYTES * 4 // 3 + BODY_SLACK
        async with _upload_room(request, limit, too_big):
            body = await _read_json(request, limit, too_big)
            region = body.get("region")
            if region is not None and not isinstance(region, dict):
                raise HTTPException(400, "A region needs x, y, width and height.")
            return await _call("slow", clean_start, user, _field(body, "data_b64", str, required=True), region)

    @router.get("/clean/{job}")
    async def get_clean(job: str, user=Depends(access)):
        return await _call("slow", clean_get, user, job)

    return router
