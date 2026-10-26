"""Windows' "downloaded from the internet" mark comes off Studio's own files (cr_unblock), so Start on Windows.cmd stops
showing "Open File - Security Warning ... Unknown Publisher" on every launch (the owner's Windows test, 2026-10-26).

The mark is a Zone.Identifier stream beside each extracted file; Unblock-File deletes it, and so does cr_unblock. The
last test writes a real mark on NTFS and takes it off (it runs on the Windows CI); the others run everywhere."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_unblock  # noqa: E402

RUNTIME = Path(__file__).resolve().parents[1] / "local_runtime"


def studio(tmp_path, files, listed_only=()):
    """A Studio folder with these files, and a cr-files.json naming them plus listed_only (names never written)."""
    home = tmp_path / "ChannelRecipe studio"
    home.mkdir()
    (home / "cr-files.json").write_text(json.dumps({"schema": 1, "files": {
        name: "0" * 64 for name in [*files, *listed_only]}}), encoding="utf-8")
    for name in files:
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text("studio file", encoding="utf-8")
    return home


def test_only_studios_own_files_lose_the_mark(tmp_path):
    # a damaged list's paths outside the folder are never touched (and never written by this test)
    home = studio(tmp_path, ["Start on Windows.cmd", "cr_studio.py", "threed/engine/index.html"],
                  listed_only=["../outside.txt", "C:/Windows/system.ini", "/etc/hosts", "a:b"])
    (home / "output").mkdir()
    (home / "output" / "my video.mp4").write_text("the creator's", encoding="utf-8")
    (home / ".venv").mkdir()
    removed = []
    assert cr_unblock.clear(home, windows=True, remove=removed.append) == 4
    assert sorted(removed) == sorted(str(home.joinpath(*name.split("/"))) + ":Zone.Identifier" for name in
                                     ["Start on Windows.cmd", "cr-files.json", "cr_studio.py", "threed/engine/index.html"])
    assert not any(word in path for path in removed for word in ("output", ".venv", "outside", "system.ini", "hosts"))


def test_a_file_without_a_mark_or_a_disk_without_streams_never_stops_it(tmp_path):
    home = studio(tmp_path, ["Start on Windows.cmd", "cr_studio.py"])

    def remove(path):
        raise (FileNotFoundError if "cr_studio" in path else OSError)(path)
    assert cr_unblock.clear(home, windows=True, remove=remove) == 0


def test_without_a_file_list_the_launcher_still_loses_its_mark(tmp_path):
    removed = []
    assert cr_unblock.clear(tmp_path, windows=True, remove=removed.append) == 2
    assert removed == [str(tmp_path / "Start on Windows.cmd") + ":Zone.Identifier",
                       str(tmp_path / "cr-files.json") + ":Zone.Identifier"]


def test_nothing_happens_off_windows(tmp_path):
    def remove(path):
        raise AssertionError("nothing is removed on a Mac")
    assert cr_unblock.clear(studio(tmp_path, ["cr_studio.py"]), windows=False, remove=remove) == 0


def test_the_launcher_clears_it_on_its_first_start_and_studio_on_every_start():
    lines = [line.strip() for line in (RUNTIME / "Start on Windows.cmd").read_text(encoding="utf-8").splitlines()]
    unblock = lines.index('".venv\\Scripts\\python.exe" cr_unblock.py >nul 2>&1')
    assert lines.index('if exist ".venv\\.cr-installed" goto run_studio') < unblock
    assert unblock < next(i for i, line in enumerate(lines) if "-m pip install" in line), "before the long install"
    main = (RUNTIME / "cr_studio.py").read_text(encoding="utf-8").split("def main():", 1)[1]
    assert "target=cr_unblock.clear, args=(HOME,)" in main and main.index("cr_unblock") < main.index("create_app()")


@pytest.mark.skipif(sys.platform != "win32", reason="the mark is an NTFS stream: Windows only (the Windows CI runs it)")
def test_a_real_mark_of_the_web_comes_off_and_the_file_stays(tmp_path):
    home = studio(tmp_path, ["Start on Windows.cmd", "cr_studio.py"])
    launcher = home / "Start on Windows.cmd"
    mark = str(launcher) + ":Zone.Identifier"
    # what Extract All writes beside a file from a downloaded ZIP (ZoneId 3: the internet)
    with open(mark, "w", encoding="utf-8") as stream:
        stream.write("[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://channelrecipe.com/\r\n")
    with open(mark, encoding="utf-8") as stream:
        assert "ZoneId=3" in stream.read()
    assert cr_unblock.clear(home) == 1
    with pytest.raises(FileNotFoundError):
        open(mark, encoding="utf-8").close()
    assert launcher.read_text(encoding="utf-8") == "studio file", "only the mark goes, never the file"
    assert cr_unblock.clear(home) == 0, "already clear: nothing to do"
