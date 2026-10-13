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
  function selectRecipe(recipe, persist = true) {
    if (state.selected) state.featureDrafts[state.selected.name] = featureValues();
    state.selected = recipe;
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
    updateOptional(); summary(); gate(); if (persist) save();
  }
  function updateOptional() {
    const features = featureValues(); hide('crw-fast-section', !features.threed || strict());
    hide('crw-avatar-section', !features.avatar || strict());
    hide('crw-character-section', strict());
    hide('crw-offer-section', $('crw-offer').options.length<2 || !features.avatar || strict());
    hide('crw-description-options', !checked('crw-description')); hide('crw-thumbnail-options', !checked('crw-thumbnails'));
    text('crw-fast-status', checked('crw-fast-toggle') ? 'Fast mode is selected for every 3D scene. Its installed setup will be checked before starting.' : 'Standard scene rendering is selected.');
  }
  function summary() {
    const r = state.selected; text('crw-summary-title', $('crw-title').value.trim() || 'Your next video');
    text('crw-summary-recipe', r?.label || 'Choose a recipe');
    text('crw-summary-duration', state.narration === 'own' ? 'Length of your recording' : `${number('crw-minutes',10,1,60)} minutes`);
    text('crw-summary-visuals', strict(r) ? 'Reference animation throughout' : Object.entries(featureValues()).filter(([k,v]) => v && k !== 'sound').map(([k]) => (featureCopy[k] || [k])[0]).join(', ') || 'Recipe defaults');
    text('crw-summary-voice', state.narration === 'own' ? 'Your uploaded narration' : state.setup?.narration?.voice || 'Connected narration account');
    text('crw-summary-renderer', checked('crw-fast-toggle') && featureValues().threed && !strict() ? 'Fast mode · local render' : 'Local render');
  }
  function gate() {
    const ready = state.setup?.readiness?.ready === true;
    const blocked = state.submitting || state.uploading || !state.catalog || !ready || state.busy || (state.narration === 'own' && !state.audio);
    $('crw-create').disabled = blocked;
    let message = !state.catalog ? 'Loading your visual recipes…' : !ready ? (state.setup?.readiness?.blockers || ['Finish account setup before creating a video.']).join(' ') : state.busy ? 'A project is using this studio. Its progress is available in Projects.' : state.narration === 'own' && !state.audio ? 'Choose a narration recording to continue.' : 'Ready. Each Create starts a separate new project.';
    text('crw-readiness', message);
    const status = $('crw-connection'); status.querySelector('span:last-of-type').textContent = ready ? 'Accounts connected' : 'Accounts need attention';
    status.querySelector('.crw-status-dot').className = `crw-status-dot ${ready ? 'is-ready' : 'is-warning'}`;
  }
  function showPage(name, persist = true) {
    state.page = ['create','projects','styles'].includes(name) ? name : 'create';
    if(state.page!=='projects')for(const player of all('.crw-video-preview'))player.pause();
    for (const p of ['create','projects','styles']) hide(`crw-page-${p}`, p !== state.page);
    for (const button of all('.crw-nav [data-nav]')) {const active = button.dataset.nav === state.page; button.classList.toggle('is-active', active); active ? button.setAttribute('aria-current','page') : button.removeAttribute('aria-current');}
    text('crw-page-name', {create:'Create video',projects:'Your projects',styles:'Style library'}[state.page]);
    if (persist) history.replaceState(null,'',`${location.pathname}${location.search}#${state.page}`);
    if (state.page === 'projects') renderProjects(); if (state.page === 'styles') renderStyles();
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
    const query = $('crw-gallery-search').value.trim().toLowerCase();
    const recipes = rows().filter(r => (state.family === 'all' || (state.family === 'custom' ? r.custom : familyKey(r.family) === state.family)) && [r.label,r.description,...(r.capabilities || [])].join(' ').toLowerCase().includes(query));
    $('crw-gallery-grid').replaceChildren(...recipes.map(r => recipeCard(r, selected=>inspect(selected,true)))); text('crw-gallery-count', `${recipes.length} ${recipes.length === 1 ? 'recipe' : 'recipes'}`); hide('crw-gallery-empty',!!recipes.length); hide('crw-gallery-detail',!recipes.length);
    if (state.inspected && recipes.includes(state.inspected)) inspect(state.inspected); else if (recipes[0]) inspect(recipes[0]);
  }
  function openGallery() {gallery(); $('crw-gallery').showModal(); $('crw-gallery-search').focus();}
  function renderStyles() {
    const customs = rows().filter(r => r.custom); const target = $('crw-styles-list'); target.replaceChildren();
    for (const recipe of customs) {const card = recipeCard(recipe,r => {selectRecipe(r); showPage('create');}); target.append(card);}
    hide('crw-styles-empty',!!customs.length);
    if (state.studies.length) {
      const heading = el('h3','crw-library-subheading','Reference studies'); target.append(heading);
      for (const study of state.studies) {const a = el('a','crw-study-card'); a.href = study.link; a.append(el('strong','',study.label || study.title),el('span','crw-card-meta',`${study.state === 'running' ? 'In progress' : study.state === 'done' ? 'Saved' : 'Stopped'} · ${study.phase || 'study'}`)); target.append(a);}
    }
  }
  const phaseCopy = line => {
    const v = String(line || ''); if (/DONE|published:/i.test(v)) return 'Finishing your video';
    if (/ffmpeg|final render|concat|mux/i.test(v)) return 'Rendering the film'; if (/review|critique|verify|repair/i.test(v)) return 'Reviewing visuals';
    if (/subtitles|whisper|timing narration/i.test(v)) return 'Timing narration and captions'; if (/voice|tts|narration/i.test(v)) return 'Creating narration';
    if (/design|scene|graphic|image|motion|kit/i.test(v)) return 'Building visual scenes'; if (/script|fact check/i.test(v)) return 'Writing and checking the story'; return 'Preparing the project';
  };
  function statusBadge(value) {const kind = value === 'done' ? 'done' : ['running','queued'].includes(value) ? 'running' : 'stopped'; return el('span',`crw-status-badge is-${kind}`,{done:'Ready',running:'In production',queued:'Queued',cancelled:'Cancelled',error:'Needs review',failed:'Needs review'}[value] || 'Saved');}
  function combinedProjects() {
    const map = new Map(state.jobs.map(j => [j.slug,{...j,state:'done',hasVideo:true}]));
    for (const q of state.queue) map.set(q.slug,{...(map.get(q.slug) || {}),...q});
    return [...map.values()];
  }
  function projectCard(job) {
    const card=el('article','crw-project-card');card.dataset.slug=job.slug;
    const r=findRecipe(job.style);const media=el('div','crw-card-art');
    if(job.hasVideo) {
      const video=el('video','crw-video-preview');video.controls=true;video.playsInline=true;video.preload='none';
      video.dataset.previewPoster=`/cr-workspace/preview/project/${encoded(job.slug)}.jpg`;video.src=`/f/video/${encoded(job.slug)}`;
      video.setAttribute('aria-label',job.title||'Video project');
      const play=el('button','crw-preview-play','▶');play.type='button';play.setAttribute('aria-label',`Play ${job.title||'video'}`);
      play.addEventListener('click',()=>{for(const other of all('.crw-video-preview'))if(other!==video)other.pause();video.play().catch(()=>toast('The video could not start. Open its saved output or check your launcher.'));});
      video.addEventListener('play',()=>{for(const other of all('.crw-video-preview'))if(other!==video)other.pause();play.hidden=true;});video.addEventListener('pause',()=>{play.hidden=false;});video.addEventListener('ended',()=>{play.hidden=false;});
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
      const data = await api(`/api/status/${encoded(state.openJob)}`); const lines = data.lines || [];
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
        }
      }
    } catch (e) {text('crw-progress-note',e.message);}
  }
  async function openOutput(job) {
    try {
      const data = await api(`/api/result/${encoded(job.slug)}`); if (!data.video) throw new Error('This project has no finished video to open yet.');
      for(const player of all('.crw-video-preview'))player.pause();state.currentOutput = job; text('crw-output-title',job.title || 'Your video'); text('crw-output-message','');
      $('crw-output-video').poster=`/cr-workspace/preview/project/${encoded(job.slug)}.jpg`;$('crw-output-video').src = `/f/video/${encoded(job.slug)}`; $('crw-output-download').href = `/f/video/${encoded(job.slug)}`;
      $('crw-output-editor').href = `/editor/${encoded(job.slug)}`; $('crw-output-editor').hidden = job.editable === false;
      const exports = [['audio','Narration','audio'],['script','Script','script'],['srt','Subtitles','srt'],['desc','Description','desc']];
      $('crw-output-exports').replaceChildren(); for (const [key,label,kind] of exports) if (data[key]) {const a=el('a','crw-export-link',label);a.href=`/f/${kind}/${encoded(job.slug)}`;a.download='';$('crw-output-exports').append(a);}
      for (const name of data.thumbs || data.thumbnails || []) {const filename=typeof name==='string'?name:name.name; if (!filename) continue;const a=el('a','crw-export-link','Thumbnail');a.href=`/f/thumb/${encoded(job.slug)}/${encoded(filename)}`;a.download='';$('crw-output-exports').append(a);}
      $('crw-output').showModal();
    } catch (e) {toast(e.message);}
  }
  async function refreshActivity(initial = false) {
    if (state.refresh) return state.refresh;
    state.refresh = (async()=>{
      const results = await Promise.allSettled([api('/api/queue'),api('/cr-workspace/activity'), initial || state.page === 'projects' ? api('/api/jobs') : Promise.resolve(null)]);
      if (results[0].status==='fulfilled') state.queue = results[0].value.jobs || [];
      if (results[1].status==='fulfilled') {state.busy=results[1].value.busy===true || results[0].status==='fulfilled' && results[0].value.busy===true;state.studies=results[1].value.operations || [];}
      if (results[2].status==='fulfilled' && results[2].value) state.jobs=results[2].value.jobs || [];
      const active = state.studies.find(x=>x.state==='running') || state.queue.find(x=>['running','queued'].includes(x.state));
      const notice=$('crw-active-notice');notice.replaceChildren();notice.hidden=!state.busy;
      if (state.busy) {notice.append(el('strong','',active?.label || active?.title || 'Production in progress'),el('span','',active?.operation_id?'Your reference study is using the creative workspace.':'This studio is finishing a project.'));
        const a=el('a','crw-inline-link','View progress');a.href=active?.link || '#projects';notice.append(a);}
      renderProjects(); if(state.page==='styles')renderStyles(); gate(); await refreshProgress();
    })().finally(()=>{state.refresh=null;}); return state.refresh;
  }
  function schedule() {clearTimeout(state.timer);state.timer=setTimeout(async()=>{await refreshActivity();schedule();},document.hidden?15000:state.busy||state.openJob?2500:10000);}
  function payload() {
    const title = [$('crw-title').value.trim(),...$('crw-batch-titles').value.split('\n').map(v=>v.trim()).filter(Boolean)].filter(Boolean).join('\n');
    if (!title) throw new Error('Give your video a title or idea.'); if (title.split('\n').some(t=>t.length>280)) throw new Error('Keep each title under 280 characters. Put detailed guidance in Extra instructions.'); if (title.split('\n').length>30) throw new Error('Create up to 30 titles in one batch.'); const duration=Number($('crw-minutes').value); if (!Number.isInteger(duration)||duration<1||duration>60) throw new Error('Choose a whole-number duration between 1 and 60 minutes.'); if (!state.selected) throw new Error('Choose a visual recipe.');
    if (state.input==='script' && !$('crw-script').value.trim()) throw new Error('Paste your narration script, or choose My idea.');
    if (state.narration==='own' && !state.audio) throw new Error('Choose your narration recording first.');
    if (state.narration==='own' && title.includes('\n')) throw new Error('Use one title for an uploaded narration recording.');
    const features=featureValues(); const steps=['script','voiceover','images','pexels','video'];if(checked('crw-thumbnails'))steps.push('thumbnail');
    const p={title,style:state.selected.name,minutes:number('crw-minutes',10,1,60),steps,
      script:state.input==='script'?$('crw-script').value.trim():'',extra:$('crw-extra').value.trim(),byo_audio:state.narration==='own'?state.audio:'',
      burn_subs:checked('crw-captions'),force:false,thumb_count:number('crw-thumbnail-count',2,1,4),
      desc:checked('crw-description'),chapters:checked('crw-description')?number('crw-chapters',5,0,20):0,
      sources:checked('crw-description')&&checked('crw-sources')?number('crw-source-count',3,0,10):0,
      features,render_backend:features.threed&&checked('crw-fast-toggle')&&!strict()?'manimgx':'original',
      character:strict()?'':$('crw-character').value.trim(),character_image:strict()?'':$('crw-character-image').value,
      avatar_id:features.avatar&&!strict()?$('crw-avatar-id').value:'',avatar_s:number('crw-avatar-seconds',8,0,15),avatar_count:number('crw-avatar-count',1,1,6),
      offer:$('crw-offer').value,offer_plugs:number('crw-offer-plugs',0,0,2)};
    if(state.selected.auto_style)p.auto_style=checked('crw-adaptive'); return p;
  }
  async function submit(event) {
    event.preventDefault(); if(state.submitting||state.busy)return;error(''); let p;
    try {p=payload();} catch(e){error(e.message);return;}
    state.submitting=true;gate();$('crw-create').textContent='Checking your video…';
    try {
      // /api/run performs the authoritative option, account and renderer checks
      // before creating work; a second preflight would repeat those requests.
      const result=await api('/api/run',{method:'POST',body:p});save();showPage('projects');
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
  async function start() {
    const draft=restore();
    try {
      const [catalog,setup]=await Promise.all([api('/cr-workspace/catalog'),api('/cr-status')]);state.catalog=catalog;state.setup=setup;
      text('crw-release',setup.local_version?`Studio ${setup.local_version.replace(/^5\.3-admin\./,'')}`:'');
      const narration=setup.narration||{};text('crw-narration-name',narration.voice||'Your configured narrator');text('crw-narration-account',[narration.label,narration.model_label||narration.model].filter(Boolean).join(' · ')||'Uses the voice account selected in Accounts.');
      const initial=findRecipe(boot.preset?.style||draft.recipe)||findRecipe('documentary')||rows()[0]; if(!initial)throw new Error('No visual recipes are installed. Update this studio from your ChannelRecipe account.');
      if(boot.preset?.features)state.featureDrafts[initial.name]=boot.preset.features;
      selectRecipe(initial,false);chooseGroup('[data-minutes]','minutes',$('crw-minutes').value);updateOptional();await refreshActivity(true);showPage(location.hash.slice(1)||'create',false);schedule();
    }catch(e){error(e.message);text('crw-readiness','The workspace could not finish loading. Keep the launcher open and refresh this page.');}
    finally{gate();}
  }
  for(const b of all('[data-nav]'))b.addEventListener('click',()=>showPage(b.dataset.nav));
  for(const b of all('[data-input-mode]'))b.addEventListener('click',()=>setInput(b.dataset.inputMode));
  for(const b of all('[data-narration-mode]'))b.addEventListener('click',()=>setNarration(b.dataset.narrationMode));
  for(const b of all('[data-minutes]'))b.addEventListener('click',()=>{$('crw-minutes').value=b.dataset.minutes;chooseGroup('[data-minutes]','minutes',b.dataset.minutes);summary();save();});
  $('crw-minutes').addEventListener('input',()=>chooseGroup('[data-minutes]','minutes',$('crw-minutes').value));
  for(const b of all('[data-project-filter]'))b.addEventListener('click',()=>{state.projectFilter=b.dataset.projectFilter;chooseGroup('[data-project-filter]','projectFilter',state.projectFilter);renderProjects();});
  for(const b of all('[data-family]'))b.addEventListener('click',()=>{state.family=b.dataset.family;chooseGroup('[data-family]','family',state.family);gallery();});
  $('crw-form').addEventListener('submit',submit);
  $('crw-form').addEventListener('input',()=>{updateOptional();summary();save();});
  $('crw-form').addEventListener('change',()=>{updateOptional();summary();save();});
  $('crw-recipe-open').addEventListener('click',openGallery);$('crw-styles-browse').addEventListener('click',openGallery);
  $('crw-gallery-search').addEventListener('input',gallery);$('crw-gallery-close').addEventListener('click',()=>{$('crw-gallery').close();$('crw-recipe-open').focus();});
  $('crw-detail-use').addEventListener('click',()=>{if(state.inspected){selectRecipe(state.inspected);$('crw-gallery').close();showPage('create');$('crw-recipe-open').focus();}});
  $('crw-output-close').addEventListener('click',()=>{$('crw-output-video').pause();$('crw-output').close();});
  $('crw-output').addEventListener('close',()=>{$('crw-output-video').pause();$('crw-output-video').removeAttribute('src');$('crw-output-video').load();});
  $('crw-output-reveal').addEventListener('click',async()=>{if(!state.currentOutput)return;try{await api(`/api/reveal/${encoded(state.currentOutput.slug)}`,{method:'POST',body:{}});text('crw-output-message','The video folder is open on this computer.');}catch(e){text('crw-output-message',e.message);}});
  $('crw-projects-refresh').addEventListener('click',()=>refreshActivity(true));
  $('crw-progress-cancel').addEventListener('click',async()=>{const b=$('crw-progress-cancel');if(!b.dataset.run||!state.openJob)return;b.disabled=true;try{await api(`/cr-video/${encoded(state.openJob)}/cancel`,{method:'POST',body:{run_id:b.dataset.run}});toast('Cancellation requested. Completed work stays saved.');await refreshActivity();}catch(e){text('crw-progress-note',e.message);}});
  $('crw-audio-recent').addEventListener('change',()=>{state.audio=$('crw-audio-recent').value;$('crw-audio-player').src=`/api/voiceover/${encoded(state.audio)}`;hide('crw-audio-player',!state.audio);summary();gate();});
  $('crw-audio-file').addEventListener('change',async()=>{const file=$('crw-audio-file').files[0];if(!file)return;state.uploading=true;gate();text('crw-audio-status','Saving your narration recording…');const form=new FormData();form.append('file',file);try{const result=await api('/api/voiceover',{method:'POST',body:form});state.audio=result.voiceover.id;$('crw-audio-player').src=`/api/voiceover/${encoded(state.audio)}`;hide('crw-audio-player',false);text('crw-audio-status',`${result.voiceover.name} · ${Math.round(result.voiceover.seconds)} seconds`);}catch(e){text('crw-audio-status',e.message);}finally{state.uploading=false;summary();gate();}});
  for(const detail of all('details'))detail.addEventListener('toggle',()=>{if(detail.open&&detail.querySelector('#crw-character'))optionalAccounts();});
  window.addEventListener('hashchange',()=>showPage(location.hash.slice(1)||'create',false));
  document.addEventListener('visibilitychange',schedule);
  if(!posterObserver)window.addEventListener('scroll',visiblePosters,{passive:true});
  window.CR_WORKSPACE_CLIENT={payload,selectRecipe,featureValues,api,state};
  start();
})();
