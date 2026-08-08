"""Fish TTS retries + clean error messages for gateway failures."""
from __future__ import annotations

from pathlib import Path

import pytest

from core import fish_clone as fc


class _Resp:
    def __init__(self, status_code: int, text: str = "", content: bytes = b""):
        self.status_code = status_code
        self.text = text
        self.content = content


def test_fish_error_summary_strips_html_gateway():
    html = "<!DOCTYPE html><title>fish.audio | 502: Bad gateway</title>"
    msg = fc._fish_error_summary(502, html)
    assert "502" in msg
    assert "temporarily unavailable" in msg.lower()
    assert "<!DOCTYPE" not in msg
    assert "Bad gateway" not in msg or "unavailable" in msg.lower()


def test_tts_with_clone_retries_502_then_succeeds(monkeypatch, tmp_path: Path):
    calls = {"n": 0}
    wav = b"RIFF" + b"\x00" * 800

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(502, "<!DOCTYPE html><title>fish.audio | 502: Bad gateway</title>")
        return _Resp(200, content=wav)

    monkeypatch.setattr(fc, "_headers", lambda: {"Authorization": "Bearer x"})
    monkeypatch.setattr(fc, "_tts_model", lambda: "s2.1-pro-free")
    monkeypatch.setattr(fc.httpx, "post", fake_post)
    monkeypatch.setattr(fc.time, "sleep", lambda *_a, **_k: None)

    out = tmp_path / "out.wav"
    path = fc.tts_with_clone("Hello there cloned voice chunk.", "model123", str(out))
    assert Path(path).is_file()
    assert calls["n"] == 3


def test_tts_with_clone_gives_clean_error_after_retries(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(fc, "_headers", lambda: {"Authorization": "Bearer x"})
    monkeypatch.setattr(fc, "_tts_model", lambda: "s2.1-pro-free")
    monkeypatch.setattr(
        fc.httpx,
        "post",
        lambda *a, **k: _Resp(502, "<!DOCTYPE html><html>bad gateway</html>"),
    )
    monkeypatch.setattr(fc.time, "sleep", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="temporarily unavailable") as ei:
        fc.tts_with_clone("Hello there.", "model123", str(tmp_path / "x.wav"))
    assert "<!DOCTYPE" not in str(ei.value)
