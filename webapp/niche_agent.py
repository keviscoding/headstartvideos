"""The niche agent's two read-only endpoints for Studio (the agent itself runs in the creator's Claude Code).

- GET /api/studio/niche-agent/teaching: the owner's rules and examples (niche_agent/teaching/ in this repository), so
  new teaching reaches every creator with a deploy, without a Studio release. Studio keeps the copy it shipped with.
- GET /api/studio/niche-agent/breakouts: young channels with strong recent views from niche_channels, matched by words
  in their names and video titles. The table's source_keyword is a discovery probe word ("hadn't", "somehow"), not a
  topic, so it is never used as the niche. The rows are seeds: Studio's agent checks them live on YouTube.

Both need a Studio plan (web session or the Studio's account key). Nothing here writes, spends or calls a model.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from webapp import database as db

TEACHING = Path(__file__).resolve().parents[1] / "niche_agent" / "teaching"
SORTS = {"recent_avg": "recent_avg_views DESC", "ratio": "view_to_sub_ratio DESC, recent_avg_views DESC",
         "newest": "first_seen_at DESC", "views_per_sub": "view_to_sub_ratio DESC"}
MAX_WORDS = 6
# a soft per-instance guard (two web instances: each keeps its own count); an agent run makes 2-5 of these calls
RATE_WINDOW, RATE_MAX = 600, 40
_rate: dict = defaultdict(list)
_rate_lock = threading.Lock()


def teaching() -> dict:
    rules = (TEACHING / "rules.md").read_text(encoding="utf-8")
    examples = []
    for line in (TEACHING / "examples.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            examples.append(row)
    version = hashlib.sha256((rules + json.dumps(examples, sort_keys=True)).encode()).hexdigest()[:12]
    updated = max((str(e.get("added") or "") for e in examples), default="")
    return {"rules": rules, "examples": examples, "version": version, "updated": updated}


def _rate_ok(user_id) -> bool:
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate[user_id] if now - t < RATE_WINDOW]
        if len(hits) >= RATE_MAX:
            _rate[user_id] = hits
            return False
        _rate[user_id] = hits + [now]
        return True


def _videos(raw, limit=4) -> list:
    try:
        rows = json.loads(raw or "[]")
    except ValueError:
        return []
    out = []
    for v in rows if isinstance(rows, list) else []:
        if isinstance(v, dict):
            out.append({k: v.get(k) for k in ("title", "url", "view_count", "duration_sec", "published_at")})
    return out[:limit]


def breakouts(q: str = "", *, max_age_days: int = 90, max_subs: int = 50_000, min_recent_avg: int = 10_000,
              min_ratio: float = 0, sort: str = "recent_avg", limit: int = 25, now: float | None = None) -> dict:
    """Channels from niche_channels: young at their last scoring (days_since_start + the days since then), small,
    with strong recent averages, whose names or video titles contain every word of q."""
    now = time.time() if now is None else now
    words = [w for w in str(q or "").lower().split() if len(w) >= 2][:MAX_WORDS]
    clauses = ["active = 1", "days_since_start IS NOT NULL",
               "days_since_start + (? - last_scored_at) / 86400.0 <= ?",
               "COALESCE(subscriber_count, 0) <= ?", "COALESCE(recent_avg_views, 0) >= ?"]
    params: list = [now, float(max_age_days), int(max_subs), float(min_recent_avg)]
    if min_ratio:
        clauses.append("COALESCE(view_to_sub_ratio, 0) >= ?")
        params.append(float(min_ratio))
    text = ("LOWER(COALESCE(channel_name, '') || ' ' || COALESCE(recent_videos_json, '') || ' ' || "
            "COALESCE(popular_videos_json, ''))")
    for word in words:
        clauses.append(f"{text} LIKE ?")
        params.append("%" + word.replace("%", "").replace("_", "") + "%")
    order = SORTS.get(sort) or SORTS["recent_avg"]
    params.append(max(1, min(int(limit or 25), 50)))
    sql = ("SELECT channel_id, channel_name, channel_url, subscriber_count, video_count, days_since_start, "
           "recent_avg_views, avg_views_per_video, view_to_sub_ratio, uploads_per_month, outlier_score, "
           "videos_last_14d, recent_videos_json, popular_videos_json, last_scored_at, first_seen_at "
           f"FROM niche_channels WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ?")
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute(db._q(sql), tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
    out = []
    for r in rows:
        scored = float(r.get("last_scored_at") or now)
        age_then = r.get("days_since_start")
        out.append({
            "channel": r.get("channel_name"), "channel_id": r.get("channel_id"), "channel_url": r.get("channel_url"),
            "subscribers": int(r.get("subscriber_count") or 0), "videos": int(r.get("video_count") or 0),
            "age_days_now": round(float(age_then) + (now - scored) / 86400) if age_then is not None else None,
            "recent_avg_views": int(r.get("recent_avg_views") or 0),
            "avg_views": int(r.get("avg_views_per_video") or 0),
            "views_per_sub": round(float(r.get("view_to_sub_ratio") or 0), 1),
            "uploads_per_month": round(float(r.get("uploads_per_month") or 0), 1),
            "top_video_vs_average": round(float(r.get("outlier_score") or 0), 1),
            "videos_last_14d": int(r.get("videos_last_14d") or 0),
            "recent_videos": _videos(r.get("recent_videos_json")),
            "popular_videos": _videos(r.get("popular_videos_json")),
            "numbers_from": time.strftime("%Y-%m-%d", time.gmtime(scored)),
            "numbers_days_old": round((now - scored) / 86400),
        })
    return {"channels": out, "count": len(out), "q": " ".join(words),
            "filters": {"max_age_days": max_age_days, "max_subs": max_subs, "min_recent_avg": min_recent_avg,
                        "min_ratio": min_ratio, "sort": sort if sort in SORTS else "recent_avg"},
            "note": "Seeds from ChannelRecipe's niche database (long-form). Numbers are from each channel's last "
                    "scoring (numbers_from) and keep 4 recent videos: check them live before using them."}


def make_router(access) -> APIRouter:
    """`access` is the Studio dependency (a Studio plan, by session or Studio key)."""
    router = APIRouter(prefix="/api/studio/niche-agent", tags=["ChannelRecipe Studio"])
    private = {"Cache-Control": "private, no-store"}

    @router.get("/teaching")
    def get_teaching(user=Depends(access)):
        try:
            return JSONResponse(teaching(), headers=private)
        except OSError:
            raise HTTPException(503, "The niche agent's teaching isn't available right now.") from None

    @router.get("/breakouts")
    def get_breakouts(q: str = "", max_age_days: int = 90, max_subs: int = 50_000, min_recent_avg: int = 10_000,
                      min_ratio: float = 0, sort: str = "recent_avg", limit: int = 25, user=Depends(access)):
        if not _rate_ok(user.get("id")):
            raise HTTPException(429, "Too many niche database reads in a few minutes. Try again shortly.")
        data = breakouts(q[:200], max_age_days=max(1, min(int(max_age_days), 3650)),
                         max_subs=max(0, min(int(max_subs), 100_000_000)),
                         min_recent_avg=max(0, int(min_recent_avg)), min_ratio=max(0.0, float(min_ratio)),
                         sort=sort, limit=limit)
        return JSONResponse(data, headers=private)

    return router
