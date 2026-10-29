"""Setup's graphics browser check says why it isn't ready, and one press installs it again. The launcher used to install
it only on its first run, so "reopen the launcher" never repaired it (a customer was stuck in that loop, 2026-10-23)."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_studio  # noqa: E402


def fake_playwright(monkeypatch, exe_exists=True, launch_error=None, draws=True, full_chrome=False, launch_errors=None):
    """exe_exists: Playwright's headless shell is installed (what a headless launch starts). full_chrome: the full
    Chrome for Testing is too, which Studio no longer downloads (chromium.executable_path names it). launch_errors:
    one launch's error after another (None: that launch works); the launches are counted in launches."""
    launches = []

    class Chromium:
        executable_path = "/fake/chrome" if full_chrome else "/missing/chrome"

        def launch(self, **kw):
            launches.append(kw)
            if not exe_exists:                                 # Playwright's own words when the shell is missing
                raise RuntimeError("BrowserType.launch: Executable doesn't exist at /missing/chrome-headless-shell\n"
                                   "Looks like Playwright was just installed or updated. Please run: playwright install")
            if launch_errors and launch_errors[min(len(launches), len(launch_errors)) - 1]:
                raise RuntimeError(launch_errors[min(len(launches), len(launch_errors)) - 1])
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
    return launches


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
    assert cr_studio.graphics_ready() == {"browser": True, "webgl": True, "browser_reason": "", "browser_why": ""}


def finish_fix():
    for _ in range(200):
        if cr_studio.BROWSER_FIX["state"] != "installing":
            break
        time.sleep(0.02)
    return dict(cr_studio.BROWSER_FIX)


