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
    assert {k: v for k, v in seen["stages"][0].items() if k != "at"} == {
        "key": "words", "label": "Every word, caught", "state": "done", "detail": "212 words, each one timed"}
    assert seen["stages"][1]["at"] > time.time() - 5                  # an active stage says when it started
    store.finish(job["id"], "done", output_key=f"creators/u7/jobs/{job['id']}/reel.mp4",
                 poster_key=f"creators/u7/jobs/{job['id']}/poster.jpg", result={"seconds": 41.2})
    done = api.get(f"/api/creators/jobs/{job['id']}").json()
    assert done["status"] == "done" and done["progress"] == 1.0 and done["result"]["seconds"] == 41.2
    assert "reel.mp4" in done["result"]["video_url"] and "poster.jpg" in done["result"]["poster_url"]
    disp = [p for op, p, _ in api.s3.signed if p.get("ResponseContentDisposition")]
    assert disp and "my-take-energy.mp4" in disp[-1]["ResponseContentDisposition"].lower()


def test_pasted_reel_links_ride_with_the_job_and_name_its_look(api):
    """The owner's landing page promises "paste a Reel you love": the links reach the machine (which learns the style
    first), anything that isn't a TikTok, Reel or Short is refused, and a learned style names the result."""
    bad = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy",
                                               "refs": ["https://example.com/tiktok.com/x"]})
    assert bad.status_code == 400 and "TikTok" in bad.json()["detail"]
    links = ["https://www.tiktok.com/@ahormozi/video/7689188323323120909", "https://www.instagram.com/reel/C1kzUmsRcEl/"]
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy", "refs": links}).json()
    assert job["refs"] == links and store.get(job["id"])["refs"] == links and job["look_name"] == "Creator energy"
    store.finish(job["id"], "done", output_key=f"creators/u7/jobs/{job['id']}/reel.mp4",
                 result={"seconds": 30.0, "summary": {"style": {"name": "Your Alex Hormozi cut", "from": "bold"}}})
    assert api.get(f"/api/creators/jobs/{job['id']}").json()["look_name"] == "Your Alex Hormozi cut"


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


def test_when_our_claude_account_cannot_answer_the_edit_stops_gives_the_edit_back_and_tells_us(api, monkeypatch):
    w = pytest.importorskip("webapp.creators_worker")   # the edit machines' image only: not in the public repo
    from creators.hosted import claude_api
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story"}).json()
    alerts, edited = [], []
    monkeypatch.setattr(claude_api, "enabled", lambda: True)
    monkeypatch.setattr(claude_api, "DOWN", "")

    def ready():
        claude_api.DOWN = "Claude API HTTP 400: Your credit balance is too low"
        return claude_api.DOWN
    monkeypatch.setattr(claude_api, "ready", ready)
    monkeypatch.setattr(w, "alert_admins", lambda subject, text: alerts.append((subject, text)))
    monkeypatch.setattr(w, "_edit", lambda *a: edited.append(a) or 0)
    assert w.run(job["id"]) == 1
    row = store.get(job["id"])
    assert row["status"] == "failed" and row["error"] == w.PAUSED and "didn't use a free edit" in w.PAUSED
    assert not edited                                   # stopped before ten minutes of a bare cut
    assert "credit balance" in alerts[0][1] and job["id"] in alerts[0][1]
    assert api.get("/api/creators/me").json()["free_left"] == 2


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


# ── the creators home: projects (one per take, its notes rounds as versions v1, v2...) ──

def _round(api, parent_id, status="done"):
    """A notes round on `parent_id`, finished with `status`."""
    nxt = api.post(f"/api/creators/jobs/{parent_id}/notes",
                   files=[("notes", (None, json.dumps(NOTES), "application/json"))]).json()
    if status == "done":
        store.finish(nxt["id"], "done", output_key=f"creators/u7/jobs/{nxt['id']}/reel.mp4",
                     poster_key=f"creators/u7/jobs/{nxt['id']}/poster.jpg",
                     result={"seconds": 39.0, "summary": {"title": "Three mistakes new creators make"}})
    elif status:
        store.finish(nxt["id"], status, error="The edit machine stopped answering. Nothing was charged: try again.")
    return nxt


