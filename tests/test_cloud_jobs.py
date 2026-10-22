"""Studio Cloud jobs (webapp/cloud_jobs.py): a job reserves its most before a machine starts and settles what it used;
a machine only gets the creator's allowed render settings and links signed inside their own folder; its Claude
questions wait for the creator's Studio and come back as the Anthropic API would answer."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from webapp import cloud_jobs as cj
from webapp import cloud_wallet as cw
from webapp import database as db

USER = {"id": 7, "email": "team@channelrecipe.test", "plan": "studio_max"}


class FakeS3:
    def __init__(self):
        self.objects = {"studio-cloud/u7/out/a/video.mp4": 10, "studio-cloud/u7/jobs/a.json": 2,
                        "studio-cloud/u8/out/b/video.mp4": 5}
        self.deleted = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys, **kw):
        hits = [{"Key": k, "Size": s, "LastModified": datetime(2026, 10, 22, tzinfo=timezone.utc)}
                for k, s in sorted(self.objects.items()) if k.startswith(Prefix)]
        return {"Contents": hits, "IsTruncated": False}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("404")

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://media.sfo3.digitaloceanspaces.com/{Params['Key']}?sig=1&op={op}"


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db", raising=False)
    monkeypatch.setattr(db, "IS_PG", False, raising=False)
    cw._ready.clear()
    cj._ready.clear()
    s3 = FakeS3()
    monkeypatch.setattr(cj, "_storage", lambda: (s3, "media", "https://sfo3.digitaloceanspaces.com"))
    from webapp import storage
    monkeypatch.setattr(storage, "_spaces_creds",
                        lambda: ("k", "s", "media", "sfo3", "https://sfo3.digitaloceanspaces.com"))
    monkeypatch.setattr(cj, "public", lambda user: True)
    calls = []

    def control(path, body, timeout=60):
        calls.append((path, body))
        if path == "/spawn":
            return {"call_id": "fc-1"}
        if path == "/status":
            return cloud.state
        return {"ok": True}

    monkeypatch.setattr(cj, "_control", control)
    cloud = SimpleNamespace(cj=cj, s3=s3, calls=calls, state={"state": "running"})
    return cloud


def _env(**extra):
    return {"FARM_JOB_KEY": "jobs/a.json", "FARM_SLUG": "a", "FARM_TITLE": "A video",
            "FARM_CODE_URL": "https://media.sfo3.digitaloceanspaces.com/studio-cloud/u7/code/c.zip?sig=1",
            "WAVESPEED_API_KEY": "ws-key", "ANTHROPIC_API_KEY": "sk-ant-x", "RUNPOD_API_KEY": "studio-cloud",
            "FARM_BUCKET_SECRET": "studio-cloud", "OPENLUX_API_KEY": "theirs", **extra}


def test_a_quote_says_what_it_usually_costs_and_the_most_it_can(cloud):
    q = cj.quote(10, "cpu")
    assert q["expected_minutes"] == 36 and q["expected_cents"] == 72
    assert q["max_minutes"] == 82 and q["max_cents"] == 164, "the reservation covers twice the usual time"
    assert cj.quote(500, "cpu")["max_minutes"] == cj.MAX_MINUTES
    with pytest.raises(HTTPException):
        cj.quote(10, "tpu")


def test_nothing_starts_when_the_balance_cannot_cover_the_most_it_can_cost(cloud):
    cw.credit_topup(7, 100, ref="cs_1")
    with pytest.raises(HTTPException) as e:
        cj.start_job(USER, "cpu", 10, _env())
    assert e.value.status_code == 402 and "$1.64" in e.value.detail and "Nothing was charged" in e.value.detail
    assert cw.balance_cents(7) == 100 and not [c for c in cloud.calls if c[0] == "/spawn"]


def test_a_job_reserves_starts_and_settles_only_the_time_it_ran(cloud, monkeypatch):
    cw.credit_topup(7, 1000, ref="cs_1")
    job = cj.start_job(USER, "cpu", 10, _env(), "a", "A video")
    assert job["status"] == "running" and cw.balance_cents(7) == 1000 - 164
    spawn = next(body for path, body in cloud.calls if path == "/spawn")
    env = spawn["env"]
    assert env["WAVESPEED_API_KEY"] == "ws-key", "the creator's render keys go to the machine"
    for gone in ("ANTHROPIC_API_KEY", "RUNPOD_API_KEY", "FARM_BUCKET_SECRET"):
        assert gone not in env
    assert env["CLAUDE_RELAY"] == "openlux" and env["OPENLUX_API_KEY"].startswith("crj_"), \
        "Claude goes to the relay, with the job's own token instead of the creator's OpenLux key"
    assert env["OPENLUX_API_KEY"] == env["FARM_JOB_TOKEN"] and env["FARM_MAX_MINUTES"] == "82"
    assert spawn["max_seconds"] == 82 * 60

    row = cj._job(job["id"])
    monkeypatch.setattr(cj.time, "time", lambda: row["started_at"] + 1800)
    cloud.state = {"state": "done", "result": {"ok": True, "seconds": 1500}}
    done = cj.refresh(cj._job(job["id"]), force=True)
    assert done["status"] == "done" and done["charged_cents"] == 50, "25 machine-minutes at 2 cents"
    assert cw.balance_cents(7) == 1000 - 50


def test_a_machine_only_gets_links_signed_inside_its_creators_folder(cloud):
    cw.credit_topup(7, 1000, ref="cs_1")
    for bad in ("https://media.sfo3.digitaloceanspaces.com/studio-cloud/u8/code/c.zip?sig=1",
                "https://evil.example/studio-cloud/u7/code/c.zip",
                "http://media.sfo3.digitaloceanspaces.com/studio-cloud/u7/code/c.zip",
                "https://media.sfo3.digitaloceanspaces.com/studio-cloud/u7/../u8/c.zip"):
        with pytest.raises(HTTPException) as e:
            cj.start_job(USER, "cpu", 10, _env(FARM_CODE_URL=bad))
        assert e.value.status_code == 400
    assert cw.balance_cents(7) == 1000


def test_storage_is_signed_one_request_at_a_time_inside_the_creators_folder(cloud):
    listed = cj.storage_op(7, "list", "out/")
    assert [i["key"] for i in listed["items"]] == ["out/a/video.mp4"], "another creator's folder never shows"
    assert cj.storage_op(7, "exists", "jobs/a.json") == {"exists": True}
    assert cj.storage_op(7, "exists", "jobs/b.json") == {"exists": False}
    url = cj.storage_op(7, "presign", "status/a.json", method="PUT")["url"]
    assert "/studio-cloud/u7/status/a.json" in url and "put_object" in url
    for bad in ("../u8/out/b/video.mp4", "/etc/passwd/../x", ""):
        with pytest.raises(HTTPException):
            cj.storage_op(7, "presign", bad)


def test_cancelling_settles_by_the_time_it_ran(cloud, monkeypatch):
    cw.credit_topup(7, 1000, ref="cs_1")
    job = cj.start_job(USER, "cpu", 10, _env())
    row = cj._job(job["id"])
    monkeypatch.setattr(cj.time, "time", lambda: row["started_at"] + 600)
    out = cj.cancel(row)
    assert out["status"] == "cancelled" and out["charged_cents"] == 20
    assert ("/cancel", {"call_id": "fc-1"}) in cloud.calls


def _app():
    app = FastAPI()
    app.include_router(cj.make_router(lambda: USER))
    return TestClient(app)


def test_a_machines_claude_question_is_answered_by_the_creators_studio(cloud, monkeypatch):
    monkeypatch.setenv("CR_CLOUD_SWEEP", "0")
    monkeypatch.setattr(cj, "RELAY_WAIT_S", 30)
    cw.credit_topup(7, 1000, ref="cs_1")
    job = cj.start_job(USER, "cpu", 10, _env())
    token = next(b for p, b in cloud.calls if p == "/spawn")["env"]["FARM_JOB_TOKEN"]
    client = _app()

    import threading

    def studio():                                        # the creator's Studio, answering when the question arrives
        for _ in range(50):
            q = cj.relay_next(7)
            if q:
                assert q["request"]["messages"][0]["content"] == "Pick the best shot."
                cj.relay_answer(7, q["id"], "Shot 3.")
                return
            import time
            time.sleep(0.1)
    t = threading.Thread(target=studio)
    t.start()
    r = client.post("/api/studio/cloud/relay/v1/messages", headers={"x-api-key": token},
                    json={"model": "claude-sonnet-5", "max_tokens": 100, "stream": True,
                          "messages": [{"role": "user", "content": "Pick the best shot."}]})
    t.join()
    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    text = "".join(e["delta"]["text"] for e in events if e["type"] == "content_block_delta")
    assert r.status_code == 200 and text == "Shot 3." and events[-1]["type"] == "message_stop"

    t = threading.Thread(target=studio)
    t.start()
    r = client.post("/api/studio/cloud/relay/v1/messages", headers={"x-api-key": token},
                    json={"model": "claude-sonnet-5", "max_tokens": 100,
                          "messages": [{"role": "user", "content": "Pick the best shot."}]})
    t.join()
    assert r.status_code == 200 and r.json()["content"][0]["text"] == "Shot 3.", "a plain request gets plain JSON"

    assert client.post("/api/studio/cloud/relay/v1/messages", headers={"x-api-key": "crj_nope"},
                       json={"messages": [{"role": "user", "content": "x"}]}).status_code == 401
    cj.cancel(cj._job(job["id"]))
    assert client.post("/api/studio/cloud/relay/v1/messages", headers={"x-api-key": token},
                       json={"messages": [{"role": "user", "content": "x"}]}).status_code == 401, \
        "an ended job's token opens nothing"


def test_an_unanswered_claim_goes_back_in_the_queue(cloud, monkeypatch):
    cw.credit_topup(7, 1000, ref="cs_1")
    job = cj.start_job(USER, "cpu", 10, _env())
    rid = cj.relay_enqueue(cj._job(job["id"]), {"messages": [{"role": "user", "content": "x"}]})
    assert cj.relay_next(7)["id"] == rid and cj.relay_next(7) is None
    later = cj.time.time() + cj.RELAY_CLAIM_S + 5
    monkeypatch.setattr(cj.time, "time", lambda: later)
    assert cj.relay_next(7)["id"] == rid, "a Studio that closed mid-answer doesn't lose the question"
    assert cj.relay_next(8) is None, "another creator's Studio never sees it"


def test_only_the_team_can_add_balance(cloud, monkeypatch):
    from webapp import frontier_studio
    monkeypatch.setattr(db, "get_user_by_email", lambda email: {"id": 9} if email == "flor@example.test" else None)
    monkeypatch.setattr(frontier_studio, "is_admin", lambda user: user["email"] == USER["email"])
    client = _app()
    r = client.post("/api/studio/cloud/admin/credit", json={"email": "Flor@example.test", "usd": 5, "note": "re-clone"})
    assert r.status_code == 200 and r.json() == {"balance_usd": 5.0}
    assert cw.ledger(9)[0]["kind"] == "grant" and "by team@channelrecipe.test" in cw.ledger(9)[0]["note"]
    assert client.post("/api/studio/cloud/admin/credit", json={"email": "x@y.z", "usd": 5}).status_code == 404
    assert client.post("/api/studio/cloud/admin/credit", json={"usd": 2}).json() == {"balance_usd": 2.0}, \
        "no email: the team member's own account"
    assert client.post("/api/studio/cloud/admin/credit", json={"email": "flor@example.test", "usd": 500}).status_code == 400
    monkeypatch.setattr(frontier_studio, "is_admin", lambda user: False)
    assert client.post("/api/studio/cloud/admin/credit", json={"email": "flor@example.test", "usd": 5}).status_code == 403
