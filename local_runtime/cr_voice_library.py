"""Choose voice with ElevenLabs connected directly: the creator's ElevenLabs voices and its shared library.

Setup's "Paste an ElevenLabs key" saves narration as the engine's own ("original") provider and tells the creator to
"pick any voice from its library in Create → Choose voice". The picker only knew the media-hub accounts (Kie.ai,
Atlas Cloud), so with ElevenLabs connected it said "Connect a voice account in Setup & accounts first" (the owner,
2026-10-21: "it keeps telling me to connect the voice account even though it's connected"), and setup reported
narration as not connected.

With ElevenLabs direct, /cr-voices answers in library mode, /cr-voices/library lists one page of the creator's own
voices or ElevenLabs' shared library (the engine's voices.library: each voice has its own free sample), and
/cr-voices/select keeps the chosen voice for that recipe with the engine's voices.set_pick, the same pick the
engine's Voice step makes. Ranking Shorts read it too (cr_shorts.ranking_voice). The media-hub picker is unchanged.
"""
from __future__ import annotations

import os
import re

import cr_bridge

VOICE_ID = re.compile(r"[A-Za-z0-9]{16,32}\Z")
STYLE = re.compile(r"[\w.-]{1,120}\Z")


def direct() -> bool:
    """Narration is ElevenLabs on the creator's own key (setup's ElevenLabs card), not a media-hub account."""
    narration = cr_bridge.load_settings().get("narration") or {}
    return narration.get("provider") == "original" and bool(os.getenv("ELEVENLABS_API_KEY", "").strip())


def current_pick(studio, style: str) -> dict:
    voices = getattr(studio, "voices", None)
    try:
        pick = (voices.picks() if voices else {}).get(style) or {}
    except Exception:                                          # noqa: BLE001 - an unreadable picks file: no pick
        pick = {}
    return pick if pick.get("provider") == "elevenlabs" and pick.get("voice_id") else {}


def install(studio, activity):
    from flask import jsonify, request
    app = studio.app
    listing = app.view_functions.get("voices_list")
    selecting = app.view_functions.get("voices_select")

    def recipe():
        style = str(request.args.get("style") or (request.get_json(silent=True) or {}).get("style") or "").strip()
        styles = getattr(getattr(studio, "styles", None), "STYLES", None)
        if not STYLE.fullmatch(style) or (isinstance(styles, dict) and style not in styles):
            return ""
        return style

    def voices_list():
        if not direct():
            return listing()
        style = recipe()
        pick = current_pick(studio, style) if style else {}
        return jsonify(connected=True, library=True, provider="elevenlabs", provider_label="ElevenLabs",
                       model="elevenlabs", model_label="Your ElevenLabs account", style=style,
                       voice=pick.get("voice_name") or "", voice_id=pick.get("voice_id") or "", voices=[], previews=[])

    def voices_select():
        if not direct():
            return selecting()
        data = request.get_json(silent=True) or {}
        voice_id, name = str(data.get("voice_id") or "").strip(), str(data.get("name") or "").strip()[:90]
        style = recipe()
        if activity.busy():
            return jsonify(error="Finish the video that's running, then change the voice for your next one."), 409
        if not style:
            return jsonify(error="Choose a recipe first, then its voice."), 400
        if not VOICE_ID.fullmatch(voice_id):
            return jsonify(error="Choose a voice from the list."), 400
        gender = str(data.get("gender") or "").lower()
        try:
            studio.voices.set_pick(style, "elevenlabs", voice_id, name or voice_id, gender)
            studio.styles.load_all()
            studio.styles.apply_to_engine(studio.mv, studio.motion)
        except (ValueError, AttributeError) as error:
            return jsonify(error=str(error) or "That voice couldn't be kept."), 400
        label = ((getattr(studio.styles, "STYLES", {}) or {}).get(style) or {}).get("label") or "this recipe's"
        return jsonify(saved=True, voice=name or voice_id, voice_id=voice_id,
                       message=f"Your next {label} videos use {name or 'that voice'} from your ElevenLabs account.")

    @app.get("/cr-voices/library")
    def voices_library():
        if not direct():
            return jsonify(error="Connect ElevenLabs in Setup & accounts to browse its voices."), 409
        a = request.args
        source = "mine" if a.get("source") == "mine" else ""
        try:
            page = max(1, min(200, int(a.get("page") or 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            got = studio.voices.library("elevenlabs", source, str(a.get("search") or "")[:80],
                                        str(a.get("gender") or "")[:8], "", page)
        except Exception as error:                             # noqa: BLE001 - the dialog says what went wrong
            return jsonify(error=str(error)[:200] or "ElevenLabs didn't answer.", voices=[], more=False), 502
        rows = [{k: v.get(k, "") for k in ("id", "name", "gender", "accent", "age", "description", "preview")}
                for v in got.get("voices") or [] if VOICE_ID.fullmatch(str(v.get("id") or ""))]
        return jsonify(voices=rows, more=bool(got.get("more")), note=got.get("note") or "", source=source or "shared")

    if listing is not None:
        app.view_functions["voices_list"] = voices_list
    if selecting is not None:
        app.view_functions["voices_select"] = voices_select
