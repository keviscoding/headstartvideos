"""Channel Recipe sales attribution: which video or link brought each signup and each dollar.

Self-contained on purpose. Everything here lives in its own tables (link_codes, link_clicks, sales, video_meta) plus
three nullable columns on users (first_touch_json, last_touch_json, first_touch_at). server.py only calls the small
``safe_*`` wrappers, and every one of them catches its own errors and logs them: when tracking breaks, sign-in,
checkout and the Stripe webhook carry on exactly as before.

Switch it off without a revert: CR_ATTRIBUTION_ENABLED=0 (default on).

Credit model: the headline number goes to the LAST touch, with the FIRST touch shown alongside.
- /v/{video_id}  YouTube description links -> /?utm_source=youtube&utm_medium=organic_video&utm_campaign=cr_sales
                 &utm_content=<id>&utm_term=description
- /r/{code}      short links from link_codes (bios, about pages, email, Discord), with no IP stored for a click.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from webapp import database as db

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
CLICK_KEYS = ("gclid", "fbclid", "msclkid", "ttclid")
TOUCH_KEYS = UTM_KEYS + CLICK_KEYS + ("ref", "landing_path")
SALE_KINDS = ("first_payment", "renewal", "topup", "refund")
_BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|embedly|quora link|whatsapp|"
                     r"telegram|discordbot|skypeuri|vkshare|headless|python-requests|curl|wget", re.I)

# Stripe metadata limits: 50 keys per object, key <= 40 chars, value <= 500 chars.
STRIPE_MAX_KEYS = 50
STRIPE_MAX_KEY_LEN = 40
STRIPE_MAX_VALUE_LEN = 500

# (code, utm_source, utm_medium, utm_campaign, utm_content, label). utm_term is always the code itself, so every
# short link stays distinguishable in reports even when two share a source and medium.
SEED_CODES = (
    ("kevis-about", "youtube", "social", "cr_sales", "channel_description", "Kevis YouTube About"),
    ("jesse-about", "youtube", "social", "cr_sales", "channel_description", "Jesse YouTube About"),
    ("ig-kevis", "instagram", "social", "cr_sales", "bio", "Instagram: Kevis"),
    ("ig-jesse", "instagram", "social", "cr_sales", "bio", "Instagram: Jesse"),
    ("ig-jesse2", "instagram", "social", "cr_sales", "bio", "Instagram: Jesse (2nd)"),
    ("ig-abby", "instagram", "social", "cr_sales", "bio", "Instagram: Abby"),
    ("tt-glintverse", "tiktok", "social", "cr_sales", "bio", "TikTok: Glintverse"),
    ("fb-jesse", "facebook", "social", "cr_sales", "bio", "Facebook: Jesse"),
    ("x-kevis", "x", "social", "cr_sales", "bio", "X: Kevis"),
    ("email", "email", "email", "cr_sales", "newsletter", "Email"),
    ("discord", "discord", "community", "cr_sales", "discord", "Discord"),
)


def enabled() -> bool:
    return os.getenv("CR_ATTRIBUTION_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no", "")


def _log(msg: str) -> None:
    print(f"[attribution] {msg}")


# ---------------------------------------------------------------------------
# Schema (created lazily on first use, never at boot, so a failure here cannot stop the app starting)
# ---------------------------------------------------------------------------
_ready: set = set()
_failed_at: dict = {}
_lock = threading.Lock()
_RETRY_AFTER_S = 300


def _db_key():
    return (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))


def _users_columns(conn) -> set[str]:
    if db.IS_PG:
        rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'").fetchall()
        return {dict(r)["column_name"] for r in rows}
    return {dict(r)["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}


def initialize() -> bool:
    key = _db_key()
    if key in _ready:
        return True
    if time.time() - _failed_at.get(key, 0) < _RETRY_AFTER_S:
        return False
    with _lock:
        if key in _ready:
            return True
        try:
            real = "DOUBLE PRECISION" if db.IS_PG else "REAL"
            with db._conn() as conn:
                if db.IS_PG:
                    # never queue behind a long query on users: give up quickly and retry later instead
                    conn.execute("SET LOCAL lock_timeout = '3s'")
                conn.execute("""CREATE TABLE IF NOT EXISTS link_codes (
                    code TEXT PRIMARY KEY, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT, utm_content TEXT,
                    utm_term TEXT, label TEXT, active INTEGER NOT NULL DEFAULT 1)""")
                conn.execute(f"""CREATE TABLE IF NOT EXISTS link_clicks (
                    kind TEXT NOT NULL, key TEXT NOT NULL, ts {real} NOT NULL,
                    ua_is_bot INTEGER NOT NULL DEFAULT 0, referrer_host TEXT)""")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_link_clicks_ts ON link_clicks (ts)")
                conn.execute(f"""CREATE TABLE IF NOT EXISTS sales (
                    stripe_event_id TEXT PRIMARY KEY, user_id BIGINT, invoice_id TEXT, sub_id TEXT, charge_id TEXT,
                    kind TEXT NOT NULL, amount_cents INTEGER NOT NULL DEFAULT 0, currency TEXT, plan_key TEXT,
                    ft_src TEXT, ft_med TEXT, ft_vid TEXT, ft_term TEXT, ft_key TEXT,
                    lt_src TEXT, lt_med TEXT, lt_vid TEXT, lt_term TEXT, lt_key TEXT,
                    ts {real} NOT NULL)""")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_ts ON sales (ts)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_invoice ON sales (invoice_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_user ON sales (user_id, kind)")
                conn.execute(f"""CREATE TABLE IF NOT EXISTS video_meta (
                    vid TEXT PRIMARY KEY, title TEXT, fetched_at {real} NOT NULL DEFAULT 0)""")
                have = _users_columns(conn)
                cur = conn.cursor()
                for col, col_def in (("first_touch_json", "TEXT"), ("last_touch_json", "TEXT"),
                                     ("first_touch_at", real)):
                    if col not in have:
                        db._ensure_column(cur, "users", col, col_def)          # nullable, no default: instant
                for code, src, med, camp, content, label in SEED_CODES:
                    conn.execute(db._q("""INSERT INTO link_codes
                        (code, utm_source, utm_medium, utm_campaign, utm_content, utm_term, label, active)
                        VALUES (?,?,?,?,?,?,?,1) ON CONFLICT (code) DO NOTHING"""),
                                 (code, src, med, camp, content, code, label))
            _ready.add(key)
            return True
        except Exception as e:  # noqa: BLE001
            _failed_at[key] = time.time()
            _log(f"initialize failed (tracking paused, retry in {_RETRY_AFTER_S}s): {e}")
            return False


def _reset_for_tests() -> None:
    _ready.clear()
    _failed_at.clear()
    _codes_cache.clear()


# ---------------------------------------------------------------------------
# Touches
# ---------------------------------------------------------------------------
def _clip(v: Any, n: int = 200) -> str:
    return str(v).strip()[:n] if v is not None else ""


def clean_touch(raw: Any) -> dict:
    """Keep only known marketing keys (never free-form PII), each clipped to 200 chars."""
    if not isinstance(raw, dict):
        return {}
    out = {k: _clip(raw.get(k)) for k in TOUCH_KEYS if _clip(raw.get(k))}
    ts = raw.get("captured_at")
    if isinstance(ts, (int, float)) and ts > 0:
        out["captured_at"] = float(ts) / 1000.0 if ts > 1e11 else float(ts)    # JS sends ms
    return out


def has_utm(t: dict) -> bool:
    return any(t.get(k) for k in UTM_KEYS)


def is_hit(t: dict) -> bool:
    return has_utm(t) or any(t.get(k) for k in CLICK_KEYS) or bool(t.get("ref"))


def split_payload(attribution: Any) -> tuple[dict, dict]:
    """(first, last) from either cr-telemetry's attributionPayload() {v:2, first, last} or the flat
    attributionProps() shape (utm_* = last touch, initial_* = first touch)."""
    if not isinstance(attribution, dict):
        return {}, {}
    if isinstance(attribution.get("first"), dict) or isinstance(attribution.get("last"), dict):
        return clean_touch(attribution.get("first")), clean_touch(attribution.get("last"))
    last = clean_touch(attribution)
    first = clean_touch({k: attribution.get("initial_" + k) for k in TOUCH_KEYS})
    return first, last


_codes_cache: dict = {}


def _known_codes() -> set[str]:
    hit = _codes_cache.get("codes")
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        with db._conn() as conn:
            codes = {dict(r)["code"] for r in conn.execute("SELECT code FROM link_codes").fetchall()}
    except Exception as e:  # noqa: BLE001
        _log(f"link_codes read failed: {e}")
        return {c[0] for c in SEED_CODES}
    _codes_cache["codes"] = (time.time(), codes)
    return codes


def touch_vid(t: dict) -> str:
    content = (t or {}).get("utm_content") or ""
    if (t or {}).get("utm_source") == "youtube" and VIDEO_ID_RE.match(content):
        return content
    return ""


def touch_code(t: dict) -> str:
    term = ((t or {}).get("utm_term") or "").lower()
    return term if term and term in _known_codes() else ""


def touch_key(t: dict) -> str:
    """The report row a touch belongs to: 'vid:<id>', 'code:<code>', 'src:<source>/<medium>', 'ref:<host>' or
    'unknown'."""
    t = t or {}
    vid = touch_vid(t)
    if vid:
        return f"vid:{vid}"
    code = touch_code(t)
    if code:
        return f"code:{code}"
    if t.get("utm_source"):
        return f"src:{t['utm_source']}/{t.get('utm_medium') or '-'}"[:120]
    if t.get("ref"):
        return f"ref:{t['ref']}"[:120]
    return "unknown"


def _load_json(s: Any) -> dict:
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def get_touches(user_id: int) -> tuple[dict, dict]:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT first_touch_json, last_touch_json FROM users WHERE id = ?"),
                           (int(user_id),)).fetchone()
    if not row:
        return {}, {}
    row = dict(row)
    return _load_json(row.get("first_touch_json")), _load_json(row.get("last_touch_json"))


def record_touches(user_id: int, attribution: Any) -> tuple[dict, dict]:
    """First touch is written once, only while empty. Last touch is replaced when the incoming touch carries a UTM.
    Returns the stored (first, last)."""
    inc_first, inc_last = split_payload(attribution)
    first, last = get_touches(user_id)
    fields: dict[str, Any] = {}
    if not first:
        candidate = inc_first if is_hit(inc_first) else (inc_last if is_hit(inc_last) else {})
        if candidate:
            first = {**candidate, "captured_at": float(candidate.get("captured_at") or time.time())}
            fields["first_touch_json"] = json.dumps(first, separators=(",", ":"))
            fields["first_touch_at"] = float(first.get("captured_at") or time.time())
    if has_utm(inc_last):
        last = inc_last
        fields["last_touch_json"] = json.dumps(last, separators=(",", ":"))
    elif not last and is_hit(inc_last):
        last = inc_last
        fields["last_touch_json"] = json.dumps(last, separators=(",", ":"))
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with db._conn() as conn:
            conn.execute(db._q(f"UPDATE users SET {sets} WHERE id = ?"), (*fields.values(), int(user_id)))
    return first, last


def initial_props(first: dict) -> dict:
    """initial_* person properties for PostHog $set_once."""
    out = {f"initial_{k}": v for k, v in (first or {}).items() if k in TOUCH_KEYS and v}
    vid = touch_vid(first or {})
    if vid:
        out["initial_video_id"] = vid
    return out


def _brief(t: dict) -> dict:
    return {"src": t.get("utm_source") or "", "med": t.get("utm_medium") or "", "vid": touch_vid(t),
            "term": t.get("utm_term") or "", "key": touch_key(t)}


def checkout_metadata(first: dict, last: dict) -> dict:
    b1, b2 = _brief(first or {}), _brief(last or {})
    at = (first or {}).get("captured_at")
    meta = {"ft_src": b1["src"], "ft_med": b1["med"], "ft_vid": b1["vid"], "ft_term": b1["term"],
            "ft_at": str(int(at)) if at else "",
            "lt_src": b2["src"], "lt_med": b2["med"], "lt_vid": b2["vid"], "lt_term": b2["term"]}
    return {k: v for k, v in meta.items() if v}


def merge_metadata(existing: dict | None, extra: dict) -> dict:
    """A copy of ``existing`` with ``extra`` ADDED: existing keys are never overwritten, Stripe's limits (50 keys,
    40-char keys, 500-char values) are respected, and anything that would not fit is dropped."""
    out = dict(existing or {})
    for k, v in (extra or {}).items():
        k = str(k)[:STRIPE_MAX_KEY_LEN]
        if k in out or len(out) >= STRIPE_MAX_KEYS:
            continue
        out[k] = str(v)[:STRIPE_MAX_VALUE_LEN]
    return out


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
def _sale_exists(conn, *, invoice_id: str = "", kinds: tuple = ()) -> bool:
    if not invoice_id:
        return False
    q = "SELECT 1 FROM sales WHERE invoice_id = ?"
    args: list = [invoice_id]
    if kinds:
        q += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
        args += list(kinds)
    return conn.execute(db._q(q), tuple(args)).fetchone() is not None


def insert_sale(*, event_id: str, user_id: int | None, kind: str, amount_cents: int, currency: str = "usd",
                invoice_id: str = "", sub_id: str = "", charge_id: str = "", plan_key: str = "",
                ts: float | None = None, first: dict | None = None, last: dict | None = None) -> bool:
    """Idempotent: a Stripe event (or an invoice already recorded) is written once. Returns True when a row was
    added."""
    if kind not in SALE_KINDS or not event_id:
        return False
    if first is None and last is None and user_id:
        first, last = get_touches(int(user_id))
    b1, b2 = _brief(first or {}), _brief(last or {})
    with db._conn() as conn:
        if kind != "refund" and _sale_exists(conn, invoice_id=invoice_id, kinds=("first_payment", "renewal")):
            return False
        cur = conn.execute(db._q("""INSERT INTO sales (stripe_event_id, user_id, invoice_id, sub_id, charge_id, kind,
            amount_cents, currency, plan_key, ft_src, ft_med, ft_vid, ft_term, ft_key,
            lt_src, lt_med, lt_vid, lt_term, lt_key, ts)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (stripe_event_id) DO NOTHING"""),
            (event_id, int(user_id) if user_id else None, invoice_id, sub_id, charge_id, kind, int(amount_cents),
             (currency or "usd").lower(), plan_key, b1["src"], b1["med"], b1["vid"], b1["term"], b1["key"],
             b2["src"], b2["med"], b2["vid"], b2["term"], b2["key"], float(ts or time.time())))
        return bool(getattr(cur, "rowcount", 0) and cur.rowcount > 0)


def _has_paid_before(user_id: int, sub_id: str) -> bool:
    with db._conn() as conn:
        q = "SELECT 1 FROM sales WHERE kind IN ('first_payment','renewal') AND (user_id = ?"
        args: list = [int(user_id)]
        if sub_id:
            q += " OR sub_id = ?"
            args.append(sub_id)
        return conn.execute(db._q(q + ")"), tuple(args)).fetchone() is not None


def _refunded_so_far(charge_id: str) -> int:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT COALESCE(SUM(amount_cents),0) AS n FROM sales WHERE kind='refund' "
                                 "AND charge_id = ?"), (charge_id,)).fetchone()
    return int(dict(row)["n"] or 0) if row else 0


def record_from_event(evt_type: str, event: dict, obj: dict, *, find_user: Callable[[str, str], dict | None],
                      is_cr_invoice: Callable[[dict], bool], sub_of: Callable[[dict], str] | None = None) -> str:
    """Writes the sales row a Stripe event implies (only ever to `sales`). Returns what it did, for logs/tests."""
    event_id = str(event.get("id") or "")
    ts = float(event.get("created") or time.time())
    if evt_type == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        uid = meta.get("user_id")
        amount = int(obj.get("amount_total") or 0)
        if not uid or amount <= 0:
            return "skip:no-user-or-zero"
        topup = bool(meta.get("topup_credits") or meta.get("cloud_topup_cents"))
        if not topup and obj.get("mode") not in (None, "subscription"):
            return "skip:mode"
        kind = "topup" if topup else "first_payment"
        inv = obj.get("invoice")
        inv = inv.get("id") if isinstance(inv, dict) else (inv or "")
        sub = obj.get("subscription")
        sub = sub.get("id") if isinstance(sub, dict) else (sub or "")
        ok = insert_sale(event_id=event_id, user_id=int(uid), kind=kind, amount_cents=amount,
                         currency=obj.get("currency") or "usd", invoice_id=str(inv), sub_id=str(sub),
                         plan_key=str(meta.get("plan") or ""), ts=ts)
        return f"{kind}:{'added' if ok else 'dup'}"
    if evt_type == "invoice.paid":
        amount = int(obj.get("amount_paid") or 0)
        reason = obj.get("billing_reason") or ""
        if amount <= 0:
            return "skip:zero"
        if reason == "subscription_create":
            return "skip:covered-by-checkout"          # the checkout.session.completed row is the first payment
        if reason not in ("subscription_cycle", "subscription_update"):
            return f"skip:reason:{reason}"
        if not is_cr_invoice(obj):
            return "skip:not-cr"
        sub_id = sub_of(obj) if sub_of else ""
        customer = obj.get("customer")
        customer = customer.get("id") if isinstance(customer, dict) else (customer or "")
        row = find_user(sub_id, str(customer))
        if not row:
            return "skip:no-user"
        kind = "renewal" if _has_paid_before(row["id"], sub_id) else "first_payment"   # trial conversions are firsts
        ok = insert_sale(event_id=event_id, user_id=row["id"], kind=kind, amount_cents=amount,
                         currency=obj.get("currency") or "usd", invoice_id=str(obj.get("id") or ""), sub_id=sub_id,
                         plan_key=str(row.get("plan") or ""), ts=ts)
        return f"{kind}:{'added' if ok else 'dup'}"
    if evt_type == "charge.refunded":
        customer = obj.get("customer")
        customer = customer.get("id") if isinstance(customer, dict) else (customer or "")
        row = find_user("", str(customer)) if customer else None
        if not row:
            return "skip:no-user"
        charge_id = str(obj.get("id") or "")
        total = int(obj.get("amount_refunded") or 0)
        amount = max(0, total - _refunded_so_far(charge_id))
        if amount <= 0:
            return "skip:zero"
        inv = obj.get("invoice")
        inv = inv.get("id") if isinstance(inv, dict) else (inv or "")
        ok = insert_sale(event_id=event_id, user_id=row["id"], kind="refund", amount_cents=amount,
                         currency=obj.get("currency") or "usd", invoice_id=str(inv), charge_id=charge_id,
                         plan_key=str(row.get("plan") or ""), ts=ts)
        return f"refund:{'added' if ok else 'dup'}"
    return "skip:event"


# ---------------------------------------------------------------------------
# The only entry points server.py calls. Each one is a no-op when the flag is off and NEVER raises.
# ---------------------------------------------------------------------------
def safe_sign_in(user_id: int, attribution: Any, posthog_client: Any = None) -> None:
    """Called from _sign_in: store first/last touch, then $set_once the initial_* person properties."""
    if not enabled():
        return
    try:
        if not initialize():
            return
        first, _last = record_touches(int(user_id), attribution)
        props = initial_props(first)
        if posthog_client is not None and props:
            posthog_client.set_once(distinct_id=str(user_id), properties=props)
    except Exception as e:  # noqa: BLE001
        _log(f"sign-in hook failed for user {user_id}: {e}")


def _set_customer_meta(customer_id: str, meta: dict) -> None:
    try:
        import stripe
        stripe.Customer.modify(customer_id, metadata=meta)
    except Exception as e:  # noqa: BLE001
        _log(f"customer metadata update failed: {e}")


def safe_checkout(user: dict, attribution: Any, session_kwargs: dict, *, customer_id: str = "",
                  background: bool = True) -> None:
    """Called from create_checkout just before the Stripe session is created. Updates the user's touches and ADDS
    ft_*/lt_* keys to the session and subscription metadata. Price, line items, mode and URLs are never touched,
    and session_kwargs is only changed once everything has been computed."""
    if not enabled():
        return
    try:
        if not initialize():
            return
        first, last = record_touches(int(user["id"]), attribution)
        extra = checkout_metadata(first, last)
        if not extra:
            return
        session_meta = merge_metadata(session_kwargs.get("metadata"), extra)
        sub_data = dict(session_kwargs.get("subscription_data") or {})
        sub_meta = merge_metadata(sub_data.get("metadata"), extra)
        if session_kwargs.get("mode") == "subscription":
            sub_data["metadata"] = sub_meta
            session_kwargs["subscription_data"] = sub_data
        session_kwargs["metadata"] = session_meta
        cust = {k: extra[k] for k in ("ft_vid", "ft_src") if extra.get(k)}
        if customer_id and cust:
            if background:
                threading.Thread(target=_set_customer_meta, args=(customer_id, cust), daemon=True).start()
            else:
                _set_customer_meta(customer_id, cust)
    except Exception as e:  # noqa: BLE001
        _log(f"checkout hook failed for user {user.get('id') if isinstance(user, dict) else '?'}: {e}")


def safe_money_props(user_id: Any, props: dict) -> None:
    """Called from _money_track: adds ft_vid / lt_vid to the PostHog money event without overwriting a key."""
    if not enabled():
        return
    try:
        if not user_id or not initialize():
            return
        first, last = get_touches(int(user_id))
        for k, v in (("ft_vid", touch_vid(first)), ("lt_vid", touch_vid(last))):
            if v:
                props.setdefault(k, v)
    except Exception as e:  # noqa: BLE001
        _log(f"money props failed for user {user_id}: {e}")


def safe_record_sale(evt_type: str, event: dict, obj: dict, **kw) -> str:
    """Called at the end of the Stripe webhook. Writes only to `sales`; the webhook's response never changes."""
    if not enabled() or evt_type not in ("checkout.session.completed", "invoice.paid", "charge.refunded"):
        return "off"
    try:
        if not initialize():
            return "unavailable"
        return record_from_event(evt_type, event or {}, obj or {}, **kw)
    except Exception as e:  # noqa: BLE001
        _log(f"sale record failed for {evt_type} {event.get('id') if isinstance(event, dict) else ''}: {e}")
        return "error"


