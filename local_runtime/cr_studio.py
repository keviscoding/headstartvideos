"""Run the current Frontier engine with CR's admin bridge and local onboarding."""
from __future__ import annotations
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from functools import wraps
from types import FunctionType, SimpleNamespace

import cr_bridge
import cr_capabilities
import cr_media
import cr_render
import cr_claude
import cr_mcp
import cr_readiness
import cr_workers
import cr_ui
import cr_submission
import hashlib
import re

HOME = Path(__file__).resolve().parent
CR_URL = "https://channelrecipe.com"
STUDIO_URL = "http://127.0.0.1:7860"
# The black launcher window isn't saved anywhere, so keep its recent lines
# for Copy diagnostics (secrets are hidden when they're copied).
import collections
RECENT_OUTPUT = collections.deque(maxlen=400)


class _RecentOutput:
    """Write to the window as usual and remember the last lines."""
    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        try:
            written = self._stream.write(text)
        except Exception:
            written = len(text)
        for line in str(text).splitlines():
            if line.strip():
                RECENT_OUTPUT.append(line[:500])
        return written

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


_SECRET = re.compile(r"(cr_mcp_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{8,}|(?i:(?:key|token|secret|authorization|password)\s*[=:]\s*)\S+"
                     r"|[A-Za-z0-9+/_-]{40,})")


def redact(text):
    return _SECRET.sub("[hidden]", str(text))
CONNECT_SECONDS = 900
_graphics_check = {}


