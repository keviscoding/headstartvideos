"""Approved reference studies stay owned, bounded and original; no paid calls."""
import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_claude
import cr_style_clone as clone

PROJECT = "crv_" + "1" * 24
VIDEO = "abcdefghijk"
OTHER = "mnopqrstuvw"
ORIGINAL = Path("/Users/workcomputer/Downloads/ChannelRecipe studio")


@pytest.fixture
def approved(tmp_path, monkeypatch):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles/documentary.json").write_text(json.dumps({"name": "documentary", "label": "Documentary"}))
    cfg = {"key": "cr_mcp_synthetic_account", "brain_mode": "claudecode", "costs_accepted": True,
           "general_model": cr_claude.GENERAL_MODEL, "visual_model": cr_claude.VISUAL_MODEL}
    proof = {"local_version": "5.3-admin.17", "brain_mode": "claudecode", "purpose": "creative",
             "general_model": cfg["general_model"], "visual_model": cfg["visual_model"],
             "api_provider": "firstParty", **dict.fromkeys(("claude_installed", "claude_authenticated",
                 "ffmpeg_ready", "browser_ready", "node_ready", "models_verified", "costs_accepted"), True)}
    events = []
    def begin(name, payload):
        events.append(("begin", payload))
        return {"project": {"id": PROJECT}}
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: dict(cfg), _mcp_tool=begin, _project=None,
        prepare_runtime=lambda p: events.append(("prepare", p)),
        project_progress=lambda p: events.append(("progress", p)),
        finish_project=lambda p: events.append(("finish", p)))
    monkeypatch.setattr(cr_claude, "sync_receipts", lambda b: events.append(("sync", None)))
    metadata = SimpleNamespace(_flat=lambda url, count, timeout: [{"id": VIDEO, "title": "Reference video",
        "channel": "Reference creator", "views": 200000, "duration": 900}])
    preview = clone.preview(tmp_path, "https://youtube.com/watch?v=" + VIDEO, learner=metadata)
    spec = {"preview_id": preview["preview_id"], "reference_ids": [VIDEO], "label": "My learned style",
            "title": "Example story", "acknowledge_costs": True, "visual_mode": "mixed"}
    return SimpleNamespace(home=tmp_path, cfg=cfg, proof=proof, bridge=bridge, events=events,
                           preview=preview, spec=spec, metadata=metadata)


def ready_fixture(folder, spec, progress):
    progress("watch")
    (folder / "genkit").mkdir(exist_ok=True)
    (folder / "genkit/kit.js").write_text("function draw() {}")
    (folder / "genkit/kit.json").write_text(json.dumps({"name": "learned", "js": "kit.js",
        "types": {"a": 5, "b": 5, "c": 5}, "palette": {"accent": "#fff"}}))
    clone._write(folder / "ready-style.json", {"name": spec["style_id"], "label": spec["label"],
        "look": {"graphics": "kit", "kit": "learned", "genkit": {"manifest": str(folder / "genkit/kit.json")}},
        "cr_style_clone": {"operation_id": folder.name}})


@pytest.mark.parametrize("url,expected", [
    ("https://youtu.be/abcdefghijk?si=tracking", "https://www.youtube.com/watch?v=abcdefghijk"),
    ("https://www.youtube.com/shorts/abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"),
    ("https://youtube.com/@creator/videos?view=popular", "https://www.youtube.com/@creator/videos"),
    ("https://youtube.com/channel/UCexample", "https://www.youtube.com/channel/UCexample/videos"),
])
def test_public_youtube_inputs_are_canonical_and_tracking_is_dropped(url, expected):
    assert clone.normalize_url(url) == expected


@pytest.mark.parametrize("url", ["https://youtube.com.evil/watch?v=abcdefghijk", "http://youtube.com/@creator",
    "https://user:pass@youtube.com/@creator", "https://youtube.com:8443/@creator", "file:///private/file",
    "https://127.0.0.1/", "https://youtube.com/watch?v=short", "https://youtube.com/playlist?list=abc",
    "https://youtube.com/@creator\n"])
def test_nonpublic_or_incomplete_inputs_cannot_be_fetch_targets(url):
    with pytest.raises(ValueError):
        clone.normalize_url(url)


