"""A recipe's character, changed for one video (cr_character): new words or an uploaded picture replace the recipe's
picture of its old character, which otherwise goes with every still (Discord, 2026-10-24: "I tried this but it didn't
work")."""
import ast
import io
import json
import os
import re
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, jsonify, request
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_bridge  # noqa: E402
import cr_character as character  # noqa: E402
import cr_cloud  # noqa: E402
import cr_studio_tools as tools  # noqa: E402
import cr_submission  # noqa: E402
import cr_threed_look  # noqa: E402
import cr_workspace as workspace  # noqa: E402

ENGINE = Path("/Users/workcomputer/Downloads/ChannelRecipe studio")
OLD_CAST = ("THE MAIN CHARACTER is the man from the reference image, and he is in EVERY frame:\nearly thirties, short "
            "messy dark-brown hair, a plain dark-grey crew-neck t-shirt — always this exact man.")
NEW_CAST = "Mia, a woman in her thirties with long red hair, round glasses, a green raincoat and yellow boots."
TOKEN = "synthetic-character-token"
BASE = "http://127.0.0.1:7860"


def picture_bytes(size=(512, 512)) -> bytes:
    out = io.BytesIO()
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(out, "PNG")
    return out.getvalue()


def engine_module(home: Path, *, real: bool = False):
    """The engine's character state and still-step functions: the installed engine's own code when `real` (and it is
    installed here), else a copy of their behaviour."""
    (home / "assets" / "characters").mkdir(parents=True, exist_ok=True)
    (home / "assets" / "characters" / "stories.png").write_bytes(picture_bytes())
    mod = types.ModuleType("synthetic_make_video")
    mod.Path, mod.HERE = Path, home
    mod.STYLE_CHARACTER = {"stories": OLD_CAST, "plain": ""}
    mod.STYLE_CHARACTER_IMAGE = {"stories": "characters/stories.png"}
    mod.STYLE_INFO = {"stories": {"look": {"character_image": "characters/stories.png",
                                           "still_suffix": "Flat 2D cartoon, thin clean outlines."}},
                      "plain": {"look": {}}}
    mod._CHARACTER, mod._CHARACTER_IMAGE, mod._CAST_FROM_AVATAR, mod._STYLE_NOW = "", "", False, "stories"
    mod.lines, mod.drawn, mod.hosted = [], [], []
    mod.log = mod.lines.append

    def image_gen(prompt, dest, label="image", ref_urls=None, kie_create_fn=None, stills=False):
        mod.drawn.append({"prompt": prompt, "refs": list(ref_urls or []), "stills": stills, "label": label})
        Path(dest).write_bytes(picture_bytes((256, 256)))
    mod.image_gen = image_gen

    def host_image(path):
        mod.hosted.append(Path(path).name)
        return "https://files.example/" + Path(path).name
    mod.host_image = host_image
    mod._extra_block = lambda: ""
    if real:
        source = ENGINE / "make_video.py"
        if not source.is_file():
            pytest.skip("The owner's pinned private engine is not installed on this computer.")
        wanted = {"set_character", "set_character_image", "character_image_path", "_character_refs"}
        tree = ast.parse(source.read_text(encoding="utf-8"))
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        body += [n for n in tree.body if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "REF_ONLY_CHARACTER" for t in n.targets)]
        assert len(body) == 5, "the engine's character functions moved: check cr_character still wraps them"
        exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), mod.__dict__)
    else:
        def set_character(text=""):
            mod._CHARACTER = (text or "").strip()

        def set_character_image(rel=""):
            mod._CHARACTER_IMAGE = (rel or "").strip().replace("\\", "/")

        def character_image_path(style):
            for rel in (mod._CHARACTER_IMAGE, mod.STYLE_CHARACTER_IMAGE.get(style) or ""):
                if rel and ".." not in rel.split("/") and (home / "assets" / rel).is_file():
                    return home / "assets" / rel
            return None

        def _character_refs(style):
            p = mod.character_image_path(style)          # a module global in the engine: the wrapped one
            if p is None:
                return []
            url = mod.host_image(p)
            return [url] if url else []
        mod.set_character, mod.set_character_image = set_character, set_character_image
        mod.character_image_path, mod._character_refs = character_image_path, _character_refs
    character.install(mod, home)
    return mod


