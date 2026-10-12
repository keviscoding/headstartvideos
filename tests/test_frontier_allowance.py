from datetime import datetime, timezone
from types import SimpleNamespace
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_allowance
import cr_claude


def test_timezone_reset_and_account_boundary(tmp_path):
    now = datetime(2026, 10, 12, 7, 30, tzinfo=timezone.utc).timestamp()
    result = cr_allowance.record(tmp_path, "account-a", "You've hit your session limit · resets 9:40am (Europe/London)", now=now)
    assert result["blocked"]
    assert result["reset_at"] == datetime(2026, 10, 12, 8, 40, tzinfo=timezone.utc).timestamp()
    assert not cr_allowance.status(tmp_path, "account-b", now=now)["blocked"]
    assert not cr_allowance.status(tmp_path, "account-a", now=result["reset_at"])["blocked"]
    assert (tmp_path / ".cr-claude-allowance.json").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("message", ["too many requests", "weekly limit resets tomorrow", "resets 9:40am", "resets 29:40pm (Europe/London)", "resets 9:40am (Unknown/City)"])
def test_ambiguous_reset_never_invents_cooldown(message):
    assert cr_allowance.reset_from_message(message) is None


def test_queued_call_stops_before_authorization_but_cache_can_sync(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {"ready": True, "account_fingerprint": "a"})
    cr_claude._private_write(tmp_path / ".cr-claude-allowance.json", {"account": "a", "reset_at": __import__('time').time()+3600, "reset_label": "the reported reset"})
    calls = []
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: {"brain_mode": "claudecode", "costs_accepted": True},
        current_project=lambda: {"id": "crv_"+"a"*24},
        authorize_stage=lambda s: calls.append(s) or {"reuse": True, "execute": False, "result_fingerprint": hashlib.sha256(b"saved").hexdigest()})
    runtime = cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude", lambda *a, **k: pytest.fail("No generation during cooldown"))
    args = ["/fake/claude", "--print"]
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (None, "test"))
    with pytest.raises(cr_claude.ClaudeStageStopped, match="usage limit"):
        runtime.run(args, input="prompt")
    assert calls == []
    digest = cr_claude._digest({"args": args[1:], "input": "prompt", "max_tokens": None, "read_files": []})
    cache = tmp_path / "output/cr-claude-cache" / ("crv_"+"a"*24) / ("cc_"+digest+".json")
    cr_claude._private_write(cache, {"state": "complete", "stdout": "saved", "result_fingerprint": hashlib.sha256(b"saved").hexdigest()})
    assert runtime.run(args, input="prompt").stdout == "saved"
    assert len(calls) == 1
