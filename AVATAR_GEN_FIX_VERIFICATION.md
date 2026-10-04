# Avatar Generator Recipe Fix - Verification Summary

## Problem Analysis

### Symptom
POST `/api/avatar-gen/generate` creates cook jobs with `recipe=avatar_generator` in the database, but Fly cook workers execute `run_explainer_pipeline` instead of `run_avatar_gen_pipeline`.

### Evidence
- Sentry logs for job `f88b34f2-89c3-45bb-a6da-28d359b3d282` show `recipe=animated_explainer` in cook context
- Database column shows `recipe=avatar_generator` 
- One-shot logs can print `recipe=avatar_generator` from the column while the pipeline ignores it

### Root Cause Confirmed

1. **Endpoint behavior** (webapp/server.py:2689-2697):
   - `avatar_gen_generate` creates request_data dict WITHOUT recipe key
   - Only stores recipe in DB column via `create_cook_job(recipe="avatar_generator")`
   
2. **Hydration gap** (webapp/cook_runner.py:35-64):
   - `hydrate_job_from_row()` copies request_json → job["request"]
   - Does NOT copy the recipe column
   
3. **Default fallback** (webapp/cook_runner.py:126):
   ```python
   recipe = (req_data.get("recipe") or "").strip() or "animated_explainer"
   ```
   - Missing recipe key defaults to "animated_explainer"
   - Calls `run_explainer_pipeline` instead of `run_avatar_gen_pipeline`

4. **Comparison with working endpoint** (/api/build):
   - Line 3634: `req_payload = req.model_dump()` includes recipe from request
   - Line 3660: JSON serializes the full payload including recipe
   - Works because recipe is IN the request_json, not just the column

## Solution

Add `"recipe": "avatar_generator"` to request_data dict before JSON serialization:

```python
request_data = {
    "recipe": "avatar_generator",  # ← FIX: Add this line
    "script": script,
    "title": title_text,
    "avatar_source": avatar_path_or_prompt,
    "reference_tags": reference_tags,
    "target_duration": target_minutes * 60.0,
    "credits_charged": credits,
    "notify_email": user.get("email") or "",
}
```

## Test Results

Created `tests/test_avatar_gen_recipe.py` to verify the fix:

```
======================================================================
Running Avatar Generator Recipe Tests
======================================================================
TEST: avatar_gen_request_includes_recipe
  ✓ PASS: recipe=avatar_generator is in request_data and survives JSON serialization

TEST: hydrate_job_preserves_recipe
  ✓ PASS: hydrate_job_from_row preserves recipe=avatar_generator in job['request']

TEST: recipe_used_in_run_cook_job
  ✓ PASS: recipe extraction logic would use avatar_generator (not animated_explainer)
  ✓ PASS: empty recipe correctly falls back to animated_explainer

======================================================================
✓ ALL TESTS PASSED
======================================================================
```

## Code Flow Verification

### Before Fix
```
POST /api/avatar-gen/generate
  ↓
request_data = {script, title, ...}  ← NO recipe key
  ↓
create_cook_job(recipe="avatar_generator", request_json=json.dumps(request_data))
  ↓ DB stores: recipe column="avatar_generator", request_json="{...no recipe...}"
  ↓
hydrate_job_from_row(row)
  ↓ job["request"] = json.loads(row["request_json"])  ← NO recipe key
  ↓
run_cook_job(job_id, job)
  ↓ recipe = req_data.get("recipe") or "animated_explainer"  ← Defaults!
  ↓
run_explainer_pipeline()  ← WRONG PIPELINE
```

### After Fix
```
POST /api/avatar-gen/generate
  ↓
request_data = {"recipe": "avatar_generator", script, title, ...}  ← HAS recipe
  ↓
create_cook_job(recipe="avatar_generator", request_json=json.dumps(request_data))
  ↓ DB stores: recipe column="avatar_generator", request_json='{"recipe":"avatar_generator",...}'
  ↓
hydrate_job_from_row(row)
  ↓ job["request"] = json.loads(row["request_json"])  ← HAS recipe key
  ↓
run_cook_job(job_id, job)
  ↓ recipe = req_data.get("recipe") = "avatar_generator"  ← Found!
  ↓
run_avatar_gen_pipeline()  ← CORRECT PIPELINE ✓
```

## Impact Analysis

### Changed
- **Avatar generator**: Now routes to `run_avatar_gen_pipeline` (lines 356-369)

### Unchanged
- **Animated explainer**: Already includes recipe in request ✓
- **B-roll recipes**: Already includes recipe in request ✓
- **Avatar + B-roll**: Already includes recipe in request ✓
- **Storyboard recipes**: Already includes recipe in request ✓
- **Ranking countdown**: Already includes recipe in request ✓
- **Frontier**: Already includes recipe in request ✓

### No Breaking Changes
- `/api/build` endpoint unchanged
- Cook runner logic unchanged
- All existing recipes continue working as before

## Deployment Checklist

1. ✅ Fix committed to branch `cursor/fix-avatar-gen-recipe-5279`
2. ✅ Tests added and passing
3. ✅ Pull request created: #14
4. ⏳ Merge PR to main
5. ⏳ Deploy web dyno (picks up server.py change)
6. ⏳ Rebuild Fly cook image with: `fly deploy -c fly.cook.toml --ha=false`
7. ⏳ Destroy any leftover always-on app process-group machines
8. ⏳ Test end-to-end: POST /api/avatar-gen/generate → verify cook runs avatar pipeline

## Verification Steps for Production

After deployment, verify:

1. Create an avatar generation job via API or UI
2. Check cook_jobs table: `recipe` column should be "avatar_generator"
3. Check cook_jobs table: `request_json` should contain `"recipe":"avatar_generator"`
4. Monitor cook logs: Should show "AI Avatar Generator" progress messages
5. Monitor Sentry: Cook context should show `recipe=avatar_generator` (not animated_explainer)
6. Wait for completion: Result should be an avatar video, not animated explainer style

## Diff Summary

**webapp/server.py** (+1 line):
```diff
     request_data = {
+        "recipe": "avatar_generator",
         "script": script,
```

**tests/test_avatar_gen_recipe.py** (+133 lines, new file):
- Comprehensive test coverage for recipe propagation
- Validates JSON serialization round-trip
- Verifies hydrate_job_from_row preserves recipe
- Tests cook_runner extraction logic

**Total change**: Minimal, focused, low-risk fix.