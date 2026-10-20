/* Find My Money Niche: four questions, a taste (niche #1 and one proof channel), then sign in for the full result.
   Story-style: one idea per screen, progress bars on top, Back and Next at the bottom. Server: webapp/money_niche*.py */
(function () {
    'use strict';
    const stage = document.getElementById('stage');
    const bars = document.getElementById('bars');
    const MINE = 'cr_money_niche_results';

    const QUESTIONS = [
        {key: 'face', kicker: 'Question 1 of 4', title: 'Will you show your face?',
         lede: 'Plenty of channels never do. This changes which niches fit and who does the talking.',
         options: [['camera', '🎥', 'Yes, on camera', 'I’m happy to be seen'],
                   ['voice', '🎙️', 'Voice only', 'You’ll hear me, not see me'],
                   ['none', '🤖', 'No, neither', 'Fully faceless, with an AI voice']]},
        {key: 'hours', kicker: 'Question 2 of 4', title: 'How many hours a week can you give it?',
         lede: 'This decides Shorts or long videos, and how often you upload.',
         options: [['under3', '⏱️', 'Under 3 hours', 'A little each week'],
                   ['3to8', '🌙', '3 to 8 hours', 'An evening or two'],
                   ['8to15', '🔥', '8 to 15 hours', 'A serious side project'],
                   ['over15', '🚀', '15+ hours', 'Close to a part-time job']]},
        {key: 'loves', kicker: 'Question 3 of 4', title: 'What do you love watching?',
         lede: 'Pick up to three. You’ll make better videos about what you’d watch anyway.', multi: true},
        {key: 'budget', kicker: 'Question 4 of 4', title: 'Monthly budget for AI tools?',
         lede: 'For voices, pictures and animation. This decides which formats fit.',
         options: [['free', '🆓', '$0', 'Free tools, stock footage and paintings'],
                   ['low', '🖼️', 'Up to $30', 'AI pictures'],
                   ['mid', '🧊', '$30 to $100', '3D scenes or an AI presenter'],
                   ['high', '🎬', '$100+', 'AI video clips, anything goes']]},
    ];
    // The same cards as webapp/money_niche.py LOVES: a real video still from each niche.
    const LOVES = [
        ['mysteries', 'Mysteries & documentaries', 'I2O7blSSzpI'], ['disasters', 'How disasters happen', 'wLFY_Zu_O08'],
        ['money', 'Money & business', 'Ha2oNXDZuSg'], ['crime', 'True crime & bodycam', 'NrTaqT_PgmI'],
        ['history', 'History', 'd3tXkJkgbQw'], ['stories', 'Stories & anime', 't8JEQXEAKSs'],
        ['mind', 'Psychology & big ideas', 'sNg5FXt_DDM'], ['world', 'Maps & world politics', 'yIZRffLA294'],
        ['tips', 'Life tips & real skills', 'wR1l5k8CKgg']];
    const RESULT_STEPS = ['niche', 'more', 'titles', 'recipe', 'share', 'final'];

    const state = {answers: {loves: []}, q: 0, user: null, teaser: null, full: null, id: '', step: 0, mine: false};

    // ---- helpers --------------------------------------------------------------------------------------
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const safeUrl = (u) => /^https:\/\//.test(String(u || '')) ? esc(u) : '#';
    const n = (x) => {
        x = Number(x) || 0;
        if (x >= 1e6) return (x / 1e6).toFixed(x >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
        if (x >= 1e3) return Math.round(x / 1e3) + 'K';
        return String(Math.round(x));
    };
    const money = (r) => r ? '$' + Number(r[0]).toLocaleString('en-US') + '–$' + Number(r[1]).toLocaleString('en-US') : '';
    const mine = () => { try { return JSON.parse(localStorage.getItem(MINE) || '[]'); } catch (_) { return []; } };
    const remember = (id) => { try { localStorage.setItem(MINE, JSON.stringify([id, ...mine().filter((x) => x !== id)].slice(0, 10))); } catch (_) {} };
    function toast(text) {
        const t = document.createElement('div'); t.className = 'toast'; t.textContent = text; document.body.appendChild(t);
        setTimeout(() => t.remove(), 2200);
    }
    async function api(path, body) {
        const res = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw Object.assign(new Error(data.detail || 'Something went wrong. Try again.'), {status: res.status});
        return data;
    }
    function progress(total, done) {
        bars.innerHTML = Array.from({length: total}, (_, i) => '<div class="bar' + (i < done ? ' done' : '') + '"><span></span></div>').join('');
        if (done < total && bars.children[done]) requestAnimationFrame(() => { const s = bars.children[done].firstChild; if (s) s.style.width = '35%'; });
    }
    function show(html) {
        stage.innerHTML = '<section class="screen">' + html + '</section>';
        window.scrollTo({top: 0});
        const focus = stage.querySelector('h1, h2'); if (focus) { focus.tabIndex = -1; focus.focus({preventScroll: true}); }
    }

    // ---- the quiz -------------------------------------------------------------------------------------
    function intro() {
        progress(4, 0);
        const last = mine()[0];
        show('<div class="kicker">Free · 60 seconds</div>' +
            '<h1>Find your <span class="grad">money niche</span></h1>' +
            '<p class="lede">Which faceless YouTube channel should you make, and would it actually make money? Four taps, and real channels as proof.</p>' +
            '<div class="card lift"><div class="stats">' +
            '<div class="stat"><b>15</b><span>proven niches checked</span></div>' +
            '<div class="stat"><b>Real</b><span>small channels growing right now</span></div></div>' +
            '<p class="note">No promises: we show what real channels did, and say plainly what new channels earn (nothing, until YouTube’s Partner Program).</p></div>' +
            '<div class="spacer"></div>' +
            '<button class="btn" data-start>Start</button>' +
            (last ? '<button class="btn ghost" style="margin-top:10px" data-last>See my last result</button>' : '') +
            '<p class="foot"><a href="/3d-harness#niches">Just browse the 15 niches</a></p>');
        stage.querySelector('[data-start]').onclick = () => { state.q = 0; question(); };
        const lastBtn = stage.querySelector('[data-last]');
        if (lastBtn) lastBtn.onclick = () => openResult(last, true);
    }

    function question() {
        const q = QUESTIONS[state.q];
        progress(4, state.q);
        let body;
        if (q.multi) {
            const picked = state.answers.loves;
            body = '<div class="loves">' + LOVES.map(([id, label, vid]) =>
                '<button class="love" type="button" data-love="' + id + '" aria-pressed="' + picked.includes(id) + '">' +
                '<img src="https://i.ytimg.com/vi/' + vid + '/mqdefault.jpg" alt="" loading="lazy" referrerpolicy="no-referrer">' +
                '<span>' + esc(label) + '</span><i class="tick" aria-hidden="true">' + (picked.includes(id) ? '✓' : '') + '</i></button>').join('') + '</div>';
        } else {
            body = '<div class="opts">' + q.options.map(([value, icon, title, sub]) =>
                '<button class="opt" type="button" data-value="' + value + '" aria-pressed="' + (state.answers[q.key] === value) + '">' +
                '<span class="ic" aria-hidden="true">' + icon + '</span><span><b>' + esc(title) + '</b><small>' + esc(sub) + '</small></span></button>').join('') + '</div>';
        }
        show('<div class="kicker">' + q.kicker + '</div><h2>' + esc(q.title) + '</h2><p class="lede">' + esc(q.lede) + '</p>' + body +
            '<div class="spacer"></div><div class="nav"><button class="btn ghost back" type="button" aria-label="Back" data-back>←</button>' +
            '<button class="btn" type="button" data-next' + (ready(q) ? '' : ' disabled') + '>' + (state.q === 3 ? 'Show my niche' : 'Next') + '</button></div>');
        stage.querySelector('[data-back]').onclick = () => { if (state.q === 0) intro(); else { state.q -= 1; question(); } };
        stage.querySelector('[data-next]').onclick = next;
        stage.querySelectorAll('[data-value]').forEach((b) => b.onclick = () => {
            state.answers[q.key] = b.dataset.value;
            stage.querySelectorAll('[data-value]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
            stage.querySelector('[data-next]').disabled = false;
            setTimeout(next, 260);
        });
        stage.querySelectorAll('[data-love]').forEach((b) => b.onclick = () => {
            const id = b.dataset.love, list = state.answers.loves;
            if (list.includes(id)) list.splice(list.indexOf(id), 1);
            else if (list.length < 3) list.push(id);
            else { toast('Up to three: untick one first'); return; }
            b.setAttribute('aria-pressed', String(list.includes(id)));
            b.querySelector('.tick').textContent = list.includes(id) ? '✓' : '';
            stage.querySelector('[data-next]').disabled = !list.length;
        });
    }
    function ready(q) { return q.multi ? state.answers.loves.length > 0 : !!state.answers[q.key]; }
    function next() {
        if (!ready(QUESTIONS[state.q])) return;
        if (state.q < 3) { state.q += 1; question(); } else submit();
    }

    async function submit() {
        progress(4, 4);
        show('<div class="think"><div><div class="orb" aria-hidden="true"></div><h2 id="think">Checking 15 proven niches…</h2>' +
            '<p class="lede">Then the Niche Finder’s small channels with big views.</p></div></div>');
        const lines = ['Finding small channels with big views…', 'Working out what they plausibly earn…'];
        const tick = setInterval(() => { const h = document.getElementById('think'); if (h && lines.length) h.textContent = lines.shift(); }, 700);
        try {
            const [teaser] = await Promise.all([api('/api/money-niche/quiz', state.answers), new Promise((r) => setTimeout(r, 1500))]);
            state.teaser = teaser; state.id = teaser.id; state.mine = true; remember(teaser.id);
            history.replaceState(null, '', '/money-niche/r/' + teaser.id);
            taste();
        } catch (e) {
            show('<h2>That didn’t work</h2><p class="lede">' + esc(e.message) + '</p><div class="spacer"></div><button class="btn" data-retry>Try again</button>');
            stage.querySelector('[data-retry]').onclick = submit;
        } finally { clearInterval(tick); }
    }

    // ---- the taste, then the gate ---------------------------------------------------------------------
    function proofCard(p) {
        if (!p) return '<div class="card"><p class="note">The Niche Finder hasn’t caught a new channel in this niche this week. Your full result shows the established ones.</p></div>';
        if (p.kind === 'established') {
            return '<div class="card proof">' + (p.thumbnail ? '<a class="thumb" href="' + safeUrl(p.url) + '" target="_blank" rel="noopener"><img src="' + safeUrl(p.thumbnail) + '" alt="" referrerpolicy="no-referrer"></a>' : '') +
                '<div><span class="tag">Established channel</span></div><div class="who"><div><a href="' + safeUrl(p.url) + '" target="_blank" rel="noopener">' + esc(p.name) + '</a>' +
                '<small>' + esc(p.views_label) + ' views on its breakout video</small></div></div></div>';
        }
        return '<div class="card proof">' + (p.thumbnail ? '<a class="thumb" href="' + safeUrl(p.url) + '" target="_blank" rel="noopener"><img src="' + safeUrl(p.thumbnail) + '" alt="" referrerpolicy="no-referrer"></a>' : '') +
            '<div class="who">' + (p.avatar ? '<img src="' + safeUrl(p.avatar) + '" alt="" referrerpolicy="no-referrer">' : '') +
            '<div><a href="' + safeUrl(p.url) + '" target="_blank" rel="noopener">' + esc(p.name) + '</a><small>' +
            (p.months ? 'Started ' + p.months + (p.months === 1 ? ' month' : ' months') + ' ago · ' : '') + n(p.subscribers) + ' subscribers</small></div></div>' +
            '<div><span class="big">' + n(p.recent_avg_views) + '</span> <span class="small">views per video, on its last ' + (p.recent_count || 'few') + ' videos</span></div></div>';
    }

    function taste() {
        const t = state.teaser;
        progress(RESULT_STEPS.length, 0);
        const signedIn = !!state.user;
        const theirs = !state.mine && !signedIn;
        let gate;
        if (theirs) {
            gate = '<div class="spacer"></div><a class="btn" href="/money-niche">Find my own money niche</a>' +
                '<p class="foot small">Free, 60 seconds, real channels as proof.</p>';
        } else if (signedIn) {
            gate = '<div class="spacer"></div><button class="btn" data-full>See my full result</button>';
        } else {
            gate = '<div class="card gate"><h3>Your full result is ready</h3><ul>' +
                '<li>' + (t.more || 2) + ' more niches that fit you</li><li>What channels like these plausibly earn</li>' +
                '<li>How many videos they needed before their first breakout</li><li>5 ready-to-use video titles</li>' +
                '<li>The recipe: format, length, look, pacing</li></ul>' +
                '<div class="google" id="google-btn" hidden></div><div class="or" id="google-or" hidden>or</div>' +
                '<div id="email-step"><input class="field" id="email" type="email" autocomplete="email" placeholder="you@example.com" aria-label="Your email">' +
                '<button class="btn" style="margin-top:8px" data-send>Email me a sign-in code</button></div>' +
                '<div id="code-step" hidden><p class="small" id="code-to"></p><input class="field code" id="code" inputmode="numeric" maxlength="6" placeholder="000000" aria-label="6-digit code">' +
                '<div class="row"><button class="btn ghost" style="width:auto" data-other>Back</button><button class="btn" data-verify>See my result</button></div></div>' +
                '<label class="consent"><input type="checkbox" id="consent"> <span>Email me new breakout channels in my niche. About every week or two. Unsubscribe anytime.</span></label>' +
                '<p class="err" id="err" role="alert"></p><p class="note">Free. No card. Signing in just saves your result; we never post anything.</p></div>';
        }
        show('<div class="kicker">' + (theirs ? 'Their #1 money niche' : 'Your #1 money niche') + '</div>' +
            '<h1 class="grad">' + esc(t.niche.name) + '</h1><p class="lede">' + esc(t.niche.pitch) + '</p>' +
            '<p class="kicker" style="margin-top:18px">The proof</p>' + proofCard(t.proof) + gate);
        if (signedIn) stage.querySelector('[data-full]').onclick = loadFull;
        if (!theirs && !signedIn) bindGate();
    }

    function bindGate() {
        const err = (m) => { document.getElementById('err').textContent = m || ''; };
        const done = async (data) => {
            state.user = data.user;
            if (document.getElementById('consent')?.checked) {
                try { await api('/api/leads', {source: 'quiz', consent: true, result: state.id}); } catch (_) {}
            }
            loadFull();
        };
        window.CRGoogle?.mount(document.getElementById('google-btn'), {
            theme: 'filled_black', width: Math.min(360, stage.clientWidth - 40),
            onShown: () => { document.getElementById('google-or').hidden = false; },
            onSignedIn: done, onError: err});
        const email = document.getElementById('email'), code = document.getElementById('code');
        const send = async () => {
            err('');
            const value = email.value.trim();
            if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)) { err('Enter your email address.'); return; }
            const b = stage.querySelector('[data-send]'); b.disabled = true; b.textContent = 'Sending…';
            try {
                await api('/api/auth/send-code', {email: value});
                document.getElementById('email-step').hidden = true; document.getElementById('code-step').hidden = false;
                document.getElementById('code-to').textContent = 'Enter the 6-digit code we sent to ' + value;
                code.focus();
            } catch (e) { err(e.message); } finally { b.disabled = false; b.textContent = 'Email me a sign-in code'; }
        };
        const verify = async () => {
            err('');
            if (!/^\d{6}$/.test(code.value.trim())) { err('Enter the 6-digit code.'); return; }
            const b = stage.querySelector('[data-verify]'); b.disabled = true;
            let attribution = null; try { attribution = window.CRTelemetry?.attributionProps?.() || null; } catch (_) {}
            try { await done(await api('/api/auth/verify', {email: email.value.trim(), code: code.value.trim(), attribution})); }
            catch (e) { err(e.message); b.disabled = false; }
        };
        stage.querySelector('[data-send]').onclick = send;
        email.onkeydown = (e) => { if (e.key === 'Enter') send(); };
        stage.querySelector('[data-verify]').onclick = verify;
        code.onkeydown = (e) => { if (e.key === 'Enter') verify(); };
        stage.querySelector('[data-other]').onclick = () => { document.getElementById('email-step').hidden = false; document.getElementById('code-step').hidden = true; };
    }

    async function loadFull() {
        try {
            const full = await api('/api/money-niche/result/' + encodeURIComponent(state.id));
            if (full.gated) { state.user = null; taste(); return; }
            state.full = full; state.step = 0; result();
        } catch (e) { toast(e.message); }
    }

    // ---- the full result, one idea per screen ---------------------------------------------------------
    function proofFull(p) {
        const meta = [p.months ? 'Started ' + p.months + (p.months === 1 ? ' month' : ' months') + ' ago' : '', p.videos ? p.videos + ' videos' : '', n(p.subscribers) + ' subscribers'].filter(Boolean).join(' · ');
        return '<div class="card proof"><div class="who">' + (p.avatar ? '<img src="' + safeUrl(p.avatar) + '" alt="" referrerpolicy="no-referrer">' : '') +
            '<div><a href="' + safeUrl(p.url) + '" target="_blank" rel="noopener">' + esc(p.name) + '</a><small>' + esc(meta) + '</small></div></div>' +
            (p.recent && p.recent.length ? '<div class="strip">' + p.recent.map((v) => '<a href="' + safeUrl(v.url) + '" target="_blank" rel="noopener">' +
                (v.thumbnail ? '<img src="' + safeUrl(v.thumbnail) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '') + n(v.views) + ' views</a>').join('') + '</div>' : '') +
            '<div class="stats"><div class="stat"><b>' + n(p.recent_avg_views) + '</b><span>views per recent video</span></div>' +
            '<div class="stat"><b>' + (p.earnings ? money(p.earnings) : '–') + '</b><span>a month, plausibly (estimate)</span></div></div>' +
            (p.breakout_after ? '<p class="note">First breakout after about <b>' + p.breakout_after + ' videos</b>.</p>' : '') + '</div>';
    }
    function refs(list) {
        if (!list || !list.length) return '';
        return '<div class="card"><span class="tag">Established channels</span><p class="note" style="margin-top:6px">Bigger and older, so not “new channel” proof, but the format clearly works.</p>' +
            list.map((r) => '<div class="who" style="margin-top:10px"><img src="' + safeUrl(r.thumbnail) + '" alt="" style="border-radius:8px;width:64px;height:36px" referrerpolicy="no-referrer">' +
                '<div><a href="' + safeUrl(r.url) + '" target="_blank" rel="noopener">' + esc(r.name) + '</a><small>' + esc(r.views_label) + ' views on its breakout video</small></div></div>').join('') + '</div>';
    }
    function earningsCard(niche, f) {
        return '<div class="card lift"><div class="kicker">What channels like these plausibly earn</div>' +
            (niche.earnings ? '<p style="margin-top:6px"><span class="big">' + money(niche.earnings) + '</span> <span class="small">a month, estimated</span></p>'
                : '<p class="lede">Not enough recent numbers to estimate yet.</p>') +
            '<p class="note">' + esc(f.estimate_note) + ' Ads in this niche: about <b>$' + niche.rpm[0] + '–$' + niche.rpm[1] + '</b> per 1,000 views.' +
            (niche.shorts_rpm ? ' Shorts pay far less: about <b>$' + niche.shorts_rpm[0] + '–$' + niche.shorts_rpm[1] + '</b> per 1,000 views.' : '') + '</p>' +
            '<p class="note"><b>' + esc(f.ypp_note) + '</b></p>' +
            (niche.breakout_after ? '<p class="note">These channels needed about <b>' + niche.breakout_after + ' videos</b> before their first breakout. Plan for that many before judging.</p>' : '') + '</div>';
    }

    function result() {
        const f = state.full, step = RESULT_STEPS[state.step];
        progress(RESULT_STEPS.length, state.step);
        const top = f.niches[0];
        let html = '';
        if (step === 'niche') {
            html = '<div class="kicker">Your #1 money niche</div><h1 class="grad">' + esc(top.name) + '</h1><p class="lede">' + esc(top.pitch) + '</p>' +
                (top.why.length ? '<div class="chips">' + top.why.map((w) => '<span class="chip">' + esc(w) + '</span>').join('') + '</div>' : '') +
                earningsCard(top, f) + '<p class="kicker" style="margin-top:20px">Proof: small channels doing it now</p>' +
                (top.proofs.length ? top.proofs.map(proofFull).join('') : '<div class="card"><p class="note">The Niche Finder hasn’t caught a new small channel in this niche this week. It hunts twice a day, so check back.</p></div>') +
                refs(top.references);
        } else if (step === 'more') {
            html = '<div class="kicker">Also a great fit</div><h2>Two more niches for you</h2>' + f.niches.slice(1).map((m, i) =>
                '<div class="card"><span class="tag">#' + (i + 2) + '</span><h3 style="margin-top:6px">' + esc(m.name) + '</h3><p class="small" style="margin-top:4px">' + esc(m.pitch) + '</p>' +
                (m.why.length ? '<div class="chips">' + m.why.map((w) => '<span class="chip">' + esc(w) + '</span>').join('') + '</div>' : '') +
                '<div class="stats" style="margin-top:12px"><div class="stat"><b>' + (m.earnings ? money(m.earnings) : '–') + '</b><span>a month for channels like these (estimate)</span></div>' +
                '<div class="stat"><b>' + (m.breakout_after ? '~' + m.breakout_after : '–') + '</b><span>videos before their first breakout</span></div></div>' +
                (m.proofs.length ? m.proofs.slice(0, 2).map(proofFull).join('') : refs(m.references)) + '</div>').join('') +
                '<p class="note">' + esc(f.ypp_note) + '</p>';
        } else if (step === 'titles') {
            html = '<div class="kicker">Ready to use</div><h2>5 video titles for ' + esc(top.name.toLowerCase()) + '</h2>' +
                '<p class="lede">' + (f.titles_source === 'model' ? 'Fresh today, in the tone of the small channels winning right now.' : 'Proven title shapes for this niche.') + '</p>' +
                '<ol class="titles">' + f.titles.map((t) => '<li><span>' + esc(t) + '</span><button class="copy" data-copy="' + esc(t) + '">Copy</button></li>').join('') + '</ol>';
        } else if (step === 'recipe') {
            const r = top.recipe;
            html = '<div class="kicker">The recipe</div><h2>How to make ' + esc(top.name.toLowerCase()) + '</h2>' +
                '<dl class="recipe"><div><dt>Format</dt><dd>' + esc(r.format) + (r.kind === 'shorts' ? ' · Shorts' : ' · long videos') + '</dd></div>' +
                '<div><dt>Length</dt><dd>' + esc(r.length) + '</dd></div><div><dt>Look</dt><dd>' + esc(r.look) + '</dd></div>' +
                '<div><dt>Pacing</dt><dd>' + esc(r.pacing) + '</dd></div><div><dt>Voice</dt><dd>' + esc(r.voice) + '</dd></div>' +
                '<div><dt>Upload</dt><dd>' + esc(r.rhythm) + '</dd></div></dl>';
        } else if (step === 'share') {
            const url = location.origin + '/money-niche/r/' + f.id;
            html = '<div class="kicker">Share it</div><h2>Post your money niche</h2><p class="lede">Your card shows your #1 niche and its proof. Nothing personal.</p>' +
                '<div class="card share"><img src="/money-niche/card/' + encodeURIComponent(f.id) + '.png" alt="My money niche: ' + esc(top.name) + ', on channelrecipe.com"></div>' +
                '<div class="share-btns"><button class="btn" data-share>Share</button><button class="btn ghost" data-link>Copy link</button>' +
                '<a class="btn ghost" href="/money-niche/card/' + encodeURIComponent(f.id) + '.png" download="my-money-niche.png">Save image</a>' +
                '<a class="btn ghost" target="_blank" rel="noopener" href="https://twitter.com/intent/tweet?text=' + encodeURIComponent('My money niche: ' + top.name + '. Find yours:') + '&url=' + encodeURIComponent(url) + '">Post on X</a></div>';
        } else if (step === 'final') {
            const chosen = state.user?.money_niche?.id;
            html = '<div class="kicker">Your next step</div><h2>Make your first video in this niche with <span class="grad">ChannelRecipe</span></h2>' +
                '<p class="lede">Your Studio page opens with this niche picked, and the recipe to make it.</p>' +
                '<div class="pick">' + f.niches.map((m, i) => '<label><input type="radio" name="pick" value="' + esc(m.id) + '"' +
                    ((chosen ? chosen === m.id : i === 0) ? ' checked' : '') + '><span><b>' + esc(m.name) + '</b><br><span class="small">' + esc(m.recipe.format) + '</span></span></label>').join('') + '</div>' +
                // TODO(owner decision): a free first hosted video in the chosen niche would be offered here once a hosted
                // video's cost is known. Until then this goes to sign-up/plans with the niche pre-selected.
                '<label class="consent"><input type="checkbox" id="consent-final"> <span>Email me new breakout channels in my niche. About every week or two. Unsubscribe anytime.</span></label>' +
                '<div class="spacer"></div><button class="btn" data-go>Make my first video</button>' +
                '<p class="foot small">You’ll see the plans next. Nothing is charged until you pick one.</p>';
        }
        const last = state.step === RESULT_STEPS.length - 1;
        show(html + (last ? '<div class="nav"><button class="btn ghost back" aria-label="Back" data-back>←</button></div>'
            : '<div class="spacer"></div><div class="nav"><button class="btn ghost back" aria-label="Back" data-back>←</button><button class="btn" data-next>Next</button></div>'));
        stage.querySelector('[data-back]').onclick = () => { if (state.step === 0) taste(); else { state.step -= 1; result(); } };
        const nx = stage.querySelector('[data-next]'); if (nx) nx.onclick = () => { state.step += 1; result(); };
        stage.querySelectorAll('[data-copy]').forEach((b) => b.onclick = async () => {
            try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = 'Copied'; } catch (_) { toast('Select the title to copy it'); }
        });
        const url = location.origin + '/money-niche/r/' + f.id;
        const sh = stage.querySelector('[data-share]');
        if (sh) sh.onclick = async () => {
            if (navigator.share) { try { await navigator.share({title: 'My money niche: ' + top.name, text: 'My money niche: ' + top.name + '. Find yours in 60 seconds.', url}); } catch (_) {} }
            else { try { await navigator.clipboard.writeText(url); toast('Link copied'); } catch (_) { toast(url); } }
        };
        const ln = stage.querySelector('[data-link]');
        if (ln) ln.onclick = async () => { try { await navigator.clipboard.writeText(url); toast('Link copied'); } catch (_) { toast(url); } };
        const go = stage.querySelector('[data-go]');
        if (go) go.onclick = async () => {
            const pick = stage.querySelector('input[name="pick"]:checked')?.value || top.id;
            go.disabled = true; go.textContent = 'Opening Studio…';
            try {
                const r = await api('/api/money-niche/choose', {result: f.id, niche: pick});
                if (document.getElementById('consent-final')?.checked) {
                    try { await api('/api/leads', {source: 'quiz', consent: true, result: f.id, niche: pick}); } catch (_) {}
                }
                location.href = r.next || '/app#local-studio';
            }
            catch (e) { toast(e.message); go.disabled = false; go.textContent = 'Make my first video'; }
        };
    }

    // ---- start ----------------------------------------------------------------------------------------
    async function openResult(id, own) {
        state.id = id; state.mine = own || mine().includes(id);
        try {
            const data = await api('/api/money-niche/result/' + encodeURIComponent(id));
            if (location.pathname !== '/money-niche/r/' + id) history.replaceState(null, '', '/money-niche/r/' + id);
            if (data.gated) { state.teaser = data; taste(); }
            else { state.teaser = data.teaser; state.full = data; state.step = 0; result(); }
        } catch (e) {
            history.replaceState(null, '', '/money-niche');
            intro(); toast(e.message);
        }
    }

    (async function boot() {
        try { window.CRTelemetry?.captureAttributionFromUrl?.(); } catch (_) {}
        try { state.user = (await api('/api/auth/me')).user || null; } catch (_) {}
        const m = location.pathname.match(/^\/money-niche\/r\/([A-Za-z0-9_-]{6,24})$/);
        if (m) openResult(m[1], false); else intro();
    })();
})();
