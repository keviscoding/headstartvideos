"""Presentation follows the active account without changing native production."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_ui


UI = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<span id="costHint"></span><div class="cp-cost" title="about $0.08 with the free tier">$0.08</div>
<div id="voAi"><div id="voBar"></div><span id="voHint"></span>
<p id="voWarn">Add ELEVENLABS_API_KEY to .env</p><div id="voLib">Original voices</div>
<div class="vonote">This channel's own voice is saved.</div></div>
<div id="voOwn" class="hidden"><input id="own-recording" type="file"><p id="own-note">Use your own recording</p></div>
<button id="go">Create video</button>
<footer><a href="https://whop.com/frontierhq/frontier-access/">Get updates</a>
<a href="https://go.algrow.online/tool">Powered by Algrow</a></footer>
<script>
let VO_MODE='ai',VO_OPEN=true;
window.nativeDraws=0;
window.jobSpec={style:'original',features:{stock:true,ai_images:false}};
function updateEstimate(){document.getElementById('costHint').textContent='about $0.50; a free GEMINI_API_KEY makes it cheaper';}
function drawVoice(){
 window.nativeDraws++;
 document.getElementById('voAi').classList.toggle('hidden',VO_MODE!=='ai');
 document.getElementById('voOwn').classList.toggle('hidden',VO_MODE==='ai');
 if(VO_MODE==='own'){document.getElementById('own-note').textContent='Native recording ready';return 'own';}
 document.getElementById('voBar').innerHTML='<b>Original ElevenLabs voice</b><button id="voChg">Change original voice</button>';
 document.getElementById('voWarn').classList.remove('hidden');
 updateEstimate();return 'ai';
}
</script></body></html>"""


def narration(**updates):
    return {"provider": "kie", "label": "Kie.ai", "model": "google/voice-test",
            "model_label": "Selected Gemini voice model", "voice": "Kore", "connected": True,
            **updates}


def test_only_production_screen_is_adapted():
    product = '<html><body><div id="go">Buy</div><p>$20</p><footer>Product seller</footer></body></html>'
    assert cr_ui.transform_html(product, narration=narration()) == product
    result = cr_ui.transform_html(UI, narration=narration())
    assert 'id="cr-account-ui"' in result
    assert cr_ui.transform_html(result, narration=narration()) == result


def test_checked_preset_uses_original_controls_before_original_script():
    result=cr_ui.transform_html(UI,preset={"style":"3d","tab":"3d","features":{"threed":True,"youtube":False,"not_a_bool":"secret-value"}})
    assert result.index('id="cr-preset-selection"') < result.index("let VO_MODE")
    assert '"threed": true' in result and '"youtube": false' in result
    assert "not_a_bool" not in result and "secret-value" not in result
    assert 'localStorage.setItem("fr.feat2."+s.style' in result
    assert 'id="cr-preset-selection"' not in cr_ui.transform_html(UI,preset={"style":"</script><script>bad()","features":{}})


def test_old_vendor_update_links_are_replaced_with_cr_accounts_and_updates():
    result = cr_ui.transform_html(UI)
    assert "whop.com" not in result and "go.algrow.online" not in result
    assert "https://channelrecipe.com/app#local-studio" in result
    assert 'href="/cr-setup"' in result
    assert "actual charges are shown in those accounts" in result


def test_original_controls_and_submitted_choices_are_retained():
    result = cr_ui.transform_html(UI, narration=narration())
    original_script = re.search(r"<script>(.*?)</script>", UI, re.S).group(1)
    assert original_script in result
    assert '<input id="own-recording" type="file">' in result
    assert "window.jobSpec={style:'original',features:{stock:true,ai_images:false}}" in result
    assert 'narration&&typeof window.drawVoice' in result
    assert "original.apply(this,args)" in result


def test_only_display_metadata_is_serialized_and_html_cannot_escape_script():
    result = cr_ui.transform_html(UI, narration=narration(
        label='</script><script>window.stolen=true</script>', key="never-show-api-key",
        config={"key": "never-show-connect-key"}))
    payload = re.search(r"const narration=(.*?);\n", result).group(1)
    assert json.loads(payload)["label"] == '</script><script>window.stolen=true</script>'
    assert "\\u003c/script\\u003e" in payload and "</script>" not in payload
    assert "never-show-api-key" not in result and "never-show-connect-key" not in result
    assert set(json.loads(payload)) == {"provider", "label", "model", "model_label", "voice", "connected"}


@pytest.mark.parametrize("override", [None, {}, {"provider": "original"}, {"provider": "unknown"}])
def test_native_narration_selection_keeps_native_voice_controls(override):
    result = cr_ui.transform_html(UI, narration=override)
    assert "const narration=null;" in result


def test_js_parses_without_any_browser_or_provider_calls(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    html = cr_ui.transform_html(UI, narration=narration())
    script = re.search(r'<script id="cr-account-ui">(.*?)</script>', html, re.S).group(1)
    filename = tmp_path / "account-ui.js"
    filename.write_text(script)
    result = subprocess.run([node, "--check", str(filename)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error:
            pytest.skip("A local Playwright Chromium installation is unavailable")
        tab = browser.new_page()
        tab.route("**/*", lambda route: pytest.fail("UI fixture must not make network calls"))
        errors = []
        tab.on("pageerror", lambda error: errors.append(str(error)))
        yield tab, errors
        browser.close()
        assert not errors, errors


def test_override_voice_display_and_original_recording_flow_in_browser(page):
    tab, _ = page
    tab.set_content(cr_ui.transform_html(UI, narration=narration()))
    tab.evaluate("drawVoice()")
    assert tab.locator("#voBar b").inner_text() == "Kore"
    assert "Kie.ai · Selected Gemini voice model" in tab.locator("#voBar").inner_text()
    assert tab.locator("#voWarn").inner_text() == ""
    assert tab.locator("#voLib").evaluate("el=>el.classList.contains('hidden')")
    assert tab.locator("#cr-narration-change").get_attribute("href") == "/cr-setup"
    assert tab.locator("#voChg").count() == 0
    assert tab.evaluate("VO_OPEN") is False
    assert tab.evaluate("window.jobSpec") == {"style": "original", "features": {"stock": True, "ai_images": False}}
    tab.evaluate("VO_MODE='own';drawVoice()")
    assert tab.locator("#own-note").inner_text() == "Native recording ready"
    assert tab.locator("#voOwn").evaluate("el=>!el.classList.contains('hidden')")
    tab.evaluate("VO_MODE='ai';drawVoice()")
    assert tab.locator("#voBar b").inner_text() == "Kore"
    assert tab.evaluate("window.nativeDraws") == 3


def test_native_voice_stays_native_and_dynamic_estimates_are_removed_in_browser(page):
    tab, _ = page
    tab.set_content(cr_ui.transform_html(UI))
    tab.evaluate("drawVoice()")
    assert tab.locator("#voBar b").inner_text() == "Original ElevenLabs voice"
    assert tab.locator("#voChg").count() == 1
    assert "$" not in tab.locator("#costHint").inner_text()
    assert "free" not in tab.locator("#costHint").inner_text()
    assert tab.locator(".cp-cost").is_hidden()
    tab.evaluate("""()=>{const tile=document.createElement('div');tile.className='sl-pick';tile.title='Original look\\nabout $0.08 with a free Gemini tier';tile.textContent='Style';document.body.append(tile)}""")
    tab.wait_for_function("document.querySelector('.sl-pick').title==='Original look'")
    assert tab.locator(".sl-pick").get_attribute("title") == "Original look"
