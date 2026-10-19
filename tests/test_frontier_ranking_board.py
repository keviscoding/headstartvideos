"""The Clip Board: boards stay inside output/ranking-boards, workers run the pipeline's own board.py, the countdown
the creator sets is what make_ranking.py gets, and only the local page with its token may change anything."""
import io
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
    app = Flask("clip-board")
    app.config["SERVER_NAME"] = "127.0.0.1:7860"           # the Studio answers on localhost only
    routes.install(SimpleNamespace(app=app), token=TOKEN, home=tmp_path)
    return SimpleNamespace(client=app.test_client(), home=tmp_path)


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