def test_projects_group_a_take_and_its_rounds_newest_first(api):
    first = finished_edit(api)                                      # v1, then a failed round, then v2
    store.update(first["id"], poster_key=f"creators/u7/jobs/{first['id']}/poster.jpg")
    _round(api, first["id"], "failed")
    v2 = _round(api, first["id"])
    other = api.post("/api/creators/jobs", json={"take_key": upload(api, name="IMG_4021.MOV")["key"],
                                                 "take_name": "IMG_4021.MOV", "look": "story"}).json()
    store.claim(other["id"], "m9")
    store.set_stage(other["id"], "words", "done")
    ps = api.get("/api/creators/projects").json()["projects"]
    assert [p["id"] for p in ps] == [other["id"], first["id"]]       # the running one was active last
    running, done = ps
    assert running["state"] == "editing" and running["title"] == "IMG 4021" and running["version"] == 1
    assert running["minutes_left"] >= 1 and 0.1 < running["progress"] < 1 and running["take_url"]
    assert running["poster_url"] == "" and running["ready_version"] == 0 and running["notify"] is True
    assert done["state"] == "ready" and done["version"] == 2 and done["ready_version"] == 2
    assert [v["n"] for v in done["versions"]] == [1, 2]                 # the failed round got no number
    assert done["open_id"] == done["latest_id"] == v2["id"]
    assert done["title"] == "Three mistakes new creators make"         # written by the edit
    assert "poster.jpg" in done["poster_url"] and v2["id"] in done["download_url"]
    assert done["look_name"] == cj.LOOK_NAMES["energy"] and done["take_url"] == ""


def test_project_states_making_a_version_and_one_that_did_not_finish(api):
    first = finished_edit(api)
    making = _round(api, first["id"], status="")                      # v2 still going
    p = api.get("/api/creators/projects").json()["projects"][0]
    assert p["state"] == "making" and p["version"] == 2 and p["ready_version"] == 1 and p["open_id"] == first["id"]
    assert p["notify"] is False                                       # rounds don't email
    store.finish(making["id"], "failed", error="The edit machine stopped answering. Nothing was charged: try again.")
    p = api.get("/api/creators/projects").json()["projects"][0]
    assert p["state"] == "failed" and p["version"] == 2 and p["ready_version"] == 1 and "Nothing was charged" in p["error"]
    assert p["open_id"] == first["id"] and p["latest_id"] == making["id"]
    api.who["user"] = {"id": 8, "email": "other@creators.test", "plan": "free"}
    fail = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story"}).json()
    store.finish(fail["id"], "failed", error="We couldn't start an edit machine.")
    p = api.get("/api/creators/projects").json()["projects"][0]
    assert p["state"] == "failed" and p["version"] == 1 and p["versions"] == [] and p["open_id"] == fail["id"]


def test_rename_and_soft_delete_only_your_own_projects(api):
    first = finished_edit(api)
    v2 = _round(api, first["id"])
    r = api.patch(f"/api/creators/projects/{first['id']}", json={"title": "  My   morning routine "})
    assert r.status_code == 200 and r.json()["title"] == "My morning routine" and r.json()["renamed"]
    assert api.get("/api/creators/projects").json()["projects"][0]["title"] == "My morning routine"
    assert api.patch(f"/api/creators/projects/{v2['id']}", json={"title": ""}).json()["title"] == \
        "Three mistakes new creators make"                                # any version names it; empty = ours again
    api.who["user"] = {"id": 8, "email": "other@creators.test", "plan": "free"}
    assert api.get("/api/creators/projects").json()["projects"] == []
    assert api.patch(f"/api/creators/projects/{first['id']}", json={"title": "mine now"}).status_code == 404
    assert api.delete(f"/api/creators/projects/{first['id']}").status_code == 404
    api.who["user"] = USER
    running = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story"}).json()
    assert api.delete(f"/api/creators/projects/{running['id']}").status_code == 409   # still editing
    store.finish(running["id"], "done", output_key="k")
    assert api.delete(f"/api/creators/projects/{first['id']}").json() == {"ok": True, "id": first["id"]}
    assert [p["id"] for p in api.get("/api/creators/projects").json()["projects"]] == [running["id"]]
    with db._conn() as conn:                                            # soft: the rows (and files) stay
        rows = conn.execute("SELECT id, deleted_at FROM creator_jobs WHERE root=?", (first["id"],)).fetchall()
    assert len(rows) == 2 and all(r["deleted_at"] for r in rows)
    assert api.get("/api/creators/me").json()["free_left"] == 0          # deleting never gives an edit back
    assert api.delete(f"/api/creators/projects/{first['id']}").status_code == 404


