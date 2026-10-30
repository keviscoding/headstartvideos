"""Local style-study controls, original studio integration and free browser flow."""
import ast
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_claude
import cr_style_map
import cr_readiness
import cr_studio
import cr_style_clone as clone
import cr_style_look
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
    def look_spawn(home, look_id):
        # The look's own process, finished at once: what Studio saw, with a recommended recipe.
        path = cr_style_look.folder(home, look_id) / "look.json"
        saved = json.loads(path.read_text())
        saved.update(state="done", phase="done", finished_at=__import__("time").time(), sheets=[], not_watched=[],
                     profile={"recognisable": ["Painted stick figures on watercolour backgrounds"]},
                     mapping={"router": cr_style_map.ROUTER, "base": "documentary",
                              "base_why": "most pictures are real footage",
                              "base_for": "real footage, documents, maps and graphics", "needs": [],
                              "settings": [{"area": "Pace", "label": "Footage cut length", "value": "3 s",
                                            "why": "the references cut every 3 s", "path": "pacing.cut_s"}],
                              "questions": [{"id": "music", "ask": "How much music?",
                                             "options": ["A story score under the whole video", "No music"]}],
                              "cost": {"usd_per_min": 0}})
        path.write_text(json.dumps(saved))
        calls.append(("look", look_id))
    monkeypatch.setattr(cr_style_look, "_spawn", look_spawn)
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
    assert catalogue.json == {"styles": [{"name": "documentary", "label": "Editorial Documentary"}]}
    assert "private" not in catalogue.get_data(as_text=True)
    assert env.get("/api/styles").json == {"original": True}
    assert [name for name, _ in env.calls] == ["load_all", "apply"]


@pytest.mark.parametrize("path,method", [("", "get"), ("/catalog", "get"), ("/preview", "post"), ("/start", "post"),
    ("/op_" + "1" * 24 + "/status", "get"), ("/op_" + "1" * 24 + "/board", "get"),
    ("/op_" + "1" * 24 + "/sync", "post"), ("/op_" + "1" * 24 + "/continue", "post"),
    ("/op_" + "1" * 24 + "/recover", "post"), ("/op_" + "1" * 24 + "/refine", "post"),
    ("/op_" + "1" * 24 + "/improve", "post"), ("/op_" + "1" * 24 + "/improvement/save", "post"),
    ("/op_" + "1" * 24 + "/improvement-picture/new/0", "get"), ("/op_" + "1" * 24 + "/improvement-video/new", "get")])
def test_every_route_rejects_remote_hosts_and_origins(env, path, method):
    invoke = getattr(env.client, method)
    assert invoke("/cr-style-clone" + path, base_url="http://rebound.example:7860").status_code == 403
    assert invoke("/cr-style-clone" + path, base_url=BASE, headers={"Origin": "https://untrusted.example", "X-CR-Local-Token": TOKEN}).status_code == 403
    assert not env.calls and not env.targets


@pytest.mark.parametrize("path", ["/preview", "/start", "/op_" + "1" * 24 + "/sync", "/op_" + "1" * 24 + "/continue", "/op_" + "1" * 24 + "/recover", "/op_" + "1" * 24 + "/refine",
                                  "/style/cr_clone_" + "1" * 12, "/sample", "/op_" + "1" * 24 + "/improve",
                                  "/op_" + "1" * 24 + "/improvement/save", "/op_" + "1" * 24 + "/improvement/keep"])
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


def test_metadata_recovery_uses_ready_identity_without_requiring_new_claude_allowance(env, monkeypatch):
    operation = "op_" + "2" * 24
    recovered = "op_" + "3" * 24
    env.checks["claude_code"]["allowance"] = {"blocked": True, "message": "Wait for Claude"}
    calls = []
    def recover(home, source, bridge, *, readiness):
        assert env.activity.busy()
        proof = readiness()
        assert proof["purpose"] == "creative"
        calls.append((home, source, bridge))
        return {"operation_id": recovered}
    monkeypatch.setattr(clone, "recover_metadata", recover)
    monkeypatch.setattr(clone, "status", lambda *a: {"operation_id": recovered, "state": "done", "metadata_only": True})
    response = env.post(f"/cr-style-clone/{operation}/recover")
    assert response.status_code == 200 and response.json["operation_id"] == recovered
    assert calls == [(env.home, operation, env.bridge)]
    assert not env.activity.busy() and not env.workers and not env.targets
    assert "no-store" in response.headers["Cache-Control"]


def test_metadata_recovery_cannot_overlap_work_and_always_releases_its_reservation(env, monkeypatch):
    operation = "op_" + "2" * 24
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("Reconcile the saved completed stages first.")
    monkeypatch.setattr(clone, "recover_metadata", fail)
    reservation = env.activity.reserve()
    try:
        assert env.post(f"/cr-style-clone/{operation}/recover").status_code == 409
        assert not calls
    finally:
        env.activity.release(reservation)
    response = env.post(f"/cr-style-clone/{operation}/recover")
    assert response.status_code == 409 and "Reconcile" in response.json["error"]
    assert calls == [True] and not env.activity.busy()


def test_prepared_metadata_operation_never_offers_a_paid_study_restart(env, monkeypatch):
    operation = "op_" + "2" * 24
    monkeypatch.setattr(clone, "status", lambda *a: {"operation_id": operation, "state": "prepared", "metadata_only": True})
    status = env.get(f"/cr-style-clone/{operation}/status")
    assert status.status_code == 200 and not status.json["can_continue_prepared"]