def test_preview_is_bounded_original_metadata_with_no_model_calls(approved):
    observed = []
    def flat(url, count, timeout):
        observed.append((url, count, timeout))
        return [{"id": VIDEO, "title": "Long video", "duration": 900, "views": 40},
                {"id": OTHER, "title": "Short video", "duration": 20, "views": 100}]
    result = clone.preview(approved.home, "https://youtube.com/@creator", learner=SimpleNamespace(_flat=flat))
    assert observed == [("https://www.youtube.com/@creator/videos", 8, 90)]
    assert result["no_model_requests"] and [x["eligible"] for x in result["candidates"]] == [True, False]
    assert approved.events == []
    saved = approved.home / "output/cr-style-clone/.previews" / (result["preview_id"] + ".json")
    if os.name != "nt":
        assert saved.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("change", [{"reference_ids": [OTHER]}, {"reference_ids": [VIDEO, VIDEO]},
    {"reference_ids": [VIDEO] * 4}, {"acknowledge_costs": False}, {"max_calls": 81},
    {"budget_minutes": 31}, {"budget_minutes": True}, {"base_style": "../../file"},
    {"name": "../preset"}, {"label": "line\nbreak"}])
def test_only_explicit_eligible_references_and_bounded_approvals_can_prepare(approved, change):
    with pytest.raises(ValueError):
        clone.prepare(approved.home, {**approved.spec, **change}, approved.proof, approved.bridge)
    assert not any(name == "begin" for name, _ in approved.events)


@pytest.mark.parametrize("field", ["models_verified", "costs_accepted", "claude_authenticated", "browser_ready"])
def test_missing_native_readiness_never_starts_a_session(approved, field):
    with pytest.raises(RuntimeError, match="native Claude"):
        clone.prepare(approved.home, approved.spec, {**approved.proof, field: False}, approved.bridge)
    assert approved.events == []


def test_same_approval_reuses_one_private_operation_and_pins_actual_session_limit(approved):
    one = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    two = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    assert one["operation_id"] == two["operation_id"] and one["state"] == "prepared"
    result = clone.run(approved.home, one["operation_id"], approved.bridge,
                       readiness=lambda: approved.proof, execute=ready_fixture)
    assert result["state"] == "done" and result["style_ready"]
    begin = next(payload for name, payload in approved.events if name == "begin")
    assert begin["setup"]["purpose"] == "creative" and begin["max_calls"] == 40
    assert begin["style"] == "documentary" and begin["options"] == {} and begin["features"] == {}
    finish = next(payload for name, payload in approved.events if name == "finish")
    assert finish["status"] == "done" and {x["name"] for x in finish["artifacts"]} == {"learned-style.json", "style-study-report.json"}
    assert str(approved.home) not in json.dumps(finish) and "synthetic_account" not in json.dumps(finish)
    clone.run(approved.home, one["operation_id"], approved.bridge, readiness=lambda: pytest.fail("readiness rerun"),
              execute=lambda *args: pytest.fail("paid duplicate"))
    assert sum(name == "begin" for name, _ in approved.events) == 1


def test_foreign_account_cannot_read_launch_or_sync_saved_study(approved):
    result = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    approved.cfg["key"] = "cr_mcp_different_synthetic_account"
    for operation in (lambda: clone.status(approved.home, result["operation_id"], approved.bridge),
                      lambda: clone.sync(approved.home, result["operation_id"], approved.bridge),
                      lambda: clone.run(approved.home, result["operation_id"], approved.bridge,
                                       readiness=lambda: approved.proof, execute=lambda *a: pytest.fail("foreign generation"))):
        with pytest.raises(RuntimeError, match="owns this"):
            operation()


def test_changed_model_profile_or_base_config_stops_before_session(approved):
    result = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    approved.cfg["visual_model"] = cr_claude.GENERAL_MODEL
    with pytest.raises(RuntimeError, match="pinned"):
        clone.run(approved.home, result["operation_id"], approved.bridge,
                  readiness=lambda: {**approved.proof, "visual_model": cr_claude.GENERAL_MODEL})
    approved.cfg["visual_model"] = cr_claude.VISUAL_MODEL
    (approved.home / "styles/documentary.json").write_text('{"name":"documentary","changed":true}')
    with pytest.raises(RuntimeError, match="base preset changed"):
        clone.run(approved.home, result["operation_id"], approved.bridge, readiness=lambda: approved.proof)
    assert not any(name == "begin" for name, _ in approved.events)


