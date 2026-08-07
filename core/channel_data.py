"""
Automated channel data collection.

Uses YouTube Data API v3 to list a channel's videos (title, views, publish date)
and multiple caption sources for transcripts. Combines into structured channel
data for use in Script Studio.
"""

from __future__ import annotations
import html
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

# DownSub returns 401/403 when the key is invalid or the account is unpaid.
# Disable only for that exact key so renewing/pasting a new key re-enables it
# without waiting for a process restart.
_downsub_disabled_for_key: str = ""
_downsub_disabled_reason: str | None = None

# In-process cache for video metadata (title/author/views).
_meta_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_META_TTL_SEC = 3600.0
# ASR for MCP: skip videos longer than this (latency / cost).
_ASR_MAX_SECONDS = 45 * 60


def reset_downsub_circuit(reason: str = "manual") -> None:
    """Clear a DownSub outage latch (e.g. after the API key is updated)."""
    global _downsub_disabled_for_key, _downsub_disabled_reason
    if _downsub_disabled_reason:
        print(f"[channel_data] DownSub re-enabled ({reason}; was: {_downsub_disabled_reason})")
    _downsub_disabled_for_key = ""
    _downsub_disabled_reason = None


def _downsub_is_disabled(key: str) -> bool:
    return bool(_downsub_disabled_reason) and _downsub_disabled_for_key == (key or "")


def _disable_downsub(key: str, reason: str) -> None:
    global _downsub_disabled_for_key, _downsub_disabled_reason
    _downsub_disabled_for_key = key or ""
    _downsub_disabled_reason = reason
    print(f"[channel_data] DownSub disabled for this key ({reason})")


def _extract_channel_id(url: str, yt_api_key: str) -> str:
    """
    Extract channel ID from various YouTube URL formats:
    - youtube.com/channel/UCxxxxxx
    - youtube.com/@handle
    - youtube.com/c/customname
    - youtube.com/user/name
    """
    url = url.strip().rstrip("/")

    match = re.search(r"/channel/(UC[\w-]+)", url, re.I)
    if match:
        return match.group(1)

    # Bare UC… id pasted as the whole "URL"
    bare = re.fullmatch(r"(UC[\w-]{20,})", url)
    if bare:
        return bare.group(1)

    handle_match = re.search(r"/@([\w.-]+)", url)
    custom_match = re.search(r"/c/([\w.-]+)", url)
    user_match = re.search(r"/user/([\w.-]+)", url)
    username = (
        handle_match.group(1) if handle_match
        else (custom_match.group(1) if custom_match
              else (user_match.group(1) if user_match else None))
    )

    if username:
        # Official API — most reliable for @handles
        channel_id = _resolve_handle_via_api(username, yt_api_key)
        if channel_id:
            return channel_id

        # Page scrape (works when API quota/scopes are limited)
        channel_id = _resolve_handle_via_page(username)
        if channel_id:
            return channel_id

        # Last resort: search (often needs broader API enablement)
        channel_id = _resolve_via_search(username, yt_api_key, prefer_handle=bool(handle_match))
        if channel_id:
            return channel_id

        raise ValueError(
            f"No YouTube channel found for @{username}. "
            "Check the handle spelling, or paste a channel URL like "
            "youtube.com/channel/UCxxxx or youtube.com/@handle."
        )

    raise ValueError(
        f"Could not extract channel ID from: {url}. "
        "Use a URL like youtube.com/@handle or youtube.com/channel/UCxxxx."
    )


def _resolve_handle_via_api(handle: str, yt_api_key: str) -> str | None:
    """Resolve @handle via channels.list forHandle (YouTube Data API v3)."""
    if not yt_api_key:
        return None
    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", developerKey=yt_api_key)
        for candidate in (handle, f"@{handle}"):
            resp = youtube.channels().list(part="id", forHandle=candidate).execute()
            items = resp.get("items") or []
            if items:
                cid = items[0].get("id")
                if cid:
                    print(f"[channel_data] forHandle @{handle} -> {cid}")
                    return cid
    except Exception as e:
        print(f"[channel_data] forHandle failed for @{handle}: {e}")
    return None