# ---------------------------------------------------------------------------
# Redirects
# ---------------------------------------------------------------------------
def _referrer_host(request: Request) -> str:
    try:
        return (urlparse(request.headers.get("referer") or "").hostname or "")[:120]
    except Exception:  # noqa: BLE001
        return ""


def log_click(kind: str, key: str, request: Request) -> None:
    """No IP is stored; only whether the user agent looks like a bot, and the referring host."""
    try:
        if not enabled() or not initialize():
            return
        ua = request.headers.get("user-agent") or ""
        with db._conn() as conn:
            conn.execute(db._q("INSERT INTO link_clicks (kind, key, ts, ua_is_bot, referrer_host) VALUES (?,?,?,?,?)"),
                         (kind, key[:64], time.time(), 1 if (not ua or _BOT_RE.search(ua)) else 0,
                          _referrer_host(request)))
    except Exception as e:  # noqa: BLE001
        _log(f"click log failed: {e}")


def video_target(vid: str) -> str:
    return "/?" + urlencode({"utm_source": "youtube", "utm_medium": "organic_video", "utm_campaign": "cr_sales",
                             "utm_content": vid, "utm_term": "description"})


def code_target(code: str) -> str:
    """'/' for an unknown or inactive code."""
    if not CODE_RE.match(code or "") or not initialize():
        return "/"
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM link_codes WHERE code = ? AND active = 1"), (code,)).fetchone()
    if not row:
        return "/"
    row = dict(row)
    params = {k: row.get(k) for k in UTM_KEYS if row.get(k)}
    return "/?" + urlencode(params) if params else "/"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _parse_day(s: str | None, default: datetime) -> datetime:
    if s:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return default


