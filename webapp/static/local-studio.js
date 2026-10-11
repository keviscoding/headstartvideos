/* Guided owner preview. Backend enforces access independently of this page. */
window.CRLocalStudio = (() => {
    let data = null, step = 0, platform = /Win/i.test(navigator.userAgent) ? 'windows' : 'mac';
    let busy = false;
    const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const root = () => document.getElementById('page-local-studio');
    const steps = ['How it works', 'Download', 'Connect Claude', 'Connect accounts', 'Make a video'];
    const icons = {
        computer:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
        plan:'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6m-6 4h6m-6 4h4"/>',
        video:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
        arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
        check:'<path d="m5 12 4 4L19 6"/>',
        download:'<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>'
    };
    const icon = key => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">'+icons[key]+'</svg>';
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
            '<p>Choose a style. Connect your accounts. Make a video with ChannelRecipe and Claude Code, using your own computer.</p></div>' +
            '<div class="ls-flow" aria-label="How production works"><div>'+icon('plan')+'<strong>CR plans it</strong><small>Research, writing & directing</small></div>' +
            '<span>'+icon('arrow')+'</span><div>'+icon('computer')+'<strong>You make it</strong><small>Your accounts & computer</small></div></div></header>';
    }
    function body() {
        if (step === 0) return '<div class="ls-section-head"><span class="ls-step-number">01</span><div><h2>A quick tour before you begin</h2><p>Three parts work together. You only need to connect them once.</p></div></div>' +
            '<div class="ls-explain"><article>'+icon('plan')+'<h3>ChannelRecipe is your director</h3><p>It helps choose a topic, write the script and decide what appears on screen. Your CR account connects the workflow.</p><span>YOUR CR SUBSCRIPTION</span></article>' +
            '<article>'+icon('computer')+'<h3>Claude Code is your assistant</h3><p>You describe the video you want. Claude helps you set up and use the studio. You do not need to write the code yourself.</p><span>YOUR CLAUDE ACCOUNT</span></article>' +
            '<article>'+icon('video')+'<h3>Your accounts make the media</h3><p>Your voice, image and video providers create the assets. Your computer combines them into a finished video.</p><span>YOUR PROVIDER BALANCES</span></article></div>' +
            '<div class="ls-callout"><strong>Know what you’re paying for.</strong><p>CR, Claude and your media providers are separate accounts. Some styles need more paid media than others. Preview costs vary; check your provider prices and balances before making a video.</p></div>' +
            '<button class="btn-primary ls-next" data-ls-next>Show me the setup '+icon('arrow')+'</button>';
        if (step === 1) return '<div class="ls-section-head"><span class="ls-step-number">02</span><div><h2>Put your studio on your computer</h2><p>This contains the current Frontier engine connected to CR. Your videos stay on your computer.</p></div></div>' +
            '<div class="ls-platform" role="group" aria-label="Choose your computer"><button data-ls-platform="mac" class="'+(platform==='mac'?'selected':'')+'">macOS</button><button data-ls-platform="windows" class="'+(platform==='windows'?'selected':'')+'">Windows</button></div>' +
            '<div class="ls-download"><div>'+icon('download')+'<div><strong>ChannelRecipe local studio</strong><p>Engine, presets, graphics and guided setup · '+esc(data.version)+'</p></div></div>' +
            '<a class="btn-primary '+(!data.download_ready?'ls-disabled':'')+'" '+(data.download_ready?'href="/api/frontier/download"':'aria-disabled="true"')+'>Download studio</a></div>' +
            (!data.download_ready?'<p class="ls-inline-note">The private download is being prepared. You can continue through the setup guide now.</p>':'') +
            '<ol class="ls-instructions"><li><strong>Extract the whole ZIP.</strong><p>Keep the folder somewhere you can find again.</p></li><li><strong>Open '+(platform==='mac'?'Start on Mac.command':'Start on Windows.cmd')+'.</strong><p>The first launch installs its libraries and browser. Keep the window open while making videos.</p></li><li><strong>Follow the setup page that opens.</strong><p>It checks the video tools and tells you what needs attention.</p></li></ol>' +
            '<details class="ls-help"><summary>What if it does not open?</summary><p>Install <a href="https://www.python.org/downloads/" target="_blank" rel="noopener">Python 3.12</a> first. On Windows, select “Add Python to PATH”. On Mac, try right-click → Open. If a video tool is missing, open the folder in Claude Code and ask it to help with the setup checks.</p></details>' +
            '<button class="btn-primary ls-next" data-ls-next>Next: connect Claude '+icon('arrow')+'</button>';
        if (step === 2) return '<div class="ls-section-head"><span class="ls-step-number">03</span><div><h2>Give Claude access to CR</h2><p>The connection is called an MCP. It lets Claude ask ChannelRecipe for help.</p></div></div>' +
            '<div class="ls-two-col"><div><h3>Already have Claude Code?</h3><p>Copy this connection command into your terminal. Then open Claude Code, type <code>/mcp</code> and sign in to ChannelRecipe when asked.</p>' +
            '<div class="ls-copybox"><code id="ls-connect-command">claude mcp add --transport http channelrecipe https://channelrecipe.com/mcp --scope user</code><button class="btn-secondary" data-ls-copy-command>Copy command</button></div>' +
            '<p class="ls-inline-note">No API key is included in this command.</p></div><aside class="ls-callout"><h3>New to Claude Code?</h3><p>It is Claude’s app for working with files on your computer. Start with its official installation guide, then return here.</p><a href="https://code.claude.com/docs/en/quickstart" target="_blank" rel="noopener">Open Claude Code setup ↗</a><p class="ls-inline-note">Claude’s subscription has its own usage limits.</p></aside></div>' +
            '<div class="ls-callout"><strong>A first message to try</strong><p class="ls-prompt">“Use ChannelRecipe to help me set up my local video studio. Walk me through it one step at a time.”</p><button class="btn-secondary" data-ls-copy-prompt>Copy first message</button></div>' +
            '<button class="btn-primary ls-next" data-ls-next>Next: connect your accounts '+icon('arrow')+'</button>';
        if (step === 3) return '<div class="ls-section-head"><span class="ls-step-number">04</span><div><h2>Connect the accounts that do the work</h2><p>The brain key lives securely in CR. Voice, image and video keys are added on your computer.</p></div></div>' +
            '<div class="ls-key-card"><div class="ls-key-top"><div><h3>Google Gemini</h3><p>Powers writing, directing and review through your Google project.</p></div><span class="ls-status '+(data.brain.connected?'connected':'')+'">'+(data.brain.connected?'Connected':'Needs a key')+'</span></div>' +
            '<form id="ls-key-form"><label for="ls-brain-key">Your Gemini API key</label><div class="ls-key-input"><input id="ls-brain-key" class="cr-input" type="password" autocomplete="off" placeholder="'+(data.brain.connected?'Paste a new key to replace the saved one':'Paste your Gemini key')+'" required minlength="16"><button class="btn-primary" type="submit">Save & check</button></div></form>' +
            '<p class="ls-inline-note">This check does not generate a video. Your key is encrypted and never included in the download.</p><a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Create a Gemini key ↗</a>' +
            (data.brain.connected?'<button class="btn-ghost ls-remove" data-ls-remove-key>Disconnect Gemini</button>':'')+'</div>' +
            '<div class="ls-key-card"><h3>Connect your downloaded studio</h3><p>Copy your CR connect key, then paste it into the local setup page. This connects the studio to your CR account.</p><div class="ls-key-actions"><button class="btn-secondary" data-ls-copy-key>Copy CR connect key</button><a href="http://127.0.0.1:7860/cr-setup" target="_blank" rel="noopener" class="btn-secondary">Open local setup ↗</a></div><p class="ls-inline-note">Keep this key private. It is different from your Google or media keys.</p></div>' +
            '<div class="ls-callout"><strong>Add media accounts in the local setup page.</strong><p>Start with a voice and image provider. Avatar and motion styles may need additional services. You will see which keys are missing before production.</p></div>' +
            '<button class="btn-primary ls-next" data-ls-next>Choose your first video '+icon('arrow')+'</button>';
        return '<div class="ls-section-head"><span class="ls-step-number">05</span><div><h2>Choose a look for your first video</h2><p>The current library covers illustrated stories, documentaries, presenters, maps and 3D. Each has different account and hardware needs.</p></div></div>' +
            '<div class="ls-library-controls"><input class="cr-input" id="ls-style-search" type="search" placeholder="Search styles" aria-label="Search styles"><select class="cr-input" id="ls-family-filter" aria-label="Filter styles"><option value="">All styles</option><option value="illustrated">Illustrated</option><option value="documentary">Documentary & graphics</option><option value="avatar">AI presenters</option><option value="3d">3D</option></select></div>' +
            '<div id="ls-style-grid" class="ls-style-grid"></div><p class="ls-inline-note">These are imported presets for admin testing. Full production validation is still in progress.</p>' +
            '<div class="ls-launch"><div><h3>Ready when your setup checks pass</h3><p>Open your local studio, choose a style, add a title and review the options. Start with a short video to check your accounts and output.</p></div><a class="btn-primary" href="http://127.0.0.1:7860/cr-setup" target="_blank" rel="noopener">Open your studio ↗</a></div>' +
            '<details class="ls-help"><summary>If something stops, what happens to my work?</summary><p>The engine saves work in your local output folder. Reopen the same job to resume. If a provider request has an uncertain result, check its recorded status before retrying, so you do not accidentally pay twice.</p></details>' +
            '<div id="ls-history"></div>';
    }
    function render() {
        if (!root() || !data) return;
        root().innerHTML = '<div class="ls-wrap">'+hero()+'<nav class="ls-steps" aria-label="Setup steps">'+steps.map((title,i)=>
            '<button data-ls-step="'+i+'" class="'+(i===step?'active':'')+'" '+(i===step?'aria-current="step"':'')+'><span>'+String(i+1).padStart(2,'0')+'</span>'+title+'</button>').join('')+
            '</nav><main class="ls-panel"><div id="ls-notice" role="status" hidden></div>'+body()+'</main><footer class="ls-footer"><span>Admin access only · '+esc(data.version)+'</span><button class="btn-ghost" data-ls-refresh>Refresh connection status</button></footer></div>';
        root().querySelectorAll('[data-ls-step]').forEach(el => el.addEventListener('click', () => {step = Number(el.dataset.lsStep);render();}));
        root().querySelectorAll('[data-ls-next]').forEach(el => el.addEventListener('click', () => {step = Math.min(4, step+1);render();root().scrollIntoView({behavior:'smooth',block:'start'});}));
        root().querySelectorAll('[data-ls-platform]').forEach(el => el.addEventListener('click', () => {platform=el.dataset.lsPlatform;render();}));
        root().querySelector('[data-ls-refresh]')?.addEventListener('click', open);
        root().querySelector('[data-ls-copy-command]')?.addEventListener('click', () => copy(document.getElementById('ls-connect-command').textContent,'Connection command copied.'));
        root().querySelector('[data-ls-copy-prompt]')?.addEventListener('click', () => copy('Use ChannelRecipe to help me set up my local video studio. Walk me through it one step at a time.','First message copied.'));
        root().querySelector('[data-ls-copy-key]')?.addEventListener('click', async () => {
            try { const mcp = await fetch('/api/me/mcp'); if(!mcp.ok) throw new Error('Could not get your connect key.');const account=await mcp.json();await copy(account.api_key,'Connect key copied. Paste it into the local setup form.');}
            catch(e){notice(e.message,true);}
        });
        root().querySelector('#ls-key-form')?.addEventListener('submit', async event => {
            event.preventDefault(); const button=event.target.querySelector('button');button.disabled=true;button.textContent='Checking…';
            try { await api('brain-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:document.getElementById('ls-brain-key').value.trim()})}); data=await api('status');render();notice('Gemini connected. Requests use your Google project.');}
            catch(e){notice(e.message,true);}
            finally{button.disabled=false;button.textContent='Save & check';}
        });
        root().querySelector('[data-ls-remove-key]')?.addEventListener('click', async () => {
            try{await api('brain-key',{method:'DELETE'});data=await api('status');render();notice('Gemini disconnected. Add a key before starting more work.');}
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
                '<article><div><strong>'+esc(p.title)+'</strong><small>'+esc(p.style)+' · '+new Date(p.created_at*1000).toLocaleDateString()+'</small>'+(p.status==='needs_review'?'<small>A request stopped. Check your local log and provider usage before restarting.</small>':'')+'</div><span>'+Number(p.calls)+' brain requests<br>'+Number(p.input_tokens+p.output_tokens).toLocaleString()+' reported tokens</span></article>').join('')+'</div>':'<p>Your first project will appear here when you start a video in the local studio.</p>')+
                '<p class="ls-inline-note">Brain usage is recorded here. Voice, image and video charges are in your provider accounts.</p>';
        }catch(e){notice(e.message,true);}
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
        try{data=await api('status');render();}
        catch(e){root().innerHTML='<div class="ls-wrap ls-panel"><h2>Your setup could not load</h2><p>'+esc(e.message)+'</p><button class="btn-primary" id="ls-retry">Try again</button></div>';document.getElementById('ls-retry').addEventListener('click',open);}
        finally{busy=false;}
    }
    return {open};
})();
