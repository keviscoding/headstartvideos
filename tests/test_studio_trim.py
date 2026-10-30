"""Trim a finished video without AI (thatguygab, Discord 2026-10-30: "Sometimes I just want to trim the video to match
the VO but I need to prompt it which takes even longer").

cr_trim cuts the finished file on this computer with FFmpeg: frame-exact cuts with the sound trimmed alongside, the
untrimmed video kept in the working folder, one-click Undo, and a second trim cut from the original again."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

from flask import Flask, jsonify, request
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_trim
import cr_workspace as workspace

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg makes the test videos")
BASE = "http://127.0.0.1:7860"
TOKEN = "synthetic-trim-token"


def film(path, seconds=6, size="160x90", audio=True):
    """A test video whose frame number is readable from its colour: red rises 40 per second."""
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c=black:s={size}:d={seconds}:r=25,geq=r='40*floor(T)':g=0:b=0"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else []) + ["-shortest", str(path)]
    subprocess.run(args, check=True, timeout=60)
    return path


def lengths(path):
    """(video stream length, audio stream length) in seconds."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                          str(path)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    got = {s["codec_type"]: float(s["duration"]) for s in json.loads(out)["streams"]}
    return got.get("video"), got.get("audio")


def red_at(path, t):
    """The red level of the frame at t (which second of the original it came from = red / 40)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return raw[(45 * 160 + 80) * 3]


def test_the_kept_parts_are_checked_and_said_plainly():
    assert cr_trim.kept(60, 2, 50) == [(2.0, 50.0)]
    assert cr_trim.kept(60, 2, 50, [10, 20]) == [(2.0, 10.0), (20.0, 50.0)]
    assert cr_trim.kept(60, 2, 50, [20, 10]) == [(2.0, 10.0), (20.0, 50.0)], "a backwards range still means that range"
    assert cr_trim.kept(60, -3, 30) == [(0.0, 30.0)], "times outside the video are clamped to it"
    with pytest.raises(cr_trim.Refused, match="Nothing to trim"):
        cr_trim.kept(60, 0, 60)
    assert cr_trim.kept(60, 0, 60, [0, 5]) == [(5.0, 60.0)], "removing the very start is a new start"
    with pytest.raises(cr_trim.Refused, match="at least a second"):
        cr_trim.kept(60, 30, 30.5)
    with pytest.raises(cr_trim.Refused, match="less than a second"):
        cr_trim.kept(60, 10, 12, [10.2, 11.9])
    with pytest.raises(cr_trim.Refused, match="times in the video"):
        cr_trim.kept(60, "soon", 10)


def test_a_second_trim_is_cut_from_the_original_in_its_time():
    first = [(2.0, 10.0), (20.0, 50.0)]                    # 38 s long now
    assert cr_trim.compose(first, [(1.0, 30.0)]) == [(3.0, 10.0), (20.0, 42.0)]
    assert cr_trim.compose(first, [(0.0, 8.0)]) == [(2.0, 10.0)]
    assert cr_trim.compose(first, [(0.0, 4.0), (12.0, 38.0)]) == [(2.0, 6.0), (24.0, 50.0)]
    assert cr_trim.compose([(0.0, 60.0)], [(5.0, 6.0)]) == [(5.0, 6.0)]


def test_the_ffmpeg_command_cuts_picture_and_sound_together_and_re_encodes(tmp_path):
    src, dst = tmp_path / "in put.mp4", tmp_path / "out.mp4"
    cmd = cr_trim.command(src, dst, [(1.0, 2.5), (4.0, 5.0)], audio=True)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v]trim=start=1.000:end=2.500,setpts=PTS-STARTPTS[v0]" in graph
    assert "[0:a]atrim=start=1.000:end=2.500,asetpts=PTS-STARTPTS[a0]" in graph
    assert graph.endswith("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    assert cmd[cmd.index("-c:v") + 1] == "libx264" and cmd[cmd.index("-c:a") + 1] == "aac"
    assert str(src) in cmd and cmd[-1] == str(dst), "paths are single arguments (spaces, Windows drive letters)"
    silent = cr_trim.command(src, dst, [(0.0, 3.0)], audio=False)
    assert "-c:a" not in silent and silent[silent.index("-filter_complex") + 1].endswith("concat=n=1:v=1:a=0[v]")


@needs_ffmpeg
def test_a_trim_keeps_the_original_and_undo_puts_it_back(tmp_path):
    job = tmp_path / "output" / "documentary" / "_work" / "the-heist--abc"
    job.mkdir(parents=True)
    video = film(tmp_path / "output" / "documentary" / "The Heist.mp4", seconds=6)
    original = video.read_bytes()
    got = cr_trim.start(job, video, 1.0, 5.0, [2.0, 3.0], wait=True)
    assert got["parts"] == [(1.0, 2.0), (3.0, 5.0)] and got["length"] == 3.0
    v, a = lengths(video)
    assert abs(v - 3.0) < 0.08 and abs(a - 3.0) < 0.08, (v, a)           # the sound stays with the picture
    assert [round(red_at(video, t) / 40) for t in (0.5, 1.5, 2.5)] == [1, 3, 4], "seconds 1, 3 and 4 kept"
    assert (job / "cr-trim" / "original.mp4").read_bytes() == original
    state = cr_trim.status(job, video)
    assert state["trimmed"] and state["original_duration"] == 6.0 and abs(state["duration"] - 3.0) < 0.08
    assert not list(video.parent.glob("*.part.mp4"))

    # a second trim cuts the original again: its first second here is the original's second 1
    cr_trim.start(job, video, 1.0, 3.0, wait=True)
    assert abs(lengths(video)[0] - 2.0) < 0.08
    assert [round(red_at(video, t) / 40) for t in (0.5, 1.5)] == [3, 4]
    assert json.loads((job / "cr-trim" / "trim.json").read_text(encoding="utf-8"))["parts"] == [[3.0, 5.0]]

    assert cr_trim.undo(job, video)["undone"]
    assert video.read_bytes() == original and not cr_trim.status(job, video)["trimmed"]
    with pytest.raises(cr_trim.Refused, match="no trim to undo"):
        cr_trim.undo(job, video)


@needs_ffmpeg
def test_a_video_rendered_again_after_its_trim_forgets_the_trim(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    video = film(tmp_path / "video.mp4", seconds=4, audio=False)        # a silent video trims too
    cr_trim.start(job, video, 0.0, 3.0, wait=True)
    assert cr_trim.status(job, video)["trimmed"]
    film(video, seconds=5, audio=False)                                  # the timeline rendered it again
    assert not cr_trim.status(job, video)["trimmed"], "Undo would throw the new render away"
    with pytest.raises(cr_trim.Refused):
        cr_trim.undo(job, video)
    cr_trim.start(job, video, 1.0, 5.0, wait=True)                       # the new render is the new original
    assert abs(lengths(video)[0] - 4.0) < 0.08
    assert abs(lengths(job / "cr-trim" / "original.mp4")[0] - 5.0) < 0.08


@needs_ffmpeg
def test_a_failed_cut_leaves_the_video_as_it_was(tmp_path, monkeypatch):
    job = tmp_path / "job"
    job.mkdir()
    video = film(tmp_path / "video.mp4", seconds=3)
    before = video.read_bytes()
    monkeypatch.setattr(cr_trim, "CRF", "not-a-number")
    cr_trim.start(job, video, 0.5, 2.5, wait=True)
    state = cr_trim.status(job, video)
    assert state["error"].startswith("FFmpeg couldn't cut this video") and not state["trimmed"]
    assert video.read_bytes() == before and not (job / "cr-trim" / "original.mp4").exists()
    assert not list(tmp_path.glob("*.part.mp4"))


@pytest.fixture
def routes(tmp_path):
    app = Flask(__name__)

    @app.get("/")
    def original_page():
        return "Original"

    @app.get("/api/channels")
    def original_channels():
        return jsonify(channels=[])

    @app.before_request
    def original_guards():
        if request.method == "POST" and request.headers.get("X-CR-Local-Token") != TOKEN:
            return jsonify(error="local token required"), 403

    (tmp_path / "cr_workspace.html").write_text("<html></html>", encoding="utf-8")
    jobs = {}
    job = tmp_path / "output" / "ranking" / "_work" / "funniest-dogs--abc"
    job.mkdir(parents=True)
    jobs[job.name] = job
    published = tmp_path / "output" / "ranking" / "Funniest Dogs" / "Funniest Dogs.mp4"
    published.parent.mkdir()
    engine = SimpleNamespace(find_job=lambda slug: jobs.get(slug), final_video=lambda j: published)
    studio = SimpleNamespace(app=app, styles=SimpleNamespace(STYLES={}), mv=engine, JOBS={})
    workspace.install(studio, SimpleNamespace(lock=threading.RLock(), busy=lambda: False), token=TOKEN, home=tmp_path)
    client = app.test_client()
    post = lambda path, body=None: client.post(path, base_url=BASE, json=body or {}, headers={"X-CR-Local-Token": TOKEN})
    return SimpleNamespace(client=client, post=post, job=job, video=published, studio=studio, home=tmp_path,
                           get=lambda path: client.get(path, base_url=BASE))


@needs_ffmpeg
def test_the_trim_routes_cut_a_published_short_and_undo_it(routes, monkeypatch):
    film(routes.video, seconds=5, size="90x160")
    assert routes.get("/cr-workspace/trim/funniest-dogs--abc").json["duration"] == 5.0
    assert routes.client.post("/cr-workspace/trim/funniest-dogs--abc", base_url=BASE, json={}).status_code == 403
    start = cr_trim.start
    monkeypatch.setattr(cr_trim, "start", lambda *a, **k: start(*a, wait=True, **k))      # the route doesn't wait
    started = routes.post("/cr-workspace/trim/funniest-dogs--abc", {"start": 0, "end": 3.5})
    assert started.status_code == 202 and started.json["parts"] == [[0.0, 3.5]]
    state = routes.get("/cr-workspace/trim/funniest-dogs--abc").json
    assert state["trimmed"] and abs(state["duration"] - 3.5) < 0.08
    assert (routes.job / "cr-trim" / "original.mp4").is_file(), "the backup sits in the working folder"
    assert routes.post("/cr-workspace/trim/funniest-dogs--abc/undo").json["undone"]
    assert routes.get("/cr-workspace/trim/funniest-dogs--abc").json["duration"] == 5.0


def test_the_trim_routes_refuse_what_they_cant_do(routes):
    assert routes.get("/cr-workspace/trim/..").status_code in (400, 404)
    assert routes.get("/cr-workspace/trim/nobody").status_code == 400
    routes.studio.JOBS["funniest-dogs--abc"] = {"state": "running"}
    got = routes.post("/cr-workspace/trim/funniest-dogs--abc", {"start": 0, "end": 2})
    assert got.status_code == 409 and "still being made" in got.json["error"]
    routes.studio.JOBS.clear()
    got = routes.post("/cr-workspace/trim/funniest-dogs--abc", {"start": 0, "end": 2})
    assert got.status_code == 409 and got.json["error"] == "This video has no finished file to trim."
    got = routes.post("/cr-workspace/trim/funniest-dogs--abc/undo")
    assert got.status_code == 409 and "no trim to undo" in got.json["error"]
