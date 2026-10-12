"""Explicit native-Claude refinement of an already saved reference style.

No reference discovery, download, fresh design or alternate brain is involved.
The original browser check, repair, visual critique, rescore and type-drop rules
operate on a new owned copy, keeping the accepted source style immutable.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import secrets
import shutil
import time

import cr_style_clone as clone


def validation_completed(folder, saved):
    """Fresh refinement proof, distinct from its copied source checkpoint."""
    folder = Path(folder)
    checked = clone._read(folder / "refinement-validation.json")
    if (saved.get("refinement_only") is not True or checked.get("schema") != 1
            or checked.get("browser") is not True or len(checked.get("kept") or []) < 3
            or checked.get("refinement_version") != saved.get("refinement_version")
            or not clone._metadata_checkpoint_ready(folder, saved)):
        return False
    record = clone._read(folder / "validated-checkpoint.json").get("record") or {}
    try:
        return checked.get("assets") == clone._validated_assets(folder, record)
    except (RuntimeError, OSError):
        return False


def _reference_files(home, source, saved, record, bridge):
    """Find original downloaded evidence, including an owned recovery ancestor."""
    expected = (record.get("reference") or {}).get("id")
    approved = {row["id"] for row in saved["spec"]["references"]}
    if expected not in approved:
        raise RuntimeError("The saved style does not identify an approved reference.")
    seen = set()
    candidate, operation = source, saved
    for _ in range(16):
        if operation["operation_id"] in seen:
            break
        seen.add(operation["operation_id"])
        evidence = clone._read(candidate / "reflearn/reference-evidence.json")
        if evidence.get("actual_frames") is True and evidence.get("id") == expected:
            work = candidate / "reflearn"
            paths = sorted([*work.glob("ref_sheet*.jpg"), *work.glob("ref_long*.jpg"),
                            *work.glob("ref_frames/*.jpg"), *work.glob("ref_frames/*.png")])
            if any(path.name.startswith("ref_sheet") for path in paths):
                paths += [path for name in ("spec.json", "reference-evidence.json", "measures.json")
                          if (path := work / name).is_file()]
                if not (work / "spec.json").is_file():
                    raise RuntimeError("The original reference style specification is missing. Restore its saved files before refining.")
                result = {}
                for path in paths:
                    if not path.resolve().is_relative_to(candidate.resolve()):
                        raise RuntimeError("Keep reference evidence inside its owned study operation.")
                    result[path.relative_to(candidate).as_posix()] = (path, clone._file_hash(path))
                return result
        ancestor = operation.get("recovery_of") or operation.get("refinement_of")
        if not ancestor:
            break
        candidate, operation = clone._saved(home, ancestor, bridge)
    raise RuntimeError("The original downloaded reference frames and sheets are missing. Restore the saved study before refining; no new download or study was started.")


def prepare(home, source_operation, proof, bridge, *, acknowledge_costs=True):
    """Prepare one immutable refinement; actual creative work starts separately."""
    if acknowledge_costs is not True:
        raise ValueError("Approve this refinement and your own Claude account usage before starting.")
    source, original = clone._saved(home, source_operation, bridge)
    proof = clone._proof(bridge, proof)
    owner = clone._owner(bridge)
    if any(proof[key] != original["proof"][key] for key in ("general_model", "visual_model")):
        raise RuntimeError("Keep this style's pinned Claude model profile for refinement.")
    with clone._lock(clone._folder(home), ".prepare.lock"), clone._lock(source):
        index = clone._folder(home) / ".refinements" / (source_operation + ".json")
        existing = clone._read(index)
        if existing.get("operation_id"):
            result = clone.status(home, existing["operation_id"], bridge)
            _, child = clone._saved(home, existing["operation_id"], bridge)
            if child.get("refinement_only") is not True or child.get("refinement_of") != source_operation:
                raise RuntimeError("The saved refinement does not belong to this style.")
            return result
        if (original.get("state") not in {"complete", "complete_local"}
                or not clone._published(home, original)
                or not clone._metadata_checkpoint_ready(source, original)):
            raise RuntimeError("Save and reconcile the validated original style before refining it.")
        spec = copy.deepcopy(original["spec"])
        base = Path(home) / "styles" / (spec["base_style"] + ".json")
        if clone._file_hash(base) != spec["base_sha256"]:
            raise RuntimeError("The original base preset changed. Preserve the style and review the change before refining.")
        cloud = bridge.project_progress(original["project_id"])
        if (not isinstance(cloud, dict) or (cloud.get("project") or {}).get("status") != "done"
                or any(stage.get("status") != "complete" for stage in cloud.get("stages", []))):
            raise RuntimeError("Reconcile the source style's completed project first. Active or uncertain Claude stages cannot be repeated.")
        design = clone._read(source / "design-checkpoint.json")["design"]
        record = clone._read(source / "validated-checkpoint.json")["record"]
        reference_files = _reference_files(home, source, original, record, bridge)
        assets = clone._validated_assets(source, record)
        bridge.prepare_runtime(proof)
        operation_id = "op_" + secrets.token_hex(12)
        folder = clone._operation(home, operation_id)
        folder.mkdir(parents=True, mode=0o700)
        spec["style_id"] = "cr_clone_" + operation_id[3:15]
        if (Path(home) / "styles" / (spec["style_id"] + ".json")).exists():
            raise RuntimeError("The refinement's unique style name already exists. Preserve the saved styles.")
        spec["max_calls"] = min(spec["max_calls"], 8)
        files = {name: (source / name, digest) for name, digest in assets.items()}
        files.update(reference_files)
        copied = {}
        for name, (path, digest) in files.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, target)
            target.chmod(0o600)
            if clone._file_hash(target) != digest:
                raise RuntimeError("Saved reference evidence changed while copying. Preserve the operation before starting.")
            copied[name] = digest
        record = copy.deepcopy(record)
        # Original kit_of() caches by kit name. A separate revision needs its
        # own registration identity so source/refined styles coexist correctly.
        # This changes manifest metadata only; Claude owns every visual edit.
        manifest_path = folder / "genkit/kit.json"
        manifest = clone._read(manifest_path)
        source_kit = manifest["name"]
        manifest["name"] = "gen_cr_refine_" + operation_id[3:15]
        clone._write(manifest_path, manifest)
        record["kit"] = manifest["name"]
        copied["genkit/kit.json"] = clone._file_hash(manifest_path)
        record["board"] = str(folder / "reflearn/board.jpg")
        version = int(original.get("refinement_version") or 0) + 1
        saved = {"operation_id": operation_id, "owner": owner, "spec": spec, "proof": proof,
            "identity": clone._hash({"source": source_operation, "source_style_sha256": original["style_sha256"],
                                     "owner": owner, "spec": spec}),
            "state": "prepared", "phase": "prepared", "refinement_only": True,
            "refinement_of": source_operation, "source_style": original["spec"]["style_id"],
            "source_style_sha256": original["style_sha256"], "refinement_version": version,
            "begin_id": "b_" + secrets.token_hex(16), "created_at": time.time()}
        clone._write(folder / "design-checkpoint.json", {"schema": 1, "design": design, "spec_sha256": clone._hash(spec)})
        clone._write(folder / "validated-checkpoint.json", {"schema": 1, "record": record,
            "spec_sha256": clone._hash(spec), "assets": clone._validated_assets(folder, record)})
        clone._write(folder / "refinement-source.json", {"schema": 1, "source_operation": source_operation,
            "source_style_sha256": original["style_sha256"], "source_kit": source_kit,
            "source_assets": assets, "files": copied})
        clone._write(folder / "operation.json", saved)
        clone._write(index, {"operation_id": operation_id})
    return clone.status(home, operation_id, bridge)


def run_original(engine, folder, saved, progress, *, learner=None, designer=None):
    """Run the complete original quality loop on the approved private copy."""
    import cr_claude
    runtime = cr_claude._runtime
    if (saved.get("refinement_only") is not True or saved.get("state") != "running"
            or runtime is None or runtime.bridge.current_project().get("id") != saved.get("project_id")
            or getattr(engine, "SCRIPT_PROVIDER", None) != "claudecode"
            or getattr(engine, "CLAUDE_PROVIDER", None) != "claudecode"):
        raise RuntimeError("Start this owned refinement through the verified native Claude Code studio.")
    if learner is None:
        import reflearn as learner
    if designer is None:
        import autostyle as designer
    folder = Path(folder)
    if not clone._metadata_checkpoint_ready(folder, saved):
        raise RuntimeError("The approved saved kit changed before refinement. Review the preserved operation.")
    evidence = clone._read(folder / "refinement-source.json")
    if evidence.get("source_operation") != saved.get("refinement_of"):
        raise RuntimeError("The refinement's owned source evidence is missing.")
    for name, digest in (evidence.get("files") or {}).items():
        path = folder / name
        if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file() or clone._file_hash(path) != digest:
            raise RuntimeError("Approved reference files changed before refinement. Review this operation without repeating its study.")
    design = clone._read(folder / "design-checkpoint.json")["design"]
    record = clone._read(folder / "validated-checkpoint.json")["record"]
    spec = saved["spec"]
    work, kit = folder / learner.WORK, folder / "genkit"
    sheets = sorted(work.glob("ref_sheet*.jpg"))
    reference_frames = sorted([*work.glob("ref_frames/*.jpg"), *work.glob("ref_frames/*.png")])
    actual = clone._read(work / "reference-evidence.json")
    if not sheets or actual.get("actual_frames") is not True or actual.get("id") != (record.get("reference") or {}).get("id"):
        raise RuntimeError("Actual saved reference frames are required for original visual review.")
    def log(message):
        descriptor = os.open(folder / "study.log", os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
    engine.set_extra(spec["brief"])
    clock = learner.Clock(spec["budget_minutes"], log)
    progress("validate")
    clock.step("validate")
    with clone._creative_budget(spec["budget_minutes"]):
        # Keep the original deterministic stand-in. This consent authorizes
        # Claude review/repair, without new portrait/media-provider requests.
        media = learner._media(engine, design["genome"].get("analysis") or {}, folder, work, fetch=False)
        checked = learner.validate(engine, kit, media, clock, work, str(sheets[0]))
    kept = checked.get("kept") or []
    if checked.get("browser") is not True or checked.get("bad") == ["*"] or len(kept) < 3:
        raise clone.StudyStopped("The refinement did not pass the original browser validation. Its source style is still saved; no default graphics were substituted.")
    clock.end()
    report = clock.report()
    learned_spec = clone._read(work / "spec.json")
    reference = next((row for row in spec["references"] if row["id"] == actual["id"]), None)
    if not reference or not learned_spec:
        raise RuntimeError("The original reference specification is missing. Preserve the completed review before saving.")
    board = learner.board(work / "board.jpg", spec["title"], learned_spec, reference, checked,
                          [str(path) for path in reference_frames] or [str(path) for path in sheets], report)
    manifest = clone._read(kit / "kit.json")
    record.update(kit=manifest.get("name"), types=list(manifest.get("types") or {}),
        board=str(board), browser=True, critique=checked.get("critique"),
        problems=(checked.get("problems") or [])[:12], report=report)
    clone._write(folder / "refinement-validation.json", {"schema": 1, "browser": True,
        "kept": kept, "bad": checked.get("bad") or [], "critique": checked.get("critique"),
        "report": report, "refinement_version": saved["refinement_version"],
        "assets": clone._validated_assets(folder, record)})
    clone._write(folder / "validated-checkpoint.json", {"schema": 1, "record": record,
        "spec_sha256": clone._hash(spec), "assets": clone._validated_assets(folder, record)})
    style = clone.finish_original(engine, folder, spec, design, record, progress,
        learner=learner, designer=designer, log=log, report=report)
    style["cr_style_clone"].update(refinement_of=saved["refinement_of"],
        source_style=saved["source_style"], refinement_version=saved["refinement_version"])
    clone._write(folder / "ready-style.json", style)
    return style
