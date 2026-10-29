"""A 3D video that stopped part-way carries on where it stopped (Swapnil, 2026-10-29): the review's finished rounds and
sections are never paid again, a round's previews are not rendered again, finished final clips are kept, and the
stopped card names its steps once with a count. Fakes only: no engine, no GPU, no Claude."""
import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_allowance  # noqa: E402
import cr_claude  # noqa: E402
import cr_claude_use  # noqa: E402
import cr_recover  # noqa: E402
import cr_threed_resume as resume  # noqa: E402


class Stop(BaseException):
    """Claude's usage limit, as cr_claude raises it (a BaseException the engine's handlers don't catch)."""


def fake_threed(stop_at=None, broken=None):
    """An engine shaped like threed.review: per round, previews (_render) then one review call per section (_look) with
    the prompt from SB.review_prompt; a reply fixes the shots it names; the board is saved only after the last round."""
    mod = types.ModuleType("threed")
    seen = {"renders": [], "calls": [], "labels": [], "stills": 0}

    def _json_object(raw):
        return json.loads(raw)

    def review_prompt(title, board, sheet_rows, words, issues, round_no, rounds, language_line="", context="",
                      checks=None, full_board=None, catalog=None, look=None):
        ids = [s["id"] for m in board["moments"] for s in m["scenes"]]
        return f"review {title} round {round_no}/{rounds} shots {ids} rows {sheet_rows} issues {issues}"
    mod.SB = types.SimpleNamespace(_json_object=_json_object, review_prompt=review_prompt)

    def _render(engine, video, outdir, args, what, total_scenes=0, progress=None):
        seen["renders"].append(what)
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        scenes = []
        for k, s in enumerate(video["scenes"]):
            if broken == "died" and k == 1:
                break                                       # the renderer died mid-round (exit 5 on the pod)
            if broken == "error" and k == 1:
                scenes.append({"id": s["id"], "status": "error", "error": "GPU process crashed"})
                continue
            seen["stills"] += 1
            f = outdir / f"{s['id']}_0.50.jpg"
            f.write_bytes(os.urandom(64))                   # a GPU render is never byte-identical
            scenes.append({"id": s["id"], "status": "preview", "stills": [{"t": 0.5, "file": str(f)}]})
        return {"scenes": scenes, "_s": 1.0}
    mod._render = _render

    def _look(engine, prompt, images, model, max_tokens):
        seen["calls"].append(prompt)
        seen["labels"].append(cr_claude.stage_label())
        if stop_at is not None and len(seen["calls"]) == stop_at:
            raise Stop("usage limit")
        ids = prompt.split("shots ")[1].split(" rows")[0]
        return json.dumps({"fix": json.loads(ids.replace("'", '"'))[:1]})
    mod._look = _look

    def sections(moments, cfg):
        return [[m] for m in moments]
    mod.sections = sections

    def _video(board, sub=None, preset=None):
        return {"cast": board.get("cast", []), "scenes": [dict(s, render={"sub": sub}) for m in board["moments"]
                                                          for s in m["scenes"]]}
    mod._video = _video

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
            rep = mod._render(engine, mod._video({"moments": todo}), job / "threed" / "review" / f"r{rn}",
                              ["--preview-frac", cfg["preview_frac"]], f"previews round {rn}")
            issues = {s["id"]: [] for s in rep["scenes"]}
            nxt = set()
            for part in (mod.sections(todo, cfg) if rn == 1 else [todo]):     # later rounds: the open shots packed
                prompt = mod.SB.review_prompt(title, {"moments": part}, ["row"], {}, issues, rn, cfg["review_rounds"],
                                              "lang", context, checks={}, full_board=board, catalog=catalog)
                reply = mod.SB._json_object(mod._look(engine, prompt, [], cfg["model"], 32000))
                for m in board["moments"]:
                    for s in m["scenes"]:
                        if s["id"] in reply["fix"]:
                            s["v"] = s.get("v", 0) + 1
                            nxt.add(s["id"])
            open_ids = nxt
        f.write_text(json.dumps({"board": board}), encoding="utf-8")
        return board
    mod.review = review
    return mod, seen