def _resolve_via_search(username: str, yt_api_key: str, prefer_handle: bool = True) -> str | None:
    if not yt_api_key:
        return None
    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", developerKey=yt_api_key)
        search_query = f"@{username}" if prefer_handle else username
        resp = youtube.search().list(
            part="snippet", q=search_query, type="channel", maxResults=1
        ).execute()
        items = resp.get("items", [])
        if not items:
            return None
        if "id" in items[0] and isinstance(items[0]["id"], dict):
            cid = items[0]["id"].get("channelId")
            if cid:
                return cid
        if "snippet" in items[0] and "channelId" in items[0]["snippet"]:
            return items[0]["snippet"]["channelId"]
    except Exception as e:
        print(f"[channel_data] search fallback failed for {username}: {e}")
    return None


def _resolve_handle_via_page(handle: str) -> str | None:
    """Resolve a YouTube handle to a channel ID by fetching the channel page."""
    try:
        resp = httpx.get(
            f"https://www.youtube.com/@{handle}",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            cookies={
                "CONSENT": "YES+cb.20210328-17-p0.en+FX+111",
                "SOCS": "CAISNQgDEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjMwODI5LjA3X3AxGgJlbiACGgYIgJnSmgY",
            },
            follow_redirects=True,
            timeout=15,
        )
        if resp.status_code == 404:
            print(f"[channel_data] Page 404 for @{handle} — channel likely does not exist")
            return None
        if resp.status_code == 200:
            for pattern in [
                r'"externalId"\s*:\s*"(UC[\w-]+)"',
                r'"channelId"\s*:\s*"(UC[\w-]+)"',
                r'channel/(UC[\w-]+)',
                r'"browseId"\s*:\s*"(UC[\w-]+)"',
            ]:
                match = re.search(pattern, resp.text)
                if match:
                    print(f"[channel_data] Resolved @{handle} -> {match.group(1)}")
                    return match.group(1)
    except Exception as e:
        print(f"[channel_data] Page scrape failed for @{handle}: {e}")
    return None


def _validate_yt_key(key: str) -> None:
    """Check that the key looks like a valid YouTube Data API v3 key."""
    if not key:
        raise ValueError("No YouTube API key provided. Add one in Settings.")
    if not key.startswith("AIza"):
        raise ValueError(
            "Invalid YouTube API key format. YouTube Data API v3 keys start with 'AIza...'. "
            "Get one from console.cloud.google.com -> APIs & Services -> Credentials."
        )


def _get_uploads_playlist(channel_id: str, yt_api_key: str) -> tuple[str, dict]:
    """Get the uploads playlist ID and channel metadata."""
    from googleapiclient.discovery import build
    youtube = build("youtube", "v3", developerKey=yt_api_key)

    resp = youtube.channels().list(
        part="contentDetails,statistics,snippet",
        id=channel_id,
    ).execute()

    items = resp.get("items", [])
    if not items:
        raise ValueError(f"Channel not found: {channel_id}")

    channel = items[0]
    playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    metadata = {
        "channel_name": channel["snippet"]["title"],
        "channel_id": channel_id,
        "subscribers": int(channel["statistics"].get("subscriberCount", 0)),
        "total_views": int(channel["statistics"].get("viewCount", 0)),
        "video_count": int(channel["statistics"].get("videoCount", 0)),
    }

    return playlist_id, metadata


