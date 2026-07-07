/**
 * ChannelRecipe — Complete System
 * Pipeline + Tools + History + Settings
 */

const state = {
    page: 'pipeline',
    step: 1,
    niche: null,
    nicheData: null,
    title: '',
    script: '',
    voice: 'Charon',
    targetMinutes: 8,
    voiceoverPath: '',
    voiceoverUrl: '',
    thumbnailPath: '',
    thumbnailUrl: '',
    thumbnailRefs: [],
    videoUrl: '',
    videoPath: '',
    channelData: null,
    channelAnalysis: null,
    voiceMode: 'generate',
    uploadedVoPath: '',
};

let previewAudio = null;

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
let currentUser = null;
let authReady = false;
let pendingAuthAction = null;

// Defense-in-depth: any protected API call that comes back 401 forces the
// sign-in modal, so there is no way to consume compute without authenticating.
const _origFetch = window.fetch.bind(window);
window.fetch = async function (input, init) {
    const res = await _origFetch(input, init);
    try {
        const url = typeof input === 'string' ? input : (input && input.url) || '';
        if (res.status === 401 && url.includes('/api/') && !url.includes('/api/auth/')) {
            currentUser = null;
            if (typeof updateAuthUI === 'function') updateAuthUI();
            if (typeof showAuthModal === 'function') showAuthModal();
        }
    } catch (_) { /* ignore */ }
    return res;
};

// Proactive gate: returns true if signed in; otherwise shows the modal and
// (optionally) replays `retry` once the user finishes signing in.
function ensureAuth(retry) {
    if (currentUser) return true;
    pendingAuthAction = typeof retry === 'function' ? retry : null;
    showAuthModal();
    return false;
}

// ---------------------------------------------------------------------------
// Analytics (PostHog + Sentry). Inert unless keys are configured on the server.
// Metadata only — we never send script/voiceover text.
// ---------------------------------------------------------------------------
let track = () => {}; // replaced with posthog.capture once loaded

function analyticsDeclined() {
    return localStorage.getItem('cr_cookie_consent') === 'declined';
}

async function initAnalytics() {
    let cfg;
    try {
        const res = await _origFetch('/api/config');
        cfg = await res.json();
    } catch { return; }

    // Sentry (error tracking) — legitimate interest, load regardless of consent.
    if (cfg.sentry_dsn) {
        const s = document.createElement('script');
        s.src = 'https://browser.sentry-cdn.com/7.120.0/bundle.min.js';
        s.crossOrigin = 'anonymous';
        s.onload = () => { try { window.Sentry?.init({ dsn: cfg.sentry_dsn, tracesSampleRate: 0.1 }); } catch (_) {} };
        document.head.appendChild(s);
    }

    // PostHog (product analytics) — only with cookie consent.
    if (cfg.posthog_key && !analyticsDeclined()) {
        !function (t, e) { var o, n, p, r; e.__SV || (window.posthog = e, e._i = [], e.init = function (i, s, a) { function g(t, e) { var o = e.split("."); 2 == o.length && (t = t[o[0]], e = o[1]), t[e] = function () { t.push([e].concat(Array.prototype.slice.call(arguments, 0))) } } (p = t.createElement("script")).type = "text/javascript", p.async = !0, p.src = s.api_host + "/static/array.js", (r = t.getElementsByTagName("script")[0]).parentNode.insertBefore(p, r); var u = e; for (void 0 !== a ? u = e[a] = [] : a = "posthog", u.people = u.people || [], u.toString = function (t) { var e = "posthog"; return "posthog" !== a && (e += "." + a), t || (e += " (stub)"), e }, u.people.toString = function () { return u.toString(1) + ".people (stub)" }, o = "capture identify alias people.set people.set_once set_config register register_once unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset isFeatureEnabled onFeatureFlags getFeatureFlag getFeatureFlagPayload reloadFeatureFlags group updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures getActiveMatchingSurveys getSurveys onSessionId".split(" "), n = 0; n < o.length; n++)g(u, o[n]); e._i.push([i, s, a]) }, e.__SV = 1) }(document, window.posthog || []);
        try {
            window.posthog.init(cfg.posthog_key, { api_host: cfg.posthog_host || 'https://us.i.posthog.com', capture_pageview: true, autocapture: true });
            track = (event, props) => { try { window.posthog.capture(event, props || {}); } catch (_) {} };
            if (currentUser) window.posthog.identify(String(currentUser.id), { email: currentUser.email, plan: currentUser.plan });
        } catch (_) { /* ignore */ }
    }

    maybeShowCookieBanner(cfg);
}

function maybeShowCookieBanner(cfg) {
    if (!cfg.posthog_key) return;                       // nothing to consent to
    if (localStorage.getItem('cr_cookie_consent')) return; // already chose
    const banner = document.getElementById('cookie-banner');
    if (banner) banner.classList.remove('hidden');
}

function acceptCookies() {
    localStorage.setItem('cr_cookie_consent', 'accepted');
    document.getElementById('cookie-banner')?.classList.add('hidden');
    initAnalytics();
}

function declineCookies() {
    localStorage.setItem('cr_cookie_consent', 'declined');
    document.getElementById('cookie-banner')?.classList.add('hidden');
}

document.addEventListener('DOMContentLoaded', async () => {
    await checkAuth();
    initAnalytics();
    loadNiches();
    bindEvents();
    loadSettingsFromServer();
    loadVoiceOptions();

    const hash = window.location.hash.slice(1);
    if (hash) navigateTo(hash);
});

// ---------------------------------------------------------------------------
// Page Navigation (SPA routing)
// ---------------------------------------------------------------------------
function navigateTo(page) {
    // Settings is an ops-only console — never expose it to non-admins.
    if (page === 'settings' && !(currentUser && currentUser.is_admin)) {
        if (!currentUser) { showAuthModal(); }
        return;
    }
    state.page = page;
    document.querySelectorAll('.page-container').forEach(p => p.classList.add('hidden'));

    const pageId = `page-${page}`;
    const el = document.getElementById(pageId);
    if (el) {
        el.classList.remove('hidden');
        el.style.animation = 'none';
        el.offsetHeight;
        el.style.animation = '';
    }

    // Update nav buttons
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    const toolPages = ['script-studio', 'voiceover-studio', 'thumbnail-studio', 'niche-screener', 'channel-analyzer'];
    if (page === 'pipeline') {
        document.querySelector('[data-page="pipeline"]')?.classList.add('active');
        goToStep(state.step);
    } else if (toolPages.includes(page)) {
        document.querySelector('[data-page="tools"]')?.classList.add('active');
    } else {
        document.querySelector(`[data-page="${page}"]`)?.classList.add('active');
    }

    // Close menus
    document.getElementById('tools-menu')?.classList.add('hidden');
    document.getElementById('mobile-menu')?.classList.add('hidden');

    window.location.hash = page;
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function toggleToolsMenu(e) {
    e.stopPropagation();
    const menu = document.getElementById('tools-menu');
    menu.classList.toggle('hidden');
}

function toggleMobileMenu() {
    document.getElementById('mobile-menu').classList.toggle('hidden');
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('#tools-dropdown')) {
        document.getElementById('tools-menu')?.classList.add('hidden');
    }
});

