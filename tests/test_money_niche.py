"""Find My Money Niche: every answer matters, the proof is real or honestly absent, a taste before the gate, the full
result only after sign-in, results cached, the one paid call capped, and the pick carried into onboarding."""
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from webapp import database as db
from webapp import money_niche as mn
from webapp import money_niche_web as web

BASE = {"face": "none", "hours": "3to8", "loves": ["history", "stories"], "budget": "low"}


def iso(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400))


def channel(cid, name, keyword, title, *, subs=8000, views=240_000, days=200, videos=14, per_month=4.0, fresh=2):
    return {"channel_id": cid, "channel_name": name, "channel_url": f"https://www.youtube.com/@{cid}",
            "avatar_url": "https://yt3.ggpht.com/a", "source_keyword": keyword, "subscriber_count": subs,
            "video_count": videos, "days_since_start": days, "recent_avg_views": views, "uploads_per_month": per_month,
            "videos_last_14d": fresh,
            "recent_videos": [{"title": title, "url": f"https://www.youtube.com/watch?v={cid}", "view_count": views,
                               "thumbnail": f"https://i.ytimg.com/vi/{cid}/mqdefault.jpg", "published_at": iso(20)}],
            "popular_videos": [{"title": title, "url": f"https://www.youtube.com/watch?v={cid}", "view_count": 600_000,
                                "published_at": iso(120)}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "quiz.db")
    db._init_db()
    mn._proof_cache.clear()
    web._hits.clear()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db.upsert_niche_channels([
        channel("stick1", "Stick Lab", "stick figure", "What if you lived in Rome | stick figure animation"),
        channel("stick2", "Tiny Stickman", "history", "The worst jobs in history (stickman)", subs=3000, views=90_000),
        channel("big1", "Huge Stick Co", "stick figure", "stick figure", subs=2_000_000, views=900_000),   # not small
        channel("face1", "On Camera Stick", "stick figure", "stick figure vlog"),
    ])
    with db._conn() as conn:        # the hunt's screening marks on-camera channels (webapp/niche_store.py)
        conn.execute("UPDATE niche_channels SET production_format='presenter', quality_status='screened' "
                     "WHERE channel_id='face1'")
    from webapp import server
    events = []
    monkeypatch.setattr(server, "track", lambda uid, event, props=None: events.append((uid, event, props or {})))
    return SimpleNamespace(client=TestClient(server.app), events=events, server=server)


def sign_in(env, email="maker@example.com"):
    user = db.create_user(email)
    env.client.cookies.set("session", db.create_session(user["id"]))
    return user


# ---- scoring ----------------------------------------------------------------------------------------------

def no_proof(_niche):
    return []


def outcome(answers):
    result = mn.compute(answers, proofs=no_proof)
    return [(n["id"], n["recipe"]) for n in result["niches"]]


@pytest.mark.parametrize("key,value", [("face", "camera"), ("face", "voice"), ("hours", "under3"), ("hours", "over15"),
                                       ("loves", ["money"]), ("loves", ["crime", "mysteries"]),
                                       ("budget", "free"), ("budget", "high")])
def test_every_question_changes_the_result(key, value):
    assert outcome({**BASE, key: value}) != outcome(BASE), f"changing {key} alone must change the result"


def test_what_each_answer_decides():
    shorts = mn.compute({**BASE, "hours": "under3"}, proofs=no_proof)["niches"][0]["recipe"]
    assert shorts["kind"] == "shorts" and "Shorts" in shorts["rhythm"]
    long = mn.compute({**BASE, "hours": "over15"}, proofs=no_proof)["niches"][0]["recipe"]
    assert long["kind"] == "long" and "week" in long["rhythm"]
    cheap = mn.compute({**BASE, "loves": ["disasters"], "budget": "free"}, proofs=no_proof)["niches"]
    rich = mn.compute({**BASE, "loves": ["disasters"], "budget": "mid"}, proofs=no_proof)["niches"]
    three_d = next(n for n in rich if n["id"] == "3d_reconstruction")
    assert three_d["recipe"]["format"] == "3D scenes", "a $30-100 budget affords 3D"
    assert all(n["recipe"]["format"] != "3D scenes" for n in cheap), "$0 never gets a 3D recipe"
    on_camera = mn.compute({**BASE, "face": "camera", "loves": ["tips"]}, proofs=no_proof)["niches"][0]
    assert on_camera["id"] in {"ai_presenter", "over_60_tips"} and on_camera["recipe"]["format"].startswith("You on camera")
    assert mn.compute(BASE, proofs=no_proof)["niches"][0]["recipe"]["voice"] == "An AI narrator voice"


def test_bad_answers_are_refused_and_country_is_never_asked(env):
    assert env.client.post("/api/money-niche/quiz", json={**BASE, "loves": []}).status_code == 400
    assert env.client.post("/api/money-niche/quiz", json={**BASE, "budget": "lots"}).status_code == 400
    assert env.client.post("/api/money-niche/quiz", json={**BASE, "loves": ["a", "b", "c", "d"]}).status_code == 400
    assert "country" not in json.dumps(mn.clean_answers(BASE))


# ---- proof: real channels or an honest fallback -------------------------------------------------------------

def test_proof_is_small_recent_faceless_channels_from_the_niche_finder(env):
    proofs = mn.proof_channels(mn.BY_ID["stick_figures"])
    names = [p["name"] for p in proofs]
    assert "Stick Lab" in names and "Huge Stick Co" not in names and "On Camera Stick" not in names
    lab = next(p for p in proofs if p["name"] == "Stick Lab")
    assert lab["months"] == 7 and lab["recent_avg_views"] == 240_000 and lab["url"].startswith("https://www.youtube.com/")
    low, high = lab["earnings"]
    assert 0 < low < high, "earnings are a range, never one figure"
    assert lab["breakout_after"] == 12, "about 3.4 months of uploads at 4 a month, then the breakout"


def test_a_niche_without_qualifying_channels_shows_established_ones_and_invents_nothing(env):
    result = mn.compute({**BASE, "loves": ["world"]})
    geo = next(n for n in result["niches"] if n["id"] == "geopolitics_maps")
    assert geo["proofs"] == [] and geo["earnings"] is None
    assert [r["name"] for r in geo["references"]] == ["Wendover Productions", "Caspian Report", "Kings and Generals"]
    real = {"Stick Lab", "Tiny Stickman", "Huge Stick Co", "On Camera Stick"}
    for niche in result["niches"]:
        assert {p["name"] for p in niche["proofs"]} <= real


# ---- a taste, then the gate ---------------------------------------------------------------------------------

def test_a_visitor_gets_niche_one_and_one_proof_channel_and_nothing_more(env):
    res = env.client.post("/api/money-niche/quiz", json=BASE)
    assert res.status_code == 200 and res.headers["cache-control"] == "private, no-store"
    taste = res.json()
    assert taste["gated"] and taste["niche"]["name"] == "Stick figures on detailed backgrounds"
    assert taste["proof"]["name"] == "Stick Lab" and taste["proof"]["months"] == 7
    for hidden in ("niches", "titles", "earnings", "recipe"):
        assert hidden not in taste and hidden not in json.dumps(taste["proof"])
    again = env.client.get(f"/api/money-niche/result/{taste['id']}").json()
    assert again["gated"] and "niches" not in again
    assert env.events[-1][1] == "money_niche_quiz_completed"


def test_signed_in_gets_three_niches_proof_earnings_breakouts_titles_and_the_recipe(env):
    rid = env.client.post("/api/money-niche/quiz", json=BASE).json()["id"]
    sign_in(env)
    full = env.client.get(f"/api/money-niche/result/{rid}").json()
    assert not full["gated"] and len(full["niches"]) == 3
    top = full["niches"][0]
    assert top["proofs"] and top["earnings"][0] < top["earnings"][1] and top["breakout_after"]
    assert len(full["titles"]) == 5 and full["titles_source"] == "catalogue", "no model key: the niche's own titles"
    assert {"format", "length", "look", "pacing", "voice", "rhythm"} <= set(top["recipe"])
    assert "Partner Program" in full["ypp_note"] and "1,000 subscribers" in full["ypp_note"]
    assert "not promises" in full["estimate_note"]


def test_the_same_answers_are_one_cached_result(env):
    first = env.client.post("/api/money-niche/quiz", json=BASE).json()["id"]
    again = env.client.post("/api/money-niche/quiz", json={**BASE, "loves": ["stories", "history"]}).json()["id"]
    assert first == again
    assert env.events[-1][2]["cached"] is True
    later = mn.take_quiz(BASE, now=time.time() + mn.BUCKET_S + 5)
    assert later["id"] != first, "a new Niche Finder hunt, a fresh result"


# ---- the one paid call: shared, capped per account and per day ---------------------------------------------

def model_answer(calls, titles=None):
    titles = titles or [f"A fresh title number {i}" for i in range(5)]

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps({"titles": titles})}]}}],
            "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 120}})
    return post


