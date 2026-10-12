"""Keep the original studio controls honest about CR account overrides.

This is a presentation adapter. It does not change the original voice, feature,
style, director or render functions used for production.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping


_FOOTER = """<footer class="cr-studio-footer">
  <div class="updates"><span class="txt"><b>ChannelRecipe studio</b>
    Get the current studio and workflow from your ChannelRecipe account.
    Planning uses your selected brain account and its limits. Generated media uses your
    provider balances; actual charges are shown in those accounts.</span>
    <a href="https://channelrecipe.com/app#local-studio" target="_blank" rel="noopener">Studio &amp; updates &rarr;</a>
  </div>
  <div class="foot-row"><span class="by">CHANNELRECIPE</span>
    <a href="/cr-setup">Setup &amp; accounts</a>
  </div>
</footer>"""

_ADAPTER = r"""<style id="cr-account-ui-styles">
/* Original-provider estimates do not describe the customer's CR overrides. */
.cp-cost,.sl-cost,.chbar .cost,#chBar .cost,#featCost,.feat .price,#avCost,.avcost{display:none!important}
#costHint{white-space:normal;max-width:100%;line-height:1.6}
.cr-narration-note{font-size:12.5px;line-height:1.6;color:var(--dim);margin-top:10px}
</style>
<script id="cr-account-ui">
(()=>{
  'use strict';
  const narration=__CR_NARRATION__;
  const costs='Planning uses your Claude Code or API account. Media uses your provider balances; check those accounts for prices and actual charges.';
  const get=id=>document.getElementById(id);
  function showCosts(){
    const hint=get('costHint');
    if(hint){if(hint.textContent!==costs)hint.textContent=costs;hint.removeAttribute('title');}
    document.querySelectorAll('.sl-pick[title],.cp-cost[title],.sl-cost[title],#chBar .cost[title],.feat .price[title]').forEach(el=>{
      const title=el.getAttribute('title')||'';
      const clean=title.split('\n').filter(line=>!/(?:[$\u00a3\u20ac]|\bfree(?:[ -]tier)?\b)/i.test(line)).join('\n');
      if(clean!==title){if(clean)el.setAttribute('title',clean);else el.removeAttribute('title');}
    });
  }
  function showNarration(){
    if(!narration)return;
    // The original recording/upload path owns all of its own controls.
    if(typeof VO_MODE!=='undefined'&&VO_MODE!=='ai')return;
    const bar=get('voBar');if(!bar)return;
    const info=document.createElement('div');info.className='who';
    const title=document.createElement('b');title.textContent=narration.voice||'Your saved narration voice';
    const detail=document.createElement('span');
    detail.textContent=narration.label+' · '+(narration.model_label||narration.model||'selected voice model')+(narration.connected?' · your selected narration account':' · reconnect this account in setup');
    info.append(title,detail);
    const change=document.createElement('a');change.className='chg';change.id='cr-narration-change';change.href='/cr-setup';change.textContent='Change narration';
    bar.replaceChildren(info,change);
    const warn=get('voWarn');if(warn){warn.textContent='';warn.classList.add('hidden');}
    const library=get('voLib');if(library)library.classList.add('hidden');
    if(typeof VO_OPEN!=='undefined')VO_OPEN=false;
    const hint=get('voHint');if(hint)hint.textContent='your selected narration account';
    const note=document.querySelector('#voAi .vonote');
    if(note)note.textContent='Narration uses the account, model and voice selected in Setup & accounts. This replaces preset and library voices. Your own uploaded voiceover is still available. Saved audio is reused when you resume; redo the voice only when you want it regenerated.';
  }
  function install(){
    // These global functions only draw the original UI. Production choices,
    // native recording handlers and the submitted job specification stay intact.
    if(typeof window.updateEstimate==='function')window.updateEstimate=showCosts;
    if(narration&&typeof window.drawVoice==='function'){
      const original=window.drawVoice;
      window.drawVoice=function(...args){const result=original.apply(this,args);showNarration();return result;};
    }
    showNarration();showCosts();
    const observer=new MutationObserver(showCosts);
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['title']});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
</script>"""


_THREED_REVIEW = r"""<style id="cr-3d-review-styles">
.cr-3d-panel{border:1px solid var(--line,#343744);border-radius:16px;padding:22px;margin:18px 0;background:var(--panel,#171922)}
.cr-3d-panel[hidden]{display:none!important}.cr-3d-top{display:flex;align-items:center;justify-content:space-between;gap:16px}
.cr-3d-top h3{margin:0}.cr-3d-panel p{line-height:1.6;color:var(--dim,#adb0bd)}
.cr-3d-panel video{display:block;width:100%;max-height:360px;margin:16px 0;background:#000;border-radius:10px}
.cr-3d-note{display:block;margin:18px 0 8px}.cr-3d-panel textarea{display:block;width:100%;min-height:78px;box-sizing:border-box;padding:12px;border:1px solid var(--line,#343744);border-radius:10px;background:var(--bg,#101116);color:var(--text,#f1f1f4);font:inherit}
.cr-3d-scenes{display:grid;gap:10px;margin:16px 0}.cr-3d-scene{display:flex;gap:10px;align-items:flex-start;padding:12px;border:1px solid var(--line,#343744);border-radius:10px}.cr-3d-scene[hidden]{display:none!important}
.cr-3d-scene input{margin-top:4px}.cr-3d-scene small{display:block;margin-top:5px;color:var(--dim,#adb0bd);line-height:1.5}.cr-3d-scene button{display:block;margin-top:8px}
.cr-3d-actions{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}.cr-3d-actions button,.cr-3d-top button{cursor:pointer}
.cr-3d-actions button:disabled{opacity:.45;cursor:default}.cr-3d-status{white-space:pre-wrap}.cr-3d-recovery a{color:inherit}
</style><script id="cr-3d-review-ui">
(()=>{
  'use strict';
  const cards=document.getElementById('cards');if(!cards)return;
  const discovery=new Map();let slug='',state=null,working=false,timer=null,lastAction='',previousState='';
  const selected=new Set();
  const panel=document.createElement('section');panel.id='cr-3d-panel';panel.className='cr-3d-panel';panel.hidden=true;
  panel.innerHTML='<div class="cr-3d-top"><h3>Review 3D scenes</h3><button type="button" class="dl" id="cr-3d-close">Close</button></div>'+
    '<p id="cr-3d-title"></p><video id="cr-3d-video" controls playsinline preload="metadata"></video>'+
    '<p>Watch the finished video, then check its rendered 3D scenes or select scenes to repair. Repairs keep this video’s original style.</p>'+
    '<p><b>Claude usage:</b> Checking and repairing scenes use your connected Claude account and its limits. Each action starts only when you press its button.</p>'+
    '<p id="cr-3d-status" class="cr-3d-status" role="status" aria-live="polite"></p>'+
    '<p id="cr-3d-recovery" class="cr-3d-recovery" hidden></p><p id="cr-3d-summary"></p>'+
    '<label id="cr-3d-new-work-label" class="cr-3d-scene" hidden><input type="checkbox" id="cr-3d-new-work">'+
    '<span>I reviewed the stopped action and accept that a new action may repeat Claude work and use more allowance.</span></label>'+
    '<div id="cr-3d-scenes" class="cr-3d-scenes"></div>'+
    '<label class="cr-3d-note" for="cr-3d-note">What would you like changed? <small>Optional, for selected scenes</small></label>'+
    '<textarea id="cr-3d-note" maxlength="2000" placeholder="For example: make the subject easier to see and keep the original look."></textarea>'+
    '<div class="cr-3d-actions"><button type="button" class="dl" id="cr-3d-qa">Check finished scenes</button>'+
    '<button type="button" class="dl" id="cr-3d-redo">Repair selected scenes</button>'+
    '<button type="button" class="dl" id="cr-3d-flagged">Repair flagged scenes</button>'+
    '<button type="button" class="dl" id="cr-3d-sync" hidden>Sync saved completion</button>'+
    '<button type="button" class="dl" id="cr-3d-assemble" hidden>Update final video</button></div>'+
    '<p id="cr-3d-sync-note" hidden>Sync only updates the saved completion record. It does not start generation or use your Claude or media allowance.</p>'+
    '<p id="cr-3d-assembly" hidden>Repaired scenes are saved. Update the final video to assemble those changes into the video you can watch and export.</p>';
  cards.before(panel);
  const get=id=>document.getElementById(id);
  const stopped=()=>state&&state.state!=='pending'&&state.state!=='running'&&(state.state==='failed'||state.needs_manual_recovery);
  const blocked=()=>working||!state||!state.available||state.busy||state.state==='running'||state.state==='pending'||(stopped()&&!get('cr-3d-new-work').checked);
  const flags=()=>Array.isArray(state?.flags)?state.flags:[];
  function buttons(){
    const stop=blocked();get('cr-3d-qa').disabled=stop;
    get('cr-3d-redo').disabled=stop||selected.size===0;
    get('cr-3d-flagged').disabled=stop||!flags().some(f=>Number(f.severity)>=2);
    get('cr-3d-sync').hidden=state?.state!=='pending';
    get('cr-3d-sync').disabled=working||state?.state!=='pending';
    get('cr-3d-sync-note').hidden=state?.state!=='pending';
    get('cr-3d-new-work').disabled=working||Boolean(state?.busy);
    get('cr-3d-assemble').hidden=!state?.needs_reassembly;
    get('cr-3d-assemble').disabled=stop;
    get('cr-3d-assembly').hidden=!state?.needs_reassembly;
    get('cr-3d-note').disabled=stop;
    get('cr-3d-scenes').querySelectorAll('input').forEach(input=>{input.disabled=stop;});
  }
  function draw(){
    get('cr-3d-status').textContent=state?.message||(working?'Starting your requested action…':'Ready to review this video.');
    const recovery=get('cr-3d-recovery');recovery.replaceChildren();
    const needsRecovery=state?.needs_manual_recovery||state?.state==='failed'||state?.state==='pending';
    recovery.hidden=!needsRecovery;
    get('cr-3d-new-work-label').hidden=!stopped();if(!stopped())get('cr-3d-new-work').checked=false;
    get('cr-3d-assembly').textContent=stopped()?'Update final video resumes the same cached assembly project when its saved stages are ready. Failed or unfinished Claude stages must be reviewed in Setup & accounts first. This resumes the export; new scene checks and repairs are separate actions.':'Repaired scenes are saved. Update the final video to assemble those changes into the video you can watch and export.';
    if(needsRecovery){
      const explanation=document.createElement('span');explanation.textContent=state?.state==='pending'?'Your result is saved, but its completion record needs syncing before more work starts. ':state?.state==='running'?'This action is still running. Wait for it to finish before starting more work. ':'The stopped action will not replay. Review its saved record, then explicitly start a new check or repair from the current cached scenes. New work may repeat Claude calls and use more allowance. ';
      const link=document.createElement('a');link.href='/cr-setup';link.textContent='Open Setup & accounts';recovery.append(explanation,link);
      if(/^crv_[a-f0-9]{24}$/.test(state.operation_project_id||'')){
        const reference=document.createElement('small');reference.style.display='block';reference.textContent='Saved work: '+state.operation_project_id;recovery.append(reference);
      }
    }
    const all=Array.isArray(state?.scenes)?state.scenes:[];
    const byId=new Map(flags().filter(f=>typeof f.id==='string').map(f=>[f.id,f]));
    const sceneRows=all.filter(s=>typeof s.id==='string');
    const ids=new Set(sceneRows.map(s=>s.id));selected.forEach(id=>{if(!ids.has(id))selected.delete(id);});
    const failed=Array.isArray(state?.failed_calls)?state.failed_calls.length:0;
    get('cr-3d-summary').textContent=flags().length?flags().length+' scene'+(flags().length===1?'':'s')+' flagged in the saved check. Select individual scenes or repair the flagged scenes.'+(failed?' '+failed+' could not be checked. Review the saved action in Setup & accounts.':''):
      failed?failed+' scene'+(failed===1?'':'s')+' could not be checked. Review the saved action in Setup & accounts.':
      sceneRows.length?'No saved flags to repair. You can check the finished scenes or select a scene you want changed.':'No rendered 3D scenes are available for this video.';
    const list=get('cr-3d-scenes');list.replaceChildren();
    sceneRows.forEach((scene,index)=>{
      const row=document.createElement('label');row.className='cr-3d-scene';
      const box=document.createElement('input');box.type='checkbox';box.checked=selected.has(scene.id);box.setAttribute('aria-label','Select scene '+(index+1));
      box.onchange=()=>{if(box.checked)selected.add(scene.id);else selected.delete(scene.id);buttons();};
      const content=document.createElement('span');const title=document.createElement('b');title.textContent='Scene '+(index+1)+(byId.has(scene.id)?' · flagged':'');content.append(title);
      if(typeof scene.words==='string'&&scene.words){const words=document.createElement('small');words.textContent='Scene plan: '+scene.words;content.append(words);}
      if(typeof scene.at==='number'&&Number.isFinite(scene.at)&&scene.at>=0){
        const jump=document.createElement('button');jump.type='button';jump.className='dl cr-3d-jump';
        jump.textContent='Watch from '+Math.floor(scene.at/60)+':'+String(Math.floor(scene.at%60)).padStart(2,'0');
        jump.onclick=event=>{event.preventDefault();event.stopPropagation();const video=get('cr-3d-video');video.currentTime=scene.at;video.play().catch(()=>{});};content.append(jump);
      }
      const flag=byId.get(scene.id);
      if(flag){const detail=document.createElement('small');detail.textContent=flag.note||'The saved check suggests a repair.';content.append(detail);
        if(flag.fix){const fix=document.createElement('small');fix.textContent='Suggested change: '+flag.fix;content.append(fix);}}
      row.append(box,content);list.append(row);
    });
    buttons();
  }
  async function request(path,options){
    const response=await fetch(path,options);let data;
    try{data=await response.json();}catch(error){throw new Error('The studio could not read this response. Refresh the studio and try again.');}
    if(!response.ok||data.error)throw new Error(data.error||'The studio could not complete this request.');
    return data;
  }
  const endpoint=value=>'/cr-3d/'+encodeURIComponent(value);
  function schedule(){clearTimeout(timer);if(!panel.hidden&&(state?.busy||state?.state==='running'||state?.state==='pending'))timer=setTimeout(()=>refresh(slug),2500);}
  async function refresh(value){
    try{
      const data=await request(endpoint(value));if(value!==slug||panel.hidden)return;
      if(state?.operation_project_id!==data.operation_project_id)get('cr-3d-new-work').checked=false;
      state=data;discovery.set(value,Promise.resolve(data));
      if(previousState==='running'&&state.state==='done'&&lastAction==='assemble'&&!state.needs_reassembly){
        const video=get('cr-3d-video');video.src='/f/video/'+encodeURIComponent(slug)+'?cr-revision='+Date.now();
        if(typeof window.loadLibrary==='function')window.loadLibrary();lastAction='';
      }
      previousState=state.state;draw();schedule();
    }catch(error){if(value!==slug||panel.hidden)return;clearTimeout(timer);state=null;get('cr-3d-status').textContent=error.message;buttons();}
  }
  async function open(value,card){
    clearTimeout(timer);slug=value;state=null;selected.clear();working=false;lastAction='';previousState='';panel.hidden=false;get('cr-3d-new-work').checked=false;get('cr-3d-new-work-label').hidden=true;
    get('cr-3d-title').textContent=card.querySelector('.meta>b')?.textContent||'Your video';
    get('cr-3d-video').src='/f/video/'+encodeURIComponent(value);get('cr-3d-note').value='';
    get('cr-3d-scenes').replaceChildren();get('cr-3d-summary').textContent='';get('cr-3d-recovery').hidden=true;
    get('cr-3d-status').textContent='Loading the saved scene review…';buttons();panel.scrollIntoView({behavior:'smooth',block:'start'});await refresh(value);
  }
  async function act(action,fromQa=false){
    if(action==='sync'?(working||state?.state!=='pending'):blocked())return;const value=slug;working=true;lastAction=action;clearTimeout(timer);buttons();get('cr-3d-status').textContent=action==='sync'?'Syncing the saved completion record…':'Starting your requested action…';
    try{
      const note=action==='redo'?get('cr-3d-note').value.trim():'';
      // Frontier applies a custom note to named scenes. Keep the reviewed
      // selection when feedback is added, rather than dropping that feedback.
      const sceneIds=action==='redo'?(fromQa?(note?flags().filter(f=>Number(f.severity)>=2).map(f=>f.id):[]):Array.from(selected)):[];
      const payload=action==='sync'?{action}:{action,scene_ids:sceneIds,note,from_qa:fromQa&&sceneIds.length===0};
      if(action!=='sync'&&stopped()&&get('cr-3d-new-work').checked)payload.acknowledge_new_work=true;
      await request(endpoint(value),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(value===slug&&!panel.hidden){working=false;previousState='running';await refresh(value);}
    }catch(error){if(value===slug&&!panel.hidden){working=false;await refresh(value);get('cr-3d-status').textContent=error.message;buttons();}}
  }
  get('cr-3d-qa').onclick=()=>act('qa');get('cr-3d-redo').onclick=()=>act('redo');get('cr-3d-flagged').onclick=()=>act('redo',true);get('cr-3d-assemble').onclick=()=>act('assemble');get('cr-3d-sync').onclick=()=>act('sync');
  get('cr-3d-new-work').onchange=buttons;
  get('cr-3d-close').onclick=()=>{panel.hidden=true;clearTimeout(timer);get('cr-3d-video').pause();};
  function discover(){
    cards.querySelectorAll('.reveal[data-slug]').forEach(reveal=>{
      if(reveal.dataset.cr3dChecked)return;reveal.dataset.cr3dChecked='true';const value=reveal.dataset.slug;const card=reveal.closest('.card');if(!card)return;
      if(!discovery.has(value))discovery.set(value,request(endpoint(value)).catch(()=>null));
      discovery.get(value).then(data=>{
        if(!data?.available)discovery.delete(value);
        if(!data?.available||!reveal.isConnected||card.querySelector('.cr-3d-open'))return;
        const button=document.createElement('button');button.type='button';button.className='dl cr-3d-open';button.textContent='Review 3D scenes';button.onclick=()=>open(value,card);reveal.parentElement.append(button);
      });
    });
  }
  const observer=new MutationObserver(discover);observer.observe(cards,{childList:true,subtree:true});discover();
})();
</script>"""


def _narration_payload(narration: Mapping | None) -> dict | None:
    if not isinstance(narration, Mapping) or narration.get("provider") not in {"kie", "atlas"}:
        return None
    # Never pass config objects, keys or credentials through to the browser.
    result = {key: str(narration.get(key) or "")[:200] for key in
              ("provider", "label", "model", "model_label", "voice")}
    result["label"] = result["label"] or {"kie": "Kie.ai", "atlas": "Atlas Cloud"}[result["provider"]]
    result["connected"] = narration.get("connected") is True
    return result


def transform_html(text: str, *, narration: Mapping | None = None, preset: Mapping | None = None) -> str:
    """Adapt the production screen using sanitized active narration metadata.

    ``narration`` accepts provider, label, model, model_label, voice and connected.
    Pass None when the individual original voice settings are selected. Other
    pages (including product previews with their own prices) are left untouched.
    """
    if not (re.search(r'\bid=["\']voBar["\']', text)
            and re.search(r'\bid=["\']go["\']', text)):
        return text
    if 'id="cr-account-ui"' in text:
        return text
    if isinstance(preset,Mapping) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",str(preset.get("style", ""))):
        style=str(preset["style"])
        tab=preset.get("tab") if preset.get("tab") in {"production","avatar","3d"} else "production"
        features={k:v for k,v in (preset.get("features") or {}).items() if re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",str(k)) and isinstance(v,bool)}
        state=json.dumps({"style":style,"tab":tab,"features":features},ensure_ascii=True).replace("<","\\u003c")
        # Run before Frontier reads its own remembered controls. This carries
        # the creator's checked selection, without changing preset defaults.
        carry='<script id="cr-preset-selection">(()=>{const s='+state+';try{localStorage.setItem("fr.tab",s.tab);localStorage.setItem("fr.ch",s.style);localStorage.setItem("fr.ch."+s.tab,s.style);localStorage.setItem("fr.feat2."+s.style,JSON.stringify(s.features))}catch(e){}})();</script>'
        text=text.replace("</head>",carry+"</head>",1)
    text = re.sub(r"<footer\b[^>]*>.*?</footer>", lambda _: _FOOTER, text,
                  count=1, flags=re.IGNORECASE | re.DOTALL)
    payload = json.dumps(_narration_payload(narration), ensure_ascii=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    adapter = _ADAPTER.replace("__CR_NARRATION__", payload) + _THREED_REVIEW
    return re.sub(r"</body\s*>", lambda match: adapter + match.group(), text,
                  count=1, flags=re.IGNORECASE)