def still_step(engine, style, stills_left=True):
    """The engine's still step (make_video.generate_astrology_visuals): the picture is asked for first, and put online
    only when there is something left to draw."""
    refs = []
    face = engine.character_image_path(style)
    if face is not None and stills_left:
        refs = engine._character_refs(style)
    return face, refs


# ── the engine: which picture rides along on every still ─────────────────────────────────────────────────────

@pytest.mark.parametrize("real", [False, True], ids=["copy", "installed-engine"])
def test_new_words_draw_the_character_once_in_the_recipes_style_and_replace_its_picture(tmp_path, real):
    engine = engine_module(tmp_path, real=real)
    engine.set_character(NEW_CAST)
    engine.set_character_image("")
    face, refs = still_step(engine, "stories")
    assert face.name != "stories.png" and face.parent.name == "characters"
    assert refs == ["https://files.example/" + face.name], "the drawn sheet, never the recipe's picture"
    draw = engine.drawn[0]
    assert draw["label"] == "character sheet" and draw["refs"] == ["https://files.example/stories.png"]
    assert draw["prompt"].startswith(character.STYLE_NOTE + character.SHEET + NEW_CAST)
    assert "Do NOT copy its character" in draw["prompt"] and draw["prompt"].endswith("thin clean outlines.")
    assert face.with_suffix(".txt").read_text().strip() == NEW_CAST, "Create lists it with its words"
    assert any("drew " + face.name in line for line in engine.lines)
    # the next still, the next video with the same words, a resumed video: no second picture is paid for
    still_step(engine, "stories")
    engine.set_character("  " + NEW_CAST.replace(" ", "  ") + "\n")
    still_step(engine, "stories")
    assert len(engine.drawn) == 1


@pytest.mark.parametrize("real", [False, True], ids=["copy", "installed-engine"])
def test_the_recipes_own_character_a_picked_picture_and_a_presenter_are_unchanged(tmp_path, real):
    engine = engine_module(tmp_path, real=real)
    # Create sends the recipe's own words back as they were shown (dashes and spacing as a textarea keeps them)
    engine.set_character(OLD_CAST.replace("—", "-").replace("\n", " "))
    assert still_step(engine, "stories")[1] == ["https://files.example/stories.png"]
    engine.set_character("")
    assert still_step(engine, "stories")[1] == ["https://files.example/stories.png"]
    mine = tmp_path / "assets" / "characters" / "my-host-abc123.png"
    mine.write_bytes(picture_bytes())
    engine.set_character(NEW_CAST)
    engine.set_character_image("characters/my-host-abc123.png")
    assert still_step(engine, "stories") == (mine, ["https://files.example/my-host-abc123.png"])
    engine.set_character_image("")
    engine._CAST_FROM_AVATAR = True                   # the presenter's own photo is set by the engine itself
    assert still_step(engine, "stories")[1] == ["https://files.example/stories.png"]
    assert not engine.drawn


def test_a_look_chosen_for_the_title_keeps_the_recipes_own_character(tmp_path):
    engine = engine_module(tmp_path)
    engine.STYLE_INFO["stories-rainy"] = {"variant_of": "stories", "look": engine.STYLE_INFO["stories"]["look"]}
    engine.STYLE_CHARACTER["stories-rainy"] = OLD_CAST + " In the rain."
    engine.STYLE_CHARACTER_IMAGE["stories-rainy"] = "characters/stories.png"
    engine.set_character(OLD_CAST)                  # Create showed the recipe's words and they were left alone
    assert still_step(engine, "stories-rainy")[1] == ["https://files.example/stories.png"] and not engine.drawn


def test_a_recipe_without_a_character_picture_keeps_following_the_words(tmp_path):
    engine = engine_module(tmp_path)
    engine.set_character(NEW_CAST)
    assert still_step(engine, "plain") == (None, []) and not engine.drawn


