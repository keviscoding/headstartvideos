"""Saved-style refinement remains explicit, native, isolated and original."""
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tests.test_frontier_style_clone import approved, ready_fixture, _original_functions, ORIGINAL, VIDEO
import cr_claude
import cr_style_clone as clone
import cr_style_refine as refine


@pytest.fixture
def published(approved):
    operation = clone.prepare(approved.home, approved.spec, approved.proof, approved.bridge)["operation_id"]
    def complete(folder, spec, progress):
        ready_fixture(folder, spec, progress)
        (folder / "genkit/kit.js").write_text("let colour = 'old';")
        work = folder / "reflearn"
        (work / "ref_frames").mkdir(parents=True)
        (work / "board.jpg").write_bytes(b"source board fixture")
        (work / "ref_sheet1.jpg").write_bytes(b"actual downloaded sheet fixture")
        (work / "ref_frames/k00.jpg").write_bytes(b"actual downloaded frame fixture")
        clone._write(work / "spec.json", {"style_name": "Learned look", "grammar": "reference grammar"})
        clone._write(work / "reference-evidence.json", {"actual_frames": True, "id": VIDEO, "captions_available": True})
        design = {"genome": {"analysis": {"genre": ["science"]}}}
        record = {"kit": "learned", "browser": True, "ref": "reference_library",
            "reference": {"id": VIDEO}, "types": ["a", "b", "c"]}
        clone._write(folder / "design-checkpoint.json", {"schema": 1, "design": design, "spec_sha256": clone._hash(spec)})
        clone._write(folder / "validated-checkpoint.json", {"schema": 1, "record": record,
            "spec_sha256": clone._hash(spec), "assets": clone._validated_assets(folder, record)})
    result = clone.run(approved.home, operation, approved.bridge, readiness=lambda: approved.proof, execute=complete)
    source = clone._operation(approved.home, operation)
    snapshots = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    cloud = {"project": {"status": "done"}, "stages": [{"status": "complete"}]}
    approved.bridge.project_progress = lambda project: cloud
    original_begin = approved.bridge._mcp_tool
    def child_begin(name, payload):
        original_begin(name, payload)
        return {"project": {"id": "crv_" + "2" * 24}}
    approved.bridge._mcp_tool = child_begin
    approved.bridge.current_project = lambda: approved.bridge._project
    return SimpleNamespace(approved=approved, operation=operation, source=source, snapshots=snapshots,
                           result=result, cloud=cloud)


def prepare(published, **kwargs):
    a = published.approved
    return refine.prepare(a.home, published.operation, a.proof, a.bridge, **kwargs)


def test_preparation_copies_saved_evidence_and_owns_a_unique_kit_identity_without_creative_calls(published):
    a = published.approved
    result = prepare(published)
    child = clone._operation(a.home, result["operation_id"])
    saved = clone._read(child / "operation.json")
    assert result["state"] == "prepared" and saved["refinement_only"]
    assert saved["refinement_of"] == published.operation and saved["refinement_version"] == 1
    assert saved["spec"]["label"] == a.spec["label"] and saved["spec"]["max_calls"] == 8
    assert saved["spec"]["style_id"] != published.result["style"]
    assert (child / "genkit/kit.js").read_bytes() == published.snapshots["genkit/kit.js"]
    assert clone._read(child / "genkit/kit.json")["name"] != clone._read(published.source / "genkit/kit.json")["name"]
    assert (child / "reflearn/ref_sheet1.jpg").read_bytes() == published.snapshots["reflearn/ref_sheet1.jpg"]
    assert (child / "reflearn/ref_frames/k00.jpg").read_bytes() == published.snapshots["reflearn/ref_frames/k00.jpg"]
    assert clone._metadata_checkpoint_ready(child, saved)
    assert not refine.validation_completed(child, saved)
    assert sum(name == "begin" for name, _ in a.events) == 1
    assert all((published.source / name).read_bytes() == data for name, data in published.snapshots.items())
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in child.rglob("*") if path.is_file())


