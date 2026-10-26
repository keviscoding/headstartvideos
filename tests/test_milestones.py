"""How far a customer has got with Studio: downloaded, connected, seen, first finished video."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp import database as db
from webapp import milestones
from webapp import studio_connect as sc


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(milestones, "_ready", set())
    monkeypatch.setattr(milestones, "_seen", {})
    return tmp_path


def test_a_milestone_keeps_its_first_time_and_counts_repeats(fresh_db):
    milestones.record(7, milestones.DOWNLOADED, now=100.0)
    milestones.record(7, milestones.DOWNLOADED, now=200.0)
    milestones.record(7, milestones.CONNECTED, now=150.0)
    got = milestones.of(7)
    assert got["downloaded_at"] == 100.0 and got["connected_at"] == 150.0 and got["seen_at"] is None
    assert milestones.of(8)["downloaded_at"] is None, "each customer has their own"
    with db._conn() as conn:
        row = conn.execute("SELECT count, last_at FROM user_milestones WHERE user_id=7 AND name=?",
                           (milestones.DOWNLOADED,)).fetchone()
    assert row["count"] == 2 and row["last_at"] == 200.0


def test_a_studio_calling_in_is_recorded_at_most_every_ten_minutes(fresh_db, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(milestones.time, "time", lambda: clock["t"])
    milestones.seen(7)
    clock["t"] += 60
    milestones.seen(7)
    with db._conn() as conn:
        assert conn.execute("SELECT count FROM user_milestones WHERE name=?", (milestones.SEEN,)).fetchone()["count"] == 1
    clock["t"] += milestones.SEEN_EVERY_S
    milestones.seen(7)
    assert milestones.of(7)["seen_at"] == clock["t"]


def test_finished_projects_count_from_the_studio_projects(fresh_db):
    assert milestones.of(7)["projects"] == 0, "no projects table yet is fine"
    with db._conn() as conn:
        conn.execute("""CREATE TABLE frontier_projects (id TEXT PRIMARY KEY, user_id INTEGER, title TEXT, style TEXT,
            version TEXT, max_calls INTEGER, status TEXT, created_at REAL, updated_at REAL)""")
        for i, (status, t) in enumerate((("done", 50.0), ("ready", 60.0), ("done", 40.0))):
            conn.execute("INSERT INTO frontier_projects VALUES (?,?,?,?,?,?,?,?,?)", (f"p{i}", 7, "t", "s", "v", 1, status, t, t))
    got = milestones.of(7)
    assert got["projects"] == 3 and got["finished"] == 2 and got["first_finished_at"] == 40.0


def test_a_milestone_never_breaks_the_request(monkeypatch, capsys):
    monkeypatch.setattr(milestones, "_ready", set())
    monkeypatch.setattr(milestones.db, "_conn", lambda: (_ for _ in ()).throw(RuntimeError("database away")))
    milestones.record(7, milestones.DOWNLOADED)
    assert milestones.of(7)["projects"] == 0
    assert "not recorded" in capsys.readouterr().out


def test_connecting_a_studio_is_recorded(fresh_db, monkeypatch):
    monkeypatch.setenv("FRONTIER_SECRETS_KEY", "connect-test-secret")
    user = {"id": 7, "email": "creator@example.com", "plan": "studio_pro"}
    monkeypatch.setattr(sc.db, "get_user_by_id", lambda uid: user)
    monkeypatch.setattr(sc.db, "ensure_user_mcp_api_key", lambda uid: "cr_mcp_key")
    app = FastAPI(); app.include_router(sc.make_router(lambda request: user))
    client = TestClient(app)
    verifier = "v" * 64
    redirect = client.post("/api/studio/connect/approve", json={"challenge": sc.challenge_for(verifier), "state": "s" * 24}).json()["redirect"]
    code = parse_qs(urlsplit(redirect).query)["code"][0]
    assert client.post("/api/studio/connect/exchange", json={"code": code, "verifier": verifier}).status_code == 200
    assert milestones.of(7)["connected_at"] is not None


def _studio_tables(conn):
    conn.execute("""CREATE TABLE frontier_projects (id TEXT PRIMARY KEY, user_id INTEGER, title TEXT, style TEXT,
        version TEXT, max_calls INTEGER, status TEXT, created_at REAL, updated_at REAL)""")
    conn.execute("""CREATE TABLE frontier_native_sessions (project_id TEXT PRIMARY KEY, user_id INTEGER, begin_id TEXT,
        begin_fingerprint TEXT, workflow_json TEXT, setup_json TEXT, spec_json TEXT)""")


def _project(conn, pid, user, status, t, purpose):
    conn.execute("INSERT INTO frontier_projects VALUES (?,?,?,?,?,?,?,?,?)", (pid, user, "t", "s", "v", 1, status, t, t))
    conn.execute("INSERT INTO frontier_native_sessions VALUES (?,?,?,?,?,?,?)",
                 (pid, user, pid, "f", "{}", json.dumps({"purpose": purpose}), "{}"))


def test_only_videos_count_as_finished_videos(fresh_db):
    """The setup model check finishes before any video: counted, it ticked "first video" and kept the first-video
    email from ever going out (assessment, 2026-10-26)."""
    with db._conn() as conn:
        _studio_tables(conn)
        _project(conn, "setup", 7, "done", 10.0, "setup")
        _project(conn, "study", 7, "done", 20.0, "creative")
    got = milestones.of(7)
    assert got["projects"] == 2 and got["videos"] == 0 and got["finished"] == 0 and got["first_finished_at"] is None
    with db._conn() as conn:
        _project(conn, "video", 7, "done", 30.0, "video")
    got = milestones.of(7)
    assert got["finished"] == 1 and got["first_finished_at"] == 30.0, "so on_video_finished sends the email now"


def test_activation_counts_how_far_new_customers_got(fresh_db):
    from webapp import lifecycle_email
    now = 100 * 86400.0
    for uid in (1, 2, 3, 4):
        milestones.record(uid, lifecycle_email.SUBSCRIBED, now=now - 5 * 86400)
    milestones.record(9, lifecycle_email.SUBSCRIBED, now=now - 90 * 86400)        # before the window
    milestones.record(1, milestones.DOWNLOADED, now=now - 4 * 86400)
    milestones.record(2, milestones.CONNECTED, now=now - 4 * 86400)
    with db._conn() as conn:
        _studio_tables(conn)
        _project(conn, "s2", 2, "done", now - 4 * 86400, "setup")
        _project(conn, "v2", 2, "done", now - 4 * 86400 + 3600, "video")
        _project(conn, "s3", 3, "done", now - 3 * 86400, "setup")
        _project(conn, "v3", 3, "failed", now - 3 * 86400, "video")
    got = milestones.activation(30, now=now)
    assert {k: got[k] for k in ("subscribed", "downloaded", "connected", "setup_ready", "first_video_started",
                                "first_video_finished", "only_failed_videos")} == {
        "subscribed": 4, "downloaded": 3, "connected": 2, "setup_ready": 2, "first_video_started": 2,
        "first_video_finished": 1, "only_failed_videos": 1}
    assert got["hours_to_first_video"]["median"] == 25.0
    assert "@" not in json.dumps(got)


def test_only_admins_read_activation(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient
    import webapp.server as server
    monkeypatch.setattr(server.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setattr(server, "require_user", lambda request: {"id": 2, "email": "customer@example.com"})
    assert TestClient(server.app).get("/api/admin/activation").status_code == 403
    monkeypatch.setattr(server, "require_user", lambda request: {"id": 1, "email": "owner@example.com"})
    res = TestClient(server.app).get("/api/admin/activation?days=30")
    assert res.status_code == 200 and res.headers["cache-control"] == "private, no-store"
    assert res.json()["days"] == 30 and res.json()["subscribed"] == 0
