"""Explicit native Fast mode quality and proof-only local render benchmarks.

This module changes no director, review, repair, media or narration decisions.
The selected renderer draws every 3D scene with ManimGX. Its native spatial
antialiasing is not Frontier's temporal shutter/aperture accumulation. Only a
proved setting newly injected by the original output runner may leave creative
compile identity; explicit scene optics remain requirements for the compiler.

Reuse assessment is not cache acceptance. The caller still verifies the native
receipt, source/asset/reference/setup hashes, original AST policy and pinned API
checker before borrowing an accepted class. No historical record is rewritten.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import time

VERSION = "cr-manimgx-fast-profile.1"
MANIMGX_VERSION = "0.1.1"
_HASH = re.compile(r"[a-f0-9]{64}")
_FORMAT_FIELDS = {"pixel_width", "pixel_height", "frame_rate", "simulation_rate"}
_ASPECT_FIELDS = {"frame_width", "frame_height", "frame_x_radius", "frame_y_radius", "aspect_ratio"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _copy(value):
    # Refuse non-JSON and nonfinite inputs; never mutate the original board.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def native_profile(options):
    """The pinned native settings the runner can actually apply, without optics claims."""
    if not isinstance(options, dict):
        raise ValueError("Fast mode needs the original output settings.")
    width, height, fps = (options.get(name) for name in ("width", "height", "fps"))
    if (any(isinstance(v, bool) or not isinstance(v, int) for v in (width, height, fps))
            or min(width, height) < 2 or max(width, height) > 7680 or width % 2 or height % 2
            or not 1 <= fps <= 240 or options.get("quality") not in {"draft", "final"}):
        raise ValueError("Fast mode requires even output dimensions, whole FPS and original draft/final quality.")
    divisor = math.gcd(width, height)
    return {"version": VERSION, "label": "Fast mode", "renderer": "manimgx",
        "renderer_version": MANIMGX_VERSION, "whole_3d_video": True,
        "width": width, "height": height, "fps": fps, "aspect": [width // divisor, height // divisor],
        "quality": options["quality"], "simulation_rate": 60,
        "msaa_samples": 4, "msaa_source": "Pinned Film uses Player(width, height), whose spatial samples default is 4.",
        "frame_sampling": "One scene instant k / fps per frame; simulation ticks are not motion blur.",
        "encoder": {"preset": "slow" if options["quality"] == "final" else "veryfast", "crf": 16},
        "optical_limits": ["Native MSAA is spatial antialiasing, not temporal shutter accumulation.",
            "No automatic aperture supersampling, depth-of-field or shutter mapping is supplied.",
            "Explicit scene optics must be implemented faithfully or reported as unsupported."],
        "quality_scope": "Native Fast renderer settings; original planning, reviews and repairs still run.",
        "speed_scope": "Measured local 3D rendering only; script, models, narration and full generation are excluded."}


def apply_native_config(config, profile):
    """Call before making a scene; only documented pinned Config fields are set."""
    expected = native_profile(profile)
    if profile != expected:
        raise ValueError("Use this adapter's exact native Fast mode profile.")
    config.pixel_width, config.pixel_height = profile["width"], profile["height"]
    config.frame_rate, config.simulation_rate = profile["fps"], profile["simulation_rate"]


def render_kwargs(profile):
    if profile != native_profile(profile):
        raise ValueError("Use this adapter's exact native Fast mode profile.")
    return copy.deepcopy(profile["encoder"])


def injected_sub_proof(before_scene, after_scene, value):
    """Prove exact original _video(sub=None) -> _video(sub=value) output injection.

    Both scenes must come from the same original _video call/preset. Existing
    director/user `render.sub` is never eligible, even if its value equals cfg.sub.
    A missing render container is distinct from an explicitly empty container.
    """
    before, after = _copy(before_scene), _copy(after_scene)
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("Compare original rendered scene objects.")
    if "render" in before and not isinstance(before["render"], dict):
        raise ValueError("The original scene's render settings are incomplete.")
    if "sub" in before.get("render", {}):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 12:
        raise ValueError("Verify the original runner's finite sub-frame setting.")
    expected = _copy(before)
    expected.setdefault("render", {})["sub"] = value
    if _digest(expected) != _digest(after):
        raise ValueError("The original scene changed beyond its newly injected sub-frame setting.")
    return {"version": VERSION, "origin": "original_video_output_injection",
        "pointer": "/scene/render/sub", "value": value,
        "before_scene_sha256": _digest(before), "after_scene_sha256": _digest(after),
        "render_container_was_present": "render" in before}


def _creative_scene(scene, injection):
    result = _copy(scene)
    if injection is None:
        return result
    if (not isinstance(injection, dict) or injection.get("version") != VERSION
            or injection.get("origin") != "original_video_output_injection"
            or injection.get("pointer") != "/scene/render/sub"
            or injection.get("after_scene_sha256") != _digest(result)
            or not isinstance(result.get("render"), dict)
            or result["render"].get("sub") != injection.get("value")
            or not isinstance(injection.get("render_container_was_present"), bool)):
        raise ValueError("The original output-injection evidence changed.")
    del result["render"]["sub"]
    if not injection["render_container_was_present"]:
        if result["render"]:
            raise ValueError("Preserve the original scene's explicit render requirements.")
        del result["render"]
    if _digest(result) != injection.get("before_scene_sha256"):
        raise ValueError("The complete original scene no longer matches the injection evidence.")
    return result


def compilation_identity(context, *, injection=None):
    """A separate native identity; it never replaces an existing accepted cache key."""
    value = _copy(context)
    if (not isinstance(value, dict) or set(value) != {"scene", "cast", "video", "output"}
            or not isinstance(value["scene"], dict) or not isinstance(value["video"], dict)
            or not isinstance(value["cast"], list) or not isinstance(value["output"], dict)
            or set(value["output"]) != {"width", "height", "fps", "quality"}):
        raise ValueError("Preserve the complete original scene, cast, video and output context.")
    profile = native_profile(value["output"])
    value["scene"] = _creative_scene(value["scene"], injection)
    # Dimensions and encoder settings are runner-owned. Same aspect/FPS is
    # required; every scene/cast/video field and explicit optical field stays.
    value["output"] = {"aspect": profile["aspect"], "fps": profile["fps"],
        "native_profile": VERSION, "simulation_rate": profile["simulation_rate"],
        "spatial_msaa": profile["msaa_samples"], "frame_sampling": "single_instant"}
    return {"identity": _digest(value), "creative_context": value,
        "request_sha256": _digest(context), "profile": profile, "injection": _copy(injection)}


def format_dependencies(source):
    """Conservatively flag direct, aliased or dynamic native output-config access."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in _FORMAT_FIELDS or node.attr == "Pixels":
                found.add(node.attr)
            if node.attr == "config":
                parent = parents.get(node)
                if not (isinstance(parent, ast.Attribute) and parent.value is node and parent.attr in _ASPECT_FIELDS):
                    found.add("output_config_object_or_dynamic_access")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _FORMAT_FIELDS:
            found.add(node.value)
        elif isinstance(node, ast.Name) and node.id == "Pixels":
            found.add("Pixels")
    return sorted(found)


