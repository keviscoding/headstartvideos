"""Sales attribution (webapp/attribution.py): /v and /r links, first/last touch, checkout metadata, the sales table
from Stripe webhooks, the admin report, and above all ISOLATION: sign-in, checkout and webhooks behave exactly as
before when attribution code raises or CR_ATTRIBUTION_ENABLED=0. Stripe is mocked; nothing leaves the machine."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import config
from webapp import attribution as attr
from webapp import database as db

VID = "dQw4w9WgXcQ"
PRICE = "price_creator_monthly_test"
ADMIN = "boss@example.com"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "attr.db")
    db._init_db()
    attr._reset_for_tests()
    monkeypatch.setenv("CR_ATTRIBUTION_ENABLED", "1")          # off by default; these tests exercise it on
    from webapp import server
    import stripe

    events, created, modified = [], [], []
    monkeypatch.setattr(server, "track", lambda uid, event, props=None: events.append((uid, event, props or {})))
    monkeypatch.setattr(server, "_posthog", None)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_dummy")
    monkeypatch.setattr(config, "STRIPE_PRICE_DAILY_MONTHLY", PRICE)
    monkeypatch.setattr(config, "FREE_TRIAL_DAYS", 7, raising=False)
    monkeypatch.setattr(config, "ADMIN_EMAILS", [ADMIN])
    monkeypatch.setattr(server._lifecycle_email, "on_subscribed", lambda uid: None)

    def session_create(**kw):
        created.append(json.loads(json.dumps(kw)))
        return SimpleNamespace(url="https://checkout.stripe.test/s/1")
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(session_create))
    monkeypatch.setattr(stripe.Customer, "create", staticmethod(lambda **kw: SimpleNamespace(id="cus_test")))
    monkeypatch.setattr(stripe.Customer, "modify", staticmethod(lambda cid, **kw: modified.append((cid, kw))))
    monkeypatch.setattr(stripe.Webhook, "construct_event", staticmethod(lambda payload, sig, secret: json.loads(payload)))
    # customer metadata runs in a thread in production; run inline here so tests are deterministic
    real_checkout = attr.safe_checkout
    monkeypatch.setattr(attr, "safe_checkout", lambda *a, **kw: real_checkout(*a, **{**kw, "background": False}))
    return SimpleNamespace(client=TestClient(server.app), server=server, events=events, created=created,
                           modified=modified)


def _login(env, email="maker@example.com") -> dict:
    user = db.get_user_by_email(email) or db.create_user(email)
    env.client.cookies.set("session", db.create_session(user["id"]))
    return user


def _hook(env, event: dict):
    return env.client.post("/api/billing/webhook", content=json.dumps(event), headers={"stripe-signature": "t"})


def _sales():
    with db._conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM sales ORDER BY ts").fetchall()]


def _user(uid):
    return db.get_user_by_id(uid)


def _attr_tables() -> set:
    with db._conn() as conn:
        names = {dict(r)["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return names & {"link_codes", "link_clicks", "sales", "video_meta"}


def _user_cols() -> set:
    with db._conn() as conn:
        return {dict(r)["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}


YT_TOUCH = {"utm_source": "youtube", "utm_medium": "organic_video", "utm_campaign": "cr_sales",
            "utm_content": VID, "utm_term": "description"}


# --- links ---------------------------------------------------------------------------------------------------------
def test_video_link_redirects_valid_ids_with_params_before_any_hash(env):
    res = env.client.get(f"/v/{VID}", follow_redirects=False)
    assert res.status_code == 302
    loc = res.headers["location"]
    assert "#" not in loc and loc.startswith("/?")
    assert {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()} == YT_TOUCH
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM link_clicks").fetchall()]
        cols = {dict(r)["name"] for r in conn.execute("PRAGMA table_info(link_clicks)").fetchall()}
    assert rows[0]["kind"] == "v" and rows[0]["key"] == VID
    assert not any("ip" in c for c in cols), "no IP is stored"


@pytest.mark.parametrize("bad", ["short", "dQw4w9WgXcQx", "dQw4w9WgXc!", "dQw4w9%20gXcQ", "../../etc/pa"])
def test_invalid_video_ids_go_home_and_are_not_logged(env, bad):
    res = env.client.get(f"/v/{bad}", follow_redirects=False)
    assert res.status_code in (302, 404) and (res.status_code == 404 or res.headers["location"] == "/")
    attr.initialize()
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM link_clicks").fetchone()["n"] == 0


def test_short_links_use_link_codes_and_unknown_codes_go_home(env):
    res = env.client.get("/r/kevis-about", follow_redirects=False, headers={"referer": "https://www.youtube.com/@x"})
    q = {k: v[0] for k, v in parse_qs(urlparse(res.headers["location"]).query).items()}
    assert res.status_code == 302 and q["utm_medium"] == "social" and q["utm_content"] == "channel_description"
    assert q["utm_source"] == "youtube" and q["utm_term"] == "kevis-about"
    for code in ("ig-kevis", "ig-jesse", "ig-jesse2", "ig-abby", "tt-glintverse", "fb-jesse", "x-kevis", "email",
                 "discord", "jesse-about"):
        assert env.client.get(f"/r/{code}", follow_redirects=False).headers["location"] != "/"
    assert env.client.get("/r/nope", follow_redirects=False).headers["location"] == "/"
    with db._conn() as conn:
        conn.execute("UPDATE link_codes SET active = 0 WHERE code = 'discord'")
    assert env.client.get("/r/discord", follow_redirects=False).headers["location"] == "/"
    with db._conn() as conn:
        row = dict(conn.execute("SELECT * FROM link_clicks WHERE key='kevis-about'").fetchone())
    assert row["referrer_host"] == "www.youtube.com"


def test_bots_are_flagged(env):
    env.client.get(f"/v/{VID}", follow_redirects=False, headers={"user-agent": "Googlebot/2.1"})
    with db._conn() as conn:
        assert conn.execute("SELECT ua_is_bot FROM link_clicks").fetchone()["ua_is_bot"] == 1


# --- touches -------------------------------------------------------------------------------------------------------
def test_first_touch_is_written_once_and_last_touch_is_replaced(env):
    attr.initialize()
    uid = db.create_user("t@example.com")["id"]
    attr.record_touches(uid, {"v": 2, "first": YT_TOUCH, "last": YT_TOUCH})
    attr.record_touches(uid, {"v": 2, "first": {"utm_source": "instagram"},
                              "last": {"utm_source": "instagram", "utm_medium": "social"}})
    first, last = attr.get_touches(uid)
    assert first["utm_content"] == VID, "first touch never changes"
    assert last == {"utm_source": "instagram", "utm_medium": "social"}, "last is replaced, not merged"
    attr.record_touches(uid, {"v": 2, "last": {"ref": "google.com"}})
    assert attr.get_touches(uid)[1]["utm_source"] == "instagram", "a touch without a UTM does not overwrite last"
    assert _user(uid)["first_touch_at"]


def test_flat_attribution_props_from_existing_sign_in_pages_are_understood(env):
    attr.initialize()
    uid = db.create_user("flat@example.com")["id"]
    attr.record_touches(uid, {"utm_source": "x", "initial_utm_source": "youtube", "initial_utm_medium": "organic_video",
                             "initial_utm_content": VID})
    first, last = attr.get_touches(uid)
    assert attr.touch_vid(first) == VID and last["utm_source"] == "x"


def test_sign_in_stores_touches_and_sets_initial_props_once(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.server, "_posthog", SimpleNamespace(
        set_once=lambda **kw: calls.append(kw), identify=lambda **kw: None, capture=lambda **kw: None))
    code = db.create_verify_code("new@example.com")
    res = env.client.post("/api/auth/verify", json={"email": "new@example.com", "code": code,
                                                    "attribution": {f"initial_{k}": v for k, v in YT_TOUCH.items()}})
    assert res.status_code == 200 and res.json()["is_new"] is True
    uid = res.json()["user"]["id"]
    assert attr.touch_vid(attr.get_touches(uid)[0]) == VID
    assert calls and calls[0]["properties"]["initial_video_id"] == VID and calls[0]["distinct_id"] == str(uid)


# --- checkout ------------------------------------------------------------------------------------------------------
def test_metadata_only_adds_keys_within_stripe_limits():
    existing = {f"k{i}": "x" for i in range(48)}
    existing["plan"] = "daily_monthly"
    out = attr.merge_metadata(existing, {"plan": "HACK", "ft_src": "y" * 900, "a" * 60: "1", "lt_src": "z"})
    assert out["plan"] == "daily_monthly", "existing keys are never overwritten"
    assert len(out) == 50 and len(out["ft_src"]) == 500
    assert all(len(k) <= 40 for k in out) and "lt_src" not in out, "keys past 50 are dropped"
    assert existing.get("ft_src") is None, "the original dict is untouched"


def test_checkout_adds_attribution_metadata_without_touching_price_mode_or_urls(env, monkeypatch):
    user = _login(env)
    body = {"plan": "daily_monthly", "skip_trial": True}
    monkeypatch.setenv("CR_ATTRIBUTION_ENABLED", "0")
    assert env.client.post("/api/billing/checkout", json=body).status_code == 200
    db.update_user(user["id"], trial_used=0)
    monkeypatch.setenv("CR_ATTRIBUTION_ENABLED", "1")
    res = env.client.post("/api/billing/checkout", json={**body, "attribution": {"v": 2, "first": YT_TOUCH,
                                                                                  "last": YT_TOUCH}})
    assert res.status_code == 200 and res.json()["url"]
    off, on = env.created
    for k in ("line_items", "mode", "success_url", "cancel_url", "customer", "allow_promotion_codes"):
        assert on[k] == off[k], k
    assert {k: on["metadata"][k] for k in off["metadata"]} == off["metadata"]
    m = on["metadata"]
    assert m["ft_vid"] == VID and m["lt_vid"] == VID and m["ft_src"] == "youtube" and m["ft_term"] == "description"
    assert m["ft_at"] and on["subscription_data"]["metadata"]["lt_vid"] == VID
    assert "trial_period_days" not in on.get("subscription_data", {}), "skip_trial stays a paid checkout"
    assert env.modified == [("cus_test", {"metadata": {"ft_vid": VID, "ft_src": "youtube"}})]


def test_checkout_with_trial_keeps_trial_days(env):
    _login(env)
    env.client.post("/api/billing/checkout", json={"plan": "daily_monthly", "attribution": {"last": YT_TOUCH}})
    sub = env.created[-1]["subscription_data"]
    assert sub["trial_period_days"] == 7 and sub["metadata"]["lt_vid"] == VID


@pytest.mark.parametrize("junk", ["a string", 42, ["x"], {"first": "nope", "last": 5}, None])
def test_malformed_attribution_never_fails_checkout(env, junk):
    _login(env)
    res = env.client.post("/api/billing/checkout", json={"plan": "daily_monthly", "attribution": junk})
    assert res.status_code == 200


# --- webhooks / sales ----------------------------------------------------------------------------------------------
def _checkout_event(uid, eid="evt_1", amount=4900):
    return {"id": eid, "type": "checkout.session.completed", "created": 1760000000, "data": {"object": {
        "id": "cs_1", "mode": "subscription", "amount_total": amount, "currency": "usd", "subscription": "sub_1",
        "invoice": "in_1", "metadata": {"user_id": str(uid), "plan": "daily_monthly", "skip_trial": "1",
                                        "ft_vid": VID}}}}


def _invoice_event(eid, inv, reason="subscription_cycle", amount=4900, created=1762600000):
    return {"id": eid, "type": "invoice.paid", "created": created, "data": {"object": {
        "id": inv, "customer": "cus_test", "subscription": "sub_1", "amount_paid": amount, "currency": "usd",
        "billing_reason": reason, "lines": {"data": [{"price": {"id": PRICE}, "amount": amount}]}}}}


def _paying_user(env, touch=YT_TOUCH):
    user = db.create_user("payer@example.com")
    attr.initialize()
    attr.record_touches(user["id"], {"first": touch, "last": touch})
    db.update_user(user["id"], stripe_customer_id="cus_test")
    return user


def test_duplicate_webhooks_write_one_sale_and_responses_do_not_change(env):
    user = _paying_user(env)
    first = _hook(env, _checkout_event(user["id"]))
    again = _hook(env, _checkout_event(user["id"]))
    assert first.status_code == again.status_code == 200 and first.json() == again.json() == {"ok": True}
    rows = _sales()
    assert len(rows) == 1 and rows[0]["kind"] == "first_payment" and rows[0]["amount_cents"] == 4900
    assert rows[0]["ft_vid"] == VID and rows[0]["lt_key"] == f"vid:{VID}"
    assert _user(user["id"])["plan"] == "daily", "billing itself still ran"


def test_renewals_refunds_and_subscription_create_skip(env):
    user = _paying_user(env)
    _hook(env, _checkout_event(user["id"]))
    _hook(env, _invoice_event("evt_sc", "in_1", reason="subscription_create"))    # same money as the checkout
    _hook(env, _invoice_event("evt_r1", "in_2"))
    _hook(env, _invoice_event("evt_r1", "in_2"))                                    # duplicate delivery
    refund = {"id": "evt_ref1", "type": "charge.refunded", "created": 1762700000, "data": {"object": {
        "id": "ch_2", "customer": "cus_test", "invoice": "in_2", "amount_refunded": 1000, "currency": "usd"}}}
    assert _hook(env, refund).json() == {"ok": True}
    refund2 = json.loads(json.dumps(refund))
    refund2["id"] = "evt_ref2"
    refund2["data"]["object"]["amount_refunded"] = 4900                            # cumulative on the charge
    _hook(env, refund2)
    kinds = [(r["kind"], r["amount_cents"]) for r in _sales()]
    assert kinds == [("first_payment", 4900), ("renewal", 4900), ("refund", 1000), ("refund", 3900)]


def test_trial_conversion_counts_as_first_payment(env):
    user = _paying_user(env)
    db.update_user(user["id"], plan="daily_trial", stripe_sub_id="sub_1")
    _hook(env, _invoice_event("evt_conv", "in_9"))
    assert [r["kind"] for r in _sales()] == ["first_payment"]
    assert _user(user["id"])["plan"] == "daily"


def test_money_events_carry_ft_and_lt_video(env):
    user = _paying_user(env)
    _hook(env, _checkout_event(user["id"]))
    money = [p for _, e, p in env.events if e == "subscription_started"]
    assert money and money[0]["ft_vid"] == VID and money[0]["lt_vid"] == VID


def test_refund_for_an_unknown_customer_is_ignored(env):
    attr.initialize()
    _hook(env, {"id": "evt_x", "type": "charge.refunded", "data": {"object": {
        "id": "ch_x", "customer": "cus_other_business", "amount_refunded": 500}}})
    assert _sales() == []


# --- report --------------------------------------------------------------------------------------------------------
def test_admin_report_last_and_first_and_csv_matches_json(env, monkeypatch):
    monkeypatch.setattr(attr, "video_titles", lambda vids, **kw: {VID: "How I <b>grew</b>"})
    user = _paying_user(env, touch={"utm_source": "instagram", "utm_medium": "social", "utm_term": "ig-kevis"})
    attr.record_touches(user["id"], {"last": YT_TOUCH})                 # first = ig-kevis, last = the video
    env.client.get(f"/v/{VID}", follow_redirects=False)
    env.client.get(f"/v/{VID}", follow_redirects=False)
    _hook(env, {**_checkout_event(user["id"]), "created": __import__("time").time()})
    _login(env, ADMIN)
    last = env.client.get("/api/admin/sales/attribution?model=last").json()
    row = next(r for r in last["rows"] if r["key"] == f"vid:{VID}")
    assert row["clicks"] == 2 and row["signups"] == 1 and row["paying"] == 1 and row["revenue_cents"] == 4900
    assert row["title"] == "How I <b>grew</b>" and row["signup_to_paid_pct"] == 100.0
    assert row["revenue_per_click_cents"] == 2450.0 and last["totals"]["revenue_cents"] == 4900
    first = env.client.get("/api/admin/sales/attribution?model=first").json()
    frow = next(r for r in first["rows"] if r["key"] == "code:ig-kevis")
    assert frow["revenue_cents"] == 4900 and frow["label"] == "Instagram: kevis.marr"
    csv_res = env.client.get("/api/admin/sales/attribution?model=last&format=csv")
    assert csv_res.headers["content-type"].startswith("text/csv")
    import csv as _csv
    import io
    parsed = list(_csv.DictReader(io.StringIO(csv_res.text)))
    assert len(parsed) == len(last["rows"])
    for c, j in zip(parsed, last["rows"]):
        for col in attr.CSV_COLUMNS:
            assert c[col] == ("" if j[col] is None else str(j[col])), col


def test_report_is_admin_only(env):
    _login(env, "someone@example.com")
    assert env.client.get("/api/admin/sales/attribution").status_code == 403
    env.client.cookies.clear()
    assert env.client.get("/api/admin/sales/attribution").status_code == 401


def test_admin_page_is_served(env):
    res = env.client.get("/admin/sales")
    assert res.status_code == 200 and "Sales attribution" in res.text


# --- isolation -----------------------------------------------------------------------------------------------------
def _boom(*a, **kw):
    raise RuntimeError("attribution exploded")


@pytest.mark.parametrize("mode", ["raises", "flag_off", "db_down"])
def test_sign_in_checkout_and_webhooks_still_work_when_attribution_breaks(env, monkeypatch, mode):
    if mode == "raises":
        for name in ("record_touches", "get_touches", "record_from_event", "checkout_metadata", "initial_props"):
            monkeypatch.setattr(attr, name, _boom)
    elif mode == "flag_off":
        monkeypatch.setenv("CR_ATTRIBUTION_ENABLED", "0")
    else:
        monkeypatch.setattr(attr, "initialize", lambda: False)
    monkeypatch.setattr(env.server, "_posthog", SimpleNamespace(set_once=_boom, identify=lambda **kw: None,
                                                                capture=lambda **kw: None))

    code = db.create_verify_code("iso@example.com")
    signin = env.client.post("/api/auth/verify", json={"email": "iso@example.com", "code": code,
                                                       "attribution": {"utm_source": "youtube"}})
    assert signin.status_code == 200 and signin.json()["is_new"] is True
    assert env.events[-1][1] == "signup_completed" and env.events[-1][2]["utm_source"] == "youtube"
    uid = signin.json()["user"]["id"]

    co = env.client.post("/api/billing/checkout", json={"plan": "daily_monthly", "skip_trial": True,
                                                        "attribution": {"last": YT_TOUCH}})
    assert co.status_code == 200 and co.json()["url"]
    sent = env.created[-1]
    if mode == "flag_off":
        assert _attr_tables() == set() and not (_user_cols() & {"first_touch_json", "last_touch_json",
                                                                "first_touch_at"}), "nothing created while off"
    assert sent["metadata"] == {"user_id": str(uid), "plan": "daily_monthly", "skip_trial": "1"}
    assert sent["line_items"] == [{"price": PRICE, "quantity": 1}] and sent["mode"] == "subscription"
    assert "subscription_data" not in sent

    hook = _hook(env, _checkout_event(uid))
    assert hook.status_code == 200 and hook.json() == {"ok": True}
    assert _user(uid)["plan"] == "daily" and _user(uid)["credits"] > 0
    refund = _hook(env, {"id": "evt_r", "type": "charge.refunded", "data": {"object": {
        "id": "ch", "customer": "cus_test", "amount_refunded": 100}}})
    assert refund.status_code == 200 and refund.json() == {"ok": True}
    assert any(e == "subscription_started" for _, e, _ in env.events)
    if mode != "raises":
        assert env.client.get(f"/v/{VID}", follow_redirects=False).status_code == 302


def test_the_webhook_helper_itself_cannot_raise(env, monkeypatch):
    monkeypatch.setattr(attr, "safe_record_sale", _boom)
    user = _paying_user(env)
    assert _hook(env, _checkout_event(user["id"])).json() == {"ok": True}


def test_schema_init_failure_pauses_tracking_instead_of_failing(env, monkeypatch):
    attr._reset_for_tests()
    monkeypatch.setattr(db, "_conn", _boom)
    assert attr.initialize() is False
    attr.safe_sign_in(1, {"utm_source": "x"})
    attr.safe_checkout({"id": 1}, {"last": YT_TOUCH}, {"metadata": {}})
    assert attr.safe_record_sale("invoice.paid", {}, {}) == "unavailable"


# --- client --------------------------------------------------------------------------------------------------------
NODE_SNIPPET = r"""
const fs = require('fs');
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
global.document = { referrer: '' };
const win = { location: { search: '', pathname: '/' } };
global.window = win;
new Function('window', 'document', 'localStorage', fs.readFileSync(process.argv[2], 'utf8'))(win, global.document, global.localStorage);
const T = win.CRTelemetry;
const visit = (search, ref) => { win.location.search = search; global.document.referrer = ref || ''; return T.captureAttributionFromUrl(); };
const out = {};
visit('');                                    out.plain = localStorage.getItem('cr_attr_v1');
visit('?utm_source=youtube&utm_content=AAAAAAAAAAA'); out.a = JSON.parse(localStorage.getItem('cr_attr_v1'));
visit('');                                    out.b = JSON.parse(localStorage.getItem('cr_attr_v1'));
visit('?utm_source=instagram');               out.c = JSON.parse(localStorage.getItem('cr_attr_v1'));
visit('', 'https://www.channelrecipe.com/x');  out.d = JSON.parse(localStorage.getItem('cr_attr_v1'));
visit('?fbclid=abc');                          out.e = JSON.parse(localStorage.getItem('cr_attr_v1'));
visit('', 'https://www.google.com/');          out.f = JSON.parse(localStorage.getItem('cr_attr_v1'));
store['cr_attr_v1'] = JSON.stringify({first: {ref: 'news.ycombinator.com'}, last: {ref: 'news.ycombinator.com'}});
visit('', 'https://www.google.com/');          out.g = JSON.parse(localStorage.getItem('cr_attr_v1'));
out.payload = T.attributionPayload();
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_client_counts_only_real_touches_replaces_last_and_sets_first_once(tmp_path):
    js = Path(__file__).resolve().parents[1] / "webapp" / "static" / "cr-telemetry.js"
    script = tmp_path / "t.js"
    script.write_text(NODE_SNIPPET)
    out = json.loads(subprocess.run(["node", str(script), str(js)], capture_output=True, text=True,
                                    check=True).stdout)
    assert out["plain"] is None, "a plain page view is not a touch"
    assert out["a"]["first"]["utm_content"] == "AAAAAAAAAAA" and out["a"]["last"]["v"] == 2
    assert out["a"]["last"]["captured_at"] > 0
    assert out["b"] == out["a"], "internal navigation does not change anything"
    assert out["c"]["last"]["utm_source"] == "instagram" and "utm_content" not in out["c"]["last"], "replaced"
    assert out["c"]["first"]["utm_source"] == "youtube", "first is set once"
    assert out["d"] == out["c"], "our own domain is not an external referrer"
    assert out["e"]["last"] == {**out["e"]["last"], "fbclid": "abc"} and "utm_source" not in out["e"]["last"]
    assert out["f"] == out["e"], "a referrer-only visit never replaces a tagged last touch"
    assert out["g"]["last"]["ref"] == "www.google.com" and out["g"]["first"]["ref"] == "news.ycombinator.com",         "but it does replace an untagged one"
    p = out["payload"]
    assert p["first"]["ref"] == "news.ycombinator.com" and p["v"] == 2 and p["initial_ref"] == "news.ycombinator.com"


