"""Admin niche-research MCP: URL parsing, research pack, newest thumbs."""
from __future__ import annotations

from webapp import mcp_server as mcp
from core import channel_data as cd


def test_parse_channel_urls_caps_and_dedupes():
    urls = [
        "https://www.youtube.com/@a",
        "https://www.youtube.com/@a",
        "https://www.youtube.com/@b",
    ] + [f"https://www.youtube.com/@c{i}" for i in range(12)]
    out = mcp._parse_channel_urls(urls)
    assert out[0].endswith("@a")
    assert out[1].endswith("@b")
    assert len(out) == mcp._MAX_RESEARCH_CHANNELS


def test_parse_channel_urls_from_newline_and_json():
    text = "https://youtube.com/@one\nhttps://youtube.com/@two, https://youtube.com/@three"
    assert len(mcp._parse_channel_urls(text)) == 3
    raw = '["https://youtube.com/@x", "https://youtube.com/@y"]'
    assert mcp._parse_channel_urls(raw) == [
        "https://youtube.com/@x",
        "https://youtube.com/@y",
    ]


def test_newest_video_ids_picks_two_most_recent():
    videos = [
        {"video_id": "old", "published_at": "2024-01-01T00:00:00Z"},
        {"video_id": "mid", "published_at": "2025-06-01T00:00:00Z"},
        {"video_id": "new", "published_at": "2026-08-01T00:00:00Z"},
    ]
    assert cd.newest_video_ids(videos, 2) == ["new", "mid"]


def test_fetch_channel_research_one_transcript_and_thumb_ids(monkeypatch):
    monkeypatch.setattr(cd, "_validate_yt_key", lambda _k: None)
    monkeypatch.setattr(cd, "_extract_channel_id", lambda url, key: "UCabc")
    monkeypatch.setattr(
        cd,
        "_get_uploads_playlist",
        lambda cid, key: (
            "UUabc",
            {
                "channel_name": "Demo",
                "channel_id": "UCabc",
                "subscribers": 10,
                "total_views": 100,
                "video_count": 3,
            },
        ),
    )
    monkeypatch.setattr(
        cd,
        "_list_videos",
        lambda pid, key, max_videos: [
            {"video_id": "v1", "title": "Low", "published_at": "2026-08-01T00:00:00Z"},
            {"video_id": "v2", "title": "High", "published_at": "2026-07-01T00:00:00Z"},
            {"video_id": "v3", "title": "Mid", "published_at": "2026-06-01T00:00:00Z"},
        ][:max_videos],
    )
    monkeypatch.setattr(
        cd,
        "_get_video_stats",
        lambda ids, key: {
            "v1": {"views": 10, "likes": 0, "comments": 0, "duration": "PT1M"},
            "v2": {"views": 999, "likes": 0, "comments": 0, "duration": "PT2M"},
            "v3": {"views": 50, "likes": 0, "comments": 0, "duration": "PT3M"},
        },
    )

    calls: list[str] = []

    def fake_tr(vid, key="", *, allow_asr=False):
        calls.append(vid)
        if vid == "v2":
            return {"text": "hello niche", "source": "youtube_api", "error": ""}
        return {"text": "", "source": "", "error": "no_transcript"}

    monkeypatch.setattr(cd, "fetch_transcript_detailed", fake_tr)

    pack = cd.fetch_channel_research(
        "https://youtube.com/@demo",
        "AIzaTESTKEY1234567890",
        "downsub",
        max_videos=30,
    )
    assert pack["channel_name"] == "Demo"
    assert len(pack["videos"]) == 3
    assert pack["videos"][0]["thumbnail_url"].endswith("/hqdefault.jpg")
    assert pack["sample_transcript"]["video_id"] == "v2"
    assert pack["sample_transcript"]["transcript"] == "hello niche"
    # Highest views first — only one successful call needed
    assert calls == ["v2"]
    assert pack["thumbnails_included"] == ["v1", "v2"]