def assess_reuse(source_context, target_context, source, *, source_injection=None, target_injection=None):
    """Assess equivalence only; the caller must still revalidate source and receipts."""
    left = compilation_identity(source_context, injection=source_injection)
    right = compilation_identity(target_context, injection=target_injection)
    dependencies = format_dependencies(source)
    return {"eligible": left["identity"] == right["identity"] and not dependencies,
        "creative_identity": left["identity"], "target_creative_identity": right["identity"],
        "source_context_sha256": left["request_sha256"], "target_context_sha256": right["request_sha256"],
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "format_dependencies": dependencies,
        "source_injection": left["injection"], "target_injection": right["injection"],
        "profile": right["profile"], "requires_fresh_ast_and_api_check": True,
        "requires_accepted_native_receipt": True, "changes_historical_records": False}


def _probe_video(path, width, height, fps, duration, *, environment):
    completed = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,codec_name,avg_frame_rate,nb_frames:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=20, env=environment)
    try:
        result = json.loads(completed.stdout); stream = result["streams"][0]
        top, bottom = stream["avg_frame_rate"].split("/")
        valid = (completed.returncode == 0 and stream["width"] == width and stream["height"] == height
            and stream["codec_name"] == "h264" and float(top) / float(bottom) == fps
            and int(stream["nb_frames"]) > 0 and abs(float(result["format"]["duration"]) - duration) <= 2 / fps + .001)
    except (KeyError, ValueError, IndexError, TypeError, ZeroDivisionError):
        valid = False
    if not valid:
        raise RuntimeError("The isolated benchmark did not render the identical output size, FPS and scene duration.")
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
        "width": width, "height": height, "fps": fps, "duration": float(result["format"]["duration"]),
        "frames": int(stream["nb_frames"])}


