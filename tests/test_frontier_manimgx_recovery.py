"""Completed native output can be validated explicitly, never replayed blindly."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_manimgx as renderer
import cr_manimgx_recovery as recovery

PROJECT = "crv_" + "a" * 24
STAGE = "cc_" + "b" * 64
SOURCE = "import manimgx as m\nclass OriginalScene(m.Scene):\n    def construct(self):\n        self.wait(1)\n"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def saved(tmp_path, monkeypatch):
    job = tmp_path / "output/3d/_work/original"
    out = job / "threed/review/r1"
    video = {"cast": [], "scenes": [{"id": "original", "dur": 1}]}
    context = {"scene": video["scenes"][0], "cast": [], "video": {},
               "output": {"width": 640, "height": 360, "fps": 30, "quality": "draft"}}
    asset = tmp_path / "threed/engine/lib/nature/earth.jpg"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"original bundled image")
    assets = {"original:lib/nature/earth.jpg": {"path": str(asset), "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
              "bytes": asset.stat().st_size, "source": "private_original_renderer"}}
    bundle = {"identity": "ref" * 21 + "0", "assets": assets, "context": {"source_identity": "unchanged"}}
    ready = {"ready": True, "identity": "f" * 64, "python": "/pinned/python", "docs": "/pinned/docs"}
    spec = {"title": "Original", "style": "3d", "render_backend": "manimgx", "manimgx_identity": ready["identity"]}
    write(job / "cr-project.json", {"id": PROJECT, "brain_mode": "claudecode", "job_spec": spec})
    write(job / ".cr-finish.json", {"project_id": PROJECT, "status": "failed"})
    write(tmp_path / "cr-release.json", {"source_sha256": "c" * 64, "workflow_version": "workflow"})
    write(tmp_path / ".cr-connection.json", {"brain_mode": "claudecode", "costs_accepted": True,
                                           "general_model": "claude-sonnet-5-5", "visual_model": "claude-opus-5-5"})
    write(out / "video.json", video)
    write(out / "report.json", {"renderer": "manimgx", "w": 640, "h": 360, "fps": 30, "quality": "draft",
                              "scenes": [{"id": "original", "status": "error", "error": "string indices must be integers"}]})
    result = {"scene_class": "OriginalScene", "source": SOURCE, "unsupported": [],
              "coverage": {p: "Original input requirement preserved" for p in renderer._pointers(context)}}
    raw = json.dumps(result)
    native = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": raw})
    fingerprint = hashlib.sha256(native.encode()).hexdigest()
    write(tmp_path / "output/cr-claude-cache" / PROJECT / (STAGE + ".json"),
          {"state": "complete", "input_fingerprint": STAGE[3:], "result_fingerprint": fingerprint, "stdout": native})
    cloud = {"project_id": PROJECT, "project": {"id": PROJECT, "status": "failed", "title": "Original"},
             "brain_mode": "claudecode", "purpose": "video", "job_spec": spec,
             "workflow": {"engine_source_sha256": "c" * 64, "workflow_version": "workflow"},
             "stages": [{"stage_id": STAGE, "status": "complete", "result_fingerprint": fingerprint,
                        "model_role": "visual", "selected_model": "claude-opus-5-5", "attempts": 1, "error_code": ""}]}
    identity = {"input": context, "compiler": renderer.COMPILER_VERSION, "setup": ready["identity"], "original_references": bundle["identity"]}
    legacy = renderer._digest({**identity, "assets": {str(asset): assets[next(iter(assets))]["sha256"]}})
    key = renderer._digest({**identity, "assets": assets})
    old = job / "threed/.cr-manimgx" / legacy
    write(old / "compile-attempts.json", {"key": legacy, "state": "stopped", "calls": 1})
    (old / "compiler-response-1.txt").write_text(raw)
    (old / "original-reference.txt").write_text("exact original private reference")
    monkeypatch.setattr(recovery.refs, "resolve", lambda *a: bundle)
    monkeypatch.setattr(recovery.refs, "reference_text", lambda _: "exact original private reference")
    monkeypatch.setattr(recovery.cr_claude, "status", lambda: {"ready": True})
    monkeypatch.setattr(recovery.cr_claude, "models_verified", lambda *a, **kw: True)
    monkeypatch.setattr(renderer, "readiness", lambda *a, **kw: ready)
    checked = []
    monkeypatch.setattr(renderer, "_typecheck", lambda python, path: checked.append((python, path.read_text())))
    return SimpleNamespace(home=tmp_path, job=job, out=out, old=old, key=key, cloud=cloud, checked=checked,
                           asset=asset, destination=job / "threed/.cr-manimgx" / key, bundle=bundle)


def invoke(saved, **kwargs):
    return recovery.recover(saved.home, PROJECT, saved.out, "original", saved.cloud, **kwargs)


def test_explicit_acceptance_preserves_history_and_creates_exact_normal_cache(saved):
    original = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    assert recovery.inspect(saved.home, PROJECT, saved.out, "original", saved.cloud)["ready_for_local_typecheck"]
    assert not saved.destination.exists() and not saved.checked
    with pytest.raises(RuntimeError, match="acknowledge"):
        invoke(saved)
    result = invoke(saved, acknowledge_recovery=True)
    assert result["accepted"] and not result["reused"] and len(saved.checked) == 1
    manifest = json.loads((saved.destination / "compiler.json").read_text())
    assert manifest["key"] == saved.key and manifest["typed"] is True and manifest["result"]["source"] == SOURCE
    assert {p.name: p.read_bytes() for p in saved.old.iterdir()} == original
    assert invoke(saved, acknowledge_recovery=True)["reused"] and len(saved.checked) == 1
    assert json.loads((saved.destination / "compile-attempts.json").read_text())["calls"] == 0


@pytest.mark.parametrize("mutation", ["active", "receipt", "model", "image", "reference", "journal", "source", "workflow"])
def test_changed_or_unowned_proof_never_accepts_source(saved, mutation):
    if mutation == "active": saved.cloud["stages"][0]["status"] = "authorized"
    elif mutation == "receipt": saved.cloud["stages"][0]["result_fingerprint"] = "d" * 64
    elif mutation == "model": saved.cloud["stages"][0]["selected_model"] = "claude-sonnet-5-5"
    elif mutation == "image": saved.asset.write_bytes(b"changed image")
    elif mutation == "reference": (saved.old / "original-reference.txt").write_text("changed source")
    elif mutation == "journal": write(saved.old / "compile-attempts.json", {"key": saved.old.name, "state": "running", "calls": 1})
    elif mutation == "source": (saved.old / "compiler-response-1.txt").write_text("different answer")
    elif mutation == "workflow": saved.cloud["workflow"]["engine_source_sha256"] = "e" * 64
    with pytest.raises(RuntimeError):
        invoke(saved, acknowledge_recovery=True)
    assert not saved.destination.exists() and not saved.checked


def test_unfinished_local_worker_prevents_acceptance(saved):
    write(saved.home / "output/cr-video-workers/video_fixture/spec.json", {"project_id": PROJECT})
    write(saved.home / "output/cr-video-workers/video_fixture/progress.json", {"job": {"done": False}})
    with pytest.raises(RuntimeError, match="local worker"):
        invoke(saved, acknowledge_recovery=True)
    assert not saved.destination.exists()


def test_unpaired_local_running_record_prevents_acceptance(saved):
    write(saved.home / "output/cr-claude-cache" / PROJECT / ("cc_" + "d" * 64 + ".json"), {"state": "running"})
    with pytest.raises(RuntimeError, match="active or incomplete"):
        invoke(saved, acknowledge_recovery=True)
    assert not saved.destination.exists()


def test_saved_native_account_identity_must_match_current_verified_account(saved, monkeypatch):
    path = saved.home / "output/cr-claude-cache" / PROJECT / (STAGE + ".json")
    native = json.loads(path.read_text()); native["account_fingerprint"] = "old-account"
    write(path, native)
    monkeypatch.setattr(recovery.cr_claude, "status", lambda: {"ready": True, "account_fingerprint": "new-account"})
    with pytest.raises(RuntimeError, match="Claude account"):
        invoke(saved, acknowledge_recovery=True)
    assert not saved.destination.exists()


@pytest.mark.parametrize("api_failure", [True, False])
def test_local_checker_failure_is_durable_and_never_repeats_or_calls_model(saved, monkeypatch, api_failure):
    def checker(python, source):
        saved.checked.append(source)
        if api_failure:
            source.with_suffix(".validation.log").write_text("Known API type error")
            raise renderer.ManimGXSourceAPIError("Known API type error")
        raise RuntimeError("Checker cannot start")
    monkeypatch.setattr(renderer, "_typecheck", checker)
    with pytest.raises(RuntimeError, match="no model"):
        invoke(saved, acknowledge_recovery=True)
    receipt = json.loads((saved.destination / "recovery.json").read_text())
    assert receipt["state"] == ("source_invalid" if api_failure else "stopped")
    assert receipt["model_calls"] == 0 and not (saved.destination / "compiler.json").exists()
    assert (saved.destination / "recovery-input.json").is_file() and (saved.destination / "recovery-result.json").is_file()
    with pytest.raises(RuntimeError, match="not execute again"):
        invoke(saved, acknowledge_recovery=True)
    assert len(saved.checked) == 1


def test_discovery_is_read_only_opaque_and_selection_bound_to_current_inputs(saved):
    rows = recovery.discover(saved.home, saved.job, saved.cloud)
    assert len(rows) == 1 and rows[0]["can_validate"] and len(rows[0]["selection_id"]) == 64
    assert not saved.destination.exists() and not saved.checked
    assert recovery.resolve_selection(saved.home, saved.job, saved.cloud, rows[0]["selection_id"])["outdir"] == str(saved.out)
    saved.asset.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="changed or is unavailable"):
        recovery.resolve_selection(saved.home, saved.job, saved.cloud, rows[0]["selection_id"])


@pytest.mark.parametrize("metadata_failure", [False, True])
def test_correction_requires_explicit_fresh_native_setup_and_restores_context(saved, monkeypatch, metadata_failure):
    import cr_bridge
    def checker(python, source):
        source.with_suffix(".validation.log").write_text("Known API type error")
        raise renderer.ManimGXSourceAPIError("Known API type error")
    monkeypatch.setattr(renderer, "_typecheck", checker)
    with pytest.raises(RuntimeError): invoke(saved, acknowledge_recovery=True)
    previous = {"id": "prior-context"}
    monkeypatch.setattr(cr_bridge, "HOME", saved.home)
    monkeypatch.setattr(cr_bridge, "_project", previous)
    calls = []
    monkeypatch.setattr(cr_bridge, "begin_project", lambda *a, **kw: calls.append(("begin", a, kw)))
    monkeypatch.setattr(cr_bridge, "finish_project", lambda payload: calls.append(("finish", payload)))
    monkeypatch.setattr(renderer, "correct_recovered_source", lambda *a, **kw: (calls.append(("correct", a, kw)) or
                         json.loads((saved.destination / "recovery-result.json").read_text()), saved.destination / "scene.py"), raising=False)
    engine = SimpleNamespace(CLAUDE_PROVIDER="claudecode")
    setup = {"brain_mode": "claudecode", "models_verified": True, "costs_accepted": True}
    with pytest.raises(RuntimeError, match="Confirm"):
        recovery.correct(saved.home, PROJECT, saved.out, "original", saved.cloud, engine=engine, setup_proof=setup)
    assert not calls
    if metadata_failure:
        real_write = renderer._write
        def fail_pending(path, *args, **kwargs):
            if path.name == ".cr-finish-pending.json": raise OSError("Disk full while saving final metadata")
            return real_write(path, *args, **kwargs)
        monkeypatch.setattr(renderer, "_write", fail_pending)
        with pytest.raises(OSError, match="Disk full"):
            recovery.correct(saved.home, PROJECT, saved.out, "original", saved.cloud,
                             acknowledge_correction=True, engine=engine, setup_proof=setup)
        assert [c[0] for c in calls] == ["begin", "correct"]
    else:
        result = recovery.correct(saved.home, PROJECT, saved.out, "original", saved.cloud,
                                  acknowledge_correction=True, engine=engine, setup_proof=setup)
        assert result["corrected"] and [c[0] for c in calls] == ["begin", "correct", "finish"]
        assert calls[-1][1]["status"] == "failed"
    assert cr_bridge._project is previous
    receipt = json.loads((saved.destination / "recovery.json").read_text())
    assert receipt["state"] == "corrected" and receipt["corrected_source_sha256"] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert not recovery.discover(saved.home, saved.job, saved.cloud)[0]["can_correct"]
