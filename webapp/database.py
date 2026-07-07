"""
SQLite database for users, sessions, and credits.
Auto-creates tables on first import.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "channelrecipe.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
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
    expires_at  REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS verify_codes (
    email       TEXT NOT NULL,
    code        TEXT NOT NULL,
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    expires_at  REAL NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);
"""


def _init_db():
    with _conn() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# -- Users ------------------------------------------------------------------

def get_user_by_email(email: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(email: str) -> dict:
    email = email.lower().strip()
    with _conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row)


def update_user(user_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [user_id]
    with _conn() as conn:
        conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)


def deduct_credit(user_id: int) -> bool:
    """Deduct 1 credit. Returns False if insufficient."""
    with _conn() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["credits"] < 1:
            return False
        conn.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (user_id,))
        return True


def refund_credit(user_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET credits = credits + 1 WHERE id = ?", (user_id,))


# -- Verification codes -----------------------------------------------------

def create_verify_code(email: str) -> str:
    code = f"{secrets.randbelow(1000000):06d}"
    email = email.lower().strip()
    expires = time.time() + 600  # 10 minutes
    with _conn() as conn:
        conn.execute("DELETE FROM verify_codes WHERE email = ? AND used = 0", (email,))
        conn.execute(
            "INSERT INTO verify_codes (email, code, expires_at) VALUES (?, ?, ?)",
            (email, code, expires),
        )
    return code


def verify_code(email: str, code: str) -> bool:
    email = email.lower().strip()
    with _conn() as conn:
        row = conn.execute(
            "SELECT rowid FROM verify_codes WHERE email = ? AND code = ? AND used = 0 AND expires_at > ?",
            (email, code, time.time()),
        ).fetchone()
        if not row:
            return False
        conn.execute("UPDATE verify_codes SET used = 1 WHERE rowid = ?", (row["rowid"],))
        return True


# -- Sessions ---------------------------------------------------------------

SESSION_DURATION = 30 * 24 * 3600  # 30 days


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.time() + SESSION_DURATION
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
    return token


def get_session_user(token: str) -> dict | None:
    if not token:
        return None
    with _conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON s.user_id = u.id
               WHERE s.token = ? AND s.expires_at > ?""",
            (token, time.time()),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def cleanup_expired() -> int:
    """Remove expired sessions and verification codes. Returns count removed."""
    now = time.time()
    with _conn() as conn:
        c1 = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,)).rowcount
        c2 = conn.execute("DELETE FROM verify_codes WHERE expires_at < ?", (now,)).rowcount
        return c1 + c2


# Initialize on import
_init_db()
