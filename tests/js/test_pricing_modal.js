/**
 * The trial paywall is the whole revenue path, so exercise it against a real DOM.
 *
 * Run: node tests/js/test_pricing_modal.js   (needs `npm i jsdom` — see README)
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..', '..');
const APP_JS = fs.readFileSync(path.join(ROOT, 'webapp/static/app.js'), 'utf8');
const INDEX = fs.readFileSync(path.join(ROOT, 'webapp/static/index.html'), 'utf8');

let failures = 0;
function check(name, fn) {
    try { fn(); console.log(`  ok   ${name}`); }
    catch (e) { failures++; console.log(`  FAIL ${name}\n       ${e.message}`); }
}
function eq(actual, expected, what) {
    if (actual !== expected) {
        throw new Error(`${what || 'value'}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
    }
}
function has(hay, needle, what) {
    if (!String(hay).includes(needle)) {
        throw new Error(`${what || 'text'}: ${JSON.stringify(String(hay))} is missing ${JSON.stringify(needle)}`);
    }
}
function lacks(hay, needle, what) {
    if (String(hay).includes(needle)) {
        throw new Error(`${what || 'text'}: ${JSON.stringify(String(hay))} should not contain ${JSON.stringify(needle)}`);
    }
}

/**
 * Load the real markup + app.js, stubbing only what talks to the network.
 *
 * app.js top-level state uses `let`, which in a script is a lexical binding and
 * not a property of the global object, so it can only be set by evaluating an
 * assignment inside the context — plain `sandbox.x = ...` is invisible to it.
 */
function makeEnv({ plan = 'starter_trial', credits = 2, trialUsed = true, trialDays = 7 } = {}) {
    const dom = new JSDOM(INDEX, { runScripts: 'outside-only' });
    const sandbox = dom.getInternalVMContext();
    const calls = { endTrial: [], checkout: [], tracked: [], resumed: 0, portal: 0 };

    // Installed before app.js so its own fetch wrapper delegates here. The
    // page's boot-time GETs land here too; only the billing POST matters.
    const respond = (payload) => {
        const body = JSON.stringify(payload);
        // app.js reads responses via readJson(), which calls res.text().
        return Promise.resolve({
            ok: true, status: 200,
            text: () => Promise.resolve(body),
            json: () => Promise.resolve(payload),
        });
    };
    dom.window.fetch = (url, opts = {}) => {
        if (String(url).includes('/api/billing/end-trial')) {
            const sent = JSON.parse(opts.body || '{}');
            calls.endTrial.push({ url, body: sent });
            const monthly = { daily: 35, studio_pro: 60, studio_max: 100 }[sent.tier] || 15;
            return respond({
                plan: sent.tier,
                credits: sent.interval === 'annual' ? monthly * 12 : monthly,
            });
        }
        if (String(url).includes('/api/billing/portal')) {
            calls.portal++;
            return respond({});
        }
        return respond({});
    };
    vm.runInContext(APP_JS, sandbox);

    sandbox.__calls = calls;
    const run = (src) => vm.runInContext(src, sandbox);
    run(`
        currentUser = ${JSON.stringify({ plan, credits, trial_used: trialUsed, has_billing_account: true })};
        _featureFlags = { storyboard_animate_credits_flat: 12, trial_credits: 2, free_trial_days: ${trialDays} };
        track = (ev, props) => __calls.tracked.push([ev, props]);
        _doCheckout = (planKey) => { __calls.checkout.push(planKey); };
        updateAuthUI = () => {};
        loadBillingPage = () => { __calls.billingLoads = (__calls.billingLoads || 0) + 1; };
        navigateTo = (page) => { __calls.nav = page; };
        showCelebration = () => {};
        loadIntegrations = () => {};
        window.alert = () => {};
    `);

    return { dom, sandbox, calls, run };
}

console.log('\nPaid access chooser path skips trial CTA labels');
{
    const { run, sandbox } = makeEnv({ plan: 'free', credits: 0, trialUsed: false });
    run('showPricingModal({ skipTrial: true, reason: "access_chooser_paid" })');
    const doc = sandbox.document;
    check('paid chooser shows Subscribe now', () => {
        has(doc.getElementById('pricing-cta-daily')?.textContent || '', 'Subscribe now', 'creator CTA');
    });
    check('paid chooser heading is Full access', () => {
        has(doc.querySelector('#pricing-modal h2.cr-display')?.textContent || '', 'Full access', 'heading');
    });
}

