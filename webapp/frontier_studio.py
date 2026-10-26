"""ChannelRecipe Studio: the downloadable creator studio and its cloud authorization.

Creators run the native workflow on their own Claude Code account, with CR MCP
authorization and owned project records. An API brain is an explicit alternate.
Access follows the account's plan (Creator, Pro, Max and their trials; admins
always) and is checked live on every web, runner and MCP request.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Literal

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

import config
from webapp import milestones
from webapp import studio_failures
from webapp import database as db
from webapp import plans
from cryptography.fernet import Fernet, InvalidToken
from webapp import frontier_providers as providers

VERSION = "5.3-admin.105"
CATALOG = Path(__file__).resolve().parent / "frontier" / "catalog.json"
RELEASE_KEY = "private/frontier/channelrecipe-5.3-admin.105.zip"
# Every release the server still serves (older ones keep running projects alive). Append each new release; never
# remove one, or projects pinned to it stop.
APPROVED_RELEASES = ("5.3-admin.8", "5.3-admin.9", "5.3-admin.10", "5.3-admin.11", "5.3-admin.12",
                "5.3-admin.13", "5.3-admin.14", "5.3-admin.15", "5.3-admin.16", "5.3-admin.17",
                "5.3-admin.18", "5.3-admin.19", "5.3-admin.20", "5.3-admin.21", "5.3-admin.22",
                "5.3-admin.23", "5.3-admin.24", "5.3-admin.25", "5.3-admin.26", "5.3-admin.27", "5.3-admin.28", "5.3-admin.29", "5.3-admin.30", "5.3-admin.31", "5.3-admin.32", "5.3-admin.33", "5.3-admin.34", "5.3-admin.35", "5.3-admin.36", "5.3-admin.38", "5.3-admin.39", "5.3-admin.40", "5.3-admin.41", "5.3-admin.42", "5.3-admin.43", "5.3-admin.44", "5.3-admin.45", "5.3-admin.46", "5.3-admin.47", "5.3-admin.48", "5.3-admin.49", "5.3-admin.50", "5.3-admin.51", "5.3-admin.52", "5.3-admin.53", "5.3-admin.54", "5.3-admin.55", "5.3-admin.56", "5.3-admin.57", "5.3-admin.58", "5.3-admin.59", "5.3-admin.60", "5.3-admin.61", "5.3-admin.62", "5.3-admin.63", "5.3-admin.64", "5.3-admin.65", "5.3-admin.66", "5.3-admin.67", "5.3-admin.68", "5.3-admin.69", "5.3-admin.70", "5.3-admin.71", "5.3-admin.72", "5.3-admin.73", "5.3-admin.74", "5.3-admin.75", "5.3-admin.76", "5.3-admin.77", "5.3-admin.78", "5.3-admin.79", "5.3-admin.80", "5.3-admin.81", "5.3-admin.82", "5.3-admin.83", "5.3-admin.84", "5.3-admin.85", "5.3-admin.86", "5.3-admin.87", "5.3-admin.88", "5.3-admin.89", "5.3-admin.90", "5.3-admin.91", "5.3-admin.92", "5.3-admin.93", "5.3-admin.94", "5.3-admin.95", "5.3-admin.96", "5.3-admin.97", "5.3-admin.98", "5.3-admin.99", "5.3-admin.100", "5.3-admin.101", "5.3-admin.102", "5.3-admin.103", "5.3-admin.104", "5.3-admin.105")
# Releases that may also START or RESUME a video: every release from MIN_SUPPORTED_RELEASE to the newest. A new release
# never forces an update (2026-10-24: with "the newest two", five releases in one night made creators stop their work
# to update, twice). Raise the minimum only on purpose, when older Studios can no longer work with this server, and say
# why here. The project pins the release it started on.
MIN_SUPPORTED_RELEASE = "5.3-admin.80"           # 2026-10-21: every release since Studio Cloud's farm fixes
SUPPORTED_RELEASES = APPROVED_RELEASES[APPROVED_RELEASES.index(MIN_SUPPORTED_RELEASE):]
WORKFLOW_VERSION = "frontier-5.3-claudecode.1"
BRANDED_WORKFLOW_VERSION = "channelrecipe-5.3-claudecode.1"
SOURCE_SHA256 = "e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf"
NATIVE_STAGE_LIMIT = 2
# A Studio update reads the release in many byte ranges with one link, and storage is in San Francisco: from Europe
# a slow connection outlived a 10-minute link and every retry was refused ("the update keeps getting interrupted").
DOWNLOAD_LINK_SECONDS = 3600
# A Studio asks for a download link again only when the last one failed. Asked this often within ten minutes, its
# computer can't fetch storage links (a creator's internet provider reset every connection to the storage host while
# this site worked), so for a day that account's links stream through this site instead. Studio 92 switches by itself;
# earlier Studios did only when the storage host's name didn't resolve.
SITE_LINK_ASKS, SITE_LINK_WINDOW, SITE_LINK_DAYS = 3, 600, 1
_link_asks: dict = {}
_link_lock = threading.Lock()


def storage_blocked(user_id: int, what: str, now: float | None = None) -> bool:
    """Whether this account's link for `what` ("pack" or "release") should stream through this site; counts this ask."""
    now = time.time() if now is None else now
    with _link_lock:
        entry = _link_asks.setdefault(int(user_id), {"until": 0.0})
        asks = [t for t in entry.get(what, []) if now - t < SITE_LINK_WINDOW][-10:] + [now]
        entry[what] = asks
        if len(asks) >= SITE_LINK_ASKS:
            entry["until"] = now + SITE_LINK_DAYS * 86400
        if len(_link_asks) > 5000:                      # forget accounts with nothing recent
            for uid in [u for u, e in _link_asks.items() if e["until"] < now
                        and all(now - t >= SITE_LINK_WINDOW for k, v in e.items() if k != "until" for t in v)]:
                _link_asks.pop(uid, None)
        return entry["until"] > now


def _stream_key() -> bytes | None:
    base = (os.getenv("FRONTIER_SECRETS_KEY", "") or config.SECRETS_KEY or "").strip()
    return hashlib.sha256(b"cr-studio-stream\0" + base.encode()).digest() if base else None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def stream_token(user_id: int, what: str, now: float | None = None) -> str | None:
    """A signed, hour-long link token for one account and one file; None when the server has no signing secret."""
    key = _stream_key()
    if not key:
        return None
    body = _b64(json.dumps({"u": int(user_id), "w": what, "e": int((time.time() if now is None else now)
                                                                     + DOWNLOAD_LINK_SECONDS)},
                           separators=(",", ":")).encode())
    return body + "." + _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def read_stream_token(token: str, what: str, now: float | None = None) -> int | None:
    """The account a valid, unexpired token for `what` was issued to, or None."""
    key = _stream_key()
    body, _, signature = str(token or "").partition(".")
    if not key or not body or not signature or len(token) > 400:
        return None
    if not hmac.compare_digest(_b64(hmac.new(key, body.encode(), hashlib.sha256).digest()), signature):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload.get("w") != what or int(payload.get("e", 0)) < (time.time() if now is None else now):
            return None
        return int(payload["u"])
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
# The Shorts asset pack (people, animals, motion clips, the prop and creature libraries, sound libraries, the 2D host):
# a Studio downloads it the first time someone makes a Short (local_runtime/cr_shorts.py ensure_pack). A private
# object; each entitled request gets a short-lived signed link. Built by shorts-clone tools/pack_shorts.py.
SHORTS_PACK_KEY = "private/frontier/shorts-assets-675889f34246.zip"
GEMINI_MODEL = "gemini-3.8-flash"
# Model requests in flight: fair per person, bounded for the server. Each one
# waits on its provider, so a few at a time per creator is plenty.
BRAIN_PER_USER = 3
BRAIN_TOTAL = 12
_brain_lock = threading.Lock()
_brain_active: dict = {}
# DigitalOcean cuts a request after 100 s. A creator's Studio asks again for a
# request still being written; the server waits this long for it each time.
RUNNING_WAIT_S = 75
# A request "running" longer than any answer can take was cut off (e.g. a deploy).
RUNNING_STALE_S = 900


def _take_slot(user_id) -> bool:
    with _brain_lock:
        if sum(_brain_active.values()) >= BRAIN_TOTAL or _brain_active.get(user_id, 0) >= BRAIN_PER_USER:
            return False
        _brain_active[user_id] = _brain_active.get(user_id, 0) + 1
        return True


def _give_slot(user_id):
    with _brain_lock:
        left = _brain_active.get(user_id, 0) - 1
        if left > 0:
            _brain_active[user_id] = left
        else:
            _brain_active.pop(user_id, None)
_schema_lock = threading.Lock()
_schema_ready = set()


def _vault():
    # Dedicated key: enabling this feature must not rotate existing HeyGen secrets.
    raw = os.getenv("FRONTIER_SECRETS_KEY", "").strip()
    if not raw:
        raise HTTPException(503, "The studio's secure account storage is being configured. Try again shortly.")
    try:
        return Fernet(raw.encode())
    except ValueError:
        raise HTTPException(503, "The studio's secure account storage needs attention.")


def encrypt_secret(value):
    return _vault().encrypt(value.encode()).decode()


def decrypt_secret(value):
    try:
        return _vault().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


UPGRADE_URL = "https://channelrecipe.com/app#billing"
STUDIO_UPGRADE_MESSAGE = ("ChannelRecipe Studio is included in the Creator, Pro and Max plans. "
                          "Choose a plan at " + UPGRADE_URL + " to download and use it.")
# Style studies begin with this title; the allowance counts them per account.
STYLE_STUDY_PREFIX = "Style study: "


def is_admin(user: dict | None) -> bool:
    admins = {str(e).strip().lower() for e in getattr(config, "ADMIN_EMAILS", []) if e}
    return str((user or {}).get("email", "")).strip().lower() in admins


def require_studio_user(user: dict | None) -> dict:
    """Signed in with a plan that includes Studio. The plan is the live database row."""
    if not user:
        raise HTTPException(401, "Sign in to connect your studio.")
    if not plans.has_studio(user.get("plan"), is_admin=is_admin(user)):
        raise HTTPException(403, STUDIO_UPGRADE_MESSAGE)
    return user


