"""A video in the creator's language: its script, voice, captions and words on screen (local_runtime/cr_language.py).

The owner, 2026-10-27: "people want different languages ... Like Spanish, especially during the voiceover." The
private engine speaks whatever language a style's file names (make_video.STYLE_LANGUAGE): its outline and script
prompts open with that language (_lang_prompt) and its graphics, figures and description ask _job_language. Studio now
lets the creator choose per video; for that run the style speaks the chosen language. Fakes only: nothing is paid for
and no model is called."""
import ast
import json
import re
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_language
import cr_media

# the owner's installed Studio: the pinned engine's own functions are read from it (never changed) where it exists
ENGINE = Path("/Users/workcomputer/Downloads/ChannelRecipe studio")


@pytest.fixture
def job(tmp_path):
    folder = tmp_path / "output" / "la-boveda"
    folder.mkdir(parents=True)
    return folder


def engine_with(job, started, **extra):
    """make_video's language table and run entry point: run_custom reads the table while it runs."""
    engine = SimpleNamespace(STYLE_LANGUAGE={"documentary": "English"}, log=lambda line: None,
                             job_dir=lambda title, style="": job, **extra)

    def run_custom(title, minutes=20, *args, **kwargs):
        started.append({"title": title, "style": kwargs.get("style"),
                        "language": engine.STYLE_LANGUAGE.get(kwargs.get("style")), "whisper": cr_language.whisper_code()})
    engine.run_custom = run_custom
    return engine


# ── the language list and a creator's choice ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said", ["es", "Spanish", "spanish", "Español", "espanol", "ESPAÑOL", "es-MX", "Spanish (español)"])
def test_spanish_is_understood_however_it_is_written(said):
    assert cr_language.clean(said) == "es"


def test_a_language_studio_cant_make_is_refused_with_the_list():
    with pytest.raises(ValueError, match="Choose a language from the list: English, Spanish, Portuguese"):
        cr_language.clean("Klingon")
    assert cr_language.clean("") == "" and cr_language.clean(None) == ""


def test_every_language_has_its_words_for_studio_s_own_lines():
    ids = [row["id"] for row in cr_language.LANGUAGES]
    assert ids[0] == "en" and len(ids) == len(set(ids)) and "es" in ids
    for row in cr_language.LANGUAGES:
        assert row["cue"] and row["label"] and row["name"] and row["native"], row["id"]
        assert cr_language.find(row["engine"]) is row, "the engine's name for it reads back as the same language"
    assert cr_language.choices()[1] == {"id": "es", "name": "Spanish", "native": "Español", "offered": True}
    assert cr_language.find("Tagalog")["id"] == "tl" and cr_language.find("Bahasa Indonesia")["id"] == "id"


def test_only_languages_every_font_draws_are_offered_for_any_recipe():
    """Lilita One and Share Tech Mono have no ą ě ș ő ğ: Polish, Czech, Romanian, Hungarian and Turkish wait for
    fonts that draw them. A recipe that speaks one already (a Czech channel) keeps it."""
    offered = [l["id"] for l in cr_language.choices()]
    assert offered == ["en", "es", "pt", "fr", "de", "it", "nl", "sv", "da", "no", "fi", "id", "tl"]
    assert "cs" in [l["id"] for l in cr_language.choices(also={"cs", "en", None})]
    with pytest.raises(ValueError, match="Choose a language from the list"):
        cr_language.clean("Polski", {"language": "English"})
    assert cr_language.clean("cs", {"language": "Czech (čeština)"}) == "cs"
    assert cr_language.clean("Español", {"language": "Czech (čeština)"}) == "es"


@pytest.mark.skipif(not (ENGINE / "assets" / "fonts").is_dir(), reason="the installed Studio's fonts are not here")
def test_every_shipped_font_draws_every_offered_languages_letters():
    fontTools = pytest.importorskip("fontTools.ttLib")
    letters = {"es": "áéíóúñÑ¿¡ü", "pt": "ãõâêôçÇáéíóú", "fr": "àâçéèêëîïôùûüÿœŒÉÀ", "de": "äöüÄÖÜß",
               "it": "àèéìòù", "nl": "ëïéè", "sv": "åäöÅÄÖ", "da": "æøåÆØÅ", "no": "æøåÆØÅ", "fi": "äöåÄÖ",
               "id": "", "tl": "ñÑ", "en": ""}
    assert set(letters) == {l["id"] for l in cr_language.choices()}
    for font in sorted((ENGINE / "assets" / "fonts").glob("*.ttf")):
        cmap = fontTools.TTFont(font).getBestCmap() or {}
        missing = {k: "".join(c for c in v if ord(c) not in cmap) for k, v in letters.items()}
        assert not any(missing.values()), (font.name, missing)


