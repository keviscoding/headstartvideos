"""Gemini style watching: plan-gated, cached for everyone, capped per account and per day, every call costed."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp import frontier_studio as studio
from webapp import style_watch as sw

LOG = {"narration": "voiceover", "shots": [{"t": "0:00", "len_s": 2.0, "source": "stick_figure", "art": "painted",
       "motion": "zoom_in", "text": "none", "who": "", "audio": "narration"}], "recognisable": ["stick men"]}


class Pool:
    """Runs submitted jobs only when the test says so."""
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append((fn, args))

    def drain(self):
        while self.jobs:
            fn, args = self.jobs.pop(0)
            fn(*args)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sw.db, "IS_PG", False)
    monkeypatch.setattr(sw.db, "DB_PATH", tmp_path / "watch.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setenv("GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("GEMINI_STYLE_MODEL", "gemini-test-flash")
    sw._ready.clear()
    users = {"creator": {"id": 7, "email": "c@example.com", "plan": "daily"},
             "max": {"id": 8, "email": "m@example.com", "plan": "studio_max"},
             "starter": {"id": 9, "email": "s@example.com", "plan": "starter"}}
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "body": json})
        return SimpleNamespace(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(LOG)}]}}],
            "usageMetadata": {"promptTokenCount": 30000, "candidatesTokenCount": 2000, "thoughtsTokenCount": 500}})
    monkeypatch.setattr(sw.requests, "post", fake_post)
    pool = Pool()
    monkeypatch.setattr(sw, "_pool", pool)
    app = FastAPI()
    app.include_router(sw.make_router(studio.studio_access(lambda req: users.get(req.headers.get("x-test-user")))))
    return SimpleNamespace(client=TestClient(app), calls=calls, pool=pool, users=users)


def watch(env, who="creator", vid="dN_RMA8LVi0", start=0, end=180):
    return env.client.post("/api/studio/style/watch", json={"video_id": vid, "start": start, "end": end},
                           headers={"x-test-user": who})


def test_a_watch_runs_as_a_job_and_returns_the_log_with_its_cost(env):
    started = watch(env).json()
    assert started["status"] == "running"
    env.pool.drain()
    done = env.client.get(f"/api/studio/style/watch/{started['job']}", headers={"x-test-user": "creator"}).json()
    assert done["status"] == "done" and done["log"]["recognisable"] == ["stick men"]
    sent = env.calls[0]
    assert sent["url"].endswith("/models/gemini-test-flash:generateContent")
    assert sent["headers"] == {"x-goog-api-key": "test-google-key"}, "the key travels in a header, never the URL"
    video = sent["body"]["contents"][0]["parts"][0]
    assert video["fileData"]["fileUri"] == "https://www.youtube.com/watch?v=dN_RMA8LVi0"
    assert video["videoMetadata"]["startOffset"] == "0s" and video["videoMetadata"]["endOffset"] == "180s"
    with sw.db._conn() as conn:
        row = dict(conn.execute("SELECT * FROM style_watch_jobs").fetchone())
    assert row["tokens_in"] == 30000 and row["tokens_out"] == 2500 and row["cost_usd"] > 0


def test_a_window_is_watched_once_for_everyone(env):
    first = watch(env).json()
    env.pool.drain()
    again = watch(env, who="max").json()
    assert again["status"] == "done" and again["cached"] is True and again["log"] == LOG
    assert len(env.calls) == 1
    with sw.db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT cost_usd, cached FROM style_watch_jobs ORDER BY created_at")]
    assert rows[1] == {"cost_usd": 0, "cached": 1}, "a cached answer costs nothing and isn't counted"
    assert first["job"] != again["job"]


def test_two_studies_asking_at_once_share_one_running_watch(env):
    a = watch(env).json()
    b = watch(env, who="max").json()
    assert a["job"] == b["job"] and b["status"] == "running"
    env.pool.drain()
    polled = env.client.get(f"/api/studio/style/watch/{a['job']}", headers={"x-test-user": "max"}).json()
    assert polled["status"] == "done" and len(env.calls) == 1


def test_plans_without_studio_cannot_watch(env):
    assert watch(env, who="starter").status_code == 403
    assert watch(env, who="nobody").status_code == 401


def test_each_account_has_a_daily_number_of_watches(env, monkeypatch):
    monkeypatch.setitem(sw.DAILY_WATCHES, "daily", 2)
    for start in (0, 200):
        assert watch(env, start=start, end=start + 120).status_code == 200
    refused = watch(env, start=400, end=520)
    assert refused.status_code == 402 and "today" in refused.json()["detail"]
    assert watch(env, who="max", start=400, end=520).status_code == 200, "another account is unaffected"


def test_the_global_daily_budget_stops_new_watches(env, monkeypatch):
    monkeypatch.setenv("STYLE_WATCH_DAILY_USD", "0.01")
    watch(env)
    env.pool.drain()
    over = watch(env, who="max", start=300, end=420)
    assert over.status_code == 503 and "own measurements" in over.json()["detail"]


@pytest.mark.parametrize("body", [{"video_id": "not a video", "start": 0, "end": 60},
                                  {"video_id": "dN_RMA8LVi0", "start": 0, "end": 900},
                                  {"video_id": "dN_RMA8LVi0", "start": 50, "end": 55}])
def test_only_youtube_ids_and_bounded_windows(env, body):
    res = env.client.post("/api/studio/style/watch", json=body, headers={"x-test-user": "creator"})
    assert res.status_code == 400 and not env.calls


def test_without_our_key_the_study_is_told_to_continue_on_its_own(env, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    res = watch(env)
    assert res.status_code == 503 and "own measurements" in res.json()["detail"]


def test_a_failed_watch_is_reported_and_costs_nothing(env, monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("Gemini HTTP 400: video unavailable")
    monkeypatch.setattr(sw, "watch_once", refuse)
    job = watch(env).json()["job"]
    env.pool.drain()
    res = env.client.get(f"/api/studio/style/watch/{job}", headers={"x-test-user": "creator"}).json()
    assert res["status"] == "failed" and "unavailable" in res["error"]
    assert watch(env).json()["status"] == "running", "a failed window can be watched again"


def test_a_stuck_job_is_released(env, monkeypatch):
    job = watch(env).json()["job"]
    with sw.db._conn() as conn:
        conn.execute("UPDATE style_watch_jobs SET updated_at=updated_at-?", (sw.STALE_S + 5,))
    res = env.client.get(f"/api/studio/style/watch/{job}", headers={"x-test-user": "creator"}).json()
    assert res["status"] == "failed"


def test_gemini_answers_wrapped_in_fences_still_parse():
    assert sw._parse("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert sw._parse("Here you go: {\"a\": 2} thanks") == {"a": 2}
    with pytest.raises(ValueError):
        sw._parse("no json here")


def test_the_prompt_names_the_window_and_the_fixed_vocabularies():
    text = sw.prompt(65, 185)
    assert "between 1:05 and 3:05" in text
    assert "stick_figure" in text and "cutout_move" in text and "[SOURCES]" not in text
    json.dumps(text)


def test_a_busy_gemini_is_asked_again_before_the_watch_fails(env, monkeypatch):
    answers = [503, 503, 200]
    waits = []
    monkeypatch.setattr(sw, "_sleep", waits.append)

    def flaky(url, headers=None, json=None, timeout=None):
        code = answers.pop(0)
        body = {"candidates": [{"content": {"parts": [{"text": __import__("json").dumps(LOG)}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}} if code == 200 else \
            {"error": {"message": "This model is currently experiencing high demand."}}
        return SimpleNamespace(status_code=code, json=lambda: body)
    log, tin, tout = sw.watch_once("dN_RMA8LVi0", 0, 60, post=flaky)
    assert log == LOG and waits == [15, 45]


def test_a_refusal_is_not_retried(env, monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "_sleep", lambda s: calls.append(s))

    def refuse(url, headers=None, json=None, timeout=None):
        return SimpleNamespace(status_code=400, json=lambda: {"error": {"message": "Video unavailable"}})
    with pytest.raises(RuntimeError, match="400"):
        sw.watch_once("dN_RMA8LVi0", 0, 60, post=refuse)
    assert calls == []


class Upload:
    """Gemini's file service and model, as the uploaded-video watch uses them."""
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None, data=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json, "data": data})
        if url == sw.UPLOAD_BASE:
            return SimpleNamespace(status_code=200, headers={"X-Goog-Upload-URL": "https://upload.example/session"},
                                   json=lambda: {})
        if url == "https://upload.example/session":
            return SimpleNamespace(status_code=200, headers={}, json=lambda: {
                "file": {"name": "files/abc", "uri": "https://files.example/abc", "state": "PROCESSING"}})
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(LOG)}]}}],
            "usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 800}})

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return SimpleNamespace(status_code=200, json=lambda: {"name": "files/abc", "uri": "https://files.example/abc",
                                                              "state": "ACTIVE"})


