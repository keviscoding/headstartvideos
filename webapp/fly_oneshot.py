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


def _init_sentry() -> None:
    try:
        import config
        if not getattr(config, "SENTRY_DSN", ""):
            return
        import sentry_sdk
        try:
            if sentry_sdk.is_initialized():
                return
        except Exception:
            pass
        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            traces_sample_rate=0.0,
            send_default_pii=False,
            environment=os.getenv("APP_ENV", "fly-cook"),
        )
        print("[fly] Sentry initialized")
    except Exception as e:
        print(f"[fly] Sentry init failed: {e}")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m webapp.fly_oneshot <job_id>", file=sys.stderr)
        return 2
    job_id = sys.argv[1].strip()
    worker_id = (
        f"fly-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    )

    _init_sentry()

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
        try:
            import config
            if not getattr(config, "SENTRY_DSN", ""):
                return
            import sentry_sdk
            _init_sentry()
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("runtime", "fly")
                scope.set_tag("job_id", job_id)
                if context:
                    scope.set_context("cook", context)
                sentry_sdk.capture_exception(exc)
            try:
                sentry_sdk.flush(timeout=3)
            except Exception:
                pass
        except Exception as sentry_err:
            print(f"[fly] sentry capture failed: {sentry_err}")

    def _cancel():
        r = get_cook_job(job_id)
        return bool(r and r.get("status") == "cancelled")

    print(f"[fly] cooking {job_id} recipe={row.get('recipe')}")
    # Log Spaces fingerprint (no secrets) so SignatureDoesNotMatch is debuggable
    try:
        import config as _cfg
        print(
            f"[fly] spaces configured="
            f"{bool(_cfg.SPACES_KEY and _cfg.SPACES_SECRET and _cfg.SPACES_BUCKET)} "
            f"endpoint={(_cfg.SPACES_ENDPOINT or '')[:48]!r} "
            f"bucket={_cfg.SPACES_BUCKET!r} "
            f"key_prefix={(_cfg.SPACES_KEY or '')[:6]!r}"
        )
    except Exception:
        pass

    run_cook_job(
        job_id,
        job,
        track=_track,
        capture_error=_capture,
        cancel_check=_cancel,
    )
    status = job.get("status")
    print(f"[fly] finished {job_id} status={status}")
    return 0 if status in ("complete", "done", "cancelled") else 1


if __name__ == "__main__":
    raise SystemExit(main())
