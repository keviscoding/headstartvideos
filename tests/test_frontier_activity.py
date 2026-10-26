"""Original editor background work keeps its account context and stops visibly."""
import json
import importlib.util
import ast
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from flask import Flask, Response, jsonify, request

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_studio


def original_function(name, filename, namespace):
    path = Path("/Users/workcomputer/Downloads/ChannelRecipe studio") / filename
    if not path.is_file():
        pytest.skip("The owner's original private studio source is not installed here.")
    source = ast.parse(path.read_text())
    definition = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
    definition.decorator_list = []
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.fixture
def timeline():
    module = ModuleType("isolated_original_editor")
    # The fake has the same original dispatch boundary: chat returns before the
    # worker enters _chat_reply. No provider, subprocess or installed file runs.
    exec("""
def chat(job, slug, message, sel, after_render=False):
    if not message.strip():
        return {"error": "Write what should change."}
    if after_render:
        return {"ok": True, "queued": True}
    threading.Thread(target=_chat_reply, args=(job, slug, message, sel, "reply"), daemon=True).start()
    return {"ok": True, "id": "user"}

def _chat_reply(*args):
    return reply_handler(*args)

def _run_task(*args):
    return task_handler(*args)
""", module.__dict__)
    module.spawned = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            module.spawned.append(self)

    module.threading = SimpleNamespace(Thread=DeferredThread)
    module.reply_handler = lambda *args: module.calls.append(("reply", args))
    module.task_handler = lambda *args: module.calls.append(("task", args))
    module.calls = []
    module.state = {"auto_render": True, "chat": [{"id": "reply", "pending": True}], "tasks": []}
    module._lock = lambda job: threading.RLock()
    module._state = lambda job: module.state
    module._save_state = lambda job, state: None
    module._QCOND = threading.Condition()
    module._QUEUE = []
    module._WORKER = {"current": None}

    def replace(job, mid, text, results, error=False):
        module.state["chat"][0].update(pending=False, text=text, error=error)

    module._replace_msg = replace
    module._finish = lambda job, tid, state, msg: module.state["tasks"].append(
        {"id": tid, "state": state, "msg": msg})
    return module


@pytest.fixture
def runtime(tmp_path, monkeypatch, timeline):
    app = Flask("isolated_cr_activity")

    @app.get("/")
    def original_page():
        return "Original engine page"

    @app.get("/api/channels")
    def original_channels():
        return jsonify(channels=[], features=[], caps={}, prices={}, dlc={})

    @app.post("/api/run")
    def api_run():
        return jsonify(queued=["native-job"])

    @app.post("/api/tl/example/chat")
    def chat():
        busy = studio.RUNNING.get("slug") == "example" or any(s.get("slug") == "example" for s in studio.QUEUE)
        return jsonify(timeline.chat(tmp_path, "example", "Keep the original edit", {"kind": "range"}, after_render=busy))

    @app.post("/api/products/example/reveal")
    def reveal():
        return jsonify(ok=True)

    @app.post("/api/tl/example/op")
    def op():
        return jsonify(results=[])

    @app.get("/f/video/cached")
    def saved_video():
        return Response(b"already saved", mimetype="video/mp4")

    studio = SimpleNamespace(app=app, mv=SimpleNamespace(), styles=SimpleNamespace(STYLES={}), RUNNING={}, QUEUE=[],
        JOBS={}, PRODUCT_BUILD={}, FARM={}, timeline=timeline, _run_spec=lambda spec: None)
    monkeypatch.setattr(cr_studio.cr_submission, "install", lambda *a: None)
    cfg = {"brain_mode": "channelrecipe", "costs_accepted": True, "key": "cr_mcp_fixture"}
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
    monkeypatch.setattr(cr_studio.cr_bridge, "_mcp_tool", lambda *a: {"approved": True})
    monkeypatch.setattr(cr_studio.cr_claude, "sync_receipts", lambda *a: None)
    for adapter in (cr_studio.cr_bridge, cr_studio.cr_media, cr_studio.cr_capabilities,
                    cr_studio.cr_render, cr_studio.cr_workers):
        monkeypatch.setattr(adapter, "install", lambda *a: None)
    monkeypatch.setattr(cr_studio.cr_media, "profile", lambda *a: {})
    monkeypatch.setattr(cr_studio.cr_media, "active", lambda *a: False)
    monkeypatch.setattr(cr_studio.cr_workers, "busy", lambda: False)
    monkeypatch.setattr(cr_studio.cr_readiness, "basic", lambda checks: {
        "ready": True, "checks": [], "blockers": [], "brain_mode": cfg["brain_mode"], "claude_code": {}})
    monkeypatch.setattr(cr_studio.cr_readiness, "preset", lambda *a: {"ready": True, "proof": {}})
    cr_studio.create_app()
    token = json.loads((tmp_path / ".cr-runtime.json").read_text())["token"]
    headers = {"Host": "127.0.0.1:7860", "X-CR-Local-Token": token}
    return SimpleNamespace(studio=studio, client=app.test_client(), headers=headers, cfg=cfg, job=tmp_path)


def test_chat_reserves_context_before_the_original_thread_starts(runtime, timeline):
    r = runtime
    response = r.client.post("/api/tl/example/chat", headers=r.headers)
    assert response.status_code == 200
    assert len(timeline.spawned) == 1 and timeline.calls == []
    assert r.studio._cr_activity.busy()
    for path in ("/cr-connect", "/cr-recover"):
        assert r.client.post(path, headers=r.headers, json={}).status_code == 409
    # a video joins the queue instead (the dispatcher starts it once the editor's reply is done: wait_for_editor);
    # a saved project is checked, never refused as busy
    assert r.client.post("/api/run", headers=r.headers, json={}).status_code == 200
    assert r.client.post("/cr-submit-project", headers=r.headers, json={}).status_code == 400
    original = timeline.spawned.pop()
    original.target(*original.args)
    assert timeline.calls == [("reply", (r.job, "example", "Keep the original edit", {"kind": "range"}, "reply"))]
    assert not r.studio._cr_activity.busy()
    assert r.client.post("/api/run", headers=r.headers, json={}).status_code == 200


def test_workspace_shell_owns_its_token_and_skips_legacy_ui_injection(runtime, monkeypatch):
    shell = '<html><head><script>window.CR_WORKSPACE={token:__LOCAL_TOKEN_JSON__,preset:__PRESET_JSON__};</script></head><body>ChannelRecipe workspace</body></html>'
    (runtime.job / "cr_workspace.html").write_text(shell)

    def legacy_transform(*args, **kwargs):
        pytest.fail("The independent workspace must not receive legacy controls.")

    monkeypatch.setattr(cr_studio.cr_ui, "transform_html", legacy_transform)
    response = runtime.client.get("/", headers=runtime.headers)
    assert response.status_code == 200 and response.headers["X-CR-Workspace"] == "1"
    assert "ChannelRecipe workspace" in response.text
    assert runtime.headers["X-CR-Local-Token"] in response.text
    assert "CR_LOCAL_TOKEN" not in response.text and "cr-generation-readiness" not in response.text
    assert runtime.client.get("/cr-workspace/catalog", headers=runtime.headers).json["channels"] == []