class CreativeActivity:
    """Keep the original editor's asynchronous work on its selected accounts."""
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.active = set()
        self.waiting_editor = False
        self.awake = None

    def reserve(self):
        token = object()
        with self.lock:
            if sys.platform == "darwin" and (self.awake is None or self.awake.poll() is not None):
                try:
                    self.awake = subprocess.Popen(
                        ["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    self.awake = None
            self.active.add(token)
        return token

    def release(self, token):
        awake = None
        with self.lock:
            self.active.discard(token)
            if not self.active:
                awake, self.awake = self.awake, None
            self.changed.notify_all()
        if awake is not None:
            try:
                awake.terminate()
                awake.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    awake.kill()
                    awake.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def wait_for_editor(self):
        """A queued title must not replace a still-running editor's project."""
        with self.changed:
            self.waiting_editor = True
            try:
                while self.active or self.editor_queue_busy():
                    self.changed.wait(timeout=0.25)
            finally:
                self.waiting_editor = False

    def rendering(self, job):
        # The original dispatcher reserves RUNNING before entering run_spec.
        # Editor tasks for that job must still drain before its next render.
        return not self.waiting_editor and self.studio._tl_busy(Path(job).name) == "running"

    def editor_queue_busy(self):
        timeline = getattr(self.studio, "timeline", None)
        condition = getattr(timeline, "_QCOND", None)
        if condition is None:
            return False
        with condition:
            return bool(timeline._QUEUE or timeline._WORKER.get("current"))

    def busy(self):
        with self.lock:
            if self.active or self.studio.RUNNING.get("slug") or self.studio.QUEUE or cr_workers.busy():
                return True
            if self.editor_queue_busy():
                return True
            product = getattr(self.studio, "PRODUCT_BUILD", {}).get("proc")
            if product is not None and product.poll() is None:
                return True
            farm = getattr(self.studio, "FARM", {}).get("thread")
            return bool(farm is not None and farm.is_alive())

    def install(self):
        timeline = getattr(self.studio, "timeline", None)
        if timeline is None or not all(hasattr(timeline, name) for name in ("chat", "_chat_reply", "_run_task")):
            return
        original_chat, original_reply, original_task = timeline.chat, timeline._chat_reply, timeline._run_task

        def stopped(job):
            # A stopped authorization must not trigger an automatic final render.
            with timeline._lock(job):
                state = timeline._state(job)
                state["auto_render"] = False
                timeline._save_state(job, state)

        def reply(job, slug, message, selection, mid, token=None):
            token = token if token is not None else self.reserve()
            try:
                return original_reply(job, slug, message, selection, mid)
            except (cr_claude.ClaudeStageStopped, cr_media.AccountStop) as error:
                stopped(job)
                timeline._replace_msg(job, mid, str(error)[:440] +
                    " Sending another editor message starts new work; it does not retry this saved stage.", [], error=True)
            finally:
                self.release(token)

        @wraps(original_chat)
        def chat(job, slug, message, sel, after_render=False):
            # Reserve before the source spawns its thread, including the interval
            # between returning the HTTP response and the thread's first stage.
            token = self.reserve()
            bound_reply = lambda *args: reply(*args, token=token)
            dispatch = FunctionType(original_chat.__code__, {**original_chat.__globals__, "_chat_reply": bound_reply},
                                    original_chat.__name__, original_chat.__defaults__, original_chat.__closure__)
            try:
                result = dispatch(job, slug, message, sel, after_render=after_render)
                if not result.get("ok") or result.get("queued"):
                    self.release(token)
                return result
            except BaseException:
                self.release(token)
                raise

        @wraps(original_task)
        def task(job, tid):
            token = self.reserve()
            try:
                return original_task(job, tid)
            except (cr_claude.ClaudeStageStopped, cr_media.AccountStop) as error:
                stopped(job)
                timeline._finish(job, tid, "failed", str(error)[:600])
            finally:
                self.release(token)

        timeline.chat, timeline._chat_reply, timeline._run_task = chat, reply, task


class DeferredRender:
    """Run native deferred edits after the render's own receipt has finished."""
    def __init__(self, activity, spec, setup, mode):
        self.activity, self.spec, self.setup, self.mode = activity, spec, setup, mode
        self.timeline = activity.studio.timeline
        self.finished = threading.Event()
        self.receipt_saved = False
        self.render_token = activity.reserve()
        self.deferred_tokens = []

    def close(self, receipt_saved):
        self.receipt_saved = receipt_saved
        self.finished.set()
        self.activity.release(self.render_token)
        if not receipt_saved:
            for token in self.deferred_tokens:
                self.activity.release(token)

    def __getattr__(self, name):
        if name != "release_deferred":
            return getattr(self.timeline, name)
        # The original app retrieves this callback before constructing its
        # daemon thread. Reserve now, rather than after that thread starts.
        token = self.activity.reserve()
        self.deferred_tokens.append(token)

        def release(job, slug):
            try:
                self.finished.wait()
                with self.timeline._lock(job):
                    deferred = bool(self.timeline._state(job).get("deferred"))
                if not deferred:
                    return 0
                if not self.receipt_saved:
                    self.timeline._note(job, "sys", "Your edits are still queued. Reconnect CR and render again to save the render record before those edits start.")
                    return 0
                title = str(self.spec.get("title") or slug)
                style = str(self.spec.get("style") or "custom")
                try:
                    project = cr_bridge.begin_project(title, style, proof=self.setup,
                        job_spec={**self.spec, "minutes":max(1, int(self.spec.get("minutes") or 1))}, purpose="creative")
                    cr_claude._private_write(Path(job) / ".cr-editor-project.json", {**project, "brain_mode": self.mode})
                except Exception:
                    self.timeline._note(job, "sys", "Your edits are still queued. Reconnect CR in setup before rendering again; no editor generation has started.")
                    return 0
                original = self.timeline.release_deferred
                source_thread = original.__globals__["threading"].Thread

                def spawn(*args, **kwargs):
                    target = kwargs.get("target")
                    if target is not self.timeline._chat_reply:
                        return source_thread(*args, **kwargs)
                    child_token = self.activity.reserve()

                    def reply(*args, **kwargs):
                        try:
                            return target(*args, **kwargs)
                        finally:
                            self.activity.release(child_token)

                    try:
                        thread = source_thread(*args, **{**kwargs, "target":reply})
                    except BaseException:
                        self.activity.release(child_token)
                        raise

                    class Dispatch:
                        def start(dispatch):
                            try:
                                return thread.start()
                            except BaseException:
                                self.activity.release(child_token)
                                raise

                    return Dispatch()

                dispatch = FunctionType(original.__code__, {**original.__globals__, "threading":SimpleNamespace(Thread=spawn)},
                                        original.__name__, original.__defaults__, original.__closure__)
                return dispatch(job, slug)
            finally:
                self.activity.release(token)

        return release


def deferred_editor_request(studio, path):
    """Only the same job's timeline render can accept native deferred edits."""
    match = re.fullmatch(r"/api/tl/([^/]+)/(?:chat|op)", path)
    if not match:
        return False
    slug = match[1]
    if studio.RUNNING.get("slug") == slug:
        return bool(getattr(studio, "JOBS", {}).get(slug, {}).get("timeline"))
    return any(spec.get("slug") == slug and spec.get("timeline") for spec in studio.QUEUE)


def creative_request(path):
    """Routes that may start creative work; saved output remains accessible."""
    return (path.startswith(("/api/style", "/api/offer", "/api/auto", "/api/learn", "/api/farm"))
            or path in {"/api/products/ideas", "/api/products/build"}
            or path.startswith("/api/tl/") and path.rsplit("/", 1)[-1] in {"chat", "op", "render"})


def output_artifacts(engine, job):
    """Record published deliverables before working files, without uploading them."""
    media_types={".mp4":"video/mp4",".mp3":"audio/mpeg",".wav":"audio/wav",".png":"image/png",".jpg":"image/jpeg",".zip":"application/zip",".txt":"text/plain",".json":"application/json",".srt":"text/plain"}
    final=engine.final_video(job)
    candidates=[final, *sorted(engine.main_dir(job).iterdir())] if engine.main_dir(job).is_dir() else [final]
    candidates.extend(sorted(job.iterdir()))
    artifacts=[]
    seen=set()
    names=set()
    for path in candidates:
        if not path.is_file() or path in seen or path.suffix not in media_types or path.name.startswith(("_",".cr","cr-project")):
            continue
        seen.add(path)
        # Receipts describe local files; canonical names also support titles in
        # languages outside the server's ASCII metadata contract.
        stem=re.sub(r"[^a-zA-Z0-9._ -]","_",path.stem).strip("._ -") or "output"
        name="video.mp4" if path==final else stem[:160-len(path.suffix)]+path.suffix
        if name in names:
            name=f"{len(artifacts)+1}-{name}"[:160]
        names.add(name)
        digest=hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b""):
                digest.update(chunk)
        artifacts.append({"name":name,"sha256":digest.hexdigest(),"media_type":media_types[path.suffix]})
        if len(artifacts)==20:
            break
    return artifacts


def graphics_ready():
    if _graphics_check.get("expires",0)>time.monotonic():
        return _graphics_check["result"]
    result={"browser":False,"webgl":False}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if Path(p.chromium.executable_path).is_file():
                browser=p.chromium.launch(headless=True,args=["--enable-unsafe-swiftshader"])
                page=browser.new_page()
                result=page.evaluate("""() => ({browser:!!document.createElement('canvas').getContext('2d'),webgl:!!document.createElement('canvas').getContext('webgl2')})""")
                browser.close()
    except Exception:
        pass
    _graphics_check.update(result=result,expires=time.monotonic()+300)
    return result


def sync_completion_receipt(bridge, pending):
    """Sync metadata, retaining evidence when an old repeat contradicted success."""
    receipt = json.loads(pending.read_text())
    saved_path = pending.with_name(".cr-finish.json")
    try:
        saved = json.loads(saved_path.read_text())
    except (OSError, ValueError):
        saved = {}
    if (receipt.get("status") == "failed" and saved.get("status") == "done"
            and saved.get("project_id") == receipt.get("project_id") and saved.get("artifacts")):
        progress = bridge.project_progress(receipt["project_id"])
        # The server's typed artifact contract includes absent optional fields
        # as null. Their omission locally is the same metadata, not a change.
        canonical = lambda rows: [{k:v for k,v in row.items() if v is not None} for row in rows]
        if (progress.get("project", {}).get("status") == "done"
                and canonical(progress.get("artifacts", [])) == canonical(saved["artifacts"])
                and not any(s.get("status") in {"authorized", "failed", "retry_allowed"}
                            for s in progress.get("stages", []))):
            # The finished session is immutable. An unsuccessful repeat must
            # never downgrade it or overwrite its successful receipt.
            cr_claude._private_write(pending.with_name(".cr-finish-superseded.json"), receipt)
            pending.unlink()
            return
    bridge.finish_project(receipt)
    pending.replace(saved_path)


def tool_ready(name, flag="-version"):
    executable = shutil.which(name)
    if not executable:
        return False
    try:
        return subprocess.run([executable, flag], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def video_tools_ready(ffmpeg="ffmpeg", ffprobe="ffprobe"):
    if not tool_ready(ffprobe):
        return False
    executable = shutil.which(ffmpeg)
    if not executable:
        return False
    try:
        # A minimal Homebrew build can pass -version but cannot burn captions.
        for flag, required in (("-filters", {"subtitles"}),
                               ("-encoders", {"libx264", "aac", "libmp3lame"})):
            result = subprocess.run([executable, flag], capture_output=True, text=True, timeout=5)
            names = {line.split()[1] for line in result.stdout.splitlines() if len(line.split()) > 1}
            if result.returncode or not required <= names:
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def configure_video_tools():
    if sys.platform != "darwin" or video_tools_ready():
        return
    # Homebrew's full build can coexist with a stale binary earlier on PATH.
    # Select an already installed, runnable pair for this studio process only.
    for directory in ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"):
        if video_tools_ready(str(Path(directory) / "ffmpeg"), str(Path(directory) / "ffprobe")):
            os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
            return


def create_app():
    configure_video_tools()
    initial=cr_bridge.load_settings()
    if not initial:
        initial={"brain_mode":"claudecode","costs_accepted":False}
        cr_bridge.save_settings(initial)
    mode = initial.get("brain_mode", "channelrecipe")
    os.environ["CLAUDE_PROVIDER"] = "claudecode" if mode == "claudecode" else "channelrecipe"
    os.environ["SCRIPT_PROVIDER"] = os.environ["CLAUDE_PROVIDER"]
    os.environ["CLAUDE_HANDOFF_DIR"] = ""
    import app as studio
    from flask import Response, g, has_request_context, jsonify, request
    cr_bridge.install(studio.mv)
    cr_media.install(studio.mv)
    cr_capabilities.install(studio)
    cr_render.install(studio.mv)
    import cr_style_delivery
    cr_style_delivery.install(studio.mv)
    import cr_shorts
    cr_shorts.install(studio.mv)
    cr_workers.install()
    cr_submission.install(studio)
    activity = getattr(studio, "_cr_activity", None)
    if activity is None:
        activity = studio._cr_activity = CreativeActivity(studio)
        activity.install()
    import cr_video_cancel
    video_workers = studio._cr_video_workers = cr_video_cancel.VideoWorkers(studio, HOME)
    video_workers.install_routes()
    import cr_queue
    queue_keeper = cr_queue.install(studio, HOME)               # long batches: saved, and held on a shared stop
    token = secrets.token_urlsafe(24)
    # The project launcher reads this private, local-only control token. It is
    # never sent to CR or included in copied launch commands.
    control = HOME / ".cr-runtime.json"
    control.write_text(json.dumps({"pid":os.getpid(),"token":token,"port":7860}))
    control.chmod(0o600)
    # Prevent a remote website from using the local control room (DNS rebinding/CSRF).
    @studio.app.before_request
    def local_access():
        if request.host not in ("127.0.0.1:7860", "localhost:7860"):
            return jsonify(error="Open the studio on localhost."), 403
        if getattr(studio, "_cr_restart_for_update", False) and request.method != "GET":
            return jsonify(error="Studio is restarting to finish its update. Try again in a few seconds."), 409
        origin = request.headers.get("Origin")
        if origin and origin not in ("http://127.0.0.1:7860", "http://localhost:7860"):
            return jsonify(error="Open the studio on localhost."), 403
        if request.method in ("POST", "DELETE", "PUT", "PATCH"):
            if request.headers.get("X-CR-Local-Token") != token:
                return jsonify(error="Refresh your studio page and try again."), 403
        account_change = request.path in {"/cr-connect", "/cr-provider", "/cr-brain", "/cr-hub", "/cr-voice", "/cr-images", "/cr-claude/setup"}
        if request.path == "/cr-claude/setup" and (request.get_json(silent=True) or {}).get("action") == "check":
            account_change = False
        deferred = deferred_editor_request(studio, request.path)
        creative = creative_request(request.path) and not deferred
        if request.method == "POST" and (account_change or creative or request.path.startswith("/cr-3d/") or request.path in {"/api/run", "/cr-submit-project", "/cr-recover"}):
            # Serialize the initial check and context selection. Native chat/task
            # reservations keep this protection after their response returns.
            if not activity.lock.acquire(blocking=False):
                return jsonify(error="Finish the current studio request before starting more work or changing accounts."), 409
            g.cr_activity_locked = True
        if account_change and request.method == "POST":
            if activity.busy():
                return jsonify(error="Finish your current video before changing its accounts."), 409
        if request.path == "/api/run" and request.method == "POST":
            try:
                cfg = cr_bridge.load_settings()
                if not cfg.get("costs_accepted"):
                    return jsonify(error="Review your provider spending in setup before making a video."), 409
                data = request.get_json(silent=True) or {}
                # Do not change the shared brain project while another video is active.
                if activity.busy():
                    return jsonify(error="Finish your current video before starting another."), 409
                checks = setup_status().get_json()
                preflight = cr_readiness.preset(studio,checks,data)
                if not preflight["ready"]:
                    return jsonify(error="Complete this video's setup before starting.",**preflight),409
                if preflight.get("manimgx_identity"):
                    data["manimgx_identity"] = preflight["manimgx_identity"]
                    request._cached_json = (data, data)
                cfg = cr_bridge.load_settings()
                cfg["setup_proof"] = preflight["proof"]
                cr_bridge.save_settings(cfg)
                if cfg.get("brain_mode") == "claudecode":
                    cr_bridge.prepare_runtime(preflight["proof"])
            except Exception as e:
                return jsonify(error=str(e)), 409
        elif request.method == "POST" and creative:
            checks=setup_status().get_json()
            if not checks["readiness"]["ready"]:
                return jsonify(error="Finish studio setup before creating styles or products.",**checks["readiness"]),409
            if activity.busy():
                return jsonify(error="Finish the current video before starting more creative work."),409
            if cr_bridge.load_settings().get("brain_mode") == "claudecode":
                if request.path.startswith("/api/farm/"):
                    return jsonify(error="The selected Claude Code workflow renders on this computer. A remote farm does not have this Claude login."),409
                current=cr_bridge._project
                if current and cr_bridge.project_progress(current["id"])["project"]["status"] in {"done","failed"}:
                    current=None
                if not current:
                    p=cr_readiness.proof(checks,purpose="creative")
                    cr_bridge.begin_project("Studio style or product work","custom",proof=p,purpose="creative")

    @studio.app.teardown_request
    def release_activity_lock(error=None):
        if getattr(g, "cr_activity_locked", False):
            g.cr_activity_locked = False
            activity.lock.release()

    @studio.app.get("/cr-setup")
    def setup_page():
        text = (HOME / "cr_setup.html").read_text()
        return Response(text.replace("__LOCAL_TOKEN__", token), mimetype="text/html", headers={"Cache-Control": "no-store"})

    # The full check runs Claude Code, its tools list and an account call: about 2.5 s. Pages that only need to draw
    # ask for the last result (?cached=1, up to five minutes old) and refresh in the background; anything that
    # starts work (preflight, setup) always gets a fresh check.
    last_status = {}

    @studio.app.get("/cr-status")
    def setup_status():
        if (has_request_context() and request.args.get("cached") == "1"
                and last_status.get("at", 0) > time.monotonic() - 300):
            return jsonify({**last_status["checks"], "cached": True})
        cfg = cr_bridge.load_settings()
        checks = {"python": sys.version_info >= (3,10), "ffmpeg": video_tools_ready(),
                  "node": tool_ready("node", "--version") or tool_ready("deno", "--version"),
                  "connected": bool(cfg.get("key")), "costs_accepted": bool(cfg.get("costs_accepted"))}
        checks.update(graphics_ready())
        try:
            checks["account"] = cr_bridge.call("status") if cfg.get("key") else None
        except Exception as e:
            checks["connection_error"] = str(e)
        brain = (checks.get("account") or {}).get("brain") or {}
        checks["brain_models"] = {"general": brain.get("model", ""),
                                  "visual": brain.get("visual_model") or brain.get("model", "")}
        # Key readiness only; never send saved provider keys to the browser.
        checks["providers"] = {name: bool(os.getenv(name)) for name in
                              ["ELEVENLABS_API_KEY", "ALGROW_API_KEY", "WAVESPEED_API_KEY", "KIE_API_KEY", "ATLASCLOUD_API_KEY", "PEXELS_API_KEY", "GEMINI_API_KEY", "EPIDEMIC_API_KEY", "AI33_API_KEY", "SIRAY_API_KEY", "BRAVE_API_KEY"]}
        hub = cr_media.profile()
        checks["model_accounts"] = cr_media.PROVIDERS
        checks["media_hub"] = {k: hub[k] for k in ("provider", "tasks", "voice") if k in hub}
        checks["media_hub"]["connected"] = bool(hub and os.getenv(cr_media.PROVIDERS[hub["provider"]]["key_env"]))
        motion_cfg = cr_media.profile("motion") if cr_media.active("motion") else {}
        # The history kit chooses Kie directly when motion isn't replaced.
        motion_provider = motion_cfg.get("provider") or ("kie" if os.getenv("KIE_API_KEY") else "original")
        checks["motion"] = {"provider": motion_provider, "resolution": "720p",
                            "seconds": 5 if motion_provider == "kie" else 8,
                            "model": cr_media.PROVIDERS.get(motion_provider, {}).get("models", {}).get("motion", ""),
                            "connected": bool(motion_provider in cr_media.PROVIDERS and
                                              os.getenv(cr_media.PROVIDERS[motion_provider].get("key_env", "")))}
        voice = cr_media.profile("narration")
        if not cr_media.active("narration"):
            voice = {}
        override = cfg.get("narration") or {}
        checks["narration"] = {"selection": override.get("provider", "inherit"),
                               "provider": voice.get("provider", "original"), "voice": voice.get("voice", ""),
                               "model": voice.get("model") or (cr_media.PROVIDERS[voice["provider"]]["models"]["narration"] if voice else ""),
                               "connected": bool(cr_media.active("narration") and voice and
                                                 os.getenv(cr_media.PROVIDERS[voice["provider"]]["key_env"]))}
        image_cfg = cr_media.profile("images") if cr_media.active("images") else {}
        plan = []
        if image_cfg.get("provider") == "kie":
            try:
                plan = cr_media.image_plan(image_cfg)
            except cr_media.AccountStop as error:
                checks["image_error"] = str(error)
        checks["images"] = {"selection": (cfg.get("images") or {}).get("provider", "inherit"),
                            "provider": image_cfg.get("provider", "original"),
                            "model": plan[0] if plan else "", "fallbacks": plan[1:],
                            "connected": bool(image_cfg and os.getenv(cr_media.PROVIDERS[image_cfg["provider"]]["key_env"]))}
        try:
            checks["local_version"] = json.loads((HOME / "cr-release.json").read_text())["version"]
        except (OSError, ValueError, KeyError):
            checks["local_version"] = "development"
        readiness = cr_readiness.basic(checks)
        checks["brain_mode"] = readiness["brain_mode"]
        checks["claude_code"] = readiness.pop("claude_code")
        checks["readiness"] = readiness
        last_status.update(at=time.monotonic(), checks=checks)
        return jsonify(checks)

    @studio.app.post("/cr-preflight")
    def preflight():
        try:
            checks=setup_status().get_json()
            result=cr_readiness.preset(studio,checks,request.get_json(silent=True) or {})
            if result["ready"]:
                cfg=cr_bridge.load_settings()
                if cfg.get("brain_mode") == "claudecode":
                    cr_claude.preserve_model_verification(cfg,checks["claude_code"])
                cfg.update(local_version=result["proof"]["local_version"],setup_proof=result["proof"])
                cr_bridge.save_settings(cfg)
                proof_file=HOME/".cr-setup-proof.json"
                proof_file.write_text(json.dumps({"setup":result["proof"],"style":(request.get_json(silent=True) or {}).get("style"),"features":result["features"]},indent=2))
                proof_file.chmod(0o600)
                if cfg.get("brain_mode") == "claudecode":
                    result["workflow"]=cr_bridge.prepare_runtime(result["proof"])["workflow"]
            return jsonify(result)
        except Exception as error:
            return jsonify(error=str(error),ready=False,blockers=[str(error)]),409

    @studio.app.post("/cr-submit-project")
    def submit_project():
        if activity.busy():
            return jsonify(error="Finish the current video before starting another."),409
        try:
            project_id=str((request.get_json(silent=True) or {}).get("project_id", ""))
            import re
            if not re.fullmatch(r"crv_[a-f0-9]{24}",project_id):
                return jsonify(error="Use a valid saved CR project."),400
            progress=cr_bridge.project_progress(project_id)
            if progress.get("purpose", "video") != "video":
                return jsonify(error="This is a setup or creative action. Continue it in its original studio controls, not the saved-video launcher."),409
            spec=progress["job_spec"]
            if progress["project"]["status"] == "done":
                return jsonify(error="This project already finished. Open its saved local output."),409
            preflight=cr_readiness.preset(studio,setup_status().get_json(),spec)
            if not preflight["ready"]:
                return jsonify(error="Complete this project's setup before starting.",**preflight),409
            cfg=cr_bridge.load_settings()
            cfg["setup_proof"]=preflight["proof"]
            cr_bridge.save_settings(cfg)
            # Capture original normalized specs before the live dispatcher can
            # consume them, and publish only this project's fully owned items.
            payload={**spec,"steps":spec.get("steps") or ["script","voiceover","pexels","images","video","thumbnail"]}
            response=cr_submission.submit(studio,payload,project_id)
            if not 200 <= response.status_code < 300:
                return response
            queued=response.get_json().get("queued",[])
            return jsonify(queued=queued,project_id=project_id)
        except Exception as error:
            return jsonify(error=str(error)),409

    @studio.app.post("/cr-claude/setup")
    def claude_setup():
        data=request.get_json(silent=True) or {}
        action=data.get("action")
        cfg=cr_bridge.load_settings()
        try:
            if action == "check":
                return jsonify(message="Claude Code login and connection checks updated.",**setup_status().get_json())
            if not cfg.get("key") or not cfg.get("costs_accepted"):
                raise RuntimeError("Connect CR and acknowledge account usage before connecting Claude Code.")
            local=cr_claude.status()
            if not local["ready"]:
                raise RuntimeError(local["message"])
            if action == "connect_mcp":
                connection=cr_mcp.register(cfg)
                if not connection["connected"]:
                    raise RuntimeError(connection["why"])
                return jsonify(message="CR production tools connected. Save your model choices next.",mcp_connected=True)
            if action == "select":
                if not cr_mcp.status(cfg)["connected"]:
                    raise RuntimeError("Connect CR tools before selecting your Claude Code route.")
                if cfg.get("brain_mode") == "claudecode":
                    cr_claude.preserve_model_verification(cfg,local)
                cfg.update(cr_claude.select_profile(data.get("general_model"),data.get("visual_model")))
                cfg["local_version"]=json.loads((HOME/"cr-release.json").read_text())["version"]
                cfg.pop("setup_proof",None)
                cr_bridge.save_settings(cfg)
                cr_bridge.install(studio.mv)
                return jsonify(message="Your Claude Code route is selected. Verify both models, then check your preset.")
            if action == "verify":
                if cfg.get("brain_mode") != "claudecode" or not cr_mcp.status(cfg)["connected"]:
                    raise RuntimeError("Connect CR tools and save your Claude Code model choices first.")
                if not all(cfg.get(key) for key in ("general_model","visual_model","local_version")):
                    raise RuntimeError("Save your Claude Code model choices before verifying them.")
                checks=setup_status().get_json()
                result=cr_claude.verify_models(studio.mv,cr_bridge,cr_readiness.proof(checks,purpose="setup"))
                return jsonify(result)
            return jsonify(error="Choose a supported setup action."),400
        except (Exception,cr_claude.ClaudeStageStopped) as error:
            return jsonify(error=str(error)),409

    @studio.app.get("/cr-projects")
    def local_projects():
        try:
            cr_claude.sync_receipts(cr_bridge)
            # Only replay final metadata, never generation, after a lost receipt.
            if activity.lock.acquire(blocking=False):
                try:
                    if not activity.busy():
                        for pending in list(studio.OUT.rglob(".cr-finish-pending.json"))[:20]:
                            try:
                                sync_completion_receipt(cr_bridge, pending)
                            except (Exception,ValueError):
                                pass
                finally:
                    activity.lock.release()
            projects=cr_bridge.call("projects")["projects"]
            result=[]
            for p in projects[:20]:
                try:
                    progress=cr_bridge.project_progress(p["id"])
                except RuntimeError:
                    continue
                result.append({"project_id":p["id"],"title":p["title"],"status":p["status"],"purpose":progress.get("purpose", ""),
                    "stages":[s for s in progress["stages"] if s["status"] in {"failed","authorized","retry_allowed"}]})
            return jsonify(projects=result)
        except Exception:
            return jsonify(error="Reconnect CR before loading project recovery.",projects=[]),409

    @studio.app.post("/cr-recover")
    def recover_stage():
        if activity.busy():
            return jsonify(error="Stop or finish the running video before reviewing recovery."),409
        try:
            cr_claude.sync_receipts(cr_bridge)
            result=cr_bridge._mcp_tool("local_studio_retry_stage",request.get_json(silent=True) or {})
            return jsonify(result)
        except Exception as error:
            return jsonify(error=str(error)),409

    @studio.app.post("/cr-connect")
    def connect():
        data = request.get_json(silent=True) or {}
        cfg = {**cr_bridge.load_settings(), "url": "https://channelrecipe.com", "key": str(data.get("key", "")).strip(),
               "costs_accepted": data.get("costs_accepted") is True}
        try:
            status = cr_bridge.call("status", settings=cfg)
            cr_bridge.save_settings(cfg)
        except Exception as e:
            return jsonify(error=str(e)), 400
        return jsonify(connected=True, brain_connected=bool(status.get("brain", {}).get("connected")))

    @studio.app.get("/cr-diagnostics")
    def diagnostics():
        import platform
        if request.headers.get("X-CR-Local-Token") != token:
            return jsonify(error="Refresh your studio page and try again."), 403
        try:
            checks = setup_status().get_json()
        except Exception as error:
            checks = {"error": type(error).__name__}
        tick = lambda value: "yes" if value else "no"
        cli = checks.get("claude_code") or {}
        hub, voice = checks.get("media_hub") or {}, checks.get("narration") or {}
        readiness = checks.get("readiness") or {}
        try:
            running = "yes" if activity.busy() else "no"
        except Exception:
            running = "unknown"
        lines = [
            "ChannelRecipe Studio diagnostics",
            "Studio: %s" % checks.get("local_version", "unknown"),
            "Computer: %s %s (%s) · Python %s" % (platform.system(), platform.release(), platform.machine(),
                                                   platform.python_version()),
            "Tools: Python %s · FFmpeg %s · graphics browser %s · Node %s" % (
                tick(checks.get("python")), tick(checks.get("ffmpeg")), tick(checks.get("browser")), tick(checks.get("node"))),
            "ChannelRecipe account connected: %s" % tick(checks.get("connected")),
            "Claude Code: installed %s%s · signed in %s · tools connected %s · models verified %s" % (
                tick(cli.get("installed")), (" " + str(cli["version"])) if cli.get("version") else "",
                tick(cli.get("authenticated")), tick(cli.get("mcp_connected")), tick(cli.get("models_verified"))),
            "Writing route: %s" % checks.get("brain_mode", "unknown"),
            "Voice and images: %s%s · narration %s" % (hub.get("provider", "none"),
                (" (" + ", ".join(hub.get("tasks", [])) + ")") if hub.get("tasks") else "", tick(voice.get("connected"))),
            "Setup ready: %s" % tick(readiness.get("ready")),
            "Working right now: %s" % running,
        ]
        blockers = readiness.get("blockers") or []
        if blockers:
            lines.append("Setup still needs: " + "; ".join(str(b.get("message") if isinstance(b, dict) else b) for b in blockers)[:600])
        recent = [line for line in RECENT_OUTPUT if '" 200 -' not in line and '" 304 -' not in line]
        problems = [line for line in recent if any(word in line.lower() for word in ("error", "traceback", "failed", "exception", "stopped"))]
        lines += ["", "Recent problems:"] + (problems[-30:] or ["none"])
        lines += ["", "Recent Studio window output:"] + (recent[-40:] or ["none"])
        home = str(Path.home())
        text = redact("\n".join(lines)).replace(home, "~")
        return Response(text, mimetype="text/plain", headers={"Cache-Control": "private, no-store"})

    # In-app updates. Downloading never touches the running studio; restarting
    # waits until nothing is running and then reopens in the same window.
    updater = {"state": "idle", "done": 0, "total": 0, "message": "", "version": None, "thread": None}

    def update_snapshot():
        return {key: updater[key] for key in ("state", "done", "total", "message", "version")}

    @studio.app.get("/cr-update")
    def update_status():
        return jsonify(update_snapshot())

    @studio.app.post("/cr-update/download")
    def update_download():
        import cr_update
        if updater["state"] in ("downloading", "restarting"):
            return jsonify(update_snapshot())

        def run():
            def progress(done, total):
                updater.update(done=done, total=total)
            try:
                result = cr_update.prepare(HOME, progress=progress)
                if result["state"] == "current":
                    updater.update(state="current", message="You already have the latest Studio.")
                else:
                    updater.update(state="staged", version=result["version"],
                                   message="The update is ready. Restart Studio to finish.")
            except cr_update.UpdateStopped as stopped:
                updater.update(state="failed", message=str(stopped))
            except Exception:
                studio.app.logger.exception("The update could not download")
                updater.update(state="failed", message="The update couldn't download. Check your connection and try again.")

        updater.update(state="downloading", done=0, total=0, message="Downloading the update…")
        updater["thread"] = threading.Thread(target=run, daemon=True)
        updater["thread"].start()
        return jsonify(update_snapshot())

    @studio.app.post("/cr-update/restart")
    def update_restart():
        if updater["state"] != "staged":
            return jsonify(error="Download the update first."), 409
        with activity.lock:
            if activity.busy():
                return jsonify(error="A video or task is still running. Studio will be ready to restart when it finishes."), 409
            # From here no new work starts; the server stops after this reply.
            studio._cr_restart_for_update = True
            updater.update(state="restarting", message="Restarting Studio…")
        server = getattr(studio, "_cr_server", None)
        if server is not None:
            threading.Timer(0.5, server.shutdown).start()
        return jsonify(update_snapshot())

    # One-click connect: the setup page opens channelrecipe.com, the creator
    # approves, and the browser comes back here with a short-lived code that
    # only this studio can redeem (it holds the matching verifier).
    pending_connect = {}

    @studio.app.post("/cr-connect/start")
    def connect_start():
        import base64
        from urllib.parse import urlencode
        data = request.get_json(silent=True) or {}
        if data.get("costs_accepted") is not True:
            return jsonify(error="Confirm how Studio is billed, then connect."), 400
        now = time.time()
        for state, entry in list(pending_connect.items()):
            if now - entry["created"] > CONNECT_SECONDS:
                pending_connect.pop(state, None)
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(24)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        pending_connect[state] = {"verifier": verifier, "created": now}
        return jsonify(url=CR_URL + "/studio/connect?" + urlencode({"challenge": challenge, "state": state}))

    @studio.app.get("/cr-connect/return")
    def connect_return():
        import requests
        from flask import redirect
        entry = pending_connect.pop(request.args.get("state", ""), None)
        code = request.args.get("code", "")
        if not entry or time.time() - entry["created"] > CONNECT_SECONDS or not code or len(code) > 512:
            return redirect("/cr-setup?connect=expired")
        if activity.busy():
            return redirect("/cr-setup?connect=busy")
        try:
            response = requests.post(CR_URL + "/api/studio/connect/exchange",
                                     json={"code": code, "verifier": entry["verifier"]}, timeout=30)
            if response.status_code == 403:
                return redirect("/cr-setup?connect=plan")
            response.raise_for_status()
            key = str(response.json().get("key") or "")
            cfg = {**cr_bridge.load_settings(), "url": CR_URL, "key": key, "costs_accepted": True}
            cr_bridge.call("status", settings=cfg)
            cr_bridge.save_settings(cfg)
        except Exception:
            studio.app.logger.exception("One-click connect could not finish")
            return redirect("/cr-setup?connect=failed")
        return redirect("/cr-setup?connect=done")

    def write_provider(name, value):
        from dotenv import set_key
        env = HOME / ".env"
        env.touch(exist_ok=True)
        env.chmod(0o600)
        set_key(str(env), name, value)
        os.environ[name] = value
        if hasattr(studio.mv, name):
            setattr(studio.mv, name, value)

    @studio.app.post("/cr-brain")
    def save_brain():
        data = request.get_json(silent=True) or {}
        cfg = cr_bridge.load_settings()
        if not cfg.get("key") or not cfg.get("costs_accepted"):
            return jsonify(error="Connect CR and review provider spending first."), 409
        provider = data.get("provider")
        info = cr_media.PROVIDERS.get(provider) if isinstance(provider, str) else None
        if not info or not info.get("brain_models"):
            return jsonify(error="Choose a supported brain account."), 400
        supplied = str(data.get("key", "")).strip()
        # Reusing a saved media key is explicit in the brain form. A direct
        # Anthropic key is stored only in CR's encrypted per-user vault.
        key = supplied or (os.getenv(info.get("key_env", ""), "").strip()
                           if provider in ("kie", "atlas") else "")
        if not 16 <= len(key) <= 512 or any(ch.isspace() for ch in key):
            return jsonify(error="Paste your complete API key, or reuse your saved Kie or Atlas key."), 400
        try:
            result = cr_bridge.call("brain-key", {"provider": provider, "api_key": key,
                                    "model": data.get("model"), "visual_model": data.get("visual_model")})
        except Exception as error:
            return jsonify(error=str(error)), 400
        cfg["brain_mode"]="channelrecipe"
        cfg.pop("setup_proof",None)
        cr_bridge.save_settings(cfg)
        cr_claude.uninstall()
        cr_bridge.install(studio.mv)
        return jsonify(saved=True, message=result.get("message", "Brain account connected."))

    def check_media_account(provider, key):
        # Read-only validation: no generated audio and no change to the CR brain.
        url = "https://api.kie.ai/api/v1/chat/credit" if provider == "kie" else "https://api.atlascloud.ai/v1/models"
        import requests
        cr_media._data(requests.get(url, headers={"Authorization": "Bearer " + key},
                                   timeout=(10, 20), allow_redirects=False), provider)

    @studio.app.post("/cr-images")
    def save_images():
        data = request.get_json(silent=True) or {}
        cfg = cr_bridge.load_settings()
        if not cfg.get("key") or not cfg.get("costs_accepted"):
            return jsonify(error="Connect CR and review the spending agreement first."), 409
        provider = data.get("provider")
        if provider == "inherit":
            cfg.pop("images", None)
            cr_bridge.save_settings(cfg)
            return jsonify(saved=True, message="Images use your model account or individual image settings.")
        if provider != "kie":
            return jsonify(error="Choose Kie.ai or your existing image account."), 400
        fallbacks = data.get("fallbacks", [])
        if (not isinstance(data.get("model"), str) or not isinstance(fallbacks, list)
                or len(fallbacks) > 4 or any(not isinstance(m, str) for m in fallbacks)):
            return jsonify(error="Choose a primary image model and up to four fallbacks."), 400
        try:
            plan = cr_media.image_plan({"model": data["model"], "fallbacks": fallbacks})
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 400
        supplied = str(data.get("key", "")).strip()
        key = supplied or os.getenv("KIE_API_KEY", "").strip()
        if not 16 <= len(key) <= 512 or any(c.isspace() for c in key):
            return jsonify(error="Connect your Kie API key, or leave it blank to reuse your saved key."), 400
        try:
            check_media_account("kie", key)
            if supplied:
                write_provider("KIE_API_KEY", key)
            cfg["images"] = {"provider": "kie", "model": plan[0], "fallbacks": plan[1:]}
            cr_bridge.save_settings(cfg)
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 400
        except Exception:
            return jsonify(error="Kie could not be connected. Check its key and API access."), 400
        return jsonify(saved=True, message="Image choices saved. Each image can try up to " + str(len(plan)) +
                       " selected models, once each, on your Kie balance. Narration and writing keep their existing choices.")

    @studio.app.post("/cr-voice")
    def save_voice():
        data = request.get_json(silent=True) or {}
        cfg = cr_bridge.load_settings()
        provider = data.get("provider")
        if provider not in ("inherit", "original", "kie", "atlas"):
            return jsonify(error="Choose a narration account."), 400
        if not cfg.get("key") or not cfg.get("costs_accepted"):
            return jsonify(error="Connect CR and review the spending agreement first."), 409
        if provider in ("inherit", "original"):
            if provider == "inherit":
                cfg.pop("narration", None)
            else:
                cfg["narration"] = {"provider": "original"}
            cr_bridge.save_settings(cfg)
            return jsonify(saved=True, message="Narration uses " +
                           ("the model account choices above." if provider == "inherit" else "your individual voice settings."))
        info = cr_media.PROVIDERS[provider]
        model_id = str(data.get("model", "")).strip() or info["models"]["narration"]
        try:
            model = cr_media.narration_model(provider, model_id)
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 400
        supplied = str(data.get("key", "")).strip()
        key = supplied or os.getenv(info["key_env"], "").strip()
        voice = str(data.get("voice", "")).strip()
        if model.get("voice_names") and voice and voice not in model["voice_names"]:
            return jsonify(error="Choose a voice name from this model’s list, or leave it blank for " + model["default_voice"] + "."), 400
        if (not 16 <= len(key) <= 512 or any(c.isspace() for c in key)
                or (voice and (len(voice) > 100 or not all(c.isalnum() or c in "_-" for c in voice)))):
            return jsonify(error="Paste a complete API key and a valid voice ID, or leave the voice blank for the default."), 400
        try:
            check_media_account(provider, key)
            if supplied:
                write_provider(info["key_env"], key)
            cfg["narration"] = {"provider": provider, "model": model_id, "voice": voice or model["default_voice"]}
            cr_bridge.save_settings(cfg)
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 400
        except Exception:
            return jsonify(error="This voice account could not be connected. Check its API key and access, then try again."), 400
        return jsonify(saved=True, message="Narration now uses " + info["label"] +
                       ". Voice generation is charged to that account. Your writing and other media choices are unchanged.")

    # The creator's own ElevenLabs account for narration: Eleven v4, ElevenLabs' newest and most expressive model,
    # through the studio's own voice settings (and its whole voice library in the Create page's voice picker). The
    # key is checked the only way that proves it can speak - a restricted key can list voices yet not make speech -
    # by reading one short line, which comes back as the sample the creator hears.
    ELEVEN_SAMPLE = "Hey! This is your narrator. Let's make something people can't stop watching."
    ELEVEN_SAMPLE_VOICE = "JBFqnCBsd6RMkjVDRZzb"

    @studio.app.post("/cr-voice/elevenlabs")
    def use_elevenlabs():
        import base64
        import requests
        data = request.get_json(silent=True) or {}
        cfg = cr_bridge.load_settings()
        if not cfg.get("key") or not cfg.get("costs_accepted"):
            return jsonify(error="Connect CR and review the spending agreement first."), 409
        supplied = str(data.get("key", "")).strip()
        key = supplied or os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not 16 <= len(key) <= 512 or any(c.isspace() for c in key):
            return jsonify(error="Paste your complete ElevenLabs API key."), 400
        sample, used = None, ""
        for model in ("eleven_v4", "eleven_v3"):
            try:
                r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_SAMPLE_VOICE}"
                                  "?output_format=mp3_44100_64", headers={"xi-api-key": key},
                                  json={"text": ELEVEN_SAMPLE, "model_id": model}, timeout=(10, 60))
            except requests.RequestException:
                return jsonify(error="ElevenLabs didn't answer. Check your connection and try again."), 502
            detail = r.text[:400].lower() if r.status_code != 200 else ""
            if r.status_code == 200 and r.content:
                sample, used = r.content, model
                break
            if r.status_code in (400, 403, 404, 422) and "model" in detail and model != "eleven_v3":
                continue                                       # an account without v4 yet: v3
            if r.status_code == 401 and "permission" in detail:
                return jsonify(error="This ElevenLabs key is restricted and can't make speech. In ElevenLabs, create "
                                     "a key with Text to Speech access and paste that one."), 400
            if r.status_code == 401:
                return jsonify(error="ElevenLabs didn't accept this key. Copy it again from ElevenLabs → API keys."), 400
            if r.status_code in (402, 429) or "quota" in detail or "credits" in detail:
                return jsonify(error="Your ElevenLabs plan has no characters left. Top it up, then try again."), 400
            return jsonify(error=f"ElevenLabs couldn't read the check line (HTTP {r.status_code})."), 400
        if sample is None:
            return jsonify(error="ElevenLabs couldn't read the check line."), 400
        if supplied:
            write_provider("ELEVENLABS_API_KEY", key)
        cfg["narration"] = {"provider": "original"}
        cr_bridge.save_settings(cfg)
        label = "Eleven v4" if used == "eleven_v4" else "Eleven v3"
        return jsonify(saved=True, model=used, sample="data:audio/mpeg;base64," + base64.b64encode(sample).decode(),
                       message=f"ElevenLabs connected: narration now uses {label} from your ElevenLabs account. "
                               "Pick any voice from its library in Create → Choose voice.")

    # Narration voice picker: list the voices of the narration model in use,
    # play a short preview (made once with the creator's own account, then
    # kept), and choose one without touching keys or accounts.
    VOICE_SAMPLE = "Every great story starts with a single question. Here is what really happened."
    VOICE_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")

    def narration_in_use():
        voice = cr_media.profile("narration") if cr_media.active("narration") else {}
        provider = voice.get("provider")
        if provider not in cr_media.PROVIDERS:
            return None
        model_id = voice.get("model") or cr_media.PROVIDERS[provider]["models"]["narration"]
        model = cr_media.narration_model(provider, model_id)
        return {"provider": provider, "model": model, "voice": voice.get("voice") or model["default_voice"]}

    def preview_file(model, voice):
        folder = HOME / "output" / ".cr-voice-previews" / model["id"].replace("/", "_")
        return folder / (voice + "." + model.get("format", "mp3"))

    @studio.app.get("/cr-voices")
    def voices_list():
        try:
            current = narration_in_use()
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 409
        if not current:
            return jsonify(connected=False, voices=[], previews=[])
        model, provider = current["model"], current["provider"]
        names = list(model.get("voice_names") or [])
        return jsonify(connected=True, provider=provider, provider_label=cr_media.PROVIDERS[provider]["label"],
                       model=model["id"], model_label=model.get("label", model["id"]), voice=current["voice"],
                       default_voice=model["default_voice"], voices=names,
                       previews=[name for name in names if preview_file(model, name).is_file()])

    @studio.app.post("/cr-voices/select")
    def voices_select():
        voice = str((request.get_json(silent=True) or {}).get("voice", "")).strip()
        if activity.busy():
            return jsonify(error="Finish the video that's running, then change the voice for your next one."), 409
        try:
            current = narration_in_use()
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 409
        if not current:
            return jsonify(error="Connect a voice account in Setup & accounts first."), 409
        names = current["model"].get("voice_names") or []
        if not VOICE_NAME.fullmatch(voice) or (names and voice not in names):
            return jsonify(error="Choose a voice from the list."), 400
        cfg = cr_bridge.load_settings()
        if (cfg.get("narration") or {}).get("provider") == current["provider"]:
            cfg["narration"] = {**cfg["narration"], "voice": voice}
        elif (cfg.get("media_hub") or {}).get("provider") == current["provider"]:
            cfg["media_hub"] = {**cfg["media_hub"], "voice": voice}
        else:
            return jsonify(error="Choose your narration account in Setup & accounts first."), 409
        cr_bridge.save_settings(cfg)
        return jsonify(saved=True, voice=voice, message="Your next videos use the " + voice + " voice.")

    @studio.app.post("/cr-voices/preview")
    def voices_preview():
        voice = str((request.get_json(silent=True) or {}).get("voice", "")).strip()
        try:
            current = narration_in_use()
            if not current:
                return jsonify(error="Connect a voice account in Setup & accounts first."), 409
            model = current["model"]
            names = model.get("voice_names") or []
            if not VOICE_NAME.fullmatch(voice) or (names and voice not in names):
                return jsonify(error="Choose a voice from the list."), 400
            target = preview_file(model, voice)
            if not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                cr_media.run("narration", cr_media.narration_input(VOICE_SAMPLE, voice, model["id"]), target,
                             model=model["id"], log=studio.app.logger.info, timeout_s=180)
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 409
        except Exception:
            studio.app.logger.exception("Voice preview failed")
            return jsonify(error="That preview couldn't be made right now. Try again in a moment."), 502
        return jsonify(url="/cr-voices/sample/" + voice)

    @studio.app.get("/cr-voices/sample/<voice>")
    def voices_sample(voice):
        from flask import send_file
        try:
            current = narration_in_use()
        except cr_media.AccountStop:
            current = None
        if not current or not VOICE_NAME.fullmatch(voice) or not preview_file(current["model"], voice).is_file():
            return jsonify(error="No preview yet."), 404
        response = send_file(preview_file(current["model"], voice), conditional=True)
        response.headers["Cache-Control"] = "private, max-age=86400"
        return response

    @studio.app.post("/cr-hub")
    def save_hub():
        data = request.get_json(silent=True) or {}
        cfg = cr_bridge.load_settings()
        if data.get("provider") == "original":
            cfg.pop("media_hub", None)
            cr_bridge.save_settings(cfg)
            return jsonify(saved=True, message="Using your individual studio accounts.")
        provider = data.get("provider")
        key = str(data.get("key", "")).strip()
        tasks = data.get("tasks")
        voice = str(data.get("voice", "")).strip()
        if (provider not in ("kie", "atlas") or not 16 <= len(key) <= 512 or any(c.isspace() for c in key)
                or not isinstance(tasks, list) or not tasks or any(not isinstance(t, str) or t not in cr_media.TASKS for t in tasks)
                or (voice and (len(voice) > 100 or not all(c.isalnum() or c in "_-" for c in voice)))):
            return jsonify(error="Choose a model account, its tasks and a complete API key."), 400
        if not cfg.get("key") or not cfg.get("costs_accepted"):
            return jsonify(error="Connect CR and review the spending agreement first."), 409
        try:
            info = cr_media.PROVIDERS[provider]
            if data.get("use_for_brain") is True:
                # Explicit form consent: the same key is encrypted in CR for brain calls.
                if not data.get("model"):
                    return jsonify(error="Choose your brain models before replacing the current brain account."), 400
                cr_bridge.call("brain-key", {"provider": provider, "api_key": key,
                                            "model": data["model"], "visual_model": data.get("visual_model")})
            else:
                # Check the media account without generating content. Never send it to CR.
                check_media_account(provider, key)
            write_provider(info["key_env"], key)
            cfg["media_hub"] = {"provider": provider, "tasks": sorted(set(tasks)), "voice": voice or info["default_voice"]}
            if data.get("use_for_brain") is True:
                cfg["brain_mode"]="channelrecipe"
                cfg.pop("setup_proof",None)
                cr_claude.uninstall()
            cr_bridge.save_settings(cfg)
            if data.get("use_for_brain") is True:
                cr_bridge.install(studio.mv)
        except cr_media.AccountStop as e:
            return jsonify(error=str(e)), 400
        except Exception:
            return jsonify(error="This account could not be connected. Check its key and API access, then try again."), 400
        return jsonify(saved=True, message=info["label"] + " connected. Selected media tasks use this account." +
                       (" Brain calls also use it through CR." if data.get("use_for_brain") is True else " Your brain account is unchanged."))

    @studio.app.post("/cr-provider")
    def save_provider():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", ""))
        allowed = {"ELEVENLABS_API_KEY", "ALGROW_API_KEY", "WAVESPEED_API_KEY", "KIE_API_KEY", "ATLASCLOUD_API_KEY", "PEXELS_API_KEY", "GEMINI_API_KEY", "EPIDEMIC_API_KEY", "AI33_API_KEY", "SIRAY_API_KEY", "BRAVE_API_KEY"}
        value = str(data.get("key", "")).strip()
        if name not in allowed or not value or any(ch.isspace() for ch in value):
            return jsonify(error="Choose an account and paste its complete key."), 400
        write_provider(name, value)
        return jsonify(saved=True, message="Saved on this computer. This checks the format; the provider verifies it when used.")

    # Legacy editor/control pages keep their existing navigation and token adapter.
    # The independent workspace owns its shell and local-token client already.
    @studio.app.after_request
    def inject(response):
        if response.headers.get("X-CR-Workspace") == "1":
            return response
        if response.mimetype == "text/html" and request.path != "/cr-setup" and not response.direct_passthrough:
            narration=None
            if request.path=="/" and cr_media.active("narration"):
                voice=cr_media.profile("narration")
                provider=voice.get("provider")
                if provider in cr_media.PROVIDERS:
                    info=cr_media.PROVIDERS[provider]
                    model=cr_media.narration_model(provider,voice.get("model"))
                    narration={"provider":provider,"label":info["label"],"model":model["id"],
                        "model_label":model.get("label",model["id"]),"voice":voice.get("voice") or model["default_voice"],
                        "connected":bool(os.getenv(info["key_env"]))}
            preset=None
            if request.path=="/" and request.args.get("cr-preset"):
                try:
                    name=request.args["cr-preset"]
                    studio.styles.load_all()
                    entry=next((s for s in studio.styles.catalogue() if s["name"]==name),None)
                    if entry:
                        preset={"style":name,"tab":entry.get("tab","production")}
                        try:
                            checked=json.loads((HOME/".cr-setup-proof.json").read_text())
                            if checked.get("style")==name and checked.get("setup",{}).get("local_version")==json.loads((HOME/"cr-release.json").read_text())["version"]:
                                preset["features"]=checked.get("features",{})
                        except (OSError,ValueError,KeyError):
                            pass
                except (OSError,ValueError,KeyError):
                    pass
            text=cr_ui.transform_html(response.get_data(as_text=True),narration=narration,preset=preset)
            patch = "<script>const CR_LOCAL_TOKEN=" + json.dumps(token) + ";const crFetch=window.fetch.bind(window);window.fetch=(url,opts={})=>{if(new URL(url instanceof Request?url.url:url,location.href).origin===location.origin){const h=new Headers(opts.headers||(url instanceof Request?url.headers:undefined));h.set('X-CR-Local-Token',CR_LOCAL_TOKEN);opts={...opts,headers:h}}return crFetch(url,opts)};</script>"
            text = text.replace("</head>", patch + "</head>", 1)
            text = text.replace("<body>", '<body><a href="/cr-setup" style="display:block;background:#171B2E;color:#EDEEF5;padding:12px 24px;font:14px system-ui;text-decoration:none">ChannelRecipe · Setup &amp; accounts</a>', 1)
            if request.path == "/":
                text=text.replace("</body>",(HOME/"cr_renderer_panel.html").read_text()+"</body>",1)
                cancel_panel = HOME / "cr_video_cancel.html"
                if cancel_panel.is_file():
                    text=text.replace("</body>",cancel_panel.read_text()+"</body>",1)
                guard = """<style>#farm.cr-native-farm #farmSetup,#farm.cr-native-farm #farmBody{display:none!important}</style>
<script>document.addEventListener('DOMContentLoaded',()=>{
 const go=document.getElementById('go');if(!go)return;
 let ready=false,settled=false,nativeFarm=false,farmRequested=false,wasBlocked=true;
 let message='Checking ChannelRecipe setup…';
 const note=document.createElement('p');note.id='cr-generation-readiness';note.style.cssText='color:#e8a13c;font:14px system-ui;line-height:1.6';
 go.insertAdjacentElement('beforebegin',note);go.disabled=true;
 const farm=document.getElementById('farm'),farmToggle=document.getElementById('f_on');
 function drawGate(){
  const blocked=!ready||(nativeFarm&&farmRequested);
  if(blocked)go.disabled=true;else if(wasBlocked)go.disabled=false;
  wasBlocked=blocked;
  note.textContent=ready&&nativeFarm&&farmRequested?'Uncheck Cloud render below to make this video on your computer with Claude Code.':message;
  if(settled&&!ready){const link=document.createElement('a');link.href='/cr-setup';link.textContent=' Open setup';note.append(link);}
 }
 function drawNativeFarm(){
  if(!nativeFarm||!farm||!farmToggle)return;
  farm.classList.add('cr-native-farm');farm.classList.remove('hidden');
  farmToggle.checked=farmRequested;farmToggle.disabled=!farmRequested;
  const why=document.getElementById('farmWhy');if(why)why.textContent=farmRequested?'Cloud render is selected, but your Claude Code login runs on this computer. Uncheck this option to continue locally.':'Cloud render is unavailable with your local Claude Code account. Your computer renders the video; no farm accounts are required.';
  go.textContent='Create video';drawGate();
 }
 function connectNativeFarm(){
  if(!farm||!farmToggle)return;
  nativeFarm=true;farmRequested=farmToggle.checked;
  try{farmRequested=farmRequested||!!localStorage.getItem('fr.farm');}catch(error){}
  // Original polling calls this layout function after updating its farm state.
  // Preserve the user's selection until their existing change handler saves it.
  if(typeof window.farmBody==='function')window.farmBody=drawNativeFarm;
  farmToggle.addEventListener('change',()=>{farmRequested=farmToggle.checked;drawNativeFarm();},true);
  drawNativeFarm();
 }
 fetch('/cr-status').then(response=>response.json()).then(data=>{
  ready=data.readiness?.ready===true;settled=true;
  message=ready?(data.brain_mode==='claudecode'&&data.claude_code?.allowance?.reported_limit?data.claude_code.allowance.message:'Your setup is connected. The selected preset and options will be checked before generation.'):'Finish Setup & accounts before generating a video.';
  if(data.brain_mode==='claudecode')connectNativeFarm();drawGate();
 }).catch(()=>{settled=true;message='Reconnect ChannelRecipe in Setup & accounts before generating.';drawGate();});
 go.addEventListener('click',event=>{if(!ready||(nativeFarm&&farmRequested)){event.preventDefault();event.stopImmediatePropagation();drawGate();if(!ready)location.href='/cr-setup';}},true);
 drawGate();
});</script>"""
                text=text.replace("</body>",guard+"</body>",1)
            response.set_data(text)
        return response

    # Project context is set per dispatched title, so batches and resumed jobs
    # retain their own receipts rather than billing every title to the first one.
    original_run = studio._run_spec
    def run_owned_spec(spec):
        title = str(spec.get("title") or "Your video")
        style = str(spec.get("style") or "jung")
        try:
            if spec.get("timeline") and spec.get("job"):
                job = Path(spec["job"])
                if hasattr(studio.mv, "find_job"):
                    found = studio.mv.find_job(spec["slug"])
                    if found is None or Path(found).resolve() != job.resolve():
                        raise RuntimeError("Use this timeline's saved video folder.")
            else:
                job = studio.mv.job_dir(title, style)
            marker = job / "cr-project.json"
            previous = json.loads(marker.read_text()) if marker.is_file() else {}
            if spec.get("timeline"):
                for key in ("render_backend", "manimgx_identity"):
                    if key in (previous.get("job_spec") or {}):
                        spec[key] = previous["job_spec"][key]
            old_backend = (previous.get("job_spec") or {}).get("render_backend", "original")
            if previous and old_backend != spec.get("render_backend", "original"):
                raise RuntimeError("This saved video uses a different renderer. Keep its pinned renderer, or use a new title for a new project.")
            cfg=cr_bridge.load_settings()
            if old_backend == "manimgx" and cfg.get("brain_mode") != "claudecode":
                raise RuntimeError("This saved Fast mode project uses your Claude Code brain. Reconnect its pinned Claude profile before editing or exporting it.")
            setup=cfg.get("setup_proof")
            cr_bridge.install(studio.mv)
            if cfg.get("brain_mode") == "claudecode":
                pending = job / ".cr-finish-pending.json"
                if pending.is_file():
                    # Settle the previous attempt before reopening its session.
                    # A background recovery poll must not close live new work.
                    sync_completion_receipt(cr_bridge, pending)
                purpose="creative" if spec.get("timeline") else "video"
                if spec.get("timeline"):
                    with studio.app.app_context():
                        setup=cr_readiness.proof(setup_status().get_json(),purpose=purpose)
                resume=previous.get("id") if previous.get("brain_mode")=="claudecode" else None
                if resume and not (spec.get("cr_project_id") or spec.get("force") or spec.get("timeline")):
                    # A new user request can reuse the engine's local media
                    # caches, but cannot execute new stages on a final session.
                    # Query the live record: the on-disk begin marker may still
                    # say running after its successful completion was recorded.
                    if cr_bridge.project_progress(resume)["project"]["status"] == "done":
                        resume = None
                project=cr_bridge.begin_project(title,style,spec.get("cr_project_id") or (None if spec.get("force") or spec.get("timeline") else resume),
                    proof=setup,job_spec={**spec,"minutes":max(1,int(spec.get("minutes") or 1))},purpose=purpose)
            else:
                project = cr_bridge.begin_project(title, style, None if spec.get("force") else previous.get("id"))
            job.mkdir(parents=True, exist_ok=True)
            import cr_attempts
            cr_attempts.reserve_output(studio.mv, job, spec)
            marker.write_text(json.dumps({**project,"brain_mode":cfg.get("brain_mode","channelrecipe")}, indent=2))
        except Exception as error:
            with studio.LOCK:
                studio.JOBS.setdefault(spec["slug"], {}).update(state="error", error=str(error), done=False)
                studio.RUNNING["slug"] = None
            return
        deferred = DeferredRender(activity, spec, setup, cfg.get("brain_mode", "channelrecipe")) if spec.get("timeline") else None
        render_token = None if deferred else activity.reserve()
        receipt_saved = False
        try:
            dispatch = original_run
            if deferred:
                dispatch = FunctionType(original_run.__code__, {**original_run.__globals__, "timeline":deferred},
                                        original_run.__name__, original_run.__defaults__, original_run.__closure__)
            if video_workers.supports(spec):
                result=video_workers.run(spec,project["id"])
            else:
                result=dispatch(spec)
            if cfg.get("brain_mode") == "claudecode":
                # Only reconcile receipts already saved by the stopped worker.
                # A killed/uncertain stage remains authorized for explicit
                # recovery, never completed or silently retried here.
                cr_claude.sync_receipts(cr_bridge)
                state=studio.JOBS.get(spec["slug"],{})
                artifacts=[]
                if state.get("state") == "done":
                    artifacts=output_artifacts(studio.mv,job)
                complete=bool(artifacts) and state.get("state")=="done"
                if "video" in spec.get("steps",[]):
                    complete=complete and studio.mv.final_video(job).is_file()
                try:
                    report={"project_id":project["id"],"status":"done" if complete else "failed",
                        "artifacts":artifacts,"error_code":"" if complete else "cancelled" if state.get("state")=="cancelled" else "runtime_failed"}
                    pending=job/".cr-finish-pending.json"
                    cr_claude._private_write(pending,report)
                    cr_bridge.finish_project(report)
                    pending.replace(job/".cr-finish.json")
                    receipt_saved=True
                except Exception as error:
                    detail = str(error) if isinstance(error, cr_mcp.WorkflowError) else "CR could not save its completion record. Check your connection, then sync in Setup."
                    state.setdefault("lines", []).append("Your local output is saved. " + detail)
            else:
                receipt_saved=True
            return result
        finally:
            if deferred:
                deferred.close(receipt_saved)
            else:
                activity.release(render_token)
    def run_spec(spec):
        activity.wait_for_editor()
        import cr_attempts
        queue_keeper.started(spec)
        try:
            with cr_attempts.bind(studio.mv, spec):
                try:
                    return run_owned_spec(spec)
                finally:
                    # Inside the attempt's binding, so job_dir names this attempt's folder (its short/error.json).
                    queue_keeper.finished(spec, lambda: studio.mv.job_dir(str(spec.get("title") or "Your video"),
                                                                          str(spec.get("style") or "jung")))
        except Exception as error:
            with studio.LOCK:
                studio.JOBS.setdefault(spec["slug"], {}).update(state="error", error=str(error), done=True)
                studio.RUNNING["slug"] = None
    studio._run_spec = run_spec
    import cr_3d_routes
    cr_3d_routes.install(studio, activity, setup_status)
    import cr_style_routes
    cr_style_routes.install(studio, activity, setup_status, token=token, home=HOME)
    import cr_ranking_routes
    cr_ranking_routes.install(studio, token=token, home=HOME)
    import cr_optional_renderer
    cr_optional_renderer.install(studio, activity, home=HOME, readiness=setup_status)
    import cr_workspace
    cr_workspace.install(studio, activity, token=token, home=HOME)
    import cr_style_options
    cr_style_options.install(studio)
    return studio


