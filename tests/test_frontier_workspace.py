"""Workspace reads preserve native controls and never request creative work."""
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from flask import Flask, jsonify, request
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_clone as clone
import cr_workspace as workspace

BASE = "http://127.0.0.1:7860"
TOKEN = "synthetic-workspace-token"
PROJECT = "crv_" + "1" * 24
OPERATION = "op_" + "2" * 24


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "cr_workspace.html").write_text(
        '<html><head><script>window.CR_WORKSPACE={token:__LOCAL_TOKEN_JSON__,preset:__PRESET_JSON__};</script></head><body>CR workspace</body></html>')
    (tmp_path / "cr_workspace.js").write_text("window.workspaceLoaded=true;")
    (tmp_path / "cr_workspace.css").write_text("body{color:#fff}")
    (tmp_path / "cr-release.json").write_text('{"version":"synthetic-workspace"}')
    cfg = {"key": "cr_mcp_synthetic_account", "url": "https://channelrecipe.com"}
    def forbidden(*args, **kwargs):
        pytest.fail("A workspace GET made a cloud, generation or synchronization request.")
    bridge = SimpleNamespace(load_settings=lambda: dict(cfg), _mcp_tool=forbidden,
        call=forbidden, prepare_runtime=forbidden, project_progress=forbidden,
        finish_project=forbidden)
    monkeypatch.setattr(workspace, "cr_bridge", bridge)
    rows = [{"name": "3d", "label": "Original renderer", "tab": "3d", "features": {"on": ["threed"]}},
            {"name": "cr_clone_abcdef", "label": "My precise 2D style", "tab": "production"}]
    styles = SimpleNamespace(STYLES={"3d": {"private_prompt": "never expose"},
        "hidden-variant": {"label": "Hidden"},
        "cr_clone_abcdef": {"cr_style_clone": {"visual_mode": "reference_only"}, "prompts": {"secret": "private"}}})
    calls = []
    app = Flask(__name__)
    @app.get("/")
    def original_page():
        return "Original renderer UI"
    @app.get("/api/channels")
    def original_channels():
        calls.append("channels")
        return jsonify(channels=rows, features=[{"key": "threed"}], caps={"threed": True}, prices={"images": 2}, dlc={"maps": True})
    @app.get("/api/queue")
    def original_queue():
        return jsonify(busy=False, jobs=[])
    @app.post("/api/run")
    def original_run():
        return jsonify(queued=request.json["titles"])
    @app.before_request
    def original_guards():
        if request.method == "POST" and request.headers.get("X-CR-Local-Token") != TOKEN:
            return jsonify(error="local token required"), 403
    jobs = {}
    engine = SimpleNamespace(find_job=lambda slug: jobs.get(slug))
    studio = SimpleNamespace(app=app, styles=styles, mv=engine, JOBS={})
    activity = SimpleNamespace(lock=threading.RLock(), busy=lambda: False)
    original_views = dict(app.view_functions)
    workspace.install(studio, activity, token=TOKEN, home=tmp_path)
    return SimpleNamespace(app=app, client=app.test_client(), home=tmp_path, calls=calls, rows=rows,
        jobs=jobs, studio=studio, bridge=bridge, cfg=cfg, activity=activity,
        originals=original_views, get=lambda path, **kwargs: app.test_client().get(path, base_url=BASE, **kwargs))


def test_workspace_replaces_only_root_and_preserves_original_controls(env):
    for endpoint, original in env.originals.items():
        assert (env.app.view_functions[endpoint] is original) == (endpoint != "original_page")
    response = env.get("/")
    assert response.status_code == 200 and response.headers["X-CR-Workspace"] == "1"
    assert "CR workspace" in response.text and TOKEN in response.text
    assert "__LOCAL_TOKEN_JSON__" not in response.text and "__PRESET_JSON__" not in response.text
    assert '"preset":' not in response.text and "preset:null" in response.text
    assert env.get("/api/channels").json["channels"] == env.rows
    assert env.get("/api/queue").json == {"busy": False, "jobs": []}
    assert env.client.post("/api/run", base_url=BASE, json={"titles": ["New title"]}).status_code == 403
    assert env.client.post("/api/run", base_url=BASE, json={"titles": ["New title"]},
        headers={"X-CR-Local-Token": TOKEN}).json == {"queued": ["New title"]}


def test_catalog_has_actual_capabilities_and_ids_without_private_style_fields(env):
    response = env.get("/cr-workspace/catalog")
    assert response.status_code == 200
    assert response.json["features"] == [{"key": "threed"}] and response.json["caps"] == {"threed": True}
    assert response.json["prices"] == {"images": 2} and response.json["dlc"] == {"maps": True}
    assert [row["name"] for row in response.json["channels"]] == [row["name"] for row in env.rows]
    assert response.json["channels"][0]["label"] == "Cinematic Reconstruction"
    assert response.json["channels"][1]["label"] == "My precise 2D style"
    assert response.json["channels"][1]["visual_policy"] == "reference_only"
    assert "automatic" not in response.json
    assert "private_prompt" not in response.text and "never expose" not in response.text and "prompts" not in response.text
    assert env.rows[0]["label"] == "Original renderer"


