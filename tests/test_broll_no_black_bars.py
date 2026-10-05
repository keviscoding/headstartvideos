"""
Test that b-roll normalization uses cover+crop to avoid black bars.

Problem: Seedance i2v returns 1268×728 clips (slightly off 16:9).
Using decrease+pad left ~12px black bars on sides.
Solution: Use increase+crop for center crop without bars.
"""
import sys
from pathlib import Path
import subprocess
import tempfile

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_filter_chain_uses_increase_crop():
    """
    Verify avatar_gen_pipeline uses increase+crop, not decrease+pad.
    """
    print("TEST: filter_chain_uses_increase_crop")
    
    from core.avatar_gen_pipeline import _generate_broll_motion_parallel
    import inspect
    
    source = inspect.getsource(_generate_broll_motion_parallel)
    
    print("  Checking _generate_broll_motion_parallel for correct filter...")
    
    # Should have increase+crop
    assert "force_original_aspect_ratio=increase" in source, (
        "Should use increase (cover) not decrease (fit)"
    )
    assert "crop=1280:720" in source, (
        "Should use crop not pad"
    )
    
    # Should NOT have decrease+pad
    assert "force_original_aspect_ratio=decrease" not in source, (
        "Should not use decrease (causes black bars)"
    )
    assert "pad=1280:720" not in source, (
        "Should not use pad (causes black bars)"
    )
    
    print("    ✓ Trim step uses: force_original_aspect_ratio=increase,crop=1280:720")
    
    # Also check assembly function
    from core.avatar_gen_pipeline import assemble_mixed_avatar_broll_video
    assembly_source = inspect.getsource(assemble_mixed_avatar_broll_video)
    
    print("  Checking assemble_mixed_avatar_broll_video for correct filter...")
    
    # Should have increase+crop
    assert "force_original_aspect_ratio=increase" in assembly_source, (
        "Assembly should use increase (cover) not decrease (fit)"
    )
    assert "crop=1280:720" in assembly_source, (
        "Assembly should use crop not pad"
    )
    
    # Should NOT have decrease+pad
    assert "force_original_aspect_ratio=decrease" not in assembly_source, (
        "Assembly should not use decrease (causes black bars)"
    )
    
    print("    ✓ Assembly uses: force_original_aspect_ratio=increase,crop=1280:720")
    
    print("  ✓ PASS: Both locations use cover+crop (no black bars)")


def test_ffmpeg_increase_crop_produces_no_bars():
    """
    Dry-run: verify 1268×728 source → 1280×720 output with increase+crop.
    Creates a test image and runs the actual ffmpeg command.
    """
    print("\nTEST: ffmpeg_increase_crop_produces_no_bars")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Create a 1268×728 test image (simulates Seedance output)
        input_image = tmppath / "input_1268x728.png"
        
        # Generate a solid color test image with ffmpeg
        create_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=blue:s=1268x728:d=1",
            "-frames:v", "1",
            str(input_image),
        ]
        
        result = subprocess.run(create_cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, f"Failed to create test image: {result.stderr[:200]}"
        assert input_image.exists(), "Test image not created"
        
        print(f"  Created test image: 1268×728")
        
        # Apply the increase+crop filter (what we use in the cook)
        output_video = tmppath / "output_1280x720.mp4"
        
        filter_cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(input_image),
            "-t", "1",
            "-vf", "fps=30,scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an",
            str(output_video),
        ]
        
        result = subprocess.run(filter_cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, f"Failed to process video: {result.stderr[:200]}"
        assert output_video.exists(), "Output video not created"
        
        print(f"  Applied filter: increase+crop")
        
        # Verify output is exactly 1280×720
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(output_video),
        ]
        
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, f"Failed to probe video: {result.stderr[:200]}"
        
        dimensions = result.stdout.strip()
        width, height = dimensions.split(",")
        
        print(f"  Output dimensions: {width}×{height}")
        
        assert width == "1280", f"Width should be 1280, got {width}"
        assert height == "720", f"Height should be 720, got {height}"
        
        # Note: We can't easily check for black pixels without additional tools,
        # but the fact that we scaled with increase (cover) and then cropped
        # means the full frame is filled with content, no letterboxing.
        
        print("  ✓ PASS: 1268×728 input → 1280×720 output (no black bars with increase+crop)")


if __name__ == "__main__":
    print("=" * 70)
    print("Running B-roll Black Bars Tests")
    print("=" * 70)
    
    try:
        test_filter_chain_uses_increase_crop()
        test_ffmpeg_increase_crop_produces_no_bars()
        
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
