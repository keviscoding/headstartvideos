"""Light tests for ranking commentary helpers (no live Atlas)."""
from core.ranking_commentary import (
    _is_junk_line,
    _is_repetitive_line,
    _normalize_rank_label,
    _parse_line_and_label,
    _role_prompt,
)


def test_normalize_label():
    assert _normalize_rank_label("roof gap!!") == "ROOF GAP!!"
    assert _normalize_rank_label("roof@gap") == "ROOF GAP"
    assert len(_normalize_rank_label("a b c d e f").split()) <= 4


def test_parse_json_line():
    raw = '{"line":"bro folded","label":"FOLDED"}'
    p = _parse_line_and_label(raw, "fallback", "X")
    assert p["line"] == "bro folded"
    assert p["label"] == "FOLDED"


def test_junk_and_repeat():
    assert _is_junk_line("")
    assert _is_junk_line("```json")
    assert _is_repetitive_line("bro folded hard", ["bro folded hard"], "")
    assert _is_repetitive_line("the roof gap", [], "roof gap")


def test_role_prompt_forbids_title_read():
    p = _role_prompt("react", "ranking funniest youtube moments", 2, 3, [])
    assert "NEVER read it aloud" in p
    assert "ONLY on what you SEE" in p
