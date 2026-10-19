"""Ranking Shorts clip watching: scout and detail looks, shared for everyone, a monthly allowance, credits past it."""
import base64
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp import frontier_studio as studio
from webapp import plans
from webapp import studio_ranking as sr
from webapp import style_watch as sw

LOG = {"on_topic": True, "moments": [{"start": 1.0, "payoff": 2.4, "end": 4.0, "score": 8}]}
Q = "Log every moment that could be one ranked clip."


class Pool:
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
    monkeypatch.setattr(sr.db, "IS_PG", False)
    monkeypatch.setattr(sr.db, "DB_PATH", tmp_path / "ranking.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setenv("GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("GEMINI_STYLE_MODEL", "gemini-test-flash")
    sr._ready.clear()
    users = {"creator": {"id": 7, "email": "c@example.com", "plan": "daily"},
             "trial": {"id": 8, "email": "t@example.com", "plan": "studio_pro_trial"},
             "starter": {"id": 9, "email": "s@example.com", "plan": "starter"},
             "owner": {"id": 1, "email": "owner@example.com", "plan": "free"}}
    wallet = {7: 1.0, 8: 0.0}
    monkeypatch.setattr(sr, "credits_of", lambda uid: wallet.get(uid, 0.0))

    def deduct(uid, amount):
        if wallet.get(uid, 0) < amount:
            return False
        wallet[uid] -= amount
        return True
    monkeypatch.setattr(sr, "deduct", deduct)
    monkeypatch.setattr(sr, "refund", lambda uid, amount: wallet.__setitem__(uid, wallet.get(uid, 0) + amount))
    calls, answer = [], {"status": 200}

    def fake_post(url, headers=None, json=None, timeout=None, data=None):
        calls.append({"url": url, "body": json})
        if answer["status"] != 200:
            return SimpleNamespace(status_code=answer["status"], json=lambda: {"error": {"message": "nope"}})
        return SimpleNamespace(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(LOG)}]}}],
            "usageMetadata": {"promptTokenCount": 6000, "candidatesTokenCount": 1500, "thoughtsTokenCount": 300}})
    monkeypatch.setattr(sw.requests, "post", fake_post)
    monkeypatch.setattr(sw, "_sleep", lambda s: None)
    pool = Pool()
    monkeypatch.setattr(sr, "_pool", pool)
    app = FastAPI()
    app.include_router(sr.make_router(studio.studio_access(lambda req: users.get(req.headers.get("x-test-user")))))
    return SimpleNamespace(client=TestClient(app), calls=calls, pool=pool, wallet=wallet, answer=answer)


def watch(env, who="creator", vid="dN_RMA8LVi0", end=60, mode="scout", question=Q, **extra):
    return env.client.post("/api/studio/ranking/watch", headers={"x-test-user": who},
                           json={"video_id": vid, "start": 0, "end": end, "mode": mode, "question": question, **extra})


def poll(env, job, who="creator"):
    return env.client.get(f"/api/studio/ranking/watch/{job}", headers={"x-test-user": who}).json()


