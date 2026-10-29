"""The niche agent's read-only server endpoints: the owner's teaching and the niche database's young breakouts."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp import database as db
from webapp import niche_agent as na
from tests.test_money_niche import channel

NOW = time.time()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agent.db")
    db._init_db()
    na._rate.clear()
    db.upsert_niche_channels([
        channel("city1", "CityEons", "hadn't", "Watch ROME Grow for 3,000 Years - 3D Timelapse", subs=6900,
                views=640_000, days=20),
        channel("city2", "Latos Charts", "somehow", "PRAGUE - 3D Timelapse", subs=40_000, views=500_000, days=50),
        channel("cityold", "Old Cities", "related video", "LONDON - 3D Timelapse", subs=9000, views=300_000, days=400),
        channel("citybig", "Huge Timelapse", "x", "PARIS - 3D Timelapse", subs=900_000, views=2_000_000, days=30),
        channel("citylow", "Quiet Cities", "x", "OSLO - 3D Timelapse", subs=500, views=2_000, days=30),
        channel("stick1", "Stick Lab", "3d timelapse", "What if you lived in Rome | stick figure", subs=5000,
                views=90_000, days=25),
    ])
    with db._conn() as conn:                      # Latos was scored 50 days old, 45 days ago: 95 days old today
        conn.execute("UPDATE niche_channels SET last_scored_at=? WHERE channel_id='city2'", (NOW - 45 * 86400,))
    app = FastAPI()
    app.include_router(na.make_router(lambda: {"id": 7}))
    return TestClient(app)


def test_breakouts_match_words_in_titles_never_the_probe_keyword(env):
    data = env.get("/api/studio/niche-agent/breakouts", params={"q": "3D timelapse"}).json()
    names = [c["channel"] for c in data["channels"]]
    assert names == ["CityEons"]                  # young, small, strong; the stick channel only had the probe word
    c = data["channels"][0]
    assert c["age_days_now"] == 20 and c["subscribers"] == 6900 and c["recent_avg_views"] == 640_000
    assert c["recent_videos"][0]["title"].startswith("Watch ROME") and c["numbers_days_old"] == 0
    assert "check them live" in data["note"]


def test_breakouts_age_is_counted_to_today_and_filters_apply(env):
    older = env.get("/api/studio/niche-agent/breakouts", params={"q": "timelapse", "max_age_days": 120}).json()
    by = {c["channel"]: c for c in older["channels"]}
    assert set(by) == {"CityEons", "Latos Charts"} and by["Latos Charts"]["age_days_now"] == 95
    big = env.get("/api/studio/niche-agent/breakouts",
                  params={"q": "timelapse", "max_subs": 2_000_000, "sort": "newest", "limit": 1}).json()
    assert big["count"] == 1
    none = env.get("/api/studio/niche-agent/breakouts", params={"q": "timelapse zzzz"}).json()
    assert none["channels"] == []


def test_breakouts_are_rate_limited_per_user(env, monkeypatch):
    monkeypatch.setattr(na, "RATE_MAX", 2)
    assert env.get("/api/studio/niche-agent/breakouts").status_code == 200
    assert env.get("/api/studio/niche-agent/breakouts").status_code == 200
    assert env.get("/api/studio/niche-agent/breakouts").status_code == 429


def test_teaching_serves_the_repository_files(env):
    data = env.get("/api/studio/niche-agent/teaching").json()
    assert data["rules"].startswith("# Niche agent") and "```json" in data["rules"]
    assert len(data["examples"]) >= 20 and data["version"] and data["updated"] >= "2026-10-29"
    assert {"kind", "verdict", "link", "why", "lesson"} <= set(data["examples"][0])


def test_the_site_needs_a_studio_plan_for_both():
    from webapp import server
    client = TestClient(server.app)
    for path in ("/api/studio/niche-agent/teaching", "/api/studio/niche-agent/breakouts"):
        assert client.get(path).status_code in (401, 403)
        assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_the_niche_finder_shows_the_agent_card():
    html = (na.TEACHING.parents[1] / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    card = html.split('id="nf-agent-card"', 1)[1].split("</section>", 1)[0]
    assert "/niche-agent" in card and "Claude Code" in card and "costs nothing" in card


def test_a_slow_database_read_is_stopped_and_says_so(env, monkeypatch):
    class QueryCanceled(Exception):
        pass

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return self

        def execute(self, sql, params=()):
            self.seen = getattr(self, "seen", []) + [sql]
            if sql.startswith("SELECT"):
                raise QueryCanceled("canceling statement due to statement timeout")
    conn = Conn()
    monkeypatch.setattr(db, "IS_PG", True)
    monkeypatch.setattr(db, "_conn", lambda: conn)
    r = env.get("/api/studio/niche-agent/breakouts", params={"q": "timelapse"})
    assert r.status_code == 503 and "took too long" in r.json()["detail"]
    assert conn.seen[0] == f"SET LOCAL statement_timeout = {na.STATEMENT_TIMEOUT_MS}"


def test_like_wildcards_and_escapes_in_words_are_plain_text(env):
    data = env.get("/api/studio/niche-agent/breakouts", params={"q": "3d\\ time%lapse_"}).json()
    assert [c["channel"] for c in data["channels"]] == ["CityEons"]
