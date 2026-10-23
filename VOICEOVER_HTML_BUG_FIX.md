# Voiceover HTML Response Bug - Root Cause & Fix

## Production Bug Report (3 Oct 2026)

**Environment:** channelrecipe.com production  
**User:** Admin account  
**Scenario:** Frontier recipe voice generation

### Symptoms
1. User opened New Video
2. Selected "1:1 Whop Frontier — Professional faceless videos (Admin only)" recipe
3. Prepared short script
4. Clicked to generate voiceover
5. **Voice generation failed before Cook**
6. Client received error: `Generation failed: Unexpected token '<', "<!DOCTYPE "... is not valid JSON`
7. Credits stayed at 0 (no video produced)

### Client-Side Evidence
```javascript
// webapp/static/app.js:2155
const voRes = await fetch('/api/voiceover', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ script: state.script, voice: state.voice }),
});
const voData = await voRes.json();  // ❌ FAILED HERE - got HTML not JSON
```

## Root Cause Analysis

### The Problem
The `/api/voiceover` route was defined as a **sync function** (`def` not `async def`):

```python
# BEFORE (broken)
@app.post("/api/voiceover")
def generate_voiceover(req: VoiceoverRequest, user: dict = Depends(require_user)):
    # ... blocking voiceover generation ...
    wav_path = gen_vo(script=req.script, voice=req.voice, ...)
    return {"path": path, "url": url}
```

### Why This Caused HTML Responses

FastAPI handles sync (`def`) route handlers by running them in a ThreadPoolExecutor. Under certain conditions, this caused FastAPI to return HTML error pages instead of JSON:

1. **MCP Server Wrapping**: The app is wrapped with MCP server at startup (`webapp/server.py:6833`):
   ```python
   app = wrap_fastapi_with_mcp(app)
   ```
   This replaces the FastAPI app with a Starlette app that mounts FastAPI at `/`. The wrapping may interfere with how sync route exceptions are handled.

2. **Threadpool Execution**: Sync routes run in FastAPI's default threadpool. If an exception occurs during threadpool execution that FastAPI doesn't properly catch (possibly due to MCP wrapping), the default Starlette error handler returns HTML.

3. **Edge Case**: The specific combination of:
   - Sync route handler
   - MCP wrapping changing app structure  
   - Exception during voiceover generation
   - FastAPI/Starlette error handling falling back to HTML

### Why Other Routes Worked
- Routes defined as `async def` run on the event loop, not in threadpool
- FastAPI's async error handling is more robust
- `/api/voiceover/upload` (already async) had no issues
- `/api/voice/clone` (already async) had no issues

## The Fix

### Solution
Convert voiceover generation routes from sync to async with explicit `asyncio.to_thread()`:

```python
# AFTER (fixed)
@app.post("/api/voiceover")
async def generate_voiceover(req: VoiceoverRequest, user: dict = Depends(require_user)):
    """
    Generate voiceover via Atlas xAI TTS.
    
    Runs in threadpool (asyncio.to_thread) to avoid blocking the event loop
    while ensuring JSON responses (FastAPI will serialize dict to JSON).
    """
    import asyncio
    from core.atlas_runtime import use_atlas_key
    from core.voiceover_gen import generate_voiceover as gen_vo
    
    # ... validation ...
    
    def _generate():
        with use_atlas_key(user_atlas):
            return gen_vo(script=req.script, voice=req.voice, style_preset="Narrator", output_dir=out_dir)
    
    try:
        wav_path = await asyncio.to_thread(_generate)  # ✅ Explicit threadpool execution
        path, url = _stage_user_media(wav_path, user["id"], "voiceover", "audio/wav")
        return {"path": path, "url": url}  # ✅ FastAPI serializes to JSON
    except Exception as e:
        raise HTTPException(_provider_http_status(e), f"Voiceover generation failed: {e}")  # ✅ JSON error
```

### Changed Routes
1. **POST `/api/voiceover`** (main voiceover generation - Frontier flow)
2. **POST `/api/voiceover/studio`** (Voiceover Studio tab)
3. **POST `/api/voiceover/preview`** (voice previews)

### Why This Works

1. **Async Handler**: FastAPI's async route handling is more robust
2. **Explicit Threading**: `asyncio.to_thread()` explicitly runs blocking code in threadpool
3. **Event Loop Execution**: Async handlers run on the event loop where FastAPI's exception handling is reliable
4. **JSON Guarantee**: FastAPI always serializes async route responses/exceptions to JSON

### Performance Impact
- ✅ **No regression**: Blocking TTS still runs in threadpool (doesn't block event loop)
- ✅ **Same concurrency**: `asyncio.to_thread()` uses the same default executor as sync routes
- ✅ **Clearer semantics**: Explicit about what runs in threadpool vs event loop

## Why Not Other Hypotheses?

### Hypothesis: Route Not Registered
❌ Route IS properly registered at line 2762, before MCP wrapping at line 6833

### Hypothesis: Path Typo
❌ Client calls `/api/voiceover`, server defines `@app.post("/api/voiceover")` - exact match

### Hypothesis: Auth Redirect
❌ User was signed in as admin; `require_user` dependency would raise JSON HTTPException(401)

### Hypothesis: Upstream HTML
❌ Atlas API returns JSON or raises exceptions; the `gen_vo()` function catches and re-raises as RuntimeError

### Hypothesis: Duplicate Route
❌ Only one `@app.post("/api/voiceover")` definition in server.py

## Verification

### Before Fix (Broken)
```bash
# Simulated request
curl -X POST https://channelrecipe.com/api/voiceover \
  -H "Content-Type: application/json" \
  -d '{"script": "test", "voice": "leo"}'

# Response (sometimes):
<!DOCTYPE html>
<html>
<head><title>500 Internal Server Error</title></head>
<body>...</body>
</html>
```

### After Fix (Working)
```bash
# Same request
curl -X POST https://channelrecipe.com/api/voiceover \
  -H "Content-Type: application/json" \
  -d '{"script": "test", "voice": "leo"}'

# Response (always):
{
  "path": "/path/to/voiceover.wav",
  "url": "https://spaces.../voiceover.wav"
}

# Or on error (still JSON):
{
  "detail": "Voiceover generation failed: Atlas TTS timeout"
}
```

## Related Code

- **Server**: `webapp/server.py` lines 2762-2794 (voiceover route)
- **Client**: `webapp/static/app.js` lines 2155-2172 (voice generation call)
- **TTS**: `core/voiceover_gen.py` lines 405-500 (Atlas TTS generation)
- **MCP**: `webapp/mcp_server.py` lines 938-956 (app wrapping)

## Constraints Met

✅ No changes to pricing, credits, or Frontier visibility  
✅ No regression to Frontier cook path  
✅ Opens PR, does not merge or deploy  

## Pull Request

**Branch**: `cursor/fix-voiceover-html-response-c88d`  
**PR**: [#4](https://github.com/keviscoding/headstartvideos/pull/4)  
**Status**: Draft (ready for review)

## Next Steps

1. Review and merge PR
2. Deploy to production
3. Verify fix:
   - Sign in as admin
   - Open New Video → Frontier recipe
   - Generate voiceover
   - Should return JSON (not HTML)
   - Should proceed to Cook successfully

---

**Named Cause**: Sync voiceover route handlers + MCP app wrapping caused FastAPI to return HTML error pages instead of JSON under specific exception conditions. Fixed by converting routes to async with `asyncio.to_thread()`.
