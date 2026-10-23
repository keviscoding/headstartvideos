import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cr_shorts", ROOT / "local_runtime" / "cr_shorts.py")
shorts = importlib.util.module_from_spec(spec)
sys.modules["cr_shorts"] = shorts
spec.loader.exec_module(shorts)


def test_a_shorts_recipe_is_marked_by_its_format():
    assert shorts.is_short({"format": "short", "shorts": {"pipeline": "zack3d"}})
    assert not shorts.is_short({"format": "long"})
    assert not shorts.is_short(None)


def test_the_length_travels_at_the_front_of_the_extra_instructions():
    assert shorts.split_extra("[[short_seconds=45]] Keep it eerie.") == (45, "Keep it eerie.")
    assert shorts.split_extra("No marker here") == (None, "No marker here")
    assert shorts.split_extra("") == (None, "")


def test_the_length_comes_from_the_page_then_the_recipe_and_stays_in_range():
    dark = {"format": "short", "shorts": {"pipeline": "zack3d", "seconds": 45}}
    assert shorts.seconds_of(dark, {}, "") == 45                          # the recipe's own length
    assert shorts.seconds_of(dark, {}, "[[short_seconds=25]] x") == 25    # the Create page's choice wins
    assert shorts.seconds_of(dark, {}, "[[short_seconds=900]]") == 60     # never past a Short
    assert shorts.seconds_of(dark, {}, "[[short_seconds=3]]") == 15
    assert shorts.seconds_of({"format": "short"}, {"short_seconds": "bad"}, "") == 25


def test_a_studio_without_the_pipelines_or_the_pack_says_what_is_missing(tmp_path):
    missing = shorts.ready(tmp_path)
    assert any("pipelines" in m for m in missing) and any("asset pack" in m for m in missing)
    for folder, script in shorts.PIPELINES.values():
        (tmp_path / "shorts" / folder).mkdir(parents=True, exist_ok=True)
        (tmp_path / "shorts" / folder / script).write_text("")
    index = tmp_path / "shorts" / "rbx" / "r3d" / "engine" / "skins" / "rbx" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text("{}")
    assert shorts.ready(tmp_path) == []


def _pack(path, names):
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "x")


def test_the_pack_downloads_once_and_unpacks_into_shorts_with_its_index_last(tmp_path, monkeypatch):
    import types
    index = "/".join(shorts.PACK_INDEX)
    calls = []
    fake = types.ModuleType("cr_update")
    class UpdateStopped(Exception):
        pass
    fake.UpdateStopped = UpdateStopped
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    def download(base, key, target, path="/api/studio/download", **options):
        calls.append(path)
        assert options.get("resume") is True and callable(options.get("progress"))
        _pack(target, [index, "rbx/r3d/engine/skins/animals/dog.glb", "hoodie/host1/pose.png"])
    fake.download = download
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    said = []
    shorts.ensure_pack(said.append, None, home=tmp_path)
    root = tmp_path / "shorts"
    assert calls == ["/api/studio/shorts-pack"]
    assert (root / index).is_file() and (root / "hoodie" / "host1" / "pose.png").is_file()
    assert not (tmp_path / ".cr-shorts-pack.zip").exists()
    shorts.ensure_pack(said.append, None, home=tmp_path)          # in place: no second download
    assert calls == ["/api/studio/shorts-pack"]


def test_a_pack_that_would_write_outside_shorts_is_refused(tmp_path, monkeypatch):
    import types
    fake = types.ModuleType("cr_update")
    fake.UpdateStopped = type("UpdateStopped", (Exception,), {})
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    fake.download = lambda base, key, target, path=None, **options: _pack(target, ["../evil.py"])
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    import pytest
    with pytest.raises(RuntimeError, match="damaged"):
        shorts.ensure_pack(None, None, home=tmp_path)
    assert not (tmp_path / "evil.py").exists()


def test_a_failed_pack_download_says_why_in_plain_words(tmp_path, monkeypatch):
    import types
    fake = types.ModuleType("cr_update")
    class UpdateStopped(Exception):
        pass
    fake.UpdateStopped = UpdateStopped
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    def download(*a, **k):
        raise UpdateStopped("The download was interrupted. Nothing was changed; check your connection and try again.")
    fake.download = download
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    import pytest
    with pytest.raises(RuntimeError, match="Shorts pack couldn't be downloaded. The download was interrupted. Check your connection"):
        shorts.ensure_pack(None, None, home=tmp_path)


