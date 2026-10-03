# AI Avatar Generator Deployment

## Status: ✅ Code Merged to Main

The AI avatar generator feature has been merged to `main` branch and is ready for production deployment.

**PR:** https://github.com/keviscoding/headstartvideos/pull/8

## Deploy Steps

### 1. Deploy Cook Workers (REQUIRED)

The cook workers need to be redeployed because `webapp/cook_runner.py` now handles the `avatar_generator` recipe:

```bash
fly deploy -c fly.cook.toml --ha=false
```

After deploy, check for any leftover always-on machines:
```bash
fly machines list -a channelrecipe-cook
# If any "app" process-group machines exist:
fly machine destroy <id> -a channelrecipe-cook --force
```

### 2. Deploy Web App

Deploy the main web application using your normal deployment process. The feature includes:

- New API endpoints: `/api/avatar-gen/cost` and `/api/avatar-gen/generate`
- New pipeline: `core/avatar_gen_pipeline.py`
- Integration with existing job queue and credit system

### 3. Verify Deployment

Test the API endpoints:

```bash
# Check cost calculation
curl -X POST https://channelrecipe.com/api/avatar-gen/cost \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"script": "test script here", "length_preset": "short"}'

# Should return: {"credits": 3, "target_minutes": 2.0, ...}
```

## Feature Summary

### What Users Can Do
- Paste a script and title
- Choose video length (short 2min / medium 5min / long 8min)
- Select AI avatar source (upload, URL, or prompt)
- See credit cost before generating
- Generate videos following reference channel patterns

### Credit Costs
- Short (2min): 3 credits
- Medium (5min): 6 credits
- Long (8min): 10 credits

### Technical Details
- **Avatar Generation**: Atlas Cloud `kwaivgi/kling-v2.6-std/avatar`
- **B-roll Images**: Atlas `black-forest-labs/flux-schnell`
- **Pattern Detection**: Learns from reference channel tags (face returns, timing, title cards)
- **Integration**: Uses existing recipe/cook infrastructure

## Known Limitations

1. **UI Not Yet Integrated**: The backend API is complete, but the web UI form needs to be added in a follow-up
2. **Reference Channel Import**: Tag import from reference channels not yet implemented
3. **Manual Testing Needed**: First live user test pending after deployment

## Next Steps After Deploy

1. Add UI form in main app (`webapp/static/index.html`)
2. Test with real user
3. Implement reference channel tag import
4. Refine pattern detection based on usage data

## Rollback Plan

If issues occur:
```bash
git revert 667ee43
git push origin main
fly deploy -c fly.cook.toml --ha=false
```

## Contact

Feature shipped by: Cursor AI Agent  
Date: October 3, 2026, 9:10 PM UTC  
Branch: `cursor/avatar-generator-37fc`  
Commit: `667ee43`
