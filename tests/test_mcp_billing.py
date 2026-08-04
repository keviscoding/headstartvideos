"""MCP discovery tiers: free taste, trial mid-tier, paid full."""
from webapp.mcp_billing import (
    FREE_CHANNELS,
    FREE_NICHES,
    FREE_SUBJECTS,
    STARTER_NICHES,
    TRIAL_CHANNELS,
    TRIAL_NICHES,
    TRIAL_SUBJECTS,
    discovery_capped,
    discovery_channel_limit,
    discovery_limits,
    discovery_niche_limit,
    discovery_subject_limit,
    is_paid_plan,
    is_trial_plan,
    paid_required,
    script_allowed,
    upgrade_cta_for,
)


def test_trial_is_triple_prior_mid_tier():
    assert TRIAL_NICHES == 15
    assert TRIAL_SUBJECTS == 24
    assert TRIAL_CHANNELS == 24
    assert STARTER_NICHES > TRIAL_NICHES


def test_trial_is_not_paid_for_full_library():
    assert is_trial_plan("starter_trial")
    assert is_trial_plan("daily_trial")
    assert not is_paid_plan("starter_trial")
    assert is_paid_plan("starter")
    assert discovery_capped("starter_trial")
    assert discovery_capped("free")
    assert not discovery_capped("starter")


def test_discovery_limits_ladder():
    assert discovery_niche_limit("free") == FREE_NICHES
    assert discovery_subject_limit("free") == FREE_SUBJECTS
    assert discovery_channel_limit("free") == FREE_CHANNELS

    assert discovery_niche_limit("starter_trial") == TRIAL_NICHES
    assert discovery_subject_limit("daily_trial") == TRIAL_SUBJECTS
    assert discovery_channel_limit("starter_trial") == TRIAL_CHANNELS

    assert discovery_niche_limit("starter") == STARTER_NICHES
    assert discovery_niche_limit("daily") >= STARTER_NICHES


def test_discovery_limits_helper():
    d = discovery_limits("starter_trial")
    assert d["niches"] == TRIAL_NICHES
    assert d["subjects"] == TRIAL_SUBJECTS
    assert d["channels"] == TRIAL_CHANNELS


def test_trial_still_limited_on_paid_only_tools():
    ok, msg = paid_required({"plan": "starter_trial"}, feature="Transcripts")
    assert ok is False
    assert "limited niche" in msg.lower() or "upgrade" in msg.lower()

    ok2, _ = paid_required({"plan": "starter"}, feature="Transcripts")
    assert ok2 is True


def test_trial_script_still_uses_lifetime_free_quota():
    ok, _ = script_allowed({"plan": "starter_trial", "mcp_free_scripts_used": 0})
    assert ok is True
    ok2, msg = script_allowed({"plan": "starter_trial", "mcp_free_scripts_used": 1})
    assert ok2 is False
    assert "upgrade" in msg.lower()


def test_upgrade_cta_mentions_trial_limit():
    assert "limited niche" in upgrade_cta_for("starter_trial").lower()
