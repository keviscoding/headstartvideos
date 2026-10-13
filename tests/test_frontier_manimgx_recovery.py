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
    monkeypatch.setattr(recovery.refs, "reference_text", lambda *a, **kw: "exact original private reference")
    monkeypatch.setattr(recovery.refs, "save_reference", lambda path, bundle: write(path, "exact original private reference", text=True))
    monkeypatch.setattr(recovery.cr_claude, "status", lambda: {"ready": True})
    monkeypatch.setattr(recovery.cr_claude, "models_verified", lambda *a, **kw: True)
    monkeypatch.setattr(renderer, "readiness", lambda *a, **kw: ready)
    checked = []
    monkeypatch.setattr(renderer, "_typecheck", lambda python, path: checked.append((python, path.read_text())))
    return SimpleNamespace(home=tmp_path, job=job, out=out, old=old, key=key, cloud=cloud, checked=checked,
                           asset=asset, destination=job / "threed/.cr-manimgx" / key, bundle=bundle)


def invoke(saved, **kwargs):
    return recovery.recover(saved.home, PROJECT, saved.out, "original", saved.cloud, **kwargs)


def update_completed_response(saved, result):
    """Preserve the fixture's paired, completed native/cloud response identity."""
    raw = json.dumps(result)
    (saved.old / "compiler-response-1.txt").write_text(raw)
    stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": raw})
    fingerprint = hashlib.sha256(stdout.encode()).hexdigest()
    write(saved.home / "output/cr-claude-cache" / PROJECT / (STAGE + ".json"),
          {"state": "complete", "input_fingerprint": STAGE[3:], "result_fingerprint": fingerprint, "stdout": stdout})
    saved.cloud["stages"][0]["result_fingerprint"] = fingerprint


@pytest.fixture
def coverage_saved(saved):
    saved.old.rename(saved.destination)
    saved.old = saved.destination
    write(saved.old / "compile-attempts.json", {"key": saved.key, "state": "stopped", "calls": 1})
    result = json.loads((saved.old / "compiler-response-1.txt").read_text())
    result["coverage"]["/scene/dur"] = "1."
    update_completed_response(saved, result)
    return saved


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


@pytest.mark.parametrize("known_failure", [True, False])
def test_procedural_pixel_validation_preserves_completed_native_source(coverage_saved, known_failure):
    saved = coverage_saved
    result = json.loads((saved.old / "compiler-response-1.txt").read_text())
    result["coverage"] = {p: "Original input requirement preserved" for p in result["coverage"]}
    result["source"] = SOURCE.replace("import manimgx as m", "import manimgx as m\nimport numpy as np").replace(
        "self.wait(1)", "pixels = np.zeros((16,16,4), dtype=np.uint8)\n        pixels[...,3] = 255\n        self.add(m.ImageMobject(pixels))\n        self.wait(1)")
    update_completed_response(saved, result)
    report = json.loads((saved.out / "report.json").read_text())
    report["scenes"][0]["error"] = "ManimGX source must use the original scene's verified local media paths." if known_failure else "Other failure"
    write(saved.out / "report.json", report)
    history = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    if not known_failure:
        with pytest.raises(RuntimeError, match="former coverage"):
            invoke(saved, acknowledge_recovery=True)
        assert not saved.checked
        return
    assert recovery.inspect(saved.home, PROJECT, saved.out, "original", saved.cloud)["kind"] == "procedural_pixels"
    accepted = invoke(saved, acknowledge_recovery=True)
    assert accepted["accepted"] and not accepted["reused"] and len(saved.checked) == 1
    assert (saved.destination / "scene.py").read_text() == result["source"]
    assert all((saved.old / name).read_bytes() == content for name, content in history.items())
    plan = recovery._prepare(saved.home, PROJECT, saved.out, "original", saved.cloud)
    assert renderer._accepted_response(saved.destination, renderer._read(saved.destination / "compiler.json"),
        plan["context"], saved.bundle["assets"])
    assert invoke(saved, acknowledge_recovery=True)["reused"] and len(saved.checked) == 1


@pytest.mark.parametrize("known_failure", [True, False])
def test_blank_extra_annotation_recovery_uses_exact_completed_source(coverage_saved, known_failure):
    saved = coverage_saved
    result = json.loads((saved.old / "compiler-response-1.txt").read_text())
    result["coverage"] = {p: "Original input requirement preserved" for p in result["coverage"]}
    result["coverage"]["/scene/objects/4/anim/1/1"] = ""
    update_completed_response(saved, result)
    report = json.loads((saved.out / "report.json").read_text())
    report["scenes"][0]["error"] = "Claude's ManimGX manifest omitted or changed render requirements." if known_failure else "Other failure"
    write(saved.out / "report.json", report)
    history = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    if not known_failure:
        with pytest.raises(RuntimeError, match="former coverage"):
            invoke(saved, acknowledge_recovery=True)
        assert not saved.checked
        return
    assert recovery.inspect(saved.home, PROJECT, saved.out, "original", saved.cloud)["starts_model"] is False
    accepted = invoke(saved, acknowledge_recovery=True)
    assert accepted["accepted"] and len(saved.checked) == 1
    assert (saved.destination / "scene.py").read_text() == result["source"]
    assert all((saved.old / name).read_bytes() == content for name, content in history.items())
    plan = recovery._prepare(saved.home, PROJECT, saved.out, "original", saved.cloud)
    assert renderer._accepted_response(saved.destination, renderer._read(saved.destination / "compiler.json"),
        plan["context"], saved.bundle["assets"])
    assert invoke(saved, acknowledge_recovery=True)["reused"] and len(saved.checked) == 1


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