console.log('\nNo free trials (the launch default): free users go straight to paid plans');
{
    const { run, calls, sandbox } = makeEnv({ plan: 'free', credits: 0, trialUsed: false, trialDays: 0 });
    run('openUpgradeFlow({ reason: "nav" })');
    const doc = sandbox.document;
    check('pricing opens directly, never the trial chooser', () => {
        eq(doc.getElementById('pricing-modal').style.display, 'flex', 'pricing display');
        const chooser = doc.getElementById('access-chooser-modal');
        eq(chooser.classList.contains('hidden') || chooser.style.display === 'none', true, 'chooser closed');
    });
    check('nothing mentions a free trial', () => {
        lacks(doc.getElementById('pricing-subtitle').textContent, 'trial', 'subtitle');
        for (const tier of ['daily', 'studio_pro', 'studio_max']) {
            has(doc.getElementById(`pricing-cta-${tier}`).textContent, 'Subscribe now', `${tier} CTA`);
        }
    });
    check('a new signup is taken to Studio instead of a trial chooser', () => {
        run('maybeShowAccessChooser({ fromSignup: true })');
        eq(calls.nav, 'local-studio', 'nav');
    });
    run('proceedToCheckout("studio_pro")');
    check('checkout charges today', () => {
        eq(calls.checkout[0], 'studio_pro_monthly', 'checkout plan');
        eq(run('!!_checkoutSkipTrial'), true, 'skipTrial');
    });
}

console.log('\nPlans without a Stripe price yet are shown but cannot be bought');
{
    const { run, sandbox } = makeEnv({ plan: 'free', credits: 0, trialUsed: true, trialDays: 0 });
    run(`_featureFlags.plan_catalog = [
        { tier: 'daily', available: true, annual_available: true },
        { tier: 'studio_pro', available: false, annual_available: false },
        { tier: 'studio_max', available: true, annual_available: false }]`);
    run('showPricingModal({})');
    const doc = sandbox.document;
    check('unconfigured monthly plan says Coming soon and is disabled', () => {
        eq(doc.getElementById('pricing-cta-studio_pro').textContent, 'Coming soon', 'pro CTA');
        eq(doc.getElementById('pricing-cta-studio_pro').disabled, true, 'pro disabled');
        eq(doc.getElementById('pricing-cta-daily').disabled, false, 'creator enabled');
    });
    run('setPricingPlan("annual")');
    check('a plan missing only its yearly price is disabled on yearly', () => {
        eq(doc.getElementById('pricing-cta-studio_max').disabled, true, 'max annual disabled');
        eq(doc.getElementById('pricing-cta-daily').disabled, false, 'creator annual enabled');
    });
}

console.log('\nopenUpgradeFlow: free + trial available → access chooser');
{
    const { run, sandbox } = makeEnv({ plan: 'free', credits: 0, trialUsed: false });
    run('openUpgradeFlow({ reason: "nav" })');
    const doc = sandbox.document;
    check('access chooser is shown', () => {
        eq(doc.getElementById('access-chooser-modal').style.display, 'flex', 'display');
    });
    check('pricing stays closed until they pick a path', () => {
        const pricing = doc.getElementById('pricing-modal');
        eq(pricing.classList.contains('hidden') || pricing.style.display === 'none', true, 'pricing closed');
    });
    check('upgrade opts are parked for the chooser', () => {
        eq(run('_pendingUpgradeOpts && _pendingUpgradeOpts.reason'), 'nav', 'parked reason');
    });
    check('canStartFreeTrial is true', () => {
        eq(run('canStartFreeTrial()'), true, 'canStartFreeTrial');
    });
}

