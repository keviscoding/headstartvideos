"""
Database layer for users, sessions, credits, and render telemetry.

Uses Postgres when DATABASE_URL is set (production, durable across redeploys
and multiple instances), and falls back to a local SQLite file otherwise
(handy for local development). Auto-creates tables on first import.
"""
from __future__ import annotations

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
    credits     INTEGER NOT NULL DEFAULT 3,
    stripe_customer_id TEXT DEFAULT '',
    stripe_sub_id      TEXT DEFAULT ''
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
"""

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    plan        TEXT NOT NULL DEFAULT 'free',
    credits     INTEGER NOT NULL DEFAULT 3,
    stripe_customer_id TEXT DEFAULT '',
    stripe_sub_id      TEXT DEFAULT ''
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
"""


def _init_db():
    schema = _SCHEMA_PG if IS_PG else _SCHEMA_SQLITE
    with _conn() as conn:
        if IS_PG:
            with conn.cursor() as cur:
                cur.execute(schema)
                cur.execute(_INDEXES)
        else:
            conn.executescript(schema)
            conn.executescript(_INDEXES)


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
    return {
        "days": days,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "success_rate": round(succeeded / total * 100, 1) if total else 0,
        "total_cost_pence": round(total_cost, 1),
        "avg_cost_pence": round(total_cost / total, 2) if total else 0,
        "avg_duration_sec": round(avg_dur, 1),
        "by_recipe": by_recipe,
    }


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


def backend_name() -> str:
    return "postgres" if IS_PG else "sqlite"


# Initialize on import
print(f"[db] Using {backend_name()} backend")
_init_db()
