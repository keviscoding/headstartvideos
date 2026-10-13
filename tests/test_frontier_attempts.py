"""New clicks own separate originals; only explicit resume retains media."""
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

from flask import Flask, jsonify, request, send_file
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_attempts
import cr_studio
import cr_submission
from tests.test_frontier_activity import runtime, timeline

INSTALL_SUBMISSION = cr_submission.install
SOURCE = Path("/Users/workcomputer/Downloads/ChannelRecipe studio")
TITLE = "Same title, same script"
SCRIPT = "The user's exact narration stays unchanged."
PROJECT = "crv_" + "a" * 24


def original_functions(filename, names, namespace):
    path = SOURCE / filename
    if not path.is_file():
        pytest.skip("The owner's original private studio is not installed here.")
    definitions = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            definitions.append(node)
    assert len(definitions) == len(names)
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)


@pytest.fixture
def original_engine(tmp_path):
    engine = ModuleType("original_storage_helpers")
    engine.__dict__.update(Path=Path, re=re, json=json, hashlib=hashlib,
                          shutil=shutil, os=__import__("os"), time=time,
                          OUTPUT_ROOT=tmp_path / "output", WORK_DIR="_work",
                          STYLE_INFO={}, log=lambda *args: None)
    original_functions("make_video.py", {
        "slugify", "style_folder", "_is_old_job", "find_job", "job_dir",
        "_title_of", "main_dir", "list_jobs", "_published", "final_video",
        "_move", "_unpublish", "_republish_if_left", "youtube_filename",
        "publish_named_copy",
    }, engine.__dict__)
    return engine


def original_submission(engine):
    studio = SimpleNamespace(app=Flask("original_attempt_submission"), mv=engine,
                             QUEUE=[], JOBS={}, LOCK=threading.Lock())
    engine.prompts = SimpleNamespace(PROMPT_SETS={"documentary": {}, "other": {}})
    studio._ensure_dispatcher = lambda: None
    studio._cr_video_workers = SimpleNamespace()
    ns = dict(request=request, jsonify=jsonify, re=re, QUEUE=studio.QUEUE,
              JOBS=studio.JOBS, LOCK=studio.LOCK, mv=engine,
              _ensure_dispatcher=studio._ensure_dispatcher,
              VALID_STEPS={"script", "voiceover", "pexels", "images", "video", "thumbnail"},
              features=SimpleNamespace(BY_KEY={}), avatar=SimpleNamespace(MAX_COUNT=4),
              _char_rel=lambda value: "", _threed_on=lambda *args: False)
    original_functions("app.py", {"api_run"}, ns)
    studio.app.add_url_rule("/api/run", "api_run", ns["api_run"], methods=["POST"])
    INSTALL_SUBMISSION(studio)
    return studio


def create(studio, **changes):
    payload = {"title": TITLE, "style": "documentary", "script": SCRIPT,
               "steps": ["script", "voiceover", "images", "video"], **changes}
    response = studio.app.test_client().post("/api/run", json=payload)
    assert response.status_code == 200, response.get_json()
    return studio.QUEUE[-1], response.get_json()["queued"][-1]


def publish(engine, spec, content, project_id=PROJECT):
    with cr_attempts.bind(engine, spec):
        job = engine.job_dir(spec["title"], spec["style"])
        job.mkdir(parents=True, exist_ok=True)
        cr_attempts.reserve_output(engine, job, spec)
        (job / "title.txt").write_text(spec["title"] + "\n")
        (job / "style.txt").write_text(spec["style"] + "\n")
        (job / "script.txt").write_text(spec["script_text"])
        (job / "cr-project.json").write_text(json.dumps({"id": project_id,
            "brain_mode": "claudecode", "job_spec": spec}))
        (job / "video.mp4").write_bytes(content)
        final = engine.publish_named_copy(job, spec["title"])
    return job, final


def test_repeated_create_same_title_script_has_independent_caches_and_published_videos(original_engine):
    engine = original_engine
    studio = original_submission(engine)
    first, first_reply = create(studio)
    first_job, first_video = publish(engine, first, b"first finished video")
    original_receipt = (first_job / "published.json").read_bytes()
    (first_job / "astro_scenes.json").write_text('{"saved":"first creative work"}')
    second, second_reply = create(studio)
    assert first["slug"] != second["slug"]
    assert first["cr_video_run_id"] != second["cr_video_run_id"]
    assert first_reply["slug"] == first["slug"] and second_reply["slug"] == second["slug"]
    assert first["title"] == second["title"] == TITLE
    assert first["script_text"] == second["script_text"] == SCRIPT
    with cr_attempts.bind(engine, second):
        second_job = engine.job_dir(TITLE, "documentary")
        assert not engine.final_video(second_job).exists()
        assert not (second_job / "astro_scenes.json").exists()
    second_job, second_video = publish(engine, second, b"second finished video", "crv_" + "b" * 24)
    assert first_job != second_job and first_video != second_video
    assert first_video.read_bytes() == b"first finished video"
    assert second_video.read_bytes() == b"second finished video"
    assert (first_job / "published.json").read_bytes() == original_receipt
    assert set(engine.list_jobs()) == {first_job, second_job}
    assert engine.find_job(first["slug"]) == first_job
    assert engine.find_job(second["slug"]) == second_job
    assert engine.final_video(first_job) == first_video and engine.final_video(second_job) == second_video