def test_adaptive_documentary_keeps_actual_engine_identity_and_all_options(env):
    row = {"name": "documentary", "label": "Original documentary", "tab": "production",
        "auto_style": True, "features": {"offered": ["youtube", "motion", "sound"],
        "on": ["youtube", "motion"], "prefer": "youtube"}, "custom": False,
        "character": "", "pictures_per_min": 4, "wpm": 160, "render_x": 1.0,
        "mix": {"footage": 0.8}, "cost": {"images": 2}, "sample": True}
    original = json.loads(json.dumps(row))
    env.rows.append(row)
    result = env.get("/cr-workspace/catalog").json
    automatic = result["automatic"]
    assert automatic["name"] == "documentary" and automatic["auto_style"] is True
    assert automatic["label"] == "Adaptive Documentary"
    assert automatic["art_url"].endswith("/automatic.svg")
    for field in ("tab", "features", "auto_style", "custom", "character", "pictures_per_min",
                  "wpm", "render_x", "mix", "cost", "sample"):
        assert automatic[field] == original[field]
    assert [entry["name"] for entry in result["channels"]] == [entry["name"] for entry in env.rows]
    assert not any(entry["name"] == "automatic" for entry in result["channels"])
    assert row == original


@pytest.mark.parametrize("entry", [
    {"name": "documentary", "label": "Without learning"},
    {"name": "documentary", "auto_style": False},
    {"name": "custom-documentary", "auto_style": True},
])
def test_adaptive_recipe_exists_only_when_original_documentary_supports_learning(env, entry):
    env.rows.append(entry)
    assert "automatic" not in env.get("/cr-workspace/catalog").json


def test_valid_preset_and_saved_feature_choices_transfer_without_authoring(env):
    (env.home / ".cr-setup-proof.json").write_text(json.dumps({"style": "cr_clone_abcdef",
        "setup": {"local_version": "synthetic-workspace"}, "features": {"threed": False, "sound": True},
        "secret": "not injected"}))
    response = env.get("/?cr-preset=cr_clone_abcdef")
    assert 'preset:{"style": "cr_clone_abcdef", "tab": "production", "features": {"threed": false, "sound": true}}' in response.text
    assert "not injected" not in response.text
    (env.home / "cr-release.json").write_text('{"version":"newer-release"}')
    assert '"features"' not in env.get("/?cr-preset=cr_clone_abcdef").text


def test_malformed_saved_setup_proof_does_not_block_the_workspace(env):
    (env.home / ".cr-setup-proof.json").write_text('{"style":"cr_clone_abcdef","setup":"invalid"}')
    response = env.get("/?cr-preset=cr_clone_abcdef")
    assert response.status_code == 200 and '"style": "cr_clone_abcdef"' in response.text
    assert '"features"' not in response.text


def test_inline_values_are_script_safe_even_if_a_future_token_contains_markup():
    value = '</script><script>alert("unsafe")</script>&'
    encoded = workspace._json_script(value)
    assert "<" not in encoded and ">" not in encoded and "&" not in encoded
    assert json.loads(encoded) == value


@pytest.mark.parametrize("name", ["hidden-variant", "unknown", "../private", "<script>alert(1)</script>"])
def test_unknown_hidden_or_unsafe_preset_cannot_select_engine_state(env, name):
    response = env.get("/", query_string={"cr-preset": name})
    assert response.status_code == 200 and "preset:null" in response.text
    assert "<script>alert(1)</script>" not in response.text


@pytest.mark.parametrize("path", ["/", "/cr-workspace/catalog", "/cr-workspace/activity",
    "/cr-workspace/project/example", "/cr-workspace/studio.js", "/cr-workspace/studio.css", "/cr-workspace/art/3d.svg"])
