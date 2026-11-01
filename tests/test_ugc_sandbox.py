"""channelrecipe.com/ugc/<key>: the UGC creator sandbox (a creator kit and Studio's style-clone page driven with videos
made ahead), open only with a key from UGC_SANDBOX_KEY and never indexed."""
from fastapi.testclient import TestClient

import webapp.server as server


def test_the_sandbox_is_closed_without_its_key(monkeypatch):
    client = TestClient(server.app)
    monkeypatch.delenv("UGC_SANDBOX_KEY", raising=False)
    assert client.get("/ugc/anything/").status_code == 404                  # unset: closed to everyone
    monkeypatch.setenv("UGC_SANDBOX_KEY", "k3y-one, k3y-two")
    assert client.get("/ugc/wrong/").status_code == 404
    assert client.get("/ugc/wrong/studio").status_code == 404


def test_a_creator_with_the_key_gets_the_kit_and_the_studio_walkthrough(monkeypatch):
    client = TestClient(server.app)
    monkeypatch.setenv("UGC_SANDBOX_KEY", "k3y-one, k3y-two")
    r = client.get("/ugc/k3y-two", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/ugc/k3y-two/"
    kit = client.get("/ugc/k3y-two/")
    assert kit.status_code == 200 and "Creator kit" in kit.text
    assert kit.headers["x-robots-tag"].startswith("noindex") and "no-store" in kit.headers["cache-control"]
    studio = client.get("/ugc/k3y-one/studio")
    assert studio.status_code == 200 and "Which channel inspires you?" in studio.text      # Studio's own page
    assert "/ugc-sandbox/studio.js" in studio.text and "<script>" not in studio.text.replace('<script src', '')
    for f in ("kit.js", "kit.css", "studio.js"):
        assert client.get("/ugc-sandbox/" + f).status_code == 200, f
    for f in ("pathways.json", "trending.json"):         # the hooks and scripts live on the CDN, not in this repo
        assert client.get("/ugc-sandbox/" + f).status_code == 404, f
    assert "public/ugc-sandbox/" in client.get("/ugc-sandbox/kit.js").text
