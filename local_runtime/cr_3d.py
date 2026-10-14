"""Authorize original 3D QA/redo commands without changing their creative code.

The operation has its own CR session, leaving the completed video's marker and
assets intact. A stopped operation is never automatically executed a second
time; completion receipts can be reconciled without invoking the engine.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time


def _read(path):
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def _digest(path):
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_operation(home,source_project_id,arguments):
    if not re.fullmatch(r"crv_[a-f0-9]{24}",source_project_id or ""):
        raise RuntimeError("Choose an owned saved ChannelRecipe video.")
    if (not isinstance(arguments,(list,tuple)) or len(arguments)<2 or
            any(not isinstance(x,str) for x in arguments) or arguments[0] not in {"qa","redo"}):
        raise RuntimeError("Use the original 3D qa or redo command on a saved video.")
    job=Path(arguments[1]).resolve()
    if not job.is_dir() or not job.is_relative_to((Path(home)/"output").resolve()):
        raise RuntimeError("Choose a saved video in this studio's output folder.")
    marker=_read(job/"cr-project.json")
    if marker.get("id") != source_project_id or marker.get("brain_mode") != "claudecode":
        raise RuntimeError("This saved video does not belong to the selected Claude Code project.")
    if not (job/"threed/board_final.json").is_file():
        raise RuntimeError("Wait for this video's original 3D render to finish before reviewing or repairing it.")
    values=list(arguments[2:])
    selected=[]
    i=0
    while i<len(values):
        value=values[i]
        if value in ({"--failed"} if arguments[0]=="qa" else {"--from-qa"}):
            i+=1
        elif arguments[0]=="redo" and value in {"--note","--min-severity"}:
            if i+1>=len(values) or not values[i+1] or (value=="--min-severity" and not re.fullmatch(r"-?\d+",values[i+1])):
                raise RuntimeError("Supply the original redo option's value.")
            i+=2
        elif re.fullmatch(r"[a-zA-Z0-9_.:-]+",value) and not value.startswith("-"):
            selected.append(value)
            i+=1
        else:
            raise RuntimeError("Use original scene IDs and supported QA/redo options; the final verification stays enabled.")
    board=_read(job/"threed/board_final.json")
    known={str(scene["id"]) for moment in board.get("moments",[]) if isinstance(moment,dict)
           for scene in moment.get("scenes",[]) if isinstance(scene,dict) and scene.get("id")}
    if len(selected)!=len(set(selected)) or any(scene not in known for scene in selected):
        raise RuntimeError("Choose scene IDs from this video's finished original 3D board.")
    if arguments[0]=="redo" and not selected and "--from-qa" not in values:
        raise RuntimeError("Choose finished scenes to repair, or use the original QA flags.")
    if arguments[0]=="redo" and not selected:
        minimum=int(values[values.index("--min-severity")+1]) if "--min-severity" in values else 2
        flags=_read(job/"threed/qa/qa.json").get("flags") or {}
        if not isinstance(flags,dict) or not any(scene in known and isinstance(flag,dict) and
                isinstance(flag.get("severity",2),int) and flag.get("severity",2)>=minimum for scene,flag in flags.items()):
            raise RuntimeError("Inspect the finished scenes first, or select scenes to repair.")
    return job


def _command_fingerprint(arguments):
    return hashlib.sha256(json.dumps([arguments[0],*arguments[2:]],ensure_ascii=False).encode()).hexdigest()


def _journal(job,arguments,operation_id=None):
    if operation_id is not None and not re.fullmatch(r"op_[a-f0-9]{24}",operation_id):
        raise RuntimeError("Use a valid explicitly created 3D operation ID.")
    key=operation_id or _command_fingerprint(arguments)
    return job/"threed/.cr-operations"/(key+".json")


def _matching_command(saved,arguments,operation_id):
    if saved and operation_id is not None and (saved.get("operation_id") != operation_id or
            saved.get("command_fingerprint") != _command_fingerprint(arguments)):
        raise RuntimeError("This operation ID belongs to different 3D work. Explicitly create a new operation instead.")


def _source_fingerprint(job,operation):
    paths=[job/"threed/board_final.json",*sorted((job/"threed/final").glob("*.mp4"))]
    if operation=="redo":
        paths.append(job/"threed/qa/qa.json")
    records=[[str(path.relative_to(job)),_digest(path)] for path in paths if path.is_file()]
    return hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()


def operation_status(job,arguments,operation_id=None):
    """Safe UI metadata: never return local paths, feedback, prompts or keys."""
    job=Path(job).resolve()
    saved=_read(_journal(job,arguments,operation_id))
    if not saved:
        return {}
    _matching_command(saved,arguments,operation_id)
    state=saved.get("state","ready")
    if operation_id is None and state=="complete" and _source_fingerprint(job,arguments[0]) not in {saved.get("before_fingerprint"),saved.get("after_fingerprint")}:
        return {}
    result={"state":{"complete":"done","complete_local":"pending","prepared":"idle"}.get(state,state),"operation_project_id":saved.get("operation_project_id"),
        "reconciliation_pending":bool(saved.get("finish_receipt")),
        "message":("This operation stopped. Review its saved Claude stages before starting any further paid work."
                   if state=="failed" else "The original 3D operation has started. Its saved work will not execute again automatically."
                   if state=="running" else "Your original 3D result is saved locally. Reconnect CR to record its completion."
                   if state=="complete_local" else "Your original 3D operation is saved." if state=="complete" else "Ready for an explicitly requested 3D operation.")}
    if state=="failed":
        result["needs_manual_recovery"]=True
    if saved.get("needs_reassembly"):
        result["needs_reassembly"]=True
    if operation_id is not None:
        result["operation_id"]=operation_id
    return result


@contextmanager
def _operation_lock(job):
    folder=job/"threed/.cr-operations"
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/".runner.lock").open("a+b") as handle:
        try:
            if os.name=="nt":
                import msvcrt
                handle.seek(0);handle.write(b"0");handle.flush();handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another 3D operation is already working on this saved video.") from None
        try:
            yield
        finally:
            if os.name=="nt":
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle,fcntl.LOCK_UN)


def _reconcile(bridge,path,saved):
    import cr_claude
    cr_claude.sync_receipts(bridge)
    if saved.get("finish_receipt"):
        bridge.finish_project(saved["finish_receipt"])
        saved.pop("finish_receipt")
        saved["state"]="complete" if saved["state"]=="complete_local" else saved["state"]
        cr_claude._private_write(path,saved)


def reconcile_operation(job,arguments,bridge,operation_id=None):
    """Sync saved metadata only; this route never runs a model or renderer."""
    job=Path(job).resolve()
    with _operation_lock(job):
        path=_journal(job,arguments,operation_id)
        saved=_read(path)
        _matching_command(saved,arguments,operation_id)
        if saved.get("operation_project_id"):
            bridge.project_progress(saved["operation_project_id"])
            _reconcile(bridge,path,saved)
    return operation_status(job,arguments,operation_id)


def run_operation(home,source_project_id,arguments,bridge,proof,execute,operation_id=None):
    """Run unchanged original argv once, with its own authorized native session."""
    import cr_claude
    job=validate_operation(home,source_project_id,arguments)
    with _operation_lock(job):
        source=bridge.project_progress(source_project_id)
        parent=source.get("project") or {}
        parent_spec=source.get("job_spec") or {}
        if parent.get("id") != source_project_id or source.get("brain_mode") != "claudecode":
            raise RuntimeError("Reconnect the ChannelRecipe account that made this video, then try again.")
        if parent.get("status") != "done":
            raise RuntimeError("Finish the saved video before starting a separate 3D review or repair.")
        path=_journal(job,arguments,operation_id)
        before=_source_fingerprint(job,arguments[0])
        saved=_read(path)
        _matching_command(saved,arguments,operation_id)
        if saved and saved.get("source_project_id") != source_project_id:
            raise RuntimeError("This operation belongs to a different saved video project.")
        if saved.get("state") in {"complete","complete_local"}:
            try:
                _reconcile(bridge,path,saved)
            except Exception:
                print("The original 3D result is saved locally. Reconnect CR to record its completion; it will not execute again.")
                return 0
            if operation_id is not None or before in {saved.get("before_fingerprint"),saved.get("after_fingerprint")}:
                print("The original 3D operation already finished. Its saved result and receipts were reused.")
                return 0
            saved={}
        if saved.get("state") in {"running","failed"}:
            try:_reconcile(bridge,path,saved)
            except Exception:pass
            raise RuntimeError("This 3D operation already started or stopped. Review its saved Claude stages; it will not execute again automatically.")
        if proof.get("brain_mode") != "claudecode" or proof.get("purpose") != "creative":
            raise RuntimeError("Finish native Claude Code setup before reviewing or repairing 3D scenes.")
        bridge.prepare_runtime(proof)
        if saved and saved.get("before_fingerprint") != before:
            raise RuntimeError("The 3D inputs changed while this operation was being prepared. Review it before starting more work.")
        if not saved:
            saved={"state":"prepared","source_project_id":source_project_id,"operation":arguments[0],
                   "before_fingerprint":before,"begin_id":"b_"+secrets.token_hex(16),"created_at":time.time()}
            if operation_id is not None:
                saved.update(operation_id=operation_id,command_fingerprint=_command_fingerprint(arguments))
            cr_claude._private_write(path,saved)
        title=(job/"title.txt").read_text(encoding="utf-8").splitlines()[0].strip()
        style=(job/"style.txt").read_text(encoding="utf-8").strip()
        if title != parent.get("title") or style != parent.get("style"):
            raise RuntimeError("The saved video's title or preset does not match its owned CR project.")
        renderer_options={key: parent_spec[key] for key in ("render_backend", "manimgx_identity") if key in parent_spec}
        result=bridge._mcp_tool("local_studio_begin",{"begin_id":saved["begin_id"],"title":title,"style":style,
            "minutes":1,"features":{"threed":True},"options":renderer_options,"setup":proof,"max_calls":500,
            "platform":"windows" if os.name=="nt" else "mac"})
        operation=result["project"]
        bridge._project={**operation, "job_spec":renderer_options}
        saved.update(state="running",operation_project_id=operation["id"])
        cr_claude._private_write(path,saved)
        report_path=job/("threed/qa/qa.json" if arguments[0]=="qa" else "threed/redo_last.json")
        previous_report=(report_path.stat().st_mtime_ns,_digest(report_path)) if report_path.is_file() else None
        try:
            try:
                execute()
            except SystemExit as error:
                if error.code not in (None,0):raise
            fresh_report=report_path.is_file() and previous_report != (report_path.stat().st_mtime_ns,_digest(report_path))
            if not report_path.is_file() and arguments[0]=="qa":
                raise RuntimeError("The original operation did not save its completion report.")
            report=_read(report_path) if fresh_report or arguments[0]=="qa" else {}
            needs_reassembly=arguments[0]=="redo" and bool(report.get("rendered"))
            summary={"operation":arguments[0],"source_project_id":source_project_id,
                "operation_project_id":operation["id"],"completed_at":time.time(),
                "report_sha256":_digest(report_path) if fresh_report else "","report_updated":bool(fresh_report),
                "needs_reassembly":needs_reassembly,
                "flagged":len(report.get("flags") or {}),"unjudged":len(report.get("failed_calls") or []),
                "rendered":len(report.get("rendered") or []),"kept_old":len(report.get("kept_old") or [])}
            safe_report=path.with_name(path.stem+"-report.json")
            cr_claude._private_write(safe_report,summary)
            saved.update(state="complete_local",after_fingerprint=_source_fingerprint(job,arguments[0]),
                needs_reassembly=needs_reassembly,finish_receipt={"project_id":operation["id"],"status":"done",
                "error_code":"","artifacts":[{"name":"3d-operation-report.json","sha256":_digest(safe_report),"media_type":"application/json"}]})
            cr_claude._private_write(path,saved)
        except BaseException:
            saved.update(state="failed",finish_receipt={"project_id":operation["id"],"status":"failed",
                "artifacts":[],"error_code":"runtime_failed"})
            cr_claude._private_write(path,saved)
            try:_reconcile(bridge,path,saved)
            except Exception:pass
            raise
        try:_reconcile(bridge,path,saved)
        except Exception:
            print("The original 3D result is saved locally. Reconnect CR to record its completion; it will not execute again.")
        return 0
