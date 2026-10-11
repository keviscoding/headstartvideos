"""Bootstrap original timeline/product/reference workers with the CR adapters."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import re

HOME=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--project",required=True)
    parser.add_argument("command",nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if not re.fullmatch(r"crv_[a-f0-9]{24}",args.project):
        parser.error("Use an owned CR project ID.")
    command=args.command[1:] if args.command[:1]==["--"] else args.command
    import cr_workers,cr_bridge,cr_claude,cr_media,cr_render,cr_studio,cr_readiness
    if not command:parser.error("Use an original studio worker command.")
    target=Path(command[0]).resolve()
    if target.parent != HOME or target.name not in cr_workers.TARGETS:
        parser.error("Use a supported original worker from this studio folder.")
    cfg=cr_bridge.load_settings()
    mode=cfg.get("brain_mode","channelrecipe")
    cr_studio.configure_video_tools()
    os.environ["CLAUDE_PROVIDER"]=os.environ["SCRIPT_PROVIDER"]="claudecode" if mode=="claudecode" else "channelrecipe"
    os.environ["CLAUDE_HANDOFF_DIR"]=""
    import make_video as engine
    cr_bridge.install(engine)
    cr_media.install(engine)
    cr_render.install(engine)
    if mode=="claudecode":
        progress=cr_bridge.project_progress(args.project)
        version=json.loads((HOME/"cr-release.json").read_text())["version"]
        if version != progress["workflow"]["required_release"]:
            raise RuntimeError("Download this project's current studio release before resuming.")
        cr_bridge._project=progress["project"]
    else:
        cr_bridge._project=cr_bridge.call("projects/"+args.project)
    checks={**cr_studio.graphics_ready(),"python":True,"ffmpeg":cr_studio.video_tools_ready(),
        "node":cr_studio.tool_ready("node","--version") or cr_studio.tool_ready("deno","--version"),
        "local_version":json.loads((HOME/"cr-release.json").read_text())["version"],"account":cr_bridge.call("status")}
    ready=cr_readiness.basic(checks)
    if not ready["ready"]:
        raise RuntimeError("Finish setup before starting this worker: "+" ".join(ready["blockers"]))
    cr_workers.install()
    sys.argv=command
    runpy.run_path(str(target),run_name="__main__")


if __name__=="__main__":
    try:main()
    except BaseException as error:
        if isinstance(error,SystemExit):raise
        raise SystemExit(str(error) or "The worker stopped. Its saved work can be reviewed in setup.") from None
