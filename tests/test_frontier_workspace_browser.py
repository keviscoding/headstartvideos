"""Browser contracts for the creator workspace, using synthetic local controls.

The ephemeral server does not import the creative engine, a provider adapter,
saved account settings or private catalogue data. Tests cannot reach production
7860, cloud endpoints, media accounts or native Claude work.
"""
from __future__ import annotations

import io
import json
import os
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
import cr_watch

TOKEN = "synthetic-browser-local-token"
CLONE = "cr_clone_browser_fixture"
STORY_CAST = "THE MAIN CHARACTER is the man from the reference image \u2014 early thirties, short dark hair."


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
    env = SimpleNamespace(mode="ready", busy=False, status_lines=None, creates=[], posts=[], errors=[], preview_gets=[], preview_cached=set(),
                          local_version="synthetic-studio", account=None, update_state={"state": "idle"}, voice="Kore",
                          preview_active=0, preview_max_active=0, preview_busy=0, preview_delay=0,
                          finished_jobs=[{"slug": "finished-film", "title": "A finished film", "style": "documentary", "editable": True}],
                          cloud=None, cloud_starts=[], topups=[], characters=[], drawn=[], active_lines=["ffmpeg: final render"],
                          claude_fails="")
    preview_lock = threading.Lock()
    image = io.BytesIO()
    Image.new("RGB", (160, 90), "#415573").save(image, "JPEG")
    rows = [
        {"name": "documentary", "features": {"offered": ["youtube", "motion", "sound"], "on": ["youtube", "motion", "sound"]}},
        {"name": "3d", "tab": "3d", "features": {"offered": ["threed", "maps", "sound"], "on": ["threed", "sound"]}},
        {"name": CLONE, "label": "My Illustrated Worlds", "custom": True, "captions": False,   # its channel has none
         "features": {"offered": ["motion", "sound"], "on": ["motion", "sound"]}},
        {"name": "stories", "label": "2D Life Stories", "character": STORY_CAST, "character_image": "characters/stories.png",
         "features": {"offered": ["ai_images", "sound"], "on": ["ai_images", "sound"]}},
    ]
    features = [{"key": key, "label": key, "explicit": key == "threed"}
                for key in ("youtube", "ai_images", "motion", "maps", "threed", "sound")]
    catalog = {"channels": cr_presentation.presentation_catalog(rows, {
        CLONE: {"cr_style_clone": {"visual_mode": "reference_only"}}}),
        "features": features, "caps": {"features": {row["key"]: {"ok": True} for row in features}},
        "dlc": {}, "prices": {}, "footage_watch": [cr_watch.budget(m) for m in range(1, 61)],
        "footage_watcher": {"note": "On your last video, Kie's video watcher wasn't answering, so ChannelRecipe's "
                                    "watcher watched 12 of its videos."}}
    env.catalog = catalog                                  # a test may add recipes before it opens the page
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
        if env.mode in ("models", "models_auto"):         # only the models' daily check is left (Studio 110)
            why = "Your Claude models need their quick check: press Verify my Claude models."
            return jsonify(local_version=env.local_version, account=env.account, brain_mode="claudecode",
                           connected=True, readiness={"ready": False, "blockers": [why],
                                                      "checks": [{"key": "models", "ready": False, "message": why}]},
                           claude_code={"authenticated": True, "mcp_connected": env.mode == "models_auto"},
                           narration={"voice": "My narrator", "label": "My media account"})
        if env.mode == "api_route":                         # still on the old API route: its videos still start
            return jsonify(local_version=env.local_version, account=env.account, brain_mode="channelrecipe",
                           connected=True, readiness={"ready": True, "blockers": [], "checks": []},
                           api_route={"ready_to_switch": False}, claude_code={},
                           narration={"voice": "My narrator", "label": "My media account"})
        ready = env.mode not in ("needs_setup", "needs_update")
        blockers = [] if ready else ["A new Studio is out."] if env.mode == "needs_update" else ["Connect your narration account."]
        checks = [{"key": "release", "ready": False, "message": blockers[0]}] if env.mode == "needs_update" else []
        return jsonify(local_version=env.local_version, account=env.account, brain_mode="claudecode", connected=True,
                       readiness={"ready": ready, "blockers": blockers, "checks": checks},
                       claude_code={"allowance": {"reported_limit": True, "blocked": False}},
                       narration={"voice": "My narrator", "label": "My media account"})

    @app.post("/cr-claude/setup")
    def claude_setup():
        if env.claude_fails:
            return jsonify(error=env.claude_fails), 409
        action = request.json["action"]
        env.mode = "ready"
        return jsonify(verified=True, switched=action == "switch",
                       message="Switched: Studio now makes your videos with Claude Code." if action == "switch"
                       else "Both selected Claude models passed.")

    @app.get("/cr-diagnostics")
    def diagnostics():
        if request.headers.get("X-CR-Local-Token") != TOKEN:
            return jsonify(error="token"), 403
        return Response("ChannelRecipe Studio diagnostics\nStudio: synthetic", mimetype="text/plain")

    @app.get("/cr-voices")
    def voice_list():
        if env.mode == "voice_ids":                            # a voice model with no list (a voice ID on Atlas Cloud)
            return jsonify(connected=True, provider="atlas", provider_label="Atlas Cloud", model="elevenlabs/v3/text-to-speech",
                           model_label="ElevenLabs V3", voice=env.voice, default_voice="JBFqnCBsd6RMkjVDRZzb",
                           voices=[], groups={}, note="", previews=[])
        return jsonify(connected=True, provider="kie", provider_label="Kie.ai", model="google/gemini-3-8-flash-tts",
                       model_label="Gemini 3.8 Flash", voice=env.voice, default_voice="Kore",
                       voices=["Bodi", "Kore", "Charon", "Sulafat"], previews=[],
                       groups={"Bodi": "Tutor", "Kore": "Commercial voiceover", "Charon": "Call center", "Sulafat": "Tutor"},
                       note="Kie narrates with Gemini voices. For ElevenLabs voices, add your own ElevenLabs key.")

    @app.post("/cr-voices/preview")
    def voice_preview():
        if env.mode == "no_free_samples" and request.json.get("confirm_paid") is not True:
            return jsonify(needs_paid=True, voice=request.json["voice"], cost_usd=0.01,
                           message="Free sample unavailable: make one with your Kie.ai balance (about $0.01)?")
        return jsonify(url="/cr-voices/sample/" + request.json["voice"],
                       paid=env.mode == "no_free_samples")

    @app.get("/cr-voices/sample/<name>")
    def voice_sample(name):
        return Response(b"", mimetype="audio/mpeg")

    @app.post("/cr-voices/select")
    def voice_select():
        env.voice = request.json["voice"]
        return jsonify(saved=True, voice=env.voice, message="Your next videos use the " + env.voice + " voice.")

    @app.get("/cr-update")
    def update_status():
        return jsonify(env.update_state)

    @app.post("/cr-update/download")
    def update_download():
        env.update_state = {"state": "staged", "version": env.account["version"], "done": 1, "total": 1}
        return jsonify(env.update_state)

    @app.post("/cr-update/restart")
    def update_restart():
        env.update_state = {"state": "restarting"}
        return jsonify(env.update_state)

    @app.get("/cr-workspace/presenter-choice/<slug>")
    def presenter_choices(slug):
        return jsonify(stopped_on_presenter=slug == "presenter-film", current="again",
                       failed={"label": "Kie.ai", "reason": "Internal Error", "charged": 0,
                               "uncertain": getattr(env, "presenter_uncertain", False)}, choices=[
                           {"id": "skip", "label": "Finish without the presenter", "ready": True,
                            "note": "The video is finished as it is now, without the presenter intro."},
                           {"id": "again", "label": "Try the presenter again", "ready": True,
                            "note": "Best after waiting a few minutes."}])

    @app.post("/cr-workspace/presenter-choice/<slug>")
    def presenter_pick(slug):
        return jsonify(saved=True, choice=request.json["choice"])

    @app.get("/cr-workspace/stopped")
    def stopped_projects():
        if env.mode == "presenter":
            return jsonify(projects=[{"slug": "presenter-film", "title": "The Heist", "style": "documentary",
                                      "state": "stopped", "mtime": 1, "stopped_on": "presenter"}])
        if env.mode == "claude_stage":
            return jsonify(projects=[{"slug": "stage-film", "title": "The Vault", "style": "documentary",
                                      "state": "stopped", "mtime": 1}])
        return jsonify(projects=[])

    # cr_recover: a video stopped on a Claude step (one honest yes), and a stopped twin of a new Create
    env.recovery = {"stopped_on": "claude_stage", "needs_approval": True, "key": "k" * 12, "fix_first": [],
                    "do_first": [], "media": [], "claude_stages": [{
                        "stage_id": "cc_" + "a" * 64, "step": "a Claude step that looks at pictures (Opus)",
                        "status": "failed", "error_code": "usage_limit", "needs_approval": True,
                        "reason": "Claude stopped at your plan's usage limit.",
                        "spent_note": "Whether Claude counts a refusal at the limit against your usage isn't confirmed.",
                        "reset_label": "26 Oct, 03:00 PM (Europe/London)", "reset_passed": False}],
                    "reset_label": "26 Oct, 03:00 PM (Europe/London)", "reset_passed": False,
                    "auto": {"safe": False, "why": "", "count": 0, "limit": 2}}

    @app.get("/cr-workspace/recovery/<slug>")
    def recovery(slug):
        if slug != "stage-film":
            return jsonify(error="This isn't a saved video of this Studio."), 404
        return jsonify(env.recovery)

    @app.post("/cr-workspace/recovery/<slug>")
    def recover(slug):
        if (request.json or {}).get("when") == "after_reset":
            return jsonify(scheduled=True, message="Studio carries this video on by itself after the reset.")
        return jsonify(approved={"approved_stages": ["cc_" + "a" * 64], "set_aside": []}, resumed=True,
                       message="Carrying on from where it stopped. Everything already made is reused.")
    HELD = "Your Kie.ai balance ran out. Top it up, then Carry on."

    @app.get("/cr-queue")
    def queue_status():
        held = [{"slug": "held-film", "title": "Titanic", "ranking": False, "reason": HELD}] if env.mode == "queue_held" else []
        return jsonify(held=held, reason=HELD if held else "", code="balance" if held else "", scope="all" if held else "",
                       waiting=1 if env.mode == "queue" else 0)

    @app.post("/cr-queue/resume")
    def queue_resume():                                        # Carry on: Studio checked them again, still blocked
        still = "Connect ElevenLabs in Setup & accounts for this recipe's voice."
        return jsonify(resumed=0, held=[{"slug": "held-film", "title": "Titanic", "reason": still}], reason=still,
                       code="setup", scope="", checking=False, waiting=0)

    @app.get("/api/queue")
    def queue():
        if env.mode in ("queue", "queue_held"):
            # cr_queue's rows: the running one, each waiting one's place in line, then the held ones with why
            rows = [{"slug": "active-film", "title": "A film in production", "style": "3d", "state": "running"}]
            if env.mode == "queue":
                rows.append({"slug": "queued-film", "title": "Moon landing", "state": "queued", "position": 1, "ahead": 1})
            else:
                rows = [{"slug": "held-film", "title": "Titanic", "state": "held", "reason": HELD}]
            return jsonify(busy=env.mode == "queue", jobs=rows)
        if env.mode == "presenter":
            return jsonify(busy=False, jobs=[])
        if env.mode == "voice":
            return jsonify(busy=False, jobs=[{"slug": "voice-film", "title": "The Heist", "state": "error"}])
        return jsonify(busy=env.mode == "active", jobs=[{"slug": "active-film", "title": "A film in production",
                       "style": "3d", "state": "running"}] if env.mode == "active" else [
                       {"slug": "stopped-film", "title": "Saved project needs review", "state": "error"}])

    @app.get("/cr-workspace/activity")
    def activity():
        return jsonify(busy=env.busy or env.mode in ("active", "queue"), operations=getattr(env, "studies", []),
                       other_account=getattr(env, "other_studies", 0))

    @app.get("/f/video/<slug>")
    def finished_video(slug):                              # a test that sets env.video_file plays a real film
        film = getattr(env, "video_file", None)
        return send_file(film, mimetype="video/webm", conditional=True) if film else Response(status=404)

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

    @app.get("/cr-workspace/project/<slug>")
    def saved_project(slug):
        return jsonify(slug=slug, project_id="crv_" + "9" * 24,
                       available_for_resume=slug in ("voice-film", "presenter-film", "stage-film"),
                       title="The Heist", status="error")

    @app.get("/cr-workspace/voice/<slug>")
    def voice_choices(slug):
        return jsonify(stopped_on_voice=slug == "voice-film", has_voice=False, current="again",
                       failed={"label": "Kie.ai", "reason": "Internal Error, Please try again later", "charged": 0},
                       script="The vault opened at midnight.", choices=[
                           {"id": "again", "label": "Try the same voice again", "ready": True, "note": "The voice in Setup & accounts, once more."},
                           {"id": "gemini-man", "label": "Gemini voice through Kie (a man's voice)", "ready": True, "note": "The same Kie account, a different voice service."},
                           {"id": "gemini-woman", "label": "Gemini voice through Kie (a woman's voice)", "ready": True, "note": "The same Kie account."},
                           {"id": "elevenlabs", "label": "Your own ElevenLabs account", "ready": False, "setup": "/cr-setup",
                            "note": "Add your ElevenLabs key in Setup & accounts first (paste it there, never in a chat)."},
                           {"id": "own", "label": "Your own recording", "ready": True, "note": "Read the script aloud and upload the recording."}])

    @app.post("/cr-workspace/voice/<slug>")
    def voice_pick(slug):
        return jsonify(saved=True, choice=request.json["choice"])

    @app.post("/cr-submit-project")
    def submit_saved():
        return jsonify(queued=[{"slug": "voice-film"}], project_id=request.json["project_id"])

    @app.get("/api/status/<slug>")
    def progress(slug):
        if slug in ("presenter-film", "stage-film"):
            return jsonify(error="no live job"), 404
        if slug in ("queued-film", "held-film") or env.mode in ("queue", "queue_held") and slug.startswith("fresh-"):
            return jsonify(state="held" if slug == "held-film" or env.mode == "queue_held" else "queued", done=False,
                           lines=[], progress=0, error=None, cr_cancel={"can_cancel": slug == "queued-film"})
        if slug == "voice-film":
            return jsonify(state="error", done=True, lines=["Writing the voiceover"], progress=0.08, elapsed=60,
                           error="Kie.ai failed this narration task. Internal Error, Please try again later",
                           cr_cancel={"can_cancel": False})
        active = slug == "active-film"
        return jsonify(state="running" if active else "done", done=not active,
                       lines=(env.status_lines or env.active_lines) if active else ["published: synthetic film"],
                       progress=0.425 if active else 1, elapsed=125, error="",
                       cr_cancel={"can_cancel": active, "run_id": "exact-active-run" if active else ""})

    @app.get("/api/result/<slug>")
    def result(slug):
        return jsonify(video=True, audio=True, script=True, srt=True, desc=True, thumbs=["cover.png"])

    @app.post("/api/run")
    def run():
        if env.mode == "selected_account_missing":
            return jsonify(error="Selected options need attention.", blockers=["Your image account needs attention."]), 409
        if env.mode == "twin" and not request.json.get("new_attempt"):
            return jsonify(error="\u201cThe Vault\u201d stopped on 2026-10-26 08:10 with its work saved.",
                           resume_first=[{"title": "The Vault", "slug": "stage-film", "id": "video:stage-film",
                                          "project_id": "crv_" + "9" * 24, "stopped": "2026-10-26 08:10"}],
                           others=[]), 409
        slug = "fresh-" + str(len(env.creates) + 1)
        env.creates.append({"slug": slug, "body": request.json})
        place = {"position": 2, "ahead": 2} if env.mode == "queue" else {"held": HELD} if env.mode == "queue_held" else {}
        return jsonify(queued=[{"slug": slug, "title": request.json["title"], **place}])

    @app.get("/cr-cloud")
    def cloud():
        return jsonify(env.cloud or {"available": False})

    @app.post("/cr-cloud/start")
    def cloud_start():
        env.cloud_starts.append(request.json)
        return jsonify(started=True), 202

    @app.post("/cr-cloud/quote")
    def cloud_quote():
        body, balance = request.json or {}, float((env.cloud or {}).get("balance_usd") or 0)
        gpu, titles = bool((body.get("features") or {}).get("threed")), int(body.get("titles") or 1)
        most = round((4.8 if gpu else 1.68) * titles, 2)
        short = round(max(0.0, most - balance), 2)
        return jsonify(available=True, machine="gpu" if gpu else "cpu", cents_per_min=4 if gpu else 2, titles=titles,
                       label="Cloud machine", expected_usd=round((1.6 if gpu else 0.74) * titles, 2),
                       most_reserved_usd=most, balance_usd=balance, short_by_usd=short, chunks_usd=[5, 10, 25, 50],
                       topup_usd=next((c for c in (5, 10, 25, 50) if c >= short), 50) if short else 0)

    @app.post("/cr-cloud/topup")
    def cloud_topup():
        env.topups.append(request.json["usd"])
        return jsonify(url="about:blank#checkout")

    @app.get("/cr-cloud/progress")
    def cloud_progress():
        return jsonify(state="preparing", title="Cloud film", message="Writing the script…", error="", balance_usd=9.5,
                       lines=["queued"], farm="", videos=[{"slug": "done-film", "title": "Done film",
                                                           "video": "Done film.mp4"}])

    @app.get("/api/characters")
    def characters():
        return jsonify(characters=env.characters)

    @app.post("/cr-workspace/character/draw")
    def character_draw():
        env.drawn.append(request.json)
        row = {"rel": "characters/mia-a-woman-abc123.png", "file": "mia-a-woman-abc123.png", "label": "mia a woman",
               "src": "/charimg/mia-a-woman-abc123.png", "text": request.json["description"]}
        env.characters.append(row)
        return jsonify(character=row)

    @app.post("/cr-workspace/character")
    def character_upload():
        assert request.files["file"].read()
        row = {"rel": "characters/my-host-def456.png", "file": "my-host-def456.png", "label": "my host",
               "src": "/charimg/my-host-def456.png", "text": "THE CAST: the main character is exactly the character in "
               "the character reference image."}
        return jsonify(character=row)

    @app.get("/char/<style>")
    @app.get("/charimg/<name>")
    def character_picture(style=None, name=None):
        return Response(image.getvalue(), mimetype="image/jpeg")

    # Delete into Studio's trash (cr_trash): what goes and what stays, then Restore
    env.trash, env.trashed = [], {}

    def item_title(kind, name):
        if kind == "style":
            return next((r["label"] for r in catalog["channels"] if r["name"] == name), name)
        return next((j["title"] for j in env.finished_jobs if j["slug"] == name), name)

    @app.get("/cr-workspace/delete/<kind>/<name>")
    def delete_plan(kind, name):
        goes = ["The style “My Illustrated Worlds”: its look, prompts and settings, from your Style library"] \
            if kind == "style" else ["The finished video: A finished film.mp4 (12 MB)", "Its working files (40 MB)"]
        kept = ["Videos you made with it stay in Projects"] if kind == "style" else ["Your voice library and saved recordings"]
        return jsonify(kind=kind, id=name, title=item_title(kind, name), goes=goes, kept=kept, keep_days=30, blocked="")

    @app.post("/cr-workspace/delete/<kind>/<name>")
    def delete_item(kind, name):
        entry = f"{kind[0]}20261025-120000-{len(env.trash):06x}"
        title = item_title(kind, name)
        if kind == "style":
            env.trashed[entry] = ("style", [r for r in catalog["channels"] if r["name"] == name])
            catalog["channels"] = [r for r in catalog["channels"] if r["name"] != name]
        else:
            env.trashed[entry] = ("video", [j for j in env.finished_jobs if j["slug"] == name])
            env.finished_jobs = [j for j in env.finished_jobs if j["slug"] != name]
        env.trash.insert(0, {"id": entry, "kind": kind, "name": title, "deleted_at": 1793000000, "days_left": 29})
        return jsonify(deleted=True, kind=kind, id=name, title=title, trash_id=entry, goes=[], kept=[])

    @app.get("/cr-workspace/trash")
    def trash():
        return jsonify(entries=env.trash, keep_days=30)

    @app.post("/cr-workspace/trash/<entry>/restore")
    def restore(entry):
        kind, rows = env.trashed.pop(entry)
        if kind == "style":
            catalog["channels"] += rows
        else:
            env.finished_jobs += rows
        row = next(r for r in env.trash if r["id"] == entry)
        env.trash.remove(row)
        return jsonify(restored=True, kind=kind, title=row["name"])

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


