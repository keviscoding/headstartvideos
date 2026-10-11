/* Guided owner preview. Backend enforces access independently of this page. */
window.CRLocalStudio = (() => {
    let data = null, step = 0, platform = /Win/i.test(navigator.userAgent) ? 'windows' : 'mac';
    let busy = false, brainChoice = 'anthropic';
    const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const root = () => document.getElementById('page-local-studio');
    const steps = ['How it works', 'Download', 'Choose your brain', 'Connect accounts', 'Make a video'];
    const icons = {
        computer:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
        plan:'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6m-6 4h6m-6 4h4"/>',
        video:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
        arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
        check:'<path d="m5 12 4 4L19 6"/>',
        download:'<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>'
    };
    const icon = key => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">'+icons[key]+'</svg>';
    const providerInfo = () => data?.providers?.[brainChoice] || {label:'Claude API', brain_models:[]};
    const modelName = (provider, id) => data?.providers?.[provider]?.brain_models?.find(m=>m.id===id)?.label || id || 'Not selected';
    function modelOptions(account, selected, same=false) {
        const choices=account?.brain_models||[];
        const saved=selected&&!choices.some(m=>m.id===selected)?'<option value="'+esc(selected)+'" selected>'+esc(selected)+' · saved selection</option>':'';
        return (same?'<option value="">Use the same model as writing</option>':'')+saved+choices.map(m=>'<option value="'+esc(m.id)+'" '+(m.id===selected?'selected':'')+'>'+esc(m.label||m.id)+'</option>').join('');
    }
    function updateBrainModels(general, visual) {
        const account=providerInfo();
        document.getElementById('ls-brain-model').innerHTML=modelOptions(account,general||account.default_brain);
        document.getElementById('ls-brain-visual-model').innerHTML=modelOptions(account,visual===undefined?account.default_visual_brain:visual,true);
        document.querySelector('#ls-key-form button').disabled=!account.brain_models?.length;
        updateBrainResearch();
    }
    function updateBrainResearch() {
        const model=providerInfo().brain_models?.find(m=>m.id===document.getElementById('ls-brain-model').value);
        document.getElementById('ls-brain-research').textContent=model?.research===false?'This model does not include built-in web research through this provider. Choose a model with research support when the video needs it.':'Research tools vary by provider. Changing media accounts leaves this brain choice in place.';
    }
    function brainForm() {
        const account=providerInfo(), saved=data.brain||{};
        const general=saved.provider===brainChoice?saved.model:account.default_brain;
        const visual=saved.provider===brainChoice?(saved.visual_model||saved.model):account.default_visual_brain;
        return '<div class="ls-key-card"><div class="ls-key-top"><div><h3>Choose your API brain</h3><p>The brain writes and plans. Your media accounts generate the voice, images and video separately.</p></div><span class="ls-status '+(saved.connected?'connected':'')+'">'+(saved.connected?esc(data.providers?.[saved.provider]?.label||saved.provider)+' connected':'Needs a key')+'</span></div>'+
            (saved.connected?'<p class="ls-inline-note">Saved API account: '+esc(data.providers?.[saved.provider]?.label||saved.provider)+' · writing: '+esc(modelName(saved.provider,saved.model))+' · complex visuals: '+esc(modelName(saved.provider,saved.visual_model||saved.model))+'.</p>':'')+
            '<p class="ls-inline-note">Choose the models used by your CR brain. Its usage is billed to your selected API account; the video workflow comes from ChannelRecipe.</p>'+
            '<form id="ls-key-form"><label for="ls-brain-provider">Brain API account</label><select id="ls-brain-provider" class="cr-input">'+(!data.providers?.anthropic?'<option value="anthropic" selected disabled>Claude API — waiting for model choices</option>':'')+Object.entries(data.providers||{}).map(([id,p])=>'<option value="'+esc(id)+'" '+(id===brainChoice?'selected':'')+'>'+esc(p.label)+'</option>').join('')+'</select>'+
            '<label for="ls-brain-model">Writing, planning and research model</label><select id="ls-brain-model" class="cr-input" required>'+modelOptions(account,general)+'</select>'+
            '<label for="ls-brain-visual-model">Complex visuals and render review model</label><select id="ls-brain-visual-model" class="cr-input">'+modelOptions(account,visual,true)+'</select><p class="ls-inline-note">This model handles demanding visual work, including 3D scenes. Choosing the same model keeps one model for both roles.</p>'+
            '<label for="ls-brain-key" id="ls-brain-label">Your '+esc(account.label)+' API key</label><div class="ls-key-input"><input id="ls-brain-key" class="cr-input" type="password" autocomplete="off" placeholder="Paste the complete key" required minlength="16"><button class="btn-primary" type="submit">Save & check</button></div></form>'+
            '<p id="ls-brain-research" class="ls-inline-note">Research tools vary by provider. Changing media accounts leaves this brain choice in place.</p><p class="ls-inline-note">Your key is encrypted in CR. These calls use your own API balance and do not spend CR generation credits. Accounts change only when you save your choice.</p>'+(account.key_url?'<a id="ls-key-link" href="'+esc(account.key_url)+'" target="_blank" rel="noopener">Create an account key ↗</a>':'<a id="ls-key-link" hidden>Create an account key ↗</a>')+
            (saved.connected?'<button class="btn-ghost ls-remove" data-ls-remove-key>Disconnect API brain account</button>':'')+'</div>';
    }
    function notice(text, error = false) {
        const el = document.getElementById('ls-notice');
        if (el) { el.textContent = text; el.classList.toggle('ls-error', error); el.hidden = !text; }
    }
    async function api(path, options) {
        const response = await fetch('/api/frontier/' + path, options);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'This step could not finish. Please try again.');
        return body;
    }
    function hero() {
        return '<header class="ls-header"><div><div class="ls-eyebrow">LOCAL STUDIO <span>ADMIN PREVIEW</span></div>' +
            '<h1>Your computer.<br>Your next <em>video.</em></h1>' +
            '<p>Connect Claude Code and your media accounts. The complete Frontier workflow runs on your computer with CR’s maintained production tools.</p></div>' +
            '<div class="ls-flow" aria-label="How production works"><div>'+icon('plan')+'<strong>Your brain plans it</strong><small>Research, writing & directing</small></div>' +
            '<span>'+icon('arrow')+'</span><div>'+icon('computer')+'<strong>You make it</strong><small>Your accounts & computer</small></div></div></header>';
    }
    function body() {
        if(step===0)return '<div class="ls-section-head"><span class="ls-step-number">01</span><div><h2>Frontier’s complete workflow, connected to CR</h2><p>Claude Code runs the creative stages. Your computer makes the media and assembles the video. CR supplies the maintained workflow and project controls.</p></div></div>'+
            '<div class="ls-explain"><article>'+icon('plan')+'<h3>Your Claude does the thinking</h3><p>Writing, web research, direction, visual code and render review use your signed-in Claude Code account.</p><span>YOUR CLAUDE ACCOUNT</span></article><article>'+icon('computer')+'<h3>CR keeps the workflow current</h3><p>The original presets, production process and MCP tools stay connected to your CR account. Projects pin their workflow and model choices.</p><span>YOUR CR ACCOUNT</span></article><article>'+icon('video')+'<h3>Your computer creates the result</h3><p>Media services create assets; local browsers, graphics tools and FFmpeg render and assemble them. Review the timeline and export locally.</p><span>YOUR MEDIA ACCOUNTS</span></article></div>'+
            '<div class="ls-callout"><strong>Separate accounts, clear spending.</strong><p>Claude Code uses your Claude account and its limits. Kie, Atlas and other media services use their own balances. These are separate from your CR subscription.</p></div><button class="btn-primary ls-next" data-ls-next>Set up my studio '+icon('arrow')+'</button>';
        if(step===1)return '<div class="ls-section-head"><span class="ls-step-number">02</span><div><h2>Install the complete studio</h2><p>Keep the extracted folder together. Your videos stay on this computer.</p></div></div>'+
            '<div class="ls-platform" role="group" aria-label="Choose your computer"><button data-ls-platform="mac" class="'+(platform==='mac'?'selected':'')+'">macOS</button><button data-ls-platform="windows" class="'+(platform==='windows'?'selected':'')+'">Windows</button></div>'+
            '<div class="ls-download"><div>'+icon('download')+'<div><strong>ChannelRecipe local studio</strong><p>Original engine, presets, graphics, 3D, editor and guided setup · '+esc(data.version)+'</p></div></div><a class="btn-primary '+(!data.download_ready?'ls-disabled':'')+'" '+(data.download_ready?'href="/api/frontier/download"':'aria-disabled="true"')+'>Download studio</a></div>'+
            '<ol class="ls-instructions"><li><strong>Extract the whole ZIP.</strong><p>Keep it somewhere you can find again.</p></li><li><strong>Open '+(platform==='mac'?'Start on Mac.command':'Start on Windows.cmd')+'.</strong><p>First launch installs its libraries and graphics browser. Keep the launcher open.</p></li><li><strong>Follow the setup page that opens.</strong><p>It shows each required connection and tool. Generation stays blocked until the required checks pass.</p></li></ol>'+
            '<details class="ls-help"><summary>If the launcher does not open</summary><p>Install <a href="https://www.python.org/downloads/" target="_blank" rel="noopener">Python 3.12</a>. On Windows select “Add Python to PATH”. On Mac right-click the launcher and choose Open. Missing FFmpeg or Node tools are explained in local setup.</p></details><button class="btn-primary ls-next" data-ls-next>Connect Claude Code '+icon('arrow')+'</button>';
        if(step===2)return '<div class="ls-section-head"><span class="ls-step-number">03</span><div><h2>Use your Claude Code as the brain</h2><p>Follow the preferred route in local setup. It preserves Frontier’s separate writing, research, vision and specialist visual stages.</p></div></div>'+
            '<div class="ls-key-card"><h3>Connect CR in the local setup screen first</h3><p>Copy this key into step 2 of local setup. Review account costs there, then continue with Claude Code.</p><button class="btn-secondary" data-ls-copy-key>Copy CR connect key</button></div>'+
            '<ol class="ls-instructions"><li><strong>Install Claude Code and sign in.</strong><p>Use the <a href="https://code.claude.com/docs/en/setup" target="_blank" rel="noopener">official installation guide</a>, then run Claude Code and follow its browser login. Your Claude password is never entered into CR.</p></li><li><strong>Connect CR tools in the setup screen.</strong><p>The studio saves a private MCP connection for this folder. If you open Claude Code here, approve the project connection when prompted.</p></li><li><strong>Save and verify your models.</strong><p>Choose Sonnet 5.5 for general work and Opus 5.5 for demanding visuals, or choose either available model for each role. Verification makes two tiny text checks using your Claude account.</p></li></ol>'+
            '<div class="ls-callout"><strong>Finish these steps on your computer.</strong><p>The local screen verifies your login, model access and production MCP tools. Starting the server alone does not sign you into Claude.</p><a class="btn-secondary" href="http://127.0.0.1:7860/cr-setup" target="_blank" rel="noopener">Open local setup ↗</a></div><button class="btn-primary ls-next" data-ls-next>Connect media accounts '+icon('arrow')+'</button>';
        if(step===3)return '<div class="ls-section-head"><span class="ls-step-number">04</span><div><h2>Connect CR and your media accounts</h2><p>Keys belong in the local setup forms. Keep them out of chat.</p></div></div>'+
            '<div class="ls-key-card"><h3>Connect this computer to your CR account</h3><p>Copy your CR connect key and paste it into step 2 of local setup. This is separate from Claude and media keys.</p><div class="ls-key-actions"><button class="btn-secondary" data-ls-copy-key>Copy CR connect key</button><a href="http://127.0.0.1:7860/cr-setup" target="_blank" rel="noopener" class="btn-secondary">Open local setup ↗</a></div></div>'+
            '<div class="ls-callout"><strong>One media key can cover several tasks.</strong><p>Kie.ai or Atlas Cloud can supply narration, images, image animation and supported Kling presenters. Saving media accounts leaves your Claude Code brain in place. Pexels footage, licensed music and specialised services may need their own accounts; your selected preset checks them before generation.</p></div>'+
            '<details class="ls-help"><summary>Alternative: use a separately billed API brain</summary><p>This explicitly replaces the local Claude Code brain with your API account. Provider tool support differs; use Claude Code for the full original research and visual workflow.</p>'+brainForm()+'</details><button class="btn-primary ls-next" data-ls-next>Choose and validate a preset '+icon('arrow')+'</button>';
        return '<div class="ls-section-head"><span class="ls-step-number">05</span><div><h2>Create, review and export</h2><p>Select a preset in local setup and check its requirements. Then choose your title, duration and options in the studio.</p></div></div><div id="ls-history"></div>'+
            '<div class="ls-library-controls"><input class="cr-input" id="ls-style-search" type="search" placeholder="Search styles" aria-label="Search styles"><select class="cr-input" id="ls-family-filter" aria-label="Filter styles"><option value="">All styles</option><option value="illustrated">Illustrated</option><option value="documentary">Documentary & graphics</option><option value="avatar">AI presenters</option><option value="3d">3D</option></select></div><div id="ls-style-grid" class="ls-style-grid"></div>'+
            '<div class="ls-launch"><div><h3>Generation checks the actual selected options</h3><p>Required accounts and tools must pass. Missing footage or animation access cannot silently turn your video into an image-only substitute. The original preset still decides which media types belong in the edit.</p></div><a class="btn-primary" href="http://127.0.0.1:7860/cr-setup" target="_blank" rel="noopener">Open your studio ↗</a></div>'+
            '<details class="ls-help"><summary>Review and recovery</summary><p>Use Your videos and the timeline editor to inspect the final result. Videos, thumbnails and export files stay in output/. Completed work resumes from cache. Review stopped Claude stages in local setup before approving any additional attempt.</p></details>';
    }
    function render() {
        if (!root() || !data) return;
        root().innerHTML = '<div class="ls-wrap">'+hero()+'<nav class="ls-steps" aria-label="Setup steps">'+steps.map((title,i)=>
            '<button data-ls-step="'+i+'" class="'+(i===step?'active':'')+'" '+(i===step?'aria-current="step"':'')+'><span>'+String(i+1).padStart(2,'0')+'</span>'+title+'</button>').join('')+
            '</nav>'+(data.recovery_requests&&step!==4?'<aside class="ls-callout ls-recovery-notice"><div><strong>A model request needs attention</strong><p>You can review its status and recovery option here.</p></div><button class="btn-secondary" data-ls-review>Review stopped request</button></aside>':'')+'<main class="ls-panel"><div id="ls-notice" role="status" hidden></div>'+body()+'</main><footer class="ls-footer"><span>Admin access only · '+esc(data.version)+'</span><button class="btn-ghost" data-ls-refresh>Refresh connection status</button></footer></div>';
        root().querySelectorAll('[data-ls-step]').forEach(el => el.addEventListener('click', () => {step = Number(el.dataset.lsStep);render();}));
        root().querySelectorAll('[data-ls-next]').forEach(el => el.addEventListener('click', () => {step = Math.min(4, step+1);render();root().scrollIntoView({behavior:'smooth',block:'start'});}));
        root().querySelectorAll('[data-ls-platform]').forEach(el => el.addEventListener('click', () => {platform=el.dataset.lsPlatform;render();}));
        root().querySelector('[data-ls-refresh]')?.addEventListener('click', open);
        root().querySelector('[data-ls-review]')?.addEventListener('click',()=>{step=4;render();document.getElementById('ls-history').scrollIntoView({behavior:'smooth',block:'start'});});
        root().querySelector('[data-ls-copy-command]')?.addEventListener('click', () => copy(document.getElementById('ls-connect-command').textContent,'Connection command copied.'));
        root().querySelector('[data-ls-copy-prompt]')?.addEventListener('click', () => copy('Use ChannelRecipe to help me set up my local video studio. Walk me through it one step at a time.','First message copied.'));
        root().querySelector('[data-ls-copy-key]')?.addEventListener('click', async () => {
            try { const mcp = await fetch('/api/me/mcp'); if(!mcp.ok) throw new Error('Could not get your connect key.');const account=await mcp.json();await copy(account.api_key,'Connect key copied. Paste it into the local setup form.');}
            catch(e){notice(e.message,true);}
        });
        root().querySelector('#ls-key-form')?.addEventListener('submit', async event => {
            event.preventDefault(); const button=event.target.querySelector('button');button.disabled=true;button.textContent='Checking…';
            try { const result=await api('brain-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:document.getElementById('ls-brain-key').value.trim(),provider:brainChoice,model:document.getElementById('ls-brain-model').value,visual_model:document.getElementById('ls-brain-visual-model').value||null})}); data=await api('status');brainChoice=data.brain?.provider||brainChoice;render();notice(result.message||'Brain account and models saved.');}
            catch(e){notice(e.message,true);}
            finally{button.disabled=false;button.textContent='Save & check';}
        });
        root().querySelector('#ls-brain-provider')?.addEventListener('change', event => {
            brainChoice=event.target.value;document.getElementById('ls-brain-key').value='';
            document.getElementById('ls-brain-label').textContent='Your '+providerInfo().label+' API key';
            document.getElementById('ls-key-link').href=providerInfo().key_url;
            document.getElementById('ls-key-link').hidden=!providerInfo().key_url;
            updateBrainModels();
        });
        root().querySelector('#ls-brain-model')?.addEventListener('change',updateBrainResearch);
        if(root().querySelector('#ls-key-form')){root().querySelector('#ls-key-form button').disabled=!providerInfo().brain_models?.length;updateBrainResearch();}
        root().querySelector('[data-ls-remove-key]')?.addEventListener('click', async () => {
            try{await api('brain-key',{method:'DELETE'});data=await api('status');render();notice('Brain account disconnected. Add a key before starting more work.');}
            catch(e){notice(e.message,true);}
        });
        if(step===4){styles();document.getElementById('ls-style-search').addEventListener('input',styles);document.getElementById('ls-family-filter').addEventListener('change',styles);history();}
    }
    function styles() {
        const query=document.getElementById('ls-style-search').value.toLowerCase();
        const family=document.getElementById('ls-family-filter').value;
        const items=data.styles.filter(s=>(!family||s.family===family)&&(s.label+' '+s.description+' '+s.name).toLowerCase().includes(query));
        document.getElementById('ls-style-grid').innerHTML = items.length ? items.map(s =>
            '<article class="ls-style-card"><div class="ls-style-art '+esc(s.family)+'" aria-hidden="true"><span></span><span></span><span></span></div><div><span class="ls-family">'+esc(s.family==='3d'?'3D':s.family==='avatar'?'AI PRESENTER':s.family.toUpperCase())+'</span><h3>'+esc(s.label||s.name)+'</h3><p>'+esc(s.description||s.tagline)+'</p></div></article>').join('') :
            '<p class="ls-empty">No styles match. Try another search or choose all styles.</p>';
    }
    async function history() {
        try {
            const result=await api('projects');
            const el=document.getElementById('ls-history');if(!el)return;
            el.innerHTML='<h3>Your recent studio projects</h3>'+(result.projects.length?'<div class="ls-projects">'+result.projects.map(p=>
                '<div class="ls-project"><article><div><strong>'+esc(p.title)+'</strong><small>'+esc(p.style)+' · '+new Date(p.created_at*1000).toLocaleDateString()+'</small>'+(p.status==='needs_review'?'<small>A model request stopped. Open its status for the next step.</small>':'')+'<button class="btn-secondary ls-view-requests" data-ls-requests="'+esc(p.id)+'" aria-expanded="false">View request status</button></div><span>'+Number(p.calls)+' brain requests<br>'+Number(p.input_tokens+p.output_tokens).toLocaleString()+' reported tokens</span></article><div id="ls-requests-'+esc(p.id)+'" class="ls-request-list" hidden></div></div>').join('')+'</div>':'<p>Your first project will appear here when you start a video in the local studio.</p>')+
                '<p class="ls-inline-note">Brain usage is recorded here. Voice, image and video charges are in your provider accounts.</p>';
            el.querySelectorAll('[data-ls-requests]').forEach(button=>button.addEventListener('click',()=>requests(button.dataset.lsRequests,button)));
        }catch(e){notice(e.message,true);}
    }
    async function requests(projectId, button) {
        const el=document.getElementById('ls-requests-'+projectId);if(!el)return;
        if(!el.hidden){el.hidden=true;button.setAttribute('aria-expanded','false');return;}
        el.hidden=false;button.setAttribute('aria-expanded','true');el.innerHTML='<p role="status">Loading request status…</p>';
        try {
            const result=await api('projects/'+projectId+'/requests');
            const labels={complete:'Answer saved',needs_review:'Request stopped',retry_allowed:'Ready to resume',running:'Still running'};
            el.innerHTML=result.requests.length?result.requests.map(r=>{
                const error=r.errors[r.errors.length-1];
                const account=r.context.provider?data.providers[r.context.provider]?.label:null;
                const message=r.status==='complete'?'Resume the same video to reuse this saved answer.':r.status==='retry_allowed'?'One retry is allowed. Resume the same video in your local studio.':r.status==='running'?'The request is still running or awaiting a saved result. Refresh its status before continuing.':error?.message||'This request stopped before a complete result was saved. The earlier preview did not retain its detailed error.';
                return '<section class="ls-request"><div class="ls-request-heading"><strong>'+esc(labels[r.status]||r.status)+'</strong><span>'+Number(r.attempts)+' '+(r.attempts===1?'attempt':'attempts')+'</span></div>'+
                    '<p>'+esc(message)+'</p><small>'+new Date(r.created_at*1000).toLocaleString()+(account?' · '+esc(account):'')+'</small>'+
                    (error?.usage_reported?'<small>'+Number(error.input_tokens+error.output_tokens).toLocaleString()+' tokens reported for the stopped attempt. Provider billing may include other charges.</small>':r.status==='needs_review'?'<small>The stopped attempt’s charge is unknown. Check your provider’s usage if needed.</small>':'')+
                    (r.can_retry?'<form data-ls-retry-form data-project="'+esc(projectId)+'" data-request="'+esc(r.id)+'" data-attempt="'+Number(r.attempts)+'"><p>A retry repeats this one model request using your connected '+esc(data.providers[data.brain.provider].label)+' account. Your provider may charge again. Your saved work stays available.</p><label class="ls-retry-consent"><input type="checkbox" required> I understand my provider may charge for this retry.</label><button class="btn-primary" type="submit" disabled>Allow one retry</button></form>':r.status==='needs_review'?'<p class="ls-inline-note">Another retry is unavailable. The recovery attempt or project request limit has been used. This status is available here for support.</p>':'')+
                    '<details class="ls-request-debug"><summary>Details for support</summary><small class="ls-request-id">Request '+esc(r.id)+'</small></details></section>';
            }).join(''):'<p>No model requests have been submitted for this project.</p>';
            el.querySelectorAll('[data-ls-retry-form]').forEach(form=>{
                const consent=form.querySelector('input'), submit=form.querySelector('button');
                consent.addEventListener('change',()=>submit.disabled=!consent.checked);
                form.addEventListener('submit',async event=>{
                    event.preventDefault();if(!consent.checked)return;submit.disabled=true;submit.textContent='Allowing retry…';
                    try {
                        const approved=await api('projects/'+form.dataset.project+'/requests/'+form.dataset.request+'/retry',{
                            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_attempt:Number(form.dataset.attempt),acknowledge_costs:true})});
                        notice(approved.message);el.hidden=true;await requests(projectId,button);
                    }catch(e){notice(e.message,true);submit.disabled=!consent.checked;submit.textContent='Allow one retry';}
                });
            });
        }catch(e){el.textContent=e.message;}
    }
    async function copy(text, message) {
        if(!text)throw new Error('The value is not available yet. Refresh your connection.');
        try{await navigator.clipboard.writeText(text);notice(message);}
        catch{notice('Your browser could not copy this. Select the text and copy it manually.',true);}
    }
    async function open() {
        if (!(typeof currentUser !== 'undefined' && currentUser?.is_admin)) return;
        if(busy)return;busy=true;
        if(!data)root().innerHTML='<div class="ls-wrap"><p role="status">Preparing your studio setup…</p></div>';
        try{data=await api('status');brainChoice=data.brain?.connected?data.brain.provider:'anthropic';render();}
        catch(e){root().innerHTML='<div class="ls-wrap ls-panel"><h2>Your setup could not load</h2><p>'+esc(e.message)+'</p><button class="btn-primary" id="ls-retry">Try again</button></div>';document.getElementById('ls-retry').addEventListener('click',open);}
        finally{busy=false;}
    }
    return {open};
})();
