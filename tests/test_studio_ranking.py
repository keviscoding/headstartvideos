"""Ranking Shorts clip watching: scout and detail looks, shared for everyone, a monthly allowance, credits past it;
jobs that survive a restart, fair turns between accounts, a spend ledger and caps that say which one refused; and the
ScrapeCreators searches and PiAPI cleans, kept in the database."""
import base64
import json
import re
import shutil
import subprocess
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
# what the Studio's common.py sorts these answers by (ranking/common.py SERVICE_DOWN, TRANSIENT, OUT_OF_WATCHES)
SERVICE_DOWN = re.compile(r"isn.t available right now|busy today|reconnect your channelrecipe", re.I)
TRANSIENT = re.compile(r"could not reach channelrecipe|channelrecipe could not answer|timed out|connection", re.I)
OUT_OF_WATCHES = re.compile(r"used this month|clip watches|not enough credits|top up credits", re.I)


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
    for name in ("RANKING_WATCH_DAILY_USD", "RANKING_ACCOUNT_SHARE", "SCRAPECREATORS_API_KEY", "PIAPI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sr, "SPOOL", tmp_path / "spool")
    sr._ready.clear()
    sr._reset_dispatch()
    sr._cap_cache.update(t=0.0, usd=0.0)
    sr._breaker.clear()
    sr._alerted.clear()
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
    calls, answer = [], {"status": 200, "blocked": False, "during": None}

    def fake_post(url, headers=None, json=None, timeout=None, data=None):
        calls.append({"url": url, "body": json})
        if answer["during"]:
            answer["during"]()
        usage = {"promptTokenCount": 6000, "candidatesTokenCount": 1500, "thoughtsTokenCount": 300}
        if answer["status"] != 200:
            return SimpleNamespace(status_code=answer["status"], json=lambda: {"error": {"message": "nope"}})
        if answer["blocked"]:                       # Google billed the video, then answered nothing usable
            return SimpleNamespace(status_code=200, json=lambda: {
                "candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}], "usageMetadata": usage})
        return SimpleNamespace(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(LOG)}]}}], "usageMetadata": usage})
    monkeypatch.setattr(sw.requests, "post", fake_post)
    monkeypatch.setattr(sw, "_sleep", lambda s: None)
    uploads, deleted = [], []

    def upload(data, mime):
        uploads.append(data)
        return {"uri": "https://files.example/abc", "name": "files/abc"}
    monkeypatch.setattr(sw, "upload", upload)
    monkeypatch.setattr(sw, "delete_file", lambda name, delete=None: deleted.append(name) or True)
    pool = Pool()
    monkeypatch.setattr(sr, "_pool", pool)
    app = FastAPI()
    app.include_router(sr.make_router(studio.studio_access(lambda req: users.get(req.headers.get("x-test-user")))))
    return SimpleNamespace(client=TestClient(app), calls=calls, pool=pool, wallet=wallet, answer=answer,
                           uploads=uploads, deleted=deleted, tmp=tmp_path)


def watch(env, who="creator", vid="dN_RMA8LVi0", end=60, mode="scout", question=Q, start=0, **extra):
    return env.client.post("/api/studio/ranking/watch", headers={"x-test-user": who},
                           json={"video_id": vid, "start": start, "end": end, "mode": mode, "question": question, **extra})


def upload(env, clip=b"\x00\x00\x00 ftypmp42 my clip", who="creator", end=12):
    return env.client.post("/api/studio/ranking/watch-upload", headers={"x-test-user": who},
                           json={"data_b64": base64.b64encode(clip).decode(), "end": end, "question": Q})


def poll(env, job, who="creator"):
    return env.client.get(f"/api/studio/ranking/watch/{job}", headers={"x-test-user": who}).json()


def restart(env, *jobs, age=None):
    """What a deploy does to jobs in flight: the process holding them is gone, so nothing heartbeats them."""
    env.pool.jobs.clear()
    sr._reset_dispatch()
    with sr.db._conn() as conn:
        for job in jobs:
            conn.execute(sr.db._q("UPDATE ranking_watch_jobs SET heartbeat_at=? WHERE id=?"),
                         (time.time() - sr.LOST_S - 5, job))
            if age is not None:
                conn.execute(sr.db._q("UPDATE ranking_watch_jobs SET created_at=? WHERE id=?"), (time.time() - age, job))


