"""When Kie's video watcher fails, ChannelRecipe's own watcher takes the footage watching (cr_watch, Studio 106).

Customer ALEX (Studio 104): Kie.ai was his only watcher and it was down: 14 YouTube videos in a row failed ("Read timed
out (read timeout=300)", "Internal Server Error", "Service Unavailable"), footage logging took 6087 s, nothing fell
back and the UI never said so. The fake engine below follows youtube.py: _log_one tries each watcher with a key
(here only Kie), _kie_log counts failures in _KIE_DOWN and refuses at once after three."""
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_bridge
import cr_watch as watch

FAKE = r'''
import json
from pathlib import Path
_KIE_DOWN = {"fails": 0}
KIE = {"mode": "down", "calls": [], "watchers": ["kie"]}
N = 6
def _sec(v):
    if isinstance(v, (int, float)):
        return float(v)
    parts = str(v or "").strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return -1.0
def _clock(t):
    return f"{int(t) // 60:02d}:{t - (int(t) // 60) * 60:04.1f}"
def _safe(v): return v.replace(":", "_").replace("/", "_")
def settings(engine, style): return {"watcher": "kie"}
def analyst(prefer=None): return KIE["watchers"][0]
def watchers(prefer=None): return list(KIE["watchers"])
def focus_analysts(prefer=None): return list(KIE["watchers"])
def plan_focus(engine, title, needs, videos, work): return {v["id"]: None for v in videos}
def _low_copy(vid, work, span=None):
    if KIE.get("local") == "blocked":
        raise RuntimeError(f"could not fetch a 360p copy of {vid}")
    a, b = (float(span[0]), float(span[1])) if span else (0.0, 0.0)
    out = Path(work) / "low" / (f"{_safe(vid)}_{int(a)}_{int(b)}.mp4" if span else f"{_safe(vid)}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"low copy")
    return out
def _kie_log(vid, prompt, work, span=None):
    if _KIE_DOWN["fails"] >= 3:
        raise RuntimeError("kie's video watcher failed three videos in a row - this search goes on without it")
    _low_copy(vid, work, span)
    KIE["calls"].append(vid)
    if KIE["mode"] == "down":
        _KIE_DOWN["fails"] += 1
        raise RuntimeError("api.kie.ai HTTP 500: Internal Server Error")
    _KIE_DOWN["fails"] = 0
    return json.dumps({"video": "by kie", "shots": [{"start": "00:01.0", "end": "00:03.0", "shows": "kie shot"}]})
def _log_one(vid, work, prompt, span=None, prefer=None):
    text, last = None, None
    for who in (focus_analysts(prefer) if span else watchers(prefer)):
        try:
            if who == "kie":
                text = _kie_log(vid, prompt, work, span)
            else:
                raise RuntimeError(who + " is out of credit")
            break
        except Exception as e:
            last = e
    if text is None:
        raise last
    return json.loads(text)
def shots(vid, work, spans=None, cfg=None):
    cat = Path(work) / "catalog" / f"{_safe(vid)}.json"
    if cat.exists():
        return json.loads(cat.read_text(encoding="utf-8"))
    if spans:
        data = {"shots": [s for sp in spans for s in _log_one(vid, work, "This clip is cut from a longer video.",
                                                              tuple(sp), prefer="kie")["shots"]]}
    else:
        data = _log_one(vid, work, "Watch the whole video itself.", prefer="kie")
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(json.dumps(data), encoding="utf-8")
    return data
def fetch_clips(engine, script, job, style, force, title=""):
    ydir = Path(job) / "youtube"
    videos = [{"id": f"vid{i:08d}", "duration": 300} for i in range(N)]
    needs = [{"w": 0, "videos": [v["id"] for v in videos]}]
    plan = plan_focus(engine, title, needs, videos, ydir)
    out = {}
    for v in [x for x in needs[0]["videos"]]:
        try:
            out[v] = shots(v, ydir, plan.get(v), cfg={})
        except Exception as e:
            engine.log(f"  {v}: not watched - {e}")
    return out
'''


