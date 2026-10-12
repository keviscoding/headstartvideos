"""Optional renderer contract: no model/network calls, plus a local GPU fixture.

The native checks run when the separately installed ManimGX environment exists.
The original review functions are read from the owner's installation, never copied.
"""
import ast
import copy
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_manimgx as gx

SOURCE = """import manimgx as m
class BoardFixture(m.ThreeDScene):
    def construct(self):
        self.set_camera_orientation(phi=65*m.DEGREES, theta=-40*m.DEGREES)
        cube = m.Cube(side_length=2, fill_opacity=0.7, color=m.BLUE)
        self.add(cube)
        self.play(m.Rotate(cube, m.PI/2, axis=m.UP), run_time=0.4)
"""
VIDEO = {"cast": [], "scenes": [{"id": "cube", "dur": 0.4,
    "look": {"preset": "documentary"},
    "objects": [{"kind": "prop.build", "parts": [{"shape": "box", "size": [2, 2, 2], "color": "blue"}],
                 "rotation": {"axis": "up", "degrees": 90, "duration": 0.4}}],
    "camera": {"phi": 65, "theta": -40}}]}
SMALL = ["--w", "320", "--h", "180", "--quality", "draft"]
PREVIEW = SMALL + ["--preview-frac", "0.2,0.5,0.8"]


def manifest(context, source=SOURCE, unsupported=None):
    return {"scene_class": "BoardFixture", "source": source,
            "coverage": {p: "Preserved by the original board fixture." for p in gx._pointers(context)},
            "unsupported": unsupported or []}


def fixture_compiler(prompt, maximum):
    assert maximum == 16384
    context = json.loads(prompt.split("ORIGINAL INPUT:\n", 1)[1].split("\nLOCAL ASSETS:", 1)[0])
    return json.dumps(manifest(context))


