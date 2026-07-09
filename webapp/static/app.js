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
                } else if (typeof showPricingModal === 'function') {
                    showPricingModal({ reason: 'cook' });
                }
            }
        }
    } catch (_) { /* ignore */ }
    return res;
};

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
        cfg = await res.json();
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
                    ],
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

    if (page === 'history') { try { loadHistory(); } catch(_) {} }
    if (page === 'billing') { try { loadBillingPage(); } catch(_) {} }

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

    if (n >= 2) persistPipelineState();
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
        if (!ensureSignedIn()) return;
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
        if (!ensureSignedIn()) return;
        state.script = document.getElementById('script-editor').value.trim();
        if (!state.script) return;
        goToStep(4);
        loadVoices();
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
        voice: 'leo', targetMinutes: 8, voiceoverPath: '', voiceoverUrl: '',
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
    setTimeout(() => goToStep(2), 300);
}

function isPaidUser() {
    return currentUser && ['pro', 'starter', 'daily', 'starter_trial', 'daily_trial'].includes(currentUser.plan);
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
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
    setPricingPlan('monthly');

    const topupRow = document.getElementById('topup-row');
    if (topupRow) {
        if (isPaidUser() && !isTrialUser()) { topupRow.classList.remove('hidden'); }
        else { topupRow.classList.add('hidden'); }
    }

    // Returning users who already used a trial pay immediately — update CTA copy
    const usedTrial = !!currentUser.trial_used;
    const starterBtn = document.getElementById('pricing-cta-starter');
    const dailyBtn = document.getElementById('pricing-cta-daily');
    const subtitle = document.getElementById('pricing-subtitle');
    const heading = modal.querySelector('h2.cr-display');
    const ctaText = usedTrial ? 'Subscribe now' : 'Start free trial';
    if (starterBtn) starterBtn.textContent = ctaText;
    if (dailyBtn) dailyBtn.textContent = ctaText;

    if (opts.reason === 'cook' && !usedTrial) {
        if (heading) heading.textContent = 'Your video is ready to cook';
        if (subtitle) subtitle.textContent = 'Start your free trial to cook this video — 3 videos included.';
    } else if (usedTrial) {
        if (heading) heading.textContent = 'Choose your plan';
        if (subtitle) subtitle.textContent = 'Your free trial was already used. Subscribe to keep creating.';
    } else {
        if (heading) heading.textContent = 'Choose your plan';
        if (subtitle) subtitle.textContent = '7-day free trial on any plan. Cancel anytime.';
    }

    track('upgrade_viewed', { reason: opts.reason || 'general' });
}

function hidePricingModal() {
    const modal = document.getElementById('pricing-modal');
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
        if (sub) sub.textContent = 'Your free trial is live. 3 videos ready to cook.';
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
    if (!welcome) return;
    // Clean URL without losing hash
    const hash = window.location.hash || '#pipeline';
    window.history.replaceState({}, '', window.location.pathname + hash);
    // Wait a beat for auth/UI to settle, then celebrate
    setTimeout(() => {
        if (welcome === 'trial') showCelebration('trial');
        else if (welcome === 'upgrade') showCelebration('upgrade');
        else showCelebration('subscribe');
    }, 400);
}

