"""Local style-study controls, original studio integration and free browser flow."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_claude
import cr_readiness
import cr_studio
import cr_style_clone as clone
import cr_style_routes as routes
import cr_ui

BASE = "http://127.0.0.1:7860"
TOKEN = "synthetic-local-control-token"
VIDEO = "9iB2XSmhxKo"
BACKUP = "abcdefghijk"
THIRD = "mnopqrstuvw"
FOURTH = "zyxwvutsrqp"
PROJECT = "crv_" + "1" * 24
RUNTIME = Path(__file__).parents[1] / "local_runtime"


def _saved_kit(folder, spec, progress):
    progress("watch")
    clone._write(folder / "ready-style.json", {
        "name": spec["style_id"], "label": spec["label"],
        "look": {"graphics": "kit"}, "cr_style_clone": {"operation_id": folder.name}})
    clone._write(folder / "study-result.json", {"reference_used": spec["references"][0]["id"]})
    clone._write(folder / "reflearn/reference-evidence.json", {"captions_available": True})


def _studio_inject(app, studio, home):
    """Exercise the actual installed response wrapper without booting the engine."""
    tree = ast.parse((RUNTIME / "cr_studio.py").read_text())
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "inject")
    function.decorator_list = []
    namespace = {"studio": studio, "HOME": home, "request": request, "json": json,
                 "cr_ui": cr_ui, "token": TOKEN, "cr_media": SimpleNamespace(active=lambda _: False)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(RUNTIME / "cr_studio.py"), "exec"), namespace)
    app.after_request(namespace["inject"])


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles/documentary.json").write_text('{"name":"documentary","label":"Documentary","prompts":{"outline":"preserve"}}')
    (tmp_path / "cr_style_clone.html").write_bytes((RUNTIME / "cr_style_clone.html").read_bytes())
    cfg = {"key": "cr_mcp_synthetic_account", "brain_mode": "claudecode", "costs_accepted": True,
           "general_model": cr_claude.GENERAL_MODEL, "visual_model": cr_claude.VISUAL_MODEL}
    checks = {"readiness": {"ready": True}, "local_version": "5.3-admin.17", "ffmpeg": True,
              "browser": True, "node": True, "claude_code": {"installed": True, "authenticated": True,
              "api_provider": "firstParty", "models_verified": True, "allowance": {"blocked": False}}}
    calls, targets, workers = [], [], []
    bridge = SimpleNamespace(HOME=tmp_path, _project=None, load_settings=lambda: dict(cfg),
        prepare_runtime=lambda proof: calls.append(("prepare", proof)),
        _mcp_tool=lambda name, payload: calls.append((name, payload)) or {"project": {"id": PROJECT}},
        project_progress=lambda identifier: {"project": {"id": identifier, "status": "running"}},
        finish_project=lambda payload: calls.append(("finish", payload)))
    monkeypatch.setattr(routes, "cr_bridge", bridge)
    monkeypatch.setattr(cr_readiness, "cr_bridge", bridge)
    monkeypatch.setattr(cr_claude, "sync_receipts", lambda _: calls.append(("sync", None)))
    monkeypatch.setattr(cr_studio.cr_workers, "busy", lambda: False)
    metadata = [
        {"id": VIDEO, "title": "What If One Second Felt Like Ten", "channel": "Ifverse", "duration": 406, "views": 100000},
        *[{"id": identifier, "title": "Backup " + str(index), "channel": "Ifverse", "duration": 420, "views": 20000}
          for index, identifier in enumerate((BACKUP, THIRD, FOURTH), 1)],
    ]
    original_preview = clone.preview
    def preview(home, url):
        calls.append(("preview", url))
        return original_preview(home, url, learner=SimpleNamespace(_flat=lambda *args, **kwargs: metadata))
    monkeypatch.setattr(clone, "preview", preview)
    class DeferredThread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            self.target = target
        def start(self):
            targets.append(self.target)
    monkeypatch.setattr(routes, "threading", SimpleNamespace(Thread=DeferredThread))
    def subprocess_run(argv, *, cwd, stdout, stderr):
        workers.append((argv, cwd))
        clone.run(tmp_path, argv[-1], bridge, readiness=lambda: cr_readiness.proof(checks, purpose="creative"), execute=_saved_kit)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(routes, "subprocess", SimpleNamespace(run=subprocess_run, STDOUT=-2))
    app = Flask(__name__)
    app.config["TESTING"] = True
    @app.get("/api/styles")
    def original_styles():
        return jsonify(original=True)
    @app.get("/")
    def original_page():
        return "<html><head></head><body>Original studio</body></html>"
    styles = SimpleNamespace(load_all=lambda: calls.append(("load_all", None)),
        apply_to_engine=lambda *args: calls.append(("apply", args)),
        catalogue=lambda: [{"name": "documentary", "label": "Documentary", "prompts": {"secret": "private"}}])
    studio = SimpleNamespace(app=app, styles=styles, mv=object(), motion=object(), RUNNING={}, QUEUE=[])
    activity = cr_studio.CreativeActivity(studio)
    before = dict(app.view_functions)
    routes.install(studio, activity, lambda: jsonify(checks), token=TOKEN, home=tmp_path)
    _studio_inject(app, studio, tmp_path)
    assert all(app.view_functions[key] is value for key, value in before.items())
    client = app.test_client()
    def get(path, **kwargs):
        return client.get(path, base_url=BASE, **kwargs)
    def post(path, payload=None, **kwargs):
        headers = kwargs.pop("headers", {"X-CR-Local-Token": TOKEN})
        return client.post(path, base_url=BASE, json=payload or {}, headers=headers, **kwargs)
    def approved():
        result = post("/cr-style-clone/preview", {"url": "https://www.youtube.com/@Ifverse"})
        assert result.status_code == 200
        return {"preview_id": result.json["preview_id"], "reference_ids": [VIDEO, BACKUP],
                "base_style": "documentary", "label": "My science stories", "title": "How bees build homes",
                "brief": "Use clear diagrams", "budget_minutes": 22, "max_calls": 40, "minutes": 10,
                "acknowledge_costs": True}
    return SimpleNamespace(app=app, client=client, get=get, post=post, approved=approved, cfg=cfg,
        checks=checks, bridge=bridge, calls=calls, targets=targets, workers=workers, activity=activity,
        studio=studio, home=tmp_path, metadata=metadata)


def test_page_and_catalog_integrate_without_replacing_original_api(env):
    page = env.get("/cr-style-clone")
    assert page.status_code == 200 and "no-store" in page.headers["Cache-Control"]
    assert page.headers["X-Frame-Options"] == "DENY" and page.headers["X-Content-Type-Options"] == "nosniff"
    text = page.get_data(as_text=True)
    assert "__LOCAL_TOKEN_JSON__" not in text and "const CR_LOCAL_TOKEN=" in text
    assert "const localToken=" in text and 'id="reference-form"' in text
    catalogue = env.get("/cr-style-clone/catalog")
    assert catalogue.json == {"styles": [{"name": "documentary", "label": "Documentary"}]}
    assert "private" not in catalogue.get_data(as_text=True)
    assert env.get("/api/styles").json == {"original": True}
    assert [name for name, _ in env.calls] == ["load_all", "apply"]


@pytest.mark.parametrize("path,method", [("", "get"), ("/catalog", "get"), ("/preview", "post"), ("/start", "post"),
    ("/op_" + "1" * 24 + "/status", "get"), ("/op_" + "1" * 24 + "/board", "get"),
    ("/op_" + "1" * 24 + "/sync", "post"), ("/op_" + "1" * 24 + "/continue", "post")])
def test_every_route_rejects_remote_hosts_and_origins(env, path, method):
    invoke = getattr(env.client, method)
    assert invoke("/cr-style-clone" + path, base_url="http://rebound.example:7860").status_code == 403
    assert invoke("/cr-style-clone" + path, base_url=BASE, headers={"Origin": "https://untrusted.example", "X-CR-Local-Token": TOKEN}).status_code == 403
    assert not env.calls and not env.targets


@pytest.mark.parametrize("path", ["/preview", "/start", "/op_" + "1" * 24 + "/sync", "/op_" + "1" * 24 + "/continue"])
def test_mutations_require_local_control_token(env, path):
    assert env.post("/cr-style-clone" + path, headers={}).status_code == 403
    assert env.post("/cr-style-clone" + path, headers={"X-CR-Local-Token": "wrong"}).status_code == 403
    assert not env.calls and not env.targets


def test_preview_rejects_invalid_or_oversized_json_before_metadata(env):
    for body in ("[]", "bad json", json.dumps({"url": "x" * 33000})):
        response = env.client.post("/cr-style-clone/preview", base_url=BASE, data=body,
            headers={"X-CR-Local-Token": TOKEN, "Content-Type": "application/json"})
        assert response.status_code == 400
    assert env.calls == []


def test_preview_and_selection_approval_do_not_start_model_work(env):
    supplied = env.approved()
    assert [name for name, _ in env.calls] == ["preview"]
    supplied["acknowledge_costs"] = False
    assert env.post("/cr-style-clone/start", supplied).status_code == 400
    assert not env.targets and not env.activity.busy()
    assert not (env.home / "output/cr-style-clone/.requests").exists()


@pytest.mark.parametrize("blocker", ["api_mode", "readiness", "allowance"])
def test_start_requires_native_readiness_and_top_level_allowance(env, blocker):
    payload = env.approved()
    if blocker == "api_mode":
        env.cfg["brain_mode"] = "channelrecipe"
    elif blocker == "readiness":
        env.checks["readiness"]["ready"] = False
    else:
        env.checks["claude_code"]["allowance"] = {"blocked": True, "message": "Reset at 09:40"}
    response = env.post("/cr-style-clone/start", payload)
    assert response.status_code == 409
    if blocker == "allowance":
        assert response.json["error"] == "Reset at 09:40"
    assert not env.targets and not env.activity.busy()
    assert not (env.home / "output/cr-style-clone/.requests").exists()


def test_start_reserves_original_activity_and_worker_saves_one_new_preset(env):
    payload = env.approved()
    original = (env.home / "styles/documentary.json").read_bytes()
    start = env.post("/cr-style-clone/start", payload)
    assert start.status_code == 200 and start.json["state"] == "running" and start.json["busy"]
    operation = start.json["operation_id"]
    assert env.post("/cr-style-clone/start", payload).status_code == 409
    assert len(env.targets) == 1
    env.targets.pop()()
    status = env.get(f"/cr-style-clone/{operation}/status")
    assert status.json["state"] == "done" and status.json["style_ready"] and not status.json["busy"]
    assert status.json["reference_ids"] == [VIDEO, BACKUP] and status.json["reference_used"] == VIDEO
    assert status.json["captions_available"] is True
    assert env.workers == [([sys.executable, str(env.home / "cr_style_clone.py"), "--operation", operation], env.home)]
    assert (env.home / "styles" / (status.json["style"] + ".json")).is_file()
    assert (env.home / "styles/documentary.json").read_bytes() == original
    assert env.post("/cr-style-clone/start", payload).json["state"] == "done"
    assert not env.targets and len(env.workers) == 1
    assert sum(name == "local_studio_begin" for name, _ in env.calls) == 1
    assert "synthetic_account" not in json.dumps(status.json) and str(env.home) not in json.dumps(status.json)


def test_unrelated_activity_never_hides_an_interrupted_study(env):
    result = clone.prepare(env.home, env.approved(), cr_readiness.proof(env.checks, purpose="creative"), env.bridge)
    operation = result["operation_id"]
    folder, saved = clone._saved(env.home, operation, env.bridge)
    clone._write(folder / "launch.json", {"state": "running"})
    saved.update(state="running", phase="watch")
    clone._write(folder / "operation.json", saved)
    reservation = env.activity.reserve()
    result = env.get(f"/cr-style-clone/{operation}/status")
    assert result.json["state"] == "failed" and result.json["needs_manual_recovery"]
    assert result.json["busy"] is True
    env.activity.release(reservation)


def test_continue_requires_explicit_approval_original_models_and_prepared_state(env):
    result = clone.prepare(env.home, env.approved(), cr_readiness.proof(env.checks, purpose="creative"), env.bridge)
    operation = result["operation_id"]
    path = f"/cr-style-clone/{operation}/continue"
    assert env.get(f"/cr-style-clone/{operation}/status").json["can_continue_prepared"]
    assert env.post(path).status_code == 400
    env.cfg["visual_model"] = "different-model"
    assert env.post(path, {"acknowledge_costs": True}).status_code == 409
    env.cfg["visual_model"] = cr_claude.VISUAL_MODEL
    assert env.post(path, {"acknowledge_costs": True}).json["state"] == "running"
    env.targets.pop()()
    assert env.post(path, {"acknowledge_costs": True}).status_code == 409
    assert len(env.workers) == 1


@pytest.mark.parametrize("failure", ["thread", "launch_write"])
def test_launch_failure_releases_activity_and_preserves_prepared_approval(env, monkeypatch, failure):
    payload = env.approved()
    if failure == "thread":
        monkeypatch.setattr(routes, "threading", SimpleNamespace(Thread=lambda **kw: (_ for _ in ()).throw(RuntimeError("thread unavailable"))))
    else:
        original = cr_claude._private_write
        def write(path, value):
            if Path(path).name == "launch.json":
                raise OSError("private filesystem detail")
            original(path, value)
        monkeypatch.setattr(cr_claude, "_private_write", write)
    response = env.post("/cr-style-clone/start", payload)
    assert response.status_code in {409, 500} and not env.activity.busy() and not env.targets
    assert "private filesystem detail" not in response.json["error"]
    saved = list((env.home / "output/cr-style-clone").glob("op_*/operation.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["state"] == "prepared"


def test_status_sync_and_board_remain_owned_and_sync_never_starts_worker(env):
    payload = env.approved()
    start = env.post("/cr-style-clone/start", payload)
    operation = start.json["operation_id"]
    assert env.get(f"/cr-style-clone/{operation}/board").status_code == 404
    env.targets.pop()()
    folder = clone._operation(env.home, operation)
    board = folder / "reflearn/board.jpg"
    board.write_bytes(b"synthetic-jpeg")
    assert env.get(f"/cr-style-clone/{operation}/board").data == b"synthetic-jpeg"
    outside = env.home / "outside.jpg"
    outside.write_bytes(b"private")
    board.unlink()
    board.symlink_to(outside)
    assert env.get(f"/cr-style-clone/{operation}/board").status_code == 404
    assert env.post(f"/cr-style-clone/{operation}/sync").json["state"] == "done"
    assert len(env.workers) == 1 and not env.targets
    env.cfg["key"] = "cr_mcp_other_synthetic_account"
    for suffix, method in (("status", "get"), ("board", "get"), ("sync", "post"), ("continue", "post")):
        response = getattr(env, method)(f"/cr-style-clone/{operation}/{suffix}")
        assert response.status_code in {400, 409}
        assert not response.json.get("style_ready")


def test_browser_approved_reference_order_to_saved_style_and_no_script_collision(env):
    browser_api = pytest.importorskip("playwright.sync_api")
    with browser_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except browser_api.Error as error:
            pytest.skip("Local Chromium is unavailable: " + str(error).splitlines()[0])
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 1000})
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def respond(route):
                url = urlsplit(route.request.url)
                assert url.hostname == "127.0.0.1", "Browser flow must stay local"
                path = url.path + (("?" + url.query) if url.query else "")
                response = env.client.open(path, method=route.request.method, base_url=BASE,
                    headers=dict(route.request.headers), data=route.request.post_data)
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.data)
            page.route("**/*", respond)
            page.goto(BASE + "/cr-style-clone")
            page.locator("#reference-url").fill("https://www.youtube.com/@Ifverse")
            page.locator("#preview-button").click()
            page.locator("#candidate-list input").nth(1).check()
            page.locator("#candidate-list input").nth(0).check()
            page.locator("#candidate-list input").nth(2).check()
            page.locator("#candidate-list input").nth(3).click()
            assert not page.locator("#candidate-list input").nth(3).is_checked()
            assert "primary: Backup 1" in page.locator("#selection-summary").inner_text()
            assert not env.targets
            page.locator("#next-button").click()
            page.locator("#style-label").fill("My science stories")
            page.locator("#example-title").fill("How bees build homes")
            page.locator("#style-brief").fill("Clear diagrams and measured pacing")
            page.locator("#start-button").click()
            assert not env.targets
            page.locator("#usage-approved").check()
            page.locator("#candidate-list input").nth(2).uncheck()
            assert not page.locator("#usage-approved").is_checked()
            page.locator("#usage-approved").check()
            page.locator("#start-button").click()
            page.locator("#progress-panel").wait_for(state="visible")
            operation = page.evaluate("localStorage.getItem('cr.styleStudy')")
            folder, saved = clone._saved(env.home, operation, env.bridge)
            assert [ref["id"] for ref in saved["spec"]["references"]] == [BACKUP, VIDEO]
            assert saved["spec"]["title"] == "How bees build homes" and saved["spec"]["brief"] == "Clear diagrams and measured pacing"
            assert saved["spec"]["base_style"] == "documentary"
            env.targets.pop()()
            page.reload()
            page.locator("#saved-result").wait_for(state="visible")
            assert page.locator("#saved-label").inner_text() == "My science stories is saved"
            assert page.locator("#use-style").get_attribute("href") == "/?cr-preset=" + saved["spec"]["style_id"]
            assert page.locator("#used-reference a").get_attribute("href").endswith(BACKUP)
            assert not errors and len(env.workers) == 1
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator("#new-study").click()
            assert page.locator("#reference-panel").is_visible()
            assert page.evaluate("localStorage.getItem('cr.styleStudy')") is None
        finally:
            browser.close()
