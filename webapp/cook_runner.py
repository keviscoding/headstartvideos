"""
Shared cook execution used by the in-process web queue and by worker processes.

Workers claim jobs from Postgres/SQLite; the web dyno can either run cooks
locally (COOK_ON_WEB=1) or only enqueue (COOK_ON_WEB=0).
"""
from __future__ import annotations

import json
import os
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent

_COST_PENCE_PER_MIN = {
    "animated_explainer": 15.0,
    "broll_only": 5.0,
    "broll_cinematic": 12.0,
    "avatar_plus_broll": 40.0,
    "storyboard_pack": 8.0,
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


def job_credits_charged(job: dict) -> int:
    """How many credits to refund for this job (HQ may be > 1)."""
    req = job.get("request") if isinstance(job.get("request"), dict) else {}
    raw = req.get("credits_charged")
    if raw is None:
        raw = job.get("credit_deducted")
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        n = 1 if raw else 0
    if n <= 0 and job.get("credit_deducted"):
        return 1
    return max(0, n)


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
        refund_credits,
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

    # Storyboard pack: stills + I2V prompts zip (no voiceover / video pipeline)
    if recipe == "storyboard_pack":
        return _run_storyboard_pack_job(
            job_id,
            job,
            track=track,
            capture_error=capture_error,
            cancel_check=cancel_check,
        )

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
        # Guard: Fish (and others) used to silently truncate long scripts.
        # If the VO is far shorter than the script implies, fail loudly.
        try:
            import subprocess
            words = len((script or "").split())
            if words >= 200:
                expected_sec = words / 150.0 * 60.0
                probe = subprocess.run(
                    [
                        "ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", voiceover_path,
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                actual_sec = float((probe.stdout or "0").strip() or 0)
                if actual_sec > 5 and expected_sec > 0 and actual_sec < expected_sec * 0.55:
                    raise RuntimeError(
                        f"Voiceover is incomplete for this script: audio is "
                        f"{actual_sec / 60:.1f} min but the script is ~{expected_sec / 60:.1f} min "
                        f"({words} words). Re-generate the voiceover (cloned voices now chunk "
                        f"long scripts instead of truncating)."
                    )
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[cook] voiceover completeness check skipped: {e}")
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
    image_quality = (req_data.get("image_quality") or "standard").strip().lower()
    if image_quality in ("high", "hq", "pro"):
        image_quality = "high"
    else:
        image_quality = "standard"
    try:
        from core.image_quality_ctx import set_image_quality
        set_image_quality(image_quality)
    except Exception:
        pass
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
        "image_quality": image_quality,
        "plan": plan,
        "queue_wait_sec": queue_wait_sec,
        "job_id": job_id,
    }
    _track(user_id or "anon", "render_started", dict(base_props))

    # BYOK: if this user stored an Atlas key, bill their provider for cook images/LLM.
    atlas_cm = nullcontext()
    if user_id:
        try:
            from webapp.database import get_user_atlas_key
            from core.atlas_runtime import use_atlas_key
            ak = get_user_atlas_key(int(user_id))
            if ak:
                atlas_cm = use_atlas_key(ak)
        except Exception as atlas_err:
            print(f"[cook] atlas BYOK lookup skipped: {atlas_err}")
            atlas_cm = nullcontext()

    try:
        with atlas_cm:
            if recipe == "animated_explainer":
                from core.explainer_pipeline import run_explainer_pipeline
                result = run_explainer_pipeline(
                    script=script,
                    voiceover_path=voiceover_path,
                    output_name="pipeline_video.mp4",
                    style_preset="default",
                    progress_callback=on_progress,
                    lite_mode=lite_mode,
                    image_quality=image_quality,
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
        # Fly Machines / DO workers: disk dies with the container. Never accept
        # a local /api/files URL there — it shows "VIDEO READY" then 404s.
        import config as _cfg
        ephemeral = (
            (not getattr(_cfg, "COOK_ON_WEB", True))
            or bool(os.getenv("FLY_MACHINE_ID") or os.getenv("FLY_APP_NAME"))
        )
        if ephemeral and not storage.is_remote():
            raise RuntimeError(
                "Spaces is not configured on this cook worker — "
                "finished videos cannot be saved. Set SPACES_* secrets."
            )
        try:
            output_url = storage.store_file(
                result["output_path"], f"videos/{user_id}/{ts}_{job_id}.mp4", "video/mp4"
            )
        except Exception as up_err:
            if ephemeral or storage.is_remote():
                raise RuntimeError(
                    f"Video upload to Spaces failed (file would be lost): {up_err}"
                ) from up_err
            print(f"[storage] video upload failed, falling back to local: {up_err}")
            on_progress("Upload slow — saving local copy...")
            output_url = f"/api/files/{os.path.relpath(result['output_path'], str(ROOT))}"

        if ephemeral and not (
            output_url.startswith("http://") or output_url.startswith("https://")
        ):
            raise RuntimeError(
                f"Remote cook refused local-only video URL ({output_url!r}) — "
                "download would 404 after the machine exits."
            )

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
            amt = job_credits_charged(job)
            refund_credits(user_id, amt)
            job["credit_deducted"] = False
            try:
                update_cook_job(job_id, credit_deducted=False)
            except Exception:
                pass
            print(f"[cook] Auto-refunded {amt} credit(s) for user {user_id} after build failure")


def _run_storyboard_pack_job(
    job_id: str,
    job: dict[str, Any],
    *,
    track: Callable[..., None] | None = None,
    capture_error: Callable[..., None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """Build a downloadable storyboard zip (stills + I2V prompts)."""
    from webapp.database import update_cook_job
    from webapp import storage

    def _track(uid, event, props=None):
        if track:
            track(uid, event, props)

    def _capture(exc, ctx=None):
        if capture_error:
            capture_error(exc, ctx)

    req_data = job.get("request") or {}
    title = (req_data.get("title") or "").strip()
    topic = (req_data.get("topic") or "").strip()
    script = (req_data.get("script") or "").strip()
    try:
        target_minutes = float(req_data.get("target_minutes") or 8)
    except (TypeError, ValueError):
        target_minutes = 8.0
    is_admin = bool(req_data.get("is_admin"))
    is_paid = bool(req_data.get("is_paid"))
    user_id = job.get("user_id")
    started_at = time.time()
    job["status"] = "running"

    _progress_persist_at = [0.0]

    def on_progress(msg: str, phase: str = "running"):
        if job.get("status") == "cancelled" or (cancel_check and cancel_check()):
            job["status"] = "cancelled"
            raise RuntimeError("Cancelled by user")
        job["progress"].append({"time": time.time(), "message": msg, "phase": phase})
        now = time.time()
        if now - _progress_persist_at[0] >= 2.0:
            _progress_persist_at[0] = now
            try:
                update_cook_job(
                    job_id,
                    status=job.get("status"),
                    progress_json=json.dumps(job["progress"][-60:]),
                    heartbeat=True,
                )
            except Exception as e:
                print(f"[storyboard] persist progress failed: {e}")

    atlas_cm = nullcontext()
    if user_id:
        try:
            from webapp.database import get_user_atlas_key
            from core.atlas_runtime import use_atlas_key
            ak = get_user_atlas_key(int(user_id))
            if ak:
                atlas_cm = use_atlas_key(ak)
        except Exception:
            atlas_cm = nullcontext()

    try:
        with atlas_cm:
            on_progress("Starting storyboard pack…")
            from core.storyboard_pack import build_storyboard_pack
            result = build_storyboard_pack(
                title=title,
                topic=topic,
                script=script,
                target_minutes=target_minutes,
                progress=lambda m: on_progress(m),
                is_admin=is_admin,
                is_paid=is_paid,
            )

        zip_local = result.get("zip_path") or result.get("output_path") or ""
        if not zip_local or not Path(zip_local).is_file():
            raise RuntimeError("Storyboard zip was not created.")

        on_progress("Uploading pack…")
        ts = int(time.time())
        import config as _cfg
        ephemeral = (
            (not getattr(_cfg, "COOK_ON_WEB", True))
            or bool(os.getenv("FLY_MACHINE_ID") or os.getenv("FLY_APP_NAME"))
        )
        try:
            zip_url = storage.store_file(
                zip_local,
                f"storyboard/{user_id or 'anon'}/{ts}_{job_id}.zip",
                "application/zip",
            )
        except Exception as up_err:
            if ephemeral or storage.is_remote():
                raise RuntimeError(f"Storyboard zip upload failed: {up_err}") from up_err
            print(f"[storyboard] upload failed, local fallback: {up_err}")
            zip_url = f"/api/files/{os.path.relpath(zip_local, str(ROOT))}"

        pack_meta = {
            "title": result.get("title") or title,
            "zip_url": zip_url,
            "zip_path": zip_local,
            "pack_dir": result.get("pack_dir") or "",
            "beat_count": result.get("beat_count") or 0,
            "target_minutes": result.get("target_minutes") or target_minutes,
            "scene_files": result.get("scene_files") or [],
            "kind": "storyboard_pack",
        }
        job["status"] = "complete"
        job["result"] = pack_meta
        job["error"] = ""
        update_cook_job(
            job_id,
            status="complete",
            progress_json=json.dumps(job["progress"][-60:]),
            result_json=json.dumps(pack_meta),
            finished=True,
        )
        on_progress("Pack ready — download your zip.")
        _track(user_id or "anon", "storyboard_pack_complete", {
            "job_id": job_id,
            "beat_count": pack_meta["beat_count"],
            "target_minutes": pack_meta["target_minutes"],
            "duration_sec": round(time.time() - started_at, 1),
        })
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
        _capture(e, {"job_id": job_id, "recipe": "storyboard_pack", "user_id": user_id})
        try:
            update_cook_job(job_id, status="error", error=str(e), finished=True)
        except Exception:
            pass
        _track(user_id or "anon", "storyboard_pack_failed", {
            "job_id": job_id,
            "error_class": type(e).__name__,
        })
        print(f"[storyboard] job {job_id} failed: {e}")
