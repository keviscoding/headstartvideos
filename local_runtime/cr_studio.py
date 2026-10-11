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

import cr_bridge
import cr_capabilities
import cr_media
import cr_render
import cr_claude
import cr_mcp
import cr_readiness
import cr_workers
import hashlib
import re

HOME = Path(__file__).resolve().parent
_graphics_check = {}


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
        with path.open("rb") as handle:
            digest=hashlib.file_digest(handle,"sha256").hexdigest()
        artifacts.append({"name":name,"sha256":digest,"media_type":media_types[path.suffix]})
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
    from flask import Response, jsonify, request
    cr_bridge.install(studio.mv)
    cr_media.install(studio.mv)
    cr_capabilities.install(studio)
    cr_render.install(studio.mv)
    cr_workers.install()
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
        origin = request.headers.get("Origin")
        if origin and origin not in ("http://127.0.0.1:7860", "http://localhost:7860"):
            return jsonify(error="Open the studio on localhost."), 403
        if request.method in ("POST", "DELETE", "PUT", "PATCH"):
            if request.headers.get("X-CR-Local-Token") != token:
                return jsonify(error="Refresh your studio page and try again."), 403
        if request.path in ("/cr-connect", "/cr-provider", "/cr-brain", "/cr-hub", "/cr-voice", "/cr-images", "/cr-claude/setup") and request.method == "POST":
            if studio.RUNNING.get("slug") or studio.QUEUE or cr_workers.busy():
                return jsonify(error="Finish your current video before changing its accounts."), 409
        if request.path == "/api/run" and request.method == "POST":
            try:
                cfg = cr_bridge.load_settings()
                if not cfg.get("costs_accepted"):
                    return jsonify(error="Review your provider spending in setup before making a video."), 409
                data = request.get_json(silent=True) or {}
                # Do not change the shared brain project while another video is active.
                if studio.RUNNING.get("slug") or studio.QUEUE or cr_workers.busy():
                    return jsonify(error="Finish your current video before starting another."), 409
                checks = setup_status().get_json()
                preflight = cr_readiness.preset(studio,checks,data)
                if not preflight["ready"]:
                    return jsonify(error="Complete this video's setup before starting.",**preflight),409
                cfg = cr_bridge.load_settings()
                cfg["setup_proof"] = preflight["proof"]
                cr_bridge.save_settings(cfg)
                if cfg.get("brain_mode") == "claudecode":
                    cr_bridge.prepare_runtime(preflight["proof"])
            except Exception as e:
                return jsonify(error=str(e)), 409
        elif request.method == "POST" and (request.path.startswith(("/api/style", "/api/offer", "/api/auto", "/api/learn", "/api/products", "/api/farm"))
                or request.path.startswith("/api/tl/") and request.path.rsplit("/",1)[-1] in {"chat","op","render"}):
            checks=setup_status().get_json()
            if not checks["readiness"]["ready"]:
                return jsonify(error="Finish studio setup before creating styles or products.",**checks["readiness"]),409
            if studio.RUNNING.get("slug") or studio.QUEUE or cr_workers.busy():
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

    @studio.app.get("/cr-setup")
    def setup_page():
        text = (HOME / "cr_setup.html").read_text()
        return Response(text.replace("__LOCAL_TOKEN__", token), mimetype="text/html", headers={"Cache-Control": "no-store"})

    @studio.app.get("/cr-status")
    def setup_status():
        cfg = cr_bridge.load_settings()
        checks = {"python": True, "ffmpeg": video_tools_ready(),
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
        return jsonify(checks)

    @studio.app.post("/cr-preflight")
    def preflight():
        try:
            result=cr_readiness.preset(studio,setup_status().get_json(),request.get_json(silent=True) or {})
            if result["ready"]:
                cfg=cr_bridge.load_settings()
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
        if studio.RUNNING.get("slug") or studio.QUEUE or cr_workers.busy():
            return jsonify(error="Finish the current video before starting another."),409
        try:
            project_id=str((request.get_json(silent=True) or {}).get("project_id", ""))
            import re
            if not re.fullmatch(r"crv_[a-f0-9]{24}",project_id):
                return jsonify(error="Use a valid saved CR project."),400
            progress=cr_bridge.project_progress(project_id)
            spec=progress["job_spec"]
            if progress["project"]["status"] == "done":
                return jsonify(error="This project already finished. Open its saved local output."),409
            preflight=cr_readiness.preset(studio,setup_status().get_json(),spec)
            if not preflight["ready"]:
                return jsonify(error="Complete this project's setup before starting.",**preflight),409
            cfg=cr_bridge.load_settings()
            cfg["setup_proof"]=preflight["proof"]
            cr_bridge.save_settings(cfg)
            # Use Frontier's own validation, normalization and queue construction.
            # Defer dispatch just long enough to attach the owned MCP project.
            payload={**spec,"steps":spec.get("steps") or ["script","voiceover","pexels","images","video","thumbnail"]}
            request._cached_json=(payload,payload)
            dispatch=studio._ensure_dispatcher
            studio._ensure_dispatcher=lambda:None
            try:
                response=studio.app.view_functions["api_run"]()
            finally:
                studio._ensure_dispatcher=dispatch
            if isinstance(response,tuple):
                return response
            queued=response.get_json().get("queued",[])
            with studio.LOCK:
                for job in studio.QUEUE:
                    job["cr_project_id"]=project_id
            studio._ensure_dispatcher()
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
            # Only replay final metadata, never generation, after a lost receipt.
            for pending in list(studio.OUT.rglob(".cr-finish-pending.json"))[:20]:
                try:
                    cr_bridge.finish_project(json.loads(pending.read_text()))
                    pending.replace(pending.with_name(".cr-finish.json"))
                except (Exception,ValueError):
                    pass
            projects=cr_bridge.call("projects")["projects"]
            result=[]
            for p in projects[:20]:
                try:
                    progress=cr_bridge.project_progress(p["id"])
                except RuntimeError:
                    continue
                result.append({"project_id":p["id"],"title":p["title"],"status":p["status"],
                    "stages":[s for s in progress["stages"] if s["status"] in {"failed","authorized","retry_allowed"}]})
            return jsonify(projects=result)
        except Exception:
            return jsonify(error="Reconnect CR before loading project recovery.",projects=[]),409

    @studio.app.post("/cr-recover")
    def recover_stage():
        if studio.RUNNING.get("slug") or studio.QUEUE or cr_workers.busy():
            return jsonify(error="Stop or finish the running video before reviewing recovery."),409
        try:
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
                           ("the model account choices above." if provider == "inherit" else "your individual Frontier voice settings."))
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

    @studio.app.post("/cr-hub")
    def save_hub():
        data = request.get_json(silent=True) or {}
        cfg = cr_bridge.load_settings()
        if data.get("provider") == "original":
            cfg.pop("media_hub", None)
            cr_bridge.save_settings(cfg)
            return jsonify(saved=True, message="Using the individual accounts chosen in Frontier.")
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

    # Add navigation/security token to both original local screens without rewriting the engine UI.
    @studio.app.after_request
    def inject(response):
        if response.mimetype == "text/html" and request.path != "/cr-setup" and not response.direct_passthrough:
            patch = "<script>const CR_LOCAL_TOKEN=" + json.dumps(token) + ";const crFetch=window.fetch.bind(window);window.fetch=(url,opts={})=>{if(new URL(url instanceof Request?url.url:url,location.href).origin===location.origin){const h=new Headers(opts.headers||(url instanceof Request?url.headers:undefined));h.set('X-CR-Local-Token',CR_LOCAL_TOKEN);opts={...opts,headers:h}}return crFetch(url,opts)};</script>"
            text = response.get_data(as_text=True).replace("</head>", patch + "</head>", 1)
            text = text.replace("<body>", '<body><a href="/cr-setup" style="display:block;background:#171B2E;color:#EDEEF5;padding:12px 24px;font:14px system-ui;text-decoration:none">ChannelRecipe · Setup &amp; accounts</a>', 1)
            if request.path == "/":
                guard = """<script>document.addEventListener('DOMContentLoaded',()=>{const go=document.getElementById('go');if(!go)return;let ready=false;const note=document.createElement('p');note.style.cssText='color:#e8a13c;font:14px system-ui;line-height:1.6';note.textContent='Checking ChannelRecipe setup…';go.insertAdjacentElement('beforebegin',note);go.disabled=true;fetch('/cr-status').then(r=>r.json()).then(d=>{ready=d.readiness?.ready===true;go.disabled=!ready;note.textContent=ready?'Your setup is connected. The selected preset and options will be checked before generation.':'Finish Setup & accounts before generating a video.';if(!ready){const a=document.createElement('a');a.href='/cr-setup';a.textContent=' Open setup';note.append(a)}}).catch(()=>{note.textContent='Reconnect ChannelRecipe in Setup & accounts before generating.'});go.addEventListener('click',e=>{if(!ready){e.preventDefault();e.stopImmediatePropagation();location.href='/cr-setup'}},true)});</script>"""
                text=text.replace("</body>",guard+"</body>",1)
            response.set_data(text)
        return response

    # Project context is set per dispatched title, so batches and resumed jobs
    # retain their own receipts rather than billing every title to the first one.
    original_run = studio._run_spec
    def run_spec(spec):
        title = str(spec.get("title") or "Your video")
        style = str(spec.get("style") or "jung")
        job = studio.mv.job_dir(title, style)
        marker = job / "cr-project.json"
        try:
            previous = json.loads(marker.read_text()) if marker.is_file() else {}
            cfg=cr_bridge.load_settings()
            cr_bridge.install(studio.mv)
            if cfg.get("brain_mode") == "claudecode":
                purpose="creative" if spec.get("timeline") else "video"
                setup=cfg.get("setup_proof")
                if spec.get("timeline"):
                    with studio.app.app_context():
                        setup=cr_readiness.proof(setup_status().get_json(),purpose=purpose)
                resume=previous.get("id") if previous.get("brain_mode")=="claudecode" else None
                project=cr_bridge.begin_project(title,style,spec.get("cr_project_id") or (None if spec.get("force") or spec.get("timeline") else resume),
                    proof=setup,job_spec={**spec,"minutes":max(1,int(spec.get("minutes") or 1))},purpose=purpose)
            else:
                project = cr_bridge.begin_project(title, style, None if spec.get("force") else previous.get("id"))
            job.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({**project,"brain_mode":cfg.get("brain_mode","channelrecipe")}, indent=2))
        except Exception as error:
            with studio.LOCK:
                studio.JOBS.setdefault(spec["slug"], {}).update(state="error", error=str(error), done=False)
                studio.RUNNING["slug"] = None
            return
        result=original_run(spec)
        if cfg.get("brain_mode") == "claudecode":
            state=studio.JOBS.get(spec["slug"],{})
            artifacts=[]
            if state.get("state") == "done":
                artifacts=output_artifacts(studio.mv,job)
            complete=bool(artifacts) and state.get("state")=="done"
            if "video" in spec.get("steps",[]):
                complete=complete and studio.mv.final_video(job).is_file()
            try:
                report={"project_id":project["id"],"status":"done" if complete else "failed",
                    "artifacts":artifacts,"error_code":"" if complete else "runtime_failed"}
                pending=job/".cr-finish-pending.json"
                cr_claude._private_write(pending,report)
                cr_bridge.finish_project(report)
                pending.replace(job/".cr-finish.json")
            except Exception:
                state["lines"].append("Your local output is saved. Reconnect CR to update its completion record.")
        return result
    studio._run_spec = run_spec
    return studio


def main():
    studio = create_app()
    studio.OUT.mkdir(exist_ok=True)
    studio._ensure_dispatcher()
    studio.timeline.start_worker(busy=lambda job: bool(studio._tl_busy(Path(job).name) == "running"),
                                on_idle=lambda job: studio._enqueue_tl_render(Path(job), Path(job).name))
    def open_page():
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:7860/cr-setup")
    threading.Thread(target=open_page, daemon=True).start()
    print("ChannelRecipe local studio: http://127.0.0.1:7860/cr-setup")
    studio.app.run(host="127.0.0.1", port=7860, threaded=True, debug=False)


if __name__ == "__main__":
    main()
