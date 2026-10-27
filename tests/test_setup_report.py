"""Where new customers' setup is stuck: a connected Studio reports the keys of its failing setup checks (never names,
paths, keys or messages), and the admin activation report counts them for customers without a finished video."""
import json
import sys
from pathlib import Path

import pytest

from webapp import database as db
from webapp import milestones
from webapp import studio_setup

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_setup_report  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(milestones, "_ready", set())
    monkeypatch.setattr(studio_setup, "_ready", set())
    return tmp_path


def report(**kw):
    return studio_setup.SetupReport(**kw)


def test_a_report_keeps_only_safe_fields(fresh_db):
    got = report(ready=False, failing=["ffmpeg", "node", "ffmpeg", "C:\\Users\\jane", "Bad Key", "x" * 40],
                 os="darwin", version="5.3-admin.105; rm -rf", email="jane@example.com")
    assert got.failing == ["ffmpeg", "node"], "only check keys, once each"
    assert got.os == "other" and got.version == "" and not hasattr(got, "email")


def test_the_latest_report_wins_and_the_first_ready_time_stays(fresh_db):
    studio_setup.record(7, report(ready=False, failing=["claude"], os="mac", version="5.3-admin.105"), now=100.0)
    studio_setup.record(7, report(ready=True, failing=[], os="mac", version="5.3-admin.105"), now=200.0)
    studio_setup.record(7, report(ready=False, failing=["network"], os="mac", version="5.3-admin.106"), now=300.0)
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM studio_setup_reports WHERE user_id=7").fetchone()
    assert json.loads(row["failing_json"]) == ["network"] and row["version"] == "5.3-admin.106"
    assert row["first_ready_at"] == 200.0 and row["first_at"] == 100.0


def test_activation_says_which_check_stops_customers_without_a_finished_video(fresh_db):
    from webapp import lifecycle_email
    now = 100 * 86400.0
    for uid in (1, 2, 3):
        milestones.record(uid, lifecycle_email.SUBSCRIBED, now=now - 5 * 86400)
        milestones.record(uid, milestones.CONNECTED, now=now - 4 * 86400)
    studio_setup.record(1, report(ready=False, failing=["ffmpeg", "claude"], os="windows", version="5.3-admin.105"))
    studio_setup.record(2, report(ready=False, failing=["claude"], os="mac", version="5.3-admin.105"))
    studio_setup.record(3, report(ready=True, os="mac", version="5.3-admin.105"))
    got = milestones.activation(30, now=now)["setup_stuck"]
    assert got["reporting"] == 3 and got["not_ready"] == 2
    assert got["by_check"] == {"claude": 2, "ffmpeg": 1} and list(got["by_check"])[0] == "claude"
    assert got["by_os"] == {"windows": 1, "mac": 1}
    assert "@" not in json.dumps(got)


def test_the_studio_reports_only_when_connected_and_only_when_something_changed(monkeypatch):
    monkeypatch.setattr(cr_setup_report, "_last", {"sent": None, "at": 0.0, "wait_until": 0.0})
    sent = []
    readiness = {"ready": False, "checks": [{"key": "ffmpeg", "ready": False, "message": "Install FFmpeg in C:\\x"},
                                            {"key": "python", "ready": True}]}
    send = lambda payload: sent.append(payload)
    assert not cr_setup_report.maybe_send(readiness, "5.3-admin.106", connected=False, send=send, background=False)
    assert cr_setup_report.maybe_send(readiness, "5.3-admin.106", connected=True, send=send, now=1000, background=False)
    assert not cr_setup_report.maybe_send(readiness, "5.3-admin.106", connected=True, send=send, now=1100,
                                          background=False), "the same snapshot isn't sent again soon"
    assert cr_setup_report.maybe_send(readiness, "5.3-admin.106", connected=True, send=send,
                                      now=1000 + cr_setup_report.SEND_EVERY_S + 1, background=False)
    assert sent[0]["failing"] == ["ffmpeg"] and "message" not in json.dumps(sent[0]) and "C:\\" not in json.dumps(sent[0])
    assert set(sent[0]) == {"ready", "failing", "os", "version"}


