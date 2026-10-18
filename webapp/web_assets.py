"""How the site's pages and static files reach browsers: versioned, cached, and with safe headers.

Pages link every /static file at its content version (/static/app.js?v=<hash>), worked out from the file itself, so
a file can be cached for a year and a deploy that changes it changes its link. Nobody bumps a ?v= by hand any more.
A file asked for without its current version (an old page, a hand-typed link) is cached for an hour.

Without a Cache-Control header of our own, DigitalOcean marks every response "private" and Cloudflare caches
nothing, so each visit re-downloads app.js, the fonts' CSS and the pictures from the app itself.
"""
from __future__ import annotations

import hashlib
import mimetypes
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"
# The server's slim Linux image has no /etc/mime.types entry for WebP, so the posters went out as
# application/octet-stream. Name the types we serve rather than trust the host's table.
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/mp4", ".mp4")
YEAR = "public, max-age=31536000, immutable"
HOUR = "public, max-age=3600"
# marketing pages: browsers and the edge keep them a minute and serve them while they refresh
PAGE = "public, max-age=60, stale-while-revalidate=600"
# the app shell: always revalidated, so a deploy reaches signed-in people at once
APP = "no-cache"

_LINK = re.compile(r"""(?P<lead>["'(])/static/(?P<path>[A-Za-z0-9_./-]+?)(?:\?v=[A-Za-z0-9._-]*)?(?=["') ])""")


def _file(rel: str) -> Path | None:
    try:
        p = (STATIC_DIR / rel).resolve()
    except (OSError, ValueError):
        return None
    if STATIC_DIR not in p.parents or not p.is_file():
        return None
    return p


@lru_cache(maxsize=512)
def _hash(path: str, mtime_ns: int, size: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:10]


def asset_version(rel: str) -> str:
    """The content version of a file under /static ("" when there is no such file)."""
    p = _file(rel)
    if p is None:
        return ""
    st = p.stat()
    return _hash(str(p), st.st_mtime_ns, st.st_size)


def versioned(html: str) -> str:
    """Every /static link in a page, pointed at the file's current version."""
    def swap(m: re.Match) -> str:
        v = asset_version(m.group("path"))
        if not v:
            return m.group(0)
        return f"{m.group('lead')}/static/{m.group('path')}?v={v}"
    return _LINK.sub(swap, html)


@lru_cache(maxsize=64)
def _page(path: str, mtime_ns: int) -> str:
    return versioned(Path(path).read_text(encoding="utf-8"))


def html_page(path: Path, cache: str = PAGE, headers: dict | None = None) -> HTMLResponse:
    """A page from /static with its links versioned (worked out once per file change)."""
    path = Path(path)
    body = _page(str(path), path.stat().st_mtime_ns)
    return HTMLResponse(body, headers={"Cache-Control": cache, **(headers or {})})


class CachedStatic(StaticFiles):
    """/static with cache headers: a year at the file's current version, an hour otherwise."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 206, 304):
            query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
            asked = (query.get("v") or [""])[0]
            current = asset_version(path)
            response.headers["Cache-Control"] = YEAR if asked and asked == current else HOUR
        return response


class SecurityHeaders:
    """Plain ASGI middleware (it never buffers a stream) adding the standard protective headers to every response;
    pages may only be framed by this site."""

    BASE = [(b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"strict-transport-security", b"max-age=31536000"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), usb=()")]
    PAGE = [(b"x-frame-options", b"SAMEORIGIN"),
            (b"content-security-policy", b"frame-ancestors 'self'")]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        async def sending(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                have = {k.lower() for k, _ in headers}
                html = any(k.lower() == b"content-type" and v.lower().startswith(b"text/html") for k, v in headers)
                for k, v in self.BASE + (self.PAGE if html else []):
                    if k not in have:
                        headers.append((k, v))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, sending)
