#!/usr/bin/env python3
"""
Test that all Ken Burns effects produce visible motion.
Generates a test image and renders every effect, verifying none are frozen.
"""
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import subprocess
import tempfile
from core.ken_burns import EFFECTS, render_clip


def generate_test_image(output_path: Path, width: int = 1536, height: int = 864):
    """Generate a test image with gradients and markers for motion detection."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=blue:s={width}x{height}:d=1",
        "-f", "lavfi",
        "-i", f"color=c=red:s={width//2}x{height}:d=1",
        "-filter_complex",
        "[0:v][1:v]overlay=W/2:0,drawtext=text='LEFT':fontsize=60:fontcolor=white:x=100:y=100,"
        "drawtext=text='RIGHT':fontsize=60:fontcolor=white:x=W-300:y=100,"
        "drawtext=text='TOP':fontsize=60:fontcolor=white:x=W/2-100:y=50,"
        "drawtext=text='BOTTOM':fontsize=60:fontcolor=white:x=W/2-100:y=H-100",
        "-frames:v", "1",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"Test image generation failed: {result.stderr.decode()}")


def check_motion_freezedetect(video_path: Path) -> tuple[bool, str]:
    """
    Check if video has motion using ffmpeg freezedetect.
    Returns (is_moving, reason).
    """
    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", "freezedetect=n=0.001:d=2",
        "-f", "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    stderr = result.stderr or ""
    
    # freezedetect logs when it finds frozen segments
    freeze_lines = [line for line in stderr.split('\n') if 'freezedetect' in line.lower()]
    
    if any('freeze_start' in line for line in freeze_lines):
        return False, f"freezedetect found frozen segments:\n" + "\n".join(freeze_lines[:3])
    
    return True, "Motion detected (no freeze segments)"


def test_all_effects():
    """Test that all effects produce motion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Generate test image
        test_image = tmpdir_path / "test_input.jpg"
        print("Generating test image...")
        generate_test_image(test_image)
        
        results = []
        
        for effect in EFFECTS:
            print(f"\nTesting effect: {effect}")
            output_video = tmpdir_path / f"test_{effect}.mp4"
            
            # Render clip
            success = render_clip(
                str(test_image),
                str(output_video),
                duration_sec=3.9,
                effect=effect,
            )
            
            if not success:
                results.append((effect, False, "render_clip failed"))
                print(f"  ❌ Render failed")
                continue
            
            # Check motion with freezedetect
            has_motion, reason = check_motion_freezedetect(output_video)
            results.append((effect, has_motion, reason))
            
            if has_motion:
                print(f"  ✓ Motion detected")
            else:
                print(f"  ❌ FROZEN: {reason}")
        
        # Summary
        print("\n" + "="*70)
        print("MOTION TEST SUMMARY")
        print("="*70)
        
        failed = []
        for effect, has_motion, reason in results:
            status = "✓ PASS" if has_motion else "❌ FAIL"
            print(f"{status:8} {effect:25} {reason[:50]}")
            if not has_motion:
                failed.append(effect)
        
        print("="*70)
        
        if failed:
            print(f"\n❌ {len(failed)} effect(s) are FROZEN: {', '.join(failed)}")
            return False
        else:
            print(f"\n✓ All {len(EFFECTS)} effects produce visible motion!")
            return True


if __name__ == "__main__":
    success = test_all_effects()
    exit(0 if success else 1)
