"""Kie's finished files through ChannelRecipe when this computer can't reach Kie's file host.

A creator's network cut every connection to aiquickdraw.com (where Kie serves finished voice, pictures, music and 3D
models) during the secure-connection handshake, while api.kie.ai and channelrecipe.com worked: every download failed
and every video stopped. Any GET for a file on Kie's file hosts that fails to connect is fetched again through
ChannelRecipe's relay (/api/studio/media-relay, signed in with this Studio's account), and the host is remembered as
blocked for a day, so the rest of the video goes straight through the relay.

Uploads to Kie's upload host (kieai.redpandaai.co: reference pictures, the watcher's light video copies) go the same
way through /api/studio/upload-relay when that host can't be reached. An upload has to reach Kie with the creator's
Kie key, so only for that relayed upload the key travels to ChannelRecipe in X-CR-Kie-Authorization; the site passes
it to Kie for that one request and never keeps or logs it.

install() wraps requests' transport once per process (Studio, its video workers and the Shorts pipelines), so every
downloader and uploader in the engine is covered without changing it. Only these hosts are touched: GETs of Kie's
files, and POSTs to Kie's two upload addresses. Kie's key is never sent with a relayed download. It first has the
process trust this computer's own certificates (cr_trust), so an antivirus that scans secure connections doesn't fail
them.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

HOME = Path(__file__).resolve().parent
RELAY_SUFFIXES = ("aiquickdraw.com", "redpandaai.co")      # Kie's result and upload file hosts
UPLOAD_HOST = "kieai.redpandaai.co"
UPLOAD_PATHS = ("/api/file-stream-upload", "/api/file-base64-upload")
BLOCKED_FILE = HOME / ".cr-relay-hosts.json"
BLOCKED_SECONDS = 24 * 3600
_lock = threading.Lock()


def relayable(url: str) -> bool:
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == s or host.endswith("." + s) for s in RELAY_SUFFIXES)


def uploadable(url: str) -> bool:
    parts = urlsplit(str(url or ""))
    return (parts.scheme == "https" and (parts.hostname or "").lower() == UPLOAD_HOST and parts.port in (None, 443)
            and parts.path in UPLOAD_PATHS)


def _blocked_hosts() -> dict:
    try:
        data = json.loads(BLOCKED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {h: t for h, t in data.items() if isinstance(t, (int, float)) and t > now} if isinstance(data, dict) else {}


def blocked(host: str) -> bool:
    return host in _blocked_hosts()


def mark_blocked(host: str) -> None:
    with _lock:
        hosts = _blocked_hosts()
        hosts[host] = time.time() + BLOCKED_SECONDS
        tmp = BLOCKED_FILE.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(hosts), encoding="utf-8")
            tmp.replace(BLOCKED_FILE)
        except OSError:
            tmp.unlink(missing_ok=True)


def _account():
    """(ChannelRecipe's address, this Studio's account key), or None when it isn't connected."""
    try:
        import cr_bridge
        cfg = cr_bridge.load_settings()
    except Exception:                                          # noqa: BLE001
        return None
    key, base = str(cfg.get("key") or ""), str(cfg.get("url") or "https://channelrecipe.com").rstrip("/")
    if not key.startswith("cr_mcp_") or not base.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return None
    return base, key


def relayed(request):
    """The same GET, asked of ChannelRecipe's relay; None without a connected account."""
    account = _account()
    if account is None:
        return None
    base, key = account
    out = request.copy()
    for name in ("Authorization", "Cookie", "Proxy-Authorization"):
        out.headers.pop(name, None)                            # nothing meant for Kie goes to ChannelRecipe
    out.prepare_url(base + "/api/studio/media-relay", {"url": request.url})
    out.headers["Authorization"] = "Bearer " + key
    out.headers["X-CR-Relay"] = "1"
    return out


def relayed_upload(request):
    """The same upload, sent to ChannelRecipe's upload relay; None without a connected account, or for a streamed
    body that can't be sent a second time."""
    account = _account()
    if account is None or not isinstance(request.body, (bytes, str)):
        return None
    base, key = account
    out = request.copy()
    kie = out.headers.pop("Authorization", None)
    for name in ("Cookie", "Proxy-Authorization"):
        out.headers.pop(name, None)
    out.prepare_url(base + "/api/studio/upload-relay", {"path": urlsplit(request.url).path})
    out.headers["Authorization"] = "Bearer " + key
    if kie:
        out.headers["X-CR-Kie-Authorization"] = kie
    out.headers["X-CR-Relay"] = "1"
    return out


def install() -> None:
    try:
        import cr_trust
        cr_trust.install()
    except Exception:                                          # noqa: BLE001 - certifi's list, as before
        pass
    try:
        import requests
        from requests.adapters import HTTPAdapter
    except ImportError:
        return
    if getattr(HTTPAdapter.send, "_cr_relay", False):
        return
    original = HTTPAdapter.send

    def send(self, request, **kwargs):
        if request.headers.get("X-CR-Relay"):
            return original(self, request, **kwargs)
        if request.method == "GET" and relayable(request.url):
            via_relay, what = relayed, "fetching Kie's files"
        elif request.method == "POST" and uploadable(request.url):
            via_relay, what = relayed_upload, "uploading to Kie"
        else:
            return original(self, request, **kwargs)
        host = (urlsplit(request.url).hostname or "").lower()
        if not blocked(host):
            try:
                return original(self, request, **kwargs)
            except requests.exceptions.ConnectionError as error:       # incl. SSL handshake cuts and connect timeouts
                via = via_relay(request)
                if via is None:
                    raise
                mark_blocked(host)
                print(f"{host} can't be reached from this computer ({type(error).__name__}); "
                      f"{what} through ChannelRecipe instead", flush=True)
                return original(self, via, **kwargs)
        via = via_relay(request)
        return original(self, via if via is not None else request, **kwargs)

    send._cr_relay = True
    HTTPAdapter.send = send
