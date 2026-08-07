/**
 * Ranking & Countdown short-form client flow (ChannelRecipe UI).
 * Depends on globals from app.js: goToStep, cookingManager, readJson, etc.
 */
/* global goToStep, cookingManager, readJson, isTrialUser, isPaidUser,
          showPricingModal, showTrialExhaustedModal, showCreditsNeededModal,
          openUpgradeFlow, endTrialNow, setButtonLoading, currentUser, track */

let _rkClips = [];
let _rkTrimIdx = 0;
let _rkStyle = 'viral';
let _rkAssembling = false;
let _rkAccess = null;
let _rkTimelineDuration = 0;
let _rkDragging = null; // 'start' | 'end' | null
let _rkTimelineBound = false;
let _rkPlayBound = false;
let _rkColorPalette = 'yellow';
let _rkCheckered = false;
let _rkCommentary = false;
let _rkSubtitleColor = 'yellow';
let _rkPreviewActive = 0;
let _rkLayout = { listX: 5, titleY: 6, titleSize: 48, lineSpacing: 65, numSize: 50 };

const RK_STYLE_PRESETS = {
    viral: {
        colorPalette: 'yellow', checkeredMode: false,
        layout: { listX: 5, titleY: 4, titleSize: 52, lineSpacing: 65, numSize: 50 },
        subtitleFont: 'Arial', subtitleY: 50, subtitleColor: 'yellow',
    },
    classic: {
        colorPalette: 'yellow', checkeredMode: false,
        layout: { listX: 5, titleY: 6, titleSize: 48, lineSpacing: 65, numSize: 50 },
        subtitleFont: 'Arial', subtitleY: 55, subtitleColor: 'yellow',
    },
    bold: {
        colorPalette: 'orange', checkeredMode: false,
        layout: { listX: 4, titleY: 5, titleSize: 58, lineSpacing: 72, numSize: 62 },
        subtitleFont: 'Impact', subtitleY: 50, subtitleColor: 'yellow',
    },
    minimal: {
        colorPalette: 'white', checkeredMode: false,
        layout: { listX: 8, titleY: 8, titleSize: 40, lineSpacing: 58, numSize: 42 },
        subtitleFont: 'Arial', subtitleY: 72, subtitleColor: 'white',
    },
    checkered: {
        colorPalette: 'cyan', checkeredMode: true,
        layout: { listX: 5, titleY: 6, titleSize: 48, lineSpacing: 68, numSize: 52 },
        subtitleFont: 'Verdana', subtitleY: 58, subtitleColor: 'cyan',
    },
};

function isRankingRecipe() {
    const id = (window.state?.nicheData?.recipe || window.state?.niche || '');
    return id === 'ranking_countdown';
}

function rkEscapeHtml(s) {
    return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function rkResetState() {
    _rkClips = [];
    _rkTrimIdx = 0;
    _rkStyle = 'viral';
    _rkColorPalette = 'yellow';
    _rkCheckered = false;
    _rkCommentary = false;
    _rkSubtitleColor = 'yellow';
    _rkPreviewActive = 0;
    _rkLayout = { listX: 5, titleY: 6, titleSize: 48, lineSpacing: 65, numSize: 50 };
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
            layout: _rkLayout,
            colorPalette: _rkColorPalette,
            checkered: _rkCheckered,
            commentary: _rkCommentary,
            subtitleColor: _rkSubtitleColor,
            title: document.getElementById('rk-title-text')?.value || '',
            highlight: document.getElementById('rk-title-hl')?.value || '',
        }));
    } catch (_) {}
}

function rkLayoutPayload() {
    return {
        listXPercent: _rkLayout.listX,
        titleYPercent: _rkLayout.titleY,
        titleFontSize: _rkLayout.titleSize,
        lineSpacing: _rkLayout.lineSpacing,
        numSize: _rkLayout.numSize,
    };
}

function rkSyncLayoutSliders() {
    const map = {
        'list-x': ['listX', '%'],
        'title-y': ['titleY', '%'],
        'title-size': ['titleSize', ''],
        'line-spacing': ['lineSpacing', ''],
        'num-size': ['numSize', ''],
    };
    Object.entries(map).forEach(([suffix, [key, unit]]) => {
        const el = document.getElementById('rk-pos-' + suffix);
        const val = document.getElementById('rk-pos-' + suffix + '-val');
        if (el) el.value = _rkLayout[key];
        if (val) val.textContent = unit ? `${_rkLayout[key]}${unit}` : String(_rkLayout[key]);
    });
}

function rkOnLayoutChange(key, value) {
    _rkLayout[key] = parseInt(value, 10);
    const unit = (key === 'listX' || key === 'titleY') ? '%' : '';
    const idMap = {
        listX: 'rk-pos-list-x-val',
        titleY: 'rk-pos-title-y-val',
        titleSize: 'rk-pos-title-size-val',
        lineSpacing: 'rk-pos-line-spacing-val',
        numSize: 'rk-pos-num-size-val',
    };
    const valEl = document.getElementById(idMap[key]);
    if (valEl) valEl.textContent = unit ? `${_rkLayout[key]}${unit}` : String(_rkLayout[key]);
    rkSaveDraft();
    rkRenderPreview('rk-preview-dash');
}

