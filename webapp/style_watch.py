"""Style learning: Gemini watches a reference video for a Studio style study.

Studio measures a reference on the customer's computer (cuts, motion, light, the
shape of the sound). What numbers can't say, Gemini watches straight from the
YouTube link with ChannelRecipe's own key, so customers need no Google account:
what kind of picture each shot is and how it's drawn, who keeps coming back,
what moves, what the music and sound effects do, what makes the channel
recognisable.

Bounded so a study can't run up the bill:
- only Studio plans, with a daily number of watches per account;
- fixed window lengths;
- a global daily spend cap (past it, studies continue with the free measurements);
- a shared cache: one window of a public video is watched once for everyone.
Every call records its tokens and cost.

A watch takes longer than the hosting's 100 s request limit, so it runs as a job:
POST starts it (or returns the cached log), GET polls it.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from webapp import database as db
from webapp import plans
from webapp.frontier_studio import is_admin

PROMPT_VERSION = 1
MAX_WINDOW_S = 200
STALE_S = 900
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
# A day's watches per account. A study watches up to three references, an opening and a later window each.
DAILY_WATCHES = {"daily": 12, "studio_pro": 24, "studio_max": 40}
_ready = set()
_ready_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="style-watch")
_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")

SOURCES = ("ai_image", "ai_video", "live_footage", "archival_footage", "archival_photo", "stock_footage",
           "news_clip", "bodycam_cctv", "interview", "screenshot_web", "document", "map", "chart", "3d_render",
           "drawing", "painting", "stick_figure", "cutout", "manga_panel", "motion_graphic", "text_card",
           "meme_gif", "presenter", "avatar", "other")
MOTIONS = ("still", "zoom_in", "zoom_out", "pan", "shake", "animated", "live_action", "motion_graphic",
           "cutout_move")

WATCH_PROMPT = """You are logging the PRODUCTION STYLE of this YouTube video between [A] and [B], so that a studio
can make new videos in the same style about other topics. Do not summarise the story; describe how it is made.
Watch every shot and listen to the whole soundtrack.

