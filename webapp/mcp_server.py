"""
ChannelRecipe MCP server (Streamable HTTP).

Mounted at /mcp on the main FastAPI app. Discovery tools are public with
tutorial-thin caps; script/thumbnail require an MCP API key.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from webapp import mcp_billing as billing

mcp = MCPServer(
    name="ChannelRecipe",
    instructions=(
        "ChannelRecipe helps YouTube cash-cow creators find live niche subjects, "
        "write scripts, and make thumbnails. Free tier is tutorial-thin — "
        "upgrade at channelrecipe.com for volume. "
        "Pass your MCP API key via Authorization: Bearer <key> (Settings → Claude / MCP) "
        "or the api_key tool argument."
    ),
    website_url="https://channelrecipe.com",
)

# Simple IP rate limit for anonymous discovery (in-process).
_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_MAX = 30  # calls / window / bucket
_RATE_WINDOW = 60.0


def _client_bucket(extra: str = "") -> str:
    # Tools don't always have request IP; use a shared bucket + optional key.
    return (extra or "anon")[:120]


def _rate_ok(bucket: str) -> bool:
    now = time.time()
    hits = _RATE[bucket]
    _RATE[bucket] = [t for t in hits if now - t < _RATE_WINDOW]
    if len(_RATE[bucket]) >= _RATE_MAX:
        return False
    _RATE[bucket].append(now)
    return True


def _key_from_headers(headers: Any) -> str:
    if not headers:
        return ""
    # Starlette Headers are case-insensitive; Mapping may not be.
    def _get(name: str) -> str:
        try:
            return (headers.get(name) or headers.get(name.lower()) or "").strip()
        except Exception:
            return ""

    auth = _get("authorization") or _get("Authorization")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    for h in ("x-api-key", "X-Api-Key", "x-channelrecipe-key", "X-ChannelRecipe-Key"):
        v = _get(h)
        if v:
            return v
    return ""


def _resolve_api_key(api_key: str | None = "", ctx: Context | None = None) -> str:
    key = (api_key or "").strip()
    if key:
        return key
    if ctx is not None:
        try:
            return _key_from_headers(ctx.headers)
        except Exception:
            return ""
    return ""


def _user_from_key(api_key: str | None) -> dict | None:
    if not (api_key or "").strip():
        return None
    from webapp.database import get_user_by_mcp_api_key
    return get_user_by_mcp_api_key(api_key.strip())


def _plan_of(user: dict | None) -> str:
    return (user or {}).get("plan") or "free"


def _channels_for_niche(niche: str, *, limit: int) -> list[dict]:
    from webapp.database import list_niche_channels
    q = (niche or "gta 6").strip() or "gta 6"
    return list_niche_channels(sort="recent_revenue", limit=limit, offset=0, q=q)


@mcp.tool()
def list_niche_subjects(
    niche: str = "gta 6",
    api_key: str = "",
    ctx: Context | None = None,
) -> str:
    """
    List subjects currently pulling views in a niche (e.g. "gta 6").

    Uses ChannelRecipe's live niche database (not training data). Free accounts
    get a short tutorial list; paid plans get the full ranked set.
    """
    api_key = _resolve_api_key(api_key, ctx)
    if not _rate_ok(_client_bucket(api_key or "anon")):
        return json.dumps({
            "error": "Rate limit — wait a minute and try again.",
            "upgrade": billing.UPGRADE_CTA,
        })
    user = _user_from_key(api_key)
    plan = _plan_of(user)
    subj_limit = billing.discovery_subject_limit(plan)
    ch_limit = billing.discovery_channel_limit(plan)
    channels = _channels_for_niche(niche, limit=max(ch_limit, 14))
    if not channels:
        return json.dumps({
            "niche": niche,
            "subjects": [],
            "note": (
                "No channels in the database for that niche yet. "
                "GTA 6 is preloaded — try niche='gta 6'."
            ),
            "upgrade_url": billing.UPGRADE_URL,
        })

    from core.niche_subjects import list_subjects_from_channels
    subjects = list_subjects_from_channels(channels, limit=subj_limit)
    free_capped = not billing.is_paid_plan(plan)
    payload = billing.with_upgrade_hint(
        {
            "niche": niche,
            "subjects": subjects,
            "channels_scanned": len(channels),
            "limit": subj_limit,
            "plan": plan,
            "tip": (
                "Pick subjects people will live inside (money, police, cars, property, "
                "editions) — not one-off cheat-code fluff."
            ),
        },
        free_capped=free_capped,
    )
    if free_capped:
        payload["note"] = (
            f"Free tutorial view: top {subj_limit} subjects. "
            "Upgrade for the full ranked list and coverage vs your channel."
        )
    return json.dumps(payload, default=str)


@mcp.tool()
def list_niche_channels(
    niche: str = "gta 6",
    api_key: str = "",
    ctx: Context | None = None,
) -> str:
    """
    List example channels in a niche from ChannelRecipe's database,
    with recent average views and recent video titles.
    """
    api_key = _resolve_api_key(api_key, ctx)
    if not _rate_ok(_client_bucket(api_key or "anon-ch")):
        return json.dumps({
            "error": "Rate limit — wait a minute and try again.",
            "upgrade": billing.UPGRADE_CTA,
        })
    user = _user_from_key(api_key)
    plan = _plan_of(user)
    limit = billing.discovery_channel_limit(plan)
    rows = _channels_for_niche(niche, limit=limit)
    free_capped = not billing.is_paid_plan(plan)
    cards = []
    for ch in rows:
        recent = ch.get("recent_videos") or []
        titles = [str(v.get("title") or "") for v in recent[:3] if isinstance(v, dict)]
        cards.append({
            "channel": ch.get("channel_name"),
            "url": ch.get("channel_url"),
            "subscribers": ch.get("subscriber_count"),
            "recent_avg_views": ch.get("recent_avg_views"),
            "recent_titles": titles,
        })
    payload = billing.with_upgrade_hint(
        {
            "niche": niche,
            "channels": cards,
            "limit": limit,
            "plan": plan,
        },
        free_capped=free_capped,
    )
    if free_capped:
        payload["note"] = (
            f"Free tutorial view: {limit} channels. "
            "Upgrade for the full Niche Finder library."
        )
    return json.dumps(payload, default=str)


@mcp.tool()
def generate_script(
    title: str,
    video_idea: str = "",
    target_minutes: int = 8,
    api_key: str = "",
    ctx: Context | None = None,
) -> str:
    """
    Generate a YouTube narration script for the given title/subject.

    Requires a ChannelRecipe MCP API key (Settings → Claude / MCP).
    Free accounts get one tutorial script; paid plans unlock volume.
    """
    from webapp.database import bump_mcp_free_script, get_user_by_mcp_api_key
    import config

    api_key = _resolve_api_key(api_key, ctx)
    user = get_user_by_mcp_api_key(api_key) if api_key else None
    ok, msg = billing.script_allowed(user)
    if not ok:
        return json.dumps({"error": msg, "upgrade_url": billing.UPGRADE_URL})
    if not config.ANTHROPIC_KEY:
        return json.dumps({"error": "Script generation is temporarily unavailable."})

    title = (title or "").strip()
    if not title:
        return json.dumps({"error": "Pass a title / subject for the script."})

    try:
        from core.script_gen import generate_script as _gen
        mins = max(3, min(int(target_minutes or 8), 20 if billing.is_paid_plan(_plan_of(user)) else 10))
        script = _gen(
            title=title,
            video_idea=video_idea or title,
            channel_data=None,
            api_key=config.ANTHROPIC_KEY,
            target_length_min=mins,
        )
        if user and not billing.is_paid_plan(user.get("plan")):
            bump_mcp_free_script(int(user["id"]))
        payload = {
            "title": title,
            "script": script,
            "word_count": len(script.split()),
            "plan": _plan_of(user),
        }
        if not billing.is_paid_plan(_plan_of(user)):
            payload["note"] = "Free tutorial script used. " + billing.UPGRADE_CTA
            payload["upgrade_url"] = billing.UPGRADE_URL
        return json.dumps(payload)
    except Exception as e:
        return json.dumps({"error": f"Script failed: {e}"})


@mcp.tool()
def generate_thumbnail(
    title: str,
    style: str = "",
    api_key: str = "",
    ctx: Context | None = None,
) -> str:
    """
    Generate one YouTube thumbnail for the title.

    Requires MCP API key. Free accounts get one tutorial thumbnail.
    """
    from webapp.database import bump_mcp_free_thumb, get_user_by_mcp_api_key
    from pathlib import Path
    import config

    api_key = _resolve_api_key(api_key, ctx)
    user = get_user_by_mcp_api_key(api_key) if api_key else None
    ok, msg = billing.thumbnail_allowed(user)
    if not ok:
        return json.dumps({"error": msg, "upgrade_url": billing.UPGRADE_URL})

    title = (title or "").strip()
    if not title:
        return json.dumps({"error": "Pass a title for the thumbnail."})

    try:
        from core.thumbnail_gen import generate_thumbnail_no_refs
        from webapp import storage

        out_dir = Path(config.OUTPUT_DIR) / "mcp_thumbs" / str(int(time.time()))
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = generate_thumbnail_no_refs(
            title=title,
            style_description=style
            or "Bold, eye-catching YouTube thumbnail with dramatic lighting",
            output_dir=str(out_dir),
            count=1,
        )
        if not paths:
            return json.dumps({"error": "No thumbnail generated — try again."})
        uid = int(user["id"]) if user else 0
        url = storage.store_file(
            paths[0],
            f"mcp_thumbs/{uid}/{int(time.time())}.png",
            "image/png",
        ) if storage.is_remote() else f"/api/files/{paths[0]}"
        if user and not billing.is_paid_plan(user.get("plan")):
            bump_mcp_free_thumb(uid)
        payload: dict[str, Any] = {
            "title": title,
            "thumbnail_url": url,
            "plan": _plan_of(user),
        }
        if not billing.is_paid_plan(_plan_of(user)):
            payload["note"] = "Free tutorial thumbnail used. " + billing.UPGRADE_CTA
            payload["upgrade_url"] = billing.UPGRADE_URL
        return json.dumps(payload)
    except Exception as e:
        return json.dumps({"error": f"Thumbnail failed: {e}"})


def build_mcp_asgi():
    """Starlette app for mounting at /mcp (stateless streamable HTTP)."""
    from mcp.server.transport_security import TransportSecuritySettings

    # Default MCP security only allows localhost:* — that blocks channelrecipe.com
    # and Host: localhost (no port). Explicit allowlist for prod + local.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "channelrecipe.com",
            "www.channelrecipe.com",
            "localhost",
            "localhost:*",
            "127.0.0.1",
            "127.0.0.1:*",
            "[::1]",
            "[::1]:*",
        ],
        allowed_origins=[
            "https://channelrecipe.com",
            "https://www.channelrecipe.com",
            "https://claude.ai",
            "http://localhost",
            "http://localhost:*",
            "http://127.0.0.1",
            "http://127.0.0.1:*",
        ],
    )
    return mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=security,
        host="0.0.0.0",  # do not auto-swap to localhost-only security
    )
