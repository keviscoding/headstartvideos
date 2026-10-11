"""Run the current Frontier engine with CR's admin bridge and local onboarding."""
from __future__ import annotations
import json
import os
import secrets
import shutil
import threading
import time
import webbrowser
from pathlib import Path

import cr_bridge

HOME = Path(__file__).resolve().parent


def create_app():
    os.environ["CLAUDE_PROVIDER"] = "channelrecipe"
    os.environ["SCRIPT_PROVIDER"] = "channelrecipe"
    os.environ["CLAUDE_HANDOFF_DIR"] = ""
    import app as studio
    from flask import Response, jsonify, request
    cr_bridge.install(studio.mv)
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
                    return jsonify(error="Reconnect your Gemini account in CR before starting a video."), 409
            except Exception as e:
                return jsonify(error=str(e)), 409

    @studio.app.get("/cr-setup")
    def setup_page():
        text = (HOME / "cr_setup.html").read_text()
        return Response(text.replace("__LOCAL_TOKEN__", token), mimetype="text/html", headers={"Cache-Control": "no-store"})

    @studio.app.get("/cr-status")
    def setup_status():
        cfg = cr_bridge.load_settings()
        checks = {"python": True, "ffmpeg": bool(shutil.which("ffmpeg")),
                  "node": bool(shutil.which("node") or shutil.which("deno")),
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
                              ["ELEVENLABS_API_KEY", "ALGROW_API_KEY", "WAVESPEED_API_KEY", "KIE_API_KEY", "PEXELS_API_KEY", "GEMINI_API_KEY", "EPIDEMIC_API_KEY", "AI33_API_KEY", "SIRAY_API_KEY", "BRAVE_API_KEY"]}
        return jsonify(checks)

    @studio.app.post("/cr-connect")
    def connect():
        data = request.get_json(silent=True) or {}
        cfg = {"url": "https://channelrecipe.com", "key": str(data.get("key", "")).strip(),
               "costs_accepted": data.get("costs_accepted") is True}
        try:
            status = cr_bridge.call("status", settings=cfg)
            if not status.get("brain", {}).get("connected"):
                return jsonify(error="Connect your Gemini key in ChannelRecipe first, then return here."), 409
            cr_bridge.save_settings(cfg)
        except Exception as e:
            return jsonify(error=str(e)), 400
        return jsonify(connected=True)

    @studio.app.post("/cr-provider")
    def save_provider():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", ""))
        allowed = {"ELEVENLABS_API_KEY", "ALGROW_API_KEY", "WAVESPEED_API_KEY", "KIE_API_KEY", "PEXELS_API_KEY", "GEMINI_API_KEY", "EPIDEMIC_API_KEY", "AI33_API_KEY", "SIRAY_API_KEY", "BRAVE_API_KEY"}
        value = str(data.get("key", "")).strip()
        if name not in allowed or not value or any(ch.isspace() for ch in value):
            return jsonify(error="Choose an account and paste its complete key."), 400
        from dotenv import set_key
        env = HOME / ".env"
        env.touch(exist_ok=True)
        try:
            env.chmod(0o600)
        except OSError:
            pass
        set_key(str(env), name, value)
        os.environ[name] = value
        if hasattr(studio.mv, name):
            setattr(studio.mv, name, value)
        return jsonify(saved=True, message="Saved on this computer. This checks the format; the provider verifies it when used.")

    # Add navigation/security token to both original local screens without rewriting the engine UI.
    @studio.app.after_request
    def inject(response):
        if response.mimetype == "text/html" and request.path != "/cr-setup" and not response.direct_passthrough:
            patch = "<script>const CR_LOCAL_TOKEN=" + json.dumps(token) + ";const crFetch=window.fetch.bind(window);window.fetch=(url,opts={})=>{opts.headers={...opts.headers,'X-CR-Local-Token':CR_LOCAL_TOKEN};return crFetch(url,opts)};</script>"
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