def spend(user_id, usd, n=1):
    sr.initialize()
    with sr.db._conn() as conn:
        now = time.time()
        for i in range(n):
            conn.execute(sr.db._q("""INSERT INTO ranking_watch_jobs (id,user_id,source,start_s,end_s,mode,question_sha,
                model,status,cost_usd,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'done',?,?,?)"""),
                (f"rw_spent{user_id}_{i}_{time.time_ns()}", user_id, f"s{i:010d}", 0, 60, "scout", "x",
                 "gemini-test-flash", usd / n, now, now))


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
    assert watch(env, end=243).status_code == 400, "a scout is at most four minutes"
    assert watch(env, mode="detail", end=43).status_code == 400, "a detail look is at most 40 seconds"
    assert watch(env, end=1).status_code == 400
    assert watch(env, mode="everything").status_code == 400
    assert watch(env, question=" ").status_code == 400
    assert watch(env, vid="not a video").status_code == 400
    assert watch(env, who="starter").status_code in (401, 402, 403), "only Studio plans watch"


def test_a_window_a_second_or_two_too_long_is_trimmed_not_refused(env):
    """A heatmap window of 240 s from 812.4 s used to arrive as 812-1053 (241 s) and was refused, silently, for
    16-52% of every Short's candidates."""
    scout = watch(env, start=812.4, end=1053).json()
    assert scout["status"] == "running" and (scout["start"], scout["end"]) == (812, 1052)
    detail = watch(env, mode="detail", start=30, end=71).json()
    assert (detail["start"], detail["end"]) == (30, 70)


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
    assert first["job"] != again["job"]


def test_the_monthly_allowance_then_credits_only_when_the_studio_asks(env, monkeypatch):
    monkeypatch.setitem(plans.TIERS, "daily", plans.TIERS["daily"].__class__(
        **{**plans.TIERS["daily"].__dict__, "ranking_watches": 2}))
    for vid in ("aaaaaaaaaa1", "aaaaaaaaaa2"):
        assert watch(env, vid=vid).json()["credits"] == 0
    env.pool.drain()
    over = watch(env, vid="aaaaaaaaaa3")
    assert over.status_code == 402 and "credits" in over.json()["detail"] and OUT_OF_WATCHES.search(over.json()["detail"])
    paid = watch(env, vid="aaaaaaaaaa3", use_credits=True).json()
    assert paid["credits"] == sr.WATCH_CREDITS and env.wallet[7] == pytest.approx(1.0 - sr.WATCH_CREDITS)
    env.pool.drain()
    allowance = env.client.get("/api/studio/ranking/allowance", headers={"x-test-user": "creator"}).json()
    assert allowance["used"] == 2 and allowance["remaining"] == 0 and allowance["limit"] == 2
    assert allowance["credits"] == pytest.approx(1.0 - sr.WATCH_CREDITS)
    assert allowance["per_short"]["low"] >= 26, "pages can say how many Shorts the watches left make"


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


def test_a_creators_own_clip_is_watched_from_an_upload_and_stays_theirs(env):
    job = upload(env).json()
    assert (env.tmp / "spool" / f"{job['job']}.bin").is_file(), "kept on disk, not in memory, until a worker takes it"
    env.pool.drain()
    assert poll(env, job["job"])["status"] == "done" and "video_id" not in poll(env, job["job"])
    assert env.calls[0]["body"]["contents"][0]["parts"][0]["fileData"]["fileUri"] == "https://files.example/abc"
    assert env.deleted == ["files/abc"], "deleted from Gemini's file service once watched"
    assert not (env.tmp / "spool" / f"{job['job']}.bin").exists()
    other = env.client.get(f"/api/studio/ranking/watch/{job['job']}", headers={"x-test-user": "trial"})
    assert other.status_code == 404, "another account cannot read someone's own clip"


