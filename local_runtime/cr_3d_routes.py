"""Local controls for Frontier's original finished-scene inspection and repair."""
from __future__ import annotations
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading

import cr_bridge
import cr_claude
import cr_readiness
import cr_submission

HOME = Path(__file__).resolve().parent


def read_json(path):
    try:
        value = Path(path).read_text(encoding="utf-8")
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, ValueError):
        return {}


def ui_path(job):
    # Frontier's original activity detector scans direct working files. Keep
    # adapter bookkeeping in a deeper directory so it cannot impersonate a
    # still-running original render.
    return Path(job) / "threed" / ".cr-operations" / ".ui.json"


def read_ui(job):
    path=ui_path(job)
    # Read an earlier stopped operation without modifying its saved evidence.
    # Every new write uses ui_path; a legacy file still follows Frontier's
    # original quiet-period safety check until it naturally becomes old.
    return read_json(path if path.is_file() else Path(job) / ".cr-3d-ui.json")


def log_path(job, operation_id):
    if not isinstance(operation_id,str) or not re.fullmatch(r"op_[a-f0-9]{24}",operation_id):
        raise ValueError("Use a valid explicitly created 3D operation ID.")
    return ui_path(job).parent / "logs" / (operation_id + ".log")


def original_job_active(job):
    """Use Frontier's own read-only cooldown check before a redo starts."""
    import threed
    return threed._job_active(job)


def scene_ids(job):
    return [s["id"] for s in scene_rows(job)]


def scene_rows(job):
    board = read_json(job / "threed" / "board_final.json")
    rows = []
    for moment in board.get("moments", []):
        at = float(moment.get("t0", 0))
        for scene in moment.get("scenes", []):
            if scene.get("id"):
                rows.append({"id": str(scene["id"]), "at": round(at, 3),
                             "words": str(scene.get("beat") or "")[:300]})
            at += float(scene.get("dur", 0))
    return rows


