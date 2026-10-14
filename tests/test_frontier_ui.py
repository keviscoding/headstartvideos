"""Presentation follows the active account without changing native production."""
import json
import ast
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


@pytest.mark.parametrize("ready", [True, False])
def test_saved_claude_limit_is_advisory_and_setup_still_gates_create(page, ready):
    page, errors = page
    source = ast.parse((Path(__file__).parents[1] / "local_runtime/cr_studio.py").read_text())
    guard = next(node.value.value for node in ast.walk(source)
                 if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "guard" for t in node.targets)
                 and isinstance(node.value, ast.Constant))
    status = {"readiness": {"ready": ready}, "brain_mode": "claudecode",
              "claude_code": {"allowance": {"blocked": False, "reported_limit": True,
                                             "message": "Earlier limit. You can create a new video now."}}}
    page.route("https://studio.fixture/cr-status", lambda route: route.fulfill(json=status))
    page.route("https://studio.fixture/", lambda route: route.fulfill(
        content_type="text/html", body='<html><body><button id="go">Create video</button>'+guard+'</body></html>'))
    page.goto("https://studio.fixture/")
    page.wait_for_function("document.getElementById('cr-generation-readiness').textContent !== 'Checking ChannelRecipe setup…'")
    assert page.locator("#go").is_disabled() is not ready
    if ready:
        assert "new video now" in page.locator("#cr-generation-readiness").inner_text()
        assert page.locator("#cr-generation-readiness a").count() == 0
    assert errors == []


def test_checked_preset_uses_original_controls_before_original_script():
    result=cr_ui.transform_html(UI,preset={"style":"3d","tab":"3d","features":{"threed":True,"youtube":False,"not_a_bool":"secret-value"}})
    assert result.index('id="cr-preset-selection"') < result.index("let VO_MODE")
    assert '"threed": true' in result and '"youtube": false' in result
    assert "not_a_bool" not in result and "secret-value" not in result
    assert 'localStorage.setItem("fr.feat2."+s.style' in result
    assert 'id="cr-preset-selection"' not in cr_ui.transform_html(UI,preset={"style":"</script><script>bad()","features":{}})


