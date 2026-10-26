"""Production native-Claude authorization, isolation and saved-result recovery."""
import hashlib
import json
import os
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from webapp import frontier_studio as studio
from local_runtime import cr_claude, cr_mcp


@pytest.fixture
def native(tmp_path,monkeypatch):
    monkeypatch.setattr(studio.db,"IS_PG",False)
    monkeypatch.setattr(studio.db,"DB_PATH",tmp_path/"native.db")
    monkeypatch.setattr(studio.config,"ADMIN_EMAILS",["owner@example.com","other@example.com"])
    monkeypatch.setattr(studio,"release_ready",lambda:True)
    monkeypatch.setattr(studio.providers,"generate",lambda *a,**k:pytest.fail("Native stages must never spend an API wallet"))
    owner={"id":1,"email":"owner@example.com"}
    other={"id":2,"email":"other@example.com"}
    proof={"local_version":studio.VERSION,"general_model":"claude-sonnet-5-5","visual_model":"claude-opus-5-5",
        "claude_installed":True,"claude_authenticated":True,"api_provider":"firstParty","ffmpeg_ready":True,
        "browser_ready":True,"node_ready":True,"costs_accepted":True,"models_verified":True,"media_ready":True}
    begin=studio.NativeBegin(begin_id="begin_one_native",title="A complete video",style="history",setup=proof)
    project=studio.native_begin(owner,begin)["project"]
    return SimpleNamespace(owner=owner,other=other,proof=proof,begin=begin,project=project,tmp=tmp_path)


def stage(n,suffix="a",role="general"):
    return studio.NativeAuthorize(project_id=n.project["id"],stage_id="cc_"+suffix*64,
        input_fingerprint=suffix*64,model_role=role,requested_model="claude-sonnet-5",local_version=studio.VERSION)


def test_optional_renderer_and_checked_identity_are_pinned_in_project(native):
    req=native.begin.model_copy(update={"begin_id":"optional_renderer", "options":{"render_backend":"manimgx","manimgx_identity":"a"*64}})
    result=studio.native_begin(native.owner,req)
    assert result["job_spec"]["render_backend"]=="manimgx"
    assert studio.native_progress(native.owner,result["project_id"])["job_spec"]["manimgx_identity"]=="a"*64
    for invalid in ({"render_backend":"other"},{"render_backend":"manimgx"}):
        with pytest.raises(HTTPException):
            studio.native_begin(native.owner,req.model_copy(update={"begin_id":"invalid_renderer","options":invalid}))


def test_native_uses_no_api_credentials_and_duplicate_begin_is_idempotent(native):
    assert studio.credential(1) is None
    repeated=studio.native_begin(native.owner,native.begin)
    assert repeated["project_id"]==native.project["id"] and repeated["resumed"]
    assert native.project["id"] in repeated["launch_command"]
    assert native.begin.title not in repeated["launch_command"]
    changed=native.begin.model_copy(update={"title":"Different saved work"})
    with pytest.raises(HTTPException,match="different saved work"):
        studio.native_begin(native.owner,changed)


@pytest.mark.parametrize("purpose", ["setup", "creative"])
def test_non_video_actions_cannot_launch_a_saved_video(native, purpose):
    result = studio.native_begin(native.owner, studio.NativeBegin(begin_id="action_" + purpose,
        title="Original studio action", style="custom", setup={**native.proof, "purpose": purpose}))
    assert result["purpose"] == purpose and result["launch_command"] == "" and not result["launch_required"]
    progress = studio.native_progress(native.owner, result["project_id"])
    assert progress["purpose"] == purpose and not progress["can_resume"]


def test_recovery_notice_includes_owned_failed_native_stages(native):
    assert studio.status(native.owner)["recovery_requests"] == 0
    req = stage(native)
    allowed = studio.native_authorize(native.owner, req)
    # An active stage is normal progress, not a stopped-work notice.
    assert studio.status(native.owner)["recovery_requests"] == 0
    studio.native_finish_stage(native.owner, studio.NativeFinishStage(
        project_id=req.project_id, stage_id=req.stage_id,
        authorization_token=allowed["authorization_token"], status="failed", error_code="usage_limit"))
    assert studio.status(native.owner)["recovery_requests"] == 1
    assert studio.status(native.other)["recovery_requests"] == 0


@pytest.mark.parametrize("field,value",[("claude_authenticated",False),("models_verified",False),("media_ready",False),
    ("node_ready",False),("ffmpeg_ready",False),("browser_ready",False),("costs_accepted",False),("local_version","old")])
def test_every_required_setup_failure_blocks_video_before_authorization(native,field,value):
    proof=studio.NativeSetup(**{**native.proof,field:value})
    assert not studio.native_prepare(native.owner,proof)["ready"]


