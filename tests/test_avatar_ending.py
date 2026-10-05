"""
Test that avatar videos end on the avatar, not b-roll, and have proper b-roll count.

Symptoms from jobs a6d78406 and 5cf1270c:
- Video ended on b-roll with ~4s of b-roll at end
- Only 2 b-roll shots in 20s Short (should be 3-4)
- Opening stretched to ~8s instead of respecting pattern's 4s

Fix: Opening respects pattern duration, middle section has 3-4 b-roll shots for ~20s Shorts,
always end on avatar (~3s).
"""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.avatar_gen_pipeline import plan_avatar_video_shots


def test_video_ends_on_avatar():
    """Last shot should be avatar type."""
    print("TEST: video_ends_on_avatar")
    
    script = (
        "A library card allows you to borrow books for free from your local library. "
        "Simply scan your card at the checkout desk when you're ready. "
        "Return books on time to avoid late fees. "
        "Most libraries also offer digital books and audiobooks. "
        "Get your library card today and start exploring!"
    )
    
    # Typical Short duration
    audio_duration = 20.0
    
    # Default pattern (opening avatar, then mix with b-roll)
    avatar_pattern = {
        "opening_avatar_sec": 4.0,
        "face_return_frequency": 10.0,
        "face_shot_duration": 3.0,
        "use_title_cards": False,
    }
    
    shots = plan_avatar_video_shots(script, audio_duration, avatar_pattern)
    
    assert len(shots) > 0, "No shots generated"
    
    last_shot = shots[-1]
    print(f"  Total shots: {len(shots)}")
    print(f"  Last shot type: {last_shot.shot_type}")
    print(f"  Last shot time: {last_shot.start_sec:.1f}s - {last_shot.end_sec:.1f}s")
    
    # Last shot MUST be avatar
    assert last_shot.shot_type == "avatar", (
        f"Last shot is {last_shot.shot_type}, not avatar. "
        f"Video should end on avatar for strong finish."
    )
    
    # Last shot should be at least 2 seconds
    assert last_shot.duration >= 2.0, (
        f"Last avatar shot is only {last_shot.duration:.1f}s, should be at least 2s"
    )
    
    print("  ✓ PASS: Video ends on avatar shot")


def test_first_shot_is_avatar():
    """First shot should always be avatar (opening)."""
    print("\nTEST: first_shot_is_avatar")
    
    script = "This is a test script for avatar generation."
    audio_duration = 15.0
    
    avatar_pattern = {
        "opening_avatar_sec": 4.0,
        "face_return_frequency": 10.0,
        "face_shot_duration": 3.0,
        "use_title_cards": False,
    }
    
    shots = plan_avatar_video_shots(script, audio_duration, avatar_pattern)
    
    assert len(shots) > 0, "No shots generated"
    
    first_shot = shots[0]
    print(f"  First shot type: {first_shot.shot_type}")
    
    assert first_shot.shot_type == "avatar", (
        f"First shot is {first_shot.shot_type}, not avatar"
    )
    
    print("  ✓ PASS: Video starts with avatar shot")


def test_broll_appears_in_middle():
    """B-roll should appear between opening and ending avatar."""
    print("\nTEST: broll_appears_in_middle")
    
    script = (
        "First sentence sets the scene with the avatar speaking. "
        "Second sentence continues with more context and details. "
        "Third sentence adds even more information for the viewer. "
        "Fourth sentence provides additional valuable content. "
        "Final sentence wraps up and concludes the video."
    )
    
    audio_duration = 25.0
    
    avatar_pattern = {
        "opening_avatar_sec": 4.0,
        "face_return_frequency": 10.0,
        "face_shot_duration": 3.0,
        "use_title_cards": False,
    }
    
    shots = plan_avatar_video_shots(script, audio_duration, avatar_pattern)
    
    shot_types = [s.shot_type for s in shots]
    print(f"  Shot sequence: {shot_types}")
    
    # Should have at least one b-roll
    broll_count = sum(1 for s in shots if s.shot_type == "broll_still")
    assert broll_count > 0, "No b-roll shots found"
    
    # First should be avatar
    assert shots[0].shot_type == "avatar", "First shot not avatar"
    
    # Last should be avatar
    assert shots[-1].shot_type == "avatar", "Last shot not avatar"
    
    # At least one b-roll in between
    middle_shots = shots[1:-1]
    middle_broll = sum(1 for s in middle_shots if s.shot_type == "broll_still")
    assert middle_broll > 0, "No b-roll in middle sections"
    
    print(f"  ✓ PASS: Found {broll_count} b-roll shots between avatar bookends")