@pytest.fixture
def refined(env, monkeypatch):
    result = env.post("/cr-style-clone/start", env.approved())
    source = result.json["operation_id"]
    env.targets.pop()()
    child = "op_" + "4" * 24
    preparations = []
    # Backend evidence/copying has its own actual refinement tests. This fixture
    # isolates HTTP consent, activity, ownership and repeated worker submissions.
    def prepare(home, operation, proof, bridge, *, acknowledge_costs):
        assert acknowledge_costs is True
        folder, original = clone._saved(home, operation, bridge)
        assert original["state"] == "complete"
        preparations.append((operation, proof))
        child_folder = clone._operation(home, child)
        if not (child_folder / "operation.json").exists():
            saved = copy.deepcopy(original)
            for field in ("started_at", "completed_at", "project_id", "finish_receipt", "style_sha256"):
                saved.pop(field, None)
            saved.update(operation_id=child, state="prepared", phase="validate", refinement_only=True,
                         refinement_of=source, begin_id="b_" + "5" * 32)
            saved["spec"]["max_calls"] = min(saved["spec"]["max_calls"], 8)
            saved["spec"]["style_id"] = "cr_clone_" + "4" * 12
            clone._write(child_folder / "operation.json", saved)
        return clone.status(home, child, bridge)
    monkeypatch.setattr(routes.cr_style_refine, "prepare", prepare)
    return SimpleNamespace(env=env, source=source, child=child, preparations=preparations)


def test_refinement_requires_explicit_approval_and_completed_owned_style(refined):
    env, source = refined.env, refined.source
    path = f"/cr-style-clone/{source}/refine"
    for payload in ({}, {"acknowledge_costs": False}, {"acknowledge_costs": "true"}):
        assert env.post(path, payload).status_code == 400
    assert not refined.preparations and not env.targets
    folder, saved = clone._saved(env.home, source, env.bridge)
    saved["state"] = "failed"
    clone._write(folder / "operation.json", saved)
    assert env.post(path, {"acknowledge_costs": True}).status_code == 409
    assert not refined.preparations and not env.targets
    env.cfg["key"] = "cr_mcp_different_synthetic_owner"
    assert env.post(path, {"acknowledge_costs": True}).status_code == 409
    assert not refined.preparations and not env.targets


@pytest.mark.parametrize("blocker", ["busy", "api_mode", "readiness", "allowance"])
def test_refinement_honours_native_setup_allowance_and_shared_activity(refined, blocker):
    env = refined.env
    reservation = None
    if blocker == "busy":
        reservation = env.activity.reserve()
    elif blocker == "api_mode":
        env.cfg["brain_mode"] = "channelrecipe"
    elif blocker == "readiness":
        env.checks["readiness"]["ready"] = False
    else:
        env.checks["claude_code"]["allowance"] = {"blocked": True, "message": "Reset at 09:40"}
    try:
        response = env.post(f"/cr-style-clone/{refined.source}/refine", {"acknowledge_costs": True})
        assert response.status_code == 409
        assert not refined.preparations and not env.targets and len(env.workers) == 1
        if blocker == "allowance":
            assert response.json["error"] == "Reset at 09:40"
    finally:
        if reservation:
            env.activity.release(reservation)


def test_refinement_repeated_post_starts_only_one_worker_and_preserves_source(refined):
    env = refined.env
    path = f"/cr-style-clone/{refined.source}/refine"
    source_folder, source = clone._saved(env.home, refined.source, env.bridge)
    original_operation = (source_folder / "operation.json").read_bytes()
    original_style = (env.home / "styles" / (source["spec"]["style_id"] + ".json")).read_bytes()
    response = env.post(path, {"acknowledge_costs": True})
    assert response.status_code == 200 and response.json["operation_id"] == refined.child
    assert response.json["refinement_only"] and response.json["refinement_of"] == refined.source
    assert response.json["state"] == "running" and response.json["max_calls"] == 8
    assert response.json["budget_minutes"] == 22 and "no-store" in response.headers["Cache-Control"]
    assert len(env.targets) == 1 and env.activity.busy()
    assert env.post(path, {"acknowledge_costs": True}).status_code == 409
    env.targets.pop()()
    response = env.post(path, {"acknowledge_costs": True})
    assert response.status_code == 200 and response.json["state"] == "done"
    assert not env.targets and not env.activity.busy() and len(env.workers) == 2
    assert (source_folder / "operation.json").read_bytes() == original_operation
    assert (env.home / "styles" / (source["spec"]["style_id"] + ".json")).read_bytes() == original_style
    assert sum(name == "local_studio_begin" for name, _ in env.calls) == 2


def test_stopped_refinement_is_returned_without_blind_worker_retry(refined):
    env = refined.env
    proof = cr_readiness.proof(env.checks, purpose="creative")
    routes.cr_style_refine.prepare(env.home, refined.source, proof, env.bridge, acknowledge_costs=True)
    folder, saved = clone._saved(env.home, refined.child, env.bridge)
    saved.update(state="failed", phase="validate")
    clone._write(folder / "operation.json", saved)
    response = env.post(f"/cr-style-clone/{refined.source}/refine", {"acknowledge_costs": True})
    assert response.status_code == 200 and response.json["state"] == "failed"
    assert response.json["needs_manual_recovery"] and not env.targets and len(env.workers) == 1


