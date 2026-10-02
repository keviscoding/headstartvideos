# Mac Smoke Test Critical Fixes - Oct 2, 2026 11:28 AM

## Live Smoke Test Results

User ran Mac smoke on PR branch `cursor/frontier-admin-recipe-e6f9` with real ATLASCLOUD_KEY + PEXELS_KEY.

### ✅ What Worked
- Pexels: 24 videos + 12 photos fetched successfully

### ❌ Critical Bugs Found

## Bug #1: Atlas URL-Only Parser Failed

**Symptom**: All 8 stills failed with "No image URL in response" after 3 retries.

**Root Cause**: Using wrong Atlas API endpoint and response format.
- Was using: `/v1/images/generations` (OpenAI-style, expecting `data[0].url`)
- Should use: Whop Frontier's `/api/v1/model/generateImage` create→poll pattern

**User Confirmation**: After patching locally to Whop create→poll with b64 fallback, **8/8 stills succeeded**.

## Bug #2: SentenceTimestamp Dataclass Mishandling

**Symptom**: Crash in Step 5 (motion planning) with:
```
AttributeError: 'SentenceTimestamp' object has no attribute 'get'
```

**Root Cause**: Code treated `SentenceTimestamp` as dict when it's a dataclass.

```python
# WRONG (frontier_pipeline.py lines 182-196)
sent_text = sent_time.get("text", "").lower()      # ❌ .get() on dataclass
best_match["start"]                                  # ❌ wrong key name
best_match["end"]                                    # ❌ wrong key name

# CORRECT
sent_text = sent_time.text.lower()                  # ✅ dataclass attribute
best_match.start_sec                                # ✅ correct attribute
best_match.end_sec                                  # ✅ correct attribute
```

`SentenceTimestamp` definition (core/segmenter.py):
```python
@dataclass
class SentenceTimestamp:
    text: str
    start_sec: float
    end_sec: float
    word_timestamps: list[dict] = field(default_factory=list)
```

---

## Fixes Applied

### Fix #1: Port Whop Frontier Atlas Pattern (core/frontier_atlas.py)

Replaced direct `/v1/images/generations` call with Whop's 3-step flow:

1. **Create Prediction**
   ```python
   POST https://api.atlascloud.ai/api/v1/model/generateImage
   → {"id": "pred_12345"}
   ```

2. **Poll Until Complete**
   ```python
   GET https://api.atlascloud.ai/api/v1/model/predictions/{id}
   → {"status": "processing"} (repeat)
   → {"status": "succeeded", "output": "<base64_png>"}
   ```

3. **Extract Base64 PNG**
   ```python
   if isinstance(output, str):
       png_bytes = base64.b64decode(output)
   elif isinstance(output, list):
       png_bytes = base64.b64decode(output[0])
   ```

**Polling Parameters**:
- Poll timeout: 120 seconds
- Poll interval: 2 seconds
- Retry logic: 3 attempts with exponential backoff

### Fix #2: SentenceTimestamp Dataclass Access (core/frontier_pipeline.py)

Changed lines 182-196 to use dataclass attributes:

```python
# Correct dataclass attribute access
for sent_idx, sent_time in enumerate(sentence_times):
    sent_text = sent_time.text.lower() if hasattr(sent_time, 'text') else ""
    # ...

if best_match:
    ai_stills.append({
        "path": result["path"],
        "start_sec": best_match.start_sec,   # Not best_match["start"]
        "end_sec": best_match.end_sec,       # Not best_match["end"]
        "text": scene_text,
    })
```

---

## Unit Tests Updated

Updated `tests/test_frontier_atlas_b64.py` to match create→poll pattern:

1. ✅ `test_atlas_image_create_poll_success` - Full flow with processing→succeeded
2. ✅ `test_atlas_image_output_as_list` - Handle array output format
3. ✅ `test_atlas_image_prediction_failed` - Error handling for failed predictions
4. ✅ `test_atlas_image_no_prediction_id` - Invalid create response

---

## Git History

```
66c0539 fix(frontier): Port Whop Atlas create→poll + fix SentenceTimestamp dataclass
9cac6f4 docs: Add Atlas b64_json bug fix summary
42c4553 fix(frontier): Handle Atlas b64_json response format
05754b2 docs(assets): Add README explaining dust overlay generation
e60af9b feat(frontier): Add dust overlay, harden kinetic captions, add output scorecard
```

---

## Status

✅ **BOTH FIXES PUSHED** to PR #2 (`cursor/frontier-admin-recipe-e6f9`)

🔄 **User is re-running Mac smoke** with these exact fixes applied locally.

Expected outcome:
- ✅ Atlas: 8/8 stills generate successfully
- ✅ Pexels: 24 videos + 12 photos (already working)
- ✅ Motion planning: No SentenceTimestamp crash
- ✅ Full video assembly completes

Awaiting user's smoke results + frame grabs for visual scoring.

---

## Next Steps

1. User completes Mac smoke test rerun
2. Smoke succeeds → full 60-90s video with real Atlas stills + Pexels
3. User provides:
   - Smoke MP4 path
   - 4 frame grabs from output
   - 4 frame grabs from Jung reference
4. Score output using `scripts/frontier_output_scorecard.md`
5. Iterate fixes until visual ≥9.5/10
6. Mark PR #2 ready for review
