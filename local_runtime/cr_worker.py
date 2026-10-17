"""Bootstrap original editor, product, reference and 3D workers with CR."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import re

HOME=Path(__file__).resolve().parent


def run_threed_original(arguments):
    """Use the original CLI dispatch on the shared, compatibility-checked module."""
    import threed
    if arguments[0] not in {"qa", "redo"}:
        raise RuntimeError("Use original 3D inspection or verified repair.")
    original = threed._qa_cli if arguments[0] == "qa" else threed._redo_cli
    raise SystemExit(original(Path(arguments[1]), arguments[2:]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--project",required=True)
    parser.add_argument("--operation")
    parser.add_argument("command",nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if not re.fullmatch(r"crv_[a-f0-9]{24}",args.project):
        parser.error("Use an owned CR project ID.")
    if args.operation is not None and not re.fullmatch(r"op_[a-f0-9]{24}",args.operation):
        parser.error("Use a valid explicitly created 3D operation ID.")
    command=args.command[1:] if args.command[:1]==["--"] else args.command
    import cr_workers,cr_bridge,cr_claude,cr_media,cr_render,cr_studio,cr_readiness
    if not command:parser.error("Use an original studio worker command.")
    target=Path(command[0]).resolve()
    if target.parent != HOME or target.name not in cr_workers.TARGETS:
        parser.error("Use a supported original worker from this studio folder.")
    threed = target.name == "threed.py"
    video = target.name == "app.py"
    if video and (len(command) != 3 or command[1] != "--cr-video" or
                  not re.fullmatch(r"video_[a-f0-9]{24}", command[2])):
        parser.error("Use an explicitly created owned video worker.")
    if video:
        import cr_video_cancel
        cr_video_cancel.guard_parent(HOME,args.project,command[2])
    if args.operation is not None and not threed:
        parser.error("Operation IDs apply only to original 3D inspection or repair.")
    if threed:
        import cr_3d
        cr_3d.validate_operation(HOME,args.project,command[1:])
    cfg=cr_bridge.load_settings()
    mode=cfg.get("brain_mode","channelrecipe")
    if threed and mode != "claudecode":
        raise RuntimeError("Connect and verify your Claude Code models before reviewing or repairing 3D scenes.")
    cr_studio.configure_video_tools()
    os.environ["CLAUDE_PROVIDER"]=os.environ["SCRIPT_PROVIDER"]="claudecode" if mode=="claudecode" else "channelrecipe"
    os.environ["CLAUDE_HANDOFF_DIR"]=""
    import make_video as engine
    cr_bridge.install(engine)
    cr_media.install(engine)
    cr_render.install(engine)
    import cr_style_delivery
    cr_style_delivery.install(engine)
    import cr_shorts
    cr_shorts.install(engine)
    if mode=="claudecode":
        progress=cr_bridge.project_progress(args.project)
        version=json.loads((HOME/"cr-release.json").read_text())["version"]
        current=cr_bridge.call("status")["production_workflow"]
        workflow=progress["workflow"]
        compatible=version==workflow["required_release"] or (version==current["required_release"] and
            workflow.get("workflow_version")==current.get("workflow_version") and
            workflow.get("engine_source_sha256")==current.get("engine_source_sha256"))
        if not compatible:
            raise RuntimeError("Download this project's current studio release before resuming.")
        cr_bridge._project={**progress["project"], "job_spec":progress["job_spec"]}
    else:
        cr_bridge._project=cr_bridge.call("projects/"+args.project)
    checks={**cr_studio.graphics_ready(),"python":sys.version_info >= (3,10),"ffmpeg":cr_studio.video_tools_ready(),
        "node":cr_studio.tool_ready("node","--version") or cr_studio.tool_ready("deno","--version"),
        "local_version":json.loads((HOME/"cr-release.json").read_text())["version"],"account":cr_bridge.call("status")}
    ready=cr_readiness.basic(checks)
    if not ready["ready"]:
        raise RuntimeError("Finish setup before starting this worker: "+" ".join(ready["blockers"]))
    if threed and checks.get("webgl") is not True:
        raise RuntimeError("Finish the local 3D graphics check before reviewing or repairing scenes.")
    if video:
        cr_workers.contain_children()
    cr_workers.install()
    sys.argv=command
    if video:
        import app as studio
        import cr_capabilities
        import cr_video_cancel
        cr_capabilities.install(studio)
        return cr_video_cancel.run_original_worker(HOME,args.project,command[2],studio)
    if threed:
        checks["claude_code"]=ready["claude_code"]
        proof=cr_readiness.proof(checks,purpose="creative")
        return cr_3d.run_operation(HOME,args.project,command[1:],cr_bridge,proof,
            lambda:run_threed_original(command[1:]),operation_id=args.operation)
    return runpy.run_path(str(target),run_name="__main__")


if __name__=="__main__":
    try:main()
    except BaseException as error:
        if isinstance(error,SystemExit):raise
        raise SystemExit(str(error) or "The worker stopped. Its saved work can be reviewed in setup.") from None
