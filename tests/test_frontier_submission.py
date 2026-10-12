"""An already-running dispatcher only sees fully owned original queue specs."""
import ast
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace

from flask import Flask, jsonify, request
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_submission


PROJECT = "crv_" + "a" * 24


def studio_with_original(queue=None):
    app = Flask("isolated-original-submission")
    studio = SimpleNamespace(app=app, QUEUE=queue if queue is not None else [],
                             LOCK=threading.Lock(), JOBS={})
    observed = []
    dispatches = []
    studio._ensure_dispatcher = lambda: dispatches.append(True)
    namespace = {"request": request, "jsonify": jsonify, "QUEUE": studio.QUEUE,
                 "JOBS": studio.JOBS, "LOCK": studio.LOCK,
                 "_ensure_dispatcher": studio._ensure_dispatcher,
                 "observe_live": lambda: observed.append(list(studio.QUEUE))}
    # Like the pinned function, the queue and dispatcher are module globals.
    exec('''
def api_run(*, title_field="title"):
    data = request.get_json(force=True)
    if not data.get(title_field):
        return jsonify(error="Original validation refused this title."), 422, {"X-Original": "validation"}
    queued = []
    for title in data[title_field].splitlines():
        slug = title.strip().lower()
        JOBS[slug] = {"state": "queued", "title": title}
        with LOCK:
            QUEUE.append({"slug": slug, "title": title, "steps": data["steps"]})
        observe_live()
        queued.append({"slug": slug, "title": title})
    _ensure_dispatcher()
    return jsonify(queued=queued)
''', namespace)
    app.add_url_rule("/api/run", "api_run", namespace["api_run"], methods=["POST"])
    return studio, observed, dispatches


def test_existing_dispatcher_consumes_only_owned_specs_at_atomic_publication():
    published = threading.Event()
    consumed = threading.Event()
    stop = threading.Event()
    received = []
    states = []

    class PublishedQueue(list):
        def extend(self, items):
            assert studio.LOCK.locked()
            assert all(item["cr_project_id"] == PROJECT for item in items)
            assert states == ["saved before publication"]
            super().extend(items)
            published.set()

    studio, observed, _ = studio_with_original(PublishedQueue())

    # This dispatcher is already running before api_run. It wakes on the exact
    # publication boundary, rather than relying on thread timing or sleeps.
    def permanent_dispatcher():
        while not stop.is_set():
            if not published.wait(1):
                continue
            with studio.LOCK:
                while studio.QUEUE:
                    received.append(studio.QUEUE.pop(0))
                published.clear()
            consumed.set()

    worker = threading.Thread(target=permanent_dispatcher, daemon=True)
    worker.start()
    studio._ensure_dispatcher = lambda: consumed.wait(2) or pytest.fail("Existing dispatcher did not consume publication")
    try:
        with studio.app.test_request_context(json={"project_id": PROJECT}):
            response = cr_submission.submit(studio, {"title": "First\nSecond", "steps": ["video"]}, PROJECT,
                before_publish=lambda: states.append("saved before publication"))
            assert response.status_code == 200
            assert request.get_json() == {"project_id": PROJECT}
        assert not studio.QUEUE
        assert [item["slug"] for item in received] == ["first", "second"]
        assert all(item["cr_project_id"] == PROJECT for item in received)
        assert observed == [[], []]
        assert set(studio.JOBS) == {"first", "second"}
        assert studio.app.view_functions["api_run"].__globals__["QUEUE"] is studio.QUEUE
    finally:
        stop.set()
        published.set()
        worker.join(2)
    assert not worker.is_alive()


def test_unrelated_queued_job_keeps_its_existing_project():
    old = {"slug": "old", "cr_project_id": "crv_" + "b" * 24}
    studio, observed, dispatches = studio_with_original([old])
    with studio.app.test_request_context(json={}):
        response = cr_submission.submit(studio, {"title": "New", "steps": ["video"]}, PROJECT)
    assert response.status_code == 200 and dispatches == [True]
    assert studio.QUEUE[0] is old and old["cr_project_id"] == "crv_" + "b" * 24
    assert studio.QUEUE[1]["cr_project_id"] == PROJECT
    assert observed == [[old]]