def test_a_scout_looks_at_the_whole_clip_cheaply_and_detail_looks_closely(env):
    scout = watch(env).json()
    env.pool.drain()
    assert poll(env, scout["job"])["log"] == LOG
    body = env.calls[0]["body"]
    video, text = body["contents"][0]["parts"]
    assert video["fileData"]["fileUri"] == "https://www.youtube.com/watch?v=dN_RMA8LVi0"
    assert video["videoMetadata"]["fps"] == 1 and body["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert text["text"] == Q, "the Studio's own instructions are what Gemini reads"
    watch(env, mode="detail", end=20)
    env.pool.drain()
    detail = env.calls[1]["body"]
    assert detail["contents"][0]["parts"][0]["videoMetadata"]["fps"] == 4
    assert "mediaResolution" not in detail["generationConfig"]


def test_limits_on_what_one_watch_may_be(env):
    assert watch(env, end=241).status_code == 400, "a scout is at most four minutes"
    assert watch(env, mode="detail", end=41).status_code == 400, "a detail look is at most 40 seconds"
    assert watch(env, mode="everything").status_code == 400
    assert watch(env, question=" ").status_code == 400
    assert watch(env, vid="not a video").status_code == 400
    assert watch(env, who="starter").status_code in (401, 402, 403), "only Studio plans watch"


def test_a_watch_of_a_public_clip_is_shared_and_free_the_second_time(env):
    first = watch(env).json()
    env.pool.drain()
    again = watch(env, who="trial").json()
    assert again["status"] == "done" and again["cached"] and again["log"] == LOG
    assert len(env.calls) == 1, "Gemini watched it once"
    other_question = watch(env, who="trial", question=Q + " Also the sound.").json()
    assert other_question["status"] == "running", "different instructions are a different watch"
    allowance = env.client.get("/api/studio/ranking/allowance", headers={"x-test-user": "trial"}).json()
    assert allowance["used"] == 1, "the shared watch did not count against the second account"


def test_the_monthly_allowance_then_credits_only_when_the_studio_asks(env, monkeypatch):
    monkeypatch.setitem(plans.TIERS, "daily", plans.TIERS["daily"].__class__(
        **{**plans.TIERS["daily"].__dict__, "ranking_watches": 2}))
    for vid in ("aaaaaaaaaa1", "aaaaaaaaaa2"):
        assert watch(env, vid=vid).json()["credits"] == 0
    env.pool.drain()
    over = watch(env, vid="aaaaaaaaaa3")
    assert over.status_code == 402 and "credits" in over.json()["detail"]
    paid = watch(env, vid="aaaaaaaaaa3", use_credits=True).json()
    assert paid["credits"] == sr.WATCH_CREDITS and env.wallet[7] == pytest.approx(1.0 - sr.WATCH_CREDITS)
    env.pool.drain()
    allowance = env.client.get("/api/studio/ranking/allowance", headers={"x-test-user": "creator"}).json()
    assert allowance["used"] == 2 and allowance["remaining"] == 0 and allowance["limit"] == 2
    assert allowance["credits"] == pytest.approx(1.0 - sr.WATCH_CREDITS)


def test_no_credits_no_watch_and_nothing_is_taken(env):
    sr.initialize()
    with sr.db._conn() as conn:                         # the trial's allowance, already used this month
        now = time.time()
        for i in range(plans.TRIAL_RANKING_WATCHES):
            conn.execute(sr.db._q("""INSERT INTO ranking_watch_jobs (id,user_id,source,start_s,end_s,mode,question_sha,
                model,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'done',?,?)"""),
                (f"rw_old{i}", 8, f"v{i:010d}", 0, 60, "scout", "x", "gemini-test-flash", now, now))
    broke = watch(env, who="trial", use_credits=True)
    assert broke.status_code == 402 and env.wallet[8] == 0
    assert not env.pool.jobs


def test_a_failed_watch_is_not_counted_and_its_credits_come_back(env, monkeypatch):
    monkeypatch.setitem(plans.TIERS, "daily", plans.TIERS["daily"].__class__(
        **{**plans.TIERS["daily"].__dict__, "ranking_watches": 0}))
    env.answer["status"] = 400
    job = watch(env, use_credits=True).json()
    assert env.wallet[7] == pytest.approx(1.0 - sr.WATCH_CREDITS)
    env.pool.drain()
    assert poll(env, job["job"])["status"] == "failed"
    assert env.wallet[7] == pytest.approx(1.0), "refunded"


def test_a_creators_own_clip_is_watched_from_an_upload_and_stays_theirs(env, monkeypatch):
    monkeypatch.setattr(sw, "upload_file", lambda data, mime: "https://files.example/abc")
    clip = base64.b64encode(b"\x00\x00\x00 ftypmp42 my clip").decode()
    job = env.client.post("/api/studio/ranking/watch-upload", headers={"x-test-user": "creator"},
                          json={"data_b64": clip, "end": 12, "question": Q}).json()
    env.pool.drain()
    assert poll(env, job["job"])["status"] == "done" and "video_id" not in poll(env, job["job"])
    assert env.calls[0]["body"]["contents"][0]["parts"][0]["fileData"]["fileUri"] == "https://files.example/abc"
    other = env.client.get(f"/api/studio/ranking/watch/{job['job']}", headers={"x-test-user": "trial"})
    assert other.status_code == 404, "another account cannot read someone's own clip"


def test_a_watch_given_up_as_stale_is_refunded_once_and_a_late_answer_cannot_revive_it(env, monkeypatch):
    monkeypatch.setitem(plans.TIERS, "daily", plans.TIERS["daily"].__class__(
        **{**plans.TIERS["daily"].__dict__, "ranking_watches": 0}))
    job = watch(env, use_credits=True).json()["job"]
    with sr.db._conn() as conn:
        conn.execute(sr.db._q("UPDATE ranking_watch_jobs SET updated_at=? WHERE id=?"), (time.time() - sr.STALE_S - 5, job))
    assert poll(env, job)["status"] == "failed"
    assert poll(env, job)["status"] == "failed"
    assert env.wallet[7] == pytest.approx(1.0), "refunded exactly once"
    env.pool.drain()                                    # Gemini answers after all
    assert poll(env, job)["status"] == "failed" and env.wallet[7] == pytest.approx(1.0)


def test_plans_give_each_studio_tier_a_monthly_allowance():
    assert plans.ranking_watch_limit("daily") == 300
    assert plans.ranking_watch_limit("studio_pro") == 900
    assert plans.ranking_watch_limit("studio_max") == 2400
    assert plans.ranking_watch_limit("studio_max_trial") == plans.TRIAL_RANKING_WATCHES
    assert plans.ranking_watch_limit("starter") == 0 and plans.ranking_watch_limit("free") == 0
    assert plans.ranking_watch_limit("free", is_admin=True) is None


def test_tiktok_search_answers_not_set_up_until_the_key_is_there(env, monkeypatch):
    monkeypatch.delenv("SCRAPECREATORS_API_KEY", raising=False)
    r = env.client.post("/api/studio/ranking/tiktok-search", headers={"x-test-user": "creator"}, json={"query": "cat fails"})
    assert r.status_code == 503 and "not set up" in r.json()["detail"]


def test_tiktok_search_lists_videos_once_each_and_has_a_daily_cap(env, monkeypatch):
    import requests
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "sc-test-key")
    monkeypatch.setattr(sr, "TIKTOK_DAILY", 2)
    sr._tiktok_used.clear()
    asked = []
    item = lambda vid, views: {"aweme_info": {"aweme_id": vid, "desc": "cat vs cucumber 😭", "author": {"unique_id": "catclips"},
                                              "statistics": {"play_count": views}, "video": {"duration": 14200},
                                              "share_url": f"https://www.tiktok.com/@catclips/video/{vid}"}}

    def get(url, params=None, headers=None, timeout=None):
        asked.append((url, params, headers))
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "search_item_list": [
            item("7301", 900000), item("7301", 900000), item("7302", 12000), {"aweme_info": {"aweme_id": "bad"}}]})
    monkeypatch.setattr(requests, "get", get)
    r = env.client.post("/api/studio/ranking/tiktok-search", headers={"x-test-user": "creator"}, json={"query": "cat fails"})
    assert r.status_code == 200
    videos = r.json()["videos"]
    assert [v["id"] for v in videos] == ["7301", "7302"]
    assert videos[0] == {"id": "7301", "url": "https://www.tiktok.com/@catclips/video/7301", "title": "cat vs cucumber 😭",
                         "channel": "catclips", "views": 900000, "dur": 14.2}
    assert asked[0][2] == {"x-api-key": "sc-test-key"} and asked[0][1]["query"] == "cat fails"
    env.client.post("/api/studio/ranking/tiktok-search", headers={"x-test-user": "creator"}, json={"query": "dog fails"})
    capped = env.client.post("/api/studio/ranking/tiktok-search", headers={"x-test-user": "creator"}, json={"query": "x y"})
    assert capped.status_code == 429


