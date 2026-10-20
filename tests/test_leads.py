"""The free tools' email list: only with consent, the harness PDF once, quiz leads by their signed-in address, the
same signed unsubscribe links, and the breakout email only when an admin presses send."""
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from webapp import database as db
from webapp import lifecycle_email as le
from webapp import money_niche as mn
from webapp import money_niche_web as web
from tests.test_money_niche import BASE, channel


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "leads.db")
    db._init_db()
    mn._proof_cache.clear()
    web._hits.clear()
    monkeypatch.setenv("FRONTIER_SECRETS_KEY", "lead-test-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    sent = []

    def send(user, kind, subject, page, plain):
        sent.append({"to": user["email"], "id": user["id"], "kind": kind, "subject": subject, "page": page,
                     "plain": plain, "unsubscribe": user.get("unsubscribe")})
        return True
    monkeypatch.setattr(le, "_send", send)
    monkeypatch.setattr(le, "_later", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(le.time, "sleep", lambda s: None)
    from webapp import server
    monkeypatch.setattr(server.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setattr(server, "track", lambda *a, **k: None)
    return SimpleNamespace(client=TestClient(server.app), sent=sent)


def sign_in(env, email):
    user = db.create_user(email)
    env.client.cookies.set("session", db.create_session(user["id"]))
    return user


def test_nothing_is_stored_without_consent(env):
    res = env.client.post("/api/leads", json={"source": "harness", "email": "reader@example.com", "consent": False})
    assert res.status_code == 400 and le.lead_stats()["total"] == 0 and env.sent == []
    assert env.client.post("/api/leads", json={"source": "harness", "email": "nope", "consent": True}).status_code == 400
    assert env.client.post("/api/leads", json={"source": "harness", "email": "x@mailinator.com",
                                               "consent": True}).status_code == 400


def test_a_harness_lead_gets_the_pdf_once_with_its_consent_recorded(env):
    for _ in range(2):
        res = env.client.post("/api/leads", json={"source": "harness", "email": "Reader@Example.com", "consent": True})
        assert res.status_code == 200 and res.json()["pdf"].endswith(".pdf")
    assert [m["kind"] for m in env.sent] == ["harness_pdf"], "the PDF goes once per address"
    mail = env.sent[0]
    assert mail["to"] == "reader@example.com" and "3D-Documentary-Harness.pdf" in mail["page"]
    assert "/email/unsubscribe?l=" in mail["unsubscribe"] and "free tools" in mail["page"]
    with db._conn() as conn:
        lead = dict(conn.execute("SELECT * FROM leads").fetchone())
    assert lead["sources"] == "harness" and lead["consent_text"] == web.CONSENT["harness"] and lead["consent_at"] > 0


def test_the_harness_page_stays_readable_and_offers_the_email_capture(env):
    page = env.client.get("/3d-harness").text
    assert "Get the PDF + the next niche lists by email" in page and 'name="consent"' in page
    assert "The niche list" in page and "Download the PDF" in page, "the page reads the same without signing up"


def test_a_quiz_lead_uses_the_signed_in_address_and_the_result_niche(env):
    rid = env.client.post("/api/money-niche/quiz", json=BASE).json()["id"]
    assert env.client.post("/api/leads", json={"source": "quiz", "consent": True, "result": rid}).status_code == 401
    sign_in(env, "maker@example.com")
    res = env.client.post("/api/leads", json={"source": "quiz", "consent": True, "result": rid,
                                              "email": "someone-else@example.com"})
    assert res.status_code == 200
    with db._conn() as conn:
        lead = dict(conn.execute("SELECT * FROM leads").fetchone())
    assert lead["email"] == "maker@example.com" and lead["niche_id"] == "stick_figures"
    assert env.sent == [], "signing up for the quiz list sends nothing by itself"


def seed_breakouts():
    db.upsert_niche_channels([channel("new1", "Fresh Stick", "stick figure", "Stick figure history")])


def test_breakout_emails_go_only_when_an_admin_presses_send_once_per_issue(env):
    seed_breakouts()
    stick = le.capture_lead("stick@example.com", "quiz", "ok", "stick_figures")
    geo = le.capture_lead("geo@example.com", "quiz", "ok", "geopolitics_maps")
    le.capture_lead("any@example.com", "harness", "ok")
    sign_in(env, "creator@example.com")
    assert env.client.post("/api/admin/leads/breakout-email", json={"issue": "2026-10-20"}).status_code == 403
    assert env.client.get("/api/admin/leads").status_code == 403
    assert env.sent == [], "nothing is ever sent on its own"
    result = le.send_lead_breakouts("2026-10-20", pause_s=0)
    assert result == {"sent": 2, "skipped": 1, "issue": "2026-10-20"}, "the maps lead had no new breakouts: skipped"
    assert {m["to"] for m in env.sent} == {"stick@example.com", "any@example.com"}
    mail = next(m for m in env.sent if m["to"] == "stick@example.com")
    assert mail["subject"] == "New breakout channels in Stick figures on detailed backgrounds"
    assert "Fresh Stick" in mail["page"] and "Partner Program" in mail["page"]
    assert f"l={stick['id']}" in mail["unsubscribe"] and "Unsubscribe from these emails:" in mail["plain"]
    assert le.send_lead_breakouts("2026-10-20", pause_s=0)["sent"] == 0, "an issue goes once"
    assert geo["id"]


def test_the_admin_button_starts_the_send_and_sees_the_list(env, monkeypatch):
    started = []
    monkeypatch.setattr(le, "send_lead_breakouts", lambda issue: started.append(issue) or {})
    le.capture_lead("one@example.com", "harness", "ok")
    sign_in(env, "owner@example.com")
    stats = env.client.get("/api/admin/leads").json()
    assert stats["subscribed"] == 1 and stats["by_source"] == {"quiz": 0, "harness": 1}
    assert env.client.post("/api/admin/leads/breakout-email", json={"issue": "bad issue!"}).status_code == 400
    assert env.client.post("/api/admin/leads/breakout-email", json={"issue": "2026-10-20"}).json()["started"]
    for _ in range(50):
        if started:
            break
        time.sleep(0.02)
    assert started == ["2026-10-20"]


def test_a_lead_unsubscribes_with_its_signed_link_and_is_never_mailed_again(env):
    seed_breakouts()
    lead = le.capture_lead("bye@example.com", "quiz", "ok", "stick_figures")
    assert env.client.get(f"/email/unsubscribe?l={lead['id']}&t=forged").status_code == 400
    ok = env.client.get(f"/email/unsubscribe?l={lead['id']}&t={le.lead_unsubscribe_token(lead['id'])}")
    assert ok.status_code == 200 and "unsubscribed" in ok.text and "niche emails" in ok.text
    assert le.send_lead_breakouts("issue-2", pause_s=0)["sent"] == 0 and env.sent == []
    assert le.lead_stats()["unsubscribed"] == 1
    assert env.client.post(f"/email/unsubscribe?l={lead['id']}&t={le.lead_unsubscribe_token(lead['id'])}").status_code == 200


def test_a_customer_who_unsubscribed_is_not_mailed_as_a_lead_either(env):
    seed_breakouts()
    user = db.create_user("customer@example.com")
    le.capture_lead("customer@example.com", "harness", "ok")
    assert le.opt_out(user["id"], le.unsubscribe_token(user["id"]))
    assert le.send_lead_breakouts("issue-3", pause_s=0)["sent"] == 0 and env.sent == []


def test_lead_and_customer_links_cannot_be_swapped(env):
    assert le.lead_unsubscribe_token(5) != le.unsubscribe_token(5)
    assert not le.opt_out(5, le.lead_unsubscribe_token(5))
    assert not le.lead_opt_out(5, le.unsubscribe_token(5))


def test_sign_ups_are_rate_limited(env):
    codes = [env.client.post("/api/leads", json={"source": "harness", "email": f"r{i}@example.com",
                                                 "consent": True}).status_code for i in range(10)]
    assert codes[:8] == [200] * 8 and codes[8] == 429
