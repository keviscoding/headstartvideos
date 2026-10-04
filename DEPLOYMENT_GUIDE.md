# Deployment Guide: Avatar Generator Recipe Fix

## PR Information
- **Branch**: `cursor/fix-avatar-gen-recipe-5279`
- **PR**: [#14](https://github.com/keviscoding/headstartvideos/pull/14)
- **Status**: Ready for review and merge

## What Changed
Single line added to `webapp/server.py:2689`:
```python
"recipe": "avatar_generator",
```

This ensures avatar generation jobs route to `run_avatar_gen_pipeline` instead of defaulting to `run_explainer_pipeline`.

## Testing Completed
✅ All tests passing:
- `tests/test_avatar_gen_recipe.py` - Unit tests for recipe propagation
- `tests/test_avatar_gen_integration.py` - End-to-end integration test

Run tests:
```bash
python3 tests/test_avatar_gen_recipe.py
python3 tests/test_avatar_gen_integration.py
```

## Deployment Steps

### 1. Merge the PR
```bash
# Review and merge PR #14 on GitHub
# Or via gh CLI:
gh pr review 14 --approve
gh pr merge 14 --squash
```

### 2. Deploy Web Application
The web dyno needs the updated `server.py` to include recipe in request_json.

```bash
# If using Heroku/similar
git push heroku main

# If using fly.io for web
fly deploy

# Or however your web deployment works
```

### 3. Rebuild Fly Cook Workers
Cook workers need the same code to process jobs correctly.

```bash
# Deploy to Fly cook machines
fly deploy -c fly.cook.toml --ha=false

# After successful deploy, clean up any leftover always-on machines
fly machines list -a channelrecipe-cook
# If any "app" process group machines exist (shouldn't after --ha=false):
fly machines destroy <machine-id> -a channelrecipe-cook
```

### 4. Verification in Production

#### 4.1 Create Test Avatar Job
Make a test request to `/api/avatar-gen/generate`:
```bash
curl -X POST https://channelrecipe.com/api/avatar-gen/generate \
  -H "Authorization: Bearer $TOKEN" \
  -F "script=Test script for avatar generation" \
  -F "title=Test Avatar" \
  -F "avatar_source=prompt" \
  -F "avatar_prompt=A professional avatar" \
  -F "target_minutes=2.0"
```

#### 4.2 Check Database
```sql
-- Get the latest avatar_generator job
SELECT job_id, recipe, request_json
FROM cook_jobs
WHERE recipe = 'avatar_generator'
ORDER BY created_at DESC
LIMIT 1;
```

Verify:
- `recipe` column = `"avatar_generator"` ✓
- `request_json` contains `"recipe":"avatar_generator"` ✓

#### 4.3 Monitor Cook Logs
Watch the cook logs for the job:
```bash
# If using Fly logs
fly logs -a channelrecipe-cook
```

Look for:
- ✓ "AI Avatar Generator" progress messages (not "Generating animated explainer")
- ✓ `recipe=avatar_generator` in logs (not `animated_explainer`)

#### 4.4 Check Sentry
In Sentry, find the cook job event and verify:
- Tag: `recipe=avatar_generator` (not `animated_explainer`)
- Context shows correct pipeline execution

#### 4.5 Verify Result
Once the job completes:
- Check the output video matches avatar_gen_pipeline style
- Should have speaking avatar with B-roll, not animated explainer style
- Result should use avatar_gen_pipeline timing/composition

## Rollback Plan
If issues arise, the fix is minimal and safe to revert:

```bash
# Revert the commit
git revert HEAD

# Redeploy
git push heroku main  # or fly deploy
fly deploy -c fly.cook.toml --ha=false
```

The revert removes the `"recipe"` line from request_data, causing avatar jobs to default back to animated_explainer (original broken behavior).

## Impact Summary

### Fixed
- ✅ Avatar generator jobs now execute correct pipeline
- ✅ Matches user expectations (avatar video, not animated explainer)

### No Breaking Changes
- ✅ Existing recipes (explainer, broll, storyboard, etc.) unchanged
- ✅ Database schema unchanged
- ✅ API contracts unchanged
- ✅ No migration needed

### Performance
- No performance impact (single dict key addition)
- No new dependencies
- No database queries added

## Post-Deployment Monitoring

### First 24 Hours
Monitor:
1. **Sentry**: Watch for new errors in cook_runner
2. **Cook success rate**: Should remain stable or improve
3. **Avatar job completions**: Verify they produce avatar-style videos
4. **User reports**: Check for feedback on avatar quality

### Metrics to Track
- Avatar job success rate (should improve from 0% to normal)
- Average cook time for avatar jobs
- Error rate in avatar_gen_pipeline
- Customer satisfaction with avatar outputs

## Known Limitations

### Current Implementation
- Recipe must be in both DB column AND request_json
  - Column used for queries/filtering
  - JSON used for cook execution
  - This is consistent with all other recipes

### Future Improvements (Optional)
1. Refactor to read recipe from top-level job key instead of nested in request
2. Add DB constraint to enforce recipe in request_json matches column
3. Add monitoring alert for recipe mismatches

## Support

### If Avatar Jobs Still Run Wrong Pipeline
1. Check web deployment completed (server.py has the fix)
2. Check cook worker deployment completed (same commit)
3. Verify request_json in DB contains `"recipe":"avatar_generator"`
4. Check Sentry tags show `recipe=avatar_generator`
5. Review cook logs for which pipeline was called

### If New Issues Arise
1. Check Sentry for stack traces
2. Compare with previous successful avatar jobs (if any)
3. Verify all other recipe types still work correctly
4. Check database for malformed request_json

## Additional Resources

- `AVATAR_GEN_FIX_VERIFICATION.md` - Detailed problem analysis
- `FLOW_DIAGRAM.txt` - Visual before/after code paths
- `tests/test_avatar_gen_recipe.py` - Unit tests
- `tests/test_avatar_gen_integration.py` - Integration test
- PR #14 - Full code review and discussion

## Questions?

This is a minimal, surgical fix that aligns avatar_gen with how all other recipes work. The change is:
- Small (1 line)
- Safe (no side effects)
- Tested (unit + integration tests)
- Proven (matches working /api/build pattern)

Risk level: **Low** ✅
