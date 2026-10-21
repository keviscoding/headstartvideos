"""Long Ranking Shorts batches (local_runtime/cr_queue.py): a shared stop holds the Shorts still waiting instead of
letting each start and fail; what waits survives a closed Studio, on hold; Carry on and Remove; at most 15 topics in
one batch, each once."""
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
sys.path.insert(0, str(Path(__file__).parent))
import cr_queue
import cr_submission
from test_frontier_submission import studio_with_original

RANKING = '[[short_seconds=25]] [[ranking={"ranks": 5}]]'


def spec(title, ranking=True, **more):
    return {"slug": title.lower().replace(" ", "-") + "--run", "title": title, "style": "cr-shorts-ranking",
            "steps": ["script", "video"], "minutes": 1, "extra": RANKING if ranking else "", **more}


def fake_studio(queue=()):
    dispatched = []
    studio = SimpleNamespace(app=Flask("queue-test"), QUEUE=list(queue), LOCK=threading.Lock(), JOBS={})
    studio._ensure_dispatcher = lambda: dispatched.append(True)
    for s in studio.QUEUE:
        studio.JOBS[s["slug"]] = {"state": "queued", "title": s["title"], "done": False}
    return studio, dispatched


def stopped(tmp_path, code, message="Your clip watches for this month are used up."):
    job = tmp_path / "job"
    (job / "short").mkdir(parents=True, exist_ok=True)
    (job / "short" / "error.json").write_text(json.dumps({"code": code, "message": message}), encoding="utf-8")
    return job


def test_a_shared_stop_holds_the_ranking_shorts_still_waiting(tmp_path):
    first = spec("Funniest Cats")
    waiting = [spec("Funniest Dogs"), spec("Long video", ranking=False), spec("Scariest Moments")]
    studio, _ = fake_studio(waiting)
    keeper = cr_queue.Keeper(studio, tmp_path)
    studio.JOBS[first["slug"]] = {"state": "error", "title": first["title"], "done": True}
    job = stopped(tmp_path, "out_of_watches")
    keeper.finished(first, lambda: job)
    assert [s["title"] for s in studio.QUEUE] == ["Long video"]          # other recipes carry on
    assert [s["title"] for s in keeper.held] == ["Funniest Dogs", "Scariest Moments"]
    assert all(studio.JOBS[s["slug"]]["state"] == "held" for s in keeper.held)
    assert "used up" in keeper.status()["reason"]
    saved = json.loads((tmp_path / "output" / ".cr-queue.json").read_text())
    assert [s["title"] for s in saved["held"]] == ["Funniest Dogs", "Scariest Moments"]
    assert [s["title"] for s in saved["waiting"]] == ["Long video"]


def test_every_shared_code_holds_and_a_shorts_own_stop_does_not(tmp_path):
    for code, holds in (("youtube_blocked", True), ("claude_usage_limit", True), ("watch_service", True),
                        ("voice_missing", True), ("crashed", False), ("clip_missing", False),
                        ("render_failed", False), ("stopped", False)):
        studio, _ = fake_studio([spec("Funniest Dogs")])
        keeper = cr_queue.Keeper(studio, tmp_path / code)
        first = spec("Funniest Cats")
        studio.JOBS[first["slug"]] = {"state": "error", "done": True}
        job = stopped(tmp_path / code, code)
        keeper.finished(first, lambda: job)
        assert (len(keeper.held) == 1) is holds, code


def test_a_finished_short_or_another_recipe_never_holds(tmp_path):
    studio, _ = fake_studio([spec("Funniest Dogs")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    job = stopped(tmp_path, "out_of_watches")                  # a stale stop from an earlier attempt
    done = spec("Funniest Cats")
    studio.JOBS[done["slug"]] = {"state": "done", "done": True}
    keeper.finished(done, lambda: job)
    other = spec("History video", ranking=False)
    studio.JOBS[other["slug"]] = {"state": "error", "done": True}
    keeper.finished(other, lambda: job)
    assert keeper.held == [] and len(studio.QUEUE) == 1


def test_a_closed_studio_keeps_what_waited_on_hold(tmp_path):
    studio, _ = fake_studio([spec("Funniest Dogs"), spec("Scariest Moments"),
                             spec("Timeline render", timeline=True), {"slug": "odd", "title": "x", "f": {1, 2}}])
    cr_queue.Keeper(studio, tmp_path).save()
    fresh, dispatched = fake_studio()                          # the next Studio opens with an empty queue
    keeper = cr_queue.Keeper(fresh, tmp_path)
    keeper.restore()
    assert [s["title"] for s in keeper.held] == ["Funniest Dogs", "Scariest Moments"]
    assert fresh.QUEUE == [] and dispatched == []              # never started unasked
    assert keeper.status()["reason"] == cr_queue.CLOSED
    assert keeper.resume() == 2
    assert [s["title"] for s in fresh.QUEUE] == ["Funniest Dogs", "Scariest Moments"] and dispatched == [True]
    assert all(fresh.JOBS[s["slug"]]["state"] == "queued" for s in fresh.QUEUE)
    again = cr_queue.Keeper(fake_studio()[0], tmp_path)
    again.restore()                                            # the saved list now says they are waiting again
    assert len(again.held) == 2


def test_remove_drops_the_held_shorts(tmp_path):
    studio, _ = fake_studio([spec("Funniest Dogs")])
    keeper = cr_queue.Keeper(studio, tmp_path)
    keeper.hold_ranking("Claude's usage limit is reached until 4 am.")
    assert keeper.drop() == 1
    assert keeper.status() == {"held": [], "reason": "", "waiting": 0, "max_ranking_batch": 15}
    assert studio.JOBS == {}
    restored = cr_queue.Keeper(fake_studio()[0], tmp_path)
    restored.restore()
    assert restored.held == []


def test_routes(tmp_path):
    studio, dispatched = fake_studio([spec("Funniest Dogs")])
    keeper = cr_queue.install(studio, tmp_path)
    keeper.hold_ranking("YouTube is asking this computer to slow down.")
    client = studio.app.test_client()
    got = client.get("/cr-queue").get_json()
    assert got["held"][0]["title"] == "Funniest Dogs" and "slow down" in got["reason"]
    assert client.post("/cr-queue/resume").get_json()["resumed"] == 1 and dispatched == [True]
    keeper.hold_ranking("again")
    assert client.post("/cr-queue/drop").get_json()["removed"] == 1


def test_a_ranking_batch_is_at_most_15_topics_each_once():
    studio, _, _ = studio_with_original()
    cr_submission.install(studio)
    client = studio.app.test_client()
    titles = [f"Funniest Thing {i}" for i in range(16)]
    got = client.post("/api/run", json={"title": "\n".join(titles), "steps": ["video"], "extra": RANKING})
    assert got.status_code == 400 and "15" in got.get_json()["error"]
    got = client.post("/api/run", json={"title": "Funniest Dogs\nfunniest  dogs\nScariest Moments\n",
                                        "steps": ["video"], "extra": RANKING})
    assert got.status_code == 200
    assert [s["title"] for s in studio.QUEUE] == ["Funniest Dogs", "Scariest Moments"]
    other = client.post("/api/run", json={"title": "\n".join(titles), "steps": ["video"]})
    assert other.status_code == 200                            # other recipes keep their own batch limit
