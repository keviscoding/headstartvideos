"""Finished-scene controls preserve ownership, native argv and stopped work."""
import json
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace

from flask import Flask, jsonify, request
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_3d_routes as routes


@pytest.fixture
def local(tmp_path, monkeypatch):
    job = tmp_path / "output" / "3d" / "_work" / "example"
    (job / "threed" / "qa").mkdir(parents=True)
    parent = "crv_" + "1" * 24
    for name, data in {"cr-project.json": {"id": parent, "brain_mode": "claudecode"},
        "threed/board_final.json": {"moments": [{"scenes": [{"id": "scene_a"}, {"id": "scene_b"}]}]},
        "threed/lock.json": {}, "threed/qa/qa.json": {"flags": {"scene_a": {"severity": 3, "note": "Black frame", "codes": ["dark"]}}}}.items():
        (job / name).write_text(json.dumps(data))
    (job / "video.mp4").write_bytes(b"saved video")
    app = Flask("3d-controls")
    studio = SimpleNamespace(app=app, OUT=tmp_path / "output", SAFE=re.compile(r"^[a-zA-Z0-9_-]+$"),
        _job=lambda slug: job if slug == "example" else tmp_path / slug,
        mv=SimpleNamespace(final_video=lambda job: job / "video.mp4"), QUEUE=[], LOCK=threading.Lock())
    state = {"busy": False, "mode": "claudecode", "ready": True, "parent_status": "done", "threads": [], "released": []}
    activity = SimpleNamespace(busy=lambda: state["busy"], reserve=lambda: state.update(busy=True) or "reservation",
        release=lambda token: (state["released"].append(token), state.update(busy=False)))
    monkeypatch.setattr(routes.cr_bridge, "load_settings", lambda: {"brain_mode": state["mode"]})
    monkeypatch.setattr(routes.cr_bridge, "project_progress", lambda pid: {"project": {"id": pid, "status": state["parent_status"]},
        "job_spec": {"title": "Example", "style": "3d", "minutes": 1, "burn_subs": True}})
    monkeypatch.setitem(sys.modules, "cr_3d", SimpleNamespace(operation_status=lambda *a, **kw: {}))
    class Deferred:
        def __init__(self, target, daemon): self.target = target
        def start(self): state["threads"].append(self)
    monkeypatch.setattr(routes.threading, "Thread", Deferred)
    routes.install(studio, activity, lambda: SimpleNamespace(get_json=lambda: {"readiness": {"ready": state["ready"]}}))
    return SimpleNamespace(job=job, studio=studio, state=state, client=app.test_client(), parent=parent)


def test_saved_inspection_status_is_light_and_excludes_private_paths(local):
    d = local.client.get("/cr-3d/example").get_json()
    assert d["available"] and [s["id"] for s in d["scenes"]] == ["scene_a", "scene_b"]
    assert all(s["at"] == 0 for s in d["scenes"])
    assert d["flags"][0]["note"] == "Black frame"
    assert str(local.job) not in json.dumps(d)
    assert "command" not in d and not local.state["threads"]


@pytest.mark.parametrize("scene", ["--no-verify", "../../other", "scene_a;touch bad", "not_in_board"])
def test_unrecognized_scene_cannot_become_a_cli_option(local, scene):
    response = local.client.post("/cr-3d/example", json={"action": "redo", "scene_ids": [scene]})
    assert response.status_code == 409 and not local.state["threads"]


@pytest.mark.parametrize("field,value", [("busy", True), ("ready", False), ("mode", "channelrecipe"), ("parent_status", "failed")])
def test_paid_work_requires_idle_native_ready_and_finished_owned_source(local, field, value):
    local.state[field] = value
    assert local.client.post("/cr-3d/example", json={"action": "qa"}).status_code == 409
    assert not local.state["threads"]


def test_review_launch_reserves_context_and_uses_owned_worker_without_shell(local, monkeypatch):
    calls = []
    monkeypatch.setattr(routes.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or SimpleNamespace(returncode=0))
    monkeypatch.setitem(sys.modules, "cr_3d", SimpleNamespace(operation_status=lambda *a, **kw: {"state": "done", "operation_project_id": "crv_" + "2" * 24}))
    response = local.client.post("/cr-3d/example", json={"action": "qa"})
    assert response.status_code == 200 and local.state["busy"]
    assert local.client.post("/cr-3d/example", json={"action": "redo", "from_qa": True}).status_code == 409
    local.state["threads"].pop().target()
    assert len(calls) == 1 and "shell" not in calls[0][1]
    assert calls[0][0][2:5] == ["--project", local.parent, "--operation"]
    assert re.fullmatch(r"op_[a-f0-9]{24}", calls[0][0][5])
    assert calls[0][0][6:8] == ["--", str(routes.HOME / "threed.py")]
    assert calls[0][0][-2:] == ["qa", str(local.job)]
    assert not local.state["busy"] and local.state["released"] == ["reservation"]
    assert json.loads((local.job / "cr-project.json").read_text())["id"] == local.parent


