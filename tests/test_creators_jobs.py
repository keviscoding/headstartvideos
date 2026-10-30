"""Hosted creator edits (webapp/creators_jobs.py + creators_store.py): uploads are signed only inside the creator's
own folder, a job starts one machine and serves the stages its worker writes, two free edits per account (a failed
one is given back), one edit at a time, and a machine that went quiet fails its job instead of spinning forever."""
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp import creators_jobs as cj
from webapp import creators_store as store
from webapp import database as db

USER = {"id": 7, "email": "maker@creators.test", "plan": "free"}
ADMIN = {"id": 1, "email": "boss@creators.test", "plan": "free"}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.signed = []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("404")
        return {"ContentLength": self.objects[Key]}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = len(Body)
        self.bodies = getattr(self, "bodies", {})
        self.bodies[Key] = Body

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.signed.append((op, Params, ExpiresIn))
        return f"https://media.sfo3.digitaloceanspaces.com/{Params['Key']}?op={op}"


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db", raising=False)
    monkeypatch.setattr(db, "IS_PG", False, raising=False)
    store._ready.clear()
    s3 = FakeS3()
    monkeypatch.setattr(cj, "_storage", lambda: (s3, "media"))
    monkeypatch.setenv("CREATORS_HOSTED", "1")
    monkeypatch.setenv("CREATORS_PUBLIC", "1")
    monkeypatch.delenv("CREATORS_PHONE_REQUIRED", raising=False)
    monkeypatch.delenv("CREATORS_RUN_LOCAL", raising=False)
    monkeypatch.setenv("CREATORS_DISPATCH", "0")
    monkeypatch.setattr(cj, "is_admin", lambda u: bool(u) and u["email"] == ADMIN["email"])
    spawned, stopped = [], []

    def spawn(job_id):
        spawned.append(job_id)
        return f"m{len(spawned)}"

    monkeypatch.setattr(cj, "spawn", spawn)
    monkeypatch.setattr(cj, "stop_machine", lambda mid: stopped.append(mid))
    who = {"user": USER}
    app = FastAPI()
    app.include_router(cj.make_router(lambda: who["user"]))
    app.include_router(cj.make_review_router(lambda: who["user"]))
    client = TestClient(app)
    client.s3, client.spawned, client.stopped, client.who = s3, spawned, stopped, who
    return client


def upload(api, kind="take", size=50_000_000, name="My take.MOV", type_="video/quicktime"):
    r = api.post("/api/creators/uploads", json={"kind": kind, "name": name, "size": size, "type": type_})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    api.s3.objects[key] = size                       # the browser's PUT landed
    return r.json()


def test_upload_is_signed_for_one_key_in_the_creators_folder(api):
    up = upload(api)
    assert up["key"].startswith("creators/u7/up/") and up["key"].endswith("/take-My-take.mov")
    op, params, seconds = api.s3.signed[-1]
    assert op == "put_object" and params["ContentType"] == "video/quicktime" and params["ContentLength"] == 50_000_000
    assert up["headers"] == {"Content-Type": "video/quicktime"} and seconds <= 3 * 3600
    r = api.post("/api/creators/uploads", json={"kind": "take", "name": "x.mp4", "size": 3 * 1024 ** 3})
    assert r.status_code == 413


