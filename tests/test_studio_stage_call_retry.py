"""A lost answer on a running video's stage call is sent again; nothing can run twice (recipe sweep, 2026-10-26).

A FOLIO video stopped at shot 8 of 22 with "ChannelRecipe could not authorize this stage": the dev Studio's log showed
one Cloudflare 521 page ("web server is down") for that one authorization. Lost answers are now TransportError, and
cr_bridge sends again only the calls ChannelRecipe records under their own ids (authorize, finish a stage, finish a
project). The request-level rule in test_studio_connect_retry stays: _message itself never resends anything.
"""
import sys
from pathlib import Path

import pytest
import requests
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_bridge  # noqa: E402
import cr_mcp  # noqa: E402
from webapp import frontier_studio as studio  # noqa: E402


class Page:
    """What requests.post hands back: a status and headers, used as a context manager."""
    def __init__(self, status, headers=None):
        self.status_code, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def send(monkeypatch, reply, headers=None):
    monkeypatch.setattr(cr_mcp.requests, "post", lambda *a, **k: reply() if callable(reply) else reply)
    return cr_mcp._message("https://channelrecipe.com/mcp", headers or {}, {"jsonrpc": "2.0", "id": 2, "method": "tools/call"})


@pytest.mark.parametrize("status", [521, 520, 502, 503, 504, 429])
def test_a_gateway_error_page_is_a_lost_answer(monkeypatch, status):
    with pytest.raises(cr_mcp.TransportError, match="needs attention"):
        send(monkeypatch, Page(status))


def test_a_read_timeout_or_reset_after_sending_is_a_lost_answer_and_is_not_resent_here(monkeypatch):
    calls = []

    def timeout():
        calls.append(1)
        raise requests.exceptions.ReadTimeout("read timed out")

    with pytest.raises(cr_mcp.TransportError, match="Could not connect"):
        send(monkeypatch, timeout)
    assert calls == [1], "the request level never resends a request that may have reached the server"


@pytest.mark.parametrize("status,kind", [(401, RuntimeError), (403, RuntimeError), (400, RuntimeError), (404, RuntimeError)])
def test_the_servers_own_refusals_are_not_lost_answers(monkeypatch, status, kind):
    with pytest.raises(kind) as failure:
        send(monkeypatch, Page(status))
    assert not isinstance(failure.value, cr_mcp.TransportError)


def test_an_unknown_session_starts_a_new_one(monkeypatch):
    cr_mcp._state.identity = "an old session"
    with pytest.raises(cr_mcp.TransportError):
        send(monkeypatch, Page(404), headers={"Mcp-Session-Id": "gone"})
    assert cr_mcp._state.identity is None


def flaky(monkeypatch, failures, answer=None):
    """call_tool loses `failures` answers, then answers; returns what was sent."""
    sent = []

    def call_tool(name, arguments, settings):
        sent.append((name, arguments))
        if len(sent) <= failures:
            raise cr_mcp.TransportError("ChannelRecipe's workflow connection needs attention. Check setup and try again.")
        return answer or {"execute": True, "authorization_token": "t" * 32}

    monkeypatch.setattr(cr_mcp, "call_tool", call_tool)
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: {})
    return sent


@pytest.mark.parametrize("tool", sorted(cr_bridge.RETRY_TOOLS))
def test_a_stage_call_rides_out_lost_answers_with_the_same_request(monkeypatch, tool):
    sent, sleeps = flaky(monkeypatch, 2), []
    assert cr_bridge._mcp_tool(tool, {"stage_id": "cc_" + "a" * 64}, sleep=sleeps.append)["execute"] is True
    assert sent == [(tool, {"payload": {"stage_id": "cc_" + "a" * 64}})] * 3 and sleeps == [5, 15]


def test_a_long_outage_still_stops_after_about_three_minutes(monkeypatch):
    sent, sleeps = flaky(monkeypatch, 99), []
    with pytest.raises(cr_mcp.TransportError):
        cr_bridge._mcp_tool("local_studio_authorize_stage", {}, sleep=sleeps.append)
    assert len(sent) == len(cr_bridge.TOOL_RETRY_DELAYS) + 1 and sum(sleeps) == 200


@pytest.mark.parametrize("tool", ["local_studio_begin", "local_studio_retry_stage", "local_studio_prepare",
                                  "local_studio_progress"])
def test_starting_work_an_approved_retry_and_page_reads_are_never_resent(monkeypatch, tool):
    sent, sleeps = flaky(monkeypatch, 1), []
    with pytest.raises(cr_mcp.TransportError):
        cr_bridge._mcp_tool(tool, {}, sleep=sleeps.append)
    assert len(sent) == 1 and sleeps == []


