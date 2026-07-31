"""
ChannelRecipe MCP server (Streamable HTTP).

Combined into the main FastAPI process at domain root so OAuth discovery
(/.well-known/...) works for Claude.ai custom connectors. MCP endpoint: /mcp.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import Context, MCPServer

from webapp import mcp_billing as billing
from webapp.mcp_oauth import (
    MCP_SCOPE,
    mcp_resource_url,
    oauth_provider,
    public_base_url,
)

mcp = MCPServer(
    name="ChannelRecipe",
    instructions=(
        "ChannelRecipe helps YouTube cash-cow creators find live niche subjects, "
        "write scripts, and make thumbnails. Free tier is tutorial-thin — "
        "upgrade at channelrecipe.com for volume. "
        "Start with list_niches to see what is loaded, then list_niche_subjects / "
        "list_niche_channels, then generate_script / generate_thumbnail. "
        "Auth is the Claude connector OAuth session (or Desktop Bearer key) — "
        "do not ask the user for an api_key tool argument."
    ),
    website_url="https://channelrecipe.com",
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=public_base_url(),  # type: ignore[arg-type]
        resource_server_url=mcp_resource_url(),  # type: ignore[arg-type]
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[MCP_SCOPE],
            default_scopes=[MCP_SCOPE],
        ),
        required_scopes=[MCP_SCOPE],
    ),
)

_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_MAX = 30
_RATE_WINDOW = 60.0

THUMB_STYLES = (
    "bold_dramatic",
    "clean_minimal",
    "high_contrast_face",
    "gaming_neon",
    "documentary",
)

_PRELOADED_NICHE = "gta 6"


def _client_bucket(extra: str = "") -> str:
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


def _user_from_request(ctx: Context | None = None) -> dict | None:
    """Resolve user from OAuth middleware or Authorization Bearer MCP key."""
    from webapp.database import get_user_by_id, get_user_by_mcp_api_key

    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        tok = get_access_token()
        if tok and tok.subject:
            try:
                user = get_user_by_id(int(tok.subject))
                if user:
                    return user
            except Exception:
                pass
    except Exception:
        pass

    key = ""
    if ctx is not None:
        try:
            key = _key_from_headers(ctx.headers)
        except Exception:
            key = ""
    if key.startswith("cr_mcp_"):
        return get_user_by_mcp_api_key(key)
    return get_user_by_mcp_api_key(key) if key else None


def _plan_of(user: dict | None) -> str:
    return (user or {}).get("plan") or "free"


def _normalize_niche(niche: str) -> str:
    raw = (niche or "").strip().lower()
    if not raw:
        return _PRELOADED_NICHE
    # gta6 / gta-6 / GTA 6 → gta 6
    if re.fullmatch(r"gta[\s_\-]*6", raw) or re.sub(r"[\s_\-]+", "", raw) == "gta6":
        return _PRELOADED_NICHE
    return raw


def _channels_for_niche(niche: str, *, limit: int) -> list[dict]:
    from webapp.database import list_niche_channels
    q = _normalize_niche(niche)
    return list_niche_channels(sort="recent_revenue", limit=limit, offset=0, q=q)


def _library_stats() -> dict[str, Any]:
    from webapp.database import count_niche_channels, list_niche_keywords

    total = count_niche_channels(active_only=True)
    niches = list_niche_keywords(limit=40)
    return {"total_channels": total, "niches": niches}


def _empty_niche_payload(niche: str, *, plan: str) -> dict[str, Any]:
    """Distinguish empty library vs unknown niche vs known-but-empty."""
    stats = _library_stats()
    requested = _normalize_niche(niche)
    known = {(n.get("niche") or "").lower() for n in stats["niches"]}
    payload: dict[str, Any] = {
        "niche": requested,
        "subjects": [],
        "channels": [],
        "plan": plan,
        "library_channels": stats["total_channels"],
        "available_niches": [n.get("niche") for n in stats["niches"]],
    }
    if stats["total_channels"] <= 0:
        payload["status"] = "empty_library"
        payload["note"] = (
            "ChannelRecipe niche library is empty in this environment — nothing is seeded yet. "
            "Ops: wait for startup GTA seed or POST /api/internal/niche/seed-gta6. "
            "Call list_niches after seed completes."
        )
    elif requested in known:
        payload["status"] = "niche_empty"
        payload["note"] = (
            f"Niche '{requested}' is known but has no matching channels right now. "
            "Try another niche from available_niches."
        )
    else:
        payload["status"] = "niche_not_found"
        if stats["niches"]:
            payload["note"] = (
                f"No niche matching '{requested}'. "
                f"Call list_niches — loaded niches include: "
                + ", ".join(n.get("niche") for n in stats["niches"][:8])
            )
        else:
            payload["note"] = f"No niche matching '{requested}'."
        # Only suggest GTA when it is actually loaded and the user didn't already ask for it
        if _PRELOADED_NICHE in known and requested != _PRELOADED_NICHE:
            payload["hint"] = f"Try niche='{_PRELOADED_NICHE}'."
    payload["upgrade_url"] = billing.UPGRADE_URL
    return payload


@mcp.tool()
def list_niches(ctx: Context | None = None) -> str:
    """
    List niches actually present in ChannelRecipe's live niche database.

    Call this first. If it returns empty, the library is unseeded — do not guess
    niche strings or invent research from training data.
    """
    user = _user_from_request(ctx)
    bucket = str((user or {}).get("id") or "anon-niches")
    if not _rate_ok(_client_bucket(bucket)):
        return json.dumps({
            "error": "Rate limit — wait a minute and try again.",
            "upgrade": billing.UPGRADE_CTA,
        })
    plan = _plan_of(user)
    stats = _library_stats()
    limit = billing.discovery_niche_limit(plan)
    niches = stats["niches"][:limit]
    free_capped = not billing.is_paid_plan(plan)
    payload = billing.with_upgrade_hint(
        {
            "niches": niches,
            "library_channels": stats["total_channels"],
            "plan": plan,
            "limit": limit,
            "status": "ok" if niches else "empty_library",
        },
        free_capped=free_capped,
    )
    if not niches:
        payload["note"] = (
            "Niche library is empty — seed has not run in this environment yet."
        )
    elif free_capped:
        payload["note"] = (
            f"Free taste: showing {limit} niche(s) of {stats['total_channels']} channels in the library. "
            "Upgrade to unlock the full live niche database in Claude."
        )
    return json.dumps(payload, default=str)


@mcp.tool()
def list_niche_subjects(
    niche: str = "gta 6",
    ctx: Context | None = None,
) -> str:
    """
    List subjects currently pulling views in a niche (e.g. "gta 6").

    Uses ChannelRecipe's live niche database (not training data). Prefer
    list_niches first so you pass a niche that exists. Free accounts get a
    short tutorial list; paid plans get the full ranked set.
    """
    user = _user_from_request(ctx)
    bucket = str((user or {}).get("id") or "anon")
    if not _rate_ok(_client_bucket(bucket)):
        return json.dumps({
            "error": "Rate limit — wait a minute and try again.",
            "upgrade": billing.UPGRADE_CTA,
        })
    plan = _plan_of(user)
    requested = _normalize_niche(niche)
    subj_limit = billing.discovery_subject_limit(plan)
    ch_limit = billing.discovery_channel_limit(plan)
    channels = _channels_for_niche(requested, limit=max(ch_limit, 14))
    if not channels:
        return json.dumps(_empty_niche_payload(requested, plan=plan), default=str)

    from core.niche_subjects import list_subjects_from_channels
    subjects = list_subjects_from_channels(channels, limit=subj_limit)
    free_capped = not billing.is_paid_plan(plan)
    payload = billing.with_upgrade_hint(
        {
            "status": "ok",
            "niche": requested,
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
    ctx: Context | None = None,
) -> str:
    """
    List example channels in a niche from ChannelRecipe's database,
    with recent average views and recent video titles.
    """
    user = _user_from_request(ctx)
    bucket = str((user or {}).get("id") or "anon-ch")
    if not _rate_ok(_client_bucket(bucket)):
        return json.dumps({
            "error": "Rate limit — wait a minute and try again.",
            "upgrade": billing.UPGRADE_CTA,
        })
    plan = _plan_of(user)
    requested = _normalize_niche(niche)
    limit = billing.discovery_channel_limit(plan)
    rows = _channels_for_niche(requested, limit=limit)
    if not rows:
        return json.dumps(_empty_niche_payload(requested, plan=plan), default=str)
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
            "status": "ok",
            "niche": requested,
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
    ctx: Context | None = None,
) -> str:
    """
    Generate a YouTube narration script for the given title/subject.

    Free accounts get one tutorial script; paid plans unlock volume.
    Identity comes from the connector OAuth session — no api_key argument.
    """
    from webapp.database import bump_mcp_free_script
    import config

    user = _user_from_request(ctx)
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
    style: str = "bold_dramatic",
    ctx: Context | None = None,
) -> str:
    """
    Generate one YouTube thumbnail for the title.

    Free accounts get one tutorial thumbnail.
    style must be one of: bold_dramatic, clean_minimal, high_contrast_face,
    gaming_neon, documentary.
    """
    from webapp.database import bump_mcp_free_thumb
    from pathlib import Path
    import config

    user = _user_from_request(ctx)
    ok, msg = billing.thumbnail_allowed(user)
    if not ok:
        return json.dumps({"error": msg, "upgrade_url": billing.UPGRADE_URL})

    title = (title or "").strip()
    if not title:
        return json.dumps({"error": "Pass a title for the thumbnail."})

    style_key = (style or "bold_dramatic").strip().lower().replace(" ", "_")
    style_prompts = {
        "bold_dramatic": "Bold, eye-catching YouTube thumbnail with dramatic lighting and strong contrast",
        "clean_minimal": "Clean minimal YouTube thumbnail with simple composition and readable text space",
        "high_contrast_face": "High-contrast face-forward YouTube thumbnail, expressive reaction, crisp subject",
        "gaming_neon": "Gaming neon YouTube thumbnail, saturated colors, energetic composition",
        "documentary": "Documentary-style YouTube thumbnail, cinematic lighting, serious tone",
    }
    if style_key not in style_prompts:
        return json.dumps({
            "error": f"Unknown style '{style}'.",
            "available_styles": list(THUMB_STYLES),
        })

    try:
        from core.thumbnail_gen import generate_thumbnail_no_refs
        from webapp import storage

        out_dir = Path(config.OUTPUT_DIR) / "mcp_thumbs" / str(int(time.time()))
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = generate_thumbnail_no_refs(
            title=title,
            style_description=style_prompts[style_key],
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
            "style": style_key,
            "thumbnail_url": url,
            "plan": _plan_of(user),
            "available_styles": list(THUMB_STYLES),
        }
        if not billing.is_paid_plan(_plan_of(user)):
            payload["note"] = "Free tutorial thumbnail used. " + billing.UPGRADE_CTA
            payload["upgrade_url"] = billing.UPGRADE_URL
        return json.dumps(payload)
    except Exception as e:
        return json.dumps({"error": f"Thumbnail failed: {e}"})


def build_mcp_asgi():
    """Starlette app with /mcp + OAuth routes at domain-root paths."""
    from mcp.server.transport_security import TransportSecuritySettings

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
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=security,
        host="0.0.0.0",
    )


def wrap_fastapi_with_mcp(fastapi_app):
    """Put MCP + OAuth routes beside FastAPI for Claude.ai discovery."""
    from contextlib import asynccontextmanager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    mcp_asgi = build_mcp_asgi()

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with fastapi_app.router.lifespan_context(fastapi_app):
            yield

    routes = list(mcp_asgi.routes) + [Mount("/", app=fastapi_app)]
    return Starlette(
        routes=routes,
        middleware=list(mcp_asgi.user_middleware),
        lifespan=lifespan,
    )
