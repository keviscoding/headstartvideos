"""
Automated channel data collection.

Uses YouTube Data API v3 to list a channel's videos (title, views, publish date)
and multiple caption sources for transcripts. Combines into structured channel
data for use in Script Studio.
"""

from __future__ import annotations
import html
import re
import time
import httpx

# DownSub returns 401/403 when the key is invalid or the account is unpaid.
# Disable only for that exact key so renewing/pasting a new key re-enables it
# without waiting for a process restart.
_downsub_disabled_for_key: str = ""
_downsub_disabled_reason: str | None = None


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


def _fetch_transcript_ytdlp(video_id: str) -> str | None:
    """Pull auto/manual captions through yt-dlp's player response.

    More reliable from cloud IPs than youtube-transcript-api, which YouTube
    often blocks from datacenter ranges.
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as e:
        print(f"[channel_data] yt-dlp caption lookup failed for {video_id}: {e}")
        return None

    buckets = [
        info.get("subtitles") or {},
        info.get("automatic_captions") or {},
    ]
    preferred_langs = ("en", "en-US", "en-GB", "en-AU", "a.en")
    preferred_exts = ("json3", "vtt", "srv3", "srv1", "ttml")

    candidates: list[tuple[str, str]] = []
    for bucket in buckets:
        for lang in preferred_langs:
            for track in bucket.get(lang) or []:
                ext = (track.get("ext") or "").lower()
                track_url = track.get("url") or ""
                if ext in preferred_exts and track_url:
                    candidates.append((ext, track_url))
        if candidates:
            break
    if not candidates:
        # Any English-ish lang we missed, or first available track.
        for bucket in buckets:
            for lang, tracks in bucket.items():
                if not str(lang).lower().startswith("en"):
                    continue
                for track in tracks or []:
                    ext = (track.get("ext") or "").lower()
                    track_url = track.get("url") or ""
                    if ext in preferred_exts and track_url:
                        candidates.append((ext, track_url))
            if candidates:
                break

    # Prefer formats in preferred_exts order.
    candidates.sort(key=lambda c: preferred_exts.index(c[0]) if c[0] in preferred_exts else 99)

    for ext, track_url in candidates[:4]:
        try:
            resp = httpx.get(track_url, timeout=30, follow_redirects=True)
            if resp.status_code != 200 or not (resp.text or "").strip():
                continue
            if ext == "json3":
                text = _json3_to_text(resp.json())
            elif ext == "vtt":
                text = _vtt_to_text(resp.text)
            else:
                # srv1 / srv3 / ttml share <text>…</text> payloads enough for our use.
                chunks = re.findall(r"<text[^>]*>(.*?)</text>", resp.text, flags=re.S)
                text = _clean_caption_text(
                    " ".join(re.sub(r"<[^>]+>", "", html.unescape(c)) for c in chunks)
                )
            if text and len(text) > 20:
                return text
        except Exception as e:
            print(f"[channel_data] yt-dlp {ext} fetch failed for {video_id}: {e}")
            continue
    return None


def _fetch_transcript_official(video_id: str) -> str | None:
    """Free youtube-transcript-api path. Fast when YouTube allows the IP."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id)
        text = _clean_caption_text(" ".join(s.text for s in transcript.snippets))
        return text or None
    except Exception as e:
        print(f"[channel_data] youtube-transcript-api failed for {video_id}: {e}")
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


def _fetch_transcript(video_id: str, downsub_key: str = "") -> str | None:
    """
    Fetch transcript for a video.

    When a DownSub key is configured, try it first — paid accounts are the
    reliable path and used to be the primary. Free sources follow as backup
    so an unpaid/denied key still does not empty the transcripts array.
    """
    key = (downsub_key or "").strip()
    if key and not _downsub_is_disabled(key):
        text = _fetch_transcript_downsub(video_id, key)
        if text:
            return text

    text = _fetch_transcript_official(video_id)
    if text:
        return text

    text = _fetch_transcript_ytdlp(video_id)
    if text:
        return text

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
            # Some account-limit errors arrive as 200 + status:error.
            err_l = str(err).lower()
            if any(s in err_l for s in ("denied", "limit", "unauthorized", "invalid api", "quota")):
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