function rkApplyPreset(id) {
    const p = RK_STYLE_PRESETS[id];
    if (!p) return;
    _rkStyle = id;
    _rkColorPalette = p.colorPalette;
    _rkCheckered = !!p.checkeredMode;
    _rkSubtitleColor = p.subtitleColor;
    _rkLayout = { ...p.layout };
    document.querySelectorAll('#rk-style-presets .rk-style-btn').forEach((b) => {
        b.classList.toggle('is-active', b.dataset.style === id);
    });
    document.querySelectorAll('#rk-color-swatches .rk-color-swatch').forEach((b) => {
        b.classList.toggle('is-active', b.dataset.color === _rkColorPalette);
    });
    document.querySelectorAll('#rk-sub-color-swatches .rk-color-swatch').forEach((b) => {
        b.classList.toggle('is-active', b.dataset.color === _rkSubtitleColor);
    });
    const check = document.getElementById('rk-checkered-toggle');
    if (check) check.checked = _rkCheckered;
    const fontEl = document.getElementById('rk-subtitle-font');
    if (fontEl) fontEl.value = p.subtitleFont;
    const subY = document.getElementById('rk-subtitle-y');
    const subYVal = document.getElementById('rk-subtitle-y-val');
    if (subY) subY.value = p.subtitleY;
    if (subYVal) subYVal.textContent = p.subtitleY + '%';
    rkSyncLayoutSliders();
    rkSaveDraft();
    rkRenderPreview('rk-preview-dash');
    rkRenderPreview('rk-preview-trim');
    rkUpdateAssembleLabel();
}

function rkSetStyle(style) {
    rkApplyPreset(style === 'classic' ? 'classic' : 'viral');
}

function rkSetColor(color) {
    _rkColorPalette = color;
    document.querySelectorAll('#rk-color-swatches .rk-color-swatch').forEach((b) => {
        b.classList.toggle('is-active', b.dataset.color === color);
    });
    rkSaveDraft();
    rkRenderPreview('rk-preview-dash');
}

function rkSetSubColor(color) {
    _rkSubtitleColor = color;
    document.querySelectorAll('#rk-sub-color-swatches .rk-color-swatch').forEach((b) => {
        b.classList.toggle('is-active', b.dataset.color === color);
    });
    rkSaveDraft();
    rkRenderPreview('rk-preview-dash');
}

function rkSetCheckered(on) {
    _rkCheckered = !!on;
    rkSaveDraft();
    rkRenderPreview('rk-preview-dash');
}

function rkSetCommentary(on) {
    _rkCommentary = !!on;
    const vp = document.getElementById('rk-voice-picker');
    const ss = document.getElementById('rk-subtitle-settings');
    if (vp) vp.style.display = on ? '' : 'none';
    if (ss) ss.style.display = on ? '' : 'none';
    rkSaveDraft();
    rkUpdateAssembleLabel();
    rkRenderPreview('rk-preview-dash');
}

function rkOnSubtitleY(v) {
    const val = document.getElementById('rk-subtitle-y-val');
    if (val) val.textContent = v + '%';
    rkSaveDraft();
    rkRenderPreview('rk-preview-dash');
}

function rkCyclePreviewClip() {
    const n = _rkClips.filter((c) => !c.uploading && !c.downloading && !c.importFailed).length;
    if (n < 1) return;
    _rkPreviewActive = (_rkPreviewActive + 1) % n;
    rkRenderPreview('rk-preview-dash');
}

function rkFormatCredits(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return '1';
    if (Math.abs(x - Math.round(x)) < 1e-9) return String(Math.round(x));
    return x.toFixed(1).replace(/\.0$/, '');
}

function rkCookCreditTotal() {
    if (!_rkAccess) return _rkCommentary ? 1 : 0.5;
    if (_rkAccess.is_trial && (_rkAccess.ranking_free_left ?? 0) > 0) return 0;
    if (_rkCommentary) {
        const c = Number(_rkAccess.credit_cost_commentary);
        if (Number.isFinite(c)) return c;
        const base = Number(_rkAccess.credit_cost) || 0.5;
        const extra = Number(_rkAccess.commentary_credit_cost);
        return base + (Number.isFinite(extra) ? extra : 0.5);
    }
    const base = Number(_rkAccess.credit_cost);
    return Number.isFinite(base) ? base : 0.5;
}

function rkUpdateAssembleLabel() {
    const btn = document.getElementById('rk-btn-assemble');
    const text = btn?.querySelector('.btn-text');
    if (!text) return;
    if (_rkAccess?.is_trial && (_rkAccess.ranking_free_left ?? 0) > 0) {
        text.textContent = 'Cook ranking short (trial)';
        return;
    }
    if (_rkAccess?.is_trial) {
        text.textContent = 'Cook ranking short';
        return;
    }
    const total = rkCookCreditTotal();
    const label = rkFormatCredits(total);
    text.textContent = `Cook ranking short (${label} credit${total === 1 ? '' : 's'})`;
}