def test_improve_this_style_needs_the_tick_a_saved_coded_style_and_starts_one_worker(env):
    """Improve this style on the study page: approved in so many words, only for a saved style written in code, one
    worker for one change, and a change is decided only once it is drawn."""
    import hashlib
    import cr_animated2d as a2d
    source = env.post("/cr-style-clone/start", env.approved()).json["operation_id"]
    env.targets.pop()()
    path = f"/cr-style-clone/{source}/improve"
    for payload in ({"request": "rounder heads"}, {"request": "rounder heads", "acknowledge_costs": "true"}):
        assert env.post(path, payload).status_code == 400
    assert env.post(path, {"request": "rounder heads", "acknowledge_costs": True}).status_code == 409, "pictures"
    folder, saved = clone._saved(env.home, source, env.bridge)
    style = clone._read(env.home / "styles" / (saved["spec"]["style_id"] + ".json"))
    code = "Kit.renderer({name: 'mine', frame() {}});"
    (env.home / "assets/styles/learned" / style["name"]).mkdir(parents=True)
    (env.home / "assets/styles/learned" / style["name"] / "renderer-v1.js").write_text(code)
    style["look"] = {"visuals": a2d.VISUALS, "animated2d": {"renderer": {
        "file": f"styles/learned/{style['name']}/renderer-v1.js", "version": 1, "state": "passed",
        "sha256": hashlib.sha256(code.encode()).hexdigest()}}}
    clone.republish(folder, saved, style)
    assert env.post(path, {"request": "x", "acknowledge_costs": True}).status_code == 400
    response = env.post(path, {"request": "rounder heads", "acknowledge_costs": True})
    assert response.status_code == 200 and response.json["renderer_improve"] and response.json["state"] == "running"
    assert response.json["refinement_of"] == source and len(env.targets) == 1 and env.activity.busy()
    assert env.post(path, {"request": "calmer camera", "acknowledge_costs": True}).status_code == 409, "one at a time"
    change = response.json["operation_id"]
    assert env.post(f"/cr-style-clone/{change}/improvement/save").status_code == 409, "nothing drawn yet"
    assert env.post(f"/cr-style-clone/{change}/improvement/maybe").status_code == 400
    assert env.get(f"/cr-style-clone/{change}/improvement-picture/elsewhere/0").status_code == 400
    assert env.get(f"/cr-style-clone/{change}/improvement-picture/new/0").status_code == 404
    page = env.get("/cr-style-clone").get_data(as_text=True)
    assert 'id="improve-panel"' in page and "no money" in page and "/improvement/" in page


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
            assert page.locator('body > a[href="/cr-setup"]').is_hidden()
            assert page.locator('nav[aria-label="Workspace"] .navlinks a').all_text_contents() == [
                "Create", "Projects", "Style library", "Accounts"]
            assert page.locator('#base-style option[value="documentary"]').inner_text() == "Editorial Documentary"
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
            page.locator("#look-result").wait_for(state="visible")
            assert page.locator("#look-base").inner_text() == "Editorial Documentary"
            assert "Painted stick figures" in page.locator("#look-recognisable").inner_text()
            assert "Footage cut length" in page.locator("#look-settings").inner_text()
            page.locator("input[name='q-music']").nth(1).check()
            assert not env.targets, "looking never starts a study"
            page.locator("#look-next").click()
            page.locator("#style-label").fill("My science stories")
            page.locator("#example-title").fill("How bees build homes")
            page.locator("#style-brief").fill("Clear diagrams and measured pacing")
            page.locator("#start-button").click()
            assert not env.targets
            page.locator("#usage-approved").check()
            page.locator("#candidate-list input").nth(2).uncheck()
            assert not page.locator("#usage-approved").is_checked()
            assert page.locator("#brief-panel").is_hidden(), "changed references are looked at again first"
            page.locator("#next-button").click()
            page.locator("#look-result").wait_for(state="visible")
            page.locator("#look-next").click()
            page.locator("#usage-approved").check()
            page.locator("#start-button").click()
            page.locator("#progress-panel").wait_for(state="visible")
            operation = page.evaluate("localStorage.getItem('cr.styleStudy')")
            folder, saved = clone._saved(env.home, operation, env.bridge)
            assert [ref["id"] for ref in saved["spec"]["references"]] == [BACKUP, VIDEO]
            assert saved["spec"]["title"] == "How bees build homes" and saved["spec"]["brief"] == "Clear diagrams and measured pacing"
            assert saved["spec"]["base_style"] == "documentary"
            assert saved["spec"]["look_id"].startswith("lk_")
            assert saved["spec"]["answers"] == {"music": "A story score under the whole video"}
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


def test_browser_saved_style_refinement_requires_fresh_consent_and_never_autostarts(refined):
    env = refined.env
    browser_api = pytest.importorskip("playwright.sync_api")
    with browser_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except browser_api.Error as error:
            pytest.skip("Local Chromium unavailable: " + str(error).splitlines()[0])
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 900})
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def respond(route):
                url = urlsplit(route.request.url)
                assert url.hostname == "127.0.0.1"
                path = url.path + (("?" + url.query) if url.query else "")
                response = env.client.open(path, method=route.request.method, base_url=BASE,
                    headers=dict(route.request.headers), data=route.request.post_data)
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.data)
            page.route("**/*", respond)
            page.goto(BASE + "/cr-style-clone?operation=" + refined.source)
            page.locator("#refine-controls").wait_for(state="visible")
            assert not page.locator("#refine-approved").is_checked()
            assert not page.locator("#refine-button").is_enabled()
            assert not env.targets and len(env.workers) == 1
            page.locator("#refine-approved").check()
            assert page.locator("#refine-button").is_enabled()
            page.reload()
            page.locator("#refine-controls").wait_for(state="visible")
            assert not page.locator("#refine-approved").is_checked()
            assert not env.targets
            page.locator("#refine-approved").check()
            page.locator("#refine-button").click()
            page.wait_for_function("(child)=>localStorage.getItem('cr.styleStudy')===child", arg=refined.child)
            assert len(env.targets) == 1 and env.activity.busy()
            assert not page.locator("#refine-approved").is_checked()
            assert page.locator("#refine-controls").is_hidden()
            assert "reuses reference and design" in page.locator("#progress-details").inner_text()
            assert page.locator("#style-board").get_attribute("src") is None
            # Refreshing a saved/running refinement resumes status only.
            page.goto(BASE + "/cr-style-clone")
            page.wait_for_function("(child)=>localStorage.getItem('cr.styleStudy')===child", arg=refined.child)
            assert len(env.targets) == 1 and len(env.workers) == 1
            env.targets.pop()()
            page.reload()
            page.locator("#saved-result").wait_for(state="visible")
            assert not page.locator("#refine-approved").is_checked()
            assert not page.locator("#refine-button").is_enabled()
            assert not env.targets and len(env.workers) == 2 and not errors
        finally:
            browser.close()


def test_continuing_a_prepared_study_checks_the_room_too(env, monkeypatch):
    result = clone.prepare(env.home, env.approved(), cr_readiness.proof(env.checks, purpose="creative"), env.bridge)
    monkeypatch.setattr(clone, "_room", lambda: "Your Mac has 1.0 GB of free disk space")
    response = env.post(f"/cr-style-clone/{result['operation_id']}/continue", {"acknowledge_costs": True})
    assert response.status_code == 409 and "free disk space" in response.json["error"] and not env.workers


