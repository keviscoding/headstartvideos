/**
 * Ranking & Countdown short-form client flow (ChannelRecipe UI).
 * Depends on globals from app.js: goToStep, cookingManager, readJson,
 * ensureCanCook / openUpgradeFlow / endTrialNow / isTrialUser / showPricingModal, etc.
 */
/* global goToStep, cookingManager, readJson, isTrialUser, isPaidUser,
          showPricingModal, showTrialExhaustedModal, showCreditsNeededModal,
          openUpgradeFlow, endTrialNow, setButtonLoading, currentUser, track */

let _rkClips = [];
let _rkTrimIdx = 0;
let _rkStyle = 'viral';
let _rkAccess = null;

function isRankingRecipe() {
    const id = (window.state?.nicheData?.recipe || window.state?.niche || '');
    return id === 'ranking_countdown';
}

function rkResetState() {
    _rkClips = [];
    _rkTrimIdx = 0;
    _rkStyle = 'viral';
    try { localStorage.removeItem('cr_ranking_draft'); } catch (_) {}
}

function rkSaveDraft() {
    try {
        localStorage.setItem('cr_ranking_draft', JSON.stringify({
            clips: _rkClips.map((c) => ({
                filename: c.filename,
                url: c.url,
                browserUrl: c.browserUrl,
                duration: c.duration,
                originalDuration: c.originalDuration,
                startTime: c.startTime,
                endTime: c.endTime,
                label: c.label,
            })),
            style: _rkStyle,
            title: document.getElementById('rk-title-text')?.value || '',
            highlight: document.getElementById('rk-title-hl')?.value || '',
        }));
    } catch (_) {}
}

function rkInitUploadUI() {
    const zone = document.getElementById('rk-upload-zone');
    const input = document.getElementById('rk-file-input');
    if (!zone || zone.dataset.bound) return;
    zone.dataset.bound = '1';
    zone.addEventListener('click', () => input?.click());
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('is-drag'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('is-drag'));
    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('is-drag');
        if (e.dataTransfer?.files?.length) rkHandleFiles(e.dataTransfer.files);
    });
    input?.addEventListener('change', () => {
        if (input.files?.length) rkHandleFiles(input.files);
        input.value = '';
    });
    rkRenderClipList();
    rkRefreshAccess();
}

async function rkHandleFiles(fileList) {
    const files = [...fileList].filter((f) => f.type.startsWith('video/') || /\.(mp4|mov|webm|mkv|m4v)$/i.test(f.name));
    for (const file of files) {
        const placeholder = {
            filename: '',
            url: '',
            browserUrl: '',
            duration: 0,
            originalDuration: 0,
            startTime: 0,
            endTime: 0,
            label: file.name.replace(/\.[^.]+$/, '').slice(0, 40),
            uploading: true,
        };
        _rkClips.push(placeholder);
        rkRenderClipList();
        try {
            const fd = new FormData();
            fd.append('file', file);
            const res = await fetch('/api/ranking/upload', { method: 'POST', body: fd });
            const data = await readJson(res, {});
            if (!res.ok) throw new Error(data.detail || data.message || 'Upload failed');
            Object.assign(placeholder, {
                filename: data.filename,
                url: data.url,
                browserUrl: data.browser_url || data.url,
                duration: data.duration || 0,
                originalDuration: data.duration || 0,
                startTime: 0,
                endTime: data.duration || 0,
                uploading: false,
            });
        } catch (e) {
            _rkClips = _rkClips.filter((c) => c !== placeholder);
            alert(e.message || 'Upload failed');
        }
        rkRenderClipList();
        rkSaveDraft();
    }
}

function rkRenderClipList() {
    const list = document.getElementById('rk-clip-list');
    const next = document.getElementById('rk-btn-to-trim');
    if (!list) return;
    const n = _rkClips.length;
    list.innerHTML = _rkClips.map((c, i) => {
        const num = n - i;
        const thumb = c.browserUrl
            ? `<video class="rk-clip-thumb" src="${c.browserUrl}" muted></video>`
            : `<div class="rk-clip-thumb"></div>`;
        return `<div class="rk-clip-item">
            <span class="rk-clip-num">${num}</span>
            ${thumb}
            <div style="flex:1;min-width:0;">
                <div style="font-weight:600;font-size:14px;">${c.uploading ? 'Uploading…' : (c.label || c.filename)}</div>
                <div class="cr-mono" style="font-size:11px;color:var(--app-ink-3);">${(c.duration || 0).toFixed(1)}s</div>
            </div>
            <button type="button" class="btn-ghost" style="font-size:12px;padding:6px 10px;" onclick="rkRemoveClip(${i})">Remove</button>
        </div>`;
    }).join('');
    if (next) next.disabled = _rkClips.filter((c) => !c.uploading && c.url).length < 1;
}