function rkParseImportUrls(raw) {
    const found = String(raw || '').match(/https?:\/\/[^\s<>"']+/gi) || [];
    const seen = {};
    const out = [];
    for (const u0 of found) {
        const u = u0.replace(/[),.;]+$/g, '');
        if (seen[u]) continue;
        seen[u] = true;
        out.push(u);
    }
    return out;
}

function rkShortUrlLabel(url) {
    try {
        const u = new URL(url);
        const host = u.hostname.replace(/^www\./, '');
        const tail = u.pathname.replace(/\/$/, '').split('/').pop() || '';
        return (tail ? `${host}/${tail}` : host).slice(0, 40);
    } catch (_) {
        return 'Imported clip';
    }
}

function rkSetImportProgress(show, opts = {}) {
    const wrap = document.getElementById('rk-import-progress');
    if (!wrap) return;
    if (!show) {
        wrap.classList.add('hidden');
        return;
    }
    wrap.classList.remove('hidden');
    const done = opts.done || 0;
    const total = opts.total || 0;
    const label = document.getElementById('rk-import-progress-label');
    const count = document.getElementById('rk-import-progress-count');
    const fill = document.getElementById('rk-import-progress-fill');
    const hint = document.getElementById('rk-import-progress-hint');
    if (label) label.textContent = opts.label || 'Downloading…';
    if (count) count.textContent = `${done} / ${total}`;
    if (fill) fill.style.width = total ? `${Math.round((done / total) * 100)}%` : '0%';
    if (hint && opts.hint) hint.textContent = opts.hint;
}

function rkInitUploadUI() {
    const zone = document.getElementById('rk-upload-zone');
    const input = document.getElementById('rk-file-input');
    if (!zone || zone.dataset.bound) {
        rkRenderClipList();
        rkRefreshAccess();
        return;
    }
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
    const urlInput = document.getElementById('rk-url-input');
    urlInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            rkImportUrls();
        }
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

async function rkImportUrls() {
    const input = document.getElementById('rk-url-input');
    const btn = document.getElementById('rk-btn-import-url');
    const status = document.getElementById('rk-url-status');
    const urls = rkParseImportUrls(input?.value || '');
    if (!urls.length) {
        if (status) {
            status.classList.remove('hidden');
            status.className = 'rk-url-status is-err';
            status.textContent = 'Paste one or more http(s) links first.';
        }
        return;
    }
    const readyCount = _rkClips.filter((c) => !c.importFailed).length;
    if (readyCount >= 10) {
        alert('Maximum 10 clips reached');
        return;
    }
    let room = 10 - readyCount;
    const batch = urls.slice(0, room);

    if (btn) btn.disabled = true;
    if (input) input.disabled = true;
    if (status) status.classList.add('hidden');

    const placeholders = [];
    for (let p = 0; p < batch.length; p++) {
        const ph = {
            downloading: true,
            originalName: rkShortUrlLabel(batch[p]),
            importUrl: batch[p],
            filename: '',
            url: '',
            browserUrl: '',
            duration: 0,
            originalDuration: 0,
            startTime: 0,
            endTime: 0,
            label: '',
        };
        _rkClips.push(ph);
        placeholders.push(_rkClips.length - 1);
    }
    rkRenderClipList();

    let ok = 0;
    let fail = 0;
    const startedAll = Date.now();
    rkSetImportProgress(true, {
        done: 0,
        total: batch.length,
        label: 'Downloading clip 1 of ' + batch.length + '…',
        hint: 'Still working — TikTok/YouTube imports often take 20–90 seconds each.',
    });

    for (let i = 0; i < batch.length; i++) {
        const idx = placeholders[i];
        const elapsedAll = Math.round((Date.now() - startedAll) / 1000);
        if (btn) btn.textContent = `Downloading ${i + 1}/${batch.length}…`;
        rkSetImportProgress(true, {
            done: i,
            total: batch.length,
            label: `Downloading clip ${i + 1} of ${batch.length}…`,
            hint: `Elapsed ${elapsedAll}s · keep this tab open.`,
        });
        try {
            await rkImportOneUrl(batch[i], idx);
            ok++;
        } catch (err) {
            fail++;
            const failed = _rkClips[idx];
            if (failed) {
                failed.downloading = false;
                failed.importFailed = true;
                failed.importError = err.message || 'Import failed';
                failed.originalName = rkShortUrlLabel(batch[i]);
            }
            rkRenderClipList();
        }
        rkSetImportProgress(true, {
            done: i + 1,
            total: batch.length,
            label: i + 1 < batch.length
                ? `Downloaded ${i + 1} of ${batch.length} — starting next…`
                : `Finished ${i + 1} of ${batch.length}`,
            hint: fail
                ? `${ok} imported, ${fail} failed so far.`
                : 'Previews appear below as each download completes.',
        });
    }

    if (input) {
        input.value = '';
        input.disabled = false;
    }
    if (btn) {
        btn.disabled = false;
        btn.textContent = 'Import links';
    }

    const totalSec = Math.round((Date.now() - startedAll) / 1000);
    if (status) {
        status.classList.remove('hidden');
        if (ok && !fail) {
            status.className = 'rk-url-status is-ok';
            status.textContent = `Imported ${ok} clip${ok === 1 ? '' : 's'} in ${totalSec}s.`;
            setTimeout(() => rkSetImportProgress(false), 3500);
        } else if (ok && fail) {
            status.className = 'rk-url-status';
            status.textContent = `Imported ${ok}, failed ${fail}.`;
        } else {
            rkSetImportProgress(false);
            status.className = 'rk-url-status is-err';
            status.textContent = 'All imports failed — try uploading the files instead.';
        }
    } else if (ok && !fail) {
        setTimeout(() => rkSetImportProgress(false), 3500);
    }
    rkSaveDraft();
}

async function rkImportOneUrl(url, placeholderIndex) {
    const res = await fetch('/api/ranking/import-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
    });
    const data = await readJson(res, {});
    if (!res.ok || !data.success) {
        const detail = data.detail;
        const msg = typeof detail === 'string'
            ? detail
            : (detail?.message || data.message || data.error || 'Import failed');
        throw new Error(msg);
    }
    const clip = _rkClips[placeholderIndex];
    if (!clip) return data;
    Object.assign(clip, {
        filename: data.filename,
        url: data.url,
        browserUrl: data.browser_url || data.url,
        duration: data.duration || 0,
        originalDuration: data.duration || 0,
        startTime: 0,
        endTime: data.duration || 0,
        label: clip.label || data.label_hint || rkShortUrlLabel(url),
        downloading: false,
        uploading: false,
        importFailed: false,
    });
    rkRenderClipList();
    return data;
}

