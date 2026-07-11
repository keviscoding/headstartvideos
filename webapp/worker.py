"""
Cook worker process — claims queued jobs from the DB and runs pipelines.

Usage:
  python -m webapp.worker

Set COOK_ON_WEB=0 on the web dyno so only workers execute cooks.
Scale by running more worker processes (each respects MAX_CONCURRENT_COOKS).
"""
from __future__ import annotations

import os
import signal
import socket
import threading
import time
import uuid

from config import (
    MAX_CONCURRENT_COOKS,
    WORKER_DRAIN_SECONDS,
    WORKER_POLL_SECONDS,
    WORKER_STALE_SECONDS,
)
from webapp.cook_runner import hydrate_job_from_row, run_cook_job
from webapp.database import (
    claim_next_cook_job,
    get_cook_job,
    reclaim_stale_cook_jobs,
    requeue_cook_job,
    update_cook_job,
)

_stop = threading.Event()
_worker_id = os.getenv(
    "WORKER_ID",
    f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}",
)
_slots = threading.Semaphore(max(1, int(MAX_CONCURRENT_COOKS)))
_active = 0
_active_jobs: set[str] = set()
_active_lock = threading.Lock()


def _track(distinct_id, event, props=None):
    """Lightweight PostHog capture without importing the FastAPI app."""
    try:
        import config
        if not getattr(config, "POSTHOG_KEY", ""):
            return
        from posthog import Posthog
        client = Posthog(project_api_key=config.POSTHOG_KEY, host=config.POSTHOG_HOST)
        client.capture(distinct_id=str(distinct_id), event=event, properties=props or {})
        try:
            client.shutdown()
        except Exception:
            pass
    except Exception:
        pass


def _capture(exc, context=None):
    try:
        import config
        if not getattr(config, "SENTRY_DSN", ""):
            print(f"[worker] error: {exc} context={context}")
            return
        import sentry_sdk
        if not sentry_sdk.Hub.current.client:
            sentry_sdk.init(dsn=config.SENTRY_DSN, traces_sample_rate=0.0)
        with sentry_sdk.push_scope() as scope:
            if context:
                for k, v in context.items():
                    scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        print(f"[worker] error: {exc} context={context}")


def _cancel_check(job_id: str) -> bool:
    row = get_cook_job(job_id)
    return bool(row and row.get("status") == "cancelled")


def _run_claimed(row: dict) -> None:
    global _active
    job_id = row["job_id"]
    with _active_lock:
        _active += 1
        _active_jobs.add(job_id)
    try:
        job = hydrate_job_from_row(row)
        job["status"] = "running"
        job["progress"].append({
            "time": time.time(),
            "message": "Starting your cook...",
            "phase": "running",
        })
        try:
            update_cook_job(
                job_id,
                status="running",
                started=True,
                heartbeat=True,
                worker_id=_worker_id,
                progress_json=__import__("json").dumps(job["progress"][-40:]),
            )
        except Exception:
            pass
        print(f"[worker {_worker_id}] claimed {job_id} recipe={row.get('recipe')}")
        run_cook_job(
            job_id,
            job,
            track=_track,
            capture_error=_capture,
            cancel_check=lambda: _cancel_check(job_id),
        )
        print(f"[worker {_worker_id}] finished {job_id} status={job.get('status')}")
    except Exception as e:
        print(f"[worker {_worker_id}] crash on {job_id}: {e}")
        try:
            update_cook_job(job_id, status="error", error=str(e), finished=True)
        except Exception:
            pass
    finally:
        with _active_lock:
            _active -= 1
            _active_jobs.discard(job_id)
        _slots.release()


def _handle_signal(signum, frame):
    print(f"[worker {_worker_id}] signal {signum} — draining (no new claims)...")
    _stop.set()


def _requeue_inflight(reason: str) -> None:
    with _active_lock:
        jobs = list(_active_jobs)
    for jid in jobs:
        try:
            if requeue_cook_job(jid, reason=reason):
                print(f"[worker {_worker_id}] requeued {jid} ({reason})")
                _track("system", "cook_requeued", {
                    "job_id": jid, "reason": reason, "worker_id": _worker_id,
                })
        except Exception as e:
            print(f"[worker {_worker_id}] requeue failed for {jid}: {e}")


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    print(
        f"[worker {_worker_id}] online — max_concurrent={MAX_CONCURRENT_COOKS} "
        f"poll={WORKER_POLL_SECONDS}s stale={WORKER_STALE_SECONDS}s "
        f"drain={WORKER_DRAIN_SECONDS}s"
    )
    last_reclaim = 0.0
    while not _stop.is_set():
        now = time.time()
        if now - last_reclaim >= 30:
            try:
                n = reclaim_stale_cook_jobs(WORKER_STALE_SECONDS)
                if n:
                    print(f"[worker {_worker_id}] reclaimed {n} stale job(s)")
                    _track("system", "cook_stale_reclaimed", {
                        "count": n, "worker_id": _worker_id,
                    })
            except Exception as e:
                print(f"[worker {_worker_id}] reclaim failed: {e}")
            last_reclaim = now

        if not _slots.acquire(blocking=False):
            _stop.wait(WORKER_POLL_SECONDS)
            continue

        try:
            row = claim_next_cook_job(_worker_id)
        except Exception as e:
            print(f"[worker {_worker_id}] claim failed: {e}")
            _slots.release()
            _stop.wait(WORKER_POLL_SECONDS)
            continue

        if not row:
            _slots.release()
            _stop.wait(WORKER_POLL_SECONDS)
            continue

        t = threading.Thread(
            target=_run_claimed,
            args=(row,),
            daemon=True,
            name=f"cook-{row['job_id'][:8]}",
        )
        t.start()

    # Drain: let in-flight cooks finish; if deadline hits, requeue so another worker picks up.
    deadline = time.time() + max(30, int(WORKER_DRAIN_SECONDS))
    print(f"[worker {_worker_id}] waiting up to {WORKER_DRAIN_SECONDS}s for in-flight cooks...")
    while time.time() < deadline:
        with _active_lock:
            if _active <= 0:
                break
        time.sleep(0.5)
    with _active_lock:
        still = _active
    if still > 0:
        print(f"[worker {_worker_id}] drain timeout with {still} active — requeueing")
        _requeue_inflight("Requeued after worker drain timeout (redeploy)")
    print(f"[worker {_worker_id}] stopped")


if __name__ == "__main__":
    main()
