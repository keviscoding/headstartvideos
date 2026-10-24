"""Prevent late caption failures after paid assets have already been made."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_render
import cr_studio


def test_version_only_ffmpeg_is_insufficient_and_full_build_is_selected(monkeypatch):
    monkeypatch.setattr(cr_studio.sys, "platform", "darwin")
    monkeypatch.setattr(cr_studio.shutil, "which", lambda name: name)
    def run(args, **kwargs):
        full = "ffmpeg-full" in args[0]
        stdout = " TS subtitles caption renderer" if args[-1] == "-filters" and full else ""
        if args[-1] == "-encoders":
            stdout = " V libx264 H264\n A aac AAC\n A libmp3lame MP3"
        return SimpleNamespace(returncode=0, stdout=stdout)
    monkeypatch.setattr(cr_studio.subprocess, "run", run)
    monkeypatch.setenv("PATH", "/original/tools")
    assert not cr_studio.video_tools_ready()
    cr_studio.configure_video_tools()
    assert cr_studio.os.environ["PATH"].startswith("/opt/homebrew/opt/ffmpeg-full/bin" + os.pathsep)
    assert cr_studio.video_tools_ready("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")


def test_mux_error_preserves_actionable_diagnostic_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "private-test-key")
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(234, ["ffmpeg"], stderr=b"No such filter: subtitles private-test-key https://media.example/signed?token=secret\nInvalid argument")
    import cr_manimgx
    monkeypatch.setattr(cr_manimgx, "install", lambda *a, **k: None)
    engine = SimpleNamespace(_final_mux=fail)
    cr_render.install(engine)
    try:
        engine._final_mux(tmp_path, "audio", "subs", True)
    except RuntimeError as error:
        assert "saved" in str(error) and "No such filter: subtitles" in str(error)
        assert "private-test-key" not in str(error) and "token=secret" not in str(error)
    else:
        raise AssertionError("A failed mux must not be marked complete")
    report = tmp_path / "cr-render-error.log"
    assert "Invalid argument" in report.read_text()
    if os.name != "nt":                         # POSIX permission bits; Windows keeps the file private by its ACL
        assert report.stat().st_mode & 0o077 == 0
