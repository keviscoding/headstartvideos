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
        assert archive.read("ChannelRecipe studio/FRONTIER_GUIDE.md") == original
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
        assert manifest["access"] == "admin_only" and not manifest["public_release_allowed"]
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
                       if name not in {"ChannelRecipe studio/cr-release.json", "ChannelRecipe studio/cr-providers.json"})
        assert not any("nested" in name for name in archive.namelist())
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
