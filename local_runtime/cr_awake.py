"""Keep this computer from idle-sleeping while a video worker runs (recipe sweep, 2026-10-26).

A computer that idle-sleeps in the middle of a video stops it: Claude Code ends the stage it was answering with "API
Error: Your computer went to sleep mid-response" (three FOLIO stages on the dev Studio, 2026-10-26), and media tasks
polled across the sleep come back uncertain. A 10-minute video takes from minutes to a couple of hours, longer than
most computers' idle timer, and Studio kept a computer awake only for editor work (cr_studio.CreativeActivity).

Mac: `caffeinate -i -s -w <worker>` (no idle sleep, and no system sleep on power) ends by itself with the worker.
Windows: SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) on the thread that waits for the worker, cleared
when the worker ends. Neither changes a setting, keeps the display on or stops a sleep the creator chooses (closing
a laptop's lid or pressing Sleep still sleeps). Elsewhere it does nothing.
"""
from __future__ import annotations

import subprocess
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class Hold:
    def __init__(self, release=None):
        self._release = release

    def release(self):
        release, self._release = self._release, None
        if release is not None:
            try:
                release()
            except Exception:                    # noqa: BLE001 - staying awake is a courtesy, never a reason to stop
                pass


def hold(process, *, platform=None, popen=None, kernel32=None) -> Hold:
    """Keep the computer awake while the child `process` runs; call .release() when it has ended (on Windows, from
    the thread that called hold)."""
    platform = platform or sys.platform
    try:
        if platform == "darwin":
            awake = (popen or subprocess.Popen)(["/usr/bin/caffeinate", "-i", "-s", "-w", str(process.pid)],
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL)

            def stop():
                if awake.poll() is None:
                    awake.terminate()
                    awake.wait(timeout=2)
            return Hold(stop)
        if platform == "win32":
            if kernel32 is None:
                import ctypes
                kernel32 = ctypes.windll.kernel32
            if not kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                return Hold()
            return Hold(lambda: kernel32.SetThreadExecutionState(ES_CONTINUOUS))
    except Exception:                            # noqa: BLE001 - no caffeinate, no ctypes: the video still runs
        return Hold()
    return Hold()
