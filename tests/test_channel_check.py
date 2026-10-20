"""Channel check: read a channel's public numbers once, compare it with the breakout channels in its niche, show the
biggest problem first, and the full plan and ideas only after sign-in. YouTube and the model are faked."""
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from webapp import channel_check as cc
from webapp import database as db
from webapp import money_niche as mn
from webapp import money_niche_web as web
from tests.test_money_niche import channel

NOW = time.time()


def iso(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - days_ago * 86400))


def my_channel(**over):
    videos = [{"id": f"v{i}", "title": t, "url": f"https://www.youtube.com/watch?v=v{i}",
               "thumbnail": f"https://i.ytimg.com/vi/v{i}/mqdefault.jpg", "views": views, "likes": 10, "comments": 1,
               "seconds": 600, "short": False, "published_at": iso(age)}
              for i, (t, views, age) in enumerate([
                  ("Stick figure history: medieval peasants", 2100, 45), ("Stickman explains Rome", 1500, 66),
                  ("The worst jobs (stick figure)", 31000, 87), ("Stick figure: Vikings", 1800, 108),
                  ("Stickman pirates", 1200, 129), ("Stick figure castles", 2600, 150),
                  ("Stick figure knights", 2900, 171), ("Stick figure monks", 2400, 192)])]
    return {"channel_id": "UCmine000000000000000000", "name": "My Stick Channel", "url": "https://www.youtube.com/channel/UCmine",
            "handle": "@mystick", "avatar": "https://yt3.ggpht.com/me", "subscribers": 2300, "total_views": 61000,
            "video_count": 8, "started": iso(400), "hidden_subscribers": False, "videos": videos, **over}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "check.db")
    db._init_db()
    mn._proof_cache.clear()
    web._hits.clear()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db.upsert_niche_channels([channel("stick1", "Stick Lab", "stick figure", "What if you lived in Rome | stick figure")])
    reads = []
    monkeypatch.setattr(cc, "fetch", lambda url, key: reads.append(url) or my_channel())
    from webapp import server
    monkeypatch.setattr(server.config, "YOUTUBE_API_KEY", "AIza-test")
    monkeypatch.setattr(server, "track", lambda *a, **k: None)
    return SimpleNamespace(client=TestClient(server.app), reads=reads)


def sign_in(env, email="me@example.com"):
    user = db.create_user(email)
    env.client.cookies.set("session", db.create_session(user["id"]))
    return user


def test_the_niche_and_the_problems_come_from_the_channels_own_numbers():
    found = cc.diagnose(my_channel(), [{"recent_avg_views": 240_000, "uploads_per_month": 4.0}], now=NOW)
    ids = [f["id"] for f in found["findings"]]
    assert ids[0] == "quiet", "45 days without an upload is the worst problem"
    assert "gap" in ids and next(f for f in found["findings"] if f["id"] == "gap")["level"] == "bad"
    hit = next(f for f in found["findings"] if f["id"] == "hit")
    assert hit["video"]["title"] == "The worst jobs (stick figure)" and "14×" in hit["title"]
    assert found["stats"]["median_views"] == 2250 and found["stats"]["peer_views"] == 240_000
    assert cc.detect_niche(my_channel())["id"] == "stick_figures"
    steps = cc.plan(found["findings"], mn.BY_ID["stick_figures"])
    assert any("The worst jobs" in s for s in steps) and len(steps) <= 6


def test_a_steady_growing_channel_is_told_so():
    fine = my_channel()
    for i, v in enumerate(fine["videos"]):
        v["published_at"], v["views"] = iso(3 + i * 7), 300_000 - i * 30_000
    found = cc.diagnose(fine, [{"recent_avg_views": 240_000, "uploads_per_month": 4.0}], now=NOW)
    levels = {f["id"]: f["level"] for f in found["findings"]}
    assert levels["rhythm"] == "good" and levels["gap"] == "good" and levels["trend"] == "good"


@pytest.mark.parametrize("raw,ok", [("@mystick", True), ("mystick", True), ("youtube.com/@mystick", True),
                                    ("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", True),
                                    ("https://evil.example.com/x", False), ("", False)])
def test_links_and_handles_are_understood(raw, ok):
    if ok:
        assert cc.normalise(raw).startswith(("https://", "UC"))
    else:
        with pytest.raises(cc.CheckError):
            cc.normalise(raw)


def test_a_visitor_sees_their_dashboard_and_the_biggest_problem_only(env):
    res = env.client.post("/api/channel-check", json={"url": "@mystick"})
    assert res.status_code == 200
    t = res.json()
    assert t["gated"] and t["channel"]["name"] == "My Stick Channel" and len(t["videos"]) == 8
    assert t["niche"]["id"] == "stick_figures" and t["headline"]["title"].startswith("You haven't posted")
    for hidden in ("findings", "plan", "ideas", "peers"):
        assert hidden not in t
    assert "channel_id" not in t["channel"]


def test_signed_in_gets_every_finding_the_peers_the_plan_and_ideas(env):
    cid = env.client.post("/api/channel-check", json={"url": "@mystick"}).json()["id"]
    sign_in(env)
    full = env.client.get(f"/api/channel-check/{cid}").json()
    assert not full["gated"] and len(full["findings"]) >= 3 and full["plan"]
    assert [p["name"] for p in full["peers"]] == ["Stick Lab"]
    assert len(full["ideas"]) == 5 and full["ideas_source"] == "catalogue" and all(i["thumbnail"] for i in full["ideas"])
    assert full["looks"] and full["angles"] and "Partner Program" in full["ypp_note"]


