"""Stock footage from Pixabay on the creator's own free key (khay, 2026-11-03: Pexels paused new API keys). The
answers below are Pixabay's own documented examples (pixabay.com/api/docs, read 2026-11-03)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_pixabay as px  # noqa: E402

VIDEO_HIT = {"id": 125, "pageURL": "https://pixabay.com/videos/id-125/", "type": "film", "duration": 12, "videos": {
    "large": {"url": "https://cdn.pixabay.com/video/2015/08/08/125-135736646_large.mp4", "width": 1920, "height": 1080},
    "medium": {"url": "https://cdn.pixabay.com/video/2015/08/08/125-135736646_medium.mp4", "width": 1280, "height": 720},
    "small": {"url": "https://cdn.pixabay.com/video/2015/08/08/125-135736646_small.mp4", "width": 640, "height": 360},
    "tiny": {"url": "https://cdn.pixabay.com/video/2015/08/08/125-135736646_tiny.mp4", "width": 480, "height": 270}}}
PHOTO_HIT = {"id": 195893, "pageURL": "https://pixabay.com/en/blossom-bloom-flower-195893/",
             "webformatURL": "https://pixabay.com/get/35bbf209e13e39d2_640.jpg",
             "largeImageURL": "https://pixabay.com/get/ed6a99fd0a76647_1280.jpg"}


@pytest.fixture
def pixabay(tmp_path, monkeypatch):
    monkeypatch.setattr(px, "HOME", tmp_path)
    monkeypatch.setattr(px, "CACHE", tmp_path / ".cr-pixabay-cache")
    monkeypatch.setenv(px.KEY_NAME, "creators-own-key")
    asked = []

    def get(url, params, timeout):
        asked.append((url, dict(params)))
        hits = [VIDEO_HIT, {**VIDEO_HIT, "id": 7, "videos": {"large": {"url": "", "width": 0, "height": 0}}}] \
            if "/videos/" in url else [PHOTO_HIT]
        return SimpleNamespace(status_code=200, json=lambda: {"total": 1, "totalHits": 1, "hits": hits})
    return asked, get


def test_a_video_search_comes_back_in_the_shape_the_engine_reads(pixabay):
    asked, get = pixabay
    out = px.search("https://api.pexels.com/videos/search", {"query": "city at night", "per_page": 15,
                                                              "orientation": "landscape", "page": 2}, get=get, now=1)
    (video,) = out["videos"]                                  # the one without a file is left out
    assert video["id"] == "pixabay-125" and video["duration"] == 12
    assert {f["width"] for f in video["video_files"]} == {1920, 1280, 640, 480}
    assert all(f["file_type"] == "video/mp4" and f["link"].startswith("https://cdn.pixabay.com/") for f in video["video_files"])
    url, params = asked[0]
    assert url == px.VIDEOS and params["q"] == "city at night" and params["per_page"] == 15 and params["page"] == 2
    assert params["key"] == "creators-own-key" and params["safesearch"] == "true"


def test_a_photo_search_gives_src_sizes_and_asks_for_wide_photos(pixabay):
    asked, get = pixabay
    out = px.search("https://api.pexels.com/v1/search", {"query": "x" * 150, "per_page": 1, "orientation": "landscape"},
                    get=get, now=1)
    assert out["photos"][0]["src"]["large2x"] == PHOTO_HIT["largeImageURL"] and out["photos"][0]["id"] == "pixabay-195893"
    url, params = asked[0]
    assert url == px.PHOTOS and params["image_type"] == "photo" and params["orientation"] == "horizontal"
    assert len(params["q"]) == 100 and params["per_page"] == 3, "Pixabay's own limits"


def test_searches_are_cached_for_24_hours_as_pixabays_terms_ask(pixabay):
    asked, get = pixabay
    ask = ("https://api.pexels.com/videos/search", {"query": "forest", "per_page": 10})
    px.search(*ask, get=get, now=1000)
    px.search(*ask, get=get, now=1000 + 3600)
    assert len(asked) == 1
    px.search(*ask, get=get, now=1000 + px.CACHE_SECONDS + 1)
    assert len(asked) == 2


def test_a_failed_search_leaves_the_video_without_stock_instead_of_stopping_it(pixabay):
    def refused(url, params, timeout):
        return SimpleNamespace(status_code=429, json=lambda: {})
    assert px.search("https://api.pexels.com/videos/search", {"query": "x"}, get=refused, now=1) == {"videos": []}
    def broken(url, params, timeout):
        raise ConnectionError("offline")
    assert px.search("https://api.pexels.com/v1/search", {"query": "x"}, get=broken, now=1) == {}


def test_pixabay_answers_only_when_there_is_no_pexels_key(pixabay, monkeypatch):
    own = SimpleNamespace(PEXELS_API_KEY="pexels-key", _pexels_get="original")
    assert not px.install(own) and own._pexels_get == "original", "a creator's Pexels key is used as before"
    none = SimpleNamespace(PEXELS_API_KEY="", _pexels_get="original")
    assert px.install(none) and none.PEXELS_API_KEY == px.STAND_IN and none._pexels_get is px.search
    monkeypatch.delenv(px.KEY_NAME)
    neither = SimpleNamespace(PEXELS_API_KEY="", _pexels_get="original")
    assert not px.install(neither) and neither.PEXELS_API_KEY == "", "no key at all: the engine skips stock as before"


def test_the_key_saved_in_setup_is_read_from_studios_env(pixabay, monkeypatch, tmp_path):
    monkeypatch.delenv(px.KEY_NAME)
    assert px.key() == ""
    (tmp_path / ".env").write_text('KIE_API_KEY=k\nPIXABAY_API_KEY="from-setup"\n', encoding="utf-8")
    assert px.key() == "from-setup"
