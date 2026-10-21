"""Release gates for Ranking Shorts that only the calendar breaks.

A customer's computer downloads every clip with yt-dlp, and YouTube changes its player every few weeks. The Studio's
yt-dlp is upgraded only when a release raises the floor in requirements.txt (an in-place update reinstalls libraries
only when that file changes, and nothing else ever upgrades it), so a release must not ship a floor much older than
itself. The floor is a date (yt-dlp's versions are), checked against the release's own changelog date: no clock, so
the same commit always passes or fails the same way, and the first release past the limit is the one that fails."""
import json
import re
from datetime import date
from pathlib import Path

from scripts import build_frontier_admin as package

ROOT = Path(__file__).resolve().parents[1]
FLOOR_MAX_AGE_DAYS = 75             # yt-dlp has released every two to six weeks; YouTube breaks old ones within months


def test_each_release_ships_a_recent_ytdlp_floor():
    patched = "".join(new for _old, new in package.ENGINE_PATCHES["requirements.txt"])
    found = re.search(r"^yt-dlp\[[^\]]*\]>=(\d{4})\.(\d{1,2})\.(\d{1,2})\b", patched, re.M)
    assert found, "the Studio's yt-dlp requirement carries a dated floor"
    floor = date(*map(int, found.groups()))
    entries = json.loads((ROOT / "webapp" / "frontier" / "changelog.json").read_text(encoding="utf-8"))
    newest = max(entries, key=lambda e: int(e["build"]))
    released = date.fromisoformat(newest["date"])
    age = (released - floor).days
    assert age <= FLOOR_MAX_AGE_DAYS, (
        f"Release {newest['build']} ({released}) would ship yt-dlp>={floor}, {age} days old. Raise the floor in "
        f"scripts/build_frontier_admin.py ENGINE_PATCHES['requirements.txt'] to the newest yt-dlp on "
        f"pypi.org/project/yt-dlp (and its line in tests/test_frontier_package.py), so customers' copies update.")
