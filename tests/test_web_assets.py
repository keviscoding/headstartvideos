"""The site's pages and static files: versioned links, cache headers, protective headers, sitemap."""
import re

from fastapi.testclient import TestClient

from webapp import server, web_assets


def client():
    return TestClient(server.app)


def test_pages_link_static_files_at_their_content_version():
    c = client()
    for page in ("/", "/app", "/privacy", "/terms", "/3d-harness"):
        html = c.get(page).text
        links = re.findall(r'/static/([A-Za-z0-9_./-]+)\?v=([A-Za-z0-9._-]+)', html)
        assert links, page
        for rel, v in links:
            assert v == web_assets.asset_version(rel), (page, rel)
    assert "app.js?v=20261014" not in c.get("/app").text, "hand-made versions are replaced"


def test_a_versioned_file_is_cached_for_a_year_and_an_unversioned_one_for_an_hour():
    c = client()
    v = web_assets.asset_version("app.js")
    assert c.get(f"/static/app.js?v={v}").headers["cache-control"] == web_assets.YEAR
    assert c.get("/static/app.js").headers["cache-control"] == web_assets.HOUR
    assert c.get("/static/app.js?v=old").headers["cache-control"] == web_assets.HOUR, "an old link never pins new code"
    assert c.get("/static/no-such-file.js").status_code == 404


def test_pages_are_short_lived_and_the_app_shell_always_revalidates():
    c = client()
    assert c.get("/").headers["cache-control"] == web_assets.PAGE
    assert c.get("/app").headers["cache-control"] == web_assets.APP
    assert c.get("/community/discord").headers["cache-control"] == "private, no-store"


def test_every_response_carries_protective_headers_and_pages_cannot_be_framed_elsewhere():
    c = client()
    page, api = c.get("/"), c.get("/api/health")
    for r in (page, api):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "max-age=" in r.headers["strict-transport-security"]
    assert page.headers["x-frame-options"] == "SAMEORIGIN"
    assert page.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert "x-frame-options" not in api.headers


def test_the_api_reference_is_not_public():
    c = client()
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(p).status_code == 404, p


def test_search_engines_get_a_sitemap():
    c = client()
    xml = c.get("/sitemap.xml")
    assert xml.status_code == 200 and xml.headers["content-type"].startswith("application/xml")
    for p in server.SITEMAP_PAGES:
        assert f"<loc>https://channelrecipe.com{p}</loc>" in xml.text
    assert "Sitemap: https://channelrecipe.com/sitemap.xml" in c.get("/robots.txt").text


def test_a_static_link_outside_the_folder_is_never_versioned():
    assert web_assets.asset_version("../server.py") == ""
    assert web_assets.versioned('<a href="/static/../server.py">') == '<a href="/static/../server.py">'


def test_every_public_page_has_a_share_card_and_a_canonical_link():
    c = client()
    for page, image in (("/", "og/home.jpg"), ("/3d-harness", "og/harness.jpg"), ("/app", "og/home.jpg"),
                        ("/privacy", "og/home.jpg"), ("/terms", "og/home.jpg")):
        html = c.get(page).text
        assert f'<link rel="canonical" href="https://channelrecipe.com{page}"' in html, page
        assert f'<meta property="og:image" content="https://channelrecipe.com/static/{image}">' in html, page
        assert '<meta name="twitter:card" content="summary_large_image">' in html and 'name="description"' in html, page
        got = c.get(f"/static/{image}")
        assert got.status_code == 200 and got.content[:3] == b"\xff\xd8\xff" and len(got.content) < 300_000


def test_the_homepage_welcomes_back_customers_and_stays_light():
    html = client().get("/").text
    assert 'href="/app?signin=1#local-studio" class="nav-login">Log in</a>' in html and 'href="/3d-harness"' in html
    assert ".gif" not in html, "previews are small looping videos, not 1.6 MB of GIFs"
    assert html.count('data-src="/static/previews/') == 4 and 'preload="none"' in html
    assert "b59-3d-reconstruction-v1.mp4" in html and "killer-mountain-deadly-places-v1.mp4" in html
    assert html.index('id="showcase"') < html.index("Classic recipes for quick cloud videos"), "Studio before Classic"
    assert 'class="cookie-bar"' in html and "youtube.com/@editarick" in html
    assert "you'd" not in html and "you’d" in html, "typographic apostrophes in the copy"
    js = (web_assets.STATIC_DIR / "app.js").read_text()
    assert "function maybeOpenSignIn()" in js and "maybeOpenSignIn();" in js


def test_every_classic_recipe_preview_it_names_exists():
    import json
    root = web_assets.STATIC_DIR.parent / "niches"
    missing = []
    for f in sorted(root.glob("*.json")):
        gif = json.loads(f.read_text()).get("preview_gif") or ""
        if gif.startswith("/static/") and not (web_assets.STATIC_DIR / gif[len("/static/"):]).is_file():
            missing.append((f.name, gif))
    assert missing == [], missing
