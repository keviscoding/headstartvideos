"""
Seed named YouTube channels into niche_channels (no scroll scrape).

Used to preload niches for MCP discovery (e.g. GTA 6 pack) without
running a full Niche Finder hunt.
"""

from __future__ import annotations

import json
from typing import Any, Callable

ProgressCb = Callable[[str], None]

GTA6_CHANNEL_URLS = [
    "https://www.youtube.com/@KRTGTA",
    "https://www.youtube.com/@Sav_Official_YT",
    "https://www.youtube.com/channel/UCGx65m4U5nd8fIO9YDQQpYw",
    "https://www.youtube.com/@saukko505",
    "https://www.youtube.com/channel/UCLyRlfEA20eOnqndC_uukxQ",
    "https://www.youtube.com/@RockStationGaming",
    "https://www.youtube.com/channel/UCNv4A1UmHJNNEzO28T-i4oQ",
    "https://www.youtube.com/@GTA6Videos_",
    "https://www.youtube.com/@Misteri_GTA",
    "https://www.youtube.com/@WillMacGTA",
    "https://www.youtube.com/@MoreImmortal",
    "https://www.youtube.com/@TGG_",
    "https://www.youtube.com/@adiantonline",
    "https://www.youtube.com/@HazardousHDTV",
]

GTA6_SOURCE_KEYWORD = "gta 6"


def _normalize_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u.endswith("/videos"):
        u = u[: -len("/videos")]
    return u


