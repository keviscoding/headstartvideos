# Frontier Recipe - Production Deployment Checklist

## ✅ PR Status: MERGED TO MAIN

**Merge Commit**: 31e6ba9
**PR**: https://github.com/keviscoding/headstartvideos/pull/1
**Branch**: `cursor/frontier-admin-recipe-e6f9` → `main`

---

## 🚀 DEPLOYMENT STEPS (Required for Production)

### Step 1: Deploy Webapp (DigitalOcean / Web Server)

```bash
# SSH into DigitalOcean droplet or web server
cd /path/to/headstartvideos
git pull origin main
# Should show merge commit 31e6ba9

# Restart web service (depends on your setup)
# Example for systemd:
sudo systemctl restart channelrecipe-web

# Or if using supervisor/PM2/other:
# sudo supervisorctl restart channelrecipe
# pm2 restart channelrecipe
```

**Verify**: Check that webapp is running and responding on channelrecipe.com

---

### Step 2: Rebuild Fly Cook Image (CRITICAL)

The cook image must be rebuilt to include `core/frontier_pipeline.py`.

```bash
# From your local machine or CI/CD
cd /path/to/headstartvideos
git checkout main
git pull

# Deploy cook image (do NOT start an always-on VM)
fly deploy -c fly.cook.toml --ha=false

# Expected output:
# - Building image...
# - Pushing image...
# - Image deployed successfully
# - No machines running (scale-to-zero)
```

**Verify**: Run `fly image show -a channelrecipe-cook` and confirm image is recent

---

### Step 3: Clean Up Leftover Machines

Check for and destroy any leftover "app" process-group machines:

```bash
# List all machines
fly machines list -a channelrecipe-cook

# If any machines exist (should be none for scale-to-zero):
# Look for process_group="app" (always-on VMs)
# Destroy them:
fly machine destroy <machine-id> -a channelrecipe-cook --force

# Repeat for each leftover machine
```

**Expected result**: `fly machines list` should show "No machines found" or only recent one-shot cook machines

---

### Step 4: Verify Admin Access

1. Go to https://channelrecipe.com
2. Sign in as **nwalikelv@gmail.com** (admin email)
3. Navigate to recipe picker
4. **Verify**: "Frontier" appears in long-form recipe list

**If Frontier doesn't appear:**
- Check webapp logs for errors
- Verify `ADMIN_EMAILS` env var includes `nwalikelv@gmail.com`
- Clear browser cache and retry

---

### Step 5: Test Non-Admin User

1. Sign out
2. Sign in as a different (non-admin) email
3. Navigate to recipe picker
4. **Verify**: "Frontier" does NOT appear
5. Try direct API call (if testing):
   ```bash
   curl -X POST https://channelrecipe.com/api/build \
     -H "Content-Type: application/json" \
     -d '{"recipe": "frontier", "script": "test", "voiceover_path": "/dev/null"}' \
     -b "session=<your-non-admin-session-cookie>"
   
   # Expected: 403 Forbidden
   ```

---

## 🧪 ADMIN TEST COOK (Optional but Recommended)

Once deployed, test a full cook cycle:

1. Sign in as **nwalikelv@gmail.com**
2. Select "Frontier" recipe
3. Generate a short script (~1-2 min, ~150-300 words)
4. Generate voiceover (or upload existing)
5. Click "Cook Video"
6. Monitor progress:
   - Check webapp UI for progress updates
   - Check Fly logs: `fly logs -a channelrecipe-cook`
7. Wait for completion (~5-10 min for short video)
8. Verify video appears in History with "Frontier" label
9. Download and review video quality

**Expected result**: Video with AI stills + voiceover, assembled in slideshow style

---

## 📋 Post-Deployment Verification Checklist

- [ ] Webapp is running on latest `main` (commit 31e6ba9)
- [ ] Fly cook image rebuilt and deployed (`fly deploy -c fly.cook.toml --ha=false`)
- [ ] No leftover always-on machines (`fly machines list` shows none)
- [ ] Admin user (nwalikelv@gmail.com) sees "Frontier" in picker
- [ ] Non-admin users do NOT see "Frontier"
- [ ] Non-admin API attempts return 403
- [ ] (Optional) Test cook completes successfully

---

## 🔧 Rollback Plan (If Issues Arise)

If Frontier causes problems:

### Quick disable (keep code, hide from all users)
```bash
# Edit core/recipes.py:
# Change frontier's "admin_only": True to "admin_only": False
# AND set "available": False in webapp/niches/frontier.json
git commit -am "Temporarily disable Frontier"
git push origin main
# Redeploy webapp
```

### Full rollback
```bash
git revert 31e6ba9
git push origin main
# Redeploy webapp and Fly cook image
```

---

## 🎯 Success Criteria

Deployment is successful when:
1. ✅ Admin (nwalikelv@gmail.com) can see and select Frontier recipe
2. ✅ Non-admin users cannot see Frontier (filtered in UI)
3. ✅ Non-admin API attempts return 403 Forbidden
4. ✅ Fly cook image includes `core/frontier_pipeline.py`
5. ✅ (Optional) Test cook completes successfully with valid output

---

## 📞 Support

If deployment fails or tests don't pass:
- Check logs: `fly logs -a channelrecipe-cook`
- Check webapp logs on DigitalOcean
- Review TESTING_FRONTIER.md for troubleshooting
- Check PR discussion: https://github.com/keviscoding/headstartvideos/pull/1

---

## 📝 Next Steps After Successful Deploy

1. **Admin testing**: Generate 2-3 test videos with different lengths
2. **Monitor costs**: Track Atlas API spend for image generation
3. **Tune parameters**: Adjust cost estimates if needed
4. **Feedback**: Document any issues or improvements
5. **Public release**: When ready, remove `admin_only` flag

---

**Deploy Status**: ⏳ AWAITING PRODUCTION DEPLOYMENT

Update this document after each step is completed.
