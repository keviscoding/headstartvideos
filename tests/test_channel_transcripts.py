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
    cd._downsub_disabled_reason = None
    yield
    cd._downsub_disabled_reason = None


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
    def test_official_wins_when_available(self, monkeypatch):
        calls = []

        monkeypatch.setattr(cd, "_fetch_transcript_official",
                            lambda vid: calls.append("official") or "from official")
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp",
                            lambda vid: calls.append("ytdlp") or "from ytdlp")
        monkeypatch.setattr(cd, "_fetch_transcript_downsub",
                            lambda vid, key: calls.append("downsub") or "from downsub")
        monkeypatch.setattr(cd, "_downsub_disabled_reason", None)

        assert cd._fetch_transcript("abc", "key") == "from official"
        assert calls == ["official"]

    def test_ytdlp_used_when_official_fails(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cd, "_fetch_transcript_official",
                            lambda vid: calls.append("official") or None)
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp",
                            lambda vid: calls.append("ytdlp") or "from ytdlp")
        monkeypatch.setattr(cd, "_fetch_transcript_downsub",
                            lambda vid, key: calls.append("downsub") or "from downsub")
        monkeypatch.setattr(cd, "_downsub_disabled_reason", None)

        assert cd._fetch_transcript("abc", "key") == "from ytdlp"
        assert calls == ["official", "ytdlp"]

    def test_downsub_skipped_once_disabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cd, "_fetch_transcript_official", lambda vid: None)
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp", lambda vid: None)
        monkeypatch.setattr(cd, "_fetch_transcript_downsub",
                            lambda vid, key: calls.append("downsub") or "x")
        monkeypatch.setattr(cd, "_downsub_disabled_reason", "HTTP 403: exhausted")

        assert cd._fetch_transcript("abc", "key") is None
        assert calls == []


class TestDownsubCircuitBreaker:
    def test_403_disables_further_calls(self, monkeypatch):
        cd._downsub_disabled_reason = None

        class FakeResp:
            status_code = 403
            text = '{"status":"error","error":"Access denied or usage limit exceeded"}'

            def json(self):
                return json.loads(self.text)

        monkeypatch.setattr(cd.httpx, "post", lambda *a, **k: FakeResp())

        assert cd._fetch_transcript_downsub("vid", "key") is None
        assert cd._downsub_disabled_reason and "403" in cd._downsub_disabled_reason

        # Second call via _fetch_transcript must not hit DownSub again.
        hits = {"n": 0}

        def boom(*a, **k):
            hits["n"] += 1
            return FakeResp()

        monkeypatch.setattr(cd.httpx, "post", boom)
        monkeypatch.setattr(cd, "_fetch_transcript_official", lambda vid: None)
        monkeypatch.setattr(cd, "_fetch_transcript_ytdlp", lambda vid: None)
        assert cd._fetch_transcript("vid2", "key") is None
        assert hits["n"] == 0


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