def test_an_upload_is_deleted_from_gemini_even_when_the_watch_fails(env):
    env.answer["status"] = 400
    job = upload(env).json()
    env.pool.drain()
    assert poll(env, job["job"])["status"] == "failed" and env.deleted == ["files/abc"]


def test_a_huge_upload_is_refused_before_it_is_read(env, monkeypatch):
    monkeypatch.setattr(sw, "MAX_UPLOAD_BYTES", 1000)
    big = env.client.post("/api/studio/ranking/watch-upload", headers={"x-test-user": "creator"},
                          content=b"{" + b" " * (sr.BODY_SLACK + 2000) + b"}")
    assert big.status_code == 413 and "MB" in big.json()["detail"]
    assert not env.pool.jobs and not env.uploads
    assert sr._inflight["bytes"] == 0, "the memory budget is handed back"
    odd = upload(env, clip=b"x" * 1200)
    assert odd.status_code == 400


def test_two_accounts_uploading_the_same_clip_each_get_a_job_they_can_poll(env):
    first = upload(env).json()
    second = upload(env, who="trial").json()
    assert second["job"] != first["job"]
    assert env.client.get(f"/api/studio/ranking/watch/{second['job']}", headers={"x-test-user": "trial"}).status_code == 200
    env.pool.drain()
    assert poll(env, second["job"], who="trial")["status"] == "done"


def test_watches_take_turns_so_one_account_cannot_fill_every_worker(env, monkeypatch):
    monkeypatch.setattr(sr, "WORKERS", 3)
    monkeypatch.setattr(sr, "ACCOUNT_RUNNING", 1)
    monkeypatch.setattr(sr, "ACCOUNT_RUNNING_MAX", 2)
    mine = [watch(env, vid=f"aaaaaaaaaa{i}").json()["job"] for i in range(4)]
    assert len(env.pool.jobs) == 2, "alone, an account may use idle workers, up to its limit"
    theirs = watch(env, who="trial", vid="bbbbbbbbbbb").json()["job"]
    assert len(env.pool.jobs) == 3, "another account's watch starts at once, not behind the first one's queue"
    assert [args[0] for _, args in env.pool.jobs][-1] == theirs
    assert all(poll(env, j)["status"] == "running" for j in mine), "waiting watches read as running for the Studio"
    env.pool.drain()
    assert all(poll(env, j)["status"] == "done" for j in mine) and poll(env, theirs, who="trial")["status"] == "done"


def test_an_account_with_too_many_watches_waiting_is_asked_to_wait(env, monkeypatch):
    monkeypatch.setattr(sr, "ACCOUNT_WAITING", 2)
    for i in range(2):
        assert watch(env, vid=f"aaaaaaaaaa{i}").status_code == 200
    busy = watch(env, vid="aaaaaaaaaa9")
    assert busy.status_code == 429 and TRANSIENT.search(busy.json()["detail"]), "the Studio backs off and asks again"
    assert not OUT_OF_WATCHES.search(busy.json()["detail"]), "never mistaken for running out of watches"
    assert watch(env, who="trial", vid="bbbbbbbbbbb").status_code == 200, "other accounts are unaffected"


def test_a_restart_picks_a_lost_watch_up_again_with_its_own_instructions(env):
    job = watch(env).json()["job"]
    restart(env, job)
    allowance = env.client.get("/api/studio/ranking/allowance", headers={"x-test-user": "creator"}).json()
    assert allowance["used"] == 0, "a job nobody is running does not use the allowance"
    picked = poll(env, job)
    assert picked["status"] == "running" and len(env.pool.jobs) == 1, "the poll after the restart picks it up"
    env.pool.drain()
    assert poll(env, job)["status"] == "done"
    assert env.calls[0]["body"]["contents"][0]["parts"][1]["text"] == Q, "watched with exactly the same instructions"


def test_the_supervisor_picks_up_lost_jobs_nobody_polls(env):
    job = watch(env).json()["job"]
    restart(env, job)
    sr._tick()
    assert sr._row(job)["status"] == "queued" and len(env.pool.jobs) == 1
    env.pool.drain()
    assert sr._row(job)["status"] == "done"