function rkInitDragReorder(listEl, onDrop) {
    let dragItem = null;
    listEl.querySelectorAll('[data-index]').forEach((item) => {
        item.addEventListener('dragstart', (e) => {
            dragItem = item;
            item.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
        });
        item.addEventListener('dragend', () => {
            item.classList.remove('dragging');
            dragItem = null;
        });
        item.addEventListener('dragover', (e) => e.preventDefault());
        item.addEventListener('drop', (e) => {
            e.preventDefault();
            if (!dragItem || dragItem === item) return;
            const from = parseInt(dragItem.dataset.index, 10);
            const to = parseInt(item.dataset.index, 10);
            if (!Number.isFinite(from) || !Number.isFinite(to)) return;
            onDrop(from, to);
        });
    });
}

function rkRenderClipList() {
    const list = document.getElementById('rk-clip-list');
    const next = document.getElementById('rk-btn-to-trim');
    if (!list) return;
    const n = _rkClips.length;
    list.innerHTML = _rkClips.map((c, i) => {
        const num = n - i;
        const thumb = c.browserUrl
            ? `<video class="rk-clip-thumb" src="${rkEscapeHtml(c.browserUrl)}" muted></video>`
            : `<div class="rk-clip-thumb"></div>`;
        let status = '';
        if (c.uploading) status = 'Uploading…';
        else if (c.downloading) status = 'Downloading…';
        else if (c.importFailed) status = c.importError || 'Import failed';
        else status = c.label || c.originalName || c.filename || 'Clip';
        return `<div class="rk-clip-item" draggable="true" data-index="${i}">
            <span class="rk-drag-handle" title="Drag to reorder">⠿</span>
            <span class="rk-clip-num">${num}</span>
            ${thumb}
            <div style="flex:1;min-width:0;">
                <div style="font-weight:600;font-size:14px;">${rkEscapeHtml(status)}</div>
                <div class="cr-mono" style="font-size:11px;color:var(--app-ink-3);">${(c.duration || 0).toFixed(1)}s</div>
            </div>
            <button type="button" class="btn-ghost" style="font-size:12px;padding:6px 10px;" onclick="event.stopPropagation();rkRemoveClip(${i})">${c.importFailed ? 'Dismiss' : 'Remove'}</button>
        </div>`;
    }).join('');
    rkInitDragReorder(list, (from, to) => {
        const moved = _rkClips.splice(from, 1)[0];
        _rkClips.splice(to, 0, moved);
        rkRenderClipList();
        rkSaveDraft();
    });
    if (next) {
        next.disabled = _rkClips.filter((c) => !c.uploading && !c.downloading && !c.importFailed && c.url).length < 1;
    }
}

function rkRemoveClip(i) {
    _rkClips.splice(i, 1);
    rkRenderClipList();
    rkSaveDraft();
}

function rkGoTrim() {
    const ready = _rkClips.filter((c) => !c.uploading && !c.downloading && !c.importFailed && c.url);
    if (!ready.length) { alert('Upload at least one clip.'); return; }
    _rkClips = ready;
    _rkTrimIdx = 0;
    goToStep('rk-trim');
    rkEnsureTimelineBound();
    rkEnsurePlayBound();
    rkShowTrimClip();
}

function rkTimeToPercent(t) {
    return _rkTimelineDuration <= 0 ? 0 : Math.max(0, Math.min(100, (t / _rkTimelineDuration) * 100));
}
function rkPercentToTime(pct) {
    return Math.max(0, Math.min(_rkTimelineDuration, (pct / 100) * _rkTimelineDuration));
}
function rkGetTrackRect() {
    return document.getElementById('rk-timeline-track')?.getBoundingClientRect();
}
function rkXToPercent(clientX) {
    const r = rkGetTrackRect();
    if (!r || !r.width) return 0;
    return Math.max(0, Math.min(100, ((clientX - r.left) / r.width) * 100));
}

function rkUpdateTimelineUI() {
    const clip = _rkClips[_rkTrimIdx];
    if (!clip) return;
    const sp = rkTimeToPercent(clip.startTime || 0);
    const ep = rkTimeToPercent(clip.endTime || clip.duration || 0);
    const fill = document.getElementById('rk-timeline-fill');
    const hs = document.getElementById('rk-handle-start');
    const he = document.getElementById('rk-handle-end');
    if (fill) {
        fill.style.left = sp + '%';
        fill.style.width = Math.max(0, ep - sp) + '%';
    }
    if (hs) hs.style.left = `calc(${sp}% - 7px)`;
    if (he) he.style.left = `calc(${ep}% - 7px)`;
    const sd = document.getElementById('rk-trim-start-display');
    const ed = document.getElementById('rk-trim-end-display');
    const badge = document.getElementById('rk-trim-duration-badge');
    if (sd) sd.textContent = (clip.startTime || 0).toFixed(1) + 's';
    if (ed) ed.textContent = (clip.endTime || 0).toFixed(1) + 's';
    if (badge) badge.textContent = Math.max(0, (clip.endTime || 0) - (clip.startTime || 0)).toFixed(1) + 's selected';
    let total = 0;
    _rkClips.forEach((c) => { total += Math.max(0, (c.endTime || c.duration || 0) - (c.startTime || 0)); });
    const el = document.getElementById('rk-trim-total-duration');
    if (el) el.textContent = total.toFixed(1) + 's';
}

function rkUpdatePlayhead() {
    const v = document.getElementById('rk-trim-video');
    const ph = document.getElementById('rk-timeline-playhead');
    if (!v || !ph) return;
    ph.style.left = `calc(${rkTimeToPercent(v.currentTime || 0)}% - 1.5px)`;
}

