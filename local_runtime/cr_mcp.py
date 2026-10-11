"""Authenticated CR production tools over MCP; no model generation here."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

import requests

HOME = Path(__file__).resolve().parent
PROTOCOL = "2025-03-26"
_state = threading.local()
REQUIRED_TOOLS = {"local_studio_prepare", "local_studio_begin", "local_studio_authorize_stage",
                  "local_studio_finish_stage", "local_studio_progress", "local_studio_finish_project", "local_studio_retry_stage"}


def _connection(settings):
    base = str(settings.get("url", "https://channelrecipe.com")).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
        raise RuntimeError("Connect to ChannelRecipe using HTTPS in Setup & accounts.")
    key = settings.get("key", "")
    if not isinstance(key, str) or not key.startswith("cr_mcp_"):
        raise RuntimeError("Connect your ChannelRecipe account in Setup & accounts.")
    return base + "/mcp", key


def _message(url, headers, payload):
    # No automatic retries: stage reservations must never create duplicate work.
    try:
        with requests.post(url, headers=headers, json=payload, stream=True,
                           timeout=(10, 45), allow_redirects=False) as response:
            if response.status_code in {401, 403}:
                raise RuntimeError("Reconnect your ChannelRecipe account in Setup & accounts.")
            if response.status_code not in {200, 202}:
                raise RuntimeError("ChannelRecipe's workflow connection needs attention. Check setup and try again.")
            session = response.headers.get("Mcp-Session-Id")
            if payload.get("id") is None:
                return None, session
            if "text/event-stream" in response.headers.get("Content-Type", ""):
                event, total = [], 0
                for line in response.iter_lines(decode_unicode=True):
                    if isinstance(line, bytes):
                        line = line.decode("utf-8")
                    total += len(line or "")
                    if total > 2_000_000:
                        raise RuntimeError("ChannelRecipe returned an oversized workflow response.")
                    if line and line.startswith("data:"):
                        event.append(line[5:].lstrip())
                    if not line and event:
                        data = json.loads("\n".join(event))
                        event = []
                        if data.get("id") == payload["id"]:
                            return data, session
                if event:
                    data = json.loads("\n".join(event))
                    if data.get("id") == payload["id"]:
                        return data, session
                raise RuntimeError("ChannelRecipe's workflow response was interrupted. Your work is saved.")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise RuntimeError("ChannelRecipe returned an oversized workflow response.")
            return json.loads(data), session
    except (requests.RequestException, ValueError, UnicodeError):
        raise RuntimeError("Could not connect to ChannelRecipe's workflow tools. Check setup and your connection.") from None


def _session(settings):
    url, key = _connection(settings)
    identity = hashlib.sha256((url + key).encode()).hexdigest()
    if getattr(_state, "identity", None) == identity:
        return url, _state.headers
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    message, session = _message(url, headers, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                   "clientInfo": {"name": "ChannelRecipe local studio", "version": "5.3-admin.11"}}})
    result = (message or {}).get("result") or {}
    if not isinstance(message, dict) or message.get("error") or not result.get("protocolVersion"):
        raise RuntimeError("ChannelRecipe's workflow connection could not initialize.")
    headers["MCP-Protocol-Version"] = result["protocolVersion"]
    if session:
        headers["Mcp-Session-Id"] = session
    _message(url, headers, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    _state.identity, _state.headers = identity, headers
    return url, headers


def rpc(method, params, settings):
    url, headers = _session(settings)
    message, _ = _message(url, headers, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
    if not isinstance(message, dict) or message.get("id") != 2 or message.get("error"):
        raise RuntimeError("ChannelRecipe could not complete this workflow step. Review setup before resuming.")
    return message.get("result") or {}


def call_tool(name, arguments, settings):
    result = rpc("tools/call", {"name": name, "arguments": arguments}, settings)
    if result.get("isError"):
        # Backend tools return safe creator-facing errors, never provider bodies.
        message = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        raise RuntimeError(message[:800] or "ChannelRecipe stopped this workflow step. Review the project before resuming.")
    data = result.get("structuredContent")
    # MCP SDK string-returning tools expose {result: <JSON string>} as their
    # structured result, alongside a JSON text block. Unwrap that contract.
    if isinstance(data,dict) and set(data)=={"result"} and isinstance(data["result"],str):
        try:
            data=json.loads(data["result"])
        except ValueError:
            data=None
    if not isinstance(data, dict):
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        try:
            data = json.loads("\n".join(texts))
        except ValueError:
            raise RuntimeError("ChannelRecipe returned an invalid workflow result.") from None
    if isinstance(data, dict) and (data.get("error") or data.get("detail")):
        raise RuntimeError(str(data.get("error") or data.get("detail"))[:800])
    if not isinstance(data, dict):
        raise RuntimeError("ChannelRecipe returned an invalid workflow result.")
    return data


def status(settings):
    try:
        tools = rpc("tools/list", {}, settings).get("tools", [])
        available = {t.get("name") for t in tools}
        missing = sorted(REQUIRED_TOOLS - available)
        return {"connected": not missing, "missing_tools": missing,
                "why": "Update ChannelRecipe's workflow connection." if missing else ""}
    except RuntimeError as error:
        return {"connected": False, "missing_tools": [], "why": str(error)}


def register(settings):
    """Write project-scoped Claude MCP configuration without keys in shell args."""
    url, key = _connection(settings)
    path = HOME / ".mcp.json"
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        raise RuntimeError("The local MCP configuration needs attention. Back it up before reconnecting.") from None
    servers = data.setdefault("mcpServers", {})
    servers["channelrecipe"] = {"type": "http", "url": url,
                                "headers": {"Authorization": "Bearer " + key}}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2))
    temp.chmod(0o600)
    temp.replace(path)
    return status(settings)


def is_registered(settings):
    try:
        url,key=_connection(settings)
        server=json.loads((HOME/".mcp.json").read_text()).get("mcpServers",{}).get("channelrecipe",{})
        return server.get("url")==url and server.get("headers",{}).get("Authorization")=="Bearer "+key
    except (OSError,ValueError,RuntimeError):
        return False
