"""MCP retry approval must safely reconcile pending first-attempt receipts."""
import json
from types import SimpleNamespace

import pytest

from webapp import frontier_studio as studio
from webapp import mcp_server


@pytest.fixture
def pending(tmp_path, monkeypatch):
    monkeypatch.setattr(studio.db, "IS_PG", False)
    monkeypatch.setattr(studio.db, "DB_PATH", tmp_path / "late-receipts.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setattr(studio, "release_ready", lambda: True)
    monkeypatch.setattr(studio.providers, "generate",
                        lambda *a, **k: pytest.fail("Receipt recovery must not generate content"))
    owner = {"id": 1, "email": "owner@example.com"}
    monkeypatch.setattr(mcp_server, "_user_from_request", lambda ctx: owner)
    proof = {"local_version": studio.VERSION, "general_model": "claude-sonnet-5-5",
             "visual_model": "claude-opus-5-5", "claude_installed": True,
             "claude_authenticated": True, "api_provider": "firstParty",
             "ffmpeg_ready": True, "browser_ready": True, "node_ready": True,
             "costs_accepted": True, "models_verified": True, "media_ready": True}
    project = studio.native_begin(owner, studio.NativeBegin(
        begin_id="late_receipt_project", title="Saved original video", style="history", setup=proof))["project"]
    request = studio.NativeAuthorize(project_id=project["id"], stage_id="cc_" + "a" * 64,
        input_fingerprint="a" * 64, requested_model="claude-sonnet-5", local_version=studio.VERSION)
    approval = call(mcp_server.local_studio_authorize_stage, request.model_dump())
    return SimpleNamespace(owner=owner, project=project, request=request, approval=approval)


def call(tool, payload):
    return json.loads(tool(payload=payload))


def receipt(pending, status="failed", usage=None, token=None):
    return studio.NativeFinishStage(project_id=pending.project["id"], stage_id=pending.request.stage_id,
        authorization_token=token or pending.approval["authorization_token"], status=status,
        result_fingerprint="f" * 64 if status == "complete" else "",
        error_code="timeout" if status == "failed" else "", usage=usage or {})


def retry(pending):
    result = call(mcp_server.local_studio_retry_stage, {"project_id": pending.project["id"],
        "stage_id": pending.request.stage_id, "acknowledge_costs": True})
    assert result["approved"]


def progress(pending):
    return call(mcp_server.local_studio_progress, {"project_id": pending.project["id"]})


@pytest.mark.parametrize("already_recorded", [False, True])
def test_mcp_approved_retry_survives_late_failed_receipt_without_counting_again(pending, already_recorded):
    failure = receipt(pending, usage={"input_tokens": 4, "output_tokens": 7, "reported": True})
    if already_recorded:
        assert call(mcp_server.local_studio_finish_stage, failure.model_dump())["saved"]
    retry(pending)
    before = progress(pending)
    for _ in range(2):
        reconciled = call(mcp_server.local_studio_finish_stage, failure.model_dump())
        assert reconciled == {"saved": True, "reused": True, "status": "retry_allowed",
                              "stage_id": pending.request.stage_id}
    after = progress(pending)
    assert after["project"] == before["project"]
    assert after["stages"] == before["stages"]
    assert after["project"]["calls"] == 1
    assert after["stages"][0]["status"] == "retry_allowed"
    second = call(mcp_server.local_studio_authorize_stage, pending.request.model_dump())
    assert second["execute"] and second["authorization_token"] != pending.approval["authorization_token"]
    assert progress(pending)["project"]["calls"] == 2


@pytest.mark.parametrize("previous_usage,expected_input,expected_output", [
    ({}, 21, 13),
    ({"input_tokens": 4, "cache_read_input_tokens": 2, "cache_creation_input_tokens": 1,
      "output_tokens": 7, "reported": True}, 21, 13),
    ({"input_tokens": 30, "output_tokens": 20, "reported": True}, 30, 20),
])
def test_mcp_late_success_completes_before_retry_and_only_adds_usage_delta(
        pending, previous_usage, expected_input, expected_output):
    if previous_usage:
        call(mcp_server.local_studio_finish_stage, receipt(pending, usage=previous_usage).model_dump())
    retry(pending)
    success = receipt(pending, status="complete", usage={"input_tokens": 9,
        "cache_read_input_tokens": 7, "cache_creation_input_tokens": 5,
        "output_tokens": 13, "reported": True})
    first = call(mcp_server.local_studio_finish_stage, success.model_dump())
    assert first["saved"] and not first["reused"] and first["status"] == "complete"
    assert call(mcp_server.local_studio_finish_stage, success.model_dump())["reused"]
    reused = call(mcp_server.local_studio_authorize_stage, pending.request.model_dump())
    assert reused["reuse"] and not reused["execute"]
    saved = progress(pending)
    assert saved["project"]["status"] == "ready"
    assert saved["project"]["calls"] == 1
    assert saved["project"]["input_tokens"] == expected_input
    assert saved["project"]["output_tokens"] == expected_output
    assert saved["stages"][0]["attempts"] == 1
    assert saved["stages"][0]["usage"] == success.usage.model_dump()
    assert not saved["stages"][0]["can_retry"]


@pytest.mark.parametrize("old_status", ["failed", "complete"])
def test_mcp_rotated_second_attempt_token_rejects_old_receipts(pending, old_status):
    failure = receipt(pending, usage={"input_tokens": 4, "output_tokens": 7, "reported": True})
    call(mcp_server.local_studio_finish_stage, failure.model_dump())
    retry(pending)
    second = call(mcp_server.local_studio_authorize_stage, pending.request.model_dump())
    before = progress(pending)
    stale = receipt(pending, status=old_status, usage={"input_tokens": 30, "output_tokens": 40})
    rejected = call(mcp_server.local_studio_finish_stage, stale.model_dump())
    assert rejected["status"] == 409 and "matching execution authorization" in rejected["error"]
    assert progress(pending) == before
    current = receipt(pending, status="complete", token=second["authorization_token"],
                      usage={"input_tokens": 9, "output_tokens": 11, "reported": True})
    assert call(mcp_server.local_studio_finish_stage, current.model_dump())["saved"]
    assert call(mcp_server.local_studio_finish_stage, current.model_dump())["reused"]
    saved = progress(pending)
    assert saved["project"]["calls"] == 2
    assert saved["project"]["input_tokens"] == 13
    assert saved["project"]["output_tokens"] == 18
    assert saved["stages"][0]["attempts"] == 2
