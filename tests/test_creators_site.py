"""channelrecipe.com/creators: the creators landing with its launch film, served from webapp/static/creators."""
import re

from fastapi.testclient import TestClient

import webapp.server as server

CDN = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/creators/v1/"


def test_creators_redirects_to_its_folder_and_serves_the_landing():
    client = TestClient(server.app)
    r = client.get("/creators", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/creators/"
    page = client.get("/creators/")
    assert page.status_code == 200 and "Edit like the creators you watch" in page.text
    assert '<meta name="robots" content="noindex">' in page.text  # unannounced until the owner says so


def test_the_launch_film_sits_under_the_headline_and_plays_from_the_cdn():
    page = TestClient(server.app).get("/creators/").text
    assert page.index('id="hero-h"') < page.index('id="film"') < page.index('class="hero-cta"')
    for v in ("creators-launch-1080-v1.mp4", "creators-launch-720-v1.mp4", "creators-launch-poster-v1.jpg"):
        assert CDN + "film/" + v in page


def test_every_page_file_is_served_and_no_local_media_link_is_left():
    client = TestClient(server.app)
    page = client.get("/creators/").text
    for rel in set(re.findall(r'(?:href|src)="((?!https?:|#|/)[^"#?]+)', page)):
        assert client.get("/creators/" + rel).status_code == 200, rel
    assert client.get("/creators/app.html").status_code == 200
    assert client.get("/creators/media/local/looks.json").json()
    for f in (server.STATIC_DIR / "creators").rglob("*"):
        if f.suffix in (".html", ".js", ".json", ".css"):
            assert not re.search(r"media/local/(?![\w-]+\.json)", f.read_text()), f


def test_whats_in_an_edit_shows_real_moments_not_diagrams():
    page = TestClient(server.app).get("/creators/").text
    section = page[page.index('id="every-edit"'):page.index('id="learn"')]
    for clip in ("broll", "title", "ticket", "music"):
        assert f'src="{CDN}clips/{clip}.mp4"' in section
    assert "Loudness on target" not in section and "data-wave" not in section and "data-cutline" not in section
