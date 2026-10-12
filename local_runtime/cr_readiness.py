"""Check the actual selected Frontier requirements before any video work."""
from __future__ import annotations

import hashlib
import json
import os
import time
from types import FunctionType

import requests

import cr_bridge
import cr_claude
import cr_media
import cr_mcp

_accounts = {}


def item(key, label, ready, why=""):
    return {"key": key, "label": label, "ready": bool(ready), "message": "" if ready else why}


def basic(checks, *, verify_mcp=True):
    cfg = cr_bridge.load_settings()
    mode = cfg.get("brain_mode", "channelrecipe")
    account = checks.get("account") or {}
    results = [item("python","Python 3.10 or newer",checks.get("python") is True,"Install Python 3.12, then reopen the launcher."),
        item("connection", "ChannelRecipe account", bool(account), "Connect your CR account in step 2."),
        item("spending", "Account spending acknowledged", cfg.get("costs_accepted"), "Review account costs in step 2."),
        item("release", "Current workflow installed", account.get("version") == checks.get("local_version"),
             "Download and start the current studio from ChannelRecipe."),
        item("ffmpeg", "Video and caption tools", checks.get("ffmpeg"), "Install FFmpeg with subtitles, H.264, AAC and MP3 support, then reopen the launcher."),
        item("browser", "Graphics browser", checks.get("browser"), "Reopen the launcher to install its graphics browser."),
        item("node", "JavaScript runtime", checks.get("node"), "Install Node.js LTS, then reopen the launcher.")]
    cli = cr_claude.status()
    cli.update(models=[{"id":m,"label":"Claude Opus 5.5" if "opus" in m else "Claude Sonnet 5.5"} for m in cr_claude.MODELS],
               general_model=cfg.get("general_model", cr_claude.GENERAL_MODEL),
               visual_model=cfg.get("visual_model", cr_claude.VISUAL_MODEL),
               models_saved=bool(cfg.get("general_model") and cfg.get("visual_model") and cfg.get("local_version")),
               billing="Your signed-in Claude Code account and its usage limits apply.")
    mcp = cr_mcp.status(cfg) if cfg.get("key") and verify_mcp else {"connected":False,"why":"Connect CR first."}
    registered=cr_mcp.is_registered(cfg)
    cli["mcp_connected"] = mcp["connected"] and registered
    cli["models_verified"] = cr_claude.models_verified(cfg, cli)
    if mode == "claudecode":
        results += [item("claude", "Claude Code installed and signed in", cli["ready"], cli.get("message", "Sign in to Claude Code.")),
            item("mcp", "CR production tools connected", cli["mcp_connected"], mcp.get("why") or "Connect CR tools in step 3."),
            item("models", "Both Claude models verified", cli["models_verified"], "Save your model choices, then press Verify both Claude models.")]
    else:
        results.append(item("brain", "Selected API brain connected", account.get("brain", {}).get("connected"), "Choose your Claude Code route or connect an API brain in step 3."))
    return {"ready": all(row["ready"] for row in results), "checks":results,
            "blockers":[r["message"] for r in results if not r["ready"]], "claude_code":cli, "brain_mode":mode}


def proof(checks, *, media_ready=False, purpose="video"):
    cfg = cr_bridge.load_settings()
    cli = checks.get("claude_code") or cr_claude.status()
    return {"local_version":checks.get("local_version", "5.3-admin.13"), "brain_mode":"claudecode",
            "general_model":cfg.get("general_model", cr_claude.GENERAL_MODEL),
            "visual_model":cfg.get("visual_model", cr_claude.VISUAL_MODEL),
            "claude_installed":cli.get("installed") is True, "claude_authenticated":cli.get("authenticated") is True,
            "api_provider":cli.get("api_provider", "other"), "ffmpeg_ready":checks.get("ffmpeg") is True,
            "browser_ready":checks.get("browser") is True, "node_ready":checks.get("node") is True,
            "costs_accepted":cfg.get("costs_accepted") is True, "models_verified":cli.get("models_verified") is True,
            "media_ready":media_ready, "purpose":purpose}


