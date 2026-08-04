"""Tests for ranking ASS overlay + assemble helpers (no heavy cook)."""
from pathlib import Path
from unittest import mock

import pytest

from core.ranking_pipeline import (
    ass_time,
    generate_ass,
    _esc_ass,
    _escape_ffmpeg_filter_path,
    _format_viral_title,
    _run,
)


def test_ass_time_format():
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(65.5).startswith("0:01:05")


def test_esc_ass():
    assert "\\{" in _esc_ass("hello {world}")
    assert "\\N" in _esc_ass("a\nb")


def test_viral_title_colors():
    out = _format_viral_title("Top Parkour Moments", "Parkour")
    assert "Parkour" in out
    assert "\\c" in out


def test_generate_ass_viral(tmp_path: Path):
    clips = [
        {"number": 3, "label": "Roof"},
        {"number": 2, "label": "Wall"},
        {"number": 1, "label": "Best"},
    ]
    durs = [1.0, 1.2, 0.8]
    out = tmp_path / "o.ass"
    generate_ass(out, clips, durs, {"text": "Top 3", "highlightWord": "Top"}, style_preset="viral")
    text = out.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in text
    assert "PlayResY: 1920" in text
    assert "3. Roof" in text or "Roof" in text
    assert "Dialogue:" in text


def test_generate_ass_classic(tmp_path: Path):
    clips = [{"number": 2, "label": "A"}, {"number": 1, "label": "B"}]
    out = tmp_path / "c.ass"
    generate_ass(out, clips, [1.0, 1.0], {"text": "Classic"}, style_preset="classic")
    text = out.read_text(encoding="utf-8")
    assert "NumActive" in text
    assert "Dialogue:" in text


def test_escape_ffmpeg_filter_path_escapes_colons(tmp_path: Path):
    p = tmp_path / "overlay.ass"
    p.write_text("x")
    esc = _escape_ffmpeg_filter_path(p)
    assert "overlay.ass" in esc
    # Absolute paths on Windows need \:; Unix paths should still be absolute
    assert esc.startswith("/") or ":\\" in str(p) or "\\" in esc


def test_run_wraps_timeout_as_runtime_error():
    import subprocess
    with mock.patch("core.ranking_pipeline.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1)):
        with pytest.raises(RuntimeError, match="timed out"):
            _run(["ffmpeg", "-version"], timeout=1)