def test_an_admin_has_a_test_video_watched_with_the_reference_instructions(env, monkeypatch):
    import base64
    up = Upload()
    monkeypatch.setattr(sw.requests, "post", up.post)
    monkeypatch.setattr(sw.requests, "get", up.get)
    monkeypatch.setattr(sw, "_sleep", lambda s: None)
    env.users["owner"] = {"id": 1, "email": "owner@example.com", "plan": "studio_max"}
    body = {"data_b64": base64.b64encode(b"a light mp4 copy").decode(), "mime": "video/mp4", "start": 0, "end": 75}
    started = env.client.post("/api/studio/style/watch-upload", json=body, headers={"x-test-user": "owner"}).json()
    assert started["status"] == "running" and started["video_id"].startswith("file:")
    env.pool.drain()
    done = env.client.get(f"/api/studio/style/watch/{started['job']}", headers={"x-test-user": "owner"}).json()
    assert done["status"] == "done" and done["log"]["recognisable"] == ["stick men"]
    assert up.calls[0]["headers"]["X-Goog-Upload-Header-Content-Length"] == str(len(b"a light mp4 copy"))
    assert up.calls[1]["data"] == b"a light mp4 copy" and up.calls[2]["url"].endswith("/files/abc")
    watched = up.calls[3]["json"]["contents"][0]["parts"]
    assert watched[0]["fileData"] == {"fileUri": "https://files.example/abc", "mimeType": "video/mp4"}
    assert watched[1]["text"] == sw.prompt(0, 75), "the very instructions the references were watched with"
    again = env.client.post("/api/studio/style/watch-upload", json=body, headers={"x-test-user": "owner"}).json()
    assert again["job"] == started["job"] and len(up.calls) == 4, "the same copy and window is watched once"


