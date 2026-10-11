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
from webapp import frontier_providers as providers

VERSION = "5.3-admin.4"
CATALOG = Path(__file__).resolve().parent / "frontier" / "catalog.json"
RELEASE_KEY = "private/frontier/channelrecipe-5.3-admin.4.zip"
GEMINI_MODEL = "gemini-3.8-flash"
_slots = threading.BoundedSemaphore(2)
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


def require_admin_user(user: dict | None) -> dict:
    if not user:
        raise HTTPException(401, "Sign in to connect your studio.")
    admins = {str(e).strip().lower() for e in getattr(config, "ADMIN_EMAILS", []) if e}
    if str(user.get("email", "")).strip().lower() not in admins:
        raise HTTPException(403, "Local studio is available to admins during this preview.")
    return user


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
                         "context_json": "TEXT NOT NULL DEFAULT ''"}
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
        _schema_ready.add(schema_key)


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
    with db._conn() as conn:
        recovery = conn.execute(db._q("""SELECT COUNT(*) AS total FROM frontier_requests r
            JOIN frontier_projects p ON p.id=r.project_id WHERE p.user_id=? AND r.status='needs_review'"""),
            (user["id"],)).fetchone()["total"]
    return {"access": "admin_preview", "version": VERSION,
            "brain": {"connected": bool(c and decrypt_secret(c["key_enc"])),
                      "provider": c["provider"] if c else "gemini", "model": c["model"] if c else GEMINI_MODEL},
            "providers": providers.PROVIDERS,
            "recovery_requests": recovery,
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
    provider: Literal["gemini", "kie", "atlas"] = "gemini"
    model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9./_\-]+$", max_length=100)


def save_key(user: dict, req: BrainKey) -> dict:
    require_admin_user(user)
    _vault()  # Fail before any provider lookup if secure storage is unavailable.
    key = req.api_key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(400, "Paste the complete key without spaces.")
    info = providers.PROVIDERS[req.provider]
    model = req.model or info["default_brain"]
    if req.provider == "gemini":
        import re
        valid = re.fullmatch(r"gemini-[a-zA-Z0-9.\-]+", model)
    else:
        valid = model in {m["id"] for m in info["brain_models"]}
    if not valid:
        raise HTTPException(400, "Choose one of this account's supported brain models.")
    # Read-only account/model lookup. Never generates content or spends tokens.
    providers.check_key(req.provider, key, model)
    initialize()
    with db._conn() as conn:
        conn.execute(db._q("""INSERT INTO frontier_credentials (user_id,key_enc,model,updated_at,provider)
            VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            key_enc=excluded.key_enc,model=excluded.model,updated_at=excluded.updated_at,provider=excluded.provider"""),
            (user["id"], encrypt_secret(key), model, time.time(), req.provider))
    return {"connected": True, "provider": req.provider, "model": model,
            "message": f"Connected. Brain calls use your {info['label']} account."}


class ProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    style: str = Field(default="jung", min_length=1, max_length=80, pattern=r"^[a-z0-9_\-]+$")
    max_calls: int = Field(default=120, ge=1, le=500)


def create_project(user: dict, req: ProjectRequest) -> dict:
    require_admin_user(user)
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


def _account_hash(c, key):
    return hashlib.sha256((c["provider"] + "\0" + c["model"] + "\0" + key).encode()).hexdigest()


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
    require_admin_user(user)
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


def generate(user: dict, req: BrainRequest) -> dict:
    """Compatibility relay for owners only. Never expose this tool to paid users."""
    require_admin_user(user)
    project(user, req.project_id)
    c = credential(user["id"])
    key = decrypt_secret(c["key_enc"]) if c else ""
    if not key:
        raise HTTPException(409, "Reconnect your model account in Local studio.")
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
    attempt = 1
    context = json.dumps({"provider": c["provider"], "model": c["model"], "kind": req.kind, "max_tokens": req.max_tokens,
                          "prompt_chars": len(req.prompt), "image_count": len(req.images), "image_bytes": size,
                          "thinking_level": "low" if c["provider"] == "kie" or providers.thinking_config(c["model"]) else "model_default"})
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
                if existing["status"] != "retry_allowed":
                    raise HTTPException(409, "This request stopped or is still running. Open Local studio in CR to review its status and recovery options.")
                approval = conn.execute(db._q("SELECT * FROM frontier_retries WHERE request_id=?"), (req.request_id,)).fetchone()
                if not approval or approval["used_at"] or approval["attempt"] != existing["attempts"]:
                    raise HTTPException(409, "This request has no unused recovery retry.")
                if approval["account_hash"] != _account_hash(c, key):
                    raise HTTPException(409, "Reconnect the model account used to allow this retry before resuming.")
                attempt = existing["attempts"] + 1
            if row["calls"] >= row["max_calls"]:
                raise HTTPException(409, "This project reached its brain request limit. Review its usage before starting more work.")
            conn.execute(db._q("UPDATE frontier_projects SET calls=calls+1,status='running',updated_at=? WHERE id=?"), (time.time(), req.project_id))
            if existing:
                errors = _errors(existing["error_json"])
                if not errors:
                    errors = [{"attempt": 1, "code": "unrecorded", "message": "The original error was not retained by the earlier preview."}]
                conn.execute(db._q("UPDATE frontier_requests SET status='running',attempts=?,context_json=?,error_json=? WHERE id=?"),
                             (attempt, context, json.dumps(errors), req.request_id))
                conn.execute(db._q("UPDATE frontier_retries SET used_at=? WHERE request_id=?"), (time.time(), req.request_id))
            else:
                conn.execute(db._q("INSERT INTO frontier_requests (id,project_id,fingerprint,status,created_at,context_json) VALUES (?,?,?,?,?,?)"),
                             (req.request_id, req.project_id, fingerprint, "running", time.time(), context))
            reserved = True
        text, incoming, outgoing = providers.generate(c["provider"], key, c["model"], parts, req.kind, req.max_tokens)
        result = {"text": text, "provider": c["provider"], "model": c["model"], "input_tokens": incoming,
                  "output_tokens": outgoing, "request_id": req.request_id, "version": VERSION}
        with db._conn() as conn:
            conn.execute(db._q("UPDATE frontier_requests SET status='complete',result_json=? WHERE id=?"),
                         (json.dumps(result), req.request_id))
            conn.execute(db._q("""UPDATE frontier_projects SET input_tokens=input_tokens+?,output_tokens=output_tokens+?,
                status=CASE WHEN EXISTS (SELECT 1 FROM frontier_requests WHERE project_id=? AND status='needs_review')
                THEN 'needs_review' ELSE 'ready' END,updated_at=? WHERE id=?"""),
                         (incoming, outgoing, req.project_id, time.time(), req.project_id))
        return result
    except HTTPException as error:
        if reserved:
            _record_failure(req, attempt, error)
        raise
    except Exception as error:
        if reserved:
            _record_failure(req, attempt, error)
        raise HTTPException(502, "CR could not complete this request. Open Local studio in CR to review its recorded status.") from None
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

    @router.get("/projects/{project_id}/requests")
    def get_requests(project_id: str, user=Depends(access)):
        return request_history(user, project_id)

    @router.post("/projects/{project_id}/requests/{request_id}/retry")
    def retry(project_id: str, request_id: str, req: RetryApproval, user=Depends(access)):
        return approve_retry(user, project_id, request_id, req)

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
