"""Exercise first-launch interpreter selection and interrupted installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1] / "local_runtime"
BROWSER_CHECK = ["-c", "import os, sys\nfrom playwright.sync_api import sync_playwright\np = sync_playwright().start()\n"
                       "ok = os.path.exists(p.chromium.executable_path)\np.stop()\nsys.exit(0 if ok else 1)"]


def fake_python(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    # The repository's venv path contains spaces. Resolve its interpreter link
    # before using it in a shebang, whose interpreter field cannot be quoted.
    interpreter = Path(sys.executable).resolve()
    script = f'''#!{interpreter}
import json, os, pathlib, shutil, sys
version = {version!r}
args = sys.argv[1:]
with open(os.environ["LAUNCH_TEST_LOG"], "a") as log:
    log.write(json.dumps({{"exe":str(pathlib.Path(sys.argv[0])),"version":version,"args":args}})+"\\n")
if args[:1] == ["-c"]:
    if "chromium.executable_path" in args[1]:
        raise SystemExit(1 if os.environ.get("LAUNCH_TEST_NO_BROWSER") else 0)
    if "Studio Python:" in args[1]: print("Studio Python:", version)
    raise SystemExit(0 if tuple(map(int,version.split("."))) >= (3,10) else 1)
if args == ["--version"]:
    print("Python",version)
elif args[:2] == ["-m","venv"]:
    child=pathlib.Path(args[2])/"bin"/"python"
    child.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(sys.argv[0],child)
elif args[:2] == ["-m","pip"]:
    raise SystemExit(int(os.environ.get("LAUNCH_TEST_FAIL_PIP","0")))
elif args[:3] == ["-m","playwright","install"]:
    raise SystemExit(int(os.environ.get("LAUNCH_TEST_FAIL_BROWSER","0")))
elif args == ["cr_studio.py"]:
    raise SystemExit(int(os.environ.get("LAUNCH_TEST_STUDIO_EXIT","0")))
else:
    raise SystemExit("Unexpected fake interpreter call: "+repr(args))
'''
    path.write_text(script)
    path.chmod(0o755)


@pytest.fixture
def mac_launch(tmp_path):
    if sys.platform == "win32":
        pytest.skip("Start on Mac.command runs in /bin/bash, with fake POSIX Pythons (Windows has its own launcher tests)")
    folder = tmp_path / "Fresh studio folder"
    folder.mkdir()
    launcher = folder / "Start on Mac.command"
    shutil.copy2(ROOT / launcher.name, launcher)
    tools = tmp_path / "Interpreter tools"
    tools.mkdir()
    # Override all candidate names so the test never launches an installed
    # interpreter or installs packages from this computer.
    for name in ("python3.12", "python3", "python3.11", "python3.10"):
        fake_python(tools / name, "3.9.9")
    log = tmp_path / "calls.jsonl"
    environment = {**os.environ, "PATH":str(tools)+os.pathsep+os.environ["PATH"],
                   "LAUNCH_TEST_LOG":str(log)}
    def run(**extra):
        result = subprocess.run(["/bin/bash",str(launcher)],input="\n",capture_output=True,text=True,
                                env={**environment,**extra},timeout=10)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return folder, tools, run


def test_mac_selects_compatible_fallback_and_preserves_spaced_paths(mac_launch):
    folder, tools, run = mac_launch
    fake_python(tools / "python3.11", "3.11.10")
    result, calls = run()
    assert result.returncode == 0
    assert "Studio Python: 3.11.10" in result.stdout
    assert (folder / ".venv/.cr-installed").is_file()
    create = next(call for call in calls if call["args"][:2] == ["-m","venv"])
    assert create["exe"] == str(tools / "python3.11")
    assert [call["args"] for call in calls if call["exe"].endswith(".venv/bin/python")] == [
        ["-c", "import sys; print(\"Studio Python:\", sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
        ["-m","pip","install","-r","requirements.txt"],
        ["-m","playwright","install","chromium"], BROWSER_CHECK, ["cr_studio.py"]]


def test_mac_rejects_all_old_interpreters_before_creating_environment(mac_launch):
    folder, _, run = mac_launch
    result, calls = run()
    assert result.returncode == 1 and "Python 3.10 or newer" in result.stdout
    assert not (folder / ".venv").exists()
    assert not any(call["args"][:2] == ["-m","venv"] for call in calls)


def test_mac_rejects_existing_old_environment_without_installing_or_deleting(mac_launch):
    folder, tools, run = mac_launch
    fake_python(tools / "python3.12", "3.12.9")
    fake_python(folder / ".venv/bin/python", "3.9.9")
    marker = folder / ".venv/.cr-installed"
    marker.touch()
    result, calls = run()
    assert result.returncode == 1 and "rename the .venv folder" in result.stdout
    assert marker.is_file()
    assert len(calls) == 1 and calls[0]["args"][0] == "-c"


@pytest.mark.parametrize("failure,expected",[("LAUNCH_TEST_FAIL_PIP","studio libraries"),
                                              ("LAUNCH_TEST_FAIL_BROWSER","graphics browser")])
def test_mac_install_failure_stays_visible_and_does_not_mark_ready(mac_launch,failure,expected):
    folder, tools, run = mac_launch
    fake_python(tools / "python3.12", "3.12.9")
    result, calls = run(**{failure:"1"})
    assert result.returncode == 1 and expected in result.stdout
    assert not (folder / ".venv/.cr-installed").exists()
    assert not any(call["args"] == ["cr_studio.py"] for call in calls)


def test_mac_existing_current_environment_skips_install_and_preserves_server_exit(mac_launch):
    folder, _, run = mac_launch
    fake_python(folder / ".venv/bin/python", "3.12.9")
    (folder / ".venv/.cr-installed").touch()
    result, calls = run(LAUNCH_TEST_STUDIO_EXIT="7")
    assert result.returncode == 7
    assert [call["args"] for call in calls][1:] == [BROWSER_CHECK, ["cr_studio.py"]]


def test_mac_reinstalls_a_missing_graphics_browser_on_every_start(mac_launch):
    folder, _, run = mac_launch
    fake_python(folder / ".venv/bin/python", "3.12.9")
    (folder / ".venv/.cr-installed").touch()
    result, calls = run(LAUNCH_TEST_NO_BROWSER="1")
    assert result.returncode == 0
    assert [call["args"] for call in calls][1:] == [BROWSER_CHECK, ["-m","playwright","install","chromium"],
                                                   ["cr_studio.py"]]


def test_windows_launcher_keeps_double_click_diagnostics_and_has_no_compound_blocks():
    text = (ROOT / "Start on Windows.cmd").read_text()
    lines = [line.strip() for line in text.splitlines()]
    assert 'setlocal DisableDelayedExpansion' in lines
    assert 'if /i "%~1"=="--cr-window" goto launch_studio' in lines
    assert 'set "CR_WINDOWS_LAUNCHER=%~f0"' in lines
    assert 'start "ChannelRecipe Studio" "%ComSpec%" /d /k ""%%CR_WINDOWS_LAUNCHER%%" --cr-window"' in lines
    assert lines.index('exit /b 0') < lines.index(':launch_studio')
    # Avoid cmd.exe's whole-block parsing and delayed expansion corrupting paths
    # containing parentheses, ampersands or exclamation marks.
    assert not any(line.endswith("(") or line == ")" for line in lines)
    assert 'cd /d "%~dp0"' in lines
    assert 'if not exist "cr_studio.py" goto incomplete_download' in lines
    assert 'if not exist "requirements.txt" goto incomplete_download' in lines
    assert lines.index('set "CR_PYTHON_ARGS=-3.13"') < lines.index('set "CR_PYTHON_ARGS=-3"')
    assert ':failed' in lines and lines[lines.index(':failed') + 3] == 'pause'
    # Studio itself runs in Python's UTF-8 mode, as its video workers do (review, 2026-10-26)
    start = lines.index(':start_studio')
    assert lines.index('set "PYTHONUTF8=1"', start) < lines.index('".venv\\Scripts\\python.exe" cr_studio.py', start)


@pytest.fixture
def windows_launch(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Requires the real Windows cmd.exe interpreter")
    folder = tmp_path / "Fresh studio & (creator)! 100%"
    folder.mkdir()
    launcher = folder / "Start on Windows.cmd"
    launcher.write_bytes((ROOT / launcher.name).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    (folder / "requirements.txt").write_text("")
    # An existing environment lets this exercise cmd's real quoting and exit
    # behavior without installing libraries or contacting a provider.
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(folder / ".venv")],
                   check=True, capture_output=True, timeout=60)
    (folder / ".venv/.cr-installed").touch()
    def run():
        command = f'"{os.environ["ComSpec"]}" /d /s /c ""{launcher}" --cr-window"'
        return subprocess.run(command, input="\n", capture_output=True, text=True, timeout=15)
    return folder, run


def test_windows_real_cmd_preserves_spaced_metacharacter_path_and_studio_exit(windows_launch):
    folder, run = windows_launch
    (folder / "cr_studio.py").write_text('print("STUDIO STARTED"); raise SystemExit(7)')
    result = run()
    assert result.returncode == 7
    assert "STUDIO STARTED" in result.stdout
    assert "The studio stopped with exit code 7" in result.stdout
    assert (folder / ".venv/.cr-installed").exists()


def test_windows_real_cmd_missing_extracted_files_displays_action(windows_launch):
    _, run = windows_launch
    result = run()
    assert result.returncode == 1
    assert "The studio files are missing" in result.stdout
    assert "Extract All" in result.stdout
    assert "This window stays open" in result.stdout


def test_windows_real_cmd_studio_failure_keeps_original_diagnostic(windows_launch):
    folder, run = windows_launch
    (folder / "cr_studio.py").write_text('raise RuntimeError("visible studio error")')
    result = run()
    assert result.returncode == 1
    assert "visible studio error" in result.stderr
    assert "The studio stopped with exit code 1" in result.stdout


def test_windows_real_cmd_double_click_bootstrap_starts_owned_persistent_window(windows_launch):
    folder, _ = windows_launch
    launcher = folder / "Start on Windows.cmd"
    signal = folder / "test-studio-started.json"
    stop = folder / "test-studio-stop"
    ended = folder / "test-studio-ended"
    nonce = uuid.uuid4().hex
    # Keep this synthetic studio alive until the test has identified its parent
    # cmd.exe. No CR modules, provider keys or installed engine are loaded.
    (folder / "cr_studio.py").write_text(f'''import json, os, pathlib, time
signal = pathlib.Path({str(signal)!r})
signal.write_text(json.dumps({{"nonce": {nonce!r}, "parent_pid": os.getppid(),
    "cwd": os.getcwd(), "launcher": os.environ.get("CR_WINDOWS_LAUNCHER"),
    "studio_file": str(pathlib.Path(__file__).resolve())}}))
stop = pathlib.Path({str(stop)!r})
deadline = time.monotonic() + 30
while not stop.exists() and time.monotonic() < deadline:
    time.sleep(0.05)
pathlib.Path({str(ended)!r}).touch()
raise SystemExit(7)
''')
    command = f'"{os.environ["ComSpec"]}" /d /s /c ""{launcher}""'
    saved = None
    bootstrap = None
    owned_cmd = None
    def process_info(pid):
        result = subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f'Get-CimInstance Win32_Process -Filter "ProcessId = {pid}" '
            '| Select-Object Name,ParentProcessId,CommandLine,ExecutablePath | ConvertTo-Json -Compress'
        ], check=True, capture_output=True, text=True, timeout=10)
        return json.loads(result.stdout) if result.stdout.strip() else None
    def is_owned_window(process):
        return (process and process["Name"].lower() == "cmd.exe"
                and process["ParentProcessId"] == bootstrap.pid
                and "/k" in (process["CommandLine"] or "").lower()
                and "--cr-window" in (process["CommandLine"] or ""))
    with (folder / "test-bootstrap.log").open("w") as log:
        try:
            bootstrap = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            assert bootstrap.wait(timeout=15) == 0
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and saved is None:
                try:
                    saved = json.loads(signal.read_text())
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
            assert saved is not None, (folder / "test-bootstrap.log").read_text()
            assert saved["nonce"] == nonce
            assert Path(saved["cwd"]).resolve() == folder.resolve()
            assert Path(saved["launcher"]).resolve() == launcher.resolve()
            assert Path(saved["studio_file"]).resolve() == (folder / "cr_studio.py").resolve()
            assert isinstance(saved["parent_pid"], int) and saved["parent_pid"] > 0
            # CPython's Windows venv redirector can insert another python.exe
            # between the synthetic script and cmd.exe. Follow only this test's
            # exact venv/base executables, never an arbitrary Python process.
            allowed_python = {Path(sys.executable).resolve(),
                              Path(getattr(sys, "_base_executable", sys.executable)).resolve(),
                              (folder / ".venv/Scripts/python.exe").resolve()}
            pid = saved["parent_pid"]
            seen = set()
            for _ in range(4):
                assert pid not in seen
                seen.add(pid)
                parent = process_info(pid)
                assert parent is not None, f"Studio ancestor {pid} disappeared"
                if parent["Name"].lower() == "cmd.exe":
                    assert is_owned_window(parent), parent
                    owned_cmd = pid
                    break
                assert parent["Name"].lower() == "python.exe", parent
                assert Path(parent["ExecutablePath"]).resolve() in allowed_python, parent
                assert "cr_studio.py" in (parent["CommandLine"] or ""), parent
                pid = parent["ParentProcessId"]
            assert owned_cmd is not None, "No owned persistent cmd.exe found within bounded ancestry"
            # The window must survive an actual nonzero studio exit too.
            stop.touch()
            deadline = time.monotonic() + 5
            while not ended.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert ended.exists()
            assert is_owned_window(process_info(owned_cmd))
        finally:
            stop.touch()
            # Only kill the verified cmd ancestor of our nonce/folder-bound
            # synthetic studio. Recheck its ownership immediately before kill.
            if owned_cmd is not None:
                if is_owned_window(process_info(owned_cmd)):
                    subprocess.run([
                        "taskkill.exe", "/PID", str(owned_cmd), "/T", "/F"
                    ], capture_output=True, timeout=10)


