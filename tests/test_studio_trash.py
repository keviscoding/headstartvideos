"""Deleting a video or a learned style moves it into Studio's trash (cr_trash), never removes it at once.

A customer (thatguygab, 2026-10-25) asked to delete videos and cloned styles. Delete says what goes and what is kept,
is refused while a video is being made, touches nothing outside Studio's own output and styles folders, and Restore
puts everything back exactly. The trash empties itself after 30 days.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from flask import Flask, jsonify, request
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_trash  # noqa: E402
import cr_workspace as workspace  # noqa: E402

BASE = "http://127.0.0.1:7860"
TOKEN = "synthetic-trash-token"


class Engine:
    """The engine's own rules for where a video lives (make_video: output/<channel>/_work/<slug> and the folder its
    finished video is published to)."""

    def __init__(self, output: Path):
        self.output = output

    def find_job(self, slug):
        return next((c for c in sorted(self.output.glob(f"*/_work/{slug}")) if c.is_dir()), None)

    def _published(self, job):
        try:
            return json.loads((Path(job) / "published.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def main_dir(self, job):
        job = Path(job)
        title = (job / "title.txt").read_text(encoding="utf-8").splitlines()[0] if (job / "title.txt").exists() else job.name
        return job.parent.parent / (self._published(job).get("folder") or title)

    def final_video(self, job):
        job = Path(job)
        if (job / "video.mp4").exists():
            return job / "video.mp4"
        name = self._published(job).get("video")
        if name and (self.main_dir(job) / name).exists():
            return self.main_dir(job) / name
        return job / "video.mp4"

    def list_jobs(self):
        return [c for c in self.output.glob("*/_work/*") if c.is_dir() and not c.name.startswith(".")]


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "studio"
    output = home / "output"
    output.mkdir(parents=True)
    (home / "styles").mkdir()
    app = Flask("trash")

    @app.get("/api/channels")
    def channels():
        return jsonify(channels=[], features=[], caps={}, prices={}, dlc={})

    @app.get("/")
    def root_page():
        return "engine"

    @app.before_request
    def token_guard():                                      # cr_studio's own guard for every POST
        if request.method == "POST" and request.headers.get("X-CR-Local-Token") != TOKEN:
            return jsonify(error="Refresh your studio page and try again."), 403

    studio = SimpleNamespace(app=app, mv=Engine(output), styles=SimpleNamespace(STYLES={}), JOBS={}, QUEUE=[],
                             RUNNING={"slug": None}, HISTORY=[], LOCK=threading.Lock())
    activity = SimpleNamespace(lock=threading.RLock(), busy=lambda: False)
    workspace.install(studio, activity, token=TOKEN, home=home)
    client = app.test_client()
    return SimpleNamespace(home=home, output=output, studio=studio, activity=activity, client=client,
                           get=lambda path: client.get(path, base_url=BASE),
                           post=lambda path, **kw: client.post(path, base_url=BASE, json={},
                                                               headers={"X-CR-Local-Token": TOKEN}, **kw))


def finished_video(env, slug="how-one-ship--abc", title="How One Ship Blocked the Suez Canal", channel="documentary"):
    job = env.output / channel / "_work" / slug
    (job / "images").mkdir(parents=True)
    (job / "title.txt").write_text(title + "\n", encoding="utf-8")
    (job / "script.txt").write_text("March 23, 2021.", encoding="utf-8")
    (job / "audio.mp3").write_bytes(b"v" * 2000)
    (job / "images" / "001.jpg").write_bytes(b"i" * 3000)
    home = env.output / channel / title
    home.mkdir(parents=True)
    (home / f"{title}.mp4").write_bytes(b"m" * 5_000_000)
    (home / f"{title} - description.txt").write_text("desc", encoding="utf-8")
    (home / "thumbnail_1.png").write_bytes(b"t")
    (home / ".channelrecipe-video").write_text(slug, encoding="utf-8")
    (job / "published.json").write_text(json.dumps({"video": f"{title}.mp4", "folder": title,
                                                    "files": [f"{title} - description.txt", "thumbnail_1.png"]}))
    return job, home


def snapshot(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def learned_style(env, name="cr_clone_stickmen", label="2D AI Stickmen"):
    path = env.home / "styles" / f"{name}.json"
    path.write_text(json.dumps({"name": name, "label": label, "cr_style_clone": {"operation_id": "op_" + "1" * 24}}))
    return path


# ── videos ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_finished_video_goes_to_the_trash_with_everything_it_made_and_comes_back_exactly(env):
    job, published = finished_video(env)
    other, other_home = finished_video(env, "another--def", "Another Video")
    voices = env.home / "uploads" / "voiceovers"
    voices.mkdir(parents=True)
    (voices / "my-take.mp3").write_bytes(b"mine")
    before = {"job": snapshot(job), "video": snapshot(published)}
    others = {"job": snapshot(other), "video": snapshot(other_home), "voices": snapshot(voices)}
    env.studio.JOBS["how-one-ship--abc"] = {"state": "done", "title": "How One Ship"}
    env.studio.HISTORY.extend(["another--def", "how-one-ship--abc"])

    plan = env.get("/cr-workspace/delete/video/how-one-ship--abc").json
    assert plan["title"] == "How One Ship Blocked the Suez Canal" and plan["finished"] is True and plan["blocked"] == ""
    assert plan["goes"][0] == "The finished video: How One Ship Blocked the Suez Canal.mp4 (5.0 MB)"
    assert plan["goes"][1] == "Its YouTube description and thumbnails" and plan["goes"][2].startswith("Its working files")
    assert "Your voice library and saved recordings" in plan["kept"] and plan["keep_days"] == 30
    assert "moves" not in plan and str(env.home) not in json.dumps(plan), "no paths on this computer"
    assert job.is_dir(), "asking what goes changes nothing"

    assert env.client.post("/cr-workspace/delete/video/how-one-ship--abc", base_url=BASE).status_code == 403
    done = env.post("/cr-workspace/delete/video/how-one-ship--abc").json
    assert done["deleted"] is True and cr_trash.ENTRY.fullmatch(done["trash_id"]) and "30 days" in done["message"]
    assert not job.exists() and not published.exists()
    assert {"job": snapshot(other), "video": snapshot(other_home), "voices": snapshot(voices)} == others
    assert "how-one-ship--abc" not in env.studio.JOBS and env.studio.HISTORY == ["another--def"]
    assert env.studio.mv.find_job("how-one-ship--abc") is None and env.studio.mv.list_jobs() == [other]
    entry = env.output / ".cr-trash" / done["trash_id"]
    assert snapshot(entry / "job") == before["job"] and snapshot(entry / "video") == before["video"]

    rows = env.get("/cr-workspace/trash").json["entries"]
    assert [(r["id"], r["kind"], r["name"], r["days_left"]) for r in rows] == [
        (done["trash_id"], "video", "How One Ship Blocked the Suez Canal", 29)]
    back = env.post(f"/cr-workspace/trash/{done['trash_id']}/restore").json
    assert back == {"restored": True, "kind": "video", "title": "How One Ship Blocked the Suez Canal",
                    "id": "how-one-ship--abc"}
    assert {"job": snapshot(job), "video": snapshot(published)} == before
    assert not entry.exists() and env.get("/cr-workspace/trash").json["entries"] == []


def test_a_stopped_video_has_only_its_working_folder(env):
    job = env.output / "stories" / "_work" / "zombie--abc"
    job.mkdir(parents=True)
    (job / "cr-project.json").write_text(json.dumps({"id": "crv_" + "1" * 24, "title": "Zombie Outbreak"}))
    plan = env.get("/cr-workspace/delete/video/zombie--abc").json
    assert plan["title"] == "Zombie Outbreak" and plan["finished"] is False and len(plan["goes"]) == 1
    assert env.post("/cr-workspace/delete/video/zombie--abc").json["deleted"]
    assert not job.exists() and (env.output / "stories" / "_work").is_dir()


@pytest.mark.parametrize("how", ["running", "queued", "engine-running", "engine-queue", "held", "worker", "editing"])
def test_a_video_being_made_or_edited_is_never_deleted(env, how):
    job, published = finished_video(env)
    slug = job.name
    if how in ("running", "queued"):
        env.studio.JOBS[slug] = {"state": how}
    elif how == "engine-running":
        env.studio.RUNNING["slug"] = slug
    elif how == "engine-queue":
        env.studio.QUEUE.append({"slug": slug, "timeline": True})
    elif how == "held":
        env.studio._cr_queue = SimpleNamespace(held=[{"slug": slug, "style": "documentary"}])
    elif how == "worker":
        env.studio._cr_video_workers = SimpleNamespace(current={"slug": slug, "run_id": "r"})
    else:
        (job / "_timeline").mkdir()
        (job / "_timeline" / "state.json").write_text(json.dumps({"tasks": [{"id": "t", "state": "working"}]}))
    plan = env.get(f"/cr-workspace/delete/video/{slug}").json
    assert plan["blocked"]
    response = env.post(f"/cr-workspace/delete/video/{slug}")
    assert response.status_code == 409 and plan["title"] in response.json["error"]
    assert job.is_dir() and published.is_dir() and not (env.output / ".cr-trash").exists()


def test_a_title_folder_shared_with_another_video_keeps_the_other_videos_files(env):
    job, published = finished_video(env)
    (published / ".channelrecipe-video").write_text("someone-else--xyz", encoding="utf-8")
    (published / "their video.mp4").write_bytes(b"theirs")
    env.post("/cr-workspace/delete/video/how-one-ship--abc")
    assert sorted(p.name for p in published.iterdir()) == [".channelrecipe-video", "their video.mp4"]
    assert not job.exists()


def test_nothing_outside_studios_output_is_ever_moved(env, tmp_path):
    outside = tmp_path / "Desktop" / "my-video"
    outside.mkdir(parents=True)
    (outside / "title.txt").write_text("Mine\n", encoding="utf-8")
    env.studio.mv.find_job = lambda slug: outside if slug == "mine" else None
    assert env.get("/cr-workspace/delete/video/mine").status_code == 404
    assert env.post("/cr-workspace/delete/video/mine").status_code == 404
    link = env.output / "documentary" / "_work" / "linked"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    env.studio.mv = Engine(env.output)
    assert env.post("/cr-workspace/delete/video/linked").status_code == 404
    assert (outside / "title.txt").exists() and link.is_symlink()
    for bad in ("..", ".cr-trash", "a%2Fb"):
        assert env.post(f"/cr-workspace/delete/video/{bad}").status_code in (404, 405)


def test_a_move_that_fails_halfway_puts_everything_back(env, monkeypatch):
    job, published = finished_video(env)
    real = cr_trash._move

    def flaky(src, dest):
        if Path(dest).name == "video":
            raise PermissionError("in use")                 # Windows: the video is open in a player
        return real(src, dest)

    monkeypatch.setattr(cr_trash, "_move", flaky)
    response = env.post("/cr-workspace/delete/video/how-one-ship--abc")
    assert response.status_code == 409 and "close it and try again" in response.json["error"]
    assert job.is_dir() and published.is_dir() and list((env.output / ".cr-trash").iterdir()) == []


def test_delete_waits_for_a_create_or_resume_in_progress(env, monkeypatch):
    monkeypatch.setattr(workspace, "TRASH_LOCK_WAIT_S", 0.3)
    job, _ = finished_video(env)
    env.activity.lock.acquire()
    held = threading.Event()

    def other_request():
        response = env.post("/cr-workspace/delete/video/how-one-ship--abc")
        held.response = response
        held.set()

    worker = threading.Thread(target=other_request)
    worker.start()
    held.wait(5)
    env.activity.lock.release()
    assert held.response.status_code == 409 and job.is_dir()


def test_a_delete_waits_out_a_moment_of_the_page_refreshing(env, monkeypatch):
    """The review (2026-10-26): the page's own refreshes take the lock briefly, so a delete waits instead of failing."""
    monkeypatch.setattr(workspace, "TRASH_LOCK_WAIT_S", 3)
    job, _ = finished_video(env)
    taken, done = threading.Event(), threading.Event()

    def refresh():                                          # another request holds the lock for a moment
        with env.activity.lock:
            taken.set()
            done.wait(0.3)
    threading.Thread(target=refresh).start()
    taken.wait(2)
    assert env.post("/cr-workspace/delete/video/how-one-ship--abc").status_code == 200 and not job.exists()