@pytest.fixture
def stub_runtime(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    for filename in gx.DOCS:
        (docs / filename).write_text("Pinned local API fixture; no external instructions.")
    ready = {"ready": True, "version": gx.MANIMGX_VERSION, "identity": "setup-one",
             "python": str(tmp_path / "python"), "docs": str(docs)}
    monkeypatch.setattr(gx, "readiness", lambda *args, **kwargs: dict(ready))
    monkeypatch.setattr(gx, "_typecheck", lambda *args, **kwargs: None)
    state = SimpleNamespace(root=tmp_path, ready=ready, invocations=[], issues=[])
    def invoke(python, payload, directory, *, timeout, event=None):
        state.invocations.append(copy.deepcopy(payload))
        frames = math.ceil(payload["duration"] * payload["fps"]) + 1
        stills = []
        for index, moment in enumerate(sorted(set(payload["times"]))):
            path = Path(payload["still_dir"]) / f"{payload['id']}_{index}.jpg"
            Image.new("RGB", (payload["width"], payload["height"]), "blue").save(path)
            stills.append({"t": moment, "file": str(path), "qa": {"warnings": []}})
        if event:
            event({"event": "frame", "frame": 4, "time": .1, "elapsed": .2})
            event({"event": "exporting", "frame": frames, "time": payload["duration"], "elapsed": .3})
        return {"event": "result", "frames": frames, "duration": frames / payload["fps"],
                "width": payload["width"], "height": payload["height"], "fps": payload["fps"],
                "stills": stills, "issues": state.issues, "layout_checked": True,
                "exported": bool(payload["output"]), "elapsed": .4}
    monkeypatch.setattr(gx, "_invoke", invoke)
    return state


def run_stub(state, *, engine=None, video=None, args=None, compiler=fixture_compiler, event=None, output="review"):
    return gx.render(engine or SimpleNamespace(), copy.deepcopy(VIDEO if video is None else video),
                     state.root / "job/threed" / output, PREVIEW if args is None else args,
                     "test review", root=state.root, compiler=compiler, event=event)


def test_install_keeps_original_default_and_selects_each_projects_pinned_renderer(tmp_path, monkeypatch):
    calls = []
    def original(*args):
        calls.append(args)
        return "original"
    module = SimpleNamespace(_render=original)
    monkeypatch.setitem(sys.modules, "threed", module)
    bridge = SimpleNamespace(_project={}, load_settings=lambda: {"brain_mode": "claudecode"})
    engine = SimpleNamespace()
    monkeypatch.setattr(gx, "readiness", lambda *a, **kw: {"ready": True, "identity": "proof"})
    monkeypatch.setattr(gx, "render", lambda *a, **kw: (a, kw))
    gx.install(engine, bridge, root=tmp_path)
    wrapped = module._render
    gx.install(engine, bridge, root=tmp_path)
    assert module._render is wrapped
    values = (engine, VIDEO, tmp_path / "out", ["--force"], "final", 6, object())
    assert wrapped(*values) == "original" and calls == [values]
    bridge._project = {"job_spec": {"render_backend": "manimgx", "manimgx_identity": "proof"}}
    actual, kwargs = wrapped(*values)
    assert actual == values and kwargs["root"] == tmp_path
    bridge._project = {"job_spec": {"render_backend": "original"}}
    assert wrapped(*values) == "original" and len(calls) == 2


@pytest.mark.parametrize("mode,identity,message", [
    ("api", "proof", "owned native"), ("claudecode", "stale", "environment changed"),
    ("claudecode", None, "environment changed")])
def test_opt_in_never_falls_back_on_account_or_identity_failure(tmp_path, monkeypatch, mode, identity, message):
    def forbidden(*args, **kwargs):
        pytest.fail("A selected renderer cannot silently fall back")
    module = SimpleNamespace(_render=forbidden)
    monkeypatch.setitem(sys.modules, "threed", module)
    bridge = SimpleNamespace(_project={"job_spec": {"render_backend": "manimgx", "manimgx_identity": identity}},
                             load_settings=lambda: {"brain_mode": mode})
    monkeypatch.setattr(gx, "readiness", lambda *a, **kw: {"ready": True, "identity": "proof"})
    monkeypatch.setattr(gx, "render", forbidden)
    gx.install(SimpleNamespace(), bridge, root=tmp_path)
    with pytest.raises(gx.ManimGXStageStopped, match=message):
        module._render(SimpleNamespace(), VIDEO, tmp_path, [], "test")


def test_compiler_uses_original_native_model_and_engine_account(stub_runtime):
    calls = []
    def native(prompt, model, *, max_tokens):
        calls.append((prompt, model, max_tokens))
        return fixture_compiler(prompt, max_tokens)
    engine = SimpleNamespace(_cr_original_cc_model="owned-model-choice", CLAUDE_PROVIDER="claudecode", claude=native)
    result = run_stub(stub_runtime, engine=engine, compiler=None)
    assert len(calls) == 1 and calls[0][1:] == ("owned-model-choice", 16384)
    assert result["scenes"][0]["status"] == "preview"


@pytest.mark.parametrize("model,provider", [(None, "claudecode"), ("original", "api"), ("original", None)])
def test_missing_native_model_does_not_route_to_a_paid_api(stub_runtime, model, provider):
    engine = SimpleNamespace(_cr_original_cc_model=model, CLAUDE_PROVIDER=provider,
                             claude=lambda *a, **kw: pytest.fail("No authorized native compiler"))
    with pytest.raises(RuntimeError, match="owned native Claude"):
        run_stub(stub_runtime, engine=engine, compiler=None)
    assert not stub_runtime.invocations
    assert gx.progress_status(stub_runtime.root)["state"] == "failed"


def test_compiler_sees_every_input_leaf_assets_and_output_format(stub_runtime):
    video = copy.deepcopy(VIDEO)
    video.update({"directors_note": "keep the empty landscape", "empty": {}, "a/b~": [None, False]})
    video["cast"] = [{"id": "narrator", "costume": "blue"}]
    video["scenes"][0]["render"] = {"sub": 6}
    captured = []
    def compiler(prompt, maximum):
        context = json.loads(prompt.split("ORIGINAL INPUT:\n", 1)[1].split("\nLOCAL ASSETS:", 1)[0])
        captured.append(context)
        return fixture_compiler(prompt, maximum)
    run_stub(stub_runtime, video=video, compiler=compiler)
    context = captured[0]
    assert context["scene"] == video["scenes"][0] and context["cast"] == video["cast"]
    assert context["video"] == {key: value for key, value in video.items() if key not in {"cast", "scenes"}}
    assert context["output"] == {"width": 320, "height": 180, "fps": 30, "quality": "draft"}
    pointers = gx._pointers(context)
    assert "/video/a~1b~0/0" in pointers and "/video/empty" in pointers and "/scene/render/sub" in pointers


def test_cache_reuses_verified_source_but_invalidates_changed_format_board_and_setup(stub_runtime):
    calls = []
    def compiler(prompt, maximum):
        calls.append(prompt)
        return fixture_compiler(prompt, maximum)
    first = run_stub(stub_runtime, compiler=compiler)
    again = run_stub(stub_runtime, compiler=compiler, output="review2")
    assert len(calls) == 1
    assert first["scenes"][0]["compiler_key"] == again["scenes"][0]["compiler_key"]
    run_stub(stub_runtime, compiler=compiler, args=["--w", "640", "--h", "360", "--preview", "0.2"])
    video = copy.deepcopy(VIDEO)
    video["scenes"][0]["look"]["preset"] = "night"
    run_stub(stub_runtime, compiler=compiler, video=video)
    stub_runtime.ready["identity"] = "setup-two"
    run_stub(stub_runtime, compiler=compiler)
    assert len(calls) == 4


@pytest.mark.parametrize("change", ["missing", "extra", "empty", "unsupported", "syntax", "non_json", "non_text"])
def test_bad_compiler_results_stop_before_render_and_record_failure(stub_runtime, change):
    events = []
    def compiler(prompt, maximum):
        value = json.loads(fixture_compiler(prompt, maximum))
        if change == "missing":
            value["coverage"].pop(next(iter(value["coverage"])))
        elif change == "extra":
            value["coverage"]["/invented"] = "This requirement was invented."
        elif change == "empty":
            value["coverage"][next(iter(value["coverage"]))] = "yes"
        elif change == "unsupported":
            value["unsupported"] = ["Film sequences cannot be represented faithfully"]
        elif change == "syntax":
            value["source"] = "class Broken( syntax is incomplete"
        elif change == "non_json":
            return "This scene looks fine."
        elif change == "non_text":
            return None
        return json.dumps(value)
    with pytest.raises(RuntimeError):
        run_stub(stub_runtime, compiler=compiler, event=events.append)
    assert not stub_runtime.invocations
    assert [event["event"] for event in events] == ["compile", "failed"]
    assert gx.progress_status(stub_runtime.root)["state"] == "failed"
    report = gx._read(stub_runtime.root / "job/threed/review/report.json")
    assert report["scenes"][0]["status"] == "error" and "mp4" not in report["scenes"][0]


@pytest.mark.parametrize("line", ["import os", "import random", "from manimgx import Scene", "open('secret')",
    "m.config.pixel_width = 5", "m.config['frame_rate'] = 5", "m.config.update({'frame_rate': 5})",
    "m.ImageMobject('https://example.invalid/image.jpg')", "m.ImageMobject('/private/unapproved.jpg')",
    "m.Speech('unrequested audio')", "m.config.preview = True", "m.np.random.random()", "exec('pass')"])
def test_source_rejects_unapproved_side_effects_or_format_override(line):
    source = SOURCE + "\n" + line + "\n"
    with pytest.raises(RuntimeError):
        gx._validate_source(source, "BoardFixture")


def test_verified_local_asset_is_the_only_accepted_image_source(tmp_path):
    directory = tmp_path / "threed/assets"
    directory.mkdir(parents=True)
    path = directory / "original.jpg"
    Image.new("RGB", (8, 8), "blue").save(path)
    assets = gx._assets({"image": {"src": "assets/original.jpg"}}, tmp_path / "threed/review")
    assert assets["assets/original.jpg"]["sha256"] == gx._file_hash(path)
    source = SOURCE.replace("self.add(cube)", f"self.add(cube, m.ImageMobject({str(path.resolve())!r}))")
    gx._validate_source(source, "BoardFixture", assets)
    for src in ["https://example.invalid/photo.jpg", "assets/missing.jpg", "../outside.jpg", "assets/"]:
        with pytest.raises(RuntimeError):
            gx._assets({"image": {"src": src}}, tmp_path / "threed/review")


@pytest.mark.parametrize("args", [["--w"], ["--w", "--h", "40"], ["--w", "three"], ["--w", "321"],
    ["--h", "0"], ["--preview-frac", "nan"], ["--preview-frac", "1.1"], ["--preview", "-1"],
    ["--preview", ""], ["--quality", "ultra"], ["--sub", "6"], ["--unknown"], ["stray"]])
def test_unknown_or_invalid_original_options_fail_explicitly(args):
    with pytest.raises(RuntimeError):
        gx._options(args, VIDEO)


def test_option_defaults_match_original_renderer():
    final = gx._options([], VIDEO)
    preview = gx._options(["--preview", "0.2"], VIDEO)
    assert (final["width"], final["height"], final["fps"], final["quality"]) == (1920, 1080, 30, "final")
    assert (preview["width"], preview["height"], preview["quality"]) == (960, 540, "final")


def test_fractional_preview_precedence_and_quality_encoding_match_original(stub_runtime):
    run_stub(stub_runtime, args=PREVIEW + ["--preview", "0.1"])
    assert stub_runtime.invocations[-1]["times"] == [.4 * .2, .4 * .5, .4 * .8]
    assert stub_runtime.invocations[-1]["preset"] == "veryfast"
    run_stub(stub_runtime, args=["--w", "320", "--h", "180", "--preview", "0.2", "--quality", "final"])
    assert stub_runtime.invocations[-1]["preset"] == "slow" and stub_runtime.invocations[-1]["crf"] == 16


def test_render_progress_is_bounded_and_excludes_private_source(stub_runtime):
    events = []
    result = run_stub(stub_runtime, event=events.append)
    status = gx.progress_status(stub_runtime.root)
    assert status["state"] == "complete" and status["frames_done"] == 13 and status["total_frames"] == 13
    assert {event["event"] for event in events} == {"compile", "frame", "exporting", "complete"}
    assert status["elapsed_seconds"] == .4 and status["scenes"]["cube"]["state"] == "complete"
    assert "source" not in json.dumps(status) and "compiler_key" not in json.dumps(status)
    assert result["scenes"][0]["qa"]["checks"]["frontier_camera_clearance"] == "not_available"
    gx._progress(stub_runtime.root, {"scene_id": "cube", "event": "frame", "frame": float("nan"), "fps": True})
    status = gx.progress_status(stub_runtime.root)
    assert "frames_done" not in status and "fps" not in status
    before = gx.progress_status(stub_runtime.root)
    gx._progress(stub_runtime.root, {"scene_id": "../secret", "event": "frame", "frame": 1})
    assert gx.progress_status(stub_runtime.root) == before


def test_corrupt_saved_status_and_readiness_fail_safely(tmp_path):
    gx._write(tmp_path / ".cr-manimgx/ready.json", [])
    assert gx.readiness(tmp_path)["ready"] is False
    gx._write(tmp_path / ".cr-manimgx/progress.json", {"scenes": ["corrupt"], "source": "secret"})
    assert gx.progress_status(tmp_path) == {"scenes": {}}


def test_layout_warnings_remain_reviewable_without_claiming_camera_failure(stub_runtime):
    stub_runtime.issues = ["manimgx_layout: clipped: Label: outside frame"]
    record = run_stub(stub_runtime)["scenes"][0]
    assert record["qa"]["ok"] is True and record["qa"]["issues"] == stub_runtime.issues
    assert record["qa"]["checks"]["layout_2d"] is True


def test_failed_render_replaces_stale_report_and_marks_progress_failed(stub_runtime, monkeypatch):
    run_stub(stub_runtime)
    def fail(*args, **kwargs):
        raise RuntimeError("ManimGX shader compilation failed")
    monkeypatch.setattr(gx, "_invoke", fail)
    with pytest.raises(RuntimeError, match="shader compilation"):
        run_stub(stub_runtime)
    report = gx._read(stub_runtime.root / "job/threed/review/report.json")
    assert len(report["scenes"]) == 1 and report["scenes"][0]["status"] == "error"
    assert gx.progress_status(stub_runtime.root)["state"] == "failed"


def test_child_environment_excludes_service_credentials_and_python_path(monkeypatch):
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FAL_KEY", "VOICE_TOKEN", "CLAUDE_CONFIG_DIR", "PYTHONPATH", "PYTHONHOME"]:
        monkeypatch.setenv(key, "private-value")
    monkeypatch.setenv("NORMAL_RENDER_OPTION", "keep")
    env = gx._environment()
    assert "private-value" not in env.values() and env["NORMAL_RENDER_OPTION"] == "keep"
    assert env["PYTHONUNBUFFERED"] == "1"


def child_process(monkeypatch, program):
    popen = subprocess.Popen
    children = []
    def launch(args, **kwargs):
        child = popen([sys.executable, "-c", program], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(gx.subprocess, "Popen", launch)
    return children


@pytest.mark.parametrize("close_output", [False, True])
def test_real_child_timeout_kills_process_even_when_stdout_is_closed(tmp_path, monkeypatch, close_output):
    program = "import json,os,sys,time\njson.load(sys.stdin)\nprint('CR_MANIMGX '+json.dumps({'event':'frame','frame':3}),flush=True)\n"
    if close_output:
        program += "os.close(1)\nos.close(2)\n"
    program += "time.sleep(30)\n"
    children = child_process(monkeypatch, program)
    events = []
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="time ceiling"):
        gx._invoke(sys.executable, {}, tmp_path, timeout=.35, event=events.append)
    assert time.monotonic() - started < 3
    assert len(children) == 1 and children[0].poll() is not None
    assert events == [{"event": "frame", "frame": 3}]
    assert "CR_MANIMGX" in (tmp_path / "render_stdout.log").read_text()


@pytest.mark.parametrize("program,message", [
    ("print('shader compilation failed');raise SystemExit(1)", "could not build"),
    ("print('CR_MANIMGX not json')", "invalid progress"),
    ("print('CR_MANIMGX []')", "invalid progress"),
    ("print('finished without result')", "could not build")])
def test_child_shader_or_protocol_failure_cannot_report_completion(tmp_path, monkeypatch, program, message):
    children = child_process(monkeypatch, "import json,sys\njson.load(sys.stdin)\n" + program)
    with pytest.raises(RuntimeError, match=message):
        gx._invoke(sys.executable, {}, tmp_path, timeout=3)
    assert children[0].poll() is not None
    assert (tmp_path / "render_stdout.log").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("changes", [{"duration": float("nan")}, {"duration": float("inf")}, {"frames": 1},
    {"frames": True}, {"fps": 24}, {"width": 642}, {"duration": 4}])
def test_invalid_frame_metadata_is_rejected_before_completion(changes):
    value = {"frames": 13, "duration": 13/30, "fps": 30, "width": 320, "height": 180, **changes}
    with pytest.raises(RuntimeError, match="checks"):
        gx._validate_frames(value, .4, 30, 320, 180)


@pytest.mark.parametrize("bad", ["missing", "corrupt", "outside", "wrong_size", "wrong_time", "duplicate"])
def test_preview_files_are_independently_verified(tmp_path, bad):
    out = (tmp_path / "review").resolve()
    out.mkdir()
    path = out / "still.jpg"
    Image.new("RGB", (320, 180), "blue").save(path)
    stills = [{"t": .2, "file": str(path)}]
    if bad == "missing":
        path.unlink()
    elif bad == "corrupt":
        path.write_bytes(b"not an image")
    elif bad == "outside":
        other = tmp_path / "elsewhere.jpg"
        shutil.copyfile(path, other)
        stills[0]["file"] = str(other)
    elif bad == "wrong_size":
        Image.new("RGB", (10, 10)).save(path)
    elif bad == "wrong_time":
        stills[0]["t"] = .3
    elif bad == "duplicate":
        stills.append(dict(stills[0]))
    with pytest.raises(RuntimeError, match="preview frames"):
        gx._validate_stills({"stills": stills}, [.2], out, 320, 180)


@pytest.fixture(scope="module")
def native_runtime(tmp_path_factory):
    optional = Path(os.environ.get("FRONTIER_TEST_MANIMGX_DIR", ROOT / "data/frontier/manimgx"))
    python = optional / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file() or not (optional / "docs").is_dir():
        pytest.skip("The optional pinned ManimGX environment has not been installed")
    if not shutil.which("ffprobe"):
        pytest.skip("Native output validation requires the original video tools")
    root = tmp_path_factory.mktemp("manimgx_native")
    ready = gx.prepare(root, python, optional / "docs")
    assert ready["ready"]
    return SimpleNamespace(root=root, ready=ready, python=python)


def test_native_zero_claude_compile_preview_and_video_are_original_report_compatible(native_runtime):
    state = native_runtime
    engine = SimpleNamespace(claude=lambda *a, **kw: pytest.fail("Fixture must never call Claude"))
    events, completed = [], []
    preview = gx.render(engine, VIDEO, state.root / "threed/review", PREVIEW, "fixture preview",
                        root=state.root, compiler=fixture_compiler, event=events.append)
    final = gx.render(engine, VIDEO, state.root / "threed/final", SMALL, "fixture final", 1, completed.append,
                      root=state.root, compiler=lambda *a: pytest.fail("Identical source should be cached"), event=events.append)
    assert completed == [1]
    assert preview["scenes"][0]["status"] == "preview" and "mp4" not in preview["scenes"][0]
    assert final["scenes"][0]["status"] == "ok" and Path(final["scenes"][0]["mp4"]).stat().st_size > 1000
    assert preview["scenes"][0]["compiler_key"] == final["scenes"][0]["compiler_key"]
    assert {e["event"] for e in events} == {"compile", "frame", "exporting", "complete"}
    assert gx.progress_status(state.root)["state"] == "complete"
    for report in [preview, final]:
        assert (report["w"], report["h"], report["fps"]) == (320, 180, 30)
        assert report["scenes"][0]["load"]["look"]["preset"] == "documentary"
        for still in report["scenes"][0]["stills"]:
            with Image.open(still["file"]) as image:
                assert image.size == (320, 180)
    state.preview, state.final = preview, final


def test_native_report_feeds_original_contact_sheet_issues_and_luminance(native_runtime):
    original = Path(os.environ.get("FRONTIER_TEST_ENGINE_DIR", Path.home() / "Downloads/ChannelRecipe studio")) / "threed.py"
    if not original.is_file():
        pytest.skip("Original private review consumer is not installed")
    preview = gx.render(SimpleNamespace(), VIDEO, native_runtime.root / "threed/consumer", PREVIEW, "review",
                        root=native_runtime.root, compiler=fixture_compiler)
    names = {"contact_sheets", "_issues", "_issue_str", "_no_subject", "_luma"}
    tree = ast.parse(original.read_text(encoding="utf-8"), filename=str(original))
    definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(definitions) == len(names)
    namespace = {"Path": Path, "ROOT": original.parent / "threed", "CODE_RE": re.compile(r"^\s*([a-z][a-z0-9_]{2,24}):\s")}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(original), "exec"), namespace)
    assert namespace["_issues"](preview) == {"cube": []}
    paths, legend = namespace["contact_sheets"](preview, VIDEO["scenes"], native_runtime.root / "contact", frames=3, width=960)
    assert len(paths) == 1 and paths[0].is_file() and "missing" not in legend[0]
    luminance = namespace["_luma"](preview["scenes"][0]["stills"][1]["file"])
    assert luminance["mean"] > 0 and luminance["std"] > 0