def test_a_job_starts_one_machine_and_shows_the_workers_stages(api):
    up = upload(api)
    r = api.post("/api/creators/jobs", json={"take_key": up["key"], "take_name": "My take.MOV", "look": "energy"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "starting" and job["free"] and api.spawned == [job["id"]]
    assert [s["label"] for s in job["stages"]] == ["Every word, caught", "Dead air, gone", "Music made for it",
                                                   "B-roll on your words", "Title cards that hit", "Watched back"]
    # the worker takes it and moves the stages
    assert store.claim(job["id"], "m1")["status"] == "running"
    assert store.claim(job["id"], "m1") is None                       # never twice
    store.set_stage(job["id"], "words", "done", "212 words, each one timed")
    store.set_stage(job["id"], "deadair", "active")
    seen = api.get(f"/api/creators/jobs/{job['id']}").json()
    assert seen["stage"] == "deadair" and 0.14 <= seen["progress"] < 0.2
    assert seen["stages"][0] == {"key": "words", "label": "Every word, caught", "state": "done",
                                 "detail": "212 words, each one timed"}
    store.finish(job["id"], "done", output_key=f"creators/u7/jobs/{job['id']}/reel.mp4",
                 poster_key=f"creators/u7/jobs/{job['id']}/poster.jpg", result={"seconds": 41.2})
    done = api.get(f"/api/creators/jobs/{job['id']}").json()
    assert done["status"] == "done" and done["progress"] == 1.0 and done["result"]["seconds"] == 41.2
    assert "reel.mp4" in done["result"]["video_url"] and "poster.jpg" in done["result"]["poster_url"]
    disp = [p for op, p, _ in api.s3.signed if p.get("ResponseContentDisposition")]
    assert disp and "my-take-energy.mp4" in disp[-1]["ResponseContentDisposition"].lower()


def test_someone_elses_upload_or_job_is_refused(api):
    up = upload(api)
    api.who["user"] = {"id": 8, "email": "other@creators.test", "plan": "free"}
    r = api.post("/api/creators/jobs", json={"take_key": up["key"], "look": "story"})
    assert r.status_code == 400 and "isn't yours" in r.json()["detail"]
    api.who["user"] = USER
    job = api.post("/api/creators/jobs", json={"take_key": up["key"], "look": "story"}).json()
    api.who["user"] = {"id": 8, "email": "other@creators.test", "plan": "free"}
    assert api.get(f"/api/creators/jobs/{job['id']}").status_code == 404
    r = api.post("/api/creators/jobs", json={"take_key": "creators/u7/../u8/x.mp4", "look": "story"})
    assert r.status_code == 400


def test_two_free_edits_one_at_a_time_and_a_failed_one_comes_back(api):
    def start():
        return api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story"})

    first = start().json()
    assert start().status_code == 409                                  # one at a time
    store.finish(first["id"], "failed", error="boom")                  # failed: given back
    assert api.get("/api/creators/me").json()["free_left"] == 2
    second = start().json()
    store.finish(second["id"], "done")
    third = start().json()
    store.finish(third["id"], "done")
    assert api.get("/api/creators/me").json()["free_left"] == 0
    r = start()
    assert r.status_code == 402 and "2 free edits" in r.json()["detail"]
    api.who["user"] = ADMIN                                            # the team isn't counted
    assert api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story"}).json()["free"] is False


def test_gates_off_switch_admins_only_and_phone(api, monkeypatch):
    up = upload(api)
    monkeypatch.setenv("CREATORS_PUBLIC", "0")
    assert api.post("/api/creators/jobs", json={"take_key": up["key"], "look": "story"}).status_code == 403
    assert api.get("/api/creators/me").json()["allowed"] is False
    monkeypatch.setenv("CREATORS_PUBLIC", "1")
    monkeypatch.setenv("CREATORS_PHONE_REQUIRED", "1")
    r = api.post("/api/creators/jobs", json={"take_key": up["key"], "look": "story"})
    assert r.status_code == 403 and "phone" in r.json()["detail"]
    monkeypatch.setenv("CREATORS_HOSTED", "0")
    assert api.post("/api/creators/uploads", json={"kind": "take", "name": "a.mp4", "size": 9}).status_code == 503
    assert api.get("/api/creators/config").json()["hosted"] is False
    assert api.post("/api/creators/jobs", json={"take_key": up["key"], "look": "nope"}).status_code == 503


def test_no_machine_fails_the_job_at_once_and_gives_the_edit_back(api, monkeypatch):
    def broken(job_id):
        raise RuntimeError("Fly API POST → 422: billing")

    monkeypatch.setattr(cj, "spawn", broken)
    r = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy"})
    assert r.status_code == 503
    me = api.get("/api/creators/me").json()
    assert me["free_left"] == 2 and me["jobs"][0]["status"] == "failed"


def test_a_machine_that_went_quiet_fails_its_job_and_is_stopped(api):
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy"}).json()
    store.claim(job["id"], "m1")
    store.update(job["id"], heartbeat_at=time.time() - cj.STALE_S - 5)
    seen = api.get(f"/api/creators/jobs/{job['id']}").json()
    assert seen["status"] == "failed" and "stopped answering" in seen["error"] and api.stopped == ["m1"]
    job2 = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy"}).json()
    store.update(job2["id"], heartbeat_at=time.time() - cj.START_S - 5)
    assert api.get(f"/api/creators/jobs/{job2['id']}").json()["status"] == "failed"


def test_cancel_stops_the_machine(api):
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy"}).json()
    r = api.post(f"/api/creators/jobs/{job['id']}/cancel").json()
    assert r["status"] == "cancelled" and api.stopped == ["m1"]


def test_the_machine_gets_only_what_an_edit_needs(monkeypatch):
    for k, v in {"DATABASE_URL": "postgres://x", "SPACES_KEY": "k", "SPACES_SECRET": "s", "SPACES_BUCKET": "b",
                 "ANTHROPIC_KEY": "sk-ant", "GEMINI_API_KEY": "g", "CREATORS_KIE_KEY": "kie",
                 "STRIPE_SECRET_KEY": "sk_live", "FLY_API_TOKEN": "fly"}.items():
        monkeypatch.setenv(k, v)
    env = cj.machine_env("ce_1")
    assert env["ANTHROPIC_API_KEY"] == "sk-ant" and env["KIE_API_KEY"] == "kie" and env["CREATORS_CLAUDE"] == "api"
    assert "STRIPE_SECRET_KEY" not in env and "FLY_API_TOKEN" not in env and env["DB_POOL_MAX"] == "2"


def test_past_the_cap_jobs_wait_in_line_and_start_when_a_slot_frees(api, monkeypatch):
    monkeypatch.setenv("CREATORS_MAX_RUNNING", "1")
    first = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy"}).json()
    api.who["user"] = {"id": 9, "email": "third@creators.test", "plan": "free"}
    second = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy"}).json()
    assert second["status"] == "queued" and second["in_line"] == 1 and api.spawned == [first["id"]]
    assert cj.dispatch_once() == 0                                  # still full
    store.finish(first["id"], "done", output_key="k")
    assert cj.dispatch_once() == 1 and api.spawned == [first["id"], second["id"]]
    seen = api.get(f"/api/creators/jobs/{second['id']}").json()
    assert seen["status"] == "starting" and seen["in_line"] == 0
    assert cj.dispatch_once() == 0                                  # never twice
    api.who["user"] = {"id": 10, "email": "fourth@creators.test", "plan": "free"}
    third = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy"}).json()
    store.update(third["id"], created_at=time.time() - cj.LINE_S - 5)
    gone = api.get(f"/api/creators/jobs/{third['id']}").json()
    assert gone["status"] == "failed" and "too busy" in gone["error"]


def finished_edit(api):
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy"}).json()
    store.finish(job["id"], "done", output_key=f"creators/u7/jobs/{job['id']}/reel.mp4", result={"seconds": 40.0})
    return job


NOTES = {"version": 1, "reel": {"id": "x"}, "notes": [
    {"id": "n1", "t": 27.9, "kind": "change", "text": "move it up", "audio": "notes/n1.webm",
     "frame_png": "notes/n1_frame.png", "drawing_png": "notes/n1_drawing.png", "marks": []}]}


def test_a_review_round_stores_the_notes_and_starts_the_next_version(api):
    job = finished_edit(api)
    files = [("notes", ("notes.json", json.dumps(NOTES).encode(), "application/json")),   # as review.js sends it
             ("notes/n1_frame.png", ("n1_frame.png", b"png-bytes", "image/png")),
             ("notes/n1_drawing.png", ("n1_drawing.png", b"ink", "image/png")),
             ("notes/n1.webm", ("n1.webm", b"opus", "audio/webm"))]
    r = api.post(f"/api/creators/jobs/{job['id']}/notes", files=files)
    assert r.status_code == 200, r.text
    nxt = r.json()
    assert nxt["kind"] == "notes" and nxt["parent"] == job["id"] and nxt["free"] is False
    assert [s["label"] for s in nxt["stages"]] == ["Hearing your notes", "Planning the changes",
                                                   "Re-cutting your Reel", "Checking it"]
    base = f"creators/u7/jobs/{nxt['id']}/"
    assert json.loads(api.s3.bodies[base + "notes.json"])["notes"][0]["id"] == "n1"
    assert api.s3.bodies[base + "notes/n1_frame.png"] == b"png-bytes" and api.spawned[-1] == nxt["id"]
    assert api.get("/api/creators/me").json()["free_left"] == 1             # a round isn't another free edit


def test_review_rounds_are_capped_and_bad_files_refused(api):
    job = finished_edit(api)
    bad = [("notes", (None, json.dumps(NOTES), "application/json")),
           ("../../etc/passwd", ("x", b"x", "text/plain"))]
    assert api.post(f"/api/creators/jobs/{job['id']}/notes", files=bad).status_code == 400
    ok = [("notes", (None, json.dumps(NOTES), "application/json"))]
    for _ in range(3):
        nxt = api.post(f"/api/creators/jobs/{job['id']}/notes", files=ok).json()
        store.finish(nxt["id"], "done", output_key="k")
    r = api.post(f"/api/creators/jobs/{job['id']}/notes", files=ok)
    assert r.status_code == 402 and "3 rounds" in r.json()["detail"]


def test_voice_notes_are_written_out_by_groq(api, monkeypatch):
    import sys
    import types
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    seen = {}

    class Groq:
        def __init__(self, api_key, timeout):
            self.audio = types.SimpleNamespace(transcriptions=types.SimpleNamespace(create=self.create))

        def create(self, file, model, **kw):
            seen.update(name=file[0], size=len(file[1]), model=model)
            return types.SimpleNamespace(text=" move it up ")

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=Groq))
    r = api.post("/creators/api/review/transcribe", content=b"opus" * 10, headers={"Content-Type": "audio/mp4"})
    assert r.json() == {"text": "move it up"} and seen["name"] == "note.m4a" and seen["size"] == 40
    api.who["user"] = None
    assert api.post("/creators/api/review/transcribe", content=b"x").status_code == 401


