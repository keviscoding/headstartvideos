"""Keep model access checks across reviewed adapter upgrades without paid probes."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]/"local_runtime"))
import cr_claude
import cr_studio


SOURCE = "e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf"
WORKFLOW = "frontier-5.3-claudecode.1"


@pytest.fixture
def verified(tmp_path, monkeypatch):
    cfg = {"brain_mode":"claudecode", "general_model":cr_claude.GENERAL_MODEL,
           "visual_model":cr_claude.VISUAL_MODEL, "local_version":"5.3-admin.9", "costs_accepted":True}
    local = {"installed":True, "authenticated":True, "ready":True, "version":"2.1.291",
             "auth_method":"claude.ai", "api_provider":"firstParty", "account_fingerprint":"a"*64}
    release = {"version":"5.3-admin.14", "workflow_version":WORKFLOW, "source_sha256":SOURCE}
    (tmp_path/"cr-release.json").write_text(json.dumps(release))
    monkeypatch.setattr(cr_claude, "HOME", tmp_path)
    monkeypatch.setattr(cr_claude.time, "time", lambda: 200000.0)
    monkeypatch.setattr(cr_claude, "status", lambda: dict(local))
    return SimpleNamespace(root=tmp_path, cfg=cfg, local=local, release=release,
                           marker=tmp_path/".cr-claude-verified.json")


def legacy_marker(v, *, age=60, cfg=None):
    cfg = cfg or v.cfg
    identity = cr_claude._digest({"general":cfg.get("general_model"), "visual":cfg.get("visual_model"),
        "version":cfg.get("local_version"), "claude_version":v.local.get("version"),
        "auth":v.local.get("auth_method"), "provider":v.local.get("api_provider"),
        "account":v.local.get("account_fingerprint")})
    v.marker.write_text(json.dumps({"identity":identity, "verified_at":200000-age}))


def current_marker(v):
    v.marker.write_text(json.dumps({"schema_version":2,
        "identity":cr_claude._verification_identity(v.cfg,v.local), "verified_at":199940}))


def test_new_model_identity_tracks_engine_contract_instead_of_adapter_version(verified):
    v = verified
    current_marker(v)
    upgraded = {**v.cfg, "local_version":"5.3-admin.14"}
    assert cr_claude.models_verified(upgraded,v.local)
    assert cr_claude._verification_identity(v.cfg,v.local) == cr_claude._verification_identity(upgraded,v.local)
    (v.root/"cr-release.json").write_text(json.dumps({**v.release,"version":"5.3-admin.15"}))
    assert cr_claude.models_verified(upgraded,v.local)


@pytest.mark.parametrize("location,field,value", [
    ("cfg","general_model",cr_claude.VISUAL_MODEL),
    ("cfg","visual_model",cr_claude.GENERAL_MODEL),
    ("cfg","brain_mode","channelrecipe"),
    ("local","account_fingerprint","b"*64),
    ("local","version","2.1.292"),
    ("local","auth_method","api_key"),
    ("local","api_provider","other"),
    ("local","ready",False),
    ("release","workflow_version","frontier-5.4-claudecode.1"),
    ("release","source_sha256","f"*64),
])
def test_model_account_cli_and_workflow_changes_invalidate_verification(verified,location,field,value):
    v = verified
    current_marker(v)
    getattr(v,location)[field] = value
    (v.root/"cr-release.json").write_text(json.dumps(v.release))
    assert not cr_claude.models_verified(v.cfg,v.local)


def test_legacy_migration_keeps_check_age_and_requires_exact_saved_profile(verified):
    v = verified
    legacy_marker(v)
    before = v.marker.read_text()
    assert cr_claude.models_verified(v.cfg,v.local)
    assert v.marker.read_text() == before  # Status stays read-only.
    assert not cr_claude.models_verified({**v.cfg,"local_version":"5.3-admin.13"},v.local)
    assert cr_claude.preserve_model_verification(v.cfg,v.local)
    migrated = json.loads(v.marker.read_text())
    assert migrated["schema_version"] == 2 and migrated["verified_at"] == 199940
    assert migrated["workflow"] == {"workflow":WORKFLOW,"source":SOURCE}
    assert cr_claude.models_verified({**v.cfg,"local_version":"5.3-admin.14"},v.local)
    if os.name != "nt":                         # POSIX permission bits; Windows keeps the file private by its ACL
        assert v.marker.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("kind", ["expired","future_timestamp","unknown_adapter","changed_workflow","changed_source","missing_manifest"])
def test_legacy_checks_cannot_migrate_outside_reviewed_contract(verified,kind):
    v = verified
    if kind == "unknown_adapter":
        v.cfg["local_version"] = "5.3-admin.15"
    legacy_marker(v,age=86400 if kind=="expired" else -60 if kind=="future_timestamp" else 60)
    if kind == "changed_workflow":
        v.release["workflow_version"] = "different-original-process"
    if kind == "changed_source":
        v.release["source_sha256"] = "f"*64
    (v.root/"cr-release.json").write_text(json.dumps(v.release))
    if kind == "missing_manifest":
        (v.root/"cr-release.json").unlink()
    before = v.marker.read_text()
    assert not cr_claude.models_verified(v.cfg,v.local)
    assert not cr_claude.preserve_model_verification(v.cfg,v.local)
    assert v.marker.read_text() == before


def test_verify_reuses_recent_legacy_check_without_model_requests(verified,monkeypatch):
    v = verified
    legacy_marker(v)
    monkeypatch.setattr(cr_claude.subprocess,"run",lambda *a,**k:pytest.fail("A compatible update must not request paid probes"))
    bridge = SimpleNamespace(HOME=v.root,load_settings=lambda:dict(v.cfg),
                             begin_project=lambda *a,**k:pytest.fail("A reused check must not create a probe project"))
    result = cr_claude.verify_models(SimpleNamespace(),bridge,{"local_version":"5.3-admin.14"})
    assert result["verified"] and result["reused"]
    assert json.loads(v.marker.read_text())["verified_at"] == 199940


@pytest.mark.parametrize("change", ["adapter","models","account","mode"])
def test_probe_completion_uses_current_selected_profile_without_stale_identity(verified,monkeypatch,change):
    v = verified
    calls = []
    setup = []
    finished = []
    bridge = SimpleNamespace(HOME=v.root,load_settings=lambda:dict(v.cfg),_project=None,
                             finish_project=lambda receipt:finished.append(receipt))
    def begin(*args,**kwargs):
        setup.append(kwargs["proof"])
        bridge._project = {"id":"crv_"+"1"*24}
        return bridge._project
    bridge.begin_project = begin
    def install(*args):
        v.cfg["local_version"] = "5.3-admin.14"
    monkeypatch.setattr(cr_claude,"install",install)
    monkeypatch.setattr(cr_claude,"find_executable",lambda:"/fake/claude")
    def run(args,**kwargs):
        calls.append(args)
        if len(calls) == 2 and change == "models":
            v.cfg["general_model"] = cr_claude.VISUAL_MODEL
        if len(calls) == 2 and change == "account":
            v.local["account_fingerprint"] = "b"*64
        if len(calls) == 2 and change == "mode":
            v.cfg["brain_mode"] = "channelrecipe"
        return SimpleNamespace(stdout=json.dumps({"type":"result","subtype":"success","result":"OK"}))
    monkeypatch.setattr(cr_claude.subprocess,"run",run)
    if change == "adapter":
        result = cr_claude.verify_models(SimpleNamespace(),bridge,{"local_version":"5.3-admin.14"})
        assert result["verified"] and not result["reused"]
        assert json.loads(v.marker.read_text())["identity"] == cr_claude._verification_identity(v.cfg,v.local)
        assert cr_claude.models_verified(v.cfg,v.local)
        assert not result["completion_pending"] and len(finished) == 1
    else:
        with pytest.raises(RuntimeError,match="changed during these checks"):
            cr_claude.verify_models(SimpleNamespace(),bridge,{"local_version":"5.3-admin.14"})
        assert not v.marker.exists()
        assert not finished
    assert len(calls) == 2 and bridge._project is None
    assert setup[0]["general_model"] == cr_claude.GENERAL_MODEL
    assert setup[0]["visual_model"] == cr_claude.VISUAL_MODEL
    assert json.loads((v.root/".cr-claude-probe.json").read_text())["complete"]


@pytest.mark.parametrize("legacy",[False,True])
def test_completed_setup_report_and_lost_receipt_recover_without_more_model_calls(verified,monkeypatch,legacy):
    v = verified
    project_id = "crv_"+"1"*24
    calls = []
    delivered = []
    bridge = SimpleNamespace(HOME=v.root,load_settings=lambda:dict(v.cfg),_project=None)
    def finish(receipt):
        delivered.append(receipt)
        if len(delivered) == 1:
            raise OSError("Simulated completion connection loss")
    bridge.finish_project = finish
    def begin(*args,**kwargs):
        assert not legacy
        bridge._project = {"id":project_id}
        return bridge._project
    bridge.begin_project = begin
    monkeypatch.setattr(cr_claude,"install",lambda *a:None)
    monkeypatch.setattr(cr_claude,"find_executable",lambda:"/fake/claude")
    def run(args,**kwargs):
        assert not legacy, "A valid legacy verification must not run paid probes"
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({"type":"result","subtype":"success","result":"OK"}))
    monkeypatch.setattr(cr_claude.subprocess,"run",run)
    probe_path = v.root/".cr-claude-probe.json"
    if legacy:
        legacy_marker(v)
        probe_path.write_text(json.dumps({"identity":json.loads(v.marker.read_text())["identity"],
                                         "project_id":project_id,"complete":True}))
    first = cr_claude.verify_models(SimpleNamespace(),bridge,{"local_version":"5.3-admin.14"})
    assert first["verified"] and first["reused"] is legacy and first["completion_pending"]
    assert cr_claude.models_verified(v.cfg,v.local)
    pending = json.loads(probe_path.read_text())
    assert pending["complete"] and pending["finish_receipt"] == delivered[0]
    assert pending["identity"] == cr_claude._verification_identity(v.cfg,v.local)
    report_dir = v.root/"output/cr-claude-checks"/project_id
    report_path = report_dir/"model-verification.json"
    report = json.loads(report_path.read_text())
    assert set(report) == {"schema_version","general_model","visual_model","workflow","verified_at"}
    assert report["general_model"] == cr_claude.GENERAL_MODEL
    assert report["visual_model"] == cr_claude.VISUAL_MODEL
    assert report["workflow"] == {"workflow":WORKFLOW,"source":SOURCE}
    assert report["verified_at"] == (199940 if legacy else 200000)
    assert v.local["account_fingerprint"] not in report_path.read_text()
    expected_receipt = {"project_id":project_id,"status":"done","error_code":"","artifacts":[{
        "name":"model-verification.json","sha256":hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "media_type":"application/json"}]}
    assert delivered[0] == expected_receipt
    assert json.loads((report_dir/".cr-finish-pending.json").read_text()) == expected_receipt
    if os.name != "nt":                         # POSIX permission bits; Windows keeps the file private by its ACL
        assert report_path.stat().st_mode & 0o077 == 0
    second = cr_claude.verify_models(SimpleNamespace(),bridge,{"local_version":"5.3-admin.14"})
    assert second["verified"] and second["reused"] and not second["completion_pending"]
    assert len(calls) == (0 if legacy else 2)
    assert delivered == [expected_receipt,expected_receipt]
    complete = json.loads(probe_path.read_text())
    assert complete["completion_saved"] and "finish_receipt" not in complete
    assert not (report_dir/".cr-finish-pending.json").exists()
    assert json.loads((report_dir/".cr-finish.json").read_text()) == expected_receipt
    assert cr_claude.verify_models(SimpleNamespace(),bridge,{})["reused"]
    assert len(delivered) == 2 and len(calls) == (0 if legacy else 2)


def test_unrelated_or_unfinished_probe_does_not_close_another_project(verified,monkeypatch):
    v = verified
    current_marker(v)
    bridge = SimpleNamespace(HOME=v.root,load_settings=lambda:dict(v.cfg),
        finish_project=lambda payload:pytest.fail("Only this verification's completed project may close"))
    monkeypatch.setattr(cr_claude.subprocess,"run",lambda *a,**k:pytest.fail("Saved models must not be probed again"))
    for probe in ({"identity":"different-check","complete":True,"project_id":"crv_"+"1"*24},
                  {"identity":cr_claude._verification_identity(v.cfg,v.local),"complete":False,"project_id":"crv_"+"1"*24}):
        (v.root/".cr-claude-probe.json").write_text(json.dumps(probe))
        result = cr_claude.verify_models(SimpleNamespace(),bridge,{})
        assert result["verified"] and result["reused"] and not result["completion_pending"]


def test_verification_report_finishes_actual_owned_setup_project_over_mcp(verified,monkeypatch):
    from webapp import frontier_studio as studio,mcp_server
    v = verified
    monkeypatch.setattr(studio.db,"IS_PG",False)
    monkeypatch.setattr(studio.db,"DB_PATH",v.root/"setup-report.db")
    monkeypatch.setattr(studio.config,"ADMIN_EMAILS",["owner@example.com"])
    monkeypatch.setattr(studio,"release_ready",lambda:True)
    monkeypatch.setattr(studio.providers,"generate",lambda *a,**k:pytest.fail("Setup reports must not call a model API"))
    owner = {"id":1,"email":"owner@example.com"}
    monkeypatch.setattr(mcp_server,"_user_from_request",lambda ctx:owner)
    proof = {"local_version":studio.VERSION,"general_model":cr_claude.GENERAL_MODEL,
        "visual_model":cr_claude.VISUAL_MODEL,"claude_installed":True,"claude_authenticated":True,
        "api_provider":"firstParty","ffmpeg_ready":True,"browser_ready":True,"node_ready":True,
        "costs_accepted":True,"models_verified":False,"purpose":"setup"}
    project = studio.native_begin(owner,studio.NativeBegin(begin_id="finish_setup_checks",
        title="Claude model connection checks",style="custom",setup=proof,max_calls=4))["project"]
    for role,character in (("general","a"),("visual","b")):
        request = studio.NativeAuthorize(project_id=project["id"],stage_id="cc_"+character*64,
            input_fingerprint=character*64,model_role=role,requested_model=cr_claude.GENERAL_MODEL,
            local_version=studio.VERSION)
        approval = studio.native_authorize(owner,request)
        studio.native_finish_stage(owner,studio.NativeFinishStage(project_id=project["id"],
            stage_id=request.stage_id,authorization_token=approval["authorization_token"],
            status="complete",result_fingerprint=character*64,usage={"input_tokens":1,"output_tokens":1}))
    assert studio.native_progress(owner,project["id"])["project"]["status"] == "ready"
    receipts = []
    def finish(payload):
        result = json.loads(mcp_server.local_studio_finish_project(payload=payload))
        assert result["saved"]
        receipts.append((payload,result))
    bridge = SimpleNamespace(HOME=v.root,finish_project=finish)
    verification = {"identity":cr_claude._verification_identity(v.cfg,v.local),
                    "workflow":{"workflow":WORKFLOW,"source":SOURCE},"verified_at":200000}
    probe = {"identity":verification["identity"],"project_id":project["id"],"complete":True}
    assert cr_claude._finish_model_verification(bridge,probe,verification,v.cfg)
    saved = studio.native_progress(owner,project["id"])
    assert saved["project"]["status"] == "done" and saved["project"]["calls"] == 2
    assert saved["artifacts"] == [{**receipts[0][0]["artifacts"][0],
                                   "duration_seconds":None,"width":None,"height":None}]
    assert not studio._native_begin_result(owner,studio._native_session(owner,project["id"])[1],"mac")["launch_required"]
    finish(receipts[0][0])
    assert receipts[-1][1]["reused"]
    assert studio.native_progress(owner,project["id"])["project"]["calls"] == 2


@pytest.mark.parametrize("fails",[False,True])
def test_tiny_probes_use_empty_neutral_directory_and_clean_it_after_success_or_failure(verified,monkeypatch,fails):
    v = verified
    (v.root/"CLAUDE.md").write_text("Large interactive creative instructions must not enter tiny checks.")
    directories = []
    bridge = SimpleNamespace(HOME=v.root,load_settings=lambda:dict(v.cfg),_project=None,
        begin_project=lambda *a,**k:{"id":"crv_"+"1"*24},finish_project=lambda receipt:None)
    monkeypatch.setattr(cr_claude,"install",lambda *a:None)
    monkeypatch.setattr(cr_claude,"find_executable",lambda:"/fake/claude")
    def run(args,**kwargs):
        cwd = Path(kwargs["cwd"])
        directories.append(cwd)
        assert cwd.is_dir() and list(cwd.iterdir()) == []
        assert cwd != v.root and v.root not in cwd.parents
        assert kwargs["input"] == "Connection check: reply exactly OK."
        assert kwargs["timeout"] == 120
        assert args == ["/fake/claude","--print","--model",
            cr_claude.GENERAL_MODEL if len(directories)==1 else cr_claude.VISUAL_MODEL,
            "--output-format","json","--tools","","--strict-mcp-config","--mcp-config",'{"mcpServers":{}}',
            "--max-turns","1","--no-session-persistence"]
        if fails:
            raise cr_claude.ClaudeStageStopped("Simulated interrupted access check")
        return SimpleNamespace(stdout=json.dumps({"type":"result","subtype":"success","result":"OK"}))
    monkeypatch.setattr(cr_claude.subprocess,"run",run)
    if fails:
        with pytest.raises(cr_claude.ClaudeStageStopped):
            cr_claude.verify_models(SimpleNamespace(),bridge,{"local_version":"5.3-admin.14"})
    else:
        assert cr_claude.verify_models(SimpleNamespace(),bridge,{"local_version":"5.3-admin.14"})["verified"]
        assert len(directories)==2 and directories[0]==directories[1]
    assert directories and all(not directory.exists() for directory in directories)


def test_neutral_cwd_does_not_change_existing_probe_stage_cache_or_authorization(verified,monkeypatch):
    v = verified
    project = {"id":"crv_"+"1"*24}
    completed = {}
    stages = []
    generated = []
    bridge = SimpleNamespace(HOME=v.root,load_settings=lambda:dict(v.cfg),current_project=lambda:project)
    def authorize(stage):
        stages.append(stage)
        if stage["stage_id"] in completed:
            return {"execute":False,"reuse":True,"result_fingerprint":completed[stage["stage_id"]]}
        return {"execute":True,"reuse":False,"authorization_token":"x"*32,"selected_model":cr_claude.GENERAL_MODEL}
    bridge.authorize_stage = authorize
    bridge.finish_stage = lambda receipt:completed.update({receipt["stage_id"]:receipt["result_fingerprint"]})
    monkeypatch.setattr(cr_claude,"status",lambda *a,**k:dict(v.local))
    monkeypatch.setattr(cr_claude,"_source_context",lambda:(None,"model-check"))
    def run(args,**kwargs):
        generated.append(kwargs)
        return SimpleNamespace(returncode=0,stderr="",stdout=json.dumps({"type":"result","subtype":"success","result":"OK"}))
    runtime = cr_claude.Runtime(SimpleNamespace(),bridge,"/fake/claude",run)
    command = ["/fake/claude","--print","--model",cr_claude.GENERAL_MODEL,"--output-format","json",
        "--tools","","--strict-mcp-config","--mcp-config",'{"mcpServers":{}}',"--max-turns","1","--no-session-persistence"]
    kwargs = {"input":"Connection check: reply exactly OK.","capture_output":True,"text":True,"timeout":120}
    first = runtime.run(command,**kwargs)
    cwd = v.root/"empty-neutral-directory"
    cwd.mkdir()
    second = runtime.run(command,cwd=str(cwd),**kwargs)
    assert second.stdout == first.stdout and len(generated) == 1
    assert stages[0]["stage_id"] == stages[1]["stage_id"]
    assert stages[0]["input_fingerprint"] == stages[1]["input_fingerprint"]


@pytest.fixture
def setup_client(verified,monkeypatch):
    from flask import Flask, jsonify
    v = verified
    legacy_marker(v)
    v.cfg["key"] = "cr_mcp_fixture_key"
    original = SimpleNamespace(app=Flask(__name__),mv=SimpleNamespace(
        prompts=SimpleNamespace(PROMPT_SETS={"history":{}}),STYLE_INFO={}),
        styles=SimpleNamespace(STYLES={},load_all=lambda:None),RUNNING={},QUEUE=[],
        features=SimpleNamespace(KEYS=[],EXPLICIT=[],BY_KEY={},resolve=lambda info,asked:{},
                                 capabilities=lambda:{"features":{}}),_run_spec=lambda spec:None)
    original.app.add_url_rule("/", "original_page", lambda: "Original engine page")
    original.app.add_url_rule("/api/channels", "original_channels", lambda: jsonify(
        channels=[], features=[], caps={}, prices={}, dlc={}))
    monkeypatch.setitem(sys.modules,"app",original)
    monkeypatch.setattr(cr_studio,"HOME",v.root)
    monkeypatch.setattr(cr_studio,"configure_video_tools",lambda:None)
    monkeypatch.setattr(cr_studio,"video_tools_ready",lambda:True)
    monkeypatch.setattr(cr_studio,"tool_ready",lambda *a:True)
    monkeypatch.setattr(cr_studio,"graphics_ready",lambda:{"browser":True,"webgl":True})
    monkeypatch.setattr(cr_studio.cr_bridge,"load_settings",lambda:dict(v.cfg))
    monkeypatch.setattr(cr_studio.cr_bridge,"save_settings",lambda cfg:v.cfg.update(cfg))
    monkeypatch.setattr(cr_studio.cr_bridge,"call",lambda *a,**k:{"version":"5.3-admin.14"})
    monkeypatch.setattr(cr_studio.cr_bridge,"prepare_runtime",lambda proof:{"workflow":{}})
    monkeypatch.setattr(cr_studio.cr_bridge,"install",lambda *a:None)
    for module in (cr_studio.cr_capabilities,cr_studio.cr_media,cr_studio.cr_render,cr_studio.cr_workers):
        monkeypatch.setattr(module,"install",lambda *a:None)
    monkeypatch.setattr(cr_studio.cr_media,"profile",lambda *a:{})
    monkeypatch.setattr(cr_studio.cr_media,"active",lambda *a:False)
    monkeypatch.setattr(cr_studio.cr_workers,"busy",lambda:False)
    monkeypatch.setattr(cr_studio.cr_mcp,"status",lambda *a:{"connected":True})
    monkeypatch.setattr(cr_studio.cr_mcp,"is_registered",lambda *a:True)
    monkeypatch.setattr(cr_studio.cr_submission,"install",lambda *a:None)
    client = cr_studio.create_app().app.test_client()
    token = json.loads((v.root/".cr-runtime.json").read_text())["token"]
    return client,{"Host":"127.0.0.1:7860","X-CR-Local-Token":token}


def test_preflight_keeps_models_verified_after_saving_current_adapter(verified,setup_client):
    client,headers = setup_client
    assert client.get("/cr-status",headers=headers).get_json()["claude_code"]["models_verified"]
    response = client.post("/cr-preflight",headers=headers,json={"style":"history","steps":["script"]})
    assert response.status_code == 200 and response.get_json()["ready"]
    assert verified.cfg["local_version"] == "5.3-admin.14"
    after = client.get("/cr-status",headers=headers).get_json()
    assert after["claude_code"]["models_verified"] and after["readiness"]["ready"]
    assert json.loads(verified.marker.read_text())["verified_at"] == 199940


@pytest.mark.parametrize("changed",[False,True])
def test_profile_selection_preserves_only_the_same_verified_models(verified,setup_client,changed):
    client,headers = setup_client
    response = client.post("/cr-claude/setup",headers=headers,json={"action":"select",
        "general_model":cr_claude.VISUAL_MODEL if changed else cr_claude.GENERAL_MODEL,
        "visual_model":cr_claude.VISUAL_MODEL})
    assert response.status_code == 200
    assert verified.cfg["local_version"] == "5.3-admin.14"
    assert client.get("/cr-status",headers=headers).get_json()["claude_code"]["models_verified"] is not changed


# ── Studio 110: Claude Code only; the models' check is never a dead end ─────────────────────────────────────────────
# A Max customer on Windows (2026-10-27) was signed in to Claude Code but never got "Verify both Claude models" to pass,
# so they took setup's API-brain shortcut and three videos stopped on Kie 500s. A check that stopped refused every
# later press ("This stage already started or stopped"), its reason talked about "recovery", and the quick setup's
# own line forgot why it stopped.

def probing(v, monkeypatch, *, progress=None, answer="OK", stop=None):
    """A bridge for verify_models whose saved check is in `progress` (None: ChannelRecipe can't be asked)."""
    begun = []
    bridge = SimpleNamespace(HOME=v.root, load_settings=lambda: dict(v.cfg), _project=None,
                             finish_project=lambda receipt: None)
    def project_progress(project_id):
        if progress is None:
            raise RuntimeError("Could not reach ChannelRecipe.")
        return progress
    bridge.project_progress = project_progress
    def begin(title, style, resume=None, **kwargs):
        begun.append(resume)
        bridge._project = {"id": resume or "crv_" + "2" * 24}
        return bridge._project
    bridge.begin_project = begin
    monkeypatch.setattr(cr_claude, "install", lambda *a: None)
    monkeypatch.setattr(cr_claude, "find_executable", lambda: "/fake/claude")
    def run(args, **kwargs):
        if stop:
            raise cr_claude.ClaudeStageStopped(stop)
        return SimpleNamespace(stdout=json.dumps({"type": "result", "subtype": "success", "result": answer}))
    monkeypatch.setattr(cr_claude.subprocess, "run", run)
    return bridge, begun


@pytest.mark.parametrize("stages,carried_on", [
    ([{"status": "failed"}], False),                  # stopped: the press is the yes, a new check runs
    ([{"status": "authorized"}], False),              # a Studio closed mid-check: never answered, a new check
    ([{"status": "complete"}, {"status": "retry_allowed"}], True),   # an approved retry runs in its own check
    ([], True),                                       # begun, nothing asked yet
    (None, False),                                    # ChannelRecipe can't say: a new check
])
def test_a_stopped_model_check_never_blocks_the_next_press_of_verify(verified, monkeypatch, stages, carried_on):
    v = verified
    old = "crv_" + "1" * 24
    identity = cr_claude._verification_identity(v.cfg, v.local)
    (v.root / ".cr-claude-probe.json").write_text(json.dumps({"identity": identity, "project_id": old}))
    progress = None if stages is None else {"project": {"status": "running"}, "stages": stages}
    bridge, begun = probing(v, monkeypatch, progress=progress)
    assert cr_claude.verify_models(SimpleNamespace(), bridge, {"local_version": "5.3-admin.14"})["verified"]
    assert begun == [old if carried_on else None]
    assert cr_claude.models_verified(v.cfg, v.local)


@pytest.mark.parametrize("answer", ["OK", "OK.", "ok", "**OK**", "\"OK\"\n"])
def test_the_check_accepts_ok_however_claude_dresses_it(verified, monkeypatch, answer):
    bridge, _ = probing(verified, monkeypatch, answer=answer)
    assert cr_claude.verify_models(SimpleNamespace(), bridge, {"local_version": "5.3-admin.14"})["verified"]


@pytest.mark.parametrize("stop,said", [
    ("Your Claude account reached its usage limit. Check its reset or usage allowance, then review this saved stage in "
     "setup. It will not run again automatically.", "usage limit"),
    ("Claude took too long to finish this stage. Check your account usage and saved work in setup before resuming. It "
     "will not run again automatically.", "more than two minutes"),
    ("ChannelRecipe stopped this stage: This stage already started or stopped. Review its local cache and Claude usage; "
     "it will not run again automatically. No new Claude request was made.", "stopped part-way"),
    ("ChannelRecipe stopped this stage: This project reached its stage request ceiling. No new Claude request was "
     "made.", "ChannelRecipe stopped the model check: This project reached its stage request ceiling."),
    ("Claude could not finish this stage. Its diagnostic is saved privately on this computer. Review recovery in "
     "ChannelRecipe. It will not run again automatically.", "Copy diagnostics"),
    ("Claude Code needs you to sign in again. Reconnect your Claude account, then review this saved stage in setup.",
     "Open the sign-in window"),
])
def test_a_stopped_check_says_why_in_plain_words_and_what_to_press(verified, monkeypatch, stop, said):
    bridge, _ = probing(verified, monkeypatch, stop=stop)
    with pytest.raises(cr_claude.ClaudeStageStopped) as stopped:
        cr_claude.verify_models(SimpleNamespace(), bridge, {"local_version": "5.3-admin.14"})
    text = str(stopped.value)
    assert said in text and "verify" in text.lower()
    assert "recovery" not in text.lower() and "resum" not in text.lower() and "review" not in text.lower()
    assert not cr_claude.models_verified(verified.cfg, verified.local)


def test_an_old_claude_code_is_told_to_update_not_to_sign_in_again():
    run = lambda args, **kw: SimpleNamespace(returncode=0, stdout="2.1.100 (Claude Code)", stderr="")
    found = cr_claude.status("/fake/claude", run=run, environment={})
    assert found["outdated"] and not found["authenticated"] and "claude update" in found["message"]


def test_an_api_route_customer_is_ready_to_switch_only_with_claude_code_set_up_and_verified(verified):
    v = verified
    current_marker(v)                                     # a check on the Claude Code route, under a day old
    api = {**v.cfg, "brain_mode": "channelrecipe"}
    local = {**v.local, "mcp_connected": True}
    assert cr_claude.ready_to_switch(api, local)
    assert not cr_claude.models_verified(api, local)      # the API route itself never counts as verified
    assert not cr_claude.ready_to_switch(v.cfg, local)    # already on Claude Code
    assert not cr_claude.ready_to_switch(api, {**local, "mcp_connected": False})
    assert not cr_claude.ready_to_switch(api, {**local, "ready": False})
    assert not cr_claude.ready_to_switch({k: x for k, x in api.items() if k != "visual_model"}, local)
    v.marker.unlink()
    assert not cr_claude.ready_to_switch(api, local)


def test_switch_moves_an_api_route_customer_to_claude_code_and_checks_the_models(verified, setup_client, monkeypatch):
    client, headers = setup_client
    v = verified
    v.cfg.update(brain_mode="channelrecipe")
    for key in ("general_model", "visual_model"):
        v.cfg.pop(key)
    status = client.get("/cr-status", headers=headers).get_json()
    assert status["brain_mode"] == "channelrecipe" and "Switch to Claude Code" in status["api_route"]["message"]
    assert not status["api_route"]["ready_to_switch"]
    assert "Old API route" in status["readiness"]["checks"][-1]["label"]
    checks = []
    monkeypatch.setattr(cr_claude, "verify_models", lambda *a: checks.append(a) or {"verified": True})
    answer = client.post("/cr-claude/setup", headers=headers, json={"action": "switch"})
    assert answer.status_code == 200, answer.get_json()
    result = answer.get_json()
    assert result["switched"] and result["verified"] and len(checks) == 1
    assert result["message"].startswith("Switched: Studio now makes your videos with Claude Code")
    assert "stays saved" in result["message"]
    assert v.cfg["brain_mode"] == "claudecode" and v.cfg["switched_to_claude_code_at"]
    assert (v.cfg["general_model"], v.cfg["visual_model"]) == (cr_claude.GENERAL_MODEL, cr_claude.VISUAL_MODEL)
    assert v.cfg["local_version"] == "5.3-admin.14" and "setup_proof" not in v.cfg
    after = client.get("/cr-status", headers=headers).get_json()
    assert after["brain_mode"] == "claudecode" and "api_route" not in after


@pytest.mark.parametrize("local,step", [({"installed": False, "ready": False, "authenticated": False}, "install"),
                                        ({"ready": False, "authenticated": False}, "sign_in"),
                                        ({"ready": False, "authenticated": False, "outdated": True,
                                          "message": "This Claude Code is too old for Studio."}, "update")])
def test_switch_without_claude_code_ready_switches_and_says_the_next_setup_step(verified, setup_client, monkeypatch,
                                                                                local, step):
    client, headers = setup_client
    verified.cfg.update(brain_mode="channelrecipe")
    monkeypatch.setattr(cr_claude, "status", lambda *a, **k: {**verified.local, **local})
    monkeypatch.setattr(cr_claude, "verify_models", lambda *a: pytest.fail("Nothing to check without Claude Code"))
    result = client.post("/cr-claude/setup", headers=headers, json={"action": "switch"}).get_json()
    assert result["switched"] and not result["verified"] and result["next"] == step
    assert verified.cfg["brain_mode"] == "claudecode"
    assert ("too old" in result["message"]) is (step == "update")


def test_a_failed_check_during_the_switch_is_said_and_the_route_stays_switched(verified, setup_client, monkeypatch):
    client, headers = setup_client
    verified.cfg.update(brain_mode="channelrecipe")
    def stopped(*a):
        raise cr_claude.ClaudeStageStopped(cr_claude.check_failure("usage limit"))
    monkeypatch.setattr(cr_claude, "verify_models", stopped)
    answer = client.post("/cr-claude/setup", headers=headers, json={"action": "switch"})
    result = answer.get_json()
    assert answer.status_code == 200 and result["next"] == "verify" and "usage limit" in result["message"]
    assert verified.cfg["brain_mode"] == "claudecode"


def test_verify_is_one_click_it_connects_the_tools_and_saves_the_recommended_models_first(verified, setup_client,
                                                                                       monkeypatch):
    client, headers = setup_client
    v = verified
    for key in ("general_model", "visual_model", "local_version"):
        v.cfg.pop(key)
    registered = []
    monkeypatch.setattr(cr_studio.cr_mcp, "is_registered", lambda *a: bool(registered))
    monkeypatch.setattr(cr_studio.cr_mcp, "register", lambda cfg: registered.append(1) or {"connected": True})
    monkeypatch.setattr(cr_claude, "verify_models", lambda *a: {"verified": True, "message": "Both passed."})
    answer = client.post("/cr-claude/setup", headers=headers, json={"action": "verify"})
    assert answer.status_code == 200 and answer.get_json()["verified"]
    assert registered and v.cfg["general_model"] == cr_claude.GENERAL_MODEL and v.cfg["local_version"] == "5.3-admin.14"


def test_verify_on_the_old_api_route_points_to_the_switch(verified, setup_client, monkeypatch):
    client, headers = setup_client
    verified.cfg.update(brain_mode="channelrecipe")
    monkeypatch.setattr(cr_claude, "verify_models", lambda *a: pytest.fail("The API route is switched first"))
    answer = client.post("/cr-claude/setup", headers=headers, json={"action": "verify"})
    assert answer.status_code == 409 and "Switch to Claude Code (recommended)" in answer.get_json()["error"]


def test_the_api_brain_routes_are_closed_and_a_media_key_is_always_saved(verified, setup_client, monkeypatch):
    import requests
    client, headers = setup_client
    v = verified
    sent = []
    monkeypatch.setattr(cr_studio.cr_bridge, "call", lambda *a, **k: sent.append(a) or {"version": "5.3-admin.14"})
    monkeypatch.delenv("KIE_API_KEY", raising=False)
    brain = client.post("/cr-brain", headers=headers, json={"provider": "kie", "key": "k" * 32,
                                                            "model": cr_claude.GENERAL_MODEL})
    assert brain.status_code == 410 and brain.get_json()["error"] == cr_studio.API_BRAIN_CLOSED
    # an older setup page's "also use this account as my API brain": the voice and image key is saved all the same
    checked = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: checked.append(url) or SimpleNamespace())
    monkeypatch.setattr(cr_studio.cr_media, "_data", lambda response, provider: {"data": 12})
    hub = client.post("/cr-hub", headers=headers, json={"provider": "kie", "key": "k" * 32,
                                                        "tasks": ["narration", "images"], "use_for_brain": True,
                                                        "model": cr_claude.GENERAL_MODEL})
    assert hub.status_code == 200, hub.get_json()
    assert hub.get_json()["saved"] and cr_studio.API_BRAIN_CLOSED in hub.get_json()["message"]
    assert checked == ["https://api.kie.ai/api/v1/chat/credit"]
    assert os.environ["KIE_API_KEY"] == "k" * 32 and v.cfg["media_hub"]["provider"] == "kie"
    assert v.cfg["brain_mode"] == "claudecode"
    assert not [call for call in sent if call and call[0] == "brain-key"], "no API brain key reaches ChannelRecipe"
