"""Explicit setup and safe status for the optional, separately installed renderer."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import cr_claude
import cr_manimgx

_check_cache = {}


def supported_python():
    env = cr_manimgx._environment()
    env["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "false"
    for setting in ("PYLAUNCHER_ALLOW_INSTALL", "PYLAUNCHER_ALWAYS_INSTALL", "PYLAUNCHER_DRYRUN"):
        env.pop(setting, None)

    def supported(candidate):
        try:
            check = subprocess.run([candidate, "-c", "import sys;print(int(sys.version_info >= (3,13)))"],
                capture_output=True, text=True, timeout=10, env=env)
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
                    capture_output=True, text=True, timeout=10, env=env)
            except (OSError, subprocess.TimeoutExpired):
                continue
            candidate = found.stdout.strip()
            if (found.returncode == 0 and candidate and "\n" not in candidate and "\r" not in candidate
                    and candidate not in seen):
                seen.add(candidate)
                if supported(candidate):
                    return candidate
    raise RuntimeError("ManimGX needs Python 3.13 or newer. Install it, reopen this studio, then run this check again. Your standard renderer is still available.")


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
            report("installing", "Preparing a separate Python environment for ManimGX.")
            subprocess.run([supported_python(), "-m", "venv", str(directory / "venv")], check=True,
                stdout=log, stderr=subprocess.STDOUT, timeout=180, env=env)
        report("installing", "Installing ManimGX and its code checker. This does not use your model accounts.")
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
            raise RuntimeError("ManimGX's documentation changed upstream. This version cannot be enabled until its integration is updated.")
        path.write_bytes(response.content)
    report("rendering", "Rendering a short local 3D check and verifying the exported video.")
    cr_manimgx.prepare(home, python, docs, timeout=90)
    result = ready(home, refresh=True)
    if not result["ready"]:
        raise RuntimeError(result["message"])
    report("ready", "ManimGX is ready. You can enable it on a new 3D project.")


def install(studio, activity, *, home):
    from flask import jsonify, request
    home = Path(home)
    state_path = home / ".cr-manimgx/setup-status.json"
    worker = {"thread": None}

    def report(state, message):
        cr_claude._private_write(state_path, {"state": state, "message": message, "updated_at": time.time()})

    @studio.app.get("/cr-renderer")
    def renderer_status():
        with activity.lock:
            try:
                state = json.loads(state_path.read_text())
            except (OSError, ValueError):
                state = {"state": "not_installed", "message": "Optional. The standard renderer is ready to use with your normal setup."}
            running = worker["thread"] is not None and worker["thread"].is_alive()
            if state.get("state") in {"installing", "checking", "rendering"} and not running:
                state = {"state": "stopped", "message": "The renderer check stopped. Run it again to finish setup."}
        result = ready(home)
        progress = cr_manimgx.progress_status(home) if hasattr(cr_manimgx, "progress_status") else {}
        return jsonify(ready=result["ready"], version=result["version"], setup=state,
                       message=result.get("message", ""), progress=progress, busy=activity.busy())

    @studio.app.post("/cr-renderer")
    def renderer_setup():
        data = request.get_json(silent=True) or {}
        if data.get("action") != "install":
            return jsonify(error="Choose Install and check ManimGX."), 400
        with activity.lock:
            if activity.busy():
                return jsonify(error="Finish the current studio work before setting up ManimGX."), 409
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
