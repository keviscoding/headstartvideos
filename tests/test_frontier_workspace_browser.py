"""Browser contracts for the creator workspace, using synthetic local controls.

The ephemeral server does not import the creative engine, a provider adapter,
saved account settings or private catalogue data. Tests cannot reach production
7860, cloud endpoints, media accounts or native Claude work.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from flask import Flask, Response, jsonify, request, send_file
import pytest
from PIL import Image

playwright = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server

RUNTIME = Path(__file__).parents[1] / "local_runtime"
sys.path.insert(0, str(RUNTIME))
import cr_presentation

TOKEN = "synthetic-browser-local-token"
CLONE = "cr_clone_browser_fixture"


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as driver:
        try:
            browser = driver.chromium.launch(headless=True)
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install the Playwright Chromium browser to run workspace UI tests.")
            raise
        yield browser
        browser.close()


@pytest.fixture
def workspace(browser):
    env = SimpleNamespace(mode="ready", creates=[], posts=[], errors=[], preview_gets=[], preview_cached=set(),
                          preview_active=0, preview_max_active=0, preview_busy=0, preview_delay=0,
                          finished_jobs=[{"slug": "finished-film", "title": "A finished film", "style": "documentary", "editable": True}])
    preview_lock = threading.Lock()
    image = io.BytesIO()
    Image.new("RGB", (160, 90), "#415573").save(image, "JPEG")
    rows = [
        {"name": "documentary", "features": {"offered": ["youtube", "motion", "sound"], "on": ["youtube", "motion", "sound"]}},
        {"name": "3d", "tab": "3d", "features": {"offered": ["threed", "maps", "sound"], "on": ["threed", "sound"]}},
        {"name": CLONE, "label": "My Illustrated Worlds", "custom": True,
         "features": {"offered": ["motion", "sound"], "on": ["motion", "sound"]}},
    ]
    features = [{"key": key, "label": key, "explicit": key == "threed"}
                for key in ("youtube", "ai_images", "motion", "maps", "threed", "sound")]
    catalog = {"channels": cr_presentation.presentation_catalog(rows, {
        CLONE: {"cr_style_clone": {"visual_mode": "reference_only"}}}),
        "features": features, "caps": {"features": {row["key"]: {"ok": True} for row in features}},
        "dlc": {}, "prices": {}}
    app = Flask(__name__)

    @app.before_request
    def local_token():
        if request.method == "POST":
            if request.headers.get("X-CR-Local-Token") != TOKEN:
                return jsonify(error="Synthetic local token required."), 403
            env.posts.append({"path": request.path, "body": request.get_json(silent=True)})

    @app.get("/")
    def page():
        preset = {"style": request.args["cr-preset"]} if request.args.get("cr-preset") else None
        html = (RUNTIME / "cr_workspace.html").read_text().replace(
            "__LOCAL_TOKEN_JSON__", json.dumps(TOKEN)).replace("__PRESET_JSON__", json.dumps(preset))
        return Response(html, mimetype="text/html")

    @app.get("/cr-workspace/studio.js")
    @app.get("/cr-workspace/studio.css")
    def asset():
        script = request.path.endswith(".js")
        return send_file(RUNTIME / ("cr_workspace.js" if script else "cr_workspace.css"),
                         mimetype="application/javascript" if script else "text/css")

    @app.get("/cr-workspace/art/<name>")
    def art(name):
        return send_file(cr_presentation.asset(name), mimetype="image/svg+xml")

    @app.get("/cr-workspace/catalog")
    def api_catalog():
        return jsonify(catalog)

    @app.get("/cr-status")
    def status():
        ready = env.mode != "needs_setup"
        return jsonify(local_version="synthetic-studio", brain_mode="claude_code",
                       readiness={"ready": ready, "blockers": [] if ready else ["Connect your narration account."]},
                       claude_code={"allowance": {"reported_limit": True, "blocked": False}},
                       narration={"voice": "My narrator", "label": "My media account"})

    @app.get("/api/queue")
    def queue():
        return jsonify(busy=env.mode == "active", jobs=[{"slug": "active-film", "title": "A film in production",
                       "style": "3d", "state": "running"}] if env.mode == "active" else [
                       {"slug": "stopped-film", "title": "Saved project needs review", "state": "error"}])

    @app.get("/cr-workspace/activity")
    def activity():
        return jsonify(busy=env.mode == "active", operations=[])

    @app.get("/api/jobs")
    def jobs():
        return jsonify(jobs=env.finished_jobs, platform="darwin")

    @app.get("/cr-workspace/preview/project/<slug>.jpg")
    def project_preview(slug):
        with preview_lock:
            env.preview_gets.append(slug)
            if slug in env.preview_cached:
                return Response(image.getvalue(), mimetype="image/jpeg")
            if env.preview_busy:
                env.preview_busy -= 1
                return Response(status=503, headers={"Retry-After": "1"})
            env.preview_active += 1
            env.preview_max_active = max(env.preview_max_active, env.preview_active)
        time.sleep(env.preview_delay)
        with preview_lock:
            env.preview_active -= 1
            env.preview_cached.add(slug)
        return Response(image.getvalue(), mimetype="image/jpeg")

    @app.get("/api/status/<slug>")
    def progress(slug):
        active = slug == "active-film"
        return jsonify(state="running" if active else "done", done=not active,
                       lines=["ffmpeg: final render"] if active else ["published: synthetic film"],
                       progress=0.425 if active else 1, elapsed=125, error="",
                       cr_cancel={"can_cancel": active, "run_id": "exact-active-run" if active else ""})

    @app.get("/api/result/<slug>")
    def result(slug):
        return jsonify(video=True, audio=True, script=True, srt=True, desc=True, thumbs=["cover.png"])

    @app.post("/api/run")
    def run():
        if env.mode == "selected_account_missing":
            return jsonify(error="Selected options need attention.", blockers=["Your image account needs attention."]), 409
        slug = "fresh-" + str(len(env.creates) + 1)
        env.creates.append({"slug": slug, "body": request.json})
        return jsonify(queued=[{"slug": slug, "title": request.json["title"]}])

    @app.post("/cr-video/<slug>/cancel")
    def cancel(slug):
        if slug != "active-film" or request.json != {"run_id": "exact-active-run"}:
            return jsonify(error="Cancellation is bound to the exact active run."), 409
        return jsonify(ok=True)

    server = make_server("127.0.0.1", 0, app, threaded=True)
    assert server.server_port != 7860
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env.base = f"http://127.0.0.1:{server.server_port}"
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    # Only this ephemeral fixture may receive a request. Optional remote fonts,
    # cloud addresses and the real local studio are all blocked by the browser.
    context.route("**/*", lambda route: route.continue_()
                  if route.request.url.startswith(env.base + "/") else route.abort())
    page = context.new_page()
    page.set_default_timeout(5000)
    page.on("pageerror", lambda error: env.errors.append(str(error)))
    env.page = page

    def open_page(query=""):
        page.goto(env.base + "/" + query, wait_until="networkidle")
        page.wait_for_function("window.CR_WORKSPACE_CLIENT?.state.catalog && window.CR_WORKSPACE_CLIENT.state.setup")
        page.wait_for_function("!window.CR_WORKSPACE_CLIENT.state.refresh")
    env.open = open_page
    yield env
    context.close()
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)
    assert not env.errors, "Unexpected workspace JavaScript error: " + str(env.errors)


def test_same_title_and_script_submit_two_fresh_projects(workspace):
    env, page = workspace, workspace.page
    env.open("?cr-preset=" + CLONE)
    page.locator("[data-input-mode=script]").click()
    page.locator("#crw-title").fill("The same title")
    page.locator("#crw-script").fill("The same narration. Each Create must be a new video attempt.")
    for _ in range(2):
        page.locator("#crw-create").click()
        page.wait_for_function("window.CR_WORKSPACE_CLIENT.state.page==='projects' && !window.CR_WORKSPACE_CLIENT.state.submitting")
        page.locator(".crw-nav [data-nav=create]").click()
    assert len(env.creates) == 2
    first, second = env.creates
    assert first["slug"] != second["slug"]
    assert first["body"]["title"] == second["body"]["title"]
    assert first["body"]["script"] == second["body"]["script"]
    assert all(not item["body"].get("slug") and not item["body"].get("project_id") for item in env.creates)
    assert [row["path"] for row in env.posts] == ["/api/run", "/api/run"]


def test_saved_reference_recipe_excludes_stale_unrelated_visuals(workspace):
    env, page = workspace, workspace.page
    page.add_init_script("localStorage.setItem('channelrecipe.workspace.v1', JSON.stringify({recipe:'3d',fast:true,features:{" +
                         json.dumps(CLONE) + ":{motion:true,sound:true,youtube:true,ai_images:true,threed:true}}}));")
    env.open("?cr-preset=" + CLONE)
    assert page.locator("#crw-selected-name").inner_text() == "My Illustrated Worlds"
    assert "no footage" in page.locator("#crw-selected-policy").inner_text()
    assert page.locator("#crw-fast-section").is_hidden()
    page.locator("#crw-title").fill("My new illustrated film")
    page.locator("#crw-create").click()
    page.wait_for_function("!window.CR_WORKSPACE_CLIENT.state.submitting && window.CR_WORKSPACE_CLIENT.state.page==='projects'")
    body = env.creates[0]["body"]
    assert body["style"] == CLONE
    assert body["features"] == {"motion": True, "sound": True}
    assert body["render_backend"] == "original"
    assert body["character_image"] == body["avatar_id"] == ""


def test_setup_blocks_before_work_but_quota_history_is_advisory(workspace):
    env, page = workspace, workspace.page
    env.mode = "needs_setup"
    env.open()
    assert page.locator("#crw-create").is_disabled()
    assert "Connect your narration account" in page.locator("#crw-readiness").inner_text()
    assert not env.posts
    env.mode = "ready"
    env.open()
    assert not page.locator("#crw-create").is_disabled()
    env.mode = "selected_account_missing"
    page.locator("#crw-title").fill("A video with a missing selected account")
    page.locator("#crw-create").click()
    page.locator("#crw-error").wait_for(state="visible")
    assert "image account" in page.locator("#crw-error").inner_text()
    assert len(env.posts) == 1 and env.posts[0]["path"] == "/api/run"
    assert not env.creates


def test_projects_draw_native_progress_cancel_exact_run_and_preserve_exports(workspace):
    env, page = workspace, workspace.page
    env.mode = "active"
    env.open("#projects")
    page.get_by_role("button", name="View progress", exact=True).click()
    page.locator("#crw-progress-cancel").wait_for(state="visible")
    assert page.locator("#crw-progress-bar").evaluate("e=>e.style.width") == "42.5%"
    assert page.locator("#crw-progress-bar").evaluate("e=>e.parentElement.getAttribute('aria-valuenow')") == "43"
    page.locator("#crw-progress-cancel").click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('Cancellation requested')")
    assert env.posts[-1] == {"path": "/cr-video/active-film/cancel", "body": {"run_id": "exact-active-run"}}
    page.locator("[data-project-filter=done]").click()
    assert page.locator("#crw-projects-list .crw-project-card").count() == 1
    player=page.locator('#crw-projects-list video.crw-video-preview')
    assert player.count()==1 and player.get_attribute('src')=='/f/video/finished-film'
    page.wait_for_function("document.querySelector('#crw-projects-list video')?.dataset.previewPosterState==='loaded'")
    assert player.get_attribute('poster')=='/cr-workspace/preview/project/finished-film.jpg'
    # Cards show the poster and one Play button; native controls appear only while watching.
    assert player.get_attribute('preload')=='none' and player.get_attribute('controls') is None
    page.evaluate("window.keptVideo=document.querySelector('#crw-projects-list video')")
    page.locator('#crw-projects-refresh').click()
    page.wait_for_timeout(150)
    assert page.evaluate("window.keptVideo===document.querySelector('#crw-projects-list video')")
    page.get_by_role("button", name="Play A finished film", exact=True).wait_for(state='visible')
    page.get_by_role("button", name="Video & exports", exact=True).click()
    page.locator("#crw-output").wait_for(state="visible")
    assert page.locator("#crw-output-download").get_attribute("href") == "/f/video/finished-film"
    assert page.locator("#crw-output-editor").get_attribute("href") == "/editor/finished-film"
    assert page.locator("#crw-output-exports a").count() == 5
    assert page.locator("#crw-output-exports a").all_text_contents() == ["Narration", "Script", "Subtitles", "Description", "Thumbnail"]


def test_gallery_works_with_keyboard_and_a_mobile_viewport(workspace):
    env, page = workspace, workspace.page
    page.set_viewport_size({"width": 390, "height": 844})
    env.open()
    assert page.evaluate("document.documentElement.scrollWidth") == 390
    page.locator("#crw-recipe-open").focus()
    page.keyboard.press("Enter")
    assert page.locator("#crw-gallery-search").evaluate("e=>e===document.activeElement")
    page.locator("#crw-gallery-search").fill("Cinematic Reconstruction")
    cards = page.locator("#crw-gallery-grid .crw-recipe-card")
    assert cards.count() == 1
    cards.first.focus()
    page.keyboard.press("Enter")
    page.locator("#crw-detail-use").focus()
    assert page.locator("#crw-detail-use").evaluate("e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}")
    page.keyboard.press("Enter")
    assert page.locator("#crw-gallery").is_hidden()
    assert page.locator("#crw-selected-name").inner_text() == "Cinematic Reconstruction"
    assert page.locator("#crw-recipe-open").evaluate("e=>e===document.activeElement")
    page.locator("#crw-recipe-open").click()
    # Native search fields consume Escape to clear their current query first.
    page.locator("#crw-gallery-search").fill("")
    page.keyboard.press("Escape")
    page.locator("#crw-gallery").wait_for(state="hidden")


def test_project_posters_are_lazy_bounded_and_retry_busy_decoders(workspace):
    env, page = workspace, workspace.page
    env.finished_jobs = [{"slug": f"film-{i}", "title": f"Finished film {i}", "style": "documentary", "editable": True}
                         for i in range(24)]
    env.preview_busy = 1
    env.preview_delay = 0.12
    env.open()
    assert not env.preview_gets, "The hidden Projects page must not decode its entire video library."
    page.locator('.crw-nav [data-nav=projects]').click()
    first = page.locator('[data-slug="film-0"] video')
    page.wait_for_function("document.querySelector('[data-slug=film-0] video')?.dataset.previewPosterState==='loaded'")
    assert first.get_attribute('poster') == '/cr-workspace/preview/project/film-0.jpg'
    assert env.preview_gets.count('film-0') >= 2, "A temporary 503 must be retried."
    assert 'film-23' not in env.preview_gets, "Offscreen cards must stay lazy."
    last = page.locator('[data-slug="film-23"] video')
    last.scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('[data-slug=film-23] video')?.dataset.previewPosterState==='loaded'")
    assert last.get_attribute('poster') == '/cr-workspace/preview/project/film-23.jpg'
    assert env.preview_max_active <= 2, "Only two screenshot decoders may be warmed concurrently."
    assert not env.posts, "Loading and retrying screenshots must not create creative work."