def test_coverage_length_recovery_accepts_same_key_without_changing_old_history(coverage_saved):
    saved = coverage_saved
    original = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    rows = recovery.discover(saved.home, saved.job, saved.cloud)
    assert len(rows) == 1 and rows[0]["kind"] == "coverage_text"
    assert rows[0]["can_validate"] and not rows[0]["can_correct"] and not saved.checked
    result = invoke(saved, acknowledge_recovery=True)
    assert result["accepted"] and not result["reused"] and len(saved.checked) == 1
    for name, data in original.items():
        assert (saved.old / name).read_bytes() == data
    work = saved.old / ".recovery-coverage-text"
    receipt = json.loads((work / "recovery.json").read_text())
    manifest = json.loads((saved.old / "compiler.json").read_text())
    assert receipt["state"] == "validated" and receipt["model_calls"] == 0
    assert manifest["key"] == saved.key and manifest["typed"] is True
    assert manifest["recovery_of"] == saved.key and manifest["recovery_kind"] == "coverage_text"
    assert manifest["recovery_receipt"] == ".recovery-coverage-text/recovery.json"
    assert invoke(saved, acknowledge_recovery=True)["reused"] and len(saved.checked) == 1
    row = recovery.discover(saved.home, saved.job, saved.cloud)[0]
    assert row["state"] == "validated" and not row["can_validate"] and not row["can_correct"]


@pytest.mark.parametrize("mutation", ["missing", "empty", "non_string", "oversized", "long_only", "unsafe", "unsupported"])
def test_coverage_recovery_rejects_real_manifest_failures_without_typecheck(coverage_saved, mutation):
    saved = coverage_saved
    result = json.loads((saved.old / "compiler-response-1.txt").read_text())
    if mutation == "missing": del result["coverage"]["/scene/dur"]
    elif mutation == "empty": result["coverage"]["/scene/dur"] = " "
    elif mutation == "non_string": result["coverage"]["/scene/dur"] = 1
    elif mutation == "oversized": result["coverage"]["/scene/dur"] = "a" * 2001
    elif mutation == "long_only": result["coverage"]["/scene/dur"] = "Original input requirement preserved"
    elif mutation == "unsafe": result["source"] = "import os\n" + SOURCE
    elif mutation == "unsupported": result["unsupported"] = ["Original camera cannot be preserved"]
    update_completed_response(saved, result)
    history = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    with pytest.raises(RuntimeError): invoke(saved, acknowledge_recovery=True)
    assert not saved.checked and not (saved.old / ".recovery-coverage-text").exists()
    assert {p.name: p.read_bytes() for p in saved.old.iterdir()} == history
    assert not recovery.discover(saved.home, saved.job, saved.cloud)


@pytest.mark.parametrize("description", ["2.", "done", "x 0.", "31*S.", "0.6*S."])
def test_coverage_descriptions_are_bounded_metadata_not_scalar_proofs(coverage_saved, description):
    saved = coverage_saved
    result = json.loads((saved.old / "compiler-response-1.txt").read_text())
    result["coverage"]["/scene/dur"] = description
    update_completed_response(saved, result)
    accepted = invoke(saved, acknowledge_recovery=True)
    assert accepted["accepted"] and len(saved.checked) == 1


@pytest.mark.parametrize("api_failure", [True, False])
def test_coverage_typecheck_failure_preserves_history_and_offers_only_explicit_api_correction(coverage_saved, monkeypatch, api_failure):
    saved = coverage_saved
    history = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    def checker(python, source):
        saved.checked.append(source)
        source.with_suffix(".validation.log").write_text("Known API type error")
        if api_failure:
            raise renderer.ManimGXSourceAPIError("Known API type error")
        raise RuntimeError("Checker failed to start")
    monkeypatch.setattr(renderer, "_typecheck", checker)
    with pytest.raises(RuntimeError, match="no model"):
        invoke(saved, acknowledge_recovery=True)
    for name, data in history.items(): assert (saved.old / name).read_bytes() == data
    assert not (saved.old / "compiler.json").exists()
    receipt = json.loads((saved.old / ".recovery-coverage-text/recovery.json").read_text())
    state = "source_invalid" if api_failure else "stopped"
    assert receipt["state"] == state and receipt["model_calls"] == 0
    row = recovery.discover(saved.home, saved.job, saved.cloud)[0]
    assert row["state"] == state and row["can_correct"] is api_failure and not row["can_validate"]
    with pytest.raises(RuntimeError, match="not execute again"):
        invoke(saved, acknowledge_recovery=True)
    with pytest.raises(RuntimeError, match="verified native|no longer matches"):
        recovery.correct(saved.home, PROJECT, saved.out, "original", saved.cloud, acknowledge_correction=True)
    assert len(saved.checked) == 1


def test_coverage_unknown_accepted_cache_cannot_be_overwritten(coverage_saved):
    saved = coverage_saved
    write(saved.old / "compiler.json", {"key": saved.key, "typed": True, "recovery_kind": "coverage_text"})
    with pytest.raises(RuntimeError, match="other acceptance"):
        invoke(saved, acknowledge_recovery=True)
    assert not saved.checked and not (saved.old / ".recovery-coverage-text").exists()


def test_coverage_accepted_cache_changes_are_not_reported_as_validated(coverage_saved):
    saved = coverage_saved
    invoke(saved, acknowledge_recovery=True)
    (saved.old / "scene.py").write_text(SOURCE + "# Changed source\n")
    with pytest.raises(RuntimeError, match="source changed"):
        invoke(saved, acknowledge_recovery=True)
    assert not recovery.discover(saved.home, saved.job, saved.cloud)
    assert len(saved.checked) == 1


@pytest.mark.parametrize("contents", ["{}", "{malformed", "[]"])
def test_coverage_existing_empty_or_malformed_manifest_is_never_overwritten(coverage_saved, contents):
    saved = coverage_saved
    manifest = saved.old / "compiler.json"
    manifest.write_text(contents)
    before = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    with pytest.raises(RuntimeError): invoke(saved, acknowledge_recovery=True)
    assert {p.name: p.read_bytes() for p in saved.old.iterdir()} == before
    assert not saved.checked and not (saved.old / ".recovery-coverage-text").exists()


