"""
OAuth 2.1 authorization server for ChannelRecipe MCP (Claude.ai connectors).

Supports Dynamic Client Registration, PKCE authorization_code, and token
verification. Users authorize by pasting their MCP API key (or an existing
ChannelRecipe session cookie).
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from pydantic import AnyHttpUrl
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

MCP_SCOPE = "mcp"
AUTH_CODE_TTL = 300
ACCESS_TTL = 3600 * 12
REFRESH_TTL = 3600 * 24 * 30


def public_base_url() -> str:
    import os
    configured = (
        os.getenv("PUBLIC_BASE_URL")
        or os.getenv("BASE_URL")
        or ""
    ).strip().rstrip("/")
    if configured:
        return configured
    return "https://channelrecipe.com"


def mcp_resource_url() -> str:
    return f"{public_base_url().rstrip('/')}/mcp"


def oauth_login_url() -> str:
    return f"{public_base_url().rstrip('/')}/oauth/mcp/login"


# ---------------------------------------------------------------------------
# DB helpers (Postgres or SQLite via webapp.database)
# ---------------------------------------------------------------------------

def _ensure_oauth_tables() -> None:
    from webapp.database import IS_PG, _conn

    ddl = """
    CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
        client_id   TEXT PRIMARY KEY,
        data_json   TEXT NOT NULL,
        created_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mcp_oauth_codes (
        code        TEXT PRIMARY KEY,
        data_json   TEXT NOT NULL,
        expires_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mcp_oauth_tokens (
        token       TEXT PRIMARY KEY,
        kind        TEXT NOT NULL,
        data_json   TEXT NOT NULL,
        expires_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mcp_oauth_pending (
        state       TEXT PRIMARY KEY,
        data_json   TEXT NOT NULL,
        expires_at  REAL NOT NULL
    );
    """
    with _conn() as conn:
        if IS_PG:
            with conn.cursor() as cur:
                for stmt in ddl.strip().split(";"):
                    s = stmt.strip()
                    if s:
                        cur.execute(s)
        else:
            conn.executescript(ddl)


def _db_put(table: str, key_col: str, key: str, data: dict, expires_at: float | None = None) -> None:
    from webapp.database import _conn, _q

    payload = json.dumps(data)
    now = time.time()
    with _conn() as conn:
        cur = conn.cursor()
        if table == "mcp_oauth_clients":
            cur.execute(
                _q(
                    "INSERT INTO mcp_oauth_clients (client_id, data_json, created_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(client_id) DO UPDATE SET data_json = excluded.data_json"
                ),
                (key, payload, now),
            )
        elif table == "mcp_oauth_codes":
            cur.execute(
                _q(
                    "INSERT INTO mcp_oauth_codes (code, data_json, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(code) DO UPDATE SET data_json = excluded.data_json, expires_at = excluded.expires_at"
                ),
                (key, payload, float(expires_at or 0)),
            )
        elif table == "mcp_oauth_tokens":
            cur.execute(
                _q(
                    "INSERT INTO mcp_oauth_tokens (token, kind, data_json, expires_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(token) DO UPDATE SET kind = excluded.kind, data_json = excluded.data_json, "
                    "expires_at = excluded.expires_at"
                ),
                (key, data.get("_kind") or "access", payload, float(expires_at or 0)),
            )
        elif table == "mcp_oauth_pending":
            cur.execute(
                _q(
                    "INSERT INTO mcp_oauth_pending (state, data_json, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(state) DO UPDATE SET data_json = excluded.data_json, expires_at = excluded.expires_at"
                ),
                (key, payload, float(expires_at or 0)),
            )


def _db_get(table: str, key: str) -> dict | None:
    from webapp.database import _conn, _q

    with _conn() as conn:
        cur = conn.cursor()
        if table == "mcp_oauth_clients":
            cur.execute(_q("SELECT data_json FROM mcp_oauth_clients WHERE client_id = ?"), (key,))
        elif table == "mcp_oauth_codes":
            cur.execute(
                _q("SELECT data_json, expires_at FROM mcp_oauth_codes WHERE code = ?"),
                (key,),
            )
        elif table == "mcp_oauth_tokens":
            cur.execute(
                _q("SELECT data_json, expires_at FROM mcp_oauth_tokens WHERE token = ?"),
                (key,),
            )
        elif table == "mcp_oauth_pending":
            cur.execute(
                _q("SELECT data_json, expires_at FROM mcp_oauth_pending WHERE state = ?"),
                (key,),
            )
        else:
            return None
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        if "expires_at" in d and d["expires_at"] and float(d["expires_at"]) < time.time():
            _db_delete(table, key)
            return None
        try:
            return json.loads(d["data_json"])
        except Exception:
            return None


def _db_delete(table: str, key: str) -> None:
    from webapp.database import _conn, _q

    col = {
        "mcp_oauth_clients": "client_id",
        "mcp_oauth_codes": "code",
        "mcp_oauth_tokens": "token",
        "mcp_oauth_pending": "state",
    }.get(table)
    if not col:
        return
    with _conn() as conn:
        conn.cursor().execute(_q(f"DELETE FROM {table} WHERE {col} = ?"), (key,))


class ChannelRecipeOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """In-process OAuth AS backed by the ChannelRecipe DB."""

    def __init__(self) -> None:
        _ensure_oauth_tables()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = _db_get("mcp_oauth_clients", client_id)
        if not raw:
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("No client_id provided")
        _db_put(
            "mcp_oauth_clients",
            "client_id",
            client_info.client_id,
            client_info.model_dump(mode="json"),
        )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        state = params.state or secrets.token_hex(16)
        pending = {
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "redirect_uri_provided_explicitly": bool(params.redirect_uri_provided_explicitly),
            "client_id": client.client_id,
            "resource": params.resource,
            "scopes": list(params.scopes or [MCP_SCOPE]),
            "client_state": params.state,
        }
        _db_put("mcp_oauth_pending", "state", state, pending, expires_at=time.time() + AUTH_CODE_TTL)
        return f"{oauth_login_url()}?{urlencode({'state': state})}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raw = _db_get("mcp_oauth_codes", authorization_code)
        if not raw:
            return None
        try:
            return AuthorizationCode.model_validate(raw)
        except Exception:
            return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not client.client_id:
            raise ValueError("No client_id")
        stored = _db_get("mcp_oauth_codes", authorization_code.code)
        if not stored:
            raise ValueError("Invalid authorization code")
        _db_delete("mcp_oauth_codes", authorization_code.code)

        access = f"cr_oa_{secrets.token_urlsafe(32)}"
        refresh = f"cr_or_{secrets.token_urlsafe(32)}"
        now = int(time.time())
        access_row = AccessToken(
            token=access,
            client_id=client.client_id,
            scopes=authorization_code.scopes or [MCP_SCOPE],
            expires_at=now + ACCESS_TTL,
            resource=authorization_code.resource,
            subject=authorization_code.subject,
        )
        refresh_row = RefreshToken(
            token=refresh,
            client_id=client.client_id,
            scopes=authorization_code.scopes or [MCP_SCOPE],
            expires_at=now + REFRESH_TTL,
        )
        access_data = access_row.model_dump(mode="json")
        access_data["_kind"] = "access"
        refresh_data = refresh_row.model_dump(mode="json")
        refresh_data["_kind"] = "refresh"
        refresh_data["subject"] = authorization_code.subject
        refresh_data["resource"] = authorization_code.resource
        _db_put("mcp_oauth_tokens", "token", access, access_data, expires_at=access_row.expires_at)
        _db_put("mcp_oauth_tokens", "token", refresh, refresh_data, expires_at=refresh_row.expires_at)

        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            scope=" ".join(authorization_code.scopes or [MCP_SCOPE]),
            refresh_token=refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        token = (token or "").strip()
        if not token:
            return None
        # Claude Desktop / Settings JSON still send the MCP API key as Bearer
        if token.startswith("cr_mcp_"):
            from webapp.database import get_user_by_mcp_api_key

            user = get_user_by_mcp_api_key(token)
            if not user:
                return None
            return AccessToken(
                token=token,
                client_id="mcp_api_key",
                scopes=[MCP_SCOPE],
                expires_at=None,
                subject=str(user["id"]),
            )
        raw = _db_get("mcp_oauth_tokens", token)
        if not raw or raw.get("_kind") == "refresh":
            return None
        try:
            return AccessToken.model_validate(
                {k: v for k, v in raw.items() if not k.startswith("_")}
            )
        except Exception:
            return None

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        raw = _db_get("mcp_oauth_tokens", refresh_token)
        if not raw or raw.get("_kind") != "refresh":
            return None
        try:
            return RefreshToken.model_validate(
                {k: v for k, v in raw.items() if k in ("token", "client_id", "scopes", "expires_at")}
            )
        except Exception:
            return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        raw = _db_get("mcp_oauth_tokens", refresh_token.token)
        if not raw or raw.get("_kind") != "refresh":
            raise ValueError("Invalid refresh token")
        subject = raw.get("subject")
        resource = raw.get("resource")
        use_scopes = scopes or refresh_token.scopes or [MCP_SCOPE]
        access = f"cr_oa_{secrets.token_urlsafe(32)}"
        now = int(time.time())
        access_row = AccessToken(
            token=access,
            client_id=client.client_id or refresh_token.client_id,
            scopes=use_scopes,
            expires_at=now + ACCESS_TTL,
            resource=resource,
            subject=subject,
        )
        access_data = access_row.model_dump(mode="json")
        access_data["_kind"] = "access"
        _db_put("mcp_oauth_tokens", "token", access, access_data, expires_at=access_row.expires_at)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            scope=" ".join(use_scopes),
            refresh_token=refresh_token.token,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        tok = getattr(token, "token", None) or ""
        if tok:
            _db_delete("mcp_oauth_tokens", tok)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Kept for callers that expect TokenVerifier; SDK uses load_access_token."""
        return await self.load_access_token(token)

    # --- login helpers (used by FastAPI routes) ---

    def get_pending(self, state: str) -> dict | None:
        return _db_get("mcp_oauth_pending", state)

    def complete_login(self, state: str, user_id: int) -> str:
        pending = _db_get("mcp_oauth_pending", state)
        if not pending:
            raise HTTPException(400, "Invalid or expired login state. Start Connect again from Claude.")
        code = f"cr_ac_{secrets.token_urlsafe(24)}"
        auth_code = AuthorizationCode(
            code=code,
            client_id=str(pending["client_id"]),
            redirect_uri=AnyHttpUrl(pending["redirect_uri"]),
            redirect_uri_provided_explicitly=bool(pending.get("redirect_uri_provided_explicitly")),
            expires_at=time.time() + AUTH_CODE_TTL,
            scopes=list(pending.get("scopes") or [MCP_SCOPE]),
            code_challenge=str(pending["code_challenge"]),
            resource=pending.get("resource"),
            subject=str(user_id),
        )
        _db_put(
            "mcp_oauth_codes",
            "code",
            code,
            auth_code.model_dump(mode="json"),
            expires_at=auth_code.expires_at,
        )
        _db_delete("mcp_oauth_pending", state)
        kwargs: dict[str, Any] = {"code": code}
        if pending.get("client_state"):
            kwargs["state"] = pending["client_state"]
        return construct_redirect_uri(str(pending["redirect_uri"]), **kwargs)


oauth_provider = ChannelRecipeOAuthProvider()


def login_page_html(
    *,
    state: str,
    error: str = "",
    logged_in_email: str = "",
) -> str:
    err = f'<p style="color:#b91c1c;margin:0 0 12px;">{error}</p>' if error else ""
    if logged_in_email:
        account_block = f"""
      <p><strong>ChannelRecipe account required.</strong> You're signed in as
      <span style="color:var(--ink)">{logged_in_email}</span>.</p>
      <form method="post" action="/oauth/mcp/login">
        <input type="hidden" name="state" value="{state}"/>
        <input type="hidden" name="use_session" value="1"/>
        <button type="submit">Authorize Claude as {logged_in_email}</button>
      </form>
      <p class="alt">Not you? <a href="/app">Sign in with a different account</a>,
      or paste an MCP API key below.</p>
      <form method="post" action="/oauth/mcp/login" style="margin-top:18px">
        <input type="hidden" name="state" value="{state}"/>
        <label for="api_key">Or paste MCP API key</label>
        <input id="api_key" name="api_key" type="password" autocomplete="off"
               placeholder="cr_mcp_… from Settings → Claude / MCP"/>
        <button type="submit" style="background:#0f172a">Authorize with API key</button>
      </form>
"""
    else:
        account_block = f"""
      <p><strong>A free ChannelRecipe account is required</strong> before Claude
      can use your niche library. Random URL visitors cannot connect without one.</p>
      <p class="alt" style="margin:12px 0 0">
        <a href="/app" target="_blank" rel="noopener">Create account / sign in</a>
        → Settings → copy MCP API key → paste below.
      </p>
      <form method="post" action="/oauth/mcp/login">
        <input type="hidden" name="state" value="{state}"/>
        <label for="api_key">MCP API key</label>
        <input id="api_key" name="api_key" type="password" autocomplete="off" required
               placeholder="cr_mcp_… from Settings → Claude / MCP"/>
        <button type="submit">Authorize Claude</button>
      </form>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Connect ChannelRecipe to Claude</title>
  <style>
    :root {{ --ink:#0f172a; --muted:#64748b; --accent:#5b4dff; --bg:#f8fafc; --card:#fff; }}
    body {{ margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
            background:linear-gradient(160deg,#eef2ff,#f8fafc 40%,#fff); color:var(--ink); min-height:100vh; }}
    main {{ max-width:440px; margin:10vh auto; padding:24px; }}
    .card {{ background:var(--card); border:1px solid #e2e8f0; border-radius:16px; padding:28px;
             box-shadow:0 12px 40px rgba(15,23,42,.06); }}
    h1 {{ font-size:22px; margin:0 0 8px; }}
    p {{ color:var(--muted); line-height:1.5; font-size:14px; }}
    label {{ display:block; font-size:13px; font-weight:600; margin:16px 0 6px; }}
    input {{ width:100%; box-sizing:border-box; padding:12px 14px; border:1px solid #cbd5e1;
             border-radius:10px; font-size:14px; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }}
    button {{ margin-top:16px; width:100%; padding:12px 16px; border:0; border-radius:10px;
              background:var(--accent); color:#fff; font-weight:700; font-size:15px; cursor:pointer; }}
    .alt {{ margin-top:14px; font-size:13px; }}
    a {{ color:var(--accent); }}
  </style>
</head>
<body>
  <main>
    <div class="card">
      <h1>Connect ChannelRecipe</h1>
      {err}
      {account_block}
    </div>
  </main>
</body>
</html>"""


async def render_login(request: Request) -> Response:
    state = (request.query_params.get("state") or "").strip()
    if not state or not oauth_provider.get_pending(state):
        raise HTTPException(400, "Invalid or expired login link. Click Connect again in Claude.")

    # Silent approve when already logged into ChannelRecipe in this browser —
    # fastest path for returning users. Strangers without a session still must
    # create an account and paste an MCP API key.
    token = request.cookies.get("session")
    if token:
        from webapp.database import get_session_user

        user = get_session_user(token)
        if user:
            redirect = oauth_provider.complete_login(state, int(user["id"]))
            return RedirectResponse(url=redirect, status_code=302)

    return HTMLResponse(login_page_html(state=state, logged_in_email=""))


async def handle_login_post(request: Request) -> Response:
    form = await request.form()
    state = str(form.get("state") or "").strip()
    api_key = str(form.get("api_key") or "").strip()
    use_session = str(form.get("use_session") or "").strip() in ("1", "true", "yes")
    if not state or not oauth_provider.get_pending(state):
        raise HTTPException(400, "Invalid or expired login state.")

    from webapp.database import get_user_by_mcp_api_key, ensure_user_mcp_api_key, get_session_user

    user = None
    if api_key:
        user = get_user_by_mcp_api_key(api_key)
    if not user and (use_session or not api_key):
        sess = request.cookies.get("session")
        if sess:
            user = get_session_user(sess)
            if user:
                ensure_user_mcp_api_key(int(user["id"]))

    if not user:
        return HTMLResponse(
            login_page_html(
                state=state,
                error=(
                    "Account required. Sign in at channelrecipe.com, then copy your "
                    "MCP API key from Settings → Claude / MCP."
                ),
            ),
            status_code=401,
        )

    redirect = oauth_provider.complete_login(state, int(user["id"]))
    return RedirectResponse(url=redirect, status_code=302)