Return ONE JSON object with exactly these keys:
{
 "narration": "voiceover | presenter_on_camera | avatar | mixed | none",
 "shots": [   // every shot in order; a shot ends at any cut or transition
   {"t": "m:ss", "len_s": 2.5,
    "source": "<one of: [SOURCES]>",
    "art": "a few words on how this picture looks (e.g. 'watercolour painting, soft edges', 'low-poly 3D, flat
            pastel colours', 'grainy bodycam', 'glossy stock footage', 'black-and-white manga panel',
            'stick figure on a detailed painted background')",
    "motion": "<one of: [MOTIONS]>",
    "text": "none | caption | label | headline | quote | handwritten | kinetic | data | cta",
    "who": "short stable name of a recurring character or presenter in this shot, or empty",
    "audio": "narration | source_audio | music_only | ambience | silence"}
 ],
 "audio_events": [{"t": "m:ss", "kind": "music_in | music_change | music_out | music_drop | riser | hit | whoosh |
                   foley | ambience | source_clip | silence", "what": "a few words"}],
 "music": {"share": 0.0, "role": "story score | ambient bed | lofi | upbeat | none",
           "moods": ["..."], "instruments": ["..."], "under_voice": "loud | moderate | quiet | none",
           "changes": "how often and where the music changes"},
 "sfx": {"density": "none | sparse | moderate | dense", "kinds": ["..."], "on": "what triggers them"},
 "ambience": "environmental sound beds, or empty",
 "voice": {"gender": "...", "age": "young | adult | older", "accent": "...", "tone": "...",
           "pace": "slow | moderate | fast"},
 "look": {"grade": "dark | neutral | bright", "filter": "colour treatment, overlays, texture", "grain": "none | light |
          heavy", "palette": ["#RRGGBB"]},
 "art_style": "one or two sentences: the picture style a new video must keep, specific enough to prompt an image
               generator (medium, line, shading, colour, how characters are drawn, backgrounds)",
 "text_style": "how on-screen words look and move, or empty",
 "graphics": ["recurring graphic devices: maps, charts, highlighted documents, logos, timelines, counters ..."],
 "recurring": [{"name": "...", "look": "how to draw them consistently", "role": "mascot | presenter | character"}],
 "cta": [{"t": "m:ss", "what": "what is promoted and how it is shown (e.g. ebook cover and QR code)"}],
 "hook": "how the first seconds grab attention, if [A] is 0:00; else empty",
 "breathing": "where the edit pauses (music swells, empty shots, silence) and how long",
 "recognisable": ["the 3 to 6 things that make this channel instantly recognisable, most important first"]
}
Use the vocabularies exactly. "share" is the fraction of the window with audible music. If something is absent, use
an empty list, "none" or an empty string; never invent."""


def prompt(start: float, end: float) -> str:
    return (WATCH_PROMPT.replace("[A]", _mmss(start)).replace("[B]", _mmss(end))
            .replace("[SOURCES]", " | ".join(SOURCES)).replace("[MOTIONS]", " | ".join(MOTIONS)))


def _mmss(t: float) -> str:
    t = int(round(t))
    return f"{t // 60}:{t % 60:02d}"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def model() -> str:
    return (os.getenv("GEMINI_STYLE_MODEL") or "gemini-3.8-flash").strip()


def _key() -> str:
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def cost(tokens_in: int, tokens_out: int) -> float:
    """Dollars for one call, from the configured per-million prices (input includes video and audio)."""
    return (tokens_in * _env_float("GEMINI_STYLE_PRICE_IN", 0.75)
            + tokens_out * _env_float("GEMINI_STYLE_PRICE_OUT", 3.75)) / 1e6


def initialize():
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS style_watch_jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, video_id TEXT NOT NULL,
                start_s INTEGER NOT NULL, end_s INTEGER NOT NULL, prompt_version INTEGER NOT NULL,
                model TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0, cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                cached INTEGER NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_style_watch_window ON style_watch_jobs"
                         "(video_id,start_s,end_s,prompt_version,model,status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_style_watch_user ON style_watch_jobs(user_id,created_at)")
        _ready.add(key)


def _daily_limit(user: dict) -> int | None:
    if is_admin(user):
        return None
    return DAILY_WATCHES.get(plans.tier_of(user.get("plan")), 0)


def _spent_today(conn) -> float:
    row = conn.execute(db._q("SELECT COALESCE(SUM(cost_usd),0) AS s FROM style_watch_jobs WHERE created_at>?"),
                       (time.time() - 86400,)).fetchone()
    return float(row["s"] or 0)


def _row(job_id: str) -> dict | None:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM style_watch_jobs WHERE id=?"), (job_id,)).fetchone()
    return dict(row) if row else None


def public(row: dict) -> dict:
    out = {"job": row["id"], "status": row["status"], "video_id": row["video_id"],
           "start": row["start_s"], "end": row["end_s"], "model": row["model"], "cached": bool(row["cached"])}
    if row["status"] == "done":
        out["log"] = json.loads(row["result_json"] or "{}")
    if row["status"] == "failed":
        out["error"] = row["error"] or "Gemini could not watch this part of the video."
    return out


def _parse(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        value = json.loads(text[a:b + 1]) if 0 <= a < b else None
    if not isinstance(value, dict):
        raise ValueError("Gemini's answer was not a JSON object")
    return value


def watch_once(video_id: str, start: int, end: int, *, post=None) -> tuple[dict, int, int]:
    """One Gemini watch of a YouTube window: (log, input tokens, output tokens)."""
    post = post or requests.post
    body = {"contents": [{"parts": [
                {"fileData": {"fileUri": f"https://www.youtube.com/watch?v={video_id}", "mimeType": "video/*"},
                 "videoMetadata": {"startOffset": f"{start}s", "endOffset": f"{end}s", "fps": 2}},
                {"text": prompt(start, end)}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "mediaResolution": "MEDIA_RESOLUTION_LOW",
                                 "thinkingConfig": {"thinkingLevel": "low"}}}
    res = post(f"{GEMINI_BASE}/models/{model()}:generateContent", headers={"x-goog-api-key": _key()},
               json=body, timeout=(15, 600))
    if res.status_code != 200:
        detail = ""
        try:
            detail = str((res.json().get("error") or {}).get("message") or "")[:200]
        except ValueError:
            pass
        raise RuntimeError(f"Gemini HTTP {res.status_code}{': ' + detail if detail else ''}")
    data = res.json()
    usage = data.get("usageMetadata") or {}
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    tokens_in = int(usage.get("promptTokenCount") or 0)
    tokens_out = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    return _parse(text), tokens_in, tokens_out


def _run(job_id: str):
    row = _row(job_id)
    if not row or row["status"] != "running":
        return
    try:
        log, tin, tout = watch_once(row["video_id"], row["start_s"], row["end_s"])
        values = ("done", json.dumps(log), "", tin, tout, cost(tin, tout))
    except Exception as exc:                                    # noqa: BLE001 - recorded on the job
        print(f"[style-watch] {row['video_id']} {row['start_s']}-{row['end_s']}: {exc}", flush=True)
        values = ("failed", "", str(exc)[:300], 0, 0, 0.0)
    with db._conn() as conn:
        conn.execute(db._q("""UPDATE style_watch_jobs SET status=?, result_json=?, error=?, tokens_in=?,
            tokens_out=?, cost_usd=?, updated_at=? WHERE id=?"""), (*values, time.time(), job_id))


def start(user: dict, video_id: str, start_s: float, end_s: float, *, submit=None) -> dict:
    """Return a finished cached log, the account's running job for this window, or start a new watch."""
    initialize()
    if not _VIDEO.fullmatch(video_id or ""):
        raise HTTPException(400, "Choose a YouTube video to watch.")
    a, b = int(max(0, start_s)), int(end_s)
    if not 10 <= b - a <= MAX_WINDOW_S:
        raise HTTPException(400, f"Watch between 10 and {MAX_WINDOW_S} seconds at a time.")
    if not _key():
        raise HTTPException(503, "Style watching isn't available right now. The study continues with its own measurements.")
    now, name = time.time(), model()
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("SELECT pg_advisory_xact_lock(726346120)")
        same = conn.execute(db._q("""SELECT * FROM style_watch_jobs WHERE video_id=? AND start_s=? AND end_s=?
            AND prompt_version=? AND model=? AND (status='done' OR (status='running' AND updated_at>?))
            ORDER BY status='done' DESC, created_at DESC LIMIT 1"""),
            (video_id, a, b, PROMPT_VERSION, name, now - STALE_S)).fetchone()
        if same and same["status"] == "done":
            job = "sw_" + secrets.token_hex(10)
            conn.execute(db._q("""INSERT INTO style_watch_jobs (id,user_id,video_id,start_s,end_s,prompt_version,model,
                status,result_json,cached,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'done',?,1,?,?)"""),
                (job, user["id"], video_id, a, b, PROMPT_VERSION, name, same["result_json"], now, now))
            return public({**dict(same), "id": job, "cached": 1})
        if same:                                                # someone is watching this window right now
            return public(dict(same))
        limit = _daily_limit(user)
        if limit is not None:
            used = conn.execute(db._q("""SELECT COUNT(*) AS n FROM style_watch_jobs WHERE user_id=? AND cached=0
                AND created_at>?"""), (user["id"], now - 86400)).fetchone()["n"]
            if used >= limit:
                # 402, not 429: Studio retries a 429 within seconds, and this limit lasts until tomorrow
                raise HTTPException(402, "You've used today's style watching. The study continues with its own "
                                         "measurements, or try again tomorrow.")
        if _spent_today(conn) >= _env_float("STYLE_WATCH_DAILY_USD", 15.0):
            raise HTTPException(503, "Style watching is busy today. The study continues with its own measurements.")
        job = "sw_" + secrets.token_hex(10)
        conn.execute(db._q("""INSERT INTO style_watch_jobs (id,user_id,video_id,start_s,end_s,prompt_version,model,
            status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'running',?,?)"""),
            (job, user["id"], video_id, a, b, PROMPT_VERSION, name, now, now))
    (submit or _pool.submit)(_run, job)
    return public(_row(job))


def get(user: dict, job_id: str) -> dict:
    initialize()
    row = _row(job_id) if isinstance(job_id, str) and job_id.startswith("sw_") else None
    # Any Studio account may poll a job ID it was given: a window another account started first is shared, and a
    # log describes a public video's style only (public() never returns who started it).
    if not row:
        raise HTTPException(404, "That style watch wasn't found.")
    if row["status"] == "running" and time.time() - row["updated_at"] > STALE_S:
        with db._conn() as conn:
            conn.execute(db._q("UPDATE style_watch_jobs SET status='failed', error=?, updated_at=? WHERE id=? AND status='running'"),
                         ("The watch stopped before it finished. Start it again.", time.time(), job_id))
        row = _row(job_id)
    return public(row)


class WatchRequest(BaseModel):
    video_id: str
    start: float
    end: float


def make_router(access) -> APIRouter:
    """`access` is the Studio dependency (a Studio plan, by session or Studio key)."""
    router = APIRouter(prefix="/api/studio/style", tags=["ChannelRecipe Studio"])

    @router.post("/watch")
    def post_watch(req: WatchRequest, user=Depends(access)):
        return start(user, req.video_id, req.start, req.end)

    @router.get("/watch/{job_id}")
    def get_watch(job_id: str, user=Depends(access)):
        return get(user, job_id)

    return router
