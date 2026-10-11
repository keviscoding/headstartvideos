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

HOME = Path(__file__).resolve().parent


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
    os.environ["CLAUDE_PROVIDER"] = "channelrecipe"
    os.environ["SCRIPT_PROVIDER"] = "channelrecipe"
    os.environ["CLAUDE_HANDOFF_DIR"] = ""
    import app as studio
    from flask import Response, jsonify, request
    cr_bridge.install(studio.mv)
    cr_media.install(studio.mv)
    cr_capabilities.install(studio)
    cr_render.install(studio.mv)
    token = secrets.token_urlsafe(24)
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
        if request.path in ("/cr-connect", "/cr-provider", "/cr-hub", "/cr-voice", "/cr-images") and request.method == "POST":
            if studio.RUNNING.get("slug") or studio.QUEUE:
                return jsonify(error="Finish your current video before changing its accounts."), 409
        if request.path == "/api/run" and request.method == "POST":
            try:
                cfg = cr_bridge.load_settings()
                if not cfg.get("costs_accepted"):
                    return jsonify(error="Review your provider spending in setup before making a video."), 409
                data = request.get_json(silent=True) or {}
                # Do not change the shared brain project while another video is active.
                if studio.RUNNING.get("slug") or studio.QUEUE:
                    return jsonify(error="Finish your current video before starting another."), 409
                account = cr_bridge.call("status")
                if not account.get("brain", {}).get("connected"):
                    return jsonify(error="Connect a brain account in Setup & accounts before starting a video."), 409
            except Exception as e:
                return jsonify(error=str(e)), 409

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
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                checks["browser"] = Path(p.chromium.executable_path).is_file()
        except Exception:
            checks["browser"] = False
        try:
            checks["account"] = cr_bridge.call("status") if cfg.get("key") else None
        except Exception as e:
            checks["connection_error"] = str(e)
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
        return jsonify(checks)

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
                cr_bridge.call("brain-key", {"provider": provider, "api_key": key, "model": info["default_brain"]})
            else:
                # Check the media account without generating content. Never send it to CR.
                check_media_account(provider, key)
            write_provider(info["key_env"], key)
            cfg["media_hub"] = {"provider": provider, "tasks": sorted(set(tasks)), "voice": voice or info["default_voice"]}
            cr_bridge.save_settings(cfg)
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
            project = cr_bridge.begin_project(title, style, None if spec.get("force") else previous.get("id"))
            job.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(project, indent=2))
        except Exception as error:
            with studio.LOCK:
                studio.JOBS.setdefault(spec["slug"], {}).update(state="error", error=str(error), done=False)
                studio.RUNNING["slug"] = None
            return
        return original_run(spec)
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
