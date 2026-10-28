"""Studio Cloud balance (webapp/cloud_wallet.py): chunks topped up once, jobs reserve before anything starts and
settle what they used (never more than they reserved), and a short balance stops a job before it starts."""
from types import SimpleNamespace

import pytest

from webapp import cloud_wallet as cw
from webapp import database as db


@pytest.fixture
def wallet(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db", raising=False)
    monkeypatch.setattr(db, "IS_PG", False, raising=False)
    cw._ready.clear()
    return cw


def test_a_topup_is_credited_once_however_often_stripe_says_so(wallet):
    paid = {"id": "cs_1", "payment_status": "paid", "metadata": {"user_id": "7", "cloud_topup_cents": "1000"}}
    assert wallet.on_checkout_completed(paid) and wallet.on_checkout_completed(paid)
    assert wallet.balance_cents(7) == 1000
    assert not wallet.on_checkout_completed({"id": "cs_2", "metadata": {"user_id": "7", "topup_credits": "5"}}), \
        "a video-credit top-up is not a cloud one"
    unpaid = {"id": "cs_3", "payment_status": "unpaid", "metadata": {"user_id": "7", "cloud_topup_cents": "500"}}
    wallet.on_checkout_completed(unpaid)
    assert wallet.balance_cents(7) == 1000


def test_a_job_reserves_its_quote_and_settles_what_it_used(wallet):
    wallet.credit_topup(7, 1000, ref="cs_1")
    assert wallet.reserve(7, "job_a", 300) and wallet.reserve(7, "job_a", 300), "once per job"
    assert wallet.balance_cents(7) == 700
    out = wallet.settle(7, "job_a", 120)
    assert out == {"reserved": 300, "charged": 120, "returned": 180} and wallet.balance_cents(7) == 880
    assert wallet.settle(7, "job_a", 120)["charged"] is None and wallet.balance_cents(7) == 880, "settled once"
    assert wallet.reserve(7, "job_b", 200) and wallet.settle(7, "job_b", 999)["charged"] == 200, \
        "never more than the quote"
    assert wallet.balance_cents(7) == 680


def test_a_short_balance_stops_a_job_before_it_starts(wallet):
    wallet.credit_topup(7, 500, ref="cs_1")
    assert not wallet.reserve(7, "job_big", 501) and wallet.balance_cents(7) == 500
    assert wallet.reserve(7, "job_ok", 500) and wallet.balance_cents(7) == 0


def test_a_topup_page_is_one_of_the_chunks_and_carries_the_cents(wallet, monkeypatch):
    made, customers = {}, []
    stripe = SimpleNamespace(Customer=SimpleNamespace(create=lambda **kw: customers.append(kw) or SimpleNamespace(id="cus_1")),
                             checkout=SimpleNamespace(Session=SimpleNamespace(
                                 create=lambda **kw: made.update(kw) or SimpleNamespace(url="https://pay/x"))))
    monkeypatch.setattr(db, "update_user", lambda *a, **k: None)
    user = {"id": 7, "email": "a@b.c", "stripe_customer_id": ""}
    assert wallet.checkout_session(user, 10, "https://cr", stripe=stripe) == {"url": "https://pay/x"}
    assert made["metadata"] == {"user_id": "7", "cloud_topup_cents": "1000"} and made["mode"] == "payment"
    assert made["line_items"][0]["price_data"]["unit_amount"] == 1000
    assert customers == [{"email": "a@b.c", "idempotency_key": "cr-customer-7"}], "one Stripe customer per account"
    with pytest.raises(Exception):
        wallet.checkout_session(user, 7, "https://cr", stripe=stripe)


def test_a_delayed_payment_is_credited_when_it_clears_once(wallet):
    session = {"id": "cs_9", "payment_status": "unpaid", "metadata": {"user_id": "7", "cloud_topup_cents": "2500"}}
    wallet.on_checkout_completed(session)
    assert wallet.balance_cents(7) == 0, "a bank debit completes the checkout before it pays"
    cleared = {**session, "payment_status": "paid"}             # checkout.session.async_payment_succeeded
    assert wallet.on_checkout_completed(cleared) and wallet.on_checkout_completed(cleared)
    assert wallet.balance_cents(7) == 2500


def test_no_balance_is_sold_before_studio_cloud_opens(wallet, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import cloud_jobs
    user = {"id": 7, "email": "a@b.c"}
    app = FastAPI()
    app.include_router(wallet.make_router(lambda: user))
    monkeypatch.setattr(wallet, "checkout_session", lambda u, usd, base, **kw: {"url": f"https://pay/{usd}"})
    monkeypatch.setattr(cloud_jobs, "public", lambda u: False)
    client = TestClient(app)
    r = client.post("/api/studio/cloud/topup", json={"usd": 10})
    assert r.status_code == 403 and "testing" in r.json()["detail"]
    monkeypatch.setattr(cloud_jobs, "public", lambda u: True)
    assert client.post("/api/studio/cloud/topup", json={"usd": 10}).json() == {"url": "https://pay/10"}
