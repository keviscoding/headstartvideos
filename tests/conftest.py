"""Shared test settings."""
import sys

import pytest


@pytest.fixture(autouse=True)
def _a_computer_with_room_for_a_style_study(monkeypatch):
    """Style studies check this computer's free disk and memory before starting (cr_style_clone.room_to_check); the
    tests stand in a computer with room, so they never depend on the machine running them."""
    clone = sys.modules.get("cr_style_clone")
    if clone is not None and hasattr(clone, "_room"):
        monkeypatch.setattr(clone, "_room", lambda: "")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_setup_checks: run setup's network and speech-model checks for real "
                                       "(their own tests), instead of the stand-ins every other test gets")


@pytest.fixture(autouse=True)
def _a_reachable_network_and_a_ready_speech_model(request, monkeypatch):
    """Setup checks the network (TLS handshakes to the hosts Studio uses) and the speech-timing model (downloaded and
    loaded once). Other tests stand in a computer where both pass, so they never touch the network or depend on this
    machine's model cache."""
    if request.node.get_closest_marker("real_setup_checks"):
        return
    network, whisper = sys.modules.get("cr_network"), sys.modules.get("cr_whisper")
    if network is not None:
        monkeypatch.setattr(network, "status", lambda *a, **k: {
            "ready": True, "message": "This computer reaches every service Studio uses.", "hosts": [], "blocked": [],
            "warnings": [], "fallbacks": [], "unreachable": []})
        monkeypatch.setattr(network, "start", lambda *a, **k: None)
    if whisper is not None:
        monkeypatch.setattr(whisper, "status", lambda: {"ready": True, "state": "ready", "reason": ""})
        monkeypatch.setattr(whisper, "start", lambda: None)
        monkeypatch.setattr(whisper, "local_path", lambda *a, **k: "")
