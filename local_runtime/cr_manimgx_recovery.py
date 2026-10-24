"""Explicit local validation of one completed response stopped by an adapter bug.

The caller must supply an authenticated, ownership-checked CR progress response.
Inspection and local validation never contact CR or Claude. A separate explicit
correction uses the original verified native route exactly once. No action
mutates the historical response/journal or runs automatically during rendering.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

import cr_claude
import cr_manimgx as renderer
import cr_manimgx_refs as refs

_PROJECT = re.compile(r"crv_[a-f0-9]{24}")
_HASH = re.compile(r"[a-f0-9]{64}")


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise RuntimeError("A required saved recovery record is missing or unreadable.") from None
    if not isinstance(value, dict):
        raise RuntimeError("A saved recovery record has an invalid shape.")
    return value


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _present(path):
    return path.exists() or path.is_symlink()


def _regular(path):
    return stat.S_ISREG(path.lstat().st_mode)


def _existing_source(path, expected):
    if not _present(path):
        return False
    if not _regular(path) or _hash(path) != expected:
        raise RuntimeError("An existing scene source changed or is not a regular file. It will not be overwritten.")
    return True


def _publish_new(path, value, *, text=False):
    """Atomically publish an absent path without replacing concurrent work."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="." + path.name, suffix=".recovery-tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:     # the hashed text, byte for byte
            handle.write(value) if text else json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        temporary.chmod(0o600)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise RuntimeError("Saved compiler work appeared during validation. It will not be overwritten.") from None
    finally:
        temporary.unlink(missing_ok=True)


def runtime_diagnostic(directory, context, assets, ready, key, reference_bundle):
    """Verify a bounded runtime AttributeError against its exact native source.

    This is read-only. Older releases did not save a structured failure receipt;
    their complete private traceback remains usable evidence, but the receipt
    is materialized only by explicit local validation.
    """
    directory = Path(directory)
    paths = {name: directory / name for name in
             ("compiler.json", "compile-attempts.json", "scene.py", "render_stdout.log")}
    if any(not _present(path) or not _regular(path) for path in paths.values()):
        raise RuntimeError("The saved runtime failure lost its original source, manifest or diagnostic.")
    manifest, journal = _read(paths["compiler.json"]), _read(paths["compile-attempts.json"])
    expected_key = renderer._cache_key(context, assets, ready, reference_bundle)
    result = renderer._compiler_result(json.dumps(manifest.get("result")), context, assets)
    source_hash = hashlib.sha256(result["source"].encode()).hexdigest()
    if (key != expected_key or manifest.get("key") != key or manifest.get("typed") is not True
            or manifest.get("source_sha256") != source_hash or _hash(paths["scene.py"]) != source_hash
            or manifest.get("context") != context or journal.get("key") != key
            or journal.get("state") != "validated" or journal.get("calls") not in (1, 2)):
        raise RuntimeError("This runtime failure no longer matches its accepted native compiler source.")
    raw_path = directory / f"compiler-response-{journal['calls']}.txt"
    if (not _present(raw_path) or not _regular(raw_path)
            or renderer._compiler_result(raw_path.read_text(encoding="utf-8"), context, assets) != result):
        raise RuntimeError("The runtime failure has no matching accepted native response.")
    log = paths["render_stdout.log"]
    if log.stat().st_size > 1000000:
        raise RuntimeError("The saved runtime diagnostic exceeds its bounded review limit.")
    text = log.read_text(encoding="utf-8")
    lines = text.rstrip().splitlines()
    missing = re.fullmatch(r"AttributeError: '([A-Za-z_][A-Za-z_0-9]*)' object has no attribute '([A-Za-z_][A-Za-z_0-9]*)'(?:\..*)?", lines[-1] if lines else "")
    frames = list(re.finditer(r'  File "([^"\n]+)", line (\d+), in [^\n]+\n([^\n]+)', text))
    if not missing or not frames or "Traceback (most recent call last):" not in text:
        raise RuntimeError("This saved failure is not a supported, complete local runtime API diagnostic.")
    final = frames[-1]
    source = paths["scene.py"]
    line_number = int(final[2])
    source_lines = result["source"].splitlines()
    if (Path(final[1]).resolve() != source.resolve() or not 1 <= line_number <= len(source_lines)
            or final[3].strip() != source_lines[line_number - 1].strip()
            or not re.search(r"\." + re.escape(missing[2]) + r"\b", source_lines[line_number - 1])):
        raise RuntimeError("The runtime traceback is not bound to the exact failed source expression.")
    diagnostic = renderer._redacted_diagnostic("\n".join(lines))
    evidence = {"version": 1, "key": key, "source_sha256": source_hash,
                "compiler_manifest_sha256": _hash(paths["compiler.json"]),
                "setup_identity": ready["identity"], "render_log_sha256": _hash(log),
                "error_type": "AttributeError", "diagnostic": diagnostic,
                "diagnostic_sha256": hashlib.sha256(diagnostic.encode()).hexdigest(),
                "source_line": line_number, "object_type": missing[1], "attribute": missing[2]}
    failure_path = directory / "runtime-failure.json"
    if _present(failure_path):
        if not _regular(failure_path):
            raise RuntimeError("The runtime failure receipt is not a regular file.")
        saved = _read(failure_path)
        # Newly written structured errors can use a concise diagnostic. Its
        # source/manifest/log identities and exact exception must still agree.
        for name in ("version", "key", "source_sha256", "compiler_manifest_sha256", "setup_identity",
                     "render_log_sha256", "error_type", "source_line", "object_type", "attribute"):
            if saved.get(name) != evidence[name]:
                raise RuntimeError("The saved runtime failure receipt changed; no source correction is available.")
        if (not isinstance(saved.get("diagnostic"), str) or not saved["diagnostic"].strip()
                or hashlib.sha256(saved["diagnostic"].encode()).hexdigest() != saved.get("diagnostic_sha256")
                or missing[2] not in saved["diagnostic"]):
            raise RuntimeError("The saved runtime failure diagnostic changed.")
        evidence = saved
    return evidence


def _completed_correction_inputs(work, context, assets, proof):
    """Read one paid correction which completed but failed only local typing."""
    names = ("recovery.json", "correction-attempts.json", "compiler-correction-response-1.txt",
             "scene-corrected.py", "scene-corrected.validation.log")
    if any(not _present(work / name) or not _regular(work / name) for name in names):
        raise RuntimeError("The completed correction lost its original saved result or validation diagnostic.")
    receipt, journal = _read(work / "recovery.json"), _read(work / "correction-attempts.json")
    if (receipt.get("state") != "stopped" or receipt.get("model_calls") != 1
            or receipt.get("correction_error_type") != "ManimGXSourceAPIError"
            or receipt.get("proof") != proof or journal.get("state") != "stopped"
            or journal.get("key") != proof["key"] or journal.get("calls") != 1
            or journal.get("recovery_proof") != proof or _present(work / "compiler-corrected.json")
            or _present(work / "runtime-failure.json")):
        raise RuntimeError("This correction is not one completed native result stopped only by local API validation.")
    raw = (work / "compiler-correction-response-1.txt").read_text(encoding="utf-8")
    result = renderer._compiler_result(raw, context, assets)
    if hashlib.sha256(result["source"].encode()).hexdigest() != _hash(work / "scene-corrected.py"):
        raise RuntimeError("The completed correction source changed before local revalidation.")
    return result, {name: _hash(work / name) for name in names}