def test_captions_start_at_the_recipes_own_choice_and_asking_for_them_is_sent(workspace):
    """Alex: a cloned style without captions showed "Burn captions into the video" on, and the video came out with
    none. The switch now starts at the recipe's own choice and says so; switching it on asks for them."""
    env, page = workspace, workspace.page
    env.open("?cr-preset=" + CLONE)
    page.locator("summary", has_text="Captions & publishing").click()
    assert not page.locator("#crw-captions").is_checked()
    assert "This style has no captions, like its channel. Switch this on" in page.inner_text("#crw-captions-note")
    page.locator("#crw-captions").check()
    assert "They will be added to this video." in page.inner_text("#crw-captions-note")
    page.locator("#crw-title").fill("A wargame battle")
    page.locator("#crw-create").click()
    page.wait_for_function("!window.CR_WORKSPACE_CLIENT.state.submitting && window.CR_WORKSPACE_CLIENT.state.page==='projects'")
    assert env.creates[0]["body"]["burn_subs"] is True and env.creates[0]["body"]["cr_captions"] == "on"
    page.locator(".crw-nav [data-nav=create]").click()
    page.evaluate("window.CR_WORKSPACE_CLIENT.selectRecipe(window.CR_WORKSPACE_CLIENT.state.catalog.channels.find(r => r.name === 'documentary'))")
    assert page.locator("#crw-captions").is_checked() and page.locator("#crw-captions-note").is_hidden()
    page.evaluate("window.CR_WORKSPACE_CLIENT.selectRecipe(window.CR_WORKSPACE_CLIENT.state.catalog.channels.find(r => r.name === %s))"
                  % json.dumps(CLONE))
    assert page.locator("#crw-captions").is_checked(), "the creator's choice for that recipe is kept"


