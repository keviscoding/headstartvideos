# PROVEN Atlas Path Implemented - Oct 2, 2026 11:36 AM

## Status: ✅ READY FOR YOUR MAC SMOKE

The EXACT Whop Frontier Atlas path (Mac 8/8 success) is now on PR #2.

---

## What Was Fixed

### ❌ DELETED: Broken Atlas Implementation

Previous code had 5 critical bugs:
1. `model: "gpt-image-2"` — WRONG (must be `openai/gpt-image-2/text-to-image`)
2. `width`/`height` payload — WRONG (must be `size` + `quality` + `output_format`)
3. `data.get("id")` top-level — WRONG (ID is nested: `response["data"]["id"]`)
4. Poll `/predictions/` PLURAL — WRONG (must be `/prediction/` SINGULAR)
5. Decode base64 — WRONG (outputs are IMAGE URLs to download)

### ✅ ADDED: Proven Whop Helpers

Copied EXACT working code from your Mac test:

```python
_atlas_key()      # Accept ATLASCLOUD_KEY or ATLASCLOUD_API_KEY
_atlas_headers()  # Bearer auth
_atlas_create(prompt) → prediction_id
_atlas_poll(prediction_id) → image_url
_download(url, dest)
_generate_atlas_image(prompt, output_path)
```

**Request shape** (exactly as Mac):
```python
{
  "model": "openai/gpt-image-2/text-to-image",
  "prompt": prompt[:5000],
  "size": "1536x864",
  "quality": "medium",
  "output_format": "jpeg",
  "enable_sync_mode": False,
  "enable_base64_output": False,
}
```

**Response shape**: `{code: 200, data: {id: "pred_xxx"}}`  
**Poll URL**: `/api/v1/model/prediction/{id}` (singular)  
**Success**: `{data: {status: "succeeded", outputs: ["https://...jpg"]}}`  
**Download**: JPEG bytes from URL

---

## Verification on This VM

✅ **Atlas TTS worked**:
```
[voiceover] Atlas xAI TTS — 1 chunk(s), voice=leo, total_chars=563
[voiceover] Chunk 1/1 (563 chars)...
[voiceover] Polling prediction a6b917a4251e40c198256b2e28f23e4a (budget 45s, 563 chars)...
[voiceover] Chunk 1/1 done
[voiceover] Complete via Atlas: /workspace/artifacts/voiceover.wav (1777526 bytes)
✓ Voiceover: /workspace/artifacts/voiceover.wav
```

❌ **Full smoke blocked** by Whisper `av.open()` env issue (not Frontier code bug):
```
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

This is a faster-whisper/av compatibility issue in this VM environment. Not related to our Atlas/Pexels/Frontier code.

---

## Commits Pushed to PR #2

```
e531bbe test: Add real Frontier smoke test with Atlas + Pexels
c509e49 fix(frontier): Replace broken Atlas with PROVEN Whop path (Mac 8/8)
66c0539 fix(frontier): Port Whop Atlas create→poll + fix SentenceTimestamp dataclass
5fd1e7d docs: Add Mac smoke test critical fixes summary
```

---

## Unit Tests Updated

All tests now mock the PROVEN flow:

1. ✅ `test_atlas_create_poll_download_success` - Full create→poll→download
2. ✅ `test_atlas_poll_processing_then_success` - Processing state transition
3. ✅ `test_atlas_prediction_failed` - Failed status handling
4. ✅ `test_atlas_create_missing_data_id` - Missing data.id error
5. ✅ `test_atlas_no_outputs` - Empty outputs error

---

## Files Changed

### core/frontier_atlas.py
- ✅ Added `_atlas_key()`, `_atlas_headers()`, `_atlas_create()`, `_atlas_poll()`, `_download()`
- ✅ Replaced `_generate_atlas_image()` with proven Whop path
- ✅ Uses env vars: `ATLAS_IMAGE_MODEL`, `ATLAS_IMAGE_SIZE`, `ATLAS_IMAGE_QUALITY`
- ✅ Polls `/api/v1/model/prediction/{id}` (singular)
- ✅ Downloads JPEG from `data.outputs[0]` URL

### core/frontier_pipeline.py
- ✅ Fixed SentenceTimestamp dataclass access (was using `.get()` on dataclass)
- ✅ Changed `sent_time.get("text")` → `sent_time.text`
- ✅ Changed `best_match["start"]` → `best_match.start_sec`
- ✅ Changed `best_match["end"]` → `best_match.end_sec`

### tests/test_frontier_atlas_b64.py
- ✅ Updated all tests to match proven create→poll→download flow
- ✅ Mock responses: `{code: 200, data: {id: "..."}}`
- ✅ Mock poll: `{data: {status: "succeeded", outputs: ["https://..."]}}`
- ✅ Mock download: JPEG bytes from URL

### scripts/frontier_smoke_real.py
- ✅ New smoke test using runtime_keys.txt
- ✅ Generates voiceover with Atlas TTS (✓ verified working)
- ✅ Runs Frontier pipeline with 6 stills + Pexels
- ✅ Extracts frame grabs for visual comparison

---

## Ready For Your Mac Smoke

**What to test**:
1. Pull latest PR #2: `git pull origin cursor/frontier-admin-recipe-e6f9`
2. Load runtime_keys.txt env vars
3. Run Mac smoke with real script + VO
4. Verify: 6-8 Atlas stills generate (8/8 should succeed now)
5. Verify: Pexels videos/photos fetch
6. Verify: Full video assembles with captions/grade/dust/vignette
7. Extract frame grabs
8. Score against Jung reference using `scripts/frontier_output_scorecard.md`

**Expected**: Atlas stills generate successfully. No more `No image URL` or `data.id` errors.

---

## PR #2 Status

- ✅ **Proven Atlas path** pushed
- ✅ **SentenceTimestamp fix** pushed
- ✅ **Unit tests** updated and passing
- ✅ **Dust overlay** asset present (32MB, 10s loop)
- ✅ **Kinetic captions** hardened
- ✅ **Output scorecard** template ready
- ✅ **PR kept as draft** until visual ≥9.5/10

**Awaiting**: Your Mac smoke results + frame grabs for final visual scoring.

