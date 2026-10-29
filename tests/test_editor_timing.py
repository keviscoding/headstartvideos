"""Picture timing in the timeline editor (cr_timing): words from the voice, the add-on loaded into the engine's own
editor page, and, in a browser, every timing change sent as the editor's own `retime` op.

A customer (thatguygab, 2026-10-25): "Image generation and VO are on point. The problem is the timing. Prompting makes
it even longer to fix." The browser test drives the real editor page of the newest release in data/frontier (the
private engine, so it is skipped where this checkout doesn't have it) with a synthetic timeline behind it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace
import zipfile

from flask import Flask, Response, jsonify, request
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "local_runtime"
sys.path.insert(0, str(RUNTIME))
import cr_timing  # noqa: E402

BASE = "http://127.0.0.1:7860"
EDITOR = "<html><head><title>Timeline editor</title></head><body><div id=laneV></div><script>let D=null;</script></body></html>"
HEARD = [["March", 0.0, 0.42], ["23,", 0.42, 0.92], ["2021.", 1.14, 1.9], ["A", 2.26, 2.5], ["quarter", 2.5, 2.78],
         ["mile", 2.78, 3.12], ["long", 3.12, 3.4], ["ship", 3.4, 3.7], ["turns", 3.7, 4.04], ["sideways", 4.04, 4.64],
         ["in", 4.64, 4.96], ["the", 4.96, 5.06], ["Suez", 6.2, 6.6], ["Canal", 6.6, 7.0], ["and", 7.0, 7.4],
         ["stops.", 7.4, 8.0], ["The", 10.4, 10.6], ["Ever", 10.6, 10.9], ["Given", 10.9, 11.5], ["is", 12.3, 12.5],
         ["wedged", 12.5, 13.0], ["bank", 13.0, 13.4], ["to", 13.4, 13.6], ["bank.", 13.6, 14.2], ["Six", 15.6, 15.9],
         ["days", 15.9, 16.3], ["later", 16.3, 16.8], ["it", 16.8, 16.9], ["floats.", 16.9, 17.8]]


@pytest.fixture
def studio(tmp_path):
    job = tmp_path / "output" / "documentary" / "_work" / "suez--abc"
    job.mkdir(parents=True)
    jobs = {"suez--abc": job}
    app = Flask("timing")

    @app.get("/editor/<slug>")
    def editor_page(slug):
        return Response(EDITOR, mimetype="text/html", headers={"Cache-Control": "no-store"})

    studio = SimpleNamespace(app=app, mv=SimpleNamespace(find_job=lambda slug: jobs.get(slug)))
    for name in ("cr_timing.js", "cr_timing.css"):
        (tmp_path / name).write_text((RUNTIME / name).read_text(encoding="utf-8"), encoding="utf-8")
    assert cr_timing.install(studio, home=tmp_path)
    get = lambda path, **kw: app.test_client().get(path, base_url=BASE, **kw)
    return SimpleNamespace(app=app, job=job, jobs=jobs, home=tmp_path, get=get)


def test_the_editor_page_loads_the_timing_add_on_once_and_keeps_everything_else(studio):
    page = studio.get("/editor/suez--abc")
    assert page.status_code == 200 and page.headers["Cache-Control"] == "no-store"
    assert page.text.count("/cr-editor/timing.js") == 1 and page.text.count("/cr-editor/timing.css") == 1
    assert page.text.index("let D=null") < page.text.index("/cr-editor/timing.js") < page.text.index("</body>")
    assert cr_timing.with_addon(page.text) == page.text, "never loaded twice"
    assert page.text.replace(cr_timing.TAGS, "") == EDITOR, "the editor itself is served as it is"


def test_a_video_drawn_by_its_styles_own_code_gets_no_timeline_and_says_why(studio):
    """The owner (2026-10-27): the timeline can't edit a coded video (each minute is one whole animation: a retime or
    delete froze or restarted it, and its re-render doubled the renderer's captions), so it is hidden, said plainly."""
    import cr_animated2d
    assert not cr_animated2d.coded_video(studio.job)
    (studio.job / "motion").mkdir()
    (studio.job / "motion" / "a2d_000_1a2b3c4d.mp4").write_bytes(b"x")
    assert cr_animated2d.coded_video(studio.job)
    page = studio.get("/editor/suez--abc")
    assert page.status_code == 200 and "No timeline for this video" in page.text and "Improve this style" in page.text
    assert "let D=null" not in page.text and "/cr-editor/timing.js" not in page.text, "never the editor"
    assert 'href="/f/video/suez--abc"' in page.text and page.headers["Cache-Control"] == "private, no-store"
    assert studio.get("/editor/unknown").text != page.text, "any other video keeps its editor"