def _completed_correction_candidate(home, plan, cloud_progress):
    if plan["kind"] != "runtime_api":
        raise RuntimeError("Use the original runtime correction's local validation controls.")
    work = plan["recovery_directory"]
    result, history = _completed_correction_inputs(work, plan["context"], plan["assets"], plan["proof"])
    raw = (work / "compiler-correction-response-1.txt").read_text(encoding="utf-8")
    cfg, local = _read(Path(home) / ".cr-connection.json"), cr_claude.status()
    matches = []
    for stage in cloud_progress.get("stages", []):
        stage_id = stage.get("stage_id")
        if not isinstance(stage_id, str) or not re.fullmatch(r"cc_[a-f0-9]{64}", stage_id):
            continue
        path = Path(home) / "output/cr-claude-cache" / plan["proof"]["project_id"] / (stage_id + ".json")
        if not _present(path) or not _regular(path):
            continue
        native = _read(path)
        stdout = native.get("stdout")
        if not isinstance(stdout, str):
            continue
        try:
            envelope = json.loads(stdout)
        except ValueError:
            continue
        if not (isinstance(envelope, dict) and envelope.get("result") == raw):
            continue
        fingerprint = hashlib.sha256(stdout.encode()).hexdigest()
        if (stage_id == plan["proof"]["native_stage_id"] or stage.get("status") != "complete"
                or native.get("state") != "complete" or native.get("input_fingerprint") != stage_id[3:]
                or fingerprint != native.get("result_fingerprint") or fingerprint != stage.get("result_fingerprint")
                or envelope.get("type") != "result" or envelope.get("subtype") != "success" or envelope.get("is_error") is not False
                or stage.get("model_role") != "visual" or stage.get("selected_model") != cfg.get("visual_model")
                or stage.get("attempts") != 1 or stage.get("error_code")
                or (native.get("account_fingerprint") is not None and native["account_fingerprint"] != local.get("account_fingerprint"))):
            raise RuntimeError("The completed correction no longer matches its exact successful native account/model receipt.")
        matches.append({"stage_id": stage_id, "result_fingerprint": fingerprint, "local_receipt_sha256": _hash(path),
                        "account_fingerprint": local.get("account_fingerprint")})
    if len(matches) != 1:
        raise RuntimeError("The completed correction has no unique successful native receipt; no revalidation is available.")
    return result, {"origin": plan["proof"], "history": history, "native": matches[0]}


def _completed_validation_result(work, context, assets, proof):
    validation = work / ".completed-correction-validation"
    if validation.is_symlink() or not validation.is_dir():
        raise RuntimeError("The completed-correction validation directory changed.")
    result, history = _completed_correction_inputs(work, context, assets, proof)
    record_path, manifest_path, source = validation / "recovery.json", validation / "compiler-corrected.json", validation / "scene.py"
    if any(not _present(path) or not _regular(path) for path in (record_path, manifest_path, source)):
        raise RuntimeError("This completed correction's local validation did not finish.")
    record, manifest = _read(record_path), _read(manifest_path)
    acceptance = record.get("proof", {})
    native = acceptance.get("native", {})
    home = next((parent for parent in work.parents if parent.name == "output"), None)
    stage_id = native.get("stage_id", "")
    if home is None or not re.fullmatch(r"cc_[a-f0-9]{64}", str(stage_id)):
        raise RuntimeError("The revalidated correction lost its native receipt identity.")
    native_path = home / "cr-claude-cache" / proof["project_id"] / (stage_id + ".json")
    source_hash = hashlib.sha256(result["source"].encode()).hexdigest()
    if (record.get("state") != "validated" or record.get("model_calls") != 0
            or acceptance.get("origin") != proof or acceptance.get("history") != history
            or not _present(native_path) or not _regular(native_path) or _hash(native_path) != native.get("local_receipt_sha256")
            or not _HASH.fullmatch(str(native.get("result_fingerprint", "")))
            or manifest.get("key") != proof["key"] or manifest.get("typed") is not True
            or manifest.get("result") != result or manifest.get("source_sha256") != source_hash
            or manifest.get("recovery_proof") != acceptance or _hash(source) != source_hash):
        raise RuntimeError("The revalidated correction no longer matches its unchanged paid result and zero-model receipt.")
    return result, source


def _recover_completed_correction(home, outdir, plan, cloud_progress):
    work = plan["recovery_directory"]
    validation = work / ".completed-correction-validation"
    if _present(validation):
        result, source = _completed_validation_result(work, plan["context"], plan["assets"], plan["proof"])
        return {"accepted": True, "reused": True, "corrected": True, "model_calls": 0,
                **plan["proof"], "source_sha256": _hash(source)}
    result, proof = _completed_correction_candidate(home, plan, cloud_progress)
    try:
        validation.mkdir(mode=0o700)
    except FileExistsError:
        raise RuntimeError("This completed correction is already being validated.") from None
    record = {"state": "validating", "proof": proof, "model_calls": 0, "created_at": time.time()}
    renderer._write(validation / "recovery.json", record)
    candidate = validation / "scene.py"
    renderer._write(candidate, result["source"], text=True)
    try:
        renderer._typecheck(plan["ready"]["python"], candidate)
        fresh = _prepare(home, plan["proof"]["project_id"], outdir, plan["proof"]["scene_id"], cloud_progress)
        current, current_proof = _completed_correction_candidate(home, fresh, cloud_progress)
        if (fresh["proof"] != plan["proof"] or current != result or current_proof != proof
                or not _regular(candidate) or _hash(candidate) != hashlib.sha256(result["source"].encode()).hexdigest()):
            raise RuntimeError("The completed correction changed during local validation.")
        renderer._write(validation / "compiler-corrected.json", {"key": plan["key"], "typed": True,
            "result": result, "source_sha256": _hash(candidate), "recovery_proof": proof})
        record.update(state="validated", checked_at=time.time())
        renderer._write(validation / "recovery.json", record)
    except BaseException as error:
        record.update(state="stopped", diagnostic=renderer._redacted_diagnostic(str(error)), error_type=type(error).__name__)
        renderer._write(validation / "recovery.json", record)
        raise
    return {"accepted": True, "reused": False, "corrected": True, "model_calls": 0,
            **plan["proof"], "source_sha256": _hash(candidate)}


