"""A 3D video stopped in its picture review can finish with the review so far (Swapnil, Claude Pro, 2026-10-30: an
18-minute 3D video stopped at Claude's limit in round 3 of 3 with rounds 1-2 done). Reviewed sections replay from their
saved answers as always; the rest are kept as they are with no Claude call, and a round with nothing answered isn't
previewed. Only the creator's choice does it, for that video only. Fakes only: no engine, no GPU, no Claude."""
import json
import os
import shutil
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_claude  # noqa: E402
import cr_recover  # noqa: E402
import cr_studio_tools  # noqa: E402
import cr_threed_resume as resume  # noqa: E402

PROJECT = "crv_" + "f" * 24
CFG = {"review_rounds": 3, "preview_frac": "0.5", "model": "claude-opus", "lean_call_scenes": 24, "sub": 6}


class Stop(BaseException):
    """Claude's usage limit, as cr_claude raises it (a BaseException the engine's handlers don't catch)."""


def fake_threed(stop_at=None):
    """Shaped like threed.review, with its two Exception handlers: a review section whose call fails keeps its shots
    (_safe), and a preview round that fails sends the board on as it is."""
    mod = types.ModuleType("threed")
    seen = {"renders": [], "calls": [], "labels": []}

    def review_prompt(title, board, sheet_rows, words, issues, round_no, rounds, language_line="", context="",
                      checks=None, full_board=None, catalog=None, look=None):
        ids = [s["id"] for m in board["moments"] for s in m["scenes"]]
        return f"review {title} round {round_no}/{rounds} shots {ids} rows {sheet_rows} issues {issues}"
    mod.SB = types.SimpleNamespace(_json_object=json.loads, review_prompt=review_prompt)

    def _render(engine, video, outdir, args, what, total_scenes=0, progress=None):
        seen["renders"].append(what)
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        scenes = []
        for s in video["scenes"]:
            f = outdir / f"{s['id']}_0.50.jpg"
            f.write_bytes(os.urandom(64))
            scenes.append({"id": s["id"], "status": "preview", "stills": [{"t": 0.5, "file": str(f)}]})
        return {"scenes": scenes}
    mod._render = _render

    def _look(engine, prompt, images, model, max_tokens):
        seen["calls"].append(prompt)
        seen["labels"].append(cr_claude.stage_label())
        if stop_at is not None and len(seen["calls"]) == stop_at:
            raise Stop("usage limit")
        ids = prompt.split("shots ")[1].split(" rows")[0]
        return json.dumps({"fix": json.loads(ids.replace("'", '"'))[:1]})
    mod._look = _look
    mod.sections = lambda moments, cfg: [[m] for m in moments]
    mod._video = lambda board, sub=None, preset=None: {"scenes": [dict(s) for m in board["moments"]
                                                                  for s in m["scenes"]]}

    def review(engine, job, style, title, board, sents, context, cfg, catalog, force):
        f = job / "threed" / "board_reviewed.json"
        if f.exists() and not force:
            return json.loads(f.read_text(encoding="utf-8"))["board"]
        open_ids = None
        for rn in range(1, cfg["review_rounds"] + 1):
            todo = [dict(m, scenes=[s for s in m["scenes"] if open_ids is None or s["id"] in open_ids])
                    for m in board["moments"]]
            todo = [m for m in todo if m["scenes"]]
            if not todo:
                break
            try:
                rep = mod._render(engine, mod._video({"moments": todo}), job / "threed" / "review" / f"r{rn}",
                                  ["--preview-frac", cfg["preview_frac"]], f"previews round {rn}")
            except Exception:                                          # noqa: BLE001 - the engine's own handler
                break
            issues = {s["id"]: [] for s in rep["scenes"]}
            nxt = set()
            for part in (mod.sections(todo, cfg) if rn == 1 else [todo]):
                prompt = mod.SB.review_prompt(title, {"moments": part}, ["row"], {}, issues, rn, cfg["review_rounds"],
                                              "lang", context, checks={}, full_board=board, catalog=catalog)
                try:
                    reply = mod.SB._json_object(mod._look(engine, prompt, [], cfg["model"], 32000))
                except (Exception, SystemExit):                        # the engine's _safe: the section keeps its shots
                    continue
                for m in board["moments"]:
                    for s in m["scenes"]:
                        if s["id"] in reply["fix"]:
                            s["v"] = s.get("v", 0) + 1
                            nxt.add(s["id"])
            open_ids = nxt
        f.write_text(json.dumps({"board": board, "rounds": rn}), encoding="utf-8")
        return board
    mod.review = review
    return mod, seen