def board():
    return {"cast": [], "moments": [{"id": k, "t0": 10.0 * k, "t1": 10.0 * k + 8,
                                     "scenes": [{"id": f"m{k}s{i}", "dur": 4.0} for i in range(2)]}
                                    for k in range(3)]}


CFG = {"review_rounds": 3, "preview_frac": "0.5", "model": "claude-opus", "lean_call_scenes": 24, "sub": 6}


def run(mod, job, b=None, force=False, cfg=CFG):
    return mod.review(None, job, "cr-3d", "The Vault", b or board(), [], "ctx", dict(cfg), {"kinds": {}}, force)


@pytest.fixture
def job(tmp_path):
    j = tmp_path / "the-vault"
    (j / "threed").mkdir(parents=True)
    return j


def test_a_stop_mid_review_carries_on_from_where_it_stopped_and_pays_nothing_twice(job):
    whole, whole_seen = fake_threed()
    resume.install(whole)
    (job.parent / "clean" / "threed").mkdir(parents=True)
    clean = run(whole, job.parent / "clean")
    assert len(whole_seen["calls"]) == 5                      # round 1: 3 sections, round 2: 1, round 3: 1

    mod, seen = fake_threed(stop_at=4)                        # Claude's limit: round 2's first call
    resume.install(mod)
    with pytest.raises(Stop):
        run(mod, job)
    assert seen["renders"] == ["previews round 1", "previews round 2"] and len(seen["calls"]) == 4
    assert not (job / "threed" / "board_reviewed.json").exists()
    left = cr_claude_use.threed_review(job)
    assert left["round"] == 2 and left["rounds"] == 3 and left["left"] >= 1 and not left["finished"]

    again, seen2 = fake_threed()                              # Try again and carry on
    resume.install(again)
    done = run(again, job)
    assert seen2["renders"] == ["previews round 3"], "rounds 1 and 2 keep their previews: nothing rendered again"
    assert len(seen2["calls"]) == 2, "only round 2's stopped call and round 3 reach Claude"
    assert done == clean, "the carried-on review ends exactly where an uninterrupted one does"
    assert cr_claude_use.threed_review(job)["finished"]
    assert not (job / "threed" / resume.KEEP / "previews").exists(), "a finished review's previews are let go"


def test_a_section_answer_is_reused_only_for_the_same_board_round_and_settings(job):
    mod, seen = fake_threed(stop_at=2)
    resume.install(mod)
    with pytest.raises(Stop):
        run(mod, job)
    changed = board()
    changed["moments"][0]["scenes"][0]["dur"] = 5.0           # the director changed a shot: nothing is reused
    again, seen2 = fake_threed()
    resume.install(again)
    run(again, job, changed)
    assert len(seen2["calls"]) == 5 and seen2["renders"][0] == "previews round 1"
    other, seen3 = fake_threed()                              # another model: its own answers
    resume.install(other)
    (job / "threed" / "board_reviewed.json").unlink()
    run(other, job, cfg=dict(CFG, model="claude-sonnet"))
    assert len(seen3["calls"]) == 5


def test_a_forced_remake_reuses_nothing(job):
    mod, _ = fake_threed()
    resume.install(mod)
    run(mod, job)
    again, seen = fake_threed()
    resume.install(again)
    run(again, job, force=True)
    assert len(seen["calls"]) == 5 and len(seen["renders"]) == 3


def test_review_calls_are_named_for_the_stopped_card(job):
    mod, seen = fake_threed()
    resume.install(mod)
    run(mod, job)
    assert seen["labels"][:3] == ["3D picture review, round 1"] * 3 and seen["labels"][3] == "3D picture review, round 2"
    assert cr_claude.stage_label() == "", "the name lives only while its call runs"


