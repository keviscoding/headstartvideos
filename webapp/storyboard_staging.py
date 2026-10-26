"""Storyboard Match -> Assemble that works whichever container answers (two web containers, 2026-10-26).

Match used to leave staging.json and the matched clips on the disk of the container that answered it, and Assemble
read them back from its own disk: with two containers about half the assembles answered 400 ("run Match first").
Now Match bundles the matched clips into one zip on a PRIVATE Spaces key and records the match here; Assemble reads
the record on any container and hands the cook a presigned link to the bundle (no public link to a customer's clips).

A match id is "<job id>_<stamp>" (STAGING_ID, the rule from the security fix of 2026-10-26) and a record is only ever
read for the account that made it.
"""
from __future__ import annotations

import json
import re
import threading
import time
import zipfile
from pathlib import Path

from webapp import database as db
from webapp import storage

STAGING_ID = re.compile(r"[A-Za-z0-9_-]{1,160}")
CLIP_SUFFIXES = (".mp4", ".webm", ".mov", ".mkv", ".m4v")
LINK_SECONDS = 3 * 86400            # the cook fetches the bundle within minutes; a queued one still has days

_ready: set = set()
_ready_lock = threading.Lock()


def initialize() -> None:
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            db.schema_lock(conn, "storyboard_staging")
            conn.execute("""CREATE TABLE IF NOT EXISTS storyboard_staging (
                user_id INTEGER NOT NULL, staging_id TEXT NOT NULL, parent_job_id TEXT NOT NULL,
                clips_key TEXT NOT NULL DEFAULT '', clips_dir TEXT NOT NULL DEFAULT '',
                clip_count INTEGER NOT NULL DEFAULT 0, match_json TEXT NOT NULL DEFAULT '[]',
                created_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (user_id, staging_id))""")
        _ready.add(key)


def valid_id(staging_id: str) -> bool:
    return bool(STAGING_ID.fullmatch(staging_id or ""))


def bundle_key(user_id: int, staging_id: str) -> str:
    return f"storyboard/{int(user_id)}/staging/{staging_id}/clips_bundle.zip"


def bundle(clips_dir: Path, zip_path: Path) -> int:
    """The clips in `clips_dir` in one zip (stored: video doesn't compress), and how many there are."""
    clips = [p for p in sorted(Path(clips_dir).iterdir()) if p.is_file() and p.suffix.lower() in CLIP_SUFFIXES]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for p in clips:
            zf.write(p, arcname=p.name)
    return len(clips)


def save(user_id: int, staging_id: str, parent_job_id: str, clips_dir: Path, match_report: list) -> dict:
    """Bundle the matched clips to private storage and record the match. Raises when the bundle can't be stored:
    a match another container can't find would fail at Assemble instead."""
    if not valid_id(staging_id):
        raise ValueError("bad staging id")
    clips_dir = Path(clips_dir)
    zip_path = clips_dir.parent / "clips_bundle.zip"
    count = bundle(clips_dir, zip_path)
    clips_key = storage.store_private_file(str(zip_path), bundle_key(user_id, staging_id), "application/zip")
    record = {"user_id": int(user_id), "staging_id": staging_id, "parent_job_id": parent_job_id,
              "clips_key": clips_key, "clips_dir": str(clips_dir), "clip_count": count,
              "match_json": json.dumps(match_report or []), "created_at": time.time()}
    initialize()
    with db._conn() as conn:
        conn.execute(db._q("DELETE FROM storyboard_staging WHERE user_id=? AND staging_id=?"),
                     (record["user_id"], staging_id))
        conn.execute(db._q("""INSERT INTO storyboard_staging (user_id, staging_id, parent_job_id, clips_key, clips_dir,
            clip_count, match_json, created_at) VALUES (?,?,?,?,?,?,?,?)"""),
                     tuple(record[k] for k in ("user_id", "staging_id", "parent_job_id", "clips_key", "clips_dir",
                                               "clip_count", "match_json", "created_at")))
    return record


def load(user_id: int, staging_id: str) -> dict | None:
    """This account's match, or None (another account's id, an unknown one, or a malformed one)."""
    if not valid_id(staging_id):
        return None
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM storyboard_staging WHERE user_id=? AND staging_id=?"),
                           (int(user_id), staging_id)).fetchone()
    return dict(row) if row else None


def clips_link(record: dict) -> str:
    """A link the cook can fetch the match's bundle from: presigned on Spaces, the local path without Spaces."""
    key = (record or {}).get("clips_key") or ""
    return storage.presigned_get_url(key, expires=LINK_SECONDS) if key else ""
