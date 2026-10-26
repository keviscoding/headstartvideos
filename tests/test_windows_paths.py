"""Windows stops at 260 characters in a file's path unless long paths are turned on (an administrator's setting).
A creator's collages stopped there (2026-10-24): a Studio folder in their profile, a learned style's folder, a long
video folder, a collage picture, and Studio's own receipt beside it, which repeated the picture's whole name.

These build the deepest paths a video makes with Studio's own naming (its attempt folders, receipts and Fast mode's
Windows names) and hold them to about 240 characters under a typical download folder, and inside the limit under the
longest Windows user name."""
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_attempts  # noqa: E402
import cr_manimgx as gx  # noqa: E402
import cr_media  # noqa: E402
import cr_paths  # noqa: E402
import cr_readiness  # noqa: E402
from tests.test_frontier_style_clone import approved, clone  # noqa: E402,F401  (the style study's fixture)
from tests.test_windows_portability import ENGINE_SOURCE  # noqa: E402

TYPICAL = PureWindowsPath("C:\\Users", "Christopher", "Downloads", "ChannelRecipe studio")   # where Extract All puts it
LONGEST = PureWindowsPath("C:\\Users", "x" * 20, "Downloads", "ChannelRecipe studio")       # user names stop at 20
BUDGET = 240


@pytest.fixture
def windows_names(monkeypatch):
    monkeypatch.setattr(gx, "SHORT_NAMES", True)


def deepest_paths(tmp_path):
    """The deepest files of a video with the longest new learned style and a long title, named by Studio's code."""
    style = cr_paths.style_folder("cr_clone_" + "x" * cr_paths.STYLE_NAME)
    slug = cr_attempts.new_slug("x" * 80, "crv_" + "a1b2c3d4" * 3)                 # Studio's attempt folder
    video = PurePosixPath("output", style, "_work", slug)
    collage = video / "vox_scenes" / "m00" / "vox" / "assets" / ("x" * 40 + "_raw.png")   # the engine's VOX picture
    receipt = cr_media.task_receipt(tmp_path / collage, "0" * 64)                    # Studio's receipt beside it
    scene = video / "threed" / gx.cache_name() / gx.key_folder("f" * 64)
    temporary = "x" * 8 + ".tmp"
    fast = [scene / gx.recovery_name(kind) / (".compiler-correction-response-1.txt" + temporary)
            for kind in ("runtime_api", "coverage_text", "manifest_json", "procedural_pixels")]
    fast.append(scene / gx.recovery_name("runtime_api") / gx.recovery_name("validation") / ".ty-xxxxxxxx"
                / (".scene-corrected.py" + temporary))
    return {"collage picture": collage, "its receipt": collage.parent / ".cr-tasks" / receipt.name,
            **{f"Fast mode {n}": path for n, path in enumerate(fast)}}


def test_a_videos_deepest_files_fit_under_a_typical_windows_download_folder(tmp_path, windows_names):
    for what, path in deepest_paths(tmp_path).items():
        full = str(TYPICAL.joinpath(*path.parts))
        assert len(full) <= BUDGET, f"{what}: {len(full)} characters, over {BUDGET}: {full}"
        full = str(LONGEST.joinpath(*path.parts))
        assert len(full) <= cr_paths.LIMIT, f"{what}: {len(full)} characters, over Windows' limit: {full}"


def test_setups_budget_covers_them(tmp_path, windows_names):
    need = cr_paths.deepest()
    assert all(len(str(path)) <= need for path in deepest_paths(tmp_path).values())
    assert len(str(TYPICAL)) + 1 + need <= BUDGET and len(str(LONGEST)) + 1 + need <= cr_paths.LIMIT
    assert len(cr_attempts.new_slug("x" * 200, "crv_" + "0" * 24)) <= cr_paths.SLUG


def test_the_old_names_went_past_the_limit(tmp_path):
    """What the customer hit: the receipt repeated the collage picture's name, beside a long attempt folder."""
    video = PurePosixPath("output", "cr-clone-7373019c2237", "_work", "x" * 52 + "-cadeb5--" + "3aa80eee" * 3)
    old = video / "vox_scenes/m00/vox/assets/.cr-tasks" / ("x" * 40 + "_raw.png-" + "0" * 24 + ".json")
    assert len(str(TYPICAL.joinpath(*old.parts))) > cr_paths.LIMIT
    fast = video / "threed/.cr-manimgx" / ("f" * 64) / ".recovery-runtime-api/.completed-correction-validation"
    assert len(str(TYPICAL.joinpath(*fast.parts, "compiler-corrected.json"))) > cr_paths.LIMIT


