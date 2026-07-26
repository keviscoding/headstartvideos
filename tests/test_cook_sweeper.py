"""Tests for the abandoned-cook sweeper (fail + refund exactly once).

Run from videofactory/:
  python -m pytest tests/test_cook_sweeper.py -q
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import webapp.database as database


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Point the module at a throwaway SQLite file, freshly schema'd."""
    if database.IS_PG:
        pytest.skip("sweeper tests target the SQLite path")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database._init_db()
    return database


def _mk_user(db, credits: int = 5) -> int:
    user = db.create_user("cook@example.com")
    db.update_user(user["id"], credits=credits)
    return user["id"]


def _mk_job(db, user_id: int, job_id: str, *, status: str, age_sec: float,
            credits_charged: int = 1, credit_deducted: bool = True) -> None:
    db.create_cook_job(
        job_id=job_id,
        user_id=user_id,
        recipe="animated_explainer",
        title="t",
        request_json=json.dumps({"credits_charged": credits_charged}),
        credit_deducted=credit_deducted,
        lite_mode=False,
    )
    # create_cook_job clamps status to queued/web_queued, so age + status go in raw.
    old = time.time() - age_sec
    stamp = old if status == "running" else 0
    with db._conn() as conn:
        conn.cursor().execute(
            db._q("UPDATE cook_jobs SET status = ?, created_at = ?, started_at = ?, "
                  "heartbeat_at = ? WHERE job_id = ?"),
            (status, old, stamp, stamp, job_id),
        )


def _credits(db, user_id: int) -> int:
    return db.get_user_by_id(user_id)["credits"]


def test_running_job_with_dead_heartbeat_is_failed_and_refunded(db):
    uid = _mk_user(db, credits=4)
    _mk_job(db, uid, "j1", status="running", age_sec=1200)

    swept = db.fail_abandoned_cook_jobs(900, 1800)

    assert [s["job_id"] for s in swept] == ["j1"]
    assert swept[0]["refunded"] == 1
    assert db.get_cook_job("j1")["status"] == "error"
    assert _credits(db, uid) == 5


def test_healthy_running_job_is_left_alone(db):
    uid = _mk_user(db, credits=4)
    _mk_job(db, uid, "j1", status="running", age_sec=60)

    assert db.fail_abandoned_cook_jobs(900, 1800) == []
    assert db.get_cook_job("j1")["status"] == "running"
    assert _credits(db, uid) == 4


def test_queued_job_that_never_spawned_is_refunded(db):
    uid = _mk_user(db, credits=0)
    _mk_job(db, uid, "j1", status="queued", age_sec=3600)

    swept = db.fail_abandoned_cook_jobs(900, 1800)

    assert swept[0]["prev_status"] == "queued"
    assert _credits(db, uid) == 1


def test_recently_queued_job_is_left_alone(db):
    uid = _mk_user(db, credits=0)
    _mk_job(db, uid, "j1", status="queued", age_sec=300)

    assert db.fail_abandoned_cook_jobs(900, 1800) == []
    assert _credits(db, uid) == 0


def test_hq_job_refunds_all_three_credits(db):
    uid = _mk_user(db, credits=0)
    _mk_job(db, uid, "j1", status="running", age_sec=1200, credits_charged=3)

    swept = db.fail_abandoned_cook_jobs(900, 1800)

    assert swept[0]["refunded"] == 3
    assert _credits(db, uid) == 3


def test_sweeping_twice_refunds_only_once(db):
    """The guarded credit_deducted flip must make the refund idempotent."""
    uid = _mk_user(db, credits=0)
    _mk_job(db, uid, "j1", status="running", age_sec=1200)

    db.fail_abandoned_cook_jobs(900, 1800)
    assert _credits(db, uid) == 1

    # Re-age the now-errored row and sweep again.
    old = time.time() - 5000
    with db._conn() as conn:
        conn.cursor().execute(
            db._q("UPDATE cook_jobs SET status = 'running', heartbeat_at = ? WHERE job_id = ?"),
            (old, "j1"),
        )
    db.fail_abandoned_cook_jobs(900, 1800)

    assert _credits(db, uid) == 1, "second sweep must not refund again"


def test_uncharged_job_is_failed_without_refund(db):
    """Admin/BYOK cooks are never charged, so must not be credited."""
    uid = _mk_user(db, credits=2)
    _mk_job(db, uid, "j1", status="running", age_sec=1200, credit_deducted=False)

    swept = db.fail_abandoned_cook_jobs(900, 1800)

    assert swept[0]["refunded"] == 0
    assert db.get_cook_job("j1")["status"] == "error"
    assert _credits(db, uid) == 2


def test_completed_job_is_never_touched(db):
    uid = _mk_user(db, credits=1)
    _mk_job(db, uid, "j1", status="complete", age_sec=99999)

    assert db.fail_abandoned_cook_jobs(900, 1800) == []
    assert db.get_cook_job("j1")["status"] == "complete"
    assert _credits(db, uid) == 1


def test_running_job_with_zero_heartbeat_falls_back_to_created_at(db):
    """reclaim_stale_cook_jobs ignores heartbeat_at=0 rows; the sweeper must not."""
    uid = _mk_user(db, credits=0)
    _mk_job(db, uid, "j1", status="running", age_sec=1200)
    with db._conn() as conn:
        conn.cursor().execute(
            db._q("UPDATE cook_jobs SET heartbeat_at = 0, started_at = 0 WHERE job_id = ?"),
            ("j1",),
        )

    swept = db.fail_abandoned_cook_jobs(900, 1800)

    assert [s["job_id"] for s in swept] == ["j1"]
    assert _credits(db, uid) == 1


def test_thresholds_are_floored_to_five_minutes(db):
    """A tiny threshold must not nuke healthy in-flight cooks."""
    uid = _mk_user(db, credits=0)
    _mk_job(db, uid, "j1", status="running", age_sec=60)

    assert db.fail_abandoned_cook_jobs(1, 1) == []
    assert db.get_cook_job("j1")["status"] == "running"
