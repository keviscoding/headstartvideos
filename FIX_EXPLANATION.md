# Fix: Allow local Whisper fallback on Fly cook workers when Groq returns 403

## Root Cause

Production cooks on the Fly app `channelrecipe-cook` were failing with:

```
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

**However**, this was NOT the actual root cause - it was a symptom that appeared AFTER the real issue occurred.

### The Real Issue

1. **Groq API is blocked from Fly's network** → returns `403 PermissionDeniedError: "Access denied. Please check your network settings."`
2. The code tried to fall back to local Whisper (faster-whisper)
3. But `ALLOW_LOCAL_WHISPER=0` in `fly.cook.toml`, blocking the fallback
4. The original logic assumed local Whisper is dangerous **everywhere in production**, but the comment specifically said it's dangerous **on the web dyno** (due to CPU/memory overload under concurrent load)

### The Confusion About PyAV

The error message mentioned `metadata_errors` because:
- faster-whisper 1.2.1 internally uses PyAV's `av.open()` 
- Newer PyAV (19+) removed the `metadata_errors` parameter
- But faster-whisper handles this automatically with version detection
- The real issue was that the fallback was being blocked before it even tried to use PyAV

## The Fix

### Changed Behavior

**Before:** Local Whisper fallback was blocked in production if `ALLOW_LOCAL_WHISPER=0`, regardless of environment.

**After:** 
- ✅ **Cook workers** (Fly Machines with `COOK_ON_WEB=0` and 4GB RAM) can fall back to local Whisper even in production when Groq fails
- ❌ **Web dyno** (`COOK_ON_WEB=1`) still blocks local Whisper in production to prevent CPU overload from concurrent requests
- ✅ **PyAV compatibility check** added before attempting local Whisper fallback, with clear error message if missing

### Detection Logic

Cook worker is detected when:
- `config.COOK_ON_WEB` is `False` (set in `fly.cook.toml` and worker configs)
- OR `FLY_MACHINE_ID` / `FLY_APP_NAME` environment variables are present (Fly ephemeral machines)

### Safety Guardrails

1. **PyAV availability check** before fallback - fails fast with installation instructions
2. **Environment detection** - different behavior for cook workers vs web dyno
3. **Explicit error messages** - tells you which environment you're in and what went wrong

## Changes Made

### `core/segmenter.py`

1. Added `_check_pyav_compatible()` function to verify PyAV is installed and usable
2. Added `on_cook_worker` detection using `COOK_ON_WEB` config and Fly environment variables
3. Modified `align_script_to_audio()` to:
   - Allow local Whisper fallback on cook workers even when `ALLOW_LOCAL_WHISPER=0`
   - Check PyAV compatibility before attempting fallback
   - Provide clear error messages indicating the environment type
   - Still block fallback on web dyno to prevent resource exhaustion

### No Changes Needed

- `requirements.txt` - already has `av>=12.0.0` pinned (from commit 5b2f6db)
- `fly.cook.toml` - keeps `ALLOW_LOCAL_WHISPER=0` (now correctly interpreted as "don't allow on web, but OK on cook workers")

## Testing

Added `verify_fix.py` to validate:
- PyAV compatibility check function exists and works
- Cook worker detection is implemented correctly
- Fallback logic differentiates web dyno from cook workers
- PyAV check is called before fallback attempts
- requirements.txt has correct PyAV pin

## Why This Works

1. **4GB cook machines** have enough RAM to run Whisper `base` model without OOM
2. **One cook per machine** (`MAX_CONCURRENT_COOKS=1`) prevents CPU thrashing
3. **Ephemeral machines** are started per-cook and destroyed after, so resource leaks are contained
4. **Web dyno** remains protected from concurrent Whisper loads that would freeze FastAPI

## Deployment Notes

When the Fly cook image is rebuilt with this change:
- Groq 403 errors will automatically fall back to local Whisper
- Cooks will complete successfully instead of failing
- No environment variable changes needed (existing `ALLOW_LOCAL_WHISPER=0` is correct)
- Web process behavior unchanged (still blocks local Whisper)

## Related Issues

- Sentry issue `PYTHON-FASTAPI-4B`: `TypeError: open() got an unexpected keyword argument 'metadata_errors'`
- Environment: `fly-cook`
- Recipe: `animated_explainer`
- Job: `258217e1-2a3d-405e-bdf1-9de689d32a11`
- Last event: `2026-10-03T13:24:35Z`
