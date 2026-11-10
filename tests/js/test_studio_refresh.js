/**
 * The Studio page must not rebuild itself on the background account refresh.
 *
 * A Max customer (2026-11-10): "Every 2-3 minutes while using Channel Recipe, it redirects me back to the MAX PLAN
 * tab." The 45 s refresh (and every tab focus) re-rendered the whole page, which closed "Install Studio on another
 * computer", stopped the setup video and left them on the plan card.
 *
 * Run: node tests/js/test_studio_refresh.js   (needs jsdom; tests/test_studio_page_refresh.py runs it)
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
    return Promise.resolve().then(fn).then(
        () => console.log(`  ok   ${name}`),
        (e) => { failures++; console.log(`  FAIL ${name}\n       ${e.message}`); });
}
function eq(actual, expected, what) {
    if (actual !== expected) throw new Error(`${what}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

function makeEnv() {
    const dom = new JSDOM(INDEX, { runScripts: 'outside-only' });
    const sandbox = dom.getInternalVMContext();
    let me = { id: 7, email: 'max@example.com', plan: 'studio_max', credits: 100 };
    const body = () => JSON.stringify({ user: me, notices: [] });
    dom.window.fetch = (url) => Promise.resolve({
        ok: true, status: 200,
        text: () => Promise.resolve(String(url).includes('/api/auth/me') ? body() : '{}'),
        json: () => Promise.resolve(String(url).includes('/api/auth/me') ? JSON.parse(body()) : {}),
    });
    vm.runInContext(APP_JS, sandbox);
    const run = (src) => vm.runInContext(src, sandbox);
    sandbox.__opens = 0;
    run(`
        window.CRLocalStudio = { open: () => { __opens++; } };
        window.scrollTo = () => {};
        loadIntegrations = () => {};
        track = () => {};
    `);
    return { run, sandbox, setUser: (u) => { me = u; } };
}

(async () => {
    console.log('\nThe Studio page redraws only when who is signed in, or their plan, changes');
    const { run, sandbox, setUser } = makeEnv();
    await new Promise((done) => setTimeout(done, 300));   // the page boots: signs in, opens Studio (the front door)
    await check('the page boots on Studio and draws it once', () => {
        eq(run('state.page'), 'local-studio', 'page'); eq(sandbox.__opens, 1, 'opens');
    });
    for (let i = 0; i < 4; i++) await run('refreshUserData()');
    await check('the 45 s refresh and tab focus leave it alone', () => eq(sandbox.__opens, 1, 'opens'));
    setUser({ id: 7, email: 'max@example.com', plan: 'studio_pro', credits: 60 });
    await run('refreshUserData()');
    await check('a plan change redraws it', () => eq(sandbox.__opens, 2, 'opens'));
    setUser({ id: 7, email: 'max@example.com', plan: 'studio_pro', credits: 55 });
    await run('refreshUserData()');
    await check('spending credits does not', () => eq(sandbox.__opens, 2, 'opens'));
    run('currentUser = null; updateAuthUI()');
    await check('signing out redraws it', () => eq(sandbox.__opens, 3, 'opens'));
    run(`state.page = 'pipeline'; currentUser = ${JSON.stringify({ id: 8, email: 'other@example.com', plan: 'studio_max' })}; updateAuthUI();`);
    await check('another page is left alone', () => eq(sandbox.__opens, 3, 'opens'));

    console.log(failures ? `\n${failures} failed` : '\nall passed');
    process.exit(failures ? 1 : 0);
})();
