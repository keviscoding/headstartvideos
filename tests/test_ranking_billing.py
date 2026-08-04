"""Unit tests for ranking trial quota / credit math."""
from webapp.ranking_billing import (
    is_trial_plan,
    is_paid_plan,
    trial_ranking_allowed,
    ranking_credit_cost,
)


def test_trial_plan_detection():
    assert is_trial_plan("starter_trial")
    assert is_trial_plan("daily_trial")
    assert not is_trial_plan("starter")
    assert not is_trial_plan("free")


def test_paid_plan_includes_trial():
    assert is_paid_plan("starter")
    assert is_paid_plan("starter_trial")
    assert is_paid_plan("free", is_admin=True)
    assert not is_paid_plan("free")


def test_trial_quota_two_free():
    assert trial_ranking_allowed(cooks_used=0, trial_limit=2, is_trial=True)
    assert trial_ranking_allowed(cooks_used=1, trial_limit=2, is_trial=True)
    assert not trial_ranking_allowed(cooks_used=2, trial_limit=2, is_trial=True)
    assert trial_ranking_allowed(cooks_used=99, is_trial=False)  # paid: allowed (credits elsewhere)
    assert trial_ranking_allowed(cooks_used=99, is_trial=True, is_admin=True)


def test_credit_cost_zero_during_trial_quota():
    assert ranking_credit_cost(is_trial=True, cooks_used=0, trial_limit=2) == 0
    assert ranking_credit_cost(is_trial=True, cooks_used=1, trial_limit=2) == 0
    assert ranking_credit_cost(is_trial=True, cooks_used=2, trial_limit=2) == 1
    assert ranking_credit_cost(is_trial=False, cooks_used=0, paid_cost=1) == 1
    assert ranking_credit_cost(is_trial=True, cooks_used=0, is_admin=True) == 0