def test_original_validation_response_is_preserved_without_publication():
    studio, _, dispatches = studio_with_original()
    before_publish = []
    with studio.app.test_request_context(json={"project_id": PROJECT}):
        response = cr_submission.submit(studio, {"steps": ["video"]}, PROJECT,
                                        before_publish=lambda: before_publish.append(True))
        assert request.get_json() == {"project_id": PROJECT}
    assert response.status_code == 422 and response.headers["X-Original"] == "validation"
    assert response.get_json() == {"error": "Original validation refused this title."}
    assert not studio.QUEUE and not studio.JOBS and not dispatches and not before_publish


def test_failed_local_receipt_cannot_publish_or_dispatch_a_job():
    studio, _, dispatches = studio_with_original()
    def failing_receipt():
        raise OSError("The local receipt could not be saved.")
    with studio.app.test_request_context(json={}):
        with pytest.raises(OSError, match="receipt"):
            cr_submission.submit(studio, {"title": "New", "steps": ["video"]}, PROJECT,
                                 before_publish=failing_receipt)
    assert not studio.QUEUE and not dispatches


def test_invalid_owned_id_does_not_call_original_validation():
    studio, _, dispatches = studio_with_original()
    with studio.app.test_request_context(json={}):
        with pytest.raises(ValueError, match="valid saved CR project"):
            cr_submission.submit(studio, {"title": "New", "steps": ["video"]}, "other-project")
    assert not studio.QUEUE and not studio.JOBS and not dispatches


def test_exact_installed_frontier_queue_validation_and_normalization_are_kept():
    path = Path("/Users/workcomputer/Downloads/ChannelRecipe studio/app.py")
    if not path.is_file():
        pytest.skip("The owner's pinned private engine is not installed on this computer.")
    definition = next(node for node in ast.parse(path.read_text()).body
                      if isinstance(node, ast.FunctionDef) and node.name == "api_run")
    definition.decorator_list = []
    studio, _, dispatches = studio_with_original()
    namespace = {"request": request, "jsonify": jsonify, "re": re,
        "QUEUE": studio.QUEUE, "JOBS": studio.JOBS, "LOCK": studio.LOCK,
        "_ensure_dispatcher": studio._ensure_dispatcher,
        "VALID_STEPS": {"script", "voiceover", "pexels", "images", "video", "thumbnail"},
        "mv": SimpleNamespace(prompts=SimpleNamespace(PROMPT_SETS={"3d": {}}), slugify=lambda title: title.lower()),
        "features": SimpleNamespace(BY_KEY={"threed": {}}), "avatar": SimpleNamespace(MAX_COUNT=4),
        "_char_rel": lambda value: "", "_threed_on": lambda style, features: style == "3d"}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), "exec"), namespace)
    studio.app.view_functions["api_run"] = namespace["api_run"]
    payload = {"title": "Example", "style": "3d", "steps": ["video", "invalid"], "minutes": 999,
               "mix": [-1, 30, 70], "burn_subs": False, "auto_style": False,
               "features": {"threed": True, "unknown": True}, "extra": " Original creative direction "}
    with studio.app.test_request_context(json={}):
        response = cr_submission.submit(studio, payload, PROJECT)
    assert response.status_code == 200 and dispatches == [True]
    spec = studio.QUEUE[0]
    assert spec["cr_project_id"] == PROJECT and spec["steps"] == ["video"]
    assert spec["minutes"] == 60 and spec["mix"] == (0, 30, 70)
    assert spec["auto_style"] is False and spec["burn_subs"] is False
    assert spec["features"] == {"threed": True} and spec["extra"] == "Original creative direction"
    assert studio.JOBS["example"]["threed"] and studio.JOBS["example"]["state"] == "queued"