def test_lost_begin_uses_same_id_without_study_before_authorization(approved):
    result = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    attempts = []
    original = approved.bridge._mcp_tool
    def offline(name, payload):
        attempts.append(payload["begin_id"])
        raise RuntimeError("offline")
    approved.bridge._mcp_tool = offline
    with pytest.raises(RuntimeError, match="offline"):
        clone.run(approved.home, result["operation_id"], approved.bridge,
                  readiness=lambda: approved.proof, execute=lambda *a: pytest.fail("unapproved work"))
    def online(name, payload):
        attempts.append(payload["begin_id"])
        return original(name, payload)
    approved.bridge._mcp_tool = online
    clone.run(approved.home, result["operation_id"], approved.bridge, readiness=lambda: approved.proof, execute=ready_fixture)
    assert attempts[0] == attempts[1]


def test_uncertain_native_failure_cannot_be_automatically_executed_again(approved):
    result = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    def stopped(*args):
        raise cr_claude.ClaudeStageStopped("usage limit")
    with pytest.raises(cr_claude.ClaudeStageStopped):
        clone.run(approved.home, result["operation_id"], approved.bridge, readiness=lambda: approved.proof, execute=stopped)
    assert clone.status(approved.home, result["operation_id"], approved.bridge)["needs_manual_recovery"]
    with pytest.raises(RuntimeError, match="will not execute again"):
        clone.run(approved.home, result["operation_id"], approved.bridge,
                  readiness=lambda: approved.proof, execute=lambda *a: pytest.fail("paid retry"))
    assert sum(name == "begin" for name, _ in approved.events) == 1


def test_completion_sync_never_repeats_native_work(approved):
    result = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    finish = approved.bridge.finish_project
    approved.bridge.finish_project = lambda payload: (_ for _ in ()).throw(RuntimeError("offline"))
    pending = clone.run(approved.home, result["operation_id"], approved.bridge, readiness=lambda: approved.proof, execute=ready_fixture)
    assert pending["state"] == "pending" and pending["style_ready"] and pending["reconciliation_pending"]
    approved.bridge.finish_project = finish
    assert clone.sync(approved.home, result["operation_id"], approved.bridge)["state"] == "done"
    assert sum(name == "begin" for name, _ in approved.events) == 1


def test_other_existing_style_is_never_overwritten_after_study(approved):
    result = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    def conflicted(folder, spec, progress):
        ready_fixture(folder, spec, progress)
        (approved.home / "styles" / (spec["style_id"] + ".json")).write_text('{"name":"existing","look":{"keep":true}}')
    with pytest.raises(RuntimeError, match="preserved"):
        clone.run(approved.home, result["operation_id"], approved.bridge, readiness=lambda: approved.proof, execute=conflicted)
    folder, saved = clone._saved(approved.home, result["operation_id"], approved.bridge)
    assert saved["state"] == "validated"
    # Publication failure cannot trigger another study, even after a user fixes
    # the naming conflict on disk and reconnects.
    (approved.home / "styles" / (saved["spec"]["style_id"] + ".json")).unlink()
    final = clone.run(approved.home, result["operation_id"], approved.bridge,
                      readiness=lambda: pytest.fail("paid resume"), execute=lambda *a: pytest.fail("paid resume"))
    assert final["style_ready"] and final["state"] == "done"


def test_running_study_holds_global_isolation_lock(approved):
    result = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    with clone._lock(clone._folder(approved.home), ".runner.lock"):
        with pytest.raises(RuntimeError, match="already working"):
            clone.run(approved.home, result["operation_id"], approved.bridge,
                      readiness=lambda: approved.proof, execute=lambda *a: pytest.fail("concurrent generation"))
    assert not any(name == "begin" for name, _ in approved.events)


