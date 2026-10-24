"""Setup's graphics browser check says why it isn't ready, and one press installs it again. The launcher used to install
it only on its first run, so "reopen the launcher" never repaired it (a customer was stuck in that loop, 2026-10-23)."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_studio  # noqa: E402


def fake_playwright(monkeypatch, exe_exists=True, launch_error=None, draws=True):
    class Chromium:
        executable_path = "/fake/chrome" if exe_exists else "/missing/chrome"

        def launch(self, **kw):
            if launch_error:
                raise RuntimeError(launch_error)
            page = SimpleNamespace(evaluate=lambda js: {"browser": draws, "webgl": draws})
            return SimpleNamespace(new_page=lambda: page, close=lambda: None)

    class Ctx:
        def __enter__(self):
            return SimpleNamespace(chromium=Chromium())

        def __exit__(self, *a):
            return False
    module = SimpleNamespace(sync_playwright=lambda: Ctx())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setattr(cr_studio.Path, "is_file", lambda self: self.as_posix() == "/fake/chrome" or Path.exists(self))
    cr_studio._graphics_check.clear()


def test_a_missing_browser_says_so_and_is_checked_again_every_time(monkeypatch):
    fake_playwright(monkeypatch, exe_exists=False)
    r = cr_studio.graphics_ready()
    assert r["browser"] is False and "isn't installed yet" in r["browser_reason"] and "Fix it" in r["browser_reason"]
    fake_playwright(monkeypatch, exe_exists=True)
    monkeypatch.setattr(cr_studio, "_graphics_check", cr_studio._graphics_check)
    assert cr_studio.graphics_ready()["browser"] is True, "a failure is never cached: the fix shows at once"


def test_a_browser_that_wont_start_gives_the_first_line_of_the_error(monkeypatch):
    fake_playwright(monkeypatch, launch_error="Executable doesn't exist at /x\nmore detail")
    r = cr_studio.graphics_ready()
    assert "won't start (Executable doesn't exist at /x)" in r["browser_reason"] and "more detail" not in r["browser_reason"]


def test_fix_it_installs_into_studios_own_python_in_the_background(monkeypatch):
    ran = []
    monkeypatch.setattr(cr_studio.subprocess, "run",
                        lambda cmd, **kw: ran.append(cmd) or SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    monkeypatch.setattr(cr_studio, "graphics_ready", lambda: {"browser": True})
    cr_studio.BROWSER_FIX.update(state="idle", tail="")
    assert cr_studio.fix_graphics_browser()["state"] == "installing"
    for _ in range(100):
        if cr_studio.BROWSER_FIX["state"] != "installing":
            break
        time.sleep(0.02)
    assert ran == [[sys.executable, "-m", "playwright", "install", "chromium"]]
    assert cr_studio.BROWSER_FIX["state"] == "done"


def test_the_launchers_check_the_browser_on_every_start():
    root = Path(__file__).resolve().parents[1] / "local_runtime"
    for name in ("Start on Mac.command", "Start on Windows.cmd"):
        text = (root / name).read_text(encoding="utf-8")
        after_marker = text.split(".cr-installed", 2)[-1]
        assert "playwright install chromium" in after_marker, name