@pytest.mark.parametrize("kind", ["edited", "symlink", "directory"])
def test_coverage_existing_unknown_scene_is_never_overwritten(coverage_saved, kind):
    saved = coverage_saved
    scene = saved.old / "scene.py"
    target = saved.home / "unrelated.py"
    target.write_text(SOURCE)
    if kind == "edited": scene.write_text(SOURCE + "# User edit\n")
    elif kind == "symlink": scene.symlink_to(target)
    else: scene.mkdir()
    before = scene.lstat()
    with pytest.raises(RuntimeError, match="will not be overwritten"):
        invoke(saved, acknowledge_recovery=True)
    after = scene.lstat()
    assert (before.st_ino, before.st_mode, before.st_mtime_ns) == (after.st_ino, after.st_mode, after.st_mtime_ns)
    assert target.read_text() == SOURCE
    if kind == "edited": assert scene.read_text() == SOURCE + "# User edit\n"
    assert not saved.checked and not (saved.old / ".recovery-coverage-text").exists()


def test_coverage_identical_regular_scene_preserves_inode_and_bytes(coverage_saved):
    saved = coverage_saved
    scene = saved.old / "scene.py"
    scene.write_text(SOURCE)
    before = scene.stat()
    result = invoke(saved, acknowledge_recovery=True)
    after = scene.stat()
    assert result["accepted"] and len(saved.checked) == 1
    assert scene.read_bytes() == SOURCE.encode()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


@pytest.mark.parametrize("path_name", ["compiler.json", "scene.py"])
def test_coverage_publication_rechecks_work_that_appeared_during_typecheck(coverage_saved, monkeypatch, path_name):
    saved = coverage_saved
    contents = "{}" if path_name == "compiler.json" else SOURCE + "# Concurrent user edit\n"
    def checker(python, candidate):
        saved.checked.append(candidate)
        (saved.old / path_name).write_text(contents)
    monkeypatch.setattr(renderer, "_typecheck", checker)
    with pytest.raises(RuntimeError, match="will not be overwritten"):
        invoke(saved, acknowledge_recovery=True)
    assert (saved.old / path_name).read_text() == contents
    receipt = json.loads((saved.old / ".recovery-coverage-text/recovery.json").read_text())
    assert receipt["state"] == "stopped" and receipt["model_calls"] == 0
    assert json.loads((saved.old / "compile-attempts.json").read_text()) == {"key": saved.key, "state": "stopped", "calls": 1}
    assert len(saved.checked) == 1


@pytest.mark.parametrize("path_name", ["compiler.json", "scene.py"])
def test_coverage_exclusive_publication_cannot_replace_a_racing_file(coverage_saved, monkeypatch, path_name):
    saved = coverage_saved
    original_link = recovery.os.link
    contents = "{}" if path_name == "compiler.json" else SOURCE + "# Racing user edit\n"
    def race_link(temporary, destination):
        if destination.name == path_name:
            destination.write_text(contents)
        return original_link(temporary, destination)
    monkeypatch.setattr(recovery.os, "link", race_link)
    with pytest.raises(RuntimeError, match="will not be overwritten"):
        invoke(saved, acknowledge_recovery=True)
    assert (saved.old / path_name).read_text() == contents
    receipt = json.loads((saved.old / ".recovery-coverage-text/recovery.json").read_text())
    assert receipt["state"] == "stopped" and receipt["model_calls"] == 0
    assert json.loads((saved.old / "compile-attempts.json").read_text()) == {"key": saved.key, "state": "stopped", "calls": 1}
    assert len(saved.checked) == 1


def test_recovery_never_writes_redirected_cache(coverage_saved, tmp_path):
    saved = coverage_saved
    outside = tmp_path / "redirected"
    saved.old.rename(outside)
    saved.old.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="redirected paths"):
        invoke(saved, acknowledge_recovery=True)
    assert not saved.checked and not (outside / ".recovery-coverage-text").exists()


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


@pytest.fixture
def coverage_correction(coverage_saved, monkeypatch):
    import cr_bridge
    saved = coverage_saved
    work = saved.old / ".recovery-coverage-text"
    corrected_source = SOURCE.replace("self.wait(1)", "self.wait(1.0)")
    calls = []
    def checker(python, source):
        saved.checked.append(source)
        if source.name == "scene-recovered.py":
            source.with_suffix(".validation.log").write_text("Known API type error")
            raise renderer.ManimGXSourceAPIError("Known API type error")
    monkeypatch.setattr(renderer, "_typecheck", checker)
    with pytest.raises(RuntimeError, match="no model"):
        invoke(saved, acknowledge_recovery=True)
    monkeypatch.setattr(cr_bridge, "HOME", saved.home)
    previous = {"id": "prior-native-project"}
    monkeypatch.setattr(cr_bridge, "_project", previous)
    monkeypatch.setattr(cr_bridge, "begin_project", lambda *a, **kw: calls.append("begin"))
    monkeypatch.setattr(cr_bridge, "finish_project", lambda *a, **kw: calls.append("finish"))
    def compiler(engine, context, assets, ready, directory, key, *, reference_bundle, recovery_directory):
        assert directory == saved.old and recovery_directory == work
        calls.append("correct")
        receipt = json.loads((work / "recovery.json").read_text())
        result = json.loads((work / "recovery-result.json").read_text())
        result["source"] = corrected_source
        (work / "compiler-correction-response-1.txt").write_text(json.dumps(result))
        (work / "scene-corrected.py").write_text(corrected_source)
        write(work / "correction-attempts.json", {"key": key, "state": "validated", "calls": 1,
              "recovery_proof": receipt["proof"]})
        write(work / "compiler-corrected.json", {"key": key, "typed": True, "result": result,
              "source_sha256": hashlib.sha256(corrected_source.encode()).hexdigest(), "recovery_proof": receipt["proof"]})
        return result, work / "scene-corrected.py"
    monkeypatch.setattr(renderer, "correct_recovered_source", compiler)
    saved.work, saved.calls, saved.corrected_source, saved.previous = work, calls, corrected_source, previous
    saved.engine = SimpleNamespace(CLAUDE_PROVIDER="claudecode")
    saved.setup = {"brain_mode": "claudecode", "models_verified": True, "costs_accepted": True}
    return saved