def test_ownership_live_access_and_specialist_profile_are_enforced(native):
    with pytest.raises(HTTPException): studio.native_progress(native.other,native.project["id"])
    selected=studio.native_authorize(native.owner,stage(native,role="visual"))
    assert selected["selected_model"]=="claude-opus-5-5"
    with pytest.raises(HTTPException): studio.native_authorize(native.owner,stage(native))
    studio.config.ADMIN_EMAILS=[]
    with pytest.raises(HTTPException): studio.native_progress(native.owner,native.project["id"])


def test_stage_reservation_completion_and_recovery_do_not_count_twice(native):
    req=stage(native)
    allowed=studio.native_authorize(native.owner,req)
    with pytest.raises(HTTPException): studio.native_authorize(native.owner,req)
    finish=studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=allowed["authorization_token"],status="complete",result_fingerprint="f"*64,
        usage={"input_tokens":4,"output_tokens":7,"reported":True})
    assert studio.native_finish_stage(native.owner,finish)["saved"]
    assert studio.native_finish_stage(native.owner,finish)["reused"]
    reuse=studio.native_authorize(native.owner,req)
    assert reuse["reuse"] and not reuse["execute"]
    p=studio.native_progress(native.owner,req.project_id)["project"]
    assert p["calls"]==1 and p["input_tokens"]==4 and p["output_tokens"]==7
    with pytest.raises(HTTPException):
        studio.native_finish_stage(native.owner,finish.model_copy(update={"authorization_token":"wrong"*8}))


def test_adapter_upgrade_reuses_pinned_project_and_completed_stage(native,monkeypatch):
    monkeypatch.setattr(studio,"VERSION","5.3-admin.9")
    native.proof={**native.proof,"local_version":studio.VERSION}
    native.project=studio.native_begin(native.owner,studio.NativeBegin(begin_id="old_adapter_project",title="A complete video",
        style="history",setup=native.proof))["project"]
    req=stage(native)
    approval=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=approval["authorization_token"],status="complete",result_fingerprint="f"*64))
    monkeypatch.setattr(studio,"VERSION","5.3-admin.10")
    setup={**native.proof,"local_version":studio.VERSION}
    resumed=studio.native_begin(native.owner,studio.NativeBegin(begin_id="resume_adapter_upgrade",title="A complete video",
        style="history",setup=setup,resume_project_id=req.project_id))
    assert resumed["resumed"]
    assert studio.native_authorize(native.owner,req.model_copy(update={"local_version":studio.VERSION}))["reuse"]
    assert studio.native_progress(native.owner,req.project_id)["project"]["calls"]==1
    monkeypatch.setattr(studio,"SOURCE_SHA256","0"*64)
    with pytest.raises(HTTPException):
        studio.native_authorize(native.owner,req.model_copy(update={"local_version":studio.VERSION}))


def test_running_approved_runtime_survives_cloud_update_but_new_projects_require_latest(native,monkeypatch):
    monkeypatch.setattr(studio,"VERSION","5.3-admin.19")
    native.proof={**native.proof,"local_version":studio.VERSION}
    native.project=studio.native_begin(native.owner,studio.NativeBegin(begin_id="pinned_19_running",
        title="Saved video",style="history",setup=native.proof))["project"]
    monkeypatch.setattr(studio,"VERSION","5.3-admin.26")
    req=stage(native)
    approval=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=approval["authorization_token"],status="complete",result_fingerprint="f"*64))
    monkeypatch.setattr(studio,"VERSION","5.3-admin.27")
    assert studio.native_authorize(native.owner,req)["reuse"]
    next_stage=stage(native,"b").model_copy(update={"local_version":"5.3-admin.26"})
    assert studio.native_authorize(native.owner,next_stage)["execute"]
    for invalid in ("5.3-admin.18","5.3-admin.28","5.3-admin.999","unrecognized"):
        with pytest.raises(HTTPException):
            studio.native_authorize(native.owner,stage(native,"c").model_copy(update={"local_version":invalid}))
    old_setup={**native.proof,"local_version":"5.3-admin.26"}
    assert not studio.native_prepare(native.owner,studio.NativeSetup(**old_setup))["ready"]
    with pytest.raises(HTTPException):
        studio.native_begin(native.owner,studio.NativeBegin(begin_id="fresh_old_runtime",title="New video",
            style="history",setup=old_setup))
    latest_setup={**old_setup,"local_version":"5.3-admin.27"}
    assert studio.native_begin(native.owner,studio.NativeBegin(begin_id="fresh_latest_runtime",title="New video",
        style="history",setup=latest_setup))["project"]
    monkeypatch.setattr(studio,"WORKFLOW_VERSION","different_workflow")
    with pytest.raises(HTTPException):studio.native_authorize(native.owner,req)


def test_uncertain_or_failed_stage_never_restarts_automatically(native):
    req=stage(native)
    allowed=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=allowed["authorization_token"],status="failed",error_code="timeout"))
    with pytest.raises(HTTPException):studio.native_authorize(native.owner,req)
    assert studio.native_progress(native.owner,req.project_id)["project"]["calls"]==1