def _check_hub(provider, key):
    identity = hashlib.sha256((provider+key).encode()).hexdigest()
    if _accounts.get(identity, 0) > time.monotonic():
        return
    url = "https://api.kie.ai/api/v1/chat/credit" if provider == "kie" else "https://api.atlascloud.ai/v1/models"
    data = cr_media._data(requests.get(url, headers={"Authorization":"Bearer "+key}, timeout=(10,20), allow_redirects=False),provider)
    if provider == "kie":
        credit = data.get("data") if isinstance(data,dict) else data
        if isinstance(credit,(int,float)) and credit <= 0:
            raise RuntimeError("Top up your Kie balance before starting this preset.")
    _accounts[identity] = time.monotonic()+300


def preset(studio, checks, spec):
    ready = basic(checks)
    results = list(ready["checks"])
    style = spec.get("style") or ""
    catalog = studio.mv.prompts.PROMPT_SETS
    if style not in catalog:
        results.append(item("style", "Original preset selected", False, "Choose a preset from your installed Frontier library."))
        return {"ready":False,"checks":results,"blockers":[r["message"] for r in results if not r["ready"]]}
    studio.styles.load_all()
    info = studio.styles.STYLES.get(style) or studio.mv.STYLE_INFO.get(style,{})
    caps = studio.features.capabilities()
    fcfg = (info or {}).get("features") or {}
    offered = fcfg.get("offered") if isinstance(fcfg.get("offered"),list) else [k for k in studio.features.KEYS if k not in studio.features.EXPLICIT]
    asked = spec.get("features")
    original = studio.features.resolve
    # Execute the original resolver with account availability temporarily treated
    # as available. This reveals requested work before its missing-key filter,
    # preserving autostyle ownership and parent rules without disabling features.
    resolve_requested = FunctionType(original.__code__, {**original.__globals__,
        "capabilities":lambda:{"features":{key:{"ok":True} for key in studio.features.KEYS}}},
        original.__name__, original.__defaults__, original.__closure__)
    wanted = resolve_requested(info,asked)
    if wanted.get("threed"):
        results.append(item("webgl","3D graphics support",checks.get("webgl"),"The graphics browser could not create a 3D canvas. Reopen the launcher and check your graphics drivers before using this preset."))
    steps = spec.get("steps") or ["script","voiceover","pexels","images","video","thumbnail"]
    if not spec.get("byo_audio") and any(s in steps for s in ("voiceover","video")):
        voice = caps.get("base",{}).get("voice",{})
        results.append(item("voice", "Narration account", voice.get("ok"), voice.get("why") or "Connect narration in step 4."))
    for feature, active in wanted.items():
        if active and "video" in steps or active and feature == "ai_images" and any(s in steps for s in ("images","thumbnail")):
            cap = caps.get("features",{}).get(feature,{"ok":True})
            label = studio.features.BY_KEY.get(feature,{}).get("label",feature)
            results.append(item(feature,label,cap.get("ok"),cap.get("why") or "Connect the required account or explicitly turn this option off."))
    # Validate selected replacement accounts before the first voice/image job.
    checked = set()
    for task in ("narration","images","motion","avatars"):
        if not cr_media.active(task):
            continue
        if task == "avatars" and not wanted.get("avatar") or task == "motion" and not wanted.get("ai_images"):
            continue
        try:
            provider,key = cr_media.account(task)
            if provider not in checked:
                _check_hub(provider,key)
                checked.add(provider)
            results.append(item("account_"+provider,cr_media.PROVIDERS[provider]["label"]+" API access",True))
        except (Exception,cr_media.AccountStop):
            results.append(item("account_"+task,task.title()+" account",False,"Reconnect or top up the selected "+task+" account in step 4."))
    result = {"ready":all(r["ready"] for r in results),"checks":results,
              "blockers":[r["message"] for r in results if not r["ready"]],"features":wanted}
    checks["claude_code"] = ready["claude_code"]
    result["proof"] = proof(checks,media_ready=result["ready"])
    return result