def test_an_engine_without_the_timeline_editor_is_left_alone():
    app = Flask("no-editor")
    assert cr_timing.install(SimpleNamespace(app=app, mv=None)) is False
    assert not any(rule.rule.startswith("/cr-editor") for rule in app.url_map.iter_rules())


def test_words_come_from_the_voices_own_clock(studio):
    (studio.job / "words_heard.json").write_text(json.dumps(HEARD + [["", 1, 2], ["bad", "x", 3], ["neg", -1, 2]]))
    data = studio.get("/cr-editor/words/suez--abc").json
    assert data["source"] == "voice" and data["words"][12] == ["Suez", 6.2, 6.6]
    assert len(data["words"]) == len(HEARD), "blank, broken and negative entries are skipped"


def test_without_a_word_clock_the_captions_place_each_word_by_its_letters(studio):
    (studio.job / "subs.srt").write_text("1\n00:00:02,000 --> 00:00:04,000\nA ship\nturns\n\n"
                                         "2\n00:00:05,500 --> 00:00:06,000\nstops.\n", encoding="utf-8")
    data = studio.get("/cr-editor/words/suez--abc").json
    assert data["source"] == "captions"
    assert [w[0] for w in data["words"]] == ["A", "ship", "turns", "stops."]
    assert data["words"][0][1] == 2.0 and data["words"][2][2] == 4.0 and data["words"][3][1:] == [5.5, 6.0]
    assert data["words"][0][2] < data["words"][1][2] < data["words"][2][2]


def test_words_and_add_on_files_are_local_and_never_outside_studios_output(studio, tmp_path):
    assert studio.app.test_client().get("/cr-editor/words/suez--abc", base_url="http://rebound.example:7860").status_code == 403
    assert studio.get("/cr-editor/words/suez--abc", headers={"Origin": "https://evil.example"}).status_code == 403
    assert studio.get("/cr-editor/words/..").status_code in (400, 404)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "words_heard.json").write_text(json.dumps([["secret", 0, 1]]))
    studio.jobs["outside"] = outside
    assert studio.get("/cr-editor/words/outside").status_code == 404
    assert studio.get("/cr-editor/words/unknown").status_code == 404
    script = studio.get("/cr-editor/timing.js")
    assert script.status_code == 200 and script.mimetype == "application/javascript"
    assert script.headers["Cache-Control"] == "private, no-store"
    assert studio.get("/cr-editor/timing.css").mimetype == "text/css"
    assert studio.get("/cr-editor/cr_timing.py").status_code == 404


def test_the_add_on_only_sends_the_editors_own_retime_op():
    """Every timing change goes through the editor's op() as {op: 'retime', slot, start, end}, the body its own edge
    drag sends, so it saves, previews, undoes and renders like the editor's own edits. Nothing else is written."""
    script = (RUNTIME / "cr_timing.js").read_text(encoding="utf-8")
    assert re.findall(r"await op\((.*?)\);", script) == [
        "edge === 'start' ? {op: 'retime', slot: i, start: t, end: x.t + x.dur} : {op: 'retime', slot: i, start: x.t, end: t}"]
    assert "fetch(" in script and script.count("fetch(") == 1 and "/cr-editor/words/" in script
    assert "method" not in script.split("fetch(")[1].split(")")[0], "its only request reads the words"


# ── the real editor page, in a browser ───────────────────────────────────────────────────────────────────────────

def engine_editor() -> str:
    """editor.html of the newest release build in data/frontier ('' when this checkout has none)."""
    wanted = os.environ.get("CR_EDITOR_HTML")
    if wanted and Path(wanted).is_file():
        return Path(wanted).read_text(encoding="utf-8")
    builds = sorted(((int(m[1]), p) for p in (ROOT / "data" / "frontier").glob("channelrecipe-5.3-admin.*.zip")
                     if (m := re.search(r"admin\.(\d+)\.zip$", p.name))), reverse=True)
    for _, path in builds:
        try:
            with zipfile.ZipFile(path) as archive:
                return archive.read("ChannelRecipe studio/editor.html").decode("utf-8")
        except (KeyError, OSError, zipfile.BadZipFile):
            continue
    return ""