def test_an_improved_copy_keeps_the_art_directors_words_and_the_paid_likeness_check(published):
    a = published.approved
    clone._write(published.source / "art-checkpoint.json", {"schema": 1, "art": {"overrides": {"label": "x"}}})
    clone._write(published.source / "likeness.json", {"state": "done", "verdict": "match"})
    (published.source / "likeness").mkdir()
    (published.source / "likeness/r0_1.jpg").write_bytes(b"best test picture")
    child = clone._operation(a.home, prepare(published)["operation_id"])
    assert clone._read(child / "likeness.json")["verdict"] == "match"
    assert (child / "likeness/r0_1.jpg").read_bytes() == b"best test picture"
    assert clone._read(child / "art-checkpoint.json")["art"]["overrides"] == {"label": "x"}
    assert "likeness/r0_1.jpg" in clone._read(child / "refinement-source.json")["files"]


def test_lost_prepare_response_returns_same_child_without_repeating_preparation(published):
    a = published.approved
    first = prepare(published)
    events = list(a.events)
    second = prepare(published)
    assert first["operation_id"] == second["operation_id"] and a.events == events


@pytest.mark.parametrize("status,stage", [("running", "complete"), ("done", "authorized"),
    ("done", "failed"), ("done", "retry_allowed")])
def test_active_or_uncertain_source_project_cannot_prepare_paid_refinement(published, status, stage):
    published.cloud.update(project={"status": status}, stages=[{"status": stage}])
    with pytest.raises(RuntimeError, match="Reconcile"):
        prepare(published)
    assert not (clone._folder(published.approved.home) / ".refinements").exists()


def test_cost_consent_foreign_owner_changed_model_or_missing_original_reference_stops_preparation(published):
    a = published.approved
    with pytest.raises(ValueError, match="Approve"):
        prepare(published, acknowledge_costs=False)
    a.cfg["key"] = "cr_mcp_other_account"
    with pytest.raises(RuntimeError, match="owns this"):
        prepare(published)
    a.cfg["key"] = "cr_mcp_synthetic_account"
    a.cfg["visual_model"] = cr_claude.GENERAL_MODEL
    with pytest.raises(RuntimeError, match="pinned"):
        refine.prepare(a.home, published.operation, {**a.proof, "visual_model": cr_claude.GENERAL_MODEL}, a.bridge)
    a.cfg["visual_model"] = cr_claude.VISUAL_MODEL
    (published.source / "reflearn/ref_sheet1.jpg").unlink()
    with pytest.raises(RuntimeError, match="frames and sheets are missing"):
        prepare(published)


def test_owned_metadata_recovery_ancestor_supplies_existing_reference_files_without_download(published):
    a = published.approved
    # A metadata publication operation retains its board and evidence; the
    # original study owns the actual downloaded sheets and frames.
    ancestor_id = "op_" + "a" * 24
    ancestor = clone._operation(a.home, ancestor_id)
    ancestor.mkdir(parents=True)
    import shutil
    shutil.copytree(published.source / "reflearn", ancestor / "reflearn")
    saved = clone._read(published.source / "operation.json")
    clone._write(ancestor / "operation.json", {**saved, "operation_id": ancestor_id, "state": "failed"})
    saved["recovery_of"] = ancestor_id
    clone._write(published.source / "operation.json", saved)
    (published.source / "reflearn/ref_sheet1.jpg").unlink()
    shutil.rmtree(published.source / "reflearn/ref_frames")
    (published.source / "reflearn/spec.json").unlink()
    result = prepare(published)
    child = clone._operation(a.home, result["operation_id"])
    assert (child / "reflearn/ref_frames/k00.jpg").is_file()
    assert clone._read(child / "reflearn/spec.json")["grammar"] == "reference grammar"


