"""Light tests for ranking commentary helpers (no live Atlas)."""
from core.ranking_commentary import (
    _is_junk_label,
    _is_junk_line,
    _is_repetitive_line,
    _normalize_rank_label,
    _parse_line_and_label,
    _role_prompt,
    _strip_meta_prefixes,
)


def test_normalize_label():
    assert _normalize_rank_label("roof gap!!") == "ROOF GAP!!"
    assert _normalize_rank_label("roof@gap") == "ROOF GAP"
    assert len(_normalize_rank_label("a b c d e f").split()) <= 4
    assert _normalize_rank_label("OUTPUT FORMAT") == "MOMENT"


def test_parse_json_line():
    raw = '{"line":"bro folded","label":"FOLDED"}'
    p = _parse_line_and_label(raw, "fallback", "X")
    assert p["line"] == "bro folded"
    assert p["label"] == "FOLDED"


def test_strips_prompt_bleed():
    raw = 'Output format: {"line":"she didn\'t expect that","label":"TAPE PRANK"}'
    p = _parse_line_and_label(raw, "fallback", "X")
    assert "expect" in p["line"].lower()
    assert "output" not in p["line"].lower()
    assert _is_junk_line("specific constraint: be short")
    assert _is_junk_label("OUTPUT FORMAT")
    cleaned = _strip_meta_prefixes("Output format:\nbro folded")
    assert "output format" not in cleaned.lower()
    assert "bro folded" in cleaned.lower()


def test_junk_and_repeat():
    assert _is_junk_line("")
    assert _is_junk_line("```json")
    assert _is_repetitive_line("bro folded hard", ["bro folded hard"], "")
    assert _is_repetitive_line("the roof gap", [], "roof gap")


def test_role_prompt_is_vision_first_and_keyos_style():
    p = _role_prompt("react", "ranking funniest youtube moments", 2, 3, [])
    assert "Watch the action carefully" in p or "visible" in p.lower()
    assert "She didn't expect that" in p
    assert "ONLY this JSON" in p or "only this JSON" in p.lower()
    assert "formats, constraints" in p.lower() or "Never mention JSON" in p
