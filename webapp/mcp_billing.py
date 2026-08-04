"""Tutorial-thin free quotas + upgrade CTAs for ChannelRecipe MCP.

MCP niche *browsing* is DB-only (no per-call COGS). Caps are a conversion lever:
free gets a taste, trial gets a limited mid-tier library, Starter/Daily unlock full.
Script / thumbnail / transcript tools may still hit paid providers.
"""

from __future__ import annotations

UPGRADE_URL = "https://channelrecipe.com/app#billing"
UPGRADE_CTA = (
    f"Upgrade at {UPGRADE_URL} — Starter unlocks the full live niche database "
    "inside Claude + Niche Finder in the app. Daily unlocks higher volume for "
    "daily publishing."
)
TRIAL_UPGRADE_CTA = (
    "Your free trial includes a limited niche database in Claude. "
    + UPGRADE_CTA
)

# Free = barely enough to feel the product, then hit a wall
FREE_NICHES = 1
FREE_SUBJECTS = 3
FREE_CHANNELS = 2
FREE_SCRIPTS = 1  # lifetime per account
FREE_THUMBS = 1  # lifetime per account

# Trial = 3× prior mid-tier (5/8/8 → 15/24/24). Starter stays clearly above.
TRIAL_NICHES = 15
TRIAL_SUBJECTS = 24
TRIAL_CHANNELS = 24

# Paid tiers (Starter / Daily). Legacy "pro" counts as Daily-level.
STARTER_NICHES = 30
STARTER_SUBJECTS = 30
STARTER_CHANNELS = 30

DAILY_NICHES = 50
DAILY_SUBJECTS = 50
DAILY_CHANNELS = 50

PAID_PLANS = frozenset({"starter", "daily", "pro"})


def is_paid_plan(plan: str | None) -> bool:
    """Full paid subscription (not trial). Unlocks full MCP library + paid tools."""
    return (plan or "free").lower() in PAID_PLANS


def is_trial_plan(plan: str | None) -> bool:
    p = (plan or "").lower()
    return p.endswith("_trial") or p in ("starter_trial", "daily_trial")


def discovery_capped(plan: str | None) -> bool:
    """True when the plan does not have the full paid niche library."""
    return not is_paid_plan(plan)


def _tier(plan: str | None) -> str:
    p = (plan or "free").lower()
    if p in ("daily", "pro"):
        return "daily"
    if p == "starter":
        return "starter"
    if is_trial_plan(p):
        return "trial"
    return "free"


def discovery_niche_limit(plan: str | None) -> int:
    t = _tier(plan)
    if t == "daily":
        return DAILY_NICHES
    if t == "starter":
        return STARTER_NICHES
    if t == "trial":
        return TRIAL_NICHES
    return FREE_NICHES


def discovery_subject_limit(plan: str | None) -> int:
    t = _tier(plan)
    if t == "daily":
        return DAILY_SUBJECTS
    if t == "starter":
        return STARTER_SUBJECTS
    if t == "trial":
        return TRIAL_SUBJECTS
    return FREE_SUBJECTS


def discovery_channel_limit(plan: str | None) -> int:
    t = _tier(plan)
    if t == "daily":
        return DAILY_CHANNELS
    if t == "starter":
        return STARTER_CHANNELS
    if t == "trial":
        return TRIAL_CHANNELS
    return FREE_CHANNELS


def discovery_limits(plan: str | None) -> dict[str, int]:
    return {
        "niches": discovery_niche_limit(plan),
        "subjects": discovery_subject_limit(plan),
        "channels": discovery_channel_limit(plan),
        "scripts": FREE_SCRIPTS,
        "thumbnails": FREE_THUMBS,
    }


def upgrade_cta_for(plan: str | None) -> str:
    if is_trial_plan(plan):
        return TRIAL_UPGRADE_CTA
    return UPGRADE_CTA


def script_allowed(user: dict | None) -> tuple[bool, str]:
    if not user:
        return False, (
            "Create a free ChannelRecipe account, connect MCP, then you get one "
            f"tutorial script. After that: {UPGRADE_CTA}"
        )
    if is_paid_plan(user.get("plan")):
        return True, ""
    used = int(user.get("mcp_free_scripts_used") or 0)
    if used < FREE_SCRIPTS:
        return True, ""
    return False, (
        "Free tutorial script already used. "
        + upgrade_cta_for(user.get("plan"))
    )


def thumbnail_allowed(user: dict | None) -> tuple[bool, str]:
    if not user:
        return False, (
            "Create a free ChannelRecipe account, connect MCP, then you get one "
            f"tutorial thumbnail. After that: {UPGRADE_CTA}"
        )
    if is_paid_plan(user.get("plan")):
        return True, ""
    used = int(user.get("mcp_free_thumbs_used") or 0)
    if used < FREE_THUMBS:
        return True, ""
    return False, (
        "Free tutorial thumbnail already used. "
        + upgrade_cta_for(user.get("plan"))
    )


def with_upgrade_hint(payload: dict, *, free_capped: bool, plan: str | None = None) -> dict:
    out = dict(payload)
    if free_capped:
        out["upgrade"] = upgrade_cta_for(plan or out.get("plan"))
        out["upgrade_url"] = UPGRADE_URL
        out["upgrade_plans"] = {
            "starter": "Unlock the full live niche database in Claude + Niche Finder in the app.",
            "daily": "Higher MCP volume for researching and publishing every day.",
        }
    return out


def paid_required(user: dict | None, *, feature: str) -> tuple[bool, str]:
    """Hard gate for paid-only MCP tools (transcripts, etc.)."""
    if not user:
        return False, (
            f"{feature} requires a ChannelRecipe account. Sign up, connect MCP, "
            f"then upgrade. {UPGRADE_CTA}"
        )
    if is_paid_plan(user.get("plan")):
        return True, ""
    if is_trial_plan(user.get("plan")):
        return False, (
            f"{feature} is a paid MCP feature. "
            + TRIAL_UPGRADE_CTA
        )
    return False, (
        f"{feature} is a paid MCP feature. Free includes a taste of niche discovery only. "
        + UPGRADE_CTA
    )
