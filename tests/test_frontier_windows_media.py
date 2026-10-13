"""Windows executes the original archival media commands without Unix nice."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_bridge
import cr_workers


@pytest.fixture(params=["nt", "posix"])
def platform_launcher(request, tmp_path, monkeypatch):
    captured = []
    original_popen = subprocess.Popen
    result = SimpleNamespace(poll=lambda: None)

    def launch(command, *args, **kwargs):
        captured.append((command, args, kwargs))
        return result

    # Do not change global os.name: pathlib and pytest keep the real host OS.
    monkeypatch.setattr(cr_workers, "os", SimpleNamespace(name=request.param, path=os.path))
    monkeypatch.setattr(cr_workers, "HOME", tmp_path)
    monkeypatch.setattr(cr_workers, "_installed", False)
    monkeypatch.setattr(cr_workers, "_contained", False)
    monkeypatch.setattr(cr_workers, "_original_popen", launch)
    monkeypatch.setattr(cr_bridge, "_project", None)
    cr_workers.install()
    try:
        yield SimpleNamespace(windows=request.param == "nt", calls=captured, result=result, home=tmp_path)
    finally:
        cr_workers.uninstall()
        subprocess.Popen = original_popen


@pytest.mark.parametrize("arguments", [
    ["-v", "error", "-y", "-i", "film with spaces.mp4", "-vf", "fps=0.125,scale=240:-2",
     "-threads", "2", "frames/f_%04d.jpg"],
    ["-v", "info", "-ss", "1.50", "-t", "3.10", "-i", "film.mp4",
     "-vf", "select='gt(scene,0.32)',showinfo", "-an", "-f", "null", "-"],
    ["-v", "error", "-y", "-ss", "1.500", "-t", "3.100", "-i", "film.mp4",
     "-vf", "fps=12,scale=480:-2", "-q:v", "4", "-threads", "2", "frames/%04d.jpg"],
])
def test_original_archival_ffmpeg_argv_and_io_are_preserved(platform_launcher, arguments):
    command = ["nice", "-n", "10", "ffmpeg", *arguments]
    unchanged = list(command)
    options = {"cwd": "a studio folder", "env": {"PATH": "selected tools"},
               "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
               "stderr": subprocess.STDOUT, "text": True, "encoding": "utf-8",
               "creationflags": 512, "start_new_session": False}
    assert subprocess.Popen(command, **options) is platform_launcher.result
    expected = command[3:] if platform_launcher.windows else command
    assert platform_launcher.calls == [(expected, (), options)]
    assert command == unchanged


def test_ffprobe_tuple_stays_an_argv_tuple(platform_launcher):
    command = ("nice", "-n", "10", "ffprobe", "-of", "json", "a movie.mp4")
    subprocess.Popen(command, stderr=subprocess.PIPE)
    expected = command[3:] if platform_launcher.windows else command
    assert platform_launcher.calls[0][0] == expected
    assert isinstance(platform_launcher.calls[0][0], tuple)


@pytest.mark.parametrize("command", [
    "nice -n 10 ffmpeg -version",
    ["nice", "-n", "11", "ffmpeg", "-version"],
    ["nice", "-10", "ffmpeg", "-version"],
    ["nice", "-n", "10", "node", "render.js"],
    ["/usr/bin/nice", "-n", "10", "ffmpeg", "-version"],
    ["nice", "-n", "10", "/usr/bin/ffmpeg", "-version"],
    ["cmd", "/c", "nice", "-n", "10", "ffmpeg", "-version"],
    ["ffmpeg", "-version"],
])
def test_unrelated_priority_and_shell_text_are_not_reinterpreted(platform_launcher, command):
    subprocess.Popen(command)
    assert platform_launcher.calls == [(command, (), {})]


@pytest.mark.parametrize("args,options", [
    ((), {"shell": True}),
    ((), {"executable": "a custom executable"}),
    ((0,), {}),  # Positional Popen controls are deliberately not parsed.
])
def test_shell_executable_and_positional_overrides_are_kept(platform_launcher, args, options):
    command = ["nice", "-n", "10", "ffmpeg", "-version"]
    subprocess.Popen(command, *args, **options)
    assert platform_launcher.calls == [(command, args, options)]


def test_windows_media_compatibility_cannot_bypass_project_authorization(platform_launcher):
    command = ["nice", "-n", "10", sys.executable,
               str(platform_launcher.home / "timeline.py"), "render", "job"]
    with pytest.raises(RuntimeError, match="start this creative work"):
        subprocess.Popen(command)
    assert platform_launcher.calls == []
