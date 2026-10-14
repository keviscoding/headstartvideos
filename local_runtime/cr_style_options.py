"""Learned mixed-media styles offer every option, not only their base recipe's.

A style study starts from a recipe, and the saved style used to inherit that
recipe's short options list (a 2D Life Stories base offered two). A mixed-media
style can use any engine option: they are generic switches that only need the
right account. Reference-animation styles keep their own list on purpose (every
scene is the learned animation), and 3D stays with styles built for it.

Widened in memory each time styles load, so saved style files (and the hashes
that prove a study published them) are never rewritten. Defaults don't change:
options a style didn't have before start unticked.
"""
from __future__ import annotations


def widen(styles: dict, catalog: list) -> list:
    """Add every general option to mixed-media learned styles. Returns the styles changed."""
    general = [row["key"] for row in catalog if isinstance(row, dict) and row.get("key") and not row.get("explicit")]
    changed = []
    for name, data in styles.items():
        learned = data.get("cr_style_clone") if isinstance(data, dict) else None
        if not isinstance(learned, dict) or learned.get("visual_mode") == "reference_only":
            continue
        options = data.get("features") if isinstance(data.get("features"), dict) else {}
        offered = [key for key in options.get("offered") or [] if isinstance(key, str)]
        missing = [key for key in general if key not in offered]
        if not missing:
            continue
        # The engine treats a style without an "on" list as "everything offered
        # is on"; pin today's defaults before offering more.
        if not isinstance(options.get("on"), list):
            options["on"] = list(offered)
        options["offered"] = offered + missing
        data["features"] = options
        changed.append(name)
    return changed


def install(studio):
    """Widen now and every time the engine reloads its styles."""
    import features
    original = studio.styles.load_all
    if getattr(original, "_cr_widened", False):
        return

    def load_all():
        result = original()
        widen(studio.styles.STYLES, features.FEATURES)
        return result

    load_all._cr_widened = True
    studio.styles.load_all = load_all
    if widen(studio.styles.STYLES, features.FEATURES):
        studio.styles.apply_to_engine(studio.mv, studio.motion)
