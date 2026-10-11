"""Exercise first-launch interpreter selection and interrupted installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1] / "local_runtime"


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
        ["-m","playwright","install","chromium"], ["cr_studio.py"]]


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
    assert len(calls) == 2 and calls[-1]["args"] == ["cr_studio.py"]


