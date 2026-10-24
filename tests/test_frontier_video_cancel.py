"""Owned video cancellation never stops the studio or replays paid work."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from flask import Flask, g, jsonify, request
import pytest

RUNTIME = Path(__file__).parents[1] / "local_runtime"
sys.path.insert(0, str(RUNTIME))
import cr_studio
import cr_submission
import cr_video_cancel as cancel
import cr_workers

PROJECT = "crv_" + "a" * 24
RUN = "video_" + "b" * 24


def fixture_studio(tmp_path):
    (tmp_path / "app.py").write_text("# isolated original source fixture")
    return SimpleNamespace(app=Flask(__name__), LOCK=threading.Lock(), QUEUE=[],
                           JOBS={}, HISTORY=[], RUNNING={"slug": "selected"})


def spec(**values):
    return {"slug": "selected", "title": "Selected", "steps": ["video"], "style": "documentary",
            "minutes": 1, "cr_video_run_id": RUN, **values}


def test_individual_queued_cancel_preserves_other_jobs_and_completed_files(tmp_path):
    studio = fixture_studio(tmp_path)
    studio.RUNNING["slug"] = None
    first, other = spec(cr_project_id=PROJECT), spec(slug="other", cr_video_run_id="video_" + "c" * 24)
    studio.QUEUE.extend([first, other])
    studio.JOBS["selected"] = {"state": "queued"}
    saved = tmp_path / "output" / "saved.mp4"
    saved.parent.mkdir()
    saved.write_bytes(b"completed media")
    manager = cancel.VideoWorkers(studio, tmp_path)
    assert manager.cancel("selected", RUN)["cancelled"]
    assert studio.QUEUE == [other] and studio.JOBS["selected"]["state"] == "cancelled"
    assert saved.read_bytes() == b"completed media"
    report = json.loads((tmp_path / "output/cr-video-workers" / RUN / ".cr-finish-pending.json").read_text())
    assert report == {"project_id": PROJECT, "status": "failed", "artifacts": [], "error_code": "cancelled"}
    assert manager.cancel("selected", RUN)["cancelled"]
    with pytest.raises(ValueError):
        manager.cancel("other", RUN)


def test_stale_run_id_cannot_cancel_later_video_with_same_slug(tmp_path):
    studio = fixture_studio(tmp_path)
    studio.QUEUE.append(spec(cr_video_run_id="video_" + "c" * 24))
    manager = cancel.VideoWorkers(studio, tmp_path)
    with pytest.raises(ValueError, match="no longer"):
        manager.cancel("selected", RUN)
    assert len(studio.QUEUE) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group contract")
def test_running_cancel_stops_owned_group_and_preserves_uncertain_native_receipts(tmp_path):
    studio = fixture_studio(tmp_path)
    native = tmp_path / "output/cr-claude-cache" / PROJECT
    native.mkdir(parents=True)
    (native / "complete.json").write_text('{"state":"complete","stdout":"cached original answer"}')
    (native / "running.json").write_text('{"state":"running","input_fingerprint":"unchanged"}')
    ready = tmp_path / "child.pid"
    # An isolated fake media pipeline, never the user's runtime or paid model.
    program = "import subprocess,sys,time,pathlib; child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);pathlib.Path(sys.argv[1]).write_text(str(child.pid));time.sleep(120)"
    spawned = []
    def popen(command, **kw):
        assert command[-3:] == [str(tmp_path / "app.py"), "--cr-video", RUN]
        assert command[2:4] == ["--project", PROJECT]
        assert kw["start_new_session"] is True
        child = subprocess.Popen([sys.executable, "-c", program, str(ready)], **kw)
        spawned.append(child)
        return child
    manager = cancel.VideoWorkers(studio, tmp_path, popen=popen)
    runner = threading.Thread(target=manager.run, args=(spec(), PROJECT))
    runner.start()
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    assert manager.info("selected")["can_cancel"]
    assert manager.cancel("selected", RUN)["cancelled"]
    runner.join(5)
    assert not runner.is_alive() and spawned[0].poll() is not None
    assert manager.current is None and studio.RUNNING["slug"] is None
    assert studio.JOBS["selected"]["state"] == "cancelled" and studio.JOBS["selected"]["done"]
    assert json.loads((native / "running.json").read_text())["state"] == "running"
    assert json.loads((native / "complete.json").read_text())["stdout"] == "cached original answer"
    assert manager.cancel("selected", RUN)["cancelled"] and len(spawned) == 1
    # A terminated descendant can briefly be a zombie, but is no longer running.
    process = subprocess.run(["ps", "-o", "stat=", "-p", ready.read_text()], capture_output=True, text=True)
    assert not process.stdout.strip() or process.stdout.strip().startswith("Z")


def test_failed_stop_keeps_active_worker_and_no_false_cancel_receipt(tmp_path):
    studio = fixture_studio(tmp_path)
    process = SimpleNamespace(poll=lambda: None)
    manager = cancel.VideoWorkers(studio, tmp_path, stop=lambda _: (_ for _ in ()).throw(OSError()))
    manager.current = {"slug": "selected", "run_id": RUN, "process": process, "cancel_requested": False}
    with pytest.raises(RuntimeError, match="remains busy"):
        manager.cancel("selected", RUN)
    assert manager.current["process"] is process and manager.info("selected")["can_cancel"]
    assert studio.RUNNING["slug"] == "selected"
    assert not (tmp_path / "output/cr-video-workers" / RUN / "cancelled.json").exists()


def test_spawn_failure_is_final_error_and_releases_original_dispatcher(tmp_path):
    studio = fixture_studio(tmp_path)
    manager = cancel.VideoWorkers(studio, tmp_path, popen=lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    manager.run(spec(), PROJECT)
    assert studio.JOBS["selected"]["state"] == "error" and studio.JOBS["selected"]["done"]
    assert studio.RUNNING["slug"] is None and manager.current is None


def test_actual_original_pipeline_runs_unchanged_in_owned_worker(tmp_path):
    path = Path("/Users/workcomputer/Downloads/ChannelRecipe studio/app.py")
    if not path.is_file():
        pytest.skip("The owner's private source is not installed")
    studio = fixture_studio(tmp_path)
    job = tmp_path / "original-job"
    calls = []
    def original_generation(*args, **kw):
        calls.append((args, kw))
        kw["sink"]("Original paid stages would be here; this fixture generates nothing")
    engine = SimpleNamespace(job_dir=lambda *a: job, run_custom=original_generation)
    studio.mv = engine
    namespace = {"JOBS": studio.JOBS, "RUNNING": studio.RUNNING, "HISTORY": studio.HISTORY,
                 "LOCK": studio.LOCK, "mv": engine, "time": time, "Path": Path, "json": json}
    definition = next(node for node in ast.parse(path.read_text()).body
                      if isinstance(node, ast.FunctionDef) and node.name == "_run_spec")
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), "exec"), namespace)
    studio._run_spec = namespace["_run_spec"]
    payload = spec(script_text="Original script", force=False, burn_subs=True, thumb_count=0,
                   features={"youtube": True, "threed": True}, extra="Original creative direction")
    folder, spec_path, progress = cancel.worker_files(tmp_path, RUN)
    cancel._write(spec_path, {"project_id": PROJECT, "spec": payload})
    cancel.run_original_worker(tmp_path, PROJECT, RUN, studio)
    result = json.loads(progress.read_text())
    assert result["job"]["state"] == "done" and result["job"]["done"]
    assert calls[0][0] == ("Selected", 1)
    assert calls[0][1]["features"] == payload["features"] and calls[0][1]["script_text"] == "Original script"
    assert calls[0][1]["extra"] == "Original creative direction"
    assert result["job"]["lines"] == ["Original paid stages would be here; this fixture generates nothing"]
    with pytest.raises(RuntimeError, match="owned project"):
        cancel.run_original_worker(tmp_path, "crv_" + "d" * 24, RUN, studio)


def test_cancel_routes_use_existing_loopback_origin_and_private_token_guard(tmp_path):
    studio = fixture_studio(tmp_path)
    manager = cancel.VideoWorkers(studio, tmp_path)
    manager.install_routes()
    node = next(node for node in ast.walk(ast.parse((RUNTIME / "cr_studio.py").read_text()))
                if isinstance(node, ast.FunctionDef) and node.name == "local_access")
    node.decorator_list = []
    ns = {"request": request, "jsonify": jsonify, "g": g, "token": "private-local-token", "studio": studio,
          "activity": SimpleNamespace(), "deferred_editor_request": cr_studio.deferred_editor_request,
          "creative_request": cr_studio.creative_request}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "actual-local-guard", "exec"), ns)
    studio.app.before_request(ns["local_access"])
    studio.QUEUE.append(spec())
    studio.JOBS["selected"] = {"state": "queued"}
    client = studio.app.test_client()
    route = "/cr-video/selected/cancel"
    payload = {"run_id": RUN}
    assert client.post(route, json=payload, headers={"Host": "127.0.0.1:7860"}).status_code == 403
    headers = {"Host": "127.0.0.1:7860", "X-CR-Local-Token": "private-local-token"}
    assert client.post(route, json=payload, headers={**headers, "Origin": "https://external.test"}).status_code == 403
    assert client.post(route, json=payload, headers={**headers, "Host": "external.test"}).status_code == 403
    assert len(studio.QUEUE) == 1
    assert client.post(route, json=payload, headers=headers).status_code == 200
    assert not studio.QUEUE


def test_timeline_and_unowned_original_jobs_offer_no_cancellation(tmp_path):
    studio = fixture_studio(tmp_path)
    manager = cancel.VideoWorkers(studio, tmp_path)
    studio.QUEUE.extend([spec(timeline=True), spec(slug="original", cr_video_run_id="")])
    assert not manager.info("selected")["can_cancel"] and not manager.info("original")["can_cancel"]
    with pytest.raises(ValueError):
        manager.cancel("selected", RUN)


def test_nested_cli_and_media_children_cannot_leave_worker_group(monkeypatch):
    captured = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(cr_workers, "_contained", True)
    monkeypatch.setattr(cr_workers, "_installed", False)
    monkeypatch.setattr(cr_workers, "_original_popen", lambda cmd, *a, **kw: captured.append((cmd, kw)))
    cr_workers.install()
    try:
        subprocess.Popen(["claude", "--print"], start_new_session=True, process_group=123)
        subprocess.Popen(["ffmpeg", "-version"], start_new_session=True)
    finally:
        cr_workers.uninstall()
        subprocess.Popen = real_popen
    assert len(captured) == 2
    if os.name != "nt":
        assert all(not kw["start_new_session"] and "process_group" not in kw for _, kw in captured)


@pytest.fixture
def original_progress_page():
    api = pytest.importorskip("playwright.sync_api")
    source = Path("/Users/workcomputer/Downloads/ChannelRecipe studio/ui.html")
    if not source.is_file():
        pytest.skip("The owner's private UI is not installed")
    text = source.read_text()
    original_tick = text.split("async function tick(){", 1)[1].split("/* ── the render as stages", 1)[0]
    screen = '''<!doctype html><html><head></head><body><textarea id="title">Preserve the user's title</textarea>
<section id="run"><div class="run-top"><span class="spin"></span><b id="runTitle"></b><span id="runEl"></span></div>
<div id="runBar"></div><div id="runLook"></div><div id="runVideo"></div><pre id="log"></pre><div id="queue"></div></section>
<script>const $=value=>document.querySelector(value);let WATCH='selected';
const api=async url=>(await fetch(url)).json(),fmt=value=>String(value),esc=value=>String(value),fmtLine=value=>String(value);
const drawStages=()=>{},showRunVideo=()=>{},loadLibrary=()=>{};
async function tick(){''' + original_tick + "\ntick();</script>" + (RUNTIME / "cr_video_cancel.html").read_text() + "</body></html>"
    state = SimpleNamespace(posts=[], reject=False, jobs=[{"slug": "selected", "title": "Selected",
        "state": "running", "elapsed": 3, "progress": .2, "cr_cancel": {"can_cancel": True, "run_id": RUN}},
        {"slug": "next", "title": "Queued next", "state": "queued",
         "cr_cancel": {"can_cancel": True, "run_id": "video_" + "c" * 24}}])
    with api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def route(route):
            req = route.request
            path = req.url.removeprefix("http://fixture.test")
            if path == "/":
                return route.fulfill(content_type="text/html", body=screen)
            if path == "/api/queue":
                return route.fulfill(content_type="application/json", body=json.dumps({"jobs": state.jobs}))
            if path == "/api/status/selected":
                return route.fulfill(content_type="application/json", body='{"lines":["Original progress"],"done":false}')
            if req.method == "POST" and path.startswith("/cr-video/"):
                state.posts.append((path, req.post_data_json))
                return route.fulfill(status=409 if state.reject else 200, content_type="application/json",
                    body=json.dumps({"error": "The selected worker changed. Refresh its progress."} if state.reject else
                                    {"cancelled": True, "message": cancel.CANCEL_MESSAGE}))
            pytest.fail("Unexpected UI request: " + path)
        page.route("**/*", route)
        page.goto("http://fixture.test/")
        page.get_by_role("button", name="Cancel render", exact=True).wait_for()
        yield page, state
        browser.close()
        assert not errors


def test_actual_original_progress_has_deliberate_running_and_individual_queue_cancel(original_progress_page):
    page, state = original_progress_page
    page.get_by_role("button", name="Cancel queued video", exact=True).wait_for()
    page.get_by_role("button", name="Cancel queued video", exact=True).click()
    page.wait_for_function("document.getElementById('cr-cancel-note').dataset.result==='1'")
    assert state.posts == [("/cr-video/next/cancel", {"run_id": "video_" + "c" * 24})]
    page.get_by_role("button", name="Cancel render", exact=True).wait_for()
    page.get_by_role("button", name="Cancel render", exact=True).click()
    page.wait_for_function("document.getElementById('cr-cancel-note').textContent.includes('Cancelled on this computer')")
    assert state.posts[-1] == ("/cr-video/selected/cancel", {"run_id": RUN})
    assert page.locator("#title").input_value() == "Preserve the user's title"
    assert "Original progress" in page.locator("#log").inner_text()


def test_failed_cancel_remains_visible_and_does_not_restart_or_refresh_video(original_progress_page):
    page, state = original_progress_page
    state.reject = True
    page.get_by_role("button", name="Cancel render", exact=True).click()
    page.wait_for_function("document.getElementById('cr-cancel-note').textContent.includes('worker changed')")
    assert page.get_by_role("button", name="Cancel render", exact=True).is_enabled()
    assert len(state.posts) == 1 and state.posts[0][0] == "/cr-video/selected/cancel"
    assert page.locator("#title").input_value() == "Preserve the user's title"


def test_owned_nonce_is_assigned_before_original_queue_publication_only_for_standard_videos(tmp_path):
    studio = fixture_studio(tmp_path)
    observed = []
    studio._ensure_dispatcher = lambda: observed.extend(dict(row) for row in studio.QUEUE)
    ns = {"request": request, "jsonify": jsonify, "QUEUE": studio.QUEUE,
          "_ensure_dispatcher": studio._ensure_dispatcher}
    exec('''def original_run():
 data=request.get_json()
 QUEUE.append({"slug":"selected","title":"Selected","timeline":data.get("timeline",False)})
 _ensure_dispatcher()
 return jsonify(queued=[{"slug":"selected"}])
''', ns)
    studio.app.add_url_rule("/api/run", "api_run", ns["original_run"], methods=["POST"])
    cr_submission.install(studio)
    client = studio.app.test_client()
    assert client.post("/api/run", json={}).status_code == 200
    assert "cr_video_run_id" not in observed[-1]
    studio.QUEUE.clear()
    studio._cr_video_workers = object()
    assert client.post("/api/run", json={}).status_code == 200
    assert cancel.RUN_ID.fullmatch(observed[-1]["cr_video_run_id"])
    studio.QUEUE.clear()
    assert client.post("/api/run", json={"timeline": True}).status_code == 200
    assert "cr_video_run_id" not in observed[-1]


def test_native_cancel_saves_failed_project_and_reconciles_only_saved_receipts(tmp_path):
    import cr_claude
    studio = fixture_studio(tmp_path)
    saved = tmp_path / "output/cr-claude-cache" / PROJECT
    saved.mkdir(parents=True)
    complete = {"project_id": PROJECT, "stage_id": "cc_saved", "authorization_token": "private-fake-token",
                "status": "complete", "result_fingerprint": "f" * 64}
    cr_claude._private_write(saved / "complete.json", {"state": "complete_local", "receipt": complete,
                                                     "stdout": "Saved original result"})
    cr_claude._private_write(saved / "running.json", {"state": "running", "input_fingerprint": "unchanged"})
    report, stages, released = [], [], []
    job = tmp_path / "saved-original-job"
    studio.mv = SimpleNamespace(job_dir=lambda *a: job)
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: {"brain_mode": "claudecode", "setup_proof": {}},
        install=lambda _: None, begin_project=lambda *a, **kw: {"id": PROJECT},
        finish_project=lambda payload: report.append(payload), finish_stage=lambda payload: stages.append(payload))
    activity = SimpleNamespace(wait_for_editor=lambda: None, reserve=lambda: "reservation",
                               release=lambda token: released.append(token))
    def worker(spec, project_id):
        assert project_id == PROJECT
        studio.JOBS[spec["slug"]] = {"state": "cancelled", "lines": [], "done": True, "error": cancel.CANCEL_MESSAGE}
        studio.RUNNING["slug"] = None
    workers = SimpleNamespace(supports=lambda _: True, run=worker)
    node = next(node for node in ast.walk(ast.parse((RUNTIME / "cr_studio.py").read_text()))
                if isinstance(node, ast.FunctionDef) and node.name == "run_owned_spec")
    ns = {"activity": activity, "studio": studio, "json": json, "cr_bridge": bridge,
          "cr_claude": cr_claude, "video_workers": workers, "original_run": lambda _: pytest.fail("in-process pipeline"),
          "output_artifacts": lambda *a: pytest.fail("Cancelled work cannot publish final artifacts")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "actual-native-video-wrapper", "exec"), ns)
    ns["run_owned_spec"](spec())
    assert report == [{"project_id": PROJECT, "status": "failed", "artifacts": [], "error_code": "cancelled"}]
    assert stages == [complete] and released == ["reservation"]
    assert json.loads((saved / "running.json").read_text())["state"] == "running"
    assert json.loads((saved / "complete.json").read_text())["state"] == "complete"


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent/session ownership contract")
def test_worker_stops_its_private_group_when_owning_studio_exits(tmp_path):
    ready, pid_path = tmp_path / "ready", tmp_path / "worker.pid"
    child_program = "import cr_video_cancel,sys,time,pathlib;cr_video_cancel.guard_parent(sys.argv[1],sys.argv[2],sys.argv[3]);pathlib.Path(sys.argv[4]).write_text('ready');time.sleep(120)"
    parent_program = """import cr_video_cancel,sys,os,subprocess,pathlib,time
