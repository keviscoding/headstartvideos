/**
 * Video Factory — Complete System
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
};

let previewAudio = null;

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
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
        generateScript();
    });

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
            state.targetMinutes = parseInt(slider.value);
            label.textContent = slider.value + ' min';
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
    if (slider) { slider.value = 8; document.getElementById('target-minutes-label').textContent = '8 min'; }
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
            card.innerHTML = `
                ${previewHtml}
                <div class="niche-card-body">
                    <div class="flex items-start justify-between mb-3">
                        <span class="niche-badge" style="background: ${niche.color || '#3B82F6'}22; color: ${niche.color || '#3B82F6'}">
                            ${niche.category || 'Video'}
                        </span>
                        <span class="text-xs text-gray-500">${niche.difficulty || ''}</span>
                    </div>
                    <h3 class="text-lg font-bold text-white mb-1">${niche.name}</h3>
                    <p class="text-sm text-gray-400 mb-3">${niche.tagline || niche.description || ''}</p>
                    <div class="flex items-center justify-between text-xs text-gray-500">
                        <span>RPM: ${niche.rpm_range || 'N/A'}</span>
                        <span>Demand: ${niche.demand || 'N/A'}</span>
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
    document.querySelectorAll('.niche-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    state.niche = niche.id;
    state.nicheData = niche;
    state.voice = niche.default_voice || 'Charon';
    state.targetMinutes = niche.default_minutes || 8;
    const slider = document.getElementById('target-minutes');
    if (slider) {
        slider.value = state.targetMinutes;
        document.getElementById('target-minutes-label').textContent = state.targetMinutes + ' min';
    }
    setTimeout(() => goToStep(2), 300);
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
            card.innerHTML = `
                <div class="flex items-center gap-3">
                    <button class="play-btn" data-voice="${v.id}" title="Preview voice">
                        <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                    </button>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="font-semibold text-white">${v.name}</span>
                            <span class="text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">${v.tag}</span>
                            ${v.default ? '<span class="text-xs text-accent">Recommended</span>' : ''}
                        </div>
                        <p class="text-xs text-gray-500 mt-0.5">${v.desc}</p>
                    </div>
                </div>
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

async function handleVoiceNext() {
    const btn = document.getElementById('btn-next-4');
    setLoading(btn, true);
    document.getElementById('vo-generating').classList.remove('hidden');
    try {
        const [voRes, thumbRes] = await Promise.all([
            fetch('/api/voiceover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ script: state.script, voice: state.voice }) }),
            fetch('/api/thumbnail', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: state.title, niche_style: state.nicheData?.thumbnail_style || '' }) }),
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

async function startBuild() {
    document.getElementById('build-start').classList.add('hidden');
    document.getElementById('build-progress').classList.remove('hidden');
    const progressBar = document.getElementById('progress-bar');
    const progressLog = document.getElementById('progress-log');
    try {
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
            }),
        });
        const { job_id } = await res.json();
        const evtSrc = new EventSource(`/api/build/${job_id}/progress`);
        let msgCount = 0;
        evtSrc.addEventListener('progress', (e) => {
            msgCount++;
            const data = JSON.parse(e.data);
            const line = document.createElement('div');
            line.textContent = `> ${data.message}`;
            progressLog.appendChild(line);
            progressLog.scrollTop = progressLog.scrollHeight;
            progressBar.style.width = Math.min(95, Math.round((msgCount / 30) * 100)) + '%';
        });
        evtSrc.addEventListener('complete', (e) => {
            evtSrc.close();
            progressBar.style.width = '100%';
            const result = JSON.parse(e.data);
            state.videoUrl = result.output_url;
            state.videoPath = result.output_path;
            setTimeout(() => showUploadKit(result), 500);
        });
        evtSrc.addEventListener('error', (e) => {
            if (e.data) alert('Build failed: ' + (JSON.parse(e.data).error || 'Unknown'));
            evtSrc.close();
        });
    } catch (e) {
        alert('Build request failed: ' + e.message);
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
    try {
        const res = await fetch('/api/settings/keys');
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