def test_another_letter_case_never_reaches_a_video_being_made(env):
    """The review (2026-10-26): Mac and Windows folders ignore case, so MY-VIDEO found my-video while it rendered."""
    job, published = finished_video(env)
    env.studio.RUNNING["slug"] = "how-one-ship--abc"
    for slug in ("HOW-ONE-SHIP--ABC", "How-One-Ship--abc"):
        assert env.get(f"/cr-workspace/delete/video/{slug}").status_code == 404
        assert env.post(f"/cr-workspace/delete/video/{slug}").status_code == 404
    assert job.is_dir() and published.is_dir() and not (env.output / ".cr-trash").exists()


def test_files_the_creator_keeps_in_the_videos_folder_are_named_before_they_go(env):
    job, published = finished_video(env)
    assert not [g for g in env.get("/cr-workspace/delete/video/how-one-ship--abc").json["goes"] if "Other files" in g]
    (published / "my edit.prproj").write_text("mine", encoding="utf-8")
    (published / "b-roll").mkdir()
    goes = env.get("/cr-workspace/delete/video/how-one-ship--abc").json["goes"]
    assert goes[-1] == "Other files in its folder that Studio didn't make: b-roll, my edit.prproj"


def test_an_older_video_with_no_published_record_lists_only_the_creators_files(env):
    job, published = finished_video(env)
    (job / "published.json").unlink()
    assert not [g for g in env.get("/cr-workspace/delete/video/how-one-ship--abc").json["goes"] if "Other files" in g]


