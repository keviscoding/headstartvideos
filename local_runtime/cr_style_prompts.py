"""Neutral writing and storyboard prompts for learned styles.

Some recipes are built around one niche: 2D Stories plans money stories in
"levels" with balances that must add up, and its storyboard draws banking apps
and car badges; Carl Jung writes dark psychology. A style learned from another
channel and built on one of them must not inherit that niche. These prompts
keep the recipe's workflow (beats, word budgets, a storyboard of one picture per
line) and leave the channel's voice to the art director's notes, which the
engine adds to every outline and script, and its picture style, which the
engine adds to every image prompt.
"""
from __future__ import annotations

NICHE_BASES = {"stories", "jung"}

OUTLINE = """You plan one episode of a faceless YouTube channel.

CHANNEL: [INSERT CHANNEL HERE]

TITLE: [INSERT TITLE HERE]
LENGTH: [INSERT LENGTH — e.g., 15 minutes, 20 minutes, 25 minutes]

Plan the episode in plain prose as numbered beats (BEAT 1, BEAT 2, ...), about one beat per minute of narration.
- BEAT 1 is the hook: the first thirty seconds that make the title's promise feel urgent.
- Each beat says what it covers, the concrete moment, place, object or fact it opens on, and how it moves the episode
  forward. Every beat has things a picture can show.
- Keep the episode true to the title. Use only facts you are certain of; where you are not sure of a date, number or
  quote, plan around it instead of guessing.
- Name the ending: how the last beat pays off the promise of the first.
- If the channel recurs a host or mascot (THE CAST in the channel's notes), plan where they appear."""

SCRIPT = """You write the narration for one episode of a faceless YouTube channel.

CHANNEL: [INSERT CHANNEL HERE]

THE PLAN (it starts with the episode's title):
[INSERT OUTLINE HERE]

Write the full narration for about [INSERT TARGET LENGTH], following the plan beat by beat.
Plain text only: no headings, no stage directions, no speaker labels, nothing that is not spoken aloud.
Write it the way this channel sounds (its notes below say how): its point of view, its sentence length, its humour,
how it delivers facts. Every fact is one you are certain of."""

SCENE = """You are the storyboard artist for a faceless YouTube video.

CHANNEL: [INSERT CHANNEL HERE]

Below is the narration. Draw [INSERT COUNT HERE] consecutive frames covering it from its first line to its last, so
that each frame shows EXACTLY what the viewer is hearing at that moment.

BE SPECIFIC: the frame shows the precise thing the line names: the person doing what the sentence says, the object,
the place and the time of day. Words, numbers and signs that the line names are written on the thing they belong to.
People who come back keep the same face, hair and clothes: decide their look the first time they appear and repeat
those exact words every time. If THE CAST is given, copy each cast member's description word for word whenever they
are in frame.

BE A STORYTELLER: frames follow each other like a comic. Vary the shot the way an editor would: the wide scene, a
close reaction on a face, the object on its own. Never repeat a frame or a layout twice in a row. Never illustrate an
idea with a symbol; show the real moment. Keep every person whole in the frame, or clearly one person in a close-up:
never a lone arm or hand reaching in from the edge without the person it belongs to. Describe the scene, not the art
style: the channel's picture style is added to every prompt for you.

Return ONLY a JSON array of [INSERT COUNT HERE] objects, in narration order. Laid end to end, the "text" fields are the
narration itself, with nothing skipped:

  {"text": "<the narration this frame covers, COPIED WORD FOR WORD from the script>",
   "prompt": "<a complete image prompt: the shot, who is in it and what they are doing, the objects, the setting,
              the time of day, the light>"}

SCRIPT:
[INSERT SCRIPT HERE]"""


def overrides(base: str, craft: str = "", channel: str = "") -> dict:
    """Style settings that replace a niche recipe's writing with neutral prompts ({} for format-only recipes).
    The engine fills the channel token only in storyboard prompts, so the writing prompts carry the learned
    channel's own description."""
    if base not in NICHE_BASES:
        return {}
    channel = " ".join(str(channel or "a faceless YouTube channel").split())[:600]
    out = {"prompts": {"outline": OUTLINE.replace("[INSERT CHANNEL HERE]", channel),
                       "script": SCRIPT.replace("[INSERT CHANNEL HERE]", channel)}}
    if base == "stories":
        out["look"] = {"scene_prompt": SCENE}
    if craft in ("story", "teaching"):
        out["script_craft"] = craft
    return out


OUTLINE_SOUND = """

ORIGINAL SOUND: this channel lets the real recordings speak (a 911 call, an officer's words on bodycam, a witness, a
statement, a verdict, an executive on stage). Plan about one such moment for each minute of the video, at least one:
the narration sets it up, then the real recording plays with its own sound. Note each in its beat as
"[CLIP: who says what, where]", and only moments that were really recorded and published."""

SCRIPT_SOUND = """

ORIGINAL SOUND HANDOFFS: where the plan marks [CLIP: ...], write the narration so it hands off to the real recording,
then put one line holding only "[[clip: who says what, in a few words]]" on its own line right after that paragraph
(for example "[[clip: the 911 caller describing the van]]"). The edit finds the real recording and cuts to it there,
with its own sound and its words on screen, and the narration waits. Never repeat the recording's words in the
narration."""


def with_original_sound(style: dict) -> dict:
    """A style whose videos play real clips with their own sound (look.soundbites.quotes) asks its outline to plan
    those moments and its script to mark them ([[clip: ...]]): the clips are found only where the script marks them."""
    sb = ((style.get("look") or {}).get("soundbites") or {})
    prompts = style.get("prompts") if isinstance(style.get("prompts"), dict) else {}
    if not (isinstance(sb, dict) and int(sb.get("quotes") or 0) > 0) or not prompts:
        return style
    for key, add in (("outline", OUTLINE_SOUND), ("script", SCRIPT_SOUND)):
        text = prompts.get(key)
        if isinstance(text, str) and text and "ORIGINAL SOUND" not in text:
            prompts[key] = text.rstrip() + add
    return style


def current_scene(scene):
    """Studio's storyboard prompt as it is today, for a learned style saved with an older one (an older one let a lone
    arm reach into the frame); any other prompt is returned unchanged."""
    if (isinstance(scene, str) and scene.startswith("You are the storyboard artist for a faceless YouTube video.")
            and "[INSERT CHANNEL HERE]" in scene and scene != SCENE):
        return SCENE
    return scene


def install(engine):
    """Learned styles saved with an older storyboard prompt draw with today's (in memory: their files stay as saved)."""
    original = getattr(engine, "_scene_prompts", None)
    if original is None or getattr(original, "_cr_current", False):
        return

    def _scene_prompts(style):
        look = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}).get("look")
        if isinstance(look, dict) and look.get("scene_prompt"):
            look["scene_prompt"] = current_scene(look["scene_prompt"])
        return original(style)
    _scene_prompts._cr_current = True
    engine._scene_prompts = _scene_prompts
