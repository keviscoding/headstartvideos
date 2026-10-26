"""Plan tiers: Creator / Pro / Max with Studio access, legacy Starter, trials.

Existing Daily subscribers must become Creator with no migration, Starter must
stay billable for existing subscribers but never be sold again, and every
Stripe path (checkout, renewals, portal changes, trial conversion) must
recognise the new Studio tiers.
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
from webapp import plans

PRICES = {
    "STRIPE_PRICE_STARTER_MONTHLY": "price_starter_m",
    "STRIPE_PRICE_STARTER_ANNUAL": "price_starter_a",
    "STRIPE_PRICE_DAILY_MONTHLY": "price_daily_m",
    "STRIPE_PRICE_DAILY_ANNUAL": "price_daily_a",
    "STRIPE_PRICE_PRO_MONTHLY": "price_pro_m",
    "STRIPE_PRICE_PRO_ANNUAL": "price_pro_a",
    "STRIPE_PRICE_MAX_MONTHLY": "price_max_m",
    "STRIPE_PRICE_MAX_ANNUAL": "price_max_a",
    "STRIPE_PRICE_ID": "",
    "STRIPE_PRICE_ID_ANNUAL": "",
}


@pytest.fixture(autouse=True)
def prices(monkeypatch):
    for attr, value in PRICES.items():
        monkeypatch.setattr(config, attr, value)


class TestPlanValues:
    @pytest.mark.parametrize("plan,tier", [
        ("starter", "starter"), ("daily", "daily"), ("studio_pro", "studio_pro"),
        ("studio_max", "studio_max"), ("daily_trial", "daily"), ("studio_max_trial", "studio_max"),
        ("pro", "daily"), ("free", ""), ("", ""), (None, ""), ("bogus", ""), ("bogus_trial", ""),
    ])
    def test_tier_of(self, plan, tier):
        assert plans.tier_of(plan) == tier

    def test_paid_trial_and_active(self):
        assert plans.is_paid_plan("studio_pro") and not plans.is_trial_plan("studio_pro")
        assert plans.is_trial_plan("studio_pro_trial") and not plans.is_paid_plan("studio_pro_trial")
        assert plans.is_paid_plan("pro"), "legacy single-tier subscribers are paying customers"
        assert not plans.is_active_plan("free")
        assert not plans.is_trial_plan("free_trial")
        assert plans.is_active_plan("starter_trial")

    def test_labels_are_customer_facing_names(self):
        assert plans.label("daily") == "Creator"
        assert plans.label("studio_pro") == "Pro"
        assert plans.label("studio_max_trial") == "Max (trial)"
        assert plans.label("free") == "Free"

    def test_existing_daily_subscribers_keep_their_allowance(self):
        assert plans.allowance("daily") == 35
        assert plans.allowance("daily", "annual") == 420

    def test_new_tier_allowances(self):
        assert plans.allowance("studio_pro") == 60
        assert plans.allowance("studio_max", "annual") == 1200


class TestStudioAccess:
    @pytest.mark.parametrize("plan", ["daily", "pro", "studio_pro", "studio_max", "daily_trial", "studio_max_trial"])
    def test_studio_tiers_and_their_trials_have_studio(self, plan):
        assert plans.has_studio(plan)

    @pytest.mark.parametrize("plan", ["free", "starter", "starter_trial", "", None])
    def test_free_and_starter_do_not(self, plan):
        assert not plans.has_studio(plan)

    def test_admins_always_have_studio(self):
        assert plans.has_studio("free", is_admin=True)
        assert plans.style_clone_limit("free", is_admin=True) is None

    @pytest.mark.parametrize("plan,limit", [
        ("daily", 3), ("pro", 3), ("studio_pro", 15), ("studio_max", None),
        ("daily_trial", 1), ("studio_max_trial", 1), ("starter", 0), ("free", 0),
    ])
    def test_style_clone_limits(self, plan, limit):
        assert plans.style_clone_limit(plan) == limit

    @pytest.mark.parametrize("plan,upgrade", [
        ("free", "daily"), ("starter", "daily"), ("daily", "studio_pro"),
        ("studio_pro", "studio_max"), ("studio_max", ""), ("daily_trial", "studio_pro"),
    ])
    def test_upgrade_path(self, plan, upgrade):
        assert plans.studio_upgrade_tier(plan) == upgrade


class TestPrices:
    @pytest.mark.parametrize("pid,tier,interval", [
        ("price_pro_m", "studio_pro", "monthly"), ("price_pro_a", "studio_pro", "annual"),
        ("price_max_m", "studio_max", "monthly"), ("price_max_a", "studio_max", "annual"),
        ("price_daily_a", "daily", "annual"), ("price_starter_m", "starter", "monthly"),
    ])
    def test_price_ids_resolve(self, pid, tier, interval):
        assert server._tier_from_price_id(pid) == tier
        assert server._interval_from_price_id(pid) == interval

    def test_plan_keys(self):
        assert plans.parse_plan_key("studio_pro_annual") == ("studio_pro", "annual")
        assert plans.parse_plan_key("daily_monthly") == ("daily", "monthly")
        assert plans.parse_plan_key("annual") == ("starter", "annual")
        assert plans.parse_plan_key("nope_monthly") == ("", "")
        assert server._price_for_plan_key("studio_max_monthly") == "price_max_m"

    def test_unconfigured_tier_has_no_price(self, monkeypatch):
        monkeypatch.setattr(config, "STRIPE_PRICE_MAX_ANNUAL", "")
        assert plans.price_for("studio_max", "annual") == ""
        assert plans.tier_from_price_id("") == ""

    def test_catalog_lists_only_tiers_on_sale(self):
        catalog = plans.catalog()
        assert [t["tier"] for t in catalog] == ["daily", "studio_pro", "studio_max"]
        assert [t["label"] for t in catalog] == ["Creator", "Pro", "Max"]
        assert all(t["available"] and t["annual_available"] for t in catalog)

    def test_channelrecipe_price_ids_include_new_tiers(self):
        ids = server._channelrecipe_price_ids()
        assert {"price_pro_m", "price_pro_a", "price_max_m", "price_max_a"} <= ids


class TestUpgradesBetweenStudioTiers:
    def test_creator_to_max_grants_the_difference(self):
        assert server._upgrade_grant({"period_tier": "daily"}, "daily", "studio_max") == 100 - 35

    def test_re_upgrade_in_the_same_period_grants_nothing(self):
        assert server._upgrade_grant({"period_tier": "studio_max"}, "daily", "studio_max") == 0

    def test_downgrade_grants_nothing(self):
        assert server._upgrade_grant({"period_tier": "studio_pro"}, "studio_pro", "daily") == 0


class _FakeStripe:
    """Just enough of stripe.Subscription for _switch_trial_price."""

    def __init__(self, price_id):
        self.price_id = price_id
        self.modified = []
        stripe = self

        class Subscription:
            @staticmethod
            def retrieve(sub_id):
                return {"items": {"data": [{"id": "si_1", "price": {"id": stripe.price_id}}]}}

            @staticmethod
            def modify(sub_id, **kwargs):
                stripe.modified.append((sub_id, kwargs))

        self.Subscription = Subscription


def test_creator_trial_can_convert_onto_pro(monkeypatch):
    monkeypatch.setattr(server, "update_user", lambda *a, **k: None)
    fake = _FakeStripe("price_daily_m")
    interval = server._switch_trial_price(fake, "sub_1", "studio_pro", 7, "annual")
    assert interval == "annual"
    assert fake.modified[0][1]["items"][0]["price"] == "price_pro_a"


class _Req:
    """A request object the checkout handler never reaches before refusing."""

    base_url = "https://channelrecipe.com/"
    cookies: dict = {}
    headers: dict = {}


@pytest.mark.parametrize("plan_key", ["starter_monthly", "starter_annual", "monthly", "annual", "bogus_monthly"])
def test_checkout_refuses_plans_not_on_sale(monkeypatch, plan_key):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_dummy")
    with pytest.raises(HTTPException) as err:
        server.create_checkout(server.CheckoutRequest(plan=plan_key), _Req())
    assert err.value.status_code == 400


class _CheckoutStripe:
    """Fake stripe module capturing the Checkout session request."""

    def __init__(self):
        self.sessions = []
        outer = self

        class Customer:
            @staticmethod
            def create(**kw):
                return type("C", (), {"id": "cus_test"})()

        class Session:
            @staticmethod
            def create(**kw):
                outer.sessions.append(kw)
                return type("S", (), {"url": "https://checkout.stripe.test/s"})()

        self.Customer = Customer
        self.checkout = type("checkout", (), {"Session": Session})
        self.api_key = None


def _checkout(monkeypatch, plan_key, *, trial_days, user=None):
    fake = _CheckoutStripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setattr(config, "FREE_TRIAL_DAYS", trial_days)
    member = user or {"id": 9, "email": "new@example.com", "plan": "free", "trial_used": 0, "stripe_customer_id": "cus_x"}
    monkeypatch.setattr(server, "_current_user", lambda request: member)
    result = server.create_checkout(server.CheckoutRequest(plan=plan_key), _Req())
    assert result["url"].startswith("https://checkout.stripe.test")
    return fake.sessions[0]


def test_checkout_charges_today_when_free_trials_are_off(monkeypatch):
    session = _checkout(monkeypatch, "studio_pro_monthly", trial_days=0)
    assert "subscription_data" not in session
    assert session["metadata"] == {"user_id": "9", "plan": "studio_pro_monthly", "skip_trial": "1"}
    assert session["line_items"] == [{"price": "price_pro_m", "quantity": 1}]
    assert session["success_url"].endswith("/app?welcome=1#local-studio")


def test_a_configured_trial_still_works(monkeypatch):
    session = _checkout(monkeypatch, "daily_annual", trial_days=7)
    assert session["subscription_data"] == {"trial_period_days": 7}
    assert "skip_trial" not in session["metadata"]
