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


def test_a_long_prompt_bullet_is_not_mistaken_for_a_file(tmp_path):
    """A style study's watch step: "- Fonts: only these bundled faces..." made macOS raise File name too long
    (Errno 63) and the vision call failed every time (a customer's report, Studio 104)."""
    picture = tmp_path / "frame.png"
    picture.write_bytes(b"png")
    prompt = "\n".join(["Look at these:", "- " + str(picture), "- Fonts: only these bundled faces " + "x" * 1200,
                        "- " + "\0bad"])
    hashes = cr_claude._read_inputs(prompt, ["/fake/claude", "-p", "--tools", "Read"])
    assert [h[0] for h in hashes] == [str(picture.resolve())], "the real picture counts, the bullets don't crash it"


def test_a_long_text_answer_continued_past_the_output_limit_reaches_the_engine_whole(tmp_path, monkeypatch):
    """A style study's graphics kit asks for 32k tokens of plain text: Claude Code went on in a second message and
    only the last part came back, so kit.json and the start of kit.js were lost."""
    seen = []
    def invoke(command, **kw):
        seen.append(command)
        return SimpleNamespace(returncode=0, stderr="", stdout=stream(
            {"type": "system", "subtype": "init"}, said('=== kit.json ===\n{"a": 1}\n=== kit.js ===\nfunction f(', "m1"),
            GO_ON, said(") { return 1 }", "m2"),
            {"type": "result", "subtype": "success", "is_error": False, "result": ") { return 1 }",
             "usage": {"output_tokens": 9}}))
    text_call = ["/fake/claude", "-p", "--model", "opus", "--tools", ""]
    out = runtime(tmp_path, monkeypatch, invoke).run(text_call, input="Build the kit", text=True)
    assert out.stdout == '=== kit.json ===\n{"a": 1}\n=== kit.js ===\nfunction f() { return 1 }'
    stage = [c for c in seen if c[1:] != ["--help"]][0]
    assert cr_claude._option(stage, "--output-format") == "stream-json", "read as a stream to join the parts"


def test_a_short_text_answer_is_left_as_plain_text(tmp_path, monkeypatch):
    seen = []
    def invoke(command, **kw):
        seen.append(command)
        return SimpleNamespace(returncode=0, stderr="", stdout="Just the answer.")
    out = runtime(tmp_path, monkeypatch, invoke, maximum=4000).run(["/fake/claude", "-p", "--model", "opus"],
                                                                   input="Name it", text=True)
    assert out.stdout == "Just the answer."
    assert "stream-json" not in [c for c in seen if c[1:] != ["--help"]][0]