def test_style_navigation_keeps_native_feature_defaults():
    result = cr_ui.transform_html(UI, preset={"style": "learned-look", "tab": "production"})
    payload = re.search(r"const s=(.*?);try", result).group(1)
    assert json.loads(payload) == {"style": "learned-look", "tab": "production"}
    assert 'if(s.features)localStorage.setItem("fr.feat2."' in result
    assert 'href="/cr-style-clone"' in result


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
    for script_id in ("cr-account-ui", "cr-studio-branding"):
        script = re.search(r'<script id="' + script_id + r'">(.*?)</script>', html, re.S).group(1)
        filename = tmp_path / (script_id + ".js")
        filename.write_text(script)
        result = subprocess.run([node, "--check", str(filename)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


# Small independent contract fixture: no private studio source is checked in.
BRANDED_UI = """<!doctype html><html><head><title>Frontier</title>
<style>body{margin:0}.wrap{margin:auto}.hidden{display:none!important}
.maintabs{display:grid;grid-template-columns:repeat(3,1fr)}
.go{color:#1a1206}.chip[aria-pressed=true]{border-color:rgba(255,176,32,.55)}
@media(max-width:760px){.maintabs{grid-template-columns:1fr}}</style></head><body>
<div class="wrap"><header><a class="logo" href="/" aria-label="Frontier"><img id="brandmark" src="/brand/frontier.png" alt="Frontier"><span id="wordmark">Frontier</span></a></header>
<nav class="maintabs" id="mainTabs"><button data-t="production" class="on"><b>FRONTIER PRODUCTION</b><small>Documentaries and footage</small></button>
<button data-t="avatar"><b>AI AVATAR</b><small>Presenter videos</small></button><button data-t="3d"><b>FRONTIER 3D</b><small>Scenes rendered on your computer</small></button></nav>
<div class="help">Original onboarding</div>
<section class="step"><div class="step-head"><span class="num">1</span><h2>Channel style</h2><button id="chToggle">Compact</button></div><div id="channels"></div></section>
<section class="step"><div class="step-head"><span class="num">2</span><h2>Voice</h2></div><div id="voBar">Loading…</div><div id="voWarn" class="hidden"></div><input id="voFile" type="file" hidden></section>
<section class="step"><h2>Title</h2><textarea id="title">Life on the Frontier</textarea><input id="secret" type="password" value="Frontier-secret"><textarea id="o_script" placeholder="Frontier writes your script">The Frontier is a place.</textarea></section>
<button class="go" id="go" disabled>Create video</button>
<details class="opts" open><summary>Options</summary><div class="optbox"><input id="o_subs" type="checkbox" checked><select id="provider"><option value="original">Individual Frontier providers</option></select></div></details>
<section id="run" class="hidden"><b id="runTitle">Life on the Frontier</b><pre id="log">Frontier log untouched</pre></section>
<div id="err" class="hidden"></div><div id="styleLib" class="hidden"><b id="slTitle">Style library</b><span id="slSub"></span></div>
<div class="lib"><div id="cards"><div class="card"><div class="meta"><b>Frontier video title</b></div></div></div></div>
<footer>Frontier footer</footer></div>
<script>window.originalBrand='Frontier';window.originalEndpoint='/api/frontier';
document.querySelectorAll('#mainTabs button').forEach(button=>button.onclick=()=>{document.querySelectorAll('#mainTabs button').forEach(item=>item.classList.remove('on'));button.classList.add('on');window.selectedStudio=button.dataset.t;});</script>
</body></html>"""


EDITOR_UI = """<!doctype html><html><head><title>Timeline editor — Frontier</title></head><body>
<div class="top"><a href="/">Library</a><b id="title">A Frontier story</b><button id="render" disabled>Render video</button></div>
<div class="main"><textarea id="msg">Keep the Frontier title</textarea></div>
<div id="laneV"></div><canvas id="rulerCv"></canvas>
<script>window.editorBrand='Frontier';window.editSpec={slug:'frontier-story'};</script>
</body></html>"""


def test_studio_branding_retains_scripts_ids_values_and_state_contracts():
    result = cr_ui.transform_html(BRANDED_UI)
    for element_id in re.findall(r'\bid="([^"]+)"', BRANDED_UI):
        assert len(re.findall(r'\bid="' + re.escape(element_id) + '"', result)) == 1, element_id
    original_script = re.search(r"<script>(.*?)</script>", BRANDED_UI, re.S).group(1)
    assert original_script in result
    assert '<title>ChannelRecipe studio</title>' in result
    assert '/brand/frontier.png' not in result
    assert 'placeholder="ChannelRecipe writes your script"' in result
    assert '>The Frontier is a place.</textarea>' in result
    assert 'value="Frontier-secret"' in result
    assert '>Frontier log untouched</pre>' in result
    assert '>Frontier video title</b>' in result
    assert 'value="original"' in result
    assert 'rgba(109,90,224,.55)' in result
    assert 'color:#FFFFFF' in result
    assert '<button class="go" id="go" disabled>' in result
    assert '<input id="o_subs" type="checkbox" checked>' in result


def test_editor_branding_keeps_native_editor_contract_and_is_idempotent():
    result = cr_ui.transform_html(EDITOR_UI)
    assert 'data-cr-screen="editor"' in result
    assert '<title>Timeline editor · ChannelRecipe studio</title>' in result
    assert 'class="cr-editor-header"' in result
    assert '>A Frontier story</b>' in result
    assert 'id="cr-account-ui"' not in result
    assert 'id="cr-3d-review-ui"' not in result
    assert "window.editSpec={slug:'frontier-story'}" in result
    assert '>Keep the Frontier title</textarea>' in result
    assert cr_ui.transform_html(result) == result


def test_cr_theme_navigation_and_dynamic_errors_preserve_choices_in_browser(page):
    tab, _ = page
    tab.set_content(cr_ui.transform_html(BRANDED_UI))
    assert tab.title() == "ChannelRecipe studio"
    assert tab.locator("#mainTabs").inner_text().find("FRONTIER") == -1
    assert tab.locator(".cr-brand").inner_text() == "ChannelRecipe"
    assert tab.locator(".cr-setup-link").get_attribute("href") == "/cr-setup"
    assert tab.locator("#go").is_disabled()
    assert tab.locator("#o_subs").is_checked()
    assert tab.locator("#title").input_value() == "Life on the Frontier"
    assert tab.locator("#provider").input_value() == "original"
    tab.locator('[data-t="avatar"]').click()
    assert tab.evaluate("window.selectedStudio") == "avatar"
    assert tab.locator('[data-t="avatar"]').evaluate("el=>el.classList.contains('on')")
    tab.evaluate("""()=>{
      document.querySelector('#err').textContent='Frontier is not running.';
      document.querySelector('#err').classList.remove('hidden');
      document.querySelector('#slSub').innerHTML='<span>Frontier 3D styles</span>';
      document.querySelector('#cards .meta b').textContent='A new Frontier video';
      document.querySelector('#runTitle').textContent='Frontier documentary';
    }""")
    tab.wait_for_function("document.querySelector('#err').textContent==='ChannelRecipe is not running.'")
    assert tab.locator("#slSub").inner_text() == "3D studio styles"
    assert tab.locator("#cards .meta b").inner_text() == "A new Frontier video"
    assert tab.locator("#runTitle").text_content() == "Frontier documentary"
    assert tab.evaluate("window.originalBrand") == "Frontier"
    assert tab.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(15, 18, 34)"
    assert tab.locator(".step").first.evaluate("el=>getComputedStyle(el).backgroundColor") == "rgb(23, 27, 46)"
    tab.set_viewport_size({"width": 390, "height": 844})
    assert tab.locator(".cr-setup-link").is_visible()
    assert tab.locator("#go").is_visible()
    assert tab.evaluate("document.documentElement.scrollWidth<=window.innerWidth")


def test_editor_dynamic_title_and_edit_text_are_preserved_in_browser(page):
    tab, _ = page
    tab.set_content(cr_ui.transform_html(EDITOR_UI))
    tab.evaluate("document.title='A Frontier story — Frontier'")
    tab.wait_for_function("document.title==='A Frontier story — ChannelRecipe studio'")
    assert tab.locator("#msg").input_value() == "Keep the Frontier title"
    assert tab.locator("#render").is_disabled()
    assert tab.evaluate("window.editSpec") == {"slug": "frontier-story"}
    tab.set_viewport_size({"width": 390, "height": 844})
    assert tab.locator(".cr-setup-link").is_visible()


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
    control = {"defer_save": False, "fail_save": False, "recovery_projects": [], "preset_blockers": []}

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
        if request.method == "GET" and path == "/cr-workspace/catalog":
            return route.fulfill(json=catalog)
        if request.method == "GET" and path == "/cr-projects":
            return route.fulfill(json={"projects": control["recovery_projects"]})
        payload = request.post_data_json
        calls.append((path, payload))
        if path == "/cr-recover":
            return route.fulfill(json={"message": "One further stage attempt is allowed."})
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
            ready = state["readiness"]["ready"] and not control["preset_blockers"]
            return route.fulfill(json={"ready": ready,
                "checks": [{"label": "Original fixture preset", "ready": ready}],
                "blockers": [] if ready else control["preset_blockers"] or ["Verify the saved models first."],
                "features": payload["features"]})
        pytest.fail("Unexpected UI fixture request: " + request.method + " " + path)

    tab.route("**/*", fixture)
    tab.goto("http://setup-ui-fixture.invalid/cr-setup")
    tab.wait_for_function("document.getElementById('claude-verify').disabled===false")
    tab.select_option("#preflight-style", "original fixture")
    yield {"page": tab, "state": state, "calls": calls, "control": control, "catalog": catalog,
           "pending": pending, "finish_save": finish_save}
    for route, _ in pending:
        route.abort()


def test_setup_displays_channelrecipe_recipe_names_without_changing_engine_identity(setup_page):
    data = setup_page
    tab = data["page"]
    data["catalog"]["channels"][0]["label"] = "Illustrated Explainer"
    data["catalog"]["channels"].append({"name": "cr_clone_custom_fixture", "label": "My specific 2D style",
        "custom": True, "features": {"offered": [], "on": []}})
    tab.evaluate("loadStyles()")
    assert tab.locator('#preflight-style option[value="original fixture"]').inner_text() == "Illustrated Explainer"
    assert tab.locator('#preflight-style option[value="cr_clone_custom_fixture"]').inner_text() == "My specific 2D style"
    tab.select_option("#preflight-style", "original fixture")
    tab.locator("#preflight-check").click()
    tab.wait_for_function("document.getElementById('launch').getAttribute('href').startsWith('/?cr-preset=')")
    assert data["calls"] == [("/cr-preflight", {"style": "original fixture", "features": {"stock": True}})]
    assert tab.locator("#launch").get_attribute("href") == "/?cr-preset=original%20fixture"
    links = tab.locator('nav[aria-label="Workspace"] .workspace-links a')
    assert links.all_text_contents() == ["Create", "Projects", "Style library", "Accounts"]
    assert [links.nth(index).get_attribute("href") for index in range(4)] == [
        "/#create", "/#projects", "/#styles", "/cr-setup"]


@pytest.mark.parametrize("field,choice", [("claude-general", "opus-fixture"),
                                         ("claude-visual", "sonnet-fixture")])
def test_unsaved_either_claude_model_blocks_verify_preflight_and_launch(setup_page, field, choice):
    tab = setup_page["page"]
    tab.locator("#preflight-check").click()
    tab.wait_for_function("document.getElementById('launch').getAttribute('href').startsWith('/?cr-preset=')")
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


def test_saving_retains_displayed_choices_then_requires_verification_before_studio_entry(setup_page):
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
    assert tab.locator("#launch").get_attribute("aria-disabled") == "false"
    assert tab.locator("#launch").get_attribute("href") == "/"
    tab.locator("#preflight-check").click()
    tab.wait_for_function("document.getElementById('launch').getAttribute('href').startsWith('/?cr-preset=')")
    assert tab.locator("#launch").get_attribute("href") == "/?cr-preset=original%20fixture"
    assert setup_page["calls"][-1] == ("/cr-preflight", {"style": "original fixture", "features": {"stock": True}})
    assert [payload["action"] for path, payload in setup_page["calls"] if path == "/cr-claude/setup"] == ["select", "verify"]


def test_completed_basic_setup_opens_studio_without_preset_or_media_requests(setup_page):
    tab = setup_page["page"]
    tab.select_option("#preflight-style", "")
    assert tab.locator("#launch").get_attribute("aria-disabled") == "false"
    assert tab.locator("#launch").get_attribute("href") == "/"
    assert tab.locator("#launch").get_attribute("tabindex") == "0"
    assert tab.locator("#readiness-title").inner_text() == "Your studio is ready"
    assert "before starting" in tab.locator("#readiness-message").inner_text()
    assert setup_page["calls"] == []


def test_missing_preset_service_does_not_block_studio_entry_or_start_generation(setup_page):
    tab = setup_page["page"]
    setup_page["control"]["preset_blockers"] = ["Connect stock footage for this preset."]
    tab.locator("#preflight-check").click()
    tab.wait_for_function("document.getElementById('readiness-message').textContent.includes('still needs')")
    assert tab.locator("#launch").get_attribute("aria-disabled") == "false"
    assert tab.locator("#launch").get_attribute("href") == "/"
    assert "before generation" in tab.locator("#readiness-blockers").inner_text()
    assert "Connect stock footage" in tab.locator("#readiness-blockers").inner_text()
    assert setup_page["calls"] == [("/cr-preflight", {"style": "original fixture", "features": {"stock": True}})]


@pytest.mark.parametrize("blocker", ["Connect CR tools.", "Verify your selected models.", "Install FFmpeg with captions."])
def test_incomplete_basic_setup_still_blocks_studio_entry(setup_page, blocker):
    setup_page["state"]["readiness"].update(ready=False, blockers=[blocker])
    tab = setup_page["page"]
    tab.evaluate("refresh()")
    assert tab.locator("#launch").get_attribute("aria-disabled") == "true"
    assert tab.locator("#launch").get_attribute("tabindex") == "-1"
    assert blocker in tab.locator("#readiness-blockers").inner_text()
    assert not setup_page["calls"]


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


@pytest.mark.parametrize("purpose,button,approved,guidance", [
    ("video", "Allow one video-stage attempt", "Approved. Resume this video in the studio", "reused from cache"),
    ("setup", "Allow one model-check attempt", "Approved. Repeat model verification", "Approval does not start verification"),
    (None, "Allow one stage attempt", "Approved. Return to the original controls", "original controls that started this work"),
])
def test_recovery_approval_guidance_matches_work_purpose_without_generation(setup_page, purpose, button, approved, guidance):
    data = setup_page
    tab = data["page"]
    data["control"]["recovery_projects"] = [{"project_id": "saved-project", "title": "Saved work", "purpose": purpose,
        "stages": [{"stage_id": "saved-stage", "kind": "text", "status": "failed", "can_retry": True}]}]
    tab.locator("#load-recovery").click()
    tab.wait_for_function("document.querySelector('#recovery-projects button')!==null")
    assert guidance in tab.locator("#recovery-projects").inner_text()
    approve = tab.locator("#recovery-projects button")
    assert approve.inner_text() == button
    assert approve.is_disabled()
    assert not any(path == "/cr-recover" for path, _ in data["calls"])
    tab.locator("#recovery-projects input").check()
    assert not any(path == "/cr-recover" for path, _ in data["calls"])
    approve.click()
    tab.wait_for_function("document.querySelector('#recovery-projects button').textContent.startsWith('Approved')")
    assert approve.inner_text() == approved and approve.is_disabled()
    assert tab.locator("#recovery-projects input").is_disabled()
    assert [(path, body) for path, body in data["calls"] if path == "/cr-recover"] == [
        ("/cr-recover", {"project_id": "saved-project", "stage_id": "saved-stage", "acknowledge_costs": True})]
    assert not any(path in {"/api/run", "/cr-claude/setup"} for path, _ in data["calls"])


def test_creative_recovery_routes_to_original_controls_without_old_stage_replay(setup_page):
    data = setup_page
    data["control"]["recovery_projects"] = [{"project_id": "saved-creative", "title": "Stopped 3D repair", "purpose": "creative",
        "stages": [{"stage_id": "stopped-stage", "kind": "vision", "status": "failed", "can_retry": True}]}]
    tab = data["page"]
    tab.locator("#load-recovery").click()
    tab.wait_for_function("document.querySelector('#recovery-projects a')!==null")
    assert "will not replay" in tab.locator("#recovery-projects").inner_text()
    assert "explicitly start a new action" in tab.locator("#recovery-projects").inner_text()
    assert "may repeat Claude calls" in tab.locator("#recovery-projects").inner_text()
    assert "resume this video" not in tab.locator("#recovery-projects").inner_text()
    assert tab.locator("#recovery-projects button").count() == 0
    assert tab.locator("#recovery-projects a").get_attribute("href") == "/"
    assert not any(path == "/cr-recover" for path, _ in data["calls"])