def test_a_video_whose_stills_are_all_made_draws_nothing(tmp_path):
    engine = engine_module(tmp_path)
    engine.set_character(NEW_CAST)
    face, refs = still_step(engine, "stories", stills_left=False)
    assert face is not None and refs == [] and not engine.drawn and not face.exists()


def test_a_failed_drawing_never_brings_back_the_recipes_character(tmp_path):
    engine = engine_module(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("Kie answered 500")
    engine.image_gen = broken
    engine.set_character(NEW_CAST)
    face, refs = still_step(engine, "stories")
    assert refs == [], "no reference rather than the recipe's character (its picture was only the drawing's style)"
    assert any("couldn't be drawn (Kie answered 500)" in line and "without the recipe's picture" in line
               for line in engine.lines)
    assert not list((tmp_path / "assets" / "characters").glob("*.part.png"))


def test_words_compare_without_case_spacing_dashes_or_the_4000_character_cut():
    assert not character.is_new(OLD_CAST.upper().replace("—", "-"), OLD_CAST)
    assert not character.is_new("", OLD_CAST) and not character.is_new(["a", "b"], "a\nb")
    long = "word " * 1000
    assert not character.is_new(long[:4000], long), "the textarea keeps the first 4,000 characters"
    assert character.is_new(NEW_CAST, OLD_CAST) and character.is_new(NEW_CAST, None)
    assert character.sheet_path("/s", "stories", NEW_CAST).name.startswith("mia-a-woman-in-her-thirties-")
    assert character.sheet_path("/s", "stories", NEW_CAST) != character.sheet_path("/s", "hustle", NEW_CAST)
    assert character.sheet_path("/s", "stories", "THE CAST: Sam, a tall baker").name.startswith("sam-a-tall-baker-")


def test_3d_scenes_hear_the_new_character_and_nothing_else_does(tmp_path, monkeypatch):
    engine = engine_module(tmp_path)
    engine.set_character(NEW_CAST)
    assert "THE MAIN CHARACTER OF THIS VIDEO" not in engine._extra_block(), "outside 3D work: scripts are unchanged"
    monkeypatch.setitem(cr_threed_look._ACTIVE, "job", tmp_path)
    block = engine._extra_block()
    assert "THE MAIN CHARACTER OF THIS VIDEO" in block and NEW_CAST in block and "replace the channel's" in block
    engine.set_character(OLD_CAST)
    assert "THE MAIN CHARACTER OF THIS VIDEO" not in engine._extra_block()


# ── Create: upload a picture, draw from words ────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, text in (("cr_workspace.html", "<html>__LOCAL_TOKEN_JSON__ __PRESET_JSON__</html>"),
                       ("cr_workspace.js", ""), ("cr_workspace.css", "")):
        (tmp_path / name).write_text(text)
    settings = {"costs_accepted": True}
    monkeypatch.setattr(workspace, "cr_bridge", SimpleNamespace(load_settings=lambda: dict(settings)))
    import cr_media
    monkeypatch.setattr(cr_media, "active", lambda task: False)       # the engine's own picture service draws
    app = Flask(__name__)
    app.add_url_rule("/", "original_page", lambda: "Original")
    app.add_url_rule("/api/channels", "api_channels", lambda: jsonify(channels=[]))
    engine = engine_module(tmp_path)
    styles = SimpleNamespace(STYLES={"stories": {"look": engine.STYLE_INFO["stories"]["look"]}})
    studio = SimpleNamespace(app=app, styles=styles, mv=engine, JOBS={})
    workspace.install(studio, SimpleNamespace(lock=threading.RLock(), busy=lambda: False), token=TOKEN, home=tmp_path)
    client = app.test_client()
    return SimpleNamespace(app=app, home=tmp_path, engine=engine, settings=settings, studio=studio,
                           post=lambda path, **kw: client.post(path, base_url=BASE, **kw))


