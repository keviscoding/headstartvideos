"""
ChannelRecipe MCP server (Streamable HTTP).

Combined into the main FastAPI process at domain root so OAuth discovery
(/.well-known/...) works for Claude.ai custom connectors. MCP endpoint: /mcp.
"""

from __future__ import annotations

import base64
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
        "ChannelRecipe helps YouTube creators find live niche subjects, "
        "write scripts, and make thumbnails. Free includes a short sample — "
        "upgrade at channelrecipe.com for the full library. "
        "Start with list_niches, then list_niche_subjects / "
        "list_niche_channels, then generate_script / generate_thumbnail. "
        "Admin: get_video_transcript accepts a video URL OR up to 10 channel "
        "URLs (comma/newline-separated @handles) for niche research — titles, "
        "views, one sample transcript per channel, and 2 recent thumbnail "
        "images (base64 + image parts). Prefer that when research_niche_channels "
        "is missing from the tool list (Claude.ai sometimes caches old tools). "
        "Auth is the Claude connector session (or Desktop Bearer key) — "
        "do not ask the user for an api_key tool argument. "
        "When a tool returns upgrade_url, tell the user to upgrade at that link. "
        "After server tool updates, start a brand-new chat so the tool catalog refreshes."
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
    capped = billing.discovery_capped(plan)
    payload = billing.with_upgrade_hint(
        {
            "niches": niches,
            "library_channels": stats["total_channels"],
            "plan": plan,
            "limit": limit,
            "status": "ok" if niches else "empty_library",
        },
        free_capped=capped,
        plan=plan,
    )
    if not niches:
        payload["note"] = (
            "Niche library is empty — seed has not run in this environment yet."
        )
    elif capped:
        if billing.is_trial_plan(plan):
            payload["note"] = (
                f"Trial library: showing {limit} niche(s). "
                "Upgrade for the full live niche database in Claude + Niche Finder in the app."
            )
        else:
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
    capped = billing.discovery_capped(plan)
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
        free_capped=capped,
        plan=plan,
    )
    if capped:
        label = "Trial" if billing.is_trial_plan(plan) else "Free tutorial"
        payload["note"] = (
            f"{label} view: top {subj_limit} subjects. "
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
    capped = billing.discovery_capped(plan)
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
        free_capped=capped,
        plan=plan,
    )
    if capped:
        label = "Trial" if billing.is_trial_plan(plan) else "Free tutorial"
        payload["note"] = (
            f"{label} view: {limit} channels. "
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


def _mcp_is_admin(user: dict | None) -> bool:
    """Transcript MCP is admin-gated (token / provider cost)."""
    import config
    email = ((user or {}).get("email") or "").strip().lower()
    admins = {e.strip().lower() for e in (getattr(config, "ADMIN_EMAILS", []) or []) if e}
    return bool(email) and email in admins


_MAX_RESEARCH_CHANNELS = 10
_MAX_RESEARCH_VIDEOS = 30


def _parse_channel_urls(channel_urls: Any) -> list[str]:
    """Normalize MCP channel_urls arg (list, JSON array string, or comma/newline text)."""
    if channel_urls is None:
        return []
    items: list[str] = []
    if isinstance(channel_urls, (list, tuple)):
        for x in channel_urls:
            s = str(x or "").strip()
            if s:
                items.append(s)
    else:
        raw = str(channel_urls).strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    for x in parsed:
                        s = str(x or "").strip()
                        if s:
                            items.append(s)
                    raw = ""
            except Exception:
                pass
        if raw:
            for part in re.split(r"[\n,]+", raw):
                s = part.strip().strip("'\"")
                if s:
                    items.append(s)

    # Dedupe preserving order
    out: list[str] = []
    seen: set[str] = set()
    for u in items:
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out[:_MAX_RESEARCH_CHANNELS]


def _looks_like_channel_input(raw: str) -> bool:
    """True when input is channel URL(s)/@handles rather than a single video id."""
    s = (raw or "").strip()
    if not s:
        return False
    # Multiple URLs / handles → research batch
    parts = _parse_channel_urls(s)
    if len(parts) > 1:
        return True
    one = parts[0] if parts else s
    low = one.lower()
    if "/channel/" in low or "/@" in low or low.startswith("@"):
        return True
    if re.search(r"youtube\.com/(c|user)/", low):
        return True
    # Bare handle without watch?v=
    if re.fullmatch(r"@?[\w.-]{3,}", one) and "watch" not in low and "youtu.be" not in low:
        if one.startswith("@") or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", one):
            return one.startswith("@")
    return False


def _admin_gate(user: dict | None, *, feature: str) -> str | None:
    """Return JSON error string if user cannot use admin MCP feature; else None."""
    if not user:
        return json.dumps({
            "error": f"{feature} requires a signed-in ChannelRecipe MCP account.",
            "code": "auth_required",
            "upgrade_url": billing.UPGRADE_URL,
        })
    if not _mcp_is_admin(user):
        return json.dumps({
            "error": (
                f"{feature} is limited to the ChannelRecipe admin account "
                "while we control YouTube quota and caption credits."
            ),
            "code": "admin_only",
            "email": (user.get("email") or ""),
        })
    return None


def _run_niche_research(
    urls: list[str],
    *,
    user: dict,
    max_videos: int = 30,
) -> list[Any]:
    """Shared admin research: JSON + Image parts (+ base64 in JSON for clients that drop images)."""
    import config
    from core.channel_data import (
        download_youtube_thumbnail,
        fetch_channel_research,
    )
    from mcp.server.mcpserver.utilities.types import Image

    yt_key = (getattr(config, "YOUTUBE_API_KEY", "") or "").strip()
    if not yt_key:
        return [json.dumps({
            "error": "YOUTUBE_API_KEY is not configured on the server.",
            "code": "youtube_key_missing",
        })]

    if not urls:
        return [json.dumps({
            "error": "Pass 1–10 YouTube channel URLs.",
            "code": "invalid_url",
        })]

    mv = max(1, min(int(max_videos or 30), _MAX_RESEARCH_VIDEOS))
    downsub = getattr(config, "DOWNSUB_KEY", "") or ""
    t0 = time.time()
    channels_out: list[dict[str, Any]] = []
    content: list[Any] = []
    total_videos = 0
    total_transcripts = 0
    total_thumbs = 0

    for url in urls:
        try:
            pack = fetch_channel_research(
                url,
                yt_key,
                downsub,
                max_videos=mv,
                fetch_sample_transcript=True,
            )
            videos = pack.get("videos") or []
            total_videos += len(videos)
            if pack.get("sample_transcript"):
                total_transcripts += 1

            thumb_ids = list(pack.get("thumbnails_included") or [])[:2]
            by_id = {v.get("video_id"): v for v in videos if v.get("video_id")}
            embedded: list[dict[str, Any]] = []
            for vid in thumb_ids:
                raw = download_youtube_thumbnail(vid)
                if not raw:
                    continue
                meta = by_id.get(vid) or {}
                title = (meta.get("title") or vid)[:80]
                ch_name = pack.get("channel_name") or "channel"
                b64 = base64.b64encode(raw).decode("ascii")
                content.append(f"[thumbnail] {ch_name} — {title} ({vid})")
                content.append(Image(data=raw, format="jpeg"))
                embedded.append({
                    "video_id": vid,
                    "title": meta.get("title") or "",
                    "url": meta.get("url") or f"https://www.youtube.com/watch?v={vid}",
                    "thumbnail_url": meta.get("thumbnail_url") or "",
                    # Claude.ai sometimes drops ImageContent; base64 lets the model still “see” it
                    # when the client forwards multimodal tool JSON.
                    "image_base64": b64,
                    "mime_type": "image/jpeg",
                })
                total_thumbs += 1

            channels_out.append({
                "status": "ok",
                "channel_url": pack.get("channel_url") or url,
                "channel_name": pack.get("channel_name") or "",
                "channel_id": pack.get("channel_id") or "",
                "subscribers": pack.get("subscribers"),
                "total_views": pack.get("total_views"),
                "video_count": pack.get("video_count"),
                "videos": videos,
                "sample_transcript": pack.get("sample_transcript"),
                "transcript_note": pack.get("transcript_note") or "",
                "thumbnails_included": [e["video_id"] for e in embedded],
                "thumbnail_meta": embedded,
            })
        except Exception as e:
            print(f"[mcp] research_niche_channels failed url={url!r}: {e}")
            channels_out.append({
                "status": "error",
                "channel_url": url,
                "error": str(e)[:400],
                "videos": [],
                "sample_transcript": None,
                "thumbnails_included": [],
            })

    ok_n = sum(1 for c in channels_out if c.get("status") == "ok")
    elapsed_ms = int((time.time() - t0) * 1000)
    print(
        f"[mcp] research_niche_channels channels={len(urls)} ok={ok_n} "
        f"videos={total_videos} transcripts={total_transcripts} "
        f"thumbs={total_thumbs} ms={elapsed_ms}"
    )

    payload = {
        "status": "ok" if ok_n else "error",
        "mode": "channel_research",
        "channels": channels_out,
        "channel_count": len(channels_out),
        "ok_count": ok_n,
        "max_videos": mv,
        "limits": {
            "max_channels": _MAX_RESEARCH_CHANNELS,
            "max_videos": _MAX_RESEARCH_VIDEOS,
            "transcripts_per_channel": 1,
            "thumbnails_per_channel": 2,
        },
        "plan": _plan_of(user),
        "elapsed_ms": elapsed_ms,
        "tools_revision": "research_v2",
        "note": (
            "Each channel includes thumbnail_meta[].image_base64 (JPEG) for the 2 newest "
            "uploads, plus matching image parts after this JSON when the client supports them."
        ),
    }
    return [json.dumps(payload), *content]


@mcp.tool()
def get_video_transcript(
    youtube_url: str,
    ctx: Context | None = None,
) -> Any:
    """
    Admin: fetch a YouTube video transcript + metadata, OR research channels.

    Video mode — pass one watch URL / video id. Returns transcript, title,
    author, views, transcript_source.

    Channel research mode — pass up to 10 channel URLs or @handles
    (comma/newline-separated). Returns recent titles/views (≤30 each),
    one sample transcript per channel, and 2 newest thumbnails (base64 + images).
    Use this when research_niche_channels is not in your tool list.
    """
    import time as _time
    import config
    from core.channel_data import (
        fetch_transcript_detailed,
        fetch_video_meta,
        parse_youtube_video_id,
    )

    user = _user_from_request(ctx)
    gated = _admin_gate(user, feature="Video transcripts / niche channel research")
    if gated:
        return gated

    raw = (youtube_url or "").strip()
    if not raw:
        return json.dumps({"error": "Pass a YouTube URL, video id, or channel URL(s).", "code": "invalid_url"})

    # Claude.ai often caches the tool catalog and never sees research_niche_channels.
    # Route channel inputs through this existing tool so research still works.
    if _looks_like_channel_input(raw):
        urls = _parse_channel_urls(raw)
        # Normalize bare @handles to youtube URLs
        norm: list[str] = []
        for u in urls:
            if u.startswith("@") or (
                re.fullmatch(r"[\w.-]{3,}", u)
                and "youtube" not in u.lower()
                and "http" not in u.lower()
            ):
                handle = u if u.startswith("@") else f"@{u}"
                norm.append(f"https://www.youtube.com/{handle}")
            else:
                norm.append(u)
        print(f"[mcp] get_video_transcript → channel research urls={len(norm)}")
        return _run_niche_research(norm, user=user, max_videos=_MAX_RESEARCH_VIDEOS)

    video_id = parse_youtube_video_id(raw)
    if not video_id:
        return json.dumps({
            "error": "Could not parse a YouTube video id or channel URL from that input.",
            "code": "invalid_url",
        })

    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    t0 = _time.time()

    meta: dict = {}
    try:
        meta = fetch_video_meta(video_id, getattr(config, "YOUTUBE_API_KEY", "") or "")
    except Exception as e:
        print(f"[mcp] video meta failed job video={video_id}: {e}")
        meta = {"title": "", "author": "", "views": None, "source": "", "error": "meta_failed"}

    tr: dict = {"text": "", "source": "", "error": "no_transcript"}
    try:
        # Captions only (auto-generated preferred). No slow ASR — Claude needs
        # YouTube's existing auto captions, not a re-transcription.
        tr = fetch_transcript_detailed(
            video_id,
            getattr(config, "DOWNSUB_KEY", "") or "",
            allow_asr=False,
        )
    except Exception as e:
        print(f"[mcp] transcript failed video={video_id}: {e}")
        tr = {"text": "", "source": "", "error": f"transcript_failed:{e}"}

    text = (tr.get("text") or "").strip()
    # Admin gets a large cap so long videos aren't silently chopped for Claude.
    max_chars = 100_000
    truncated = len(text) > max_chars
    body = text[:max_chars] if text else ""

    meta_err = (meta.get("error") or "").strip()
    tr_err = (tr.get("error") or "").strip()
    has_meta = bool(meta.get("title") or meta.get("author") or meta.get("views") is not None)
    has_tr = bool(body)

    if has_tr and has_meta:
        status = "ok"
    elif has_tr:
        status = "partial_transcript"
    elif has_meta:
        status = "partial_meta"
    else:
        status = "unavailable"

    code = ""
    if not has_tr and tr_err:
        code = tr_err if tr_err in (
            "no_transcript", "private_video", "quota_exceeded", "invalid_url",
        ) else "no_transcript"
    if not has_meta and meta_err in ("private_video", "private_or_missing", "quota_exceeded"):
        code = meta_err if meta_err != "private_or_missing" else "private_video"

    elapsed_ms = int((_time.time() - t0) * 1000)
    print(
        f"[mcp] get_video_transcript video={video_id} status={status} "
        f"tr_source={tr.get('source')!r} meta_source={meta.get('source')!r} "
        f"ms={elapsed_ms}"
    )

    out = {
        "status": status,
        "mode": "video",
        "url": watch_url,
        "video_id": video_id,
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "views": meta.get("views"),
        "transcript": body,
        "transcript_source": tr.get("source") or "",
        "meta_source": meta.get("source") or "",
        "truncated": truncated,
        "char_count": len(body),
        "plan": _plan_of(user),
        "tools_revision": "research_v2",
    }
    if code:
        out["code"] = code
    if status != "ok":
        notes = []
        if not has_tr:
            notes.append(tr_err or "No captions or ASR transcript available.")
        if not has_meta:
            notes.append(meta_err or "Could not load title/views/author.")
        out["note"] = " ".join(notes)
        if status == "unavailable":
            out["error"] = out["note"]
    return json.dumps(out)


@mcp.tool()
def research_niche_channels(
    channel_urls: str,
    max_videos: int = 30,
    ctx: Context | None = None,
) -> list[Any]:
    """
    Admin niche-research mode: inspect competitor YouTube channels.

    Pass up to 10 channel URLs (comma/newline-separated or a JSON array string).
    For each channel returns up to 30 recent videos (title, views, thumb URL),
    exactly one sample transcript (highest-view recent video with captions),
    and embeds the 2 most recent video thumbnails as images so you can see
    packaging/visual niche style.

    If this tool is missing from your connector, call get_video_transcript with
    the same channel URLs instead (Claude.ai sometimes caches old tool lists).
    """
    user = _user_from_request(ctx)
    gated = _admin_gate(user, feature="Niche channel research")
    if gated:
        return [gated]

    urls = _parse_channel_urls(channel_urls)
    try:
        mv = int(max_videos)
    except Exception:
        mv = _MAX_RESEARCH_VIDEOS
    return _run_niche_research(urls, user=user, max_videos=mv)


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