def correct_coverage(saved, **kwargs):
    return recovery.correct(saved.home, PROJECT, saved.out, "original", saved.cloud,
                            engine=saved.engine, setup_proof=saved.setup, **kwargs)


def test_coverage_api_correction_is_explicit_single_native_call_and_preserves_origin(coverage_correction):
    import cr_bridge
    saved = coverage_correction
    history = {p.name: p.read_bytes() for p in saved.old.iterdir() if p.is_file()}
    with pytest.raises(RuntimeError, match="Confirm"):
        correct_coverage(saved)
    assert not saved.calls
    result = correct_coverage(saved, acknowledge_correction=True)
    assert result["accepted"] and result["corrected"]
    assert saved.calls == ["begin", "correct", "finish"] and cr_bridge._project is saved.previous
    for name, content in history.items(): assert (saved.old / name).read_bytes() == content
    assert (saved.old / "scene.py").read_text() == saved.corrected_source
    manifest = json.loads((saved.old / "compiler.json").read_text())
    receipt = json.loads((saved.work / "recovery.json").read_text())
    assert manifest["recovery_receipt"] == ".recovery-coverage-text/recovery.json"
    assert manifest["recovery_proof"] == receipt["proof"] and receipt["state"] == "corrected"
    assert receipt["model_calls"] == 1 and receipt["diagnostic"] == "Known API type error"
    assert manifest["source_sha256"] != receipt["proof"]["source_sha256"]
    plan = recovery._prepare(saved.home, PROJECT, saved.out, "original", saved.cloud)
    assert renderer._accepted_response(saved.old, manifest, plan["context"], plan["assets"])["kind"] == "completed_native_recovery_correction"
    assert recovery.discover(saved.home, saved.job, saved.cloud)[0]["state"] == "corrected"
    assert not recovery.discover(saved.home, saved.job, saved.cloud)[0]["can_correct"]
    assert invoke(saved, acknowledge_recovery=True)["reused"]
    with pytest.raises(RuntimeError, match="no longer matches"):
        correct_coverage(saved, acknowledge_correction=True)
    assert saved.calls == ["begin", "correct", "finish"]


@pytest.mark.parametrize("existing", ["compiler.json", "scene.py"])
def test_coverage_correction_refuses_existing_root_work_before_native_call(coverage_correction, existing):
    saved = coverage_correction
    content = "{}" if existing == "compiler.json" else SOURCE
    (saved.old / existing).write_text(content)
    with pytest.raises(RuntimeError): correct_coverage(saved, acknowledge_correction=True)
    assert not saved.calls and (saved.old / existing).read_text() == content
    assert not (saved.work / "correction-attempts.json").exists()


@pytest.mark.parametrize("mutation", ["source", "raw", "proof", "child_manifest", "asset", "manifest_race", "source_race", "type_error"])
def test_coverage_correction_rechecks_provenance_and_never_overwrites_new_work(coverage_correction, monkeypatch, mutation):
    import cr_bridge
    saved = coverage_correction
    history = {p.name: p.read_bytes() for p in saved.old.iterdir() if p.is_file()}
    compiler = renderer.correct_recovered_source
    def changed(*a, **kw):
        result, source = compiler(*a, **kw)
        if mutation == "source": source.write_text(saved.corrected_source + "# changed\n")
        elif mutation == "raw": (saved.work / "compiler-correction-response-1.txt").write_text("{}")
        elif mutation == "proof":
            journal = json.loads((saved.work / "correction-attempts.json").read_text())
            journal["recovery_proof"]["source_sha256"] = "e" * 64
            write(saved.work / "correction-attempts.json", journal)
        elif mutation == "asset": saved.asset.write_bytes(b"changed image")
        elif mutation == "child_manifest": (saved.work / "compiler-corrected.json").write_text("{}")
        elif mutation == "manifest_race": (saved.old / "compiler.json").write_text("{}")
        elif mutation == "source_race": (saved.old / "scene.py").write_text("# User work\n")
        elif mutation == "type_error":
            monkeypatch.setattr(renderer, "_typecheck", lambda *a: (_ for _ in ()).throw(renderer.ManimGXSourceAPIError("Correction still invalid")))
        return result, source
    monkeypatch.setattr(renderer, "correct_recovered_source", changed)
    with pytest.raises(RuntimeError): correct_coverage(saved, acknowledge_correction=True)
    for name, content in history.items(): assert (saved.old / name).read_bytes() == content
    assert cr_bridge._project is saved.previous
    assert json.loads((saved.work / "recovery.json").read_text())["state"] == "stopped"
    if mutation == "manifest_race": assert (saved.old / "compiler.json").read_text() == "{}"
    else: assert not (saved.old / "compiler.json").exists()
    if mutation == "source_race": assert (saved.old / "scene.py").read_text() == "# User work\n"
    else: assert not (saved.old / "scene.py").exists()
    with pytest.raises(RuntimeError): correct_coverage(saved, acknowledge_correction=True)
    assert saved.calls.count("correct") == 1


