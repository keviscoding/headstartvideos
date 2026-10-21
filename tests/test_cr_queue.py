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
    """Shaped like Frontier's api_run makes them: mix is a tuple, meta None, features a dict (the first version's
    JSON round-trip check rejected every real spec over the tuple, so nothing was ever saved)."""
    return {"slug": title.lower().replace(" ", "-") + "--run", "title": title, "style": "cr-shorts-ranking",
            "steps": ["script", "video"], "minutes": 1, "extra": RANKING if ranking else "", "mix": (),
            "meta": None, "features": {"captions": True}, "script_text": "", "burn_subs": False,
            "cr_video_run_id": "run_" + title.lower().replace(" ", "_"), **more}


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
    assert "used up" in keeper.status()["reason"] and keeper.status()["code"] == "out_of_watches"
    saved = json.loads((tmp_path / "output" / ".cr-queue.json").read_text())
    assert [s["title"] for s in saved["held"]] == ["Funniest Dogs", "Scariest Moments"]
    assert [s["title"] for s in saved["waiting"]] == ["Long video"]


def test_every_shared_code_holds_and_a_shorts_own_stop_does_not(tmp_path):
    for code, holds in (("youtube_blocked", True), ("claude_usage_limit", True), ("claude_auth", True),
                        ("claude_missing", True), ("watch_service", True), ("voice_missing", True),
                        ("claude_format", False), ("claude_timeout", False), ("claude_other", False),
                        ("crashed", False), ("clip_missing", False), ("render_failed", False), ("stopped", False)):
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
    assert keeper.status() == {"held": [], "reason": "", "code": "", "waiting": 0, "max_ranking_batch": 15}
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


def test_carry_on_using_credits_turns_credits_on_for_the_held_ranking_shorts_only(tmp_path):
    """Out of clip watches, Carry on alone would stop again at once: the notice offers Carry on using credits, which
    sets use_credits in each held Ranking Short's own options (and nothing else), as the creator chose there."""
    studio, _ = fake_studio([spec("Funniest Dogs")])
    keeper = cr_queue.install(studio, tmp_path / "a")
    keeper.hold_ranking("Your clip watches for this month are used up.", "out_of_watches")
    again = cr_queue.Keeper(fake_studio()[0], tmp_path / "a")
    again.restore()                                            # the reason's code survives a restart
    assert again.status()["code"] == "out_of_watches"
    got = studio.app.test_client().post("/cr-queue/resume", json={"use_credits": True}).get_json()
    assert got["resumed"] == 1 and got["code"] == ""
    resumed = studio.QUEUE[0]
    assert resumed["extra"].startswith("[[short_seconds=25]] [[ranking=")
    assert json.loads(resumed["extra"].split("[[ranking=")[1].rsplit("]]", 1)[0]) == {"ranks": 5, "use_credits": True}

    other = spec("Long video", ranking=False, extra="[[short_seconds=25]] keep me")
    studio, _ = fake_studio([spec("Funniest Cats"), other])
    keeper = cr_queue.install(studio, tmp_path / "b")
    keeper.hold_ranking("used up", "out_of_watches")
    keeper.resume(use_credits=False)                           # plain Carry on changes nothing
    assert [s["extra"] for s in studio.QUEUE] == ["[[short_seconds=25]] keep me", RANKING]


def test_real_specs_are_saved_and_come_back_as_the_engine_made_them(tmp_path):
    studio, _ = fake_studio([spec("Funniest Dogs", mix=(40.0, 30.0, 30.0)), spec("Scariest Moments")])
    studio.JOBS["funniest-dogs--run"]["threed"] = True
    cr_queue.Keeper(studio, tmp_path).save()
    saved = json.loads((tmp_path / "output" / ".cr-queue.json").read_text())
    assert [s["title"] for s in saved["waiting"]] == ["Funniest Dogs", "Scariest Moments"]
    fresh, _ = fake_studio()
    keeper = cr_queue.Keeper(fresh, tmp_path)
    keeper.restore()
    assert keeper.held[0]["mix"] == (40.0, 30.0, 30.0) and keeper.held[1]["mix"] == ()
    assert keeper.held[0]["features"] == {"captions": True}


def test_a_cancelled_short_never_comes_back(tmp_path):
    studio, _ = fake_studio([spec("Funniest Dogs"), spec("Scariest Moments")])
    cr_queue.Keeper(studio, tmp_path).save()
    gone = tmp_path / "output" / "cr-video-workers" / "run_scariest_moments"
    gone.mkdir(parents=True)
    (gone / "cancelled.json").write_text("{}")                 # cr_video_cancel's record of a queued cancel
    keeper = cr_queue.Keeper(fake_studio()[0], tmp_path)
    keeper.restore()
    assert [s["title"] for s in keeper.held] == ["Funniest Dogs"]


def test_the_3d_flag_survives_a_hold(tmp_path):
    studio, _ = fake_studio([spec("A 3D video", ranking=True)])
    studio.JOBS["a-3d-video--run"]["threed"] = True
    keeper = cr_queue.Keeper(studio, tmp_path)
    keeper.hold_ranking("stop", "youtube_blocked")
    assert studio.JOBS["a-3d-video--run"]["threed"] is True
    keeper.resume()
    assert studio.JOBS["a-3d-video--run"]["threed"] is True and studio.JOBS["a-3d-video--run"]["state"] == "queued"
    fresh, _ = fake_studio()
    fresh._threed_on = lambda style, features: style == "cr-shorts-ranking"
    keeper.hold_ranking("stop", "youtube_blocked")
    again = cr_queue.Keeper(fresh, tmp_path)
    again.restore()                                            # a new Studio works it out from the recipe
    assert fresh.JOBS["a-3d-video--run"]["threed"] is True


def test_the_engines_clear_queue_and_a_cancel_save_the_list(tmp_path):
    studio, _ = fake_studio([spec("Funniest Dogs"), spec("Scariest Moments")])
    keeper = cr_queue.install(studio, tmp_path)
    keeper.save()

    @studio.app.post("/api/queue/clear")
    def api_queue_clear():                                     # as Frontier's app.py has it
        with studio.LOCK:
            studio.QUEUE.clear()
        return {"cleared": 2}
    assert studio.app.test_client().post("/api/queue/clear").status_code == 200
    assert json.loads((tmp_path / "output" / ".cr-queue.json").read_text())["waiting"] == []