def test_a_folder_too_deep_gets_a_note_in_setup_and_studio_never_changes_windows(tmp_path, windows_names):
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


def test_a_title_too_long_for_this_folder_stops_before_the_video_not_at_its_end():
    """A finished video's folder and description file are both named after its title (the engine's youtube_filename,
    up to 120 characters): a long title in a deep folder can't be saved on Windows, so Create says so first."""
    youtube_filename = lambda title: title[:120] + ".mp4"               # noqa: E731 - the engine's rule, in short
    folder = str(TYPICAL)
    assert cr_paths.title_problem(folder, "cr_clone_7373019c2237", ["What if you woke up tomorrow"], youtube_filename) == ""
    problem = cr_paths.title_problem(folder, "cr_clone_7373019c2237", ["x" * 100], youtube_filename)
    most = int(re.search(r"up to about (\d+) characters", problem).group(1))
    assert cr_paths.title_problem(folder, "cr_clone_7373019c2237", ["x" * most], youtube_filename) == ""
    assert cr_paths.title_problem(folder, "cr_clone_7373019c2237", ["x" * (most + 1)], youtube_filename)
    finished = PureWindowsPath(folder, "output", "cr-clone-7373019c2237", "x" * most + " (12345678)",
                               "x" * most + " - description.txt")
    assert len(str(finished)) <= cr_paths.LIMIT


def test_a_long_title_gets_a_shorter_file_name_on_windows_instead_of_stopping_create(tmp_path):
    """The owner's Windows test (2026-10-26): a 112-character title in a Downloads folder stopped Create with "Shorten
    the title". Studio now cuts the finished video's file name to fit; the title stays whole everywhere else."""
    class Engine:
        @staticmethod
        def youtube_filename(title):
            return title[:120].rstrip(". ") + ".mp4"
    home = tmp_path / "studio"
    (home / "styles").mkdir(parents=True)
    folder = r"C:\Users\creator\Downloads\ChannelRecipe-local-studio-5.3-admin.107\ChannelRecipe studio"
    title = "100 Strangers, 1 Bunker: Who Survives 30 Days? It's a drama-and-alliances format that keeps viewers on the edge."
    off = Engine()
    cr_paths.install(off, home, windows=False)
    assert off.youtube_filename(title) == title.rstrip(".") + ".mp4", "a Mac keeps the whole name"
    on = Engine()
    cr_paths.install(on, home, windows=True, long_paths=True)
    assert on.youtube_filename(title) == title.rstrip(".") + ".mp4", "so does Windows with long paths on"
    win = Engine()
    cr_paths.install(win, home, windows=True, long_paths=False, folder=folder)
    cr_paths.install(win, home, windows=True, long_paths=False, folder="C:\\x")   # installed once
    assert cr_paths.title_problem(folder, "cr_clone_7373019c2237", [title], Engine.youtube_filename), "it was stopped"
    assert cr_paths.title_problem(folder, "cr_clone_7373019c2237", [title], win.youtube_filename) == ""
    name = win.youtube_filename(title)
    assert name.endswith(".mp4") and title.startswith(name[:-4].rsplit(" ", 1)[0])
    assert len(name) - 4 <= cr_paths.title_room(folder, home)
    assert win.youtube_filename("Short title") == "Short title.mp4"
    one, two = win.youtube_filename(title + " Part 1"), win.youtube_filename(title + " Part 2")
    assert one != two and len(one) - 4 <= cr_paths.title_room(folder, home), "two long titles cut alike stay apart"


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
    """The video folder (slugify), a VOX collage picture's id and a finished video's name are the engine's."""
    with zipfile.ZipFile(ENGINE_SOURCE) as source:
        make_video = source.read("Frontier/make_video.py").decode("utf-8")
        vox = source.read("Frontier/vox.py").decode("utf-8")
    assert "if len(s) <= 52:" in make_video and "hexdigest()[:6]" in make_video and 'f"{s[:52]' in make_video
    assert 'name = name[:120].rstrip().rstrip(".") or "video"' in make_video
    assert 'd_name = f"{Path(name).stem} - description.txt"' in make_video
    assert re.search(r'\.strip\("_"\)\[:40\]', vox) and 'f"{it[\'id\']}_raw.png"' in vox
    assert '/ "vox" / "assets"' in vox and '"vox_scenes"' in vox
