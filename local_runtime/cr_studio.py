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
import cr_cloud
import cr_capabilities
import cr_media
import cr_render
import cr_claude
import cr_failure
import cr_installs
import cr_machine
import cr_mcp
import cr_network
import cr_paths
import cr_readiness
import cr_workers
import cr_ui
import cr_submission
import cr_whisper
import hashlib
import re

HOME = Path(__file__).resolve().parent
CR_URL = "https://channelrecipe.com"
STUDIO_URL = "http://127.0.0.1:7860"
# The black launcher window isn't saved anywhere, so keep its recent lines
# for Copy diagnostics (secrets are hidden when they're copied).
import collections
RECENT_OUTPUT = collections.deque(maxlen=400)

# A Studio started from inside a Claude Code chat inherits that chat's variables. CLAUDE_EFFORT=xhigh then makes every
# `claude -p` step Studio runs think at extra-high effort: a 3D review round went past half an hour and steps overran
# CC_TIMEOUT, so a 2-minute video took hours. The chat's ids and sockets also point at a session that may be gone.
# Studio's own Claude steps run on the customer's normal settings, so these are dropped when Studio starts. The login
# (CLAUDE_CODE_OAUTH_TOKEN) and the customer's own configuration (Bedrock, Vertex, Git Bash path, output limits) stay.
_CHAT_ONLY = {"CLAUDE_EFFORT", "CLAUDECODE", "CLAUDE_PID", "CLAUDE_PREVIEW_CLASSIFIER_FLOOR",
              "MCP_CONNECTION_NONBLOCKING", "MCP_SERVER_CONNECTION_BATCH_SIZE"}
_CHAT_PREFIXES = ("CLAUDE_AGENT_SDK_", "CLAUDE_CODE_SESSION", "CLAUDE_CODE_HOST_", "CLAUDE_CODE_CHILD_",
                  "CLAUDE_CODE_MESSAGING_", "CLAUDE_CODE_DESKTOP_", "CLAUDE_CODE_SDK_")
_CHAT_CODE = {"ENTRYPOINT", "SIMPLE", "EXECPATH", "TERMINAL_MCP_TOOLS", "STARTUP_FAILURE_RESULTS", "REPORT_FINDINGS",
              "EMIT_TOOL_USE_SUMMARIES", "ENABLE_ASK_USER_QUESTION_TOOL", "ENABLE_SDK_FILE_CHECKPOINTING", "EAGER_FLUSH",
              "DISABLE_CRON", "DISABLE_TERMINAL_TITLE", "OAUTH_SCOPES", "ACCOUNT_UUID", "ORGANIZATION_UUID", "USER_EMAIL"}
CHAT_SETTINGS_DROPPED = []


def chat_line() -> str:
    """For Copy diagnostics: whether this Studio was started from inside a Claude Code chat."""
    if not CHAT_SETTINGS_DROPPED:
        return "no"
    effort = [x for x in CHAT_SETTINGS_DROPPED if x.startswith("CLAUDE_EFFORT=")]
    return "yes, its settings were left out" + (" (%s)" % effort[0] if effort else "")


def forget_claude_chat(environ=None) -> list:
    """Drop the variables a Claude Code chat hands its child processes; -> what was dropped (names, plus the effort
    level the chat asked for, never a value that could be personal)."""
    environ = os.environ if environ is None else environ
    gone = []
    for name in sorted(environ):
        if (name in _CHAT_ONLY or name.startswith(_CHAT_PREFIXES)
                or (name.startswith("CLAUDE_CODE_") and name[len("CLAUDE_CODE_"):] in _CHAT_CODE)):
            gone.append("CLAUDE_EFFORT=%s" % environ[name][:12] if name == "CLAUDE_EFFORT" else name)
            environ.pop(name, None)
    return gone


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
        """A queued title must not replace a still-running editor's project, nor start beside a style study, a 3D
        repair, a product build or a farm render: queued behind them, it starts when they finish."""
        with self.changed:
            self.waiting_editor = True
            try:
                while self.active or self.editor_queue_busy() or self.side_work():
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

    def side_work(self):
        """A product build or a farm render is going (neither holds a reservation)."""
        product = getattr(self.studio, "PRODUCT_BUILD", {}).get("proc")
        if product is not None and product.poll() is None:
            return True
        farm = getattr(self.studio, "FARM", {}).get("thread")
        return bool(farm is not None and farm.is_alive())

    def busy(self):
        with self.lock:
            if self.active or self.studio.RUNNING.get("slug") or self.studio.QUEUE or cr_workers.busy():
                return True
            if self.editor_queue_busy():
                return True
            return self.side_work()

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
    if path == "/api/farm/kill" or path.startswith(("/api/farm/fetch/", "/api/farm/forget/")):
        return False                        # stopping, collecting or deleting a farm video starts nothing
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
    """Can the graphics browser draw? With the reason when it can't, so setup (and the creator's Claude Code) can say
    what to do instead of a bare "needs attention". A working browser is checked again in five minutes; a broken one
    every time, so a fix shows at once (a cached failure used to look like the fix hadn't worked)."""
    if _graphics_check.get("expires",0)>time.monotonic():
        return _graphics_check["result"]
    result={"browser":False,"webgl":False,"browser_reason":""}
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        result["browser_reason"]="The graphics browser's library isn't installed. Press Fix it next to Graphics browser."
    else:
        try:
            with sync_playwright() as p:
                if not Path(p.chromium.executable_path).is_file():
                    result["browser_reason"]="The graphics browser isn't installed yet. Press Fix it to install it (a few minutes)."
                else:
                    browser=p.chromium.launch(headless=True,args=["--enable-unsafe-swiftshader"])
                    page=browser.new_page()
                    result.update(page.evaluate("""() => ({browser:!!document.createElement('canvas').getContext('2d'),webgl:!!document.createElement('canvas').getContext('webgl2')})"""))
                    browser.close()
                    if not result.get("browser"):
                        result["browser_reason"]="The graphics browser started but couldn't draw. Press Fix it to reinstall it."
        except Exception as error:
            first=(str(error).strip().splitlines() or [type(error).__name__])[0][:160]
            result["browser_reason"]=f"The graphics browser won't start ({first}). Press Fix it to reinstall it."
    _graphics_check.update(result=result,expires=time.monotonic()+(300 if result.get("browser") else 0))
    return result


