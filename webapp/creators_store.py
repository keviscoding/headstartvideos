"""Hosted creator edits: the `creator_jobs` table, shared by the web server (webapp/creators_jobs.py creates rows and
reads them) and the worker machine (webapp/creators_worker.py writes its stages, result and cost into its own row).

No web framework here, so the worker image needs only the database driver. One row per edit; the web server never
does the work, it only reads what the worker wrote.
"""
from __future__ import annotations

import json
import secrets
import threading
import time

from webapp.creators_spec import NOTE_STAGES, STAGES
from webapp import database as db

TERMINAL = ("done", "failed", "cancelled")
_ready = set()
_ready_lock = threading.Lock()


def initialize():
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        real = "DOUBLE PRECISION" if db.IS_PG else "REAL"
        with db._conn() as conn:
            db.schema_lock(conn, "creator_jobs")              # two containers boot together: one at a time
            conn.execute(f"""CREATE TABLE IF NOT EXISTS creator_jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, look TEXT NOT NULL, status TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT '', stages TEXT NOT NULL DEFAULT '[]', progress {real} NOT NULL DEFAULT 0,
                take_key TEXT NOT NULL, take_name TEXT NOT NULL DEFAULT '', broll_keys TEXT NOT NULL DEFAULT '[]',
                output_key TEXT NOT NULL DEFAULT '', poster_key TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '{{}}', cost TEXT NOT NULL DEFAULT '{{}}', error TEXT NOT NULL DEFAULT '',
                free INTEGER NOT NULL DEFAULT 0, notify INTEGER NOT NULL DEFAULT 1, emailed INTEGER NOT NULL DEFAULT 0,
                machine_id TEXT NOT NULL DEFAULT '', created_at {real} NOT NULL, started_at {real},
                heartbeat_at {real}, ended_at {real}, kind TEXT NOT NULL DEFAULT 'edit',
                parent TEXT NOT NULL DEFAULT '', root TEXT NOT NULL DEFAULT '', refs TEXT NOT NULL DEFAULT '[]')""")
            # a review round (kind "notes") is a child of the edit it revises; root is the first edit of the chain;
            # refs: the Reel links whose style the edit learns first (creators.looks.clone)
            have = {r["name"] for r in conn.execute("PRAGMA table_info(creator_jobs)").fetchall()} if not db.IS_PG \
                else {r["column_name"] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='creator_jobs'").fetchall()}
            for col in ("kind", "parent", "root", "refs"):
                if col not in have:
                    default = {"kind": "'edit'", "refs": "'[]'"}.get(col, "''")
                    conn.execute(f"ALTER TABLE creator_jobs ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
            # the creators home (projects): a title the creator gave the first edit's row, and a soft delete
            for col, kind in (("title", "TEXT NOT NULL DEFAULT ''"), ("deleted_at", real)):
                if col not in have:
                    conn.execute(f"ALTER TABLE creator_jobs ADD COLUMN {col} {kind}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_creator_jobs_user ON creator_jobs(user_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_creator_jobs_status ON creator_jobs(status)")
            # phone verification for free edits: only a hash of the number is kept, so one number = one account
            conn.execute(f"""CREATE TABLE IF NOT EXISTS creator_phones (
                user_id INTEGER PRIMARY KEY, phone_hash TEXT NOT NULL UNIQUE, verified_at {real} NOT NULL)""")
        _ready.add(key)


def blank_stages(kind: str = "edit") -> list[dict]:
    return [{"key": k, "label": label, "state": "todo", "detail": ""}
            for k, label in (NOTE_STAGES if kind == "notes" else STAGES)]


# How much of the edit each stage is, for the progress bar (measured on the first hosted edits).
WEIGHTS = {"words": 0.14, "deadair": 0.04, "music": 0.16, "broll": 0.06, "titles": 0.45, "watched": 0.15,
           "transcribing": 0.1, "planning": 0.2, "rendering": 0.6, "checking": 0.1}


def progress_of(stages: list[dict]) -> float:
    p = 0.0
    for s in stages:
        w = WEIGHTS.get(s["key"], 0.0)
        p += w if s["state"] == "done" else w * 0.35 if s["state"] == "active" else 0.0
    return round(min(0.99, p), 3)


def create(user_id: int, look: str, take_key: str, take_name: str, broll_keys: list[str], *, free: bool,
           notify: bool, kind: str = "edit", parent: str = "", root: str = "", refs: list[str] | None = None) -> dict:
    initialize()
    job_id = "ce_" + secrets.token_hex(8)
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO creator_jobs (id, user_id, look, status, stages, take_key, take_name,
            broll_keys, free, notify, created_at, kind, parent, root, refs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""),
                     (job_id, int(user_id), look, "queued", json.dumps(blank_stages(kind)), take_key, take_name[:200],
                      json.dumps(broll_keys), 1 if free else 0, 1 if notify else 0, time.time(), kind, parent,
                      root or job_id, json.dumps(refs or [])))
    return get(job_id)


def rounds(root: str) -> int:
    """Review rounds started on one edit (failed ones don't count)."""
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("""SELECT COUNT(*) AS n FROM creator_jobs WHERE root=? AND kind='notes'
            AND status NOT IN ('failed','cancelled')"""), (root,)).fetchone()
    return int(row["n"] if row else 0)


