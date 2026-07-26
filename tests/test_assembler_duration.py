"""Tests for slideshow visuals covering the full narration.

Both slideshow mux paths pass `-shortest`, so when the images total less than
the voiceover ffmpeg drops the tail of the *audio* — the narration stops
mid-sentence. _stretch_last_to_cover_audio holds the final still instead.

Run from videofactory/:
  python -m pytest tests/test_assembler_duration.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.assembler as assembler
from core.assembler import _stretch_last_to_cover_audio


@pytest.fixture()
def audio_dur(monkeypatch):
    """Stub ffprobe so these stay pure unit tests."""
    def _set(seconds: float):
        monkeypatch.setattr(assembler, "_probe_duration_sec", lambda _p: seconds)
    return _set


def test_short_visuals_are_extended_to_audio(audio_dur):
    audio_dur(100.0)
    out = _stretch_last_to_cover_audio([10.0] * 5, "vo.wav")

    assert sum(out) >= 100.0
    assert out[:4] == [10.0] * 4, "only the final still should change"


def test_matching_durations_are_untouched(audio_dur):
    audio_dur(50.0)
    durations = [10.0] * 5

    assert _stretch_last_to_cover_audio(durations, "vo.wav") == durations


def test_visuals_longer_than_audio_are_untouched(audio_dur):
    audio_dur(20.0)
    durations = [10.0] * 5

    assert _stretch_last_to_cover_audio(durations, "vo.wav") == durations


def test_sub_half_second_deficit_is_ignored(audio_dur):
    """Not worth touching; avoids churn from rounding noise."""
    audio_dur(50.3)
    durations = [10.0] * 5

    assert _stretch_last_to_cover_audio(durations, "vo.wav") == durations


def test_unprobeable_audio_is_a_noop(audio_dur):
    audio_dur(0.0)
    durations = [10.0] * 5

    assert _stretch_last_to_cover_audio(durations, "vo.wav") == durations


def test_input_list_is_not_mutated(audio_dur):
    audio_dur(100.0)
    durations = [10.0] * 5
    _stretch_last_to_cover_audio(durations, "vo.wav")

    assert durations == [10.0] * 5


def test_twenty_minute_narration_with_five_minute_visuals(audio_dur):
    """The reported shape: 20 min of audio, visuals covering only the open."""
    audio_dur(1200.0)
    out = _stretch_last_to_cover_audio([4.0] * 75, "vo.wav")

    assert sum(out) >= 1200.0
    assert len(out) == 75, "must not invent extra images (no COGS change)"