def style_clone_usage(user: dict) -> dict:
    """Style studies this account has started or kept, against its plan limit.

    Stopped studies that failed outright don't use up the allowance, so a
    creator can try again with the same reference.
    """
    initialize()
    with db._conn() as conn:
        grant = conn.execute(db._q("SELECT extra, counted_from FROM style_clone_grants WHERE user_id=?"),
                             (user["id"],)).fetchone()
        extra, since = (int(grant["extra"]), float(grant["counted_from"])) if grant else (0, 0.0)
        used = conn.execute(db._q("""SELECT COUNT(*) AS n FROM frontier_projects
            WHERE user_id=? AND title LIKE ? AND status != 'failed' AND created_at > ?"""),
            (user["id"], STYLE_STUDY_PREFIX + "%", since)).fetchone()["n"]
    limit = plans.style_clone_limit(user.get("plan"), is_admin=is_admin(user))
    if limit is not None:
        limit += extra
    upgrade = plans.studio_upgrade_tier(user.get("plan"), is_admin=is_admin(user))
    return {"used": int(used), "limit": limit,
            "remaining": None if limit is None else max(0, limit - int(used)),
            "upgrade_tier": upgrade, "upgrade_label": plans.TIERS[upgrade].label if upgrade else "",
            "upgrade_url": UPGRADE_URL}


def grant_style_clones(admin: dict, email: str, extra: int, reset: bool, note: str = "") -> dict:
    """Support: give one account extra style clones and/or restart its count from now. Admins only."""
    if not is_admin(admin):
        raise HTTPException(403, "Only ChannelRecipe's team can change style clone allowances.")
    target = db.get_user_by_email((email or "").strip().lower())
    if not target:
        raise HTTPException(404, "No account with that email.")
    initialize()
    now = time.time()
    with db._conn() as conn:
        old = conn.execute(db._q("SELECT counted_from FROM style_clone_grants WHERE user_id=?"),
                           (target["id"],)).fetchone()
        since = now if reset else (float(old["counted_from"]) if old else 0.0)
        conn.execute(db._q("DELETE FROM style_clone_grants WHERE user_id=?"), (target["id"],))
        conn.execute(db._q("""INSERT INTO style_clone_grants (user_id, extra, counted_from, note, updated_at)
            VALUES (?,?,?,?,?)"""), (target["id"], max(0, min(100, int(extra))), since,
                                     f"{note.strip()[:200]} (by {admin.get('email')})".strip(), now))
    return {"email": target["email"], **style_clone_usage(target)}


class StyleCloneGrant(BaseModel):
    email: str
    extra: int = 0
    reset: bool = False
    note: str = ""


def _require_style_clone_allowance(user: dict) -> None:
    usage = style_clone_usage(user)
    if usage["limit"] is not None and usage["used"] >= usage["limit"]:
        plan = plans.label(user.get("plan"))
        more = (f" Upgrade to {usage['upgrade_label']} at {UPGRADE_URL} to clone more channels."
                if usage["upgrade_label"] else "")
        raise HTTPException(402, f"Your {plan} plan includes {usage['limit']} style "
                                 f"clone{'s' if usage['limit'] != 1 else ''}, and you've used "
                                 f"{usage['used']}.{more}")


def initialize():
    # Dedicated additive tables; no changes to existing Stripe or render schemas.
    schema_key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if schema_key in _schema_ready:
        return
    with _schema_lock:
        if schema_key in _schema_ready:
            return
        with db._conn() as conn:
            if db.IS_PG:
                # Serialize additive preview migrations across web processes/replicas.
                conn.execute("SELECT pg_advisory_xact_lock(726346119)")
            conn.execute("""CREATE TABLE IF NOT EXISTS frontier_credentials (
                user_id INTEGER PRIMARY KEY, key_enc TEXT NOT NULL, model TEXT NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL)""")
            if db.IS_PG:
                conn.execute("ALTER TABLE frontier_credentials ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'gemini'")
            elif "provider" not in {r["name"] for r in conn.execute("PRAGMA table_info(frontier_credentials)").fetchall()}:
                conn.execute("ALTER TABLE frontier_credentials ADD COLUMN provider TEXT NOT NULL DEFAULT 'gemini'")
            if db.IS_PG:
                conn.execute("ALTER TABLE frontier_credentials ADD COLUMN IF NOT EXISTS visual_model TEXT NOT NULL DEFAULT ''")
            elif "visual_model" not in {r["name"] for r in conn.execute("PRAGMA table_info(frontier_credentials)").fetchall()}:
                conn.execute("ALTER TABLE frontier_credentials ADD COLUMN visual_model TEXT NOT NULL DEFAULT ''")
            conn.execute("""CREATE TABLE IF NOT EXISTS frontier_projects (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
                style TEXT NOT NULL, version TEXT NOT NULL, max_calls INTEGER NOT NULL,
                calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ready',
                created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS frontier_requests (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '',
                created_at DOUBLE PRECISION NOT NULL)""")
            additions = {"attempts": "INTEGER NOT NULL DEFAULT 1", "error_json": "TEXT NOT NULL DEFAULT ''",
                         "context_json": "TEXT NOT NULL DEFAULT ''",
                         "started_at": "DOUBLE PRECISION NOT NULL DEFAULT 0"}
            columns = set() if db.IS_PG else {r["name"] for r in conn.execute("PRAGMA table_info(frontier_requests)").fetchall()}
            for name, definition in additions.items():
                if db.IS_PG:
                    conn.execute(f"ALTER TABLE frontier_requests ADD COLUMN IF NOT EXISTS {name} {definition}")
                elif name not in columns:
                    conn.execute(f"ALTER TABLE frontier_requests ADD COLUMN {name} {definition}")
            conn.execute("""CREATE TABLE IF NOT EXISTS frontier_retries (
                request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id INTEGER NOT NULL,
                attempt INTEGER NOT NULL, account_hash TEXT NOT NULL,
                approved_at DOUBLE PRECISION NOT NULL, used_at DOUBLE PRECISION NOT NULL DEFAULT 0)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_frontier_requests_project ON frontier_requests(project_id,status,created_at)")
            conn.execute("""CREATE TABLE IF NOT EXISTS frontier_native_sessions (
                project_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, begin_id TEXT NOT NULL,
                begin_fingerprint TEXT NOT NULL, workflow_json TEXT NOT NULL, setup_json TEXT NOT NULL,
                spec_json TEXT NOT NULL, artifacts_json TEXT NOT NULL DEFAULT '[]',
                error_code TEXT NOT NULL DEFAULT '', created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL, UNIQUE(user_id,begin_id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS frontier_native_stages (
                project_id TEXT NOT NULL, stage_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                input_fingerprint TEXT NOT NULL, kind TEXT NOT NULL, model_role TEXT NOT NULL,
                selected_model TEXT NOT NULL, status TEXT NOT NULL, authorization_hash TEXT NOT NULL,
                result_fingerprint TEXT NOT NULL DEFAULT '', usage_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT NOT NULL DEFAULT '', created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(project_id,stage_id),
                UNIQUE(project_id,input_fingerprint,kind,model_role))""")
            if db.IS_PG:
                conn.execute("ALTER TABLE frontier_native_stages ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 1")
            elif "attempts" not in {r["name"] for r in conn.execute("PRAGMA table_info(frontier_native_stages)").fetchall()}:
                conn.execute("ALTER TABLE frontier_native_stages ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")
            # an admin's extra style clones for one account (support), and the time its count restarts from
            conn.execute("""CREATE TABLE IF NOT EXISTS style_clone_grants (
                user_id INTEGER PRIMARY KEY, extra INTEGER NOT NULL DEFAULT 0,
                counted_from DOUBLE PRECISION NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
                updated_at DOUBLE PRECISION NOT NULL)""")
        _schema_ready.add(schema_key)


def catalog() -> list[dict]:
    return json.loads(CATALOG.read_text())


def credential(user_id: int) -> dict | None:
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM frontier_credentials WHERE user_id=?"), (user_id,)).fetchone()
    return dict(row) if row else None


CHANGELOG = Path(__file__).resolve().parent / "frontier" / "changelog.json"


def changelog(limit: int = 12) -> list[dict]:
    """What's new in recent Studio releases, newest first (shown before updating)."""
    try:
        rows = json.loads(CHANGELOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("build"), int)][:limit]


VOICE_SAMPLES = Path(__file__).resolve().parent / "static" / "voices" / "manifest.json"


def voice_samples() -> dict:
    """Hosted narration samples by model and voice name, so previews are instant and free."""
    try:
        data = json.loads(VOICE_SAMPLES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {model: {name: url for name, url in voices.items() if isinstance(url, str) and url.startswith("https://")}
            for model, voices in data.items() if isinstance(voices, dict)} if isinstance(data, dict) else {}


def status(user: dict) -> dict:
    require_studio_user(user)
    c = credential(user["id"])
    with db._conn() as conn:
        recovery = conn.execute(db._q("""SELECT COUNT(*) AS total FROM frontier_requests r
            JOIN frontier_projects p ON p.id=r.project_id WHERE p.user_id=? AND r.status='needs_review'"""),
            (user["id"],)).fetchone()["total"]
        recovery += conn.execute(db._q("""SELECT COUNT(*) AS total FROM frontier_native_stages s
            JOIN frontier_projects p ON p.id=s.project_id WHERE p.user_id=? AND s.status='failed'"""),
            (user["id"],)).fetchone()["total"]
    return {"access": "admin" if is_admin(user) else "subscriber", "version": VERSION,
            "plan": plans.tier_of(user.get("plan")), "plan_label": plans.label(user.get("plan")),
            "style_clones": style_clone_usage(user),
            "brain": {"connected": bool(c and decrypt_secret(c["key_enc"])),
                      "provider": c["provider"] if c else "anthropic",
                      "model": c["model"] if c else providers.PROVIDERS["anthropic"]["default_brain"],
                      "visual_model": (c.get("visual_model") or c["model"]) if c else providers.PROVIDERS["anthropic"]["default_visual_brain"]},
            "providers": providers.PROVIDERS,
            "recovery_requests": recovery,
            "download_ready": release_ready(), "styles": catalog(),
            "local_url": "http://127.0.0.1:7860/cr-setup",
            "generation_billing": "Your provider accounts", "rendering": "Your computer",
            "changelog": changelog(), "voice_samples": voice_samples(),
            "milestones": milestones.of(user["id"]),
            "production_workflow": workflow_contract(),
            # the releases that may start a new video (the Studio's readiness accepts any of them)
            "supported_versions": list(SUPPORTED_RELEASES)}


def release_ready() -> bool:
    path = os.getenv("FRONTIER_RELEASE_PATH", "")
    if path:
        return Path(path).is_file()
    from webapp import storage
    if not storage.is_remote():
        return False
    # Metadata check only; cache head results instead of downloading the bundle.
    now = time.monotonic()
    previous = getattr(release_ready, "_cached", (0, False))
    if now - previous[0] < 60:
        return previous[1]
    try:
        storage._get_client().head_object(Bucket=storage._spaces_creds()[2], Key=RELEASE_KEY)
        found = True
    except Exception:
        found = False
    release_ready._cached = (now, found)
    return found


class BrainKey(BaseModel):
    api_key: str = Field(min_length=16, max_length=512)
    provider: Literal["gemini", "kie", "atlas", "anthropic"] = "gemini"
    model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9./_\-]+$", max_length=100)
    visual_model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9./_\-]+$", max_length=100)


def _supported_model(provider, model):
    if provider == "gemini":
        import re
        return bool(re.fullmatch(r"gemini-[a-zA-Z0-9.\-]+", model))
    return model in {m["id"] for m in providers.PROVIDERS[provider]["brain_models"]}


def save_key(user: dict, req: BrainKey) -> dict:
    require_studio_user(user)
    _vault()  # Fail before any provider lookup if secure storage is unavailable.
    key = req.api_key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(400, "Paste the complete key without spaces.")
    info = providers.PROVIDERS[req.provider]
    model = req.model or info["default_brain"]
    # An omitted specialist choice inherits the explicitly selected general
    # model. Only the setup form can opt into a more expensive specialist.
    visual_model = req.visual_model or model
    if not all(_supported_model(req.provider, selected) for selected in (model, visual_model)):
        raise HTTPException(400, "Choose one of this account's supported brain models.")
    # Read-only account/model lookup. Never generates content or spends tokens.
    providers.check_key(req.provider, key, model)
    if visual_model != model:
        providers.check_key(req.provider, key, visual_model)
    initialize()
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO frontier_credentials (user_id,key_enc,model,updated_at,provider,visual_model)
            VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            key_enc=excluded.key_enc,model=excluded.model,updated_at=excluded.updated_at,
            provider=excluded.provider,visual_model=excluded.visual_model"""),
            (user["id"], encrypt_secret(key), model, time.time(), req.provider, visual_model))
    return {"connected": True, "provider": req.provider, "model": model, "visual_model": visual_model,
            "message": f"Connected. Brain calls use your {info['label']} account."}


class ProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    style: str = Field(default="jung", min_length=1, max_length=80, pattern=r"^[a-z0-9_\-]+$")
    max_calls: int = Field(default=120, ge=1, le=500)


def create_project(user: dict, req: ProjectRequest) -> dict:
    require_studio_user(user)
    if not credential(user["id"]):
        raise HTTPException(409, "Connect a model account in Local studio first.")
    project_id = "crv_" + secrets.token_hex(12)
    now = time.time()
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO frontier_projects
            (id,user_id,title,style,version,max_calls,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)"""),
            (project_id, user["id"], req.title.strip(), req.style, VERSION, req.max_calls, now, now))
    return project(user, project_id)