def test_an_upload_lost_in_a_restart_is_asked_for_again(env):
    job = upload(env).json()["job"]
    restart(env, job)
    (env.tmp / "spool" / f"{job}.bin").unlink()                 # a new container: the old disk is gone
    lost = env.client.get(f"/api/studio/ranking/watch/{job}", headers={"x-test-user": "creator"})
    assert lost.status_code == 404 and lost.json()["detail"] == "That clip watch wasn't found.", \
        "the text common.watch answers by sending the clip again"
    again = upload(env).json()
    assert again["job"] != job
    env.pool.drain()
    assert poll(env, again["job"])["status"] == "done"


def test_a_lost_watch_too_old_is_given_up_refunded_once_and_a_late_answer_cannot_revive_it(env, monkeypatch):
    monkeypatch.setitem(plans.TIERS, "daily", plans.TIERS["daily"].__class__(
        **{**plans.TIERS["daily"].__dict__, "ranking_watches": 0}))
    job = watch(env, use_credits=True).json()["job"]
    stale = env.pool.jobs[:]
    restart(env, job, age=sr.STALE_S + 5)
    assert poll(env, job)["status"] == "failed"
    assert poll(env, job)["status"] == "failed"
    assert env.wallet[7] == pytest.approx(1.0), "refunded exactly once"
    env.pool.jobs[:] = stale                                    # the old worker gets to it after all
    env.pool.drain()
    assert poll(env, job)["status"] == "failed" and env.wallet[7] == pytest.approx(1.0) and not env.calls


def test_a_new_request_replaces_a_lost_job_for_the_same_watch(env):
    old = watch(env).json()["job"]
    restart(env, old)
    new = watch(env).json()["job"]
    assert new != old and sr._row(old)["status"] == "failed", "not run twice"
    env.pool.drain()
    assert poll(env, new)["status"] == "done" and len(env.calls) == 1


def test_a_studio_whose_lost_watch_another_account_replaced_asks_again_and_shares_the_answer(env):
    """A shared public watch: the creator's job was lost in a deploy and the trial account's identical request
    replaced it. The creator's Studio must hear "not found" (common.watch then asks once more), never "stopped",
    which made it drop the candidate while the replacement finished."""
    first = watch(env, who="creator").json()["job"]
    restart(env, first)
    second = watch(env, who="trial").json()["job"]
    lost = env.client.get(f"/api/studio/ranking/watch/{first}", headers={"x-test-user": "creator"})
    assert lost.status_code == 404 and lost.json()["detail"] == "That clip watch wasn't found."
    again = watch(env, who="creator").json()
    assert again["job"] == second and again["credits"] == 0, "the creator's second ask joins the live replacement"
    env.pool.drain()
    assert poll(env, second, who="creator")["status"] == "done" and len(env.calls) == 1, "watched once, for both"


def test_a_watch_google_billed_but_could_not_answer_is_recorded(env):
    env.answer["blocked"] = True
    job = watch(env).json()["job"]
    env.pool.drain()
    row = sr._row(job)
    assert row["status"] == "failed" and row["tokens_in"] == 6000 and row["cost_usd"] == pytest.approx(sw.cost(6000, 1800))
    with sr.db._conn() as conn:
        assert sr._spent_today(conn) == pytest.approx(sw.cost(6000, 1800)), "the daily cap sees what Google billed"
    allowance = env.client.get("/api/studio/ranking/allowance", headers={"x-test-user": "creator"}).json()
    assert allowance["used"] == 0, "the customer is not charged a watch for it"


def test_the_cost_of_a_watch_given_up_while_google_was_answering_is_still_recorded(env):
    job = watch(env).json()["job"]

    def given_up():
        with sr.db._conn() as conn:
            conn.execute(sr.db._q("UPDATE ranking_watch_jobs SET status='failed', error='gave up' WHERE id=?"), (job,))
    env.answer["during"] = given_up
    env.pool.drain()
    row = sr._row(job)
    assert row["status"] == "failed" and row["cost_usd"] == pytest.approx(sw.cost(6000, 1800))


