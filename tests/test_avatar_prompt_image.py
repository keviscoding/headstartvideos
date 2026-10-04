"""
Test that avatar_gen_pipeline uses the working Atlas image path for prompt-based avatars.

Symptom: generate_broll_image_atlas hardcoded black-forest-labs/flux-schnell, 
returned False on errors without logging, causing silent failures.

Fix: Use core.atlas_llm.generate_image_file which uses the premium model and logs errors.
"""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_avatar_gen_imports_generate_image_file():
    """Avatar pipeline should import and use generate_image_file."""
    print("TEST: avatar_gen_imports_generate_image_file")
    
    # Check that the pipeline file imports generate_image_file in the right place
    pipeline_file = ROOT / "core" / "avatar_gen_pipeline.py"
    content = pipeline_file.read_text()
    
    # Look for the import statement that's used for prompt avatars
    assert "from core.atlas_llm import generate_image_file" in content, \
        "Pipeline should import generate_image_file from atlas_llm"
    
    # Verify it's called with progress parameter
    assert "generate_image_file(prompt, str(avatar_img_path), progress=progress)" in content, \
        "Should call generate_image_file with progress parameter"
    
    # Verify the error message includes the prompt
    assert "Failed to generate avatar image from prompt:" in content, \
        "Error should mention it's from a prompt"
    assert "prompt[:100]" in content, \
        "Error should include the prompt text"
    
    print("  ✓ PASS: Avatar pipeline correctly uses generate_image_file")


def test_generate_image_file_has_progress_callback():
    """Verify generate_image_file accepts progress callback."""
    print("\nTEST: generate_image_file_has_progress_callback")
    
    # Check the function signature in the source file
    atlas_file = ROOT / "core" / "atlas_llm.py"
    content = atlas_file.read_text()
    
    # Look for the function definition
    lines = content.split('\n')
    func_def_lines = []
    in_func_def = False
    
    for line in lines:
        if 'def generate_image_file(' in line:
            in_func_def = True
        if in_func_def:
            func_def_lines.append(line)
            if ') -> bool:' in line or ')->bool:' in line:
                break
    
    func_def = '\n'.join(func_def_lines)
    
    # Verify progress parameter exists
    assert 'progress' in func_def, \
        f"generate_image_file should have progress parameter. Found: {func_def}"
    
    # Verify it's optional with a default value or Callable type hint
    assert 'progress:' in func_def or 'progress =' in func_def, \
        "progress should be a defined parameter"
    
    print("  ✓ PASS: generate_image_file has progress callback parameter")


def test_generate_image_file_error_handling():
    """Verify generate_image_file provides error details."""
    print("\nTEST: generate_image_file_error_handling")
    
    atlas_file = ROOT / "core" / "atlas_llm.py"
    content = atlas_file.read_text()
    
    # Verify error messages are sent to progress callback
    assert 'if progress:' in content, "Should check for progress callback"
    assert 'progress(f"Atlas image generation failed' in content or \
           'progress(f\'Atlas image generation failed' in content or \
           'progress("Atlas image generation failed' in content, \
        "Should send failure messages to progress"
    
    # Verify HTTP errors are logged
    assert 'if resp.status_code >= 400:' in content, "Should check HTTP status"
    
    print("  ✓ PASS: generate_image_file has proper error handling")


def test_broll_image_not_used_for_avatar():
    """Verify generate_broll_image_atlas is not used for avatar generation."""
    print("\nTEST: broll_image_not_used_for_avatar")
    
    pipeline_file = ROOT / "core" / "avatar_gen_pipeline.py"
    content = pipeline_file.read_text()
    
    # Find the section that handles prompt avatars
    lines = content.split('\n')
    in_prompt_section = False
    uses_broll = False
    uses_image_file = False
    
    for i, line in enumerate(lines):
        # Look for the prompt avatar section
        if 'if str(avatar_source).startswith("prompt:")' in line:
            in_prompt_section = True
        
        # Check the next 10 lines after entering the section
        if in_prompt_section and i < len(lines) - 1:
            next_lines = '\n'.join(lines[i:i+10])
            if 'generate_broll_image_atlas' in next_lines:
                uses_broll = True
            if 'generate_image_file' in next_lines:
                uses_image_file = True
            if 'else:' in next_lines or 'elif' in next_lines:
                break
    
    assert not uses_broll, "Should NOT use generate_broll_image_atlas for prompt avatars"
    assert uses_image_file, "Should use generate_image_file for prompt avatars"
    
    print("  ✓ PASS: Avatar generation uses generate_image_file, not generate_broll_image_atlas")


def test_error_message_includes_context():
    """Verify error messages include helpful context."""
    print("\nTEST: error_message_includes_context")
    
    pipeline_file = ROOT / "core" / "avatar_gen_pipeline.py"
    content = pipeline_file.read_text()
    
    # The error should help identify what failed
    assert 'raise RuntimeError(f"Failed to generate avatar image from prompt:' in content, \
        "Error should be specific about avatar image from prompt"
    
    # Should include the prompt (or at least the first 100 chars)
    assert '{prompt[:100]}' in content or '{prompt}' in content, \
        "Error should include the prompt that failed"
    
    print("  ✓ PASS: Error messages include helpful context")


if __name__ == "__main__":
    print("=" * 70)
    print("Running Avatar Prompt Image Generation Tests")
    print("=" * 70)
    
    try:
        test_avatar_gen_imports_generate_image_file()
        test_generate_image_file_has_progress_callback()
        test_generate_image_file_error_handling()
        test_broll_image_not_used_for_avatar()
        test_error_message_includes_context()
        
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