// ---------------------------------------------------------------------------
// Pipeline Step Navigation
// ---------------------------------------------------------------------------
function goToStep(n) {
    state.step = n;
    document.querySelectorAll('.step-panel').forEach(p => p.classList.add('hidden'));
    const panel = document.getElementById(`step-${n}`);
    if (panel) {
        panel.classList.remove('hidden');
        panel.style.animation = 'none';
        panel.offsetHeight;
        panel.style.animation = '';
    }

    document.querySelectorAll('.step-indicator').forEach(ind => {
        const s = parseInt(ind.dataset.step);
        const dot = ind.querySelector('.step-dot');
        dot.classList.remove('active', 'done');
        if (s < n) dot.classList.add('done');
        else if (s === n) dot.classList.add('active');
    });

    document.querySelectorAll('.step-line').forEach((line, i) => {
        line.classList.toggle('done', i < n - 1);
    });

    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function goToCompletedStep(n) {
    if (n < state.step) goToStep(n);
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
function bindEvents() {
    document.getElementById('btn-gen-titles').addEventListener('click', generateTitles);

    document.getElementById('custom-title').addEventListener('input', (e) => {
        state.title = e.target.value.trim();
        updateNextBtn2();
    });

    document.getElementById('btn-next-2').addEventListener('click', () => {
        if (!state.title) return;
        goToStep(3);
    });

    document.getElementById('btn-gen-script').addEventListener('click', generateScript);

    document.getElementById('script-editor').addEventListener('input', (e) => {
        state.script = e.target.value;
        updateWordCount();
    });

    document.getElementById('btn-regen-script').addEventListener('click', generateScript);

    document.getElementById('btn-next-3').addEventListener('click', () => {
        state.script = document.getElementById('script-editor').value.trim();
        if (!state.script) return;
        goToStep(4);
        loadVoices();
    });

    document.getElementById('btn-next-4').addEventListener('click', handleVoiceNext);

    document.getElementById('btn-next-5').addEventListener('click', () => {
        goToStep(6);
        populateBuildSummary();
    });

    document.getElementById('btn-build').addEventListener('click', startBuild);

    document.getElementById('btn-make-another').addEventListener('click', resetPipeline);

    // Target minutes slider
    const slider = document.getElementById('target-minutes');
    const label = document.getElementById('target-minutes-label');
    if (slider) {
        slider.addEventListener('input', () => {
            let val = parseInt(slider.value);
            const cap = freeMinuteCap();
            if (!isPaidUser() && val > cap) {
                val = cap;
                slider.value = cap;
                showLengthUpgradePrompt();
            } else {
                hideLengthUpgradePrompt();
            }
            state.targetMinutes = val;
            label.textContent = val + ' min';
        });
    }

    // Range label updaters
    bindSliderLabel('ss-video-count', 'ss-video-count-label');
    bindSliderLabel('ss-idea-count', 'ss-idea-count-label');
    bindSliderLabel('ns-minutes', 'ns-minutes-label');
    bindSliderLabel('ca-count', 'ca-count-label');

    // Voiceover studio style toggle
    const voStyle = document.getElementById('vo-style');
    if (voStyle) {
        voStyle.addEventListener('change', () => {
            document.getElementById('vo-custom-wrap').classList.toggle('hidden', voStyle.value !== 'Custom');
        });
    }

    // Copy buttons (delegated)
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('.copy-btn[data-target]');
        if (!btn) return;
        const target = document.getElementById(btn.dataset.target);
        if (!target) return;
        navigator.clipboard.writeText(target.textContent).then(() => {
            btn.textContent = 'Copied!';
            btn.classList.add('copied');
            setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
        });
    });
}

function bindSliderLabel(sliderId, labelId) {
    const s = document.getElementById(sliderId);
    const l = document.getElementById(labelId);
    if (s && l) s.addEventListener('input', () => { l.textContent = s.value; });
}

function resetPipeline() {
    Object.assign(state, {
        step: 1, niche: null, nicheData: null, title: '', script: '',
        voice: 'Charon', targetMinutes: 8, voiceoverPath: '', voiceoverUrl: '',
        thumbnailPath: '', thumbnailUrl: '', thumbnailRefs: [], videoUrl: '', videoPath: '',
        voiceMode: 'generate', uploadedVoPath: '',
    });
    document.getElementById('topic-input').value = '';
    document.getElementById('custom-title').value = '';
    document.getElementById('script-editor').value = '';
    document.getElementById('titles-list').innerHTML = '';
    document.getElementById('thumb-grid').innerHTML = '';
    document.getElementById('thumb-refs-preview').innerHTML = '';
    document.getElementById('build-start').classList.remove('hidden');
    document.getElementById('build-progress').classList.add('hidden');
    document.getElementById('upload-kit').classList.add('hidden');
    document.getElementById('progress-log').innerHTML = '';
    document.getElementById('progress-bar').style.width = '0%';
    const slider = document.getElementById('target-minutes');
    if (slider) { slider.value = 8; slider.max = 20; document.getElementById('target-minutes-label').textContent = '8 min'; }
    setVoiceMode('generate');
    const uploadInfo = document.getElementById('vo-upload-info');
    if (uploadInfo) uploadInfo.classList.add('hidden');
    const uploadPlaceholder = document.getElementById('vo-upload-placeholder');
    if (uploadPlaceholder) uploadPlaceholder.classList.remove('hidden');
    goToStep(1);
}