def test_the_ready_email_goes_once_per_reel_in_the_house_style(api, monkeypatch):
    w = pytest.importorskip("webapp.creators_worker")   # the edit machines' image only: not in the public repo
    from webapp import lifecycle_email as le
    sent = []
    monkeypatch.setattr(le, "_send", lambda user, kind, subject, page, plain: sent.append((user, kind, page)) or True)
    monkeypatch.setenv("CREATORS_UNSUB_URL", "https://channelrecipe.com/email/unsubscribe?u=7&t=abc")
    le._ready.clear()
    user = {"id": 7, "email": "maker@creators.test"}
    assert w.send_ready(user, "ce_1", "Creator energy", "https://dl/reel.mp4", 41.0, True)
    assert not w.send_ready(user, "ce_1", "Creator energy", "https://dl/reel.mp4", 41.0, True)     # once
    u, kind, page = sent[0]
    assert kind == "creator_reel:ce_1" and u["unsubscribe"].endswith("t=abc") and "https://dl/reel.mp4" in page
    assert "0:41 Reel is edited in Creator energy, and we watched it back" in page
    monkeypatch.delenv("CREATORS_UNSUB_URL")
    assert not w.send_ready(user, "ce_2", "Creator energy", "https://dl/x.mp4", 9.0, False)      # no signed link