function rkRenderTicks() {
    const ticks = document.getElementById('rk-timeline-ticks');
    if (!ticks) return;
    const count = Math.min(10, Math.max(3, Math.floor(_rkTimelineDuration / 5)));
    let html = '';
    for (let i = 0; i <= count; i++) {
        html += `<span>${((_rkTimelineDuration / count) * i).toFixed(1)}s</span>`;
    }
    ticks.innerHTML = html;
}

function rkEnsureTimelineBound() {
    if (_rkTimelineBound) return;
    const track = document.getElementById('rk-timeline-track');
    const hs = document.getElementById('rk-handle-start');
    const he = document.getElementById('rk-handle-end');
    const v = document.getElementById('rk-trim-video');
    if (!track || !hs || !he || !v) return;
    _rkTimelineBound = true;
    v.addEventListener('timeupdate', rkUpdatePlayhead);
    track.addEventListener('click', (e) => {
        if (_rkDragging) return;
        v.currentTime = rkPercentToTime(rkXToPercent(e.clientX));
    });
    hs.addEventListener('mousedown', (e) => { e.preventDefault(); e.stopPropagation(); _rkDragging = 'start'; });
    hs.addEventListener('touchstart', (e) => { e.preventDefault(); e.stopPropagation(); _rkDragging = 'start'; }, { passive: false });
    he.addEventListener('mousedown', (e) => { e.preventDefault(); e.stopPropagation(); _rkDragging = 'end'; });
    he.addEventListener('touchstart', (e) => { e.preventDefault(); e.stopPropagation(); _rkDragging = 'end'; }, { passive: false });
    function onMove(cx) {
        if (!_rkDragging) return;
        const clip = _rkClips[_rkTrimIdx];
        if (!clip) return;
        const t = Math.round(rkPercentToTime(rkXToPercent(cx)) * 10) / 10;
        if (_rkDragging === 'start') {
            clip.startTime = Math.max(0, Math.min(t, (clip.endTime || 0) - 0.5));
            v.currentTime = clip.startTime;
        } else {
            clip.endTime = Math.min(_rkTimelineDuration, Math.max(t, (clip.startTime || 0) + 0.5));
            v.currentTime = clip.endTime;
        }
        clip.duration = Math.max(0.3, (clip.endTime || 0) - (clip.startTime || 0));
        rkUpdateTimelineUI();
        rkSaveDraft();
    }
    document.addEventListener('mousemove', (e) => onMove(e.clientX));
    document.addEventListener('touchmove', (e) => {
        if (_rkDragging && e.touches.length) onMove(e.touches[0].clientX);
    }, { passive: true });
    document.addEventListener('mouseup', () => { _rkDragging = null; });
    document.addEventListener('touchend', () => { _rkDragging = null; });
}

function rkUpdatePlayOverlay() {
    const v = document.getElementById('rk-trim-video');
    const o = document.getElementById('rk-play-overlay');
    if (!v || !o) return;
    if (v.paused || v.ended) o.classList.remove('is-playing');
    else o.classList.add('is-playing');
}

function rkEnsurePlayBound() {
    if (_rkPlayBound) return;
    const wrap = document.getElementById('rk-trim-video-wrap');
    const v = document.getElementById('rk-trim-video');
    if (!wrap || !v) return;
    _rkPlayBound = true;
    wrap.addEventListener('click', (e) => {
        if (e.target.closest('.rk-timeline-handle')) return;
        if (v.paused) v.play();
        else v.pause();
    });
    v.addEventListener('play', rkUpdatePlayOverlay);
    v.addEventListener('pause', rkUpdatePlayOverlay);
    v.addEventListener('ended', rkUpdatePlayOverlay);
}

function rkShowTrimClip() {
    const clip = _rkClips[_rkTrimIdx];
    const video = document.getElementById('rk-trim-video');
    const meta = document.getElementById('rk-trim-meta');
    const labelEl = document.getElementById('rk-trim-label');
    if (!clip || !video) return;
    if (meta) meta.textContent = `Clip ${_rkTrimIdx + 1} of ${_rkClips.length} · will be #${_rkClips.length - _rkTrimIdx}`;
    video.src = clip.browserUrl || clip.url;
    video.load();
    rkUpdatePlayOverlay();
    _rkTimelineDuration = clip.originalDuration || clip.duration || 0;
    if (labelEl) {
        labelEl.value = clip.label || '';
        labelEl.oninput = () => {
            clip.label = labelEl.value.trim();
            rkSaveDraft();
            rkRenderPreview('rk-preview-trim');
        };
    }
    video.onloadedmetadata = () => {
        _rkTimelineDuration = video.duration || clip.originalDuration || clip.duration || 0;
        clip.originalDuration = _rkTimelineDuration;
        if (!clip.endTime || clip.endTime > _rkTimelineDuration) clip.endTime = _rkTimelineDuration;
        if (clip.startTime == null) clip.startTime = 0;
        rkUpdateTimelineUI();
        rkRenderTicks();
        video.currentTime = clip.startTime || 0;
    };
    const prev = document.getElementById('rk-btn-trim-prev');
    const next = document.getElementById('rk-btn-trim-next');
    if (prev) prev.disabled = _rkTrimIdx <= 0;
    if (next) next.textContent = _rkTrimIdx >= _rkClips.length - 1 ? 'Next: Title' : 'Next clip';
    rkRenderPreview('rk-preview-trim');
}

function rkCommitTrimFields() {
    const clip = _rkClips[_rkTrimIdx];
    if (!clip) return;
    const labelEl = document.getElementById('rk-trim-label');
    let start = Math.max(0, Number(clip.startTime) || 0);
    let end = Number(clip.endTime);
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
        rkRenderPreview('rk-preview-dash');
        return;
    }
    _rkTrimIdx += 1;
    rkShowTrimClip();
}