function showTrialExhaustedModal() {
    const existing = document.getElementById('trial-exhausted-modal');
    if (existing) { existing.style.display = 'flex'; return; }

    const tierLabel = currentUser.plan === 'daily_trial' ? 'Daily' : 'Starter';
    const credits = currentUser.plan === 'daily_trial' ? 35 : 15;
    const price = currentUser.plan === 'daily_trial' ? '$49' : '$27';

    const modal = document.createElement('div');
    modal.id = 'trial-exhausted-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:200;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.7);';
    modal.innerHTML = `
        <div style="background:var(--bg-card,#1a1a2e);border-radius:16px;padding:32px;max-width:420px;width:90%;text-align:center;position:relative;">
            <button onclick="hideTrialExhaustedModal()" style="position:absolute;top:12px;right:16px;background:none;border:none;color:var(--text-secondary,#aaa);font-size:20px;cursor:pointer;">&times;</button>
            <div style="font-size:40px;margin-bottom:12px;">🎬</div>
            <h3 style="margin:0 0 8px;color:var(--text-primary,#fff);font-size:20px;">You've used your 3 trial videos</h3>
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
async function loadVoices() {
    try {
        const res = await fetch('/api/voices');
        const voices = await res.json();
        const grid = document.getElementById('voices-grid');
        grid.innerHTML = '';
        // Default to Leo (Atlas narrator) if current voice is a legacy Gemini name
        const legacy = ['Charon', 'Kore', 'Gacrux', 'Schedar', 'Puck', 'Sulafat'];
        if (!state.voice || legacy.includes(state.voice)) {
            const def = voices.find(v => v.default) || voices[0];
            if (def) state.voice = def.id;
        }
        voices.forEach(v => {
            const card = document.createElement('div');
            card.className = `voice-card${v.id === state.voice ? ' selected' : ''}`;
            const recommended = v.default
                ? `<span style="font-family:var(--font-mono);font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:var(--accent);background:var(--accent-soft-dark);border-radius:var(--radius-pill);padding:2px 7px;">Best pick</span>`
                : '';
            const gender = v.gender ? `<span style="font-family:var(--font-mono);font-size:10px;color:var(--app-ink-3);text-transform:uppercase;">${v.gender}</span>` : '';
            card.innerHTML = `
                <button class="play-btn" data-voice="${v.id}" title="Preview voice">
                    <svg width="13" height="13" viewBox="0 0 14 14"><path d="M4 2.5 L4 11.5 L11 7 Z" fill="currentColor"/></svg>
                </button>
                <div class="flex-1 min-w-0">
                    <div style="display:flex;align-items:center;gap:8px;font-family:var(--font-body);font-weight:600;font-size:15px;color:var(--app-ink);">
                        ${v.name} ${recommended} ${gender}
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
                persistPipelineState();
            });
            card.querySelector('.play-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                previewVoice(v.id, e.currentTarget, v.preview_url);
            });
            grid.appendChild(card);
        });
    } catch (e) {
        console.error('Failed to load voices:', e);
    }
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
    if (!ensureSignedIn(handleVoiceNext)) return;
    const btn = document.getElementById('btn-next-4');
    setLoading(btn, true);

    const isUpload = state.voiceMode === 'upload' && state.uploadedVoPath;

    if (isUpload) {
        state.voiceoverPath = state.uploadedVoPath;
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
        track('voiceover_generated', { voice: state.voice, recipe: state.niche });

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

function _friendlyProgress(raw) {
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
    if (/Assembling video/i.test(raw)) return 'Building your video...';
    if (/Concatenat/i.test(raw)) return 'Building your video...';
    if (/concepts? planned/i.test(raw)) return 'Scenes planned';
    if (/Style ref/i.test(raw)) return 'Art style ready';
    if (/Got \d+ words/i.test(raw)) return 'Script analyzed';
    if (/illustrations? generated/i.test(raw)) return 'All artwork ready';
    if (/images? prepared/i.test(raw)) return 'Images ready';
    if (/clips? rendered/i.test(raw)) return 'Almost there...';
    if (/Assembly complete/i.test(raw)) return 'Finishing up...';
    if (/Total pipeline/i.test(raw)) return 'Done!';
    if (/Generating thumbnail/i.test(raw)) return 'Creating thumbnails...';
    if (/watermark/i.test(raw)) return 'Finishing up...';
    return raw.replace(/\[.*?\]\s*/g, '').replace(/Step \d\/\d:\s*/g, '').substring(0, 60);
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
                const errMsg = typeof data.detail === 'string' ? data.detail : (data.detail?.message || JSON.stringify(data.detail) || 'Build failed');
                if (res.status === 401) { showAuthModal(); }
                else if (res.status === 402 && isTrialUser()) { showTrialExhaustedModal(); }
                else if (res.status === 402) { showPricingModal({ reason: 'cook' }); }
                else { alert(errMsg); }
                throw new Error(errMsg);
            }
            this.jobId = data.job_id;
            this._persist();
            this._showCookingBar();
            this._connect();
            // Reflect the deducted credit in the UI immediately
            if (currentUser && typeof currentUser.credits === 'number' && currentUser.credits > 0) {
                currentUser.credits -= 1;
                updateAuthUI();
            }
            refreshUserData();
        } catch (e) {
            if (e.message.includes('Sign in')) showAuthModal();
            document.getElementById('build-start').classList.remove('hidden');
            document.getElementById('build-progress').classList.add('hidden');
        }
    },

    _connect() {
        // Reset per-connection state so a reconnect replay doesn't duplicate the
        // log or inflate the progress bar (the server replays from the start).
        this.msgCount = 0;
        this.evtSrc = new EventSource(`/api/build/${this.jobId}/progress`);
        const progressBar = document.getElementById('progress-bar');
        const progressLog = document.getElementById('progress-log');
        if (progressLog) progressLog.innerHTML = '';

        this.evtSrc.addEventListener('progress', (e) => {
            this.msgCount++;
            const msg = JSON.parse(e.data);
            const friendly = _friendlyProgress(msg.message);
            document.getElementById('cooking-bar-status').textContent = friendly.substring(0, 60);
            if (progressLog) {
                const line = document.createElement('div');
                line.textContent = `> ${friendly}`;
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
            this._clear();
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
            // Explicit server-sent error event → the render genuinely failed.
            if (e && e.data) {
                let err = 'Unknown error';
                try { err = JSON.parse(e.data).error || err; } catch (_) {}
                try { this.evtSrc && this.evtSrc.close(); } catch (_) {}
                this.evtSrc = null;
                this.jobId = null;
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
            localStorage.setItem('cr_active_job', JSON.stringify({ jobId: this.jobId, title: this.title }));
        } catch (_) {}
    },

    _clear() {
        try { localStorage.removeItem('cr_active_job'); } catch (_) {}
    },

    // Re-attach to an in-flight (or just-finished) render after a page refresh.
    async restore() {
        let saved;
        try { saved = JSON.parse(localStorage.getItem('cr_active_job') || 'null'); } catch (_) { saved = null; }
        if (!saved || !saved.jobId) return;
        this.jobId = saved.jobId;
        this.title = saved.title || 'your video';
        this._reattach();
    },

    // Check the job's real state, then either finish, stop, or reconnect the
    // live stream. Shared by page-load restore and transient SSE reconnects.
    async _reattach() {
        if (!this.jobId || this.result) return;
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
            this.jobId = null;
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
            if (state.page === 'pipeline' && state.step === 6) {
                showUploadKit(data);
            } else {
                this._showToast();
            }
            return;
        }
        if (data && (data.status === 'error' || data.status === 'cancelled')) {
            this.jobId = null;
            this._clear();
            this._hideCookingBar();
            return;
        }
        // Still running / queued — show the bar and reconnect the live stream.
        this._showCookingBar();
        this._connect();
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
        this._clear();
        this._hideCookingBar();
        if (state.page === 'pipeline' && state.step === 6) {
            document.getElementById('build-start').classList.remove('hidden');
            document.getElementById('build-progress').classList.add('hidden');
        }
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
    const btn = document.getElementById('btn-build');
    if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; }
    try {
        await cookingManager.start();
    } finally {
        if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
    }
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
    const btn = document.querySelector('#ss-channel-result .btn-primary');
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
                analysis: state.channelAnalysis || '',
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
const RECIPE_LABELS = {
    animated_explainer: 'Animated Explainer',
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
    return `
    <div class="video-card" id="video-card-${v.id}">
        <div class="video-thumb">${thumb}<span class="video-badge">${esc(recipe)}</span></div>
        <div class="video-body">
            <div class="video-title" title="${esc(v.title)}">${esc(v.title)}</div>
            <div class="video-meta"><span>${date}</span>${expiry}</div>
            <div class="video-actions">
                <a class="video-btn video-btn-primary" href="${esc(v.url)}" download="${esc(v.title)}.mp4" target="_blank" rel="noopener">Download</a>
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

async function refreshUserData() {
    try {
        const res = await _origFetch('/api/auth/me');
        const data = await res.json();
        if (data.user) {
            currentUser = data.user;
            updateAuthUI();
        }
    } catch (e) {
        console.error('Refresh user data failed:', e);
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
            document.getElementById('credits-count').textContent = currentUser.credits + ' of 3 trial';
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
    } else {
        loginBtn.classList.remove('hidden');
        userBtn.classList.add('hidden');
        const cc = document.getElementById('credits-count');
        const cp = document.getElementById('credits-plan');
        if (cc) cc.textContent = '3 free';
        if (cp) cp.textContent = 'trial';
        creditsDisplay.classList.remove('hidden');
        if (navUpgrade) navUpgrade.classList.add('hidden');
        if (cookingUpgrade) cookingUpgrade.classList.add('hidden');
        try { window.Sentry?.setUser(null); } catch (_) {}
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
        document.getElementById('billing-credits').textContent = currentUser.credits + ' of 3 trial videos';
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
