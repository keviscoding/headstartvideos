"""Setup's graphics browser check says why it isn't ready, and one press installs it again. The launcher used to install
it only on its first run, so "reopen the launcher" never repaired it (a customer was stuck in that loop, 2026-10-23)."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_studio  # noqa: E402


def fake_playwright(monkeypatch, exe_exists=True, launch_error=None, draws=True, full_chrome=False):
    """exe_exists: Playwright's headless shell is installed (what a headless launch starts). full_chrome: the full
    Chrome for Testing is too, which Studio no longer downloads (chromium.executable_path names it)."""
    class Chromium:
        executable_path = "/fake/chrome" if full_chrome else "/missing/chrome"

        def launch(self, **kw):
            if not exe_exists:                                 # Playwright's own words when the shell is missing
                raise RuntimeError("BrowserType.launch: Executable doesn't exist at /missing/chrome-headless-shell\n"
                                   "Looks like Playwright was just installed or updated. Please run: playwright install")
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
    fake_playwright(monkeypatch, launch_error="Target page, context or browser has been closed\nmore detail")
    r = cr_studio.graphics_ready()
    assert "won't start (Target page, context or browser has been closed)" in r["browser_reason"]
    assert "more detail" not in r["browser_reason"]


def test_the_headless_shell_alone_is_a_ready_graphics_browser(monkeypatch):
    """Studio installs only Playwright's headless shell (B5, 2026-10-26): the missing full Chrome for Testing that
    chromium.executable_path names must not read as "isn't installed"."""
    fake_playwright(monkeypatch, exe_exists=True, full_chrome=False)
    assert cr_studio.graphics_ready() == {"browser": True, "webgl": True, "browser_reason": ""}


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
    assert ran == [[sys.executable, "-m", "playwright", "install", "--only-shell", "chromium"]]
    assert cr_studio.BROWSER_FIX["state"] == "done"


def test_a_playwright_without_the_headless_shell_installs_the_full_browser_instead():
    ran = []

    def run(cmd, **kw):
        ran.append(cmd)
        return SimpleNamespace(returncode=1 if "--only-shell" in cmd else 0)    # older than 1.49: unknown option
    assert cr_studio.install_graphics_browser(run=run).returncode == 0
    assert ran == [[sys.executable, "-m", "playwright", "install", "--only-shell", "chromium"],
                   [sys.executable, "-m", "playwright", "install", "chromium"]]
    calls = []
    assert cr_studio.install_graphics_browser(run=lambda cmd, **kw: calls.append(cmd) or 0) == 0, "subprocess.call"
    assert len(calls) == 1


def test_the_launchers_check_the_browser_on_every_start():
    root = Path(__file__).resolve().parents[1] / "local_runtime"
    for name in ("Start on Mac.command", "Start on Windows.cmd"):
        text = (root / name).read_text(encoding="utf-8")
        after_marker = text.split(".cr-installed", 2)[-1]
        # the Mac launcher installs through its install_browser function, the Windows one inline
        assert "playwright install --only-shell chromium" in after_marker or "install_browser ||" in after_marker, name
        assert "playwright install --only-shell chromium" in text and "playwright install chromium" in text, name
