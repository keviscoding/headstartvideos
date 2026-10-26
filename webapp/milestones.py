"""How far each customer has got with Studio: downloaded it, connected it, last seen, first video.

The Studio page uses these to show a new customer their next setup step and a returning one their home (open
Studio, what's new, recent projects) instead of the install guide every time. The same numbers measure activation
(how many reach a first finished video, and how fast) and can drive helpful setup emails later.

One row per (user, milestone) with the first and latest time and a count. Writes are cheap upserts; "studio_seen"
is recorded at most once per ten minutes per customer, however often their Studio calls in.
"""
from __future__ import annotations

import json
import statistics
import threading
import time

from webapp import database as db

DOWNLOADED, CONNECTED, SEEN = "studio_downloaded", "studio_connected", "studio_seen"
SEEN_EVERY_S = 600

_ready: set = set()
_lock = threading.Lock()
_seen: dict[int, float] = {}


def initialize() -> None:
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with db._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS user_milestones (
                user_id INTEGER NOT NULL, name TEXT NOT NULL,
                first_at DOUBLE PRECISION NOT NULL, last_at DOUBLE PRECISION NOT NULL,
                count INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (user_id, name))""")
        _ready.add(key)


def record(user_id: int, name: str, now: float | None = None) -> None:
    """Note that a customer reached a milestone (again). Never raises: a milestone is never worth a failed request."""
    try:
        initialize()
        t = float(now if now is not None else time.time())
        with db._conn() as conn:
            conn.execute(db._q("""INSERT INTO user_milestones (user_id, name, first_at, last_at, count)
                VALUES (?,?,?,?,1) ON CONFLICT(user_id, name) DO UPDATE SET
                last_at = excluded.last_at, count = user_milestones.count + 1"""), (int(user_id), name, t, t))
    except Exception as e:                                          # noqa: BLE001
        print(f"[milestones] {name} for user {user_id} not recorded: {e}")


def seen(user_id: int) -> None:
    """The customer's Studio called in; recorded at most once per SEEN_EVERY_S."""
    now = time.time()
    if now - _seen.get(int(user_id), 0) < SEEN_EVERY_S:
        return
    _seen[int(user_id)] = now
    record(user_id, SEEN, now)


def _purpose(setup_json) -> str:
    """A Studio project's purpose from its native session: "video", "setup" (Studio's own model check) or "creative"
    (style studies, products). A project with no session is a video, as before sessions existed."""
    try:
        return str((json.loads(setup_json) if setup_json else {}).get("purpose") or "video")
    except (TypeError, ValueError, AttributeError):
        return "video"


def _projects(user_id=None, since=None) -> list:
    """[{"user_id", "status", "created_at", "updated_at", "purpose"}] of Studio projects. Raises when there is no
    projects table yet."""
    where, args = [], []
    if user_id is not None:
        where.append("p.user_id=?"); args.append(int(user_id))
    if since is not None:
        where.append("p.created_at>=?"); args.append(float(since))
    filters = " WHERE " + " AND ".join(where) if where else ""
    try:
        with db._conn() as conn:
            rows = conn.execute(db._q("SELECT p.user_id, p.status, p.created_at, p.updated_at, s.setup_json "
                                      "FROM frontier_projects p LEFT JOIN frontier_native_sessions s "
                                      "ON s.project_id=p.id" + filters), tuple(args)).fetchall()
    except Exception:                                               # noqa: BLE001 - no sessions table: all videos
        with db._conn() as conn:                                    # (a fresh transaction: Postgres spoils the old one)
            rows = [{**dict(r), "setup_json": None} for r in conn.execute(db._q(
                "SELECT p.user_id, p.status, p.created_at, p.updated_at FROM frontier_projects p" + filters),
                tuple(args)).fetchall()]
    return [{"user_id": r["user_id"], "status": r["status"], "created_at": r["created_at"],
             "updated_at": r["updated_at"], "purpose": _purpose(r["setup_json"])} for r in rows]


