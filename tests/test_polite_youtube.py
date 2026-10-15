"""Studio asks YouTube politely by default, so one creator's videos rarely trigger its bot check."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_studio  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def own_environment(monkeypatch):
    """Studio sets these for its whole process; each test gets its own copy so none leaks into other tests."""
    monkeypatch.setattr(os, "environ", dict(os.environ))


def test_youtube_is_asked_with_a_pause_and_fewer_downloads_at_once(monkeypatch):
    monkeypatch.delenv("YT_SLEEP", raising=False)
    monkeypatch.setenv("YT_WORKERS", "6")
    monkeypatch.delenv("YT_FORCE_IPV4", raising=False)
    cr_studio.polite_youtube(reach=lambda: False)
    assert os.environ["YT_SLEEP"] == "1" and "YT_FORCE_IPV4" not in os.environ
    assert os.environ["YT_WORKERS"] == "6", "a creator's own setting is kept"


def test_youtube_is_asked_over_ipv4_when_this_computer_reaches_it_that_way(monkeypatch):
    monkeypatch.delenv("YT_FORCE_IPV4", raising=False)
    cr_studio.polite_youtube(reach=lambda: True)
    assert os.environ["YT_FORCE_IPV4"] == "1"
    import cr_style_clone
    assert "--force-ipv4" in cr_style_clone._ytdlp_command(), "the style study's downloads too"
    monkeypatch.setenv("YT_FORCE_IPV4", "0")
    cr_studio.polite_youtube(reach=lambda: True)
    assert os.environ["YT_FORCE_IPV4"] == "0" and "--force-ipv4" not in cr_style_clone._ytdlp_command()


def test_the_engine_passes_ipv4_to_every_youtube_request():
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    import build_frontier_admin as package
    engine = (b'def ytdlp():\n    cmd = ["yt-dlp"]\n    if _num_env("YT_SLEEP") > 0:\n        cmd += ["--sleep-requests", "1"]\n'
              b'    return cmd\n'
              b'def search():\n        try:\n            p = 1\n        except subprocess.TimeoutExpired:\n            return []\n'
              b'        for line in p.stdout.splitlines():\n            pass\n')
    patched = package.patch_engine("youtube.py", engine).decode()
    scope = {"_env": lambda k: {"YT_FORCE_IPV4": "1"}.get(k, ""), "_num_env": lambda k: 0}
    exec(patched.split("def search")[0], scope)
    assert scope["ytdlp"]() == ["yt-dlp", "--force-ipv4"]
