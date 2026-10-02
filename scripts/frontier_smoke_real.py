"""
Frontier smoke test with REAL Atlas + Pexels integration.

Run this after loading runtime_keys.txt environment variables.
"""

import os
import sys
from pathlib import Path

# Add workspace to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.frontier_pipeline import run_frontier_pipeline
from core.voiceover_gen import generate_voiceover


def main():
    print("=== Frontier Smoke Test with Real Atlas + Pexels ===\n")
    
    # Verify keys
    if not os.environ.get("ATLASCLOUD_KEY") and not os.environ.get("ATLASCLOUD_API_KEY"):
        print("❌ ATLASCLOUD_KEY not set")
        sys.exit(1)
    
    print(f"✓ ATLASCLOUD_KEY: {os.environ.get('ATLASCLOUD_KEY', 'N/A')[:20]}...")
    print(f"✓ PEXELS_KEY: {os.environ.get('PEXELS_KEY', 'N/A')[:20]}...")
    print(f"✓ ATLAS_IMAGE_MODEL: {os.environ.get('ATLAS_IMAGE_MODEL')}")
    print(f"✓ ATLAS_IMAGE_SIZE: {os.environ.get('ATLAS_IMAGE_SIZE')}\n")
    
    # Test script (short, 6-8 sentences for 60s)
    script = """
    Success is not final, failure is not fatal. It is the courage to continue that counts.
    
    Throughout history, great achievers faced countless setbacks. They persisted when others gave up.
    
    Each obstacle became a stepping stone. Every failure taught a valuable lesson.
    
    The journey shapes us more than the destination. Our character is forged in adversity.
    
    Winners are simply those who kept going. They refused to accept defeat as final.
    
    Today, you face your own challenges. Remember, persistence always outlasts resistance.
    """.strip()
    
    print("📝 Script (60-90s):")
    print(script[:200] + "...\n")
    
    # Output paths
    artifacts_dir = Path("/workspace/artifacts")
    artifacts_dir.mkdir(exist_ok=True)
    
    output_video = artifacts_dir / "frontier_smoke_real.mp4"
    
    # Generate voiceover using Atlas TTS
    print("🎤 Generating voiceover with Atlas TTS...")
    try:
        voiceover_path = generate_voiceover(
            script=script,
            voice="leo",  # Atlas default voice
            output_dir=str(artifacts_dir),
        )
        print(f"✓ Voiceover: {voiceover_path}\n")
    except Exception as e:
        print(f"❌ Voiceover generation failed: {e}")
        print("Falling back to test fixture...")
        # Create a silent audio file as fallback
        import subprocess
        voiceover_path = str(artifacts_dir / "test_vo.wav")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=mono",
            "-t", "60",
            voiceover_path
        ], check=True, capture_output=True)
        print(f"✓ Fallback silent audio: {voiceover_path}\n")
    
    # Run Frontier pipeline
    print("🎬 Running Frontier pipeline...")
    print("  - Atlas gpt-image-2 stills (target: 6-8)")
    print("  - Pexels b-roll videos + photos")
    print("  - Motion planning with word-locked timing")
    print("  - ASS kinetic captions")
    print("  - Jung color grade + dust + vignette\n")
    
    try:
        result = run_frontier_pipeline(
            script=script,
            voiceover_path=voiceover_path,
            output_name="frontier_smoke_real.mp4",
            progress_callback=lambda msg: print(f"  {msg}"),
        )
        
        print(f"\n✅ SMOKE TEST COMPLETE")
        print(f"Output: {result['output_path']}")
        print(f"Duration: {result.get('duration_sec', 0):.1f}s")
        print(f"\nTiming breakdown:")
        for step, duration in result.get("timing", {}).items():
            print(f"  {step}: {duration:.1f}s")
        
        # Extract frame grabs
        print(f"\n📸 Extracting frame grabs...")
        for i, sec in enumerate([5, 15, 30, 45]):
            frame_path = artifacts_dir / f"frame_{i+1:02d}_{sec}s.png"
            subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(sec),
                "-i", result['output_path'],
                "-frames:v", "1",
                str(frame_path)
            ], check=True, capture_output=True)
            print(f"  ✓ {frame_path.name}")
        
        print(f"\n📊 Compare frames against Jung reference:")
        print(f"  Reference: /home/ubuntu/.cursor/projects/workspace/uploads/jung-whop-ref-45s_1ac1.mp4")
        print(f"  Output: {result['output_path']}")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ PIPELINE FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