def engine_module():
    module = types.ModuleType("fake_youtube_kie")
    exec(compile(FAKE, "fake_youtube_kie", "exec"), module.__dict__)
    watch._install_budget(module)
    watch._install_fallback(module)
    return module


class Service:
    """ChannelRecipe's watch service, as cr_bridge.call answers it."""

    def __init__(self):
        self.posts, self.polls, self.refuse, self.running, self.lost = [], [], None, 0, 0
        self.remaining = 300

    def __call__(self, path, payload=None, settings=None):
        if path == "ranking/allowance":
            return {"used": 0, "limit": 300, "remaining": self.remaining}
        if self.refuse:
            raise RuntimeError(self.refuse)
        if payload is not None:
            assert path == "ranking/watch"
            self.posts.append(payload)
            job = f"rw_{len(self.posts):020d}"
            if self.running:
                return {"job": job, "status": "running"}
            return self.done(job, payload)
        self.polls.append(path)
        if self.lost:
            self.lost -= 1
            raise RuntimeError("That clip watch wasn't found.")
        return self.done(path.rsplit("/", 1)[-1], self.posts[-1])

    @staticmethod
    def done(job, payload):
        a = payload["start"]
        return {"job": job, "status": "done", "credits": 0,
                "log": {"video": "a shot log from ChannelRecipe", "shots": [
                    {"start": "00:10.0", "end": "00:14.0", "shows": f"window {a}"}]}}


@pytest.fixture
def run(tmp_path, monkeypatch):
    service = Service()
    monkeypatch.setattr(cr_bridge, "call", service)
    monkeypatch.setattr(watch, "_sleep", lambda s: None)
    monkeypatch.setitem(watch._home, "path", tmp_path)
    module = engine_module()
    engine = SimpleNamespace(lines=[], STYLE_WPM={})
    engine.log = engine.lines.append
    return SimpleNamespace(module=module, engine=engine, service=service, job=tmp_path / "job", home=tmp_path)


def said(lines, text):
    return [line for line in lines if text in line]


def test_kie_down_hands_every_watch_to_channelrecipe_and_stops_asking_kie_after_three(run):
    got = run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert len(got) == 6 and all(v["shots"] for v in got.values()), "every video is watched"
    # three failures open Kie's breaker; the first wave watches up to WORKERS_UPLOAD at once, so it may reach four
    assert 3 <= len(run.module.KIE["calls"]) <= watch.WORKERS_UPLOAD, "then Kie is never asked again for this video"
    posts = run.service.posts
    assert {p["use_credits"] for p in posts} == {False} and {p["mode"] for p in posts} == {"scout"}
    assert all(p["end"] - p["start"] <= 240 for p in posts)
    assert sorted({(p["start"], p["end"]) for p in posts}) == [(0, 240), (240, 300)], "a 5-minute video, whole"
    assert len(posts) == 12
    taken = said(run.engine.lines, "ChannelRecipe's own watcher takes over")
    assert len(taken) == 1 and "never your credits" in taken[0] and "Kie's video watcher isn't answering" in taken[0]
    assert "(300 left this month; at most 20 for this video)" in taken[0]
    assert said(run.engine.lines, "ChannelRecipe's watcher watched 6 video(s) with 12 of your plan's clip watches (at "
                                  "most 20 for a video) because Kie's watcher wasn't answering.")
    outcome = watch.last_outcome(run.home)
    assert outcome["channelrecipe_watched"] == 6 and outcome["kie_failed"] == len(run.module.KIE["calls"])
    assert outcome["note"] == ("On your last video, Kie's video watcher wasn't answering, so ChannelRecipe's watcher "
                               "watched 6 of its videos.")


def test_a_working_kie_watcher_never_touches_channelrecipe(run):
    run.module.KIE["mode"] = "up"
    got = run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert all(v["video"] == "by kie" for v in got.values())
    assert run.service.posts == [] and not said(run.engine.lines, "ChannelRecipe")
    assert watch.last_outcome(run.home) == {}


