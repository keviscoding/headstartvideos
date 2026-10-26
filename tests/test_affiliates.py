"""The affiliate program (Rewardful): dormant until the owner connects it, and a referral can never fail a checkout."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import webapp.server as server  # noqa: E402
from tests.test_plans import PRICES, _Req  # noqa: E402

REF = "8f2a1c3e-7b4d-4e9a-9c1f-2d3e4f5a6b7c"


class _Stripe:
    def __init__(self, modify_fails=False):
        self.sessions, self.labels, outer = [], [], self

        class Customer:
            @staticmethod
            def create(**kw):
                return type("C", (), {"id": "cus_new"})()

            @staticmethod
            def modify(cid, **kw):
                if modify_fails:
                    raise RuntimeError("stripe hiccup")
                outer.labels.append((cid, kw))

        class Session:
            @staticmethod
            def create(**kw):
                outer.sessions.append(kw)
                return type("S", (), {"url": "https://checkout.stripe.test/s"})()

        self.Customer, self.checkout, self.api_key = Customer, type("checkout", (), {"Session": Session}), None


@pytest.fixture
def checkout(monkeypatch):
    for attr, value in PRICES.items():
        monkeypatch.setattr(config, attr, value)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setattr(config, "FREE_TRIAL_DAYS", 0)
    monkeypatch.setattr(server, "_current_user", lambda request: {"id": 9, "email": "new@example.com", "plan": "free",
                                                                   "trial_used": 0, "stripe_customer_id": "cus_x"})

    def run(referral="", on=True, modify_fails=False):
        fake = _Stripe(modify_fails)
        monkeypatch.setitem(sys.modules, "stripe", fake)
        monkeypatch.setattr(config, "REWARDFUL_PUBLIC_KEY", "rw_public_key" if on else "")
        result = server.create_checkout(server.CheckoutRequest(plan="studio_pro_monthly", referral=referral), _Req())
        assert result["url"].startswith("https://checkout.stripe.test")
        return fake.sessions[0], fake.labels
    return run


def test_a_referred_checkout_is_credited_to_the_affiliate(checkout):
    session, labels = checkout(REF)
    assert session["client_reference_id"] == REF
    assert labels == [("cus_x", {"metadata": {"referral": REF}})]


def test_with_the_program_off_or_no_referral_checkout_is_unchanged(checkout):
    for referral, on in ((REF, False), ("", True)):
        session, labels = checkout(referral, on=on)
        assert "client_reference_id" not in session and labels == []


def test_a_bad_referral_or_a_stripe_hiccup_never_stops_a_checkout(checkout):
    session, labels = checkout("<script>x</script>")
    assert "client_reference_id" not in session and labels == []
    session, _ = checkout(REF, modify_fails=True)
    assert session["client_reference_id"] == REF


def test_the_front_end_gets_the_tracker_key_only_once_connected(monkeypatch):
    monkeypatch.setattr(config, "REWARDFUL_PUBLIC_KEY", "")
    assert asyncio.run(server.get_client_config())["affiliate"] is None
    monkeypatch.setattr(config, "REWARDFUL_PUBLIC_KEY", "rw_public_key")
    assert asyncio.run(server.get_client_config())["affiliate"] == {"provider": "rewardful", "key": "rw_public_key"}


def test_the_affiliate_page_is_live_only_once_set_up(monkeypatch):
    monkeypatch.setattr(config, "REWARDFUL_PUBLIC_KEY", "")
    monkeypatch.setattr(config, "AFFILIATE_PORTAL_URL", "https://channelrecipe.getrewardful.com/signup")
    with pytest.raises(HTTPException) as gone:
        asyncio.run(server.affiliates_page())
    assert gone.value.status_code == 404
    monkeypatch.setattr(config, "REWARDFUL_PUBLIC_KEY", "rw_public_key")
    monkeypatch.setattr(config, "AFFILIATE_COMMISSION_PCT", 30)
    monkeypatch.setattr(config, "AFFILIATE_COMMISSION_MONTHS", 12)
    page = asyncio.run(server.affiliates_page()).body.decode()
    assert 'href="https://channelrecipe.getrewardful.com/signup"' in page and "30% of every payment" in page
    assert "12 months" in page and "__" not in page


def test_every_page_a_visitor_lands_or_pays_on_loads_the_tracker():
    for page in ("landing.html", "index.html", "money-niche.html", "affiliates.html"):
        assert "/static/cr-affiliate.js" in (ROOT / "webapp" / "static" / page).read_text(encoding="utf-8"), page
