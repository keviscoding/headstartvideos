"""Choose voice with ElevenLabs connected directly (local_runtime/cr_voice_library.py). The owner, 2026-10-21: "it
keeps telling me to connect the voice account even though it's connected": setup's ElevenLabs card saves narration
as the engine's own provider, and the picker only knew Kie.ai and Atlas Cloud."""
import sys
import types
from pathlib import Path

import pytest
from flask import Flask, jsonify

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_bridge
import cr_shorts
import cr_voice_library

VOICE = "pNInz6obpgDQGcFmaJgB"


@pytest.fixture
def studio(monkeypatch):
    calls = {"set_pick": [], "library": [], "applied": 0}
    app = Flask("voice-library-test")

    @app.get("/cr-voices", endpoint="voices_list")
    def voices_list():                                         # the media-hub picker (Kie.ai, Atlas Cloud)
        return jsonify(connected=False, voices=[], previews=[], hub=True)

    @app.post("/cr-voices/select", endpoint="voices_select")
    def voices_select():
        return jsonify(hub=True)

    picks = {"cr-shorts-ranking": {"provider": "elevenlabs", "voice_id": VOICE, "voice_name": "Adam"}}
    voices = types.SimpleNamespace(
        picks=lambda: picks,
        set_pick=lambda *a: calls["set_pick"].append(a) or picks.update({a[0]: {"provider": a[1], "voice_id": a[2],
                                                                                "voice_name": a[3]}}),
        library=lambda *a: calls["library"].append(a) or {"voices": [
            {"id": VOICE, "name": "Adam", "gender": "male", "accent": "american", "preview": "https://x/adam.mp3"},
            {"id": "minimax_bad", "name": "not an ElevenLabs id"}], "more": False})
    styles = types.SimpleNamespace(STYLES={"cr-shorts-ranking": {"label": "Ranking Shorts"}, "jung": {"label": "Jung"}},
                                   load_all=lambda: None,
                                   apply_to_engine=lambda mv, motion: calls.__setitem__("applied", calls["applied"] + 1))
    s = types.SimpleNamespace(app=app, voices=voices, styles=styles, mv=object(), motion=object())
    busy = types.SimpleNamespace(busy=lambda: False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_" + "a" * 40)
    settings = {"narration": {"provider": "original"}}
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: settings)
    cr_voice_library.install(s, busy)
    return s, calls, settings, busy


def test_elevenlabs_direct_is_connected_and_lists_its_library(studio):
    s, calls, _, _ = studio
    client = s.app.test_client()
    got = client.get("/cr-voices?style=cr-shorts-ranking").get_json()
    assert got["connected"] is True and got["library"] is True and got["voice"] == "Adam" and got["voice_id"] == VOICE
    rows = client.get("/cr-voices/library?source=mine&search=ad").get_json()
    assert [r["id"] for r in rows["voices"]] == [VOICE] and rows["voices"][0]["preview"] == "https://x/adam.mp3"
    assert calls["library"][-1][:3] == ("elevenlabs", "mine", "ad")
    client.get("/cr-voices/library?source=anything")
    assert calls["library"][-1][1] == ""                       # the shared library


def test_choosing_keeps_the_voice_for_that_recipe(studio):
    s, calls, _, busy = studio
    client = s.app.test_client()
    got = client.post("/cr-voices/select", json={"voice_id": VOICE, "name": "Adam", "gender": "male",
                                                 "style": "jung"}).get_json()
    assert got["saved"] is True and "Jung" in got["message"] and "Adam" in got["message"]
    assert calls["set_pick"][-1] == ("jung", "elevenlabs", VOICE, "Adam", "male") and calls["applied"] == 1
    assert client.post("/cr-voices/select", json={"voice_id": "nope", "style": "jung"}).status_code == 400
    assert client.post("/cr-voices/select", json={"voice_id": VOICE, "style": "no-such-recipe"}).status_code == 400
    busy.busy = lambda: True
    assert client.post("/cr-voices/select", json={"voice_id": VOICE, "style": "jung"}).status_code == 409


def test_a_media_hub_account_keeps_its_own_picker(studio):
    s, _, settings, _ = studio
    settings["narration"] = {"provider": "kie"}
    client = s.app.test_client()
    assert client.get("/cr-voices").get_json()["hub"] is True
    assert client.post("/cr-voices/select", json={"voice": "Puck"}).get_json()["hub"] is True
    assert client.get("/cr-voices/library").status_code == 409


def test_without_a_key_it_is_not_elevenlabs(studio, monkeypatch):
    s, _, _, _ = studio
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    assert s.app.test_client().get("/cr-voices").get_json()["hub"] is True


def test_ranking_shorts_use_the_voice_chosen_for_their_recipe(monkeypatch):
    fake = types.SimpleNamespace(picks=lambda: {"cr-shorts-ranking": {"provider": "elevenlabs", "voice_id": VOICE},
                                                "kie-one": {"provider": "kie", "voice_id": VOICE},
                                                "odd": {"provider": "elevenlabs", "voice_id": "../../etc"}})
    monkeypatch.setitem(sys.modules, "voices", fake)
    assert cr_shorts.ranking_voice("cr-shorts-ranking") == "eleven:" + VOICE
    assert cr_shorts.ranking_voice("kie-one") == "" and cr_shorts.ranking_voice("odd") == ""
    assert cr_shorts.ranking_voice("none") == ""


def test_the_narration_check_knows_elevenlabs_and_ranking_shorts(monkeypatch):
    """The engine's own check counts only Algrow, WaveSpeed and AI33 keys: with ElevenLabs connected every Create stopped
    on 'The voiceover needs ALGROW_API_KEY, WAVESPEED_API_KEY or AI33_API_KEY' (the owner's Ranking Short batch)."""
    import cr_readiness
    picks = {}
    studio = types.SimpleNamespace(voices=types.SimpleNamespace(picks=lambda: picks))
    ranking = {"shorts": {"pipeline": "ranking"}}
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_" + "a" * 40)
    monkeypatch.setenv("KIE_API_KEY", "")
    assert cr_readiness.own_narration(studio, "cr-shorts-ranking", ranking, "old")[0] is True
    ok, why = cr_readiness.own_narration(studio, "jung", {}, "old")
    assert ok is False and "Choose voice" in why                  # long videos: pick the recipe's voice first
    picks["jung"] = {"provider": "elevenlabs", "voice_id": VOICE}
    assert cr_readiness.own_narration(studio, "jung", {}, "old") == (True, "")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    assert cr_readiness.own_narration(studio, "jung", {}, "old") == (False, "old")
    ok, why = cr_readiness.own_narration(studio, "cr-shorts-ranking", ranking, "old")
    assert ok is False and "ElevenLabs or Kie.ai" in why
    monkeypatch.setenv("KIE_API_KEY", "kie_" + "b" * 30)
    assert cr_readiness.own_narration(studio, "cr-shorts-ranking", ranking, "old") == (True, "")
    roblox = {"shorts": {"pipeline": "roblox"}}                   # Roblox Story Shorts narrate themselves too
    assert cr_readiness.own_narration(studio, "cr-shorts-roblox", roblox, "old") == (True, "")
    monkeypatch.setenv("KIE_API_KEY", "")
    assert cr_readiness.own_narration(studio, "cr-shorts-roblox", roblox, "old")[0] is False
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_" + "a" * 40)
    assert cr_readiness.own_narration(studio, "cr-shorts-roblox", roblox, "old") == (True, ""), "no voice pick needed"