def test_recovered_first_setup_model_leaves_budget_for_second_model(native):
    proof={**native.proof,"purpose":"setup","models_verified":False}
    project=studio.native_begin(native.owner,studio.NativeBegin(begin_id="two_models_with_recovery",title="Model connection checks",
        style="custom",setup=proof,max_calls=4))["project"]
    req=studio.NativeAuthorize(project_id=project["id"],stage_id="setup_first_model",input_fingerprint="a"*64,requested_model="claude-sonnet-5-5",local_version=studio.VERSION)
    first=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=first["authorization_token"],status="failed",error_code="network_interrupted"))
    studio.native_retry_stage(native.owner,studio.NativeRetryStage(project_id=req.project_id,stage_id=req.stage_id,acknowledge_costs=True))
    retried=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=retried["authorization_token"],status="complete",result_fingerprint="b"*64))
    visual=req.model_copy(update={"stage_id":"setup_second_model","input_fingerprint":"c"*64,"model_role":"visual","requested_model":"claude-opus-5-5"})
    second=studio.native_authorize(native.owner,visual)
    assert second["execute"] and second["selected_model"]=="claude-opus-5-5"
    assert studio.native_progress(native.owner,req.project_id)["project"]["calls"]==3


def test_final_metadata_cannot_hide_incomplete_stages(native):
    studio.native_authorize(native.owner,stage(native))
    report=studio.NativeFinishProject(project_id=native.project["id"],status="done",
        artifacts=[{"name":"video.mp4","sha256":"e"*64,"media_type":"video/mp4"}])
    with pytest.raises(HTTPException):studio.native_finish_project(native.owner,report)


def test_recovery_requires_acknowledgement_and_a_new_approval_for_each_bounded_attempt(native):
    """Each further run of a stopped stage needs its own approval, up to MAX_STAGE_ATTEMPTS runs in all: a stage that
    stopped twice (a usage limit met again before its reset) no longer leaves the video unfinishable."""
    req=stage(native)
    approval=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=approval["authorization_token"],status="failed",error_code="timeout"))
    retry=studio.NativeRetryStage(project_id=req.project_id,stage_id=req.stage_id)
    with pytest.raises(HTTPException):studio.native_retry_stage(native.owner,retry)
    with pytest.raises(HTTPException):studio.native_retry_stage(native.other,retry.model_copy(update={"acknowledge_costs":True}))
    tokens={approval["authorization_token"]}
    for attempt in range(2, studio.MAX_STAGE_ATTEMPTS + 1):
        with pytest.raises(HTTPException):studio.native_authorize(native.owner,req)   # never without the approval
        assert studio.native_retry_stage(native.owner,retry.model_copy(update={"acknowledge_costs":True}))["approved"]
        again=studio.native_authorize(native.owner,req)
        assert again["execute"] and again["authorization_token"] not in tokens
        tokens.add(again["authorization_token"])
        studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
            authorization_token=again["authorization_token"],status="failed",error_code="usage_limit"))
    with pytest.raises(HTTPException):studio.native_retry_stage(native.owner,retry.model_copy(update={"acknowledge_costs":True}))
    progress=studio.native_progress(native.owner,req.project_id)
    assert progress["project"]["calls"]==studio.MAX_STAGE_ATTEMPTS and not progress["stages"][0]["can_retry"]
    assert progress["stages"][0]["attempts_left"]==0


def test_a_late_failure_of_the_attempt_before_keeps_the_newest_approval(native):
    req=stage(native)
    first=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=first["authorization_token"],status="failed",error_code="timeout"))
    studio.native_retry_stage(native.owner,studio.NativeRetryStage(project_id=req.project_id,stage_id=req.stage_id,acknowledge_costs=True))
    second=studio.native_authorize(native.owner,req)
    # the second attempt was cut off (no receipt); the creator approves a third before its failure arrives
    studio.native_retry_stage(native.owner,studio.NativeRetryStage(project_id=req.project_id,stage_id=req.stage_id,acknowledge_costs=True))
    late=studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=second["authorization_token"],status="failed",error_code="network_interrupted"))
    assert late["status"]=="retry_allowed"
    assert studio.native_authorize(native.owner,req)["execute"]


def test_local_render_failure_can_resume_completed_claude_without_new_charge(native):
    req=stage(native)
    approval=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=approval["authorization_token"],status="complete",result_fingerprint="f"*64))
    studio.native_finish_project(native.owner,studio.NativeFinishProject(project_id=req.project_id,status="failed",error_code="render_failed"))
    resumed=studio.native_begin(native.owner,native.begin.model_copy(update={"resume_project_id":req.project_id}))
    assert resumed["project"]["status"] == "ready"
    assert studio.native_authorize(native.owner,req)["reuse"]
    assert studio.native_progress(native.owner,req.project_id)["project"]["calls"] == 1


