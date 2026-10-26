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
import cr_machine
import cr_media
import cr_mcp
import cr_network

_accounts = {}


def item(key, label, ready, why=""):
    return {"key": key, "label": label, "ready": bool(ready), "message": "" if ready else why}


def basic(checks, *, verify_mcp=True):
    cfg = cr_bridge.load_settings()
    mode = cfg.get("brain_mode", "channelrecipe")
    account = checks.get("account") or {}
    results = [item("python","Python 3.10 or newer",checks.get("python") is True,"Install Python 3.12, then reopen the launcher."),
        item("connection", "ChannelRecipe account", bool(account), checks.get("connection_error") if
             cfg.get("key") and checks.get("connection_error") else "Connect your CR account in step 2."),
        item("spending", "Account spending acknowledged", cfg.get("costs_accepted"), "Review account costs in step 2."),
        # the releases that may start new videos: the latest, or any the account lists as still supported
        # without the account's answer nothing is known about releases (the connection row says why)
        item("release", "Current workflow installed", not account or checks.get("local_version") in (
            account.get("supported_versions") or [account.get("version")]),
             "A new Studio is out. Press Update now at the top of the studio page: it keeps your projects."),
        item("ffmpeg", "Video and caption tools", checks.get("ffmpeg"), checks.get("ffmpeg_reason") or
             "Open Setup and press Fix it next to Video tools: Studio installs a full FFmpeg in its own folder."),
        item("browser", "Graphics browser", checks.get("browser"), checks.get("browser_reason") or "Open Setup and press Fix it next to Graphics browser."),
        item("node", "JavaScript runtime", checks.get("node"), checks.get("node_reason") or
             "Open Setup and press Fix it next to YouTube helper: Studio installs Node.js in its own folder.")]
    results += setup_rows(checks)
    cli = cr_claude.status()
    cli.update(models=[{"id":m,"label":"Claude Opus 5.5" if "opus" in m else "Claude Sonnet 5.5"} for m in cr_claude.MODELS],
               general_model=cfg.get("general_model", cr_claude.GENERAL_MODEL),
               visual_model=cfg.get("visual_model", cr_claude.VISUAL_MODEL),
               models_saved=bool(cfg.get("general_model") and cfg.get("visual_model") and cfg.get("local_version")),
               billing="Your signed-in Claude Code account and its usage limits apply.")
    if checks.get("cr_unreachable") is True:      # setup found channelrecipe.com unreachable just now: no 90 s wait
        mcp = {"connected": False, "why": checks.get("connection_error") or "Studio can't reach channelrecipe.com."}
    else:
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


def setup_rows(checks):
    """The network and the speech-timing model, found in setup rather than halfway through a video. A network row
    that passes can still carry a note: a blocked host Studio goes around, or YouTube (which only some recipes use:
    preset() stops those)."""
    rows = []
    if "network" in checks:
        net = checks.get("network") or {}
        rows.append({"key": "network", "label": "Network", "ready": net.get("ready", True) is not False,
                     "message": "" if net.get("checking") and not net.get("hosts") or not (
                         net.get("blocked") or net.get("warnings") or net.get("fallbacks")) else net.get("message", "")})
    if "speech_model" in checks:
        speech = checks.get("speech_model") or {}
        rows.append(item("speech_model", "Speech-timing model", speech.get("ready"),
                         speech.get("reason") or "Open Setup and press Fix it next to Speech-timing model."))
    if checks.get("file_paths"):                  # Windows only: a note when the Studio folder is too deep (cr_paths)
        paths = checks["file_paths"]
        rows.append({"key": "file_paths", "label": "Windows file paths", "ready": True, "message": paths.get("message", "")})
    if checks.get("computer"):                    # memory, cores and free disk (cr_machine): a note; preset() checks
        computer = checks["computer"]             # the disk against each video
        rows.append({"key": "computer", "label": "This computer", "ready": True, "message": computer.get("message", "")})
    return rows