def test_browser_the_answer_on_how_the_3d_is_made_carries_through_continue_and_start(env, monkeypatch):
    """Review of Studio 75: Continue put back the recipe guessed before the answer, so a creator who said the
    characters have faces could be built on mannequins. The server's plan for the answer is what the page keeps."""
    browser_api = pytest.importorskip("playwright.sync_api")
    sys.path.insert(0, str(Path(__file__).parent))
    import test_style_map as tsm
    for name, label in (("stories", "2D Life Stories"), ("3d", "3D Reconstruction")):
        (env.home / "styles" / f"{name}.json").write_text(json.dumps({**tsm.STYLES[name], "name": name, "label": label}))
    catalogue = env.studio.styles.catalogue
    env.studio.styles.catalogue = lambda: catalogue() + [{"name": "stories", "label": "2D Life Stories"},
                                                         {"name": "3d", "label": "3D Reconstruction"}]
    recon = tsm.profile({"3d": 0.6, "graphic": 0.4}, **{"pictures__art_style": tsm.RECONSTRUCTION})

    def spawn(home, look_id):
        path = cr_style_look.folder(home, look_id) / "look.json"
        saved = json.loads(path.read_text())
        saved.update(state="done", phase="done", finished_at=__import__("time").time(), sheets=[], not_watched=[],
                     profile=recon)
        saved.pop("mapping", None)
        path.write_text(json.dumps(saved))
    monkeypatch.setattr(cr_style_look, "_spawn", spawn)
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
                path = url.path + (("?" + url.query) if url.query else "")
                response = env.client.open(path, method=route.request.method, base_url=BASE,
                                           headers=dict(route.request.headers), data=route.request.post_data)
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.data)
            page.route("**/*", respond)
            page.goto(BASE + "/cr-style-clone")
            page.locator("#reference-url").fill("https://www.youtube.com/@Ifverse")
            page.locator("#preview-button").click()
            page.locator("#candidate-list input").nth(0).check()
            page.locator("#next-button").click()
            page.locator("#look-result").wait_for(state="visible")
            assert page.evaluate("look.mapping.base") == "3d", "Studio's guess: faceless figures, the 3D recipe"
            options = page.locator("input[name='q-medium']")
            assert options.nth(0).get_attribute("value") == cr_style_map.MEDIUM_FACELESS
            page.locator(f"input[name='q-medium'][value='{cr_style_map.MEDIUM_DESIGNED}']").check()
            page.wait_for_function("document.getElementById('medium-gap').hidden === false")
            page.locator("#look-next").click()
            assert page.locator("#base-style").input_value() == "stories", "Continue keeps the plan for the answer"
            page.locator("#style-label").fill("My science stories")
            page.locator("#example-title").fill("How bees build homes")
            page.locator("#usage-approved").check()
            page.locator("#start-button").click()
            assert "tick that you understand" in page.locator("#notice").inner_text().lower() and not env.targets
            page.locator("#accept-medium").check()
            page.locator("#start-button").click()
            page.locator("#progress-panel").wait_for(state="visible")
            operation = page.evaluate("localStorage.getItem('cr.styleStudy')")
            _, saved = clone._saved(env.home, operation, env.bridge)
            assert saved["spec"]["base_style"] == "stories" and saved["spec"]["accept_medium"] is True
            assert saved["spec"]["answers"]["medium"] == cr_style_map.MEDIUM_DESIGNED
            assert not errors, errors
        finally:
            browser.close()


def test_browser_the_answer_on_animated_2d_characters_replans_and_pictures_need_the_tick(env, monkeypatch):
    """A @DartAnimates clone made AI stills of stick figures the channel animates. The look screen asks, the answer
    re-plans at once, and pictures in place of animation start only once the creator ticks that they understand."""
    browser_api = pytest.importorskip("playwright.sync_api")
    sys.path.insert(0, str(Path(__file__).parent))
    import test_style_map as tsm
    (env.home / "styles" / "stories.json").write_text(json.dumps({**tsm.STYLES["stories"], "name": "stories",
                                                                  "label": "2D Life Stories"}))
    catalogue = env.studio.styles.catalogue
    env.studio.styles.catalogue = lambda: catalogue() + [{"name": "stories", "label": "2D Life Stories"}]
    dart = tsm.animated()

    def spawn(home, look_id):
        path = cr_style_look.folder(home, look_id) / "look.json"
        saved = json.loads(path.read_text())
        saved.update(state="done", phase="done", finished_at=__import__("time").time(), sheets=[], not_watched=[],
                     profile=dart)
        saved.pop("mapping", None)
        path.write_text(json.dumps(saved))
    monkeypatch.setattr(cr_style_look, "_spawn", spawn)
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
                path = url.path + (("?" + url.query) if url.query else "")
                response = env.client.open(path, method=route.request.method, base_url=BASE,
                                           headers=dict(route.request.headers), data=route.request.post_data)
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.data)
            page.route("**/*", respond)
            page.goto(BASE + "/cr-style-clone")
            page.locator("#reference-url").fill("https://www.youtube.com/@DartAnimates")
            page.locator("#preview-button").click()
            page.locator("#candidate-list input").nth(0).check()
            page.locator("#next-button").click()
            page.locator("#look-result").wait_for(state="visible")
            options = page.locator("input[name='q-animation']")
            assert options.nth(0).get_attribute("value") == cr_style_map.ANIMATED_CODE, "Studio's guess first"
            assert page.evaluate("look.mapping.route.medium_gap") == cr_style_map.ANIMATION_GAP
            page.locator(f"input[name='q-animation'][value='{cr_style_map.ANIMATED_PICTURES}']").check()
            page.wait_for_function("!(look.mapping.route||{}).medium_gap")
            page.locator(f"input[name='q-animation'][value=\"{cr_style_map.ANIMATED_CODE}\"]").check()
            page.wait_for_function("document.getElementById('medium-gap').hidden === false")
            assert "can't rig and animate these characters yet" in page.locator("#medium-gap-text").inner_text()
            assert "How the pictures are made" in page.locator("#look-route").inner_text()
            page.locator("#look-next").click()
            page.locator("#style-label").fill("Stick fights")
            page.locator("#example-title").fill("Gojo vs Sukuna: who wins")
            page.locator("#usage-approved").check()
            page.locator("#start-button").click()
            assert "tick that you understand" in page.locator("#notice").inner_text().lower() and not env.targets
            page.locator("#accept-medium").check()
            page.locator("#start-button").click()
            page.locator("#progress-panel").wait_for(state="visible")
            operation = page.evaluate("localStorage.getItem('cr.styleStudy')")
            _, saved = clone._saved(env.home, operation, env.bridge)
            assert saved["spec"]["accept_medium"] is True
            assert saved["spec"]["answers"]["animation"] == cr_style_map.ANIMATED_CODE
            assert not errors, errors
        finally:
            browser.close()


