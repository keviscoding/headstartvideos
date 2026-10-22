"""The 3D look a creator chooses for one video: the recipe's own, white studio (all-white models), studio colour
(the clean studio models painted in the creator's palette) or colour film.

A recipe's 3D look is its look.threed.preset (white_studio for the clean 3D reconstruction channels; designed for a
channel whose designed cast the engine builds itself, from the cast spec look.threed.designed: Studio 78). In Create the
creator may choose another for one video (run_spec cr_threed_look); a colour palette always comes with the colour look,
so its colours show. The engine reads a video's 3D settings without knowing the job, so the job is remembered while
its 3D is made and the choice applied whenever the settings are read.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

LOOKS = ("white_studio", "studio_color", "documentary")
_current = threading.local()
_ACTIVE: dict = {}          # the same job for the director's worker threads (one video per engine process)


def _job():
    return getattr(_current, "job", None) or _ACTIVE.get("job")


def chosen(job) -> str:
    try:
        spec = json.loads((Path(job) / "run_spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    look = str((spec or {}).get("cr_threed_look") or "")
    return look if look in LOOKS else ""


def clean(value) -> str:
    """The choice from Create: "" (the recipe's own) or one of LOOKS."""
    value = str(value or "").strip()
    if value and value not in LOOKS:
        raise ValueError("Choose the recipe's 3D look, white studio or colour.")
    return value


DEFAULT_PALETTE = "warm ivory white surfaces, signal yellow accents, deep charcoal shadows"
PALETTE_LINE = "3D SCENE PALETTE: every 3D scene uses only {}; keep it minimal and contrasting."


def recipe_palette(engine, job) -> str:
    """The palette a studio-colour video paints in when its creator chose none in Create: the recipe's own
    (look.threed.palette), else ivory with signal-yellow accents. "" for any other look."""
    try:
        style = (Path(job) / "style.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    look = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}).get("look") or {}
    lt = look.get("threed") if isinstance(look.get("threed"), dict) else {}
    if (chosen(job) or str(lt.get("preset") or "")) != "studio_color":
        return ""
    return str(lt.get("palette") or DEFAULT_PALETTE)


def recipe_designed(engine, job):
    """The cast spec of a designed-look video (the recipe's look.threed.designed), or None for any other look."""
    try:
        style = (Path(job) / "style.txt").read_text(encoding="utf-8").strip()
    except (OSError, TypeError):
        return None
    look = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}).get("look") or {}
    lt = look.get("threed") if isinstance(look.get("threed"), dict) else {}
    if (chosen(job) or str(lt.get("preset") or "")) != "designed":
        return None
    return lt.get("designed") if isinstance(lt.get("designed"), dict) else {}


def _with_palette(engine):
    """The 3D director's creative direction carries the recipe's palette when the creator gave none."""
    original = getattr(engine, "_extra_block", None)
    if original is None or getattr(original, "_cr_palette", False):
        return

    def _extra_block():
        out = original() or ""
        job = _job()
        lines = []
        if job and "3D SCENE PALETTE" not in out:
            palette = recipe_palette(engine, job)
            if palette:
                lines.append(PALETTE_LINE.format(palette))
        spec = recipe_designed(engine, job) if job else None
        if spec is not None and "DESIGNED CAST" not in out:
            import cr_designed
            cast = cr_designed.cast_lines(spec)
            if cast:
                lines.append(cast)
        for line in lines:
            out = (out.rstrip("\n") + "\n" + line + "\n") if out else (
                "\n\nEXTRA INSTRUCTIONS FROM THE CREATOR FOR THIS VIDEO — follow them wherever they apply:\n" + line + "\n")
        return out
    _extra_block._cr_palette = True
    engine._extra_block = _extra_block


def install(engine=None):
    if engine is not None:
        _with_palette(engine)
    try:
        import threed
    except Exception:                                           # noqa: BLE001 - no 3D here
        return
    if getattr(threed._cfg, "_cr_look", False):
        return
    original_cfg = threed._cfg

    def _cfg(engine_, style):
        c = original_cfg(engine_, style)
        job = _job()
        look = chosen(job) if job else ""
        if look and look in (getattr(threed.SB, "PRESETS", None) or LOOKS):
            c["preset"] = look
        return c
    _cfg._cr_look = True
    threed._cfg = _cfg

    def remembering(fn):
        def run(*args, **kwargs):
            # the job folder: _add_to_timeline(segs, srt, job, ...) is handed the subtitle file first
            job = kwargs.get("job") or next((a for a in args if isinstance(a, Path) and a.is_dir()), None)
            before, active = getattr(_current, "job", None), _ACTIVE.get("job")
            _current.job = _ACTIVE["job"] = job
            try:
                return fn(*args, **kwargs)
            finally:
                _current.job, _ACTIVE["job"] = before, active
        return run
    for name in ("_add_to_timeline", "_repair_cli", "_cli_setup"):
        if hasattr(threed, name):
            setattr(threed, name, remembering(getattr(threed, name)))
    _with_designed(threed, engine)


def _with_designed(threed, engine=None):
    """Every video.json of a designed-look video carries the channel's archetype on each scene, stands in a designed
    set and gives every CastDef its designed block (cr_designed.stamp); other looks are untouched."""
    original = getattr(threed, "_video", None)
    if original is None or getattr(original, "_cr_designed", False):
        return

    def _video(board, sub=None, preset=None):
        video = original(board, sub, preset)
        try:
            look = threed.SB.look_id(preset) if hasattr(threed, "SB") and hasattr(threed.SB, "look_id") else str(preset or "")
        except Exception:                                   # noqa: BLE001 - the engine's own json stands
            look = ""
        if look != "designed":
            return video
        import sys
        import cr_designed
        job = _job()
        spec = recipe_designed(engine or sys.modules.get("make_video"), job) if job else None
        return cr_designed.stamp(video, spec or {})
    _video._cr_designed = True
    threed._video = _video
