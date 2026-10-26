"""Money races that two web containers make possible (2026-10-26).

- A cancel refunded from the in-memory job: two cancels (a double click reaching both containers) or a cancel plus
  the cook's own failure refund (the cook stops because it was cancelled, then refunds from its own copy of the job)
  paid twice. Every cook refund now goes through one guarded flip of credit_deducted in the database.
- Two checkouts at once could create two Stripe customers for one account.
"""
import json
import sys
import time
import types
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import webapp.database as database
from webapp import cloud_wallet, server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    if database.IS_PG:
        pytest.skip("these run on the SQLite path")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "money.db")
    database._init_db()
    return database


def _user(db, credits=5):
    user = db.create_user(f"money-{uuid.uuid4().hex[:8]}@example.test")
    db.update_user(user["id"], credits=credits)
    return user["id"]


def _cook(db, user_id, job_id, *, status="running", charged=1, deducted=True, recipe="animated_explainer"):
    db.create_cook_job(job_id=job_id, user_id=user_id, recipe=recipe, title="t",
                       request_json=json.dumps({"credits_charged": charged}), credit_deducted=deducted, lite_mode=False)
    with db._conn() as conn:
        conn.cursor().execute(db._q("UPDATE cook_jobs SET status = ? WHERE job_id = ?"), (status, job_id))


def _credits(db, user_id):
    return db.get_user_by_id(user_id)["credits"]


def test_a_cancel_refunds_once_however_often_it_is_sent(db):
    uid = _user(db, credits=3)
    _cook(db, uid, "c1", charged=2)
    assert db.cancel_cook_job_with_refund("c1") == (True, 2)
    assert db.cancel_cook_job_with_refund("c1") == (False, 0)
    row = db.get_cook_job("c1")
    assert _credits(db, uid) == 5 and row["status"] == "cancelled" and not row["credit_deducted"]


def test_a_cancelled_cook_that_then_fails_is_not_refunded_again(db):
    """The cook on Fly sees the cancel, raises, and its failure path asks for the refund too."""
    uid = _user(db, credits=0)
    _cook(db, uid, "c2", charged=1)
    assert db.cancel_cook_job_with_refund("c2") == (True, 1)
    assert db.refund_cook_credits_once("c2") == 0
    assert _credits(db, uid) == 1


def test_a_cook_that_failed_and_refunded_itself_cant_be_cancelled_into_a_second_refund(db):
    uid = _user(db, credits=0)
    _cook(db, uid, "c3", charged=1)
    assert db.refund_cook_credits_once("c3") == 1
    assert db.fail_cook_job_with_refund("c3", "running", "boom") == (True, 0), "the sweeper fails it, pays nothing"
    assert db.cancel_cook_job_with_refund("c3") == (False, 0)
    assert _credits(db, uid) == 1


@pytest.mark.parametrize("status", ["complete", "error", "cancelled"])
def test_a_finished_cook_is_never_cancelled_or_refunded(db, status):
    uid = _user(db, credits=0)
    _cook(db, uid, "c4", status=status)
    assert db.cancel_cook_job_with_refund("c4") == (False, 0)
    assert _credits(db, uid) == 0 and db.get_cook_job("c4")["status"] == status


def test_a_ranking_shorts_half_credit_comes_back_as_half(db):
    uid = _user(db, credits=0)
    _cook(db, uid, "r1", charged=0.5, recipe="ranking_countdown")
    assert db.cancel_cook_job_with_refund("r1") == (True, 0.5)
    assert _credits(db, uid) == 0.5


def test_an_uncharged_cook_is_cancelled_without_a_refund(db):
    uid = _user(db, credits=0)
    _cook(db, uid, "c5", charged=0, deducted=False)
    assert db.cancel_cook_job_with_refund("c5") == (True, 0)
    assert _credits(db, uid) == 0