def test_fix_it_installs_into_studios_own_python_in_the_background(monkeypatch):
    ran = []
    monkeypatch.setattr(cr_studio.subprocess, "run",
                        lambda cmd, **kw: ran.append(cmd) or SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    checks = iter([{"browser": False, "browser_why": "missing"}, {"browser": True}])
    monkeypatch.setattr(cr_studio, "graphics_ready", lambda: next(checks, {"browser": True}))
    cr_studio.BROWSER_FIX.update(state="idle", tail="")
    assert cr_studio.fix_graphics_browser()["state"] == "installing"
    assert finish_fix()["state"] == "done"
    assert ran == [[sys.executable, "-m", "playwright", "install", "--only-shell", "chromium"]]


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


# 2026-10-27: the only Studio stuck in setup (setup report, Mac, 109) had passed it and made 4 style studies, then its
# graphics browser check failed. On a full disk the launch stops at "ENOSPC ... mkdtemp" (tested on a full volume),
# and setup said "Press Fix it to reinstall it": Fix it downloaded ~115 MB onto that full disk and failed again.

ENOSPC = ("BrowserType.launch: ENOSPC: no space left on device, mkdtemp "
          "'/var/folders/xy/T/playwright-artifacts-YVLpKr'")
TIMEOUT = "BrowserType.launch: Timeout 30000ms exceeded.\nCall log:\n  - <launching> /x/chrome-headless-shell"


@pytest.mark.parametrize("error,why", [
    (ENOSPC, "disk_full"),
    (OSError(28, "No space left on device"), "disk_full"),
    ("[WinError 112] There is not enough space on the disk", "disk_full"),
    (OSError(24, "Too many open files"), "too_many_files"),
    ("spawn EMFILE", "too_many_files"),
    (TIMEOUT, "timeout"),
    ("BrowserType.launch: Executable doesn't exist at /x\nPlease run: playwright install", "missing"),
    ("Target page, context or browser has been closed", "wont_start"),
    ("Failed to install browsers\nError: ENOSPC: no space left on device\nrun: playwright install", "disk_full"),
])
def test_what_stops_the_graphics_browser_has_a_short_code(error, why):
    assert cr_studio.browser_trouble(error) == why


def test_a_full_disk_says_free_space_and_never_offers_a_reinstall(monkeypatch):
    launches = fake_playwright(monkeypatch, launch_error=ENOSPC)
    r = cr_studio.graphics_ready()
    assert r["browser"] is False and r["browser_why"] == "disk_full"
    assert "disk is full" in r["browser_reason"] and "Free a few GB" in r["browser_reason"]
    assert "Reinstalling it won't help" in r["browser_reason"] and "Press Fix it" not in r["browser_reason"]
    assert len(launches) == 1, "a full disk isn't tried twice"
    assert "disk_full" in cr_studio.BROWSER_NOT_A_REINSTALL


def test_a_launch_that_times_out_once_is_tried_again_before_it_counts(monkeypatch):
    launches = fake_playwright(monkeypatch, launch_errors=[TIMEOUT, None])
    assert cr_studio.graphics_ready()["browser"] is True and len(launches) == 2
    launches = fake_playwright(monkeypatch, launch_errors=[TIMEOUT, TIMEOUT])
    r = cr_studio.graphics_ready()
    assert r["browser"] is False and r["browser_why"] == "timeout" and len(launches) == 2
    assert "took too long to start" in r["browser_reason"] and "Check again" in r["browser_reason"]


def test_too_many_open_files_says_restart_studio(monkeypatch):
    fake_playwright(monkeypatch, launch_error="OSError: [Errno 24] Too many open files")
    r = cr_studio.graphics_ready()
    assert r["browser_why"] == "too_many_files" and "open Studio again" in r["browser_reason"]


def test_a_browser_that_wont_start_still_offers_the_reinstall(monkeypatch):
    fake_playwright(monkeypatch, launch_error="Target page, context or browser has been closed")
    r = cr_studio.graphics_ready()
    assert r["browser_why"] == "wont_start" and "Press Fix it to reinstall it" in r["browser_reason"]
    assert "wont_start" not in cr_studio.BROWSER_NOT_A_REINSTALL
    fake_playwright(monkeypatch, exe_exists=False)
    assert cr_studio.graphics_ready()["browser_why"] == "missing"


def test_fix_it_on_a_full_disk_downloads_nothing_and_says_why(monkeypatch):
    ran = []
    monkeypatch.setattr(cr_studio.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    fake_playwright(monkeypatch, launch_error=ENOSPC)
    cr_studio.BROWSER_FIX.update(state="idle", tail="")
    cr_studio.fix_graphics_browser()
    fix = finish_fix()
    assert fix["state"] == "failed" and "disk is full" in fix["tail"] and ran == []


def test_fix_it_on_a_browser_that_draws_now_downloads_nothing(monkeypatch):
    ran = []
    monkeypatch.setattr(cr_studio.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    fake_playwright(monkeypatch)
    cr_studio.BROWSER_FIX.update(state="idle", tail="")
    cr_studio.fix_graphics_browser()
    assert finish_fix() == {"state": "done", "tail": ""} and ran == []


def test_an_install_that_fills_the_disk_says_so_instead_of_its_log(monkeypatch):
    monkeypatch.setattr(cr_studio.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=1, stdout="Downloading Chromium Headless Shell 141.0 ...", stderr="Error: ENOSPC: no space left on device, write"))
    fake_playwright(monkeypatch, exe_exists=False)
    cr_studio.BROWSER_FIX.update(state="idle", tail="")
    cr_studio.fix_graphics_browser()
    fix = finish_fix()
    assert fix["state"] == "failed" and fix["tail"].startswith("This computer's disk is full")


def test_readiness_carries_the_code_and_setup_offers_check_again_for_it():
    import cr_readiness
    row = cr_readiness.item("browser", "Graphics browser", False, "full", "disk_full")
    assert row["code"] == "disk_full" and "code" not in cr_readiness.item("browser", "Graphics browser", True, "", "x")
    assert "code" not in cr_readiness.item("python", "Python", False, "Install Python")
    page = (Path(__file__).resolve().parents[1] / "local_runtime" / "cr_setup.html").read_text(encoding="utf-8")
    row_js = page.split("if(!data.browser){")[1].split("text.append(fix)")[0]
    assert "data.can_fix?.browser===false" in row_js and "Check again" in row_js and "refresh()" in row_js
    assert "row.classList.add('wide')" in row_js, "laid out like the FFmpeg and Node rows"
    assert "fixBrowser(fix,note)" in row_js, "a missing browser keeps its Fix it"