def test_cloud_clean_is_off_without_its_key(env, monkeypatch):
    monkeypatch.delenv("PIAPI_API_KEY", raising=False)
    import base64
    r = env.client.post("/api/studio/ranking/clean", headers={"x-test-user": "creator"},
                        json={"data_b64": base64.b64encode(b"mp4").decode()})
    assert r.status_code == 503


def test_cloud_clean_stores_privately_signs_a_link_and_deletes_it_when_done(env, monkeypatch):
    import base64, requests
    from webapp import storage
    monkeypatch.setenv("PIAPI_API_KEY", "pi-test-key")
    stored, deleted, sent = {}, [], []

    class Client:
        def put_object(self, **kw):
            stored.update(kw)
    monkeypatch.setattr(storage, "_make_client", lambda: Client())
    monkeypatch.setattr(storage, "_spaces_creds", lambda: ("k", "s", "bucket", "sfo3", "e"))
    monkeypatch.setattr(storage, "presigned_get_url", lambda key, expires=0: f"https://signed/{key}?exp={expires}")
    monkeypatch.setattr(storage, "delete_key", deleted.append)
    states = iter([{"status": "processing"}, {"status": "completed", "output": {"video_url": "https://piapi/out.mp4"}}])

    def post(url, headers=None, json=None, timeout=None):
        sent.append((url, headers, json))
        return SimpleNamespace(status_code=200, json=lambda: {"code": 200, "data": {"task_id": "task-9", "status": "pending"}})
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None: SimpleNamespace(
        status_code=200, json=lambda d=next(states): {"code": 200, "data": d}))
    r = env.client.post("/api/studio/ranking/clean", headers={"x-test-user": "creator"},
                        json={"data_b64": base64.b64encode(b"clip bytes").decode(),
                              "region": {"x": 0, "y": 860, "width": 1080, "height": 200}})
    job = r.json()["job"]
    assert "ACL" not in stored and stored["Bucket"] == "bucket" and stored["Key"].startswith("private/ranking-clean/")
    body = sent[0][2]
    assert sent[0][1] == {"x-api-key": "pi-test-key"} and body["task_type"] == "remove-subtitle"
    assert body["input"]["video"].startswith("https://signed/private/ranking-clean/") and body["input"]["region"]["y"] == 860
    first = env.client.get(f"/api/studio/ranking/clean/{job}", headers={"x-test-user": "creator"}).json()
    assert first["status"] == "running" and not deleted
    done = env.client.get(f"/api/studio/ranking/clean/{job}", headers={"x-test-user": "creator"}).json()
    assert done == {"status": "done", "video_url": "https://piapi/out.mp4", "error": ""}
    assert deleted == [stored["Key"]], "the staged clip is deleted once PiAPI is done with it"
    assert env.client.get(f"/api/studio/ranking/clean/{job}", headers={"x-test-user": "trial"}).status_code == 404
