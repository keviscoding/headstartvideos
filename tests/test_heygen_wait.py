"""HeyGen wait/timeout recovery and studio create helpers."""

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
            raise heygen.httpx.HTTPStatusError("err", request=None, response=self)


class TestWaitForCompletion:
    def test_max_wait_is_one_hour(self):
        assert heygen.MAX_WAIT >= 3600

    def test_recovers_completed_after_timeout_window(self, monkeypatch):
        """Final status check after wall clock expires still returns completed."""
        statuses = [
            heygen.AvatarVideo(video_id="vid1", status="processing"),
            heygen.AvatarVideo(video_id="vid1", status="processing"),
            # Final recovery check
            heygen.AvatarVideo(
                video_id="vid1",
                status="completed",
                video_url="https://cdn.example/v.mp4",
                duration=42,
            ),
        ]
        calls = {"n": 0}

        def fake_check(video_id, api_key=None):
            i = min(calls["n"], len(statuses) - 1)
            calls["n"] += 1
            return statuses[i]

        sleeps = []
        clock = {"t": 0.0}

        def fake_time():
            return clock["t"]

        def fake_sleep(sec):
            sleeps.append(sec)
            clock["t"] += sec

        monkeypatch.setattr(heygen, "check_status", fake_check)
        monkeypatch.setattr(heygen.time, "time", fake_time)
        monkeypatch.setattr(heygen.time, "sleep", fake_sleep)

        # Tiny timeout so we exit the loop quickly after one poll + sleep
        result = heygen.wait_for_completion("vid1", poll_interval=1, timeout=2)
        assert result.status == "completed"
        assert result.video_url.endswith("v.mp4")
        assert calls["n"] >= 2

    def test_timeout_message_includes_video_id(self, monkeypatch):
        monkeypatch.setattr(
            heygen,
            "check_status",
            lambda video_id, api_key=None: heygen.AvatarVideo(
                video_id=video_id, status="processing"
            ),
        )
        monkeypatch.setattr(heygen.time, "time", lambda: 100.0)
        # Force immediate timeout: start=100, elapsed already >= timeout
        # by advancing time after first check
        times = [0.0, 0.5, 5.0, 5.0, 5.0]

        def fake_time():
            return times.pop(0) if times else 5.0

        monkeypatch.setattr(heygen.time, "time", fake_time)
        monkeypatch.setattr(heygen.time, "sleep", lambda s: None)

        with pytest.raises(TimeoutError) as ei:
            heygen.wait_for_completion("abc123deadbeef", poll_interval=1, timeout=2)
        msg = str(ei.value)
        assert "abc123deadbeef" in msg
        assert "timed out" in msg.lower()

    def test_adaptive_poll_slows_down(self):
        assert heygen._poll_interval_for_elapsed(60, 10) == 10
        assert heygen._poll_interval_for_elapsed(700, 10) == 20
        assert heygen._poll_interval_for_elapsed(2000, 10) == 30


class TestNormalizeScenes:
    def test_auto_chunk_from_script(self):
        scenes = heygen.normalize_heygen_scenes(
            None,
            script_text="Hello world. " * 10,
            avatar_id="av1",
            voice_id="vo1",
        )
        assert scenes
        assert all(s["type"] == "avatar" for s in scenes)
        assert scenes[0]["avatar_id"] == "av1"

    def test_image_scene_triggers_studio_only(self):
        scenes = [
            {"type": "avatar", "script": "Hi"},
            {"type": "image", "image_url": "https://x/a.png", "script": "Chart"},
        ]
        assert heygen.scenes_need_studio_only(scenes) is True
        assert heygen.scenes_need_studio_only([{"type": "avatar", "script": "x"}]) is False

    def test_create_prefers_v3_studio(self, monkeypatch):
        posts = []

        def fake_post(url, **kwargs):
            posts.append(url)
            if "/v3/videos" in url:
                return _Resp(200, {"data": {"video_id": "v3id"}})
            return _Resp(500, {"error": {"message": "no"}})

        monkeypatch.setattr(heygen.httpx, "post", fake_post)
        monkeypatch.setattr(heygen, "_resolve_key", lambda api_key=None: "k" * 20)

        result = heygen.create_avatar_video(
            "Hello there everyone.",
            avatar_id="av",
            voice_id="vo",
            caption=True,
            background="#112233",
            aspect_ratio="9:16",
            voice_speed=1.1,
            engine="avatar_v",
        )
        assert result.video_id == "v3id"
        assert any("/v3/videos" in u for u in posts)

    def test_create_falls_back_to_v2(self, monkeypatch):
        posts = []

        def fake_post(url, **kwargs):
            posts.append(url)
            if "/v3/videos" in url:
                return _Resp(400, {"error": {"message": "bad studio body"}})
            return _Resp(200, {"data": {"video_id": "v2id"}})

        monkeypatch.setattr(heygen.httpx, "post", fake_post)
        monkeypatch.setattr(heygen, "_resolve_key", lambda api_key=None: "k" * 20)

        result = heygen.create_avatar_video(
            "Short script.",
            avatar_id="av",
            voice_id="vo",
        )
        assert result.video_id == "v2id"
        assert any("/v2/video/generate" in u for u in posts)
