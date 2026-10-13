# Channel Recipe Pricing Migration Plan

## Current State (Grandfathered Pricing)

**Starter Plan:**
- Monthly: $27/month → 15 credits/month
- Annual: $270/year → 180 credits/year (10 months billing, 2 free)

**Daily Plan:**
- Monthly: $49/month → 35 credits/month
- Annual: $490/year → 420 credits/year (10 months billing, 2 free)

## New Pricing (For NEW Signups Only)

**Starter Plan:**
- Monthly: $19/month → 15 credits/month (keep same credits)
- Annual: $190/year → 180 credits/year (10 months billing, 2 free)

**Daily Plan:**
- Monthly: $79/month → 35 credits/month (keep same credits)
- Annual: $790/year → 420 credits/year (10 months billing, 2 free)

## Implementation Strategy

### Grandfathering Mechanism

Existing subscribers stay on their current $27/$49 pricing automatically because:

1. **Existing Stripe Subscriptions reference the OLD price IDs** and will continue to do so
2. **We create NEW Stripe Price objects** for the new pricing
3. **OLD price IDs remain ACTIVE** in Stripe (never archived/deactivated)
4. **NEW checkout/signup flow points to NEW price IDs**
5. **Webhook handlers recognize BOTH old and new price IDs** and grant correct credits

### Credit Allocation (Unchanged)

Credits remain the same for both old and new pricing:
- Starter monthly/annual: 15 / 180 credits
- Daily monthly/annual: 35 / 420 credits

Upgrades, downgrades, and interval changes continue working as before.

## Required Changes

### 1. Create New Stripe Prices

In Stripe Dashboard (or via API), create 4 new Price objects on the **existing** Starter/Daily Product objects:

**New Starter Prices:**
- Monthly: $19.00 USD recurring every 1 month
  - Suggested price ID format: `price_starter_monthly_v2_19` or similar
- Annual: $190.00 USD recurring every 1 year
  - Suggested price ID format: `price_starter_annual_v2_190` or similar

**New Daily Prices:**
- Monthly: $79.00 USD recurring every 1 month
  - Suggested price ID format: `price_daily_monthly_v2_79` or similar
- Annual: $790.00 USD recurring every 1 year
  - Suggested price ID format: `price_daily_annual_v2_790` or similar

### 2. DigitalOcean Environment Variables

After creating the new Stripe Prices, update these environment variables on DigitalOcean:

```bash
# NEW PRICING (point checkout to these)
STRIPE_PRICE_STARTER_MONTHLY=price_xxxxxxxxxxxxxxxxxxxxx  # $19/mo NEW
STRIPE_PRICE_STARTER_ANNUAL=price_yyyyyyyyyyyyyyyyyyyyy  # $190/yr NEW
STRIPE_PRICE_DAILY_MONTHLY=price_zzzzzzzzzzzzzzzzzzzzz  # $79/mo NEW
STRIPE_PRICE_DAILY_ANNUAL=price_aaaaaaaaaaaaaaaaaaaaa  # $790/yr NEW

# OLD PRICING (keep these for grandfathered subs)
# Add new env vars to explicitly track the old price IDs:
STRIPE_PRICE_STARTER_MONTHLY_V1=price_xxxxxxxxxxxxxxxxxxxxx  # $27/mo OLD
STRIPE_PRICE_STARTER_ANNUAL_V1=price_yyyyyyyyyyyyyyyyyyyyy  # $270/yr OLD
STRIPE_PRICE_DAILY_MONTHLY_V1=price_zzzzzzzzzzzzzzzzzzzzz  # $49/mo OLD
STRIPE_PRICE_DAILY_ANNUAL_V1=price_aaaaaaaaaaaaaaaaaaaaa  # $490/yr OLD
```

**Critical:** Do NOT remove or change the values of the old price ID environment variables until all grandfathered subscriptions have naturally expired or been canceled.

### 3. Code Changes

#### config.py
Add new constants for old (grandfathered) price IDs:

```python
# OLD pricing (grandfathered existing subscribers)
STRIPE_PRICE_STARTER_MONTHLY_V1 = os.getenv("STRIPE_PRICE_STARTER_MONTHLY_V1", "")
STRIPE_PRICE_STARTER_ANNUAL_V1 = os.getenv("STRIPE_PRICE_STARTER_ANNUAL_V1", "")
STRIPE_PRICE_DAILY_MONTHLY_V1 = os.getenv("STRIPE_PRICE_DAILY_MONTHLY_V1", "")
STRIPE_PRICE_DAILY_ANNUAL_V1 = os.getenv("STRIPE_PRICE_DAILY_ANNUAL_V1", "")
```

#### webapp/server.py
Update `_all_price_ids()` to include the V1 (grandfathered) price IDs:

```python
def _all_price_ids() -> set[str]:
    """All valid price IDs (current + grandfathered)."""
    return {
        p.strip()
        for p in (
            # Current pricing (NEW)
            config.STRIPE_PRICE_STARTER_MONTHLY,
            config.STRIPE_PRICE_STARTER_ANNUAL,
            config.STRIPE_PRICE_DAILY_MONTHLY,
            config.STRIPE_PRICE_DAILY_ANNUAL,
            # Grandfathered pricing (V1)
            config.STRIPE_PRICE_STARTER_MONTHLY_V1,
            config.STRIPE_PRICE_STARTER_ANNUAL_V1,
            config.STRIPE_PRICE_DAILY_MONTHLY_V1,
            config.STRIPE_PRICE_DAILY_ANNUAL_V1,
            # Legacy
            config.STRIPE_PRICE_ID,
            config.STRIPE_PRICE_ID_ANNUAL,
            # Top-ups
            config.STRIPE_PRICE_TOPUP_5,
            config.STRIPE_PRICE_TOPUP_15,
        )
        if (p or "").strip()
    }
```