def test_failed_operation_cannot_be_replayed_by_repeated_button(local, monkeypatch):
    monkeypatch.setattr(routes.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    assert local.client.post("/cr-3d/example", json={"action": "qa"}).status_code == 200
    local.state["threads"].pop().target()
    status = local.client.get("/cr-3d/example").get_json()
    assert status["state"] == "failed" and status["needs_manual_recovery"]
    assert local.client.post("/cr-3d/example", json={"action": "qa"}).status_code == 409
    assert not local.state["threads"]


def test_repair_keeps_original_verification_and_literal_feedback(local):
    note = "Make the bee visible; $(literal feedback)"
    args = routes.command("redo", local.job, {"scene_ids": ["scene_a"], "note": note})
    assert args == ["redo", str(local.job), "scene_a", "--note", note]
    assert "--no-verify" not in args
    assert routes.command("redo", local.job, {"from_qa": True})[-3:] == ["--from-qa", "--min-severity", "2"]


def test_stopped_work_requires_explicit_new_work_acknowledgement(local):
    (local.job / ".cr-3d-ui.json").write_text(json.dumps({"state": "failed", "needs_manual_recovery": True}))
    assert local.client.post("/cr-3d/example", json={"action": "qa"}).status_code == 409
    assert local.client.post("/cr-3d/example", json={"action": "qa", "acknowledge_new_work": True}).status_code == 200
    assert len(local.state["threads"]) == 1


def test_new_acknowledged_action_cannot_bypass_an_uncertain_cloud_stage(local, monkeypatch):
    (local.job / ".cr-3d-ui.json").write_text(json.dumps({"state": "failed", "needs_manual_recovery": True,
        "operation_project_id": "crv_" + "2" * 24}))
    monkeypatch.setattr(routes.cr_bridge, "project_progress", lambda *a: {"stages": [{"status": "authorized"}]})
    assert local.client.post("/cr-3d/example", json={"action": "qa", "acknowledge_new_work": True}).status_code == 409
    assert not local.state["threads"]


def test_assembly_completion_can_sync_without_generation(local, monkeypatch):
    project = "crv_" + "3" * 24
    (local.job / ".cr-3d-ui.json").write_text(json.dumps({"action": "assemble", "state": "running",
        "operation_project_id": project, "needs_reassembly": True}))
    receipt = {"project_id": project, "status": "done", "artifacts": []}
    (local.job / ".cr-finish-pending.json").write_text(json.dumps(receipt))
    assert local.client.get("/cr-3d/example").get_json()["state"] == "pending"
    synced = []
    monkeypatch.setattr(routes.cr_bridge, "finish_project", lambda r: synced.append(r))
    assert local.client.post("/cr-3d/example", json={"action": "sync"}).status_code == 200
    result = local.client.get("/cr-3d/example").get_json()
    assert result["state"] == "done" and not result["needs_reassembly"]
    assert synced == [receipt] and not local.state["threads"]


def test_assembly_startup_failure_is_stopped_not_permanently_running(local):
    (local.job / ".cr-3d-ui.json").write_text(json.dumps({"action": "assemble", "state": "running",
        "operation_project_id": "crv_" + "3" * 24, "needs_reassembly": True}))
    result = local.client.get("/cr-3d/example").get_json()
    assert result["state"] == "failed" and result["needs_manual_recovery"] and result["needs_reassembly"]


def test_live_worker_journal_is_normal_progress_not_stopped_work(local, monkeypatch):
    local.state["busy"] = True
    (local.job / ".cr-3d-ui.json").write_text(json.dumps({"action": "qa", "state": "running", "command": ["qa", str(local.job)]}))
    monkeypatch.setitem(sys.modules, "cr_3d", SimpleNamespace(operation_status=lambda *a, **kw: {
        "state": "running", "needs_manual_recovery": True, "message": "Stopped"}))
    result = local.client.get("/cr-3d/example").get_json()
    assert result["state"] == "running" and not result["needs_manual_recovery"]
    assert "inspecting" in result["message"]


def test_reassembly_uses_original_validation_and_same_job_queue(local, monkeypatch):
    (local.job / ".cr-3d-ui.json").write_text(json.dumps({"state": "done", "needs_reassembly": True}))
    new_id = "crv_" + "3" * 24
    monkeypatch.setattr(routes.cr_readiness, "preset", lambda *a: {"ready": True, "proof": {"purpose": "video"}})
    monkeypatch.setattr(routes.cr_bridge, "save_settings", lambda *a: None)
    monkeypatch.setattr(routes.cr_bridge, "begin_project", lambda *a, **kw: {"id": new_id})
    dispatched = []
    local.studio._ensure_dispatcher = lambda: dispatched.append(True)
    @local.studio.app.post("/api/run")
    def api_run():
        data = request.get_json()
        assert data["steps"] == ["video"] and data["burn_subs"] and not data.get("force")
        with LOCK:
            QUEUE.append({"slug": "example"})
        return jsonify(queued=[{"slug": "example"}])
    monkeypatch.setitem(api_run.__globals__, "QUEUE", local.studio.QUEUE)
    monkeypatch.setitem(api_run.__globals__, "LOCK", local.studio.LOCK)
    response = local.client.post("/cr-3d/example", json={"action": "assemble"})
    assert response.status_code == 200
    assert local.studio.QUEUE == [{"slug": "example", "cr_project_id": new_id}]
    assert dispatched == [True]
    # The completed original marker remains until the normal dispatcher starts.
    assert json.loads((local.job / "cr-project.json").read_text())["id"] == local.parent
