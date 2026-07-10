"""
In-process FIFO cook queue with a hard concurrency cap.

Stops the "everything slows at once" failure mode: previously every /api/build
spawned a daemon thread immediately. Now jobs wait in line and at most
MAX_CONCURRENT_COOKS renders run at once on this process.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Callable


try:
    from config import MAX_CONCURRENT_COOKS as _CFG_MAX
    MAX_CONCURRENT_COOKS = max(1, int(_CFG_MAX))
except Exception:
    MAX_CONCURRENT_COOKS = max(1, int(os.getenv("MAX_CONCURRENT_COOKS", "1")))
# Rough minutes per cook for UI wait estimates (heuristic until we have p50 history)
EST_MINUTES_PER_COOK = float(os.getenv("EST_MINUTES_PER_COOK", "7"))

_lock = threading.Lock()
_waiting: deque[str] = deque()  # job_ids in FIFO order
_running: set[str] = set()
_runner: Callable[[str], None] | None = None
_jobs_ref: dict[str, dict[str, Any]] | None = None


def configure(
    jobs: dict[str, dict[str, Any]],
    runner: Callable[[str], None],
) -> None:
    """Wire the in-memory job store and the function that executes a job."""
    global _jobs_ref, _runner
    _jobs_ref = jobs
    _runner = runner
    print(f"[job_queue] Ready — max concurrent cooks = {MAX_CONCURRENT_COOKS}")


def enqueue(job_id: str) -> dict[str, Any]:
    """Add a job to the FIFO queue and try to start it if a slot is free."""
    with _lock:
        if job_id not in _waiting and job_id not in _running:
            _waiting.append(job_id)
        _announce_queue_unlocked()
    _dispatch()
    # Return status after dispatch so "you're first" can show running immediately
    return queue_info(job_id)


def cancel_queued(job_id: str) -> bool:
    """Remove a still-queued job. Returns True if it was waiting (not running)."""
    with _lock:
        try:
            _waiting.remove(job_id)
        except ValueError:
            return False
        _announce_queue_unlocked()
        return True


def job_finished(job_id: str) -> None:
    """Mark a running job done and start the next waiter."""
    with _lock:
        _running.discard(job_id)
        try:
            _waiting.remove(job_id)
        except ValueError:
            pass
        _announce_queue_unlocked()
    _dispatch()


def queue_info(job_id: str) -> dict[str, Any]:
    with _lock:
        return _queue_info_unlocked(job_id)


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "max_concurrent": MAX_CONCURRENT_COOKS,
            "running": len(_running),
            "queued": len(_waiting),
            "waiting_ids": list(_waiting),
            "running_ids": list(_running),
        }


def _queue_info_unlocked(job_id: str) -> dict[str, Any]:
    if job_id in _running:
        return {
            "status": "running",
            "queue_position": 0,
            "queue_length": len(_waiting),
            "running_count": len(_running),
            "est_wait_minutes": 0,
        }
    try:
        pos = list(_waiting).index(job_id) + 1  # 1-based among waiters
    except ValueError:
        pos = max(1, len(_waiting))
    # Waiters ahead + currently running slots
    ahead = (pos - 1) + len(_running)
    return {
        "status": "queued",
        "queue_position": pos,
        "queue_length": len(_waiting),
        "running_count": len(_running),
        "est_wait_minutes": round(ahead * EST_MINUTES_PER_COOK, 1),
    }


def _announce_queue_unlocked() -> None:
    """Push fresh queue-position messages so SSE clients stay honest."""
    if not _jobs_ref:
        return
    waiting_list = list(_waiting)
    total = len(waiting_list)
    for i, jid in enumerate(waiting_list):
        job = _jobs_ref.get(jid)
        if not job or job.get("status") != "queued":
            continue
        pos = i + 1
        ahead = i + len(_running)
        wait_m = round(ahead * EST_MINUTES_PER_COOK)
        if ahead <= 0:
            msg = "You're next — starting shortly..."
        elif pos == 1 and len(_running) > 0:
            msg = f"Queued — 1 cook ahead (~{max(wait_m, 1)} min)"
        else:
            msg = f"Queued — position {pos} of {total} (~{max(wait_m, 1)} min wait)"
        # Avoid spamming identical lines
        prev = job["progress"][-1]["message"] if job.get("progress") else ""
        if prev != msg:
            job["progress"].append({"time": time.time(), "message": msg, "phase": "queued"})
        job["queue_position"] = pos
        job["est_wait_minutes"] = wait_m


def _dispatch() -> None:
    """Start waiters until we hit the concurrency cap."""
    to_start: list[str] = []
    with _lock:
        if not _runner or not _jobs_ref:
            return
        while len(_running) < MAX_CONCURRENT_COOKS and _waiting:
            jid = _waiting.popleft()
            job = _jobs_ref.get(jid)
            if not job or job.get("status") == "cancelled":
                continue
            _running.add(jid)
            job["status"] = "running"
            job["started_at"] = time.time()
            job["progress"].append({
                "time": time.time(),
                "message": "Starting your cook...",
                "phase": "running",
            })
            to_start.append(jid)
        _announce_queue_unlocked()

    for jid in to_start:
        t = threading.Thread(target=_safe_run, args=(jid,), daemon=True, name=f"cook-{jid[:8]}")
        t.start()


def _safe_run(job_id: str) -> None:
    try:
        if _runner:
            _runner(job_id)
    except Exception as e:
        print(f"[job_queue] Runner crashed for {job_id}: {e}")
        if _jobs_ref and job_id in _jobs_ref:
            job = _jobs_ref[job_id]
            if job.get("status") not in ("complete", "cancelled"):
                job["status"] = "error"
                job["error"] = str(e)
    finally:
        job_finished(job_id)