def project(user: dict, project_id: str) -> dict:
    require_studio_user(user)
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM frontier_projects WHERE id=? AND user_id=?"),
                           (project_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "This studio project was not found in your account.")
    result = dict(row)
    result["receipt_note"] = "Provider-reported brain tokens. Media charges remain in your local provider accounts."
    return result


class BrainRequest(BaseModel):
    project_id: str = Field(pattern=r"^crv_[a-f0-9]{24}$")
    request_id: str = Field(min_length=12, max_length=80, pattern=r"^[a-zA-Z0-9_\-]+$")
    kind: Literal["text", "vision", "research"] = "text"
    prompt: str = Field(min_length=1, max_length=150000)
    max_tokens: int = Field(default=8000, ge=128, le=32768)
    images: list[str] = Field(default_factory=list, max_length=8)
    model_role: Literal["general", "visual"] = "general"
    requested_model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9./_\-]+$", max_length=100)


def _account_hash(c, key):
    material = c["provider"] + "\0" + c["model"] + "\0" + key
    if c.get("visual_model") and c["visual_model"] != c["model"]:
        material += "\0" + c["visual_model"]
    return hashlib.sha256(material.encode()).hexdigest()


def _errors(raw):
    try:
        items = json.loads(raw)
        return items if isinstance(items, list) else []
    except (ValueError, TypeError):
        return []


def request_history(user, project_id):
    p = project(user, project_id)
    with db._conn() as conn:
        rows = conn.execute(db._q("""SELECT id,status,attempts,error_json,context_json,created_at
            FROM frontier_requests WHERE project_id=?
            ORDER BY CASE WHEN status IN ('needs_review','retry_allowed') THEN 0 ELSE 1 END,created_at DESC LIMIT 100"""), (project_id,)).fetchall()
    result = []
    for row in rows:
        errors = _errors(row["error_json"])
        context = json.loads(row["context_json"] or "{}")
        result.append({"id": row["id"], "status": row["status"], "attempts": row["attempts"],
                       "created_at": row["created_at"], "context": context, "errors": errors,
                       "can_retry": row["status"] == "needs_review" and row["attempts"] == 1 and p["calls"] < p["max_calls"]})
    return {"requests": result, "project_id": project_id}


class RetryApproval(BaseModel):
    expected_attempt: int = Field(ge=1, le=2)
    acknowledge_costs: bool = Field(default=False, strict=True)


def approve_retry(user, project_id, request_id, req):
    require_studio_user(user)
    project(user, project_id)
    if req.acknowledge_costs is not True:
        raise HTTPException(400, "Confirm that your provider may charge for this retry.")
    c = credential(user["id"])
    key = decrypt_secret(c["key_enc"]) if c else ""
    if not key:
        raise HTTPException(409, "Connect your model account before allowing a retry.")
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.IS_PG else ""
        p = conn.execute(db._q("SELECT * FROM frontier_projects WHERE id=? AND user_id=?") + lock,
                         (project_id, user["id"])).fetchone()
        row = conn.execute(db._q("SELECT * FROM frontier_requests WHERE id=? AND project_id=?"), (request_id, project_id)).fetchone()
        if not p or not row:
            raise HTTPException(404, "This request was not found in your account.")
        if row["attempts"] != req.expected_attempt:
            raise HTTPException(409, "This request changed. Refresh its status before continuing.")
        if row["status"] == "retry_allowed":
            return {"allowed": True, "message": "One retry is already allowed. Resume the same video in your local studio."}
        if row["status"] != "needs_review" or row["attempts"] != 1:
            raise HTTPException(409, "Only a stopped request can receive one recovery retry.")
        if p["calls"] >= p["max_calls"]:
            raise HTTPException(409, "This project reached its request limit. Its budget has not been increased.")
        conn.execute(db._q("""INSERT INTO frontier_retries
            (request_id,project_id,user_id,attempt,account_hash,approved_at) VALUES (?,?,?,?,?,?)"""),
            (request_id, project_id, user["id"], row["attempts"], _account_hash(c, key), time.time()))
        conn.execute(db._q("UPDATE frontier_requests SET status='retry_allowed' WHERE id=?"), (request_id,))
    return {"allowed": True, "message": "One retry is allowed. Resume the same video in your local studio; your saved work will be reused."}


def _record_failure(req, attempt, error):
    if isinstance(error, providers.ProviderFailure):
        failure = dict(error.diagnostics)
    else:
        failure = {"code": "internal_error", "message": "CR could not save a complete result for this request.",
                   "error_type": type(error).__name__}
    failure.update(attempt=attempt, recorded_at=time.time())
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT error_json FROM frontier_requests WHERE id=?"), (req.request_id,)).fetchone()
        errors = _errors(row["error_json"]) if row else []
        changed = conn.execute(db._q("""UPDATE frontier_requests SET status='needs_review',error_json=?
            WHERE id=? AND project_id=? AND status='running' AND attempts=?"""),
            (json.dumps(errors + [failure]), req.request_id, req.project_id, attempt)).rowcount
        if changed:
            conn.execute(db._q("""UPDATE frontier_projects SET status='needs_review',updated_at=?,
                input_tokens=input_tokens+?,output_tokens=output_tokens+? WHERE id=?"""),
                (time.time(), failure.get("input_tokens", 0), failure.get("output_tokens", 0), req.project_id))


def _release_busy(req, attempt, retried):
    with db._conn() as conn:
        if retried:
            conn.execute(db._q("""UPDATE frontier_requests SET status='retry_allowed',attempts=?
                WHERE id=? AND project_id=? AND status='running' AND attempts=?"""),
                (attempt - 1, req.request_id, req.project_id, attempt))
            conn.execute(db._q("UPDATE frontier_retries SET used_at=NULL WHERE request_id=?"), (req.request_id,))
        else:
            conn.execute(db._q("DELETE FROM frontier_requests WHERE id=? AND project_id=? AND status='running' AND attempts=1"),
                         (req.request_id, req.project_id))
        conn.execute(db._q("""UPDATE frontier_projects SET calls=CASE WHEN calls>0 THEN calls-1 ELSE 0 END,
            status=CASE WHEN EXISTS (SELECT 1 FROM frontier_requests WHERE project_id=? AND status='needs_review')
            THEN 'needs_review' ELSE 'ready' END,updated_at=? WHERE id=?"""),
            (req.project_id, time.time(), req.project_id))


def _collect_running(user, req, fingerprint, legacy_fingerprint):
    """Asked again for a request still being written (the first connection was cut at
    100 s): wait for it and hand back the saved answer. Never starts a second request."""
    deadline = time.monotonic() + RUNNING_WAIT_S
    while True:
        with db._conn() as conn:
            row = conn.execute(db._q("""SELECT r.* FROM frontier_requests r JOIN frontier_projects p ON p.id=r.project_id
                WHERE r.id=? AND r.project_id=? AND p.user_id=?"""), (req.request_id, req.project_id, user["id"])).fetchone()
        if row is None or row["fingerprint"] not in (fingerprint, legacy_fingerprint) or row["status"] != "running":
            return None  # the normal path answers: new, complete, stopped or someone else's
        started = float(row["started_at"] or row["created_at"] or 0)
        if time.time() - started > RUNNING_STALE_S:
            with db._conn() as conn:
                conn.execute(db._q("UPDATE frontier_requests SET status='needs_review' WHERE id=? AND status='running'"),
                             (req.request_id,))
            return None
        if time.monotonic() >= deadline:
            raise HTTPException(409, "This request is still being written. Studio will collect it when it's ready.",
                                headers={"Retry-After": "5", "X-CR-Request-State": "running"})
        time.sleep(2)


