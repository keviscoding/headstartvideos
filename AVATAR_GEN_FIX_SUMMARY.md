# Avatar Generator Assembly Fix Summary

## Issues Fixed

### 1. Concat Path Doubling
**Error:** `output/avatar_gen/1791129206/output/avatar_gen/1791129206/shot_000_avatar.mp4`

**Root Cause:**
- `concat.txt` contained relative paths like `output/avatar_gen/1791129206/shot_000_avatar.mp4`
- When ffmpeg read these paths, they were interpreted relative to an already-nested directory
- This caused the work directory path to appear twice

**Fix:**
- Changed `assemble_avatar_video()` to use absolute resolved paths via `Path.resolve()`
- Added single quote escaping for ffmpeg concat demuxer safety
- Matches the pattern used by `ranking_pipeline.py` (line 1036)

**Code Change (`core/avatar_gen_pipeline.py:544-547`):**
```python
# Before:
concat_lines.append(f"file '{shot.asset_path}'")

# After:
abs_path = Path(shot.asset_path).resolve()
escaped = str(abs_path).replace("'", "'\\''")
concat_lines.append(f"file '{escaped}'")
```

### 2. Missing Uploaded Avatar Files
**Error:** `[Errno 2] No such file or directory: '/app/output/avatar_uploads/2/1791130317_fb29e178.jpg'`

**Root Cause:**
- Uploaded avatars were saved locally on web dyno: `OUTPUT_DIR / "avatar_uploads" / user_id / filename`
- Cook jobs run on separate Fly machines (ephemeral disks)
- Local file paths don't transfer between machines

**Fix:**
Two-part solution:

**Part A - Upload to remote storage (`webapp/server.py:2637-2644, 2671-2678`):**
```python
# After saving locally, upload to Spaces if configured
if storage.is_remote():
    key = f"avatar_uploads/{user['id']}/{fname}"
    avatar_path_or_prompt = storage.store_file(str(local), key, "image/jpeg")
else:
    avatar_path_or_prompt = str(local)
```

**Part B - Fetch in cook worker (`webapp/cook_runner.py:361-366`):**
```python
# Fetch avatar if it's a URL or remote path (not a prompt)
if avatar_source and not avatar_source.startswith("prompt:"):
    try:
        avatar_source = fetch_to_local(avatar_source, cache_dir)
    except Exception as e:
        raise RuntimeError(f"Could not fetch avatar image: {e}")
```

## How It Works

### Concat Path Resolution Flow
1. Pipeline creates shot files: `work_dir / "shot_000_avatar.mp4"`
2. `shot.asset_path` stores the path as string
3. When assembling, paths are resolved to absolute: `/tmp/.../output/avatar_gen/1791129206/shot_000_avatar.mp4`
4. FFmpeg reads absolute paths correctly from `concat.txt`

### Avatar Upload Flow

**With Remote Storage (Production - Fly workers):**
1. User uploads avatar → saved locally on web dyno
2. File uploaded to Spaces → returns URL
3. Job request_json contains Spaces URL
4. Cook worker fetches URL → downloads to local cache
5. Pipeline receives local cache path → works correctly

**Without Remote Storage (Development - COOK_ON_WEB=1):**
1. User uploads avatar → saved locally
2. Job request_json contains local path
3. Cook worker (same machine) uses local path directly
4. Pipeline receives local path → works correctly

**Prompt-based Avatars:**
1. User enters prompt → stored as `"prompt:description"`
2. Cook worker skips fetch (not a file path)
3. Pipeline receives prompt string unchanged
4. Pipeline generates avatar from prompt

## Testing

### Test Coverage
- `tests/test_avatar_gen_concat_fix.py` - Verifies absolute path usage
- `tests/test_avatar_gen_upload_fix.py` - Verifies upload and fetch logic

### Test Results
All tests pass, confirming:
- ✅ concat.txt uses absolute paths without doubling
- ✅ Server uploads avatars to remote storage when configured
- ✅ Cook runner fetches avatar_source before pipeline
- ✅ Prompt avatars skip fetch
- ✅ Single quotes are escaped in paths

## Deployment

### Changes Made
- `core/avatar_gen_pipeline.py` - Concat path resolution
- `webapp/server.py` - Upload to remote storage
- `webapp/cook_runner.py` - Fetch before pipeline
- `tests/test_avatar_gen_concat_fix.py` - New test
- `tests/test_avatar_gen_upload_fix.py` - New test

### Compatibility
- ✅ Works with existing Fly cook image (v73+)
- ✅ No changes to fly.cook.toml or deployment config
- ✅ No changes to UI, pricing, credits, or refunds
- ✅ Backward compatible (works with or without remote storage)

### Ready to Deploy
Once merged, Short generate avatar jobs will successfully complete assembly on Fly cook workers.

## References

- **Live Failure:** Job 275cdf9e-c5e8-472d-8f22-c44839e64e09 (2026-10-04T16:03:48Z)
- **Error Logs:** Fly image v73 (channelrecipe-cook)
- **Pattern Source:** `ranking_pipeline.py:1036` (uses `p.resolve()` for concat paths)
- **PR:** https://github.com/keviscoding/headstartvideos/pull/16