@pytest.mark.parametrize("error", [cr_mcp.WorkflowError("This stage already started or stopped.", 409),
                                   RuntimeError("ChannelRecipe returned an invalid workflow result.")])
def test_answers_and_protocol_faults_are_not_resent(monkeypatch, error):
    sent = []

    def call_tool(*args):
        sent.append(1)
        raise error

    monkeypatch.setattr(cr_mcp, "call_tool", call_tool)
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: {})
    with pytest.raises(type(error)):
        cr_bridge._mcp_tool("local_studio_authorize_stage", {}, sleep=lambda s: pytest.fail("no wait"))
    assert sent == [1]


# ── against ChannelRecipe's own stage records: a resent call can never run work twice ──────────────────────────────

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(studio.db, "IS_PG", False)
    monkeypatch.setattr(studio.db, "DB_PATH", tmp_path / "native.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setattr(studio, "release_ready", lambda: True)
    owner = {"id": 1, "email": "owner@example.com"}
    proof = {"local_version": studio.VERSION, "general_model": "claude-sonnet-5-5", "visual_model": "claude-opus-5-5",
             "claude_installed": True, "claude_authenticated": True, "api_provider": "firstParty", "ffmpeg_ready": True,
             "browser_ready": True, "node_ready": True, "costs_accepted": True, "models_verified": True,
             "media_ready": True}
    project = studio.native_begin(owner, studio.NativeBegin(begin_id="begin_retry_test", title="A video",
                                                            style="history", setup=proof))["project"]
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: {})
    return owner, project


def wire(monkeypatch, owner, lose):
    """call_tool runs the server's handler; `lose(n)` says whether the n-th answer is lost after the server acted
    ('after'), lost before it ran ('before') or delivered (None)."""
    handlers = {"local_studio_authorize_stage": (studio.native_authorize, studio.NativeAuthorize),
                "local_studio_finish_stage": (studio.native_finish_stage, studio.NativeFinishStage)}
    calls = []

    def call_tool(name, arguments, settings):
        calls.append(name)
        when = lose(len(calls))
        if when == "before":
            raise cr_mcp.TransportError("ChannelRecipe's workflow connection needs attention. Check setup and try again.")
        method, contract = handlers[name]
        try:
            answer = method(owner, contract(**arguments["payload"]))
        except HTTPException as error:
            raise cr_mcp.WorkflowError(error.detail, error.status_code) from None
        if when == "after":
            raise cr_mcp.TransportError("Could not connect to ChannelRecipe's workflow tools. Check setup and your connection.")
        return answer

    monkeypatch.setattr(cr_mcp, "call_tool", call_tool)
    return calls


def stage_request(project):
    return {"project_id": project["id"], "stage_id": "cc_" + "b" * 64, "input_fingerprint": "b" * 64,
            "model_role": "general", "requested_model": "claude-sonnet-5", "local_version": studio.VERSION}


def rows(owner, project):
    return studio.native_progress(owner, project["id"])["stages"]


def test_a_gateway_page_before_the_server_acted_authorizes_the_stage_once(server, monkeypatch):
    owner, project = server
    wire(monkeypatch, owner, lambda n: "before" if n == 1 else None)
    approval = cr_bridge._mcp_tool("local_studio_authorize_stage", stage_request(project), sleep=lambda s: None)
    assert approval["execute"] is True and len(rows(owner, project)) == 1


def test_an_answer_lost_after_the_server_authorized_is_refused_not_run_twice(server, monkeypatch):
    owner, project = server
    wire(monkeypatch, owner, lambda n: "after" if n == 1 else None)
    with pytest.raises(cr_mcp.WorkflowError, match="already started or stopped"):
        cr_bridge._mcp_tool("local_studio_authorize_stage", stage_request(project), sleep=lambda s: None)
    assert [r["status"] for r in rows(owner, project)] == ["authorized"], "one stage, one authorization"


def test_a_saved_result_whose_answer_was_lost_is_reused(server, monkeypatch):
    owner, project = server
    wire(monkeypatch, owner, lambda n: "after" if n == 2 else None)
    approval = cr_bridge._mcp_tool("local_studio_authorize_stage", stage_request(project), sleep=lambda s: None)
    receipt = {"project_id": project["id"], "stage_id": "cc_" + "b" * 64, "authorization_token": approval["authorization_token"],
               "status": "complete", "result_fingerprint": "c" * 64, "error_code": "",
               "usage": {"input_tokens": 10, "output_tokens": 5}}
    saved = cr_bridge._mcp_tool("local_studio_finish_stage", receipt, sleep=lambda s: None)
    assert saved["saved"] and saved["reused"] and [r["status"] for r in rows(owner, project)] == ["complete"]