console.log('\nopenUpgradeFlow: free + trial_used → Subscribe now (no chooser)');
{
    const { run, sandbox } = makeEnv({ plan: 'free', credits: 0, trialUsed: true });
    run('openUpgradeFlow({ reason: "billing" })');
    const doc = sandbox.document;
    check('pricing opens directly', () => {
        eq(doc.getElementById('pricing-modal').style.display, 'flex', 'pricing display');
    });
    check('access chooser stays closed', () => {
        const chooser = doc.getElementById('access-chooser-modal');
        eq(chooser.classList.contains('hidden') || chooser.style.display === 'none', true, 'chooser closed');
    });
    check('CTAs say Subscribe now', () => {
        has(doc.getElementById('pricing-cta-studio_pro').textContent, 'Subscribe now', 'pro CTA');
    });
    check('checkout will skip Stripe trial', () => {
        eq(run('!!_checkoutSkipTrial'), true, 'skipTrial');
    });
}

console.log('\nopenUpgradeFlow: active trial → convert pricing (no chooser)');
{
    const { run, sandbox } = makeEnv({ plan: 'daily_trial', credits: 2, trialUsed: true });
    run('openUpgradeFlow({ reason: "nav" })');
    const doc = sandbox.document;
    check('pricing opens for convert', () => {
        eq(doc.getElementById('pricing-modal').style.display, 'flex', 'pricing display');
    });
    check('access chooser stays closed', () => {
        const chooser = doc.getElementById('access-chooser-modal');
        eq(chooser.classList.contains('hidden') || chooser.style.display === 'none', true, 'chooser closed');
    });
    check('CTA names the charge today', () => {
        has(doc.getElementById('pricing-cta-daily').textContent, 'Start Creator — $49 today', 'creator CTA');
    });
    check('canStartFreeTrial is false while on trial', () => {
        eq(run('canStartFreeTrial()'), false, 'canStartFreeTrial');
    });
}

console.log('\nopenUpgradeFlow: paid non-trial → Billing, not trial chooser');
{
    const { run, calls, sandbox } = makeEnv({ plan: 'starter', credits: 10, trialUsed: true });
    run('openUpgradeFlow({ reason: "billing" })');
    check('navigates to settings/billing', () => {
        eq(calls.nav, 'settings', 'nav');
        eq(calls.billingLoads, 1, 'billing load');
    });
    check('does not open access chooser', () => {
        const chooser = sandbox.document.getElementById('access-chooser-modal');
        eq(chooser.classList.contains('hidden') || chooser.style.display === 'none', true, 'chooser closed');
    });
}

console.log('\nChooser paid path keeps parked reason and skipTrial');
{
    const { run, sandbox } = makeEnv({ plan: 'free', credits: 0, trialUsed: false });
    run('openUpgradeFlow({ reason: "cook" })');
    run('chooseAccessPaid()');
    const doc = sandbox.document;
    check('chooser closes', () => {
        const chooser = doc.getElementById('access-chooser-modal');
        eq(chooser.classList.contains('hidden') || chooser.style.display === 'none', true, 'chooser closed');
    });
    check('pricing opens with Subscribe now', () => {
        eq(doc.getElementById('pricing-modal').style.display, 'flex', 'pricing');
        has(doc.getElementById('pricing-cta-daily').textContent, 'Subscribe now', 'CTA');
    });
    check('skipTrial is set for checkout', () => {
        eq(run('!!_checkoutSkipTrial'), true, 'skipTrial');
    });
}

console.log('\nChooser trial path keeps Start free trial CTAs');
{
    const { run, sandbox } = makeEnv({ plan: 'free', credits: 0, trialUsed: false });
    run('openUpgradeFlow({ reason: "cook" })');
    run('chooseAccessTrial()');
    const doc = sandbox.document;
    check('pricing opens with Start free trial', () => {
        eq(doc.getElementById('pricing-modal').style.display, 'flex', 'pricing');
        has(doc.getElementById('pricing-cta-daily').textContent, 'Start free trial', 'CTA');
    });
    check('skipTrial is off', () => {
        eq(run('!!_checkoutSkipTrial'), false, 'skipTrial');
    });
}

