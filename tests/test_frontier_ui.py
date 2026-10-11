"""Presentation follows the active account without changing native production."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

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


@pytest.fixture
def setup_page(page):
    """Only the public setup HTML runs; all account checks are fake responses."""
    tab, _ = page
    tab.unroute("**/*")
    html = (Path(__file__).parents[1] / "local_runtime" / "cr_setup.html").read_text()
    html = html.replace("__LOCAL_TOKEN__", "ui-fixture-local-token")
    models = [{"id": "sonnet-fixture", "label": "Sonnet fixture"},
              {"id": "opus-fixture", "label": "Opus fixture"}]
    state = {
        "python": True, "ffmpeg": True, "browser": True, "node": True,
        "connected": True, "costs_accepted": True, "brain_mode": "claudecode",
        "local_version": "ui-fixture", "model_accounts": {}, "providers": {},
        "account": {"version": "ui-fixture", "brain": {"connected": False}},
        "claude_code": {"installed": True, "authenticated": True, "mcp_connected": True,
                        "models": models, "general_model": "sonnet-fixture",
                        "visual_model": "opus-fixture", "models_saved": True,
                        "models_verified": True},
        "readiness": {"ready": True, "checks": [{"label": "Fixture account checks", "ready": True}], "blockers": []},
    }
    catalog = {"channels": [{"name": "original fixture", "label": "Original fixture",
                             "description": "The original preset's requirements",
                             "features": {"offered": ["stock"], "on": ["stock"]}}],
               "features": [{"key": "stock", "label": "Original stock footage"}]}
    calls, pending = [], []
    control = {"defer_save": False, "fail_save": False}

    def finish_save(route, payload):
        if control["fail_save"]:
            route.fulfill(status=409, json={"error": "Fixture model choices could not save"})
            return
        state["claude_code"].update(general_model=payload["general_model"],
                                     visual_model=payload["visual_model"],
                                     models_saved=True, models_verified=False)
        state["readiness"].update(ready=False, blockers=["Verify the saved models first."])
        route.fulfill(json={"message": "Saved fixture models; verify them next."})

    def fixture(route):
        request = route.request
        path = urlparse(request.url).path
        if request.method == "GET" and path == "/cr-setup":
            return route.fulfill(body=html, content_type="text/html")
        assert request.headers.get("x-cr-local-token") == "ui-fixture-local-token"
        if request.method == "GET" and path == "/cr-status":
            return route.fulfill(json=state)
        if request.method == "GET" and path == "/api/channels":
            return route.fulfill(json=catalog)
        payload = request.post_data_json
        calls.append((path, payload))
        if path == "/cr-claude/setup":
            action = payload["action"]
            if action == "select":
                if control["defer_save"]:
                    pending.append((route, payload))
                    return
                return finish_save(route, payload)
            if action == "verify":
                state["claude_code"]["models_verified"] = True
                state["readiness"].update(ready=True, blockers=[])
                return route.fulfill(json={"message": "Fixture model access verified."})
            assert action == "check"
            return route.fulfill(json={"message": "Read-only fixture checks complete."})
        if path == "/cr-preflight":
            ready = state["readiness"]["ready"]
            return route.fulfill(json={"ready": ready,
                "checks": [{"label": "Original fixture preset", "ready": ready}],
                "blockers": [] if ready else ["Verify the saved models first."],
                "features": payload["features"]})
        pytest.fail("Unexpected UI fixture request: " + request.method + " " + path)

    tab.route("**/*", fixture)
    tab.goto("http://setup-ui-fixture.invalid/cr-setup")
    tab.wait_for_function("document.getElementById('claude-verify').disabled===false")
    tab.select_option("#preflight-style", "original fixture")
    yield {"page": tab, "state": state, "calls": calls, "control": control,
           "pending": pending, "finish_save": finish_save}
    for route, _ in pending:
        route.abort()


@pytest.mark.parametrize("field,choice", [("claude-general", "opus-fixture"),
                                         ("claude-visual", "sonnet-fixture")])
def test_unsaved_either_claude_model_blocks_verify_preflight_and_launch(setup_page, field, choice):
    tab = setup_page["page"]
    tab.locator("#preflight-check").click()
    tab.wait_for_function("document.getElementById('launch').getAttribute('aria-disabled')==='false'")
    assert tab.locator("#launch").get_attribute("href") == "/?cr-preset=original%20fixture"
    previous_calls = len(setup_page["calls"])
    tab.select_option("#" + field, choice)
    assert tab.locator("#claude-verify").is_disabled()
    assert tab.locator("#preflight-check").is_disabled()
    assert tab.locator("#launch").get_attribute("aria-disabled") == "true"
    assert tab.locator("#launch").get_attribute("href") == "/"
    assert not tab.locator("#final-ready").evaluate("el=>el.classList.contains('ready')")
    assert "Unsaved choices" in tab.locator("#claude-status").inner_text()
    assert "Saved Claude profile" in tab.locator("#claude-status").inner_text()
    # The handler guards also prevent scripted/Enter submits from issuing checks.
    tab.evaluate("claudeAction('verify')")
    tab.evaluate("document.getElementById('preflight').requestSubmit()")
    assert len(setup_page["calls"]) == previous_calls
    assert "Save the displayed Claude model choices" in tab.locator("#notice").inner_text()


def test_saving_retains_displayed_choices_then_requires_verify_and_preset_check(setup_page):
    tab = setup_page["page"]
    setup_page["control"]["defer_save"] = True
    tab.select_option("#claude-general", "opus-fixture")
    tab.select_option("#claude-visual", "sonnet-fixture")
    tab.locator("#claude-select").click()
    tab.wait_for_function("document.getElementById('claude-general').disabled===true")
    assert tab.locator("#claude-general").input_value() == "opus-fixture"
    assert tab.locator("#claude-visual").input_value() == "sonnet-fixture"
    assert "Saving your displayed choices" in tab.locator("#claude-status").inner_text()
    assert tab.locator("#claude-verify").is_disabled()
    assert tab.locator("#preflight-check").is_disabled()
    assert tab.locator("#launch").get_attribute("aria-disabled") == "true"
    route, payload = setup_page["pending"].pop()
    assert payload == {"action": "select", "general_model": "opus-fixture", "visual_model": "sonnet-fixture"}
    setup_page["finish_save"](route, payload)
    tab.wait_for_function("document.getElementById('claude-verify').disabled===false")
    assert tab.locator("#claude-general").input_value() == "opus-fixture"
    assert tab.locator("#claude-visual").input_value() == "sonnet-fixture"
    assert "dropdowns match your saved profile" in tab.locator("#claude-status").inner_text()
    assert "Unsaved choices" not in tab.locator("#claude-status").inner_text()
    assert tab.locator("#launch").get_attribute("aria-disabled") == "true"
    tab.locator("#preflight-check").click()
    tab.wait_for_function("document.getElementById('readiness-message').textContent.includes('needs the items')")
    assert tab.locator("#launch").get_attribute("aria-disabled") == "true"
    tab.locator("#claude-verify").click()
    tab.wait_for_function("document.getElementById('notice').textContent.includes('model access verified')")
    tab.wait_for_function("document.getElementById('claude-verify').disabled===false")
    assert tab.locator("#launch").get_attribute("aria-disabled") == "true"
    tab.locator("#preflight-check").click()
    tab.wait_for_function("document.getElementById('launch').getAttribute('aria-disabled')==='false'")
    assert tab.locator("#launch").get_attribute("href") == "/?cr-preset=original%20fixture"
    assert setup_page["calls"][-1] == ("/cr-preflight", {"style": "original fixture", "features": {"stock": True}})
    assert [payload["action"] for path, payload in setup_page["calls"] if path == "/cr-claude/setup"] == ["select", "verify"]


def test_failed_save_and_read_only_refresh_keep_unsaved_choice_blocked(setup_page):
    tab = setup_page["page"]
    setup_page["control"]["fail_save"] = True
    tab.select_option("#claude-visual", "sonnet-fixture")
    tab.locator("#claude-select").click()
    tab.wait_for_function("document.getElementById('notice').textContent.includes('could not save')")
    assert tab.locator("#claude-visual").input_value() == "sonnet-fixture"
    assert tab.locator("#claude-verify").is_disabled()
    assert tab.locator("#preflight-check").is_disabled()
    assert "Unsaved choices" in tab.locator("#claude-status").inner_text()
    tab.locator("#claude-check").click()
    tab.wait_for_function("document.getElementById('notice').textContent.includes('Read-only fixture checks complete')")
    tab.wait_for_function("document.getElementById('claude-check').disabled===false")
    assert tab.locator("#claude-visual").input_value() == "sonnet-fixture"
    assert tab.locator("#claude-verify").is_disabled()
    assert tab.locator("#preflight-check").is_disabled()
    assert tab.locator("#launch").get_attribute("aria-disabled") == "true"
    assert setup_page["state"]["claude_code"]["visual_model"] == "opus-fixture"
    assert [payload["action"] for path, payload in setup_page["calls"] if path == "/cr-claude/setup"] == ["select", "check"]