function rkRemoveClip(i) {
    _rkClips.splice(i, 1);
    rkRenderClipList();
    rkSaveDraft();
}

function rkGoTrim() {
    const ready = _rkClips.filter((c) => !c.uploading && c.url);
    if (!ready.length) { alert('Upload at least one clip.'); return; }
    _rkClips = ready;
    _rkTrimIdx = 0;
    goToStep('rk-trim');
    rkShowTrimClip();
}

function rkShowTrimClip() {
    const clip = _rkClips[_rkTrimIdx];
    const video = document.getElementById('rk-trim-video');
    const meta = document.getElementById('rk-trim-meta');
    const inEl = document.getElementById('rk-trim-in');
    const outEl = document.getElementById('rk-trim-out');
    const labelEl = document.getElementById('rk-trim-label');
    if (!clip || !video) return;
    if (meta) meta.textContent = `Clip ${_rkTrimIdx + 1} of ${_rkClips.length} · will be #${_rkClips.length - _rkTrimIdx}`;
    video.src = clip.browserUrl || clip.url;
    if (inEl) inEl.value = String(clip.startTime || 0);
    if (outEl) outEl.value = String(clip.endTime || clip.duration || 0);
    if (labelEl) labelEl.value = clip.label || '';
    const prev = document.getElementById('rk-btn-trim-prev');
    const next = document.getElementById('rk-btn-trim-next');
    if (prev) prev.disabled = _rkTrimIdx <= 0;
    if (next) next.textContent = _rkTrimIdx >= _rkClips.length - 1 ? 'Next: Title' : 'Next clip';
}

function rkCommitTrimFields() {
    const clip = _rkClips[_rkTrimIdx];
    if (!clip) return;
    const inEl = document.getElementById('rk-trim-in');
    const outEl = document.getElementById('rk-trim-out');
    const labelEl = document.getElementById('rk-trim-label');
    let start = Math.max(0, parseFloat(inEl?.value) || 0);
    let end = parseFloat(outEl?.value);
    if (!Number.isFinite(end) || end <= start) end = clip.originalDuration || clip.duration || start + 1;
    clip.startTime = start;
    clip.endTime = end;
    clip.duration = Math.max(0.3, end - start);
    clip.label = (labelEl?.value || clip.label || '').trim();
    rkSaveDraft();
}

function rkTrimPrev() {
    rkCommitTrimFields();
    if (_rkTrimIdx > 0) {
        _rkTrimIdx -= 1;
        rkShowTrimClip();
    }
}

function rkTrimNext() {
    rkCommitTrimFields();
    if (_rkTrimIdx >= _rkClips.length - 1) {
        goToStep('rk-title');
        rkRenderOrderList();
        rkRefreshAccess();
        return;
    }
    _rkTrimIdx += 1;
    rkShowTrimClip();
}

function rkSetStyle(style) {
    _rkStyle = style === 'classic' ? 'classic' : 'viral';
    document.querySelectorAll('.rk-style-btn').forEach((b) => {
        b.classList.toggle('is-active', b.dataset.style === _rkStyle);
    });
    rkSaveDraft();
}

function rkRenderOrderList() {
    const list = document.getElementById('rk-order-list');
    if (!list) return;
    const n = _rkClips.length;
    list.innerHTML = _rkClips.map((c, i) => {
        const num = n - i;
        return `<div class="rk-order-item">
            <span class="rk-clip-num">${num}</span>
            <div style="flex:1;min-width:0;font-size:14px;font-weight:600;">${c.label || ('Clip ' + (i + 1))}</div>
            <button type="button" class="btn-ghost" style="font-size:12px;padding:4px 8px;" onclick="rkMoveClip(${i},-1)" ${i === 0 ? 'disabled' : ''}>↑</button>
            <button type="button" class="btn-ghost" style="font-size:12px;padding:4px 8px;" onclick="rkMoveClip(${i},1)" ${i >= n - 1 ? 'disabled' : ''}>↓</button>
        </div>`;
    }).join('');
}

function rkMoveClip(i, dir) {
    const j = i + dir;
    if (j < 0 || j >= _rkClips.length) return;
    const tmp = _rkClips[i];
    _rkClips[i] = _rkClips[j];
    _rkClips[j] = tmp;
    rkRenderOrderList();
    rkSaveDraft();
}

async function rkRefreshAccess() {
    const badge = document.getElementById('rk-trial-badge');
    try {
        const res = await fetch('/api/ranking/access');
        _rkAccess = await readJson(res, null);
        if (!badge || !_rkAccess) return;
        if (_rkAccess.is_trial) {
            const left = _rkAccess.ranking_free_left ?? 0;
            badge.textContent = left > 0
                ? `${left} free ranking short${left === 1 ? '' : 's'} left on your trial`
                : 'Free ranking shorts used — upgrade to keep cooking';
        } else if (_rkAccess.can_cook) {
            badge.textContent = `${_rkAccess.credit_cost || 1} credit per ranking short`;
        } else {
            badge.textContent = 'Start a free trial to cook ranking shorts';
        }
    } catch (_) {
        if (badge) badge.textContent = '';
    }
}