def test_a_stopped_pack_download_keeps_what_arrived_and_resume_carries_on(tmp_path, monkeypatch):
    import types
    import pytest
    index = "/".join(shorts.PACK_INDEX)
    fake = types.ModuleType("cr_update")
    class UpdateStopped(Exception):
        pass
    fake.UpdateStopped = UpdateStopped
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    runs = []
    def download(base, key, target, path=None, resume=False, progress=None):
        runs.append(target.stat().st_size if target.exists() else 0)
        if len(runs) == 1:
            target.write_bytes(b"PK partial")                       # some of it arrived, then the connection dropped
            raise UpdateStopped("The download was interrupted. Nothing was changed; check your connection and try again.")
        assert resume, "the second run carries on from the partial file"
        progress(200 << 20, 400 << 20)
        _pack(target, [index])
    fake.download = download
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    said = []
    with pytest.raises(RuntimeError, match="Resume carries on from where it stopped"):
        shorts.ensure_pack(said.append, None, home=tmp_path)
    assert (tmp_path / ".cr-shorts-pack.zip").exists(), "what arrived is kept"
    shorts.ensure_pack(said.append, None, home=tmp_path)
    assert runs == [0, len(b"PK partial")]
    assert (tmp_path / "shorts" / index).is_file() and not (tmp_path / ".cr-shorts-pack.zip").exists()
    assert any("200 of 400 MB" in line for line in said) and any("Carrying on" in line for line in said)


def test_a_whole_pack_left_by_a_stopped_unpack_is_unpacked_without_downloading_again(tmp_path, monkeypatch):
    import types
    index = "/".join(shorts.PACK_INDEX)
    fake = types.ModuleType("cr_update")
    fake.UpdateStopped = type("UpdateStopped", (Exception,), {})
    fake._settings = lambda home: ("https://channelrecipe.com", "cr_mcp_k")
    def download(*a, **k):
        raise AssertionError("no second download")
    fake.download = download
    monkeypatch.setitem(sys.modules, "cr_update", fake)
    _pack(tmp_path / ".cr-shorts-pack.zip", [index, "hoodie/host1/pose.png"])
    shorts.ensure_pack(None, None, home=tmp_path)
    assert (tmp_path / "shorts" / index).is_file()


def test_a_ranking_shorts_options_travel_in_their_own_marker_and_only_known_kinds_pass():
    extra = '[[short_seconds=25]] [[ranking={"ranks": 7, "booms": false, "sfx": "light", "board": "dogs-1a2b3c", ' \
            '"ranks_typo": 3, "commentary": "yes", "punch_ins": 1}]] Keep it savage.'
    assert shorts.split_extra(extra) == (25, "Keep it savage.")
    assert shorts.ranking_options(extra) == {"ranks": 7, "booms": False, "sfx": "light", "board": "dogs-1a2b3c"}
    assert shorts.ranking_options('[[ranking={"ranks": 40}]]') == {"ranks": 10}
    assert shorts.ranking_options('[[ranking={"clean": false}]]') == {"clean": False}   # burned-in text left on
    assert shorts.ranking_options('[[ranking={not json}]]') == {}
    assert shorts.ranking_options("no marker") == {}


def test_the_ranking_pipeline_needs_no_asset_pack_and_says_so_when_it_is_missing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(shorts, "ensure_pack", lambda *a, **k: calls.append("pack"))
    monkeypatch.setenv("CR_SHORTS_ROOT", str(tmp_path / "shorts"))

    class Engine:
        def job_dir(self, title, style):
            return str(tmp_path / "job")
    info = {"format": "short", "shorts": {"pipeline": "ranking"}}
    try:
        shorts.run_short(Engine(), "Funniest Dog Moments", "cr-shorts-ranking", info, extra='[[ranking={"ranks": 5}]]')
    except RuntimeError as error:
        assert "Ranking Shorts pipeline is not installed" in str(error)
    else:
        raise AssertionError("a Studio without the pipeline must say so")
    assert calls == [], "no 400 MB pack download for a ranking Short"


def test_the_ranking_pipeline_gets_its_options_and_progress_reads_in_plain_words(tmp_path, monkeypatch):
    root = tmp_path / "shorts"
    (root / "ranking").mkdir(parents=True)
    (root / "ranking" / "make_ranking.py").write_text(
        "import json, os, sys, pathlib\n"
        "work = pathlib.Path(sys.argv[2])\n"
        "print('12:00:00 watching 5 of 26 watched', flush=True)\n"
        "(work / 'opts.json').write_text(os.environ['CR_RANKING'])\n"
        "(work / 'short.mp4').write_bytes(b'mp4')\n"
        "(work / 'plan.json').write_text(json.dumps({'title': 'Ranking Funniest Dog Moments', 'narration': 'Bro.'}))\n"
        "(work / 'voice.json').write_text(json.dumps({'words': [[0.1, 0.4, 'Bro.']]}))\n")
    monkeypatch.setenv("CR_SHORTS_ROOT", str(root))
    said = []

    class Engine:
        def job_dir(self, title, style):
            return str(tmp_path / "job")
    info = {"format": "short", "label": "Ranking Shorts", "shorts": {"pipeline": "ranking"}}
    job = shorts.run_short(Engine(), "Funniest Dog Moments", "cr-shorts-ranking", info, sink=said.append,
                           extra='[[short_seconds=25]] [[ranking={"ranks": 6, "commentary": false}]]')
    assert json.loads((job / "short" / "opts.json").read_text()) == {"ranks": 6, "commentary": False}
    assert (job / "video.mp4").read_bytes() == b"mp4" and "Bro." in (job / "subs.srt").read_text()
    assert any(line.startswith("Watching candidate clips") for line in said)
    assert any("6 ranks" in line for line in said)