def _original_loop(events, *, browser=True, kept=None, rescore=8):
    def check(kit, media, out, log):
        code = (kit / "kit.js").read_text()
        events.append(("check", code))
        out.mkdir(parents=True, exist_ok=True)
        frame = out / "a.jpg"
        frame.write_bytes(b"real browser fixture frame")
        return {"name": "fixture", "browser": browser, "why": "", "n_types": 3, "bad": [],
            "problems": ["computed text colour is black"] if "'old'" in code else [],
            "types": {"a": {"frames": [str(frame)]}}}
    def ask(engine, clock, step, prompt, **kwargs):
        events.append(("repair", engine.SCRIPT_PROVIDER))
        return json.dumps({"edits": [{"find": "'old'", "replace": "'repair'"}]})
    def see(engine, clock, step, prompt, images, **kwargs):
        events.append(("vision", len(images)))
        return json.dumps({"score": 5, "verdict": "needs brighter type",
            "edits": [{"find": "'repair'", "replace": "'vision'"}]}) if kwargs["max_tokens"] != 2000 else json.dumps({"score": rescore, "verdict": "rescored"})
    def sheet(check, out, label):
        out.write_bytes(b"actual review sheet fixture")
        return out
    namespace = {"Path": Path, "json": json, "re": re, "check": check, "_ask": ask, "_see": see,
        "_write": lambda path, text: path.write_text(text), "_read_json": clone._read,
        "_register": lambda *a: None, "_ours_sheet": sheet,
        "REPAIR_PROMPT": "[PROBLEMS] [EXAMPLES] [JS]", "CRITIQUE_PROMPT": "[REF] [OURS] [JS]",
        "RESCORE_PROMPT": "[REF] [OURS]"}
    _original_functions(ORIGINAL / "reflearn.py", {"validate", "apply_edits", "_examples", "_rescore", "_json_obj", "_json_with", "summary"}, namespace)
    class Clock:
        def __init__(self, budget, log): self.log = log; events.append(("clock", budget))
        def left(self): return 1000
        def step(self, phase): events.append(("step", phase))
        def end(self): pass
        def report(self): return {"total_min": 1, "total_usd": 0, "total_usd_api": 0, "minutes": {"validate": 1}}
    def media(engine, analysis, folder, work, fetch):
        events.append(("media", fetch)); assert fetch is False
        return {}
    def board(path, title, spec, reference, checked, frames, report):
        events.append(("board", checked.get("critique"), len(frames)))
        path.write_bytes(b"fresh original board fixture")
        return path
    namespace["VERSION"] = "original-loop-test"
    learner = SimpleNamespace(**namespace, Clock=Clock, WORK="reflearn", _media=media, board=board,
        around=lambda design, *a, **k: design["genome"])
    if kept is not None:
        learner.validate = lambda *a, **k: {"browser": browser, "kept": kept, "bad": []}
    return learner


def _designer():
    def save(folder, style):
        path = folder / "auto_style.json"
        path.write_text(json.dumps(style)); return path
    def genkit(style, folder, log, invalidate):
        manifest = clone._read(folder / "genkit/kit.json")
        style["look"] = {"graphics": "kit", "kit": manifest["name"],
            "genkit": {"manifest": str(folder / "genkit/kit.json")}}
        return manifest["name"]
    return SimpleNamespace(build=lambda *a, **k: {"name": "auto-look", "variant_of": "documentary", "look": {}},
                           save=save, genkit=genkit)


def test_actual_original_loop_checks_repairs_critiques_rescores_and_publishes_a_separate_style(published, monkeypatch):
    a = published.approved
    result = prepare(published)
    folder = clone._operation(a.home, result["operation_id"])
    saved = clone._read(folder / "operation.json")
    events = []
    learner = _original_loop(events)
    engine = SimpleNamespace(SCRIPT_PROVIDER="claudecode", CLAUDE_PROVIDER="claudecode", SCRIPT_MODEL="native",
        STYLE_INFO={"documentary": {}}, set_extra=lambda value: events.append(("brief", value)))
    monkeypatch.setattr(cr_claude, "_runtime", SimpleNamespace(bridge=a.bridge, real_run=lambda *a, **k: pytest.fail("unit test invoked native subprocess")))
    def execute(folder, spec, progress):
        current = clone._read(folder / "operation.json")
        return refine.run_original(engine, folder, current, progress, learner=learner, designer=_designer())
    done = clone.run(a.home, result["operation_id"], a.bridge, readiness=lambda: a.proof, execute=execute)
    assert done["state"] == "done" and done["style_ready"]
    assert [kind for kind, *_ in events if kind in {"check", "repair", "vision", "board"}] == [
        "check", "repair", "check", "vision", "check", "vision", "board"]
    assert "'vision'" in (folder / "genkit/kit.js").read_text()
    style = clone._read(folder / "ready-style.json")
    assert style["cr_style_clone"]["refinement_version"] == 1 and style["label"] == a.spec["label"]
    assert clone._read(folder / "validated-checkpoint.json")["record"]["report"]["total_min"] == 1
    assert clone._read(folder / "refinement-validation.json")["critique"]["after"]["score"] == 8
    assert refine.validation_completed(folder, clone._read(folder / "operation.json"))
    assert all((published.source / name).read_bytes() == data for name, data in published.snapshots.items())
    # The failed/running/finished child is returned on a repeat preparation;
    # approval of the same source cannot create another paid operation.
    assert prepare(published)["operation_id"] == result["operation_id"]


