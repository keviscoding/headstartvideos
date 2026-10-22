/* Find My Money Niche: two doors on one page, dressed like the places creators already live (a dark home feed,
   channel pages, Studio-style analytics), built from public numbers and labelled as estimates.
   - Starting from zero: four questions -> niche #1 as a channel page + analytics -> sign in -> the full result and
     a feed of viral niches -> make it yours -> your recipe -> plans.
   - I have a channel: paste it -> a dashboard of it + the biggest problem -> sign in -> every finding, channels to
     study, a plan, five ideas -> make it yours -> your recipe -> plans.
   ChannelRecipe's own look (Studio clips, the recipe card, plans) only arrives at "make it yours".
   Server: webapp/money_niche.py, webapp/channel_check.py, webapp/money_niche_web.py */
(function () {
    'use strict';
    const stage = document.getElementById('stage'), bar = document.getElementById('ytbar');
    const MINE = 'cr_money_niche_results', MB = '/static/moodboard/';
    const still = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const ICON = {
        camera: '<path d="M17 10.5V7a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3.5l4 4v-11l-4 4z"/>',
        mic: '<path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z"/>',
        robot: '<path d="M12 2a2 2 0 0 1 1 3.73V7h4a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-8a3 3 0 0 1 3-3h4V5.73A2 2 0 0 1 12 2zM9 12a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/>',
        shorts: '<path d="M17.77 10.32l-1.2-.5L18 9.06a3.74 3.74 0 0 0-3.5-6.62L6 6.94a3.74 3.74 0 0 0 .23 6.74l1.2.49L6 14.93a3.75 3.75 0 0 0 3.5 6.63l8.5-4.5a3.74 3.74 0 0 0-.23-6.74zM10 14.65V9.35L15 12l-5 2.65z"/>',
        clock: '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 10.41V6h-2v7.24l4.62 2.77 1-1.66L13 12.41z"/>',
        fire: '<path d="M13.5.67s.74 2.65.74 4.8c0 2.06-1.35 3.73-3.41 3.73-2.07 0-3.63-1.67-3.63-3.73l.03-.36C5.21 7.51 4 10.62 4 14a8 8 0 0 0 16 0c0-5.39-2.59-10.2-6.5-13.33z"/>',
        rocket: '<path d="M12 2.5s4.5 2 4.5 9.5l2.5 2.5v4l-4-2.5h-6L5 18.5v-4L7.5 12C7.5 4.5 12 2.5 12 2.5zm0 6a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zM10 20h4l-2 2.5z"/>',
        free: '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15.9V19h-2v-1.1A4 4 0 0 1 8 14h2a2 2 0 1 0 2-2 4 4 0 0 1-1-7.9V3h2v1.1A4 4 0 0 1 16 8h-2a2 2 0 1 0-2 2 4 4 0 0 1 1 7.9z"/>',
        image: '<path d="M21 19V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2zM8.5 13.5l2.5 3 3.5-4.5 4.5 6H5l3.5-4.5z"/>',
        cube: '<path d="M12 2 3 7v10l9 5 9-5V7l-9-5zm0 2.3L18.6 8 12 11.7 5.4 8 12 4.3zM5 9.7l6 3.4v6.6l-6-3.3V9.7zm8 10v-6.6l6-3.4v6.7l-6 3.3z"/>',
        film: '<path d="M18 3v2h-2V3H8v2H6V3H4v18h2v-2h2v2h8v-2h2v2h2V3h-2zM8 17H6v-2h2v2zm0-4H6v-2h2v2zm0-4H6V7h2v2zm10 8h-2v-2h2v2zm0-4h-2v-2h2v2zm0-4h-2V7h2v2z"/>',
        search: '<path d="M20.87 20.17l-5.59-5.59A7.46 7.46 0 0 0 17 9.5 7.5 7.5 0 1 0 9.5 17c1.95 0 3.72-.75 5.08-1.98l5.59 5.59.7-.44zM3 9.5A6.5 6.5 0 1 1 9.5 16 6.5 6.5 0 0 1 3 9.5z"/>',
        play: '<path d="M8 5v14l11-7z"/>',
    };
    const icon = (k) => '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICON[k] + '</svg>';

    const QUESTIONS = [
        {key: 'face', title: 'Will you show your face?', lede: 'Plenty of channels never do. This changes which niches fit, and who does the talking.',
         options: [['camera', 'camera', 'Yes, on camera', 'Presenter channels open up'], ['voice', 'mic', 'Voice only', 'Your voice over the pictures'],
                   ['none', 'robot', 'No, neither', 'Fully faceless, AI voice']]},
        {key: 'hours', title: 'How many hours a week can you give it?', lede: 'This decides Shorts or long videos, and how often you upload.',
         options: [['under3', 'shorts', 'Under 3 hours', 'Shorts, a few a week'], ['3to8', 'clock', '3 to 8 hours', 'About one long video a week'],
                   ['8to15', 'fire', '8 to 15 hours', 'A serious side project'], ['over15', 'rocket', '15+ hours', 'Close to a part-time job']]},
        {key: 'loves', title: 'What would you click on?', lede: 'Pick up to three. You make better videos about what you’d watch anyway.', multi: true,
         options: [['mysteries', 'I2O7blSSzpI', 'Mysteries & documentaries', 'LEMMiNO', '42M'], ['disasters', 'wLFY_Zu_O08', 'How disasters happen', 'Fern', '15M'],
                   ['money', 'Ha2oNXDZuSg', 'Money & business', 'Modern MBA', '2.2M'], ['crime', 'NrTaqT_PgmI', 'True crime & bodycam', 'Bodycam Verdicts', '745K'],
                   ['history', 'd3tXkJkgbQw', 'History', 'Simple History', '1.9M'], ['stories', 't8JEQXEAKSs', 'Stories & anime', 'Tobs Manhwa', '865K'],
                   ['mind', 'sNg5FXt_DDM', 'Psychology & big ideas', 'Academy of Ideas', '786K'], ['world', 'yIZRffLA294', 'Maps & world politics', 'Wendover Productions', '3.9M'],
                   ['tips', 'wR1l5k8CKgg', 'Life tips & real skills', 'Frank Miller Cuts', '491K']]},
        {key: 'budget', title: 'Monthly budget for AI tools?', lede: 'For voices, pictures and animation. This decides which formats fit.',
         options: [['free', 'free', '$0', 'Stock footage, archive and paintings'], ['low', 'image', 'Up to $30', 'AI pictures, animated'],
                   ['mid', 'cube', '$30 to $100', '3D scenes or an AI presenter'], ['high', 'film', '$100+', 'Cinematic AI video']]},
    ];
    const QUIZ_STEPS = ['niche', 'viral', 'make', 'recipe', 'share', 'plans'];
    const CHECK_STEPS = ['dashboard', 'peers', 'plan', 'ideas', 'make', 'recipe', 'plans'];
    const PLAN_COPY = {   // the same words as the Studio page's plan cards
        daily: {tag: 'Start making videos', items: ['Studio + every built-in style', 'Videos render on <b>your own computer</b>', 'Clone <b>3</b> channel styles', '35 cloud credits / month for Classic recipes', 'Niche Finder + niche database in Claude']},
        studio_pro: {tag: 'Run several channels', items: ['Everything in Creator', 'Videos render on <b>your own computer</b>', 'Clone <b>15</b> channel styles', '60 cloud credits / month for Classic recipes', '3 Classic cloud videos at once']},
        studio_max: {tag: 'For serious operators', items: ['Everything in Pro', 'Videos render on <b>your own computer</b>', '<b>Unlimited</b> style clones', '100 cloud credits / month for Classic recipes', 'Priority help in Discord']},
    };
    const S = {answers: {loves: []}, q: 0, user: null, cfg: null, pulse: null, board: [], niches: [], teaser: null, full: null, id: '', step: 0,
        mine: false, pick: {}, check: null, checkId: '', cycle: 'monthly', door: ''};

    // ---- helpers --------------------------------------------------------------------------------------
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const url = (u) => /^https:\/\//.test(String(u || '')) ? esc(u) : '#';
    const name = (c) => String(c || '').replace(/[^a-z0-9-]/g, '');
    const n = (x) => {
        x = Number(x) || 0;
        if (x >= 1e6) return (x / 1e6).toFixed(x >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
        if (x >= 1e3) return (x >= 1e4 ? Math.round(x / 1e3) : (x / 1e3).toFixed(1).replace(/\.0$/, '')) + 'K';
        return String(Math.round(x));
    };
    const money = (x) => '$' + Number(x).toLocaleString('en-US');
    const ago = (iso) => {
        const t = Date.parse(iso || ''); if (!t) return '';
        const d = (Date.now() - t) / 86400000;
        if (d < 1) return 'today'; if (d < 2) return '1 day ago'; if (d < 7) return Math.floor(d) + ' days ago';
        if (d < 14) return '1 week ago'; if (d < 31) return Math.floor(d / 7) + ' weeks ago';
        if (d < 61) return '1 month ago'; if (d < 365) return Math.floor(d / 30) + ' months ago';
        return Math.floor(d / 365) + (d < 730 ? ' year ago' : ' years ago');
    };
    const dur = (s) => { s = Number(s) || 0; if (!s) return ''; const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = String(s % 60).padStart(2, '0');
        return h ? h + ':' + String(m).padStart(2, '0') + ':' + x : m + ':' + x; };
    const months = (m) => m ? m + (m === 1 ? ' month' : ' months') : '';
    const mine = () => { try { return JSON.parse(localStorage.getItem(MINE) || '[]'); } catch (_) { return []; } };
    const remember = (id) => { try { localStorage.setItem(MINE, JSON.stringify([id, ...mine().filter((x) => x !== id)].slice(0, 10))); } catch (_) {} };
    const STOP = new Set('a an the of in on to for and or but is are was were be it its this that with without how why what when who your you my i we they he she his her their from at by as into than then just about after before do does did not no never every most more one two like if live lived would could really'.split(' '));
    const hook = (t) => String(t || '').replace(/[^A-Za-z0-9' ]/g, ' ').split(/\s+/).filter((w) => w && !STOP.has(w.toLowerCase())).slice(0, 2).join(' ').toUpperCase();
    function toast(text) { const t = document.createElement('div'); t.className = 'toast'; t.textContent = text; document.body.appendChild(t); setTimeout(() => t.remove(), 2600); }
    async function api(path, body) {
        const res = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw Object.assign(new Error(data.detail || 'Something went wrong. Try again.'), {status: res.status});
        return data;
    }
    function progress(k) { bar.style.width = Math.max(0, Math.min(1, k)) * 100 + '%'; }
    function show(html, wide) {
        stage.innerHTML = '<section class="screen' + (wide ? '' : ' narrow') + '">' + html + '</section>';
        window.scrollTo({top: 0});
        const h = stage.querySelector('h1, h2'); if (h) { h.tabIndex = -1; h.focus({preventScroll: true}); }
        requestAnimationFrame(() => setTimeout(() => stage.querySelectorAll('[data-h]').forEach((el) => { el.style.height = el.dataset.h; }), 60));
        stage.querySelectorAll('video').forEach((v) => { v.muted = true; v.play?.().catch(() => {}); });
    }

    // ---- what creators already know: video cards, channel pages, analytics ---------------------------------
    function videoCard(v, ch, hot) {
        return '<a class="yv" href="' + url(v.url) + '" target="_blank" rel="noopener"><div class="th">' +
            (v.thumbnail ? '<img src="' + url(v.thumbnail) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '') +
            (dur(v.seconds) ? '<span class="dur">' + dur(v.seconds) + '</span>' : '') + '</div><div class="meta">' +
            (ch && ch.avatar ? '<img class="av" src="' + url(ch.avatar) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '') +
            '<div><h4>' + esc(v.title || '') + '</h4>' + (ch && ch.name ? '<p>' + esc(ch.name) + '</p>' : '') +
            '<p>' + n(v.views) + ' views' + (ago(v.published_at) ? ' · ' + ago(v.published_at) : '') + '</p>' +
            (hot ? '<span class="hot">' + esc(hot) + '</span>' : '') + '</div></div></a>';
    }
    const hotLine = (c) => [c.months ? months(c.months) + ' old' : '', c.subscribers ? n(c.subscribers) + ' subs' : ''].filter(Boolean).join(' · ');
    function channelPage(p) {
        const vids = (p.recent || []).filter((v) => v.thumbnail || v.title);
        return '<div class="ych"><div class="banner" style="background-image:url(\'' + url(vids[0]?.thumbnail || p.avatar || '') + '\')"></div>' +
            '<div class="head">' + (p.avatar ? '<img class="av" src="' + url(p.avatar) + '" alt="" referrerpolicy="no-referrer">' : '<span></span>') +
            '<div class="info"><h3>' + esc(p.name) + '</h3><p>' + [p.subscribers ? n(p.subscribers) + ' subscribers' : '', p.videos ? p.videos + ' videos' : ''].filter(Boolean).join(' · ') + '</p>' +
            (p.months ? '<p><span class="chip ok">Started ' + months(p.months) + ' ago</span></p>' : '') + '</div>' +
            '<a class="btn" href="' + url(p.url) + '" target="_blank" rel="noopener">View channel</a></div>' +
            '<div class="tabs"><span class="on">Videos</span><span>Latest</span></div>' +
            '<div class="grid two">' + vids.slice(0, 4).map((v) => videoCard(v, null)).join('') + '</div></div>';
    }
    function lineChart(values, peer) {
        const vals = values.map((v) => Number(v) || 0); if (vals.length < 2) return '';
        const top = Math.max(...vals, peer || 0, 1) * 1.1, W = 300, H = 100;
        const pts = vals.map((v, i) => [i / (vals.length - 1) * W, H - v / top * H]);
        const line = pts.map((p) => p.map((x) => x.toFixed(1)).join(',')).join(' ');
        return '<svg class="chart" viewBox="0 0 300 100" preserveAspectRatio="none" role="img" aria-label="Views by video">' +
            '<defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3ea6ff" stop-opacity=".35"/><stop offset="1" stop-color="#3ea6ff" stop-opacity="0"/></linearGradient></defs>' +
            '<path class="area" d="M0,100 L' + line.replace(/ /g, ' L') + ' L300,100 Z"/><polyline class="ln" points="' + line + '"/>' +
            (peer ? '<line class="peer" x1="0" x2="300" y1="' + (H - peer / top * H).toFixed(1) + '" y2="' + (H - peer / top * H).toFixed(1) + '"/>' : '') + '</svg>';
    }
    function niceAnalytics(nn, f) {   // the niche's proof channels as one Studio-style analytics card
        const ps = nn.proofs || [], vids = ps.flatMap((p) => p.recent || []).filter((v) => v.published_at).sort((a, b) => Date.parse(a.published_at) - Date.parse(b.published_at));
        const avg = ps.length ? Math.round(ps.reduce((s, p) => s + p.recent_avg_views, 0) / ps.length) : 0;
        const subs = ps.length ? ps.map((p) => p.subscribers).sort((a, b) => a - b)[Math.floor(ps.length / 2)] : 0;
        if (!ps.length) return '';
        return '<div class="studio"><div class="top"><h4>Channel analytics · channels like these</h4><span class="src">Public numbers</span></div>' +
            '<div class="kpis"><div class="on"><small>Views</small><b>' + n(avg) + '</b><i class="up">per recent video</i></div>' +
            '<div><small>Subscribers</small><b>' + n(subs) + '</b><i>typical channel</i></div>' +
            '<div><small>Revenue</small><b>' + (nn.earnings ? money(nn.earnings[0]) + '+' : '–') + '</b><i>estimated, a month</i></div></div>' +
            lineChart(vids.map((v) => v.views)) + '<div class="chart-x"><span>' + esc(ago(vids[0]?.published_at) || '') + '</span><span>' + esc(ago(vids[vids.length - 1]?.published_at) || 'now') + '</span></div>' +
            '<div class="legend"><span><i></i>Views on each new video, ' + ps.length + ' small channel' + (ps.length > 1 ? 's' : '') + ' in this niche</span></div></div>';
    }
    function realtime(p) {
        const vids = (p.recent || []).slice().reverse(), top = Math.max(...vids.map((v) => v.views || 0), 1);
        return '<div class="studio realtime"><div class="top"><h4>Realtime</h4><span class="live">Public numbers</span></div>' +
            '<p class="big">' + n(p.subscribers) + '</p><p class="small">Subscribers · ' + esc(p.name) + '</p>' +
            '<div class="mini-bars">' + vids.map((v) => '<i data-h="' + Math.max(4, Math.round((v.views || 0) / top * 100)) + '%"></i>').join('') + '</div>' +
            '<p class="small" style="margin-top:6px"><b style="color:var(--text)">' + n(p.recent_avg_views) + '</b> views per video, latest ' + vids.length + '</p></div>';
    }
    function topContent(vids, title) {
        const list = (vids || []).slice().sort((a, b) => (b.views || 0) - (a.views || 0)).slice(0, 4);
        if (!list.length) return '';
        return '<div class="studio"><div class="top"><h4>' + esc(title || 'Top content') + '</h4><span class="src">Views</span></div><div class="toplist">' +
            list.map((v, i) => '<a href="' + url(v.url) + '" target="_blank" rel="noopener"><em>' + (i + 1) + '</em>' +
                (v.thumbnail ? '<img src="' + url(v.thumbnail) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '<span></span>') +
                '<span>' + esc(v.title) + '</span><b>' + n(v.views) + '</b></a>').join('') + '</div></div>';
    }
    function rangeBar(r) {
        if (!r) return '';
        const max = Math.max(r[1] * 1.25, 100), a = r[0] / max * 100, b = r[1] / max * 100;
        return '<div class="range"><i style="left:' + a + '%;width:' + Math.max(2, b - a) + '%"></i><span style="left:' + a + '%">' + money(r[0]) +
            '</span><span style="left:' + b + '%">' + money(r[1]) + '</span><small style="left:' + a + '%">low</small><small style="left:' + b + '%">high</small></div>';
    }
    function money_card(nn, f) {
        return '<div class="studio"><div class="top"><h4>Estimated revenue · channels like these</h4><span class="src">Estimate</span></div>' +
            (nn.earnings ? rangeBar(nn.earnings) : '<p class="small" style="margin-top:10px">Not enough recent numbers to estimate yet.</p>') +
            '<div class="kpis" style="border-bottom:0"><div><small>RPM</small><b>$' + nn.rpm[0] + '–$' + nn.rpm[1] + '</b><i>per 1,000 views</i></div>' +
            '<div><small>First breakout</small><b>' + (nn.breakout_after ? '~' + nn.breakout_after : '–') + '</b><i>videos in</i></div>' +
            '<div><small>Format</small><b style="font-size:16px;white-space:normal">' + esc(nn.recipe ? nn.recipe.kind === 'shorts' ? 'Shorts' : 'Long' : 'Long') + '</b><i>&nbsp;</i></div></div>' +
            (nn.breakout_after ? '<div class="roadmap">' + Array.from({length: Math.min(nn.breakout_after, 40)}, (_, i) => '<i class="' + (i === Math.min(nn.breakout_after, 40) - 1 ? 'b' : '') + '"></i>').join('') + '</div>' : '') +
            (nn.shorts_rpm ? '<p class="note">Shorts pay far less: about <b>$' + nn.shorts_rpm[0] + '–$' + nn.shorts_rpm[1] + '</b> per 1,000 views.</p>' : '') +
            '<p class="note">' + esc(f.estimate_note) + '</p><p class="note"><b>' + esc(f.ypp_note) + '</b></p></div>';
    }
    function refCards(list) {
        if (!list || !list.length) return '';
        return '<div class="sec"><div class="sec-h"><h3>Established channels in this niche</h3></div><p class="small" style="margin-bottom:12px">Bigger and older, so not “new channel” proof, but the format clearly works.</p>' +
            '<div class="grid">' + list.map((r) => videoCard({url: r.url, thumbnail: r.thumbnail, title: r.name + ': its breakout video', views: parseViews(r.views_label)}, {name: r.name})).join('') + '</div></div>';
    }
    const parseViews = (label) => { const m = /([\d.]+)\s*([KM]?)/i.exec(label || ''); if (!m) return 0; return parseFloat(m[1]) * (m[2].toUpperCase() === 'M' ? 1e6 : m[2].toUpperCase() === 'K' ? 1e3 : 1); };
    function skeleton(title, lines) {
        show('<h2 id="think">' + esc(title) + '</h2><p class="sub" id="think2">' + esc(lines[0] || '') + '</p><div class="grid sec">' +
            Array.from({length: 6}, () => '<div><div class="skel" style="aspect-ratio:16/9"></div><div style="display:flex;gap:12px;margin-top:12px"><div class="skel" style="width:36px;height:36px;border-radius:50%"></div><div style="flex:1"><div class="skel" style="height:14px"></div><div class="skel" style="height:12px;width:60%;margin-top:8px"></div></div></div></div>').join('') + '</div>', true);
        let k = 1; const t = setInterval(() => { const el = document.getElementById('think2'); if (el && k < lines.length) el.textContent = lines[k++]; }, 650);
        return () => clearInterval(t);
    }

    // ---- home ---------------------------------------------------------------------------------------
    function home() {
        S.door = ''; progress(0); history.replaceState(null, '', '/money-niche');
        const p = S.pulse || {}, vids = p.videos || [], last = mine()[0];
        const feed = vids.length ? vids.map((v) => videoCard(v, {name: v.channel, avatar: v.avatar}, hotLine(v)))
            : S.niches.map((x) => { const b = (x.board || []).find((i) => i.still); return b ? videoCard({url: b.url, thumbnail: b.still, title: x.name, views: parseViews(b.views)}, {name: b.channel}) : ''; });
        const lb = S.board.slice(0, 10);
        const board = lb.length ? '<div class="sec"><div class="sec-h"><div><h2>Best niches on YouTube right now</h2><p class="small" style="margin-top:4px">From ChannelRecipe’s Niche Finder: new, small channels getting big views, sorted by what they plausibly earn.</p></div></div>' +
            '<div class="lb">' + lb.map((t, i) => topicRow(t, i + 1, i === 0)).join('') + '</div><p class="note">Earnings are estimates from public views and typical ad rates, for channels in the Partner Program. New channels earn nothing from ads until they join it.</p></div>' : '';
        show('<div class="hero narrow" style="margin-left:0"><h1>What channel should you make?</h1>' +
            '<p class="sub" style="margin-top:0">And would it actually make money? Find the niche that fits you, or find out why your channel isn’t growing.</p>' +
            (p.week ? '<span class="pulse"><span><b>' + p.week + '</b> small channels broke out this week</span></span>' : '') + '</div>' +
            '<div class="doors"><button class="door" data-door="zero"><span class="ic">' + icon('play') + '</span><span><b>I’m starting from zero</b><small>Find my money niche · 4 taps</small></span></button>' +
            '<button class="door alt" data-door="channel"><span class="ic">' + icon('search') + '</span><span><b>I already have a channel</b><small>Find out why it’s not growing, and fix it</small></span></button></div>' +
            board + '<div class="sec"><div class="chips"><span class="chip on">' + (vids.length ? 'Breaking out this week' : 'Proven niches') + '</span><span class="chip">Small channels</span><span class="chip">Big views</span><span class="chip">Faceless</span></div>' +
            '<div class="grid wide" style="margin-top:12px">' + feed.join('') + '</div></div>' +
            (last ? '<div class="nav end"><button class="btn tonal" data-last>See my last result</button></div>' : ''), true);
        stage.querySelector('[data-door="zero"]').onclick = () => { S.door = 'zero'; S.q = 0; question(); };
        stage.querySelector('[data-door="channel"]').onclick = () => { S.door = 'channel'; checkStart(); };
        const l = stage.querySelector('[data-last]'); if (l) l.onclick = () => openResult(last, true);
        bindRows();
    }
    // one row of the leaderboard: the niche, its money and age, and (opened) the videos behind it
    function topicRow(t, rank, open) {
        const thumbs = t.channels.flatMap((c) => (c.recent || []).slice(0, 1)).filter((v) => v.thumbnail).slice(0, 3);
        const earn = t.earnings ? '<b class="lb-money">' + moneyK(t.earnings[0]) + '–' + moneyK(t.earnings[1]) + '</b><small>a month, typical channel (est.)</small>' : '<small>views only</small>';
        return '<div class="lb-row' + (open ? ' open' : '') + '"><button class="lb-head" data-row aria-expanded="' + !!open + '"><span class="lb-rank">' + rank + '</span>' +
            '<span class="lb-thumbs">' + thumbs.map((v) => '<img src="' + url(v.thumbnail) + '" alt="" loading="lazy" referrerpolicy="no-referrer">').join('') + '</span>' +
            '<span class="lb-name"><b>' + esc(t.label) + '</b><small>' + t.count + ' new channels · ' + n(t.views) + ' views per video' + (t.youngest_months ? ' · youngest ' + months(t.youngest_months) + ' old' : '') + '</small></span>' +
            '<span class="lb-right">' + earn + '</span></button>' +
            '<div class="lb-body"><div class="shelf">' + t.channels.flatMap((c) => (c.recent || []).slice(0, 2).map((v) => videoCard(v, c, hotLine(c) + (c.earnings ? ' · est. ' + moneyK(c.earnings[0]) + '–' + moneyK(c.earnings[1]) + '/mo' : '')))).join('') + '</div></div></div>';
    }
    const moneyK = (x) => x >= 1000 ? '$' + (x / 1000).toFixed(x >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'K' : '$' + Math.round(x);
    function bindRows() {
        stage.querySelectorAll('[data-row]').forEach((b) => b.onclick = () => { const row = b.parentElement, on = !row.classList.contains('open');
            row.classList.toggle('open', on); b.setAttribute('aria-expanded', String(on)); });
    }

    // ---- door 1: the quiz -----------------------------------------------------------------------------
    function question() {
        const q = QUESTIONS[S.q];
        progress(S.q / 4 * 0.45);
        const picked = (v) => q.multi ? S.answers.loves.includes(v) : S.answers[q.key] === v;
        const body = q.multi
            ? '<div class="grid two sec" style="margin-top:18px">' + q.options.map(([v, vid, label, ch, views]) =>
                '<button type="button" class="yv pickable" data-v="' + v + '" aria-pressed="' + picked(v) + '"><div class="th"><img src="https://i.ytimg.com/vi/' + vid + '/mqdefault.jpg" alt="" loading="lazy" referrerpolicy="no-referrer">' +
                '<i class="tick" aria-hidden="true">' + (picked(v) ? '✓' : '') + '</i></div><div class="meta"><div><h4>' + esc(label) + '</h4><p>' + esc(ch) + ' · ' + views + ' views</p></div></div></button>').join('') + '</div>'
            : '<div class="opts">' + q.options.map(([v, ic, title, sub]) => '<button type="button" class="opt" data-v="' + v + '" aria-pressed="' + picked(v) + '">' +
                '<span class="ic">' + icon(ic) + '</span><span><b>' + esc(title) + '</b><small>' + esc(sub) + '</small></span></button>').join('') + '</div>';
        show('<p class="small">Question ' + (S.q + 1) + ' of 4</p><h2 style="margin-top:4px">' + esc(q.title) + '</h2><p class="sub">' + esc(q.lede) + '</p>' + body +
            '<div class="spacer"></div><div class="nav"><button class="btn tonal back" type="button" aria-label="Back" data-back>←</button>' +
            '<button class="btn big blue" type="button" data-next' + (ready(q) ? '' : ' disabled') + '>' + (S.q === 3 ? 'Show my niche' : 'Next') + '</button></div>');
        stage.querySelector('[data-back]').onclick = () => { if (S.q === 0) home(); else { S.q -= 1; question(); } };
        stage.querySelector('[data-next]').onclick = next;
        stage.querySelectorAll('[data-v]').forEach((b) => b.onclick = () => {
            const v = b.dataset.v;
            if (q.multi) {
                const list = S.answers.loves;
                if (list.includes(v)) list.splice(list.indexOf(v), 1); else if (list.length < 3) list.push(v); else { toast('Up to three: untick one first'); return; }
                b.setAttribute('aria-pressed', String(list.includes(v))); b.querySelector('.tick').textContent = list.includes(v) ? '✓' : '';
                stage.querySelector('[data-next]').disabled = !list.length;
            } else {
                S.answers[q.key] = v; stage.querySelectorAll('[data-v]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); setTimeout(next, 250);
            }
        });
    }
    const ready = (q) => q.multi ? S.answers.loves.length > 0 : !!S.answers[q.key];
    function next() { if (!ready(QUESTIONS[S.q])) return; if (S.q < 3) { S.q += 1; question(); } else submit(); }
    async function submit() {
        progress(0.5);
        const stop = skeleton('Finding your niche…', ['Checking 15 proven niches', 'Searching the Niche Finder for small channels with big views', 'Working out what they earn']);
        try {
            const [t] = await Promise.all([api('/api/money-niche/quiz', S.answers), new Promise((r) => setTimeout(r, 1800))]);
            S.teaser = t; S.id = t.id; S.mine = true; remember(t.id); history.replaceState(null, '', '/money-niche/r/' + t.id); taste();
        } catch (e) {
            show('<h2>That didn’t work</h2><p class="sub">' + esc(e.message) + '</p><div class="nav end"><button class="btn big blue" data-retry>Try again</button></div>');
            stage.querySelector('[data-retry]').onclick = submit;
        } finally { stop(); }
    }

    // ---- the taste, then the sign-in ----------------------------------------------------------------
    function gateHTML(title, lines) {
        return '<div class="gate"><h3>' + esc(title) + '</h3><ul>' + lines.map((l) => '<li>' + esc(l) + '</li>').join('') + '</ul>' +
            '<div class="google" id="google-btn" hidden></div><div class="or" id="google-or" hidden>or</div>' +
            '<div id="email-step"><input class="field" id="email" type="email" autocomplete="email" placeholder="you@example.com" aria-label="Your email">' +
            '<button class="btn big blue" style="margin-top:8px" data-send>Email me a sign-in code</button></div>' +
            '<div id="code-step" hidden><p class="small" id="code-to"></p><input class="field code" id="code" inputmode="numeric" maxlength="6" placeholder="000000" aria-label="6-digit code" style="margin-top:8px">' +
            '<div class="row"><button class="btn tonal" data-other>Back</button><button class="btn blue" style="flex:1" data-verify>Show me</button></div></div>' +
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
        window.CRGoogle?.mount(document.getElementById('google-btn'), {theme: 'filled_black', width: Math.min(360, stage.clientWidth - 40),
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
        const t = S.teaser, signedIn = !!S.user, theirs = !S.mine && !signedIn, p = t.proof;
        progress(0.55);
        let proof = '';
        if (p && p.kind === 'fresh') {
            const ch = {...p, recent: p.recent || [], videos: p.videos};
            proof = '<div class="studio-grid">' + realtime(ch) + topContent(ch.recent, 'Top content · ' + p.name) + '</div>' + channelPage(ch);
        } else if (p) proof = refCards([{name: p.name, url: p.url, views_label: p.views_label, thumbnail: p.thumbnail}]);
        const peek = (t.peek || []).length ? '<div class="sec"><div class="sec-h"><h3>' + (t.viral_count || t.peek.length) + ' more viral niches matched to you</h3></div><div class="shelf">' +
            t.peek.map((x) => '<div class="yv"><div class="th"><img src="' + url(x.thumbnail) + '" alt="" loading="lazy" referrerpolicy="no-referrer"></div><div class="meta"><div><h4>' + esc(x.name) + '</h4><p>' + n(x.views) + ' views on a recent video</p></div></div></div>').join('') + '</div></div>' : '';
        let gate;
        if (theirs) gate = '<div class="nav end"><a class="btn big blue" href="/money-niche">Find my own money niche</a></div>';
        else if (signedIn) gate = '<div class="nav"><button class="btn big blue" data-full>See my full result</button></div>';
        else gate = gateHTML('Sign in to see your full result', [(t.viral_count || 2) + ' more viral niches, with the channels and videos behind them', 'What channels like these plausibly earn',
            'How many videos they needed before their first breakout', 'Your recipe: format, look, pacing and your first title']);
        show('<p class="small">' + (theirs ? 'Their #1 money niche' : 'Your #1 money niche') + '</p><h1 style="margin-top:4px">' + esc(t.niche.name) + '</h1><p class="sub">' + esc(t.niche.pitch) + '</p>' +
            (t.why && t.why.length ? '<div class="chips" style="margin-top:12px">' + t.why.map((w) => '<span class="chip ok">✓ ' + esc(w) + '</span>').join('') + '</div>' : '') +
            '<div class="sec"><div class="sec-h"><h3>A small channel doing it right now</h3></div>' + proof + '</div>' + peek + gate, true);
        if (signedIn) stage.querySelector('[data-full]').onclick = loadFull;
        if (!theirs && !signedIn) bindGate(loadFull);
    }
    async function loadFull() {
        try {
            const f = await api('/api/money-niche/result/' + encodeURIComponent(S.id));
            if (f.gated) { S.user = null; taste(); return; }
            S.full = f; S.step = 0; const top = f.niches[0];
            S.pick = {niche: top.id, angle: top.angles[0]?.id || '', look: top.looks[0]?.id || '', title: ''};
            result();
        } catch (e) { toast(e.message); }
    }

    // ---- the full result ------------------------------------------------------------------------------
    const currentNiche = () => S.door === 'channel' ? S.niches.find((x) => x.id === S.pick.niche)
        : (S.full.niches.find((x) => x.id === S.pick.niche) || S.niches.find((x) => x.id === S.pick.niche) || S.full.niches[0]);
    function nav(last) {
        return last ? '<div class="nav end"><button class="btn tonal back" aria-label="Back" data-back>←</button></div>'
            : '<div class="nav"><button class="btn tonal back" aria-label="Back" data-back>←</button><button class="btn big blue" data-next>Next</button></div>';
    }
    function shelf(title, sub, proofs, pick, badge) {
        const cards = proofs.flatMap((p) => (p.recent || []).slice(0, 3).map((v) => videoCard(v, p, hotLine(p))));
        if (!cards.length) return '';
        return '<div class="sec"><div class="sec-h"><div><h3>' + (badge ? '<span class="chip ok" style="margin-right:8px;font-size:12px;padding:3px 8px">' + esc(badge) + '</span>' : '') + esc(title) + '</h3>' +
            '<p class="small" style="margin-top:4px">' + esc(sub) + '</p></div>' + (pick ? '<button class="btn tonal" data-use="' + esc(pick) + '">Make this mine</button>' : '') + '</div>' +
            '<div class="shelf">' + cards.join('') + '</div></div>';
    }
    const shelfLine = (x) => [x.proofs.length + ' small channel' + (x.proofs.length > 1 ? 's' : ''),
        n(Math.round(x.proofs.reduce((s, p) => s + p.recent_avg_views, 0) / x.proofs.length)) + ' views per video',
        x.earnings ? 'est. ' + money(x.earnings[0]) + '–' + money(x.earnings[1]) + '/mo' : ''].filter(Boolean).join(' · ');
    function result() {
        const f = S.full, step = QUIZ_STEPS[S.step], top = f.niches[0];
        progress(0.6 + S.step / QUIZ_STEPS.length * 0.4);
        let html = '', wide = true;
        if (step === 'niche') {
            html = '<p class="small">Your #1 money niche</p><h1 style="margin-top:4px">' + esc(top.name) + '</h1><p class="sub">' + esc(top.pitch) + '</p>' +
                (top.why.length ? '<div class="chips" style="margin-top:12px">' + top.why.map((w) => '<span class="chip ok">✓ ' + esc(w) + '</span>').join('') + '</div>' : '') +
                '<div class="studio-grid">' + niceAnalytics(top, f) + money_card(top, f) + '</div>' +
                (top.proofs.length ? '<div class="sec"><div class="sec-h"><h3>Small channels doing it right now</h3></div>' + top.proofs.map(channelPage).join('') + '</div>'
                    : '<p class="note">The Niche Finder hasn’t caught a new small channel here this week. It hunts twice a day.</p>') + refCards(top.references);
        } else if (step === 'viral') {
            const fits = f.niches.slice(1).map((x, i) => shelf(x.name, shelfLine(x) || x.pitch, x.proofs, x.id, '#' + (i + 2) + ' fit')).filter(Boolean);
            const rows = (f.topics || []).map((t, i) => topicRow(t, i + 1, i < 2) + (t.niche_id ? '<div style="margin:-6px 0 14px 56px"><button class="btn tonal" data-use="' + esc(t.niche_id) + '">Make ' + esc(t.label.toLowerCase()) + ' mine</button></div>' : ''));
            html = '<p class="small">More for you</p><h1 style="margin-top:4px">Viral niches right now</h1><p class="sub">From the Niche Finder: new, small channels getting big views, the topics you’d love first, then the money. Tap a niche to see its videos; tap a video to watch it on YouTube.</p>' +
                fits.join('') + (rows.length ? '<div class="sec"><div class="sec-h"><h2>' + rows.length + ' more niches with channels breaking out</h2></div><div class="lb">' + rows.join('') + '</div></div>' : '') +
                (fits.length || rows.length ? '' : '<p class="note">The Niche Finder hasn’t caught other breakout channels this week. It hunts twice a day.</p>') +
                '<p class="note">' + esc(f.estimate_note) + '</p><p class="note">' + esc(f.ypp_note) + '</p>';
        } else if (step === 'make') { html = makeIt(); wide = false; }
        else if (step === 'recipe') { html = recipeScreen(); wide = false; }
        else if (step === 'share') {
            const link = location.origin + '/money-niche/r/' + f.id; wide = false;
            html = '<p class="small">Share it</p><h2 style="margin-top:4px">Post your money niche</h2><p class="sub">Your card shows your #1 niche and its proof. Nothing personal.</p>' +
                '<div class="share sec"><img src="/money-niche/card/' + encodeURIComponent(f.id) + '.png" alt="My money niche: ' + esc(top.name) + '"></div>' +
                '<div class="share-btns"><button class="btn blue" data-share>Share</button><button class="btn tonal" data-link>Copy link</button>' +
                '<a class="btn tonal" href="/money-niche/card/' + encodeURIComponent(f.id) + '.png" download="my-money-niche.png">Save image</a>' +
                '<a class="btn tonal" target="_blank" rel="noopener" href="https://twitter.com/intent/tweet?text=' + encodeURIComponent('My money niche: ' + top.name + '. Find yours:') + '&url=' + encodeURIComponent(link) + '">Post on X</a></div>';
        } else if (step === 'plans') { html = plansScreen(); }
        show(html + nav(S.step === QUIZ_STEPS.length - 1), wide);
        bindResult(() => result(), QUIZ_STEPS.length, () => taste()); bindRows();
    }

    // ---- ChannelRecipe's part: make it yours -> recipe -> plans (shared by both doors) --------------------
    function clip(c, cls = '') {
        const k = name(c), at = cls ? ' class="' + cls + '"' : '';
        if (still) return '<img src="' + MB + k + '.webp" alt=""' + at + '>';
        return '<video muted loop playsinline autoplay preload="metadata" disablepictureinpicture poster="' + MB + k + '.webp" src="' + MB + k + '.mp4"' + at + '></video>';
    }
    const media = (it) => it.clip ? clip(it.clip) : '<img src="' + url(it.still) + '" alt="" referrerpolicy="no-referrer" loading="lazy">';
    function makeIt() {
        const nn = currentNiche();
        if (!nn) return '<p class="cr-kicker">Make it yours · ChannelRecipe</p><h2 style="margin-top:6px">Which niche is yours?</h2><p class="sub">We couldn’t tell from your titles. Pick the closest.</p>' +
            '<div class="cards2">' + S.niches.map((x) => '<button class="pick" data-niche="' + esc(x.id) + '"><b>' + esc(x.name) + '</b><small>' + esc(x.pitch) + '</small></button>').join('') + '</div>';
        return '<p class="cr-kicker">Make it yours · ChannelRecipe</p><h2 style="margin-top:6px">Pick your angle in ' + esc(nn.name.toLowerCase()) + '</h2><p class="sub">The corner of the niche you’ll own.</p>' +
            '<div class="cards2">' + nn.angles.map((a) => '<button class="pick" data-angle="' + esc(a.id) + '" aria-pressed="' + (S.pick.angle === a.id) + '"><b>' + esc(a.name) + '</b><small>“' + esc(a.title) + '”</small></button>').join('') + '</div>' +
            '<h2 class="sec" style="font-size:22px">Pick your look</h2><p class="sub">What every video will look like. Made in ChannelRecipe Studio.</p>' +
            nn.looks.map((l) => '<button class="look" data-look="' + esc(l.id) + '" aria-pressed="' + (S.pick.look === l.id) + '">' + media(l) + '<span class="in"><b>' + esc(l.name) + '</b><small>' + esc(l.how) + '</small></span></button>').join('');
    }
    function recipeOf(nn) {
        if (S.door !== 'channel' && nn.recipe) return nn.recipe;
        return {format: nn.format, kind: 'long', length: nn.length, look: nn.look, pacing: nn.pacing, voice: 'Your voice or an AI narrator', rhythm: '1 video a week, steady'};
    }
    function recipeScreen() {
        const nn = currentNiche(), cat = S.niches.find((x) => x.id === nn.id) || nn, r = recipeOf(nn);
        const angle = cat.angles.find((a) => a.id === S.pick.angle) || cat.angles[0], look = cat.looks.find((l) => l.id === S.pick.look) || cat.looks[0];
        const titles = [...new Set([angle?.title].concat(S.door === 'channel' ? (S.check.ideas || []).map((i) => i.title) : (S.full.niches[0].id === nn.id ? S.full.titles : [])).filter(Boolean))];
        if (!S.pick.title || !titles.includes(S.pick.title)) S.pick.title = titles[0] || '';
        return '<p class="cr-kicker">Your recipe · ChannelRecipe</p><h2 style="margin-top:6px">' + esc(nn.name) + (angle ? ': ' + esc(angle.name.toLowerCase()) : '') + '</h2>' +
            '<div class="recipe-card"><div class="hero-m">' + (look ? media(look) : '') + '<span>' + esc(look ? look.name : 'Your look') + '</span></div>' +
            '<div class="first"><small>Your first video</small><b id="first-title">' + esc(S.pick.title) + '</b></div><dl>' +
            [['Format', r.format + (r.kind === 'shorts' ? ' · Shorts' : ' · long videos')], ['Length', r.length], ['Look', r.look], ['Pacing', r.pacing], ['Voice', r.voice], ['Upload', r.rhythm],
                ['In Studio', look ? look.how : 'Pick a style in the Style library']].map(([k, v]) => '<div><dt>' + k + '</dt><dd>' + esc(v) + '</dd></div>').join('') + '</dl></div>' +
            (titles.length > 1 ? '<h3 class="sec">Pick your first title</h3><ul class="titles">' + titles.slice(0, 6).map((t) =>
                '<li><button data-title="' + esc(t) + '" aria-pressed="' + (t === S.pick.title) + '"><span>' + esc(t) + '</span><i>' + (t === S.pick.title ? 'chosen' : 'use this') + '</i></button></li>').join('') + '</ul>' : '');
    }
    function plansScreen() {
        const nn = currentNiche(), cat = ((S.cfg && S.cfg.plan_catalog) || []).filter((t) => t.studio);
        const annual = S.cycle === 'annual', paid = S.user && S.user.plan && S.user.plan !== 'free';
        // TODO(owner decision): a free first hosted video in this niche would be offered here once a hosted video's
        // cost is known. Until then: plans (no trial) and the recipe carried into Studio.
        return '<p class="cr-kicker">Your next step · ChannelRecipe</p><h2 style="margin-top:6px">Make “' + esc(S.pick.title || nn.name) + '” in ChannelRecipe</h2>' +
            '<p class="sub">Studio opens with your recipe picked: ' + esc(nn.name.toLowerCase()) + ', your look, your first title. It writes, voices, animates and edits the video on your computer.</p>' +
            (paid ? '<div class="nav end"><button class="btn big cr" data-go="">Open my recipe in Studio</button></div>' :
                ((S.cfg && S.cfg.annual_plans_available) ? '<div class="cycle"><button class="chip' + (annual ? '' : ' on') + '" data-cycle="monthly">Monthly</button><button class="chip' + (annual ? ' on' : '') + '" data-cycle="annual">Yearly</button></div>' : '') +
                '<div class="plans">' + cat.map((t) => {
                    const copy = PLAN_COPY[t.tier] || {tag: '', items: []}, pop = t.tier === 'daily';
                    return '<div class="plan' + (pop ? ' pop' : '') + '">' + (pop ? '<span class="badge">Start here</span>' : '') + '<h3>' + esc(t.label) + '</h3><p class="small">' + esc(copy.tag) + '</p>' +
                        '<p class="price">$' + (annual ? t.annual_usd : t.monthly_usd) + '<small> / ' + (annual ? 'year' : 'month') + '</small></p><ul>' + copy.items.map((i) => '<li>' + i + '</li>').join('') + '</ul>' +
                        '<button class="btn big ' + (pop ? 'cr' : 'tonal') + '" data-go="' + esc(t.tier) + '">Start with ' + esc(t.label) + '</button></div>';
                }).join('') + '</div><p class="note">Billed today, cancel anytime from Billing. Studio also needs your own Claude account and a Kie.ai or Atlas Cloud balance for voice and pictures.</p>') +
            '<label class="consent"><input type="checkbox" id="consent-final"> <span>Email me new breakout channels in my niche. About every week or two. Unsubscribe anytime.</span></label>';
    }
    function bindResult(redraw, total, before) {
        const back = stage.querySelector('[data-back]'); if (back) back.onclick = () => { if (S.step === 0) before(); else { S.step -= 1; redraw(); } };
        const nx = stage.querySelector('[data-next]'); if (nx) nx.onclick = () => { S.step = Math.min(total - 1, S.step + 1); redraw(); };
        const pickNiche = (id) => { const nn = S.niches.find((x) => x.id === id); if (!nn) return; S.pick = {niche: id, angle: nn.angles[0]?.id || '', look: nn.looks[0]?.id || '', title: ''}; };
        stage.querySelectorAll('[data-use]').forEach((b) => b.onclick = () => { pickNiche(b.dataset.use); S.step = QUIZ_STEPS.indexOf('make'); redraw(); });
        stage.querySelectorAll('[data-niche]').forEach((b) => b.onclick = () => { pickNiche(b.dataset.niche); redraw(); });
        stage.querySelectorAll('[data-angle]').forEach((b) => b.onclick = () => { S.pick.angle = b.dataset.angle; S.pick.title = ''; stage.querySelectorAll('[data-angle]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); });
        stage.querySelectorAll('[data-look]').forEach((b) => b.onclick = () => { S.pick.look = b.dataset.look; stage.querySelectorAll('[data-look]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); });
        stage.querySelectorAll('[data-title]').forEach((b) => b.onclick = () => {
            S.pick.title = b.dataset.title; document.getElementById('first-title').textContent = S.pick.title;
            stage.querySelectorAll('[data-title]').forEach((x) => { const on = x === b; x.setAttribute('aria-pressed', String(on)); x.querySelector('i').textContent = on ? 'chosen' : 'use this'; });
        });
        stage.querySelectorAll('[data-cycle]').forEach((b) => b.onclick = () => { S.cycle = b.dataset.cycle; redraw(); });
        const link = S.full ? location.origin + '/money-niche/r/' + S.full.id : location.href;
        const sh = stage.querySelector('[data-share]');
        if (sh) sh.onclick = async () => { const t = 'My money niche: ' + S.full.niches[0].name + '. Find yours in 60 seconds.';
            if (navigator.share) { try { await navigator.share({title: t, text: t, url: link}); } catch (_) {} } else { try { await navigator.clipboard.writeText(link); toast('Link copied'); } catch (_) { toast(link); } } };
        const ln = stage.querySelector('[data-link]'); if (ln) ln.onclick = async () => { try { await navigator.clipboard.writeText(link); toast('Link copied'); } catch (_) { toast(link); } };
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
            let attribution = null; try { attribution = window.CRTelemetry?.attributionPayload?.() || null; } catch (_) {}
            try { const c = await api('/api/billing/checkout', {plan: tier + '_' + S.cycle, skip_trial: true, return_to: 'local-studio', attribution}); if (c.url) { location.href = c.url; return; } }
            catch (e) { toast(e.message); }
            location.href = r.next || '/app#local-studio';
        } catch (e) { toast(e.message); button.disabled = false; button.textContent = label; }
    }

    // ---- door 2: "I already have a channel" -----------------------------------------------------------
    function checkStart(prefill) {
        S.door = 'channel'; progress(0.05);
        show('<p class="small">Channel check · free</p><h1 style="margin-top:4px">Why isn’t your channel growing?</h1>' +
            '<p class="sub">Paste your channel. We put it next to the small channels breaking out in your niche right now, show what’s holding it back, and what to make next.</p>' +
            '<div class="channel-input"><input class="field" id="channel" placeholder="youtube.com/@yourchannel" autocomplete="url" aria-label="Your channel link" value="' + esc(prefill || '') + '">' +
            '<button class="btn blue" style="height:48px;border-radius:12px" data-check>Check</button></div><p class="err" id="err" role="alert"></p>' +
            '<p class="note">We read your channel’s public numbers only: views, uploads, titles. You don’t sign in to YouTube.</p>' +
            '<div class="studio sec"><div class="top"><h4>What you’ll get</h4></div><ul class="checklist" style="padding:0;background:none"><li>Your channel as a dashboard</li><li>The breakout channels in your niche, side by side</li><li>What’s holding you back, in plain words</li><li>A step-by-step plan</li><li>5 video ideas with thumbnail ideas</li></ul></div>' +
            '<div class="nav end"><button class="btn tonal back" aria-label="Back" data-back>←</button></div>');
        const input = document.getElementById('channel');
        const run = () => { const v = input.value.trim(); if (!v) { document.getElementById('err').textContent = 'Paste your channel link.'; return; } checkRun(v); };
        stage.querySelector('[data-check]').onclick = run; input.onkeydown = (e) => { if (e.key === 'Enter') run(); };
        stage.querySelector('[data-back]').onclick = home; if (prefill) run(); else input.focus();
    }
    async function checkRun(v) {
        const stop = skeleton('Checking your channel…', ['Reading your last 15 videos', 'Finding your niche', 'Comparing with channels breaking out right now']);
        try {
            const [t] = await Promise.all([api('/api/channel-check', {url: v}), new Promise((r) => setTimeout(r, 1600))]);
            S.check = t; S.checkId = t.id; history.replaceState(null, '', '/money-niche/c/' + t.id); stop(); checkTaste();
        } catch (e) { stop(); checkStart(); document.getElementById('err').textContent = e.message; }
    }
    function dashboard(t) {
        const c = t.channel, vids = t.videos || [], latest = vids[0];
        const ranked = vids.slice().sort((a, b) => b.views - a.views), rank = latest ? ranked.indexOf(latest) + 1 : 0;
        const chron = vids.slice().reverse();
        return '<div class="ych" style="margin-top:12px"><div class="banner" style="background-image:url(\'' + url(latest?.thumbnail || c.avatar) + '\')"></div><div class="head">' +
            (c.avatar ? '<img class="av" src="' + url(c.avatar) + '" alt="" referrerpolicy="no-referrer">' : '<span></span>') +
            '<div class="info"><h3>' + esc(c.name) + '</h3><p>' + (c.hidden_subscribers ? 'Subscribers hidden' : n(c.subscribers) + ' subscribers') + ' · ' + c.video_count + ' videos</p>' +
            (t.niche ? '<p><span class="chip ok">Niche: ' + esc(t.niche.name) + '</span></p>' : '') + '</div><a class="btn" href="' + url(c.url) + '" target="_blank" rel="noopener">View channel</a></div></div>' +
            '<div class="studio-grid" style="margin-top:16px">' +
            (latest ? '<div class="studio"><div class="top"><h4>Latest video performance</h4><span class="src">' + esc(ago(latest.published_at)) + '</span></div>' +
                '<div style="display:grid;grid-template-columns:140px 1fr;gap:12px;margin-top:12px"><img src="' + url(latest.thumbnail) + '" alt="" style="width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:8px" referrerpolicy="no-referrer">' +
                '<p style="font-size:14px">' + esc(latest.title) + '</p></div><div class="toplist"><a><em></em><span>Ranking by views</span><span></span><b>' + rank + ' of ' + vids.length + '</b></a>' +
                '<a><em></em><span>Views</span><span></span><b>' + n(latest.views) + '</b></a><a><em></em><span>Your typical video</span><span></span><b>' + n(t.stats.median_views) + '</b></a></div></div>' : '') +
            '<div class="studio"><div class="top"><h4>Channel analytics</h4><span class="src">Last ' + vids.length + ' videos · public numbers</span></div>' +
            '<div class="kpis"><div class="on"><small>Views</small><b>' + n(t.stats.median_views) + '</b><i>typical video</i></div>' +
            '<div><small>Breakout channels</small><b>' + (t.stats.peer_views ? n(t.stats.peer_views) : '–') + '</b><i class="up">per video in your niche</i></div>' +
            '<div><small>Last upload</small><b>' + (t.stats.last_upload_days != null ? t.stats.last_upload_days + 'd' : '–') + '</b><i' + (t.stats.last_upload_days > 14 ? ' class="down"' : '') + '>ago</i></div></div>' +
            lineChart(chron.map((v) => v.views), t.stats.peer_views) + '<div class="chart-x"><span>' + esc(ago(chron[0]?.published_at)) + '</span><span>' + esc(ago(chron[chron.length - 1]?.published_at)) + '</span></div>' +
            '<div class="legend"><span><i></i>Your videos</span>' + (t.stats.peer_views ? '<span><i class="p"></i>Small channels breaking out in your niche</span>' : '') + '</div></div>' +
            topContent(vids, 'Your top videos') + '</div>';
    }
    function ideaCard(f) {
        const sym = {bad: '!', warn: '•', good: '✓'}[f.level] || '•';
        return '<div class="idea-card ' + esc(f.level) + '"><span class="ic">' + sym + '</span><div><small>' + ({bad: 'Holding you back', warn: 'Worth fixing', good: 'Working'}[f.level] || '') + '</small>' +
            '<h3>' + esc(f.title) + '</h3><p>' + esc(f.detail) + '</p></div></div>';
    }
    function checkTaste() {
        const t = S.check, signedIn = !!S.user;
        progress(0.3);
        show('<p class="small">Channel dashboard</p><h1 style="margin-top:4px">' + esc(t.channel.name) + '</h1>' + dashboard(t) +
            (t.headline ? '<div class="sec"><div class="sec-h"><h3>Ideas for you</h3></div>' + ideaCard(t.headline) + '</div>' : '') +
            (signedIn ? '<div class="nav"><button class="btn big blue" data-full>See everything</button></div>'
                : gateHTML('Sign in to see everything', [(t.more || 0) + ' more findings about your channel', (t.peers_count ? t.peers_count + ' ' : '') + 'breakout channels in your niche to study',
                    'A step-by-step plan', '5 video ideas with thumbnail ideas', 'Your recipe, ready to make in ChannelRecipe']) +
                    '<div class="nav end"><button class="btn tonal back" aria-label="Back" data-back>←</button></div>'), true);
        const b = stage.querySelector('[data-back]'); if (b) b.onclick = () => checkStart();
        if (signedIn) stage.querySelector('[data-full]').onclick = loadCheck; else bindGate(loadCheck);
    }
    async function loadCheck() {
        try {
            const f = await api('/api/channel-check/' + encodeURIComponent(S.checkId));
            if (f.gated) { S.user = null; checkTaste(); return; }
            S.check = f; S.step = 0; const nn = S.niches.find((x) => x.id === f.niche?.id);
            S.pick = {niche: f.niche?.id || '', angle: nn?.angles[0]?.id || '', look: nn?.looks[0]?.id || '', title: ''};
            checkResult();
        } catch (e) { toast(e.message); }
    }
    function checkResult() {
        const f = S.check, step = CHECK_STEPS[S.step];
        progress(0.35 + S.step / CHECK_STEPS.length * 0.65);
        let html = '', wide = true;
        if (step === 'dashboard') {
            html = '<p class="small">Channel dashboard</p><h1 style="margin-top:4px">' + esc(f.channel.name) + '</h1>' + dashboard(f) +
                '<div class="sec"><div class="sec-h"><h3>Ideas for you</h3></div>' + f.findings.map(ideaCard).join('') + '</div>';
        } else if (step === 'peers') {
            html = '<p class="small">Study these</p><h1 style="margin-top:4px">Breakout channels in ' + esc(f.niche ? f.niche.name.toLowerCase() : 'your niche') + '</h1>' +
                '<p class="sub">Small channels getting big views right now. Watch their last three videos: the idea, the title, the thumbnail, the first 30 seconds.</p>' +
                (f.peers.length ? f.peers.map(channelPage).join('') : '<p class="note">The Niche Finder hasn’t caught a small breakout channel in this niche this week. It hunts twice a day.</p>') +
                '<p class="note">' + esc(f.ypp_note) + '</p>';
        } else if (step === 'plan') {
            html = '<p class="small">Your plan</p><h1 style="margin-top:4px">Step by step</h1><ul class="checklist">' + f.plan.map((s) => '<li>' + esc(s) + '</li>').join('') + '</ul>'; wide = false;
        } else if (step === 'ideas') {
            const thumbs = (f.videos || []).filter((v) => v.thumbnail);
            html = '<p class="small">Make these next</p><h1 style="margin-top:4px">5 video ideas, with thumbnails</h1><p class="sub">' +
                (f.ideas_source === 'model' ? 'Built on what already worked for your channel.' : 'Proven ideas for your niche.') + '</p><div class="grid sec">' +
                f.ideas.map((i, k) => '<div class="yv"><div class="th">' + (thumbs.length ? '<img src="' + url(thumbs[k % thumbs.length].thumbnail) + '" alt="" style="filter:saturate(1.3) contrast(1.1)" referrerpolicy="no-referrer">' : '') +
                    '<b style="position:absolute;left:10px;right:10px;bottom:10px;font-size:22px;font-weight:900;line-height:1;text-shadow:0 2px 0 #000,0 0 14px rgba(0,0,0,.9)">' + esc(hook(i.title)) + '</b></div>' +
                    '<div class="meta">' + (f.channel.avatar ? '<img class="av" src="' + url(f.channel.avatar) + '" alt="" referrerpolicy="no-referrer">' : '') + '<div><h4>' + esc(i.title) + '</h4><p>' + esc(f.channel.name) + ' · next video idea</p>' +
                    '<p class="tiny" style="margin-top:4px">Thumbnail: ' + esc(i.thumbnail) + '</p></div></div></div>').join('') + '</div>' +
                '<p class="note">Thumbnails are mock-ups on your own pictures; Studio makes the real one with each video.</p>';
        } else if (step === 'make') { html = makeIt(); wide = false; }
        else if (step === 'recipe') { html = recipeScreen(); wide = false; }
        else if (step === 'plans') html = plansScreen();
        show(html + nav(S.step === CHECK_STEPS.length - 1), wide);
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
            else { S.teaser = data.teaser; S.full = data; S.step = 0; const top = data.niches[0]; S.pick = {niche: top.id, angle: top.angles[0]?.id || '', look: top.looks[0]?.id || '', title: ''}; result(); }
        } catch (e) { console.error(e); home(); toast(e.message); }
    }
    async function openCheck(id) {
        S.door = 'channel'; S.checkId = id;
        try {
            const data = await api('/api/channel-check/' + encodeURIComponent(id)); S.check = data;
            if (data.gated) checkTaste();
            else { S.step = 0; const nn = S.niches.find((x) => x.id === data.niche?.id); S.pick = {niche: data.niche?.id || '', angle: nn?.angles[0]?.id || '', look: nn?.looks[0]?.id || '', title: ''}; checkResult(); }
        } catch (e) { home(); toast(e.message); }
    }
    document.getElementById('search').addEventListener('submit', (e) => {
        e.preventDefault(); const v = document.getElementById('search-input').value.trim(); if (v) checkStart(v); else checkStart();
    });
    (async function boot() {
        try { window.CRTelemetry?.captureAttributionFromUrl?.(); } catch (_) {}
        const [me, cfg, pulse, niches, board] = await Promise.all([api('/api/auth/me').catch(() => ({})), api('/api/config').catch(() => null),
            api('/api/money-niche/pulse').catch(() => null), api('/api/money-niche/niches').catch(() => []), api('/api/money-niche/leaderboard').catch(() => [])]);
        S.user = me.user || null; S.cfg = cfg; S.pulse = pulse; S.niches = niches || []; S.board = board || [];
        const r = location.pathname.match(/^\/money-niche\/r\/([A-Za-z0-9_-]{6,24})$/);
        const c = location.pathname.match(/^\/money-niche\/c\/(cc_[A-Za-z0-9_-]{6,20})$/);
        if (r) openResult(r[1], false); else if (c) openCheck(c[1]); else home();
    })();
})();
