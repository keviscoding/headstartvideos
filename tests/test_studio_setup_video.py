"""The setup walkthrough on the member install guide (the onboarding people get stuck in)."""
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "webapp" / "static" / "local-studio.js").read_text()


def test_install_guide_opens_with_the_walkthrough():
    setup = JS[JS.index("function setupSteps()"):JS.index("function planPanel(")]
    assert "setupVideo()" in setup                       # first thing in the install steps (and "another computer")
    assert "public/onboarding/studio-setup-walkthrough-v1" in JS


def test_walkthrough_loads_only_when_asked():
    card = JS[JS.index("function setupVideo()"):JS.index("function setupSteps()")]
    assert 'preload="none"' in card and "autoplay" not in card
    assert "data-ls-walk-seek" in card                   # chapters jump to the step someone is stuck on


def test_walkthrough_is_for_members_not_the_landing_page():
    guest = JS[JS.index("function features()"):JS.index("function planCards()")]
    assert "setupVideo" not in guest
