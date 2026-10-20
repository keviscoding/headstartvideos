/* Find My Money Niche: two doors on one page.
   - Starting from zero: four questions -> a taste (niche #1 + one proof channel) -> sign in -> the full result ->
     make it yours (angle, look, first title) -> your recipe -> plans.
   - I have a channel: paste it -> its dashboard + the biggest problem -> sign in -> every finding, the breakout
     channels to study, a plan, five ideas with thumbnails -> make it yours -> your recipe -> plans.
   Clips playing everywhere are Studio's own (/static/moodboard); stills are real videos from the niche list.
   Server: webapp/money_niche.py, webapp/channel_check.py, webapp/money_niche_web.py */
(function () {
    'use strict';
    const stage = document.getElementById('stage'), bars = document.getElementById('bars');
    const wallEl = document.getElementById('wall'), sideEl = document.getElementById('side');
    const MINE = 'cr_money_niche_results', MB = '/static/moodboard/';
    const desktop = () => window.matchMedia('(min-width: 980px)').matches;
    const still = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

    // Every clip on the landing page's moodboard, made in ChannelRecipe Studio (v = vertical Shorts).
    const ALL = ['titanic-lifeboat', 'gladiator-sold', 'nanga-parbat-climber', 'hand-drawn-money', 'night-vision:v',
        'titanic-iceberg', 'peasant-fire', 'seventies-kids', 'luck-elevator', 'orca-dunkleosteus:v', 'b59-officers',
        'paper-bank', 'chimney-sweeps', 'motel-phone:v', 'forgotten-ruins', 'honeycomb', 'empty-street-run',
        'hippo-yawn:v', 'parliament', 'seventies-hose', 'titanic-clock', 'deep-sea-jelly:v', 'lights-fail', 'bacon-skillet',
        'nanga-parbat-title', 'doorbell-figure:v', 'empty-city', 'cooper-money:v', 'empty-street-dog', 'grape-plasma:v'];
    const VERT = new Set(ALL.filter((c) => c.endsWith(':v')).map((c) => c.slice(0, -2)));

    const QUESTIONS = [
        {key: 'face', kicker: '1 of 4', title: 'Will you show your face?',
         lede: 'Plenty of channels never do. This changes which niches fit, and who does the talking.',
         options: [['camera', 'bacon-skillet', 'Yes, on camera', 'Presenter channels open up'],
                   ['voice', 'nanga-parbat-climber', 'Voice only', 'Your voice over the pictures'],
                   ['none', 'gladiator-sold', 'No, neither', 'Fully faceless, AI voice']]},
        {key: 'hours', kicker: '2 of 4', title: 'Hours a week you can give it?',
         lede: 'This decides Shorts or long videos, and how often you upload.',
         options: [['under3', 'orca-dunkleosteus', 'Under 3 hours', 'Shorts, a few a week'],
                   ['3to8', 'titanic-lifeboat', '3 to 8 hours', 'About a long video a week'],
                   ['8to15', 'hand-drawn-money', '8 to 15 hours', 'A serious side project'],
                   ['over15', 'seventies-kids', '15+ hours', 'Close to a part-time job']]},
        {key: 'loves', kicker: '3 of 4', title: 'What do you love watching?',
         lede: 'Pick up to three. You make better videos about what you’d watch anyway.', multi: true,
         options: [['mysteries', 'nanga-parbat-climber', 'Mysteries & documentaries'], ['disasters', 'titanic-iceberg', 'How disasters happen'],
                   ['money', 'hand-drawn-money', 'Money & business'], ['crime', 'night-vision', 'True crime & bodycam'],
                   ['history', 'peasant-fire', 'History'], ['stories', 'luck-elevator', 'Stories & drama'],
                   ['mind', 'forgotten-ruins', 'Psychology & big ideas'], ['world', 'nanga-parbat-title', 'Maps & world politics'],
                   ['tips', 'bacon-skillet', 'Life tips & real skills']]},
        {key: 'budget', kicker: '4 of 4', title: 'Monthly budget for AI tools?',
         lede: 'For voices, pictures and animation. This decides which formats fit.',
         options: [['free', 'seventies-hose', '$0', 'Stock, archive and paintings'],
                   ['low', 'gladiator-sold', 'Up to $30', 'AI pictures, animated'],
                   ['mid', 'titanic-clock', '$30 to $100', '3D scenes or an AI presenter'],
                   ['high', 'luck-elevator', '$100+', 'Cinematic AI video']]},
    ];
    const QUIZ_STEPS = ['niche', 'more', 'make', 'recipe', 'share', 'plans'];
    const CHECK_STEPS = ['diagnosis', 'peers', 'plan', 'ideas', 'make', 'recipe', 'plans'];
    const PLAN_COPY = {   // the same words as the Studio page's plan cards
        daily: {tag: 'Start making videos', items: ['Studio + every built-in style', 'Videos render on <b>your own computer</b>', 'Clone <b>3</b> channel styles', '35 cloud credits / month for Classic recipes', 'Niche Finder + niche database in Claude']},
        studio_pro: {tag: 'Run several channels', items: ['Everything in Creator', 'Videos render on <b>your own computer</b>', 'Clone <b>15</b> channel styles', '60 cloud credits / month for Classic recipes', '3 Classic cloud videos at once']},
        studio_max: {tag: 'For serious operators', items: ['Everything in Pro', 'Videos render on <b>your own computer</b>', '<b>Unlimited</b> style clones', '100 cloud credits / month for Classic recipes', 'Priority help in Discord']},
    };

    const S = {answers: {loves: []}, q: 0, user: null, cfg: null, pulse: null, niches: null, teaser: null, full: null,
        id: '', step: 0, mine: false, pick: {}, check: null, checkId: '', cycle: 'monthly', door: ''};

    // ---- helpers --------------------------------------------------------------------------------------
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const url = (u) => /^https:\/\//.test(String(u || '')) ? esc(u) : '#';
    const name = (c) => String(c || '').replace(/:v$/, '').replace(/[^a-z0-9-]/g, '');
    const n = (x) => {
        x = Number(x) || 0;
        if (x >= 1e6) return (x / 1e6).toFixed(x >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
        if (x >= 1e3) return (x >= 1e4 ? Math.round(x / 1e3) : (x / 1e3).toFixed(1).replace(/\.0$/, '')) + 'K';
        return String(Math.round(x));
    };
    const money = (x) => '$' + Number(x).toLocaleString('en-US');
    const months = (m) => m ? m + (m === 1 ? ' month' : ' months') : '';
    const mine = () => { try { return JSON.parse(localStorage.getItem(MINE) || '[]'); } catch (_) { return []; } };
    const remember = (id) => { try { localStorage.setItem(MINE, JSON.stringify([id, ...mine().filter((x) => x !== id)].slice(0, 10))); } catch (_) {} };
    function toast(text) {
        const t = document.createElement('div'); t.className = 'toast'; t.textContent = text; document.body.appendChild(t);
        setTimeout(() => t.remove(), 2400);
    }
    async function api(path, body) {
        const res = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw Object.assign(new Error(data.detail || 'Something went wrong. Try again.'), {status: res.status});
        return data;
    }

    // a playing clip (a poster while reduced motion is on), or a real still that slowly drifts
    // `cls` is a class name for the element (bg = fills its card)
    function clip(c, cls = '') {
        const k = name(c), at = cls ? ' class="' + cls + '"' : '';
        if (still) return '<img src="' + MB + k + '.webp" alt=""' + at + '>';
        return '<video muted loop playsinline autoplay preload="metadata" disablepictureinpicture poster="' + MB + k + '.webp" src="' + MB + k + '.mp4"' + at + '></video>';
    }
    function media(item, cls = '') {
        if (item.clip) return clip(item.clip, cls);
        return '<img class="kb ' + cls + '" src="' + url(item.still) + '" alt="" referrerpolicy="no-referrer" loading="lazy">';
    }
    // the words a thumbnail would shout: the two strongest words of the title
    const STOP = new Set('a an the of in on to for and or but is are was were be it its this that with without how why what when who your you my i we they he she his her their from at by as into than then just about after before do does did not no never every most more one two like if live lived would could really'.split(' '));
    const hook = (t) => String(t || '').replace(/[^A-Za-z0-9' ]/g, ' ').split(/\s+/).filter((w) => w && !STOP.has(w.toLowerCase())).slice(0, 2).join(' ').toUpperCase();
    function boardHTML(items, max = 5) {
        if (!items || !items.length) return '';
        return '<div class="board">' + items.slice(0, max).map((it) =>
            '<figure class="' + (it.vertical ? 'v' : '') + '">' + media(it) + '<figcaption>' +
            esc(it.clip ? 'Made in Studio · ' + it.style : it.channel + ' · ' + it.views + ' views') + '</figcaption></figure>').join('') + '</div>';
    }

    // play only what's on screen (phones and laptops stay cool)
    const io = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => entries.forEach((e) => {
        const v = e.target; if (e.isIntersecting) { v.muted = true; v.play?.().catch(() => {}); } else v.pause?.();
    }), {rootMargin: '80px'}) : null;
    function wire(root) {
        root.querySelectorAll('video').forEach((v) => { v.muted = true; if (io) io.observe(v); else v.play?.().catch(() => {}); });
        requestAnimationFrame(() => setTimeout(() => {
            root.querySelectorAll('[data-h]').forEach((el) => { el.style.height = el.dataset.h; });
            root.querySelectorAll('[data-count]').forEach(countUp);
        }, 60));
    }
    function countUp(el) {
        const end = Number(el.dataset.count) || 0, fmt = el.dataset.fmt || 'n', t0 = performance.now(), dur = still ? 0 : 900;
        const draw = (v) => { el.textContent = fmt === 'money' ? money(Math.round(v)) : n(v); };
        const step = (t) => { const k = dur ? Math.min(1, (t - t0) / dur) : 1; draw(end * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
        requestAnimationFrame(step);
    }

    // ---- the wall: everything at first, then your own mood board --------------------------------------
    let wallKey = '';
    function wall(list) {
        const clips = (list && list.length ? list : ALL).map(name);
        const key = clips.join(',') + (desktop() ? 'd' : 'm');
        if (key === wallKey) return; wallKey = key;
        const cols = desktop() ? 4 : 3, out = Array.from({length: cols}, () => []);
        let fill = clips.slice(); while (fill.length < cols * 4) fill = fill.concat(clips);
        fill.forEach((c, i) => out[i % cols].push(c));
        // phones get posters on the wall (the cards in front play); desktops get the clips
        const tile = (c) => '<figure class="' + (VERT.has(c) ? 'v' : '') + '">' + (desktop() && !still ? clip(c) : '<img src="' + MB + c + '.webp" alt="" loading="lazy">') + '</figure>';
        wallEl.style.opacity = '0';
        setTimeout(() => {
            wallEl.innerHTML = out.map((col) => '<div class="col">' + col.concat(col).map(tile).join('') + '</div>').join('');
            wire(wallEl); wallEl.style.opacity = '';
        }, 250);
    }
    function side(html) {
        if (!desktop()) { sideEl.innerHTML = ''; return; }
        sideEl.innerHTML = html || ''; wire(sideEl);
    }
    function progress(total, done) {
        bars.innerHTML = Array.from({length: total}, (_, i) => '<div class="bar' + (i < done ? ' done' : '') + '"><span></span></div>').join('');
        if (done < total && bars.children[done]) requestAnimationFrame(() => { const s = bars.children[done].firstChild; if (s) s.style.width = '35%'; });
    }
    function show(html) {
        stage.querySelectorAll('video').forEach((v) => io?.unobserve(v));
        stage.innerHTML = '<section class="screen">' + html + '</section>';
        window.scrollTo({top: 0});
        const h = stage.querySelector('h1, h2'); if (h) { h.tabIndex = -1; h.focus({preventScroll: true}); }
        wire(stage);
    }

    // ---- analytics pieces -----------------------------------------------------------------------------
    function chart(videos, peer) {
        const list = (videos || []).slice(0, 15).reverse();              // oldest left, newest right
        if (!list.length) return '';
        const top = Math.max(...list.map((v) => v.views || 0), peer || 0, 1);
        const best = Math.max(...list.map((v) => v.views || 0));
        return '<div class="bars-chart" role="img" aria-label="Views on each recent video">' + list.map((v) =>
            '<a href="' + url(v.url) + '" target="_blank" rel="noopener" class="' + ((v.views === best && best > 0) ? 'top' : '') + (v.short ? ' short' : '') +
            '" title="' + esc((v.title ? v.title + ': ' : '') + n(v.views) + ' views') + '"><i data-h="' + Math.max(3, Math.round((v.views || 0) / top * 100)) + '%"></i>' +
            (v.views === best && best > 0 ? '<em>' + n(v.views) + '</em>' : '') + '</a>').join('') +
            (peer ? '<div class="peer" style="bottom:' + Math.round(peer / top * 100) + '%"><span>breakout channels ' + n(peer) + '</span></div>' : '') + '</div>';
    }
    function meter(range) {
        if (!range) return '';
        const max = Math.max(range[1] * 1.25, 100), a = range[0] / max * 100, b = range[1] / max * 100;
        return '<div class="meter"><i style="left:' + a + '%;width:' + Math.max(2, b - a) + '%"></i>' +
            '<span style="left:' + a + '%">' + money(range[0]) + '</span><span style="left:' + b + '%">' + money(range[1]) + '</span>' +
            '<small style="left:' + a + '%">low</small><small style="left:' + b + '%">high</small></div>';
    }
    function road(count) {
        if (!count) return '';
        const shown = Math.min(count, 40);
        return '<div class="road" aria-label="About ' + count + ' videos before the first breakout">' +
            Array.from({length: shown}, (_, i) => '<i class="' + (i === shown - 1 ? 'b' : '') + '"></i>').join('') + '</div>' +
            '<p class="note">Breakout after about <b>' + count + ' videos</b>' + (count > 40 ? ' (first 40 shown)' : '') + '.</p>';
    }
    function proofCard(p, opts = {}) {
        const meta = [p.months ? 'Started ' + months(p.months) + ' ago' : '', p.videos ? p.videos + ' videos' : ''].filter(Boolean).join(' · ');
        const vids = (p.recent || []).map((v) => ({...v, views: v.views}));
        return '<div class="card proof">' +
            '<div class="who">' + (p.avatar ? '<img src="' + url(p.avatar) + '" alt="" referrerpolicy="no-referrer">' : '') +
            '<div style="flex:1;min-width:0"><a href="' + url(p.url) + '" target="_blank" rel="noopener">' + esc(p.name) + '</a><small>' + esc(meta) + '</small></div><span class="live">Live</span></div>' +
            '<div class="stats">' +
            '<div class="stat"><b data-count="' + (p.recent_avg_views || 0) + '">0</b><span>views per recent video</span></div>' +
            '<div class="stat"><b data-count="' + (p.subscribers || 0) + '">0</b><span>subscribers</span></div>' +
            '<div class="stat"><b>' + (p.months ? months(p.months).replace(' months', 'mo').replace(' month', 'mo') : '–') + '</b><span>old</span></div></div>' +
            (vids.length ? chart(vids) + '<div class="thumbs">' + vids.slice(0, 3).map((v) => '<a href="' + url(v.url) + '" target="_blank" rel="noopener">' +
                (v.thumbnail ? '<img src="' + url(v.thumbnail) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '') + '<b>' + n(v.views) + '</b> views</a>').join('') + '</div>' : '') +
            (opts.money && p.earnings ? '<p class="small" style="margin-top:12px">Plausibly earns, if it’s in the Partner Program (estimate)</p>' + meter(p.earnings) : '') +
            (opts.money && p.breakout_after ? road(p.breakout_after) : '') + '</div>';
    }
    function refs(list) {
        if (!list || !list.length) return '';
        return '<div class="card"><span class="tag">Established channels</span><p class="note" style="margin-top:6px">Bigger and older, so not “new channel” proof, but the format clearly works.</p>' +
            '<div class="thumbs" style="margin-top:12px">' + list.map((r) => '<a href="' + url(r.url) + '" target="_blank" rel="noopener"><img src="' + url(r.thumbnail) + '" alt="" referrerpolicy="no-referrer"><b>' +
                esc(r.name) + '</b><br>' + esc(r.views_label) + ' views on its breakout</a>').join('') + '</div></div>';
    }

    // ---- home: two doors ------------------------------------------------------------------------------
    function home() {
        S.door = ''; progress(4, 0); wall(); history.replaceState(null, '', '/money-niche');
        const p = S.pulse, chans = (p && p.channels) || [], last = mine()[0];
        show('<div class="kicker">Free · 60 seconds</div>' +
            '<h1>What channel should <span class="grad">you</span> make?</h1>' +
            '<p class="lede">And would it actually make money? Find the niche that fits you, or find out why your channel isn’t growing.</p>' +
            (p && p.week ? '<div class="pulse"><span class="dot"></span><span class="faces">' + chans.slice(0, 5).map((c) => '<img src="' + url(c.avatar) + '" alt="" referrerpolicy="no-referrer">').join('') +
                '</span><span><b>' + p.week + '</b> small channels broke out this week</span></div>' : '') +
            '<div class="doors">' +
            '<button class="door" data-door="zero">' + clip('titanic-lifeboat', 'bg') + '<span class="go">→</span><span class="in"><b>I’m starting<br>from zero</b><small>Find my money niche · 4 taps</small></span></button>' +
            '<button class="door" data-door="channel">' + clip('hand-drawn-money', 'bg') + '<span class="go">→</span><span class="in"><b>I already have<br>a channel</b><small>Find out why it’s not growing, and fix it</small></span></button></div>' +
            (chans.length ? '<div class="ticker" aria-label="Channels breaking out now"><div class="track">' + chans.concat(chans).map((c) =>
                '<a class="tick" href="' + url(c.url) + '" target="_blank" rel="noopener"><img src="' + url(c.thumbnail || c.avatar) + '" alt="" referrerpolicy="no-referrer" loading="lazy"><span><b>' + esc(c.name) + '</b><small>' +
                n(c.recent_avg_views) + ' views/video' + (c.months ? ' · ' + months(c.months) + ' old' : '') + '</small></span></a>').join('') + '</div></div>' : '') +
            (last ? '<button class="btn ghost" style="margin-top:18px" data-last>See my last result</button>' : '') +
            '<p class="foot"><a href="/3d-harness#niches">Browse the 15 proven niches</a></p>');
        stage.querySelector('[data-door="zero"]').onclick = () => { S.door = 'zero'; S.q = 0; question(); };
        stage.querySelector('[data-door="channel"]').onclick = () => { S.door = 'channel'; checkStart(); };
        const l = stage.querySelector('[data-last]'); if (l) l.onclick = () => openResult(last, true);
        side('<div class="card"><span class="live">Made in ChannelRecipe Studio</span><h3 style="margin-top:6px">Faceless channels, in any look</h3>' +
            boardHTML([{clip: 'titanic-iceberg', style: 'Clean 3D Reconstruction'}, {clip: 'gladiator-sold', style: 'Stick Figure Stories'},
                {clip: 'night-vision', style: '3D Dark Stories', vertical: true}, {clip: 'hand-drawn-money', style: 'Hand-Drawn Money'},
                {clip: 'seventies-kids', style: 'Painted History'}], 5) + '</div>');
    }

    // ---- door 1: the quiz -----------------------------------------------------------------------------
    function chosenClips() {
        const a = S.answers, out = [];
        QUESTIONS.forEach((q) => q.options.forEach(([v, c]) => { if (q.multi ? a.loves.includes(v) : a[q.key] === v) out.push(c); }));
        return out;
    }
    function mineStrip() {
        const c = chosenClips();
        return '<div class="mine" aria-hidden="true">' + c.map((x) => '<figure><img src="' + MB + name(x) + '.webp" alt=""></figure>').join('') +
            (c.length ? '<span>your mood board</span>' : '') + '</div>';
    }
    function question() {
        const q = QUESTIONS[S.q];
        progress(4, S.q);
        const picked = (v) => q.multi ? S.answers.loves.includes(v) : S.answers[q.key] === v;
        const cards = q.multi
            ? '<div class="cards two">' + q.options.map(([v, c, label]) => '<button type="button" class="pick tall" data-v="' + v + '" aria-pressed="' + picked(v) + '">' +
                clip(c, 'bg') + '<i class="tickmark" aria-hidden="true">' + (picked(v) ? '✓' : '') + '</i><span class="in"><b>' + esc(label) + '</b></span></button>').join('') + '</div>'
            : '<div class="cards">' + q.options.map(([v, c, title, sub]) => '<button type="button" class="pick row" data-v="' + v + '" aria-pressed="' + picked(v) + '">' +
                clip(c, 'bg') + '<span class="in"><b>' + esc(title) + '</b><small>' + esc(sub) + '</small></span></button>').join('') + '</div>';
        show(mineStrip() + '<div class="kicker" style="margin-top:12px">Question ' + q.kicker + '</div><h2>' + esc(q.title) + '</h2><p class="lede">' + esc(q.lede) + '</p>' + cards +
            '<div class="spacer"></div><div class="nav"><button class="btn ghost back" type="button" aria-label="Back" data-back>←</button>' +
            '<button class="btn" type="button" data-next' + (ready(q) ? '' : ' disabled') + '>' + (S.q === 3 ? 'Show my niche' : 'Next') + '</button></div>');
        wall(chosenClips().length ? chosenClips().concat(ALL.slice(0, 8)) : null);
        side('<div class="card"><h3>Your mood board</h3><p class="small">Every answer adds to it. Your niche will look like this.</p>' +
            (chosenClips().length ? boardHTML(chosenClips().map((c) => ({clip: c, style: 'your pick', vertical: VERT.has(name(c))})), 6)
                : '<p class="note">Pick an answer to start it.</p>') + '</div>');
        stage.querySelector('[data-back]').onclick = () => { if (S.q === 0) home(); else { S.q -= 1; question(); } };
        stage.querySelector('[data-next]').onclick = next;
        stage.querySelectorAll('[data-v]').forEach((b) => b.onclick = () => {
            const v = b.dataset.v;
            if (q.multi) {
                const list = S.answers.loves;
                if (list.includes(v)) list.splice(list.indexOf(v), 1);
                else if (list.length < 3) list.push(v);
                else { toast('Up to three: untick one first'); return; }
                b.setAttribute('aria-pressed', String(list.includes(v)));
                b.querySelector('.tickmark').textContent = list.includes(v) ? '✓' : '';
                stage.querySelector('[data-next]').disabled = !list.length;
                stage.querySelector('.mine').outerHTML = mineStrip();
                side('<div class="card"><h3>Your mood board</h3><p class="small">Every answer adds to it.</p>' +
                    boardHTML(chosenClips().map((c) => ({clip: c, style: 'your pick', vertical: VERT.has(name(c))})), 6) + '</div>');
            } else {
                S.answers[q.key] = v;
                stage.querySelectorAll('[data-v]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
                setTimeout(next, 280);
            }
        });
    }
    const ready = (q) => q.multi ? S.answers.loves.length > 0 : !!S.answers[q.key];
    function next() { if (!ready(QUESTIONS[S.q])) return; if (S.q < 3) { S.q += 1; question(); } else submit(); }

    function thinking(lines, clips) {
        show('<div class="think"><div><div class="montage">' + clips.slice(0, 6).map((c, i) => clip(c, i ? '' : 'on')).join('') +
            '<div class="scan"></div></div><h2 id="think" style="font-size:30px">' + esc(lines[0]) + '</h2><p class="count" id="count"></p></div></div>');
        const els = stage.querySelectorAll('.montage video, .montage img');
        let i = 0, k = 1;
        const timer = setInterval(() => {
            els.forEach((e) => e.classList.remove('on')); els[(i = (i + 1) % els.length)]?.classList.add('on');
            const h = document.getElementById('think'); if (h && k < lines.length) h.textContent = lines[k++];
        }, 650);
        return () => clearInterval(timer);
    }
    async function submit() {
        progress(4, 4);
        const stop = thinking(['Checking 15 proven niches…', 'Matching your answers…', 'Finding small channels with big views…', 'Working out what they earn…'],
            chosenClips().length ? chosenClips() : ALL.slice(0, 6));
        try {
            const [t] = await Promise.all([api('/api/money-niche/quiz', S.answers), new Promise((r) => setTimeout(r, 2400))]);
            S.teaser = t; S.id = t.id; S.mine = true; remember(t.id);
            history.replaceState(null, '', '/money-niche/r/' + t.id);
            taste();
        } catch (e) {
            show('<h2>That didn’t work</h2><p class="lede">' + esc(e.message) + '</p><div class="spacer"></div><button class="btn" data-retry>Try again</button>');
            stage.querySelector('[data-retry]').onclick = submit;
        } finally { stop(); }
    }

    // ---- the taste, then the gate ---------------------------------------------------------------------
    function tasteProof(p) {
        if (!p) return '<div class="card"><p class="note">The Niche Finder hasn’t caught a new small channel here this week. Your full result shows the established ones.</p></div>';
        if (p.kind === 'established') {
            return '<div class="card"><span class="tag">Established channel</span><div class="thumbs" style="grid-template-columns:1fr;margin-top:10px"><a href="' + url(p.url) + '" target="_blank" rel="noopener">' +
                '<img src="' + url(p.thumbnail) + '" alt="" referrerpolicy="no-referrer"><b>' + esc(p.name) + '</b> · ' + esc(p.views_label) + ' views on its breakout video</a></div></div>';
        }
        return proofCard({...p, recent: p.recent || []});
    }
    function gateHTML(lines) {
        return '<div class="card gate lift"><h3>Your full result is ready</h3><ul>' + lines.map((l) => '<li>' + esc(l) + '</li>').join('') + '</ul>' +
            '<div class="google" id="google-btn" hidden></div><div class="or" id="google-or" hidden>or</div>' +
            '<div id="email-step"><input class="field" id="email" type="email" autocomplete="email" placeholder="you@example.com" aria-label="Your email">' +
            '<button class="btn" style="margin-top:8px" data-send>Email me a sign-in code</button></div>' +
            '<div id="code-step" hidden><p class="small" id="code-to"></p><input class="field code" id="code" inputmode="numeric" maxlength="6" placeholder="000000" aria-label="6-digit code">' +
            '<div class="row"><button class="btn ghost" style="width:auto" data-other>Back</button><button class="btn" data-verify>Show me</button></div></div>' +
            '<label class="consent"><input type="checkbox" id="consent"> <span>Email me new breakout channels in my niche. About every week or two. Unsubscribe anytime.</span></label>' +
            '<p class="err" id="err" role="alert"></p><p class="note">Free. No card. Signing in saves your result; we never post anything.</p></div>';
    }
    function bindGate(after) {
        const err = (m) => { const e = document.getElementById('err'); if (e) e.textContent = m || ''; };
        const done = async (data) => {
            S.user = data.user;
            if (document.getElementById('consent')?.checked) {
                const body = S.door === 'channel' ? {source: 'quiz', consent: true, niche: S.check?.niche?.id || ''} : {source: 'quiz', consent: true, result: S.id};
                try { await api('/api/leads', body); } catch (_) {}
            }
            after();
        };
        window.CRGoogle?.mount(document.getElementById('google-btn'), {
            theme: 'filled_black', width: Math.min(360, stage.clientWidth - 40),
            onShown: () => { document.getElementById('google-or').hidden = false; }, onSignedIn: done, onError: err});
        const email = document.getElementById('email'), code = document.getElementById('code');
        const send = async () => {
            err(''); const v = email.value.trim();
            if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { err('Enter your email address.'); return; }
            const b = stage.querySelector('[data-send]'); b.disabled = true; b.textContent = 'Sending…';
            try {
                await api('/api/auth/send-code', {email: v});
                document.getElementById('email-step').hidden = true; document.getElementById('code-step').hidden = false;
                document.getElementById('code-to').textContent = 'Enter the 6-digit code we sent to ' + v; code.focus();
            } catch (e) { err(e.message); } finally { b.disabled = false; b.textContent = 'Email me a sign-in code'; }
        };
        const verify = async () => {
            err(''); if (!/^\d{6}$/.test(code.value.trim())) { err('Enter the 6-digit code.'); return; }
            const b = stage.querySelector('[data-verify]'); b.disabled = true;
            let attribution = null; try { attribution = window.CRTelemetry?.attributionProps?.() || null; } catch (_) {}
            try { await done(await api('/api/auth/verify', {email: email.value.trim(), code: code.value.trim(), attribution})); } catch (e) { err(e.message); b.disabled = false; }
        };
        stage.querySelector('[data-send]').onclick = send; email.onkeydown = (e) => { if (e.key === 'Enter') send(); };
        stage.querySelector('[data-verify]').onclick = verify; code.onkeydown = (e) => { if (e.key === 'Enter') verify(); };
        stage.querySelector('[data-other]').onclick = () => { document.getElementById('email-step').hidden = false; document.getElementById('code-step').hidden = true; };
    }

    function taste() {
        const t = S.teaser, signedIn = !!S.user, theirs = !S.mine && !signedIn;
        progress(QUIZ_STEPS.length, 0);
        const b = t.board || [];
        wall(b.filter((x) => x.clip).map((x) => x.clip).concat(chosenClips()));
        let gate;
        if (theirs) gate = '<div class="spacer"></div><a class="btn" href="/money-niche">Find my own money niche</a><p class="foot small">Free, 60 seconds, real channels as proof.</p>';
        else if (signedIn) gate = '<div class="spacer"></div><div class="nav"><button class="btn" data-full>See my full result</button></div>';
        else gate = gateHTML([(t.more || 2) + ' more niches that fit you', 'What channels like these plausibly earn', 'How many videos they needed before their first breakout',
            'Make it yours: angle, look and first title', 'Your recipe, ready to make in ChannelRecipe']);
        show('<div class="kicker">' + (theirs ? 'Their #1 money niche' : 'Your #1 money niche') + '</div>' +
            '<h1 class="grad" style="font-size:clamp(36px,10vw,58px)">' + esc(t.niche.name) + '</h1><p class="lede">' + esc(t.niche.pitch) + '</p>' +
            (t.why && t.why.length ? '<div class="chips">' + t.why.map((w) => '<span class="chip ok">✓ ' + esc(w) + '</span>').join('') + '</div>' : '') +
            boardHTML(b, 4) + '<p class="kicker section-k">The proof</p>' + tasteProof(t.proof) + gate);
        side('<div class="card"><h3>' + esc(t.niche.name) + '</h3><p class="small">The look of this niche</p>' + boardHTML(b.slice(1), 5) + '</div>');
        if (signedIn) stage.querySelector('[data-full]').onclick = loadFull;
        if (!theirs && !signedIn) bindGate(loadFull);
    }
    async function loadFull() {
        try {
            const f = await api('/api/money-niche/result/' + encodeURIComponent(S.id));
            if (f.gated) { S.user = null; taste(); return; }
            S.full = f; S.step = 0;
            const top = f.niches[0];
            S.pick = {niche: top.id, angle: top.angles[0]?.id || '', look: top.looks[0]?.id || '', title: ''};
            result();
        } catch (e) { toast(e.message); }
    }

    // ---- the full result ------------------------------------------------------------------------------
    const currentNiche = () => S.door === 'channel'
        ? (S.niches || []).find((x) => x.id === S.pick.niche)
        : (S.full.niches.find((x) => x.id === S.pick.niche) || S.full.niches[0]);
    function earningsCard(nn, f) {
        return '<div class="card lift"><div class="kicker">What channels like these plausibly earn</div>' +
            (nn.earnings ? '<p style="margin-top:8px"><span class="big grad">' + money(nn.earnings[0]) + '–' + money(nn.earnings[1]) + '</span><span class="small"> a month, estimated</span></p>' + meter(nn.earnings)
                : '<p class="lede">Not enough recent numbers to estimate yet.</p>') +
            '<div class="stats two"><div class="stat"><b>$' + nn.rpm[0] + '–$' + nn.rpm[1] + '</b><span>ads pay per 1,000 views in this niche</span></div>' +
            '<div class="stat"><b>' + (nn.breakout_after ? '~' + nn.breakout_after : '–') + '</b><span>videos before their first breakout</span></div></div>' +
            (nn.shorts_rpm ? '<p class="note">Shorts pay far less: about <b>$' + nn.shorts_rpm[0] + '–$' + nn.shorts_rpm[1] + '</b> per 1,000 views.</p>' : '') +
            '<p class="note">' + esc(f.estimate_note) + '</p><p class="note"><b>' + esc(f.ypp_note) + '</b></p></div>';
    }
    function nav(last) {
        return last ? '<div class="nav end"><button class="btn ghost back" aria-label="Back" data-back>←</button></div>'
            : '<div class="spacer"></div><div class="nav"><button class="btn ghost back" aria-label="Back" data-back>←</button><button class="btn" data-next>Next</button></div>';
    }
    function result() {
        const f = S.full, step = QUIZ_STEPS[S.step], top = f.niches[0];
        progress(QUIZ_STEPS.length, S.step);
        let html = '';
        if (step === 'niche') {
            wall(top.board.filter((x) => x.clip).map((x) => x.clip).concat(chosenClips()));
            html = '<div class="kicker">Your #1 money niche</div><h1 class="grad" style="font-size:clamp(36px,10vw,58px)">' + esc(top.name) + '</h1><p class="lede">' + esc(top.pitch) + '</p>' +
                (top.why.length ? '<div class="chips">' + top.why.map((w) => '<span class="chip ok">✓ ' + esc(w) + '</span>').join('') + '</div>' : '') +
                boardHTML(top.board, 5) + earningsCard(top, f) + '<p class="kicker section-k">Small channels doing it right now</p>' +
                (top.proofs.length ? top.proofs.map((p) => proofCard(p, {money: true})).join('') : '<div class="card"><p class="note">The Niche Finder hasn’t caught a new small channel here this week. It hunts twice a day.</p></div>') + refs(top.references);
            side('<div class="card"><span class="live">Live from the Niche Finder</span><h3 style="margin-top:6px">Why ' + esc(top.name.toLowerCase()) + '</h3>' +
                (top.proofs[0] ? chart(top.proofs[0].recent) + '<p class="small">' + esc(top.proofs[0].name) + ': views on its latest videos</p>' : '') + boardHTML(top.board.slice(1), 4) + '</div>');
        } else if (step === 'more') {
            html = '<div class="kicker">Also a great fit</div><h2>Two more niches for you</h2>' + f.niches.slice(1).map((m, i) =>
                '<div class="card"><span class="tag">#' + (i + 2) + '</span><h3 style="margin-top:8px;font-size:24px">' + esc(m.name) + '</h3><p class="small" style="margin-top:4px">' + esc(m.pitch) + '</p>' +
                (m.why.length ? '<div class="chips">' + m.why.map((w) => '<span class="chip ok">✓ ' + esc(w) + '</span>').join('') + '</div>' : '') + boardHTML(m.board, 3) +
                '<div class="stats two"><div class="stat"><b>' + (m.earnings ? money(m.earnings[0]) + '–' + money(m.earnings[1]) : '–') + '</b><span>a month for channels like these (estimate)</span></div>' +
                '<div class="stat"><b>' + (m.breakout_after ? '~' + m.breakout_after : '–') + '</b><span>videos before the first breakout</span></div></div>' +
                (m.proofs[0] ? proofCard(m.proofs[0]) : refs(m.references)) +
                '<button class="btn ghost" style="margin-top:12px" data-use="' + esc(m.id) + '">Make this one mine</button></div>').join('') + '<p class="note">' + esc(f.ypp_note) + '</p>';
            side('');
        } else if (step === 'make') html = makeIt();
        else if (step === 'recipe') html = recipeScreen();
        else if (step === 'share') {
            const link = location.origin + '/money-niche/r/' + f.id;
            html = '<div class="kicker">Share it</div><h2>Post your money niche</h2><p class="lede">Your card shows your #1 niche and its proof. Nothing personal.</p>' +
                '<div class="card share"><img src="/money-niche/card/' + encodeURIComponent(f.id) + '.png" alt="My money niche: ' + esc(top.name) + ', on channelrecipe.com"></div>' +
                '<div class="share-btns"><button class="btn" data-share>Share</button><button class="btn ghost" data-link>Copy link</button>' +
                '<a class="btn ghost" href="/money-niche/card/' + encodeURIComponent(f.id) + '.png" download="my-money-niche.png">Save image</a>' +
                '<a class="btn ghost" target="_blank" rel="noopener" href="https://twitter.com/intent/tweet?text=' + encodeURIComponent('My money niche: ' + top.name + '. Find yours:') + '&url=' + encodeURIComponent(link) + '">Post on X</a></div>';
        } else if (step === 'plans') html = plansScreen();
        show(html + nav(S.step === QUIZ_STEPS.length - 1));
        bindResult(() => result(), QUIZ_STEPS.length, () => taste());
    }

    // make it yours: angle -> look -> recipe (shared by both doors)
    function makeIt() {
        const nn = currentNiche();
        if (!nn) return nichePicker();
        wall((nn.board || []).filter((x) => x.clip).map((x) => x.clip).concat(ALL.slice(0, 6)));
        side('<div class="card"><h3>' + esc(nn.name) + '</h3>' + boardHTML(nn.board, 5) + '</div>');
        return '<div class="kicker">Make it yours · ' + esc(nn.name) + '</div><h2>Pick your angle</h2><p class="lede">The corner of the niche you’ll own.</p>' +
            '<div class="cards two">' + nn.angles.map((a) => '<button class="pick" data-angle="' + esc(a.id) + '" aria-pressed="' + (S.pick.angle === a.id) + '" style="padding:14px;min-height:112px">' +
                '<span style="position:relative;z-index:1;display:block"><b>' + esc(a.name) + '</b><small>“' + esc(a.title) + '”</small></span></button>').join('') + '</div>' +
            '<h2 style="margin-top:28px;font-size:30px">Pick your look</h2><p class="lede">What every video will look like.</p>' +
            '<div class="cards">' + nn.looks.map((l) => '<button class="pick row" data-look="' + esc(l.id) + '" aria-pressed="' + (S.pick.look === l.id) + '" style="min-height:120px">' +
                media(l, 'bg') + '<span class="in"><b>' + esc(l.name) + '</b><small>' + esc(l.how) + '</small></span></button>').join('') + '</div>';
    }
    function nichePicker() {
        return '<div class="kicker">Make it yours</div><h2>Which niche is yours?</h2><p class="lede">We couldn’t tell from your titles. Pick the closest.</p>' +
            '<div class="cards two">' + (S.niches || []).map((x) => '<button class="pick tall" data-niche="' + esc(x.id) + '">' + (x.board[0] ? media(x.board[0], 'bg') : '') +
                '<span class="in"><b>' + esc(x.name) + '</b></span></button>').join('') + '</div>';
    }
    function recipeOf(nn) {
        if (S.door !== 'channel') return nn.recipe;
        return {format: nn.format, kind: 'long', length: nn.length, look: nn.look, pacing: nn.pacing, voice: 'Your voice or an AI narrator', rhythm: '1 video a week, steady'};
    }
    function recipeScreen() {
        const nn = currentNiche(), r = recipeOf(nn);
        const angle = nn.angles.find((a) => a.id === S.pick.angle) || nn.angles[0];
        const look = nn.looks.find((l) => l.id === S.pick.look) || nn.looks[0];
        const titles = [...new Set([angle?.title].concat(S.door === 'channel' ? (S.check.ideas || []).map((i) => i.title) : (S.full.niches[0].id === nn.id ? S.full.titles : [])).filter(Boolean))];
        if (!S.pick.title || !titles.includes(S.pick.title)) S.pick.title = titles[0] || '';
        side('');
        return '<div class="kicker">Your recipe</div><h2>' + esc(nn.name) + (angle ? ': ' + esc(angle.name.toLowerCase()) : '') + '</h2>' +
            '<div class="recipe-card"><div class="hero">' + (look ? media(look) : (nn.board[0] ? media(nn.board[0]) : '')) +
            '<div class="t"><span class="tag">' + esc(look ? look.name : 'Your look') + '</span></div></div><div class="body">' +
            '<div class="first-title"><small>Your first video</small><b id="first-title">' + esc(S.pick.title) + '</b></div>' +
            '<dl><div><dt>Format</dt><dd>' + esc(r.format) + (r.kind === 'shorts' ? ' · Shorts' : ' · long videos') + '</dd></div>' +
            '<div><dt>Length</dt><dd>' + esc(r.length) + '</dd></div><div><dt>Look</dt><dd>' + esc(r.look) + '</dd></div>' +
            '<div><dt>Pacing</dt><dd>' + esc(r.pacing) + '</dd></div><div><dt>Voice</dt><dd>' + esc(r.voice) + '</dd></div>' +
            '<div><dt>Upload</dt><dd>' + esc(r.rhythm) + '</dd></div>' +
            '<div><dt>In Studio</dt><dd>' + esc(look ? look.how : 'Pick a style in the Style library') + '</dd></div></dl></div></div>' +
            (titles.length > 1 ? '<p class="kicker section-k">Pick your first title</p><ul class="titles">' + titles.slice(0, 6).map((t) =>
                '<li><button data-title="' + esc(t) + '" aria-pressed="' + (t === S.pick.title) + '"><span>' + esc(t) + '</span><i>' + (t === S.pick.title ? 'chosen' : 'use this') + '</i></button></li>').join('') + '</ul>' : '');
    }
    function plansScreen() {
        const nn = currentNiche(), cat = ((S.cfg && S.cfg.plan_catalog) || []).filter((t) => t.studio);
        const annual = S.cycle === 'annual', paid = S.user && S.user.plan && S.user.plan !== 'free';
        // TODO(owner decision): a free first hosted video in this niche would be offered here once a hosted video's
        // cost is known. Until then: plans (no trial) and the recipe carried into Studio.
        return '<div class="kicker">Your next step</div><h2>Make <span class="grad">' + esc(S.pick.title || nn.name) + '</span> in ChannelRecipe</h2>' +
            '<p class="lede">Studio opens with your recipe picked: ' + esc(nn.name.toLowerCase()) + ', your look, your first title. It writes, voices, animates and edits the video on your computer.</p>' +
            (paid ? '<div class="spacer"></div><button class="btn" data-go="">Open my recipe in Studio</button>' :
                ((S.cfg && S.cfg.annual_plans_available) ? '<div class="cycle"><button data-cycle="monthly" aria-pressed="' + !annual + '">Monthly</button><button data-cycle="annual" aria-pressed="' + annual + '">Yearly</button></div>' : '') +
                '<div class="plans">' + cat.map((t) => {
                    const copy = PLAN_COPY[t.tier] || {tag: '', items: []}, pop = t.tier === 'daily';
                    return '<div class="plan' + (pop ? ' pop' : '') + '">' + (pop ? '<span class="tag badge">Start here</span>' : '') +
                        '<h3>' + esc(t.label) + '</h3><p class="tagline">' + esc(copy.tag) + '</p>' +
                        '<p class="price">$' + (annual ? t.annual_usd : t.monthly_usd) + '<small> / ' + (annual ? 'year' : 'month') + '</small></p>' +
                        '<ul>' + copy.items.map((i) => '<li>' + i + '</li>').join('') + '</ul>' +
                        '<button class="btn' + (pop ? '' : ' ghost') + '" data-go="' + esc(t.tier) + '">Start with ' + esc(t.label) + '</button></div>';
                }).join('') + '</div><p class="note">Billed today, cancel anytime from Billing. Studio also needs your own Claude account and a Kie.ai or Atlas Cloud balance for voice and pictures.</p>') +
            '<label class="consent"><input type="checkbox" id="consent-final"> <span>Email me new breakout channels in my niche. About every week or two. Unsubscribe anytime.</span></label>';
    }
    function bindResult(redraw, total, before) {
        const back = stage.querySelector('[data-back]');
        if (back) back.onclick = () => { if (S.step === 0) before(); else { S.step -= 1; redraw(); } };
        const nx = stage.querySelector('[data-next]'); if (nx) nx.onclick = () => { S.step = Math.min(total - 1, S.step + 1); redraw(); };
        stage.querySelectorAll('[data-use]').forEach((b) => b.onclick = () => {
            const nn = S.full.niches.find((x) => x.id === b.dataset.use);
            S.pick = {niche: nn.id, angle: nn.angles[0]?.id || '', look: nn.looks[0]?.id || '', title: ''}; S.step = QUIZ_STEPS.indexOf('make'); redraw();
        });
        stage.querySelectorAll('[data-niche]').forEach((b) => b.onclick = () => {
            const nn = S.niches.find((x) => x.id === b.dataset.niche);
            S.pick = {niche: nn.id, angle: nn.angles[0]?.id || '', look: nn.looks[0]?.id || '', title: ''}; redraw();
        });
        stage.querySelectorAll('[data-angle]').forEach((b) => b.onclick = () => {
            S.pick.angle = b.dataset.angle; S.pick.title = '';
            stage.querySelectorAll('[data-angle]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        });
        stage.querySelectorAll('[data-look]').forEach((b) => b.onclick = () => {
            S.pick.look = b.dataset.look; stage.querySelectorAll('[data-look]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        });
        stage.querySelectorAll('[data-title]').forEach((b) => b.onclick = () => {
            S.pick.title = b.dataset.title; document.getElementById('first-title').textContent = S.pick.title;
            stage.querySelectorAll('[data-title]').forEach((x) => { const on = x === b; x.setAttribute('aria-pressed', String(on)); x.querySelector('i').textContent = on ? 'chosen' : 'use this'; });
        });
        stage.querySelectorAll('[data-cycle]').forEach((b) => b.onclick = () => { S.cycle = b.dataset.cycle; redraw(); });
        const link = S.full ? location.origin + '/money-niche/r/' + S.full.id : location.href;
        const sh = stage.querySelector('[data-share]');
        if (sh) sh.onclick = async () => {
            const t = 'My money niche: ' + S.full.niches[0].name + '. Find yours in 60 seconds.';
            if (navigator.share) { try { await navigator.share({title: t, text: t, url: link}); } catch (_) {} }
            else { try { await navigator.clipboard.writeText(link); toast('Link copied'); } catch (_) { toast(link); } }
        };
        const ln = stage.querySelector('[data-link]');
        if (ln) ln.onclick = async () => { try { await navigator.clipboard.writeText(link); toast('Link copied'); } catch (_) { toast(link); } };
        stage.querySelectorAll('[data-go]').forEach((b) => b.onclick = () => go(b, b.dataset.go));
    }
    async function go(button, tier) {
        button.disabled = true; const label = button.textContent; button.textContent = 'One moment…';
        try {
            const p = S.pick, body = {niche: p.niche, angle: p.angle, look: p.look, title: p.title};
            const r = S.door === 'channel' ? await api('/api/channel-check/' + encodeURIComponent(S.checkId) + '/choose', body)
                : await api('/api/money-niche/choose', {...body, result: S.full.id});
            if (document.getElementById('consent-final')?.checked) {
                try { await api('/api/leads', {source: 'quiz', consent: true, niche: p.niche, result: S.door === 'channel' ? '' : S.full.id}); } catch (_) {}
            }
            if (!tier) { location.href = r.next || '/app#local-studio'; return; }
            try {
                const c = await api('/api/billing/checkout', {plan: tier + '_' + S.cycle, skip_trial: true, return_to: 'local-studio'});
                if (c.url) { location.href = c.url; return; }
            } catch (e) { toast(e.message); }
            location.href = r.next || '/app#local-studio';
        } catch (e) { toast(e.message); button.disabled = false; button.textContent = label; }
    }

    // ---- door 2: "I already have a channel" -----------------------------------------------------------
    function checkStart() {
        progress(CHECK_STEPS.length + 1, 0); wall(['hand-drawn-money', 'night-vision:v', 'titanic-clock', 'paper-bank', 'luck-elevator', 'cooper-money:v', 'gladiator-sold', 'nanga-parbat-title']);
        show('<div class="kicker">Channel check · free</div><h2>Why isn’t your channel <span class="grad">growing?</span></h2>' +
            '<p class="lede">Paste your channel. We compare it with the small channels breaking out in your niche right now, then show what’s holding it back and what to make next.</p>' +
            '<div class="card lift" style="margin-top:20px"><input class="field" id="channel" placeholder="youtube.com/@yourchannel" autocomplete="url" aria-label="Your channel link">' +
            '<button class="btn" style="margin-top:10px" data-check>Check my channel</button><p class="err" id="err" role="alert"></p>' +
            '<p class="note">We read your channel’s public numbers only: views, uploads, titles. No sign-in to YouTube.</p></div>' +
            '<div class="spacer"></div><div class="nav end"><button class="btn ghost back" aria-label="Back" data-back>←</button></div>');
        side('<div class="card"><h3>What you’ll get</h3><ul class="steps"><li>Your channel as a dashboard</li><li>The breakout channels in your niche, side by side</li><li>What’s holding you back, in plain words</li><li>A step-by-step plan</li><li>5 video ideas with thumbnail ideas</li></ul></div>');
        const input = document.getElementById('channel');
        const run = () => { const v = input.value.trim(); if (!v) { document.getElementById('err').textContent = 'Paste your channel link.'; return; } checkRun(v); };
        stage.querySelector('[data-check]').onclick = run; input.onkeydown = (e) => { if (e.key === 'Enter') run(); };
        stage.querySelector('[data-back]').onclick = home; input.focus();
    }
    async function checkRun(v) {
        const stop = thinking(['Reading your channel…', 'Measuring your last 15 videos…', 'Finding your niche…', 'Comparing with channels breaking out…'],
            ['hand-drawn-money', 'titanic-clock', 'gladiator-sold', 'night-vision', 'paper-bank', 'luck-elevator']);
        try {
            const [t] = await Promise.all([api('/api/channel-check', {url: v}), new Promise((r) => setTimeout(r, 2200))]);
            S.check = t; S.checkId = t.id; history.replaceState(null, '', '/money-niche/c/' + t.id); checkTaste();
        } catch (e) { stop(); checkStart(); const er = document.getElementById('err'); if (er) er.textContent = e.message; return; }
        stop();
    }
    function channelHeader(t) {
        const c = t.channel;
        return '<div class="card"><div class="who">' + (c.avatar ? '<img src="' + url(c.avatar) + '" alt="" referrerpolicy="no-referrer">' : '') +
            '<div style="flex:1;min-width:0"><a href="' + url(c.url) + '" target="_blank" rel="noopener">' + esc(c.name) + '</a><small>' +
            (t.niche ? 'Niche: ' + esc(t.niche.name) : 'Niche: not clear from your titles') + '</small></div></div>' +
            '<div class="stats"><div class="stat"><b data-count="' + (c.hidden_subscribers ? 0 : c.subscribers) + '">0</b><span>subscribers</span></div>' +
            '<div class="stat"><b data-count="' + (t.stats.median_views || 0) + '">0</b><span>views on a usual video</span></div>' +
            '<div class="stat"><b>' + (t.stats.last_upload_days != null ? t.stats.last_upload_days + 'd' : '–') + '</b><span>since your last upload</span></div></div>' +
            chart(t.videos, t.stats.peer_views) + '<p class="small" style="margin-top:6px">Your last ' + t.videos.length + ' videos' +
            (t.stats.peer_views ? ' · dashed line: what small breakout channels in your niche get' : '') + '</p></div>';
    }
    function findingCard(f) {
        return '<div class="card finding ' + esc(f.level) + '"><span class="lvl">' + ({bad: 'Holding you back', warn: 'Worth fixing', good: 'Working'}[f.level] || '') + '</span>' +
            '<h3>' + esc(f.title) + '</h3><p>' + esc(f.detail) + '</p>' +
            (f.video ? '<div class="thumbs" style="grid-template-columns:1fr 1fr"><a href="' + url(f.video.url) + '" target="_blank" rel="noopener"><img src="' + url(f.video.thumbnail) + '" alt="" referrerpolicy="no-referrer"><b>' + n(f.video.views) + '</b> views</a></div>' : '') + '</div>';
    }
    function checkTaste() {
        const t = S.check, signedIn = !!S.user;
        progress(CHECK_STEPS.length + 1, 1);
        wall((t.board || []).filter((x) => x.clip).map((x) => x.clip).concat(['hand-drawn-money', 'titanic-clock']));
        show('<div class="kicker">Your channel check</div><h2>' + esc(t.channel.name) + '</h2>' + channelHeader(t) +
            (t.headline ? '<p class="kicker section-k">The biggest thing</p>' + findingCard(t.headline) : '') +
            (signedIn ? '<div class="spacer"></div><div class="nav"><button class="btn" data-full>See everything</button></div>'
                : gateHTML([(t.more || 0) + ' more findings about your channel', t.peers_count ? t.peers_count + ' breakout channels in your niche to study' : 'The breakout channels to study',
                    'A step-by-step plan', '5 video ideas with thumbnail ideas', 'Your recipe, ready to make in ChannelRecipe']) +
                    '<div class="nav end"><button class="btn ghost back" aria-label="Back" data-back>←</button></div>'));
        side(t.board && t.board.length ? '<div class="card"><h3>' + esc(t.niche ? t.niche.name : 'Your niche') + '</h3><p class="small">What it looks like when it’s made well</p>' + boardHTML(t.board, 4) + '</div>' : '');
        const b = stage.querySelector('[data-back]'); if (b) b.onclick = checkStart;
        if (signedIn) stage.querySelector('[data-full]').onclick = loadCheck; else bindGate(loadCheck);
    }
    async function loadCheck() {
        try {
            const f = await api('/api/channel-check/' + encodeURIComponent(S.checkId));
            if (f.gated) { S.user = null; checkTaste(); return; }
            S.check = f; S.step = 0;
            const nid = f.niche ? f.niche.id : '', nn = (S.niches || []).find((x) => x.id === nid);
            S.pick = {niche: nid, angle: nn?.angles[0]?.id || '', look: nn?.looks[0]?.id || '', title: ''};
            checkResult();
        } catch (e) { toast(e.message); }
    }
    function checkResult() {
        const f = S.check, step = CHECK_STEPS[S.step];
        progress(CHECK_STEPS.length + 1, S.step + 1);
        let html = '';
        if (step === 'diagnosis') {
            html = '<div class="kicker">Your channel check</div><h2>What’s holding ' + esc(f.channel.name) + ' back</h2>' + channelHeader(f) + f.findings.map(findingCard).join('');
            side('');
        } else if (step === 'peers') {
            html = '<div class="kicker">Study these</div><h2>Breakout channels in ' + esc(f.niche ? f.niche.name.toLowerCase() : 'your niche') + '</h2>' +
                '<p class="lede">Small channels getting big views right now. Watch their last three videos: the idea, the title, the thumbnail, the first 30 seconds.</p>' +
                (f.peers.length ? f.peers.map((p) => proofCard(p, {money: true})).join('') : '<div class="card"><p class="note">The Niche Finder hasn’t caught a small breakout channel in this niche this week. It hunts twice a day, so check back.</p></div>') +
                '<p class="note">' + esc(f.ypp_note) + '</p>';
        } else if (step === 'plan') {
            html = '<div class="kicker">Your plan</div><h2>Step by step</h2><ol class="steps">' + f.plan.map((s) => '<li>' + esc(s) + '</li>').join('') + '</ol>';
        } else if (step === 'ideas') {
            const pool = (f.board || []).length ? f.board : [{clip: 'hand-drawn-money'}, {clip: 'titanic-clock'}, {clip: 'gladiator-sold'}];
            html = '<div class="kicker">Make these next</div><h2>5 video ideas, with thumbnails</h2>' +
                '<p class="lede">' + (f.ideas_source === 'model' ? 'Built on what already worked for your channel.' : 'Proven ideas for your niche.') + '</p><div class="ideas">' +
                f.ideas.map((i, k) => '<div class="card idea" style="margin-top:0"><div class="thumb-idea">' + media(pool[k % pool.length]) + '<b>' + esc(hook(i.title)) + '</b></div>' +
                    '<div><h3>' + esc(i.title) + '</h3><p>Thumbnail: ' + esc(i.thumbnail) + '</p></div></div>').join('') + '</div>' +
                '<p class="note">The pictures are mock-ups in your niche’s look; Studio makes the real thumbnail with each video.</p>';
        } else if (step === 'make') html = makeIt();
        else if (step === 'recipe') html = recipeScreen();
        else if (step === 'plans') html = plansScreen();
        show(html + nav(S.step === CHECK_STEPS.length - 1));
        bindResult(() => checkResult(), CHECK_STEPS.length, () => checkTaste());
        if (step === 'make' && !currentNiche()) { const nx = stage.querySelector('[data-next]'); if (nx) nx.disabled = true; }
    }

    // ---- start ----------------------------------------------------------------------------------------
    async function openResult(id, own) {
        S.id = id; S.mine = own || mine().includes(id); S.door = 'zero';
        try {
            const data = await api('/api/money-niche/result/' + encodeURIComponent(id));
            if (location.pathname !== '/money-niche/r/' + id) history.replaceState(null, '', '/money-niche/r/' + id);
            if (data.gated) { S.teaser = data; taste(); }
            else {
                S.teaser = data.teaser; S.full = data; S.step = 0;
                const top = data.niches[0];
                S.pick = {niche: top.id, angle: top.angles[0]?.id || '', look: top.looks[0]?.id || '', title: ''};
                result();
            }
        } catch (e) { home(); toast(e.message); }
    }
    async function openCheck(id) {
        S.door = 'channel'; S.checkId = id;
        try {
            const data = await api('/api/channel-check/' + encodeURIComponent(id));
            S.check = data;
            if (data.gated) checkTaste();
            else { S.step = 0; const nn = (S.niches || []).find((x) => x.id === data.niche?.id); S.pick = {niche: data.niche?.id || '', angle: nn?.angles[0]?.id || '', look: nn?.looks[0]?.id || '', title: ''}; checkResult(); }
        } catch (e) { home(); toast(e.message); }
    }
    (async function boot() {
        try { window.CRTelemetry?.captureAttributionFromUrl?.(); } catch (_) {}
        const [me, cfg, pulse, niches] = await Promise.all([api('/api/auth/me').catch(() => ({})), api('/api/config').catch(() => null),
            api('/api/money-niche/pulse').catch(() => null), api('/api/money-niche/niches').catch(() => [])]);
        S.user = me.user || null; S.cfg = cfg; S.pulse = pulse; S.niches = niches;
        const r = location.pathname.match(/^\/money-niche\/r\/([A-Za-z0-9_-]{6,24})$/);
        const c = location.pathname.match(/^\/money-niche\/c\/(cc_[A-Za-z0-9_-]{6,20})$/);
        if (r) openResult(r[1], false); else if (c) openCheck(c[1]); else home();
        let wasDesktop = desktop();
        window.addEventListener('resize', () => { if (desktop() !== wasDesktop) { wasDesktop = desktop(); wallKey = ''; wall(); } });
    })();
})();