@pytest.fixture
def editor(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    html = engine_editor()
    if not html:
        pytest.skip("the engine's timeline editor is private (data/frontier release builds)")
    from werkzeug.serving import make_server
    import cr_ui
    job = tmp_path / "output" / "documentary" / "_work" / "suez--abc"
    job.mkdir(parents=True)
    (job / "words_heard.json").write_text(json.dumps(HEARD))
    for name in ("cr_timing.js", "cr_timing.css"):
        (tmp_path / name).write_text((RUNTIME / name).read_text(encoding="utf-8"), encoding="utf-8")
    plan = [["video", "a.mp4", 5.0], ["video", "b.mp4", 5.0], ["photo", "c.jpg", 5.0], ["video", "d.mp4", 5.0]]
    env = SimpleNamespace(ops=[], plan=plan, errors=[])
    app = Flask("editor")

    def starts():
        out, t = [], 0.0
        for e in env.plan:
            out.append(t)
            t += e[2]
        return out

    def items():
        st = starts()
        return [{"i": i, "t": round(st[i], 3), "dur": round(e[2], 3), "kind": e[0], "file": e[1], "type": "footage",
                 "title": "Footage · " + e[1], "label": "", "source": "", "changed": False, "editable_gfx": False,
                 "preview": "", "thumb": f"/api/tl/suez--abc/thumb/{i}", "line": ""} for i, e in enumerate(env.plan)]

    @app.get("/editor/<slug>")
    def editor_page(slug):
        return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})

    @app.after_request
    def branded(response):                                 # as cr_studio does for every engine page
        if response.mimetype == "text/html":
            response.set_data(cr_ui.transform_html(response.get_data(as_text=True)))
        return response

    @app.get("/api/tl/<slug>")
    def timeline(slug):
        return jsonify(slug=slug, title="How One Ship Blocked the Suez Canal", style="documentary", editable=True,
                       duration=sum(e[2] for e in env.plan), video=f"/f/video/{slug}", has_video=True, items=items(),
                       labels=[], captions=[], chapters=[], changes=len(env.ops), tasks=[], chat=[], deferred=[],
                       auto_render=False, render_eta=40, thinking=0, catalog=0, busy="")

    @app.get("/api/tl/<slug>/peaks")
    def peaks(slug):
        return jsonify(per_s=20, v=[40] * 400)

    @app.post("/api/tl/<slug>/op")
    def edit(slug):
        body = request.get_json(force=True)
        env.ops.append(body)
        if body.get("op") == "retime":                     # the engine's own rule, enough for the page to redraw
            i, st = int(body["slot"]), starts()
            a, b = float(body["start"]), float(body["end"])
            if i > 0:
                env.plan[i - 1][2] = a - st[i - 1]
            if i + 1 < len(env.plan):
                env.plan[i + 1][2] = st[i + 1] + env.plan[i + 1][2] - b
            env.plan[i][2] = b - a
        return jsonify(results=[{"done": "Retimed."}])

    @app.get("/api/tl/<slug>/thumb/<int:i>")
    @app.get("/f/video/<slug>")
    @app.get("/f/audio/<slug>")
    def nothing(slug, i=None):
        return Response(status=404)

    studio = SimpleNamespace(app=app, mv=SimpleNamespace(find_job=lambda slug: job if slug == "suez--abc" else None))
    assert cr_timing.install(studio, home=tmp_path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(cr_timing, "LOCAL", (f"127.0.0.1:{server.server_port}",))   # never Studio's own 7860 here
    with playwright.sync_playwright() as driver:
        try:
            browser = driver.chromium.launch(headless=True)
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install the Playwright Chromium browser to run editor UI tests.")
            raise
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
        page = context.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: env.errors.append(str(error)))
        page.goto(base + "/editor/suez--abc")
        page.wait_for_function("window.CR_TIMING && window.CR_TIMING.state.words.length > 0")
        page.wait_for_selector("#laneV .clip[data-i='3']")
        env.page = page
        yield env
        browser.close()
    server.shutdown()
    worker.join(timeout=2)
    assert not env.errors, "Unexpected editor JavaScript error: " + str(env.errors)


def retimes(env):
    return [o for o in env.ops if o.get("op") == "retime"]