def _list_videos(playlist_id: str, yt_api_key: str, max_videos: int = 20) -> list[dict]:
    """
    List videos from a playlist using playlistItems.list (1 quota unit per page).
    Returns list of {video_id, title, published_at}.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    youtube = build("youtube", "v3", developerKey=yt_api_key)

    videos = []
    page_token = None

    try:
        while len(videos) < max_videos:
            resp = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=min(50, max_videos - len(videos)),
                pageToken=page_token,
            ).execute()

            for item in resp.get("items", []):
                videos.append({
                    "video_id": item["contentDetails"]["videoId"],
                    "title": item["snippet"]["title"],
                    "published_at": item["snippet"]["publishedAt"],
                })

            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        reason = ""
        try:
            reason = e.error_details[0].get("reason", "") if e.error_details else ""
        except Exception:
            reason = ""
        status = getattr(e.resp, "status", None) if getattr(e, "resp", None) else None
        if status == 404 or reason == "playlistNotFound" or "playlistNotFound" in str(e):
            raise ValueError(
                "This channel's uploads playlist is unavailable (private, empty, "
                "or removed). Try another channel URL, or a channel with public videos."
            ) from e
        raise

    return videos[:max_videos]


def _list_videos_via_search(channel_id: str, yt_api_key: str, max_videos: int = 20) -> list[dict]:
    """Fallback when uploads playlist 404s — search.list by channelId."""
    from googleapiclient.discovery import build
    youtube = build("youtube", "v3", developerKey=yt_api_key)

    videos = []
    page_token = None
    while len(videos) < max_videos:
        resp = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=min(50, max_videos - len(videos)),
            pageToken=page_token,
        ).execute()
        for item in resp.get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            if not vid:
                continue
            sn = item.get("snippet") or {}
            videos.append({
                "video_id": vid,
                "title": sn.get("title") or "",
                "published_at": sn.get("publishedAt") or "",
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return videos[:max_videos]


def _get_video_stats(video_ids: list[str], yt_api_key: str) -> dict[str, dict]:
    """
    Get view counts and other stats for a batch of video IDs.
    Processes in batches of 50 (1 quota unit per batch).
    """
    from googleapiclient.discovery import build
    youtube = build("youtube", "v3", developerKey=yt_api_key)

    stats = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = youtube.videos().list(
            part="statistics,contentDetails",
            id=",".join(batch),
        ).execute()

        for item in resp.get("items", []):
            vid = item["id"]
            s = item["statistics"]
            stats[vid] = {
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
                "duration": item["contentDetails"].get("duration", ""),
            }

    return stats


def parse_youtube_video_id(raw: str) -> str | None:
    """Extract a YouTube video id from a URL or bare id. None if unusable."""
    s = (raw or "").strip()
    if not s:
        return None
    m = re.search(
        r"(?:youtu\.be/|v=|/shorts/|/embed/|youtube\.com/watch\?.*?v=)([A-Za-z0-9_-]{6,})",
        s,
    )
    video_id = m.group(1) if m else s
    video_id = re.sub(r"[^A-Za-z0-9_-]", "", video_id)[:20]
    if len(video_id) < 6:
        return None
    return video_id


def _fetch_video_meta_ytdata(video_id: str, yt_api_key: str) -> dict[str, Any] | None:
    """Primary metadata via YouTube Data API v3."""
    key = (yt_api_key or "").strip()
    if not key:
        return None
    try:
        from googleapiclient.discovery import build

        youtube = build("youtube", "v3", developerKey=key)
        resp = youtube.videos().list(
            part="snippet,statistics",
            id=video_id,
        ).execute()
        items = resp.get("items") or []
        if not items:
            return {
                "title": "",
                "author": "",
                "views": None,
                "source": "youtube_data_api",
                "error": "private_or_missing",
            }
        item = items[0]
        sn = item.get("snippet") or {}
        st = item.get("statistics") or {}
        views_raw = st.get("viewCount")
        return {
            "title": str(sn.get("title") or "").strip(),
            "author": str(sn.get("channelTitle") or "").strip(),
            "views": int(views_raw) if views_raw is not None else None,
            "source": "youtube_data_api",
            "error": "",
        }
    except Exception as e:
        err_l = str(e).lower()
        code = "quota_exceeded" if "quota" in err_l else "ytdata_failed"
        print(f"[channel_data] YouTube Data API meta failed for {video_id}: {e}")
        # HttpError 403 quota — surface code for MCP
        try:
            from googleapiclient.errors import HttpError
            if isinstance(e, HttpError) and getattr(e, "resp", None) is not None:
                if int(getattr(e.resp, "status", 0) or 0) == 403 and "quota" in err_l:
                    code = "quota_exceeded"
        except Exception:
            pass
        return {"title": "", "author": "", "views": None, "source": "youtube_data_api", "error": code}


def _fetch_video_meta_ytdlp(video_id: str) -> dict[str, Any] | None:
    """Fallback metadata via yt-dlp (no download)."""
    try:
        import yt_dlp
    except ImportError:
        return None
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as e:
        err_l = str(e).lower()
        code = "private_video" if any(x in err_l for x in ("private", "login", "unavailable")) else "ytdlp_failed"
        print(f"[channel_data] yt-dlp meta failed for {video_id}: {e}")
        return {"title": "", "author": "", "views": None, "source": "ytdlp", "error": code}

    views = info.get("view_count")
    try:
        views_i = int(views) if views is not None else None
    except (TypeError, ValueError):
        views_i = None
    return {
        "title": str(info.get("title") or "").strip(),
        "author": str(info.get("channel") or info.get("uploader") or "").strip(),
        "views": views_i,
        "source": "ytdlp",
        "error": "",
        "duration": float(info.get("duration") or 0) or 0.0,
    }


def fetch_video_meta(video_id: str, yt_api_key: str = "") -> dict[str, Any]:
    """
    Title / author / views for one video.

    Primary: YouTube Data API. Fallback: yt-dlp. Cached ~1h in-process.
    """
    vid = (video_id or "").strip()
    now = time.time()
    cached = _meta_cache.get(vid)
    if cached and (now - cached[0]) < _META_TTL_SEC:
        return dict(cached[1])

    def _usable(m: dict[str, Any] | None) -> bool:
        if not m:
            return False
        return bool(m.get("title") or m.get("author") or m.get("views") is not None)

    meta: dict[str, Any] = {
        "title": "",
        "author": "",
        "views": None,
        "source": "",
        "error": "unavailable",
    }
    api = _fetch_video_meta_ytdata(vid, yt_api_key)
    if _usable(api) and not (api or {}).get("error"):
        meta = api  # type: ignore[assignment]
    else:
        fb = _fetch_video_meta_ytdlp(vid)
        if _usable(fb) and not (fb or {}).get("error"):
            meta = fb  # type: ignore[assignment]
        elif _usable(api):
            meta = api  # type: ignore[assignment]
        elif api and api.get("error"):
            meta = api
        elif fb:
            meta = fb

    _meta_cache[vid] = (now, dict(meta))
    return dict(meta)


def _clean_caption_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _json3_to_text(payload: dict) -> str:
    """YouTube json3 captions: newlines drop the leading space of the next word."""
    parts: list[str] = []
    for event in payload.get("events") or []:
        for seg in event.get("segs") or []:
            utf8 = seg.get("utf8")
            if utf8 is None:
                continue
            if utf8 == "\n":
                parts.append(" ")
                continue
            parts.append(utf8)
    return _clean_caption_text("".join(parts))


def _vtt_to_text(body: str) -> str:
    lines: list[str] = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if (
            not line
            or line.startswith("WEBVTT")
            or line.startswith("NOTE")
            or line.startswith("Kind:")
            or line.startswith("Language:")
            or "-->" in line
            or line.isdigit()
        ):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = html.unescape(line).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return _clean_caption_text(" ".join(lines))


def _ytdlp_caption_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignore_no_formats_error": True,
        # Prefer clients that still expose caption tracks without PO tokens.
        "extractor_args": {
            "youtube": {"player_client": ["tv_simply", "android_vr", "mweb", "web"]},
        },
    }
    try:
        import config as _cfg
        cookies = (getattr(_cfg, "YOUTUBE_COOKIES_FILE", "") or "").strip()
    except Exception:
        cookies = (os.environ.get("YOUTUBE_COOKIES_FILE") or "").strip()
    if cookies and Path(cookies).is_file():
        opts["cookiefile"] = cookies
    return opts


def _caption_track_to_text(ext: str, body: str, *, json_payload: Any = None) -> str:
    ext = (ext or "").lower()
    if ext == "json3":
        data = json_payload
        if data is None:
            try:
                data = json.loads(body)
            except Exception:
                return ""
        return _json3_to_text(data if isinstance(data, dict) else {})
    if ext == "vtt" or "-->" in (body or "") or (body or "").startswith("WEBVTT"):
        return _vtt_to_text(body)
    chunks = re.findall(r"<text[^>]*>(.*?)</text>", body or "", flags=re.S)
    if chunks:
        return _clean_caption_text(
            " ".join(re.sub(r"<[^>]+>", "", html.unescape(c)) for c in chunks)
        )
    return _clean_caption_text(body or "")


def _collect_ytdlp_caption_candidates(info: dict[str, Any]) -> list[tuple[int, int, str, str]]:
    """
    Rank caption tracks for download.

    Score: lower is better.
      0 = English auto-generated
      1 = English manual
      2 = any auto-generated
      3 = any other manual
    Then prefer json3/vtt over other formats.
    """
    preferred_exts = ("json3", "vtt", "srv3", "srv1", "ttml")
    out: list[tuple[int, int, str, str]] = []

    def lang_is_en(lang: str) -> bool:
        l = str(lang or "").lower()
        return l == "en" or l.startswith("en-") or l.startswith("a.en") or l == "en_US"

    # Auto first — MCP callers want YouTube auto captions, not only manual subs.
    for bucket_rank, bucket in (
        (0, info.get("automatic_captions") or {}),
        (1, info.get("subtitles") or {}),
    ):
        if not isinstance(bucket, dict):
            continue
        for lang, tracks in bucket.items():
            en = lang_is_en(str(lang))
            if bucket_rank == 0:
                rank = 0 if en else 2
            else:
                rank = 1 if en else 3
            for track in tracks or []:
                if not isinstance(track, dict):
                    continue
                ext = (track.get("ext") or "").lower()
                track_url = (track.get("url") or "").strip()
                if not track_url or ext not in preferred_exts:
                    continue
                ext_rank = preferred_exts.index(ext)
                out.append((rank, ext_rank, ext, track_url))
    out.sort(key=lambda c: (c[0], c[1]))
    return out


def _fetch_transcript_ytdlp(video_id: str) -> str | None:
    """Pull auto/manual captions through yt-dlp's player response.

    Tries auto-generated tracks first (any language), English preferred.
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = _ytdlp_caption_opts()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as e:
        print(f"[channel_data] yt-dlp caption lookup failed for {video_id}: {e}")
        return None

    candidates = _collect_ytdlp_caption_candidates(info)
    if not candidates:
        print(f"[channel_data] yt-dlp: no caption tracks for {video_id}")
        return None

    seen_urls: set[str] = set()
    for _rank, _er, ext, track_url in candidates[:8]:
        if track_url in seen_urls:
            continue
        seen_urls.add(track_url)
        try:
            resp = httpx.get(track_url, timeout=30, follow_redirects=True)
            if resp.status_code != 200 or not (resp.text or "").strip():
                continue
            payload = None
            if ext == "json3":
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
            text = _caption_track_to_text(ext, resp.text, json_payload=payload)
            if text and len(text) > 20:
                return text
        except Exception as e:
            print(f"[channel_data] yt-dlp {ext} fetch failed for {video_id}: {e}")
            continue
    return None


