"""Edit countdown in the Studio (local_runtime/cr_ranking_edit.py): a finished Ranking Short's slots, lines and spans;
the changes written to short/edits.json; the Short made again in its own attempt folder (cr_shorts.run_short inside
cr_attempts.bind), one remake at a time."""
import json
import sys
import threading
import time
import types
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_ranking_edit

SLUG = "funniest-parrot-moments--26f5cfe4ae8c65e5ea12347a"


def make_job(tmp_path):
    job = tmp_path / "output" / "cr-shorts-ranking" / "_work" / SLUG
    (job / "short").mkdir(parents=True)
    picked = [{"slot": 3, "label": "Unknown caller 😨", "what": "parrot answers", "line": None},
              {"slot": 1, "label": "Promoted 🫡", "line": {"text": "Budget Rottweiler.", "alts": [{"text": "Cover me."}]}}]
    (job / "short" / "picked.json").write_text(json.dumps(picked))
    (job / "short" / "timeline.json").write_text(json.dumps({"clips": [{"slot": 3, "span": [0, 5.9]}, {"slot": 1, "span": [12.1, 19.1]}]}))
    (job / "short" / "short.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (job / "short" / "plan.json").write_text(json.dumps({"title": "Ranking The Funniest Parrot Moments"}))
    (job / "cr-project.json").write_text(json.dumps({"job_spec": {"title": "Funniest Parrot Moments", "style": "cr-shorts-ranking",
                                                                   "extra": "[[ranking={\"ranks\":3}]]", "features": {}}}))
    return job


def studio_for(job):
    app = Flask("ranking-edit-test")
    mv = types.SimpleNamespace(find_job=lambda slug: job if slug == SLUG else None,
                               STYLE_INFO={"cr-shorts-ranking": {"shorts": {"pipeline": "ranking"}}})
    return types.SimpleNamespace(app=app, mv=mv)


def test_the_countdown_and_a_remake_in_its_own_folder(tmp_path, monkeypatch):
    job = make_job(tmp_path)
    studio = studio_for(job)
    (tmp_path / "cr_ranking_edit.html").write_text("<script>const TOKEN = __LOCAL_TOKEN_JSON__;</script>")
    cr_ranking_edit._runs.clear()
    cr_ranking_edit.install(studio, types.SimpleNamespace(busy=lambda: False), tmp_path, "tok")
    client = studio.app.test_client()
    assert '"tok"' in client.get("/cr-ranking-edit?job=" + SLUG).get_data(as_text=True)
    got = client.get("/cr-ranking/edit/" + SLUG).get_json()
    assert got["title"] == "Ranking The Funniest Parrot Moments"
    assert [s["slot"] for s in got["slots"]] == [3, 1] and got["slots"][1]["alts"] == ["Cover me."]
    assert got["slots"][0]["line"] == "" and got["slots"][1]["span"] == [12.1, 19.1]
    assert client.get("/cr-ranking/edit/nope").status_code == 404
    assert client.post("/cr-ranking/edit/" + SLUG, json={}).status_code == 400

    seen, done = {}, threading.Event()
    def run_short(engine, title, style, info, features=None, sink=None, extra=""):
        seen.update(title=title, style=style, extra=extra, folder=engine.job_dir(title, style))
        sink("Writing the narrator's lines")
        done.set()
    monkeypatch.setitem(sys.modules, "cr_shorts", types.SimpleNamespace(run_short=run_short))
    bound = {}
    class Bind:
        def __init__(self, engine, spec):
            self.engine, self.spec = engine, spec
        def __enter__(self):
            bound.update(self.spec)
            self.engine.job_dir = lambda t, s="": job if self.spec.get("cr_attempt_slug") == SLUG else tmp_path / "fresh"
        def __exit__(self, *a):
            return False
    monkeypatch.setitem(sys.modules, "cr_attempts", types.SimpleNamespace(bind=Bind))
    r = client.post("/cr-ranking/edit/" + SLUG, json={"lines": {"1": "New line", "7": "x"}, "swap": [3, 9]})
    assert r.status_code == 200 and "1 line and 1 clip" in r.get_json()["message"]
    assert done.wait(5)
    assert json.loads((job / "short" / "edits.json").read_text()) == {"lines": {"1": "New line"}, "swap": [3]}
    assert seen["folder"] == job and bound["slug"] == SLUG and seen["title"] == "Funniest Parrot Moments"
    for _ in range(50):
        if not client.get("/cr-ranking/edit/" + SLUG).get_json()["running"]:
            break
        time.sleep(0.05)
    state = client.get("/cr-ranking/edit/" + SLUG).get_json()
    assert state["running"] is False and state["error"] == "" and "Writing the narrator's lines" in state["lines"][-1]
