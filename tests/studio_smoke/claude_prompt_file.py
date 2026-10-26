"""Windows CI: the real Claude Code (npm's claude.cmd) takes a long, multi-line system prompt from a file.

FOLIO hands Claude 42-54 KB of drawing rules as --system-prompt. Through npm's claude.cmd, cmd.exe caps a command line at
8,191 characters and cuts it at the first newline, so every FOLIO video on Windows stopped at its first storyboard call.
Studio 105 moves such a prompt into a UTF-8 file (cr_claude.prompts_on_disk, --system-prompt-file). This runs the real
CLI with no account: an answer about signing in means Claude Code read its arguments and the file, and stopped only
because nobody is signed in. A missing file must give a different answer, so the check can tell the two apart.

Usage: python tests/studio_smoke/claude_prompt_file.py CLAUDE_CMD WORK_FOLDER [--report report.md]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SIGN_IN = re.compile(r"not logged in|log ?in|/login|api key|authenticat|unauthori[sz]ed|credential|oauth", re.I)
TOO_LONG = re.compile(r"too long|command line|cannot find the path|is not recognized", re.I)
MISSING = re.compile(r"no such file|not found|enoent|does not exist|cannot read|failed to read|could not read", re.I)


def rules() -> str:
    """About 54 KB like FOLIO's, with the characters a cp1252 PC gets wrong."""
    line = "Draw every shot as a page of a picture book — ink, wash, paper, café light, naïve hands. 🎨\n"
    return "THE FOLIO BIBLE\n" + line * 600


def run(claude: str, args: list, env: dict, cwd: Path) -> tuple:
    try:
        done = subprocess.run([claude, "-p", *args, "--output-format", "json", "--max-turns", "1"],
                              input="Say OK.", capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env, cwd=str(cwd), timeout=180)
        return done.returncode, (done.stdout or "") + "\n" + (done.stderr or "")
    except (OSError, subprocess.SubprocessError) as error:          # e.g. WinError 206: the filename or extension is too long
        return None, f"{type(error).__name__}: {error}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("claude")
    parser.add_argument("work")
    parser.add_argument("--report")
    a = parser.parse_args()
    work = Path(a.work) / "ChannelRecipe studio" / "output" / "folio" / "_work" / "why do cats purr"
    work.mkdir(parents=True, exist_ok=True)
    text = rules()
    prompt = work / ".prompt-0123456789abcdef.txt"
    with open(prompt, "w", encoding="utf-8", newline="") as handle:      # as cr_claude.prompts_on_disk writes it
        handle.write(text)
    home = Path(tempfile.mkdtemp(prefix="no-account-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
    env.update({"USERPROFILE": str(home), "HOME": str(home), "APPDATA": str(home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home / "AppData" / "Local")})

    rows = []
    code, out = run(a.claude, ["--system-prompt-file", str(prompt)], env, work)
    file_ok = bool(SIGN_IN.search(out)) and not TOO_LONG.search(out) and not MISSING.search(out)
    rows.append(("A 54 KB multi-line prompt file in a folder with spaces reaches the real Claude Code", file_ok, code, out))
    code, out = run(a.claude, ["--system-prompt-file", str(work / ".prompt-missing.txt")], env, work)
    told = bool(MISSING.search(out)) or not SIGN_IN.search(out)
    rows.append(("A missing prompt file gets a different answer (so the check above proves the file was read)",
                 told, code, out))
    code, out = run(a.claude, ["--system-prompt", text], env, work)
    old_broken = code is None or bool(TOO_LONG.search(out)) or not SIGN_IN.search(out)
    rows.append(("The old way (the rules on the command line) still fails on Windows, as customers saw",
                 old_broken, code, out))
    short = len(" ".join([a.claude, "-p", "--system-prompt-file", str(prompt), "--output-format", "json"])) < 8000
    rows.append(("The command line with the file is under claude.cmd's 8,191 characters", short, 0, ""))

    lines = ["## Real Claude Code reads a long system prompt from a file", ""]
    for name, ok, code, out in rows:
        shown = re.sub(r"\s+", " ", out).strip()[:300]
        lines.append(f"- {'PASS' if ok else 'FAIL'}: {name} (exit {code}){': ' + shown if shown else ''}")
    report = "\n".join(lines) + "\n"
    print(report)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(report, encoding="utf-8")
    # the missing-file control only tells us how strong the first check is; it never fails the job on its own
    return 0 if rows[0][1] and rows[3][1] else 1


if __name__ == "__main__":
    sys.exit(main())
