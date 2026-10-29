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
import cr_style_edit
import cr_style_look
import cr_style_refine
import cr_style_sample

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
            if operation not in live_workers:
                try:                       # a change Studio stopped working on (it quit mid-change) never stays stuck
                    cr_style_clone.settle_improvement(home, operation, cr_bridge)
                except Exception:          # noqa: BLE001 - status says what it can below
                    pass
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
        # a style written in code has no graphics kit to review: it is improved with Improve this style instead
        result["can_refine_saved"] = (result["state"] == "done" and result.get("style_ready") is True
                                      and not result.get("renderer") and not result.get("coded"))
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
        room = cr_style_clone._room()          # every way a study starts (start, continue, refine): room to check
        if room:
            raise RuntimeError(room)
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
        """One link, or up to three channels or videos to mix into one style (urls): their videos, listed only."""
        try:
            payload = data()
            if isinstance(payload.get("urls"), list):
                return no_store(jsonify(cr_style_clone.preview_many(home, payload["urls"])))
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

    @app.post("/cr-style-clone/look/<look_id>/plan")
    def cr_style_look_plan(look_id):
        """The look's plan with the creator's answers (how its 3D is made can change the recipe): no model call."""
        try:
            payload = data()
            looked = cr_style_look.status(home, look_id)
            if looked.get("state") != "done":
                raise ValueError("Look at the references first.")
            answers = payload.get("answers") or {}
            options = {q["id"]: q.get("options") or [] for q in (looked.get("mapping") or {}).get("questions") or []}
            if (not isinstance(answers, dict) or len(answers) > 12
                    or any(k not in options or v not in options[k] for k, v in answers.items())):
                raise ValueError("Answer the questions from what Studio saw.")
            import cr_style_map
            styles = cr_style_clone._installed_styles(home)
            base = payload.get("base_style")                    # a recipe the creator picked by hand, or none
            base = base if isinstance(base, str) and base in styles else None
            return no_store(jsonify(mapping=cr_style_map.plan_style(looked.get("profile") or {}, styles, base=base,
                                                                   answers=answers)))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/use-pictures")
    def cr_style_use_pictures(operation):
        """An animated style whose renderer fell short draws pictures instead, on the creator's word."""
        try:
            data()
            with activity.lock:
                if activity.busy():
                    raise RuntimeError("Finish the current studio work before changing this style.")
                cr_style_clone.use_pictures(home, operation, cr_bridge)
            return no_store(jsonify(safe_status(operation)))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/use-animation")
    def cr_style_use_animation(operation):
        """Use pictures instead, undone on the creator's word: Claude's animation draws the style again."""
        try:
            data()
            with activity.lock:
                if activity.busy():
                    raise RuntimeError("Finish the current studio work before changing this style.")
                cr_style_clone.use_animation(home, operation, cr_bridge)
            return no_store(jsonify(safe_status(operation)))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/<operation>/renderer-picture/<kind>/<int:index>")
    def cr_style_renderer_picture(operation, kind, index):
        """One of the renderer's test shots or the channel's frames beside them, by index (never by path)."""
        try:
            local_access()
            if kind not in ("shot", "channel"):
                raise ValueError("Choose a test shot or a channel frame.")
            pictures = cr_style_clone.renderer_pictures(home, operation, cr_bridge)["shots" if kind == "shot" else "channel"]
            if not 0 <= index < len(pictures):
                return jsonify(error="No such picture."), 404
            return no_store(send_file(pictures[index], mimetype="image/jpeg", conditional=False))
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

    @app.post("/cr-style-clone/<operation>/graphics")
    def cr_style_graphics(operation):
        """The opt-in graphics study of a style written in code that now draws pictures: the channel's own motion
        graphics and title cards, learned on the creator's request (Claude usage only), saved in a copy of the style."""
        try:
            payload = data()
            if payload.get("acknowledge_costs") is not True:
                raise ValueError("Approve using your own Claude account for studying the channel's graphics first.")
            with activity.lock:
                if activity.busy():
                    raise RuntimeError("Finish the current studio work before studying this style's graphics.")
                proof = native_proof()
                source = safe_status(operation)
                if source["state"] != "done" or not source.get("style_ready"):
                    raise RuntimeError("Save and reconcile this style before studying its graphics.")
                result = cr_style_clone.prepare_graphics(home, operation, proof, cr_bridge)
                # one at a time per style: a repeated POST returns the study already waiting, running or done
                return no_store(jsonify(start_worker(result["operation_id"])))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/improve")
    def cr_style_improve(operation):
        """Improve this style: one change to a coded style's renderer in the creator's words, written by their own
        Claude Code, drawn and checked here, and kept only if they save it."""
        try:
            payload = data()
            if payload.get("acknowledge_costs") is not True:
                raise ValueError("Approve using your own Claude account for this change first.")
            with activity.lock:
                if activity.busy():
                    raise RuntimeError("Finish the current studio work before improving this style.")
                proof = native_proof()
                source = safe_status(operation)
                if source["state"] != "done" or not source.get("style_ready"):
                    raise RuntimeError("Save and reconcile this style before improving it.")
                result = cr_style_clone.prepare_improvement(home, operation, payload.get("request"), proof, cr_bridge)
                # one change at a time per style: a repeated POST returns the change already waiting or running
                return no_store(jsonify(start_worker(result["operation_id"])))
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/<operation>/improvement/<choice>")
    def cr_style_improvement_choice(operation, choice):
        """The creator's choice on a ready change: save it as the style's next renderer version, or keep the old one."""
        try:
            data()
            if choice not in ("save", "keep"):
                raise ValueError("Choose save or keep.")
            result = cr_style_clone.keep_improvement(home, operation, cr_bridge, use_new=choice == "save")
            if choice == "save":
                studio.styles.load_all()
            return no_store(jsonify(result))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/<operation>/improvement-picture/<kind>/<int:index>")
    def cr_style_improvement_picture(operation, kind, index):
        """A change's test shots, the current version's or the new one's, by index (never by path)."""
        try:
            local_access()
            if kind not in ("current", "new"):
                raise ValueError("Choose the current version or the new one.")
            pictures = cr_style_clone.improvement_pictures(home, operation, cr_bridge)[kind]
            if not 0 <= index < len(pictures):
                return jsonify(error="No such picture."), 404
            return no_store(send_file(pictures[index], mimetype="image/jpeg", conditional=False))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/<operation>/improvement-video/<kind>")
    def cr_style_improvement_video(operation, kind):
        try:
            local_access()
            if kind not in ("current", "new"):
                raise ValueError("Choose the current version or the new one.")
            video = cr_style_clone.improvement_pictures(home, operation, cr_bridge)[kind + "_video"]
            if video is None:
                return jsonify(error="No video was drawn for this version."), 404
            return no_store(send_file(video, mimetype="video/mp4", conditional=True))
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

    @app.post("/cr-style-clone/<operation>/character")
    def cr_style_character(operation):
        """The creator's own host or mascot for a learned style."""
        try:
            local_access()
            upload = request.files.get("file")
            if upload is None:
                raise ValueError("Choose a picture of your character.")
            return no_store(jsonify(cr_style_anchor.upload_character(home, operation, upload.read())))
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

    @app.get("/cr-style-clone/<operation>/likeness/<name>")
    def cr_style_likeness_image(operation, name):
        try:
            local_access()
            import re as _re
            if not _re.fullmatch(r"r\d_\d\.jpg|character\.png", name or ""):
                return jsonify(error="No such test picture."), 404
            folder = cr_style_clone._operation(home, operation) / "likeness"
            picture = folder / name
            if not picture.is_file() or not picture.resolve().is_relative_to(folder.resolve()):
                return jsonify(error="That test picture isn't drawn yet."), 404
            return no_store(send_file(picture, mimetype="image/png" if name.endswith(".png") else "image/jpeg",
                                      conditional=False))
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

    def reload_styles():
        try:
            studio.styles.load_all()
            studio.styles.apply_to_engine(studio.mv, studio.motion)
        except Exception:                                  # noqa: BLE001 - the file is saved; Studio reads it on start
            app.logger.exception("Styles could not be reloaded after an edit")

    @app.get("/cr-style-clone/style/<name>")
    def cr_style_editable(name):
        """A learned style's name, description, creative direction and Create's starting options, to edit."""
        try:
            local_access()
            return no_store(jsonify(cr_style_edit.editable(home, name)))
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/style/<name>")
    def cr_style_edit_save(name):
        """Save the creator's changes to a learned style (the version before is kept). No model or paid call."""
        try:
            payload = data()
            result = cr_style_edit.save(home, name, payload.get("changes"))
            reload_styles()
            return no_store(jsonify(result))
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except Exception as error:
            return error_response(error)

    @app.post("/cr-style-clone/sample")
    def cr_style_try_sample():
        """A few seconds of an installed style on the creator's topic (two pictures, a line of voice, a slow move
        over each): about $0.05 on their own accounts, only with their approval. A suggestion, never a gate."""
        try:
            payload = data()
            if payload.get("acknowledge_costs") is not True:
                raise ValueError("Approve the sample's cost on your own accounts first.")
            if not cr_bridge.load_settings().get("costs_accepted"):
                raise ValueError("Review your provider spending in setup before drawing a sample.")
            name = str(payload.get("style") or "")
            style = (getattr(studio.styles, "STYLES", {}) or {}).get(name)
            if not isinstance(style, dict):
                style = cr_style_clone._read(home / "styles" / (name + ".json")) if name.replace("-", "").replace(
                    "_", "").isalnum() else {}
            if not style:
                raise ValueError("Choose an installed style.")
            look = style.get("look") if isinstance(style.get("look"), dict) else {}
            import cr_media
            # drawn only on the creator's own image account through Studio's media receipts (quoted, never paid
            # twice); the engine's own image path tries other wallets without them, so it never draws a sample
            if not cr_media.active("images"):
                raise ValueError(cr_style_sample.NEEDS_IMAGES)

            def generate(prompt, dest, label, ref_urls):
                return cr_media.image(prompt, dest, label, ref_urls=ref_urls, model=look.get("image_model") or None)
            return no_store(jsonify(cr_style_sample.start(home, name, style, payload.get("topic"), payload.get("line"),
                                                          generate=generate, host=studio.mv.host_image,
                                                          voice=cr_style_sample.kie_voice)))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/sample/<ident>/status")
    def cr_style_sample_status(ident):
        try:
            local_access()
            return no_store(jsonify(cr_style_sample.status(home, ident)))
        except Exception as error:
            return error_response(error)

    @app.get("/cr-style-clone/sample/<ident>/video.mp4")
    def cr_style_sample_video(ident):
        try:
            local_access()
            video = cr_style_sample.folder(home, ident) / "sample.mp4"
            if not video.is_file():
                return jsonify(error="That sample isn't ready yet."), 404
            response = send_file(video, mimetype="video/mp4", conditional=True)
            response.headers["Cache-Control"] = "private, no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception as error:
            return error_response(error)

    return {"page": "/cr-style-clone", "start": "/cr-style-clone/start"}