def board():
    return {"cast": [], "moments": [{"id": k, "scenes": [{"id": f"m{k}s{i}", "dur": 4.0} for i in range(2)]}
                                    for k in range(3)]}


def run(mod, job):
    return mod.review(None, job, "cr-3d", "Nanga Parbat", board(), [], "ctx", dict(CFG), {"kinds": {}}, False)


@pytest.fixture
def job(tmp_path):
    j = tmp_path / "nanga-parbat"
    (j / "threed").mkdir(parents=True)
    return j


def stopped(job, stop_at):
    mod, seen = fake_threed(stop_at=stop_at)
    resume.install(mod)
    with pytest.raises(Stop):
        run(mod, job)
    return seen


def test_stopped_in_the_last_round_it_finishes_with_no_claude_call_and_no_render(job):
    stopped(job, 5)                                           # round 1: 3 calls, round 2: 1, round 3's call stops
    resume.choose_finish(job)
    again, seen = fake_threed()
    resume.install(again)
    done = run(again, job)
    assert seen["calls"] == [], "the stopped round-3 call is skipped; rounds 1-2 replay from their saved answers"
    assert seen["renders"] == [], "every round's previews were kept: nothing rendered again"
    assert (job / "threed" / "board_reviewed.json").exists(), "the review ends and the video goes on"
    fixed = {s["id"]: s.get("v", 0) for m in done["moments"] for s in m["scenes"]}
    assert fixed == {"m0s0": 2, "m0s1": 0, "m1s0": 1, "m1s1": 0, "m2s0": 1, "m2s1": 0}, \
        "rounds 1-2's fixes are kept; round 3's never-made fix isn't"
    assert not resume.finishing(job), "the choice was for that review only"


def test_answers_an_updated_engine_cant_reuse_stop_the_review_after_one_preview_round(job, monkeypatch):
    stopped(job, 5)
    resume.choose_finish(job)
    monkeypatch.setattr(resume, "engine_version", lambda threed, what: "an updated engine")
    again, seen = fake_threed()
    resume.install(again)
    run(again, job)
    assert seen["calls"] == [] and seen["renders"] == ["previews round 1"], \
        "round 1's answers don't match the new engine: rounds 2 and 3 aren't previewed only to be skipped"


def test_the_clip_repair_after_the_review_is_named_apart_and_still_needs_a_yes(job, tmp_path):
    mod, seen = fake_threed()
    resume.install(mod)
    mod._look(None, "frame these black clips: shots ['m0s0'] rows", [], "claude-opus", 8000)   # repair_pass
    assert seen["labels"] == [resume.REPAIR_LABEL]
    home = tmp_path / "home"
    card = card_job(home)
    resume.choose_finish(card)
    d = cr_recover.diagnose(home, card, server_stages=stuck(home, ["3D picture review, round 3", resume.REPAIR_LABEL]))
    assert [s["label"] for s in d["claude_stages"] if s["needs_approval"]] == [resume.REPAIR_LABEL]


def test_a_round_with_nothing_answered_is_not_previewed(job):
    stopped(job, 4)                                           # round 2's only call stops
    shutil.rmtree(job / "threed" / resume.KEEP / "previews")  # e.g. a renderer that died mid-round
    resume.choose_finish(job)
    again, seen = fake_threed()
    resume.install(again)
    run(again, job)
    assert seen["calls"] == [] and seen["renders"] == ["previews round 1"], \
        "round 1 is previewed to replay its saved answers; round 2 had none, so no previews and no review"


def test_without_the_choice_a_resume_tries_the_stopped_call_again(job):
    stopped(job, 5)
    again, seen = fake_threed()
    resume.install(again)
    run(again, job)
    assert len(seen["calls"]) == 1 and "round 3/3" in seen["calls"][0]


def test_a_finished_video_forgets_the_choice(job):
    resume.choose_finish(job)
    assert resume.finishing(job)
    (job / cr_recover.RUN_FILE).write_text(json.dumps({"started": 1.0}), encoding="utf-8")
    cr_recover.run_ended(job, {"state": "done"})
    assert not resume.finishing(job)