def test_a_running_watch_counts_its_estimate_until_googles_bill_is_in(env):
    watch(env, end=240)
    with sr.db._conn() as conn:
        assert sr._spent_today(conn) == pytest.approx(sr._estimate("scout", 240)) and sr._estimate("scout", 240) > 0


def test_one_account_may_use_only_its_share_of_the_days_cap(env, monkeypatch):
    monkeypatch.setenv("RANKING_WATCH_DAILY_USD", "1.0")
    spend(7, 0.30)                                              # the creator: past 25% of $1
    refused = watch(env)
    assert refused.status_code == 503 and refused.headers["X-CR-Reason"] == "account-share"
    assert SERVICE_DOWN.search(refused.json()["detail"]) and "saved" in refused.json()["detail"]
    assert watch(env, who="trial").status_code == 200, "everyone else keeps watching"


def test_the_global_cap_says_so_and_cached_watches_still_work(env, monkeypatch):
    first = watch(env).json()
    env.pool.drain()
    monkeypatch.setenv("RANKING_WATCH_DAILY_USD", "1.0")
    spend(99, 1.0, n=10)
    refused = watch(env, who="trial", vid="ccccccccccc")
    assert refused.status_code == 503 and refused.headers["X-CR-Reason"] == "global-cap"
    assert SERVICE_DOWN.search(refused.json()["detail"]) and int(refused.headers["Retry-After"]) > 0
    cached = watch(env, who="trial").json()
    assert cached["status"] == "done" and cached["cached"], "a watch already made costs nothing and is still served"
    assert first["status"] == "running"


def test_crossing_a_cap_is_logged_for_the_team(env, monkeypatch, capsys):
    monkeypatch.setenv("RANKING_WATCH_DAILY_USD", "1.0")
    monkeypatch.setenv("RANKING_ACCOUNT_SHARE", "1.0")
    spend(99, 0.6)
    watch(env)
    out = capsys.readouterr().out
    assert "[ranking-cap] ALERT" in out and "50%" in out
    watch(env, vid="aaaaaaaaaa1")
    assert "[ranking-cap] ALERT" not in capsys.readouterr().out, "once a day per threshold"


def test_the_daily_cap_grows_with_the_plans_customers_pay_for(env, monkeypatch):
    sr.initialize()
    with sr.db._conn() as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, plan TEXT NOT NULL DEFAULT 'free')")
        conn.executemany("INSERT INTO users (plan) VALUES (?)", [("daily",)] * 1000 + [("free",)] * 50)
    assert sr.daily_cap() == pytest.approx(1000 * 300 / 30 * 0.015 * 1.5), "1,000 Creators: $225 a day, not $40"
    monkeypatch.setenv("RANKING_WATCH_DAILY_USD_MAX", "100")
    assert sr.daily_cap() == pytest.approx(100.0)
    monkeypatch.delenv("RANKING_WATCH_DAILY_USD_MAX")
    sr._cap_cache.update(t=0.0)
    with sr.db._conn() as conn:
        conn.execute("DELETE FROM users WHERE plan='daily'")
    assert sr.daily_cap() == pytest.approx(40.0), "never below RANKING_WATCH_DAILY_USD"


def test_plans_give_each_studio_tier_a_monthly_allowance():
    assert plans.ranking_watch_limit("daily") == 300
    assert plans.ranking_watch_limit("studio_pro") == 900
    assert plans.ranking_watch_limit("studio_max") == 2400
    assert plans.ranking_watch_limit("studio_max_trial") == plans.TRIAL_RANKING_WATCHES
    assert plans.ranking_watch_limit("starter") == 0 and plans.ranking_watch_limit("free") == 0
    assert plans.ranking_watch_limit("free", is_admin=True) is None


# ── searches ────────────────────────────────────────────────────────────────────────────────────────────────────────
def search(env, kind="tiktok", who="creator", **body):
    return env.client.post(f"/api/studio/ranking/{kind}-search", headers={"x-test-user": who}, json=body)


def test_tiktok_search_answers_not_set_up_until_the_key_is_there(env):
    r = search(env, query="cat fails")
    assert r.status_code == 503 and "not set up" in r.json()["detail"]