def test_local_workspace_surfaces_reject_remote_host_and_origin(env, path):
    assert env.client.get(path, base_url="http://rebound.example:7860").status_code == 403
    assert env.get(path, headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert env.calls == []


@pytest.mark.parametrize("path,mimetype", [("/cr-workspace/studio.js", "application/javascript"),
    ("/cr-workspace/studio.css", "text/css"), ("/cr-workspace/art/3d.svg", "image/svg+xml")])
def test_owned_static_assets_are_private_and_correctly_typed(env, path, mimetype):
    response = env.get(path)
    assert response.status_code == 200 and response.mimetype == mimetype
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_artwork_never_serves_arbitrary_paths_and_script_symlink_is_rejected(env):
    assert env.get("/cr-workspace/art/cr_bridge.py").status_code == 400
    assert env.get("/cr-workspace/art/%2e%2e%2fcr_bridge.py").status_code in {400, 404}
    outside = env.home.parent / "private.js"
    outside.write_text("private credential")
    (env.home / "cr_workspace.js").unlink()
    (env.home / "cr_workspace.js").symlink_to(outside)
    response = env.get("/cr-workspace/studio.js")
    assert response.status_code == 400 and "private credential" not in response.text


def save_study(env, operation=OPERATION, *, owner=None, title="My study"):
    folder = clone._operation(env.home, operation)
    folder.mkdir(parents=True)
    saved = {"operation_id": operation, "owner": owner or clone._owner(env.bridge),
        "state": "running", "phase": "build", "started_at": 1,
        "spec": {"title": title, "label": "My graphic style", "reference_url": "https://youtube.com/@creator",
            "references": [{"id": "abcdefghijk"}], "budget_minutes": 22, "max_calls": 40,
            "brief": "private style brief", "style_id": "cr_clone_abcdef"},
        "proof": {"secret": "private model setup"}}
    clone._write(folder / "operation.json", saved)
    return folder


def test_activity_only_reads_owned_local_studies_and_never_syncs_or_generates(env):
    save_study(env)
    save_study(env, "op_" + "3" * 24, owner="foreign-owner", title="Foreign study")
    env.activity.busy = lambda: True
    response = env.get("/cr-workspace/activity")
    assert response.status_code == 200 and response.json["busy"] is True
    assert len(response.json["operations"]) == 1
    operation = response.json["operations"][0]
    assert operation["operation_id"] == OPERATION and operation["phase"] == "build"
    assert operation["title"] == "My study" and operation["label"] == "My graphic style"
    assert operation["link"] == "/cr-style-clone?operation=" + OPERATION
    assert set(operation) == {"operation_id", "state", "phase", "title", "label", "percent", "elapsed_seconds", "link"}
    assert all(value not in response.text for value in ("private style brief", "private model setup", "Foreign study", env.cfg["key"]))


def test_project_lookup_is_local_hint_and_resume_auth_route_is_not_invoked(env):
    job = env.home / "output/documentary/_work/my-video"
    job.mkdir(parents=True)
    env.jobs["my-video"] = job
    (job / "cr-project.json").write_text(json.dumps({"id": PROJECT, "brain_mode": "claudecode", "status": "failed",
        "title": "My video", "style": "documentary", "job_spec": {"script_text": "private script", "extra": "private instruction"}}))
    response = env.get("/cr-workspace/project/my-video")
    assert response.json["project_id"] == PROJECT and response.json["available_for_resume"] is True
    assert response.json["title"] == "My video" and response.json["link"] == "/editor/my-video"
    assert "private script" not in response.text and "private instruction" not in response.text
    env.studio.JOBS["my-video"] = {"state": "running"}
    assert env.get("/cr-workspace/project/my-video").json["available_for_resume"] is False
    env.studio.JOBS["my-video"] = {"state": "done"}
    assert env.get("/cr-workspace/project/my-video").json["available_for_resume"] is False


def test_project_path_outside_output_and_symlinked_marker_are_not_read(env):
    outside = env.home / "private"
    outside.mkdir()
    (outside / "cr-project.json").write_text(json.dumps({"id": PROJECT, "secret": "private credential"}))
    env.jobs["outside"] = outside
    assert env.get("/cr-workspace/project/outside").status_code == 404
    job = env.home / "output/documentary/_work/symlinked"
    job.mkdir(parents=True)
    env.jobs["symlinked"] = job
    (job / "cr-project.json").symlink_to(outside / "cr-project.json")
    response = env.get("/cr-workspace/project/symlinked")
    assert response.status_code == 400 and "private credential" not in response.text


def test_live_render_metrics_exclude_previous_projects_setup_and_private_fields(env):
    import cr_manimgx
    cr_manimgx._write(env.home / ".cr-manimgx/progress.json", {"source":"private source", "scenes": {
        "old": {"scene_id":"old", "updated_at":9, "frames_done":10},
        "setup": {"scene_id":"setup", "updated_at":11, "frames_done":10},
        "current": {"scene_id":"current", "updated_at":11, "frames_done":30, "total_frames":60,
                    "elapsed_seconds":1.5, "state":"running", "stage":"render", "source":"private source"}}})
    env.studio.JOBS["active"]={"state":"running", "started":10}
    result=env.get("/cr-workspace/render/active")
    assert result.status_code==200 and len(result.json["scenes"])==1
    assert result.json["scenes"][0]["scene_id"]=="current"
    assert result.json["scenes"][0]["frames_done"]==30 and "private" not in result.text
    env.studio.JOBS["active"]["state"]="done"
    assert env.get("/cr-workspace/render/active").json=={"scenes":[]}
    assert env.get("/cr-workspace/render/unknown").json=={"scenes":[]}