BROWSER_FIX = {"state": "idle", "tail": ""}
_browser_fix_lock = threading.Lock()


def fix_graphics_browser():
    """Install (or reinstall) the graphics browser into this Studio's own Python, in the background. The launcher used
    to do this only on its very first run, so "reopen the launcher" never repaired a missing browser."""
    with _browser_fix_lock:
        if BROWSER_FIX["state"] == "installing":
            return dict(BROWSER_FIX)
        BROWSER_FIX.update(state="installing", tail="")

    def work():
        try:
            run = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
            ok, tail = run.returncode == 0, ((run.stdout or "") + (run.stderr or ""))[-1500:]
        except Exception as error:
            ok, tail = False, f"{type(error).__name__}: {error}"[:500]
        _graphics_check.clear()
        ready = bool(ok and graphics_ready().get("browser"))
        BROWSER_FIX.update(state="done" if ready else "failed",
                           tail="" if ready else (tail or (graphics_ready().get("browser_reason") or "")))
    started = dict(BROWSER_FIX)
    threading.Thread(target=work, daemon=True, name="cr-fix-browser").start()
    return started


def network_needs(speech: dict) -> dict:
    """What this Studio needs to reach: Kie unless another media account replaced it, ElevenLabs with a key, and
    huggingface.co only while the speech-timing model isn't on this computer."""
    try:
        hub = cr_media.profile() or {}
    except Exception:                                          # noqa: BLE001
        hub = {}
    return cr_network.wants_from_env(speech_model_missing=(speech or {}).get("state") in ("missing", "downloading",
                                                                                          "broken"),
                                     hub_provider=str(hub.get("provider") or ""))


def channelrecipe_answers(cfg, network, *, head=None) -> bool:
    """False only when the network check just found channelrecipe.com unreachable and one quick request agrees."""
    row = next((h for h in (network or {}).get("hosts") or [] if h.get("host") == cr_network.VIA_CR), None)
    if row is None or row.get("reachable"):
        return True
    import requests
    try:
        (head or requests.head)(str(cfg.get("url") or "https://channelrecipe.com").rstrip("/") + "/api/health",
                                timeout=(5, 5), allow_redirects=False)
        return True
    except requests.RequestException:
        return False


def fix_speech_model():
    """Setup's Fix it for the speech-timing model: download it (again, when it's there but won't load) and load it
    once, in the background."""
    state = cr_whisper.status().get("state")
    return cr_whisper.ensure(force=state == "broken")


def sync_completion_receipt(bridge, pending):
    """Sync metadata, retaining evidence when an old repeat contradicted success."""
    receipt = json.loads(pending.read_text(encoding="utf-8"))
    saved_path = pending.with_name(".cr-finish.json")
    try:
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
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
    return cr_installs.runs(name, flag)