@pytest.mark.parametrize("target", ["compiler.json", "scene.py"])
def test_coverage_correction_exclusive_publication_rejects_racing_work(coverage_correction, monkeypatch, target):
    saved = coverage_correction
    original_link = recovery.os.link
    content = "{}" if target == "compiler.json" else "# Racing user work\n"
    def racing_link(source, destination):
        if destination.name == target: destination.write_text(content)
        return original_link(source, destination)
    monkeypatch.setattr(recovery.os, "link", racing_link)
    with pytest.raises(RuntimeError, match="will not be overwritten"):
        correct_coverage(saved, acknowledge_correction=True)
    assert (saved.old / target).read_text() == content
    assert json.loads((saved.work / "recovery.json").read_text())["state"] == "stopped"
    assert saved.calls.count("correct") == 1

@pytest.fixture
def runtime_saved(saved):
    saved.old.rename(saved.destination)
    saved.old = saved.destination
    write(saved.old / "compile-attempts.json", {"key": saved.key, "state": "validated", "calls": 1})
    result = json.loads((saved.old / "compiler-response-1.txt").read_text())
    result["source"] = SOURCE.replace("self.wait(1)", "m.Sphere().set_shade_in_3d(False)")
    update_completed_response(saved, result)
    (saved.old / "scene.py").write_text(result["source"])
    context = {"scene": {"id": "original", "dur": 1}, "cast": [], "video": {},
               "output": {"width": 640, "height": 360, "fps": 30, "quality": "draft"}}
    write(saved.old / "compiler.json", {"key": saved.key, "typed": True, "context": context,
          "result": result, "source_sha256": hashlib.sha256(result["source"].encode()).hexdigest()})
    (saved.old / "render_stdout.log").write_text(
        f'Traceback (most recent call last):\n  File "{saved.old / "scene.py"}", line 4, in construct\n'
        "    m.Sphere().set_shade_in_3d(False)\n"
        "AttributeError: 'Sphere' object has no attribute 'set_shade_in_3d'. Did you mean: 'shade_in_3d'?\n")
    saved.work = saved.old / ".recovery-runtime-api"
    return saved


def test_runtime_validation_binds_exact_failure_without_model_or_rerender(runtime_saved):
    saved = runtime_saved
    original = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    rows = recovery.discover(saved.home, saved.job, saved.cloud)
    assert len(rows) == 1 and rows[0]["kind"] == "runtime_api" and rows[0]["can_validate"]
    assert not (saved.old / "runtime-failure.json").exists() and not saved.checked
    with pytest.raises(RuntimeError, match="acknowledge"):
        invoke(saved)
    result = invoke(saved, acknowledge_recovery=True)
    assert result["source_invalid"] and result["starts_model"] is False and not saved.checked
    for name, content in original.items(): assert (saved.old / name).read_bytes() == content
    receipt = json.loads((saved.work / "recovery.json").read_text())
    assert receipt["state"] == "source_invalid" and receipt["error_type"] == "ManimGXRuntimeAPIError"
    assert receipt["model_calls"] == 0
    assert recovery.discover(saved.home, saved.job, saved.cloud)[0]["can_correct"]
    with pytest.raises(RuntimeError, match="not execute again"):
        invoke(saved, acknowledge_recovery=True)


@pytest.mark.parametrize("mutation", ["source", "source_line", "wrong_path", "not_attribute", "truncated", "manifest", "receipt", "log_symlink"])
def test_runtime_recovery_refuses_unbound_failure(runtime_saved, mutation):
    saved = runtime_saved
    path = saved.old / "render_stdout.log"
    if mutation == "source": (saved.old / "scene.py").write_text(SOURCE)
    elif mutation == "source_line": path.write_text(path.read_text().replace("line 4", "line 3"))
    elif mutation == "wrong_path": path.write_text(path.read_text().replace(str(saved.old / "scene.py"), "/other/scene.py"))
    elif mutation == "not_attribute": path.write_text(path.read_text().replace("AttributeError", "RuntimeError"))
    elif mutation == "truncated": path.write_text(path.read_text().split("AttributeError")[0])
    elif mutation == "manifest": write(saved.old / "compiler.json", {"key": saved.key, "typed": True})
    elif mutation == "receipt": write(saved.old / "runtime-failure.json", {"diagnostic": "other failure"})
    elif mutation == "log_symlink":
        content = path.read_text(); path.unlink(); other = saved.home / "other.log"; other.write_text(content); path.symlink_to(other)
    with pytest.raises((RuntimeError, ValueError, KeyError)):
        invoke(saved, acknowledge_recovery=True)
    assert not saved.work.exists() and not saved.checked