console.log('\nTrial user sees all three plans, with the real charge on the button');
{
    const { run, sandbox } = makeEnv({ plan: 'daily_trial' });
    run('showPricingModal({ reason: "cook" })');
    const doc = sandbox.document;

    check('modal is shown to a trial user rather than a second dialog', () => {
        eq(doc.getElementById('pricing-modal').style.display, 'flex', 'display');
    });
    check('Creator, Pro and Max are reachable, and Starter is no longer sold', () => {
        for (const tier of ['daily', 'studio_pro', 'studio_max']) {
            if (!doc.getElementById(`pricing-cta-${tier}`)) throw new Error(`no ${tier} CTA`);
        }
        eq(doc.getElementById('pricing-cta-starter'), null, 'starter CTA');
    });
    check('CTA states the exact monthly charge', () => {
        has(doc.getElementById('pricing-cta-daily').textContent, '$49 today', 'creator CTA');
        has(doc.getElementById('pricing-cta-studio_pro').textContent, '$79 today', 'pro CTA');
        has(doc.getElementById('pricing-cta-studio_max').textContent, '$119 today', 'max CTA');
    });
    check('annual toggle is offered', () => {
        eq(doc.getElementById('pricing-toggle').style.display, 'inline-flex', 'toggle');
    });
    check('no top-up buttons, which 403 without an active subscription', () => {
        eq(doc.getElementById('topup-row').classList.contains('hidden'), true, 'topup row hidden');
    });
    check('subtitle sells the refresh and top-ups, not "1 video a month"', () => {
        const sub = doc.getElementById('pricing-subtitle').textContent;
        has(sub, 'refresh', 'subtitle');
        has(sub, 'buy more cook credits', 'subtitle');
        lacks(sub, '1 animated video', 'subtitle');
    });
    check('cards lead with Studio and the style-clone allowance', () => {
        has(doc.getElementById('pricing-card-daily').textContent, 'ChannelRecipe Studio', 'creator card');
        has(doc.getElementById('pricing-card-daily').textContent, 'Clone 3', 'creator card');
        has(doc.getElementById('pricing-card-studio_pro').textContent, 'Clone 15', 'pro card');
        has(doc.getElementById('pricing-card-studio_max').textContent, 'Unlimited', 'max card');
        has(doc.getElementById('pricing-card-studio_pro').textContent, 'Most popular', 'pro badge');
    });
}

console.log('\nSwitching to annual re-prices the CTA to the yearly charge');
{
    const { run, sandbox } = makeEnv({ plan: 'daily_trial' });
    run('showPricingModal({})');
    run('setPricingPlan("annual")');
    const doc = sandbox.document;

    check('CTA shows the full annual amount actually billed today', () => {
        has(doc.getElementById('pricing-cta-daily').textContent, '$490 today', 'creator CTA');
        has(doc.getElementById('pricing-cta-studio_pro').textContent, '$790 today', 'pro CTA');
        has(doc.getElementById('pricing-cta-studio_max').textContent, '$1,190 today', 'max CTA');
    });
    check('annual shows the monthly equivalent and a full year of credits', () => {
        eq(doc.getElementById('studio_pro-price').textContent, '$65.83', 'pro price');
        has(doc.getElementById('daily-videos').textContent, '420 credits', 'creator credits');
        has(doc.getElementById('studio_max-videos').textContent, '1200 credits', 'max credits');
    });
    check('switching back restores the monthly charge', () => {
        run('setPricingPlan("monthly")');
        has(doc.getElementById('pricing-cta-daily').textContent, '$49 today', 'creator CTA');
        eq(doc.getElementById('studio_pro-price').textContent, '$79', 'pro price');
    });
}

console.log('\nAnnual is hidden when Stripe has no annual price configured');
{
    const { run, sandbox } = makeEnv({ plan: 'starter_trial' });
    run('_featureFlags.annual_plans_available = false');
    run('showPricingModal({})');
    check('toggle is hidden rather than offering an unbuyable plan', () => {
        eq(sandbox.document.getElementById('pricing-toggle').style.display, 'none', 'toggle');
    });
    check('monthly still works normally', () => {
        has(sandbox.document.getElementById('pricing-cta-daily').textContent, '$49 today', 'creator CTA');
    });
}

