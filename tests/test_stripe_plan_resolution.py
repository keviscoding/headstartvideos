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
from fastapi import HTTPException

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


class TestIntervalFromPriceId:
    @pytest.mark.parametrize("pid", [STARTER_A, DAILY_A])
    def test_annual_prices_are_annual(self, pid):
        assert server._interval_from_price_id(pid) == "annual"

    @pytest.mark.parametrize("pid", [STARTER_M, DAILY_M, "", "price_unknown", None])
    def test_everything_else_is_monthly(self, pid):
        assert server._interval_from_price_id(pid) == "monthly"


class TestPriceForTier:
    @pytest.mark.parametrize("tier,interval,want", [
        ("starter", "monthly", STARTER_M), ("starter", "annual", STARTER_A),
        ("daily", "monthly", DAILY_M), ("daily", "annual", DAILY_A),
    ])
    def test_resolves_each_combination(self, tier, interval, want):
        assert server._price_for_tier(tier, interval) == want

    def test_unknown_tier_is_blank(self):
        assert server._price_for_tier("mystery", "monthly") == ""


class _FakeStripe:
    """Minimal Stripe stand-in recording the modify call."""

    def __init__(self, price_id, item_id="si_1"):
        self._sub = {"items": {"data": [{"id": item_id, "price": {"id": price_id}}]}}
        self.modified = []

        outer = self

        class Subscription:
            @staticmethod
            def retrieve(sub_id):
                return outer._sub

            @staticmethod
            def modify(sub_id, **kwargs):
                outer.modified.append((sub_id, kwargs))
                return outer._sub

        self.Subscription = Subscription


class TestSwitchTrialPrice:
    @pytest.fixture(autouse=True)
    def no_db(self, monkeypatch):
        self.updates = []
        monkeypatch.setattr(server, "update_user",
                            lambda uid, **f: self.updates.append((uid, f)))

    def test_monthly_starter_trial_moves_to_monthly_daily(self):
        fake = _FakeStripe(STARTER_M)

        server._switch_trial_price(fake, "sub_1", "daily", 7)

        assert len(fake.modified) == 1
        _, kwargs = fake.modified[0]
        assert kwargs["items"][0]["price"] == DAILY_M
        assert kwargs["proration_behavior"] == "none"

    def test_annual_trial_stays_annual(self):
        """Switching tier must not quietly move an annual customer to monthly."""
        fake = _FakeStripe(STARTER_A)

        server._switch_trial_price(fake, "sub_1", "daily", 7)

        assert fake.modified[0][1]["items"][0]["price"] == DAILY_A

    def test_local_plan_is_written_so_state_matches_stripe(self):
        fake = _FakeStripe(STARTER_M)

        server._switch_trial_price(fake, "sub_1", "daily", 7)

        assert self.updates == [(7, {"plan": "daily_trial"})]

    def test_same_price_is_a_noop(self):
        """Confirming the tier you already have must not touch Stripe."""
        fake = _FakeStripe(DAILY_M)

        server._switch_trial_price(fake, "sub_1", "daily", 7)

        assert fake.modified == []
        assert self.updates == []

    def test_subscription_without_items_is_rejected(self):
        fake = _FakeStripe(STARTER_M)
        fake._sub = {"items": {"data": []}}

        with pytest.raises(HTTPException) as e:
            server._switch_trial_price(fake, "sub_1", "daily", 7)
        assert e.value.status_code == 400

    def test_unconfigured_target_price_is_rejected(self, monkeypatch):
        monkeypatch.setattr(config, "STRIPE_PRICE_DAILY_MONTHLY", "")
        fake = _FakeStripe(STARTER_M)

        with pytest.raises(HTTPException) as e:
            server._switch_trial_price(fake, "sub_1", "daily", 7)
        assert e.value.status_code == 400
        assert fake.modified == []
