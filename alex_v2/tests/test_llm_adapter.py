"""The Gemini/Live adapter, against a mock client using the real google-genai types."""

import asyncio

import pytest

genai = pytest.importorskip("google.genai")
from google.genai import types  # noqa: E402

from alex_v2 import llm as LLM  # noqa: E402
from alex_v2 import prompt as P  # noqa: E402


class _Msg:
    def __init__(self, text=None, done=False):
        parts = [types.Part(text=text)] if text else []
        self.server_content = types.LiveServerContent(model_turn=types.Content(role="model", parts=parts) if parts else None,
                                                      turn_complete=done)


class _Session:
    def __init__(self, chunks, rec):
        self.chunks, self.rec = chunks, rec

    async def send_client_content(self, *, turns=None, turn_complete=True):
        self.rec["sent"] = turns

    async def receive(self):
        for i, c in enumerate(self.chunks):
            yield _Msg(c, done=(i == len(self.chunks) - 1))


class _Live:
    def __init__(self, chunks, rec, fail=None):
        self.chunks, self.rec, self.fail = chunks, rec, fail

    def connect(self, *, model, config):
        rec, chunks, fail = self.rec, self.chunks, self.fail
        rec.setdefault("configs", []).append(config)
        rec["model"] = model

        class _CM:
            async def __aenter__(self_inner):
                if fail:
                    raise fail
                return _Session(chunks, rec)

            async def __aexit__(self_inner, *a):
                return False
        return _CM()


class _Models:
    def __init__(self, rec, text="type: playful\nC: answer_and_pivot | Adulting I see"):
        self.rec, self.text = rec, text

    async def generate_content(self, *, model, contents, config):
        self.rec.setdefault("gen", []).append((model, config))
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=self.text)]))])


class _Client:
    def __init__(self, rec, chunks=("type: playful\n", "C: answer_and_pivot | Adulting I see"), live_fail=None):
        self.aio = type("A", (), {})()
        self.aio.live = _Live(list(chunks), rec, live_fail)
        self.aio.models = _Models(rec)


def test_live_model_text_path(monkeypatch):
    rec = {}
    monkeypatch.setattr(LLM, "_client", lambda: _Client(rec))
    g = LLM.GeminiLLM({"model": "gemini-3.8-flash-live", "fallback_model": "gemini-3.7-flash"})
    out = asyncio.run(g("SYS", "USER", temperature=0.6, max_output_tokens=500, thinking_budget=0, timeout_s=5))
    assert "Adulting I see" in out and g.last_model == "gemini-3.8-flash-live" and g.last_path == "live"
    cfg = rec["configs"][0]
    assert cfg.response_modalities == [types.Modality.TEXT]
    assert cfg.system_instruction.parts[0].text == "SYS" and cfg.temperature == 0.6 and cfg.max_output_tokens == 500
    assert cfg.thinking_config.thinking_budget == 0
    assert rec["sent"].parts[0].text == "USER"
    parsed = P.parse(out)
    assert parsed["candidates"][0]["text"] == "Adulting I see"


def test_live_failure_falls_back_to_generate(monkeypatch):
    rec = {}
    monkeypatch.setattr(LLM, "_client", lambda: _Client(rec, live_fail=RuntimeError("response_modalities TEXT not supported")))
    g = LLM.GeminiLLM({"model": "gemini-3.8-flash-live", "fallback_model": "gemini-3.7-flash"})
    out = asyncio.run(g("SYS", "USER", timeout_s=5))
    assert "Adulting" in out and g.last_model == "gemini-3.7-flash"
    assert rec["gen"][0][0] == "gemini-3.7-flash"
    assert "gemini-3.8-flash-live" in LLM._LIVE_TEXT_BROKEN
    LLM._LIVE_TEXT_BROKEN.clear()


def test_thinking_budget_rejected_is_retried_without(monkeypatch):
    rec = {"n": 0}

    class _M(_Models):
        async def generate_content(self, *, model, contents, config):
            rec["n"] += 1
            if config.thinking_config is not None:
                raise RuntimeError("400 thinking budget is not supported for this model")
            return await super().generate_content(model=model, contents=contents, config=config)

    c = _Client(rec)
    c.aio.models = _M(rec)
    monkeypatch.setattr(LLM, "_client", lambda: c)
    g = LLM.GeminiLLM({"model": "gemini-3.7-flash", "fallback_model": ""})
    out = asyncio.run(g("SYS", "USER", thinking_budget=0, timeout_s=5))
    assert "Adulting" in out and rec["n"] == 2


def test_call_accepts_plain_sync_function():
    def my_model(system, user):
        return "type: playful\nC: answer_and_pivot | Adulting I see"
    out = asyncio.run(LLM.call(my_model, "S", "U", {"timeout_s": 5}))
    assert "Adulting" in out


def test_parse_tolerates_json_and_noise():
    j = '```json\n{"analysis": {"type": "shit_test", "light": "green"}, "candidates": [{"move_id": "pass_shit_test", "text": "Guilty"},]}\n```'
    p = P.parse(j)
    assert p["analysis"]["her_msg_type"] == "shit_test" and p["candidates"][0]["text"] == "Guilty"
    p2 = P.parse("Type: Shit test\nLight: GREEN\nflags: cancelled, cancel_reason\n- C1: pass_shit_test | Guilty\nC2: [tease_frame] | Confident, are we")
    assert p2["analysis"]["her_msg_type"] == "shit_test" and p2["analysis"]["sexual_light"] == "green"
    assert p2["analysis"]["cancel_has_reason"] and [c["move_id"] for c in p2["candidates"]] == ["pass_shit_test", "tease_frame"]