@pytest.fixture
def runtime_correction(runtime_saved, monkeypatch):
    import cr_bridge
    saved = runtime_saved
    invoke(saved, acknowledge_recovery=True)
    saved.calls = []
    saved.engine = SimpleNamespace(CLAUDE_PROVIDER="claudecode")
    saved.setup = {"brain_mode": "claudecode", "models_verified": True, "costs_accepted": True}
    monkeypatch.setattr(cr_bridge, "HOME", saved.home)
    monkeypatch.setattr(cr_bridge, "begin_project", lambda *a, **kw: saved.calls.append("begin"))
    monkeypatch.setattr(cr_bridge, "finish_project", lambda *a, **kw: saved.calls.append("finish"))
    def compiler(engine, context, assets, ready, directory, key, *, reference_bundle, recovery_directory):
        assert recovery_directory == saved.work
        saved.calls.append("correct")
        receipt = json.loads((saved.work / "recovery.json").read_text())
        result = json.loads((saved.work / "recovery-result.json").read_text())
        result["source"] = SOURCE
        (saved.work / "compiler-correction-response-1.txt").write_text(json.dumps(result))
        (saved.work / "scene-corrected.py").write_text(SOURCE)
        write(saved.work / "correction-attempts.json", {"key": key, "state": "validated", "calls": 1,
              "recovery_proof": receipt["proof"]})
        write(saved.work / "compiler-corrected.json", {"key": key, "typed": True, "result": result,
              "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(), "recovery_proof": receipt["proof"]})
        return result, saved.work / "scene-corrected.py"
    monkeypatch.setattr(renderer, "correct_recovered_source", compiler)
    return saved


def test_runtime_correction_preserves_original_accepted_source_and_receipts(runtime_correction):
    saved = runtime_correction
    originals = {p.name: p.read_bytes() for p in saved.old.iterdir() if p.is_file()}
    with pytest.raises(RuntimeError, match="Confirm"):
        correct_coverage(saved)
    assert not saved.calls
    assert correct_coverage(saved, acknowledge_correction=True)["accepted"]
    assert saved.calls == ["begin", "correct", "finish"]
    for name, content in originals.items(): assert (saved.old / name).read_bytes() == content
    plan = recovery._prepare(saved.home, PROJECT, saved.out, "original", saved.cloud)
    args = (saved.old, plan["context"], plan["assets"], plan["ready"], saved.key, saved.bundle)
    result, source = recovery.runtime_override(*args)
    assert result["source"] == SOURCE and source.parent == saved.work
    acceptance = recovery.runtime_acceptance(*args)
    assert acceptance["kind"] == "completed_native_runtime_correction"
    assert acceptance["source_sha256"] != acceptance["parent_source_sha256"]
    assert invoke(saved, acknowledge_recovery=True)["reused"]
    assert not recovery.discover(saved.home, saved.job, saved.cloud)[0]["can_correct"]
    with pytest.raises(RuntimeError): correct_coverage(saved, acknowledge_correction=True)
    assert saved.calls.count("correct") == 1


@pytest.mark.parametrize("target", ["compiler-corrected.json", "compiler-correction-response-1.txt", "scene-corrected.py", "correction-attempts.json", "recovery.json"])
def test_runtime_override_rejects_changed_child(runtime_correction, target):
    saved = runtime_correction
    correct_coverage(saved, acknowledge_correction=True)
    plan = recovery._prepare(saved.home, PROJECT, saved.out, "original", saved.cloud)
    (saved.work / target).write_text("{}")
    with pytest.raises((RuntimeError, ValueError, KeyError)):
        recovery.runtime_override(saved.old, plan["context"], plan["assets"], plan["ready"], saved.key, saved.bundle)
    assert saved.calls.count("correct") == 1


def test_runtime_correction_with_second_saved_failure_never_rerenders(runtime_correction):
    saved = runtime_correction
    correct_coverage(saved, acknowledge_correction=True)
    plan = recovery._prepare(saved.home, PROJECT, saved.out, "original", saved.cloud)
    write(saved.work / "runtime-failure.json", {"error_type": "AttributeError"})
    with pytest.raises(RuntimeError, match="will not render again"):
        recovery.runtime_override(saved.old, plan["context"], plan["assets"], plan["ready"], saved.key, saved.bundle)
    assert saved.calls.count("correct") == 1


def update_completed_raw(saved, raw):
    (saved.old / "compiler-response-1.txt").write_text(raw)
    stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": raw})
    fingerprint = hashlib.sha256(stdout.encode()).hexdigest()
    write(saved.home / "output/cr-claude-cache" / PROJECT / (STAGE + ".json"),
          {"state": "complete", "input_fingerprint": STAGE[3:], "result_fingerprint": fingerprint, "stdout": stdout})
    saved.cloud["stages"][0]["result_fingerprint"] = fingerprint


@pytest.fixture
def manifest_saved(coverage_saved):
    saved = coverage_saved
    result = json.loads((saved.old / "compiler-response-1.txt").read_text())
    result["coverage"]["/scene/dur"] = "Exact original timing"
    saved.complete_result = result
    saved.original_raw = json.dumps(result)[:-30]
    update_completed_raw(saved, saved.original_raw)
    saved.work = saved.old / ".recovery-manifest-json"
    return saved


def test_incomplete_manifest_validation_saves_full_native_result_without_fake_source(manifest_saved):
    saved = manifest_saved
    history = {p.name: p.read_bytes() for p in saved.old.iterdir()}
    rows = recovery.discover(saved.home, saved.job, saved.cloud)
    assert len(rows) == 1 and rows[0]["kind"] == "manifest_json" and rows[0]["can_validate"]
    assert not saved.work.exists()
    result = invoke(saved, acknowledge_recovery=True)
    assert not result["accepted"] and result["source_invalid"] and not result["starts_model"]
    for name, data in history.items(): assert (saved.old / name).read_bytes() == data
    assert (saved.work / "saved-response.txt").read_text() == saved.original_raw
    assert not (saved.work / "scene-recovered.py").exists() and not saved.checked
    receipt = json.loads((saved.work / "recovery.json").read_text())
    assert receipt["error_type"] == "ManimGXManifestJSONError" and receipt["model_calls"] == 0
    assert receipt["proof"]["source_sha256"] == hashlib.sha256(saved.original_raw.encode()).hexdigest()
    assert recovery.discover(saved.home, saved.job, saved.cloud)[0]["can_correct"]
    with pytest.raises(RuntimeError, match="not execute again"):
        invoke(saved, acknowledge_recovery=True)


def test_unique_wrapped_manifest_recovers_without_native_correction(manifest_saved):
    saved = manifest_saved
    raw = "Here is the completed scene:\n```json\n" + json.dumps(saved.complete_result) + "\n```\nReady for local validation."
    update_completed_raw(saved, raw)
    result = invoke(saved, acknowledge_recovery=True)
    assert result["accepted"] and len(saved.checked) == 1
    assert (saved.old / "scene.py").read_text() == saved.complete_result["source"]
    manifest = json.loads((saved.old / "compiler.json").read_text())
    assert manifest["recovery_kind"] == "manifest_json"
    assert json.loads((saved.work / "recovery.json").read_text())["model_calls"] == 0
    assert invoke(saved, acknowledge_recovery=True)["reused"]
    assert (saved.old / "compiler-response-1.txt").read_text() == raw


@pytest.mark.parametrize("mutation", ["coverage", "unsupported", "unsafe", "oversized", "unverified", "existing_source"])
def test_manifest_recovery_does_not_weaken_other_acceptance_rules(manifest_saved, mutation):
    saved = manifest_saved
    result = saved.complete_result
    if mutation == "coverage": result["coverage"].pop("/scene/dur")
    elif mutation == "unsupported": result["unsupported"] = ["Cannot preserve original request"]
    elif mutation == "unsafe": result["source"] = "import os\n" + SOURCE
    if mutation in {"coverage", "unsupported", "unsafe"}:
        update_completed_raw(saved, "```json\n" + json.dumps(result) + "\n```")
    elif mutation == "oversized": update_completed_raw(saved, "x" * 2000001)
    elif mutation == "unverified": saved.cloud["stages"][0]["result_fingerprint"] = "d" * 64
    elif mutation == "existing_source": (saved.old / "scene.py").write_text(SOURCE)
    with pytest.raises(RuntimeError): invoke(saved, acknowledge_recovery=True)
    assert not saved.work.exists() and not saved.checked


@pytest.fixture
def manifest_correction(manifest_saved, monkeypatch):
    import cr_bridge
    saved = manifest_saved
    invoke(saved, acknowledge_recovery=True)
    saved.calls = []
    saved.engine = SimpleNamespace(CLAUDE_PROVIDER="claudecode")
    saved.setup = {"brain_mode": "claudecode", "models_verified": True, "costs_accepted": True}
    monkeypatch.setattr(cr_bridge, "HOME", saved.home)
    monkeypatch.setattr(cr_bridge, "begin_project", lambda *a, **kw: saved.calls.append("begin"))
    monkeypatch.setattr(cr_bridge, "finish_project", lambda *a, **kw: saved.calls.append("finish"))
    def compiler(engine, context, assets, ready, directory, key, *, reference_bundle, recovery_directory):
        assert recovery_directory == saved.work
        saved.calls.append("correct")
        receipt = json.loads((saved.work / "recovery.json").read_text())
        assert json.loads((saved.work / "recovery-result.json").read_text()) == {"saved_response": saved.original_raw}
        result = saved.complete_result
        (saved.work / "compiler-correction-response-1.txt").write_text(json.dumps(result))
        (saved.work / "scene-corrected.py").write_text(result["source"])
        write(saved.work / "correction-attempts.json", {"key": key, "state": "validated", "calls": 1,
              "recovery_proof": receipt["proof"]})
        write(saved.work / "compiler-corrected.json", {"key": key, "typed": True, "result": result,
              "source_sha256": hashlib.sha256(result["source"].encode()).hexdigest(), "recovery_proof": receipt["proof"]})
        return result, saved.work / "scene-corrected.py"
    monkeypatch.setattr(renderer, "correct_recovered_source", compiler)
    return saved


def test_manifest_correction_is_explicit_one_shot_and_preserves_saved_incomplete_response(manifest_correction):
    saved = manifest_correction
    history = {p.name: p.read_bytes() for p in saved.old.iterdir() if p.is_file()}
    with pytest.raises(RuntimeError, match="Confirm"):
        correct_coverage(saved)
    assert not saved.calls
    result = correct_coverage(saved, acknowledge_correction=True)
    assert result["accepted"] and saved.calls == ["begin", "correct", "finish"]
    for name, data in history.items(): assert (saved.old / name).read_bytes() == data
    assert (saved.old / "scene.py").read_text() == saved.complete_result["source"]
    receipt = json.loads((saved.work / "recovery.json").read_text())
    manifest = json.loads((saved.old / "compiler.json").read_text())
    assert receipt["state"] == "corrected" and receipt["model_calls"] == 1
    assert manifest["recovery_kind"] == "manifest_json" and manifest["recovery_receipt"] == ".recovery-manifest-json/recovery.json"
    assert invoke(saved, acknowledge_recovery=True)["reused"]
    with pytest.raises(RuntimeError): correct_coverage(saved, acknowledge_correction=True)
    assert saved.calls.count("correct") == 1


@pytest.mark.parametrize("target", ["saved-response.txt", "recovery-result.json", "scene-recovered.validation.log", "recovery-input.json"])
def test_manifest_correction_refuses_changed_saved_data_without_native_call(manifest_correction, target):
    saved = manifest_correction
    (saved.work / target).write_text("{}")
    with pytest.raises(RuntimeError): correct_coverage(saved, acknowledge_correction=True)
    assert not saved.calls and not (saved.old / "compiler.json").exists()


@pytest.fixture
def completed_runtime_correction(runtime_saved):
    saved = runtime_saved
    invoke(saved, acknowledge_recovery=True)
    receipt_path = saved.work / "recovery.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(state="stopped", correction_error_type="ManimGXSourceAPIError", model_calls=1)
    write(receipt_path, receipt)
    result = json.loads((saved.work / "recovery-result.json").read_text())
    result["source"] = SOURCE
    raw = json.dumps(result)
    (saved.work / "compiler-correction-response-1.txt").write_text(raw)
    (saved.work / "scene-corrected.py").write_text(SOURCE)
    (saved.work / "scene-corrected.validation.log").write_text("Old checker false positive")
    write(saved.work / "correction-attempts.json", {"key": saved.key, "state": "stopped", "calls": 1,
          "recovery_proof": receipt["proof"]})
    saved.correction_stage = "cc_" + "d" * 64
    stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": raw})
    fingerprint = hashlib.sha256(stdout.encode()).hexdigest()
    saved.correction_native = saved.home / "output/cr-claude-cache" / PROJECT / (saved.correction_stage + ".json")
    write(saved.correction_native, {"state": "complete", "input_fingerprint": saved.correction_stage[3:],
          "result_fingerprint": fingerprint, "stdout": stdout})
    saved.cloud["stages"].append({"stage_id": saved.correction_stage, "status": "complete", "result_fingerprint": fingerprint,
                                 "model_role": "visual", "selected_model": "claude-opus-5-5", "attempts": 1, "error_code": ""})
    return saved


def test_completed_native_correction_recovers_locally_without_rewriting_stopped_history(completed_runtime_correction):
    saved = completed_runtime_correction
    originals = {p: p.read_bytes() for p in saved.old.rglob("*") if p.is_file()}
    row = recovery.discover(saved.home, saved.job, saved.cloud)[0]
    assert row["can_validate"] and not row["can_correct"] and not saved.checked
    assert "no model call" in row["message"]
    accepted = invoke(saved, acknowledge_recovery=True)
    assert accepted["accepted"] and accepted["model_calls"] == 0 and not accepted["reused"]
    assert len(saved.checked) == 1
    for path, content in originals.items(): assert path.read_bytes() == content
    plan = recovery._prepare(saved.home, PROJECT, saved.out, "original", saved.cloud)
    args = (saved.old, plan["context"], plan["assets"], plan["ready"], saved.key, saved.bundle)
    result, source = recovery.runtime_override(*args)
    assert result["source"] == SOURCE and source.parent.name == ".completed-correction-validation"
    assert "completed_validation_sha256" in recovery.runtime_acceptance(*args)
    row = recovery.discover(saved.home, saved.job, saved.cloud)[0]
    assert row["state"] == "validated" and not row["can_validate"] and not row["can_correct"]
    assert invoke(saved, acknowledge_recovery=True)["reused"] and len(saved.checked) == 1
    assert json.loads((saved.work / "correction-attempts.json").read_text())["state"] == "stopped"


@pytest.mark.parametrize("mutation", ["raw", "source", "journal", "native", "model", "duplicate", "account", "accepted_manifest"])
def test_completed_correction_requires_exact_successful_native_receipt_before_local_validation(completed_runtime_correction, mutation):
    saved = completed_runtime_correction
    if mutation == "raw": (saved.work / "compiler-correction-response-1.txt").write_text("{}")
    elif mutation == "source": (saved.work / "scene-corrected.py").write_text(SOURCE + "# Changed\n")
    elif mutation == "journal": write(saved.work / "correction-attempts.json", {"state": "running", "calls": 1})
    elif mutation == "native": saved.cloud["stages"][-1]["result_fingerprint"] = "a" * 64
    elif mutation == "model": saved.cloud["stages"][-1]["selected_model"] = "different-model"
    elif mutation == "duplicate": saved.cloud["stages"].append(dict(saved.cloud["stages"][-1]))
    elif mutation == "account":
        native = json.loads(saved.correction_native.read_text()); native["account_fingerprint"] = "other-account"; write(saved.correction_native, native)
    elif mutation == "accepted_manifest": write(saved.work / "compiler-corrected.json", {"typed": True})
    with pytest.raises(RuntimeError): invoke(saved, acknowledge_recovery=True)
    assert not (saved.work / ".completed-correction-validation").exists() and not saved.checked


def test_completed_correction_local_failure_is_one_shot_and_keeps_original_source(completed_runtime_correction, monkeypatch):
    saved = completed_runtime_correction
    original = (saved.work / "scene-corrected.py").read_bytes()
    def fail(python, source):
        saved.checked.append(source)
        raise renderer.ManimGXSourceAPIError("Still invalid")
    monkeypatch.setattr(renderer, "_typecheck", fail)
    with pytest.raises(renderer.ManimGXSourceAPIError): invoke(saved, acknowledge_recovery=True)
    with pytest.raises(RuntimeError): invoke(saved, acknowledge_recovery=True)
    assert len(saved.checked) == 1 and (saved.work / "scene-corrected.py").read_bytes() == original
    row = recovery.discover(saved.home, saved.job, saved.cloud)[0]
    assert not row["can_validate"] and not row["can_correct"]


@pytest.mark.parametrize("target", ["history", "sidecar_source", "sidecar_manifest", "native", "sidecar_runtime_failure"])
def test_revalidated_correction_rechecks_original_and_sidecar_hashes_before_render(completed_runtime_correction, target):
    saved = completed_runtime_correction
    invoke(saved, acknowledge_recovery=True)
    plan = recovery._prepare(saved.home, PROJECT, saved.out, "original", saved.cloud)
    validation = saved.work / ".completed-correction-validation"
    if target == "history": (saved.work / "scene-corrected.validation.log").write_text("Changed old diagnostic")
    elif target == "sidecar_source": (validation / "scene.py").write_text("# Changed")
    elif target == "sidecar_manifest": write(validation / "compiler-corrected.json", {"typed": True})
    elif target == "native": write(saved.correction_native, {"state": "complete"})
    elif target == "sidecar_runtime_failure": write(validation / "runtime-failure.json", {"error_type": "AttributeError"})
    with pytest.raises(RuntimeError):
        recovery.runtime_override(saved.old, plan["context"], plan["assets"], plan["ready"], saved.key, saved.bundle)


@pytest.mark.parametrize("mutation", ["asset", "source", "account"])
def test_completed_correction_rechecks_inputs_after_local_validation(completed_runtime_correction, monkeypatch, mutation):
    saved = completed_runtime_correction
    def racing_checker(python, source):
        if mutation == "asset": saved.asset.write_bytes(b"Changed original image")
        elif mutation == "source": source.write_text(SOURCE + "# Changed during check")
        elif mutation == "account": monkeypatch.setattr(recovery.cr_claude, "status", lambda: {"ready": True, "account_fingerprint": "changed"})
    monkeypatch.setattr(renderer, "_typecheck", racing_checker)
    with pytest.raises(RuntimeError): invoke(saved, acknowledge_recovery=True)
    receipt = json.loads((saved.work / ".completed-correction-validation/recovery.json").read_text())
    assert receipt["state"] == "stopped" and receipt["model_calls"] == 0
    assert not (saved.work / ".completed-correction-validation/compiler-corrected.json").exists()