def test_a_saved_captions_off_survives_reloads(workspace):
    """A creator who switched captions off before Studio 106 (one switch for every recipe) keeps it off after the
    page saves its draft again and reloads (review of bugs-106, fix 4)."""
    env, page = workspace, workspace.page
    env.open()
    page.evaluate("localStorage.setItem('channelrecipe.workspace.v1', JSON.stringify({recipe: 'documentary', "
                  "captions: false}))")
    for _ in range(2):
        env.open()
        page.locator("summary", has_text="Captions & publishing").click()
        assert not page.locator("#crw-captions").is_checked()
        page.locator("#crw-title").fill("Saved again")
        page.locator("#crw-title").dispatch_event("change")              # the page saves its draft
        saved = page.evaluate("JSON.parse(localStorage.getItem('channelrecipe.workspace.v1'))")
        assert saved["captions"] is False


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


def test_a_vertical_short_plays_whole_in_its_card_and_gets_a_tall_player(workspace, tmp_path):
    """Altt and Mac7834 (2026-10-29): Studio showed a vertical Short with the wrong aspect ratio (a 16:9 card cropped
    it to a wide band of its middle); downloaded, the file was right. The card now shows it whole, and the video
    dialog's player is tall for a tall video."""
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg makes the test film")
    film = tmp_path / "tall.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x415573:s=180x320:d=1:r=10",
                    "-c:v", "libvpx-vp9", "-b:v", "50k", str(film)], check=True, timeout=60)
    env, page = workspace, workspace.page
    env.video_file = str(film)
    env.open("#projects")
    player = page.locator("#crw-projects-list video.crw-video-preview")
    assert player.evaluate("v=>getComputedStyle(v).objectFit") == "contain", "never cropped to the card's 16:9"
    page.get_by_role("button", name="Video & exports", exact=True).click()
    page.locator("#crw-output").wait_for(state="visible")
    page.wait_for_function("document.querySelector('#crw-output-video').classList.contains('is-tall')")
    box = page.locator("#crw-output-video").bounding_box()
    assert box["height"] > box["width"] * 1.5, box
    page.locator("#crw-output-close").click()
    page.wait_for_function("!document.querySelector('#crw-output-video').classList.contains('is-tall')")
    assert not env.errors


def test_research_footage_says_what_its_watching_adds_and_progress_counts_the_videos(workspace):
    """A customer's 10-minute video watched YouTube clips for 22 extra hours (2026-10-25): Create says what Research
    footage adds for the length chosen, and the progress names how far the watching is."""
    env, page = workspace, workspace.page
    env.open("?cr-preset=documentary")
    note = page.locator("#crw-footage-note")
    note.wait_for(state="visible")
    ten = cr_watch.budget(10)
    assert f"about {ten['typical_minutes'][0]} to {ten['typical_minutes'][1]} minutes to a 10-minute video" in note.inner_text()
    assert f"up to {ten['videos']} YouTube videos" in note.inner_text()
    # Alex: Kie was his only watcher and it was down; Create says what happens then, and what happened last time
    assert "If Kie's video watcher isn't answering, ChannelRecipe's own watcher takes over" in note.inner_text()
    assert "never your credits" in note.inner_text() and "watched 12 of its videos" in note.inner_text()
    page.locator("[data-minutes='20']").click()
    assert f"up to {cr_watch.budget(20)['videos']} YouTube videos" in note.inner_text()
    if os.environ.get("CR_SHOT"):
        page.locator(".crw-video-summary").screenshot(path=os.environ["CR_SHOT"])
    page.locator("summary:has-text('Visual sources')").click()
    page.locator("#crw-features [data-feature='youtube']").uncheck()
    assert note.is_hidden(), "no footage, no watching"
    env.mode = "active"
    env.active_lines = ["YouTube: 40 searches", "  YouTube: watched 12 of 42 videos (5 min so far; this video stops "
                        "watching at 40 min)"]
    env.open("#projects")
    page.get_by_role("button", name="View progress", exact=True).click()
    page.wait_for_function("document.querySelector('#crw-progress-phase').textContent.includes('12 of 42')")
    assert page.locator("#crw-progress-phase").inner_text() == "Watching YouTube clips (12 of 42)"


