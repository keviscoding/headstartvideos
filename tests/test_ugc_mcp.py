"""channelrecipe.com/ugc-mcp/<key>/mcp: the UGC creators' sandboxed ChannelRecipe connector for Claude (webapp/ugc_mcp.py).
It opens only with a UGC_SANDBOX_KEY key, speaks MCP (stateless Streamable HTTP, JSON answers), draws its cards with
one MCP Apps page, answers from the sandbox's cached data, always lands a real ready-made video for the topic asked
(a different one each time), and says plainly in its text that it's a demo that renders nothing."""
import uuid

import pytest
from fastapi.testclient import TestClient

import webapp.server as server
from webapp import ugc_mcp

V = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com/public/ugc-sandbox/v1/out/"


def _out(stem, title, tall=False):
    return {"video": V + stem + ".mp4", "poster": V + stem + "_f1.jpg", "title": title, "seconds": 75, "tall": tall,
            "frames": [V + f"{stem}_f{k}.jpg" for k in range(6)], "script": ["A first line.", "A second line."]}


GLOOBY = {"id": "0qCw4co8QCg", "url": "https://www.youtube.com/watch?v=0qCw4co8QCg", "views": 1344148, "channel": "Glooby",
          "channel_url": "https://www.youtube.com/channel/UCglooby", "title": "The GTA 6 Map Is Impossible", "seconds": 974}
TOPICS = [
    {"id": "history", "name": "History stories", "pathway": "history", "look": ["Stick figures"],
     "outputs": [_out("history_1", "Why it sucked to be a Roman gladiator"),
                 _out("history_2", "Why medieval peasants feared winter"),
                 _out("history_3", "A day as a Victorian chimney sweep")],
     "refs": [{**GLOOBY, "id": "h1", "channel": "Historia Civilis", "channel_url": "https://www.youtube.com/channel/UChist"}]},
    {"id": "gta6", "name": "GTA 6 channels", "pathway": "gta6", "pin": "Glooby", "look": ["Cinematic city shots"],
     "outputs": [_out("gta6_1", "The GTA 6 map is impossible"), _out("gta6_2", "Everything hidden in Leonida")],
     "refs": [GLOOBY, {**GLOOBY, "id": "n1", "channel": "Nino", "channel_url": "https://www.youtube.com/channel/UCnino",
                       "views": 1988564, "title": "The World Isn't Ready For GTA 6"}]},
    {"id": "roblox", "name": "Roblox stories", "pathway": "roblox", "look": [], "outputs": [], "refs": []},
]
DATA = {"version": 1, "updated": "2026-11-03",
        "methods": [{"id": "clone", "flow": "clone", "topics": TOPICS},
                    {"id": "ranking", "flow": "ranking", "topics": [
                        {"id": "rank-cats", "name": "Funniest cat moments", "search": "funny cat moments",
                         "outputs": [_out("rank-cats_1", "Funniest cat moments", tall=True)],
                         "clips": [{"id": f"c{i}", "title": f"Cat clip {i}", "views": 1000000 - i} for i in range(12)]},
                        {"id": "rank-sharks", "name": "Best shark moments", "search": "shark moments",
                         "outputs": [_out("rank-sharks_1", "Best shark moments", tall=True)], "clips": []}]},
                    {"id": "clipping", "flow": "clipping", "soon": True, "topics": []}],
        "channels": {"https://www.youtube.com/channel/UCglooby": [
            {"id": "g1", "title": "GTA 6 Map Secrets", "views": 900000}, {"id": "g2", "title": "Lucia Explained", "views": 300000}]}}


@pytest.fixture
def mcp(monkeypatch):
    monkeypatch.setenv("UGC_SANDBOX_KEY", "k3y-one, k3y-two")
    fetches = []
    monkeypatch.setattr(ugc_mcp, "_fetch", lambda: fetches.append(1) or DATA)
    monkeypatch.setattr(ugc_mcp, "_data", {"at": 0.0, "value": None})
    client = TestClient(server.app)
    key = "k3y-two"

    def rpc(method, params=None, path=None):
        r = client.post(path or f"/ugc-mcp/{key}/mcp", json={"jsonrpc": "2.0", "id": 7, "method": method,
                                                            "params": params or {}})
        assert r.status_code == 200, r.text
        assert r.headers["x-robots-tag"].startswith("noindex")
        return r.json()

    def tool(name, **args):
        out = rpc("tools/call", {"name": name, "arguments": args})["result"]
        return out["content"][0]["text"], out["structuredContent"]

    rpc.client, rpc.tool, rpc.fetches = client, tool, fetches
    return rpc


def test_the_connector_is_closed_without_a_creator_key(mcp, monkeypatch):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    assert mcp.client.post("/ugc-mcp/wrong/mcp", json=body).status_code == 404
    monkeypatch.delenv("UGC_SANDBOX_KEY")
    assert mcp.client.post("/ugc-mcp/k3y-two/mcp", json=body).status_code == 404     # unset: closed to everyone


