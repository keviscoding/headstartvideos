"""A stand-in for the private engine's app.py, for the Studio smoke test (tests/studio_smoke/smoke.py).

The real engine is private: it never goes into this public repository or its CI. Studio's own code (cr_studio and the
adapters it installs) wraps the engine's Flask app and its make_video module; this has just enough of both for Studio
to start and answer its setup, status and diagnostics pages. It makes no videos."""
import re
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
SAFE = re.compile(r"[a-z0-9][a-z0-9._-]{0,120}")
# two recipes of the stand-in's own: a story, and one whose videos are 3D scenes (the workspace's 3D options)
RECIPES = [{"name": "smoke-story", "label": "Smoke story", "desc": "A stand-in story recipe.", "tab": "production",
            "features": {"offered": ["motion", "vox"], "on": ["motion"]}},
           {"name": "smoke-3d", "label": "Smoke 3D", "desc": "A stand-in recipe with 3D scenes.", "tab": "3d",
            "features": {"offered": ["threed", "sound"], "on": ["threed"]}}]
FEATURE_KEYS = ["motion", "vox", "threed", "sound"]


@app.get("/")
def home():
    return "<!doctype html><html><head><meta charset='utf-8'><title>Studio</title></head><body>Studio</body></html>"


@app.post("/api/run")
def api_run():
    return jsonify(queued=[])


@app.get("/api/channels")
def api_channels():
    return jsonify(channels=RECIPES, dlc={}, features=[{"key": key, "label": key} for key in FEATURE_KEYS],
                   caps=features.capabilities(), prices={})


@app.get("/api/queue")
def api_queue():
    return jsonify(jobs=[], busy=False)


@app.get("/api/jobs")
def api_jobs():
    return jsonify(jobs=[], platform="")


def _job(slug):
    return OUT / "smoke-3d" / "_work" / slug


def _run_spec(spec):
    raise RuntimeError("The smoke test's stand-in engine makes no videos.")


def _ensure_dispatcher():
    pass


def _enqueue_tl_render(job, name):
    pass


def _tl_busy():
    return False