def window(frm: str | None, to: str | None) -> tuple[float, float]:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = _parse_day(frm, today - timedelta(days=30))
    end = _parse_day(to, today) + timedelta(days=1)                 # 'to' is inclusive
    return start.timestamp(), end.timestamp()


def _blank(key: str) -> dict:
    return {"key": key, "kind": key.split(":", 1)[0] if ":" in key else key, "label": "", "title": "",
            "clicks": 0, "signups": 0, "paying": 0, "first_payment_cents": 0, "revenue_cents": 0, "refund_cents": 0}


def report(frm: str | None = None, to: str | None = None, model: str = "last",
           titles: Callable[[list[str]], dict] | None = None) -> dict:
    model = "first" if model == "first" else "last"
    start, end = window(frm, to)
    rows: dict[str, dict] = {}

    def row(key: str) -> dict:
        if key not in rows:
            rows[key] = _blank(key)
        return rows[key]

    with db._conn() as conn:
        for r in conn.execute(db._q("SELECT kind, key FROM link_clicks WHERE ts >= ? AND ts < ? AND ua_is_bot = 0"),
                              (start, end)).fetchall():
            r = dict(r)
            row(("vid:" if r["kind"] == "v" else "code:") + r["key"])["clicks"] += 1
        col = "first_touch_json" if model == "first" else "last_touch_json"
        for r in conn.execute(db._q(f"SELECT {col} AS t FROM users WHERE created_at >= ? AND created_at < ?"),
                              (start, end)).fetchall():
            row(touch_key(_load_json(dict(r)["t"])))["signups"] += 1
        kcol = "ft_key" if model == "first" else "lt_key"
        payers: dict[str, set] = {}
        for r in conn.execute(db._q(f"SELECT {kcol} AS k, kind, amount_cents, user_id FROM sales "
                                    "WHERE ts >= ? AND ts < ?"), (start, end)).fetchall():
            r = dict(r)
            target = row(r["k"] or "unknown")
            amt = int(r["amount_cents"] or 0)
            if r["kind"] == "refund":
                target["refund_cents"] += amt
                continue
            target["revenue_cents"] += amt
            if r["kind"] == "first_payment":
                target["first_payment_cents"] += amt
                payers.setdefault(target["key"], set()).add(r["user_id"])
        for k, s in payers.items():
            rows[k]["paying"] = len(s)
        labels = {dict(r)["code"]: dict(r)["label"] for r in conn.execute("SELECT code, label FROM link_codes")}
    vids = [k[4:] for k in rows if k.startswith("vid:")]
    names = {}
    if titles and vids:
        try:
            names = titles(vids) or {}
        except Exception as e:  # noqa: BLE001
            _log(f"video titles failed: {e}")
    for r in rows.values():
        if r["kind"] == "vid":
            r["title"] = names.get(r["key"][4:], "")
        elif r["kind"] == "code":
            r["label"] = labels.get(r["key"][5:], "")
        r["net_revenue_cents"] = r["revenue_cents"] - r["refund_cents"]
        r["signup_to_paid_pct"] = round(100.0 * r["paying"] / r["signups"], 1) if r["signups"] else None
        r["revenue_per_click_cents"] = round(r["revenue_cents"] / r["clicks"], 1) if r["clicks"] else None
    out = sorted(rows.values(), key=lambda r: (-r["revenue_cents"], -r["signups"], -r["clicks"], r["key"]))
    totals = {k: sum(r[k] for r in out) for k in ("clicks", "signups", "paying", "first_payment_cents",
                                                  "revenue_cents", "refund_cents", "net_revenue_cents")}
    totals["signup_to_paid_pct"] = round(100.0 * totals["paying"] / totals["signups"], 1) if totals["signups"] else None
    totals["revenue_per_click_cents"] = (round(totals["revenue_cents"] / totals["clicks"], 1)
                                         if totals["clicks"] else None)
    return {"model": model, "from": datetime.fromtimestamp(start, timezone.utc).date().isoformat(),
            "to": (datetime.fromtimestamp(end, timezone.utc) - timedelta(days=1)).date().isoformat(),
            "totals": totals, "rows": out}


