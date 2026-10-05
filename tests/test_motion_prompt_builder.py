"""
Test motion prompt builder for Atlas i2v b-roll generation.

The builder creates deterministic motion prompts from segment text + camera move,
without LLM calls.
"""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.avatar_gen_pipeline import build_motion_prompt, select_shot_framing


def test_motion_prompt_extracts_action():
    """Motion prompt should extract key action words from segment text."""
    print("TEST: motion_prompt_extracts_action")
    
    test_cases = [
        {
            "text": "A library card allows you to borrow books for free",
            "camera": "gentle camera drift closer",
            "expected_keywords": ["library", "card", "borrow", "books"],
        },
        {
            "text": "Simply scan your card at the checkout desk",
            "camera": "handheld camera following action",
            "expected_keywords": ["scan", "card", "checkout", "desk"],
        },
        {
            "text": "Return books on time to avoid late fees",
            "camera": "camera slowly pushes forward",
            "expected_keywords": ["return", "books", "time"],
        },
    ]
    
    for tc in test_cases:
        prompt = build_motion_prompt(tc["text"], tc["camera"])
        
        print(f"  Text: '{tc['text'][:50]}...'")
        print(f"  Motion prompt: '{prompt}'")
        
        # Should include camera movement
        assert tc["camera"] in prompt, f"Camera move '{tc['camera']}' not in prompt"
        
        # Should include realistic
        assert "realistic" in prompt, "Missing 'realistic' keyword"
        
        # Should include at least 2 of the expected keywords
        found_keywords = [kw for kw in tc["expected_keywords"] if kw in prompt.lower()]
        assert len(found_keywords) >= 2, (
            f"Expected at least 2 keywords from {tc['expected_keywords']}, "
            f"found {found_keywords} in '{prompt}'"
        )
    
    print("  ✓ PASS: Motion prompts extract action and include camera moves")


def test_motion_prompt_length_capped():
    """Motion prompt should be capped at reasonable length."""
    print("\nTEST: motion_prompt_length_capped")
    
    # Very long text
    long_text = "This is a very long segment with many words " * 20
    camera = "gentle camera drift"
    
    prompt = build_motion_prompt(long_text, camera)
    
    print(f"  Input length: {len(long_text)} chars")
    print(f"  Prompt length: {len(prompt)} chars")
    print(f"  Prompt: '{prompt[:100]}...'")
    
    # Should be capped at 200 chars
    assert len(prompt) <= 200, f"Prompt too long: {len(prompt)} chars"
    
    print("  ✓ PASS: Long prompts are capped")


def test_framing_includes_camera_move():
    """Each framing should have a camera_move field for i2v."""
    print("\nTEST: framing_includes_camera_move")
    
    # Get all 5 framings
    for broll_idx in range(5):
        text = f"Test segment {broll_idx}"
        prev = None if broll_idx == 0 else f"prev_{broll_idx-1}"
        framing = select_shot_framing(broll_idx, text, prev, 5)
        
        assert "camera_move" in framing, f"Framing {broll_idx} missing camera_move"
        assert isinstance(framing["camera_move"], str), "camera_move not a string"
        assert len(framing["camera_move"]) > 0, "camera_move is empty"
        
        print(f"  {framing['framing']:15s}: '{framing['camera_move']}'")
    
    print("  ✓ PASS: All 5 framings have camera moves")


def test_library_card_example():
    """Test motion prompts for the library card script example."""
    print("\nTEST: library_card_example")
    
    script_segments = [
        "A library card is the small card that lets you borrow books",
        "You show it at the desk, the librarian scans it",
        "Then you can take the books home for a few weeks",
    ]
    
    print("  Library card script motion prompts:")
    for idx, text in enumerate(script_segments):
        framing = select_shot_framing(idx, text, None, len(script_segments))
        motion_prompt = build_motion_prompt(text, framing["camera_move"])
        
        print(f"    {idx+1}. {framing['framing']:12s}: {motion_prompt}")
        
        # Verify structure
        assert len(motion_prompt) > 20, "Prompt too short"
        assert framing["camera_move"] in motion_prompt, "Missing camera move"
        assert "realistic" in motion_prompt, "Missing realistic"
    
    print("  ✓ PASS: Library card example generates valid motion prompts")


def test_deterministic_prompts():
    """Same input should produce same output (no randomness)."""
    print("\nTEST: deterministic_prompts")
    
    text = "Scan your library card at the desk"
    camera = "gentle camera drift"
    
    # Generate same prompt 5 times
    prompts = [build_motion_prompt(text, camera) for _ in range(5)]
    
    # All should be identical
    assert len(set(prompts)) == 1, f"Non-deterministic: {set(prompts)}"
    
    print(f"  Same input → same output: '{prompts[0]}'")
    print("  ✓ PASS: Motion prompts are deterministic")


if __name__ == "__main__":
    print("=" * 70)
    print("Running Motion Prompt Builder Tests")
    print("=" * 70)
    
    try:
        test_motion_prompt_extracts_action()
        test_motion_prompt_length_capped()
        test_framing_includes_camera_move()
        test_library_card_example()
        test_deterministic_prompts()
        
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
