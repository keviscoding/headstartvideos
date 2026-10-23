/* ChannelRecipe creator workspace. Presentation owns no engine or model policy. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const all = selector => [...document.querySelectorAll(selector)];
  const boot = window.CR_WORKSPACE || {};
  const state = {catalog: null, setup: null, selected: null, inspected: null, input: 'idea', narration: 'ai',
    audio: '', featureDrafts: {}, page: 'create', family: 'all', projectFilter: 'all',
    queue: [], jobs: [], studies: [], busy: false, submitting: false, uploading: false,
    openJob: null, currentOutput: null, timer: null, refresh: null, optionalLoaded: false, projectNodes: new Map()};
  const STORAGE = 'channelrecipe.workspace.v1';
  const posterQueue = [];
  let posterLoads = 0;
  const posterObserver = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) queuePoster(entry.target);
  }, {rootMargin:'160px'}) : null;
  function queuePoster(video) {
    if (state.page !== 'projects' || !video.isConnected || !video.dataset.previewPoster ||
        ['queued','loaded','unavailable'].includes(video.dataset.previewPosterState)) return;
    video.dataset.previewPosterState = 'queued'; posterQueue.push(video); pumpPosters();
  }
  function pumpPosters() {
    while (posterLoads < 2 && posterQueue.length) {
      const video = posterQueue.shift();
      if (!video.isConnected || state.page !== 'projects') {video.dataset.previewPosterState='idle';continue;}
      posterLoads++;
      loadPoster(video).finally(()=>{posterLoads--;pumpPosters();});
    }
  }
  async function loadPoster(video) {
    // Warm at most two local screenshot requests before assigning the native
    // poster. A transient busy decoder never becomes a permanently blank card.
    const url=video.dataset.previewPoster;
    for(let attempt=0;attempt<4;attempt++) {
      const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);
      try {
        const response=await fetch(url,{headers:{'X-CR-Local-Token':boot.token||''},cache:'no-store',signal:controller.signal});
        if(response.status===503 && attempt<3) {
          const delay=Math.min(2,Math.max(1,Number(response.headers.get('Retry-After'))||1));
          await response.body?.cancel();clearTimeout(timer);
          await new Promise(resolve=>setTimeout(resolve,delay*1000));
          if(!video.isConnected || state.page!=='projects'){video.dataset.previewPosterState='idle';return;}
          continue;
        }
        if(!response.ok || !response.headers.get('Content-Type')?.startsWith('image/'))break;
        await response.blob();video.poster=url;video.dataset.previewPosterState='loaded';posterObserver?.unobserve(video);return;
      } catch (_) {break;} finally {clearTimeout(timer);}
    }
    video.dataset.previewPosterState='unavailable';posterObserver?.unobserve(video);
  }
  function visiblePosters() {
    if(state.page!=='projects')return;
    for(const video of all('.crw-video-preview')) {
      const bounds=video.getBoundingClientRect();
      if(bounds.bottom>=-160 && bounds.top<=innerHeight+160)queuePoster(video);
    }
  }
  function discardProject(saved) {const video=saved?.node.querySelector('video');if(video){video.pause();posterObserver?.unobserve(video);}saved?.node.remove();}
  const featureCopy = {
    youtube: ['Research footage', 'Find clips of the people, places and events in your story.'],
    stock: ['Stock footage', 'Use supporting footage for scenes without a specific source clip.'],
    ai_images: ['Generated imagery', 'Create illustrations or still images with your media account.'],
    motion: ['Motion graphics', 'Animated explanations in your recipe’s visual language.'],
    maps: ['Maps & routes', 'Show the places and journeys mentioned in the narration.'],
    headlines: ['Article highlights', 'Bring source articles and supported claims onto the screen.'],
    spotlight: ['Subject portraits', 'Introduce the people and subjects of the story.'],
    objects: ['Object visuals', 'Explain details with cutaways and isolated objects.'],
    vox: ['Graphic collage', 'Combine visual elements into explanatory compositions.'],
    threed: ['3D scenes', 'Directed and reviewed reconstructions of the story.'],
    depth: ['Image depth', 'Add depth and movement to generated imagery.'],
    sound: ['Sound design', 'Sound effects and available music underneath the narration.'],
    avatar: ['On-screen presenter', 'Use the configured presenter where this recipe supports it.']
  };
  function el(tag, cls, text) {const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n;}
  function text(id, value) {if ($(id)) $(id).textContent = value == null ? '' : String(value);}
  function hide(id, value) {if ($(id)) $(id).hidden = !!value;}
  function error(message) {text('crw-error', message || ''); hide('crw-error', !message);}
  function toast(message) {text('crw-toast', message); hide('crw-toast', false); clearTimeout(toast.timer); toast.timer = setTimeout(() => hide('crw-toast', true), 5500);}
  function encoded(value) {return encodeURIComponent(String(value));}
  function providerName(id) {return ({kie:'Kie.ai',atlas:'Atlas Cloud',original:''})[id] || '';}
  // "google/gemini-3-8-flash-tts" → "Gemini 3.8 Flash"; never show raw provider model ids.
  function friendlyModel(id) {
    const tail = String(id || '').split('/').pop().replace(/-(tts|preview|latest)$/i,'');
    if (!tail) return '';
    const m = tail.match(/^(gemini|eleven(?:labs)?|eleven_)[-_]?(.*)$/i);
    const words = (m ? (m[1].toLowerCase().startsWith('gemini') ? 'Gemini ' : 'ElevenLabs ') + m[2] : tail)
      .replace(/(\d)-(\d)/g,'$1.$2').replace(/[-_]+/g,' ').trim();
    return words.replace(/\b([a-z])/g, c => c.toUpperCase());
  }
  const DISCORD_URL = 'https://discord.gg/aUbazAAqw3';
  function number(id, fallback, min, max) {const v = Number($(id)?.value); return Math.max(min, Math.min(max, Number.isFinite(v) ? Math.round(v) : fallback));}
  function checked(id) {return $(id)?.checked === true;}
  function familyKey(value) {return ({'data & graphics': 'data', 'learned style': 'custom', 'my styles': 'custom'})[String(value).toLowerCase()] || String(value).toLowerCase();}
  function elapsed(seconds) {const v = Math.max(0, Math.round(Number(seconds) || 0)); return v < 60 ? `${v}s` : `${Math.floor(v / 60)}m ${v % 60}s`;}
  function date(value) {const d = new Date(Number(value) * 1000); return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour:'numeric', minute:'2-digit'});}
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {}); headers.set('X-CR-Local-Token', boot.token || '');
    let body = options.body;
    if (body != null && !(body instanceof FormData)) {headers.set('Content-Type', 'application/json'); body = JSON.stringify(body);}
    const r = await fetch(path, {...options, headers, body, cache: 'no-store'});
    let data; try {data = await r.json();} catch (_) {throw new Error('The studio could not read this response. Check that your launcher is still open.');}
    if (!r.ok) {const e = new Error(data.error || 'This request could not complete.'); e.details = data; throw e;}
    return data;
  }
  function chooseGroup(selector, attribute, value) {
    for (const button of all(selector)) {const active = button.dataset[attribute] === value; button.classList.toggle('is-selected', active); button.setAttribute('aria-pressed', String(active));}
  }
  function rows() {const result = [...(state.catalog?.channels || [])]; if (state.catalog?.automatic) result.push(state.catalog.automatic); return result;}
  function identity(recipe) {return recipe.presentation_id || (recipe.art_url?.endsWith('/automatic.svg') ? 'automatic' : recipe.name);}
  function findRecipe(id) {return rows().find(r => identity(r) === id || r.name === id);}
  function featureValues() {const out = {}; for (const input of all('#crw-features input')) out[input.dataset.feature] = input.checked; return out;}
  function strict(recipe = state.selected) {return recipe?.visual_policy === 'reference_only' || recipe?.visual_policy_label === 'Reference animation only';}
  function save() {
    if (!state.selected) return;
    state.featureDrafts[state.selected.name] = featureValues();
    const draft = {recipe: identity(state.selected), title: $('crw-title').value, script: $('crw-script').value,
      batch: $('crw-batch-titles').value, minutes: $('crw-minutes').value, input: state.input,
      extra: $('crw-extra').value, features: state.featureDrafts, captions: checked('crw-captions'),
      description: checked('crw-description'), thumbnails: checked('crw-thumbnails'),
      thumbnailCount: $('crw-thumbnail-count').value, chapters: $('crw-chapters').value,
      sources: checked('crw-sources'), sourceCount: $('crw-source-count').value,
      fast: checked('crw-fast-toggle'), adaptive: checked('crw-adaptive')};
    try {localStorage.setItem(STORAGE, JSON.stringify(draft));} catch (_) {}
  }
  function restore() {
    let d = {}; try {d = JSON.parse(localStorage.getItem(STORAGE) || '{}') || {};} catch (_) {}
    state.featureDrafts = d.features && typeof d.features === 'object' ? d.features : {};
    for (const [id, key] of [['crw-title','title'],['crw-script','script'],['crw-batch-titles','batch'],['crw-extra','extra'],
      ['crw-minutes','minutes'],['crw-thumbnail-count','thumbnailCount'],['crw-chapters','chapters'],['crw-source-count','sourceCount']])
      if (typeof d[key] === 'string') $(id).value = d[key];
    for (const [id, key] of [['crw-captions','captions'],['crw-description','description'],['crw-thumbnails','thumbnails'],
      ['crw-sources','sources'],['crw-fast-toggle','fast'],['crw-adaptive','adaptive']]) if (typeof d[key] === 'boolean') $(id).checked = d[key];
    setInput(d.input === 'script' ? 'script' : 'idea', false);
    return d;
  }
  function setInput(mode, persist = true) {state.input = mode; chooseGroup('[data-input-mode]', 'inputMode', mode); hide('crw-script-panel', mode !== 'script'); if (persist) save();}
  async function setNarration(mode) {
    state.narration = mode; chooseGroup('[data-narration-mode]', 'narrationMode', mode);
    hide('crw-narration-ai', mode !== 'ai'); hide('crw-narration-own', mode !== 'own');
    if (mode === 'own' && !$('crw-audio-recent').dataset.loaded) {
      try {const result = await api('/api/voiceovers'); const records = result.voiceovers || result.items || [];
        for (const row of records) {const o = el('option', '', `${row.name} · ${Math.round(row.seconds || 0)}s`); o.value = row.id; $('crw-audio-recent').append(o);}
        $('crw-audio-recent').dataset.loaded = '1';
      } catch (e) {text('crw-audio-status', e.message);}
    }
    summary(); gate();
  }
  function tags(id, values) {const n = $(id); n.replaceChildren(...(values || []).slice(0, 4).map(v => el('span', 'crw-tag', v)));}
  // Shorts recipes (family "Shorts"): a vertical Short of 15-60 seconds; the long-form options don't apply
  const isShort = (r) => String(r?.family || '') === 'Shorts';
  const isRanking = (r) => !!r?.ranking;   // Ranking Shorts: real clips counted down; their length comes from the clips
  const SHORT_ONLY_HIDDEN = ['crw-fast-section','crw-palette-section','crw-avatar-section','crw-character-section','crw-offer-section','crw-cta-section','crw-pictures-section'];
  function shortsMode() {
    const s = isShort(state.selected), rk = isRanking(state.selected);
    // The owner couldn't find "Create several videos" for a batch of ranking ideas: for Ranking Shorts it says what it is.
    text('crw-batch-label', rk ? 'Queue more topics (up to 15, one per line)' : 'Create several videos');
    $('crw-batch-titles').placeholder = rk ? 'Ranking the Funniest Dog Moments\nRanking the Best Bunny Moments' : 'One additional title per line';
    hide('crw-short-duration', !s || rk); hide('crw-long-duration', s); hide('crw-ranking-ranks', !rk); hide('crw-ranking-section', !rk);
    text('crw-canvas-label', s ? '9:16 vertical' : '16:9 landscape'); text('crw-summary-canvas', s ? '9:16 vertical' : '16:9 landscape');
    if (s) for (const id of SHORT_ONLY_HIDDEN) hide(id, true);
  }
  function selectRecipe(recipe, persist = true) {
    if (state.selected) state.featureDrafts[state.selected.name] = featureValues();
    const fresh = !state.selected || state.selected.name !== recipe?.name;
    state.selected = recipe;
    if (fresh && isShort(recipe) && recipe.short_seconds) { $('crw-seconds').value = recipe.short_seconds; chooseGroup('[data-seconds]', 'seconds', String(recipe.short_seconds)); }   // its own length
    text('crw-selected-name', recipe.label); text('crw-selected-description', recipe.description || recipe.tagline);
    recipePreview($('crw-selected-art'),recipe);
    tags('crw-selected-tags', recipe.capabilities);
    text('crw-selected-policy', recipe.visual_policy_label || ''); hide('crw-selected-policy', !recipe.visual_policy_label);
    const automatic = identity(recipe) === 'automatic'; hide('crw-adaptive-section', !recipe.auto_style);
    if (automatic) $('crw-adaptive').checked = true;
    const featureDefinitions = state.catalog?.features || [];
    const offered = Array.isArray(recipe.features?.offered) ? recipe.features.offered : featureDefinitions.filter(f => !f.explicit).map(f => f.key);
    const defaults = Array.isArray(recipe.features?.on) ? recipe.features.on : offered;
    const stored = state.featureDrafts[recipe.name] || {};
    const group = $('crw-features'); group.replaceChildren();
    for (const key of offered) {
      const f = featureDefinitions.find(v => v.key === key) || {key, label: key};
      const copy = featureCopy[key] || [f.label, 'An available option for this visual recipe.'];
      const label = el('label', 'crw-feature'); const input = el('input'); input.type = 'checkbox'; input.dataset.feature = key;
      input.checked = typeof stored[key] === 'boolean' ? stored[key] : defaults.includes(key);
      const body = el('span'); body.append(el('strong', '', copy[0]), el('small', '', copy[1])); label.append(input, body);
      if (state.catalog?.caps?.features?.[key]?.ok === false) {label.classList.add('is-unavailable'); body.append(el('small', 'crw-account-warning', 'Account setup needed before using this option'));}
      input.addEventListener('change', () => {if (key === 'ai_images' && !input.checked) {const depth = group.querySelector('[data-feature="depth"]'); if (depth) depth.checked = false;} if (key === 'depth' && input.checked) {const images = group.querySelector('[data-feature="ai_images"]'); if (images) images.checked = true;} updateOptional(); save(); summary();});
      group.append(label);
    }
    text('crw-features-note', strict(recipe) ? 'Every visual scene uses your learned animation kit. Narration, captions and sound remain available.' : 'Choose the sources this recipe can use. Account requirements are checked before generation.');
    $('crw-character').value = recipe.character || '';
    hide('crw-panels-section', !recipe.panels); if (recipe.panels) loadPanels();
    const pics = recipe.pictures; hide('crw-pictures-section', !pics || strict(recipe));
    if (pics) {const r = $('crw-pictures'); r.min = pics.floor; r.max = pics.ceiling; r.value = pics.default; showPictures();}
    updateOptional(); summary(); gate(); if (persist) save();
  }
  function showPictures() {
    const pics = state.selected?.pictures; if (!pics) return; const v = Number($('crw-pictures').value);
    text('crw-pictures-value', `· ${v}`);
    text('crw-pictures-help', `The recipe draws ${pics.default} a minute. Fewer pictures cost less (about $${(v * 0.02).toFixed(2)} a minute in pictures); never fewer than ${pics.floor}, so each picture stays on its own words.`);
  }
  function threedLook(features) {
    const v = $('crw-palette').value;
    if (!v || !features.threed || strict()) return '';
    if (v.startsWith('look:')) return v.slice(5);
    return v.startsWith('studio:') ? 'studio_color' : 'documentary';      // a palette's colours need a colour look
  }
  function withPalette(extra, features) {
    const palette = $('crw-palette').value;
    if (!palette || palette.startsWith('look:') || !features.threed || strict()) return extra;
    const colours = palette.startsWith('studio:') ? palette.slice(7) : palette;
    return (extra ? extra + '\n' : '') + `3D SCENE PALETTE: every 3D scene uses only ${colours}; keep it minimal and contrasting.`;
  }
  function ctaValue() {
    if (!checked('crw-cta-on')) return null;
    const what = $('crw-cta-what').value.trim(), link = $('crw-cta-link').value.trim();
    if (!what) throw new Error('Say what you are promoting, or switch the call to action off.');
    if (!/^https?:\/\/\S{3,}$/.test(link)) throw new Error('Give your call to action a full link, starting with https://.');
    return {what, link, count: Number($('crw-cta-count').value) || 1, image: state.ctaImage || ''};
  }
  function showPanels(set) {
    state.panels = set ? set.id : ''; const strip = $('crw-panels-strip'); strip.replaceChildren();
    if (set) {
      text('crw-panels-status', `${set.count} panels ready · ${set.name}`);
      for (let n = 1; n <= Math.min(set.count, 8); n++) {const img = el('img'); img.alt = `Panel ${n}`; img.loading = 'lazy'; img.src = `/cr-workspace/panels/${encoded(set.id)}/${n}.jpg`; strip.append(img);}
    } else text('crw-panels-status', '');
    gate();
  }
  async function loadPanels() {
    if ($('crw-panels-recent').dataset.loaded) return;
    try {
      const result = await api('/cr-workspace/panels'); state.panelSets = result.panels || [];
      for (const set of state.panelSets) {const o = el('option', '', `${set.name} · ${set.count} panels`); o.value = set.id; $('crw-panels-recent').append(o);}
      $('crw-panels-recent').dataset.loaded = '1';
    } catch (e) {text('crw-panels-status', e.message);}
  }
  function updateOptional() {
    const features = featureValues(); hide('crw-fast-section', !features.threed || strict());
    hide('crw-palette-section', !features.threed || strict());
    hide('crw-avatar-section', !features.avatar || strict());
    hide('crw-character-section', strict());
    hide('crw-offer-section', $('crw-offer').options.length<2 || !features.avatar || strict());
    hide('crw-description-options', !checked('crw-description')); hide('crw-thumbnail-options', !checked('crw-thumbnails'));
    text('crw-fast-status', checked('crw-fast-toggle') ? 'Fast mode is on for every 3D scene.' : state.fastReady ? 'Standard scene rendering is selected.' : '');
    shortsMode();
  }
  // Fast mode needs a one-time local install and test render (no Claude or
  // media usage). Offer it right here, where the switch is.
  let fastTimer = null;
  async function refreshFast() {
    let data; try {data = await api('/cr-renderer');} catch (_) {return;}
    const running = ['installing', 'checking', 'rendering'].includes(data.setup?.state), failed = data.setup?.state === 'failed';
    state.fastReady = !!data.ready && !running;
    const toggle = $('crw-fast-toggle');
    if (!state.fastReady && toggle.checked) toggle.checked = false;
    toggle.disabled = !state.fastReady;
    hide('crw-fast-setup', state.fastReady);
    text('crw-fast-setup-status', running ? (data.setup?.message || 'Setting up Fast mode…')
      : failed ? data.setup.message
      : 'Fast mode needs a one-time setup on this computer. It installs the fast graphics engine and renders a short test. It doesn\'t use your Claude or media accounts, and takes a few minutes.');
    const button = $('crw-fast-install');
    button.disabled = running || !!data.busy;
    button.textContent = running ? 'Setting up…' : failed ? 'Try setup again' : 'Set up Fast mode';
    clearTimeout(fastTimer); if (running) fastTimer = setTimeout(refreshFast, 2000);
    updateOptional(); summary();
  }
  function summary() {
    const r = state.selected; text('crw-summary-title', $('crw-title').value.trim() || 'Your next video');
    text('crw-summary-recipe', r?.label || 'Choose a recipe');
    text('crw-summary-duration', isRanking(r) ? `${number('crw-ranks',5,3,10)} ranked clips` : isShort(r) ? `${number('crw-seconds',25,15,60)} seconds` : state.narration === 'own' ? 'Length of your recording' : `${number('crw-minutes',10,1,60)} minutes`);
    text('crw-summary-visuals', strict(r) ? 'Reference animation throughout' : Object.entries(featureValues()).filter(([k,v]) => v && k !== 'sound').map(([k]) => (featureCopy[k] || [k])[0]).join(', ') || 'Recipe defaults');
    text('crw-summary-voice', state.narration === 'own' ? 'Your uploaded narration' : state.setup?.narration?.voice || 'Connected narration account');
    text('crw-summary-renderer', state.renderOn==='cloud' && state.cloudInfo?.available && !cloudBlock() ? 'Studio Cloud' : checked('crw-fast-toggle') && featureValues().threed && !strict() ? 'Fast mode · local render' : 'Local render');
  }
  function gate() {
    const ready = state.setup?.readiness?.ready === true;
    const needsPanels = !!state.selected?.panels && !state.panels;
    const blocked = state.submitting || state.uploading || !state.catalog || !ready || state.busy || (state.narration === 'own' && !state.audio) || needsPanels;
    $('crw-create').disabled = blocked;
    let message = !state.catalog ? 'Loading your visual recipes…' : !ready && mustUpdate() ? 'A new Studio is out. Press Update now at the top of this page to keep making new videos: it takes a minute and keeps your projects.' : !ready ? (state.setup?.readiness?.blockers || ['Finish account setup before creating a video.']).join(' ') : state.busy ? 'A project is using this studio. Its progress is available in Projects.' : state.narration === 'own' && !state.audio ? 'Choose a narration recording to continue.' : needsPanels ? 'Choose your panels to continue.' : 'Ready. Each Create starts a separate new project.';
    text('crw-readiness', message);
    const status = $('crw-connection'); status.querySelector('span:last-of-type').textContent = ready ? 'Accounts connected' : 'Accounts need attention';
    status.querySelector('.crw-status-dot').className = `crw-status-dot ${ready ? 'is-ready' : 'is-warning'}`;
    cloudChoice();
  }
  function showPage(name, persist = true) {
    state.page = ['create','projects','styles'].includes(name) ? name : 'create';
    if(state.page!=='projects')for(const player of all('.crw-video-preview'))player.pause();
    for (const p of ['create','projects','styles']) hide(`crw-page-${p}`, p !== state.page);
    for (const button of all('.crw-nav [data-nav]')) {const active = button.dataset.nav === state.page; button.classList.toggle('is-active', active); active ? button.setAttribute('aria-current','page') : button.removeAttribute('aria-current');}
    text('crw-page-name', {create:'Create video',projects:'Your projects',styles:'Style library'}[state.page]);
    if (persist) history.replaceState(null,'',`${location.pathname}${location.search}#${state.page}`);
    if (state.page === 'projects') {renderProjects(); cloudPanel();} if (state.page === 'styles') renderStyles();
    $('crw-main').focus({preventScroll:true});
  }
  function inspect(recipe, reveal = false) {
    state.inspected = recipe; text('crw-detail-name', recipe.label); text('crw-detail-description',recipe.description || recipe.tagline);
    recipePreview($('crw-detail-art'),recipe);
    text('crw-detail-family', recipe.family || 'Visual recipe'); tags('crw-detail-tags', recipe.capabilities);
    text('crw-detail-policy',recipe.visual_policy_label || ''); hide('crw-detail-policy',!recipe.visual_policy_label);
    $('crw-detail-points').replaceChildren(...(recipe.capabilities || []).map(v => el('li','',v)));
    const sample = $('crw-detail-preview'); sample.href = recipe.preview_video_url || '#'; sample.hidden = !recipe.preview_video_url; $('crw-detail-use').disabled = false;
    for (const card of all('#crw-gallery-grid .crw-recipe-card')) card.classList.toggle('is-selected', card.dataset.recipe === identity(recipe));
    if (reveal && matchMedia('(max-width:700px)').matches && $('crw-gallery').open) $('crw-gallery-detail').scrollIntoView({behavior:'smooth',block:'start'});
  }
  function recipePreview(img,recipe) {
    const parent=img.parentElement;let label=parent.querySelector('[data-preview-state]') || parent.querySelector('span');
    if(!label){label=el('span','crw-art-caption');parent.append(label);}
    label.dataset.previewState='1';label.textContent=recipe.preview_available ? (recipe.preview_label||'Video screenshot') : 'No video preview yet';
    img.hidden=!recipe.preview_available;img.alt=`${recipe.label} · ${recipe.preview_label||'Video screenshot'}`;
    parent.classList.toggle('crw-preview-unavailable',!recipe.preview_available);
    if(recipe.preview_available && recipe.preview_url) {
      img.onload=()=>{img.hidden=false;parent.classList.remove('crw-preview-unavailable');};
      img.onerror=()=>{img.hidden=true;parent.classList.add('crw-preview-unavailable');label.textContent='Video preview unavailable';};
      img.src=recipe.preview_url;
    } else {img.removeAttribute('src');img.onload=null;img.onerror=null;}
  }
  function recipeCard(recipe, action) {
    const b = el('button','crw-recipe-card'); b.type = 'button'; b.dataset.recipe = identity(recipe); b.setAttribute('aria-label', `Explore ${recipe.label}`);
    const art = el('div','crw-card-art'); const img = el('img'); img.loading='lazy';art.append(img);recipePreview(img,recipe);
    const body = el('div','crw-card-body'); body.append(el('small','crw-card-meta',recipe.family || 'Your style'),el('h3','',recipe.label),el('p','crw-card-description',recipe.description || recipe.tagline || ''));
    b.append(art,body); b.addEventListener('click',()=>action(recipe)); return b;
  }
  function gallery() {
    { const tab = document.querySelector('[data-family="shorts"]'); if (tab) tab.hidden = !rows().some(isShort); }   // private recipes: no tab without them
    const query = $('crw-gallery-search').value.trim().toLowerCase();
    const recipes = rows().filter(r => (state.family === 'all' || (state.family === 'custom' ? r.custom : familyKey(r.family) === state.family)) && [r.label,r.description,...(r.capabilities || [])].join(' ').toLowerCase().includes(query));
    $('crw-gallery-grid').replaceChildren(...recipes.map(r => recipeCard(r, selected=>inspect(selected,true)))); text('crw-gallery-count', `${recipes.length} ${recipes.length === 1 ? 'recipe' : 'recipes'}`); hide('crw-gallery-empty',!!recipes.length); hide('crw-gallery-detail',!recipes.length);
    if (state.inspected && recipes.includes(state.inspected)) inspect(state.inspected); else if (recipes[0]) inspect(recipes[0]);
  }
  function openGallery() {gallery(); $('crw-gallery').showModal(); $('crw-gallery-search').focus();}
  function renderAllowance() {
    const box = $('crw-clone-allowance'); if (!box) return;
    const clones = state.setup?.account?.style_clones; box.replaceChildren();
    if (!clones) {box.hidden = true; return;}
    const used = Number(clones.used || 0), limit = clones.limit;
    const label = el('span','crw-clone-count', limit == null ? `${used} style${used === 1 ? '' : 's'} cloned · unlimited on your plan` : `${used} of ${limit} style clones used`);
    box.append(label);
    if (limit != null) {const bar = el('span','crw-clone-bar'); const fill = el('span'); fill.style.width = `${Math.min(100, limit ? used / limit * 100 : 100)}%`; bar.append(fill); box.append(bar);}
    if (clones.upgrade_label && limit != null && used >= limit) {const a = el('a','crw-inline-link',`Upgrade to ${clones.upgrade_label} to clone more ↗`); a.href = clones.upgrade_url || 'https://channelrecipe.com/app#billing'; a.target = '_blank'; a.rel = 'noopener'; box.append(a);}
    box.hidden = false;
  }
  function renderStyles() {
    renderAllowance();
    const customs = rows().filter(r => r.custom); const target = $('crw-styles-list'); target.replaceChildren();
    for (const recipe of customs) {const card = recipeCard(recipe,r => {selectRecipe(r); showPage('create');}); target.append(card);}
    hide('crw-styles-empty',!!customs.length);
    // a finished study is already a saved style above; only studies still running or stopped are listed here
    const studies = state.studies.filter(study => study.state !== 'done');
    if (studies.length) {
      const heading = el('h3','crw-library-subheading','Style studies'); target.append(heading);
      for (const study of studies) {const a = el('a','crw-study-card'); a.href = study.link; a.append(el('strong','',study.label || study.title),el('span',`crw-study-state is-${study.state === 'running' ? 'running' : study.state === 'done' ? 'done' : 'stopped'}`,study.state === 'running' ? 'Studying the channel…' : study.state === 'done' ? 'Style saved' : 'Stopped. Open to review')); target.append(a);}
    }
  }
  const phaseCopy = line => {
    const v = String(line || ''); if (/DONE|published:/i.test(v)) return 'Finishing your video';
    if (/ffmpeg|final render|concat|mux/i.test(v)) return 'Rendering the film'; if (/review|critique|verify|repair/i.test(v)) return 'Reviewing visuals';
    if (/subtitles|whisper|timing narration/i.test(v)) return 'Timing narration and captions'; if (/voice|tts|narration/i.test(v)) return 'Creating narration';
    if (/design|scene|graphic|image|motion|kit/i.test(v)) return 'Building visual scenes'; if (/script|fact check/i.test(v)) return 'Writing and checking the story'; return 'Preparing the project';
  };
  function statusBadge(value) {const kind = value === 'done' ? 'done' : ['running','queued'].includes(value) ? 'running' : 'stopped'; return el('span',`crw-status-badge is-${kind}`,{done:'Ready',running:'In production',queued:'Queued',held:'On hold',cancelled:'Cancelled',error:'Needs review',failed:'Needs review'}[value] || 'Saved');}
  function combinedProjects() {
    // stopped videos saved on disk first, so a live or finished entry for the same folder wins
    const map = new Map((state.stopped || []).map(j => [j.slug,{...j}]));
    for (const j of state.jobs) map.set(j.slug,{...j,state:'done',hasVideo:true});
    for (const q of state.queue) map.set(q.slug,{...(map.get(q.slug) || {}),...q});
    return [...map.values()];
  }
  function projectCard(job) {
    const card=el('article','crw-project-card');card.dataset.slug=job.slug;
    const r=findRecipe(job.style);const media=el('div','crw-card-art');
    if(job.hasVideo) {
      const video=el('video','crw-video-preview');video.controls=false;video.playsInline=true;video.preload='none';
      video.dataset.previewPoster=`/cr-workspace/preview/project/${encoded(job.slug)}.jpg`;video.src=`/f/video/${encoded(job.slug)}`;
      video.setAttribute('aria-label',job.title||'Video project');
      const play=el('button','crw-preview-play','▶');play.type='button';play.setAttribute('aria-label',`Play ${job.title||'video'}`);
      play.addEventListener('click',()=>{for(const other of all('.crw-video-preview'))if(other!==video)other.pause();video.play().catch(()=>toast('The video could not start. Open its saved output or check your launcher.'));});
      video.addEventListener('play',()=>{for(const other of all('.crw-video-preview'))if(other!==video)other.pause();video.controls=true;play.hidden=true;});video.addEventListener('pause',()=>{play.hidden=false;});video.addEventListener('ended',()=>{video.controls=false;play.hidden=false;});
      media.append(video,play);
      posterObserver?.observe(video);
    } else {media.classList.add('crw-preview-unavailable');media.append(el('span','', ['running','queued'].includes(job.state)?'Video is in production':'No finished video yet'));}
    const body=el('div','crw-card-body');body.append(statusBadge(job.state),el('h3','',job.title||'Untitled video'),el('p','crw-card-meta',`${r?.label||'Video project'}${job.mtime?' · '+date(job.mtime):''}`));
    const actions=el('div','crw-card-actions');const open=el('button','crw-secondary-button',job.hasVideo?'Video & exports':['running','queued'].includes(job.state)?'View progress':'Review issue');open.type='button';open.addEventListener('click',()=>job.hasVideo?openOutput(job):openProgress(job));actions.append(open);body.append(actions);card.append(media,body);return card;
  }
  function renderProjects() {
    const projects=combinedProjects().filter(j=>state.projectFilter==='all'||(state.projectFilter==='done'?j.state==='done':state.projectFilter==='active'?['running','queued'].includes(j.state):!['running','queued','done'].includes(j.state)));
    const target=$('crw-projects-list');const wanted=new Set(projects.map(j=>j.slug));
    const available=new Set(combinedProjects().map(j=>j.slug));for(const [slug,cached] of state.projectNodes)if(!available.has(slug)){discardProject(cached);state.projectNodes.delete(slug);}
    for(const node of [...target.children])if(!wanted.has(node.dataset.slug)){node.querySelector('video')?.pause();node.remove();}
    projects.forEach((job,index)=>{
      const fingerprint=JSON.stringify([job.state,job.title,job.style,job.mtime,job.hasVideo,job.editable]);let saved=state.projectNodes.get(job.slug);
      if(!saved||saved.fingerprint!==fingerprint){discardProject(saved);saved={fingerprint,node:projectCard(job)};state.projectNodes.set(job.slug,saved);}
      if(target.children[index]!==saved.node)target.insertBefore(saved.node,target.children[index]||null);
    });
    hide('crw-projects-empty',!!projects.length);text('crw-project-count',combinedProjects().length);hide('crw-project-count',!combinedProjects().length);
    requestAnimationFrame(visiblePosters);
  }
  async function openProgress(job) {
    state.openJob = job.slug; showPage('projects'); hide('crw-progress',false); text('crw-progress-title',job.title || 'Your project');
    $('crw-progress-actions').replaceChildren(); await refreshProgress(); $('crw-progress').scrollIntoView({behavior:'smooth',block:'start'});
  }
  async function refreshProgress() {
    if (!state.openJob) return;
    try {
      let data;
      try {data = await api(`/api/status/${encoded(state.openJob)}`);}
      catch (e) {
        // A video that stopped before Studio last restarted: its log is gone, its saved work and Resume are not
        if (!(state.stopped || []).some(j => j.slug === state.openJob)) throw e;
        data = {done: true, state: 'error', lines: [], error: 'This video stopped before Studio last restarted. Its saved work is kept: press Resume saved project to carry on from where it stopped.'};
      }
      const lines = data.lines || [];
      const last = lines[lines.length-1] || ''; text('crw-progress-phase', data.done ? (data.state === 'done' ? 'Ready to watch' : 'This project stopped') : phaseCopy(last));
      const queued = state.queue.find(q=>q.slug===state.openJob); text('crw-progress-time',data.elapsed != null ? elapsed(data.elapsed) : queued?.elapsed != null ? elapsed(queued.elapsed) : '');
      const bar = $('crw-progress-bar'); const percent = data.done && data.state === 'done' ? 100 : Number.isFinite(data.progress) ? Math.max(0,Math.min(100,data.progress*100)) : null;
      bar.parentElement.hidden = percent === null; if (percent !== null) {bar.style.width = `${percent}%`; bar.parentElement.setAttribute('role','progressbar'); bar.parentElement.setAttribute('aria-valuemin','0'); bar.parentElement.setAttribute('aria-valuemax','100'); bar.parentElement.setAttribute('aria-valuenow',String(Math.round(percent)));}
      const stats=$('crw-render-stats');
      stats.replaceChildren();stats.hidden=true;
      if(data.threed && !data.done) {
        try {const rendered=await api(`/cr-workspace/render/${encoded(state.openJob)}`);
          for(const scene of rendered.scenes||[]) {const frames=Number(scene.frames_done||0), seconds=Number(scene.elapsed_seconds||0);const compiling=scene.stage==='compile' && scene.state==='running';
            const label=compiling?'Claude is writing scene code':scene.stage==='export' && scene.state==='running'?'Exporting scene':scene.state==='complete'?'Scene rendered':scene.state==='failed'?'Scene stopped':'Rendering scene';
            stats.append(el('div','',`${scene.scene_id} · ${label}${compiling?'':` · ${frames}${scene.total_frames?' / '+scene.total_frames:''} frames · ${seconds.toFixed(1)}s${frames&&seconds?' · '+(frames/seconds).toFixed(1)+' frames/sec':''}`}`));
          } stats.hidden=!stats.childElementCount;
        } catch(_) {/* Overall job progress remains available if GPU metrics are missing. */}
      }
      text('crw-progress-log',lines.join('\n')); text('crw-progress-note',data.error || (data.done ? 'Saved work remains with this project. Starting another video creates a fresh attempt.' : 'Your studio is working. Completed steps and elapsed time come from the live job.'));
      const cancel = data.cr_cancel || {}; $('crw-progress-cancel').disabled = cancel.can_cancel !== true; hide('crw-progress-cancel',cancel.can_cancel !== true); $('crw-progress-cancel').dataset.run = cancel.run_id || '';
      if (data.done && !$('crw-progress-actions').childElementCount) {
        const another = el('button','crw-secondary-button','Create another video'); another.type='button'; another.addEventListener('click',()=>showPage('create')); $('crw-progress-actions').append(another);
        if (data.state === 'done') {const open = el('button','crw-primary-button','Open video');open.type='button';open.addEventListener('click',()=>openOutput({slug:state.openJob,title:$('crw-progress-title').textContent,editable:true}));$('crw-progress-actions').append(open);}
        else {
          const saved = await api(`/cr-workspace/project/${encoded(state.openJob)}`);
          if (saved.available_for_resume) {const resume=el('button','crw-text-button','Resume saved project'); resume.type='button'; resume.addEventListener('click',async()=>{resume.disabled=true;try{await api('/cr-submit-project',{method:'POST',body:{project_id:saved.project_id}});toast('Saved project submitted. Completed assets will be reused.');await refreshActivity();}catch(e){text('crw-progress-note',e.message);}finally{resume.disabled=false;}});$('crw-progress-actions').append(resume);}
          if (saved.available_for_resume) {try {const voice=await api(`/cr-workspace/voice/${encoded(state.openJob)}`); if ((voice.stopped_on_voice || /narration (task|service)/i.test(data.error || '')) && !voice.has_voice) voiceChoices(voice, saved);} catch (_) {/* Resume stays available */}}
        }
      }
    } catch (e) {text('crw-progress-note',e.message);}
  }
  // A video whose voice stopped: voice it another way, this video only (Gemini through Kie, the creator's own
  // ElevenLabs account, or their own recording), then carry on from where it stopped.
  function voiceChoices(voice, saved) {
    const slug = state.openJob, box = el('div', 'crw-voice-choices');
    box.append(el('p', 'crw-voice-title', 'Voice it another way'));
    const why = voice.failed && voice.failed.label ? `${voice.failed.label} couldn't record this video's voice${voice.failed.reason ? ` ("${voice.failed.reason}")` : ''}.` : "This video's voice couldn't be recorded.";
    box.append(el('p', 'crw-field-help', `${why} Pick how to carry on. Everything already made is kept.`));
    const resumeWith = async (body, button) => {
      button.disabled = true;
      try {
        await api(`/cr-workspace/voice/${encoded(slug)}`, {method: 'POST', body});
        await api('/cr-submit-project', {method: 'POST', body: {project_id: saved.project_id}});
        toast('Carrying on with the new voice. Completed work is reused.'); await refreshActivity();
      } catch (e) {text('crw-progress-note', e.message);} finally {button.disabled = false;}
    };
    for (const choice of voice.choices || []) {
      const row = el('div', 'crw-voice-choice'), button = el('button', choice.id === voice.current ? 'crw-primary-button' : 'crw-secondary-button', choice.label);
      button.type = 'button'; button.disabled = !choice.ready; row.append(button, el('span', 'crw-field-help', choice.note));
      if (!choice.ready && choice.setup) {const link = el('a', 'crw-text-button', 'Open Setup'); link.href = choice.setup; row.append(link);}
      if (choice.id === 'own') {
        const file = el('input'); file.type = 'file'; file.accept = 'audio/*,video/*'; file.hidden = true;
        const read = el('details', 'crw-voice-script'), summary = el('summary', '', 'Show the script to read'), words = el('pre', '', voice.script || 'The script is not saved yet.');
        read.append(summary, words);
        button.addEventListener('click', () => file.click());
        file.addEventListener('change', async () => {
          if (!file.files.length) return; button.disabled = true; text('crw-progress-note', 'Uploading your recording…');
          try {const form = new FormData(); form.append('file', file.files[0]); const up = await api('/api/voiceover', {method: 'POST', body: form});
            await resumeWith({choice: 'own', recording: up.voiceover.id}, button);
          } catch (e) {text('crw-progress-note', e.message);} finally {button.disabled = false; file.value = '';}
        });
        row.append(file); box.append(row, read); continue;
      }
      button.addEventListener('click', () => resumeWith({choice: choice.id}, button));
      box.append(row);
    }
    $('crw-progress-actions').append(box);
  }
  async function openOutput(job) {
    try {
      const data = await api(`/api/result/${encoded(job.slug)}`); if (!data.video) throw new Error('This project has no finished video to open yet.');
      for(const player of all('.crw-video-preview'))player.pause();state.currentOutput = job; text('crw-output-title',job.title || 'Your video'); text('crw-output-message','');
      $('crw-output-video').poster=`/cr-workspace/preview/project/${encoded(job.slug)}.jpg`;$('crw-output-video').src = `/f/video/${encoded(job.slug)}`; $('crw-output-download').href = `/f/video/${encoded(job.slug)}`;
      $('crw-output-editor').href = `/editor/${encoded(job.slug)}`; $('crw-output-editor').hidden = job.editable === false;
      text('crw-output-editor-label', 'Open timeline');
      // A Ranking Short: its own editor (change a line, swap a clip, remake), the owner's "mini timeline for this format"
      try {const rk = await api(`/cr-ranking/edit/${encoded(job.slug)}`); if (rk && rk.slots) {$('crw-output-editor').href = `/cr-ranking-edit?job=${encoded(job.slug)}`; text('crw-output-editor-label', 'Edit countdown'); $('crw-output-editor').hidden = false;}} catch (_) {}
      const exports = [['audio','Narration','audio'],['script','Script','script'],['srt','Subtitles','srt'],['desc','Description','desc']];
      $('crw-output-exports').replaceChildren(); for (const [key,label,kind] of exports) if (data[key]) {const a=el('a','crw-export-link',label);a.href=`/f/${kind}/${encoded(job.slug)}`;a.download='';$('crw-output-exports').append(a);}
      for (const name of data.thumbs || data.thumbnails || []) {const filename=typeof name==='string'?name:name.name; if (!filename) continue;const a=el('a','crw-export-link','Thumbnail');a.href=`/f/thumb/${encoded(job.slug)}/${encoded(filename)}`;a.download='';$('crw-output-exports').append(a);}
      try {const saved = await api(`/cr-workspace/project/${encoded(job.slug)}`); const lines = (saved.sound_check || []).map(r => (r.state === 'ok' ? '✓ ' : '• ') + r.text); text('crw-output-message', lines.length ? 'Sound check: ' + lines.join(' · ') : '');} catch (_) {/* the sound check is optional */}
      $('crw-output').showModal();
    } catch (e) {toast(e.message);}
  }
  function celebrate(job) {
    const box = $('crw-finished'); if (!box) return;
    box.replaceChildren();
    const copy = el('div','');copy.append(el('strong','',`“${job.title || 'Your video'}” is ready`),el('span','','Watch it, then share it in the Discord. We feature the best community videos.'));
    const watch = el('button','crw-primary-button','Watch video');watch.type='button';watch.addEventListener('click',()=>{box.hidden=true;openOutput({slug:job.slug,title:job.title,editable:true});});
    const share = el('a','crw-secondary-button','Share in Discord ↗');share.href=DISCORD_URL;share.target='_blank';share.rel='noopener';share.dataset.discord='finished-banner';
    const close = el('button','crw-icon-button','×');close.type='button';close.setAttribute('aria-label','Dismiss');close.addEventListener('click',()=>{box.hidden=true;});
    box.append(copy,watch,share,close);box.hidden=false;
    if (document.hidden) {
      state.readyTitle = job.title || 'Your video';
      try {if ('Notification' in window && Notification.permission === 'granted') {const n = new Notification('Your video is ready', {body: state.readyTitle, icon: '/cr-workspace/icon.svg?state=done', tag: 'cr-ready-' + (job.slug || '')}); n.onclick = () => {window.focus(); n.close();};}} catch (_) {/* notifications are optional */}
    }
    tabStatus();
  }
  // The browser tab says what the studio is doing, so a long video can run in the background: a violet dot and
  // "Making …" while it works, a green dot and "Ready: …" when a video finished while you were in another tab.
  const BASE_TITLE = document.title;
  function tabStatus() {
    const running = state.queue.filter(q=>q.state==='running'), queued = state.queue.filter(q=>q.state==='queued').length;
    const study = state.studies.find(x=>x.state==='running');
    let title = BASE_TITLE, dot = '';
    if (state.readyTitle) {title = `Ready: ${state.readyTitle.slice(0,50)} · ChannelRecipe`; dot = 'done';}
    else if (running.length) {title = `Making “${(running[0].title || 'your video').slice(0,40)}”${queued?` (+${queued} queued)`:''} · ChannelRecipe`; dot = 'busy';}
    else if (study) {title = `Learning a style${Number.isFinite(study.percent)?' '+Math.round(study.percent)+'%':''} · ChannelRecipe`; dot = 'busy';}
    if (document.title !== title) document.title = title;
    const icon = $('crw-favicon'), href = '/cr-workspace/icon.svg' + (dot ? '?state=' + dot : '');
    if (icon && icon.getAttribute('href') !== href) icon.setAttribute('href', href);
  }
  document.addEventListener('visibilitychange', () => {if (!document.hidden && state.readyTitle) {state.readyTitle = ''; tabStatus();}});
  // Shorts on hold (cr_queue): a long batch stops on what every Short would hit (clip watches used up, YouTube or
  // Claude limits), or a Studio closed before they started. Nothing of theirs was spent; the creator decides.
  function drawHeld(q) {
    const held = q?.held || [];
    for (const box of all('[data-held-notice]')) {
      box.replaceChildren(); box.hidden = !held.length; if (!held.length) continue;
      const n = held.length, names = held.slice(0, 3).map(h => `“${h.title}”`).join(', ') + (n > 3 ? ` and ${n - 3} more` : '');
      box.append(el('strong', '', `${n} ${n === 1 ? 'video is' : 'videos are'} on hold`),
        el('span', '', `${q.reason ? q.reason + ' ' : ''}Nothing was spent on ${n === 1 ? 'it' : 'them'} yet: ${names}.`));
      const actions = el('div', 'crw-held-actions');
      const go = el('button', 'crw-primary-button', n === 1 ? 'Carry on' : `Carry on with all ${n}`); go.type = 'button';
      // Out of clip watches: carrying on now only helps with credits (0.02 a watch); otherwise it waits for next month.
      const paid = q.code === 'out_of_watches' ? el('button', 'crw-secondary-button', 'Carry on using credits') : null;
      if (paid) paid.type = 'button';
      const drop = el('button', 'crw-secondary-button', 'Remove'); drop.type = 'button';
      const buttons = [go, paid, drop].filter(Boolean), busy = on => buttons.forEach(b => b.disabled = on);
      const resume = async credits => {busy(true); state.heldBusy = true;
        try {const r = await api('/cr-queue/resume', {method: 'POST', body: {use_credits: credits}}); drawHeld(r);
          toast(r.resumed ? `${r.resumed} queued again${credits ? ', using credits if watches run out' : ''}, made one at a time.` : 'Nothing was on hold.'); await refreshActivity();}
        catch (e) {toast(e.message); busy(false);} finally {state.heldBusy = false;}};
      go.addEventListener('click', () => resume(false));
      if (paid) paid.addEventListener('click', () => resume(true));
      drop.addEventListener('click', async () => {if (!confirm(`Remove ${n === 1 ? 'this video' : `these ${n} videos`} from the queue?`)) return; busy(true); state.heldBusy = true;
        try {const r = await api('/cr-queue/drop', {method: 'POST'}); drawHeld(r); toast(r.removed ? 'Removed from the queue.' : 'Nothing was on hold.');}
        catch (e) {toast(e.message); busy(false);} finally {state.heldBusy = false;}});
      actions.append(...buttons); box.append(actions);
    }
  }
  async function refreshActivity(initial = false) {
    if (state.refresh) return state.refresh;
    state.refresh = (async()=>{
      const results = await Promise.allSettled([api('/api/queue'),api('/cr-workspace/activity'), initial || state.page === 'projects' ? api('/api/jobs') : Promise.resolve(null), api('/cr-queue'),
        initial || state.page === 'projects' ? api('/cr-workspace/stopped') : Promise.resolve(null)]);
      if (results[4].status==='fulfilled' && results[4].value) state.stopped=results[4].value.projects || [];
      if (results[3].status==='fulfilled' && !state.heldBusy) drawHeld(results[3].value);
      if (results[0].status==='fulfilled') {
        const watched = state.queue.filter(q=>['running','queued'].includes(q.state));
        state.queue = results[0].value.jobs || [];
        // A project this page watched in production just finished (it either
        // reports done or leaves the queue): confirm with its status, then say so.
        for (const was of watched) {
          const now = state.queue.find(q=>q.slug===was.slug);
          if (now && ['running','queued'].includes(now.state)) continue;
          api(`/api/status/${encoded(was.slug)}`).then(st=>{if(st.done && st.state==='done')celebrate({...was,...(now||{})});}).catch(()=>{});
        }
      }
      if (results[1].status==='fulfilled') {state.busy=results[1].value.busy===true || results[0].status==='fulfilled' && results[0].value.busy===true;state.studies=results[1].value.operations || [];}
      if (results[2].status==='fulfilled' && results[2].value) state.jobs=results[2].value.jobs || [];
      const active = state.studies.find(x=>x.state==='running') || state.queue.find(x=>['running','queued'].includes(x.state));
      const notice=$('crw-active-notice');notice.replaceChildren();notice.hidden=!state.busy;
      if (state.busy) {notice.append(el('strong','',active?.label || active?.title || 'Production in progress'),el('span','',active?.operation_id?'Your reference study is using the creative workspace.':'This studio is finishing a project.'));
        const a=el('a','crw-inline-link','View progress');a.href=active?.link || '#projects';notice.append(a);
        if ('Notification' in window && Notification.permission === 'default') {
          const ask=el('button','crw-inline-link','Notify me when it’s ready');ask.type='button';
          ask.addEventListener('click',async()=>{try{await Notification.requestPermission();}catch(_){}ask.remove();toast(Notification.permission==='granted'?'We’ll let you know when it’s ready, even in another tab.':'Notifications are off. The tab still shows when it’s ready.');});
          notice.append(ask);
        }}
      renderProjects(); if(state.page==='styles')renderStyles(); gate(); tabStatus(); await refreshProgress();
    })().finally(()=>{state.refresh=null;}); return state.refresh;
  }
  function schedule() {clearTimeout(state.timer);state.timer=setTimeout(async()=>{await refreshActivity();schedule();},document.hidden?15000:state.busy||state.openJob?2500:10000);}
  function payload() {
    const title = [$('crw-title').value.trim(),...$('crw-batch-titles').value.split('\n').map(v=>v.trim()).filter(Boolean)].filter(Boolean).join('\n');
    if (!title) throw new Error('Give your video a title or idea.'); if (title.split('\n').some(t=>t.length>280)) throw new Error('Keep each title under 280 characters. Put detailed guidance in Extra instructions.'); if (title.split('\n').length>30) throw new Error('Create up to 30 titles in one batch.'); if (!state.selected) throw new Error('Choose a visual recipe.'); const short=isShort(state.selected); if (short) {const sec=Number($('crw-seconds').value); if (!Number.isInteger(sec)||sec<15||sec>60) throw new Error('Choose a Short length between 15 and 60 seconds.'); if (state.narration==='own') throw new Error('Shorts write and voice their own narration. Choose My idea.');} else {const duration=Number($('crw-minutes').value); if (!Number.isInteger(duration)||duration<1||duration>60) throw new Error('Choose a whole-number duration between 1 and 60 minutes.');}
    if (state.input==='script' && !$('crw-script').value.trim()) throw new Error('Paste your narration script, or choose My idea.');
    if (state.narration==='own' && !state.audio) throw new Error('Choose your narration recording first.');
    if (state.narration==='own' && title.includes('\n')) throw new Error('Use one title for an uploaded narration recording.');
    const features=featureValues(); const steps=['script','voiceover','images','pexels','video'];if(checked('crw-thumbnails'))steps.push('thumbnail');
    const p={title,style:state.selected.name,minutes:number('crw-minutes',10,1,60),steps,
      script:state.input==='script'?$('crw-script').value.trim():'',extra:withPalette($('crw-extra').value.trim(),features),byo_audio:state.narration==='own'?state.audio:'',
      burn_subs:checked('crw-captions'),force:false,thumb_count:number('crw-thumbnail-count',2,1,4),
      desc:checked('crw-description'),chapters:checked('crw-description')?number('crw-chapters',5,0,20):0,
      sources:checked('crw-description')&&checked('crw-sources')?number('crw-source-count',3,0,10):0,
      features,render_backend:features.threed&&checked('crw-fast-toggle')&&!strict()?'manimgx':'original',
      character:strict()?'':$('crw-character').value.trim(),character_image:strict()?'':$('crw-character-image').value,
      avatar_id:features.avatar&&!strict()?$('crw-avatar-id').value:'',avatar_s:number('crw-avatar-seconds',8,0,15),avatar_count:number('crw-avatar-count',1,1,6),
      offer:$('crw-offer').value,offer_plugs:number('crw-offer-plugs',0,0,2),panels:state.selected.panels?state.panels:'',cta:ctaValue(),
      pictures_per_min:state.selected.pictures&&Number($('crw-pictures').value)!==state.selected.pictures.default?Number($('crw-pictures').value):null,
      threed_look:threedLook(features)};
    if(short&&isRanking(state.selected)){const handle=$('crw-rank-handle').value.trim().replace(/[^@\w.]/g,'').slice(0,40);
      // A Short to remake: any YouTube link form (Shorts, watch, youtu.be, m., any case), sent as one plain link, for one topic
      const pasted=$('crw-rank-reference').value.trim(), refId=(/(?:youtu\.be\/|youtube\.com\/(?:shorts\/|watch\?(?:[^#]*&)?v=|live\/|embed\/))([A-Za-z0-9_-]{11})/i.exec(pasted)||[])[1]||'';
      if(pasted&&!refId)throw new Error('Paste a YouTube Short’s link to remake, or leave it empty.');
      if(refId&&title.includes('\n'))throw new Error('A Short to remake goes with one topic. Clear its link, or make that topic on its own.');
      const reference=refId?'https://www.youtube.com/watch?v='+refId:'';
      const ranking={ranks:number('crw-ranks',5,3,10),commentary:checked('crw-rank-commentary'),booms:checked('crw-rank-booms'),sfx:$('crw-rank-sfx').value,
        punch_ins:checked('crw-rank-punch'),clean:checked('crw-rank-clean'),captions:checked('crw-rank-captions'),use_credits:checked('crw-rank-credits'),...(handle?{handle}:{}),...(reference?{reference}:{})};
      p.extra=`[[ranking=${JSON.stringify(ranking)}]] `+(p.extra||'');}
    if(short){p.minutes=1;p.extra=`[[short_seconds=${number('crw-seconds',25,15,60)}]] `+(p.extra||'');p.avatar_id='';p.cta=null;p.offer='';p.character='';p.character_image='';p.threed_look='';}
    if(p.panels && title.includes('\n')) throw new Error('Use one title for a set of panels.');
    if(state.selected.auto_style)p.auto_style=checked('crw-adaptive'); return p;
  }
  // A batch of Ranking Shorts: at most 15, each topic once, and an honest word on clip watches before it starts.
  async function rankingBatchOk(p) {
    if (!isRanking(state.selected)) return true;
    const seen = new Set(), titles = p.title.split('\n').filter(t => {const k = t.toLowerCase().split(/\s+/).join(' '); if (seen.has(k)) return false; seen.add(k); return true;});
    if (titles.length > 15) {error('Queue up to 15 Ranking Shorts at once. They are made one at a time.'); return false;}
    p.title = titles.join('\n');
    if (titles.length < 2) return true;
    let a; try {a = await api('/cr-ranking/allowance');} catch (_) {return true;}   // the run itself checks again
    if (a.limit == null) return true;
    const lo = (a.per_short?.low || 30) * titles.length, hi = (a.per_short?.high || 60) * titles.length;
    const credits = /"use_credits":true/.test(p.extra || '');
    if (a.remaining >= hi || credits) return true;
    const fit = Math.floor(a.remaining / (a.per_short?.high || 60));
    return confirm(`${titles.length} Ranking Shorts use about ${lo} to ${hi} clip watches, and you have ${a.remaining} left this month (about ${fit} Short${fit === 1 ? '' : 's'}). When they run out, the Shorts still waiting go on hold with nothing spent, and you can carry on next month or carry on using credits. Queue all ${titles.length}?`);
  }
  // Studio Cloud: the engine's own recipes can render on a ChannelRecipe machine instead of this computer, paid from
  // the account's cloud balance (ChannelRecipe's add-on recipes, 3D, FOLIO and your own recording stay here for now).
  function cloudBlock() {
    const r=state.selected||{}, f=featureValues();
    if(/^cr-shorts/.test(r.name||''))return 'Shorts render on this computer for now.';
    if(r.name==='cr-manga-recaps'||r.panels)return 'Recaps made from your own panels render on this computer.';
    if(r.name==='folio')return 'FOLIO renders on this computer: every shot is drawn by your Claude Code.';
    if(state.narration==='own')return 'Your own recording renders on this computer.';
    return '';
  }
  function cloudChoice() {
    const info=state.cloudInfo, box=$('crw-cloud-choice'); if(!box)return;
    box.hidden=!info?.available; if(!info?.available){state.renderOn='local';return;}
    if(!state.selected)return;
    const gpu=!!featureValues().threed, m=(gpu?info.machines?.gpu:info.machines?.cpu)||{};
    const why=cloudBlock(), rate=(m.price_per_hr||(gpu?2.4:1.2)).toFixed(2), balance=(info.balance_usd||0).toFixed(2);
    box.querySelector('[data-render-on="cloud"]').disabled=!!why; if(why)state.renderOn='local';
    chooseGroup('[data-render-on]','renderOn',state.renderOn||'local');
    // When the cloud is the better choice, say so: this computer is already making a video, or a long one would tie it up.
    const minutes=number('crw-minutes',10,1,60), suggest=!why&&state.renderOn!=='cloud'
      ? (state.busy?'Your computer is making another video. Studio Cloud can make this one at the same time. '
        :minutes>=15?`A ${minutes}-minute video keeps this computer busy for a long time. Studio Cloud renders it while you keep working. `:'')
      : '';
    box.classList.toggle('is-suggested', !!suggest);
    text('crw-cloud-note', why || (state.renderOn==='cloud'
      ? `The script, voice and pictures are made here with your Claude Code, and YouTube clips come down on your own connection; a ChannelRecipe machine${gpu?' with a graphics card renders the 3D scenes and':''} does the heavy rendering at $${rate} an hour${gpu?'':' (a 10-minute video is usually under $1)'}. It reserves its most before it starts; you pay only what it used. Balance: $${balance}. Keep Studio open while it renders.`
      : `${suggest}Or render on a ChannelRecipe machine at about $${rate} an hour and keep your computer free. Balance: $${balance}.`));
  }
  async function refreshCloud() {
    try {state.cloudInfo=await api('/cr-cloud');} catch(_) {state.cloudInfo=null;}
    cloudChoice(); if(state.page==='projects')cloudPanel();
  }
  async function cloudPanel() {
    if(!state.cloudInfo?.available){hide('crw-cloud-panel',true);return;}
    let d; try {d=await api('/cr-cloud/progress');} catch(_) {return;}
    const working=['preparing','starting','rendering'].includes(d.state)||d.farm==='running';
    hide('crw-cloud-panel', !(working||d.state==='failed'||(d.videos||[]).length));
    text('crw-cloud-balance', d.balance_usd==null?'':`Balance $${Number(d.balance_usd).toFixed(2)}`);
    text('crw-cloud-status', d.error?`${d.title?d.title+': ':''}${d.error}`:d.message?`${d.title?d.title+': ':''}${d.message}`:(d.videos||[]).length?'Finished cloud renders stay here until you delete them.':'');
    text('crw-cloud-log', (d.lines||[]).join('\n'));
    const list=$('crw-cloud-videos'); list.replaceChildren();
    for(const v of d.videos||[]){
      const row=el('div','crw-cloud-video'); row.append(el('strong','',v.title||v.slug));
      if(v.video){const a=el('a','crw-primary-button','Download');a.href=`/api/farm/file/${encoded(v.slug)}/${encoded(v.video)}`;a.target='_blank';a.rel='noopener';row.append(a);}
      const keep=el('button','crw-secondary-button','Save to Projects');keep.type='button';
      keep.addEventListener('click',async()=>{keep.disabled=true;try{await api(`/api/farm/fetch/${encoded(v.slug)}`,{method:'POST'});toast('Saved on this computer with your other projects.');await refreshActivity(true);}catch(e){toast(e.message);}finally{keep.disabled=false;}});
      const drop=el('button','crw-text-button','Delete from the cloud');drop.type='button';
      drop.addEventListener('click',async()=>{if(!confirm('Delete this video from Studio Cloud? A copy saved to Projects stays.'))return;drop.disabled=true;try{await api(`/api/farm/forget/${encoded(v.slug)}`,{method:'POST'});cloudPanel();}catch(e){toast(e.message);drop.disabled=false;}});
      row.append(keep,drop); list.append(row);
    }
    clearTimeout(cloudPanel.timer); if(working&&state.page==='projects')cloudPanel.timer=setTimeout(cloudPanel,10000);
  }
  async function submit(event) {
    event.preventDefault(); if(state.submitting||state.busy)return;error(''); let p;
    try {p=payload();} catch(e){error(e.message);return;}
    if(state.renderOn==='cloud'&&state.cloudInfo?.available&&!cloudBlock()){
      state.submitting=true;gate();$('crw-create').textContent='Starting in the cloud…';
      try {await api('/cr-cloud/start',{method:'POST',body:p});save();showPage('projects');toast('Studio is preparing your video here. A ChannelRecipe machine renders it next.');await cloudPanel();}
      catch(e){error(e.message);} finally{state.submitting=false;$('crw-create').textContent='Create video';gate();}
      return;
    }
    state.submitting=true;gate();$('crw-create').textContent='Checking your video…';
    let ok=false;
    try {ok=await rankingBatchOk(p);} catch(_) {ok=true;}       // the run itself checks again
    if (!ok) {state.submitting=false;$('crw-create').textContent='Create video';gate();return;}
    try {
      // /api/run performs the authoritative option, account and renderer checks
      // before creating work; a second preflight would repeat those requests.
      const result=await api('/api/run',{method:'POST',body:p});$('crw-rank-reference').value='';save();showPage('projects');   // a Short to remake goes with that video only
      toast('Your new video project has started.');await refreshActivity(true);
      const first=(result.queued||[])[0];if(first)await openProgress(typeof first==='string'?{slug:first,title:p.title.split('\n')[0]}:first);
    } catch(e){error((e.details?.blockers||[]).join(' ')||e.message);showPage('create');}finally{state.submitting=false;$('crw-create').textContent='Create video';gate();}
  }
  async function optionalAccounts() {
    if(state.optionalLoaded)return;state.optionalLoaded=true;
    try{const result=await api('/api/characters');for(const row of result.characters||[]){const o=el('option','',row.label||row.file);o.value=row.rel;$('crw-character-image').append(o);}}catch(_){/* Empty reference libraries are optional. */}
    try{const result=await api('/api/products');for(const row of result.products||[]){const o=el('option','',row.title||row.name||row.slug);o.value=row.slug;$('crw-offer').append(o);} hide('crw-offer-section', !(result.products||[]).length);}catch(_){hide('crw-offer-section',true);}
    try{const result=await api(`/api/avatars?style=${encoded(state.selected?.name||'')}`);for(const row of result.avatars||[]){const o=el('option','',row.persona||row.id);o.value=row.id;$('crw-avatar-id').append(o);} if(result.seconds){$('crw-avatar-seconds').min=result.seconds.min;$('crw-avatar-seconds').max=result.seconds.max;}if(result.count)$('crw-avatar-count').max=result.count.max;}catch(_){text('crw-avatar-portraits','Presenter choices are available after account setup.');}
  }
  // Studio's Claude model check lasts a day. When it is the only thing in the way, run it here (two tiny "reply OK"
  // calls on the creator's Claude) instead of sending them back to setup every morning.
  async function autoVerify() {
    const r = state.setup?.readiness || {}, b = r.blockers || [], cc = state.setup?.claude_code || {};
    if (r.ready || state.autoVerified || b.length !== 1 || !/Verify both Claude models/.test(b[0]) || !cc.authenticated || !cc.mcp_connected) return;
    state.autoVerified = true; text('crw-readiness', 'Checking your Claude models (a tiny check, about 20 seconds)…');
    try {await api('/cr-claude/setup', {method: 'POST', body: {action: 'verify'}}); state.setup = await api('/cr-status');}
    catch (_) {/* Setup explains what still needs attention. */}
    finally {gate();}
  }
  async function start() {
    const draft=restore();
    try {
      // the studio's last check draws the page at once; a fresh one follows in the background
      const [catalog,setup]=await Promise.all([api('/cr-workspace/catalog'),api('/cr-status?cached=1')]);state.catalog=catalog;state.setup=setup;
      if(setup.cached)api('/cr-status').then(fresh=>{state.setup=fresh;gate();autoVerify();checkForUpdate();}).catch(()=>{});
      text('crw-release',setup.local_version?`Studio ${setup.local_version.replace(/^5\.3-admin\./,'')}`:'');
      const narration=setup.narration||{};text('crw-narration-name',narration.voice||'Your configured narrator');text('crw-narration-account',[narration.label||providerName(narration.provider),narration.model_label||friendlyModel(narration.model)].filter(Boolean).join(' · ')||'Uses the voice account selected in Accounts.');
      const initial=findRecipe(boot.preset?.style||draft.recipe)||findRecipe('documentary')||rows()[0]; if(!initial)throw new Error('No visual recipes are installed. Update this studio from your ChannelRecipe account.');
      if(boot.preset?.features)state.featureDrafts[initial.name]=boot.preset.features;
      selectRecipe(initial,false);chooseGroup('[data-minutes]','minutes',$('crw-minutes').value);updateOptional();await refreshActivity(true);showPage(location.hash.slice(1)||'create',false);schedule();checkForUpdate();autoVerify();refreshCloud();
    }catch(e){error(e.message);text('crw-readiness','The workspace could not finish loading. Keep the launcher open and refresh this page.');}
    finally{gate();}
  }
  for(const b of all('[data-nav]'))b.addEventListener('click',()=>showPage(b.dataset.nav));
  for(const b of all('[data-input-mode]'))b.addEventListener('click',()=>setInput(b.dataset.inputMode));
  for(const b of all('[data-narration-mode]'))b.addEventListener('click',()=>setNarration(b.dataset.narrationMode));
  for(const b of all('[data-minutes]'))b.addEventListener('click',()=>{$('crw-minutes').value=b.dataset.minutes;chooseGroup('[data-minutes]','minutes',b.dataset.minutes);summary();save();});
  for(const b of all('[data-seconds]'))b.addEventListener('click',()=>{$('crw-seconds').value=b.dataset.seconds;chooseGroup('[data-seconds]','seconds',b.dataset.seconds);summary();});
  for(const b of all('[data-ranks]'))b.addEventListener('click',()=>{$('crw-ranks').value=b.dataset.ranks;chooseGroup('[data-ranks]','ranks',b.dataset.ranks);summary();});
  $('crw-ranks').addEventListener('input',()=>{chooseGroup('[data-ranks]','ranks',$('crw-ranks').value);summary();});
  $('crw-seconds').addEventListener('input',()=>{chooseGroup('[data-seconds]','seconds',$('crw-seconds').value);summary();});
  $('crw-minutes').addEventListener('input',()=>chooseGroup('[data-minutes]','minutes',$('crw-minutes').value));
  for(const b of all('[data-project-filter]'))b.addEventListener('click',()=>{state.projectFilter=b.dataset.projectFilter;chooseGroup('[data-project-filter]','projectFilter',state.projectFilter);renderProjects();});
  for(const b of all('[data-family]'))b.addEventListener('click',()=>{state.family=b.dataset.family;chooseGroup('[data-family]','family',state.family);gallery();});
  $('crw-form').addEventListener('submit',submit);
  for(const b of all('[data-render-on]'))b.addEventListener('click',()=>{if(b.disabled)return;state.renderOn=b.dataset.renderOn;cloudChoice();summary();});
  $('crw-fast-install').addEventListener('click',async()=>{$('crw-fast-install').disabled=true;try{await api('/cr-renderer',{method:'POST',body:{action:'install'}});}catch(e){text('crw-fast-setup-status',e.message);}refreshFast();});
  refreshFast();
  $('crw-form').addEventListener('input',()=>{updateOptional();summary();save();cloudChoice();});
  $('crw-form').addEventListener('change',()=>{updateOptional();summary();save();cloudChoice();});
  $('crw-recipe-open').addEventListener('click',openGallery);$('crw-styles-browse').addEventListener('click',openGallery);
  $('crw-gallery-search').addEventListener('input',gallery);$('crw-gallery-close').addEventListener('click',()=>{$('crw-gallery').close();$('crw-recipe-open').focus();});
  $('crw-detail-use').addEventListener('click',()=>{if(state.inspected){selectRecipe(state.inspected);$('crw-gallery').close();showPage('create');$('crw-recipe-open').focus();}});
  $('crw-output-close').addEventListener('click',()=>{$('crw-output-video').pause();$('crw-output').close();});
  $('crw-output').addEventListener('close',()=>{$('crw-output-video').pause();$('crw-output-video').removeAttribute('src');$('crw-output-video').load();});
  $('crw-output-reveal').addEventListener('click',async()=>{if(!state.currentOutput)return;try{await api(`/api/reveal/${encoded(state.currentOutput.slug)}`,{method:'POST',body:{}});text('crw-output-message','The video folder is open on this computer.');}catch(e){text('crw-output-message',e.message);}});
  $('crw-projects-refresh').addEventListener('click',()=>refreshActivity(true));
  $('crw-progress-cancel').addEventListener('click',async()=>{const b=$('crw-progress-cancel');if(!b.dataset.run||!state.openJob)return;b.disabled=true;try{await api(`/cr-video/${encoded(state.openJob)}/cancel`,{method:'POST',body:{run_id:b.dataset.run}});toast('Cancellation requested. Completed work stays saved.');await refreshActivity();}catch(e){text('crw-progress-note',e.message);}});
  $('crw-presenter-add').addEventListener('click',async()=>{const file=$('crw-presenter-photo').files[0];if(!file){text('crw-presenter-status','Choose a photo of your presenter.');return;}state.uploading=true;gate();text('crw-presenter-status','Adding your presenter…');const form=new FormData();form.append('file',file);form.append('name',$('crw-presenter-name').value);form.append('gender',$('crw-presenter-gender').value);form.append('persona',$('crw-presenter-persona').value);try{const result=await api('/cr-workspace/presenter',{method:'POST',body:form});const row=result.presenter;const o=el('option','',row.name||row.id);o.value=row.id;$('crw-avatar-id').prepend(o);$('crw-avatar-id').value=row.id;text('crw-presenter-status',`${row.name} is ready to present.`);}catch(e){text('crw-presenter-status',e.message);}finally{state.uploading=false;gate();}});
  $('crw-pictures').addEventListener('input',()=>{showPictures();summary();});
  $('crw-cta-on').addEventListener('change',()=>{hide('crw-cta-fields',!checked('crw-cta-on'));save();});
  $('crw-cta-image').addEventListener('change',async()=>{const file=$('crw-cta-image').files[0];if(!file)return;state.uploading=true;gate();text('crw-cta-status','Saving your picture…');const form=new FormData();form.append('file',file);try{const result=await api('/cr-workspace/cta-image',{method:'POST',body:form});state.ctaImage=result.image;text('crw-cta-status','Your picture is on the card.');}catch(e){state.ctaImage='';text('crw-cta-status',e.message);}finally{state.uploading=false;gate();}});
  $('crw-panels-recent').addEventListener('change',()=>{showPanels((state.panelSets||[]).find(s=>s.id===$('crw-panels-recent').value));});
  $('crw-panels-file').addEventListener('change',async()=>{const files=[...$('crw-panels-file').files];if(!files.length)return;state.uploading=true;gate();text('crw-panels-status',`Saving ${files.length} file${files.length>1?'s':''}…`);const form=new FormData();for(const f of files)form.append('files',f);try{const result=await api('/cr-workspace/panels',{method:'POST',body:form});showPanels(result.panels);}catch(e){text('crw-panels-status',e.message);}finally{state.uploading=false;$('crw-panels-file').value='';gate();}});
  $('crw-audio-recent').addEventListener('change',()=>{state.audio=$('crw-audio-recent').value;$('crw-audio-player').src=`/api/voiceover/${encoded(state.audio)}`;hide('crw-audio-player',!state.audio);summary();gate();});
  $('crw-audio-file').addEventListener('change',async()=>{const file=$('crw-audio-file').files[0];if(!file)return;state.uploading=true;gate();text('crw-audio-status','Saving your narration recording…');const form=new FormData();form.append('file',file);try{const result=await api('/api/voiceover',{method:'POST',body:form});state.audio=result.voiceover.id;$('crw-audio-player').src=`/api/voiceover/${encoded(state.audio)}`;hide('crw-audio-player',false);text('crw-audio-status',`${result.voiceover.name} · ${Math.round(result.voiceover.seconds)} seconds`);}catch(e){text('crw-audio-status',e.message);}finally{state.uploading=false;summary();gate();}});
  for(const detail of all('details'))detail.addEventListener('toggle',()=>{if(detail.open&&detail.querySelector('#crw-character'))optionalAccounts();});
  window.addEventListener('hashchange',()=>showPage(location.hash.slice(1)||'create',false));
  document.addEventListener('visibilitychange',schedule);
  if(!posterObserver)window.addEventListener('scroll',visiblePosters,{passive:true});
  window.CR_WORKSPACE_CLIENT={payload,selectRecipe,featureValues,api,state};


  async function copyDiagnostics(button,status){
 const label=button.textContent;button.disabled=true;button.textContent='Copying…';
 try{const response=await fetch('/cr-diagnostics',{headers:{'X-CR-Local-Token':boot.token||''},cache:'no-store'});if(!response.ok)throw Error('Refresh this page and try again.');const text=await response.text();
  try{await navigator.clipboard.writeText(text);}catch(_){const area=document.createElement('textarea');area.value=text;document.body.append(area);area.select();document.execCommand('copy');area.remove();}
  status.textContent='Copied. Paste it in Discord #help with a screenshot of what you see.';}
 catch(error){status.textContent=error.message;}
 finally{button.disabled=false;button.textContent=label;}
}
  $('crw-copy-diagnostics').addEventListener('click', () => copyDiagnostics($('crw-copy-diagnostics'), $('crw-diagnostics-status')));

  // Narration voice picker: hear a voice, then choose it for your next videos.
  const VOICE_STYLES = {Zephyr:'Bright',Puck:'Upbeat',Charon:'Informative',Kore:'Firm',Fenrir:'Excitable',Leda:'Youthful',Orus:'Firm',Aoede:'Breezy',Callirrhoe:'Easy-going',Autonoe:'Bright',Enceladus:'Breathy',Iapetus:'Clear',Umbriel:'Easy-going',Algieba:'Smooth',Despina:'Smooth',Erinome:'Clear',Algenib:'Gravelly',Rasalgethi:'Informative',Laomedeia:'Upbeat',Achernar:'Soft',Alnilam:'Firm',Schedar:'Even',Gacrux:'Mature',Pulcherrima:'Forward',Achird:'Friendly',Zubenelgenubi:'Casual',Vindemiatrix:'Gentle',Sadachbia:'Lively',Sadaltager:'Knowledgeable',Sulafat:'Warm'};
  const voices = {data: null, previews: new Set(), playing: null};
  function sharedSample(name) {const samples = state.setup?.account?.voice_samples?.[voices.data?.model] || {}; return samples[name] || '';}
  function renderVoices() {
    const list = $('crw-voices-list'), query = $('crw-voices-search').value.trim().toLowerCase(), data = voices.data;
    list.replaceChildren();
    const names = data.voices.filter(name => !query || name.toLowerCase().includes(query) || (VOICE_STYLES[name] || '').toLowerCase().includes(query));
    const ordered = [data.voice, ...names.filter(name => name !== data.voice && VOICE_STYLES[name]), ...names.filter(name => name !== data.voice && !VOICE_STYLES[name])].filter(name => names.includes(name));
    for (const name of ordered) {
      const row = el('div', 'crw-voice-row' + (name === data.voice ? ' is-current' : '')); row.setAttribute('role', 'listitem');
      const play = el('button', 'crw-secondary-button crw-voice-play', '▶'); play.type = 'button'; play.setAttribute('aria-label', 'Preview ' + name); play.addEventListener('click', () => previewVoice(name, play));
      const copy = el('div'); copy.append(el('strong', '', name), el('span', '', VOICE_STYLES[name] ? VOICE_STYLES[name] + ' voice' : 'Gemini voice'));
      row.append(play, copy);
      if (name === data.voice) row.append(el('span', 'crw-voice-badge', 'Selected'));
      else {const use = el('button', 'crw-primary-button', 'Use'); use.type = 'button'; use.addEventListener('click', () => chooseVoice(name, use)); row.append(use);}
      list.append(row);
    }
    if (!ordered.length) list.append(el('p', 'crw-voices-note', 'No voices match that search.'));
  }
  // ElevenLabs connected directly (cr_voice_library): the creator's own voices and ElevenLabs' shared library, each
  // with its own free sample, kept per recipe. The media-hub picker above is unchanged.
  const library = {source: 'mine', rows: [], timer: null};
  async function loadLibrary() {
    const q = $('crw-voices-search').value.trim(), source = library.source;
    text('crw-voices-status', 'Loading voices…');
    try {const r = await api(`/cr-voices/library?source=${source}&search=${encodeURIComponent(q)}`);
      if (source !== library.source) return;                   // a newer tab or search answered first
      library.rows = r.voices || []; text('crw-voices-status', r.note || '');}
    catch (e) {library.rows = []; text('crw-voices-status', e.message);}
    renderLibrary();
  }
  function renderLibrary() {
    const list = $('crw-voices-list'), data = voices.data, audio = $('crw-voices-audio');
    list.replaceChildren();
    for (const v of library.rows) {
      const current = v.id === data.voice_id;
      const row = el('div', 'crw-voice-row' + (current ? ' is-current' : '')); row.setAttribute('role', 'listitem');
      const play = el('button', 'crw-secondary-button crw-voice-play', '▶'); play.type = 'button'; play.setAttribute('aria-label', 'Preview ' + v.name); play.disabled = !v.preview;
      play.addEventListener('click', () => {if (voices.playing === v.id && !audio.paused) {audio.pause(); return;}
        voices.playing = v.id; audio.src = v.preview; audio.play().catch(() => text('crw-voices-status', 'Press ▶ again to play.'));});
      const copy = el('div'); copy.append(el('strong', '', v.name), el('span', '', [v.gender, v.accent, v.age, v.description].filter(Boolean).join(' · ') || 'ElevenLabs voice'));
      row.append(play, copy);
      if (current) row.append(el('span', 'crw-voice-badge', 'Selected'));
      else {const use = el('button', 'crw-primary-button', 'Use'); use.type = 'button'; use.addEventListener('click', () => chooseLibraryVoice(v, use)); row.append(use);}
      list.append(row);
    }
    if (!library.rows.length) list.append(el('p', 'crw-voices-note', library.source === 'mine'
      ? 'No voices on your ElevenLabs account match. Try the Voice library.' : 'No voices match that search.'));
  }
  async function chooseLibraryVoice(v, button) {
    button.disabled = true;
    try {
      const result = await api('/cr-voices/select', {method: 'POST', body: {voice_id: v.id, name: v.name, gender: v.gender, style: voices.data.style}});
      voices.data.voice = v.name; voices.data.voice_id = v.id; if (state.setup?.narration) state.setup.narration.voice = v.name;
      text('crw-narration-name', v.name); text('crw-voices-status', result.message); renderLibrary(); summary();
    } catch (e) {text('crw-voices-status', e.message); button.disabled = false;}
  }
  for (const b of all('[data-voice-source]')) b.addEventListener('click', () => {library.source = b.dataset.voiceSource; chooseGroup('[data-voice-source]', 'voiceSource', library.source); loadLibrary();});
  async function openVoices() {
    text('crw-voices-status', ''); $('crw-voices-search').value = '';
    try {voices.data = await api('/cr-voices?style=' + encodeURIComponent(state.selected?.name || ''));} catch (e) {error(e.message); return;}
    if (!voices.data.connected) {error('Connect a voice account in Setup & accounts first.'); return;}
    $('crw-voices-sources').hidden = !voices.data.library;
    if (voices.data.library) {
      if (!voices.data.style) {error('Choose a recipe first, then its voice.'); return;}
      text('crw-voices-sub', `ElevenLabs · for ${state.selected?.label || state.selected?.name || 'this recipe'}`);
      text('crw-voices-note', 'Press ▶ to hear a voice (free). Your choice is kept for this recipe.');
      library.source = 'mine'; chooseGroup('[data-voice-source]', 'voiceSource', 'mine');
      $('crw-voices').showModal(); $('crw-voices-search').focus(); await loadLibrary(); return;
    }
    if (!voices.data.voices.length) {location.href = '/cr-setup'; return;}
    voices.previews = new Set(voices.data.previews || []);
    text('crw-voices-sub', `${voices.data.model_label} on ${voices.data.provider_label} · ${voices.data.voices.length} voices`);
    const shared = Object.keys(state.setup?.account?.voice_samples?.[voices.data.model] || {}).length;
    text('crw-voices-note', shared ? 'Press ▶ to hear a voice.' : `Press ▶ to hear a voice. The first play of each voice makes a short sample with your ${voices.data.provider_label} account (a tiny amount of your balance); after that it replays free.`);
    renderVoices(); $('crw-voices').showModal(); $('crw-voices-search').focus();
  }
  async function previewVoice(name, button) {
    const audio = $('crw-voices-audio');
    if (voices.playing === name && !audio.paused) {audio.pause(); return;}
    let url = sharedSample(name) || (voices.previews.has(name) ? '/cr-voices/sample/' + encodeURIComponent(name) : '');
    if (!url) {
      button.classList.add('is-loading'); button.disabled = true; text('crw-voices-status', `Making a short ${name} sample…`);
      try {url = (await api('/cr-voices/preview', {method: 'POST', body: {voice: name}})).url; voices.previews.add(name); text('crw-voices-status', '');}
      catch (e) {text('crw-voices-status', e.message); return;}
      finally {button.classList.remove('is-loading'); button.disabled = false;}
    }
    voices.playing = name; audio.src = url; audio.play().catch(() => text('crw-voices-status', 'Press ▶ again to play.'));
  }
  async function chooseVoice(name, button) {
    button.disabled = true;
    try {
      const result = await api('/cr-voices/select', {method: 'POST', body: {voice: name}});
      voices.data.voice = name; if (state.setup?.narration) state.setup.narration.voice = name;
      text('crw-narration-name', name); text('crw-voices-status', result.message); renderVoices(); summary();
    } catch (e) {text('crw-voices-status', e.message); button.disabled = false;}
  }
  $('crw-voice-open').addEventListener('click', openVoices);
  $('crw-voices-search').addEventListener('input', () => {if (voices.data?.library) {clearTimeout(library.timer); library.timer = setTimeout(loadLibrary, 350);} else renderVoices();});
  $('crw-voices-close').addEventListener('click', () => $('crw-voices').close());
  $('crw-voices').addEventListener('close', () => $('crw-voices-audio').pause());

  // Updates are optional and explained. A banner offers them, the Discord
  // links mention them once (the fix may already be out), and Update now
  // downloads only what changed, then restarts Studio when nothing is running.
  const updates = {timer: null, from: 0, discordShown: false, status: null};
  function buildNumber(value) {const m = String(value || '').match(/(\d+)$/); return m ? Number(m[1]) : 0;}
  function pendingUpdate() {
    const s = state.setup || {}, local = buildNumber(s.local_version), remote = buildNumber(s.account?.version);
    if (!local || remote <= local) return null;
    return {local, remote, entries: (s.account?.changelog || []).filter(e => Number(e.build) > local)};
  }
  function laterKey(info) {return 'crw-update-later-' + info.remote;}
  // New videos need the Studio release your account offers (each project pins it); a project already running
  // finishes on this one. While an update is required the banner stays, so Update now is always one click away.
  const mustUpdate = () => (state.setup?.readiness?.checks || []).some(c => c.key === 'release' && !c.ready);
  function dismissed(info) {if (mustUpdate()) return false; try {return localStorage.getItem(laterKey(info)) === '1';} catch (_) {return false;}}
  function showWhatsNew(mode) {
    const info = pendingUpdate(); if (!info) return false;
    const list = $('crw-whatsnew-list'); list.replaceChildren();
    for (const entry of info.entries) {const section = el('section'); section.append(el('h3', '', `Studio ${entry.build} · ${entry.title || 'Improvements'}`)); const ul = el('ul'); for (const item of entry.items || []) {const m = /^(New|Improved|Fixed):\s*/.exec(item); const li = el('li'); if (m) {li.className = 'crw-tagged'; li.append(el('span', `crw-tag crw-tag-${m[1].toLowerCase()}`, m[1]), document.createTextNode(item.slice(m[0].length)));} else li.textContent = item; ul.append(li);} section.append(ul); list.append(section);}
    if (!info.entries.length) list.append(el('p', 'crw-whatsnew-note', 'Fixes and improvements.'));
    text('crw-whatsnew-title', mode === 'discord' ? `Before you ask: Studio ${info.remote} is out` : `What’s new in Studio ${info.remote}`);
    text('crw-whatsnew-note', mode === 'discord' ? 'Your issue may already be fixed. Updating takes about a minute and keeps your projects, accounts and styles.' : 'Updating takes about a minute. Your projects, accounts and styles stay as they are, and your current version keeps working if you wait.');
    hide('crw-whatsnew-discord', mode !== 'discord');
    $('crw-whatsnew').showModal(); return true;
  }
  function updateBanner(status) {
    const box = $('crw-update'), info = pendingUpdate(); updates.status = status || updates.status;
    const s = updates.status || {state: 'idle'};
    if (!info && !['restarting', 'staged'].includes(s.state)) {box.hidden = true; return;}
    box.replaceChildren(); const copy = el('div', ''); box.append(copy);
    const button = (label, cls, handler) => {const b = el('button', cls, label); b.type = 'button'; b.addEventListener('click', handler); box.append(b); return b;};
    if (s.state === 'downloading') {
      copy.append(el('strong', '', 'Downloading the update…'), el('span', '', s.total ? `${Math.round(100 * s.done / s.total)}% · only the files that changed` : 'Finding the files that changed'));
      const bar = el('progress'); bar.max = s.total || 1; bar.value = s.done || 0; if (!s.total) bar.removeAttribute('value'); copy.append(bar);
    } else if (s.state === 'staged') {
      copy.append(el('strong', '', 'The update is ready'), el('span', '', 'Restart Studio to finish. It takes a few seconds and this page reloads by itself.'));
      button('Restart now', 'crw-primary-button', restartForUpdate);
    } else if (s.state === 'restarting') {
      copy.append(el('strong', '', 'Restarting Studio…'), el('span', '', 'This page reloads by itself in a few seconds.'));
    } else if (s.state === 'failed') {
      copy.append(el('strong', '', 'The update didn’t finish'), el('span', '', s.message || 'Try again in a moment.'));
      button('Try again', 'crw-secondary-button', startUpdate);
    } else {
      if (dismissed(info)) {box.hidden = true; return;}
      const first = info.entries[0];
      copy.append(el('strong', '', `Studio ${info.remote} is available`), el('span', '', (first ? `${first.title}${info.entries.length > 1 ? ` and ${info.entries.length - 1} more update${info.entries.length > 2 ? 's' : ''}` : ''}.` : 'Fixes and improvements.') + (mustUpdate() ? ' Update to keep making new videos; your projects and settings stay.' : ' It takes a minute and keeps your projects and settings.')));
      button('What’s new', 'crw-secondary-button', () => showWhatsNew('news'));
      button('Update now', 'crw-primary-button', startUpdate);
      const close = button('×', 'crw-icon-button', () => {try {localStorage.setItem(laterKey(info), '1');} catch (_) {} box.hidden = true;}); close.setAttribute('aria-label', 'Remind me later');
    }
    box.hidden = false;
  }
  async function pollUpdate() {
    clearTimeout(updates.timer);
    try {const s = await api('/cr-update'); updateBanner(s); if (s.state === 'downloading') updates.timer = setTimeout(pollUpdate, 1000);} catch (_) {}
  }
  async function startUpdate() {
    if ($('crw-whatsnew').open) $('crw-whatsnew').close();
    try {updateBanner(await api('/cr-update/download', {method: 'POST', body: {}}));} catch (e) {updateBanner({state: 'failed', message: e.message});}
    pollUpdate();
  }
  async function restartForUpdate() {
    updates.from = buildNumber(state.setup?.local_version);
    try {updateBanner(await api('/cr-update/restart', {method: 'POST', body: {}})); setTimeout(waitForRestart, 3000);}
    catch (e) {updateBanner({...updates.status, state: 'staged'}); error(e.message);}
  }
  async function waitForRestart() {
    try {
      const response = await fetch('/cr-status', {cache: 'no-store'});
      if (response.ok) {const s = await response.json(); if (buildNumber(s.local_version) > updates.from) {try {sessionStorage.setItem('crw-updated', String(buildNumber(s.local_version)));} catch (_) {} location.reload(); return;}}
    } catch (_) {}
    setTimeout(waitForRestart, 1500);
  }
  function announceUpdated() {
    let build = ''; try {build = sessionStorage.getItem('crw-updated') || ''; sessionStorage.removeItem('crw-updated');} catch (_) {}
    if (!build) return;
    const box = $('crw-finished'); box.replaceChildren();
    const copy = el('div', ''); copy.append(el('strong', '', `Updated to Studio ${build}`), el('span', '', 'Your projects, accounts and styles are just as you left them.'));
    const close = el('button', 'crw-icon-button', '×'); close.type = 'button'; close.setAttribute('aria-label', 'Dismiss'); close.addEventListener('click', () => {box.hidden = true;});
    box.append(copy, close); box.hidden = false;
  }
  async function checkForUpdate() {
    announceUpdated();
    if (!pendingUpdate()) return;
    try {updateBanner(await api('/cr-update'));} catch (_) {updateBanner({state: 'idle'});}
    if (updates.status?.state === 'downloading') pollUpdate();
  }
  $('crw-whatsnew-update').addEventListener('click', startUpdate);
  $('crw-whatsnew-later').addEventListener('click', () => $('crw-whatsnew').close());
  $('crw-whatsnew-close').addEventListener('click', () => $('crw-whatsnew').close());
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-discord]');
    if (!link || link.dataset.discord === 'finished-banner' || updates.discordShown || !pendingUpdate()) return;
    updates.discordShown = true; event.preventDefault(); showWhatsNew('discord');
  }, true);
  start();
})();
