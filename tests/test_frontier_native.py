"""Production native-Claude authorization, isolation and saved-result recovery."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from webapp import frontier_studio as studio
from local_runtime import cr_claude


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


def test_native_uses_no_api_credentials_and_duplicate_begin_is_idempotent(native):
    assert studio.credential(1) is None
    repeated=studio.native_begin(native.owner,native.begin)
    assert repeated["project_id"]==native.project["id"] and repeated["resumed"]
    assert native.project["id"] in repeated["launch_command"]
    assert native.begin.title not in repeated["launch_command"]
    changed=native.begin.model_copy(update={"title":"Different saved work"})
    with pytest.raises(HTTPException,match="different saved work"):
        studio.native_begin(native.owner,changed)


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


def test_uncertain_or_failed_stage_never_restarts_automatically(native):
    req=stage(native)
    allowed=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=allowed["authorization_token"],status="failed",error_code="timeout"))
    with pytest.raises(HTTPException):studio.native_authorize(native.owner,req)
    assert studio.native_progress(native.owner,req.project_id)["project"]["calls"]==1


def test_final_metadata_cannot_hide_incomplete_stages(native):
    studio.native_authorize(native.owner,stage(native))
    report=studio.NativeFinishProject(project_id=native.project["id"],status="done",
        artifacts=[{"name":"video.mp4","sha256":"e"*64,"media_type":"video/mp4"}])
    with pytest.raises(HTTPException):studio.native_finish_project(native.owner,report)


def test_recovery_requires_acknowledgement_and_only_one_extra_execution(native):
    req=stage(native)
    approval=studio.native_authorize(native.owner,req)
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=approval["authorization_token"],status="failed",error_code="timeout"))
    retry=studio.NativeRetryStage(project_id=req.project_id,stage_id=req.stage_id)
    with pytest.raises(HTTPException):studio.native_retry_stage(native.owner,retry)
    with pytest.raises(HTTPException):studio.native_retry_stage(native.other,retry.model_copy(update={"acknowledge_costs":True}))
    assert studio.native_retry_stage(native.owner,retry.model_copy(update={"acknowledge_costs":True}))["approved"]
    second=studio.native_authorize(native.owner,req)
    assert second["authorization_token"] != approval["authorization_token"]
    studio.native_finish_stage(native.owner,studio.NativeFinishStage(project_id=req.project_id,stage_id=req.stage_id,
        authorization_token=second["authorization_token"],status="failed",error_code="timeout"))
    with pytest.raises(HTTPException):studio.native_retry_stage(native.owner,retry.model_copy(update={"acknowledge_costs":True}))
    progress=studio.native_progress(native.owner,req.project_id)
    assert progress["project"]["calls"]==2 and not progress["stages"][0]["can_retry"]


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
    assert next((tmp_path/"output/cr-claude-cache").rglob("*.json")).stat().st_mode&0o077==0


def test_read_only_claude_auth_never_returns_identity_or_api_credentials():
    def invoke(args,**kw):
        return SimpleNamespace(returncode=0,stdout="2.1.291" if "--version" in args else json.dumps({
            "loggedIn":True,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"pro","email":"private@example.com","apiKey":"secret"}))
    result=cr_claude.status("/fake/claude",run=invoke)
    assert result["ready"] and "private@example.com" not in json.dumps(result) and "secret" not in json.dumps(result)


@pytest.mark.parametrize("message,code",[("You've hit your limit · resets later","usage_limit"),
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


def test_general_opus_choice_does_not_change_source_role_marker(tmp_path,monkeypatch):
    engine=SimpleNamespace(CC_MODEL="sonnet")
    bridge=SimpleNamespace(load_settings=lambda:{"general_model":cr_claude.VISUAL_MODEL,"visual_model":cr_claude.GENERAL_MODEL})
    monkeypatch.setattr(cr_claude,"find_executable",lambda:None)
    cr_claude.install(engine,bridge)
    assert engine.CC_MODEL=="sonnet" and engine.CLAUDE_PROVIDER=="claudecode"
    assert engine._claude_endpoint("anthropic") is None
