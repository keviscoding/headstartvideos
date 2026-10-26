"""Studio's own Claude stages ask for a deliberate effort: an Opus stage left to the CLI thought at length on every
call (a 2-minute 3D video spent 84 minutes in Opus, 2026-10-25), and an effort inherited from a Claude Code chat
(CLAUDE_EFFORT=xhigh) made every step overrun."""
import json
from types import SimpleNamespace

import pytest

from local_runtime import cr_claude


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(cr_claude, "_effort_flag", {})
    monkeypatch.delenv("CR_CLAUDE_EFFORT", raising=False)


def runtime(tmp_path, monkeypatch, invoke, model=cr_claude.VISUAL_MODEL):
    cfg = {"brain_mode": "claudecode", "costs_accepted": True,
           "general_model": cr_claude.GENERAL_MODEL, "visual_model": cr_claude.VISUAL_MODEL}
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: {"ready": True, "account_fingerprint": "a" * 64})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (8000, "threed:review"))
    monkeypatch.setattr(cr_claude, "_real_run", invoke)        # the free --help check runs the CLI directly
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: cfg,
                             current_project=lambda: {"id": "crv_" + "1" * 24},
                             authorize_stage=lambda stage: {"execute": True, "authorization_token": "x" * 32,
                                                            "selected_model": model},
                             finish_stage=lambda receipt: None)
    return cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude", invoke)


def cli(help_text="  --effort <level>   Effort level for the current session (low, medium, high, xhigh, max)"):
    """A fake Claude Code: --help shows its options, a stage answers once; every call is kept."""
    calls = []

    def invoke(command, **kw):
        calls.append((list(command), kw.get("env") or {}))
        if command[1:] == ["--help"]:
            return SimpleNamespace(returncode=0, stdout=help_text, stderr="")
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"type": "result", "subtype": "success", "is_error": False, "result": "done", "usage": {"output_tokens": 3}}))
    return invoke, calls


def stages(calls):
    return [c for c in calls if c[0][1:] != ["--help"]]


OPUS = ["/fake/claude", "-p", "--model", "opus", "--tools", "Read", "--output-format", "json"]
SONNET = ["/fake/claude", "-p", "--model", "sonnet", "--output-format", "json"]


def test_an_opus_stage_asks_for_medium_effort(tmp_path, monkeypatch):
    invoke, calls = cli()
    runtime(tmp_path, monkeypatch, invoke).run(OPUS, input="Review these scenes", text=True)
    (command, env), = stages(calls)
    assert cr_claude._option(command, "--effort") == "medium"


def test_a_sonnet_stage_keeps_the_clis_own_effort(tmp_path, monkeypatch):
    invoke, calls = cli()
    runtime(tmp_path, monkeypatch, invoke, model=cr_claude.GENERAL_MODEL).run(SONNET, input="Write", text=True)
    (command, env), = stages(calls)
    assert "--effort" not in command


def test_an_effort_the_engine_chose_itself_is_kept(tmp_path, monkeypatch):
    invoke, calls = cli()
    runtime(tmp_path, monkeypatch, invoke).run(OPUS + ["--effort", "low"], input="Review", text=True)
    (command, env), = stages(calls)
    assert cr_claude._option(command, "--effort") == "low" and command.count("--effort") == 1


def test_an_older_claude_code_without_the_option_is_never_given_it(tmp_path, monkeypatch):
    invoke, calls = cli(help_text="  --model <model>   Model for the current session")
    runtime(tmp_path, monkeypatch, invoke).run(OPUS, input="Review", text=True)
    (command, env), = stages(calls)
    assert "--effort" not in command


def test_the_help_check_runs_once_per_claude_code(tmp_path, monkeypatch):
    invoke, calls = cli()
    run = runtime(tmp_path, monkeypatch, invoke)
    run.run(OPUS, input="One", text=True)
    run.run(OPUS, input="Two", text=True)
    assert sum(1 for c in calls if c[0][1:] == ["--help"]) == 1


@pytest.mark.parametrize("chosen,expected", [("low", "low"), ("XHIGH", "xhigh"), ("default", None), ("loud", "medium")])
def test_cr_claude_effort_sets_every_stage(tmp_path, monkeypatch, chosen, expected):
    monkeypatch.setenv("CR_CLAUDE_EFFORT", chosen)
    invoke, calls = cli()
    runtime(tmp_path, monkeypatch, invoke).run(OPUS, input="Review", text=True)
    (command, env), = stages(calls)
    assert (cr_claude._option(command, "--effort") or None) == expected


def test_an_effort_inherited_from_a_claude_code_chat_never_reaches_a_stage(tmp_path, monkeypatch):
    invoke, calls = cli()
    runtime(tmp_path, monkeypatch, invoke).run(OPUS, input="Review", text=True,
                                                env={"CLAUDE_EFFORT": "xhigh", "PATH": "/usr/bin"})
    (command, env), = stages(calls)
    assert "CLAUDE_EFFORT" not in env and cr_claude._option(command, "--effort") == "medium"