def test_a_style_speaks_its_own_files_language():
    assert cr_language.style_language({"language": "English"}) == "en"
    assert cr_language.style_language({"language": "Czech (čeština)"}) == "cs"
    assert cr_language.style_language({"language": "Spanish (español)"}) == "es"
    assert cr_language.style_language({}) == "en" and cr_language.style_language(None) == "en"


def test_studio_keeps_the_creators_choice_for_the_next_videos(tmp_path):
    assert cr_language.default(tmp_path) == ""
    assert cr_language.for_new_video(tmp_path, {"language": "Czech"}) == "cs", "never chosen: the style's own"
    (tmp_path / cr_language.PREFS).write_text(json.dumps({"animation_frame_check": False}), encoding="utf-8")
    assert cr_language.set_default(tmp_path, "Español") == "es"
    prefs = json.loads((tmp_path / cr_language.PREFS).read_text(encoding="utf-8"))
    assert prefs == {"animation_frame_check": False, "video_language": "es"}, "the frame check's setting is kept"
    assert cr_language.default(tmp_path) == "es" and cr_language.for_new_video(tmp_path, {"language": "Czech"}) == "es"
    assert cr_language.set_default(tmp_path, "cs") == "es", "a Czech channel's own language never becomes the default"


# ── one video's run ──────────────────────────────────────────────────────────────────────────────────────────────

def test_the_chosen_language_is_the_styles_for_its_run_and_only_its_run(job):
    started = []
    engine = engine_with(job, started)
    cr_language.install(engine)
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "es"}), encoding="utf-8")
    engine.run_custom("La bóveda", 8, style="documentary")
    assert started[-1] == {"title": "La bóveda", "style": "documentary", "language": "Spanish (español)",
                           "whisper": "es"}
    assert engine.STYLE_LANGUAGE == {"documentary": "English"}, "the style's own language is back after the run"
    assert cr_language.current() == "" and cr_language.whisper_code() == ""


def test_a_resume_speaks_the_language_its_video_was_made_in(job):
    """A Resume or a repair runs from the saved project's spec, which never carries cr_language: the engine writes its
    run_spec.json without it. The choice kept in the video's folder at the first run's start still counts."""
    started = []
    engine = engine_with(job, started)
    cr_language.install(engine)
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "es"}), encoding="utf-8")
    engine.run_custom("La bóveda", 8, style="documentary")
    assert json.loads((job / cr_language.STICKY).read_text(encoding="utf-8")) == {"cr_language": "es"}
    (job / "run_spec.json").write_text(json.dumps({"burn_subs": True}), encoding="utf-8")       # the Resume's own
    engine.run_custom("La bóveda", 8, style="documentary")
    assert started[-1]["language"] == "Spanish (español)"
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "pt"}), encoding="utf-8")     # made again in another
    engine.run_custom("La bóveda", 8, style="documentary")
    assert started[-1]["language"] == "Portuguese (português)" and cr_language.of_job(job) == "pt"


def test_a_video_asked_nothing_runs_exactly_as_before(job):
    started = []
    engine = engine_with(job, started)
    engine.STYLE_LANGUAGE["cr_jung"] = "Czech (čeština)"
    cr_language.install(engine)
    (job / "run_spec.json").write_text(json.dumps({"burn_subs": True}), encoding="utf-8")
    engine.run_custom("The vault", 8, style="documentary")
    engine.run_custom("Trezor", 8, style="cr_jung")
    assert [s["language"] for s in started] == ["English", "Czech (čeština)"]
    assert [s["whisper"] for s in started] == ["", ""], "the speech model detects the language, as before"
    assert not (job / cr_language.STICKY).exists()