def prefer_studio_ytdlp():
    """Point the engine at the yt-dlp installed with Studio, unless the creator chose one.

    An older yt-dlp elsewhere on PATH often can't read today's YouTube pages."""
    configured = os.environ.get("YTDLP_BIN")
    if not configured:
        try:
            from dotenv import dotenv_values
            configured = dotenv_values(HOME / ".env").get("YTDLP_BIN")
        except Exception:
            configured = None
    if configured:
        return
    scripts = Path(sys.executable).parent
    for name in ("yt-dlp.exe", "yt-dlp"):
        if (scripts / name).is_file():
            os.environ["YTDLP_BIN"] = str(scripts / name)
            return


# YouTube answers a burst of requests from one computer with "Sign in to confirm you're not a bot" and then refuses
# every footage search and download for hours. A pause between requests and fewer downloads at once make that rare;
# a creator's own YT_SLEEP / YT_WORKERS in .env still win (the engine reads .env over these).
POLITE_YOUTUBE = {"YT_SLEEP": "1", "YT_WORKERS": "3"}


def polite_youtube(reach=None):
    for key, value in POLITE_YOUTUBE.items():
        os.environ.setdefault(key, value)
    # YouTube scores IPv6 addresses harder (a whole /64 counts as one): on 2026-10-15 a computer it refused over IPv6
    # was let through over IPv4 at once. Asked over IPv4 whenever this computer reaches YouTube that way.
    if "YT_FORCE_IPV4" not in os.environ and (reach or _youtube_over_ipv4)():
        os.environ["YT_FORCE_IPV4"] = "1"


