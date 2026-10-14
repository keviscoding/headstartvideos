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


def test_discord_is_for_members_only(monkeypatch):
    client = TestClient(server.app)
    session = {"user": None}
    monkeypatch.setattr(server, "_current_user", lambda request: session["user"])
    assert client.get("/api/community/access").json() == {"member": False}
    page = client.get("/community/discord", follow_redirects=False)
    assert page.status_code == 200 and "The Discord is for members" in page.text
    assert "discord.gg" not in page.text
    session["user"] = {"id": 1, "email": "free@example.com", "plan": "free"}
    assert client.get("/community/discord", follow_redirects=False).status_code == 200
    session["user"] = {"id": 2, "email": "creator@example.com", "plan": "daily"}
    assert client.get("/api/community/access").json() == {"member": True}
    joined = client.get("/community/discord", follow_redirects=False)
    assert joined.status_code == 302 and joined.headers["location"] == server.DISCORD_INVITE_URL


def test_public_pages_never_carry_the_invite():
    for name in ("landing.html", "index.html", "local-studio.js", "app.js"):
        assert "discord.gg" not in (server.STATIC_DIR / name).read_text(), name