Update `_tier_from_price_id()` to recognize V1 price IDs:

```python
def _tier_from_price_id(pid: str | None) -> str:
    """Map a Stripe price ID to a tier (starter/daily/empty)."""
    pid = (pid or "").strip()
    if not pid:
        return ""
    
    daily_prices = {
        (config.STRIPE_PRICE_DAILY_MONTHLY or "").strip(),
        (config.STRIPE_PRICE_DAILY_ANNUAL or "").strip(),
        (config.STRIPE_PRICE_DAILY_MONTHLY_V1 or "").strip(),
        (config.STRIPE_PRICE_DAILY_ANNUAL_V1 or "").strip(),
    } - {""}
    
    starter_prices = {
        (config.STRIPE_PRICE_STARTER_MONTHLY or "").strip(),
        (config.STRIPE_PRICE_STARTER_ANNUAL or "").strip(),
        (config.STRIPE_PRICE_STARTER_MONTHLY_V1 or "").strip(),
        (config.STRIPE_PRICE_STARTER_ANNUAL_V1 or "").strip(),
        (config.STRIPE_PRICE_ID or "").strip(),
        (config.STRIPE_PRICE_ID_ANNUAL or "").strip(),
    } - {""}
    
    if pid in daily_prices:
        return "daily"
    if pid in starter_prices:
        return "starter"
    return ""
```

Update `_annual_price_ids()` to include V1 annual prices:

```python
def _annual_price_ids() -> tuple[str, ...]:
    return tuple(p for p in (
        config.STRIPE_PRICE_STARTER_ANNUAL,
        config.STRIPE_PRICE_DAILY_ANNUAL,
        config.STRIPE_PRICE_STARTER_ANNUAL_V1,
        config.STRIPE_PRICE_DAILY_ANNUAL_V1,
        config.STRIPE_PRICE_ID_ANNUAL,
    ) if (p or "").strip())
```

#### webapp/static/app.js
Update pricing display to show new amounts:

```javascript
const PLAN_CATALOG = [
    { tier: 'starter', monthly: { charge: '$19' }, annual: { charge: '$190' } },
    { tier: 'daily', monthly: { charge: '$79' }, annual: { charge: '$790' } },
];

// In setPricingCycle function:
if (cycle === 'annual') {
    // Starter annual
    document.getElementById('starter-price').textContent = '$15.83';  // $190/12
    document.getElementById('starter-period').textContent = '/mo';
    document.getElementById('starter-note').textContent = 'Billed $190/year · 2 months free';
    document.getElementById('starter-videos').innerHTML = '<strong>180 credits</strong>/year';
    // Daily annual
    document.getElementById('daily-price').textContent = '$65.83';  // $790/12
    document.getElementById('daily-period').textContent = '/mo';
    document.getElementById('daily-note').textContent = 'Billed $790/year · 2 months free';
    document.getElementById('daily-videos').innerHTML = '<strong>420 credits</strong>/year';
} else {
    // Starter monthly
    document.getElementById('starter-price').textContent = '$19';
    document.getElementById('starter-period').textContent = '/mo';
    document.getElementById('starter-note').textContent = '15 credits / month · cancel anytime';
    document.getElementById('starter-videos').innerHTML = '<strong>15 credits</strong>/month';
    // Daily monthly
    document.getElementById('daily-price').textContent = '$79';
    document.getElementById('daily-period').textContent = '/mo';
    document.getElementById('daily-note').textContent = '35 credits / month · cancel anytime';
    document.getElementById('daily-videos').innerHTML = '<strong>35 credits</strong>/month';
}
```

### 4. Verification Steps

After deployment:

1. **Verify webhook handlers recognize both old and new price IDs**
   - Check logs for `invoice.paid` events from grandfathered subscribers
   - Confirm credits are granted correctly (15/180 for Starter, 35/420 for Daily)

2. **Test new signup flow**
   - Create test subscription with new Starter monthly ($19)
   - Create test subscription with new Daily monthly ($79)
   - Verify correct credit grants

3. **Test grandfathered subscriber renewal**
   - Wait for existing $27 or $49 sub to renew (or use test clock)
   - Verify renewal succeeds and credits granted

4. **Test plan upgrades/downgrades**
   - Grandfathered Starter ($27) → Daily ($79) 
   - New Starter ($19) → Daily ($79)
   - Verify upgrade grants work correctly

5. **Test portal access**
   - Grandfathered subscriber opens billing portal
   - Verify they can change payment method, cancel, etc.
   - Verify they CANNOT see new pricing (Stripe handles this automatically)

## Rollback Plan

If issues arise:

1. **To rollback pricing display only**: Revert the `app.js` changes (restores $27/$49 display)
2. **To rollback new signups**: Update env vars to point back to V1 price IDs temporarily
3. **Grandfathered subs are unaffected**: They continue on their existing subscriptions regardless

## Timeline

1. **Create new Stripe Prices** (5 minutes via Dashboard or API)
2. **Update DigitalOcean env vars** (5 minutes, requires app restart)
3. **Deploy code changes** (standard deployment process)
4. **Monitor first 24-48 hours** for any webhook/renewal issues
5. **Announce pricing change** to marketing channels (new customers only)

## Notes

- **No existing subscribers are migrated or notified** - this is purely for new signups
- **Credits remain unchanged** - only the dollar amounts differ
- **All existing logic (upgrades, downgrades, renewals) continues working** because it's based on tiers and credits, not dollar amounts
- **Stripe automatically prevents grandfathered users from seeing new pricing** in the portal - they only see their current subscription options