def test_create_draws_a_described_character_once_and_lists_it(env):
    r = env.post("/cr-workspace/character/draw", json={"style": "stories", "description": NEW_CAST})
    assert r.status_code == 200, r.json
    row = r.json["character"]
    assert row["rel"].startswith("characters/mia-a-woman") and row["src"] == "/charimg/" + row["file"]
    assert row["text"] == NEW_CAST and row["label"].startswith("mia a woman")
    assert (env.home / "assets" / row["rel"]).is_file() and len(env.engine.drawn) == 1
    assert env.engine.drawn[0]["refs"] == ["https://files.example/stories.png"], "drawn in the recipe's style"
    assert env.post("/cr-workspace/character/draw", json={"style": "stories", "description": NEW_CAST}).json[
        "character"] == row and len(env.engine.drawn) == 1, "a second press pays nothing"
    # and the video with the same words finds it instead of drawing again
    env.engine.set_character(NEW_CAST)
    assert still_step(env.engine, "stories")[1] == ["https://files.example/" + row["file"]]
    assert len(env.engine.drawn) == 1


def test_drawing_needs_words_a_recipe_and_accepted_spending(env):
    assert "Describe your character" in env.post("/cr-workspace/character/draw",
                                                 json={"style": "stories", "description": " "}).json["error"]
    assert env.post("/cr-workspace/character/draw", json={"style": "nope", "description": "x"}).status_code == 400
    env.settings["costs_accepted"] = False
    r = env.post("/cr-workspace/character/draw", json={"style": "stories", "description": NEW_CAST})
    assert r.status_code == 400 and "provider spending" in r.json["error"] and not env.engine.drawn


def test_create_saves_an_uploaded_character_with_words_that_point_at_it(env):
    form = {"file": (io.BytesIO(picture_bytes()), "My Host.JPG")}
    row = env.post("/cr-workspace/character", data=form, content_type="multipart/form-data").json["character"]
    assert re.fullmatch(r"characters/my-host-[0-9a-f]{6}\.png", row["rel"]) and row["text"] == character.UPLOADED
    with Image.open(env.home / "assets" / row["rel"]) as img:
        assert img.format == "PNG" and max(img.size) <= 1600
    worded = {"file": (io.BytesIO(picture_bytes()), "mia.png"), "description": NEW_CAST}
    assert env.post("/cr-workspace/character", data=worded,
                    content_type="multipart/form-data").json["character"]["text"] == NEW_CAST
    tiny = {"file": (io.BytesIO(picture_bytes((100, 100))), "tiny.png")}
    assert "at least 256 pixels" in env.post("/cr-workspace/character", data=tiny,
                                             content_type="multipart/form-data").json["error"]
    junk = {"file": (io.BytesIO(b"not a picture"), "x.png")}
    assert "PNG, JPG or WebP" in env.post("/cr-workspace/character", data=junk,
                                          content_type="multipart/form-data").json["error"]


def test_create_page_offers_the_character_before_extra_instructions():
    page = (ROOT / "local_runtime" / "cr_workspace.html").read_text()
    assert page.index('id="crw-character-section"') < page.index('id="crw-extra"')
    assert 'id="crw-character-draw"' in page and 'id="crw-character-file"' in page and 'id="crw-character-art"' in page
    assert "That one picture goes with every image" in page and "For a different character, use Character above" in page
    script = (ROOT / "local_runtime" / "cr_workspace.js").read_text()
    assert "character:strict()?'':$('crw-character').value.trim(),character_image:strict()?'':$('crw-character-image').value" in script
    assert "api('/cr-workspace/character/draw'" in script and "api('/cr-workspace/character'," in script
    assert "A character picture of your own renders on this computer for now." in script


def test_studio_cloud_keeps_a_character_picture_on_this_computer():
    assert "on this computer" in cr_cloud.blocked({"style": "stories", "character_image": "characters/x.png"})
    assert cr_cloud.blocked({"style": "stories", "character": NEW_CAST}) == "", "words are prepared here"


# ── Claude Code: make_videos(options.character / character_picture) ──────────────────────────────────────────

