"""Give the original style learner facts about malformed browser text markup.

No generated kit, palette, source renderer or verdict is rewritten here. The
same browser check and original Claude repair/re-render loop receive additional
DOM evidence when unescaped quotes have split a style attribute into attributes.
"""
from __future__ import annotations

import functools
import importlib
from types import FunctionType


KIND = "kit_html_style"

# This is evaluated by the learner in the existing Chromium page. It only reads
# DOM/style/layout; opacity, colours, fonts and markup are never changed. Narrow
# malformed attribute signatures avoid interpreting legitimate dark typography,
# arbitrary palettes or valid quoted CSS as an error.
TEXT_CONTRACT_JS = r"""(()=>{
 const out=[], seen=new Set(), root=document.getElementById('scene');
 if(!root)return out;
 for(const el of root.querySelectorAll('[style]')){
  const attrs=[...el.attributes].filter(a=>a.name!=='style'&&
   (/['"]/.test(a.name)||/;[a-z-]+\s*:/i.test(a.name)));
  if(!attrs.length||!el.textContent.trim())continue;
  let op=1, node=el;
  while(node&&node!==document.body){const cs=getComputedStyle(node);
   if(cs.display==='none'||cs.visibility==='hidden'){op=0;break;}
   op*=parseFloat(cs.opacity||'1');node=node.parentElement;}
  const box=el.getBoundingClientRect();
  if(op<0.3||box.width<3||box.height<3||box.right<=0||box.bottom<=0||
   box.left>=innerWidth||box.top>=innerHeight)continue;
  const text=el.textContent.trim().slice(0,80), signature=text+'|'+attrs.map(a=>a.name).join('|');
  if(seen.has(signature))continue;seen.add(signature);
  const cs=getComputedStyle(el);
  out.push({kind:'kit_html_style',text,
   attributes:attrs.slice(0,3).map(a=>({name:a.name.slice(0,220),value:a.value.slice(0,100)})),
   style:(el.getAttribute('style')||'').slice(-260),
   color:cs.color,font_family:cs.fontFamily,
   box:[box.left|0,box.top|0,box.right|0,box.bottom|0]});
  if(out.length>=8)break;
 }
 return out;
})()"""


CONTRACT_NOTE = (
    "\nChannelRecipe browser text contract: the HTML helper parses markup with "
    "innerHTML. CSS font-family quotes inside a quoted HTML style attribute must "
    "be HTML-escaped, or assign the CSS through the element's style API after "
    "creating it. JavaScript string escaping alone does not escape HTML. When "
    "a style attribute closes early, later CSS becomes bogus HTML attributes "
    "and the intended font/colour is lost. Preserve the designed typography and "
    "palette; correct markup encoding rather than masking the error with global "
    "CSS or a different palette. Browser DOM diagnostics report actual malformed "
    "attributes; fix their construction and re-render through the ordinary review."
)


def browser_expression(original):
    """Keep original overflow evidence and add read-only parser diagnostics."""
    return "(()=>{const original=(" + original + ");const contract=(" + TEXT_CONTRACT_JS + ");return [...contract,...original];})()"


def add_feedback(result):
    """Expose actual DOM failures to the original repair and validity safeguards."""
    if not isinstance(result, dict) or result.get("browser") is not True:
        return result
    affected, rows = [], []
    for scene, report in (result.get("types") or {}).items():
        if not isinstance(report, dict):
            continue
        evidence = [row for row in report.get("text") or []
                    if isinstance(row, dict) and row.get("kind") == KIND
                    and isinstance(row.get("attributes"), list) and row["attributes"]]
        if not evidence:
            continue
        affected.append(scene)
        first = evidence[0]
        attrs = ", ".join(str(a.get("name") or "")[:220]
                          for a in first["attributes"][:3] if isinstance(a, dict))
        rows.append(f"{scene}: text {str(first.get('text') or '')[:80]!r}; parsed style "
                    f"{str(first.get('style') or '')[:260]!r}; stray attributes {attrs!r}; "
                    f"browser font {str(first.get('font_family') or '')[:100]!r}, "
                    f"colour {str(first.get('color') or '')[:80]!r}")
    if not rows:
        return result
    # One prioritized diagnostic reaches the original repair's first-16 problem
    # budget even when many scene types already have ordinary overflow warnings.
    message = ("kit_html_style: malformed inline CSS was parsed as HTML attributes. "
               "Fix the shared element/HTML construction, including font quote "
               "escaping, while retaining the designed colours and fonts; "
               "re-render every affected type. " + " | ".join(rows[:12]))
    result["problems"] = [message] + [p for p in result.get("problems") or []
                                     if not str(p).startswith("kit_html_style:")]
    result["bad"] = sorted(set(result.get("bad") or []) | set(affected))
    result["kit_contracts"] = {"malformed_style_types": affected}
    return result


def install(learner=None):
    """Patch callable inputs only; never alter the original module's source file."""
    if learner is None:
        try:
            learner = importlib.import_module("reflearn")
        except ImportError:
            return None
    if getattr(learner, "_cr_kit_contracts", None):
        return learner._cr_kit_contracts
    render = getattr(learner, "render_check", None)
    check = getattr(learner, "check", None)
    overflow = getattr(learner, "OVERFLOW_JS", None)
    if not isinstance(render, FunctionType) or not callable(check) or not isinstance(overflow, str):
        return None
    # Bind the exact private renderer function to an augmented text expression;
    # its existing browser, frame times, screenshots and blank/motion checks stay.
    bound = FunctionType(render.__code__, {**render.__globals__, "OVERFLOW_JS": browser_expression(overflow)},
                         render.__name__, render.__defaults__, render.__closure__)
    bound.__kwdefaults__ = render.__kwdefaults__
    functools.update_wrapper(bound, render)
    learner.render_check = bound
    @functools.wraps(check)
    def checked(*args, **kwargs):
        return add_feedback(check(*args, **kwargs))
    learner.check = checked
    if isinstance(getattr(learner, "CONTRACT", None), str):
        learner.CONTRACT += CONTRACT_NOTE
    learner._cr_kit_contracts = checked
    return checked