def test_no_broll_in_last_3_seconds():
    """The last ~3 seconds should be pure avatar, no b-roll."""
    print("\nTEST: no_broll_in_last_3_seconds")
    
    script = "Test script " * 20  # Enough for ~20s
    audio_duration = 20.0
    
    avatar_pattern = {
        "opening_avatar_sec": 4.0,
        "face_return_frequency": 8.0,
        "face_shot_duration": 3.0,
        "use_title_cards": False,
    }
    
    shots = plan_avatar_video_shots(script, audio_duration, avatar_pattern)
    
    # Find any b-roll shots in last 3 seconds
    cutoff = audio_duration - 3.0
    late_broll = [
        s for s in shots 
        if s.shot_type == "broll_still" and s.start_sec >= cutoff
    ]
    
    print(f"  Total shots: {len(shots)}")
    print(f"  Last shot: {shots[-1].shot_type} at {shots[-1].start_sec:.1f}s")
    print(f"  B-roll shots after {cutoff:.1f}s: {len(late_broll)}")
    
    assert len(late_broll) == 0, (
        f"Found {len(late_broll)} b-roll shot(s) in last 3 seconds, should be avatar only"
    )
    
    print("  ✓ PASS: Last 3 seconds are avatar-only")


def test_twenty_second_short_has_three_to_four_broll():
    """A ~20s Short should have 3-4 b-roll shots."""
    print("\nTEST: twenty_second_short_has_three_to_four_broll")
    
    script = (
        "A library card allows you to borrow books for free from your local library. "
        "Simply scan your card at the checkout desk when you're ready. "
        "Return books on time to avoid late fees. "
        "Most libraries also offer digital books and audiobooks."
    )
    
    audio_duration = 19.7  # Match job 5cf1270c
    
    avatar_pattern = {
        "opening_avatar_sec": 4.0,
        "face_return_frequency": 10.0,
        "face_shot_duration": 3.0,
        "use_title_cards": False,
    }
    
    shots = plan_avatar_video_shots(script, audio_duration, avatar_pattern)
    
    broll_shots = [s for s in shots if s.shot_type == "broll_still"]
    broll_count = len(broll_shots)
    
    print(f"  Total shots: {len(shots)}")
    print(f"  B-roll shots: {broll_count}")
    print(f"  Shot sequence:")
    for s in shots:
        print(f"    {s.shot_type:12s} {s.start_sec:5.1f}s - {s.end_sec:5.1f}s ({s.duration:.1f}s)")
    
    assert broll_count >= 3, f"Expected at least 3 b-roll shots, got {broll_count}"
    assert broll_count <= 4, f"Expected at most 4 b-roll shots, got {broll_count}"
    
    print(f"  ✓ PASS: 20s Short has {broll_count} b-roll shots (expected 3-4)")


def test_opening_respects_pattern_duration():
    """Opening avatar shot should match pattern's opening_avatar_sec, not stretch."""
    print("\nTEST: opening_respects_pattern_duration")
    
    script = "Test script " * 20
    audio_duration = 20.0
    
    avatar_pattern = {
        "opening_avatar_sec": 4.0,
        "face_return_frequency": 10.0,
        "face_shot_duration": 3.0,
        "use_title_cards": False,
    }
    
    shots = plan_avatar_video_shots(script, audio_duration, avatar_pattern)
    
    # First shot should be avatar
    first_shot = shots[0]
    assert first_shot.shot_type == "avatar", "First shot not avatar"
    
    # Duration should match pattern (4.0s), not stretch to ~8s
    opening_duration = first_shot.duration
    expected = avatar_pattern["opening_avatar_sec"]
    
    print(f"  Opening shot duration: {opening_duration:.1f}s")
    print(f"  Pattern opening_avatar_sec: {expected:.1f}s")
    
    # Allow small tolerance
    assert abs(opening_duration - expected) < 0.5, (
        f"Opening duration {opening_duration:.1f}s doesn't match pattern {expected:.1f}s"
    )
    
    print(f"  ✓ PASS: Opening avatar respects pattern duration ({expected:.1f}s)")


if __name__ == "__main__":
    print("=" * 70)
    print("Running Avatar Ending Tests")
    print("=" * 70)
    
    try:
        test_video_ends_on_avatar()
        test_first_shot_is_avatar()
        test_broll_appears_in_middle()
        test_no_broll_in_last_3_seconds()
        test_twenty_second_short_has_three_to_four_broll()
        test_opening_respects_pattern_duration()
        
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