# --- review fixes (PR #166) ----------------------------------------------------------------------------------------
def test_flag_is_off_by_default_and_creates_nothing(env, monkeypatch):
    monkeypatch.delenv("CR_ATTRIBUTION_ENABLED", raising=False)
    attr._reset_for_tests()
    assert attr.enabled() is False and attr.initialize() is False
    res = env.client.get("/r/discord", follow_redirects=False)
    q = {k: v[0] for k, v in parse_qs(urlparse(res.headers["location"]).query).items()}
    assert q == {"utm_source": "discord", "utm_medium": "community", "utm_campaign": "cr_sales",
                 "utm_content": "cr_discord", "utm_term": "discord"}, "seeded links work with the flag off"
    assert env.client.get("/r/nope", follow_redirects=False).headers["location"] == "/"
    assert env.client.get(f"/v/{VID}", follow_redirects=False).status_code == 302
    _login(env, ADMIN)
    assert env.client.get("/api/admin/sales/attribution").status_code == 503
    code = db.create_verify_code("off@example.com")
    assert env.client.post("/api/auth/verify", json={"email": "off@example.com", "code": code,
                                                     "attribution": {"utm_source": "youtube"}}).status_code == 200
    assert _attr_tables() == set(), "no attribution table is created while the flag is off"
    assert not (_user_cols() & {"first_touch_json", "last_touch_json", "first_touch_at"})