def generate(user: dict, req: BrainRequest) -> dict:
    """Compatibility relay for owners only. Never expose this tool to paid users."""
    require_studio_user(user)
    project(user, req.project_id)
    c = credential(user["id"])
    key = decrypt_secret(c["key_enc"]) if c else ""
    if not key:
        raise HTTPException(409, "Reconnect your model account in Local studio.")
    model = (c.get("visual_model") or c["model"]) if req.model_role == "visual" else c["model"]
    parts = [{"text": req.prompt}]
    size = 0
    for data in req.images:
        if len(data) > 8_000_004:
            raise HTTPException(413, "Use PNG review samples, up to 6 MB total.")
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(400, "A review image could not be read.")
        size += len(raw)
        if size > 6_000_000 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(413, "Use PNG review samples, up to 6 MB total.")
        parts.append({"inlineData": {"mimeType": "image/png", "data": data}})
    fingerprint = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
    legacy_fingerprint = hashlib.sha256(req.model_dump_json(exclude={"model_role", "requested_model"}).encode()).hexdigest()
    collected = _collect_running(user, req, fingerprint, legacy_fingerprint)
    if collected is not None:
        return collected
    if not _take_slot(user["id"]):
        raise HTTPException(429, "The brain is busy. Your local runner will retry shortly.", headers={"Retry-After": "5"})
    reserved = False
    retried = False
    attempt = 1
    context = json.dumps({"provider": c["provider"], "model": model, "model_role": req.model_role,
                          "requested_model": req.requested_model, "kind": req.kind, "max_tokens": req.max_tokens,
                          "prompt_chars": len(req.prompt), "image_count": len(req.images), "image_bytes": size,
                          "thinking_level": "low" if not providers.is_claude(model) and
                          (c["provider"] == "kie" or providers.thinking_config(model)) else "model_default"})
    try:
        with db._conn() as conn:
            # A row lock makes the per-project limit safe across web replicas.
            lock = " FOR UPDATE" if db.IS_PG else ""
            if not db.IS_PG:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(db._q("SELECT * FROM frontier_projects WHERE id=? AND user_id=?") + lock,
                               (req.project_id, user["id"])).fetchone()
            if not row:
                raise HTTPException(404, "Project not found.")
            existing = conn.execute(db._q("SELECT * FROM frontier_requests WHERE id=?"), (req.request_id,)).fetchone()
            if existing:
                # Keep existing interrupted admin.6 requests recoverable. A
                # model-profile upgrade must never create a fresh paid attempt
                # for an old request merely because routing metadata was added.
                old_context = json.loads(existing["context_json"] or "{}")
                matches = existing["fingerprint"] == fingerprint or (
                    "model_role" not in old_context and existing["fingerprint"] == legacy_fingerprint)
                if existing["project_id"] != req.project_id or not matches:
                    raise HTTPException(409, "This request ID belongs to different work.")
                if existing["status"] == "complete":
                    return json.loads(existing["result_json"])
                if existing["status"] != "retry_allowed":
                    raise HTTPException(409, "This request stopped or is still running. Open Local studio in CR to review its status and recovery options.")
                approval = conn.execute(db._q("SELECT * FROM frontier_retries WHERE request_id=?"), (req.request_id,)).fetchone()
                if not approval or approval["used_at"] or approval["attempt"] != existing["attempts"]:
                    raise HTTPException(409, "This request has no unused recovery retry.")
                if approval["account_hash"] != _account_hash(c, key):
                    raise HTTPException(409, "Reconnect the model account used to allow this retry before resuming.")
                attempt = existing["attempts"] + 1
                retried = True
            if row["calls"] >= row["max_calls"]:
                raise HTTPException(409, "This project reached its brain request limit. Review its usage before starting more work.")
            conn.execute(db._q("UPDATE frontier_projects SET calls=calls+1,status='running',updated_at=? WHERE id=?"), (time.time(), req.project_id))
            if existing:
                errors = _errors(existing["error_json"])
                if not errors:
                    errors = [{"attempt": 1, "code": "unrecorded", "message": "The original error was not retained by the earlier preview."}]
                conn.execute(db._q("UPDATE frontier_requests SET status='running',attempts=?,context_json=?,error_json=?,fingerprint=?,started_at=? WHERE id=?"),
                             (attempt, context, json.dumps(errors), fingerprint, time.time(), req.request_id))
                conn.execute(db._q("UPDATE frontier_retries SET used_at=? WHERE request_id=?"), (time.time(), req.request_id))
            else:
                conn.execute(db._q("INSERT INTO frontier_requests (id,project_id,fingerprint,status,created_at,context_json,started_at) VALUES (?,?,?,?,?,?,?)"),
                             (req.request_id, req.project_id, fingerprint, "running", time.time(), context, time.time()))
            reserved = True
        text, incoming, outgoing = providers.generate(c["provider"], key, model, parts, req.kind, req.max_tokens)
        result = {"text": text, "provider": c["provider"], "model": model, "model_role": req.model_role, "input_tokens": incoming,
                  "output_tokens": outgoing, "request_id": req.request_id, "version": VERSION}
        with db._conn() as conn:
            conn.execute(db._q("UPDATE frontier_requests SET status='complete',result_json=? WHERE id=?"),
                         (json.dumps(result), req.request_id))
            conn.execute(db._q("""UPDATE frontier_projects SET input_tokens=input_tokens+?,output_tokens=output_tokens+?,
                status=CASE WHEN EXISTS (SELECT 1 FROM frontier_requests WHERE project_id=? AND status='needs_review')
                THEN 'needs_review' ELSE 'ready' END,updated_at=? WHERE id=?"""),
                         (incoming, outgoing, req.project_id, time.time(), req.project_id))
        return result
    except providers.ProviderBusy as busy:
        # Turned away at the door: nothing was generated or charged. Release the
        # request so the creator's Studio can simply send it again.
        if reserved:
            _release_busy(req, attempt, retried)
        raise HTTPException(429, busy.detail, headers={"Retry-After": str(busy.retry_after)}) from None
    except HTTPException as error:
        if reserved:
            _record_failure(req, attempt, error)
        raise
    except Exception as error:
        if reserved:
            _record_failure(req, attempt, error)
        raise HTTPException(502, "CR could not complete this request. Open Local studio in CR to review its recorded status.") from None
    finally:
        _give_slot(user["id"])


class SafeContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NativeSetup(SafeContract):
    """Reported local checks, not a cryptographic attestation of the computer."""
    local_version: str = Field(max_length=40)
    brain_mode: Literal["claudecode"] = "claudecode"
    general_model: str = Field(pattern=r"^[a-zA-Z0-9._\-]+$", max_length=100)
    visual_model: str = Field(pattern=r"^[a-zA-Z0-9._\-]+$", max_length=100)
    claude_installed: bool = Field(strict=True)
    claude_authenticated: bool = Field(strict=True)
    api_provider: Literal["firstParty"]
    ffmpeg_ready: bool = Field(strict=True)
    browser_ready: bool = Field(strict=True)
    costs_accepted: bool = Field(strict=True)
    node_ready: bool = Field(default=False, strict=True)
    media_ready: bool = Field(default=False, strict=True)
    models_verified: bool = Field(default=False, strict=True)
    purpose: Literal["video", "setup", "creative"] = "video"


class NativeBegin(SafeContract):
    begin_id: str = Field(min_length=12, max_length=80, pattern=r"^[a-zA-Z0-9_\-]+$")
    title: str = Field(min_length=1, max_length=300)
    style: str = Field(default="jung", min_length=1, max_length=80, pattern=r"^[a-z0-9_\-]+$")
    minutes: int = Field(default=1, ge=1, le=60)
    features: dict[str, bool] = Field(default_factory=dict, max_length=60)
    setup: NativeSetup
    max_calls: int = Field(default=500, ge=1, le=1500)
    resume_project_id: str | None = Field(default=None, pattern=r"^crv_[a-f0-9]{24}$")
    platform: Literal["mac", "windows"] = "mac"
    options: dict = Field(default_factory=dict, max_length=40)


class NativeAuthorize(SafeContract):
    project_id: str = Field(pattern=r"^crv_[a-f0-9]{24}$")
    stage_id: str = Field(min_length=12, max_length=80, pattern=r"^[a-zA-Z0-9_\-]+$")
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["text", "vision", "research"] = "text"
    model_role: Literal["general", "visual"] = "general"
    requested_model: str = Field(pattern=r"^[a-zA-Z0-9./_\-]+$", max_length=100)
    local_version: str = Field(max_length=40)
    brain_mode: Literal["claudecode"] = "claudecode"


class NativeUsage(SafeContract):
    input_tokens: int = Field(default=0, ge=0, le=1_000_000_000, strict=True)
    output_tokens: int = Field(default=0, ge=0, le=1_000_000_000, strict=True)
    cache_read_input_tokens: int = Field(default=0, ge=0, le=1_000_000_000, strict=True)
    cache_creation_input_tokens: int = Field(default=0, ge=0, le=1_000_000_000, strict=True)
    duration_seconds: float = Field(default=0, ge=0, le=86400)
    reported: bool = Field(default=False, strict=True)


NativeError = Literal["", "cli_failed", "timeout", "auth_missing", "cancelled", "network_interrupted",
                      "invalid_result", "authorization_interrupted", "render_failed", "runtime_failed", "usage_limit", "model_unavailable"]


class NativeFinishStage(SafeContract):
    project_id: str = Field(pattern=r"^crv_[a-f0-9]{24}$")
    stage_id: str = Field(min_length=12, max_length=80, pattern=r"^[a-zA-Z0-9_\-]+$")
    authorization_token: str = Field(min_length=24, max_length=100, pattern=r"^[a-zA-Z0-9_\-]+$")
    status: Literal["complete", "failed"]
    result_fingerprint: str = Field(default="", pattern=r"^(|[a-f0-9]{64})$")
    usage: NativeUsage = Field(default_factory=NativeUsage)
    error_code: NativeError = ""


class NativeArtifact(SafeContract):
    name: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._ \-]*$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: Literal["video/mp4", "audio/mpeg", "audio/wav", "image/png", "image/jpeg", "application/zip", "text/plain", "application/json"]
    duration_seconds: float | None = Field(default=None, ge=0, le=86400)
    width: int | None = Field(default=None, ge=1, le=16384, strict=True)
    height: int | None = Field(default=None, ge=1, le=16384, strict=True)


class NativeFinishProject(SafeContract):
    project_id: str = Field(pattern=r"^crv_[a-f0-9]{24}$")
    status: Literal["done", "failed"]
    artifacts: list[NativeArtifact] = Field(default_factory=list, max_length=20)
    error_code: NativeError = ""
    # why a stopped project stopped (Studio 105+, local_runtime/cr_failure.py); older Studios send none
    failure: studio_failures.FailureRecord | None = None