function rkOnTitleChange() {
    rkSaveDraft();
    rkRenderPreview('rk-preview-dash');
}

function rkRenderOrderList() {
    const list = document.getElementById('rk-order-list');
    if (!list) return;
    const n = _rkClips.length;
    let totalDur = 0;
    list.innerHTML = _rkClips.map((c, i) => {
        const num = n - i;
        const dur = Math.max(0, (c.endTime || c.duration || 0) - (c.startTime || 0));
        totalDur += dur;
        const thumb = c.browserUrl
            ? `<video class="rk-clip-thumb" src="${rkEscapeHtml(c.browserUrl)}" muted></video>`
            : `<div class="rk-clip-thumb"></div>`;
        return `<div class="rk-order-item" draggable="true" data-index="${i}">
            <span class="rk-drag-handle" title="Drag to reorder">⠿</span>
            <span class="rk-clip-num">${num}</span>
            ${thumb}
            <div style="flex:1;min-width:0;">
                <input type="text" class="cr-input rk-label-input" value="${rkEscapeHtml(c.label || '')}" placeholder="Label…" maxlength="40" data-label-idx="${i}">
                <div class="cr-mono" style="font-size:11px;color:var(--app-ink-3);margin-top:4px;">${dur.toFixed(1)}s</div>
            </div>
            <button type="button" class="btn-ghost" style="font-size:12px;padding:4px 8px;" onclick="rkMoveClip(${i},-1)" ${i === 0 ? 'disabled' : ''}>↑</button>
            <button type="button" class="btn-ghost" style="font-size:12px;padding:4px 8px;" onclick="rkMoveClip(${i},1)" ${i >= n - 1 ? 'disabled' : ''}>↓</button>
        </div>`;
    }).join('');
    list.querySelectorAll('[data-label-idx]').forEach((input) => {
        input.addEventListener('input', () => {
            const idx = parseInt(input.dataset.labelIdx, 10);
            if (_rkClips[idx]) {
                _rkClips[idx].label = input.value.trim();
                rkSaveDraft();
                rkRenderPreview('rk-preview-dash');
            }
        });
    });
    rkInitDragReorder(list, (from, to) => {
        const moved = _rkClips.splice(from, 1)[0];
        _rkClips.splice(to, 0, moved);
        rkRenderOrderList();
        rkSaveDraft();
        rkRenderPreview('rk-preview-dash');
    });
    const td = document.getElementById('rk-total-duration');
    const tc = document.getElementById('rk-total-clips');
    if (td) td.textContent = totalDur.toFixed(1) + 's';
    if (tc) tc.textContent = String(n);
}

function rkMoveClip(i, dir) {
    const j = i + dir;
    if (j < 0 || j >= _rkClips.length) return;
    const tmp = _rkClips[i];
    _rkClips[i] = _rkClips[j];
    _rkClips[j] = tmp;
    rkRenderOrderList();
    rkSaveDraft();
    rkRenderPreview('rk-preview-dash');
}