def _parsed(row) -> dict:
    j = dict(row)
    for k, empty in (("stages", "[]"), ("broll_keys", "[]"), ("refs", "[]"), ("result", "{}"), ("cost", "{}")):
        try:
            j[k] = json.loads(j.get(k) or empty)
        except ValueError:
            j[k] = json.loads(empty)
    return j


def _select(where: str, params: tuple, tail: str = "") -> list[dict]:
    initialize()
    with db._conn() as conn:
        rows = conn.execute(db._q(f"SELECT * FROM creator_jobs WHERE {where} {tail}"), params).fetchall()
    return [_parsed(r) for r in rows]


def get(job_id: str, user_id: int | None = None) -> dict | None:
    rows = _select("id=?", (job_id,))
    if not rows or (user_id is not None and int(rows[0]["user_id"]) != int(user_id)):
        return None
    return rows[0]


def update(job_id: str, **fields) -> None:
    for k in ("stages", "broll_keys", "refs", "result", "cost"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    sets = ",".join(f"{k}=?" for k in fields)
    with db._conn() as conn:
        conn.execute(db._q(f"UPDATE creator_jobs SET {sets} WHERE id=?"), (*fields.values(), job_id))


def claim(job_id: str, machine_id: str = "") -> dict | None:
    """The worker takes its job once: queued/starting → running. None when it's gone, ended or already taken."""
    initialize()
    now = time.time()
    with db._conn() as conn:
        cur = conn.execute(db._q("""UPDATE creator_jobs SET status='running', started_at=?, heartbeat_at=?,
            machine_id=CASE WHEN ?<>'' THEN ? ELSE machine_id END WHERE id=? AND status IN ('queued','starting')"""),
                           (now, now, machine_id, machine_id, job_id))
        taken = cur.rowcount
    return get(job_id) if taken else None


def set_stage(job_id: str, key: str, state: str, detail: str = "") -> dict:
    """One stage moves (todo → active → done); the row's stage, progress and heartbeat follow."""
    j = get(job_id)
    stages = j["stages"] or blank_stages(j.get("kind") or "edit")
    for s in stages:
        if s["key"] == key:
            s["state"] = state
            if detail:
                s["detail"] = detail[:200]
    active = [s["key"] for s in stages if s["state"] == "active"]
    update(job_id, stages=stages, stage=active[-1] if active else key, progress=progress_of(stages),
           heartbeat_at=time.time())
    return stages


def heartbeat(job_id: str) -> None:
    update(job_id, heartbeat_at=time.time())


def finish(job_id: str, status: str, *, error: str = "", **fields) -> None:
    update(job_id, status=status, ended_at=time.time(), error=error[:500],
           **({"progress": 1.0} if status == "done" else {}), **fields)


def list_for(user_id: int, limit: int = 20) -> list[dict]:
    return _select("user_id=?", (int(user_id), int(limit)), "ORDER BY created_at DESC LIMIT ?")


def project_rows(user_id: int, limit: int = 2000) -> list[dict]:
    """Every job of one creator that isn't deleted, newest first: the home groups them into projects by `root`."""
    return _select("user_id=? AND deleted_at IS NULL", (int(user_id), int(limit)), "ORDER BY created_at DESC LIMIT ?")


def project_chain(root: str, user_id: int) -> list[dict]:
    """One project's jobs (the first edit and its notes rounds), oldest first; [] when it isn't theirs or is deleted."""
    return _select("user_id=? AND (id=? OR root=?) AND deleted_at IS NULL", (int(user_id), root, root),
                   "ORDER BY created_at")


def set_title(root: str, user_id: int, title: str) -> bool:
    initialize()
    with db._conn() as conn:
        cur = conn.execute(db._q("UPDATE creator_jobs SET title=? WHERE id=? AND user_id=? AND deleted_at IS NULL"),
                           (title, root, int(user_id)))
        return (cur.rowcount or 0) > 0


def soft_delete(root: str, user_id: int) -> int:
    """Hide a project from the home: every row of the chain gets deleted_at. Its files stay (a Trash comes later)."""
    initialize()
    with db._conn() as conn:
        cur = conn.execute(db._q("UPDATE creator_jobs SET deleted_at=? WHERE user_id=? AND (id=? OR root=?) "
                                 "AND deleted_at IS NULL"), (time.time(), int(user_id), root, root))
        return int(cur.rowcount or 0)


def free_used(user_id: int) -> int:
    """Free edits that count: every free job except the ones that failed or were cancelled (those are given back)."""
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("""SELECT COUNT(*) AS n FROM creator_jobs WHERE user_id=? AND free=1
            AND kind='edit' AND status NOT IN ('failed','cancelled')"""), (int(user_id),)).fetchone()
    return int(row["n"] if row else 0)