def test_a_project_learned_from_pasted_reels_says_whose_style(api):
    r = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "energy",
                                              "refs": ["https://www.tiktok.com/@minolee/video/7301234567890123456"]})
    job = r.json()
    p = api.get("/api/creators/projects").json()["projects"][0]
    assert p["state"] == "editing" and p["look_name"] == "@minolee's style"          # while it learns
    store.finish(job["id"], "done", output_key="k", result={"summary": {"style": {"name": "Your Mino Lee cut"}}})
    assert api.get("/api/creators/projects").json()["projects"][0]["look_name"] == "Your Mino Lee cut"


# ── the owner's Loom, 2026-10-30 (big-frontier/loom-2026-10-30/README.md, sections C, D, F) ──────────────────────

def test_the_learned_style_is_named_while_the_edit_runs(api):
    """P2-02: the editing screen said "In Cinematic story" while it learned the pasted Reel's style. The worker records
    the style's name as soon as it has it; until then the job says it has links and no name yet."""
    link = "https://www.instagram.com/reel/DeeFoOLirO1/"
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story", "refs": [link]}).json()
    assert job["refs"] == [link] and job["style_name"] == "" and job["expected_s"] == 7 * 60 + cj.STYLE_S
    store.claim(job["id"], "m1")
    store.set_style(job["id"], {"key": "ref-91baa18e64cd", "name": "Your Dylan Mouzakki cut", "from": "noir",
                                "label": "Dylan Mouzakki"})
    seen = api.get(f"/api/creators/jobs/{job['id']}").json()
    assert seen["style_name"] == seen["look_name"] == "Your Dylan Mouzakki cut"
    assert seen["expected_s"] == 7 * 60 and seen["result"] is None          # learned: no extra minutes, still running
    home = api.get("/api/creators/projects").json()["projects"][0]
    assert home["look_name"] == "Your Dylan Mouzakki cut"
    store.set_style(job["id"], {"error": "We couldn't open that link, so this edit is in the look you picked."})
    seen = api.get(f"/api/creators/jobs/{job['id']}").json()
    assert seen["style_name"] == "" and "couldn't open" in seen["style_error"] and seen["look_name"] == "Cinematic story"


def test_minutes_left_never_climb_and_a_long_render_still_moves_the_bar(api):
    """P2-04: the badge went 7, 6, 4, 5 … 10, 5, 2, 3, 4 minutes and the bar sat at 56% through a 3.5-minute render.
    The estimate is the usual time left unless the job's pace says it's ahead; past the usual time it says "almost"
    then "late", never more minutes. An active stage fills with its usual time."""
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story"}).json()
    store.claim(job["id"], "m1")
    now = time.time()
    stages = store.get(job["id"])["stages"]
    for s in stages[:4]:
        s["state"] = "done"
    stages[4].update(state="active", at=now - 150)                     # the render, 2.5 min in
    store.update(job["id"], stages=stages, progress=store.progress_of(stages), started_at=now - 200)
    seen = api.get(f"/api/creators/jobs/{job['id']}").json()
    stalled = store.progress_of(stages)
    assert stalled < 0.6 and seen["progress"] > stalled + 0.15          # the bar moved on its own
    j = store.get(job["id"])
    mins = []
    for elapsed in (200, 260, 330, 400, 440):                           # a slow edit: never more minutes
        j["started_at"] = now - elapsed
        mins.append(cj.eta(j, 7))
    numbers = [m for m, _ in mins if m is not None]
    assert numbers == sorted(numbers, reverse=True) and numbers[0] <= 4
    j["started_at"] = now - 7.2 * 60
    assert cj.eta(j, 7) == (None, "almost")
    j["started_at"] = now - 10 * 60
    assert cj.eta(j, 7) == (None, "late")
    home = api.get("/api/creators/projects").json()["projects"][0]
    assert "eta_note" in home


def test_a_tiktok_profile_rides_with_the_job_and_instagram_waits(api):
    """P1-23 / P2-01: B-roll is the editor's job. A TikTok profile is stored on the job, normalised (our machines can
    list and fetch it); an Instagram profile is refused plainly (Instagram turns our machines away), junk too."""
    def start(profile):
        return api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story", "profile": profile})

    r = start("https://www.instagram.com/dylan.mouzakki/")
    assert r.status_code == 400 and "coming soon" in r.json()["detail"]
    r = start("https://example.com/@me")
    assert r.status_code == 400 and "TikTok profile" in r.json()["detail"]
    job = start("tiktok.com/@ahormozi?lang=en").json()
    assert job["profile"] == "https://www.tiktok.com/@ahormozi" == store.get(job["id"])["profile"]
    store.finish(job["id"], "done")
    plain = start("").json()
    assert plain["profile"] == "" and store.get(plain["id"])["profile"] == ""


