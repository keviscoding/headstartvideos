"""A long answer through Claude Code comes back whole: a 3D review of 25 scenes outgrew one message, Claude Code went
on in the next, and only the last part reached the engine (every fix lost)."""
import json
from types import SimpleNamespace

import pytest

from local_runtime import cr_claude


def runtime(tmp_path, monkeypatch, invoke, maximum=32000):
    cfg = {"brain_mode": "claudecode", "costs_accepted": True,
           "general_model": cr_claude.GENERAL_MODEL, "visual_model": cr_claude.VISUAL_MODEL}
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: {"ready": True, "account_fingerprint": "a" * 64})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (maximum, "threed:_look"))
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: cfg,
                             current_project=lambda: {"id": "crv_" + "1" * 24},
                             authorize_stage=lambda stage: {"execute": True, "authorization_token": "x" * 32,
                                                            "selected_model": cr_claude.VISUAL_MODEL},
                             finish_stage=lambda receipt: None)
    return cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude", invoke)


def stream(*rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def said(text, mid="m"):
    return {"type": "assistant", "message": {"id": mid, "content": [{"type": "text", "text": text}]}}


ARGS = ["/fake/claude", "-p", "--model", "opus", "--tools", "Read", "--output-format", "json"]
READ = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "sheet"}]}}
GO_ON = {"type": "user", "isSynthetic": True, "message": {"content": [{"type": "text", "text":
         "Output token limit hit. Resume directly — no apology, no recap of what you were doing."}]}}


def test_an_answer_continued_past_the_output_limit_reaches_the_engine_whole(tmp_path, monkeypatch):
    seen = []
    def invoke(command, **kw):
        seen.append((command, kw["env"]))
        return SimpleNamespace(returncode=0, stderr="", stdout=stream(
            {"type": "system", "subtype": "init"}, said("I'll open the sheets.", "m0"), READ,
            {"type": "rate_limit_event"}, said('{"scenes": [{"id": "s1", "fix": ', "m1"), GO_ON,
            said('"wider"}, {"id": "s2", "fix": "closer"}]}', "m2"),
            {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
             "result": '"wider"}, {"id": "s2", "fix": "closer"}]}', "usage": {"output_tokens": 9}}))
    out = runtime(tmp_path, monkeypatch, invoke).run(ARGS, input="Review these scenes", text=True)
    reply = json.loads(out.stdout)
    assert json.loads(reply["result"]) == {"scenes": [{"id": "s1", "fix": "wider"}, {"id": "s2", "fix": "closer"}]}
    command, env = seen[0]
    assert cr_claude._option(command, "--output-format") == "stream-json" and "--verbose" in command
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(cr_claude.LONG_OUTPUT_TOKENS), "room for the whole answer"


def test_an_answer_in_one_message_is_returned_as_it_was(tmp_path, monkeypatch):
    seen = []
    def invoke(command, **kw):
        seen.append(kw["env"])
        return SimpleNamespace(returncode=0, stderr="", stdout=stream(
            said("Looking first.", "m0"), READ, said('{"ok": true}', "m1"),
            {"type": "result", "subtype": "success", "is_error": False, "result": '{"ok": true}', "usage": {}}))
    out = runtime(tmp_path, monkeypatch, invoke, maximum=1500).run(ARGS, input="A short look", text=True)
    assert json.loads(out.stdout)["result"] == '{"ok": true}'
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in seen[0], "a short answer keeps Claude Code's own limit"


def test_a_failed_run_is_not_mistaken_for_a_usage_limit_by_the_streams_rate_limit_events(tmp_path, monkeypatch):
    def invoke(command, **kw):
        return SimpleNamespace(returncode=1, stderr="", stdout=stream(
            {"type": "system", "subtype": "init"}, {"type": "rate_limit_event", "rate_limit_info": {}}))
    with pytest.raises(cr_claude.ClaudeStageStopped) as stopped:
        runtime(tmp_path, monkeypatch, invoke).run(ARGS, input="Review", text=True)
    assert "usage limit" not in str(stopped.value)