def test_installing_twice_wraps_once(job):
    started = []
    engine = engine_with(job, started)
    cr_language.install(engine)
    wrapped = engine.run_custom
    cr_language.install(engine)
    assert engine.run_custom is wrapped


def test_the_speech_model_is_told_the_videos_language(monkeypatch, job):
    """Captions and every word's timing come from Whisper on this computer: in a Spanish video it is told Spanish
    instead of guessing it from the audio."""
    asked = []
    model = SimpleNamespace(transcribe=lambda audio, *a, **k: (asked.append(k), ([], None))[1])
    engine = SimpleNamespace(_get_whisper=lambda: model)
    monkeypatch.setattr(cr_media, "narration_audio", lambda path: "pcm")
    cr_media.wrap_whisper(engine)
    engine._get_whisper().transcribe(str(job / "audio.mp3"), word_timestamps=True)
    monkeypatch.setitem(cr_language._current, "id", "es")
    engine._get_whisper().transcribe(str(job / "audio.mp3"), word_timestamps=True)
    engine._get_whisper().transcribe(str(job / "audio.mp3"), language="en")
    assert asked == [{"word_timestamps": True}, {"word_timestamps": True, "language": "es"}, {"language": "en"}]


# ── the engine's own functions (read from the installed Studio, never changed) ───────────────────────────────────

ENGINE_NAMES = {"_lang_prompt", "_job_language", "_to_english", "_fig_seps", "_apply_cta_scenes"}
ENGINE_VALUES = {"STYLE_LANGUAGE", "_CZ_MARKS", "_FIG_SPACE_LANGS", "_FIG_DOT_LANGS", "_CTA_SUB_RE", "_CTA_COM_RE",
                 "_CTA_COPY"}


def real_engine(job):
    """The engine's language functions and tables, exactly as the installed engine has them, in a module of their own
    (as in make_video, a module attribute is the functions' global)."""
    source = ENGINE / "make_video.py"
    if not source.is_file():
        pytest.skip("The owner's pinned private engine is not installed on this computer.")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ENGINE_NAMES]
    body += [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
             and {getattr(t, "id", "") for t in (n.targets if isinstance(n, ast.Assign) else [n.target])} & ENGINE_VALUES]
    assert {n.name for n in body if isinstance(n, ast.FunctionDef)} == ENGINE_NAMES, \
        "the engine's language functions moved: check cr_language still wraps them"
    engine = types.ModuleType("make_video")
    engine.os, engine.re, engine.Path = __import__("os"), re, Path
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), engine.__dict__)
    engine.STYLE_LANGUAGE.update(documentary="English")
    engine.log, engine.job_dir = (lambda line: None), (lambda title, style="": job)
    seen = {}

    def run_custom(title, minutes=20, *args, **kwargs):
        style = kwargs.get("style")
        lang = engine._job_language(job, style)
        seen.update(prompt=engine._lang_prompt("Write the script for: " + title, style), graphics=lang,
                    separators=engine._fig_seps(lang))
        specs = [{"template": "scene"}, {"template": "scene"}]
        seen["scenes"] = engine._apply_cta_scenes(specs, ["Suscríbete si te gustó.", "Cuéntamelo en los comentarios."],
                                                  style, job)
        seen["specs"] = specs
    engine.run_custom = run_custom
    return engine, seen


def test_the_real_engine_writes_draws_and_numbers_the_video_in_spanish(job):
    engine, seen = real_engine(job)
    cr_language.install(engine)
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "es"}), encoding="utf-8")
    (job / "script.txt").write_text("La bóveda se abrió a medianoche. Nadie lo vio.", encoding="utf-8")
    engine.run_custom("La bóveda", 8, style="documentary")
    assert seen["prompt"].startswith("LANGUAGE — THIS OVERRIDES EVERYTHING BELOW: write your entire output in Spanish.")
    assert seen["graphics"] == "Spanish (español)", "graphics, maps, figures and the description ask this"
    assert seen["separators"] == (".", ","), "figures are written the Spanish way: 1.234,5"
    assert seen["scenes"] == 2 and seen["specs"][0]["value"] == "SUSCRÍBETE"
    assert seen["specs"][1]["title"] == "Cuéntamelo en los comentarios"
    assert engine.STYLE_LANGUAGE["documentary"] == "English"
    assert engine._CTA_COPY[("subscribe", False)]["value"] == "SUBSCRIBE", "the engine's own copy is back"