CSV_COLUMNS = ("key", "kind", "label", "title", "clicks", "signups", "paying", "first_payment_cents",
               "revenue_cents", "refund_cents", "net_revenue_cents", "signup_to_paid_pct", "revenue_per_click_cents")


def to_csv(rep: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in rep["rows"]:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in CSV_COLUMNS])
    return buf.getvalue()


_TITLE_TTL_S = 7 * 86400
_TITLE_RETRY_S = 86400


def video_titles(vids: list[str], *, fetch_limit: int = 15, timeout: float = 3.0) -> dict:
    """Titles from YouTube oEmbed, cached in video_meta (a failed lookup is retried after a day)."""
    vids = [v for v in dict.fromkeys(vids) if VIDEO_ID_RE.match(v)]
    if not vids:
        return {}
    now = time.time()
    with db._conn() as conn:
        marks = ",".join("?" for _ in vids)
        cached = {dict(r)["vid"]: dict(r) for r in conn.execute(
            db._q(f"SELECT vid, title, fetched_at FROM video_meta WHERE vid IN ({marks})"), tuple(vids)).fetchall()}
    out = {v: (c.get("title") or "") for v, c in cached.items()}
    stale = [v for v in vids if v not in cached or now - float(cached[v]["fetched_at"] or 0) >
             (_TITLE_TTL_S if cached[v].get("title") else _TITLE_RETRY_S)][:fetch_limit]
    if not stale:
        return out
    import requests
    for v in stale:
        title = ""
        try:
            r = requests.get("https://www.youtube.com/oembed",
                             params={"url": f"https://www.youtube.com/watch?v={v}", "format": "json"}, timeout=timeout)
            if r.ok:
                title = _clip(r.json().get("title"), 300)
        except Exception as e:  # noqa: BLE001
            _log(f"oembed {v} failed: {e}")
        with db._conn() as conn:
            conn.execute(db._q("""INSERT INTO video_meta (vid, title, fetched_at) VALUES (?,?,?)
                ON CONFLICT (vid) DO UPDATE SET title = excluded.title, fetched_at = excluded.fetched_at"""),
                         (v, title or out.get(v, ""), now))
        if title:
            out[v] = title
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def make_router(require_admin: Callable, static_dir) -> APIRouter:
    router = APIRouter()

    @router.get("/v/{vid}", include_in_schema=False)
    async def video_link(vid: str, request: Request):
        if not VIDEO_ID_RE.match(vid or ""):
            return RedirectResponse("/", status_code=302)
        log_click("v", vid, request)
        return RedirectResponse(video_target(vid), status_code=302)

    @router.get("/r/{code}", include_in_schema=False)
    async def short_link(code: str, request: Request):
        code = (code or "").strip().lower()
        try:
            target = code_target(code)          # links keep working with the flag off; only the click log stops
        except Exception as e:  # noqa: BLE001
            _log(f"short link {code!r} lookup failed: {e}")
            target = "/"
        if target != "/":
            log_click("r", code, request)
        return RedirectResponse(target, status_code=302)

    @router.get("/api/admin/sales/attribution")
    async def sales_attribution(request: Request, model: str = "last", format: str = "json",
                                admin: dict = Depends(require_admin)):
        import asyncio
        if not initialize():
            return Response('{"detail":"Attribution storage unavailable"}', status_code=503,
                            media_type="application/json")
        qp = request.query_params
        rep = await asyncio.to_thread(report, qp.get("from"), qp.get("to"), model, video_titles)
        if format == "csv":
            return Response(to_csv(rep), media_type="text/csv", headers={
                "Content-Disposition": f'attachment; filename="cr-attribution-{rep["model"]}-{rep["from"]}'
                                       f'-{rep["to"]}.csv"', "Cache-Control": "private, no-store"})
        return rep

    @router.get("/admin/sales", include_in_schema=False)
    async def admin_sales_page():
        return FileResponse(str(static_dir / "admin-sales.html"), headers={"Cache-Control": "private, no-store"})

    return router
