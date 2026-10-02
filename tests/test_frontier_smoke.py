"""
Frontier smoke test - renders a short test video to validate the pipeline.

This smoke test validates the Frontier assembly pipeline without requiring
API keys by using fixture/mock data. Tests:
- Motion plan assembly
- Ken Burns zoom generation
- ASS caption styling
- Video assembly workflow

For full testing with real Atlas/Pexels, set ATLASCLOUD_KEY and PEXELS_KEY.
"""

import os
import sys
from pathlib import Path

# Add workspace to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import frontier_motion_planner, frontier_assembler


def create_test_fixtures(output_dir: Path):
    """Create minimal test fixtures for smoke test."""
    print("[smoke] Creating test fixtures...")
    
    # Create test stills (colored placeholders)
    from PIL import Image, ImageDraw, ImageFont
    
    stills = []
    colors = ["#2C3E50", "#34495E", "#7F8C8D", "#95A5A6"]
    
    for i, color in enumerate(colors):
        still_path = output_dir / f"test_still_{i}.png"
        img = Image.new("RGB", (1920, 1080), color)
        draw = ImageDraw.Draw(img)
        
        # Add text
        text = f"Test Still {i+1}"
        # Use default font
        draw.text((960, 540), text, fill="white", anchor="mm")
        
        img.save(still_path)
        stills.append(still_path)
        print(f"  Created {still_path.name}")
    
    # Create test Pexels "videos" (static images for now)
    pexels_videos = []
    for i in range(2):
        pexels_path = output_dir / f"test_pexels_video_{i}.mp4"
        # Create a short black video using ffmpeg
        os.system(f"ffmpeg -f lavfi -i color=c=#16213E:s=1920x1080:d=3 -c:v libx264 -y {pexels_path} 2>/dev/null")
        if pexels_path.exists():
            pexels_videos.append(pexels_path)
    
    # Create test Pexels photos
    pexels_photos = []
    for i in range(2):
        photo_path = output_dir / f"test_pexels_photo_{i}.png"
        img = Image.new("RGB", (1920, 1080), "#0F3460")
        img.save(photo_path)
        pexels_photos.append(photo_path)
    
    # Create test voiceover (silence)
    vo_path = output_dir / "test_voiceover.mp3"
    os.system(f"ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 20 -c:a libmp3lame -y {vo_path} 2>/dev/null")
    
    # Create test SRT
    srt_path = output_dir / "test_subs.srt"
    srt_content = """1
00:00:00,000 --> 00:00:04,000
The shadow is not what you've been told.

2
00:00:04,000 --> 00:00:08,000
It is the part of yourself you've pushed away.

3
00:00:08,000 --> 00:00:12,000
Buried so thoroughly that you forgot it existed.

4
00:00:12,000 --> 00:00:16,000
But it has not forgotten you.

5
00:00:16,000 --> 00:00:20,000
It runs beneath every decision you make.
"""
    srt_path.write_text(srt_content)
    
    return {
        "stills": stills,
        "pexels_videos": pexels_videos,
        "pexels_photos": pexels_photos,
        "voiceover": vo_path,
        "srt": srt_path,
    }


def test_frontier_smoke():
    """Run Frontier smoke test."""
    print("\n" + "="*60)
    print("FRONTIER SMOKE TEST")
    print("="*60 + "\n")
    
    output_dir = Path("/tmp/frontier_smoke_test")
    output_dir.mkdir(exist_ok=True)
    
    # Create fixtures
    fixtures = create_test_fixtures(output_dir)
    
    # Build motion plan
    print("\n[smoke] Building motion plan...")
    ai_stills = [
        {
            "path": fixtures["stills"][0],
            "start_sec": 0.0,
            "end_sec": 4.0,
            "text": "The shadow is not what you've been told",
        },
        {
            "path": fixtures["stills"][1],
            "start_sec": 8.0,
            "end_sec": 12.0,
            "text": "Buried so thoroughly",
        },
        {
            "path": fixtures["stills"][2],
            "start_sec": 16.0,
            "end_sec": 20.0,
            "text": "It runs beneath every decision",
        },
    ]
    
    motion_segments = frontier_motion_planner.plan_motion_timeline(
        ai_stills=ai_stills,
        pexels_videos=fixtures["pexels_videos"],
        pexels_photos=fixtures["pexels_photos"],
        total_duration_sec=20.0,
        zoom_strategy="alternate",
    )
    
    print(f"  Created {len(motion_segments)} motion segments")
    for i, seg in enumerate(motion_segments):
        print(f"    {i+1}. {seg.type} ({seg.start_sec:.1f}s-{seg.end_sec:.1f}s) zoom={seg.zoom}")
    
    # Assemble video
    print("\n[smoke] Assembling video...")
    output_video = output_dir / "frontier_smoke_test.mp4"
    
    result = frontier_assembler.assemble_frontier_video(
        motion_segments=motion_segments,
        voiceover_path=fixtures["voiceover"],
        subtitle_path=fixtures["srt"],
        output_path=output_video,
        color_grade="eq=brightness=-0.06:saturation=0.62:contrast=1.10",
        add_dust=False,  # No dust overlay asset
        add_vignette=True,
        progress_callback=lambda msg: print(f"  {msg}"),
    )
    
    print(f"\n✅ Smoke test complete!")
    print(f"   Output: {output_video}")
    print(f"   Duration: {result['duration_sec']:.1f}s")
    print(f"   Segments: {result['segment_count']}")
    
    if output_video.exists():
        size_mb = output_video.stat().st_size / 1024 / 1024
        print(f"   Size: {size_mb:.2f} MB")
        
        # Extract a frame to verify
        frame_path = output_dir / "smoke_frame.jpg"
        os.system(f"ffmpeg -i {output_video} -ss 8 -frames:v 1 -y {frame_path} 2>/dev/null")
        if frame_path.exists():
            print(f"   Frame extracted: {frame_path}")
        
        return output_video
    else:
        print("   ⚠️  Video file not created")
        return None


if __name__ == "__main__":
    try:
        video_path = test_frontier_smoke()
        if video_path:
            print("\n" + "="*60)
            print("SMOKE TEST PASSED")
            print("="*60)
            sys.exit(0)
        else:
            print("\n" + "="*60)
            print("SMOKE TEST FAILED - No output video")
            print("="*60)
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ SMOKE TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
