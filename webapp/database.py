"""
Database layer for users, sessions, credits, and render telemetry.

Uses Postgres when DATABASE_URL is set (production, durable across redeploys
and multiple instances), and falls back to a local SQLite file otherwise
(handy for local development). Auto-creates tables on first import.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith("postgres")

if IS_PG:
    import psycopg
    from psycopg.rows import dict_row
else:
    DB_PATH = Path(__file__).resolve().parent.parent / "data" / "channelrecipe.db"
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _q(sql: str) -> str:
    """Translate '?' placeholders to Postgres '%s' when needed."""
    return sql.replace("?", "%s") if IS_PG else sql


# --- Schemas ---------------------------------------------------------------

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT UNIQUE NOT NULL,
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    plan        TEXT NOT NULL DEFAULT 'free',
    credits     INTEGER NOT NULL DEFAULT 0,
    stripe_customer_id TEXT DEFAULT '',
    stripe_sub_id      TEXT DEFAULT '',
    trial_used  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS verify_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    code        TEXT NOT NULL,
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    expires_at  REAL NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS render_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    job_id        TEXT,
    recipe        TEXT,
    status        TEXT,
    duration_sec  REAL DEFAULT 0,
    target_minutes REAL DEFAULT 0,
    cost_pence    REAL DEFAULT 0,
    error_class   TEXT DEFAULT '',
    created_at    REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    title         TEXT DEFAULT '',
    recipe        TEXT DEFAULT '',
    video_url     TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    description   TEXT DEFAULT '',
    tags          TEXT DEFAULT '',
    hashtags      TEXT DEFAULT '',
    created_at    REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS cook_jobs (
    job_id        TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    recipe        TEXT DEFAULT '',
    title         TEXT DEFAULT '',
    request_json  TEXT DEFAULT '',
    progress_json TEXT DEFAULT '[]',
    result_json   TEXT DEFAULT '',
    error         TEXT DEFAULT '',
    credit_deducted INTEGER NOT NULL DEFAULT 0,
    lite_mode     INTEGER NOT NULL DEFAULT 0,
    worker_id     TEXT DEFAULT '',
    heartbeat_at  REAL DEFAULT 0,
    created_at    REAL NOT NULL DEFAULT (strftime('%s','now')),
    started_at    REAL DEFAULT 0,
    finished_at   REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ops_credit_grants (
    grant_key   TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    applied_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);
"""

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    plan        TEXT NOT NULL DEFAULT 'free',
    credits     INTEGER NOT NULL DEFAULT 0,
    stripe_customer_id TEXT DEFAULT '',
    stripe_sub_id      TEXT DEFAULT '',
    trial_used  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    expires_at  DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS verify_codes (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    code        TEXT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    expires_at  DOUBLE PRECISION NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS render_events (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT,
    job_id        TEXT,
    recipe        TEXT,
    status        TEXT,
    duration_sec  DOUBLE PRECISION DEFAULT 0,
    target_minutes DOUBLE PRECISION DEFAULT 0,
    cost_pence    DOUBLE PRECISION DEFAULT 0,
    error_class   TEXT DEFAULT '',
    created_at    DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);
CREATE TABLE IF NOT EXISTS videos (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    title         TEXT DEFAULT '',
    recipe        TEXT DEFAULT '',
    video_url     TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    description   TEXT DEFAULT '',
    tags          TEXT DEFAULT '',
    hashtags      TEXT DEFAULT '',
    created_at    DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);
CREATE TABLE IF NOT EXISTS cook_jobs (
    job_id        TEXT PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    recipe        TEXT DEFAULT '',
    title         TEXT DEFAULT '',
    request_json  TEXT DEFAULT '',
    progress_json TEXT DEFAULT '[]',
    result_json   TEXT DEFAULT '',
    error         TEXT DEFAULT '',
    credit_deducted INTEGER NOT NULL DEFAULT 0,
    lite_mode     INTEGER NOT NULL DEFAULT 0,
    worker_id     TEXT DEFAULT '',
    heartbeat_at  DOUBLE PRECISION DEFAULT 0,
    created_at    DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    started_at    DOUBLE PRECISION DEFAULT 0,
    finished_at   DOUBLE PRECISION DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ops_credit_grants (
    grant_key   TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    applied_at  DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);
"""


@contextmanager
def _conn():
    if IS_PG:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    else:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_users_stripe_sub ON users (stripe_sub_id);
CREATE INDEX IF NOT EXISTS idx_videos_user ON videos (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_verify_codes_email ON verify_codes (email, used);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_render_events_created ON render_events (created_at);
CREATE INDEX IF NOT EXISTS idx_cook_jobs_user ON cook_jobs (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cook_jobs_status ON cook_jobs (status, created_at);
"""


_MIGRATIONS = """
UPDATE users SET credits = 0 WHERE plan = 'free' AND credits > 0;
UPDATE users SET trial_used = 1 WHERE plan IN ('starter_trial', 'daily_trial', 'starter', 'daily', 'pro') AND COALESCE(trial_used, 0) = 0;
UPDATE users SET trial_used = 1 WHERE plan = 'free' AND COALESCE(stripe_customer_id, '') != '' AND COALESCE(trial_used, 0) = 0;
"""


def _ensure_column(cur, table: str, column: str, col_def: str):
    """Add a column if missing (Postgres + SQLite)."""
    try:
        if IS_PG:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_def}")
        else:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
    except Exception:
        pass


def _init_db():
    schema = _SCHEMA_PG if IS_PG else _SCHEMA_SQLITE
    with _conn() as conn:
        if IS_PG:
            with conn.cursor() as cur:
                cur.execute(schema)
                cur.execute(_INDEXES)
                _ensure_column(cur, "users", "trial_used", "INTEGER NOT NULL DEFAULT 0")
                _ensure_column(cur, "users", "heygen_key_enc", "TEXT DEFAULT ''")
                _ensure_column(cur, "cook_jobs", "lite_mode", "INTEGER NOT NULL DEFAULT 0")
                _ensure_column(cur, "cook_jobs", "worker_id", "TEXT DEFAULT ''")
                _ensure_column(cur, "cook_jobs", "heartbeat_at", "DOUBLE PRECISION DEFAULT 0")
                cur.execute(_MIGRATIONS)
                try:
                    cur.execute("ALTER TABLE users ALTER COLUMN credits SET DEFAULT 0")
                except Exception:
                    pass
        else:
            conn.executescript(schema)
            conn.executescript(_INDEXES)
            cur = conn.cursor()
            _ensure_column(cur, "users", "trial_used", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(cur, "users", "heygen_key_enc", "TEXT DEFAULT ''")
            _ensure_column(cur, "cook_jobs", "lite_mode", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(cur, "cook_jobs", "worker_id", "TEXT DEFAULT ''")
            _ensure_column(cur, "cook_jobs", "heartbeat_at", "REAL DEFAULT 0")
            conn.executescript(_MIGRATIONS)
    _apply_pending_credit_grants()


# Idempotent support credit grants (applied once per grant_key on boot).
_PENDING_CREDIT_GRANTS = [
    # Spaces CDN env had a trailing newline → cook failed after credit deduct;
    # customer reported being charged twice for the failed build.
    ("2026-07-11-arman-newline-url", "armankaladiya02@gmail.com", 2),
]


def _apply_pending_credit_grants() -> None:
    for grant_key, email, amount in _PENDING_CREDIT_GRANTS:
        email = email.lower().strip()
        if amount <= 0:
            continue
        try:
            with _conn() as conn:
                cur = conn.cursor()
                cur.execute(_q("SELECT 1 FROM ops_credit_grants WHERE grant_key = ?"), (grant_key,))
                if cur.fetchone():
                    continue
                cur.execute(_q("SELECT id, credits FROM users WHERE email = ?"), (email,))
                row = cur.fetchone()
                if not row:
                    print(f"[ops] credit grant {grant_key}: user {email} not found yet — will retry next boot")
                    continue
                user = dict(row)
                cur.execute(
                    _q("UPDATE users SET credits = credits + ? WHERE id = ?"),
                    (amount, user["id"]),
                )
                cur.execute(
                    _q("INSERT INTO ops_credit_grants (grant_key, email, amount, applied_at) VALUES (?, ?, ?, ?)"),
                    (grant_key, email, amount, time.time()),
                )
                print(
                    f"[ops] Granted +{amount} credits to {email} "
                    f"(was {user['credits']}) via {grant_key}"
                )
        except Exception as e:
            print(f"[ops] credit grant {grant_key} failed: {e}")


# -- Users ------------------------------------------------------------------

def get_user_by_email(email: str) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("SELECT * FROM users WHERE email = ?"), (email.lower().strip(),))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("SELECT * FROM users WHERE id = ?"), (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_user(email: str) -> dict:
    email = email.lower().strip()
    with _conn() as conn:
        cur = conn.cursor()
        if IS_PG:
            cur.execute(_q("INSERT INTO users (email) VALUES (?) ON CONFLICT (email) DO NOTHING"), (email,))
        else:
            cur.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        cur.execute(_q("SELECT * FROM users WHERE email = ?"), (email,))
        return dict(cur.fetchone())


def get_user_by_sub_id(sub_id: str) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("SELECT * FROM users WHERE stripe_sub_id = ?"), (sub_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_user(user_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [user_id]
    with _conn() as conn:
        conn.cursor().execute(_q(f"UPDATE users SET {sets} WHERE id = ?"), vals)


def deduct_credit(user_id: int) -> bool:
    """Atomically deduct 1 credit. Returns False if insufficient."""
    with _conn() as conn:
        cur = conn.cursor()
        # Conditional update avoids a read/write race across concurrent requests.
        cur.execute(
            _q("UPDATE users SET credits = credits - 1 WHERE id = ? AND credits >= 1"),
            (user_id,),
        )
        return cur.rowcount > 0


def refund_credit(user_id: int) -> None:
    with _conn() as conn:
        conn.cursor().execute(_q("UPDATE users SET credits = credits + 1 WHERE id = ?"), (user_id,))


def add_credits(user_id: int, amount: int) -> None:
    """Atomically add N credits (for top-ups)."""
    with _conn() as conn:
        conn.cursor().execute(
            _q("UPDATE users SET credits = credits + ? WHERE id = ?"),
            (amount, user_id),
        )


def set_user_heygen_key(user_id: int, plaintext: str | None) -> None:
    """Store encrypted HeyGen API key (empty/None clears)."""
    from webapp.secrets import encrypt_secret

    enc = encrypt_secret((plaintext or "").strip()) if plaintext else ""
    update_user(user_id, heygen_key_enc=enc)


def get_user_heygen_key(user_id: int) -> str | None:
    """Decrypt and return the user's HeyGen API key, or None."""
    from webapp.secrets import decrypt_secret

    user = get_user_by_id(user_id)
    if not user:
        return None
    raw = decrypt_secret(user.get("heygen_key_enc") or "")
    return raw.strip() or None


def user_heygen_status(user_id: int) -> dict:
    """Public status for Settings UI — never returns the full key."""
    from webapp.secrets import decrypt_secret, secret_last4

    user = get_user_by_id(user_id)
    if not user:
        return {"configured": False, "last4": ""}
    plain = decrypt_secret(user.get("heygen_key_enc") or "")
    if not plain:
        return {"configured": False, "last4": ""}
    return {"configured": True, "last4": secret_last4(plain)}


# -- Verification codes -----------------------------------------------------

def create_verify_code(email: str) -> str:
    code = f"{secrets.randbelow(1000000):06d}"
    email = email.lower().strip()
    expires = time.time() + 600  # 10 minutes
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("DELETE FROM verify_codes WHERE email = ? AND used = 0"), (email,))
        cur.execute(
            _q("INSERT INTO verify_codes (email, code, expires_at) VALUES (?, ?, ?)"),
            (email, code, expires),
        )
    return code


def verify_code(email: str, code: str) -> bool:
    email = email.lower().strip()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT id FROM verify_codes WHERE email = ? AND code = ? AND used = 0 AND expires_at > ?"),
            (email, code, time.time()),
        )
        row = cur.fetchone()
        if not row:
            return False
        rid = row["id"]
        cur.execute(_q("UPDATE verify_codes SET used = 1 WHERE id = ?"), (rid,))
        return True


# -- Sessions ---------------------------------------------------------------

SESSION_DURATION = 30 * 24 * 3600  # 30 days


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.time() + SESSION_DURATION
    with _conn() as conn:
        conn.cursor().execute(
            _q("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)"),
            (token, user_id, expires),
        )
    return token


def get_session_user(token: str) -> dict | None:
    if not token:
        return None
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("""SELECT u.* FROM sessions s JOIN users u ON s.user_id = u.id
                  WHERE s.token = ? AND s.expires_at > ?"""),
            (token, time.time()),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_session(token: str) -> None:
    with _conn() as conn:
        conn.cursor().execute(_q("DELETE FROM sessions WHERE token = ?"), (token,))


# -- Render telemetry (COGS / unit economics) -------------------------------

def log_render_event(
    user_id: int | None,
    job_id: str,
    recipe: str,
    status: str,
    duration_sec: float = 0,
    target_minutes: float = 0,
    cost_pence: float = 0,
    error_class: str = "",
) -> None:
    with _conn() as conn:
        conn.cursor().execute(
            _q("""INSERT INTO render_events
                  (user_id, job_id, recipe, status, duration_sec, target_minutes, cost_pence, error_class)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""),
            (user_id, job_id, recipe, status, duration_sec, target_minutes, cost_pence, error_class),
        )


def render_stats(days: int = 30) -> dict:
    """Aggregate render telemetry for a simple admin overview."""
    since = time.time() - days * 86400
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT status, recipe, duration_sec, cost_pence FROM render_events WHERE created_at >= ?"),
            (since,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    total = len(rows)
    succeeded = sum(1 for r in rows if r["status"] == "succeeded")
    failed = total - succeeded
    total_cost = sum((r["cost_pence"] or 0) for r in rows)
    avg_dur = (sum((r["duration_sec"] or 0) for r in rows) / total) if total else 0
    by_recipe: dict[str, int] = {}
    for r in rows:
        key = r["recipe"] or "unknown"
        by_recipe[key] = by_recipe.get(key, 0) + 1
    p50_cook = median_cook_minutes(lookback=80)
    return {
        "days": days,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "success_rate": round(succeeded / total * 100, 1) if total else 0,
        "total_cost_pence": round(total_cost, 1),
        "avg_cost_pence": round(total_cost / total, 2) if total else 0,
        "avg_duration_sec": round(avg_dur, 1),
        "p50_cook_minutes": p50_cook,
        "by_recipe": by_recipe,
        "queue": cook_queue_stats(),
    }


def median_cook_minutes(lookback: int = 40, recipe: str | None = None) -> float:
    """
    Live p50 cook duration (minutes) from recent successes.
    Falls back to EST_MINUTES_PER_COOK when we lack data.
    """
    try:
        from config import EST_MINUTES_PER_COOK as _fallback
        fallback = float(_fallback)
    except Exception:
        fallback = float(os.getenv("EST_MINUTES_PER_COOK", "7"))
    lookback = max(5, min(int(lookback), 200))
    with _conn() as conn:
        cur = conn.cursor()
        if recipe:
            cur.execute(
                _q("""SELECT duration_sec FROM render_events
                      WHERE status = 'succeeded' AND duration_sec > 30 AND recipe = ?
                      ORDER BY created_at DESC LIMIT ?"""),
                (recipe, lookback),
            )
        else:
            cur.execute(
                _q("""SELECT duration_sec FROM render_events
                      WHERE status = 'succeeded' AND duration_sec > 30
                      ORDER BY created_at DESC LIMIT ?"""),
                (lookback,),
            )
        secs = sorted(float(r["duration_sec"]) for r in cur.fetchall() if r and r["duration_sec"])
    if len(secs) < 3:
        return fallback
    mid = secs[len(secs) // 2]
    minutes = mid / 60.0
    # Clamp so a wild outlier batch can't advertise 90-min waits forever
    return round(max(3.0, min(minutes, 25.0)), 1)


def requeue_cook_job(job_id: str, reason: str = "Requeued by worker drain") -> bool:
    """Put a running job back on the queue (deploy drain / crash recovery)."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("""UPDATE cook_jobs SET status = 'queued', worker_id = '',
                      started_at = 0, heartbeat_at = 0, error = ?
                  WHERE job_id = ? AND status = 'running'"""),
            (reason[:500], job_id),
        )
        return (cur.rowcount or 0) > 0


# -- Videos (per-user library) ----------------------------------------------

def create_video(
    user_id: int,
    title: str = "",
    recipe: str = "",
    video_url: str = "",
    thumbnail_url: str = "",
) -> int:
    with _conn() as conn:
        cur = conn.cursor()
        if IS_PG:
            cur.execute(
                _q("""INSERT INTO videos (user_id, title, recipe, video_url, thumbnail_url)
                      VALUES (?, ?, ?, ?, ?) RETURNING id"""),
                (user_id, title, recipe, video_url, thumbnail_url),
            )
            return cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO videos (user_id, title, recipe, video_url, thumbnail_url) VALUES (?, ?, ?, ?, ?)",
            (user_id, title, recipe, video_url, thumbnail_url),
        )
        return cur.lastrowid


def list_videos(user_id: int, limit: int = 100) -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT * FROM videos WHERE user_id = ? ORDER BY created_at DESC LIMIT ?"),
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_video(video_id: int, user_id: int) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("SELECT * FROM videos WHERE id = ? AND user_id = ?"), (video_id, user_id))
        row = cur.fetchone()
        return dict(row) if row else None


def update_video_kit(video_id: int, user_id: int, description: str, tags: str, hashtags: str) -> None:
    with _conn() as conn:
        conn.cursor().execute(
            _q("UPDATE videos SET description = ?, tags = ?, hashtags = ? WHERE id = ? AND user_id = ?"),
            (description, tags, hashtags, video_id, user_id),
        )


def delete_video(video_id: int, user_id: int) -> dict | None:
    """Delete a video row (after the caller removes the stored files). Returns the row."""
    row = get_video(video_id, user_id)
    if not row:
        return None
    with _conn() as conn:
        conn.cursor().execute(_q("DELETE FROM videos WHERE id = ? AND user_id = ?"), (video_id, user_id))
    return row


def cleanup_expired() -> int:
    """Remove expired sessions and verification codes. Returns count removed."""
    now = time.time()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("DELETE FROM sessions WHERE expires_at < ?"), (now,))
        c1 = cur.rowcount
        cur.execute(_q("DELETE FROM verify_codes WHERE expires_at < ?"), (now,))
        c2 = cur.rowcount
        return (c1 or 0) + (c2 or 0)


# -- Cook jobs (durable queue metadata) ------------------------------------

def create_cook_job(
    job_id: str,
    user_id: int,
    recipe: str = "",
    title: str = "",
    request_json: str = "",
    credit_deducted: bool = False,
    lite_mode: bool = False,
    status: str = "queued",
) -> None:
    """
    status='queued' — durable queue; workers may claim (COOK_ON_WEB=0).
    status='web_queued' — in-process web queue only; workers ignore.
    """
    if status not in ("queued", "web_queued"):
        status = "queued"
    with _conn() as conn:
        conn.cursor().execute(
            _q("""INSERT INTO cook_jobs
                  (job_id, user_id, status, recipe, title, request_json, credit_deducted,
                   lite_mode, created_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
            (
                job_id, user_id, status, recipe, title, request_json,
                1 if credit_deducted else 0, 1 if lite_mode else 0, time.time(),
            ),
        )


def update_cook_job(
    job_id: str,
    *,
    status: str | None = None,
    progress_json: str | None = None,
    result_json: str | None = None,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
    credit_deducted: bool | None = None,
    worker_id: str | None = None,
    heartbeat: bool = False,
) -> None:
    fields: list[str] = []
    vals: list = []
    if status is not None:
        fields.append("status = ?")
        vals.append(status)
    if progress_json is not None:
        fields.append("progress_json = ?")
        vals.append(progress_json)
    if result_json is not None:
        fields.append("result_json = ?")
        vals.append(result_json)
    if error is not None:
        fields.append("error = ?")
        vals.append(error)
    if started:
        fields.append("started_at = ?")
        vals.append(time.time())
    if finished:
        fields.append("finished_at = ?")
        vals.append(time.time())
    if credit_deducted is not None:
        fields.append("credit_deducted = ?")
        vals.append(1 if credit_deducted else 0)
    if worker_id is not None:
        fields.append("worker_id = ?")
        vals.append(worker_id)
    if heartbeat:
        fields.append("heartbeat_at = ?")
        vals.append(time.time())
    if not fields:
        return
    vals.append(job_id)
    with _conn() as conn:
        conn.cursor().execute(
            _q(f"UPDATE cook_jobs SET {', '.join(fields)} WHERE job_id = ?"),
            tuple(vals),
        )


def get_cook_job(job_id: str) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("SELECT * FROM cook_jobs WHERE job_id = ?"), (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def _row_count(row) -> int:
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(row.get("c") or 0)
    try:
        return int(row["c"])
    except Exception:
        return int(row[0])


def cook_queue_stats(job_id: str | None = None) -> dict:
    """FIFO position among queued jobs + running count (DB-backed)."""
    est_min = median_cook_minutes()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("SELECT COUNT(*) AS c FROM cook_jobs WHERE status = 'running'"))
        running = _row_count(cur.fetchone())
        cur.execute(_q("SELECT COUNT(*) AS c FROM cook_jobs WHERE status = 'queued'"))
        queued = _row_count(cur.fetchone())
        pos = 0
        status = "unknown"
        if job_id:
            cur.execute(
                _q("SELECT status, created_at FROM cook_jobs WHERE job_id = ?"),
                (job_id,),
            )
            row = cur.fetchone()
            if row:
                status = row["status"]
                if status == "queued":
                    cur.execute(
                        _q("""SELECT COUNT(*) AS c FROM cook_jobs
                              WHERE status = 'queued' AND created_at <= ?"""),
                        (row["created_at"],),
                    )
                    pos = _row_count(cur.fetchone()) or 1
                elif status == "running":
                    pos = 0
    work_ahead = (max(pos - 1, 0) + running) if status == "queued" else 0
    parallelism = max(running, 1)
    est_wait = 0.0
    if status == "queued" and work_ahead > 0:
        import math
        est_wait = round(math.ceil(work_ahead / parallelism) * est_min, 1)
    return {
        "status": status,
        "queue_position": pos,
        "queue_length": queued,
        "running_count": running,
        "est_wait_minutes": est_wait,
        "est_minutes_per_cook": est_min,
    }


def announce_queued_jobs() -> None:
    """Refresh progress messages for all queued jobs (DB-backed queue UX)."""
    est_min = median_cook_minutes()
    import math
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q("SELECT COUNT(*) AS c FROM cook_jobs WHERE status = 'running'"))
        running = _row_count(cur.fetchone())
        cur.execute(
            _q("""SELECT job_id, progress_json, created_at FROM cook_jobs
                  WHERE status = 'queued'
                  ORDER BY created_at ASC""")
        )
        rows = [dict(r) for r in cur.fetchall()]
    total = len(rows)
    parallelism = max(running, 1)
    for i, row in enumerate(rows):
        pos = i + 1
        work_ahead = i + running
        wait_m = int(math.ceil(work_ahead / parallelism) * est_min) if work_ahead else 0
        if work_ahead <= 0:
            msg = "You're next — starting shortly..."
        elif pos == 1 and running > 0:
            msg = f"Queued — 1 cook ahead (~{max(wait_m, 1)} min)"
        else:
            msg = f"Queued — position {pos} of {total} (~{max(wait_m, 1)} min wait)"
        try:
            progress = json.loads(row.get("progress_json") or "[]")
        except Exception:
            progress = []
        if not isinstance(progress, list):
            progress = []
        prev = progress[-1]["message"] if progress else ""
        if prev == msg:
            continue
        progress.append({"time": time.time(), "message": msg, "phase": "queued"})
        update_cook_job(row["job_id"], progress_json=json.dumps(progress[-40:]), status="queued")

def claim_cook_job(job_id: str, worker_id: str) -> dict | None:
    """Claim a specific queued job (Modal spawn path)."""
    now = time.time()
    with _conn() as conn:
        cur = conn.cursor()
        if IS_PG:
            cur.execute(
                """
                UPDATE cook_jobs SET
                    status = 'running',
                    worker_id = %s,
                    started_at = %s,
                    heartbeat_at = %s
                WHERE job_id = %s AND status = 'queued'
                RETURNING *
                """,
                (worker_id, now, now, job_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """UPDATE cook_jobs SET status = 'running', worker_id = ?,
                   started_at = ?, heartbeat_at = ?
               WHERE job_id = ? AND status = 'queued'""",
            (worker_id, now, now, job_id),
        )
        if cur.rowcount != 1:
            conn.commit()
            return None
        cur.execute("SELECT * FROM cook_jobs WHERE job_id = ?", (job_id,))
        claimed = cur.fetchone()
        conn.commit()
        return dict(claimed) if claimed else None


def claim_next_cook_job(worker_id: str) -> dict | None:
    """
    Atomically claim the oldest queued job for a worker (strict FIFO).
    Postgres: FOR UPDATE SKIP LOCKED. SQLite: BEGIN IMMEDIATE + conditional UPDATE.
    """
    now = time.time()
    if IS_PG:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cook_jobs SET
                        status = 'running',
                        worker_id = %s,
                        started_at = %s,
                        heartbeat_at = %s
                    WHERE job_id = (
                        SELECT job_id FROM cook_jobs
                        WHERE status = 'queued'
                        ORDER BY created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *
                    """,
                    (worker_id, now, now),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    # SQLite
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """SELECT job_id FROM cook_jobs
               WHERE status = 'queued'
               ORDER BY created_at ASC LIMIT 1"""
        )
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None
        jid = row["job_id"]
        cur.execute(
            """UPDATE cook_jobs SET status = 'running', worker_id = ?,
                   started_at = ?, heartbeat_at = ?
               WHERE job_id = ? AND status = 'queued'""",
            (worker_id, now, now, jid),
        )
        if cur.rowcount != 1:
            conn.commit()
            return None
        cur.execute("SELECT * FROM cook_jobs WHERE job_id = ?", (jid,))
        claimed = cur.fetchone()
        conn.commit()
        return dict(claimed) if claimed else None


def reclaim_stale_cook_jobs(stale_seconds: int = 180) -> int:
    """Re-queue jobs stuck in running with a dead worker heartbeat."""
    cutoff = time.time() - stale_seconds
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("""UPDATE cook_jobs SET status = 'queued', worker_id = '',
                      started_at = 0, heartbeat_at = 0,
                      error = 'Requeued after stale worker heartbeat'
                  WHERE status = 'running'
                    AND COALESCE(heartbeat_at, 0) > 0
                    AND heartbeat_at < ?"""),
            (cutoff,),
        )
        return cur.rowcount or 0


def append_cook_progress(job_id: str, message: str, phase: str = "running") -> None:
    """Append one progress line and bump heartbeat (worker → web SSE via DB)."""
    row = get_cook_job(job_id)
    if not row:
        return
    try:
        progress = json.loads(row.get("progress_json") or "[]")
    except Exception:
        progress = []
    if not isinstance(progress, list):
        progress = []
    progress.append({"time": time.time(), "message": message, "phase": phase})
    progress = progress[-60:]
    update_cook_job(
        job_id,
        progress_json=json.dumps(progress),
        heartbeat=True,
        status=row.get("status"),
    )


def backend_name() -> str:
    return "postgres" if IS_PG else "sqlite"


# Initialize on import
print(f"[db] Using {backend_name()} backend")
_init_db()
