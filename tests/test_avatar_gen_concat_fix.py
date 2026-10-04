"""
Test that avatar_gen concat.txt uses absolute paths to avoid path doubling.

Reproduces the error:
  [concat] Impossible to open 'output/avatar_gen/1791129206/output/avatar_gen/1791129206/shot_000_avatar.mp4'

The fix ensures concat.txt contains absolute resolved paths.
"""
from pathlib import Path
import tempfile
import shutil


def test_concat_uses_absolute_paths():
    """Verify concat.txt contains absolute paths, not relative paths."""
    from core.avatar_gen_pipeline import AvatarShot, assemble_avatar_video
    
    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir) / "output" / "avatar_gen" / "1791129206"
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Create dummy shot files
        shot_files = []
        for i in range(3):
            shot_path = work_dir / f"shot_{i:03d}_avatar.mp4"
            shot_path.write_text(f"fake video {i}")
            shot_files.append(shot_path)
        
        # Create shots with relative-style paths (as the pipeline currently creates)
        shots = [
            AvatarShot(
                index=i,
                start_sec=i * 4.0,
                end_sec=(i + 1) * 4.0,
                duration=4.0,
                shot_type="avatar",
                text=f"Segment {i}",
                visual_prompt="",
                asset_path=str(shot_files[i]),
                is_generated=True,
            )
            for i in range(3)
        ]
        
        output_path = work_dir / "final_video.mp4"
        concat_file = work_dir / "concat.txt"
        
        # The assembly should fail without ffmpeg, but we can check the concat file
        try:
            assemble_avatar_video(shots, work_dir, output_path, progress=None)
        except Exception:
            # Expected to fail without real ffmpeg/video files
            pass
        
        # Verify concat.txt was created
        assert concat_file.exists(), "concat.txt should be created"
        
        concat_content = concat_file.read_text()
        print(f"\nconcat.txt content:\n{concat_content}\n")
        
        lines = concat_content.strip().split("\n")
        file_lines = [line for line in lines if line.startswith("file ")]
        
        # Verify all paths are absolute
        for line in file_lines:
            # Extract path from "file 'path'"
            path_str = line[len("file '"):-1]  # Remove "file '" prefix and "'" suffix
            
            # Path should be absolute
            assert Path(path_str).is_absolute(), (
                f"Path in concat.txt must be absolute, got: {path_str}"
            )
            
            # Path should NOT contain doubled segments like "output/.../output/..."
            path_parts = Path(path_str).parts
            part_counts = {}
            for i, part in enumerate(path_parts):
                if i == 0:
                    continue  # Skip root
                if part in part_counts:
                    # Check if we have suspicious doubling
                    if part in ("output", "avatar_gen") and part_counts[part] > 1:
                        raise AssertionError(
                            f"Path appears to have doubled segments: {path_str}"
                        )
                part_counts[part] = part_counts.get(part, 0) + 1
        
        print("✓ PASS: concat.txt uses absolute paths without doubling")


def test_concat_path_not_doubled_in_work_dir():
    """
    Ensure that when work_dir is output/avatar_gen/123,
    the concat.txt paths don't become output/.../output/... when resolved.
    """
    from core.avatar_gen_pipeline import AvatarShot, assemble_avatar_video
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Simulate the work_dir structure as it happens in production
        work_dir = Path(tmpdir) / "output" / "avatar_gen" / "test_job"
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a shot file
        shot_file = work_dir / "shot_000_avatar.mp4"
        shot_file.write_text("fake")
        
        shot = AvatarShot(
            index=0,
            start_sec=0,
            end_sec=4,
            duration=4,
            shot_type="avatar",
            text="Test",
            visual_prompt="",
            asset_path=str(shot_file),
            is_generated=True,
        )
        
        output_path = work_dir / "final.mp4"
        
        try:
            assemble_avatar_video([shot], work_dir, output_path)
        except Exception:
            pass
        
        concat_file = work_dir / "concat.txt"
        content = concat_file.read_text()
        
        # The absolute path should appear exactly once, not doubled
        absolute_shot = shot_file.resolve()
        assert f"file '{absolute_shot}'" in content or str(absolute_shot) in content, (
            "concat.txt should contain the resolved absolute path"
        )
        
        # Check for path doubling
        if "output/avatar_gen" in str(absolute_shot):
            # Count occurrences of the work_dir pattern
            parts_str = str(absolute_shot)
            if parts_str.count("output/avatar_gen/test_job") > 1:
                raise AssertionError(f"Path is doubled: {parts_str}")
        
        print("✓ PASS: Paths in concat.txt are not doubled")


if __name__ == "__main__":
    test_concat_uses_absolute_paths()
    test_concat_path_not_doubled_in_work_dir()
    print("\n✓ All concat path tests passed")