class _Resp:
    def __init__(self, text="", status=200, body=None, ctype="text/html", data=b""):
        self.text, self.status_code, self.ok, self._body = text, status, status < 400, body
        self.content, self.headers = data, {"Content-Type": ctype}

    def json(self):
        return self._body


def _jpeg(w, h):
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 40, 40)).save(out, "JPEG")
    return out.getvalue()


def test_a_pasted_reel_shows_its_picture_and_creator(api, monkeypatch):
    """P1-21 / P1-22: the pasted link showed a gradient tile. The preview reads each platform with one short request
    (TikTok and Instagram from the Reel's page, YouTube from its oEmbed) and returns the picture inline, cropped to a
    portrait; a platform that says nothing gives the plain card. Signed-in only, Reel links only."""
    import requests
    cj._previews.clear()
    tt_page = ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">' + json.dumps(
        {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {"itemStruct": {
            "desc": "Can your business afford   someone good?", "author": {"nickname": "Alex Hormozi", "uniqueId": "ahormozi"},
            "video": {"cover": "https://p16-sign.tiktokcdn-us.com/cover.jpeg"}}}}}}) + "</script>")
    ig_page = ('<meta property="og:title" content="Dylan Mouzakki on Instagram: &quot;I&#x2019;ve lost. I&#x2019;ve '
               'failed.&quot;" /><meta property="og:image" content="https://scontent.cdninstagram.com/v/x.jpg" />'
               '<meta property="og:url" content="https://www.instagram.com/dylan.mouzakki/reel/DeeFoOLirO1/" />')
    calls = []

    def get(url, params=None, headers=None, timeout=None, **kw):
        calls.append((url, timeout))
        if "tiktok.com/@" in url:
            return _Resp(tt_page)
        if "instagram.com/reel/" in url:
            return _Resp(ig_page)
        if "youtube.com/oembed" in url:
            return _Resp(body={"author_name": "Mino Lee", "author_url": "https://www.youtube.com/@mino.mp4",
                               "title": "this HOOK strategy", "thumbnail_url": "https://i.ytimg.com/vi/x/hqdefault.jpg"})
        if url.endswith(".jpg") or url.endswith(".jpeg"):
            return _Resp(ctype="image/jpeg", data=_jpeg(480, 360))
        return _Resp(status=404)
    monkeypatch.setattr(requests, "get", get)

    q = "/api/creators/reel-preview?url="
    tt = api.get(q + "https://www.tiktok.com/@ahormozi/video/7702450963327487246").json()
    assert tt["author"] == "Alex Hormozi" and tt["handle"] == "ahormozi" and tt["platform"] == "TikTok"
    assert tt["title"] == "Can your business afford someone good?" and tt["thumb"].startswith("data:image/jpeg;base64,")
    ig = api.get(q + "https://www.instagram.com/reel/DeeFoOLirO1/?utm_source=ig_web_copy_link").json()
    assert ig["author"] == "Dylan Mouzakki" and ig["handle"] == "dylan.mouzakki" and ig["title"].startswith("I’ve lost")
    yt = api.get(q + "https://www.youtube.com/shorts/aXwgg5Tj5Nc").json()
    assert yt["author"] == "Mino Lee" and yt["handle"] == "mino.mp4" and yt["thumb"]
    import base64
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(yt["thumb"].split(",", 1)[1])))
    assert im.size[1] > im.size[0]                                       # a landscape thumbnail comes back portrait
    assert all(t and t[1] <= 3 for _, t in calls)                       # short requests only
    n = len(calls)
    api.get(q + "https://www.youtube.com/shorts/aXwgg5Tj5Nc")
    assert len(calls) == n                                               # asked once
    # the platform said nothing (blocked, private): the plain card, never an error
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status=429))
    blank = api.get(q + "https://www.instagram.com/reel/C1kzUmsRcEl/")
    assert blank.status_code == 200 and blank.json()["thumb"] == "" and blank.json()["platform"] == "Instagram Reel"
    assert api.get(q + "https://example.com/x").status_code == 400
    api.who["user"] = None
    assert api.get(q + "https://www.youtube.com/shorts/aXwgg5Tj5Nc").status_code == 401