def test_the_page_previews_up_to_three_channels_as_one_mix(env):
    """A customer could only pick videos from one channel on the web; Claude Code could already mix them."""
    mixed = env.post("/cr-style-clone/preview", {"urls": ["https://www.youtube.com/@Ifverse",
                                                          "https://www.youtube.com/@Other"]})
    assert mixed.status_code == 200 and mixed.json["combined"]
    assert mixed.json["reference_urls"] == ["https://www.youtube.com/@Ifverse/videos",
                                            "https://www.youtube.com/@Other/videos"]
    assert [name for name, _ in env.calls] == ["preview", "preview"]
    one = env.post("/cr-style-clone/preview", {"urls": ["https://www.youtube.com/@Ifverse"]})
    assert one.status_code == 200 and "combined" not in one.json
    assert env.post("/cr-style-clone/preview", {"urls": ["a", "b", "c", "d"]}).status_code == 400
    # the study keeps every channel it mixes, so a new attempt lists them all again
    payload = {**env.approved(), "preview_id": mixed.json["preview_id"]}
    started = env.post("/cr-style-clone/start", payload)
    assert started.status_code == 200 and len(started.json["reference_urls"]) == 2


def test_a_learned_style_is_edited_through_the_page_and_studio_reads_it_again(env):
    name = "cr_clone_" + "6" * 12
    (env.home / "styles" / (name + ".json")).write_text(json.dumps({
        "name": name, "label": "Mine", "description": "About bees.", "features": {"offered": ["sound"], "on": []},
        "cr_style_clone": {"operation_id": "op_" + "6" * 24}}))
    got = env.get("/cr-style-clone/style/" + name)
    assert got.status_code == 200 and got.json["name"] == "Mine"
    assert env.get("/cr-style-clone/style/cr_clone_none").status_code == 404
    assert env.get("/cr-style-clone/style/documentary").status_code == 400
    saved = env.post("/cr-style-clone/style/" + name, {"changes": {"name": "My bees", "features_on": ["sound"]}})
    assert saved.status_code == 200 and saved.json["saved"] and saved.json["name"] == "My bees"
    assert ("load_all", None) in env.calls, "Studio reads the edited style at once"
    assert not env.targets, "an edit never starts a study"


def test_a_sample_needs_the_creators_approval_and_runs_on_their_own_accounts(env, monkeypatch):
    import cr_media
    import cr_style_sample
    (env.home / "styles/stories.json").write_text(json.dumps({"name": "stories", "pacing": {"photo_every_s": 5}}))
    started, drawn, engine = [], [], []
    monkeypatch.setattr(cr_style_sample, "start", lambda home, name, style, topic, line, **kw: started.append(
        (name, topic, sorted(kw))) or kw["generate"]("words", "still.jpg", "style sample 1", []) or {
        "sample_id": "sm_" + "7" * 24, "state": "running"})
    env.studio.mv = SimpleNamespace(host_image=lambda path: "url", image_gen=lambda *a, **k: engine.append(a))
    images = {"active": False}
    monkeypatch.setattr(cr_media, "active", lambda task: task == "images" and images["active"])
    monkeypatch.setattr(cr_media, "image", lambda prompt, dest, label, **kw: drawn.append(label))
    body = {"style": "stories", "topic": "How bees build homes"}
    assert env.post("/cr-style-clone/sample", body).status_code == 400 and not started
    env.cfg["costs_accepted"] = False
    assert env.post("/cr-style-clone/sample", {**body, "acknowledge_costs": True}).status_code == 400
    env.cfg["costs_accepted"] = True
    assert env.post("/cr-style-clone/sample", {**body, "style": "none", "acknowledge_costs": True}).status_code == 400
    # drawn only on the creator's own image account with Studio's receipts, never by the engine's other wallets
    held = env.post("/cr-style-clone/sample", {**body, "acknowledge_costs": True})
    assert held.status_code == 400 and "Connect Kie" in held.json["error"] and not started and not engine
    images["active"] = True
    ok = env.post("/cr-style-clone/sample", {**body, "acknowledge_costs": True})
    assert ok.status_code == 200 and started == [("stories", "How bees build homes", ["generate", "host", "voice"])]
    assert drawn == ["style sample 1"] and not engine
    assert env.get("/cr-style-clone/sample/sm_bad/status").status_code == 400
    assert env.get("/cr-style-clone/sample/" + "sm_" + "8" * 24 + "/video.mp4").status_code == 404
    assert not env.targets


def _browser(env):
    browser_api = pytest.importorskip("playwright.sync_api")
    playwright = browser_api.sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
    except browser_api.Error as error:
        playwright.stop()
        pytest.skip("Local Chromium is unavailable: " + str(error).splitlines()[0])
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
    return SimpleNamespace(page=page, errors=errors, close=lambda: (browser.close(), playwright.stop()))


CLONE_PREVIEW = clone.preview