def test_a_language_without_closing_scenes_gets_none_rather_than_english_ones(job):
    """Polish "komentarz" matches the engine's Czech "koment": it showed the English card in a Polish video."""
    engine, seen = real_engine(job)
    cr_language.install(engine)
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "pl"}), encoding="utf-8")
    engine.run_custom("Skarbiec", 8, style="documentary")
    assert seen["prompt"].startswith("LANGUAGE — THIS OVERRIDES EVERYTHING BELOW: write your entire output in Polish.")
    assert seen["scenes"] == 0 and seen["specs"] == [{"template": "scene"}, {"template": "scene"}]
    (job / "run_spec.json").write_text(json.dumps({"burn_subs": True}), encoding="utf-8")
    (job / cr_language.STICKY).unlink()
    engine.run_custom("The vault", 8, style="documentary")
    assert seen["prompt"].startswith("LANGUAGE — THIS OVERRIDES EVERYTHING BELOW: write your entire output in English.")


# ── what Studio itself writes: the call to action, a coded style's on-screen words ────────────────────────────────

def test_the_call_to_action_says_its_cue_in_the_videos_language(job):
    import cr_cta
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "es"}), encoding="utf-8")
    block = cr_cta.script_block({"what": "Mi curso", "count": 1, "link": "https://example.com"}, 10, job)
    assert 'contains the exact words "enlace en pantalla"' in block and "link on screen" not in block
    heard = [["Mira", 200.0, 200.3], ["el", 200.3, 200.4], ["¡enlace", 200.4, 200.8], ["en", 200.8, 200.9],
             ["pantalla!", 200.9, 201.3]]
    (job / "words_heard.json").write_text(json.dumps(heard), encoding="utf-8")
    assert cr_cta.mention_times(job, 1, 600.0) == [200.4]
    english = job.parent / "english"
    english.mkdir()
    assert 'contains the exact words "link on screen"' in cr_cta.script_block(
        {"what": "My course", "count": 1, "link": "https://example.com"}, 10, english)


def test_the_call_to_action_card_reads_in_the_videos_language(job, monkeypatch):
    import cr_cta
    drawn = []
    from PIL import ImageDraw
    original = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *a, **k: (drawn.append(text),
                                                                                      original(self, xy, text, *a, **k)))
    cr_cta.card({"what": "Mi curso de cocina", "link": "https://example.com/curso", "count": 1}, job / "card.png",
                label=cr_language.card_label("es"))
    assert "Escanea el código o usa el enlace de abajo" in " ".join(drawn)


def test_a_coded_styles_director_draws_its_words_in_the_videos_language(job, monkeypatch):
    import cr_animated2d as a2d
    asked = []
    monkeypatch.setattr(a2d, "_prompt", lambda name, home=None: "Direct [TITLE] over [BEATS].")
    monkeypatch.setattr(a2d, "earlier", lambda name, home=None: [])
    monkeypatch.setattr(a2d, "clean_plan", lambda plan, *a, **k: plan)
    engine = SimpleNamespace(step=lambda line: None, _extra_block=lambda: "",
                             claude=lambda prompt, *a, **k: (asked.append(prompt), '{"shots": [], "actions": []}')[1])
    casting = {"cast": {"hero": {"look": "a boy"}}, "sets": {}}
    (job / "animated2d").mkdir()
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "es"}), encoding="utf-8")
    a2d.direct(engine, job, casting, {}, (0.0, 6.0, [(0.0, 6.0, "El niño abre la puerta.", [("niño", 0.4)])]), 0, "",
               "La puerta")
    assert asked[0].startswith("LANGUAGE: the narration is in Spanish. Every word drawn on screen")
    assert "Action names, ids and JSON keys stay exactly as given." in asked[0]
    english = job.parent / "english"
    (english / "animated2d").mkdir(parents=True)
    a2d.direct(engine, english, casting, {}, (0.0, 6.0, [(0.0, 6.0, "The boy opens the door.", [("boy", 0.4)])]), 0,
               "", "The door")
    assert asked[1].startswith("Direct The door"), "an English video's director prompt is exactly as before"


