"""Tutorial-thin free quotas + upgrade CTAs for ChannelRecipe MCP.

MCP is a paid converter: free gets a taste of the live niche library;
Starter unlocks the database; Daily gets higher volume.
"""

from __future__ import annotations

UPGRADE_URL = "https://channelrecipe.com/app#billing"
UPGRADE_CTA = (
    f"Upgrade at {UPGRADE_URL} — Starter unlocks the full live niche database "
    "inside Claude. Daily unlocks higher volume for daily publishing."
)

# Free = barely enough to feel the product, then hit a wall
FREE_NICHES = 1
FREE_SUBJECTS = 3
FREE_CHANNELS = 2
FREE_SCRIPTS = 1  # lifetime per account
FREE_THUMBS = 1  # lifetime per account

# Paid tiers (Starter / Daily). Legacy "pro" counts as Daily-level.
STARTER_NICHES = 15
STARTER_SUBJECTS = 15
STARTER_CHANNELS = 15

DAILY_NICHES = 40
DAILY_SUBJECTS = 40
DAILY_CHANNELS = 40

PAID_PLANS = frozenset({"starter", "daily", "pro"})


def is_paid_plan(plan: str | None) -> bool:
    return (plan or "free").lower() in PAID_PLANS


def _tier(plan: str | None) -> str:
    p = (plan or "free").lower()
    if p in ("daily", "pro"):
        return "daily"
    if p == "starter":
        return "starter"
    return "free"


def discovery_niche_limit(plan: str | None) -> int:
    t = _tier(plan)
    if t == "daily":
        return DAILY_NICHES
    if t == "starter":
        return STARTER_NICHES
    return FREE_NICHES


def discovery_subject_limit(plan: str | None) -> int:
    t = _tier(plan)
    if t == "daily":
        return DAILY_SUBJECTS
    if t == "starter":
        return STARTER_SUBJECTS
    return FREE_SUBJECTS


def discovery_channel_limit(plan: str | None) -> int:
    t = _tier(plan)
    if t == "daily":
        return DAILY_CHANNELS
    if t == "starter":
        return STARTER_CHANNELS
    return FREE_CHANNELS


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
        + UPGRADE_CTA
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
        + UPGRADE_CTA
    )


def with_upgrade_hint(payload: dict, *, free_capped: bool) -> dict:
    out = dict(payload)
    if free_capped:
        out["upgrade"] = UPGRADE_CTA
        out["upgrade_url"] = UPGRADE_URL
        out["upgrade_plans"] = {
            "starter": "Unlock the full live niche database in Claude + Niche Finder in the app.",
            "daily": "Higher MCP volume for researching and publishing every day.",
        }
    return out


def free_limit_reached_payload(*, kind: str, plan: str) -> dict:
    """Hard stop message Claude should relay to the user."""
    return {
        "status": "limit_reached",
        "plan": plan or "free",
        "error": (
            f"Free MCP {kind} limit reached. "
            "Upgrade to unlock the full ChannelRecipe niche database in Claude."
        ),
        "upgrade": UPGRADE_CTA,
        "upgrade_url": UPGRADE_URL,
        "upgrade_plans": {
            "starter": "Full niche database access.",
            "daily": "Full database + higher daily volume.",
        },
    }
