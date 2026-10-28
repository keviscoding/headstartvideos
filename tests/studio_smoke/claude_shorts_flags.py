"""Windows CI: the Shorts pipelines' one-question flags reach the real Claude Code (npm's claude.cmd) intact.

Every Roblox Story Short on Windows stopped at its director with "the director did not answer with a plan" (Studio
110, 2026-10-28), the real reason thrown away. Its flags include an empty --tools value and a JSON --mcp-config, and
npm's claude.cmd runs through cmd.exe, which parses the command line again. Studio now starts the script the shim
points to with node (cr_claude.node_route / print_command). This runs the real CLI with no account: an answer about
signing in means Claude Code read every argument and stopped only because nobody is signed in. It also runs the same
flags straight through claude.cmd and a stand-in that echoes its arguments, and reports what each one got (the
direct runs are reported, not required: they show whether cmd.exe was the cause).

Usage: python tests/studio_smoke/claude_shorts_flags.py CLAUDE_CMD WORK_FOLDER [--report report.md]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_runtime"))
sys.path.insert(0, str(ROOT / "tests"))

import cr_claude  # noqa: E402
from test_claude_print import PROMPT, fake_npm  # noqa: E402

SIGN_IN = re.compile(r"not logged in|log ?in|/login|api key|authenticat|unauthori[sz]ed|credential|oauth", re.I)


def run(command: list, env: dict, cwd: Path, prompt: str = "Say OK.") -> tuple:
    try:
        done = subprocess.run(command, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, cwd=str(cwd), timeout=180)
        return done.returncode, done.stdout or "", done.stderr or ""
    except (OSError, subprocess.SubprocessError) as error:
        return None, "", f"{type(error).__name__}: {error}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("claude")
    parser.add_argument("work")
    parser.add_argument("--report")
    a = parser.parse_args()
    work = Path(a.work)
    room = work / "room with space"
    room.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="no-account-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
    env.update({"USERPROFILE": str(home), "HOME": str(home), "APPDATA": str(home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home / "AppData" / "Local")})
    rows, required = [], []

    def row(name, ok, code, text, need=False):
        rows.append((name, ok, code, text, need))
        if need:
            required.append(ok)

    routed = cr_claude.print_command("claude-sonnet-5-5", exe=a.claude)
    code, out, err = run(routed, env, room)
    answer, why = cr_claude.print_answer(out, err)
    row(f"The real Claude Code through node ({Path(routed[0]).name} {Path(routed[1]).name if len(routed) > 1 else ''}) "
        "reads every Shorts flag and asks only for a sign-in", bool(SIGN_IN.search(out + err)) and not answer,
        code, why or out + err, need=True)
    seeing = cr_claude.print_command("claude-sonnet-5-5", read_dir=room, exe=a.claude)
    code, out, err = run(seeing, env, room)
    row("The same with Claude's Read tool in a folder with a space", bool(SIGN_IN.search(out + err)), code,
        cr_claude.print_answer(out, err)[1] or out + err, need=True)
    broken = routed[:-1] + ["{not json"]
    code, out, err = run(broken, env, room)
    row("A broken --mcp-config gets a different answer (so the checks above prove the JSON was read)",
        not SIGN_IN.search(out + err) or "mcp" in (out + err).lower(), code, out + err)
    direct = [a.claude, *cr_claude.print_command("claude-sonnet-5-5", exe="claude")[1:]]
    code, out, err = run(direct, env, room)
    row("Reported: the same flags straight through claude.cmd (cmd.exe)", bool(SIGN_IN.search(out + err)), code,
        out + err)

    stand = fake_npm(work / "npm with space")
    for name, command in (("node route", cr_claude.print_command("m", exe=str(stand))),
                          ("straight through claude.cmd", [str(stand), *cr_claude.print_command("m", exe="x")[1:]])):
        code, out, err = run(command, env, room, PROMPT)
        answer, why = cr_claude.print_answer(out, err)
        try:
            got = json.loads(answer)
        except ValueError:
            got = {}
        sent = command[2:] if name == "node route" else command[1:]
        same = got.get("argv") == sent and got.get("stdin") == PROMPT
        row(f"{'Required' if name == 'node route' else 'Reported'}: a stand-in echo, {name}: every argument and the "
            "prompt arrive exactly", same, code, "" if same else f"sent {sent}, got {got.get('argv')} ({why or err})",
            need=name == "node route")

    lines = ["## The Shorts pipelines' Claude flags on Windows (real npm claude.cmd, no account)", ""]
    for name, ok, code, text, need in rows:
        shown = re.sub(r"\s+", " ", str(text)).strip()[:400]
        mark = ("PASS" if ok else "FAIL") if need else ("yes" if ok else "no")
        lines.append(f"- {mark}: {name} (exit {code}){': ' + shown if shown else ''}")
    report = "\n".join(lines) + "\n"
    print(report)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(report, encoding="utf-8")
    shutil.rmtree(home, ignore_errors=True)
    return 0 if all(required) else 1


if __name__ == "__main__":
    sys.exit(main())