def test_production_tools_work_over_actual_mcp_http_transport(native,monkeypatch):
    from fastapi.testclient import TestClient
    from webapp import mcp_server,mcp_oauth
    from mcp.server.auth.provider import AccessToken
    async def token(value):
        if value == "cr_mcp_native_test":
            return AccessToken(token=value,client_id="native-test",scopes=[mcp_oauth.MCP_SCOPE],subject="1")
    monkeypatch.setattr(mcp_oauth.oauth_provider,"load_access_token",token)
    monkeypatch.setattr(studio.db,"get_user_by_id",lambda value:native.owner if value==1 else None)
    headers={"Host":"localhost","Authorization":"Bearer cr_mcp_native_test","Accept":"application/json, text/event-stream"}
    def payload(response):
        response.raise_for_status()
        if "text/event-stream" in response.headers.get("content-type",""):
            return json.loads(next(line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")))
        return response.json()
    with TestClient(mcp_server.build_mcp_asgi()) as client:
        init=payload(client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"native-test","version":"1"}}}))
        headers["MCP-Protocol-Version"]=init["result"]["protocolVersion"]
        catalog=payload(client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}))
        assert {"local_studio_begin","local_studio_authorize_stage","local_studio_retry_stage"} <= {t["name"] for t in catalog["result"]["tools"]}
        progress=payload(client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":3,"method":"tools/call",
            "params":{"name":"local_studio_progress","arguments":{"payload":{"project_id":native.project["id"]}}}}))
        data=json.loads(progress["result"]["content"][0]["text"])
        assert data["project_id"] == native.project["id"] and data["brain_mode"] == "claudecode"
        from local_runtime import cr_mcp
        monkeypatch.setattr(cr_mcp,"rpc",lambda *a:progress["result"])
        decoded=cr_mcp.call_tool("local_studio_progress",{"payload":{"project_id":native.project["id"]}},{})
        assert decoded["project_id"] == native.project["id"]
        assert client.post("/mcp",headers={"Host":"localhost","Accept":"application/json, text/event-stream"},json={"jsonrpc":"2.0","id":4,"method":"tools/list"}).status_code==401


def test_cli_contract_and_local_cache_survive_connection_recovery(tmp_path,monkeypatch):
    calls=[]
    cfg={"brain_mode":"claudecode","costs_accepted":True,"local_version":studio.VERSION}
    finish=[]
    backend={}
    def authorize(s):
        if backend.get("complete"):
            return {"reuse":True,"execute":False,"result_fingerprint":backend["digest"]}
        return {"execute":True,"authorization_token":"x"*32,"selected_model":"claude-opus-5-5"}
    def save(r):
        finish.append(r)
        backend.update(complete=True,digest=r["result_fingerprint"])
    bridge=SimpleNamespace(HOME=tmp_path,load_settings=lambda:cfg,current_project=lambda:{"id":"crv_"+"1"*24},
        authorize_stage=authorize,finish_stage=save)
    def invoke(args,**kw):
        if "--version" in args:return SimpleNamespace(returncode=0,stdout="2.1.291")
        if "auth" in args:return SimpleNamespace(returncode=0,stdout=json.dumps({"loggedIn":True,"authMethod":"claude.ai","apiProvider":"firstParty"}))
        calls.append((args,kw))
        return SimpleNamespace(returncode=0,stdout=json.dumps({"type":"result","subtype":"success","result":"Original creative output","usage":{"input_tokens":9,"output_tokens":11}}))
    runtime=cr_claude.Runtime(SimpleNamespace(),bridge,"/fake/claude",invoke)
    monkeypatch.setattr(cr_claude,"_source_context",lambda:(2000,"make_video:_cc_vision"))
    args=["/fake/claude","--print","--model","claude-opus-5-5","--effort","high","--system-prompt","Original visual instructions",
        "--tools","Read","--output-format","json","--strict-mcp-config","--mcp-config",'{"mcpServers":{}}']
    kwargs={"input":"Original review with reference evidence","text":True,"capture_output":True,"env":{"ANTHROPIC_API_KEY":"must-not-bill","KIE_API_KEY":"private-media-key"},"timeout":180}
    first=runtime.run(args,**kwargs)
    second=runtime.run(args,**kwargs)
    assert first.stdout==second.stdout and len(calls)==1
    actual,options=calls[0]
    assert actual[actual.index("--system-prompt")+1]=="Original visual instructions"
    assert actual[actual.index("--effort")+1]=="high" and options["input"]==kwargs["input"]
    assert "ANTHROPIC_API_KEY" not in options["env"] and "KIE_API_KEY" not in options["env"]
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in options["env"]
    assert finish[0]["usage"]["reported"] and finish[0]["usage"]["output_tokens"]==11
    if os.name != "nt":                         # POSIX permission bits; Windows keeps the file private by its ACL
        assert next((tmp_path/"output/cr-claude-cache").rglob("*.json")).stat().st_mode&0o077==0


