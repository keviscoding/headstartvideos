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
    assert studio.status_code == 200 and "ChannelRecipe Studio" in studio.text      # the sandbox Studio
    assert "/ugc-sandbox/studio2.js" in studio.text and "<script>" not in studio.text.replace('<script src', '')
    for f in ("kit.js", "kit.css", "studio2.js", "studio2.css"):
        assert client.get("/ugc-sandbox/" + f).status_code == 200, f
    for f in ("pathways.json", "trending.json", "strategies.json"):   # the data lives on the CDN, not in this repo
        assert client.get("/ugc-sandbox/" + f).status_code == 404, f
    assert "public/ugc-sandbox/" in client.get("/ugc-sandbox/kit.js").text


def test_the_connect_page_shows_the_connector_link_to_copy_and_never_a_creator_key(monkeypatch):
    """channelrecipe.com/ugc/connect (the scriptwriter's walk, 2026-11-10): the link arrives on the creator's phone but goes
    into claude.ai on their laptop. A short page with the shared connector link, one Copy button, the four steps and a
    button to Claude's connectors. Its key is derived from the sandbox key (HMAC "ugc-connect-v1": no new setting to
    deploy) and opens only the demo connector, never a kit, so the public page never shows a kit's key."""
    import hashlib
    import hmac
    client = TestClient(server.app)
    monkeypatch.delenv("UGC_SANDBOX_KEY", raising=False)
    assert client.get("/ugc/connect").status_code == 404                   # no sandbox key: no page
    monkeypatch.setenv("UGC_SANDBOX_KEY", "k3y-one-0123456789, k3y-two")
    connect = hmac.new(b"k3y-one-0123456789", b"ugc-connect-v1", hashlib.sha256).hexdigest()[:18]
    assert server._ugc_connect_key() == connect and connect not in ("k3y-one-0123456789", "k3y-two")
    for path in ("/ugc/connect", "/ugc/connect/"):
        r = client.get(path)
        assert r.status_code == 200 and f"https://channelrecipe.com/ugc-mcp/{connect}/mcp" in r.text
        assert "k3y-" not in r.text and r.headers["x-robots-tag"].startswith("noindex")
    page = client.get("/ugc/connect").text
    for words in ("<b>Customize</b> (on the left)", "<b>Connectors</b>", "+ Add", "Add custom connector", "ChannelRecipe",
                  "<b>Add</b> at the bottom", "<b>Connect</b>", "Copy the link", "https://claude.ai/new#customize/connectors/yours"):
        assert words in page, words
    assert "Settings" not in page                                            # (claude.ai moved it: Customize › Connectors)
    assert client.get(f"/ugc/{connect}/").status_code == 404                 # the connect key opens no kit
    assert client.get(f"/ugc/{connect}/studio").status_code == 404
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    assert client.post(f"/ugc-mcp/{connect}/mcp", json=body).status_code == 200   # but it opens the connector
    assert client.post("/ugc-mcp/wrong/mcp", json=body).status_code == 404
    monkeypatch.setenv("UGC_MCP_BASE", "https://ugc.example.app/")          # the connector's own host (UGC_MCP_HOST)
    r = client.get("/ugc/connect")
    assert f"https://ugc.example.app/ugc-mcp/{connect}/mcp" in r.text and "https://channelrecipe.com/ugc-mcp/" not in r.text