class FailureReport(BaseModel):
    """A stop that ends no project of its own (Studio Cloud's preparing)."""
    model_config = ConfigDict(extra="ignore")
    failure: studio_failures.FailureRecord


def update_note(local_version) -> str:
    """One plain line for a stopped video's message when its Studio isn't the newest release, or "". It never asks
    for more than the release rules do: any of SUPPORTED_RELEASES may keep making videos (MIN_SUPPORTED_RELEASE), so
    for those updating is the creator's choice, between videos."""
    if local_version not in APPROVED_RELEASES or VERSION not in APPROVED_RELEASES:
        return ""
    if APPROVED_RELEASES.index(local_version) >= APPROVED_RELEASES.index(VERSION):
        return ""
    newest = "Studio " + VERSION.rsplit(".", 1)[-1]
    if local_version in SUPPORTED_RELEASES:
        return (f"A newer Studio, {newest}, is out and fixes many problems like this one. You can update between "
                "videos with Update now at the top of the Studio page.")
    return (f"A newer Studio, {newest}, is out and fixes many problems like this one. This Studio can finish its saved "
            "videos; update between videos (Update now at the top of the Studio page) to start new ones.")


class NativeRetryStage(SafeContract):
    project_id: str = Field(pattern=r"^crv_[a-f0-9]{24}$")
    stage_id: str = Field(min_length=12,max_length=80,pattern=r"^[a-zA-Z0-9_\-]+$")
    acknowledge_costs: bool = Field(default=False,strict=True)


def workflow_contract(release=None):
    """The production contract. A new project pins the release it starts on: the customer's own when it is one of
    SUPPORTED_RELEASES, else the newest (VERSION)."""
    choices = [row["id"] for row in providers.PROVIDERS["anthropic"]["brain_models"]]
    return {"workflow_version": WORKFLOW_VERSION, "schema_version": 1,
            "required_release": release if release in SUPPORTED_RELEASES else VERSION, "engine_source_sha256": SOURCE_SHA256,
            "mode": "claudecode", "models": choices, "default_general_model": "claude-sonnet-5-5",
            "default_visual_model": "claude-opus-5-5", "max_active_stages": NATIVE_STAGE_LIMIT,
            "access": "subscriber", "public_release_allowed": True,
            "brain_billing": "Your local Claude Code account and its usage limits",
            "brain_api_key_required": False, "rendering": "Your computer",
            "prompt_visibility": "The downloaded studio includes ChannelRecipe's local workflow and prompts.",
            "setup_required": ["local_version", "brain_mode", "general_model", "visual_model", "claude_installed",
                               "claude_authenticated", "api_provider", "ffmpeg_ready", "browser_ready", "node_ready",
                               "models_verified", "media_ready", "costs_accepted"]}


def _runtime_compatible(version, workflow):
    # Adapter-only upgrades keep the pinned creative engine and workflow. A
    # changed engine or workflow must explicitly migrate projects instead.
    approved = APPROVED_RELEASES
    current = workflow_contract()
    if (version not in approved or workflow.get("required_release") not in approved or VERSION not in approved
            or workflow.get("workflow_version") != current["workflow_version"]
            or workflow.get("engine_source_sha256") != current["engine_source_sha256"]):
        return False
    # A running approved adapter can finish a project pinned to an older
    # identical engine. A fresh contract pins today's release, so it still
    # requires the current download. Never admit an unknown/future release.
    return approved.index(workflow["required_release"]) <= approved.index(version) <= approved.index(VERSION)


def _setup_issues(setup, workflow):
    issues = []
    if not _runtime_compatible(setup.local_version,workflow):
        issues.append("Download the release required by this workflow before starting or resuming.")
    if not setup.claude_installed or not setup.claude_authenticated:
        issues.append("Install Claude Code and sign in locally before making a video.")
    if not setup.ffmpeg_ready or not setup.browser_ready:
        issues.append("Finish the local video-tools and graphics-browser checks.")
    if not setup.costs_accepted:
        issues.append("Review and acknowledge your Claude and media-provider spending.")
    if not setup.node_ready:
        issues.append("Install the JavaScript runtime required by the local workflow.")
    if setup.purpose != "setup" and not setup.models_verified:
        issues.append("Verify both selected Claude models in local setup before creating a video.")
    if setup.purpose == "video" and not setup.media_ready:
        issues.append("Complete this preset's media-account checks before creating a video.")
    if any(model not in workflow["models"] for model in (setup.general_model, setup.visual_model)):
        issues.append("Choose supported writing and specialist models for this workflow.")
    return issues


def native_prepare(user, setup: NativeSetup | None = None):
    require_studio_user(user)
    workflow = workflow_contract(setup.local_version if setup else None)
    issues = _setup_issues(setup, workflow) if setup else ["Run the downloaded studio's local setup checks first."]
    if not release_ready():
        issues.append("The required protected runtime download is being prepared.")
    return {"ready": not issues, "workflow": workflow, "issues": issues,
            "local_checks_are_attestation": False, "starts_local_process": False,
            "message": "Claude Code must execute the local launch command. MCP does not launch a computer over the network."}