# ── Create and Claude Code ───────────────────────────────────────────────────────────────────────────────────────

def submission_studio():
    import cr_submission
    app = Flask("language-submission")
    studio = SimpleNamespace(app=app, QUEUE=[], LOCK=threading.Lock(), JOBS={}, _ensure_dispatcher=lambda: None)
    namespace = {"request": request, "jsonify": jsonify, "QUEUE": studio.QUEUE, "JOBS": studio.JOBS,
                 "LOCK": studio.LOCK, "_ensure_dispatcher": studio._ensure_dispatcher}
    exec('''
def api_run():
    data = request.get_json(force=True)
    with LOCK:
        QUEUE.append({"slug": data["title"].lower(), "title": data["title"]})
    _ensure_dispatcher()
    return jsonify(queued=[{"slug": data["title"].lower(), "title": data["title"]}])
''', namespace)
    app.add_url_rule("/api/run", "api_run", namespace["api_run"], methods=["POST"])
    cr_submission.install(studio)
    return studio, app.test_client()


def test_creates_language_rides_the_videos_spec():
    studio, client = submission_studio()
    assert client.post("/api/run", json={"title": "Una", "language": "es"}).status_code == 200
    assert client.post("/api/run", json={"title": "Plain"}).status_code == 200
    by = {s["title"]: s for s in studio.QUEUE}
    assert by["Una"]["cr_language"] == "es" and "cr_language" not in by["Plain"]
    refused = client.post("/api/run", json={"title": "Klingon", "language": "tlh"})
    assert refused.status_code == 400 and "Choose a language from the list" in refused.get_json()["error"]
    assert "Klingon" not in {s["title"] for s in studio.QUEUE}, "refused before anything is queued"


ROWS = [{"name": "documentary", "label": "Documentary", "language": "en", "features": {"offered": [], "on": []}},
        {"name": "cr_clone_canal", "label": "Mi Canal", "language": "es", "features": {"offered": [], "on": []}}]
CATALOG = {"channels": ROWS, "languages": cr_language.choices(), "default_language": None}


def test_make_videos_speaks_the_language_asked_else_the_creators_last_else_the_styles():
    import cr_studio_tools as tools
    doc, canal = ROWS
    assert tools.language_of(CATALOG, doc, {"language": "Español"}) == "es"
    assert tools.language_of(CATALOG, doc, {}) == "en" and tools.language_of(CATALOG, canal, {}) == "es"
    assert tools.language_of({**CATALOG, "default_language": "pt"}, doc, {}) == "pt"
    assert tools.language_of({**CATALOG, "default_language": "pt"}, doc, {"language": "en"}) == "en"
    with pytest.raises(tools.Refused, match="Choose a language from the list"):
        tools.language_of(CATALOG, doc, {"language": "Elvish"})
    assert tools.video_payload(doc, ["La bóveda"], 8, {}, {}, "es")["language"] == "es"
    assert "language" not in tools.video_payload(doc, ["The vault"], 8, {}, {})
    assert tools.style_card({**canal, "tagline": "x"})["language"] == "Spanish"


def test_make_videos_starts_a_spanish_video_and_keeps_another_language_off_studio_cloud(monkeypatch, tmp_path):
    import cr_studio_tools as tools
    staged = []
    monkeypatch.setattr(tools, "STATE", tmp_path / "state")
    monkeypatch.setattr(tools, "thumbnail", lambda row: "")
    monkeypatch.setattr(tools, "same_studio", lambda: None)

    def call(method, path, body=None, **kw):
        if path == "/cr-workspace/catalog":
            return json.loads(json.dumps(CATALOG))
        if path == "/cr-claude-code/styles":
            return {"styles": []}
        if path == "/cr-workspace/resume-first":
            return {"matches": [], "others": body["titles"]}
        if path == "/api/run":
            staged.append(body)
            return {"queued": [{"slug": "la-boveda", "title": body["title"]}]}
        if path == "/api/queue":
            return {"jobs": [], "busy": False}
        if path == "/cr-gallery/picks":
            raise tools.Refused("no picker in this test")
        raise AssertionError("unexpected route " + path)
    monkeypatch.setattr(tools, "call", call)
    args = {"titles": ["La bóveda"], "style": "documentary", "minutes": 8, "options": {"language": "Spanish"}}
    q = tools.run_tool("quote", {**args, "titles": 1})["structuredContent"]
    out = tools.run_tool("make_videos", {**args, "where": "this_computer", "quote_id": q["quote_id"]})["structuredContent"]
    assert out["started"] is True and out["language"] == "Spanish" and staged[0]["language"] == "es"
    cloud = tools.run_tool("make_videos", {**args, "where": "studio_cloud", "quote_id": q["quote_id"]})
    assert cloud.get("isError") and "renders on this computer for now" in json.dumps(cloud)
    styles = tools.run_tool("list_styles", {})["structuredContent"]
    assert "Spanish" in styles["languages"] and styles["default_language"].startswith("each style's own")