def test_uploaded_videos_are_for_admins_only_and_bounded(env):
    import base64
    body = {"data_b64": base64.b64encode(b"x").decode(), "start": 0, "end": 60}
    assert env.client.post("/api/studio/style/watch-upload", json=body, headers={"x-test-user": "max"}).status_code == 403
    env.users["owner"] = {"id": 1, "email": "owner@example.com", "plan": "studio_max"}
    bad = env.client.post("/api/studio/style/watch-upload", json={**body, "mime": "text/html"},
                          headers={"x-test-user": "owner"})
    assert bad.status_code == 400
    long = env.client.post("/api/studio/style/watch-upload", json={**body, "end": 900}, headers={"x-test-user": "owner"})
    assert long.status_code == 400


def test_an_admin_can_ask_their_own_research_question(env, monkeypatch):
    import base64
    up = Upload()
    monkeypatch.setattr(sw.requests, "post", up.post)
    monkeypatch.setattr(sw.requests, "get", up.get)
    monkeypatch.setattr(sw, "_sleep", lambda s: None)
    env.users["owner"] = {"id": 1, "email": "owner@example.com", "plan": "studio_max"}
    body = {"data_b64": base64.b64encode(b"a ranking short").decode(), "start": 0, "end": 30,
            "question": "Log every clip of this ranking Short as JSON."}
    started = env.client.post("/api/studio/style/watch-upload", json=body, headers={"x-test-user": "owner"}).json()
    env.pool.drain()
    done = env.client.get(f"/api/studio/style/watch/{started['job']}", headers={"x-test-user": "owner"}).json()
    assert done["status"] == "done"
    asked = up.calls[3]["json"]
    assert asked["contents"][0]["parts"][1]["text"] == body["question"]
    assert asked["contents"][0]["parts"][0]["videoMetadata"]["fps"] == 4 and "mediaResolution" not in asked["generationConfig"]
    plain = env.client.post("/api/studio/style/watch-upload", json={**body, "question": ""},
                            headers={"x-test-user": "owner"}).json()
    assert plain["job"] != started["job"], "a different question is a different watch, not the cached one"
    customer = env.client.post("/api/studio/style/watch-upload", json=body, headers={"x-test-user": "max"})
    assert customer.status_code == 403