def test_used_up_clip_watches_stop_footage_watching_with_one_line_and_never_credits(run):
    run.service.refuse = ("You've used this month's 300 clip watches. Each further watch costs 0.02 credits; top up "
                          "credits or wait for next month.")
    got = run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert got == {}
    stopped = said(run.engine.lines, "footage watching stopped for this video")
    assert stopped == ["  YouTube: footage watching stopped for this video: Kie's watcher isn't answering and this "
                       "month's ChannelRecipe clip watches are used up (your credits were not used). It goes on with "
                       "the clips it already found."]
    assert watch.last_outcome(run.home)["note"].startswith("On your last video, Kie's video watcher wasn't answering "
                                                           "and footage watching stopped: this month's")


def test_channelrecipe_unreachable_stops_after_its_patience(run, monkeypatch):
    monkeypatch.setattr(watch, "CR_PATIENCE_S", 0)
    run.service.refuse = "Could not reach ChannelRecipe. Check your connection."
    run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    stopped = said(run.engine.lines, "footage watching stopped for this video")
    assert len(stopped) == 1 and "ChannelRecipe can't be reached from this computer right now" in stopped[0]


def test_three_failed_channelrecipe_watches_in_a_row_stop_it_for_the_video(run, monkeypatch):
    def broken(path, payload=None, settings=None):
        if payload is not None:
            return {"job": "rw_" + "1" * 20, "status": "failed", "error": "Gemini could not watch this clip."}
        raise AssertionError("no polls for a failed job")
    monkeypatch.setattr(cr_bridge, "call", broken)
    run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    stopped = said(run.engine.lines, "footage watching stopped for this video")
    assert len(stopped) == 1 and "ChannelRecipe's watcher failed three watches in a row" in stopped[0]


def test_a_running_job_is_polled_and_a_lost_one_asked_again_once(run):
    run.service.running, run.service.lost = 1, 1
    run.module.N = 1
    run.module.KIE["mode"] = "down"
    run.module._KIE_DOWN["fails"] = 0
    got = run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert len(got) == 1 and run.service.polls, "polled while running"


def test_each_video_asks_kie_again(run):
    run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert run.module._KIE_DOWN["fails"] >= 3
    run.module.KIE["mode"] = "up"
    run.module.KIE["calls"].clear()
    got = run.module.fetch_clips(run.engine, "word " * 300, run.home / "job2", "documentary", False)
    assert len(run.module.KIE["calls"]) == 6 and all(v["video"] == "by kie" for v in got.values())


def test_another_watcher_with_a_key_keeps_the_engines_own_order(run):
    run.module.KIE["watchers"] = ["gemini", "kie"]
    run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert run.service.posts == [], "the engine falls over between the customer's own watchers itself"


def test_a_stretch_is_watched_in_windows_on_the_stretchs_own_clock(run):
    """A stretch of a long video (400-700 s): two windows; shots come back on the clip's clock, as the engine asks."""
    module = run.module
    module.plan_focus = None                                   # not used: the engine's _log_one is called directly
    b = {"cr": {"watched": 0, "windows": 0, "kie_failed": 0, "said": False, "stopped": "", "down": 0}, "dur": {},
         "engine": run.engine}
    work = run.job / "youtube"
    with watch._budget_lock:
        watch._budget[watch._bkey(work)] = b
    try:
        module._KIE_DOWN["fails"] = 3
        got = module._log_one("vid00000001", work, "This clip is 05:00 long.", (400.0, 700.0), prefer="kie")
    finally:
        watch._budget.pop(watch._bkey(work), None)
    assert [(p["start"], p["end"]) for p in run.service.posts] == [(400, 640), (640, 700)]
    # the service answered "00:10.0" in each window: 10 s into each window = 10 s and 250 s into the stretch
    assert [s["start"] for s in got["shots"]] == ["00:10.0", "04:10.0"]


