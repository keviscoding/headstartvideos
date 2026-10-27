"""ChannelRecipe's presentation layer over the original local studio.

This is a presentation adapter. It does not change the original voice, feature,
style, director or render functions used for production.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser


# Use the same native glyph and tokens as the CR website. Everything is inline:
# a downloaded studio can draw its chrome without a font/CDN or brand request.
_BRAND = """<a class="cr-brand" href="/" aria-label="ChannelRecipe studio">
<svg viewBox="0 0 100 100" fill="none" id="brandmark" width="34" height="34" aria-hidden="true"><defs><linearGradient id="crpot-heat" x1="0" y1="1" x2="0.6" y2="0"><stop offset="0" stop-color="#6D5AE0"/><stop offset="0.6" stop-color="#9B5BE6"/><stop offset="1" stop-color="#E8608F"/></linearGradient></defs><g stroke="#FF7A59" stroke-width="6" stroke-linecap="round"><path d="M37 24C33 19 41 15 37 7"/><path d="M50 24C46 19 54 15 50 7"/><path d="M63 24C59 19 67 15 63 7"/></g><rect x="44" y="27" width="12" height="6" rx="3" fill="#9B5BE6"/><rect x="14" y="32" width="72" height="8" rx="4" fill="#9B5BE6"/><rect x="7" y="49" width="14" height="9" rx="4.5" fill="#7F5AE3"/><rect x="79" y="49" width="14" height="9" rx="4.5" fill="#A95BE4"/><path d="M18 43H82V74A14 14 0 0 1 68 88H32A14 14 0 0 1 18 74Z" fill="url(#crpot-heat)"/><path d="M44 55L44 76L62 65.5Z" fill="#F7F5F0" stroke="#F7F5F0" stroke-width="5" stroke-linejoin="round"/></svg><span id="wordmark">Channel<span>Recipe</span></span></a>"""

_HEADER = """<header class="cr-header">
<div class="cr-topline">""" + _BRAND + """<span class="cr-local">Local studio</span>
<nav class="cr-account-nav" aria-label="ChannelRecipe account">
<a href="https://channelrecipe.com/app#local-studio" target="_blank" rel="noopener">Your CR account ↗</a>
<a href="/cr-style-clone">Learn a style</a>
<a class="cr-setup-link" href="/cr-setup">Setup &amp; accounts</a></nav></div>
<div class="cr-intro"><span class="cr-eyebrow">YOUR CHANNEL. YOUR CREATIVE WORKFLOW.</span>
<h1>Make your next <em>great video.</em></h1>
<p>Choose a style, add your idea, and bring it to life with your connected accounts.</p></div>
</header>"""

_THEME = r"""<style id="cr-studio-theme">
:root{
 color-scheme:dark;--bg:#0F1222;--bg2:#14182A;--bg3:#1E2338;--panel:#171B2E;--panel2:#1E2338;
 --line:#262B45;--line2:#454A69;--ink:#EDEEF5;--text:#EDEEF5;--dim:#A7ACC4;--dimmer:#959CB9;
 --accent:#6D5AE0;--accent-hover:#5A48C9;--accent-soft:#23213F;--accent-ink:#C5BAFF;
 --amber:#A99AFF;--amber2:#6D5AE0;--amber-dim:#23213F;--ok:#36D69A;--bad:#FF9298;--r:14px;
 --font-body:"General Sans","Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
 --font-display:"Cabinet Grotesk","General Sans","Inter",system-ui,sans-serif;
}
body{background:radial-gradient(ellipse 850px 430px at 70% 0,rgba(109,90,224,.10),transparent 75%),var(--bg);color:var(--ink);font-family:var(--font-body);line-height:1.6}
button,input,select,textarea{font-family:var(--font-body)}
button,a,input,select,textarea,summary{touch-action:manipulation}
:is(button,a,input,select,textarea,summary):focus-visible{outline:2px solid var(--accent-ink);outline-offset:3px}
input,textarea,select{accent-color:var(--accent)}
input::placeholder,textarea::placeholder{color:var(--dimmer);opacity:1}
[hidden],.hidden{display:none!important}
::selection{background:rgba(109,90,224,.4)}
/* This strip is injected by older launchers; the complete navigation is below. */
html[data-cr-screen] body>a[href="/cr-setup"]:first-child{display:none!important}
.cr-header{padding:0;text-align:left}.cr-topline{display:flex;align-items:center;gap:14px;min-height:82px;border-bottom:1px solid var(--line)}
.cr-brand{display:inline-flex;align-items:center;gap:8px;color:var(--ink);text-decoration:none;flex-shrink:0}
.cr-brand #wordmark{font:800 22px/1 var(--font-display);letter-spacing:-.04em}.cr-brand #wordmark>span{color:#A99AFF}
.cr-brand svg{display:block;flex-shrink:0}.cr-local{font-size:11px;letter-spacing:.035em;background:var(--accent-soft);color:var(--accent-ink);padding:4px 9px;border:1px solid #39345B;border-radius:6px;white-space:nowrap}
.cr-account-nav{margin-left:auto;display:flex;align-items:center;gap:22px;font-size:13px}
.cr-account-nav a{text-decoration:none;color:var(--dim)}.cr-account-nav a:hover{color:var(--ink)}
.cr-account-nav .cr-setup-link{padding:8px 12px;border:1px solid var(--line2);border-radius:8px;color:var(--ink);background:var(--panel)}
.cr-intro{padding:38px 0 6px}.cr-eyebrow{color:var(--accent-ink);font-size:10px;font-weight:600;letter-spacing:.14em}
.cr-intro h1{font:800 clamp(30px,4vw,43px)/1.12 var(--font-display);letter-spacing:-.045em;margin:13px 0}
.cr-intro h1 em{font-style:normal;color:#B6A8FF}.cr-intro p{font-size:14px;color:var(--dim);margin:0;max-width:650px}
html[data-cr-screen="production"] .wrap{max-width:1080px;padding:0 32px 64px}
html[data-cr-screen="production"] .maintabs{gap:12px;margin-top:25px}
.maintabs button{min-height:108px;padding:18px;border-radius:12px;background:var(--panel)}
.maintabs button b{font:700 17px/1.3 var(--font-display);letter-spacing:-.01em}.maintabs button small{font-size:12px;line-height:1.55;margin-top:8px}
.maintabs button.on{background:linear-gradient(140deg,#292442,#1C1C35);border-color:var(--accent);box-shadow:inset 0 2px 0 #A99AFF}
.maintabs button.on b{color:#E2DBFF}
.help{padding:13px 16px;background:rgba(23,27,46,.65);border-radius:10px;font-size:12px;margin-top:16px}
.help a{color:var(--accent-ink);text-underline-offset:3px}
.step{margin-top:20px;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:22px}
.step-head{gap:10px;flex-wrap:wrap;margin-bottom:16px}.step h2{font:700 18px/1.3 var(--font-display);letter-spacing:-.02em}
.num{background:var(--accent-soft);color:var(--accent-ink);border-color:#4D427D;border-radius:7px;width:25px;height:25px;flex-basis:25px;transform:none}
.step .hint{font-size:12px;color:var(--dim)}#costHint{flex:1 1 300px;max-width:560px;line-height:1.55;text-align:right}
.chpick,.vobar,.avbox,.vodrop,.prods .prod{background:var(--bg2);border-color:var(--line2)}
.chpick{padding:12px;gap:18px}.cp-meta b{font-family:var(--font-display);font-size:19px}.cp-line{white-space:normal}
.cp-open,.vobar .chg,.chbar .chg{border-radius:8px}.cp-open{border-color:#504876;color:#D5CAFF}
.chips{gap:9px}.chip{border-radius:8px;padding:9px 19px;background:var(--bg2)}
.chip[aria-pressed=true]{background:var(--accent-soft);border-color:var(--accent);color:#E2DBFF}
textarea#title{min-height:112px;border-radius:10px;background:var(--bg2);border-color:var(--line2)}
.vonote,.avnote,.feat-note,.opt small{opacity:1;color:var(--dim);line-height:1.65}
.go,.btn.go{background:var(--accent);color:#fff;box-shadow:0 8px 24px rgba(109,90,224,.2)}
#go{margin-top:22px;padding:16px;border-radius:11px}.go:hover:not(:disabled){background:var(--accent-hover);filter:none}
details.opts{margin-top:16px;border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:3px 20px}
details.opts>summary{display:flex;padding:14px 0;font-size:14px;font-weight:600;color:var(--ink)}
details.opts>.optbox{border:0;border-radius:0;padding:4px 0 20px;margin-top:0;background:transparent}
.feats{gap:10px}.feat{background:var(--bg2)}.feat.on{background:#21213B;border-color:#5D4E99}.feat input[type=checkbox]:checked,.opt input[type=checkbox]:checked{background:var(--accent);border-color:var(--accent)}
.sl,.lb,.mbox{background:var(--bg2);border-color:var(--line2)}
.sl-ai{background:linear-gradient(120deg,#2B2449,#1A1C32);border-color:#5D4E99}.sl-ai-txt p,.ch .tagline{color:var(--dim)}
.sl-use,.sl-cur,.vrow .use,.vobar .chg,.chbar .chg{background:var(--accent);color:white}
.sl-chip[aria-pressed=true],.sl-chip[aria-pressed=true] small{color:#D5CAFF}
.wrap>.lib{margin-top:36px;padding-top:24px}.lib h2{font:700 23px/1.3 var(--font-display)}
.card{background:var(--panel);border-radius:12px}.card .meta b{font-size:14px}.card .meta span{font-size:12px}
.lib-empty{padding:30px 20px;border:1px dashed var(--line2);border-radius:12px;text-align:center;background:var(--panel)}
.run{border-color:#5D4E99}.run-top,.stages{color:var(--ink)}.log{color:var(--dim)}
.cr-studio-footer{margin-top:36px;border-top:1px solid var(--line);padding-top:24px;color:var(--dim)}
.cr-studio-footer .updates{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;gap:20px;align-items:center}
.cr-studio-footer .updates b{color:var(--ink)}.cr-studio-footer .updates a{background:var(--accent-soft);color:var(--accent-ink);border-color:#4D427D;border-radius:8px}
.cr-studio-footer .foot-row{font-size:12px;gap:16px;flex-wrap:wrap}
.cr-3d-panel .dl{padding:8px 12px;border:1px solid #504876;border-radius:8px;background:var(--accent-soft);color:var(--accent-ink);font:inherit}
.cr-3d-panel .dl:disabled{opacity:.45}
/* Preserve the native editor's timeline and sizing logic. */
html[data-cr-screen="editor"] body{font-size:13.5px}
.cr-editor-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:10px 16px;background:var(--bg);border-bottom:1px solid var(--line);flex:0 0 auto}
.cr-editor-header .cr-brand #wordmark{font-size:18px}.cr-editor-header .cr-brand svg{width:26px;height:26px}
.cr-editor-header .cr-account-nav{gap:14px}.cr-editor-header .cr-local{font-size:10px}
html[data-cr-screen="editor"] .top{background:var(--panel);gap:8px;flex-wrap:wrap}
html[data-cr-screen="editor"] .side{background:var(--panel)}
@media(max-width:760px){
 html[data-cr-screen="production"] .wrap{padding:0 18px 40px}.cr-topline{flex-wrap:wrap;gap:10px;padding:18px 0;min-height:0}
 .cr-account-nav{gap:14px}.cr-account-nav>a[href^="https://"]{display:none}.cr-local{margin-left:auto}.cr-topline .cr-account-nav{margin-left:0}
 .cr-intro{padding-top:28px}.maintabs button{min-height:0;padding:14px}.maintabs button small{margin-top:5px}
 .step{padding:18px}.step .hint{font-size:11.5px}#costHint{flex-basis:100%;text-align:left;margin-left:35px;max-width:none}
 .cr-editor-header{flex-wrap:wrap;padding:10px}.cr-editor-header .cr-local{margin-left:0}
 html[data-cr-screen="editor"] body{overflow:auto;min-height:100dvh;height:auto}
 html[data-cr-screen="editor"] .main{flex-direction:column;min-height:auto;flex:none}
 html[data-cr-screen="editor"] .stage{min-height:300px;height:45vh;flex:none}
 html[data-cr-screen="editor"] .side{width:100%;min-width:0;max-width:none;min-height:420px;flex:none;border-left:0;border-top:1px solid var(--line)}
 html[data-cr-screen="editor"] .tl{min-height:240px;flex-shrink:0}
 html[data-cr-screen="editor"] .transport{flex-wrap:wrap}
}
@media(max-width:480px){
 .cr-brand #wordmark{font-size:20px}.cr-brand svg{width:28px;height:28px}.cr-local{font-size:10px}.cr-account-nav{width:100%}
 .cr-account-nav>a{white-space:nowrap}.cr-account-nav .cr-setup-link{flex:1;text-align:center}.cr-intro h1{font-size:32px}.cr-intro p{font-size:13px}
 .step{padding:16px}.step-head .chtoggle{margin-left:auto}.chpick{gap:10px}.cp-meta b{font-size:16px}.feats{grid-template-columns:1fr}
 .cr-studio-footer .updates{flex-direction:column;align-items:flex-start}.cr-editor-header .cr-account-nav{width:auto;margin-left:auto}
 .cr-3d-top{align-items:flex-start}.cr-3d-panel{padding:16px}.cr-3d-actions button{width:100%}
}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important}}
</style>"""

_BRANDING = r"""<script id="cr-studio-branding">
(()=>{
 'use strict';
 const brand=text=>text.replace(/\bFrontier Production\b/gi,'Video production').replace(/\bFrontier 3D\b/gi,'3D studio').replace(/\bFrontier\b/gi,'ChannelRecipe');
 // Restrict dynamic copy changes to interface labels. Video titles, prompts,
 // transcripts, logs, chat messages and form values remain the creator's text.
 const chrome='#mainTabs,#slTitle,#slSub,#chHint,#voWarn,#voErr,#voBar,#apiWarn,#err,#cannotWhy,.vonote,.mix-h,.toast';
 function labels(root){
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  while(walker.nextNode()){
   const node=walker.currentNode;
   if(node.parentElement?.closest('script,style,textarea,input,pre,code,[contenteditable]'))continue;
   const value=brand(node.nodeValue);if(value!==node.nodeValue)node.nodeValue=value;
  }
 }
 function title(){
  const current=document.title;
  const next=current.replace(/Frontier(?:\s*)$/i,'ChannelRecipe studio');
  if(next!==current)document.title=next;
 }
 function refresh(){document.querySelectorAll(chrome).forEach(labels);title();}
 function install(){
  refresh();
  const observer=new MutationObserver(records=>{
   for(const record of records){
    const element=record.target.nodeType===Node.ELEMENT_NODE?record.target:record.target.parentElement;
    if(element?.closest(chrome)){labels(element.closest(chrome));continue;}
    for(const added of record.addedNodes){
     if(added.nodeType!==Node.ELEMENT_NODE)continue;
     if(added.matches(chrome))labels(added);
     added.querySelectorAll(chrome).forEach(labels);
    }
   }
   title();
  });
  observer.observe(document.body,{subtree:true,childList:true,characterData:true});
  const heading=document.querySelector('title');if(heading)observer.observe(heading,{childList:true,characterData:true,subtree:true});
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
</script>"""


def _brand_text(value: str) -> str:
    value = re.sub(r"\bFrontier Production\b", "Video production", value, flags=re.I)
    value = re.sub(r"\bFrontier 3D\b", "3D studio", value, flags=re.I)
    return re.sub(r"\bFrontier\b", "ChannelRecipe", value, flags=re.I)


def _theme_css(value: str) -> str:
    # Adapt presentation literals only; renderer palettes inside JS are intact.
    value = re.sub(r"(rgba?\()255\s*,\s*(?:176\s*,\s*32|150\s*,\s*20|138\s*,\s*0|180\s*,\s*60|190\s*,\s*90)(?=[,)])",
                   r"\g<1>109,90,224", value, flags=re.I)
    colors = {"#ffd189": "#C5BAFF", "#1a1206": "#FFFFFF", "#1a1405": "#FFFFFF"}
    return re.sub(r"#[0-9a-f]{6}\b", lambda m: colors.get(m.group().lower(), m.group()), value, flags=re.I)


class _StudioMarkup(HTMLParser):
    """Keep native scripts and form values byte-for-byte while adapting chrome."""

    def __init__(self, screen: str):
        super().__init__(convert_charrefs=False)
        self.screen = screen
        self.output: list[str] = []
        self.stack: list[tuple[str, bool]] = []

    @property
    def protected(self):
        return bool(self.stack and self.stack[-1][1])

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        if tag == "html":
            raw = raw[:-1] + f' data-cr-screen="{self.screen}">'
        if tag not in {"script", "style"} and not self.protected:
            # Never change href/src/action, option values, IDs or data attributes.
            raw = re.sub(r"(\s(?:title|alt|aria-label|placeholder)\s*=\s*)([\"'])(.*?)\2",
                         lambda m: m[1] + m[2] + _brand_text(m[3]) + m[2], raw, flags=re.I | re.S)
        self.output.append(raw)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            attributes = dict(attrs)
            user_content = attributes.get("id") in {"cards", "title", "runTitle", "log", "msgs"}
            self.stack.append((tag, self.protected or user_content or tag in {"script", "style", "textarea", "pre", "code"}))

    def handle_startendtag(self, tag, attrs):
        depth = len(self.stack)
        self.handle_starttag(tag, attrs)
        del self.stack[depth:]

    def handle_endtag(self, tag):
        self.output.append(f"</{tag}>")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.protected:
            self.output.append(_theme_css(data) if self.stack[-1][0] == "style" else data)
        else:
            self.output.append(_brand_text(data))

    def handle_entityref(self, name):
        self.output.append(f"&{name};")

    def handle_charref(self, name):
        self.output.append(f"&#{name};")

    def handle_comment(self, data):
        self.output.append("<!--" + data + "-->")

    def handle_decl(self, decl):
        self.output.append("<!" + decl + ">")

    def handle_pi(self, data):
        self.output.append("<?" + data + ">")


_FOOTER = """<footer class="cr-studio-footer">
  <div class="updates"><span class="txt"><b>ChannelRecipe studio</b>
    Get the current studio and workflow from your ChannelRecipe account.
    Planning uses your Claude plan through Claude Code and its limits. Generated media uses your
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
    """Brand native studio screens and display sanitized account metadata.

    ``narration`` accepts provider, label, model, model_label, voice and connected.
    Pass None when the individual original voice settings are selected. Other
    pages (including product previews with their own prices) are left untouched.
    """
    production = bool(re.search(r'\bid=["\']voBar["\']', text)
                      and re.search(r'\bid=["\']go["\']', text))
    editor = all(re.search(r'\bid=["\']' + element + r'["\']', text)
                 for element in ("laneV", "rulerCv", "render", "msg"))
    if not (production or editor):
        return text
    if 'id="cr-studio-branding"' in text or 'id="cr-account-ui"' in text:
        return text
    screen = "production" if production else "editor"
    markup = _StudioMarkup(screen)
    markup.feed(text)
    markup.close()
    text = "".join(markup.output)
    title = "ChannelRecipe studio" if production else "Timeline editor · ChannelRecipe studio"
    if re.search(r"<title\b", text, flags=re.I):
        text = re.sub(r"<title\b[^>]*>.*?</title>", lambda _: "<title>" + title + "</title>", text, count=1, flags=re.I | re.S)
    else:
        text = re.sub(r"</head\s*>", lambda m: "<title>" + title + "</title>" + m[0], text, count=1, flags=re.I)
    text = re.sub(r"</head\s*>", lambda m: _THEME + m[0], text, count=1, flags=re.I)
    if production:
        # The brand-only header has no production handlers. Preserve its two
        # existing IDs so the runtime's DOM contract remains complete.
        text, header_count = re.subn(r"<header\b[^>]*>(?:(?!</header>).)*?\bid=[\"']brandmark[\"'].*?</header>",
                                     lambda _: _HEADER, text, count=1, flags=re.I | re.S)
        if not header_count:
            text = re.sub(r"<body\b[^>]*>", lambda m: m[0] + _HEADER, text, count=1, flags=re.I)
        text = re.sub(r'<div class="help">.*?</div>',
                      '<div class="help"><b>Start with your connections.</b> <a href="/cr-setup">Setup &amp; accounts</a> checks your tools, brain and media accounts. Then choose a style below.</div>',
                      text, count=1, flags=re.S)
    else:
        editor_header = '<div class="cr-editor-header">' + _BRAND + '<span class="cr-local">Timeline editor</span><nav class="cr-account-nav" aria-label="ChannelRecipe account"><a class="cr-setup-link" href="/cr-setup">Setup &amp; accounts</a></nav></div>'
        text = re.sub(r"<body\b[^>]*>", lambda m: m[0] + editor_header, text, count=1, flags=re.I)
    if production and isinstance(preset,Mapping) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",str(preset.get("style", ""))):
        style=str(preset["style"])
        tab=preset.get("tab") if preset.get("tab") in {"production","avatar","3d"} else "production"
        selection={"style":style,"tab":tab}
        if isinstance(preset.get("features"), Mapping):
            selection["features"]={k:v for k,v in preset["features"].items() if re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",str(k)) and isinstance(v,bool)}
        state=json.dumps(selection,ensure_ascii=True).replace("<","\\u003c")
        # Run before Frontier reads its own remembered controls. This carries
        # the creator's checked selection, without changing preset defaults.
        carry='<script id="cr-preset-selection">(()=>{const s='+state+';try{localStorage.setItem("fr.tab",s.tab);localStorage.setItem("fr.ch",s.style);localStorage.setItem("fr.ch."+s.tab,s.style);if(s.features)localStorage.setItem("fr.feat2."+s.style,JSON.stringify(s.features))}catch(e){}})();</script>'
        text=text.replace("</head>",carry+"</head>",1)
    text = re.sub(r"<footer\b[^>]*>.*?</footer>", lambda _: _FOOTER, text,
                  count=1, flags=re.IGNORECASE | re.DOTALL)
    payload = json.dumps(_narration_payload(narration), ensure_ascii=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    adapter = _BRANDING
    if production:
        adapter += _ADAPTER.replace("__CR_NARRATION__", payload) + _THREED_REVIEW
    return re.sub(r"</body\s*>", lambda match: adapter + match.group(), text,
                  count=1, flags=re.IGNORECASE)
