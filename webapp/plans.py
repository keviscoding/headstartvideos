"""Subscription plans in one place: tiers, Stripe prices, allowances, Studio access.

Stored plan values (``users.plan``):

* ``free`` — no subscription.
* ``<tier>`` — active paid subscription to that tier.
* ``<tier>_trial`` — the 7-day card-backed trial of that tier.
* ``pro`` — legacy single-tier subscribers, served as Creator.

Tier ids are database values and never change; ``label`` is what customers see.
``daily`` is the original $49 tier and is now sold as Creator, so existing Daily
subscribers become Creator without a Stripe or database migration. Starter is
kept for existing subscribers but is no longer offered.

Price ids are read from ``config`` on every call so deploy-time configuration
(and tests that monkeypatch it) take effect without a restart.
"""
from __future__ import annotations

from dataclasses import dataclass

import config

ANNUAL_MONTHS = 12
TRIAL_STYLE_CLONES = 1


@dataclass(frozen=True)
class Tier:
    id: str
    label: str
    monthly_usd: int
    annual_usd: int
    monthly_credits: int
    studio: bool
    # Completed or running style studies a customer may keep. None = unlimited.
    style_clones: int | None
    # Offered to new customers on the pricing page.
    public: bool
    # config attributes holding this tier's monthly and annual Stripe prices.
    price_attrs: tuple[str, str]


TIERS: dict[str, Tier] = {
    "starter": Tier("starter", "Starter", 27, 270, 15, studio=False, style_clones=0, public=False,
                    price_attrs=("STRIPE_PRICE_STARTER_MONTHLY", "STRIPE_PRICE_STARTER_ANNUAL")),
    "daily": Tier("daily", "Creator", 49, 490, 35, studio=True, style_clones=3, public=True,
                  price_attrs=("STRIPE_PRICE_DAILY_MONTHLY", "STRIPE_PRICE_DAILY_ANNUAL")),
    "studio_pro": Tier("studio_pro", "Pro", 79, 790, 60, studio=True, style_clones=15, public=True,
                       price_attrs=("STRIPE_PRICE_PRO_MONTHLY", "STRIPE_PRICE_PRO_ANNUAL")),
    "studio_max": Tier("studio_max", "Max", 119, 1190, 100, studio=True, style_clones=None, public=True,
                       price_attrs=("STRIPE_PRICE_MAX_MONTHLY", "STRIPE_PRICE_MAX_ANNUAL")),
}
PAID_TIERS = tuple(TIERS)
PUBLIC_TIERS = tuple(t for t, spec in TIERS.items() if spec.public)
# Legacy plan values that are paid subscriptions served as another tier.
LEGACY_PLANS = {"pro": "daily"}
TRIAL_SUFFIX = "_trial"


def _norm(plan: str | None) -> str:
    return (plan or "").strip().lower()


def tier_of(plan: str | None) -> str:
    """The tier a stored plan value belongs to, or '' for free/unknown plans."""
    p = _norm(plan)
    if p.endswith(TRIAL_SUFFIX):
        p = p[: -len(TRIAL_SUFFIX)]
    p = LEGACY_PLANS.get(p, p)
    return p if p in TIERS else ""


def is_trial_plan(plan: str | None) -> bool:
    p = _norm(plan)
    return p.endswith(TRIAL_SUFFIX) and p[: -len(TRIAL_SUFFIX)] in TIERS


def is_paid_plan(plan: str | None) -> bool:
    """A paying subscription (not a trial)."""
    p = _norm(plan)
    return p in TIERS or p in LEGACY_PLANS


def is_active_plan(plan: str | None) -> bool:
    """Paying or trialing — anything that isn't free."""
    return is_paid_plan(plan) or is_trial_plan(plan)


def trial_plan(tier: str) -> str:
    return tier + TRIAL_SUFFIX


def label(plan: str | None) -> str:
    tier = tier_of(plan)
    if not tier:
        return "Free"
    name = TIERS[tier].label
    return f"{name} (trial)" if is_trial_plan(plan) else name


def allowance(tier: str, interval: str = "monthly") -> int:
    """Cloud credits one invoice at this tier buys (annual = 12 months up front)."""
    spec = TIERS.get(tier_of(tier)) or TIERS["starter"]
    return spec.monthly_credits * (ANNUAL_MONTHS if interval == "annual" else 1)