def _youtube_over_ipv4(timeout=2.0) -> bool:
    import socket
    try:
        for family, kind, proto, _, address in socket.getaddrinfo("www.youtube.com", 443, socket.AF_INET,
                                                                    socket.SOCK_STREAM)[:2]:
            with socket.socket(family, kind, proto) as sock:
                sock.settimeout(timeout)
                sock.connect(address)
                return True
    except OSError:
        pass
    return False


def already_running():
    """True when a ChannelRecipe Studio already answers on this computer."""
    import urllib.request
    try:
        with urllib.request.urlopen(STUDIO_URL + "/cr-status", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def banner():
    line = "=" * 64
    print("\n".join(["", line, "  ChannelRecipe Studio is running.",
                     "  Open it in your browser: " + STUDIO_URL,
                     "  Keep this window open while you use Studio.",
                     "  To stop Studio, close this window.", line, ""]), flush=True)


SESSION = Path("output") / ".cr-studio-session.json"


def _cut_off(home, since: float) -> list:
    """Titles of the videos a Studio was making when it closed: their worker's last progress, saved after it
    started, still says running."""
    titles = []
    for progress in (Path(home) / "output" / "cr-video-workers").glob("video_*/progress.json"):
        try:
            if progress.stat().st_mtime < since:
                continue
            job = json.loads(progress.read_text(encoding="utf-8")).get("job") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(job, dict) and job.get("state") == "running" and not job.get("done"):
            titles.append(str(job.get("title") or "a video"))
    return titles


def session_started(home=HOME) -> str:
    """Note this Studio as running; a plain note when the previous one closed in the middle of a video.

    A Studio that stops itself (Ctrl+C, an update, an error) records how. One that never did was closed from
    outside: its window (the usual way to stop it), Task Manager, Windows signing out, or a program that started
    Studio and then ended (a Claude Code command that finished or timed out closes everything it started). That only
    matters when a video was being made: it stopped with Studio."""
    path = Path(home) / SESSION
    note = ""
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
        cut = _cut_off(home, float(last.get("started") or 0)) if isinstance(last, dict) and not last.get("ended") else []
        if cut:
            note = ("Last time, Studio closed while it was making \"%s\": its window was closed, Task Manager ended "
                    "it, or the program that started it ended (for example a Claude Code command). That video "
                    "stopped with it; its saved work is kept. Keep Studio's launcher window open until a video "
                    "finishes." % "\", \"".join(cut[:3]))
    except (OSError, ValueError, TypeError):
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")
    except OSError:
        pass
    return note


def session_stopped(how: str, home=HOME):
    path = Path(home) / SESSION
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("pid") == os.getpid():
            record.update(ended=time.time(), how=how)
            path.write_text(json.dumps(record), encoding="utf-8")
    except (OSError, ValueError, AttributeError):
        pass


def main():
    import logging
    if not isinstance(sys.stdout, _RecentOutput):
        sys.stdout, sys.stderr = _RecentOutput(sys.stdout), _RecentOutput(sys.stderr)
    # Every page poll printed a line; keep the window for what matters.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    import cr_update
    if cr_update.studio_running():
        if already_running():
            print("ChannelRecipe Studio is already running in another window. Opening it in your browser.", flush=True)
            if not os.environ.get("CR_RESTARTED_FOR_UPDATE"):
                webbrowser.open(STUDIO_URL + "/")
            return
        print("Another program is using Studio's address (port 7860). Close it, or restart your computer, "
              "then open Studio again.", flush=True)
        raise SystemExit(1)
    prefer_studio_ytdlp()
    polite_youtube()
    try:
        # Updates from older releases ran an updater that could not remove
        # retired files, so tidy them on start too.
        import cr_update
        cr_update.retire(HOME)
    except Exception:
        pass
    studio = create_app()
    studio.OUT.mkdir(exist_ok=True)
    studio._ensure_dispatcher()
    studio.timeline.start_worker(busy=studio._cr_activity.rendering,
                                on_idle=lambda job: studio._enqueue_tl_render(Path(job), Path(job).name))
    def open_page():
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:7860/cr-setup")
    if not os.environ.pop("CR_RESTARTED_FOR_UPDATE", ""):
        threading.Thread(target=open_page, daemon=True).start()
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 7860, studio.app, threaded=True)
    studio._cr_server = server
    banner()
    note = session_started()
    if note:
        print(note + "\n", flush=True)
    how = "error"
    try:
        server.serve_forever()
        how = "update" if getattr(studio, "_cr_restart_for_update", False) else "stopped"
    except KeyboardInterrupt:
        how = "ctrl-c"
        raise
    finally:
        session_stopped(how)
        server.server_close()
    if getattr(studio, "_cr_restart_for_update", False):
        restart_into_update()


def restart_into_update():
    """Install the staged update and start the new Studio in this same window."""
    import cr_update
    print("\nInstalling the update…", flush=True)
    try:
        # A Windows launcher reads its own file while Studio runs; never rewrite it then.
        keep = {"Start on Windows.cmd"} if os.name == "nt" else set()
        result = cr_update.apply_staged(HOME, keep=keep)
        print("Updated to ChannelRecipe Studio %s." % cr_update._build(result["version"]), flush=True)
        if result["requirements_changed"]:
            print("Installing updated libraries. This can take a minute…", flush=True)
            if (subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(HOME / "requirements.txt")]) == 0
                    and subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"]) == 0):
                (HOME / ".venv" / ".cr-installed").touch()
    except cr_update.UpdateStopped as stopped:
        print(stopped, flush=True)
    except Exception as error:
        print("The update couldn't finish (%s). Your previous version was kept." % error, flush=True)
    os.environ["CR_RESTARTED_FOR_UPDATE"] = "1"
    command = [sys.executable, str(HOME / "cr_studio.py")]
    if os.name == "nt":
        # Stay as the launcher's process so its window waits for the new Studio.
        raise SystemExit(subprocess.call(command, cwd=str(HOME)))
    os.chdir(HOME)
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