def test_a_later_discord_click_does_not_steal_renewal_or_refund_credit(env):
    user = _paying_user(env)                                         # came from the video
    _hook(env, _checkout_event(user["id"]))
    env.client.get("/r/discord", follow_redirects=False)
    attr.record_touches(user["id"], {"last": {"utm_source": "discord", "utm_medium": "community",
                                              "utm_campaign": "cr_sales", "utm_content": "cr_discord",
                                              "utm_term": "discord"}})  # they later signed in from the Discord link
    assert attr.touch_key(attr.get_touches(user["id"])[1]) == "code:discord"
    _hook(env, _invoice_event("evt_r1", "in_2"))
    _hook(env, {"id": "evt_ref", "type": "charge.refunded", "created": 1762700000, "data": {"object": {
        "id": "ch_unknown", "customer": "cus_test", "amount_refunded": 4900, "currency": "usd"}}})
    rows = _sales()
    assert [r["kind"] for r in rows] == ["first_payment", "renewal", "refund"]
    for r in rows:
        assert r["lt_key"] == f"vid:{VID}" and r["ft_vid"] == VID, r


def test_refunds_link_by_charge_or_payment_intent_on_basil_payloads(env):
    user = _paying_user(env)
    _hook(env, _checkout_event(user["id"]))                          # the checkout session carries no charge id
    create = _invoice_event("evt_create", "in_1", reason="subscription_create")
    create["data"]["object"]["payments"] = {"data": [{"payment": {"type": "payment_intent",
                                                                  "payment_intent": "pi_1", "charge": "ch_1"}}]}
    _hook(env, create)
    first = _sales()[0]
    assert (first["charge_id"], first["payment_intent"], first["invoice_id"]) == ("ch_1", "pi_1", "in_1")
    renewal = _invoice_event("evt_r", "in_2")
    renewal["data"]["object"]["payments"] = {"data": [{"payment": {"payment_intent": "pi_2"}}]}
    _hook(env, renewal)
    db.update_user(user["id"], stripe_customer_id="cus_changed")      # only the payment ids can link it now
    _hook(env, {"id": "evt_ref", "type": "charge.refunded", "data": {"object": {   # Basil: no charge.invoice
        "id": "ch_2", "payment_intent": "pi_2", "customer": "cus_test", "amount_refunded": 900}}})
    refund = _sales()[-1]
    assert refund["kind"] == "refund" and refund["user_id"] == user["id"] and refund["invoice_id"] == "in_2"
    assert refund["payment_intent"] == "pi_2" and refund["lt_key"] == f"vid:{VID}"