def test_gallery_works_with_keyboard_and_a_mobile_viewport(workspace):
    env, page = workspace, workspace.page
    page.set_viewport_size({"width": 390, "height": 844})
    env.open()
    assert page.evaluate("document.documentElement.scrollWidth") == 390
    page.locator("#crw-recipe-open").focus()
    page.keyboard.press("Enter")
    assert page.locator("#crw-gallery-search").evaluate("e=>e===document.activeElement")
    page.locator("#crw-gallery-search").fill("3D Reconstruction")
    cards = page.locator("#crw-gallery-grid .crw-recipe-card")
    assert cards.count() == 1
    cards.first.focus()
    page.keyboard.press("Enter")
    page.locator("#crw-detail-use").focus()
    assert page.locator("#crw-detail-use").evaluate("e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}")
    page.keyboard.press("Enter")
    assert page.locator("#crw-gallery").is_hidden()
    assert page.locator("#crw-selected-name").inner_text() == "3D Reconstruction"
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


def test_updates_are_explained_and_mentioned_before_discord(workspace):
    workspace.local_version = "5.3-admin.51"
    workspace.account = {"version": "5.3-admin.52", "changelog": [
        {"build": 52, "title": "Faster thumbnails", "items": ["Thumbnails load faster."]},
        {"build": 51, "title": "Already installed", "items": ["Something this Studio already has."]}]}
    workspace.open()
    page = workspace.page
    page.wait_for_selector("#crw-update:not([hidden])")
    assert "Studio 52 is available" in page.inner_text("#crw-update")
    assert "keeps your projects" in page.inner_text("#crw-update")
    # The first Discord click shows what the update fixes instead of leaving.
    page.click("a[data-discord='sidebar']")
    assert page.locator("#crw-whatsnew").is_visible()
    assert "Before you ask" in page.inner_text("#crw-whatsnew-title")
    listed = page.inner_text("#crw-whatsnew-list")
    assert "Thumbnails load faster." in listed and "already has" not in listed
    assert page.locator("#crw-whatsnew-discord").is_visible()
    page.click("#crw-whatsnew-update")
    page.wait_for_function("document.querySelector('#crw-update').textContent.includes('The update is ready')")
    assert any(post["path"] == "/cr-update/download" for post in workspace.posts)
    page.click("#crw-update .crw-primary-button")
    page.wait_for_function("document.querySelector('#crw-update').textContent.includes('Restarting')")


def test_a_required_update_says_how_and_its_banner_cannot_be_put_off(workspace):
    workspace.mode = "needs_update"
    workspace.local_version = "5.3-admin.51"
    workspace.account = {"version": "5.3-admin.52", "changelog": [{"build": 52, "title": "Faster thumbnails", "items": ["x"]}]}
    workspace.open()
    page = workspace.page
    page.wait_for_selector("#crw-update:not([hidden])")
    assert "keep making new videos" in page.inner_text("#crw-update")
    assert "Update now" in page.inner_text("#crw-readiness") and "download" not in page.inner_text("#crw-readiness").lower()
    page.click("#crw-update .crw-icon-button")              # "Remind me later"
    page.reload(); page.wait_for_selector("#crw-update:not([hidden])")
    assert page.locator("#crw-update").is_visible(), "while an update is required the way out stays on screen"


def test_no_update_banner_when_studio_is_current(workspace):
    workspace.local_version = "5.3-admin.52"
    workspace.account = {"version": "5.3-admin.52", "changelog": []}
    workspace.open()
    assert workspace.page.locator("#crw-update").is_hidden()
    assert not any(post["path"].startswith("/cr-update") for post in workspace.posts)


def test_choose_a_narration_voice_after_hearing_it(workspace):
    workspace.open()
    page = workspace.page
    assert page.locator("#crw-voices").is_hidden(), "the picker is closed until asked for"
    page.click("#crw-voice-open")
    page.wait_for_selector("#crw-voices[open] .crw-voice-row")
    rows = page.locator(".crw-voice-row strong").all_inner_texts()
    assert rows[0] == "Kore", "the current voice comes first"
    assert "Informative · Call center" in page.inner_text("#crw-voices-list"), "voices say what they sound like"
    note = page.inner_text("#crw-voices-note")
    assert note.startswith("Kie narrates with Gemini voices.") and "your own ElevenLabs key" in note
    assert "free" in note and "tiny amount" not in note, "Gemini previews are ChannelRecipe's free samples"
    page.fill("#crw-voices-search", "warm")
    assert page.locator(".crw-voice-row strong").all_inner_texts() == ["Sulafat"]
    page.fill("#crw-voices-search", "")
    page.click("button[aria-label='Preview Charon']")
    page.wait_for_function("document.getElementById('crw-voices-audio').src.endsWith('/cr-voices/sample/Charon')")
    assert any(post["path"] == "/cr-voices/preview" and post["body"] == {"voice": "Charon"} for post in workspace.posts)
    page.locator(".crw-voice-row", has_text="Charon").locator("button", has_text="Use").click()
    page.wait_for_function("document.getElementById('crw-narration-name').textContent === 'Charon'")
    assert workspace.voice == "Charon"
    assert "Charon" in page.inner_text("#crw-voices-status")


def test_a_paid_voice_sample_is_made_only_after_the_creator_says_yes_to_its_cost(workspace):
    """channelrecipe.com's free samples couldn't be fetched: the dialog names the cost and waits for a yes, voice by
    voice; another voice clicked meanwhile pays nothing (review of 1f91260, blocker 1)."""
    env, page = workspace, workspace.page
    env.mode = "no_free_samples"
    env.open()
    page.click("#crw-voice-open")
    page.wait_for_selector("#crw-voices[open] .crw-voice-row")
    page.click("button[aria-label='Preview Charon']")
    page.wait_for_selector("[data-paid-sample='Charon']")
    assert "Free sample unavailable: make one with your Kie.ai balance (about $0.01)?" in page.inner_text("#crw-voices-status")
    page.click("button[aria-label='Preview Sulafat']")
    page.wait_for_selector("[data-paid-sample='Sulafat']")
    previews = [p["body"] for p in env.posts if p["path"] == "/cr-voices/preview"]
    assert previews == [{"voice": "Charon"}, {"voice": "Sulafat"}], "no yes, no paid sample"
    page.click("[data-paid-sample='Sulafat']")
    page.wait_for_function("document.getElementById('crw-voices-audio').src.endsWith('/cr-voices/sample/Sulafat')")
    assert [p["body"] for p in env.posts if p["path"] == "/cr-voices/preview"][-1] == {"voice": "Sulafat", "confirm_paid": True}


def test_choose_voice_never_sends_a_connected_creator_to_setup(workspace):
    """Alex (Studio 104): Choose voice with a voice model that has no list sent him back to the setup page. A model
    without a list now takes a voice ID in the dialog itself."""
    env, page = workspace, workspace.page
    env.mode, env.voice = "voice_ids", "JBFqnCBsd6RMkjVDRZzb"
    env.open()
    page.click("#crw-voice-open")
    page.wait_for_selector("#crw-voices[open] #crw-voices-id")
    assert page.url.endswith("/") or "/cr-setup" not in page.url
    assert "paste a voice ID" in page.inner_text("#crw-voices-note") and page.locator("#crw-voices-search").is_hidden()
    page.fill("#crw-voices-id", "pNInz6obpgDQGcFmaJgB")
    page.locator("#crw-voices-list button", has_text="Use").click()
    page.wait_for_function("document.getElementById('crw-narration-name').textContent === 'pNInz6obpgDQGcFmaJgB'")
    assert {"path": "/cr-voices/select", "body": {"voice": "pNInz6obpgDQGcFmaJgB"}} in env.posts
    assert "/cr-setup" not in page.url


