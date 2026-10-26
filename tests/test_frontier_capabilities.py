"""Readiness follows selected accounts without changing the native feature mix."""
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_capabilities as capabilities
import cr_media as media


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(media.cr_bridge, "SETTINGS", tmp_path / ".cr-connection.json")
    monkeypatch.setattr(media, "_native_images", None)
    monkeypatch.delenv("KIE_API_KEY", raising=False)
    monkeypatch.delenv("ATLASCLOUD_API_KEY", raising=False)
    monkeypatch.setattr(media.requests, "request", lambda *a, **k: pytest.fail("Readiness must not call a provider"))
    monkeypatch.setattr(media.requests, "get", lambda *a, **k: pytest.fail("Readiness must not call a provider"))
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: pytest.fail("Readiness must not call a provider"))
    original = {
        "base": {"claude": {"ok": False, "why": "Original brain requirement"},
                 "voice": {"ok": False, "why": "Original voice requirement"}},
        "features": {key: {"ok": key in ("motion", "maps", "headlines", "sound"),
                           "why": "" if key in ("motion", "maps", "headlines", "sound") else "Original " + key + " requirement"}
                     for key in ("youtube", "stock", "ai_images", "avatar", "depth", "vox", "spotlight", "objects", "threed", "motion", "maps", "headlines", "sound")},
    }
    app = SimpleNamespace(features=SimpleNamespace(capabilities=lambda: original), native_capabilities=original,
                          mv=SimpleNamespace(CLAUDE_PROVIDER="channelrecipe"))
    capabilities.install(app)
    return app


def settings(**values):
    media.cr_bridge.save_settings({"key": "cr_mcp_test_only", "costs_accepted": True, **values})


def test_atlas_only_enables_supported_media_without_inventing_dependencies(studio, monkeypatch):
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "atlas-test-key")
    settings(media_hub={"provider": "atlas", "tasks": ["narration", "images", "motion", "avatars"]})
    original = deepcopy(studio.native_capabilities)
    result = studio.features.capabilities()
    assert result["base"]["voice"] == {"ok": True, "why": ""}
    assert result["features"]["ai_images"] == result["features"]["avatar"] == {"ok": True, "why": ""}
    assert result["base"]["claude"] == {"ok": True, "why": "", "configured": True, "verification": "before_run"}
    for key in result["features"]:
        if key not in ("ai_images", "avatar"):
            assert result["features"][key] == original["features"][key]
    assert studio.native_capabilities == original


def test_kie_voice_only_keeps_original_image_footage_and_presenter_choices(studio, monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "kie-test-key")
    settings(narration={"provider": "kie", "model": "google/gemini-3-8-flash-tts", "voice": "Kore"})
    result = studio.features.capabilities()
    assert result["base"]["voice"]["ok"]
    assert result["features"] == studio.native_capabilities["features"]


def test_kie_presenter_can_use_hub_hosting_without_a_wavespeed_key(studio, monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "kie-test-key")
    settings(media_hub={"provider": "kie", "tasks": ["avatars"]})
    result = studio.features.capabilities()
    assert result["features"]["avatar"]["ok"]
    assert result["base"]["voice"] == studio.native_capabilities["base"]["voice"]
    assert not result["features"]["ai_images"]["ok"]


def test_selected_account_missing_key_does_not_fall_back_to_another_ready_provider(studio):
    studio.native_capabilities["base"]["voice"] = {"ok": True, "why": ""}
    studio.native_capabilities["features"]["ai_images"] = {"ok": True, "why": ""}
    settings(media_hub={"provider": "atlas", "tasks": ["narration", "images"]})
    result = studio.features.capabilities()
    assert not result["base"]["voice"]["ok"]
    assert not result["features"]["ai_images"]["ok"]
    assert "Reconnect Atlas Cloud" in result["features"]["ai_images"]["why"]


@pytest.mark.parametrize("values,expected", [
    ({"narration": {"provider": "kie", "model": "unsupported"}}, "supported voice model"),
    ({"costs_accepted": False, "narration": {"provider": "kie"}}, "spending agreement"),
])
def test_invalid_voice_and_missing_spending_agreement_stay_unavailable(studio, monkeypatch, values, expected):
    monkeypatch.setenv("KIE_API_KEY", "kie-test-key")
    settings(**values)
    result = studio.features.capabilities()
    assert not result["base"]["voice"]["ok"]
    assert expected in result["base"]["voice"]["why"]


