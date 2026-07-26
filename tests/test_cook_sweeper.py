"""Tests for abandoned-cook detection, refunds, and machine cleanup.

The safety property that matters most here: a healthy cook must never be killed.
Heartbeats only advance on progress callbacks, and a long ffmpeg assembly emits
none for many minutes, so silence alone must not be enough to fail a job.

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


def _ids(rows) -> list[str]:
    return sorted(r["job_id"] for r in rows)


class TestListStaleCookJobs:
    def test_quiet_running_job_is_a_candidate(self, db):
        uid = _mk_user(db)
        _mk_job(db, uid, "j1", status="running", age_sec=1200)

        assert _ids(db.list_stale_cook_jobs(600, 1800)) == ["j1"]

    def test_recently_active_job_is_not_a_candidate(self, db):
        uid = _mk_user(db)
        _mk_job(db, uid, "j1", status="running", age_sec=60)

        assert db.list_stale_cook_jobs(600, 1800) == []

    def test_long_queued_job_is_a_candidate(self, db):
        uid = _mk_user(db)
        _mk_job(db, uid, "j1", status="queued", age_sec=3600)

        assert _ids(db.list_stale_cook_jobs(600, 1800)) == ["j1"]

    def test_recently_queued_job_is_not_a_candidate(self, db):
        uid = _mk_user(db)
        _mk_job(db, uid, "j1", status="queued", age_sec=300)

        assert db.list_stale_cook_jobs(600, 1800) == []

    def test_finished_jobs_are_never_candidates(self, db):
        uid = _mk_user(db)
        _mk_job(db, uid, "done", status="complete", age_sec=99999)
        _mk_job(db, uid, "failed", status="error", age_sec=99999)
        _mk_job(db, uid, "gone", status="cancelled", age_sec=99999)

        assert db.list_stale_cook_jobs(600, 1800) == []

    def test_listing_never_mutates_anything(self, db):
        """It is read-only by contract; the caller decides."""
        uid = _mk_user(db, credits=2)
        _mk_job(db, uid, "j1", status="running", age_sec=9999)

        db.list_stale_cook_jobs(600, 1800)

        assert db.get_cook_job("j1")["status"] == "running"
        assert _credits(db, uid) == 2

    def test_zero_heartbeat_falls_back_to_created_at(self, db):
        """reclaim_stale_cook_jobs skips heartbeat_at=0 rows; this must not."""
        uid = _mk_user(db)
        _mk_job(db, uid, "j1", status="running", age_sec=1200)
        with db._conn() as conn:
            conn.cursor().execute(
                db._q("UPDATE cook_jobs SET heartbeat_at = 0, started_at = 0 "
                      "WHERE job_id = ?"),
                ("j1",),
            )

        assert _ids(db.list_stale_cook_jobs(600, 1800)) == ["j1"]


class TestCookSilenceSeconds:
    def test_uses_heartbeat_when_present(self, db):
        assert db.cook_silence_seconds({"heartbeat_at": time.time() - 300}) == pytest.approx(300, abs=5)

    def test_falls_back_to_started_then_created(self, db):
        started = {"heartbeat_at": 0, "started_at": time.time() - 200, "created_at": 1}
        assert db.cook_silence_seconds(started) == pytest.approx(200, abs=5)

        created = {"heartbeat_at": 0, "started_at": 0, "created_at": time.time() - 100}
        assert db.cook_silence_seconds(created) == pytest.approx(100, abs=5)

    def test_no_timestamps_is_zero(self, db):
        assert db.cook_silence_seconds({}) == 0.0


class TestFailCookJobWithRefund:
    def test_fails_and_refunds(self, db):
        uid = _mk_user(db, credits=4)
        _mk_job(db, uid, "j1", status="running", age_sec=1200)

        failed, refunded = db.fail_cook_job_with_refund("j1", "running", "dead")

        assert (failed, refunded) == (True, 1)
        assert db.get_cook_job("j1")["status"] == "error"
        assert db.get_cook_job("j1")["error"] == "dead"
        assert _credits(db, uid) == 5

    def test_hq_job_refunds_all_three_credits(self, db):
        uid = _mk_user(db, credits=0)
        _mk_job(db, uid, "j1", status="running", age_sec=1200, credits_charged=3)

        assert db.fail_cook_job_with_refund("j1", "running", "dead")[1] == 3
        assert _credits(db, uid) == 3

    def test_second_call_refunds_nothing(self, db):
        """The guarded credit_deducted flip makes the refund idempotent."""
        uid = _mk_user(db, credits=0)
        _mk_job(db, uid, "j1", status="running", age_sec=1200)

        db.fail_cook_job_with_refund("j1", "running", "dead")
        assert _credits(db, uid) == 1

        again = db.fail_cook_job_with_refund("j1", "running", "dead")

        assert again == (False, 0), "job is no longer 'running' — must not re-fail"
        assert _credits(db, uid) == 1

    def test_worker_that_finished_first_wins(self, db):
        """A cook that completed while we were deciding must be left intact."""
        uid = _mk_user(db, credits=0)
        _mk_job(db, uid, "j1", status="running", age_sec=1200)
        db.update_cook_job("j1", status="complete")

        failed, refunded = db.fail_cook_job_with_refund("j1", "running", "dead")

        assert (failed, refunded) == (False, 0)
        assert db.get_cook_job("j1")["status"] == "complete"
        assert _credits(db, uid) == 0, "a completed cook must not be refunded"

    def test_uncharged_job_is_failed_without_refund(self, db):
        """Admin/BYOK cooks are never charged, so must not be credited."""
        uid = _mk_user(db, credits=2)
        _mk_job(db, uid, "j1", status="running", age_sec=1200, credit_deducted=False)

        failed, refunded = db.fail_cook_job_with_refund("j1", "running", "dead")

        assert (failed, refunded) == (True, 0)
        assert _credits(db, uid) == 2

    def test_missing_job_is_a_noop(self, db):
        assert db.fail_cook_job_with_refund("nope", "running", "dead") == (False, 0)

    def test_queued_job_refund(self, db):
        uid = _mk_user(db, credits=0)
        _mk_job(db, uid, "j1", status="queued", age_sec=3600)

        assert db.fail_cook_job_with_refund("j1", "queued", "never started") == (True, 1)
        assert _credits(db, uid) == 1


class TestCookDeathReason:
    """The healthy-cook safety net. Silence alone must never be enough."""

    @pytest.fixture()
    def reason(self, monkeypatch):
        import config as cfg
        import webapp.server as server

        monkeypatch.setattr(cfg, "COOK_HUNG_SECONDS", 3600, raising=False)
        monkeypatch.setattr(server, "COOK_ON_FLY", True, raising=False)

        def _set_machine(value):
            """value: dict (machine), None (gone), or Exception (API down)."""
            import webapp.fly_bridge as fb

            def _find(_job_id):
                if isinstance(value, Exception):
                    raise value
                return value

            monkeypatch.setattr(fb, "find_cook_machine", _find)

        return server._cook_death_reason, _set_machine

    def test_running_machine_quiet_for_ten_minutes_is_left_alone(self, reason):
        """The critical case: a long ffmpeg assembly is silent but healthy."""
        death, set_machine = reason
        set_machine({"id": "abc", "state": "started"})

        assert death("j1", 600) == ""

    def test_running_machine_quiet_for_fifty_minutes_is_still_left_alone(self, reason):
        death, set_machine = reason
        set_machine({"id": "abc", "state": "started"})

        assert death("j1", 3000) == ""

    def test_missing_machine_is_failed_immediately(self, reason):
        """Machines are one-shot per job, so absence is proof it cannot resume."""
        death, set_machine = reason
        set_machine(None)

        assert death("j1", 600) == "machine no longer exists"

    @pytest.mark.parametrize("state", ["stopped", "failed", "destroyed", "suspended"])
    def test_dead_machine_states_are_failed_immediately(self, reason, state):
        death, set_machine = reason
        set_machine({"id": "abc", "state": state})

        assert death("j1", 600) == f"machine is {state}"

    def test_running_machine_past_hung_threshold_is_failed(self, reason):
        death, set_machine = reason
        set_machine({"id": "abc", "state": "started"})

        assert death("j1", 3700) == "silent past hung threshold"

    def test_fly_outage_does_not_fail_in_flight_cooks(self, reason):
        """An API outage must not look like every cook died at once."""
        death, set_machine = reason
        set_machine(RuntimeError("fly api 500"))

        assert death("j1", 600) == ""

    def test_fly_outage_still_honours_the_long_timer(self, reason):
        death, set_machine = reason
        set_machine(RuntimeError("fly api 500"))

        assert "fly unreachable" in death("j1", 3700)

    def test_without_fly_only_the_long_timer_applies(self, monkeypatch):
        import config as cfg
        import webapp.server as server

        monkeypatch.setattr(cfg, "COOK_HUNG_SECONDS", 3600, raising=False)
        monkeypatch.setattr(server, "COOK_ON_FLY", False, raising=False)

        assert server._cook_death_reason("j1", 600) == ""
        assert server._cook_death_reason("j1", 3700) == "silent past hung threshold"


class TestFindCookMachine:
    """Machine matching must never resolve to a different job."""

    @pytest.fixture()
    def fly(self, monkeypatch):
        import webapp.fly_bridge as fb

        monkeypatch.setattr(fb.config, "FLY_COOK_APP", "cook-app", raising=False)

        def _set_machines(machines):
            monkeypatch.setattr(fb, "_request", lambda *a, **k: machines)

        return fb, _set_machines

    def _machine(self, mid, job_id, module="webapp.fly_oneshot", state="started"):
        return {
            "id": mid,
            "state": state,
            "config": {"init": {"cmd": ["python", "-m", module, job_id]}},
        }

    def test_matches_only_its_own_job(self, fly):
        fb, set_machines = fly
        set_machines([self._machine("m1", "job-a"), self._machine("m2", "job-b")])

        assert fb.find_cook_machine("job-b")["id"] == "m2"

    def test_unknown_job_matches_nothing(self, fly):
        fb, set_machines = fly
        set_machines([self._machine("m1", "job-a")])

        assert fb.find_cook_machine("job-zzz") is None

    def test_niche_scrape_machines_are_out_of_scope(self, fly):
        """Same app and image, different one-shot — must never be destroyed."""
        fb, set_machines = fly
        set_machines([self._machine("m1", "job-a", module="webapp.fly_niche_oneshot")])

        assert fb.find_cook_machine("job-a") is None

    def test_partial_id_does_not_match(self, fly):
        """Exact argv match only, so a prefix cannot hit the wrong machine."""
        fb, set_machines = fly
        set_machines([self._machine("m1", "job-abcdef")])

        assert fb.find_cook_machine("job-abc") is None

    def test_empty_job_id_matches_nothing(self, fly):
        fb, set_machines = fly
        set_machines([self._machine("m1", "job-a")])

        assert fb.find_cook_machine("") is None

    def test_malformed_machine_entries_are_skipped(self, fly):
        fb, set_machines = fly
        set_machines([None, {}, {"config": None}, {"config": {"init": {"cmd": "str"}}}])

        assert fb.find_cook_machine("job-a") is None


class TestDestroyCookMachine:
    @pytest.fixture()
    def fly(self, monkeypatch):
        import webapp.fly_bridge as fb

        monkeypatch.setattr(fb.config, "FLY_COOK_APP", "cook-app", raising=False)
        return fb, monkeypatch

    def test_reports_already_gone(self, fly):
        fb, monkeypatch = fly
        monkeypatch.setattr(fb, "find_cook_machine", lambda _j: None)

        assert fb.destroy_cook_machine("j1") == "already gone"

    def test_deletes_the_matched_machine(self, fly):
        fb, monkeypatch = fly
        monkeypatch.setattr(
            fb, "find_cook_machine", lambda _j: {"id": "m9", "state": "started"}
        )
        calls = []
        monkeypatch.setattr(fb, "_request", lambda m, p, b=None: calls.append((m, p)))

        result = fb.destroy_cook_machine("j1")

        assert calls == [("DELETE", "/v1/apps/cook-app/machines/m9?force=true")]
        assert "destroyed m9" in result

    def test_lookup_failure_never_raises(self, fly):
        """A Fly outage must not stop the refund that accompanies this."""
        fb, monkeypatch = fly

        def _boom(_j):
            raise RuntimeError("fly down")

        monkeypatch.setattr(fb, "find_cook_machine", _boom)

        assert "lookup failed" in fb.destroy_cook_machine("j1")

    def test_delete_failure_never_raises(self, fly):
        fb, monkeypatch = fly
        monkeypatch.setattr(
            fb, "find_cook_machine", lambda _j: {"id": "m9", "state": "started"}
        )

        def _boom(*a, **k):
            raise RuntimeError("409 conflict")

        monkeypatch.setattr(fb, "_request", _boom)

        assert "failed" in fb.destroy_cook_machine("j1")


class TestCookSweepReason:
    """Which statuses are eligible at all. Queued waiting must be respected."""

    @pytest.fixture()
    def sweep(self, monkeypatch):
        import config as cfg
        import webapp.server as server

        monkeypatch.setattr(cfg, "COOK_HUNG_SECONDS", 3600, raising=False)

        def _configure(*, cook_on_web: bool, machine=None):
            import webapp.fly_bridge as fb

            monkeypatch.setattr(server, "COOK_ON_WEB", cook_on_web, raising=False)
            monkeypatch.setattr(server, "COOK_ON_FLY", not cook_on_web, raising=False)
            monkeypatch.setattr(fb, "find_cook_machine", lambda _j: machine)

        return server._cook_sweep_reason, _configure

    def test_web_queued_is_never_swept(self, sweep):
        """The in-process FIFO owns these; waiting is normal."""
        reason, configure = sweep
        configure(cook_on_web=True)

        row = {"job_id": "j1", "status": "web_queued"}
        assert reason(row, 99999) == ""

    def test_queued_is_not_swept_when_cooking_on_web(self, sweep):
        """A single-slot web queue makes long waits legitimate."""
        reason, configure = sweep
        configure(cook_on_web=True)

        row = {"job_id": "j1", "status": "queued"}
        assert reason(row, 99999) == ""

    def test_queued_is_swept_when_machines_spawn_per_job(self, sweep):
        reason, configure = sweep
        configure(cook_on_web=False)

        row = {"job_id": "j1", "status": "queued"}
        assert reason(row, 3600) == "no worker ever claimed it"

    @pytest.mark.parametrize("status", ["complete", "error", "cancelled", "", "weird"])
    def test_non_actionable_statuses_are_ignored(self, sweep, status):
        reason, configure = sweep
        configure(cook_on_web=False)

        assert reason({"job_id": "j1", "status": status}, 99999) == ""

    def test_running_delegates_to_machine_evidence(self, sweep):
        reason, configure = sweep
        configure(cook_on_web=False, machine=None)

        row = {"job_id": "j1", "status": "running"}
        assert reason(row, 600) == "machine no longer exists"

    def test_running_with_healthy_machine_is_left_alone(self, sweep):
        reason, configure = sweep
        configure(cook_on_web=False, machine={"id": "m1", "state": "started"})

        row = {"job_id": "j1", "status": "running"}
        assert reason(row, 600) == ""


class TestUnknownFlyPayload:
    """An unreadable Fly response is "we don't know", never "confirmed gone"."""

    @pytest.mark.parametrize("payload", [None, {}, {"error": "bad gateway"}, "nope", 0])
    def test_non_list_payload_raises_instead_of_reporting_gone(self, payload, monkeypatch):
        import config as cfg
        from webapp import fly_bridge

        monkeypatch.setattr(cfg, "FLY_COOK_APP", "cook-app")
        monkeypatch.setattr(fly_bridge, "_request", lambda *a, **k: payload)

        with pytest.raises(RuntimeError):
            fly_bridge.find_cook_machine("job-1")

    def test_a_quiet_cook_survives_an_unreadable_payload(self, monkeypatch):
        """The regression this guards: mass-failing live cooks during an outage."""
        import config as cfg
        import webapp.server as server
        from webapp import fly_bridge

        monkeypatch.setattr(cfg, "FLY_COOK_APP", "cook-app")
        monkeypatch.setattr(cfg, "COOK_HUNG_SECONDS", 3600)
        monkeypatch.setattr(server, "COOK_ON_FLY", True)
        monkeypatch.setattr(fly_bridge, "_request", lambda *a, **k: {"error": "gateway"})

        assert server._cook_death_reason("job-1", 700) == ""
        assert "unreachable" in server._cook_death_reason("job-1", 3601)

    def test_empty_list_still_means_gone(self, monkeypatch):
        """A real, readable "no machines" answer must still be actionable."""
        import config as cfg
        from webapp import fly_bridge

        monkeypatch.setattr(cfg, "FLY_COOK_APP", "cook-app")
        monkeypatch.setattr(fly_bridge, "_request", lambda *a, **k: [])

        assert fly_bridge.find_cook_machine("job-1") is None
