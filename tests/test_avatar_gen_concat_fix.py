"""
Test that the avatar generation pipeline skips zero-duration b-roll shots.

Reproduces the error:
  FFmpeg: Stream specifier ':v' ... matches no streams / Error binding filtergraph
  
When shot planning creates near-zero duration shots (e.g., between(t,16.43,16.43)),
the assembly should skip them to avoid FFmpeg errors.
"""
from pathlib import Path
import tempfile


def test_zero_duration_broll_skip():
    """Verify that zero-duration or near-zero b-roll shots are skipped in assembly."""
    from core.avatar_gen_pipeline import AvatarShot, assemble_mixed_avatar_broll_video
    
    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir) / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a fake avatar video file
        avatar_video_path = work_dir / "avatar_full.mp4"
        avatar_video_path.write_bytes(b"fake video")
        
        # Create a fake audio file
        audio_path = work_dir / "audio.wav"
        audio_path.write_bytes(b"fake audio")
        
        # Create a mix of normal and zero-duration shots
        shots = [
            AvatarShot(
                index=0,
                start_sec=0.0,
                end_sec=4.0,
                duration=4.0,
                shot_type="avatar",
                text="Opening",
                visual_prompt="",
                asset_path="",
                is_generated=False,
            ),
            # Normal b-roll shot
            AvatarShot(
                index=1,
                start_sec=4.0,
                end_sec=7.5,
                duration=3.5,
                shot_type="broll_still",
                text="Some content",
                visual_prompt="content",
                asset_path=str(work_dir / "broll_1.mp4"),
                is_generated=True,
            ),
            # ZERO-DURATION b-roll (should be skipped)
            AvatarShot(
                index=2,
                start_sec=7.5,
                end_sec=7.5,
                duration=0.0,
                shot_type="broll_still",
                text="Zero duration",
                visual_prompt="zero",
                asset_path=str(work_dir / "broll_2.mp4"),
                is_generated=True,
            ),
            # Near-zero b-roll (< 0.05s, should be skipped)
            AvatarShot(
                index=3,
                start_sec=7.5,
                end_sec=7.52,
                duration=0.02,
                shot_type="broll_still",
                text="Near zero",
                visual_prompt="near zero",
                asset_path=str(work_dir / "broll_3.mp4"),
                is_generated=True,
            ),
            # Normal b-roll again
            AvatarShot(
                index=4,
                start_sec=7.52,
                end_sec=11.0,
                duration=3.48,
                shot_type="broll_still",
                text="More content",
                visual_prompt="more",
                asset_path=str(work_dir / "broll_4.mp4"),
                is_generated=True,
            ),
            AvatarShot(
                index=5,
                start_sec=11.0,
                end_sec=14.0,
                duration=3.0,
                shot_type="avatar",
                text="Ending",
                visual_prompt="",
                asset_path="",
                is_generated=False,
            ),
        ]
        
        # Create fake b-roll files for the valid shots
        (work_dir / "broll_1.mp4").write_bytes(b"fake broll 1")
        (work_dir / "broll_2.mp4").write_bytes(b"fake broll 2")
        (work_dir / "broll_3.mp4").write_bytes(b"fake broll 3")
        (work_dir / "broll_4.mp4").write_bytes(b"fake broll 4")
        
        output_path = work_dir / "final.mp4"
        
        # Track progress messages to verify skipping
        progress_messages = []
        def track_progress(msg: str):
            progress_messages.append(msg)
        
        # The assembly should skip zero-duration shots without error
        # (It will fail at ffmpeg execution, but we can check the filter construction)
        try:
            assemble_mixed_avatar_broll_video(
                avatar_video_path,
                audio_path,
                shots,
                work_dir,
                output_path,
                progress=track_progress,
            )
        except Exception as e:
            # Expected to fail without real video files, but check error type
            error_msg = str(e)
            # Should NOT have the "Stream specifier ':v' matches no streams" error
            assert "matches no streams" not in error_msg, (
                f"Zero-duration shots were not skipped! Error: {error_msg}"
            )
            # Should have skipped the zero-duration shots (check progress messages)
            skip_messages = [m for m in progress_messages if "Skipping zero-duration" in m]
            assert len(skip_messages) >= 2, (
                f"Expected at least 2 zero-duration skip messages, got {len(skip_messages)}: {progress_messages}"
            )
            print(f"✓ Zero-duration shots skipped: {skip_messages}")


if __name__ == "__main__":
    test_zero_duration_broll_skip()
    print("\n✓ Zero-duration b-roll skip test passed")