def test_copy_diagnostics_for_support(workspace):
    workspace.open()
    page = workspace.page
    page.click("#crw-copy-diagnostics")
    page.wait_for_function("document.getElementById('crw-diagnostics-status').textContent.startsWith('Copied')")
    assert "Discord #help" in page.inner_text("#crw-diagnostics-status")


def test_the_tab_says_what_the_studio_is_making(workspace):
    workspace.mode = "active"
    workspace.open()
    page = workspace.page
    page.wait_for_function("document.title.startsWith('Making')")
    assert "A film in production" in page.title()
    assert page.get_attribute("#crw-favicon", "href") == "/cr-workspace/icon.svg?state=busy"


def test_the_tab_is_plain_when_nothing_is_running(workspace):
    workspace.open()
    assert workspace.page.title() == "ChannelRecipe · Studio"
    assert workspace.page.get_attribute("#crw-favicon", "href") == "/cr-workspace/icon.svg"


def test_studio_cloud_renders_on_a_channelrecipe_machine_when_chosen(workspace):
    env, page = workspace, workspace.page
    env.open()
    assert page.locator("#crw-cloud-choice").is_hidden(), "no Studio Cloud on the account: no choice to make"
    env.cloud = {"available": True, "balance_usd": 10, "machines": {"cpu": {"price_per_hr": 1.2}}}
    env.open()
    page.wait_for_selector("#crw-cloud-choice:not([hidden])")
    page.locator("[data-render-on=cloud]").click()
    page.wait_for_function("document.getElementById('crw-cloud-note').textContent.includes('usually about')")
    note = page.locator("#crw-cloud-note").inner_text()
    assert page.locator("#crw-summary-renderer").inner_text() == "Studio Cloud", "the summary says where it renders"
    assert "$1.20 an hour" in note and "Balance: $10.00" in note and "you pay only what it used" in note
    assert "up to $1.68" in note and page.locator("#crw-cloud-topup").is_hidden(), "the balance covers it"
    page.locator("#crw-title").fill("Cloud film")
    page.locator("#crw-create").click()
    page.wait_for_function("window.CR_WORKSPACE_CLIENT.state.page==='projects'")
    assert len(env.cloud_starts) == 1 and env.cloud_starts[0]["style"] == "documentary"
    assert env.cloud_starts[0]["title"] == "Cloud film" and not env.creates, "it never also renders here"
    assert page.evaluate("document.getElementById('crw-summary-renderer').textContent") in ("Studio Cloud", "Local render")
    page.wait_for_selector("#crw-cloud-panel:not([hidden])")
    assert "Writing the script" in page.locator("#crw-cloud-status").inner_text()
    assert page.locator("#crw-cloud-videos a").get_attribute("href") == "/api/farm/file/done-film/Done%20film.mp4"

    page.locator(".crw-nav [data-nav=create]").click()
    page.locator("[data-render-on=local]").click()
    page.locator("[data-minutes='20']").click() if page.locator("[data-minutes='20']").count() else page.fill("#crw-minutes", "20")
    page.evaluate("document.getElementById('crw-minutes').dispatchEvent(new Event('input',{bubbles:true}))")
    assert "keeps this computer busy" in page.locator("#crw-cloud-note").inner_text(), "a long video: the cloud is suggested"

    env.cloud["machines"]["gpu"] = {"price_per_hr": 2.4}
    env.open("?cr-preset=3d")
    page.wait_for_selector("#crw-cloud-choice:not([hidden])")
    assert page.locator("[data-render-on=cloud]").is_enabled(), "3D renders on the cloud's graphics card"
    page.locator("[data-render-on=cloud]").click()
    note = page.locator("#crw-cloud-note").inner_text()
    assert "graphics card" in note and "$2.40 an hour" in note


def test_a_video_whose_voice_stopped_offers_other_voices_and_carries_on(workspace):
    """Kie's ElevenLabs voice failed three times and the creator could only wait: the stopped project now offers the
    same voice again, Gemini through Kie, their ElevenLabs account (Setup first) or their own recording."""
    env, page = workspace, workspace.page
    env.mode = "voice"
    env.open("#projects")
    page.get_by_role("button", name="Review issue", exact=True).click()
    box = page.locator(".crw-voice-choices")
    box.wait_for(state="visible")
    assert "Kie.ai couldn't record this video's voice" in box.inner_text()
    assert page.get_by_role("button", name="Your own ElevenLabs account").is_disabled()
    assert box.get_by_role("link", name="Open Setup").get_attribute("href") == "/cr-setup"
    page.locator(".crw-voice-script summary").click()
    assert "The vault opened at midnight." in page.locator(".crw-voice-script pre").inner_text()
    if os.environ.get("CR_SHOT"):
        page.locator("#crw-progress").screenshot(path=os.environ["CR_SHOT"])
    page.get_by_role("button", name="Gemini voice through Kie (a woman's voice)").click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('new voice')")
    assert [p for p in env.posts if p["path"].startswith(("/cr-workspace/voice", "/cr-submit"))] == [
        {"path": "/cr-workspace/voice/voice-film", "body": {"choice": "gemini-woman"}},
        {"path": "/cr-submit-project", "body": {"project_id": "crv_" + "9" * 24}}]


def test_a_video_whose_presenter_stopped_finishes_without_it_from_projects(workspace):
    """Kie answered 500 on a presenter intro and stopped a finished video (2026-10-25). Its film is on disk, so the
    library lists it, but it stays a stopped project offering to finish without the presenter or to try again."""
    env, page = workspace, workspace.page
    env.mode = "presenter"
    env.finished_jobs = [{"slug": "presenter-film", "title": "The Heist", "style": "documentary", "editable": True}]
    env.open("#projects")
    card = page.locator('.crw-project-card[data-slug="presenter-film"]')
    card.get_by_role("button", name="Review issue", exact=True).click()
    box = page.locator(".crw-voice-choices")
    box.wait_for(state="visible")
    assert "Kie.ai couldn't make this video's presenter" in box.inner_text()
    assert "The rest of the video is finished and saved" in box.inner_text()
    page.get_by_role("button", name="Finish without the presenter").click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('without its presenter')")
    assert [p for p in env.posts if p["path"].startswith(("/cr-workspace/presenter-choice", "/cr-submit"))] == [
        {"path": "/cr-workspace/presenter-choice/presenter-film", "body": {"choice": "skip"}},
        {"path": "/cr-submit-project", "body": {"project_id": "crv_" + "9" * 24}}]


def test_trying_an_uncertain_presenter_again_asks_first_because_it_can_pay_twice(workspace):
    """Its last attempt got no clear answer (sent, no task ID back): that task may have started, so trying again could
    pay for a second presenter. One click is never enough: the creator confirms, having read why (review, 2026-10-26)."""
    env, page = workspace, workspace.page
    env.mode, env.presenter_uncertain = "presenter", True
    env.finished_jobs = [{"slug": "presenter-film", "title": "The Heist", "style": "documentary", "editable": True}]
    env.open("#projects")
    page.locator('.crw-project-card[data-slug="presenter-film"]').get_by_role("button", name="Review issue",
                                                                             exact=True).click()
    page.locator(".crw-voice-choices").wait_for(state="visible")
    asked = []
    page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
    page.get_by_role("button", name="Try the presenter again").click()
    page.wait_for_function("true")
    assert asked and "pays for a second presenter" in asked[0] and "Kie.ai" in asked[0]
    assert not [p for p in env.posts if p["path"].startswith(("/cr-workspace/presenter-choice", "/cr-submit"))]
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Try the presenter again").click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('Trying the presenter again')")
    assert [p for p in env.posts if p["path"].startswith(("/cr-workspace/presenter-choice", "/cr-submit"))] == [
        {"path": "/cr-workspace/presenter-choice/presenter-film", "body": {"choice": "again"}},
        {"path": "/cr-submit-project", "body": {"project_id": "crv_" + "9" * 24}}]