def test_native_typecheck_rejects_unknown_api_before_gpu_render(native_runtime, tmp_path):
    path = tmp_path / "bad_api.py"
    path.write_text(SOURCE.replace("side_length=2", "invented_size=2"))
    with pytest.raises(RuntimeError, match="pinned API"):
        gx._typecheck(native_runtime.python, path)
    diagnostic = path.with_suffix(".validation.log")
    assert "invented_size" in diagnostic.read_text() and diagnostic.stat().st_mode & 0o077 == 0


def test_native_close_preview_times_do_not_overwrite_requested_frames(native_runtime):
    report = gx.render(SimpleNamespace(), VIDEO, native_runtime.root / "threed/close-times",
                       SMALL + ["--preview", "0.20001,0.20002"], "precise preview", root=native_runtime.root,
                       compiler=fixture_compiler)
    stills = report["scenes"][0]["stills"]
    assert [still["t"] for still in stills] == [.20001, .20002]
    assert len({still["file"] for still in stills}) == 2


def test_native_runtime_exception_propagates_with_private_diagnostic(native_runtime, tmp_path):
    source = tmp_path / "broken.py"
    source.write_text(SOURCE.replace("self.add(cube)", "raise RuntimeError('native fixture shader failure')"))
    with pytest.raises(RuntimeError, match="could not build"):
        gx._invoke(native_runtime.python, {"source": str(source), "class": "BoardFixture", "output": str(tmp_path / "bad.mp4"),
                   "width": 320, "height": 180, "fps": 30, "duration": .4, "times": [.2]}, tmp_path, timeout=10)
    assert "native fixture shader failure" in (tmp_path / "render_stdout.log").read_text()
    assert not (tmp_path / "bad.mp4").exists()


