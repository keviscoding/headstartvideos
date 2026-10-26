"""Each Stripe event applies once, and one at a time across every web container (two containers, 2026-10-26).

There was no record of processed events: one event loop happened to serialize deliveries, and Stripe's retries of a
delivery it wasn't sure arrived (or a duplicate) were applied again. With two containers two deliveries can run at
once. Now a transaction-level advisory lock serializes them and stripe_events records each applied event id."""
import sys
import threading
import time
import types
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from webapp import database as db
from webapp import server


@pytest.fixture
def stripe_sends(monkeypatch):
    """Stripe as the webhook sees it: the event a delivery carries (signature checks stood in)."""
    deliveries = {}

    def construct_event(payload, sig, secret):
        return deliveries[sig]
    monkeypatch.setitem(sys.modules, "stripe", types.SimpleNamespace(
        api_key="", Webhook=types.SimpleNamespace(construct_event=construct_event)))
    monkeypatch.setattr(server.config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(server.config, "STRIPE_WEBHOOK_SECRET", "whsec_x")
    for quiet in ("_money_track", "identify_user", "track"):
        monkeypatch.setattr(server, quiet, lambda *a, **k: None)
    client = TestClient(server.app)

    def send(event):
        sig = "t=1,v1=" + uuid.uuid4().hex
        deliveries[sig] = event
        return client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": sig})
    return send


def _customer(**fields):
    user = db.create_user(f"webhook-{uuid.uuid4().hex[:10]}@example.test")
    if fields:
        db.update_user(user["id"], **fields)
    return user["id"]


def _topup(user_id, credits=5):
    return {"id": "evt_" + uuid.uuid4().hex, "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_" + uuid.uuid4().hex[:8], "amount_total": 500, "currency": "usd",
                                "metadata": {"user_id": str(user_id), "topup_credits": str(credits)}}}}


def _cancelled(sub_id, customer_id="cus_x"):
    return {"id": "evt_" + uuid.uuid4().hex, "type": "customer.subscription.deleted",
            "data": {"object": {"id": sub_id, "customer": customer_id}}}


def test_the_same_event_delivered_twice_applies_once(stripe_sends):
    user_id = _customer()
    event = _topup(user_id)
    first, again = stripe_sends(event), stripe_sends(event)
    assert first.status_code == 200 and first.json() == {"ok": True}
    assert again.status_code == 200 and again.json() == {"ok": True, "duplicate": True}
    assert db.get_user_by_id(user_id)["credits"] == 5, "five credits once, not ten"


def test_an_event_that_fails_is_applied_by_stripes_retry(stripe_sends, monkeypatch):
    user_id = _customer()
    event = _topup(user_id)
    real = server.add_credits
    calls = []

    def flaky(uid, amount):
        calls.append(amount)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        real(uid, amount)
    monkeypatch.setattr(server, "add_credits", flaky)
    with pytest.raises(RuntimeError):
        stripe_sends(event)
    assert db.get_user_by_id(user_id)["credits"] == 0
    assert stripe_sends(event).json() == {"ok": True}, "not recorded as done, so the retry applies it"
    assert db.get_user_by_id(user_id)["credits"] == 5 and stripe_sends(event).json()["duplicate"]


def test_two_event_types_for_one_customer_apply_in_order_never_interleaved(monkeypatch):
    """A top-up, then the subscription ending: the second waits for the first, so the customer ends with 0 credits on
    free (in the other order, or interleaved, they'd keep the top-up's credits)."""
    sub_id = "sub_" + uuid.uuid4().hex[:10]
    user_id = _customer(plan="starter", stripe_sub_id=sub_id)
    for quiet in ("_money_track", "identify_user", "track"):
        monkeypatch.setattr(server, quiet, lambda *a, **k: None)
    order, gate, started = [], threading.Event(), threading.Event()
    real_add, real_update = server.add_credits, server.update_user

    def slow_topup(uid, amount):
        order.append("topup starts")
        started.set()
        gate.wait(5)
        real_add(uid, amount)
        order.append("topup done")

    def update(uid, **fields):
        if fields.get("plan") == "free":
            order.append("cancel applies")
        real_update(uid, **fields)
    monkeypatch.setattr(server, "add_credits", slow_topup)
    monkeypatch.setattr(server, "update_user", update)

    first = threading.Thread(target=server._apply_stripe_event_once, args=(_topup(user_id),))
    second = threading.Thread(target=server._apply_stripe_event_once, args=(_cancelled(sub_id),))
    first.start()
    assert started.wait(5)
    second.start()
    time.sleep(0.3)
    assert order == ["topup starts"], "the cancellation waits while the top-up applies"
    gate.set()
    first.join(5), second.join(5)
    assert order == ["topup starts", "topup done", "cancel applies"]
    row = db.get_user_by_id(user_id)
    assert row["plan"] == "free" and row["credits"] == 0


def test_events_the_site_doesnt_use_never_touch_the_database(stripe_sends, monkeypatch):
    monkeypatch.setattr(server, "stripe_event_once", lambda *a, **k: pytest.fail("no lock, no record"))
    got = stripe_sends({"id": "evt_" + uuid.uuid4().hex, "type": "customer.created", "data": {"object": {}}})
    assert got.status_code == 200 and got.json() == {"ok": True}


class _PgConn:
    def __init__(self, seen):
        self.sql, self.seen = [], seen

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        return self

    def fetchall(self):
        return [(1,)] if self.seen else []


def _pg(monkeypatch, conn):
    @contextmanager
    def fake_conn():
        yield conn
    monkeypatch.setattr(db, "IS_PG", True)
    monkeypatch.setattr(db, "_conn", fake_conn)


def test_on_postgres_the_lock_comes_first_and_the_record_after_the_event_applied(monkeypatch):
    conn = _PgConn(seen=False)
    _pg(monkeypatch, conn)
    with db.stripe_event_once("evt_1", "invoice.paid") as fresh:
        assert fresh and len(conn.sql) == 2, "nothing recorded while the event applies"
    lock, check, record = conn.sql
    assert lock == ("SELECT pg_advisory_xact_lock(%s)", (db.STRIPE_EVENT_LOCK,))
    assert "SELECT 1 FROM stripe_events WHERE event_id = %s" in check[0] and check[1] == ("evt_1",)
    assert "ON CONFLICT (event_id) DO NOTHING" in record[0] and record[1][:2] == ("evt_1", "invoice.paid")


def test_on_postgres_a_failed_or_repeated_event_records_nothing(monkeypatch):
    conn = _PgConn(seen=False)
    _pg(monkeypatch, conn)
    with pytest.raises(RuntimeError):
        with db.stripe_event_once("evt_2", "invoice.paid"):
            raise RuntimeError("boom")
    assert not any("INSERT" in sql for sql, _ in conn.sql)
    repeat = _PgConn(seen=True)
    _pg(monkeypatch, repeat)
    with db.stripe_event_once("evt_3", "invoice.paid") as fresh:
        assert fresh is False
    assert not any("INSERT" in sql for sql, _ in repeat.sql)