def _original_functions(path, names, namespace):
    if not path.is_file():
        pytest.skip("Pinned original runtime is not installed on this test computer")
    tree = ast.parse(path.read_text())
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in definitions} == set(names)
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("visual_mode,reviewed", [("mixed", True), ("reference_only", True), ("reference_only", False)])
def test_actual_original_learner_uses_only_approved_videos_and_keeps_full_creative_steps(approved, monkeypatch, visual_mode, reviewed):
    events = []
    folder = approved.home / "output/cr-style-clone" / ("op_" + "2" * 24)
    folder.mkdir(parents=True)
    spec = clone._spec(approved.home, approved.spec)
    spec["visual_mode"] = visual_mode
    reference_ids = [VIDEO]
    if visual_mode == "reference_only":
        reference_ids += [OTHER, "xyzabcdefgh"]
        spec["references"] += [{**spec["references"][0], "id": identifier} for identifier in reference_ids[1:]]
        guidance = folder / "study-guidance.txt"
        guidance.write_text("Full-scene illustration fixture.")
        monkeypatch.setattr(clone, "STUDY_GUIDANCE", guidance)
    spec["style_id"] = "cr_clone_custom"
    sheet = folder / "actual-downloaded-frame.jpg"
    sheet.write_bytes(b"synthetic frame fixture")
    class Clock:
        def __init__(self, *args): self.log = lambda message: None
        def left(self): return 1800
        def step(self, name): pass
        def end(self): pass
        def report(self): return {"minutes": {}, "total_min": 0, "cost": {}, "total_usd": 0, "total_usd_api": 0}
    def watch_impl(reference, clock, work):
        events.append(("watch", reference["id"]))
        return {"sheets": [str(sheet)], "frames": [str(sheet)], "measures": {"cuts": 12}}
    watch_impl.__globals__["watch_remote"] = lambda *args: pytest.fail("API brain fallback")
    def make_kit(engine, title, analysis, genome, spec, clock, stage, channel):
        events.append(("build", channel)); stage.mkdir(parents=True)
        (stage / "kit.json").write_text(json.dumps({"name": "learned", "label": "Learned", "types": {"a": 4, "b": 4, "c": 4}}))
        (stage / "kit.js").write_text("original learner output")
        return {"wants": {}}
    def install(stage, dest):
        import shutil
        shutil.copytree(stage, dest)
    namespace = {"Path": Path, "shutil": __import__("shutil"), "GENKIT": "genkit", "LIB_MIN": .62,
        "match": lambda *a: pytest.fail("unrelated library selection"), "mode": lambda: "fresh",
        "_envf": lambda name, default: default,
        "research": lambda *a: pytest.fail("unrelated research"), "watch": watch_impl,
        "_fallback": lambda *a: pytest.fail("plain-style fallback"),
        "learn_spec": lambda *a: (events.append(("spec", a[4]["id"])) or {"style_name": "Learned", "signatures": [{"id": "a"}], "genome_wants": {}}),
        "build_kit": make_kit,
        "_media": lambda *a, **kw: (events.append(("validation_media", kw.get("fetch"))) or {}),
        "validate": lambda *a: (events.append(("validate", True)) or {"kept": ["a", "b", "c"], "bad": [], "browser": True,
                                                                   "critique": {"score": 8} if reviewed else {}}),
        "_strip_axes_file": lambda *a: {}, "_install": install,
        "board": lambda path, *a: path,
        "_slug": lambda text, n=60: "reference_library", "save_library": lambda *a: (events.append(("library_save", a[2]["id"])) or folder),
        "_read_json": lambda path: json.loads(Path(path).read_text())}
    namespace.update(copy=copy, VERSION="test-pinned-original")
    namespace = _original_functions(ORIGINAL / "reflearn.py", {"_learn", "around", "summary"}, namespace)
    learner = SimpleNamespace(**{**namespace, "Clock": Clock, "WORK": "reflearn",
        })
    engine = SimpleNamespace(STYLE_INFO={"documentary": {"language": "English"}}, set_extra=lambda text: events.append(("brief", text)))
    def genkit(style, job, log, invalidate):
        style["look"] = {"graphics": "kit", "kit": "learned", "genkit": {"manifest": str(job / "genkit/kit.json")}}
        return "learned"
    designer = SimpleNamespace(design=lambda *a, **kw: {"genome": {"analysis": {"genre": ["history_modern"]}}},
        build=lambda *a, **kw: {"name": "auto-look", "variant_of": "documentary", "look": {}, "prompts": {"outline": "original contract"}},
        save=lambda *a: None, genkit=genkit)
    transcripts = []
    def transcript(video, work, original):
        transcripts.append((video, original)); return [(0, "Reference narration"), (600, "Later narration")]
    if not reviewed:
        with pytest.raises(clone.StudyStopped, match="visual comparison"):
            clone.study_original(engine, folder, spec, lambda phase: None, learner=learner, designer=designer, transcript=transcript)
        assert not (folder / "ready-style.json").exists()
        return
    style = clone.study_original(engine, folder, spec, lambda phase: events.append(("phase", phase)),
        learner=learner, designer=designer, transcript=transcript)
    assert [(name, value) for name, value in events if name in {"watch", "spec", "build", "validate", "library_save"}] == [
        *[("watch", identifier) for identifier in reference_ids], ("spec", VIDEO), ("build", "Reference creator"), ("validate", True), ("library_save", VIDEO)]
    assert ("validation_media", False) in events and transcripts == [(identifier, True) for identifier in reference_ids]
    evidence = clone._read(folder / "reflearn/reference-evidence.json")
    assert [row["id"] for row in evidence["references_studied"]] == reference_ids
    assert "variant_of" not in style and style["name"] == "cr_clone_custom" and style["auto_style"] is False
    assert style["prompts"]["outline"] == "original contract" and style["look"]["graphics"] == "kit"
    assert clone._read(folder / "reflearn/reference-evidence.json")["actual_frames"]
    assert clone._read(folder / "design-checkpoint.json")["design"]["genome"]
    validated = clone._read(folder / "validated-checkpoint.json")
    assert validated["record"]["browser"] and validated["assets"]["genkit/kit.js"]
    # The actual original catalogue must list the resulting custom file, and
    # the actual original kit reader must register its durable manifest.
    _catalogue_accepts_style(approved.home, style)