ROWS = [{"name": "stories", "label": "2D Life Stories", "character": OLD_CAST, "character_image": "characters/stories.png",
         "features": {"offered": ["depth"], "on": ["depth"]}},
        {"name": "cr-shorts-3d", "label": "3D Shorts", "short_seconds": 25, "features": {"offered": [], "on": []}},
        {"name": "cr_clone_kit", "label": "My Kit", "visual_policy": "reference_only",
         "features": {"offered": [], "on": []}}]


@pytest.fixture
def studio(monkeypatch, tmp_path, env):
    """The tools' Studio: the real Create routes for characters, and the real submission path for /api/run."""
    staged = []
    monkeypatch.setattr(tools, "STATE", tmp_path / "state")
    monkeypatch.setattr(tools, "thumbnail", lambda row: "")
    monkeypatch.setattr(tools, "same_studio", lambda: None)
    calls = []

    def call(method, path, body=None, **kw):
        calls.append((method, path, body))
        if path == "/cr-workspace/catalog":
            return {"channels": json.loads(json.dumps(ROWS)), "prices": {}}
        if path == "/cr-claude-code/styles":
            return {"styles": []}
        if path == "/cr-workspace/character":
            r = env.post(path, json=body)
            if r.status_code != 200:
                raise tools.Refused(r.json["error"])
            return r.json
        if path == "/api/run":
            staged.append(body)
            return {"queued": [{"slug": "mia-video", "title": body["title"]}]}
        if path == "/api/queue":                               # the quote says what's already in Studio's queue
            return {"jobs": [], "busy": False}
        raise AssertionError("unexpected route " + path)
    monkeypatch.setattr(tools, "call", call)
    return SimpleNamespace(staged=staged, calls=calls, env=env)


def run(name, args):
    return tools.run_tool(name, args)


def test_make_videos_with_new_words_tells_the_cost_and_sends_the_character(studio):
    options = {"character": NEW_CAST}
    q = run("quote", {"style": "stories", "minutes": 8, "options": options})["structuredContent"]
    assert "draws your character once" in q["character"] and "$0.02" in q["character"]
    out = run("make_videos", {"titles": ["A rainy year"], "style": "stories", "minutes": 8, "where": "this_computer",
                              "options": options, "quote_id": q["quote_id"]})["structuredContent"]
    assert out["started"] is True
    body = studio.staged[0]
    assert body["character"] == NEW_CAST and body["character_image"] == "" and NEW_CAST not in body["extra"]


def test_make_videos_with_a_picture_saves_it_and_picks_it_for_the_video(studio, tmp_path):
    picture = tmp_path / "mia.webp"
    Image.frombytes("RGB", (400, 400), os.urandom(400 * 400 * 3)).save(picture, "WEBP")
    options = {"character_picture": str(picture)}
    q = run("quote", {"style": "stories", "minutes": 8, "options": options})["structuredContent"]
    run("make_videos", {"titles": ["Mia's year"], "style": "stories", "minutes": 8, "where": "this_computer",
                        "options": options, "quote_id": q["quote_id"]})
    body = studio.staged[0]
    assert re.fullmatch(r"characters/mia-[0-9a-f]{6}\.png", body["character_image"])
    assert body["character"] == character.UPLOADED
    assert (studio.env.home / "assets" / body["character_image"]).is_file()


@pytest.mark.parametrize("style,options,where,message", [
    ("cr-shorts-3d", {"character": NEW_CAST}, "this_computer", "Shorts keep their own host"),
    ("cr_clone_kit", {"character": NEW_CAST}, "this_computer", "learned animation kit"),
    ("stories", {"character_picture": "/nowhere/mia.png"}, "this_computer", "PNG, JPG or WebP"),
    ("stories", {"character": "x" * 4001}, "this_computer", "under 4,000 characters"),
])
def test_make_videos_refuses_a_character_it_cant_keep_before_anything_starts(studio, style, options, where, message):
    result = run("make_videos", {"titles": ["T"], "style": style, "where": where, "options": options})
    assert result["isError"] is True and message in json.dumps(result)
    assert not studio.staged and not any(p == "/cr-workspace/character" for _, p, _ in studio.calls)


