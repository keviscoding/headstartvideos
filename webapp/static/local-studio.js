/* ChannelRecipe Studio (v2) page: explains Studio, sells it, and gets subscribers set up.
 * The server enforces plan access independently of anything rendered here. */
window.CRLocalStudio = (() => {
    const DISCORD_URL = '/community/discord';
    const SETUP_URL = 'http://127.0.0.1:7860/cr-setup';
    const STUDIO_URL = 'http://127.0.0.1:7860/';
    const CLAUDE_PROMPT = 'Help me install ChannelRecipe Studio on this computer. Check what is missing, '
        + 'install it for me one step at a time, then start the studio and open its setup page.';
    let data = null, platform = /Win/i.test(navigator.userAgent) ? 'windows' : 'mac';
    let busy = false, brainChoice = 'anthropic', view = 'guest';

    const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const root = () => document.getElementById('page-local-studio');
    const icons = {
        idea:'<path d="M9 18h6m-5 3h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z"/>',
        film:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
        style:'<circle cx="8" cy="8" r="3"/><circle cx="16" cy="16" r="3"/><path d="M10.5 10.5l3 3M16 3v5m5 0h-5M3 16h5m0 5v-5"/>',
        cube:'<path d="M12 3 4 7.5v9L12 21l8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>',
        edit:'<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m14 6 4 4"/>',
        computer:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
        arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
        check:'<path d="m5 12 4 4L19 6"/>',
        download:'<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
        copy:'<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
        chat:'<path d="M4 5h16v11H8l-4 4z"/>',
        key:'<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9m-4 4 3 3"/>',
        external:'<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
        spark:'<path d="M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5m7 7L18 18M6 18l2.5-2.5m7-7L18 6"/>',
        play:'<path d="M8 5.5v13a.5.5 0 0 0 .77.42l10-6.5a.5.5 0 0 0 0-.84l-10-6.5A.5.5 0 0 0 8 5.5z" fill="currentColor" stroke="none"/>'
    };
    const icon = key => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[key]+'</svg>';
    const providerInfo = () => data?.providers?.[brainChoice] || {label:'Claude API', brain_models:[]};
    const modelName = (provider, id) => data?.providers?.[provider]?.brain_models?.find(m=>m.id===id)?.label || id || 'Not selected';
    const user = () => (typeof currentUser !== 'undefined' ? currentUser : null);
    const cycle = () => (typeof _pricingBillingCycle !== 'undefined' && _pricingBillingCycle === 'annual') ? 'annual' : 'monthly';

    function notice(text, error = false) {
        const el = document.getElementById('ls-notice');
        if (el) { el.textContent = text; el.classList.toggle('ls-error', error); el.hidden = !text; }
    }
    async function api(path, options) {
        const response = await fetch('/api/studio/' + path, options);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(typeof body.detail === 'string' ? body.detail : 'This step could not finish. Please try again.');
            error.status = response.status;
            throw error;
        }
        return body;
    }
    async function copy(text, message) {
        if (!text) throw new Error('That isn’t available yet. Refresh the page and try again.');
        try { await navigator.clipboard.writeText(text); notice(message); }
        catch { notice('Your browser blocked copying. Select the text and copy it yourself.', true); }
    }
    function track(event, props = {}) { try { _track(event, props); } catch (_) {} }
    const studioName = version => { const m = /(\d+)$/.exec(String(version || '')); return m ? 'Studio '+m[1] : String(version || ''); };

    // ---- Shared sections -------------------------------------------------
    // A returning customer has connected Studio, been seen by it, or made a project: they get their home, not the
    // install guide. Everyone else gets the three steps to a first video, ticked off as they go.
    const progress = () => data?.milestones || {};
    const returning = () => { const m = progress(); return !!(m.connected_at || m.seen_at || m.projects); };
    function hero() {
        const member = view === 'member';
        if (member && returning()) {
            const latest = (data.changelog || [])[0];
            return '<header class="ls-hero ls-hero-home"><div class="ls-hero-copy">'+
                '<span class="ls-version-chip">'+icon('film')+'ChannelRecipe Studio'+(latest?' <b>'+esc(latest.build)+'</b>':'')+'</span>'+
                '<h1>Welcome back. <em>What are we making?</em></h1>'+
                '<p>Studio runs on your computer. Open it to make a video, clone a style or pick up a project.</p>'+
                '<div class="ls-hero-actions"><a class="btn-primary" href="'+STUDIO_URL+'" target="_blank" rel="noopener" data-ls-open-studio>Open Studio '+icon('external')+'</a>'+
                '<a class="ls-text-link" href="#ls-whats-new">What’s new</a></div>'+
                '<p class="ls-hero-hint">Not opening? Start Studio first: double-click <b>'+(platform==='mac'?'Start on Mac.command':'Start on Windows.cmd')+'</b> in your Studio folder.</p>'+
                '</div>'+checklist()+'</header>';
        }
        return '<header class="ls-hero"><div class="ls-hero-copy">'+
            '<span class="ls-version-chip">'+icon('film')+'ChannelRecipe Studio <b>v2</b></span>'+
            '<h1>'+(member?'Your studio is ready to <em>install.</em>':'Whole faceless videos, made on <em>your computer.</em>')+'</h1>'+
            '<p>'+(member
                ? 'Three steps and you’re making your first video. Claude can do the installing for you.'
                : 'Type a title or paste a script. Studio researches, writes, narrates, animates and edits a finished YouTube video in the style you choose, or in one it learns from any channel.')+'</p>'+
            (member?'':'<div class="ls-hero-actions"><button class="btn-primary" data-ls-plans>See plans '+icon('arrow')+'</button><a class="ls-text-link" href="#ls-how">How it works</a></div>')+
            '</div>'+(member?checklist():heroVisual())+'</header>';
    }
    function heroVisual() {
        const stages = [['idea','Your idea','“Why the Romans never invented the steam engine”'],
            ['film','Studio makes it','Research · script · voice · footage · motion graphics · 3D'],
            ['edit','You publish','Finished 1080p video, thumbnail, captions and description']];
        return '<div class="ls-hero-visual" aria-hidden="true">'+stages.map(([key,title,detail],i)=>
            '<div class="ls-stage"><span class="ls-stage-icon">'+icon(key)+'</span><div><strong>'+title+'</strong><small>'+detail+'</small></div>'+
            (i<2?'<span class="ls-stage-line"></span>':'')+'</div>').join('')+'</div>';
    }
    function versionNote() {
        return '<aside class="ls-version-note"><strong>Studio is the current version of ChannelRecipe.</strong> '+
            'The Classic recipes (v1) still work, but Studio makes better videos with far more control. '+
            '<button class="ls-text-link" data-ls-classic>Open Classic recipes</button></aside>';
    }
    function features() {
        const list = [
            ['film','Any faceless format','Documentaries, explainers, history, true crime, sports, essays and more. Dozens of built-in styles.'],
            ['style','Clone a channel’s style','Point it at a YouTube channel you admire. Studio studies the real frames and builds a reusable style.'],
            ['cube','Motion graphics and 3D','Maps, charts, archival footage, animated scenes and fast 3D graphics. Not just slideshows.'],
            ['edit','Edit before you publish','Review every scene on a timeline, fix what you don’t like, then export.'],
            ['chat','Runs from Claude Code','Open Claude Code in your Studio folder and just ask. It finds your niche, picks the style with you, shows the cost first and makes the videos.']];
        return '<section class="ls-section" id="ls-how"><h2>What Studio does</h2><div class="ls-features">'+list.map(([key,title,text])=>
            '<article>'+icon(key)+'<h3>'+title+'</h3><p>'+text+'</p></article>').join('')+'</div>'+
            '<div class="ls-how"><div><span>1</span><p><strong>Download Studio</strong>Mac or Windows. Claude Code can install it for you.</p></div>'+
            '<div><span>2</span><p><strong>Connect your accounts</strong>Your Claude account does the thinking; Kie.ai or Atlas Cloud makes voice and images.</p></div>'+
            '<div><span>3</span><p><strong>Make videos</strong>Pick a style and type a title in Studio, or just ask Claude Code.</p></div></div></section>';
    }
    function planCards() {
        const catalog = typeof PLAN_CATALOG !== 'undefined' ? PLAN_CATALOG : [];
        const LOCAL = 'Videos render on <b>your own computer</b>. No credits needed';
        const details = {
            daily: {tag:'Start making videos', items:['Studio + every built-in style',LOCAL,'Clone <b>3</b> channel styles','35 cloud credits / month for Classic recipes','Niche Finder + niche database in Claude']},
            studio_pro: {tag:'Run several channels', items:['Everything in Creator',LOCAL,'Clone <b>15</b> channel styles','60 cloud credits / month for Classic recipes','3 Classic cloud videos at once']},
            studio_max: {tag:'For serious operators', items:['Everything in Pro',LOCAL,'<b>Unlimited</b> style clones','100 cloud credits / month for Classic recipes','Priority help in Discord']}
        };
        const u = user(), annual = cycle() === 'annual';
        const onTrial = typeof isTrialUser === 'function' && isTrialUser();
        const subscribed = typeof isPaidUser === 'function' && isPaidUser() && !onTrial;
        const current = typeof planTier === 'function' ? planTier(u?.plan) : '';
        const trials = typeof trialsEnabled === 'function' && trialsEnabled();
        const usedTrial = !trials || (!!(u && u.trial_used) && !onTrial);
        return '<section class="ls-section" id="ls-plans"><div class="ls-plans-head"><div><h2>Plans</h2><p>Every plan includes Studio. Cancel anytime.</p></div>'+
            '<div class="ls-cycle" role="group" aria-label="Billing period"><button data-ls-cycle="monthly" class="'+(annual?'':'selected')+'">Monthly</button><button data-ls-cycle="annual" class="'+(annual?'selected':'')+'">Yearly <small>2 months free</small></button></div></div>'+
            '<div class="ls-plans">'+catalog.map(plan=>{
                const d = details[plan.tier] || {tag:'', items:[]};
                const featured = plan.tier === 'studio_pro';
                const price = annual ? plan.annual.perMonth : plan.monthly.charge;
                let cta = usedTrial ? 'Get '+plan.label : 'Start free trial';
                const open = typeof planAvailable !== 'function' || planAvailable(plan.tier, annual ? 'annual' : 'monthly');
                if (!open) cta = 'Coming soon';
                else if (onTrial) cta = 'Start '+plan.label+': '+(annual?plan.annual.charge:plan.monthly.charge)+' today';
                else if (subscribed) cta = plan.tier === current ? 'Your plan' : 'Switch to '+plan.label;
                return '<article class="ls-plan'+(featured?' featured':'')+'">'+(featured?'<span class="ls-plan-badge">Most popular</span>':'')+
                    '<h3>'+esc(plan.label)+'</h3><p class="ls-plan-tag">'+d.tag+'</p>'+
                    '<p class="ls-plan-price"><b>'+esc(price)+'</b>/mo</p><p class="ls-plan-bill">'+(annual?'Billed '+esc(plan.annual.charge)+' yearly':'Billed monthly · cancel anytime')+'</p>'+
                    '<ul>'+d.items.map(item=>'<li>'+icon('check')+'<span>'+item+'</span></li>').join('')+'</ul>'+
                    '<button class="'+(featured?'btn-primary':'btn-secondary')+'" data-ls-buy="'+esc(plan.tier)+'" '+((subscribed&&plan.tier===current)||!open?'disabled':'')+'>'+esc(cta)+'</button></article>';
            }).join('')+'</div>'+
            '<p class="ls-fineprint">Studio runs on your computer. Writing and planning use your own Claude account (Claude Pro or Max); voice, images and animation use your Kie.ai or Atlas Cloud balance. Cloud credits are for Classic (v1) recipes.</p></section>';
    }
    function faq() {
        const items = [
            ['What do I need?','A Mac or Windows computer, a Claude account (Pro or Max) and a media account from Kie.ai or Atlas Cloud. Studio checks everything during setup and tells you what’s missing.'],
            ['What does a video cost to make?','It depends on length and style. You pay Claude and your media provider directly at their own prices. We don’t add a markup.'],
            ['Is this different from the recipes on ChannelRecipe?','Yes. Classic recipes are ChannelRecipe v1 and run in our cloud. Studio is v2: far more styles, style cloning, motion graphics, 3D and a real editor.'],
            ['Can Claude set it up for me?','Yes. Open the downloaded folder in Claude Code and paste the prompt we give you. It checks your computer, installs what’s missing and starts the studio.'],
            ['Can I make videos from Claude Code instead of the Studio page?','Yes. Open Claude Code in your Studio folder and type /make-videos, /niche-strategy, /clone-style or /digital-product, or just ask in your own words. Claude shows you the styles, tells you the cost before anything starts and asks before it spends anything.'],
            ['What if I get stuck?','Ask in our Discord. We answer there every day, and it’s where we hear what to build next.']];
        return '<section class="ls-section ls-faq"><h2>Questions</h2>'+items.map(([q,a])=>'<details><summary>'+q+'</summary><p>'+a+'</p></details>').join('')+'</section>';
    }
    // The other way to use Studio: talk to Claude Code in the Studio folder (Studio 84+: its own tools and commands).
    function claudeCodeWay() {
        const list = [
            ['film','/make-videos','Pick a style from a gallery, see the cost first, make up to 5 videos.'],
            ['idea','/niche-strategy','Channel ideas from our live niche database: a winning format bent toward a new audience, an open lane, or two channels combined.'],
            ['style','/clone-style','Clone a YouTube channel’s look, or combine two or three channels into one new style.'],
            ['key','/digital-product','Product ideas for your niche, then the book, its landing page and checkout, so you earn beyond AdSense.'],
            ['check','/my-videos','What’s being made right now, and your finished videos.']];
        return '<section class="ls-section" id="ls-claude-code"><div class="ls-section-row"><div><h2>Or just ask Claude Code</h2>'+
            '<p class="ls-section-sub">Open Claude Code in your Studio folder (in the Claude desktop app: <b>Code</b>, then choose the folder) and type a command, or say what you want in your own words. Studio needs to be running. Claude asks you before anything that costs money.</p></div></div>'+
            '<div class="ls-features">'+list.map(([key,title,text])=>'<article>'+icon(key)+'<h3>'+title+'</h3><p>'+text+'</p></article>').join('')+'</div>'+
            '<p class="ls-fineprint">Want a panel with your styles and videos inside Claude Code? Install the ChannelRecipe plugin from a terminal: <code>claude plugin install channelrecipe --marketplace keviscoding/channelrecipe-plugins</code>, then type /studio.</p></section>';
    }
    function discordCard(compact = false) {
        return '<aside class="ls-discord'+(compact?' compact':'')+'"><span class="ls-discord-icon">'+icon('chat')+'</span><div><strong>Join the ChannelRecipe Discord</strong>'+
            '<p>Get help with setup, share the videos you make, and tell us what to build next. We read every message.</p></div>'+
            '<a class="btn-secondary" href="'+DISCORD_URL+'" target="_blank" rel="noopener" data-ls-discord data-community="discord">Join Discord ↗</a></aside>';
    }

    // ---- Member view -----------------------------------------------------
    function checklist() {
        const m = progress();
        const steps = [
            ['Download Studio', !!m.downloaded_at || !!m.connected_at || !!m.projects, 'Mac or Windows, about 250 MB'],
            ['Install and connect it', !!m.connected_at || !!m.seen_at || !!m.projects, 'Claude Code can do it for you'],
            ['Make your first video', Number(m.finished || 0) > 0, Number(m.finished || 0) > 0
                ? Number(m.finished)+' finished '+(Number(m.finished)===1?'video':'videos') : 'In Studio, or type /make-videos in Claude Code']];
        const done = steps.filter(s=>s[1]).length;
        if (done === steps.length) {
            // all set: what's worth knowing at a glance instead of three struck-through steps
            const ago = t => { if (!t) return 'not yet'; const m = Math.max(1, Math.round((Date.now()/1000 - t)/60));
                return m < 60 ? m+' min ago' : m < 1440 ? Math.round(m/60)+' h ago' : Math.round(m/1440)+' days ago'; };
            const clones = data.style_clones || {};
            const rows = [['Studio last online', ago(m.seen_at)], ['Videos finished', String(Number(m.finished || 0))],
                ['Projects', String(Number(m.projects || 0))],
                ['Style clones', clones.limit==null ? Number(clones.used||0)+' · unlimited' : Number(clones.used||0)+' of '+clones.limit]];
            return '<aside class="ls-checklist ls-statcard" aria-label="Your studio"><div class="ls-checklist-head"><strong>Your studio</strong>'+
                '<span class="ls-pill ok">Connected</span></div><dl>'+rows.map(([k,v])=>'<div><dt>'+k+'</dt><dd>'+esc(v)+'</dd></div>').join('')+'</dl></aside>';
        }
        return '<aside class="ls-checklist" aria-label="Your progress"><div class="ls-checklist-head"><strong>'+
            (done===steps.length?'You’re all set':'Your first video in 3 steps')+'</strong><span>'+done+' of '+steps.length+'</span></div>'+
            '<div class="ls-meter-bar"><span style="width:'+Math.round(done/steps.length*100)+'%"></span></div><ol>'+
            steps.map(([title,ok,note])=>'<li class="'+(ok?'done':'')+'"><span class="ls-check">'+(ok?icon('check'):'')+'</span><div><b>'+title+'</b><small>'+esc(note)+'</small></div></li>').join('')+
            '</ol>'+(view==='member'?'<a class="ls-walk-link" href="#ls-setup-video">'+icon('play')+'Stuck? Watch the 3-minute setup</a>':'')+'</aside>';
    }
    const READY = [
        ['stick-stories','Stick Figure Stories','Funny stick-figure stories on painted backgrounds, with a recurring mascot.'],
        ['clean-3d','Clean 3D Reconstruction','Real events rebuilt as clean 3D scenes in ivory and signal yellow.'],
        ['deadly-places','Deadly Places Documentary','Calm investigations of deadly places over archival-style pictures and old maps.'],
        ['handdrawn-money','Hand-Drawn Money','Money explained with whiteboard drawings that draw themselves on.'],
        ['manga-recaps','Manga Recaps','Your own manga panels, read and retold as a gripping recap.']];
    function readyMade() {
        const picked = chosenNiche()?.studio || '';
        return '<section class="ls-section"><div class="ls-section-row"><div><h2>Ready-made recipes</h2>'+
            '<p class="ls-section-sub">Learned from top channels and tested end to end. In Studio, choose <b>Create → Browse recipes</b>.</p></div></div>'+
            '<div class="ls-ready">'+READY.map(([id,name,text])=>'<article'+(id===picked?' class="ls-ready-picked"':'')+'><img src="/static/recipes/'+id+'.webp" alt="" width="640" height="360" loading="lazy" decoding="async">'+
            '<div>'+(id===picked?'<span class="ls-pill ok">Your niche</span>':'')+'<h3>'+esc(name)+'</h3><p>'+esc(text)+'</p></div></article>').join('')+'</div></section>';
    }
    // The niche picked in the money-niche quiz (/money-niche), stored on the account: Studio starts from it.
    const chosenNiche = () => user()?.money_niche || null;
    function nicheNote() {
        const n = chosenNiche(); if (!n) return '';
        const ready = READY.find(([id]) => id === n.studio);
        const how = ready
            ? 'In Studio, choose <b>Create → Browse recipes → '+esc(ready[1])+'</b>'+(view==='member'&&returning()?' (marked below).':'.')
            : (n.proof ? 'In Studio, open the <b>Style library</b> and clone the look of <a href="'+esc(n.proof_url||'#')+'" target="_blank" rel="noopener">'+esc(n.proof)+'</a>: paste three of its videos.'
                       : 'In Studio, open the <b>Style library</b> and clone a channel from your result.');
        const back = n.result && n.result.startsWith('cc_') ? '/money-niche/c/'+encodeURIComponent(n.result) : '/money-niche'+(n.result?'/r/'+encodeURIComponent(n.result):'');
        return '<aside class="ls-version-note ls-niche-pick"><strong>Your recipe: '+esc(n.name)+(n.angle?' · '+esc(n.angle.toLowerCase()):'')+'.</strong> '+
            (n.look?esc(n.look)+', ':'')+esc(n.format||'')+(n.kind==='shorts'?' · Shorts':'')+'. '+how+
            (n.title?' First video: <b>“'+esc(n.title)+'”</b>.':'')+' <a class="ls-text-link" href="'+back+'">See your result</a></aside>';
    }
    // Just shipped: the newest Studio release, front and centre for a returning customer for three weeks after it
    // ships (the owner: "every release visible and exciting", not a list at the bottom). Each change is a tile: its
    // first sentence as the headline. Hidden per release once the customer says Got it.
    const SHIPPED_DAYS = 21;
    const shippedKey = build => 'cr-shipped-seen-'+build;
    function shippedSeen(build) { try { return localStorage.getItem(shippedKey(build)) === '1'; } catch (_) { return false; } }
    function shipped() {
        const latest = (data.changelog || [])[0];
        if (!latest || !returning() || shippedSeen(latest.build)) return '';
        const when = Date.parse(latest.date || '');
        if (!isFinite(when) || Date.now() - when > SHIPPED_DAYS * 864e5) return '';
        // Every release of the last three weeks, newest first: a small fix release must not hide the big one before it.
        const recent = (data.changelog || []).filter(r => { const t = Date.parse(r.date || ''); return isFinite(t) && Date.now() - t <= SHIPPED_DAYS * 864e5; });
        const big = recent.reduce((a, r) => (r.items || []).length > (a.items || []).length ? r : a, latest);
        const tiles = recent.flatMap(r => r.items || []).map(text => {
            const m = /^(New|Improved|Fixed):\s*(.*)$/.exec(text || '');
            if (!m) return null;
            const cut = m[2].search(/[.!?](\s|$)/);
            const head = cut > 0 ? m[2].slice(0, cut) : m[2];
            const rest = cut > 0 ? m[2].slice(cut + 1).trim() : '';
            return {tag: m[1], head: head.charAt(0).toUpperCase() + head.slice(1), rest};
        }).filter(Boolean).sort((a, b) => (a.tag === 'New' ? 0 : 1) - (b.tag === 'New' ? 0 : 1)).slice(0, 4);
        if (!tiles.length) return '';
        const date = new Date(when).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
        return '<section class="ls-shipped" id="ls-shipped" aria-labelledby="ls-shipped-title">'+
            '<div class="ls-shipped-head"><span class="ls-shipped-chip"><i class="ls-shipped-pulse" aria-hidden="true"></i>Just shipped · Studio '+esc(latest.build)+' · '+esc(date)+'</span>'+
            '<button class="ls-shipped-close" type="button" data-ls-shipped-seen="'+esc(latest.build)+'">Got it</button></div>'+
            '<h2 id="ls-shipped-title">'+esc(big.title || latest.title || 'What’s new in Studio')+'</h2>'+
            '<div class="ls-shipped-grid">'+tiles.map((t, i) => '<article class="ls-shipped-tile" style="--i:'+i+'">'+
                '<span class="ls-tag ls-tag-'+t.tag.toLowerCase()+'">'+t.tag+'</span><h3>'+esc(t.head)+'</h3>'+
                (t.rest ? '<p>'+esc(t.rest)+'</p>' : '')+'</article>').join('')+'</div>'+
            '<div class="ls-shipped-actions"><a class="btn-primary" href="'+STUDIO_URL+'" target="_blank" rel="noopener" data-ls-open-studio data-ls-shipped-update>Update in Studio '+icon('external')+'</a>'+
            '<a class="ls-text-link" href="#ls-whats-new">Every change in Studio '+esc(latest.build)+'</a>'+
            '<small>Studio offers the update itself in one click, and keeps your projects and settings.</small></div></section>';
    }
    function whatsNew() {
        const rows = data.changelog || []; if (!rows.length) return '';
        const [latest, ...older] = rows;
        const item = text => { const m = /^(New|Improved|Fixed):\s*(.*)$/.exec(text || '');
            return '<li>'+(m?'<span class="ls-tag ls-tag-'+m[1].toLowerCase()+'">'+m[1]+'</span>'+esc(m[2]):esc(text))+'</li>'; };
        const items = latest.items || [];
        return '<section class="ls-section" id="ls-whats-new"><div class="ls-section-row"><div><h2>What’s new in Studio '+esc(latest.build)+'</h2>'+
            '<p class="ls-section-sub">'+esc(latest.title || '')+'. Studio offers the update itself in one click and downloads only what changed. New videos need the latest Studio; projects already running finish on yours.</p></div></div>'+
            '<div class="ls-news"><ul>'+items.slice(0,5).map(item).join('')+'</ul>'+
            (items.length>5?'<details class="ls-mini"><summary>All '+items.length+' changes</summary><ul>'+items.slice(5).map(item).join('')+'</ul></details>':'')+
            (older.length?'<details class="ls-mini"><summary>Earlier updates</summary><ul class="ls-older">'+older.slice(0,8).map(r=>'<li><b>Studio '+esc(r.build)+'</b> · '+esc(r.title||'')+'</li>').join('')+'</ul></details>':'')+
            '</div></section>';
    }
    // The owner's own three-minute walkthrough of the whole setup, with chapters, so anyone stuck on one step can jump
    // straight to it. It plays from the media CDN, only when asked.
    const WALKTHROUGH = 'https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/onboarding/studio-setup-walkthrough-v1';
    const CHAPTERS = [[0,'What you need'],[26,'Download Studio'],[31,'Open it in Claude Code'],[51,'Paste the install prompt'],
        [62,'The Mac security warning'],[81,'Connect your account'],[90,'Set up Claude'],[100,'Your Kie.ai key'],
        [119,'Make your first video'],[131,'Choose a voice'],[152,'What came out']];
    const clock = t => Math.floor(t/60)+':'+String(t%60).padStart(2,'0');
    function setupVideo() {
        return '<section class="ls-walk" id="ls-setup-video" aria-label="Watch the whole setup">'+
            '<div class="ls-walk-head"><span class="ls-walk-chip">'+icon('play')+'3 min · start to finish</span>'+
            '<h3>Watch the whole setup first</h3>'+
            '<p>Download, Claude Code, the Mac security prompt, your Kie.ai key and your first video, done for real. Stuck on one step? Jump straight to it.</p></div>'+
            '<div class="ls-walk-player"><video id="ls-walk-video" preload="none" playsinline poster="'+WALKTHROUGH+'.jpg" '+
                'aria-label="The whole ChannelRecipe Studio setup, start to finish"><source src="'+WALKTHROUGH+'.mp4" type="video/mp4"></video>'+
            '<button type="button" class="ls-walk-play" data-ls-walk-seek="0">'+icon('play')+'Watch the setup <small>3:10</small></button></div>'+
            '<ol class="ls-chapters">'+CHAPTERS.map(([t,label])=>'<li><button type="button" data-ls-walk-seek="'+t+'"><span>'+clock(t)+'</span>'+label+'</button></li>').join('')+'</ol>'+
            '</section>';
    }
    function setupSteps() {
        const launcher = platform === 'mac' ? 'Start on Mac.command' : 'Start on Windows.cmd';
        const ready = !!data.download_ready;
        return '<section class="ls-setup">'+setupVideo()+
            '<div class="ls-step"><span class="ls-step-n">1</span><div class="ls-step-body"><h3>Download Studio</h3>'+
                '<div class="ls-platform" role="group" aria-label="Your computer"><button data-ls-platform="mac" class="'+(platform==='mac'?'selected':'')+'">Mac</button><button data-ls-platform="windows" class="'+(platform==='windows'?'selected':'')+'">Windows</button></div>'+
                '<a class="btn-primary ls-download-btn '+(ready?'':'ls-disabled')+'" '+(ready?'href="/api/studio/download/file" data-ls-download':'aria-disabled="true"')+'>'+icon('download')+(ready?'Download ChannelRecipe Studio':'Preparing the download…')+'</a>'+
                '<p class="ls-step-note">About 250 MB. When it finishes, <b>extract the whole ZIP</b>'+(platform==='windows'?' (right-click → Extract All)':'')+' into a folder you’ll keep, like Documents.</p></div></div>'+
            '<div class="ls-step"><span class="ls-step-n">2</span><div class="ls-step-body"><h3>Install it</h3>'+
                '<div class="ls-install-options">'+
                '<div class="ls-option recommended"><span class="ls-option-tag">Easiest</span><h4>Let Claude Code install it</h4>'+
                    '<p>Open the extracted folder in Claude Code. In the Claude desktop app, choose <b>Code</b> and select the folder, then paste:</p>'+
                    '<div class="ls-prompt-box"><code>'+esc(CLAUDE_PROMPT)+'</code><button class="btn-ghost" data-ls-copy-prompt>'+icon('copy')+'Copy</button></div>'+
                    '<p class="ls-step-note">No Claude Code yet? <a href="https://claude.com/download" target="_blank" rel="noopener">Download the Claude app</a> and sign in with your Claude account.</p></div>'+
                '<div class="ls-option"><h4>Or install it yourself</h4>'+
                    '<p>In the extracted folder, double-click <b>'+launcher+'</b>'+(platform==='mac'?' (if macOS blocks it, right-click → Open)':'')+'. The first launch takes a few minutes. Keep that window open. Your setup page opens in the browser.</p>'+
                    '<details class="ls-mini"><summary>It says something is missing</summary><p>Studio needs Python 3.10 or newer (3.13 recommended'+(platform==='windows'?'; tick “Add Python to PATH” when installing':'')+'), Node.js LTS and FFmpeg. The setup page links each one. Easiest fix: use the Claude Code option, which installs them for you.</p></details></div>'+
                '</div></div></div>'+
            '<div class="ls-step"><span class="ls-step-n">3</span><div class="ls-step-body"><h3>Connect your account</h3>'+
                '<p>On the setup page, press <b>Connect my ChannelRecipe account</b> and approve. No key to copy.</p>'+
                '<div class="ls-step-actions"><a class="btn-ghost" href="'+SETUP_URL+'" target="_blank" rel="noopener">Open setup page ↗</a>'+
                '<button class="btn-secondary" data-ls-copy-key>'+icon('key')+'Copy my key instead</button></div>'+
                '<p class="ls-step-note">Then press <b>Set up Claude for videos</b> and paste a Kie.ai key. When all three steps show ✓ Done, open Studio and make your first video.</p></div></div>'+
            '<div class="ls-step ls-step-quiet"><span class="ls-step-n">↻</span><div class="ls-step-body"><h3>Already installed? Update in place</h3>'+
                '<p>Studio shows what’s new when an update is out and updates itself in one click, downloading only what changed. Your projects, styles and settings stay. New videos need the latest Studio, so press Update now when it asks. On Studio 51 or older, close the launcher and ask Claude Code in your Studio folder to “Update ChannelRecipe Studio” once. Don’t download a fresh copy into a new folder.</p></div></div>'+
            '</section>';
    }
    function planPanel(home = false) {
        const clones = data.style_clones || {};
        const limit = clones.limit;
        const used = Number(clones.used || 0);
        const pct = limit ? Math.min(100, Math.round(used / limit * 100)) : 0;
        const label = data.access === 'admin' ? 'Admin' : (data.plan_label || 'Your plan');
        return '<aside class="ls-plan-panel"><div class="ls-plan-panel-head"><span>Your plan</span><strong>'+esc(label)+'</strong></div>'+
            '<div class="ls-meter"><div class="ls-meter-label"><span>Style clones</span><b>'+(limit==null?used+' used · unlimited':used+' of '+limit+' used')+'</b></div>'+
            (limit==null?'':'<div class="ls-meter-bar"><span style="width:'+pct+'%"></span></div>')+'</div>'+
            (clones.upgrade_label?'<button class="btn-ghost ls-upgrade" data-ls-plans>Upgrade to '+esc(clones.upgrade_label)+' for more →</button>':'')+
            (home?'':'<div class="ls-needs"><strong>You’ll also need</strong><ul><li>A Claude account (Pro or Max)</li><li>A Kie.ai or Atlas Cloud account for voice and images</li></ul></div>'+
            '<div class="ls-open-studio"><a class="btn-secondary" href="'+STUDIO_URL+'" target="_blank" rel="noopener">Open my studio ↗</a><small>Works while Studio is running on this computer.</small></div>')+'</aside>';
    }
    function memberBody() {
        if (returning()) {
            return '<section class="ls-section ls-first"><div class="ls-section-row"><h2>Your projects</h2><button class="btn-ghost" data-ls-refresh>Refresh</button></div><div id="ls-history"><p class="ls-muted">Loading…</p></div></section>'+
                '<section class="ls-section" id="ls-niches-section" hidden><div class="ls-section-row"><div><h2 id="ls-niches-title">Fresh niches this week</h2>'+
                '<p class="ls-section-sub">Faceless channels the Niche Finder found growing right now. Pick one, then clone its look in Studio’s Style library.</p></div>'+
                '<button class="btn-ghost" data-ls-niche-finder>Open Niche Finder</button></div><div id="ls-niches" class="ls-niches"></div></section>'+
                claudeCodeWay()+whatsNew()+readyMade()+discordCard()+
                '<details class="ls-advanced ls-reinstall"><summary>Install Studio on another computer</summary>'+setupSteps()+'</details>'+
                '<div class="ls-home-plan">'+planPanel(true)+'</div>'+
                '<details class="ls-advanced"><summary>Advanced: use an API key instead of Claude Code</summary><p>Most people should use Claude Code. If you’d rather pay per request with an API key, save it here, then choose “API brain” in the studio’s setup page.</p>'+brainForm()+'</details>';
        }
        return '<div class="ls-member-grid">'+setupSteps()+planPanel()+'</div>'+claudeCodeWay()+discordCard()+
            '<section class="ls-section"><div class="ls-section-row"><h2>Your projects</h2><button class="btn-ghost" data-ls-refresh>Refresh</button></div><div id="ls-history"><p class="ls-muted">Loading…</p></div></section>'+
            '<details class="ls-advanced"><summary>Advanced: use an API key instead of Claude Code</summary><p>Most people should use Claude Code. If you’d rather pay per request with an API key, save it here, then choose “API brain” in the studio’s setup page.</p>'+brainForm()+'</details>';
    }

    // Ideas to come back for: channels the Niche Finder added this week (or the top earners when the week was quiet).
    const compact = n => { n = Number(n) || 0; return n >= 1e6 ? (n/1e6).toFixed(n >= 1e7 ? 0 : 1)+'M' : n >= 1e3 ? (n/1e3).toFixed(n >= 1e4 ? 0 : 1)+'k' : String(Math.round(n)); };
    async function freshNiches() {
        const box = document.getElementById('ls-niches'), section = document.getElementById('ls-niches-section');
        if (!box || !section) return;
        const get = async query => { const r = await fetch('/api/niche-finder/channels?'+query); return r.ok ? ((await r.json()).channels || []) : null; };
        try {
            let rows = await get('added_within_days=7&limit=6&has_recent_avg=true'), fresh = true;
            if (rows === null) return;                       // a plan without the Niche Finder: no section
            if (rows.length < 3) { rows = await get('sort=recent_revenue&limit=6&has_recent_avg=true') || []; fresh = false; }
            if (!rows.length) return;
            document.getElementById('ls-niches-title').textContent = fresh ? 'Fresh niches this week' : 'Top niches right now';
            box.innerHTML = rows.slice(0, 6).map(c => {
                const video = (c.popular_videos || [])[0] || (c.recent_videos || [])[0] || {};
                const picture = video.thumbnail || c.avatar_url || '';
                const days = c.first_seen_at ? Math.max(0, Math.round((Date.now()/1000 - c.first_seen_at) / 86400)) : null;
                const money = Number(c.est_recent_monthly_revenue_usd || c.est_monthly_revenue_usd || 0);
                return '<a class="ls-niche" href="'+esc(c.channel_url || '#')+'" target="_blank" rel="noopener">'+
                    (picture ? '<img src="'+esc(picture)+'" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">' : '<span class="ls-niche-blank"></span>')+
                    '<div><strong>'+esc(c.channel_name || 'A growing channel')+'</strong>'+
                    '<small>'+(money ? 'est. $'+compact(money)+'/mo · ' : '')+compact(c.recent_avg_views)+' avg views'+
                    (fresh && days !== null ? ' · found '+(days === 0 ? 'today' : days === 1 ? 'yesterday' : days+' days ago') : '')+'</small>'+
                    (video.title ? '<span>“'+esc(String(video.title).slice(0, 90))+'”</span>' : '')+'</div></a>';
            }).join('');
            section.hidden = false;
        } catch (_) { /* ideas are a bonus: the page works without them */ }
    }

    // ---- API brain (alternative route) ---------------------------------
    function modelOptions(account, selected, same = false) {
        const choices = account?.brain_models || [];
        const saved = selected && !choices.some(m=>m.id===selected) ? '<option value="'+esc(selected)+'" selected>'+esc(selected)+' · saved</option>' : '';
        return (same?'<option value="">Same as writing model</option>':'')+saved+choices.map(m=>'<option value="'+esc(m.id)+'" '+(m.id===selected?'selected':'')+'>'+esc(m.label||m.id)+'</option>').join('');
    }
    function updateBrainModels(general, visual) {
        const account = providerInfo();
        document.getElementById('ls-brain-model').innerHTML = modelOptions(account, general||account.default_brain);
        document.getElementById('ls-brain-visual-model').innerHTML = modelOptions(account, visual===undefined?account.default_visual_brain:visual, true);
        document.querySelector('#ls-key-form button').disabled = !account.brain_models?.length;
    }
    function brainForm() {
        const account = providerInfo(), saved = data.brain || {};
        const general = saved.provider===brainChoice ? saved.model : account.default_brain;
        const visual = saved.provider===brainChoice ? (saved.visual_model||saved.model) : account.default_visual_brain;
        return '<div class="ls-key-card">'+
            (saved.connected?'<p class="ls-step-note">Saved: '+esc(data.providers?.[saved.provider]?.label||saved.provider)+' · writing '+esc(modelName(saved.provider,saved.model))+' · visuals '+esc(modelName(saved.provider,saved.visual_model||saved.model))+'</p>':'')+
            '<form id="ls-key-form"><label for="ls-brain-provider">Provider</label><select id="ls-brain-provider" class="cr-input">'+Object.entries(data.providers||{}).map(([id,p])=>'<option value="'+esc(id)+'" '+(id===brainChoice?'selected':'')+'>'+esc(p.label)+'</option>').join('')+'</select>'+
            '<label for="ls-brain-model">Writing and research model</label><select id="ls-brain-model" class="cr-input" required>'+modelOptions(account,general)+'</select>'+
            '<label for="ls-brain-visual-model">Visuals and review model</label><select id="ls-brain-visual-model" class="cr-input">'+modelOptions(account,visual,true)+'</select>'+
            '<label for="ls-brain-key" id="ls-brain-label">Your '+esc(account.label)+' API key</label><div class="ls-key-input"><input id="ls-brain-key" class="cr-input" type="password" autocomplete="off" placeholder="Paste the complete key" required minlength="16"><button class="btn-primary" type="submit">Save</button></div></form>'+
            '<p class="ls-step-note">Encrypted and stored with your ChannelRecipe account. Requests bill your own API account.'+(account.key_url?' <a id="ls-key-link" href="'+esc(account.key_url)+'" target="_blank" rel="noopener">Get a key ↗</a>':' <a id="ls-key-link" hidden>Get a key ↗</a>')+'</p>'+
            (saved.connected?'<button class="btn-ghost" data-ls-remove-key>Remove saved API key</button>':'')+'</div>';
    }

    // ---- Projects and stage review ------------------------------------
    async function history() {
        const el = document.getElementById('ls-history'); if (!el) return;
        try {
            const result = await api('projects');
            const projects = result.projects || [];
            el.innerHTML = projects.length ? '<div class="ls-projects">'+projects.slice(0,20).map(p=>{
                const stopped = ['needs_review','failed'].includes(p.status);
                const status = p.status==='done'?'Finished':stopped?'Needs attention':'In progress';
                return '<div class="ls-project"><article><div><strong>'+esc(p.title)+'</strong><small>'+new Date(p.created_at*1000).toLocaleDateString()+' · <span class="ls-pill '+(p.status==='done'?'ok':stopped?'warn':'')+'">'+status+'</span>'+(Number(p.calls)?' · '+Number(p.calls)+' model requests':'')+'</small></div>'+
                    '<button class="btn-ghost ls-view-requests" data-ls-requests="'+esc(p.id)+'" aria-expanded="false">Details</button></article><div id="ls-requests-'+esc(p.id)+'" class="ls-request-list" hidden></div></div>';
            }).join('')+'</div><p class="ls-step-note">Your finished videos, timelines and exports live in Studio on your computer. Claude Code uses your Claude account and its limits; voice, image and video charges stay in your media accounts.</p>'
            : '<p class="ls-muted">No projects yet. Once Studio is set up, your videos appear here.</p>';
            el.querySelectorAll('[data-ls-requests]').forEach(button=>button.addEventListener('click',()=>requests(button.dataset.lsRequests,button)));
        } catch (e) { el.innerHTML = '<p class="ls-muted">'+esc(e.message)+'</p>'; }
    }
    function nativeRequests(result) {
        const stages=result.stages||[];
        const purpose=result.purpose;
        const guidance=purpose==='video'?{
            complete:'The result is saved. Resume the same video to reuse its completed work.',
            approved:'One additional stage attempt has been approved. Resume the same video in your local studio; nothing starts from this history page.',
            footer:'Saved videos and exports remain on your computer. Use Your videos to open the timeline or the output folder.',
            link:'Open local setup to review this video ↗'}:purpose==='setup'?{
            complete:'This model-check result is saved. Return to local setup to finish or review model verification.',
            approved:'One additional model-check attempt has been approved. Repeat model verification in local setup; approval does not start verification.',
            footer:'These are model-verification checks, not video renders. Return to local setup to verify the saved model profile.',
            link:'Open local setup for model verification ↗'}:purpose==='creative'?{
            complete:'This creative-stage result is saved with its operation. Review the result using its original studio controls.',
            approved:'An additional stage attempt is allowed, but the stopped creative action will not replay. Use the original studio controls to explicitly start a new action from current cached assets; new work may repeat Claude calls and use more allowance.',
            footer:'Stopped creative actions do not replay from this history page. Review their saved records, then use the original studio controls for an explicitly requested new action.',
            link:'Open local setup to review creative work ↗'}:{
            complete:'This result is saved. Review it using the original controls that started this work.',
            approved:'One additional stage attempt is allowed. Review its purpose and original controls before starting more work.',
            footer:'Review the saved status and original studio controls before continuing this work.',
            link:'Open local setup to review saved work ↗'};
        const labels={complete:'Stage saved',failed:'Stage stopped',retry_allowed:'Stage attempt approved',authorized:'Awaiting a saved result'};
        const errors={usage_limit:'Your Claude account reached its usage limit. Wait for its reset, then review this stage in local setup.',
            model_unavailable:'Your Claude account could not use the selected model. Review model access in local setup.',
            auth_missing:'Claude Code could not use your login. Sign in again, then review this stage in local setup.',
            timeout:'Claude Code did not return a complete result before the timeout. Review its saved status and Claude usage in local setup.'};
        return '<p class="ls-inline-note">These stages use your local Claude Code account. CR records their authorization and saved results; it does not bill an API brain for these stages.</p>'+
            (stages.length?stages.map(stage=>{
                const usage=stage.usage||{}, tokens=Number(usage.input_tokens||0)+Number(usage.output_tokens||0)+Number(usage.cache_read_input_tokens||0)+Number(usage.cache_creation_input_tokens||0);
                const message=stage.status==='complete'?guidance.complete:stage.status==='retry_allowed'?guidance.approved:stage.status==='authorized'?'This stage may still be running or waiting for its result to sync. Refresh its status in local setup before continuing or approving another attempt.':errors[stage.error_code]||'This stage stopped before a complete result was saved. Review its status and your Claude usage in local setup.';
                const role=stage.model_role==='visual'?'Complex visuals':'General work';
                const kind={text:'Text',vision:'Vision',research:'Research'}[stage.kind]||stage.kind;
                return '<section class="ls-request"><div class="ls-request-heading"><strong>'+esc(labels[stage.status]||stage.status)+'</strong><span>'+Number(stage.attempts)+' '+(stage.attempts===1?'attempt':'attempts')+'</span></div>'+
                    '<p>'+esc(message)+'</p><small>'+esc(role)+' · '+esc(kind)+' · '+esc(modelName('anthropic',stage.selected_model))+' · Claude Code</small><small>'+new Date(stage.updated_at*1000).toLocaleString()+'</small>'+
                    (usage.reported?'<small>'+tokens.toLocaleString()+' locally reported tokens, including cached input. Your Claude account determines actual usage and limits.</small>':'<small>Token usage has not been reported for this attempt. Check your Claude account; this does not mean no usage occurred.</small>')+
                    '<details class="ls-request-debug"><summary>Details for support</summary><small class="ls-request-id">Stage '+esc(stage.stage_id)+'</small>'+(stage.error_code?'<small>'+esc(stage.error_code)+'</small>':'')+'</details></section>';
            }).join(''):'<p>No Claude Code stages have been authorized for this project yet.</p>')+
            '<p class="ls-inline-note">'+esc(guidance.footer)+'</p>'+
            '<a class="btn-secondary" href="http://127.0.0.1:7860/cr-setup" target="_blank" rel="noopener">'+esc(guidance.link)+'</a>';
    }
    async function requests(projectId, button) {
        const el=document.getElementById('ls-requests-'+projectId);if(!el)return;
        if(!el.hidden){el.hidden=true;button.setAttribute('aria-expanded','false');return;}
        el.hidden=false;button.setAttribute('aria-expanded','true');el.innerHTML='<p role="status">Loading request status…</p>';
        try {
            let progress;
            try {progress=await api('projects/'+projectId+'/progress');}
            catch(error) {if(error.status!==404)throw error;}
            if(progress) {
                if(progress.brain_mode!=='claudecode')throw new Error('This project returned an unrecognised workflow. Refresh its status before continuing.');
                el.innerHTML=nativeRequests(progress);
                return;
            }
            const result=await api('projects/'+projectId+'/requests');
            const labels={complete:'Answer saved',needs_review:'Request stopped',retry_allowed:'Ready to resume',running:'Still running'};
            el.innerHTML=result.requests.length?result.requests.map(r=>{
                const error=r.errors?.[r.errors.length-1];
                const account=r.context?.provider?(data.providers?.[r.context.provider]?.label||r.context.provider):null;
                const retryAccount=account||data.providers?.[data.brain?.provider]?.label||'selected API';
                const message=r.status==='complete'?'Resume the same video to reuse this saved answer.':r.status==='retry_allowed'?'One retry is allowed. Resume the same video in your local studio.':r.status==='running'?'The request is still running or awaiting a saved result. Refresh its status before continuing.':error?.message||'This request stopped before a complete result was saved. The earlier preview did not retain its detailed error.';
                return '<section class="ls-request"><div class="ls-request-heading"><strong>'+esc(labels[r.status]||r.status)+'</strong><span>'+Number(r.attempts)+' '+(r.attempts===1?'attempt':'attempts')+'</span></div>'+
                    '<p>'+esc(message)+'</p><small>'+new Date(r.created_at*1000).toLocaleString()+(account?' · '+esc(account):'')+'</small>'+
                    (error?.usage_reported?'<small>'+Number(error.input_tokens+error.output_tokens).toLocaleString()+' tokens reported for the stopped attempt. Provider billing may include other charges.</small>':r.status==='needs_review'?'<small>The stopped attempt’s charge is unknown. Check your provider’s usage if needed.</small>':'')+
                    (r.can_retry?'<form data-ls-retry-form data-project="'+esc(projectId)+'" data-request="'+esc(r.id)+'" data-attempt="'+Number(r.attempts)+'"><p>A retry repeats this one model request using your connected '+esc(retryAccount)+' account. Your provider may charge again. Your saved work stays available.</p><label class="ls-retry-consent"><input type="checkbox" required> I understand my provider may charge for this retry.</label><button class="btn-primary" type="submit" disabled>Allow one retry</button></form>':r.status==='needs_review'?'<p class="ls-inline-note">Another retry is unavailable. The recovery attempt or project request limit has been used. This status is available here for support.</p>':'')+
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

    // ---- Render ----------------------------------------------------------
    function render() {
        if (!root()) return;
        const u = user();
        const starterNote = view==='upgrade' && u && typeof planTier==='function' && planTier(u.plan)==='starter'
            ? '<aside class="ls-version-note"><strong>Your Starter plan includes Classic recipes (v1).</strong> Studio is included from Creator. Switch any time and keep your account.</aside>' : '';
        const body = view === 'member' ? memberBody() : versionNote()+starterNote+features()+planCards()+discordCard(true)+faq();
        root().innerHTML = '<div class="ls-wrap">'+hero()+(view === 'member' ? shipped() : '')+'<div id="ls-notice" role="status" hidden></div>'+nicheNote()+body+
            '<footer class="ls-footer"><span>ChannelRecipe '+(data?.version?esc(studioName(data.version)):'Studio')+'</span><a href="'+DISCORD_URL+'" target="_blank" rel="noopener" data-community="discord">Help in Discord ↗</a></footer></div>';
        bind();
        if (view === 'member') { history(); if (returning()) freshNiches(); }
    }
    function bind() {
        const r = root();
        r.querySelectorAll('[data-ls-shipped-seen]').forEach(el=>el.addEventListener('click',()=>{
            try { localStorage.setItem(shippedKey(el.dataset.lsShippedSeen), '1'); } catch (_) {}
            track('studio_shipped_dismiss', {build: el.dataset.lsShippedSeen});
            const card = document.getElementById('ls-shipped'); if (card) card.remove();
        }));
        r.querySelector('[data-ls-shipped-update]')?.addEventListener('click',()=>track('studio_shipped_update', {build: (data.changelog||[])[0]?.build}));
        if (r.querySelector('#ls-shipped')) track('studio_shipped_view', {build: (data.changelog||[])[0]?.build});
        r.querySelectorAll('[data-ls-plans]').forEach(el=>el.addEventListener('click',()=>{
            if (view === 'member') { if (typeof showPricingModal === 'function') showPricingModal({reason:'studio'}); return; }
            document.getElementById('ls-plans')?.scrollIntoView({behavior:'smooth',block:'start'});
        }));
        r.querySelector('[data-ls-classic]')?.addEventListener('click',()=>{ track('studio_open_classic'); navigateTo('pipeline'); });
        r.querySelectorAll('[data-ls-cycle]').forEach(el=>el.addEventListener('click',()=>{
            if (typeof setPricingPlan === 'function') setPricingPlan(el.dataset.lsCycle);
            render(); document.getElementById('ls-plans')?.scrollIntoView({block:'start'});
        }));
        r.querySelectorAll('[data-ls-buy]').forEach(el=>el.addEventListener('click',()=>{
            const tier = el.dataset.lsBuy;
            track('studio_plan_clicked', {tier, interval: cycle(), signed_in: !!user()});
            const go = () => proceedToCheckout(tier);
            if (!user()) { if (typeof ensureAuth === 'function') ensureAuth(go); else showAuthModal(); return; }
            go();
        }));
        r.querySelectorAll('[data-ls-platform]').forEach(el=>el.addEventListener('click',()=>{ platform = el.dataset.lsPlatform; render(); }));
        r.querySelector('[data-ls-download]')?.addEventListener('click',()=>track('studio_download_clicked',{platform}));
        r.querySelector('[data-ls-open-studio]')?.addEventListener('click',()=>track('studio_open_clicked',{source:'home'}));
        r.querySelectorAll('[data-ls-walk-seek]').forEach(el=>el.addEventListener('click',()=>{
            const video = r.querySelector('#ls-walk-video'); if (!video) return;
            const at = Number(el.dataset.lsWalkSeek) || 0;
            const first = !video.controls;
            video.controls = true;
            video.closest('.ls-walk-player')?.classList.add('playing');
            const go = () => { try { video.currentTime = at; } catch (_) {} video.play().catch(()=>{}); };
            if (video.readyState >= 1) go(); else { video.addEventListener('loadedmetadata', go, {once:true}); video.load(); }
            if (!el.classList.contains('ls-walk-play')) video.scrollIntoView({block:'nearest', behavior:'smooth'});
            track('studio_setup_video_played', {at, first});
        }));
        r.querySelector('[data-ls-copy-prompt]')?.addEventListener('click',()=>{ track('studio_prompt_copied'); copy(CLAUDE_PROMPT,'Copied. Paste it into Claude Code in your Studio folder.'); });
        r.querySelector('[data-ls-copy-key]')?.addEventListener('click',async()=>{
            try {
                const mcp = await fetch('/api/me/mcp'); if (!mcp.ok) throw new Error('Couldn’t get your key. Refresh and try again.');
                const account = await mcp.json(); await copy(account.api_key,'Key copied. Paste it into the studio’s setup page.');
            } catch (e) { notice(e.message,true); }
        });
        r.querySelectorAll('[data-ls-discord]').forEach(el=>el.addEventListener('click',()=>track('discord_clicked',{source:'studio_page'})));
        r.querySelector('[data-ls-refresh]')?.addEventListener('click',history);
        r.querySelector('[data-ls-niche-finder]')?.addEventListener('click',()=>{ track('studio_home_niche_finder'); navigateTo('niche-finder'); });
        r.querySelector('#ls-key-form')?.addEventListener('submit',async event=>{
            event.preventDefault(); const button = event.target.querySelector('button'); button.disabled = true; button.textContent = 'Checking…';
            try {
                const result = await api('brain-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:document.getElementById('ls-brain-key').value.trim(),provider:brainChoice,model:document.getElementById('ls-brain-model').value,visual_model:document.getElementById('ls-brain-visual-model').value||null})});
                data = await api('status'); brainChoice = data.brain?.provider||brainChoice; render(); notice(result.message||'Saved.');
            } catch (e) { notice(e.message,true); }
            finally { button.disabled = false; button.textContent = 'Save'; }
        });
        r.querySelector('#ls-brain-provider')?.addEventListener('change',event=>{
            brainChoice = event.target.value; document.getElementById('ls-brain-key').value = '';
            document.getElementById('ls-brain-label').textContent = 'Your '+providerInfo().label+' API key';
            const link = document.getElementById('ls-key-link'); link.href = providerInfo().key_url||''; link.hidden = !providerInfo().key_url;
            updateBrainModels();
        });
        if (r.querySelector('#ls-key-form')) r.querySelector('#ls-key-form button').disabled = !providerInfo().brain_models?.length;
        r.querySelector('[data-ls-remove-key]')?.addEventListener('click',async()=>{
            try { await api('brain-key',{method:'DELETE'}); data = await api('status'); render(); notice('API key removed.'); }
            catch (e) { notice(e.message,true); }
        });
    }
    async function open() {
        if (!root() || busy) return;
        busy = true;
        try {
            const u = user();
            const entitled = typeof hasStudioAccess === 'function' ? hasStudioAccess() : !!u?.is_admin;
            if (!u || !entitled) { view = u ? 'upgrade' : 'guest'; data = null; render(); track('studio_page_viewed',{view}); return; }
            if (!data) root().innerHTML = '<div class="ls-wrap"><p class="ls-muted">Loading your studio…</p></div>';
            try {
                data = await api('status'); view = 'member';
                brainChoice = data.brain?.connected ? data.brain.provider : 'anthropic';
            } catch (e) {
                if (e.status === 403 || e.status === 401) { view = 'upgrade'; data = null; }
                else { root().innerHTML = '<div class="ls-wrap"><div class="ls-error-card"><h2>Studio couldn’t load</h2><p>'+esc(e.message)+'</p><button class="btn-primary" id="ls-retry">Try again</button></div></div>'; document.getElementById('ls-retry').addEventListener('click',open); return; }
            }
            render(); track('studio_page_viewed',{view});
        } finally { busy = false; }
    }
    return {open};
})();