def seed_channels_from_urls(
    urls: list[str],
    *,
    api_key: str,
    source_keyword: str,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """Resolve URLs → enrich → return niche_channels-shaped hits."""
    from core.channel_data import _extract_channel_id
    from core.niche_finder import (
        DEFAULT_RPM_USD,
        POPULAR_SCAN,
        RECENT_VIDEO_COUNT,
        _days_since,
        _fetch_channels,
        _longform_from_uploads,
        _yt,
        estimate_monthly_revenue_usd,
        recent_average_views,
        score_channel,
        videos_posted_last_days,
    )

    def _log(msg: str) -> None:
        print(f"[niche_seed] {msg}")
        if progress:
            progress(msg)

    if not api_key:
        raise ValueError("YouTube API key required")
    cleaned = [_normalize_url(u) for u in urls if (u or "").strip()]
    if not cleaned:
        raise ValueError("No channel URLs provided")

    youtube = _yt(api_key)
    channel_ids: list[str] = []
    errors: list[dict] = []
    for url in cleaned:
        try:
            cid = _extract_channel_id(url, api_key)
            if cid:
                channel_ids.append(cid)
                _log(f"Resolved {url} → {cid}")
            else:
                errors.append({"url": url, "error": "Could not resolve channel id"})
        except Exception as e:
            errors.append({"url": url, "error": str(e)})
            _log(f"Resolve failed for {url}: {e}")

    channel_ids = list(dict.fromkeys(channel_ids))
    if not channel_ids:
        return {"hits": [], "errors": errors, "meta": {"resolved": 0}}

    _log(f"Fetching {len(channel_ids)} channels…")
    channels = _fetch_channels(youtube, channel_ids)
    hits: list[dict] = []
    kw = (source_keyword or "").strip()

    for cid in channel_ids:
        ch = channels.get(cid)
        if not ch:
            errors.append({"channel_id": cid, "error": "channels.list miss"})
            continue
        try:
            longform = _longform_from_uploads(
                youtube, ch.get("uploads_playlist") or "", want=POPULAR_SCAN
            )
            if not longform:
                errors.append({"channel_id": cid, "error": "No long-form uploads found"})
                continue

            by_date = sorted(
                longform, key=lambda x: x.get("published_at") or "", reverse=True
            )
            recent = by_date[:RECENT_VIDEO_COUNT]
            popular = sorted(
                longform, key=lambda x: x.get("view_count") or 0, reverse=True
            )[:4]
            recent_avg = recent_average_views(recent)
            videos_last_14d = videos_posted_last_days(by_date, days=14)
            subs = int(ch.get("subscriber_count") or 0)
            lifetime_avg = int(ch.get("avg_views_per_video") or 0)
            display_avg = lifetime_avg or recent_avg
            ratio = (
                round(recent_avg / subs, 3)
                if subs > 0 and recent_avg > 0
                else (round(display_avg / subs, 3) if subs > 0 else 0.0)
            )
            days = _days_since(ch.get("published_at") or "")
            video_count = int(ch.get("video_count") or 0)
            if days and days > 1 and video_count > 0:
                uploads_per_month = round(video_count / (days / 30.0), 2)
            elif len(by_date) >= 2:
                d0 = _days_since(by_date[0].get("published_at") or "") or 0
                d1 = _days_since(by_date[-1].get("published_at") or "") or 0
                span = abs(d1 - d0) or 1
                uploads_per_month = round((len(by_date) - 1) / (span / 30.0), 2)
            else:
                uploads_per_month = 0.0

            top_views = max((int(v.get("view_count") or 0) for v in popular), default=0)
            outlier = (
                round(top_views / display_avg, 2)
                if display_avg > 0 and top_views > 0
                else 0.0
            )
            rev = estimate_monthly_revenue_usd(
                avg_views=float(display_avg),
                uploads_per_month=uploads_per_month,
                rpm_usd=DEFAULT_RPM_USD,
            )
            rev_recent = estimate_monthly_revenue_usd(
                avg_views=float(recent_avg or display_avg),
                uploads_per_month=uploads_per_month,
                rpm_usd=DEFAULT_RPM_USD,
            )
            score = score_channel(
                subscriber_count=subs,
                avg_views_per_video=float(display_avg),
                recent_avg_views=float(recent_avg),
                view_to_sub_ratio=ratio,
                uploads_per_month=uploads_per_month,
                days_since_start=days,
                outlier_score=outlier,
                est_monthly_revenue_usd=rev_recent["est_monthly_revenue_usd"],
            )

            def _vid_row(v: dict) -> dict:
                return {
                    "title": v.get("title"),
                    "url": v.get("url"),
                    "thumbnail": v.get("thumbnail"),
                    "view_count": v.get("view_count"),
                    "duration_sec": v.get("duration_sec"),
                    "published_at": v.get("published_at"),
                }

            hits.append(
                {
                    "channel_id": cid,
                    "channel_name": ch.get("channel_name"),
                    "channel_url": ch.get("channel_url"),
                    "avatar_url": ch.get("avatar_url"),
                    "source_keyword": kw,
                    "subscriber_count": subs,
                    "video_count": video_count,
                    "days_since_start": round(days) if days is not None else None,
                    "avg_views_per_video": display_avg,
                    "recent_avg_views": recent_avg,
                    "view_to_sub_ratio": ratio,
                    "uploads_per_month": uploads_per_month,
                    "videos_last_14d": videos_last_14d,
                    "outlier_score": outlier,
                    "likely_monetized": subs >= 1000,
                    "score": score,
                    **rev,
                    "est_recent_monthly_revenue_usd": rev_recent[
                        "est_monthly_revenue_usd"
                    ],
                    "est_recent_monthly_revenue_low_usd": rev_recent[
                        "est_monthly_revenue_low_usd"
                    ],
                    "est_recent_monthly_revenue_high_usd": rev_recent[
                        "est_monthly_revenue_high_usd"
                    ],
                    "recent_videos": [_vid_row(v) for v in recent[:4]],
                    "popular_videos": [_vid_row(v) for v in popular],
                }
            )
            _log(f"Enriched {ch.get('channel_name')} ({cid})")
        except Exception as e:
            errors.append({"channel_id": cid, "error": str(e)})
            _log(f"Enrich failed for {cid}: {e}")

    return {
        "hits": hits,
        "errors": errors,
        "meta": {
            "requested": len(cleaned),
            "resolved": len(channel_ids),
            "enriched": len(hits),
            "source_keyword": kw,
        },
    }


def seed_gta6_pack(*, api_key: str, progress: ProgressCb | None = None) -> dict[str, Any]:
    return seed_channels_from_urls(
        GTA6_CHANNEL_URLS,
        api_key=api_key,
        source_keyword=GTA6_SOURCE_KEYWORD,
        progress=progress,
    )


def seed_gta6_into_db(*, api_key: str, progress: ProgressCb | None = None) -> dict[str, Any]:
    """Seed + upsert into niche_channels. For CLI / ops."""
    from webapp.database import upsert_niche_channels

    result = seed_gta6_pack(api_key=api_key, progress=progress)
    hits = result.get("hits") or []
    n = upsert_niche_channels(hits, source_keyword=GTA6_SOURCE_KEYWORD) if hits else 0
    result["upserted"] = n
    return result


if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from dotenv import load_dotenv
    load_dotenv(root / ".env", override=True)
    import config

    key = (config.YOUTUBE_API_KEY or os.getenv("YOUTUBE_API_KEY") or "").strip()
    if not key:
        print("YOUTUBE_API_KEY required", file=sys.stderr)
        sys.exit(1)
    out = seed_gta6_into_db(api_key=key)
    print(json.dumps({
        "upserted": out.get("upserted"),
        "meta": out.get("meta"),
        "errors": out.get("errors"),
    }, indent=2, default=str))

