"""
Long-form Niche Finder — keyword hunt for underserved YouTube niches.

Uses YouTube Data API only (no per-video related scrape — too slow).
Long-form = duration >= MIN_DURATION_SEC (default 4 minutes). No max.
"""

from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from googleapiclient.discovery import build

# ≥ 4 minutes; no upper bound
MIN_DURATION_SEC = 240
RECENT_VIDEO_COUNT = 4
DEFAULT_MAX_PER_KEYWORD = 12
DEFAULT_MAX_CHANNELS = 40

# Starter pack oriented at faceless / story / documentary long-form
DEFAULT_KEYWORDS = [
    "history documentary",
    "true story explained",
    "what happened to",
    "why did they",
    "ancient mysteries",
    "untold history",
    "dark history facts",
    "science explained",
    "how the world works",
    "lost civilizations",
]


ProgressCb = Callable[[str], None]


def parse_duration_iso8601(duration: str) -> int:
    """PT#H#M#S → seconds."""
    if not duration:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


def _yt(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def _search_video_ids(
    youtube,
    keyword: str,
    *,
    video_duration: str,
    max_results: int,
) -> list[str]:
    resp = (
        youtube.search()
        .list(
            part="id",
            q=keyword,
            type="video",
            videoDuration=video_duration,
            order="viewCount",
            maxResults=min(25, max(1, max_results)),
            relevanceLanguage="en",
        )
        .execute()
    )
    ids = []
    for item in resp.get("items") or []:
        vid = (item.get("id") or {}).get("videoId")
        if vid:
            ids.append(vid)
    return ids


def _chunk(xs: list[str], n: int = 50) -> list[list[str]]:
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def _fetch_videos(youtube, video_ids: list[str]) -> list[dict]:
    out: list[dict] = []
    for batch in _chunk(video_ids, 50):
        if not batch:
            continue
        resp = (
            youtube.videos()
            .list(part="snippet,contentDetails,statistics", id=",".join(batch))
            .execute()
        )
        for item in resp.get("items") or []:
            dur = parse_duration_iso8601(
                (item.get("contentDetails") or {}).get("duration") or ""
            )
            if dur < MIN_DURATION_SEC:
                continue
            sn = item.get("snippet") or {}
            st = item.get("statistics") or {}
            thumbs = sn.get("thumbnails") or {}
            thumb = (
                (thumbs.get("medium") or {}).get("url")
                or (thumbs.get("high") or {}).get("url")
                or (thumbs.get("default") or {}).get("url")
                or ""
            )
            out.append(
                {
                    "video_id": item["id"],
                    "title": sn.get("title") or "",
                    "channel_id": sn.get("channelId") or "",
                    "channel_title": sn.get("channelTitle") or "",
                    "published_at": sn.get("publishedAt") or "",
                    "duration_sec": dur,
                    "view_count": int(st.get("viewCount") or 0),
                    "thumbnail": thumb,
                    "url": f"https://www.youtube.com/watch?v={item['id']}",
                }
            )
    return out


def _fetch_channels(youtube, channel_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for batch in _chunk(list(dict.fromkeys(channel_ids)), 50):
        if not batch:
            continue
        resp = (
            youtube.channels()
            .list(
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
            )
            .execute()
        )
        for item in resp.get("items") or []:
            sn = item.get("snippet") or {}
            st = item.get("statistics") or {}
            cd = item.get("contentDetails") or {}
            thumbs = sn.get("thumbnails") or {}
            avatar = (
                (thumbs.get("medium") or {}).get("url")
                or (thumbs.get("default") or {}).get("url")
                or ""
            )
            out[item["id"]] = {
                "channel_id": item["id"],
                "channel_name": sn.get("title") or "",
                "channel_url": f"https://www.youtube.com/channel/{item['id']}",
                "avatar_url": avatar,
                "subscriber_count": int(st.get("subscriberCount") or 0),
                "video_count": int(st.get("videoCount") or 0),
                "view_count_total": int(st.get("viewCount") or 0),
                "uploads_playlist": (cd.get("relatedPlaylists") or {}).get("uploads")
                or "",
                "published_at": sn.get("publishedAt") or "",
            }
    return out


def _recent_longform_from_uploads(
    youtube,
    uploads_playlist: str,
    *,
    want: int = RECENT_VIDEO_COUNT,
    scan: int = 20,
) -> list[dict]:
    """Pull recent uploads, keep long-form, return up to `want` with metrics."""
    if not uploads_playlist:
        return []
    try:
        resp = (
            youtube.playlistItems()
            .list(
                part="snippet,contentDetails",
                playlistId=uploads_playlist,
                maxResults=min(50, max(want * 3, scan)),
            )
            .execute()
        )
    except Exception as e:
        print(f"[niche_finder] playlistItems failed: {e}")
        return []

    ids = []
    for item in resp.get("items") or []:
        vid = (item.get("contentDetails") or {}).get("videoId") or (
            (item.get("snippet") or {}).get("resourceId") or {}
        ).get("videoId")
        if vid:
            ids.append(vid)

    if not ids:
        return []
    return _fetch_videos(youtube, ids)[:want]


def _days_since(iso: str) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return None


def _uploads_per_month(recent: list[dict]) -> float:
    """Rough cadence from the span of recent long-form publish dates."""
    if len(recent) < 2:
        return 0.0
    days = []
    for v in recent:
        d = _days_since(v.get("published_at") or "")
        if d is not None:
            days.append(d)
    if len(days) < 2:
        return 0.0
    span = max(days) - min(days)
    if span < 1:
        return float(len(recent))
    return round((len(recent) - 1) / (span / 30.0), 2)


def score_channel(
    *,
    subscriber_count: int,
    recent_avg_views: float,
    view_to_sub_ratio: float,
    uploads_per_month: float,
) -> float:
    """
    Simple viral-niche score (higher = more interesting to clone into).

    - Strong recent views vs small audience (ratio)
    - Absolute recent avg views (log-scaled)
    - Healthy cadence (not dead, not spammy daily mega-uploaders)
    - Soft penalty for very large channels
    """
    ratio = max(0.0, view_to_sub_ratio)
    recent = max(0.0, recent_avg_views)
    cadence = max(0.0, uploads_per_month)

    score = 0.0
    score += min(ratio, 50.0) * 2.0
    score += math.log10(recent + 1) * 12.0

    # Ideal-ish: ~2–12 long uploads / month
    if 2.0 <= cadence <= 12.0:
        score += 8.0
    elif 0.5 <= cadence < 2.0:
        score += 4.0
    elif cadence > 20:
        score -= 6.0
    elif cadence > 0:
        score += 1.0

    if subscriber_count >= 1_000_000:
        score -= 15.0
    elif subscriber_count >= 500_000:
        score -= 8.0
    elif subscriber_count >= 200_000:
        score -= 3.0
    elif 1_000 <= subscriber_count <= 100_000:
        score += 5.0

    return round(max(0.0, score), 2)


def run_niche_finder(
    *,
    api_key: str,
    keywords: list[str] | None = None,
    max_per_keyword: int = DEFAULT_MAX_PER_KEYWORD,
    max_channels: int = DEFAULT_MAX_CHANNELS,
    min_recent_avg_views: int = 0,
    max_subscribers: int = 2_000_000,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """
    Run a keyword long-form hunt. Returns {hits, meta}.
    """
    def _log(msg: str) -> None:
        print(f"[niche_finder] {msg}")
        if progress:
            progress(msg)

    if not api_key:
        raise ValueError("YouTube API key not configured")

    kws = [k.strip() for k in (keywords or DEFAULT_KEYWORDS) if k and k.strip()]
    if not kws:
        raise ValueError("Add at least one keyword")
    kws = kws[:30]

    youtube = _yt(api_key)
    video_ids: list[str] = []
    seen_vids: set[str] = set()

    for i, kw in enumerate(kws):
        _log(f"Searching ({i + 1}/{len(kws)}): {kw}")
        for duration in ("medium", "long"):
            try:
                ids = _search_video_ids(
                    youtube, kw, video_duration=duration, max_results=max_per_keyword
                )
            except Exception as e:
                _log(f"Search failed for '{kw}' ({duration}): {e}")
                continue
            for vid in ids:
                if vid not in seen_vids:
                    seen_vids.add(vid)
                    video_ids.append(vid)
        time.sleep(0.05)

    _log(f"Enriching {len(video_ids)} candidate videos…")
    videos = _fetch_videos(youtube, video_ids)
    by_channel: dict[str, list[dict]] = {}
    for v in videos:
        cid = v.get("channel_id")
        if not cid:
            continue
        by_channel.setdefault(cid, []).append(v)

    channel_ids = list(by_channel.keys())
    _log(f"Fetching {len(channel_ids)} channels…")
    channels = _fetch_channels(youtube, channel_ids)

    hits: list[dict] = []
    # Enrich recent long-form in parallel (bounded)
    to_enrich = [
        (cid, ch)
        for cid, ch in channels.items()
        if ch.get("subscriber_count", 0) <= max_subscribers
    ]

    def _enrich_one(cid: str, ch: dict) -> dict | None:
        seed_videos = sorted(
            by_channel.get(cid, []),
            key=lambda x: x.get("view_count") or 0,
            reverse=True,
        )
        recent = _recent_longform_from_uploads(
            youtube, ch.get("uploads_playlist") or "", want=RECENT_VIDEO_COUNT
        )
        # Prefer playlist recent; fall back to search hits for this channel
        if len(recent) < 2:
            recent = (recent + seed_videos)[:RECENT_VIDEO_COUNT]
        if not recent:
            return None

        views = [int(v.get("view_count") or 0) for v in recent]
        recent_avg = round(sum(views) / len(views)) if views else 0
        if min_recent_avg_views and recent_avg < min_recent_avg_views:
            return None

        subs = int(ch.get("subscriber_count") or 0)
        ratio = round(recent_avg / subs, 3) if subs > 0 else 0.0
        cadence = _uploads_per_month(recent)
        score = score_channel(
            subscriber_count=subs,
            recent_avg_views=float(recent_avg),
            view_to_sub_ratio=ratio,
            uploads_per_month=cadence,
        )
        sample = seed_videos[0] if seed_videos else recent[0]
        return {
            "channel_id": cid,
            "channel_name": ch.get("channel_name"),
            "channel_url": ch.get("channel_url"),
            "avatar_url": ch.get("avatar_url"),
            "subscriber_count": subs,
            "video_count": ch.get("video_count"),
            "recent_avg_views": recent_avg,
            "view_to_sub_ratio": ratio,
            "uploads_per_month": cadence,
            "score": score,
            "sample_video": {
                "title": sample.get("title"),
                "url": sample.get("url"),
                "thumbnail": sample.get("thumbnail"),
                "view_count": sample.get("view_count"),
                "duration_sec": sample.get("duration_sec"),
            },
            "recent_videos": [
                {
                    "title": v.get("title"),
                    "url": v.get("url"),
                    "thumbnail": v.get("thumbnail"),
                    "view_count": v.get("view_count"),
                    "duration_sec": v.get("duration_sec"),
                    "published_at": v.get("published_at"),
                }
                for v in recent[:RECENT_VIDEO_COUNT]
            ],
        }

    _log(f"Scoring {len(to_enrich)} channels (recent long-form)…")
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_enrich_one, cid, ch) for cid, ch in to_enrich]
        for fut in as_completed(futs):
            try:
                hit = fut.result()
            except Exception as e:
                print(f"[niche_finder] enrich error: {e}")
                continue
            if hit:
                hits.append(hit)

    hits.sort(key=lambda h: h.get("score") or 0, reverse=True)
    hits = hits[: max(1, max_channels)]
    _log(f"Done — {len(hits)} niches ranked")

    return {
        "hits": hits,
        "meta": {
            "keywords": kws,
            "videos_scanned": len(videos),
            "channels_considered": len(channel_ids),
            "min_duration_sec": MIN_DURATION_SEC,
            "recent_video_count": RECENT_VIDEO_COUNT,
        },
    }