def runtime_override(directory, context, assets, ready, key, reference_bundle):
    """Return only a completed explicit runtime correction, retaining its parent."""
    directory = Path(directory)
    work = directory / ".recovery-runtime-api"
    if not _present(work):
        return None
    if work.is_symlink() or not work.is_dir():
        raise RuntimeError("The runtime recovery directory changed.")
    if _present(work / "runtime-failure.json"):
        raise RuntimeError("The single corrected graphics source also stopped at runtime. Review its saved diagnostic; it will not render again automatically.")
    evidence = runtime_diagnostic(directory, context, assets, ready, key, reference_bundle)
    record = _read(work / "recovery.json")
    proof = record.get("proof", {})
    journal = _read(directory / "compile-attempts.json")
    original_response = directory / f"compiler-response-{journal.get('calls')}.txt"
    reference = directory / "original-reference.txt"
    validated_completion = _present(work / ".completed-correction-validation")
    if ((record.get("state") != "corrected" and not (record.get("state") == "stopped" and validated_completion)) or record.get("model_calls") != 1
            or proof.get("runtime_failure") != evidence or proof.get("key") != key
            or proof.get("source_sha256") != evidence["source_sha256"]
            or proof.get("legacy_journal_sha256") != _hash(directory / "compile-attempts.json")
            or proof.get("response_sha256") != _hash(original_response)
            or not _present(reference) or not _regular(reference)
            or proof.get("reference_sha256") != _hash(reference)
            or reference.read_text(encoding="utf-8") != refs.reference_text(reference_bundle, reference_path=reference)
            or not re.fullmatch(r"cc_[a-f0-9]{64}", str(proof.get("native_stage_id", "")))
            or not _HASH.fullmatch(str(proof.get("native_result_fingerprint", "")))
            or proof.get("context_sha256") != renderer._digest(context)
            or proof.get("assets_sha256") != renderer._digest(assets)
            or proof.get("setup_identity") != ready["identity"]
            or proof.get("reference_identity") != reference_bundle["identity"]):
        raise RuntimeError("This saved runtime source needs its explicit recovery completed before rendering.")
    if validated_completion:
        result, source = _completed_validation_result(work, context, assets, proof)
        if _present(source.parent / "runtime-failure.json"):
            raise RuntimeError("The revalidated correction stopped at runtime; it will not render again automatically.")
        return result, source
    manifest = _read(work / "compiler-corrected.json")
    result = _runtime_corrected_result(directory, work, context, assets, proof, record, manifest)
    return result, work / "scene-corrected.py"


def runtime_acceptance(directory, context, assets, ready, key, reference_bundle):
    """Hash the verified parent and child lineage for deterministic format reuse."""
    accepted = runtime_override(directory, context, assets, ready, key, reference_bundle)
    if accepted is None:
        return None
    directory = Path(directory)
    work = directory / ".recovery-runtime-api"
    manifest_directory = accepted[1].parent
    acceptance = {"kind": "completed_native_runtime_correction", "key": key,
            "source_sha256": _hash(accepted[1]),
            "manifest_sha256": _hash(manifest_directory / "compiler-corrected.json"),
            "receipt_sha256": _hash(work / "recovery.json"),
            "journal_sha256": _hash(work / "correction-attempts.json"),
            "response_sha256": _hash(work / "compiler-correction-response-1.txt"),
            "failure_sha256": _hash(directory / "runtime-failure.json"),
            "parent_manifest_sha256": _hash(directory / "compiler.json"),
            "parent_source_sha256": _hash(directory / "scene.py"),
            "parent_journal_sha256": _hash(directory / "compile-attempts.json")}
    if manifest_directory != work:
        acceptance["completed_validation_sha256"] = _hash(manifest_directory / "recovery.json")
    return acceptance


def _runtime_corrected_result(directory, work, context, assets, proof, receipt, manifest):
    response, source = work / "compiler-correction-response-1.txt", work / "scene-corrected.py"
    journal = _read(work / "correction-attempts.json")
    if any(not _present(p) or not _regular(p) for p in (response, source, work / "compiler-corrected.json")):
        raise RuntimeError("The accepted runtime correction lost its saved result or source.")
    result = renderer._compiler_result(response.read_text(encoding="utf-8"), context, assets)
    source_hash = hashlib.sha256(result["source"].encode()).hexdigest()
    if (receipt.get("state") != "corrected" or receipt.get("model_calls") != 1
            or receipt.get("proof") != proof or journal.get("key") != proof["key"]
            or journal.get("state") != "validated" or journal.get("calls") != 1
            or journal.get("recovery_proof") != proof or manifest.get("key") != proof["key"]
            or manifest.get("typed") is not True or manifest.get("result") != result
            or manifest.get("source_sha256") != source_hash or manifest.get("recovery_proof") != proof
            or receipt.get("corrected_source_sha256") != source_hash
            or receipt.get("corrected_response_sha256") != _hash(response) or _hash(source) != source_hash):
        raise RuntimeError("The accepted runtime correction no longer matches its single native result and receipt.")
    return result


def _corrected_manifest(plan, receipt, manifest):
    """Read a completed explicit correction without weakening its origin proof."""
    work, directory, proof = plan["recovery_directory"], plan["directory"], plan["proof"]
    journal_path = work / "correction-attempts.json"
    response_path = work / "compiler-correction-response-1.txt"
    if (not _present(journal_path) or not _regular(journal_path)
            or not _present(response_path) or not _regular(response_path)):
        raise RuntimeError("The accepted graphics correction lost its saved native result or journal.")
    journal = _read(journal_path)
    result = renderer._compiler_result(response_path.read_text(encoding="utf-8"), plan["context"], plan["assets"])
    source_hash = hashlib.sha256(result["source"].encode()).hexdigest()
    child_manifest_path = work / "compiler-corrected.json"
    if not _present(child_manifest_path) or not _regular(child_manifest_path):
        raise RuntimeError("The accepted graphics correction lost its validated child manifest.")
    child_manifest = _read(child_manifest_path)
    if (receipt.get("state") != "corrected" or receipt.get("proof") != proof
            or journal.get("key") != plan["key"] or journal.get("state") != "validated"
            or journal.get("calls") != 1 or journal.get("recovery_proof") != proof
            or receipt.get("corrected_source_sha256") != source_hash
            or receipt.get("corrected_response_sha256") != _hash(response_path)
            or manifest.get("key") != plan["key"] or manifest.get("typed") is not True
            or manifest.get("result") != result or manifest.get("source_sha256") != source_hash
            or manifest.get("recovery_proof") != proof
            or manifest.get("recovery_of") != plan["legacy_key"]
            or manifest.get("recovery_kind") != plan["kind"]
            or manifest.get("recovery_receipt") != (work / "recovery.json").relative_to(directory).as_posix()
            or child_manifest.get("key") != plan["key"] or child_manifest.get("typed") is not True
            or child_manifest.get("result") != result or child_manifest.get("source_sha256") != source_hash
            or child_manifest.get("recovery_proof") != proof
            or not _existing_source(work / "scene-corrected.py", source_hash)
            or not _existing_source(directory / "scene.py", source_hash)):
        raise RuntimeError("This accepted correction no longer matches its completed native result and recovery receipt.")
    return result