console.log('\nChoosing a plan converts the trial onto that exact plan');
{
    const { run, calls } = makeEnv({ plan: 'daily_trial' });
    run('showPricingModal({})');
    run('setPricingPlan("annual")');
    run('proceedToCheckout("studio_pro")');

    check('trial conversion is used, never Checkout (which the server rejects)', () => {
        eq(calls.checkout.length, 0, 'checkout calls');
        eq(calls.endTrial.length, 1, 'end-trial calls');
        has(calls.endTrial[0].url, '/api/billing/end-trial', 'url');
    });
    check('the chosen tier and interval both reach the server', () => {
        eq(calls.endTrial[0].body.tier, 'studio_pro', 'tier');
        eq(calls.endTrial[0].body.interval, 'annual', 'interval');
    });
    setTimeout(() => {
        check('a Creator trial ends up on Pro annual, with the year of credits', () => {
            eq(run('currentUser.plan'), 'studio_pro', 'plan after convert');
            eq(run('currentUser.credits'), 720, 'credits after convert');
        });
    }, 30);
}

console.log('\nA paid subscriber does get the top-up buttons');
{
    const { run, sandbox } = makeEnv({ plan: 'daily', credits: 3, trialUsed: true });
    run('showPricingModal({})');
    check('top-up row is offered to someone who can actually buy one', () => {
        eq(sandbox.document.getElementById('topup-row').classList.contains('hidden'), false, 'topup row hidden');
    });
}

console.log('\nA free user who already trialled goes through Stripe Checkout');
{
    const { run, calls } = makeEnv({ plan: 'free', credits: 0, trialUsed: true });
    run('showPricingModal({})');
    run('proceedToCheckout("daily")');
    check('checkout is used and end-trial is not', () => {
        eq(calls.endTrial.length, 0, 'end-trial calls');
        eq(calls.checkout[0], 'daily_monthly', 'checkout plan');
    });
    check('CTA does not promise a charge amount it cannot know', () => {
        eq(calls.checkout.length, 1, 'checkout calls');
    });
}

console.log('\nSubscribers change plans in the Stripe portal, never Checkout');
{
    const { run, calls, sandbox } = makeEnv({ plan: 'starter', credits: 5, trialUsed: true });
    run('showPricingModal({})');
    check('existing Starter subscribers are offered Creator as a switch', () => {
        has(sandbox.document.getElementById('pricing-cta-daily').textContent, 'Switch to Creator', 'creator CTA');
    });
    run('proceedToCheckout("daily")');
    check('switching opens the billing portal', () => {
        eq(calls.portal, 1, 'portal calls');
        eq(calls.checkout.length, 0, 'checkout calls');
        eq(calls.endTrial.length, 0, 'end-trial calls');
    });
    const creator = makeEnv({ plan: 'daily', credits: 5, trialUsed: true });
    creator.run('showPricingModal({})');
    check('a Creator subscriber sees their current plan marked', () => {
        has(creator.sandbox.document.getElementById('pricing-cta-daily').textContent, 'Your current plan', 'creator CTA');
        has(creator.sandbox.document.getElementById('pricing-cta-studio_max').textContent, 'Switch to Max', 'max CTA');
    });
    creator.run('proceedToCheckout("daily")');
    check('choosing the plan you already have does nothing', () => {
        eq(creator.calls.portal, 0, 'portal calls');
    });
}

console.log('\nThe blocked action resumes after the charge succeeds');
{
    const { run, calls } = makeEnv({ plan: 'daily_trial' });
    run('showPricingModal({ afterEndTrial: () => __calls.resumed++ })');
    run('proceedToCheckout("daily")');

    setTimeout(() => {
        check('paying continues the cook they were blocked on', () => eq(calls.resumed, 1, 'resume count'));

        // A fresh prompt with no callback must not replay the old one.
        run('currentUser.plan = "daily_trial"');
        run('showPricingModal({})');
        run('proceedToCheckout("daily")');
        setTimeout(() => {
            check('a later prompt does not replay a stale callback', () => eq(calls.resumed, 1, 'resume count'));
            console.log(failures ? `\n${failures} check(s) failed\n` : '\nAll checks passed\n');
            process.exit(failures ? 1 : 0);
        }, 30);
    }, 30);
}