def _catalogue_accepts_style(home, style):
    path = home / "styles" / (style["name"] + ".json")
    clone._write(path, style)
    namespace = {"HERE": home, "STYLE_DIR": home / "styles", "STYLES": {}, "RUNTIME": {}, "BUILTIN": ("documentary",), "ASSETS": home / "assets"}
    namespace = _original_functions(ORIGINAL / "styles.py", {"_files", "is_builtin", "catalogue"}, namespace)
    namespace["STYLES"] = {style["name"]: json.loads(path.read_text())}
    assert style["name"] in [row["name"] for row in namespace["catalogue"]()]
    kit_namespace = {"Path": Path, "json": json, "KITS": {}, "_FONT_CACHE": {}}
    _original_functions(ORIGINAL / "kits.py", {"kit_of", "register_gen_kit"}, kit_namespace)
    manifest = Path(style["look"]["genkit"]["manifest"])
    metadata = json.loads(manifest.read_text())
    metadata.update(palette={"accent": "#ffffff"})
    manifest.write_text(json.dumps(metadata))
    assert kit_namespace["kit_of"](style["look"]) == "learned"


def test_native_budget_bounds_subprocesses_and_preserves_request_contract(monkeypatch):
    calls = []
    clock = [100.0]
    def original(command, **kwargs): calls.append((command, kwargs)); return "result"
    runtime = SimpleNamespace(real_run=original)
    monkeypatch.setattr(cr_claude, "_runtime", runtime)
    monkeypatch.setattr(clone.time, "monotonic", lambda: clock[0])
    with clone._creative_budget(1):
        assert runtime.real_run(["claude", "--model", "opus"], input="original prompt", timeout=900) == "result"
        assert calls[0][1] == {"input": "original prompt", "timeout": 60.0}
        clock[0] = 161.0
        with pytest.raises(subprocess.TimeoutExpired): runtime.real_run(["claude"], input="another request")
    assert runtime.real_run is original and len(calls) == 1


def test_actual_original_watch_cannot_fall_back_to_an_api_brain():
    namespace = {"Path": Path, "Clock": object, "_env": lambda name: "remote",
        "_sections": lambda duration: [(0, 180), (400, 445)],
        "watch_remote": lambda *args: pytest.fail("direct API watcher")}
    _original_functions(ORIGINAL / "reflearn.py", {"watch"}, namespace)
    def blocked(*args):
        raise clone.StudyStopped("No alternate API brain")
    original = namespace["watch"]
    before = original.__globals__["watch_remote"]
    bound = clone._bind(original, watch_remote=blocked)
    with pytest.raises(clone.StudyStopped):
        bound({"id": VIDEO, "duration": 900}, SimpleNamespace(log=lambda line: None), Path("unused"))
    assert original.__globals__["watch_remote"] is before


def test_symlink_cannot_move_private_study_outside_the_runtime(approved, tmp_path):
    target = tmp_path.parent / (tmp_path.name + "-foreign")
    target.mkdir()
    folder = approved.home / "output/cr-style-clone"
    import shutil
    shutil.rmtree(folder)
    folder.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="output folder"):
        clone.preview(approved.home, "https://youtube.com/watch?v=" + VIDEO, learner=approved.metadata)


