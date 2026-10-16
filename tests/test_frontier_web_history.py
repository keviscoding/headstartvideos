"""Website history renders native production receipts without API-wallet actions."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from webapp import frontier_studio as studio


SCRIPT = Path(__file__).parents[1] / "webapp/static/local-studio.js"


@pytest.fixture
def native_history(tmp_path, monkeypatch):
    monkeypatch.setattr(studio.db, "IS_PG", False)
    monkeypatch.setattr(studio.db, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setattr(studio, "release_ready", lambda: True)
    monkeypatch.setattr(studio.providers, "generate", lambda *a, **k: pytest.fail("History must not generate content"))
    owner = {"id": 1, "email": "owner@example.com"}
    proof = {"local_version": studio.VERSION, "general_model": "claude-sonnet-5-5", "visual_model": "claude-opus-5-5",
             "claude_installed": True, "claude_authenticated": True, "api_provider": "firstParty",
             "ffmpeg_ready": True, "browser_ready": True, "node_ready": True, "costs_accepted": True,
             "models_verified": True, "media_ready": True}
    project = studio.native_begin(owner, studio.NativeBegin(begin_id="web_history_native", title="A complete workflow",
                                                           style="history", setup=proof))["project"]

    def authorize(suffix, role="general", kind="text"):
        req = studio.NativeAuthorize(project_id=project["id"], stage_id="cc_" + suffix * 64,
                                     input_fingerprint=suffix * 64, model_role=role, kind=kind,
                                     requested_model="claude-sonnet-5", local_version=studio.VERSION)
        return req, studio.native_authorize(owner, req)

    complete, approval = authorize("a")
    studio.native_finish_stage(owner, studio.NativeFinishStage(project_id=project["id"], stage_id=complete.stage_id,
        authorization_token=approval["authorization_token"], status="complete", result_fingerprint="f" * 64,
        usage={"input_tokens": 4, "output_tokens": 7, "cache_read_input_tokens": 3,
               "cache_creation_input_tokens": 2, "reported": True}))
    failed, approval = authorize("b", "visual", "vision")
    authorize("c", "general", "research")
    studio.native_finish_stage(owner, studio.NativeFinishStage(project_id=project["id"], stage_id=failed.stage_id,
        authorization_token=approval["authorization_token"], status="failed", error_code="usage_limit",
        usage={"input_tokens": 20, "output_tokens": 5, "reported": True}))
    empty = studio.native_begin(owner, studio.NativeBegin(begin_id="web_history_empty", title="Not started yet",
                                                         style="history", setup=proof))["project"]
    return SimpleNamespace(status=studio.status(owner), progress=studio.native_progress(owner, project["id"]),
                           empty=studio.native_progress(owner, empty["id"]))


@pytest.fixture
def browser_page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error:
            pytest.skip("A local Playwright Chromium installation is unavailable")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page
        browser.close()
        assert not errors, errors


def mount(page, status, project, progress, *, progress_status=200, legacy=None):
    calls = []

    def route_request(route):
        path = route.request.url.removeprefix("https://cr-history.test")
        calls.append((route.request.method, path))
        if path == "/app":
            return route.fulfill(content_type="text/html", body='<div id="page-local-studio"></div><script>const currentUser={is_admin:true};</script>')
        if path.startswith("/static/recipes/"):              # the returning customer's home shows recipe pictures
            return route.fulfill(status=200, content_type="image/webp", body=b"")
        if path == "/api/studio/status":
            payload, code = status, 200
        elif path == "/api/studio/projects":
            payload, code = {"projects": [project]}, 200
        elif path == "/api/studio/projects/" + project["id"] + "/progress":
            payload, code = progress, progress_status
        elif path == "/api/studio/projects/" + project["id"] + "/requests" and legacy is not None:
            payload, code = legacy, 200
        else:
            pytest.fail("Unexpected history request: " + path)
        assert route.request.method == "GET", "Reviewing history must not submit a paid request or approve recovery"
        route.fulfill(status=code, content_type="application/json", body=json.dumps(payload))

    page.route("**/*", route_request)
    page.goto("https://cr-history.test/app")
    page.add_script_tag(path=str(SCRIPT))
    page.evaluate("CRLocalStudio.open()")
    # Subscribers land on the setup page; their projects load below it.
    page.locator(".ls-view-requests").click()
    return calls, page.locator(".ls-request-list")


def test_native_history_uses_actual_service_receipts_and_customer_claude_account(native_history, browser_page):
    payload = native_history.progress
    # No API brain is connected; the website must not dereference its key/provider.
    assert not native_history.status["brain"]["connected"]
    calls, history = mount(browser_page, native_history.status, payload["project"], payload)
    browser_page.wait_for_function("document.querySelectorAll('.ls-request').length===3")
    text = history.inner_text()
    assert "Stage saved" in text and "Stage stopped" in text and "Awaiting a saved result" in text
    assert "reached its usage limit" in text
    assert "Claude Code" in text and "Sonnet 5.5" in text and "Opus 5.5" in text
    assert "16 locally reported tokens, including cached input" in text
    assert "25 locally reported tokens, including cached input" in text
    assert "does not mean no usage occurred" in text
    assert "3 model requests" in browser_page.locator(".ls-project").inner_text()
    assert "No model requests" not in text
    assert history.locator("[data-ls-retry-form]").count() == 0
    assert history.locator('a[href="http://127.0.0.1:7860/cr-setup"]').count() == 1
    assert not any(path.endswith("/requests") for _, path in calls)


def test_native_project_without_stages_does_not_query_legacy_history(native_history, browser_page):
    payload = native_history.empty
    calls, history = mount(browser_page, native_history.status, payload["project"], payload)
    browser_page.wait_for_function("document.querySelector('.ls-request-list').textContent.includes('No Claude Code stages')")
    assert "No Claude Code stages have been authorized" in history.inner_text()
    assert not any(path.endswith("/requests") for _, path in calls)


@pytest.mark.parametrize("purpose,saved,approved", [
    ("video", "Resume the same video to reuse its completed work", "Resume the same video in your local studio"),
    ("setup", "model-check result is saved", "Repeat model verification in local setup"),
    ("creative", "creative-stage result is saved with its operation", "stopped creative action will not replay"),
    (None, "Review it using the original controls", "Review its purpose and original controls"),
])
def test_native_history_recovery_describes_actual_saved_work_purpose(native_history, browser_page, purpose, saved, approved):
    payload = json.loads(json.dumps(native_history.progress))
    payload["purpose"] = purpose
    payload["stages"][1].update(status="retry_allowed")
    calls, history = mount(browser_page, native_history.status, payload["project"], payload)
    browser_page.wait_for_function("document.querySelectorAll('.ls-request').length===3")
    text = history.inner_text()
    assert saved in text and approved in text
    assert "Stage attempt approved" in text
    if purpose != "video":
        assert "Resume the same video" not in text
    if purpose == "creative":
        assert "explicitly start a new action from current cached assets" in text
        assert "may repeat Claude calls" in text
    assert history.locator("[data-ls-retry-form]").count() == 0
    assert all(method == "GET" for method, _ in calls)


def test_legacy_404_fallback_keeps_api_recovery_without_saved_brain_assumptions(native_history, browser_page):
    status = {**native_history.status, "brain": {}, "providers": {"kie": {"label": "Kie.ai"}}}
    project = {**native_history.progress["project"], "status": "needs_review"}
    legacy = {"requests": [{"id": "old_api_request", "status": "needs_review", "attempts": 1,
        "created_at": project["created_at"], "context": {"provider": "kie"}, "can_retry": True,
        "errors": [{"message": "Provider connection stopped", "usage_reported": True,
                    "input_tokens": 10, "output_tokens": 3}]}]}
    calls, history = mount(browser_page, status, project, {"detail": "No native session"}, progress_status=404, legacy=legacy)
    browser_page.wait_for_function("document.querySelector('[data-ls-retry-form]')!==null")
    assert "Provider connection stopped" in history.inner_text()
    assert "connected Kie.ai account" in history.inner_text()
    submit = history.locator("[data-ls-retry-form] button")
    assert submit.is_disabled()
    history.locator("[data-ls-retry-form] input").check()
    assert submit.is_enabled()
    assert any(path.endswith("/requests") for _, path in calls)
    assert all(method == "GET" for method, _ in calls)


@pytest.mark.parametrize("http_status", [401, 403, 500])
def test_progress_auth_or_service_errors_never_fall_back_to_api_history(native_history, browser_page, http_status):
    calls, history = mount(browser_page, native_history.status, native_history.progress["project"],
                          {"detail": "Reconnect or refresh before reviewing"}, progress_status=http_status)
    browser_page.wait_for_function("document.querySelector('.ls-request-list').textContent.includes('Reconnect or refresh')")
    assert history.inner_text() == "Reconnect or refresh before reviewing"
    assert not any(path.endswith("/requests") for _, path in calls)