def test_duplicate_titles_in_one_new_batch_have_separate_ui_and_cancel_identities(original_engine):
    studio = original_submission(original_engine)
    create(studio, title=TITLE + "\n" + TITLE)
    first, second = studio.QUEUE
    assert first["slug"] != second["slug"]
    assert set(studio.JOBS) == {first["slug"], second["slug"]}
    assert first["cr_video_run_id"] != second["cr_video_run_id"]
    assert all(row["title"] == TITLE for row in studio.JOBS.values())


def test_explicit_saved_project_resume_keeps_exact_cache_and_publication(original_engine):
    engine = original_engine
    studio = original_submission(engine)
    first, _ = create(studio)
    job, final = publish(engine, first, b"completed original video")
    (job / "astro_scenes.json").write_text('{"kept":"creative cache"}')
    saved = {p.name: p.read_bytes() for p in job.iterdir() if p.is_file()}
    with studio.app.test_request_context(json={}):
        response = cr_submission.submit(studio, {"title": TITLE, "style": "documentary",
            "script": SCRIPT, "steps": ["video"]}, PROJECT)
    assert response.status_code == 200
    resumed = studio.QUEUE[-1]
    assert resumed["slug"] == first["slug"]
    assert resumed["cr_project_id"] == PROJECT
    assert resumed["cr_video_run_id"] != first["cr_video_run_id"]
    with cr_attempts.bind(engine, resumed):
        assert engine.job_dir(TITLE, "documentary") == job
        assert engine.final_video(job).read_bytes() == b"completed original video"
        assert (job / "astro_scenes.json").read_text() == '{"kept":"creative cache"}'
    assert {p.name: p.read_bytes() for p in job.iterdir() if p.is_file()} == saved
    assert final.read_bytes() == b"completed original video"


def test_explicit_resume_of_legacy_job_keeps_original_slug_and_style(original_engine):
    engine = original_engine
    studio = original_submission(engine)
    legacy = engine.job_dir(TITLE, "documentary")
    legacy.mkdir(parents=True)
    (legacy / "cr-project.json").write_text(json.dumps({"id": PROJECT}))
    (legacy / "title.txt").write_text(TITLE + "\n")
    (legacy / "script.txt").write_text(SCRIPT)
    with studio.app.test_request_context(json={}):
        response = cr_submission.submit(studio, {"title": TITLE, "style": "documentary",
            "script": SCRIPT, "steps": ["video"]}, PROJECT)
    assert response.status_code == 200
    resumed = studio.QUEUE[-1]
    assert resumed["slug"] == legacy.name
    with cr_attempts.bind(engine, resumed):
        assert engine.job_dir(TITLE, "documentary") == legacy
        assert (legacy / "script.txt").read_text() == SCRIPT


def test_active_attempt_binding_is_seen_by_parallel_original_media_threads_and_restored(original_engine):
    engine = original_engine
    studio = original_submission(engine)
    old = engine.job_dir(TITLE, "documentary")
    old.mkdir(parents=True)
    (old / "script.txt").write_text("An older project must not leak into this one.")
    first, _ = create(studio)
    original = engine.job_dir
    with cr_attempts.bind(engine, first):
        target = engine.job_dir(TITLE, "documentary")
        target.mkdir(parents=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(lambda _: engine.job_dir(TITLE, "documentary"), range(8)))
        assert paths == [target] * 8 and target != old
        assert engine.job_dir("Unrelated project", "documentary") == original("Unrelated project", "documentary")
        assert engine.job_dir(TITLE, "other") == target
    assert engine.job_dir is original
    assert engine.job_dir(TITLE, "documentary") == old


def test_new_different_style_for_same_title_cannot_find_old_video(original_engine):
    engine = original_engine
    studio = original_submission(engine)
    first, _ = create(studio)
    old_job, old_video = publish(engine, first, b"saved documentary")
    second, _ = create(studio, style="other")
    with cr_attempts.bind(engine, second):
        target = engine.job_dir(TITLE, "other")
        assert target != old_job and target.parent.parent.name == "other"
        assert not engine.final_video(target).exists()
    assert old_video.read_bytes() == b"saved documentary"


