"""A stopped video carries on by itself when that is clearly free, or with one honest yes, and never pays twice
(cr_recover; customers ALEX and DeeDub, 2026-10-26)."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from flask import Flask, jsonify, request
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_claude
import cr_recover as recover

TOKEN = "synthetic-recover-token"
BASE = "http://127.0.0.1:7860"
PROJECT = "crv_" + "1" * 24
S1, S2 = "cc_" + "a" * 64, "cc_" + "b" * 64
NARRATION = "google/gemini-3-8-flash-tts"
PICTURE = "google/nano-banana"


def stage(sid, status="complete", code="", attempts=1, kind="text", role="general", minutes=0.0):
    return {"stage_id": sid, "status": status, "error_code": code, "attempts": attempts, "kind": kind,
            "model_role": role, "can_retry": status in ("failed", "authorized") and attempts < 3,
            "usage": {"duration_seconds": minutes * 60}}


def cache(home, sid, state, code="", project=PROJECT):
    folder = Path(home) / "output" / "cr-claude-cache" / project
    folder.mkdir(parents=True, exist_ok=True)
    data = {"state": state, "input_fingerprint": sid[3:]}
    if code:
        data["error_code"] = code
    if state.startswith("complete"):
        data.update(stdout="x" * 2000, result_fingerprint="f" * 64)
    (folder / (sid + ".json")).write_text(json.dumps(data), encoding="utf-8")


def make_job(home, slug="the-vault--abcd1234", title="The Vault", style="stories", minutes=3, extra="", status="ready"):
    job = Path(home) / "output" / style / "_work" / slug
    job.mkdir(parents=True, exist_ok=True)
    (job / "cr-project.json").write_text(json.dumps({"id": PROJECT, "brain_mode": "claudecode", "status": status,
        "title": title, "job_spec": {"title": title, "style": style, "minutes": minutes, "extra": extra}}),
        encoding="utf-8")
    (job / "title.txt").write_text(title + "\n", encoding="utf-8")
    return job


def receipt(job, folder, name, **data):
    tasks = Path(job) / folder / ".cr-tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    path = tasks / name
    path.write_text(json.dumps({"provider": "kie", **data}), encoding="utf-8")
    return path


def closed_run(job, *, ago=60, pid=1):
    """A run whose Studio closed: started, never ended, by another Studio."""
    started = time.time() - ago
    (Path(job) / recover.RUN_FILE).write_text(json.dumps({"started": started, "studio_pid": pid, "run_id": ""}),
                                              encoding="utf-8")
    os.utime(Path(job) / recover.RUN_FILE, (started, started))


def ended_run(job, state="error", category="crash", provider="none", ago=60):
    now = time.time()
    (Path(job) / recover.RUN_FILE).write_text(json.dumps({"started": now - ago - 30, "studio_pid": os.getpid(),
        "ended": now - ago, "state": state, "category": category, "provider": provider}), encoding="utf-8")


# ── what stopped it ───────────────────────────────────────────────────────────────────────────────────────────

def test_a_failed_claude_step_is_named_with_its_reason_cost_and_reset(tmp_path):
    job = make_job(tmp_path)
    cache(tmp_path, S1, "failed", "usage_limit")
    reset = {"reset_at": time.time() + 3600, "reset_label": "26 Oct, 03:00 PM (Europe/London)"}
    d = recover.diagnose(tmp_path, job, server_stages=[stage(S2), stage(S1, "failed", "usage_limit", kind="vision",
                                                                       role="visual")], allowance=reset)
    assert d["stopped_on"] == "claude_stage" and d["needs_approval"] and d["key"]
    [s] = d["claude_stages"]
    assert s["stage_id"] == S1 and s["error_code"] == "usage_limit" and s["spent"] == "unclear"
    assert s["may_have_spent"] and s["step"] == "a Claude step that looks at pictures (Opus)"
    assert s["reset_label"].startswith("26 Oct") and s["reset_passed"] is False
    assert "usage resets at 26 Oct" in d["what_to_tell_them"] and "Nothing runs again unless you say yes" in \
        d["what_to_tell_them"]
    assert not d["free_to_resume"]


def test_a_step_claude_finished_here_is_not_stuck_and_a_saved_failure_is(tmp_path):
    """A lost finish receipt (complete_local) is synced by the resume, never run again; a failure saved here but not
    yet on ChannelRecipe (failed_local) is a failure."""
    job = make_job(tmp_path)
    cache(tmp_path, S1, "complete_local")
    d = recover.diagnose(tmp_path, job, server_stages=[stage(S1, "authorized")])
    assert d["claude_stages"] == [] and d["free_to_resume"] and not d["needs_approval"]
    cache(tmp_path, S2, "failed_local", "timeout")
    d = recover.diagnose(tmp_path, job, server_stages=[stage(S1, "authorized"), stage(S2, "authorized")])
    assert [(s["stage_id"], s["status"], s["error_code"], s["spent"]) for s in d["claude_stages"]] == [
        (S2, "failed", "timeout", "yes")]


def test_a_step_cut_off_mid_way_says_usage_may_have_gone_on_it(tmp_path):
    job = make_job(tmp_path)
    cache(tmp_path, S1, "running")
    d = recover.diagnose(tmp_path, job, server_stages=[stage(S1, "authorized")])
    [s] = d["claude_stages"]
    assert s["status"] == "authorized" and "cut off" in s["reason"] and s["spent"] == "maybe" and s["needs_approval"]
    # ChannelRecipe out of reach: this computer's caches still tell it
    d = recover.diagnose(tmp_path, job, server_stages=None)
    assert [x["stage_id"] for x in d["claude_stages"]] == [S1] and d["claude_stages"][0]["needs_approval"]


def test_a_step_with_no_attempts_left_says_so_and_is_not_offered(tmp_path):
    job = make_job(tmp_path)
    d = recover.diagnose(tmp_path, job, server_stages=[stage(S1, "failed", "cli_failed", attempts=3)])
    assert not d["needs_approval"] and "every attempt" in d["fix_first"][0] and not d["free_to_resume"]
    signed_out = recover.diagnose(tmp_path, job, server_stages=[stage(S1, "failed", "auth_missing")],
                                  claude_ready=False)
    assert "Sign in to Claude Code" in signed_out["fix_first"][0] and not signed_out["needs_approval"]


def test_media_tasks_are_told_apart_uncertain_checking_free_and_charged(tmp_path):
    job = make_job(tmp_path)
    ended_run(job, category="provider_error", provider="kie")
    run = json.loads((job / recover.RUN_FILE).read_text())
    uncertain = receipt(job, "motion", "u.json", model="bytedance/v1-pro-fast-image-to-video", status="submitting")
    receipt(job, "images", "p.json", model=PICTURE, status="pending", id="t1")
    receipt(job, "audio-hub-voice", "f.json", model=NARRATION, status="failed", id="t2", credits=0,
            failure_code="500", failure_message="Internal Error, please try again later")
    receipt(job, "motion", "c.json", model="bytedance/v1-pro-fast-image-to-video", status="failed", id="t3",
            credits=40, failure_code="501", failure_message="generation failed")
    receipt(job, "images", "done.json", model=PICTURE, status="complete", id="t4")
    receipt(job, "images", "up-kie-abc.json", url="https://x", created_at=1)
    receipt(job, "images", "x.uncertain-1.json", model=PICTURE, status="submitting")
    old = receipt(job, "motion", "old.json", model="bytedance/v1-pro-fast-image-to-video", status="failed", id="t5",
                  credits=40)
    os.utime(old, (run["started"] - 3600, run["started"] - 3600))
    d = recover.diagnose(tmp_path, job, server_stages=[])
    rows = {r["receipt"]: r for r in d["media"]}
    assert set(rows) == {"motion/.cr-tasks/u.json", "images/.cr-tasks/p.json", "audio-hub-voice/.cr-tasks/f.json",
                         "motion/.cr-tasks/c.json"}, "complete, uploads, set aside and older failures left out"
    assert rows["motion/.cr-tasks/u.json"]["state"] == "uncertain" and rows["motion/.cr-tasks/u.json"]["pays_twice"]
    assert rows["images/.cr-tasks/p.json"]["state"] == "checking" and not rows["images/.cr-tasks/p.json"]["needs_approval"]
    assert rows["audio-hub-voice/.cr-tasks/f.json"]["free"] and not rows["audio-hub-voice/.cr-tasks/f.json"]["pays_twice"]
    assert rows["motion/.cr-tasks/c.json"]["needs_approval"] and "charged 40 credits" in rows["motion/.cr-tasks/c.json"]["reason"]
    assert d["stopped_on"] == "media" and d["needs_approval"] and uncertain.is_file()


def test_a_skipped_pictures_failure_is_not_offered_again_unless_a_picture_stopped_it(tmp_path):
    job = make_job(tmp_path)
    receipt(job, "images", "a.json", model=PICTURE, status="failed", id="t1", credits=None, failure_code="501")
    assert recover.diagnose(tmp_path, job, server_stages=[])["media"] == []
    d = recover.diagnose(tmp_path, job, server_stages=[], message="Kie.ai failed this images task. generation failed")
    assert [r["task"] for r in d["media"]] == ["images"] and d["media"][0]["needs_approval"]


def test_a_rejected_task_charged_nothing_and_says_what_to_fix(tmp_path):
    job = make_job(tmp_path)
    receipt(job, "audio-hub-voice", "r.json", model=NARRATION, status="failed", rejected=True, failure_code="402",
            failure_category="balance", credits=0)
    (job / "audio-hub-voice" / ".cr-tasks" / "r.json").touch()
    import cr_media
    (job / cr_media.VOICE_FAILED).unlink(missing_ok=True)
    d = recover.diagnose(tmp_path, job, server_stages=[])
    [r] = d["media"]
    assert not r["pays_twice"] and not r["needs_approval"] and r["do_first"].startswith("Top up your Kie.ai")
    assert d["do_first"] and not d["fix_first"] and not d["needs_approval"] and d["free_to_resume"]
    assert d["what_to_tell_them"].startswith("Top up your Kie.ai")


def test_the_voice_and_presenter_choices_keep_their_own_tasks(tmp_path):
    import cr_media
    job = make_job(tmp_path)
    receipt(job, "audio-hub-voice", "f.json", model=NARRATION, status="submitting")
    (job / cr_media.VOICE_FAILED).write_text("{}", encoding="utf-8")
    assert recover.diagnose(tmp_path, job, server_stages=[])["media"] == []


# ── the creator's yes ─────────────────────────────────────────────────────────────────────────────────────────

class Bridge:
    def __init__(self, stages=None, refuse=()):
        self.stages, self.refuse, self.calls = list(stages or []), set(refuse), []

    def _mcp_tool(self, name, payload):
        self.calls.append((name, payload))
        if payload.get("stage_id") in self.refuse:
            raise RuntimeError("409")
        for s in self.stages:
            if s["stage_id"] == payload.get("stage_id"):
                s.update(status="retry_allowed", can_retry=False)
        return {"approved": True}

    def project_progress(self, project_id):
        return {"stages": [dict(s) for s in self.stages], "project": {"status": "failed"}}


def test_approve_allows_exactly_the_listed_attempts_and_keeps_set_aside_receipts(tmp_path):
    job = make_job(tmp_path)
    uncertain = receipt(job, "motion", "u.json", model="bytedance/v1-pro-fast-image-to-video", status="submitting")
    bridge = Bridge([stage(S1, "failed", "timeout"), stage(S2)])
    d = recover.diagnose(tmp_path, job, server_stages=bridge.project_progress(PROJECT)["stages"])
    done = recover.approve(bridge, job, d, key=d["key"])
    assert bridge.calls == [("local_studio_retry_stage", {"project_id": PROJECT, "stage_id": S1,
                                                          "acknowledge_costs": True})]
    assert done["approved_stages"] == [S1] and not uncertain.exists()
    kept = list((job / "motion" / ".cr-tasks").glob("u.uncertain-*.json"))
    assert len(kept) == 1 and json.loads(kept[0].read_text())["status"] == "submitting", "kept, never deleted"
    assert recover.diagnose(tmp_path, job, server_stages=bridge.project_progress(PROJECT)["stages"])["media"] == []


def test_a_changed_picture_or_a_fix_first_approves_nothing(tmp_path):
    job = make_job(tmp_path)
    bridge = Bridge([stage(S1, "failed", "timeout")])
    d = recover.diagnose(tmp_path, job, server_stages=bridge.stages)
    with pytest.raises(recover.Refused, match="changed"):
        recover.approve(bridge, job, d, key="0" * 12)
    signed_out = recover.diagnose(tmp_path, job, server_stages=[stage(S1, "failed", "auth_missing")],
                                  claude_ready=False)
    with pytest.raises(recover.Refused, match="Sign in"):
        recover.approve(bridge, job, signed_out)
    assert bridge.calls == []


def test_a_refused_attempt_stops_before_any_receipt_is_touched(tmp_path):
    job = make_job(tmp_path)
    uncertain = receipt(job, "motion", "u.json", model="bytedance/v1-pro-fast-image-to-video", status="submitting")
    bridge = Bridge([stage(S1, "failed", "timeout"), stage(S2, "failed", "timeout")], refuse={S2})
    d = recover.diagnose(tmp_path, job, server_stages=bridge.stages)
    with pytest.raises(recover.Refused) as refused:
        recover.approve(bridge, job, d, key=d["key"])
    assert refused.value.data["approved"] == [S1] and uncertain.exists()


def test_a_receipt_outside_the_video_is_never_moved(tmp_path):
    job = make_job(tmp_path)
    other = receipt(tmp_path / "elsewhere", "images", "x.json", status="submitting")
    with pytest.raises(recover.Refused):
        recover.set_aside(job, "../../../../elsewhere/images/.cr-tasks/x.json", "uncertain")
    assert other.exists()


# ── carrying on by itself ─────────────────────────────────────────────────────────────────────────────────────

def test_studio_closed_mid_run_carries_on_by_itself_when_nothing_is_uncertain(tmp_path):
    job = make_job(tmp_path)
    closed_run(job)
    cache(tmp_path, S1, "complete")
    ok, why = recover.safe_to_resume(job, home=tmp_path, server_stages=[stage(S1)])
    assert ok and why == "Studio closed while it was being made"
    assert recover.safe_to_resume(job, home=tmp_path, server_stages=None) == (False, recover.UNCHECKED)
    assert recover.safe_to_resume(job, home=tmp_path, server_stages=[stage(S1)], live="running")[0] is False


@pytest.mark.parametrize("why", ["cut", "uncertain", "failed", "approved", "cancelled", "twice", "old", "provider",
                                 "here"])
def test_anything_uncertain_or_chosen_waits_for_the_creator(tmp_path, why):
    job = make_job(tmp_path)
    closed_run(job)
    stages = [stage(S1)]
    if why == "cut":
        cache(tmp_path, S1, "running")
        stages = [stage(S1, "authorized")]
    elif why == "uncertain":
        receipt(job, "images", "u.json", model=PICTURE, status="submitting")
    elif why == "failed":
        stages = [stage(S1, "failed", "timeout")]
    elif why == "approved":
        stages = [stage(S1, "retry_allowed", "timeout")]
    elif why == "cancelled":
        ended_run(job, state="cancelled", category="cancelled")
    elif why == "twice":
        (job / recover.AUTO_FILE).write_text(json.dumps({"count": recover.AUTO_LIMIT}))
    elif why == "old":
        closed_run(job, ago=recover.AUTO_WINDOW + 60)
    elif why == "provider":
        ended_run(job, category="provider_error", provider="kie")
    elif why == "here":
        closed_run(job, pid=os.getpid())                       # this Studio's own run, still finishing
    ok, said = recover.safe_to_resume(job, home=tmp_path, server_stages=stages)
    assert ok is False and said


@pytest.mark.parametrize("category", ["crash", "out_of_memory", "tool_error"])
def test_a_local_crash_carries_on(tmp_path, category):
    job = make_job(tmp_path)
    ended_run(job, category=category)
    assert recover.safe_to_resume(job, home=tmp_path, server_stages=[stage(S1)])[0]


def test_a_usage_limit_stop_carries_on_only_by_the_creators_choice_after_the_reset(tmp_path):
    job = make_job(tmp_path)
    ended_run(job, category="usage_limit", provider="claude")
    stages = [stage(S1), stage(S2, "failed", "usage_limit")]
    past = {"reset_at": time.time() - recover.USAGE_MARGIN - 5, "reset_label": "earlier"}
    ahead = {"reset_at": time.time() + 600, "reset_label": "later"}
    assert not recover.safe_to_resume(job, home=tmp_path, server_stages=stages, allowance=past)[0], "default OFF"
    (job / recover.CARRY_ON).write_text(json.dumps({"after_usage_reset": True}))
    assert not recover.safe_to_resume(job, home=tmp_path, server_stages=stages, allowance=ahead)[0]
    assert not recover.safe_to_resume(job, home=tmp_path, server_stages=stages, allowance={})[0], "reset unknown"
    plan = recover._auto_plan(job, home=tmp_path, server_stages=stages, allowance=past)
    assert plan["safe"] and plan["approve"] == [S2]
    # one more Claude failure of another kind still waits for the creator
    assert not recover.safe_to_resume(job, home=tmp_path, server_stages=stages + [stage("cc_" + "c" * 64, "failed",
                                                                                         "timeout")],
                                      allowance=past)[0]


def test_run_markers_tell_a_closed_studio_from_a_stop_here(tmp_path):
    job = make_job(tmp_path)
    recover.run_started(job, {"cr_video_run_id": "video_" + "e" * 24, "cr_carry_on_after_reset": True})
    assert recover.last_stop(job, tmp_path)["kind"] == "interrupted_here" and recover.carry_on(job)
    assert recover.last_stop(job, tmp_path, studio_pid=1)["category"] == "studio_closed"
    recover.run_ended(job, {"state": "error"}, {"category": "out_of_memory", "failed_step": "render"})
    stop = recover.last_stop(job, tmp_path)
    assert stop["kind"] == "local" and stop["category"] == "out_of_memory" and stop["step"] == "render"
    recover.run_started(job, {})                               # a resume without the choice keeps the creator's choice
    assert recover.carry_on(job)


def test_a_stale_running_status_is_a_stopped_video():
    studio = SimpleNamespace(JOBS={"live--abc": {"state": "running"}}, RUNNING={"slug": None}, QUEUE=[{"slug": "q--a"}],
                             LOCK=threading.Lock())
    assert recover.effective_state(studio, "gone--abc", {"status": "running"}) == "stopped"
    assert recover.effective_state(studio, "live--abc", {"status": "ready"}) == "running"
    assert recover.effective_state(studio, "q--a", {"status": "ready"}) == "queued"
    assert recover.effective_state(studio, "gone--abc", {"status": "failed"}) == "failed"


# ── the same video again ──────────────────────────────────────────────────────────────────────────────────────

def engine_for(*jobs):
    return SimpleNamespace(list_jobs=lambda: list(jobs), final_video=lambda job: Path(job) / "video.mp4",
                           find_job=lambda slug: next((j for j in jobs if j.name == slug), None))


def test_the_same_title_style_and_minutes_find_the_stopped_video(tmp_path):
    job = make_job(tmp_path, title="The Vault")
    other = make_job(tmp_path, slug="done--abcd1234", title="Done")
    (other / "video.mp4").write_bytes(b"done")
    studio = SimpleNamespace(mv=engine_for(job, other), JOBS={})
    [m] = recover.twins(studio, ["  the   vault ", "Done", "New one"], "stories", 3)
    assert m == {"title": "the   vault", "slug": job.name, "id": "video:" + job.name, "project_id": PROJECT,
                 "stopped": m["stopped"]}
    assert recover.twins(studio, ["The Vault"], "stories", 4) == [], "other minutes: another video"
    assert recover.twins(studio, ["The Vault"], "documentary", 3) == []
    studio.JOBS[job.name] = {"state": "running"}
    assert recover.twins(studio, ["The Vault"], "stories", 3) == [], "a video being made isn't stopped"


def test_a_ranking_short_is_the_same_only_with_the_same_countdown(tmp_path):
    extra = '[[short_seconds=25]] [[ranking={"ranks":5,"booms":true}]]'
    job = make_job(tmp_path, title="Funniest dogs", style="cr-shorts-ranking", minutes=1, extra=extra)
    studio = SimpleNamespace(mv=engine_for(job), JOBS={})
    assert recover.twins(studio, ["Funniest dogs"], "cr-shorts-ranking", 1,
                         extra='[[short_seconds=25]] [[ranking={"booms": true, "ranks": 5}]]')
    assert not recover.twins(studio, ["Funniest dogs"], "cr-shorts-ranking", 1,
                             extra='[[ranking={"ranks":7,"booms":true}]]')


def test_create_offers_resume_first_and_starts_new_only_when_insisted(tmp_path, monkeypatch):
    import cr_submission
    job = make_job(tmp_path, title="The Vault")
    app = Flask(__name__)
    seen = []

    @app.post("/api/run")
    def api_run():
        return jsonify(queued=[])

    studio = SimpleNamespace(app=app, mv=engine_for(job), JOBS={})
    monkeypatch.setattr(cr_submission, "_publish", lambda studio, payload, original, **kw: seen.append(payload) or
                        jsonify(queued=[{"slug": "new"}]))
    cr_submission.install(studio)
    client = app.test_client()
    body = {"title": "The Vault\nAnother one", "style": "stories", "minutes": 3}
    answer = client.post("/api/run", json=body)
    assert answer.status_code == 409 and seen == []
    data = answer.get_json()
    assert data["resume_first"][0]["slug"] == job.name and data["others"] == ["Another one"]
    assert "Resume" in data["error"] and "pays again" in data["error"]
    assert client.post("/api/run", json={**body, "minutes": 5}).status_code == 200
    assert client.post("/api/run", json={**body, "new_attempt": True}).status_code == 200
    assert "new_attempt" not in seen[-1], "the engine never sees the choice"


# ── the routes and the watcher ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def studio_env(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {"ready": True, "allowance": {}})
    monkeypatch.setattr(cr_claude, "sync_receipts", lambda bridge: 0)
    import cr_claude_code
    monkeypatch.setattr(cr_claude_code, "notify", lambda *a, **k: True)
    app = Flask(__name__)
    submitted = []
    lock = threading.RLock()

    @app.before_request
    def guard():
        if request.method == "POST" and request.headers.get("X-CR-Local-Token") != TOKEN:
            return jsonify(error="token"), 403

    @app.post("/cr-submit-project")
    def submit():
        if env.refuse_submit:
            return jsonify(error=env.refuse_submit), 409
        submitted.append(request.get_json()["project_id"])
        return jsonify(queued=[{"slug": "the-vault--abcd1234"}], project_id=request.get_json()["project_id"])

    job = make_job(tmp_path)
    bridge = Bridge([stage(S1, "failed", "timeout"), stage(S2)])
    busy = {"on": False}
    activity = SimpleNamespace(lock=lock, busy=lambda: busy["on"])
    studio = SimpleNamespace(app=app, mv=engine_for(job), JOBS={}, RUNNING={"slug": None}, QUEUE=[],
                             LOCK=threading.Lock())
    watcher = recover.install(studio, activity, token=TOKEN, home=tmp_path, bridge=bridge, start_watcher=False)
    env = SimpleNamespace(app=app, client=app.test_client(), job=job, bridge=bridge, busy=busy, submitted=submitted,
                          studio=studio, watcher=watcher, home=tmp_path, refuse_submit="")
    return env


def post(env, slug, body):
    return env.client.post("/cr-workspace/recovery/" + slug, json=body, base_url=BASE,
                           headers={"X-CR-Local-Token": TOKEN})


def test_the_recovery_route_tells_and_the_yes_approves_then_resumes(studio_env):
    env = studio_env
    got = env.client.get("/cr-workspace/recovery/" + env.job.name, base_url=BASE).get_json()
    assert got["stopped_on"] == "claude_stage" and got["key"] and env.bridge.calls == []
    assert got["auto"]["safe"] is False and env.submitted == []
    assert post(env, env.job.name, {"key": got["key"]}).status_code == 400, "the yes must be explicit"
    assert env.bridge.calls == []
    answer = post(env, env.job.name, {"acknowledge_costs": True, "key": got["key"]})
    assert answer.status_code == 200 and answer.get_json()["resumed"]
    assert [c[1]["stage_id"] for c in env.bridge.calls] == [S1] and env.submitted == [PROJECT]


def test_a_stale_key_or_a_video_being_made_approves_nothing(studio_env):
    env = studio_env
    answer = post(env, env.job.name, {"acknowledge_costs": True, "key": "0" * 12})
    assert answer.status_code == 409 and "changed" in answer.get_json()["error"] and answer.get_json()["recovery"]
    env.studio.JOBS[env.job.name] = {"state": "running"}
    assert post(env, env.job.name, {"acknowledge_costs": True}).status_code == 409
    assert env.bridge.calls == [] and env.submitted == []


def test_a_yes_while_studio_is_busy_carries_on_when_it_is_free(studio_env):
    env = studio_env
    env.busy["on"] = True
    answer = post(env, env.job.name, {"acknowledge_costs": True}).get_json()
    assert answer["waiting"] and not answer["resumed"] and env.submitted == []
    assert (env.job / recover.PENDING).is_file()
    assert env.watcher.tick() is None, "still busy"
    env.busy["on"] = False
    assert env.watcher.tick() == (env.job.name, "you approved it while Studio was busy")
    assert env.submitted == [PROJECT] and not (env.job / recover.PENDING).exists()


def test_a_usage_limit_stop_can_wait_for_the_reset_by_the_creators_choice(studio_env, monkeypatch):
    env = studio_env
    env.bridge.stages[:] = [stage(S1, "failed", "usage_limit"), stage(S2)]
    ahead = {"reset_at": time.time() + 3600, "reset_label": "4 PM"}
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {"ready": True, "allowance": ahead})
    early = post(env, env.job.name, {"acknowledge_costs": True})
    assert early.status_code == 409 and "4 PM" in early.get_json()["error"] and env.bridge.calls == []
    later = post(env, env.job.name, {"acknowledge_costs": True, "when": "after_reset"}).get_json()
    assert later["scheduled"] and recover.carry_on(env.job) and env.bridge.calls == [] and env.submitted == []
    now = post(env, env.job.name, {"acknowledge_costs": True, "added_usage": True})
    assert now.status_code == 200 and env.submitted == [PROJECT]


def test_the_watcher_carries_on_a_safe_video_and_backs_off_when_refused(studio_env):
    env = studio_env
    env.bridge.stages[:] = [stage(S1), stage(S2)]
    closed_run(env.job)
    env.refuse_submit = "Complete this project's setup before starting."
    assert env.watcher.tick() is None
    record = recover.auto_record(env.job)
    assert record["count"] == 0 and "setup" in record["refused"]
    assert env.watcher.tick() is None and env.submitted == [], "backs off"
    record["last_try"] = time.time() - recover.RETRY_BACKOFF - 1
    (env.job / recover.AUTO_FILE).write_text(json.dumps(record))
    env.refuse_submit = ""
    assert env.watcher.tick() == (env.job.name, "Studio closed while it was being made")
    assert env.submitted == [PROJECT] and recover.auto_record(env.job)["count"] == 1
    assert env.bridge.calls == [], "nothing was approved: nothing needed it"


def test_the_watcher_never_approves_a_failed_step_and_stops_after_two(studio_env):
    env = studio_env
    closed_run(env.job)
    assert env.watcher.tick() is None and env.bridge.calls == [] and env.submitted == []
    env.bridge.stages[:] = [stage(S1)]
    (env.job / recover.AUTO_FILE).write_text(json.dumps({"count": recover.AUTO_LIMIT}))
    assert env.watcher.tick() is None and env.submitted == []


def test_the_watcher_approves_a_usage_limit_refusal_only_by_choice_after_the_reset(studio_env, monkeypatch):
    env = studio_env
    ended_run(env.job, category="usage_limit", provider="claude")
    env.bridge.stages[:] = [stage(S1, "failed", "usage_limit"), stage(S2)]
    past = {"reset_at": time.time() - recover.USAGE_MARGIN - 5, "reset_label": "earlier"}
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {"ready": True, "allowance": past})
    assert env.watcher.tick() is None and env.bridge.calls == [], "not chosen"
    (env.job / recover.CARRY_ON).write_text(json.dumps({"after_usage_reset": True}))
    assert env.watcher.tick() == (env.job.name, "Claude's usage reset, as you chose")
    assert [c[1]["stage_id"] for c in env.bridge.calls] == [S1] and env.submitted == [PROJECT]
