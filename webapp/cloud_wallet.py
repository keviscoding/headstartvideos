"""Studio Cloud balance: dollars a creator tops up in chunks (as Claude's console does) and cloud renders spend.

The owner (2026-10-21): "we can handle the top ups the way claude does it, they can top up selected chunks and use
from them... if the card fails it stops". So the card is charged once per chunk (never per render: card fees would
eat a 40-cent render), and every cloud job:

1. reserves its quoted cost before any machine starts (refused, with nothing started, when the balance is short),
2. settles what it actually used when it ends, never more than it reserved (a job never spends past its quote
   without asking), and the rest goes back to the balance.

Balances are integer cents. Every change is a ledger row; a top-up is credited once per Stripe Checkout session (the
webhook may arrive twice), a reservation once per job.
"""
from __future__ import annotations

import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from webapp import database as db

CHUNKS_USD = (5, 10, 25, 50)            # the top-up chunks a creator can buy
_ready = set()
_ready_lock = threading.Lock()


def initialize():
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS cloud_wallets (
                user_id INTEGER PRIMARY KEY, balance_cents INTEGER NOT NULL DEFAULT 0,
                updated_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS cloud_ledger (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, cents INTEGER NOT NULL,
                job_id TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                created_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cloud_ledger_user ON cloud_ledger(user_id,created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cloud_ledger_job ON cloud_ledger(job_id,kind)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_ledger_ref ON cloud_ledger(kind,ref) "
                         "WHERE ref <> ''")
        _ready.add(key)


def _row(conn, user_id: int, kind: str, cents: int, job_id: str = "", ref: str = "", note: str = ""):
    conn.execute(db._q("""INSERT INTO cloud_ledger (id,user_id,kind,cents,job_id,ref,note,created_at)
        VALUES (?,?,?,?,?,?,?,?)"""), ("cl_" + secrets.token_hex(10), user_id, kind, int(cents), job_id, ref,
                                       note[:200], time.time()))


def _ensure(conn, user_id: int):
    if not conn.execute(db._q("SELECT 1 FROM cloud_wallets WHERE user_id=?"), (user_id,)).fetchone():
        conn.execute(db._q("INSERT INTO cloud_wallets (user_id,balance_cents,updated_at) VALUES (?,?,?)"),
                     (user_id, 0, time.time()))


def balance_cents(user_id: int) -> int:
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT balance_cents FROM cloud_wallets WHERE user_id=?"), (user_id,)).fetchone()
        return int(row["balance_cents"]) if row else 0


def credit_topup(user_id: int, cents: int, ref: str) -> bool:
    """A paid top-up into the balance, once per Stripe session (ref). False when already credited."""
    initialize()
    if cents <= 0 or not ref:
        return False
    with db._conn() as conn:
        if conn.execute(db._q("SELECT 1 FROM cloud_ledger WHERE kind='topup' AND ref=?"), (ref,)).fetchone():
            return False
        _ensure(conn, user_id)
        _row(conn, user_id, "topup", cents, ref=ref, note="Studio Cloud top-up")
        conn.execute(db._q("UPDATE cloud_wallets SET balance_cents=balance_cents+?, updated_at=? WHERE user_id=?"),
                     (int(cents), time.time(), user_id))
    return True


def reserve(user_id: int, job_id: str, cents: int, note: str = "") -> bool:
    """Hold a job's quoted cost. False (and nothing held) when the balance is short; once per job."""
    initialize()
    if cents <= 0:
        return True
    with db._conn() as conn:
        if conn.execute(db._q("SELECT 1 FROM cloud_ledger WHERE kind='reserve' AND job_id=?"), (job_id,)).fetchone():
            return True
        _ensure(conn, user_id)
        cur = conn.cursor()
        cur.execute(db._q("UPDATE cloud_wallets SET balance_cents=balance_cents-?, updated_at=? "
                          "WHERE user_id=? AND balance_cents>=?"), (int(cents), time.time(), user_id, int(cents)))
        if cur.rowcount <= 0:
            return False
        _row(conn, user_id, "reserve", -int(cents), job_id=job_id, ref="reserve:" + job_id, note=note)
    return True


def settle(user_id: int, job_id: str, used_cents: int, note: str = "") -> dict:
    """End a job: charge what it used (at most what it reserved) and give the rest back. Once per job."""
    initialize()
    with db._conn() as conn:
        held = conn.execute(db._q("SELECT COALESCE(SUM(-cents),0) AS c FROM cloud_ledger WHERE kind='reserve' "
                                  "AND job_id=?"), (job_id,)).fetchone()
        held = int(held["c"] or 0)
        if conn.execute(db._q("SELECT 1 FROM cloud_ledger WHERE kind='settle' AND job_id=?"), (job_id,)).fetchone():
            return {"reserved": held, "charged": None, "returned": 0}
        charged = max(0, min(int(used_cents), held))
        back = held - charged
        _row(conn, user_id, "settle", 0, job_id=job_id, ref="settle:" + job_id,
             note=(note or "cloud render") + f" (charged {charged}c of {held}c held)")
        if back:
            _row(conn, user_id, "release", back, job_id=job_id, note="unused part of the quote")
            conn.execute(db._q("UPDATE cloud_wallets SET balance_cents=balance_cents+?, updated_at=? WHERE user_id=?"),
                         (back, time.time(), user_id))
    return {"reserved": held, "charged": charged, "returned": back}


def ledger(user_id: int, limit: int = 30) -> list:
    initialize()
    with db._conn() as conn:
        rows = conn.execute(db._q("SELECT kind,cents,job_id,note,created_at FROM cloud_ledger WHERE user_id=? "
                                  "ORDER BY created_at DESC LIMIT ?"), (user_id, int(limit))).fetchall()
        return [dict(r) for r in rows]


def checkout_session(user: dict, usd: int, base_url: str, *, stripe=None):
    """A Stripe Checkout page for one top-up chunk (the webhook credits it)."""
    if usd not in CHUNKS_USD:
        raise HTTPException(400, f"Choose a top-up of ${', $'.join(str(c) for c in CHUNKS_USD)}.")
    if stripe is None:
        import stripe as stripe_module
        from webapp import config
        if not config.STRIPE_SECRET_KEY:
            raise HTTPException(503, "Payments aren't set up yet.")
        stripe_module.api_key = config.STRIPE_SECRET_KEY
        stripe = stripe_module
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer_id = stripe.Customer.create(email=user["email"]).id
        db.update_user(user["id"], stripe_customer_id=customer_id)
    session = stripe.checkout.Session.create(
        customer=customer_id, mode="payment",
        line_items=[{"price_data": {"currency": "usd", "unit_amount": usd * 100,
                                    "product_data": {"name": f"Studio Cloud balance: ${usd}"}}, "quantity": 1}],
        success_url=f"{base_url}/app?cloud_topup=1#billing", cancel_url=f"{base_url}/app#billing",
        metadata={"user_id": str(user["id"]), "cloud_topup_cents": str(usd * 100)})
    return {"url": session.url}


def on_checkout_completed(obj: dict) -> bool:
    """The Stripe webhook's part: a completed Studio Cloud top-up is credited once. True when this was one."""
    meta = obj.get("metadata") or {}
    cents = meta.get("cloud_topup_cents")
    if not cents:
        return False
    if obj.get("payment_status") not in (None, "paid"):
        return True
    credit_topup(int(meta["user_id"]), int(cents), ref=str(obj.get("id") or ""))
    return True


class TopupRequest(BaseModel):
    usd: int


def make_router(access) -> APIRouter:
    router = APIRouter(prefix="/api/studio/cloud", tags=["ChannelRecipe Studio"])

    @router.get("/wallet")
    def get_wallet(user=Depends(access)):
        return {"balance_usd": round(balance_cents(user["id"]) / 100, 2), "chunks_usd": list(CHUNKS_USD),
                "ledger": [{**r, "usd": round(r["cents"] / 100, 2)} for r in ledger(user["id"], 20)]}

    @router.post("/topup")
    def post_topup(req: TopupRequest, request: Request, user=Depends(access)):
        return checkout_session(user, req.usd, str(request.base_url).rstrip("/"))

    return router