def test_ownerless_existing_title_folder_cannot_be_overwritten_by_fresh_create(original_engine):
    engine = original_engine
    studio = original_submission(engine)
    title_home = engine.OUTPUT_ROOT / "documentary" / Path(engine.youtube_filename(TITLE)).stem
    title_home.mkdir(parents=True)
    old_video = title_home / engine.youtube_filename(TITLE)
    old_video.write_bytes(b"legacy published video without an owner marker")
    spec, _ = create(studio)
    _, new_video = publish(engine, spec, b"newly generated video")
    assert new_video != old_video
    assert old_video.read_bytes() == b"legacy published video without an owner marker"
    assert new_video.read_bytes() == b"newly generated video"


def test_conflicting_project_folders_are_not_silently_resumed(original_engine):
    engine = original_engine
    for name in ("first", "second"):
        job = engine.OUTPUT_ROOT / "documentary" / engine.WORK_DIR / name
        job.mkdir(parents=True)
        (job / "cr-project.json").write_text(json.dumps({"id": PROJECT}))
    with pytest.raises(RuntimeError, match="conflicting"):
        cr_attempts.owned_slug(engine, PROJECT)


def test_original_library_downloads_and_timeline_lookup_address_each_attempt(original_engine):
    engine = original_engine
    studio = original_submission(engine)
    first, _ = create(studio)
    first_job, _ = publish(engine, first, b"first movie bytes")
    second, _ = create(studio)
    second_job, _ = publish(engine, second, b"second movie bytes", "crv_" + "b" * 24)
    ns = dict(mv=engine, OUT=engine.OUTPUT_ROOT, SAFE=re.compile(r"^[\w.\-]+$"),
              jsonify=jsonify, send_file=send_file, sys=sys, os=__import__("os"),
              Path=Path)
    original_functions("app.py", {"_job", "_tl_job", "api_result", "api_jobs", "f_video", "_send", "f_script"}, ns)
    app = Flask("original_attempt_downloads")
    app.add_url_rule("/api/jobs", "api_jobs", ns["api_jobs"])
    app.add_url_rule("/api/result/<slug>", "api_result", ns["api_result"])
    app.add_url_rule("/f/video/<slug>", "f_video", ns["f_video"])
    app.add_url_rule("/f/script/<slug>", "f_script", ns["f_script"])
    client = app.test_client()
    jobs = client.get("/api/jobs").get_json()["jobs"]
    assert {row["slug"] for row in jobs} == {first["slug"], second["slug"]}
    assert all(row["title"] == TITLE for row in jobs)
    for spec, expected, expected_job in ((first, b"first movie bytes", first_job),
                                         (second, b"second movie bytes", second_job)):
        video = client.get("/f/video/" + spec["slug"])
        assert video.status_code == 200 and video.data == expected
        script = client.get("/f/script/" + spec["slug"])
        assert script.status_code == 200 and script.data.decode() == SCRIPT
        result = client.get("/api/result/" + spec["slug"]).get_json()
        assert result["title"] == TITLE and result["video"] and result["script"]
        with app.app_context():
            job, error = ns["_tl_job"](spec["slug"])
            assert job == expected_job and error is None


def test_timeline_render_completion_receipt_uses_selected_same_title_attempt(runtime, original_engine, monkeypatch):
    r = runtime
    engine = original_engine
    r.studio.mv = engine
    studio = original_submission(engine)
    first, _ = create(studio)
    first_job, first_video = publish(engine, first, b"first film")
    second, _ = create(studio)
    second_job, second_video = publish(engine, second, b"second film", "crv_" + "b" * 24)
    first_marker = (first_job / "cr-project.json").read_bytes()
    r.cfg.update(brain_mode="claudecode", setup_proof={})
    events = []
    monkeypatch.setattr(cr_studio.cr_readiness, "proof", lambda *a, **kw: {})
    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", lambda *a, **kw: {"id": "crv_" + "c" * 24})
    monkeypatch.setattr(cr_studio.cr_bridge, "finish_project", lambda report: events.append(("finish", report)))
    monkeypatch.setattr(cr_studio, "output_artifacts", lambda engine, job:
        events.append(("artifacts", job)) or [{"name": "video.mp4", "sha256": "f" * 64, "media_type": "video/mp4"}])
    cells = dict(zip(r.studio._run_spec.__code__.co_freevars, r.studio._run_spec.__closure__))
    if "run_owned_spec" in cells:
        owned = cells["run_owned_spec"].cell_contents
        cells = dict(zip(owned.__code__.co_freevars, owned.__closure__))
    def rendered(spec):
        assert Path(spec["job"]) == second_job
        r.studio.JOBS[spec["slug"]].update(state="done", done=True)
        r.studio.RUNNING["slug"] = None
    monkeypatch.setattr(cells["original_run"], "cell_contents", rendered)
    spec = {"slug": second_job.name, "title": TITLE, "style": "documentary", "job": str(second_job),
            "timeline": True, "minutes": 0, "steps": ["video"]}
    r.studio.JOBS[spec["slug"]] = {"state": "queued", "lines": []}
    r.studio.RUNNING["slug"] = spec["slug"]
    r.studio._run_spec(spec)
    assert events[0] == ("artifacts", second_job)
    assert events[1][0] == "finish" and events[1][1]["status"] == "done"
    assert (first_job / "cr-project.json").read_bytes() == first_marker
    assert not (first_job / ".cr-finish.json").exists()
    assert (second_job / ".cr-finish.json").is_file()
    assert first_video.read_bytes() == b"first film" and second_video.read_bytes() == b"second film"


