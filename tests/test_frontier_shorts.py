import importlib.util
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
    def download(base, key, target, path="/api/studio/download"):
        calls.append(path)
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
    fake.download = lambda base, key, target, path=None: _pack(target, ["../evil.py"])
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