def test_existing_editor_keeps_the_local_token_adapter(runtime, monkeypatch):
    runtime.studio.app.add_url_rule("/editor/saved", "saved_editor", lambda: "<html><head></head><body>Saved editor</body></html>")
    monkeypatch.setattr(cr_studio.cr_ui, "transform_html", lambda text, **kwargs: text)
    response = runtime.client.get("/editor/saved", headers=runtime.headers)
    assert response.status_code == 200 and "X-CR-Workspace" not in response.headers
    assert "Saved editor" in response.text and "CR_LOCAL_TOKEN" in response.text
    assert runtime.headers["X-CR-Local-Token"] in response.text


def test_fresh_create_is_not_locked_by_a_historical_allowance_report(runtime, monkeypatch):
    runtime.cfg["brain_mode"] = "claudecode"
    monkeypatch.setattr(cr_studio.cr_claude, "status", lambda *a, **k: {
        "ready": True, "allowance": {"blocked": False, "reported_limit": True,
                                      "message": "Earlier usage limit; extra usage may now be available."}})
    response = runtime.client.post("/api/run", headers=runtime.headers, json={})
    assert response.status_code == 200
    assert response.get_json()["queued"] == ["native-job"]


@pytest.mark.parametrize("status", ["done", "failed", "running"])
def test_new_run_uses_fresh_session_only_after_previous_video_completed(runtime, monkeypatch, status):
    r = runtime
    old = "crv_" + "1" * 24
    new = "crv_" + "2" * 24
    r.cfg.update(brain_mode="claudecode", setup_proof={})
    r.studio.mv.job_dir = lambda *a:r.job
    r.studio.mv.final_video = lambda *a:r.job / "video.mp4"
    (r.job / "video.mp4").write_bytes(b"saved output")
    (r.job / "cr-project.json").write_text(json.dumps({"id":old, "status":"running",
        "brain_mode":"claudecode", "job_spec":{"render_backend":"original"}}))
    r.studio.JOBS["example"] = {"state":"queued", "lines":[]}
    r.studio.RUNNING["slug"] = "example"
    events = []
    monkeypatch.setattr(cr_studio.cr_bridge, "project_progress", lambda pid:
        events.append(("progress", pid)) or {"project":{"status":status}})
    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", lambda title, style, resume, **kw:
        events.append(("begin", resume)) or {"id":resume or new})
    monkeypatch.setattr(cr_studio.cr_bridge, "finish_project", lambda receipt:events.append(("finish", receipt)))
    monkeypatch.setattr(cr_studio, "output_artifacts", lambda *a:[
        {"name":"video.mp4", "sha256":"f"*64, "media_type":"video/mp4"}])
    cells = dict(zip(r.studio._run_spec.__code__.co_freevars, r.studio._run_spec.__closure__))
    if "run_owned_spec" in cells:
        owned = cells["run_owned_spec"].cell_contents
        cells = dict(zip(owned.__code__.co_freevars, owned.__closure__))
    def render(spec):
        events.append(("render",))
        r.studio.JOBS["example"].update(state="done", done=True)
        r.studio.RUNNING["slug"] = None
    monkeypatch.setattr(cells["original_run"], "cell_contents", render)
    r.studio._run_spec({"slug":"example", "title":"Existing video", "style":"history",
        "minutes":5, "steps":["video"]})
    assert events[:2] == [("progress",old), ("begin",None if status == "done" else old)]
    assert events[2][0] == "render" and events[3][0] == "finish"
    assert events[3][1]["project_id"] == (new if status == "done" else old)
    assert (r.job / "video.mp4").read_bytes() == b"saved output"


@pytest.mark.parametrize("server_optional", [{}, {"duration_seconds":None,"width":None,"height":None}])
def test_completed_receipt_supersedes_only_matching_failed_repeat(tmp_path, server_optional):
    pid = "crv_" + "1" * 24
    artifacts = [{"name":"video.mp4", "sha256":"a"*64, "media_type":"video/mp4"}]
    saved = {"project_id":pid, "status":"done", "artifacts":artifacts, "error_code":""}
    failed = {"project_id":pid, "status":"failed", "artifacts":[], "error_code":"runtime_failed"}
    completed = tmp_path / ".cr-finish.json"
    pending = tmp_path / ".cr-finish-pending.json"
    completed.write_text(json.dumps(saved)); original = completed.read_bytes()
    pending.write_text(json.dumps(failed))
    bridge = SimpleNamespace(project_progress=lambda project:{"project":{"status":"done"},
        "artifacts":[{**a, **server_optional} for a in artifacts],"stages":[{"status":"complete"}]},
        finish_project=lambda *a:pytest.fail("Never downgrade finished work"))
    cr_studio.sync_completion_receipt(bridge, pending)
    assert not pending.exists() and completed.read_bytes() == original
    assert json.loads((tmp_path / ".cr-finish-superseded.json").read_text()) == failed


@pytest.mark.parametrize("mismatch", ["project", "status", "artifacts", "active", "failed", "metadata"])
def test_receipt_reconciliation_does_not_hide_unproven_or_active_work(tmp_path, mismatch):
    pid = "crv_" + "1" * 24
    artifacts = [{"name":"video.mp4", "sha256":"a"*64, "media_type":"video/mp4"}]
    saved = {"project_id":pid, "status":"done", "artifacts":artifacts}
    failed = {"project_id":pid, "status":"failed", "artifacts":[], "error_code":"runtime_failed"}
    progress = {"project":{"status":"done"}, "artifacts":artifacts, "stages":[]}
    if mismatch == "project": saved["project_id"] = "crv_" + "2" * 24
    if mismatch == "status": progress["project"]["status"] = "running"
    if mismatch == "artifacts": progress["artifacts"] = []
    if mismatch == "metadata": progress["artifacts"] = [{**artifacts[0], "duration_seconds":27.04}]
    if mismatch in {"active", "failed"}: progress["stages"] = [{"status":"authorized" if mismatch == "active" else "failed"}]
    completed = tmp_path / ".cr-finish.json"; completed.write_text(json.dumps(saved))
    pending = tmp_path / ".cr-finish-pending.json"; pending.write_text(json.dumps(failed))
    def reject(receipt):
        assert receipt == failed
        raise RuntimeError("Review saved work")
    bridge = SimpleNamespace(project_progress=lambda project:progress, finish_project=reject)
    with pytest.raises(RuntimeError, match="Review saved work"):
        cr_studio.sync_completion_receipt(bridge, pending)
    assert pending.exists() and json.loads(completed.read_text()) == saved
    assert not (tmp_path / ".cr-finish-superseded.json").exists()


