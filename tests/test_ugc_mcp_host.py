"""The UGC creators' demo connector on a host of its own (web_assets.UgcHostGate, UGC_MCP_HOST). On channelrecipe.com
claude.ai found the paid connector's OAuth metadata (/.well-known/oauth-authorization-server) and sent creators to the
paid sign-in for the sandbox URL too (2026-11-11). On the gated host only /ugc-mcp/* and /ugc/connect
answer, so initialize works without a sign-in and there is no OAuth to find; channelrecipe.com is exactly as before."""
from fastapi.testclient import TestClient

import webapp.server as server

DO = "cr-app-x1y2z.ondigitalocean.app"            # (the app's default domain stands in)
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude"}}}
CLOSED = [("GET", "/.well-known/oauth-authorization-server"), ("GET", "/.well-known/oauth-protected-resource"),
          ("GET", "/.well-known/oauth-protected-resource/ugc-mcp/k3y-two/mcp"), ("GET", "/authorize"), ("POST", "/token"),
          ("POST", "/register"), ("POST", "/mcp"), ("GET", "/"), ("GET", "/ugc/k3y-two/"), ("GET", "/static/favicon.svg"),
          ("GET", "/oauth/mcp/login"), ("GET", "/api/health")]


def _client(host: str) -> TestClient:
    return TestClient(server.app, base_url=f"https://{host}")


def test_on_the_ugc_host_only_the_demo_connector_answers_without_a_sign_in(monkeypatch):
    monkeypatch.setenv("UGC_SANDBOX_KEY", "k3y-one, k3y-two")
    monkeypatch.setenv("UGC_MCP_HOST", f"{DO}, ugc.channelrecipe.com")
    for host in (DO, "ugc.channelrecipe.com", f"{DO.upper()}:443"):
        c = _client(host)
        r = c.post("/ugc-mcp/k3y-two/mcp", json=INIT)                       # no Authorization header
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["title"] == "ChannelRecipe", host
        assert c.get("/ugc-mcp/k3y-two/mcp").status_code == 405
        assert c.post("/ugc-mcp/wrong/mcp", json=INIT).status_code == 404   # (still a creator's key only)
        for method, path in CLOSED:
            got = c.request(method, path)
            assert got.status_code == 404 and got.text == "Not Found", (host, method, path, got.status_code)


def test_the_proxys_forwarded_host_is_gated_too(monkeypatch):
    monkeypatch.setenv("UGC_SANDBOX_KEY", "k3y-one, k3y-two")
    monkeypatch.setenv("UGC_MCP_HOST", DO)
    c = _client("10.0.0.7:8080")
    hdr = {"X-Forwarded-Host": DO}
    assert c.get("/.well-known/oauth-authorization-server", headers=hdr).status_code == 404
    assert c.post("/ugc-mcp/k3y-two/mcp", json=INIT, headers=hdr).status_code == 200


def test_channelrecipe_com_is_exactly_as_before(monkeypatch):
    monkeypatch.setenv("UGC_SANDBOX_KEY", "k3y-one, k3y-two")
    paths = ["/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource", "/robots.txt"]

    def seen(host):
        c = _client(host)
        return ([(r.status_code, r.content) for r in (c.get(p) for p in paths)],
                c.post("/ugc-mcp/k3y-two/mcp", json=INIT).json(), c.get("/").status_code)

    monkeypatch.delenv("UGC_MCP_HOST", raising=False)
    before = {h: seen(h) for h in ("channelrecipe.com", "www.channelrecipe.com", DO)}
    monkeypatch.setenv("UGC_MCP_HOST", DO)
    for host in ("channelrecipe.com", "www.channelrecipe.com"):
        assert seen(host) == before[host], host
    assert before["channelrecipe.com"][0][0][0] == 200                       # (the paid connector's OAuth is still there)
    assert before[DO][0][0][0] == 200                                          # unset: the default domain unchanged too
