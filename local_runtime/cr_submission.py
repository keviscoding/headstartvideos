"""Publish Frontier's original queue items with their owned CR project attached."""
from __future__ import annotations

import re
import json
from types import FunctionType

from flask import request


def submit(studio, payload, project_id, *, before_publish=None, job_slug=None, setup_proof=None):
    """Keep original validation and JOBS, but hide new specs until fully owned.

    Frontier's dispatcher remains alive between jobs. Merely suppressing its
    startup function cannot keep it from consuming an item appended to QUEUE.
    A private globals dictionary stages only this invocation's queue items;
    all other original globals, including JOBS and LOCK, remain shared.
    """
    if not re.fullmatch(r"crv_[a-f0-9]{24}", str(project_id)):
        raise ValueError("Use a valid saved CR project.")
    original = getattr(studio, "_cr_original_api_run", studio.app.view_functions["api_run"])
    return _publish(studio, payload, original, project_id=project_id, before_publish=before_publish, job_slug=job_slug,
                    setup_proof=setup_proof)


def _request_proof():
    """The setup proof cr_studio's check made for this request (flask.g.cr_setup_proof), or None."""
    try:
        from flask import g, has_request_context
        return getattr(g, "cr_setup_proof", None) if has_request_context() else None
    except Exception:                                          # noqa: BLE001 - no Flask context: none
        return None


def _publish(studio, payload, original, *, project_id=None, before_publish=None, job_slug=None, setup_proof=None):
    proof = setup_proof if setup_proof is not None else _request_proof()
    backend = payload.get("render_backend", "original")
    if backend not in {"original", "manimgx"}:
        raise ValueError("Choose the standard renderer or Fast mode.")
    import cr_cta
    import cr_threed_look
    try:
        cta = cr_cta.clean(payload.get("cta"))         # your call to action (cr_cta), checked before any work
        threed_look = cr_threed_look.clean(payload.get("threed_look"))
    except ValueError as error:
        from flask import jsonify
        return studio.app.make_response((jsonify(error=str(error)), 400))
    ppm = payload.get("pictures_per_min")
    if ppm not in (None, "", 0):
        # the creator's picture count for a story recipe, kept between the recipe's quality floor and ceiling
        import cr_media
        styles = getattr(getattr(studio, "styles", None), "STYLES", {}) or {}
        allowed = cr_media.picture_range(styles.get(payload.get("style")) or {})
        try:
            ppm = float(ppm)
        except (TypeError, ValueError):
            ppm = None
        if not allowed or ppm is None or not allowed[0] <= ppm <= allowed[2]:
            from flask import jsonify
            low, high = (allowed[0], allowed[2]) if allowed else (0, 0)
            return studio.app.make_response((jsonify(error=f"Choose between {low:g} and {high:g} pictures a minute."
                                                     if allowed else "This recipe sets its own pictures."), 400))
    else:
        ppm = None
    if "[[ranking=" in str(payload.get("extra") or ""):
        # A batch of Ranking Shorts: each topic once (the same topic twice made the same Short twice), at most
        # cr_queue.MAX_RANKING_BATCH, made one at a time.
        import cr_queue
        from flask import jsonify
        seen, titles = set(), []
        for t in str(payload.get("title") or "").splitlines():
            key = " ".join(t.lower().split())
            if key and key not in seen:
                seen.add(key)
                titles.append(t.strip())
        if len(titles) > cr_queue.MAX_RANKING_BATCH:
            return studio.app.make_response((jsonify(error=f"Queue up to {cr_queue.MAX_RANKING_BATCH} Ranking Shorts "
                                                           "at once. They are made one at a time."), 400))
        payload = {**payload, "title": "\n".join(titles)}
    panels = payload.get("panels") or ""
    if panels:
        # your own comic panels (cr_panels): one set is one video
        import cr_panels
        from flask import jsonify
        try:
            cr_panels.panel_set(panels)
        except ValueError as error:
            return studio.app.make_response((jsonify(error=str(error)), 400))
        if len([t for t in str(payload.get("title") or "").split("\n") if t.strip()]) > 1:
            return studio.app.make_response((jsonify(error="Use one title for a set of panels."), 400))
    staged = []
    staged_jobs = {}
    globals_ = {**original.__globals__, "QUEUE": staged, "JOBS": staged_jobs, "_ensure_dispatcher": lambda: None}
    capture = FunctionType(original.__code__, globals_, original.__name__,
                           original.__defaults__, original.__closure__)
    capture.__kwdefaults__ = original.__kwdefaults__
    cached_json = request._cached_json
    request._cached_json = (payload, payload)
    try:
        response = studio.app.make_response(capture())
    finally:
        request._cached_json = cached_json
    if not 200 <= response.status_code < 300:
        return response
    if not staged:
        raise RuntimeError("Frontier did not construct a queued job.")
    import cr_attempts
    import cr_video_cancel
    saved_slug = job_slug or (cr_attempts.owned_slug(studio.mv, project_id)
                              if project_id and hasattr(studio, "mv") else None)
    published_jobs = {}
    queued = response.get_json().get("queued", [])
    for index, spec in enumerate(staged):
        old_slug = spec["slug"]
        run_id = cr_video_cancel.new_run_id()
        if project_id:
            spec["cr_project_id"] = project_id
        if hasattr(studio, "_cr_video_workers") and not spec.get("timeline"):
            spec["cr_video_run_id"] = run_id
        if not spec.get("timeline") and (not project_id or saved_slug or hasattr(getattr(studio, "mv", None), "list_jobs")):
            slug = saved_slug or cr_attempts.new_slug(old_slug, project_id or run_id)
            spec.update(slug=slug, cr_attempt_slug=slug)
            if index < len(queued):
                queued[index]["slug"] = slug
        published_jobs[spec["slug"]] = dict(staged_jobs.get(old_slug, {"state":"queued", "title":spec.get("title", payload.get("title", old_slug))}))
        spec["render_backend"] = backend
        if panels:
            spec["cr_panels"] = panels
        if cta:
            spec["cr_cta"] = cta
        if ppm:
            spec["cr_pictures_per_min"] = ppm
        if threed_look:
            spec["cr_threed_look"] = threed_look
        if payload.get("burn_subs") is False:
            spec["cr_captions"] = "off"          # switched off for this video: a request kept from before never wins
        elif payload.get("cr_captions") == "on":
            spec["cr_captions"] = "on"           # captions asked for on a style that has none: burned in (cr_captions)
        if payload.get("threed_quick") is True:
            spec["cr_threed_quick"] = True            # one 3D review round (cr_threed_look)
        if isinstance(payload.get("frame_check"), bool):
            # a coded style's frame check for this video (cr_animated2d): Create's switch, or make_videos frame_check
            spec["cr_frame_check"] = "on" if payload["frame_check"] else "off"
        if payload.get("carry_on_after_reset") is True:
            spec["cr_carry_on_after_reset"] = True     # the creator's up-front choice (cr_recover.CARRY_ON)
        if backend == "manimgx":
            spec["manimgx_identity"] = payload.get("manimgx_identity")
        if isinstance(proof, dict):
            # this video's own setup check: a later submission (another recipe, other accounts) never changes it
            spec["cr_setup_proof"] = dict(proof)
    if before_publish is not None:
        before_publish()
    keeper = getattr(studio, "_cr_queue", None)
    if keeper is not None:
        # the waiting line, or on hold with the others when the queue is held for a stop these would hit too; saved,
        # so a Studio closed mid-queue still knows what waited
        places = keeper.add(staged, published_jobs)
        for row, spec in zip(queued, staged):
            if isinstance(row, dict):
                row.update(places.get(spec["slug"]) or {})
    else:
        with studio.LOCK:
            studio.JOBS.update(published_jobs)
            studio.QUEUE.extend(staged)
    response.set_data(json.dumps({**response.get_json(), "queued":queued}))
    studio._ensure_dispatcher()
    return response


