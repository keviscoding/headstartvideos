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


def test_landing_page_has_the_moodboard_and_icons():
    import json, re
    page = (server.STATIC_DIR / "landing.html").read_text()
    assert 'href="/favicon.ico"' in page and "apple-touch-icon" in page
    assert "youtube-nocookie" not in page and "data-yt-id" not in page  # the launch video is gone
    wall = json.loads(re.search(r'<script type="application/json" id="wall-clips">(.*?)</script>', page, re.S).group(1))
    assert len(wall) >= 30 and {c["t"] for c in wall} == {0, 1}  # Shorts and long videos both on the wall
    links = re.findall(r'"/static/(moodboard/[^"?]+)"', page)
    assert len(page.split('class="mood ')) - 1 >= 30
    for rel in set(links) | {c["v"][8:] for c in wall} | {c["p"][8:] for c in wall}:
        assert (server.STATIC_DIR / rel).is_file(), rel
    # served with every clip at its content version, so the edge can keep them for a year
    served = TestClient(server.app).get("/").text
    assert re.search(r'/static/moodboard/[a-z0-9-]+\.mp4\?v=[0-9a-f]{10}', served)
    clip = TestClient(server.app).get("/static/moodboard/" + wall[0]["v"].rsplit("/", 1)[1])
    assert clip.status_code == 200 and clip.headers["content-type"] == "video/mp4"


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


def test_the_harness_example_videos_load_only_when_played():
    page = TestClient(server.app).get("/3d-harness").text
    cdn = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/harness/"
    for stem in ("b59-3d-reconstruction-v1.mp4", "killer-mountain-deadly-places-v1.mp4"):
        assert f'data-src="{cdn}{stem}"' in page
    assert page.count('class="ex"') == 2 and page.count('loading="lazy"') >= 2
    assert "<video" not in page, "no video is fetched until someone presses play"
