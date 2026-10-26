"""archive.org that isn't answering is skipped for the rest of the video after two failed calls (cr_archive.py).

Customer ALEX (Studio 104): archive.org didn't respond at all, and the footage step kept trying search after search
with a 45-second timeout each, adding 20+ minutes. The engine's archiveorg.search asks each query up to six ways and
catches every failure, so nothing ever stopped it."""
import importlib.util
import json
import os
import sys
import threading
import types
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_archive
import cr_watch

ENGINE = Path(os.environ.get("FRONTIER_TEST_ENGINE_DIR", Path.home() / "Downloads/ChannelRecipe studio"))


@pytest.fixture(autouse=True)
def fresh():
    cr_archive.reset()
    yield
    cr_archive.reset()


def fake_archive(behaviour):
    """A module shaped like the engine's archiveorg: _get is its one network call; search() swallows failures."""
    module = types.ModuleType("fake_archiveorg")
    calls = []

    def _get(url, timeout=45.0):
        calls.append(url)
        return behaviour(url)

    def search(query):
        out = []
        for variant in ("a", "b", "c", "d", "e", "f"):
            try:
                out += _call(f"https://archive.org/advancedsearch.php?q={query}-{variant}")["response"]["docs"]
            except Exception:                                  # noqa: BLE001 - the engine's own rule
                continue
        return out

    def _call(url):
        return module._get(url)                                # a module global, read at call time like the engine's
    module._get, module.search = _get, search
    return module, calls


def timed_out(url):
    raise TimeoutError("The read operation timed out")


def test_two_failed_calls_switch_archive_org_off_for_the_rest_of_the_video():
    module, calls = fake_archive(timed_out)
    lines = []
    cr_archive.reset(SimpleNamespace(log=lines.append))
    cr_archive.install(module)
    for _ in range(5):                                         # five stretches, six variants each: thirty calls before
        assert module.search("chernobyl liquidators") == []
    assert len(calls) == 2, "two timeouts, then nothing more is asked"
    assert cr_archive.skipped()
    assert lines == ["  Internet Archive: archive.org isn't answering (2 calls in a row timed out); skipping it for "
                     "the rest of this video. Its footage comes from YouTube and the other sources."]


@pytest.mark.parametrize("error", [urllib.error.URLError("[Errno 61] Connection refused"),
                                   ConnectionResetError("reset by peer"),
                                   urllib.error.HTTPError("https://archive.org", 503, "Service Unavailable", {}, None),
                                   json.JSONDecodeError("Expecting value", "<html>down for maintenance", 0)])
def test_refused_reset_5xx_and_a_maintenance_page_count_too(error):
    def fail(url):
        raise error
    module, calls = fake_archive(fail)
    cr_archive.install(module)
    module.search("moon landing")
    assert len(calls) == 2 and cr_archive.skipped()


def test_a_missing_item_is_an_answer_and_a_good_answer_resets_the_count():
    answers = iter([urllib.error.HTTPError("u", 404, "Not Found", {}, None), {"response": {"docs": [1]}},
                    TimeoutError("timed out"), {"response": {"docs": [2]}}, TimeoutError("timed out"),
                    {"response": {"docs": [3]}}])

    def mixed(url):
        got = next(answers, {"response": {"docs": []}})
        if isinstance(got, BaseException):
            raise got
        return got
    module, calls = fake_archive(mixed)
    cr_archive.install(module)
    assert module.search("apollo") == [1, 2, 3]
    assert not cr_archive.skipped(), "never two failures in a row"


def test_the_next_video_asks_archive_org_again():
    module, calls = fake_archive(timed_out)
    cr_archive.install(module)
    module.search("x")
    assert cr_archive.skipped()
    cr_archive.reset()
    assert not cr_archive.skipped()
    module.search("x")
    assert len(calls) == 4


def test_install_is_idempotent_and_a_missing_module_is_a_noop(monkeypatch):
    module, calls = fake_archive(lambda url: {"response": {"docs": []}})
    cr_archive.install(module)
    wrapped = module._get
    cr_archive.install(module)
    assert module._get is wrapped
    monkeypatch.setitem(sys.modules, "archiveorg", None)        # import archiveorg -> ModuleNotFoundError
    assert cr_archive.install() is None