def _manifest_recovery(raw, context, assets):
    """Recognize only JSON serialization failures, never weaken scene checks."""
    try:
        json.loads(raw.strip())
        return None
    except ValueError:
        pass
    try:
        renderer._manifest_json(raw)
    except RuntimeError as error:
        if "not a complete JSON compiler manifest" not in str(error):
            raise
        return {"result": {"saved_response": raw}, "diagnostic": str(error)}
    # Markdown/prose around one complete object now needs no native call, but
    # malformed coverage, unsafe source and unsupported requirements still stop.
    return {"result": renderer._compiler_result(raw, context, assets), "diagnostic": None}


def _prepare(home, project_id, outdir, scene_id, cloud_progress):
    home, outdir = Path(home).resolve(), Path(outdir).resolve()
    if not _PROJECT.fullmatch(str(project_id)) or not outdir.is_relative_to(home / "output"):
        raise RuntimeError("Use an owned saved video inside this studio's output folder.")
    threed = next((p for p in [outdir, *outdir.parents] if p.name == "threed"), None)
    if threed is None or not threed.is_relative_to(home / "output"):
        raise RuntimeError("Use the original saved 3D preview for this video.")
    job = threed.parent
    marker = _read(job / "cr-project.json")
    finish = _read(job / ".cr-finish.json")
    proof = cloud_progress
    if (not isinstance(proof, dict) or proof.get("project_id") != project_id
            or (proof.get("project") or {}).get("id") != project_id
            or (proof.get("project") or {}).get("status") != "failed"
            or proof.get("brain_mode") != "claudecode" or proof.get("purpose") != "video"
            or marker.get("id") != project_id or marker.get("brain_mode") != "claudecode"
            or finish.get("project_id") != project_id or finish.get("status") != "failed"):
        raise RuntimeError("Recovery requires this owned failed native video and its verified cloud receipt.")
    stages = proof.get("stages")
    if (not isinstance(stages, list) or not stages
            or any(not isinstance(s, dict) or s.get("status") != "complete" for s in stages)):
        raise RuntimeError("Finish or review all active and incomplete native stages before local recovery.")
    for spec_path in (home / "output/cr-video-workers").glob("*/spec.json"):
        spec = _read(spec_path)
        if spec.get("project_id") != project_id:
            continue
        progress = renderer._read(spec_path.with_name("progress.json"))
        cancelled = renderer._read(spec_path.with_name("cancelled.json"))
        if (progress.get("job") or {}).get("done") is not True and cancelled.get("status") != "cancelled":
            raise RuntimeError("Stop this video's local worker before recovering its graphics response.")
    release = _read(home / "cr-release.json")
    workflow = proof.get("workflow") or {}
    if (workflow.get("workflow_version") != release.get("workflow_version")
            or workflow.get("engine_source_sha256") != release.get("source_sha256")
            or not workflow.get("engine_source_sha256")):
        raise RuntimeError("This failed video belongs to a different pinned source or workflow.")
    cfg = _read(home / ".cr-connection.json")
    local = cr_claude.status()
    if (cfg.get("brain_mode") != "claudecode" or cfg.get("costs_accepted") is not True
            or not local.get("ready") or not cr_claude.models_verified(cfg, local, root=home)):
        raise RuntimeError("Reconnect the verified native Claude profile before local graphics recovery.")
    ready = renderer.readiness(home, refresh=True)
    spec = proof.get("job_spec") or {}
    saved_spec = marker.get("job_spec") or {}
    if (not ready.get("ready") or spec.get("render_backend") != "manimgx"
            or saved_spec.get("render_backend") != "manimgx"
            or spec.get("manimgx_identity") != ready.get("identity")
            or saved_spec.get("manimgx_identity") != ready.get("identity")):
        raise RuntimeError("Restore this project's exact pinned Fast mode environment before recovery.")
    video, report = _read(outdir / "video.json"), _read(outdir / "report.json")
    scenes = [s for s in video.get("scenes", []) if isinstance(s, dict) and s.get("id") == scene_id]
    if len(scenes) != 1 or report.get("renderer") != "manimgx":
        raise RuntimeError("Use exactly one scene from the saved original Fast mode preview.")
    context = {"scene": scenes[0], "cast": video.get("cast", []),
               "video": {k: v for k, v in video.items() if k not in {"scenes", "cast"}},
               "output": {"width": report.get("w"), "height": report.get("h"),
                          "fps": report.get("fps"), "quality": report.get("quality")}}
    bundle = refs.resolve(home, context)
    assets = renderer._assets(context, outdir)
    legacy_assets = dict(assets)
    for asset in bundle["assets"].values():
        legacy_assets[asset["path"]] = asset["sha256"]
    assets.update(bundle["assets"])
    # Both the normal renderer and this explicit recovery use these full
    # verified records keyed by the resolver's original:relative identifier.
    for asset in assets.values():
        path = Path(asset["path"]).resolve()
        if not path.is_relative_to(home) or not path.is_file() or _hash(path) != asset["sha256"]:
            raise RuntimeError("A verified original scene image changed; no source was accepted.")
    identity = {"input": context, "compiler": renderer.COMPILER_VERSION,
                "setup": ready["identity"], "original_references": bundle["identity"]}
    legacy_key = renderer._digest({**identity, "assets": legacy_assets})
    key = renderer._digest({**identity, "assets": assets})
    directory = threed / ".cr-manimgx" / key
    old = threed / ".cr-manimgx" / legacy_key
    kind = "asset_records"
    if legacy_key == key or not (old / "compiler-response-1.txt").is_file():
        # This second known adapter failure used the correct full asset
        # records. Only its former description-length rule rejected it.
        old, legacy_key, kind = directory, key, "coverage_text"
    cache_root = threed / ".cr-manimgx"
    if directory.resolve().parent != cache_root or old.resolve().parent != cache_root:
        raise RuntimeError("Use this video's original compiler cache, without redirected paths.")
    journal = _read(old / "compile-attempts.json")
    runtime_failure = None
    if (old == directory and journal.get("key") == key and journal.get("state") == "validated"
            and journal.get("calls") in (1, 2)):
        runtime_failure = runtime_diagnostic(directory, context, assets, ready, key, bundle)
        kind = "runtime_api"
    if runtime_failure is None and journal != {"key": legacy_key, "state": "stopped", "calls": 1}:
        raise RuntimeError("This is not a single completed compiler response stopped by a known validation-only adapter failure.")
    manifest_recovery = None
    if kind == "coverage_text":
        manifest_recovery = _manifest_recovery((old / "compiler-response-1.txt").read_text(encoding="utf-8"), context, assets)
        if manifest_recovery is not None:
            kind = "manifest_json"
        elif any(s.get("id") == scene_id and "verified local media paths" in s.get("error", "")
                 for s in report.get("scenes", []) if isinstance(s, dict)):
            pixels_result = renderer._compiler_result((old / "compiler-response-1.txt").read_text(encoding="utf-8"), context, assets)
            tree = ast.parse(pixels_result["source"])
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "ImageMobject" and renderer._local_pixel_array(tree, n,
                       n.args[0] if n.args else next((k.value for k in n.keywords if k.arg == "filename_or_array"), None))
                   for n in ast.walk(tree)):
                kind = "procedural_pixels"
    child_names = {"runtime_api": ".recovery-runtime-api", "coverage_text": ".recovery-coverage-text",
                   "manifest_json": ".recovery-manifest-json", "procedural_pixels": ".recovery-procedural-pixels"}
    work = directory / child_names[kind] if kind in child_names else directory
    if work.is_symlink():
        raise RuntimeError("The saved recovery path was redirected; no source was accepted.")
    manifest_path = old / "compiler.json"
    recovered_manifest = None
    if _present(manifest_path):
        if not _regular(manifest_path):
            raise RuntimeError("An existing compiler manifest is not a regular file. It will not be overwritten.")
        recovered_manifest = _read(manifest_path)
    if kind != "runtime_api" and ((recovered_manifest is not None and (kind not in {"coverage_text", "manifest_json", "procedural_pixels"} or recovered_manifest.get("recovery_kind") != kind
            or renderer._read(work / "recovery.json").get("state") not in {"validated", "corrected"}))
            or (old / "compiler-response-2.txt").exists()):
        raise RuntimeError("This compiler request has other acceptance or correction work; use its original recovery controls.")
    reference = old / "original-reference.txt"
    if reference.read_text(encoding="utf-8") != refs.reference_text(bundle, reference_path=reference):
        raise RuntimeError("The original source references changed; no response was accepted.")
    raw_path = old / f"compiler-response-{journal['calls'] if kind == 'runtime_api' else 1}.txt"
    raw = raw_path.read_text(encoding="utf-8")
    matches = []
    cache_root = home / "output/cr-claude-cache" / project_id
    for path in cache_root.glob("*.json"):
        if _read(path).get("state") != "complete":
            raise RuntimeError("A local native stage is still active or incomplete; review it before recovery.")
    for stage in stages:
        stage_id = stage.get("stage_id", "")
        if not re.fullmatch(r"cc_[a-f0-9]{64}", str(stage_id)):
            continue
        path = cache_root / (stage_id + ".json")
        if not path.is_file():
            continue
        native = _read(path)
        if native.get("state") != "complete":
            raise RuntimeError("A local native stage is still incomplete; review it before recovery.")
        if (native.get("account_fingerprint") is not None
                and native["account_fingerprint"] != local.get("account_fingerprint")):
            raise RuntimeError("Reconnect the Claude account that completed this saved native result.")
        stdout = native.get("stdout")
        if not isinstance(stdout, str):
            continue
        digest = hashlib.sha256(stdout.encode()).hexdigest()
        if (not _HASH.fullmatch(str(stage.get("result_fingerprint", "")))
                or digest != stage["result_fingerprint"] or digest != native.get("result_fingerprint")
                or native.get("input_fingerprint") != stage_id[3:]):
            raise RuntimeError("A completed native result does not match its verified cloud receipt.")
        try:
            envelope = json.loads(stdout)
        except ValueError:
            continue
        if (isinstance(envelope, dict) and envelope.get("type") == "result"
                and envelope.get("subtype") == "success" and envelope.get("is_error") is False
                and envelope.get("result") == raw):
            if (stage.get("model_role") != "visual" or stage.get("selected_model") != cfg.get("visual_model")
                    or stage.get("attempts") != 1 or stage.get("error_code")):
                raise RuntimeError("Restore the exact verified visual model for this completed native stage.")
            matches.append(stage)
    if len(matches) != 1:
        raise RuntimeError("The saved compiler response has no unique matching completed native receipt.")
    result = manifest_recovery["result"] if kind == "manifest_json" else renderer._compiler_result(raw, context, assets)
    if kind == "coverage_text":
        descriptions = result["coverage"].values()
        empty_annotations = renderer._blank_coverage_annotations(context, result["coverage"])
        known_empty_failure = bool(empty_annotations) and any(
            s.get("id") == scene_id and "manifest omitted or changed render requirements" in s.get("error", "")
            for s in report.get("scenes", []) if isinstance(s, dict))
        if not known_empty_failure and (not any(len(value.strip()) < 8 for value in descriptions)
                or any(not isinstance(value, str) or not value.strip() for value in result["coverage"].values())
                or set(result["coverage"]) != set(renderer._pointers(context)) or result["unsupported"]):
            raise RuntimeError("This response was not rejected solely by the former coverage-description length check.")
    plan = {"directory": directory, "recovery_directory": work,
            "kind": kind, "key": key, "legacy_key": legacy_key,
            "ready": ready, "result": result, "context": context, "assets": assets, "reference_bundle": bundle,
            "references": {"identity": bundle["identity"], "reference_file": str(reference.resolve()),
                           "evidence": bundle["context"]},
            "proof": {"project_id": project_id, "scene_id": scene_id, "legacy_key": legacy_key,
                      "key": key, "response_sha256": _hash(raw_path),
                      "legacy_journal_sha256": _hash(old / "compile-attempts.json"),
                      "reference_sha256": _hash(reference), "reference_identity": bundle["identity"],
                      "context_sha256": renderer._digest(context), "assets_sha256": renderer._digest(assets),
                      "setup_identity": ready["identity"], "native_stage_id": matches[0]["stage_id"],
                      "native_result_fingerprint": matches[0]["result_fingerprint"],
                      "native_account_fingerprint": local.get("account_fingerprint"),
                      "source_sha256": hashlib.sha256((result.get("saved_response") if kind == "manifest_json" and "saved_response" in result else result["source"]).encode()).hexdigest()}}
    if kind == "manifest_json" and manifest_recovery["diagnostic"] is not None:
        plan["proof"]["manifest_diagnostic"] = manifest_recovery["diagnostic"]
    if kind == "runtime_api":
        plan["proof"]["runtime_failure"] = runtime_failure
    receipt = renderer._read(work / "recovery.json")
    if kind == "runtime_api":
        if receipt.get("state") == "corrected":
            _runtime_corrected_result(directory, work, context, assets, plan["proof"], receipt,
                                      _read(work / "compiler-corrected.json"))
        return plan
    if kind in {"coverage_text", "manifest_json", "procedural_pixels"} and (recovered_manifest is None or receipt.get("state") != "corrected"):
        if kind == "manifest_json" and "saved_response" in result and _present(directory / "scene.py"):
            raise RuntimeError("Existing scene source will not be overwritten by JSON manifest recovery.")
        _existing_source(directory / "scene.py", plan["proof"]["source_sha256"])
    if recovered_manifest is not None:
        if receipt.get("state") == "corrected":
            _corrected_manifest(plan, receipt, recovered_manifest)
        elif (receipt.get("proof") != plan["proof"] or recovered_manifest.get("key") != key
                or recovered_manifest.get("typed") is not True or recovered_manifest.get("result") != result
                or recovered_manifest.get("source_sha256") != plan["proof"]["source_sha256"]
                or recovered_manifest.get("recovery_of") != legacy_key
                or recovered_manifest.get("recovery_receipt") != (work / "recovery.json").relative_to(directory).as_posix()
                or not (directory / "scene.py").is_file()
                or _hash(directory / "scene.py") != plan["proof"]["source_sha256"]):
            raise RuntimeError("This accepted source no longer matches its original completed response and local recovery receipt.")
    return plan