def test_native_duration_mismatch_is_not_completed(native_runtime):
    def wrong_timing(prompt, maximum):
        value = json.loads(fixture_compiler(prompt, maximum))
        value["source"] = value["source"].replace("run_time=0.4", "run_time=0.1")
        return json.dumps(value)
    changed = copy.deepcopy(VIDEO)
    changed["scenes"][0]["id"] = "too_short"
    with pytest.raises(RuntimeError, match="duration, frame"):
        gx.render(SimpleNamespace(), changed, native_runtime.root / "threed/short", PREVIEW, "short test",
                  root=native_runtime.root, compiler=wrong_timing)
    assert gx.progress_status(native_runtime.root)["state"] == "failed"


def test_native_encoded_video_fps_is_checked_independently(native_runtime, tmp_path):
    ready = gx._read(native_runtime.root / ".cr-manimgx/ready.json")
    destination = tmp_path / "wrong_fps.mp4"
    shutil.copyfile(ready["smoke"], destination)
    # The setup MP4 is 15 FPS; a child claiming it was 30 FPS must not be believed.
    response = {"frames": 20, "duration": 20/30, "fps": 30, "width": 320, "height": 180, "exported": True}
    with pytest.raises(RuntimeError, match="independent video-tools"):
        gx._validate_output(response, destination, .6, 30, 320, 180)
    assert not destination.exists()


def test_native_readiness_detects_tampered_smoke_file(native_runtime, tmp_path):
    proof = gx._read(native_runtime.root / ".cr-manimgx/ready.json")
    smoke = tmp_path / "tampered.mp4"
    smoke.write_bytes(Path(proof["smoke"]).read_bytes() + b"changed")
    proof["smoke"] = str(smoke)
    gx._write(tmp_path / ".cr-manimgx/ready.json", proof)
    result = gx.readiness(tmp_path)
    assert result["ready"] is False and "missing or changed" in result["message"]