def test_click_a_picture_then_the_word_it_should_appear_on(editor):
    page = editor.page
    page.locator("#laneV .clip[data-i='1']").click(position={"x": 40, "y": 30})
    panel = page.locator("#insp .crt-panel")
    panel.wait_for()
    assert "Appears at 0:05.00" in panel.inner_text() and "Ends at 0:10.00" in panel.inner_text()
    chips = panel.locator(".crt-edge").first.locator(".crt-chip")
    assert "Suez" in chips.all_inner_texts() and "2021." not in chips.all_inner_texts(), "only where the cut can go"
    if os.environ.get("CR_SHOT"):
        page.screenshot(path=os.environ["CR_SHOT"].replace(".png", "-timing.png"))
    chips.filter(has_text="Suez").click()
    page.wait_for_function("document.querySelector('#insp .crt-panel b')?.textContent.includes('0:06.20')")
    assert retimes(editor) == [{"op": "retime", "slot": 1, "start": 6.2, "end": 10}]
    assert page.locator("#laneV .clip[data-i='1']").count() == 1 and editor.plan[0][2] == pytest.approx(6.2)


def test_nudges_are_saved_as_one_change_and_the_keys_put_the_cut_at_the_playhead(editor):
    page = editor.page
    page.locator("#laneV .clip[data-i='2']").click(position={"x": 40, "y": 30})
    panel = page.locator("#insp .crt-panel")
    panel.wait_for()
    start = panel.locator(".crt-edge").first
    for _ in range(3):
        start.get_by_role("button", name="+0.1 s").click()
    assert "saving" in start.inner_text() and not retimes(editor), "a run of nudges waits to be saved once"
    page.wait_for_function("(t => t && t.includes('0:10.30') && !t.includes('saving'))"
                           "(document.querySelector('#insp .crt-panel b')?.textContent)")
    assert retimes(editor) == [{"op": "retime", "slot": 2, "start": 10.3, "end": 15}]
    page.keyboard.press("Alt+ArrowLeft")
    page.wait_for_timeout(900)
    assert retimes(editor)[-1] == {"op": "retime", "slot": 2, "start": 10.2, "end": 15}
    page.evaluate("seek(13.05)")                           # a pause just after "bank" starts: the cut lands on it
    page.keyboard.press("]")
    page.wait_for_timeout(300)
    assert retimes(editor)[-1] == {"op": "retime", "slot": 2, "start": 10.2, "end": 13}
    page.evaluate("seek(1.0)")
    page.keyboard.press("[")
    page.wait_for_selector(".toast.err")
    assert "between" in page.locator(".toast.err").inner_text() and len(retimes(editor)) == 3


def test_dragging_an_edge_snaps_to_the_spoken_word_and_sends_one_retime(editor):
    page = editor.page
    handle = page.locator("#laneV .clip[data-i='3'] .eh.l")
    box = handle.bounding_box()
    at = lambda t: page.evaluate(f"(s => s.getBoundingClientRect().left - s.scrollLeft + {t} * PPS)"
                                 "(document.getElementById('scroll'))")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(at(12.36), box["y"] + box["height"] / 2, steps=8)      # a hair after "is" starts (12.3)
    assert "“is”" in page.locator(".crt-ghost").inner_text()
    page.mouse.up()
    page.wait_for_timeout(300)
    assert retimes(editor) == [{"op": "retime", "slot": 3, "start": 12.3, "end": 20}], "one op, not the editor's too"
    # Alt drags freely, to the exact place the mouse lets go
    box = handle.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.keyboard.down("Alt")
    page.mouse.down()
    page.mouse.move(at(13.27), box["y"] + box["height"] / 2, steps=5)       # near "bank" (13.0), not snapped
    page.mouse.up()
    page.keyboard.up("Alt")
    page.wait_for_timeout(300)
    assert len(retimes(editor)) == 2 and retimes(editor)[1]["start"] == pytest.approx(13.27, abs=0.03)


def test_the_words_are_written_on_the_voice_track(editor):
    page = editor.page
    page.wait_for_function("document.querySelectorAll('#laneA .crt-word').length === 29")
    assert "2021." in page.locator("#laneA .crt-word").all_inner_texts(), "a word with room is written out"
    page.evaluate("setZoom(160, 0, 0)")                      # zoomed in: every word has room
    page.wait_for_function("[...document.querySelectorAll('#laneA .crt-word')].some(n => n.textContent === 'Suez')")
    assert "floats." not in page.locator("#laneA .crt-word").all_inner_texts(), "only the words in view are drawn"
    page.evaluate("document.getElementById('scroll').scrollLeft = 1e6")
    page.wait_for_function("[...document.querySelectorAll('#laneA .crt-word')].some(n => n.textContent === 'floats.')")
    assert page.locator("#insp .crt-tip").count() == 1, "the empty inspector says how to fix a picture's timing"