def inspect(home, project_id, outdir, scene_id, cloud_progress):
    """Verify supplied receipt and immutable inputs without writing or rendering."""
    plan = _prepare(home, project_id, outdir, scene_id, cloud_progress)
    return {**plan["proof"], "kind": plan["kind"], "ready_for_local_typecheck": True, "starts_model": False}


def discover(home, job, cloud_progress):
    """List verified candidates from original reports, without accepting source.

    Returned preview/receipt paths are for the local route only. The route sends
    selection_id and descriptive metadata to the browser, never accepts paths.
    """
    home, job = Path(home).resolve(), Path(job).resolve()
    if not job.is_relative_to(home / "output"):
        raise RuntimeError("Use an owned saved local video.")
    marker = _read(job / "cr-project.json")
    project_id = marker.get("id")
    if not isinstance(cloud_progress, dict) or cloud_progress.get("project_id") != project_id:
        raise RuntimeError("Use the authenticated progress receipt for this local project.")
    found = []
    for report_path in sorted((job / "threed").rglob("report.json"))[:64]:
        report = renderer._read(report_path)
        if report.get("renderer") != "manimgx":
            continue
        for scene in report.get("scenes", []):
            if not isinstance(scene, dict) or scene.get("status") != "error":
                continue
            try:
                plan = _prepare(home, project_id, report_path.parent, scene.get("id"), cloud_progress)
            except (RuntimeError, OSError, ValueError, KeyError, TypeError):
                continue
            receipt_path = plan["recovery_directory"] / "recovery.json"
            saved = renderer._read(receipt_path)
            if saved and saved.get("proof") != plan["proof"]:
                continue
            completed_available = False
            completed_validated = False
            if plan["kind"] == "runtime_api" and saved.get("state") == "stopped":
                validation = plan["recovery_directory"] / ".completed-correction-validation"
                try:
                    if _present(validation):
                        _completed_validation_result(plan["recovery_directory"], plan["context"], plan["assets"], plan["proof"])
                        completed_validated = True
                    else:
                        _completed_correction_candidate(home, plan, cloud_progress)
                        completed_available = True
                except (RuntimeError, OSError, ValueError, KeyError, TypeError):
                    pass
            selector = renderer._digest({"project_id": project_id, "preview": str(report_path.parent.relative_to(job)),
                                         "scene_id": scene["id"], "legacy_key": plan["legacy_key"]})
            found.append({"selection_id": selector, "project_id": project_id, "scene_id": scene["id"],
                          "title": str((cloud_progress.get("project") or {}).get("title") or project_id),
                          "state": "validated" if completed_validated else saved.get("state", "available"), "outdir": str(report_path.parent),
                          "receipt_path": str(receipt_path), "key": plan["key"], "kind": plan["kind"],
                          "diagnostic": saved.get("diagnostic", ""),
                          "message": "The completed correction passed local validation. Resume through the original video controls." if completed_validated
                              else "Claude already completed this correction. Validate its saved source locally; no model call or media generation is needed." if completed_available
                              else "The saved Claude response needs one explicit JSON formatting or completion correction." if saved.get("state") == "source_invalid" and plan["kind"] == "manifest_json" and "saved_response" in plan["result"]
                              else "The saved source needs one explicit Claude API correction." if saved.get("state") == "source_invalid"
                              else "Local validation stopped. Review its saved diagnostic; no Claude correction is available." if saved.get("state") == "stopped"
                              else "The completed response passed local validation. Resume through the original video controls." if saved.get("state") in {"validated", "corrected"}
                              else "Validate the completed response locally. This generates no media and uses no model calls.",
                          "can_validate": not saved or completed_available, "can_correct": plan["kind"] != "procedural_pixels" and saved.get("state") == "source_invalid"
                              and not _present(plan["recovery_directory"] / "correction-attempts.json")
                              and (plan["kind"] not in {"coverage_text", "manifest_json"} or not _present(plan["directory"] / "scene.py"))})
    return found


