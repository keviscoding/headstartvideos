"""Expose selected CR media accounts to Frontier's existing feature controls.

This adjusts account readiness only. The original style and feature resolver
still decides what is offered, enabled, and included in each video's plan.
"""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlsplit

import cr_bridge
import cr_media


def _brain_readiness():
    """Check saved CR configuration, not remote account access or model health."""
    cfg = cr_bridge.load_settings()
    key = cfg.get("key") or ""
    configured = isinstance(key, str) and key.startswith("cr_mcp_") and len(key) > len("cr_mcp_")
    result = {"ok": False, "configured": configured, "verification": "before_run"}
    if not configured:
        return {**result, "why": "Connect your ChannelRecipe account in Setup & accounts before using the CR brain."}
    try:
        parsed = urlsplit(cfg.get("url", "https://channelrecipe.com"))
        secure = bool(parsed.hostname) and (parsed.scheme == "https" or
                 (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")))
    except (ValueError, TypeError, AttributeError):
        secure = False
    if not secure:
        return {**result, "why": "Connect to ChannelRecipe using a secure HTTPS address in Setup & accounts."}
    if not cfg.get("costs_accepted"):
        return {**result, "why": "Review provider spending in Setup & accounts before using the CR brain."}
    if cfg.get("brain_mode") == "claudecode":
        try:
            import cr_claude
            local=cr_claude.status()
            if not local["ready"] or not cr_claude.models_verified(cfg,local):
                return {**result,"why":"Sign in to Claude Code and verify both selected models in Setup & accounts."}
        except (ImportError,ValueError):
            return {**result,"why":"Complete Claude Code setup before using the studio."}
    # /api/run already checks the connected remote brain before dispatch.
    # A saved key alone must never be presented as verified remote readiness.
    return {**result, "ok": True, "why": ""}


def _readiness(task):
    """None means keep the original provider's capability; never call an API."""
    if not cr_media.active(task):
        return None
    try:
        provider, _ = cr_media.account(task)
        cfg = cr_media.profile(task)
        info = cr_media.PROVIDERS[provider]
        if task == "narration":
            model = cr_media.narration_model(provider, cfg.get("model"))
            voice = cfg.get("voice") or model["default_voice"]
            if model.get("voice_names") and voice not in model["voice_names"]:
                raise cr_media.AccountStop("Choose a voice name supported by your narration model in Setup & accounts.")
        elif task == "images":
            if provider == "kie":
                cr_media.image_plan(cfg)
            elif not info["models"].get("images"):
                raise cr_media.AccountStop("Choose a supported image account in Setup & accounts.")
        elif task == "avatars" and not info["models"].get("avatars"):
            raise cr_media.AccountStop("Choose a supported Kling presenter account in Setup & accounts.")
        return {"ok": True, "why": ""}
    except cr_media.AccountStop as error:
        return {"ok": False, "why": str(error)}


def install(studio):
    """Wrap the native capabilities lookup, leaving native feature rules intact."""
    original = studio.features.capabilities
    if getattr(original, "_cr_media_capabilities", False):
        return

    def capabilities():
        result = deepcopy(original())
        if cr_bridge.load_settings().get("brain_mode") == "claudecode" or getattr(getattr(studio, "mv", None), "CLAUDE_PROVIDER", None) == "channelrecipe":
            result.setdefault("base", {})["claude"] = _brain_readiness()
        # Image animation is a provider task, not the native 'motion' switch
        # for locally rendered graphics. It must not disable those graphics or
        # enable stock, footage analysis, cutouts, or other specialised paths.
        for task, section, key in (("narration", "base", "voice"),
                                   ("images", "features", "ai_images"),
                                   ("avatars", "features", "avatar")):
            readiness = _readiness(task)
            if readiness is not None:
                result.setdefault(section, {})[key] = readiness
        return result

    capabilities._cr_media_capabilities = True
    studio.features.capabilities = capabilities