def test_restore_waits_while_a_video_of_the_same_name_is_queued(env):
    job, _ = finished_video(env)
    done = env.post("/cr-workspace/delete/video/how-one-ship--abc").json
    env.studio.QUEUE.append({"slug": "how-one-ship--abc"})
    response = env.post(f"/cr-workspace/trash/{done['trash_id']}/restore")
    assert response.status_code == 409 and "can't be put back yet" in response.json["error"] and not job.exists()
    env.studio.QUEUE.clear()
    assert env.post(f"/cr-workspace/trash/{done['trash_id']}/restore").status_code == 200 and job.is_dir()


def test_restore_never_overwrites_a_video_made_again_with_the_same_title(env):
    job, _ = finished_video(env)
    done = env.post("/cr-workspace/delete/video/how-one-ship--abc").json
    job.mkdir(parents=True)
    (job / "title.txt").write_text("How One Ship Blocked the Suez Canal\n", encoding="utf-8")
    response = env.post(f"/cr-workspace/trash/{done['trash_id']}/restore")
    assert response.status_code == 409 and "same title" in response.json["error"]
    assert (env.output / ".cr-trash" / done["trash_id"] / "job" / "audio.mp3").exists()
    assert env.post("/cr-workspace/trash/../restore").status_code in (404, 405)
    assert env.post("/cr-workspace/trash/v20261025-000000-abcdef/restore").status_code == 404


