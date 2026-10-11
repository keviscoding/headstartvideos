"""Admin-only Frontier integration. Never publish the preview engine to public users.

This preview preserves the original engine while routing its model calls through
CR on an admin's own key. Public release requires the named-stage/private-prompt
split described in docs/frontier-admin.md; the generic relay must remain admin-only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Literal

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
from webapp import database as db
from cryptography.fernet import Fernet, InvalidToken

VERSION = "5.3-admin.1"
CATALOG = Path(__file__).resolve().parent / "frontier" / "catalog.json"
RELEASE_KEY = "private/frontier/channelrecipe-5.3-admin.1.zip"
GEMINI_MODEL = "gemini-3.8-flash"
_slots = threading.BoundedSemaphore(2)
_schema_lock = threading.Lock()


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


def require_admin_user(user: dict | None) -> dict:
    if not user:
        raise HTTPException(401, "Sign in to connect your studio.")
    admins = {str(e).strip().lower() for e in getattr(config, "ADMIN_EMAILS", []) if e}
    if str(user.get("email", "")).strip().lower() not in admins:
        raise HTTPException(403, "Local studio is available to admins during this preview.")
    return user


def initialize():
    # Dedicated additive tables; no changes to existing Stripe or render schemas.
    with _schema_lock, db._conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS frontier_credentials (
            user_id INTEGER PRIMARY KEY, key_enc TEXT NOT NULL, model TEXT NOT NULL,
            updated_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS frontier_projects (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
            style TEXT NOT NULL, version TEXT NOT NULL, max_calls INTEGER NOT NULL,
            calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ready',
            created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS frontier_requests (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL)""")


def catalog() -> list[dict]:
    return json.loads(CATALOG.read_text())


def credential(user_id: int) -> dict | None:
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM frontier_credentials WHERE user_id=?"), (user_id,)).fetchone()
    return dict(row) if row else None


def status(user: dict) -> dict:
    require_admin_user(user)
    c = credential(user["id"])
    return {"access": "admin_preview", "version": VERSION,
            "brain": {"connected": bool(c and decrypt_secret(c["key_enc"])),
                      "provider": "gemini", "model": c["model"] if c else GEMINI_MODEL},
            "download_ready": release_ready(), "styles": catalog(),
            "local_url": "http://127.0.0.1:7860/cr-setup",
            "generation_billing": "Your provider accounts", "rendering": "Your computer",
            "preview_note": "Admin preview preserves the original Frontier engine. Public release remains disabled."}


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
    model: str = Field(default=GEMINI_MODEL, pattern=r"^gemini-[a-zA-Z0-9.\-]+$", max_length=100)


def save_key(user: dict, req: BrainKey) -> dict:
    require_admin_user(user)
    _vault()  # Fail before any provider lookup if secure storage is unavailable.
    key = req.api_key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(400, "Paste the complete key without spaces.")
    try:
        # Auth/model lookup: does not generate content or spend generation tokens.
        res = requests.get("https://generativelanguage.googleapis.com/v1beta/models/" + req.model,
                           headers={"x-goog-api-key": key}, timeout=(10, 20))
    except requests.RequestException:
        raise HTTPException(503, "Google could not be reached. Your existing key was kept; try again.")
    if res.status_code != 200:
        message = {401: "Google did not accept this key.", 403: "Enable Gemini API access for this key.",
                   404: "This model is not available on your Google project.",
                   429: "Google is rate limiting this project. Try again after its limit resets."}
        raise HTTPException(400, message.get(res.status_code, "Google could not verify this key. Try again."))
    if "generateContent" not in res.json().get("supportedGenerationMethods", []):
        raise HTTPException(400, "Choose a Gemini model that can generate text and understand images.")
    initialize()
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO frontier_credentials (user_id,key_enc,model,updated_at)
            VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            key_enc=excluded.key_enc,model=excluded.model,updated_at=excluded.updated_at"""),
            (user["id"], encrypt_secret(key), req.model, time.time()))
    return {"connected": True, "model": req.model, "message": "Connected. Model calls use your Google project."}


class ProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    style: str = Field(default="jung", min_length=1, max_length=80, pattern=r"^[a-z0-9_\-]+$")
    max_calls: int = Field(default=120, ge=1, le=500)


def create_project(user: dict, req: ProjectRequest) -> dict:
    require_admin_user(user)
    if not credential(user["id"]):
        raise HTTPException(409, "Connect your Gemini account in Local studio first.")
    project_id = "crv_" + secrets.token_hex(12)
    now = time.time()
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO frontier_projects
            (id,user_id,title,style,version,max_calls,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)"""),
            (project_id, user["id"], req.title.strip(), req.style, VERSION, req.max_calls, now, now))
    return project(user, project_id)


def project(user: dict, project_id: str) -> dict:
    require_admin_user(user)
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


