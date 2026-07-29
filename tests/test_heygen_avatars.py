"""HeyGen avatar listing — v3 looks, not the broken v2 catalog."""

from __future__ import annotations

import pytest

from core import heygen


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or ""
        self.content = b"x" if payload is not None or text else b""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise heygen.httpx.HTTPStatusError(
                "err", request=None, response=self
            )


class TestListAvatarsV3:
    def test_uses_v3_looks_and_dedupes_by_group(self, monkeypatch):
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, dict(kwargs.get("params") or {})))
            assert "/v3/avatars/looks" in url
            params = kwargs.get("params") or {}
            if params.get("avatar_type") == "studio_avatar":
                return _Resp(200, {
                    "data": [
                        {
                            "id": "Daphne_Grey_Blazer",
                            "name": "Daphne in Grey blazer",
                            "group_id": "daphne",
                            "preview_image_url": "https://x/daphne.jpg",
                            "gender": "female",
                            "default_voice_id": "v1",
                            "avatar_type": "studio_avatar",
                            "status": "completed",
                        },
                        {
                            "id": "Daphne_Grey_Suit",
                            "name": "Daphne in Grey suit",
                            "group_id": "daphne",
                            "preview_image_url": "https://x/daphne2.jpg",
                            "gender": "female",
                            "avatar_type": "studio_avatar",
                            "status": "completed",
                        },
                    ],
                    "has_more": False,
                })
            # Other types empty
            return _Resp(200, {"data": [], "has_more": False})

        monkeypatch.setattr(heygen.httpx, "get", fake_get)
        monkeypatch.setattr(heygen, "HEYGEN_KEY", "test-key-xxxxxxxxxxxx")

        avatars = heygen.list_avatars(api_key="test-key-xxxxxxxxxxxx")
        assert len(avatars) == 1  # one per group
        assert avatars[0]["avatar_id"] == "Daphne_Grey_Blazer"
        assert avatars[0]["avatar_name"] == "Daphne in Grey blazer"
        assert any(c[1].get("avatar_type") == "studio_avatar" for c in calls)

    def test_skips_incomplete_private_looks(self, monkeypatch):
        def fake_get(url, **kwargs):
            params = kwargs.get("params") or {}
            if params.get("avatar_type") == "studio_avatar":
                return _Resp(200, {
                    "data": [
                        {
                            "id": "ready_1",
                            "name": "Ready",
                            "group_id": "g1",
                            "status": "completed",
                            "avatar_type": "studio_avatar",
                        },
                        {
                            "id": "training_1",
                            "name": "Training",
                            "group_id": "g2",
                            "status": "processing",
                            "avatar_type": "studio_avatar",
                        },
                    ],
                    "has_more": False,
                })
            return _Resp(200, {"data": [], "has_more": False})

        monkeypatch.setattr(heygen.httpx, "get", fake_get)
        avatars = heygen.list_avatars(api_key="test-key-xxxxxxxxxxxx")
        assert [a["avatar_id"] for a in avatars] == ["ready_1"]

    def test_v2_timeout_does_not_block_when_v3_works(self, monkeypatch):
        """Regression: v2/avatars hang must not be the primary path."""
        def fake_get(url, **kwargs):
            if "/v2/avatars" in url:
                raise heygen.httpx.TimeoutException("hang")
            if "/v3/avatars/looks" in url:
                return _Resp(200, {
                    "data": [{
                        "id": "look_ok",
                        "name": "OK",
                        "group_id": "g",
                        "status": "completed",
                        "avatar_type": "studio_avatar",
                    }],
                    "has_more": False,
                })
            return _Resp(200, {"data": [], "has_more": False})

        monkeypatch.setattr(heygen.httpx, "get", fake_get)
        avatars = heygen.list_avatars(api_key="test-key-xxxxxxxxxxxx")
        assert avatars[0]["avatar_id"] == "look_ok"
