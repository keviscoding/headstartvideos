"""Tests for resolving the paid tier from Stripe prices.

The bug behind these: renewal credits were derived from `users.plan`, but a
portal upgrade changes the Stripe price without touching that row. A customer
who moved to Daily kept getting Starter's 15 credits every month, forever.

Run from videofactory/:
  python -m pytest tests/test_stripe_plan_resolution.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
import webapp.server as server

STARTER_M = "price_starter_monthly"
STARTER_A = "price_starter_annual"
DAILY_M = "price_daily_monthly"
DAILY_A = "price_daily_annual"


@pytest.fixture(autouse=True)
def prices(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_PRICE_STARTER_MONTHLY", STARTER_M)
    monkeypatch.setattr(config, "STRIPE_PRICE_STARTER_ANNUAL", STARTER_A)
    monkeypatch.setattr(config, "STRIPE_PRICE_DAILY_MONTHLY", DAILY_M)
    monkeypatch.setattr(config, "STRIPE_PRICE_DAILY_ANNUAL", DAILY_A)
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "")
    monkeypatch.setattr(config, "STRIPE_PRICE_ID_ANNUAL", "")


def _invoice(*lines) -> dict:
    return {"lines": {"data": list(lines)}}


def _line(price_id, amount, *, shape="price") -> dict:
    if shape == "price":
        return {"price": {"id": price_id}, "amount": amount}
    if shape == "plan":
        return {"plan": {"id": price_id}, "amount": amount}
    return {"pricing": {"price_details": {"price": price_id}}, "amount": amount}


class TestTierFromPriceId:
    @pytest.mark.parametrize("pid,tier", [
        (STARTER_M, "starter"), (STARTER_A, "starter"),
        (DAILY_M, "daily"), (DAILY_A, "daily"),
    ])
    def test_known_prices(self, pid, tier):
        assert server._tier_from_price_id(pid) == tier

    @pytest.mark.parametrize("pid", ["", "   ", "price_unknown", None])
    def test_unknown_prices_are_blank(self, pid):
        assert server._tier_from_price_id(pid) == ""

    def test_unset_config_never_matches_empty_price(self, monkeypatch):
        """Blank config must not make an empty price id resolve to a tier."""
        monkeypatch.setattr(config, "STRIPE_PRICE_STARTER_MONTHLY", "")
        monkeypatch.setattr(config, "STRIPE_PRICE_STARTER_ANNUAL", "")
        monkeypatch.setattr(config, "STRIPE_PRICE_DAILY_MONTHLY", "")
        monkeypatch.setattr(config, "STRIPE_PRICE_DAILY_ANNUAL", "")

        assert server._tier_from_price_id("") == ""
        assert server._tier_from_price_id("anything") == ""

    def test_legacy_single_price_id_maps_to_starter(self, monkeypatch):
        monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_legacy")
        assert server._tier_from_price_id("price_legacy") == "starter"


class TestTierFromInvoice:
    def test_simple_renewal(self):
        assert server._tier_from_invoice(_invoice(_line(DAILY_M, 4900))) == "daily"

    def test_upgrade_proration_picks_the_new_plan(self):
        """Stripe credits the unused Starter time as a negative line."""
        invoice = _invoice(_line(STARTER_M, -1200), _line(DAILY_M, 4900))

        assert server._tier_from_invoice(invoice) == "daily"

    def test_proration_order_does_not_matter(self):
        invoice = _invoice(_line(DAILY_M, 4900), _line(STARTER_M, -1200))

        assert server._tier_from_invoice(invoice) == "daily"

    def test_downgrade_proration_picks_starter(self):
        invoice = _invoice(_line(DAILY_M, -2000), _line(STARTER_M, 2700))

        assert server._tier_from_invoice(invoice) == "starter"

    @pytest.mark.parametrize("shape", ["price", "plan", "basil"])
    def test_all_line_shapes_are_read(self, shape):
        invoice = _invoice(_line(DAILY_A, 49000, shape=shape))

        assert server._tier_from_invoice(invoice) == "daily"

    def test_unrecognized_lines_yield_blank(self):
        """Top-ups and one-offs must not look like a subscription tier."""
        invoice = _invoice(_line("price_topup_5", 500))

        assert server._tier_from_invoice(invoice) == ""

    @pytest.mark.parametrize("invoice", [{}, {"lines": {}}, {"lines": {"data": []}},
                                        {"lines": {"data": [None, "x"]}}])
    def test_malformed_invoices_are_safe(self, invoice):
        assert server._tier_from_invoice(invoice) == ""


class TestTierFromSubscription:
    def test_reads_subscription_items(self):
        sub = {"items": {"data": [_line(DAILY_M, 4900)]}}

        assert server._tier_from_subscription(sub) == "daily"

    @pytest.mark.parametrize("sub", [{}, {"items": {}}, {"items": {"data": []}}])
    def test_malformed_subscriptions_are_safe(self, sub):
        assert server._tier_from_subscription(sub) == ""


class TestSubscriptionPriceChanged:
    @pytest.mark.parametrize("previous", [
        {"items": {}}, {"plan": {}}, {"quantity": 1},
    ])
    def test_priced_fields_count_as_a_change(self, previous):
        assert server._subscription_price_changed(previous) is True

    @pytest.mark.parametrize("previous", [
        {}, None, "nope",
        {"default_payment_method": "pm_x"},
        {"current_period_end": 123},
        {"latest_invoice": "in_x"},
    ])
    def test_unrelated_updates_do_not_count(self, previous):
        """A card change must never re-grant upgrade credits."""
        assert server._subscription_price_changed(previous) is False


class TestTierAllowance:
    def test_known_tiers(self):
        assert server._tier_allowance("starter") == 15
        assert server._tier_allowance("daily") == 35

    def test_unknown_tier_defaults_to_starter(self):
        assert server._tier_allowance("mystery") == 15


class TestUpgradeGrant:
    """Upgrades grant the difference only, net of what this period already bought."""

    def test_starter_to_daily_grants_twenty(self):
        assert server._upgrade_grant({"period_tier": "starter"}, "starter", "daily") == 20

    def test_downgrade_grants_nothing(self):
        assert server._upgrade_grant({"period_tier": "daily"}, "daily", "starter") == 0

    def test_same_tier_grants_nothing(self):
        assert server._upgrade_grant({"period_tier": "daily"}, "daily", "daily") == 0

    def test_missing_ratchet_falls_back_to_current_plan(self):
        """Rows predating the period_tier column must still upgrade correctly."""
        for row in ({}, {"period_tier": ""}, {"period_tier": None}, {"period_tier": "  "}):
            assert server._upgrade_grant(row, "starter", "daily") == 20

    def test_top_up_credits_do_not_block_the_grant(self):
        """Capping on credits held would have starved anyone holding top-ups."""
        row = {"period_tier": "starter", "credits": 500}

        assert server._upgrade_grant(row, "starter", "daily") == 20

    def test_downgrade_then_reupgrade_grants_nothing(self):
        """The farming exploit: pay ~nothing in prorations, mint 20 credits."""
        row = {"period_tier": "daily"}  # already credited Daily this period

        assert server._upgrade_grant(row, "starter", "daily") == 0

    def test_toggling_all_period_cannot_farm_credits(self):
        """Full simulation: Daily user toggling plans nets zero extra credits."""
        row = {"plan": "daily", "period_tier": "daily"}
        granted = 0
        for nxt in ("starter", "daily", "starter", "daily", "starter", "daily"):
            delta = server._upgrade_grant(row, row["plan"], nxt)
            granted += delta
            row["plan"] = nxt
            if delta > 0:
                row["period_tier"] = nxt

        assert granted == 0, f"toggling farmed {granted} credits"

    def test_one_upgrade_per_period_is_granted_once(self):
        row = {"plan": "starter", "period_tier": "starter"}
        first = server._upgrade_grant(row, "starter", "daily")
        row["plan"] = row["period_tier"] = "daily"
        second = server._upgrade_grant(row, "daily", "daily")

        assert (first, second) == (20, 0)

    def test_renewal_resets_the_ratchet(self):
        """Next month's invoice sets period_tier, so upgrades work again."""
        row = {"plan": "starter", "period_tier": "daily"}
        assert server._upgrade_grant(row, "starter", "daily") == 0

        row["period_tier"] = "starter"  # invoice.paid for the new period
        assert server._upgrade_grant(row, "starter", "daily") == 20
