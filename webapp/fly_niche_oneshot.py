"""
Run one adaptive, quality-screened discovery hunt on a Fly Machine, then exit.

  python -m webapp.fly_niche_oneshot <job_id>

Isolated from cook jobs with a dedicated digest-pinned image.
Progress + status live in niche_hunt_runs so the web UI can resume after refresh.
"""
from __future__ import annotations

import os
import sys
import time

_SHUTDOWN = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _sentry_before_send(event, hint):
    exc_info = hint.get("exc_info")
    if exc_info and isinstance(exc_info[1], _SHUTDOWN):
        return None
    return event


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
            environment=os.getenv("APP_ENV", "fly-niche"),
            before_send=_sentry_before_send,
        )
        print("[fly-niche] Sentry initialized")
    except Exception as e:
        print(f"[fly-niche] Sentry init failed: {e}")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m webapp.fly_niche_oneshot <job_id>", file=sys.stderr)
        return 2
    job_id = sys.argv[1].strip()
    _init_sentry()

    from core.niche_cloud import run_cloud_hunt
    try:
        run_cloud_hunt(job_id)
        return 0
    except _SHUTDOWN:
        raise
    except Exception as error:
        print(f"[fly-niche] job {job_id} failed: {type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