@pytest.mark.parametrize("envelope", ["structured", "wrapped", "text"])
def test_completed_project_authorization_preserves_creator_reason_without_executing(native, monkeypatch, envelope):
    studio.native_finish_project(native.owner, studio.NativeFinishProject(project_id=native.project["id"],
        status="done", artifacts=[{"name":"video.mp4", "sha256":"f"*64, "media_type":"video/mp4"}]))
    cfg = {"brain_mode":"claudecode", "costs_accepted":True, "local_version":studio.VERSION}

    def rpc(method, params, settings):
        assert method == "tools/call" and params["name"] == "local_studio_authorize_stage"
        with pytest.raises(HTTPException) as failure:
            studio.native_authorize(native.owner, studio.NativeAuthorize(**params["arguments"]["payload"]))
        error = {"error":failure.value.detail, "status":failure.value.status_code}
        if envelope == "structured": return {"structuredContent":error}
        if envelope == "wrapped": return {"structuredContent":{"result":json.dumps(error)}}
        return {"content":[{"type":"text", "text":json.dumps(error)}]}

    monkeypatch.setattr(cr_mcp, "rpc", rpc)
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: {"ready":True})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (2000, "make_video:metadata"))
    bridge = SimpleNamespace(HOME=native.tmp, load_settings=lambda:cfg, current_project=lambda:native.project,
        authorize_stage=lambda stage:cr_mcp.call_tool("local_studio_authorize_stage", {"payload":stage}, cfg))
    runtime = cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude",
        lambda *a, **kw:pytest.fail("Rejected work cannot execute Claude"))
    with pytest.raises(cr_claude.ClaudeStageStopped) as failure:
        runtime.run(["/fake/claude", "--print", "--model", "sonnet"], input="New description for the published video", text=True)
    assert "Resume this saved project before authorizing new work." in str(failure.value)
    assert "No new Claude request was made." in str(failure.value)
    assert studio.native_progress(native.owner, native.project["id"])["stages"] == []
    assert not (native.tmp / "output/cr-claude-cache").exists()


@pytest.mark.parametrize("source", ["transport", "protocol", "unhandled_tool"])
def test_authorization_infrastructure_diagnostics_are_not_echoed(tmp_path, monkeypatch, source):
    cfg = {"brain_mode":"claudecode", "costs_accepted":True, "local_version":studio.VERSION}
    secret = "cr_mcp_infrastructure-secret"
    def rpc(*args, **kwargs):
        if source == "unhandled_tool":
            return {"isError":True, "content":[{"type":"text", "text":"Server traceback: " + secret}]}
        raise RuntimeError("Connection/protocol diagnostic Bearer " + secret)
    monkeypatch.setattr(cr_mcp, "rpc", rpc)
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: {"ready":True})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (2000, "make_video:metadata"))
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda:cfg,
        current_project=lambda:{"id":"crv_" + "1"*24},
        authorize_stage=lambda stage:cr_mcp.call_tool("local_studio_authorize_stage", {"payload":stage}, cfg))
    runtime = cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude",
        lambda *a, **kw:pytest.fail("Infrastructure failures cannot execute Claude"))
    with pytest.raises(cr_claude.ClaudeStageStopped) as failure:
        runtime.run(["/fake/claude", "--print", "--model", "sonnet"], input="Saved metadata request", text=True)
    assert "could not authorize this stage" in str(failure.value)
    assert secret not in str(failure.value) and "Server traceback" not in str(failure.value)
    assert not (tmp_path / "output/cr-claude-cache").exists()


def test_structured_workflow_rejection_is_typed_bounded_and_credential_safe(monkeypatch):
    cfg = {"key":"cr_mcp_creator-private-key"}
    monkeypatch.setenv("KIE_API_KEY", "private-saved-media-key")
    message = ("Resume this saved project. " + cfg["key"] + " Bearer private-header-token "
               + "sk-ant-private-key private-saved-media-key https://example.invalid/?token=private-query " + "x"*900)
    monkeypatch.setattr(cr_mcp, "rpc", lambda *a, **kw: {"structuredContent":{"error":message, "status":409}})
    with pytest.raises(cr_mcp.WorkflowError) as failure:
        cr_mcp.call_tool("local_studio_authorize_stage", {}, cfg)
    error = failure.value
    assert error.status == 409 and str(error) == error.detail and len(str(error)) <= 800
    assert "Resume this saved project." in str(error)
    assert all(secret not in str(error) for secret in (cfg["key"], "private-header-token", "sk-ant-private-key",
        "private-saved-media-key", "private-query"))