def test_calls_left_counts_this_rounds_unanswered_sections_and_later_rounds_at_most():
    p = {"rounds": 3, "round": 1, "per": 24, "sections": {"1": 18}, "scenes": {"1": 138}, "answered": {"1": ["a"] * 8}}
    assert resume.calls_left(p) == {"round": 1, "rounds": 3, "left": 10 + 2 * 6, "finished": False}
    p.update(round=2, scenes={"1": 138, "2": 30}, answered={"1": ["a"] * 18, "2": []})
    assert resume.calls_left(p)["left"] == 2 + 2
    assert resume.calls_left(dict(p, finished=True))["left"] == 0


# ── final clips ───────────────────────────────────────────────────────────────────────────────────────────────────

def fake_final(mod, stop_after=None, unbuildable=()):
    """The engine's final_render, shaped like it: its stamp is keyed on the whole board and written at the end; with
    no stamp it clears the folder; each clip is written whole (as render.py's .part.mp4 + replace)."""
    rendered = []

    def final_render(engine, job, board, cfg, force):
        out = job / "threed" / "final"
        video = mod._video(board, cfg["sub"])
        key = resume.stamp_key(video)
        st = out / "stamp.json"
        have = {}
        if st.exists() and not force:
            got = json.loads(st.read_text(encoding="utf-8"))
            if got["key"] == key:
                have = got["mp4"]
        todo = [s for s in video["scenes"] if s["id"] not in have]
        if not todo:
            return have
        if out.exists() and not have:
            import shutil
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "video.json").write_text(json.dumps(video), encoding="utf-8")
        got = dict(have)
        for s in todo:
            if stop_after is not None and len(rendered) == stop_after:
                raise Stop("Studio closed")
            rendered.append(s["id"])
            if s["id"] in unbuildable:
                continue                                    # a scene the engine can't build: no clip
            (out / f"{s['id']}.mp4").write_bytes(b"clip " + s["id"].encode())
            got[s["id"]] = str(out / f"{s['id']}.mp4")
        st.write_text(json.dumps({"key": key, "mp4": got}), encoding="utf-8")
        return got
    mod.final_render = final_render
    return rendered


def test_finished_final_clips_are_kept_across_a_resume_and_a_changed_shot_is_rendered_again(job):
    mod, _ = fake_threed()
    rendered = fake_final(mod, stop_after=4)
    resume.install(mod)
    with pytest.raises(Stop):
        mod.final_render(None, job, board(), CFG, False)
    assert rendered == ["m0s0", "m0s1", "m1s0", "m1s1"]

    mod2, _ = fake_threed()
    rendered2 = fake_final(mod2)
    resume.install(mod2)
    got = mod2.final_render(None, job, board(), CFG, False)
    assert rendered2 == ["m2s0", "m2s1"], "the four finished clips are kept"
    assert sorted(got) == sorted(s["id"] for m in board()["moments"] for s in m["scenes"])
    assert all(Path(p).is_file() for p in got.values()) and not (job / "threed" / resume.SIDE).exists()

    changed = board()
    changed["moments"][1]["scenes"][0]["dur"] = 6.0
    mod3, _ = fake_threed()
    rendered3 = fake_final(mod3)
    resume.install(mod3)
    got = mod3.final_render(None, job, changed, CFG, False)
    assert rendered3 == ["m1s0"] and len(got) == 6

    mod4, _ = fake_threed()
    rendered4 = fake_final(mod4)
    resume.install(mod4)
    assert len(mod4.final_render(None, job, changed, CFG, False)) == 6 and rendered4 == []