def plan_key(tier: str, interval: str) -> str:
    return f"{tier}_{'annual' if interval == 'annual' else 'monthly'}"


def parse_plan_key(key: str | None) -> tuple[str, str]:
    """('daily', 'annual') from 'daily_annual'. Legacy 'monthly'/'annual' mean Starter."""
    k = _norm(key)
    if k in ("monthly", "annual"):
        return "starter", k
    for interval in ("monthly", "annual"):
        suffix = "_" + interval
        if k.endswith(suffix) and k[: -len(suffix)] in TIERS:
            return k[: -len(suffix)], interval
    return "", ""


def price_for(tier: str, interval: str) -> str:
    """Stripe price id for a tier and billing interval, or '' when not configured."""
    spec = TIERS.get(tier)
    if not spec:
        return ""
    attr = spec.price_attrs[1 if interval == "annual" else 0]
    price = (getattr(config, attr, "") or "").strip()
    if not price and tier == "starter":
        legacy = "STRIPE_PRICE_ID_ANNUAL" if interval == "annual" else "STRIPE_PRICE_ID"
        price = (getattr(config, legacy, "") or "").strip()
    return price


def _tier_prices(tier: str) -> dict[str, str]:
    """Every configured price id for a tier, mapped to its interval."""
    spec = TIERS[tier]
    out = {}
    for interval, attr in zip(("monthly", "annual"), spec.price_attrs):
        pid = (getattr(config, attr, "") or "").strip()
        if pid:
            out[pid] = interval
    if tier == "starter":
        for interval, attr in (("monthly", "STRIPE_PRICE_ID"), ("annual", "STRIPE_PRICE_ID_ANNUAL")):
            pid = (getattr(config, attr, "") or "").strip()
            if pid:
                out[pid] = interval
    return out


def tier_from_price_id(price_id: str | None) -> str:
    pid = (price_id or "").strip()
    if not pid:
        return ""
    for tier in TIERS:
        if pid in _tier_prices(tier):
            return tier
    return ""


def interval_from_price_id(price_id: str | None) -> str:
    """'annual' or 'monthly' for a subscription price, defaulting to monthly."""
    pid = (price_id or "").strip()
    for tier in TIERS:
        interval = _tier_prices(tier).get(pid)
        if interval:
            return interval
    return "monthly"


def subscription_price_ids() -> set[str]:
    return {pid for tier in TIERS for pid in _tier_prices(tier)}


def annual_price_ids() -> tuple[str, ...]:
    return tuple(pid for tier in TIERS for pid, interval in _tier_prices(tier).items()
                 if interval == "annual")


def has_studio(plan: str | None, *, is_admin: bool = False) -> bool:
    """May this account use the downloadable ChannelRecipe Studio?"""
    if is_admin:
        return True
    tier = tier_of(plan)
    return bool(tier) and TIERS[tier].studio


def style_clone_limit(plan: str | None, *, is_admin: bool = False) -> int | None:
    """How many learned styles the account may keep. None means unlimited."""
    if is_admin:
        return None
    if not has_studio(plan):
        return 0
    if is_trial_plan(plan):
        return TRIAL_STYLE_CLONES
    return TIERS[tier_of(plan)].style_clones


def studio_upgrade_tier(plan: str | None, *, is_admin: bool = False) -> str:
    """The cheapest public tier that raises this account's Studio limits, or ''."""
    if is_admin:
        return ""
    current = tier_of(plan)
    order = list(PUBLIC_TIERS)
    if current not in order:
        return order[0]
    index = order.index(current)
    return order[index + 1] if index + 1 < len(order) else ""


def catalog() -> list[dict]:
    """Public tiers for pricing UIs. `available` is False until both prices exist."""
    out = []
    for tier in PUBLIC_TIERS:
        spec = TIERS[tier]
        out.append({
            "tier": tier,
            "label": spec.label,
            "monthly_usd": spec.monthly_usd,
            "annual_usd": spec.annual_usd,
            "monthly_credits": spec.monthly_credits,
            "style_clones": spec.style_clones,
            "studio": spec.studio,
            "available": bool(price_for(tier, "monthly")),
            "annual_available": bool(price_for(tier, "annual")),
        })
    return out