// ---------------------------------------------------------------------------
// Step 1: Niches
// ---------------------------------------------------------------------------
async function loadNiches() {
    try {
        const res = await fetch('/api/niches');
        const niches = await res.json();
        const grid = document.getElementById('niche-grid');
        grid.innerHTML = '';

        niches.forEach(niche => {
            const card = document.createElement('div');
            card.className = 'niche-card';
            const previewHtml = niche.preview_gif
                ? `<div class="niche-preview">
                       <img src="${niche.preview_gif}" alt="${niche.name} preview" loading="lazy">
                   </div>`
                : '';
            const statusChip = niche.status === 'proven'
                ? `<span class="status-chip proven"><svg width="11" height="11" viewBox="0 0 12 12"><path d="M1 9 L4 5 L6.5 7 L11 1" fill="none" stroke="var(--success)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>Proven</span>`
                : `<span class="status-chip new">New</span>`;
            card.innerHTML = `
                ${previewHtml}
                <svg class="fold-play" width="40" height="40" viewBox="0 0 40 40" aria-hidden="true">
                    <path d="M40 0 V22 L18 0 Z" fill="var(--accent)" opacity="0.16"/>
                    <path d="M15 11 L15 27 L29 19 Z" fill="var(--accent)"/>
                </svg>
                <div class="niche-card-body">
                    <div style="margin-bottom: 4px;">${statusChip}</div>
                    <h3 style="font-family: var(--font-display); font-weight: 800; font-size: 22px; line-height: 1.3; letter-spacing: -0.01em; color: var(--app-ink); max-width: 88%; margin-bottom: 6px;">${niche.name}</h3>
                    <p style="font-family: var(--font-body); font-size: 15px; line-height: 1.5; color: var(--app-ink-2); margin-bottom: 12px;">${niche.tagline || niche.description || ''}</p>
                    <div style="display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin-top: auto; font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.01em; color: var(--app-ink-3);">
                        <span>~15 min</span>
                        <span style="opacity: 0.5;">·</span>
                        <span>1 credit</span>
                        <span style="opacity: 0.5;">·</span>
                        <span>RPM ${niche.rpm_range || 'N/A'}</span>
                    </div>
                </div>
            `;
            card.addEventListener('click', () => selectNiche(niche, card));
            grid.appendChild(card);
        });
    } catch (e) {
        console.error('Failed to load niches:', e);
    }
}