root,project,run,ready,pid,child=sys.argv[1:]
_,spec,_=cr_video_cancel.worker_files(root,run)
cr_video_cancel._write(spec,{'project_id':project,'parent_pid':os.getpid()})
worker=subprocess.Popen([sys.executable,'-c',child,root,project,run,ready],start_new_session=True)
pathlib.Path(pid).write_text(str(worker.pid))
time.sleep(120)
"""
    environment = {**os.environ, "PYTHONPATH": str(RUNTIME)}
    parent = subprocess.Popen([sys.executable, "-c", parent_program, str(tmp_path), PROJECT, RUN,
                               str(ready), str(pid_path), child_program], env=environment,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    worker_pid = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        worker_pid = int(pid_path.read_text())
        parent.terminate()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        status = ""
        while time.monotonic() < deadline:
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(worker_pid)],
                                    capture_output=True, text=True).stdout.strip()
            if not status or status.startswith("Z"):
                break
            time.sleep(.05)
        assert not status or status.startswith("Z")
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if worker_pid is not None:
            try:
                os.killpg(worker_pid, 9)
            except ProcessLookupError:
                pass


def test_actual_worker_bootstrap_keeps_pinned_project_and_setup_without_new_begin(tmp_path, monkeypatch):
    import cr_worker, cr_bridge, cr_claude, cr_media, cr_render, cr_readiness, cr_capabilities
    (tmp_path / "cr-release.json").write_text('{"version":"fixture-release"}')
    engine, original = SimpleNamespace(), SimpleNamespace()
    workflow = {"required_release": "fixture-release", "workflow_version": "same", "engine_source_sha256": "same"}
    progress = {"project": {"id": PROJECT}, "workflow": workflow,
                "job_spec": {"title": "Selected", "render_backend": "original", "features": {"youtube": True}}}
    observed = []
    monkeypatch.setenv("CLAUDE_PROVIDER", "fixture-original")
    monkeypatch.setenv("SCRIPT_PROVIDER", "fixture-original")
    monkeypatch.setenv("CLAUDE_HANDOFF_DIR", "")
    monkeypatch.setitem(sys.modules, "make_video", engine)
    monkeypatch.setitem(sys.modules, "app", original)
    monkeypatch.setattr(cr_worker, "HOME", tmp_path)
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: {"brain_mode": "claudecode"})
    monkeypatch.setattr(cr_bridge, "_project", None)
    monkeypatch.setattr(cr_bridge, "project_progress", lambda project: progress if project == PROJECT else pytest.fail("other project"))
    monkeypatch.setattr(cr_bridge, "call", lambda _: {"production_workflow": workflow})
    monkeypatch.setattr(cr_bridge, "begin_project", lambda *a, **kw: pytest.fail("duplicate begin"))
    for module in (cr_bridge, cr_media, cr_render):
        monkeypatch.setattr(module, "install", lambda _: None)
    monkeypatch.setattr(cr_capabilities, "install", lambda app: observed.append(("capabilities", app)))
    monkeypatch.setattr(cr_studio, "configure_video_tools", lambda: None)
    monkeypatch.setattr(cr_studio, "graphics_ready", lambda: {"browser": True, "webgl": True})
    monkeypatch.setattr(cr_studio, "video_tools_ready", lambda: True)
    monkeypatch.setattr(cr_studio, "tool_ready", lambda *a: True)
    monkeypatch.setattr(cr_readiness, "basic", lambda _: {"ready": True})
    monkeypatch.setattr(cr_workers, "contain_children", lambda: observed.append(("contained", True)))
    monkeypatch.setattr(cr_workers, "install", lambda: None)
    monkeypatch.setattr(cancel, "guard_parent", lambda *args: observed.append(("ownership", args)))
    monkeypatch.setattr(cancel, "run_original_worker", lambda *args: observed.append(("original", args)))
    monkeypatch.setattr(sys, "argv", ["cr_worker.py", "--project", PROJECT, "--", str(tmp_path / "app.py"), "--cr-video", RUN])
    cr_worker.main()
    assert observed == [("ownership", (tmp_path, PROJECT, RUN)), ("contained", True),
                        ("capabilities", original), ("original", (tmp_path, PROJECT, RUN, original))]
    assert cr_bridge._project == {**progress["project"], "job_spec": progress["job_spec"]}


@pytest.mark.parametrize("provider,tasks", [("atlas", ["images", "avatars"]), ("kie", ["avatars"])])
def test_child_capabilities_preserve_selected_cr_media_with_original_feature_resolver(provider, tasks, monkeypatch):
    import importlib.util
    import cr_capabilities, cr_media, cr_bridge
    path = Path("/Users/workcomputer/Downloads/ChannelRecipe studio/features.py")
    if not path.is_file():
        pytest.skip("The owner's private source is not installed")
    module_spec = importlib.util.spec_from_file_location("isolated_original_features", path)
    original = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(original)
    # Never read the real computer's saved media credentials.
    monkeypatch.setattr(original, "_env", lambda: {})
    monkeypatch.setenv("ATLASCLOUD_API_KEY" if provider == "atlas" else "KIE_API_KEY", "isolated-test-media-key")
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: {"key": "cr_mcp_isolated", "costs_accepted": True,
        "brain_mode": "channelrecipe", "media_hub": {"provider": provider, "tasks": tasks}})
    monkeypatch.setattr(cr_media, "_native_images", None)
    style = {"features": {"offered": ["ai_images", "avatar"], "on": ["ai_images", "avatar"]}}
    requested = {"ai_images": "images" in tasks, "avatar": True}
    before = original.resolve(style, requested)
    assert not before["ai_images"] and not before["avatar"]
    studio = SimpleNamespace(features=original, mv=SimpleNamespace(CLAUDE_PROVIDER="channelrecipe"))
    cr_capabilities.install(studio)
    after = original.resolve(style, requested)
    assert after["ai_images"] == requested["ai_images"] and after["avatar"]
    assert not any(after[key] for key in after if key not in {"ai_images", "avatar"})


def test_a_busy_progress_file_on_windows_never_stops_the_video(tmp_path, monkeypatch):
    # Windows refuses to replace a file Studio has open (PermissionError [WinError 5]); a moment later it is free.
    import cr_claude
    target = tmp_path / "progress.json"
    real, refused = Path.replace, []
    def busy(self, dest):
        if Path(dest) == target and len(refused) < 3:
            refused.append(dest)
            raise PermissionError(13, "Access is denied", str(dest))
        return real(self, dest)
    monkeypatch.setattr(Path, "replace", busy)
    cr_claude._private_write(target, {"state": "running"})
    assert len(refused) == 3 and json.loads(target.read_text()) == {"state": "running"}
    assert not list(tmp_path.glob(".progress.json.*.tmp"))

    studio = fixture_studio(tmp_path)
    studio.mv = SimpleNamespace(job_dir=lambda *a: tmp_path / "job")
    def original(spec_):
        studio.JOBS["selected"]["lines"].append("working")
        time.sleep(1.3)
        studio.JOBS["selected"].update(state="done", done=True)
    studio._run_spec = original
    folder, spec_path, progress = cancel.worker_files(tmp_path, RUN)
    cancel._write(spec_path, {"project_id": PROJECT, "spec": spec()})
    writes, real_write = [], cancel._write
    def flaky(path, data):
        writes.append(path)
        if path == progress and len(writes) <= 2:
            raise PermissionError(13, "Access is denied", str(path))
        return real_write(path, data)
    monkeypatch.setattr(cancel, "_write", flaky)
    cancel.run_original_worker(tmp_path, PROJECT, RUN, studio)
    assert len(writes) >= 3, "progress kept being saved after the busy file"
    result = json.loads(progress.read_text())
    assert result["job"]["done"] and result["job"]["lines"] == ["working"]


def test_studio_says_when_it_was_closed_in_the_middle_of_a_video(tmp_path):
    assert cr_studio.session_started(tmp_path) == ""
    cr_studio.session_stopped("ctrl-c", tmp_path)
    assert cr_studio.session_started(tmp_path) == "", "a Studio that stopped itself is not news"
    assert cr_studio.session_started(tmp_path) == "", "closing its window between videos is how Studio stops"
    _, _, progress = cancel.worker_files(tmp_path, RUN)
    progress.parent.mkdir(parents=True)
    cancel._write(progress, {"project_id": PROJECT, "run_id": RUN, "job": {"state": "running", "done": False,
                                                                           "title": "The Hindenburg"}})
    # written later in that session, as a real video's progress is (Windows stamps files from a clock that can run
    # 16 ms behind time.time(), so a file written right after the session's note could look older than it)
    later = time.time() + 5
    os.utime(progress, (later, later))
    note = cr_studio.session_started(tmp_path)                      # closed from outside while that video ran
    assert '"The Hindenburg"' in note and "Claude Code" in note and "launcher window" in note
    cancel._write(progress, {"project_id": PROJECT, "run_id": RUN, "job": {"state": "done", "done": True}})
    assert cr_studio.session_started(tmp_path) == ""
