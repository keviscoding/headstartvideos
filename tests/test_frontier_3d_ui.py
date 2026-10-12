"""Original 3D review actions are explicit; all browser/worker responses are fake."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_runtime import cr_ui


UI = """<!doctype html><html><head></head><body>
<div id="voBar"></div><button id="go">Create video</button>
<section><h2>Your videos</h2><div id="cards"><div class="card">
<video src="/f/video/finished-3d" controls></video>
<div class="meta"><b>Finished bees video</b><span>Original 3D look</span>
<div class="acts"><a href="/editor/finished-3d">Edit timeline</a>
<button class="reveal" data-slug="finished-3d">Open folder</button></div></div>
</div></div></section><footer>Original footer</footer>
<script>window.originalSpec={style:'original-3d',features:{threed:true,stock:true}};
window.originalCalls=0;window.loadLibrary=()=>{window.originalCalls++;};</script>
</body></html>"""


def state(**updates):
    return {"available": True, "busy": False, "state": "idle", "message": "Saved scenes are ready.",
            "operation_project_id": "crv_" + "a" * 24, "needs_reassembly": False,
            "flags": [{"id": "original-shot-b", "severity": 2,
                       "note": "The subject is hard to see.", "fix": "Bring the subject forward.",
                       "codes": ["framing"]}],
            "scenes": [{"id": "original-shot-a"}, {"id": "original-shot-b"}],
            "failed_calls": [], "needs_manual_recovery": False, **updates}


@pytest.fixture
def studio_page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error:
            pytest.skip("Local Playwright Chromium is unavailable")
        page = browser.new_page()
        fixture = SimpleNamespace(page=page, state=state(), posts=[], gets=0, reject=None, errors=[])
        page.on("pageerror", lambda error: fixture.errors.append(str(error)))

        def route(request):
            url = request.request.url
            if url.endswith("/"):
                request.fulfill(content_type="text/html", body=cr_ui.transform_html(UI))
            elif "/cr-3d/" in url:
                if request.request.method == "POST":
                    data = request.request.post_data_json
                    fixture.posts.append(data)
                    if fixture.reject:
                        request.fulfill(status=409, content_type="application/json",
                                        body=json.dumps({"error": fixture.reject}))
                        return
                    if data["action"] == "sync":
                        fixture.state.update(state="complete", busy=False, needs_manual_recovery=False,
                                             message="Your saved completion record is synced.")
                    else:
                        fixture.state.update(state="running", busy=True, message="Your requested action is running.")
                else:
                    fixture.gets += 1
                request.fulfill(content_type="application/json", body=json.dumps(fixture.state))
            elif "/f/video/" in url:
                request.fulfill(status=204, body="")
            else:
                pytest.fail("Unexpected browser request: " + url)

        page.route("**/*", route)
        fixture.open = lambda: (page.goto("http://studio-fixture.test/"),
                                page.locator(".cr-3d-open").click(),
                                page.wait_for_function("document.querySelectorAll('#cr-3d-scenes input').length===2"))
        yield fixture
        browser.close()
        assert not fixture.errors


def test_review_script_parses_and_original_controls_remain(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    html = cr_ui.transform_html(UI)
    assert "window.originalSpec={style:'original-3d',features:{threed:true,stock:true}}" in html
    assert '<a href="/editor/finished-3d">Edit timeline</a>' in html
    script = re.search(r'<script id="cr-3d-review-ui">(.*?)</script>', html, re.S).group(1)
    path = tmp_path / "review-ui.js"
    path.write_text(script)
    completed = subprocess.run([node, "--check", str(path)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr


def test_open_reads_saved_review_without_starting_qa_or_repair(studio_page):
    f = studio_page
    f.open()
    assert f.posts == []
    assert f.page.locator("#cr-3d-title").inner_text() == "Finished bees video"
    assert "Claude usage:" in f.page.locator("#cr-3d-panel").inner_text()
    assert "The subject is hard to see." in f.page.locator("#cr-3d-scenes").inner_text()
    assert f.page.locator("#cr-3d-redo").is_disabled()
    assert f.page.locator("#cr-3d-flagged").is_enabled()
    assert f.page.evaluate("window.originalSpec") == {"style": "original-3d", "features": {"threed": True, "stock": True}}


def test_selected_repair_preserves_scene_ids_and_note_then_blocks_duplicates(studio_page):
    f = studio_page
    f.open()
    f.page.get_by_role("checkbox", name="Select scene 2").check()
    f.page.locator("#cr-3d-note").fill("  Make the subject clear, keeping this look.  ")
    f.page.locator("#cr-3d-redo").click()
    f.page.wait_for_function("document.querySelector('#cr-3d-qa').disabled")
    assert f.posts == [{"action": "redo", "scene_ids": ["original-shot-b"],
                        "note": "Make the subject clear, keeping this look.", "from_qa": False}]
    assert f.page.locator("#cr-3d-redo").is_disabled()
    assert f.page.locator("#cr-3d-flagged").is_disabled()
    assert f.page.locator("#cr-3d-note").is_disabled()
    f.page.locator("#cr-3d-close").click()
    assert f.posts == [{"action": "redo", "scene_ids": ["original-shot-b"],
                        "note": "Make the subject clear, keeping this look.", "from_qa": False}]


def test_qa_and_flagged_repair_use_the_original_explicit_actions(studio_page):
    f = studio_page
    f.open()
    f.page.locator("#cr-3d-qa").click()
    f.page.wait_for_function("document.querySelector('#cr-3d-qa').disabled")
    assert f.posts == [{"action": "qa", "scene_ids": [], "note": "", "from_qa": False}]
    f.page.locator("#cr-3d-close").click()
    f.state.update(state="done", busy=False)
    f.page.locator(".cr-3d-open").click()
    f.page.wait_for_function("!document.querySelector('#cr-3d-flagged').disabled")
    f.page.locator("#cr-3d-flagged").click()
    assert f.posts[-1] == {"action": "redo", "scene_ids": [], "note": "", "from_qa": True}


def test_flagged_repair_keeps_custom_feedback_and_reviewed_scene_selection(studio_page):
    f = studio_page
    f.state["flags"].append({"id": "original-shot-a", "severity": 1, "note": "Minor issue."})
    f.open()
    f.page.locator("#cr-3d-note").fill("Keep the style and improve the flagged subject.")
    f.page.locator("#cr-3d-flagged").click()
    assert f.posts == [{"action": "redo", "scene_ids": ["original-shot-b"],
                        "note": "Keep the style and improve the flagged subject.", "from_qa": False}]


def test_original_scene_timing_and_words_help_users_review_the_actual_video(studio_page):
    f = studio_page
    f.state["scenes"] = [{"id": "original-shot-a", "at": 12.75, "words": "The bee lands on the flower."},
                         {"id": "original-shot-b", "at": -1, "words": "The wings fold."}]
    f.open()
    assert "Scene plan: The bee lands on the flower." in f.page.locator("#cr-3d-scenes").inner_text()
    assert f.page.locator(".cr-3d-jump").count() == 1
    f.page.get_by_role("button", name="Watch from 0:12").click()
    assert f.page.locator("#cr-3d-video").evaluate("video=>video.currentTime") == 12.75
    assert not f.page.get_by_role("checkbox", name="Select scene 1").is_checked()
    assert f.posts == []


@pytest.mark.parametrize("updates", [
    {"state": "failed", "needs_manual_recovery": True},
    {"state": "pending", "needs_manual_recovery": True},
    {"state": "idle", "busy": True},
])
def test_pending_failed_or_busy_work_never_starts_more_work(studio_page, updates):
    f = studio_page
    f.state.update(updates)
    f.open()
    assert f.posts == []
    assert f.page.locator("#cr-3d-qa").is_disabled()
    assert f.page.locator("#cr-3d-flagged").is_disabled()
    assert f.page.locator("#cr-3d-sync").is_visible() is (updates["state"] == "pending")
    if updates.get("needs_manual_recovery"):
        assert f.page.locator("#cr-3d-recovery a").get_attribute("href") == "/cr-setup"
        assert "crv_" + "a" * 24 in f.page.locator("#cr-3d-recovery").inner_text()


def test_pending_completion_has_explicit_metadata_only_sync_without_generation(studio_page):
    f = studio_page
    f.state.update(state="pending", needs_manual_recovery=True)
    f.open()
    assert f.posts == []
    assert f.page.locator("#cr-3d-sync").is_enabled()
    assert "does not start generation" in f.page.locator("#cr-3d-sync-note").inner_text()
    assert f.page.locator("#cr-3d-qa").is_disabled()
    f.page.locator("#cr-3d-sync").click()
    f.page.wait_for_function("document.querySelector('#cr-3d-sync').hidden")
    assert f.posts == [{"action": "sync"}]
    assert f.page.locator("#cr-3d-qa").is_enabled()
    assert f.page.locator("#cr-3d-recovery").is_hidden()
    assert f.page.locator("#cr-3d-sync-note").is_hidden()


def test_stopped_action_needs_explicit_new_work_acknowledgement_not_replay(studio_page):
    f = studio_page
    f.state.update(state="failed", needs_manual_recovery=True)
    f.open()
    assert f.posts == []
    assert "The stopped action will not replay" in f.page.locator("#cr-3d-recovery").inner_text()
    assert "current cached scenes" in f.page.locator("#cr-3d-recovery").inner_text()
    assert f.page.locator("#cr-3d-qa").is_disabled()
    assert f.page.locator("#cr-3d-new-work").is_visible()
    f.page.locator("#cr-3d-new-work").check()
    assert f.posts == []
    assert f.page.locator("#cr-3d-qa").is_enabled()
    assert f.page.locator("#cr-3d-flagged").is_enabled()
    f.page.locator("#cr-3d-qa").click()
    f.page.wait_for_function("document.querySelector('#cr-3d-qa').disabled")
    assert f.posts == [{"action": "qa", "scene_ids": [], "note": "", "from_qa": False,
                        "acknowledge_new_work": True}]
    f.page.wait_for_function("document.querySelector('#cr-3d-new-work-label').hidden")
    assert f.page.locator("#cr-3d-new-work-label").is_hidden()


def test_new_repair_preserves_selected_scenes_and_explicit_extra_usage_ack(studio_page):
    f = studio_page
    f.state.update(state="failed", needs_manual_recovery=True)
    f.open()
    f.page.locator("#cr-3d-new-work").check()
    f.page.get_by_role("checkbox", name="Select scene 2").check()
    f.page.locator("#cr-3d-note").fill("Make the flagged subject visible.")
    f.page.locator("#cr-3d-redo").click()
    assert f.posts == [{"action": "redo", "scene_ids": ["original-shot-b"],
                        "note": "Make the flagged subject visible.", "from_qa": False,
                        "acknowledge_new_work": True}]


@pytest.mark.parametrize("updates", [
    {"state": "pending", "needs_manual_recovery": True},
    {"state": "running", "needs_manual_recovery": True},
    {"state": "failed", "needs_manual_recovery": True, "busy": True},
])
def test_pending_running_or_busy_stopped_work_cannot_enable_a_new_action(studio_page, updates):
    f = studio_page
    f.state.update(updates)
    f.open()
    assert f.page.locator("#cr-3d-qa").is_disabled()
    if updates["state"] in {"pending", "running"}:
        assert f.page.locator("#cr-3d-new-work-label").is_hidden()
    else:
        assert f.page.locator("#cr-3d-new-work").is_disabled()
    assert f.posts == []


def test_new_work_acknowledgement_is_not_remembered_when_reopening(studio_page):
    f = studio_page
    f.state.update(state="failed", needs_manual_recovery=True)
    f.open()
    f.page.locator("#cr-3d-new-work").check()
    f.page.locator("#cr-3d-close").click()
    f.page.locator(".cr-3d-open").click()
    f.page.wait_for_function("document.querySelector('#cr-3d-new-work-label').hidden===false")
    assert not f.page.locator("#cr-3d-new-work").is_checked()
    assert f.page.locator("#cr-3d-qa").is_disabled()
    assert f.posts == []


def test_stopped_assembly_resumes_only_after_acknowledgement_and_explicit_click(studio_page):
    f = studio_page
    f.state.update(state="failed", needs_manual_recovery=True, needs_reassembly=True)
    f.open()
    assert f.page.locator("#cr-3d-assemble").is_disabled()
    assert "same cached assembly project" in f.page.locator("#cr-3d-assembly").inner_text()
    assert "Failed or unfinished Claude stages" in f.page.locator("#cr-3d-assembly").inner_text()
    f.page.locator("#cr-3d-new-work").check()
    assert f.page.locator("#cr-3d-qa").is_enabled()
    assert f.page.locator("#cr-3d-flagged").is_enabled()
    assert f.page.locator("#cr-3d-assemble").is_enabled()
    assert f.posts == []
    f.page.locator("#cr-3d-assemble").click()
    assert f.posts == [{"action": "assemble", "scene_ids": [], "note": "", "from_qa": False,
                        "acknowledge_new_work": True}]
    f.page.wait_for_function("document.querySelector('#cr-3d-assemble').disabled")


def test_repaired_scenes_require_explicit_assembly_and_refresh_finished_video(studio_page):
    f = studio_page
    f.state.update(state="done", needs_reassembly=True)
    f.open()
    assert f.posts == []
    assert f.page.locator("#cr-3d-assemble").is_visible()
    assert "Repaired scenes are saved" in f.page.locator("#cr-3d-assembly").inner_text()
    f.page.locator("#cr-3d-assemble").click()
    f.page.wait_for_function("document.querySelector('#cr-3d-assemble').disabled")
    assert f.posts == [{"action": "assemble", "scene_ids": [], "note": "", "from_qa": False}]
    f.state.update(state="done", busy=False, needs_reassembly=False, message="The updated video is ready.")
    f.page.wait_for_function("document.querySelector('#cr-3d-assemble').hidden")
    assert "cr-revision=" in f.page.locator("#cr-3d-video").get_attribute("src")
    assert f.page.evaluate("window.originalCalls") == 1
    assert len(f.posts) == 1


def test_rejected_request_is_readable_and_does_not_retry(studio_page):
    f = studio_page
    f.reject = "Finish your current video before checking another."
    f.open()
    f.page.locator("#cr-3d-qa").click()
    f.page.wait_for_function("document.querySelector('#cr-3d-status').textContent.includes('Finish your current video')")
    assert len(f.posts) == 1
    assert f.gets >= 3  # Discovery, opening, and read-only reconciliation.


def test_server_notes_are_text_and_non_3d_jobs_offer_no_repair(studio_page):
    f = studio_page
    f.state["flags"][0]["note"] = '<img src="x" onerror="window.stolen=true">'
    f.open()
    assert f.page.locator("#cr-3d-scenes img").count() == 0
    assert f.page.evaluate("window.stolen===undefined")
    assert '<img src="x"' in f.page.locator("#cr-3d-scenes").inner_text()
    f.state["available"] = False
    f.page.reload()
    f.page.wait_for_function("document.querySelector('.reveal').dataset.cr3dChecked==='true'")
    assert f.page.locator(".cr-3d-open").count() == 0
    assert f.posts == []
