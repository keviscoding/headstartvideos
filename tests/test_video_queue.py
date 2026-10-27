"""Several videos queue while one is made (Studio 106, customer ALEX: "i cnt even make 2 or 3 vids in parallel... i have
to sit and wait"). Create, Resume and make_videos join the queue instead of "Finish your current video"; each queued
video keeps its own setup proof; a stop every waiting video would hit too (cr_failure categories) holds the rest, with
Carry on checking each one's setup again; a stop of one video's own never does."""
import itertools
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
sys.path.insert(0, str(Path(__file__).parent))
import cr_bridge
import cr_failure
import cr_machine
import cr_queue
import cr_studio
import cr_submission
from test_frontier_submission import studio_with_original

PROJECT = "crv_" + "a" * 24


def spec(title, *, ranking=False, **more):
    slug = title.lower().replace(" ", "-") + "--run"
    return {"slug": slug, "title": title, "style": "history", "steps": ["script", "video"], "minutes": 10,
            "extra": '[[ranking={"ranks": 5}]]' if ranking else "", "mix": (), "meta": None, "features": None,
            "cr_video_run_id": "run_" + slug.replace("-", "_"), **more}


def fake_studio(queue=(), running=None):
    dispatched = []
    studio = SimpleNamespace(app=Flask("video-queue"), QUEUE=list(queue), LOCK=threading.Lock(), JOBS={},
                             RUNNING={"slug": running})
    studio._ensure_dispatcher = lambda: dispatched.append(True)
    for s in studio.QUEUE:
        studio.JOBS[s["slug"]] = {"state": "queued", "title": s["title"], "done": False}
    return studio, dispatched


def stopped(keeper, studio, video, error, tmp_path, **state):
    """`video` ended with `error` (as JOBS holds it); the keeper's after-video hook runs."""
    job = tmp_path / ("job-" + video["slug"])
    job.mkdir(parents=True, exist_ok=True)
    studio.JOBS[video["slug"]] = {"state": "error", "error": error, "done": True, "title": video["title"],
                                  "started": time.time() - 60, "lines": [], **state}
    keeper.started(video)
    keeper.finished(video, lambda: job)
    return job