def test_pending_receipt_rejection_does_not_masquerade_as_new_authorization(tmp_path, monkeypatch):
    cfg = {"brain_mode":"claudecode", "costs_accepted":True, "local_version":studio.VERSION}
    args = ["/fake/claude", "--print", "--model", "sonnet"]
    prompt = "Already completed metadata"
    fingerprint = cr_claude._digest({"args":args[1:], "input":prompt, "max_tokens":2000, "read_files":[]})
    project = "crv_" + "1"*24
    path = tmp_path / "output/cr-claude-cache" / project / ("cc_" + fingerprint + ".json")
    cr_claude._private_write(path, {"state":"complete_local", "receipt":{"status":"complete"}, "stdout":"Saved output"})
    original = path.read_bytes()
    def finish(payload): raise cr_mcp.WorkflowError("Private receipt has a different saved result.", 409)
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda:cfg, current_project=lambda:{"id":project},
        finish_stage=finish, authorize_stage=lambda *a:pytest.fail("A failed receipt cannot authorize new work"))
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: {"ready":True})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (2000, "make_video:metadata"))
    runtime = cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude",
        lambda *a, **kw:pytest.fail("A failed receipt cannot execute Claude"))
    with pytest.raises(cr_claude.ClaudeStageStopped) as failure:
        runtime.run(args, input=prompt, text=True)
    assert "Private receipt" not in str(failure.value) and "could not authorize this stage" in str(failure.value)
    assert path.read_bytes() == original


def test_read_only_claude_auth_never_returns_identity_or_api_credentials():
    def invoke(args,**kw):
        return SimpleNamespace(returncode=0,stdout="2.1.291" if "--version" in args else json.dumps({
            "loggedIn":True,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"pro","email":"private@example.com","apiKey":"secret"}))
    result=cr_claude.status("/fake/claude",run=invoke)
    assert result["ready"] and "private@example.com" not in json.dumps(result) and "secret" not in json.dumps(result)


@pytest.mark.parametrize("message,code",[("You've hit your limit · resets later","usage_limit"),
    ("You've hit your session limit · resets 4:40am (Europe/London)","usage_limit"),
    ("API Error: 529 overloaded","network_interrupted"),("Please log in again","auth_missing"),
    ("Model does not exist","model_unavailable")])
def test_failure_messages_classify_without_echoing_upstream_text(message,code):
    actual,hint=cr_claude.failure_info(message+" sk-ant-private-account-secret")
    assert actual==code and "private-account-secret" not in hint


def test_failed_stage_receipt_sync_never_runs_claude_or_keeps_authorization_token(tmp_path):
    receipt={"status":"failed","authorization_token":"private-test-authorization"}
    path=tmp_path/"output/cr-claude-cache/project/stage.json"
    cr_claude._private_write(path,{"state":"failed_local","receipt":receipt,"error_code":"cli_failed"})
    reports=[]
    bridge=SimpleNamespace(HOME=tmp_path,finish_stage=lambda payload:reports.append(payload))
    assert cr_claude.sync_receipts(bridge)==1 and reports==[receipt]
    assert cr_claude.sync_receipts(bridge)==0
    data=json.loads(path.read_text())
    assert data["state"]=="failed" and "private-test-authorization" not in path.read_text()


def test_diagnostics_are_private_and_redact_keys(monkeypatch):
    monkeypatch.setenv("KIE_API_KEY","a-private-saved-media-key")
    d=cr_claude.private_diagnostic(SimpleNamespace(returncode=1,stdout="API Error: 529",stderr="Bearer secret-header-token a-private-saved-media-key sk-ant-example-secret cr_mcp_example-secret"),{})
    assert d["returncode"]==1
    assert all(s not in json.dumps(d) for s in ("secret-header-token","a-private-saved-media-key","example-secret"))


