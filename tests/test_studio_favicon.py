"""The studio's own tab icon (it works offline): the website's mark, with a status dot."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_favicon  # noqa: E402


def test_the_icon_is_the_websites_own():
    site = Path(__file__).parents[1] / "webapp/static"
    assert cr_favicon.ICO == (site / "favicon.ico").read_bytes()
    assert cr_favicon.SVG == (site / "favicon.svg").read_text().strip()


def test_a_status_dot_only_for_busy_or_done():
    assert cr_favicon.svg() == cr_favicon.svg("anything else") == cr_favicon.SVG
    for state, color in (("busy", "#9B8CFF"), ("done", "#14B87A")):
        out = cr_favicon.svg(state)
        assert out.endswith("</svg>") and color in out and out.count("<svg") == 1
