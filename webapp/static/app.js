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
    voice: 'leo',
    targetMinutes: 8,
    imageQuality: 'standard',
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
    avatarId: '',
    voiceId: '',
    avatarName: '',
    heygenVoiceName: '',
};

let previewAudio = null;
let heygenConfigured = false;

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
let currentUser = null;
let authReady = false;
let pendingAuthAction = null;

// Defense-in-depth: any protected API call that comes back 401 forces the
// sign-in modal; 402 (no credits) forces the pricing modal.
const _origFetch = window.fetch.bind(window);
window.fetch = async function (input, init) {
    const res = await _origFetch(input, init);
    try {
        const url = typeof input === 'string' ? input : (input && input.url) || '';
        if (url.includes('/api/') && !url.includes('/api/auth/')) {
            if (res.status === 401) {
                currentUser = null;
                if (typeof updateAuthUI === 'function') updateAuthUI();
                if (typeof showAuthModal === 'function') showAuthModal();
            } else if (res.status === 402) {
                if (typeof isTrialUser === 'function' && isTrialUser()) {
                    if (typeof showTrialExhaustedModal === 'function') showTrialExhaustedModal();
                } else if (
                    typeof isPaidUser === 'function' && isPaidUser()
                    && typeof isTrialUser === 'function' && !isTrialUser()
                    && typeof showCreditsNeededModal === 'function'
                ) {
                    showCreditsNeededModal({
                        need: 1,
                        have: (typeof currentUser !== 'undefined' && currentUser) ? currentUser.credits : 0,
                        reason: 'credits',
                    });
                } else if (typeof showPricingModal === 'function') {
                    showPricingModal({ reason: 'cook' });
                }
            }
        }
    } catch (_) { /* ignore */ }
    return res;
};

/** Parse JSON from a fetch Response without throwing on HTML/plain-text error bodies. */
async function readJson(res, fallback = null) {
    const text = await res.text();
    if (!text) return fallback;
    try {
        return JSON.parse(text);
    } catch (_) {
        return fallback;
    }
}

function safeJsonParse(text, fallback = null) {
    if (text == null || text === '') return fallback;
    try {
        return JSON.parse(text);
    } catch (_) {
        return fallback;
    }
}

// Signed-in only — lets free users walk the pipeline (steps 1–5).
function ensureSignedIn(retry) {
    if (!currentUser) {
        pendingAuthAction = typeof retry === 'function' ? retry : null;
        showAuthModal();
        return false;
    }
    return true;
}

// Paid/trial required — used only when cooking a video (step 6).
function ensureCanCook(retry) {
    if (!ensureSignedIn(retry)) return false;
    if (!isPaidUser()) {
        pendingAuthAction = typeof retry === 'function' ? retry : null;
        persistPipelineState(); // keep progress across Stripe redirect
        showPricingModal({ reason: 'cook' });
        return false;
    }
    if (isTrialUser() && (currentUser.credits || 0) <= 0) {
        showTrialExhaustedModal();
        return false;
    }
    return true;
}

