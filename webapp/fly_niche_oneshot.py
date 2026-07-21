"""
Run a single Niche Finder scroll scrape on a Fly Machine, then exit.

  python -m webapp.fly_niche_oneshot <job_id>

Isolated from cook jobs — same cook app image, different command.
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

    import config
    from core.niche_finder import DEFAULT_KEYWORDS, run_niche_finder
    from webapp.database import (
        append_niche_hunt_progress,
        finish_niche_hunt_run,
        get_niche_hunt_run_by_job_id,
        upsert_niche_channels,
    )

    run = get_niche_hunt_run_by_job_id(job_id)
    if not run:
        print(f"[fly-niche] job {job_id} not found")
        return 1
    if (run.get("status") or "") != "running":
        print(f"[fly-niche] skip {job_id} status={run.get('status')}")
        return 0

    run_id = int(run["id"])
    req = run.get("request") or {}
    kws = [k for k in (run.get("keywords") or []) if k]
    if not kws:
        kws = list(DEFAULT_KEYWORDS)

    def _progress(msg: str) -> None:
        try:
            append_niche_hunt_progress(job_id, msg)
        except Exception as e:
            print(f"[fly-niche] progress write failed: {e}")
        print(f"[fly-niche] {msg}")

    _progress("Fly Machine started — scrolling YouTube…")
    try:
        if not config.YOUTUBE_API_KEY:
            raise RuntimeError("YOUTUBE_API_KEY missing on niche Machine")
        result = run_niche_finder(
            api_key=config.YOUTUBE_API_KEY,
            keywords=kws,
            max_per_keyword=max(3, min(int(req.get("max_per_keyword") or 12), 25)),
            max_channels=max(5, min(int(req.get("max_channels") or 60), 100)),
            min_recent_avg_views=max(0, int(req.get("min_recent_avg_views") or 0)),
            max_subscribers=max(10_000, int(req.get("max_subscribers") or 150_000)),
            scroll_count=max(5, min(int(req.get("scroll_count") or 20), 40)),
            max_video_age_days=max(30, min(int(req.get("max_video_age_days") or 180), 365)),
            progress=_progress,
        )
        hits = result.get("hits") or []
        n = upsert_niche_channels(hits)
        meta = dict(result.get("meta") or {})
        meta["runner"] = "fly"
        meta["finished_at"] = time.time()
        finish_niche_hunt_run(
            run_id,
            status="completed",
            meta=meta,
            channels_upserted=n,
        )
        _progress(f"Saved {n} channels to the niche library")
        return 0
    except _SHUTDOWN:
        finish_niche_hunt_run(
            run_id,
            status="error",
            channels_upserted=0,
            error="Machine stopped before scrape finished",
        )
        raise
    except Exception as e:
        finish_niche_hunt_run(
            run_id,
            status="error",
            channels_upserted=0,
            error=str(e),
        )
        print(f"[fly-niche] job {job_id} failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
