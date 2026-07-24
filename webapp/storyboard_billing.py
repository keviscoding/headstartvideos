"""Pure storyboard credit math — kept free of FastAPI so unit tests can import it."""
from __future__ import annotations

import math
import os


def pack_credit_cost(
    target_minutes: float,
    *,
    pack_mode: str = "full",
    is_trial: bool = False,
    credits_per_2_min: int | None = None,
    credits_min: int | None = None,
) -> int:
    """Credits for a stills pack. Trial packs are free (quota-limited elsewhere)."""
    if is_trial:
        return 0
    mins = float(target_minutes or 8)
    if (pack_mode or "").strip().lower() == "preview":
        mins = min(mins, 1.2)
    per_2 = max(
        1,
        int(
            credits_per_2_min
            if credits_per_2_min is not None
            else (os.getenv("STORYBOARD_PACK_CREDITS_PER_2_MIN", "1") or 1)
        ),
    )
    floor = max(
        0,
        int(
            credits_min
            if credits_min is not None
            else (os.getenv("STORYBOARD_PACK_CREDITS_MIN", "1") or 1)
        ),
    )
    raw = int(math.ceil(max(mins, 0.5) / 2.0)) * per_2
    return max(floor, raw)


def animate_credit_cost(
    target_minutes: float,
    *,
    byok: bool = False,
    flat: int | None = None,
    per_min: float | None = None,
    floor: int | None = None,
) -> int:
    """Credits for on-site Seedance cook. Flat charge preferred (default 12)."""
    flat_v = int(
        flat
        if flat is not None
        else (os.getenv("STORYBOARD_ANIMATE_CREDITS_FLAT", "12") or 12)
    )
    if flat_v > 0:
        cost = flat_v
    else:
        per = float(
            per_min
            if per_min is not None
            else (os.getenv("STORYBOARD_ANIMATE_CREDITS_PER_MIN", "0") or 0)
        )
        fl = int(
            floor
            if floor is not None
            else (os.getenv("STORYBOARD_ANIMATE_CREDITS_MIN", "0") or 0)
        )
        if per <= 0 and fl <= 0:
            return 0
        cost = max(fl, int(math.ceil(max(target_minutes, 0.5) * per)))
    if byok:
        return max(1, int(math.ceil(cost / 2.0)))
    return cost


def niche_finder_can_browse(plan: str, *, is_admin: bool = False) -> bool:
    if is_admin:
        return True
    return (plan or "free").lower() in ("starter", "daily", "pro")


def trial_pack_allowed(
    *,
    packs_used: int,
    trial_limit: int = 2,
    is_trial: bool = True,
    is_admin: bool = False,
) -> bool:
    if is_admin or not is_trial:
        return True
    return int(packs_used or 0) < int(trial_limit or 2)