def test_browser_mixing_two_channels_suggests_close_styles_and_tries_one_first(env, monkeypatch):
    import cr_style_sample
    lists = {"@Ifverse": [{"id": VIDEO, "title": "What If One Second Felt Like Ten", "channel": "Ifverse",
                           "duration": 406, "views": 100000}],
             "@gneef": [{"id": THIRD, "title": "Every City Has A Beast", "channel": "Gneef", "views": 10000000,
                         "short": True}]}

    def preview(home, url):
        env.calls.append(("preview", url))
        rows = next(v for k, v in lists.items() if k in url)
        return CLONE_PREVIEW(home, url, learner=SimpleNamespace(_flat=lambda *a: rows))
    monkeypatch.setattr(clone, "preview", preview)
    suggestion = {"id": "stories", "name": "2D Life Stories", "closeness": 91, "why": "both are mostly drawn pictures",
                  "kind": "video", "mine": False, "recommended": True, "same_references": False,
                  "preview_url": "/cr-workspace/preview/recipe/stories.jpg", "use_url": "/?cr-preset=stories",
                  "description": "", "sample": {"can": True, "pictures": 2, "seconds": 5, "cost_usd": 0.05}}
    monkeypatch.setattr(cr_style_look, "similar", lambda *a, **k: [suggestion])
    (env.home / "styles/stories.json").write_text(json.dumps({"name": "stories", "pacing": {"photo_every_s": 5}}))
    env.studio.mv = SimpleNamespace(host_image=lambda path: "url", image_gen=lambda *a, **k: None)
    import cr_media
    monkeypatch.setattr(cr_media, "active", lambda task: task == "images")
    asked = []
    state = {"state": "running"}
    monkeypatch.setattr(cr_style_sample, "start", lambda home, name, style, topic, line, **kw: asked.append(
        (name, topic)) or {"sample_id": "sm_" + "9" * 24, "state": "running", "message": "Drawing two pictures."})
    monkeypatch.setattr(cr_style_sample, "status", lambda home, ident: {
        "sample_id": ident, "state": state["state"], "message": "Here are a few seconds.",
        "video_url": "/cr-style-clone/sample/" + ident + "/video.mp4"})
    b = _browser(env)
    try:
        page = b.page
        page.goto(BASE + "/cr-style-clone")
        page.locator("#add-link").click()
        page.locator("#reference-url").fill("https://www.youtube.com/@Ifverse")
        page.locator("#reference-url-2").fill("https://www.youtube.com/@gneef")
        page.locator("#preview-button").click()
        page.locator("#references").wait_for(state="visible")
        assert page.locator("#mix-note").is_visible() and page.locator("#shorts-note").is_visible()
        assert "Short" in page.locator("#candidate-list").inner_text(), "a Short is listed, never refused"
        page.locator("#candidate-list input").nth(0).check()
        page.locator("#candidate-list input").nth(1).check()
        assert "mixing 2 channels" in page.locator("#selection-summary").inner_text()
        page.locator("#next-button").click()
        page.locator("#look-result").wait_for(state="visible")
        card = page.locator("#similar-list .similar-card")
        assert card.count() == 1 and "91% close" in card.inner_text() and "$0.05" in card.inner_text()
        assert page.locator("#look-next").is_enabled(), "a suggestion never stands in the way of the study"
        assert card.locator("a").get_attribute("href") == "/?cr-preset=stories"
        card.locator("input[type=text]").fill("How bees build homes")
        assert card.locator("button").is_disabled()
        card.locator("input[type=checkbox]").check()
        state["state"] = "done"
        card.locator("button").click()
        card.locator("video").wait_for(state="attached", timeout=8000)
        assert asked == [("stories", "How bees build homes")] and not env.targets
        page.locator("#look-next").click()
        page.locator("#style-label").fill("My mix")
        page.locator("#example-title").fill("How bees build homes")
        page.locator("#usage-approved").check()
        page.locator("#start-button").click()
        page.locator("#progress-panel").wait_for(state="visible")
        operation = page.evaluate("localStorage.getItem('cr.styleStudy')")
        _, saved = clone._saved(env.home, operation, env.bridge)
        assert [r["id"] for r in saved["spec"]["references"]] == [VIDEO, THIRD]
        assert saved["spec"]["reference_urls"] == ["https://www.youtube.com/@Ifverse/videos",
                                                   "https://www.youtube.com/@gneef/videos"]
        assert not b.errors, b.errors
    finally:
        b.close()


def test_browser_a_learned_style_is_edited_and_studied_again_from_its_page(env):
    name = "cr_clone_" + "6" * 12
    (env.home / "styles" / (name + ".json")).write_text(json.dumps({
        "name": name, "label": "Mine", "description": "About bees.", "script_notes": ["Be warm."],
        "look": {"captions": {"off": True}}, "pacing": {"photo_every_s": 3.0},
        "features": {"offered": ["ai_images", "sound"], "on": ["ai_images"]},
        "cr_style_clone": {"operation_id": "op_" + "6" * 24}}))
    b = _browser(env)
    try:
        page = b.page
        page.goto(BASE + "/cr-style-clone?edit=" + name)
        page.locator("#edit-panel").wait_for(state="visible")
        assert page.locator("#reference-panel").is_hidden() and page.locator("#edit-name").input_value() == "Mine"
        assert page.locator("#edit-script-notes").input_value() == "Be warm."
        page.locator("#edit-name").fill("Warm bees")
        page.locator("#edit-script-notes").fill("Be warm.\nEnd on a question.")
        page.locator("#edit-features input[value='sound']").check()
        page.locator("#edit-subtitles").check()
        page.locator("#edit-save").click()
        page.wait_for_function("document.getElementById('notice').textContent.startsWith('Saved')")
        style = json.loads((env.home / "styles" / (name + ".json")).read_text())
        assert style["label"] == "Warm bees" and style["script_notes"] == ["Be warm.", "End on a question."]
        assert style["features"]["on"] == ["ai_images", "sound"] and style["look"]["captions"]["off"] is False
        assert page.locator("#edit-restudy").get_attribute("href") == "/cr-style-clone?restudy=" + name
        page.goto(BASE + "/cr-style-clone?restudy=" + name)
        page.wait_for_function("document.getElementById('notice').textContent.includes('new version of Warm bees')")
        assert page.locator("#reference-panel").is_visible() and not b.errors, b.errors
    finally:
        b.close()


