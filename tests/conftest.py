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
