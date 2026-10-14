"""One-click "Connect ChannelRecipe" for the downloaded Studio.

Instead of copying a key, the Studio setup page opens channelrecipe.com, the
creator signs in and presses Approve, and the browser is sent back to the
Studio on the same computer with a short-lived code. The Studio trades that
code for the account's key directly with this server.

The code is HMAC-signed (no database row), expires in five minutes and is
bound to a secret the Studio created (PKCE): only the Studio that started the
request can redeem it, so a copied or logged URL is useless on its own.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
from webapp import database as db
from webapp.frontier_studio import require_studio_user

# The Studio always listens here; the approval never redirects anywhere else.
RETURN_URL = "http://127.0.0.1:7860/cr-connect/return"
CODE_SECONDS = 300
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_STATE = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
_VERIFIER = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
PAGE = Path(__file__).resolve().parent / "static" / "studio-connect.html"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signing_key() -> bytes:
    base = (os.getenv("FRONTIER_SECRETS_KEY", "") or config.SECRETS_KEY or "").strip()
    if not base:
        raise HTTPException(503, "One-click connect isn't available right now. Paste your key instead.")
    return hashlib.sha256(b"cr-studio-connect\0" + base.encode()).digest()


def challenge_for(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


def make_code(user_id: int, challenge: str, *, now: float | None = None) -> str:
    payload = {"u": int(user_id), "c": challenge, "e": int((now or time.time()) + CODE_SECONDS)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    return body + "." + signature


def read_code(code: str, verifier: str, *, now: float | None = None) -> int:
    """The user id a valid, unexpired code was issued to, for this exact Studio."""
    invalid = HTTPException(400, "This connection link expired. Press Connect in Studio again.")
    if not isinstance(code, str) or not isinstance(verifier, str) or not _VERIFIER.fullmatch(verifier):
        raise invalid
    body, _, signature = code.partition(".")
    if not body or not signature or len(code) > 512:
        raise invalid
    expected = _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        raise invalid
    try:
        payload = json.loads(_unb64(body))
    except ValueError:
        raise invalid from None
    if (not isinstance(payload, dict) or int(payload.get("e", 0)) < (now or time.time())
            or not hmac.compare_digest(str(payload.get("c", "")), challenge_for(verifier))):
        raise invalid
    return int(payload["u"])


class ApproveRequest(BaseModel):
    challenge: str
    state: str


class ExchangeRequest(BaseModel):
    code: str
    verifier: str


def make_router(session_user) -> APIRouter:
    router = APIRouter(tags=["ChannelRecipe Studio"])

    @router.get("/studio/connect", include_in_schema=False)
    async def connect_page():
        return FileResponse(str(PAGE), headers={"Cache-Control": "no-store"})

    @router.post("/api/studio/connect/approve")
    async def approve(body: ApproveRequest, request: Request):
        user = require_studio_user(session_user(request))
        if not _CHALLENGE.fullmatch(body.challenge) or not _STATE.fullmatch(body.state):
            raise HTTPException(400, "This connection link is incomplete. Press Connect in Studio again.")
        code = make_code(int(user["id"]), body.challenge)
        return {"redirect": RETURN_URL + "?" + urlencode({"code": code, "state": body.state})}

    @router.post("/api/studio/connect/exchange")
    async def exchange(body: ExchangeRequest):
        user_id = read_code(body.code, body.verifier)
        user = require_studio_user(db.get_user_by_id(user_id))
        return {"key": db.ensure_user_mcp_api_key(int(user["id"]))}

    return router
