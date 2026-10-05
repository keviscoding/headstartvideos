#!/bin/bash
# Validation script for new pricing implementation
# Does not require Python dependencies - just checks code changes

set -e

echo "==================================================================="
echo "Validating New Pricing Implementation ($19/$79, grandfather $27/$49)"
echo "==================================================================="
echo ""

# Check config.py has V1 price constants
echo "✓ Checking config.py for V1 grandfathered price constants..."
grep -q "STRIPE_PRICE_STARTER_MONTHLY_V1" config.py || { echo "❌ Missing STARTER_MONTHLY_V1"; exit 1; }
grep -q "STRIPE_PRICE_STARTER_ANNUAL_V1" config.py || { echo "❌ Missing STARTER_ANNUAL_V1"; exit 1; }
grep -q "STRIPE_PRICE_DAILY_MONTHLY_V1" config.py || { echo "❌ Missing DAILY_MONTHLY_V1"; exit 1; }
grep -q "STRIPE_PRICE_DAILY_ANNUAL_V1" config.py || { echo "❌ Missing DAILY_ANNUAL_V1"; exit 1; }
echo "  ✓ All V1 price constants defined in config.py"
echo ""

# Check server.py includes V1 prices in _channelrecipe_price_ids
echo "✓ Checking server.py includes V1 prices in _channelrecipe_price_ids()..."
grep -A 20 "def _channelrecipe_price_ids" webapp/server.py | grep -q "STRIPE_PRICE_STARTER_MONTHLY_V1" || { echo "❌ V1 prices not in _channelrecipe_price_ids"; exit 1; }
echo "  ✓ _channelrecipe_price_ids() includes V1 prices"
echo ""

# Check server.py _tier_from_price_id recognizes V1
echo "✓ Checking server.py _tier_from_price_id() recognizes V1 prices..."
grep -A 25 "def _tier_from_price_id" webapp/server.py | grep -q "STRIPE_PRICE_DAILY_MONTHLY_V1" || { echo "❌ _tier_from_price_id missing V1"; exit 1; }
grep -A 25 "def _tier_from_price_id" webapp/server.py | grep -q "STRIPE_PRICE_STARTER_MONTHLY_V1" || { echo "❌ _tier_from_price_id missing V1"; exit 1; }
echo "  ✓ _tier_from_price_id() recognizes V1 prices"
echo ""

# Check server.py _annual_price_ids includes V1
echo "✓ Checking server.py _annual_price_ids() includes V1 annual prices..."
grep -A 8 "def _annual_price_ids" webapp/server.py | grep -q "STRIPE_PRICE_STARTER_ANNUAL_V1" || { echo "❌ _annual_price_ids missing V1 annual"; exit 1; }
grep -A 8 "def _annual_price_ids" webapp/server.py | grep -q "STRIPE_PRICE_DAILY_ANNUAL_V1" || { echo "❌ _annual_price_ids missing V1 annual"; exit 1; }
echo "  ✓ _annual_price_ids() includes V1 annual prices"
echo ""

# Check app.js has new pricing ($19/$79)
echo "✓ Checking app.js shows new pricing ($19/$79)..."
grep -q "charge: '\$19'" webapp/static/app.js || { echo "❌ Missing \$19 starter monthly"; exit 1; }
grep -q "charge: '\$79'" webapp/static/app.js || { echo "❌ Missing \$79 daily monthly"; exit 1; }
grep -q "charge: '\$190'" webapp/static/app.js || { echo "❌ Missing \$190 starter annual"; exit 1; }
grep -q "charge: '\$790'" webapp/static/app.js || { echo "❌ Missing \$790 daily annual"; exit 1; }
echo "  ✓ PLAN_CATALOG shows new pricing"
echo ""

# Check setPricingCycle function has new amounts
echo "✓ Checking setPricingCycle() function displays new amounts..."
grep "starter-price.*\$19" webapp/static/app.js || { echo "❌ Missing \$19 in setPricingCycle"; exit 1; }
grep "daily-price.*\$79" webapp/static/app.js || { echo "❌ Missing \$79 in setPricingCycle"; exit 1; }
grep "starter-note.*\$190" webapp/static/app.js || { echo "❌ Missing \$190/year in note"; exit 1; }
grep "daily-note.*\$790" webapp/static/app.js || { echo "❌ Missing \$790/year in note"; exit 1; }
echo "  ✓ setPricingCycle() displays new amounts"
echo ""

# Check .env.example documents new vars
echo "✓ Checking .env.example documents V1 grandfathered vars..."
grep -q "STRIPE_PRICE_STARTER_MONTHLY_V1" .env.example || { echo "❌ Missing V1 vars in .env.example"; exit 1; }
grep -q "STRIPE_PRICE_DAILY_MONTHLY_V1" .env.example || { echo "❌ Missing V1 vars in .env.example"; exit 1; }
grep -q "Grandfathered" .env.example || { echo "❌ Missing grandfathered comment"; exit 1; }
echo "  ✓ .env.example documents V1 environment variables"
echo ""

# Check tests updated
echo "✓ Checking test file includes V1 price constants..."
grep -q "STARTER_M_V1" tests/test_stripe_plan_resolution.py || { echo "❌ Tests missing V1 constants"; exit 1; }
grep -q "DAILY_M_V1" tests/test_stripe_plan_resolution.py || { echo "❌ Tests missing V1 constants"; exit 1; }
grep -q "test_grandfathered_v1_prices_recognized" tests/test_stripe_plan_resolution.py || { echo "❌ Tests missing V1 test"; exit 1; }
echo "  ✓ Tests include V1 price handling"
echo ""

echo "==================================================================="
echo "✓ ALL VALIDATION CHECKS PASSED"
echo "==================================================================="
echo ""
echo "Code changes complete. Ready for deployment after:"
echo "1. Creating new Stripe Prices at \$19/\$190 and \$79/\$790"
echo "2. Updating DigitalOcean environment variables:"
echo "   - Set current STRIPE_PRICE_* vars to NEW price IDs"
echo "   - Set STRIPE_PRICE_*_V1 vars to OLD price IDs"
echo "3. Deploying this code"
echo ""
echo "See PRICING_MIGRATION_PLAN.md for complete instructions."
echo ""
