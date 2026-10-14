"""One-click Studio connect: approve on channelrecipe.com, redeem only from the asking Studio."""
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from webapp import studio_connect as sc

VERIFIER = "v" * 64
CHALLENGE = sc.challenge_for(VERIFIER)
STATE = "s" * 24


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FRONTIER_SECRETS_KEY", "connect-test-secret")
    users = {7: {"id": 7, "email": "creator@example.com", "plan": "studio_pro"},
             8: {"id": 8, "email": "free@example.com", "plan": "free"}}
    session = {"user": users[7]}
    monkeypatch.setattr(sc.db, "get_user_by_id", lambda uid: users.get(uid))
    monkeypatch.setattr(sc.db, "ensure_user_mcp_api_key", lambda uid: f"cr_mcp_key_for_{uid}")
    app = FastAPI()
    app.include_router(sc.make_router(lambda request: session["user"]))
    return SimpleNamespace(client=TestClient(app), session=session, users=users)


def approve(env, **body):
    return env.client.post("/api/studio/connect/approve", json={"challenge": CHALLENGE, "state": STATE, **body})


def test_approving_sends_the_browser_back_to_this_computer_and_the_studio_gets_the_key(env):
    redirect = approve(env).json()["redirect"]
    assert redirect.startswith("http://127.0.0.1:7860/cr-connect/return?")
    query = parse_qs(urlsplit(redirect).query)
    assert query["state"] == [STATE]
    exchanged = env.client.post("/api/studio/connect/exchange", json={"code": query["code"][0], "verifier": VERIFIER})
    assert exchanged.status_code == 200 and exchanged.json() == {"key": "cr_mcp_key_for_7"}
    assert "cr_mcp_" not in redirect, "the key itself never travels in a URL"


def test_a_code_only_works_for_the_studio_that_started_it(env):
    code = parse_qs(urlsplit(approve(env).json()["redirect"]).query)["code"][0]
    stolen = env.client.post("/api/studio/connect/exchange", json={"code": code, "verifier": "x" * 64})
    assert stolen.status_code == 400


def test_expired_and_tampered_codes_are_refused(env):
    old = sc.make_code(7, CHALLENGE, now=time.time() - sc.CODE_SECONDS - 5)
    with pytest.raises(HTTPException):
        sc.read_code(old, VERIFIER)
    body, _, signature = sc.make_code(7, CHALLENGE).partition(".")
    forged = sc._b64(b'{"u":8,"c":"' + CHALLENGE.encode() + b'","e":9999999999}') + "." + signature
    with pytest.raises(HTTPException):
        sc.read_code(forged, VERIFIER)
    assert sc.read_code(body + "." + signature, VERIFIER) == 7


def test_signing_in_and_a_studio_plan_are_required(env):
    env.session["user"] = None
    assert approve(env).status_code == 401
    env.session["user"] = env.users[8]
    assert approve(env).status_code == 403
    # A plan that lapsed between approving and redeeming gets no key.
    code = sc.make_code(8, CHALLENGE)
    assert env.client.post("/api/studio/connect/exchange", json={"code": code, "verifier": VERIFIER}).status_code == 403


@pytest.mark.parametrize("body", [{"challenge": "short"}, {"state": "bad state!"}])
def test_malformed_requests_are_rejected(env, body):
    assert approve(env, **body).status_code == 400


def test_the_approval_page_is_served(env):
    page = env.client.get("/studio/connect")
    assert page.status_code == 200 and "Approve and connect" in page.text
