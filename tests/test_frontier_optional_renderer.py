"""Optional-renderer setup failures stay actionable without locking the studio."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from flask import Flask, request
import pytest

RUNTIME = Path(__file__).parents[1] / "local_runtime"
sys.path.insert(0, str(RUNTIME))
import cr_optional_renderer as renderer
import cr_studio
import cr_ui


@pytest.fixture
def windows_python(monkeypatch):
    current = r"C:\Studio folder\.venv\Scripts\python.exe"
    older = r"C:\Python312\python.exe"
    newer = r"C:\Users\Owner\Python 3.13\python.exe"
    launcher = r"C:\Windows\py.exe"
    state = SimpleNamespace(calls=[], current=current, older=older, newer=newer, launcher=launcher,
                            discovered=newer, supports_newer=True, timeout_first=False)
    monkeypatch.setattr(renderer, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(renderer.sys, "executable", current)
    monkeypatch.setattr(renderer.shutil, "which", lambda name: {
        "python": older, "python3": older, "py": launcher}.get(name))
    for name in ("KIE_API_KEY", "ANTHROPIC_API_KEY", "PYLAUNCHER_ALLOW_INSTALL",
                 "PYLAUNCHER_ALWAYS_INSTALL", "PYLAUNCHER_DRYRUN"):
        monkeypatch.setenv(name, "isolated-fixture")

    def invoke(args, *, capture_output, text, timeout, env, encoding, errors):
        assert capture_output and text and timeout == 10
        assert encoding == "utf-8" == env["PYTHONIOENCODING"] and errors == "replace", "C:\\Users\\Zoé's Python path"
        assert env["PYTHON_MANAGER_AUTOMATIC_INSTALL"] == "false"
        assert not any(name in env for name in ("KIE_API_KEY", "ANTHROPIC_API_KEY", "PYLAUNCHER_ALLOW_INSTALL",
                                               "PYLAUNCHER_ALWAYS_INSTALL", "PYLAUNCHER_DRYRUN"))
        state.calls.append(args)
        if args[0] == launcher:
            if args[1] == "-3.14" and state.timeout_first:
                raise renderer.subprocess.TimeoutExpired(args, 10)
            return SimpleNamespace(returncode=0 if args[1] == "-3.13" else 1,
                                   stdout=state.discovered + "\n" if args[1] == "-3.13" else "")
        assert args[1:] == ["-c", "import sys;print(int(sys.version_info >= (3,13)))"]
        return SimpleNamespace(returncode=0, stdout="1\n" if args[0] == newer and state.supports_newer else "0\n")

    monkeypatch.setattr(renderer.subprocess, "run", invoke)
    return state


@pytest.mark.parametrize("timeout_first", [False, True])
def test_windows_py_finds_supported_side_by_side_python_without_changing_accounts(windows_python, timeout_first):
    state = windows_python
    state.timeout_first = timeout_first
    assert renderer.supported_python() == state.newer
    assert [call[0] for call in state.calls].count(state.older) == 1
    assert state.calls[-1][0] == state.newer  # Validate the resolved executable, not only its launcher tag.
    assert [state.launcher, "-3.13", "-c", "import sys;print(sys.executable)"] in state.calls


def test_windows_launcher_resolution_cannot_bypass_minimum_version(windows_python):
    windows_python.supports_newer = False
    with pytest.raises(RuntimeError, match="Python 3.13 or newer"):
        renderer.supported_python()
    assert any(call[0] == windows_python.newer for call in windows_python.calls)


def test_windows_launcher_diagnostic_output_is_not_used_as_an_executable(windows_python):
    windows_python.discovered = "Launcher warning\n" + windows_python.newer
    with pytest.raises(RuntimeError, match="Python 3.13 or newer"):
        renderer.supported_python()
    assert not any(call[0] == windows_python.discovered for call in windows_python.calls)


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.testing = True
    studio = SimpleNamespace(app=app, RUNNING={}, QUEUE=[])
    activity = cr_studio.CreativeActivity(studio)
    monkeypatch.setattr(cr_studio.cr_workers, "busy", lambda: False)
    state = SimpleNamespace(ready=False, threads=[], attempts=[])

    class DeferredThread:
        def __init__(self, *, target, daemon):
            assert daemon
            self.target = target
            self.alive = False
            state.threads.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def finish(self):
            try:
                self.target()
            finally:
                self.alive = False

    monkeypatch.setattr(renderer, "threading", SimpleNamespace(Thread=DeferredThread))
    monkeypatch.setattr(renderer, "ready", lambda *a, **k: {
        "ready": state.ready, "version": "fixture", "message": "Run the local check."})
    monkeypatch.setattr(renderer.cr_manimgx, "progress_status", lambda _: {})

    def install(home, report):
        state.attempts.append(home)
        report("checking", "Checking the local renderer.")
        state.ready = True
        report("ready", "Fast mode is ready.")

    monkeypatch.setattr(renderer, "install_dependencies", install)
    renderer.install(studio, activity, home=tmp_path)
    return SimpleNamespace(client=app.test_client(), activity=activity, home=tmp_path,
                           state=state, status=tmp_path / ".cr-manimgx/setup-status.json")


def test_first_install_remains_explicit_and_owns_its_reservation(env):
    initial = env.client.get("/cr-renderer").json
    assert not initial["ready"] and not initial["busy"]
    assert initial["setup"]["state"] == "not_installed"
    assert not env.state.attempts and not env.state.threads
    assert env.client.post("/cr-renderer", json={"action": "install"}).status_code == 200
    checking = env.client.get("/cr-renderer").json
    assert checking["busy"] and checking["setup"]["state"] == "installing"
    assert env.client.post("/cr-renderer", json={"action": "install"}).status_code == 409
    assert len(env.state.threads) == 1 and not env.state.attempts
    env.state.threads[0].finish()
    finished = env.client.get("/cr-renderer").json
    assert finished["ready"] and not finished["busy"]
    assert finished["setup"]["state"] == "ready"
    assert env.state.attempts == [env.home]


def test_status_write_failure_releases_reservation_and_allows_explicit_retry(env, monkeypatch):
    write = renderer.cr_claude._private_write

    def denied(*args, **kwargs):
        raise OSError("fixture: disk write denied")

    monkeypatch.setattr(renderer.cr_claude, "_private_write", denied)
    failed = env.client.post("/cr-renderer", json={"action": "install"})
    assert failed.status_code == 503 and "could not start" in failed.json["error"]
    assert not env.activity.busy() and not env.state.threads
    monkeypatch.setattr(renderer.cr_claude, "_private_write", write)
    assert env.client.post("/cr-renderer", json={"action": "install"}).status_code == 200
    env.state.threads[0].finish()
    assert env.client.get("/cr-renderer").json["ready"]


def test_thread_start_failure_releases_reservation(env, monkeypatch):
    def failed_start(_):
        raise RuntimeError("fixture: thread unavailable")

    monkeypatch.setattr(renderer.threading.Thread, "start", failed_start)
    result = env.client.post("/cr-renderer", json={"action": "install"})
    assert result.status_code == 503 and not env.activity.busy()
    status = env.client.get("/cr-renderer").json
    assert status["setup"]["state"] == "stopped"
    assert not status["busy"] and not env.state.attempts


@pytest.mark.parametrize("stage", ["installing", "checking", "rendering"])
def test_unrelated_activity_cannot_keep_an_interrupted_installer_running(env, stage):
    renderer.cr_claude._private_write(env.status, {"state": stage, "message": "Old check"})
    reservation = env.activity.reserve()
    try:
        status = env.client.get("/cr-renderer").json
        assert status["busy"]  # The unrelated task still owns the studio.
        assert status["setup"]["state"] == "stopped"
        assert not env.state.threads and not env.state.attempts
    finally:
        env.activity.release(reservation)


def test_worker_failure_is_visible_and_does_not_hold_the_studio(env, monkeypatch):
    def fail(home, report):
        report("checking", "Checking the local renderer.")
        raise RuntimeError("The pinned documentation changed.")

    monkeypatch.setattr(renderer, "install_dependencies", fail)
    assert env.client.post("/cr-renderer", json={"action": "install"}).status_code == 200
    env.state.threads[0].finish()
    status = env.client.get("/cr-renderer").json
    assert not status["busy"] and not status["ready"]
    assert status["setup"]["state"] == "failed"
    assert status["setup"]["message"] == "The pinned documentation changed."


def _injected_page(home):
    """Use the actual token and panel response wrapper without starting the engine."""
    (home / "cr_renderer_panel.html").write_bytes((RUNTIME / "cr_renderer_panel.html").read_bytes())
    app = Flask("isolated_renderer_panel")
    studio = SimpleNamespace(app=app)
    tree = ast.parse((RUNTIME / "cr_studio.py").read_text())
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "inject")
    function.decorator_list = []
    namespace = {"studio": studio, "HOME": home, "request": request, "json": json,
                 "cr_ui": cr_ui, "token": "fixture-token", "cr_media": SimpleNamespace(active=lambda _: False)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 str(RUNTIME / "cr_studio.py"), "exec"), namespace)
    app.after_request(namespace["inject"])

    @app.get("/")
    def page():
        return '<!doctype html><html><head><meta charset="utf-8"></head><body><button id="go">Make video</button></body></html>'

    return app.test_client().get("/").get_data(as_text=True)


@pytest.fixture
def panel(tmp_path):
    api = pytest.importorskip("playwright.sync_api")
    html = _injected_page(tmp_path)
    state = SimpleNamespace(posts=[], polls=0, post_status=409, get_status=200,
                            error="Finish the current studio work before setting up Fast mode.",
                            recovery_posts=[], recovery_gets=0, recovery_choices=[], recovery_status=200,
                            recovery_error="The saved recovery changed; refresh its status.")
    state.data = {"ready": True, "busy": False, "setup": {"state": "ready", "message": "Fast mode is ready."}, "progress": {}}
    with api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except api.Error:
            pytest.skip("A local Playwright Chromium installation is unavailable")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(route):
            req = route.request
            if req.url == "http://fixture.test/":
                return route.fulfill(status=200, content_type="text/html", body=html)
            if req.url == "http://fixture.test/cr-status":
                return route.fulfill(status=200, content_type="application/json", body='{"readiness":{"ready":true}}')
            if req.url == "http://fixture.test/cr-renderer/recovery":
                if req.method == "POST":
                    body = req.post_data_json
                    state.recovery_posts.append({"body": body, "token": req.headers.get("x-cr-local-token")})
                    if state.recovery_status != 200:
                        return route.fulfill(status=state.recovery_status, content_type="application/json",
                                             body=json.dumps({"error": state.recovery_error}))
                    state.data.update(busy=True, recovery={**body, "state": "running", "message": "Saved-scene action running."})
                    return route.fulfill(status=200, content_type="application/json",
                                         body=json.dumps({"started": True, "recovery": state.data["recovery"]}))
                state.recovery_gets += 1
                return route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "choices": state.recovery_choices, "recovery": state.data.get("recovery", {})}))
            if req.url == "http://fixture.test/cr-renderer":
                if req.method == "POST":
                    state.posts.append({"body": req.post_data_json, "token": req.headers.get("x-cr-local-token")})
                    return route.fulfill(status=state.post_status, content_type="application/json",
                                         body=json.dumps({"error": state.error} if state.post_status != 200 else {"started": True}))
                state.polls += 1
                return route.fulfill(status=state.get_status, content_type="application/json",
                                     body=json.dumps(state.data if state.get_status == 200 else {"error": "Renderer status unavailable."}))
            pytest.fail("Unexpected UI network request: " + req.url)

        page.route("**/*", route)
        page.goto("http://fixture.test/")
        yield page, state
        browser.close()
        assert not errors


@pytest.mark.parametrize("status,message", [
    (409, "Finish the current studio work before setting up Fast mode."),
    (403, "Refresh your studio page and try again."),
    (503, "The renderer check could not start. Check the local studio log, then try again."),
])
def test_setup_post_errors_survive_status_polling(panel, status, message):
    page, state = panel
    state.post_status, state.error = status, message
    page.get_by_role("button", name="Check Fast mode again").click()
    page.wait_for_function("document.getElementById('cr-renderer-error').hidden===false")
    polls = state.polls
    page.wait_for_timeout(2150)
    assert state.polls > polls
    assert page.get_by_role("alert").inner_text() == message
    assert state.posts == [{"body": {"action": "install"}, "token": "fixture-token"}]
    assert page.get_by_role("button", name="Check Fast mode again").is_enabled()
    state.post_status = 200
    page.get_by_role("button", name="Check Fast mode again").click()
    assert page.get_by_role("alert").is_hidden()


def test_status_failure_preserves_the_users_renderer_choice(panel):
    page, state = panel
    toggle = page.locator("#cr-renderer-toggle")
    toggle.check()
    state.get_status = 503
    page.wait_for_function("document.getElementById('cr-renderer-status').textContent==='Renderer status unavailable.'")
    assert toggle.is_checked() and toggle.is_enabled()
    toggle.uncheck()
    assert toggle.is_disabled()


def test_old_success_does_not_hide_required_renderer_recheck(panel):
    page, state = panel
    message = "Run the optional Fast mode setup check before selecting it."
    state.data.update(ready=False, message=message)
    page.wait_for_function("document.getElementById('cr-renderer-status').textContent=== " + json.dumps(message))
    assert page.locator('#cr-renderer-toggle').is_disabled()
    assert page.get_by_role('button', name='Install and check Fast mode').is_enabled()


def test_claude_compilation_is_distinct_from_gpu_frame_progress(panel):
    page, state = panel
    state.data['progress'] = {'scenes': {'example': {'scene_id': 'example', 'stage': 'compile',
        'state': 'running', 'frames_done': 0, 'total_frames': 91, 'elapsed_seconds': 0}}}
    page.wait_for_function("document.getElementById('cr-renderer-progress').textContent.includes('Claude is writing 3D code')")
    text = page.locator('#cr-renderer-progress').inner_text()
    assert 'frames/sec' not in text and '0 frames' not in text
    state.data['progress']['scenes']['example'].update(stage='render', frames_done=45, elapsed_seconds=.5)
    page.wait_for_function("document.getElementById('cr-renderer-progress').textContent.includes('90.0 frames/sec')")
    assert '45 frames / 91' in page.locator('#cr-renderer-progress').inner_text()


def test_saved_source_validation_is_explicit_and_never_runs_create(panel):
    page, state = panel
    choice = {"project_id": "crv_" + "1" * 24, "selection_id": "a" * 64, "scene_id": "bee",
              "title": "Saved <video>", "state": "available", "can_validate": True, "can_correct": False}
    state.recovery_choices = [choice]
    assert state.recovery_gets == 0 and not state.recovery_posts
    page.get_by_text('Recover a stopped 3D scene', exact=True).click()
    page.get_by_role('button', name='Find saved 3D work').click()
    assert page.locator('.cr-recovery-choice strong').inner_text() == 'Saved <video> · bee'
    assert 'no model or media credits' in page.locator('#cr-recovery-panel').inner_text()
    page.get_by_role('button', name='Validate saved 3D code · no credits').click()
    assert state.recovery_posts == [{"body": {"action": "validate", "project_id": choice['project_id'],
        "selection_id": choice['selection_id'], "acknowledge_recovery": True}, "token": "fixture-token"}]
    assert page.get_by_role('button', name='Validate saved 3D code · no credits').is_disabled()
    state.data.update(busy=False, recovery={**state.data['recovery'], 'state': 'validated',
        'message': 'Saved code passed validation. The video is still stopped. Press Create video manually to resume.'})
    page.wait_for_function("document.getElementById('cr-recovery-message').textContent.includes('still stopped')")
    assert len(state.recovery_posts) == 1 and state.recovery_gets == 1
    assert page.get_by_role('button', name='Validate saved 3D code · no credits').is_disabled()
    assert not state.posts  # No setup, paid fallback or automatic video submission occurred.


def test_saved_source_correction_needs_its_own_visible_cost_acknowledgement(panel):
    page, state = panel
    choice = {"project_id": "crv_" + "1" * 24, "selection_id": "a" * 64, "scene_id": "bee",
              "title": "Saved video", "state": "source_invalid", "can_validate": False, "can_correct": True}
    state.recovery_choices = [choice]
    page.get_by_text('Recover a stopped 3D scene', exact=True).click()
    page.get_by_role('button', name='Find saved 3D work').click()
    correct = page.get_by_role('button', name='Correct saved 3D code', exact=True)
    assert correct.is_disabled() and not state.recovery_posts
    page.get_by_label('Use my Claude account for one correction to this saved scene.').check()
    assert correct.is_enabled()
    page.wait_for_timeout(2150)
    assert correct.is_enabled()  # Status polling preserves an explicit checkbox choice.
    correct.click()
    assert state.recovery_posts == [{"body": {"action": "correct", "project_id": choice['project_id'],
        "selection_id": choice['selection_id'], "acknowledge_correction": True}, "token": "fixture-token"}]
    assert correct.is_disabled() and state.recovery_gets == 1
    state.data.update(busy=False, recovery={**state.data['recovery'], 'state': 'failed',
        'message': 'The one correction stopped. Review its saved diagnostic; it will not retry.'})
    page.wait_for_function("document.getElementById('cr-recovery-message').textContent.includes('one correction stopped')")
    assert correct.is_disabled()  # Its old checked checkbox cannot re-enable a spent action.
    assert len(state.recovery_posts) == 1


def test_saved_source_errors_remain_visible_during_renderer_polling(panel):
    page, state = panel
    state.recovery_choices = [{"project_id": "crv_" + "1" * 24, "selection_id": "a" * 64, "scene_id": "bee",
        "title": "Saved video", "state": "available", "can_validate": True, "can_correct": False}]
    state.recovery_status = 409
    page.get_by_text('Recover a stopped 3D scene', exact=True).click()
    page.get_by_role('button', name='Find saved 3D work').click()
    page.get_by_role('button', name='Validate saved 3D code · no credits').click()
    page.wait_for_timeout(2150)
    assert page.locator('#cr-recovery-error').inner_text() == state.recovery_error
    assert page.locator('#cr-recovery-error').is_visible()
    assert len(state.recovery_posts) == 1


def test_completed_correction_can_validate_despite_stopped_state_and_previous_failed_correction(panel):
    page, state = panel
    choice = {"project_id": "crv_" + "1" * 24, "selection_id": "a" * 64, "scene_id": "tilt",
              "title": "Saved eclipse", "state": "stopped", "can_validate": True, "can_correct": False}
    state.recovery_choices = [choice]
    state.data.update(busy=False, recovery={"project_id": choice["project_id"], "selection_id": choice["selection_id"],
        "action": "correct", "state": "failed", "message": "Claude completed; its local checker stopped."})
    page.get_by_text('Recover a stopped 3D scene', exact=True).click()
    page.get_by_role('button', name='Find saved 3D work').click()
    validate = page.get_by_role('button', name='Validate saved 3D code · no credits')
    assert validate.is_enabled() and not state.recovery_posts
    assert page.get_by_role('button', name='Correct saved 3D code', exact=True).count() == 0
    page.wait_for_timeout(2150)
    assert validate.is_enabled()  # The old failed correction must not override fresh eligibility.
    validate.click()
    assert state.recovery_posts == [{"body": {"action": "validate", "project_id": choice['project_id'],
        "selection_id": choice['selection_id'], "acknowledge_recovery": True}, "token": "fixture-token"}]
    assert validate.is_disabled()
    state.data.update(busy=False, recovery={**state.data['recovery'], 'state': 'validated',
        'message': 'Completed correction validated locally.'})
    page.wait_for_function("document.getElementById('cr-recovery-message').textContent.includes('validated locally')")
    assert validate.is_disabled() and len(state.recovery_posts) == 1


@pytest.mark.parametrize("action,status,busy", [
    ("validate", "running", False), ("validate", "validated", False),
    ("validate", "failed", False), ("correct", "running", False), ("correct", "failed", True),
])
def test_recovery_validation_does_not_repeat_a_started_action_or_run_while_busy(panel, action, status, busy):
    page, state = panel
    choice = {"project_id": "crv_" + "1" * 24, "selection_id": "a" * 64, "scene_id": "tilt",
              "title": "Saved eclipse", "state": "stopped", "can_validate": True, "can_correct": False}
    state.recovery_choices = [choice]
    page.get_by_text('Recover a stopped 3D scene', exact=True).click()
    page.get_by_role('button', name='Find saved 3D work').click()
    validate = page.get_by_role('button', name='Validate saved 3D code · no credits')
    assert validate.is_enabled()
    state.data.update(busy=busy, recovery={"project_id": choice["project_id"], "selection_id": choice["selection_id"],
        "action": action, "state": status, "message": "Updated saved action status."})
    page.wait_for_function("document.getElementById('cr-recovery-message').textContent.includes('Updated saved action status')")
    assert validate.is_disabled() and not state.recovery_posts