function rkRenderPreview(targetId) {
    const el = document.getElementById(targetId);
    if (!el) return;
    const isTrim = targetId === 'rk-preview-trim';
    const titleText = (document.getElementById('rk-title-text')?.value || '').trim();
    const hlWord = (document.getElementById('rk-title-hl')?.value || '').trim();
    const totalClips = _rkClips.filter((c) => !c.uploading && !c.downloading && !c.importFailed).length;
    if (totalClips < 1) {
        el.innerHTML = '<div class="pv-bg"></div><div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#555;font-size:0.65rem;text-align:center;padding:1rem">Add clips to see preview</div>';
        return;
    }
    // Commentary must not force Viral Shorts — respect the selected style preset.
    const viral = !['classic', 'minimal', 'checkered'].includes(_rkStyle);
    const colorMap = {
        yellow: '#facc15', cyan: '#22d3ee', green: '#34d399', red: '#f87171',
        pink: '#f472b6', orange: '#fb923c', white: '#ffffff',
    };
    const accent = colorMap[_rkColorPalette] || '#facc15';
    // Match ViewHunt letterbox (≈12% black bars) + burn layout sliders 1:1.
    let html = '<div class="pv-bars top"></div><div class="pv-bars bottom"></div><div class="pv-bg"></div>';
    const activeIdx = isTrim ? _rkTrimIdx : Math.min(_rkPreviewActive, totalClips - 1);
    const titleY = _rkLayout.titleY;
    const titleSizeRem = (_rkLayout.titleSize / 48) * 0.72;
    const numRem = (_rkLayout.numSize / 50) * 0.65;

    if (viral) {
        html += '<div style="position:absolute;top:0;left:0;right:0;height:14%;background:#000;z-index:2"></div>';
    }
    if (titleText) {
        const words = titleText.split(/\s+/).filter(Boolean);
        const n = words.length;
        const titleParts = words.map((w, i) => {
            let col = '#ffffff';
            if (hlWord && w.toLowerCase() === hlWord.toLowerCase()) col = accent;
            else if (viral) {
                if (i === 0) col = '#ffffff';
                else if (i === n - 1 && n > 2) col = '#22d3ee';
                else if (i < Math.ceil(n * 0.4)) col = '#f472b6';
                else col = '#facc15';
            } else if (hlWord && w.toLowerCase() === hlWord.toLowerCase()) {
                col = accent;
            }
            const br = (viral && (i + 1) % 3 === 0 && i < n - 1) ? '<br>' : (i < n - 1 ? ' ' : '');
            return `<span style="color:${col}">${rkEscapeHtml(w.toUpperCase())}</span>${br}`;
        }).join('');
        html += `<div class="pv-title" style="top:${titleY}%;z-index:3"><div class="pv-title-text" style="font-weight:900;font-size:${titleSizeRem.toFixed(2)}rem;line-height:1.15;text-transform:uppercase">${titleParts}</div></div>`;
    }
    if (viral && _rkCommentary && activeIdx > 0 && activeIdx < totalClips - 1) {
        html += '<div style="position:absolute;inset:18% 8% 28% 8%;background:#fff;z-index:4;display:flex;align-items:center;justify-content:center;padding:8px;border-radius:2px"><div style="color:#111;font-weight:900;font-size:0.72rem;text-align:center;text-transform:uppercase;line-height:1.2">WHITE CARD<br><span style="color:#ca8a04;font-size:0.58rem">mid commentary beat</span></div></div>';
    }
    {
        // Persistent countdown stack for every style (3 stays while 2 and 1 play).
        const gap = Math.round((_rkLayout.lineSpacing / 65) * 3);
        html += `<div class="pv-list" style="left:${_rkLayout.listX}%;gap:${gap}px;top:52%">`;
        for (let row = 0; row < totalClips; row++) {
            const num = row + 1;
            const clipIdx = totalClips - num;
            const clip = _rkClips[clipIdx];
            const label = (clip?.label || '').toUpperCase();
            let numClass = 'dim';
            let labelClass = 'dim';
            let numColor = '';
            if (isTrim) {
                if (clipIdx < _rkTrimIdx) { numClass = 'done'; labelClass = ''; }
                else if (clipIdx === _rkTrimIdx) { numClass = 'active'; labelClass = ''; }
            } else if (clipIdx < activeIdx) {
                numClass = 'done'; labelClass = '';
            } else if (clipIdx === activeIdx) {
                numClass = 'active'; labelClass = '';
            }
            if (numClass === 'active') numColor = `color:${accent};`;
            else if (numClass === 'done') {
                numColor = (_rkCheckered && row % 2 === 1) ? 'color:#ffffff;' : `color:${accent};opacity:0.85;`;
            }
            html += `<div class="pv-row"><div class="pv-num ${numClass}" style="${numColor};font-size:${numRem.toFixed(2)}rem">${num}.</div><div class="pv-label ${labelClass}" style="text-transform:uppercase">${rkEscapeHtml(label)}</div></div>`;
        }
        html += '</div>';
    }
    if (_rkCommentary) {
        const sample = viral ? 'watch this you need to see it' : 'number one hits different';
        const subY = document.getElementById('rk-subtitle-y')?.value || 50;
        const subCol = colorMap[_rkSubtitleColor] || '#facc15';
        html += `<div style="position:absolute;left:6%;right:6%;top:${subY}%;transform:translateY(-50%);text-align:center;z-index:4;font-weight:800;font-size:0.55rem;text-transform:uppercase;color:${subCol};text-shadow:0 2px 8px rgba(0,0,0,0.9)">${rkEscapeHtml(sample)}</div>`;
    }
    el.innerHTML = html;
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
            const base = rkFormatCredits(_rkAccess.credit_cost ?? 0.5);
            const withVo = rkFormatCredits(_rkAccess.credit_cost_commentary ?? 1);
            badge.textContent = `${base} credit without commentary · ${withVo} with AI commentary`;
        } else {
            badge.textContent = 'Start a free trial to cook ranking shorts';
        }
        const chip = document.getElementById('rk-commentary-chip');
        if (chip) {
            if (_rkAccess.is_trial) {
                chip.textContent = 'included on trial';
            } else {
                const withVo = rkFormatCredits(_rkAccess.credit_cost_commentary ?? 1);
                chip.textContent = `${withVo} credit total`;
            }
        }
        rkUpdateAssembleLabel();
    } catch (_) {
        if (badge) badge.textContent = '';
    }
}

async function rkAssemble() {
    if (_rkAssembling) return;
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

    if (_rkAccess && !_rkAccess.can_cook) {
        openUpgradeFlow({ reason: 'cook' });
        return;
    }
    if (_rkAccess?.is_trial && _rkAccess.trial_allowed === false) {
        if (typeof endTrialNow === 'function') endTrialNow();
        else if (typeof openUpgradeFlow === 'function') openUpgradeFlow({ reason: 'trial_exhausted' });
        else showTrialExhaustedModal();
        return;
    }

    const btn = document.getElementById('rk-btn-assemble');
    _rkAssembling = true;
    // Instant cook scene so users don't spam the button during the network hop.
    goToStep('rk-cook');
    document.getElementById('rk-result-wrap')?.classList.add('hidden');
    rkResetCookProgress('Starting your ranking short…');
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
            layout: rkLayoutPayload(),
            color_palette: _rkColorPalette,
            checkered_mode: _rkCheckered,
            commentary: _rkCommentary,
            voice_name: document.getElementById('rk-voice-picker')?.value || 'Kore',
            subtitle_font: document.getElementById('rk-subtitle-font')?.value || 'Arial',
            subtitle_y: parseFloat(document.getElementById('rk-subtitle-y')?.value || '50'),
            subtitle_color: _rkSubtitleColor,
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
                openUpgradeFlow({ reason: 'cook' });
            } else {
                alert(errMsg);
            }
            throw new Error(errMsg);
        }
        try { track('ranking_assemble_started', { job_id: data.job_id, clips: n }); } catch (_) {}
        if (cookingManager?.adoptRanking) {
            cookingManager.adoptRanking(data.job_id, titleText);
        } else if (cookingManager?.adoptStoryboard) {
            cookingManager.adoptStoryboard(data.job_id, titleText);
            cookingManager.kind = 'ranking';
            try { cookingManager._persist(); cookingManager._connect(); } catch (_) {}
        }
        rkPollResult(data.job_id);
    } catch (_) {
        // Failed before job started — return to title step so they can retry.
        goToStep('rk-title');
    } finally {
        _rkAssembling = false;
        if (typeof setButtonLoading === 'function') setButtonLoading(btn, false);
    }
}