def of(user_id: int) -> dict:
    """{"downloaded_at", "connected_at", "seen_at", "projects", "videos", "finished", "first_finished_at"} (times or
    None). projects counts everything Studio ran; videos, finished and first_finished_at count videos only: Studio's
    setup model check is a finished project too, and once counted it ticked "first video" before any video existed
    and kept the first-video email from ever being sent."""
    out = {"downloaded_at": None, "connected_at": None, "seen_at": None,
           "projects": 0, "videos": 0, "finished": 0, "first_finished_at": None}
    try:
        initialize()
        with db._conn() as conn:
            for row in conn.execute(db._q("SELECT name, first_at, last_at FROM user_milestones WHERE user_id=?"),
                                    (int(user_id),)).fetchall():
                if row["name"] == DOWNLOADED:
                    out["downloaded_at"] = row["first_at"]
                elif row["name"] == CONNECTED:
                    out["connected_at"] = row["first_at"]
                elif row["name"] == SEEN:
                    out["seen_at"] = row["last_at"]
    except Exception as e:                                          # noqa: BLE001
        print(f"[milestones] read for user {user_id} failed: {e}")
    try:                                                            # its own transaction: the table may not exist yet
        rows = _projects(user_id)
    except Exception:                                               # noqa: BLE001 - no Studio projects yet
        return out
    videos = [r for r in rows if r["purpose"] == "video"]
    done = [r["updated_at"] for r in videos if r["status"] == "done"]
    out.update(projects=len(rows), videos=len(videos), finished=len(done), first_finished_at=min(done) if done else None)
    return out


def activation(days: int = 30, now: float | None = None) -> dict:
    """How far the customers who subscribed in the last `days` days got: downloaded Studio, connected it, passed
    setup (Studio's model check finished), started a first video, finished one, and how long that took. Counts and
    hours only, never who. Subscriptions are recorded since the lifecycle emails shipped."""
    from webapp import lifecycle_email
    initialize()
    days = max(1, min(365, int(days or 30)))
    now = time.time() if now is None else now
    since = now - days * 86400
    with db._conn() as conn:
        subscribed = {int(r["user_id"]): float(r["first_at"]) for r in conn.execute(db._q(
            "SELECT user_id, first_at FROM user_milestones WHERE name=? AND first_at>=?"),
            (lifecycle_email.SUBSCRIBED, since)).fetchall()}
        marks: dict = {}
        if subscribed:
            for r in conn.execute(db._q("SELECT user_id, name FROM user_milestones WHERE name IN (?,?,?)"),
                                  (DOWNLOADED, CONNECTED, SEEN)).fetchall():
                marks.setdefault(int(r["user_id"]), set()).add(r["name"])
    try:
        rows = [r for r in _projects(since=since - 86400) if int(r["user_id"]) in subscribed]
    except Exception:                                               # noqa: BLE001 - no Studio projects yet
        rows = []
    by: dict = {}
    for r in rows:
        by.setdefault(int(r["user_id"]), []).append(r)
    counts = {"subscribed": len(subscribed), "downloaded": 0, "connected": 0, "setup_ready": 0,
              "first_video_started": 0, "first_video_finished": 0, "only_failed_videos": 0}
    hours = []
    for uid, at in subscribed.items():
        mine, got = by.get(uid, []), marks.get(uid, set())
        videos = [r for r in mine if r["purpose"] == "video"]
        done = sorted(r["updated_at"] for r in videos if r["status"] == "done")
        counts["downloaded"] += bool(got & {DOWNLOADED, CONNECTED, SEEN} or mine)
        counts["connected"] += bool(got & {CONNECTED, SEEN} or mine)
        counts["setup_ready"] += any(r["purpose"] == "setup" and r["status"] == "done" for r in mine) or bool(done)
        counts["first_video_started"] += bool(videos)
        counts["first_video_finished"] += bool(done)
        counts["only_failed_videos"] += bool(videos) and not done and all(r["status"] == "failed" for r in videos)
        if done:
            hours.append(max(0.0, (done[0] - at) / 3600))
    return {"days": days, "since": since, **counts,
            "hours_to_first_video": {"median": round(statistics.median(hours), 1) if hours else None,
                                     "p90": round(sorted(hours)[int(0.9 * (len(hours) - 1))], 1) if hours else None},
            "note": "Customers who subscribed in the window; counts only."}


def make_router(require_admin):
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse
    router = APIRouter(prefix="/api/admin/activation", tags=["admin"])

    @router.get("")
    def get_activation(days: int = 30, admin=Depends(require_admin)):
        return JSONResponse(activation(days), headers={"Cache-Control": "private, no-store"})

    return router