def resolve_selection(home, job, cloud_progress, selection_id):
    if not isinstance(selection_id, str) or not _HASH.fullmatch(selection_id):
        raise RuntimeError("Choose a saved graphics recovery from this project's list.")
    matches = [row for row in discover(home, job, cloud_progress) if row["selection_id"] == selection_id]
    if len(matches) != 1:
        raise RuntimeError("This graphics recovery changed or is unavailable. Refresh its saved status.")
    return matches[0]


def recover(home, project_id, outdir, scene_id, cloud_progress, *, acknowledge_recovery=False):
    """Accept only after explicit acknowledgement and unchanged local API checks."""
    if acknowledge_recovery is not True:
        raise RuntimeError("Explicitly acknowledge local recovery of this completed response first.")
    plan = _prepare(home, project_id, outdir, scene_id, cloud_progress)
    directory, work, proof = plan["directory"], plan["recovery_directory"], plan["proof"]
    if work.exists():
        saved = renderer._read(work / "recovery.json")
        manifest = renderer._read(directory / "compiler.json")
        if plan["kind"] == "runtime_api" and saved.get("state") == "stopped" and saved.get("proof") == proof:
            return _recover_completed_correction(home, outdir, plan, cloud_progress)
        if plan["kind"] == "runtime_api" and saved.get("state") == "corrected" and saved.get("proof") == proof:
            result = runtime_override(directory, plan["context"], plan["assets"], plan["ready"], plan["key"], plan["reference_bundle"])
            return {"accepted": True, "reused": True, **proof,
                    "source_sha256": saved["corrected_source_sha256"], "corrected": True}
        if plan["kind"] in {"coverage_text", "manifest_json"} and saved.get("state") == "corrected" and saved.get("proof") == proof:
            _corrected_manifest(plan, saved, manifest)
            return {"accepted": True, "reused": True, **proof,
                    "source_sha256": saved["corrected_source_sha256"], "corrected": True}
        if (saved.get("state") == "validated" and saved.get("proof") == proof
                and manifest.get("key") == plan["key"] and manifest.get("typed") is True
                and manifest.get("recovery_kind") == plan["kind"]
                and manifest.get("recovery_of") == plan["legacy_key"]
                and manifest.get("recovery_receipt") == (work / "recovery.json").relative_to(directory).as_posix()
                and manifest.get("source_sha256") == proof["source_sha256"]
                and manifest.get("result") == plan["result"]
                and _hash(directory / "scene.py") == proof["source_sha256"]):
            return {"accepted": True, "reused": True, **proof}
        raise RuntimeError("This recovery destination already started or stopped. It will not execute again automatically.")
    # Atomic creation prevents two explicit invocations from validating or
    # overwriting the same destination at once. Historical files remain intact.
    try:
        work.mkdir(mode=0o700)
    except FileExistsError:
        raise RuntimeError("This graphics recovery is already being reviewed.") from None
    record = {"state": "validating", "proof": proof, "kind": plan["kind"], "created_at": time.time(), "model_calls": 0}
    renderer._write(work / "recovery.json", record)
    renderer._write(work / "compile-attempts.json", {"key": plan["key"], "state": "recovery_started",
                    "calls": 0, "recovery_of": plan["legacy_key"]})
    renderer._write(work / "recovery-input.json", {"context": plan["context"], "assets": plan["assets"],
                    "references": plan["references"], "ready": plan["ready"], "proof": proof})
    renderer._write(work / "recovery-result.json", plan["result"])
    candidate = work / "scene-recovered.py"
    if plan["kind"] == "manifest_json" and "saved_response" in plan["result"]:
        diagnostic = proof["manifest_diagnostic"]
        renderer._write(work / "saved-response.txt", plan["result"]["saved_response"], text=True)
        renderer._write(candidate.with_suffix(".validation.log"), diagnostic, text=True)
        record.update(state="source_invalid", error_type="ManimGXManifestJSONError", diagnostic=diagnostic,
                      diagnostic_sha256=hashlib.sha256(diagnostic.encode()).hexdigest(),
                      checker_version=renderer.TYPECHECKER_VERSION)
        renderer._write(work / "recovery.json", record)
        renderer._write(work / "compile-attempts.json", {"key": plan["key"], "state": "stopped",
                        "calls": 0, "recovery_of": plan["legacy_key"]})
        return {"accepted": False, "source_invalid": True, "starts_model": False, **proof}
    renderer._write(candidate, plan["result"]["source"], text=True)
    if plan["kind"] == "runtime_api":
        failure = proof["runtime_failure"]
        if not _present(directory / "runtime-failure.json"):
            _publish_new(directory / "runtime-failure.json", failure)
        renderer._write(candidate.with_suffix(".validation.log"), failure["diagnostic"], text=True)
        record.update(state="source_invalid", error_type="ManimGXRuntimeAPIError",
                      diagnostic=failure["diagnostic"], diagnostic_sha256=failure["diagnostic_sha256"],
                      checker_version=renderer.TYPECHECKER_VERSION)
        renderer._write(work / "recovery.json", record)
        renderer._write(work / "compile-attempts.json", {"key": plan["key"], "state": "stopped",
                        "calls": 0, "recovery_of": plan["legacy_key"]})
        return {"accepted": False, "source_invalid": True, "starts_model": False, **proof}
    try:
        renderer._typecheck(plan["ready"]["python"], candidate)
    except BaseException as error:
        diagnostic_path = candidate.with_suffix(".validation.log")
        record.update(state="source_invalid" if isinstance(error, renderer.ManimGXSourceAPIError) else "stopped",
                      error_type=type(error).__name__, diagnostic=renderer._redacted_diagnostic(
                          error.diagnostic if isinstance(error, renderer.ManimGXSourceAPIError) else str(error)),
                      diagnostic_sha256=_hash(diagnostic_path) if diagnostic_path.is_file() else None,
                      checker_version=renderer.TYPECHECKER_VERSION)
        renderer._write(work / "recovery.json", record)
        renderer._write(work / "compile-attempts.json", {"key": plan["key"], "state": "stopped",
                        "calls": 0, "recovery_of": plan["legacy_key"]})
        raise RuntimeError("The recovered source did not pass the pinned local API check. Its diagnostic is saved; no model or rendering was started.") from None
    try:
        # Recheck after the local checker returns. Exclusive publication also
        # protects against a file appearing between this guard and the write.
        if _present(directory / "compiler.json"):
            raise RuntimeError("A compiler manifest appeared during validation. It will not be overwritten.")
        source = directory / "scene.py"
        if not _existing_source(source, proof["source_sha256"]):
            _publish_new(source, plan["result"]["source"], text=True)
        _existing_source(source, proof["source_sha256"])
        _publish_new(directory / "compiler.json", {"key": plan["key"], "typed": True,
                        "source_sha256": proof["source_sha256"], "result": plan["result"], "recovery_of": plan["legacy_key"],
                        "recovery_kind": plan["kind"], "recovery_receipt": (work / "recovery.json").relative_to(directory).as_posix()})
    except BaseException as error:
        record.update(state="stopped", error_type=type(error).__name__, diagnostic=renderer._redacted_diagnostic(str(error)))
        renderer._write(work / "recovery.json", record)
        renderer._write(work / "compile-attempts.json", {"key": plan["key"], "state": "stopped",
                        "calls": 0, "recovery_of": plan["legacy_key"]})
        raise
    record.update(state="validated", checked_at=time.time())
    renderer._write(work / "recovery.json", record)
    renderer._write(work / "compile-attempts.json", {"key": plan["key"], "state": "validated",
                    "calls": 0, "recovery_of": plan["legacy_key"]})
    return {"accepted": True, "reused": False, **proof}