def test_studio_cloud_keeps_a_video_in_another_language_on_this_computer():
    import cr_cloud
    assert cr_cloud.blocked({"style": "documentary", "language": "es"}) == \
        "A video in Spanish renders on this computer for now."
    assert cr_cloud.blocked({"style": "documentary", "language": "en"}) == ""
    assert cr_cloud.blocked({"style": "documentary"}) == ""


def test_the_create_page_offers_the_language_and_sends_it():
    page = (ROOT / "local_runtime" / "cr_workspace.html").read_text(encoding="utf-8")
    assert '<label for="crw-language">Language</label><select id="crw-language">' in page
    assert page.index('id="crw-language-section"') < page.index('data-narration-mode="ai"'), "first in Narration"
    assert '<dt>Language</dt><dd id="crw-summary-language">' in page
    script = (ROOT / "local_runtime" / "cr_workspace.js").read_text(encoding="utf-8")
    assert "language:$('crw-language').value||'en'" in script
    assert "api('/cr-workspace/language',{method:'POST',body:{language:state.language}})" in script
    assert "[state.language, state.catalog?.default_language, own, 'en'].find(" in script
    assert "l.offered !== false || l.id === own" in script, "a recipe's own language (Czech) only on that recipe"
    assert "renders on this computer for now." in script


def test_the_workspace_keeps_the_choice_as_studios_default():
    source = (ROOT / "local_runtime" / "cr_workspace.py").read_text(encoding="utf-8")
    assert '@app.post("/cr-workspace/language")' in source and "cr_language.set_default(home, body.get(\"language\"))" in source
    assert 'row["language"] = cr_language.style_language(info)' in source
    assert 'data["languages"] = cr_language.choices(also={row.get("language") for row in data["channels"]})' in source


def test_a_learned_style_can_speak_another_language_by_default(tmp_path):
    import cr_style_edit
    (tmp_path / "styles").mkdir()
    path = tmp_path / "styles" / "cr_clone_canal.json"
    path.write_text(json.dumps({"name": "cr_clone_canal", "label": "Mi Canal", "language": "English", "look": {},
                                "cr_style_clone": {"operation_id": "op_" + "5" * 24, "base_style": "documentary"}}),
                    encoding="utf-8")
    assert cr_style_edit.editable(tmp_path, "cr_clone_canal")["language"] == "English"
    saved = cr_style_edit.save(tmp_path, "cr_clone_canal", {"language": "es"})
    assert saved["saved"] is True and saved["language"] == "Spanish"
    assert json.loads(path.read_text(encoding="utf-8"))["language"] == "Spanish (español)", "the engine reads this"
    with pytest.raises(ValueError):
        cr_style_edit.save(tmp_path, "cr_clone_canal", {"language": "Elvish"})


def test_a_short_is_told_its_language(tmp_path):
    job = tmp_path / "out" / "corto"
    job.mkdir(parents=True)
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "es"}), encoding="utf-8")
    source = (ROOT / "local_runtime" / "cr_shorts.py").read_text(encoding="utf-8")
    assert 'env["CR_LANGUAGE"], env["CR_LANGUAGE_CODE"] = cr_language.BY_ID[lang]["name"], lang' in source
    assert cr_language.remember(job) == "es" and (job / cr_language.STICKY).is_file()


