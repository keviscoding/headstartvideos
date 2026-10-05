"""
Test that b-roll framing selection actually covers all 5 framings.

Symptoms from jobs a6d78406 and 5cf1270c: Only OTS and medium appeared.
No close-up, detail, or wide even though select_shot_framing has all 5.

The fix: round-robin using b-roll index (not overall shot index),
first 3 always include close-up/detail and wide, no consecutive repeats.
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
    First 3 must include close-up or detail, and wide.
    """
    print("TEST: framing_covers_all_five_for_typical_short")
    
    # Simulate 3 b-roll segments (typical for 20s Short)
    segments = [
        "A library card allows you to borrow books for free",
        "Simply scan your card at the checkout desk",
        "Return books on time to avoid late fees",
    ]
    
    framings_used = []
    prev = None
    
    for broll_idx, text in enumerate(segments):
        result = select_shot_framing(broll_idx, text, prev, len(segments))
        framing = result["framing"]
        framings_used.append(framing)
        
        # Should never repeat consecutive
        assert framing != prev, f"B-roll {broll_idx} repeated framing {framing}"
        
        prev = framing
        print(f"  B-roll {broll_idx}: {framing} - {result['directive'][:60]}...")
    
    # With 3 segments, we should get 3 different framings
    assert len(set(framings_used)) == 3, f"Expected 3 unique framings, got {len(set(framings_used))}: {framings_used}"
    
    # First 3 must include close-up or detail
    has_closeup_or_detail = any(f in framings_used for f in ["close_up", "detail"])
    assert has_closeup_or_detail, f"First 3 framings missing close-up/detail: {framings_used}"
    
    # First 3 must include wide
    has_wide = "wide" in framings_used
    assert has_wide, f"First 3 framings missing wide: {framings_used}"
    
    print(f"  ✓ PASS: 3 b-roll shots produced 3 unique framings with close-up/detail and wide: {framings_used}")


def test_framing_covers_all_five_with_four_broll():
    """
    A Short with 4 b-roll shots gets 4 different framings.
    With 5 b-roll shots, all 5 framings appear.
    """
    print("\nTEST: framing_covers_all_five_with_four_broll")
    
    # Test 4 b-roll shots
    all_framings_4 = []
    prev = None
    for broll_idx in range(4):
        text = f"Segment {broll_idx}"
        result = select_shot_framing(broll_idx, text, prev, 4)
        framing = result["framing"]
        all_framings_4.append(framing)
        prev = framing
    
    unique_4 = set(all_framings_4)
    print(f"  4 b-roll shots: {all_framings_4}")
    print(f"  Unique: {len(unique_4)}/4")
    assert len(unique_4) == 4, f"Expected 4 unique framings, got {len(unique_4)}: {unique_4}"
    
    # Test 5 b-roll shots gets all 5
    all_framings_5 = []
    prev = None
    for broll_idx in range(5):
        text = f"Segment {broll_idx}"
        result = select_shot_framing(broll_idx, text, prev, 5)
        framing = result["framing"]
        all_framings_5.append(framing)
        prev = framing
    
    unique_5 = set(all_framings_5)
    available_framings = {"close_up", "detail", "over_shoulder", "wide", "medium"}
    
    print(f"  5 b-roll shots: {all_framings_5}")
    print(f"  Unique: {len(unique_5)}/5")
    
    assert len(unique_5) == 5, f"Expected all 5 framings, got {len(unique_5)}: {unique_5}"
    
    # Check each is in the available set
    for f in unique_5:
        assert f in available_framings, f"Unknown framing: {f}"
    
    print(f"  ✓ PASS: 4 b-roll gets 4 framings, 5 b-roll gets all 5")


def test_framing_never_repeats_consecutive():
    """No two consecutive b-roll shots should use the same framing."""
    print("\nTEST: framing_never_repeats_consecutive")
    
    # Test with same text repeated (should still vary)
    segments = ["Same text repeated"] * 10
    prev = None
    
    for broll_idx, text in enumerate(segments):
        result = select_shot_framing(broll_idx, text, prev, len(segments))
        framing = result["framing"]
        
        if prev is not None:
            assert framing != prev, f"B-roll {broll_idx} repeated framing {framing} after {prev}"
        
        prev = framing
    
    print("  ✓ PASS: 10 consecutive b-roll shots never repeated framing")


def test_framing_directive_present():
    """Each framing should have a directive for prompt generation."""
    print("\nTEST: framing_directive_present")
    
    # With round-robin, first 5 b-roll shots give us all 5 framings
    framings_found = []
    
    for broll_idx in range(5):
        text = f"Test segment {broll_idx}"
        prev = framings_found[-1] if framings_found else None
        result = select_shot_framing(broll_idx, text, prev, 5)
        framing = result["framing"]
        
        # Check directive
        assert "directive" in result, f"Missing directive for {framing}"
        assert isinstance(result["directive"], str), f"directive not a string for {framing}"
        assert len(result["directive"]) > 0, f"Empty directive for {framing}"
        
        framings_found.append(framing)
        print(f"  B-roll {broll_idx} ({framing}): '{result['directive'][:60]}...'")
    
    unique_framings = set(framings_found)
    assert len(unique_framings) == 5, f"Expected 5 unique framings, got {len(unique_framings)}: {unique_framings}"
    print("  ✓ PASS: All 5 framings have directives")


def test_framing_round_robin_for_short_videos():
    """
    Round-robin ensures different framings across b-roll shots.
    """
    print("\nTEST: framing_round_robin_for_short_videos")
    
    # 4 b-roll shots should give us 4 different framings
    segments = [f"Segment {i}" for i in range(4)]
    framings = []
    prev = None
    
    for broll_idx, text in enumerate(segments):
        result = select_shot_framing(broll_idx, text, prev, len(segments))
        framing = result["framing"]
        framings.append(framing)
        prev = framing
    
    unique_count = len(set(framings))
    assert unique_count == 4, f"Expected 4 unique framings for 4 b-roll shots, got {unique_count}: {framings}"
    
    print(f"  ✓ PASS: 4 b-roll shots yielded 4 unique framings: {framings}")


if __name__ == "__main__":
    print("=" * 70)
    print("Running B-roll Framing Selection Tests")
    print("=" * 70)
    
    try:
        test_framing_covers_all_five_for_typical_short()
        test_framing_covers_all_five_with_four_broll()
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
