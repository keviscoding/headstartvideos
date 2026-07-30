"""Tutorial-thin free quotas + upgrade CTAs for ChannelRecipe MCP."""

from __future__ import annotations

UPGRADE_URL = "https://channelrecipe.com/app#billing"
UPGRADE_CTA = (
    f"Upgrade at {UPGRADE_URL} — Starter unlocks the full niche library, "
    "unlimited scripts, and more thumbnails so you can publish every week."
)

# Free caps (barely enough to finish the tutorial once)
FREE_SUBJECTS = 5
FREE_CHANNELS = 3
FREE_SCRIPTS = 1  # lifetime per account
FREE_THUMBS = 1  # lifetime per account

# Paid plans that lift MCP volume gates
PAID_PLANS = frozenset({"starter", "daily", "pro"})


def is_paid_plan(plan: str | None) -> bool:
    return (plan or "free").lower() in PAID_PLANS


def discovery_subject_limit(plan: str | None) -> int:
    return 40 if is_paid_plan(plan) else FREE_SUBJECTS


def discovery_channel_limit(plan: str | None) -> int:
    return 40 if is_paid_plan(plan) else FREE_CHANNELS


def script_allowed(user: dict | None) -> tuple[bool, str]:
    """Return (ok, message). message includes CTA when blocked."""
    if not user:
        return False, (
            "Create a free ChannelRecipe account and paste your MCP API key "
            f"to generate one tutorial script. Then {UPGRADE_CTA}"
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
            "Create a free ChannelRecipe account and paste your MCP API key "
            f"for one free tutorial thumbnail. Then {UPGRADE_CTA}"
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
    return out
