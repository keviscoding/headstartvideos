"""Time a narration with Whisper the way a Studio video worker does (part of tests/studio_smoke/smoke.py).

Run with the Studio's own Python, from the Studio's folder:  python narration_worker.py NARRATION.mp3 CAPTIONS.srt TEXT

It starts the worker the way Studio does (cr_video_cancel.worker_options: its own process group, no input,
KMP_DUPLICATE_LIB_OK), and that worker loads what a video worker loads, then Whisper's base model on the CPU (int8),
and times the narration through Studio's own cr_media._time_narration, which writes the captions. A worker that dies
without a word is explained with Studio's own crash_message, as Studio would show it."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.cwd()
sys.path.insert(0, str(HOME))


def parent(mp3, srt, text):
    import cr_video_cancel
    log_path = Path(srt).with_name("worker.log")
    with open(log_path, "ab") as log:
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", mp3, srt, text],
                                  stdout=log, stderr=log, **cr_video_cancel.worker_options(HOME))
        try:
            code = worker.wait(timeout=900)
        except subprocess.TimeoutExpired:
            worker.kill()
            code = worker.wait()
    if code:
        print(json.dumps({"ok": False, "exit_code": code, "why": cr_video_cancel.crash_message(log_path, code)}))
        return 1
    print(json.dumps({"ok": True, "exit_code": 0}))
    return 0


def worker(mp3, srt, text):
    started = time.monotonic()
    # what cr_worker.py loads before the engine, then the engine's own libraries that bring native threads
    import cr_workers, cr_bridge, cr_claude, cr_media, cr_render, cr_studio, cr_readiness  # noqa: F401,E401
    import numpy  # noqa: F401
    import cv2  # noqa: F401
    import make_video as engine                     # the smoke test's stand-in for the private engine
    cr_media.install(engine)                        # Studio's own narration hooks, as a worker installs them
    print("worker: KMP_DUPLICATE_LIB_OK=%s" % os.environ.get("KMP_DUPLICATE_LIB_OK"), flush=True)
    engine._get_whisper()
    loaded = time.monotonic()
    cr_media._time_narration(engine, Path(mp3), Path(srt), text)
    print("worker: model ready in %.1f s, narration timed in %.1f s" % (loaded - started, time.monotonic() - loaded),
          flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "--worker":
        worker(*sys.argv[2:5])
    else:
        raise SystemExit(parent(*sys.argv[1:4]))
