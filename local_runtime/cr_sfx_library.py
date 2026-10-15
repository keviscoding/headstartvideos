"""ChannelRecipe's recorded sound effects, for styles that ask for them.

Studio synthesises its effects (whooshes, hits, risers, taps) unless a video has
recorded ones in job/sound/sfx/ (named <cue>_<n>.wav), which until now only an
Epidemic Sound subscription filled. This library is generated once and ships with
Studio (assets/sfx/cr/): a learned style whose references use sound effects
(look.sound.library) gets the recorded kit, at no cost per video. A creator's own
Epidemic Sound kit still comes first.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

HOME = Path(__file__).resolve().parent
LIBRARY = HOME / "assets" / "sfx" / "cr"


def wanted(look: dict) -> bool:
    snd = (look or {}).get("sound") if isinstance(look, dict) else None
    return isinstance(snd, dict) and snd.get("library") is True


def files(library: Path = None) -> dict:
    """{cue: [files]} in the library."""
    out = {}
    folder = library or LIBRARY
    if folder.is_dir():
        for path in sorted(folder.glob("*.wav")):
            match = re.fullmatch(r"([a-z_]+)_\d+", path.stem)
            if match:
                out.setdefault(match.group(1), []).append(path)
    return out


def fill(job: Path, log=print, library: Path = None) -> int:
    """Copy the library into job/sound/sfx/ (once). Returns how many effects the job has."""
    target = Path(job) / "sound" / "sfx"
    if target.is_dir() and any(target.glob("*.wav")):
        return len(list(target.glob("*.wav")))
    kit = files(library)
    if not kit:
        return 0
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for cue, paths in kit.items():
        for i, path in enumerate(paths, 1):
            shutil.copyfile(path, target / f"{cue}_{i}.wav")
            count += 1
    log(f"sound: ChannelRecipe's recorded effects for {', '.join(sorted(kit))}")
    return count


# What a picture shows -> the recorded effect for it (physical moments only; words never get a sound)
ACTIONS = [
    (r"\b(door|gate)s?\b.*\b(kick|slam|bang|burst|smash)|\b(kick|slam|bang|burst|smash)\w*\b.*\b(door|gate)", "hit", -4.0),
    (r"\b(punch|slap|smack|strik|crash|collaps|falls?\b|fell\b|thud|hammer)", "hit", -6.0),
    (r"\b(coins?|gold pieces|purse|pays?\b|paid|florins?|dollars?|cash|money bag)", "cash", -5.0),
    (r"\b(writ\w*|signs?\b|signing|scribbl\w*|quill|pen\b|chalk)", "scribble", -6.0),
    (r"\b(scrolls?|letters?|parchment|documents?|newspaper|ledger|pages?)\b", "paper", -6.0),
    (r"\b(runs?\b|running|rush\w*|throws?|thrown|flee\w*|chase\w*|dash\w*|swings?|leaps?|jumps?)", "whoosh", -6.0),
    (r"\b(knocks?|knocking|taps?\b)", "tap", -6.0),
    (r"\b(photo|camera|snapshot)", "shutter", -5.0),
]
GAP_S = {"sparse": 8.0, "moderate": 5.0, "dense": 3.0}


def story_cues(job, plan, look) -> list:
    """[(second, cue, dB)] for the pictures of a story whose storyboard shows a physical action."""
    import json
    density = str(((look or {}).get("sound") or {}).get("density") or "sparse")
    suffix = str((look or {}).get("still_suffix") or "")
    gap = GAP_S.get(density, 8.0)
    try:
        frames = json.loads((Path(job) / "astro_scenes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    clips = len(list((Path(job) / "clip_src").glob("vsrc_*.jpg")))
    out, t, last = [], 0.0, -99.0
    for kind, path, _leak, dur in plan:
        match = re.search(r"(scene|vsrc)_(\d+)\.(?:jpg|png)$", str(path))
        if match and t - last >= gap:
            # a story's clips are its first items (their first frame stands in the timeline), then the stills
            index = int(match.group(2)) + (clips if match.group(1) == "scene" else 0)
            frame = frames[index] if 0 <= index < len(frames) and isinstance(frames[index], dict) else {}
            # what the picture shows, without the channel's own style words appended to every prompt
            shown = str(frame.get("prompt") or "").replace(suffix, "")[:400].lower() if suffix else \
                str(frame.get("prompt") or "")[:400].lower()
            for pattern, cue, gain in ACTIONS:
                if re.search(pattern, shown):
                    out.append((round(t + 0.15, 2), cue, gain))
                    last = t
                    break
        t += float(dur)
    return out


def install(engine=None):
    """Let Studio's mixer pick up the library for styles that ask, when no Epidemic kit is set up."""
    try:
        import epidemic_sound
    except ImportError:
        return
    if getattr(epidemic_sound.settings, "_cr_library", False):
        return
    original_settings, original_prepare = epidemic_sound.settings, epidemic_sound.prepare_sfx

    def settings(look):
        cfg = original_settings(look)
        if not cfg and wanted(look) and not epidemic_sound.available():
            return {"sfx": True, "library": True}
        return cfg

    def prepare_sfx(style, look, job, log=print, budget_s=None):
        if wanted(look) and not epidemic_sound.available():
            return fill(job, log)
        return original_prepare(style, look, job, log, budget_s=budget_s) if budget_s is not None else \
            original_prepare(style, look, job, log)

    settings._cr_library = True
    epidemic_sound.settings, epidemic_sound.prepare_sfx = settings, prepare_sfx
    try:
        import sfx
    except ImportError:
        return
    original_cues = sfx.cues

    def cues(plan, job=None, style=None, look=None):
        out = original_cues(plan, job, style, look)
        the_look = look if isinstance(look, dict) else sfx._v2_look(style) if style else {}
        if job is not None and wanted(the_look):
            out = sorted(out + story_cues(job, plan, the_look))
        return out
    sfx.cues = cues