def command(action, job, data):
    """Only original QA/redo arguments, never arbitrary local commands."""
    if action not in {"qa", "redo"}:
        raise ValueError("Choose inspection or repair.")
    ids = data.get("scene_ids") or []
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) > 200:
        raise ValueError("Choose scenes from the saved video.")
    known = set(scene_ids(job))
    if len(ids) != len(set(ids)) or any(i not in known or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", i) for i in ids):
        raise ValueError("Choose scenes from the saved video.")
    args = [action, str(job), *ids]
    if action == "redo":
        note = data.get("note") or ""
        if not isinstance(note, str) or len(note) > 2000 or "\x00" in note:
            raise ValueError("Keep your repair note under 2,000 characters.")
        if not ids:
            flags = read_json(job / "threed" / "qa" / "qa.json").get("flags") or {}
            if data.get("from_qa") is not True or not any(int(f.get("severity", 2)) >= 2 for f in flags.values()):
                raise ValueError("Inspect the finished scenes first, or select scenes to repair.")
            args += ["--from-qa", "--min-severity", "2"]
        if note:
            # Frontier's original CLI applies a note to selected scene IDs.
            if not ids:
                raise ValueError("Select scenes when adding a repair note.")
            args += ["--note", note]
    return args


def safe_status(studio, activity, job):
    marker = read_json(job / "cr-project.json")
    available = bool(marker.get("brain_mode") == "claudecode" and
                     (job / "threed" / "board_final.json").is_file() and
                     (job / "threed" / "lock.json").is_file() and studio.mv.final_video(job).is_file())
    state = read_ui(job)
    if state.get("command"):
        import cr_3d
        recorded = cr_3d.operation_status(job, state["command"], operation_id=state.get("operation_id"))
        state.update(recorded)
        if state.get("state") == "running" and activity.busy():
            state.update(needs_manual_recovery=False,
                         message="Claude is inspecting the finished scenes." if state.get("action") == "qa" else "Claude is repairing and reviewing the selected scenes.")
        if state.get("state") == "running" and not activity.busy():
            state.update(state="failed", needs_manual_recovery=True,
                         message="This action stopped and will not replay automatically. Review its saved project before starting a new action.")
    if state.get("action") == "assemble" and state.get("state") == "running":
        receipt = read_json(job / ".cr-finish.json")
        if receipt.get("project_id") == state.get("operation_project_id"):
            state.update(state="done" if receipt.get("status") == "done" else "failed",
                         needs_reassembly=receipt.get("status") != "done")
            cr_claude._private_write(ui_path(job), state)
        elif read_json(job / ".cr-finish-pending.json").get("project_id") == state.get("operation_project_id"):
            state.update(state="pending", message="Your final video is saved locally. Sync its completion record to finish.")
        elif not activity.busy():
            state.update(state="failed", needs_manual_recovery=True,
                         message="The assembly stopped. Review its saved project before explicitly resuming the cached export.")
    review = read_json(job / "threed" / "qa" / "qa.json")
    flags = [{"id": sid, **{k: f.get(k) for k in ("severity", "note", "fix", "codes")}}
             for sid, f in (review.get("flags") or {}).items() if isinstance(f, dict)]
    return {"available": available, "busy": activity.busy(),
            "state": state.get("state", "idle"), "message": state.get("message", ""),
            "operation_project_id": state.get("operation_project_id"),
            "needs_reassembly": bool(state.get("needs_reassembly")),
            "needs_manual_recovery": bool(state.get("needs_manual_recovery")),
            "flags": flags, "scenes": scene_rows(job),
            "failed_calls": review.get("failed_calls") or [],
            "review": {k: review.get(k) for k in ("written", "shots", "looked_at", "flagged", "no_clip")}}


def install(studio, activity, setup_status):
    from flask import jsonify, request

    def job_for(slug):
        if not studio.SAFE.fullmatch(slug):
            raise ValueError("Choose a saved local video.")
        job = studio._job(slug).resolve()
        if not job.is_relative_to(studio.OUT.resolve()) or not job.is_dir():
            raise ValueError("Choose a saved local video.")
        return job

    @studio.app.get("/cr-3d/<slug>")
    def cr_3d_status(slug):
        try:
            return jsonify(safe_status(studio, activity, job_for(slug)))
        except (ValueError, OSError):
            return jsonify(available=False, busy=activity.busy(), state="idle")

    @studio.app.post("/cr-3d/<slug>")
    def cr_3d_action(slug):
        try:
            job = job_for(slug)
            state = safe_status(studio, activity, job)
            data = request.get_json(silent=True) or {}
            action = data.get("action")
            if action == "sync":
                saved = read_ui(job)
                if saved.get("action") == "assemble":
                    pending = job / ".cr-finish-pending.json"
                    receipt = read_json(pending)
                    if receipt.get("project_id") != saved.get("operation_project_id"):
                        raise ValueError("This assembly has no saved completion receipt to sync.")
                    cr_bridge.finish_project(receipt)
                    pending.replace(job / ".cr-finish.json")
                    saved.update(state="done" if receipt.get("status") == "done" else "failed",
                                 needs_reassembly=receipt.get("status") != "done",
                                 message="Your local final video and completion record are saved.")
                    cr_claude._private_write(ui_path(job), saved)
                    return jsonify(safe_status(studio, activity, job))
                if not saved.get("command"):
                    raise ValueError("No saved scene operation needs reconciliation.")
                import cr_3d
                cr_3d.reconcile_operation(job, saved["command"], cr_bridge, operation_id=saved.get("operation_id"))
                return jsonify(safe_status(studio, activity, job))
            if activity.busy():
                raise ValueError("Finish the current studio work before inspecting or repairing scenes.")
            if not state["available"]:
                raise ValueError("Finish a 3D video with your Claude Code route first.")
            stopped = state["state"] == "failed" or state["needs_manual_recovery"]
            if state["state"] in {"pending", "running"} or stopped and (data.get("acknowledge_new_work") is not True or action not in {"qa", "redo", "assemble"}):
                raise ValueError("Review this stopped operation before explicitly accepting a new action. Its saved work will not replay.")
            if stopped and state.get("operation_project_id"):
                previous = cr_bridge.project_progress(state["operation_project_id"])
                if any(stage["status"] == "authorized" for stage in previous.get("stages", [])):
                    raise ValueError("The saved project has an uncertain active Claude stage. Review that stage before starting more work.")
            cfg = cr_bridge.load_settings()
            if cfg.get("brain_mode") != "claudecode":
                raise ValueError("Use the original Claude Code route for 3D inspection and repair.")
            checks = setup_status().get_json()
            if action in {"qa", "redo"} and checks.get("claude_code", {}).get("allowance", {}).get("blocked"):
                raise ValueError(checks["claude_code"]["allowance"]["message"])
            if not checks["readiness"]["ready"]:
                raise ValueError("Finish Setup & accounts before inspecting or repairing scenes.")
            marker = read_json(job / "cr-project.json")
            source = cr_bridge.project_progress(marker.get("id", ""))
            resume_assembly = action == "assemble" and stopped
            if source["project"]["status"] != "done" and not (resume_assembly and marker.get("id") == state.get("operation_project_id")):
                raise ValueError("Finish or recover this video's original project first.")
            if action == "assemble":
                if not state["needs_reassembly"]:
                    raise ValueError("This video has no repaired scenes waiting for assembly.")
                # Use Frontier's own validation and queue; the saved 3D lock is
                # reused, and the completed source project's marker is retained
                # until the normal dispatcher starts this new owned assembly.
                spec = {**source["job_spec"], "steps": ["video"]}
                proof = cr_readiness.preset(studio, checks, spec)
                if not proof["ready"]:
                    raise ValueError(" ".join(proof["blockers"]))
                cfg["setup_proof"] = proof["proof"]
                cr_bridge.save_settings(cfg)
                if resume_assembly:
                    previous = cr_bridge.project_progress(state["operation_project_id"])
                    if any(s["status"] in {"failed", "authorized"} for s in previous.get("stages", [])):
                        raise ValueError("Review the stopped assembly's Claude stages in Setup & accounts before resuming it.")
                project = cr_bridge.begin_project(spec["title"], spec["style"],
                    state["operation_project_id"] if resume_assembly else None, proof=proof["proof"], job_spec=spec)
                def before_publish():
                    cr_claude._private_write(ui_path(job), {"action": action, "state": "running",
                        "operation_project_id": project["id"], "needs_reassembly": True,
                        "message": "Updating your final video from the repaired scene cache."})
                response = cr_submission.submit(studio, spec, project["id"], before_publish=before_publish)
                if not 200 <= response.status_code < 300:
                    return response
                return jsonify(queued=True, operation_project_id=project["id"])
            args = command(action, job, data)
            if action == "redo" and original_job_active(job):
                raise ValueError("This video was updated recently. Wait a little longer before starting its repair, then try again.")
            operation_id = "op_" + secrets.token_hex(12)
            entry = {"action": action, "command": args, "operation_id": operation_id, "state": "running",
                     "needs_reassembly": state["needs_reassembly"],
                     "message": "Claude is inspecting the finished scenes." if action == "qa" else "Claude is repairing and reviewing the selected scenes."}
            cr_claude._private_write(ui_path(job), entry)
            reservation = activity.reserve()

            def run():
                try:
                    operation_log = log_path(job, operation_id)
                    operation_log.parent.mkdir(parents=True, exist_ok=True)
                    with operation_log.open("w", encoding="utf-8") as log:
                        operation_log.chmod(0o600)
                        result = subprocess.run([sys.executable, str(HOME / "cr_worker.py"), "--project", marker["id"], "--operation", operation_id,
                            "--", str(HOME / "threed.py"), *args], cwd=HOME, stdout=log, stderr=subprocess.STDOUT)
                    import cr_3d
                    recorded = cr_3d.operation_status(job, args, operation_id=operation_id)
                    entry.update(recorded)
                    if result.returncode and entry.get("state") not in {"failed", "pending"}:
                        entry.update(state="failed", needs_manual_recovery=True,
                                     message="The operation stopped. Review its project in Setup & accounts; your saved scenes are kept.")
                    cr_claude._private_write(ui_path(job), entry)
                except BaseException:
                    entry.update(state="failed", needs_manual_recovery=True,
                                 message="The operation stopped. Review it in Setup & accounts; your saved scenes are kept.")
                    cr_claude._private_write(ui_path(job), entry)
                finally:
                    activity.release(reservation)

            try:
                threading.Thread(target=run, daemon=True).start()
            except BaseException:
                activity.release(reservation)
                raise
            return jsonify(queued=True)
        except (Exception, cr_claude.ClaudeStageStopped) as error:
            return jsonify(error=str(error)), 409