def test_concurrent_receipt_writes_remain_atomic_and_private(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/"stage.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n:cr_claude._private_write(path,{"state":"complete","result":"x"*5000,"writer":n}),range(50)))
    data=json.loads(path.read_text())
    assert data["state"]=="complete" and len(data["result"])==5000
    assert list(tmp_path.glob("*.tmp"))==[]
    if os.name != "nt":                         # POSIX permission bits; Windows keeps the file private by its ACL
        assert path.stat().st_mode&0o077==0


def test_upgraded_runtime_authorizes_installed_release_with_saved_old_model_profile(native,monkeypatch):
    root=native.tmp
    (root/"cr-release.json").write_text(json.dumps({"version":studio.VERSION}))
    cfg={"brain_mode":"claudecode","costs_accepted":True,"local_version":"5.3-admin.9"}
    bridge=SimpleNamespace(HOME=root,load_settings=lambda:cfg,current_project=lambda:native.project,
        authorize_stage=lambda data:studio.native_authorize(native.owner,studio.NativeAuthorize(**data)),
        finish_stage=lambda data:studio.native_finish_stage(native.owner,studio.NativeFinishStage(**data)))
    def invoke(args,**kwargs):
        if "--version" in args:return SimpleNamespace(returncode=0,stdout="2.1.291")
        if "auth" in args:return SimpleNamespace(returncode=0,stdout=json.dumps({"loggedIn":True,"authMethod":"claude.ai","apiProvider":"firstParty"}))
        return SimpleNamespace(returncode=0,stdout="Creative output",stderr="")
    runtime=cr_claude.Runtime(SimpleNamespace(),bridge,"/fake/claude",invoke)
    result=runtime.run(["/fake/claude","--print","--model","claude-sonnet-5-5","--output-format","text"],input="A fresh stage after upgrading.",text=True,capture_output=True)
    assert result.stdout=="Creative output"
    assert studio.native_progress(native.owner,native.project["id"])["stages"][0]["status"]=="complete"


def test_general_opus_choice_does_not_change_source_role_marker(tmp_path,monkeypatch):
    engine=SimpleNamespace(CC_MODEL="sonnet")
    bridge=SimpleNamespace(load_settings=lambda:{"general_model":cr_claude.VISUAL_MODEL,"visual_model":cr_claude.GENERAL_MODEL})
    monkeypatch.setattr(cr_claude,"find_executable",lambda:None)
    cr_claude.install(engine,bridge)
    assert engine.CC_MODEL=="sonnet" and engine.CLAUDE_PROVIDER=="claudecode"
    assert engine._claude_endpoint("anthropic") is None


@pytest.mark.parametrize("changed_account", [False, True])
def test_actual_native_execution_binds_account_but_cached_reuse_never_restamps(tmp_path, monkeypatch, changed_account):
    from pathlib import Path
    (tmp_path / "cr-release.json").write_text(json.dumps({"version": studio.VERSION,
        "workflow_version": studio.WORKFLOW_VERSION, "source_sha256": studio.SOURCE_SHA256}))
    cfg = {"brain_mode": "claudecode", "costs_accepted": True,
        "general_model": cr_claude.GENERAL_MODEL, "visual_model": cr_claude.VISUAL_MODEL}
    local = {"ready": True, "account_fingerprint": "a" * 64, "version": "2.1.291",
        "auth_method": "claude.ai", "api_provider": "firstParty"}
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: dict(local))
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (16384, "threed:_cc"))
    backend, calls = {}, []
    def authorize(stage):
        if backend:
            return {"reuse": True, "execute": False, "result_fingerprint": backend["result_fingerprint"]}
        return {"execute": True, "authorization_token": "x" * 32, "selected_model": cr_claude.VISUAL_MODEL}
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: cfg,
        current_project=lambda: {"id": "crv_" + "1" * 24}, authorize_stage=authorize,
        finish_stage=lambda receipt: backend.update(receipt))
    def invoke(args, **kw):
        calls.append(args)
        if changed_account: local["account_fingerprint"] = "b" * 64
        return SimpleNamespace(returncode=0, stdout=json.dumps({"type": "result", "subtype": "success",
            "is_error": False, "result": "Complete source", "usage": {}}), stderr="")
    runtime = cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude", invoke)
    args = ["/fake/claude", "--print", "--model", "opus", "--tools", "Read", "--output-format", "json"]
    result = runtime.run(args, input="Original full scene", text=True, capture_output=True)
    path = next((tmp_path / "output/cr-claude-cache").rglob("*.json"))
    saved = json.loads(path.read_text()); original = path.read_bytes()
    if changed_account:
        assert saved["execution_binding"] is None
    else:
        binding = saved["execution_binding"]
        assert binding["native_account_fingerprint"] == "a" * 64
        assert binding["selected_model"] == cr_claude.VISUAL_MODEL and binding["model_role"] == "visual"
        assert binding["workflow_version"] == studio.WORKFLOW_VERSION
        assert binding["engine_source_sha256"] == studio.SOURCE_SHA256
        assert binding["profile_identity"] == cr_claude._verification_identity(cfg, local, root=tmp_path)
    local["account_fingerprint"] = "c" * 64
    assert runtime.run(args, input="Original full scene", text=True, capture_output=True).stdout == result.stdout
    assert len(calls) == 1 and path.read_bytes() == original


def test_legacy_native_cached_result_never_acquires_current_account_binding(tmp_path, monkeypatch):
    cfg = {"brain_mode": "claudecode", "costs_accepted": True}
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: {"ready": True, "account_fingerprint": "a" * 64})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (1000, "threed:_cc"))
    args = ["/fake/claude", "--print", "--model", "opus", "--output-format", "json"]
    fingerprint = cr_claude._digest({"args": args[1:], "input": "Saved stage", "max_tokens": 1000, "read_files": []})
    path = tmp_path / "output/cr-claude-cache" / ("crv_" + "1" * 24) / ("cc_" + fingerprint + ".json")
    cr_claude._private_write(path, {"state": "complete", "stdout": "legacy complete",
        "result_fingerprint": "b" * 64, "input_fingerprint": fingerprint})
    original = path.read_bytes()
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: cfg,
        current_project=lambda: {"id": "crv_" + "1" * 24},
        authorize_stage=lambda s: {"reuse": True, "execute": False, "result_fingerprint": "b" * 64})
    runtime = cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude",
        lambda *a, **kw: pytest.fail("Cached results cannot execute or restamp"))
    assert runtime.run(args, input="Saved stage", text=True).stdout == "legacy complete"
    assert path.read_bytes() == original and "execution_binding" not in json.loads(path.read_text())


