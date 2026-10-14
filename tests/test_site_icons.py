"""Browsers, iOS and search engines find the logo at the site root."""
from fastapi.testclient import TestClient

import webapp.server as server


def test_root_icons_are_served():
    client = TestClient(server.app)
    icon = client.get("/favicon.ico")
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/x-icon"
    assert icon.content[:4] == b"\x00\x00\x01\x00"
    touch = client.get("/apple-touch-icon.png")
    assert touch.status_code == 200 and touch.content.startswith(b"\x89PNG")


def test_landing_page_has_the_launch_video_and_icons():
    page = (server.STATIC_DIR / "landing.html").read_text()
    assert 'data-yt-id="lxVILY1ha6w"' in page and "UQwQi4jK76I" not in page
    assert 'href="/favicon.ico"' in page and "apple-touch-icon" in page
    for name in ("studio-2d", "studio-3d-honeycomb", "studio-3d-story", "studio-painted-history"):
        assert (server.STATIC_DIR / "showcase" / f"{name}.mp4").is_file()
        assert (server.STATIC_DIR / "showcase" / f"{name}.jpg").is_file()