def test_clips_from_before_this_studio_follow_the_engines_own_stamp(job):
    mod, _ = fake_threed()
    rendered = fake_final(mod)
    mod.final_render(None, job, board(), CFG, False)            # Studio 111: no clip record of Studio's
    mod2, _ = fake_threed()
    rendered2 = fake_final(mod2)
    resume.install(mod2)
    assert len(mod2.final_render(None, job, board(), CFG, False)) == 6 and rendered2 == []
    assert (job / "threed" / resume.CLIPS).exists()


# ── the stopped card and the stop message ─────────────────────────────────────────────────────────────────────────

PROJECT = "crv_" + "b" * 24


def stuck(home, n, label, code="usage_limit"):
    folder = Path(home) / "output" / "cr-claude-cache" / PROJECT
    folder.mkdir(parents=True, exist_ok=True)
    ids = []
    for k in range(n):
        sid = "cc_" + f"{label}{k}".encode().hex().ljust(64, "0")[:64]
        (folder / f"{sid}.json").write_text(json.dumps({"state": "failed", "label": label, "seconds": 90.0,
                                                        "error_code": code}), encoding="utf-8")
        ids.append(sid)
    return ids


def test_the_stopped_card_names_each_kind_of_step_once_with_a_count(tmp_path):
    job = tmp_path / "output" / "cr-3d" / "_work" / "the-vault--abcd1234"
    (job / "threed").mkdir(parents=True)
    (job / "cr-project.json").write_text(json.dumps({"id": PROJECT, "brain_mode": "claudecode", "status": "error",
                                                     "title": "The Vault"}), encoding="utf-8")
    ids = stuck(tmp_path, 3, "3D picture review, round 1") + stuck(tmp_path, 1, "3D shot planning")
    server = [{"stage_id": s, "status": "failed", "error_code": "usage_limit", "kind": "vision", "model_role": "visual",
               "can_retry": True, "attempts": 1} for s in ids]
    d = cr_recover.diagnose(tmp_path, job, server_stages=server,
                            allowance={"reset_at": 9e12, "reset_label": "30 Oct, 03:00 AM (Europe/London)"})
    lines = sorted(g["line"] for g in d["groups"])
    assert lines == ["3D picture review, round 1: 3 calls stopped at your Claude plan's limit.",
                     "3D shot planning: 1 call stopped at your Claude plan's limit."]
    assert d["usage_line"] == ("Claude stopped at your plan's usage limit. When your Claude limit resets (30 Oct, 03:00 "
                               "AM (Europe/London)), press Try again and carry on: everything already made is kept, "
                               "including finished 3D review rounds.")
    assert "create a new video" not in d["what_to_tell_them"]
    use = d.get("claude_use") or cr_claude_use.summary(tmp_path, job, PROJECT)
    assert use["calls"] == 4 and use["line"].startswith("Claude so far for this video: 4 calls, about 6 min")


def test_a_usage_limit_stop_says_carry_on_not_create_a_new_video(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {"ready": True, "account_fingerprint": "a"})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (None, "test"))
    bridge = types.SimpleNamespace(HOME=tmp_path, load_settings=lambda: {"brain_mode": "claudecode", "costs_accepted": True},
                                   current_project=lambda: {"id": PROJECT},
                                   authorize_stage=lambda s: {"execute": True, "authorization_token": "t",
                                                              "selected_model": cr_claude.GENERAL_MODEL},
                                   finish_stage=lambda r: None)
    run = lambda args, **k: subprocess.CompletedProcess(args, 1, "You've hit your session limit · resets 9:40am "
                                                        "(Europe/London)", "")
    runtime = cr_claude.Runtime(types.SimpleNamespace(), bridge, "/fake/claude", run)
    with cr_claude.labelled("3D picture review, round 2"):
        with pytest.raises(cr_claude.ClaudeStageStopped) as stopped:
            runtime.run(["/fake/claude", "--print"], input="prompt")
    said = str(stopped.value)
    assert "usage limit" in said and "press Try again and carry on" in said and "including finished 3D review rounds" in said
    assert "create a new video" not in said and "(Europe/London))" in said
    [cache] = list((tmp_path / "output" / "cr-claude-cache").rglob("*.json"))
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["label"] == "3D picture review, round 2" and isinstance(saved["seconds"], float)