def test_a_video_stopped_on_a_claude_step_offers_one_honest_yes_on_its_card(workspace):
    """A failed Claude step was a dead end outside a hidden setup box (ALEX, 2026-10-26). The stopped card says what
    stopped it and what trying again may use, and only a ticked yes allows exactly those attempts and carries on."""
    env, page = workspace, workspace.page
    env.mode = "claude_stage"
    env.open("#projects")
    page.locator('.crw-project-card[data-slug="stage-film"]').get_by_role("button", name="Review issue",
                                                                         exact=True).click()
    box = page.locator(".crw-recovery")
    box.wait_for(state="visible")
    said = box.inner_text()
    assert "Try the stopped steps again?" in said and "usage limit" in said and "26 Oct, 03:00 PM" in said
    assert page.locator(".crw-resume-saved").is_hidden(), "a plain Resume would stop at the same step again"
    now = page.get_by_role("button", name="Try now: I added Claude usage")
    later = page.get_by_role("button", name="Carry on by itself after 26 Oct, 03:00 PM (Europe/London)")
    assert now.is_disabled() and later.is_disabled()
    assert not [p for p in env.posts if p["path"].startswith("/cr-workspace/recovery")]
    box.locator("input[type=checkbox]").check()
    if os.environ.get("CR_SHOT"):
        page.locator("#crw-progress").screenshot(path=os.environ["CR_SHOT"])
    later.click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('after the reset')")
    assert [p for p in env.posts if p["path"].startswith("/cr-workspace/recovery")] == [
        {"path": "/cr-workspace/recovery/stage-film", "body": {"acknowledge_costs": True, "key": "k" * 12,
                                                               "when": "after_reset"}}]


def test_create_offers_to_carry_on_a_stopped_video_of_the_same_title_first(workspace):
    """Starting the same title again made a new project and paid for every step again (6 of 26 failed videos). Create
    now offers to carry the stopped one on; a brand-new video only when the creator insists."""
    env, page = workspace, workspace.page
    env.mode = "twin"
    env.open()
    page.locator("#crw-title").fill("The Vault")
    asked = []
    page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.accept()))
    page.get_by_role("button", name="Create video").click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('The Vault')")
    assert asked and "stopped earlier with its work saved" in asked[0]
    assert [p for p in env.posts if p["path"].startswith("/cr-workspace/recovery")] == [
        {"path": "/cr-workspace/recovery/stage-film", "body": {"only_if_free": True}}]
    assert env.creates == [], "no new project"
    env.open()
    page.locator("#crw-title").fill("The Vault")
    answers = iter([False, True])
    page.on("dialog", lambda dialog: dialog.accept() if next(answers) else dialog.dismiss())
    page.get_by_role("button", name="Create video").click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('new video project')")
    assert [c["body"].get("new_attempt") for c in env.creates] == [True]


def test_a_new_character_is_drawn_or_uploaded_and_goes_with_the_video(workspace):
    """A customer (Discord, 2026-10-24) couldn't change the character in Creative direction: new words now draw the
    character (or a picture is uploaded) and that picture goes with the video."""
    env, page = workspace, workspace.page
    env.open("?cr-preset=stories")
    page.locator("summary:has-text('Creative direction')").click()
    assert page.locator("#crw-character").input_value() == STORY_CAST, "the recipe's own words, as written"
    page.wait_for_function("!document.getElementById('crw-character-art').hidden")
    assert page.locator("#crw-character-art").get_attribute("src") == "/char/stories"
    assert page.locator("#crw-character-draw").is_disabled()
    assert "The recipe’s own character" in page.locator("#crw-character-status").inner_text()
    mia = "Mia, a woman in her thirties with long red hair and a green raincoat."
    page.locator("#crw-character").fill(mia)
    assert "draws your character from these words" in page.locator("#crw-character-status").inner_text()
    assert page.locator("#crw-character-art").is_hidden(), "the recipe's man is no longer shown as the character"
    page.locator("#crw-character-draw").click()
    page.wait_for_function("document.getElementById('crw-character-image').value==='characters/mia-a-woman-abc123.png'")
    assert env.drawn == [{"style": "stories", "description": mia}]
    assert page.locator("#crw-character-art").get_attribute("src") == "/charimg/mia-a-woman-abc123.png"
    page.locator("#crw-title").fill("A rainy year")
    page.locator("#crw-create").click()
    page.wait_for_function("!window.CR_WORKSPACE_CLIENT.state.submitting && window.CR_WORKSPACE_CLIENT.state.page==='projects'")
    body = env.creates[0]["body"]
    assert body["character"] == mia and body["character_image"] == "characters/mia-a-woman-abc123.png"
    assert mia not in body["extra"]
    # or a picture of their own: its words replace the recipe's, so words and picture agree
    page.locator(".crw-nav [data-nav=create]").click()
    image = io.BytesIO()
    Image.new("RGB", (400, 400), "#C04040").save(image, "PNG")
    page.set_input_files("#crw-character-file", files=[{"name": "host.png", "mimeType": "image/png",
                                                         "buffer": image.getvalue()}])
    page.wait_for_function("document.getElementById('crw-character-image').value==='characters/my-host-def456.png'")
    assert page.locator("#crw-character").input_value().startswith("THE CAST: the main character is exactly")
    # going back to Automatic brings back the recipe's own character
    page.locator("#crw-character-image").select_option("")
    assert page.locator("#crw-character").input_value() == STORY_CAST



def test_a_learned_style_in_the_library_can_be_edited_or_studied_again(workspace):
    """A customer asked whether a learned recipe can be changed or has to be deleted and made again."""
    env, page = workspace, workspace.page
    env.open("#styles")
    page.locator(".crw-nav [data-nav=styles]").click()
    item = page.locator("#crw-styles-list .crw-style-item")
    assert item.count() == 1 and "My Illustrated Worlds" in item.inner_text()
    assert item.locator("a").get_attribute("href") == "/cr-style-clone?edit=" + CLONE
    item.locator(".crw-recipe-card").click()
    assert page.locator("#crw-selected-name").inner_text() == "My Illustrated Worlds", "the card still picks the style"


def test_unfinished_and_stopped_studies_show_in_my_styles_and_the_clone_bar_says_what_it_counts(workspace):
    """JamesElyse20 (2026-10-29): two studies died with a closed Studio; My styles said "No styles of your own yet"
    and the Clone card's bar (clones used) read as a study a third done."""
    env, page = workspace, workspace.page
    env.account = {"connected": True, "style_clones": {"used": 1, "limit": 3}}
    env.studies = [
        {"operation_id": "op_" + "1" * 24, "label": "Ocean facts", "title": "Ocean facts", "state": "running",
         "phase": "watch", "percent": 34, "elapsed_seconds": 60, "studio_closed": False, "can_study_again": False,
         "link": "/cr-style-clone?operation=op_" + "1" * 24},
        {"operation_id": "op_" + "2" * 24, "label": "Space stories", "title": "Space stories", "state": "failed",
         "phase": "watch", "percent": 30, "elapsed_seconds": 900, "studio_closed": True, "can_study_again": True,
         "link": "/cr-style-clone?operation=op_" + "2" * 24}]
    env.other_studies = 1
    env.open("#styles")
    page.locator(".crw-nav [data-nav=styles]").click()
    page.wait_for_selector("#crw-styles-list .crw-study-card")
    cards = page.locator("#crw-styles-list .crw-study-card")
    assert cards.count() == 2
    assert "Studying the channel… 34%" in cards.nth(0).inner_text()
    assert "Keep the ChannelRecipe launcher window open" in cards.nth(0).inner_text()
    assert "Stopped: Studio was closed. Study again" in cards.nth(1).inner_text()
    assert cards.nth(1).get_attribute("href") == "/cr-style-clone?operation=op_" + "2" * 24
    assert "another ChannelRecipe connection" in page.locator("#crw-styles-list").inner_text()
    assert page.locator("#crw-styles-empty").is_hidden()
    allowance = page.locator("#crw-clone-allowance")
    assert "1 of 3 style clones used on your plan" in allowance.inner_text()
    assert "not a study's progress" in allowance.locator(".crw-clone-bar").get_attribute("aria-label")
    # the library's My styles tab lists them too
    page.locator("#crw-styles-browse").click()
    page.locator('[data-family="custom"]').click()
    assert page.locator("#crw-gallery-grid .crw-study-card").count() == 2
    assert "2 style studies not finished" in page.locator("#crw-gallery-count").inner_text()


