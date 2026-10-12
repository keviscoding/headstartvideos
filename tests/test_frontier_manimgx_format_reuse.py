"""Production-format changes reuse proven accepted creative code, without a model."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_frontier_manimgx import SOURCE, VIDEO, manifest, stub_runtime, run_stub, saved_source_correction
import cr_manimgx as gx
import cr_manimgx_profile as profile


FINAL = ["--w", "1920", "--h", "1080", "--quality", "final"]


def compiler_count(calls, source=SOURCE):
    def compile(prompt, maximum):
        calls.append(prompt)
        context = json.loads(prompt.split("ORIGINAL INPUT:\n", 1)[1].split("\nLOCAL ASSETS:", 1)[0])
        return json.dumps(manifest(context, source))
    return compile


def final_video(injected=True):
    video = copy.deepcopy(VIDEO)
    if injected:
        before = copy.deepcopy(video["scenes"][0])
        video["scenes"][0]["render"] = {"sub": 6}
        proof = profile.injected_sub_proof(before, video["scenes"][0], 6)
        gx._OUTPUT_INJECTIONS[gx._digest(video)] = {"cube": proof}
    return video


def test_preview_to_final_reuses_one_accepted_source_with_separate_lineage(stub_runtime, monkeypatch):
    state, calls = stub_runtime, []
    monkeypatch.setattr(gx, "_validate_output", lambda *a: None)
    first = run_stub(state, compiler=compiler_count(calls))
    old = state.root / "job/threed/.cr-manimgx" / first["scenes"][0]["compiler_key"]
    saved = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in old.iterdir() if p.is_file()}
    checks = []
    monkeypatch.setattr(gx, "_typecheck", lambda py, source: checks.append(Path(source)))
    second = run_stub(state, video=final_video(), args=FINAL, compiler=compiler_count(calls), output="final")
    target = old.parent / second["scenes"][0]["compiler_key"]
    receipt = gx._read(target / "reuse.json")
    assert len(calls) == 1 and checks == [old / "scene.py"]
    assert receipt["source_key"] == old.name and receipt["target_key"] == target.name
    assert receipt["model_calls"] == 0 and receipt["changes_original_coverage"] is False
    assert receipt["assessment"]["target_injection"]["value"] == 6
    assert not (target / "compiler.json").exists() and not (target / "scene.py").exists()
    assert not (target / "compile-attempts.json").exists()
    assert all(p.read_bytes() == saved[p.name][0] and p.stat().st_mtime_ns == saved[p.name][1]
               for p in old.iterdir() if p.name in saved)
    assert state.invocations[-1]["width"] == 1920
    assert state.invocations[-1]["native_profile"]["msaa_samples"] == 4
    assert state.invocations[-1]["preset"] == "slow"
    run_stub(state, video=final_video(), args=FINAL, compiler=compiler_count(calls), output="final-again")
    assert len(calls) == 1 and len(checks) == 2


def test_legacy_source_reconstructs_only_exact_actual_report_video_input(stub_runtime, monkeypatch):
    state, calls = stub_runtime, []
    monkeypatch.setattr(gx, "_validate_output", lambda *a: None)
    first = run_stub(state, compiler=compiler_count(calls))
    old = state.root / "job/threed/.cr-manimgx" / first["scenes"][0]["compiler_key"]
    saved = gx._read(old / "compiler.json"); saved.pop("context"); saved.pop("output_injection")
    gx._write(old / "compiler.json", saved)
    run_stub(state, video=final_video(), args=FINAL, compiler=compiler_count(calls), output="final")
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["camera", "aspect", "explicit_sub", "format_dependency", "bad_report"])
def test_non_equivalent_or_output_dependent_source_is_never_borrowed(stub_runtime, monkeypatch, change):
    state, calls = stub_runtime, []
    monkeypatch.setattr(gx, "_validate_output", lambda *a: None)
    source = SOURCE.replace("cube =", "width = m.config.pixel_width\n        cube =") if change == "format_dependency" else SOURCE
    first = run_stub(state, compiler=compiler_count(calls, source))
    video, args = final_video(), FINAL
    if change == "camera": video["scenes"][0]["camera"]["theta"] = 30
    elif change == "aspect": args = ["--w", "1920", "--h", "1200", "--quality", "final"]
    elif change == "explicit_sub": gx._OUTPUT_INJECTIONS[gx._digest(video)] = {}
    elif change == "bad_report":
        old = state.root / "job/threed/.cr-manimgx" / first["scenes"][0]["compiler_key"]
        saved = gx._read(old / "compiler.json"); saved.pop("context"); saved.pop("output_injection")
        gx._write(old / "compiler.json", saved)
        report = state.root / "job/threed/review/report.json"
        record = gx._read(report); record["w"] = 640; gx._write(report, record)
    run_stub(state, video=video, args=args, compiler=compiler_count(calls), output="final")
    assert len(calls) == 2


@pytest.mark.parametrize("changed", ["source", "accepted_response", "target_stopped", "target_malformed", "target_unknown_manifest"])
def test_changed_acceptance_or_stopped_target_cannot_trigger_compile_or_reuse(stub_runtime, monkeypatch, changed):
    state, calls = stub_runtime, []
    monkeypatch.setattr(gx, "_validate_output", lambda *a: None)
    first = run_stub(state, compiler=compiler_count(calls))
    cache = state.root / "job/threed/.cr-manimgx"
    old = cache / first["scenes"][0]["compiler_key"]
    video = final_video()
    context = {"scene": video["scenes"][0], "cast": [], "video": {},
               "output": {"width": 1920, "height": 1080, "fps": 30, "quality": "final"}}
    target = cache / gx._cache_key(context, {}, state.ready, None)
    if changed == "source": (old / "scene.py").write_text(SOURCE + "# edited\n")
    elif changed == "accepted_response": (old / "compiler-response-1.txt").write_text("{}")
    elif changed == "target_stopped": gx._write(target / "compile-attempts.json", {"state": "stopped", "calls": 1})
    elif changed == "target_malformed":
        target.mkdir(); (target / "compile-attempts.json").write_text("broken")
    else:
        target.mkdir(); (target / "compiler.json").write_text("broken")
    with pytest.raises(RuntimeError):
        run_stub(state, video=video, args=FINAL, compiler=compiler_count(calls), output="final")
    assert len(calls) == 1
    assert not (target / "reuse.json").exists()


def test_output_provenance_keeps_original_values_hashes_and_explicit_sub(monkeypatch):
    def original(board, sub=None, preset=None):
        value = copy.deepcopy(board)
        if sub:
            for scene in value["scenes"]: scene.setdefault("render", {})["sub"] = int(sub)
        return value
    module = SimpleNamespace(_video=original)
    monkeypatch.setattr(gx, "_selected_environment", lambda *a: {"ready": True})
    gx._install_output_provenance(module, SimpleNamespace(), Path("/fixture"))
    before = original(VIDEO, 6)
    actual = module._video(VIDEO, 6)
    assert actual == before and gx._digest(actual) == gx._digest(before)
    assert gx._OUTPUT_INJECTIONS[gx._digest(actual)]["cube"]["value"] == 6
    explicit = copy.deepcopy(VIDEO); explicit["scenes"][0]["render"] = {"sub": 2}
    assert module._video(explicit, 6) == original(explicit, 6)
    assert gx._OUTPUT_INJECTIONS[gx._digest(actual)] == {}
    assert VIDEO["scenes"][0].get("render") is None


def coverage_correction_fixture(state):
    root = state.directory
    work = root / ".recovery-coverage-text"
    work.mkdir()
    for name in ("recovery.json", "recovery-result.json", "scene-recovered.py"):
        (root / name).rename(work / name)
    receipt = gx._read(work / "recovery.json")
    receipt["proof"].update(native_stage_id="cc_" + "a" * 64, native_result_fingerprint="b" * 64)
    gx._write(work / "recovery.json", receipt)
    gx._write(root / "compile-attempts.json", {"key": state.key, "state": "stopped", "calls": 1})
    gx._write(root / "compiler-response-1.txt", "Original completed response retained unchanged", text=True)
    return work, receipt


def test_coverage_correction_writes_only_child_and_has_accepted_reuse_lineage(saved_source_correction):
    state, calls = saved_source_correction, []
    work, receipt = coverage_correction_fixture(state)
    old = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in state.directory.iterdir() if p.is_file()}
    corrected_source = SOURCE + "\n# One pinned API correction\n"
    def compiler(prompt, maximum):
        calls.append(prompt)
        return json.dumps(manifest(state.context, corrected_source))
    result, source = gx.correct_recovered_source(SimpleNamespace(), state.context, state.assets,
        state.ready, state.directory, state.key, reference_bundle=state.bundle, compiler=compiler,
        recovery_directory=work)
    assert len(calls) == 1 and source == work / "scene-corrected.py"
    assert gx._read(work / "compiler-corrected.json")["typed"] is True
    assert gx._read(work / "correction-attempts.json")["state"] == "validated"
    assert not (state.directory / "compiler.json").exists() and not (state.directory / "scene.py").exists()
    assert all((state.directory / name).read_bytes() == contents
               and (state.directory / name).stat().st_mtime_ns == modified
               for name, (contents, modified) in old.items())
    source_hash = gx._file_hash(source)
    receipt.update(state="corrected", model_calls=1, corrected_source_sha256=source_hash)
    gx._write(work / "recovery.json", receipt)
    root_manifest = {"key": state.key, "typed": True, "result": result, "source_sha256": source_hash,
        "recovery_of": state.key, "recovery_kind": "coverage_text",
        "recovery_receipt": ".recovery-coverage-text/recovery.json", "recovery_proof": receipt["proof"]}
    acceptance = gx._accepted_response(state.directory, root_manifest, state.context, state.assets)
    assert acceptance["kind"] == "completed_native_recovery_correction"
    journal = gx._read(work / "correction-attempts.json"); journal["calls"] = 2
    gx._write(work / "correction-attempts.json", journal)
    with pytest.raises(RuntimeError, match="correction changed"):
        gx._accepted_response(state.directory, root_manifest, state.context, state.assets)
    with pytest.raises(RuntimeError, match="no longer matches"):
        gx.correct_recovered_source(SimpleNamespace(), state.context, state.assets,
            state.ready, state.directory, state.key, reference_bundle=state.bundle, compiler=compiler,
            recovery_directory=work)
    assert len(calls) == 1


def test_coverage_correction_rejects_outside_or_failed_child_without_repeat(saved_source_correction):
    state, calls = saved_source_correction, []
    work, receipt = coverage_correction_fixture(state)
    outside = work.parent.parent / ".recovery-coverage-text"; outside.mkdir()
    with pytest.raises(RuntimeError, match="exact private"):
        gx.correct_recovered_source(SimpleNamespace(), state.context, state.assets,
            state.ready, state.directory, state.key, reference_bundle=state.bundle,
            compiler=lambda *a: pytest.fail("No model call"), recovery_directory=outside)
    def compiler(prompt, maximum):
        calls.append(prompt)
        return json.dumps(manifest(state.context, unsupported=["Required geometry cannot be represented"]))
    with pytest.raises(RuntimeError, match="cannot preserve"):
        gx.correct_recovered_source(SimpleNamespace(), state.context, state.assets,
            state.ready, state.directory, state.key, reference_bundle=state.bundle,
            compiler=compiler, recovery_directory=work)
    assert gx._read(work / "correction-attempts.json")["state"] == "stopped"
    assert (work / "compiler-correction-response-1.txt").is_file()
    assert not (state.directory / "compiler.json").exists()
    with pytest.raises(RuntimeError, match="already started or stopped"):
        gx.correct_recovered_source(SimpleNamespace(), state.context, state.assets,
            state.ready, state.directory, state.key, reference_bundle=state.bundle,
            compiler=compiler, recovery_directory=work)
    assert len(calls) == 1
