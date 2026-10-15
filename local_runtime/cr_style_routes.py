"""Local HTTP controls for an approved original Frontier reference study."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading

import cr_bridge
import cr_claude
import cr_readiness
import cr_presentation
import cr_style_anchor
import cr_style_clone
import cr_style_look
import cr_style_refine

HOME = Path(__file__).resolve().parent


def install(studio, activity, setup_status, *, token, home=None):
    """Use the studio's local token and shared activity reservation.

    Integration: install(studio, activity, setup_status, token=token). All
    creative work lives in a separate worker; this server only previews and
    publishes owned metadata. No account, engine or creative prompt is edited.
    """
    from flask import Response, jsonify, request, send_file
    home = Path(home or HOME).resolve()
    app = studio.app
    # A reservation for another studio task is not evidence this worker lives.
    live_workers = set()

    def local_access(mutation=False):
        if request.host not in {"127.0.0.1:7860", "localhost:7860"}:
            raise PermissionError("Open the studio on localhost.")
        if request.headers.get("Origin") not in {None, "http://127.0.0.1:7860", "http://localhost:7860"}:
            raise PermissionError("Open the studio on localhost.")
        if mutation and not secrets.compare_digest(request.headers.get("X-CR-Local-Token", ""), token):
            raise PermissionError("Refresh your studio page and try again.")

    def data():
        local_access(True)
        if request.content_length and request.content_length > 32768:
            raise ValueError("Keep the reference and style brief under 32 KB.")
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("Send the reference-study form as a JSON object.")
        return value

    def error_response(error):
        if isinstance(error, PermissionError):
            return jsonify(error=str(error)), 403
        if isinstance(error, ValueError):
            return jsonify(error=str(error)), 400
        if isinstance(error, RuntimeError):
            return jsonify(error=str(error)), 409
        # Show the real cause in the launcher window so it can be fixed.
        app.logger.error("Style study request %s failed", request.path, exc_info=error)
        return jsonify(error="The study could not complete this step. Your saved operation is preserved; review setup before trying again."), 500

    def no_store(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def launch_path(operation):
        return cr_style_clone._operation(home, operation) / "launch.json"

    def safe_status(operation):
        with activity.lock:
            result = cr_style_clone.status(home, operation, cr_bridge)
            working = operation in live_workers
            busy = activity.busy()
        if result["state"] == "prepared" and working:
            result.update(state="running", message="Starting your approved study and checking its owned Claude session.")
        elif result["state"] == "running" and not working:
            result.update(state="failed", needs_manual_recovery=True,
                message="This study was interrupted. Review its saved Claude stages in setup; it will not restart automatically.")
        elif result["state"] == "validated":
            result.update(state="pending", reconciliation_pending=True,
                message="Your validated kit is saved. Sync to publish its preset and completion record; no generation will repeat.")
        result["busy"] = busy
        result["can_continue_prepared"] = (result["state"] == "prepared" and not result["busy"]
                                           and not result.get("metadata_only"))
        result["can_refine_saved"] = result["state"] == "done" and result.get("style_ready") is True
        return result

    def native_proof(*, check_allowance=True):
        checks = setup_status().get_json()
        if (cr_bridge.load_settings().get("brain_mode") != "claudecode"
                or not checks.get("readiness", {}).get("ready")):
            raise RuntimeError("Finish native Claude Code setup and model verification before studying references.")
        allowance = checks.get("claude_code", {}).get("allowance", {})
        if check_allowance and allowance.get("blocked"):
            raise RuntimeError(allowance.get("message") or "Wait for your Claude allowance to reset before starting a reference study.")
        return cr_readiness.proof(checks, purpose="creative")

    def start_worker(operation):
        saved = cr_style_clone.status(home, operation, cr_bridge)
        if saved.get("metadata_only"):
            raise RuntimeError("Use Save completed style to finish this saved metadata operation. No new study is required.")
        if saved["state"] != "prepared":
            return safe_status(operation)
        reservation = activity.reserve()
        location = launch_path(operation)
        def work():
            try:
                log = location.with_name("worker.log")
                descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    result = subprocess.run([sys.executable, str(home / "cr_style_clone.py"), "--operation", operation],
                        cwd=home, stdout=handle, stderr=subprocess.STDOUT)
                cr_claude._private_write(location, {"state": "finished", "returncode": result.returncode})
            except BaseException:
                # Details stay local. Prepared means no creative stage started;
                # running/failed must be reviewed rather than blindly rerun.
                cr_claude._private_write(location, {"state": "finished", "returncode": 1})
            finally:
                with activity.lock:
                    live_workers.discard(operation)
                    activity.release(reservation)
        try:
            cr_claude._private_write(location, {"state": "running"})
            live_workers.add(operation)
            threading.Thread(target=work, daemon=True).start()
        except BaseException:
            try:
                cr_claude._private_write(location, {"state": "finished", "returncode": 1})
            finally:
                live_workers.discard(operation)
                activity.release(reservation)
            raise RuntimeError("The local worker could not start. Your approval is saved; finish setup before explicitly continuing it.") from None
        return safe_status(operation)

    @app.get("/cr-style-clone")
    def cr_style_page():
        try:
            local_access()
            template = (home / "cr_style_clone.html").read_text(encoding="utf-8")
            return no_store(Response(template.replace("__LOCAL_TOKEN_JSON__", json.dumps(token)), mimetype="text/html"))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/catalog")
    def cr_style_catalog():
        try:
            local_access()
            studio.styles.load_all()
            studio.styles.apply_to_engine(studio.mv, studio.motion)
            return no_store(jsonify(styles=[{"name": row["name"], "label": row["label"]}
                                          for row in cr_presentation.presentation_catalog(studio.styles.catalogue(), getattr(studio.styles,"STYLES",{}))]))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/preview")
    def cr_style_preview():
        try:
            payload = data()
            return no_store(jsonify(cr_style_clone.preview(home, payload.get("url"))))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/look")
    def cr_style_look_start():
        """Look at the chosen references (measure here, Gemini watches): free for the creator, no Claude."""
        try:
            payload = data()
            return no_store(jsonify(cr_style_look.start(home, payload.get("preview_id"), payload.get("reference_ids"))))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/look/<look_id>")
    def cr_style_look_status(look_id):
        try:
            local_access()
            return no_store(jsonify(cr_style_look.status(home, look_id)))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/look/<look_id>/sheet/<video_id>")
    def cr_style_look_sheet(look_id, video_id):
        try:
            local_access()
            folder = cr_style_look.folder(home, look_id) / "sheets"
            if not isinstance(video_id, str) or not __import__("re").fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                raise ValueError("Choose a reference video.")
            sheet = folder / (video_id + ".jpg")
            if not sheet.is_file() or not sheet.resolve().is_relative_to(folder.resolve()):
                return jsonify(error="No frames were saved for this reference."), 404
            return no_store(send_file(sheet, mimetype="image/jpeg", conditional=False))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/start")
    def cr_style_start():
        try:
            payload = data()
            with activity.lock:
                if activity.busy():
                    raise RuntimeError("Finish the current studio work before starting a reference study.")
                proof = native_proof()
                result = cr_style_clone.prepare(home, payload, proof, cr_bridge)
                return no_store(jsonify(start_worker(result["operation_id"])))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/<operation>/status")
    def cr_style_status(operation):
        try:
            local_access()
            return no_store(jsonify(safe_status(operation)))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/sync")
    def cr_style_sync(operation):
        try:
            data()
            cr_style_clone.sync(home, operation, cr_bridge)
            return no_store(jsonify(safe_status(operation)))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/recover")
    def cr_style_recover(operation):
        try:
            data()
            with activity.lock:
                if activity.busy():
                    raise RuntimeError("Finish the current studio work before saving this completed style.")
                proof = native_proof(check_allowance=False)
                reservation = activity.reserve()
                try:
                    result = cr_style_clone.recover_metadata(home, operation, cr_bridge,
                                                            readiness=lambda: proof)
                finally:
                    activity.release(reservation)
                return no_store(jsonify(safe_status(result["operation_id"])))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/continue")
    def cr_style_continue(operation):
        try:
            payload = data()
            if payload.get("acknowledge_costs") is not True:
                raise ValueError("Approve your saved study and Claude account usage before continuing preparation.")
            with activity.lock:
                if activity.busy():
                    raise RuntimeError("Finish the current studio work before continuing this study.")
                proof = native_proof()
                _, saved = cr_style_clone._saved(home, operation, cr_bridge)
                if saved.get("metadata_only"):
                    raise RuntimeError("Use Save completed style for this metadata operation. A paid study cannot restart it.")
                if saved["state"] != "prepared":
                    raise RuntimeError("This operation already started. Review its saved stages; it cannot be blindly restarted.")
                if any(proof[key] != saved["proof"][key] for key in ("general_model", "visual_model")):
                    raise RuntimeError("Use this study's pinned Claude model profile.")
                return no_store(jsonify(start_worker(operation)))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/refine")
    def cr_style_refine_saved(operation):
        try:
            payload = data()
            if payload.get("acknowledge_costs") is not True:
                raise ValueError("Approve another graphics review and your own Claude account usage before improving this style.")
            with activity.lock:
                if activity.busy():
                    raise RuntimeError("Finish the current studio work before improving saved graphics.")
                proof = native_proof()
                source = safe_status(operation)
                if not source["can_refine_saved"]:
                    raise RuntimeError("Save and reconcile this completed style before improving its graphics.")
                result = cr_style_refine.prepare(home, operation, proof, cr_bridge, acknowledge_costs=True)
                # Preparation is indexed by the owned source. Repeated POSTs
                # return the same operation; stopped/completed work never starts.
                return no_store(jsonify(start_worker(result["operation_id"])))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/<operation>/samples")
    def cr_style_samples(operation):
        try:
            local_access()
            return no_store(jsonify(cr_style_anchor.status(home, operation)))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/samples")
    def cr_style_samples_start(operation):
        """Draw three samples of the learned style on the creator's image account (approved first)."""
        try:
            payload = data()
            if payload.get("acknowledge_costs") is not True:
                raise ValueError("Approve drawing the samples on your image account first.")
            return no_store(jsonify(cr_style_anchor.start(home, operation)))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/samples/choose")
    def cr_style_samples_choose(operation):
        try:
            payload = data()
            return no_store(jsonify(cr_style_anchor.choose(home, operation, payload.get("index"))))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/<operation>/samples/<int:index>")
    def cr_style_sample_image(operation, index):
        try:
            local_access()
            folder = cr_style_clone._operation(home, operation) / "anchors"
            sample = folder / f"sample_{index}.jpg"
            if not sample.is_file() or not sample.resolve().is_relative_to(folder.resolve()):
                return jsonify(error="That sample isn't drawn yet."), 404
            return no_store(send_file(sample, mimetype="image/jpeg", conditional=False))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/<operation>/board")
    def cr_style_board(operation):
        try:
            local_access()
            result = safe_status(operation)
            folder = cr_style_clone._operation(home, operation)
            board = folder / "reflearn/board.jpg"
            if not result.get("style_ready") or not board.is_file() or not board.resolve().is_relative_to(folder.resolve()):
                return jsonify(error="A validated style board is not available yet."), 404
            return no_store(send_file(board, mimetype="image/jpeg", conditional=False))
        except Exception as error:
            return error_response(error)

    return {"page": "/cr-style-clone", "start": "/cr-style-clone/start"}