def test_a_ready_made_shorts_recipe_says_it_speaks_its_own_language_before_anything_starts():
    """Ranking, 3D, photo and Roblox Shorts write, voice and caption in their own pipelines, which don't take a
    language yet: a Spanish one is refused plainly instead of made in English."""
    import cr_studio_tools as tools
    studio, client = submission_studio()
    studio.styles = SimpleNamespace(STYLES={
        "cr-shorts-ranking": {"format": "short", "label": "Ranking Shorts", "language": "English",
                              "shorts": {"pipeline": "ranking"}},
        "cr-shorts-3d": {"format": "short", "label": "3D Shorts", "language": "English", "shorts": {"pipeline": "zack3d"}}})
    refused = client.post("/api/run", json={"title": "Perros", "style": "cr-shorts-ranking", "language": "es"})
    assert refused.status_code == 400
    assert refused.get_json()["error"].startswith("Ranking Shorts speak the language you type the topic in")
    refused = client.post("/api/run", json={"title": "El cofre", "style": "cr-shorts-3d", "language": "es"})
    assert refused.get_json()["error"].startswith("3D Shorts speaks English for now")
    assert client.post("/api/run", json={"title": "Dogs", "style": "cr-shorts-ranking", "language": "en"}).status_code == 200
    short = {"name": "cr-shorts-ranking", "label": "Ranking Shorts", "short_seconds": 30, "language": "en", "ranking": True}
    with pytest.raises(tools.Refused, match="Ranking Shorts speak the language you type the topic in"):
        tools.language_of(CATALOG, short, {"language": "es"})
    assert tools.language_of({**CATALOG, "default_language": "es"}, short, {}) == "en", "the default is for videos"
    script = (ROOT / "local_runtime" / "cr_workspace.js").read_text(encoding="utf-8")
    assert "isShort(state.selected) && state.selected?.short_seconds != null" in script and "box.disabled = pipelineShort" in script


def test_every_prompt_that_shapes_what_the_viewer_sees_says_the_language(job):
    """The engine adds its extra-instructions block to every prompt that shapes what the viewer sees or hears: the
    scene director's labels and cards, graphics, 3D, thumbnails and the script. In a Spanish video it says Spanish,
    and that picture prompts and search queries stay in English."""
    asked = []
    engine = SimpleNamespace(log=lambda line: None, job_dir=lambda title, style="": job,
                             STYLE_LANGUAGE={"documentary": "English"}, _extra_block=lambda: "")

    def run_custom(title, minutes=20, *args, **kwargs):
        asked.append("Design ONE thumbnail for " + title + engine._extra_block())
    engine.run_custom = run_custom
    cr_language.install(engine)
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "es"}), encoding="utf-8")
    engine.run_custom("La bóveda", 8, style="documentary")
    assert "LANGUAGE OF THIS VIDEO: Spanish. Every word the viewer hears or reads is in Spanish" in asked[-1]
    assert "These stay in English: image-generation prompts, stock-footage and picture search queries" in asked[-1]
    assert engine._extra_block() == "", "outside the run nothing is added"
    english = job.parent / "english"
    english.mkdir()
    engine.job_dir = lambda title, style="": english
    engine.run_custom("The vault", 8, style="documentary")
    assert asked[-1] == "Design ONE thumbnail for The vault", "an English video's prompts are exactly as before"


def test_the_language_holds_when_the_engine_rebuilds_its_tables_mid_video(job):
    """A 3D section reloads every style from disk and an automatic look registers itself under a new name: both
    rebuild STYLE_LANGUAGE and STYLE_INFO in place (styles.apply_to_engine). The fact check reads the language from
    STYLE_INFO: a Spanish script must never get English fixes."""
    seen = {}
    doc = {"name": "documentary", "language": "English"}
    engine = SimpleNamespace(log=lambda line: None, job_dir=lambda title, style="": job,
                             STYLE_LANGUAGE={"documentary": "English"}, STYLE_INFO={"documentary": doc})

    def run_custom(title, minutes=20, *args, **kwargs):
        engine.STYLE_INFO.clear()                                      # styles.apply_to_engine after load_all()
        engine.STYLE_INFO.update({"documentary": dict(doc), "auto-documentary": dict(doc)})
        engine.STYLE_LANGUAGE["documentary"] = engine.STYLE_LANGUAGE["auto-documentary"] = "English"
        seen["table"] = engine.STYLE_LANGUAGE.get("auto-documentary")
        seen["fact_check"] = (engine.STYLE_INFO.get("documentary") or {}).get("language")
        seen["auto"] = engine.STYLE_INFO["auto-documentary"]["language"]
        seen["dicts"] = list(engine.STYLE_INFO.values())
    engine.run_custom = run_custom
    cr_language.install(engine)
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "es"}), encoding="utf-8")
    engine.run_custom("La bóveda", 8, style="documentary")
    assert seen["table"] == seen["fact_check"] == seen["auto"] == "Spanish (español)"
    assert [d["language"] for d in seen["dicts"]] == ["English", "English"], "every style is put back after the run"
    assert engine.STYLE_LANGUAGE.get("documentary") == "English" and doc["language"] == "English"