def test_a_finished_video_is_deleted_into_the_trash_and_undo_brings_it_back(workspace):
    """thatguygab (2026-10-25) asked to delete videos: Delete names what goes and what stays, moves it to Studio's
    trash, and Undo or Recently deleted puts it back."""
    env, page = workspace, workspace.page
    env.open("#projects")
    card = page.locator('[data-slug="finished-film"]')
    assert page.locator('[data-slug="active-film"], [data-slug="stopped-film"]').count() >= 1
    card.get_by_role("button", name="Delete A finished film").click()
    dialog = page.locator("#crw-delete")
    dialog.wait_for(state="visible")
    page.wait_for_function("!document.getElementById('crw-delete-confirm').disabled")
    assert page.locator("#crw-delete-title").inner_text() == "Delete this video?"
    assert page.locator("#crw-delete-name").inner_text() == "“A finished film”"
    assert page.locator("#crw-delete-goes li").all_inner_texts()[0] == "The finished video: A finished film.mp4 (12 MB)"
    assert page.locator("#crw-delete-kept li").all_inner_texts() == ["Your voice library and saved recordings"]
    assert "30 days" in page.locator("#crw-delete-note").inner_text()
    if os.environ.get("CR_SHOT"):
        page.screenshot(path=os.environ["CR_SHOT"].replace(".png", "-delete.png"))
    page.locator("#crw-delete-cancel").click()
    assert dialog.is_hidden() and not [p for p in env.posts if "/delete/" in p["path"]], "Keep it deletes nothing"
    card.get_by_role("button", name="Delete A finished film").click()
    page.wait_for_function("!document.getElementById('crw-delete-confirm').disabled")
    page.locator("#crw-delete-confirm").click()
    page.wait_for_function("!document.querySelector('[data-slug=\"finished-film\"]')")
    assert [p["path"] for p in env.posts if "/delete/" in p["path"]] == ["/cr-workspace/delete/video/finished-film"]
    assert "is in Studio’s trash" in page.locator("#crw-toast").inner_text()
    trash = page.locator("#crw-trash-videos")
    trash.wait_for(state="visible")
    assert "Recently deleted (1)" in trash.locator("summary").inner_text()
    page.locator("#crw-toast .crw-toast-undo").click()
    page.wait_for_selector('[data-slug="finished-film"]')
    assert any(p["path"].endswith("/restore") for p in env.posts)
    page.wait_for_function("document.getElementById('crw-trash-videos').hidden")
    for slug in ("active-film", "stopped-film"):           # nothing being made offers Delete
        if page.locator(f'[data-slug="{slug}"]').count():
            assert page.locator(f'[data-slug="{slug}"] .crw-delete-button').count() == (0 if slug == "active-film" else 1)


def test_a_learned_style_is_deleted_from_the_style_library_and_restored(workspace):
    env, page = workspace, workspace.page
    env.open("?cr-preset=" + CLONE)
    assert page.locator("#crw-selected-name").inner_text() == "My Illustrated Worlds"
    page.locator(".crw-nav [data-nav=styles]").click()
    assert page.locator("#crw-styles-list .crw-delete-button").count() == 1, "only the style the creator cloned"
    page.get_by_role("button", name="Delete My Illustrated Worlds").click()
    page.wait_for_function("!document.getElementById('crw-delete-confirm').disabled")
    assert page.locator("#crw-delete-title").inner_text() == "Delete this style?"
    assert page.locator("#crw-delete-kept li").all_inner_texts() == ["Videos you made with it stay in Projects"]
    page.locator("#crw-delete-confirm").click()
    page.locator("#crw-trash-styles").wait_for(state="visible")
    assert page.locator("#crw-styles-list .crw-recipe-card").count() == 0
    assert page.locator("#crw-selected-name").inner_text() != "My Illustrated Worlds", "a deleted style isn't chosen"
    page.locator("#crw-trash-styles summary").click()
    page.get_by_role("button", name="Restore My Illustrated Worlds").click()
    page.wait_for_selector("#crw-styles-list .crw-recipe-card")
    assert "is back in your Style library" in page.locator("#crw-toast").inner_text()


def test_progress_says_which_3d_review_round_is_running_and_why_it_takes_long(workspace):
    env, page = workspace, workspace.page
    env.mode = "active"
    env.status_lines = ["3D: previews — round 2/3, 30 scenes, 90 stills",
                        "3D: review 2/3 — Claude is looking at 90 stills of 30 scenes (6 contact sheets)"]
    env.open("#projects")
    page.get_by_role("button", name="View progress", exact=True).click()
    page.locator("#crw-progress-detail").wait_for(state="visible")
    assert page.locator("#crw-progress-phase").inner_text() == "3D review, round 2 of 3"
    detail = page.locator("#crw-progress-detail").inner_text()
    assert "Claude is checking 90 stills of 30 scenes" in detail and "15 to 30 minutes" in detail
    if os.environ.get("CR_SHOT"):
        page.locator("#crw-progress").screenshot(path=os.environ["CR_SHOT"])


def test_progress_shows_the_real_step_under_a_general_heading(workspace):
    env, page = workspace, workspace.page
    env.mode = "active"
    env.status_lines = ["Writing the voiceover", "  subtitles: timing narration with whisper (2 of 3 parts)"]
    env.open("#projects")
    page.get_by_role("button", name="View progress", exact=True).click()
    page.locator("#crw-progress-detail").wait_for(state="visible")
    assert page.locator("#crw-progress-phase").inner_text() == "Timing narration and captions"
    assert page.locator("#crw-progress-detail").inner_text() == "subtitles: timing narration with whisper (2 of 3 parts)"



def test_create_adds_to_the_queue_while_a_video_is_made_and_projects_show_the_line(workspace):
    """Customer ALEX (2026-10-26) had to sit and wait to press Create again: while a video is made, Create says Add to
    queue, and Projects show each waiting video's place in line."""
    env, page = workspace, workspace.page
    env.mode = "queue"
    env.open()
    create = page.locator("#crw-create")
    assert create.is_enabled() and create.inner_text().strip() == "Add to queue"
    note = page.locator("#crw-readiness").inner_text()
    assert "1 more is waiting" in note and "starts when the ones before it finish" in note
    page.locator("#crw-title").fill("Another one for the queue")
    create.click()
    page.wait_for_function("window.CR_WORKSPACE_CLIENT.state.page==='projects' && !window.CR_WORKSPACE_CLIENT.state.submitting")
    assert len(env.creates) == 1 and env.creates[0]["body"]["title"] == "Another one for the queue"
    assert "Added to the queue" in page.locator("#crw-toast").inner_text()
    assert "2 videos before it" in page.locator("#crw-toast").inner_text()
    page.wait_for_function("document.getElementById('crw-progress-phase').textContent === 'Waiting in the queue'")
    card = page.locator(".crw-project-card", has_text="Moon landing")
    assert "Next in line" in card.inner_text() and "Queued" in card.inner_text()
    assert page.locator(".crw-nav [data-nav=create]").count()
    page.locator(".crw-nav [data-nav=create]").click()
    assert page.locator("#crw-create").inner_text().strip() == "Add to queue"
    assert not env.errors


def test_a_held_queue_says_why_on_its_cards_and_new_videos_join_it(workspace):
    env, page = workspace, workspace.page
    env.mode = "queue_held"
    env.open("#projects")
    card = page.locator(".crw-project-card", has_text="Titanic")
    assert "On hold" in card.inner_text() and "Kie.ai balance ran out" in card.inner_text()
    page.locator(".crw-filter-group [data-project-filter=active]").click()
    assert page.locator(".crw-project-card", has_text="Titanic").count() == 1, "held videos are still in progress"
    page.locator(".crw-nav [data-nav=create]").click()
    assert page.locator("#crw-create").inner_text().strip() == "Add to queue"
    assert "The queue is on hold: Your Kie.ai balance ran out" in page.locator("#crw-readiness").inner_text()
    page.locator("#crw-title").fill("One more")
    page.locator("#crw-create").click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('on hold with the others')")
    assert not env.errors


def test_a_3d_video_says_what_its_3d_adds_and_can_take_one_quicker_review_round(workspace):
    """A customer's 2-minute 3D video took about two hours, mostly Claude (2026-10-25), with nothing said up front."""
    env, page = workspace, workspace.page
    env.open("?cr-preset=3d")
    note = page.locator("#crw-threed-note")
    note.wait_for(state="visible")
    assert "to a 10-minute video" in note.inner_text() and "Claude directs every shot" in note.inner_text()
    page.locator("[data-minutes='2']").click() if page.locator("[data-minutes='2']").count() else \
        page.locator("#crw-minutes").fill("2")
    page.locator("#crw-minutes").dispatch_event("input")
    two = note.inner_text()
    assert "to a 2-minute video" in two
    page.locator("summary:has-text('Creative direction')").click()
    page.locator("#crw-threed-quick").check()
    assert note.inner_text() != two, "one round: less time"
    if os.environ.get("CR_SHOT"):
        page.locator("#crw-threed-quick").scroll_into_view_if_needed()
        page.screenshot(path=os.environ["CR_SHOT"], full_page=False)
    page.locator("#crw-title").fill("How one tank stopped a column")
    page.locator("#crw-create").click()
    page.wait_for_function("!window.CR_WORKSPACE_CLIENT.state.submitting && window.CR_WORKSPACE_CLIENT.state.page==='projects'")
    assert env.creates[0]["body"]["threed_quick"] is True


