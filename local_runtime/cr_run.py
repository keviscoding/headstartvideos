"""Start a saved CR production project from Claude Code's local terminal."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import requests

HOME=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(description="Run your saved ChannelRecipe project locally.")
    parser.add_argument("--project",required=True)
    args=parser.parse_args()
    if not re.fullmatch(r"crv_[a-f0-9]{24}",args.project):
        parser.error("Use the project ID returned by ChannelRecipe.")
    try:
        control=json.loads((HOME/".cr-runtime.json").read_text(encoding="utf-8"))
        response=requests.post("http://127.0.0.1:7860/cr-submit-project",json={"project_id":args.project},
            headers={"X-CR-Local-Token":control["token"]},timeout=(5,90),allow_redirects=False)
        data=response.json()
        if not response.ok:
            raise RuntimeError(data.get("error","The project could not start."))
    except (OSError,ValueError,requests.RequestException):
        raise SystemExit("Open Start on Mac.command or Start on Windows.cmd, finish setup, then run this command again.") from None
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    print(json.dumps({"started":True,"project_id":args.project,"studio_url":"http://127.0.0.1:7860/","jobs":data.get("queued",[])},indent=2))


if __name__ == "__main__":
    main()