def test_invoice_first_then_checkout_records_the_payment_once(env):
    user = _paying_user(env)
    create = _invoice_event("evt_create", "in_1", reason="subscription_create")
    create["data"]["object"]["charge"] = "ch_1"
    _hook(env, create)
    _hook(env, _checkout_event(user["id"]))
    rows = _sales()
    assert len(rows) == 1 and rows[0]["kind"] == "first_payment" and rows[0]["charge_id"] == "ch_1"


def test_topups_are_reported_apart_from_subscription_revenue(env):
    user = _paying_user(env)
    _hook(env, {**_checkout_event(user["id"]), "created": __import__("time").time()})
    top = {"id": "evt_top", "type": "checkout.session.completed", "created": __import__("time").time(),
           "data": {"object": {"id": "cs_t", "mode": "payment", "amount_total": 1500, "currency": "usd",
                               "payment_intent": "pi_t", "metadata": {"user_id": str(user["id"]), "topup_credits": "5"}}}}
    db.update_user(user["id"], plan="daily")
    _hook(env, top)
    assert _sales()[-1]["payment_intent"] == "pi_t"
    rep = attr.report(model="last")
    row = next(r for r in rep["rows"] if r["key"] == f"vid:{VID}")
    assert row["revenue_cents"] == 4900 and row["topup_cents"] == 1500 and row["net_revenue_cents"] == 6400
    assert "topup_cents" in attr.CSV_COLUMNS


