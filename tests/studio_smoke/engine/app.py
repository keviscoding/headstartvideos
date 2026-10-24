"""A stand-in for the private engine's app.py, for the Studio smoke test (tests/studio_smoke/smoke.py).

The real engine is private: it never goes into this public repository or its CI. Studio's own code (cr_studio and the
adapters it installs) wraps the engine's Flask app and its make_video module; this has just enough of both for Studio
to start and answer its setup, status and diagnostics pages. It makes no videos."""
import threading
from pathlib import Path

from flask import Flask, jsonify

import features                 # the engine's modules, as Studio reaches them (studio.features, studio.mv...)
import make_video as mv
import styles
import timeline

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
app = Flask(__name__)
LOCK = threading.RLock()
RUNNING = {"slug": None}
QUEUE, HISTORY = [], []
JOBS, PRODUCT_BUILD, FARM = {}, {}, {}


@app.get("/")
def home():
    return "<!doctype html><html><head><meta charset='utf-8'><title>Studio</title></head><body>Studio</body></html>"


@app.post("/api/run")
def api_run():
    return jsonify(queued=[])


@app.get("/api/channels")
def api_channels():
    return jsonify(channels=[])


def _run_spec(spec):
    raise RuntimeError("The smoke test's stand-in engine makes no videos.")


def _ensure_dispatcher():
    pass


def _enqueue_tl_render(job, name):
    pass


def _tl_busy():
    return False
