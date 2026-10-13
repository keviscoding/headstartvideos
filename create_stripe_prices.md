# Creating New Stripe Prices for $19/$79 Pricing

## Option 1: Stripe Dashboard (Recommended)

### Step 1: Find Your Product IDs

1. Go to https://dashboard.stripe.com/products
2. Find your **Channel Recipe Starter** product - note its ID (starts with `prod_`)
3. Find your **Channel Recipe Daily** product - note its ID (starts with `prod_`)

### Step 2: Create New Starter Prices

**Starter Monthly ($19):**
1. Open Channel Recipe Starter product
2. Click "Add another price"
3. Set: `$19.00 USD` recurring `monthly`
4. Click "Add price"
5. Copy the new price ID (starts with `price_`) → This is your **STRIPE_PRICE_STARTER_MONTHLY**

**Starter Annual ($190):**
1. Still in Channel Recipe Starter product
2. Click "Add another price"
3. Set: `$190.00 USD` recurring `yearly`
4. Click "Add price"
5. Copy the new price ID → This is your **STRIPE_PRICE_STARTER_ANNUAL**

### Step 3: Create New Daily Prices

**Daily Monthly ($79):**
1. Open Channel Recipe Daily product
2. Click "Add another price"
3. Set: `$79.00 USD` recurring `monthly`
4. Click "Add price"
5. Copy the new price ID → This is your **STRIPE_PRICE_DAILY_MONTHLY**

**Daily Annual ($790):**
1. Still in Channel Recipe Daily product
2. Click "Add another price"
3. Set: `$790.00 USD` recurring `yearly`
4. Click "Add price"
5. Copy the new price ID → This is your **STRIPE_PRICE_DAILY_ANNUAL**

### Step 4: Save Current Price IDs (for grandfathering)

Before updating your environment variables, **record your CURRENT price IDs**:

1. Go to your DigitalOcean App Platform → channelrecipe web → Environment
2. Copy the CURRENT values:
   - `STRIPE_PRICE_STARTER_MONTHLY` (currently pointing to $27 price)
   - `STRIPE_PRICE_STARTER_ANNUAL` (currently pointing to $270 price)
   - `STRIPE_PRICE_DAILY_MONTHLY` (currently pointing to $49 price)
   - `STRIPE_PRICE_DAILY_ANNUAL` (currently pointing to $490 price)

These will become your V1 (grandfathered) price IDs.

---

## Option 2: Stripe API (if you have API access)

```bash
# Set your Stripe secret key
export STRIPE_SECRET_KEY="sk_live_..."

# Get your product IDs first
curl https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:" \
  -G \
  -d "active=true" \
  -d "limit=100"
# Find prod_xxx for Starter and Daily

# Create Starter Monthly $19
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d "product=prod_STARTER_PRODUCT_ID" \
  -d "unit_amount=1900" \
  -d "currency=usd" \
  -d "recurring[interval]=month" \
  -d "nickname=Starter Monthly v2 ($19)"

# Create Starter Annual $190
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d "product=prod_STARTER_PRODUCT_ID" \
  -d "unit_amount=19000" \
  -d "currency=usd" \
  -d "recurring[interval]=year" \
  -d "nickname=Starter Annual v2 ($190)"

# Create Daily Monthly $79
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d "product=prod_DAILY_PRODUCT_ID" \
  -d "unit_amount=7900" \
  -d "currency=usd" \
  -d "recurring[interval]=month" \
  -d "nickname=Daily Monthly v2 ($79)"

# Create Daily Annual $790
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d "product=prod_DAILY_PRODUCT_ID" \
  -d "unit_amount=79000" \
  -d "currency=usd" \
  -d "recurring[interval]=year" \
  -d "nickname=Daily Annual v2 ($790)"
```

Each command returns JSON with an `id` field - that's your new price ID.

---

## Step 5: Update DigitalOcean Environment Variables

In DigitalOcean App Platform → channelrecipe web → Environment:

### Update existing variables (point to NEW prices):
```
STRIPE_PRICE_STARTER_MONTHLY = price_NEW_19_MONTHLY
STRIPE_PRICE_STARTER_ANNUAL = price_NEW_190_ANNUAL
STRIPE_PRICE_DAILY_MONTHLY = price_NEW_79_MONTHLY
STRIPE_PRICE_DAILY_ANNUAL = price_NEW_790_ANNUAL
```

### Add NEW variables (grandfathered OLD prices):
```
STRIPE_PRICE_STARTER_MONTHLY_V1 = price_OLD_27_MONTHLY
STRIPE_PRICE_STARTER_ANNUAL_V1 = price_OLD_270_ANNUAL
STRIPE_PRICE_DAILY_MONTHLY_V1 = price_OLD_49_MONTHLY
STRIPE_PRICE_DAILY_ANNUAL_V1 = price_OLD_490_ANNUAL
```

**Critical:** The V1 variables should contain the OLD price IDs you copied in Step 4.

---

## Step 6: Deploy

1. Merge this PR
2. Deploy to production
3. Restart the app (DigitalOcean will do this automatically when env vars change)

---

## Verification Checklist

After deployment:

- [ ] New signups see $19/$79 pricing in the app
- [ ] Checkout creates subscriptions with the NEW price IDs
- [ ] Existing $27/$49 subscribers still see their original pricing in Stripe portal
- [ ] Existing subscriber renewals still work and grant correct credits (15/35 monthly, 180/420 annual)
- [ ] Portal upgrades work (Starter → Daily) with correct prorations
- [ ] Webhook handlers process both old and new price IDs correctly

---

## What Happens to Existing Subscribers?

**Nothing.** They are automatically grandfathered because:

1. Their Stripe subscriptions reference the OLD price IDs
2. Those OLD price IDs remain active in Stripe (we never archive them)
3. Our webhook handlers now recognize BOTH old and new price IDs
4. Stripe portal shows them their CURRENT subscription options only
5. They continue paying $27/$49 until they cancel

New customers cannot select the old pricing - it's not exposed in checkout.

---

## Rollback Plan

If you need to revert to $27/$49 for new signups:

1. In DigitalOcean, swap the env vars back:
   - `STRIPE_PRICE_STARTER_MONTHLY` → point to V1 (old) price ID
   - etc.
2. Restart the app
3. New signups will see $27/$49 again
4. No code changes needed - grandfathering works both ways

---

## Testing with Stripe Test Mode

Before touching production:

1. Create test prices in Stripe test mode at $19/$79
2. Set test mode API keys in your local .env
3. Test new signup flow
4. Test webhook handling with `stripe trigger invoice.paid`
5. Verify both old and new test price IDs grant correct credits
