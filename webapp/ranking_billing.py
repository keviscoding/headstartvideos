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
    paid_cost: int = 1,
    commentary: bool = False,
    commentary_cost: int = 1,
) -> int:
    """Credits to charge for one ranking cook. 0 while trial quota remains."""
    if is_admin:
        return 0
    if is_trial and int(cooks_used or 0) < int(trial_limit or 2):
        return 0
    base = max(1, int(paid_cost or 1))
    if commentary:
        base += max(0, int(commentary_cost or 0))
    return base
