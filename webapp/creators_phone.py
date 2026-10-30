"""Phone verification before a creator's free edits (owner's brief, 2026-10-30; provider picked overnight: Twilio Verify).

One number per account keeps the two free edits fair. Only a keyed hash of the number is stored (creator_phones),
never the number. The provider sits behind a two-call interface (`start` sends a code, `check` approves it), so
another can replace Twilio without touching the routes.

Off until the owner switches it on: CREATORS_PHONE_REQUIRED=1, with TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and
TWILIO_VERIFY_SID (a Verify service, with Fraud Guard and geo-permissions limited to where we sell) on the server.
Our own limits sit on top of Twilio's: a few codes per account and per number a day.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time

import requests

from webapp import creators_store as store
from webapp import database as db

CODES_PER_DAY = 5              # per account
CODES_PER_NUMBER = 3           # per number, across accounts
E164 = re.compile(r"\+[1-9]\d{7,14}")


class PhoneError(Exception):
    """Something the creator can act on (a wrong number, too many tries, a wrong code)."""


class TwilioVerify:
    """Twilio Verify over its REST API: a code by SMS, then the check. Twilio's Fraud Guard and rate limits apply."""
    base = "https://verify.twilio.com/v2/Services"

    def __init__(self, sid: str, token: str, service: str, post=None):
        self.auth, self.service, self.post = (sid, token), service, post or requests.post

    def _call(self, path: str, data: dict) -> dict:
        res = self.post(f"{self.base}/{self.service}/{path}", data=data, auth=self.auth, timeout=(10, 30))
        body = res.json() if res.content else {}
        if res.status_code == 429 or body.get("code") in (60203, 60212):
            raise PhoneError("Too many codes for this number. Try again in a few minutes.")
        if body.get("code") in (60200, 60205, 21211, 21614):
            raise PhoneError("That number can't get a text. Check it, or try a mobile number.")
        if res.status_code == 404 and path.endswith("Check"):
            raise PhoneError("That code has expired. Send a new one.")
        if res.status_code >= 400:
            raise RuntimeError(f"Twilio Verify HTTP {res.status_code} ({body.get('code')})")
        return body

    def start(self, phone: str) -> None:
        self._call("Verifications", {"To": phone, "Channel": "sms"})

    def check(self, phone: str, code: str) -> bool:
        return self._call("VerificationCheck", {"To": phone, "Code": code}).get("status") == "approved"


def provider():
    """The configured provider, or None while phone verification isn't set up."""
    sid, token, service = (os.getenv(k, "").strip() for k in
                           ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SID"))
    return TwilioVerify(sid, token, service) if sid and token and service else None


def normalize(raw: str) -> str:
    """A number as E.164 (+15551234567). Raises PhoneError when it can't be one."""
    s = re.sub(r"[\s().-]", "", str(raw or ""))
    if s.startswith("00"):
        s = "+" + s[2:]
    if not E164.fullmatch(s):
        raise PhoneError("Enter your mobile number with its country code, like +1 415 555 0134.")
    return s


def fingerprint(phone: str) -> str:
    """The keyed hash we keep instead of the number (a plain hash of a phone number is easy to reverse)."""
    base = (os.getenv("CREATORS_PHONE_KEY") or os.getenv("FRONTIER_SECRETS_KEY") or os.getenv("CRON_SECRET") or "")
    key = hashlib.sha256(("cr-creators-phone:" + base).encode()).digest()
    return hmac.new(key, phone.encode(), hashlib.sha256).hexdigest()


def initialize() -> None:
    store.initialize()
    real = "DOUBLE PRECISION" if db.IS_PG else "REAL"
    with db._conn() as conn:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS creator_phone_tries (
            user_id INTEGER NOT NULL, phone_hash TEXT NOT NULL, at {real} NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_creator_phone_tries ON creator_phone_tries(user_id, at)")


def _tries(conn, field: str, value, since: float) -> int:
    row = conn.execute(db._q(f"SELECT COUNT(*) AS n FROM creator_phone_tries WHERE {field}=? AND at>?"),
                       (value, since)).fetchone()
    return int(row["n"] if row else 0)


def start(user_id: int, raw: str, prov=None) -> None:
    """Text a code to this number, unless it's someone else's verified number or it's had enough codes today."""
    prov = prov or provider()
    if prov is None:
        raise PhoneError("Phone checks aren't switched on yet.")
    phone = normalize(raw)
    h = fingerprint(phone)
    initialize()
    since = time.time() - 86400
    with db._conn() as conn:
        owner = conn.execute(db._q("SELECT user_id FROM creator_phones WHERE phone_hash=?"), (h,)).fetchone()
        if owner and int(owner["user_id"]) != int(user_id):
            raise PhoneError("That number is already on another account.")
        if _tries(conn, "user_id", int(user_id), since) >= CODES_PER_DAY or \
                _tries(conn, "phone_hash", h, since) >= CODES_PER_NUMBER:
            raise PhoneError("That's a lot of codes today. Try again tomorrow, or write to hello@channelrecipe.com.")
        conn.execute(db._q("INSERT INTO creator_phone_tries (user_id, phone_hash, at) VALUES (?,?,?)"),
                     (int(user_id), h, time.time()))
    prov.start(phone)


def check(user_id: int, raw: str, code: str, prov=None) -> bool:
    """Approve the code and keep the number's fingerprint on this account. False for a wrong code."""
    prov = prov or provider()
    if prov is None:
        raise PhoneError("Phone checks aren't switched on yet.")
    phone = normalize(raw)
    code = re.sub(r"\D", "", str(code or ""))
    if not 4 <= len(code) <= 10:
        raise PhoneError("Enter the code from the text.")
    if not prov.check(phone, code):
        return False
    h = fingerprint(phone)
    initialize()
    with db._conn() as conn:
        owner = conn.execute(db._q("SELECT user_id FROM creator_phones WHERE phone_hash=?"), (h,)).fetchone()
        if owner and int(owner["user_id"]) != int(user_id):
            raise PhoneError("That number is already on another account.")
        conn.execute(db._q("DELETE FROM creator_phones WHERE user_id=?"), (int(user_id),))
        conn.execute(db._q("INSERT INTO creator_phones (user_id, phone_hash, verified_at) VALUES (?,?,?)"),
                     (int(user_id), h, time.time()))
    return True