def running_for(user_id: int) -> list[dict]:
    return _select("user_id=? AND status IN ('queued','starting','running')", (int(user_id),))


def running_count() -> int:
    """Edits with a machine across everyone (each holds a machine and, briefly, database connections). Jobs waiting
    in line (queued, no machine yet) don't count."""
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT COUNT(*) AS n FROM creator_jobs WHERE status IN ('starting','running') "
                                 "OR (status='queued' AND machine_id<>'')")).fetchone()
    return int(row["n"] if row else 0)


def waiting(limit: int = 50) -> list[dict]:
    """Jobs in line for a machine, oldest first."""
    return _select("status='queued' AND machine_id=''", (int(limit),), "ORDER BY created_at LIMIT ?")


def ahead_of(j: dict) -> int:
    """How many jobs are in line before this one."""
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT COUNT(*) AS n FROM creator_jobs WHERE status='queued' AND machine_id='' "
                                 "AND created_at<?"), (j["created_at"],)).fetchone()
    return int(row["n"] if row else 0)


def take_slot(job_id: str) -> bool:
    """Claim a waiting job for one dispatcher (two web containers run one each): it gets a machine only once."""
    initialize()
    with db._conn() as conn:
        cur = conn.execute(db._q("UPDATE creator_jobs SET machine_id='pending', heartbeat_at=? WHERE id=? "
                                 "AND status='queued' AND machine_id=''"), (time.time(), job_id))
        return (cur.rowcount or 0) > 0


def stale(seconds: float) -> list[dict]:
    """Jobs whose worker went quiet (no heartbeat for `seconds`), or that never started."""
    return _select("status IN ('queued','starting','running') AND COALESCE(heartbeat_at, created_at) < ?",
                   (time.time() - seconds,))


def phone_verified(user_id: int) -> bool:
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT verified_at FROM creator_phones WHERE user_id=?"), (int(user_id),)).fetchone()
    return bool(row)
