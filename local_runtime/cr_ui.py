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
    adapter = _ADAPTER.replace("__CR_NARRATION__", payload)
    return re.sub(r"</body\s*>", lambda match: adapter + match.group(), text,
                  count=1, flags=re.IGNORECASE)
