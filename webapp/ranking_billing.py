"""Pure ranking-cook credit / trial quota math — FastAPI-free for unit tests."""
from __future__ import annotations


def is_trial_plan(plan: str | None) -> bool:
    return (plan or "").lower() in ("starter_trial", "daily_trial")


def is_paid_plan(plan: str | None, *, is_admin: bool = False) -> bool:
    if is_admin:
        return True
    return (plan or "").lower() in ("starter", "daily", "pro", "starter_trial", "daily_trial")


def trial_ranking_allowed(
    *,
    cooks_used: int,
    trial_limit: int = 2,
    is_trial: bool = True,
    is_admin: bool = False,
) -> bool:
    """Trial users get `trial_limit` free ranking cooks; admins always allowed."""
    if is_admin:
        return True
    if not is_trial:
        return True  # paid non-trial: credit gate elsewhere
    return int(cooks_used or 0) < int(trial_limit or 2)


def ranking_credit_cost(
    *,
    is_trial: bool,
    cooks_used: int,
    trial_limit: int = 2,
    is_admin: bool = False,
    paid_cost: float = 0.5,
    commentary: bool = False,
    commentary_cost: float | None = None,
    commentary_total: float = 1.0,
) -> float:
    """Credits to charge for one ranking cook. 0 while trial quota remains.

    Defaults: 0.5 without AI commentary, 1.0 with commentary.
    `commentary_cost` is legacy (added on top of paid_cost); prefer commentary_total.
    """
    if is_admin:
        return 0.0
    if is_trial and int(cooks_used or 0) < int(trial_limit or 2):
        return 0.0
    base = max(0.0, float(paid_cost if paid_cost is not None else 0.5))
    if not commentary:
        return round(base, 2)
    if commentary_cost is not None:
        # Legacy: base + extra
        return round(base + max(0.0, float(commentary_cost or 0)), 2)
    total = float(commentary_total if commentary_total is not None else 1.0)
    return round(max(base, total), 2)