def test_recovery_poll_never_replays_old_completion_during_active_work(runtime, monkeypatch):
    r = runtime
    r.studio.OUT = r.job
    pending = r.job / ".cr-finish-pending.json"
    pending.write_text(json.dumps({"project_id":"crv_" + "1" * 24, "status":"failed"}))
    r.studio.RUNNING["slug"] = "example"
    monkeypatch.setattr(cr_studio.cr_bridge, "call", lambda *a:{"projects":[]})
    monkeypatch.setattr(cr_studio.cr_bridge, "finish_project", lambda *a:pytest.fail("Cannot close live resumed work"))
    response = r.client.get("/cr-projects", headers=r.headers)
    assert response.status_code == 200 and pending.exists()


def test_idle_recovery_receipt_cannot_race_a_new_submission(runtime, monkeypatch):
    r = runtime
    r.studio.OUT = r.job
    (r.job / ".cr-finish-pending.json").write_text(json.dumps({"project_id":"crv_" + "1"*24,
        "status":"failed", "error_code":"runtime_failed"}))
    entered, release = threading.Event(), threading.Event()
    results = []
    monkeypatch.setattr(cr_studio.cr_bridge, "call", lambda *a:{"projects":[]})
    def finish(receipt):
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(cr_studio.cr_bridge, "finish_project", finish)
    def poll():
        with r.studio.app.test_client() as client:
            results.append(client.get("/cr-projects", headers=r.headers).status_code)
    thread = threading.Thread(target=poll)
    thread.start()
    try:
        assert entered.wait(3)
        response = r.client.post("/api/run", headers=r.headers, json={})
        assert response.status_code == 409
        assert "current studio request" in response.get_json()["error"]
    finally:
        release.set(); thread.join(3)
    assert results == [200] and not thread.is_alive()
    assert not (r.job / ".cr-finish-pending.json").exists()


@pytest.mark.parametrize("busy_source", ["queued", "working", "product", "farm"])
def test_native_async_work_blocks_context_and_account_changes(runtime, timeline, busy_source):
    r = runtime
    if busy_source == "queued":
        timeline._QUEUE.append((str(r.job), "task"))
    elif busy_source == "working":
        timeline._WORKER["current"] = (str(r.job), "task")
    elif busy_source == "product":
        r.studio.PRODUCT_BUILD["proc"] = SimpleNamespace(poll=lambda: None)
    else:
        r.studio.FARM["thread"] = SimpleNamespace(is_alive=lambda: True)
    for path in ("/cr-connect", "/cr-recover"):
        assert r.client.post(path, headers=r.headers, json={}).status_code == 409
    # videos queue behind it: the dispatcher waits for this work before starting them (wait_for_editor)
    assert r.client.post("/api/run", headers=r.headers, json={}).status_code == 200
    assert r.client.post("/cr-submit-project", headers=r.headers, json={}).status_code == 400
    assert r.client.get("/f/video/cached", headers=r.headers).data == b"already saved"
    assert r.client.post("/api/products/example/reveal", headers=r.headers).status_code == 200
    assert r.client.get("/cr-status", headers=r.headers).status_code == 200


def test_stopped_editor_reply_clears_thinking_and_does_not_retry(runtime, timeline):
    r = runtime
    attempts = []

    def stop(*args):
        attempts.append(args)
        raise cr_studio.cr_claude.ClaudeStageStopped("Review this stopped stage in setup.")

    timeline.reply_handler = stop
    assert r.client.post("/api/tl/example/chat", headers=r.headers).status_code == 200
    worker = timeline.spawned.pop()
    worker.target(*worker.args)
    reply = timeline.state["chat"][0]
    assert reply["error"] and not reply["pending"]
    assert "new work" in reply["text"] and len(attempts) == 1
    assert not timeline.state["auto_render"]
    assert not r.studio._cr_activity.busy()
    assert r.client.post("/cr-recover", headers=r.headers, json={}).status_code == 200


@pytest.mark.parametrize("stop_type", [cr_studio.cr_claude.ClaudeStageStopped, cr_studio.cr_media.AccountStop])
def test_stopped_edit_task_is_failed_without_killing_the_native_worker(runtime, timeline, stop_type):
    attempts = []

    def stop(*args):
        attempts.append(args)
        raise stop_type("Review the saved task in setup.")

    timeline.task_handler = stop
    timeline._run_task(runtime.job, "stopped")
    assert timeline.state["tasks"][0]["state"] == "failed"
    assert not timeline.state["auto_render"] and not runtime.studio._cr_activity.busy()
    timeline.task_handler = lambda *args: timeline.calls.append(("native-next-task", args))
    timeline._run_task(runtime.job, "next")
    assert len(attempts) == 1 and timeline.calls[-1][0] == "native-next-task"


@pytest.mark.parametrize("message,after_render", [("", False), ("Queued original edit", True)])
def test_non_dispatched_chat_releases_its_reservation(runtime, timeline, message, after_render):
    timeline.chat(runtime.job, "example", message, {}, after_render=after_render)
    assert not runtime.studio._cr_activity.busy() and not timeline.spawned


def test_parallel_request_cannot_pass_the_context_check_while_a_request_holds_it(runtime):
    activity = runtime.studio._cr_activity
    acquired, finish = threading.Event(), threading.Event()

    def hold():
        with activity.lock:
            acquired.set()
            assert finish.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(5)
    try:
        response = runtime.client.post("/api/run", headers=runtime.headers, json={})
        assert response.status_code == 409 and "current studio request" in response.get_json()["error"]
    finally:
        finish.set()
        thread.join(5)
    assert runtime.client.post("/api/run", headers=runtime.headers, json={}).status_code == 200


def test_installed_original_chat_keeps_its_dispatch_and_selection_contract(tmp_path, monkeypatch):
    source = Path("/Users/workcomputer/Downloads/ChannelRecipe studio/timeline.py")
    if not source.is_file():
        pytest.skip("The owner's original private timeline is not installed here.")
    spec = importlib.util.spec_from_file_location("isolated_private_timeline", source)
    timeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(timeline)
    spawned, calls, notes = [], [], []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            spawned.append(self)

    def note(job, role, text, **kw):
        result = {"id": str(len(notes)), "role": role, "text": text, **kw}
        notes.append(result)
        return result

    monkeypatch.setattr(timeline, "threading", SimpleNamespace(Thread=DeferredThread))
    monkeypatch.setattr(timeline, "_note", note)
    monkeypatch.setattr(timeline, "_chat_reply", lambda *args: calls.append(args))
    studio = SimpleNamespace(timeline=timeline, RUNNING={}, QUEUE=[])
    monkeypatch.setattr(cr_studio.cr_workers, "busy", lambda: False)
    activity = cr_studio.CreativeActivity(studio)
    activity.install()
    selection = {"kind": "slots", "ids": [3, 5], "playhead": 9.5}
    response = timeline.chat(tmp_path, "existing-video", "  Use the original edit plan  ", selection)
    assert response == {"ok": True, "id": "0"} and activity.busy()
    worker = spawned.pop()
    worker.target(*worker.args)
    assert calls == [(tmp_path, "existing-video", "Use the original edit plan", selection, "1")]
    assert notes[1]["pending"] is True and not activity.busy()


