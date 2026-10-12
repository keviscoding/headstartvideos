"""Explicit local validation of one completed response stopped by an adapter bug.

The caller must supply an authenticated, ownership-checked CR progress response.
Inspection and local validation never contact CR or Claude. A separate explicit
correction uses the original verified native route exactly once. No action
mutates the historical response/journal or runs automatically during rendering.
"""
from __future__ import annotations

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
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value) if text else json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        temporary.chmod(0o600)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise RuntimeError("Saved compiler work appeared during validation. It will not be overwritten.") from None
    finally:
        temporary.unlink(missing_ok=True)


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
    if journal != {"key": legacy_key, "state": "stopped", "calls": 1}:
        raise RuntimeError("This is not a single completed compiler response stopped by a known validation-only adapter failure.")
    work = directory / ".recovery-coverage-text" if kind == "coverage_text" else directory
    if work.is_symlink():
        raise RuntimeError("The saved recovery path was redirected; no source was accepted.")
    manifest_path = old / "compiler.json"
    recovered_manifest = None
    if _present(manifest_path):
        if not _regular(manifest_path):
            raise RuntimeError("An existing compiler manifest is not a regular file. It will not be overwritten.")
        recovered_manifest = _read(manifest_path)
    if ((recovered_manifest is not None and (kind != "coverage_text" or recovered_manifest.get("recovery_kind") != "coverage_text"
            or renderer._read(work / "recovery.json").get("state") != "validated"))
            or (old / "compiler-response-2.txt").exists()):
        raise RuntimeError("This compiler request has other acceptance or correction work; use its original recovery controls.")
    reference = old / "original-reference.txt"
    if reference.read_text(encoding="utf-8") != refs.reference_text(bundle):
        raise RuntimeError("The original source references changed; no response was accepted.")
    raw_path = old / "compiler-response-1.txt"
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
    result = renderer._compiler_result(raw, context, assets)
    if kind == "coverage_text":
        descriptions = result["coverage"].values()
        if (not any(len(value.strip()) < 8 for value in descriptions)
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
                      "source_sha256": hashlib.sha256(result["source"].encode()).hexdigest()}}
    if kind == "coverage_text":
        _existing_source(directory / "scene.py", plan["proof"]["source_sha256"])
    if recovered_manifest is not None:
        receipt = renderer._read(work / "recovery.json")
        if (receipt.get("proof") != plan["proof"] or recovered_manifest.get("key") != key
                or recovered_manifest.get("typed") is not True or recovered_manifest.get("result") != result
                or recovered_manifest.get("source_sha256") != plan["proof"]["source_sha256"]
                or recovered_manifest.get("recovery_of") != legacy_key
                or recovered_manifest.get("recovery_receipt") != str((work / "recovery.json").relative_to(directory))
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
            selector = renderer._digest({"project_id": project_id, "preview": str(report_path.parent.relative_to(job)),
                                         "scene_id": scene["id"], "legacy_key": plan["legacy_key"]})
            found.append({"selection_id": selector, "project_id": project_id, "scene_id": scene["id"],
                          "title": str((cloud_progress.get("project") or {}).get("title") or project_id),
                          "state": saved.get("state", "available"), "outdir": str(report_path.parent),
                          "receipt_path": str(receipt_path), "key": plan["key"], "kind": plan["kind"],
                          "diagnostic": saved.get("diagnostic", ""),
                          "message": "The saved source needs one explicit Claude API correction." if saved.get("state") == "source_invalid"
                              else "Local validation stopped. Review its saved diagnostic; no Claude correction is available." if saved.get("state") == "stopped"
                              else "The completed response passed local validation. Resume through the original video controls." if saved.get("state") in {"validated", "corrected"}
                              else "Validate the completed response locally. This generates no media and uses no model calls.",
                          "can_validate": not saved, "can_correct": plan["kind"] == "asset_records" and saved.get("state") == "source_invalid"
                              and not (plan["directory"] / "correction-attempts.json").exists()})
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
        if (saved.get("state") == "validated" and saved.get("proof") == proof
                and manifest.get("key") == plan["key"] and manifest.get("typed") is True
                and manifest.get("recovery_kind") == plan["kind"]
                and manifest.get("recovery_of") == plan["legacy_key"]
                and manifest.get("recovery_receipt") == str((work / "recovery.json").relative_to(directory))
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
    renderer._write(candidate, plan["result"]["source"], text=True)
    try:
        renderer._typecheck(plan["ready"]["python"], candidate)
    except BaseException as error:
        diagnostic_path = candidate.with_suffix(".validation.log")
        record.update(state="source_invalid" if plan["kind"] == "asset_records" and isinstance(error, renderer.ManimGXSourceAPIError) else "stopped",
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
                        "recovery_kind": plan["kind"], "recovery_receipt": str((work / "recovery.json").relative_to(directory))})
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
    if plan["kind"] != "asset_records":
        raise RuntimeError("This validation-only recovery does not authorize a Claude source correction.")
    directory = plan["directory"]
    receipt = _read(directory / "recovery.json")
    saved_input = _read(directory / "recovery-input.json")
    saved_result = _read(directory / "recovery-result.json")
    diagnostic = directory / "scene-recovered.validation.log"
    if (receipt.get("state") != "source_invalid" or receipt.get("proof") != plan["proof"]
            or receipt.get("error_type") != "ManimGXSourceAPIError"
            or receipt.get("checker_version") != renderer.TYPECHECKER_VERSION
            or not diagnostic.is_file() or receipt.get("diagnostic_sha256") != _hash(diagnostic)
            or saved_result != plan["result"] or saved_input.get("proof") != plan["proof"]
            or saved_input.get("context") != plan["context"] or saved_input.get("assets") != plan["assets"]
            or saved_input.get("references") != plan["references"]
            or _hash(directory / "scene-recovered.py") != plan["proof"]["source_sha256"]):
        raise RuntimeError("This stopped source no longer matches its completed receipt and pinned validation diagnostic.")
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
        result, _ = renderer.correct_recovered_source(engine, plan["context"], plan["assets"], plan["ready"],
                    directory, plan["key"], reference_bundle=plan["reference_bundle"])
        corrected_hash = hashlib.sha256(result["source"].encode()).hexdigest()
        receipt.update(state="corrected", corrected_source_sha256=corrected_hash, corrected_at=time.time())
        renderer._write(directory / "recovery.json", receipt)
        return {"accepted": True, "corrected": True, "source_sha256": corrected_hash,
                "project_id": project_id, "scene_id": scene_id, "key": plan["key"]}
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