function selectNiche(niche, card) {
    if (!ensureAuth(() => selectNiche(niche, card))) return;
    track('recipe_selected', { recipe: niche.id });
    document.querySelectorAll('.niche-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    state.niche = niche.id;
    state.nicheData = niche;
    state.voice = niche.default_voice || 'Charon';
    state.targetMinutes = niche.default_minutes || 8;
    state.voiceMode = 'generate';
    state.uploadedVoPath = '';
    const maxPaid = niche.max_paid_minutes || 20;
    const slider = document.getElementById('target-minutes');
    if (slider) {
        slider.value = state.targetMinutes;
        slider.max = maxPaid;
        document.getElementById('target-minutes-label').textContent = state.targetMinutes + ' min';
    }
    updateScriptLimitMsg();
    setTimeout(() => goToStep(2), 300);
}

function isPaidUser() {
    return currentUser && currentUser.plan === 'pro';
}

function freeMinuteCap() {
    return state.nicheData?.max_free_minutes || 8;
}

function updateScriptLimitMsg() {
    const msg = document.getElementById('script-limit-msg');
    if (!msg) return;
    const limitSpan = document.getElementById('limit-minutes');
    if (limitSpan) limitSpan.textContent = freeMinuteCap();
    msg.classList.add('hidden');
}

let _lengthPromptTimer = null;
function showLengthUpgradePrompt() {
    const msg = document.getElementById('script-limit-msg');
    if (!msg) return;
    const limitSpan = document.getElementById('limit-minutes');
    if (limitSpan) limitSpan.textContent = freeMinuteCap();
    msg.classList.remove('hidden');
    msg.classList.add('limit-pop');
    clearTimeout(_lengthPromptTimer);
    _lengthPromptTimer = setTimeout(() => msg.classList.remove('limit-pop'), 400);
}

function hideLengthUpgradePrompt() {
    // keep it visible once shown within the session; do nothing on normal drag
}

async function upgradeToPro(plan = 'monthly') {
    if (!currentUser) { showAuthModal(); return; }
    track('checkout_started', { plan });
    try {
        const res = await fetch('/api/billing/checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plan }),
        });
        const data = await res.json();
        if (res.ok && data.url) {
            window.location.href = data.url;
        } else {
            alert(data.detail || 'Could not start checkout. Please try again.');
        }
    } catch (e) {
        alert('Checkout failed: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// Step 2: Titles
// ---------------------------------------------------------------------------
async function generateTitles() {
    const btn = document.getElementById('btn-gen-titles');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/titles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ niche: state.niche, topic: document.getElementById('topic-input').value.trim() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        renderTitles(data.titles);
    } catch (e) {
        alert('Title generation failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

function renderTitles(titles) {
    const list = document.getElementById('titles-list');
    list.innerHTML = '';
    titles.forEach((t, i) => {
        const card = document.createElement('div');
        card.className = 'title-card';
        card.innerHTML = `<div class="flex items-center gap-3"><span class="text-accent font-bold text-lg">${i + 1}</span><span class="text-gray-100">${t}</span></div>`;
        card.addEventListener('click', () => {
            document.querySelectorAll('.title-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            state.title = t;
            document.getElementById('custom-title').value = '';
            updateNextBtn2();
        });
        list.appendChild(card);
    });
}

function updateNextBtn2() {
    const btn = document.getElementById('btn-next-2');
    const has = !!state.title;
    btn.disabled = !has;
    btn.classList.toggle('opacity-50', !has);
    btn.classList.toggle('cursor-not-allowed', !has);
}

// ---------------------------------------------------------------------------
// Step 3: Script
// ---------------------------------------------------------------------------
async function generateScript() {
    const loading = document.getElementById('script-loading');
    const editor = document.getElementById('script-editor');
    loading.classList.remove('hidden');
    editor.value = '';
    try {
        const res = await fetch('/api/script', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: state.title, niche: state.niche, target_minutes: state.targetMinutes }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        editor.value = data.script;
        state.script = data.script;
        updateWordCount();
        track('script_generated', { recipe: state.niche, target_minutes: state.targetMinutes, word_count: data.word_count });
    } catch (e) {
        alert('Script generation failed: ' + e.message);
    } finally {
        loading.classList.add('hidden');
    }
}

function updateWordCount() {
    const text = document.getElementById('script-editor').value.trim();
    const count = text ? text.split(/\s+/).length : 0;
    document.getElementById('word-count').textContent = `${count} words (~${Math.round(count / 150)} min)`;
}

// ---------------------------------------------------------------------------
// Step 4: Voice
// ---------------------------------------------------------------------------
async function loadVoices() {
    try {
        const res = await fetch('/api/voices');
        const voices = await res.json();
        const grid = document.getElementById('voices-grid');
        grid.innerHTML = '';
        voices.forEach(v => {
            const card = document.createElement('div');
            card.className = `voice-card${v.id === state.voice ? ' selected' : ''}`;
            const recommended = v.default
                ? `<span style="font-family:var(--font-mono);font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:var(--accent);background:var(--accent-soft-dark);border-radius:var(--radius-pill);padding:2px 7px;">Best pick</span>`
                : '';
            card.innerHTML = `
                <button class="play-btn" data-voice="${v.id}" title="Preview voice">
                    <svg width="13" height="13" viewBox="0 0 14 14"><path d="M4 2.5 L4 11.5 L11 7 Z" fill="currentColor"/></svg>
                </button>
                <div class="flex-1 min-w-0">
                    <div style="display:flex;align-items:center;gap:8px;font-family:var(--font-body);font-weight:600;font-size:15px;color:var(--app-ink);">
                        ${v.name} ${recommended}
                    </div>
                    <div style="font-family:var(--font-body);font-size:13px;color:var(--app-ink-3);margin-top:2px;">${v.desc || v.tag}</div>
                </div>
                <span style="width:20px;height:20px;flex:none;border-radius:50%;border:2px solid ${v.id === state.voice ? 'var(--accent)' : 'var(--app-border)'};display:flex;align-items:center;justify-content:center;">
                    ${v.id === state.voice ? '<span style="width:10px;height:10px;border-radius:50%;background:var(--accent);"></span>' : ''}
                </span>
            `;
            card.addEventListener('click', (e) => {
                if (e.target.closest('.play-btn')) return;
                document.querySelectorAll('.voice-card').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                state.voice = v.id;
            });
            card.querySelector('.play-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                previewVoice(v.id, e.currentTarget);
            });
            grid.appendChild(card);
        });
    } catch (e) {
        console.error('Failed to load voices:', e);
    }
}

async function previewVoice(voiceId, btn) {
    if (previewAudio) { previewAudio.pause(); previewAudio = null; }
    btn.classList.add('loading');
    btn.innerHTML = '<svg class="animate-spin w-4 h-4" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>';
    try {
        const res = await fetch('/api/voiceover/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice: voiceId }) });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Preview failed');
        previewAudio = new Audio(data.url);
        previewAudio.play();
        previewAudio.addEventListener('ended', () => resetPlayBtn(btn));
        btn.innerHTML = '<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>';
        btn.classList.remove('loading');
    } catch (e) {
        console.error('Voice preview failed:', e);
        resetPlayBtn(btn);
    }
}

function resetPlayBtn(btn) {
    btn.classList.remove('loading');
    btn.innerHTML = '<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
}

function setVoiceMode(mode) {
    state.voiceMode = mode;
    document.getElementById('vo-generate-panel').classList.toggle('hidden', mode !== 'generate');
    document.getElementById('vo-upload-panel').classList.toggle('hidden', mode !== 'upload');
    document.querySelectorAll('.vo-mode-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(`vo-mode-${mode}`).classList.add('active');
}

async function handleVoUpload(input) {
    const file = input.files?.[0];
    if (!file) return;
    const prog = document.getElementById('vo-upload-progress');
    prog.classList.remove('hidden');
    try {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch('/api/voiceover/upload', { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Upload failed');
        state.uploadedVoPath = data.path;
        state.voiceoverUrl = data.url;
        document.getElementById('vo-upload-placeholder').classList.add('hidden');
        document.getElementById('vo-upload-info').classList.remove('hidden');
        document.getElementById('vo-upload-name').textContent = file.name;
    } catch (e) {
        alert('Upload failed: ' + e.message);
    } finally {
        prog.classList.add('hidden');
    }
}

async function handleVoiceNext() {
    const btn = document.getElementById('btn-next-4');
    setLoading(btn, true);

    const isUpload = state.voiceMode === 'upload' && state.uploadedVoPath;

    if (isUpload) {
        state.voiceoverPath = state.uploadedVoPath;
        document.getElementById('vo-generating').classList.remove('hidden');
        try {
            const thumbRes = await fetch('/api/thumbnail', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: state.title, niche_style: state.nicheData?.thumbnail_style || '', count: 2 }) });
            const thumbData = await thumbRes.json();
            if (thumbRes.ok && thumbData.thumbnails?.length) {
                renderThumbnails(thumbData.thumbnails, thumbData.paths);
                goToStep(5);
            } else {
                goToStep(6);
                populateBuildSummary();
            }
        } catch (e) {
            alert('Thumbnail generation failed: ' + e.message);
        } finally {
            setLoading(btn, false);
            document.getElementById('vo-generating').classList.add('hidden');
        }
        return;
    }

    if (state.voiceMode === 'upload' && !state.uploadedVoPath) {
        alert('Please upload a voiceover file first.');
        setLoading(btn, false);
        return;
    }

    document.getElementById('vo-generating').classList.remove('hidden');
    try {
        const [voRes, thumbRes] = await Promise.all([
            fetch('/api/voiceover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ script: state.script, voice: state.voice }) }),
            fetch('/api/thumbnail', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: state.title, niche_style: state.nicheData?.thumbnail_style || '', count: 2 }) }),
        ]);
        const voData = await voRes.json();
        if (!voRes.ok) throw new Error(voData.detail || 'Voiceover failed');
        state.voiceoverPath = voData.path;
        state.voiceoverUrl = voData.url;

        const thumbData = await thumbRes.json();
        if (thumbRes.ok && thumbData.thumbnails?.length) {
            renderThumbnails(thumbData.thumbnails, thumbData.paths);
            goToStep(5);
        } else {
            goToStep(6);
            populateBuildSummary();
        }
    } catch (e) {
        alert('Generation failed: ' + e.message);
    } finally {
        setLoading(btn, false);
        document.getElementById('vo-generating').classList.add('hidden');
    }
}

// ---------------------------------------------------------------------------
// Step 5: Thumbnails
// ---------------------------------------------------------------------------
function renderThumbnails(urls, paths) {
    const grid = document.getElementById('thumb-grid');
    grid.innerHTML = '';
    document.getElementById('thumb-loading').classList.add('hidden');
    urls.forEach((url, i) => {
        const card = document.createElement('div');
        card.className = 'thumb-card';
        card.innerHTML = `<img src="${url}" alt="Thumbnail option ${i + 1}">`;
        card.addEventListener('click', () => {
            document.querySelectorAll('.thumb-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            state.thumbnailUrl = url;
            state.thumbnailPath = paths[i];
            const btn = document.getElementById('btn-next-5');
            btn.disabled = false;
            btn.classList.remove('opacity-50', 'cursor-not-allowed');
        });
        grid.appendChild(card);
    });
    if (urls.length > 0) grid.querySelector('.thumb-card').click();
}

function handleThumbRefUpload(input) {
    const preview = document.getElementById('thumb-refs-preview');
    preview.innerHTML = '';
    state.thumbnailRefs = [];
    for (const file of input.files) {
        state.thumbnailRefs.push(file);
        const img = document.createElement('img');
        img.className = 'ref-thumb';
        img.src = URL.createObjectURL(file);
        preview.appendChild(img);
    }
}

async function regenerateThumbnails() {
    const grid = document.getElementById('thumb-grid');
    grid.innerHTML = '';
    document.getElementById('thumb-loading').classList.remove('hidden');
    const customStyle = document.getElementById('thumb-style-input')?.value || '';
    const style = customStyle || state.nicheData?.thumbnail_style || '';
    try {
        if (state.thumbnailRefs.length > 0) {
            const formData = new FormData();
            formData.append('title', state.title);
            formData.append('style', style);
            state.thumbnailRefs.forEach(f => formData.append('refs', f));
            const res = await fetch('/api/thumbnail/with-refs', { method: 'POST', body: formData });
            const data = await res.json();
            if (res.ok && data.thumbnails?.length) renderThumbnails(data.thumbnails, data.paths);
            else throw new Error('No thumbnails generated');
        } else {
            const res = await fetch('/api/thumbnail', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: state.title, niche_style: style }) });
            const data = await res.json();
            if (res.ok && data.thumbnails?.length) renderThumbnails(data.thumbnails, data.paths);
            else throw new Error('No thumbnails generated');
        }
    } catch (e) {
        alert('Thumbnail regeneration failed: ' + e.message);
        document.getElementById('thumb-loading').classList.add('hidden');
    }
}

// ---------------------------------------------------------------------------
// Step 6: Build
// ---------------------------------------------------------------------------
function populateBuildSummary() {
    document.getElementById('summary-niche').textContent = state.nicheData?.name || state.niche;
    document.getElementById('summary-title').textContent = state.title;
    const wc = state.script.split(/\s+/).length;
    document.getElementById('summary-words').textContent = `${wc} words (~${Math.round(wc / 150)} min)`;
    document.getElementById('summary-voice').textContent = state.voice;
}

const cookingManager = {
    jobId: null,
    evtSrc: null,
    title: '',
    result: null,
    msgCount: 0,

    get isCooking() { return this.jobId && !this.result; },

    async start() {
        if (this.isCooking) {
            alert('A video is already cooking. Wait for it to finish or cancel it.');
            return;
        }

        this.result = null;
        this.msgCount = 0;
        this.title = state.title;

        document.getElementById('build-start').classList.add('hidden');
        document.getElementById('build-progress').classList.remove('hidden');
        document.getElementById('progress-log').innerHTML = '';
        document.getElementById('progress-bar').style.width = '0%';

        try {
            const notifyEmail = document.getElementById('notify-email')?.value?.trim() || '';
            const res = await fetch('/api/build', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    script: state.script,
                    voiceover_path: state.voiceoverPath,
                    title: state.title,
                    niche: state.niche,
                    recipe: state.nicheData?.recipe || 'animated_explainer',
                    thumbnail_path: state.thumbnailPath,
                    notify_email: notifyEmail,
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                if (res.status === 401) { showAuthModal(); }
                else { alert(data.detail || 'Build failed'); }
                throw new Error(data.detail || 'Build failed');
            }
            this.jobId = data.job_id;
            this._showCookingBar();
            this._connect();
        } catch (e) {
            if (e.message.includes('Sign in')) showAuthModal();
            document.getElementById('build-start').classList.remove('hidden');
            document.getElementById('build-progress').classList.add('hidden');
        }
    },

    _connect() {
        this.evtSrc = new EventSource(`/api/build/${this.jobId}/progress`);
        const progressBar = document.getElementById('progress-bar');
        const progressLog = document.getElementById('progress-log');

        this.evtSrc.addEventListener('progress', (e) => {
            this.msgCount++;
            const msg = JSON.parse(e.data);
            document.getElementById('cooking-bar-status').textContent = msg.message.substring(0, 60);
            if (progressLog) {
                const line = document.createElement('div');
                line.textContent = `> ${msg.message}`;
                progressLog.appendChild(line);
                progressLog.scrollTop = progressLog.scrollHeight;
            }
            if (progressBar) {
                progressBar.style.width = Math.min(95, Math.round((this.msgCount / 30) * 100)) + '%';
            }
        });

        this.evtSrc.addEventListener('complete', (e) => {
            this.evtSrc.close();
            this.evtSrc = null;
            this.result = JSON.parse(e.data);
            state.videoUrl = this.result.output_url;
            state.videoPath = this.result.output_path;
            this._hideCookingBar();

            if (state.page === 'pipeline' && state.step === 6) {
                if (progressBar) progressBar.style.width = '100%';
                setTimeout(() => showUploadKit(this.result), 500);
            } else {
                this._showToast();
            }
        });

        this.evtSrc.addEventListener('error', (e) => {
            if (e.data) {
                const err = JSON.parse(e.data).error || 'Unknown error';
                alert('Build failed: ' + err);
            }
            this.evtSrc.close();
            this.evtSrc = null;
            this.jobId = null;
            this._hideCookingBar();
        });
    },

    _showCookingBar() {
        const bar = document.getElementById('cooking-bar');
        document.getElementById('cooking-bar-title').textContent = this.title || 'your video';
        document.getElementById('cooking-bar-status').textContent = 'Starting...';
        bar.classList.remove('hidden');
    },

    _hideCookingBar() {
        document.getElementById('cooking-bar').classList.add('hidden');
    },

    _showToast() {
        document.getElementById('toast-title').textContent = this.title;
        document.getElementById('toast').classList.remove('hidden');
        setTimeout(() => dismissToast(), 15000);
    },

    viewProgress() {
        navigateTo('pipeline');
        goToStep(6);
        document.getElementById('build-start').classList.add('hidden');
        document.getElementById('build-progress').classList.remove('hidden');
    },

    viewResult() {
        dismissToast();
        navigateTo('pipeline');
        goToStep(6);
        if (this.result) showUploadKit(this.result);
    },

    cancel() {
        if (this.evtSrc) { this.evtSrc.close(); this.evtSrc = null; }
        if (this.jobId) {
            fetch(`/api/build/${this.jobId}`, { method: 'DELETE' }).catch(() => {});
        }
        this.jobId = null;
        this.result = null;
        this._hideCookingBar();
        if (state.page === 'pipeline' && state.step === 6) {
            document.getElementById('build-start').classList.remove('hidden');
            document.getElementById('build-progress').classList.add('hidden');
        }
    },
};

function dismissToast() {
    document.getElementById('toast').classList.add('hidden');
}

async function startBuild() {
    if (!currentUser) {
        showAuthModal();
        return;
    }
    await cookingManager.start();
}

async function showUploadKit(buildResult) {
    document.getElementById('build-progress').classList.add('hidden');
    document.getElementById('upload-kit').classList.remove('hidden');
    document.getElementById('result-video').src = buildResult.output_url;
    const dl = document.getElementById('download-link');
    dl.href = buildResult.output_url;
    dl.setAttribute('download', 'video.mp4');
    if (state.thumbnailUrl) {
        document.getElementById('kit-thumb').src = state.thumbnailUrl;
        document.getElementById('kit-thumb-dl').href = state.thumbnailUrl;
        document.getElementById('kit-thumb-dl').setAttribute('download', 'thumbnail.png');
        document.getElementById('kit-thumb-wrap').classList.remove('hidden');
    } else {
        document.getElementById('kit-thumb-wrap').classList.add('hidden');
    }
    document.getElementById('kit-title').textContent = state.title;
    try {
        const res = await fetch('/api/upload-kit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: state.title, script: state.script, niche: state.niche }) });
        const kit = await res.json();
        document.getElementById('kit-desc').textContent = kit.description || '';
        document.getElementById('kit-tags').textContent = Array.isArray(kit.tags) ? kit.tags.join(', ') : (kit.tags || '');
    } catch {
        document.getElementById('kit-desc').textContent = `Check out this video: ${state.title}`;
        document.getElementById('kit-tags').textContent = 'youtube, video';
    }
}

// ---------------------------------------------------------------------------
// Script Studio
// ---------------------------------------------------------------------------
function switchStudioTab(prefix, tabId, btn) {
    const parent = btn.closest('.page-container') || document;
    parent.querySelectorAll('.studio-tab').forEach(t => t.classList.remove('active'));
    parent.querySelectorAll('.studio-panel').forEach(p => p.classList.add('hidden'));
    btn.classList.add('active');
    document.getElementById(tabId)?.classList.remove('hidden');
}

async function fetchChannelData() {
    const btn = document.getElementById('btn-fetch-channel');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/channel/fetch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_url: document.getElementById('ss-channel-url').value.trim(),
                max_videos: parseInt(document.getElementById('ss-video-count').value),
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        state.channelData = data;
        document.getElementById('ss-channel-data').textContent = JSON.stringify(data, null, 2);
        document.getElementById('ss-channel-result').classList.remove('hidden');
    } catch (e) {
        alert('Channel fetch failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

async function analyzeChannel() {
    if (!ensureAuth(analyzeChannel)) return;
    const btn = document.querySelector('#ss-channel-result .btn-primary');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/channel/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_data: state.channelData }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        state.channelAnalysis = data.analysis;
        document.getElementById('ss-analysis-text').textContent = data.analysis;
        document.getElementById('ss-analysis').classList.remove('hidden');
    } catch (e) {
        alert('Analysis failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

async function generateIdeas() {
    if (!ensureAuth(generateIdeas)) return;
    const btn = document.querySelector('#ss-ideas .btn-primary');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/ideas', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_data: state.channelData,
                num_ideas: parseInt(document.getElementById('ss-idea-count').value),
                analysis: state.channelAnalysis || '',
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        const list = document.getElementById('ss-ideas-list');
        list.innerHTML = '';
        data.ideas.forEach(idea => {
            const card = document.createElement('div');
            card.className = 'title-card';
            card.innerHTML = `<p class="text-gray-100 text-sm">${idea}</p>`;
            card.addEventListener('click', () => {
                document.querySelectorAll('#ss-ideas-list .title-card').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                document.getElementById('ss-selected-idea').value = idea;
            });
            list.appendChild(card);
        });
    } catch (e) {
        alert('Idea generation failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

async function generateStudioTitles() {
    if (!ensureAuth(generateStudioTitles)) return;
    const btn = document.querySelector('#ss-titles .btn-primary');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/titles/claude', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_idea: document.getElementById('ss-title-idea').value.trim(),
                channel_data: state.channelData,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        const list = document.getElementById('ss-titles-list');
        list.innerHTML = '';
        data.titles.forEach(t => {
            const card = document.createElement('div');
            card.className = 'title-card';
            card.innerHTML = `<p class="text-gray-100">${t}</p>`;
            card.addEventListener('click', () => {
                document.querySelectorAll('#ss-titles-list .title-card').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                document.getElementById('ss-selected-title').value = t;
            });
            list.appendChild(card);
        });
    } catch (e) {
        alert('Title generation failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

async function generateStudioScript() {
    if (!ensureAuth(generateStudioScript)) return;
    const btn = document.querySelector('#ss-script .btn-primary:first-of-type');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/script/claude', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: document.getElementById('ss-script-title').value.trim(),
                video_idea: document.getElementById('ss-script-idea').value.trim(),
                channel_data: state.channelData,
                target_minutes: parseInt(document.getElementById('ss-script-length').value),
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        document.getElementById('ss-script-output').value = data.script;
        const wc = data.script.split(/\s+/).length;
        document.getElementById('ss-word-count').textContent = `${wc} words (~${Math.round(wc / 150)} min)`;
    } catch (e) {
        alert('Script generation failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

function useScriptInPipeline() {
    const script = document.getElementById('ss-script-output').value.trim();
    const title = document.getElementById('ss-script-title').value.trim();
    if (script) state.script = script;
    if (title) state.title = title;
    navigateTo('pipeline');
    if (state.script) {
        document.getElementById('script-editor').value = state.script;
        updateWordCount();
        goToStep(3);
    }
}

// ---------------------------------------------------------------------------
// Voiceover Studio
// ---------------------------------------------------------------------------
async function loadVoiceOptions() {
    try {
        const res = await fetch('/api/voices/all');
        const voices = await res.json();
        const sel = document.getElementById('vo-voice');
        if (!sel) return;
        sel.innerHTML = '';
        voices.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = `${v.name} — ${v.tag}`;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.error('Failed to load voice options:', e);
    }
}

async function generateStudioVoiceover() {
    if (!ensureAuth(generateStudioVoiceover)) return;
    const btn = document.getElementById('btn-vo-generate');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/voiceover/studio', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                script: document.getElementById('vo-script').value.trim(),
                voice: document.getElementById('vo-voice').value,
                style_preset: document.getElementById('vo-style').value,
                custom_notes: document.getElementById('vo-custom-notes')?.value || '',
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        document.getElementById('vo-audio').src = data.url;
        document.getElementById('vo-download').href = data.url;
        document.getElementById('vo-result').classList.remove('hidden');
    } catch (e) {
        alert('Voiceover generation failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

// ---------------------------------------------------------------------------
// Thumbnail Studio
// ---------------------------------------------------------------------------
let tsRefFiles = [];

function handleTsRefUpload(input) {
    const preview = document.getElementById('ts-refs-preview');
    preview.innerHTML = '';
    tsRefFiles = [...input.files];
    tsRefFiles.forEach(f => {
        const img = document.createElement('img');
        img.className = 'ref-thumb';
        img.src = URL.createObjectURL(f);
        preview.appendChild(img);
    });
}

async function generateStudioThumbnails() {
    if (!ensureAuth(generateStudioThumbnails)) return;
    const btn = document.querySelector('#page-thumbnail-studio .btn-primary');
    setLoading(btn, true);
    const gallery = document.getElementById('ts-gallery');
    gallery.innerHTML = '<div class="col-span-2 text-center py-8"><svg class="animate-spin h-8 w-8 text-accent mx-auto" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg></div>';
    try {
        let data;
        if (tsRefFiles.length > 0) {
            const formData = new FormData();
            formData.append('title', document.getElementById('ts-title').value.trim());
            formData.append('style', document.getElementById('ts-style').value.trim());
            formData.append('count', document.getElementById('ts-count').value);
            tsRefFiles.forEach(f => formData.append('refs', f));
            const res = await fetch('/api/thumbnail/with-refs', { method: 'POST', body: formData });
            data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Failed');
        } else {
            const res = await fetch('/api/thumbnail', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: document.getElementById('ts-title').value.trim(), niche_style: document.getElementById('ts-style').value.trim(), count: parseInt(document.getElementById('ts-count').value) }),
            });
            data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Failed');
        }
        gallery.innerHTML = '';
        (data.thumbnails || []).forEach((url, i) => {
            const card = document.createElement('div');
            card.className = 'thumb-card';
            card.innerHTML = `<img src="${url}" alt="Thumbnail ${i + 1}"><div class="p-2 text-center"><a href="${url}" download="thumbnail_${i + 1}.png" class="copy-btn text-xs">Download</a></div>`;
            gallery.appendChild(card);
        });
    } catch (e) {
        gallery.innerHTML = '';
        alert('Thumbnail generation failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

// ---------------------------------------------------------------------------
// Niche Screener
// ---------------------------------------------------------------------------
async function analyzeNiche() {
    if (!ensureAuth(analyzeNiche)) return;
    const btn = document.querySelector('#page-niche-screener .btn-primary');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/niche/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                youtube_url: document.getElementById('ns-url').value.trim(),
                minutes: parseInt(document.getElementById('ns-minutes').value),
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed');
        document.getElementById('ns-analysis').textContent = data.summary || JSON.stringify(data.profile, null, 2);
        document.getElementById('ns-json').textContent = JSON.stringify(data.profile, null, 2);
        document.getElementById('ns-result').classList.remove('hidden');
    } catch (e) {
        alert('Niche analysis failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

function useNicheProfile() {
    navigateTo('pipeline');
    goToStep(1);
}

// ---------------------------------------------------------------------------
// Channel Analyzer
// ---------------------------------------------------------------------------
async function fetchChannelForAnalyzer() {
    const btn = document.querySelector('#page-channel-analyzer .btn-primary');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/channel/fetch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_url: document.getElementById('ca-url').value.trim(),
                max_videos: parseInt(document.getElementById('ca-count').value),
            }),
        });
        const channelData = await res.json();
        if (!res.ok) throw new Error(channelData.detail || 'Failed');

        let summary = `Channel: ${channelData.channel_name || 'N/A'}\n`;
        summary += `Subscribers: ${channelData.subscriber_count || 'N/A'}\n`;
        summary += `Videos fetched: ${(channelData.videos || []).length}\n`;
        document.getElementById('ca-summary').textContent = summary;

        const aRes = await fetch('/api/channel/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_data: channelData }),
        });
        const aData = await aRes.json();
        document.getElementById('ca-analysis').textContent = aData.analysis || 'Analysis not available (Claude key needed in Settings)';
        document.getElementById('ca-result').classList.remove('hidden');
    } catch (e) {
        alert('Channel analysis failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------
async function loadHistory() {
    try {
        const filter = document.getElementById('history-filter').value;
        const res = await fetch(`/api/history?type=${filter}`);
        const data = await res.json();
        const list = document.getElementById('history-list');
        if (!data.entries || data.entries.length === 0) {
            list.innerHTML = '<p class="text-gray-500 text-center py-8">No history yet. Generate something to see it here.</p>';
            return;
        }
        list.innerHTML = '';
        data.entries.forEach(entry => {
            const item = document.createElement('div');
            item.className = 'history-item';
            item.innerHTML = `
                <div class="flex items-center justify-between">
                    <div>
                        <span class="text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">${entry.type}</span>
                        <span class="text-sm text-gray-300 ml-2">${entry.title || entry.description || 'Untitled'}</span>
                    </div>
                    <span class="text-xs text-gray-600">${new Date(entry.timestamp).toLocaleDateString()}</span>
                </div>
            `;
            list.appendChild(item);
        });
    } catch {
        document.getElementById('history-list').innerHTML = '<p class="text-gray-500 text-center py-8">Could not load history.</p>';
    }
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
async function loadSettingsFromServer() {
    // Ops-only: only admins can read key status, and we use the raw fetch so a
    // 403/401 here never triggers the global sign-in modal on normal page loads.
    if (!(currentUser && currentUser.is_admin)) return;
    try {
        const res = await _origFetch('/api/settings/keys');
        if (!res.ok) return;
        const data = await res.json();
        Object.entries(data).forEach(([key, info]) => {
            const input = document.getElementById(`key-${key}`);
            if (input && info.configured) input.placeholder = 'Configured (hidden)';
        });
    } catch { /* Settings load is best-effort */ }
}

async function saveSettings() {
    const keys = {};
    const fields = ['gemini', 'claude', 'youtube', 'atlascloud', 'heygen', 'pexels', 'downsub'];
    fields.forEach(f => {
        const val = document.getElementById(`key-${f}`)?.value?.trim();
        if (val) keys[f] = val;
    });
    try {
        const res = await fetch('/api/settings/keys', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(keys),
        });
        const data = await res.json();
        document.getElementById('settings-status').textContent = data.message || 'Saved!';
        setTimeout(() => { document.getElementById('settings-status').textContent = ''; }, 3000);
    } catch (e) {
        document.getElementById('settings-status').textContent = 'Save failed: ' + e.message;
    }
}

async function testKey(name) {
    const btn = event.target;
    const origText = btn.textContent;
    btn.textContent = '...';
    btn.disabled = true;
    try {
        const val = document.getElementById(`key-${name}`)?.value?.trim();
        const res = await fetch('/api/settings/test-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key_name: name, key_value: val || '' }),
        });
        const data = await res.json();
        btn.textContent = data.ok ? 'OK' : 'Fail';
        btn.style.color = data.ok ? '#10b981' : '#ef4444';
        setTimeout(() => { btn.textContent = origText; btn.style.color = ''; btn.disabled = false; }, 2000);
    } catch {
        btn.textContent = 'Error';
        setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
    }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function setLoading(btn, loading) {
    if (!btn) return;
    const textEl = btn.querySelector('.btn-text');
    const loadEl = btn.querySelector('.btn-loading');
    if (textEl && loadEl) {
        textEl.classList.toggle('hidden', loading);
        loadEl.classList.toggle('hidden', !loading);
    }
    btn.disabled = loading;
}

function copyText(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const text = el.value || el.textContent;
    navigator.clipboard.writeText(text);
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
async function checkAuth() {
    try {
        const res = await _origFetch('/api/auth/me');
        const data = await res.json();
        if (data.user) {
            currentUser = data.user;
            updateAuthUI();
        }
    } catch (e) {
        console.error('Auth check failed:', e);
    } finally {
        authReady = true;
    }
}

function updateAuthUI() {
    const loginBtn = document.getElementById('btn-login');
    const userBtn = document.getElementById('btn-user-menu');
    const creditsDisplay = document.getElementById('credits-display');

    const navSettings = document.getElementById('nav-settings');
    const navSettingsMobile = document.getElementById('nav-settings-mobile');
    const isAdmin = !!(currentUser && currentUser.is_admin);
    navSettings?.classList.toggle('hidden', !isAdmin);
    navSettingsMobile?.classList.toggle('hidden', !isAdmin);
    if (!isAdmin && state.page === 'settings') {
        navigateTo('pipeline');
    }

    if (currentUser) {
        loginBtn.classList.add('hidden');
        userBtn.classList.remove('hidden');
        userBtn.textContent = currentUser.email[0].toUpperCase();
        document.getElementById('user-email-display').textContent = currentUser.email;
        document.getElementById('user-plan-display').textContent = currentUser.plan === 'free' ? 'Free trial' : 'Pro';
        document.getElementById('credits-count').textContent = currentUser.credits + ' credits';
        document.getElementById('credits-plan').textContent = currentUser.plan === 'free' ? 'trial' : 'pro';
        creditsDisplay.classList.remove('hidden');
    } else {
        loginBtn.classList.remove('hidden');
        userBtn.classList.add('hidden');
        const cc = document.getElementById('credits-count');
        const cp = document.getElementById('credits-plan');
        if (cc) cc.textContent = '3 free';
        if (cp) cp.textContent = 'trial';
        creditsDisplay.classList.remove('hidden');
    }
}

function toggleUserMenu() {
    document.getElementById('user-menu').classList.toggle('hidden');
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('#btn-user-menu') && !e.target.closest('#user-menu')) {
        document.getElementById('user-menu')?.classList.add('hidden');
    }
});

function showAuthModal() {
    const modal = document.getElementById('auth-modal');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
    document.getElementById('auth-step-email').classList.remove('hidden');
    document.getElementById('auth-step-code').classList.add('hidden');
    document.getElementById('auth-email').value = '';
    document.getElementById('auth-code').value = '';
    document.getElementById('auth-email-error').classList.add('hidden');
    document.getElementById('auth-code-error').classList.add('hidden');
    setTimeout(() => document.getElementById('auth-email').focus(), 100);
}

function hideAuthModal() {
    const modal = document.getElementById('auth-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
}

async function authSendCode() {
    const email = document.getElementById('auth-email').value.trim();
    if (!email || !email.includes('@')) {
        document.getElementById('auth-email-error').textContent = 'Enter a valid email';
        document.getElementById('auth-email-error').classList.remove('hidden');
        return;
    }
    const btn = document.getElementById('btn-send-code');
    btn.disabled = true;
    btn.textContent = 'Sending...';
    try {
        const res = await fetch('/api/auth/send-code', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
        document.getElementById('auth-step-email').classList.add('hidden');
        document.getElementById('auth-step-code').classList.remove('hidden');
        document.getElementById('auth-email-display').textContent = email;
        setTimeout(() => document.getElementById('auth-code').focus(), 100);
    } catch (e) {
        document.getElementById('auth-email-error').textContent = e.message;
        document.getElementById('auth-email-error').classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Send verification code';
    }
}

async function authVerifyCode() {
    const email = document.getElementById('auth-email').value.trim();
    const code = document.getElementById('auth-code').value.trim();
    if (code.length !== 6) {
        document.getElementById('auth-code-error').textContent = 'Enter the 6-digit code';
        document.getElementById('auth-code-error').classList.remove('hidden');
        return;
    }
    const btn = document.getElementById('btn-verify-code');
    btn.disabled = true;
    btn.textContent = 'Verifying...';
    try {
        const res = await fetch('/api/auth/verify', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, code }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Invalid code');
        currentUser = data.user;
        updateAuthUI();
        try { window.posthog?.identify(String(currentUser.id), { email: currentUser.email, plan: currentUser.plan }); } catch (_) {}
        hideAuthModal();
        if (pendingAuthAction) {
            const action = pendingAuthAction;
            pendingAuthAction = null;
            setTimeout(action, 150);
        }
    } catch (e) {
        document.getElementById('auth-code-error').textContent = e.message;
        document.getElementById('auth-code-error').classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Verify';
    }
}

function authBackToEmail() {
    document.getElementById('auth-step-email').classList.remove('hidden');
    document.getElementById('auth-step-code').classList.add('hidden');
    document.getElementById('auth-code-error').classList.add('hidden');
}

async function handleLogout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) { /* ignore */ }
    currentUser = null;
    updateAuthUI();
    document.getElementById('user-menu').classList.add('hidden');
}
