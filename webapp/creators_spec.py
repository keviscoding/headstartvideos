"""What the web server needs to know about hosted /creators edits, without the engine.

The editing engine (the private `creators` package) runs only on the edit machines, from their private image; this
repository is public and doesn't carry it. The server needs just these constants. creators/tests/test_hosted.py
checks they match `creators.hosted`, so the two can't drift apart.
"""
import json
from pathlib import Path

# The editing screen's stages, in the launch film's words (owner, 2026-10-30).
STAGES = (
    ("words", "Every word, caught"),
    ("deadair", "Dead air, gone"),
    ("music", "Music made for it"),
    ("broll", "B-roll on your words"),
    ("titles", "Title cards that hit"),
    ("watched", "Watched back"),
)

# A review round (the creator's notes on a finished Reel), as the review shows it.
NOTE_STAGES = (
    ("transcribing", "Hearing your notes"),
    ("planning", "Planning the changes"),
    ("rendering", "Re-cutting your Reel"),
    ("checking", "Checking it"),
)

MAX_SECONDS = 300           # a take up to 5:00 (owner, 2026-10-30)
MAX_ROUNDS = 3              # review rounds per edit (each re-renders on our machines)

_FIRST_LOOKS = {"story": "Cinematic story", "energy": "Creator energy"}
_PICKER = Path(__file__).resolve().parent / "static" / "creators" / "looks.json"


def look_names() -> dict[str, str]:
    """The looks a creator can pick, in the picker's order: the published picker (creators/web/publish.py writes
    looks.json from the engine's catalog), else the first two."""
    try:
        return {lk["key"]: lk["name"] for lk in json.loads(_PICKER.read_text(encoding="utf-8"))} or dict(_FIRST_LOOKS)
    except (OSError, ValueError, KeyError, TypeError):
        return dict(_FIRST_LOOKS)


LOOK_NAMES = look_names()
