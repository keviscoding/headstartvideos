"""HeyGen API key sanitization and validation."""

from __future__ import annotations

import json

import pytest

from core import heygen


class TestSanitize:
    def test_strips_bearer_and_whitespace(self):
        assert heygen.sanitize_api_key("  Bearer abcdefghijklmnop  ") == "abcdefghijklmnop"

    def test_strips_zero_width_and_quotes(self):
        raw = '"\u200bhg_test_abcdefghijklmnopqrstuvwxyz\u200b"'
        assert heygen.sanitize_api_key(raw) == "hg_test_abcdefghijklmnopqrstuvwxyz"

    def test_strips_x_api_key_prefix(self):
        assert heygen.sanitize_api_key("X-Api-Key: hg_test_abcdefghijklmnop") == "hg_test_abcdefghijklmnop"

    def test_collapses_newlines_from_chat_paste(self):
        assert heygen.sanitize_api_key("sk_live_\nabcdefghijklmnop") == "hg_test_abcdefghijklmnop"


class TestValidate:
    def test_empty_key(self):
        ok, err = heygen.test_api_key("")
        assert ok is False
        assert "Paste" in err

    def test_short_key(self):
        ok, err = heygen.test_api_key("short")
        assert ok is False
        assert "full" in err.lower()

    def test_401_is_clear(self, monkeypatch):
        class FakeResp:
            status_code = 401
            text = '{"error":{"message":"Unauthorized"}}'

        monkeypatch.setattr(heygen.httpx, "get", lambda *a, **k: FakeResp())
        ok, err = heygen.test_api_key("hg_test_abcdefghijklmnop")
        assert ok is False
        assert "invalid" in err.lower() or "revoked" in err.lower()
        assert "API" in err

    def test_200_on_users_me_passes(self, monkeypatch):
        calls = []

        class FakeResp:
            status_code = 200
            text = '{"data":{"username":"x"}}'

        def fake_get(url, **kwargs):
            calls.append(url)
            return FakeResp()

        monkeypatch.setattr(heygen.httpx, "get", fake_get)
        ok, err = heygen.test_api_key("hg_test_abcdefghijklmnop")
        assert ok is True
        assert err == ""
        assert "v3/users/me" in calls[0]

    def test_timeout_then_success_on_fallback(self, monkeypatch):
        import httpx as real_httpx

        class OkResp:
            status_code = 200
            text = "{}"

        n = {"i": 0}

        def fake_get(url, **kwargs):
            n["i"] += 1
            if n["i"] == 1:
                raise real_httpx.TimeoutException("slow")
            return OkResp()

        monkeypatch.setattr(heygen.httpx, "get", fake_get)
        ok, err = heygen.test_api_key("hg_test_abcdefghijklmnop")
        assert ok is True

    def test_429_counts_as_accepted(self, monkeypatch):
        class FakeResp:
            status_code = 429
            text = "rate limit"

        monkeypatch.setattr(heygen.httpx, "get", lambda *a, **k: FakeResp())
        ok, err = heygen.test_api_key("hg_test_abcdefghijklmnop")
        assert ok is True
        assert "rate" in err.lower()
