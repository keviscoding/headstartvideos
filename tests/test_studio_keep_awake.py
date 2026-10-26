"""The computer stays awake while a video worker runs, and a sleep that stops Claude says so (sweep, 2026-10-26).

Three FOLIO stages on the dev Studio ended with Claude Code's "API Error: Your computer went to sleep mid-response"
(the laptop's lid was closed): reported as "Claude could not finish this stage", with no hint why. Studio kept the
computer awake only for editor work, never for a video.
"""
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_awake  # noqa: E402
import cr_claude  # noqa: E402
import cr_video_cancel  # noqa: E402


class Child:
    def __init__(self, pid=4242):
        self.pid, self.stopped = pid, False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


def test_a_mac_runs_caffeinate_for_the_worker_and_stops_it_after():
    started = []

    def popen(command, **kwargs):
        started.append(command)
        started.append(Child(1))
        return started[-1]

    held = cr_awake.hold(Child(4242), platform="darwin", popen=popen)
    assert started[0] == ["/usr/bin/caffeinate", "-i", "-s", "-w", "4242"]
    held.release()
    held.release()                                   # twice is harmless
    assert started[1].stopped


def test_windows_asks_for_the_system_to_stay_on_and_clears_it_after():
    calls = []
    kernel32 = SimpleNamespace(SetThreadExecutionState=lambda flags: calls.append(flags) or 0x80000000)
    held = cr_awake.hold(Child(), platform="win32", kernel32=kernel32)
    assert calls == [cr_awake.ES_CONTINUOUS | cr_awake.ES_SYSTEM_REQUIRED]
    held.release()
    assert calls[-1] == cr_awake.ES_CONTINUOUS


def test_no_way_to_stay_awake_never_stops_a_video():
    def broken(*a, **k):
        raise OSError("no caffeinate here")

    cr_awake.hold(Child(), platform="darwin", popen=broken).release()
    cr_awake.hold(Child(), platform="linux").release()
    refused = SimpleNamespace(SetThreadExecutionState=lambda flags: 0)
    cr_awake.hold(Child(), platform="win32", kernel32=refused).release()


def test_the_real_caffeinate_ends_with_the_worker():
    if sys.platform != "darwin":
        return
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    held = cr_awake.hold(worker)
    try:
        out = subprocess.run(["pgrep", "-f", f"caffeinate -i -s -w {worker.pid}"], stdout=subprocess.PIPE,
                             encoding="utf-8", timeout=10).stdout
        assert out.strip(), "caffeinate holds the computer awake for the worker"
    finally:
        worker.kill()
        worker.wait(timeout=10)
        held.release()


def fixture_studio(tmp_path):
    (tmp_path / "app.py").write_text("# isolated original source fixture", encoding="utf-8")
    return SimpleNamespace(LOCK=threading.Lock(), QUEUE=[], JOBS={}, HISTORY=[], RUNNING={"slug": None})


def test_a_video_worker_is_kept_awake_from_start_to_end(tmp_path):
    events = []

    class Worker:
        pid, returncode = 99, 0

        def poll(self):
            events.append("poll")
            return 0

    def awake(process):
        events.append(("hold", process.pid))
        return SimpleNamespace(release=lambda: events.append("release"))

    studio = fixture_studio(tmp_path)
    manager = cr_video_cancel.VideoWorkers(studio, tmp_path, popen=lambda *a, **k: Worker(), awake=awake)
    spec = {"slug": "a-video--12345678", "title": "A video", "cr_video_run_id": "video_" + "c" * 24}
    manager.run(spec, "crv_" + "a" * 24)
    assert events[0] == ("hold", 99) and events.count("release") == 1
    assert events.index("release") > events.index("poll"), "released once the worker has ended"


def test_a_stand_in_worker_spawns_nothing(tmp_path):
    manager = cr_video_cancel.VideoWorkers(fixture_studio(tmp_path), tmp_path, popen=lambda *a, **k: None)
    assert manager.awake is None
    assert cr_video_cancel.VideoWorkers(fixture_studio(tmp_path), tmp_path).awake is cr_awake.hold


def test_a_sleep_that_stopped_claude_says_so():
    stdout = ('{"type":"result","subtype":"success","is_error":true,"result":"API Error: Your computer went to sleep '
              'mid-response. The response above may be incomplete.","terminal_reason":"api_error"}')
    code, message = cr_claude.failure_info(stdout, "")
    assert code == "network_interrupted" and "went to sleep" in message and "resume" in message
    assert cr_claude.failure_info("Error: something else", "")[0] == "cli_failed"
