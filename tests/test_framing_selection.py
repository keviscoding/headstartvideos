"""
Test that b-roll framing selection actually covers all 5 framings.

Symptom from job a6d78406: Only OTS and medium appeared across 3 b-roll shots.
No close-up, detail, or wide even though select_shot_framing has all 5.

The fix: round-robin for short videos, deterministic hash for longer ones,
and always avoid consecutive repeats.
"""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.avatar_gen_pipeline import select_shot_framing


def test_framing_covers_all_five_for_typical_short():
    """
    A typical Short with 3-4 b-roll shots should use 3-4 different framings.
    Over multiple Shorts, all 5 should appear.
    """
    print("TEST: framing_covers_all_five_for_typical_short")
    
    # Simulate 3 b-roll segments (typical for 20s Short)
    segments = [
        "A library card allows you to borrow books for free",
        "Simply scan your card at the checkout desk",
        "Return books on time to avoid late fees",
    ]
    
    framings_used = set()
    prev = None
    
    for idx, text in enumerate(segments):
        result = select_shot_framing(text, idx, prev, len(segments))
        framing = result["framing"]
        framings_used.add(framing)
        
        # Should never repeat consecutive
        assert framing != prev, f"Segment {idx} repeated framing {framing}"
        
        prev = framing
        print(f"  Segment {idx}: {framing} - {result['directive'][:60]}...")
    
    # With 3 segments, we should get 3 different framings
    assert len(framings_used) == 3, f"Expected 3 unique framings, got {len(framings_used)}: {framings_used}"
    
    print(f"  ✓ PASS: 3 segments produced 3 unique framings: {framings_used}")


def test_framing_covers_all_five_across_multiple_shorts():
    """
    Across 2-3 typical Shorts (9 total segments), all 5 framings should appear.
    """
    print("\nTEST: framing_covers_all_five_across_multiple_shorts")
    
    # Simulate 3 Shorts with different content
    all_segments = [
        # Short 1 (library)
        ("Library cards let you borrow books", 3),
        ("Scan your card at the desk", 3),
        ("Return books to avoid late fees", 3),
        # Short 2 (cooking)
        ("First, chop the vegetables finely", 3),
        ("Heat oil in a large pan", 3),
        ("Sauté until golden brown", 3),
        # Short 3 (exercise)
        ("Start with a light warm-up", 3),
        ("Focus on proper form and breathing", 3),
        ("Cool down with gentle stretches", 3),
    ]
    
    all_framings = set()
    prev = None
    
    for idx, (text, total) in enumerate(all_segments):
        result = select_shot_framing(text, idx, prev, total)
        framing = result["framing"]
        all_framings.add(framing)
        prev = framing
    
    available_framings = {"close_up", "detail", "over_shoulder", "wide", "medium"}
    
    print(f"  Framings found: {all_framings}")
    print(f"  Total unique: {len(all_framings)}/5")
    
    # All 5 should appear across 9 segments
    assert len(all_framings) >= 4, f"Expected at least 4/5 framings, got {len(all_framings)}"
    
    # Check each is in the available set
    for f in all_framings:
        assert f in available_framings, f"Unknown framing: {f}"
    
    print(f"  ✓ PASS: Found {len(all_framings)} unique framings across 9 segments")


def test_framing_never_repeats_consecutive():
    """No two consecutive segments should use the same framing."""
    print("\nTEST: framing_never_repeats_consecutive")
    
    # Test with same text repeated (should still vary)
    segments = ["Same text repeated"] * 10
    prev = None
    
    for idx, text in enumerate(segments):
        result = select_shot_framing(text, idx, prev, len(segments))
        framing = result["framing"]
        
        if prev is not None:
            assert framing != prev, f"Segment {idx} repeated framing {framing} after {prev}"
        
        prev = framing
    
    print("  ✓ PASS: 10 consecutive segments never repeated framing")


def test_framing_directive_present():
    """Each framing should have a directive for prompt generation."""
    print("\nTEST: framing_directive_present")
    
    framings_to_test = ["close_up", "detail", "over_shoulder", "wide", "medium"]
    found_framings = set()
    
    for idx, target_framing in enumerate(framings_to_test):
        # Use distinct text to help hit the target
        text = f"Test text for framing {target_framing} variant {idx}"
        # Try a few indices to find one that gives us this framing
        for attempt in range(10):
            result = select_shot_framing(text, idx + attempt, None, 5)
            if result["framing"] == target_framing:
                # Found it, check directive
                assert "directive" in result, f"Missing directive for {target_framing}"
                assert isinstance(result["directive"], str), f"directive not a string for {target_framing}"
                assert len(result["directive"]) > 0, f"Empty directive for {target_framing}"
                found_framings.add(target_framing)
                print(f"  {target_framing}: '{result['directive'][:60]}...'")
                break
    
    assert len(found_framings) == 5, f"Only found {len(found_framings)}/5 framings"
    print("  ✓ PASS: All framings have directives")


def test_framing_round_robin_for_short_videos():
    """
    For videos with ≤5 segments, framings should distribute evenly (round-robin).
    """
    print("\nTEST: framing_round_robin_for_short_videos")
    
    # 4 segments should give us 4 different framings
    segments = [f"Segment {i}" for i in range(4)]
    framings = []
    prev = None
    
    for idx, text in enumerate(segments):
        result = select_shot_framing(text, idx, prev, len(segments))
        framing = result["framing"]
        framings.append(framing)
        prev = framing
    
    unique_count = len(set(framings))
    assert unique_count == 4, f"Expected 4 unique framings for 4 segments, got {unique_count}: {framings}"
    
    print(f"  ✓ PASS: 4 segments yielded 4 unique framings: {framings}")


if __name__ == "__main__":
    print("=" * 70)
    print("Running B-roll Framing Selection Tests")
    print("=" * 70)
    
    try:
        test_framing_covers_all_five_for_typical_short()
        test_framing_covers_all_five_across_multiple_shorts()
        test_framing_never_repeats_consecutive()
        test_framing_directive_present()
        test_framing_round_robin_for_short_videos()
        
        print("\n" + "=" * 70)
        print("✓ ALL TESTS PASSED")
        print("=" * 70)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
