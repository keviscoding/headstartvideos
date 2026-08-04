"""Unit tests for ranking URL import helpers."""
from pathlib import Path

import pytest

from webapp.ranking_import import (
    clean_import_url,
    detect_platform,
    parse_import_urls,
    pick_best_media_url,
    short_url_label,
    import_ranking_url,
)


def test_detect_platform():
    assert detect_platform("https://www.tiktok.com/@x/video/1") == "tiktok"
    assert detect_platform("https://vm.tiktok.com/ZMabc/") == "tiktok"
    assert detect_platform("https://youtube.com/shorts/abc") == "youtube"
    assert detect_platform("https://youtu.be/abc") == "youtube"
    assert detect_platform("https://www.instagram.com/reel/xyz/") == "instagram"
    assert detect_platform("https://example.com/x") == "other"


def test_clean_import_url_tiktok_strips_query():
    raw = "https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc"
    assert clean_import_url(raw, "tiktok") == "https://www.tiktok.com/@user/video/123"


def test_clean_import_url_youtube():
    assert clean_import_url(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "youtube"
    ) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert clean_import_url("https://youtu.be/dQw4w9WgXcQ", "youtube") == (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert clean_import_url(
        "https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube"
    ) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_parse_import_urls_dedupes():
    raw = """
    check https://tiktok.com/@a/video/1
    and https://tiktok.com/@a/video/1 again
    plus https://youtube.com/shorts/abc,
    """
    urls = parse_import_urls(raw)
    assert len(urls) == 2
    assert urls[0].startswith("https://tiktok.com")
    assert "youtube.com" in urls[1]


def test_short_url_label():
    assert "tiktok.com" in short_url_label("https://www.tiktok.com/@user/video/99")


def test_pick_best_media_url_prefers_nowm():
    item = {
        "videoUrl": "https://cdn.example/with_watermark.mp4",
        "downloadAddrNoWatermark": "https://cdn.example/nowm.mp4",
    }
    url, wm_free = pick_best_media_url(item)
    assert url == "https://cdn.example/nowm.mp4"
    assert wm_free is True


def test_import_ranking_url_invalid():
    with pytest.raises(ValueError):
        import_ranking_url("not-a-url", Path("/tmp/x.mp4"))


def test_import_ranking_url_ytdlp_mocked(tmp_path, monkeypatch):
    out = tmp_path / "clip.mp4"

    def fake_ytdlp(url, out_path, platform):
        out_path.write_bytes(b"x" * 2000)
        return {"ok": True, "platform": platform, "watermark_free": True, "source": "yt-dlp"}

    monkeypatch.setattr("webapp.ranking_import._apify_token", lambda: "")
    monkeypatch.setattr("webapp.ranking_import._download_via_ytdlp", fake_ytdlp)

    meta = import_ranking_url("https://www.youtube.com/watch?v=abc12345678", out)
    assert meta["ok"] is True
    assert meta["source"] == "yt-dlp"
    assert out.exists()
    assert out.stat().st_size >= 2000
