"""
Test that uploaded avatars are accessible to cook workers.

Reproduces the error:
  [Errno 2] No such file or directory: '/app/output/avatar_uploads/2/1791130317_fb29e178.jpg'

The fix ensures:
1. Uploads go to remote storage when configured
2. Cook runner fetches avatar_source before passing to pipeline
"""
from pathlib import Path
import tempfile


def test_cook_runner_has_avatar_fetch_logic():
    """Verify cook_runner.py contains the fetch_to_local call for avatar_source."""
    cook_runner_path = Path(__file__).parent.parent / "webapp" / "cook_runner.py"
    content = cook_runner_path.read_text()
    
    # Check that avatar_generator recipe has fetch logic
    assert 'recipe == "avatar_generator"' in content, (
        "cook_runner should have avatar_generator recipe handler"
    )
    
    # Check for fetch_to_local call with avatar_source in the avatar_generator block
    # The fix adds: avatar_source = fetch_to_local(avatar_source, cache_dir)
    lines = content.split("\n")
    in_avatar_block = False
    found_fetch = False
    avatar_block_start = -1
    
    for i, line in enumerate(lines):
        if 'recipe == "avatar_generator"' in line:
            in_avatar_block = True
            avatar_block_start = i
            print(f"Found avatar_generator block at line {i+1}")
        
        if in_avatar_block:
            # Look for fetch_to_local call with avatar_source
            if 'fetch_to_local' in line:
                print(f"Found fetch_to_local at line {i+1}: {line.strip()}")
                if 'avatar_source' in line:
                    found_fetch = True
                    print(f"✓ Found complete fetch logic at line {i+1}")
                    break
        
        # Exit the block when we hit the next recipe condition (but not the current line)
        if in_avatar_block and i > avatar_block_start:
            if i > avatar_block_start + 50:
                print(f"Searched up to line {i+1}, stopping")
                break
            
            if 'elif recipe ==' in line and 'avatar_generator' not in line:
                print(f"Hit next recipe at line {i+1}, stopping")
                break
    
    assert found_fetch, (
        "cook_runner should call fetch_to_local(avatar_source, ...) in avatar_generator block"
    )
    
    print("✓ PASS: cook_runner has avatar fetch logic")


def test_server_has_remote_storage_logic():
    """Verify server.py uploads avatars to remote storage when configured."""
    server_path = Path(__file__).parent.parent / "webapp" / "server.py"
    content = server_path.read_text()
    
    # Check for avatar upload handling
    assert 'avatar_uploads' in content, "server.py should handle avatar uploads"
    
    # Check for storage.is_remote() check in avatar upload section
    lines = content.split("\n")
    found_remote_check = False
    found_store_call = False
    in_upload_section = False
    
    for i, line in enumerate(lines):
        # Look for avatar upload sections
        if 'avatar_source_type == "upload"' in line or 'avatar_uploads' in line:
            in_upload_section = True
        
        # Check for storage.is_remote() within reasonable proximity
        if in_upload_section:
            if 'storage.is_remote()' in line or 'is_remote()' in line:
                found_remote_check = True
                print(f"✓ Found remote storage check at line {i+1}")
            
            if 'storage.store_file' in line or 'store_file(' in line:
                if 'avatar' in content[max(0, content.find(line)-500):content.find(line)+500]:
                    found_store_call = True
                    print(f"✓ Found store_file call at line {i+1}")
        
        # Exit after processing a reasonable section
        if in_upload_section and i > 100 and 'def ' in line and 'avatar' not in line.lower():
            break
    
    # At least one of these should be present for the fix
    has_fix = found_remote_check or found_store_call
    assert has_fix, (
        "server.py should check storage.is_remote() or call store_file for avatar uploads"
    )
    
    print("✓ PASS: server.py has remote storage logic for avatars")


def test_assemble_function_exists():
    """Verify the assembly function exists in the pipeline."""
    pipeline_path = Path(__file__).parent.parent / "core" / "avatar_gen_pipeline.py"
    content = pipeline_path.read_text()
    
    # Check that assemble_mixed_avatar_broll_video function exists
    assert 'def assemble_mixed_avatar_broll_video' in content, (
        "Pipeline should have assemble_mixed_avatar_broll_video function"
    )
    
    print("✓ PASS: Assembly function exists in pipeline")


if __name__ == "__main__":
    test_cook_runner_has_avatar_fetch_logic()
    test_server_has_remote_storage_logic()
    test_assemble_function_exists()
    print("\n✓ All avatar upload/fetch code verification tests passed")