def test_tiktok_search_lists_videos_once_each_and_has_a_daily_cap(env, monkeypatch):
    import requests
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "sc-test-key")
    monkeypatch.setattr(sr, "TIKTOK_DAILY", 2)
    asked = []
    item = lambda vid, views: {"aweme_info": {"aweme_id": vid, "desc": "cat vs cucumber 😭", "author": {"unique_id": "catclips"},
                                              "statistics": {"play_count": views}, "video": {"duration": 14200},
                                              "share_url": f"https://www.tiktok.com/@catclips/video/{vid}"}}

    def get(url, params=None, headers=None, timeout=None):
        asked.append((url, params, headers))
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "search_item_list": [
            item("7301", 900000), item("7301", 900000), item("7302", 12000), {"aweme_info": {"aweme_id": "bad"}}]})
    monkeypatch.setattr(requests, "get", get)
    r = search(env, query="cat fails")
    assert r.status_code == 200
    videos = r.json()["videos"]
    assert [v["id"] for v in videos] == ["7301", "7302"]
    assert videos[0] == {"id": "7301", "url": "https://www.tiktok.com/@catclips/video/7301", "title": "cat vs cucumber 😭",
                         "channel": "catclips", "views": 900000, "dur": 14.2}
    assert asked[0][2] == {"x-api-key": "sc-test-key"} and asked[0][1]["query"] == "cat fails"
    again = search(env, query="Cat  Fails")
    assert again.json()["videos"] == videos and len(asked) == 1, "the same search again is the cache's, free"
    search(env, query="dog fails")
    sr._reset_dispatch()                                        # nothing about the day is kept in memory
    capped = search(env, query="x y")
    assert capped.status_code == 429 and "used up" in capped.json()["detail"] and capped.headers["Retry-After"] == "1"
    assert search(env, who="trial", query="x y").status_code == 200, "another account's day is its own"


def youtube_body():
    video = lambda vid, kind="video", length=None, views=12345: {
        "type": kind, "id": vid, "url": f"https://www.youtube.com/{'shorts/' if kind == 'short' else 'watch?v='}{vid}",
        "title": f"Funniest dogs {vid}", "channel": {"id": "UCdogs", "title": "Dog Clips", "handle": "@dogclips"},
        "viewCountText": "1.2M views", "viewCountInt": views, "lengthText": "2:24", "lengthSeconds": length}
    return {"success": True, "credits_charged": 1,
            "videos": [video("dN_RMA8LVi0", length=144), video("dN_RMA8LVi0", length=144), video("bad id")],
            "shorts": [video("aaaaaaaaaa1", "short")], "shelves": [{"type": "shorts", "items": [
                {"type": "short", "id": "aaaaaaaaaa2", "title": "Shelf short", "viewCountText": "3.4K views"}]}]}


def test_youtube_search_answers_not_set_up_until_the_key_is_there(env):
    r = search(env, "youtube", query="funniest dogs")
    assert r.status_code == 503 and "not set up" in r.json()["detail"]


def test_youtube_search_lists_videos_and_shorts_in_the_contracts_shape(env, monkeypatch):
    import requests
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "sc-test-key")
    asked = []

    def get(url, params=None, headers=None, timeout=None):
        asked.append((url, params, headers))
        return SimpleNamespace(status_code=200, json=youtube_body)
    monkeypatch.setattr(requests, "get", get)
    videos = search(env, "youtube", query="funniest dogs").json()["videos"]
    assert asked[0][0] == sr.YOUTUBE_URL and asked[0][1] == {"query": "funniest dogs", "type": "videos", "sortBy": "relevance"}
    assert asked[0][2] == {"x-api-key": "sc-test-key"}
    assert [v["id"] for v in videos] == ["dN_RMA8LVi0", "aaaaaaaaaa1", "aaaaaaaaaa2"]
    assert videos[0] == {"id": "dN_RMA8LVi0", "title": "Funniest dogs dN_RMA8LVi0", "channel": "Dog Clips",
                         "channel_id": "UCdogs", "dur": 144.0, "views": 12345, "shorts": False}
    assert videos[1]["shorts"] and videos[1]["dur"] == 144.0, "the length text when no seconds are given"
    assert videos[2]["shorts"] and videos[2]["views"] == 3400 and videos[2]["channel"] == ""
    assert videos[2]["dur"] is None, "an unknown length is null (the Studio reads it as a Short's), never 0 s"
    shorts = search(env, "youtube", query="funniest dogs", shorts=True).json()["videos"]
    assert asked[1][1]["type"] == "shorts" and shorts[0]["id"] == "aaaaaaaaaa1", "a Shorts search is a separate search"