def test_seeds_update_their_fields_but_keep_active(env):
    attr.initialize()
    with db._conn() as conn:
        conn.execute("UPDATE link_codes SET active = 0, utm_content = 'stale', label = 'old' WHERE code = 'ig-abby'")
    attr._reset_for_tests()
    assert attr.initialize()
    with db._conn() as conn:
        rows = {dict(r)["code"]: dict(r) for r in conn.execute("SELECT * FROM link_codes").fetchall()}
    abby = rows["ig-abby"]
    assert abby["active"] == 0 and abby["utm_content"] == "abby.d_abby"
    assert abby["label"] == "Instagram: abby.d_abby (Kevis)"
    assert all(r["utm_campaign"] == "cr_sales" and r["utm_term"] == c for c, r in rows.items())
    assert (rows["fb-jesse"]["utm_source"], rows["fb-jesse"]["utm_content"]) == ("facebook", "jesse_fb_autopost")
    assert rows["x-kevis"]["utm_content"] == "kevisunfiltered" and rows["tt-glintverse"]["utm_content"] == "glintverse"


def test_an_eleven_character_content_that_is_not_from_a_video_link_is_not_a_video():
    assert attr.touch_vid({"utm_source": "youtube", "utm_medium": "social", "utm_content": "abby.d_abby"}) == ""
    assert attr.touch_vid({"utm_source": "instagram", "utm_medium": "social", "utm_content": "dQw4w9WgXcQ"}) == ""
    assert attr.touch_vid(YT_TOUCH) == VID


