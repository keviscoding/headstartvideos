"""How far each customer has got with Studio: downloaded it, connected it, last seen, first video.

The Studio page uses these to show a new customer their next setup step and a returning one their home (open
Studio, what's new, recent projects) instead of the install guide every time. The same numbers measure activation
(how many reach a first finished video, and how fast) and can drive helpful setup emails later.

One row per (user, milestone) with the first and latest time and a count. Writes are cheap upserts; "studio_seen"
is recorded at most once per ten minutes per customer, however often their Studio calls in.
"""
from __future__ import annotations

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


def of(user_id: int) -> dict:
    """{"downloaded_at", "connected_at", "seen_at", "projects", "finished", "first_finished_at"} (times or None)."""
    out = {"downloaded_at": None, "connected_at": None, "seen_at": None,
           "projects": 0, "finished": 0, "first_finished_at": None}
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
        with db._conn() as conn:
            p = conn.execute(db._q("""SELECT COUNT(*) AS total,
                SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS finished,
                MIN(CASE WHEN status='done' THEN updated_at END) AS first_finished
                FROM frontier_projects WHERE user_id=?"""), (int(user_id),)).fetchone()
        out["projects"] = int(p["total"] or 0)
        out["finished"] = int(p["finished"] or 0)
        out["first_finished_at"] = p["first_finished"]
    except Exception:                                               # noqa: BLE001 - no Studio projects yet
        pass
    return out