def test_two_cancels_on_two_containers_refund_once(db, monkeypatch):
    """Both containers read the cook as running before either cancels it (each has its own in-memory copy); the
    database's guarded flip decides, so the credits come back once."""
    uid = _user(db, credits=0)
    _cook(db, uid, "c6", charged=2)
    monkeypatch.setattr(server, "COOK_ON_WEB", False)
    monkeypatch.setattr(server, "_current_user", lambda request: {"id": uid})
    monkeypatch.setattr(server, "_get_user_job", lambda job_id, request: {
        "status": "running", "user_id": uid, "credit_deducted": True, "progress": [],
        "request": {"credits_charged": 2}})
    monkeypatch.setattr(server, "_refresh_job_from_db", lambda job_id, job: job.update(status="cancelled"))
    client = TestClient(server.app)
    first, second = client.delete("/api/build/c6"), client.delete("/api/build/c6")
    assert first.json()["status"] == "cancelled" and second.json() == {"status": "cancelled"}
    assert _credits(db, uid) == 2, "two credits back, not four"


def test_every_cook_refund_goes_through_the_guarded_flip():
    for name in ("webapp/cook_runner.py", "webapp/fly_oneshot.py"):
        src = (ROOT / name).read_text(encoding="utf-8")
        assert "refund_credits(" not in src, f"{name} refunds without the credit_deducted guard"
        assert "refund_cook_credits_once(job_id)" in src
    server_src = (ROOT / "webapp/server.py").read_text(encoding="utf-8")
    cancel = server_src[server_src.index("def cancel_build("):server_src.index("def build_result(")]
    assert "cancel_cook_job_with_refund(" in cancel and "refund_credits(" not in cancel


class _Stripe:
    """Stripe's Customer.create and Checkout, recorded."""

    def __init__(self):
        self.customers, outer = [], self

        class Customer:
            @staticmethod
            def create(**kw):
                outer.customers.append(kw)
                return types.SimpleNamespace(id="cus_" + kw.get("idempotency_key", "none"))

            @staticmethod
            def modify(*a, **kw):
                pass

        class Session:
            @staticmethod
            def create(**kw):
                return types.SimpleNamespace(url="https://checkout.stripe.test/s")

        self.Customer, self.api_key = Customer, ""
        self.checkout = types.SimpleNamespace(Session=Session)


class _Req:
    base_url = "https://channelrecipe.com/"