def _failed_completed_fixture(approved):
    operation = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)["operation_id"]
    def completed_then_failed(folder, spec, progress):
        ready_fixture(folder, spec, progress)
        (folder / "ready-style.json").unlink()
        progress("validate")
        design = {"genome": {"analysis": {"genre": ["science"]}}}
        record = {"kit": "learned", "browser": True, "ref": "approved_reference",
                  "reference": {"id": VIDEO}, "types": ["a", "b", "c"]}
        clone._write(folder / "design-checkpoint.json", {"schema": 1, "design": design,
            "spec_sha256": clone._hash(spec)})
        clone._write(folder / "validated-checkpoint.json", {"schema": 1, "record": record,
            "spec_sha256": clone._hash(spec), "assets": clone._validated_assets(folder, record)})
        raise AttributeError("private-model-prompt SECRET_CREDENTIAL")
    with pytest.raises(AttributeError):
        clone.run(approved.home, operation, approved.bridge, readiness=lambda: approved.proof,
                  execute=completed_then_failed)
    source = clone._operation(approved.home, operation)
    original = (source / "operation.json").read_bytes()
    cloud = {"project": {"status": "failed"}, "stages": []}
    approved.bridge.project_progress = lambda p: cloud
    engine = SimpleNamespace(STYLE_INFO={"documentary": {"language": "English"}})
    def save(folder, style):
        path = folder / "auto_style.json"
        path.write_text(json.dumps(style))
        return path
    def genkit(style, folder, log, invalidate):
        style["look"] = {"graphics": "kit", "kit": "learned",
            "genkit": {"manifest": str(folder / "genkit/kit.json")}}
        return "learned"
    learner = SimpleNamespace(around=lambda design, *a, **k: copy.deepcopy(design["genome"]),
                              summary=lambda r: {"ref": r["ref"]})
    # Deliberately no design/_learn/Claude/download/browser methods exist.
    designer = SimpleNamespace(build=lambda *a, **k: {"name": "auto_look", "look": {}, "variant_of": "documentary"},
                               save=save, genkit=genkit)
    return SimpleNamespace(operation=operation, source=source, original=original, engine=engine,
                           learner=learner, designer=designer, cloud=cloud)


def test_failure_diagnostics_are_private_and_do_not_repeat_provider_text(approved):
    failure = _failed_completed_fixture(approved)
    diagnostic = failure.source / "failure.json"
    saved = clone._read(diagnostic)
    assert saved["exception_type"] == "AttributeError" and saved["frames"]
    assert saved["failure_code"] == "metadata_handoff_failed"
    assert "SECRET_CREDENTIAL" not in diagnostic.read_text()
    result = clone.status(approved.home, failure.operation, approved.bridge)
    assert result["metadata_recovery_available"] and "without repeating" in result["message"]
    if os.name != "nt":
        assert diagnostic.stat().st_mode & 0o777 == 0o600


def test_completed_study_recovery_owns_new_operation_preserves_source_and_never_reruns_creative_work(approved):
    failure = _failed_completed_fixture(approved)
    assert clone.metadata_recovery_available(approved.home, failure.operation, approved.bridge)
    result = clone.recover_metadata(approved.home, failure.operation, approved.bridge,
        readiness=lambda: approved.proof, engine=failure.engine, learner=failure.learner, designer=failure.designer)
    assert result["state"] == "done" and result["style_ready"] and result["metadata_only"]
    assert result["operation_id"] != failure.operation and result["recovery_of"] == failure.operation
    assert (failure.source / "operation.json").read_bytes() == failure.original
    assert sum(name == "begin" for name, _ in approved.events) == 2
    again = clone.recover_metadata(approved.home, failure.operation, approved.bridge,
        readiness=lambda: pytest.fail("completed metadata recovery reran readiness"))
    assert again["operation_id"] == result["operation_id"] and sum(name == "begin" for name, _ in approved.events) == 2
    recovered = clone._operation(approved.home, result["operation_id"])
    style = clone._read(recovered / "ready-style.json")
    assert style["look"]["genkit"]["manifest"].startswith(str(recovered))
    if os.name != "nt":
        assert (recovered / "auto_style.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("cloud_state,stage_state", [("running", "complete"), ("failed", "authorized"),
    ("failed", "failed"), ("failed", "retry_allowed")])
def test_metadata_recovery_refuses_active_or_uncertain_owned_stages_before_new_project(approved, cloud_state, stage_state):
    failure = _failed_completed_fixture(approved)
    failure.cloud.update(project={"status": cloud_state}, stages=[{"status": stage_state}])
    with pytest.raises(RuntimeError, match="Reconcile"):
        clone.recover_metadata(approved.home, failure.operation, approved.bridge,
            readiness=lambda: approved.proof, engine=failure.engine, learner=failure.learner, designer=failure.designer)
    assert sum(name == "begin" for name, _ in approved.events) == 1
    assert (failure.source / "operation.json").read_bytes() == failure.original