def test_it_speaks_mcp_over_stateless_http(mcp):
    init = mcp("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude"}})
    assert init["id"] == 7 and init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"]["title"] == "ChannelRecipe" and "clone_channel" in init["result"]["instructions"]
    assert mcp("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == ugc_mcp.PROTOCOLS[0]
    note = mcp.client.post("/ugc-mcp/k3y-two/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202 and not note.content
    assert mcp.client.get("/ugc-mcp/k3y-two/mcp").status_code == 405                # no server stream, no sessions
    assert mcp("ping")["result"] == {}
    assert mcp("nope/nothing")["error"]["code"] == -32601
    assert mcp("tools/call", {"name": "start_real_render"})["error"]["code"] == -32602
    bad = mcp.client.post("/ugc-mcp/k3y-two/mcp", content=b"{not json")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700


def test_every_tool_draws_its_card_with_the_one_mcp_apps_page(mcp):
    tools = mcp("tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == ["find_viral_channels", "clone_channel", "make_video", "rank_clips"]
    for t in tools:
        assert t["_meta"]["ui"]["resourceUri"] == ugc_mcp.UI_URI and t["annotations"]["readOnlyHint"]
    [res] = mcp("resources/list")["result"]["resources"]
    assert res["uri"] == ugc_mcp.UI_URI and res["mimeType"] == "text/html;profile=mcp-app"
    [page] = mcp("resources/read", {"uri": ugc_mcp.UI_URI})["result"]["contents"]
    assert page["mimeType"] == "text/html;profile=mcp-app" and page["text"].startswith("<!doctype html>")
    assert '"ui/initialize"' in page["text"] and '"2026-01-26"' in page["text"]           # the MCP Apps handshake
    assert "ui/notifications/tool-result" in page["text"] and "ui/notifications/size-changed" in page["text"]
    csp = page["_meta"]["ui"]["csp"]
    assert ugc_mcp.CDN in csp["resourceDomains"] and "https://i.ytimg.com" in csp["resourceDomains"]
    assert csp["connectDomains"] == []                                          # the page itself fetches nothing
    assert mcp("resources/read", {"uri": "ui://other"})["error"]["code"] == -32002


def test_clipping_appears_only_once_the_sandbox_has_real_clipping_videos(mcp, monkeypatch):
    names = lambda: [t["name"] for t in mcp("tools/list")["result"]["tools"]]
    assert "clip_video" not in names()
    clipping = {"id": "clipping", "flow": "clipping", "topics": [
        {"id": "clip-pods", "name": "Podcast clips", "outputs": [_out("clip-pods_1", "Clip one", tall=True)]}]}
    monkeypatch.setattr(ugc_mcp, "_data", {"at": 0.0, "value": None})
    monkeypatch.setattr(ugc_mcp, "_fetch", lambda: {**DATA, "methods": DATA["methods"][:2] + [clipping]})
    assert "clip_video" in names()
    text, ui = mcp.tool("clip_video", url="https://www.youtube.com/watch?v=abc")
    assert ui["view"] == "clips" and ui["clips"][0]["video"].endswith("clip-pods_1.mp4") and "Demo workspace" in text


def test_the_gta_6_prompt_lands_a_real_gta_6_video_every_time_never_the_same_twice_in_a_row(mcp):
    text, ui = mcp.tool("clone_channel", channel="@gloobygta")
    assert ui["view"] == "clone" and ui["channel"] == "Glooby" and ui["exact"] and ui["topic"] == "GTA 6 channels"
    assert ui["stats"]["videos"] == 3 and ui["stats"]["views"] == 900000 + 300000 + 1344148   # uploads + its viral one
    assert sorted(ui["ideas"]) == ["Everything hidden in Leonida", "The GTA 6 map is impossible"]
    assert text.startswith("Style learned: Glooby")
    seen = []
    for _ in range(12):
        text, ui = mcp.tool("make_video", idea="make me a GTA 6 video like Glooby", channel="Glooby")
        assert ui["view"] == "make" and ui["channel"] == "Glooby" and ui["video"]["video"].startswith(V + "gta6_")
        assert ui["video"]["video"] in text and "Demo workspace" in text and "no new render" in text
        seen.append(ui["video"]["video"])
    assert all(a != b for a, b in zip(seen, seen[1:])) and len(set(seen)) == 2
    assert len(mcp.fetches) == 1                                                # the CDN data is cached


def test_an_idea_that_names_a_ready_made_video_gets_that_video(mcp):
    for _ in range(3):
        _, ui = mcp.tool("make_video", idea="Why medieval peasants feared winter")
        assert ui["video"]["title"] == "Why medieval peasants feared winter"


def test_it_never_shows_another_niches_video_for_what_was_asked(mcp):
    text, ui = mcp.tool("make_video", idea="a Roblox story about lava")       # its videos aren't made yet
    assert ui["view"] == "note" and "still being made" in text and "History stories" in text
    text, ui = mcp.tool("make_video", idea="a cooking video")
    assert ui["view"] == "note" and "isn't in ChannelRecipe's demo workspace" in text
    text, ui = mcp.tool("rank_clips", topic="best football goals")               # "best" isn't a shark
    assert ui["view"] == "note" and "Funniest cat moments" in text
    text, ui = mcp.tool("clone_channel", channel="MrBeast")
    assert ui["view"] == "note"


def test_ranking_and_finding_viral_channels(mcp):
    text, ui = mcp.tool("rank_clips", topic="funniest cat moments")
    assert ui["view"] == "rank" and ui["video"]["tall"] and 9 <= len(ui["clips"]) <= 12 and "Demo workspace" in text
    text, ui = mcp.tool("find_viral_channels", niche="GTA 6")
    assert ui["view"] == "viral" and ui["pin"] == "Glooby" and ui["refs"][0]["channel"] == "Glooby"
    assert "Glooby" in text and "1.3M views" in text


def test_a_cdn_hiccup_keeps_serving_the_last_copy(mcp, monkeypatch):
    mcp.tool("find_viral_channels", niche="GTA 6")

    def down():
        raise OSError("CDN down")
    monkeypatch.setattr(ugc_mcp, "_fetch", down)
    ugc_mcp._data["at"] = 0.0                                                   # its ten minutes are up
    _, ui = mcp.tool("find_viral_channels", niche="GTA 6")
    assert ui["view"] == "viral"
    monkeypatch.setattr(ugc_mcp, "_data", {"at": 0.0, "value": None})          # nothing cached yet: says so
    out = mcp("tools/call", {"name": "make_video", "arguments": {"idea": "x"}})["result"]
    assert out["isError"] and "didn't answer" in out["content"][0]["text"]


def test_picks_survive_without_the_database(monkeypatch):
    def no_db(bucket, new=None):
        raise RuntimeError("database down")
    monkeypatch.setattr(ugc_mcp, "_db_last", no_db)
    bucket = f"test:{uuid.uuid4()}"
    picks = [ugc_mcp.pick(bucket, 3) for _ in range(20)]
    assert all(a != b for a, b in zip(picks, picks[1:])) and ugc_mcp.pick(bucket + "x", 1) == 0


def test_every_card_and_every_result_names_channelrecipe_com(mcp):
    for name, args in [("find_viral_channels", {"niche": "GTA 6"}), ("clone_channel", {"channel": "Glooby"}),
                       ("make_video", {"idea": "a GTA 6 video", "channel": "Glooby"}), ("rank_clips", {"topic": "cats"}),
                       ("make_video", {"idea": "a cooking video"})]:
        text, _ = mcp.tool(name, **args)
        assert text.endswith("Make yours at channelrecipe.com."), name
    page = mcp("resources/read", {"uri": ugc_mcp.UI_URI})["result"]["contents"][0]["text"]
    assert 'class="brand"' in page and '<span class="wm">channelrecipe.com</span>' in page   # footer + on the player
    assert "channelrecipe.com" in mcp("initialize")["result"]["instructions"]


def test_no_two_runs_look_alike_titles_start_points_frames_and_clips(mcp, monkeypatch):
    titles = [f"GTA 6 map secret #{i}" for i in range(10)]
    gta = {**TOPICS[1], "outputs": [{**_out("gta6_1", "The GTA 6 map is impossible"), "seconds": 181, "titles": titles}]}
    monkeypatch.setattr(ugc_mcp, "_data", {"at": 0.0, "value": None})
    monkeypatch.setattr(ugc_mcp, "_fetch", lambda: {**DATA, "methods": [{**DATA["methods"][0], "topics": [TOPICS[0], gta]},
                                                                         *DATA["methods"][1:]]})
    runs = [mcp.tool("make_video", idea="a GTA 6 video like Glooby", channel="Glooby") for _ in range(15)]
    shown = [ui["video"]["title"] for _, ui in runs]
    assert set(shown) <= set(titles) and len(set(shown)) > 3 and all(a != b for a, b in zip(shown, shown[1:]))
    assert all(f'"{ui["video"]["title"]}"' in text for text, ui in runs)          # the text names the title shown
    starts = [ui["video"]["start"] for _, ui in runs]
    assert all(0 <= s <= 181 - 25 for s in starts) and len(set(starts)) > 3          # always 25 s left to play
    for _, ui in runs:
        f = ui["video"]["frames"]
        assert 4 <= len(f) <= 6 and f == sorted(f)                                   # a handful, in story order
    _, ui = mcp.tool("make_video", idea="Why medieval peasants feared winter")       # a short video starts at 0
    assert ui["video"]["start"] == 0 or ui["video"]["seconds"] >= 30
    _, ui = mcp.tool("clone_channel", channel="Glooby")
    assert ui["stats"]["videos"] == 3 and len(ui["uploads"]) == 3                   # few uploads: all of them
    _, ui = mcp.tool("rank_clips", topic="cats")
    assert 9 <= len(ui["clips"]) <= 12 and [c["views"] for c in ui["top"]] == sorted(
        (c["views"] for c in ui["clips"]), reverse=True)[:5]