def test_a_writers_lead_in_in_another_language_is_never_read_aloud(monkeypatch):
    import cr_spoken
    script = "Aquí tienes el guion completo:\n\nLa bóveda se abrió a medianoche.\n\nNadie lo vio."
    assert cr_spoken.strip_lead_in(script) == script, "outside a video in another language nothing changes"
    monkeypatch.setitem(cr_language._current, "id", "es")
    assert cr_spoken.strip_lead_in(script) == "La bóveda se abrió a medianoche.\n\nNadie lo vio."
    assert cr_spoken.strip_lead_in("**Guion:**\n\nLa bóveda.") == "La bóveda."
    story = "La bóveda se abrió a medianoche: nadie lo vio.\n\nY entonces llegó la policía."
    assert cr_spoken.strip_lead_in(story) == story, "a sentence of the story with a colon inside stays"
    long = "En 1972, en una ciudad pequeña del norte que nadie recuerda ya, pasó algo que cambió todo para siempre:\n\nEl banco."
    assert cr_spoken.strip_lead_in(long) == long, "a long opening sentence: the story"
    assert cr_spoken.strip_lead_in("Esto es lo que pasó:\n\nEl banco.") == "Esto es lo que pasó:\n\nEl banco.", \
        "a short opening that doesn't name the script is the story"
    assert cr_spoken.strip_lead_in("Voici le script :\n\nLa banque.") == "La banque."
    assert cr_spoken.strip_lead_in("Aqui está o roteiro narrado:\n\nO banco.") == "O banco."
    engine = SimpleNamespace(_clean_script=lambda text: text)
    cr_spoken.install(engine)
    assert engine._clean_script(script).startswith("La bóveda")


def test_the_description_headings_and_chapter_cards_are_in_the_videos_language(job):
    text = "La bóveda\n\nUna historia.\n\nFOOTAGE\n- AP\n\nCHAPTERS\n0:00 Intro\n\nSOURCES\n- BBC\n\nTAGS\nx, y\n"
    (job / "youtube.txt").write_text(text, encoding="utf-8")
    assert cr_language.description_headings(job / "youtube.txt", "es") is True
    got = (job / "youtube.txt").read_text(encoding="utf-8")
    assert "IMÁGENES\n- AP" in got and "CAPÍTULOS\n0:00" in got and "FUENTES\n- BBC" in got and "TAGS\nx, y" in got
    assert cr_language.description_headings(job / "youtube.txt", "en") is False
    calls = []
    engine = SimpleNamespace(log=lambda line: None, job_dir=lambda title, style="": job,
                             generate_youtube_meta=lambda title, script, srt, job, *a, **k: calls.append(title))
    (job / "youtube.txt").write_text(text, encoding="utf-8")
    (job / cr_language.STICKY).write_text(json.dumps({"cr_language": "pt"}), encoding="utf-8")
    cr_language.install(engine)
    engine.generate_youtube_meta("O cofre", "", job / "subs.srt", job)
    assert calls == ["O cofre"] and "CAPÍTULOS\n0:00" in (job / "youtube.txt").read_text(encoding="utf-8")
    assert set(cr_language.CHAPTER) >= {r["id"] for r in cr_language.LANGUAGES} - {"en"}
    assert set(cr_language.HEADINGS) >= {l["id"] for l in cr_language.choices()} - {"en"}
