"""The Clip Board: boards stay inside output/ranking-boards, workers run the pipeline's own board.py, the countdown
the creator sets is what make_ranking.py gets, and only the local page with its token may change anything."""
import importlib
import io
import re
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_ranking_routes as routes

TOKEN = "local-token"
HEAD = {"X-CR-Local-Token": TOKEN}


@pytest.fixture
def board(tmp_path, monkeypatch):
    (tmp_path / "cr_ranking.html").write_text("<script>const localToken=__LOCAL_TOKEN_JSON__;</script>")
    script = tmp_path / "shorts" / "ranking" / "board.py"
    script.parent.mkdir(parents=True)
    # a stand-in worker: records what it was asked, writes two moments
    script.write_text(
        "import json, sys, pathlib\n"
        "board = pathlib.Path(sys.argv[2])\n"
        "(board / 'args.json').write_text(json.dumps(sys.argv[3:]))\n"
        "(board / 'board.json').write_text(json.dumps([\n"
        "  {'id': 'm0', 'vid': 'aaaaaaaaaaa', 'score': 9, 'label': 'Guilty Smile', 'type': 'funny animal', 'cut': [1, 4], 'payoff': 2.4, 'end': 4, 'start': 0},\n"
        "  {'id': 'm1', 'vid': 'bbbbbbbbbbb', 'score': 7, 'label': 'Pop!', 'type': 'fail', 'cut': [3, 6], 'payoff': 5, 'end': 6, 'start': 2},\n"
        "  {'id': 'm2', 'vid': 'ccccccccccc', 'score': 6, 'label': 'Why', 'type': 'cute', 'cut': [0, 3], 'payoff': 1, 'end': 3, 'start': 0}]))\n"
        "print('12:00:00 done: 3 moments on the board', flush=True)\n")
    # this computer has a JavaScript runtime for yt-dlp unless a test says otherwise (CI's Node may be too old); the
    # cr_shorts the routes import now (another test file puts its own copy in sys.modules)
    shorts = importlib.import_module("cr_shorts")
    monkeypatch.setattr(shorts, "js_runtime_missing", lambda then="make the Short again": "")
    app = Flask("clip-board")
    app.config["SERVER_NAME"] = "127.0.0.1:7860"           # the Studio answers on localhost only
    routes.install(SimpleNamespace(app=app), token=TOKEN, home=tmp_path)
    return SimpleNamespace(client=app.test_client(), home=tmp_path, app=app, shorts=shorts)


def wait(board, board_id):
    for _ in range(100):
        s = board.client.get(f"/cr-ranking/boards/{board_id}").get_json()
        if not s["running"]:
            return s
        time.sleep(0.05)
    raise AssertionError("the worker did not finish")


def test_the_page_carries_the_local_token(board):
    page = board.client.get("/cr-ranking")
    assert page.status_code == 200 and json.dumps(TOKEN) in page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "private, no-store"


def test_finding_runs_the_pipelines_board_worker_and_shows_its_moments(board):
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD).get_json()
    assert started["id"].startswith("funniest-dog-moments-")
    done = wait(board, started["id"])
    path = board.home / "output" / "ranking-boards" / started["id"]
    assert json.loads((path / "args.json").read_text()) == ["find", "Funniest Dog Moments"]
    assert [m["label"] for m in done["moments"]] == ["Guilty Smile", "Pop!", "Why"]
    assert done["topic"] == "Funniest Dog Moments" and done["progress"][-1].startswith("done:")
    listed = board.client.get("/cr-ranking/boards").get_json()["boards"]
    assert listed[0]["id"] == started["id"] and listed[0]["moments"] == 3