async function rkAssemble() {
    rkCommitTrimFields();
    const titleText = (document.getElementById('rk-title-text')?.value || '').trim();
    if (!titleText) {
        alert('Add a title for your ranking Short.');
        return;
    }
    if (!_rkClips.length) {
        alert('Add clips first.');
        return;
    }

    // Gate: free plan / exhausted trial
    if (_rkAccess && !_rkAccess.can_cook) {
        showPricingModal({ reason: 'cook' });
        return;
    }
    if (_rkAccess?.is_trial && _rkAccess.trial_allowed === false) {
        if (typeof endTrialNow === 'function') endTrialNow();
        else if (typeof openUpgradeFlow === 'function') openUpgradeFlow();
        else showTrialExhaustedModal();
        return;
    }

    const btn = document.getElementById('rk-btn-assemble');
    if (typeof setButtonLoading === 'function') setButtonLoading(btn, true);
    try {
        const n = _rkClips.length;
        const body = {
            clips: _rkClips.map((c, i) => ({
                filename: c.filename,
                url: c.url,
                number: n - i,
                label: c.label || `#${n - i}`,
                startTime: c.startTime || 0,
                endTime: c.endTime || c.duration,
                originalDuration: c.originalDuration || c.duration,
            })),
            title: {
                text: titleText,
                highlightWord: (document.getElementById('rk-title-hl')?.value || '').trim(),
            },
            style_preset: _rkStyle,
            layout: {},
            notify_email: currentUser?.email || '',
        };
        const res = await fetch('/api/ranking/assemble', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await readJson(res, {});
        if (!res.ok) {
            const detail = data.detail;
            const errMsg = typeof detail === 'string' ? detail : (detail?.message || 'Assemble failed');
            const code = detail?.code || '';
            if (res.status === 402 && (code === 'ranking_trial_exhausted' || isTrialUser())) {
                if (typeof endTrialNow === 'function') endTrialNow();
                else showTrialExhaustedModal();
            } else if (res.status === 402 && isPaidUser() && !isTrialUser()) {
                showCreditsNeededModal({ need: detail?.need || 1, have: currentUser?.credits ?? 0, reason: 'credits' });
            } else if (res.status === 402) {
                showPricingModal({ reason: 'cook' });
            } else {
                alert(errMsg);
            }
            throw new Error(errMsg);
        }
        try { track('ranking_assemble_started', { job_id: data.job_id, clips: n }); } catch (_) {}
        goToStep('rk-cook');
        document.getElementById('rk-result-wrap')?.classList.add('hidden');
        if (cookingManager?.adoptStoryboard) {
            cookingManager.adoptStoryboard(data.job_id, titleText);
            cookingManager.kind = 'ranking';
        }
        // Poll until complete for in-panel preview
        rkPollResult(data.job_id);
    } catch (_) {
        /* alerted above */
    } finally {
        if (typeof setButtonLoading === 'function') setButtonLoading(btn, false);
    }
}

async function rkPollResult(jobId) {
    const wrap = document.getElementById('rk-result-wrap');
    const video = document.getElementById('rk-result-video');
    const dl = document.getElementById('rk-result-download');
    for (let i = 0; i < 180; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
            const res = await fetch(`/api/build/${jobId}/progress`);
            // Fallback: cook job endpoint used by cooking bar — try history status via SSE only.
            // Use dedicated job fetch if available
        } catch (_) {}
        try {
            const res = await fetch(`/api/build/${encodeURIComponent(jobId)}/result`);
            if (!res.ok) continue;
            const data = await readJson(res, {});
            const url = data.video_url || data.result?.video_url;
            if (url) {
                if (wrap) wrap.classList.remove('hidden');
                if (video) video.src = url;
                if (dl) { dl.href = url; dl.download = 'ranking-short.mp4'; }
                return;
            }
            if (data.status === 'error' || data.status === 'cancelled') return;
        } catch (_) {}
    }
}

function rkStartOver() {
    rkResetState();
    rkRenderClipList();
    goToStep('rk-upload');
    rkInitUploadUI();
}

// Expose for onclick handlers
window.isRankingRecipe = isRankingRecipe;
window.rkResetState = rkResetState;
window.rkInitUploadUI = rkInitUploadUI;
window.rkRemoveClip = rkRemoveClip;
window.rkGoTrim = rkGoTrim;
window.rkTrimPrev = rkTrimPrev;
window.rkTrimNext = rkTrimNext;
window.rkSetStyle = rkSetStyle;
window.rkMoveClip = rkMoveClip;
window.rkAssemble = rkAssemble;
window.rkStartOver = rkStartOver;
window.rkRefreshAccess = rkRefreshAccess;