def test_the_stop_message_without_a_reset_time():
    said = cr_allowance.stopped_message(None)
    assert said == ("Claude stopped at your plan's usage limit. When your Claude limit resets, press Try again and "
                    "carry on: everything already made is kept.")


def test_create_says_a_long_3d_video_can_take_more_than_one_usage_window():
    script = (Path(__file__).parents[1] / "local_runtime" / "cr_workspace.js").read_text(encoding="utf-8")
    assert "const LONG_3D_MINUTES = 8;" in script
    assert "can take more than one 5-hour usage window" in script and "Quicker 3D review uses less." in script
    assert "/cr-workspace/claude-use/" in script and "rec.groups" in script
    page = (Path(__file__).parents[1] / "local_runtime" / "cr_workspace.html").read_text(encoding="utf-8")
    assert 'id="crw-claude-use"' in page


# ── the review's findings (coordinator, 2026-10-29) ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("broken", ["died", "error"])
def test_a_round_the_renderer_did_not_finish_is_never_kept(job, broken):
    mod, seen = fake_threed(stop_at=2, broken=broken)
    resume.install(mod)
    with pytest.raises(Stop):
        run(mod, job)
    assert not (job / "threed" / resume.KEEP / "previews").exists() or not any(
        (job / "threed" / resume.KEEP / "previews").iterdir())
    again, seen2 = fake_threed()
    resume.install(again)
    run(again, job)
    assert seen2["renders"][0] == "previews round 1", "a broken round is rendered again"


def test_an_updated_engine_reuses_no_answer_or_preview(job, tmp_path):
    def versioned(version, stop_at=None):
        mod, seen = fake_threed(stop_at=stop_at)
        folder = tmp_path / f"engine-{version}"
        folder.mkdir(exist_ok=True)
        (folder / "threed.py").write_text(f"# engine {version}", encoding="utf-8")
        (folder / "render.py").write_text(f"# renderer {version}", encoding="utf-8")
        mod.__file__, mod.RENDER = str(folder / "threed.py"), folder / "render.py"
        resume.install(mod)
        return mod, seen
    old, _ = versioned("111", stop_at=4)
    with pytest.raises(Stop):
        run(old, job)
    new, seen = versioned("112")
    run(new, job)
    assert len(seen["calls"]) == 5 and seen["renders"][0] == "previews round 1"


def test_the_answer_key_follows_the_effort_claude_is_asked_for(job):
    mod, _ = fake_threed(stop_at=4)
    resume.install(mod)
    with pytest.raises(Stop):
        run(mod, job)
    again, seen = fake_threed()
    resume.install(again)
    with cr_claude.call_effort("low"):
        run(again, job)
    assert len(seen["calls"]) == 5, "another effort: its own answers"


def test_wrappers_pass_any_engine_signature_through(job):
    got = []

    def _look(engine, prompt, images, model, max_tokens, *, extra=None):
        got.append(extra)
        return "{}"

    def _render(engine, video, outdir, args, what, total_scenes=0, progress=None, *, gpu=None):
        got.append(gpu)
        return {"scenes": []}

    def _cc(engine, prompt, model, images=None, what="call", *, timeout=None):
        got.append((what, timeout, cr_claude.stage_label()))
        return "ok"
    mod = types.SimpleNamespace(SB=types.SimpleNamespace(_json_object=json.loads), _look=_look, _render=_render, _cc=_cc)
    resume.install(mod)
    assert mod._look(None, "p", [], "m", 10, extra=1) == "{}"
    assert mod._render(None, {"scenes": []}, job / "threed" / "final", ["--quality", "final"], what="final render",
                       gpu="x") == {"scenes": []}
    assert mod._cc(None, "p", "m", what="director", timeout=5) == "ok"
    assert got == [1, "x", ("director", 5, "3D shot planning")]


