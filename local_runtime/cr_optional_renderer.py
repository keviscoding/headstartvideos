"""Explicit setup and safe status for the optional, separately installed renderer."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import cr_claude
import cr_bridge
import cr_manimgx
import cr_readiness

_check_cache = {}


def supported_python():
    env = cr_manimgx._environment()
    env["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "false"
    for setting in ("PYLAUNCHER_ALLOW_INSTALL", "PYLAUNCHER_ALWAYS_INSTALL", "PYLAUNCHER_DRYRUN"):
        env.pop(setting, None)

    def supported(candidate):
        try:
            check = subprocess.run([candidate, "-c", "import sys;print(int(sys.version_info >= (3,13)))"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, env=env)
            return check.returncode == 0 and check.stdout.strip() == "1"
        except (OSError, subprocess.TimeoutExpired):
            return False

    candidates = [sys.executable] + [shutil.which(name) for name in ("python3.14", "python3.13", "python3", "python")]
    seen = set()
    for candidate in (p for p in candidates if p):
        if candidate not in seen:
            seen.add(candidate)
            if supported(candidate):
                return candidate
    # A normal side-by-side Windows install may be registered with py while
    # PATH and the studio's existing environment still point to Python 3.12.
    launcher = shutil.which("py") if os.name == "nt" else None
    if launcher:
        for version in ("-3.14", "-3.13", "-3"):
            try:
                found = subprocess.run([launcher, version, "-c", "import sys;print(sys.executable)"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, env=env)
            except (OSError, subprocess.TimeoutExpired):
                continue
            candidate = found.stdout.strip()
            if (found.returncode == 0 and candidate and "\n" not in candidate and "\r" not in candidate
                    and candidate not in seen):
                seen.add(candidate)
                if supported(candidate):
                    return candidate
    raise RuntimeError("Fast mode needs Python 3.13 or newer. Install it, reopen this studio, then run this check again. Your standard renderer is still available.")


def ready(home, *, refresh=False):
    proof = Path(home) / ".cr-manimgx/ready.json"
    identity = (str(home), proof.stat().st_mtime_ns if proof.is_file() else 0)
    cached = _check_cache.get(identity)
    if not refresh and cached and cached[0] > time.monotonic():
        return cached[1]
    result = cr_manimgx.readiness(home)
    _check_cache.clear()
    _check_cache[identity] = (time.monotonic()+60, result)
    return result


def install_dependencies(home, report):
    """Install upstream pins in a private sidecar; no Claude/media calls."""
    import requests
    directory = Path(home) / ".cr-manimgx"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    python = directory / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = cr_manimgx._environment()
    logpath = directory / "setup.log"
    with logpath.open("w", encoding="utf-8") as log:
        logpath.chmod(0o600)
        if not python.is_file():
            report("installing", "Preparing a separate Python environment for Fast mode.")
            subprocess.run([supported_python(), "-m", "venv", str(directory / "venv")], check=True,
                stdout=log, stderr=subprocess.STDOUT, timeout=180, env=env)
        report("installing", "Installing Fast mode and its code checker. This does not use your model accounts.")
        subprocess.run([str(python), "-m", "pip", "--disable-pip-version-check", "install", "--index-url", "https://pypi.org/simple", *cr_manimgx.DEPENDENCIES],
            check=True, stdout=log, stderr=subprocess.STDOUT, timeout=600, env=env)
    report("checking", "Checking the exact API documentation Claude will use.")
    docs = directory / "docs"
    docs.mkdir(exist_ok=True)
    for name, (url, expected) in cr_manimgx.DOCS.items():
        path = docs / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        response = requests.get(url, timeout=(10,30), allow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 2_000_000 or hashlib.sha256(response.content).hexdigest() != expected:
            raise RuntimeError("Fast mode's documentation changed upstream. This version cannot be enabled until its integration is updated.")
        path.write_bytes(response.content)
    report("rendering", "Rendering a short local 3D check and verifying the exported video.")
    cr_manimgx.prepare(home, python, docs, timeout=90)
    result = ready(home, refresh=True)
    if not result["ready"]:
        raise RuntimeError(result["message"])
    report("ready", "Fast mode is ready. You can enable it on a new 3D project.")


def _saved_jobs(home):
    """Resolve local IDs from owned project markers, never from browser paths."""
    output = (Path(home) / "output").resolve()
    jobs, duplicates = {}, set()
    for marker in output.rglob("cr-project.json"):
        if marker.is_symlink() or not marker.resolve().is_relative_to(output) or ".cr-trash" in marker.parts:
            continue                                            # a deleted video in Studio's trash is not a project
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(saved, dict):
            continue
        project_id = saved.get("id", "")
        spec = saved.get("job_spec")
        if (not isinstance(project_id, str) or not re.fullmatch(r"crv_[a-f0-9]{24}", project_id)
                or saved.get("brain_mode") != "claudecode"
                or not isinstance(spec, dict) or spec.get("render_backend") != "manimgx"):
            continue
        if project_id in jobs:
            duplicates.add(project_id)
        jobs[project_id] = marker.parent.resolve()
    return {key: value for key, value in jobs.items() if key not in duplicates}


def install(studio, activity, *, home, readiness=None):
    from flask import jsonify, request
    home = Path(home)
    state_path = home / ".cr-manimgx/setup-status.json"
    worker = {"thread": None}
    recovery_worker = {"thread": None}
    recovery_path = home / ".cr-manimgx/recovery-status.json"

    def recovery_status():
        try:
            result = json.loads(recovery_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(result, dict):
            return {}
        if result.get("state") == "running" and not (recovery_worker["thread"] and recovery_worker["thread"].is_alive()):
            return {**result, "state": "stopped", "message": "The saved-scene check stopped. Find saved 3D work again to review its receipt. Nothing will retry automatically."}
        return result

    def owned_progress(project_id):
        cloud = cr_bridge.project_progress(project_id)
        if (not isinstance(cloud, dict) or (cloud.get("project") or {}).get("id") != project_id
                or (cloud.get("project") or {}).get("status") != "failed"
                or cloud.get("brain_mode") != "claudecode"):
            raise RuntimeError("Choose a stopped Fast mode project owned by your connected ChannelRecipe account.")
        return cloud

    def native_proof(*, correction=False, spec=None):
        checks = readiness().get_json() if readiness else {}
        if (cr_bridge.load_settings().get("brain_mode") != "claudecode"
                or not checks.get("readiness", {}).get("ready")
                or not ready(home, refresh=True).get("ready")):
            raise RuntimeError("Finish Claude Code setup, model verification and the Fast mode check before reviewing saved 3D work.")
        allowance = checks.get("claude_code", {}).get("allowance", {})
        if correction and allowance.get("blocked"):
            raise RuntimeError(allowance.get("message") or "Wait for your Claude allowance to reset before correcting saved code.")
        if correction:
            preflight = cr_readiness.preset(studio, checks, spec or {})
            if not preflight.get("ready"):
                raise RuntimeError("Complete the saved video's preset checks before correcting its code: " +
                                   " ".join(preflight.get("blockers") or ["Review Setup & accounts."]))
            return preflight["proof"]
        return cr_readiness.proof(checks, purpose="creative")

    def safe_choice(row):
        # The helper also carries local source/receipt paths and diagnostics.
        # None of those are UI data or accepted back from the browser.
        return {key: row[key] for key in ("selection_id", "project_id", "scene_id", "title", "state",
                                         "can_validate", "can_correct") if key in row}

    def report(state, message):
        cr_claude._private_write(state_path, {"state": state, "message": message, "updated_at": time.time()})

    @studio.app.get("/cr-renderer")
    def renderer_status():
        with activity.lock:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {"state": "not_installed", "message": "Optional. The standard renderer is ready to use with your normal setup."}
            running = worker["thread"] is not None and worker["thread"].is_alive()
            if state.get("state") in {"installing", "checking", "rendering"} and not running:
                state = {"state": "stopped", "message": "The renderer check stopped. Run it again to finish setup."}
        result = ready(home)
        progress = cr_manimgx.progress_status(home) if hasattr(cr_manimgx, "progress_status") else {}
        return jsonify(ready=result["ready"], version=result["version"], setup=state,
                       message=result.get("message", ""), progress=progress, busy=activity.busy(),
                       recovery=recovery_status())

    @studio.app.get("/cr-renderer/recovery")
    def renderer_recovery_choices():
        import cr_manimgx_recovery
        # Only this explicit user action asks CR to confirm saved ownership.
        # The ordinary two-second renderer status poll remains local.
        if activity.busy():
            return jsonify(error="Finish the current studio work before reviewing saved 3D work."), 409
        try:
            native_proof()
        except RuntimeError as error:
            return jsonify(error=str(error)[:500]), 409
        choices, unavailable = [], False
        for project_id, job in _saved_jobs(home).items():
            try:
                cloud = owned_progress(project_id)
                rows = cr_manimgx_recovery.discover(home, job, cloud)
                for row in rows:
                    choices.append({**safe_choice(row), "project_id": project_id,
                                    "title": (cloud.get("project") or {}).get("title") or "Saved video"})
            except Exception:
                unavailable = True
        return jsonify(choices=choices, recovery=recovery_status(), message=(
            "Some saved projects could not be confirmed. Reconnect the ChannelRecipe account that made them, or review setup."
            if unavailable else ""))

    @studio.app.post("/cr-renderer/recovery")
    def renderer_recovery():
        import cr_manimgx_recovery
        data = request.get_json(silent=True)
        allowed = {"action", "project_id", "selection_id", "acknowledge_recovery", "acknowledge_correction"}
        if (request.content_length and request.content_length > 8192 or not isinstance(data, dict)
                or set(data) - allowed or not isinstance(data.get("action"), str)
                or data["action"] not in {"validate", "correct"}
                or not isinstance(data.get("project_id"), str) or not isinstance(data.get("selection_id"), str)
                or not re.fullmatch(r"crv_[a-f0-9]{24}", data["project_id"])
                or not re.fullmatch(r"[a-f0-9]{64}", data["selection_id"])):
            return jsonify(error="Choose saved 3D work from this studio; local paths are not accepted."), 400
        correction = data["action"] == "correct"
        if data.get("acknowledge_correction" if correction else "acknowledge_recovery") is not True:
            return jsonify(error="Explicitly confirm the selected saved-scene action first."), 400
        reservation = None
        try:
            with activity.lock:
                if activity.busy():
                    return jsonify(error="Finish the current studio work before reviewing saved 3D work."), 409
                job = _saved_jobs(home).get(data["project_id"])
                if job is None:
                    raise RuntimeError("This saved project is missing or ambiguous. Find saved 3D work again.")
                cloud = owned_progress(data["project_id"])
                proof = native_proof(correction=correction, spec=cloud.get("job_spec"))
                selected = cr_manimgx_recovery.resolve_selection(home, job, cloud, data["selection_id"])
                if not selected.get("can_correct" if correction else "can_validate"):
                    raise RuntimeError("This saved scene is not ready for that action. Find saved 3D work again.")
                outdir, scene_id = selected["outdir"], selected["scene_id"]
                reservation = activity.reserve()
                state = {"state": "running", "action": data["action"], "project_id": data["project_id"],
                    "selection_id": data["selection_id"], "scene_id": scene_id,
                    "title": (cloud.get("project") or {}).get("title") or "Saved video",
                    "message": ("Claude is making the one approved correction to saved 3D code. The video will stay stopped."
                        if correction else "Checking the completed 3D response locally. No generation or model call is running.")}
                cr_claude._private_write(recovery_path, state)

                def run_recovery_work():
                    try:
                        current = owned_progress(data["project_id"])
                        current_proof = native_proof(correction=correction, spec=current.get("job_spec"))
                        # Re-resolve against fresh evidence after worker startup.
                        current_selection = cr_manimgx_recovery.resolve_selection(home, job, current, data["selection_id"])
                        if (current_selection != selected or current_proof != proof):
                            raise RuntimeError("The saved scene or setup changed. Find saved 3D work again before continuing.")
                        if correction:
                            result = cr_manimgx_recovery.correct(home, data["project_id"], outdir, scene_id, current,
                                acknowledge_correction=True, engine=studio.mv, setup_proof=current_proof)
                        else:
                            result = cr_manimgx_recovery.recover(home, data["project_id"], outdir, scene_id, current,
                                acknowledge_recovery=True)
                        if result.get("accepted") is not True:
                            raise RuntimeError("The saved source was not accepted. Find saved 3D work again to review its status.")
                        final = {**state, "state": "validated", "message": "Saved 3D code passed local validation. The video is still stopped. Open Setup & accounts, show stopped projects and resume this saved project to keep its cached workflow and reviews. Create video starts a separate new attempt."}
                        cr_claude._private_write(recovery_path, final)
                    except BaseException as error:
                        message = (str(error)[:500] if isinstance(error, (RuntimeError, cr_claude.ClaudeStageStopped))
                                   else "This saved-scene action could not finish. Its original work and diagnostic are preserved. Review setup before choosing any further action.")
                        try:
                            latest = cr_manimgx_recovery.resolve_selection(home, job, owned_progress(data["project_id"]), data["selection_id"])
                            stopped_state = "source_invalid" if latest.get("state") == "source_invalid" and latest.get("can_correct") else "failed"
                            if stopped_state == "source_invalid":
                                message += " Choose Find saved 3D work to review the separate, optional Claude correction."
                            elif correction:
                                message += " The one correction is stopped. No further correction will run from this control; review the saved diagnostic."
                        except Exception:
                            stopped_state = "failed"
                        try:
                            cr_claude._private_write(recovery_path, {**state, "state": stopped_state, "message": message})
                        except Exception:
                            studio.app.logger.exception("The saved-scene status could not be written")
                    finally:
                        activity.release(reservation)
                def run_recovery():
                    with studio.app.app_context():
                        run_recovery_work()
                recovery_worker["thread"] = threading.Thread(target=run_recovery, daemon=True)
                recovery_worker["thread"].start()
            return jsonify(started=True, recovery=state)
        except Exception as error:
            recovery_worker["thread"] = None
            if reservation is not None:
                activity.release(reservation)
            if isinstance(error, RuntimeError):
                return jsonify(error=str(error)[:500]), 409
            studio.app.logger.exception("The saved-scene action could not start")
            return jsonify(error="The saved-scene action could not start. Your completed work is preserved."), 503

    @studio.app.post("/cr-renderer")
    def renderer_setup():
        data = request.get_json(silent=True) or {}
        if data.get("action") != "install":
            return jsonify(error="Choose Install and check Fast mode."), 400
        with activity.lock:
            if activity.busy():
                return jsonify(error="Finish the current studio work before setting up Fast mode."), 409
            reservation = activity.reserve()
            def run():
                try:
                    install_dependencies(home, report)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    report("failed", "The separate renderer installation or check failed. Its diagnostic is saved in the studio’s .cr-manimgx/setup.log. Your standard renderer remains available.")
                except Exception as error:
                    report("failed", str(error)[:400])
                finally:
                    activity.release(reservation)
            try:
                report("installing", "Preparing the optional renderer. Keep the studio running.")
                worker["thread"] = threading.Thread(target=run, daemon=True)
                worker["thread"].start()
            except BaseException as error:
                worker["thread"] = None
                activity.release(reservation)
                if isinstance(error, Exception):
                    studio.app.logger.exception("The optional renderer check could not start")
                    return jsonify(error="The renderer check could not start. Check the local studio log, then try again."), 503
                raise
        return jsonify(started=True)