def stuck(home, labels):
    folder = Path(home) / "output" / "cr-claude-cache" / PROJECT
    folder.mkdir(parents=True, exist_ok=True)
    ids = []
    for k, label in enumerate(labels):
        sid = "cc_" + f"{label}{k}".encode().hex().ljust(64, "0")[:64]
        (folder / f"{sid}.json").write_text(json.dumps({"state": "failed", "label": label, "error_code": "usage_limit"}),
                                            encoding="utf-8")
        ids.append(sid)
    return [{"stage_id": s, "status": "failed", "error_code": "usage_limit", "kind": "vision", "model_role": "visual",
             "can_retry": True, "attempts": 1} for s in ids]


def card_job(tmp_path):
    job = tmp_path / "output" / "cr-3d" / "_work" / "nanga-parbat--abcd1234"
    (job / "threed" / resume.KEEP).mkdir(parents=True)
    (job / "cr-project.json").write_text(json.dumps({"id": PROJECT, "brain_mode": "claudecode", "status": "error",
                                                     "title": "Nanga Parbat"}), encoding="utf-8")
    (job / "threed" / resume.KEEP / "progress.json").write_text(json.dumps(
        {"round": 3, "rounds": 3, "answered": {"3": ["abc"]}}), encoding="utf-8")
    return job


def test_the_stopped_card_offers_it_and_the_choice_needs_no_yes(tmp_path):
    job = card_job(tmp_path)
    server = stuck(tmp_path, ["3D picture review, round 3"] * 3)
    d = cr_recover.diagnose(tmp_path, job, server_stages=server)
    fin = d["threed_finish"]
    assert d["needs_approval"] and fin["available"] and not fin["chosen"]
    assert fin["note"].startswith("Review rounds 1-2 of 3 are finished and their fixes are kept; 1 part of round 3 "
                                  "is reviewed too. The shots Claude hasn't checked yet go to the final render")
    resume.choose_finish(job)
    d = cr_recover.diagnose(tmp_path, job, server_stages=server)
    assert d["threed_finish"]["chosen"] and not d["needs_approval"] and d["free_to_resume"]
    assert [g["line"] for g in d["groups"]] == [
        "3D picture review, round 3 (3 calls): Skipped: you chose to finish with the 3D review so far."]


def test_another_stopped_step_still_needs_its_yes(tmp_path):
    job = card_job(tmp_path)
    server = stuck(tmp_path, ["3D picture review, round 3", "Thumbnail check"])
    resume.choose_finish(job)
    d = cr_recover.diagnose(tmp_path, job, server_stages=server)
    assert d["needs_approval"] and [s["label"] for s in d["claude_stages"] if s["needs_approval"]] == ["Thumbnail check"]


def test_a_stop_outside_the_3d_review_doesnt_offer_it(tmp_path):
    job = card_job(tmp_path)
    d = cr_recover.diagnose(tmp_path, job, server_stages=stuck(tmp_path, ["3D shot planning"]))
    assert d["threed_finish"] == {"available": False, "chosen": False}


def test_a_finished_review_doesnt_offer_it(tmp_path):
    job = card_job(tmp_path)
    (job / "threed" / resume.KEEP / "progress.json").write_text(json.dumps({"round": 3, "rounds": 3, "finished": True}),
                                                                 encoding="utf-8")
    resume.choose_finish(job)
    d = cr_recover.diagnose(tmp_path, job, server_stages=stuck(tmp_path, ["3D picture review, round 3"]))
    assert d["threed_finish"] == {"available": False, "chosen": False} and d["needs_approval"]


def test_resume_video_passes_the_creators_choice(monkeypatch):
    sent = []

    def call(method, path, body=None, **kwargs):
        sent.append((method, path, body))
        if path.startswith("/cr-workspace/project/"):
            return {"available_for_resume": True, "project_id": PROJECT, "title": "Nanga Parbat"}
        return {"queued": []}
    monkeypatch.setattr(cr_studio_tools, "call", call)
    monkeypatch.setattr(cr_studio_tools, "same_studio", lambda: None)
    out = cr_studio_tools.tool_resume_video({"id": "video:nanga-parbat--abcd1234", "threed_review": "finish"})
    assert ("POST", "/cr-workspace/threed-finish/nanga-parbat--abcd1234", {"finish": True}) in sent
    assert out["resumed"] and out["threed_review"] == "finish"
    with pytest.raises(cr_studio_tools.Refused):
        cr_studio_tools.tool_resume_video({"id": "video:nanga-parbat--abcd1234", "threed_review": "skip-all"})
