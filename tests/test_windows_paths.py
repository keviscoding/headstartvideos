"""Windows stops at 260 characters in a file's path unless long paths are turned on (an administrator's setting).
A creator's collages stopped there (2026-10-24): a Studio folder in their profile, a learned style's folder, a long
video folder, a collage picture, and Studio's own receipt beside it, which repeated the picture's whole name.

These build the deepest path a video's collage step makes with Studio's own naming code, under a typical download
folder, and hold it inside the limit."""
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_media  # noqa: E402
import cr_paths  # noqa: E402
import cr_readiness  # noqa: E402
from tests.test_frontier_style_clone import approved, clone  # noqa: E402,F401  (the style study's fixture)
from tests.test_windows_portability import ENGINE_SOURCE  # noqa: E402

# Windows user folder names stop at 20 characters; this is where "Extract All" puts a download
TYPICAL = PureWindowsPath("C:\\Users", "x" * 20, "Downloads", "ChannelRecipe studio")


def deepest_collage(tmp_path):
    """The collage picture and Studio's receipt beside it, for the longest video folder of the longest new style."""
    style = cr_paths.style_folder("cr_clone_" + "x" * cr_paths.STYLE_NAME)
    slug = "x" * 52 + "-" + "a1b2c3"                                      # the engine's slugify at its longest
    assert len(slug) == cr_paths.SLUG
    picture = PurePosixPath("output", style, "_work", slug) / cr_paths.COLLAGE
    receipt = cr_media.task_receipt(tmp_path / picture, "0" * 64)          # Studio's own naming
    return TYPICAL.joinpath(*picture.parts), TYPICAL.joinpath(*picture.parent.parts, ".cr-tasks", receipt.name)


def test_a_collages_deepest_file_fits_under_a_typical_windows_download_folder(tmp_path):
    for path in deepest_collage(tmp_path):
        assert len(str(path)) <= cr_paths.LIMIT, f"{len(str(path))} characters, over Windows' limit: {path}"


def test_setups_budget_covers_that_path(tmp_path):
    picture, receipt = deepest_collage(tmp_path)
    assert len(str(receipt)) - len(str(TYPICAL)) - 1 <= cr_paths.deepest()
    assert cr_paths.LIMIT - len(str(TYPICAL)) - 1 - cr_paths.deepest() >= 0, "setup says a typical folder has room"


def test_the_old_receipt_name_went_past_the_limit(tmp_path):
    """What the customer hit: the receipt repeated the collage picture's name (48 characters) before its digest."""
    picture, _ = deepest_collage(tmp_path)
    old = picture.parent / ".cr-tasks" / (picture.name + "-" + "0" * 24 + ".json")
    assert len(str(old)) > cr_paths.LIMIT


def test_a_folder_too_deep_gets_a_note_in_setup_and_studio_never_changes_windows(tmp_path):
    deep = tmp_path / ("a-folder-with-a-long-name-" * 4)
    deep.mkdir()
    note = cr_paths.status(deep, windows=True, long_paths=False)
    assert note["room"] < 0 and "260 characters" in note["message"] and "C:\\ChannelRecipe studio" in note["message"]
    row, = [r for r in cr_readiness.setup_rows({"file_paths": note}) if r["key"] == "file_paths"]
    assert row["ready"] is True and row["message"] == note["message"], "a note, never a stop"
    assert cr_paths.status(deep, windows=True, long_paths=True)["message"] == "", "long paths on: nothing to say"
    assert cr_paths.status(deep, windows=False) is None and cr_readiness.setup_rows({}) == []
    assert "too deep" in cr_paths.diagnostics_line(note)
    source = Path(cr_paths.__file__).read_text(encoding="utf-8")
    assert "SetValue" not in source and "CreateKey" not in source and "reg add" not in source


def test_windows_long_path_setting_is_read_where_it_exists():
    enabled = cr_paths.long_paths_enabled()
    assert enabled is None if sys.platform != "win32" else enabled in (True, False)


def test_a_learned_styles_own_name_stays_short(approved):
    with pytest.raises(ValueError, match="up to 24"):
        clone.prepare(approved.home, {**approved.spec, "name": "x" * (cr_paths.STYLE_NAME + 1)}, approved.proof,
                      approved.bridge)
    assert clone.prepare(approved.home, {**approved.spec, "name": "x" * cr_paths.STYLE_NAME}, approved.proof,
                         approved.bridge)


@pytest.mark.skipif(not ENGINE_SOURCE.is_file(), reason="the private engine source is not on this computer")
def test_the_budget_matches_the_engines_own_names():
    """The video folder (slugify) and a VOX collage picture's id are the engine's; the budget must follow them."""
    with zipfile.ZipFile(ENGINE_SOURCE) as source:
        make_video = source.read("Frontier/make_video.py").decode("utf-8")
        vox = source.read("Frontier/vox.py").decode("utf-8")
    assert "if len(s) <= 52:" in make_video and "hexdigest()[:6]" in make_video and 'f"{s[:52]' in make_video
    assert re.search(r'\.strip\("_"\)\[:40\]', vox) and 'f"{it[\'id\']}_raw.png"' in vox
    assert '/ "vox" / "assets"' in vox and '"vox_scenes"' in vox
