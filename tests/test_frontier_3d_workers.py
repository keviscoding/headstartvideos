"""Original QA/redo execute once in owned sessions; receipts carry no prompts."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"local_runtime"))
import cr_3d
import cr_claude
import cr_worker
import cr_workers

SOURCE="crv_"+"1"*24
OPERATION="crv_"+"2"*24


@pytest.fixture
def saved(tmp_path,monkeypatch):
    job=tmp_path/"output/3d/_work/a job with spaces"
    (job/"threed/final").mkdir(parents=True)
    (job/"cr-project.json").write_text(json.dumps({"id":SOURCE,"brain_mode":"claudecode"}))
    (job/"title.txt").write_text("Saved example")
    (job/"style.txt").write_text("3d")
    (job/"threed/board_final.json").write_text('{"moments":[{"id":0,"scenes":[{"id":"scene"}]}]}')
    (job/"threed/final/scene.mp4").write_bytes(b"original clip")
    calls=[]
    def begin(name,payload):
        calls.append((name,payload))
        return {"project":{"id":OPERATION}}
    bridge=SimpleNamespace(HOME=tmp_path,_project=None,
        project_progress=lambda identifier:{"project":{"id":identifier,"status":"done","title":"Saved example","style":"3d"},"brain_mode":"claudecode"},
        prepare_runtime=lambda proof:calls.append(("prepare",proof)),_mcp_tool=begin,
        finish_project=lambda payload:calls.append(("finish",payload)))
    monkeypatch.setattr(cr_claude,"sync_receipts",lambda _:calls.append(("sync",None)))
    return tmp_path,job,bridge,calls


def qa_report(job):
    (job/"threed/qa").mkdir(exist_ok=True)
    (job/"threed/qa/qa.json").write_text(json.dumps({"flags":{"scene":{"note":"private scene feedback","severity":2}},"failed_calls":[]}))


def test_original_argv_and_priority_survive_authorized_rewrite(tmp_path):
    command=["nice","-n","10",sys.executable,"-u",str(tmp_path/"threed.py"),"redo","a job with spaces","scene", "--note","Show the action clearly"]
    result=cr_workers.rewrite(command,SOURCE,home=tmp_path)
    assert result[:5]==command[:5]
    assert result[5:]==[str(tmp_path/"cr_worker.py"),"--project",SOURCE,"--",*command[5:]]
    with pytest.raises(RuntimeError,match="start this creative work"):
        cr_workers.rewrite([sys.executable,str(tmp_path/"threed.py"),"qa","job"],None,home=tmp_path)
    native=[sys.executable,str(tmp_path/"threed.py"),"check"]
    assert cr_workers.rewrite(native,None,home=tmp_path)==native


@pytest.mark.parametrize("bad",[["run","job"],["qa","job","--force"],["redo","job","--no-verify"],["redo","job","--note"],["redo","job","--min-severity","invalid"]])
def test_only_original_review_and_verified_redo_options_are_accepted(saved,bad):
    home,job,_,_=saved
    bad[1]=str(job)
    with pytest.raises(RuntimeError):cr_3d.validate_operation(home,SOURCE,bad)


def test_foreign_marker_and_outside_output_cannot_launch(saved,tmp_path):
    home,job,bridge,calls=saved
    with pytest.raises(RuntimeError,match="does not belong"):
        cr_3d.run_operation(home,OPERATION,["qa",str(job)],bridge,{},lambda:pytest.fail("engine called"))
    outside=tmp_path/"outside";outside.mkdir()
    with pytest.raises(RuntimeError,match="output folder"):
        cr_3d.validate_operation(home,SOURCE,["qa",str(outside)])
    assert calls==[]


def test_unknown_scenes_and_empty_from_qa_stop_before_owning_a_paid_operation(saved):
    home,job,_,_=saved
    for arguments in (["qa",str(job),"unknown"],["redo",str(job),"unknown"],["redo",str(job),"--from-qa"]):
        with pytest.raises(RuntimeError):cr_3d.validate_operation(home,SOURCE,arguments)
    qa_report(job)
    assert cr_3d.validate_operation(home,SOURCE,["redo",str(job),"--from-qa","--min-severity","2"])==job


def test_complete_parent_gets_separate_session_and_safe_completion(saved):
    home,job,bridge,calls=saved
    marker=(job/"cr-project.json").read_bytes()
    executions=[]
    def execute():
        executions.append(True);assert bridge._project["id"]==OPERATION;qa_report(job);raise SystemExit(0)
    arguments=["qa",str(job),"scene"]
    proof={"brain_mode":"claudecode","purpose":"creative","models_verified":True}
    assert cr_3d.run_operation(home,SOURCE,arguments,bridge,proof,execute)==0
    assert len(executions)==1 and (job/"cr-project.json").read_bytes()==marker
    begun=next(payload for name,payload in calls if name=="local_studio_begin")
    assert begun["setup"]==proof and begun["features"]=={"threed":True}
    finish=next(payload for name,payload in calls if name=="finish")
    assert finish["project_id"]==OPERATION and finish["status"]=="done"
    serialized=json.dumps(finish)
    assert "private scene feedback" not in serialized and str(home) not in serialized
    assert cr_3d.operation_status(job,arguments)["state"]=="done"
    assert cr_3d.run_operation(home,SOURCE,arguments,bridge,{},lambda:pytest.fail("paid duplicate"))==0
    assert len([x for x in calls if x[0]=="local_studio_begin"])==1


def test_incompatible_readiness_or_live_parent_never_starts_engine(saved):
    home,job,bridge,calls=saved
    with pytest.raises(RuntimeError,match="native Claude"):
        cr_3d.run_operation(home,SOURCE,["qa",str(job)],bridge,{},lambda:pytest.fail("engine called"))
    bridge.project_progress=lambda _: {"project":{"id":SOURCE,"status":"running"},"brain_mode":"claudecode"}
    with pytest.raises(RuntimeError,match="Finish the saved video"):
        cr_3d.run_operation(home,SOURCE,["qa",str(job)],bridge,{},lambda:pytest.fail("engine called"))
    assert not any(name=="local_studio_begin" for name,_ in calls)


def test_original_uncertain_stage_blocks_new_execution_even_after_receipt_sync(saved):
    home,job,bridge,calls=saved
    arguments=["redo",str(job),"scene","--note","Improve subject visibility"]
    def stopped():raise cr_claude.ClaudeStageStopped("usage limit")
    with pytest.raises(cr_claude.ClaudeStageStopped):
        cr_3d.run_operation(home,SOURCE,arguments,bridge,{"brain_mode":"claudecode","purpose":"creative"},stopped)
    assert cr_3d.operation_status(job,arguments)["state"]=="failed"
    with pytest.raises(RuntimeError,match="will not execute again automatically"):
        cr_3d.run_operation(home,SOURCE,arguments,bridge,{},lambda:pytest.fail("paid duplicate"))
    assert len([x for x in calls if x[0]=="local_studio_begin"])==1


def test_lost_completion_connection_replays_metadata_without_models(saved):
    home,job,bridge,calls=saved
    arguments=["qa",str(job)]
    bridge.finish_project=lambda _:(_ for _ in ()).throw(RuntimeError("offline"))
    assert cr_3d.run_operation(home,SOURCE,arguments,bridge,{"brain_mode":"claudecode","purpose":"creative"},lambda:qa_report(job))==0
    assert cr_3d.operation_status(job,arguments)["state"]=="pending"
    bridge.finish_project=lambda payload:calls.append(("finish",payload))
    assert cr_3d.reconcile_operation(job,arguments,bridge)["state"]=="done"
    assert cr_3d.run_operation(home,SOURCE,arguments,bridge,{},lambda:pytest.fail("paid duplicate"))==0


def test_prepared_begin_reuses_stable_id_after_network_failure(saved):
    home,job,bridge,calls=saved
    arguments=["qa",str(job)]
    proof={"brain_mode":"claudecode","purpose":"creative"}
    real_begin=bridge._mcp_tool
    ids=[]
    def offline(name,payload):ids.append(payload["begin_id"]);raise RuntimeError("offline")
    bridge._mcp_tool=offline
    with pytest.raises(RuntimeError,match="offline"):
        cr_3d.run_operation(home,SOURCE,arguments,bridge,proof,lambda:pytest.fail("engine called"))
    def online(name,payload):ids.append(payload["begin_id"]);return real_begin(name,payload)
    bridge._mcp_tool=online
    cr_3d.run_operation(home,SOURCE,arguments,bridge,proof,lambda:qa_report(job))
    assert ids[0]==ids[1]


def test_redo_records_original_changed_clips_and_requires_reassembly(saved):
    home,job,bridge,calls=saved
    arguments=["redo",str(job),"scene","--note","Show the subject"]
    def execute():
        (job/"threed/final/scene.mp4").write_bytes(b"original engine revised clip")
        (job/"threed/redo_last.json").write_text(json.dumps({"rendered":["scene"],"kept_old":[],"dir":"private local path"}))
    cr_3d.run_operation(home,SOURCE,arguments,bridge,{"brain_mode":"claudecode","purpose":"creative"},execute)
    assert cr_3d.operation_status(job,arguments)["needs_reassembly"] is True
    cr_3d.run_operation(home,SOURCE,arguments,bridge,{},lambda:pytest.fail("repeat paid redo"))


def test_operation_lock_blocks_concurrent_commands_before_any_new_session(saved):
    home,job,bridge,calls=saved
    with cr_3d._operation_lock(job):
        with pytest.raises(RuntimeError,match="already working"):
            cr_3d.run_operation(home,SOURCE,["qa",str(job)],bridge,{},lambda:pytest.fail("concurrent engine"))
    assert calls==[]


def test_status_is_journal_backed_and_qa_preserves_prior_reassembly_state(saved):
    home,job,bridge,_=saved
    arguments=["qa",str(job)]
    assert cr_3d.operation_status(job,arguments)=={}
    cr_3d.run_operation(home,SOURCE,arguments,bridge,{"brain_mode":"claudecode","purpose":"creative"},lambda:qa_report(job))
    state={"needs_reassembly":True}
    state.update(cr_3d.operation_status(job,arguments))
    assert state["needs_reassembly"] is True


def test_worker_dispatch_preserves_original_command_and_requires_native_setup(saved,monkeypatch):
    home,job,_,_=saved
    import cr_bridge,cr_media,cr_render,cr_studio,cr_readiness
    (home/"cr-release.json").write_text('{"version":"test-release"}')
    workflow={"required_release":"test-release","workflow_version":"same","engine_source_sha256":"same"}
    source={"project":{"id":SOURCE},"workflow":workflow}
    engine=SimpleNamespace()
    monkeypatch.setitem(sys.modules,"make_video",engine)
    monkeypatch.setattr(cr_worker,"HOME",home)
    monkeypatch.setattr(cr_bridge,"load_settings",lambda:{"brain_mode":"claudecode"})
    monkeypatch.setattr(cr_bridge,"project_progress",lambda _:source)
    monkeypatch.setattr(cr_bridge,"call",lambda _: {"production_workflow":workflow})
    for module in (cr_bridge,cr_media,cr_render):monkeypatch.setattr(module,"install",lambda _:None)
    monkeypatch.setattr(cr_workers,"install",lambda:None)
    monkeypatch.setattr(cr_studio,"configure_video_tools",lambda:None)
    monkeypatch.setattr(cr_studio,"graphics_ready",lambda:{"browser":True,"webgl":True})
    monkeypatch.setattr(cr_studio,"video_tools_ready",lambda:True)
    monkeypatch.setattr(cr_studio,"tool_ready",lambda *a:True)
    monkeypatch.setattr(cr_readiness,"basic",lambda _: {"ready":True,"claude_code":{"models_verified":True}})
    monkeypatch.setattr(cr_readiness,"proof",lambda checks,**kw:{"brain_mode":"claudecode","purpose":kw["purpose"]})
    observed=[]
    def operation(home_arg,source_id,arguments,bridge,proof,execute,operation_id=None):
        assert home_arg==home and source_id==SOURCE
        assert proof["purpose"]=="creative"
        observed.append((list(arguments),operation_id))
        with pytest.raises(SystemExit) as result:execute()
        assert result.value.code==0
    monkeypatch.setattr(cr_3d,"run_operation",operation)
    def original(path,arguments):
        assert path==job and arguments==["scene","--note","Keep original art direction"]
        assert sys.argv==[str(home/"threed.py"),"redo",str(job),"scene","--note","Keep original art direction"]
        return 0
    monkeypatch.setitem(sys.modules,"threed",SimpleNamespace(_redo_cli=original,_qa_cli=lambda *args:pytest.fail("wrong original CLI")))
    operation_id="op_"+"a"*24
    monkeypatch.setattr(sys,"argv",["cr_worker.py","--project",SOURCE,"--operation",operation_id,"--",str(home/"threed.py"),"redo",str(job),"scene","--note","Keep original art direction"])
    cr_worker.main()
    assert observed==[(["redo",str(job),"scene","--note","Keep original art direction"],operation_id)]
    monkeypatch.setattr(sys,"argv",["cr_worker.py","--project",SOURCE,"--",str(home/"threed.py"),"qa",str(job)])
    monkeypatch.setattr(cr_readiness,"basic",lambda _: {"ready":False,"blockers":["Claude model verification missing"]})
    with pytest.raises(RuntimeError,match="Claude model verification missing"):cr_worker.main()
    assert len(observed)==1


@pytest.mark.parametrize("operation,code",[("qa",0),("redo",2)])
def test_shared_original_cli_preserves_hooks_and_exit_status(monkeypatch,tmp_path,operation,code):
    observed=[]
    def original(path,arguments):
        observed.append((path,arguments))
        return code
    module=SimpleNamespace(_qa_cli=original,_redo_cli=original)
    monkeypatch.setitem(sys.modules,"threed",module)
    arguments=[operation,str(tmp_path),"scene","--note","specific original feedback"]
    with pytest.raises(SystemExit) as result:cr_worker.run_threed_original(arguments)
    assert result.value.code==code
    assert observed==[(tmp_path,arguments[2:])]


def test_new_explicit_operation_preserves_failed_history_and_has_its_own_session(saved):
    home,job,bridge,calls=saved
    first="op_"+"a"*24
    second="op_"+"b"*24
    arguments=["qa",str(job)]
    proof={"brain_mode":"claudecode","purpose":"creative"}
    created=[]
    def begin(name,payload):
        project="crv_"+str(len(created)+2)*24
        created.append(project);calls.append((name,payload))
        return {"project":{"id":project}}
    bridge._mcp_tool=begin
    cache=home/"output/cr-claude-cache"/OPERATION/"original-failed-stage.json"
    def stopped():
        cache.parent.mkdir(parents=True)
        cache.write_text('{"state":"failed","private_diagnostic":"saved"}')
        raise cr_claude.ClaudeStageStopped("Original paid stage stopped")
    with pytest.raises(cr_claude.ClaudeStageStopped):
        cr_3d.run_operation(home,SOURCE,arguments,bridge,proof,stopped,operation_id=first)
    failed_path=cr_3d._journal(job,arguments,first)
    original_journal=failed_path.read_bytes()
    original_cache=cache.read_bytes()
    assert cr_3d.operation_status(job,arguments,first)["state"]=="failed"
    assert cr_3d.run_operation(home,SOURCE,arguments,bridge,proof,lambda:qa_report(job),operation_id=second)==0
    assert created==[OPERATION,"crv_"+"3"*24]
    assert failed_path.read_bytes()==original_journal and cache.read_bytes()==original_cache
    assert cr_3d.operation_status(job,arguments,second)["state"]=="done"
    with pytest.raises(RuntimeError,match="will not execute again automatically"):
        cr_3d.run_operation(home,SOURCE,arguments,bridge,proof,lambda:pytest.fail("old failed operation replayed"),operation_id=first)
    (job/"threed/final/scene.mp4").write_bytes(b"changed later by another explicitly authorized repair")
    assert cr_3d.run_operation(home,SOURCE,arguments,bridge,{},lambda:pytest.fail("successful operation replayed"),operation_id=second)==0
    assert len(created)==2
    assert failed_path.read_bytes()==original_journal and cache.read_bytes()==original_cache


def test_operation_id_is_bound_to_its_original_arguments_and_format(saved):
    home,job,bridge,_=saved
    arguments=["qa",str(job)]
    operation_id="op_"+"f"*24
    cr_3d.run_operation(home,SOURCE,arguments,bridge,{"brain_mode":"claudecode","purpose":"creative"},lambda:qa_report(job),operation_id=operation_id)
    changed=["qa",str(job),"scene"]
    with pytest.raises(RuntimeError,match="different 3D work"):
        cr_3d.run_operation(home,SOURCE,changed,bridge,{},lambda:pytest.fail("changed operation replay"),operation_id=operation_id)
    with pytest.raises(RuntimeError,match="different 3D work"):
        cr_3d.operation_status(job,changed,operation_id)
    with pytest.raises(RuntimeError,match="valid explicitly created"):
        cr_3d.operation_status(job,arguments,"op_../../bad")


def test_explicit_operation_completion_sync_is_metadata_only(saved):
    home,job,bridge,calls=saved
    arguments=["qa",str(job)]
    operation_id="op_"+"e"*24
    bridge.finish_project=lambda _:(_ for _ in ()).throw(RuntimeError("offline"))
    cr_3d.run_operation(home,SOURCE,arguments,bridge,{"brain_mode":"claudecode","purpose":"creative"},lambda:qa_report(job),operation_id=operation_id)
    assert cr_3d.operation_status(job,arguments,operation_id)["state"]=="pending"
    bridge.finish_project=lambda payload:calls.append(("finish",payload))
    assert cr_3d.reconcile_operation(job,arguments,bridge,operation_id)["state"]=="done"
    assert len([x for x in calls if x[0]=="local_studio_begin"])==1


def test_worker_rejects_invalid_operation_id_before_runtime_initialization(saved,monkeypatch):
    home,job,_,_=saved
    monkeypatch.setattr(sys,"argv",["cr_worker.py","--project",SOURCE,"--operation","invalid","--",str(home/"threed.py"),"qa",str(job)])
    with pytest.raises(SystemExit) as error:cr_worker.main()
    assert error.value.code==2