def generate(user: dict, req: BrainRequest) -> dict:
    """Compatibility relay for owners only. Never expose this tool to paid users."""
    require_admin_user(user)
    project(user, req.project_id)
    c = credential(user["id"])
    key = decrypt_secret(c["key_enc"]) if c else ""
    if not key:
        raise HTTPException(409, "Reconnect your Gemini key in Local studio.")
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
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "The brain is busy. Your local runner will retry shortly.", headers={"Retry-After": "10"})
    reserved = False
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
                if existing["project_id"] != req.project_id or existing["fingerprint"] != fingerprint:
                    raise HTTPException(409, "This request ID belongs to different work.")
                if existing["status"] == "complete":
                    return json.loads(existing["result_json"])
                raise HTTPException(409, "This request is already recorded. Check project history before retrying it.")
            if row["calls"] >= row["max_calls"]:
                raise HTTPException(409, "This project reached its brain request limit. Review its usage before starting more work.")
            conn.execute(db._q("UPDATE frontier_projects SET calls=calls+1,status='running',updated_at=? WHERE id=?"), (time.time(), req.project_id))
            conn.execute(db._q("INSERT INTO frontier_requests (id,project_id,fingerprint,status,created_at) VALUES (?,?,?,?,?)"),
                         (req.request_id, req.project_id, fingerprint, "running", time.time()))
            reserved = True
        payload = {"contents": [{"role": "user", "parts": parts}],
                   "generationConfig": {"maxOutputTokens": req.max_tokens}}
        if req.kind == "research":
            payload["tools"] = [{"google_search": {}}]
        try:
            res = requests.post("https://generativelanguage.googleapis.com/v1beta/models/" + c["model"] + ":generateContent",
                                headers={"x-goog-api-key": key}, json=payload, timeout=(15, 180))
            if res.status_code != 200:
                # Never return upstream bodies, which can contain credentials or private prompts.
                raise HTTPException(502, "Google could not complete this request (HTTP %s). No CR key was used." % res.status_code)
            data = res.json()
            candidate = (data.get("candidates") or [{}])[0]
            if candidate.get("finishReason") not in (None, "STOP"):
                raise HTTPException(502, "Google stopped before completing this answer. Review this project's usage before restarting it.")
            text = "".join(p.get("text", "") for cand in data.get("candidates", [])[:1]
                           for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
            if not text.strip():
                raise HTTPException(502, "The model returned no usable answer. Check your project before retrying.")
            usage = data.get("usageMetadata", {})
            incoming = int(usage.get("promptTokenCount", 0))
            outgoing = int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0))
            result = {"text": text, "model": c["model"], "input_tokens": incoming,
                      "output_tokens": outgoing, "request_id": req.request_id, "version": VERSION}
        except requests.RequestException:
            raise HTTPException(502, "The provider connection was interrupted. Check the recorded request before retrying.")
        except (ValueError, TypeError):
            raise HTTPException(502, "Google returned an unreadable response.")
        with db._conn() as conn:
            conn.execute(db._q("UPDATE frontier_requests SET status='complete',result_json=? WHERE id=?"),
                         (json.dumps(result), req.request_id))
            conn.execute(db._q("UPDATE frontier_projects SET input_tokens=input_tokens+?,output_tokens=output_tokens+?,status='ready',updated_at=? WHERE id=?"),
                         (incoming, outgoing, time.time(), req.project_id))
        return result
    except HTTPException:
        if reserved:
            with db._conn() as conn:
                conn.execute(db._q("UPDATE frontier_requests SET status='needs_review' WHERE id=? AND project_id=? AND status='running'"),
                             (req.request_id, req.project_id))
                conn.execute(db._q("UPDATE frontier_projects SET status='needs_review',updated_at=? WHERE id=?"), (time.time(), req.project_id))
        raise
    finally:
        _slots.release()


def make_router(session_user):
    router = APIRouter(prefix="/api/frontier", tags=["Admin local studio"])

    def access(request: Request):
        authorization = request.headers.get("authorization", "")
        if authorization:
            if not authorization.startswith("Bearer cr_mcp_"):
                raise HTTPException(401, "Reconnect your ChannelRecipe account.")
            user = db.get_user_by_mcp_api_key(authorization[7:])
        else:
            user = session_user(request)
        return require_admin_user(user)

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

    @router.post("/brain")
    def brain(req: BrainRequest, user=Depends(access)):
        return generate(user, req)

    @router.get("/download")
    def download(user=Depends(access)):
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        local = os.getenv("FRONTIER_RELEASE_PATH", "")
        filename = "ChannelRecipe-local-studio-%s.zip" % VERSION
        if local and Path(local).is_file():
            return FileResponse(local, media_type="application/zip", filename=filename, headers=headers)
        from webapp import storage
        if not storage.is_remote():
            raise HTTPException(503, "The studio download is being prepared. Please try again shortly.")
        try:
            obj = storage._get_client().get_object(Bucket=storage._spaces_creds()[2], Key=RELEASE_KEY)
        except Exception:
            raise HTTPException(503, "The studio download is being prepared. Please try again shortly.")
        body = obj["Body"]
        def chunks():
            try:
                yield from body.iter_chunks(chunk_size=1024 * 1024)
            finally:
                body.close()
        headers["Content-Disposition"] = 'attachment; filename="%s"' % filename
        headers["Content-Length"] = str(obj["ContentLength"])
        return StreamingResponse(chunks(), media_type="application/zip", headers=headers)

    return router
