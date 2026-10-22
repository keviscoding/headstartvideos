"""Native Claude setup never sends a saved farm choice to an unavailable route."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from flask import Flask, request
import pytest

RUNTIME = Path(__file__).parents[1] / "local_runtime"
sys.path.insert(0, str(RUNTIME))
import cr_ui


# A small original-screen contract: polling updates the checkbox, instructions
# and layout; its existing user handler owns the remembered farm preference.
SCREEN = '''<!doctype html><html><head><style>.hidden{display:none}</style></head><body>
<textarea id="title">Saved title</textarea><button id="go">Create video</button>
<div id="farm" class="hidden"><label><input type="checkbox" id="f_on">Cloud render</label>
<small id="farmWhy"></small><div id="farmSetup">Original credentials</div>
<div id="farmBody"><button id="f_start">Start farm</button><button id="f_kill">Stop farm</button></div></div>
<div id="cards"><article id="existing-video">An existing completed video</article></div>
<script>
let FARM={ready:false};
const farmOn=()=>document.getElementById('f_on').checked&&FARM.ready;
function farmBody(){document.getElementById('farmBody').classList.toggle('hidden',!farmOn());document.getElementById('go').textContent=farmOn()?'Prepare it here, then render on the cloud':'Create video';}
async function farmPoll(){const result=await fetch('/api/farm').then(response=>response.json());FARM=result;document.getElementById('farm').classList.remove('hidden');document.getElementById('farmWhy').textContent=result.ready?'Close the laptop — a rented machine makes the video':'Connect the farm accounts';document.getElementById('farmSetup').textContent='Original credentials';document.getElementById('farmSetup').classList.toggle('hidden',result.ready);document.getElementById('f_on').disabled=!result.ready;if(!result.ready)document.getElementById('f_on').checked=false;farmBody();}
document.getElementById('f_on').checked=!!localStorage.getItem('fr.farm');
document.getElementById('f_on').onchange=()=>{localStorage.setItem('fr.farm',document.getElementById('f_on').checked?'1':'');farmBody();farmPoll();};
document.getElementById('go').onclick=()=>fetch(farmOn()?'/api/farm/queue':'/api/run',{method:'POST',body:'{}'});
farmPoll();
</script></body></html>'''


@pytest.fixture
def browser_page(tmp_path):
    api = pytest.importorskip("playwright.sync_api")
    (tmp_path / "cr_renderer_panel.html").write_text("")
    app = Flask(__name__)
    studio = SimpleNamespace(app=app)
    tree = ast.parse((RUNTIME / "cr_studio.py").read_text())
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "inject")
    function.decorator_list = []
    namespace = {"studio": studio, "HOME": tmp_path, "request": request, "json": json,
                 "cr_ui": cr_ui, "token": "fixture", "cr_media": SimpleNamespace(active=lambda _: False)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 str(RUNTIME / "cr_studio.py"), "exec"), namespace)
    app.after_request(namespace["inject"])
    app.add_url_rule("/", view_func=lambda: SCREEN)
    html = app.test_client().get("/").get_data(as_text=True)
    state = SimpleNamespace(mode="claudecode", selected=False, farm_ready=False, posts=[], ready=True,
                            cloud={"available": False, "why": "Studio Cloud isn't switched on yet."})
    with api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except api.Error:
            pytest.skip("A local Playwright Chromium installation is unavailable")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(route):
            req = route.request
            if req.url == "http://fixture.test/":
                return route.fulfill(status=200, content_type="text/html", body=html)
            if req.url == "http://fixture.test/cr-status":
                return route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "readiness": {"ready": state.ready}, "brain_mode": state.mode}))
            if req.url == "http://fixture.test/cr-cloud":
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(state.cloud))
            if req.url == "http://fixture.test/api/farm":
                return route.fulfill(status=200, content_type="application/json", body=json.dumps({"ready": state.farm_ready}))
            if req.method == "POST" and req.url in {"http://fixture.test/api/run", "http://fixture.test/api/farm/queue"}:
                state.posts.append(req.url.rsplit("/api/", 1)[1])
                return route.fulfill(status=200, content_type="application/json", body="{}")
            pytest.fail("Unexpected UI request: " + req.url)

        def open_page():
            if state.selected:
                page.add_init_script("localStorage.setItem('fr.farm','1')")
            page.route("**/*", route)
            page.goto("http://fixture.test/")
            page.wait_for_function("!document.getElementById('cr-generation-readiness').textContent.startsWith('Checking')")

        yield page, state, open_page
        browser.close()
        assert not errors


def test_fresh_native_setup_runs_locally_without_farm_credentials(browser_page):
    page, state, open_page = browser_page
    open_page()
    assert page.locator("#go").is_enabled()
    assert page.locator("#f_on").is_disabled() and not page.locator("#f_on").is_checked()
    assert page.locator("#farmSetup").is_hidden() and page.locator("#farmBody").is_hidden()
    assert "no farm accounts are required" in page.locator("#farmWhy").inner_text()
    page.locator("#go").click()
    assert state.posts == ["run"]
    assert page.locator("#title").input_value() == "Saved title"
    assert page.locator("#existing-video").inner_text() == "An existing completed video"


@pytest.mark.parametrize("farm_ready", [False, True])
def test_saved_farm_selection_requires_explicit_uncheck_and_survives_polling(browser_page, farm_ready):
    page, state, open_page = browser_page
    state.selected, state.farm_ready = True, farm_ready
    open_page()
    checkbox = page.locator("#f_on")
    assert checkbox.is_checked() and checkbox.is_enabled()
    assert page.locator("#go").is_disabled()
    assert "Uncheck Cloud render" in page.locator("#cr-generation-readiness").inner_text()
    assert page.evaluate("localStorage.getItem('fr.farm')") == "1"
    page.evaluate("farmPoll()")
    assert checkbox.is_checked() and checkbox.is_enabled()
    assert page.locator("#go").is_disabled() and page.locator("#farmSetup").is_hidden()
    page.locator("#go").dispatch_event("click")
    assert not state.posts
    checkbox.uncheck()
    assert checkbox.is_disabled() and page.locator("#go").is_enabled()
    assert page.evaluate("localStorage.getItem('fr.farm')") == ""
    page.evaluate("farmPoll()")
    assert not checkbox.is_checked() and checkbox.is_disabled()
    page.locator("#go").click()
    assert state.posts == ["run"]


def test_native_farm_poll_does_not_reenable_a_busy_generation_button(browser_page):
    page, _, open_page = browser_page
    open_page()
    page.evaluate("document.getElementById('go').disabled=true")
    page.evaluate("farmPoll()")
    assert page.locator("#go").is_disabled()


def test_api_brain_keeps_original_farm_choice_and_dispatch(browser_page):
    page, state, open_page = browser_page
    state.mode, state.selected, state.farm_ready = "channelrecipe", True, True
    open_page()
    assert page.locator("#f_on").is_checked() and page.locator("#f_on").is_enabled()
    assert page.locator("#farmBody").is_visible()
    assert page.locator("#go").is_enabled()
    assert "render on the cloud" in page.locator("#go").inner_text()
    page.locator("#go").click()
    assert state.posts == ["farm/queue"]
    assert page.evaluate("localStorage.getItem('fr.farm')") == "1"


def test_studio_cloud_opens_the_farm_with_its_price_and_balance(browser_page):
    """With Studio Cloud on the account, Cloud render is a real choice: no RunPod or bucket to connect, the price
    and balance on the page, and a selected farm no longer blocks Create."""
    page, state, open_page = browser_page
    state.selected, state.farm_ready = True, True          # the farm's config reports Studio Cloud ready
    state.cloud = {"available": True, "balance_usd": 4.5, "topup_url": "https://channelrecipe.com/app#billing",
                   "machines": {"cpu": {"price_per_hr": 1.2}}}
    open_page()
    page.wait_for_selector("#cr-cloud-note")
    note = page.locator("#cr-cloud-note").inner_text()
    assert "Studio Cloud" in note and "$1.20 an hour" in note and "Balance: $4.50" in note
    assert page.locator("#cr-cloud-note a").get_attribute("href") == "https://channelrecipe.com/app#billing"
    assert page.locator("#f_on").is_enabled() and page.locator("#f_on").is_checked()
    assert page.locator("#go").is_enabled(), "a selected farm renders on Studio Cloud instead of being refused"
    assert "Uncheck Cloud render" not in page.locator("#cr-generation-readiness").inner_text()
