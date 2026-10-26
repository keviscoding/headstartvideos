"""Where new customers' setup is stuck, counted for the team.

A connected Studio sends what its setup still lacks (local_runtime/cr_setup_report.py): the keys of the setup checks
that don't pass yet (ffmpeg, node, claude, models, network…), whether setup is ready, the computer's family (windows,
mac or linux) and the Studio's version. Nothing else: no names, paths, keys or messages. One row per account keeps its
latest report; GET /api/admin/activation counts them for the customers who haven't finished a first video, so the team
sees which check stops people instead of waiting for Discord.
"""
from __future__ import annotations

import json
import re
import threading
import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from webapp import database as db

KEY = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
OS_FAMILIES = {"windows", "mac", "linux", "other"}
MAX_FAILING = 24

_ready: set = set()
_ready_lock = threading.Lock()


class SetupReport(BaseModel):
    """What a Studio's setup still lacks. Unknown fields are ignored, so a newer Studio never fails to report."""
    model_config = ConfigDict(extra="ignore")
    ready: bool = False
    failing: list[str] = Field(default_factory=list, max_length=64)
    os: str = "other"
    version: str = Field(default="", max_length=40)

    @field_validator("failing")
    @classmethod
    def _keys(cls, value):
        return sorted({k for k in value if isinstance(k, str) and KEY.match(k)})[:MAX_FAILING]

    @field_validator("os")
    @classmethod
    def _os(cls, value):
        return value if value in OS_FAMILIES else "other"

    @field_validator("version")
    @classmethod
    def _version(cls, value):
        return value if re.fullmatch(r"[\w.\-]{0,40}", value or "") else ""


def _key():
    return (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))


def initialize():
    """The table, made once (the server's startup calls this, under an advisory lock on Postgres)."""
    key = _key()
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            if db.IS_PG:
                conn.execute("SELECT pg_advisory_xact_lock(726346124)")
            conn.execute("""CREATE TABLE IF NOT EXISTS studio_setup_reports (
                user_id INTEGER PRIMARY KEY, ready INTEGER NOT NULL DEFAULT 0, failing_json TEXT NOT NULL DEFAULT '[]',
                os TEXT NOT NULL DEFAULT 'other', version TEXT NOT NULL DEFAULT '',
                first_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
                first_ready_at DOUBLE PRECISION)""")
        _ready.add(key)


def record(user_id: int, report: SetupReport, now: float | None = None) -> None:
    """Keep this account's latest report (and when its setup first passed)."""
    initialize()
    now = time.time() if now is None else now
    failing = json.dumps(report.failing)
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO studio_setup_reports
            (user_id, ready, failing_json, os, version, first_at, updated_at, first_ready_at) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET ready=excluded.ready, failing_json=excluded.failing_json,
            os=excluded.os, version=excluded.version, updated_at=excluded.updated_at,
            first_ready_at=COALESCE(studio_setup_reports.first_ready_at, excluded.first_ready_at)"""),
                     (int(user_id), int(report.ready), failing, report.os, report.version, now, now,
                      now if report.ready else None))


def stuck(user_ids) -> dict:
    """For these accounts (customers without a finished first video): how many Studios report setup not ready, by the
    check that fails, by computer family and by version. Counts only."""
    ids = sorted({int(u) for u in user_ids})
    out = {"reporting": 0, "not_ready": 0, "by_check": {}, "by_os": {}, "by_version": {}}
    if not ids:
        return out
    initialize()
    rows = []
    with db._conn() as conn:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows += conn.execute(db._q("SELECT ready, failing_json, os, version FROM studio_setup_reports WHERE user_id IN ("
                                       + ",".join("?" * len(chunk)) + ")"), tuple(chunk)).fetchall()
    for r in rows:
        out["reporting"] += 1
        if r["ready"]:
            continue
        out["not_ready"] += 1
        for k in json.loads(r["failing_json"] or "[]"):
            out["by_check"][k] = out["by_check"].get(k, 0) + 1
        out["by_os"][r["os"]] = out["by_os"].get(r["os"], 0) + 1
        out["by_version"][r["version"] or "?"] = out["by_version"].get(r["version"] or "?", 0) + 1
    out["by_check"] = dict(sorted(out["by_check"].items(), key=lambda kv: (-kv[1], kv[0])))
    return out