def _native_session(user, project_id):
    p = project(user, project_id)  # Includes live authorization and tenant check.
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM frontier_native_sessions WHERE project_id=? AND user_id=?"),
                           (project_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(409, "This project uses the API brain. Start a local Claude Code project explicitly.")
    return p, dict(row)


def _native_begin_result(user, session, platform, resumed=False):
    p = project(user, session["project_id"])
    purpose = json.loads(session["setup_json"]).get("purpose", "video")
    command = ((r".venv\Scripts\python.exe" if platform == "windows" else ".venv/bin/python") +
               " cr_run.py --project " + p["id"])
    return {"project_id": p["id"], "project": p, "workflow": json.loads(session["workflow_json"]),
            "job_spec": json.loads(session["spec_json"]), "purpose": purpose,
            "launch_command": command if purpose == "video" else "",
            "launch_required": purpose == "video" and p["status"] != "done", "resumed": resumed,
            "message": ("Run this command from the extracted studio folder. It starts the saved project locally."
                        if purpose == "video" else "Continue this action in its original local studio controls.")}


def native_begin(user, req: NativeBegin):
    require_studio_user(user)
    initialize()
    import re
    if not req.title.strip() or any(c in req.title for c in "\n\r") or any(not re.fullmatch(r"[a-zA-Z0-9_\-]{1,80}", key) for key in req.features):
        raise HTTPException(400, "Choose a title and valid original feature names.")
    if req.resume_project_id:
        p, session = _native_session(user, req.resume_project_id)
        workflow = json.loads(session["workflow_json"])
        previous_setup = json.loads(session["setup_json"])
        issues = _setup_issues(req.setup, workflow)
        if any(getattr(req.setup, key) != previous_setup[key] for key in ("general_model", "visual_model", "brain_mode")):
            issues.append("Resume with this project's pinned Claude model profile.")
        if issues:
            raise HTTPException(409, " ".join(issues))
        # A failed local render can reuse all completed Claude stages. A failed
        # or uncertain Claude stage still needs its separate recovery approval.
        if p["status"] == "failed":
            with db._conn() as conn:
                blocked = conn.execute(db._q("SELECT COUNT(*) AS n FROM frontier_native_stages WHERE project_id=? AND status IN ('failed','authorized')"),(req.resume_project_id,)).fetchone()["n"]
                if not blocked:
                    conn.execute(db._q("UPDATE frontier_projects SET status='ready',updated_at=? WHERE id=? AND status='failed'"),(time.time(),req.resume_project_id))
        return _native_begin_result(user, session, req.platform, resumed=True)
    ready = native_prepare(user, req.setup)
    if not ready["ready"]:
        raise HTTPException(409, " ".join(ready["issues"]))
    if req.setup.purpose == "creative" and req.title.startswith(STYLE_STUDY_PREFIX):
        with db._conn() as conn:
            replay = conn.execute(db._q("SELECT 1 FROM frontier_native_sessions WHERE user_id=? AND begin_id=?"),
                                  (user["id"], req.begin_id)).fetchone()
        if not replay:
            _require_style_clone_allowance(user)
    allowed = {"steps", "script", "burn_subs", "thumb_count", "use_motion", "character", "character_image",
               "maps", "headlines", "spotlight", "objects", "depth", "extra", "avatar_id", "avatar_s",
               "avatar_count", "auto_style", "offer", "offer_plugs", "desc", "chapters", "sources", "byo_audio", "mix", "render_backend", "manimgx_identity"}
    if set(req.options) - allowed or len(json.dumps(req.options)) > 200000:
        raise HTTPException(400, "Use valid original video options without account credentials.")
    if req.options.get("render_backend", "original") not in {"original", "manimgx"}:
        raise HTTPException(400, "Choose the standard renderer or ManimGX.")
    if req.options.get("render_backend") == "manimgx" and not re.fullmatch(r"[a-f0-9]{64}",str(req.options.get("manimgx_identity", ""))):
        raise HTTPException(400, "Complete the ManimGX check before starting this project.")
    spec = {**req.options, "title": req.title.strip(), "style": req.style, "minutes": req.minutes, "features": req.features}
    identity = {"spec": spec, "release": req.setup.local_version, "general_model": req.setup.general_model,
                "visual_model": req.setup.visual_model, "max_calls": req.max_calls}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    now = time.time()
    # A user row is not needed here: the unique per-user begin ID serializes
    # repeated project submissions, including across web processes.
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        project_id = "crv_" + secrets.token_hex(12)
        changed = conn.execute(db._q("""INSERT INTO frontier_native_sessions
            (project_id,user_id,begin_id,begin_fingerprint,workflow_json,setup_json,spec_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,begin_id) DO NOTHING"""),
            (project_id, user["id"], req.begin_id, fingerprint, json.dumps(ready["workflow"]),
             req.setup.model_dump_json(), json.dumps(spec), now, now)).rowcount
        row = conn.execute(db._q("SELECT * FROM frontier_native_sessions WHERE user_id=? AND begin_id=?"),
                           (user["id"], req.begin_id)).fetchone()
        if row["begin_fingerprint"] != fingerprint:
            raise HTTPException(409, "This begin ID belongs to different saved work. Resume its project explicitly.")
        if changed:
            conn.execute(db._q("""INSERT INTO frontier_projects
                (id,user_id,title,style,version,max_calls,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)"""),
                (project_id, user["id"], req.title.strip(), req.style, VERSION, req.max_calls, now, now))
        session = dict(row)
    return _native_begin_result(user, session, req.platform, resumed=not bool(changed))


def native_progress(user, project_id):
    p, session = _native_session(user, project_id)
    with db._conn() as conn:
        rows = conn.execute(db._q("""SELECT stage_id,kind,model_role,selected_model,status,result_fingerprint,attempts,
            usage_json,error_code,created_at,updated_at FROM frontier_native_stages
            WHERE project_id=? ORDER BY created_at,stage_id"""), (project_id,)).fetchall()
    stages = []
    for row in rows:
        stage = dict(row)
        stage["usage"] = json.loads(stage.pop("usage_json"))
        stage["can_retry"] = stage["status"] in {"failed","authorized"} and stage["attempts"] == 1
        stages.append(stage)
    p["receipt_note"] = "Locally reported Claude Code usage; your Claude and media accounts determine actual charges."
    purpose = json.loads(session["setup_json"]).get("purpose", "video")
    return {"project_id": project_id, "project": p, "workflow": json.loads(session["workflow_json"]),
            "job_spec": json.loads(session["spec_json"]), "purpose": purpose, "stages": stages,
            "artifacts": json.loads(session["artifacts_json"]), "error_code": session["error_code"],
            "brain_mode": "claudecode", "can_resume": purpose == "video" and p["status"] != "done" and not any(s["status"] == "authorized" for s in stages)}


def native_authorize(user, req: NativeAuthorize):
    p, session = _native_session(user, req.project_id)
    workflow, setup = json.loads(session["workflow_json"]), json.loads(session["setup_json"])
    if not _runtime_compatible(req.local_version,workflow):
        raise HTTPException(409, "Use this project's pinned runtime release before executing a stage.")
    selected = setup["visual_model"] if req.model_role == "visual" else setup["general_model"]
    # Preserve reservations and cached completion across approved adapter-only
    # upgrades. Changing the model, tools or stage input still conflicts.
    fingerprint = hashlib.sha256(req.model_copy(update={"local_version":workflow["required_release"]}).model_dump_json().encode()).hexdigest()
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.IS_PG else ""
        p = conn.execute(db._q("SELECT * FROM frontier_projects WHERE id=? AND user_id=?") + lock,
                         (req.project_id, user["id"])).fetchone()
        existing = conn.execute(db._q("SELECT * FROM frontier_native_stages WHERE project_id=? AND stage_id=?"),
                                (req.project_id, req.stage_id)).fetchone()
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise HTTPException(409, "This stage ID belongs to different work.")
            if existing["status"] == "complete":
                return {"execute": False, "reuse": True, "status": "complete", "stage_id": req.stage_id,
                        "project_id": req.project_id, "result_fingerprint": existing["result_fingerprint"],
                        "selected_model": existing["selected_model"], "workflow_version": workflow["workflow_version"]}
            if existing["status"] == "retry_allowed" and p["calls"] < p["max_calls"]:
                conn.execute(db._q("UPDATE frontier_native_stages SET status='authorized',attempts=attempts+1,authorization_hash=?,error_code='',updated_at=? WHERE project_id=? AND stage_id=?"),
                    (hashlib.sha256(token.encode()).hexdigest(),now,req.project_id,req.stage_id))
                conn.execute(db._q("UPDATE frontier_projects SET calls=calls+1,status='running',updated_at=? WHERE id=?"),(now,req.project_id))
                return {"execute":True,"reuse":False,"status":"authorized","authorization_token":token,
                    "selected_model":existing["selected_model"],"stage_id":req.stage_id,"project_id":req.project_id,
                    "workflow_version":workflow["workflow_version"]}
            raise HTTPException(409, "This stage already started or stopped. Review its local cache and Claude usage; it will not run again automatically.")
        if p["status"] in ("done", "failed"):
            raise HTTPException(409, "Resume this saved project before authorizing new work.")
        duplicate = conn.execute(db._q("""SELECT stage_id FROM frontier_native_stages
            WHERE project_id=? AND input_fingerprint=? AND kind=? AND model_role=?"""),
            (req.project_id, req.input_fingerprint, req.kind, req.model_role)).fetchone()
        if duplicate:
            raise HTTPException(409, "This input already has a recorded stage. Use its original stage ID and local cache.")
        if p["calls"] >= p["max_calls"]:
            raise HTTPException(409, "This project reached its stage request ceiling. Its budget has not been increased.")
        active = conn.execute(db._q("SELECT COUNT(*) AS n FROM frontier_native_stages WHERE project_id=? AND status='authorized'"),
                              (req.project_id,)).fetchone()["n"]
        if active >= NATIVE_STAGE_LIMIT:
            raise HTTPException(429, "Wait for this project's active stages before requesting another authorization.", headers={"Retry-After": "5"})
        conn.execute(db._q("""INSERT INTO frontier_native_stages
            (project_id,stage_id,fingerprint,input_fingerprint,kind,model_role,selected_model,status,authorization_hash,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)"""), (req.project_id, req.stage_id, fingerprint, req.input_fingerprint,
            req.kind, req.model_role, selected, "authorized", hashlib.sha256(token.encode()).hexdigest(), now, now))
        conn.execute(db._q("UPDATE frontier_projects SET calls=calls+1,status='running',updated_at=? WHERE id=?"),
                     (now, req.project_id))
    return {"execute": True, "reuse": False, "status": "authorized", "authorization_token": token,
            "selected_model": selected, "stage_id": req.stage_id, "project_id": req.project_id,
            "workflow_version": workflow["workflow_version"]}


def native_finish_stage(user, req: NativeFinishStage):
    _native_session(user, req.project_id)
    if req.status == "complete" and (not req.result_fingerprint or req.error_code):
        raise HTTPException(400, "A completed stage needs a saved-result digest and no error code.")
    token_hash = hashlib.sha256(req.authorization_token.encode()).hexdigest()
    usage = req.usage.model_dump()
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.IS_PG else ""
        conn.execute(db._q("SELECT id FROM frontier_projects WHERE id=? AND user_id=?") + lock,
                     (req.project_id, user["id"])).fetchone()
        stage = conn.execute(db._q("SELECT * FROM frontier_native_stages WHERE project_id=? AND stage_id=?"),
                             (req.project_id, req.stage_id)).fetchone()
        if not stage or not secrets.compare_digest(stage["authorization_hash"], token_hash):
            raise HTTPException(409, "This stage result has no matching execution authorization.")
        late_receipt = stage["status"] == "retry_allowed" and stage["attempts"] == 1
        if late_receipt and req.status == "failed":
            # A delayed first-attempt failure must not revoke an approved retry
            # or account for the same execution again.
            return {"saved": True, "reused": True, "status": "retry_allowed", "stage_id": req.stage_id}
        if stage["status"] != "authorized" and not late_receipt:
            if (stage["status"] == req.status and stage["result_fingerprint"] == req.result_fingerprint
                    and json.loads(stage["usage_json"]) == usage and stage["error_code"] == req.error_code):
                return {"saved": True, "reused": True, "status": stage["status"], "stage_id": req.stage_id}
            raise HTTPException(409, "This stage already has a different saved result.")
        now = time.time()
        conn.execute(db._q("""UPDATE frontier_native_stages SET status=?,result_fingerprint=?,usage_json=?,error_code=?,updated_at=?
            WHERE project_id=? AND stage_id=?"""), (req.status, req.result_fingerprint, json.dumps(usage), req.error_code,
            now, req.project_id, req.stage_id))
        pending = conn.execute(db._q("SELECT COUNT(*) AS n FROM frontier_native_stages WHERE project_id=? AND status='authorized'"),
                               (req.project_id,)).fetchone()["n"]
        failed = conn.execute(db._q("SELECT COUNT(*) AS n FROM frontier_native_stages WHERE project_id=? AND status='failed'"),
                              (req.project_id,)).fetchone()["n"]
        status = "running" if pending else "failed" if failed else "ready"
        incoming = usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"]
        outgoing = usage["output_tokens"]
        if late_receipt:
            # The first attempt may already have reported partial usage with
            # its failure. Reconcile its saved success before another execution
            # starts, adding only usage not yet counted for that same attempt.
            previous = json.loads(stage["usage_json"])
            recorded_incoming = sum(previous.get(key, 0) for key in
                ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            incoming = max(0, incoming - recorded_incoming)
            outgoing = max(0, outgoing - previous.get("output_tokens", 0))
        conn.execute(db._q("""UPDATE frontier_projects SET status=?,input_tokens=input_tokens+?,
            output_tokens=output_tokens+?,updated_at=? WHERE id=?"""), (status, incoming, outgoing, now, req.project_id))
    return {"saved": True, "reused": False, "status": req.status, "stage_id": req.stage_id}


def native_finish_project(user, req: NativeFinishProject):
    _native_session(user, req.project_id)
    if req.status == "done" and (not req.artifacts or req.error_code):
        raise HTTPException(400, "A finished project needs saved output metadata and no error code.")
    artifacts = [artifact.model_dump() for artifact in req.artifacts]
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.IS_PG else ""
        row = conn.execute(db._q("SELECT * FROM frontier_projects WHERE id=? AND user_id=?") + lock,
                           (req.project_id, user["id"])).fetchone()
        session = conn.execute(db._q("SELECT * FROM frontier_native_sessions WHERE project_id=?"), (req.project_id,)).fetchone()
        if row["status"] == "done":
            if req.status == "done" and json.loads(session["artifacts_json"]) == artifacts:
                return {"saved": True, "reused": True, "status": "done", "project_id": req.project_id}
            raise HTTPException(409, "This project already has its final output metadata.")
        active = conn.execute(db._q("SELECT COUNT(*) AS n FROM frontier_native_stages WHERE project_id=? AND status='authorized'"),
                              (req.project_id,)).fetchone()["n"]
        failed = conn.execute(db._q("SELECT COUNT(*) AS n FROM frontier_native_stages WHERE project_id=? AND status='failed'"),
                              (req.project_id,)).fetchone()["n"]
        if req.status == "done" and (active or failed):
            raise HTTPException(409, "Review incomplete or failed stages before marking this video done.")
        now = time.time()
        conn.execute(db._q("UPDATE frontier_native_sessions SET artifacts_json=?,error_code=?,updated_at=? WHERE project_id=?"),
                     (json.dumps(artifacts), req.error_code, now, req.project_id))
        conn.execute(db._q("UPDATE frontier_projects SET status=?,updated_at=? WHERE id=?"), (req.status, now, req.project_id))
        setup = json.loads(session["setup_json"])
        title, purpose = row["title"], setup.get("purpose", "video")
    result = {"saved": True, "reused": False, "status": req.status, "project_id": req.project_id}
    if req.status == "done" and purpose == "video":
        from webapp import lifecycle_email
        lifecycle_email.on_video_finished(user, title)              # the first one gets a short "it's done" email
    if req.status == "failed":
        if req.failure is not None:                                  # why it stopped: counted in /api/admin/failures
            studio_failures.record_project(user["id"], req.project_id, req.error_code, req.failure, title)
        note = "" if req.error_code == "cancelled" else update_note(
            (req.failure.version if req.failure is not None else "") or setup.get("local_version"))
        if note:
            result["update_note"] = note
    return result


def native_retry_stage(user,req:NativeRetryStage):
    _native_session(user,req.project_id)
    if not req.acknowledge_costs:
        raise HTTPException(400,"Confirm you reviewed Claude usage and accept another attempt before retrying.")
    with db._conn() as conn:
        if not db.IS_PG:conn.execute("BEGIN IMMEDIATE")
        lock=" FOR UPDATE" if db.IS_PG else ""
        p=conn.execute(db._q("SELECT * FROM frontier_projects WHERE id=? AND user_id=?")+lock,(req.project_id,user["id"])).fetchone()
        stage=conn.execute(db._q("SELECT * FROM frontier_native_stages WHERE project_id=? AND stage_id=?"),(req.project_id,req.stage_id)).fetchone()
        if not stage or stage["status"] not in {"authorized","failed"} or stage["attempts"] != 1 or p["status"] == "done" or p["calls"] >= p["max_calls"]:
            raise HTTPException(409,"This stage has no additional recovery attempt available.")
        now=time.time()
        conn.execute(db._q("UPDATE frontier_native_stages SET status='retry_allowed',updated_at=? WHERE project_id=? AND stage_id=?"),(now,req.project_id,req.stage_id))
        conn.execute(db._q("UPDATE frontier_projects SET status='ready',updated_at=? WHERE id=?"),(now,req.project_id))
    return {"approved":True,"project_id":req.project_id,"stage_id":req.stage_id,"message":"One recovery attempt is approved. Resume the same local video to use it."}


def _brand_workflow_response(payload):
    """Brand protocol metadata without changing saved work or arbitrary text.

    Native session contracts and stage fingerprints retain their original
    identifier for existing projects. Only these response-schema locations are
    aliases; a title, artifact name or user option with the same text is data.
    """
    if not isinstance(payload, dict):
        return False
    changed = False
    for contract in (payload, payload.get("workflow"), payload.get("production_workflow")):
        if isinstance(contract, dict) and contract.get("workflow_version") == "frontier-5.3-claudecode.1":
            contract["workflow_version"] = BRANDED_WORKFLOW_VERSION
            changed = True
    return changed


class _BrandedWorkflowRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def branded(request: Request):
            response = await original(request)
            # In particular, do not read or buffer FileResponse/StreamingResponse
            # bodies: the authenticated private ZIP keeps its original stream.
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            body = getattr(response, "body", None)
            if content_type == "application/json" and isinstance(body, bytes):
                payload = json.loads(body)
                if _brand_workflow_response(payload):
                    response.body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    response.headers["content-length"] = str(len(response.body))
            return response

        return branded


# Studio's upload relay (make_router's /upload-relay): Kie's upload host, its two upload addresses, and the limits.
MEDIA_UPLOAD_URL = "https://kieai.redpandaai.co"
MEDIA_UPLOAD_PATHS = ("/api/file-stream-upload", "/api/file-base64-upload")
MEDIA_UPLOAD_PARALLEL, MEDIA_UPLOAD_MAX_BYTES = 4, 200 * 1024 * 1024


def relay_client():
    """The HTTP client for one relayed download from Kie's file host (tests swap in a stand-in transport)."""
    return httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0, pool=10.0))


