# Quick Deployment Checklist - New Pricing

PR: https://github.com/keviscoding/headstartvideos/pull/31

---

## Pre-Deployment: Create Stripe Prices (5 min)

### Stripe Dashboard Method

1. Go to https://dashboard.stripe.com/products
2. Open **Channel Recipe Starter** product
   - Add price: **$19.00 USD** recurring **monthly** → copy `price_...` ID
   - Add price: **$190.00 USD** recurring **yearly** → copy `price_...` ID
3. Open **Channel Recipe Daily** product
   - Add price: **$79.00 USD** recurring **monthly** → copy `price_...` ID
   - Add price: **$790.00 USD** recurring **yearly** → copy `price_...` ID

You now have 4 new Price IDs. Keep them ready.

---

## Step 1: Backup Current Price IDs (2 min)

**CRITICAL**: Before changing anything, copy your CURRENT DigitalOcean env values:

Go to: DigitalOcean App Platform → channelrecipe → web → Environment

Copy these 4 current values:
```
STRIPE_PRICE_STARTER_MONTHLY = price_____________  (current $27)
STRIPE_PRICE_STARTER_ANNUAL = price______________  (current $270)
STRIPE_PRICE_DAILY_MONTHLY = price_______________  (current $49)
STRIPE_PRICE_DAILY_ANNUAL = price________________  (current $490)
```

Paste them into a text file. These become your V1 values.

---

## Step 2: Update DigitalOcean Environment (5 min)

In DigitalOcean App Platform → channelrecipe → web → Environment:

### Update existing variables (point to NEW prices):
```
STRIPE_PRICE_STARTER_MONTHLY = price_NEW_19_MONTHLY
STRIPE_PRICE_STARTER_ANNUAL = price_NEW_190_ANNUAL
STRIPE_PRICE_DAILY_MONTHLY = price_NEW_79_MONTHLY
STRIPE_PRICE_DAILY_ANNUAL = price_NEW_790_ANNUAL
```

### Add NEW variables (grandfathered OLD prices):
```
STRIPE_PRICE_STARTER_MONTHLY_V1 = price_OLD_27_MONTHLY  (from Step 1)
STRIPE_PRICE_STARTER_ANNUAL_V1 = price_OLD_270_ANNUAL  (from Step 1)
STRIPE_PRICE_DAILY_MONTHLY_V1 = price_OLD_49_MONTHLY   (from Step 1)
STRIPE_PRICE_DAILY_ANNUAL_V1 = price_OLD_490_ANNUAL    (from Step 1)
```

Click "Save" → DigitalOcean will restart the app automatically.

---

## Step 3: Merge PR (1 min)

Merge PR #31: https://github.com/keviscoding/headstartvideos/pull/31

---

## Step 4: Deploy (automatic)

DigitalOcean deploys automatically when:
- PR merged to main
- Environment variables changed

Wait for deploy to complete (~5-10 min).

---

## Step 5: Verify (5 min)

### Check 1: New signup sees new pricing
1. Open channelrecipe.com in incognito
2. Click "Sign Up" or view pricing
3. Verify displays: **$19/mo** (Starter) and **$79/mo** (Daily)

### Check 2: Webhook logs (first renewal)
1. Wait for first existing subscriber renewal (or use Stripe test clock)
2. Check app logs for `invoice.paid` webhook
3. Verify logs show: "user X renewed → starter (15 credits)" or "daily (35 credits)"
4. Confirm existing $27/$49 subscribers still renewing correctly

### Check 3: Stripe Dashboard
1. Go to Stripe Dashboard → Customers
2. Find a new signup (after deploy)
3. Verify subscription uses NEW Price ID (price_NEW_...)
4. Find an existing subscriber
5. Verify subscription still uses OLD Price ID (price_OLD_...)

---

## Success Criteria

✅ New signups see $19/$79 in UI  
✅ New subscriptions created with NEW Price IDs  
✅ Existing $27/$49 subscribers renew successfully  
✅ Both old and new Price IDs grant correct credits (15/35)  
✅ No webhook errors in logs  

---

## Rollback (if needed)

If something goes wrong:

1. In DigitalOcean env vars, revert:
   - `STRIPE_PRICE_STARTER_MONTHLY` = `STRIPE_PRICE_STARTER_MONTHLY_V1` value
   - `STRIPE_PRICE_STARTER_ANNUAL` = `STRIPE_PRICE_STARTER_ANNUAL_V1` value
   - `STRIPE_PRICE_DAILY_MONTHLY` = `STRIPE_PRICE_DAILY_MONTHLY_V1` value
   - `STRIPE_PRICE_DAILY_ANNUAL` = `STRIPE_PRICE_DAILY_ANNUAL_V1` value
2. Save → app restarts
3. New signups see $27/$49 again

**Note**: Rollback does NOT affect existing subscriptions - they continue unchanged.

---

## Support / Monitoring

### First 48 Hours

Watch for:
- Stripe webhook failures (check app logs)
- Customer support tickets about pricing
- Failed renewals (should be zero)
- Failed new signups (should be zero)

### Common Questions

**Q**: "Why am I paying $27 but the site shows $19?"  
**A**: "You're on our original pricing plan from when you signed up. Your rate is locked in - you'll never pay more unless you upgrade."

**Q**: "Can I switch to the new $19 plan?"  
**A**: (Policy decision - but technically: no, old plans can't downgrade to new pricing without manual Stripe admin work)

---

## Technical Details

Full docs:
- [PRICING_MIGRATION_PLAN.md](PRICING_MIGRATION_PLAN.md) - Complete technical guide
- [create_stripe_prices.md](create_stripe_prices.md) - Stripe setup details
- Run `./validate_pricing_changes.sh` to verify code changes

Questions? Check the PR description or PRICING_MIGRATION_PLAN.md.

---

**Total Time**: ~20 minutes (5 min Stripe + 5 min env vars + 10 min deploy)

**Risk**: Very low - existing subscribers automatically grandfathered, no migration code, no refunds.