def test_a_failed_report_is_tried_again_and_never_troubles_setup(monkeypatch):
    monkeypatch.setattr(cr_setup_report, "_last", {"sent": None, "at": 0.0, "wait_until": 0.0})
    readiness = {"ready": True, "checks": []}
    def broken(payload):
        raise RuntimeError("404 from an older server")
    assert cr_setup_report.maybe_send(readiness, "v", connected=True, send=broken, now=10, background=False)
    assert not cr_setup_report.maybe_send(readiness, "v", connected=True, send=lambda p: None, now=11,
                                          background=False), "after a failure it waits, never troubling setup"


@pytest.mark.parametrize("system,family", [("Darwin", "mac"), ("Windows", "windows"), ("Linux", "linux"),
                                           ("FreeBSD", "other")])
def test_computer_family(system, family):
    assert cr_setup_report.os_family(system) == family


def test_a_studio_reports_its_setup_over_its_own_route(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient
    import webapp.server as server
    from webapp import frontier_studio as studio
    owner = {"id": 42, "email": "owner@example.com", "plan": "studio_max"}
    monkeypatch.setattr(studio.db, "get_user_by_mcp_api_key", lambda key: owner if key == "cr_mcp_fixture" else None)
    monkeypatch.setattr(studio.milestones, "seen", lambda user_id: None)
    client = TestClient(server.app)
    assert client.post("/api/studio/setup-report", json={"ready": True}).status_code in (401, 403)
    res = client.post("/api/studio/setup-report", headers={"Authorization": "Bearer cr_mcp_fixture"},
                      json={"ready": False, "failing": ["node"], "os": "windows", "version": "5.3-admin.106"})
    assert res.status_code == 200 and res.json() == {"saved": True}
    assert studio_setup.stuck([42])["by_check"] == {"node": 1}


def test_the_privacy_page_says_what_a_setup_report_holds():
    page = (Path(__file__).parents[1] / "webapp" / "static" / "privacy.html").read_text(encoding="utf-8")
    said = page.split("Studio setup reports")[1].split("</p>")[0]
    assert "which setup checks" in said and "never" in said and "short code for why" in said


def test_a_failed_send_waits_instead_of_trying_on_every_status_check(monkeypatch):
    monkeypatch.setattr(cr_setup_report, "_last", {"sent": None, "at": 0.0, "wait_until": 0.0})
    clock = {"t": 1000.0}
    monkeypatch.setattr(cr_setup_report.time, "time", lambda: clock["t"])
    readiness = {"ready": False, "checks": [{"key": "node", "ready": False}]}
    tries = []
    def refused(payload):
        tries.append(payload)
        raise RuntimeError("Your plan doesn't include Studio.")
    assert cr_setup_report.maybe_send(readiness, "v", connected=True, send=refused, now=clock["t"], background=False)
    for step in (4, 8, 12, 600):                          # the setup page asks every 4 s
        assert not cr_setup_report.maybe_send(readiness, "v", connected=True, send=refused, now=clock["t"] + step,
                                              background=False)
    clock["t"] += cr_setup_report.RETRY_AFTER_S + 1
    assert cr_setup_report.maybe_send(readiness, "v", connected=True, send=refused, now=clock["t"], background=False)
    assert len(tries) == 2


def test_an_older_server_without_the_report_is_asked_again_only_after_hours(monkeypatch):
    monkeypatch.setattr(cr_setup_report, "_last", {"sent": None, "at": 0.0, "wait_until": 0.0})
    monkeypatch.setattr(cr_setup_report.time, "time", lambda: 1000.0)
    def old_server(payload):
        raise RuntimeError("Not Found")
    readiness = {"ready": True, "checks": []}
    assert cr_setup_report.maybe_send(readiness, "v", connected=True, send=old_server, now=1000, background=False)
    assert not cr_setup_report.maybe_send(readiness, "v", connected=True, send=old_server,
                                          now=1000 + cr_setup_report.RETRY_AFTER_S + 1, background=False)
    assert cr_setup_report.maybe_send(readiness, "v", connected=True, send=old_server,
                                      now=1000 + cr_setup_report.SEND_EVERY_S + 1, background=False)


def test_reports_older_than_ninety_days_are_dropped(fresh_db, monkeypatch):
    import time as _time
    studio_setup.record(5, report(ready=False, failing=["node"]), now=_time.time() - 100 * 86400)
    studio_setup.record(6, report(ready=False, failing=["claude"]), now=_time.time())
    monkeypatch.setattr(studio_setup, "_ready", set())
    studio_setup.initialize()
    assert studio_setup.stuck([5, 6])["by_check"] == {"claude": 1}


def test_a_failing_checks_reason_code_is_reported_never_its_message(monkeypatch):
    """2026-10-27: the only stuck Studio failed the graphics browser check and the server couldn't see why. A failing
    check's short code (cr_readiness row "code") goes with the report; its message never does."""
    monkeypatch.setattr(cr_setup_report, "_last", {"sent": None, "at": 0.0, "wait_until": 0.0})
    sent = []
    readiness = {"ready": False, "checks": [
        {"key": "browser", "ready": False, "message": "This computer's disk is full ... /Users/jane", "code": "disk_full"},
        {"key": "ffmpeg", "ready": False, "message": "Install FFmpeg", "code": "Not A Code /x"},
        {"key": "python", "ready": True, "code": "ignored"}]}
    assert cr_setup_report.maybe_send(readiness, "5.3-admin.111", connected=True, send=sent.append, now=1,
                                      background=False)
    assert sent[0]["failing"] == ["browser", "ffmpeg"] and sent[0]["why"] == {"browser": "disk_full"}
    assert "jane" not in json.dumps(sent[0]) and "message" not in json.dumps(sent[0])


def test_the_server_keeps_reason_codes_only_for_failing_checks_and_counts_them(fresh_db):
    got = report(ready=False, failing=["browser", "node"], os="mac", version="5.3-admin.111",
                 why={"browser": "disk_full", "node": "C:\\Users\\jane", "claude": "missing", "ffmpeg": 3})
    assert got.why == {"browser": "disk_full"}
    studio_setup.record(1, got)
    studio_setup.record(2, report(ready=False, failing=["browser"], os="windows", why={"browser": "disk_full"}))
    studio_setup.record(3, report(ready=False, failing=["browser"], os="mac"))
    studio_setup.record(4, report(ready=True, os="mac", why={"browser": "timeout"}))
    stuck = studio_setup.stuck([1, 2, 3, 4])
    assert stuck["by_check"] == {"browser": 3, "node": 1} and stuck["by_reason"] == {"browser:disk_full": 2}


def test_reports_saved_before_reason_codes_still_count(fresh_db):
    """A server that saved reports before the codes (no why_json column) gains the column at its next start."""
    with db._conn() as conn:
        conn.execute("""CREATE TABLE studio_setup_reports (
            user_id INTEGER PRIMARY KEY, ready INTEGER NOT NULL DEFAULT 0, failing_json TEXT NOT NULL DEFAULT '[]',
            os TEXT NOT NULL DEFAULT 'other', version TEXT NOT NULL DEFAULT '',
            first_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
            first_ready_at DOUBLE PRECISION)""")
        conn.execute("INSERT INTO studio_setup_reports (user_id, ready, failing_json, os, version, first_at, updated_at)"
                     " VALUES (9, 0, '[\"browser\"]', 'mac', '5.3-admin.109', ?, ?)", (__import__("time").time(),) * 2)
    stuck = studio_setup.stuck([9])
    assert stuck["by_check"] == {"browser": 1} and stuck["by_reason"] == {}
    studio_setup.record(9, report(ready=False, failing=["browser"], why={"browser": "timeout"}))
    assert studio_setup.stuck([9])["by_reason"] == {"browser:timeout": 1}