def test_carry_on_says_why_the_videos_are_still_on_hold(workspace):
    env, page = workspace, workspace.page
    env.mode = "queue_held"
    env.open()
    page.locator("[data-held-notice]:visible button", has_text="Carry on").first.click()
    page.wait_for_function("document.querySelector('#crw-toast').textContent.includes('Still on hold')")
    assert "Connect ElevenLabs" in page.locator("#crw-toast").inner_text()
    assert "Nothing was on hold" not in page.locator("#crw-toast").inner_text()
    assert not env.errors


# ── Studio 110: Claude, one click from Create ──────────────────────────────────────────────────────────────────────

def test_create_offers_one_click_to_verify_the_claude_models(workspace):
    env, page = workspace, workspace.page
    env.mode = "models"                                   # tools not connected yet: no check by itself, the button
    env.open()
    action = page.locator("#crw-readiness-action")
    assert action.is_visible() and action.inner_text() == "Verify my Claude models"
    assert page.locator("#crw-create").is_disabled()
    assert not [p for p in env.posts if p["path"] == "/cr-claude/setup"]
    action.click()
    page.wait_for_function("!document.getElementById('crw-create').disabled")
    assert [p["body"] for p in env.posts if p["path"] == "/cr-claude/setup"] == [{"action": "verify"}]
    assert action.is_hidden() and "passed" in page.locator("#crw-toast").inner_text()


def test_create_checks_the_models_by_itself_once_and_says_why_a_check_stopped(workspace):
    env, page = workspace, workspace.page
    env.mode = "models_auto"
    env.claude_fails = "Your Claude plan reached its usage limit, so the model check couldn't run."
    env.open()
    page.locator("#crw-error").wait_for(state="visible")
    assert "usage limit" in page.locator("#crw-error").inner_text()
    # once, never in a loop, and marked auto: a check that stopped before waits for the button
    assert [p["body"] for p in env.posts if p["path"] == "/cr-claude/setup"] == [{"action": "verify", "auto": True}]
    action = page.locator("#crw-readiness-action")
    assert action.is_visible() and action.inner_text() == "Verify my Claude models"
    env.claude_fails = ""
    action.click()
    page.wait_for_function("!document.getElementById('crw-create').disabled")
    assert [p["body"] for p in env.posts if p["path"] == "/cr-claude/setup"][1:] == [{"action": "verify"}]   # a press


def test_create_never_checks_the_models_by_itself_while_a_video_is_made(workspace):
    env, page = workspace, workspace.page
    env.mode, env.busy = "models_auto", True
    env.open()
    action = page.locator("#crw-readiness-action")
    action.wait_for(state="visible")
    page.wait_for_timeout(600)
    assert not [p for p in env.posts if p["path"] == "/cr-claude/setup"]
    assert not page.locator("#crw-error").is_visible()


def test_create_on_the_old_api_route_offers_the_switch_without_stopping_videos(workspace):
    env, page = workspace, workspace.page
    env.mode = "api_route"
    env.open()
    assert not page.locator("#crw-create").is_disabled()                 # never stranded before switching
    assert "old API route" in page.locator("#crw-readiness").inner_text()
    action = page.locator("#crw-readiness-action")
    assert action.inner_text() == "Switch to Claude Code (recommended)"
    action.click()
    page.wait_for_function("document.getElementById('crw-readiness-action').hidden")
    assert [p["body"] for p in env.posts if p["path"] == "/cr-claude/setup"] == [{"action": "switch"}]
    assert "Switched" in page.locator("#crw-toast").inner_text()
    assert "old API route" not in page.locator("#crw-readiness").inner_text()


def test_studio_cloud_asks_for_a_top_up_before_anything_starts(workspace):
    """The owner (2026-10-28): a creator tops up before their first cloud video, and the choice never fails half-way."""
    env, page = workspace, workspace.page
    env.cloud = {"available": True, "balance_usd": 0, "machines": {"cpu": {"price_per_hr": 1.2}}}
    env.open()
    page.wait_for_selector("#crw-cloud-choice:not([hidden])")
    page.locator("#crw-title").fill("Cloud film")
    page.locator("[data-render-on=cloud]").click()
    page.wait_for_selector("#crw-cloud-topup:not([hidden])")
    assert "$1.68 short" in page.locator("#crw-cloud-topup-note").inner_text()
    chunks = page.locator("#crw-cloud-chunks button")
    assert chunks.all_inner_texts() == ["Top up $5", "Top up $10", "Top up $25", "Top up $50"]
    assert "crw-primary-button" in chunks.first.get_attribute("class"), "the smallest chunk that covers it"
    assert page.locator("#crw-create").is_disabled() and page.locator("#crw-create").inner_text() == \
        "Top up to render in the cloud"
    with page.expect_popup():
        chunks.nth(1).click()
    page.wait_for_function("window.CR_WORKSPACE_CLIENT && document.querySelector('#crw-toast').textContent.includes('Finish paying')")
    assert env.topups == [10] and env.cloud_starts == [], "nothing started"

    env.cloud["balance_usd"] = 10
    page.bring_to_front()
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_selector("#crw-cloud-topup", state="hidden")
    page.wait_for_function("!document.getElementById('crw-create').disabled")
    page.locator("#crw-create").click()
    page.wait_for_function("window.CR_WORKSPACE_CLIENT.state.page==='projects'")
    assert len(env.cloud_starts) == 1

    page.locator(".crw-nav [data-nav=create]").click()
    page.locator("[data-render-on=local]").click()
    assert page.locator("#crw-cloud-topup").is_hidden() and page.locator("#crw-create").inner_text() != \
        "Top up to render in the cloud"


def test_roblox_story_shorts_take_my_recording_and_the_other_shorts_say_they_voice_their_own(workspace):
    """Primo (Discord, 2026-10-29) pitches the Adam voice 1.3x in CapCut and uploads that take as a Roblox Story
    Short's narration, but Create refused My recording for every Short ("Shorts write and voice their own narration").
    Roblox Story Shorts now take it, at the recording's length; the Shorts that can't hide the choice."""
    env, page = workspace, workspace.page
    env.catalog["channels"] += cr_presentation.presentation_catalog([
        {"name": "cr-shorts-roblox", "short_seconds": 55, "own_recording": True},
        {"name": "cr-shorts-3d", "short_seconds": 25, "own_recording": False}])
    env.open("?cr-preset=cr-shorts-roblox")
    assert page.locator("#crw-selected-name").inner_text() == "Roblox Story Shorts"
    own = page.locator("[data-narration-mode=own]")
    assert own.is_visible() and page.locator("#crw-short-duration").is_visible()
    own.click()
    assert page.locator("#crw-narration-own").is_visible()
    assert page.locator("#crw-short-duration").is_hidden(), "the recording sets the Short's length"
    # the summary refreshes once the voiceover list has loaded (setNarration awaits it); Windows CI was slower than that
    page.wait_for_function("() => document.getElementById('crw-summary-duration').textContent === 'Length of your recording'")
    assert page.inner_text("#crw-summary-duration") == "Length of your recording"
    assert page.locator("#crw-create").is_disabled() and "Choose a narration recording" in page.inner_text("#crw-readiness")
    page.evaluate("""() => {const s = document.getElementById('crw-audio-recent'), o = document.createElement('option');
        o.value = 'adam-pitched-1a2b.mp3'; o.textContent = 'adam-pitched · 32s'; s.append(o); s.value = o.value;
        s.dispatchEvent(new Event('change'));}""")
    page.locator("#crw-title").fill("Everyone has a number")
    # a length typed before My recording was chosen is hidden now: it can't stop the Create
    page.evaluate("document.getElementById('crw-seconds').value = '90'")
    page.locator("#crw-create").click()
    page.wait_for_function("!window.CR_WORKSPACE_CLIENT.state.submitting && window.CR_WORKSPACE_CLIENT.state.page==='projects'")
    body = env.creates[0]["body"]
    assert body["style"] == "cr-shorts-roblox" and body["byo_audio"] == "adam-pitched-1a2b.mp3"
    assert body["title"] == "Everyone has a number"
    # 3D Explainer Shorts write and voice their own narration: no My recording there, and AI narration again
    page.locator(".crw-nav [data-nav=create]").click()
    page.evaluate("window.CR_WORKSPACE_CLIENT.selectRecipe(window.CR_WORKSPACE_CLIENT.state.catalog.channels.find(r => r.name === 'cr-shorts-3d'))")
    assert own.is_hidden() and page.evaluate("window.CR_WORKSPACE_CLIENT.state.narration") == "ai"
    assert page.locator("#crw-narration-own").is_hidden() and page.locator("#crw-short-duration").is_visible()
    # a long video offers it again
    page.evaluate("window.CR_WORKSPACE_CLIENT.selectRecipe(window.CR_WORKSPACE_CLIENT.state.catalog.channels.find(r => r.name === 'documentary'))")
    assert own.is_visible()
    assert not env.errors
