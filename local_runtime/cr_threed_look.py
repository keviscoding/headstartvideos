"""The 3D look a creator chooses for one video: the recipe's own, white studio (all-white models) or colour film.

A recipe's 3D look is its look.threed.preset (white_studio for the clean 3D reconstruction channels). In Create the
creator may choose another for one video (run_spec cr_threed_look); a colour palette always comes with the colour look,
so its colours show. The engine reads a video's 3D settings without knowing the job, so the job is remembered while
its 3D is made and the choice applied whenever the settings are read.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

LOOKS = ("white_studio", "documentary")
_current = threading.local()


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


def install(engine=None):
    try:
        import threed
    except Exception:                                           # noqa: BLE001 - no 3D here
        return
    if getattr(threed._cfg, "_cr_look", False):
        return
    original_cfg = threed._cfg

    def _cfg(engine_, style):
        c = original_cfg(engine_, style)
        job = getattr(_current, "job", None)
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
            before = getattr(_current, "job", None)
            _current.job = job
            try:
                return fn(*args, **kwargs)
            finally:
                _current.job = before
        return run
    for name in ("_add_to_timeline", "_repair_cli", "_cli_setup"):
        if hasattr(threed, name):
            setattr(threed, name, remembering(getattr(threed, name)))
