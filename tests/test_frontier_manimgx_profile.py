"""Native Fast quality, exact runner provenance and model-free benchmark scope."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_manimgx as renderer
import cr_manimgx_profile as profile

SOURCE = "import manimgx as m\nclass ExactScene(m.Scene):\n    def construct(self):\n        self.wait(1)\n"
CONTEXT = {"scene": {"id": "exact", "dur": 1, "camera": {"fov": 40}}, "cast": [], "video": {},
           "output": {"width": 640, "height": 360, "fps": 30, "quality": "draft"}}


def final_context():
    value = copy.deepcopy(CONTEXT)
    value["output"] = {"width": 1920, "height": 1080, "fps": 30, "quality": "final"}
    return value


def test_native_profile_uses_documented_native_defaults_without_temporal_claims():
    quality = profile.native_profile(final_context()["output"])
    assert quality["label"] == "Fast mode" and quality["whole_3d_video"] is True
    assert quality["renderer_version"] == "0.1.1" and quality["msaa_samples"] == 4
    assert quality["simulation_rate"] == 60 and quality["fps"] == 30
    assert "not temporal" in quality["optical_limits"][0]
    config = SimpleNamespace()
    profile.apply_native_config(config, quality)
    assert vars(config) == {"pixel_width": 1920, "pixel_height": 1080, "frame_rate": 30, "simulation_rate": 60}
    assert profile.render_kwargs(quality) == {"preset": "slow", "crf": 16}
    assert profile.render_kwargs(profile.native_profile(CONTEXT["output"])) == {"preset": "veryfast", "crf": 16}
    quality["msaa_samples"] = 6
    with pytest.raises(ValueError): profile.apply_native_config(config, quality)


@pytest.mark.parametrize("change", [{"fps": True}, {"fps": 0}, {"width": 641}, {"height": 0}, {"quality": "ultra"}])
def test_unknown_or_invalid_profile_is_not_silently_mapped(change):
    with pytest.raises(ValueError): profile.native_profile({**CONTEXT["output"], **change})


@pytest.mark.parametrize("render", [None, {}, {"shutter": .25, "dof": False}])
def test_only_new_original_video_sub_injection_leaves_creative_identity(render):
    original = copy.deepcopy(CONTEXT)
    if render is not None: original["scene"]["render"] = render
    target = final_context(); target["scene"] = copy.deepcopy(original["scene"])
    target["scene"].setdefault("render", {})["sub"] = 6
    before = copy.deepcopy((original, target))
    evidence = profile.injected_sub_proof(original["scene"], target["scene"], 6)
    assert evidence["before_scene_sha256"] != evidence["after_scene_sha256"]
    assert evidence["render_container_was_present"] == (render is not None)
    assert not profile.assess_reuse(original, target, SOURCE)["eligible"]
    assessed = profile.assess_reuse(original, target, SOURCE, target_injection=evidence)
    assert assessed["eligible"] and assessed["requires_accepted_native_receipt"]
    assert assessed["requires_fresh_ast_and_api_check"] and not assessed["changes_historical_records"]
    canonical = profile.compilation_identity(target, injection=evidence)["creative_context"]
    assert canonical["scene"] == original["scene"]
    assert (original, target) == before


def test_exact_scene_preset_and_all_optical_requirements_stay_creative():
    original = copy.deepcopy(CONTEXT)
    original["scene"].update({"render": {"sub": 6, "shutter": .4, "dof": 2},
                              "camera": {"fov": 40, "focus": [{"t": 0, "dist": 5, "a": .2}]},
                              "look": {"preset": "noir"}})
    target = copy.deepcopy(original); target["output"] = final_context()["output"]
    assert profile.injected_sub_proof(original["scene"], target["scene"], 6) is None
    assert profile.compilation_identity(original)["creative_context"]["scene"] == original["scene"]
    target["scene"]["render"]["sub"] = 2
    assert not profile.assess_reuse(original, target, SOURCE)["eligible"]
    target["scene"] = copy.deepcopy(original["scene"]); target["scene"]["camera"]["focus"][0]["a"] = .1
    assert not profile.assess_reuse(original, target, SOURCE)["eligible"]


@pytest.mark.parametrize("mutation", ["different_scene", "different_proof", "changed_camera", "changed_sub"])
def test_injection_cannot_erase_unproved_scene_changes(mutation):
    target = final_context(); target["scene"]["render"] = {"sub": 6}
    evidence = profile.injected_sub_proof(CONTEXT["scene"], target["scene"], 6)
    if mutation == "different_scene": target["scene"]["id"] = "other"
    elif mutation == "different_proof": evidence["before_scene_sha256"] = "a" * 64
    elif mutation == "changed_camera": target["scene"]["camera"]["fov"] = 50
    else: target["scene"]["render"]["sub"] = 5
    with pytest.raises(ValueError): profile.compilation_identity(target, injection=evidence)
    if mutation != "different_proof":
        with pytest.raises(ValueError): profile.injected_sub_proof(CONTEXT["scene"], target["scene"], 6)


@pytest.mark.parametrize("location", ["aspect", "fps", "cast", "video", "scene", "extra_output"])
def test_only_same_aspect_fps_and_complete_creative_context_can_be_reused(location):
    target = final_context()
    if location == "aspect": target["output"].update(width=1080, height=1920)
    elif location == "fps": target["output"]["fps"] = 60
    elif location == "cast": target["cast"] = [{"id": "new"}]
    elif location == "video": target["video"]["empty_required_value"] = {}
    elif location == "scene": target["scene"]["camera"]["fov"] = 41
    else:
        target["output"]["shutter"] = .2
        with pytest.raises(ValueError): profile.compilation_identity(target)
        return
    assert not profile.assess_reuse(CONTEXT, target, SOURCE)["eligible"]


@pytest.mark.parametrize("expression", ["m.config.pixel_width", "cfg=m.config; x=cfg['pixel_height']",
    "m.config[key]", "m.config.get('frame_rate')", "x=m.Pixels", "m.config.simulation_rate"])
def test_source_format_dependencies_prevent_automatic_equivalence(expression):
    source = "import manimgx as m\n" + expression + "\n"
    assert profile.format_dependencies(source)
    assert not profile.assess_reuse(CONTEXT, final_context(), source)["eligible"]


def test_aspect_only_frame_units_and_bundled_texture_pixels_are_format_independent():
    source = SOURCE + "x=m.config.frame_width\ny=m.config.frame_height\nz=texture.pixel_array\n"
    assert not profile.format_dependencies(source)
    assert profile.assess_reuse(CONTEXT, final_context(), source)["eligible"]


@pytest.fixture
def bench(tmp_path, monkeypatch):
    root = tmp_path / "studio"; original = root / "threed/render.py"
    original.parent.mkdir(parents=True); original.write_text("# Original renderer fixture\n")
    python = root / "pinned-python"; python.write_text("# Existing pinned runtime fixture\n")
    ready = {"ready": True, "identity": "a" * 64, "python": str(python)}
    reference = "b" * 64
    context = final_context()
    key = renderer._digest({"input": context, "assets": {}, "compiler": renderer.COMPILER_VERSION,
        "setup": ready["identity"], "original_references": reference})
    directory = root / "output/exact/threed/.cr-manimgx" / key; directory.mkdir(parents=True)
    result = {"scene_class": "ExactScene", "source": SOURCE, "unsupported": [],
        "coverage": {pointer: "Original requirement preserved exactly." for pointer in renderer._pointers(context)}}
    manifest = {"key": key, "typed": True, "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
        "result": result, "recovery_proof": {"native_stage_id": "cc_" + "c" * 64}}
    path = directory / "compiler.json"; path.write_text(json.dumps(manifest)); (directory / "scene.py").write_text(SOURCE)
    state = SimpleNamespace(root=root, context=context, ready=ready, reference=reference, manifest=path,
        proof=tmp_path / "isolated-proof", calls=[], checks=[])
    monkeypatch.setattr(renderer, "_typecheck", lambda python, source: state.checks.append(source))
    monkeypatch.setattr(renderer, "_compiled_source", lambda *a, **kw: pytest.fail("Benchmark must not compile source"))
    def original_run(command, **kwargs):
        state.calls.append(("original", command))
        video = json.loads(Path(command[2]).read_text())
        assert video == {"cast": context["cast"], "scenes": [context["scene"]]}
        assert "--force" in command and command[command.index("--w") + 1] == "1920"
        output = Path(command[3]); (output / "exact.mp4").write_bytes(b"original video")
        status = getattr(state, "original_status", "ok")
        (output / "report.json").write_text(json.dumps({"w": 1920, "h": 1080, "fps": 30, "quality": "final",
            "scenes": [{"id": "exact", "status": status, "qa": {"ok": status == "ok"}}]}))
        return SimpleNamespace(returncode=0 if status == "ok" else 2)
    def fast_run(python, payload, directory, **kwargs):
        state.calls.append(("fast", payload))
        assert Path(payload["source"]).read_text() == SOURCE and payload["class"] == "ExactScene"
        Path(payload["output"]).write_bytes(b"fast video")
        return {"width": 1920, "height": 1080, "fps": 30, "frames": 31, "duration": 31 / 30}
    monkeypatch.setattr(profile.subprocess, "run", original_run)
    monkeypatch.setattr(renderer, "_invoke", fast_run)
    monkeypatch.setattr(renderer, "_validate_output", lambda *a: None)
    monkeypatch.setattr(profile, "_probe_video", lambda path, w, h, fps, duration, **kw:
        {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "width": w, "height": h, "fps": fps, "duration": duration})
    return state


def run_bench(state, **kwargs):
    return profile.run_zero_model_benchmark(state.root, state.context, {}, state.ready, state.reference,
        state.manifest, state.proof, **kwargs)


def test_zero_model_benchmark_uses_exact_accepted_scene_and_isolated_outputs(bench, monkeypatch):
    old = {path: path.read_bytes() for path in bench.root.rglob("*") if path.is_file()}
    clock = iter([0, 10, 10, 12, 12, 13, 13, 24])
    monkeypatch.setattr(profile.time, "perf_counter", lambda: next(clock))
    result = run_bench(bench, repeats=2)
    assert [kind for kind, _ in bench.calls] == ["original", "fast", "fast", "original"]
    assert result["model_calls"] == 0 and not result["visual_acceptance"] and not result["original_workflow_mutated"]
    assert result["median_render_multiplier"] == 7
    assert result["first_run"] == {"original_seconds": 10, "fast_seconds": 2}
    assert result["repeated_process_median"] == {"original_seconds": 11, "fast_seconds": 1}
    assert len(bench.checks) == 1 and bench.checks[0].is_relative_to(bench.proof)
    assert all(path.read_bytes() == data for path, data in old.items())
    assert json.loads((bench.proof / "accepted-manifest.json").read_text())["recovery_proof"] == {"native_stage_id": "cc_" + "c" * 64}


def test_benchmark_can_use_existing_original_playwright_runtime_without_changing_native_setup(bench):
    original_python = bench.root / "original-python"; original_python.write_text("# Existing original runtime\n")
    result = run_bench(bench, repeats=1, original_python=original_python)
    assert bench.calls[0][1][0] == str(original_python)
    assert result["original_python"] == str(original_python)
    assert result["fast_python"] == bench.ready["python"]


def test_original_completed_movie_qa_issues_are_recorded_without_claiming_visual_acceptance(bench):
    bench.original_status = "rendered_with_issues"
    result = run_bench(bench, repeats=1)
    assert result["trials"][0]["original"]["original_qa"]["qa"] == {"ok": False}
    assert result["visual_acceptance"] is False


def test_original_renderer_error_is_not_counted_as_completed_benchmark(bench):
    bench.original_status = "error"
    with pytest.raises(RuntimeError, match="no benchmark multiplier"):
        run_bench(bench, repeats=1)
    assert [kind for kind, _ in bench.calls] == ["original"]


@pytest.mark.parametrize("mutation", ["context", "source", "typed", "inside_studio", "existing_proof", "fps", "setup"])
def test_benchmark_rejects_changed_or_unaccepted_inputs_before_rendering(bench, mutation):
    if mutation == "context": bench.context["scene"]["camera"]["fov"] = 50
    elif mutation == "source": bench.manifest.with_name("scene.py").write_text(SOURCE + "# Changed\n")
    elif mutation == "typed":
        value = json.loads(bench.manifest.read_text()); value["typed"] = False; bench.manifest.write_text(json.dumps(value))
    elif mutation == "inside_studio": bench.proof = bench.root / "output/benchmark"
    elif mutation == "existing_proof": bench.proof.mkdir()
    elif mutation == "fps": bench.context["output"]["fps"] = 60
    else: bench.ready["identity"] = "d" * 64
    with pytest.raises(ValueError): run_bench(bench, repeats=1)
    assert not bench.calls and not bench.checks


@pytest.mark.parametrize("changed", [{"width": 640}, {"avg_frame_rate": "60/1"}, {"codec_name": "vp9"}])
def test_benchmark_requires_matching_actual_movie_format(monkeypatch, tmp_path, changed):
    path = tmp_path / "video.mp4"; path.write_bytes(b"fixture")
    stream = {"width": 1920, "height": 1080, "avg_frame_rate": "30/1", "nb_frames": "30", "codec_name": "h264", **changed}
    monkeypatch.setattr(profile.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0,
        stdout=json.dumps({"streams": [stream], "format": {"duration": "1"}})))
    with pytest.raises(RuntimeError): profile._probe_video(path, 1920, 1080, 30, 1, environment={})
