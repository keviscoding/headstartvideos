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
import cr_media

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


def configure_video_tools():
    if sys.platform != "darwin" or (tool_ready("ffmpeg") and tool_ready("ffprobe")):
        return
    # Homebrew's full build can coexist with a stale binary earlier on PATH.
    # Select an already installed, runnable pair for this studio process only.
    for directory in ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"):
        if tool_ready(str(Path(directory) / "ffmpeg")) and tool_ready(str(Path(directory) / "ffprobe")):
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
        if request.path in ("/cr-connect", "/cr-provider", "/cr-hub") and request.method == "POST":
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
        checks = {"python": True, "ffmpeg": tool_ready("ffmpeg") and tool_ready("ffprobe"),
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
                url = "https://api.kie.ai/api/v1/chat/credit" if provider == "kie" else "https://api.atlascloud.ai/v1/models"
                import requests
                cr_media._data(requests.get(url, headers={"Authorization": "Bearer " + key}, timeout=(10, 20), allow_redirects=False), provider)
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