def test_the_countdown_is_saved_in_play_order_for_make_ranking(board):
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD).get_json()
    wait(board, started["id"])
    saved = board.client.post(f"/cr-ranking/boards/{started['id']}/choice", json={"picks": ["m2", "m1", "m0"]},
                              headers=HEAD)
    assert saved.status_code == 200 and saved.get_json()["topic"] == "Funniest Dog Moments"
    choice = json.loads((board.home / "output" / "ranking-boards" / started["id"] / "choice.json").read_text())
    assert [m["id"] for m in choice["picks"]] == ["m2", "m1", "m0"], "the last one plays last, as #1"
    for bad in (["m0", "m1"], ["m0", "m0", "m1"], ["m0", "m1", "gone"]):
        assert board.client.post(f"/cr-ranking/boards/{started['id']}/choice", json={"picks": bad},
                                 headers=HEAD).status_code == 400


def test_own_clips_are_saved_inside_the_board_and_watched_by_the_worker(board):
    started = board.client.post("/cr-ranking/boards", json={"topic": "My clips", "find": False}, headers=HEAD).get_json()
    sent = board.client.post(f"/cr-ranking/boards/{started['id']}/clips", headers=HEAD,
                             data={"clips": [(io.BytesIO(b"mp4 bytes"), "../../evil name.mp4")]},
                             content_type="multipart/form-data")
    assert sent.status_code == 200
    wait(board, started["id"])
    path = board.home / "output" / "ranking-boards" / started["id"]
    args = json.loads((path / "args.json").read_text())
    assert args[0] == "add" and Path(args[1]).parent == path / "uploads" and Path(args[1]).suffix == ".mp4"
    assert ".." not in Path(args[1]).name
    refused = board.client.post(f"/cr-ranking/boards/{started['id']}/clips", headers=HEAD,
                                data={"clips": [(io.BytesIO(b"x"), "notes.txt")]}, content_type="multipart/form-data")
    assert refused.status_code == 400


def test_nothing_changes_without_the_local_token_or_outside_the_boards(board):
    assert board.client.post("/cr-ranking/boards", json={"topic": "Dogs"}).status_code == 403
    assert board.client.post("/cr-ranking/boards", json={"topic": "Dogs"},
                             headers={**HEAD, "Origin": "https://evil.example"}).status_code == 403
    assert board.client.get("/cr-ranking/boards/..%2F..%2Fsecrets").status_code in (400, 404)
    assert board.client.get("/cr-ranking/boards/does-not-exist").status_code == 400
    assert board.client.post("/cr-ranking/boards", json={"topic": "x"}, headers=HEAD).status_code == 400


def test_a_studio_without_the_pipeline_says_to_update(board):
    (board.home / "shorts" / "ranking" / "board.py").unlink()
    r = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD)
    assert r.status_code == 409 and "Update the Studio" in r.get_json()["error"]


def test_pasted_links_go_to_the_worker_and_only_web_links_pass(board):
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments", "find": False}, headers=HEAD).get_json()
    sent = board.client.post(f"/cr-ranking/boards/{started['id']}/links", headers=HEAD, json={"links": [
        "https://www.tiktok.com/@someone/video/7300000000000000000", "javascript:alert(1)", "file:///etc/passwd",
        "https://youtu.be/dN_RMA8LVi0"]})
    assert sent.status_code == 200
    wait(board, started["id"])
    args = json.loads((board.home / "output" / "ranking-boards" / started["id"] / "args.json").read_text())
    assert args == ["links", "https://www.tiktok.com/@someone/video/7300000000000000000", "https://youtu.be/dN_RMA8LVi0"]
    assert board.client.post(f"/cr-ranking/boards/{started['id']}/links", headers=HEAD,
                             json={"links": ["ftp://x"]}).status_code == 400


# ── scale-proofing (2026-10-21): snapshots, Stop, workers that end with the Studio, UTF-8 ───────────────────────────
import os
import signal
import subprocess
import pytest as _pytest

