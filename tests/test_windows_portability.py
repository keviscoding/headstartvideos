"""Studio code runs on creators' own Windows PCs, where two mistakes keep reaching customers first (2026-10-23/24):

- a text read or write without encoding="utf-8" uses Windows' cp1252, so the setup page crashed on a UTF-8 file and
  text came back garbled; a subprocess in text mode without an encoding decodes its UTF-8 output the same way;
- a subprocess started by the bare name "claude" (or "npm", "npx"): npm installs claude.cmd, which Windows can't start
  by name ([WinError 2]). cr_claude.find_executable() gives the full path.

These rules read the source, so they fail on a Mac too, before a release reaches a Windows PC. The Windows CI workflow
(.github/workflows/studio-windows.yml) also runs the tests and starts Studio itself on Windows (tests/studio_smoke)."""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "local_runtime"
SHORTS = ROOT / "data" / "frontier" / "shorts"           # the private Shorts source, when this checkout has it
CMD_SHIMS = {"claude", "npm", "npx", "pnpm", "yarn"}      # npm-installed commands are .cmd files on Windows
SUBPROCESS = {"run", "Popen", "call", "check_call", "check_output"}
PIPES = ("capture_output", "stdout", "stderr", "stdin", "input")
MODE = re.compile(r"[rwxabt+U]*")
NOT_FILES = {"webbrowser", "zipfile", "tarfile", "gzip", "Image", "wave", "av"}   # .open() that isn't a file's


def shipped_python():
    """Every Python file a creator's Studio runs: local_runtime (what the builder ships from this repository) and the
    private Shorts pipelines when they're here."""
    files = sorted(RUNTIME.glob("*.py"))
    if SHORTS.is_dir():
        files += sorted(p for p in SHORTS.rglob("*.py") if "node_modules" not in p.parts and "__pycache__" not in p.parts)
    return files


def _name(path):
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name


def _keyword(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _true(node):
    return isinstance(node, ast.Constant) and node.value is True


def _text_mode(mode):
    """True for a text mode, False for binary, None when the code picks it at run time."""
    if mode is None:
        return True
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" not in mode.value
    return None


def _subprocess_call(call):
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return ((func.value.id == "subprocess" and func.attr in SUBPROCESS)
                or (func.value.id == "asyncio" and func.attr == "create_subprocess_exec"))
    return False


def problems(path):
    source = path.read_text(encoding="utf-8")
    found = []
    for node in ast.walk(ast.parse(source, str(path))):
        if not isinstance(node, ast.Call):
            continue
        func, where = node.func, f"{_name(path)}:{node.lineno}"
        has_encoding = _keyword(node, "encoding") is not None
        receiver = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
        method = func.attr if isinstance(func, ast.Attribute) else ""
        if (isinstance(func, ast.Name) and func.id == "open") or (receiver, method) in {("io", "open"), ("os", "fdopen")}:
            mode = node.args[1] if len(node.args) > 1 else _keyword(node, "mode")
            if _text_mode(mode) and not has_encoding and len(node.args) < 4:
                found.append(f"{where}: {method or 'open'}() in text mode without encoding=\"utf-8\"")
        elif method == "read_text":
            if not node.args and not has_encoding:
                found.append(f"{where}: read_text() without encoding=\"utf-8\"")
        elif method == "write_text":
            if len(node.args) < 2 and not has_encoding:
                found.append(f"{where}: write_text() without encoding=\"utf-8\"")
        elif method == "open" and receiver not in NOT_FILES | {"os"}:
            # Path.open(mode): only a literal that reads as a file mode (zip.open("name") and the like aren't modes)
            mode = node.args[0] if node.args else _keyword(node, "mode")
            literal = isinstance(mode, ast.Constant) and isinstance(mode.value, str) and MODE.fullmatch(mode.value)
            if literal and _text_mode(mode) and not has_encoding and len(node.args) < 3:
                found.append(f"{where}: .open() in text mode without encoding=\"utf-8\"")
        # also through a helper (run=subprocess.run): text=True beside a pipe or input is a subprocess call
        piped = _subprocess_call(node) or any(_keyword(node, name) is not None for name in PIPES)
        if piped and (_true(_keyword(node, "text")) or _true(_keyword(node, "universal_newlines"))) and not has_encoding:
            found.append(f"{where}: subprocess in text mode without encoding=\"utf-8\" (Windows decodes it as cp1252)")
        if _subprocess_call(node):
            command = node.args[0] if node.args else _keyword(node, "args")
            first = command.elts[0] if isinstance(command, (ast.List, ast.Tuple)) and command.elts else command
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and \
                    first.value.split(" ")[0].lower() in CMD_SHIMS:
                found.append(f"{where}: starts {first.value.split(' ')[0]!r} by its bare name ([WinError 2] on Windows: "
                             "use its full path, e.g. cr_claude.find_executable())")
    return found


@pytest.mark.parametrize("path", shipped_python(), ids=_name)
def test_studio_code_reads_text_as_utf8_and_starts_cmd_tools_by_path(path):
    assert not problems(path), "\n".join(problems(path))


BAD = '''import json, os, subprocess
from pathlib import Path
setup = (Path("x") / "cr_setup.html").read_text()
Path("subs.srt").write_text("1\\n00:00:00,000 --> 00:00:01,000\\ncafé\\n")
with open("log.txt", "w") as log: pass
with Path("log.txt").open("a") as log: pass
with os.fdopen(fd, "w") as handle: pass
subprocess.run(["claude", "--version"], capture_output=True)
subprocess.Popen(["npm", "install", "-g", "@anthropic-ai/claude-code"])
version = run([exe, "--version"], capture_output=True, text=True)
Path(".cr-runtime.json").write_text(json.dumps({"port": 7860}))
'''
GOOD = '''import json, os, subprocess, webbrowser, zipfile
from pathlib import Path
Path("a.json").write_text(json.dumps({"title": "café"}), encoding="utf-8")
Path("b").read_text(encoding="utf-8")
with open("v.mp4", "rb") as video: pass
with os.fdopen(fd, "wb") as handle: pass
fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
webbrowser.open("http://127.0.0.1:7860/cr-setup")
zipfile.ZipFile("r.zip").open("cr-files.json")
subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
_write(path, source, text=True)
'''


def test_the_rules_catch_what_reached_customers(tmp_path):
    (tmp_path / "cr_bad.py").write_text(BAD, encoding="utf-8")
    found = "\n".join(problems(tmp_path / "cr_bad.py"))
    for line, why in ((3, "read_text"), (4, "write_text"), (5, "open()"), (6, ".open()"), (7, "fdopen()"),
                      (8, "'claude' by its bare name"), (9, "'npm' by its bare name"), (10, "subprocess in text mode"),
                      (11, "write_text")):
        assert f"cr_bad.py:{line}: " in found and why in found, (line, found)
    (tmp_path / "cr_good.py").write_text(GOOD, encoding="utf-8")
    assert problems(tmp_path / "cr_good.py") == []