def test_youtube_search_has_a_daily_cap_a_spend_cap_and_a_breaker(env, monkeypatch):
    import requests
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "sc-test-key")
    monkeypatch.setattr(sr, "YOUTUBE_SEARCH_DAILY", 1)
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, params=None, headers=None, timeout=None: calls.append(params) or
                        SimpleNamespace(status_code=200, json=youtube_body))
    assert search(env, "youtube", query="dogs one").status_code == 200
    capped = search(env, "youtube", query="dogs two")
    assert capped.status_code == 429 and "used up" in capped.json()["detail"] and capped.headers["Retry-After"] == "1"
    monkeypatch.setenv("RANKING_YOUTUBE_SEARCH_DAILY_USD", "0.003")
    busy = search(env, "youtube", who="owner", query="dogs three")
    assert busy.status_code == 503 and "busy today" in busy.json()["detail"], "the global spend cap holds for everyone"
    monkeypatch.setenv("RANKING_YOUTUBE_SEARCH_DAILY_USD", "10")
    monkeypatch.setattr(requests, "get", lambda url, params=None, headers=None, timeout=None: calls.append(params) or
                        SimpleNamespace(status_code=500, json=lambda: {}))
    before = len(calls)
    for i in range(5):
        assert search(env, "youtube", who="owner", query=f"broken {i}").status_code == 502
    paused = search(env, "youtube", who="owner", query="broken 6")
    assert paused.status_code == 503 and len(calls) == before + 5, "a service that is down is left alone for a minute"


# ── cloud cleans ────────────────────────────────────────────────────────────────────────────────────────────────────
def clean_env(monkeypatch):
    import requests
    from webapp import storage
    monkeypatch.setenv("PIAPI_API_KEY", "pi-test-key")
    seen = SimpleNamespace(stored=[], deleted=[], sent=[])

    class Client:
        def put_object(self, **kw):
            seen.stored.append(kw)
    monkeypatch.setattr(storage, "_make_client", lambda: Client())
    monkeypatch.setattr(storage, "_spaces_creds", lambda: ("k", "s", "bucket", "sfo3", "e"))
    monkeypatch.setattr(storage, "presigned_get_url", lambda key, expires=0: f"https://signed/{key}?exp={expires}")
    monkeypatch.setattr(storage, "delete_key", seen.deleted.append)

    def post(url, headers=None, json=None, timeout=None):
        seen.sent.append((url, headers, json))
        return SimpleNamespace(status_code=200, json=lambda: {"code": 200, "data": {"task_id": "task-9", "status": "pending"}})
    monkeypatch.setattr(requests, "post", post)
    return seen


def clean(env, data=b"clip bytes", who="creator", **body):
    return env.client.post("/api/studio/ranking/clean", headers={"x-test-user": who},
                           json={"data_b64": base64.b64encode(data).decode(), **body})


def test_cloud_clean_is_off_without_its_key(env):
    assert clean(env, b"mp4").status_code == 503


