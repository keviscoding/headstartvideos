"""Tests for concept segmentation windowing and gap backfill.

The bug these pin down: a single LLM pass over a long script returned concepts
covering only the opening minute, _build_concepts stretched the last concept to
the end of the audio, and _enforce_duration_constraints split that one concept
into hundreds of chunks sharing one illustration_prompt — so the same picture
rendered for the rest of the video.

Run from videofactory/:
  python -m pytest tests/test_concept_segmenter.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.concept_segmenter import (
    _backfill_uncovered_spans,
    _build_concepts,
    _enforce_duration_constraints,
    _fallback_concept_dicts,
    _format_words_with_timestamps,
    _resegment_overlong,
    _word_windows,
    coverage_ratio,
)


def _words(total_sec: float, wps: float = 2.5) -> list[dict]:
    """Synthetic word timeline: `wps` words per second, no gaps."""
    n = int(total_sec * wps)
    step = 1.0 / wps
    return [
        {"word": f"w{i}", "start": round(i * step, 3), "end": round((i + 1) * step, 3)}
        for i in range(n)
    ]


class TestWordWindows:
    def test_short_script_is_one_window(self):
        assert _word_windows(_words(60)) == [(0, 150)]

    def test_twenty_minute_script_is_split(self):
        words = _words(1200)
        windows = _word_windows(words)
        assert len(windows) > 5, "a 20 min script must not go to the LLM in one pass"

    def test_windows_are_contiguous_and_complete(self):
        words = _words(1200)
        windows = _word_windows(words)
        assert windows[0][0] == 0
        assert windows[-1][1] == len(words)
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
            assert prev_end == next_start, "windows must not skip or overlap words"

    def test_empty_word_list(self):
        assert _word_windows([]) == []


class TestGlobalIndices:
    def test_offset_keeps_indices_global(self):
        words = _words(10)
        block = _format_words_with_timestamps(words[5:8], offset=5)
        assert block.startswith("[5:")
        assert "[7:" in block

    def test_fallback_respects_index_offset(self):
        words = _words(40)
        dicts = _fallback_concept_dicts(words[50:80], index_offset=50)
        assert dicts
        assert dicts[0]["start_word_idx"] >= 50


class TestBackfill:
    def test_uncovered_tail_is_backfilled(self):
        """The exact reported bug: LLM covers the open, rest of audio is empty."""
        words = _words(600)
        truncated = [{
            "start_word_idx": 0,
            "end_word_idx": 40,
            "text": "opening",
            "illustration_prompt": "stick figure on a hill, tree, sun, centered",
        }]

        assert coverage_ratio(truncated, words) < 0.05

        filled = _backfill_uncovered_spans(truncated, words, target_sec=4.0)

        assert coverage_ratio(filled, words) > 0.98
        assert len(filled) > 50

    def test_backfilled_beats_have_distinct_prompts(self):
        words = _words(600)
        truncated = [{
            "start_word_idx": 0, "end_word_idx": 40, "text": "opening",
            "illustration_prompt": "stick figure on a hill",
        }]

        filled = _backfill_uncovered_spans(truncated, words, target_sec=4.0)
        prompts = [d["illustration_prompt"] for d in filled]

        assert len(set(prompts)) > len(prompts) * 0.8, "beats must not share one prompt"

    def test_midscript_gap_is_backfilled(self):
        words = _words(300)
        dicts = [
            {"start_word_idx": 0, "end_word_idx": 100, "text": "a",
             "illustration_prompt": "p1"},
            {"start_word_idx": 600, "end_word_idx": 749, "text": "b",
             "illustration_prompt": "p2"},
        ]

        filled = _backfill_uncovered_spans(dicts, words, target_sec=4.0)

        assert coverage_ratio(filled, words) > 0.98

    def test_full_coverage_is_left_untouched(self):
        words = _words(120)
        dicts = _fallback_concept_dicts(words, target_sec=4.0)

        assert _backfill_uncovered_spans(dicts, words, target_sec=4.0) == dicts

    def test_tiny_gap_is_not_backfilled(self):
        """Sub-5s gaps are fine to absorb into a neighbouring concept."""
        words = _words(120)
        dicts = _fallback_concept_dicts(words, target_sec=4.0)
        dicts = [d for d in dicts if d["start_word_idx"] > 8]  # drop ~3s at the head

        before = len(dicts)
        assert len(_backfill_uncovered_spans(dicts, words, target_sec=4.0)) == before


class TestResegmentOverlong:
    """A concept claiming a huge span is 100% 'covered' but still one image."""

    def test_whole_script_in_one_concept_is_split(self):
        words = _words(600.0)
        dicts = [{
            "start_word_idx": 0, "end_word_idx": len(words) - 1,
            "text": "everything", "illustration_prompt": "one prompt",
        }]
        out = _resegment_overlong(dicts, words, target_sec=4.0)
        assert len(out) > 100
        assert len({d["illustration_prompt"] for d in out}) > 100

    def test_normal_length_concepts_are_left_alone(self):
        words = _words(60.0)
        dicts = [
            {"start_word_idx": 0, "end_word_idx": 10, "text": "a", "illustration_prompt": "p1"},
            {"start_word_idx": 11, "end_word_idx": 22, "text": "b", "illustration_prompt": "p2"},
        ]
        assert _resegment_overlong(dicts, words, target_sec=4.0) == dicts

    def test_resegmented_span_keeps_global_indices(self):
        words = _words(300.0)
        start = 100
        dicts = [{
            "start_word_idx": start, "end_word_idx": len(words) - 1,
            "text": "tail", "illustration_prompt": "p",
        }]
        out = _resegment_overlong(dicts, words, target_sec=4.0)
        assert out[0]["start_word_idx"] == start
        assert out[-1]["end_word_idx"] == len(words) - 1

    def test_malformed_indices_are_passed_through(self):
        words = _words(600.0)
        dicts = [
            {"start_word_idx": None, "end_word_idx": 5, "text": "a", "illustration_prompt": "p"},
            {"start_word_idx": 900, "end_word_idx": 100, "text": "b", "illustration_prompt": "p"},
        ]
        assert _resegment_overlong(dicts, words, target_sec=4.0) == dicts

    def test_empty_words_is_a_noop(self):
        dicts = [{"start_word_idx": 0, "end_word_idx": 9, "text": "a", "illustration_prompt": "p"}]
        assert _resegment_overlong(dicts, [], target_sec=4.0) == dicts

    @pytest.mark.parametrize("plan_name,plan_fn", [
        ("one concept for everything", lambda n: [
            {"start_word_idx": 0, "end_word_idx": n - 1, "text": "a", "illustration_prompt": "p"}]),
        ("indices out of range", lambda n: [
            {"start_word_idx": -50, "end_word_idx": n + 9999, "text": "a", "illustration_prompt": "p"}]),
        ("twelve tiny concepts", lambda n: [
            {"start_word_idx": i * 5, "end_word_idx": i * 5 + 4,
             "text": f"c{i}", "illustration_prompt": f"p{i}"} for i in range(12)]),
    ])
    def test_no_lazy_plan_survives_as_repeated_images(self, plan_name, plan_fn):
        """20-minute narration: no model output may yield a wall of clones."""
        words = _words(1200.0)
        dicts = _resegment_overlong(plan_fn(len(words)), words, target_sec=4.0)
        dicts = _backfill_uncovered_spans(dicts, words, target_sec=4.0)
        concepts = _enforce_duration_constraints(_build_concepts(dicts, words))

        prompts = [c.illustration_prompt for c in concepts]
        worst = max(prompts.count(p) for p in set(prompts))
        assert worst <= 2, f"{plan_name}: one image repeated {worst}x"

        span = words[-1]["end"] - words[0]["start"]
        assert sum(c.duration_sec for c in concepts) == pytest.approx(span, abs=1.0)


class TestRepeatedImageRegression:
    def test_truncated_plan_no_longer_yields_hundreds_of_clones(self):
        """End-to-end: build + duration-enforce a truncated plan, with and without backfill."""
        words = _words(900)  # 15 min
        truncated = [{
            "start_word_idx": 0,
            "end_word_idx": 60,
            "text": "opening",
            "illustration_prompt": "stick figure on a hill, tree, sun, centered",
            "background_mood": "warm_earth",
        }]

        # Old behaviour: one concept stretched over the whole video, then split.
        broken = _enforce_duration_constraints(_build_concepts(truncated, words))
        broken_prompts = {c.illustration_prompt for c in broken}
        assert len(broken) > 100, "sanity: the old path really did explode into chunks"
        assert len(broken_prompts) == 1, "sanity: and they all shared one prompt"

        # Fixed behaviour: gaps get their own beats before building.
        filled = _backfill_uncovered_spans(truncated, words, target_sec=4.0)
        fixed = _enforce_duration_constraints(_build_concepts(filled, words))
        fixed_prompts = {c.illustration_prompt for c in fixed}

        assert len(fixed_prompts) > 100, "each beat must get its own illustration"
        # Image count stays in the same ballpark, so COGS does not regress.
        assert len(fixed) <= len(broken) * 1.35

    def test_coverage_holds_full_audio_duration(self):
        words = _words(900)
        truncated = [{
            "start_word_idx": 0, "end_word_idx": 60, "text": "opening",
            "illustration_prompt": "p",
        }]
        filled = _backfill_uncovered_spans(truncated, words, target_sec=4.0)
        concepts = _enforce_duration_constraints(_build_concepts(filled, words))

        assert concepts[0].start_sec <= words[0]["start"] + 0.1
        assert concepts[-1].end_sec >= words[-1]["end"] - 0.1
        total = sum(c.duration_sec for c in concepts)
        assert abs(total - (words[-1]["end"] - words[0]["start"])) < 1.0


class TestWindowedSegmentationEndToEnd:
    """Drive segment_into_concepts with a stub LLM to check window stitching."""

    @staticmethod
    def _install_stub(monkeypatch, *, fail_from_index=None):
        """`fail_from_index` always fails the window starting at that word index,
        which stays deterministic regardless of thread scheduling."""
        import re as _re

        import core.atlas_llm as atlas

        calls = []

        def fake_generate_text(prompt, **kwargs):
            m = _re.search(r"cover every word from index (\d+) to (\d+)", prompt)
            assert m, "window prompt must state its global index range"
            lo, hi = int(m.group(1)), int(m.group(2))
            calls.append((lo, hi))
            if fail_from_index is not None and lo == fail_from_index:
                raise RuntimeError("simulated provider error")
            out, idx, cid = [], lo, 0
            while idx <= hi:
                end = min(hi, idx + 9)
                out.append({
                    "id": cid,
                    "start_word_idx": idx,
                    "end_word_idx": end,
                    "text": f"beat {cid}",
                    "illustration_prompt": f"stick figure scene {lo}-{cid}, hill, tree, centered",
                    "background_mood": "warm_earth",
                    "has_character": True,
                    "cut_style": "crossfade",
                })
                idx, cid = end + 1, cid + 1
            import json as _json
            return _json.dumps(out)

        monkeypatch.setattr(atlas, "generate_text", fake_generate_text)
        monkeypatch.setattr(atlas, "has_atlas", lambda: True)
        return calls

    def test_ten_minute_script_covers_full_audio(self, monkeypatch):
        from core.concept_segmenter import segment_into_concepts

        calls = self._install_stub(monkeypatch)
        words = _words(600)

        concepts = segment_into_concepts("script text", words, niche_hint="space")

        assert len(calls) > 1, "long script must be windowed"
        assert concepts[0].start_sec <= words[0]["start"] + 0.1
        assert concepts[-1].end_sec >= words[-1]["end"] - 0.1
        assert len({c.illustration_prompt for c in concepts}) > len(concepts) * 0.8

    def test_concepts_come_back_in_time_order(self, monkeypatch):
        """Parallel windows must not scramble the timeline."""
        from core.concept_segmenter import segment_into_concepts

        self._install_stub(monkeypatch)
        concepts = segment_into_concepts("script text", _words(600))

        starts = [c.start_sec for c in concepts]
        assert starts == sorted(starts)
        for prev, nxt in zip(concepts, concepts[1:]):
            assert nxt.start_sec >= prev.start_sec

    def test_one_failing_window_falls_back_without_losing_coverage(self, monkeypatch):
        from core.concept_segmenter import _word_windows, segment_into_concepts

        words = _words(600)
        second_window_start = _word_windows(words)[1][0]
        self._install_stub(monkeypatch, fail_from_index=second_window_start)

        concepts = segment_into_concepts("script text", words, niche_hint="space")

        total = sum(c.duration_sec for c in concepts)
        span = words[-1]["end"] - words[0]["start"]
        assert abs(total - span) < 1.0, "a bad window must not shorten the video"
