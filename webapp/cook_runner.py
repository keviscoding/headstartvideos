"""
Shared cook execution used by the in-process web queue and by worker processes.

Workers claim jobs from Postgres/SQLite; the web dyno can either run cooks
locally (COOK_ON_WEB=1) or only enqueue (COOK_ON_WEB=0).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent

_COST_PENCE_PER_MIN = {
    "animated_explainer": 15.0,
    "broll_only": 5.0,
    "broll_cinematic": 12.0,
    "avatar_plus_broll": 40.0,
}


def estimate_cost_pence(recipe: str, minutes: float) -> float:
    return round(_COST_PENCE_PER_MIN.get(recipe, 10.0) * max(minutes, 0.1), 2)


def hydrate_job_from_row(row: dict) -> dict[str, Any]:
    """Build the in-memory job dict from a cook_jobs DB row."""
    progress = []
    try:
        progress = json.loads(row.get("progress_json") or "[]")
    except Exception:
        progress = []
    request = {}
    try:
        request = json.loads(row.get("request_json") or "{}")
    except Exception:
        request = {}
    result = None
    try:
        result = json.loads(row["result_json"]) if row.get("result_json") else None
    except Exception:
        result = None
    return {
        "status": row.get("status") or "queued",
        "progress": progress if isinstance(progress, list) else [],
        "result": result,
        "request": request,
        "user_id": row.get("user_id"),
        "credit_deducted": bool(row.get("credit_deducted")),
        "lite_mode": bool(row.get("lite_mode")),
        "error": row.get("error") or "",
        "queue_position": 0,
        "est_wait_minutes": 0,
        "created_at": row.get("created_at") or time.time(),
    }


def run_cook_job(
    job_id: str,
    job: dict[str, Any],
    *,
    track: Callable[..., None] | None = None,
    capture_error: Callable[..., None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """
    Execute a cook end-to-end. Mutates `job` and persists to cook_jobs.
    `job` must include request (dict), user_id, lite_mode, credit_deducted.
    """
    from webapp.database import (
        create_video,
        log_render_event,
        refund_credit,
        update_cook_job,
    )
    from webapp import storage

    def _track(uid, event, props=None):
        if track:
            track(uid, event, props)

    def _capture(exc, ctx=None):
        if capture_error:
            capture_error(exc, ctx)

    if job.get("status") == "cancelled":
        return
    if cancel_check and cancel_check():
        job["status"] = "cancelled"
        job["error"] = "Cancelled by user"
        try:
            update_cook_job(job_id, status="cancelled", error=job["error"], finished=True)
        except Exception:
            pass
        return

    req_data = job.get("request") or {}
    script = req_data.get("script") or ""
    voiceover_path = req_data.get("voiceover_path") or ""
    title = req_data.get("title") or ""
    recipe = req_data.get("recipe") or "animated_explainer"
    thumbnail_path = req_data.get("thumbnail_path") or ""
    notify_email = req_data.get("notify_email") or ""

    # Resolve Spaces URLs /api/files paths to local files for ffmpeg pipelines
    from webapp.storage import fetch_to_local
    cache_dir = ROOT / "output" / "job_inputs" / job_id
    voiceover_path = (voiceover_path or "").strip()
    thumbnail_path = (thumbnail_path or "").strip()
    if voiceover_path:
        try:
            voiceover_path = fetch_to_local(voiceover_path, cache_dir)
        except Exception as e:
            raise RuntimeError(f"Could not load voiceover for cook: {e}") from e
    if thumbnail_path:
        try:
            thumbnail_path = fetch_to_local(thumbnail_path, cache_dir)
        except Exception as e:
            print(f"[cook] thumbnail fetch failed (continuing without): {e}")
            thumbnail_path = ""

    job["status"] = "running"
    started_at = time.time()
    user_id = job.get("user_id")
    est_minutes = round(len(script.split()) / 150, 2) if script else 0
    lite_mode = bool(job.get("lite_mode"))
    queued_at = float(job.get("created_at") or started_at)
    queue_wait_sec = max(0.0, round(started_at - queued_at, 1))
    plan = ""
    if user_id:
        try:
            from webapp.database import get_user_by_id
            u = get_user_by_id(int(user_id))
            plan = (u or {}).get("plan") or ""
        except Exception:
            plan = ""
    _progress_persist_at = [0.0]

    def on_progress(msg: str, phase: str = "running"):
        if job.get("status") == "cancelled" or (cancel_check and cancel_check()):
            job["status"] = "cancelled"
            raise RuntimeError("Cancelled by user")
        job["progress"].append({"time": time.time(), "message": msg, "phase": phase})
        now = time.time()
        if now - _progress_persist_at[0] >= 3.0:
            _progress_persist_at[0] = now
            try:
                update_cook_job(
                    job_id,
                    status=job.get("status"),
                    progress_json=json.dumps(job["progress"][-40:]),
                    heartbeat=True,
                )
            except Exception as e:
                print(f"[cook] persist progress failed: {e}")

    base_props = {
        "recipe": recipe,
        "target_minutes": est_minutes,
        "lite_mode": lite_mode,
        "plan": plan,
        "queue_wait_sec": queue_wait_sec,
        "job_id": job_id,
    }
    _track(user_id or "anon", "render_started", dict(base_props))

    try:
        if recipe == "animated_explainer":
            from core.explainer_pipeline import run_explainer_pipeline
            result = run_explainer_pipeline(
                script=script,
                voiceover_path=voiceover_path,
                output_name="pipeline_video.mp4",
                style_preset="default",
                progress_callback=on_progress,
                lite_mode=lite_mode,
            )
        elif recipe == "broll_only":
            from core.pipeline import run_pipeline
            result = run_pipeline(
                script=script,
                voiceover_path=voiceover_path,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
            )
        elif recipe == "broll_cinematic":
            from core.pipeline import run_cinematic_pipeline
            result = run_cinematic_pipeline(
                script=script,
                voiceover_path=voiceover_path,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
            )
        elif recipe == "avatar_plus_broll":
            from core.avatar_pipeline import run_avatar_pipeline
            from webapp.database import get_user_heygen_key

            avatar_id = (req_data.get("avatar_id") or "").strip()
            voice_id = (req_data.get("voice_id") or "").strip()
            if not avatar_id or not voice_id:
                raise ValueError("Avatar recipe requires avatar_id and voice_id.")
            heygen_key = get_user_heygen_key(user_id) if user_id else None
            if not heygen_key:
                raise ValueError(
                    "HeyGen API key missing — reconnect it in Settings → Integrations."
                )
            result = run_avatar_pipeline(
                script=script,
                avatar_id=avatar_id,
                voice_id=voice_id,
                voiceover_path=None,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
                heygen_api_key=heygen_key,
            )
        else:
            raise ValueError(f"Unknown recipe: {recipe}")

        if job.get("status") == "cancelled" or (cancel_check and cancel_check()):
            raise RuntimeError("Cancelled by user")

        on_progress("Uploading your video...")
        ts = int(time.time())
        try:
            output_url = storage.store_file(
                result["output_path"], f"videos/{user_id}/{ts}_{job_id}.mp4", "video/mp4"
            )
        except Exception as up_err:
            print(f"[storage] video upload failed, falling back to local: {up_err}")
            on_progress("Upload slow — saving local copy...")
            output_url = f"/api/files/{os.path.relpath(result['output_path'], str(ROOT))}"

        thumb_url = ""
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                on_progress("Saving thumbnail...")
                ext = os.path.splitext(thumbnail_path)[1] or ".png"
                thumb_url = storage.store_file(
                    thumbnail_path, f"thumbnails/{user_id}/{ts}_{job_id}{ext}"
                )
            except Exception as th_err:
                print(f"[storage] thumbnail upload failed: {th_err}")

        video_id = None
        if user_id:
            on_progress("Saving to your library...")
            try:
                video_id = create_video(
                    user_id=user_id,
                    title=title or "Untitled",
                    recipe=recipe,
                    video_url=output_url,
                    thumbnail_url=thumb_url,
                )
            except Exception as rec_err:
                print(f"[videos] save failed, retrying: {rec_err}")
                try:
                    time.sleep(0.5)
                    video_id = create_video(
                        user_id=user_id,
                        title=title or "Untitled",
                        recipe=recipe,
                        video_url=output_url,
                        thumbnail_url=thumb_url,
                    )
                except Exception as rec_err2:
                    print(f"[videos] failed to save video record: {rec_err2}")
                    _capture(rec_err2, {"job_id": job_id, "user_id": user_id, "phase": "create_video"})

        on_progress("Done!")
        job["status"] = "complete"
        job["result"] = {
            "output_path": result["output_path"],
            "output_url": output_url,
            "thumbnail_url": thumb_url,
            "video_id": video_id,
            "job_dir": result.get("job_dir", ""),
            "concepts": len(result.get("slots", [])),
            "timing": result.get("timing", {}),
        }
        try:
            update_cook_job(
                job_id,
                status="complete",
                result_json=json.dumps(job["result"]),
                progress_json=json.dumps(job["progress"][-40:]),
                finished=True,
                heartbeat=True,
            )
        except Exception as e:
            print(f"[cook] final persist failed: {e}")

        duration = round(time.time() - started_at, 1)
        cost = estimate_cost_pence(recipe, est_minutes)
        try:
            log_render_event(user_id, job_id, recipe, "succeeded", duration, est_minutes, cost)
        except Exception as log_err:
            print(f"[telemetry] render log failed: {log_err}")
        _track(user_id or "anon", "render_succeeded", {
            **base_props,
            "duration_sec": duration,
            "cook_minutes": round(duration / 60.0, 2),
            "cost_pence": cost,
            "total_wall_sec": round(queue_wait_sec + duration, 1),
        })

        if notify_email:
            try:
                from webapp.email_service import send_video_ready
                send_video_ready(notify_email, title, output_url)
            except Exception as email_err:
                print(f"[cook] Email notification failed: {email_err}")

    except Exception as e:
        if job.get("status") == "cancelled" or "Cancelled by user" in str(e):
            job["status"] = "cancelled"
            job["error"] = "Cancelled by user"
            try:
                update_cook_job(job_id, status="cancelled", error=job["error"], finished=True)
            except Exception:
                pass
            return
        job["status"] = "error"
        job["error"] = str(e)
        _capture(e, {"job_id": job_id, "recipe": recipe, "user_id": user_id})
        try:
            update_cook_job(job_id, status="error", error=str(e), finished=True)
        except Exception:
            pass
        duration = round(time.time() - started_at, 1)
        err_class = type(e).__name__
        try:
            log_render_event(user_id, job_id, recipe, "failed", duration, est_minutes, 0, err_class)
        except Exception as log_err:
            print(f"[telemetry] render log failed: {log_err}")
        _track(user_id or "anon", "render_failed", {
            **base_props,
            "duration_sec": duration,
            "cook_minutes": round(duration / 60.0, 2),
            "error_class": err_class,
        })
        if user_id and job.get("credit_deducted"):
            refund_credit(user_id)
            job["credit_deducted"] = False
            try:
                update_cook_job(job_id, credit_deducted=False)
            except Exception:
                pass
            print(f"[cook] Auto-refunded credit for user {user_id} after build failure")
