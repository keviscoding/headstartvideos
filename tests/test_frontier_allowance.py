from datetime import datetime, timezone
from types import SimpleNamespace
import json
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_allowance
import cr_claude


def test_timezone_reset_and_account_boundary(tmp_path):
    now = datetime(2026, 10, 12, 7, 30, tzinfo=timezone.utc).timestamp()
    result = cr_allowance.record(tmp_path, "account-a", "You've hit your session limit · resets 9:40am (Europe/London)", now=now)
    assert not result["blocked"] and result["reported_limit"]
    assert result["reset_at"] == datetime(2026, 10, 12, 8, 40, tzinfo=timezone.utc).timestamp()
    assert cr_allowance.status(tmp_path, "account-b", now=now) == {"blocked": False}
    assert cr_allowance.status(tmp_path, "account-a", now=result["reset_at"]) == {"blocked": False}
    if os.name != "nt":                         # POSIX permission bits; Windows keeps the file private by its ACL
        assert (tmp_path / ".cr-claude-allowance.json").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("message", ["too many requests", "weekly limit resets tomorrow", "resets 9:40am", "resets 29:40pm (Europe/London)", "resets 9:40am (Unknown/City)"])
def test_ambiguous_reset_never_invents_cooldown(message):
    assert cr_allowance.reset_from_message(message) is None


def runtime_fixture(tmp_path, monkeypatch, real_run):
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {"ready": True, "account_fingerprint": "a"})
    cr_claude._private_write(tmp_path / ".cr-claude-allowance.json", {"account": "a", "reset_at": time.time()+3600, "reset_label": "the reported reset"})
    calls, receipts = [], []
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: {"brain_mode": "claudecode", "costs_accepted": True},
        current_project=lambda: {"id": "crv_"+"a"*24},
        authorize_stage=lambda s: calls.append(s) or {"execute": True, "authorization_token": "test-token", "selected_model": cr_claude.GENERAL_MODEL},
        finish_stage=lambda receipt: receipts.append(dict(receipt)))
    runtime = cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude", real_run)
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (None, "test"))
    return runtime, bridge, calls, receipts


def test_fresh_native_call_uses_actual_cli_despite_saved_limit(tmp_path, monkeypatch):
    invoked = []
    def run(args, **kwargs):
        invoked.append(args)
        return subprocess.CompletedProcess(args, 0, "A new result", "")
    runtime, _, calls, receipts = runtime_fixture(tmp_path, monkeypatch, run)
    assert runtime.run(["/fake/claude", "--print"], input="prompt").stdout == "A new result"
    assert len(invoked) == len(calls) == len(receipts) == 1
    assert receipts[0]["status"] == "complete"


def test_actual_cli_limit_stops_and_preserves_failed_receipt(tmp_path, monkeypatch):
    invoked = []
    def run(args, **kwargs):
        invoked.append(args)
        return subprocess.CompletedProcess(args, 1, "You've hit your session limit · resets 9:40am (Europe/London)", "")
    runtime, _, calls, receipts = runtime_fixture(tmp_path, monkeypatch, run)
    with pytest.raises(cr_claude.ClaudeStageStopped, match="usage limit"):
        runtime.run(["/fake/claude", "--print"], input="prompt")
    assert len(invoked) == len(calls) == len(receipts) == 1
    assert receipts[0]["status"] == "failed" and receipts[0]["error_code"] == "usage_limit"
    cached = list((tmp_path / "output/cr-claude-cache").rglob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text())["state"] == "failed"
    assert cr_allowance.status(tmp_path, "a")["reported_limit"]


def test_stopped_stage_still_requires_cloud_recovery_approval(tmp_path, monkeypatch):
    runtime, bridge, calls, _ = runtime_fixture(tmp_path, monkeypatch,
        lambda *a, **k: pytest.fail("A stopped stage must not replay"))
    def reject(stage):
        calls.append(stage)
        raise cr_claude.WorkflowError("Review this stopped stage before resuming.", 409)
    bridge.authorize_stage = reject
    args = ["/fake/claude", "--print"]
    digest = cr_claude._digest({"args": args[1:], "input": "prompt", "max_tokens": None, "read_files": []})
    cache = tmp_path / "output/cr-claude-cache" / ("crv_"+"a"*24) / ("cc_"+digest+".json")
    cr_claude._private_write(cache, {"state": "failed", "error_code": "usage_limit", "input_fingerprint": digest})
    before = cache.read_bytes()
    with pytest.raises(cr_claude.ClaudeStageStopped, match="Review this stopped stage"):
        runtime.run(args, input="prompt")
    assert len(calls) == 1 and cache.read_bytes() == before


def test_cached_complete_stage_can_sync_despite_saved_limit(tmp_path, monkeypatch):
    runtime, bridge, calls, _ = runtime_fixture(tmp_path, monkeypatch,
        lambda *a, **k: pytest.fail("A completed stage must not regenerate"))
    bridge.authorize_stage = lambda s: calls.append(s) or {"reuse": True, "execute": False, "result_fingerprint": hashlib.sha256(b"saved").hexdigest()}
    args = ["/fake/claude", "--print"]
    digest = cr_claude._digest({"args": args[1:], "input": "prompt", "max_tokens": None, "read_files": []})
    cache = tmp_path / "output/cr-claude-cache" / ("crv_"+"a"*24) / ("cc_"+digest+".json")
    cr_claude._private_write(cache, {"state": "complete", "stdout": "saved", "result_fingerprint": hashlib.sha256(b"saved").hexdigest()})
    assert runtime.run(args, input="prompt").stdout == "saved"
    assert len(calls) == 1