def test_browser_renaming_a_style_sends_only_its_name(env):
    """A real learned style: its look switched on switches the editor doesn't offer, and a pace the slider can't hit
    exactly. Renaming it alone must leave all of that byte for byte as it was."""
    name = "cr_clone_" + "7" * 12
    features = {"offered": ["maps", "motion", "sound"], "on": ["ai_images", "sound"], "channel_on": ["depth"],
                "auto_own": ["ai_images"]}
    (env.home / "styles" / (name + ".json")).write_text(json.dumps({
        "name": name, "label": "Mine", "description": "About  bees,\nmostly.", "script_notes": ["Be warm."] * 12,
        "look": {"captions": {"off": True}}, "pacing": {"photo_every_s": 5.5}, "features": features,
        "cr_style_clone": {"operation_id": "op_" + "7" * 24}}))
    sent = []
    b = _browser(env)
    try:
        page = b.page
        page.on("request", lambda r: sent.append(json.loads(r.post_data)) if r.method == "POST" else None)
        page.goto(BASE + "/cr-style-clone?edit=" + name)
        page.locator("#edit-panel").wait_for(state="visible")
        page.locator("#edit-save").click()
        page.wait_for_function("document.getElementById('notice').textContent === 'Nothing changed.'")
        page.locator("#edit-name").fill("Warm bees")
        page.locator("#edit-save").click()
        page.wait_for_function("document.getElementById('notice').textContent.startsWith('Saved')")
        assert sent == [{"changes": {"name": "Warm bees"}}], "only what the creator changed is sent"
        style = json.loads((env.home / "styles" / (name + ".json")).read_text())
        assert style["label"] == "Warm bees" and json.dumps(style["features"]) == json.dumps(features)
        assert style["pacing"] == {"photo_every_s": 5.5} and style["script_notes"] == ["Be warm."] * 12
        assert style["description"] == "About  bees,\nmostly." and not b.errors, b.errors
    finally:
        b.close()