def proof(checks, *, media_ready=False, purpose="video"):
    cfg = cr_bridge.load_settings()
    cli = checks.get("claude_code") or cr_claude.status()
    return {"local_version":checks.get("local_version", "5.3-admin.14"), "brain_mode":"claudecode",
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


def own_narration(studio, style, info, why):
    """(ok, why) for narration the engine's own check does not know. That check counts only Algrow, WaveSpeed and AI33
    keys, so with ElevenLabs connected on Setup (narration on the creator's own key) every Create stopped on "The
    voiceover needs ALGROW_API_KEY, WAVESPEED_API_KEY or AI33_API_KEY" (the owner, 2026-10-21). A Ranking or Roblox
    Story Short narrates itself (shorts/ranking/voice.py, shorts/roblox/make_roblox.py: ElevenLabs on the creator's
    key, else ElevenLabs or Gemini through Kie.ai, checked again before anything is paid); any other recipe narrates
    with ElevenLabs once a voice is chosen for it (Create, Choose voice: the engine's voice pick)."""
    eleven = bool(os.getenv("ELEVENLABS_API_KEY", "").strip())
    if ((info or {}).get("shorts") or {}).get("pipeline") in ("ranking", "roblox"):
        if eleven or os.getenv("KIE_API_KEY", "").strip():
            return True, ""
        return False, "Connect ElevenLabs or Kie.ai in Setup & accounts for the narrator."
    if eleven:
        try:
            pick = (studio.voices.picks() or {}).get(style) or {}
        except Exception:                                      # noqa: BLE001 - no pick readable: ask for one
            pick = {}
        if pick.get("provider") == "elevenlabs" and pick.get("voice_id"):
            return True, ""
        return False, ("Choose this recipe's voice in Create, under Narration, Choose voice: your ElevenLabs voices "
                       "are there.")
    return False, why


def saved_gb(studio, spec, titles) -> list:
    """What each title's own working folder already holds on the disk, in GB (0.0 for a video not started yet): a
    saved project carrying on needs only the rest."""
    mv = getattr(studio, "mv", None)
    find, slugify = getattr(mv, "find_job", None), getattr(mv, "slugify", None)
    if not callable(find):
        return []
    slug = str(spec.get("cr_attempt_slug") or "")
    out = []
    for i, title in enumerate(titles or [""]):
        try:
            job = find(slug) if slug and i == 0 else find(slugify(title)) if callable(slugify) and title else None
        except Exception:                                      # noqa: BLE001 - not found: nothing saved to count
            job = None
        out.append(cr_machine.folder_gb(job) if job else 0.0)
    return out


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
    import cr_style_delivery
    if cr_style_delivery.selected(info):
        try:
            cr_style_delivery._prompt()
            results.append(item("reference_workflow", "Full reference animation workflow", True))
        except RuntimeError as error:
            results.append(item("reference_workflow", "Full reference animation workflow", False, str(error)))
    if ((info or {}).get("look") or {}).get("visuals") == "cr_animated2d":
        import cr_animated2d
        problem = cr_animated2d.renderer_problem(info)
        results.append(item("animated2d", "The channel's animation", not problem, problem))
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
    backend = spec.get("render_backend", "original")
    renderer_identity = None
    if backend not in {"original", "manimgx"}:
        results.append(item("renderer", "3D renderer", False, "Choose the standard renderer or Fast mode."))
    if backend == "manimgx":
        import cr_optional_renderer
        renderer = cr_optional_renderer.ready(cr_bridge.HOME)
        renderer_identity = renderer.get("identity")
        results += [item("renderer", "Fast mode installed and checked", renderer["ready"], renderer.get("message", "Complete the optional Fast mode check.")),
            item("renderer_brain", "Claude Code connected for Fast mode", ready["brain_mode"] == "claudecode", "Use your Claude Code brain with Fast mode."),
            item("renderer_use", "3D scenes enabled", wanted.get("threed"), "Enable 3D scenes for this preset, or turn off Fast mode for this video.")]
        if spec.get("manimgx_identity") and spec["manimgx_identity"] != renderer_identity:
            results.append(item("renderer_pin", "Project renderer unchanged", False, "This project's Fast mode installation changed. Review setup before continuing."))
    if wanted.get("threed"):
        results.append(item("webgl","3D graphics support",checks.get("webgl"),"The graphics browser could not create a 3D canvas. Reopen the launcher and check your graphics drivers before using this preset."))
    steps = spec.get("steps") or ["script","voiceover","pexels","images","video","thumbnail"]
    if not spec.get("byo_audio") and any(s in steps for s in ("voiceover","video")):
        voice = caps.get("base",{}).get("voice",{})
        ok, why = voice.get("ok"), voice.get("why") or "Connect narration in step 4."
        if not ok:
            ok, why = own_narration(studio, style, info, why)
        results.append(item("voice", "Narration account", ok, why))
    for feature, active in wanted.items():
        if active and "video" in steps or active and feature == "ai_images" and any(s in steps for s in ("images","thumbnail")):
            cap = caps.get("features",{}).get(feature,{"ok":True})
            label = studio.features.BY_KEY.get(feature,{}).get("label",feature)
            results.append(item(feature,label,cap.get("ok"),cap.get("why") or "Connect the required account or explicitly turn this option off."))
    # Windows' 260-character limit: a finished video's files are named after its title (cr_paths)
    paths = checks.get("file_paths") or {}
    if paths.get("folder") and paths.get("long_paths") is False:
        import cr_paths
        titles = [line for line in str(spec.get("title") or "").splitlines() if line.strip()]
        problem = cr_paths.title_problem(paths["folder"], style, titles, studio.mv.youtube_filename) if titles else ""
        if problem:
            results.append(item("title_length", "Title fits Windows' path limit", False, problem))
    # A disk that fills up stops a video halfway, with nothing anyone can read: it stops here instead (cr_machine),
    # only when even the measured need can't fit; under the comfortable margin it goes ahead with a warning. A saved
    # project carrying on (Resume, a presenter choice, a 3D reassembly) needs only what its folder doesn't hold yet.
    warnings = []
    free = (checks.get("computer") or {}).get("free_disk_gb")
    if free is not None and "video" in steps:
        titles = [line for line in str(spec.get("title") or "").splitlines() if line.strip()]
        room, warning = cr_machine.disk_check(free, spec.get("minutes") or 1, len(titles) or 1,
                                              threed=bool(wanted.get("threed")) or backend == "manimgx",
                                              saved=saved_gb(studio, spec, titles))
        if room:
            results.append(item("disk_space", "Room for this video on the disk", False, room))
        elif warning:
            warnings.append(warning)
    # YouTube has no way around it: a recipe that uses it stops here on a network that can't reach it, not mid-video
    youtube = cr_network.youtube_problem(checks.get("network") or {})
    ranking = ((info or {}).get("shorts") or {}).get("pipeline") == "ranking"
    if youtube and (ranking or wanted.get("youtube") and "video" in steps):
        results.append(item("youtube_network", "YouTube reachable", False, youtube))
    # Validate selected replacement accounts before the first voice/image job.
    checked = set()
    for task in ("narration","images","motion","avatars"):
        if not cr_media.active(task):
            continue
        if task == "avatars" and not wanted.get("avatar") or task == "motion" and not wanted.get("ai_images"):
            continue
        if task == "images" and not wanted.get("ai_images") and "thumbnail" not in steps:
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
    if warnings:
        result["warnings"] = warnings
    if renderer_identity:
        result["manimgx_identity"] = renderer_identity
    checks["claude_code"] = ready["claude_code"]
    result["proof"] = proof(checks,media_ready=result["ready"])
    return result