def test_new_stripe_customers_are_created_once_per_account(db, monkeypatch):
    fake = _Stripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setattr(server.config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(server.config, "STRIPE_PRICE_TOPUP_5", "price_topup")
    monkeypatch.setattr(server.config, "FREE_TRIAL_DAYS", 0)
    monkeypatch.setattr(server.plans, "price_for", lambda tier, interval: "price_x")
    uid = _user(db, credits=0)
    monkeypatch.setattr(server, "_current_user", lambda request: {"id": uid, "email": "a@x.test", "plan": "free",
                                                                   "trial_used": 0, "stripe_customer_id": ""})
    server.create_checkout(server.CheckoutRequest(plan="studio_pro_monthly"), _Req())
    monkeypatch.setattr(server, "_current_user", lambda request: {"id": uid, "email": "a@x.test", "plan": "starter",
                                                                   "stripe_customer_id": ""})
    server.create_topup(server.TopupRequest(credits=5), _Req())
    cloud_wallet.checkout_session({"id": uid, "email": "a@x.test", "stripe_customer_id": ""}, 5,
                                  "https://channelrecipe.com", stripe=fake)
    keys = {c["idempotency_key"] for c in fake.customers}
    assert len(fake.customers) == 3 and keys == {f"cr-customer-{uid}"}, "every path asks Stripe for the same one"
    assert db.get_user_by_id(uid)["stripe_customer_id"] == f"cus_cr-customer-{uid}"


def test_a_completed_checkout_stores_the_customer_it_paid_as(db, monkeypatch):
    for quiet in ("_money_track", "identify_user", "track"):
        monkeypatch.setattr(server, quiet, lambda *a, **k: None)
    uid = _user(db, credits=0)
    db.update_user(uid, stripe_customer_id="cus_old")
    server._apply_stripe_event_once({"id": "evt_" + uuid.uuid4().hex, "type": "checkout.session.completed",
                                     "data": {"object": {"id": "cs_1", "customer": "cus_paid", "amount_total": 500,
                                                         "metadata": {"user_id": str(uid), "topup_credits": "5"}}}})
    row = db.get_user_by_id(uid)
    assert row["stripe_customer_id"] == "cus_paid" and row["credits"] == 5


class IdempotencyError(Exception):
    """Stripe's answer to a second request with a key whose first request is still in flight (409)."""


class _BusyStripe:
    """Customer.create answers IdempotencyError `busy` times, then the customer (as Stripe replays the first answer)."""

    def __init__(self, busy, on_busy=None):
        self.busy, self.on_busy, self.calls = busy, on_busy, []
        outer = self

        class Customer:
            @staticmethod
            def create(**kw):
                outer.calls.append(kw)
                if outer.busy and "idempotency_key" in kw:    # only a request with the same key is refused
                    outer.busy -= 1
                    if outer.on_busy:
                        outer.on_busy()
                    raise IdempotencyError("There is currently another in-progress request using this Idempotent Key")
                return types.SimpleNamespace(id="cus_" + ("keyed" if "idempotency_key" in kw else "unkeyed"))
        self.Customer, self.IdempotencyError = Customer, IdempotencyError


def _member(db):
    uid = _user(db, credits=0)
    return {"id": uid, "email": "a@x.test", "stripe_customer_id": ""}


def test_a_double_click_whose_first_request_is_in_flight_waits_and_gets_the_same_customer(db):
    from webapp import stripe_customers
    member, stripe = _member(db), _BusyStripe(busy=2)
    assert stripe_customers.customer_for(stripe, member, sleep=lambda s: None) == "cus_keyed"
    assert len(stripe.calls) == 3 and all(c["idempotency_key"] == f"cr-customer-{member['id']}" for c in stripe.calls)
    assert db.get_user_by_id(member["id"])["stripe_customer_id"] == "cus_keyed"


def test_a_double_click_uses_the_customer_the_first_request_stored(db):
    from webapp import stripe_customers
    member = _member(db)
    stripe = _BusyStripe(busy=5, on_busy=lambda: db.update_user(member["id"], stripe_customer_id="cus_first"))
    assert stripe_customers.customer_for(stripe, member, sleep=lambda s: None) == "cus_first"
    assert len(stripe.calls) == 1, "no second customer"


def test_a_checkout_never_fails_on_a_busy_key(db, monkeypatch):
    """Stripe stays busy for the whole wait: one customer without the key rather than a failed checkout."""
    from webapp import stripe_customers
    member, stripe = _member(db), _BusyStripe(busy=99)
    naps = []
    assert stripe_customers.customer_for(stripe, member, sleep=naps.append) == "cus_unkeyed"
    assert len(naps) == stripe_customers.ATTEMPTS - 1 and "idempotency_key" not in stripe.calls[-1]


def test_any_other_stripe_error_still_reaches_the_customer(db):
    from webapp import stripe_customers

    class CardError(Exception):
        pass

    class Stripe:
        class Customer:
            @staticmethod
            def create(**kw):
                raise CardError("declined")
    with pytest.raises(CardError):
        stripe_customers.customer_for(Stripe, _member(db), sleep=lambda s: None)


def test_checkout_and_topup_ride_out_a_busy_key(db, monkeypatch):
    stripe = _BusyStripe(busy=1)
    stripe.checkout = types.SimpleNamespace(Session=types.SimpleNamespace(
        create=lambda **kw: types.SimpleNamespace(url="https://checkout.stripe.test/s")))
    stripe.api_key = ""
    monkeypatch.setitem(sys.modules, "stripe", stripe)
    monkeypatch.setattr(server.config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(server.config, "FREE_TRIAL_DAYS", 0)
    monkeypatch.setattr(server.plans, "price_for", lambda tier, interval: "price_x")
    monkeypatch.setattr("webapp.stripe_customers.time.sleep", lambda s: None)
    member = _member(db)
    monkeypatch.setattr(server, "_current_user", lambda request: {**member, "plan": "free", "trial_used": 0})
    assert server.create_checkout(server.CheckoutRequest(plan="studio_pro_monthly"), _Req())["url"]
    assert db.get_user_by_id(member["id"])["stripe_customer_id"] == "cus_keyed"