@pytest.mark.parametrize("state", ["running", "queued"])
def test_only_the_same_original_timeline_render_accepts_deferred_requests(runtime, timeline, state):
    r = runtime
    if state == "running":
        r.studio.RUNNING["slug"] = "example"
        r.studio.JOBS["example"] = {"timeline": True}
    else:
        r.studio.QUEUE.append({"slug": "example", "timeline": True})
    # The guard passes through without creating a project. The original route
    # decides whether to defer, exactly as it did before CR was installed.
    assert r.client.post("/api/tl/example/chat", headers=r.headers).status_code == 200
    assert cr_studio.deferred_editor_request(r.studio, "/api/tl/example/op")
    assert not cr_studio.deferred_editor_request(r.studio, "/api/tl/other/chat")
    assert not cr_studio.deferred_editor_request(r.studio, "/api/tl/example/render")
    assert not cr_studio.deferred_editor_request(r.studio, "/api/tl/example/auto")


@pytest.mark.parametrize("state", ["running", "queued"])
def test_original_same_job_routes_queue_exact_chat_and_op_without_selecting_a_project(runtime, timeline, monkeypatch, state):
    r = runtime
    if state == "running":
        r.studio.RUNNING["slug"] = "example"
        r.studio.JOBS["example"] = {"timeline": True}
    else:
        r.studio.QUEUE.append({"slug": "example", "timeline": True})
    queued = []
    timeline.defer = lambda job, item: queued.append((job, item))
    namespace = {"request":request, "jsonify":jsonify, "timeline":timeline,
                 "_tl_job":lambda slug:(r.job, None), "_tl_busy":lambda slug:state}
    native_chat = original_function("api_tl_chat", "app.py", namespace)
    native_op = original_function("api_tl_op", "app.py", namespace)
    r.studio.app.view_functions["chat"] = lambda:native_chat("example")
    r.studio.app.view_functions["op"] = lambda:native_op("example")
    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", lambda *a, **k: pytest.fail("Deferring work must not select another project"))
    response = r.client.post("/api/tl/example/chat", headers=r.headers,
                             json={"message":"Exact queued message", "sel":{"kind":"range", "start":4}})
    assert response.status_code == 200 and response.get_json()["queued"]
    op = {"op":"trim", "slot":2, "start":1.5}
    response = r.client.post("/api/tl/example/op", headers=r.headers, json=op)
    assert response.status_code == 200 and "queued" in response.get_json()["results"][0]
    assert queued == [(r.job, {"type":"op", "op":op, "label":"trim"})]
    assert not timeline.spawned and not r.studio._cr_activity.active
    assert r.client.post("/api/tl/other/chat", headers=r.headers, json={}).status_code == 409


def test_regular_video_generation_does_not_accept_editor_deferrals(runtime):
    r = runtime
    r.studio.RUNNING["slug"] = "example"
    r.studio.JOBS["example"] = {"timeline": False}
    assert r.client.post("/api/tl/example/chat", headers=r.headers).status_code == 409


@pytest.fixture
def deferred(runtime, timeline, monkeypatch):
    r = runtime
    events = []
    timeline.state["deferred"] = [
        {"type": "op", "op": {"op": "trim", "slot": 2}},
        {"type": "chat", "message": "Keep this exact editor request", "sel": {"kind": "range", "start": 3}},
        {"type": "op", "op": {"op": "caption", "text": "Original words"}}]

    def note(job, role, text, **kw):
        events.append(("note", role, text))
        return {"id": "native-reply", **kw}

    timeline._note = note
    timeline.Path = Path
    timeline.run_ops = lambda job, ops, source="manual": events.append(("op", ops, source)) or [{"done": "native edit"}]
    timeline.release_deferred = original_function("release_deferred", "timeline.py", timeline.__dict__)
    timeline.reply_handler = lambda *args: events.append(("reply", args))
    spec = {"slug": "example", "title": "Original video title", "style": "history", "steps": ["video"], "minutes": 0, "timeline": True}
    setup = {"models_verified": True, "costs_accepted": True}

    def begin(*args, **kwargs):
        events.append(("begin", args, kwargs))
        return {"id": "crv_" + "a" * 24}

    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", begin)
    gate = cr_studio.DeferredRender(r.studio._cr_activity, spec, setup, "claudecode")
    return SimpleNamespace(runtime=r, timeline=timeline, events=events, gate=gate, spec=spec, setup=setup)


def test_original_deferred_order_waits_for_receipt_then_uses_a_fresh_project(deferred):
    d = deferred
    release = d.gate.release_deferred
    completed = threading.Event()
    result = []

    def run():
        result.append(release(d.runtime.job, "example"))
        completed.set()

    thread = threading.Thread(target=run)
    thread.start()
    assert not completed.wait(0.05) and not d.events
    d.events.append(("render-receipt-saved",))
    d.gate.close(True)
    assert completed.wait(3)
    thread.join(3)
    assert result == [3]
    assert d.events[0][0] == "render-receipt-saved" and d.events[1][0] == "begin"
    args, options = d.events[1][1:]
    assert args == ("Original video title", "history") and options["purpose"] == "creative"
    assert options["proof"] == d.setup and options["job_spec"]["minutes"] == 1
    assert [event[1][0] for event in d.events if event[0] == "op"] == [
        {"op": "trim", "slot": 2}, {"op": "caption", "text": "Original words"}]
    assert not d.timeline.state.get("deferred") and d.runtime.studio._cr_activity.busy()
    # Native deferred chat hasn't started yet. Its dispatch reservation still
    # blocks the next video's context and its account changes.
    assert d.runtime.client.post("/cr-connect", headers=d.runtime.headers, json={}).status_code == 409
    reply = d.timeline.spawned.pop()
    reply.target(*reply.args)
    assert d.events[-1] == ("reply", (d.runtime.job, "example", "Keep this exact editor request",
                                    {"kind": "range", "start": 3}, "native-reply"))
    assert not d.runtime.studio._cr_activity.busy()