@pytest.mark.parametrize("narration", [
    {"provider": "kie", "model": "google/gemini-3-8-flash-tts", "voice": "Unknown"},
    {"provider": "kie", "model": "elevenlabs/text-to-speech-turbo-2-5", "voice": "EkK5I93UQWFDigLMpZcX"}])
def test_a_voice_kie_no_longer_has_is_read_by_its_gemini_stand_in(studio, monkeypatch, narration):
    """Kie narrates with Gemini voices only (owner, 2026-10-26): a saved voice or model Kie's Gemini doesn't have
    (an ElevenLabs one, from before) is read by the Gemini voice of its kind, never a blocked Create."""
    monkeypatch.setenv("KIE_API_KEY", "kie-test-key")
    settings(narration=narration)
    assert studio.features.capabilities()["base"]["voice"]["ok"]


def test_original_narration_and_unselected_media_preserve_native_capabilities(studio, monkeypatch):
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "atlas-test-key")
    settings(narration={"provider": "original"}, media_hub={"provider": "atlas", "tasks": ["motion"]})
    result = studio.features.capabilities()
    assert result["features"] == studio.native_capabilities["features"]
    assert result["base"]["voice"] == studio.native_capabilities["base"]["voice"]


def test_invalid_image_choice_is_reported_without_affecting_other_switches(studio, monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "kie-test-key")
    settings(images={"provider": "kie", "model": "unsupported"})
    result = studio.features.capabilities()
    assert not result["features"]["ai_images"]["ok"]
    assert "supported Kie image model" in result["features"]["ai_images"]["why"]
    assert result["features"]["motion"] == studio.native_capabilities["features"]["motion"]


def test_native_kie_image_choice_is_recognized_and_install_is_idempotent(studio, monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "kie-test-key")
    monkeypatch.setattr(media, "_native_images", lambda: "kie")
    settings()
    lookup = studio.features.capabilities
    capabilities.install(studio)
    assert studio.features.capabilities is lookup
    assert studio.features.capabilities()["features"]["ai_images"]["ok"]
    monkeypatch.delenv("KIE_API_KEY")
    assert not studio.features.capabilities()["features"]["ai_images"]["ok"]


def test_cr_brain_uses_saved_connection_instead_of_legacy_claude_cli_requirements(studio):
    settings()
    result = studio.features.capabilities()["base"]["claude"]
    assert result["ok"] and result["configured"]
    assert result["verification"] == "before_run"
    assert not studio.native_capabilities["base"]["claude"]["ok"]


@pytest.mark.parametrize("key", [None, "", "other-provider-key", "cr_mcp_"])
def test_missing_or_invalid_cr_key_reports_setup_even_if_legacy_claude_is_ready(studio, key):
    studio.native_capabilities["base"]["claude"] = {"ok": True, "why": ""}
    settings(key=key)
    result = studio.features.capabilities()["base"]["claude"]
    assert not result["ok"] and not result["configured"]
    assert "Connect your ChannelRecipe account" in result["why"]
    assert "key" not in result and "key_enc" not in result


def test_cr_brain_requires_spending_acknowledgement_and_secure_connection(studio):
    settings(costs_accepted=False)
    result = studio.features.capabilities()["base"]["claude"]
    assert not result["ok"] and result["configured"]
    assert "provider spending" in result["why"]
    settings(url="http://remote.example")
    result = studio.features.capabilities()["base"]["claude"]
    assert not result["ok"] and "secure HTTPS" in result["why"]
    settings(url="http://127.0.0.1:9000")
    assert studio.features.capabilities()["base"]["claude"]["ok"]


def test_native_brain_capability_is_untouched_when_cr_bridge_is_not_installed(studio):
    studio.mv.CLAUDE_PROVIDER = "claudecode"
    settings(key="")
    assert studio.features.capabilities()["base"]["claude"] == studio.native_capabilities["base"]["claude"]
