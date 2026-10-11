"""Admin preview bridge. No CR platform generation credentials in the download."""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import threading
import time
import secrets
from pathlib import Path
from urllib.parse import urlsplit
import requests

HOME = Path(__file__).resolve().parent
SETTINGS = HOME / ".cr-connection.json"
_lock = threading.RLock()
_project = None
_engine_originals = {}


def _mcp_tool(name, payload):
    try:
        import cr_mcp
    except ImportError:
        from . import cr_mcp
    return cr_mcp.call_tool(name, {"payload": payload}, load_settings())


def current_project():
    with _lock:
        if _project is None:
            raise RuntimeError("Start a ChannelRecipe project before using Claude Code.")
        return _project


def prepare_runtime(proof):
    result=_mcp_tool("local_studio_prepare", proof)
    if result.get("ready") is not True:
        raise RuntimeError(" ".join(result.get("issues") or ["Finish this workflow's setup before starting."]))
    return result


def authorize_stage(payload):
    return _mcp_tool("local_studio_authorize_stage", payload)


def finish_stage(payload):
    return _mcp_tool("local_studio_finish_stage", payload)


def project_progress(project_id):
    return _mcp_tool("local_studio_progress", {"project_id": project_id})


def finish_project(payload):
    return _mcp_tool("local_studio_finish_project", payload)


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
                                   headers=headers, json=payload, timeout=(15, 650 if path == "brain" else 220))
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


def begin_project(title, style, resume_id=None, *, proof=None, job_spec=None, purpose="video"):
    global _project
    with _lock:
        if load_settings().get("brain_mode") == "claudecode":
            if proof is None:
                proof = load_settings().get("setup_proof")
            spec = job_spec or {"title": title, "style": style, "minutes": 10}
            cfg = load_settings()
            options = {k:spec[k] for k in ("steps", "script", "burn_subs", "thumb_count", "use_motion", "character",
                "character_image", "maps", "headlines", "spotlight", "objects", "depth", "extra", "avatar_id", "avatar_s",
                "avatar_count", "auto_style", "offer", "offer_plugs", "desc", "chapters", "sources", "byo_audio", "mix") if k in spec}
            if "script_text" in spec:
                options["script"] = spec["script_text"]
            if spec.get("meta"):
                options.update(desc=True, **spec["meta"])
            payload = {"setup": {**(proof or {}), "purpose": purpose},
                "resume_project_id": resume_id,
                "title": title, "style": style, "minutes": spec.get("minutes", 10),
                "features": spec.get("features") or {},
                "options": options,
                "max_calls": 2 if purpose == "setup" else 500,
                "platform": "windows" if os.name == "nt" else "mac"}
            # Preserve the begin ID if its response is interrupted. Reconnecting
            # records the same project rather than creating an orphaned duplicate.
            identity = hashlib.sha256(json.dumps({"payload":payload,"account":cfg.get("key")},sort_keys=True).encode()).hexdigest()
            pending_path = HOME / ".cr-pending-begin.json"
            try:
                pending = json.loads(pending_path.read_text())
            except (OSError,ValueError):
                pending = {}
            begin_key = pending.get("begin_id") if pending.get("identity") == identity else "b_" + secrets.token_hex(16)
            fd = os.open(pending_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,"w") as handle:
                json.dump({"identity":identity,"begin_id":begin_key},handle)
            result = _mcp_tool("local_studio_begin", {**payload,"begin_id":begin_key})
            _project = result["project"]
            pending_path.unlink(missing_ok=True)
        elif resume_id:
            _project = call("projects/" + resume_id)
        else:
            _project = call("projects", {"title": title, "style": style or "jung", "max_calls": 120})
    return _project


def think(prompt, max_tokens=8000, kind="text", images=None, model=None):
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
    # Frontier already selects a model for each stage. Preserve that distinction
    # through the account's explicit general/visual profile, without selecting a
    # provider or falling back to somebody else's balance.
    role = "visual" if isinstance(model, str) and "claude-opus" in model.lower() else "general"
    result = call("brain", {**payload, "request_id": request_id, "model_role": role,
                             "requested_model": model})
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
    # A mode change restores the pinned engine's real helpers before installing
    # the selected transport. Do not layer API lambdas over Claude Code helpers.
    names = ("claude", "_claude_try", "_claude_code_try", "claude_research", "claude_vision",
             "_claude_code_vision", "_claude_exe", "_claude_endpoint")
    original = _engine_originals.setdefault(id(engine), {name: getattr(engine, name) for name in names if hasattr(engine, name)})
    if load_settings().get("brain_mode") == "claudecode":
        for name, value in original.items():
            setattr(engine, name, value)
        try:
            import cr_claude
        except ImportError:
            from . import cr_claude
        cr_claude.install(engine, bridge=__import__(__name__, fromlist=["install"]))
        return
    engine.CLAUDE_PROVIDER = "channelrecipe"
    engine.SCRIPT_PROVIDER = "channelrecipe"
    engine.CLAUDE_FALLBACK = ""
    engine._HANDOFF_DIR = ""
    engine.claude = lambda prompt, model=None, max_tokens=16000, provider=None: think(prompt, max_tokens, model=model)
    engine._claude_try = lambda provider, model, prompt, max_tokens, budget_s: think(prompt, max_tokens, model=model)
    engine._claude_code_try = lambda model, prompt: think(prompt, model=model)
    engine.claude_research = lambda prompt, model=None: think(prompt, kind="research", model=model)
    engine.claude_vision = lambda prompt, image_paths, model=None, max_tokens=2000: think(prompt, max_tokens, "vision", image_paths, model=model)
    engine._claude_code_vision = lambda prompt, image_paths: think(prompt, 8000, "vision", image_paths)
    # Some source modules call the CLI directly, outside the common helper.
    engine._claude_exe = lambda: (_ for _ in ()).throw(RuntimeError("Use the CR brain for this preview."))
    # Provider endpoint fallback must not bypass CR using a local Anthropic key.
    engine._claude_endpoint = lambda provider: None
