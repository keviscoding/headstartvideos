"""A quota interruption resumes only its explicitly reviewed, exact native stage."""
import copy
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_frontier_manimgx_revision import revision
from test_frontier_manimgx import SOURCE, manifest, stub_runtime
import cr_manimgx as gx
import cr_claude


@pytest.fixture
def stopped(revision, monkeypatch):
    s = revision
    import cr_manimgx_refs
    monkeypatch.setattr(cr_manimgx_refs, "reference_text", lambda *a, **kw: "Private fixture")
    s.target_dir.mkdir()
    s.journal = s.target_dir / "compile-attempts.json"
    gx._write(s.journal, {"key": s.key, "state": "stopped", "calls": 1})
    s.original_journal = s.journal.read_bytes()
    gx._write(s.target_dir / "original-reference.txt", "Private fixture", text=True)
    s.sid = "cc_" + "e" * 64
    s.stage = {"stage_id": s.sid, "status": "retry_allowed", "attempts": 1,
        "error_code": "usage_limit", "model_role": "visual", "selected_model": "claude-opus-5-5"}
    s.owner["stages"] = [s.stage]
    s.failed_path = s.native_path.parent / (s.sid + ".json")
    gx._write(s.failed_path, {"state": "failed", "input_fingerprint": s.sid[3:], "error_code": "usage_limit"})
    s.original_failed = s.failed_path.read_bytes()
    gx._write(s.root / ".cr-claude-allowance.json", {"account": s.owner["identity"]["native_account_fingerprint"],
        "reset_at": time.time() - 60})
    monkeypatch.setattr(gx, "_revision_owner", lambda *a: copy.deepcopy(s.owner))
    s.calls = []
    def native(engine, prompt, model, files, **kw):
        s.calls.append((prompt, model, files))
        assert model == "opus" and kw["what"] == "ManimGX compile"
        assert cr_claude._expected_stage.get() == (s.owner["identity"]["project_id"], s.sid)
        raw = json.dumps(manifest(s.target))
        stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": raw})
        digest = hashlib.sha256(stdout.encode()).hexdigest()
        s.stage.update(status="complete", attempts=2, error_code="", result_fingerprint=digest)
        gx._write(s.failed_path, {"state": "complete", "input_fingerprint": s.sid[3:],
            "stdout": stdout, "result_fingerprint": digest,
            "execution_binding": {k: v for k, v in s.owner["identity"].items() if k not in {"project_id", "user_id"}}})
        if getattr(s, "tamper", None) == "journal": gx._write(s.journal, {"changed": True})
        if getattr(s, "tamper", None) == "receipt": s.stage["result_fingerprint"] = "f" * 64
        return raw
    monkeypatch.setitem(sys.modules, "threed", SimpleNamespace(_cc=native))
    s.engine = SimpleNamespace(CLAUDE_PROVIDER="claudecode", _cr_original_cc_model="sonnet")
    return s


def compile_scene(s):
    return gx._compiled_source(s.engine, None, s.target, {}, s.ready, s.target_dir, s.key,
        reference_bundle=s.bundle)


def test_reviewed_quota_attempt_preserves_history_and_reuses_completed_source(stopped):
    s = stopped
    result, source = compile_scene(s)
    assert len(s.calls) == 1 and source.read_text() == SOURCE
    assert s.journal.read_bytes() == s.original_journal
    work = s.target_dir / ".recovery-native-stage"
    assert (work / "failed-native-receipt.json").read_bytes() == s.original_failed
    saved = gx._read(s.target_dir / "compiler.json")
    assert gx._accepted_response(s.target_dir, saved, s.target, {})["kind"] == "reviewed_native_quota_recovery"
    assert compile_scene(s)[0] == result and len(s.calls) == 1
    assert cr_claude._expected_stage.get() is None


@pytest.mark.parametrize("change", ["not_reviewed", "already_used", "other_account", "not_reset", "other_model", "has_response", "other_failure"])
def test_ineligible_quota_resume_never_calls_native(stopped, change):
    s = stopped
    if change == "not_reviewed": s.stage["status"] = "failed"
    elif change == "already_used": s.stage["attempts"] = 2
    elif change == "other_model": s.stage["selected_model"] = "claude-sonnet-5-5"
    elif change == "has_response": gx._write(s.target_dir / "compiler-response-1.txt", "saved", text=True)
    elif change == "other_failure": s.stage["error_code"] = "network_interrupted"
    else:
        allowance = gx._read(s.root / ".cr-claude-allowance.json")
        if change == "other_account": allowance["account"] = "f" * 64
        else: allowance["reset_at"] = time.time() + 100
        gx._write(s.root / ".cr-claude-allowance.json", allowance)
    with pytest.raises(RuntimeError, match="already started or stopped"): compile_scene(s)
    assert not s.calls and s.journal.read_bytes() == s.original_journal


@pytest.mark.parametrize("change", ["journal", "receipt"])
def test_tampered_native_recovery_never_publishes_or_repeats(stopped, change):
    s = stopped; s.tamper = change
    with pytest.raises(RuntimeError, match="matching completed second-attempt"): compile_scene(s)
    assert len(s.calls) == 1 and not (s.target_dir / "compiler.json").exists()
    with pytest.raises(RuntimeError): compile_scene(s)
    assert len(s.calls) == 1


def test_completed_recovery_evidence_cannot_be_changed_before_reuse(stopped):
    s = stopped; compile_scene(s)
    gx._write(s.target_dir / ".recovery-native-stage/response.txt", "{}", text=True)
    with pytest.raises(RuntimeError, match="evidence changed"): compile_scene(s)
    assert len(s.calls) == 1


def test_reference_mismatch_stops_before_native(stopped, monkeypatch):
    import cr_manimgx_refs
    monkeypatch.setattr(cr_manimgx_refs, "reference_text", lambda *a, **kw: "changed")
    original = (stopped.target_dir / "original-reference.txt").read_bytes()
    with pytest.raises(RuntimeError, match="reference changed"): compile_scene(stopped)
    assert not stopped.calls
    assert (stopped.target_dir / "original-reference.txt").read_bytes() == original