def _official_snippets_to_text(fetched: Any) -> str:
    snippets = getattr(fetched, "snippets", None)
    if snippets is None and isinstance(fetched, list):
        # Older API returned list of dicts / objects
        parts = []
        for s in fetched:
            parts.append(getattr(s, "text", None) or (s.get("text") if isinstance(s, dict) else "") or "")
        return _clean_caption_text(" ".join(parts))
    return _clean_caption_text(" ".join(getattr(s, "text", "") or "" for s in (snippets or [])))


def _fetch_transcript_official(video_id: str) -> str | None:
    """youtube-transcript-api: prefer EN auto, then any auto-generated track."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception as e:
        print(f"[channel_data] youtube-transcript-api import failed: {e}")
        return None

    api = YouTubeTranscriptApi()

    # Fast path: English (covers many videos)
    for langs in (("en",), ("en-US", "en-GB", "en-AU")):
        try:
            fetched = api.fetch(video_id, languages=list(langs))
            text = _official_snippets_to_text(fetched)
            if text and len(text) > 20:
                return text
        except Exception:
            pass

    # List every track — this is what was missing: non-English auto captions
    # were previously ignored, so MCP looked "broken" on many videos.
    try:
        listing = api.list(video_id)
    except Exception as e:
        print(f"[channel_data] youtube-transcript-api list failed for {video_id}: {e}")
        return None

    tracks = list(listing)
    if not tracks:
        return None

    def track_rank(t: Any) -> tuple[int, str]:
        code = str(getattr(t, "language_code", "") or "").lower()
        gen = bool(getattr(t, "is_generated", False))
        en = code == "en" or code.startswith("en-")
        if en and gen:
            return (0, code)
        if en:
            return (1, code)
        if gen:
            return (2, code)
        return (3, code)

    for tr in sorted(tracks, key=track_rank):
        try:
            fetched = None
            code = str(getattr(tr, "language_code", "") or "")
            # If non-English but translatable, pull English translation of auto captions.
            if not code.lower().startswith("en") and getattr(tr, "is_translatable", False):
                try:
                    fetched = tr.translate("en").fetch()
                except Exception:
                    fetched = None
            if fetched is None:
                fetched = tr.fetch()
            text = _official_snippets_to_text(fetched)
            if text and len(text) > 20:
                print(
                    f"[channel_data] youtube-transcript-api ok video={video_id} "
                    f"lang={code} generated={getattr(tr, 'is_generated', False)}"
                )
                return text
        except Exception as e:
            print(f"[channel_data] youtube-transcript-api track failed for {video_id}: {e}")
            continue
    return None


def _pick_downsub_txt_url(subs: list) -> str | None:
    """Prefer plain-text English track URLs from a DownSub payload."""
    if not subs:
        return None

    def lang_rank(track: dict) -> int:
        lang = str(track.get("language") or track.get("lang") or "").lower()
        if lang.startswith("en") or "english" in lang:
            return 0
        return 1

    for track in sorted(subs, key=lang_rank):
        formats = track.get("formats") or []
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
            url = (fmt.get("url") or "").strip()
            kind = str(fmt.get("format") or fmt.get("ext") or "").lower()
            if not url:
                continue
            if kind == "txt" or "/txt/" in url or url.rstrip("/").endswith(".txt"):
                return url
        # Last resort on this track: any downloadable format we can strip later.
        for fmt in reversed(formats):
            if isinstance(fmt, dict) and (fmt.get("url") or "").strip():
                return fmt["url"].strip()
    return None


def _fetch_transcript_asr(
    video_id: str,
    *,
    max_seconds: float = _ASR_MAX_SECONDS,
) -> str | None:
    """
    Download audio via yt-dlp and transcribe with Groq Whisper (prod path).

    Skips videos longer than max_seconds. Returns plain text or None.
    """
    try:
        import config
    except Exception:
        config = None  # type: ignore

    groq_key = ""
    if config is not None:
        groq_key = (getattr(config, "GROQ_API_KEY", "") or "").strip()
    if not groq_key:
        groq_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not groq_key:
        print(f"[channel_data] ASR skipped for {video_id}: GROQ_API_KEY missing")
        return None

    try:
        import yt_dlp
    except ImportError:
        print(f"[channel_data] ASR skipped for {video_id}: yt-dlp not installed")
        return None

    url = f"https://www.youtube.com/watch?v={video_id}"
    work = Path(tempfile.mkdtemp(prefix=f"yt_asr_{video_id}_"))
    outtmpl = str(work / "audio.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "96",
        }],
    }
    try:
        # Probe duration first without downloading full media.
        probe_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        duration = float(info.get("duration") or 0) or 0.0
        if duration > float(max_seconds):
            print(
                f"[channel_data] ASR skipped for {video_id}: "
                f"duration {duration:.0f}s > max {max_seconds:.0f}s"
            )
            return None

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        audio_files = list(work.glob("audio.*"))
        if not audio_files:
            print(f"[channel_data] ASR: no audio file for {video_id}")
            return None
        audio_path = str(audio_files[0])

        from core.segmenter import _transcribe_groq
        words = _transcribe_groq(audio_path)
        if not words:
            return None
        text = _clean_caption_text(" ".join(str(w.get("word") or "") for w in words))
        return text if text and len(text) > 20 else None
    except Exception as e:
        print(f"[channel_data] ASR failed for {video_id}: {e}")
        return None
    finally:
        try:
            for p in work.glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            work.rmdir()
        except OSError:
            pass


def fetch_transcript_detailed(
    video_id: str,
    downsub_key: str = "",
    *,
    allow_asr: bool = False,
    max_asr_seconds: float = _ASR_MAX_SECONDS,
) -> dict[str, Any]:
    """
    Fetch video-level captions with source attribution.

    Order (captions only — auto-generated preferred inside each source):
      DownSub → youtube-transcript-api → yt-dlp → optional ASR.
    Returns {text, source, error}.
    """
    key = (downsub_key or "").strip()
    attempts: list[str] = []

    if key and not _downsub_is_disabled(key):
        attempts.append("downsub")
        text = _fetch_transcript_downsub(video_id, key)
        if text:
            return {"text": text, "source": "downsub", "error": ""}

    # Prefer listing-based official API (gets non-English auto captions) before yt-dlp.
    attempts.append("youtube_api")
    text = _fetch_transcript_official(video_id)
    if text:
        return {"text": text, "source": "youtube_api", "error": ""}

    attempts.append("ytdlp")
    text = _fetch_transcript_ytdlp(video_id)
    if text:
        return {"text": text, "source": "ytdlp", "error": ""}

    if allow_asr:
        attempts.append("asr")
        text = _fetch_transcript_asr(video_id, max_seconds=max_asr_seconds)
        if text:
            return {"text": text, "source": "asr", "error": ""}

    print(f"[channel_data] no transcript for {video_id} after {attempts}")
    return {"text": "", "source": "", "error": "no_transcript"}


def _fetch_transcript(video_id: str, downsub_key: str = "") -> str | None:
    """
    Fetch transcript for a video (captions only — no ASR).

    When a DownSub key is configured, try it first — paid accounts are the
    reliable path and used to be the primary. Free sources follow as backup
    so an unpaid/denied key still does not empty the transcripts array.
    """
    result = fetch_transcript_detailed(video_id, downsub_key, allow_asr=False)
    text = (result.get("text") or "").strip()
    return text or None


def _fetch_transcript_downsub(video_id: str, downsub_key: str) -> str | None:
    """Fetch transcript via DownSub API. Returns plain text or None."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        resp = httpx.post(
            "https://api.downsub.com/download",
            headers={
                "Authorization": f"Bearer {downsub_key}",
                "Content-Type": "application/json",
            },
            json={"url": url},
            timeout=45,
        )

        if resp.status_code in (401, 403):
            body = (resp.text or "")[:200]
            _disable_downsub(downsub_key, f"HTTP {resp.status_code}: {body}")
            return None

        if resp.status_code != 200:
            print(f"[channel_data] DownSub HTTP {resp.status_code} for {video_id}: {(resp.text or '')[:160]}")
            return None

        data = resp.json()
        if (data.get("status") or "").lower() == "error":
            err = data.get("error") or data
            print(f"[channel_data] DownSub error payload for {video_id}: {err}")
            # Only hard-disable on auth failures. Soft "limit" errors must NOT
            # latch — Premium bonus credits often still work after monthly 2k.
            err_l = str(err).lower()
            if any(s in err_l for s in ("unauthorized", "invalid api", "invalid key", "access denied")):
                _disable_downsub(downsub_key, f"payload: {str(err)[:160]}")
            return None

        inner = data.get("data", data)

        if inner.get("state") != "subtitles_found":
            print(f"[channel_data] DownSub: no subs for {video_id} (state={inner.get('state')})")
            return None

        txt_url = _pick_downsub_txt_url(inner.get("subtitles") or [])
        if not txt_url:
            print(f"[channel_data] DownSub: no downloadable formats for {video_id}")
            return None

        text_resp = httpx.get(txt_url, timeout=45, follow_redirects=True)
        if text_resp.status_code == 200 and text_resp.text.strip():
            # TXT is already plain; VTT/SRT fallbacks need a light cleanup.
            body = text_resp.text.strip()
            if "-->" in body or body.startswith("WEBVTT"):
                return _vtt_to_text(body) or None
            if body.lstrip().startswith("1\n") or body.lstrip().startswith("1\r"):
                # crude SRT → text
                lines = []
                for line in body.splitlines():
                    line = line.strip()
                    if not line or line.isdigit() or "-->" in line:
                        continue
                    lines.append(line)
                return _clean_caption_text(" ".join(lines)) or None
            return body

        print(
            f"[channel_data] DownSub download HTTP {text_resp.status_code} "
            f"for {video_id}"
        )

    except Exception as e:
        print(f"[channel_data] DownSub error for {video_id}: {e}")

    return None