def correct(home, project_id, outdir, scene_id, cloud_progress, *, acknowledge_correction=False,
            engine=None, setup_proof=None):
    """Fresh explicit native correction after a completed local API failure.

    The caller holds its creative activity reservation for the entire action.
    The original failed video remains unfinished; this only accepts its source.
    """
    if acknowledge_correction is not True:
        raise RuntimeError("Confirm the single Claude source correction and its account usage first.")
    plan = _prepare(home, project_id, outdir, scene_id, cloud_progress)
    directory, work = plan["directory"], plan["recovery_directory"]
    if plan["kind"] == "procedural_pixels":
        raise RuntimeError("This procedural-pixel recovery only revalidates the saved source locally; it cannot start a model correction.")
    receipt = _read(work / "recovery.json")
    saved_input = _read(work / "recovery-input.json")
    saved_result = _read(work / "recovery-result.json")
    diagnostic = work / "scene-recovered.validation.log"
    manifest_json_error = plan["kind"] == "manifest_json" and "saved_response" in plan["result"]
    saved_source = work / ("saved-response.txt" if manifest_json_error else "scene-recovered.py")
    error_type = "ManimGXManifestJSONError" if manifest_json_error else "ManimGXRuntimeAPIError" if plan["kind"] == "runtime_api" else "ManimGXSourceAPIError"
    if (receipt.get("state") != "source_invalid" or receipt.get("proof") != plan["proof"]
            or receipt.get("error_type") != error_type
            or receipt.get("checker_version") != renderer.TYPECHECKER_VERSION
            or not _present(diagnostic) or not _regular(diagnostic) or receipt.get("diagnostic_sha256") != _hash(diagnostic)
            or saved_result != plan["result"] or saved_input.get("proof") != plan["proof"]
            or saved_input.get("context") != plan["context"] or saved_input.get("assets") != plan["assets"]
            or saved_input.get("references") != plan["references"]
            or not _present(saved_source) or not _regular(saved_source)
            or _hash(saved_source) != plan["proof"]["source_sha256"]):
        raise RuntimeError("This stopped source no longer matches its completed receipt and pinned validation diagnostic.")
    if _present(work / "correction-attempts.json"):
        raise RuntimeError("This saved graphics source correction already started or stopped. It will not execute again automatically.")
    if plan["kind"] in {"coverage_text", "manifest_json"} and (_present(directory / "compiler.json") or _present(directory / "scene.py")):
        raise RuntimeError("Existing compiler work will not be overwritten by this explicit source correction.")
    if (engine is None or getattr(engine, "CLAUDE_PROVIDER", None) != "claudecode"
            or not isinstance(setup_proof, dict) or setup_proof.get("brain_mode") != "claudecode"
            or setup_proof.get("models_verified") is not True or setup_proof.get("costs_accepted") is not True):
        raise RuntimeError("Use the current verified native studio brain for this explicit correction.")
    import cr_bridge
    if Path(cr_bridge.HOME).resolve() != Path(home).resolve():
        raise RuntimeError("Use the same installed studio connection for this saved project.")
    previous = cr_bridge._project
    begin_attempted = False
    try:
        spec = cloud_progress["job_spec"]
        begin_attempted = True
        cr_bridge.begin_project(spec["title"], spec["style"], project_id,
                                proof=setup_proof, job_spec=spec, purpose="video")
        kwargs = {"reference_bundle": plan["reference_bundle"]}
        if plan["kind"] in {"coverage_text", "runtime_api", "manifest_json"}:
            kwargs["recovery_directory"] = work
        result, corrected_source = renderer.correct_recovered_source(engine, plan["context"], plan["assets"], plan["ready"],
                    directory, plan["key"], **kwargs)
        corrected_hash = hashlib.sha256(result["source"].encode()).hexdigest()
        if plan["kind"] in {"coverage_text", "runtime_api", "manifest_json"}:
            # Recheck current ownership, account, setup, assets and the original
            # completed response after the sole native correction returns.
            current = _prepare(home, project_id, outdir, scene_id, cloud_progress)
            if current["proof"] != plan["proof"]:
                raise RuntimeError("The saved graphics inputs changed during correction; no source was accepted.")
            response = work / "compiler-correction-response-1.txt"
            journal = _read(work / "correction-attempts.json")
            child_manifest_path = work / "compiler-corrected.json"
            if not _present(child_manifest_path) or not _regular(child_manifest_path):
                raise RuntimeError("The native correction has no complete validated child manifest.")
            child_manifest = _read(child_manifest_path)
            if (journal.get("key") != plan["key"] or journal.get("state") != "validated"
                    or journal.get("calls") != 1 or journal.get("recovery_proof") != plan["proof"]
                    or not _present(response) or not _regular(response)
                    or renderer._compiler_result(response.read_text(encoding="utf-8"), current["context"], current["assets"]) != result
                    or child_manifest.get("key") != plan["key"] or child_manifest.get("typed") is not True
                    or child_manifest.get("result") != result or child_manifest.get("source_sha256") != corrected_hash
                    or child_manifest.get("recovery_proof") != plan["proof"]
                    or Path(corrected_source).resolve().parent != work.resolve()
                    or not _existing_source(Path(corrected_source), corrected_hash)):
                raise RuntimeError("The native correction has no matching complete, validated source result.")
            renderer._typecheck(current["ready"]["python"], Path(corrected_source))
            if plan["kind"] in {"coverage_text", "manifest_json"}:
                if _present(directory / "compiler.json"):
                    raise RuntimeError("A compiler manifest appeared during correction. It will not be overwritten.")
                if not _existing_source(directory / "scene.py", corrected_hash):
                    _publish_new(directory / "scene.py", result["source"], text=True)
                _existing_source(directory / "scene.py", corrected_hash)
                _publish_new(directory / "compiler.json", {"key": plan["key"], "typed": True,
                    "source_sha256": corrected_hash, "result": result, "recovery_of": plan["legacy_key"],
                    "recovery_kind": plan["kind"], "recovery_receipt": (work / "recovery.json").relative_to(directory).as_posix(),
                    "recovery_proof": plan["proof"]})
            receipt["corrected_response_sha256"] = _hash(response)
        receipt.update(state="corrected", corrected_source_sha256=corrected_hash, corrected_at=time.time(), model_calls=1)
        renderer._write(work / "recovery.json", receipt)
        return {"accepted": True, "corrected": True, "source_sha256": corrected_hash,
                "project_id": project_id, "scene_id": scene_id, "key": plan["key"]}
    except BaseException as error:
        if plan["kind"] in {"coverage_text", "runtime_api", "manifest_json"} and _present(work / "correction-attempts.json"):
            receipt.update(state="stopped", correction_error_type=type(error).__name__,
                           correction_diagnostic=renderer._redacted_diagnostic(str(error)), model_calls=1)
            renderer._write(work / "recovery.json", receipt)
        raise
    finally:
        try:
            if begin_attempted:
                # Only metadata: the video still needs its original visual
                # review and output before it can be marked done.
                threed = next(p for p in [Path(outdir).resolve(), *Path(outdir).resolve().parents] if p.name == "threed")
                pending = threed.parent / ".cr-finish-pending.json"
                payload = {"project_id": project_id, "status": "failed", "artifacts": [], "error_code": "runtime_failed"}
                renderer._write(pending, payload)
                try:
                    cr_bridge.finish_project(payload)
                    pending.replace(pending.with_name(".cr-finish.json"))
                except Exception:
                    pass
        finally:
            # Filesystem and metadata failures must never leave the shared
            # native transport pointed at this correction's saved project.
            cr_bridge._project = previous