SLOW_WORKER = (
    "import json, os, sys, time, pathlib\n"
    "hasattr(os, 'nice') and os.nice(10)\n"                 # what board.py does first (find.lower_priority)
    "board = pathlib.Path(sys.argv[2])\n"
    "(board / 'env.json').write_text(json.dumps({k: os.environ.get(k) for k in ('PYTHONUTF8', 'PYTHONIOENCODING')}"
    " | {'nice': os.getpriority(os.PRIO_PROCESS, 0) if hasattr(os, 'getpriority') else None}))\n"
    "child = __import__('subprocess').Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "(board / 'pids.json').write_text(json.dumps([os.getpid(), child.pid]))\n"
    "print('12:00:00 finding searching YouTube: 12 searches', flush=True)\n"
    "time.sleep(60)\n")


def _gone(pid, seconds=8.0):
    for _ in range(int(seconds * 20)):
        if not routes._alive(pid):          # Studio's own check: os.kill(pid, 0) would end the process on Windows
            return True
        time.sleep(0.05)
    return False


def _wait_for(path, seconds=10.0):
    for _ in range(int(seconds * 20)):
        if path.is_file() and path.read_text():
            return
        time.sleep(0.05)
    raise AssertionError(f"{path.name} never appeared")


def test_each_make_gets_its_own_snapshot_of_the_countdown(board):
    """A Short queued behind another video reads the countdown it was made from, not the board's latest pick."""
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD).get_json()
    wait(board, started["id"])
    path = board.home / "output" / "ranking-boards" / started["id"]
    a = board.client.post(f"/cr-ranking/boards/{started['id']}/choice", json={"picks": ["m0", "m1", "m2"]}, headers=HEAD)
    b = board.client.post(f"/cr-ranking/boards/{started['id']}/choice", json={"picks": ["m2", "m0", "m1"],
                          "topic": "Best Dog Moments"}, headers=HEAD)
    first, second = a.get_json()["choice"], b.get_json()["choice"]
    assert first != second and re.fullmatch(r"choice-[A-Za-z0-9-]{1,80}\.json", first)
    assert [m["id"] for m in json.loads((path / first).read_text())["picks"]] == ["m0", "m1", "m2"], "never overwritten"
    assert [m["id"] for m in json.loads((path / second).read_text())["picks"]] == ["m2", "m0", "m1"]
    assert json.loads((path / "choice.json").read_text())["picks"][0]["id"] == "m2", "choice.json is the latest"
    assert b.get_json()["topic"] == "Best Dog Moments", "naming the countdown names the Short"


def test_stop_ends_the_worker_and_everything_it_started(board):
    (board.home / "shorts" / "ranking" / "board.py").write_text(SLOW_WORKER)
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD).get_json()
    path = board.home / "output" / "ranking-boards" / started["id"]
    _wait_for(path / "pids.json")
    worker, child = json.loads((path / "pids.json").read_text())
    assert board.client.get(f"/cr-ranking/boards/{started['id']}").get_json()["running"]
    busy = board.client.post(f"/cr-ranking/boards/{started['id']}/clips", headers=HEAD,
                             data={"clips": [(io.BytesIO(b"mp4"), "a.mp4")]}, content_type="multipart/form-data")
    assert busy.status_code == 409 and not (path / "uploads").exists(), "a refused upload leaves no file behind"
    stopped = board.client.post(f"/cr-ranking/boards/{started['id']}/stop", headers=HEAD).get_json()
    assert not stopped["running"] and "You stopped this board" in stopped["stopped"]
    assert _gone(worker) and _gone(child), "yt-dlp and ffmpeg a worker started end with it"
    assert board.client.post(f"/cr-ranking/boards/{started['id']}/stop").status_code == 403, "only the page may stop it"


def test_workers_run_in_utf8_below_normal_priority(board):
    (board.home / "shorts" / "ranking" / "board.py").write_text(SLOW_WORKER)
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD).get_json()
    path = board.home / "output" / "ranking-boards" / started["id"]
    _wait_for(path / "env.json")
    env = json.loads((path / "env.json").read_text())
    board.client.post(f"/cr-ranking/boards/{started['id']}/stop", headers=HEAD)
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8:replace"
    if os.name != "nt":
        base = os.getpriority(os.PRIO_PROCESS, 0)
        assert env["nice"] >= 10 and (base + 10 >= 19 or env["nice"] == base + 10), "nice 10 once, never 10 + 10"