@pytest.mark.parametrize("failure", ["receipt", "connection"])
def test_deferred_edits_stay_local_when_the_render_receipt_or_fresh_project_fails(deferred, monkeypatch, failure):
    d = deferred
    release = d.gate.release_deferred
    if failure == "connection":
        def failed(*args, **kwargs):
            raise RuntimeError("Connection failed")
        monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", failed)
    d.gate.close(failure != "receipt")
    assert release(d.runtime.job, "example") == 0
    assert len(d.timeline.state["deferred"]) == 3 and not d.timeline.spawned
    assert all(event[0] not in {"op", "reply"} for event in d.events)
    assert "still queued" in d.events[-1][2]
    assert not d.runtime.studio._cr_activity.busy()


def test_next_queued_video_waits_for_original_editor_work(deferred):
    d = deferred
    d.gate.close(True)
    token = d.runtime.studio._cr_activity.reserve()
    entered = threading.Event()
    waiting = threading.Thread(target=lambda: (d.runtime.studio._cr_activity.wait_for_editor(), entered.set()))
    waiting.start()
    assert not entered.wait(0.05)
    d.runtime.studio._cr_activity.release(token)
    assert entered.wait(3)
    waiting.join(3)


def test_same_job_editor_queue_drains_before_the_next_render_is_claimed(runtime, timeline):
    r = runtime
    r.studio._tl_busy = lambda slug:"running"
    timeline._QUEUE.append((str(r.job), "native-task"))
    entered = threading.Event()
    waiting = threading.Thread(target=lambda: (r.studio._cr_activity.wait_for_editor(), entered.set()))
    waiting.start()
    deadline = time.monotonic() + 3
    while not r.studio._cr_activity.waiting_editor and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not r.studio._cr_activity.rendering(r.job)
    with timeline._QCOND:
        timeline._QUEUE.clear()
    assert entered.wait(3)
    waiting.join(3)
    assert r.studio._cr_activity.rendering(r.job)


def test_original_render_dispatch_cannot_release_edits_before_its_completion_receipt(deferred):
    d = deferred
    studio = d.runtime.studio
    studio.JOBS["example"] = {}
    studio.RUNNING["slug"] = "example"
    render_entered = threading.Event()
    render_finish = threading.Event()
    d.timeline.render = lambda *args, **kwargs: (render_entered.set(), render_finish.wait(3))
    original = original_function("_run_spec", "app.py", {
        "JOBS": studio.JOBS, "RUNNING": studio.RUNNING, "HISTORY": [], "LOCK": threading.Lock(),
        "timeline": d.gate, "mv": SimpleNamespace(), "threading": threading, "time": time, "Path": Path})
    complete = threading.Event()
    source_render = threading.Thread(target=lambda: (original({**d.spec, "job":str(d.runtime.job)}), complete.set()))
    source_render.start()
    assert render_entered.wait(3)
    render_finish.set()
    assert complete.wait(3)
    source_render.join(3)
    assert studio.JOBS["example"]["state"] == "done" and not d.events
    assert len(d.timeline.state["deferred"]) == 3
    d.events.append(("render-receipt-saved",))
    d.gate.close(True)
    deadline = time.monotonic() + 3
    while d.timeline.state.get("deferred") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not d.timeline.state.get("deferred")
    assert d.events[0][0] == "render-receipt-saved" and d.events[1][0] == "begin"
    for reply in d.timeline.spawned:
        reply.target(*reply.args)
    assert not studio._cr_activity.busy()


@pytest.mark.parametrize("receipt_fails", [False, True])
def test_cr_run_spec_records_the_render_before_original_deferred_dispatch(deferred, monkeypatch, receipt_fails):
    d = deferred
    r = d.runtime
    d.gate.close(False)  # The actual CR run_spec creates and owns its own gate.
    r.cfg.update(brain_mode="claudecode", setup_proof=d.setup)
    r.studio.JOBS["example"] = {}
    r.studio.RUNNING["slug"] = "example"
    (r.job / "video.mp4").write_bytes(b"existing cached render")
    r.studio.mv.job_dir = lambda *a:r.job
    r.studio.mv.final_video = lambda *a:r.job / "video.mp4"
    d.timeline.render = lambda *a, **k:d.events.append(("native-cached-render",))
    original = original_function("_run_spec", "app.py", {
        "JOBS":r.studio.JOBS, "RUNNING":r.studio.RUNNING, "HISTORY":[], "LOCK":threading.Lock(),
        "timeline":d.timeline, "mv":r.studio.mv, "threading":threading, "time":time, "Path":Path})
    # Keep create_app's real CR wrapper, swapping only its fake native function
    # for the owner's original function loaded directly into isolated globals.
    cells = dict(zip(r.studio._run_spec.__code__.co_freevars, r.studio._run_spec.__closure__))
    if "run_owned_spec" in cells:
        owned = cells["run_owned_spec"].cell_contents
        cells = dict(zip(owned.__code__.co_freevars, owned.__closure__))
    monkeypatch.setattr(cells["original_run"], "cell_contents", original)
    monkeypatch.setattr(cr_studio.cr_readiness, "proof", lambda *a, **k:d.setup)
    monkeypatch.setattr(cr_studio, "output_artifacts", lambda *a:[
        {"name":"video.mp4", "sha256":"f"*64, "media_type":"video/mp4"}])

    def begin(*args, **kwargs):
        d.events.append(("begin", args, kwargs))
        return {"id":"crv_" + ("b" if len([e for e in d.events if e[0] == "begin"]) == 1 else "a") * 24}

    def finish(report):
        d.events.append(("render-receipt", report))
        if receipt_fails:
            raise RuntimeError("Receipt connection interrupted")
        return {"saved":True}

    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", begin)
    monkeypatch.setattr(cr_studio.cr_bridge, "finish_project", finish)
    r.studio.LOCK = threading.Lock()
    r.studio._run_spec({**d.spec, "job":str(r.job)})
    deadline = time.monotonic() + 3
    while not any(event[0] == ("note" if receipt_fails else "op") for event in d.events) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [event[0] for event in d.events[:3]] == ["begin", "native-cached-render", "render-receipt"]
    receipt = d.events[2][1]
    assert receipt["project_id"] == "crv_" + "b" * 24 and receipt["status"] == "done"
    if receipt_fails:
        assert len([event for event in d.events if event[0] == "begin"]) == 1
        assert len(d.timeline.state["deferred"]) == 3 and not d.timeline.spawned
        assert (r.job / ".cr-finish-pending.json").is_file()
    else:
        assert d.events[3][0] == "begin" and d.events[3][2]["purpose"] == "creative"
        assert (r.job / ".cr-finish.json").is_file()
        assert not d.timeline.state.get("deferred")
        for reply in d.timeline.spawned:
            reply.target(*reply.args)
    assert not r.studio._cr_activity.busy()


