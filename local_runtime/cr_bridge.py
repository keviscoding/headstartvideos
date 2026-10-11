"""Admin preview bridge. No CR platform generation credentials in the download."""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests

HOME = Path(__file__).resolve().parent
SETTINGS = HOME / ".cr-connection.json"
_lock = threading.RLock()
_project = None


def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(data):
    # Atomic write and private permissions; never echo keys into shell commands.
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(SETTINGS)


def call(path, payload=None, settings=None):
    cfg = settings if settings is not None else load_settings()
    base = cfg.get("url", "https://channelrecipe.com").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        raise RuntimeError("Connect to ChannelRecipe using a secure HTTPS address.")
    if not cfg.get("key", "").startswith("cr_mcp_"):
        raise RuntimeError("Connect your ChannelRecipe account in the setup screen first.")
    headers = {"Authorization": "Bearer " + cfg["key"]}
    for attempt in range(4):
        try:
            res = requests.request("POST" if payload is not None else "GET", base + "/api/frontier/" + path,
                                   headers=headers, json=payload, timeout=(15, 220))
        except requests.RequestException:
            raise RuntimeError("Could not reach ChannelRecipe. Your work is saved; check your connection.")
        if res.status_code == 429 and attempt < 3:
            time.sleep(min(20, int(res.headers.get("Retry-After", "10"))))
            continue
        try:
            data = res.json()
        except ValueError:
            raise RuntimeError("ChannelRecipe could not answer. Try again after checking your connection.")
        if res.status_code != 200:
            raise RuntimeError(str(data.get("detail", "ChannelRecipe could not complete this step.")))
        return data
    raise RuntimeError("The brain is busy. Wait a moment and resume your video.")


def begin_project(title, style, resume_id=None):
    global _project
    with _lock:
        if resume_id:
            _project = call("projects/" + resume_id)
        else:
            _project = call("projects", {"title": title, "style": style or "jung", "max_calls": 120})
    return _project


def think(prompt, max_tokens=8000, kind="text", images=None):
    with _lock:
        if not _project:
            cfg = load_settings()
            if not cfg.get("costs_accepted"):
                raise RuntimeError("Review provider spending in setup before using style or product generation.")
            begin_project("Studio style or product work", "custom")
        project_id = _project["id"]
    encoded = []
    from PIL import Image
    for path in list(images or [])[:8]:
        with Image.open(path) as pic:
            pic.thumbnail((1280, 1280))
            buf = io.BytesIO()
            pic.convert("RGB").save(buf, format="PNG")
            encoded.append(base64.b64encode(buf.getvalue()).decode())
    payload = {"project_id": project_id, "kind": kind, "prompt": prompt,
               "max_tokens": min(32768, max(128, max_tokens or 8000)), "images": encoded}
    # The same stage input gets the same ID after an interrupted connection.
    request_id = "r_" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    result = call("brain", {**payload, "request_id": request_id})
    receipt = HOME / "output" / "cr-receipts" / (project_id + ".json")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        try:
            rows = json.loads(receipt.read_text())
        except (ValueError, OSError):
            rows = {}
        rows[request_id] = {k: v for k, v in result.items() if k != "text"}
        receipt.write_text(json.dumps(rows, indent=2))
    return result["text"]


def install(engine):
    engine.CLAUDE_PROVIDER = "channelrecipe"
    engine.SCRIPT_PROVIDER = "channelrecipe"
    engine.CLAUDE_FALLBACK = ""
    engine._HANDOFF_DIR = ""
    engine.claude = lambda prompt, model=None, max_tokens=16000, provider=None: think(prompt, max_tokens)
    engine._claude_try = lambda provider, model, prompt, max_tokens, budget_s: think(prompt, max_tokens)
    engine._claude_code_try = lambda model, prompt: think(prompt)
    engine.claude_research = lambda prompt, model=None: think(prompt, kind="research")
    engine.claude_vision = lambda prompt, image_paths, model=None, max_tokens=2000: think(prompt, max_tokens, "vision", image_paths)
    engine._claude_code_vision = lambda prompt, image_paths: think(prompt, 8000, "vision", image_paths)
    # Some source modules call the CLI directly, outside the common helper.
    engine._claude_exe = lambda: (_ for _ in ()).throw(RuntimeError("Use the CR brain for this preview."))
    # Provider endpoint fallback must not bypass CR using a local Anthropic key.
    engine._claude_endpoint = lambda provider: None
