"""Studio asks YouTube politely by default, so one creator's videos rarely trigger its bot check."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_studio  # noqa: E402


def test_youtube_is_asked_with_a_pause_and_fewer_downloads_at_once(monkeypatch):
    monkeypatch.delenv("YT_SLEEP", raising=False)
    monkeypatch.setenv("YT_WORKERS", "6")
    cr_studio.polite_youtube()
    assert os.environ["YT_SLEEP"] == "1"
    assert os.environ["YT_WORKERS"] == "6", "a creator's own setting is kept"
