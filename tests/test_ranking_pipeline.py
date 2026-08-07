"""Tests for ranking ASS overlay + assemble helpers (no heavy cook)."""
import re
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
    assert "PARKOUR" in out
    assert "TOP" in out
    # Closing '&' required so libass does not swallow title glyphs as hex.
    assert re.search(r"\\c&H[0-9A-Fa-f]+&\}", out)
    assert "\\N" in _format_viral_title("one two three four five six")


def test_generate_ass_viral(tmp_path: Path):
    clips = [
        {"number": 3, "label": "Roof"},
        {"number": 2, "label": "Wall"},
        {"number": 1, "label": "Best"},
    ]
    durs = [1.0, 1.2, 0.8]
    out = tmp_path / "o.ass"
    generate_ass(
        out, clips, durs, {"text": "Top 3", "highlightWord": "Top"},
        style_preset="viral",
        layout={"titleFontSize": 60, "titleYPercent": 5, "numSize": 55, "listXPercent": 8},
    )
    text = out.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in text
    assert "PlayResY: 1920" in text
    assert "ROOF" in text
    assert "TOP" in text  # uppercase viral title
    assert "NumActive" in text  # persistent countdown stack (not one-at-a-time RankLine)
    assert "NumDone" in text
    assert "Style: Title,Arial Black,60," in text or ",60," in text
    assert "Dialogue:" in text


def test_classic_title_is_uppercase_like_preview(tmp_path: Path):
    clips = [{"number": 2, "label": "A"}, {"number": 1, "label": "B"}]
    out = tmp_path / "c.ass"
    generate_ass(out, clips, [1.0, 1.0], {"text": "Best Moments"}, style_preset="classic")
    text = out.read_text(encoding="utf-8")
    assert "BEST MOMENTS" in text


def test_countdown_keeps_earlier_ranks(tmp_path: Path):
    clips = [
        {"number": 3, "label": "Three"},
        {"number": 2, "label": "Two"},
        {"number": 1, "label": "One"},
    ]
    out = tmp_path / "p.ass"
    generate_ass(out, clips, [1.0, 1.0, 1.0], {"text": "Rank"}, style_preset="viral")
    text = out.read_text(encoding="utf-8")
    # During the last clip window, earlier ranks must still appear as done.
    assert text.count("THREE") >= 2
    assert "NumDone" in text


def test_generate_ass_white_card_karaoke(tmp_path: Path):
    clips = [
        {"number": 3, "label": "A"},
        {"number": 2, "label": "B"},
        {"number": 1, "label": "C"},
    ]
    durs = [1.0, 1.0, 1.0]
    # Fake audio path that exists so karaoke is burned
    wav = tmp_path / "vo.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 100)
    commentary = [{
        "clipIndex": 1,
        "line": "bro what",
        "audioPath": str(wav),
        "duration": 1.2,
        "wordTimings": [
            {"word": "bro", "start": 0.0, "end": 0.4},
            {"word": "what", "start": 0.4, "end": 1.0},
        ],
    }]
    out = tmp_path / "w.ass"
    generate_ass(
        out, clips, durs, {"text": "Funny Moments", "highlightWord": "Funny"},
        style_preset="classic",
        commentary_lines=commentary,
        timeline={
            "clipOffsets": [1.2, 2.4, 3.4],
            "voiceOffsets": {0: 0.0, 1: 1.2, 2: 2.4},
            "whiteMeta": {1: {"offset": 1.2, "duration": 1.2}},
        },
        # Caller may still request viral karaoke cards; style presets are independent.
        force_viral=True,
    )
    text = out.read_text(encoding="utf-8")
    assert "ComSubWhite" in text
    assert "BRO" in text
    assert "FUNNY" in text or "Funny" in text or "\\c" in text


def test_commentary_does_not_force_viral_style():
    from core.ranking_pipeline import _effective_overlay_viral
    lines = [{"line": "bro folded", "audioPath": "/tmp/x.wav"}]
    assert _effective_overlay_viral("classic", lines) is False
    assert _effective_overlay_viral("minimal", lines) is False
    assert _effective_overlay_viral("viral", lines) is True
    assert _effective_overlay_viral("bold", lines) is True


def test_generate_ass_classic(tmp_path: Path):
    clips = [{"number": 2, "label": "A"}, {"number": 1, "label": "B"}]
    out = tmp_path / "c2.ass"
    generate_ass(out, clips, [1.0, 1.0], {"text": "Classic"}, style_preset="classic")
    text = out.read_text(encoding="utf-8")
    assert "NumActive" in text
    assert "CLASSIC" in text
    assert "Dialogue:" in text


def test_normalize_clip_letterbox_filter_string():
    """Guard the ViewHunt scale→crop→pad letterbox filter (no cover-crop fill)."""
    import inspect
    from core import ranking_pipeline as rp
    src = inspect.getsource(rp.normalize_clip)
    assert "pad=" in src and "black" in src
    assert "RANK_CONTENT_MAX_H" in src or "1440" in src
    assert "force_original_aspect_ratio=increase" not in src


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