function rkResetCookProgress(firstLine) {
    const wrapProg = document.getElementById('rk-cook-progress');
    if (wrapProg) wrapProg.classList.remove('hidden');
    const bar = document.getElementById('rk-progress-bar');
    const pct = document.getElementById('rk-progress-pct');
    const eta = document.getElementById('rk-progress-eta');
    const log = document.getElementById('rk-progress-log');
    if (bar) bar.style.width = '2%';
    if (pct) pct.textContent = '0%';
    if (eta) eta.textContent = 'starting…';
    if (log) {
        log.innerHTML = '';
        if (firstLine) {
            const line = document.createElement('div');
            line.textContent = `> ${firstLine}`;
            log.appendChild(line);
        }
    }
}

function rkUpdateCookProgress(friendly, pct) {
    const bar = document.getElementById('rk-progress-bar');
    const pctEl = document.getElementById('rk-progress-pct');
    const eta = document.getElementById('rk-progress-eta');
    const log = document.getElementById('rk-progress-log');
    if (typeof pct === 'number') {
        if (bar) bar.style.width = Math.max(2, Math.min(100, pct)) + '%';
        if (pctEl) pctEl.textContent = Math.round(pct) + '%';
        if (eta) {
            if (pct >= 90) eta.textContent = 'almost done';
            else if (pct >= 50) eta.textContent = 'cooking…';
            else eta.textContent = 'working…';
        }
    }
    if (log && friendly) {
        const last = log.lastElementChild;
        if (!last || last.textContent !== `> ${friendly}`) {
            const line = document.createElement('div');
            line.textContent = `> ${friendly}`;
            log.appendChild(line);
            log.scrollTop = log.scrollHeight;
        }
    }
}

async function rkPollResult(jobId) {
    if (!jobId) return;
    const wrap = document.getElementById('rk-result-wrap');
    const video = document.getElementById('rk-result-video');
    const dl = document.getElementById('rk-result-download');
    for (let i = 0; i < 240; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
            const res = await fetch(`/api/build/${encodeURIComponent(jobId)}/result`);
            if (!res.ok) continue;
            const data = await readJson(res, {});
            if (data.last_message && cookingManager?.jobId === jobId) {
                const friendly = (typeof _friendlyProgress === 'function')
                    ? _friendlyProgress(data.last_message)
                    : String(data.last_message);
                const statusEl = document.getElementById('cooking-bar-status');
                if (statusEl) statusEl.textContent = friendly.substring(0, 60);
                const pct = (typeof _estimateCookPercent === 'function')
                    ? _estimateCookPercent(data.last_message, i + 1)
                    : 40;
                rkUpdateCookProgress(friendly, pct);
            }
            const url = data.video_url || data.output_url || data.result?.video_url || data.result?.output_url;
            if (url) {
                document.getElementById('rk-cook-progress')?.classList.add('hidden');
                rkUpdateCookProgress('Done!', 100);
                if (wrap) wrap.classList.remove('hidden');
                if (video) video.src = url;
                if (dl) { dl.href = url; dl.download = 'ranking-short.mp4'; }
                if (cookingManager?.jobId === jobId) {
                    cookingManager.result = { ...data, output_url: url, video_url: url };
                    cookingManager._clear();
                    cookingManager._hideCookingBar();
                }
                try { if (typeof loadHistory === 'function') loadHistory(); } catch (_) {}
                try { if (typeof refreshUserData === 'function') refreshUserData(); } catch (_) {}
                return;
            }
            if (data.status === 'error' || data.status === 'cancelled') {
                if (cookingManager?.jobId === jobId) {
                    cookingManager._clear();
                    cookingManager._hideCookingBar();
                }
                if (data.status === 'error') {
                    alert(data.error || 'Ranking cook failed.');
                }
                return;
            }
        } catch (_) {}
    }
}

function rkStartOver() {
    rkResetState();
    rkRenderClipList();
    goToStep('rk-upload');
    rkInitUploadUI();
}

window.isRankingRecipe = isRankingRecipe;
window.rkResetState = rkResetState;
window.rkInitUploadUI = rkInitUploadUI;
window.rkRemoveClip = rkRemoveClip;
window.rkGoTrim = rkGoTrim;
window.rkTrimPrev = rkTrimPrev;
window.rkTrimNext = rkTrimNext;
window.rkSetStyle = rkSetStyle;
window.rkApplyPreset = rkApplyPreset;
window.rkOnLayoutChange = rkOnLayoutChange;
window.rkSetColor = rkSetColor;
window.rkSetSubColor = rkSetSubColor;
window.rkSetCheckered = rkSetCheckered;
window.rkSetCommentary = rkSetCommentary;
window.rkOnSubtitleY = rkOnSubtitleY;
window.rkCyclePreviewClip = rkCyclePreviewClip;
window.rkMoveClip = rkMoveClip;
window.rkAssemble = rkAssemble;
window.rkUpdateCookProgress = rkUpdateCookProgress;
window.rkResetCookProgress = rkResetCookProgress;
window.rkStartOver = rkStartOver;
window.rkRefreshAccess = rkRefreshAccess;
window.rkImportUrls = rkImportUrls;
window.rkOnTitleChange = rkOnTitleChange;