def test_each_videos_footage_search_starts_with_archive_org_on_and_skips_its_films_while_off(tmp_path):
    """cr_watch's fetch_clips wrapper resets the breaker for each video; an Archive film isn't fetched while it's off."""
    module = types.ModuleType("fake_youtube_ia")
    watched, down_after = [], {"vid": None}

    def shots(vid, work, spans=None, cfg=None):
        watched.append(vid)
        if vid == down_after["vid"]:                            # archive.org stops answering during this search
            with cr_archive._lock:
                cr_archive._state["skipped"] = True
        return {"shots": []}

    def fetch_clips(engine, script, job, style, force, title=""):
        for vid in ("yt1", "ia:film/a.mp4", "yt2"):
            try:
                module.shots(vid, Path(job) / "youtube", None, cfg={})
            except RuntimeError as e:
                engine.log(f"{vid}: not watched - {e}")
    module.__dict__.update(_safe=lambda v: v.replace("/", "_"), settings=lambda e, s: {"watcher": "kie"},
                           analyst=lambda p=None: "kie", plan_focus=lambda *a: {}, shots=shots, fetch_clips=fetch_clips)
    cr_watch._install_budget(module)
    engine = SimpleNamespace(lines=[], STYLE_WPM={})
    engine.log = engine.lines.append
    with cr_archive._lock:
        cr_archive._state["skipped"] = True                     # the last video found archive.org down
    module.fetch_clips(engine, "word " * 300, tmp_path / "one", "documentary", False)
    assert watched == ["yt1", "ia:film/a.mp4", "yt2"], "a new video asks archive.org again"
    watched.clear()
    down_after["vid"] = "yt1"
    module.fetch_clips(engine, "word " * 300, tmp_path / "two", "documentary", False)
    assert watched == ["yt1", "yt2"], "its Archive film isn't fetched while archive.org is off"
    assert "ia:film/a.mp4: not watched - archive.org isn't answering, so this video goes on without it" in engine.lines


@pytest.fixture
def real_archiveorg(monkeypatch):
    source = ENGINE / "archiveorg.py"
    if not source.is_file():
        pytest.skip("Owner's private engine source is required; it is not published with these tests.")
    spec = importlib.util.spec_from_file_location("archiveorg_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_engines_own_archive_search_stops_asking_after_two_timeouts(real_archiveorg, monkeypatch):
    """The real archiveorg.py: a stretch's candidates() asked up to six searches, 45 s each. Now two calls in all."""
    opened = []

    def urlopen(request, timeout=None):
        opened.append((getattr(request, "full_url", str(request)), timeout))
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(real_archiveorg.urllib.request, "urlopen", urlopen)
    lines = []
    cr_archive.reset(SimpleNamespace(log=lines.append))
    cr_archive.install(real_archiveorg)
    for query in ("chernobyl liquidators 1986", "reactor control room", "pripyat evacuation", "soviet newsreel"):
        assert real_archiveorg.candidates(query, n=2) == []
    assert len(opened) == 2 and all(t == 45.0 for _, t in opened)
    assert len(lines) == 1 and "skipping it for the rest of this video" in lines[0]
    assert real_archiveorg.details("some-film") == {}, "file lists are skipped too"
    assert len(opened) == 2


def test_the_engines_archive_search_still_works_when_archive_org_answers(real_archiveorg, monkeypatch):
    import io
    docs = {"response": {"docs": [{"identifier": "chernobyl1986", "title": "Chernobyl 1986 newsreel", "year": "1986",
                                   "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
                                   "downloads": 900, "collection": ["prelinger"], "description": "chernobyl reactor"}]}}

    def urlopen(request, timeout=None):
        return io.BytesIO(json.dumps(docs).encode())
    monkeypatch.setattr(real_archiveorg.urllib.request, "urlopen", urlopen)
    cr_archive.install(real_archiveorg)
    found = real_archiveorg.search("chernobyl 1986 newsreel", n=3)
    assert found and found[0]["identifier"] == "chernobyl1986" and not cr_archive.skipped()