def studio_access(session_user):
    """FastAPI dependency: a Studio plan, by web session or by the Studio's account key."""
    def access(request: Request):
        authorization = request.headers.get("authorization", "")
        if authorization:
            if not authorization.startswith("Bearer cr_mcp_"):
                raise HTTPException(401, "Reconnect your ChannelRecipe account.")
            user = require_studio_user(db.get_user_by_mcp_api_key(authorization[7:]))
            milestones.seen(user["id"])                 # the customer's Studio called in
            return user
        user = session_user(request)
        return require_studio_user(user)
    return access


def make_router(session_user, *, prefix="/api/frontier"):
    options = {"route_class": _BrandedWorkflowRoute} if prefix == "/api/studio" else {}
    router = APIRouter(prefix=prefix, tags=["ChannelRecipe Studio"], **options)
    access = studio_access(session_user)

    @router.post("/admin/style-clones")
    def post_style_clone_grant(req: StyleCloneGrant, user=Depends(access)):
        return JSONResponse(grant_style_clones(user, req.email, req.extra, req.reset, req.note),
                            headers={"Cache-Control": "private, no-store"})

    @router.get("/status")
    def get_status(user=Depends(access)):
        return status(user)

    @router.post("/brain-key")
    def put_key(req: BrainKey, user=Depends(access)):
        return save_key(user, req)

    @router.delete("/brain-key")
    def delete_key(user=Depends(access)):
        initialize()
        with db._conn() as conn:
            conn.execute(db._q("DELETE FROM frontier_credentials WHERE user_id=?"), (user["id"],))
        return {"connected": False}

    @router.post("/projects")
    def new_project(req: ProjectRequest, user=Depends(access)):
        return create_project(user, req)

    @router.get("/projects")
    def projects(user=Depends(access)):
        initialize()
        with db._conn() as conn:
            rows = conn.execute(db._q("SELECT * FROM frontier_projects WHERE user_id=? ORDER BY created_at DESC LIMIT 20"), (user["id"],)).fetchall()
        return {"projects": [dict(r) for r in rows]}

    @router.get("/projects/{project_id}")
    def get_project(project_id: str, user=Depends(access)):
        return project(user, project_id)

    @router.get("/projects/{project_id}/requests")
    def get_requests(project_id: str, user=Depends(access)):
        return request_history(user, project_id)

    @router.post("/projects/{project_id}/requests/{request_id}/retry")
    def retry(project_id: str, request_id: str, req: RetryApproval, user=Depends(access)):
        return approve_retry(user, project_id, request_id, req)

    @router.post("/brain")
    def brain(req: BrainRequest, user=Depends(access)):
        return generate(user, req)

    @router.post("/runtime/prepare")
    def runtime_prepare(req: NativeSetup, user=Depends(access)):
        return native_prepare(user, req)

    @router.post("/projects/begin")
    def runtime_begin(req: NativeBegin, user=Depends(access)):
        return native_begin(user, req)

    @router.post("/stages/authorize")
    def runtime_authorize(req: NativeAuthorize, user=Depends(access)):
        return native_authorize(user, req)

    @router.post("/stages/finish")
    def runtime_finish_stage(req: NativeFinishStage, user=Depends(access)):
        return native_finish_stage(user, req)

    @router.get("/projects/{project_id}/progress")
    def runtime_progress(project_id: str, user=Depends(access)):
        return native_progress(user, project_id)

    @router.post("/projects/finish")
    def runtime_finish_project(req: NativeFinishProject, user=Depends(access)):
        return native_finish_project(user, req)

    @router.post("/stages/retry")
    def runtime_retry(req:NativeRetryStage,user=Depends(access)):
        return native_retry_stage(user,req)

    @router.post("/failures")
    def report_failure(req: FailureReport, user=Depends(access)):
        """A stop that ends no project (Studio Cloud's preparing): kept for the team's counts, a few an hour."""
        saved = studio_failures.record_report(user["id"], req.failure)
        note = "" if req.failure.category == "cancelled" else update_note(req.failure.version)
        return {"saved": saved, **({"update_note": note} if note else {})}

    def site_link(request: Request, user_id: int, what: str) -> str | None:
        """This site's own link for `what`, signed for this account, for a computer that can't fetch storage links."""
        token = stream_token(user_id, what)
        if not token:
            return None
        origin = str(request.base_url).rstrip("/")
        if origin.startswith("http://") and request.url.hostname not in ("localhost", "127.0.0.1", "testserver"):
            origin = "https://" + origin[len("http://"):]           # behind the platform's proxy: the public origin
        return f"{origin}{prefix}/stream/{what}?t={token}"

    @router.get("/shorts-pack")
    def shorts_pack(request: Request, user=Depends(access)):
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        from webapp import storage
        if not storage.is_remote():
            raise HTTPException(503, "The Shorts pack is being prepared. Please try again shortly.")
        if storage_blocked(user["id"], "pack"):
            link = site_link(request, user["id"], "pack")
            if link:
                return RedirectResponse(link, status_code=302, headers=headers)
        try:
            url = storage._get_client().generate_presigned_url("get_object", Params={
                "Bucket": storage._spaces_creds()[2], "Key": SHORTS_PACK_KEY,
                "ResponseContentDisposition": 'attachment; filename="shorts-assets.zip"',
                "ResponseContentType": "application/zip"}, ExpiresIn=DOWNLOAD_LINK_SECONDS)
        except Exception:
            raise HTTPException(503, "The Shorts pack is being prepared. Please try again shortly.")
        return RedirectResponse(url, status_code=302, headers=headers)

    def stream_private(request: Request, key: str, filename: str, headers: dict):
        """A private release file streamed through channelrecipe.com, byte ranges included (resume, partial update).
        Some internet providers can't reach the storage host the signed links name (a creator's browser showed
        DNS_PROBE_FINISHED_NXDOMAIN for channelrecipe-media.sfo3.digitaloceanspaces.com), but they reach this site."""
        from webapp import storage
        if not storage.is_remote():
            raise HTTPException(503, "The studio download is being prepared. Please try again shortly.")
        params = {"Bucket": storage._spaces_creds()[2], "Key": key}
        wanted = request.headers.get("range") or ""
        if re.fullmatch(r"bytes=\d+-\d*", wanted):
            params["Range"] = wanted
        try:
            obj = storage._get_client().get_object(**params)
        except Exception as error:
            reply = getattr(error, "response", None)
            if isinstance(reply, dict) and (reply.get("Error") or {}).get("Code") == "InvalidRange":
                raise HTTPException(416, "That part of the file doesn't exist; download it again.")
            raise HTTPException(503, "The studio download is being prepared. Please try again shortly.")
        body = obj["Body"]
        out = {**headers, "Accept-Ranges": "bytes", "Content-Length": str(obj.get("ContentLength") or 0),
               "Content-Disposition": 'attachment; filename="%s"' % filename}
        sha = (obj.get("Metadata") or {}).get("sha256")
        if sha:
            out["x-amz-meta-sha256"] = sha
        if obj.get("ContentRange"):
            out["Content-Range"] = obj["ContentRange"]

        def chunks():
            try:
                yield from body.iter_chunks(1 << 20)
            finally:
                body.close()
        return StreamingResponse(chunks(), status_code=206 if obj.get("ContentRange") else 200,
                                 media_type="application/zip", headers=out)

    @router.get("/download/file")
    def download_file(request: Request, user=Depends(access)):
        """The studio ZIP through this site (the website's download button; the updater's fallback)."""
        if request.headers.get("range", "bytes=0-").startswith("bytes=0-"):
            milestones.record(user["id"], milestones.DOWNLOADED)
        if not release_ready():
            raise HTTPException(503, "The studio download is being prepared. Please try again shortly.")
        return stream_private(request, RELEASE_KEY, "ChannelRecipe-local-studio-%s.zip" % VERSION,
                              {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    # Kie's finished files (voice, pictures, music, 3D models) for a Studio whose network can't reach Kie's file host:
    # a creator's provider cut every connection to aiquickdraw.com during the handshake while this site worked
    # (local_runtime/cr_relay.py). Only Kie's file hosts, only for a Studio account, at most MEDIA_RELAY_PARALLEL at a
    # time per account and MEDIA_RELAY_MAX_BYTES a file.
    #
    # Streamed with async httpx, so a relay holds no worker thread: not while it waits for one of its account's slots
    # (up to a minute), not while Kie's host connects and answers, not while a file trickles through. With a thread per
    # relay (requests), one Studio fetching twenty files at once held twenty of the threads every plain-def route on
    # the site shares.
    MEDIA_RELAY_SUFFIXES = ("aiquickdraw.com", "redpandaai.co")
    MEDIA_RELAY_PARALLEL, MEDIA_RELAY_MAX_BYTES = 6, 600 * 1024 * 1024
    relay_slots: dict = {}
    relay_lock = threading.Lock()

    @router.get("/media-relay")
    async def media_relay(request: Request, url: str = "", user=Depends(access)):
        import asyncio
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or parts.port not in (None, 443) or parts.username or parts.password
                or not any(host == s or host.endswith("." + s) for s in MEDIA_RELAY_SUFFIXES)):
            raise HTTPException(400, "Only Kie's generated files can be fetched this way.")
        slot = relay_slots.setdefault(user["id"], asyncio.Semaphore(MEDIA_RELAY_PARALLEL))
        try:
            await asyncio.wait_for(slot.acquire(), timeout=60)
        except asyncio.TimeoutError:
            raise HTTPException(429, "Too many files at once. Try again in a moment.") from None
        # the file's own bytes (identity): its length and byte ranges are passed on as Kie's host states them
        headers = {"User-Agent": "ChannelRecipe-Studio-relay", "Accept-Encoding": "identity"}
        wanted = request.headers.get("range") or ""
        if re.fullmatch(r"bytes=\d+-\d*", wanted):
            headers["Range"] = wanted
        client = relay_client()
        upstream = None
        try:
            upstream = await client.send(client.build_request("GET", url, headers=headers), stream=True,
                                         follow_redirects=False)
            if upstream.status_code not in (200, 206):
                raise HTTPException(502 if upstream.status_code >= 500 else 404,
                                    f"Kie's file host answered {upstream.status_code} for this file.")
            size = int(upstream.headers.get("Content-Length") or 0)
            if size > MEDIA_RELAY_MAX_BYTES:
                raise HTTPException(413, "This file is larger than the relay's limit.")
        except BaseException as error:
            if upstream is not None:
                await upstream.aclose()
            await client.aclose()
            slot.release()
            if isinstance(error, httpx.HTTPError):
                raise HTTPException(502, "Kie's file host didn't answer. Try again shortly.") from None
            raise
        out = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        # a host that encodes anyway: the decoded file goes out, so its encoded length can't
        encoded = upstream.headers.get("Content-Encoding", "identity").lower() != "identity"
        for name in ("Content-Length", "Content-Range", "Content-Type", "Accept-Ranges"):
            if upstream.headers.get(name) and not (encoded and name == "Content-Length"):
                out[name] = upstream.headers[name]

        async def chunks():
            sent = 0
            try:
                async for chunk in upstream.aiter_bytes(1 << 20):
                    sent += len(chunk)
                    if sent > MEDIA_RELAY_MAX_BYTES:
                        break
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
                slot.release()
        return StreamingResponse(chunks(), status_code=upstream.status_code, headers=out,
                                 media_type=upstream.headers.get("Content-Type") or "application/octet-stream")

    # Uploads to Kie's upload host (reference pictures, the watcher's light video copies) for a Studio whose network
    # can't reach kieai.redpandaai.co (local_runtime/cr_relay.py). Only that host's two upload addresses, only for a
    # Studio account, MEDIA_UPLOAD_PARALLEL at a time per account and MEDIA_UPLOAD_MAX_BYTES an upload. An upload has
    # to reach Kie with the creator's own Kie key: it comes in X-CR-Kie-Authorization for this one request, goes to Kie
    # and is never kept or logged. Kie's answer comes back as it is.
    upload_slots: dict = {}

    def forward_upload(user_id, path, body, content_type, kie_authorization):
        """(status, body, content type) from Kie. Never raises: an exception here would carry the key into error
        reports."""
        with relay_lock:
            slot = upload_slots.setdefault(user_id, threading.BoundedSemaphore(MEDIA_UPLOAD_PARALLEL))
        if not slot.acquire(timeout=120):
            body.close()
            return 429, b'{"error":"Too many uploads at once. Try again in a moment."}', "application/json"
        try:
            body.seek(0, 2)
            size = body.tell()
            body.seek(0)
            upstream = requests.post(MEDIA_UPLOAD_URL + path, data=body, timeout=(10, 170), allow_redirects=False,
                                     headers={"Authorization": kie_authorization, "Content-Type": content_type,
                                              "Content-Length": str(size), "User-Agent": "ChannelRecipe-Studio-relay"})
            reply = upstream.content[:1 << 20]
            kind = (upstream.headers.get("Content-Type") or "application/json").split(";", 1)[0]
            if kind == "application/json":
                try:
                    json.loads(reply)
                except ValueError:
                    kind = "text/plain"
            return upstream.status_code, reply, kind
        except Exception:                                          # noqa: BLE001 - no detail: it could hold the key
            return 502, b'{"error":"Kie\'s upload host didn\'t answer. Try again shortly."}', "application/json"
        finally:
            slot.release()
            body.close()

    @router.post("/upload-relay")
    async def upload_relay(request: Request, path: str = "", user=Depends(access)):
        import tempfile
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import Response
        kie_authorization = request.headers.get("x-cr-kie-authorization") or ""
        if path not in MEDIA_UPLOAD_PATHS:
            return JSONResponse({"error": "Only uploads to Kie's upload host can be sent this way."}, status_code=400)
        if not kie_authorization.startswith("Bearer ") or len(kie_authorization) > 512:
            return JSONResponse({"error": "Reconnect your Kie.ai account in Setup."}, status_code=400)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > MEDIA_UPLOAD_MAX_BYTES:
            return JSONResponse({"error": "This upload is larger than the relay's limit."}, status_code=413)
        body = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        size = 0
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MEDIA_UPLOAD_MAX_BYTES:
                    body.close()
                    return JSONResponse({"error": "This upload is larger than the relay's limit."}, status_code=413)
                body.write(chunk)
        except Exception:                                          # noqa: BLE001 - the Studio hung up mid-upload
            body.close()
            return JSONResponse({"error": "The upload didn't arrive. Try again."}, status_code=400)
        if not size:
            body.close()
            return JSONResponse({"error": "The upload was empty."}, status_code=400)
        status, reply, kind = await run_in_threadpool(
            forward_upload, user["id"], path, body, request.headers.get("content-type") or "application/octet-stream",
            kie_authorization)
        return Response(reply, status_code=status, media_type=kind,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/stream/{what}")
    def stream(what: str, request: Request, t: str = ""):
        """A site link from site_link(): the token stands in for the account key (the link is followed without it)."""
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        if what not in ("pack", "release") or read_stream_token(t, what) is None:
            raise HTTPException(403, "This download link expired. Try again.")
        if what == "pack":
            return stream_private(request, SHORTS_PACK_KEY, "shorts-assets.zip", headers)
        if not release_ready():
            raise HTTPException(503, "The studio download is being prepared. Please try again shortly.")
        return stream_private(request, RELEASE_KEY, "ChannelRecipe-local-studio-%s.zip" % VERSION, headers)

    @router.get("/shorts-pack/file")
    def shorts_pack_file(request: Request, user=Depends(access)):
        """The Shorts asset pack through this site (the updater's fallback when the storage host can't be reached)."""
        return stream_private(request, SHORTS_PACK_KEY, "shorts-assets.zip",
                              {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/download")
    def download(request: Request, user=Depends(access)):
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        milestones.record(user["id"], milestones.DOWNLOADED)
        local = os.getenv("FRONTIER_RELEASE_PATH", "")
        filename = "ChannelRecipe-local-studio-%s.zip" % VERSION
        if local and Path(local).is_file():
            return FileResponse(local, media_type="application/zip", filename=filename, headers=headers)
        from webapp import storage
        if not storage.is_remote() or not release_ready():
            raise HTTPException(503, "The studio download is being prepared. Please try again shortly.")
        # Hand the browser a short-lived signed link to the private object rather
        # than streaming ~250 MB through the web process: a launch-day crowd of
        # downloads must not tie up the site. The object stays private; the
        # link is minted per signed-in, entitled request and expires quickly
        # (an in-progress transfer is unaffected by expiry).
        if storage_blocked(user["id"], "release"):
            link = site_link(request, user["id"], "release")
            if link:
                return RedirectResponse(link, status_code=302, headers=headers)
        try:
            url = storage._get_client().generate_presigned_url("get_object", Params={
                "Bucket": storage._spaces_creds()[2], "Key": RELEASE_KEY,
                "ResponseContentDisposition": 'attachment; filename="%s"' % filename,
                "ResponseContentType": "application/zip"}, ExpiresIn=DOWNLOAD_LINK_SECONDS)
        except Exception:
            raise HTTPException(503, "The studio download is being prepared. Please try again shortly.")
        return RedirectResponse(url, status_code=302, headers=headers)

    return router
