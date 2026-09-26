"""The toggle, the integration hook, the lab parser and the Alex v2 page API."""

from datetime import timedelta

import pytest

from alex_v2 import facts, integration, settings
from alex_v2.lab import parse_conversation
from alex_v2.tests.fake_llm import FakeLLM, out
from alex_v2.tests.helpers import lon, thread

LON = "Europe/London"


def test_default_is_off_and_hook_does_nothing():
    assert settings.mode() == "off"
    llm = FakeLLM()
    h = integration.maybe_handle("t1", [{"speaker": "her", "text": "hey"}], llm=llm)
    assert h.handled is False and h.decision is None and llm.n == 0


def test_env_override_locks_mode(monkeypatch):
    settings.save({"mode": "off"})
    monkeypatch.setenv("ALEX_V2_MODE", "shadow")
    s = settings.load()
    assert s["mode"] == "shadow" and "mode" in s["_locked_by_env"]


def test_settings_never_touch_runtime_flags(tmp_path):
    settings.save({"mode": "on"})
    assert settings.settings_path().name == "settings.json"
    assert not list(settings.data_dir().rglob("runtime_flags.json"))


def test_on_mode_hook_owns_thread():
    settings.save({"mode": "on", "honor_timing": False})
    raw, last = thread([("me", "Hey trouble"), ("her", "just got home from work")], lon(2026, 9, 22, 18))
    h = integration.maybe_handle("t1", raw, llm=FakeLLM(out([("answer_and_pivot", "Adulting I see")])), her_tz=LON,
                                 now=last + timedelta(minutes=2))
    assert h.handled and h.arm == "v2" and h.decision.should_send


def test_ab_v1_threads_are_observed_not_handled():
    settings.save({"mode": "ab", "ab_split": 0.0})
    raw, last = thread([("me", "Hey trouble"), ("her", "just got home from work")], lon(2026, 9, 22, 18))
    h = integration.maybe_handle("t1", raw, llm=FakeLLM(), her_tz=LON, now=last + timedelta(minutes=2))
    assert not h.handled and h.arm == "v1"
    from alex_v2 import trace
    assert trace.ab_summary()["v1"]["threads"] == 1


def test_shadow_computes_but_never_owns():
    settings.save({"mode": "shadow", "honor_timing": False})
    raw, last = thread([("me", "Hey trouble"), ("her", "just got home from work")], lon(2026, 9, 22, 18))
    h = integration.maybe_handle("t1", raw, llm=FakeLLM(out([("answer_and_pivot", "Adulting I see")])), her_tz=LON,
                                 now=last + timedelta(minutes=2))
    assert not h.handled and h.decision.suggested_text == "Adulting I see"


def test_lab_parser_times_and_gaps():
    now = lon(2026, 9, 24, 21, 0)
    rows = parse_conversation("her: hey\nme: Hey trouble\n[2 days later]\nher: hellooo\nshe: you there", now, LON)
    assert [r["speaker"] for r in rows] == ["her", "me", "her", "her"]
    from datetime import datetime
    t = [datetime.fromisoformat(r["sent_at"]) for r in rows]
    assert t[2] - t[1] >= timedelta(days=2)
    assert all(a <= b for a, b in zip(t, t[1:]))


# --- the page ----------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from alex_v2.dashboard import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_page_and_status(client):
    r = client.get("/alex-v2")
    assert r.status_code == 200 and "Who texts?" in r.text
    st = client.get("/alex-v2/api/status").json()
    assert st["settings"]["mode"] == "off" and "resolved_model" in st["settings"]


def test_page_toggle_roundtrip(client):
    assert client.post("/alex-v2/api/mode", json={"mode": "ab", "ab_split": 0.3}).json()["mode"] == "ab"
    assert settings.load()["ab_split"] == 0.3
    assert client.post("/alex-v2/api/mode", json={"mode": "off"}).json()["mode"] == "off"
    assert client.post("/alex-v2/api/mode", json={"mode": "banana"}).status_code == 400


def test_page_settings_types(client):
    s = client.post("/alex-v2/api/settings", json={"candidates": "3", "honor_timing": False, "temperature": "0.5", "nope": 1}).json()
    assert s["candidates"] == 3 and s["honor_timing"] is False and s["temperature"] == 0.5 and "nope" not in s


def test_page_facts(client):
    r = client.post("/alex-v2/api/facts", json={"facts": {"first_name": "Kev", "pet": {"has_pet": False, "type": "", "name": ""}}}).json()
    assert r["facts"]["first_name"] == "Kev" and "NO pet" in r["prompt_preview"]
    assert facts.load()["first_name"] == "Kev"


def test_page_lab_prompt_only(client):
    r = client.post("/alex-v2/api/lab", json={"conversation": "her: you look like trouble\nme: Guilty\nher: so what do you do",
                                               "call_model": False, "now": "2026-09-24T21:00"}).json()
    d = r["decision"]
    assert "MOVES YOU MAY USE" in d["debug"]["user"] and d["state"]["date_state"] == "NONE"


def test_token_protects_page(client, monkeypatch):
    monkeypatch.setenv("ALEX_V2_TOKEN", "s3cret")
    assert client.get("/alex-v2/api/status").status_code == 401
    assert client.get("/alex-v2/api/status?token=s3cret").status_code == 200
    assert client.get("/alex-v2/api/status", headers={"X-Alex-V2-Token": "s3cret"}).status_code == 200


def test_page_thread_controls_and_needs_you(client):
    settings.save({"mode": "on", "honor_timing": False})
    from alex_v2 import engine
    raw, last = thread([("me", "Adulting I see"), ("her", "my dad passed away last night")], lon(2026, 9, 22, 18))
    engine.decide(raw, thread_id="t9", contact_name="Maya", her_tz=LON, now=last + timedelta(minutes=2),
                  llm=FakeLLM(out([("sincere_support", "Oh shit, sorry to hear that. Are you ok?")], type="sincere_serious")))
    ts = client.get("/alex-v2/api/threads").json()
    t = next(x for x in ts if x["thread_id"] == "t9")
    assert t["needs_you"] and "sorry to hear" in t["needs_you"]["suggested"].lower() and t["contact"] == "Maya"
    client.post("/alex-v2/api/thread", json={"thread_id": "t9", "action": "clear_handoff"})
    client.post("/alex-v2/api/thread", json={"thread_id": "t9", "action": "pause"})
    t = next(x for x in client.get("/alex-v2/api/threads").json() if x["thread_id"] == "t9")
    assert t["paused"] and not t["needs_you"]
    decs = client.get("/alex-v2/api/decisions").json()
    assert decs and decs[0]["action"] == "handoff"
