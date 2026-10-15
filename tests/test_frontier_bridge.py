"""The downloadable bridge must preserve jobs and never fall back to CR billing."""
import json
from types import SimpleNamespace

import pytest

from local_runtime import cr_bridge as bridge


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "HOME", tmp_path)
    monkeypatch.setattr(bridge, "SETTINGS", tmp_path / ".cr-connection.json")
    monkeypatch.setattr(bridge, "_project", None)
    bridge.save_settings({"url": "https://channelrecipe.com", "key": "cr_mcp_test_only", "costs_accepted": True})
    return tmp_path


def test_connection_file_is_private_and_unsecured_remote_url_is_rejected(local):
    assert bridge.SETTINGS.stat().st_mode & 0o077 == 0
    with pytest.raises(RuntimeError, match="secure HTTPS"):
        bridge.call("status", settings={"url": "http://remote.example", "key": "cr_mcp_test_only"})


def test_a_dropped_connection_resends_the_same_request_which_the_server_never_repeats(local, monkeypatch):
    """The server keys model requests by ID: sending one again collects the same answer
    (or reports it stopped). Re-sending can never start a second paid request."""
    calls, sleeps = [], []
    replies = iter([bridge.requests.ReadTimeout(), SimpleNamespace(status_code=200, headers={},
                    json=lambda: {"text": "A decision", "request_id": "recorded"})])

    def send(*a, **kw):
        calls.append(kw["json"])
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(bridge.requests, "request", send)
    monkeypatch.setattr(bridge.time, "sleep", sleeps.append)
    assert bridge.call("brain", {"request_id": "recorded"})["text"] == "A decision"
    assert calls == [{"request_id": "recorded"}] * 2 and sleeps == [10]


def test_other_requests_are_not_repeated_after_a_dropped_connection(local, monkeypatch):
    calls = []

    def fail(*a, **kw):
        calls.append(kw)
        raise bridge.requests.ReadTimeout()

    monkeypatch.setattr(bridge.requests, "request", fail)
    with pytest.raises(RuntimeError, match="Could not reach"):
        bridge.call("projects", {"title": "x"})
    assert len(calls) == 1


def test_capacity_waits_are_bounded_and_keep_the_same_payload(local, monkeypatch):
    calls, now = [], [0.0]

    def reply(*a, **kw):
        calls.append(kw)
        return SimpleNamespace(status_code=429, headers={"Retry-After": "10"}, json=lambda: {"detail": "Busy"})

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(bridge.requests, "request", reply)
    monkeypatch.setattr(bridge.time, "sleep", sleep)
    monkeypatch.setattr(bridge.time, "monotonic", lambda: now[0])
    with pytest.raises(RuntimeError, match="Busy"):
        bridge.call("brain", {"request_id": "same-id"})
    assert bridge.BRAIN_PATIENCE_S <= now[0] < bridge.BRAIN_PATIENCE_S + 61
    assert all(c["json"] == {"request_id": "same-id"} for c in calls)


def test_engine_text_research_vision_and_resumption_use_same_bridge(local, monkeypatch):
    calls = []
    def api(path, payload=None, settings=None):
        calls.append((path, payload))
        if path == "projects": return {"id": "crv_" + "1" * 24}
        if path.startswith("projects/"): return {"id": path.split("/")[1]}
        return {"text": "A decision", "input_tokens": 5, "output_tokens": 8, "request_id": payload["request_id"]}
    monkeypatch.setattr(bridge, "call", api)
    from PIL import Image
    pic = local / "review.png"
    Image.new("RGB", (1800, 1800), "black").save(pic)
    engine = SimpleNamespace()
    bridge.install(engine)
    assert engine.claude("Outline", "old-source-model") == "A decision"
    assert engine.claude("Outline", "old-source-model") == "A decision"
    assert calls[1][1]["request_id"] == calls[2][1]["request_id"]
    assert calls[1][1]["requested_model"] == "old-source-model"
    assert calls[1][1]["model_role"] == "general"
    engine.claude_research("Verify")
    engine.claude_vision("Review", [pic])
    assert calls[-2][1]["kind"] == "research"
    assert calls[-1][1]["kind"] == "vision" and calls[-1][1]["images"]
    receipt = next((local / "output/cr-receipts").glob("*.json"))
    assert "A decision" not in receipt.read_text()
    assert len(json.loads(receipt.read_text())) == 3
    bridge.begin_project("Resume", "jung", "crv_" + "1" * 24)
    assert calls[-1][0] == "projects/crv_" + "1" * 24
    with pytest.raises(RuntimeError, match="connected ChannelRecipe Claude route"):
        engine._claude_exe()
    assert engine._claude_endpoint("anthropic") is None


def test_specialist_source_models_keep_their_visual_role_and_legacy_request_id(local, monkeypatch):
    import hashlib
    calls = []
    def api(path, payload=None, settings=None):
        if path == "projects":
            return {"id": "crv_" + "1" * 24}
        calls.append(payload)
        return {"text": "Code", "provider": "kie", "model": "claude-opus-5-5"}
    monkeypatch.setattr(bridge, "call", api)
    engine = SimpleNamespace()
    bridge.install(engine)
    engine.claude("Write a scene", "claude-opus-5-5", max_tokens=24000)
    payload = calls[-1]
    assert payload["model_role"] == "visual" and payload["requested_model"] == "claude-opus-5-5"
    prior_payload = {k: v for k, v in payload.items() if k not in {"model_role", "requested_model", "request_id"}}
    assert payload["request_id"] == "r_" + hashlib.sha256(json.dumps(prior_payload, sort_keys=True).encode()).hexdigest()
    engine.claude_research("Verify a claim", model="claude-sonnet-5")
    assert calls[-1]["model_role"] == "general" and calls[-1]["kind"] == "research"
    receipt = json.loads(next((local / "output/cr-receipts").glob("*.json")).read_text())
    assert any(row["model"] == "claude-opus-5-5" for row in receipt.values())


def test_no_brain_or_product_work_without_spending_agreement(local, monkeypatch):
    bridge.save_settings({"key": "cr_mcp_test_only", "costs_accepted": False})
    monkeypatch.setattr(bridge, "call", lambda *a, **k: pytest.fail("No billable request should occur"))
    with pytest.raises(RuntimeError, match="Review provider spending"):
        bridge.think("Write a product")


def test_model_check_budget_reserves_only_one_explicit_recovery_per_model(local,monkeypatch):
    bridge.save_settings({**bridge.load_settings(),"brain_mode":"claudecode"})
    requests=[]
    monkeypatch.setattr(bridge,"_mcp_tool",lambda name,payload:requests.append((name,payload)) or {"project":{"id":"crv_"+"1"*24}})
    bridge.begin_project("Model connection checks","custom",proof={"purpose":"setup"},purpose="setup")
    assert requests[0][1]["max_calls"]==4