@_pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_worker_ends_when_the_studio_that_started_it_is_gone(tmp_path):
    """A crashed or closed Studio must not leave a search spending clip watches unseen."""
    pids = tmp_path / "pids.json"
    worker = ("import json, os, sys, time, subprocess, pathlib\n"
              "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              f"pathlib.Path({str(pids)!r}).write_text(json.dumps([os.getpid(), c.pid]))\n"
              "time.sleep(60)\n")
    studio = ("import subprocess, sys, os, time\n"
              f"subprocess.Popen([sys.executable, {str(Path(routes.__file__).resolve())!r}, '--guard', str(os.getpid()),"
              f" '--', sys.executable, '-c', {worker!r}], start_new_session=True)\n"
              "time.sleep(1.5)\n")                                # then this Studio is gone
    subprocess.run([sys.executable, "-c", studio], timeout=30, check=True)
    _wait_for(pids)
    for pid in json.loads(pids.read_text()):
        assert _gone(pid), "the guard ended the worker and its children"


def test_pasted_youtube_links_lose_their_playlist(board):
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments", "find": False}, headers=HEAD).get_json()
    board.client.post(f"/cr-ranking/boards/{started['id']}/links", headers=HEAD, json={"links": [
        "https://www.youtube.com/watch?v=dN_RMA8LVi0&list=RDdN_RMA8LVi0&start_radio=1&index=2",
        "https://youtu.be/dN_RMA8LVi0?list=PL123&t=42", "https://www.youtube.com/playlist?list=PL123"]})
    wait(board, started["id"])
    args = json.loads((board.home / "output" / "ranking-boards" / started["id"] / "args.json").read_text())
    assert args == ["links", "https://www.youtube.com/watch?v=dN_RMA8LVi0", "https://youtu.be/dN_RMA8LVi0?t=42",
                    "https://www.youtube.com/playlist?list=PL123"]


def test_a_link_typed_as_the_topic_is_refused_with_the_way_to_use_it(board):
    r = board.client.post("/cr-ranking/boards", json={"topic": "https://www.youtube.com/watch?v=dN_RMA8LVi0"}, headers=HEAD)
    assert r.status_code == 400 and "Paste links" in r.get_json()["error"]


# ── review round 2 (2026-10-21): updates wait for boards, zombies read finished, no paid find without a JS runtime ──
def test_a_find_without_a_javascript_runtime_is_refused_before_any_watch(board, monkeypatch):
    monkeypatch.setattr(board.shorts, "js_runtime_missing", lambda then="make the Short again":
                        f"Install Node.js 22 or newer from nodejs.org, restart the Studio and {then}.")
    r = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD)
    assert r.status_code == 409 and "find clips again" in r.get_json()["error"]
    assert not (board.home / "output" / "ranking-boards").exists(), "no board, no worker, no watch"
    own = board.client.post("/cr-ranking/boards", json={"topic": "My clips", "find": False}, headers=HEAD)
    assert own.status_code == 200, "a board for the creator's own clips needs no YouTube"


def test_the_update_restart_waits_for_a_working_board(board):
    restarts = []
    board.app.add_url_rule("/cr-update/restart", "restart", lambda: restarts.append(1) or {"state": "restarting"},
                           methods=["POST"])
    (board.home / "shorts" / "ranking" / "board.py").write_text(SLOW_WORKER)
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD).get_json()
    _wait_for(board.home / "output" / "ranking-boards" / started["id"] / "pids.json")
    held = board.client.post("/cr-update/restart", headers=HEAD)
    assert held.status_code == 409 and "Clip Board search is still working" in held.get_json()["error"]
    assert not restarts, "the Studio never restarts under a running search"
    board.client.post(f"/cr-ranking/boards/{started['id']}/stop", headers=HEAD)
    assert board.client.post("/cr-update/restart", headers=HEAD).status_code == 200 and restarts == [1]


