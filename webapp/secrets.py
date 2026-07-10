"""
Encrypt/decrypt per-user secrets (e.g. BYOK HeyGen API keys).

Uses Fernet. Prefer SECRETS_KEY — either a Fernet.generate_key() value or any
passphrase (we derive a key). If unset, derives from STRIPE_WEBHOOK_SECRET /
DATABASE_URL. Set SECRETS_KEY in production.
"""
from __future__ import annotations

import base64
import hashlib
import os


def _fernet():
    from cryptography.fernet import Fernet

    raw = (os.getenv("SECRETS_KEY") or "").strip()
    if raw:
        try:
            return Fernet(raw.encode("utf-8"))
        except Exception:
            digest = hashlib.sha256(raw.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(digest))

    seed = (
        os.getenv("STRIPE_WEBHOOK_SECRET")
        or os.getenv("DATABASE_URL")
        or "channelrecipe-dev-secrets-key-change-me"
    ).encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed).digest()))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def secret_last4(plaintext: str) -> str:
    p = (plaintext or "").strip()
    if len(p) < 4:
        return p
    return p[-4:]