def resume_first(studio, payload):
    """A new video with the same title, style and minutes as one of this Studio's stopped videos: the stopped one
    can carry on and reuse everything it made, so Create offers that first (409 with resume_first) instead of a new
    project that pays again for every step. new_attempt=true is the creator insisting on a brand-new video."""
    if payload.get("new_attempt") is True or payload.get("timeline"):
        return None
    titles = [t.strip() for t in str(payload.get("title") or "").splitlines() if t.strip()]
    try:
        minutes = max(1, min(60, int(payload.get("minutes") or 10)))
    except (TypeError, ValueError):
        minutes = 10
    try:
        import cr_recover
        matches = cr_recover.twins(studio, titles, payload.get("style"), minutes, extra=payload.get("extra") or "")
    except Exception:                                  # noqa: BLE001 - never in the way of a new video
        return None
    if not matches:
        return None
    from flask import jsonify
    keys = {cr_recover.title_key(m["title"]) for m in matches}
    first = matches[0]
    said = (f"\u201c{first['title']}\u201d stopped on {first['stopped']} with its work saved." if len(matches) == 1 else
            f"{len(matches)} of these titles stopped earlier with their work saved.")
    return studio.app.make_response((jsonify(
        error=said + " Resume to carry on from where it stopped and reuse everything already made, or start again "
                     "as a brand-new video, which pays again for every step.",
        resume_first=matches, others=[t for t in titles if cr_recover.title_key(t) not in keys]), 409))


def install(studio):
    if hasattr(studio, "_cr_original_api_run"):
        return
    studio._cr_original_api_run = studio.app.view_functions["api_run"]
    def publish():
        payload = request.get_json(silent=True) or {}
        offer = resume_first(studio, payload)
        if offer is not None:
            return offer
        payload = {k: v for k, v in payload.items() if k != "new_attempt"}
        return _publish(studio, payload, studio._cr_original_api_run)
    studio.app.view_functions["api_run"] = publish
