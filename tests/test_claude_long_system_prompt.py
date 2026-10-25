"""FOLIO hands Claude its drawing rules as --system-prompt (42 KB for the storyboard, 54 KB for every drawing). Windows
starts a program with a command line of at most 32,767 characters (npm's claude.cmd: 8,191 through cmd.exe, cut at the
first newline), so every FOLIO video on Windows stopped at its first storyboard call: "cli_failed" in 0 seconds, 6 of 6
customer FOLIO videos (census, 2026-10-25). Such a prompt now travels in a file for that run."""
import json
from types import SimpleNamespace

from local_runtime import cr_claude

RULES = "THE FOLIO BIBLE\n" + "Draw every shot as a page of a picture book. Ink, wash, paper.\n" * 700   # ~44 KB


def runtime(tmp_path, monkeypatch, invoke):
    cfg = {"brain_mode": "claudecode", "costs_accepted": True,
           "general_model": cr_claude.GENERAL_MODEL, "visual_model": cr_claude.VISUAL_MODEL}
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: {"ready": True, "account_fingerprint": "a" * 64})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (24000, "folio:_call"))
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: cfg,
                             current_project=lambda: {"id": "crv_" + "1" * 24},
                             authorize_stage=lambda stage: {"execute": True, "authorization_token": "x" * 32,
                                                            "selected_model": cr_claude.VISUAL_MODEL},
                             finish_stage=lambda receipt: None)
    return cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude", invoke)


def folio_call():
    """folio._call's command, as the engine builds it."""
    return ["/fake/claude", "-p", "--model", "claude-opus-5-5", "--strict-mcp-config", "--tools", "",
            "--no-session-persistence", "--disable-slash-commands", "--system-prompt", RULES, "--output-format", "json"]


def test_a_long_system_prompt_reaches_claude_through_a_file_and_the_file_is_removed(tmp_path, monkeypatch):
    seen = []
    def invoke(command, **kw):
        path = cr_claude._option(command, "--system-prompt-file")
        seen.append((command, open(path, encoding="utf-8", newline="").read() if path else None))
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"type": "result", "subtype": "success", "is_error": False, "result": '{"shots": []}', "usage": {}}) + "\n")
    out = runtime(tmp_path, monkeypatch, invoke).run(folio_call(), input="Storyboard this script.", text=True)
    assert json.loads(json.loads(out.stdout)["result"]) == {"shots": []}
    command, text = seen[0]
    assert "--system-prompt" not in command and RULES not in command, "the 44 KB rules never ride on the command line"
    assert text == RULES, "Claude reads exactly the engine's rules"
    assert sum(len(str(a)) for a in command) < 8000, "short enough for claude.cmd on Windows"
    assert not list(tmp_path.rglob(".prompt-*")), "the rules file is gone after the run"


def test_the_stage_is_the_same_request_whichever_way_the_prompt_travels(tmp_path, monkeypatch):
    stages = []
    def invoke(command, **kw):
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"type": "result", "is_error": False, "result": "ok", "usage": {}}) + "\n")
    rt = runtime(tmp_path, monkeypatch, invoke)
    authorize = rt.bridge.authorize_stage
    rt.bridge.authorize_stage = lambda stage: stages.append(stage["stage_id"]) or authorize(stage)
    rt.run(folio_call(), input="Storyboard this script.", text=True)
    expected = "cc_" + cr_claude._digest({"args": folio_call()[1:], "input": "Storyboard this script.",
                                          "max_tokens": 24000, "read_files": []})
    assert stages == [expected], "a resumed FOLIO video finds its saved storyboard"


def test_only_prompts_the_command_line_cannot_carry_move_to_files(tmp_path):
    short = ["claude", "-p", "--system-prompt", "You are a professional writer.", "--model", "opus"]
    assert cr_claude.prompts_on_disk(short, tmp_path, windows=True) == (short, [])
    assert cr_claude.prompts_on_disk(short, tmp_path, windows=False) == (short, [])
    two_lines = ["claude", "-p", "--append-system-prompt=Rule one.\nRule two."]
    moved, files = cr_claude.prompts_on_disk(two_lines, tmp_path, windows=True)
    assert moved[:2] == ["claude", "-p"] and moved[2] == "--append-system-prompt-file"
    assert files and files[0].read_bytes() == b"Rule one.\nRule two.", "cmd.exe would cut it at the newline"
    assert cr_claude.prompts_on_disk(two_lines, tmp_path, windows=False)[0] == two_lines, "a Mac keeps it inline"
    huge = ["claude", "-p", "--system-prompt", "x" * 250000]
    moved, files = cr_claude.prompts_on_disk(huge, tmp_path, windows=False)
    assert moved == ["claude", "-p", "--system-prompt-file", str(files[0])]