def test_new_create_dispatch_never_resumes_old_project_or_reads_its_finished_cache(runtime, original_engine, monkeypatch):
    r = runtime
    engine = original_engine
    r.studio.mv = engine
    studio = original_submission(engine)
    first, _ = create(studio)
    old_job, old_video = publish(engine, first, b"old video")
    old_marker = (old_job / "cr-project.json").read_bytes()
    second, _ = create(studio)
    r.cfg.update(brain_mode="claudecode", setup_proof={})
    new_id = "crv_" + "b" * 24
    begun = []
    def begin(title, style, resume_id, **kw):
        begun.append((title, style, resume_id, kw["job_spec"]["script_text"]))
        return {"id": new_id}
    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", begin)
    monkeypatch.setattr(cr_studio.cr_bridge, "project_progress", lambda *a:
        pytest.fail("A fresh Create click must not query or resume the old video project."))
    monkeypatch.setattr(cr_studio.cr_bridge, "finish_project", lambda report: None)
    cells = dict(zip(r.studio._run_spec.__code__.co_freevars, r.studio._run_spec.__closure__))
    if "run_owned_spec" in cells:
        owned = cells["run_owned_spec"].cell_contents
        cells = dict(zip(owned.__code__.co_freevars, owned.__closure__))
    def generate(spec):
        job = engine.job_dir(spec["title"], spec["style"])
        assert job != old_job and job.name == second["slug"]
        assert not engine.final_video(job).exists()
        (job / "title.txt").write_text(spec["title"] + "\n")
        (job / "script.txt").write_text(spec["script_text"])
        (job / "video.mp4").write_bytes(b"new creative attempt")
        engine.publish_named_copy(job, spec["title"])
        r.studio.JOBS[spec["slug"]].update(state="done", done=True)
        r.studio.RUNNING["slug"] = None
    monkeypatch.setattr(cells["original_run"], "cell_contents", generate)
    r.studio.JOBS[second["slug"]] = {"state": "queued", "lines": []}
    r.studio.RUNNING["slug"] = second["slug"]
    r.studio._run_spec(second)
    assert begun == [(TITLE, "documentary", None, SCRIPT)]
    assert old_video.read_bytes() == b"old video"
    assert (old_job / "cr-project.json").read_bytes() == old_marker
    fresh_job = engine.find_job(second["slug"])
    assert engine.final_video(fresh_job).read_bytes() == b"new creative attempt"
    assert json.loads((fresh_job / ".cr-finish.json").read_text())["project_id"] == new_id


def test_timeline_slug_cannot_target_another_attempts_job_or_receipts(runtime, original_engine, monkeypatch):
    r = runtime
    engine = original_engine
    r.studio.mv = engine
    r.studio.LOCK = threading.Lock()
    studio = original_submission(engine)
    first, _ = create(studio)
    first_job, first_video = publish(engine, first, b"first film")
    second, _ = create(studio)
    second_job, second_video = publish(engine, second, b"second film", "crv_" + "b" * 24)
    markers = [(job, (job / "cr-project.json").read_bytes()) for job in (first_job, second_job)]
    r.cfg.update(brain_mode="claudecode", setup_proof={})
    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", lambda *a, **kw:
        pytest.fail("A mismatched timeline must not begin paid work."))
    spec = {"slug": second_job.name, "title": TITLE, "style": "documentary", "job": str(first_job),
            "timeline": True, "minutes": 0, "steps": ["video"]}
    r.studio.JOBS[spec["slug"]] = {"state": "queued", "lines": []}
    r.studio.RUNNING["slug"] = spec["slug"]
    r.studio._run_spec(spec)
    assert r.studio.JOBS[spec["slug"]]["state"] == "error"
    assert "saved video folder" in r.studio.JOBS[spec["slug"]]["error"]
    assert r.studio.RUNNING["slug"] is None
    for job, marker in markers:
        assert (job / "cr-project.json").read_bytes() == marker
        assert not (job / ".cr-finish.json").exists()
    assert first_video.read_bytes() == b"first film" and second_video.read_bytes() == b"second film"
