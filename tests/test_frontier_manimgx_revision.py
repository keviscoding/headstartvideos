"""Revisions borrow proven implementation ideas, never old pixels or authority."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_frontier_manimgx import SOURCE, VIDEO, manifest, run_stub, stub_runtime
import cr_manimgx as gx
import cr_manimgx_refs as refs


@pytest.fixture
def revision(stub_runtime, monkeypatch):
    state = stub_runtime
    (state.root / "threed").mkdir()
    (state.root / "threed/catalog.json").write_text("{}")
    bundle = {"identity": "reference-one", "context": {}, "assets": {}}
    monkeypatch.setattr(refs, "resolve", lambda *a: copy.deepcopy(bundle))
    monkeypatch.setattr(refs, "save_reference", lambda p, b: gx._write(p, "Private fixture", text=True))
    first = run_stub(state)
    cache = state.root / "job/threed/.cr-manimgx"
    old = cache / first["scenes"][0]["compiler_key"]
    saved = gx._read(old / "compiler.json")
    identity = {"project_id": "crv_" + "1" * 24, "user_id": 1,
        "native_account_fingerprint": "a" * 64, "profile_identity": "b" * 64,
        "model_role": "visual", "selected_model": "claude-opus-5-5",
        "workflow_version": "frontier-5.3-claudecode.1", "engine_source_sha256": "c" * 64}
    raw = (old / "compiler-response-1.txt").read_text()
    stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": raw})
    digest = hashlib.sha256(stdout.encode()).hexdigest()
    stage_id = "cc_" + "d" * 64
    stage = {"stage_id": stage_id, "status": "complete", "attempts": 1,
        "model_role": "visual", "selected_model": identity["selected_model"], "result_fingerprint": digest}
    native_path = state.root / "output/cr-claude-cache" / identity["project_id"] / (stage_id + ".json")
    gx._write(native_path, {"state": "complete", "stdout": stdout,
        "input_fingerprint": stage_id[3:], "result_fingerprint": digest,
        "execution_binding": {k: v for k, v in identity.items() if k not in {"project_id", "user_id"}}})
    owner = {"home": str(state.root), "identity": identity, "stages": [stage]}
    native = gx._revision_native_result(raw, owner)
    assert native is not None
    saved["native_provenance"] = {"identity": identity, "native": native, "key": old.name,
        "context_sha256": gx._digest(saved["context"]), "assets_sha256": gx._digest({}),
        "reference_identity": bundle["identity"], "setup_identity": state.ready["identity"],
        "compiler": gx.COMPILER_VERSION}
    gx._write(old / "compiler.json", saved)
    target = copy.deepcopy(saved["context"])
    target["scene"]["camera"]["theta"] = -30
    target["scene"]["label"] = "New label"
    key = gx._cache_key(target, {}, state.ready, bundle)
    state.__dict__.update(old=old, cache=cache, owner=owner, saved=saved, target=target,
        target_dir=cache / key, key=key, native_path=native_path, bundle=bundle, raw=raw)
    return state


def start(state):
    return gx._revision_start(state.target, state.ready, state.target_dir, state.key, state.owner)


def test_revision_uses_verified_source_and_exact_diff_with_new_authority(revision):
    value = start(revision)
    assert value["lineage"]["source_file"] == str((revision.old / "scene.py").resolve())
    assert value["lineage"]["uses_previous_pixels"] is False
    assert value["lineage"]["visual_approval"] is False
    assert value["changes"]["/scene/camera/theta"] == {"before": -40, "after": -30, "change": "changed"}
    assert value["changes"]["/scene/label"]["change"] == "added"
    prompt = gx._revision_prompt(value)
    assert "new complete original input above is authoritative" in prompt
    assert "COMPLETE new scene_class/source/coverage/unsupported" in prompt


@pytest.mark.parametrize("change", ["legacy", "account", "profile", "role", "model", "workflow", "source", "stopped", "unrendered"])
def test_unproven_or_mismatched_prior_scene_is_not_a_seed(revision, change):
    saved = copy.deepcopy(revision.saved)
    if change == "legacy": saved.pop("native_provenance")
    elif change in {"account", "profile", "role", "model", "workflow", "source"}:
        key = {"account": "native_account_fingerprint", "profile": "profile_identity", "role": "model_role",
            "model": "selected_model", "workflow": "workflow_version", "source": "engine_source_sha256"}[change]
        saved["native_provenance"]["identity"][key] = "different"
    elif change == "stopped":
        gx._write(revision.old / "compile-attempts.json", {"state": "stopped", "calls": 1})
    else:
        report = revision.cache.parent / "review/report.json"
        value = gx._read(report); value["scenes"] = []; gx._write(report, value)
    gx._write(revision.old / "compiler.json", saved)
    assert start(revision) is None


@pytest.mark.parametrize("change", ["missing_binding", "other_account", "local_result", "cloud_result", "symlink"])
def test_native_execution_binding_is_mandatory_and_not_inferred(revision, change):
    value = gx._read(revision.native_path)
    if change == "missing_binding": value.pop("execution_binding")
    elif change == "other_account": value["execution_binding"]["native_account_fingerprint"] = "e" * 64
    elif change == "local_result": value["stdout"] += "changed"
    elif change == "cloud_result": revision.owner["stages"][0]["result_fingerprint"] = "f" * 64
    else:
        elsewhere = revision.root / "elsewhere.json"; elsewhere.write_text(json.dumps(value))
        revision.native_path.unlink(); revision.native_path.symlink_to(elsewhere)
    if change != "symlink": gx._write(revision.native_path, value)
    assert gx._revision_native_result(revision.raw, revision.owner) is None
    assert start(revision) is None


@pytest.mark.parametrize("change", ["source", "response", "symlink", "during_check"])
def test_tampered_prior_evidence_stops_before_new_model(revision, monkeypatch, change):
    if change == "source": gx._write(revision.old / "scene.py", SOURCE + "# edited\n", text=True)
    elif change == "response": gx._write(revision.old / "compiler-response-1.txt", "{}", text=True)
    elif change == "symlink":
        source = revision.old / "scene.py"
        elsewhere = revision.root / "elsewhere.py"; elsewhere.write_text(SOURCE)
        source.unlink(); source.symlink_to(elsewhere)
    else:
        monkeypatch.setattr(gx, "_typecheck", lambda py, source: gx._write(source, SOURCE + "# edited\n", text=True))
    with pytest.raises(RuntimeError): start(revision)
    assert not revision.target_dir.exists()


def test_new_source_still_compiles_and_checks_all_new_requirements(revision, monkeypatch):
    calls = []
    monkeypatch.setattr(gx, "_revision_owner", lambda *a: revision.owner)
    def compiler(prompt, maximum):
        calls.append(prompt)
        return json.dumps(manifest(revision.target, SOURCE + "# new revision\n"))
    result, path = gx._compiled_source(type("Engine", (), {})(), compiler, revision.target, {},
        revision.ready, revision.target_dir, revision.key, reference_bundle=revision.bundle)
    assert len(calls) == 1 and "REVISION STARTING POINT" in calls[0]
    assert path.parent == revision.target_dir and result["source"].endswith("# new revision\n")
    saved = gx._read(path.parent / "compiler.json")
    assert saved["context"] == revision.target
    assert saved["revision_start"]["target_context_sha256"] == gx._digest(revision.target)
    assert set(saved["result"]["coverage"]) == set(gx._pointers(revision.target))
    assert saved["native_provenance"] is None  # synthetic compiler cannot acquire actual native provenance


def test_native_revision_reads_frozen_source_and_keeps_visual_role(revision, monkeypatch):
    monkeypatch.setattr(gx, "_revision_owner", lambda *a: revision.owner)
    calls = []
    def native(engine, prompt, model, files, **kw):
        calls.append((prompt, model, files))
        assert model == "opus" and kw["what"] == "ManimGX compile"
        assert files[1] == revision.target_dir / "revision-start.py"
        assert files[1].read_text() == SOURCE
        assert files[1] != revision.old / "scene.py"
        assert files[1].stat().st_mode & 0o077 == 0
        return json.dumps(manifest(revision.target, SOURCE + "# revised native result\n"))
    monkeypatch.setitem(sys.modules, "threed", SimpleNamespace(_cc=native))
    engine = SimpleNamespace(_cr_original_cc_model="sonnet", CLAUDE_PROVIDER="claudecode")
    gx._compiled_source(engine, None, revision.target, {}, revision.ready, revision.target_dir,
        revision.key, reference_bundle=revision.bundle)
    assert len(calls) == 1


def test_old_coverage_cannot_approve_changed_scene_or_trigger_automatic_repeat(revision, monkeypatch):
    monkeypatch.setattr(gx, "_revision_owner", lambda *a: revision.owner)
    calls = []
    def compiler(*args):
        calls.append(args)
        return json.dumps(revision.saved["result"])
    with pytest.raises(RuntimeError, match="omitted or changed"):
        gx._compiled_source(SimpleNamespace(), compiler, revision.target, {}, revision.ready,
            revision.target_dir, revision.key, reference_bundle=revision.bundle)
    with pytest.raises(RuntimeError, match="already started or stopped"):
        gx._compiled_source(SimpleNamespace(), compiler, revision.target, {}, revision.ready,
            revision.target_dir, revision.key, reference_bundle=revision.bundle)
    assert len(calls) == 1 and not (revision.target_dir / "compiler.json").exists()