def test_metadata_recovery_lost_begin_reuses_new_owned_begin_id(approved):
    failure = _failed_completed_fixture(approved)
    original_begin = approved.bridge._mcp_tool
    attempts = []
    def lost(name, payload):
        attempts.append(payload["begin_id"])
        raise RuntimeError("lost metadata begin response")
    approved.bridge._mcp_tool = lost
    with pytest.raises(RuntimeError, match="lost metadata"):
        clone.recover_metadata(approved.home, failure.operation, approved.bridge,
            readiness=lambda: approved.proof, engine=failure.engine, learner=failure.learner, designer=failure.designer)
    def online(name, payload):
        attempts.append(payload["begin_id"])
        return original_begin(name, payload)
    approved.bridge._mcp_tool = online
    result = clone.recover_metadata(approved.home, failure.operation, approved.bridge,
        readiness=lambda: approved.proof, engine=failure.engine, learner=failure.learner, designer=failure.designer)
    assert result["style_ready"] and result["state"] == "done" and attempts[0] == attempts[1]
    assert (failure.source / "operation.json").read_bytes() == failure.original


def test_metadata_recovery_lost_finish_keeps_style_and_only_reconciles_receipt(approved):
    failure = _failed_completed_fixture(approved)
    finish = approved.bridge.finish_project
    approved.bridge.finish_project = lambda *a: (_ for _ in ()).throw(RuntimeError("offline receipt"))
    pending = clone.recover_metadata(approved.home, failure.operation, approved.bridge,
        readiness=lambda: approved.proof, engine=failure.engine, learner=failure.learner, designer=failure.designer)
    assert pending["state"] == "pending" and pending["style_ready"]
    approved.bridge.finish_project = finish
    complete = clone.recover_metadata(approved.home, failure.operation, approved.bridge,
        readiness=lambda: pytest.fail("metadata sync attempted setup or generation"))
    assert complete["state"] == "done" and complete["operation_id"] == pending["operation_id"]
    assert sum(name == "begin" for name, _ in approved.events) == 2


def test_metadata_recovery_refuses_changed_kit_or_foreign_owner(approved):
    failure = _failed_completed_fixture(approved)
    (failure.source / "genkit/kit.js").write_text("changed after validation")
    assert not clone.metadata_recovery_available(approved.home, failure.operation, approved.bridge)
    with pytest.raises(RuntimeError, match="original design evidence|completed Claude evidence"):
        clone.recover_metadata(approved.home, failure.operation, approved.bridge,
            readiness=lambda: approved.proof, engine=failure.engine, learner=failure.learner, designer=failure.designer)
    approved.cfg["key"] = "cr_mcp_other_account"
    with pytest.raises(RuntimeError, match="owns this"):
        clone.recover_metadata(approved.home, failure.operation, approved.bridge,
            readiness=lambda: pytest.fail("foreign setup"))
    assert sum(name == "begin" for name, _ in approved.events) == 1


def test_another_metadata_failure_is_safe_visible_and_cannot_repeat_blindly(approved):
    failure = _failed_completed_fixture(approved)
    failure.designer.build = lambda *a, **k: (_ for _ in ()).throw(ValueError("SECRET_CREDENTIAL private prompt"))
    with pytest.raises(RuntimeError, match="private diagnostics") as stopped:
        clone.recover_metadata(approved.home, failure.operation, approved.bridge,
            readiness=lambda: approved.proof, engine=failure.engine, learner=failure.learner, designer=failure.designer)
    assert "SECRET_CREDENTIAL" not in str(stopped.value)
    begins = sum(name == "begin" for name, _ in approved.events)
    result = clone.recover_metadata(approved.home, failure.operation, approved.bridge,
        readiness=lambda: pytest.fail("known failed metadata action retried blindly"))
    assert result["state"] == "failed" and result["needs_manual_recovery"]
    assert sum(name == "begin" for name, _ in approved.events) == begins
    assert (failure.source / "operation.json").read_bytes() == failure.original


