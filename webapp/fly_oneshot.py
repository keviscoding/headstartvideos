"""
Run a single cook job then exit — used by Fly Machines one-shots.

  python -m webapp.fly_oneshot <job_id>
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m webapp.fly_oneshot <job_id>", file=sys.stderr)
        return 2
    job_id = sys.argv[1].strip()
    worker_id = (
        f"fly-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    )

    from webapp.cook_runner import hydrate_job_from_row, run_cook_job
    from webapp.database import claim_cook_job, get_cook_job, update_cook_job

    row = claim_cook_job(job_id, worker_id)
    if not row:
        existing = get_cook_job(job_id)
        print(f"[fly] skip {job_id} status={(existing or {}).get('status')}")
        return 0

    job = hydrate_job_from_row(row)
    job["status"] = "running"
    job["progress"].append({
        "time": time.time(),
        "message": "Starting your cook (Fly)...",
        "phase": "running",
    })
    try:
        update_cook_job(
            job_id,
            status="running",
            started=True,
            heartbeat=True,
            worker_id=worker_id,
            progress_json=json.dumps(job["progress"][-40:]),
        )
    except Exception:
        pass

    def _track(uid, event, props=None):
        try:
            import config
            if not getattr(config, "POSTHOG_KEY", ""):
                return
            from posthog import Posthog
            client = Posthog(project_api_key=config.POSTHOG_KEY, host=config.POSTHOG_HOST)
            client.capture(distinct_id=str(uid), event=event, properties=props or {})
            client.shutdown()
        except Exception:
            pass

    def _capture(exc, context=None):
        print(f"[fly] error: {exc} context={context}")

    def _cancel():
        r = get_cook_job(job_id)
        return bool(r and r.get("status") == "cancelled")

    print(f"[fly] cooking {job_id} recipe={row.get('recipe')}")
    run_cook_job(
        job_id,
        job,
        track=_track,
        capture_error=_capture,
        cancel_check=_cancel,
    )
    print(f"[fly] finished {job_id} status={job.get('status')}")
    return 0 if job.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