// Back-compat alias used by tools; prefer ensureSignedIn / ensureCanCook.
function ensureAuth(retry) {
    return ensureSignedIn(retry);
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
        cfg = await readJson(res, null);
        if (!cfg) return;
    } catch { return; }

    // Sentry (error tracking) — legitimate interest, load regardless of consent.
    if (cfg.sentry_dsn) {
        const s = document.createElement('script');
        s.src = 'https://browser.sentry-cdn.com/7.120.0/bundle.min.js';
        s.crossOrigin = 'anonymous';
        s.onload = () => {
            try {
                window.Sentry?.init({
                    dsn: cfg.sentry_dsn,
                    tracesSampleRate: 0.1,
                    environment: 'production',
                    ignoreErrors: [
                        'ResizeObserver loop',
                        'Non-Error promise rejection',
                        'NetworkError when attempting to fetch',
                        'Load failed',
                        'Failed to fetch',
                        'AbortError',
                        'play() request was interrupted',
                        'The play() request was interrupted',
                        'JSON.parse',
                        'unexpected character at line',
                        'Unexpected token',
                        'is not valid JSON',
                        // Browser-extension / media noise (not our app)
                        'addListener',
                        'Picture-in-Picture',
                        'requestPictureInPicture',
                        'MetaMask',
                        'chrome-extension://',
                        'moz-extension://',
                        'nativeIframe',
                        'has already been declared',
                        "Cannot read properties of undefined (reading 'emit')",
                        "reading 'emit'",
                    ],
                    beforeSend(event) {
                        try {
                            const values = event?.exception?.values || [];
                            for (const v of values) {
                                const msg = String(v?.value || '');
                                const frames = (v?.stacktrace?.frames || [])
                                    .map(f => `${f?.filename || ''} ${f?.abs_path || ''}`)
                                    .join(' ');
                                if (/nativeIframe/i.test(msg) || /has already been declared/i.test(msg)) return null;
                                if (/reading ['\"]emit['\"]/i.test(msg)) return null;
                                if (/chrome-extension:\/\//i.test(frames) || /moz-extension:\/\//i.test(frames)) return null;
                            }
                        } catch (_) {}
                        return event;
                    },
                });
                if (currentUser) {
                    window.Sentry?.setUser({ id: String(currentUser.id), email: currentUser.email });
                }
            } catch (_) {}
        };
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
    applyFeatureFlags(cfg);
}

let _featureFlags = {
    voice_clone_enabled: false,
    voice_clone_credit_cost: 1,
    recipe_brain_enabled: false,
    max_voiceover_minutes: 25,
    max_voiceover_words: 3750,
    hq_credit_cost: 3,
    hq_max_minutes: 12,
    storyboard_pack_max_minutes: 25,
    storyboard_trial_pack_max_minutes: 8,
    storyboard_trial_pack_limit: 2,
    storyboard_cook_max_minutes: 8,
    storyboard_animate_credits_flat: 12,
};

function applyFeatureFlags(cfg) {
    if (!cfg) return;
    _featureFlags.voice_clone_enabled = !!cfg.voice_clone_enabled;
    _featureFlags.voice_clone_credit_cost = Number(cfg.voice_clone_credit_cost || 0);
    _featureFlags.recipe_brain_enabled = !!cfg.recipe_brain_enabled;
    _featureFlags.max_voiceover_minutes = Number(cfg.max_voiceover_minutes || 25);
    _featureFlags.max_voiceover_words = Number(cfg.max_voiceover_words || 3750);
    _featureFlags.hq_credit_cost = Number(cfg.hq_credit_cost || 3);
    _featureFlags.hq_max_minutes = Number(cfg.hq_max_minutes || 12);
    _featureFlags.storyboard_pack_max_minutes = Number(cfg.storyboard_pack_max_minutes || 25);
    _featureFlags.storyboard_trial_pack_max_minutes = Number(cfg.storyboard_trial_pack_max_minutes || 8);
    _featureFlags.storyboard_trial_pack_limit = Number(cfg.storyboard_trial_pack_limit || 2);
    _featureFlags.storyboard_cook_max_minutes = Number(cfg.storyboard_cook_max_minutes || 8);
    _featureFlags.storyboard_animate_credits_flat = Number(cfg.storyboard_animate_credits_flat || 12);
    const cloneBtn = document.getElementById('vo-mode-clone');
    if (cloneBtn) cloneBtn.classList.toggle('hidden', !_featureFlags.voice_clone_enabled);
}

function maybeShowCookieBanner(cfg) {
    if (!cfg.posthog_key) return;
    if (localStorage.getItem('cr_cookie_consent')) return;
    const banner = document.getElementById('cookie-banner');
    if (banner) banner.style.display = 'flex';
}

function acceptCookies() {
    localStorage.setItem('cr_cookie_consent', 'accepted');
    const b = document.getElementById('cookie-banner');
    if (b) b.style.display = 'none';
    initAnalytics();
}

function declineCookies() {
    localStorage.setItem('cr_cookie_consent', 'declined');
    const b = document.getElementById('cookie-banner');
    if (b) b.style.display = 'none';
}

document.addEventListener('DOMContentLoaded', async () => {
    await checkAuth();
    initAnalytics();
    loadNiches();
    bindEvents();
    loadSettingsFromServer();
    loadVoiceOptions();
    // Prefetch pipeline voices so step 4 never flashes empty
    loadVoices();
    refreshResourcesNewBadge();

    if (currentUser) cookingManager.restore();

    const restored = restorePipelineState();
    const hash = window.location.hash.slice(1);
    if (hash) navigateTo(hash);
    else if (restored) navigateTo('pipeline');

    maybeShowWelcomeCelebration();
});

// ---------------------------------------------------------------------------
// Page Navigation (SPA routing)
// ---------------------------------------------------------------------------
function navigateTo(page) {
    if (page === 'settings' && !currentUser) {
        showAuthModal();
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
    const toolPages = ['script-studio', 'voiceover-studio', 'thumbnail-studio', 'niche-screener', 'channel-analyzer', 'resources'];
    if (page === 'pipeline') {
        document.querySelector('[data-page="pipeline"]')?.classList.add('active');
        goToStep(state.step);
    } else if (toolPages.includes(page)) {
        document.querySelector('[data-page="tools"]')?.classList.add('active');
    } else {
        document.querySelector(`[data-page="${page}"]`)?.classList.add('active');
    }

    if (page === 'settings') {
        loadIntegrations();
        loadSettingsFromServer();
        const adminKeys = document.getElementById('settings-admin-keys');
        if (adminKeys) adminKeys.classList.toggle('hidden', !(currentUser && currentUser.is_admin));
    }

    // Close menus
    document.getElementById('tools-menu')?.classList.add('hidden');
    document.getElementById('mobile-menu')?.classList.add('hidden');

    if (page === 'history') { try { loadHistory(); } catch(_) {} }
    if (page === 'billing') { try { loadBillingPage(); } catch(_) {} }
    if (page === 'recipe-brain') { try { initRecipeBrainPage(); } catch(_) {} }
    if (page === 'script-studio') { try { syncAdminChannelUI(); } catch(_) {} }
    if (page === 'resources') { try { loadResourcesPage(); } catch(_) {} }
    if (page === 'niche-finder') { try { initNicheFinderPage(); } catch(_) {} }
    if (page === 'niche-intel') { try { initNicheIntelPage(); } catch(_) {} }

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
function syncPipelineChrome() {
    const sb = isStoryboardRecipe();
    document.getElementById('stepper-default')?.classList.toggle('hidden', sb);
    document.getElementById('stepper-storyboard')?.classList.toggle('hidden', !sb);

    const heading = document.getElementById('step2-heading');
    const sub = document.getElementById('step2-sub');
    const progress = document.getElementById('step2-progress-label');
    const nextBtn = document.getElementById('btn-next-2');
    const topicInput = document.getElementById('topic-input');
    if (sb) {
        if (heading) heading.textContent = 'Optional: pick a title';
        if (sub) sub.textContent = 'Skip or pick a title — you’ll refine it on the episode step.';
        if (progress) progress.textContent = 'Optional';
        if (nextBtn) nextBtn.textContent = 'Back to episode';
        if (topicInput) topicInput.placeholder = 'Optional seed: exams, lying, jealousy…';
    } else {
        if (heading) heading.textContent = 'Pick a title';
        if (sub) sub.textContent = 'We drafted a few from proven patterns. Pick one or write your own.';
        if (progress) progress.textContent = 'Step 2 of 6';
        if (nextBtn) nextBtn.textContent = 'Next';
        if (topicInput) topicInput.placeholder = 'Optional: enter a topic to guide title generation...';
    }
}

function updateStepperActive(n) {
    const sb = isStoryboardRecipe();
    if (sb) {
        let active = 1;
        if (n === 'sb-cast') active = 2;
        else if (n === 'storyboard') active = 3;
        else if (n === 'sb-pack') active = 4;
        else if (n === 'sb-assemble') active = 5;
        else if (n === 2) active = 3; // title ideas live inside episode flow
        else if (typeof n === 'number' && n >= 3) active = 4;
        document.querySelectorAll('#stepper-storyboard .step-indicator').forEach(ind => {
            const s = parseInt(ind.dataset.sbStep);
            const dot = ind.querySelector('.step-dot');
            dot.classList.remove('active', 'done');
            if (s < active) dot.classList.add('done');
            else if (s === active) dot.classList.add('active');
        });
        document.querySelectorAll('#stepper-storyboard .step-line').forEach((line, i) => {
            line.classList.toggle('done', i < active - 1);
        });
        return;
    }
    const stepNum = typeof n === 'number' ? n : 1;
    document.querySelectorAll('#stepper-default .step-indicator').forEach(ind => {
        const s = parseInt(ind.dataset.step);
        const dot = ind.querySelector('.step-dot');
        dot.classList.remove('active', 'done');
        if (s < stepNum) dot.classList.add('done');
        else if (s === stepNum) dot.classList.add('active');
    });
    document.querySelectorAll('#stepper-default .step-line').forEach((line, i) => {
        line.classList.toggle('done', i < stepNum - 1);
    });
}

function goToStep(n) {
    if (n === 'sb-assemble') {
        const reason = _sbBoardCookBlockReason();
        if (reason) {
            alert(reason);
            n = _sbJobId ? 'sb-pack' : 'storyboard';
        }
    }
    state.step = n;
    syncPipelineChrome();
    document.querySelectorAll('.step-panel').forEach(p => p.classList.add('hidden'));
    let panelId;
    if (n === 'storyboard') panelId = 'step-storyboard';
    else if (n === 'sb-cast') panelId = 'step-sb-cast';
    else if (n === 'sb-pack') panelId = 'step-sb-pack';
    else if (n === 'sb-assemble') panelId = 'step-sb-assemble';
    else panelId = `step-${n}`;
    const panel = document.getElementById(panelId);
    if (panel) {
        panel.classList.remove('hidden');
        panel.style.animation = 'none';
        panel.offsetHeight;
        panel.style.animation = '';
    }

    updateStepperActive(n);

    if (n === 4) {
        if (isAvatarRecipe()) {
            setupAvatarStep();
        } else {
            document.getElementById('step4-avatar')?.classList.add('hidden');
            document.getElementById('step4-voiceover')?.classList.remove('hidden');
            loadVoices();
        }
    }
    if (n === 'sb-cast') initStoryboardCastUI();
    if (n === 'storyboard') initStoryboardPackUI();
    if (n === 'sb-assemble') {
        const notify = document.getElementById('sb-assemble-notify');
        if (notify && !notify.value && currentUser?.email) notify.value = currentUser.email;
        _sbSyncAnimateUI();
    }
    if (n === 'sb-pack') _sbSyncBoardCookControls();

    if (typeof n === 'number' && n >= 2) persistPipelineState();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function goToCompletedStep(n) {
    if (n === 'sb-cast' || n === 'storyboard' || n === 'sb-pack' || n === 'sb-assemble') {
        if (!isStoryboardRecipe()) return;
        if (n === 'storyboard' || n === 'sb-pack' || n === 'sb-assemble') {
            if (!_sbCastHasLook()) {
                alert('Generate at least one character look first.');
                goToStep('sb-cast');
                return;
            }
        }
        if (n === 'sb-assemble') {
            const reason = _sbBoardCookBlockReason();
            if (reason) {
                alert(reason);
                goToStep('sb-pack');
                return;
            }
            const notify = document.getElementById('sb-assemble-notify');
            if (notify && !notify.value && currentUser?.email) notify.value = currentUser.email;
        }
        goToStep(n);
        return;
    }
    if (isStoryboardRecipe() && typeof n === 'number' && n > 2) {
        n = 2;
    }
    const cur = typeof state.step === 'number' ? state.step : 1;
    if (n < cur || n === cur) goToStep(n);
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
function bindEvents() {
    document.getElementById('btn-gen-titles').addEventListener('click', generateTitles);
    bindStoryboardPackUI();

    document.getElementById('custom-title').addEventListener('input', (e) => {
        state.title = e.target.value.trim();
        updateNextBtn2();
    });

    document.getElementById('btn-next-2').addEventListener('click', () => {
        if (!ensureSignedIn()) return;
        if (!state.title) return;
        if (isStoryboardRecipe()) {
            goToStep('storyboard');
            return;
        }
        goToStep(3);
    });

    document.getElementById('btn-gen-script').addEventListener('click', generateScript);

    document.getElementById('script-editor').addEventListener('input', (e) => {
        state.script = e.target.value;
        updateWordCount();
    });

    document.getElementById('btn-regen-script').addEventListener('click', generateScript);

    document.getElementById('btn-next-3').addEventListener('click', () => {
        if (!ensureSignedIn()) return;
        state.script = document.getElementById('script-editor').value.trim();
        if (!state.script) return;
        goToStep(4);
    });

    document.getElementById('btn-next-4').addEventListener('click', handleVoiceNext);

    document.getElementById('btn-next-5').addEventListener('click', () => {
        if (!ensureSignedIn()) return;
        goToStep(6);
        populateBuildSummary();
    });

    document.getElementById('btn-build').addEventListener('click', startBuild);

    document.getElementById('btn-make-another').addEventListener('click', resetPipeline);

    // Target minutes slider — free + trial capped; paid plans get full range
    const slider = document.getElementById('target-minutes');
    const label = document.getElementById('target-minutes-label');
    if (slider) {
        slider.addEventListener('input', () => {
            let val = parseInt(slider.value);
            const cap = effectiveMinuteCap();
            if (!hasFullLengthAccess() && val > cap) {
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
    clearPipelineDraft();
    Object.assign(state, {
        step: 1, niche: null, nicheData: null, title: '', script: '',
        voice: 'leo', targetMinutes: 8, imageQuality: 'standard',
        voiceoverPath: '', voiceoverUrl: '',
        thumbnailPath: '', thumbnailUrl: '', thumbnailRefs: [], videoUrl: '', videoPath: '',
        voiceMode: 'generate', uploadedVoPath: '',
        avatarId: '', voiceId: '', avatarName: '', heygenVoiceName: '',
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
            const needsTrial = niche.requires_trial || niche.status === 'requires_trial';
            const unavailable = niche.available === false || niche.status === 'coming_soon' || needsTrial;
            card.className = unavailable ? 'niche-card niche-card--soon' : 'niche-card';
            const previewHtml = niche.preview_gif
                ? `<div class="niche-preview">
                       <img src="${niche.preview_gif}" alt="${niche.name} preview" loading="lazy">
                   </div>`
                : '';
            const statusChip = needsTrial
                ? `<span class="status-chip new">Start free trial</span>`
                : unavailable
                ? `<span class="status-chip new">Coming soon</span>`
                : niche.status === 'proven'
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
            if (unavailable) {
                card.style.opacity = needsTrial ? '0.85' : '0.55';
                card.style.cursor = 'pointer';
                card.title = needsTrial ? 'Start your free trial to unlock Storyboard Pack' : 'Coming soon';
                card.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (needsTrial) {
                        if (!ensureSignedIn(() => showPricingModal({ reason: 'storyboard' }))) return;
                        showPricingModal({ reason: 'storyboard' });
                        return;
                    }
                    alert('This recipe is coming soon. Pick another recipe for now.');
                });
            } else {
                card.addEventListener('click', () => selectNiche(niche, card));
            }
            grid.appendChild(card);
        });
    } catch (e) {
        console.error('Failed to load niches:', e);
    }
}

function selectNiche(niche, card) {
    if (!ensureSignedIn(() => selectNiche(niche, card))) return;
    track('recipe_selected', { recipe: niche.id });
    document.querySelectorAll('.niche-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    state.niche = niche.id;
    state.nicheData = niche;
    state.voice = niche.default_voice || 'leo';
    state.targetMinutes = niche.default_minutes || 8;
    state.voiceMode = 'generate';
    state.uploadedVoPath = '';
    const slider = document.getElementById('target-minutes');
    if (slider) {
        slider.value = state.targetMinutes;
        document.getElementById('target-minutes-label').textContent = state.targetMinutes + ' min';
    }
    applyLengthSliderLimits();
    updateScriptLimitMsg();
    syncPipelineChrome();

    if ((niche.recipe || niche.id) === 'storyboard_pack') {
        setTimeout(() => goToStep('sb-cast'), 200);
        return;
    }
    setTimeout(() => goToStep(2), 300);
}

function isPaidUser() {
    return currentUser && ['pro', 'starter', 'daily', 'starter_trial', 'daily_trial'].includes(currentUser.plan);
}

function trialCredits() {
    const n = currentUser && currentUser.trial_credits;
    return (typeof n === 'number' && n > 0) ? n : 2;
}

function isTrialUser() {
    return currentUser && ['starter_trial', 'daily_trial'].includes(currentUser.plan);
}

/** Paid plans past trial can go up to max_paid_minutes; free + trial are capped. */
function hasFullLengthAccess() {
    return isPaidUser() && !isTrialUser();
}

function freeMinuteCap() {
    return state.nicheData?.max_free_minutes || 8;
}

/** Effective max minutes for the current user (trial hard-capped at 8). */
function effectiveMinuteCap() {
    if (hasFullLengthAccess()) return state.nicheData?.max_paid_minutes || 20;
    if (isTrialUser()) return 8;
    return freeMinuteCap();
}

function applyLengthSliderLimits() {
    const slider = document.getElementById('target-minutes');
    if (!slider) return;
    const cap = effectiveMinuteCap();
    const maxPaid = state.nicheData?.max_paid_minutes || 20;
    slider.max = hasFullLengthAccess() ? maxPaid : cap;
    let val = parseInt(slider.value) || state.targetMinutes || 8;
    if (val > cap) {
        val = cap;
        slider.value = cap;
        state.targetMinutes = cap;
        const label = document.getElementById('target-minutes-label');
        if (label) label.textContent = val + ' min';
    }
    const studioLen = document.getElementById('ss-script-length');
    if (studioLen) {
        studioLen.max = cap;
        if (parseInt(studioLen.value) > cap) studioLen.value = cap;
    }
}

function updateScriptLimitMsg() {
    const msg = document.getElementById('script-limit-msg');
    if (!msg) return;
    const limitSpan = document.getElementById('limit-minutes');
    const cap = effectiveMinuteCap();
    if (limitSpan) limitSpan.textContent = cap;
    const paras = msg.querySelectorAll('p');
    const body = paras[1];
    if (body) {
        if (isTrialUser()) {
            body.innerHTML = `Trial caps at <span id="limit-minutes">${cap}</span> min. Start your plan for up to 20 min videos.`;
        } else {
            body.innerHTML = `Free plan caps at <span id="limit-minutes">${cap}</span> min and watermarks output. Go Pro: up to 20 min, no watermark, 15 videos/mo.`;
        }
    }
    msg.classList.add('hidden');
    msg.style.display = 'none';
}

let _lengthPromptTimer = null;
function showLengthUpgradePrompt() {
    const msg = document.getElementById('script-limit-msg');
    if (!msg) return;
    const cap = effectiveMinuteCap();
    const paras = msg.querySelectorAll('p');
    const body = paras[1];
    if (body) {
        if (isTrialUser()) {
            body.innerHTML = `Trial caps at <span id="limit-minutes">${cap}</span> min. Start your plan for up to 20 min videos.`;
        } else {
            body.innerHTML = `Free plan caps at <span id="limit-minutes">${cap}</span> min and watermarks output. Go Pro: up to 20 min, no watermark, 15 videos/mo.`;
        }
    }
    msg.classList.remove('hidden');
    msg.style.display = 'flex';
    msg.classList.add('limit-pop');
    clearTimeout(_lengthPromptTimer);
    _lengthPromptTimer = setTimeout(() => msg.classList.remove('limit-pop'), 400);
}

function hideLengthUpgradePrompt() {
    const msg = document.getElementById('script-limit-msg');
    if (msg) { msg.classList.add('hidden'); msg.style.display = 'none'; }
}

let _pricingBillingCycle = 'monthly';

function showPricingModal(opts = {}) {
    if (!currentUser) { showAuthModal(); return; }
    const modal = document.getElementById('pricing-modal');
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
    setPricingPlan('monthly');

    const topupRow = document.getElementById('topup-row');
    const creditsPanel = document.getElementById('credits-needed-panel');
    const pricingGrid = modal.querySelector('.pricing-grid');
    const pricingToggle = document.getElementById('pricing-toggle');
    const heading = modal.querySelector('h2.cr-display');
    const subtitle = document.getElementById('pricing-subtitle');
    const starterBtn = document.getElementById('pricing-cta-starter');
    const dailyBtn = document.getElementById('pricing-cta-daily');
    const usedTrial = !!currentUser.trial_used;
    const need = Math.max(1, Number(opts.need || 1));
    const have = Math.max(0, Number(opts.have ?? currentUser.credits ?? 0));
    const paidSubscriber = isPaidUser() && !isTrialUser();
    const creditsShort = (
        opts.reason === 'credits'
        || (paidSubscriber && (opts.reason === 'cook' || opts.reason === 'hq') && have < need)
    );

    // Paid subscribers short on credits → top-up, not "subscribe again"
    if (creditsShort && paidSubscriber) {
        if (heading) heading.textContent = 'Not enough credits';
        if (subtitle) {
            subtitle.textContent = need === 1
                ? `You need 1 credit to cook. You have ${have}.`
                : `You need ${need} credits for this cook. You have ${have}.`;
        }
        const summary = document.getElementById('credits-needed-summary');
        if (summary) {
            summary.textContent = `Top up to continue — Pro visuals costs ${hqCreditCost()} credits, Standard costs 1.`;
        }
        if (creditsPanel) creditsPanel.classList.remove('hidden');
        if (pricingGrid) pricingGrid.style.display = 'none';
        if (pricingToggle) pricingToggle.style.display = 'none';
        if (topupRow) topupRow.classList.add('hidden');
        track('credits_needed_viewed', { reason: opts.reason || 'credits', need, have });
        return;
    }

    if (creditsPanel) creditsPanel.classList.add('hidden');
    if (pricingGrid) pricingGrid.style.display = 'grid';
    if (pricingToggle) pricingToggle.style.display = 'inline-flex';

    if (topupRow) {
        if (paidSubscriber) { topupRow.classList.remove('hidden'); }
        else { topupRow.classList.add('hidden'); }
    }

    const ctaText = usedTrial ? 'Subscribe now' : 'Start free trial';
    if (starterBtn) starterBtn.textContent = ctaText;
    if (dailyBtn) dailyBtn.textContent = ctaText;

    if (opts.reason === 'hq' && !paidSubscriber) {
        if (heading) heading.textContent = 'Unlock Pro visuals';
        if (subtitle) subtitle.textContent = 'High quality stills are on paid plans. Subscribe to use them.';
    } else if (opts.reason === 'cook' && !usedTrial) {
        if (heading) heading.textContent = 'Your video is ready to cook';
        if (subtitle) subtitle.textContent = `Start your free trial to cook this video — ${trialCredits()} videos included.`;
    } else if (usedTrial) {
        if (heading) heading.textContent = 'Choose your plan';
        if (subtitle) subtitle.textContent = 'Your free trial was already used. Subscribe to keep creating.';
    } else {
        if (heading) heading.textContent = 'Choose your plan';
        if (subtitle) subtitle.textContent = '7-day free trial on any plan. Cancel anytime.';
    }

    track('upgrade_viewed', { reason: opts.reason || 'general', need, have });
}

function showCreditsNeededModal({ need = 1, have = null, reason = 'credits' } = {}) {
    showPricingModal({
        reason: reason || 'credits',
        need,
        have: have == null ? (currentUser?.credits ?? 0) : have,
    });
}

function hidePricingModal() {
    const modal = document.getElementById('pricing-modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.style.display = 'none';
}

// ---------------------------------------------------------------------------
// Celebration moments (trial start / upgrade)
// ---------------------------------------------------------------------------
function showCelebration(kind = 'trial') {
    const overlay = document.getElementById('celebration-overlay');
    if (!overlay) return;
    const title = document.getElementById('celebration-title');
    const sub = document.getElementById('celebration-sub');
    const cta = document.getElementById('celebration-cta');
    const burst = document.getElementById('cele-burst');

    if (kind === 'upgrade') {
        if (title) title.textContent = "You're upgraded";
        if (sub) sub.textContent = 'Full credits unlocked. Keep cooking.';
        if (cta) cta.textContent = 'Back to cooking →';
    } else if (kind === 'subscribe') {
        if (title) title.textContent = "You're subscribed";
        if (sub) sub.textContent = 'Welcome aboard. Your plan is live.';
        if (cta) cta.textContent = "Let's go →";
    } else {
        if (title) title.textContent = "You're in";
        if (sub) sub.textContent = `Your free trial is live. ${trialCredits()} videos ready to cook.`;
        if (cta) cta.textContent = "Let's cook →";
    }

    if (burst) {
        burst.innerHTML = '';
        const colors = ['var(--accent)', '#22c55e', '#f59e0b', '#38bdf8', '#f472b6'];
        for (let i = 0; i < 18; i++) {
            const p = document.createElement('div');
            p.className = 'cele-particle';
            const angle = (i / 18) * Math.PI * 2;
            const dist = 60 + Math.random() * 80;
            p.style.setProperty('--dx', Math.cos(angle) * dist + 'px');
            p.style.setProperty('--dy', Math.sin(angle) * dist + 'px');
            p.style.left = '50%';
            p.style.top = '40%';
            p.style.background = colors[i % colors.length];
            p.style.animationDelay = (Math.random() * 0.15) + 's';
            burst.appendChild(p);
        }
    }

    overlay.classList.add('show');
    overlay.style.display = 'flex';
    // Force reflow so opacity transition plays
    void overlay.offsetWidth;
    overlay.style.opacity = '1';
    track('celebration_shown', { kind });
}

function hideCelebration() {
    const overlay = document.getElementById('celebration-overlay');
    if (!overlay) return;
    overlay.style.opacity = '0';
    setTimeout(() => {
        overlay.classList.remove('show');
        overlay.style.display = 'none';
        // After celebrating trial/upgrade, resume where they left off in the pipeline
        if (state.step >= 2) {
            navigateTo('pipeline');
            goToStep(state.step);
            if (state.step === 6) populateBuildSummary();
        }
    }, 350);
}

function maybeShowWelcomeCelebration() {
    const params = new URLSearchParams(window.location.search);
    const welcome = params.get('welcome');
    const topup = params.get('topup');
    if (!welcome && !topup) return;
    // Clean URL without losing hash
    const hash = window.location.hash || '#pipeline';
    window.history.replaceState({}, '', window.location.pathname + hash);
    // Wait a beat for auth/UI to settle, then celebrate
    setTimeout(async () => {
        if (topup) {
            // Stripe webhook may land a second after redirect — poll briefly.
            for (let i = 0; i < 6; i++) {
                await refreshUserData();
                await new Promise((r) => setTimeout(r, 800));
            }
            const overlay = document.getElementById('credit-notice-overlay');
            const title = document.getElementById('credit-notice-title');
            const body = document.getElementById('credit-notice-body');
            if (title) title.textContent = 'Credits added';
            if (body) {
                body.textContent = currentUser
                    ? `Your top-up landed. You now have ${currentUser.credits} credits.`
                    : 'Your top-up landed. Your balance will update in a moment.';
            }
            if (overlay) {
                overlay.classList.remove('hidden');
                overlay.style.display = 'flex';
            }
            return;
        }
        if (welcome === 'trial') {
            // Transparent notice for people who saw the YouTube promise of 3 free videos
            showTrialCreditChangeNotice(() => showCelebration('trial'));
        } else if (welcome === 'upgrade') {
            showCelebration('upgrade');
        } else {
            showCelebration('subscribe');
        }
    }, 400);
}

/**
 * One-time apology dialog for new trial users (video promised 3; we now grant 2).
 * Only shown on fresh trial welcome — existing trials are untouched.
 */
function showTrialCreditChangeNotice(onDone) {
    const KEY = 'cr_trial_credit_notice_v2';
    try {
        if (localStorage.getItem(KEY) === '1') {
            if (typeof onDone === 'function') onDone();
            return;
        }
    } catch (_) { /* private mode */ }

    const existing = document.getElementById('trial-credit-notice');
    if (existing) existing.remove();

    const n = trialCredits();
    const modal = document.createElement('div');
    modal.id = 'trial-credit-notice';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.style.cssText = 'position:fixed;inset:0;z-index:220;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.72);padding:16px;';
    modal.innerHTML = `
        <div style="background:var(--app-surface,#1a1a2e);border:1px solid var(--app-border,#333);border-radius:16px;padding:28px 28px 22px;max-width:440px;width:100%;text-align:left;position:relative;box-shadow:0 24px 64px rgba(0,0,0,.45);">
            <p style="font-family:var(--font-mono,monospace);font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:var(--app-ink-3,#888);margin:0 0 10px;">A quick note</p>
            <h3 style="margin:0 0 12px;color:var(--app-ink,#fff);font-family:var(--font-display,sans-serif);font-size:22px;line-height:1.25;">We had to adjust the free trial</h3>
            <p style="color:var(--app-ink-2,#bbb);margin:0 0 12px;font-size:14px;line-height:1.55;font-family:var(--font-body,sans-serif);">
                If you came from our YouTube video, we promised <strong>3 free videos</strong>.
                Due to rising generation costs, we unfortunately had to reduce new trials to
                <strong>${n} free videos</strong>.
            </p>
            <p style="color:var(--app-ink-2,#bbb);margin:0 0 22px;font-size:14px;line-height:1.55;font-family:var(--font-body,sans-serif);">
                We're sorry for the change — we still want you to fully test ChannelRecipe,
                and every recipe remains available on your trial.
            </p>
            <button type="button" id="trial-credit-notice-ok" style="width:100%;padding:13px 16px;border:none;border-radius:10px;background:var(--accent,#6c5ce7);color:#fff;font-size:15px;font-weight:600;cursor:pointer;font-family:var(--font-body,sans-serif);">
                Got it — continue with ${n} videos
            </button>
        </div>
    `;
    document.body.appendChild(modal);
    track('trial_credit_notice_shown', { trial_credits: n, previous_promised: 3 });

    const finish = () => {
        try { localStorage.setItem(KEY, '1'); } catch (_) {}
        modal.remove();
        track('trial_credit_notice_dismissed', { trial_credits: n });
        if (typeof onDone === 'function') onDone();
    };
    modal.querySelector('#trial-credit-notice-ok')?.addEventListener('click', finish);
}

function showTrialExhaustedModal() {
    const existing = document.getElementById('trial-exhausted-modal');
    if (existing) { existing.style.display = 'flex'; return; }

    const tierLabel = currentUser.plan === 'daily_trial' ? 'Daily' : 'Starter';
    const credits = currentUser.plan === 'daily_trial' ? 35 : 15;
    const price = currentUser.plan === 'daily_trial' ? '$49' : '$27';
    const trialN = trialCredits();

    const modal = document.createElement('div');
    modal.id = 'trial-exhausted-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:200;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.7);';
    modal.innerHTML = `
        <div style="background:var(--bg-card,#1a1a2e);border-radius:16px;padding:32px;max-width:420px;width:90%;text-align:center;position:relative;">
            <button onclick="hideTrialExhaustedModal()" style="position:absolute;top:12px;right:16px;background:none;border:none;color:var(--text-secondary,#aaa);font-size:20px;cursor:pointer;">&times;</button>
            <div style="font-size:40px;margin-bottom:12px;">🎬</div>
            <h3 style="margin:0 0 8px;color:var(--text-primary,#fff);font-size:20px;">You've used your ${trialN} trial videos</h3>
            <p style="color:var(--text-secondary,#aaa);margin:0 0 24px;font-size:14px;line-height:1.5;">
                Start your <strong>${tierLabel}</strong> plan now to unlock <strong>${credits} videos/month</strong> at ${price}/mo, or wait until your trial ends.
            </p>
            <button onclick="endTrialNow()" style="width:100%;padding:14px;border:none;border-radius:10px;background:var(--accent,#6c5ce7);color:#fff;font-size:16px;font-weight:600;cursor:pointer;margin-bottom:10px;">
                Start plan now — ${credits} videos
            </button>
            <button onclick="hideTrialExhaustedModal()" style="width:100%;padding:12px;border:1px solid var(--border,#333);border-radius:10px;background:transparent;color:var(--text-secondary,#aaa);font-size:14px;cursor:pointer;">
                I'll wait
            </button>
        </div>
    `;
    document.body.appendChild(modal);
    track('trial_exhausted_viewed');
}

function hideTrialExhaustedModal() {
    const modal = document.getElementById('trial-exhausted-modal');
    if (modal) modal.style.display = 'none';
}

let _endTrialInFlight = false;
async function endTrialNow() {
    if (_endTrialInFlight) return;
    _endTrialInFlight = true;

    const modalBtn = document.querySelector('#trial-exhausted-modal button');
    const billingBtn = document.querySelector('#billing-trial-section .btn-primary');
    const buttons = [modalBtn, billingBtn].filter(Boolean);
    buttons.forEach(b => { b.disabled = true; b.dataset.origText = b.textContent; b.textContent = 'Activating…'; });

    try {
        const resp = await fetch('/api/billing/end-trial', { method: 'POST' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            const msg = data.detail || 'Could not end trial. Please try again.';
            if (resp.status === 503) {
                alert(msg);
            } else if (resp.status === 402) {
                alert(msg);
            } else {
                alert(msg);
            }
            return;
        }
        if (data.plan && data.credits != null) {
            currentUser.plan = data.plan;
            currentUser.credits = data.credits;
            currentUser.trial_used = true;
            updateAuthUI();
        }
        hideTrialExhaustedModal();
        loadBillingPage();
        showCelebration('upgrade');
        track('trial_ended_early');
    } catch (e) {
        alert('Network error. Please try again.');
    } finally {
        _endTrialInFlight = false;
        buttons.forEach(b => { b.disabled = false; b.textContent = b.dataset.origText || 'Start plan now'; });
    }
}

function setPricingPlan(cycle) {
    _pricingBillingCycle = cycle;
    const mBtn = document.getElementById('pricing-monthly-btn');
    const aBtn = document.getElementById('pricing-annual-btn');

    if (cycle === 'annual') {
        mBtn.style.background = 'transparent'; mBtn.style.color = 'var(--app-ink-3)';
        aBtn.style.background = 'var(--accent)'; aBtn.style.color = 'white';
        document.getElementById('starter-price').textContent = '$22.50';
        document.getElementById('starter-period').textContent = '/mo';
        document.getElementById('starter-note').textContent = 'Billed $270/year · 2 months free';
        document.getElementById('starter-videos').innerHTML = '<strong>180 videos</strong>/year';
        document.getElementById('daily-price').textContent = '$40.83';
        document.getElementById('daily-period').textContent = '/mo';
        document.getElementById('daily-note').textContent = 'Billed $490/year · 2 months free';
        document.getElementById('daily-videos').innerHTML = '<strong>420 videos</strong>/year';
    } else {
        aBtn.style.background = 'transparent'; aBtn.style.color = 'var(--app-ink-3)';
        mBtn.style.background = 'var(--accent)'; mBtn.style.color = 'white';
        document.getElementById('starter-price').textContent = '$27';
        document.getElementById('starter-period').textContent = '/mo';
        document.getElementById('starter-note').textContent = '~$1.80 per video';
        document.getElementById('starter-videos').innerHTML = '<strong>15 videos</strong>/month';
        document.getElementById('daily-price').textContent = '$49';
        document.getElementById('daily-period').textContent = '/mo';
        document.getElementById('daily-note').textContent = '~$1.40/video · save 22%';
        document.getElementById('daily-videos').innerHTML = '<strong>35 videos</strong>/month';
    }
}

async function proceedToCheckout(tier = 'starter') {
    hidePricingModal();
    const plan = `${tier}_${_pricingBillingCycle}`;
    await _doCheckout(plan);
}

async function proceedToTopup(amount) {
    hidePricingModal();
    if (!currentUser) { showAuthModal(); return; }
    track('topup_started', { amount });
    try {
        const res = await fetch('/api/billing/topup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credits: amount }),
        });
        const data = await res.json();
        if (res.ok && data.url) window.location.href = data.url;
        else alert(data.detail || 'Could not start top-up.');
    } catch (e) {
        alert('Top-up failed: ' + e.message);
    }
}

function upgradeToPro(plan = 'monthly') {
    if (!currentUser) { showAuthModal(); return; }
    showPricingModal();
}

async function _doCheckout(plan = 'monthly') {
    if (!currentUser) { showAuthModal(); return; }
    // Save pipeline progress so Stripe redirect doesn't wipe their work
    persistPipelineState();
    track('checkout_started', { plan });
    try {
        const res = await fetch('/api/billing/checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plan }),
        });
        let data;
        try { data = await res.json(); } catch (_) { data = {}; }
        if (res.ok && data.url) {
            window.location.href = data.url;
        } else {
            alert(data.detail || `Checkout failed (${res.status}). Please try again.`);
        }
    } catch (e) {
        alert('Could not connect to payment server. Please check your connection and try again.');
    }
}

// ---------------------------------------------------------------------------
// Step 2: Titles
// ---------------------------------------------------------------------------
async function generateTitles() {
    if (!ensureSignedIn(generateTitles)) return;
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
        track('titles_generated', { recipe: state.niche, count: (data.titles || []).length });
    } catch (e) {
        alert('Title generation failed: ' + e.message);
    } finally {
        setLoading(btn, false);
    }
}

function _esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function renderTitles(titles) {
    const list = document.getElementById('titles-list');
    list.innerHTML = '';
    titles.forEach((t, i) => {
        const card = document.createElement('div');
        card.className = 'title-card';
        card.innerHTML = `<div class="flex items-center gap-3"><span class="text-accent font-bold text-lg">${i + 1}</span><span class="text-gray-100">${_esc(t)}</span></div>`;
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
    if (!ensureSignedIn(generateScript)) return;
    const cap = effectiveMinuteCap();
    if (!hasFullLengthAccess() && state.targetMinutes > cap) {
        state.targetMinutes = cap;
        applyLengthSliderLimits();
        showLengthUpgradePrompt();
        return;
    }
    const genBtn = document.getElementById('btn-gen-script');
    const regenBtn = document.getElementById('btn-regen-script');
    if (genBtn) setLoading(genBtn, true);
    if (regenBtn) setLoading(regenBtn, true);
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
        const data = await readJson(res, null);
        if (!res.ok) {
            const detail = (data && (data.detail || data.error)) || (
                res.status === 502 || res.status === 504
                    ? 'Server timed out writing the script — try a shorter length or retry.'
                    : `Script request failed (HTTP ${res.status})`
            );
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        if (!data || !data.script) {
            throw new Error('Script service returned an empty response — please retry.');
        }
        editor.value = data.script;
        state.script = data.script;
        updateWordCount();
        track('script_generated', { recipe: state.niche, target_minutes: state.targetMinutes, word_count: data.word_count });
    } catch (e) {
        alert('Script generation failed: ' + e.message);
    } finally {
        loading.classList.add('hidden');
        if (genBtn) setLoading(genBtn, false);
        if (regenBtn) setLoading(regenBtn, false);
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
// Instant fallback so the grid never looks empty while /api/voices loads
const FALLBACK_VOICES = [
    { id: 'leo', name: 'Leo', tag: 'Narrator', gender: 'male', desc: 'Authoritative, instructional — best for documentaries', preview_url: 'https://data.x.ai/audio-samples/voice_leo.mp3', default: true },
    { id: 'rex', name: 'Rex', tag: 'Professional', gender: 'male', desc: 'Polished business tone — great for explainers', preview_url: 'https://data.x.ai/audio-samples/voice_rex.mp3' },
    { id: 'sal', name: 'Sal', tag: 'Neutral', gender: 'male', desc: 'Versatile, clear delivery that fits most niches', preview_url: 'https://data.x.ai/audio-samples/voice_sal.mp3' },
    { id: 'ara', name: 'Ara', tag: 'Warm', gender: 'female', desc: 'Warm and conversational — great for storytelling', preview_url: 'https://data.x.ai/audio-samples/voice_ara.mp3' },
    { id: 'eve', name: 'Eve', tag: 'Upbeat', gender: 'female', desc: 'Energetic and upbeat — strong for viral formats', preview_url: 'https://data.x.ai/audio-samples/voice_eve.mp3' },
];

let _voicesLoaded = false;
let _voicesLoading = null;

function _paintVoiceSelection(selectedId) {
    const grid = document.getElementById('voices-grid');
    if (!grid) return;
    grid.querySelectorAll('.voice-card').forEach((card) => {
        const id = card.dataset.voiceId || '';
        const on = id === selectedId;
        card.classList.toggle('selected', on);
        const radio = card.querySelector('.voice-radio');
        if (!radio) return;
        radio.style.borderColor = on ? 'var(--accent)' : 'var(--app-border)';
        radio.innerHTML = on
            ? '<span style="width:10px;height:10px;border-radius:50%;background:var(--accent);"></span>'
            : '';
    });
}

function _renderVoiceGrid(voices) {
    const grid = document.getElementById('voices-grid');
    if (!grid || !voices?.length) return;
    grid.innerHTML = '';
    const legacy = ['Charon', 'Kore', 'Gacrux', 'Schedar', 'Puck', 'Sulafat'];
    if (!state.voice || legacy.includes(state.voice)) {
        const def = voices.find(v => v.default) || voices[0];
        if (def) state.voice = def.id;
    }
    voices.forEach(v => {
        const card = document.createElement('div');
        card.className = `voice-card${v.id === state.voice ? ' selected' : ''}`;
        card.dataset.voiceId = v.id;
        const recommended = v.default
            ? `<span style="font-family:var(--font-mono);font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:var(--accent);background:var(--accent-soft-dark);border-radius:var(--radius-pill);padding:2px 7px;">Best pick</span>`
            : '';
        const gender = v.gender ? `<span style="font-family:var(--font-mono);font-size:10px;color:var(--app-ink-3);text-transform:uppercase;">${v.gender}</span>` : '';
        card.innerHTML = `
            <button type="button" class="play-btn" data-voice="${escapeHtml(v.id)}" title="Preview voice">
                <svg width="13" height="13" viewBox="0 0 14 14"><path d="M4 2.5 L4 11.5 L11 7 Z" fill="currentColor"/></svg>
            </button>
            <div class="flex-1 min-w-0">
                <div style="display:flex;align-items:center;gap:8px;font-family:var(--font-body);font-weight:600;font-size:15px;color:var(--app-ink);">
                    ${escapeHtml(v.name)} ${recommended} ${gender}
                </div>
                <div style="font-family:var(--font-body);font-size:13px;color:var(--app-ink-3);margin-top:2px;">${escapeHtml(v.desc || v.tag || '')}</div>
            </div>
            <span class="voice-radio" style="width:20px;height:20px;flex:none;border-radius:50%;border:2px solid ${v.id === state.voice ? 'var(--accent)' : 'var(--app-border)'};display:flex;align-items:center;justify-content:center;">
                ${v.id === state.voice ? '<span style="width:10px;height:10px;border-radius:50%;background:var(--accent);"></span>' : ''}
            </span>
        `;
        card.addEventListener('click', (e) => {
            if (e.target.closest('.play-btn')) return;
            state.voice = v.id;
            _paintVoiceSelection(v.id);
            persistPipelineState();
        });
        card.querySelector('.play-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            previewVoice(v.id, e.currentTarget, v.preview_url);
        });
        grid.appendChild(card);
    });
}

async function loadVoices() {
    const grid = document.getElementById('voices-grid');
    if (!grid) return;
    // Paint instantly from cache/fallback so the step never looks empty
    if (!_voicesLoaded && !grid.children.length) {
        _renderVoiceGrid(FALLBACK_VOICES);
    }
    if (_voicesLoaded) return;
    if (_voicesLoading) return _voicesLoading;
    _voicesLoading = (async () => {
        try {
            const res = await fetch('/api/voices');
            const voices = await res.json();
            let merged = Array.isArray(voices) && voices.length ? [...voices] : [...FALLBACK_VOICES];
            try {
                const cRes = await fetch('/api/voice/clones');
                if (cRes.ok) {
                    const cData = await cRes.json();
                    if (cData.enabled) {
                        _featureFlags.voice_clone_enabled = true;
                        document.getElementById('vo-mode-clone')?.classList.remove('hidden');
                    }
                    (cData.clones || []).forEach((c) => {
                        merged.unshift({
                            id: c.voice_id,
                            name: c.name || 'Cloned voice',
                            tag: 'Cloned',
                            desc: 'Your rights-gated voice clone',
                            gender: '',
                            preview_url: '',
                        });
                    });
                }
            } catch (_) {}
            if (merged.length) {
                _renderVoiceGrid(merged);
                _voicesLoaded = true;
            }
        } catch (e) {
            console.error('Failed to load voices:', e);
        } finally {
            _voicesLoading = null;
        }
    })();
    return _voicesLoading;
}

async function previewVoice(voiceId, btn, previewUrl) {
    if (previewAudio) {
        try { previewAudio.pause(); } catch (_) {}
        previewAudio = null;
    }
    btn.classList.add('loading');
    btn.innerHTML = '<svg class="animate-spin w-4 h-4" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>';
    try {
        let url = previewUrl;
        if (!url) {
            // Fallback: generate a short sample via our API
            const res = await fetch('/api/voiceover/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ voice: voiceId }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Preview failed');
            url = data.url;
        }
        const audio = new Audio(url);
        previewAudio = audio;
        audio.addEventListener('ended', () => resetPlayBtn(btn));
        // play() rejects with AbortError if the user clicks another voice quickly — ignore that
        await audio.play().catch((err) => {
            if (err && (err.name === 'AbortError' || /interrupted/i.test(err.message || ''))) return;
            throw err;
        });
        if (previewAudio !== audio) return; // superseded by a newer preview
        btn.innerHTML = '<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>';
        btn.classList.remove('loading');
    } catch (e) {
        if (e && (e.name === 'AbortError' || /interrupted/i.test(e.message || ''))) {
            resetPlayBtn(btn);
            return;
        }
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
    document.getElementById('vo-generate-panel')?.classList.toggle('hidden', mode !== 'generate');
    document.getElementById('vo-upload-panel')?.classList.toggle('hidden', mode !== 'upload');
    document.getElementById('vo-clone-panel')?.classList.toggle('hidden', mode !== 'clone');
    document.querySelectorAll('.vo-mode-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(`vo-mode-${mode}`)?.classList.add('active');
    if (mode === 'clone') {
        const cost = _featureFlags.voice_clone_credit_cost;
        const status = document.getElementById('vo-clone-status');
        if (status && !status.dataset.touched) {
            status.textContent = cost > 0
                ? `Creating a clone costs ${cost} credit${cost === 1 ? '' : 's'}.`
                : 'Clone creation is free while this promo is on.';
        }
    }
}

async function createVoiceClone() {
    if (!ensureAuth(createVoiceClone)) return;
    if (!_featureFlags.voice_clone_enabled) {
        showSoftPrompt('Voice clone is not enabled yet.');
        return;
    }
    const consent = document.getElementById('vo-clone-consent')?.checked;
    if (!consent) {
        showSoftPrompt('Confirm you own this voice or have written permission before cloning.');
        return;
    }
    const file = document.getElementById('vo-clone-file')?.files?.[0];
    const ytInput = document.getElementById('vo-clone-yt');
    const yt = ytInput?.value?.trim() || '';
    if (!file && !yt) {
        showSoftPrompt('Paste a YouTube URL, or drop a screen recording / audio clip of the voice.');
        return;
    }
    const btn = document.getElementById('btn-vo-clone');
    const status = document.getElementById('vo-clone-status');
    if (status) status.dataset.touched = '1';
    setLoading(btn, true);
    try {
        const form = new FormData();
        form.append('consent', 'true');
        form.append('title', document.getElementById('vo-clone-title')?.value?.trim() || 'My voice');
        // Prefer upload when both are set (screen recording after a failed YouTube try).
        if (file) {
            form.append('file', file);
            if (yt && ytInput) ytInput.value = '';
        } else if (yt) {
            form.append('youtube_url', yt);
        }
        const res = await fetch('/api/voice/clone', { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Clone failed'));
        const via = data.source === 'screen_recording' ? ' from your recording'
            : data.source === 'youtube' ? ' from YouTube' : '';
        if (status) status.textContent = `Saved “${data.title}”${via}. Pick it under Generate voice.`;
        if (typeof data.credits_remaining === 'number' && currentUser) {
            currentUser.credits = data.credits_remaining;
            try { updateCreditsUI?.(); } catch (_) {}
            try { refreshUserChip?.(); } catch (_) {}
        }
        state.voice = data.voice_id;
        state.voiceMode = 'generate';
        _voicesLoaded = false;
        await loadVoices();
        setVoiceMode('generate');
    } catch (e) {
        showSoftPrompt(e.message || 'Clone failed.');
        if (status) status.textContent = e.message || 'Clone failed.';
    } finally {
        setLoading(btn, false);
    }
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
    if (!ensureSignedIn(handleVoiceNext)) return;
    const btn = document.getElementById('btn-next-4');
    setLoading(btn, true);

    // Avatar recipe: skip Atlas VO — require HeyGen IDs only
    if (isAvatarRecipe()) {
        if (!state.avatarId || !state.voiceId) {
            alert('Pick a HeyGen avatar and voice (or paste both IDs) before continuing.');
            setLoading(btn, false);
            return;
        }
        if (!heygenConfigured) {
            alert('Connect your HeyGen API key in Settings first.');
            setLoading(btn, false);
            return;
        }
        state.voiceoverPath = '';
        state.voiceoverUrl = '';
        resetThumbnailStep();
        goToStep(5);
        setLoading(btn, false);
        return;
    }

    const isUpload = state.voiceMode === 'upload' && state.uploadedVoPath;

    if (state.voiceMode === 'clone') {
        alert('Create a clone first, then pick it under Generate voice — or switch to Upload.');
        setLoading(btn, false);
        return;
    }

    if (state.voiceMode === 'upload' && !state.uploadedVoPath) {
        alert('Please upload a voiceover file first.');
        setLoading(btn, false);
        return;
    }

    if (isUpload) {
        state.voiceoverPath = state.uploadedVoPath;
        resetThumbnailStep();
        goToStep(5);
        setLoading(btn, false);
        return;
    }

    const voLimit = assertVoiceoverLengthOk(state.script);
    if (voLimit) {
        alert(voLimit);
        setLoading(btn, false);
        return;
    }

    document.getElementById('vo-generating')?.classList.remove('hidden');
    try {
        const voRes = await fetch('/api/voiceover', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script: state.script, voice: state.voice }),
        });
        const voData = await voRes.json();
        if (!voRes.ok) throw new Error(friendlyApiError(voData, 'Voiceover failed'));
        state.voiceoverPath = voData.path;
        state.voiceoverUrl = voData.url;
        track('voiceover_generated', { voice: state.voice, recipe: state.niche });
        resetThumbnailStep();
        goToStep(5);
    } catch (e) {
        alert('Generation failed: ' + (e.message || e));
    } finally {
        setLoading(btn, false);
        document.getElementById('vo-generating')?.classList.add('hidden');
    }
}

function assertVoiceoverLengthOk(script) {
    const words = String(script || '').trim().split(/\s+/).filter(Boolean).length;
    const maxW = _featureFlags.max_voiceover_words || 3750;
    const maxM = _featureFlags.max_voiceover_minutes || 25;
    if (words > maxW) {
        return `Script is too long for voiceover (${words} words). Max is ~${maxM} minutes (${maxW} words).`;
    }
    return null;
}

function resetThumbnailStep() {
    state.thumbnailPath = '';
    state.thumbnailUrl = '';
    state.thumbnailRefs = [];
    const grid = document.getElementById('thumb-grid');
    if (grid) grid.innerHTML = '';
    const preview = document.getElementById('thumb-refs-preview');
    if (preview) preview.innerHTML = '';
    document.getElementById('thumb-empty')?.classList.remove('hidden');
    document.getElementById('thumb-loading')?.classList.add('hidden');
    const next = document.getElementById('btn-next-5');
    if (next) next.disabled = true;
    updateThumbGenerateButton();
}

function updateThumbGenerateButton() {
    const btn = document.getElementById('btn-regen-thumb');
    const hasRefs = (state.thumbnailRefs || []).length > 0;
    if (btn) {
        btn.disabled = !hasRefs;
        btn.title = hasRefs ? 'Generate thumbnails from your references' : 'Upload a reference image first';
    }
    const empty = document.getElementById('thumb-empty');
    if (empty) {
        const hasCards = (document.getElementById('thumb-grid')?.children?.length || 0) > 0;
        const hasPick = !!(state.thumbnailUrl || state.thumbnailPath);
        empty.classList.toggle('hidden', hasRefs || hasPick || hasCards);
    }
}

// ---------------------------------------------------------------------------
// Step 5: Thumbnails
// ---------------------------------------------------------------------------
function renderThumbnails(urls, paths) {
    const grid = document.getElementById('thumb-grid');
    grid.innerHTML = '';
    document.getElementById('thumb-loading')?.classList.add('hidden');
    document.getElementById('thumb-empty')?.classList.add('hidden');
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
    updateThumbGenerateButton();
}

function handleThumbRefUpload(input) {
    const preview = document.getElementById('thumb-refs-preview');
    if (preview) preview.innerHTML = '';
    state.thumbnailRefs = [];
    for (const file of (input.files || [])) {
        state.thumbnailRefs.push(file);
        if (preview) {
            const img = document.createElement('img');
            img.className = 'ref-thumb';
            img.src = URL.createObjectURL(file);
            preview.appendChild(img);
        }
    }
    updateThumbGenerateButton();
    document.getElementById('thumb-empty')?.classList.toggle('hidden', state.thumbnailRefs.length > 0);
}

async function handleThumbFinalUpload(input) {
    const file = input.files?.[0];
    if (!file) return;
    if (!ensureAuth(handleThumbFinalUpload)) return;
    try {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch('/api/thumbnail/upload', { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Upload failed'));
        renderThumbnails([data.url], [data.path]);
    } catch (e) {
        alert(e.message || 'Thumbnail upload failed');
    }
}

async function regenerateThumbnails() {
    if (!ensureAuth(regenerateThumbnails)) return;
    if (!(state.thumbnailRefs || []).length) {
        showSoftPrompt('Upload a reference image first — we don’t generate thumbnails cold.');
        return;
    }
    const grid = document.getElementById('thumb-grid');
    if (grid) grid.innerHTML = '';
    document.getElementById('thumb-loading')?.classList.remove('hidden');
    document.getElementById('thumb-empty')?.classList.add('hidden');
    const customStyle = document.getElementById('thumb-style-input')?.value || '';
    const style = customStyle || state.nicheData?.thumbnail_style || '';
    try {
        const formData = new FormData();
        formData.append('title', state.title);
        formData.append('style', style);
        formData.append('count', '2');
        state.thumbnailRefs.forEach(f => formData.append('refs', f));
        const res = await fetch('/api/thumbnail/with-refs', { method: 'POST', body: formData });
        const data = await res.json();
        if (res.ok && data.thumbnails?.length) renderThumbnails(data.thumbnails, data.paths);
        else throw new Error(friendlyApiError(data, 'No thumbnails generated'));
    } catch (e) {
        alert('Thumbnail generation failed: ' + e.message);
        document.getElementById('thumb-loading')?.classList.add('hidden');
        updateThumbGenerateButton();
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
    const voiceEl = document.getElementById('summary-voice');
    if (isAvatarRecipe()) {
        const a = state.avatarName || state.avatarId || '—';
        const v = state.heygenVoiceName || state.voiceId || '—';
        voiceEl.textContent = `${a} · ${v}`;
    } else {
        voiceEl.textContent = state.voice;
    }
    syncImageQualityUI();
}

function supportsImageQualityPicker() {
    const recipe = state.nicheData?.recipe || state.niche || '';
    return recipe === 'animated_explainer' || recipe === 'broll_cinematic';
}

function canUseHighQuality() {
    return !!(currentUser && !isTrialUser() && currentUser.plan !== 'free');
}

function hqCreditCost() {
    return Math.max(1, Number(_featureFlags.hq_credit_cost || 3));
}

function hqMaxMinutes() {
    return Math.max(3, Number(_featureFlags.hq_max_minutes || 12));
}

function cookCreditCost() {
    if (supportsImageQualityPicker() && state.imageQuality === 'high') return hqCreditCost();
    return 1;
}

function setImageQuality(q) {
    const next = q === 'high' ? 'high' : 'standard';
    if (next === 'high' && !canUseHighQuality()) {
        showPricingModal({ reason: 'hq' });
        return;
    }
    if (next === 'high') {
        const words = (state.script || '').trim().split(/\s+/).filter(Boolean).length;
        const estMin = words / 150;
        if (estMin > hqMaxMinutes() + 0.5) {
            alert(`High quality caps at ${hqMaxMinutes()} minutes. Shorten the script, or cook part 2 separately.`);
            return;
        }
    }
    state.imageQuality = next;
    syncImageQualityUI();
}

function syncImageQualityUI() {
    const picker = document.getElementById('image-quality-picker');
    const blurb = document.getElementById('build-credit-blurb');
    const hint = document.getElementById('quality-hq-hint');
    const meta = document.getElementById('quality-high-meta');
    const std = document.getElementById('quality-standard');
    const hq = document.getElementById('quality-high');
    const show = supportsImageQualityPicker();
    if (picker) picker.classList.toggle('hidden', !show);
    if (!show) {
        state.imageQuality = 'standard';
    } else if (state.imageQuality === 'high' && !canUseHighQuality()) {
        state.imageQuality = 'standard';
    }
    const cost = cookCreditCost();
    if (blurb) {
        blurb.textContent = cost === 1
            ? "Here's your video. This uses 1 credit."
            : `Here's your video. High quality uses ${cost} credits.`;
    }
    if (meta) meta.textContent = `${hqCreditCost()} credits · up to ${hqMaxMinutes()} min`;
    if (std) std.setAttribute('aria-checked', state.imageQuality === 'standard' ? 'true' : 'false');
    if (hq) hq.setAttribute('aria-checked', state.imageQuality === 'high' ? 'true' : 'false');
    if (hint) {
        if (!canUseHighQuality()) {
            hint.classList.remove('hidden');
            hint.textContent = 'High quality is on paid plans — upgrade to unlock Pro visuals.';
        } else if (state.imageQuality === 'high') {
            hint.classList.remove('hidden');
            hint.textContent = `Best for channels where the stills carry the video. Max ${hqMaxMinutes()} min per cook.`;
        } else {
            hint.classList.add('hidden');
            hint.textContent = '';
        }
    }
    const btn = document.getElementById('btn-build');
    if (btn) {
        const label = cost === 1 ? 'Cook video' : `Cook · ${cost} credits`;
        const svg = btn.querySelector('svg');
        btn.textContent = '';
        if (svg) btn.appendChild(svg);
        btn.appendChild(document.createTextNode(' ' + label));
    }
}

function _friendlyProgress(raw) {
    if (/Queued/i.test(raw) || /You're next/i.test(raw)) return raw;
    if (/Starting your cook/i.test(raw)) return 'Starting your cook...';
    if (/Uploading your video/i.test(raw)) return 'Uploading your video...';
    if (/Saving thumbnail/i.test(raw)) return 'Saving thumbnail...';
    if (/Saving to your library/i.test(raw)) return 'Saving to your library...';
    if (/Upload slow/i.test(raw)) return 'Finishing upload...';
    if (/^Done!$/i.test(raw)) return 'Done!';
    if (/Step 1.*Aligning/i.test(raw)) return 'Analyzing your script...';
    if (/Step 2.*Segment/i.test(raw)) return 'Planning visual scenes...';
    if (/Step 3.*Style/i.test(raw)) return 'Creating art style...';
    if (/Step 4.*Generat.*illustr/i.test(raw)) return 'Generating artwork...';
    if (/Illustrations?:\s*(\d+)\/(\d+)/i.test(raw)) {
        const m = raw.match(/(\d+)\/(\d+)/);
        return m ? `Drawing scene ${m[1]} of ${m[2]}...` : 'Drawing scenes...';
    }
    if (/Step 5.*Prepar/i.test(raw)) return 'Preparing images...';
    if (/Step 6.*Assembl/i.test(raw)) return 'Building your video...';
    if (/Assembling/i.test(raw)) return 'Building your video...';
    if (/Concatenat/i.test(raw)) return 'Building your video...';
    if (/Planning scenes|DirectorScore/i.test(raw)) return 'Planning visual scenes...';
    if (/Resolving assets|Finding footage|Resolving scenes/i.test(raw)) {
        return raw.replace(/^\[cinematic\]\s*/i, '').slice(0, 80) || 'Finding footage & images...';
    }
    if (/assets resolved/i.test(raw)) return 'Assets ready';
    if (/concepts? planned/i.test(raw)) return 'Scenes planned';
    if (/Style ref/i.test(raw)) return 'Art style ready';
    if (/Got \d+ words|sentences aligned/i.test(raw)) return 'Script analyzed';
    if (/illustrations? generated/i.test(raw)) return 'All artwork ready';
    if (/images? prepared/i.test(raw)) return 'Images ready';
    if (/clips? rendered/i.test(raw)) return 'Almost there...';
    if (/Assembly complete|Assembled/i.test(raw)) return 'Video assembled — uploading...';
    if (/Total (cinematic )?pipeline/i.test(raw)) return 'Almost done — saving...';
    if (/Generating thumbnail/i.test(raw)) return 'Creating thumbnails...';
    if (/watermark/i.test(raw)) return 'Finishing up...';
    return raw.replace(/\[.*?\]\s*/g, '').replace(/Step \d\/\d:\s*/g, '').substring(0, 60);
}

/** Map cook log lines to a believable percent (queued stays near 0). */
function _estimateCookPercent(raw, msgCount) {
    const r = raw || '';
    if (/Queued|You're next/i.test(r)) return 2;
    if (/Starting your cook/i.test(r)) return 5;
    if (/Done!|Saving to your library/i.test(r)) return 97;
    if (/Uploading your video|Saving thumbnail|Upload slow/i.test(r)) return 92;
    if (/Total (cinematic )?pipeline|Assembly complete|Assembled/i.test(r)) return 85;
    if (/Assembling|Building your video|Concatenat/i.test(r)) return 70;
    if (/assets resolved|clips? rendered|images? prepared/i.test(r)) return 55;
    if (/Resolving assets|Finding footage/i.test(r)) return 40;
    if (/Planning scenes|DirectorScore|Segment|concepts? planned/i.test(r)) return 25;
    if (/Analyzing|Aligning|sentences aligned/i.test(r)) return 12;
    if (/Illustrations?:\s*(\d+)\/(\d+)/i.test(r)) {
        const m = r.match(/(\d+)\/(\d+)/);
        if (m) return Math.min(65, 30 + Math.round((parseInt(m[1]) / parseInt(m[2])) * 30));
    }
    // Fallback: slow climb so we never sit at 8% forever
    return Math.min(80, Math.max(8, Math.round((msgCount / 12) * 70)));
}

const cookingManager = {
    jobId: null,
    evtSrc: null,
    title: '',
    result: null,
    msgCount: 0,
    kind: 'pipeline', // 'pipeline' | 'storyboard'
    activeCount: 0,
    slotLimit: 1,

    get isCooking() { return this.jobId && !this.result; },

    async start() {
        if (this.isCooking && this.slotLimit <= 1) {
            alert('A video is already cooking. Wait for it to finish or cancel it.');
            return;
        }
        if (this.isCooking && this.activeCount >= this.slotLimit) {
            alert(`You're already cooking ${this.activeCount} video${this.activeCount === 1 ? '' : 's'}. Your plan allows ${this.slotLimit} at a time.`);
            return;
        }

        this.result = null;
        this.msgCount = 0;
        this.kind = 'pipeline';
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
                    voiceover_path: state.voiceoverPath || '',
                    title: state.title,
                    niche: state.niche,
                    recipe: state.nicheData?.recipe || 'animated_explainer',
                    thumbnail_path: state.thumbnailPath,
                    notify_email: notifyEmail,
                    avatar_id: state.avatarId || '',
                    voice_id: state.voiceId || '',
                    image_quality: supportsImageQualityPicker() ? (state.imageQuality || 'standard') : 'standard',
                }),
            });
            const data = await readJson(res, {});
            if (!res.ok) {
                const errMsg = typeof data.detail === 'string' ? data.detail : (data.detail?.message || JSON.stringify(data.detail) || 'Build failed');
                if (res.status === 401) { showAuthModal(); }
                else if (res.status === 402 && isTrialUser()) { showTrialExhaustedModal(); }
                else if (res.status === 402 && isPaidUser() && !isTrialUser()) {
                    const needMatch = String(errMsg).match(/Need\s+(\d+)/i);
                    const need = needMatch ? parseInt(needMatch[1], 10) : cookCreditCost();
                    showCreditsNeededModal({ need, have: currentUser?.credits ?? 0, reason: 'credits' });
                }
                else if (res.status === 402) { showPricingModal({ reason: 'cook' }); }
                else if (res.status === 409) { alert(errMsg); }
                else { alert(errMsg); }
                throw new Error(errMsg);
            }
            this.jobId = data.job_id;
            this.queuePosition = data.queue_position || 0;
            this.estWaitMinutes = data.est_wait_minutes || 0;
            this.activeCount = Math.max(1, this.activeCount + 1);
            this._persist();
            this._showCookingBar();
            // Immediate honest queue state before SSE connects
            if (data.status === 'queued' && (data.queue_position || 0) > 0) {
                const wait = Math.max(1, Math.round(data.est_wait_minutes || 7));
                const qMsg = data.queue_position <= 1 && data.status === 'queued'
                    ? `Queued — waiting for a free cook slot (~${wait} min)`
                    : `Queued — position ${data.queue_position} (~${wait} min wait)`;
                const progressLog = document.getElementById('progress-log');
                const statusEl = document.getElementById('cooking-bar-status');
                const etaEl = document.getElementById('progress-eta');
                const pctEl = document.getElementById('progress-pct');
                const progressBar = document.getElementById('progress-bar');
                if (statusEl) statusEl.textContent = qMsg.substring(0, 60);
                if (progressLog) {
                    const line = document.createElement('div');
                    line.textContent = `> ${qMsg}`;
                    progressLog.appendChild(line);
                }
                if (etaEl) etaEl.textContent = `~${wait} min in queue`;
                if (pctEl) pctEl.textContent = '2%';
                if (progressBar) progressBar.style.width = '2%';
            }
            this._connect();
            // Reflect the deducted credits in the UI immediately
            const charged = Number(data.credits_charged || cookCreditCost() || 1);
            if (currentUser && typeof currentUser.credits === 'number' && charged > 0) {
                currentUser.credits = Math.max(0, currentUser.credits - charged);
                updateAuthUI();
            }
            refreshUserData();
        } catch (e) {
            if (e.message.includes('Sign in')) showAuthModal();
            document.getElementById('build-start').classList.remove('hidden');
            document.getElementById('build-progress').classList.add('hidden');
        }
    },

    /** Track a storyboard animate/assemble cook so the sticky bar survives refresh. */
    adoptStoryboard(jobId, title) {
        if (!jobId) return;
        this.jobId = jobId;
        this.title = title || 'your video';
        this.kind = 'storyboard';
        this.result = null;
        this.activeCount = Math.max(1, this.activeCount);
        this._persist();
        this._showCookingBar();
    },

    _connect() {
        // Reset per-connection state so a reconnect replay doesn't duplicate the
        // log or inflate the progress bar (the server replays from the start).
        this.msgCount = 0;
        this.evtSrc = new EventSource(`/api/build/${this.jobId}/progress`);
        const progressBar = document.getElementById('progress-bar');
        const progressLog = document.getElementById('progress-log');
        // Keep any immediate queue line; only clear if empty
        if (progressLog && !progressLog.children.length) progressLog.innerHTML = '';

        this.evtSrc.addEventListener('progress', (e) => {
            this.msgCount++;
            const msg = safeJsonParse(e.data);
            if (!msg || typeof msg !== 'object') return;
            const friendly = _friendlyProgress(msg.message || '');
            const statusEl = document.getElementById('cooking-bar-status');
            if (statusEl) statusEl.textContent = friendly.substring(0, 60);
            if (progressLog) {
                // Deduplicate consecutive identical queue lines
                const last = progressLog.lastElementChild;
                if (!last || last.textContent !== `> ${friendly}`) {
                    const line = document.createElement('div');
                    line.textContent = `> ${friendly}`;
                    progressLog.appendChild(line);
                    progressLog.scrollTop = progressLog.scrollHeight;
                }
            }
            const pct = _estimateCookPercent(msg.message, this.msgCount);
            const pctEl = document.getElementById('progress-pct');
            if (progressBar) progressBar.style.width = pct + '%';
            if (pctEl) pctEl.textContent = pct + '%';
            const etaEl = document.getElementById('progress-eta');
            if (etaEl) {
                if (/Queued|You're next/i.test(msg.message || '')) {
                    const m = (msg.message || '').match(/~(\d+)\s*min/i);
                    etaEl.textContent = m ? `~${m[1]} min in queue` : 'in queue';
                } else if (pct >= 90) etaEl.textContent = 'almost done';
                else if (pct >= 70) etaEl.textContent = 'about 1 minute';
                else etaEl.textContent = 'about 3–7 minutes';
            }
        });
        this.evtSrc.addEventListener('complete', (e) => {
            this.evtSrc.close();
            this.evtSrc = null;
            this.result = safeJsonParse(e.data);
            if (!this.result || !this.result.output_url) {
                this._reattach();
                return;
            }
            this._clear();
            state.videoUrl = this.result.output_url;
            state.videoPath = this.result.output_path;
            this._hideCookingBar();
            const pctEl = document.getElementById('progress-pct');
            if (progressBar) progressBar.style.width = '100%';
            if (pctEl) pctEl.textContent = '100%';

            // Always surface the finished video on the Cook step
            if (state.page !== 'pipeline') navigateTo('pipeline');
            if (state.step !== 6) goToStep(6);
            setTimeout(() => showUploadKit(this.result), 400);
            try { loadHistory(); } catch (_) {}
            refreshUserData();
        });

        this.evtSrc.addEventListener('error', (e) => {
            // Explicit server-sent error event → the render genuinely failed.
            if (e && e.data) {
                let err = 'Unknown error';
                try { err = JSON.parse(e.data).error || err; } catch (_) {}
                try { this.evtSrc && this.evtSrc.close(); } catch (_) {}
                this.evtSrc = null;
                this._clear();
                this._hideCookingBar();
                alert('Build failed: ' + err);
                return;
            }
            // Transient disconnect (idle timeout / LB / network). The render keeps
            // running server-side — reconnect and replay instead of giving up.
            try { this.evtSrc && this.evtSrc.close(); } catch (_) {}
            this.evtSrc = null;
            if (this.jobId && !this.result) {
                clearTimeout(this._reconnectTimer);
                this._reconnectTimer = setTimeout(() => this._reattach(), 2500);
            }
        });
    },

    _persist() {
        try {
            localStorage.setItem('cr_active_job', JSON.stringify({
                jobId: this.jobId,
                title: this.title,
                kind: this.kind || 'pipeline',
            }));
        } catch (_) {}
    },

    _clear() {
        this.jobId = null;
        this.kind = 'pipeline';
        this.activeCount = Math.max(0, (this.activeCount || 1) - 1);
        try { localStorage.removeItem('cr_active_job'); } catch (_) {}
    },

    // Re-attach to an in-flight (or just-finished) render after a page refresh.
    async restore() {
        // Prefer server truth so cleared localStorage still shows the bar.
        let serverJobs = [];
        try {
            const res = await fetch('/api/cooks/active');
            if (res.ok) {
                const data = await res.json();
                serverJobs = Array.isArray(data.jobs) ? data.jobs : [];
                this.slotLimit = Math.max(1, Number(data.limit) || 1);
                this.activeCount = Number(data.count) || serverJobs.length;
            }
        } catch (_) {}

        let saved;
        try { saved = JSON.parse(localStorage.getItem('cr_active_job') || 'null'); } catch (_) { saved = null; }

        let pick = null;
        if (serverJobs.length) {
            const savedId = saved && saved.jobId;
            pick = serverJobs.find(j => j.job_id === savedId) || serverJobs[0];
        } else if (saved && saved.jobId) {
            pick = { job_id: saved.jobId, title: saved.title, kind: saved.kind || 'pipeline' };
        }
        if (!pick || !pick.job_id) return;

        this.jobId = pick.job_id;
        this.title = pick.title || (saved && saved.title) || 'your video';
        this.kind = pick.kind || (saved && saved.kind) || 'pipeline';
        this.result = null;
        this._persist();
        if (this.kind === 'storyboard') {
            this._reattachStoryboard(pick.last_message || '');
        } else {
            this._reattach();
        }
    },

    _reattachStoryboard(lastMessage) {
        if (typeof _sbAssembleJobId !== 'undefined') _sbAssembleJobId = this.jobId;
        this._showCookingBar();
        if (lastMessage) {
            const statusEl = document.getElementById('cooking-bar-status');
            if (statusEl) statusEl.textContent = (typeof _friendlySbProgress === 'function'
                ? _friendlySbProgress(lastMessage)
                : lastMessage).substring(0, 60);
        }
        if (typeof _sbAssemblePollTimer !== 'undefined' && _sbAssemblePollTimer) {
            clearInterval(_sbAssemblePollTimer);
        }
        if (typeof pollStoryboardAssemble === 'function') {
            _sbAssemblePollTimer = setInterval(pollStoryboardAssemble, 2500);
            pollStoryboardAssemble();
        }
    },

    // Check the job's real state, then either finish, stop, or reconnect the
    // live stream. Shared by page-load restore and transient SSE reconnects.
    async _reattach() {
        if (!this.jobId || this.result) return;
        if (this.kind === 'storyboard') {
            this._reattachStoryboard('');
            return;
        }
        let res;
        try {
            res = await fetch(`/api/build/${this.jobId}/result`);
        } catch (_) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = setTimeout(() => this._reattach(), 3000);
            return;
        }

        if (res.status === 404) {
            // Job no longer exists (server restarted/redeployed) — truly lost.
            this._clear();
            this._hideCookingBar();
            showRenderLostNotice();
            return;
        }

        const data = await res.json();
        if (data && data.output_url) {
            this.result = data;
            state.videoUrl = data.output_url;
            state.videoPath = data.output_path;
            this._clear();
            this._hideCookingBar();
            if (state.page !== 'pipeline') navigateTo('pipeline');
            if (state.step !== 6) goToStep(6);
            showUploadKit(data);
            try { loadHistory(); } catch (_) {}
            return;
        }
        if (data && (data.status === 'error' || data.status === 'cancelled')) {
            this._clear();
            this._hideCookingBar();
            return;
        }
        // Still running / queued — show the bar and reconnect the live stream.
        if (data && data.status === 'queued') {
            const wait = Math.max(1, Math.round(data.est_wait_minutes || 7));
            const statusEl = document.getElementById('cooking-bar-status');
            if (statusEl) {
                statusEl.textContent = data.queue_position
                    ? `Queued — position ${data.queue_position} (~${wait} min)`
                    : `Queued (~${wait} min)`;
            }
        }
        this._showCookingBar();
        this._connect();
    },

    _showCookingBar() {
        const bar = document.getElementById('cooking-bar');
        const titleEl = document.getElementById('cooking-bar-title');
        if (titleEl) {
            const n = this.activeCount || 1;
            titleEl.textContent = n > 1
                ? `${n} videos`
                : (this.title || 'your video');
        }
        const statusEl = document.getElementById('cooking-bar-status');
        if (statusEl && (!statusEl.textContent || statusEl.textContent === 'Starting...')) {
            statusEl.textContent = this.kind === 'storyboard'
                ? 'Starting your cook…'
                : 'Joining cook queue...';
        }
        if (bar) bar.classList.remove('hidden');
    },

    _hideCookingBar() {
        document.getElementById('cooking-bar')?.classList.add('hidden');
    },

    _showToast() {
        document.getElementById('toast-title').textContent = this.title;
        document.getElementById('toast').classList.remove('hidden');
        setTimeout(() => dismissToast(), 15000);
    },

    viewProgress() {
        if (this.kind === 'storyboard') {
            navigateTo('pipeline');
            if (typeof isStoryboardRecipe === 'function' && isStoryboardRecipe()) {
                goToStep('sb-assemble');
            }
            return;
        }
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
        if (typeof _sbAssemblePollTimer !== 'undefined' && _sbAssemblePollTimer) {
            clearInterval(_sbAssemblePollTimer);
            _sbAssemblePollTimer = null;
        }
        if (this.jobId) {
            fetch(`/api/build/${this.jobId}`, { method: 'DELETE' }).catch(() => {});
        }
        this.result = null;
        this._clear();
        this._hideCookingBar();
        if (state.page === 'pipeline' && state.step === 6) {
            document.getElementById('build-start').classList.remove('hidden');
            document.getElementById('build-progress').classList.add('hidden');
        }
        const btnAssemble = document.getElementById('btn-sb-assemble-run');
        const btnAnimate = document.getElementById('btn-sb-animate-run');
        if (btnAssemble) setLoading(btnAssemble, false);
        if (btnAnimate) setLoading(btnAnimate, false);
    },
};

function showRenderLostNotice() {
    alert("We couldn't recover your last render after the page reloaded — it may still be finishing. Check History in a few minutes; if it's not there and your credit wasn't restored, email hello@channelrecipe.com and we'll sort it out.");
}

function dismissToast() {
    document.getElementById('toast').classList.add('hidden');
}

async function startBuild() {
    if (!ensureCanCook(startBuild)) return;
    const cap = effectiveMinuteCap();
    const words = (state.script || '').trim().split(/\s+/).filter(Boolean).length;
    const estMin = words / 150;
    if (!hasFullLengthAccess() && estMin > cap + 0.5) {
        alert(`Trial videos are capped at ${cap} minutes. Shorten your script (~${Math.round(cap * 150)} words) or start your plan for longer videos.`);
        return;
    }
    if (supportsImageQualityPicker() && state.imageQuality === 'high') {
        if (!canUseHighQuality()) {
            showPricingModal({ reason: 'hq' });
            return;
        }
        if (estMin > hqMaxMinutes() + 0.5) {
            alert(`High quality caps at ${hqMaxMinutes()} minutes. Shorten the script, or cook part 2 separately.`);
            return;
        }
        const need = hqCreditCost();
        if (
            currentUser && !currentUser.is_admin
            && typeof currentUser.credits === 'number'
            && currentUser.credits < need
        ) {
            showCreditsNeededModal({ need, have: currentUser.credits, reason: 'hq' });
            return;
        }
    }
    // Standard cook: paid users with 0 credits should top up, not re-subscribe
    if (
        isPaidUser() && !isTrialUser()
        && currentUser && typeof currentUser.credits === 'number'
        && currentUser.credits < cookCreditCost()
        && !(currentUser.is_admin)
    ) {
        showCreditsNeededModal({ need: cookCreditCost(), have: currentUser.credits, reason: 'cook' });
        return;
    }
    const btn = document.getElementById('btn-build');
    if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; }
    try {
        await cookingManager.start();
    } finally {
        if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
    }
}

async function playableMediaUrl(url) {
    if (!url || typeof url !== 'string') return url;
    if (!/digitaloceanspaces\.com/i.test(url)) return url;
    try {
        const res = await fetch('/api/media/playable?url=' + encodeURIComponent(url));
        if (!res.ok) return url;
        const data = await res.json();
        return (data && data.url) || url;
    } catch (_) {
        return url;
    }
}

async function showUploadKit(buildResult) {
    document.getElementById('build-progress').classList.add('hidden');
    document.getElementById('upload-kit').classList.remove('hidden');
    const videoUrl = await playableMediaUrl(buildResult.output_url);
    const thumbUrl = await playableMediaUrl(buildResult.thumbnail_url || state.thumbnailUrl || '');
    state.videoUrl = videoUrl;
    document.getElementById('result-video').src = videoUrl;
    const dl = document.getElementById('download-link');
    dl.href = videoUrl;
    dl.setAttribute('download', 'video.mp4');
    if (thumbUrl) {
        state.thumbnailUrl = thumbUrl;
        document.getElementById('kit-thumb').src = thumbUrl;
        document.getElementById('kit-thumb-dl').href = thumbUrl;
        document.getElementById('kit-thumb-dl').setAttribute('download', 'thumbnail.png');
        document.getElementById('kit-thumb-wrap').classList.remove('hidden');
    } else {
        document.getElementById('kit-thumb-wrap').classList.add('hidden');
    }
    document.getElementById('kit-title').textContent = state.title;
    const isFree = !currentUser || currentUser.plan === 'free';
    document.getElementById('trial-watermark-note')?.classList.toggle('hidden', !isFree);
    const videoId = buildResult.video_id || null;
    try {
        const res = await fetch('/api/upload-kit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: state.title, script: state.script, niche: state.niche }) });
        const kit = await res.json();
        const tagsArr = Array.isArray(kit.tags) ? kit.tags : (kit.tags ? String(kit.tags).split(',').map(t => t.trim()) : []);
        const hashArr = Array.isArray(kit.hashtags) ? kit.hashtags : (kit.hashtags ? String(kit.hashtags).split(',').map(t => t.trim()) : []);
        document.getElementById('kit-desc').textContent = kit.description || '';
        document.getElementById('kit-tags').textContent = tagsArr.join(', ');
        // Attach the kit to the saved video so it shows in History.
        if (videoId) {
            fetch(`/api/videos/${videoId}/kit`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ description: kit.description || '', tags: tagsArr, hashtags: hashArr }),
            }).catch(() => {});
        }
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

function isAdminUser() {
    return !!(currentUser && currentUser.is_admin);
}

function syncAdminChannelUI() {
    const multi = document.getElementById('ss-channel-multi');
    const single = document.getElementById('ss-channel-single');
    if (!multi || !single) return;
    const admin = isAdminUser();
    multi.classList.toggle('hidden', !admin);
    single.classList.toggle('hidden', admin);
    if (admin) {
        const rows = document.getElementById('ss-channel-rows');
        if (rows && !rows.children.length) {
            // Seed from the single-channel fields if present
            const url = document.getElementById('ss-channel-url')?.value?.trim() || '';
            const count = parseInt(document.getElementById('ss-video-count')?.value || '20', 10) || 20;
            addAdminChannelRow(url, count);
        }
    }
}

function addAdminChannelRow(url = '', maxVideos = 20) {
    const rows = document.getElementById('ss-channel-rows');
    if (!rows) return;
    const idx = rows.children.length;
    const n = Math.max(5, Math.min(50, parseInt(maxVideos, 10) || 20));
    const wrap = document.createElement('div');
    wrap.className = 'ss-channel-row cr-surface';
    wrap.style.cssText = 'padding: 12px 14px;';
    wrap.innerHTML = `
        <div class="flex items-start gap-3" style="gap: 10px;">
            <div style="flex:1;min-width:0;">
                <label class="cr-label">Channel URL</label>
                <input type="text" class="cr-input ss-multi-url" placeholder="https://youtube.com/@channelname" value="${_esc(url)}">
            </div>
            <button type="button" class="btn-secondary ss-multi-remove" style="margin-top: 22px; font-size: 12px; padding: 6px 10px;" title="Remove">✕</button>
        </div>
        <div class="flex items-center gap-4" style="margin-top: 10px;">
            <div class="flex-1">
                <label class="cr-label">Videos to fetch</label>
                <input type="range" min="5" max="50" value="${n}" class="w-full ss-multi-count" style="accent-color: var(--accent);">
            </div>
            <span class="cr-mono ss-multi-count-label" style="margin-top: 24px; color: var(--app-ink);">${n}</span>
        </div>
    `;
    const slider = wrap.querySelector('.ss-multi-count');
    const label = wrap.querySelector('.ss-multi-count-label');
    slider?.addEventListener('input', () => { if (label) label.textContent = slider.value; });
    wrap.querySelector('.ss-multi-remove')?.addEventListener('click', () => {
        if (rows.children.length <= 1) {
            wrap.querySelector('.ss-multi-url').value = '';
            return;
        }
        wrap.remove();
    });
    rows.appendChild(wrap);
    // keep unused idx quiet for linters
    void idx;
}

function collectAdminChannelRows() {
    const rows = [...document.querySelectorAll('#ss-channel-rows .ss-channel-row')];
    return rows.map((row) => ({
        channel_url: row.querySelector('.ss-multi-url')?.value?.trim() || '',
        max_videos: parseInt(row.querySelector('.ss-multi-count')?.value || '20', 10) || 20,
    })).filter((r) => r.channel_url);
}

function _channelDownloadPayload() {
    if (state.channelDataBatch) return state.channelDataBatch;
    return state.channelData || null;
}

function downloadChannelData() {
    const payload = _channelDownloadPayload();
    if (!payload) {
        showSoftPrompt('Fetch channel data first, then download.');
        return;
    }
    const name = payload.channel_name
        || (payload.channels && payload.channels[0] && payload.channels[0].channel_name)
        || 'channel';
    const safe = String(name).replace(/[^\w\-]+/g, '_').slice(0, 40) || 'channel';
    const stamp = new Date().toISOString().slice(0, 10);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${safe}_channel_data_${stamp}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

function copyChannelData() {
    const payload = _channelDownloadPayload();
    if (!payload) {
        showSoftPrompt('Fetch channel data first, then copy.');
        return;
    }
    const text = JSON.stringify(payload, null, 2);
    navigator.clipboard?.writeText(text).then(
        () => showSoftPrompt('Channel data copied.'),
        () => {
            // Fallback select
            const pre = document.getElementById('ss-channel-data');
            if (pre) {
                const range = document.createRange();
                range.selectNodeContents(pre);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
            }
            showSoftPrompt('Select the JSON and copy (⌘/Ctrl+C).');
        }
    );
}

function _showChannelResult(payload, { batch = false } = {}) {
    if (batch) {
        state.channelDataBatch = payload;
        // Script Studio analyze / ideas still expect a single channel object
        state.channelData = (payload.channels && payload.channels[0]) || null;
    } else {
        state.channelDataBatch = null;
        state.channelData = payload;
    }
    const pre = document.getElementById('ss-channel-data');
    if (pre) {
        const note = batch
            ? `/* ${payload.count || 0} channel(s) fetched — Download JSON has all. Analyze uses the first. */\n`
            : '';
        pre.textContent = note + JSON.stringify(payload, null, 2);
    }
    document.getElementById('ss-channel-result')?.classList.remove('hidden');
}

async function fetchChannelData() {
    if (!ensureAuth(fetchChannelData)) return;
    const btn = document.getElementById('btn-fetch-channel');
    setLoading(btn, true);
    try {
        if (isAdminUser()) {
            const channels = collectAdminChannelRows();
            if (!channels.length) {
                throw new Error('Add at least one YouTube channel URL.');
            }
            if (channels.length === 1) {
                const res = await fetch('/api/channel/fetch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(channels[0]),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(friendlyApiError(data, 'Channel fetch failed'));
                _showChannelResult(data, { batch: false });
            } else {
                const res = await fetch('/api/channel/fetch-batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ channels }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(friendlyApiError(data, 'Batch fetch failed'));
                _showChannelResult(data, { batch: true });
                if ((data.errors || []).length) {
                    const msgs = data.errors.map((e) => `${e.channel_url}: ${e.error}`).join('\n');
                    showSoftPrompt(
                        `Fetched ${data.count} channel(s). ${data.errors.length} failed:\n${msgs.slice(0, 280)}`
                    );
                }
            }
        } else {
            const res = await fetch('/api/channel/fetch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    channel_url: document.getElementById('ss-channel-url').value.trim(),
                    max_videos: parseInt(document.getElementById('ss-video-count').value, 10) || 20,
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(friendlyApiError(data, 'Channel fetch failed'));
            _showChannelResult(data, { batch: false });
        }
    } catch (e) {
        showSoftPrompt(e.message || 'Channel fetch failed.');
    } finally {
        setLoading(btn, false);
    }
}

async function analyzeChannel() {
    if (!ensureAuth(analyzeChannel)) return;
    if (!state.channelData) {
        showSoftPrompt(
            'Fetch channel data first — then we can analyze what’s working on that channel.',
            'Go to Channel Data',
            () => {
                navigateTo('script-studio');
                const tab = document.querySelector('.studio-tab[data-tab="ss-channel"]');
                if (tab) tab.click();
            }
        );
        return;
    }
    const btn = document.getElementById('btn-analyze-channel')
        || document.querySelector('#ss-channel-result .btn-primary');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/channel/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_data: state.channelData }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Analysis failed'));
        state.channelAnalysis = data.analysis;
        document.getElementById('ss-analysis-text').textContent = data.analysis;
        document.getElementById('ss-analysis').classList.remove('hidden');
    } catch (e) {
        showSoftPrompt(e.message || 'Channel analysis failed.');
    } finally {
        setLoading(btn, false);
    }
}

async function generateIdeas() {
    if (!ensureAuth(generateIdeas)) return;
    // Channel data is optional — we generate general ideas if missing, but nudge them
    if (!state.channelData && !state._ideasGeneralOk) {
        showSoftPrompt(
            'No channel data yet. We can still generate general viral ideas — or fetch a channel first for better results.',
            'Generate anyway',
            () => {
                state._ideasGeneralOk = true;
                generateIdeas();
            }
        );
        const el = document.getElementById('soft-prompt');
        if (el) {
            const row = el.querySelector('div:last-child');
            if (row) {
                const fetchBtn = document.createElement('button');
                fetchBtn.textContent = 'Fetch channel';
                fetchBtn.style.cssText = 'padding:8px 14px;border:1px solid var(--app-border);border-radius:8px;background:transparent;color:var(--app-ink-2);font-size:13px;cursor:pointer;';
                fetchBtn.onclick = () => {
                    el.style.display = 'none';
                    navigateTo('script-studio');
                    document.querySelector('.studio-tab[data-tab="ss-channel"]')?.click();
                };
                row.insertBefore(fetchBtn, row.firstChild);
            }
        }
        return;
    }
    state._ideasGeneralOk = false;
    const btn = document.querySelector('#ss-ideas .btn-primary');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/ideas', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_data: state.channelData || { topic_hint: 'faceless YouTube automation niches' },
                num_ideas: parseInt(document.getElementById('ss-idea-count').value),
                // Trim analysis — full essays make Claude much slower
                analysis: (state.channelAnalysis || '').slice(0, 1200),
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Idea generation failed'));
        const list = document.getElementById('ss-ideas-list');
        list.innerHTML = '';
        (data.ideas || []).forEach(idea => {
            const card = document.createElement('div');
            card.className = 'title-card';
            card.innerHTML = `<p class="text-gray-100 text-sm">${_esc(idea)}</p>`;
            card.addEventListener('click', () => {
                document.querySelectorAll('#ss-ideas-list .title-card').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                document.getElementById('ss-selected-idea').value = idea;
            });
            list.appendChild(card);
        });
        if (!(data.ideas || []).length) {
            showSoftPrompt('No ideas came back. Try again, or fetch channel data for better results.');
        }
    } catch (e) {
        showSoftPrompt(e.message || 'Idea generation failed.');
    } finally {
        setLoading(btn, false);
    }
}

async function generateStudioTitles() {
    if (!ensureAuth(generateStudioTitles)) return;
    const idea = document.getElementById('ss-title-idea').value.trim();
    if (!idea) {
        showSoftPrompt(
            'Paste a video idea first — then we’ll generate title options.',
            'Use selected idea',
            () => {
                const selected = document.getElementById('ss-selected-idea')?.value?.trim();
                if (selected) {
                    document.getElementById('ss-title-idea').value = selected;
                    const tab = document.querySelector('.studio-tab[data-tab="ss-titles"]');
                    if (tab) tab.click();
                } else {
                    const ideasTab = document.querySelector('.studio-tab[data-tab="ss-ideas"]');
                    if (ideasTab) ideasTab.click();
                }
            }
        );
        return;
    }
    const btn = document.querySelector('#ss-titles .btn-primary');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/titles/claude', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_idea: idea,
                channel_data: state.channelData || null,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Title generation failed'));
        const list = document.getElementById('ss-titles-list');
        list.innerHTML = '';
        (data.titles || []).forEach(t => {
            const card = document.createElement('div');
            card.className = 'title-card';
            card.innerHTML = `<p class="text-gray-100">${_esc(t)}</p>`;
            card.addEventListener('click', () => {
                document.querySelectorAll('#ss-titles-list .title-card').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                document.getElementById('ss-selected-title').value = t;
            });
            list.appendChild(card);
        });
    } catch (e) {
        showSoftPrompt(e.message || 'Title generation failed.');
    } finally {
        setLoading(btn, false);
    }
}

async function generateStudioScript() {
    if (!ensureAuth(generateStudioScript)) return;
    const title = document.getElementById('ss-script-title').value.trim();
    if (!title) {
        showSoftPrompt(
            'Enter a video title first — then we’ll write the full script.',
            'Use selected title',
            () => {
                const selected = document.getElementById('ss-selected-title')?.value?.trim();
                if (selected) {
                    document.getElementById('ss-script-title').value = selected;
                } else {
                    document.querySelector('.studio-tab[data-tab="ss-titles"]')?.click();
                }
            }
        );
        return;
    }
    const lengthInput = document.getElementById('ss-script-length');
    let targetMinutes = parseInt(lengthInput?.value) || 8;
    const cap = effectiveMinuteCap();
    if (!hasFullLengthAccess() && targetMinutes > cap) {
        targetMinutes = cap;
        if (lengthInput) lengthInput.value = cap;
        showSoftPrompt(`Trial caps scripts at ${cap} minutes. Start your plan for up to 20 min.`);
        return;
    }
    const btn = document.querySelector('#ss-script .btn-primary:first-of-type');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/script/claude', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title,
                video_idea: document.getElementById('ss-script-idea').value.trim(),
                channel_data: state.channelData || null,
                target_minutes: targetMinutes,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Script generation failed'));
        document.getElementById('ss-script-output').value = data.script;
        const wc = data.script.split(/\s+/).length;
        document.getElementById('ss-word-count').textContent = `${wc} words (~${Math.round(wc / 150)} min)`;
    } catch (e) {
        showSoftPrompt(e.message || 'Script generation failed.');
    } finally {
        setLoading(btn, false);
    }
}

function useScriptInPipeline() {
    const script = document.getElementById('ss-script-output').value.trim();
    const title = document.getElementById('ss-script-title').value.trim();
    if (script) state.script = script;
    if (title) state.title = title;
    if (!state.niche) state.niche = 'animated_explainer';
    if (!state.nicheData) state.nicheData = { name: 'Animated Explainer', recipe: 'animated_explainer' };
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
        (Array.isArray(voices) ? voices : []).forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = `${v.name} — ${v.tag}`;
            sel.appendChild(opt);
        });
        try {
            const cRes = await fetch('/api/voice/clones');
            if (cRes.ok) {
                const cData = await cRes.json();
                (cData.clones || []).forEach((c) => {
                    const opt = document.createElement('option');
                    opt.value = c.voice_id;
                    opt.textContent = `${c.name} — Cloned`;
                    sel.prepend(opt);
                });
            }
        } catch (_) {}
    } catch (e) {
        console.error('Failed to load voice options:', e);
    }
}

async function generateStudioVoiceover() {
    if (!ensureAuth(generateStudioVoiceover)) return;
    const script = document.getElementById('vo-script')?.value?.trim() || '';
    const voLimit = assertVoiceoverLengthOk(script);
    if (voLimit) {
        showSoftPrompt(voLimit);
        return;
    }
    if (!script) {
        showSoftPrompt('Paste a script first — then we’ll generate the voiceover.');
        document.getElementById('vo-script')?.focus();
        return;
    }
    const btn = document.getElementById('btn-vo-generate');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/voiceover/studio', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                script,
                voice: document.getElementById('vo-voice').value,
                style_preset: document.getElementById('vo-style').value,
                custom_notes: document.getElementById('vo-custom-notes')?.value || '',
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Voiceover generation failed'));
        document.getElementById('vo-audio').src = data.url;
        document.getElementById('vo-download').href = data.url;
        document.getElementById('vo-result').classList.remove('hidden');
    } catch (e) {
        showSoftPrompt(e.message || 'Voiceover generation failed.');
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
// Niche Finder (shared niche library)
// ---------------------------------------------------------------------------
let _nfPollTimer = null;
let _nfAccess = null;
let _nfPage = 1;
let _nfTotal = 0;
const _NF_PAGE = 40;
let _nfFilterTimer = null;
let _nfFiltersBound = false;
const _NF_RECENT_MAX = 500000;
const _NF_SUBS_MAX = 500000;
const _NF_REV_MAX = 5000;

function _nfFmt(n) {
    const x = Number(n) || 0;
    if (x >= 1e6) return (x / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (x >= 1e3) return (x / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
    return String(Math.round(x));
}

function _nfDur(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (h) return `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
    return `${m}:${String(r).padStart(2, '0')}`;
}

function _nfMoney(n) {
    const x = Number(n) || 0;
    if (x >= 1000) return '$' + _nfFmt(x);
    return '$' + String(Math.round(x));
}

function _nfSetHidden(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = value == null || value === '' ? '' : String(value);
}

function _nfSyncDual(kind) {
    const isRecent = kind === 'recent';
    const minEl = document.getElementById(isRecent ? 'nf-recent-min' : 'nf-subs-min');
    const maxEl = document.getElementById(isRecent ? 'nf-recent-max' : 'nf-subs-max');
    const fill = document.getElementById(isRecent ? 'nf-recent-fill' : 'nf-subs-fill');
    const label = document.getElementById(isRecent ? 'nf-recent-label' : 'nf-subs-label');
    const ceiling = isRecent ? _NF_RECENT_MAX : _NF_SUBS_MAX;
    if (!minEl || !maxEl) return { min: 0, max: ceiling };
    let min = Number(minEl.value) || 0;
    let max = Number(maxEl.value) || 0;
    if (min > max) {
        if (minEl.dataset.nfLast === 'min') max = min;
        else min = max;
        minEl.value = String(min);
        maxEl.value = String(max);
    }
    const left = (min / ceiling) * 100;
    const right = (max / ceiling) * 100;
    if (fill) {
        fill.style.left = `${left}%`;
        fill.style.width = `${Math.max(0, right - left)}%`;
    }
    const openMax = max >= ceiling;
    const openMin = min <= 0;
    let text = 'Any';
    if (openMin && openMax) text = 'Any';
    else if (openMin) text = `≤ ${_nfFmt(max)}`;
    else if (openMax) text = `${_nfFmt(min)}+`;
    else text = `${_nfFmt(min)} – ${_nfFmt(max)}`;
    if (label) label.textContent = text;

    if (isRecent) {
        _nfSetHidden('nf-f-min-recent', openMin ? '' : min);
        _nfSetHidden('nf-f-max-recent', openMax ? '' : max);
    } else {
        _nfSetHidden('nf-f-min-subs', openMin ? '' : min);
        _nfSetHidden('nf-f-max-subs', openMax ? '' : max);
    }
    _nfSyncPresets(kind, min, openMax ? '' : max);
    return { min, max, openMin, openMax };
}

function _nfPaintSingleRange() {
    const el = document.getElementById('nf-rev-min');
    if (!el) return;
    const max = Number(el.max) || _NF_REV_MAX;
    const val = Number(el.value) || 0;
    const pct = Math.max(0, Math.min(100, (val / max) * 100));
    el.style.background =
        `linear-gradient(90deg, var(--accent) ${pct}%, var(--app-border) ${pct}%) no-repeat center / 100% 6px`;
}

function _nfSyncRevenue() {
    const el = document.getElementById('nf-rev-min');
    const label = document.getElementById('nf-rev-label');
    const val = Number(el?.value) || 0;
    if (label) label.textContent = val > 0 ? `${_nfMoney(val)}+` : 'Any';
    _nfSetHidden('nf-f-min-rev', val > 0 ? val : '');
    _nfSyncPresets('rev', val, '');
    _nfPaintSingleRange();
    return val;
}

function _nfSyncPresets(kind, min, max) {
    const block = document.querySelector(`.nf-filter-block[data-nf-range="${kind}"]`);
    if (!block) return;
    const presets = [...block.querySelectorAll('.nf-preset')];
    presets.forEach((btn) => {
        const pMin = Number(btn.dataset.min || 0);
        const pMaxRaw = btn.dataset.max;
        const pMax = pMaxRaw === undefined || pMaxRaw === '' ? '' : Number(pMaxRaw);
        const maxMatch = (max === '' || max == null) ? pMax === '' : Number(pMax) === Number(max);
        const minMatch = pMin === Number(min || 0);
        btn.classList.toggle('is-active', minMatch && maxMatch);
    });
}

function _nfActiveFilterCount() {
    let n = 0;
    const q = document.getElementById('nf-f-q')?.value?.trim();
    if (q) n += 1;
    if (document.getElementById('nf-f-min-recent')?.value) n += 1;
    if (document.getElementById('nf-f-max-recent')?.value) n += 1;
    if (document.getElementById('nf-f-min-subs')?.value) n += 1;
    if (document.getElementById('nf-f-max-subs')?.value) n += 1;
    if (document.getElementById('nf-f-min-rev')?.value) n += 1;
    if (!document.getElementById('nf-f-has-recent')?.checked) n += 1;
    if (document.getElementById('nf-f-active')?.checked) n += 1;
    const sort = document.getElementById('nf-sort')?.value || 'recent_revenue';
    if (sort !== 'recent_revenue') n += 1;
    return n;
}

function _nfRenderFilterChips() {
    const wrap = document.getElementById('nf-filter-chips');
    const clearBtn = document.getElementById('nf-clear-filters');
    if (!wrap) return;
    const chips = [];
    const q = document.getElementById('nf-f-q')?.value?.trim();
    if (q) chips.push({ key: 'q', label: `Search “${q.length > 24 ? q.slice(0, 24) + '…' : q}”` });
    const minR = document.getElementById('nf-f-min-recent')?.value;
    const maxR = document.getElementById('nf-f-max-recent')?.value;
    if (minR || maxR) {
        const t = !minR ? `Recent ≤ ${_nfFmt(maxR)}` : !maxR ? `Recent ${_nfFmt(minR)}+` : `Recent ${_nfFmt(minR)}–${_nfFmt(maxR)}`;
        chips.push({ key: 'recent', label: t });
    }
    const minS = document.getElementById('nf-f-min-subs')?.value;
    const maxS = document.getElementById('nf-f-max-subs')?.value;
    if (minS || maxS) {
        const t = !minS ? `Subs ≤ ${_nfFmt(maxS)}` : !maxS ? `Subs ${_nfFmt(minS)}+` : `Subs ${_nfFmt(minS)}–${_nfFmt(maxS)}`;
        chips.push({ key: 'subs', label: t });
    }
    const minRev = document.getElementById('nf-f-min-rev')?.value;
    if (minRev) chips.push({ key: 'rev', label: `Earnings ${_nfMoney(minRev)}+` });
    if (!document.getElementById('nf-f-has-recent')?.checked) {
        chips.push({ key: 'has-recent-off', label: 'Including no recent avg' });
    }
    if (document.getElementById('nf-f-active')?.checked) {
        chips.push({ key: 'active', label: 'Active recently' });
    }
    const sort = document.getElementById('nf-sort');
    if (sort && sort.value !== 'recent_revenue') {
        const opt = sort.options[sort.selectedIndex];
        chips.push({ key: 'sort', label: opt ? opt.text : sort.value });
    }
    wrap.hidden = chips.length === 0;
    wrap.innerHTML = chips.map((c) => (
        `<span class="nf-chip">${c.label}<button type="button" aria-label="Remove filter" onclick="removeNicheFilterChip('${c.key}')">×</button></span>`
    )).join('');
    if (clearBtn) clearBtn.disabled = _nfActiveFilterCount() === 0;
}

function scheduleNicheFilterApply() {
    _nfSyncDual('recent');
    _nfSyncDual('subs');
    _nfSyncRevenue();
    _nfRenderFilterChips();
    clearTimeout(_nfFilterTimer);
    _nfFilterTimer = setTimeout(() => applyNicheFilters(), 320);
}

function applyNicheFilters() {
    loadNicheFinderFeed({ reset: true });
}

function toggleNicheFilter(kind) {
    if (kind === 'has-recent') {
        const box = document.getElementById('nf-f-has-recent');
        const btn = document.getElementById('nf-toggle-has-recent');
        if (!box || !btn) return;
        box.checked = !box.checked;
        btn.classList.toggle('is-on', box.checked);
        btn.setAttribute('aria-pressed', box.checked ? 'true' : 'false');
    } else if (kind === 'active') {
        const box = document.getElementById('nf-f-active');
        const btn = document.getElementById('nf-toggle-active');
        if (!box || !btn) return;
        box.checked = !box.checked;
        btn.classList.toggle('is-on', box.checked);
        btn.setAttribute('aria-pressed', box.checked ? 'true' : 'false');
    }
    scheduleNicheFilterApply();
}

function removeNicheFilterChip(key) {
    if (key === 'q') {
        const q = document.getElementById('nf-f-q');
        if (q) q.value = '';
    } else if (key === 'recent') {
        const minEl = document.getElementById('nf-recent-min');
        const maxEl = document.getElementById('nf-recent-max');
        if (minEl) minEl.value = '0';
        if (maxEl) maxEl.value = String(_NF_RECENT_MAX);
    } else if (key === 'subs') {
        const minEl = document.getElementById('nf-subs-min');
        const maxEl = document.getElementById('nf-subs-max');
        if (minEl) minEl.value = '0';
        if (maxEl) maxEl.value = String(_NF_SUBS_MAX);
    } else if (key === 'rev') {
        const el = document.getElementById('nf-rev-min');
        if (el) el.value = '0';
    } else if (key === 'has-recent-off') {
        const box = document.getElementById('nf-f-has-recent');
        const btn = document.getElementById('nf-toggle-has-recent');
        if (box) box.checked = true;
        if (btn) {
            btn.classList.add('is-on');
            btn.setAttribute('aria-pressed', 'true');
        }
    } else if (key === 'active') {
        const box = document.getElementById('nf-f-active');
        const btn = document.getElementById('nf-toggle-active');
        if (box) box.checked = false;
        if (btn) {
            btn.classList.remove('is-on');
            btn.setAttribute('aria-pressed', 'false');
        }
    } else if (key === 'sort') {
        const sort = document.getElementById('nf-sort');
        if (sort) sort.value = 'recent_revenue';
    }
    scheduleNicheFilterApply();
}

function clearNicheFilters() {
    const q = document.getElementById('nf-f-q');
    if (q) q.value = '';
    const sort = document.getElementById('nf-sort');
    if (sort) sort.value = 'recent_revenue';
    const recentMin = document.getElementById('nf-recent-min');
    const recentMax = document.getElementById('nf-recent-max');
    const subsMin = document.getElementById('nf-subs-min');
    const subsMax = document.getElementById('nf-subs-max');
    const rev = document.getElementById('nf-rev-min');
    if (recentMin) recentMin.value = '0';
    if (recentMax) recentMax.value = String(_NF_RECENT_MAX);
    if (subsMin) subsMin.value = '0';
    if (subsMax) subsMax.value = String(_NF_SUBS_MAX);
    if (rev) rev.value = '0';
    const hasRecent = document.getElementById('nf-f-has-recent');
    const active = document.getElementById('nf-f-active');
    if (hasRecent) hasRecent.checked = true;
    if (active) active.checked = false;
    const tHas = document.getElementById('nf-toggle-has-recent');
    const tAct = document.getElementById('nf-toggle-active');
    if (tHas) {
        tHas.classList.add('is-on');
        tHas.setAttribute('aria-pressed', 'true');
    }
    if (tAct) {
        tAct.classList.remove('is-on');
        tAct.setAttribute('aria-pressed', 'false');
    }
    scheduleNicheFilterApply();
}

function bindNicheFilters() {
    if (_nfFiltersBound) {
        _nfSyncDual('recent');
        _nfSyncDual('subs');
        _nfSyncRevenue();
        _nfRenderFilterChips();
        return;
    }
    _nfFiltersBound = true;

    const onDual = (kind, which) => (e) => {
        e.target.dataset.nfLast = which;
        const minEl = document.getElementById(kind === 'recent' ? 'nf-recent-min' : 'nf-subs-min');
        const maxEl = document.getElementById(kind === 'recent' ? 'nf-recent-max' : 'nf-subs-max');
        if (minEl && maxEl) {
            minEl.style.zIndex = which === 'min' ? '5' : '4';
            maxEl.style.zIndex = which === 'max' ? '5' : '4';
        }
        scheduleNicheFilterApply();
    };
    document.getElementById('nf-recent-min')?.addEventListener('input', onDual('recent', 'min'));
    document.getElementById('nf-recent-max')?.addEventListener('input', onDual('recent', 'max'));
    document.getElementById('nf-subs-min')?.addEventListener('input', onDual('subs', 'min'));
    document.getElementById('nf-subs-max')?.addEventListener('input', onDual('subs', 'max'));
    document.getElementById('nf-rev-min')?.addEventListener('input', () => {
        _nfPaintSingleRange();
        scheduleNicheFilterApply();
    });
    document.getElementById('nf-f-q')?.addEventListener('input', () => scheduleNicheFilterApply());
    document.getElementById('nf-sort')?.addEventListener('change', () => scheduleNicheFilterApply());

    document.querySelectorAll('.nf-filter-block .nf-preset').forEach((btn) => {
        btn.addEventListener('click', () => {
            const block = btn.closest('.nf-filter-block');
            const kind = block?.dataset.nfRange;
            const min = Number(btn.dataset.min || 0);
            const maxRaw = btn.dataset.max;
            if (kind === 'rev') {
                const el = document.getElementById('nf-rev-min');
                if (el) el.value = String(min);
            } else if (kind === 'recent' || kind === 'subs') {
                const ceiling = kind === 'recent' ? _NF_RECENT_MAX : _NF_SUBS_MAX;
                const minEl = document.getElementById(kind === 'recent' ? 'nf-recent-min' : 'nf-subs-min');
                const maxEl = document.getElementById(kind === 'recent' ? 'nf-recent-max' : 'nf-subs-max');
                const max = maxRaw === undefined || maxRaw === '' ? ceiling : Number(maxRaw);
                if (minEl) minEl.value = String(min);
                if (maxEl) maxEl.value = String(max);
            }
            scheduleNicheFilterApply();
        });
    });

    _nfSyncDual('recent');
    _nfSyncDual('subs');
    _nfSyncRevenue();
    _nfRenderFilterChips();
}

async function initNicheFinderPage() {
    if (!ensureAuth(initNicheFinderPage)) return;
    const gate = document.getElementById('nf-gate');
    const workspace = document.getElementById('nf-workspace');
    const adminPanel = document.getElementById('nf-admin-panel');
    const status = document.getElementById('nf-status');
    if (status) status.textContent = '';
    bindNicheFilters();
    try {
        const res = await fetch('/api/niche-finder/access');
        const data = await readJson(res, null);
        if (!res.ok) throw new Error(data?.detail || 'Could not load Niche Finder');
        _nfAccess = data;
        const kw = document.getElementById('nf-keywords');
        if (kw && !kw.value.trim() && Array.isArray(data.default_keywords) && data.default_keywords.length) {
            kw.value = data.default_keywords.join('\n');
        }
        if (data.can_browse) {
            gate?.classList.add('hidden');
            workspace?.classList.remove('hidden');
            adminPanel?.classList.toggle('hidden', !data.can_run);
            await loadNicheFinderFeed();
            if (data.can_run) {
                const resumeId = data.active_job_id || localStorage.getItem('nf_active_job_id');
                if (resumeId) _resumeNicheFinderJob(resumeId);
            }
        } else {
            workspace?.classList.add('hidden');
            gate?.classList.remove('hidden');
            const title = document.getElementById('nf-gate-title');
            const body = document.getElementById('nf-gate-body');
            const cta = document.getElementById('nf-gate-cta');
            if (title) title.textContent = 'Only available on Pro';
            if (body) body.textContent = data.message || 'Upgrade to Pro to browse the niche library.';
            cta?.classList.remove('hidden');
        }
    } catch (e) {
        gate?.classList.remove('hidden');
        workspace?.classList.add('hidden');
        const title = document.getElementById('nf-gate-title');
        const body = document.getElementById('nf-gate-body');
        if (title) title.textContent = 'Unavailable';
        if (body) body.textContent = e.message || 'Could not load Niche Finder.';
    }
}

async function loadNicheFinderFeed(opts = {}) {
    if (_nfAccess && !_nfAccess.can_browse) return;
    if (opts.reset) _nfPage = 1;
    const meta = document.getElementById('nf-feed-meta');
    const pager = document.getElementById('nf-pager');
    const pageLabel = document.getElementById('nf-page-label');
    const prevBtn = document.getElementById('nf-prev');
    const nextBtn = document.getElementById('nf-next');
    const results = document.getElementById('nf-results');
    try {
        if (meta) meta.textContent = 'Loading niche library…';
        if (results) results.innerHTML = '';
        const offset = (_nfPage - 1) * _NF_PAGE;
        const params = new URLSearchParams();
        params.set('sort', document.getElementById('nf-sort')?.value || 'recent_revenue');
        params.set('limit', String(_NF_PAGE));
        params.set('offset', String(offset));
        const minRecent = document.getElementById('nf-f-min-recent')?.value;
        const maxRecent = document.getElementById('nf-f-max-recent')?.value;
        const minSubs = document.getElementById('nf-f-min-subs')?.value;
        const maxSubs = document.getElementById('nf-f-max-subs')?.value;
        const minRev = document.getElementById('nf-f-min-rev')?.value;
        const q = document.getElementById('nf-f-q')?.value?.trim();
        if (minRecent) params.set('min_recent_avg', minRecent);
        if (maxRecent) params.set('max_recent_avg', maxRecent);
        if (minSubs) params.set('min_subscribers', minSubs);
        if (maxSubs) params.set('max_subscribers', maxSubs);
        if (minRev) params.set('min_recent_revenue', minRev);
        if (q) params.set('q', q);
        if (document.getElementById('nf-f-has-recent')?.checked) params.set('has_recent_avg', 'true');
        if (document.getElementById('nf-f-active')?.checked) params.set('active_recently', 'true');

        const res = await fetch(`/api/niche-finder/channels?${params.toString()}`);
        const data = await readJson(res, null);
        if (!res.ok) throw new Error(data?.detail || 'Failed to load niches');
        const channels = data.channels || [];
        _nfTotal = data.total || 0;
        const totalPages = Math.max(1, Math.ceil(_nfTotal / _NF_PAGE));
        if (_nfPage > totalPages) {
            _nfPage = totalPages;
            if (offset > 0 && channels.length === 0 && _nfTotal > 0) {
                return loadNicheFinderFeed();
            }
        }
        _renderNicheFinderHits(channels, { append: false });
        const from = _nfTotal ? offset + 1 : 0;
        const to = offset + channels.length;
        if (meta) {
            meta.textContent = _nfTotal
                ? `Showing ${from}–${to} of ${_nfTotal} niches`
                : 'Library is empty — run Add niches (admin) or wait for the daily cron.';
        }
        if (pager) pager.classList.toggle('hidden', _nfTotal <= _NF_PAGE);
        if (pageLabel) pageLabel.textContent = `Page ${_nfPage} of ${totalPages}`;
        if (prevBtn) prevBtn.disabled = _nfPage <= 1;
        if (nextBtn) nextBtn.disabled = _nfPage >= totalPages;
    } catch (e) {
        if (meta) meta.textContent = e.message || 'Failed to load niches';
    }
}

function nicheFinderNextPage() {
    const totalPages = Math.max(1, Math.ceil(_nfTotal / _NF_PAGE));
    if (_nfPage >= totalPages) return;
    _nfPage += 1;
    loadNicheFinderFeed();
    document.getElementById('page-niche-finder')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function nicheFinderPrevPage() {
    if (_nfPage <= 1) return;
    _nfPage -= 1;
    loadNicheFinderFeed();
    document.getElementById('page-niche-finder')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function loadMoreNicheFinder() {
    nicheFinderNextPage();
}

async function startNicheFinder() {
    if (!ensureAuth(startNicheFinder)) return;
    if (_nfAccess && !_nfAccess.can_run) {
        alert('Only admins can refresh the niche library.');
        return;
    }
    const btn = document.getElementById('nf-run-btn');
    const status = document.getElementById('nf-status');
    const keywords = (document.getElementById('nf-keywords')?.value || '')
        .split(/\n+/)
        .map(s => s.trim())
        .filter(Boolean);
    const minViews = parseInt(document.getElementById('nf-min-views')?.value || '0', 10) || 0;
    const maxSubs = parseInt(document.getElementById('nf-max-subs')?.value || '150000', 10) || 150000;

    if (_nfPollTimer) { clearInterval(_nfPollTimer); _nfPollTimer = null; }
    setLoading(btn, true);
    if (status) status.textContent = 'Starting library refresh…';

    try {
        const res = await fetch('/api/niche-finder/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                keywords,
                min_recent_avg_views: minViews,
                max_subscribers: maxSubs,
            }),
        });
        const data = await readJson(res, null);
        if (res.status === 409) {
            const detail = data?.detail;
            const resumeId = (typeof detail === 'object' && detail?.job_id) || null;
            const msg = (typeof detail === 'object' && detail?.message)
                || (typeof detail === 'string' ? detail : 'A scrape is already running.');
            if (resumeId) {
                if (status) status.textContent = 'Reconnecting to running scrape…';
                _resumeNicheFinderJob(resumeId);
                return;
            }
            throw new Error(msg);
        }
        if (!res.ok) {
            const msg = typeof data?.detail === 'string' ? data.detail : (data?.detail?.message || data?.message || 'Failed');
            throw new Error(msg);
        }
        const jobId = data.job_id;
        localStorage.setItem('nf_active_job_id', jobId);
        if (status) status.textContent = 'Searching YouTube…';
        _resumeNicheFinderJob(jobId);
    } catch (e) {
        if (status) status.textContent = '';
        alert('Library refresh failed: ' + e.message);
        setLoading(btn, false);
    }
}

function _resumeNicheFinderJob(jobId) {
    if (!jobId) return;
    localStorage.setItem('nf_active_job_id', jobId);
    const btn = document.getElementById('nf-run-btn');
    const cancelBtn = document.getElementById('nf-cancel-btn');
    const status = document.getElementById('nf-status');
    setLoading(btn, true);
    cancelBtn?.classList.remove('hidden');
    if (status && !status.textContent) status.textContent = 'Reconnecting to scrape…';
    if (_nfPollTimer) { clearInterval(_nfPollTimer); _nfPollTimer = null; }
    _nfPollTimer = setInterval(() => _pollNicheFinderJob(jobId), 2500);
    _pollNicheFinderJob(jobId);
}

function _nfClearActiveJobUi() {
    localStorage.removeItem('nf_active_job_id');
    if (_nfPollTimer) { clearInterval(_nfPollTimer); _nfPollTimer = null; }
    setLoading(document.getElementById('nf-run-btn'), false);
    document.getElementById('nf-cancel-btn')?.classList.add('hidden');
}

async function cancelNicheFinder() {
    if (!ensureAuth(cancelNicheFinder)) return;
    const status = document.getElementById('nf-status');
    const jobId = localStorage.getItem('nf_active_job_id');
    try {
        if (status) status.textContent = 'Cancelling…';
        let res;
        if (jobId) {
            res = await fetch(`/api/niche-finder/jobs/${jobId}/cancel`, { method: 'POST' });
        } else {
            res = await fetch('/api/niche-finder/jobs/cancel-running', { method: 'POST' });
        }
        // Always force-clear any zombie running rows
        await fetch('/api/niche-finder/jobs/cancel-running', { method: 'POST' });
        _nfClearActiveJobUi();
        if (status) status.textContent = 'Cancelled — you can start a new scrape.';
        if (!res.ok) {
            const data = await readJson(res, null);
            console.warn('cancel niche', data);
        }
    } catch (e) {
        _nfClearActiveJobUi();
        if (status) status.textContent = 'Cleared local lock — try Add niches again.';
        alert('Cancel request failed: ' + e.message + ' — UI unlocked anyway.');
    }
}

async function _pollNicheFinderJob(jobId) {
    const btn = document.getElementById('nf-run-btn');
    const cancelBtn = document.getElementById('nf-cancel-btn');
    const status = document.getElementById('nf-status');
    cancelBtn?.classList.remove('hidden');
    try {
        const res = await fetch(`/api/niche-finder/jobs/${jobId}`);
        const data = await readJson(res, null);
        if (res.status === 404) {
            _nfClearActiveJobUi();
            if (status) status.textContent = '';
            return;
        }
        if (!res.ok) throw new Error(data?.detail || 'Job failed');
        const last = (data.progress || []).slice(-1)[0];
        if (status && last?.msg) status.textContent = last.msg;

        if (data.status === 'completed') {
            _nfClearActiveJobUi();
            const n = data.channels_upserted || (data.hits || []).length;
            if (status) status.textContent = `Done — ${n} channel${n === 1 ? '' : 's'} saved to the library.`;
            await loadNicheFinderFeed();
        } else if (data.status === 'error' || data.status === 'cancelled') {
            _nfClearActiveJobUi();
            if (status) status.textContent = data.status === 'cancelled' ? 'Cancelled.' : '';
            if (data.status === 'error') {
                alert('Library refresh failed: ' + (data.error || 'Unknown error'));
            }
        }
        // Still running — keep polling; do not clear on transient blips
    } catch (e) {
        // Soft: keep polling through brief network hiccups after refresh
        if (status) status.textContent = 'Still running… reconnecting (' + (e.message || 'network') + ')';
    }
}

// ---------------------------------------------------------------------------
// Niche Intel (admin-only Shorts competitor packs)
// ---------------------------------------------------------------------------
let _niPollTimer = null;
let _niJobId = null;

function initNicheIntelPage() {
    if (!ensureAuth(initNicheIntelPage)) return;
    const admin = isAdminUser();
    document.getElementById('ni-gate')?.classList.toggle('hidden', admin);
    document.getElementById('ni-workspace')?.classList.toggle('hidden', !admin);
}

async function startNicheIntel() {
    if (!ensureAuth(startNicheIntel)) return;
    if (!isAdminUser()) {
        alert('Admin only.');
        return;
    }
    const niche = (document.getElementById('ni-niche')?.value || '').trim() || 'niche';
    const raw = document.getElementById('ni-channels')?.value || '';
    const channels = raw.split(/\n+/).map(s => s.trim()).filter(Boolean);
    if (!channels.length) {
        alert('Paste at least one channel URL.');
        return;
    }
    if (channels.length > 12) {
        alert('Max 12 channels per run.');
        return;
    }
    const videos = parseInt(document.getElementById('ni-videos')?.value || '10', 10) || 10;
    const frames = parseInt(document.getElementById('ni-frames')?.value || '8', 10) || 8;

    const btn = document.getElementById('ni-run-btn');
    const status = document.getElementById('ni-status');
    const dl = document.getElementById('ni-download');
    setLoading(btn, true);
    dl?.classList.add('hidden');
    if (status) status.textContent = 'Starting…';
    if (_niPollTimer) { clearInterval(_niPollTimer); _niPollTimer = null; }

    try {
        const res = await fetch('/api/niche-intel/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                niche,
                channels,
                videos_per_channel: videos,
                frames_per_video: frames,
            }),
        });
        const data = await readJson(res, null);
        if (!res.ok) throw new Error(data?.detail || 'Failed to start');
        _niJobId = data.job_id;
        if (status) status.textContent = 'Queued…';
        _niPollTimer = setInterval(() => _pollNicheIntelJob(_niJobId), 2000);
        await _pollNicheIntelJob(_niJobId);
    } catch (e) {
        setLoading(btn, false);
        if (status) status.textContent = '';
        alert(e.message || String(e));
    }
}

async function _pollNicheIntelJob(jobId) {
    const btn = document.getElementById('ni-run-btn');
    const status = document.getElementById('ni-status');
    const dl = document.getElementById('ni-download');
    try {
        const res = await fetch(`/api/niche-intel/jobs/${jobId}`);
        const data = await readJson(res, null);
        if (res.status === 404) {
            if (_niPollTimer) { clearInterval(_niPollTimer); _niPollTimer = null; }
            setLoading(btn, false);
            return;
        }
        if (!res.ok) throw new Error(data?.detail || 'Job failed');
        const lines = (data.progress || []).slice(-8).map(p => p.msg || '').filter(Boolean);
        if (status) status.textContent = lines.join('\n') || data.status;

        if (data.status === 'complete') {
            if (_niPollTimer) { clearInterval(_niPollTimer); _niPollTimer = null; }
            setLoading(btn, false);
            const n = data.channels_ok || 0;
            let msg = `Done — ${n} channel${n === 1 ? '' : 's'} packed.`;
            if ((data.errors || []).length) {
                msg += `\nPartial errors: ${data.errors.map(e => e.error).join('; ')}`;
            }
            if (status) status.textContent = msg;
            if (dl && data.zip_ready) {
                dl.href = `/api/niche-intel/jobs/${jobId}/download`;
                dl.classList.remove('hidden');
            }
        } else if (data.status === 'error') {
            if (_niPollTimer) { clearInterval(_niPollTimer); _niPollTimer = null; }
            setLoading(btn, false);
            if (status) status.textContent = data.error || 'Failed';
            alert('Niche Intel failed: ' + (data.error || 'Unknown error'));
        }
    } catch (e) {
        if (status) status.textContent = 'Still running… (' + (e.message || 'network') + ')';
    }
}

function _nfMoney(n) {
    const x = Number(n) || 0;
    if (x >= 1000) return '$' + (x / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
    return '$' + Math.round(x);
}

function _nfEsc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _renderNicheFinderHits(hits, opts = {}) {
    const root = document.getElementById('nf-results');
    if (!root) return;
    const append = !!opts.append;
    if (!hits.length && !append) {
        root.innerHTML = `<p style="color: var(--app-ink-3); font-size: 14px;">Building the niche library… Check back after the next refresh.</p>`;
        return;
    }
    const html = hits.map((h) => {
        const vids = (h.recent_videos || h.popular_videos || []).slice(0, 4);
        const thumbs = vids.map(v => {
            const ago = v.published_at ? _nfRelTime(v.published_at) : '';
            return `
            <a href="${_nfEsc(v.url)}" target="_blank" rel="noopener" title="${_nfEsc(v.title || '')}"
               style="display:block; width: 168px; flex: none; text-decoration:none;">
                <div style="position:relative; aspect-ratio:16/9; border-radius: 8px; overflow:hidden; background: var(--app-surface-2); border: 1px solid var(--app-border);">
                    ${v.thumbnail ? `<img src="${_nfEsc(v.thumbnail)}" alt="" loading="lazy" style="width:100%;height:100%;object-fit:cover;">` : ''}
                    <span class="cr-mono" style="position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.75);color:#fff;font-size:10px;padding:2px 5px;border-radius:4px;">${_nfDur(v.duration_sec)}</span>
                </div>
                <p style="font-size: 11px; color: var(--app-ink-2); margin-top: 6px; line-height: 1.35; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">
                    ${_nfEsc(v.title || '')}
                </p>
                <p class="cr-mono" style="font-size: 10px; color: var(--app-ink-3); margin-top: 2px;">
                    ${_nfFmt(v.view_count)} views${ago ? ' · ' + ago : ''}
                </p>
            </a>`;
        }).join('');

        const metric = (label, value) => `
            <div style="flex:1; min-width: 110px; background: var(--app-surface-2); border: 1px solid var(--app-border); border-radius: 10px; padding: 10px 12px;">
                <p class="cr-mono" style="font-size: 10px; color: var(--app-ink-3); text-transform: uppercase; letter-spacing: 0.04em;">${label}</p>
                <p style="font-family: var(--font-display); font-weight: 700; font-size: 18px; color: var(--app-ink); margin-top: 4px;">${value}</p>
            </div>`;

        const mon = h.likely_monetized
            ? `<span title="Likely monetized (≥1K subs)" style="color:#14B87A;font-weight:700;margin-left:4px;">$</span>`
            : '';
        const tag = h.source_keyword
            ? `<span class="cr-mono" style="font-size: 11px; color: var(--app-ink-3); background: var(--app-surface-2); border: 1px solid var(--app-border); border-radius: 99px; padding: 2px 8px;">${_nfEsc(h.source_keyword)}</span>`
            : '';

        return `
        <div class="cr-surface" style="padding: 18px 20px;">
            <div class="flex items-start gap-4" style="flex-wrap: wrap;">
                <a href="${_nfEsc(h.channel_url)}" target="_blank" rel="noopener" style="flex:none;">
                    <img src="${_nfEsc(h.avatar_url || '')}" alt="" width="48" height="48"
                         style="width:48px;height:48px;border-radius:50%;object-fit:cover;background:var(--app-surface-2);">
                </a>
                <div style="flex:1; min-width: 200px;">
                    <div class="flex items-center gap-2" style="flex-wrap:wrap;">
                        <a href="${_nfEsc(h.channel_url)}" target="_blank" rel="noopener"
                           style="font-family: var(--font-display); font-weight: 700; font-size: 18px; color: var(--app-ink); text-decoration:none;">
                            ${_nfEsc(h.channel_name || 'Channel')}${mon}
                        </a>
                        ${tag}
                        <span class="cr-mono" style="font-size: 11px; color: var(--accent); background: var(--accent-soft-dark); border: 1px solid var(--accent); border-radius: 99px; padding: 2px 8px;">
                            score ${_nfEsc(h.score)}
                        </span>
                        <span class="cr-mono" style="font-size: 11px; color: var(--app-ink-3);">
                            ${_nfFmt(h.subscriber_count)} subscribers
                        </span>
                    </div>
                    <p class="cr-mono" style="font-size: 12px; color: var(--success); margin-top: 6px;">
                        Recent est. ${_nfMoney(h.est_recent_monthly_revenue_usd || h.est_monthly_revenue_usd)}/mo
                        <span style="color: var(--app-ink-3);">· lifetime ${_nfMoney(h.est_monthly_revenue_usd)}/mo @ $4 RPM</span>
                        · recent avg ${_nfFmt(h.recent_avg_views)}
                        · ${_nfEsc(h.view_to_sub_ratio)}× v/sub
                    </p>
                </div>
            </div>
            <div class="flex gap-2 mt-4" style="flex-wrap: wrap;">
                ${metric('Recent Avg Views', _nfFmt(h.recent_avg_views))}
                ${metric('Days Since Start', h.days_since_start != null ? _nfEsc(h.days_since_start) : '—')}
                ${metric('Uploads', _nfFmt(h.video_count))}
                ${metric('Active 14d', h.videos_last_14d != null ? _nfEsc(h.videos_last_14d) : '—')}
            </div>
            <p class="cr-eyebrow" style="margin: 16px 0 8px;">Most recent videos</p>
            <div class="flex gap-3" style="overflow-x:auto; padding-bottom: 4px;">
                ${thumbs || '<span style="color:var(--app-ink-3);font-size:13px;">No long-form videos found</span>'}
            </div>
        </div>`;
    }).join('');
    if (append) root.insertAdjacentHTML('beforeend', html);
    else root.innerHTML = html;
}

function _nfRelTime(iso) {
    try {
        const d = new Date(iso);
        const days = Math.max(0, Math.round((Date.now() - d.getTime()) / 86400000));
        if (days <= 0) return 'today';
        if (days === 1) return '1 day ago';
        if (days < 30) return days + ' days ago';
        const mo = Math.round(days / 30);
        return mo === 1 ? '1 month ago' : mo + ' months ago';
    } catch (_) {
        return '';
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
        if (!res.ok) {
            const msg = typeof data.detail === 'string' ? data.detail : (data.detail?.message || 'Failed');
            if (res.status === 503) {
                showSoftPrompt(msg, 'Go to New video', () => navigateTo('pipeline'));
                return;
            }
            throw new Error(msg);
        }
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
// Resources (free YouTube sauce — account to download, no card/plan)
// ---------------------------------------------------------------------------
function _formatResourceDate(iso) {
    if (!iso) return '';
    const d = new Date(iso + 'T12:00:00');
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function _setResourcesNewBadges(hasNew) {
    for (const id of ['nav-tools-resources-new', 'nav-resources-new', 'nav-resources-new-mobile', 'resources-page-new']) {
        document.getElementById(id)?.classList.toggle('hidden', !hasNew);
    }
}

async function _fetchSauceCatalog() {
    // Prefer /api/sauce (ad blockers often swallow "/api/resources").
    // Fall back to static JSON so the page still renders if the API is blocked.
    const urls = ['/api/sauce', '/api/resources', '/static/sauce-catalog.json'];
    for (const url of urls) {
        try {
            const res = await _origFetch(url, { cache: 'no-store' });
            if (!res.ok) continue;
            const data = await readJson(res, null);
            if (data && Array.isArray(data.resources)) return data;
        } catch (_) { /* try next */ }
    }
    return null;
}

async function refreshResourcesNewBadge() {
    try {
        const data = await _fetchSauceCatalog();
        if (!data) return;
        _setResourcesNewBadges(!!data.has_new);
    } catch (_) {}
}

async function loadResourcesPage() {
    const list = document.getElementById('resources-list');
    if (!list) return;
    list.innerHTML = '<p style="font-size: 14px; color: var(--app-ink-3);">Loading resources…</p>';
    try {
        const data = await _fetchSauceCatalog();
        if (!data) {
            list.innerHTML = '<p style="font-size: 14px; color: var(--app-ink-3);">Could not load resources.</p>';
            return;
        }
        _setResourcesNewBadges(!!data.has_new);
        const items = data.resources || [];
        if (!items.length) {
            list.innerHTML = '<p style="font-size: 14px; color: var(--app-ink-3);">No resources yet — check back soon.</p>';
            return;
        }
        list.innerHTML = items.map(r => {
            const id = String(r.id || '').replace(/[^a-zA-Z0-9_-]/g, '');
            const cost = Number(r.credit_cost || 0);
            const paid = cost > 0;
            const unlocked = !!r.unlocked || !paid;
            let cta;
            if (!paid) {
                cta = `<button type="button" class="btn-primary" style="font-size: 14px;" data-resource-id="${id}" onclick="downloadResource(this.dataset.resourceId)">Download free</button>
                <p style="font-size: 12px; color: var(--app-ink-3); margin: 10px 0 0;">Account required · no card needed</p>`;
            } else if (unlocked) {
                cta = `<button type="button" class="btn-primary" style="font-size: 14px;" data-resource-id="${id}" onclick="unlockResource(this.dataset.resourceId)">Open guide</button>
                <p style="font-size: 12px; color: var(--app-ink-3); margin: 10px 0 0;">Unlocked · yours forever</p>`;
            } else {
                cta = `<button type="button" class="btn-primary" style="font-size: 14px;" data-resource-id="${id}" onclick="unlockResource(this.dataset.resourceId)">Unlock · ${cost} credits</button>
                <p style="font-size: 12px; color: var(--app-ink-3); margin: 10px 0 0;">One-time unlock · opens the private Google Doc</p>`;
            }
            return `
            <article class="cr-surface" style="padding: 20px; position: relative;">
                <div class="flex items-start justify-between gap-3" style="flex-wrap: wrap; margin-bottom: 8px;">
                    <div class="flex items-center gap-2" style="flex-wrap: wrap;">
                        <h3 style="font-family: var(--font-display); font-size: 20px; margin: 0; color: var(--app-ink);">${escapeHtml(r.title)}</h3>
                        ${r.is_new ? '<span class="cr-new-badge" style="position:static;">New</span>' : ''}
                        ${paid ? '<span class="cr-mono" style="font-size:11px;color:var(--accent);border:1px solid var(--accent);padding:2px 6px;border-radius:4px;">Pro</span>' : ''}
                    </div>
                    <span class="cr-mono" style="font-size: 12px; color: var(--app-ink-3);">${escapeHtml(_formatResourceDate(r.date))}</span>
                </div>
                <p style="font-size: 14px; color: var(--app-ink); margin: 0 0 6px; font-weight: 500;">${escapeHtml(r.tagline || '')}</p>
                <p style="font-size: 14px; color: var(--app-ink-2); margin: 0 0 16px;">${escapeHtml(r.description || '')}</p>
                ${cta}
            </article>`;
        }).join('');
    } catch (_) {
        list.innerHTML = '<p style="font-size: 14px; color: var(--app-ink-3);">Could not load resources.</p>';
    }
}

async function unlockResource(resourceId) {
    if (!ensureSignedIn(() => unlockResource(resourceId))) return;
    const id = String(resourceId || '').replace(/[^a-zA-Z0-9_-]/g, '');
    if (!id) {
        showSoftPrompt('Unlock failed. Try again.');
        return;
    }
    try {
        let res = await _origFetch(`/api/sauce/${encodeURIComponent(id)}/unlock`, { method: 'POST' });
        if (res.status === 404) {
            res = await _origFetch(`/api/resources/${encodeURIComponent(id)}/unlock`, { method: 'POST' });
        }
        const data = await readJson(res, {});
        if (res.status === 401 || res.status === 403) {
            showAuthModal();
            return;
        }
        if (res.status === 402) {
            const errMsg = typeof data.detail === 'string' ? data.detail : 'Not enough credits';
            const needMatch = String(errMsg).match(/Need\s+(\d+)/i);
            const need = needMatch ? parseInt(needMatch[1], 10) : 55;
            if (isPaidUser() && !isTrialUser()) {
                showCreditsNeededModal({ need, have: currentUser?.credits ?? 0, reason: 'credits' });
            } else if (isTrialUser()) {
                showTrialExhaustedModal();
            } else {
                showPricingModal({ reason: 'credits' });
            }
            try { track('resource_unlock_blocked', { resource_id: id, need }); } catch (_) {}
            return;
        }
        if (!res.ok) {
            showSoftPrompt((data && data.detail) || 'Unlock failed. Try again.');
            return;
        }
        const charged = Number(data.credits_charged || 0);
        if (charged > 0 && currentUser && typeof currentUser.credits === 'number') {
            currentUser.credits = Math.max(0, currentUser.credits - charged);
            updateAuthUI();
        }
        try {
            track('resource_unlocked', {
                resource_id: id,
                credits: charged,
                already_owned: !!data.already_owned,
                purchase_count: data.purchase_count || 0,
            });
        } catch (_) {}
        if (data.url) {
            window.open(data.url, '_blank', 'noopener');
        }
        try { loadResourcesPage(); } catch (_) {}
        refreshUserData();
    } catch (_) {
        showSoftPrompt('Unlock failed. Try again.');
    }
}

async function downloadResource(resourceId) {
    if (!ensureSignedIn(() => downloadResource(resourceId))) return;
    const id = String(resourceId || '').replace(/[^a-zA-Z0-9_-]/g, '');
    if (!id) {
        showSoftPrompt('Download failed. Try again.');
        return;
    }
    try {
        let res = await _origFetch(`/api/sauce/${encodeURIComponent(id)}/download`);
        if (res.status === 404) {
            res = await _origFetch(`/api/resources/${encodeURIComponent(id)}/download`);
        }
        if (res.status === 401 || res.status === 403) {
            showAuthModal();
            return;
        }
        if (!res.ok) {
            const err = await readJson(res, null);
            showSoftPrompt((err && err.detail) || 'Download failed. Try again.');
            return;
        }
        const blob = await res.blob();
        const cd = res.headers.get('Content-Disposition') || '';
        const match = /filename="?([^";]+)"?/i.exec(cd);
        const filename = (match && match[1]) || `${id}.txt`;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        try { track('resource_download', { resource_id: id }); } catch (_) {}
    } catch (_) {
        showSoftPrompt('Download failed. Try again.');
    }
}

// ---------------------------------------------------------------------------
// Recipe Brain
// ---------------------------------------------------------------------------
let _brainChatEnabled = false;
let _brainMessages = [];

async function initRecipeBrainPage() {
    _brainMessages = [];
    const log = document.getElementById('brain-chat-log');
    const input = document.getElementById('brain-chat-input');
    const sendBtn = document.getElementById('btn-brain-chat');
    try {
        const res = await fetch('/api/brain/starter', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        if (res.ok) {
            const data = await res.json();
            _brainChatEnabled = !!data.chat_enabled;
        }
    } catch (_) {
        _brainChatEnabled = false;
    }
    if (input) input.disabled = !_brainChatEnabled;
    if (sendBtn) sendBtn.disabled = !_brainChatEnabled;
    if (log) {
        log.style.color = 'var(--app-ink-3)';
        log.textContent = _brainChatEnabled
            ? 'Ask about niches, hooks, retention, or packaging.'
            : 'Chat unlocks when Recipe Brain is enabled. Until then, use the starter pack.';
    }
}

async function loadBrainStarter() {
    if (!ensureAuth(loadBrainStarter)) return;
    const btn = document.getElementById('btn-brain-starter');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/brain/starter', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Could not load starter pack'));
        const list = document.getElementById('brain-starter-list');
        list.innerHTML = '';
        (data.mistakes || []).forEach((m, i) => {
            const title = (typeof m === 'string') ? m.split(' — ')[0] : (m.title || '');
            const body = (typeof m === 'string')
                ? (m.includes(' — ') ? m.split(' — ').slice(1).join(' — ') : '')
                : (m.body || '');
            const row = document.createElement('article');
            row.className = 'brain-mistake';
            row.innerHTML = `
                <div class="brain-mistake-num">${String(i + 1).padStart(2, '0')}</div>
                <div>
                    <h4 class="brain-mistake-title">${escapeHtml(title)}</h4>
                    ${body ? `<p class="brain-mistake-body">${escapeHtml(body)}</p>` : ''}
                </div>`;
            list.appendChild(row);
        });
        list.classList.remove('hidden');
        _brainChatEnabled = !!data.chat_enabled;
        const input = document.getElementById('brain-chat-input');
        const sendBtn = document.getElementById('btn-brain-chat');
        if (input) input.disabled = !_brainChatEnabled;
        if (sendBtn) sendBtn.disabled = !_brainChatEnabled;
    } catch (e) {
        showSoftPrompt(e.message || 'Could not load starter pack.');
    } finally {
        setLoading(btn, false);
    }
}

async function sendBrainChat() {
    if (!_brainChatEnabled) {
        showSoftPrompt('Recipe Brain chat is Coming Soon.');
        return;
    }
    if (!ensureAuth(sendBrainChat)) return;
    const input = document.getElementById('brain-chat-input');
    const text = (input?.value || '').trim();
    if (!text) return;
    const log = document.getElementById('brain-chat-log');
    _brainMessages.push({ role: 'user', content: text });
    if (input) input.value = '';
    if (log) {
        log.style.color = 'var(--app-ink)';
        log.innerHTML = _brainMessages.map((m) => {
            const who = m.role === 'user' ? 'You' : 'Brain';
            return `<div style="margin-bottom:10px;"><strong>${who}:</strong> ${escapeHtml(m.content)}</div>`;
        }).join('') + '<div style="opacity:.6;">Thinking…</div>';
    }
    try {
        const res = await fetch('/api/brain/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: _brainMessages }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyApiError(data, 'Chat failed'));
        _brainMessages.push({ role: 'assistant', content: data.reply || '' });
        if (log) {
            log.innerHTML = _brainMessages.map((m) => {
                const who = m.role === 'user' ? 'You' : 'Brain';
                return `<div style="margin-bottom:10px;"><strong>${who}:</strong> ${escapeHtml(m.content)}</div>`;
            }).join('');
        }
    } catch (e) {
        _brainMessages.pop();
        showSoftPrompt(e.message || 'Chat failed.');
        if (log) {
            log.innerHTML = _brainMessages.length
                ? _brainMessages.map((m) => {
                    const who = m.role === 'user' ? 'You' : 'Brain';
                    return `<div style="margin-bottom:10px;"><strong>${who}:</strong> ${escapeHtml(m.content)}</div>`;
                }).join('')
                : 'Chat unlocks when Recipe Brain is enabled. Until then, use the starter pack.';
        }
    }
}

function escapeHtml(s) {
    return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
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
const RECIPE_LABELS = {
    animated_explainer: 'Animated Explainer',
    broll_cinematic: 'Cinematic B-Roll',
    broll_only: 'B-Roll Documentary',
    avatar_plus_broll: 'Avatar + Illustrations',
    storyboard_pack: 'Storyboard Pack',
    storyboard_assemble: 'Storyboard Assemble',
    storyboard_animate: 'Storyboard Video',
    cinematic: 'Cinematic',
    avatar: 'Avatar',
    documentary: 'Documentary',
};

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

async function loadHistory() {
    const list = document.getElementById('history-list');
    const btn = document.getElementById('btn-history-refresh');
    setLoading(btn, true);
    if (list) {
        list.style.opacity = '0.55';
        list.style.pointerEvents = 'none';
    }
    try {
        const res = await fetch('/api/videos');
        const data = await res.json();
        window._videoLibrary = data.videos || [];
        if (!data.videos || data.videos.length === 0) {
            list.className = '';
            list.innerHTML = '<p style="color: var(--app-ink-3); text-align: center; padding: 32px 0;">No videos yet. Finish a video in the pipeline and it will appear here.</p>';
            return;
        }
        list.className = 'video-grid';
        list.innerHTML = data.videos.map(v => renderVideoCard(v, data.retention_days)).join('');
    } catch {
        list.className = '';
        list.innerHTML = '<p style="color: var(--app-ink-3); text-align: center; padding: 32px 0;">Could not load your videos.</p>';
    } finally {
        setLoading(btn, false);
        if (list) {
            list.style.opacity = '';
            list.style.pointerEvents = '';
        }
    }
}

function renderVideoCard(v, retentionDays) {
    const date = new Date(v.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    const recipe = RECIPE_LABELS[v.recipe] || v.recipe || 'Video';
    const expiry = (typeof v.expires_in_days === 'number')
        ? `<span class="video-expiry" title="Videos are kept for ${retentionDays} days">Expires in ${v.expires_in_days}d</span>`
        : '';
    const thumb = v.thumbnail_url
        ? `<img src="${esc(v.thumbnail_url)}" alt="" class="video-thumb-img">`
        : `<div class="video-thumb-placeholder">🎬</div>`;
    const hasKit = v.description || (v.tags && v.tags.length) || (v.hashtags && v.hashtags.length);
    const kitBtn = hasKit
        ? `<button class="video-btn" onclick="toggleVideoKit(${v.id})">Upload kit</button>` : '';
    // Local /api/files URLs from ephemeral Fly/worker disks are already gone.
    const broken = !v.url || String(v.url).startsWith('/api/files/');
    const dlBtn = broken
        ? `<span class="video-btn" style="opacity:.55;cursor:not-allowed" title="File was lost before cloud upload finished — re-cook this video">Unavailable</span>`
        : `<a class="video-btn video-btn-primary" href="${esc(v.url)}" download="${esc(v.title)}.mp4" target="_blank" rel="noopener">Download</a>`;
    return `
    <div class="video-card" id="video-card-${v.id}">
        <div class="video-thumb">${thumb}<span class="video-badge">${esc(recipe)}</span></div>
        <div class="video-body">
            <div class="video-title" title="${esc(v.title)}">${esc(v.title)}</div>
            <div class="video-meta"><span>${date}</span>${expiry}</div>
            <div class="video-actions">
                ${dlBtn}
                ${kitBtn}
                <button class="video-btn video-btn-danger" onclick="deleteVideo(${v.id})" title="Delete">✕</button>
            </div>
            <div class="video-kit hidden" id="video-kit-${v.id}">${renderKitPanel(v)}</div>
        </div>
    </div>`;
}

function renderKitPanel(v) {
    const tags = (v.tags || []).join(', ');
    const hashtags = (v.hashtags || []).join(' ');
    return `
        <div class="kit-block">
            <div class="kit-block-head"><span>Description</span><button class="kit-copy" data-copy="desc-${v.id}" onclick="copyRaw(this)">Copy</button></div>
            <p class="kit-text" id="kit-desc-${v.id}">${esc(v.description) || '<em>No description</em>'}</p>
        </div>
        ${tags ? `<div class="kit-block"><div class="kit-block-head"><span>Tags</span><button class="kit-copy" data-copy="tags-${v.id}" onclick="copyRaw(this)">Copy</button></div><p class="kit-text" id="kit-tags-${v.id}">${esc(tags)}</p></div>` : ''}
        ${hashtags ? `<div class="kit-block"><div class="kit-block-head"><span>Hashtags</span><button class="kit-copy" data-copy="hash-${v.id}" onclick="copyRaw(this)">Copy</button></div><p class="kit-text" id="kit-hash-${v.id}">${esc(hashtags)}</p></div>` : ''}
    `;
}

function toggleVideoKit(id) {
    document.getElementById(`video-kit-${id}`)?.classList.toggle('hidden');
}

function copyRaw(btn) {
    const [kind, id] = (btn.dataset.copy || '').split('-');
    const target = document.getElementById(`kit-${kind}-${id}`);
    if (!target) return;
    navigator.clipboard.writeText(target.textContent).then(() => {
        const old = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = old; }, 1200);
    });
}

async function deleteVideo(id) {
    if (!confirm('Delete this video? This cannot be undone.')) return;
    try {
        const res = await fetch(`/api/videos/${id}`, { method: 'DELETE' });
        if (res.ok) document.getElementById(`video-card-${id}`)?.remove();
    } catch { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Settings + HeyGen integrations
// ---------------------------------------------------------------------------
function isAvatarRecipe() {
    return (state.nicheData?.recipe || state.niche) === 'avatar_plus_broll';
}

function isStoryboardRecipe() {
    return (state.nicheData?.recipe || state.niche) === 'storyboard_pack';
}

function renderHeygenStatus(status) {
    heygenConfigured = !!(status && status.configured);
    const chip = document.getElementById('heygen-status-chip');
    const connected = document.getElementById('heygen-connected-panel');
    const connect = document.getElementById('heygen-connect-panel');
    const last4 = document.getElementById('heygen-last4');
    if (chip) {
        chip.textContent = heygenConfigured ? `Connected · ••••${status.last4 || ''}` : 'Not connected';
        chip.style.color = heygenConfigured ? 'var(--success)' : 'var(--app-ink-3)';
    }
    if (connected) connected.classList.toggle('hidden', !heygenConfigured);
    if (connect) connect.classList.toggle('hidden', heygenConfigured);
    if (last4 && status?.last4) last4.textContent = `••••${status.last4}`;
}

function renderAtlasStatus(status, byokEnabled) {
    const wrap = document.getElementById('settings-atlas-byok');
    if (wrap) wrap.classList.toggle('hidden', !byokEnabled);
    if (!byokEnabled) return;
    const configured = !!(status && status.configured);
    const chip = document.getElementById('atlas-status-chip');
    const connected = document.getElementById('atlas-connected-panel');
    const connect = document.getElementById('atlas-connect-panel');
    const last4 = document.getElementById('atlas-last4');
    if (chip) {
        chip.textContent = configured ? `Connected · ••••${status.last4 || ''}` : 'Not connected';
        chip.style.color = configured ? 'var(--success)' : 'var(--app-ink-3)';
    }
    if (connected) connected.classList.toggle('hidden', !configured);
    if (connect) connect.classList.toggle('hidden', configured);
    if (last4 && status?.last4) last4.textContent = `••••${status.last4}`;
}

async function loadIntegrations() {
    if (!currentUser) return;
    try {
        const res = await fetch('/api/me/integrations');
        const data = await readJson(res, {});
        if (!res.ok) return;
        renderHeygenStatus(data.heygen || {});
        const byok = !!(data.byok_enabled || currentUser.byok_enabled);
        renderAtlasStatus(data.atlas || {}, byok);
    } catch { /* best-effort */ }
}

async function saveAtlasKey() {
    const statusEl = document.getElementById('atlas-integ-status');
    const key = document.getElementById('atlas-user-key')?.value?.trim() || '';
    if (!key) {
        if (statusEl) statusEl.textContent = 'Paste your Atlas API key first.';
        return;
    }
    if (statusEl) {
        statusEl.style.color = 'var(--app-ink-3)';
        statusEl.textContent = 'Testing with Atlas…';
    }
    try {
        const res = await fetch('/api/me/integrations/atlas', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key, test: true }),
        });
        const data = await readJson(res, {});
        if (!res.ok) {
            if (statusEl) {
                statusEl.style.color = '#f87171';
                statusEl.textContent = friendlyApiError(data, 'Could not save key');
            }
            return;
        }
        document.getElementById('atlas-user-key').value = '';
        renderAtlasStatus(data.atlas || { configured: true }, true);
        if (currentUser) currentUser.atlas_connected = true;
        if (statusEl) {
            statusEl.style.color = data.warning ? '#fbbf24' : 'var(--success)';
            statusEl.textContent = data.warning
                || 'Connected — voice & cooks bill your Atlas account.';
        }
        track('atlas_byok_saved', {});
    } catch (e) {
        if (statusEl) {
            statusEl.style.color = '#f87171';
            statusEl.textContent = 'Save failed: ' + e.message;
        }
    }
}

async function disconnectAtlas() {
    if (!confirm('Disconnect your Atlas key? Voice and cooks will need it again.')) return;
    try {
        await fetch('/api/me/integrations/atlas', { method: 'DELETE' });
        renderAtlasStatus({ configured: false, last4: '' }, true);
        if (currentUser) currentUser.atlas_connected = false;
        const statusEl = document.getElementById('atlas-integ-status');
        if (statusEl) statusEl.textContent = 'Disconnected.';
    } catch (e) {
        alert('Could not disconnect: ' + e.message);
    }
}

async function saveHeygenKey() {
    const statusEl = document.getElementById('heygen-integ-status');
    const key = document.getElementById('heygen-user-key')?.value?.trim() || '';
    if (!key) {
        if (statusEl) statusEl.textContent = 'Paste your HeyGen API key first.';
        return;
    }
    if (statusEl) statusEl.textContent = 'Testing with HeyGen…';
    try {
        const res = await fetch('/api/me/integrations/heygen', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key, test: true }),
        });
        const data = await readJson(res, {});
        if (!res.ok) {
            if (statusEl) statusEl.textContent = friendlyApiError(data, 'Could not save key');
            return;
        }
        document.getElementById('heygen-user-key').value = '';
        renderHeygenStatus(data.heygen || { configured: true });
        if (statusEl) statusEl.textContent = 'Connected — you can cook Avatar videos.';
        track('heygen_key_saved', {});
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Save failed: ' + e.message;
    }
}

async function disconnectHeygen() {
    if (!confirm('Disconnect your HeyGen key? Avatar cooks will need it again.')) return;
    try {
        await fetch('/api/me/integrations/heygen', { method: 'DELETE' });
        renderHeygenStatus({ configured: false, last4: '' });
        const statusEl = document.getElementById('heygen-integ-status');
        if (statusEl) statusEl.textContent = 'Disconnected.';
    } catch (e) {
        alert('Could not disconnect: ' + e.message);
    }
}

async function setupAvatarStep() {
    const vo = document.getElementById('step4-voiceover');
    const av = document.getElementById('step4-avatar');
    if (vo) vo.classList.add('hidden');
    if (av) av.classList.remove('hidden');

    await loadIntegrations();
    const needKey = document.getElementById('avatar-need-key');
    const picker = document.getElementById('avatar-picker-panel');
    if (!heygenConfigured) {
        needKey?.classList.remove('hidden');
        picker?.classList.add('hidden');
        return;
    }
    needKey?.classList.add('hidden');
    picker?.classList.remove('hidden');
    await Promise.all([loadHeygenAvatars(), loadHeygenVoices()]);
}

async function loadHeygenAvatars() {
    const grid = document.getElementById('heygen-avatars-grid');
    const loading = document.getElementById('heygen-avatars-loading');
    if (!grid) return;
    loading?.classList.remove('hidden');
    grid.innerHTML = '';
    try {
        const res = await fetch('/api/heygen/avatars');
        const data = await readJson(res, {});
        if (!res.ok) throw new Error(friendlyApiError(data, 'Failed to load avatars'));
        const avatars = data.avatars || [];
        if (!avatars.length) {
            grid.innerHTML = '<p style="font-size:14px;color:var(--app-ink-2);">No avatars returned. Paste an avatar ID below.</p>';
            return;
        }
        avatars.slice(0, 48).forEach(a => {
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'cr-surface heygen-avatar-card';
            card.style.cssText = 'padding:0;overflow:hidden;text-align:left;border:2px solid transparent;border-radius:var(--radius-card);cursor:pointer;';
            card.dataset.id = a.avatar_id;
            const img = a.preview_url
                ? `<img src="${esc(a.preview_url)}" alt="" style="width:100%;aspect-ratio:1;object-fit:cover;display:block;" loading="lazy">`
                : `<div style="aspect-ratio:1;background:var(--app-border);"></div>`;
            card.innerHTML = `${img}<div style="padding:8px 10px;"><div style="font-size:13px;font-weight:600;color:var(--app-ink);">${esc(a.avatar_name || a.avatar_id)}</div></div>`;
            card.addEventListener('click', () => selectHeygenAvatar(a));
            if (state.avatarId === a.avatar_id) card.style.borderColor = 'var(--accent)';
            grid.appendChild(card);
        });
    } catch (e) {
        grid.innerHTML = `<p style="font-size:14px;color:var(--app-ink-2);">${esc(e.message)}</p>`;
    } finally {
        loading?.classList.add('hidden');
    }
}

function selectHeygenAvatar(a) {
    state.avatarId = a.avatar_id || '';
    state.avatarName = a.avatar_name || a.avatar_id || '';
    const paste = document.getElementById('heygen-avatar-paste');
    if (paste) paste.value = state.avatarId;
    document.querySelectorAll('.heygen-avatar-card').forEach(c => {
        c.style.borderColor = c.dataset.id === state.avatarId ? 'var(--accent)' : 'transparent';
    });
    if (a.default_voice_id && !state.voiceId) {
        state.voiceId = a.default_voice_id;
        const vp = document.getElementById('heygen-voice-paste');
        if (vp) vp.value = state.voiceId;
        highlightHeygenVoice(state.voiceId);
    }
}

function onHeygenAvatarPaste(val) {
    state.avatarId = (val || '').trim();
    state.avatarName = state.avatarId;
    document.querySelectorAll('.heygen-avatar-card').forEach(c => {
        c.style.borderColor = c.dataset.id === state.avatarId ? 'var(--accent)' : 'transparent';
    });
}

async function loadHeygenVoices() {
    const list = document.getElementById('heygen-voices-list');
    const loading = document.getElementById('heygen-voices-loading');
    if (!list) return;
    loading?.classList.remove('hidden');
    list.innerHTML = '';
    try {
        const res = await fetch('/api/heygen/voices');
        const data = await readJson(res, {});
        if (!res.ok) throw new Error(friendlyApiError(data, 'Failed to load voices'));
        const voices = data.voices || [];
        if (!voices.length) {
            list.innerHTML = '<p style="font-size:14px;color:var(--app-ink-2);">No voices returned. Paste a voice ID below.</p>';
            return;
        }
        voices.slice(0, 80).forEach(v => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'heygen-voice-row cr-surface';
            row.dataset.id = v.voice_id;
            row.style.cssText = 'width:100%;display:flex;justify-content:space-between;align-items:center;padding:10px 12px;text-align:left;border:1px solid var(--app-border);border-radius:8px;cursor:pointer;background:transparent;';
            const meta = [v.language, v.gender].filter(Boolean).join(' · ');
            row.innerHTML = `<span style="font-size:14px;font-weight:600;color:var(--app-ink);">${esc(v.display_name || v.voice_id)}</span><span class="cr-mono" style="font-size:11px;color:var(--app-ink-3);">${esc(meta)}</span>`;
            row.addEventListener('click', () => selectHeygenVoice(v));
            list.appendChild(row);
        });
        if (state.voiceId) highlightHeygenVoice(state.voiceId);
    } catch (e) {
        list.innerHTML = `<p style="font-size:14px;color:var(--app-ink-2);">${esc(e.message)}</p>`;
    } finally {
        loading?.classList.add('hidden');
    }
}

function selectHeygenVoice(v) {
    state.voiceId = v.voice_id || '';
    state.heygenVoiceName = v.display_name || v.voice_id || '';
    const paste = document.getElementById('heygen-voice-paste');
    if (paste) paste.value = state.voiceId;
    highlightHeygenVoice(state.voiceId);
}

function onHeygenVoicePaste(val) {
    state.voiceId = (val || '').trim();
    state.heygenVoiceName = state.voiceId;
    highlightHeygenVoice(state.voiceId);
}

function highlightHeygenVoice(id) {
    document.querySelectorAll('.heygen-voice-row').forEach(r => {
        const on = r.dataset.id === id;
        r.style.borderColor = on ? 'var(--accent)' : 'var(--app-border)';
    });
}

async function loadSettingsFromServer() {
    // Ops-only: only admins can read platform key status
    if (!(currentUser && currentUser.is_admin)) return;
    try {
        const res = await _origFetch('/api/settings/keys');
        if (!res.ok) return;
        const data = await readJson(res, null);
        if (!data || typeof data !== 'object') return;
        Object.entries(data).forEach(([key, info]) => {
            const input = document.getElementById(`key-${key}`);
            if (input && info?.configured) input.placeholder = 'Configured (hidden)';
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
        const data = await readJson(res, {});
        if (!res.ok) {
            document.getElementById('settings-status').textContent =
                friendlyApiError(data, 'Save failed');
            return;
        }
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
        const data = await readJson(res, {});
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

function showSoftPrompt(message, actionLabel, actionFn) {
    // Non-scary in-app prompt instead of raw alert() for missing prerequisites
    let el = document.getElementById('soft-prompt');
    if (!el) {
        el = document.createElement('div');
        el.id = 'soft-prompt';
        el.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:280;max-width:440px;width:calc(100% - 32px);background:var(--app-surface);border:1px solid var(--app-border);border-radius:14px;padding:16px 18px;box-shadow:0 12px 40px rgba(0,0,0,.35);display:none;';
        document.body.appendChild(el);
    }
    el.innerHTML = `
        <p style="margin:0 0 12px;font-family:var(--font-body);font-size:14px;color:var(--app-ink);line-height:1.45;">${_esc(message)}</p>
        <div style="display:flex;gap:8px;justify-content:flex-end;">
            <button id="soft-prompt-dismiss" style="padding:8px 14px;border:1px solid var(--app-border);border-radius:8px;background:transparent;color:var(--app-ink-2);font-size:13px;cursor:pointer;">Got it</button>
            ${actionLabel ? `<button id="soft-prompt-action" class="btn-primary" style="font-size:13px;padding:8px 14px;">${_esc(actionLabel)}</button>` : ''}
        </div>
    `;
    el.style.display = 'block';
    document.getElementById('soft-prompt-dismiss').onclick = () => { el.style.display = 'none'; };
    const act = document.getElementById('soft-prompt-action');
    if (act && typeof actionFn === 'function') {
        act.onclick = () => { el.style.display = 'none'; actionFn(); };
    }
}

function friendlyApiError(data, fallback) {
    const d = data && data.detail;
    if (typeof d === 'string' && d.trim()) return d;
    if (Array.isArray(d) && d[0]?.msg) return d[0].msg;
    return fallback || 'Something went wrong. Please try again.';
}

function copyText(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const text = el.value || el.textContent;
    navigator.clipboard.writeText(text);
}

// ---------------------------------------------------------------------------
// Pipeline persistence (survives Stripe checkout redirect)
// ---------------------------------------------------------------------------
const PIPELINE_STORAGE_KEY = 'cr_pipeline_draft';

function persistPipelineState() {
    try {
        const draft = {
            step: state.step,
            niche: state.niche,
            nicheData: state.nicheData,
            title: state.title,
            script: state.script,
            voice: state.voice,
            targetMinutes: state.targetMinutes,
            voiceoverPath: state.voiceoverPath,
            voiceoverUrl: state.voiceoverUrl,
            thumbnailPath: state.thumbnailPath,
            thumbnailUrl: state.thumbnailUrl,
            voiceMode: state.voiceMode,
            uploadedVoPath: state.uploadedVoPath,
            savedAt: Date.now(),
        };
        localStorage.setItem(PIPELINE_STORAGE_KEY, JSON.stringify(draft));
    } catch (_) {}
}

function clearPipelineDraft() {
    try { localStorage.removeItem(PIPELINE_STORAGE_KEY); } catch (_) {}
}

function restorePipelineState() {
    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(PIPELINE_STORAGE_KEY) || 'null'); } catch (_) { draft = null; }
    if (!draft || !draft.savedAt) return false;
    // Expire after 24h
    if (Date.now() - draft.savedAt > 24 * 60 * 60 * 1000) {
        clearPipelineDraft();
        return false;
    }
    Object.assign(state, {
        step: draft.step || 1,
        niche: draft.niche || null,
        nicheData: draft.nicheData || null,
        title: draft.title || '',
        script: draft.script || '',
        voice: draft.voice || 'leo',
        targetMinutes: draft.targetMinutes || 8,
        voiceoverPath: draft.voiceoverPath || '',
        voiceoverUrl: draft.voiceoverUrl || '',
        thumbnailPath: draft.thumbnailPath || '',
        thumbnailUrl: draft.thumbnailUrl || '',
        voiceMode: draft.voiceMode || 'generate',
        uploadedVoPath: draft.uploadedVoPath || '',
    });

    // Rehydrate UI fields
    const titleEl = document.getElementById('custom-title');
    if (titleEl && state.title) titleEl.value = state.title;
    const scriptEl = document.getElementById('script-editor');
    if (scriptEl && state.script) {
        scriptEl.value = state.script;
        updateWordCount();
    }
    const slider = document.getElementById('target-minutes');
    if (slider) {
        slider.value = state.targetMinutes;
        const label = document.getElementById('target-minutes-label');
        if (label) label.textContent = state.targetMinutes + ' min';
    }
    if (state.thumbnailUrl) {
        // Will re-render when they land on step 5/6
    }
    if (state.step >= 2) updateNextBtn2();
    goToStep(Math.min(Math.max(state.step, 1), 6));
    if (state.step === 6) populateBuildSummary();
    return true;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
async function checkAuth() {
    try {
        const res = await _origFetch('/api/auth/me');
        const data = await readJson(res, null);
        if (data?.user) {
            currentUser = data.user;
            updateAuthUI();
            loadIntegrations();
            maybeShowUserNotices(data.notices || []);
        }
    } catch (e) {
        console.error('Auth check failed:', e);
    } finally {
        authReady = true;
        startCreditsPolling();
    }
}

async function refreshUserData() {
    try {
        const res = await _origFetch('/api/auth/me');
        const data = await readJson(res, null);
        if (data?.user) {
            currentUser = data.user;
            updateAuthUI();
            loadIntegrations();
            maybeShowUserNotices(data.notices || []);
        }
    } catch (e) {
        console.error('Refresh user data failed:', e);
    }
}

let _pendingNotice = null;
function maybeShowUserNotices(notices) {
    if (!Array.isArray(notices) || !notices.length) return;
    // Prefer credit refunds first
    const sorted = [...notices].sort((a, b) => {
        const score = (n) => (n.kind === 'credit_refund' ? 0 : 1);
        return score(a) - score(b);
    });
    const next = sorted[0];
    if (!next || _pendingNotice?.id === next.id) return;
    _pendingNotice = next;
    const overlay = document.getElementById('credit-notice-overlay');
    const title = document.getElementById('credit-notice-title');
    const body = document.getElementById('credit-notice-body');
    if (title) title.textContent = next.title || 'Account update';
    if (body) body.textContent = next.body || '';
    if (overlay) {
        overlay.classList.remove('hidden');
        overlay.style.display = 'flex';
    }
    track('user_notice_shown', { kind: next.kind || 'info', notice_id: next.id });
}

async function dismissCreditNotice() {
    const overlay = document.getElementById('credit-notice-overlay');
    if (overlay) {
        overlay.classList.add('hidden');
        overlay.style.display = 'none';
    }
    const notice = _pendingNotice;
    _pendingNotice = null;
    if (notice?.id) {
        try {
            await _origFetch(`/api/notices/${notice.id}/ack`, { method: 'POST' });
        } catch (_) { /* ignore */ }
    }
    await refreshUserData();
}

function startCreditsPolling() {
    if (window.__crCreditsPoll) return;
    window.__crCreditsPoll = setInterval(() => {
        if (currentUser) refreshUserData();
    }, 45000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && currentUser) refreshUserData();
    });
    window.addEventListener('focus', () => {
        if (currentUser) refreshUserData();
    });
}

function updateAuthUI() {
    const loginBtn = document.getElementById('btn-login');
    const userBtn = document.getElementById('btn-user-menu');
    const creditsDisplay = document.getElementById('credits-display');

    const navSettings = document.getElementById('nav-settings');
    const navSettingsMobile = document.getElementById('nav-settings-mobile');
    const signedIn = !!currentUser;
    navSettings?.classList.toggle('hidden', !signedIn);
    navSettingsMobile?.classList.toggle('hidden', !signedIn);
    const isAdmin = !!(currentUser && currentUser.is_admin);
    document.getElementById('nav-niche-intel')?.classList.toggle('hidden', !isAdmin);
    document.getElementById('nav-niche-intel-mobile')?.classList.toggle('hidden', !isAdmin);
    if (!signedIn && state.page === 'settings') {
        navigateTo('pipeline');
    }

    const navUpgrade = document.getElementById('nav-upgrade-btn');
    const cookingUpgrade = document.getElementById('cooking-upgrade-btn');

    if (currentUser) {
        loginBtn.classList.add('hidden');
        userBtn.classList.remove('hidden');
        userBtn.textContent = currentUser.email[0].toUpperCase();
        document.getElementById('user-email-display').textContent = currentUser.email;
        const planLabels = { free: 'Free', starter: 'Starter', daily: 'Daily', pro: 'Pro', starter_trial: 'Starter (trial)', daily_trial: 'Daily (trial)' };
        document.getElementById('user-plan-display').textContent = planLabels[currentUser.plan] || currentUser.plan;
        if (isTrialUser()) {
            document.getElementById('credits-count').textContent = currentUser.credits + ' of ' + trialCredits() + ' trial';
            document.getElementById('credits-plan').textContent = 'videos';
        } else {
            document.getElementById('credits-count').textContent = currentUser.credits + ' credits';
            document.getElementById('credits-plan').textContent = currentUser.plan === 'free' ? '' : (planLabels[currentUser.plan] || '').toLowerCase();
        }
        creditsDisplay.classList.remove('hidden');
        // Show Upgrade for free users AND trial users (so they can convert early)
        const showUpgrade = (!isPaidUser() || isTrialUser()) && !currentUser.is_admin;
        if (navUpgrade) navUpgrade.classList.toggle('hidden', !showUpgrade);
        if (cookingUpgrade) cookingUpgrade.classList.toggle('hidden', !showUpgrade);
        applyLengthSliderLimits();
        const billingBtn = document.getElementById('menu-billing-btn');
        if (billingBtn) billingBtn.classList.remove('hidden');
        // Keep analytics person properties in sync
        try {
            window.posthog?.identify(String(currentUser.id), {
                email: currentUser.email,
                plan: currentUser.plan,
                credits: currentUser.credits,
                trial_used: !!currentUser.trial_used,
            });
            window.Sentry?.setUser({ id: String(currentUser.id), email: currentUser.email });
        } catch (_) {}
        try { syncAdminChannelUI(); } catch (_) {}
    } else {
        loginBtn.classList.remove('hidden');
        userBtn.classList.add('hidden');
        const cc = document.getElementById('credits-count');
        const cp = document.getElementById('credits-plan');
        if (cc) cc.textContent = trialCredits() + ' free';
        if (cp) cp.textContent = 'trial';
        creditsDisplay.classList.remove('hidden');
        if (navUpgrade) navUpgrade.classList.add('hidden');
        if (cookingUpgrade) cookingUpgrade.classList.add('hidden');
        try { window.Sentry?.setUser(null); } catch (_) {}
        try { syncAdminChannelUI(); } catch (_) {}
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
    // Never nag an already-signed-in user (guards against stray background 401s).
    if (currentUser) return;
    const modal = document.getElementById('auth-modal');
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
    document.getElementById('auth-step-email')?.classList.remove('hidden');
    document.getElementById('auth-step-code')?.classList.add('hidden');
    const email = document.getElementById('auth-email');
    const code = document.getElementById('auth-code');
    if (email) email.value = '';
    if (code) code.value = '';
    document.getElementById('auth-email-error')?.classList.add('hidden');
    document.getElementById('auth-code-error')?.classList.add('hidden');
    setTimeout(() => email?.focus(), 100);
}

function hideAuthModal() {
    const modal = document.getElementById('auth-modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.style.display = 'none';
    pendingAuthAction = null;
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
        loadNiches();
        try { window.posthog?.identify(String(currentUser.id), { email: currentUser.email, plan: currentUser.plan }); } catch (_) {}
        hideAuthModal();
        cookingManager.restore();
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
    loadNiches();
    document.getElementById('user-menu').classList.add('hidden');
}


// ---------------------------------------------------------------------------
// Billing page
// ---------------------------------------------------------------------------
function loadBillingPage() {
    if (!currentUser) return;
    const planLabels = { free: 'Free', starter: 'Starter', daily: 'Daily', pro: 'Pro', starter_trial: 'Starter (trial)', daily_trial: 'Daily (trial)' };
    document.getElementById('billing-plan-name').textContent = planLabels[currentUser.plan] || currentUser.plan;
    if (isTrialUser()) {
        document.getElementById('billing-credits').textContent = currentUser.credits + ' of ' + trialCredits() + ' trial videos';
    } else {
        document.getElementById('billing-credits').textContent = currentUser.credits;
    }

    const paid = isPaidUser();
    const trial = isTrialUser();
    const topupSec = document.getElementById('billing-topup-section');
    const manageSec = document.getElementById('billing-manage-section');
    const upgradeSec = document.getElementById('billing-upgrade-section');
    const trialSec = document.getElementById('billing-trial-section');

    if (paid && !trial) {
        topupSec.classList.remove('hidden');
        manageSec.classList.remove('hidden');
        upgradeSec.style.display = 'none';
        if (trialSec) trialSec.style.display = 'none';
    } else if (trial) {
        topupSec.classList.add('hidden');
        manageSec.classList.remove('hidden');
        upgradeSec.style.display = 'none';
        if (trialSec) trialSec.style.display = '';
    } else {
        topupSec.classList.add('hidden');
        manageSec.classList.add('hidden');
        upgradeSec.style.display = '';
        if (trialSec) trialSec.style.display = 'none';
        // Returning free users who already used a trial
        const usedTrial = !!currentUser.trial_used;
        const title = document.getElementById('billing-upgrade-title');
        const desc = document.getElementById('billing-upgrade-desc');
        const btn = document.getElementById('billing-upgrade-btn');
        if (usedTrial) {
            if (title) title.textContent = 'Subscribe to keep creating';
            if (desc) desc.textContent = 'Your free trial was already used. Choose a plan to continue — billed immediately.';
            if (btn) btn.textContent = 'Choose a plan';
        } else {
            if (title) title.textContent = 'Start your free trial';
            if (desc) desc.textContent = '7 days free, then your chosen plan begins. Cancel anytime.';
            if (btn) btn.textContent = 'Choose a plan';
        }
    }
}

async function openStripePortal() {
    try {
        const res = await fetch('/api/billing/portal', { method: 'POST' });
        let data;
        try { data = await res.json(); } catch (_) { data = {}; }
        if (res.ok && data.url) {
            window.location.href = data.url;
        } else {
            alert(data.detail || 'Could not open billing portal.');
        }
    } catch (e) {
        alert('Could not connect to billing. Please try again.');
    }
}

// ---------------------------------------------------------------------------
// Storyboard Pack — Cast studio → Story → live board
// ---------------------------------------------------------------------------
let _sbPollTimer = null;
let _sbJobId = null;
let _sbAssembleJobId = null;
let _sbAssembleStagingId = '';
let _sbAssembleFiles = [];
let _sbAssemblePollTimer = null;
let _sbThumbPath = '';
let _sbThumbUrl = '';
let _sbThumbRefs = [];
let _sbCast = [];
let _sbSelectedBeatIndex = null;
let _sbPackMode = 'full'; // preview | full
let _sbLastEpisodePayload = null;
let _sbBeats = [];
let _sbPackStatus = ''; // queued | running | complete | error | …
let _sbZipReady = false;
/** Contiguous cook range (beat indexes). null/null = all scenes. */
let _sbRangeFrom = null;
let _sbRangeTo = null;
let _sbDragSelecting = false;
let _sbDragAnchor = null;
let _sbDragPointerId = null;
let _sbDragStartX = 0;
let _sbDragStartY = 0;
let _sbDragShift = false;
let _sbVisualStyle = 'pixar_lite';
let _sbTemplate = '';
let _sbStyleOptions = [
    { id: 'pixar_lite', label: '3D Pixar-lite' },
    { id: 'anime_2d', label: '2D anime' },
    { id: 'storybook_watercolor', label: 'Storybook watercolor' },
    { id: 'comic_cartoon', label: 'Comic cartoon' },
    { id: 'semi_realistic', label: 'Semi-realistic 3D' },
];
let _sbFamilyTemplateCast = null;

function _sbDialogueMode() {
    const el = document.querySelector('input[name="sb-dialogue-mode"]:checked');
    return (el?.value || 'generate').trim();
}

function _sbCastHasLook() {
    return (_sbCast || []).some(c => c.included !== false && (c.portrait_url || c.sheet_url));
}

function _sbBeatHasStill(b) {
    if (!b || typeof b !== 'object') return false;
    return !!(b.image_url || b.image_path || b.still_url || b.still_path);
}

function _sbHasCookRange() {
    return _sbRangeFrom != null && _sbRangeTo != null;
}

function _sbBeatDurationSec(b) {
    const n = Number(b?.target_sec);
    return Number.isFinite(n) && n > 0 ? n : 8;
}

function _sbBeatsDurationMinutes(beats) {
    const rows = Array.isArray(beats) ? beats : [];
    if (!rows.length) return 0;
    return rows.reduce((sum, b) => sum + _sbBeatDurationSec(b), 0) / 60;
}

/** On-site Seedance cook hard cap (pack/stills may be longer). */
function _sbCookMaxMinutes() {
    return Number(_featureFlags.storyboard_cook_max_minutes || 8);
}

function _sbPackMaxMinutes() {
    if (isAdminUser() || hasFullLengthAccess()) {
        return Number(_featureFlags.storyboard_pack_max_minutes || 25);
    }
    return Number(_featureFlags.storyboard_trial_pack_max_minutes || 8);
}

function _sbAnimateCreditsFlat() {
    return Math.max(0, Number(_featureFlags.storyboard_animate_credits_flat || 12));
}

function _sbRequireAccess() {
    if (!currentUser) {
        showAuthModal();
        return false;
    }
    if (isAdminUser()) return true;
    if (!isPaidUser()) {
        showPricingModal({ reason: 'storyboard' });
        return false;
    }
    return true;
}

function _sbHandleBillingError(res, data, { needFallback = 1 } = {}) {
    const errMsg = typeof data?.detail === 'string'
        ? data.detail
        : (data?.detail?.message || data?.detail || 'Something went wrong');
    if (res.status === 401) {
        showAuthModal();
        return true;
    }
    if (res.status === 402) {
        if (isTrialUser() && /plan|paid|trial|unlock|longer|cook/i.test(String(errMsg))) {
            if (typeof endTrialNow === 'function') {
                // Soft prompt toward converting trial
                showSoftPrompt(String(errMsg), 'Start plan now', () => {
                    try { endTrialNow(); } catch (_) { showPricingModal({ reason: 'storyboard' }); }
                });
            } else {
                showPricingModal({ reason: 'storyboard' });
            }
            return true;
        }
        if (isPaidUser() && !isTrialUser()) {
            const needMatch = String(errMsg).match(/Need\s+(\d+)/i);
            const need = needMatch ? parseInt(needMatch[1], 10) : needFallback;
            showCreditsNeededModal({ need, have: currentUser?.credits ?? 0, reason: 'credits' });
            return true;
        }
        if (isTrialUser()) {
            showTrialExhaustedModal();
            return true;
        }
        showPricingModal({ reason: 'storyboard' });
        return true;
    }
    return false;
}

function _sbPackCreditEstimate(minutes, packMode = 'full') {
    if (isTrialUser() && !isAdminUser()) return 0;
    let mins = Number(minutes) || 8;
    if (packMode === 'preview') mins = Math.min(mins, 1.2);
    return Math.max(1, Math.ceil(mins / 2));
}

async function _sbSyncPackCostUI() {
    const blurb = document.getElementById('sb-pack-cost-blurb');
    if (!blurb) return;
    const slider = document.getElementById('sb-minutes');
    const mins = Number(slider?.value || 8);
    const maxMins = _sbPackMaxMinutes();
    try {
        const res = await fetch(`/api/storyboard/pack-cost?minutes=${encodeURIComponent(mins)}&pack_mode=full`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            blurb.textContent = `Packs up to ${maxMins} min. On-site cook is ${_sbAnimateCreditsFlat()} credits (paid plans).`;
            return;
        }
        const c = Number(data.credits || 0);
        const trialLeft = data.trial_packs_remaining;
        if (data.is_trial) {
            const left = trialLeft == null ? '' : ` · ${trialLeft} of ${data.trial_pack_limit} free packs left`;
            blurb.textContent = `Trial: free packs up to ${data.trial_pack_max_minutes || 8} min${left}. On-site cook unlocks when you start your plan.`;
        } else {
            blurb.textContent = c > 0
                ? `This pack: ~${c} credit${c === 1 ? '' : 's'} for ~${data.minutes || mins} min (max ${data.pack_max_minutes || maxMins}). On-site cook: ${_sbAnimateCreditsFlat()} credits.`
                : `Packs up to ${data.pack_max_minutes || maxMins} min. On-site cook: ${_sbAnimateCreditsFlat()} credits.`;
        }
    } catch (_) {
        const c = _sbPackCreditEstimate(mins);
        if (isTrialUser() && !isAdminUser()) {
            blurb.textContent = `Trial: free packs up to ${maxMins} min (max ${_featureFlags.storyboard_trial_pack_limit || 2}). On-site cook needs a paid plan.`;
        } else {
            blurb.textContent = `This pack: ~${c} credit${c === 1 ? '' : 's'}. On-site cook: ${_sbAnimateCreditsFlat()} credits.`;
        }
    }
}

function _sbClipBeatsToCookCap(beats, maxMinutes) {
    const rows = [...(Array.isArray(beats) ? beats : [])].sort((a, b) => (a.index || 0) - (b.index || 0));
    const budget = Math.max(30, (maxMinutes || 8) * 60);
    const out = [];
    let used = 0;
    for (const b of rows) {
        const sec = _sbBeatDurationSec(b);
        if (out.length && used + sec > budget + 0.25) break;
        out.push(b);
        used += sec;
        if (used >= budget) break;
    }
    return out.length ? out : rows.slice(0, 1);
}

/** Contiguous beat rows in the current cook range (or all beats). */
function _sbCookBeats() {
    const beats = Array.isArray(_sbBeats) ? [..._sbBeats].sort((a, b) => (a.index || 0) - (b.index || 0)) : [];
    if (!_sbHasCookRange()) return beats;
    const lo = Math.min(_sbRangeFrom, _sbRangeTo);
    const hi = Math.max(_sbRangeFrom, _sbRangeTo);
    return beats.filter(b => (b.index || 0) >= lo && (b.index || 0) <= hi);
}

/** Scenes that will actually be sent to on-site cook (respects 8-min cap). */
function _sbCookBeatsEffective() {
    const selected = _sbCookBeats();
    const cap = _sbCookMaxMinutes();
    if (_sbHasCookRange()) return selected;
    if (_sbBeatsDurationMinutes(selected) <= cap + 0.05) return selected;
    return _sbClipBeatsToCookCap(selected, cap);
}

function _sbIsInCookRange(idx) {
    if (!_sbHasCookRange()) return false;
    const lo = Math.min(_sbRangeFrom, _sbRangeTo);
    const hi = Math.max(_sbRangeFrom, _sbRangeTo);
    return idx >= lo && idx <= hi;
}

function _sbSetCookRange(a, b) {
    const beats = Array.isArray(_sbBeats) ? _sbBeats : [];
    if (!beats.length) {
        _sbRangeFrom = null;
        _sbRangeTo = null;
        return;
    }
    const indexes = beats.map(x => +x.index).filter(n => Number.isFinite(n));
    const minI = Math.min(...indexes);
    const maxI = Math.max(...indexes);
    let lo = Math.min(+a, +b);
    let hi = Math.max(+a, +b);
    lo = Math.max(minI, Math.min(maxI, lo));
    hi = Math.max(minI, Math.min(maxI, hi));
    // Selecting the entire board clears the special range (cook all).
    if (lo === minI && hi === maxI) {
        _sbRangeFrom = null;
        _sbRangeTo = null;
    } else {
        _sbRangeFrom = lo;
        _sbRangeTo = hi;
    }
}

function _sbClearCookRange() {
    _sbRangeFrom = null;
    _sbRangeTo = null;
    _sbSyncRangeBar();
    renderStoryboardBoard(_sbBeats);
}

function _sbSelectAllCookRange() {
    _sbClearCookRange();
}

function _sbSyncRangeBar() {
    const bar = document.getElementById('sb-range-bar');
    const label = document.getElementById('sb-range-label');
    const clearBtn = document.getElementById('btn-sb-range-clear');
    const cookBtn = document.getElementById('btn-sb-range-cook');
    const beats = Array.isArray(_sbBeats) ? _sbBeats : [];
    if (!bar) return;
    if (!beats.length) {
        bar.classList.add('hidden');
        return;
    }
    bar.classList.remove('hidden');
    const selected = _sbCookBeats();
    const effective = _sbCookBeatsEffective();
    const selectedMins = _sbBeatsDurationMinutes(selected);
    const effMins = _sbBeatsDurationMinutes(effective);
    const cap = _sbCookMaxMinutes();
    const ready = effective.length && effective.every(_sbBeatHasStill);
    const stretchTooLong = _sbHasCookRange() && selectedMins > cap + 0.05;
    if (_sbHasCookRange()) {
        const lo = Math.min(_sbRangeFrom, _sbRangeTo);
        const hi = Math.max(_sbRangeFrom, _sbRangeTo);
        if (label) {
            label.textContent = stretchTooLong
                ? `Scenes ${String(lo).padStart(3, '0')}–${String(hi).padStart(3, '0')} · ~${selectedMins.toFixed(1)} min (over ${cap} min cook cap — shorten stretch)`
                : `Scenes ${String(lo).padStart(3, '0')}–${String(hi).padStart(3, '0')} · ${selected.length} scenes · ~${selectedMins.toFixed(1)} min`;
        }
        clearBtn?.classList.remove('hidden');
        cookBtn?.classList.toggle('hidden', !ready || stretchTooLong);
    } else {
        const packMins = _sbBeatsDurationMinutes(beats);
        if (label) {
            if (packMins > cap + 0.05 && effective.length) {
                const lo = effective[0]?.index;
                const hi = effective[effective.length - 1]?.index;
                label.textContent = `Pack ~${packMins.toFixed(0)} min · on-site cook first ~${effMins.toFixed(0)} min (scenes ${String(lo).padStart(3, '0')}–${String(hi).padStart(3, '0')})`;
            } else {
                label.textContent = `All ${beats.length} scenes · ~${packMins.toFixed(1)} min · drag to cook a shorter stretch`;
            }
        }
        clearBtn?.classList.add('hidden');
        cookBtn?.classList.add('hidden');
    }
}

/** True when cook-target scenes (range or all) have stills. Full-board needs pack finished. */
function _sbBoardCookReady() {
    if (!_sbJobId) return false;
    if (!_sbHasCookRange() && !_sbZipReady && _sbPackStatus !== 'complete') return false;
    const beats = _sbCookBeatsEffective();
    if (!beats.length) return false;
    if (_sbHasCookRange() && _sbBeatsDurationMinutes(_sbCookBeats()) > _sbCookMaxMinutes() + 0.05) return false;
    return beats.every(_sbBeatHasStill);
}

function _sbBoardCookBlockReason() {
    if (!_sbJobId) return 'Generate your storyboard first.';
    if (!_sbHasCookRange() && !_sbZipReady && _sbPackStatus !== 'complete') {
        return 'Wait until every scene still is ready before cooking — or drag a ready stretch.';
    }
    const selected = _sbCookBeats();
    const cap = _sbCookMaxMinutes();
    if (_sbHasCookRange() && _sbBeatsDurationMinutes(selected) > cap + 0.05) {
        return `On-site cook is limited to ${cap} minutes. Shorten your stretch.`;
    }
    const beats = _sbCookBeatsEffective();
    if (!beats.length) return 'Wait until your storyboard scenes are ready.';
    const missing = beats.filter(b => !_sbBeatHasStill(b)).map(b => String(b.index).padStart(3, '0'));
    if (missing.length) {
        return `Still waiting on scene${missing.length === 1 ? '' : 's'} ${missing.join(', ')}.`;
    }
    return '';
}

function _sbSyncBoardCookControls() {
    const ready = _sbBoardCookReady();
    const reason = _sbBoardCookBlockReason();
    const next = document.getElementById('btn-sb-board-next-assemble');
    if (next) {
        next.disabled = !ready;
        next.title = ready ? 'Cook your video' : (reason || 'Storyboard not ready');
        next.setAttribute('aria-disabled', ready ? 'false' : 'true');
        next.classList.toggle('is-disabled', !ready);
    }
    const gotoAssemble = document.getElementById('btn-sb-goto-assemble');
    if (gotoAssemble && !gotoAssemble.classList.contains('hidden')) {
        gotoAssemble.disabled = !ready;
        gotoAssemble.title = ready ? 'Cook your video' : (reason || 'Storyboard not ready');
    }
}

function goToStoryboardCook() {
    const reason = _sbBoardCookBlockReason();
    if (reason) {
        alert(reason);
        return;
    }
    goToStep('sb-assemble');
}

function _sbNicheThumbStyle() {
    return (
        state.nicheData?.thumbnail_style
        || 'animated story characters, cinematic lighting, bold emotional moment, clean 16:9'
    );
}

function _sbSlugId(name) {
    const base = String(name || 'character').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'character';
    const used = new Set((_sbCast || []).map(c => c.id));
    let cid = base.slice(0, 32);
    let n = 2;
    while (used.has(cid)) {
        cid = `${base.slice(0, 28)}_${n}`;
        n += 1;
    }
    return cid;
}

function _sbSyncDialogueUI() {
    const wrap = document.getElementById('sb-script-wrap');
    if (!wrap) return;
    wrap.classList.toggle('hidden', _sbDialogueMode() !== 'paste');
}

function _sbRenderStyleSelect() {
    const sel = document.getElementById('sb-visual-style');
    if (!sel) return;
    const opts = _sbStyleOptions.length ? _sbStyleOptions : [{ id: 'pixar_lite', label: '3D Pixar-lite' }];
    sel.innerHTML = opts.map(o =>
        `<option value="${esc(o.id)}"${o.id === _sbVisualStyle ? ' selected' : ''}>${esc(o.label || o.id)}</option>`
    ).join('');
}

function sbGoToEpisode() {
    if (!_sbCast.length) {
        alert('Add at least one character — or extract them from your story/script.');
        return;
    }
    if (!_sbCastHasLook()) {
        alert('Generate at least one character look so scenes stay consistent.');
        return;
    }
    saveStoryboardCast(true).then(() => goToStep('storyboard'));
}

async function loadStoryboardCast() {
    try {
        const res = await fetch('/api/storyboard/cast');
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            if (Array.isArray(data.cast)) _sbCast = data.cast;
            if (data.visual_style) _sbVisualStyle = data.visual_style;
            if (typeof data.template === 'string') _sbTemplate = data.template;
            if (Array.isArray(data.styles) && data.styles.length) _sbStyleOptions = data.styles;
            if (Array.isArray(data.family_template_cast)) _sbFamilyTemplateCast = data.family_template_cast;
        }
    } catch (e) {
        console.warn('cast load', e);
    }
    if (!Array.isArray(_sbCast)) _sbCast = [];
    _sbRenderStyleSelect();
    renderStoryboardCastGrid();
}

function renderStoryboardCastGrid() {
    const grid = document.getElementById('sb-cast-grid');
    if (!grid) return;
    if (!_sbCast.length) {
        grid.innerHTML = `<div class="cr-surface" style="padding:18px;grid-column:1/-1;">
            <p style="font-family:var(--font-body);font-size:14px;color:var(--app-ink-2);margin:0;">
                No cast yet. Add a character, extract from your story/script, or start from the optional Easy English family template.
            </p>
        </div>`;
    } else {
        grid.innerHTML = (_sbCast || []).map((c, i) => {
            const hasLook = !!(c.portrait_url || c.sheet_url);
            const img = c.portrait_url
                ? `<img src="${esc(c.portrait_url)}" alt="${esc(c.name)}" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:12px;background:var(--app-surface);">`
                : `<div style="width:100%;aspect-ratio:1;border-radius:12px;background:var(--app-surface);display:flex;align-items:center;justify-content:center;color:var(--app-ink-3);font-family:var(--font-body);font-size:13px;">No look yet</div>`;
            return `<div class="sb-cast-card${hasLook ? ' has-look' : ''}" data-cast-i="${i}">
                ${img}
                <div style="margin-top:10px;display:flex;align-items:center;gap:8px;">
                    <input type="checkbox" class="sb-cast-inc" data-i="${i}" ${c.included !== false ? 'checked' : ''}>
                    <input type="text" class="cr-input sb-cast-name-in" data-i="${i}" value="${esc(c.name || '')}" style="padding:8px 10px;" placeholder="Name">
                    <button type="button" class="btn-ghost sb-cast-remove" data-i="${i}" style="font-size:12px;padding:6px 8px;" title="Remove">✕</button>
                </div>
                <textarea class="cr-input sb-cast-look" data-i="${i}" rows="2" style="margin-top:8px;font-size:13px;" placeholder="Describe their look…">${esc(c.look_prompt || '')}</textarea>
                <button type="button" class="btn-primary sb-cast-gen" data-i="${i}" style="width:100%;margin-top:10px;font-size:13px;padding:10px;">
                    <span class="btn-text">${hasLook ? 'Recreate look' : 'Generate look'}</span>
                    <span class="btn-loading hidden"><span class="cr-spinner" style="width:14px;height:14px;border-width:2px;display:inline-block;"></span> Generating…</span>
                </button>
            </div>`;
        }).join('');
    }
    grid.querySelectorAll('.sb-cast-inc').forEach(el => {
        el.addEventListener('change', () => {
            const i = +el.dataset.i;
            if (_sbCast[i]) _sbCast[i].included = el.checked;
        });
    });
    grid.querySelectorAll('.sb-cast-name-in').forEach(el => {
        el.addEventListener('input', () => {
            const i = +el.dataset.i;
            if (_sbCast[i]) _sbCast[i].name = el.value.trim();
        });
    });
    grid.querySelectorAll('.sb-cast-look').forEach(el => {
        el.addEventListener('input', () => {
            const i = +el.dataset.i;
            if (_sbCast[i]) _sbCast[i].look_prompt = el.value.trim();
        });
    });
    grid.querySelectorAll('.sb-cast-gen').forEach(el => {
        el.addEventListener('click', () => generateCastLook(+el.dataset.i, el));
    });
    grid.querySelectorAll('.sb-cast-remove').forEach(el => {
        el.addEventListener('click', () => {
            const i = +el.dataset.i;
            _sbCast.splice(i, 1);
            renderStoryboardCastGrid();
            saveStoryboardCast(true);
        });
    });
    const next = document.getElementById('btn-sb-cast-next');
    if (next) next.disabled = !_sbCastHasLook();
    const hint = document.getElementById('sb-cast-hint');
    if (hint) {
        hint.textContent = _sbCastHasLook()
            ? 'Looks ready — continue when you are happy with the cast.'
            : 'Add at least one character and generate a look to continue. Saved to your account.';
    }
}

function addStoryboardCastMember() {
    const name = prompt('Character name?');
    if (!name || !name.trim()) return;
    const cid = _sbSlugId(name.trim());
    _sbCast.push({
        id: cid,
        name: name.trim(),
        included: true,
        look_prompt: '',
        portrait_url: '',
        sheet_url: '',
        portrait_path: '',
        sheet_path: '',
    });
    _sbTemplate = '';
    renderStoryboardCastGrid();
    saveStoryboardCast(true);
}

async function extractStoryboardCast() {
    if (!_sbRequireAccess()) return;
    const story = (
        (document.getElementById('sb-extract-source')?.value || '')
        || (document.getElementById('sb-story')?.value || '')
    ).trim();
    const script = (document.getElementById('sb-script')?.value || '').trim();
    if (!story && !script) {
        alert('Paste a story or script above, then extract characters.');
        return;
    }
    const btn = document.getElementById('btn-sb-extract-cast');
    setLoading(btn, true);
    try {
        const res = await fetch('/api/storyboard/cast/extract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ story, script, visual_style: _sbVisualStyle }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Extract failed');
        const proposed = Array.isArray(data.cast) ? data.cast : [];
        if (!proposed.length) {
            alert('No recurring characters found. Add them manually.');
            return;
        }
        const byId = new Map((_sbCast || []).map(c => [c.id, c]));
        const merged = proposed.map(p => {
            const prev = byId.get(p.id) || [...byId.values()].find(x =>
                (x.name || '').toLowerCase() === (p.name || '').toLowerCase()
            );
            return prev ? { ...p, ...prev, name: p.name || prev.name, look_prompt: p.look_prompt || prev.look_prompt } : p;
        });
        _sbCast = merged;
        _sbTemplate = '';
        const src = (document.getElementById('sb-extract-source')?.value || '').trim();
        const storyEl = document.getElementById('sb-story');
        if (src && storyEl && !(storyEl.value || '').trim()) storyEl.value = src;
        renderStoryboardCastGrid();
        await saveStoryboardCast(true);
        track('storyboard_cast_extract', { count: merged.length });
    } catch (e) {
        alert(e.message || 'Could not extract cast');
    } finally {
        setLoading(btn, false);
    }
}

function applyFamilyTemplateCast() {
    const tmpl = Array.isArray(_sbFamilyTemplateCast) && _sbFamilyTemplateCast.length
        ? _sbFamilyTemplateCast
        : [
            { id: 'max', name: 'Max', included: true, look_prompt: 'boy ~8 years old, messy black hair, blue polo, grey pants', portrait_url: '', sheet_url: '' },
            { id: 'mia', name: 'Mia', included: true, look_prompt: 'girl ~7 years old, black pigtails pink ties, yellow sweater', portrait_url: '', sheet_url: '' },
            { id: 'mom', name: 'Mom', included: true, look_prompt: 'woman mid-30s, long wavy brown hair, light cardigan', portrait_url: '', sheet_url: '' },
            { id: 'dad', name: 'Dad', included: true, look_prompt: 'man mid-30s, curly dark hair, short beard, blue shirt', portrait_url: '', sheet_url: '' },
        ];
    if (_sbCast.length && !confirm('Replace your current cast with the Easy English family template?')) return;
    _sbCast = tmpl.map(c => ({ ...c, portrait_url: '', sheet_url: '', portrait_path: '', sheet_path: '' }));
    _sbTemplate = 'easy_english_family';
    renderStoryboardCastGrid();
    saveStoryboardCast(true);
}

async function generateCastLook(i, btn) {
    if (!_sbRequireAccess()) return;
    const c = _sbCast[i];
    if (!c) return;
    if (!(c.name || '').trim()) {
        alert('Give this character a name first.');
        return;
    }
    setLoading(btn, true);
    try {
        const res = await fetch('/api/storyboard/cast/generate-look', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                id: c.id || _sbSlugId(c.name),
                name: c.name,
                look_prompt: c.look_prompt,
                make_sheet: true,
                visual_style: _sbVisualStyle,
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Look generation failed');
        if (Array.isArray(data.cast)) _sbCast = data.cast;
        else if (data.member) {
            _sbCast[i] = { ...c, ...data.member };
        }
        if (data.visual_style) _sbVisualStyle = data.visual_style;
        renderStoryboardCastGrid();
        track('storyboard_cast_look', { id: c.id });
    } catch (e) {
        alert(e.message || 'Look generation failed');
    } finally {
        setLoading(btn, false);
    }
}

async function saveStoryboardCast(silent) {
    try {
        const res = await fetch('/api/storyboard/cast', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cast: _sbCast,
                visual_style: _sbVisualStyle,
                template: _sbTemplate,
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            if (Array.isArray(data.cast)) _sbCast = data.cast;
            if (data.visual_style) _sbVisualStyle = data.visual_style;
            if (typeof data.template === 'string') _sbTemplate = data.template;
        }
        if (!silent && !res.ok) throw new Error(data.detail || 'Save failed');
    } catch (e) {
        if (!silent) alert(e.message || 'Could not save cast');
    }
}

function renderEpisodeCastStrip() {
    const strip = document.getElementById('sb-episode-cast-strip');
    if (!strip) return;
    const shown = (_sbCast || []).filter(c => c.included !== false);
    strip.innerHTML = shown.map(c => {
        const face = c.portrait_url
            ? `<img src="${esc(c.portrait_url)}" alt="" style="width:48px;height:48px;border-radius:50%;object-fit:cover;">`
            : `<div style="width:48px;height:48px;border-radius:50%;background:var(--app-surface);"></div>`;
        return `<div style="display:flex;align-items:center;gap:8px;font-family:var(--font-body);font-size:13px;color:var(--app-ink);">${face}<span>${esc(c.name)}</span></div>`;
    }).join('') + `<button type="button" class="btn-ghost" style="font-size:12px;padding:6px 10px;" onclick="goToStep('sb-cast')">Edit cast</button>`;
}

async function initStoryboardCastUI() {
    await loadStoryboardCast();
}

function initStoryboardPackUI() {
    const mins = state.nicheData?.default_minutes || state.targetMinutes || 8;
    const slider = document.getElementById('sb-minutes');
    const label = document.getElementById('sb-minutes-label');
    const maxMins = _sbPackMaxMinutes();
    if (slider) {
        slider.min = 3;
        slider.max = String(maxMins);
        const clamped = Math.min(maxMins, Math.max(3, Number(mins) || 8));
        slider.value = clamped;
        if (label) label.textContent = clamped + ' min';
    }
    const titleInput = document.getElementById('sb-title');
    if (titleInput && state.title) titleInput.value = state.title;
    _sbSyncDialogueUI();
    renderEpisodeCastStrip();
    const moralsList = document.getElementById('sb-morals-list');
    if (moralsList) moralsList.innerHTML = '';
    _sbSyncPackCostUI();
}

function bindStoryboardPackUI() {
    const slider = document.getElementById('sb-minutes');
    const label = document.getElementById('sb-minutes-label');
    if (slider && label) {
        slider.addEventListener('input', () => {
            const maxMins = _sbPackMaxMinutes();
            let v = Number(slider.value) || 8;
            if (v > maxMins) {
                v = maxMins;
                slider.value = v;
                if (isTrialUser() && !isAdminUser()) {
                    showSoftPrompt(
                        `Trial packs max out at ${maxMins} minutes. Start your plan to unlock up to ${_featureFlags.storyboard_pack_max_minutes || 25} minutes.`,
                        'Start plan now',
                        () => { try { endTrialNow(); } catch (_) { showPricingModal({ reason: 'storyboard' }); } },
                    );
                }
            }
            label.textContent = v + ' min';
            _sbSyncPackCostUI();
        });
    }
    document.querySelectorAll('input[name="sb-dialogue-mode"]').forEach(el => {
        el.addEventListener('change', _sbSyncDialogueUI);
    });
    document.getElementById('sb-visual-style')?.addEventListener('change', (e) => {
        _sbVisualStyle = e.target.value || 'pixar_lite';
        saveStoryboardCast(true);
    });
    document.getElementById('btn-sb-add-cast')?.addEventListener('click', addStoryboardCastMember);
    document.getElementById('btn-sb-extract-cast')?.addEventListener('click', extractStoryboardCast);
    document.getElementById('btn-sb-family-template')?.addEventListener('click', applyFamilyTemplateCast);
    document.getElementById('btn-sb-suggest-morals')?.addEventListener('click', suggestStoryboardMorals);
    document.getElementById('btn-sb-thumb')?.addEventListener('click', generateStoryboardThumbnail);
    document.getElementById('sb-thumb-refs')?.addEventListener('change', (e) => {
        const preview = document.getElementById('sb-thumb-refs-preview');
        if (preview) preview.innerHTML = '';
        _sbThumbRefs = [...(e.target.files || [])];
        _sbThumbRefs.forEach(f => {
            if (!preview) return;
            const img = document.createElement('img');
            img.className = 'ref-thumb';
            img.src = URL.createObjectURL(f);
            preview.appendChild(img);
        });
    });
    document.getElementById('btn-sb-preview')?.addEventListener('click', () => startStoryboardPack('preview'));
    document.getElementById('btn-sb-generate')?.addEventListener('click', () => startStoryboardPack('full'));
    document.getElementById('btn-sb-continue-full')?.addEventListener('click', () => startStoryboardPack('full', { fromPreview: true }));
    document.getElementById('btn-sb-regen-beat')?.addEventListener('click', regenSelectedBeat);
    document.getElementById('btn-sb-goto-assemble')?.addEventListener('click', () => goToStoryboardCook());
    document.getElementById('btn-sb-range-cook')?.addEventListener('click', () => goToStoryboardCook());
    document.getElementById('btn-sb-range-clear')?.addEventListener('click', () => _sbClearCookRange());
    document.getElementById('btn-sb-range-all')?.addEventListener('click', () => _sbSelectAllCookRange());
    document.getElementById('btn-sb-assemble-match')?.addEventListener('click', matchStoryboardAssemble);
    document.getElementById('btn-sb-assemble-run')?.addEventListener('click', runStoryboardAssemble);
    document.getElementById('btn-sb-animate-run')?.addEventListener('click', runStoryboardAnimate);
    const drop = document.getElementById('sb-assemble-drop');
    const fileIn = document.getElementById('sb-assemble-files');
    fileIn?.addEventListener('change', (e) => {
        _sbSetAssembleFiles([...(e.target.files || [])]);
    });
    if (drop) {
        drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('is-drag'); });
        drop.addEventListener('dragleave', () => drop.classList.remove('is-drag'));
        drop.addEventListener('drop', (e) => {
            e.preventDefault();
            drop.classList.remove('is-drag');
            _sbSetAssembleFiles([...(e.dataTransfer?.files || [])]);
        });
    }
}

function _sbSetAssembleFiles(files) {
    _sbAssembleFiles = (files || []).filter(f => {
        const n = (f.name || '').toLowerCase();
        return n.endsWith('.mp4') || n.endsWith('.webm') || n.endsWith('.mov') || n.endsWith('.mkv') || n.endsWith('.m4v') || n.endsWith('.zip');
    });
    _sbAssembleStagingId = '';
    const hint = document.getElementById('sb-assemble-file-hint');
    if (hint) {
        hint.textContent = _sbAssembleFiles.length
            ? `${_sbAssembleFiles.length} file${_sbAssembleFiles.length === 1 ? '' : 's'}: ${_sbAssembleFiles.map(f => f.name).slice(0, 6).join(', ')}${_sbAssembleFiles.length > 6 ? '…' : ''}`
            : '';
    }
    const matchBtn = document.getElementById('btn-sb-assemble-match');
    const runBtn = document.getElementById('btn-sb-assemble-run');
    if (matchBtn) matchBtn.disabled = !_sbAssembleFiles.length || !_sbJobId;
    if (runBtn) runBtn.disabled = (!_sbAssembleFiles.length && !_sbAssembleStagingId) || !_sbJobId;
    // Client-side filename preview
    _sbRenderAssembleMatchLocal();
}

function _sbParseClipIndex(name) {
    const m = String(name || '').match(/(?:^|[_\-\s])(?:scene[_\-\s]*)?0*(\d{1,4})(?:[_\-\s.]|$)/i)
        || String(name || '').match(/^0*(\d{1,4})\b/);
    return m ? parseInt(m[1], 10) : null;
}

function _sbRenderAssembleMatchLocal() {
    const box = document.getElementById('sb-assemble-match');
    if (!box || !_sbAssembleFiles.length) return;
    const rows = _sbAssembleFiles
        .filter(f => !/\.zip$/i.test(f.name))
        .map(f => `<tr>
                <td class="cr-mono">—</td>
                <td>${esc(f.name)}</td>
                <td style="color:var(--app-ink-3);">awaiting hash match</td>
            </tr>`).join('');
    box.classList.remove('hidden');
    box.innerHTML = `
        <p style="font-family:var(--font-body);font-size:13px;color:var(--app-ink-2);margin-bottom:8px;">
            Filenames are ignored — run Preview match to pair clips by look (first + last frame).
        </p>
        <table style="width:100%;font-family:var(--font-body);font-size:13px;border-collapse:collapse;">
            <thead><tr style="text-align:left;color:var(--app-ink-3);">
                <th style="padding:6px 8px;">Scene</th><th style="padding:6px 8px;">File</th><th style="padding:6px 8px;">Method</th>
            </tr></thead>
            <tbody>${rows || '<tr><td colspan="3" style="padding:8px;">Zip selected — match on server.</td></tr>'}</tbody>
        </table>`;
}

function _sbRenderAssembleMatchServer(matched, unmatched) {
    const box = document.getElementById('sb-assemble-match');
    if (!box) return;
    const rows = (matched || []).map(m => {
        const methodLabel = (m.method || 'hash').replace('phash_', 'hash ');
        const pct = m.confidence != null ? ` (${Math.round((m.confidence || 0) * 100)}%)` : '';
        const color = (m.method || '').includes('phash') || (m.method || '') === 'phash'
            ? 'var(--warning, #c90)'
            : 'var(--app-ink-3)';
        return `<tr>
            <td class="cr-mono" style="padding:6px 8px;">${String(m.index).padStart(3, '0')}</td>
            <td style="padding:6px 8px;">${esc(m.filename || '')}</td>
            <td style="padding:6px 8px;color:${color};">${esc(methodLabel)}${pct}</td>
        </tr>`;
    }).join('');
    const un = (unmatched || []).map(u =>
        `<tr><td class="cr-mono" style="padding:6px 8px;color:var(--app-ink-3);">—</td>
         <td style="padding:6px 8px;">${esc(u.filename || '')}</td>
         <td style="padding:6px 8px;color:var(--error, #c55);">unmatched</td></tr>`
    ).join('');
    box.classList.remove('hidden');
    box.innerHTML = `
        <p style="font-family:var(--font-body);font-size:13px;color:var(--app-ink-2);margin-bottom:8px;">
            Matched ${(matched || []).length} scene${(matched || []).length === 1 ? '' : 's'}${(unmatched || []).length ? ` · ${unmatched.length} unmatched` : ''}.
        </p>
        <table style="width:100%;font-family:var(--font-body);font-size:13px;border-collapse:collapse;">
            <thead><tr style="text-align:left;color:var(--app-ink-3);">
                <th style="padding:6px 8px;">Scene</th><th style="padding:6px 8px;">File</th><th style="padding:6px 8px;">Method</th>
            </tr></thead>
            <tbody>${rows}${un}</tbody>
        </table>`;
}

async function matchStoryboardAssemble() {
    if (!_sbRequireAccess()) return;
    if (!_sbJobId) { alert('Generate a pack first.'); return; }
    if (!_sbAssembleFiles.length) { alert('Drop your I2V clips first.'); return; }
    const btn = document.getElementById('btn-sb-assemble-match');
    setLoading(btn, true);
    try {
        const fd = new FormData();
        _sbAssembleFiles.forEach(f => fd.append('clips', f));
        const res = await fetch(`/api/storyboard/jobs/${_sbJobId}/assemble/match`, { method: 'POST', body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Match failed');
        _sbAssembleStagingId = data.staging_id || '';
        _sbRenderAssembleMatchServer(data.matched || [], data.unmatched || []);
        const runBtn = document.getElementById('btn-sb-assemble-run');
        if (runBtn) runBtn.disabled = !(data.matched_count > 0);
        track('storyboard_assemble_match', { job_id: _sbJobId, matched: data.matched_count || 0 });
    } catch (e) {
        alert(e.message || 'Match failed');
    } finally {
        setLoading(btn, false);
    }
}

function _sbAddMusicEnabled() {
    const el = document.getElementById('sb-add-music');
    return !el || !!el.checked;
}

function _sbBurnCaptionsEnabled() {
    const el = document.getElementById('sb-burn-captions');
    return !el || !!el.checked;
}

async function runStoryboardAssemble() {
    if (!_sbRequireAccess()) return;
    if (!_sbJobId) { alert('Generate a pack first.'); return; }
    if (!_sbAssembleStagingId && !_sbAssembleFiles.length) {
        alert('Drop clips and preview match first.');
        return;
    }
    if (cookingManager.isCooking && cookingManager.slotLimit <= 1) {
        alert('A video is already cooking. Wait for it to finish or cancel it.');
        return;
    }
    const btn = document.getElementById('btn-sb-assemble-run');
    setLoading(btn, true);
    document.getElementById('sb-assemble-result')?.classList.add('hidden');
    cookingManager._showCookingBar();
    try {
        const fd = new FormData();
        if (_sbAssembleStagingId) fd.append('staging_id', _sbAssembleStagingId);
        fd.append('burn_captions', _sbBurnCaptionsEnabled() ? '1' : '0');
        fd.append('add_music', _sbAddMusicEnabled() ? '1' : '0');
        const notify = (document.getElementById('sb-assemble-notify')?.value || '').trim();
        if (notify) fd.append('notify_email', notify);
        if (!_sbAssembleStagingId) {
            _sbAssembleFiles.forEach(f => fd.append('clips', f));
        }
        const res = await fetch(`/api/storyboard/jobs/${_sbJobId}/assemble`, { method: 'POST', body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Cook failed to start');
        _sbAssembleJobId = data.job_id;
        cookingManager.adoptStoryboard(_sbAssembleJobId, 'your video');
        if (_sbAssemblePollTimer) clearInterval(_sbAssemblePollTimer);
        _sbAssemblePollTimer = setInterval(pollStoryboardAssemble, 2500);
        pollStoryboardAssemble();
        track('storyboard_assemble_queued', { job_id: _sbAssembleJobId, parent: _sbJobId });
    } catch (e) {
        setLoading(btn, false);
        if (!cookingManager.isCooking) cookingManager._hideCookingBar();
        alert(e.message || 'Cook failed');
    }
}

async function pollStoryboardAssemble() {
    if (!_sbAssembleJobId) return;
    const prog = document.getElementById('sb-assemble-progress');
    const btnAssemble = document.getElementById('btn-sb-assemble-run');
    const btnAnimate = document.getElementById('btn-sb-animate-run');
    try {
        const res = await fetch(`/api/storyboard/jobs/${_sbAssembleJobId}`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Status failed');
        const lines = (data.progress || []).map(p => p.message || '').filter(Boolean);
        const last = lines[lines.length - 1] || '';
        if (last) _sbUpdateCookingBar(last);
        if (prog) {
            prog.classList.remove('hidden');
            prog.innerHTML = lines.slice(-12).map(m => `<div>${esc(_friendlySbProgress(m))}</div>`).join('') || 'Cooking…';
            prog.scrollTop = prog.scrollHeight;
        }
        if (data.status === 'complete' || data.video_ready) {
            if (_sbAssemblePollTimer) { clearInterval(_sbAssemblePollTimer); _sbAssemblePollTimer = null; }
            setLoading(btnAssemble, false);
            setLoading(btnAnimate, false);
            if (cookingManager.jobId === _sbAssembleJobId) {
                cookingManager._clear();
                cookingManager._hideCookingBar();
            } else {
                _sbHideCookingBar();
            }
            const box = document.getElementById('sb-assemble-result');
            const summary = document.getElementById('sb-assemble-summary');
            const dl = document.getElementById('sb-assemble-download');
            const kind = data.kind || '';
            if (summary) {
                const mins = data.duration_sec ? (data.duration_sec / 60).toFixed(1) : '?';
                summary.textContent = `Ready — ${data.beat_count || '?'} scenes · ~${mins} min · saved to History`;
            }
            if (dl) {
                dl.href = `/api/storyboard/jobs/${_sbAssembleJobId}/download`;
                dl.download = '';
            }
            if (box) box.classList.remove('hidden');
            try { loadHistory(); } catch (_) {}
            if (Array.isArray(data.match_report) && data.match_report.length) {
                _sbRenderAssembleMatchServer(
                    data.match_report.map(m => ({
                        index: m.index,
                        filename: m.clip || m.filename,
                        method: m.method,
                        confidence: m.confidence,
                    })),
                    [],
                );
            }
            track(kind === 'storyboard_animate' ? 'storyboard_animate_ready' : 'storyboard_assemble_ready', { job_id: _sbAssembleJobId });
        } else if (data.status === 'error' || data.status === 'cancelled') {
            if (_sbAssemblePollTimer) { clearInterval(_sbAssemblePollTimer); _sbAssemblePollTimer = null; }
            setLoading(btnAssemble, false);
            setLoading(btnAnimate, false);
            if (cookingManager.jobId === _sbAssembleJobId) {
                cookingManager._clear();
                cookingManager._hideCookingBar();
            } else {
                _sbHideCookingBar();
            }
            alert(data.error || 'Cook failed');
        }
    } catch (e) {
        console.warn('assemble poll', e);
    }
}

function _friendlySbPackProgress(raw) {
    const s = String(raw || '');
    if (/Keeping .* preview|Continuing from preview|continuing the rest/i.test(s)) {
        const m = s.match(/Keeping\s+(\d+)/i);
        return m ? `Keeping ${m[1]} preview scenes, generating the rest…` : 'Continuing from your preview…';
    }
    if (/Uploading pack|Zipping|Writing pack/i.test(s)) return 'Saving your storyboard…';
    if (/Queued|queue/i.test(s)) return 'Queued — starting soon…';
    if (/Planning first-minute|Planning ~/i.test(s)) return 'Planning your scenes…';
    if (/Beat sheet ready|Planning/i.test(s)) return 'Planning your scenes…';
    if (/Locked .* character|character reference/i.test(s)) return 'Matching your cast looks…';
    if (/No character references/i.test(s)) return 'Drawing scenes from your story…';
    if (/Generating .* scene stills|Stills \d/i.test(s)) {
        const m = s.match(/Stills\s+(\d+)\s*\/\s*(\d+)/i) || s.match(/(\d+)\s*\/\s*(\d+)/);
        if (m) return `Drawing scenes… ${m[1]} of ${m[2]}`;
        return 'Drawing your scenes…';
    }
    if (/Scene \d+ ready/i.test(s)) {
        const m = s.match(/Scene\s+(\d+)/i);
        return m ? `Scene ${m[1]} ready` : 'Scene ready';
    }
    if (/Done —|Pack ready|zip/i.test(s)) return 'Storyboard ready…';
    if (/Warning:.*stills failed/i.test(s)) return 'Some scenes need a redo…';
    return s
        .replace(/Seedance|Fly|Atlas|phash|I2V|ffmpeg|zip_path|Spaces/gi, '')
        .replace(/\s{2,}/g, ' ')
        .trim()
        .slice(0, 72) || 'Working…';
}

function _friendlySbProgress(raw) {
    const s = String(raw || '');
    if (/Uploading pack|Zipping|Writing pack/i.test(s)) return 'Saving your storyboard…';
    if (/Queued|queue/i.test(s)) return 'Queued — cooking soon…';
    if (/Starting|Joining/i.test(s)) return 'Starting your cook…';
    if (/Adding music/i.test(s)) return 'Adding music…';
    if (/Adding captions|caption/i.test(s)) return 'Adding captions…';
    if (/Preparing scene|Putting your video/i.test(s)) return 'Putting your video together…';
    if (/Cooking scenes|Animating|Animated scene|scene\(s\)/i.test(s)) {
        const m = s.match(/(\d+)\s*\/\s*(\d+)/);
        const cooking = s.match(/\((\d+)\s*cooking\)/i);
        if (m && cooking) return `Cooking scenes… ${m[1]} of ${m[2]} (${cooking[1]} in parallel)`;
        if (m) return `Cooking scenes… ${m[1]} of ${m[2]}`;
        return 'Cooking scenes…';
    }
    if (/Stitch|Normaliz|caption|Assemble|Building/i.test(s)) return 'Putting your video together…';
    if (/ready|History|Download|complete|Done/i.test(s)) return 'Almost done…';
    if (/Uploading|Saving/i.test(s)) return 'Saving your video…';
    // Never show infra / model names to users
    return s
        .replace(/Seedance|Fly|Atlas|phash|I2V|ffmpeg/gi, '')
        .replace(/\s{2,}/g, ' ')
        .trim()
        .slice(0, 72) || 'Cooking…';
}

function _sbShowCookingBar(title) {
    cookingManager.title = title || cookingManager.title || 'your video';
    cookingManager.kind = cookingManager.kind || 'storyboard';
    cookingManager._showCookingBar();
}

function _sbHideCookingBar() {
    // Only hide if this tab isn't still tracking another cook
    if (!cookingManager.isCooking) cookingManager._hideCookingBar();
}

function _sbUpdateCookingBar(msg) {
    const st = document.getElementById('cooking-bar-status');
    if (st) st.textContent = _friendlySbProgress(msg);
}

function _sbCookTargetMinutes() {
    const effective = _sbCookBeatsEffective();
    const cap = _sbCookMaxMinutes();
    const mins = _sbBeatsDurationMinutes(effective);
    if (mins > 0) return Math.min(cap, Math.max(0.5, Math.round(mins * 10) / 10));
    let fullMins = 8;
    try {
        const slider = document.getElementById('sb-minutes');
        if (slider) fullMins = Number(slider.value) || fullMins;
    } catch (_) {}
    if ((_sbPackMode || 'full').toLowerCase() === 'preview') fullMins = Math.min(fullMins, 1.2);
    return Math.min(cap, fullMins);
}

async function _sbSyncAnimateUI() {
    const btn = document.getElementById('btn-sb-animate-run');
    const blurb = document.getElementById('sb-animate-blurb');
    const cookAllowed = isAdminUser() || hasFullLengthAccess();
    if (btn) btn.disabled = !_sbBoardCookReady() || !cookAllowed;
    const mins = _sbCookTargetMinutes();
    const selected = _sbCookBeats();
    const effective = _sbCookBeatsEffective();
    const cap = _sbCookMaxMinutes();
    let stretch;
    if (_sbHasCookRange()) {
        stretch = `scenes ${String(Math.min(_sbRangeFrom, _sbRangeTo)).padStart(3, '0')}–${String(Math.max(_sbRangeFrom, _sbRangeTo)).padStart(3, '0')}`;
    } else if (_sbBeatsDurationMinutes(selected) > cap + 0.05 && effective.length) {
        stretch = `first ~${mins} min (scenes ${String(effective[0].index).padStart(3, '0')}–${String(effective[effective.length - 1].index).padStart(3, '0')})`;
    } else {
        stretch = `${effective.length || 'all'} scenes`;
    }
    if (!cookAllowed) {
        if (blurb) {
            blurb.textContent = `On-site cook is for paid plans (${_sbAnimateCreditsFlat()} credits, max ${cap} min). Start your plan to animate ${stretch}.`;
        }
        return;
    }
    try {
        const res = await fetch(`/api/storyboard/animate-cost?minutes=${encodeURIComponent(mins)}`);
        const data = await res.json().catch(() => ({}));
        if (blurb && res.ok) {
            const c = Number(data.credits || _sbAnimateCreditsFlat() || 0);
            const capNote = ` On-site cook max ${Number(data.cook_max_minutes || cap)} min.`;
            blurb.textContent = c > 0
                ? `Cook ${stretch} into motion · ${c} credit${c === 1 ? '' : 's'}.${capNote}`
                : `Cook ${stretch} into motion, stitch them, and optionally add captions + music.${capNote}`;
        } else if (blurb) {
            blurb.textContent = `Cook ${stretch} into motion · ${_sbAnimateCreditsFlat()} credits. On-site cook max ${cap} min.`;
        }
    } catch (_) {
        if (blurb) blurb.textContent = `Cook ${stretch} into motion · ${_sbAnimateCreditsFlat()} credits. On-site cook max ${cap} min.`;
    }
}

async function runStoryboardAnimate() {
    if (!_sbRequireAccess()) return;
    if (!isAdminUser() && !hasFullLengthAccess()) {
        showSoftPrompt(
            'On-site cook is for paid plans. Start your plan to animate your storyboard.',
            'Start plan now',
            () => { try { endTrialNow(); } catch (_) { showPricingModal({ reason: 'storyboard' }); } },
        );
        return;
    }
    if (!_sbJobId) { alert('Generate a pack first.'); return; }
    const block = _sbBoardCookBlockReason();
    if (block) { alert(block); return; }
    if (cookingManager.isCooking && cookingManager.slotLimit <= 1) {
        alert('A video is already cooking. Wait for it to finish or cancel it.');
        return;
    }
    const btn = document.getElementById('btn-sb-animate-run');
    setLoading(btn, true);
    document.getElementById('sb-assemble-result')?.classList.add('hidden');
    cookingManager._showCookingBar();
    try {
        const fd = new FormData();
        fd.append('burn_captions', _sbBurnCaptionsEnabled() ? '1' : '0');
        fd.append('add_music', _sbAddMusicEnabled() ? '1' : '0');
        if (_sbHasCookRange()) {
            fd.append('beat_from', String(Math.min(_sbRangeFrom, _sbRangeTo)));
            fd.append('beat_to', String(Math.max(_sbRangeFrom, _sbRangeTo)));
        }
        const notify = (document.getElementById('sb-assemble-notify')?.value || '').trim();
        if (notify) fd.append('notify_email', notify);
        const res = await fetch(`/api/storyboard/jobs/${_sbJobId}/animate`, { method: 'POST', body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            if (_sbHandleBillingError(res, data, { needFallback: _sbAnimateCreditsFlat() })) {
                throw new Error('__billing__');
            }
            throw new Error(typeof data.detail === 'string' ? data.detail : 'Cook failed to start');
        }
        _sbAssembleJobId = data.job_id;
        const charged = Number(data.credits_charged || 0);
        if (charged > 0 && currentUser && typeof currentUser.credits === 'number') {
            currentUser.credits = Math.max(0, currentUser.credits - charged);
            updateAuthUI();
        }
        cookingManager.adoptStoryboard(_sbAssembleJobId, data.title || 'your video');
        if (_sbAssemblePollTimer) clearInterval(_sbAssemblePollTimer);
        _sbAssemblePollTimer = setInterval(pollStoryboardAssemble, 2500);
        pollStoryboardAssemble();
        track('storyboard_animate_queued', { job_id: _sbAssembleJobId, parent: _sbJobId, credits: charged });
    } catch (e) {
        setLoading(btn, false);
        if (!cookingManager.isCooking) cookingManager._hideCookingBar();
        if (!e || e.message !== '__billing__') alert(e.message || 'Cook failed');
    }
}

function _sbRenderThumbs(urls, paths) {
    const grid = document.getElementById('sb-thumb-grid');
    if (!grid) return;
    grid.innerHTML = '';
    document.getElementById('sb-thumb-loading')?.classList.add('hidden');
    urls.forEach((url, i) => {
        const card = document.createElement('div');
        card.className = 'thumb-card';
        card.innerHTML = `<img src="${url}" alt="Thumbnail ${i + 1}">`;
        card.addEventListener('click', () => {
            grid.querySelectorAll('.thumb-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            _sbThumbUrl = url;
            _sbThumbPath = paths[i] || '';
        });
        grid.appendChild(card);
    });
    if (urls.length) grid.querySelector('.thumb-card')?.click();
}

async function generateStoryboardThumbnail() {
    if (!_sbRequireAccess()) return;
    const title = (document.getElementById('sb-title')?.value || state.title || '').trim();
    if (!title) { alert('Add a title first.'); return; }
    const btn = document.getElementById('btn-sb-thumb');
    document.getElementById('sb-thumb-loading')?.classList.remove('hidden');
    setLoading(btn, true);
    try {
        const formData = new FormData();
        formData.append('title', title);
        formData.append('story', (document.getElementById('sb-story')?.value || '').trim());
        formData.append('script', (document.getElementById('sb-script')?.value || '').trim());
        formData.append('moral', (document.getElementById('sb-moral')?.value || '').trim());
        formData.append('visual_style', _sbVisualStyle || 'pixar_lite');
        formData.append('niche_style', _sbNicheThumbStyle());
        formData.append('cast_json', JSON.stringify(_sbCast || []));
        formData.append('count', '2');
        (_sbThumbRefs || []).forEach(f => formData.append('refs', f));
        const res = await fetch('/api/storyboard/thumbnail', { method: 'POST', body: formData });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Thumbnail failed');
        _sbRenderThumbs(data.thumbnails || [], data.paths || []);
    } catch (e) {
        document.getElementById('sb-thumb-loading')?.classList.add('hidden');
        alert(e.message || 'Thumbnail generation failed');
    } finally {
        setLoading(btn, false);
    }
}

async function suggestStoryboardMorals() {
    if (!_sbRequireAccess()) return;
    const story = (document.getElementById('sb-story')?.value || '').trim();
    if (!story) { alert('Describe what happens first.'); return; }
    const btn = document.getElementById('btn-sb-suggest-morals');
    const list = document.getElementById('sb-morals-list');
    if (btn) { btn.disabled = true; btn.textContent = 'Suggesting…'; }
    try {
        const res = await fetch('/api/storyboard/suggest-morals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ story, template: _sbTemplate }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Could not suggest takeaways');
        const morals = Array.isArray(data.morals) ? data.morals : [];
        if (!list) return;
        list.innerHTML = morals.map((m, i) => (
            `<button type="button" class="btn-ghost sb-moral-pick" style="display:block;width:100%;text-align:left;font-size:13px;padding:10px 12px;white-space:normal;">${esc(m)}</button>`
        )).join('');
        list.querySelectorAll('.sb-moral-pick').forEach((b, i) => {
            b.addEventListener('click', () => {
                const moralEl = document.getElementById('sb-moral');
                if (moralEl) moralEl.value = morals[i] || '';
            });
        });
    } catch (e) {
        alert(e.message || 'Could not suggest takeaways');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Suggest takeaways from my story'; }
    }
}

function _sbPaintBoardSelection() {
    const board = document.getElementById('sb-board');
    if (!board) return;
    board.querySelectorAll('.sb-board-card').forEach(c => {
        const i = +c.dataset.idx;
        c.classList.toggle('is-selected', i === _sbSelectedBeatIndex);
        c.classList.toggle('is-in-range', _sbIsInCookRange(i));
    });
    _sbSyncRangeBar();
    _sbSyncBoardCookControls();
}

function _sbBoardCardAtPoint(clientX, clientY) {
    const board = document.getElementById('sb-board');
    if (!board) return null;
    const el = document.elementFromPoint(clientX, clientY);
    const card = el?.closest?.('.sb-board-card');
    if (!card || !board.contains(card)) return null;
    return card;
}

function _sbUpdateBoardCardContent(card, beat) {
    if (!card || !beat) return;
    const chars = (beat.characters || '').trim();
    const metaName = chars ? chars.split(',')[0].trim() : 'scene';
    const meta = card.querySelector('.sb-board-meta');
    if (meta) {
        meta.innerHTML = `<span>${String(beat.index).padStart(3, '0')}</span><span>${esc(metaName)}</span>`;
    }
    const url = beat.image_url || '';
    let img = card.querySelector('img');
    const ph = card.querySelector('.sb-board-ph');
    if (url) {
        if (!img) {
            ph?.remove();
            img = document.createElement('img');
            img.alt = `Scene ${beat.index}`;
            img.draggable = false;
            card.insertBefore(img, card.firstChild);
        }
        if (img.dataset.src !== url) {
            img.dataset.src = url;
            img.src = url;
        }
    } else if (!ph && !img) {
        const holder = document.createElement('div');
        holder.className = 'sb-board-ph';
        holder.innerHTML = '<div class="cr-spinner" style="width:20px;height:20px;border-width:2px;margin:auto;"></div>';
        card.insertBefore(holder, card.firstChild);
    }
}

function _sbBindBoardGestures(board) {
    if (!board || board._sbRangeBound) return;
    board._sbRangeBound = true;

    const onMove = (e) => {
        if (!_sbDragSelecting || _sbDragAnchor == null) return;
        if (_sbDragPointerId != null && e.pointerId !== _sbDragPointerId) return;
        const dx = e.clientX - _sbDragStartX;
        const dy = e.clientY - _sbDragStartY;
        const card = _sbBoardCardAtPoint(e.clientX, e.clientY);
        const idx = card ? +card.dataset.idx : NaN;
        const moved = (dx * dx + dy * dy) >= 36;
        if (!board._sbDidDragRange) {
            if (!moved && !(Number.isFinite(idx) && idx !== _sbDragAnchor)) return;
            board._sbDidDragRange = true;
            board.classList.add('is-drag-selecting');
        }
        if (!Number.isFinite(idx)) return;
        _sbSetCookRange(_sbDragAnchor, idx);
        _sbPaintBoardSelection();
    };

    const onEnd = (e) => {
        if (!_sbDragSelecting) return;
        if (_sbDragPointerId != null && e.pointerId !== _sbDragPointerId) return;
        const wasDrag = !!board._sbDidDragRange;
        const anchor = _sbDragAnchor;
        const shift = _sbDragShift;
        _sbDragSelecting = false;
        _sbDragAnchor = null;
        _sbDragPointerId = null;
        _sbDragShift = false;
        board.classList.remove('is-drag-selecting');
        document.removeEventListener('pointermove', onMove, true);
        document.removeEventListener('pointerup', onEnd, true);
        document.removeEventListener('pointercancel', onEnd, true);

        if (wasDrag) {
            board._sbDidDragRange = false;
            _sbPaintBoardSelection();
            return;
        }
        // Tap — open recreate panel (click is unreliable after pointer gestures)
        if (anchor == null || !Number.isFinite(anchor)) return;
        if (shift && _sbSelectedBeatIndex != null && _sbSelectedBeatIndex !== anchor) {
            _sbSetCookRange(_sbSelectedBeatIndex, anchor);
            _sbPaintBoardSelection();
            return;
        }
        _sbSelectedBeatIndex = anchor;
        const beat = (Array.isArray(_sbBeats) ? _sbBeats : []).find(x => x.index === anchor);
        showBeatDetail(beat);
        _sbPaintBoardSelection();
    };

    board._sbOnPointerMove = onMove;
    board._sbOnPointerEnd = onEnd;
}

function _sbAttachCardPointerDown(board, card) {
    if (!card || card._sbPointerBound) return;
    card._sbPointerBound = true;
    card.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        // Kill native image-drag / text-select so board gestures work
        e.preventDefault();
        _sbDragSelecting = true;
        board._sbDidDragRange = false;
        _sbDragAnchor = +card.dataset.idx;
        _sbDragPointerId = e.pointerId;
        _sbDragStartX = e.clientX;
        _sbDragStartY = e.clientY;
        _sbDragShift = !!e.shiftKey;
        document.addEventListener('pointermove', board._sbOnPointerMove, true);
        document.addEventListener('pointerup', board._sbOnPointerEnd, true);
        document.addEventListener('pointercancel', board._sbOnPointerEnd, true);
    });
}

function renderStoryboardBoard(beats) {
    const board = document.getElementById('sb-board');
    if (!board) return;
    const list = Array.isArray(beats) ? [...beats].sort((a, b) => (a.index || 0) - (b.index || 0)) : [];
    _sbBeats = list;
    _sbBindBoardGestures(board);

    const existing = [...board.querySelectorAll('.sb-board-card')];
    const sameCards = existing.length === list.length
        && list.every((b, i) => existing[i] && +existing[i].dataset.idx === b.index);

    // Never wipe the DOM mid drag-select — only refresh stills in place
    if (_sbDragSelecting || sameCards) {
        list.forEach((b) => {
            const card = board.querySelector(`.sb-board-card[data-idx="${b.index}"]`);
            if (card) _sbUpdateBoardCardContent(card, b);
        });
        _sbPaintBoardSelection();
        return;
    }

    board.innerHTML = list.map(b => {
        const img = b.image_url
            ? `<img src="${esc(b.image_url)}" data-src="${esc(b.image_url)}" alt="Scene ${b.index}" draggable="false">`
            : `<div class="sb-board-ph"><div class="cr-spinner" style="width:20px;height:20px;border-width:2px;margin:auto;"></div></div>`;
        const sel = _sbSelectedBeatIndex === b.index ? ' is-selected' : '';
        const inRange = _sbIsInCookRange(b.index) ? ' is-in-range' : '';
        const chars = (b.characters || '').trim();
        return `<button type="button" class="sb-board-card${sel}${inRange}" data-idx="${b.index}">
            ${img}
            <div class="sb-board-meta">
                <span>${String(b.index).padStart(3, '0')}</span>
                <span>${esc(chars ? chars.split(',')[0].trim() : 'scene')}</span>
            </div>
        </button>`;
    }).join('');

    board.querySelectorAll('.sb-board-card').forEach(card => _sbAttachCardPointerDown(board, card));
    _sbSyncRangeBar();
    _sbSyncBoardCookControls();
}

function showBeatDetail(beat) {
    const box = document.getElementById('sb-beat-detail');
    if (!box || !beat) return;
    box.classList.remove('hidden');
    const img = document.getElementById('sb-beat-detail-img');
    if (img) {
        if (beat.image_url) {
            img.src = beat.image_url;
            img.classList.remove('hidden');
        } else {
            img.removeAttribute('src');
            img.classList.add('hidden');
        }
    }
    document.getElementById('sb-beat-detail-meta').textContent =
        `Scene ${String(beat.index).padStart(3, '0')} · ${(beat.characters || '')} · ${(beat.location || '')}`;
    document.getElementById('sb-beat-detail-dialogue').textContent = beat.dialogue || '';
    document.getElementById('sb-beat-detail-i2v').textContent = beat.i2v_prompt || '';
    const note = document.getElementById('sb-regen-note');
    if (note && !note.value) note.placeholder = 'e.g. Wider shot. More worried expression. Match the other scenes.';
}

function _sbSetPackLoading(loading) {
    setLoading(document.getElementById('btn-sb-generate'), loading);
    setLoading(document.getElementById('btn-sb-preview'), loading);
    const cont = document.getElementById('btn-sb-continue-full');
    if (cont) cont.disabled = !!loading;
}

async function regenSelectedBeat() {
    if (!_sbJobId || _sbSelectedBeatIndex == null) return;
    const btn = document.getElementById('btn-sb-regen-beat');
    const note = (document.getElementById('sb-regen-note')?.value || '').trim();
    setLoading(btn, true);
    try {
        const res = await fetch(`/api/storyboard/jobs/${_sbJobId}/regen-beat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                index: _sbSelectedBeatIndex,
                note,
                visual_style: _sbVisualStyle,
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Regen failed');
        if (Array.isArray(data.beats)) {
            renderStoryboardBoard(data.beats);
            const updated = data.beat || data.beats.find(b => b.index === _sbSelectedBeatIndex);
            if (updated) showBeatDetail(updated);
        } else if (data.beat) {
            showBeatDetail(data.beat);
        }
        _sbSyncBoardCookControls();
        if (data.zip_url || data.zip_ready) {
            const dl = document.getElementById('sb-download');
            if (dl) dl.href = `/api/storyboard/jobs/${_sbJobId}/download`;
        }
        track('storyboard_beat_regen', { job_id: _sbJobId, index: _sbSelectedBeatIndex, has_note: !!note });
    } catch (e) {
        alert(e.message || 'Regen failed');
    } finally {
        setLoading(btn, false);
    }
}

async function startStoryboardPack(packMode = 'full', opts = {}) {
    if (!_sbRequireAccess()) return;
    if (!_sbCastHasLook()) {
        alert('Generate at least one character look in Cast studio first.');
        goToStep('sb-cast');
        return;
    }
    await saveStoryboardCast(true);

    const fromPreview = !!opts.fromPreview;
    let payload;
    if (fromPreview && _sbLastEpisodePayload) {
        payload = {
            ..._sbLastEpisodePayload,
            cast: _sbCast,
            thumbnail_path: _sbThumbPath || _sbLastEpisodePayload.thumbnail_path || '',
            visual_style: _sbVisualStyle,
            template: _sbTemplate,
        };
    } else {
        const title = (document.getElementById('sb-title')?.value || '').trim();
        const story = (document.getElementById('sb-story')?.value || '').trim();
        const moral = (document.getElementById('sb-moral')?.value || '').trim();
        const dialogue_mode = _sbDialogueMode();
        const script = (document.getElementById('sb-script')?.value || '').trim();
        const minutes = Math.min(_sbPackMaxMinutes(), parseFloat(document.getElementById('sb-minutes')?.value || '8'));
        if (isTrialUser() && !isAdminUser() && parseFloat(document.getElementById('sb-minutes')?.value || '8') > _sbPackMaxMinutes() + 0.05) {
            showSoftPrompt(
                `Trial packs max out at ${_sbPackMaxMinutes()} minutes. Start your plan to unlock longer storyboards.`,
                'Start plan now',
                () => { try { endTrialNow(); } catch (_) { showPricingModal({ reason: 'storyboard' }); } },
            );
            return;
        }

        if (!title) { alert('Add a title for this video.'); return; }
        if (dialogue_mode === 'paste') {
            if (!script) { alert('Paste your script, or switch to “Write dialogue for me”.'); return; }
        } else if (!story) {
            alert('Describe what happens in this story.');
            return;
        }

        payload = {
            title,
            story,
            topic: story,
            moral,
            cast: _sbCast,
            dialogue_mode,
            script: dialogue_mode === 'paste' ? script : '',
            target_minutes: minutes,
            thumbnail_path: _sbThumbPath || '',
            visual_style: _sbVisualStyle,
            template: _sbTemplate,
        };
        state.title = title;
    }

    const mode = (packMode === 'preview') ? 'preview' : 'full';
    _sbPackMode = mode;
    _sbLastEpisodePayload = { ...payload };
    const parentPreviewId = (fromPreview && mode === 'full' && _sbJobId) ? _sbJobId : '';
    payload = {
        ...payload,
        pack_mode: mode,
        ...(parentPreviewId ? { parent_job_id: parentPreviewId } : {}),
    };

    goToStep('sb-pack');
    _sbSelectedBeatIndex = null;
    // Continue-from-preview: keep opening stills on the board while the rest generate
    if (!(fromPreview && parentPreviewId && Array.isArray(_sbBeats) && _sbBeats.length)) {
        _sbBeats = [];
        const board = document.getElementById('sb-board');
        if (board) {
            board.innerHTML = '';
            board.classList.remove('is-drag-selecting');
            board._sbDidDragRange = false;
        }
    }
    _sbPackStatus = 'queued';
    _sbZipReady = false;
    _sbRangeFrom = null;
    _sbRangeTo = null;
    _sbDragSelecting = false;
    _sbDragAnchor = null;
    _sbDragPointerId = null;
    _sbSyncBoardCookControls();
    _sbSyncRangeBar();
    document.getElementById('sb-beat-detail')?.classList.add('hidden');
    document.getElementById('sb-result')?.classList.add('hidden');
    document.getElementById('btn-sb-continue-full')?.classList.add('hidden');
    document.getElementById('btn-sb-goto-assemble')?.classList.add('hidden');
    const noteEl = document.getElementById('sb-regen-note');
    if (noteEl) noteEl.value = '';
    const status = document.getElementById('sb-pack-status');
    if (status) {
        if (parentPreviewId) {
            status.textContent = `Continuing from preview: keeping ${_sbBeats.length || '~8'} opening scenes, generating the rest…`;
        } else if (mode === 'preview') {
            status.textContent = 'Queuing first-minute preview — ~8 opening scenes…';
        } else {
            status.textContent = 'Queuing full pack — stills will appear as they’re ready…';
        }
    }
    const prog = document.getElementById('sb-progress');
    if (prog) {
        prog.classList.remove('hidden');
        prog.innerHTML = parentPreviewId ? 'Continuing from preview…' : 'Queuing…';
    }
    _sbSetPackLoading(true);

    try {
        const res = await fetch('/api/storyboard/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            if (_sbHandleBillingError(res, data, { needFallback: _sbPackCreditEstimate(payload.target_minutes || 8, mode) })) {
                throw new Error('__billing__');
            }
            throw new Error(typeof data.detail === 'string' ? data.detail : (data.detail || 'Could not start pack'));
        }
        _sbJobId = data.job_id;
        const charged = Number(data.credits_charged || 0);
        if (charged > 0 && currentUser && typeof currentUser.credits === 'number') {
            currentUser.credits = Math.max(0, currentUser.credits - charged);
            updateAuthUI();
        }
        track('storyboard_pack_started', {
            job_id: _sbJobId,
            target_minutes: payload.target_minutes,
            dialogue_mode: payload.dialogue_mode,
            pack_mode: mode,
            credits: charged,
            continued_from_preview: !!parentPreviewId,
            parent_job_id: parentPreviewId || undefined,
        });
        if (_sbPollTimer) clearInterval(_sbPollTimer);
        _sbPollTimer = setInterval(pollStoryboardPack, 1500);
        await pollStoryboardPack();
    } catch (e) {
        _sbSetPackLoading(false);
        if (e && e.message !== '__billing__') alert(e.message || 'Storyboard pack failed');
        goToStep('storyboard');
    }
}

async function pollStoryboardPack() {
    if (!_sbJobId) return;
    const prog = document.getElementById('sb-progress');
    const status = document.getElementById('sb-pack-status');
    const continueBtn = document.getElementById('btn-sb-continue-full');
    try {
        const res = await fetch(`/api/storyboard/jobs/${_sbJobId}`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Status failed');
        const mode = (data.pack_mode || _sbPackMode || 'full').toLowerCase();
        _sbPackMode = mode;
        _sbPackStatus = data.status || _sbPackStatus;
        _sbZipReady = !!(data.zip_ready || data.status === 'complete');
        const lines = (data.progress || []).map(p => p.message || p.msg || '').filter(Boolean);
        if (prog) {
            prog.classList.remove('hidden');
            prog.innerHTML = lines.slice(-12).map(m => `<div>${esc(_friendlySbPackProgress(m))}</div>`).join('') || 'Working…';
            prog.scrollTop = prog.scrollHeight;
        }
        if (Array.isArray(data.beats) && data.beats.length) {
            renderStoryboardBoard(data.beats);
            if (status && data.status !== 'complete' && !data.zip_ready) {
                const withStill = data.beats.filter(_sbBeatHasStill).length;
                status.textContent = mode === 'preview'
                    ? `${withStill}/${data.beats.length} opening scenes ready…`
                    : `${withStill}/${data.beats.length} scene stills ready…`;
            }
        }
        _sbSyncBoardCookControls();
        if (data.status === 'complete' || data.zip_ready) {
            if (_sbPollTimer) { clearInterval(_sbPollTimer); _sbPollTimer = null; }
            _sbSetPackLoading(false);
            const cookReady = _sbBoardCookReady();
            if (status) {
                if (mode === 'preview' && cookReady) {
                    status.textContent = 'First minute ready — cook this preview, or generate the full pack.';
                } else if (mode === 'preview') {
                    status.textContent = 'First minute almost ready — waiting on remaining scene stills…';
                } else if (cookReady) {
                    status.textContent = 'Pack ready — review stills, recreate any weak ones, then cook.';
                } else {
                    status.textContent = _sbBoardCookBlockReason() || 'Finishing scene stills…';
                }
            }
            const box = document.getElementById('sb-result');
            const summary = document.getElementById('sb-result-summary');
            const dl = document.getElementById('sb-download');
            if (summary) {
                const label = mode === 'preview' ? 'First-minute preview' : (data.title || 'Pack ready');
                summary.textContent = `${label} — ${data.beat_count || data.beats?.length || '?'} scenes (~${data.target_minutes || (mode === 'preview' ? 1 : '?')} min)`;
            }
            if (dl) {
                dl.href = `/api/storyboard/jobs/${_sbJobId}/download`;
                dl.download = '';
                dl.textContent = mode === 'preview' ? 'Download preview zip' : 'Download pack zip';
            }
            if (box) box.classList.remove('hidden');
            if (continueBtn) continueBtn.classList.toggle('hidden', mode !== 'preview');
            const gotoAssemble = document.getElementById('btn-sb-goto-assemble');
            if (gotoAssemble) gotoAssemble.classList.toggle('hidden', !cookReady);
            _sbSyncBoardCookControls();
            track('storyboard_pack_ready', { job_id: _sbJobId, beat_count: data.beat_count, pack_mode: mode });
        } else if (data.status === 'error' || data.status === 'cancelled') {
            if (_sbPollTimer) { clearInterval(_sbPollTimer); _sbPollTimer = null; }
            _sbSetPackLoading(false);
            if (continueBtn) continueBtn.classList.add('hidden');
            document.getElementById('btn-sb-goto-assemble')?.classList.add('hidden');
            alert(data.error || 'Storyboard pack failed');
        }
    } catch (e) {
        console.warn('storyboard poll', e);
    }
}

