"""Private renderer facts are resolved locally, generically and without execution."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_manimgx_refs as refs
import cr_manimgx as gx


@pytest.fixture
def source(tmp_path):
    engine = tmp_path / "threed/engine"
    def write(name, text):
        path = engine / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path
    write("core/registry.js", "export const registryFixture = 1;\n")
    write("lib/nature/geometry.js", "import { helper } from '../shared/helper.js';\nexport const CATALOG = {'demo.shape': {params: {radius: 2}}};\nexport function build() { return helper(); }\n")
    write("lib/shared/helper.js", "import * as graphics from 'three';\nexport function helper() { return 1; }\nconst text = 'from';\nconst object = {from: 'not-an-import'};\n")
    catalog = {"version": 1, "kinds": {"demo.shape": {"module": "nature/geometry.js", "params": {"radius": 2, "amount": 1}}}}
    path = tmp_path / "threed/catalog.json"
    path.write_text(json.dumps(catalog))
    return tmp_path, engine, write, catalog


def scene(**item):
    return {"scene": {"objects": [{"kind": "demo.shape", **item}]},
            "cast": [], "video": {}, "output": {"width": 640, "height": 360, "fps": 30, "quality": "draft"}}


def test_compiler_reads_one_private_source_document_on_the_visual_role(source, monkeypatch):
    home, engine_folder, write, _ = source
    context = scene()
    bundle = refs.resolve(home, context)
    calls = []
    program = 'import manimgx as m\nclass ReferenceFixture(m.Scene):\n    def construct(self):\n        self.wait(1)\n'
    manifest = {'scene_class': 'ReferenceFixture', 'source': program, 'unsupported': [],
        'coverage': {pointer: 'Preserved by this source-aware fixture.' for pointer in gx._pointers(context)}}

    def native_read(engine, prompt, model, files, what):
        calls.append((prompt, model, files, what))
        assert len(files) == 1
        reference = Path(files[0])
        assert reference.is_file() and reference.stat().st_mode & 0o077 == 0
        assert 'export function helper()' in reference.read_text()
        assert 'export function helper()' not in prompt
        return json.dumps(manifest)

    monkeypatch.setitem(sys.modules, 'threed', SimpleNamespace(_cc=native_read))
    monkeypatch.setattr(gx, '_api_reference', lambda docs: 'Pinned API fixture data.')
    monkeypatch.setattr(gx, '_typecheck', lambda *args, **kwargs: None)
    engine = SimpleNamespace(CLAUDE_PROVIDER='claudecode', _cr_original_cc_model='sonnet',
        claude=lambda *args, **kwargs: pytest.fail('Do not switch to a text/API compiler.'))
    folder = home / 'private-compiler'
    result, path = gx._compiled_source(engine, None, context, {}, {'python': 'fixture', 'docs': 'fixture'},
        folder, bundle['identity'], reference_bundle=bundle)
    assert result == manifest and path.is_file()
    assert calls[0][1] == 'opus' and calls[0][3] == 'ManimGX compile'
    assert 'runner coverage' in calls[0][0]
    assert 'different camera parameter names alone' in calls[0][0]
    gx._compiled_source(engine, None, context, {}, {'python': 'fixture', 'docs': 'fixture'},
        folder, bundle['identity'], reference_bundle=bundle)
    assert len(calls) == 1  # Successful typed source is reused without Claude.


def test_exact_catalog_module_default_merge_and_transitive_source_hashes(source):
    home, engine, write, catalog = source
    context = scene(params={"radius": 3, "amount": 4}, radius=5)
    before = copy.deepcopy(context)
    result = refs.resolve(home, context)
    assert context == before
    assert len(result["source_paths"]) == 3
    row = result["context"]["kinds"][0]
    assert row["kind"] == "demo.shape" and row["pointer"] == "/scene/objects/0"
    assert row["resolved_parameters"]["radius"] == 5 and row["resolved_parameters"]["amount"] == 4
    assert row["resolved_module"] == "lib/nature/geometry.js"
    assert result["context"]["external_library_imports"] == ["three"]
    for record in result["sources"]:
        assert hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest() == record["sha256"]
    assert "/output/fps" in result["context"]["runner_owned_fields"]
    assert refs.resolve(home, context)["identity"] == result["identity"]
    write("lib/shared/helper.js", "export function helper() { return 2; }\n")
    assert refs.resolve(home, context)["identity"] != result["identity"]


def test_nested_recipe_or_metadata_kind_is_not_an_original_library_selector(source):
    home, engine, write, catalog = source
    context = scene(parts=[{"shape": "box", "material": {"kind": "nested_fixture"}}])
    context["video"] = {"kind": "descriptive_metadata"}
    result = refs.resolve(home, context)
    assert [row["kind"] for row in result["context"]["kinds"]] == ["demo.shape"]


def test_static_bundled_images_are_verified_without_external_fetches(source):
    home, engine, write, catalog = source
    write("lib/nature/geometry.js", "const BASE = new URL('./data/', import.meta.url).href;\nconst present = image(BASE + 'fixture.png');\nconst absent = 'missing.jpg';\nconst remote = 'https://example.invalid/photo.jpg';\n")
    path = engine / "lib/nature/data/fixture.png"
    path.parent.mkdir(); path.write_bytes(b"private local image fixture")
    result = refs.resolve(home, scene())
    asset = result["assets"]["original:lib/nature/data/fixture.png"]
    assert asset["path"] == str(path) and asset["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert asset["source"] == "private_original_renderer"
    assert len(result["context"]["unresolved_static_images"]) == 1
    assert result["context"]["unresolved_static_images"][0]["literal"] == "missing.jpg"


@pytest.mark.parametrize("change", ["module", "import", "asset", "symlink"])
def test_catalog_import_asset_and_symlink_escapes_are_refused(source, change):
    home, engine, write, catalog = source
    outside = home / "outside.js"; outside.write_text("private unrelated file")
    if change == "module":
        catalog["kinds"]["demo.shape"]["module"] = "nature/../../../../outside.js"
        (home / "threed/catalog.json").write_text(json.dumps(catalog))
    elif change == "import":
        write("lib/nature/geometry.js", "import stuff from '../../../../outside.js';\n")
    elif change == "asset":
        write("lib/nature/geometry.js", "const source = '../../../../outside.png';\n")
    else:
        path = engine / "lib/nature/geometry.js"; path.unlink(); path.symlink_to(outside)
    with pytest.raises(RuntimeError, match="outside|inside"):
        refs.resolve(home, scene())


def test_missing_unknown_or_oversized_source_never_truncates_or_substitutes(source):
    home, engine, write, catalog = source
    with pytest.raises(RuntimeError, match="absent"):
        refs.resolve(home, scene(kind="unknown.geometry"))
    with pytest.raises(RuntimeError, match="bounded.*limit"):
        refs.resolve(home, scene(), max_files=1)
    with pytest.raises(RuntimeError, match="bounded.*limit"):
        refs.resolve(home, scene(), max_source_bytes=20)
    (engine / "lib/shared/helper.js").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        refs.resolve(home, scene())


def test_private_document_has_exact_line_index_and_refuses_changed_sources(source):
    home, engine, write, catalog = source
    result = refs.resolve(home, scene())
    text = refs.reference_text(result)
    lines = text.splitlines()
    indexes = [json.loads(line) for line in lines if line.startswith('{"reference":')]
    assert len(indexes) == len(result["sources"])
    for index in indexes:
        original = (engine / index["reference"]).read_text().splitlines()
        assert lines[index["start_line"] - 1:index["end_line"]] == original
    write("lib/shared/helper.js", "export function helper() { return 99; }")
    with pytest.raises(RuntimeError, match="changed"):
        refs.reference_text(result)


def test_object_modules_follow_the_original_index_order_instead_of_name_guessing(source):
    home, engine, write, catalog = source
    catalog["kinds"]["demo.shape"]["module"] = "human/(object)"
    (home / "threed/catalog.json").write_text(json.dumps(catalog))
    write("lib/human/index.js", "import * as first from './unexpected-name.js';\nimport * as second from './another.js';\nexport const MODULES = [first, second];\n")
    write("lib/human/unexpected-name.js", "export const CATALOG = {'demo.shape': {}};\n")
    write("lib/human/another.js", "export const CATALOG = {'demo.shape': {}};\n")
    result = refs.resolve(home, scene())
    assert result["context"]["kinds"][0]["resolved_module"] == "lib/human/unexpected-name.js"
    assert not any(row["relative_path"].endswith("another.js") for row in result["sources"])


def test_object_module_documentation_is_not_mistaken_for_a_catalog_definition(source):
    home, engine, write, catalog = source
    catalog["kinds"]["demo.shape"]["module"] = "human/(object)"
    (home / "threed/catalog.json").write_text(json.dumps(catalog))
    write("lib/human/index.js", "import * as first from './docs.js';\nimport * as second from './builder.js';\nexport const MODULES = [first, second];\n")
    write("lib/human/docs.js", "const example = \"'demo.shape': {}\";\nexport const CATALOG = {'another.kind': {}};\n")
    write("lib/human/builder.js", "export const CATALOG = {'demo.shape': {}};\n")
    result = refs.resolve(home, scene())
    assert result["context"]["kinds"][0]["resolved_module"] == "lib/human/builder.js"


def test_camera_look_labels_and_motion_contracts_are_resolved_from_actual_source(source):
    home, engine, write, catalog = source
    for name in ("core/camera.js", "core/look.js", "lib/human/labels.js", "core/loco.js"):
        write(name, "export const fixtureDefinition = 1;\n")
    context = scene(path=[[0, 0], [1, 1]], speed=3)
    context["scene"].update(camera={"move": "keys"}, look={"grade": "fixture"}, labels=[{"style": "fixture"}])
    result = refs.resolve(home, context)
    roles = {row["role"] for row in result["sources"]}
    assert {"original_camera_contract", "original_look_contract", "original_label_contract", "original_motion_contract"} <= roles


def test_actual_private_catalog_resolves_object_module_assets_and_world_contract_without_copying_source():
    home = Path(os.environ.get("FRONTIER_PRIVATE_HOME", "/Users/workcomputer/Downloads/ChannelRecipe studio"))
    if not (home / "threed/catalog.json").is_file():
        pytest.skip("owner's original private installation unavailable")
    context = {"scene": {"objects": [{"kind": "space.earth"}, {"kind": "prop.build"}],
        "world": {"biome": "space"}, "camera": {"move": "keys"}, "look": {"grade": "cold"},
        "labels": [{"style": "almanac_sans"}]}, "cast": [], "video": {}, "output": {"fps": 30}}
    result = refs.resolve(home, context)
    assert any(row["resolved_module"] == "lib/human/propbuild.js" for row in result["context"]["kinds"])
    assert {"original:lib/nature/data/earth_day.jpg", "original:lib/nature/data/earth_aux.png"} <= result["assets"].keys()
    assert result["context"]["source_bytes"] < refs.MAX_SOURCE_BYTES
    assert len(result["sources"]) < refs.MAX_FILES
    assert all(Path(row["path"]).is_relative_to(home / "threed/engine") for row in result["sources"])
    assert refs.reference_text(result).startswith("PRIVATE ORIGINAL RENDERER REFERENCE DATA")


def _large_inventory(source):
    """A full dependency inventory larger than the former prompt budget."""
    home, engine, write, _ = source
    write("lib/nature/geometry.js", "import './chunk0.js';\nexport const CATALOG = {'demo.shape': {}};\n")
    for index in range(8):
        dependency = "import './chunk%d.js';\n" % (index + 1) if index < 7 else ""
        write("lib/nature/chunk%d.js" % index,
              dependency + "// private exact source fixture\n" * 7000)
    result = refs.resolve(home, scene())
    assert result["context"]["source_bytes"] > refs.INLINE_SOURCE_BYTES
    return result


def test_large_inventory_uses_private_exact_indexed_files_without_a_giant_prompt(source):
    home, engine, write, _ = source
    result = _large_inventory(source)
    reference = home / "private-request/original-renderer-reference.txt"
    document = refs.save_reference(reference, result)
    # Recovery/correction moves the index into its own private child directory.
    # Relative paths keep its content hash stable without referring to old work.
    moved = home / "recovery-child" / "original-reference.txt"
    assert refs.save_reference(moved, result) == document
    assert reference.read_text() == document
    assert reference.stat().st_mode & 0o077 == 0
    assert "INDEXED DELIVERY" in document
    assert "// private exact source fixture" not in document
    assert len(document.encode()) < refs.INLINE_SOURCE_BYTES
    indexes = [json.loads(line) for line in document.splitlines()
               if line.startswith('{"reference":')]
    assert len(indexes) == len(result["sources"])
    by_name = {row["relative_path"]: row for row in result["sources"]}
    for row in indexes:
        copied = reference.parent / row["read_path"]
        original = by_name[row["reference"]]
        assert copied.is_relative_to(reference.parent)
        assert copied.read_bytes() == Path(original["path"]).read_bytes()
        assert copied.stat().st_mode & 0o077 == 0
        assert copied.parent.stat().st_mode & 0o077 == 0
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == row["sha256"]
        assert copied.stat().st_size == row["bytes"]


def test_large_inventory_reaches_the_existing_native_visual_compiler_as_one_index(source, monkeypatch):
    home, engine_folder, write, _ = source
    context = scene()
    bundle = _large_inventory(source)
    calls = []
    program = 'import manimgx as m\nclass IndexedFixture(m.Scene):\n    def construct(self):\n        self.wait(1)\n'
    manifest = {'scene_class': 'IndexedFixture', 'source': program, 'unsupported': [],
        'coverage': {pointer: 'Preserved by the indexed source fixture.' for pointer in gx._pointers(context)}}

    def native_read(engine, prompt, model, files, what):
        calls.append((model, files, what))
        assert len(files) == 1
        reference = Path(files[0])
        document = reference.read_text()
        assert "INDEXED DELIVERY" in document
        assert "// private exact source fixture" not in document + prompt
        indexes = [json.loads(line) for line in document.splitlines()
                   if line.startswith('{"reference":')]
        assert len(indexes) == len(bundle["sources"])
        assert all((reference.parent / row["read_path"]).is_file() for row in indexes)
        return json.dumps(manifest)

    monkeypatch.setitem(sys.modules, 'threed', SimpleNamespace(_cc=native_read))
    monkeypatch.setattr(gx, '_api_reference', lambda docs: 'Pinned API fixture data.')
    monkeypatch.setattr(gx, '_typecheck', lambda *args, **kwargs: None)
    engine = SimpleNamespace(CLAUDE_PROVIDER='claudecode', _cr_original_cc_model='sonnet',
        claude=lambda *args, **kwargs: pytest.fail('Do not switch to a text/API compiler.'))
    folder = home / 'private-compiler'
    result, path = gx._compiled_source(engine, None, context, {}, {'python': 'fixture', 'docs': 'fixture'},
        folder, bundle['identity'], reference_bundle=bundle)
    assert result == manifest and path.is_file()
    assert calls[0][0] == 'opus' and calls[0][2] == 'ManimGX compile'
    gx._compiled_source(engine, None, context, {}, {'python': 'fixture', 'docs': 'fixture'},
        folder, bundle['identity'], reference_bundle=bundle)
    assert len(calls) == 1


def test_large_reference_rejects_changed_source_before_writing_delivery_files(source):
    home, engine, write, _ = source
    result = _large_inventory(source)
    reference = home / "private-request/original-renderer-reference.txt"
    write("lib/nature/chunk7.js", "export const changed = true;\n")
    with pytest.raises(RuntimeError, match="changed"):
        refs.save_reference(reference, result)
    assert not reference.parent.exists()


def test_large_reference_rejects_index_escape_before_writing_delivery_files(source):
    home, engine, write, _ = source
    result = _large_inventory(source)
    reference = home / "private-request/original-renderer-reference.txt"
    result["sources"][0]["relative_path"] = "../../outside.js"
    with pytest.raises(RuntimeError, match="inside"):
        refs.save_reference(reference, result)
    assert not reference.parent.exists()


def test_small_reference_saver_preserves_the_existing_document_and_identity(source):
    home, engine, write, _ = source
    result = refs.resolve(home, scene())
    identity = result["identity"]
    original_document = refs.reference_text(result)
    reference = home / "private-request/original-renderer-reference.txt"
    assert refs.save_reference(reference, result) == original_document
    assert reference.read_text() == original_document
    assert not (reference.parent / "renderer-reference-sources").exists()
    assert refs.resolve(home, scene())["identity"] == identity


def test_actual_private_camera_and_people_dependencies_exceed_old_limit_but_remain_complete(tmp_path):
    home = Path(os.environ.get("FRONTIER_PRIVATE_HOME", "/Users/workcomputer/Downloads/ChannelRecipe studio"))
    if not (home / "threed/catalog.json").is_file():
        pytest.skip("owner's original private installation unavailable")
    context = {"scene": {"objects": [{"kind": "landmark.firebase"}],
        "camera": {"move": "keys"}, "look": {"grade": "cold"},
        "labels": [{"style": "almanac_sans"}]}, "cast": [], "video": {},
        "output": {"width": 640, "height": 360, "fps": 30, "quality": "draft"}}
    original = copy.deepcopy(context)
    result = refs.resolve(home, context)
    assert context == original
    assert result["context"]["source_bytes"] > refs.INLINE_SOURCE_BYTES
    assert result["context"]["source_bytes"] < refs.MAX_SOURCE_BYTES
    assert any(row["relative_path"] == "lib/human/firebase.js" for row in result["sources"])
    reference = tmp_path / "private-request/original-renderer-reference.txt"
    document = refs.save_reference(reference, result)
    indexes = [json.loads(line) for line in document.splitlines()
               if line.startswith('{"reference":')]
    assert len(indexes) == len(result["sources"])
    assert sum(row["bytes"] for row in indexes) == result["context"]["source_bytes"]
    for row in indexes:
        assert hashlib.sha256((reference.parent / row["read_path"]).read_bytes()).hexdigest() == row["sha256"]