@pytest.mark.parametrize("change", ["prompt", "project", "read_file"])
def test_reviewed_stage_guard_rejects_changed_request_before_authorization(tmp_path, monkeypatch, change):
    project = "crv_" + "1" * 24
    reference = tmp_path / "reference.txt"
    reference.write_text("Original private reference")
    prompt = "Read this private reference:\n- " + str(reference)
    args = ["/fake/claude", "--print", "--model", "opus", "--tools", "Read", "--output-format", "json"]
    fingerprint = cr_claude._digest({"args": args[1:], "input": prompt, "max_tokens": 1000,
        "read_files": cr_claude._read_inputs(prompt, args)})
    cfg = {"brain_mode": "claudecode", "costs_accepted": True}
    monkeypatch.setattr(cr_claude, "status", lambda *a, **kw: {"ready": True})
    monkeypatch.setattr(cr_claude, "_source_context", lambda: (1000, "threed:_cc"))
    if change == "prompt": prompt += " Changed"
    elif change == "project": project = "crv_" + "2" * 24
    else: reference.write_text("Changed private reference")
    bridge = SimpleNamespace(HOME=tmp_path, load_settings=lambda: cfg, current_project=lambda: {"id": project},
        authorize_stage=lambda s: pytest.fail("Changed recovery cannot authorize a replacement"))
    runtime = cr_claude.Runtime(SimpleNamespace(), bridge, "/fake/claude",
        lambda *a, **kw: pytest.fail("Changed recovery cannot run Claude"))
    with pytest.raises(cr_claude.ClaudeStageStopped, match="recovered request changed"):
        with cr_claude.expect_stage("crv_" + "1" * 24, "cc_" + fingerprint):
            runtime.run(args, input=prompt, text=True)
    assert cr_claude._expected_stage.get() is None


def test_a_slow_claude_code_never_reads_as_signed_out(monkeypatch):
    """A creator's Windows computer took 51 s to answer Claude Code's sign-in check and the 20 s limit stopped their
    video. Each command now gets CHECK_SECONDS, a confirmed sign-in is reused for a few minutes, and a check that
    times out soon after a confirmed one still counts."""
    import subprocess
    monkeypatch.setitem(__import__("sys").modules, "cr_allowance", SimpleNamespace(status=lambda home, who: {"blocked": False}))
    monkeypatch.setattr(cr_claude, "_status_cache", {"at": 0.0, "exe": "", "result": None})
    clock = {"now": 1000.0}
    monkeypatch.setattr(cr_claude.time, "monotonic", lambda: clock["now"])
    calls, slow = [], {"on": False}
    def run(args, **kw):
        calls.append((args[1:], kw["timeout"]))
        if slow["on"]:
            raise subprocess.TimeoutExpired(args, kw["timeout"])
        return SimpleNamespace(returncode=0, stdout="2.1.291" if "--version" in args else json.dumps(
            {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}))
    monkeypatch.setattr(cr_claude, "_real_run", run)
    assert cr_claude.status("/fake/claude")["ready"] and len(calls) == 2
    assert all(timeout == cr_claude.CHECK_SECONDS >= 60 for _, timeout in calls)
    assert cr_claude.status("/fake/claude")["ready"] and len(calls) == 2, "a confirmed sign-in is reused"
    clock["now"] += cr_claude.STATUS_REUSE_SECONDS + 1
    slow["on"] = True
    again = cr_claude.status("/fake/claude")
    assert again["ready"] and again["allowance"] == {"blocked": False} and len(calls) == 3, "slow, but signed in"
    clock["now"] += cr_claude.STATUS_TRUST_SECONDS
    gone = cr_claude.status("/fake/claude")
    assert not gone["ready"] and "minute and a half" in gone["message"] and "timed_out" not in gone


def test_three_stages_of_one_project_run_at_once_and_a_fourth_waits(native):
    """The 3D engine directs and reviews three sections side by side."""
    for suffix in "abc":
        assert studio.native_authorize(native.owner, stage(native, suffix))["execute"] is True
    with pytest.raises(studio.HTTPException) as waited:
        studio.native_authorize(native.owner, stage(native, "d"))
    assert waited.value.status_code == 429 and waited.value.headers.get("Retry-After") == "5"

