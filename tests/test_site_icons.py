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


def test_provider_outcomes_are_logged_not_alerted():
    from webapp import frontier_providers as providers
    outcome = server._expected_provider_outcome
    failure = providers.ProviderFailure
    assert outcome(failure("stopped", "upstream_stream_error", upstream_error_type="overloaded_error")) == \
        "upstream_stream_error (overloaded_error)"
    assert outcome(failure("busy", "upstream_http", upstream_http=429)) == "upstream_http (429)"
    assert outcome(failure("slow", "generation_timeout")) == "generation_timeout"
    # Anything that could be our bug still alerts.
    assert outcome(failure("x", "upstream_stream_error", upstream_error_type="invalid_request_error")) == ""
    assert outcome(failure("x", "unreadable")) == ""
    assert outcome(failure("x", "upstream_http", upstream_http=400)) == ""
    assert outcome(ValueError("ours")) == ""


def test_the_3d_harness_page_and_its_downloads_are_public():
    client = TestClient(server.app)
    page = client.get("/3d-harness")
    assert page.status_code == 200 and "The 3D Documentary" in page.text and "Rule 13" in page.text
    for link in ("/static/harness/3D-Documentary-Harness.pdf", "/static/harness/3D-Documentary-Harness.md"):
        assert link in page.text
        got = client.get(link)
        assert got.status_code == 200 and len(got.content) > 5000, link
    assert client.get("/static/harness/3D-Documentary-Harness.pdf").content.startswith(b"%PDF")
    assert "discord.gg" not in page.text
    assert page.text.count('class="ncard"') == 15 and "Proven niches you can break into today" in page.text
    assert "LEMMiNO" in client.get("/static/harness/3D-Documentary-Harness.md").text