def video_tools_ready(ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """FFmpeg and ffprobe run, and FFmpeg can burn captions (libass) and make H.264, AAC and MP3 (cr_installs)."""
    return not cr_installs.video_tools_problem(ffmpeg, ffprobe)


def node_ready():
    """Node.js (any version) or Deno runs: what every video needs, as before. Downloading from YouTube needs more
    (Node 22 or newer, node_youtube in tool_checks): cr_readiness.preset() refuses only the videos that do."""
    return tool_ready("node", "--version") or tool_ready("deno", "--version")


def tool_checks():
    """Setup's FFmpeg and Node rows, with what is wrong and the fix (setup's Fix it puts both in Studio's own folder,
    cr_installs). node: a JavaScript runtime runs (a missing one blocks setup, as it always did); node_youtube: yt-dlp
    can use it for YouTube (Deno, or Node.js 22 or newer). An older Node is a note in setup and stops only videos that
    download from YouTube (cr_readiness.preset), never the rest."""
    ffmpeg = "" if video_tools_ready() else (cr_installs.video_tools_problem() or "FFmpeg isn't ready.")
    deno = tool_ready("deno", "--version")
    node = {"ready": True, "version": "deno", "reason": ""} if deno else cr_installs.node_status()
    return {"ffmpeg": not ffmpeg, "ffmpeg_reason": (ffmpeg + " " + cr_installs.fix_hint("ffmpeg")) if ffmpeg else "",
            "node": deno or node["ready"] or node_ready(), "node_youtube": node["ready"], "node_version": node["version"],
            "node_reason": (node["reason"] + " " + cr_installs.fix_hint("node")) if not node["ready"] else "",
            "can_fix": {name: cr_installs.can_fix(name) for name in ("ffmpeg", "node", "claude")}}


def fix_line():
    """Diagnostics: the setup Fix its run since Studio started (what failed and why), '' when none ran."""
    ran = [(name, cr_installs.status(name)) for name in cr_installs.LABEL]
    ran = [(name, fix) for name, fix in ran if fix.get("state") not in ("idle", None)]
    if not ran:
        return ""
    return "Fix it: " + "; ".join("%s %s%s" % (cr_installs.LABEL[name], fix["state"],
                                               (" (" + str(fix.get("message") or "")[:200] + (" | " + str(
                                                   fix.get("tail") or "")[-200:] if fix.get("tail") else "") + ")")
                                               if fix["state"] == "failed" else "") for name, fix in ran)


def configure_video_tools():
    # Setup's Fix it installs FFmpeg and Node.js in Studio's own tools folder: first on PATH for Studio and its workers
    cr_installs.use_studio_tools(HOME)
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
    try:
        if cr_media.migrate_saved_voice():          # a saved Kie ElevenLabs voice: its Gemini voice now, with a note
            print("Narration: Kie now reads with Gemini voices; your saved ElevenLabs voice was moved (see Setup).",
                  flush=True)
    except Exception:                                # noqa: BLE001 - profile() reads it the same way meanwhile
        pass
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
    # "output": where the videos go (CHANNELRECIPE_OUTPUT moves it), so Claude Code's quote measures that disk
    control.write_text(json.dumps({"pid":os.getpid(),"token":token,"port":7860,
                                   "output":str(getattr(studio.mv, "OUTPUT_ROOT", "") or "")}), encoding="utf-8")
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
                # A video started while another is made joins the queue (cr_queue): its disk check counts what the
                # videos before it still need, and its setup proof travels with it (g -> cr_submission).
                checks = setup_status().get_json()
                preflight = cr_readiness.preset(studio,checks,data,queue_keeper.ahead_gb())
                if not preflight["ready"]:
                    return jsonify(error="Complete this video's setup before starting.",**preflight),409
                if preflight.get("manimgx_identity"):
                    data["manimgx_identity"] = preflight["manimgx_identity"]
                    request._cached_json = (data, data)
                g.cr_warnings = preflight.get("warnings") or []   # said with the start (inject), never a stop
                g.cr_setup_proof = preflight["proof"]
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
                if request.path.startswith("/api/farm/") and not cr_cloud.active(HOME):
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
        text = (HOME / "cr_setup.html").read_text(encoding="utf-8")
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
        checks = {"python": sys.version_info >= (3,10), **tool_checks(),
                  "connected": bool(cfg.get("key")), "costs_accepted": bool(cfg.get("costs_accepted"))}
        checks.update(graphics_ready())
        checks["speech_model"] = cr_whisper.status()
        paths = cr_paths.status(HOME)                  # Windows' 260-character limit: room for a video's files
        if paths:
            checks["file_paths"] = paths
        # memory, cores and the free disk the videos go on (CHANNELRECIPE_OUTPUT moves them): a note here, a stop per video
        checks["computer"] = cr_machine.status(HOME, root=getattr(studio.mv, "OUTPUT_ROOT", None))
        # the setup page shows "Checking…" after 3 s and asks again; a check before work waits for the answer
        # inside a request Studio is serving, so it also tries to reach itself (only a block on the computer stops that)
        page = has_request_context() and request.path == "/cr-status"
        checks["network"] = cr_network.status(network_needs(checks["speech_model"]),
                                              wait=3 if page else cr_network.TIMEOUT * 2 + 2,
                                              port=cr_network.STUDIO_PORT if has_request_context() else None)
        try:
            if cfg.get("key") and not channelrecipe_answers(cfg, checks["network"]):
                # the account call retries a lost connection for about 90 s (right for videos): setup says why now
                checks["cr_unreachable"] = True                # and readiness skips the tools check (90 s too)
                raise RuntimeError("Studio can't reach channelrecipe.com from this computer right now. See Network "
                                   "under Make sure the tools are ready.")
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
        if voice.get("provider") == "kie":
            # Kie reads with Gemini voices only; a saved ElevenLabs voice moved to Gemini says so once, plainly
            checks["narration"]["note"] = (cfg.get("voice_notice") or cr_media.kie_move_note(voice)
                                           or cr_media.KIE_NOTE)
        if not checks["narration"]["connected"] and override.get("provider") == "original" and \
                os.getenv("ELEVENLABS_API_KEY", "").strip():
            # Setup's ElevenLabs card: narration on the creator's own ElevenLabs key is connected (it read as not
            # connected, and Create asked to connect a voice account that was already connected).
            checks["narration"].update(connected=True, label="ElevenLabs", direct="elevenlabs")
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
            checks["local_version"] = json.loads((HOME / "cr-release.json").read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            checks["local_version"] = "development"
        readiness = cr_readiness.basic(checks)
        checks["brain_mode"] = readiness["brain_mode"]
        checks["claude_code"] = readiness.pop("claude_code")
        checks["readiness"] = readiness
        try:                                                # where setup is stuck, for the team (check keys only)
            import cr_setup_report
            cr_setup_report.maybe_send(readiness, checks.get("local_version", ""),
                                       connected=bool(cfg.get("key")) and not checks.get("cr_unreachable"))
        except Exception:                                   # noqa: BLE001 - never trouble setup
            pass
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
                proof_file.write_text(json.dumps({"setup":result["proof"],"style":(request.get_json(silent=True) or {}).get("style"),"features":result["features"]},indent=2), encoding="utf-8")
                proof_file.chmod(0o600)
                if cfg.get("brain_mode") == "claudecode":
                    result["workflow"]=cr_bridge.prepare_runtime(result["proof"])["workflow"]
            return jsonify(result)
        except Exception as error:
            return jsonify(error=str(error),ready=False,blockers=[str(error)]),409

    @studio.app.post("/cr-submit-project")
    def submit_project():
        try:
            project_id=str((request.get_json(silent=True) or {}).get("project_id", ""))
            import re
            if not re.fullmatch(r"crv_[a-f0-9]{24}",project_id):
                return jsonify(error="Use a valid saved CR project."),400
            if queue_keeper.has_project(project_id):
                # a saved project resumes while others are made (it joins the queue), once
                return jsonify(error="This video is already being made or waiting in the queue."),409
            progress=cr_bridge.project_progress(project_id)
            if progress.get("purpose", "video") != "video":
                return jsonify(error="This is a setup or creative action. Continue it in its original studio controls, not the saved-video launcher."),409
            spec=progress["job_spec"]
            if progress["project"]["status"] == "done":
                return jsonify(error="This project already finished. Open its saved local output."),409
            preflight=cr_readiness.preset(studio,setup_status().get_json(),spec,queue_keeper.ahead_gb())
            if not preflight["ready"]:
                return jsonify(error="Complete this project's setup before starting.",**preflight),409
            cfg=cr_bridge.load_settings()
            cfg["setup_proof"]=preflight["proof"]
            cr_bridge.save_settings(cfg)
            # Capture original normalized specs before the live dispatcher can
            # consume them, and publish only this project's fully owned items.
            payload={**spec,"steps":spec.get("steps") or ["script","voiceover","pexels","images","video","thumbnail"]}
            response=cr_submission.submit(studio,payload,project_id,setup_proof=preflight["proof"])
            if not 200 <= response.status_code < 300:
                return response
            queued=response.get_json().get("queued",[])
            return jsonify(queued=queued,project_id=project_id,**({"warnings":preflight["warnings"]} if preflight.get("warnings") else {}))
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
                cfg["local_version"]=json.loads((HOME/"cr-release.json").read_text(encoding="utf-8"))["version"]
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
                        # a video in Studio's trash (cr_trash) still tells ChannelRecipe how its run ended: the
                        # replay only renames the receipt beside itself, and Restore moves the folder back whole
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

    @studio.app.post("/cr-setup/fix-browser")
    def fix_browser_route():
        return jsonify(fix_graphics_browser()), 202

    @studio.app.get("/cr-setup/fix-browser")
    def fix_browser_status():
        return jsonify(dict(BROWSER_FIX))

    # Fix it for FFmpeg, Node.js and Claude Code (cr_installs): FFmpeg and Node.js into Studio's own folder, Claude
    # Code with Anthropic's own installer. Each runs in the background; setup polls the GET for its progress line.
    @studio.app.post("/cr-setup/fix-ffmpeg")
    def fix_ffmpeg_route():
        return jsonify(cr_installs.fix_ffmpeg(home=HOME)), 202

    @studio.app.get("/cr-setup/fix-ffmpeg")
    def fix_ffmpeg_status():
        return jsonify(cr_installs.status("ffmpeg"))

    @studio.app.post("/cr-setup/fix-node")
    def fix_node_route():
        return jsonify(cr_installs.fix_node(home=HOME)), 202

    @studio.app.get("/cr-setup/fix-node")
    def fix_node_status():
        return jsonify(cr_installs.status("node"))

    @studio.app.post("/cr-setup/fix-claude")
    def fix_claude_route():
        return jsonify(cr_installs.fix_claude()), 202

    @studio.app.get("/cr-setup/fix-claude")
    def fix_claude_status():
        state = {**cr_installs.status("claude"), "installed": bool(cr_claude.find_executable())}
        if request.args.get("signin") == "1" and state["installed"]:
            state["signed_in"] = bool(cr_claude.status().get("authenticated"))    # after the sign-in window opened
        return jsonify(state)

    @studio.app.post("/cr-setup/claude-sign-in")
    def claude_sign_in_route():
        try:
            return jsonify(cr_installs.open_sign_in(home=HOME))
        except cr_installs.Stop as stop:
            return jsonify(error=str(stop)), 409
        except OSError as error:
            return jsonify(error=f"Studio couldn't open the sign-in window ({type(error).__name__}). Open Terminal "
                                 f"(PowerShell on Windows) and run: {cr_installs.sign_in_command(cr_claude.find_executable())}"), 409

    @studio.app.post("/cr-setup/fix-speech-model")
    def fix_speech_model_route():
        return jsonify(fix_speech_model()), 202

    @studio.app.get("/cr-setup/fix-speech-model")
    def fix_speech_model_status():
        return jsonify({**dict(cr_whisper.FIX), **cr_whisper.status()})

    @studio.app.post("/cr-setup/network")
    def network_recheck():
        """Check again now (after the creator changed network or allowed a host)."""
        cr_network.forget()
        last_status.clear()
        return jsonify(cr_network.status(network_needs(cr_whisper.status()), port=cr_network.STUDIO_PORT))

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
            "Tools: Python %s · FFmpeg %s · graphics browser %s · Node %s%s" % (
                tick(checks.get("python")), tick(checks.get("ffmpeg")), tick(checks.get("browser")), tick(checks.get("node")),
                (" " + str(checks["node_version"])) if checks.get("node_version") else ""),
            fix_line(),
            "ChannelRecipe account connected: %s" % tick(checks.get("connected")),
            "Claude Code: installed %s%s · signed in %s · tools connected %s · models verified %s" % (
                tick(cli.get("installed")), (" " + str(cli["version"])) if cli.get("version") else "",
                tick(cli.get("authenticated")), tick(cli.get("mcp_connected")), tick(cli.get("models_verified"))),
            "Writing route: %s" % checks.get("brain_mode", "unknown"),
            "Started from a Claude Code chat: %s" % chat_line(),
            "Voice and images: %s%s · narration %s" % (hub.get("provider", "none"),
                (" (" + ", ".join(hub.get("tasks", [])) + ")") if hub.get("tasks") else "", tick(voice.get("connected"))),
            cr_network.diagnostics_line(checks.get("network") or {}),
            cr_paths.diagnostics_line(checks.get("file_paths")),
            cr_machine.diagnostics_line(checks.get("computer")),
            "Speech-timing model: %s" % ("ready" if (checks.get("speech_model") or {}).get("ready") else
                                         ((checks.get("speech_model") or {}).get("reason") or "unknown")[:300]),
            "Setup ready: %s" % tick(readiness.get("ready")),
            "Working right now: %s" % running,
        ]
        lines = [line for line in lines if line]          # a check that doesn't apply here says nothing
        blockers = readiness.get("blockers") or []
        if blockers:
            lines.append("Setup still needs: " + "; ".join(str(b.get("message") if isinstance(b, dict) else b) for b in blockers)[:600])
        recent = [line for line in RECENT_OUTPUT if '" 200 -' not in line and '" 304 -' not in line]
        problems = [line for line in recent if any(word in line.lower() for word in ("error", "traceback", "failed", "exception", "stopped"))]
        lines += ["", "Recent problems:"] + (problems[-30:] or ["none"])
        lines += ["", "Recent Studio window output:"] + (recent[-40:] or ["none"])
        try:                                     # the last video worker's own log (why a worker stopped)
            logs = sorted((HOME / "output" / "cr-video-workers").glob("*/worker.log"), key=lambda p: p.stat().st_mtime)
            if logs:
                with open(logs[-1], "rb") as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - 6000))
                    tail = f.read().decode("utf-8", "replace").splitlines()[-40:]
                lines += ["", "Last video worker log:"] + tail
        except OSError:
            pass
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
            cfg.pop("voice_notice", None)
            cr_bridge.save_settings(cfg)
            return jsonify(saved=True, message="Narration uses " +
                           ("the model account choices above." if provider == "inherit" else "your individual voice settings."))
        info = cr_media.PROVIDERS[provider]
        model_id = str(data.get("model", "")).strip() or info["models"]["narration"]
        if provider == "kie" and cr_media.retired_kie_model(model_id):
            model_id = info["models"]["narration"]      # Kie reads with Gemini voices only (cr_media.kie_narration)
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
            cfg.pop("voice_notice", None)
            cr_bridge.save_settings(cfg)
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 400
        except Exception:
            return jsonify(error="This voice account could not be connected. Check its API key and access, then try again."), 400
        return jsonify(saved=True, message="Narration now uses " + info["label"] +
                       (" with Gemini voices" if provider == "kie" else "") +
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
    # one short line on Kie's Gemini TTS (billed by the token, from $0.35 a million; a credit is about $0.005): an upper
    # bound the creator is shown before a paid sample is made
    PREVIEW_COST_USD = 0.01
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
        made = folder / (voice + "." + model.get("format", "mp3"))
        hosted = folder / (voice + ".mp3")                      # ChannelRecipe's own sample, kept on first play
        return hosted if not made.is_file() and hosted.is_file() else made

    def hosted_sample(model, voice, target):
        """ChannelRecipe's free sample of a Gemini voice (channelrecipe.com/static/voices, the same files the account's
        voice_samples list): fetched once and kept, so a preview costs nothing on the creator's balance."""
        import requests
        if not model["id"].startswith("google/gemini-"):
            return None
        base = str(cr_bridge.load_settings().get("url") or "https://channelrecipe.com").rstrip("/")
        if not base.startswith("https://"):
            return None
        try:
            r = requests.get(f"{base}/static/voices/{model['id'].split('/')[-1]}/{voice}.mp3", timeout=(8, 30))
        except requests.RequestException:
            return None
        if r.status_code != 200 or len(r.content) < 2000 or not r.headers.get("content-type", "").startswith("audio"):
            return None
        out = target.with_suffix(".mp3")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        return out

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
        note = ""
        if provider == "kie":
            note = (cr_bridge.load_settings().get("voice_notice") or cr_media.kie_move_note(cr_media.profile("narration"))
                    or cr_media.KIE_NOTE)
        return jsonify(connected=True, provider=provider, provider_label=cr_media.PROVIDERS[provider]["label"],
                       model=model["id"], model_label=model.get("label", model["id"]), voice=current["voice"],
                       default_voice=model["default_voice"], voices=names, groups=model.get("voice_groups") or {},
                       note=note, previews=[name for name in names if preview_file(model, name).is_file()])

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
        if current["provider"] == "kie":
            for slot in ("narration", "media_hub"):             # a voice moved from ElevenLabs: its model goes too
                if (cfg.get(slot) or {}).get("provider") == "kie" and cr_media.retired_kie_model(cfg[slot].get("model")):
                    cfg[slot] = {**cfg[slot], "model": current["model"]["id"]}
        cfg.pop("voice_notice", None)                           # the creator chose: the note has done its job
        cr_bridge.save_settings(cfg)
        return jsonify(saved=True, voice=voice, message="Your next videos use the " + voice + " voice.")

    @studio.app.post("/cr-voices/preview")
    def voices_preview():
        data = request.get_json(silent=True) or {}
        voice = str(data.get("voice", "")).strip()
        try:
            current = narration_in_use()
            if not current:
                return jsonify(error="Connect a voice account in Setup & accounts first."), 409
            model = current["model"]
            names = model.get("voice_names") or []
            if not VOICE_NAME.fullmatch(voice) or (names and voice not in names):
                return jsonify(error="Choose a voice from the list."), 400
            target, paid = preview_file(model, voice), False
            if not target.is_file() and hosted_sample(model, voice, target) is None:
                # No free sample of this voice could be fetched (channelrecipe.com unreachable or slow): a short line
                # with the creator's own balance, only after they say yes to its cost, then kept and replayed free.
                label = cr_media.PROVIDERS[current["provider"]]["label"]
                if data.get("confirm_paid") is not True:
                    return jsonify(needs_paid=True, voice=voice, cost_usd=PREVIEW_COST_USD,
                                   message=f"Free sample unavailable: make one with your {label} balance (about "
                                           f"${PREVIEW_COST_USD:.2f})?")
                target.parent.mkdir(parents=True, exist_ok=True)
                # a page waits on this: one try, never the video's patient retries (review, 2026-10-26)
                cr_media.run("narration", cr_media.narration_input(VOICE_SAMPLE, voice, model["id"]), target,
                             model=model["id"], log=studio.app.logger.info, timeout_s=180, retry=False)
                paid = True
        except cr_media.AccountStop as error:
            return jsonify(error=str(error)), 409
        except Exception:
            studio.app.logger.exception("Voice preview failed")
            return jsonify(error="That preview couldn't be made right now. Try again in a moment."), 502
        return jsonify(url="/cr-voices/sample/" + voice, paid=paid)

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

    import cr_voice_library
    cr_voice_library.install(studio, activity)                  # Choose voice with ElevenLabs connected directly

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
        if provider == "kie" and "narration" in tasks and voice:
            names = cr_media.narration_model("kie").get("voice_names") or []
            voice = next((n for n in names if n.casefold() == voice.casefold()), "")
            if not voice:
                return jsonify(error="Kie narrates with Gemini voices: type one of its voice names, such as Charon or "
                                     "Kore, or leave the voice blank. For ElevenLabs voices, add your own ElevenLabs "
                                     "key."), 400
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
            cfg.pop("voice_notice", None)
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
        if request.path == "/api/run" and getattr(g, "cr_warnings", None) and 200 <= response.status_code < 300 \
                and response.is_json:
            # what this video's check warned about (a disk with little to spare) goes back with its start
            body = response.get_json(silent=True)
            if isinstance(body, dict):
                response.set_data(json.dumps({**body, "warnings": g.cr_warnings}))
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
                            checked=json.loads((HOME/".cr-setup-proof.json").read_text(encoding="utf-8"))
                            if checked.get("style")==name and checked.get("setup",{}).get("local_version")==json.loads((HOME/"cr-release.json").read_text(encoding="utf-8"))["version"]:
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
                text=text.replace("</body>",(HOME/"cr_renderer_panel.html").read_text(encoding="utf-8")+"</body>",1)
                cancel_panel = HOME / "cr_video_cancel.html"
                if cancel_panel.is_file():
                    text=text.replace("</body>",cancel_panel.read_text(encoding="utf-8")+"</body>",1)
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
 function cloudFarm(cloud){
  if(!farm)return;
  farm.classList.remove('hidden');
  const box=document.getElementById('cr-cloud-note')||document.createElement('p');
  box.id='cr-cloud-note';box.style.cssText='font:13px system-ui;line-height:1.6;color:#9aa3b5;margin:6px 0 10px';
  const m=(cloud.machines||{}).cpu||{};
  box.textContent='Studio Cloud: the script, voice and pictures are made here with your Claude Code, then a ChannelRecipe machine renders the video (about $'+(m.price_per_hr||1.2).toFixed(2)+' an hour of machine time; a 10-minute video is usually under $1). Each render reserves its most before it starts and you pay only what it used. Balance: $'+(cloud.balance_usd||0).toFixed(2)+'. Keep Studio open while it renders: the machine asks your Claude Code its questions. ';
  const top=document.createElement('a');top.href=cloud.topup_url||'https://channelrecipe.com/app#billing';top.target='_blank';top.rel='noopener';top.textContent='Top up';
  box.append(top);
  if(!box.isConnected)farm.prepend(box);
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
  if(data.brain_mode==='claudecode'){
   fetch('/cr-cloud').then(r=>r.json()).then(cloud=>{if(cloud.available)cloudFarm(cloud);else connectNativeFarm();}).catch(connectNativeFarm);
  }
  drawGate();
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
            previous = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
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
            # its own setup check, from when it was submitted (a queued video's is never another submission's)
            setup=spec.get("cr_setup_proof") if isinstance(spec.get("cr_setup_proof"), dict) else cfg.get("setup_proof")
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
            marker.write_text(json.dumps({**project,"brain_mode":cfg.get("brain_mode","channelrecipe")}, indent=2),
                              encoding="utf-8")
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
                    if not complete:
                        # why it stopped, privacy-safe (cr_failure): what ChannelRecipe counts to fix the commonest stops
                        report["failure"]=cr_failure.for_job(HOME,state,spec,job,project["id"],report["error_code"])
                    pending=job/".cr-finish-pending.json"
                    cr_claude._private_write(pending,report)
                    answer=cr_bridge.finish_project(report)
                    pending.replace(job/".cr-finish.json")
                    receipt_saved=True
                    note=cr_failure.update_note(answer) if not complete and report["error_code"]!="cancelled" else ""
                    if note:
                        # one plain line: a newer Studio fixes many stops; updating stays the creator's choice
                        state.setdefault("lines", []).append(note)
                        if state.get("error"):
                            state["error"]=str(state["error"]).rstrip()+" "+note
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
        queue_keeper.started(spec)              # taken off the waiting line: known as the one being made from now
        ended = []

        def finished(job_of):                   # once, however it ends
            if not ended:
                ended.append(True)
                queue_keeper.finished(spec, job_of)
        try:
            activity.wait_for_editor()
            import cr_attempts
            with cr_attempts.bind(studio.mv, spec):
                try:
                    return run_owned_spec(spec)
                finally:
                    # Inside the attempt's binding, so job_dir names this attempt's folder (its short/error.json).
                    finished(lambda: studio.mv.job_dir(str(spec.get("title") or "Your video"),
                                                       str(spec.get("style") or "jung")))
        except Exception as error:
            with studio.LOCK:
                studio.JOBS.setdefault(spec["slug"], {}).update(state="error", error=str(error), done=True)
                studio.RUNNING["slug"] = None
            finished(lambda: None)
    studio._run_spec = run_spec

    def queue_check(specs):
        """Carry on (cr_queue): each held video's setup is checked again now (a top-up, a sign-in, room freed on the
        disk), and the ones ready get a fresh setup proof. -> {slug: why one still can't start}."""
        checks = setup_status().get_json()
        problems = {}
        for spec in specs:
            try:
                result = cr_readiness.preset(studio, checks, spec)
            except Exception as error:                          # noqa: BLE001 - this one stays on hold, saying why
                problems[spec["slug"]] = str(error)[:300] or "Its setup couldn't be checked."
                continue
            if result.get("ready"):
                spec["cr_setup_proof"] = result["proof"]
            else:
                problems[spec["slug"]] = (" ".join(str(b) for b in result.get("blockers") or [])[:400]
                                          or "Its setup needs attention.")
        return problems
    queue_keeper.check = queue_check
    import cr_3d_routes
    cr_3d_routes.install(studio, activity, setup_status)
    import cr_style_routes
    cr_style_routes.install(studio, activity, setup_status, token=token, home=HOME)
    import cr_style_lab
    cr_style_lab.install(studio, token, HOME)                    # Style Lab: a style's renderer worked on in Claude Code
    import cr_ranking_routes
    cr_ranking_routes.install(studio, token=token, home=HOME)
    import cr_ranking_edit
    cr_ranking_edit.install(studio, activity, HOME, token)          # Edit countdown for a finished Ranking Short
    import cr_optional_renderer
    cr_optional_renderer.install(studio, activity, home=HOME, readiness=setup_status)
    import cr_workspace
    cr_workspace.install(studio, activity, token=token, home=HOME)
    import cr_timing
    cr_timing.install(studio, home=HOME)                         # the timeline editor's picture timing (its own retime)
    import cr_style_options
    cr_style_options.install(studio)
    cr_cloud.install(HOME)                                       # the farm on ChannelRecipe's machines, when the account has it
    cr_cloud.install_routes(studio, token, HOME)                 # the workspace's "Studio Cloud" choice
    import cr_claude_code
    cr_claude_code.install(studio, token, HOME)                  # Claude Code's style gallery and the studio tools' routes

    @studio.app.get("/cr-cloud")
    def cloud_status():
        return jsonify(cr_cloud.status(force=request.args.get("fresh") == "1", home=HOME))
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
    CHAT_SETTINGS_DROPPED[:] = forget_claude_chat()
    if CHAT_SETTINGS_DROPPED:
        effort = next((x.split("=", 1)[1] for x in CHAT_SETTINGS_DROPPED if x.startswith("CLAUDE_EFFORT=")), "")
        print("Started from inside a Claude Code chat: Studio's own Claude steps use your normal settings"
              + (" (not the chat's %s effort)" % effort if effort else "") + ".", flush=True)
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
    # Found while setting up, not halfway through the first video: the speech-timing model (downloaded and loaded
    # once) and the services this network can't reach.
    for begin in (cr_whisper.start, lambda: cr_network.start(network_needs(cr_whisper.status()))):
        threading.Thread(target=begin, daemon=True, name="cr-setup-checks").start()
    try:
        # Updates from older releases ran an updater that could not remove
        # retired files, so tidy them on start too.
        import cr_update
        cr_update.retire(HOME)
    except Exception:
        pass
    studio = create_app()
    studio.OUT.mkdir(exist_ok=True)
    import cr_trash                                              # deleted videos and styles older than 30 days go
    threading.Thread(target=cr_trash.empty_old, args=(HOME,), daemon=True, name="cr-trash").start()
    try:
        import cr_claude_code
        cr_claude_code.configure(HOME)                           # Claude Code in this folder: the studio tools
        studio._cr_watcher.start()                               # a desktop notification when a video is done
    except Exception:
        pass
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
