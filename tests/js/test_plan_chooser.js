/**
 * Exercises the real plan chooser from app.js against a real DOM.
 *
 * The block is self-contained (lines 1166-1303), so it is sliced out and run
 * verbatim rather than reimplemented — a copy would not catch a regression.
 */
const fs = require('fs');
// jsdom is not a project dependency, so allow an out-of-tree install.
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const APP = process.env.APP_JS || require('path').resolve(__dirname, '../../webapp/static/app.js');
const lines = fs.readFileSync(APP, 'utf8').split('\n');
const block = lines.slice(1165, 1303).join('\n');   // 1-indexed 1166..1303

if (!/function chooseTrialPlan/.test(block) || !/function planCoverageCopy/.test(block)) {
    throw new Error('slice missed the chooser — line numbers moved');
}

let failures = 0;
function check(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failures++;
    console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}`);
    if (!ok) console.log(`        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`);
}
function checkThat(name, cond, detail = '') {
    if (!cond) failures++;
    console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ' + detail}`);
}

function makeEnv(plan, flags = {}) {
    const dom = new JSDOM('<!doctype html><html><body></body></html>');
    const tracked = [];
    const sandbox = {
        document: dom.window.document,
        window: dom.window,
        currentUser: { plan },
        _featureFlags: flags,
        track: (ev, props) => tracked.push({ ev, props }),
        console,
    };
    const vm = require('vm');
    vm.createContext(sandbox);
    vm.runInContext(block + '\nthis.__api={chooseTrialPlan,planOptions,planCoverageCopy,trialTier,animatedCookCredits};', sandbox);
    return { ...sandbox.__api, doc: dom.window.document, tracked, sandbox };
}

console.log('\n--- credits translated into outcomes ---');
{
    const e = makeEnv('starter_trial');
    check('cook costs 12 credits', e.animatedCookCredits(), 12);
    const [s, d] = e.planOptions();
    check('Starter: 1 animated video', [s.price, s.cooks, s.videos], ['$27', 1, 15]);
    check('Daily: 2 animated videos', [d.price, d.cooks, d.videos], ['$49', 2, 35]);
    checkThat('Daily flagged popular', d.popular === true);
    check('coverage copy', e.planCoverageCopy(),
        'Starter includes 1 animated video a month, Daily includes 2.');
    checkThat('copy never exposes the raw credit price of a cook',
        !e.planCoverageCopy().includes('12'), e.planCoverageCopy());
}

console.log('\n--- copy tracks the flag, so it cannot go stale ---');
{
    const e = makeEnv('starter_trial', { storyboard_animate_credits_flat: 6 });
    check('at 6 credits/cook', e.planCoverageCopy(),
        'Starter includes 2 animated videos a month, Daily includes 5.');
}

console.log('\n--- both plans are offered, and the trial plan is preselected ---');
for (const [plan, expect] of [['starter_trial', 'starter'], ['daily_trial', 'daily']]) {
    const e = makeEnv(plan);
    e.chooseTrialPlan({ reason: 'Unlock on-site cook.' });
    const picks = [...e.doc.querySelectorAll('.plan-pick')];
    check(`${plan}: two plans shown`, picks.map(p => p.dataset.tier), ['starter', 'daily']);
    check(`${plan}: preselects the trial's own tier`, e.trialTier(), expect);
    const cta = e.doc.querySelector('#confirm-charge-yes').textContent.trim();
    const price = expect === 'daily' ? '$49' : '$27';
    checkThat(`${plan}: CTA names the exact charge (${price})`, cta.includes(price), cta);
    checkThat(`${plan}: reason is shown`,
        e.doc.body.textContent.includes('Unlock on-site cook.'));
    checkThat(`${plan}: MOST POPULAR badge present`,
        e.doc.body.innerHTML.includes('MOST POPULAR'));
    checkThat(`${plan}: cancel wording present`,
        e.doc.body.textContent.includes('Cancel anytime'));
}

console.log('\n--- switching plan updates the charge, and resolves that tier ---');
(async () => {
    {
        const e = makeEnv('starter_trial');
        const p = e.chooseTrialPlan();
        const before = e.doc.querySelector('#confirm-charge-yes').textContent.trim();
        checkThat('starts at $27', before.includes('$27'), before);
        e.doc.querySelector('.plan-pick[data-tier="daily"]').onclick();
        const after = e.doc.querySelector('#confirm-charge-yes').textContent.trim();
        checkThat('after picking Daily the CTA says $49', after.includes('$49'), after);
        checkThat('and no longer says $27', !after.includes('$27'), after);
        e.doc.querySelector('#confirm-charge-yes').onclick();
        check('resolves the chosen tier', await p, 'daily');
        checkThat('upgrade recorded as switched',
            e.tracked.some(t => t.ev === 'trial_charge_confirmed' && t.props.switched === true),
            JSON.stringify(e.tracked));
    }

    console.log('\n--- declining must never charge ---');
    {
        const e = makeEnv('starter_trial');
        const p = e.chooseTrialPlan();
        e.doc.querySelector('#confirm-charge-no').onclick();
        check('"Keep my free trial" resolves null', await p, null);
        checkThat('modal removed', !e.doc.getElementById('confirm-charge-modal'));
        checkThat('decline tracked',
            e.tracked.some(t => t.ev === 'trial_charge_declined'));
    }
    {
        const e = makeEnv('starter_trial');
        const p = e.chooseTrialPlan();
        const modal = e.doc.getElementById('confirm-charge-modal');
        modal.onclick({ target: modal });
        check('backdrop click resolves null', await p, null);
    }

    console.log('\n--- double click cannot stack modals or double charge ---');
    {
        const e = makeEnv('starter_trial');
        const a = e.chooseTrialPlan();
        const b = e.chooseTrialPlan();
        checkThat('same promise reused', a === b);
        check('one modal in the DOM',
            e.doc.querySelectorAll('#confirm-charge-modal').length, 1);
        e.doc.querySelector('#confirm-charge-yes').onclick();
        check('both awaits get the tier', [await a, await b], ['starter', 'starter']);
        const e2 = e;
        const c = e2.chooseTrialPlan();
        checkThat('a fresh prompt opens after close', c !== a);
    }

    console.log(failures === 0 ? '\nALL CHOOSER CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`);
    process.exit(failures === 0 ? 0 : 1);
})();