def test_sign_in_identify_has_no_initial_props_and_works_without_client_identify(env, monkeypatch):
    calls = {"set": [], "set_once": []}

    class PH7:                                                   # posthog>=6: no identify(), only set / set_once
        def set(self, **kw): calls["set"].append(kw)
        def set_once(self, **kw): calls["set_once"].append(kw)
        def capture(self, *a, **kw): pass
    monkeypatch.setattr(env.server, "_posthog", PH7())
    code = db.create_verify_code("ph@example.com")
    res = env.client.post("/api/auth/verify", json={"email": "ph@example.com", "code": code, "attribution": {
        "utm_source": "x", **{f"initial_{k}": v for k, v in YT_TOUCH.items()}}})
    assert res.status_code == 200
    person = calls["set"][0]["properties"]
    assert person["email"] == "ph@example.com" and person["utm_source"] == "x"
    assert not any(k.startswith("initial_") for k in person), "initial_* only ever go through $set_once"
    assert calls["set_once"][0]["properties"]["initial_utm_content"] == VID


def test_payload_shape_from_the_new_sign_in_callers_keeps_signup_analytics(env):
    code = db.create_verify_code("payload@example.com")
    body = {"utm_source": "youtube", "initial_utm_source": "youtube", "v": 2, "first": YT_TOUCH, "last": YT_TOUCH}
    res = env.client.post("/api/auth/verify", json={"email": "payload@example.com", "code": code, "attribution": body})
    assert res.status_code == 200
    assert env.events[-1][1] == "signup_completed" and env.events[-1][2]["utm_source"] == "youtube"
    assert attr.touch_vid(attr.get_touches(res.json()["user"]["id"])[0]) == VID