class FakeVerify:
    def __init__(self):
        self.sent = []

    def start(self, phone):
        self.sent.append(phone)

    def check(self, phone, code):
        return code == "123456"


def test_phone_check_one_number_per_account_and_limits(api, monkeypatch):
    from webapp import creators_phone as phone
    fake = FakeVerify()
    monkeypatch.setattr(phone, "provider", lambda: fake)
    monkeypatch.setenv("CREATORS_PHONE_REQUIRED", "1")
    up = upload(api)
    assert api.post("/api/creators/jobs", json={"take_key": up["key"], "look": "story"}).status_code == 403
    r = api.post("/api/creators/phone/start", json={"phone": "415 555"})
    assert r.status_code == 400 and "country code" in r.json()["detail"]
    assert api.post("/api/creators/phone/start", json={"phone": "+1 (415) 555-0134"}).json() == {"ok": True}
    assert fake.sent == ["+14155550134"]
    assert api.post("/api/creators/phone/check", json={"phone": "+14155550134", "code": "999999"}).status_code == 400
    assert api.post("/api/creators/phone/check", json={"phone": "+14155550134", "code": "123456"}).json()["phone_verified"]
    assert api.get("/api/creators/me").json()["phone_verified"] is True
    assert api.post("/api/creators/jobs", json={"take_key": up["key"], "look": "story"}).status_code == 200
    with db._conn() as conn:                                       # only a fingerprint is kept, never the number
        row = conn.execute("SELECT phone_hash FROM creator_phones").fetchone()
    assert "4155550134" not in row["phone_hash"] and len(row["phone_hash"]) == 64
    api.who["user"] = {"id": 8, "email": "other@creators.test", "plan": "free"}
    r = api.post("/api/creators/phone/start", json={"phone": "+14155550134"})
    assert r.status_code == 400 and "another account" in r.json()["detail"]
    for _ in range(3):
        api.post("/api/creators/phone/start", json={"phone": "+447700900123"})
    r = api.post("/api/creators/phone/start", json={"phone": "+447700900123"})
    assert r.status_code == 400 and "a lot of codes" in r.json()["detail"]


def test_twilio_verify_calls_and_errors():
    from webapp import creators_phone as phone
    calls = []

    class Res:
        def __init__(self, code, body):
            self.status_code, self._b, self.content = code, body, b"x"

        def json(self):
            return self._b

    def post(url, data, auth, timeout):
        calls.append((url.rsplit("/", 1)[1], data, auth))
        if data.get("Code") == "1":
            return Res(200, {"status": "approved"})
        if data["To"] == "+15550000000":
            return Res(400, {"code": 60200})
        return Res(201, {"status": "pending"})

    t = phone.TwilioVerify("AC1", "tok", "VA1", post=post)
    t.start("+14155550134")
    assert t.check("+14155550134", "1") and calls[0] == ("Verifications", {"To": "+14155550134", "Channel": "sms"},
                                                         ("AC1", "tok"))
    try:
        t.start("+15550000000")
        raise AssertionError("should refuse")
    except phone.PhoneError as e:
        assert "can't get a text" in str(e)