def run_zero_model_benchmark(root, context, assets, ready, reference_identity, accepted_manifest,
        proof_dir, *, repeats=3, assets_root=None, original_python=None, timeout=600):
    """Render an already accepted class and identical original scene, outside the studio.

    Uses only original render.py and the local Manim child runner; no compiler,
    bridge, provider or project route is called. This is performance evidence,
    not visual acceptance. Original --force retains the exact supplied camera in
    this isolated comparison; original project reviews and caches stay untouched.
    Each trial uses fresh processes. First and repeated-process wall times are
    reported separately; repeated trials are not claimed as warm GPU throughput.
    """
    import cr_manimgx as renderer
    root, proof_dir = Path(root).resolve(), Path(proof_dir).resolve()
    if proof_dir.is_relative_to(root) or proof_dir.exists():
        raise ValueError("Use a new isolated proof folder outside the installed studio.")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 5:
        raise ValueError("Use one to five explicit zero-model benchmark trials.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 1800:
        raise ValueError("Use a bounded local benchmark timeout.")
    profile = compilation_identity(context)["profile"]
    if profile["fps"] != 30:
        raise ValueError("The pinned original renderer runs at 30 FPS; compare that same rate.")
    scene = context["scene"]; sid, duration = scene.get("id"), scene.get("dur")
    if (not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sid)
            or isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or not 0 < duration <= 180):
        raise ValueError("Benchmark the exact original scene identity and duration.")
    manifest_path = Path(accepted_manifest).resolve()
    if not manifest_path.is_relative_to(root) or manifest_path.name != "compiler.json":
        raise ValueError("Use an accepted compiler manifest from this installed studio.")
    if (not isinstance(ready, dict) or not _HASH.fullmatch(str(ready.get("identity", "")))
            or (reference_identity is not None and not _HASH.fullmatch(str(reference_identity)))):
        raise ValueError("Preserve the exact pinned setup and original reference identities.")
    manifest = json.loads(manifest_path.read_text())
    key = renderer._digest({"input": context, "assets": assets, "compiler": renderer.COMPILER_VERSION,
        "setup": ready["identity"], "original_references": reference_identity})
    result = renderer._compiler_result(json.dumps(manifest.get("result")), context, assets)
    source_hash = hashlib.sha256(result["source"].encode()).hexdigest()
    if (manifest.get("key") != key or manifest_path.parent.name != key or manifest.get("typed") is not True
            or manifest.get("source_sha256") != source_hash
            or not (manifest_path.parent / "scene.py").is_file()
            or hashlib.sha256((manifest_path.parent / "scene.py").read_bytes()).hexdigest() != source_hash):
        raise ValueError("The benchmark source is not this exact accepted scene/input/setup cache.")
    for asset in assets.values():
        path = Path(asset["path"]).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError("A verified original scene asset changed before the benchmark.")
    if assets_root is not None:
        assets_root = Path(assets_root).resolve()
        if not assets_root.is_relative_to(root) or not assets_root.is_dir():
            raise ValueError("Use this studio's existing local scene media folder.")
    original = root / "threed/render.py"
    if not original.is_file() or ready.get("ready") is not True:
        raise ValueError("Use the verified pinned native setup and original local renderer.")
    if original_python is None:
        original_python = ready["python"]
    if not Path(original_python).is_file():
        raise ValueError("Use an existing Python runtime for the original local renderer.")
    proof_dir.mkdir(parents=True, mode=0o700)
    source = proof_dir / "accepted-scene.py"
    renderer._write(source, result["source"], text=True)
    renderer._typecheck(ready["python"], source)
    renderer._write(proof_dir / "accepted-manifest.json", manifest)
    renderer._write(proof_dir / "original-context.json", context)
    video = {**context["video"], "cast": context["cast"], "scenes": [scene]}
    renderer._write(proof_dir / "video.json", video)
    rows = []
    evidence = {"profile": profile, "compiler_key": key, "context_sha256": _digest(context),
        "source_sha256": source_hash, "assets_sha256": _digest(assets), "reference_identity": reference_identity,
        "setup_identity": ready["identity"], "accepted_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "original_renderer_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "original_python": str(original_python), "fast_python": str(ready["python"]),
        "original_forced_exact_input": True, "model_calls": 0, "original_workflow_mutated": False,
        "scope": profile["speed_scope"], "visual_acceptance": False, "trials": rows}
    renderer._write(proof_dir / "benchmark.json", evidence)
    for trial in range(repeats):
        outputs = {}
        # Alternate order to reduce the chance that one renderer always benefits
        # from running second. Each renderer still starts a fresh local process.
        for backend in (("original", "fast") if trial % 2 == 0 else ("fast", "original")):
            directory = proof_dir / f"trial-{trial + 1}" / backend
            directory.mkdir(parents=True, mode=0o700)
            started = time.perf_counter()
            original_qa = None
            if backend == "original":
                command = [str(original_python), str(original), str(proof_dir / "video.json"), str(directory),
                    "--w", str(profile["width"]), "--h", str(profile["height"]), "--quality", profile["quality"], "--force", "--crf", "16"]
                if assets_root is not None:
                    command += ["--assets", str(assets_root)]
                with (directory / "process.log").open("w") as log:
                    completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                        timeout=timeout, env=renderer._environment(), cwd=str(proof_dir))
                elapsed = time.perf_counter() - started
                try:
                    report = json.loads((directory / "report.json").read_text())
                    original_qa = report["scenes"][0]
                    complete = (len(report["scenes"]) == 1 and original_qa["id"] == sid
                        and original_qa["status"] in {"ok", "rendered_with_issues"}
                        and completed.returncode == (2 if original_qa["status"] == "rendered_with_issues" else 0)
                        and report["w"] == profile["width"] and report["h"] == profile["height"]
                        and report["fps"] == profile["fps"] and report["quality"] == profile["quality"])
                except (OSError, ValueError, KeyError, TypeError, IndexError):
                    complete = False
                if not complete:
                    raise RuntimeError("The isolated original renderer failed; no benchmark multiplier was accepted.")
            else:
                response = renderer._invoke(ready["python"], {"source": str(source), "class": result["scene_class"],
                    "output": str(directory / (sid + ".mp4")), "still_dir": str(directory), "id": sid,
                    "width": profile["width"], "height": profile["height"], "fps": profile["fps"],
                    "duration": duration, "times": [duration * .5], **render_kwargs(profile)}, directory, timeout=timeout)
                elapsed = time.perf_counter() - started
                renderer._validate_output(response, directory / (sid + ".mp4"), duration,
                    profile["fps"], profile["width"], profile["height"])
            if not math.isfinite(elapsed) or elapsed <= 0:
                raise RuntimeError("The local benchmark clock did not produce a valid elapsed render time.")
            outputs[backend] = {"wall_seconds": elapsed, "output": _probe_video(directory / (sid + ".mp4"),
                profile["width"], profile["height"], profile["fps"], duration, environment=renderer._environment())}
            if original_qa is not None:
                # Original exit2 can mean a completed movie whose camera/frame
                # QA failed. Keep that evidence; timing never accepts its look.
                outputs[backend]["original_qa"] = original_qa
        rows.append(outputs)
        renderer._write(proof_dir / "benchmark.json", evidence)
    original_times = [row["original"]["wall_seconds"] for row in rows]
    fast_times = [row["fast"]["wall_seconds"] for row in rows]
    evidence.update(first_run={"original_seconds": original_times[0], "fast_seconds": fast_times[0]},
        repeated_process_median={"original_seconds": statistics.median(original_times[1:]),
            "fast_seconds": statistics.median(fast_times[1:])} if repeats > 1 else None,
        median_render_multiplier=statistics.median(original_times) / statistics.median(fast_times),
        result="complete", claim="Local render wall time including process startup; model and compilation time excluded.")
    renderer._write(proof_dir / "benchmark.json", evidence)
    return evidence
