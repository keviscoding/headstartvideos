"""The search pages (2026-11-27): eight pages at clean URLs, their stylesheet, the sitemap, llms.txt, the Viewmax
redirect, and the homepage's structured data, which must say what the page shows word for word."""
import html
import json
import re

from fastapi.testclient import TestClient

import webapp.server as server

SLUGS = ("viblo-alternative", "everygen-alternative", "viblo-vs-everygen", "crayo-alternative", "invideo-alternative",
         "vidiq-alternative", "clipchamp-alternative", "faceless-youtube-video-generator")


def _ld(page: str) -> list:
    return [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)]


def test_each_page_is_served_at_its_clean_url():
    client = TestClient(server.app)
    assert server.SEO_PAGES == SLUGS
    for slug in SLUGS:
        r = client.get(f"/{slug}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), slug
        assert f'<link rel="canonical" href="https://channelrecipe.com/{slug}">' in r.text, slug
        assert re.search(r'<link rel="stylesheet" href="/static/seo\.css\?v=[0-9a-f]{10}">', r.text), slug
        assert _ld(r.text), slug  # the JSON-LD parses
        assert 'class="verify"' not in r.text and "<!--" not in r.text.split("<head>")[0], slug
        for rel in set(re.findall(r'"/static/([A-Za-z0-9_./-]+?)(?:\?v=[0-9a-f]+)?"', r.text)):
            assert (server.STATIC_DIR / rel).is_file(), (slug, rel)
        for link in set(re.findall(r'href="/([a-z0-9-]+)"', r.text)):
            if link in SLUGS:
                continue
            assert client.get(f"/{link}", follow_redirects=False).status_code in (200, 302), (slug, link)
    css = client.get("/static/seo.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")


def test_sitemap_llms_and_the_viewmax_redirect():
    client = TestClient(server.app)
    xml = client.get("/sitemap.xml").text
    for path in ("/app", *(f"/{s}" for s in SLUGS)):
        assert f"<loc>https://channelrecipe.com{path}</loc>" in xml, path
    llms = client.get("/llms.txt")
    assert llms.status_code == 200 and llms.headers["content-type"].startswith("text/plain")
    assert llms.text.startswith("# ChannelRecipe") and "https://channelrecipe.com/viblo-alternative" in llms.text
    moved = client.get("/viewmax-alternative", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == "/everygen-alternative"


def test_homepage_structured_data_matches_the_page():
    page = TestClient(server.app).get("/").text
    assert "<title>ChannelRecipe: AI Faceless YouTube Video Generator, Any Style</title>" in page
    desc = re.search(r'<meta name="description" content="([^"]+)">', page).group(1)
    assert len(desc) <= 160
    h1 = re.sub(r"<[^>]+>", "", re.search(r'<h1 id="hero-title">(.*?)</h1>', page, re.S).group(1))
    assert h1 == "Whole faceless YouTube videos, made by AI, in any style you want"
    blocks = _ld(page)
    graph = {n["@type"]: n for b in blocks for n in b.get("@graph", [])}
    assert set(graph) == {"Organization", "SoftwareApplication"}
    assert not any("[" in u for u in graph["Organization"]["sameAs"])  # no placeholders
    assert TestClient(server.app).get(graph["Organization"]["logo"].replace("https://channelrecipe.com", "")).status_code == 200
    offers = {o["name"]: int(o["price"]) for o in graph["SoftwareApplication"]["offers"]}
    tiers = {t.label: t.monthly_usd for t in server.plans.TIERS.values() if t.public}
    assert offers == tiers, "the structured prices follow the plans"
    # FAQPage: the visible FAQ, word for word, in the same order
    faq = next(b for b in blocks if b.get("@type") == "FAQPage")["mainEntity"]
    section = page[page.index('<section class="faq-section">'):]
    section = section[:section.index("</section>")]
    text = lambda s: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()
    shown = [(text(q), text(a)) for q, a in
             re.findall(r'<div class="faq-q"[^>]*>(.*?)</div>\s*<div class="faq-a">(.*?)</div>', section, re.S)]
    assert [(q["name"], q["acceptedAnswer"]["text"]) for q in faq] == shown and len(shown) >= 8
    for slug in SLUGS:
        assert f'href="/{slug}"' in page, slug  # linked from the footer