def test_actual_original_metadata_api_ownership_and_signatures():
    import inspect
    namespace = {"Path": Path}
    _original_functions(ORIGINAL / "autostyle.py", {"design", "build", "save", "genkit"}, namespace)
    _original_functions(ORIGINAL / "reflearn.py", {"around", "summary"}, namespace)
    inspect.signature(namespace["design"]).bind(object(), "Title", "English", 10, channel="documentary", log=lambda x: None)
    inspect.signature(namespace["around"]).bind({}, {}, channel="documentary", minutes=10, title="Title", log=None)
    inspect.signature(namespace["build"]).bind({}, {}, "documentary", job=Path("fixture"), title="Title", log=None)
    inspect.signature(namespace["save"]).bind(Path("fixture"), {})
    inspect.signature(namespace["genkit"]).bind({}, Path("fixture"), None, invalidate=True)
    # The original owning module must be checked, not a convenient fake API.
    ast_names = {node.name for node in ast.parse((ORIGINAL / "autostyle.py").read_text()).body
                 if isinstance(node, ast.FunctionDef)}
    assert "around" not in ast_names


def test_new_study_identity_preserves_failed_attempt_and_same_click_is_idempotent(approved):
    spec = {**approved.spec, "attempt_id": "a" * 32}
    one = clone.prepare(approved.home, spec, approved.proof, approved.bridge)
    saved_path = approved.home / "output/cr-style-clone" / one["operation_id"] / "operation.json"
    saved = json.loads(saved_path.read_text()); saved["state"] = "failed"
    clone._write(saved_path, saved); before = saved_path.read_bytes()
    assert clone.prepare(approved.home, spec, approved.proof, approved.bridge)["operation_id"] == one["operation_id"]
    two = clone.prepare(approved.home, {**spec, "attempt_id": "b" * 32}, approved.proof, approved.bridge)
    assert two["operation_id"] != one["operation_id"] and two["state"] == "prepared"
    assert two["reference_ids"] == one["reference_ids"] and saved_path.read_bytes() == before
    assert not any(name == "begin" for name, _ in approved.events)


def test_timeout_is_shown_without_setup_redirect_or_provider_text(approved):
    one = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)
    folder = approved.home / "output/cr-style-clone" / one["operation_id"]
    saved = clone._read(folder / "operation.json")
    saved.update(state="failed", phase="build", project_id=PROJECT)
    clone._write(folder / "operation.json", saved)
    cache = approved.home / "output/cr-claude-cache" / PROJECT / ("cc_" + "1" * 64 + ".json")
    clone._write(cache, {"state": "failed", "error_code": "timeout", "diagnostic": {"stderr": "SECRET private prompt"}})
    result = clone.status(approved.home, one["operation_id"], approved.bridge)
    assert result["failure_code"] == "timeout" and result["setup_review_required"] is False
    assert "timed out" in result["message"] and "approve a new study" in result["message"]
    assert "SECRET" not in json.dumps(result)
    assert result["retry_spec"]["reference_url"] == approved.preview["reference_url"]
    assert approved.events == [("prepare", approved.proof)]


def test_native_kit_can_use_remaining_budget_beyond_inherited_900_second_cap(monkeypatch):
    clock = [100.0]; calls = []
    def original(command, **kwargs): calls.append((command, kwargs)); return "result"
    runtime = SimpleNamespace(real_run=original)
    monkeypatch.setattr(cr_claude, "_runtime", runtime)
    monkeypatch.setattr(clone.time, "monotonic", lambda: clock[0])
    with clone._creative_budget(22):
        clock[0] = 265.0
        runtime.real_run(["claude", "-p"], input="unchanged original kit prompt", timeout=900)
        runtime.real_run(["claude", "auth", "status"], timeout=20)
    assert calls[0][1] == {"input": "unchanged original kit prompt", "timeout": 1155.0}
    assert calls[1][1] == {"timeout": 20}
    assert runtime.real_run is original


def test_new_studies_default_to_reference_only_and_reject_unknown_visual_modes(approved):
    spec = dict(approved.spec); spec.pop("visual_mode")
    assert clone._spec(approved.home, spec)["visual_mode"] == "reference_only"
    with pytest.raises(ValueError, match="reference animation"):
        clone._spec(approved.home, {**spec, "visual_mode": "invalid"})
    with pytest.raises(ValueError, match="new style study"):
        clone._spec(approved.home, {**spec, "attempt_id": "invalid"})


def test_failed_study_ui_offers_explicit_new_attempt_and_preserves_duplicate_click_identity():
    html = (Path(__file__).parents[1] / "local_runtime/cr_style_clone.html").read_text()
    assert 'id="retry-study"' in html and 'id="visual-mode"' in html
    assert "payload.attempt_id=studyAttempt(payload)" in html
    assert "localStorage.setItem('cr.styleStudyRequest'" in html
    assert "$('usage-approved').checked=false" in html
    assert "data.setup_review_required||data.can_continue_prepared" in html
