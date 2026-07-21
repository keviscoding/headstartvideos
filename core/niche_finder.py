"""
Long-form Niche Finder — keyword hunt for enterable faceless YouTube niches.

Inspired by how operators use NexLev:
  - small/mid channels (enterable)
  - solid avg views
  - days since start + upload volume (factory cadence)
  - outlier hits (top video vs channel avg)
  - rough monthly revenue estimate (views × RPM)

Long-form = duration >= MIN_DURATION_SEC (default 4 minutes). No max.
Related/suggested expansion is deferred (use later as a second hop, not on every video).
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from googleapiclient.discovery import build

# ≥ 4 minutes; no upper bound
MIN_DURATION_SEC = 240
RECENT_VIDEO_COUNT = 8  # last N uploads for recent avg (ViewHunt-style)
POPULAR_SCAN = 24  # pull enough long-form from uploads playlist
DEFAULT_MAX_PER_KEYWORD = 12
DEFAULT_MAX_CHANNELS = 60

# Conservative faceless long-form RPM assumption for English markets.
# Real RPM varies wildly ($1–$12+); we show a midpoint + band in the UI.
DEFAULT_RPM_USD = 4.0

# Broad starter pack — discovery is meant to be sprawling; filters sort later.
DEFAULT_KEYWORDS = [
    "history documentary explained",
    "true story folktale",
    "forbidden history mysteries",
    "dark history facts",
    "geopolitics russia explained",
    "sci fi stories HFY",
    "christian prayer night",
    "HOA revenge story",
    "personal finance explained",
    "stoic habits self improvement",
    "american football drama story",
    "anime comic dub story",
    "war documentary full movie",
    "what happened to",
    "untold history",
    "reddit story narrated",
    "sleep story bedtime",
    "true crime documentary",
    "conspiracy unexplained",
    "bible prophecy explained",
    "motivational speech stoic",
    "retirement millionaire habits",
    "space documentary full",
    "ancient civilization mystery",
    "celebrity drama story",
    "military documentary narrated",
    "horror story narrated long",
    "business case study explained",
    "psychology facts explained",
    "relationship advice stories",
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
    pages: int = 3,
) -> list[str]:
    """
    Pull multiple YouTube search pages (nextPageToken) so we go deeper than
    the first result screen — same idea as scrolling to the bottom of search.
    """
    want = max(1, int(max_results))
    pages = max(1, min(int(pages), 5))
    ids: list[str] = []
    seen: set[str] = set()
    page_token: str | None = None
    for _ in range(pages):
        if len(ids) >= want:
            break
        kwargs = dict(
            part="id",
            q=keyword,
            type="video",
            videoDuration=video_duration,
            order="relevance",
            maxResults=min(25, want - len(ids) if want - len(ids) > 0 else 25),
            relevanceLanguage="en",
        )
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            resp = youtube.search().list(**kwargs).execute()
        except Exception:
            break
        for item in resp.get("items") or []:
            vid = (item.get("id") or {}).get("videoId")
            if vid and vid not in seen:
                seen.add(vid)
                ids.append(vid)
                if len(ids) >= want:
                    break
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.15)
    return ids[:want]


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
            video_count = int(st.get("videoCount") or 0)
            view_total = int(st.get("viewCount") or 0)
            out[item["id"]] = {
                "channel_id": item["id"],
                "channel_name": sn.get("title") or "",
                "channel_url": f"https://www.youtube.com/channel/{item['id']}",
                "avatar_url": avatar,
                "subscriber_count": int(st.get("subscriberCount") or 0),
                "video_count": video_count,
                "view_count_total": view_total,
                "avg_views_per_video": (
                    round(view_total / video_count) if video_count > 0 else 0
                ),
                "uploads_playlist": (cd.get("relatedPlaylists") or {}).get("uploads")
                or "",
                "published_at": sn.get("publishedAt") or "",
            }
    return out


def _longform_from_uploads(
    youtube,
    uploads_playlist: str,
    *,
    want: int = POPULAR_SCAN,
    scan: int = 40,
) -> list[dict]:
    """Pull recent uploads, keep long-form only."""
    if not uploads_playlist:
        return []
    resp = None
    last_err = None
    for attempt in range(2):
        try:
            resp = (
                youtube.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=uploads_playlist,
                    maxResults=min(50, max(want * 2, scan)),
                )
                .execute()
            )
            break
        except Exception as e:
            last_err = e
            time.sleep(0.35 * (attempt + 1))
    if resp is None:
        print(f"[niche_finder] playlistItems failed: {last_err}")
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


def estimate_monthly_revenue_usd(
    *,
    avg_views: float,
    uploads_per_month: float,
    rpm_usd: float = DEFAULT_RPM_USD,
) -> dict[str, float]:
    """
    Rough operator math (same spirit as sorting NexLev by monthly revenue):
      monthly_views ≈ avg_views × uploads_per_month
      revenue ≈ monthly_views × RPM / 1000

    Cap cadence for the estimate — factory channels can post a lot, but
    300+/mo is usually a data quirk and shouldn't dominate the economics.
    """
    cadence = min(max(0.0, uploads_per_month), 90.0)  # ~3 long-form/day
    monthly_views = max(0.0, avg_views) * cadence
    mid = monthly_views * rpm_usd / 1000.0
    return {
        "est_monthly_views": round(monthly_views),
        "est_monthly_revenue_usd": round(mid, 2),
        "est_monthly_revenue_low_usd": round(monthly_views * 2.0 / 1000.0, 2),
        "est_monthly_revenue_high_usd": round(monthly_views * 8.0 / 1000.0, 2),
        "rpm_assumed": rpm_usd,
    }


def recent_average_views(videos: list[dict]) -> float:
    """
    ViewHunt-style recent average from newest uploads:
    mean of view counts; if a viral outlier (>4× rest), use trimmed mean.
    """
    counts = sorted(
        (int(v.get("view_count") or 0) for v in videos),
        reverse=True,
    )
    counts = [c for c in counts if c > 0]
    if not counts:
        return 0.0
    mean = sum(counts) / len(counts)
    if len(counts) >= 3:
        mx = counts[0]
        rest = counts[1:]
        rest_avg = sum(rest) / len(rest) if rest else 0
        if rest_avg > 0 and mx / rest_avg > 4:
            trimmed = counts[1:-1] if len(counts) >= 3 else counts
            return round(sum(trimmed) / len(trimmed))
    return round(mean)


def videos_posted_last_days(videos: list[dict], *, days: int = 14) -> int:
    n = 0
    for v in videos:
        age = _days_since(v.get("published_at") or "")
        if age is not None and age <= days:
            n += 1
    return n


def score_channel(
    *,
    subscriber_count: int,
    avg_views_per_video: float,
    recent_avg_views: float,
    view_to_sub_ratio: float,
    uploads_per_month: float,
    days_since_start: float | None,
    outlier_score: float,
    est_monthly_revenue_usd: float,
) -> float:
    """
    Soft ranking for enterable faceless niches — not a hyper-optimized oracle.

    What we're looking for (from operator NexLev examples):
      - ~1K–50K subs (you can still enter)
      - avg views in the low thousands+ (often ~3–5K in good finds)
      - real upload factory cadence
      - some breakout / consistent traction (outlier or solid recent)
      - non-trivial estimated monthly revenue
    """
    score = 0.0
    avg_v = max(0.0, avg_views_per_video)
    recent = max(0.0, recent_avg_views)
    ratio = max(0.0, view_to_sub_ratio)
    cadence = max(0.0, uploads_per_month)
    outlier = max(0.0, outlier_score)
    rev = max(0.0, est_monthly_revenue_usd)

    # Enterable sub band (NexLev cards cluster ~3K–25K)
    if 1_500 <= subscriber_count <= 40_000:
        score += 18.0
    elif 500 <= subscriber_count < 1_500:
        score += 10.0
    elif 40_000 < subscriber_count <= 120_000:
        score += 6.0
    elif subscriber_count > 250_000:
        score -= 12.0

    # Channel avg views (lifetime) — the main NexLev card number
    if avg_v >= 4000:
        score += 14.0
    elif avg_v >= 2500:
        score += 10.0
    elif avg_v >= 1200:
        score += 6.0
    elif avg_v >= 500:
        score += 2.0

    # Recent long-form avg (last few uploads)
    score += min(math.log10(recent + 1) * 6.0, 18.0)

    # Views vs subs (recent)
    score += min(ratio, 8.0) * 3.0

    # Cadence: factory niches often post very often.
    # Reward activity without requiring a narrow band (avoid overfit).
    if cadence >= 20:  # ~daily long-form factory
        score += 12.0
    elif cadence >= 8:
        score += 10.0
    elif cadence >= 3:
        score += 7.0
    elif cadence >= 1:
        score += 3.0

    # Young-ish channels that already work are gold (easy to copy format)
    if days_since_start is not None:
        if days_since_start <= 120:
            score += 10.0
        elif days_since_start <= 365:
            score += 6.0
        elif days_since_start <= 900:
            score += 2.0
        elif days_since_start > 2000 and cadence < 2:
            # Old + slow = usually one viral legacy, not a copyable factory niche
            score -= 18.0
        elif days_since_start > 1500:
            score -= 6.0

    # Outlier = top popular / avg (breakout proof the niche can pop)
    if outlier >= 10:
        score += 10.0
    elif outlier >= 4:
        score += 7.0
    elif outlier >= 2:
        score += 3.0

    # Economics — prefer channels that look like they already print
    if rev >= 2000:
        score += 12.0
    elif rev >= 800:
        score += 8.0
    elif rev >= 300:
        score += 5.0
    elif rev >= 100:
        score += 2.0

    return round(max(0.0, score), 2)


def run_niche_finder(
    *,
    api_key: str,
    keywords: list[str] | None = None,
    max_per_keyword: int = DEFAULT_MAX_PER_KEYWORD,
    max_channels: int = DEFAULT_MAX_CHANNELS,
    min_recent_avg_views: int = 0,
    max_subscribers: int = 150_000,
    rpm_usd: float = DEFAULT_RPM_USD,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """Run a keyword long-form hunt. Returns {hits, meta}."""

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
                # Dig through ~3 search pages per duration (scroll deeper)
                ids = _search_video_ids(
                    youtube,
                    kw,
                    video_duration=duration,
                    max_results=max(max_per_keyword, 40),
                    pages=3,
                )
            except Exception as e:
                _log(f"Search failed for '{kw}' ({duration}): {e}")
                continue
            for vid in ids:
                if vid not in seen_vids:
                    seen_vids.add(vid)
                    video_ids.append(vid)
            _log(f"  {duration}: +{len(ids)} videos (total unique {len(video_ids)})")
        time.sleep(0.35)  # breathe between keywords so quota/pages settle

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
    to_enrich = [
        (cid, ch)
        for cid, ch in channels.items()
        if ch.get("subscriber_count", 0) <= max_subscribers
        and ch.get("subscriber_count", 0) >= 100
    ]

    def _enrich_one(cid: str, ch: dict) -> dict | None:
        seed_videos = sorted(
            by_channel.get(cid, []),
            key=lambda x: x.get("view_count") or 0,
            reverse=True,
        )
        longform = _longform_from_uploads(
            youtube, ch.get("uploads_playlist") or "", want=POPULAR_SCAN
        )
        # Always fold in keyword-search hits for richer video sample.
        merged = {v["video_id"]: v for v in longform}
        for v in seed_videos:
            merged.setdefault(v["video_id"], v)
        longform = list(merged.values())
        if not longform:
            return None

        # Recent = newest by publish date (what operators actually want to see)
        by_date = sorted(
            longform,
            key=lambda x: x.get("published_at") or "",
            reverse=True,
        )
        recent = by_date[:RECENT_VIDEO_COUNT]
        popular = sorted(
            longform, key=lambda x: x.get("view_count") or 0, reverse=True
        )[:4]

        recent_avg = recent_average_views(recent)
        if min_recent_avg_views and recent_avg < min_recent_avg_views:
            return None

        videos_last_14d = videos_posted_last_days(by_date, days=14)

        subs = int(ch.get("subscriber_count") or 0)
        lifetime_avg = int(ch.get("avg_views_per_video") or 0)
        # Card avg: lifetime views / uploads (NexLev-style), fallback to recent.
        display_avg = lifetime_avg or recent_avg
        ratio = round(recent_avg / subs, 3) if subs > 0 and recent_avg > 0 else (
            round(display_avg / subs, 3) if subs > 0 else 0.0
        )

        days = _days_since(ch.get("published_at") or "")
        video_count = int(ch.get("video_count") or 0)
        if days and days > 1 and video_count > 0:
            uploads_per_month = round(video_count / (days / 30.0), 2)
        else:
            # Fallback from recent publish span
            if len(by_date) >= 2:
                d0 = _days_since(by_date[0].get("published_at") or "") or 0
                d1 = _days_since(by_date[-1].get("published_at") or "") or 0
                span = abs(d1 - d0) or 1
                uploads_per_month = round((len(by_date) - 1) / (span / 30.0), 2)
            else:
                uploads_per_month = 0.0

        # Soft skip: ancient + idle channels (legacy virals, not copyable factories)
        if days is not None and days > 2500 and uploads_per_month < 1.0:
            return None

        top_views = max((int(v.get("view_count") or 0) for v in popular), default=0)
        outlier = (
            round(top_views / display_avg, 2)
            if display_avg > 0 and top_views > 0
            else 0.0
        )

        # Lifetime economics (catalog) + recent economics (is it still printing?)
        rev = estimate_monthly_revenue_usd(
            avg_views=float(display_avg),
            uploads_per_month=uploads_per_month,
            rpm_usd=rpm_usd,
        )
        rev_recent = estimate_monthly_revenue_usd(
            avg_views=float(recent_avg or display_avg),
            uploads_per_month=uploads_per_month,
            rpm_usd=rpm_usd,
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

        likely_monetized = subs >= 1000  # YouTube partner threshold proxy

        def _vid_row(v: dict) -> dict:
            return {
                "title": v.get("title"),
                "url": v.get("url"),
                "thumbnail": v.get("thumbnail"),
                "view_count": v.get("view_count"),
                "duration_sec": v.get("duration_sec"),
                "published_at": v.get("published_at"),
            }

        return {
            "channel_id": cid,
            "channel_name": ch.get("channel_name"),
            "channel_url": ch.get("channel_url"),
            "avatar_url": ch.get("avatar_url"),
            "subscriber_count": subs,
            "video_count": video_count,
            "days_since_start": round(days) if days is not None else None,
            "avg_views_per_video": display_avg,
            "recent_avg_views": recent_avg,
            "view_to_sub_ratio": ratio,
            "uploads_per_month": uploads_per_month,
            "videos_last_14d": videos_last_14d,
            "outlier_score": outlier,
            "likely_monetized": likely_monetized,
            "score": score,
            **rev,
            "est_recent_monthly_revenue_usd": rev_recent["est_monthly_revenue_usd"],
            "est_recent_monthly_revenue_low_usd": rev_recent["est_monthly_revenue_low_usd"],
            "est_recent_monthly_revenue_high_usd": rev_recent["est_monthly_revenue_high_usd"],
            # Primary gallery = most recent long-form
            "recent_videos": [_vid_row(v) for v in recent[:4]],
            "popular_videos": [_vid_row(v) for v in popular],
        }

    _log(f"Scoring {len(to_enrich)} channels…")
    # Sequential enrich — parallel googleapiclient calls flake hard (SSL/timeouts).
    for cid, ch in to_enrich:
        try:
            hit = _enrich_one(cid, ch)
        except Exception as e:
            print(f"[niche_finder] enrich error: {e}")
            continue
        if hit:
            hits.append(hit)

    hits.sort(
        key=lambda h: (
            h.get("est_monthly_revenue_usd") or 0,
            h.get("score") or 0,
        ),
        reverse=True,
    )
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
            "rpm_assumed": rpm_usd,
            "note": (
                "Revenue is an estimate: avg_views × uploads/month × RPM/1000. "
                "Default RPM $4 (band $2–$8). Sort mentally by est revenue + enterability."
            ),
        },
    }