def test_a_finished_worker_leaves_no_record_behind(board):
    started = board.client.post("/cr-ranking/boards", json={"topic": "Funniest Dog Moments"}, headers=HEAD).get_json()
    wait(board, started["id"])
    assert not (board.home / "output" / "ranking-boards" / started["id"] / "worker.json").exists()
    assert not routes.busy(board.home)


@_pytest.mark.skipif(os.name == "nt", reason="the Mac and Linux update restarts with os.execv")
def test_after_an_in_place_update_a_finished_worker_reads_finished(tmp_path):
    """The Mac update replaces the Studio's program under the same pid (os.execv): the worker started before it runs on
    as its child, and when it ends nobody waits for it. kill 0 still found that zombie, so the board read "working"
    until the creator pressed Stop (and busy() would have held every later update)."""
    path = tmp_path / "output" / "ranking-boards" / "dogs-1a2b3c"
    path.mkdir(parents=True)
    out, here = tmp_path / "after.json", str(Path(routes.__file__).resolve().parent)
    script = str(Path(routes.__file__).resolve())
    after = ("import json, os, sys, time, pathlib\n"
             f"sys.path.insert(0, {here!r})\n"
             "import cr_ranking_routes as r\n"
             f"board, home = pathlib.Path({str(path)!r}), pathlib.Path({str(tmp_path)!r})\n"
             "rec = r.worker_record(board)\n"
             "seen = {'same_pid': rec['studio'] == os.getpid(), 'working': r.recorded_worker(board) == rec['pid'],\n"
             "        'busy': r.busy(home)}\n"
             "for _ in range(300):\n"
             "    if r.recorded_worker(board) is None:\n"
             "        break\n"
             "    time.sleep(0.05)\n"
             "seen.update(finished=r.recorded_worker(board) is None, busy_after=r.busy(home),\n"
             "            record_left=(board / 'worker.json').exists(), reaped=not r._alive(rec['pid']))\n"
             f"pathlib.Path({str(out)!r}).write_text(json.dumps(seen))\n")
    before = ("import os, sys, subprocess, pathlib\n"
              f"sys.path.insert(0, {here!r})\n"
              "import cr_ranking_routes as r\n"
              f"p = subprocess.Popen([sys.executable, {script!r}, '--guard', str(os.getpid()), '--', sys.executable, '-c',"
              " 'import time; time.sleep(1.5)'], start_new_session=True)\n"
              f"r.write_record(pathlib.Path({str(path)!r}), p.pid, ['find'])\n"
              f"os.execv(sys.executable, [sys.executable, '-c', {after!r}])\n")
    subprocess.run([sys.executable, "-c", before], timeout=60, check=True)
    assert json.loads(out.read_text()) == {"same_pid": True, "working": True, "busy": True, "finished": True,
                                           "busy_after": False, "record_left": False, "reaped": True}


def test_a_worker_another_studio_process_started_counts_while_that_studio_is_open(tmp_path):
    """Windows' update runs the new Studio beside the old one under a new pid; the old one's worker keeps going, so the
    board still reads working (no second worker on it) until that Studio is gone."""
    path = tmp_path / "output" / "ranking-boards" / "dogs-1a2b3c"
    path.mkdir(parents=True)
    studio = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    guard = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    record = lambda age=0: (path / "worker.json").write_text(json.dumps(
        {"pid": guard.pid, "studio": studio.pid, "started": time.time() - age}))
    try:
        record()
        assert routes.recorded_worker(path) == guard.pid and routes.busy(tmp_path)
        record(age=routes.STALE_WORKER_S + 60)
        assert routes.recorded_worker(path) is None, "a record that old names pids the system may have reused"
        assert not (path / "worker.json").exists()
        record()
        studio.kill()
        studio.wait()
        assert routes.recorded_worker(path) is None and not routes.busy(tmp_path), "its Studio is gone: so is it"
    finally:
        for proc in (studio, guard):
            proc.kill()
            proc.wait()
