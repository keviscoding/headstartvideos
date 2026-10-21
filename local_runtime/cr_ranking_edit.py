"""Edit countdown: change a finished Ranking Short's narrator lines or swap any of its clips, then remake it in place.

The owner: "when it's done with the ranking short, you can click it and redo the lines... select what line you didn't
like and change it, or tell it to find a new clip to replace any particular clip. A mini timeline for this format."

GET  /cr-ranking-edit?job=<slug>        the page (cr_ranking_edit.html)
GET  /cr-ranking/edit/<slug>            the countdown: every slot's label, line (and the writer's other takes), span
GET  /cr-ranking/edit/<slug>/video      the Short as it is now
POST /cr-ranking/edit/<slug>            {"lines": {slot: text}, "swap": [slots]}: written to short/edits.json, then
                                        the Short is made again on its own saved work (cr_shorts.run_short), which
                                        applies them (shorts/ranking/make_ranking.py edit_countdown): a changed line is
                                        read again, a swapped clip is the next good moment the search already found
                                        (one clip watch), and nothing else is paid twice.
One remake at a time; the published video stays where it was until the new one is finished.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from pathlib import Path

SLUG = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_runs: dict = {}
_lock = threading.Lock()


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def install(studio, activity, home, token):
    from flask import Response, jsonify, request, send_file
    app = studio.app

    def job_of(slug):
        if not SLUG.fullmatch(slug or ""):
            return None
        try:
            found = studio.mv.find_job(slug)
        except Exception:                                      # noqa: BLE001
            found = None
        job = Path(found) if found else None
        return job if job and (job / "short" / "picked.json").is_file() else None

    def countdown(job):
        work = job / "short"
        picked = _read(work / "picked.json", []) or []
        spans = {int(c["slot"]): c.get("span") for c in (_read(work / "timeline.json", {}) or {}).get("clips") or []
                 if isinstance(c, dict) and str(c.get("slot", "")).isdigit()}
        rows = []
        for m in sorted((m for m in picked if isinstance(m, dict)), key=lambda m: -int(m.get("slot") or 0)):
            line = m.get("line") if isinstance(m.get("line"), dict) else {}
            alts = [a.get("text") if isinstance(a, dict) else a for a in line.get("alts") or []]
            rows.append({"slot": int(m["slot"]), "label": str(m.get("label") or ""), "what": str(m.get("what") or "")[:200],
                         "source": str(m.get("title") or "")[:120], "line": str(line.get("text") or ""),
                         "alts": [str(a) for a in alts if isinstance(a, str) and a.strip()][:6],
                         "span": spans.get(int(m["slot"]))})
        title = (_read(work / "plan.json", {}) or {}).get("title") or job.name
        return {"title": title, "slots": rows}

    @app.get("/cr-ranking-edit")
    def ranking_edit_page():
        page = (Path(home) / "cr_ranking_edit.html").read_text(encoding="utf-8")
        return Response(page.replace("__LOCAL_TOKEN_JSON__", json.dumps(token)), mimetype="text/html",
                        headers={"Cache-Control": "no-store"})

    @app.get("/cr-ranking/edit/<slug>")
    def ranking_edit_get(slug):
        job = job_of(slug)
        if job is None:
            return jsonify(error="This isn't a finished Ranking Short."), 404
        run = _runs.get(slug) or {}
        return jsonify(**countdown(job), slug=slug, running=bool(run.get("running")), error=run.get("error") or "",
                       lines=list(run.get("lines") or [])[-6:], finished_at=run.get("finished_at"))

    @app.get("/cr-ranking/edit/<slug>/video")
    def ranking_edit_video(slug):
        job = job_of(slug)
        if job is None or not (job / "short" / "short.mp4").is_file():
            return jsonify(error="No video yet."), 404
        response = send_file(job / "short" / "short.mp4", mimetype="video/mp4", conditional=True)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/cr-ranking/edit/<slug>")
    def ranking_edit_post(slug):
        job = job_of(slug)
        if job is None:
            return jsonify(error="This isn't a finished Ranking Short."), 404
        data = request.get_json(silent=True) or {}
        slots = {r["slot"] for r in countdown(job)["slots"]}
        lines = {str(int(k)): str(v or "").strip()[:200] for k, v in (data.get("lines") or {}).items()
                 if str(k).isdigit() and int(k) in slots}
        swap = sorted({int(k) for k in data.get("swap") or [] if str(k).isdigit() and int(k) in slots})
        if not lines and not swap:
            return jsonify(error="Change a line or pick a clip to swap first."), 400
        spec = dict((_read(job / "cr-project.json", {}) or {}).get("job_spec") or {})
        title, style = str(spec.get("title") or ""), str(spec.get("style") or "")
        if job.name != title and SLUG.fullmatch(job.name):     # an attempt folder (title--id): made again right there,
            spec.update(slug=job.name, cr_attempt_slug=job.name)   # not in a fresh folder for the title
        info = (getattr(studio.mv, "STYLE_INFO", {}) or {}).get(style)
        if not title or not info:
            return jsonify(error="This Short's saved recipe can't be found, so it can't be made again here."), 409
        with _lock:
            if any(r.get("running") for r in _runs.values()):
                return jsonify(error="Another Short is being remade. Try again when it's done."), 409
            run = _runs[slug] = {"running": True, "lines": deque(maxlen=40), "error": "", "started_at": time.time()}
        (job / "short" / "edits.json").write_text(json.dumps({"lines": lines, "swap": swap}), encoding="utf-8")

        def remake():
            import cr_attempts
            import cr_shorts

            class Sink:
                def __call__(self, line):
                    run["lines"].append(str(line).strip()[:200])
                write = __call__
            try:
                with cr_attempts.bind(studio.mv, spec):
                    cr_shorts.run_short(studio.mv, title, style, info, features=spec.get("features"), sink=Sink(),
                                        extra=spec.get("extra") or "")
            except Exception as error:                         # noqa: BLE001 - said on the page
                run["error"] = str(error)[:400] or "The Short couldn't be made again."
            finally:
                run["running"], run["finished_at"] = False, time.time()
        threading.Thread(target=remake, daemon=True, name=f"ranking-edit-{slug}").start()
        what = ([f"{len(lines)} line{'s' if len(lines) != 1 else ''}"] if lines else []) + \
               ([f"{len(swap)} clip{'s' if len(swap) != 1 else ''} to swap"] if swap else [])
        return jsonify(started=True, message="Remaking the Short with " + " and ".join(what) + ".")