def test_one_click_connect_saves_the_key_from_the_approval_round_trip(runtime, monkeypatch):
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlencode, urlsplit
    import requests

    r = runtime
    assert r.client.post("/cr-connect/start", headers=r.headers, json={}).status_code == 400
    url = r.client.post("/cr-connect/start", headers=r.headers, json={"costs_accepted": True}).json["url"]
    assert url.startswith("https://channelrecipe.com/studio/connect?")
    query = parse_qs(urlsplit(url).query)
    sent = {}

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"key": "cr_mcp_" + "n" * 30}

    def post(target, json, timeout):
        sent.update(url=target, **json)
        return Reply()

    monkeypatch.setattr(requests, "post", post)
    back = urlencode({"code": "signed-code", "state": query["state"][0]})
    # A plain browser visit from channelrecipe.com: no local token is involved.
    response = r.client.get("/cr-connect/return?" + back, headers={"Host": "127.0.0.1:7860"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/cr-setup?connect=done")
    assert sent["url"] == "https://channelrecipe.com/api/studio/connect/exchange" and sent["code"] == "signed-code"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert challenge == query["challenge"][0]
    assert r.cfg["key"] == "cr_mcp_" + "n" * 30 and r.cfg["costs_accepted"] is True
    # Each request can be completed once; unknown requests never reach the server.
    sent.clear()
    again = r.client.get("/cr-connect/return?" + back, headers={"Host": "127.0.0.1:7860"})
    assert again.headers["Location"].endswith("/cr-setup?connect=expired") and sent == {}


def test_in_app_update_downloads_in_the_background_and_restarts_only_when_idle(runtime, timeline, monkeypatch):
    import cr_update
    r = runtime
    assert r.client.post("/cr-update/restart", headers=r.headers, json={}).status_code == 409
    monkeypatch.setattr(cr_update, "prepare", lambda home, progress=None: (progress(5, 10), {
        "state": "staged", "version": "5.3-admin.99", "files": 3})[1])
    r.client.post("/cr-update/download", headers=r.headers, json={})
    for _ in range(50):
        status = r.client.get("/cr-update", headers=r.headers).json
        if status["state"] != "downloading":
            break
        time.sleep(0.02)
    assert status["state"] == "staged" and status["version"] == "5.3-admin.99" and status["done"] == 5
    # Busy: a running task means no restart, and the update waits.
    r.client.post("/api/tl/example/chat", headers=r.headers)
    assert r.studio._cr_activity.busy()
    assert r.client.post("/cr-update/restart", headers=r.headers, json={}).status_code == 409
    original = timeline.spawned.pop()
    original.target(*original.args)
    assert not r.studio._cr_activity.busy()
    restarting = r.client.post("/cr-update/restart", headers=r.headers, json={})
    assert restarting.status_code == 200 and restarting.json["state"] == "restarting"
    # Nothing new can start while Studio restarts.
    assert r.client.post("/api/run", headers=r.headers, json={}).status_code == 409


def test_voice_picker_lists_previews_once_and_saves_the_choice(runtime, timeline, monkeypatch):
    r = runtime
    r.cfg["media_hub"] = {"provider": "kie", "tasks": ["narration"], "voice": "Kore"}
    monkeypatch.setattr(cr_studio.cr_media, "profile", lambda task=None: dict(r.cfg["media_hub"]) if task == "narration" else {})
    monkeypatch.setattr(cr_studio.cr_media, "active", lambda task: task == "narration")
    monkeypatch.setitem(cr_studio.cr_media.PROVIDERS["kie"]["models"], "narration", "google/gemini-3-8-flash-tts")
    listed = r.client.get("/cr-voices", headers=r.headers).json
    assert listed["voice"] == "Kore" and "Charon" in listed["voices"] and listed["previews"] == []
    made = []

    def run(task, inputs, dest, **kwargs):
        made.append((task, inputs["speakers"][0]["voice_name"]))
        assert kwargs.get("retry") is False, "a page waits on the preview: one try, no patient retries"
        Path(dest).write_bytes(b"ID3 sample")

    monkeypatch.setattr(cr_studio.cr_media, "run", run)
    import requests

    def unreachable(*a, **k):                                  # channelrecipe.com's free sample can't be reached
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(requests, "get", unreachable)
    # no free sample: nothing is paid until the creator says yes to the cost, voice by voice
    asked = r.client.post("/cr-voices/preview", headers=r.headers, json={"voice": "Charon"})
    assert asked.status_code == 200 and asked.json["needs_paid"] is True and asked.json["cost_usd"] == 0.01
    assert asked.json["message"] == "Free sample unavailable: make one with your Kie.ai balance (about $0.01)?"
    assert r.client.post("/cr-voices/preview", headers=r.headers, json={"voice": "Kore"}).json["needs_paid"] is True
    assert made == [], "a click without a yes never pays, for any voice"
    first = r.client.post("/cr-voices/preview", headers=r.headers, json={"voice": "Charon", "confirm_paid": True})
    assert first.status_code == 200 and first.json["url"] == "/cr-voices/sample/Charon" and first.json["paid"] is True
    assert r.client.post("/cr-voices/preview", headers=r.headers, json={"voice": "Charon"}).json["paid"] is False
    assert made == [("narration", "Charon")], "each voice's sample is paid for once, after a yes"
    sample = r.client.get("/cr-voices/sample/Charon", headers=r.headers)
    assert sample.status_code == 200 and sample.data == b"ID3 sample"
    assert r.client.post("/cr-voices/preview", headers=r.headers, json={"voice": "NotAVoice"}).status_code == 400
    assert "Charon" in r.client.get("/cr-voices", headers=r.headers).json["previews"]
    # Choosing changes only the voice, in the account that narrates.
    chosen = r.client.post("/cr-voices/select", headers=r.headers, json={"voice": "Charon"})
    assert chosen.status_code == 200 and r.cfg["media_hub"] == {"provider": "kie", "tasks": ["narration"], "voice": "Charon"}
    # Never mid-video.
    r.client.post("/api/tl/example/chat", headers=r.headers)
    assert r.client.post("/cr-voices/select", headers=r.headers, json={"voice": "Kore"}).status_code == 409
    original = timeline.spawned.pop()
    original.target(*original.args)


def test_voice_previews_use_channelrecipes_free_sample_first(runtime, monkeypatch):
    """Kie narrates with Gemini voices (owner, 2026-10-26). Each of its 70 voices has a free sample on
    channelrecipe.com: a preview fetches it once and keeps it, and the creator's Kie balance is never used for it."""
    r = runtime
    r.cfg["media_hub"] = {"provider": "kie", "tasks": ["narration"], "voice": "Kore"}
    monkeypatch.setattr(cr_studio.cr_media, "profile", lambda task=None: dict(r.cfg["media_hub"]) if task == "narration" else {})
    monkeypatch.setattr(cr_studio.cr_media, "active", lambda task: task == "narration")
    monkeypatch.setattr(cr_studio.cr_media, "run", lambda *a, **k: pytest.fail("a free sample is never paid for"))
    import requests
    fetched = []

    def get(url, **kwargs):
        fetched.append(url)
        return SimpleNamespace(status_code=200, content=b"ID3" + b"x" * 4000, headers={"content-type": "audio/mpeg"})
    monkeypatch.setattr(requests, "get", get)
    listed = r.client.get("/cr-voices", headers=r.headers).json
    assert len(listed["voices"]) == 70 and listed["groups"]["Charon"] == "Call center"
    assert listed["note"].startswith("Kie narrates with Gemini voices.") and "your own ElevenLabs key" in listed["note"]
    first = r.client.post("/cr-voices/preview", headers=r.headers, json={"voice": "Puck"})
    assert first.status_code == 200 and first.json == {"url": "/cr-voices/sample/Puck", "paid": False}
    assert fetched == ["https://channelrecipe.com/static/voices/gemini-3-8-flash-tts/Puck.mp3"]
    r.client.post("/cr-voices/preview", headers=r.headers, json={"voice": "Puck"})
    assert len(fetched) == 1, "kept after the first play"
    sample = r.client.get("/cr-voices/sample/Puck", headers=r.headers)
    assert sample.status_code == 200 and sample.data.startswith(b"ID3") and sample.mimetype == "audio/mpeg"
    assert "Puck" in r.client.get("/cr-voices", headers=r.headers).json["previews"]


def test_kie_narration_is_gemini_in_setup_and_choose_voice(runtime, monkeypatch):
    """Kie narrates with Gemini voices only (owner, 2026-10-26). Setup says so; a moved ElevenLabs voice says so once,
    until the creator picks a voice; saves never keep a Kie ElevenLabs model or voice."""
    import cr_media
    r = runtime
    monkeypatch.setenv("KIE_API_KEY", "k" * 30)
    r.cfg.update(narration={"provider": "kie", "model": "elevenlabs/text-to-speech-turbo-2-5", "voice": "EkK5I93UQWFDigLMpZcX"})
    real = cr_media.kie_narration
    monkeypatch.setattr(cr_studio.cr_media, "profile",
                        lambda task=None: real({**r.cfg["narration"], "tasks": ["narration"]}) if task == "narration" else {})
    monkeypatch.setattr(cr_studio.cr_media, "active", lambda task: task == "narration")
    monkeypatch.setattr(cr_studio.cr_network, "status", lambda *a, **k: {})
    monkeypatch.setattr(cr_studio, "channelrecipe_answers", lambda *a: True)
    status = r.client.get("/cr-status", headers=r.headers).json["narration"]
    assert status["voice"] == "Charon" and status["model"] == "google/gemini-3-8-flash-tts"
    assert status["note"].startswith("Kie now narrates with Gemini voices only") and "James" in status["note"]
    listed = r.client.get("/cr-voices", headers=r.headers).json
    assert listed["voice"] == "Charon" and len(listed["voices"]) == 70 and "James" in listed["note"]
    chosen = r.client.post("/cr-voices/select", headers=r.headers, json={"voice": "Puck"})
    assert chosen.status_code == 200 and "voice_notice" not in r.cfg
    assert r.cfg["narration"] == {"provider": "kie", "model": "google/gemini-3-8-flash-tts", "voice": "Puck"}
    assert r.client.get("/cr-status", headers=r.headers).json["narration"]["note"] == cr_media.KIE_NOTE
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"code": 200, "data": 10}))
    # an old setup page (or Claude Code) asking for the Turbo model gets Gemini; an ElevenLabs id is refused plainly
    saved = r.client.post("/cr-voice", headers=r.headers, json={"provider": "kie", "model": "elevenlabs/text-to-speech-turbo-2-5"})
    assert saved.status_code == 200 and "Gemini voices" in saved.json["message"]
    assert r.cfg["narration"] == {"provider": "kie", "model": "google/gemini-3-8-flash-tts", "voice": "Kore"}
    refused = r.client.post("/cr-hub", headers=r.headers, json={"provider": "kie", "key": "k" * 30, "tasks": ["narration"],
                                                                "voice": "EkK5I93UQWFDigLMpZcX"})
    assert refused.status_code == 400 and "Kie narrates with Gemini voices" in refused.json["error"]
    assert r.client.post("/cr-hub", headers=r.headers, json={"provider": "kie", "key": "k" * 30, "tasks": ["narration"],
                                                             "voice": "charon"}).status_code == 200
    assert r.cfg["media_hub"]["voice"] == "Charon"


