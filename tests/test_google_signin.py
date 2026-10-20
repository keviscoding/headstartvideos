"""Sign in with Google: hidden until GOOGLE_CLIENT_ID is set, one account per verified email (shared with the email
code), and the site's security headers still let Google's button and pop-up work. The token check is mocked."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from webapp import database as db

CLIENT = "1234-test.apps.googleusercontent.com"


def claims(email="maker@gmail.com", **over):
    return {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": "1090", "email": email,
            "email_verified": True, "name": "Maker", **over}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "google.db")
    db._init_db()
    from webapp import server
    events, tokens = [], {}
    monkeypatch.setattr(server, "track", lambda uid, event, props=None: events.append((uid, event, props or {})))

    def check(credential, client_id):
        assert client_id == CLIENT, "the token is checked against our own client id"
        if credential not in tokens:
            raise ValueError("Token used too late / wrong signature")
        return tokens[credential]
    monkeypatch.setattr(server, "_google_claims", check)
    return SimpleNamespace(client=TestClient(server.app), events=events, tokens=tokens, server=server)


def test_without_a_client_id_there_is_no_google_button_or_route(env, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    assert env.client.get("/api/config").json()["google_client_id"] == ""
    assert env.client.post("/api/auth/google", json={"credential": "x"}).status_code == 404


def test_a_new_google_user_signs_up_once_with_the_signup_event(env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT)
    assert env.client.get("/api/config").json()["google_client_id"] == CLIENT
    env.tokens["good"] = claims("New.Maker@Gmail.com")
    res = env.client.post("/api/auth/google", json={"credential": "good", "attribution": {"utm_source": "yt", "x": "no"}})
    assert res.status_code == 200 and res.json()["is_new"] is True
    assert "session=" in res.headers["set-cookie"] and "HttpOnly" in res.headers["set-cookie"]
    assert res.json()["user"]["email"] == "new.maker@gmail.com"
    uid, event, props = env.events[-1]
    assert event == "signup_completed" and props["method"] == "google" and props["utm_source"] == "yt" and "x" not in props
    assert env.client.get("/api/auth/me").json()["user"]["email"] == "new.maker@gmail.com"
    again = env.client.post("/api/auth/google", json={"credential": "good"})
    assert again.json()["is_new"] is False and again.json()["user"]["id"] == uid and env.events[-1][1] == "login"


def test_google_and_the_email_code_reach_the_same_account(env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT)
    code = db.create_verify_code("both@example.com")
    by_code = env.client.post("/api/auth/verify", json={"email": "both@example.com", "code": code}).json()
    assert by_code["is_new"] is True and env.events[-1][2]["method"] == "email"
    env.tokens["g"] = claims("both@example.com")
    by_google = env.client.post("/api/auth/google", json={"credential": "g"}).json()
    assert by_google["is_new"] is False and by_google["user"]["id"] == by_code["user"]["id"]
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM users WHERE email='both@example.com'").fetchone()["n"] == 1


@pytest.mark.parametrize("bad", [claims(email_verified=False), claims(email_verified="false"), claims(email=""),
                                 claims(aud="someone-else"), claims(iss="https://evil.example.com")])
def test_unverified_or_foreign_tokens_are_refused(env, monkeypatch, bad):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT)
    env.tokens["t"] = bad
    assert env.client.post("/api/auth/google", json={"credential": "t"}).status_code == 400
    assert not env.events, "nobody was signed in"


def test_a_forged_or_expired_token_is_refused(env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT)
    res = env.client.post("/api/auth/google", json={"credential": "forged"})
    assert res.status_code == 400 and "session=" not in res.headers.get("set-cookie", "")


def test_the_real_check_verifies_signature_audience_and_issuer_with_google_auth(monkeypatch):
    """_google_claims hands the token to google-auth's verifier with our client id (no network in this test)."""
    from webapp import server
    from google.oauth2 import id_token
    seen = {}
    monkeypatch.setattr(id_token, "verify_oauth2_token",
                        lambda token, request, audience: seen.update(token=token, audience=audience) or claims())
    assert server._google_claims("tok", CLIENT)["email"] == "maker@gmail.com"
    assert seen == {"token": "tok", "audience": CLIENT}


def test_security_headers_stay_and_still_allow_googles_button(env):
    for path in ("/money-niche", "/app"):
        h = env.client.get(path).headers
        assert h["x-frame-options"] == "SAMEORIGIN" and h["content-security-policy"] == "frame-ancestors 'self'"
        assert h["x-content-type-options"] == "nosniff" and "strict-transport-security" in h
        assert h["referrer-policy"] == "strict-origin-when-cross-origin", "Google needs the origin in the referrer"
        assert "cross-origin-opener-policy" not in h, "a same-origin COOP would break Google's pop-up"
        csp = h["content-security-policy"]
        assert "script-src" not in csp and "frame-src" not in csp, "nothing blocks accounts.google.com"
    app_page = env.client.get("/app").text
    assert "/static/google-signin.js?v=" in app_page and 'id="auth-google"' in app_page
