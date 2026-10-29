"""One headless question to Claude Code (cr_claude.print_command, print_answer, node_route): the Shorts pipelines'
directors, stagers and checks.

Every Roblox Story Short on Windows stopped at its director with "the director did not answer with a plan" (Studio
110, 2026-10-28): the pipeline threw Claude Code's own reason away. On Windows npm's claude.cmd runs through cmd.exe;
Studio runs the program its shim names instead (node and cli.js, or a native claude.exe), and the empty --tools value,
the --mcp-config JSON and the prompt on stdin arrive exactly as given. The stand-in below is npm's own shim around a
cli.js that echoes what it got, in folders with spaces (a user name with a space); it needs node, no account.
tests/studio_smoke/claude_shorts_flags.py does the same with the real Claude Code on Windows CI (2026-10-29: the flags
arrive intact straight through claude.cmd too).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))

import cr_claude  # noqa: E402

# npm's cmd-shim for a global install (%APPDATA%\npm\claude.cmd), as npm writes it
SHIM = (
    "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n\r\n"
    'IF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n  SET "_prog=node"\r\n'
    "  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\n"
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
    '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*\r\n')
# a cli.js that answers with what it was given, in Claude Code's own JSON
ECHO_JS = """const fs = require('fs');
let stdin = ''; try { stdin = fs.readFileSync(0, 'utf8'); } catch (e) {}
process.stdout.write(JSON.stringify({type: 'result', subtype: 'success', is_error: false,
  result: JSON.stringify({argv: process.argv.slice(2), stdin})}));
"""
# what a Short's director prompt can hold: lines, quotes, cmd.exe's own special characters, a percent sign, unicode
PROMPT = 'In this world "everyone" has a hat.\nLine two & three | four < five > six ^ %PATH% 100%\ncafé — 🎨\n'


def fake_npm(folder: Path) -> Path:
    cli = folder / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text(ECHO_JS, encoding="utf-8")
    shim = folder / "claude.cmd"
    shim.write_bytes(SHIM.encode("utf-8"))
    return shim


def test_one_question_is_a_fixed_command_with_the_prompt_left_for_stdin(monkeypatch):
    monkeypatch.setattr(cr_claude, "find_executable", lambda: "/usr/local/bin/claude")
    assert cr_claude.print_command("claude-opus-5-5") == [
        "/usr/local/bin/claude", "-p", "--model", "claude-opus-5-5", "--tools", "", "--no-session-persistence",
        "--output-format", "json", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    seeing = cr_claude.print_command("m", read_dir="C:/Users/Jo Doe/room")
    assert seeing[4:10] == ["--tools", "Read", "--allowedTools", "Read", "--add-dir", "C:/Users/Jo Doe/room"]


def test_npm_shims_are_followed_to_their_script(tmp_path):
    shim = fake_npm(tmp_path / "npm with space")
    route = cr_claude.node_route(str(shim))
    if shutil.which("node"):
        assert route[1] == str(tmp_path / "npm with space" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js")
    local = tmp_path / "proj" / "node_modules"
    (local / ".bin").mkdir(parents=True)
    (local / "@anthropic-ai" / "claude-code").mkdir(parents=True)
    (local / "@anthropic-ai" / "claude-code" / "cli.js").write_text("//", encoding="utf-8")
    (local / ".bin" / "claude.cmd").write_text('"%_prog%"  "%dp0%\\..\\@anthropic-ai\\claude-code\\cli.js" %*\r\n',
                                               encoding="utf-8")
    if shutil.which("node"):
        assert cr_claude.node_route(str(local / ".bin" / "claude.cmd"))[1] == str(
            local / "@anthropic-ai" / "claude-code" / "cli.js"), "npm's node_modules\\.bin shim too"
    assert cr_claude.node_route("/usr/local/bin/claude") == ["/usr/local/bin/claude"], "anything else as it is"
    assert cr_claude.node_route(str(tmp_path / "gone.cmd")) == [str(tmp_path / "gone.cmd")]
    (tmp_path / "odd.cmd").write_text("@echo hi\r\n", encoding="utf-8")
    assert cr_claude.node_route(str(tmp_path / "odd.cmd")) == [str(tmp_path / "odd.cmd")], "a shim it can't read"
    exe = tmp_path / "native" / "claude.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    (tmp_path / "native" / "claude.cmd").write_text('"%dp0%\\claude.exe" %*\r\n', encoding="utf-8")
    assert cr_claude.node_route(str(tmp_path / "native" / "claude.cmd")) == [str(exe)], "an .exe shim: the .exe"


def test_the_answer_is_read_in_every_shape_and_a_failure_keeps_its_reason():
    done = {"type": "result", "subtype": "success", "is_error": False, "result": '{"shots": []}'}
    assert cr_claude.print_answer(json.dumps(done)) == ('{"shots": []}', "")
    verbose = [{"type": "system", "subtype": "init"}, {"type": "assistant", "message": {}}, done]
    assert cr_claude.print_answer(json.dumps(verbose)) == ('{"shots": []}', ""), "Claude Code with verbose on"
    noisy = "Warning: something about the terminal\n" + json.dumps(done) + "\n"
    assert cr_claude.print_answer(noisy) == ('{"shots": []}', ""), "after other output"
    limit = {"type": "result", "is_error": True, "result": "Claude AI usage limit reached|1761700000\nmore"}
    assert cr_claude.print_answer(json.dumps(limit)) == ("", "Claude AI usage limit reached|1761700000")
    assert cr_claude.print_answer("", "error: unknown option '--tools'\n  at ...") == ("", "error: unknown option '--tools'")
    assert cr_claude.print_answer("", "") == ("", "no answer")
    assert cr_claude.print_answer(json.dumps({"type": "result", "is_error": False, "result": "  "}))[0] == ""


@pytest.mark.skipif(not shutil.which("node"), reason="needs node (npm's Claude Code runs on it)")
@pytest.mark.parametrize("seeing", [False, True])
def test_every_flag_and_the_prompt_arrive_exactly_through_npms_claude(tmp_path, seeing):
    """On Windows (the CI runs this there) npm's claude.cmd would run through cmd.exe; through node_route the script
    gets each argument as given, from folders whose names have spaces."""
    shim = fake_npm(tmp_path / "npm with space")
    room = tmp_path / "room with space"
    room.mkdir()
    command = cr_claude.print_command("claude-opus-5-5", read_dir=room if seeing else None, exe=str(shim))
    assert not command[0].lower().endswith(".cmd"), "never through cmd.exe"
    done = subprocess.run(command, input=PROMPT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(room), timeout=60)
    answer, why = cr_claude.print_answer(done.stdout, done.stderr)
    assert why == "" and done.returncode == 0, done.stderr
    got = json.loads(answer)
    assert got["argv"] == command[2:], "every flag as given: the empty --tools value and the --mcp-config JSON"
    # (Python writes a text stdin with the computer's own line ends: \r\n on Windows, which Claude reads alike)
    assert got["stdin"].replace("\r\n", "\n") == PROMPT, "the prompt, every line and character"