def test_copy_diagnostics_explains_setup_without_secrets(runtime):
    r = runtime
    cr_studio.RECENT_OUTPUT.clear()
    cr_studio.RECENT_OUTPUT.extend([
        '127.0.0.1 - - [14/Oct/2026 19:00:00] "GET /api/queue HTTP/1.1" 200 -',
        "Style study request /cr-style-clone/preview failed",
        "connecting with key cr_mcp_" + "s" * 30,
        "Traceback (most recent call last): " + str(Path.home()) + "/studio/app.py"])
    assert r.client.get("/cr-diagnostics", headers={"Host": "127.0.0.1:7860"}).status_code == 403
    text = r.client.get("/cr-diagnostics", headers=r.headers).get_data(as_text=True)
    assert text.startswith("ChannelRecipe Studio diagnostics")
    assert "ChannelRecipe account connected: yes" in text and "Tools: Python yes" in text
    assert "Style study request /cr-style-clone/preview failed" in text
    assert "cr_mcp_" not in text and "[hidden]" in text, "keys never leave the computer"
    assert '"GET /api/queue' not in text, "page polling isn't noise in a support request"
    assert str(Path.home()) not in text


def test_a_second_start_opens_the_running_studio_instead_of_crashing(monkeypatch):
    import cr_update
    opened = []
    monkeypatch.setattr(cr_update, "studio_running", lambda: True)
    monkeypatch.setattr(cr_studio, "already_running", lambda: True)
    monkeypatch.setattr(cr_studio.webbrowser, "open", opened.append)
    monkeypatch.setattr(cr_studio, "create_app", lambda: pytest.fail("a second Studio must not start"))
    monkeypatch.delenv("CR_RESTARTED_FOR_UPDATE", raising=False)
    monkeypatch.setattr(cr_studio.sys, "stdout", cr_studio.sys.stdout)
    monkeypatch.setattr(cr_studio.sys, "stderr", cr_studio.sys.stderr)
    cr_studio.main()
    assert opened == ["http://127.0.0.1:7860/"]
    monkeypatch.setattr(cr_studio, "already_running", lambda: False)
    with pytest.raises(SystemExit):
        cr_studio.main()


