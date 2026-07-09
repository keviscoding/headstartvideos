"""
Automated channel data collection.

Uses YouTube Data API v3 to list a channel's videos (title, views, publish date)
and DownSub API to fetch transcripts. Combines into structured channel data
for use in Script Studio.
"""

from __future__ import annotations
import re
import time
import httpx
from config import GEMINI_KEY


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
    youtube = build("youtube", "v3", developerKey=yt_api_key)

    videos = []
    page_token = None

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


def _fetch_transcript(video_id: str, downsub_key: str = "") -> str | None:
    """
    Fetch transcript for a video.
    Primary: DownSub API (if key provided) — more reliable.
    Fallback: youtube-transcript-api (free, no key).
    """
    if downsub_key:
        text = _fetch_transcript_downsub(video_id, downsub_key)
        if text:
            return text

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id)
        text = " ".join(s.text for s in transcript.snippets)
        if text.strip():
            return text
    except Exception as e:
        print(f"[channel_data] Fallback transcript failed for {video_id}: {e}")

    return None


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
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[channel_data] DownSub HTTP {resp.status_code} for {video_id}")
            return None

        data = resp.json()
        inner = data.get("data", data)

        if inner.get("state") != "subtitles_found":
            print(f"[channel_data] DownSub: no subs for {video_id}")
            return None

        subs = inner.get("subtitles", [])
        if not subs:
            return None

        # Find the .txt download URL (plain text format)
        formats = subs[0].get("formats", [])
        txt_url = None
        for fmt in formats:
            dl_url = fmt.get("url", "")
            if "/txt/" in dl_url:
                txt_url = dl_url
                break
        if not txt_url and formats:
            txt_url = formats[-1].get("url", "")

        if not txt_url:
            return None

        text_resp = httpx.get(txt_url, timeout=30, follow_redirects=True)
        if text_resp.status_code == 200 and text_resp.text.strip():
            return text_resp.text.strip()

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
        "transcripts": [{"title": str, "text": str}]
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
    videos = _list_videos(playlist_id, yt_api_key, max_videos)
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

    result = {
        "metadata": metadata,
        "videos": videos,
        "transcripts": transcripts,
    }

    _log("Channel data fetch complete!")
    return result
