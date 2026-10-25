"""ChannelRecipe Studio bridge. No CR platform generation credentials in the download."""
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

# Exact identifiers for the same reviewed engine contract before/after its
# product-name migration. Digests keep compatibility metadata name-independent.
_WORKFLOW_NAMES = {"7b0bef7c7ec842ceb21abf01367533a784a41c4727ebd218e03318e1048d4ed8",
                   "25efdf9e2e0d26007e0a0b7564ab7ae01075c53bae1047b6eb4ae323ef37237e"}


def workflow_view(result):
    """View a compatible contract under this installed release's name.

    The shared MCP keeps older clients and immutable receipts intact. Only its
    contract fields are adapted here; user options, titles and artifacts stay
    byte-for-byte meaningful and no request/receipt identity is changed.
    """
    if not isinstance(result, dict):
        return result
    try:
        release = json.loads((HOME / "cr-release.json").read_text(encoding="utf-8"))
        target = release["workflow_version"]
        if hashlib.sha256(target.encode()).hexdigest() not in _WORKFLOW_NAMES:
            return result
    except (OSError, ValueError, KeyError, AttributeError):
        return result
    def contract(value):
        if not isinstance(value, dict):
            return value
        name = value.get("workflow_version")
        if isinstance(name, str) and hashlib.sha256(name.encode()).hexdigest() in _WORKFLOW_NAMES:
            return {**value, "workflow_version": target}
        return value
    viewed = contract(result)
    for key in ("workflow", "production_workflow"):
        if key in result:
            viewed = {**viewed, key: contract(result[key])}
    return viewed


def _mcp_tool(name, payload):
    try:
        import cr_mcp
    except ImportError:
        from . import cr_mcp
    return workflow_view(cr_mcp.call_tool(name, {"payload": payload}, load_settings()))


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
    """Record how a project ended; returns the server's answer. A stopped project's finish carries its failure record
    (cr_failure). A server from before those records refuses the unknown field as invalid details, a refusal with no
    status, so that finish goes again without the record: the project's end is never lost over it."""
    try:
        return _mcp_tool("local_studio_finish_project", payload)
    except RuntimeError as error:
        if not (isinstance(payload, dict) and "failure" in payload and type(error).__name__ == "WorkflowError"
                and getattr(error, "status", 0) is None):
            raise
    return _mcp_tool("local_studio_finish_project", {k: v for k, v in payload.items() if k != "failure"})


def load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data):
    # Atomic write and private permissions; never echo keys into shell commands.
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(SETTINGS)


# Model requests from this computer: a few at a time, queued here rather than
# turned away by the server or the creator's provider.
BRAIN_AT_ONCE = 3
BRAIN_PATIENCE_S = 900
_brain_slots = threading.BoundedSemaphore(BRAIN_AT_ONCE)
# What the hosting gateway sends when it cuts a long request (not ChannelRecipe's answer).
GATEWAY_CUT = {502, 503, 504, 520, 521, 522, 523, 524}


def call(path, payload=None, settings=None):
    if path == "brain":
        with _brain_slots:
            return _call(path, payload, settings, patient=True)
    return _call(path, payload, settings)


def _call(path, payload=None, settings=None, *, patient=False, sleep=None, clock=None):
    """Brain requests are idempotent by request ID: sending one again collects the same
    answer (the server never starts a second paid request), so they wait patiently."""
    cfg = settings if settings is not None else load_settings()
    base = cfg.get("url", "https://channelrecipe.com").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        raise RuntimeError("Connect to ChannelRecipe using a secure HTTPS address.")
    if not cfg.get("key", "").startswith("cr_mcp_"):
        raise RuntimeError("Connect your ChannelRecipe account in the setup screen first.")
    headers = {"Authorization": "Bearer " + cfg["key"]}
    try:
        import cr_mcp
    except ImportError:
        from . import cr_mcp
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    deadline = clock() + BRAIN_PATIENCE_S
    attempt = 0
    while True:
        attempt += 1
        try:
            res = cr_mcp.request_with_connect_retry(lambda: requests.request(
                "POST" if payload is not None else "GET", base + "/api/studio/" + path,
                headers=headers, json=payload, timeout=(15, 650 if path == "brain" else 220)))
        except requests.RequestException:
            if patient and clock() < deadline:
                sleep(10)  # the connection dropped; the server keeps the request
                continue
            raise RuntimeError("Could not reach ChannelRecipe. Your work is saved; check your connection.")
        reply_headers = getattr(res, "headers", None) or {}
        wait = int(reply_headers.get("Retry-After", "10") or 10)
        if res.status_code == 429 and (attempt < 4 or (patient and clock() < deadline)):
            sleep(min(60 if patient else 20, max(1, wait)))
            continue
        if (patient and res.status_code == 409 and reply_headers.get("X-CR-Request-State") == "running"
                and clock() < deadline):
            sleep(min(30, max(1, wait)))
            continue
        try:
            data = res.json()
        except ValueError:
            if patient and res.status_code in GATEWAY_CUT and clock() < deadline:
                sleep(10)  # cut at the gateway's time limit while the server keeps working
                continue
            raise RuntimeError("ChannelRecipe could not answer. Try again after checking your connection.")
        if res.status_code != 200:
            raise RuntimeError(str(data.get("detail", "ChannelRecipe could not complete this step.")))
        return workflow_view(data)


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
                "avatar_count", "auto_style", "offer", "offer_plugs", "desc", "chapters", "sources", "byo_audio", "mix", "render_backend", "manimgx_identity") if k in spec}
            if "script_text" in spec:
                options["script"] = spec["script_text"]
            if spec.get("meta"):
                options.update(desc=True, **spec["meta"])
            payload = {"setup": {**(proof or {}), "purpose": purpose},
                "resume_project_id": resume_id,
                "title": title, "style": style, "minutes": spec.get("minutes", 10),
                "features": spec.get("features") or {},
                "options": options,
                # Two models, with at most one explicitly approved recovery
                # for each. A recovered first check must leave room for the
                # second model's normal check.
                "max_calls": 4 if purpose == "setup" else 500,
                "platform": "windows" if os.name == "nt" else "mac"}
            # Preserve the begin ID if its response is interrupted. Reconnecting
            # records the same project rather than creating an orphaned duplicate.
            identity = hashlib.sha256(json.dumps({"payload":payload,"account":cfg.get("key")},sort_keys=True).encode()).hexdigest()
            pending_path = HOME / ".cr-pending-begin.json"
            try:
                pending = json.loads(pending_path.read_text(encoding="utf-8"))
            except (OSError,ValueError):
                pending = {}
            begin_key = pending.get("begin_id") if pending.get("identity") == identity else "b_" + secrets.token_hex(16)
            fd = os.open(pending_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,"w", encoding="utf-8") as handle:
                json.dump({"identity":identity,"begin_id":begin_key},handle)
            result = _mcp_tool("local_studio_begin", {**payload,"begin_id":begin_key})
            _project = {**result["project"], "job_spec": result.get("job_spec", spec)}
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
            rows = json.loads(receipt.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            rows = {}
        rows[request_id] = {k: v for k, v in result.items() if k != "text"}
        receipt.write_text(json.dumps(rows, indent=2), encoding="utf-8")
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
    engine._claude_exe = lambda: (_ for _ in ()).throw(RuntimeError("Use your connected ChannelRecipe Claude route for this step."))
    # Provider endpoint fallback must not bypass CR using a local Anthropic key.
    engine._claude_endpoint = lambda provider: None