def fetch_channel_data(
    channel_url: str,
    yt_api_key: str,
    downsub_key: str = "",
    max_videos: int = 20,
    fetch_transcripts: bool = True,
    progress_callback=None,
) -> dict:
    """
    Fetch complete channel data: video titles, view counts, and transcripts.

    Returns dict compatible with Script Studio's channel data format:
    {
        "metadata": {...},
        "videos": [{"title": str, "views": int, "video_id": str, ...}],
        "transcripts": [{"title": str, "text": str}],
        "transcript_status": {"requested": int, "fetched": int, "warning": str}
    }
    """
    def _log(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[channel_data] {msg}")

    _validate_yt_key(yt_api_key)

    _log("Resolving channel ID...")
    channel_id = _extract_channel_id(channel_url, yt_api_key)
    _log(f"Channel ID: {channel_id}")

    _log("Fetching channel info and uploads playlist...")
    playlist_id, metadata = _get_uploads_playlist(channel_id, yt_api_key)
    _log(f"Channel: {metadata['channel_name']} ({metadata['video_count']} videos)")

    _log(f"Listing latest {max_videos} videos...")
    try:
        videos = _list_videos(playlist_id, yt_api_key, max_videos)
    except ValueError as playlist_err:
        _log(f"Uploads playlist unavailable ({playlist_err}); falling back to search...")
        videos = _list_videos_via_search(channel_id, yt_api_key, max_videos)
        if not videos:
            raise ValueError(
                f"No public videos found for “{metadata.get('channel_name') or channel_id}”. "
                "Pick a channel that has public uploads."
            ) from playlist_err
    _log(f"Found {len(videos)} videos")

    _log("Fetching view counts...")
    video_ids = [v["video_id"] for v in videos]
    stats = _get_video_stats(video_ids, yt_api_key)

    for v in videos:
        s = stats.get(v["video_id"], {})
        v["views"] = s.get("views", 0)
        v["likes"] = s.get("likes", 0)
        v["comments"] = s.get("comments", 0)
        v["duration"] = s.get("duration", "")

    transcripts = []
    transcript_warning = ""
    if fetch_transcripts:
        _log(f"Fetching transcripts for {len(videos)} videos...")
        for i, v in enumerate(videos):
            _log(f"  Transcript {i + 1}/{len(videos)}: {v['title'][:50]}...")
            text = _fetch_transcript(v["video_id"], downsub_key)
            if text:
                transcripts.append({
                    "title": v["title"],
                    "video_id": v["video_id"],
                    "text": text[:5000],
                })
            time.sleep(0.3)
        _log(f"Got {len(transcripts)}/{len(videos)} transcripts")
        if videos and not transcripts:
            reasons = []
            if _downsub_is_disabled(downsub_key or ""):
                reasons.append(f"DownSub unavailable ({_downsub_disabled_reason})")
            elif not (downsub_key or "").strip():
                reasons.append("DOWNSUB_KEY is not configured on this server")
            reasons.append(
                "YouTube caption providers returned nothing for these videos "
                "(auto-captions missing, disabled, or blocked from this host)"
            )
            transcript_warning = "; ".join(reasons)
            _log(f"WARNING: {transcript_warning}")
        elif videos and len(transcripts) < len(videos):
            transcript_warning = (
                f"Fetched {len(transcripts)}/{len(videos)} transcripts — "
                "some videos have no public captions."
            )

    result = {
        "metadata": metadata,
        "videos": videos,
        "transcripts": transcripts,
        "transcript_status": {
            "requested": len(videos) if fetch_transcripts else 0,
            "fetched": len(transcripts),
            "warning": transcript_warning,
        },
    }

    _log("Channel data fetch complete!")
    return result
