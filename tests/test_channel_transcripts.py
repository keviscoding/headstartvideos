"""Transcript fetch for Script Studio channel data."""

from __future__ import annotations

import json

import pytest

from core import channel_data as cd


SAMPLE_JSON3 = {
    "events": [
        {"segs": [{"utf8": "This"}, {"utf8": " is"}, {"utf8": " my"}, {"utf8": "\n"}]},
        {"segs": [{"utf8": "AI"}, {"utf8": " channel"}, {"utf8": "\n"}]},
        {"segs": [{"utf8": "that"}, {"utf8": " blew"}, {"utf8": " up"}]},
    ]
}


@pytest.fixture(autouse=True)
def _reset_downsub_flag():
    cd.reset_downsub_circuit("test setup")
    yield
    cd.reset_downsub_circuit("test teardown")


class TestCaptionParsers:
    def test_json3_inserts_space_across_newlines(self):
        text = cd._json3_to_text(SAMPLE_JSON3)
        assert text == "This is my AI channel that blew up"
        assert "myAI" not in text
        assert "channelthat" not in text

    def test_vtt_strips_headers_and_timestamps(self):
        body = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
Hello world

00:00:02.000 --> 00:00:04.000
Hello world

00:00:04.000 --> 00:00:06.000
from YouTube
"""
        assert cd._vtt_to_text(body) == "Hello world from YouTube"


class TestFetchOrder:
    def test_downsub_wins_when_key_present(self, monkeypatch):
        calls = []

        monkeypatch.setattr(cd, "_fetch_transcript_downsub",
                            lambda vid, key: calls.append("downsub") or "from downsub")
        monkeypatch.setattr(cd, "_fetch_transcript_official",
                            lambda vid: calls.append("official") or "from official")
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp",
                            lambda vid: calls.append("ytdlp") or "from ytdlp")

        assert cd._fetch_transcript("abc", "key") == "from downsub"
        assert calls == ["downsub"]
        detailed = cd.fetch_transcript_detailed("abc", "key", allow_asr=False)
        assert detailed["source"] == "downsub"
        assert detailed["text"] == "from downsub"

    def test_ytdlp_used_when_downsub_and_official_fail(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cd, "_fetch_transcript_downsub",
                            lambda vid, key: calls.append("downsub") or None)
        monkeypatch.setattr(cd, "_fetch_transcript_official",
                            lambda vid: calls.append("official") or None)
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp",
                            lambda vid: calls.append("ytdlp") or "from ytdlp")

        assert cd._fetch_transcript("abc", "key") == "from ytdlp"
        assert calls == ["downsub", "official", "ytdlp"]

    def test_asr_used_when_captions_fail_and_allowed(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cd, "_fetch_transcript_downsub",
                            lambda vid, key: calls.append("downsub") or None)
        monkeypatch.setattr(cd, "_fetch_transcript_official",
                            lambda vid: calls.append("official") or None)
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp",
                            lambda vid: calls.append("ytdlp") or None)
        monkeypatch.setattr(cd, "_fetch_transcript_asr",
                            lambda vid, max_seconds=0: calls.append("asr") or "spoken words from asr audio")

        # Bulk channel path must NOT call ASR
        assert cd._fetch_transcript("abc", "key") is None
        assert "asr" not in calls

        detailed = cd.fetch_transcript_detailed("abc", "key", allow_asr=True)
        assert detailed["source"] == "asr"
        assert "spoken words" in detailed["text"]
        assert calls[-1] == "asr"

    def test_downsub_skipped_once_disabled_for_same_key(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cd, "_fetch_transcript_official", lambda vid: None)
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp", lambda vid: None)
        monkeypatch.setattr(cd, "_fetch_transcript_downsub",
                            lambda vid, key: calls.append("downsub") or "x")
        cd._disable_downsub("key", "HTTP 403: exhausted")

        assert cd._fetch_transcript("abc", "key") is None
        assert calls == []

    def test_new_key_re_enables_downsub(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cd, "_fetch_transcript_official", lambda vid: None)
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp", lambda vid: None)
        monkeypatch.setattr(cd, "_fetch_transcript_downsub",
                            lambda vid, key: calls.append(key) or f"ok:{key}")
        cd._disable_downsub("old-key", "HTTP 403: exhausted")

        assert cd._fetch_transcript("abc", "new-key") == "ok:new-key"
        assert calls == ["new-key"]


class TestParseAndMeta:
    def test_parse_youtube_video_id(self):
        assert cd.parse_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert cd.parse_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10") == "dQw4w9WgXcQ"
        assert cd.parse_youtube_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert cd.parse_youtube_video_id("") is None
        assert cd.parse_youtube_video_id("ab") is None

    def test_fetch_video_meta_uses_api_then_cache(self, monkeypatch):
        cd._meta_cache.clear()
        calls = {"n": 0}

        def fake_api(vid, key):
            calls["n"] += 1
            return {
                "title": "Hello",
                "author": "Channel",
                "views": 42,
                "source": "youtube_data_api",
                "error": "",
            }

        monkeypatch.setattr(cd, "_fetch_video_meta_ytdata", fake_api)
        monkeypatch.setattr(cd, "_fetch_video_meta_ytdlp", lambda vid: (_ for _ in ()).throw(AssertionError("no ytdlp")))

        a = cd.fetch_video_meta("dQw4w9WgXcQ", "yt-key")
        b = cd.fetch_video_meta("dQw4w9WgXcQ", "yt-key")
        assert a["title"] == "Hello"
        assert a["views"] == 42
        assert a["author"] == "Channel"
        assert b["title"] == "Hello"
        assert calls["n"] == 1

    def test_fetch_video_meta_falls_back_to_ytdlp(self, monkeypatch):
        cd._meta_cache.clear()
        monkeypatch.setattr(
            cd, "_fetch_video_meta_ytdata",
            lambda vid, key: {"title": "", "author": "", "views": None, "source": "youtube_data_api", "error": "quota_exceeded"},
        )
        monkeypatch.setattr(
            cd, "_fetch_video_meta_ytdlp",
            lambda vid: {"title": "FB", "author": "Up", "views": 9, "source": "ytdlp", "error": ""},
        )
        m = cd.fetch_video_meta("aaaaaaaaaaa", "yt-key")
        assert m["source"] == "ytdlp"
        assert m["views"] == 9


class TestDownsubCircuitBreaker:
    def test_403_disables_further_calls_for_that_key(self, monkeypatch):
        class FakeResp:
            status_code = 403
            text = '{"status":"error","error":"Access denied or usage limit exceeded"}'

            def json(self):
                return json.loads(self.text)

        monkeypatch.setattr(cd.httpx, "post", lambda *a, **k: FakeResp())

        assert cd._fetch_transcript_downsub("vid", "key") is None
        assert cd._downsub_is_disabled("key")
        assert "403" in (cd._downsub_disabled_reason or "")

        hits = {"n": 0}

        def boom(*a, **k):
            hits["n"] += 1
            return FakeResp()

        monkeypatch.setattr(cd.httpx, "post", boom)
        monkeypatch.setattr(cd, "_fetch_transcript_official", lambda vid: None)
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp", lambda vid: None)
        assert cd._fetch_transcript("vid2", "key") is None
        assert hits["n"] == 0


class TestPickTxtUrl:
    def test_prefers_txt_format_field(self):
        subs = [{
            "language": "English (auto-generated)",
            "formats": [
                {"format": "srt", "url": "https://download.downsub.com/srt/x"},
                {"format": "txt", "url": "https://download.downsub.com/txt/x"},
            ],
        }]
        assert cd._pick_downsub_txt_url(subs).endswith("/txt/x")

    def test_prefers_english_track(self):
        subs = [
            {"language": "Spanish", "formats": [{"format": "txt", "url": "https://x/es"}]},
            {"language": "English", "formats": [{"format": "txt", "url": "https://x/en"}]},
        ]
        assert cd._pick_downsub_txt_url(subs).endswith("/en")


class TestYtdlpPath:
    def test_prefers_json3_and_parses_it(self, monkeypatch):
        class FakeYDL:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                return {
                    "subtitles": {},
                    "automatic_captions": {
                        "en": [
                            {"ext": "vtt", "url": "https://example/vtt"},
                            {"ext": "json3", "url": "https://example/json3"},
                        ]
                    },
                }

        class FakeMod:
            class YoutubeDL:
                def __init__(self, opts): pass
                def __enter__(self): return FakeYDL()
                def __exit__(self, *a): return False

        class FakeGet:
            status_code = 200
            text = json.dumps(SAMPLE_JSON3)
            def json(self): return SAMPLE_JSON3

        monkeypatch.setitem(__import__("sys").modules, "yt_dlp", FakeMod)
        monkeypatch.setattr(cd.httpx, "get", lambda *a, **k: FakeGet())

        text = cd._fetch_transcript_ytdlp("za2VyvLl5T0")
        assert text == "This is my AI channel that blew up"

    def test_falls_back_to_non_english_auto_captions(self, monkeypatch):
        class FakeYDL:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                return {
                    "subtitles": {},
                    "automatic_captions": {
                        "ko": [{"ext": "json3", "url": "https://example/ko.json3"}],
                    },
                }

        class FakeMod:
            class YoutubeDL:
                def __init__(self, opts): pass
                def __enter__(self): return FakeYDL()
                def __exit__(self, *a): return False

        class FakeGet:
            status_code = 200
            text = json.dumps(SAMPLE_JSON3)
            def json(self): return SAMPLE_JSON3

        monkeypatch.setitem(__import__("sys").modules, "yt_dlp", FakeMod)
        monkeypatch.setattr(cd.httpx, "get", lambda *a, **k: FakeGet())
        text = cd._fetch_transcript_ytdlp("9bZkp7q19f0")
        assert "AI channel" in text


class TestOfficialAutoCaptions:
    def test_uses_non_english_generated_when_en_missing(self, monkeypatch):
        class Snip:
            def __init__(self, text): self.text = text

        class Fetched:
            def __init__(self, text):
                self.snippets = [Snip(w) for w in text.split()]

        class Track:
            def __init__(self, code, generated=True, translatable=False):
                self.language_code = code
                self.is_generated = generated
                self.is_translatable = translatable
            def fetch(self):
                return Fetched("korean auto caption words here that are long enough")
            def translate(self, lang):
                raise RuntimeError("no translate")

        class Listing:
            def __iter__(self):
                return iter([Track("ko", generated=True)])

        class FakeAPI:
            def fetch(self, vid, languages=None):
                raise RuntimeError("no en")
            def list(self, vid):
                return Listing()

        class FakeMod:
            YouTubeTranscriptApi = FakeAPI

        monkeypatch.setitem(__import__("sys").modules, "youtube_transcript_api", FakeMod)
        # Re-import path uses the module name at call time
        import sys
        sys.modules["youtube_transcript_api"] = FakeMod
        text = cd._fetch_transcript_official("9bZkp7q19f0")
        assert text and "korean auto caption" in text


class TestDownsubSoftLimit:
    def test_limit_payload_does_not_disable_bonus_credits(self, monkeypatch):
        class FakeResp:
            status_code = 200
            text = '{"status":"error","error":"usage limit exceeded"}'
            def json(self):
                return json.loads(self.text)

        monkeypatch.setattr(cd.httpx, "post", lambda *a, **k: FakeResp())
        assert cd._fetch_transcript_downsub("vid", "key") is None
        assert not cd._downsub_is_disabled("key")
