# Testing Frontier Recipe on channelrecipe.com

## Quick Start (Admin Testing)

1. **Sign in** to channelrecipe.com with admin account (email in `ADMIN_EMAILS`)
2. Navigate to **recipe picker**
3. **Select "Frontier"** from the long-form recipes
4. Generate script + voiceover as usual
5. Click **"Cook Video"**
6. Wait for cook to complete (~10-20 min depending on length)
7. Check History for completed video

## What Frontier Does

- **Input**: Script + voiceover
- **Process**:
  1. Word-level audio alignment (faster-whisper)
  2. LLM concept segmentation (splits script into visual concepts)
  3. AI still generation (Atlas/ERNIE for each concept)
  4. Ken Burns effects (zoom/pan on stills)
  5. Assembly with voiceover + captions
- **Output**: 16:9 video in History

## Admin-Only Access

### ✅ Admin users see:
- "Frontier" card in recipe picker (long-form section)
- Can queue Frontier cooks
- Frontier videos in History with "Frontier" label

### ❌ Non-admin users:
- Do NOT see Frontier in picker (filtered server-side)
- Get 403 error if attempting to cook via API:
  ```
  "The Frontier recipe is not yet available. Check back soon or try another recipe."
  ```

## Infrastructure

### Fly Machines (same as other recipes)
- Cook runs on ephemeral Fly Machine via `fly_bridge.py` → `fly_oneshot.py`
- Machine auto-destroys after cook completes
- Check logs: `fly logs -a channelrecipe-cook`

### Cost
- 10 pence/min (between broll_only at 5p/min and broll_cinematic at 12p/min)
- Typical 8-min video = 80 pence = ~$1 USD in API spend

## Troubleshooting

### "Recipe not found" or 403 error
- Verify admin email is in `ADMIN_EMAILS` config
- Check `/api/me` or user profile shows admin status

### Cook fails during image generation
- Check Atlas API key is configured (`ATLASCLOUD_KEY`)
- Verify ERNIE model is available in Atlas dashboard
- Check Fly logs for specific errors

### Video has no images
- Ensure `core/illustration_gen` modules are deployed
- Check that `core/frontier_pipeline.py` is in Fly cook image
- Rebuild Fly cook image: `fly deploy -c fly.cook.toml --ha=false`

### Cook hangs or times out
- Default timeout is ~20 min for long scripts
- Check Fly Machine didn't run out of memory (4GB default)
- Verify Spaces upload works (Fly must have SPACES_* env vars)

## After Testing

When ready to open Frontier to all users:
1. Remove `"admin_only": True` from `core/recipes.py`
2. Optionally update `webapp/niches/frontier.json` status to "proven"
3. Add preview GIF: `/static/previews/frontier.gif`
4. Deploy and test with trial user account

## Config Reference

### Admin Emails (set in production config)
```bash
ADMIN_EMAILS=["kevis@example.com", "admin@example.com"]
```

### Required API Keys (already configured)
- `ATLASCLOUD_KEY`: AI image generation + LLM
- `PEXELS_KEY`: (optional) Stock footage fallback
- `SPACES_KEY`, `SPACES_SECRET`, `SPACES_BUCKET`: Video upload

### Fly Cook Settings
- App: `channelrecipe-cook`
- Region: `sjc` (default)
- Memory: 4GB
- CPUs: 2 shared

## Questions?

Check the PR description for full implementation details:
https://github.com/keviscoding/headstartvideos/pull/1
