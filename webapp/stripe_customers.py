"""One Stripe customer per account, however many checkouts start at once, and a checkout never fails over it.

Customers are created with the idempotency key "cr-customer-<account id>", so two checkouts at once (a double click
reaching both web containers) get the same customer. While the first of those requests is still in flight, Stripe
answers the second with an IdempotencyError (409 idempotency_key_in_use). That second request then waits briefly and
asks again: either the first one has stored the customer on the account by then, or Stripe now replays the first
request's answer for the same key. Only if neither works within a few seconds is a customer made without the key: a
second customer on the account is harmless (the webhook stores the one each checkout paid as), a failed checkout
isn't.
"""
from __future__ import annotations

import time

from webapp import database as db

ATTEMPTS = 5
WAIT_SECONDS = 0.6


def customer_key(user_id) -> str:
    """The idempotency key for creating an account's Stripe customer (Stripe keeps a key for 24 hours)."""
    return f"cr-customer-{int(user_id)}"


def _idempotency_error(error: Exception, stripe) -> bool:
    for owner in (stripe, getattr(stripe, "error", None)):
        cls = getattr(owner, "IdempotencyError", None)
        if isinstance(cls, type) and isinstance(error, cls):
            return True
    return type(error).__name__ == "IdempotencyError"


def customer_for(stripe, user: dict, *, sleep=time.sleep) -> str:
    """The account's Stripe customer id: the stored one, else created once (stored on the account)."""
    customer_id = user.get("stripe_customer_id")
    if customer_id:
        return customer_id
    key = customer_key(user["id"])
    customer = None
    for attempt in range(ATTEMPTS):
        try:
            customer = stripe.Customer.create(email=user["email"], idempotency_key=key)
            break
        except Exception as error:                              # noqa: BLE001 - only Stripe's idempotency answer is ours
            if not _idempotency_error(error, stripe):
                raise
            stored = (db.get_user_by_id(int(user["id"])) or {}).get("stripe_customer_id")
            if stored:
                return stored                                   # the other request stored it meanwhile
            if attempt < ATTEMPTS - 1:
                sleep(WAIT_SECONDS)
    if customer is None:
        print(f"[stripe] customer for user {user['id']}: the idempotent create stayed busy; creating one without it")
        customer = stripe.Customer.create(email=user["email"])
    db.update_user(int(user["id"]), stripe_customer_id=customer.id)
    return customer.id