# ── 1. queue while busy ────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    """A Studio (cr_studio.create_app) over a stand-in engine whose api_run is shaped like Frontier's, with the real
    cr_submission and cr_queue. Each setup check gives a new proof, so the test sees whose proof a spec carries."""
    app = Flask("queue-runtime")
    studio = SimpleNamespace(app=app, mv=SimpleNamespace(), styles=SimpleNamespace(STYLES={}), RUNNING={"slug": None},
                             QUEUE=[], JOBS={}, HISTORY=[], LOCK=threading.Lock(), PRODUCT_BUILD={}, FARM={},
                             timeline=None, _run_spec=lambda spec: None)
    studio._ensure_dispatcher = lambda: None
    namespace = {"request": request, "jsonify": jsonify, "QUEUE": studio.QUEUE, "JOBS": studio.JOBS,
                 "LOCK": studio.LOCK, "_ensure_dispatcher": lambda: studio._ensure_dispatcher()}
    exec('''
def api_run():
    data = request.get_json(force=True)
    queued = []
    for title in data["title"].splitlines():
        slug = title.strip().lower().replace(" ", "-")
        JOBS[slug] = {"state": "queued", "title": title}
        with LOCK:
            QUEUE.append({"slug": slug, "title": title, "steps": ["video"], "style": data.get("style", "history"),
                          "minutes": data.get("minutes", 10)})
        queued.append({"slug": slug, "title": title})
    _ensure_dispatcher()
    return jsonify(queued=queued)
''', namespace)
    app.add_url_rule("/api/run", "api_run", namespace["api_run"], methods=["POST"])

    @app.get("/")
    def original_page():
        return "Original engine page"

    @app.get("/api/channels")
    def original_channels():
        return jsonify(channels=[], features=[], caps={}, prices={}, dlc={})

    @app.get("/api/queue")
    def api_queue():                                           # as Frontier's app.py answers it
        with studio.LOCK:
            pending = [{"slug": s["slug"], "title": s["title"], "state": "queued"} for s in studio.QUEUE]
            running = studio.RUNNING["slug"]
        out = ([{"slug": running, "title": running, "state": "running"}] if running else []) + pending
        out += [{"slug": s, "title": s, "state": (studio.JOBS.get(s) or {}).get("state", "done")} for s in studio.HISTORY]
        return jsonify(jobs=out, busy=bool(running))

    cfg = {"brain_mode": "channelrecipe", "costs_accepted": True, "key": "cr_mcp_fixture"}
    proofs = itertools.count(1)
    monkeypatch.setitem(sys.modules, "app", studio)
    monkeypatch.setattr(cr_studio, "HOME", tmp_path)
    monkeypatch.setattr(cr_studio, "configure_video_tools", lambda: None)
    monkeypatch.setattr(cr_studio, "graphics_ready", lambda: {"browser": True, "webgl": True})
    monkeypatch.setattr(cr_studio, "video_tools_ready", lambda: True)
    monkeypatch.setattr(cr_studio, "tool_ready", lambda *a: True)
    monkeypatch.setattr(cr_studio.cr_bridge, "load_settings", lambda: dict(cfg))
    monkeypatch.setattr(cr_studio.cr_bridge, "save_settings", lambda values: cfg.update(values))
    monkeypatch.setattr(cr_studio.cr_bridge, "call", lambda *a, **k: {"version": "fixture"})
    monkeypatch.setattr(cr_studio.cr_bridge, "prepare_runtime", lambda *a: {})
    monkeypatch.setattr(cr_studio.cr_claude, "sync_receipts", lambda *a: None)
    for adapter in (cr_studio.cr_bridge, cr_studio.cr_media, cr_studio.cr_capabilities,
                    cr_studio.cr_render, cr_studio.cr_workers):
        monkeypatch.setattr(adapter, "install", lambda *a: None)
    monkeypatch.setattr(cr_studio.cr_media, "profile", lambda *a: {})
    monkeypatch.setattr(cr_studio.cr_media, "active", lambda *a: False)
    monkeypatch.setattr(cr_studio.cr_workers, "busy", lambda: False)
    monkeypatch.setattr(cr_studio.cr_readiness, "basic", lambda checks: {
        "ready": True, "checks": [], "blockers": [], "brain_mode": cfg["brain_mode"], "claude_code": {}})
    asked = []
    monkeypatch.setattr(cr_studio.cr_readiness, "preset", lambda *a: asked.append(a) or {
        "ready": True, "proof": {"check": next(proofs)}})
    cr_studio.create_app()
    token = json.loads((tmp_path / ".cr-runtime.json").read_text())["token"]
    headers = {"Host": "127.0.0.1:7860", "X-CR-Local-Token": token}
    return SimpleNamespace(studio=studio, client=app.test_client(), headers=headers, cfg=cfg, home=tmp_path,
                           asked=asked, keeper=studio._cr_queue)


def test_a_create_while_a_video_is_made_joins_the_queue_with_its_own_setup_proof(env):
    env.studio.RUNNING["slug"] = "the-one-being-made"
    assert env.studio._cr_activity.busy()
    first = env.client.post("/api/run", headers=env.headers, json={"title": "Rome falls", "steps": ["video"]})
    second = env.client.post("/api/run", headers=env.headers,
                             json={"title": "Moon landing\nTitanic", "steps": ["video"], "style": "cr-3d"})
    assert first.status_code == 200 and second.status_code == 200, (first.get_json(), second.get_json())
    rows = first.get_json()["queued"] + second.get_json()["queued"]
    assert [(r["position"], r["ahead"]) for r in rows] == [(1, 1), (2, 2), (3, 3)]
    assert [s["title"] for s in env.studio.QUEUE] == ["Rome falls", "Moon landing", "Titanic"]
    # each keeps the proof of its own check: the second submission never changed the first one's
    assert [s["cr_setup_proof"] for s in env.studio.QUEUE] == [{"check": 1}, {"check": 2}, {"check": 2}]
    assert env.cfg["setup_proof"] == {"check": 2}
    saved = json.loads((env.home / "output" / ".cr-queue.json").read_text())
    assert [s["title"] for s in saved["waiting"]] == ["Rome falls", "Moon landing", "Titanic"]


def test_the_queue_shows_each_waiting_videos_place_and_the_held_ones(env):
    env.studio.RUNNING["slug"] = "the-one-being-made"
    env.client.post("/api/run", headers=env.headers, json={"title": "Rome falls\nTitanic", "steps": ["video"]})
    env.keeper.hold("Your Kie.ai balance ran out. Top it up, then Carry on.", "balance")
    env.client.post("/api/run", headers=env.headers, json={"title": "Moon landing", "steps": ["video"]})
    jobs = env.client.get("/api/queue", headers=env.headers).get_json()["jobs"]
    assert [(j["title"], j["state"]) for j in jobs] == [("the-one-being-made", "running"), ("Rome falls", "held"),
                                                       ("Titanic", "held"), ("Moon landing", "held")]
    assert all("Kie.ai balance" in j["reason"] for j in jobs[1:])
    env.keeper.check = lambda specs: {}
    assert env.client.post("/cr-queue/resume", headers=env.headers).get_json()["resumed"] == 3
    jobs = env.client.get("/api/queue", headers=env.headers).get_json()["jobs"]
    assert [(j["title"], j.get("position"), j.get("ahead")) for j in jobs] == [
        ("the-one-being-made", None, None), ("Rome falls", 1, 1), ("Titanic", 2, 2), ("Moon landing", 3, 3)]


def test_a_saved_project_resumes_while_another_is_made_once(env, monkeypatch):
    env.studio.RUNNING["slug"] = "the-one-being-made"
    monkeypatch.setattr(cr_studio.cr_bridge, "project_progress", lambda pid: {
        "purpose": "video", "project": {"status": "failed"},
        "job_spec": {"title": "Rome falls", "style": "history", "steps": ["video"]}})
    got = env.client.post("/cr-submit-project", headers=env.headers, json={"project_id": PROJECT})
    assert got.status_code == 200, got.get_json()
    assert env.studio.QUEUE[0]["cr_project_id"] == PROJECT and env.studio.QUEUE[0]["cr_setup_proof"] == {"check": 1}
    again = env.client.post("/cr-submit-project", headers=env.headers, json={"project_id": PROJECT})
    assert again.status_code == 409 and "already" in again.get_json()["error"]
    assert len(env.studio.QUEUE) == 1


def test_account_changes_stay_refused_while_videos_wait(env):
    env.client.post("/api/run", headers=env.headers, json={"title": "Rome falls", "steps": ["video"]})
    assert env.studio._cr_activity.busy()
    for path in ("/cr-connect", "/cr-hub", "/cr-voice", "/cr-claude/setup"):
        assert env.client.post(path, headers=env.headers, json={}).status_code == 409, path
    switch = env.client.post("/cr-claude/setup", headers=env.headers, json={"action": "switch"})
    assert switch.status_code == 409 and env.cfg["brain_mode"] == "channelrecipe"      # the waiting video's route
    verify = env.client.post("/cr-claude/setup", headers=env.headers, json={"action": "verify"})
    assert verify.status_code == 409 and "when it finishes" in verify.get_json()["error"]


def test_the_disk_check_counts_what_the_videos_ahead_still_need(env, monkeypatch):
    env.studio.RUNNING["slug"] = "the-one-being-made"
    env.client.post("/api/run", headers=env.headers, json={"title": "Rome falls", "steps": ["video"]})
    monkeypatch.setattr(env.keeper, "ahead_gb", lambda: 7.5)
    env.client.post("/api/run", headers=env.headers, json={"title": "Titanic", "steps": ["video"]})
    assert env.asked[-1][3] == 7.5                             # passed to cr_readiness.preset as `ahead`


def test_the_running_video_begins_with_its_own_proof_not_the_latest(env, monkeypatch):
    env.cfg.update(brain_mode="claudecode", setup_proof={"check": "latest"})
    env.studio.mv.job_dir = lambda *a: env.home / "job"
    env.studio.mv.final_video = lambda *a: env.home / "job" / "video.mp4"
    begun = []
    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", lambda *a, **k: begun.append(k["proof"]) or
                        {"id": "crv_" + "2" * 24})
    monkeypatch.setattr(cr_studio.cr_bridge, "finish_project", lambda receipt: {})
    cells = dict(zip(env.studio._run_spec.__code__.co_freevars, env.studio._run_spec.__closure__))
    owned = cells["run_owned_spec"].cell_contents
    cells = dict(zip(owned.__code__.co_freevars, owned.__closure__))
    monkeypatch.setattr(cells["original_run"], "cell_contents", lambda spec: env.studio.JOBS[spec["slug"]].update(
        state="done", done=True))
    env.studio.JOBS["rome"] = {"state": "queued", "lines": []}
    env.studio._run_spec({"slug": "rome", "title": "Rome falls", "style": "history", "minutes": 5, "steps": ["script"],
                          "cr_setup_proof": {"check": "its own"}})
    assert begun == [{"check": "its own"}]


def test_a_video_waits_for_a_product_build_or_a_farm_render_before_it_starts(env):
    activity = env.studio._cr_activity
    build = {"done": False}
    env.studio.PRODUCT_BUILD["proc"] = SimpleNamespace(poll=lambda: 0 if build["done"] else None)
    waited = threading.Event()
    thread = threading.Thread(target=lambda: (activity.wait_for_editor(), waited.set()), daemon=True)
    thread.start()
    assert not waited.wait(0.6), "a queued video never starts beside a product build"
    build["done"] = True
    assert waited.wait(3)
    env.studio.FARM["thread"] = SimpleNamespace(is_alive=lambda: False)
    activity.wait_for_editor()                                 # nothing else working: it starts at once


def test_a_queued_video_waits_for_a_style_study_or_editor_work_holding_studio(env):
    """A style study reserves Studio for its whole run (cr_style_routes): a video queued then starts after it, so a
    study's graphics check never waits on the video's renders (render.lock)."""
    activity = env.studio._cr_activity
    token = activity.reserve()
    waited = threading.Event()
    threading.Thread(target=lambda: (activity.wait_for_editor(), waited.set()), daemon=True).start()
    assert not waited.wait(0.6)
    activity.release(token)
    assert waited.wait(3)


# ── 2. hold on a shared stop ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("error, kind, says", [
    ("OSError: [Errno 28] No space left on device", "disk_full", "disk"),
    ("The local video worker stopped: this computer ran out of memory.", "out_of_memory", "memory"),
    ("Kie.ai answered 402: insufficient balance for this task.", "balance", "Kie.ai balance"),
    ("ElevenLabs answered 401 Unauthorized: invalid api key.", "auth", "ElevenLabs stopped accepting"),
    ("Claude Code hit your usage limit · resets 4am.", "usage_limit", "usage limit"),
    ("Install Claude Code and sign in locally before making a video.", "setup", "setup"),
])
def test_a_shared_stop_holds_every_video_waiting(tmp_path, error, kind, says):
    waiting = [spec("Titanic"), spec("Funniest Dogs", ranking=True), spec("Re-edit", timeline=True)]
    studio, _ = fake_studio(waiting)
    keeper = cr_queue.Keeper(studio, tmp_path)
    stopped(keeper, studio, spec("Rome falls"), error, tmp_path)
    assert [s["title"] for s in keeper.held] == ["Titanic", "Funniest Dogs"], error
    assert [s["title"] for s in studio.QUEUE] == ["Re-edit"]   # an editor's re-render isn't a new video
    status = keeper.status()
    assert status["code"] == kind and says.lower() in status["reason"].lower()
    assert "“Rome falls” stopped on it" in status["reason"]
    assert all(h["reason"] == status["reason"] for h in status["held"])
    assert all(studio.JOBS[s["slug"]]["state"] == "held" for s in keeper.held)


@pytest.mark.parametrize("error", [
    "Kie.ai refused this picture: content flagged by its safety system.",       # one picture's words
    "The narration request timed out after 600 s.",                              # a timeout: the next may work
    "Kie.ai answered 429: too many requests.",                                   # a rate limit
    "YouTube: Sign in to confirm you're not a bot (HTTP Error 403: Forbidden)",  # YouTube's own: the Shorts'
    "KeyError: 'scenes'",                                                        # one video's renderer bug
    "This saved video uses a different renderer. Keep its pinned renderer, or use a new title for a new project.",
    "Couldn't reach channelrecipe.com: connection reset.",                       # a network blip
])
def test_a_videos_own_stop_never_holds_the_rest(tmp_path, error):
    studio, _ = fake_studio([spec("Titanic")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    stopped(keeper, studio, spec("Rome falls"), error, tmp_path)
    assert keeper.held == [] and [s["title"] for s in studio.QUEUE] == ["Titanic"], error


def test_a_cancelled_or_finished_video_never_holds(tmp_path):
    studio, _ = fake_studio([spec("Titanic")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    for state in ("cancelled", "done"):
        studio.JOBS["rome-falls--run"] = {"state": state, "error": "No space left on device", "done": True}
        keeper.finished(spec("Rome falls"), lambda: tmp_path)
    assert keeper.held == []


def test_a_claude_stage_stopped_on_its_limit_or_model_holds_the_rest(tmp_path, monkeypatch):
    for code, kind in (("usage_limit", "usage_limit"), ("model_unavailable", "model"), ("auth_missing", "auth"),
                       ("invalid_result", "")):
        studio, _ = fake_studio([spec("Titanic")])
        keeper = cr_queue.Keeper(studio, tmp_path / code)
        monkeypatch.setattr(cr_failure, "_claude_stop", lambda home, pid, since, code=code: code)
        stopped(keeper, studio, spec("Rome falls", cr_project_id=PROJECT), "A Claude stage stopped.", tmp_path / code)
        assert keeper.status()["code"] == kind and (len(keeper.held) == 1) is bool(kind), code
        if kind:
            assert keeper.status()["reason"].startswith("Claude")


def test_a_ranking_shorts_claude_limit_holds_every_video_and_its_clip_watches_only_the_shorts(tmp_path):
    for code, held in (("claude_usage_limit", ["Titanic", "Funniest Dogs"]), ("out_of_watches", ["Funniest Dogs"])):
        studio, _ = fake_studio([spec("Titanic"), spec("Funniest Dogs", ranking=True)])
        keeper = cr_queue.Keeper(studio, tmp_path / code)
        short = spec("Scariest Moments", ranking=True)
        job = tmp_path / code / "job"
        (job / "short").mkdir(parents=True)
        (job / "short" / "error.json").write_text(json.dumps({"code": code, "message": "It stopped."}))
        studio.JOBS[short["slug"]] = {"state": "error", "done": True}
        keeper.finished(short, lambda: job)
        assert [s["title"] for s in keeper.held] == held, code
        assert keeper.scope == ("all" if code == "claude_usage_limit" else "ranking")


def test_a_video_submitted_while_the_queue_is_held_joins_the_hold(tmp_path):
    studio, _, _ = studio_with_original()
    keeper = studio._cr_queue = cr_queue.Keeper(studio, tmp_path)
    cr_submission.install(studio)
    client = studio.app.test_client()
    client.post("/api/run", json={"title": "Titanic", "steps": ["video"]})
    keeper.hold(cr_queue.plain_reason("balance", "kie"), "balance")
    got = client.post("/api/run", json={"title": "Moon landing", "steps": ["video"]}).get_json()
    assert "Kie.ai balance" in got["queued"][0]["held"]
    assert studio.QUEUE == [] and [s["title"] for s in keeper.held] == ["Titanic", "Moon landing"]
    # held for a Ranking Short's own stop: other recipes still run, Ranking Shorts join the hold
    keeper.drop()
    keeper.add([spec("Funniest Dogs", ranking=True)])
    keeper.hold_ranking("Your clip watches for this month are used up.", "out_of_watches")
    places = keeper.add([spec("Titanic"), spec("Cutest Cats", ranking=True)])
    assert places["titanic--run"]["position"] == 1 and "used up" in places["cutest-cats--run"]["held"]
    assert [s["title"] for s in studio.QUEUE] == ["Titanic"]


def test_a_closed_studios_leftovers_dont_hold_new_videos(tmp_path):
    studio, _ = fake_studio([spec("Titanic")])
    cr_queue.Keeper(studio, tmp_path).save()
    fresh, _ = fake_studio()
    keeper = cr_queue.Keeper(fresh, tmp_path)
    keeper.restore()
    assert keeper.status()["reason"] == cr_queue.CLOSED and keeper.scope == ""
    places = keeper.add([spec("Moon landing")])
    assert places["moon-landing--run"]["position"] == 1 and [s["title"] for s in fresh.QUEUE] == ["Moon landing"]


def test_a_shared_hold_and_its_reasons_survive_a_restart(tmp_path):
    studio, _ = fake_studio([spec("Titanic"), spec("Moon landing")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    keeper.hold("Your Kie.ai balance ran out. Top it up, then Carry on.", "balance")
    fresh, _ = fake_studio()
    again = cr_queue.Keeper(fresh, tmp_path)
    again.restore()
    assert again.scope == "all" and again.code == "balance"
    assert [h["reason"] for h in again.status()["held"]] == ["Your Kie.ai balance ran out. Top it up, then Carry on."] * 2


def test_carry_on_checks_each_held_video_again_and_keeps_the_ones_still_blocked(tmp_path):
    studio, dispatched = fake_studio([spec("Titanic"), spec("Moon landing"), spec("Rome falls")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    keeper.hold(cr_queue.plain_reason("balance", "kie"), "balance")
    seen = []

    def check(specs):
        seen.append([s["title"] for s in specs])
        for s in specs:
            s["cr_setup_proof"] = {"fresh": True}
        return {"moon-landing--run": "Connect ElevenLabs in Setup & accounts for this recipe's voice."}
    keeper.check = check
    assert keeper.resume() == 2 and dispatched == [True]
    assert seen == [["Titanic", "Moon landing", "Rome falls"]]
    assert [s["title"] for s in studio.QUEUE] == ["Titanic", "Rome falls"]
    assert all(s["cr_setup_proof"] == {"fresh": True} for s in studio.QUEUE)
    status = keeper.status()
    assert [h["title"] for h in status["held"]] == ["Moon landing"] and "ElevenLabs" in status["reason"]
    assert keeper.scope == ""                                  # its own problem: new videos aren't held by it


def test_carry_on_starts_nothing_when_the_check_itself_fails(tmp_path):
    studio, dispatched = fake_studio([spec("Titanic")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    keeper.hold("Claude's usage limit was reached.", "usage_limit")
    keeper.check = lambda specs: (_ for _ in ()).throw(RuntimeError("offline"))
    assert keeper.resume() == 0 and studio.QUEUE == [] and dispatched == []
    assert "couldn't check" in keeper.status()["reason"] and len(keeper.held) == 1


def test_a_project_being_made_waiting_or_held_is_known(tmp_path):
    studio, _ = fake_studio([spec("Titanic", cr_project_id=PROJECT)])
    keeper = cr_queue.Keeper(studio, tmp_path)
    assert keeper.has_project(PROJECT) and not keeper.has_project("crv_" + "b" * 24) and not keeper.has_project("")
    keeper.hold("stop", "balance")
    assert keeper.has_project(PROJECT)
    other = spec("Rome falls", cr_project_id="crv_" + "c" * 24)
    keeper.started(other)
    assert keeper.has_project("crv_" + "c" * 24)
    studio.JOBS[other["slug"]] = {"state": "done", "done": True}
    keeper.finished(other, lambda: tmp_path)
    assert not keeper.has_project("crv_" + "c" * 24)


def test_ahead_counts_the_running_waiting_and_held_videos_less_their_saved_work(tmp_path):
    saved = tmp_path / "rome"
    saved.mkdir()
    (saved / "a.bin").write_bytes(b"x" * 1000)
    studio, _ = fake_studio([spec("Titanic", minutes=10), spec("Timeline", timeline=True)], running="rome-falls--run")
    studio.mv = SimpleNamespace(find_job=lambda slug: saved if slug == "rome-falls--run" else None)
    keeper = cr_queue.Keeper(studio, tmp_path)
    per = lambda minutes: cr_machine.need_gb(minutes) - cr_machine.NEED_BASE_GB
    assert keeper.ahead_gb() == round(per(10), 1)              # the waiting one (an editor's re-render isn't counted)
    keeper.started(spec("Rome falls", minutes=4))              # and the one being made, less what it holds already
    assert keeper.ahead_gb() == round(per(10) + per(4) - 1000 / 1000 ** 3, 1)
    keeper.hold("stop", "balance")
    assert keeper.ahead_gb() == round(per(10) + per(4), 1)     # held ones still come before a new video


def test_disk_check_warns_when_the_queue_ahead_leaves_too_little():
    assert cr_machine.disk_check(30.0, 10, ahead=0) == ("", "")
    problem, warning = cr_machine.disk_check(30.0, 10, ahead=25.0)
    assert problem == "" and "already in the queue need about 25 GB of the 30 GB" in warning
    assert "on hold" in warning and "about 5 GB for this video" in warning
    # the queue never turns a video that fits on its own into a stop: it can't fit at all, that stays a stop
    assert cr_machine.disk_check(2.0, 10, ahead=25.0)[0]
    assert cr_machine.disk_check(30.0, 10, videos=3, ahead=20.0)[1].count("these 3 videos") == 1


# ── single slots a second submission could clobber ───────────────────────────────────────────────────────────────

def test_each_interrupted_project_begin_keeps_its_own_id(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_bridge, "HOME", tmp_path)
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: {"brain_mode": "claudecode", "key": "cr_mcp_fixture"})
    ids, online = [], {"on": False}

    def tool(name, payload):
        ids.append((payload["title"], payload["begin_id"]))
        if not online["on"]:
            raise RuntimeError("offline")
        return {"project": {"id": "crv_" + "d" * 24}}
    monkeypatch.setattr(cr_bridge, "_mcp_tool", tool)
    for title in ("Rome falls", "Titanic"):                    # two videos' begins lost, one after the other
        with pytest.raises(RuntimeError):
            cr_bridge.begin_project(title, "history", proof={"check": 1})
    online["on"] = True
    cr_bridge.begin_project("Rome falls", "history", proof={"check": 1})
    cr_bridge.begin_project("Titanic", "history", proof={"check": 1})
    rome = [b for t, b in ids if t == "Rome falls"]
    titanic = [b for t, b in ids if t == "Titanic"]
    assert rome[0] == rome[1] and titanic[0] == titanic[1] and rome[0] != titanic[0]
    assert not (tmp_path / ".cr-pending-begin.json").exists()


def test_an_older_studios_pending_begin_is_still_honoured(tmp_path):
    path = tmp_path / ".cr-pending-begin.json"
    path.write_text(json.dumps({"identity": "abc", "begin_id": "b_old"}))
    assert cr_bridge._pending_begins(path) == {"abc": "b_old"}
    cr_bridge._save_begins(path, {f"id{i}": f"b_{i}" for i in range(40)})
    kept = cr_bridge._pending_begins(path)
    assert len(kept) == cr_bridge.PENDING_BEGINS and "id39" in kept and "id0" not in kept


def test_a_video_is_known_as_being_made_from_the_moment_the_queue_hands_it_over(env, monkeypatch):
    """The dispatcher takes a video off the waiting line, then it may wait for editor or study work: a second Resume of
    the same project in that time is refused, and a video that fails to start is let go."""
    activity = env.studio._cr_activity
    token = activity.reserve()                                 # a style study holds Studio
    video = {"slug": "rome", "title": "Rome falls", "style": "history", "minutes": 5, "steps": ["script"],
             "cr_project_id": PROJECT, "cr_attempt_slug": "not/a valid slug"}
    env.studio.JOBS["rome"] = {"state": "queued"}
    thread = threading.Thread(target=env.studio._run_spec, args=(video,), daemon=True)
    thread.start()
    deadline = time.time() + 3
    while not env.keeper.has_project(PROJECT) and time.time() < deadline:
        time.sleep(0.02)
    assert env.keeper.has_project(PROJECT), "waiting for the study, it's already the one being made"
    activity.release(token)
    thread.join(3)
    assert env.studio.JOBS["rome"]["state"] == "error"         # its attempt folder was refused before it began
    assert not env.keeper.has_project(PROJECT) and env.keeper.running is None


# ── review 2026-10-26: the Carry on window, an exception's hold, auth holds ─────────────────────────────────────

def test_through_carry_ons_check_the_videos_stay_on_hold(tmp_path):
    """The check takes seconds: meanwhile a held video can't be deleted or resumed twice, a Remove sticks, a second
    Carry on waits for the first, and a video added then goes after the checked ones."""
    import cr_trash
    first, second = spec("Titanic", cr_project_id=PROJECT), spec("Moon landing")
    studio, dispatched = fake_studio([first, second])
    keeper = studio._cr_queue = cr_queue.Keeper(studio, tmp_path)
    keeper.hold(cr_queue.plain_reason("balance", "kie"), "balance")
    entered, release = threading.Event(), threading.Event()

    def check(specs):
        entered.set()
        assert release.wait(3)
        return {}
    keeper.check = check
    results = []
    worker = threading.Thread(target=lambda: results.append(keeper.resume()), daemon=True)
    worker.start()
    assert entered.wait(3)
    assert "on hold" in cr_trash._slug_busy(studio, first["slug"]), "never binned mid-check"
    assert keeper.has_project(PROJECT), "a second Resume of it is refused"
    assert keeper.resume() == 0 and keeper.status()["checking"] is True
    late = spec("Rome falls")
    assert "held" in keeper.add([late])[late["slug"]]
    release.set()
    worker.join(3)
    assert results == [2 + 1] and [s["title"] for s in studio.QUEUE] == ["Titanic", "Moon landing", "Rome falls"]
    assert keeper.held == [] and keeper.status()["checking"] is False and dispatched == [True]


def test_a_remove_or_a_shared_stop_during_carry_ons_check_wins(tmp_path):
    for during in ("remove", "stop"):
        studio, _ = fake_studio([spec("Titanic"), spec("Moon landing")])
        keeper = cr_queue.Keeper(studio, tmp_path / during)
        keeper.hold(cr_queue.plain_reason("balance", "kie"), "balance")

        def check(specs, during=during, keeper=keeper):
            if during == "remove":
                keeper.drop()
            else:
                keeper.hold(cr_queue.plain_reason("disk_full"), "disk_full")
            return {}
        keeper.check = check
        assert keeper.resume() == 0 and studio.QUEUE == [], during
        if during == "remove":
            assert keeper.held == [] and studio.JOBS == {}
        else:
            assert len(keeper.held) == 2 and keeper.status()["code"] == "disk_full"
            assert all("disk" in h["reason"] for h in keeper.status()["held"])


def test_a_held_video_cant_be_deleted_by_its_job_state_alone(tmp_path):
    import cr_trash
    studio, _ = fake_studio()
    studio.JOBS["titanic--run"] = {"state": "held"}
    assert "on hold" in cr_trash._slug_busy(studio, "titanic--run")


def test_an_exception_while_making_a_video_still_holds_the_queue(env, monkeypatch):
    """A full disk raising OSError (writing the worker's files) used to leave the video "running" for the hold check,
    so each waiting video started and failed in turn."""
    env.studio.mv.job_dir = lambda *a: env.home / "job"
    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", lambda *a, **k: {"id": "crv_" + "2" * 24})
    cells = dict(zip(env.studio._run_spec.__code__.co_freevars, env.studio._run_spec.__closure__))
    owned = cells["run_owned_spec"].cell_contents
    cells = dict(zip(owned.__code__.co_freevars, owned.__closure__))

    def full(spec):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cells["original_run"], "cell_contents", full)
    waiting = spec("Titanic")
    env.studio.QUEUE.append(waiting)
    env.studio.JOBS[waiting["slug"]] = {"state": "queued", "title": "Titanic"}
    env.studio.JOBS["rome"] = {"state": "queued", "lines": []}
    env.studio._run_spec({"slug": "rome", "title": "Rome falls", "style": "history", "minutes": 5, "steps": ["script"]})
    assert env.studio.JOBS["rome"]["state"] == "error" and "No space left" in env.studio.JOBS["rome"]["error"]
    assert env.studio.QUEUE == [] and [s["title"] for s in env.keeper.held] == ["Titanic"]
    assert env.keeper.status()["code"] == "disk_full"


@pytest.mark.parametrize("error, holds", [
    ("Pexels answered 401 Unauthorized.", False),                       # one service some videos use
    ("HTTP Error 403: Forbidden from cdn.example.org", False),          # an unknown host
    ("Kie.ai answered 401 Unauthorized: invalid api key.", True),
    ("ElevenLabs answered 401 Unauthorized.", True),
    ("Claude Code: authentication_error, please log in again (401).", True),
])
def test_an_auth_stop_holds_only_for_the_accounts_every_video_uses(tmp_path, error, holds):
    studio, _ = fake_studio([spec("Titanic")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    stopped(keeper, studio, spec("Rome falls"), error, tmp_path)
    assert bool(keeper.held) is holds, error


def test_an_unavailable_claude_model_says_so(tmp_path, monkeypatch):
    studio, _ = fake_studio([spec("Titanic")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    monkeypatch.setattr(cr_failure, "_claude_stop", lambda *a: "model_unavailable")
    stopped(keeper, studio, spec("Rome falls", cr_project_id=PROJECT), "A Claude stage stopped.", tmp_path)
    assert keeper.status()["code"] == "model" and "model" in keeper.status()["reason"]
    assert "signing in" not in keeper.status()["reason"]


def test_shorts_held_on_used_up_watches_can_still_use_credits_after_a_claude_limit(tmp_path):
    studio, _ = fake_studio([spec("Funniest Dogs", ranking=True), spec("Titanic")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    keeper.hold_ranking("Your clip watches for this month are used up.", "out_of_watches")
    keeper.hold(cr_queue.plain_reason("usage_limit"), "usage_limit")
    status = keeper.status()
    assert status["code"] == "usage_limit" and status["credits"] is True
    again = cr_queue.Keeper(fake_studio()[0], tmp_path)
    again.restore()
    assert again.status()["credits"] is True
    keeper.check = lambda specs: {}
    assert keeper.resume(use_credits=True) == 2
    shorts = [s for s in studio.QUEUE if cr_queue.is_ranking(s)]
    assert '"use_credits": true' in shorts[0]["extra"] and "use_credits" not in studio.QUEUE[1]["extra"]


# ── Studio 110: the old API route switches to Claude Code at the next new video, once it is ready ────────────────

def test_a_create_on_the_old_api_route_switches_to_claude_code_once_claude_code_is_ready(env, monkeypatch):
    status = env.client.get("/cr-status", headers=env.headers).get_json()
    assert status["api_route"] == {"ready_to_switch": False, "message": cr_studio.cr_readiness.SWITCH_TO_CLAUDE_CODE}
    first = env.client.post("/api/run", headers=env.headers, json={"title": "Rome falls", "steps": ["video"]})
    assert first.status_code == 200 and env.cfg["brain_mode"] == "channelrecipe"   # not ready: the API route stays
    assert cr_studio.SWITCHED_NOTE not in (first.get_json().get("warnings") or [])
    env.studio.QUEUE.clear()
    env.studio.JOBS.clear()
    # installed, signed in, connected and verified on the Claude Code route (cr_claude.ready_to_switch)
    monkeypatch.setattr(cr_studio.cr_claude, "ready_to_switch", lambda cfg, cli: cfg.get("brain_mode") != "claudecode")
    second = env.client.post("/api/run", headers=env.headers, json={"title": "Titanic", "steps": ["video"]})
    assert second.status_code == 200, second.get_json()
    assert second.get_json()["warnings"][0] == cr_studio.SWITCHED_NOTE           # said once, with the start
    assert env.cfg["brain_mode"] == "claudecode" and env.cfg["switched_to_claude_code_at"]
    assert env.cfg["general_model"] == cr_studio.cr_claude.GENERAL_MODEL
    third = env.client.post("/api/run", headers=env.headers, json={"title": "Moon landing", "steps": ["video"]})
    assert cr_studio.SWITCHED_NOTE not in (third.get_json().get("warnings") or [])


def test_the_api_route_never_switches_under_a_video_being_made_waiting_or_held(env, monkeypatch):
    monkeypatch.setattr(cr_studio.cr_claude, "ready_to_switch", lambda cfg, cli: cfg.get("brain_mode") != "claudecode")
    env.studio.RUNNING["slug"] = "the-one-being-made"                 # an API-route video finishes as it started
    env.client.post("/api/run", headers=env.headers, json={"title": "Rome falls", "steps": ["video"]})
    assert env.cfg["brain_mode"] == "channelrecipe"
    env.studio.RUNNING["slug"] = None
    env.client.post("/api/run", headers=env.headers, json={"title": "Titanic", "steps": ["video"]})
    assert env.cfg["brain_mode"] == "channelrecipe"                     # "Rome falls" still waits
    env.studio.QUEUE.clear()
    env.studio.JOBS.clear()
    env.keeper.held.append(spec("Moon landing"))
    env.client.post("/api/run", headers=env.headers, json={"title": "Pompeii", "steps": ["video"]})
    assert env.cfg["brain_mode"] == "channelrecipe"                     # a held one carries on as it was checked