class _Answer:
    def __init__(self, status, content=b"", text=""):
        self.status_code, self.content, self.text = status, content, text


def test_own_elevenlabs_narration_is_checked_by_one_spoken_line(runtime, monkeypatch):
    r = runtime
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")               # restored after the test (the endpoint saves the key)
    asked = []

    def post(url, headers=None, json=None, timeout=None):
        asked.append(json["model_id"])
        if json["model_id"] == "eleven_v4":
            return _Answer(422, text='{"detail": {"message": "model eleven_v4 is not available for this account"}}')
        return _Answer(200, content=b"ID3 spoken line")

    monkeypatch.setattr("requests.post", post)
    done = r.client.post("/cr-voice/elevenlabs", headers=r.headers, json={"key": "sk_" + "a" * 48})
    assert done.status_code == 200 and done.json["model"] == "eleven_v3", "an account without v4 falls back to v3"
    assert asked == ["eleven_v4", "eleven_v3"]
    assert done.json["sample"].startswith("data:audio/mpeg;base64,")
    assert r.cfg["narration"] == {"provider": "original"}, "the studio's own voice settings read the scripts"
    assert "ELEVENLABS_API_KEY" in (r.job / ".env").read_text()


def test_restricted_elevenlabs_key_is_refused_and_nothing_changes(runtime, monkeypatch):
    r = runtime
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    r.cfg["narration"] = {"provider": "kie", "model": "elevenlabs/text-to-speech-turbo-2-5"}
    monkeypatch.setattr("requests.post", lambda *a, **k: _Answer(
        401, text='{"detail": {"message": "The API key you used is missing the permission text_to_speech"}}'))
    refused = r.client.post("/cr-voice/elevenlabs", headers=r.headers, json={"key": "sk_" + "b" * 48})
    assert refused.status_code == 400 and "Text to Speech" in refused.json["error"]
    assert r.cfg["narration"]["provider"] == "kie"
    assert not (r.job / ".env").exists() or "ELEVENLABS_API_KEY" not in (r.job / ".env").read_text()


def test_a_studio_started_from_a_claude_code_chat_drops_the_chats_settings(monkeypatch):
    chat = {"CLAUDE_EFFORT": "xhigh", "CLAUDECODE": "1", "CLAUDE_PID": "123", "CLAUDE_CODE_ENTRYPOINT": "sdk-ts",
            "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/x.sock", "CLAUDE_CODE_USER_EMAIL": "a@b.c",
            "CLAUDE_AGENT_SDK_VERSION": "1", "CLAUDE_CODE_SIMPLE": "1", "MCP_CONNECTION_NONBLOCKING": "1"}
    own = {"CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_GIT_BASH_PATH": "C:/git/bash.exe",
           "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000", "ANTHROPIC_API_KEY": "k", "PATH": "/usr/bin", "CLAUDE_PROVIDER": "claudecode"}
    environ = {**chat, **own}
    gone = cr_studio.forget_claude_chat(environ)
    assert environ == own, "the login and the customer's own Claude settings stay"
    assert "CLAUDE_EFFORT=xhigh" in gone and "CLAUDE_CODE_USER_EMAIL" in gone
    assert not any("a@b.c" in x for x in gone), "only names leave, never a personal value"
    monkeypatch.setattr(cr_studio, "CHAT_SETTINGS_DROPPED", gone)
    assert cr_studio.chat_line() == "yes, its settings were left out (CLAUDE_EFFORT=xhigh)"
    monkeypatch.setattr(cr_studio, "CHAT_SETTINGS_DROPPED", [])
    assert cr_studio.chat_line() == "no"


def test_setup_fix_routes_start_studios_own_installs_behind_the_local_token(runtime, monkeypatch):
    """Setup's Fix it for FFmpeg, Node.js and Claude Code (cr_installs): a POST starts it (local token only), a GET
    reads its progress; the sign-in window says why it can't open."""
    import cr_installs
    started = []
    for name in ("fix_ffmpeg", "fix_node"):
        monkeypatch.setattr(cr_installs, name, lambda name=name, **kw: started.append((name, kw)) or {"state": "starting"})
    monkeypatch.setattr(cr_installs, "fix_claude", lambda **kw: started.append(("fix_claude", kw)) or {"state": "starting"})
    for path in ("/cr-setup/fix-ffmpeg", "/cr-setup/fix-node", "/cr-setup/fix-claude"):
        assert runtime.client.post(path, headers={"Host": "127.0.0.1:7860"}).status_code == 403, "never without the token"
        response = runtime.client.post(path, headers=runtime.headers)
        assert response.status_code == 202 and response.json == {"state": "starting"}
    assert [name for name, _ in started] == ["fix_ffmpeg", "fix_node", "fix_claude"]
    assert started[0][1]["home"] == runtime.job and started[1][1]["home"] == runtime.job
    busy = started[0][1]["busy"]                    # never switch FFmpeg or Node.js under a video being made
    assert busy() is False
    runtime.studio.RUNNING["slug"] = "a-video"
    assert busy() is True and started[1][1]["busy"]() is True
    runtime.studio.RUNNING.clear()
    monkeypatch.setitem(cr_installs.FIXES, "ffmpeg", {"state": "downloading", "done": 10 << 20, "total": 57 << 20,
                                                      "what": "FFmpeg 9.0.2", "message": "", "tail": ""})
    assert "10 of 57 MB" in runtime.client.get("/cr-setup/fix-ffmpeg", headers=runtime.headers).json["message"]
    monkeypatch.setattr(cr_studio.cr_claude, "find_executable", lambda: "/Users/zoe/.local/bin/claude")
    monkeypatch.setattr(cr_studio.cr_claude, "status", lambda *a, **k: {"authenticated": True})
    answer = runtime.client.get("/cr-setup/fix-claude?signin=1", headers=runtime.headers).json
    assert answer["installed"] is True and answer["signed_in"] is True
    assert "signed_in" not in runtime.client.get("/cr-setup/fix-claude", headers=runtime.headers).json

    def cant(**kw):
        raise cr_installs.Stop("Install Claude Code first: press Install Claude Code.")
    monkeypatch.setattr(cr_installs, "open_sign_in", cant)
    refused = runtime.client.post("/cr-setup/claude-sign-in", headers=runtime.headers)
    assert refused.status_code == 409 and "Install Claude Code first" in refused.json["error"]
    status = runtime.client.get("/cr-status", headers=runtime.headers).json
    assert status["ffmpeg"] is True and status["node"] is True and set(status["can_fix"]) == {"ffmpeg", "node", "claude"}