def test_titles_are_made_once_a_day_for_everyone_and_capped(env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("MONEY_NICHE_USER_DAILY", "1")
    calls = []
    niche = mn.BY_ID["stick_figures"]
    alice, bob, cara = (db.create_user(e) for e in ("a@example.com", "b@example.com", "c@example.com"))
    got = mn.titles_for(niche, "long", alice, post=model_answer(calls))
    assert got["source"] == "model" and len(got["titles"]) == 5 and len(calls) == 1
    assert mn.titles_for(niche, "long", bob, post=model_answer(calls)) == got and len(calls) == 1, "shared for the day"
    other = mn.BY_ID["anime_stories"]
    assert mn.titles_for(other, "long", alice, post=model_answer(calls))["source"] == "catalogue", "alice's cap is 1"
    assert len(calls) == 1
    monkeypatch.setenv("MONEY_NICHE_DAILY_USD", "0.000001")
    assert mn.titles_for(other, "long", cara, post=model_answer(calls))["source"] == "catalogue", "global cap"
    assert len(calls) == 1


def test_a_failed_or_bad_model_answer_falls_back_to_written_titles(env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    user = db.create_user("d@example.com")
    bad = model_answer([], titles=["too few"])
    got = mn.titles_for(mn.BY_ID["true_crime"], "long", user, post=bad)
    assert got == {"titles": mn.BY_ID["true_crime"]["titles"], "source": "catalogue"}

    def down(*a, **k):
        return SimpleNamespace(status_code=503, json=lambda: {})
    assert mn.titles_for(mn.BY_ID["true_crime"], "shorts", user, post=down)["source"] == "catalogue"


# ---- the pick, carried into onboarding ----------------------------------------------------------------------

def test_choosing_a_niche_stores_it_on_the_account_for_studio(env):
    rid = env.client.post("/api/money-niche/quiz", json=BASE).json()["id"]
    assert env.client.post("/api/money-niche/choose", json={"result": rid, "niche": "stick_figures"}).status_code == 401
    sign_in(env)
    assert env.client.post("/api/money-niche/choose", json={"result": rid, "niche": "finance_charts"}).status_code == 404
    res = env.client.post("/api/money-niche/choose", json={"result": rid, "niche": "stick_figures"})
    assert res.status_code == 200 and res.json()["next"] == "/app#local-studio"
    me = env.client.get("/api/auth/me").json()["user"]
    assert me["money_niche"]["id"] == "stick_figures" and me["money_niche"]["studio"] == "stick-stories"
    assert me["money_niche"]["proof"] == "Stick Lab"


# ---- the page and the share card ----------------------------------------------------------------------------

def test_the_quiz_page_and_a_shared_result_with_its_card(env):
    page = env.client.get("/money-niche")
    assert page.status_code == 200 and "/static/money-niche.js?v=" in page.text
    assert "https://channelrecipe.com/money-niche/card.png" in page.text
    rid = env.client.post("/api/money-niche/quiz", json=BASE).json()["id"]
    shared = env.client.get(f"/money-niche/r/{rid}").text
    assert f"https://channelrecipe.com/money-niche/card/{rid}.png" in shared
    assert "My money niche: Stick figures on detailed backgrounds" in shared
    card = env.client.get(f"/money-niche/card/{rid}.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png" and card.content[:4] == b"\x89PNG"
    assert env.client.get("/money-niche/card.png").content[:4] == b"\x89PNG"
    assert env.client.get("/money-niche/card/nope-nope.png").status_code == 404
    assert env.client.get("/money-niche/r/nope-nope", follow_redirects=False).status_code == 302
    assert "/money-niche" in env.client.get("/sitemap.xml").text
    assert "Disallow: /api/" in env.client.get("/robots.txt").text, "the card lives outside /api/ so crawlers can fetch it"