def test_original_rescore_reverts_a_worse_visual_patch(published, monkeypatch):
    a = published.approved
    result = prepare(published)
    folder = clone._operation(a.home, result["operation_id"])
    saved = clone._read(folder / "operation.json")
    saved.update(state="running", project_id="crv_" + "2" * 24)
    a.bridge._project = {"id": saved["project_id"]}
    monkeypatch.setattr(cr_claude, "_runtime", SimpleNamespace(bridge=a.bridge, real_run=lambda *a, **k: None))
    engine = SimpleNamespace(SCRIPT_PROVIDER="claudecode", CLAUDE_PROVIDER="claudecode", SCRIPT_MODEL="native",
        STYLE_INFO={"documentary": {}}, set_extra=lambda value: None)
    refine.run_original(engine, folder, saved, lambda phase: None,
        learner=_original_loop([], rescore=3), designer=_designer())
    assert "'repair'" in (folder / "genkit/kit.js").read_text() and "'vision'" not in (folder / "genkit/kit.js").read_text()
    assert clone._read(folder / "refinement-validation.json")["critique"]["reverted"]


@pytest.mark.parametrize("browser,kept", [(False, ["a", "b", "c"]), (True, ["a", "b"])])
def test_mock_only_validation_or_too_few_original_working_types_cannot_publish(published, monkeypatch, browser, kept):
    a = published.approved
    result = prepare(published)
    folder = clone._operation(a.home, result["operation_id"])
    saved = clone._read(folder / "operation.json")
    saved.update(state="running", project_id="crv_" + "2" * 24)
    a.bridge._project = {"id": saved["project_id"]}
    monkeypatch.setattr(cr_claude, "_runtime", SimpleNamespace(bridge=a.bridge, real_run=lambda *a, **k: None))
    engine = SimpleNamespace(SCRIPT_PROVIDER="claudecode", CLAUDE_PROVIDER="claudecode",
        STYLE_INFO={"documentary": {}}, set_extra=lambda value: None)
    with pytest.raises(clone.StudyStopped, match="original browser validation"):
        refine.run_original(engine, folder, saved, lambda phase: None,
            learner=_original_loop([], browser=browser, kept=kept), designer=_designer())
    assert not (folder / "ready-style.json").exists()
    assert not refine.validation_completed(folder, saved)


def test_missing_native_owned_project_or_altered_reference_stops_before_any_creative_call(published, monkeypatch):
    a = published.approved
    result = prepare(published)
    folder = clone._operation(a.home, result["operation_id"])
    saved = clone._read(folder / "operation.json")
    saved.update(state="running", project_id="crv_" + "2" * 24)
    a.bridge._project = {"id": "wrong-project"}
    monkeypatch.setattr(cr_claude, "_runtime", SimpleNamespace(bridge=a.bridge, real_run=lambda *a, **k: None))
    engine = SimpleNamespace(SCRIPT_PROVIDER="claudecode", CLAUDE_PROVIDER="claudecode")
    with pytest.raises(RuntimeError, match="owned refinement"):
        refine.run_original(engine, folder, saved, lambda phase: None)
    a.bridge._project = {"id": saved["project_id"]}
    (folder / "reflearn/ref_sheet1.jpg").write_bytes(b"changed after approval")
    with pytest.raises(RuntimeError, match="reference files changed"):
        refine.run_original(engine, folder, saved, lambda phase: None,
            learner=SimpleNamespace(), designer=SimpleNamespace())
