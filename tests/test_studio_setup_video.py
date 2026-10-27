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


def test_studio_setup_page_plays_it_too():
    html = (Path(__file__).resolve().parents[1] / "local_runtime" / "cr_setup.html").read_text()
    assert 'id="walk-open"' in html and "public/onboarding/studio-setup-walkthrough-v1.mp4" in html
    assert 'preload="none"' in html and "WALK_CHAPTERS" in html


def test_studio_plays_the_full_course_in_setup_and_links_it_from_the_sidebar():
    """The owner's full course (2026-10-27) plays in Studio's setup page like the setup walkthrough did (which stays
    one click away), and the workspace sidebar links its page."""
    root = Path(__file__).resolve().parents[1] / "local_runtime"
    setup = (root / "cr_setup.html").read_text(encoding="utf-8")
    assert "Watch the full course <small>10:49</small>" in setup and 'id="walk-setup"' in setup
    assert "channelrecipe-full-course-v1.mp4" in setup and "COURSE_CHAPTERS" in setup and "openWalk('setup')" in setup
    assert 'href="https://channelrecipe.com/course"' in (root / "cr_workspace.html").read_text(encoding="utf-8")