def test_kept_previews_that_cant_be_put_back_are_rendered_again(job, monkeypatch):
    mod, _ = fake_threed(stop_at=4)
    resume.install(mod)
    with pytest.raises(Stop):
        run(mod, job)
    if os.name != "nt":
        kept = next((job / "threed" / resume.KEEP / "previews").rglob("*.jpg"))
        assert kept.stat().st_nlink >= 2, "kept by a hard link, not a second copy"
    again, seen = fake_threed()
    resume.install(again)

    def full(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(resume, "_link", full)
    done = run(again, job)
    assert seen["renders"][:2] == ["previews round 1", "previews round 2"] and done["moments"]


def test_a_finished_final_render_is_left_to_the_engines_own_cache(job):
    mod, _ = fake_threed()
    rendered = fake_final(mod, unbuildable=("m2s1",))
    resume.install(mod)
    first = mod.final_render(None, job, board(), CFG, False)
    assert len(first) == 5 and "m2s1" in rendered
    (job / "threed" / "final" / "_before_repair").mkdir()
    (job / "threed" / "final" / "_before_repair" / "m0s0.mp4").write_bytes(b"before")
    mod2, _ = fake_threed()
    rendered2 = fake_final(mod2, unbuildable=("m2s1",))
    resume.install(mod2)
    again = mod2.final_render(None, job, board(), CFG, False)
    assert rendered2 == ["m2s1"], "only the scene that couldn't be built is tried again, as the engine does"
    assert len(again) == 5 and (job / "threed" / "final" / "_before_repair" / "m0s0.mp4").is_file()
    assert (job / "threed" / "final" / "stamp.json").is_file()


def test_the_reviewed_rounds_clause_is_for_review_stops_only(tmp_path):
    assert "3D review rounds" in cr_allowance.stopped_message("x", threed=True)
    source = (Path(__file__).parents[1] / "local_runtime" / "cr_claude.py").read_text(encoding="utf-8")
    assert 'threed=label.startswith("3D picture review")' in source


def test_the_card_promises_carrying_on_by_itself_only_when_chosen():
    d = {"fix_first": [], "needs_approval": True, "media": [], "reset_label": "30 Oct, 03:00 AM",
         "reset_passed": False, "claude_stages": [{"needs_approval": True}],
         "usage_line": cr_allowance.stopped_message("30 Oct, 03:00 AM", threed=True)}
    said = cr_recover._tell(dict(d, carry_on_after_reset=False), True, False, [1])
    assert "You can carry it on where it stopped then" in said and "by itself" not in said
    assert "Studio carries it on by itself then, as you chose." in cr_recover._tell(dict(d, carry_on_after_reset=True),
                                                                                   True, False, [1])
    script = (Path(__file__).parents[1] / "local_runtime" / "cr_workspace.js").read_text(encoding="utf-8")
    assert "you can carry it on where it stopped once your limit resets" in script
    assert "carries on where it stopped once your limit resets" not in script


def test_a_finished_video_lets_its_kept_previews_go(job):
    previews = job / "threed" / resume.KEEP / "previews" / "abc"
    previews.mkdir(parents=True)
    (previews / "report.json").write_text("{}", encoding="utf-8")
    (job / cr_recover.RUN_FILE).write_text(json.dumps({"started": 1.0}), encoding="utf-8")
    cr_recover.run_ended(job, {"state": "done"})
    assert not (job / "threed" / resume.KEEP / "previews").exists()


def test_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    def refuse(a, b):
        raise PermissionError(13, "in use")
    monkeypatch.setattr(resume.os, "replace", refuse)
    monkeypatch.setattr(resume.time, "sleep", lambda s: None)
    resume._write(tmp_path / "progress.json", {"a": 1})
    assert list(tmp_path.iterdir()) == []
