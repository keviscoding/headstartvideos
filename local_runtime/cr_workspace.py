"""ChannelRecipe workspace presentation over the original local control routes.

GETs read the existing catalogue and saved local evidence. They never authorize,
resume, synchronize or create creative work; existing POST controls remain owned
by the original adapters and their local token/readiness guards.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import quote

import cr_bridge
import cr_presentation
import cr_preview
import cr_style_clone

HOME = Path(__file__).resolve().parent
_STYLE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}\Z")
_SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}\Z")
_PROJECT = re.compile(r"crv_[a-f0-9]{24}\Z")
_OPERATION = re.compile(r"op_[a-f0-9]{24}\Z")


def _json_script(value):
    return json.dumps(value, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _text(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def install(studio, activity, *, token, home=None):
    """Replace only GET / and add a read-only, localhost workspace boundary."""
    from flask import Response, jsonify, request, send_file
    app = studio.app
    home = Path(home or HOME).resolve()

    def endpoint(path):
        matches = [rule.endpoint for rule in app.url_map.iter_rules() if rule.rule == path and "GET" in rule.methods]
        if len(matches) != 1:
            raise RuntimeError("The workspace needs the original local studio control routes.")
        return matches[0]

    root_endpoint = endpoint("/")
    original_channels = app.view_functions[endpoint("/api/channels")]

    def local_access():
        if request.host not in {"127.0.0.1:7860", "localhost:7860"} or request.headers.get("Origin") not in {
                None, "http://127.0.0.1:7860", "http://localhost:7860"}:
            raise PermissionError("Open the studio on localhost.")

    def protect(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def failure(error):
        if isinstance(error, PermissionError):
            return protect(jsonify(error=str(error))), 403
        if isinstance(error, ValueError):
            return protect(jsonify(error="Choose an available studio item.")), 400
        return protect(jsonify(error="The workspace could not load this local item. Your saved work is unchanged.")), 503

    def catalog_data():
        # Invoke the original view in this existing request context. No nested
        # HTTP request or after-request transformation/synchronization occurs.
        response = app.make_response(original_channels())
        if response.status_code != 200:
            raise RuntimeError("The local recipe catalogue is unavailable.")
        data = response.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("channels"), list):
            raise RuntimeError("The local recipe catalogue is unavailable.")
        data = dict(data)
        styles = getattr(studio.styles, "STYLES", {})
        automatic = next((row for row in data["channels"]
                          if row.get("name") == "documentary" and row.get("auto_style") is True), None)
        data["channels"] = cr_presentation.presentationCatalog(data["channels"], styles)
        # Adaptive Documentary is a presentation of the real documentary
        # recipe. It retains every engine field and never invents a style ID.
        data.pop("automatic", None)
        if automatic is not None:
            data["automatic"] = cr_presentation.present(automatic, styles, automatic=True)
        # Real media is resolved once per read. This only inspects existing
        # local files; a lazy screenshot GET does any necessary frame decoding.
        outputs = cr_preview.latest_outputs(studio, home)
        for row in data["channels"] + ([data["automatic"]] if data.get("automatic") else []):
            source = cr_preview.recipe_source(studio, home, row.get("name"), cr_bridge, latest=outputs)
            row.update(preview_available=source is not None,
                preview_url=("/cr-workspace/preview/recipe/" + quote(row["name"], safe="") + ".jpg") if source else None,
                preview_label=source.label if source else "No video preview yet",
                preview_video_url=source.video_url if source else None)
        return cr_presentation.plain_copy(data)

    def selected_preset():
        name = request.args.get("cr-preset", "")
        if not _STYLE.fullmatch(name):
            return None
        row = next((row for row in catalog_data()["channels"] if row.get("name") == name), None)
        if row is None:
            return None
        tab = row.get("tab")
        result = {"style": name, "tab": tab if isinstance(tab, str) and tab in {"production", "avatar", "3d"} else "production"}
        checked, release = _read(home / ".cr-setup-proof.json"), _read(home / "cr-release.json")
        setup = checked.get("setup") if isinstance(checked.get("setup"), dict) else {}
        if (checked.get("style") == name and release.get("version")
                and setup.get("local_version") == release["version"]
                and isinstance(checked.get("features"), dict)):
            result["features"] = {key: value for key, value in checked["features"].items()
                                  if isinstance(key, str) and isinstance(value, bool)}
        return result

    def workspace_page():
        try:
            local_access()
            template = (home / "cr_workspace.html").read_text(encoding="utf-8")
            text = template.replace("__LOCAL_TOKEN_JSON__", _json_script(token)).replace(
                "__PRESET_JSON__", _json_script(selected_preset()))
            response = protect(Response(text, mimetype="text/html"))
            response.headers["X-CR-Workspace"] = "1"
            return response
        except Exception as error:
            return failure(error)

    app.view_functions[root_endpoint] = workspace_page

    @app.get("/cr-workspace/studio.js")
    def cr_workspace_script():
        return workspace_asset("cr_workspace.js", "application/javascript")

    @app.get("/cr-workspace/studio.css")
    def cr_workspace_stylesheet():
        return workspace_asset("cr_workspace.css", "text/css")

    def workspace_asset(name, mimetype):
        try:
            local_access()
            path = home / name
            if not path.is_file() or path.resolve().parent != home:
                raise ValueError("Unavailable workspace asset.")
            return protect(send_file(path, mimetype=mimetype, conditional=False))
        except Exception as error:
            return failure(error)

    @app.get("/cr-workspace/art/<name>")
    def cr_workspace_art(name):
        try:
            local_access()
            return protect(send_file(cr_presentation.asset(name), mimetype="image/svg+xml", conditional=False))
        except Exception as error:
            return failure(error)

    @app.get("/cr-workspace/catalog")
    def cr_workspace_catalog():
        try:
            local_access()
            return protect(jsonify(catalog_data()))
        except Exception as error:
            return failure(error)

    def preview_response(source):
        if source is None:
            return protect(Response(status=404))
        try:
            path = cr_preview.screenshot(home, source)
            response = protect(send_file(path, mimetype="image/jpeg", conditional=False))
            response.headers["X-CR-Preview"] = source.label
            return response
        except cr_preview.Unavailable:
            return protect(Response(status=404))
        except cr_preview.Busy:
            response = protect(Response(status=503))
            response.headers["Retry-After"] = "1"
            return response

    @app.get("/cr-workspace/preview/recipe/<style>.jpg")
    def cr_workspace_recipe_preview(style):
        try:
            local_access()
            if not _STYLE.fullmatch(style):
                raise ValueError("Unavailable recipe.")
            # The original visible catalogue remains the authority. Hidden
            # variants and arbitrary file paths are never media endpoints.
            response = app.make_response(original_channels())
            rows = (response.get_json(silent=True) or {}).get("channels", [])
            row = next((row for row in rows if isinstance(row, dict) and row.get("name") == style), None)
            if row is None:
                return protect(Response(status=404))
            return preview_response(cr_preview.recipe_source(studio, home, style, cr_bridge))
        except Exception as error:
            return failure(error)

    @app.get("/cr-workspace/preview/project/<slug>.jpg")
    def cr_workspace_project_preview(slug):
        try:
            local_access()
            if not _SLUG.fullmatch(slug) or slug in {".", ".."}:
                raise ValueError("Unavailable project.")
            return preview_response(cr_preview.project_source(studio, home, slug))
        except Exception as error:
            return failure(error)

    @app.get("/cr-workspace/activity")
    def cr_workspace_activity():
        try:
            local_access()
            with activity.lock:
                busy = bool(activity.busy())
            root = cr_style_clone._folder(home)
            candidates = []
            if root.is_dir():
                for folder in root.glob("op_*"):
                    record = folder / "operation.json"
                    if (not _OPERATION.fullmatch(folder.name) or folder.is_symlink()
                            or not folder.is_dir() or not record.is_file()
                            or record.resolve().parent != folder.resolve()):
                        continue
                    candidates.append((record.stat().st_mtime, folder))
            operations = []
            for _, folder in sorted(candidates, reverse=True)[:12]:
                try:
                    status = cr_style_clone.status(home, folder.name, cr_bridge)
                    # status verifies the current local CR credential owns the
                    # operation before any title/label is copied from its file.
                    spec = _read(folder / "operation.json").get("spec") or {}
                    operations.append({"operation_id": folder.name,
                        "title": _text(spec.get("title"), 300), "label": _text(spec.get("label"), 80),
                        "state": status["state"], "phase": status["phase"], "percent": status["percent"],
                        "elapsed_seconds": status["elapsed_seconds"],
                        "link": "/cr-style-clone?operation=" + folder.name})
                except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
                    # Old malformed/foreign operations never expose credentials,
                    # private briefs, model proofs or authoring responses.
                    continue
            return protect(jsonify(busy=busy, operations=operations))
        except Exception as error:
            return failure(error)

    @app.get("/cr-workspace/render/<slug>")
    def cr_workspace_render(slug):
        try:
            local_access()
            if not _SLUG.fullmatch(slug) or slug in {".", ".."}:
                raise ValueError("Unavailable project.")
            live = getattr(studio, "JOBS", {}).get(slug) or {}
            started = live.get("started")
            if live.get("state") != "running" or not isinstance(started, (int, float)) or isinstance(started, bool):
                return protect(jsonify(scenes=[]))
            import cr_manimgx
            progress = cr_manimgx.progress_status(home)
            # The shared GPU log can include earlier films and setup checks.
            # Only report records written during this actual running job.
            scenes = [row for row in progress.get("scenes", {}).values()
                      if isinstance(row.get("updated_at"), (int, float))
                      and row["updated_at"] >= started and row.get("scene_id") != "setup"]
            return protect(jsonify(scenes=scenes))
        except Exception as error:
            return failure(error)

    @app.get("/cr-workspace/project/<slug>")
    def cr_workspace_project(slug):
        try:
            local_access()
            if not _SLUG.fullmatch(slug) or slug in {".", ".."}:
                raise ValueError("Unavailable project.")
            found = studio.mv.find_job(slug)
            job = Path(found).resolve() if found is not None else None
            if job is None or not job.is_dir() or not job.is_relative_to(home / "output"):
                return protect(jsonify(slug=slug, project_id=None, available_for_resume=False)), 404
            marker = job / "cr-project.json"
            if marker.resolve().parent != job:
                raise ValueError("Unavailable project.")
            saved = _read(marker)
            project_id = saved.get("id")
            valid = isinstance(project_id, str) and bool(_PROJECT.fullmatch(project_id))
            live = getattr(studio, "JOBS", {}).get(slug) or {}
            state = _text(live.get("state") or saved.get("status"), 32)
            spec = saved.get("job_spec") if isinstance(saved.get("job_spec"), dict) else {}
            available = (valid and saved.get("brain_mode") == "claudecode"
                         and saved.get("purpose", "video") == "video"
                         and state not in {"done", "queued", "running"})
            return protect(jsonify(slug=slug, project_id=project_id if valid else None,
                available_for_resume=bool(available), title=_text(live.get("title") or saved.get("title"), 300),
                style=_text(spec.get("style") or saved.get("style"), 140), status=state,
                link="/editor/" + quote(slug, safe="")))
        except Exception as error:
            return failure(error)