@pytest.mark.parametrize("answer,expected", [("00:10.0", 110.0), ("06:50.0", 110.0), ("05:00.0", 300.0)])
def test_whichever_clock_gemini_answers_on_lands_inside_the_window(answer, expected):
    """Window 400-640 of a stretch that starts at 300: an answer from the window's start (00:10 = 410 s), the video's
    own (06:50 = 410 s) or the stretch's (05:00 = 600 s, the only clock that puts it in the window) is placed on the
    stretch's clock."""
    import types as t
    module = t.SimpleNamespace(_sec=lambda v: int(str(v).split(":")[0]) * 60 + float(str(v).split(":")[1]),
                               _clock=lambda x: f"{int(x) // 60:02d}:{x - (int(x) // 60) * 60:04.1f}")
    rows = [{"start": answer, "end": answer, "shows": "x"}]
    rows[0]["end"] = f"{int(module._sec(answer) + 4) // 60:02d}:{(module._sec(answer) + 4) % 60:04.1f}"
    out = watch._on_clip_clock(module, rows, (400, 640), 300.0, 300.0)
    assert module._sec(out[0]["start"]) == pytest.approx(expected)


def test_archive_films_are_never_sent_to_channelrecipe(run):
    b = {"cr": {"watched": 0, "windows": 0, "kie_failed": 0, "said": False, "stopped": "", "down": 0}, "dur": {},
         "engine": run.engine}
    work = run.job / "youtube"
    watch._budget[watch._bkey(work)] = b
    try:
        with pytest.raises(RuntimeError, match="Internal Server Error"):
            run.module._log_one("ia:film/a.mp4", work, "Watch it.", prefer="kie")
    finally:
        watch._budget.pop(watch._bkey(work), None)
    assert run.service.posts == []


def test_progress_tells_claude_code_where_the_footage_watching_went(monkeypatch):
    import cr_studio_tools as tools
    status = {"state": "running", "done": False, "progress": 0.3, "lines": [
        "  YouTube: Kie's video watcher isn't answering (api.kie.ai HTTP 500); ChannelRecipe's own watcher takes over "
        "this video's footage watching. It uses your plan's monthly clip watches, never your credits.",
        "  YouTube: watched 4 of 24 videos (3 min so far; this video stops watching at 20 min)"]}
    monkeypatch.setattr(tools, "call", lambda method, path, body=None, **kw: status)
    out = tools._job_status("alex-video")
    assert out["footage_note"].startswith("YouTube: Kie's video watcher isn't answering")


def test_create_says_what_happens_when_kies_watcher_is_down():
    page = (ROOT / "local_runtime/cr_workspace.js").read_text(encoding="utf-8")
    assert "If Kie's video watcher isn't answering, ChannelRecipe's own watcher takes over" in page
    assert "footage_watcher?.note" in page


ENGINE = Path(__import__("os").environ.get("FRONTIER_TEST_ENGINE_DIR", Path.home() / "Downloads/ChannelRecipe studio"))