def test_a_tampered_trash_record_cannot_put_files_outside_studio(env, tmp_path):
    job, _ = finished_video(env)
    done = env.post("/cr-workspace/delete/video/how-one-ship--abc").json
    record = env.output / ".cr-trash" / done["trash_id"] / "entry.json"
    saved = json.loads(record.read_text(encoding="utf-8"))
    saved["items"][0]["path"] = "../../Desktop/evil"
    record.write_text(json.dumps(saved), encoding="utf-8")
    response = env.post(f"/cr-workspace/trash/{done['trash_id']}/restore")
    assert response.status_code == 409 and not (tmp_path / "Desktop").exists()


# ── learned styles ────────────────────────────────────────────────────────────────────────────────────────────

def test_a_learned_style_goes_to_the_trash_and_its_videos_stay(env):
    path = learned_style(env)
    text = path.read_bytes()
    job, published = finished_video(env, channel="cr-clone-stickmen")
    (job / "style.txt").write_text("cr_clone_stickmen", encoding="utf-8")
    plan = env.get("/cr-workspace/delete/style/cr_clone_stickmen").json
    assert plan["title"] == "2D AI Stickmen" and plan["blocked"] == ""
    assert plan["goes"] == ["The style “2D AI Stickmen”: its look, prompts and settings, from your Style library"]
    assert plan["kept"][0] == "The 1 video you made with it: it stays in Projects"
    assert "Your plan's count of style clones stays as it is" in plan["kept"]
    done = env.post("/cr-workspace/delete/style/cr_clone_stickmen").json
    assert done["kind"] == "style" and not path.exists() and job.is_dir() and published.is_dir()
    rows = env.get("/cr-workspace/trash").json["entries"]
    assert [(r["kind"], r["name"]) for r in rows] == [("style", "2D AI Stickmen")]
    assert env.post(f"/cr-workspace/trash/{done['trash_id']}/restore").json["kind"] == "style"
    assert path.read_bytes() == text


