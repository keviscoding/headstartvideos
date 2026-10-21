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
REAL_JS_CHECK = shorts.js_runtime_missing


@pytest.fixture(autouse=True)
def a_javascript_runtime(monkeypatch):
    """Every test computer has one unless a test says otherwise (CI's Node may be older than yt-dlp's minimum)."""
    monkeypatch.setattr(shorts, "js_runtime_missing", lambda then="make the Short again": "")


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
             "hasattr(os, 'nice') and os.nice(10)\n"                # what make_ranking does first (find.lower_priority)
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
        base = os.getpriority(os.PRIO_PROCESS, 0)
        assert env["nice"] >= 10 and (base + 10 >= 19 or env["nice"] == base + 10), \
            "nice 10 once: the runner's own nice on top of the pipeline's ran Shorts at 19"
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


# ── review round 2 (2026-10-21): nice once, the whole stop kept, a JavaScript runtime checked before anything is paid ──
import subprocess
import time
import types


@pytest.mark.skipif(not hasattr(os, "setpriority"), reason="POSIX nice")
def test_only_pipelines_that_do_not_lower_themselves_get_the_runners_nice():
    assert "ranking" in shorts.SELF_LOWERING, "make_ranking calls find.lower_priority itself"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        base = os.getpriority(os.PRIO_PROCESS, proc.pid)
        shorts.lower_priority(proc)
        shorts.lower_priority(proc)
        assert os.getpriority(os.PRIO_PROCESS, proc.pid) == max(10, base), "absolute, never added up"
    finally:
        proc.kill()
        proc.wait()


def test_a_deep_crash_keeps_its_whole_traceback_and_still_reads_as_a_crash(tmp_path, monkeypatch):
    """Only the last 40 lines were kept: a traceback longer than that lost its header, so its exception line was shown
    as if the pipeline had said it, and short/stopped.log missed what led up to the stop."""
    pipeline(tmp_path, monkeypatch,
             "for i in range(150):\n    print(f'12:00:{i % 60:02d} watching {i} of 150 watched', flush=True)\n"
             "def dig(n):\n    return dig(n - 1) if n else int('not a number')\n"
             "dig(40)\n")
    with pytest.raises(RuntimeError) as stop:
        run(tmp_path)
    message = str(stop.value)
    assert message.startswith("The Short stopped unexpectedly (ValueError: invalid literal for int()"), message
    log = (tmp_path / "job" / "short" / "stopped.log").read_text(encoding="utf-8")
    assert "watching 0 of 150 watched" in log and "Traceback (most recent call last)" in log


def test_a_handled_traceback_earlier_does_not_hide_the_pipelines_own_message(tmp_path, monkeypatch):
    pipeline(tmp_path, monkeypatch,
             "import traceback\ntry:\n    1 / 0\nexcept ZeroDivisionError:\n    traceback.print_exc()\n"
             "print('12:00:00 watching 3 of 26 watched', flush=True)\n"
             "raise SystemExit('Clip watching is busy today across ChannelRecipe. Your progress is saved.')\n")
    with pytest.raises(RuntimeError) as stop:
        run(tmp_path)
    assert str(stop.value) == "Clip watching is busy today across ChannelRecipe. Your progress is saved."


def test_no_javascript_runtime_stops_a_topic_short_before_anything_runs(tmp_path, monkeypatch):
    pipeline(tmp_path, monkeypatch, "raise AssertionError('must not run')\n")
    monkeypatch.setattr(shorts, "js_runtime_missing", lambda then="make the Short again": "Install Node.js 22.")
    engine = Engine(tmp_path / "job")
    with pytest.raises(RuntimeError, match="Install Node.js 22"):
        run(tmp_path, engine=engine)
    assert engine.calls == [] and not (tmp_path / "job").exists(), "nothing searched, watched, paid or moved"


def test_only_a_short_that_downloads_from_youtube_needs_the_runtime(tmp_path):
    boards = tmp_path / "output" / "ranking-boards" / "dogs-1a2b3c"
    boards.mkdir(parents=True)
    own = {"picks": [{"id": "m0", "file": "/clips/a.mp4"}, {"id": "m1", "file": "/clips/b.mp4", "linked": "tiktok"}]}
    (boards / "choice-1-a.json").write_text(json.dumps(own))
    (boards / "choice-2-b.json").write_text(json.dumps({"picks": own["picks"] + [{"id": "m2", "vid": "dN_RMA8LVi0"}]}))
    assert shorts.needs_youtube({}, home=tmp_path), "a topic Short searches and downloads YouTube"
    assert not shorts.needs_youtube({"board": "dogs-1a2b3c", "choice": "choice-1-a.json"}, home=tmp_path)
    assert shorts.needs_youtube({"board": "dogs-1a2b3c", "choice": "choice-2-b.json"}, home=tmp_path)


def fake_runtimes(monkeypatch, found: dict):
    """yt-dlp's own runtime checks, stood in: {name: (version, supported)}."""
    def kind(name, minimum=None):
        class Runtime:
            MIN_SUPPORTED_VERSION = minimum

            @property
            def info(self):
                if name not in found:
                    return None
                version, ok = found[name]
                return types.SimpleNamespace(name=name, version=version, supported=ok)
        return Runtime
    module = types.ModuleType("yt_dlp.utils._jsruntime")
    module.DenoJsRuntime, module.NodeJsRuntime = kind("deno", (2, 3, 0)), kind("node", (22, 0, 0))
    module.BunJsRuntime, module.QuickJsRuntime = kind("bun", (1, 2, 11)), kind("quickjs", (2023, 12, 9))
    utils = types.ModuleType("yt_dlp.utils")
    utils._jsruntime = module
    monkeypatch.setitem(sys.modules, "yt_dlp", types.ModuleType("yt_dlp"))
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils._jsruntime", module)
    monkeypatch.setitem(shorts._JS, "ok", False)
    monkeypatch.delenv("YTDLP_ARGS", raising=False)


def test_the_runtime_check_says_what_to_install_or_update(monkeypatch):
    fake_runtimes(monkeypatch, {})
    missing = REAL_JS_CHECK()
    assert "Install Node.js 22 or newer from nodejs.org" in missing and "make the Short again" in missing
    assert "Nothing was watched or charged" in missing and "—" not in missing
    fake_runtimes(monkeypatch, {"node": ("20.11.0", False)})
    old = REAL_JS_CHECK("find clips again")
    assert "this computer has Node.js 20.11.0" in old and "find clips again" in old
    fake_runtimes(monkeypatch, {"node": ("20.11.0", False), "deno": ("2.6.6", True)})
    assert REAL_JS_CHECK() == "" and shorts._JS["ok"], "Deno in the Studio's own Python is enough"
    fake_runtimes(monkeypatch, {})
    monkeypatch.setenv("YTDLP_ARGS", "--js-runtimes node:/opt/node/bin/node")
    assert REAL_JS_CHECK() == "", "the creator chose their runtime: yt-dlp is the judge"


def test_the_runtime_check_never_refuses_on_a_guess(monkeypatch):
    monkeypatch.setitem(shorts._JS, "ok", False)
    monkeypatch.delenv("YTDLP_ARGS", raising=False)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils._jsruntime", None)     # a yt-dlp without these checks
    assert REAL_JS_CHECK() == ""
