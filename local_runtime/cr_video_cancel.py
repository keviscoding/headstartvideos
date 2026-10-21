"""Stop exactly one owned local video worker while retaining its saved work."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time

RUN_ID = re.compile(r"video_[a-f0-9]{24}")
PROJECT_ID = re.compile(r"crv_[a-f0-9]{24}")
CANCEL_MESSAGE = "Cancelled on this computer. Saved media and caches are kept. Review stopped Claude work in Setup before resuming; nothing restarts automatically."


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(path, data):
    import cr_claude
    cr_claude._private_write(path, data)


def new_run_id():
    return "video_" + secrets.token_hex(12)


def worker_files(home, run_id):
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("Use a saved local video operation.")
    root = Path(home) / "output" / "cr-video-workers" / run_id
    return root, root / "spec.json", root / "progress.json"


def guard_parent(home, project_id, run_id):
    """A closed studio must not leave its isolated video spending unattended."""
    _, spec_path, _ = worker_files(home, run_id)
    record = _read(spec_path)
    parent = record.get("parent_pid")
    if (record.get("project_id") != project_id or not isinstance(parent, int)
            or isinstance(parent, bool) or parent <= 1 or parent == os.getpid()):
        raise RuntimeError("This video has no live owning studio.")
    if os.name != "nt":
        if os.getppid() != parent or os.getpgrp() != os.getpid():
            raise RuntimeError("Start this video through its owning studio.")
        alive = lambda: os.getppid() == parent
        stop = lambda: os.killpg(os.getpid(), signal.SIGKILL)
    else:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel.OpenProcess(0x00100000, False, parent)  # SYNCHRONIZE
        if not handle:
            raise RuntimeError("This video has no live owning studio.")
        alive = lambda: kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        stop = lambda: subprocess.run(["taskkill", "/PID", str(os.getpid()), "/T", "/F"],
                                       capture_output=True, timeout=10)

    def watch():
        while alive():
            time.sleep(0.5)
        # Said in this video's worker.log, so a closed Studio is never mistaken for a crash.
        print(time.strftime("%Y-%m-%d %H:%M:%S") + " The Studio that started this video closed, so the video "
              "stopped with it (its Claude and media steps too). Saved work is kept.", file=sys.stderr, flush=True)
        stop()

    threading.Thread(target=watch, daemon=True).start()


def run_original_worker(home, project_id, run_id, studio):
    """Invoke the untouched original app function, mirroring only its status."""
    _, spec_path, progress = worker_files(home, run_id)
    record = _read(spec_path)
    if record.get("project_id") != project_id or not PROJECT_ID.fullmatch(project_id):
        raise RuntimeError("This video worker does not match its owned project.")
    spec = record.get("spec")
    if not isinstance(spec, dict) or spec.get("cr_video_run_id") != run_id or spec.get("timeline"):
        raise RuntimeError("Use an owned ordinary video operation.")
    slug = spec.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[\w.\-]+", slug):
        raise RuntimeError("This video has no valid saved job.")
    studio.RUNNING["slug"] = slug
    studio.JOBS[slug] = {"lines": [], "state": "running", "done": False}
    stopped = threading.Event()

    def save():
        # Preserve the original job fields and logs; do not invent progress.
        state = dict(studio.JOBS.get(slug) or {})
        state["lines"] = list(state.get("lines", []))[-500:]
        _write(progress, {"project_id": project_id, "run_id": run_id, "job": state})

    def follow():
        while not stopped.wait(0.5):
            try:
                save()
            except OSError:
                pass            # a busy progress file shows on the next tick; it never stops the video

    watcher = threading.Thread(target=follow, daemon=True)
    watcher.start()
    try:
        import cr_attempts
        with cr_attempts.bind(studio.mv, spec):
            studio._run_spec(spec)
    finally:
        stopped.set()
        watcher.join(2)
        for attempt in range(20):           # the finished state must reach Studio, even past a busy file
            try:
                save()
                break
            except OSError:
                if attempt == 19:
                    raise
                time.sleep(0.5)


class VideoWorkers:
    def __init__(self, studio, home, *, popen=None, stop=None):
        self.studio, self.home = studio, Path(home)
        self.popen = popen or subprocess.Popen
        self.stop_process = stop or self._stop_process
        self.lock = threading.RLock()
        self.current = None

    def supports(self, spec):
        return (not spec.get("timeline") and RUN_ID.fullmatch(str(spec.get("cr_video_run_id", ""))) is not None
                and (self.home / "app.py").is_file())

    def info(self, slug):
        with self.lock:
            current = self.current
            if current and current["slug"] == slug:
                return {"run_id": current["run_id"], "can_cancel": not current["cancel_requested"],
                        "cancel_requested": current["cancel_requested"]}
        with self.studio.LOCK:
            for spec in self.studio.QUEUE:
                if spec.get("slug") == slug and self.supports(spec):
                    return {"run_id": spec["cr_video_run_id"], "can_cancel": True, "cancel_requested": False}
        return {"can_cancel": False}

    def run(self, spec, project_id):
        if not self.supports(spec) or not PROJECT_ID.fullmatch(str(project_id)):
            raise RuntimeError("Use an owned saved local video.")
        run_id, slug = spec["cr_video_run_id"], spec["slug"]
        folder, spec_path, progress = worker_files(self.home, run_id)
        folder.mkdir(parents=True, exist_ok=True)
        _write(spec_path, {"project_id": project_id, "parent_pid": os.getpid(), "spec": spec})
        state = self.studio.JOBS.setdefault(slug, {})
        state.update(state="running", done=False, error=None, title=spec["title"], started=time.time(),
                     steps=spec.get("steps", []), minutes=spec.get("minutes", 10), style=spec.get("style", ""))
        command = [sys.executable, str(self.home / "cr_worker.py"), "--project", project_id,
                   "--", str(self.home / "app.py"), "--cr-video", run_id]
        options = {"cwd": str(self.home), "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        log_path = folder / "worker.log"
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(log_fd, "ab") as log:
            with self.lock:
                try:
                    process = self.popen(command, stdout=log, stderr=log, **options)
                except OSError:
                    state.update(state="error", done=True, error="The local video worker could not start. Saved work is kept.")
                    state.setdefault("lines", []).append(state["error"])
                    with self.studio.LOCK:
                        self.studio.HISTORY.append(slug)
                        if self.studio.RUNNING.get("slug") == slug:
                            self.studio.RUNNING["slug"] = None
                    return
                active = {"slug": slug, "run_id": run_id, "process": process, "cancel_requested": False}
                self.current = active
            try:
                while process.poll() is None:
                    self._follow(active, progress, state, project_id)
                    time.sleep(0.25)
                self._follow(active, progress, state, project_id)
                with self.lock:
                    cancelled = active["cancel_requested"]
                if cancelled:
                    state.update(state="cancelled", done=True, error=CANCEL_MESSAGE)
                    state.setdefault("lines", []).append(CANCEL_MESSAGE)
                    _write(folder / "cancelled.json", {"project_id": project_id, "run_id": run_id, "slug": slug,
                            "status": "cancelled", "stopped_at": time.time()})
                elif not state.get("done"):
                    state.update(state="error", done=True,
                                 error="The local video worker stopped. Saved work is kept; review its status in Setup before resuming.")
                    state.setdefault("lines", []).append(state["error"])
            finally:
                # Never free the account/project reservation while a worker lives.
                if process.poll() is not None:
                    with self.lock:
                        if self.current is active:
                            self.current = None
                    with self.studio.LOCK:
                        self.studio.HISTORY.append(slug)
                        if self.studio.RUNNING.get("slug") == slug:
                            self.studio.RUNNING["slug"] = None

    def _follow(self, active, progress, state, project_id):
        record = _read(progress)
        if record.get("run_id") != active["run_id"] or record.get("project_id") != project_id:
            return
        job = record.get("job")
        if isinstance(job, dict):
            # Cancellation controls stay in this parent's private registry.
            state.update({key: value for key, value in job.items() if key in {
                "lines", "state", "done", "error", "title", "started", "minutes", "steps", "style", "threed", "mux_t0"}})

    @staticmethod
    def _stop_process(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            # No arbitrary PID is accepted: this is the Popen object owned here.
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                    capture_output=True, timeout=10, check=False)
            if result.returncode and process.poll() is None:
                raise OSError("The owned process tree did not stop.")
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                # Killing only the leader could leave a billed CLI/media child.
                # Keep the reservation instead of pretending the tree stopped.
                raise
            process.wait(timeout=5)
        if os.name != "nt":
            # The group leader may stop before an encoder/CLI which ignores
            # SIGTERM. Its private group still belongs to this operation.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def cancel(self, slug, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError("Refresh this video's progress before cancelling.")
        with self.lock:
            active = self.current
            if active and active["slug"] == slug and active["run_id"] == run_id:
                if active["process"].poll() is not None:
                    return {"cancelled": False, "message": "This worker has already stopped."}
                if active["cancel_requested"]:
                    return {"cancelled": True, "message": "Cancellation is already in progress."}
                active["cancel_requested"] = True
                try:
                    self.stop_process(active["process"])
                except (OSError, subprocess.SubprocessError):
                    if active["process"].poll() is None:
                        active["cancel_requested"] = False
                        raise RuntimeError("The worker could not be stopped. Your studio remains busy; try Cancel again.") from None
                if active["process"].poll() is None:
                    active["cancel_requested"] = False
                    raise RuntimeError("The worker has not stopped. Your studio remains busy.")
                return {"cancelled": True, "message": CANCEL_MESSAGE}
            with self.studio.LOCK:
                match = next((spec for spec in self.studio.QUEUE if spec.get("slug") == slug
                              and spec.get("cr_video_run_id") == run_id and self.supports(spec)), None)
                if match is not None:
                    self.studio.QUEUE.remove(match)
                    self.studio.JOBS.setdefault(slug, {}).update(state="cancelled", done=True,
                        error="Cancelled before generation started. Saved files are kept.")
                    self.studio.HISTORY.append(slug)
                    folder, _, _ = worker_files(self.home, run_id)
                    _write(folder / "cancelled.json", {"run_id": run_id, "slug": slug, "status": "cancelled",
                            "queued": True, "stopped_at": time.time()})
                    project_id = match.get("cr_project_id")
                    if PROJECT_ID.fullmatch(str(project_id)):
                        # The saved-project queue has already reserved this
                        # project. Record metadata only; reconnection can sync
                        # it without dispatching or authorizing any stage.
                        _write(folder / ".cr-finish-pending.json", {"project_id": project_id,
                            "status": "failed", "artifacts": [], "error_code": "cancelled"})
                    return {"cancelled": True, "message": "Removed this video from the queue. No generation started."}
        folder, _, _ = worker_files(self.home, run_id)
        saved = _read(folder / "cancelled.json")
        if saved.get("run_id") == run_id and saved.get("slug") == slug:
            return {"cancelled": True, "message": "This video is already cancelled."}
        raise ValueError("This is no longer the selected queued or running video. Refresh its progress.")

    def install_routes(self):
        from flask import jsonify, request
        app = self.studio.app

        @app.post("/cr-video/<slug>/cancel")
        def cancel_video(slug):
            try:
                result = self.cancel(slug, (request.get_json(silent=True) or {}).get("run_id"))
            except (ValueError, RuntimeError) as error:
                return jsonify(error=str(error)), 409
            keeper = getattr(self.studio, "_cr_queue", None)
            if keeper is not None:
                keeper.save()                                  # a queued video cancelled is gone from the saved list too
            return jsonify(result)

        @app.after_request
        def cancellation_status(response):
            if response.mimetype != "application/json":
                return response
            if request.path == "/api/queue":
                body = response.get_json()
                if isinstance(body, dict):
                    for job in body.get("jobs", []):
                        job["cr_cancel"] = self.info(job.get("slug"))
                    response.set_data(json.dumps(body))
            elif request.path.startswith("/api/status/") and response.status_code == 200:
                body = response.get_json()
                body["cr_cancel"] = self.info(request.path.rsplit("/", 1)[-1])
                response.set_data(json.dumps(body))
            return response
