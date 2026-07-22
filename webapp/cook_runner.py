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
    "storyboard_assemble": 4.0,
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
    recipe = (req_data.get("recipe") or "").strip() or "animated_explainer"
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
    if recipe == "storyboard_assemble":
        return _run_storyboard_assemble_job(
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
                # Defensive: storyboard should have returned above; keep message clear.
                if recipe == "storyboard_pack":
                    raise ValueError(
                        "storyboard_pack reached the video pipeline — deploy webapp/cook_runner.py "
                        "with the storyboard branch (or rebuild the Fly cook image)."
                    )
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
    story = (req_data.get("story") or topic or "").strip()
    moral = (req_data.get("moral") or "").strip()
    mistake_by = (req_data.get("mistake_by") or "").strip()
    dialogue_mode = (req_data.get("dialogue_mode") or "generate").strip().lower()
    pack_mode = (req_data.get("pack_mode") or "full").strip().lower()
    visual_style = (req_data.get("visual_style") or "").strip()
    template = (req_data.get("template") or "").strip()
    cast = req_data.get("cast") if isinstance(req_data.get("cast"), list) else []
    script = (req_data.get("script") or "").strip()
    thumbnail_path = (req_data.get("thumbnail_path") or "").strip()
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
        from webapp.storage import fetch_to_local
        cache_dir = ROOT / "output" / "job_inputs" / job_id
        cache_dir.mkdir(parents=True, exist_ok=True)

        if thumbnail_path:
            try:
                thumbnail_path = fetch_to_local(thumbnail_path, cache_dir)
            except Exception as e:
                print(f"[storyboard] thumbnail fetch failed (continuing without): {e}")
                thumbnail_path = ""

        # Resolve cast portrait/sheet URLs → local paths for pack inclusion + look lock
        resolved_cast: list[dict] = []
        for row in cast:
            if not isinstance(row, dict):
                continue
            r = dict(row)
            for url_key, path_key in (
                ("portrait_url", "portrait_path"),
                ("sheet_url", "sheet_path"),
            ):
                url = (r.get(url_key) or r.get(path_key) or "").strip()
                if not url:
                    continue
                try:
                    local = fetch_to_local(url, cache_dir / "cast")
                    r[path_key] = local
                    if url_key.endswith("_url") and not r.get(url_key):
                        r[url_key] = url
                except Exception as e:
                    print(f"[storyboard] cast asset fetch failed ({url_key}): {e}")
            resolved_cast.append(r)

        live_beats: list[dict] = []
        job["result"] = {
            "kind": "storyboard_pack",
            "title": title,
            "beats": live_beats,
            "beat_count": 0,
            "zip_ready": False,
        }

        def _persist_live():
            try:
                update_cook_job(
                    job_id,
                    status=job.get("status") or "running",
                    progress_json=json.dumps(job["progress"][-60:]),
                    result_json=json.dumps(job.get("result") or {}),
                    heartbeat=True,
                )
            except Exception as e:
                print(f"[storyboard] live persist failed: {e}")

        def on_still(beat):
            local = beat.image_path
            if not local or not Path(local).is_file():
                return
            ts_u = int(time.time())
            ext = Path(local).suffix or ".jpg"
            try:
                url = storage.store_file(
                    local,
                    f"storyboard/{user_id or 'anon'}/{job_id}/beats/{beat.index:03d}_{ts_u}{ext}",
                    "image/jpeg" if ext.lower() in (".jpg", ".jpeg") else "image/png",
                )
            except Exception as e:
                print(f"[storyboard] still upload failed: {e}")
                url = f"/api/files/{os.path.relpath(local, str(ROOT))}"
            entry = {
                "index": beat.index,
                "image_url": url,
                "dialogue": beat.dialogue,
                "i2v_prompt": beat.i2v_prompt,
                "image_prompt": beat.image_prompt,
                "location": beat.location,
                "characters": beat.characters,
                "time_of_day": getattr(beat, "time_of_day", "") or "",
                "outfit_continuity": getattr(beat, "outfit_continuity", "") or "",
                "target_sec": beat.target_sec,
                "filename": Path(local).name,
            }
            # upsert by index
            found = False
            for i, existing in enumerate(live_beats):
                if existing.get("index") == beat.index:
                    live_beats[i] = entry
                    found = True
                    break
            if not found:
                live_beats.append(entry)
            live_beats.sort(key=lambda x: int(x.get("index") or 0))
            job["result"]["beats"] = live_beats
            job["result"]["beat_count"] = len(live_beats)
            on_progress(f"Scene {beat.index:03d} ready", phase="still")
            _persist_live()

        with atlas_cm:
            on_progress("Starting storyboard pack…")
            from core.storyboard_pack import build_storyboard_pack
            result = build_storyboard_pack(
                title=title,
                topic=topic or story,
                story=story,
                moral=moral,
                cast=resolved_cast,
                mistake_by=mistake_by,
                dialogue_mode=dialogue_mode,
                script=script,
                target_minutes=target_minutes,
                thumbnail_path=thumbnail_path,
                pack_mode=pack_mode,
                visual_style=visual_style,
                template=template,
                progress=lambda m: on_progress(m),
                on_still=on_still,
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

        # Prefer streamed beats; fill any missing from pack files
        final_beats = list(live_beats)
        if not final_beats:
            for b in result.get("beats") or []:
                final_beats.append({
                    "index": b.get("index"),
                    "image_url": "",
                    "dialogue": b.get("dialogue"),
                    "i2v_prompt": b.get("i2v_prompt"),
                    "image_prompt": b.get("image_prompt"),
                    "location": b.get("location"),
                    "characters": b.get("characters"),
                    "time_of_day": b.get("time_of_day") or "",
                    "outfit_continuity": b.get("outfit_continuity") or "",
                    "target_sec": b.get("target_sec"),
                    "filename": b.get("filename"),
                })

        pack_meta = {
            "title": result.get("title") or title,
            "zip_url": zip_url,
            "zip_path": zip_local,
            "pack_dir": result.get("pack_dir") or "",
            "beat_count": result.get("beat_count") or len(final_beats),
            "target_minutes": result.get("target_minutes") or target_minutes,
            "pack_mode": result.get("pack_mode") or pack_mode,
            "visual_style": result.get("visual_style") or visual_style,
            "template": result.get("template") or template,
            "scene_files": result.get("scene_files") or [],
            "beats": final_beats,
            "kind": "storyboard_pack",
            "zip_ready": True,
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


def _run_storyboard_assemble_job(
    job_id: str,
    job: dict[str, Any],
    *,
    track: Callable[..., None] | None = None,
    capture_error: Callable[..., None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """Match I2V clips to pack beats, stitch, burn dialogue captions → MP4."""
    from webapp.database import update_cook_job
    from webapp import storage

    def _track(uid, event, props=None):
        if track:
            track(uid, event, props)

    def _capture(exc, ctx=None):
        if capture_error:
            capture_error(exc, ctx)

    req_data = job.get("request") or {}
    user_id = job.get("user_id")
    started_at = time.time()
    job["status"] = "running"
    clips_dir = Path(str(req_data.get("clips_dir") or ""))
    pack_dir_s = str(req_data.get("pack_dir") or "").strip()
    pack_dir = Path(pack_dir_s) if pack_dir_s else None
    beats = req_data.get("beats") if isinstance(req_data.get("beats"), list) else []
    title = (req_data.get("title") or "storyboard").strip()
    burn_captions = bool(req_data.get("burn_captions", True))
    parent_job_id = (req_data.get("parent_job_id") or "").strip()

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
                print(f"[sb-assemble] persist progress failed: {e}")

    try:
        from core.storyboard_assemble import (
            assemble_storyboard_video,
            extract_clips_from_uploads,
            load_pack_beats,
            match_clips_to_beats,
        )

        on_progress("Loading clips…")
        work = ROOT / "output" / "storyboard_assemble" / job_id
        work.mkdir(parents=True, exist_ok=True)
        local_clips = work / "clips"
        local_clips.mkdir(parents=True, exist_ok=True)

        clips_zip_url = (req_data.get("clips_zip_url") or "").strip()
        if clips_zip_url:
            from webapp.storage import fetch_to_local
            try:
                zip_local = fetch_to_local(clips_zip_url, work / "incoming")
                clips = extract_clips_from_uploads([zip_local], local_clips)
            except Exception as e:
                raise RuntimeError(f"Could not download clips bundle: {e}") from e
        elif clips_dir.is_dir():
            clip_files = list(clips_dir.iterdir())
            clips = extract_clips_from_uploads(clip_files, local_clips)
        else:
            raise RuntimeError("Clips folder missing — re-upload your I2V clips.")

        if not clips:
            raise RuntimeError("No video clips found. Upload .mp4/.webm/.mov or a zip.")

        # Prefer local pack_dir; else fetch stills via beat image_urls during match
        if pack_dir and not pack_dir.is_dir():
            pack_dir = None
        beat_rows = load_pack_beats(pack_dir=pack_dir, beats=beats)
        if not beat_rows:
            raise RuntimeError("No pack scenes found to match against.")

        on_progress(f"Matching {len(clips)} clip(s) to {len(beat_rows)} scenes…")
        matched = match_clips_to_beats(
            clips, beat_rows, pack_dir=pack_dir, work_dir=work / "match",
        )
        if not matched:
            on_progress(
                f"Could not match any clips to stills. "
                "Clips are matched by look (first/last frame hash) — "
                "filenames like hf_… are ignored."
            )
            raise RuntimeError(
                "Could not match any clips to storyboard stills. "
                "Re-upload clips that were generated from these scenes."
            )
        on_progress(f"Matched {len(matched)}/{len(beat_rows)} scenes — assembling…")

        out_mp4 = work / f"{job_id}_assembled.mp4"
        result = assemble_storyboard_video(
            matched=matched,
            output_path=out_mp4,
            work_dir=work / "build",
            progress=on_progress,
            burn_captions=burn_captions,
        )

        video_local = result["output_path"]
        video_url = ""
        try:
            video_url = storage.store_file(
                video_local,
                f"storyboard/{user_id or 'anon'}/{int(time.time())}_{job_id}_assembled.mp4",
            )
        except Exception as e:
            print(f"[sb-assemble] Spaces upload failed (local path kept): {e}")
        if not video_url and video_local and Path(video_local).is_file():
            try:
                import os
                video_url = f"/api/files/{os.path.relpath(str(video_local), str(ROOT))}"
            except Exception:
                video_url = ""

        # History entry (same as other recipes) + optional email notify
        video_id = None
        thumb_url = ""
        # Prefer parent pack thumbnail if present on request beats / parent
        try:
            from webapp.database import create_video, get_cook_job
            if parent_job_id:
                parent = get_cook_job(parent_job_id) or {}
                try:
                    parent_req = json.loads(parent.get("request_json") or "{}")
                except Exception:
                    parent_req = {}
                thumb_url = (parent_req.get("thumbnail_path") or "").strip()
                if thumb_url and not thumb_url.startswith("http") and not thumb_url.startswith("/"):
                    thumb_url = ""
            if user_id and video_url:
                video_id = create_video(
                    user_id=int(user_id),
                    title=title or "Storyboard assemble",
                    recipe="storyboard_assemble",
                    video_url=video_url,
                    thumbnail_url=thumb_url if thumb_url.startswith("http") else "",
                )
        except Exception as rec_err:
            print(f"[sb-assemble] create_video failed: {rec_err}")

        notify_email = (req_data.get("notify_email") or "").strip()
        if notify_email and video_url:
            try:
                from webapp.email_service import send_video_ready
                send_video_ready(notify_email, title or "Your assembled video", video_url)
            except Exception as email_err:
                print(f"[sb-assemble] email notify failed: {email_err}")

        meta = {
            "kind": "storyboard_assemble",
            "title": title,
            "parent_job_id": parent_job_id,
            "video_path": video_local,
            "video_url": video_url,
            "output_path": video_local,
            "output_url": video_url,
            "video_id": video_id,
            "thumbnail_url": thumb_url if thumb_url.startswith("http") else "",
            "match_report": result.get("match_report") or [],
            "beat_count": result.get("beat_count") or len(matched),
            "duration_sec": result.get("duration_sec") or 0,
            "caption_count": result.get("caption_count") or 0,
            "video_ready": True,
        }
        job["status"] = "complete"
        job["result"] = meta
        job["error"] = ""
        update_cook_job(
            job_id,
            status="complete",
            progress_json=json.dumps(job["progress"][-60:]),
            result_json=json.dumps(meta),
            finished=True,
        )
        on_progress("Video ready — download your MP4.")
        _track(user_id or "anon", "storyboard_assemble_complete", {
            "job_id": job_id,
            "parent_job_id": parent_job_id,
            "beat_count": meta["beat_count"],
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
        _capture(e, {"job_id": job_id, "recipe": "storyboard_assemble", "user_id": user_id})
        try:
            update_cook_job(job_id, status="error", error=str(e), finished=True)
        except Exception:
            pass
        _track(user_id or "anon", "storyboard_assemble_failed", {
            "job_id": job_id,
            "error_class": type(e).__name__,
        })
        print(f"[sb-assemble] job {job_id} failed: {e}")