def test_a_channel_is_read_from_youtube_once_per_12_hours(env):
    first = env.client.post("/api/channel-check", json={"url": "@mystick"}).json()["id"]
    again = env.client.post("/api/channel-check", json={"url": "https://www.youtube.com/channel/UCmine000000000000000000"}).json()["id"]
    assert first == again and len(env.reads) == 1, "a known channel id is answered from the cache"
    assert env.client.post("/api/channel-check", json={"url": "@mystick"}).json()["id"] == first


def test_the_daily_cap_on_new_channels_and_a_missing_key_are_polite(env, monkeypatch):
    monkeypatch.setenv("CHANNEL_CHECK_DAILY", "0")
    res = env.client.post("/api/channel-check", json={"url": "@other"})
    assert res.status_code == 400 and "busy today" in res.json()["detail"]
    with pytest.raises(cc.CheckError):
        cc.check("@x", api_key="")


def test_ideas_are_one_shared_call_a_day_within_the_caps(env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    done = cc.check("@mystick", api_key="AIza", fetcher=lambda u, k: my_channel())
    calls = []
    ideas = [{"title": f"Idea number {i} about stick figures", "thumbnail": "A shocked peasant, text: TAX DAY"}
             for i in range(5)]

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps({"ideas": ideas})}]}}],
            "usageMetadata": {"promptTokenCount": 800, "candidatesTokenCount": 200}})
    a, b = db.create_user("a@example.com"), db.create_user("b@example.com")
    got = cc.ideas_for(done["id"], done["result"], a, post=post)
    assert got["source"] == "model" and got["ideas"] == ideas
    assert cc.ideas_for(done["id"], done["result"], b, post=post) == got and len(calls) == 1
    monkeypatch.setenv("MONEY_NICHE_DAILY_USD", "0")
    other = cc.check("@x", api_key="AIza", fetcher=lambda u, k: my_channel(channel_id="UCother00000000000000000"))
    assert cc.ideas_for(other["id"], other["result"], b, post=post)["source"] == "catalogue" and len(calls) == 1


def test_choosing_a_niche_from_the_check_sets_up_the_recipe(env):
    cid = env.client.post("/api/channel-check", json={"url": "@mystick"}).json()["id"]
    assert env.client.post(f"/api/channel-check/{cid}/choose", json={"niche": "stick_figures"}).status_code == 401
    sign_in(env)
    res = env.client.post(f"/api/channel-check/{cid}/choose",
                          json={"niche": "stick_figures", "angle": "a1", "look": "style:Stick Figure Stories"})
    assert res.status_code == 200
    me = env.client.get("/api/auth/me").json()["user"]["money_niche"]
    assert me["angle"] == "Worst jobs" and me["title"] == "The Worst Jobs in Ancient Rome"
    assert me["look"] == "Stick Figure Stories" and me["studio"] == "stick-stories"
    assert env.client.get(f"/money-niche/c/{cid}").status_code == 200


def test_reading_youtube_turns_the_api_answers_into_the_channel(monkeypatch):
    """fetch() with a fake YouTube client: three cheap calls, durations parsed, Shorts spotted."""
    calls = []

    class Call:
        def __init__(self, name, data): self.name, self.data = name, data
        def execute(self): calls.append(self.name); return self.data

    class YT:
        def channels(self):
            return SimpleNamespace(list=lambda **k: Call("channels", {"items": [{
                "snippet": {"title": "Mine", "customUrl": "@mine", "publishedAt": iso(100),
                            "thumbnails": {"medium": {"url": "https://yt3.ggpht.com/m"}}},
                "statistics": {"subscriberCount": "1200", "viewCount": "50000", "videoCount": "2"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUmine"}}}]}))
        def playlistItems(self):
            return SimpleNamespace(list=lambda **k: Call("playlist", {"items": [
                {"contentDetails": {"videoId": "a"}, "snippet": {"title": "Long one", "publishedAt": iso(2)}},
                {"contentDetails": {"videoId": "b"}, "snippet": {"title": "Short one", "publishedAt": iso(9)}}]}))
        def videos(self):
            return SimpleNamespace(list=lambda **k: Call("videos", {"items": [
                {"id": "a", "statistics": {"viewCount": "900"}, "contentDetails": {"duration": "PT12M5S"}},
                {"id": "b", "statistics": {"viewCount": "5000"}, "contentDetails": {"duration": "PT45S"}}]}))
    import core.channel_data
    import googleapiclient.discovery
    monkeypatch.setattr(core.channel_data, "_extract_channel_id", lambda url, key: "UCmine")
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: YT())
    ch = cc.fetch("@mine", "AIza")
    assert calls == ["channels", "playlist", "videos"]
    assert ch["subscribers"] == 1200 and ch["avatar"] == "https://yt3.ggpht.com/m"
    assert [(v["seconds"], v["short"], v["views"]) for v in ch["videos"]] == [(725, False, 900), (45, True, 5000)]
