"""Unit tests for storyboard billing math, trial gates, and sauce catalog costs.

Run from videofactory/:
  pip install pytest
  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.storyboard_billing import (
    animate_credit_cost,
    niche_finder_can_browse,
    pack_credit_cost,
    trial_pack_allowed,
)
from webapp.resources_catalog import RESOURCES, get_resource


class TestPackCredits:
    def test_trial_packs_are_free(self):
        assert pack_credit_cost(8, is_trial=True) == 0
        assert pack_credit_cost(25, is_trial=True) == 0

    def test_paid_eight_minutes_is_four(self):
        assert pack_credit_cost(8, is_trial=False, credits_per_2_min=1, credits_min=1) == 4

    def test_paid_twenty_five_minutes_is_thirteen(self):
        assert pack_credit_cost(25, is_trial=False, credits_per_2_min=1, credits_min=1) == 13

    def test_preview_caps_minutes(self):
        # preview forces ~1.2 min → ceil(1.2/2)=1
        assert pack_credit_cost(8, pack_mode="preview", is_trial=False, credits_per_2_min=1, credits_min=1) == 1

    def test_floor_enforced(self):
        assert pack_credit_cost(0.5, is_trial=False, credits_per_2_min=1, credits_min=2) == 2


class TestAnimateCredits:
    def test_flat_twelve(self):
        assert animate_credit_cost(8, flat=12) == 12
        assert animate_credit_cost(1, flat=12) == 12

    def test_byok_half_min_one(self):
        assert animate_credit_cost(8, flat=12, byok=True) == 6
        assert animate_credit_cost(8, flat=1, byok=True) == 1


class TestTrialPackQuota:
    def test_two_free_packs(self):
        assert trial_pack_allowed(packs_used=0, trial_limit=2, is_trial=True) is True
        assert trial_pack_allowed(packs_used=1, trial_limit=2, is_trial=True) is True
        assert trial_pack_allowed(packs_used=2, trial_limit=2, is_trial=True) is False

    def test_paid_unlimited_by_quota(self):
        assert trial_pack_allowed(packs_used=99, is_trial=False) is True

    def test_admin_bypass(self):
        assert trial_pack_allowed(packs_used=99, is_trial=True, is_admin=True) is True


class TestNicheFinderBrowse:
    def test_paid_only(self):
        assert niche_finder_can_browse("starter") is True
        assert niche_finder_can_browse("daily") is True
        assert niche_finder_can_browse("pro") is True
        assert niche_finder_can_browse("starter_trial") is False
        assert niche_finder_can_browse("daily_trial") is False
        assert niche_finder_can_browse("free") is False

    def test_admin(self):
        assert niche_finder_can_browse("free", is_admin=True) is True


class TestResourcesCatalog:
    def test_paid_guide_costs_55(self):
        item = get_resource("3d-kids-animation-pro-system")
        assert item is not None
        assert int(item.get("credit_cost") or 0) == 55
        assert (item.get("unlock_url") or "").startswith("https://")

    def test_catalog_has_free_and_paid(self):
        costs = [int(r.get("credit_cost") or 0) for r in RESOURCES]
        assert any(c == 0 for c in costs)
        assert any(c > 0 for c in costs)
