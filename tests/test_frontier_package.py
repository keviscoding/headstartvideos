"""Private guide context survives packaging; fixtures contain no Frontier source."""
import hashlib
import json
import zipfile

import pytest

from scripts import build_frontier_admin as package


@pytest.fixture
def synthetic_package(tmp_path, monkeypatch):
    root = tmp_path / "public-builder"
    runtime = root / "local_runtime"
    runtime.mkdir(parents=True)
    providers = root / "webapp/frontier"
    providers.mkdir(parents=True)
    (providers / "providers.json").write_text('{"example": {}}')
    overrides = b"# CR operations\nUse CR setup forms and project authorization.\n"
    (runtime / "CLAUDE.md").write_bytes(overrides)
    (runtime / "START HERE.md").write_text("Use the launcher.")
    (runtime / "Start on Mac.command").write_text("#!/bin/bash\nexit 0\n")
    workflows = {}
    for name in package.PRIVATE_WORKFLOWS:
        path = tmp_path / name
        path.write_text("Synthetic private workflow resource.")
        workflows[name] = path
    monkeypatch.setattr(package, "PRIVATE_WORKFLOWS", workflows)
    monkeypatch.setattr(package, "ROOT", root)
    return tmp_path, overrides


def source_archive(tmp_path, monkeypatch, *, guide):
    source = tmp_path / "synthetic-private.zip"
    with zipfile.ZipFile(source, "w") as archive:
        if guide is not None:
            archive.writestr("Frontier/CLAUDE.md", guide)
        archive.writestr("Frontier/example_engine.py", b"# Synthetic engine fixture only\n")
        archive.writestr("Frontier/.env", b"synthetic-secret-must-not-ship")
    monkeypatch.setattr(package, "SOURCE_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    return source


def test_private_creative_guide_is_automatic_context_with_cr_operational_precedence(synthetic_package, monkeypatch):
    tmp_path, overrides = synthetic_package
    original = "# Synthetic creative guide\r\nKeep every scene visually coherent.\r\n## 日本語\r\n".encode()
    source = source_archive(tmp_path, monkeypatch, guide=original)
    target = tmp_path / "release.zip"
    report = package.build(source, target)
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert names.count("ChannelRecipe studio/CLAUDE.md") == 1
        assert archive.read("ChannelRecipe studio/CREATIVE_GUIDE.md") == original
        instructions = archive.read("ChannelRecipe studio/CLAUDE.md")
        assert instructions.count(original) == 1
        assert instructions.endswith(overrides)
        assert instructions.index(b"operational overrides at the end govern") < instructions.index(original)
        assert instructions.index(b"# ChannelRecipe operational overrides") > instructions.index(original)
        assert b"never collect or reveal them in chat" in instructions
        assert b"Do not follow older standalone" in instructions
        assert b"synthetic-secret-must-not-ship" not in b"".join(archive.read(name) for name in names)
        assert archive.read("ChannelRecipe studio/example_engine.py") == b"# Synthetic engine fixture only\n"
        assert archive.getinfo("ChannelRecipe studio/Start on Mac.command").external_attr >> 16 & 0o111
        manifest = json.loads(archive.read("ChannelRecipe studio/cr-release.json"))
        assert manifest["version"] == package.VERSION
        assert manifest["access"] == "subscriber" and manifest["public_release_allowed"]
    assert report["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_missing_private_guide_does_not_replace_an_existing_release(synthetic_package, monkeypatch):
    tmp_path, _ = synthetic_package
    source = source_archive(tmp_path, monkeypatch, guide=None)
    target = tmp_path / "release.zip"
    target.write_bytes(b"previous complete release")
    with pytest.raises(ValueError, match="original Claude creative guide"):
        package.build(source, target)
    assert target.read_bytes() == b"previous complete release"


def test_changed_source_is_rejected_before_instruction_composition(synthetic_package, monkeypatch):
    tmp_path, _ = synthetic_package
    source = source_archive(tmp_path, monkeypatch, guide=b"Synthetic guide")
    monkeypatch.setattr(package, "SOURCE_SHA256", "0" * 64)
    target = tmp_path / "release.zip"
    with pytest.raises(ValueError, match="Source changed"):
        package.build(source, target)
    assert not target.exists()


def test_workspace_browser_resources_ship_without_local_cache_or_secret_files(synthetic_package, monkeypatch):
    tmp_path, _ = synthetic_package
    runtime = package.ROOT / "local_runtime"
    assets = {
        "cr_workspace.html": "<main>ChannelRecipe</main>",
        "cr_workspace.js": "window.CR_WORKSPACE_CLIENT = {};",
        "cr_workspace.css": "main{color:#171b2e}",
        "cr_workspace.py": "# Synthetic route adapter",
        "workspace_art/illustrated.svg": '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    }
    for name, text in assets.items():
        path = runtime / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
    (runtime / ".cr-connection.json").write_text('"do not ship"')
    (runtime / "workspace_art/render.log").write_text("do not ship")
    (runtime / "workspace_art/nested").mkdir()
    (runtime / "workspace_art/nested/unknown.svg").write_text("do not ship")
    source = source_archive(tmp_path, monkeypatch, guide=b"Synthetic guide")
    target = tmp_path / "release.zip"
    report = package.build(source, target)
    with zipfile.ZipFile(target) as archive:
        for name, text in assets.items():
            assert archive.read("ChannelRecipe studio/" + name).decode() == text
        assert not any(name.endswith((".json", ".log")) for name in archive.namelist()
                       if name not in {"ChannelRecipe studio/cr-release.json", "ChannelRecipe studio/cr-providers.json",
                                       "ChannelRecipe studio/cr-branding.json", "ChannelRecipe studio/cr-files.json"})
        assert not any("nested" in name for name in archive.namelist())
        # The updater's manifest lists every other shipped file with its exact hash.
        shipped = json.loads(archive.read("ChannelRecipe studio/cr-files.json"))["files"]
        others = {n.removeprefix("ChannelRecipe studio/") for n in archive.namelist()} - {"cr-files.json"}
        assert set(shipped) == others
        assert all(shipped[n] == hashlib.sha256(archive.read("ChannelRecipe studio/" + n)).hexdigest() for n in others)
        assert archive.read("ChannelRecipe studio/example_engine.py") == b"# Synthetic engine fixture only\n"
    assert report["engine_files"] == 2


@pytest.mark.parametrize("resource", ["cr_workspace.js", "workspace_art/example.svg", "workspace_art"])
def test_symlinked_workspace_resource_is_rejected_before_replacing_zip(synthetic_package, monkeypatch, resource):
    tmp_path, _ = synthetic_package
    runtime = package.ROOT / "local_runtime"
    outside = tmp_path / "outside"
    if resource == "workspace_art":
        outside.mkdir()
    else:
        outside.write_text("private outside resource")
    path = runtime / resource
    path.parent.mkdir(exist_ok=True)
    path.symlink_to(outside)
    source = source_archive(tmp_path, monkeypatch, guide=b"Synthetic guide")
    target = tmp_path / "release.zip"
    target.write_bytes(b"previous complete release")
    with pytest.raises(ValueError, match="owned"):
        package.build(source, target)
    assert target.read_bytes() == b"previous complete release"


def test_style_references_ship_with_credit_and_image_pins(synthetic_package, monkeypatch):
    tmp_path,_=synthetic_package
    source=source_archive(tmp_path,monkeypatch,guide=b"Synthetic creative guide")
    folder=package.ROOT/"data/frontier/recipe-references";folder.mkdir(parents=True)
    image=folder/"quest.jpg";image.write_bytes(b"Synthetic style reference")
    manifest={"schema":2,"kind":"style_reference","frames":{"quest":{"file":"quest.jpg","kind":"style_reference",
        "channel":"Reference Channel","title":"A reference","url":"https://www.youtube.com/watch?v=x",
        "sha256":hashlib.sha256(image.read_bytes()).hexdigest()}}}
    (folder/"manifest.json").write_text(json.dumps(manifest))
    target=tmp_path/"release.zip";package.build(source,target)
    with zipfile.ZipFile(target)as archive:
        assert archive.read("ChannelRecipe studio/cr_recipe_frames/quest.jpg")==image.read_bytes()
        assert json.loads(archive.read("ChannelRecipe studio/cr_recipe_frames/manifest.json"))==manifest
    image.write_bytes(b"changed")
    with pytest.raises(ValueError,match="reference frame changed"):
        package.build(source,target)
    with zipfile.ZipFile(target)as archive:
        assert archive.read("ChannelRecipe studio/cr_recipe_frames/quest.jpg")==b"Synthetic style reference"
    del manifest["frames"]["quest"]["channel"]
    (folder/"manifest.json").write_text(json.dumps(manifest));image.write_bytes(b"Synthetic style reference")
    with pytest.raises(ValueError,match="Invalid style reference"):
        package.build(source,target)


def test_retired_sample_films_are_not_shipped(synthetic_package, monkeypatch):
    tmp_path,_=synthetic_package
    source=source_archive(tmp_path,monkeypatch,guide=b"Synthetic creative guide")
    with zipfile.ZipFile(source,"a")as archive:
        for name in package.RETIRED_SAMPLES:
            archive.writestr("Frontier/"+name,b"old sample film")
        archive.writestr("Frontier/assets/overlay_dust.mp4",b"kept overlay")
    monkeypatch.setattr(package,"SOURCE_SHA256",hashlib.sha256(source.read_bytes()).hexdigest())
    target=tmp_path/"release.zip";package.build(source,target)
    with zipfile.ZipFile(target)as archive:
        names=archive.namelist()
        assert not any("sample.mp4" in n or "Shadow You Keep" in n for n in names)
        assert "ChannelRecipe studio/assets/overlay_dust.mp4" in names


def test_files_a_release_drops_are_listed_for_installed_studios_to_remove(tmp_path):
    previous = tmp_path / "channelrecipe-5.3-admin.44.zip"
    with zipfile.ZipFile(previous, "w") as archive:
        archive.writestr("ChannelRecipe studio/cr-files.json", json.dumps({"files": {
            "assets/brand/old sample.mp4": "a" * 64, "cr_recipe_frames/quest.jpg": "b" * 64,
            "cr_studio.py": "c" * 64}, "retired": {"assets/older.mp4": ["d" * 64],
                                                   "cr_recipe_frames/quest.jpg": ["e" * 64]}}))
    retired = package.retired_files(previous, {"cr_studio.py", "cr-files.json"})
    assert retired == {"assets/brand/old sample.mp4": ["a" * 64], "assets/older.mp4": ["d" * 64],
                       "cr_recipe_frames/quest.jpg": ["b" * 64, "e" * 64]}
    # A file shipped again is no longer retired.
    assert "cr_recipe_frames/quest.jpg" not in package.retired_files(previous, {"cr_recipe_frames/quest.jpg"})
    assert package.retired_files(None, set()) == {}
    (tmp_path / "channelrecipe-5.3-admin.43.zip").write_bytes(b"")
    (tmp_path / ("channelrecipe-" + package.VERSION + ".zip")).write_bytes(b"")
    assert package.previous_release(tmp_path) == previous


def test_rebrand_covers_source_owned_runtime_and_private_resources(synthetic_package, monkeypatch):
    tmp_path, _ = synthetic_package
    runtime = package.ROOT / "local_runtime"
    (runtime / "bridge.py").write_text('API = "/api/frontier/"\nWORKFLOW = "frontier-5.3-claudecode.1"\n')
    source = source_archive(tmp_path, monkeypatch, guide=b"# Frontier creative guide\n")
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("Frontier/cache.py", b'FRONTIER_OUTPUT = ".frontier-video"\n')
        archive.writestr("Frontier/assets/frontier-module.js", b'window._FRONTIER = "Frontier";\n')
    monkeypatch.setattr(package, "SOURCE_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    next(iter(package.PRIVATE_WORKFLOWS.values())).write_text("Private Frontier workflow fixture.")
    target = tmp_path / "release.zip"
    report = package.build(source, target)
    with zipfile.ZipFile(target) as archive:
        for member in archive.infolist():
            assert "frontier" not in member.filename.lower()
            assert b"frontier" not in archive.read(member).lower()
        assert archive.read("ChannelRecipe studio/cache.py") == b'CHANNELRECIPE_OUTPUT = ".channelrecipe-video"\n'
        assert archive.read("ChannelRecipe studio/bridge.py") == b'API = "/api/studio/"\nWORKFLOW = "channelrecipe-5.3-claudecode.1"\n'
        assert archive.read("ChannelRecipe studio/assets/channelrecipe-module.js") == b'window._CHANNELRECIPE = "ChannelRecipe";\n'
        provenance = json.loads(archive.read("ChannelRecipe studio/cr-branding.json"))
        assert provenance["source_engine_files"] == report["engine_files"] == 4
        for record in provenance["transformed_files"]:
            data = archive.read("ChannelRecipe studio/" + record["path"])
            assert hashlib.sha256(data).hexdigest() == record["packaged_sha256"]
        assert json.loads(archive.read("ChannelRecipe studio/cr-release.json"))["workflow_version"] == "channelrecipe-5.3-claudecode.1"
    assert report["branding"]["legacy_byte_matches"] == report["branding"]["legacy_path_matches"] == 0


def test_unknown_binary_branding_rejection_preserves_previous_complete_release(synthetic_package, monkeypatch):
    tmp_path, _ = synthetic_package
    source = source_archive(tmp_path, monkeypatch, guide=b"Synthetic guide")
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("Frontier/opaque.dat", b"\x00\xffopaque Frontier marker")
    monkeypatch.setattr(package, "SOURCE_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    target = tmp_path / "release.zip"
    target.write_bytes(b"previous complete release")
    with pytest.raises(ValueError, match="unsupported binary"):
        package.build(source, target)
    assert target.read_bytes() == b"previous complete release"
    assert not list(target.parent.glob(".cr-build-*"))


def test_engine_machine_checks_are_patched_for_customer_computers():
    reflearn = b'x = 1\n    need_lv, need_gb = _envf("REFLEARN_MIN_MEM_LEVEL", 40), _envf("REFLEARN_MIN_DISK_GB", 20)\n'
    patched = package.patch_engine("reflearn.py", reflearn).decode()
    assert '_envf("REFLEARN_MIN_DISK_GB", 3)' in patched and '"REFLEARN_MIN_MEM_LEVEL", 20' in patched
    quest = (b"def _wait_machine(log=print, min_level: int = 40, min_free_gb: float = 20.0) -> None:\n"
             b"    pass\n    lvl = 45 if light else 40\n")
    patched = package.patch_engine("quest_hud.py", quest).decode()
    assert "min_free_gb: float = 3.0" in patched and "lvl = 25 if light else 20" in patched
    assert package.patch_engine("other.py", b"unchanged") == b"unchanged"
    with pytest.raises(ValueError, match="no longer matches"):
        package.patch_engine("reflearn.py", b"a different engine")


def test_starting_the_engine_directly_hands_over_to_studio():
    app = (b'import sys\nHERE = 1\nif __name__ == "__main__":\n    OUT.mkdir(exist_ok=True)\n    print("x")\n')
    patched = package.patch_engine("app.py", app).decode()
    assert 'subprocess.call([sys.executable, str(HERE / "cr_studio.py")]' in patched
    compile(patched, "app.py", "exec")


def test_a_story_with_moving_pictures_shows_each_picture_on_its_own_sentence(tmp_path):
    engine = (b"import json, re, shutil\nfrom pathlib import Path\n\n\n"
              b"def _build_story_plan(job: Path, total: float, photos: list) -> list:\n"
              b"    items = json.loads((job / 'astro_scenes.json').read_text())\n"
              b"    pairs = [(it if isinstance(it, dict) else {}, p) for it, p in zip(items, photos) if p]\n"
              b"    n = len(items)\n"
              b"    beat = 2.5                              # shortest a picture may hold\n"
              b"    return [(it['text'], Path(p).name) for it, p in pairs]\n\n\n"
              b"def _story_order(job, total, photos):\n"
              b"    items = json.loads((job / 'astro_scenes.json').read_text())\n"
              b"    by_i = {}\n    for p in photos:\n"
              b"        m = re.search(r\"scene_(\\d+)\\.jpg$\", str(p))\n"
              b"        if m and int(m.group(1)) < len(items):\n"
              b"            by_i[int(m.group(1))] = str(p)\n"
              b"    if len(by_i) < max(3, len(photos) // 2):\n        return []\n"
              b"    idx = sorted(by_i)\n"
              b"    return [(items[i]['text'], Path(by_i[i]).name) for i in idx]\n\n\n"
              b"def _zoom_expr(mode, idx, amount, frames):\n"
              b'    p = f"min(on/{max(1, frames - 1)}\\\\,1)"\n'
              b'    if (mode or "in").lower() != "alternate":\n'
              b'        return f"(1+{amount:.5f}*{p})"\n'
              b'    return "alternate"\n')
    patched = package.patch_engine("make_video.py", engine).decode()
    ns = {}
    exec(compile(patched, "make_video.py", "exec"), ns)
    job = tmp_path / "job"
    for d in ("images", "clip_src", "clips"):
        (job / d).mkdir(parents=True)
    texts = ["London, 1820s.", "Your family hands you over.", "The sweep grins.", "You climb.", "Soot falls."]
    (job / "astro_scenes.json").write_text(json.dumps([{"text": t} for t in texts]))
    (job / "clip_src/vsrc_00.jpg").write_bytes(b"first frame")
    (job / "clips/clip_00.mp4").write_bytes(b"m" * 30_000)
    photos = [str(job / "images" / f"scene_{k:02d}.jpg") for k in range(4)]
    for p in photos:
        ns["Path"](p).write_bytes(b"still")
    plan = ns["_build_story_plan"](job, 20.0, photos)
    assert plan == [("London, 1820s.", "vsrc_00.jpg"), ("Your family hands you over.", "scene_00.jpg"),
                    ("The sweep grins.", "scene_01.jpg"), ("You climb.", "scene_02.jpg"), ("Soot falls.", "scene_03.jpg")]
    assert (job / "clip_src/vsrc_00.anim.mp4").read_bytes() == b"m" * 30_000, "the clip plays in its own slot"
    assert ns["_story_order"](job, 20.0, photos)[0] == ("Your family hands you over.", "scene_00.jpg")
    zoom = ns["_zoom_expr"]
    assert zoom("still", 3, 0.2, 50) == "(1)" and zoom("in", 3, 0.2, 50).startswith("(1+0.2")
    assert sum(zoom("mix:0.9", i, 0.2, 50) == "(1)" for i in range(100)) == 90
    assert zoom("mix:0.0:alternate", 1, 0.2, 50) == "alternate"
    assert "0.55 * total / max(1, n)" in patched, "a fast story's shortest hold follows its pace"


def test_ready_made_recipes_ship_as_built_in_styles_with_only_their_own_pictures(tmp_path):
    recipe = tmp_path / "data/frontier/recipes/cr-stick-stories"
    recipe.mkdir(parents=True)
    (recipe / "style.jpg").write_bytes(b"jpg")
    style = {"name": "cr-stick-stories", "builtin": True, "look": {"style_image": "styles/cr/cr-stick-stories/style.jpg"}}
    (recipe / "style.json").write_text(json.dumps(style))
    shipped = {dest for _, dest in package.private_recipe_resources(tmp_path)}
    assert shipped == {"styles/cr-stick-stories.json", "assets/styles/cr/cr-stick-stories/style.jpg"}
    style["look"]["character_image"] = "characters/someone_else.png"
    (recipe / "style.json").write_text(json.dumps(style))
    with pytest.raises(ValueError, match="its own pictures"):
        package.private_recipe_resources(tmp_path)
    style["look"].pop("character_image")
    style["cr_style_clone"] = {"operation_id": "op_x"}
    (recipe / "style.json").write_text(json.dumps(style))
    with pytest.raises(ValueError, match="no study records"):
        package.private_recipe_resources(tmp_path)
    assert package.private_recipe_resources(tmp_path / "elsewhere") == []


def test_cut_out_movement_runs_on_a_kie_only_setup():
    photofx = (b'import os\n'
               b'def depth_slots(cfg, every):\n'
               b'    if not cfg["depth"] or every <= 0 or not os.environ.get("WAVESPEED_API_KEY"):\n'
               b'        return set()\n'
               b'    return {1}\n'
               b'def scenes():\n'
               b'    cuts = bool((os.environ.get("WAVESPEED_API_KEY") or "").strip())\n'
               b'    return cuts\n')
    patched = package.patch_engine("photofx.py", photofx).decode()
    ns = {}
    exec(compile(patched, "photofx.py", "exec"), ns)
    import os
    saved = {k: os.environ.pop(k, None) for k in ("WAVESPEED_API_KEY", "KIE_API_KEY")}
    try:
        os.environ["KIE_API_KEY"] = "kie-test"
        assert ns["depth_slots"]({"depth": True}, 3) == {1} and ns["scenes"]() is True
    finally:
        os.environ.pop("KIE_API_KEY", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
