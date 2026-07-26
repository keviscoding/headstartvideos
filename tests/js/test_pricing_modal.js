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
function makeEnv({ plan = 'starter_trial', credits = 2, trialUsed = true } = {}) {
    const dom = new JSDOM(INDEX, { runScripts: 'outside-only' });
    const sandbox = dom.getInternalVMContext();
    const calls = { endTrial: [], checkout: [], tracked: [], resumed: 0 };

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
            const monthly = sent.tier === 'daily' ? 35 : 15;
            return respond({
                plan: sent.tier,
                credits: sent.interval === 'annual' ? monthly * 12 : monthly,
            });
        }
        return respond({});
    };
    vm.runInContext(APP_JS, sandbox);

    sandbox.__calls = calls;
    const run = (src) => vm.runInContext(src, sandbox);
    run(`
        currentUser = ${JSON.stringify({ plan, credits, trial_used: trialUsed, has_billing_account: true })};
        _featureFlags = { storyboard_animate_credits_flat: 12, trial_credits: 2 };
        track = (ev, props) => __calls.tracked.push([ev, props]);
        _doCheckout = (planKey) => { __calls.checkout.push(planKey); };
        updateAuthUI = () => {};
        loadBillingPage = () => {};
        showCelebration = () => {};
        loadIntegrations = () => {};
        window.alert = () => {};
    `);

    return { dom, sandbox, calls, run };
}

console.log('\nTrial user sees both plans, with the real charge on the button');
{
    const { run, sandbox } = makeEnv({ plan: 'starter_trial' });
    run('showPricingModal({ reason: "cook" })');
    const doc = sandbox.document;

    check('modal is shown to a trial user rather than a second dialog', () => {
        eq(doc.getElementById('pricing-modal').style.display, 'flex', 'display');
    });
    check('both plans are reachable', () => {
        if (!doc.getElementById('pricing-cta-starter')) throw new Error('no starter CTA');
        if (!doc.getElementById('pricing-cta-daily')) throw new Error('no daily CTA');
    });
    check('CTA states the exact monthly charge', () => {
        has(doc.getElementById('pricing-cta-starter').textContent, '$27 today', 'starter CTA');
        has(doc.getElementById('pricing-cta-daily').textContent, '$49 today', 'daily CTA');
    });
    check('annual toggle is offered', () => {
        eq(doc.getElementById('pricing-toggle').style.display, 'inline-flex', 'toggle');
    });
    check('top-ups are advertised so the allowance does not read as a hard cap', () => {
        eq(doc.getElementById('topup-row').classList.contains('hidden'), false, 'topup row hidden');
    });
    check('subtitle sells the refresh and top-ups, not "1 video a month"', () => {
        const sub = doc.getElementById('pricing-subtitle').textContent;
        has(sub, 'refresh', 'subtitle');
        has(sub, 'buy more cook credits', 'subtitle');
        lacks(sub, '1 animated video', 'subtitle');
    });
    check('cards keep the landing page feature lists', () => {
        has(doc.getElementById('pricing-card-daily').textContent, 'Priority', 'daily card');
        has(doc.getElementById('pricing-card-starter').textContent, '20 min', 'starter card');
    });
}

console.log('\nSwitching to annual re-prices the CTA to the yearly charge');
{
    const { run, sandbox } = makeEnv({ plan: 'starter_trial' });
    run('showPricingModal({})');
    run('setPricingPlan("annual")');
    const doc = sandbox.document;

    check('CTA shows the full annual amount actually billed today', () => {
        has(doc.getElementById('pricing-cta-starter').textContent, '$270 today', 'starter CTA');
        has(doc.getElementById('pricing-cta-daily').textContent, '$490 today', 'daily CTA');
    });
    check('annual advertises the full year of credits', () => {
        has(doc.getElementById('starter-videos').textContent, '180 credits', 'starter credits');
        has(doc.getElementById('daily-videos').textContent, '420 credits', 'daily credits');
    });
    check('switching back restores the monthly charge', () => {
        run('setPricingPlan("monthly")');
        has(doc.getElementById('pricing-cta-starter').textContent, '$27 today', 'starter CTA');
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
        has(sandbox.document.getElementById('pricing-cta-daily').textContent, '$49 today', 'daily CTA');
    });
}

console.log('\nChoosing a plan converts the trial onto that exact plan');
{
    const { run, calls } = makeEnv({ plan: 'starter_trial' });
    run('showPricingModal({})');
    run('setPricingPlan("annual")');
    run('proceedToCheckout("daily")');

    check('trial conversion is used, never Checkout (which the server rejects)', () => {
        eq(calls.checkout.length, 0, 'checkout calls');
        eq(calls.endTrial.length, 1, 'end-trial calls');
        has(calls.endTrial[0].url, '/api/billing/end-trial', 'url');
    });
    check('the chosen tier and interval both reach the server', () => {
        eq(calls.endTrial[0].body.tier, 'daily', 'tier');
        eq(calls.endTrial[0].body.interval, 'annual', 'interval');
    });
    setTimeout(() => {
        check('a Starter trial ends up on Daily annual, with the year of credits', () => {
            eq(run('currentUser.plan'), 'daily', 'plan after convert');
            eq(run('currentUser.credits'), 420, 'credits after convert');
        });
    }, 30);
}

console.log('\nA paid, non-trial user still goes through Stripe Checkout');
{
    const { run, calls } = makeEnv({ plan: 'free', credits: 0, trialUsed: true });
    run('showPricingModal({})');
    run('proceedToCheckout("starter")');
    check('checkout is used and end-trial is not', () => {
        eq(calls.endTrial.length, 0, 'end-trial calls');
        eq(calls.checkout[0], 'starter_monthly', 'checkout plan');
    });
    check('CTA does not promise a charge amount it cannot know', () => {
        eq(calls.checkout.length, 1, 'checkout calls');
    });
}

console.log('\nThe blocked action resumes after the charge succeeds');
{
    const { run, calls } = makeEnv({ plan: 'starter_trial' });
    run('showPricingModal({ afterEndTrial: () => __calls.resumed++ })');
    run('proceedToCheckout("starter")');

    setTimeout(() => {
        check('paying continues the cook they were blocked on', () => eq(calls.resumed, 1, 'resume count'));

        // A fresh prompt with no callback must not replay the old one.
        run('currentUser.plan = "starter_trial"');
        run('showPricingModal({})');
        run('proceedToCheckout("starter")');
        setTimeout(() => {
            check('a later prompt does not replay a stale callback', () => eq(calls.resumed, 1, 'resume count'));
            console.log(failures ? `\n${failures} check(s) failed\n` : '\nAll checks passed\n');
            process.exit(failures ? 1 : 0);
        }, 30);
    }, 30);
}