@pytest.fixture
def real_youtube(tmp_path, monkeypatch):
    """The owner's own youtube.py watch path (read only): _log_one, shots with its stretch() clock check, _kie_log with
    _KIE_DOWN, the watcher lists and the catalog; Kie's chat answers 500 every time, and the low copy is a stub."""
    import ast
    import json as _json
    import math
    import os
    import re
    import time
    from concurrent.futures import ThreadPoolExecutor
    source = ENGINE / "youtube.py"
    if not source.is_file():
        pytest.skip("Owner's private engine source is required; it is not published with these tests.")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"_sec", "_json_in", "_mmss", "_safe", "_watch_url", "watchers", "focus_analysts", "_log_one", "flagged", "shots",
             "_clock", "_chat_log", "_kie_upload", "_kie_log", "can_focus"}
    consts = {"_KIE_DOWN", "WATCHERS", "_PREFER", "CATALOG_SCHEMA", "SHOTS_PROMPT"}
    nodes = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in consts
                                                                        for t in n.targets)]
    nodes += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    module = types.ModuleType("real_youtube_watch")
    sent = []

    def post(url, **kwargs):
        sent.append(url)
        if "files" in kwargs:
            return SimpleNamespace(status_code=200, ok=True, raise_for_status=lambda: None, text="",
                                   json=lambda: {"code": 200, "data": {"downloadUrl": "https://files.example/w.mp4"}})
        return SimpleNamespace(status_code=500, ok=False, text="Internal Server Error", json=lambda: {})
    (tmp_path / "w.mp4").write_bytes(b"fixture")
    module.__dict__.update(Path=Path, json=_json, re=re, math=math, os=os, ThreadPoolExecutor=ThreadPoolExecutor,
                           requests=SimpleNamespace(post=post, exceptions=SimpleNamespace(Timeout=TimeoutError,
                                                                                          ConnectionError=ConnectionError)),
                           time=SimpleNamespace(sleep=lambda s: None, monotonic=time.monotonic),
                           _env=lambda name: "fixture-no-real-key" if name == "KIE_API_KEY" else "",
                           _low_copy=lambda vid, work, span=None: _engine_low_copy(vid, work, span),
                           _watch_copy=lambda p: p,
                           archiveorg=None)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), module.__dict__)

    def _engine_low_copy(vid, work, span=None):
        a, b = (float(span[0]), float(span[1])) if span else (0.0, 0.0)
        out = Path(work) / "low" / (f"{module._safe(vid)}_{int(a)}_{int(b)}.mp4" if span else f"{module._safe(vid)}.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fixture")
        return out
    watch._install_fallback(module)
    return module, sent


def test_the_engines_own_watch_path_takes_channelrecipes_shots_on_its_own_clock(real_youtube, run, tmp_path):
    module, sent = real_youtube
    b = {"cr": {"watched": 0, "windows": 0, "kie_failed": 0, "said": False, "stopped": "", "down": 0},
         "dur": {"vidwhole001": 300.0}, "engine": run.engine}
    work = tmp_path / "youtube"
    watch._budget[watch._bkey(work)] = b
    try:
        whole = module.shots("vidwhole001", work)              # watched whole: 0-240 and 240-300
        long_ = module.shots("vidstretch1", work, [[400.0, 700.0]])   # a stretch of a long video
    finally:
        watch._budget.pop(watch._bkey(work), None)
    assert [module._sec(s["start"]) for s in whole["shots"]] == [10.0, 250.0]
    # the engine's own stretch() put the clip's clock back on the video's: 400 + 10 and 640 + 10, then (its rule: a
    # model can stop before the clip ends) it watched the tail after the last shot once more: 653-700, a shot at 663
    assert [module._sec(s["start"]) for s in long_["shots"]] == [410.0, 650.0, 663.0]
    assert [(p["start"], p["end"]) for p in run.service.posts] == [(0, 240), (240, 300), (400, 640), (640, 700),
                                                                    (653, 700)]
    saved = json.loads((work / "catalog" / "vidstretch1.json").read_text(encoding="utf-8"))
    assert saved["schema"] == module.CATALOG_SCHEMA and saved["spans"] == [[400.0, 700.0]]
    assert any("api.kie.ai" in url for url in sent), "Kie was asked first"
    assert {p["question"][:40] for p in run.service.posts} >= {module.SHOTS_PROMPT[:40]}



def test_a_clip_this_computer_cant_download_is_not_sent_to_channelrecipe(run):
    """YouTube blocking yt-dlp is a local failure before Kie is asked: no clip watch spent, no blame on Kie, and the
    engine's own error stands (review of bugs-106, fix 1)."""
    run.module.KIE["local"] = "blocked"
    got = run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert got == {} and run.service.posts == []
    assert not said(run.engine.lines, "Kie's video watcher isn't answering")
    assert said(run.engine.lines, "could not fetch a 360p copy of vid00000000")
    assert watch.last_outcome(run.home) == {}


