"""How the Studio runs a Ranking Short (local_runtime/cr_shorts.py) on many customers' Macs and PCs: UTF-8 and low
priority in the child, a stop explained in the creator's words (never a traceback tail), the published video put back
when a re-render stops, the Clip Board's choice snapshot passed on, and the pipeline's step words read plainly."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cr_shorts_runner", ROOT / "local_runtime" / "cr_shorts.py")
shorts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shorts)
INFO = {"format": "short", "label": "Ranking Shorts", "shorts": {"pipeline": "ranking"}}


class Engine:
    def __init__(self, job):
        self.job, self.calls = job, []

    def job_dir(self, title, style):
        return str(self.job)

    def _unpublish(self, job, title):
        self.calls.append("unpublish")

    def _republish_if_left(self, job, title):
        self.calls.append("republish")

    def publish_named_copy(self, job, title):
        self.calls.append("publish")


def pipeline(tmp_path, monkeypatch, body: str) -> Path:
    root = tmp_path / "shorts"
    (root / "ranking").mkdir(parents=True, exist_ok=True)
    (root / "ranking" / "make_ranking.py").write_text("import json, os, sys, pathlib\nwork = pathlib.Path(sys.argv[2])\n"
                                                      + body, encoding="utf-8")
    monkeypatch.setenv("CR_SHORTS_ROOT", str(root))
    return root


def run(tmp_path, extra='[[ranking={"ranks": 5}]]', title="Funniest Dog Moments", engine=None):
    said = []
    engine = engine or Engine(tmp_path / "job")
    job = shorts.run_short(engine, title, "cr-shorts-ranking", INFO, sink=said.append, extra=extra)
    return job, said, engine


FINISH = ("(work / 'short.mp4').write_bytes(b'mp4')\n"
          "(work / 'plan.json').write_text(json.dumps({'title': 'Ranking Funniest Dog Moments', 'narration': 'Bro.'}))\n")


def test_the_choice_snapshot_travels_and_only_safe_names_pass():
    extra = ('[[ranking={"board": "dogs-1a2b3c", "choice": "choice-20261021-101500-a1b2c3.json"}]]')
    assert shorts.ranking_options(extra) == {"board": "dogs-1a2b3c", "choice": "choice-20261021-101500-a1b2c3.json"}
    assert shorts.ranking_options('[[ranking={"board": "../../secrets", "choice": "../board.json"}]]') == {}
    assert shorts.ranking_options('[[ranking={"choice": "choice.json"}]]') == {}, "only a snapshot's name"


def test_the_child_runs_in_utf8_below_normal_priority_and_prints_emoji(tmp_path, monkeypatch):
    pipeline(tmp_path, monkeypatch,
             "(work / 'env.json').write_text(json.dumps({k: os.environ.get(k) for k in ('PYTHONUTF8', 'PYTHONIOENCODING',"
             " 'CR_RANKING')} | {'nice': os.getpriority(os.PRIO_PROCESS, 0) if hasattr(os, 'getpriority') else None}))\n"
             "print('12:00:00 looking kept: Room 209 \\U0001F47B (9.0/10)', flush=True)\n"
             "print('12:00:00 note skipped a Claude step: the writer', flush=True)\n"
             "print('12:00:00 edit #3: cleaning text 40/189 frames, about 6 min left', flush=True)\n" + FINISH)
    job, said, engine = run(tmp_path, extra='[[ranking={"ranks": 5, "choice": "choice-1-a.json"}]]')
    env = json.loads((job / "short" / "env.json").read_text())
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8:replace"
    assert json.loads(env["CR_RANKING"])["choice"] == "choice-1-a.json"
    if os.name != "nt":
        assert env["nice"] >= 10, "a Short never makes the creator's computer crawl"
    assert any(l.startswith("Watching the picks closely") and "Room 209 \U0001F47B" in l for l in said)
    assert "Note: skipped a Claude step: the writer" in said
    assert any(l.startswith("final render: taking burned-in text off") for l in said)
    assert engine.calls == ["unpublish", "publish"], "a finished Short is published and nothing is put back"


def test_a_stop_says_the_pipelines_own_reason_from_error_json(tmp_path, monkeypatch):
    pipeline(tmp_path, monkeypatch,
             "print('12:00:00 watching 3 of 26 watched', flush=True)\n"
             "(work / 'error.json').write_text(json.dumps({'code': 'watch_service', 'message': \"Clip watching is busy "
             "today across ChannelRecipe. Your progress is saved.\", 'action': 'Make the Short again later.', "
             "'saved': True}))\n"
             "raise SystemExit('Clip watching is busy today across ChannelRecipe. Your progress is saved.')\n")
    with pytest.raises(RuntimeError) as stop:
        run(tmp_path)
    assert str(stop.value) == ("Clip watching is busy today across ChannelRecipe. Your progress is saved. "
                               "Make the Short again later.")


def test_an_earlier_attempts_error_json_never_explains_this_one(tmp_path, monkeypatch):
    pipeline(tmp_path, monkeypatch, "raise SystemExit('YouTube is limiting downloads from this computer right now.')\n")
    (tmp_path / "job" / "short").mkdir(parents=True)
    (tmp_path / "job" / "short" / "error.json").write_text(json.dumps({"message": "an old reason"}))
    with pytest.raises(RuntimeError) as stop:
        run(tmp_path)
    assert str(stop.value) == "YouTube is limiting downloads from this computer right now."


def test_a_crash_reads_as_one_plain_line_and_the_details_are_kept(tmp_path, monkeypatch):
    pipeline(tmp_path, monkeypatch, "print('12:00:00 edit #2: cutting', flush=True)\n"
                                    "raise RuntimeError('ffmpeg could not read clip 2')\n")
    with pytest.raises(RuntimeError) as stop:
        run(tmp_path)
    message = str(stop.value)
    assert "Traceback" not in message and "File " not in message
    assert message.startswith("The Short stopped unexpectedly (RuntimeError: ffmpeg could not read clip 2)")
    assert "progress is saved" in message and "—" not in message
    assert "Traceback" in (tmp_path / "job" / "short" / "stopped.log").read_text(encoding="utf-8")


def test_a_rerender_that_stops_puts_the_published_video_back(tmp_path, monkeypatch):
    pipeline(tmp_path, monkeypatch, "raise SystemExit('YouTube is limiting downloads from this computer right now.')\n")
    engine = Engine(tmp_path / "job")
    with pytest.raises(RuntimeError):
        run(tmp_path, engine=engine)
    assert engine.calls == ["unpublish", "republish"]


def test_a_link_typed_as_the_title_is_refused_before_anything_runs(tmp_path, monkeypatch):
    pipeline(tmp_path, monkeypatch, "raise AssertionError('must not run')\n")
    engine = Engine(tmp_path / "job")
    with pytest.raises(RuntimeError, match="not a link"):
        run(tmp_path, title="https://www.youtube.com/watch?v=dN_RMA8LVi0", engine=engine)
    assert engine.calls == []


def test_windows_children_start_below_normal_priority(monkeypatch):
    monkeypatch.setattr(shorts.os, "name", "nt")
    flags = shorts.low_priority()
    monkeypatch.undo()
    assert flags == {"creationflags": getattr(__import__("subprocess"), "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)}