def test_cloud_clean_stores_privately_signs_a_link_and_deletes_it_when_done(env, monkeypatch):
    import requests
    seen = clean_env(monkeypatch)
    states = iter([{"status": "processing"}, {"status": "completed", "output": {"video_url": "https://piapi/out.mp4"}}])
    monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None: SimpleNamespace(
        status_code=200, json=lambda d=next(states): {"code": 200, "data": d}))
    job = clean(env, region={"x": 0, "y": 860, "width": 1080, "height": 200}).json()["job"]
    stored = seen.stored[0]
    assert "ACL" not in stored and stored["Bucket"] == "bucket" and stored["Key"] == f"private/ranking-clean/{job}.mp4"
    body = seen.sent[0][2]
    assert seen.sent[0][1] == {"x-api-key": "pi-test-key"} and body["task_type"] == "remove-subtitle"
    assert body["input"]["video"].startswith("https://signed/private/ranking-clean/") and body["input"]["region"]["y"] == 860
    first = env.client.get(f"/api/studio/ranking/clean/{job}", headers={"x-test-user": "creator"}).json()
    assert first["status"] == "running" and not seen.deleted
    sr._breaker.clear()                                         # a deploy here: the job is in the database
    done = env.client.get(f"/api/studio/ranking/clean/{job}", headers={"x-test-user": "creator"}).json()
    assert done == {"status": "done", "video_url": "https://piapi/out.mp4", "error": ""}
    assert seen.deleted == [stored["Key"]], "the staged clip is deleted once PiAPI is done with it"
    assert env.client.get(f"/api/studio/ranking/clean/{job}", headers={"x-test-user": "trial"}).status_code == 404


def test_two_accounts_cleaning_the_same_clip_never_share_its_stored_copy(env, monkeypatch):
    seen = clean_env(monkeypatch)
    clean(env)
    clean(env, who="trial")
    assert len({s["Key"] for s in seen.stored}) == 2


def test_cloud_cleans_have_a_daily_cap_a_length_limit_and_abandoned_ones_are_swept(env, monkeypatch, tmp_path):
    seen = clean_env(monkeypatch)
    monkeypatch.setattr(sr, "CLEAN_DAILY", 1)
    job = clean(env).json()["job"]
    capped = clean(env)
    assert capped.status_code == 429 and "used up" in capped.json()["detail"] and capped.headers["Retry-After"] == "1"
    with sr.db._conn() as conn:
        conn.execute(sr.db._q("UPDATE ranking_calls SET created_at=? WHERE id=?"), (time.time() - sr.CLEAN_GIVE_UP_S - 5, job))
    sr._sweep_cleans()
    assert sr._clean_row(job)["status"] == "failed" and seen.deleted == [f"private/ranking-clean/{job}.mp4"]
    if shutil.which("ffmpeg"):                                  # PiAPI bills by the second: a long clip is refused
        long = tmp_path / "long.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=40", "-c:v", "libx264",
                        "-preset", "ultrafast", str(long)], check=True, timeout=60)
        refused = clean(env, long.read_bytes(), who="trial")
        assert refused.status_code == 400 and "seconds" in refused.json()["detail"]


def test_tiktok_search_reads_full_and_trimmed_shapes(monkeypatch):
    """Production searches came back empty with trim=true: both the documented shape (aweme_info) and a flat one read."""
    from webapp import studio_ranking as sr
    full = {"success": True, "credits_charged": 1, "search_item_list": [
        {"aweme_info": {"aweme_id": "7334621391758642478", "desc": "cat falls off couch", "share_url": "https://t/1",
                        "author": {"unique_id": "catlady"}, "statistics": {"play_count": 2500000},
                        "video": {"duration": 12000}}}]}
    flat = {"success": True, "search_item_list": [
        {"aweme_id": "7334621391758642479", "desc": "dog sees cucumber", "author": {"uniqueId": "doggo"},
         "statistics": {"playCount": "1.2M"}, "duration": 9}]}
    seen = {}

    class R:
        def __init__(self, body):
            self.status_code, self._b = 200, body

        def json(self):
            return self._b

    import requests
    for body, want in ((full, ("7334621391758642478", "catlady", 2500000, 12.0)),
                       (flat, ("7334621391758642479", "doggo", 1200000, 9.0))):
        monkeypatch.setattr(requests, "get", lambda url, params=None, headers=None, timeout=None, _b=body:
                            seen.setdefault("params", params) and R(_b) or R(_b))
        videos, credits = sr._tiktok_fetch("k", "q")
        assert [(v["id"], v["channel"], v["views"], v["dur"]) for v in videos] == [want]
    assert "trim" not in (seen.get("params") or {})