def test_titles_end_on_a_whole_thought():
    """Section H: the result page's title read "This 12 -minute video costs about $12 to make and"."""
    w = pytest.importorskip("webapp.creators_worker")   # the edit machines' image only: not in the public repo
    assert w.tidy_line("This 12 -minute video costs about $12 to make and") == \
        "This 12-minute video costs about $12 to make"
    assert w.tidy_line("I quit my job , and") == "I quit my job"
    assert w.tidy_line("Stop scrolling. If you") == "Stop scrolling."
    assert w.tidy_line("you need to see this") == "you need to see this"
    plan = {"hook": {"t": [0, 2], "text": "This 12 -minute video costs about $12 to make and"}, "title": "Why and"}
    s = w.summary({}, plan, 50.0, {"words": []})
    assert s["hook"] == "This 12-minute video costs about $12 to make" and s["beats"][0]["text"] == s["hook"]


def test_with_the_scrape_creators_key_instagram_profiles_open_and_previews_fall_back(api, monkeypatch):
    """Scrape Creators (the owner's key) lists an Instagram profile's posts on the machines, so the server takes
    Instagram profiles once SCRAPECREATORS_API_KEY is set, passes the key to the machine, and asks it for a Reel's
    preview only when Instagram's own page turned us away (a credit a call, the key only in the header)."""
    import requests
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "sk-test")
    assert api.get("/api/creators/config").json()["profiles"] == ["tiktok", "instagram"]
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story",
                                               "profile": "https://www.instagram.com/dylan.mouzakki/?hl=en"}).json()
    assert job["profile"] == "https://www.instagram.com/dylan.mouzakki"
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("SPACES_KEY", "k")
    assert cj.machine_env(job["id"])["SCRAPECREATORS_API_KEY"] == "sk-test"
    cj._previews.clear()
    calls = []

    def get(url, params=None, headers=None, timeout=None, **kw):
        calls.append((url, params, headers))
        if "instagram.com/reel/" in url:
            return _Resp(status=429)
        if url.endswith("/v1/instagram/post"):
            return _Resp(body={"data": {"xdt_shortcode_media": {
                "owner": {"username": "dylan.mouzakki", "full_name": "Dylan Mouzakki"},
                "thumbnail_src": "https://scontent.cdninstagram.com/v/t.heic",
                "display_url": "https://scontent.cdninstagram.com/v/d.jpg",
                "edge_media_to_caption": {"edges": [{"node": {"text": "I've lost."}}]}}}})
        if url.endswith(".heic"):
            return _Resp(ctype="image/heic", data=b"not a jpeg")
        return _Resp(ctype="image/jpeg", data=_jpeg(640, 640))
    monkeypatch.setattr(requests, "get", get)
    d = api.get("/api/creators/reel-preview?url=https://www.instagram.com/reel/DeeFoOLirO1/").json()
    assert d["author"] == "Dylan Mouzakki" and d["handle"] == "dylan.mouzakki" and d["thumb"].startswith("data:image/jpeg")
    sc = [c for c in calls if "scrapecreators" in c[0]]
    assert len(sc) == 1 and sc[0][1] == {"url": "https://www.instagram.com/reel/DeeFoOLirO1/"}
    assert sc[0][2] == {"x-api-key": "sk-test"}
    api.get("/api/creators/reel-preview?url=https://www.instagram.com/reel/DeeFoOLirO1/")
    assert len([c for c in calls if "scrapecreators" in c[0]]) == 1                # cached: one credit


def test_a_creators_profile_can_be_pasted_to_learn_their_style(api, monkeypatch):
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story",
                                               "refs": ["tiktok.com/@ahormozi"]}).json()
    assert job["refs"] == ["https://www.tiktok.com/@ahormozi"]
    store.finish(job["id"], "failed", error="x")
    r = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story",
                                             "refs": ["https://example.com/creator"]})
    assert r.status_code == 400 and "creator's TikTok or Instagram profile" in r.json()["detail"]
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "sk-test")                 # Instagram profiles open through it
    job = api.post("/api/creators/jobs", json={"take_key": upload(api)["key"], "look": "story",
                                               "refs": ["https://www.instagram.com/sunnywallacee/"]}).json()
    assert job["refs"] == ["https://www.instagram.com/sunnywallacee"]
    assert cj._ref_style(job["refs"]) == "@sunnywallacee's style"