def test_the_tool_schema_says_to_use_character_not_extra_instructions():
    described = json.dumps(tools.TOOLS["make_videos"][2])
    assert "character_picture" in described and "rather than extra_instructions" in described
    skill = (ROOT / "local_runtime" / "claude_skills" / "make-videos" / "SKILL.md").read_text()
    assert "options.character" in skill and "rather than\n  extra_instructions" in skill


# ── end to end through the job spec ──────────────────────────────────────────────────────────────────────────

def test_a_picked_character_travels_from_create_to_the_still_step(env, monkeypatch, tmp_path):
    """Create's payload -> /api/run (the engine's own validation, then cr_submission) -> the queued job spec -> the
    project's options at local_studio_begin -> the engine's character state -> the reference on every still."""
    source = ENGINE / "app.py"
    if not source.is_file():
        pytest.skip("The owner's pinned private engine is not installed on this computer.")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("api_run", "_char_rel")]
    for node in wanted:
        node.decorator_list = []
    assert len(wanted) == 2, "the engine's run route moved: check the character still reaches the job"
    mine = character.save_upload(env.home, picture_bytes(), "mia.png", NEW_CAST)
    app, queue, jobs = Flask("engine-run"), [], {}
    ns = {"request": request, "jsonify": jsonify, "re": re, "Path": Path, "QUEUE": queue, "JOBS": jobs,
          "LOCK": threading.Lock(), "_ensure_dispatcher": lambda: None,
          "VALID_STEPS": {"script", "voiceover", "pexels", "images", "video", "thumbnail"},
          "mv": SimpleNamespace(prompts=SimpleNamespace(PROMPT_SETS={"stories": {}}), slugify=lambda t: t.lower()),
          "features": SimpleNamespace(BY_KEY={"depth": {}}), "avatar": SimpleNamespace(MAX_COUNT=4),
          "_threed_on": lambda style, features: False,
          "CHAR_DIR": env.home / "assets" / "characters", "CHAR_EXT": {".png", ".jpg", ".jpeg", ".webp"}}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(source), "exec"), ns)
    app.add_url_rule("/api/run", "api_run", ns["api_run"], methods=["POST"])
    studio = SimpleNamespace(app=app, QUEUE=queue, JOBS=jobs, LOCK=threading.Lock(), _ensure_dispatcher=lambda: None)
    payload = {"title": "Mia's rainy year", "style": "stories", "minutes": 8, "steps": ["script", "video"],
               "features": {"depth": True}, "extra": "Make it hopeful.", "character": NEW_CAST,
               "character_image": f"characters/{mine.name}", "render_backend": "original"}
    with app.test_request_context(json={}):
        assert cr_submission.submit(studio, payload, "crv_" + "c" * 24).status_code == 200
    spec = queue[0]
    assert spec["character"] == NEW_CAST and spec["character_image"] == f"characters/{mine.name}"
    # an escape or a missing file never reaches the engine
    with app.test_request_context(json={}):
        cr_submission.submit(studio, {**payload, "character_image": "../../.env"}, "crv_" + "d" * 24)
    assert queue[1]["character_image"] == ""

    sent = {}
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: {"brain_mode": "claudecode", "key": "k"})
    monkeypatch.setattr(cr_bridge, "HOME", tmp_path)
    monkeypatch.setattr(cr_bridge, "_mcp_tool", lambda name, body: sent.update(body) or {
        "project": {"id": "crv_" + "c" * 24}, "job_spec": spec})
    cr_bridge.begin_project(spec["title"], spec["style"], proof={"local_version": "5.3-admin.98"}, job_spec=spec)
    assert sent["options"]["character"] == NEW_CAST
    assert sent["options"]["character_image"] == f"characters/{mine.name}"

    engine = env.engine                                   # what run_pipeline does with the spec
    engine.set_character(sent["options"]["character"])
    engine.set_character_image(sent["options"]["character_image"])
    assert still_step(engine, "stories")[1] == ["https://files.example/" + mine.name] and not engine.drawn
