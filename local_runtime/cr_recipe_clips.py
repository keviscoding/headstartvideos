"""A few moving seconds of each recipe for its card: hover (or open) a recipe and it plays, muted and looping.

Swapnil (Discord, 2026-10-29): "The recipe style should include a video preview so we can get a sense of how it looks,
instead of judging from the image thumbnail." Built without any new render:
- Studio's ready-made recipes: the landing page's moodboard loops on channelrecipe.com (webapp/static/moodboard, every
  one made in ChannelRecipe Studio, 3-4 s each, 30-200 KB). Loaded only when a card is hovered or opened.
- A creator's own style (or a recipe they have made a video with, when it has no loop of ours): a few seconds of their
  own latest finished video in it, played from this computer (/f/video/<slug>, seconds START to END).
- Nothing to play: the card keeps its picture.
The manga recap loops stay off (their panels may belong to a real webtoon), as on the landing page.
"""
from __future__ import annotations

BASE = "https://channelrecipe.com/static/moodboard/"

# recipe id -> its moodboard loop (webapp/static/moodboard/<name>.mp4; tests check each one is there)
CLIPS = {
    "cr-shorts-3d": "orca-dunkleosteus",
    "cr-shorts-3d-dark": "night-vision",
    "cr-shorts-photo": "hippo-yawn",
    "cr-deadly-places": "nanga-parbat-climber",
    "cr-3d-reconstruction": "titanic-lifeboat",
    "3d": "honeycomb",
    "cr-stick-stories": "gladiator-sold",
    "cr-handdrawn-finance": "hand-drawn-money",
    "cr-paper-finance": "paper-bank",
    "history": "seventies-kids",
    "cr-expert-presenter": "bacon-skillet",
    "cr-quiet-essays": "forgotten-ruins",
}
VERTICAL = {"orca-dunkleosteus", "night-vision", "hippo-yawn"}

# a few seconds of the creator's own video: past the cold open, short enough to stay light
START, END = 8.0, 14.0


def clip_for(name: str, own_video_url: str | None = None, *, base: str = BASE) -> dict | None:
    """{"url", "start", "end", "kind": "recipe"|"yours", "vertical"} for a recipe card, or None (keep the picture)."""
    clip = CLIPS.get(str(name or ""))
    if clip:
        return {"url": base + clip + ".mp4", "start": 0.0, "end": 0.0, "kind": "recipe", "vertical": clip in VERTICAL}
    if isinstance(own_video_url, str) and own_video_url.startswith("/f/video/"):
        return {"url": own_video_url, "start": START, "end": END, "kind": "yours", "vertical": False}
    return None


def missing(names) -> list:
    """The ready-made recipes with no loop of ours yet (the owner decides later whether to render previews)."""
    return sorted(n for n in names if n not in CLIPS)
