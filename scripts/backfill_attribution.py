"""Backfill the sales-attribution `sales` table from Stripe's paid invoices (read-only against Stripe).

Dry run by default: it reads Stripe and the database and prints what it WOULD write, and writes nothing. Pass
--apply to write. Stripe is only ever listed (Invoice.list); nothing is created or changed there.

  python scripts/backfill_attribution.py                       # dry run, paid invoices since 2025-01-01
  python scripts/backfill_attribution.py --since 2026-06-01 --apply
  python scripts/backfill_attribution.py --posthog-csv persons.csv --apply

Only invoices that bill a ChannelRecipe price (webapp/plans.py) for a customer in our users table are counted. The
first paid invoice of a subscription is a first_payment (trial conversions included); later ones are renewals.
Invoices already in `sales` (from the live webhook or an earlier run) are skipped, so it is safe to run again.

--posthog-csv imports a PostHog persons export (an email column plus initial_utm_* / utm_* columns, with or without
a "properties." or "$" prefix) to fill first/last touches for users that have none. Anyone without a touch is
reported as 'unknown'. No secret or email is printed, only counts.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from webapp import attribution as attr  # noqa: E402
from webapp import database as db  # noqa: E402
from webapp import plans  # noqa: E402


def _cr_price_ids() -> set[str]:
    ids = set(plans.subscription_price_ids())
    for p in (getattr(config, "STRIPE_PRICE_TOPUP_5", ""), getattr(config, "STRIPE_PRICE_TOPUP_15", "")):
        if (p or "").strip():
            ids.add(p.strip())
    return {p for p in ids if p}


def _line_price(line: dict) -> str:
    price = line.get("price") or line.get("plan") or {}
    if isinstance(price, dict) and price.get("id"):
        return str(price["id"])
    if isinstance(price, str):
        return price
    details = ((line.get("pricing") or {}).get("price_details") or {})
    return str(details.get("price") or "")


def _invoice_sub(inv: dict) -> str:
    sub = inv.get("subscription")
    if isinstance(sub, dict):
        sub = sub.get("id")
    if sub:
        return str(sub)
    details = ((inv.get("parent") or {}).get("subscription_details") or {})
    sub = details.get("subscription")
    return str(sub.get("id") if isinstance(sub, dict) else (sub or ""))


def _to_dict(obj) -> dict:
    if hasattr(obj, "to_dict_recursive"):
        return obj.to_dict_recursive()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return json.loads(json.dumps(obj, default=str))


def _users_by_customer(conn) -> dict[str, dict]:
    cols = "id, plan, stripe_customer_id"
    try:
        rows = conn.execute(f"SELECT {cols}, first_touch_json, last_touch_json FROM users "
                            "WHERE COALESCE(stripe_customer_id, '') <> ''").fetchall()
    except Exception:                    # touch columns not created yet (dry run before the feature has run)
        rows = conn.execute(f"SELECT {cols} FROM users WHERE COALESCE(stripe_customer_id, '') <> ''").fetchall()
    return {dict(r)["stripe_customer_id"]: dict(r) for r in rows}


def _recorded(conn) -> tuple[set[str], set[str]]:
    """(invoice ids already in sales, subscription ids that already have a payment there)."""
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT invoice_id, sub_id FROM sales WHERE kind IN ('first_payment','renewal')").fetchall()]
    except Exception:                    # no sales table yet
        return set(), set()
    return {r["invoice_id"] for r in rows if r["invoice_id"]}, {r["sub_id"] for r in rows if r["sub_id"]}


def _norm_key(k: str) -> str:
    k = k.strip()
    for p in ("properties.", "person.properties.", "$"):
        if k.startswith(p):
            k = k[len(p):]
    return k.lstrip("$")


def import_posthog(path: Path, apply: bool) -> dict:
    stats = {"rows": 0, "matched": 0, "first_set": 0, "last_set": 0}
    with db._conn() as conn:
        try:
            emails = {dict(r)["email"].lower(): dict(r) for r in conn.execute(
                "SELECT id, email, first_touch_json, last_touch_json FROM users").fetchall()}
        except Exception:                # touch columns not created yet (only possible on a dry run)
            emails = {dict(r)["email"].lower(): dict(r) for r in conn.execute("SELECT id, email FROM users").fetchall()}
    with open(path, newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            stats["rows"] += 1
            row = {_norm_key(k): (v or "").strip() for k, v in raw.items() if k}
            user = emails.get((row.get("email") or "").lower())
            if not user:
                continue
            stats["matched"] += 1
            first = attr.clean_touch({k: row.get("initial_" + k) for k in attr.TOUCH_KEYS})
            last = attr.clean_touch({k: row.get(k) for k in attr.TOUCH_KEYS})
            fields = {}
            if not user.get("first_touch_json") and attr.is_hit(first):
                fields["first_touch_json"] = json.dumps(first, separators=(",", ":"))
                fields["first_touch_at"] = time.time()
                stats["first_set"] += 1
            if not user.get("last_touch_json") and attr.is_hit(last):
                fields["last_touch_json"] = json.dumps(last, separators=(",", ":"))
                stats["last_set"] += 1
            if apply and fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                with db._conn() as conn:
                    conn.execute(db._q(f"UPDATE users SET {sets} WHERE id = ?"), (*fields.values(), user["id"]))
    return stats


def backfill(since: datetime, apply: bool, limit: int | None = None) -> dict:
    import stripe
    if not config.STRIPE_SECRET_KEY:
        raise SystemExit("STRIPE_SECRET_KEY is not set")
    stripe.api_key = config.STRIPE_SECRET_KEY
    cr_ids = _cr_price_ids()
    if not cr_ids:
        raise SystemExit("No ChannelRecipe price ids configured; refusing to guess which invoices are ours")
    with db._conn() as conn:
        users = _users_by_customer(conn)
        done, paid_subs = _recorded(conn)

    invoices = []
    for inv in stripe.Invoice.list(status="paid", created={"gte": int(since.timestamp())}, limit=100,
                                   expand=["data.lines"]).auto_paging_iter():
        inv = _to_dict(inv)
        if int(inv.get("amount_paid") or 0) <= 0:
            continue
        prices = {_line_price(l) for l in ((inv.get("lines") or {}).get("data") or []) if isinstance(l, dict)}
        if not (prices & cr_ids):
            continue
        cust = inv.get("customer")
        cust = cust.get("id") if isinstance(cust, dict) else (cust or "")
        if cust not in users:
            continue
        invoices.append(inv)
        if limit and len(invoices) >= limit:
            break
    invoices.sort(key=lambda i: int(i.get("created") or 0))

    seen_subs: set[str] = set(paid_subs)     # pick --since before the first sale, or older renewals look like firsts
    stats = {"invoices": len(invoices), "already_recorded": 0, "would_write": 0, "written": 0}
    by_key: dict[str, dict] = defaultdict(lambda: {"first_payment": 0, "renewal": 0, "cents": 0})
    for inv in invoices:
        sub = _invoice_sub(inv)
        cust = inv.get("customer")
        cust = cust.get("id") if isinstance(cust, dict) else (cust or "")
        user = users[cust]
        kind = "first_payment" if (sub and sub not in seen_subs) or (not sub and inv.get("billing_reason") ==
                                                                        "subscription_create") else "renewal"
        if sub:
            seen_subs.add(sub)
        if inv.get("id") in done:
            stats["already_recorded"] += 1
            continue
        first = attr._load_json(user.get("first_touch_json"))
        last = attr._load_json(user.get("last_touch_json"))
        key = attr.touch_key(last)
        by_key[key][kind] += 1
        by_key[key]["cents"] += int(inv.get("amount_paid") or 0)
        stats["would_write"] += 1
        if apply:
            charge_id, pi = attr.invoice_payment_ids(inv)
            # a renewal keeps the credit of its subscription's first payment, as the live webhook does
            origin = attr._first_payment_row(sub, user["id"]) if kind == "renewal" else None
            ok = attr.insert_sale(event_id=f"backfill:{inv['id']}", user_id=user["id"], kind=kind,
                                  amount_cents=int(inv.get("amount_paid") or 0), currency=inv.get("currency") or "usd",
                                  invoice_id=str(inv["id"]), sub_id=sub, charge_id=charge_id, payment_intent=pi,
                                  plan_key=str(user.get("plan") or ""), ts=float(inv.get("created") or time.time()),
                                  first=first, last=last, credit=attr._row_credit(origin))
            stats["written"] += int(bool(ok))
    stats["by_last_touch"] = dict(sorted(by_key.items(), key=lambda kv: -kv[1]["cents"]))
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2025-01-01", help="only invoices created on/after this UTC date")
    ap.add_argument("--apply", action="store_true", help="write to the database (default is a dry run)")
    ap.add_argument("--dry-run", action="store_true", help="the default; kept for clarity")
    ap.add_argument("--posthog-csv", type=Path, help="optional PostHog persons export to fill missing touches")
    ap.add_argument("--limit", type=int, default=None, help="stop after this many matching invoices")
    args = ap.parse_args(argv)
    apply = bool(args.apply) and not args.dry_run
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    print(f"[backfill] mode={'APPLY' if apply else 'dry-run'} since={since.date()} db={db.backend_name()}")
    if apply and not attr.initialize():
        print("[backfill] attribution is off or its tables could not be created; nothing written "
              "(set CR_ATTRIBUTION_ENABLED=1 to apply)")
        return 1
    if args.posthog_csv:
        print("[backfill] posthog import:", json.dumps(import_posthog(args.posthog_csv, apply)))
    print("[backfill] stripe:", json.dumps(backfill(since, apply, args.limit), indent=2))
    if not apply:
        print("[backfill] dry run: nothing was written. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