@pytest.mark.parametrize("name,body", [
    ("documentary", {"name": "documentary", "label": "Documentary"}),
    ("cr_clone_builtin", {"name": "cr_clone_builtin", "builtin": True, "cr_style_clone": {}}),
    ("cr_clone_handmade", {"name": "cr_clone_handmade", "label": "No study record"}),
])
def test_studios_own_recipes_cannot_be_deleted(env, name, body):
    path = env.home / "styles" / f"{name}.json"
    path.write_text(json.dumps(body))
    for response in (env.get(f"/cr-workspace/delete/style/{name}"), env.post(f"/cr-workspace/delete/style/{name}")):
        assert response.status_code == 409 and "Only styles you cloned" in response.json["error"]
    assert path.exists()
    assert env.post("/cr-workspace/delete/style/cr_clone_missing").status_code == 404
    assert env.post("/cr-workspace/delete/recipe/documentary").status_code == 404


@pytest.mark.parametrize("how", ["making", "queued", "held", "study"])
def test_a_style_in_use_is_not_deleted(env, how):
    path = learned_style(env)
    if how == "making":
        env.studio.JOBS["x"] = {"state": "running", "style": "cr_clone_stickmen"}
    elif how == "queued":
        env.studio.QUEUE.append({"slug": "y", "style": "cr_clone_stickmen"})
    elif how == "held":
        env.studio._cr_queue = SimpleNamespace(held=[{"slug": "z", "style": "cr_clone_stickmen"}])
    else:
        study = env.output / "cr-style-clone" / ("op_" + "2" * 24)
        study.mkdir(parents=True)
        (study / "operation.json").write_text(json.dumps({"state": "running", "spec": {"style_id": "cr_clone_stickmen"}}))
    assert env.post("/cr-workspace/delete/style/cr_clone_stickmen").status_code == 409 and path.exists()


def test_a_style_with_its_name_taken_again_is_not_restored_over_it(env):
    path = learned_style(env)
    done = env.post("/cr-workspace/delete/style/cr_clone_stickmen").json
    path.write_text('{"name": "cr_clone_stickmen", "label": "A new clone"}')
    response = env.post(f"/cr-workspace/trash/{done['trash_id']}/restore")
    assert response.status_code == 409 and "same name" in response.json["error"]
    assert json.loads(path.read_text())["label"] == "A new clone"


# ── the trash empties itself after 30 days ────────────────────────────────────────────────────────────────────

def test_the_trash_empties_entries_older_than_30_days_and_nothing_else(env):
    job, _ = finished_video(env)
    old = env.post("/cr-workspace/delete/video/how-one-ship--abc").json["trash_id"]
    learned_style(env)
    recent = env.post("/cr-workspace/delete/style/cr_clone_stickmen").json["trash_id"]
    trash = env.output / ".cr-trash"
    record = trash / old / "entry.json"
    saved = json.loads(record.read_text(encoding="utf-8"))
    saved["deleted_at"] = time.time() - 31 * 86400
    record.write_text(json.dumps(saved), encoding="utf-8")
    (trash / "not-studios").mkdir()
    (trash / "not-studios" / "keep.txt").write_text("kept", encoding="utf-8")
    assert cr_trash.empty_old(env.home) == 1
    assert not (trash / old).exists() and (trash / recent).is_dir() and (trash / "not-studios" / "keep.txt").exists()
    assert cr_trash.empty_old(env.home, now=time.time() + 29 * 86400) == 0
    assert cr_trash.empty_old(env.home, now=time.time() + 30 * 86400 + 5) == 1 and not (trash / recent).exists()


def test_a_linked_trash_folder_is_never_used_or_emptied(env, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "v20200101-000000-abcdef").mkdir(parents=True)
    (env.output / ".cr-trash").symlink_to(elsewhere, target_is_directory=True)
    assert cr_trash.empty_old(env.home, now=time.time() + 10 ** 9) == 0
    assert (elsewhere / "v20200101-000000-abcdef").is_dir()
    job, _ = finished_video(env)
    response = env.post("/cr-workspace/delete/video/how-one-ship--abc")
    assert response.status_code == 409 and job.is_dir()


def test_a_windows_read_only_file_does_not_stop_the_trash_emptying(env):
    entry = env.output / ".cr-trash" / "v20200101-000000-abcdef"
    (entry / "job").mkdir(parents=True)
    locked = entry / "job" / "locked.txt"
    locked.write_text("x", encoding="utf-8")
    (entry / "entry.json").write_text(json.dumps({"kind": "video", "deleted_at": time.time() - 40 * 86400}))
    os.chmod(locked, 0o444)
    os.chmod(entry / "job", 0o555)
    try:
        assert cr_trash.empty_old(env.home) == 1 and not entry.exists()
    finally:
        if entry.exists():
            os.chmod(entry / "job", 0o755)