def test_browser_a_channel_written_in_code_reads_plainly_from_the_look_to_the_saved_style(env, monkeypatch, tmp_path):
    """The owner's Windows test of a stick-figure channel: "Look at these videos" stayed live while the look ran, the
    bar went back for every video, the recipe promised "a drawn or generated picture for every line", the cost read
    "$0.02 a minute ( a minute)", the example title had no ideas, the result gave two scores and two verdicts, the test
    shots spilled under the sidebar, and section numbers ran 02, 02, 03."""
    import os
    import time as _time
    import cr_animated2d
    from PIL import Image
    sys.path.insert(0, str(Path(__file__).parent))
    import test_style_map as tsm
    monkeypatch.setattr(cr_animated2d, "available", lambda home=None: True)
    (env.home / "styles" / "stories.json").write_text(json.dumps({**tsm.STYLES["stories"], "name": "stories",
                                                                  "label": "2D Life Stories"}))
    catalogue = env.studio.styles.catalogue
    env.studio.styles.catalogue = lambda: catalogue() + [{"name": "stories", "label": "2D Life Stories"}]
    looks = {}

    def spawn(home, look_id):
        path = cr_style_look.folder(home, look_id) / "look.json"
        saved = json.loads(path.read_text())
        saved.update(phase="download", percent=40, video=2, videos=2, left_s=200, updated_at=_time.time(),
                     message="Video 2 of 2 · Downloading 0:00-3:00 of Backup 1.")
        path.write_text(json.dumps(saved))
        looks["path"] = path
    monkeypatch.setattr(cr_style_look, "_spawn", spawn)
    shots = os.environ.get("CR_SHOTS")                        # a folder to save screenshots in, when looking by eye
    b = _browser(env)
    try:
        page = b.page
        page.goto(BASE + "/cr-style-clone")
        page.locator("#reference-url").fill("https://www.youtube.com/@NobodyGetsIn")
        page.locator("#preview-button").click()
        page.locator("#candidate-list input").nth(0).check()
        page.locator("#candidate-list input").nth(1).check()
        page.locator("#next-button").click()
        page.wait_for_function("document.getElementById('look-message').textContent.includes('minutes left')")
        assert page.locator("#next-button").is_disabled(), "no second look while one runs"
        assert page.locator("#look-message").inner_text() == "About 4 minutes left."
        assert page.locator("#look-phase").inner_text().startswith("Video 2 of 2")
        if shots:
            page.screenshot(path=os.path.join(shots, "1-look-running.png"))
        saved = json.loads(looks["path"].read_text())
        host = ["Yes, design one for my channel", "Yes, I'll upload mine", "No, a new cast for each story"]
        saved.update(state="done", phase="done", finished_at=_time.time(), sheets=[], not_watched=[],
                     profile={**tsm.animated(), "recognisable": ["Simple stick figure characters",
                                                                 "Stick-figure characters", "Red cap mascot"],
                              "questions": [{"id": "host", "ask": "Worker appears again and again. Should every "
                                             "video keep one recurring character like that?", "options": host}]},
                     titles=["100 Strangers, 1 Bunker: Who Survives 30 Days", "Backup 1"])
        saved.pop("mapping", None)
        looks["path"].write_text(json.dumps(saved))
        page.locator("#look-result").wait_for(state="visible", timeout=8000)
        assert not page.locator("#next-button").is_disabled()
        made = page.locator("input[name='q-made']")
        assert made.nth(0).get_attribute("value") == cr_style_map.MADE_CODE, "the saved answer is unchanged"
        label = page.locator("#look-questions label").nth(0).inner_text()
        assert label.startswith("Written in code") and "renderer" not in label
        base = page.locator("#look-base-why").inner_text()
        assert "drawn in code" in base and "drawn or generated picture" not in base
        cost = page.locator("#look-cost").inner_text()
        assert cost.startswith("Pictures cost nothing") and "( a minute)" not in cost and "a minute on" not in cost
        assert page.locator("#look-recognisable li").count() == 2, "near-duplicate chips are one chip"
        said = page.locator("#look-panel").inner_text()
        assert "Picture model" not in said and "What the pictures do" not in said, "no AI-picture rows for code"
        assert said.count("10 to 25 minutes") == 1 and "three.js" not in said and "picture costs yet" not in said
        # written in code, the channel's character is drawn in code: nowhere to upload one, so "Yes, I'll upload
        # mine" shows only for pictures, and going back to code puts the answer back on Studio's design
        upload = page.locator("fieldset[data-q='host'] label", has_text="upload mine")
        assert upload.is_hidden() and page.locator("input[name='q-host']").nth(0).is_checked()
        made.nth(1).check()
        upload.wait_for(state="visible", timeout=8000)
        upload.locator("input").check()
        assert page.evaluate("answers.host") == host[1]
        made.nth(0).check()
        upload.wait_for(state="hidden", timeout=8000)
        assert page.locator("input[name='q-host']").nth(0).is_checked() and page.evaluate("answers.host") == host[0]
        if shots:
            page.screenshot(path=os.path.join(shots, "2-look-result.png"), full_page=True)
        page.locator("#look-next").click()
        assert page.locator("#brief-panel .number").inner_text() == "03 / MAKE IT YOURS"
        assert page.locator("#visual-mode-row").is_hidden(), "a coded style has no 'video look' choice"
        ideas = page.locator("#title-idea-list button")
        assert 3 <= ideas.count() <= 5 and page.locator("#title-ideas").is_visible()
        first = ideas.nth(0).inner_text()
        ideas.nth(0).click()
        assert page.locator("#example-title").input_value() == first and first != "Backup 1"
        page.locator("#example-title").fill(first + " (mine)")                  # still the creator's to change
        assert "three.js" not in page.locator("#likeness-cost").inner_text()
        if shots:
            page.screenshot(path=os.path.join(shots, "3-brief.png"), full_page=True)
        page.locator("#style-label").fill("2d Survival Stories")
        page.locator("#usage-approved").check()
        page.locator("#start-button").click()
        page.locator("#progress-panel").wait_for(state="visible")
        assert page.locator("#look-next").is_hidden() and page.locator("#look-back").is_hidden(), "no stale buttons"
        assert page.locator("#progress-panel .number").inner_text() == "04 / STUDY & SAVE"
        operation = page.evaluate("localStorage.getItem('cr.styleStudy')")
        _, saved_op = clone._saved(env.home, operation, env.bridge)
        assert saved_op["spec"]["renderer"] == "2d" and saved_op["spec"]["title"] == first + " (mine)"
        assert saved_op["spec"]["answers"]["host"] == host[0]

        # the saved style: one score, one verdict, its test shots beside the channel's frames
        pics = []
        for k in range(3):
            p = tmp_path / f"pic_{k}.jpg"
            Image.new("RGB", (1920, 1080), (40 + 50 * k, 90, 120)).save(p)
            pics.append(p)
        monkeypatch.setattr(clone, "renderer_pictures", lambda home, op, bridge, style=None: {
            "shots": pics[:3], "channel": pics[:2]})
        ready = {**clone.status(env.home, operation, env.bridge), "state": "done", "style_ready": True, "phase": "complete",
                 "percent": 100, "style": "cr_clone_x", "label": "2d Survival Stories", "message": "Your style is saved.",
                 "likeness": {"state": "done", "verdict": "close", "scores": {"overall": 5.67, "characters": 6}},
                 "renderer": {**clone.renderer_verdict({"look": {"visuals": "cr_animated2d", "animated2d": {
                     "renderer": {"state": "passed", "overall": 5.67, "bar": 5.5}}}}), "shots": 3, "channel_frames": 2}}
        monkeypatch.setattr(clone, "status", lambda home, op, bridge: dict(ready))
        page.evaluate("poll()")
        page.locator("#renderer-panel").wait_for(state="visible")
        assert page.locator("#likeness-panel").is_hidden(), "one score: the animation's"
        text = page.locator("#saved-result").inner_text()
        assert text.count("/10") == 1 and "5.7/10" in text and "Medium 0" not in text and "6/10" not in text
        assert page.locator("#renderer-text").inner_text().startswith("Claude wrote this channel's animation in code")
        assert page.locator("#renderer-title").inner_text() == "Claude’s animation for this channel: close to the channel"
        assert page.locator("#renderer-shots img").count() == 5
        grid = page.locator("#renderer-shots").bounding_box()
        aside = page.locator("aside").bounding_box()
        assert grid["x"] + grid["width"] <= aside["x"], "the shots stay out from under the sidebar"
        assert page.locator("#anchor-panel").is_hidden() and page.locator("#refine-controls").is_hidden()
        assert page.inner_text("#caption-evidence").startswith("The study inspected actual frames")
        # a Shorts clone whose references couldn't be downloaded (Lee, 2026-10-30): said plainly, nothing to choose
        short = clone.renderer_verdict({"format": "short", "look": {"visuals": "cr_animated3d", "animated2d": {
            "renderer": {"state": "failed", "why": "too few reference frames to build a renderer from"}}}})
        monkeypatch.setattr(clone, "status", lambda home, op, bridge: {
            **ready, "renderer": {**short, "shots": 0, "channel_frames": 0}, "frames_inspected": False})
        page.evaluate("poll()")
        page.wait_for_function("document.getElementById('renderer-title').textContent === 'How its videos are made'")
        assert "recipe's own engine" in page.inner_text("#renderer-text")
        assert page.locator("#use-pictures").is_hidden() and page.locator("#improve-panel").is_hidden()
        assert page.inner_text("#caption-evidence").startswith("Studio couldn’t download the reference videos")
        monkeypatch.setattr(clone, "status", lambda home, op, bridge: dict(ready))
        page.evaluate("poll()")
        page.wait_for_function("document.getElementById('renderer-title').textContent.startsWith('Claude')")
        if shots:
            page.wait_for_timeout(300)
            page.screenshot(path=os.path.join(shots, "4-saved.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 900})
            page.wait_for_timeout(200)
            page.screenshot(path=os.path.join(shots, "5-saved-phone.png"), full_page=True)
        assert not b.errors, b.errors
    finally:
        b.close()


def test_the_channels_graphics_are_studied_only_with_consent_and_only_for_a_coded_style_now_in_pictures(env):
    """The opt-in graphics study (109 review): never without the tick, never for a style it doesn't fit."""
    result = env.post("/cr-style-clone/start", env.approved())
    source = result.json["operation_id"]
    env.targets.pop()()
    path = f"/cr-style-clone/{source}/graphics"
    for payload in ({}, {"acknowledge_costs": False}, {"acknowledge_costs": "true"}):
        assert env.post(path, payload).status_code == 400
    refused = env.post(path, {"acknowledge_costs": True})
    assert refused.status_code == 409 and "Only a style written in code" in refused.json["error"]
    assert not env.targets, "no worker started"
