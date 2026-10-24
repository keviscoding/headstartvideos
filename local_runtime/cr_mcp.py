"""Authenticated CR production tools over MCP; no model generation here."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

HOME = Path(__file__).resolve().parent
# Back-off before retrying a connection that never reached ChannelRecipe (about
# 90 seconds in total): long enough to ride out a website deploy or a brief
# Wi-Fi/DNS drop without stopping a running video.
CONNECT_RETRY_DELAYS = (2, 4, 8, 15, 30, 30)
PROTOCOL = "2025-03-26"
_state = threading.local()
REQUIRED_TOOLS = {"local_studio_prepare", "local_studio_begin", "local_studio_authorize_stage",
                  "local_studio_finish_stage", "local_studio_progress", "local_studio_finish_project", "local_studio_retry_stage"}


def _workflow_message(message, settings=None):
    """Bound creator messages and redact credentials even in malformed errors."""
    if not isinstance(message, str) or not message.strip():
        return "ChannelRecipe stopped this workflow step. Review the project before resuming."
    message = message.strip()
    secrets = [value for name, value in (settings or {}).items()
               if isinstance(value, str) and len(value) >= 8
               and (name.lower().endswith(("key", "token")) or "secret" in name.lower())]
    secrets.extend(value for name, value in os.environ.items() if len(value) >= 8
                   and (name.endswith("_KEY") or "TOKEN" in name))
    for secret in sorted(set(secrets), key=len, reverse=True):
        message = message.replace(secret, "[redacted]")
    message = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", message)
    message = re.sub(r"\b(?:cr_mcp_|sk-)[A-Za-z0-9_-]+", "[redacted]", message)
    message = re.sub(r"https?://\S+", "[URL]", message)
    return " ".join(message.split())[:800]


class WorkflowError(RuntimeError):
    """A structured creator rejection, separate from transport/protocol faults."""
    def __init__(self, message, status=None, *, settings=None):
        self.detail = _workflow_message(message, settings)
        self.status = status if isinstance(status, int) and not isinstance(status, bool) and 400 <= status <= 599 else None
        super().__init__(self.detail)


def _connection(settings):
    base = str(settings.get("url", "https://channelrecipe.com")).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
        raise RuntimeError("Connect to ChannelRecipe using HTTPS in Setup & accounts.")
    key = settings.get("key", "")
    if not isinstance(key, str) or not key.startswith("cr_mcp_"):
        raise RuntimeError("Connect your ChannelRecipe account in Setup & accounts.")
    return base + "/mcp", key


def never_reached_server(error):
    """True only when the connection failed before any request was sent.

    DNS failures, refused connections and connect timeouts cannot have created
    work on the server, so retrying them cannot duplicate a stage reservation.
    Anything after the request was sent (resets, read timeouts, 5xx) is left
    to the existing explicit review and recovery flow.
    """
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (requests.exceptions.ConnectTimeout, NewConnectionError, ConnectTimeoutError)):
            return True
        if isinstance(current, BaseException):
            pending.extend([getattr(current, "reason", None), current.__cause__, current.__context__,
                            *[arg for arg in current.args if isinstance(arg, BaseException)]])
    return False


def request_with_connect_retry(send, *, sleep=None):
    """Run send(); retry only connections that never reached the server."""
    import time
    sleep = sleep or time.sleep
    for delay in CONNECT_RETRY_DELAYS:
        try:
            return send()
        except requests.RequestException as error:
            if not never_reached_server(error):
                raise
            sleep(delay)
    return send()


def _message(url, headers, payload):
    # No retries once a request may have reached the server: stage reservations
    # must never create duplicate work. Connections that never got there are
    # retried with back-off, which cannot duplicate anything.
    try:
        with request_with_connect_retry(lambda: requests.post(url, headers=headers, json=payload, stream=True,
                                                              timeout=(10, 45), allow_redirects=False)) as response:
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
                   "clientInfo": {"name": "ChannelRecipe local studio", "version": "5.3-admin.13"}}})
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
        # Unhandled MCP errors may contain server diagnostics. Only the native
        # tools' structured rejection envelope is creator-facing data.
        raise RuntimeError("ChannelRecipe could not complete this workflow step. Review setup before resuming.")
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
        message = data.get("error") or data.get("detail")
        if name in REQUIRED_TOOLS and isinstance(message, str):
            raise WorkflowError(message, data.get("status"), settings=settings)
        raise RuntimeError("ChannelRecipe stopped this workflow step. Review the project before resuming.")
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
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        raise RuntimeError("The local MCP configuration needs attention. Back it up before reconnecting.") from None
    servers = data.setdefault("mcpServers", {})
    servers["channelrecipe"] = {"type": "http", "url": url,
                                "headers": {"Authorization": "Bearer " + key}}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)
    return status(settings)


def is_registered(settings):
    try:
        url,key=_connection(settings)
        server=json.loads((HOME/".mcp.json").read_text(encoding="utf-8")).get("mcpServers",{}).get("channelrecipe",{})
        return server.get("url")==url and server.get("headers",{}).get("Authorization")=="Bearer "+key
    except (OSError,ValueError,RuntimeError):
        return False