def test_fetch_channel_research_tries_next_when_top_has_no_captions(monkeypatch):
    monkeypatch.setattr(cd, "_validate_yt_key", lambda _k: None)
    monkeypatch.setattr(cd, "_extract_channel_id", lambda url, key: "UCabc")
    monkeypatch.setattr(
        cd,
        "_get_uploads_playlist",
        lambda cid, key: ("UUabc", {"channel_name": "X", "channel_id": "UCabc",
                                    "subscribers": 1, "total_views": 1, "video_count": 2}),
    )
    monkeypatch.setattr(
        cd,
        "_list_videos",
        lambda pid, key, max_videos: [
            {"video_id": "hi", "title": "Hi", "published_at": "2026-01-02T00:00:00Z"},
            {"video_id": "lo", "title": "Lo", "published_at": "2026-01-01T00:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        cd,
        "_get_video_stats",
        lambda ids, key: {
            "hi": {"views": 1000, "likes": 0, "comments": 0, "duration": ""},
            "lo": {"views": 10, "likes": 0, "comments": 0, "duration": ""},
        },
    )
    calls: list[str] = []

    def fake_tr(vid, key="", *, allow_asr=False):
        calls.append(vid)
        if vid == "lo":
            return {"text": "fallback captions", "source": "ytdlp", "error": ""}
        return {"text": "", "source": "", "error": "no_transcript"}

    monkeypatch.setattr(cd, "fetch_transcript_detailed", fake_tr)
    pack = cd.fetch_channel_research("https://youtube.com/@x", "AIzaTESTKEY1234567890")
    assert calls == ["hi", "lo"]
    assert pack["sample_transcript"]["video_id"] == "lo"


def test_research_niche_channels_admin_returns_images(monkeypatch):
    monkeypatch.setattr(mcp, "_user_from_request", lambda ctx=None: {
        "id": 1, "email": "nwalikelv@gmail.com", "plan": "pro",
    })
    monkeypatch.setattr(mcp, "_mcp_is_admin", lambda user: True)

    import config
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "AIzaTESTKEY1234567890", raising=False)
    monkeypatch.setattr(config, "DOWNSUB_KEY", "", raising=False)

    def fake_research(url, yt, downsub="", **kw):
        return {
            "status": "ok",
            "channel_url": url,
            "channel_name": "Pack",
            "channel_id": "UCpack",
            "subscribers": 1,
            "total_views": 2,
            "video_count": 2,
            "videos": [
                {
                    "video_id": "new1",
                    "title": "Newest",
                    "published_at": "2026-08-02T00:00:00Z",
                    "views": 5,
                    "url": "https://www.youtube.com/watch?v=new1",
                    "thumbnail_url": "https://i.ytimg.com/vi/new1/hqdefault.jpg",
                },
                {
                    "video_id": "new2",
                    "title": "Second",
                    "published_at": "2026-08-01T00:00:00Z",
                    "views": 9,
                    "url": "https://www.youtube.com/watch?v=new2",
                    "thumbnail_url": "https://i.ytimg.com/vi/new2/hqdefault.jpg",
                },
            ],
            "sample_transcript": {
                "video_id": "new2",
                "title": "Second",
                "views": 9,
                "transcript": "sample",
                "transcript_source": "youtube_api",
                "truncated": False,
                "char_count": 6,
            },
            "transcript_note": "",
            "thumbnails_included": ["new1", "new2"],
        }

    monkeypatch.setattr(cd, "fetch_channel_research", fake_research)
    monkeypatch.setattr(
        cd,
        "download_youtube_thumbnail",
        lambda vid: b"\xff\xd8\xff" + (vid.encode() * 40),
    )
    # Tool imports download from core.channel_data at call time — patch module used inside tool
    import core.channel_data as core_cd
    monkeypatch.setattr(core_cd, "fetch_channel_research", fake_research)
    monkeypatch.setattr(
        core_cd,
        "download_youtube_thumbnail",
        lambda vid: b"\xff\xd8\xff" + (vid.encode() * 40),
    )

    parts = mcp.research_niche_channels(
        "https://youtube.com/@pack",
        max_videos=30,
        ctx=None,
    )
    assert isinstance(parts, list)
    assert isinstance(parts[0], str)
    import json
    payload = json.loads(parts[0])
    assert payload["ok_count"] == 1
    assert payload["channels"][0]["thumbnails_included"] == ["new1", "new2"]
    # JSON + (label + Image) * 2
    assert len(parts) == 1 + 4
    from mcp.server.mcpserver.utilities.types import Image
    assert isinstance(parts[2], Image)
    assert isinstance(parts[4], Image)