def test_kies_own_failures_are_told_apart_from_this_computers(tmp_path):
    import subprocess
    module = SimpleNamespace(_safe=lambda v: v)
    work = tmp_path / "youtube"
    err = RuntimeError("api.kie.ai HTTP 500: Internal Server Error")
    assert not watch.kie_failed(module, "vid00000001", work, None, err), "no copy made: Kie was never reached"
    (work / "low").mkdir(parents=True)
    (work / "low" / "vid00000001.mp4").write_bytes(b"x")
    assert watch.kie_failed(module, "vid00000001", work, None, err)
    assert watch.kie_failed(module, "vid00000001", work, None, TimeoutError("Read timed out. (read timeout=300)"))
    assert not watch.kie_failed(module, "vid00000001", work, None, RuntimeError("could not fetch a 360p copy of x"))
    assert not watch.kie_failed(module, "vid00000001", work, None, subprocess.TimeoutExpired(["yt-dlp"], 900))
    assert watch.kie_failed(module, "other000001", work, (30.0, 90.0),
                            RuntimeError("kie's video watcher failed three videos in a row")), "the breaker is Kie's"
    (work / "low" / "vid00000002_30_90.mp4").write_bytes(b"x")
    assert watch.kie_failed(module, "vid00000002", work, (30.4, 90.9), err), "a stretch's copy, by the engine's name"


def test_one_video_takes_at_most_its_share_of_the_plans_clip_watches(run, monkeypatch):
    """A 10-minute video can want 50 videos; through the fallback it takes at most 20 clip watches (fix 2)."""
    monkeypatch.setattr(watch, "CR_MAX_CLIP_WATCHES", 4)
    run.module.N = 6
    got = run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert len(run.service.posts) == 4 and len(got) == 2, "two 5-minute videos, two windows each"
    stopped = said(run.engine.lines, "footage watching stopped for this video")
    assert len(stopped) == 1 and "it has used the 4 clip watches one video may take" in stopped[0]
    assert said(run.engine.lines, "watched 2 video(s) with 4 of your plan's clip watches (at most 4 for a video)")


def test_never_more_than_the_month_has_left(run):
    run.service.remaining = 2
    run.module.fetch_clips(run.engine, "word " * 300, run.job, "documentary", False)
    assert len(run.service.posts) == 2
    assert said(run.engine.lines, "(2 left this month; at most 2 for this video)")
    run2 = run.service
    run2.posts.clear()
    run.service.remaining = 0
    run.engine.lines.clear()
    run.module.fetch_clips(run.engine, "word " * 300, run.home / "job2", "documentary", False)
    assert run2.posts == []
    assert said(run.engine.lines, "this month's ChannelRecipe clip watches are used up (your credits were not used)")


def test_a_four_to_seven_minute_video_is_watched_whole_and_a_longer_one_says_how_much(run, tmp_path):
    b = {"cr": {"watched": 0, "windows": 0, "kie_failed": 0, "said": True, "stopped": "", "down": 0, "cap": 20},
         "dur": {"vid0000six1": 360.0, "vid000long1": 1500.0}, "engine": run.engine}
    work = tmp_path / "youtube"
    watch._budget[watch._bkey(work)] = b
    try:
        run.module._KIE_DOWN["fails"] = 3
        six = run.module._log_one("vid0000six1", work, "Watch the whole video itself.", prefer="kie")
        long_ = run.module._log_one("vid000long1", work, "Watch the whole video itself.", prefer="kie")
    finally:
        watch._budget.pop(watch._bkey(work), None)
    assert [(p["start"], p["end"]) for p in run.service.posts] == [(0, 240), (240, 360), (0, 240), (240, 420)]
    assert "spans" not in six, "a 6-minute video is watched whole"
    assert long_["spans"] == [[0.0, 420.0]], "a 25-minute video watched whole says it was its first 7 minutes"
