"""ChannelRecipe workspace presentation over the original local control routes.

GETs read the existing catalogue and saved local evidence. They never authorize,
resume, synchronize or create creative work; existing POST controls remain owned
by the original adapters and their local token/readiness guards. The workspace's
own POSTs only file away what the creator deletes (into Studio's trash, cr_trash)
or put it back.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import quote

import cr_bridge
import cr_favicon
import cr_presentation
import cr_preview
import cr_recover
import cr_style_clone
import cr_trash

PREVIEW_CACHE_SECONDS = 120
TRASH_LOCK_WAIT_S = 5           # a delete or restore waits this long for Create's lock (the page's refreshes take it)

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
        # Show the real cause in the launcher window so it can be fixed.
        app.logger.error("Workspace request %s failed", request.path, exc_info=error)
        return protect(jsonify(error="The workspace could not load this local item. Your saved work is unchanged.")), 503

    # Recipe previews resolved by the last catalogue read. Thumbnail requests
    # reuse these instead of re-reading every style for each image.
    preview_cache = {"at": 0.0, "sources": {}}

    def preview_version(source):
        try:
            stat = source.path.stat()
        except OSError:
            return "0"
        identity = f"{source.path}|{stat.st_size}|{stat.st_mtime_ns}|{source.label}"
        return hashlib.sha256(identity.encode()).hexdigest()[:16]

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
        try:
            outputs = cr_preview.latest_outputs(studio, home)
        except Exception:
            app.logger.exception("Could not list finished videos for recipe previews")
            outputs = {}
        sources = {}
        for row in data["channels"] + ([data["automatic"]] if data.get("automatic") else []):
            try:
                source = cr_preview.recipe_source(studio, home, row.get("name"), cr_bridge, latest=outputs)
            except Exception:
                # A preview problem must never hide the recipe list.
                app.logger.exception("Recipe preview unavailable for %s", row.get("name"))
                source = None
            sources[row.get("name")] = source
            info = styles.get(row.get("name")) if isinstance(styles, dict) else None
            # a recipe that shows the creator's own comic panels instead of drawing pictures (cr_panels)
            row["panels"] = bool(isinstance(info, dict) and isinstance(info.get("look"), dict) and info["look"].get("panels"))
            import cr_captions
            row["captions"] = cr_captions.style_default(info)       # Create's captions switch starts at this
            # a story recipe's pictures a minute, with its quality floor (create page slider)
            import cr_media
            span = None if row["panels"] else cr_media.picture_range(info if isinstance(info, dict) else {})
            row["pictures"] = {"floor": span[0], "default": span[1], "ceiling": span[2]} if span else None
            # a Shorts recipe's own length (seconds), the Create page's starting point for it
            shorts = info.get("shorts") if isinstance(info, dict) and info.get("format") == "short" else None
            row["short_seconds"] = int(shorts.get("seconds") or 25) if isinstance(shorts, dict) else None
            row["ranking"] = bool(isinstance(shorts, dict) and shorts.get("pipeline") == "ranking")
            # what one Short of this recipe costs on the creator's accounts (voice, music, effects), for quotes
            cost = shorts.get("cost_usd") if isinstance(shorts, dict) else None
            row["short_cost_usd"] = (float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                                     and 0 <= cost < 100 else None)
            # a style drawn by its own renderer: its background pictures and its own music score, for quotes
            import cr_animated2d
            row["coded_media"] = cr_animated2d.media_usd(info) if isinstance(info, dict) else {}
            # a coded style's frame check before each minute is drawn (Studio 109): Create says the Claude time it adds
            # and keeps its switch, which is Studio's own setting
            drawn = isinstance(info, dict) and isinstance(info.get("look"), dict) and \
                info["look"].get("visuals") in cr_animated2d.PLUGINS
            row["frame_check"] = {"on": cr_animated2d.frame_check_default(home),
                                  "per_minute": list(cr_animated2d.FRAME_CHECK_MINUTES)} if drawn else None
            # a style written in code with a working renderer can be improved on its study's page (Improve this style)
            look = info.get("look") if isinstance(info, dict) and isinstance(info.get("look"), dict) else {}
            made = ((look.get("animated2d") or {}).get("renderer") or {}) if look.get("visuals") in cr_animated2d.PLUGINS \
                else {}
            study = ((info or {}).get("cr_style_clone") or {}).get("operation_id") if made.get("file") else None
            row["improve_study"] = study if isinstance(study, str) and _OPERATION.fullmatch(study) else None
            row["coded"] = look.get("visuals") in cr_animated2d.PLUGINS     # its own code draws every picture
            # the language its videos speak by its own file; Create's Language starts here unless Studio has a default
            import cr_language
            row["language"] = cr_language.style_language(info)
            # a cloned style's own narrator (its channel's kind of voice, or the one chosen for it): Create shows it
            row["narrator"] = (cr_media.style_voice(row.get("name"), info if isinstance(info, dict) else {})
                               .get("voice") or None)
            row.update(preview_available=source is not None,
                preview_url=("/cr-workspace/preview/recipe/" + quote(row["name"], safe="") + ".jpg?v="
                             + preview_version(source)) if source else None,
                preview_label=source.label if source else "No video preview yet",
                preview_video_url=source.video_url if source else None)
        preview_cache.update(at=time.monotonic(), sources=sources)
        # what Research footage may watch for each length, 1 to 60 minutes (Create says it before a video starts)
        import cr_watch
        data["footage_watch"] = [cr_watch.budget(minutes) for minutes in range(1, 61)]
        # the last video's footage watching when Kie's watcher failed in it (moved to ChannelRecipe's, or stopped)
        data["footage_watcher"] = cr_watch.last_outcome(home)
        # the languages a video can speak, and the one Create starts at when the creator chose one before
        import cr_language
        data["languages"] = cr_language.choices(also={row.get("language") for row in data["channels"]})
        data["default_language"] = cr_language.default(home) or None
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

    # The tab icon: the brand mark, with a status dot the page asks for while it works or when a video is ready.
    @app.get("/favicon.ico")
    def cr_favicon_ico():
        response = Response(cr_favicon.ICO, mimetype="image/x-icon")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @app.get("/cr-workspace/icon.svg")
    def cr_favicon_svg():
        response = Response(cr_favicon.svg(request.args.get("state", "")), mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "public, max-age=86400"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/cr-workspace/art/<name>")
    def cr_workspace_art(name):
        try:
            local_access()
            return protect(send_file(cr_presentation.asset(name), mimetype="image/svg+xml", conditional=False))
        except Exception as error:
            return failure(error)

    def panel_error(error):
        if isinstance(error, (ValueError, PermissionError)):
            return protect(jsonify(error=str(error))), 403 if isinstance(error, PermissionError) else 400
        app.logger.error("Panel request %s failed", request.path, exc_info=error)
        return protect(jsonify(error="Your panels could not be saved. Nothing was changed.")), 503

    @app.post("/cr-workspace/panels")
    def cr_workspace_panels_upload():
        """Your own comic panels for a panel recipe: pictures, or one .zip/.cbz of them."""
        try:
            local_access()
            import cr_panels
            files = [(f.filename or "", f.read()) for f in request.files.getlist("files")]
            return protect(jsonify(panels=cr_panels.save(files, home / "uploads" / "panels")))
        except Exception as error:
            return panel_error(error)

    @app.get("/cr-workspace/panels")
    def cr_workspace_panels_recent():
        try:
            local_access()
            import cr_panels
            return protect(jsonify(panels=cr_panels.recent(home / "uploads" / "panels")))
        except Exception as error:
            return panel_error(error)

    @app.get("/cr-workspace/panels/<set_id>/<int:number>.jpg")
    def cr_workspace_panel(set_id, number):
        try:
            local_access()
            import cr_panels
            files = cr_panels.panel_set(set_id, home / "uploads" / "panels")["files"]
            if not 1 <= number <= len(files):
                raise ValueError("That panel is not in this set.")
            return protect(send_file(files[number - 1], mimetype="image/jpeg", conditional=False))
        except Exception as error:
            return panel_error(error)

    @app.post("/cr-workspace/presenter")
    def cr_workspace_presenter():
        """Your own presenter: a photo, a name and the voice they speak with."""
        try:
            local_access()
            import cr_presenters
            upload = request.files.get("file")
            if upload is None:
                raise ValueError("Choose a photo of your presenter.")
            row = cr_presenters.save(upload.read(), name=request.form.get("name", ""),
                                     gender=request.form.get("gender", ""), persona=request.form.get("persona", ""),
                                     folder=home / "assets" / "avatars" / "my")
            return protect(jsonify(presenter={k: row[k] for k in ("id", "name", "gender", "persona")}))
        except Exception as error:
            return panel_error(error)

    @app.post("/cr-workspace/cta-image")
    def cr_workspace_cta_image():
        """A cover or screenshot for a call-to-action card."""
        try:
            local_access()
            import cr_cta
            upload = request.files.get("file")
            if upload is None:
                raise ValueError("Choose a picture for your call to action.")
            return protect(jsonify(image=cr_cta.save_image(upload.read(), home / "uploads" / "cta")))
        except Exception as error:
            return panel_error(error)

    def character_error(error):
        if isinstance(error, (ValueError, PermissionError)):
            return protect(jsonify(error=str(error))), 403 if isinstance(error, PermissionError) else 400
        app.logger.error("Character request %s failed", request.path, exc_info=error)
        return protect(jsonify(error="Your character could not be made: " + str(error)[:200])), 503

    @app.post("/cr-workspace/character")
    def cr_workspace_character_upload():
        """A picture of your own character (cr_character): it replaces the recipe's picture in the videos it's picked
        for. Create sends a form; Studio's Claude Code tools send {image_base64, filename, description}."""
        try:
            local_access()
            import base64
            import binascii
            import cr_character
            upload = request.files.get("file")
            if upload is not None:
                data, name, words = upload.read(), upload.filename or "", request.form.get("description", "")
            else:
                body = request.get_json(silent=True) or {}
                try:
                    data = base64.b64decode(str(body.get("image_base64") or ""), validate=True)
                except (binascii.Error, ValueError):
                    raise ValueError("Send the picture as base64.") from None
                name, words = str(body.get("filename") or ""), str(body.get("description") or "")
            path = cr_character.save_upload(home, data, name, words)
            return protect(jsonify(character=cr_character.item(home, path)))
        except Exception as error:
            return character_error(error)

    @app.post("/cr-workspace/character/draw")
    def cr_workspace_character_draw():
        """A character described in words, drawn once in the recipe's own style (one picture on the creator's image
        account, about $0.02). The same words and recipe reuse it, here and when the video's stills are drawn."""
        try:
            local_access()
            import cr_character
            body = request.get_json(silent=True) or {}
            style = str(body.get("style") or "")
            words = str(body.get("description") or "").strip()
            st = (getattr(studio.styles, "STYLES", {}) or {}).get(style)
            if not isinstance(st, dict):
                raise ValueError("Choose a recipe first.")
            if not words:
                raise ValueError("Describe your character first: who they are, their age, hair, face and clothes.")
            if not cr_bridge.load_settings().get("costs_accepted"):
                raise ValueError("Review your provider spending in setup before drawing a character.")
            look = st.get("look") if isinstance(st.get("look"), dict) else {}
            import cr_media
            if cr_media.active("images"):
                def generate(prompt, dest, label, ref_urls):
                    return cr_media.image(prompt, dest, label, ref_urls=ref_urls, model=look.get("image_model") or None)
            else:
                def generate(prompt, dest, label, ref_urls):
                    return studio.mv.image_gen(prompt, dest, label, ref_urls=ref_urls,
                                               kie_create_fn=getattr(studio.mv, "_kie_create", None))
            try:
                path = cr_character.draw(home, style, words, look, generate=generate, host=studio.mv.host_image,
                                         log=app.logger.info)
            except SystemExit as stop:                   # the engine exits when no picture service is set up
                raise ValueError(f"Connect a picture account in setup first ({stop}).") from None
            return protect(jsonify(character=cr_character.item(home, path)))
        except Exception as error:
            return character_error(error)

    @app.get("/cr-workspace/cta-image/<image>.jpg")
    def cr_workspace_cta_image_file(image):
        try:
            local_access()
            import cr_cta
            if not cr_cta.IMAGE_ID.fullmatch(image or ""):
                raise ValueError("Choose your picture again.")
            path = home / "uploads" / "cta" / f"{image}.jpg"
            if not path.is_file():
                raise ValueError("Choose your picture again.")
            return protect(send_file(path, mimetype="image/jpeg", conditional=False))
        except Exception as error:
            return panel_error(error)

    @app.post("/cr-workspace/language")
    def cr_workspace_language():
        """Create's Language: the creator's choice is kept as Studio's default for their next videos."""
        try:
            local_access()
            import cr_language
            body = request.get_json(silent=True) or {}
            return protect(jsonify(language=cr_language.set_default(home, body.get("language"))))
        except Exception as error:
            return failure(error)

    @app.post("/cr-workspace/frame-check")
    def cr_workspace_frame_check():
        """Create's frame-check switch: Studio's setting for a coded style's videos (Claude looks at each minute's frames
        before they are drawn); on or off for every new video, until switched again."""
        try:
            local_access()
            import cr_animated2d
            body = request.get_json(silent=True) or {}
            if not isinstance(body.get("on"), bool):
                raise ValueError("Say on or off.")
            return protect(jsonify(on=cr_animated2d.set_frame_check(home, body["on"])))
        except Exception as error:
            return failure(error)

    @app.get("/cr-workspace/catalog")
    def cr_workspace_catalog():
        try:
            local_access()
            return protect(jsonify(catalog_data()))
        except Exception as error:
            return failure(error)

    def preview_response(source, *, versioned=False):
        if source is None:
            return protect(Response(status=404))
        try:
            path = cr_preview.screenshot(home, source)
            response = protect(send_file(path, mimetype="image/jpeg", conditional=True))
            # A versioned URL changes whenever its picture does, so the browser
            # may keep it; unversioned previews revalidate with their ETag.
            response.headers["Cache-Control"] = ("private, max-age=31536000, immutable" if versioned
                                                 else "private, no-cache")
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
            # The visible catalogue remains the authority: only recipes it lists
            # have previews, and hidden variants or file paths never do.
            if time.monotonic() - preview_cache["at"] > PREVIEW_CACHE_SECONDS or style not in preview_cache["sources"]:
                catalog_data()
            if style not in preview_cache["sources"]:
                return protect(Response(status=404))
            return preview_response(preview_cache["sources"][style], versioned=bool(request.args.get("v")))
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
                    if status.get("renderer_improve"):
                        continue                      # a change to a saved style shows on that style's own page
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

    @app.get("/cr-workspace/stopped")
    def cr_workspace_stopped():
        """Videos that stopped before finishing, read from their saved folders, so they stay in Projects (with
        Resume) after Studio restarts: the live job list only knows what ran since the last start. Read only."""
        try:
            local_access()
            import cr_media
            rows = []
            for job in (studio.mv.list_jobs() if hasattr(studio.mv, "list_jobs") else []):
                try:
                    job = Path(job)
                    slug = job.name
                    # a video that stopped on its presenter has its film finished but not published: still stopped
                    presenter = (job / cr_media.PRESENTER_FAILED).is_file()
                    if not _SLUG.fullmatch(slug) or studio.mv.final_video(job).is_file() and not presenter:
                        continue
                    marker = job / "cr-project.json"
                    saved = _read(marker)
                    project_id = saved.get("id")
                    # the same rule as the project's Resume button (cr_workspace_project), so every card can resume
                    # a saved "running" with no live job is stale: that video's Studio closed mid-run (cr_recover)
                    if (not isinstance(project_id, str) or not _PROJECT.fullmatch(project_id)
                            or saved.get("brain_mode") != "claudecode" or saved.get("purpose", "video") != "video"
                            or cr_recover.effective_state(studio, slug, saved) in {"done", "queued", "running", "held"}):
                        continue
                    spec = saved.get("job_spec") if isinstance(saved.get("job_spec"), dict) else {}
                    title_file = job / "title.txt"
                    title = (title_file.read_text(encoding="utf-8").splitlines() or [""])[0] if title_file.is_file() else ""
                    rows.append({"slug": slug, "title": _text(title or spec.get("title") or saved.get("title"), 300),
                                 "style": _text(spec.get("style") or saved.get("style"), 140), "state": "stopped",
                                 "mtime": marker.stat().st_mtime, **({"stopped_on": "presenter"} if presenter else {})})
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
            rows.sort(key=lambda r: -r["mtime"])
            return protect(jsonify(projects=rows[:30]))
        except Exception as error:
            return failure(error)

    # A video whose voice stopped (a provider down, a balance run out) can be voiced another way, for that video
    # only: Gemini through the same Kie key, the creator's own ElevenLabs account, or their own recording. The choice
    # sits in its folder (cr_media.VOICE_CHOICE) and Resume carries on with it.
    GEMINI_TTS = "google/gemini-3-8-flash-tts"
    GEMINI_VOICES = {"gemini-man": ("Charon", "a man's voice"), "gemini-woman": ("Kore", "a woman's voice")}

    def in_output(job) -> bool:
        """A video's folder inside Studio's output folder, or inside the engine's own (CHANNELRECIPE_OUTPUT moves the
        videos to another drive): never the folder itself, never anywhere else."""
        roots = [home / "output"] + ([Path(studio.mv.OUTPUT_ROOT).resolve()]
                                     if getattr(studio.mv, "OUTPUT_ROOT", None) else [])
        return any(job.is_relative_to(root) and job != root for root in roots)

    def voice_job(slug):
        if not _SLUG.fullmatch(slug) or slug in {".", ".."}:
            raise ValueError("Unavailable project.")
        found = studio.mv.find_job(slug)
        job = Path(found).resolve() if found is not None else None
        if job is None or not job.is_dir() or not in_output(job):
            raise ValueError("Unavailable project.")
        return job

    def voice_choices(job):
        import os
        import cr_media
        failed = _read(job / cr_media.VOICE_FAILED) if (job / cr_media.VOICE_FAILED).is_file() else {}
        chosen = cr_media.job_voice(job)
        current = ("own" if chosen.get("provider") == "own" else "elevenlabs" if chosen.get("provider") == "original"
                   else next((k for k, (v, _) in GEMINI_VOICES.items() if v == chosen.get("voice")), "gemini-man")
                   if chosen.get("provider") == "kie" else "again")
        kie, eleven = bool(os.getenv("KIE_API_KEY", "").strip()), bool(os.getenv("ELEVENLABS_API_KEY", "").strip())
        kie_empty = failed.get("provider") == "kie" and failed.get("category") == "balance"
        rows = [{"id": "again", "label": "Try the same voice again", "ready": True,
                 "note": "The voice in Setup & accounts, once more. Best after waiting a few minutes."}]
        if failed.get("model") != GEMINI_TTS or current != "again":
            for key, (voice, words) in GEMINI_VOICES.items():
                rows.append({"id": key, "label": f"Gemini voice through Kie ({words})", "ready": kie and not kie_empty,
                             "note": ("Your Kie balance ran out: top it up first." if kie_empty else
                                      f"The same Kie account, read by the Gemini voice {voice}." if kie
                                      else "Needs a Kie.ai key in Setup & accounts.")})
        rows.append({"id": "elevenlabs", "label": "Your own ElevenLabs account", "ready": eleven,
                     "note": "Skips Kie: ElevenLabs reads it directly, charged to your ElevenLabs plan." if eleven
                     else "Add your ElevenLabs key in Setup & accounts first (paste it there, never in a chat).",
                     **({} if eleven else {"setup": "/cr-setup"})})
        rows.append({"id": "own", "label": "Your own recording", "ready": True,
                     "note": "Read the script aloud and upload the recording (mp3, wav or m4a). Nothing is paid for "
                             "the voice, and the video is timed to your reading."})
        script = job / "script.txt"
        return {"stopped_on_voice": bool(failed), "current": current, "choices": rows,
                "failed": {k: failed.get(k) for k in ("label", "reason", "category", "charged", "at")} if failed else None,
                "has_voice": (job / "audio.mp3").is_file(),
                "script": script.read_text(encoding="utf-8", errors="replace")[:40000] if script.is_file() else "",
                "script_file": str(script) if script.is_file() else ""}

    @app.get("/cr-workspace/voice/<slug>")
    def cr_workspace_voice(slug):
        try:
            local_access()
            return protect(jsonify(slug=slug, **voice_choices(voice_job(slug))))
        except Exception as error:
            return failure(error)

    @app.post("/cr-workspace/voice/<slug>")
    def cr_workspace_voice_pick(slug):
        try:
            local_access()
            import cr_media
            job = voice_job(slug)
            if ((getattr(studio, "JOBS", {}).get(job.name) or {}).get("state")) in {"running", "queued"}:
                return protect(jsonify(error="This video is still being made. Pick its voice once it has stopped.")), 409
            if (job / "audio.mp3").is_file():
                return protect(jsonify(error="This video already has its voice. Resume it as it is.")), 409
            data = request.get_json(silent=True) or {}
            pick = str(data.get("choice") or "")
            offer = {c["id"]: c for c in voice_choices(job)["choices"]}
            if pick not in offer:
                return protect(jsonify(error="Pick one of the voices offered for this video.")), 400
            if not offer[pick]["ready"]:
                return protect(jsonify(error=offer[pick]["note"])), 409
            target = job / cr_media.VOICE_CHOICE
            if pick == "again":
                target.unlink(missing_ok=True)
            elif pick in GEMINI_VOICES:
                target.write_text(json.dumps({"provider": "kie", "model": GEMINI_TTS, "voice": GEMINI_VOICES[pick][0]}),
                                  encoding="utf-8")
            elif pick == "elevenlabs":
                target.write_text(json.dumps({"provider": "original"}), encoding="utf-8")
            else:
                import voices
                ref = str(data.get("recording") or "").strip()
                src = voices.resolve(ref)
                if src is None:
                    return protect(jsonify(error="Upload your recording first (mp3, wav or m4a).")), 400
                if src.parent.resolve() != voices.UPLOADS.resolve():      # a file on this computer: kept with the uploads
                    with src.open("rb") as stream:
                        ref = voices.save_upload(stream, src.name)["id"]
                target.write_text(json.dumps({"provider": "own", "file": ref}), encoding="utf-8")
            for pattern in ("audio_p*.mp3", "subs_p*.srt"):                    # a half-voiced presenter split: all again
                for part in job.glob(pattern):
                    part.unlink(missing_ok=True)
            label = offer[pick]["label"]
            return protect(jsonify(saved=True, choice=pick, message=f"This video will use: {label}. Resume it to carry on "
                                   "from where it stopped; everything already made is reused."))
        except ValueError as error:
            if "Unavailable" in str(error):
                return failure(error)
            return protect(jsonify(error=str(error))), 400
        except Exception as error:
            return failure(error)

    # Delete a finished or stopped video, or a learned style: GET says what goes and what is kept, POST moves it into
    # Studio's trash (cr_trash), where it waits 30 days and Recently deleted can put it back. Nothing is removed at once.
    def trash_error(error):
        if isinstance(error, cr_trash.Refused):
            return protect(jsonify(error=str(error))), 409
        if isinstance(error, LookupError):
            return protect(jsonify(error=str(error) or "That item isn't here any more.")), 404
        return failure(error)

    def one_at_a_time(work):
        """The same lock Create and Resume take, so a video can't start while it is being deleted."""
        if not activity.lock.acquire(timeout=TRASH_LOCK_WAIT_S):
            return protect(jsonify(error="Finish the current studio request, then try again. Nothing was deleted.")), 409
        try:
            return protect(jsonify(work()))
        finally:
            activity.lock.release()

    @app.get("/cr-workspace/delete/<kind>/<name>")
    def cr_workspace_delete_plan(kind, name):
        try:
            local_access()
            plan = {"video": cr_trash.video_plan, "style": cr_trash.style_plan}.get(kind)
            if plan is None:
                raise LookupError("Choose a video or a style.")
            return protect(jsonify(cr_trash.public(plan(studio, home, name))))
        except Exception as error:
            return trash_error(error)

    @app.post("/cr-workspace/delete/<kind>/<name>")
    def cr_workspace_delete(kind, name):
        try:
            local_access()
            delete = {"video": cr_trash.delete_video, "style": cr_trash.delete_style}.get(kind)
            if delete is None:
                raise LookupError("Choose a video or a style.")
            return one_at_a_time(lambda: delete(studio, home, name))
        except Exception as error:
            return trash_error(error)

    @app.get("/cr-workspace/trash")
    def cr_workspace_trash():
        try:
            local_access()
            return protect(jsonify(entries=cr_trash.entries(home), keep_days=cr_trash.KEEP_DAYS))
        except Exception as error:
            return trash_error(error)

    @app.post("/cr-workspace/trash/<entry>/restore")
    def cr_workspace_restore(entry):
        try:
            local_access()
            return one_at_a_time(lambda: cr_trash.restore(studio, home, entry))
        except Exception as error:
            return trash_error(error)

    # A video whose presenter couldn't be made (its avatar service failed at the very last step) has its whole film
    # finished and saved: it can finish without the presenter, or try the presenter again later. The choice sits in
    # its folder (cr_media.PRESENTER_CHOICE) and Resume carries on with it.
    def presenter_choices(job):
        import cr_media
        failed = _read(job / cr_media.PRESENTER_FAILED) if (job / cr_media.PRESENTER_FAILED).is_file() else {}
        uncertain = bool(cr_media.uncertain_presenter(job)) if failed else False
        charged = failed.get("charged")
        label = _text(failed.get("label"), 60) or "The presenter service"
        category = str(failed.get("category") or "")
        passing = category in ("model_failure", "rate_limit") or bool(cr_media.TRANSIENT.search(str(failed.get("reason")
                                                                                                    or "")))
        # out of credit, refused, or not a hiccup: waiting a few minutes won't change it, and the note says so
        again = ("The presenter is made again from where it stopped. Its last attempt got no clear answer from "
                 f"{label}: check its task log first, because if that task did start, trying again pays for a second "
                 "one." if uncertain else
                 f"{label} says the balance ran out: top it up first, then try again." if category == "balance" else
                 f"{label} refused the key: reconnect it in Setup & accounts first, then try again." if category ==
                 "access" else
                 f"{label} refused this presenter's content, so trying again is unlikely to work." if category ==
                 "moderation" else
                 f"{label} refused the request itself, so trying again is unlikely to work unless something changes."
                 if failed and not passing else
                 f"Best after waiting a few minutes. {label} charged nothing for the attempt that failed." if charged == 0
                 else "Best after waiting a few minutes. The presenter is made again from where it stopped.")
        rows = [{"id": "skip", "label": "Finish without the presenter", "ready": True,
                 "note": "The video is finished as it is now, without the presenter intro. Nothing more is paid."},
                {"id": "again", "label": "Try the presenter again", "ready": True, "note": again}]
        return {"stopped_on_presenter": bool(failed),
                "current": "skip" if cr_media.job_presenter(job).get("skip") else "again", "choices": rows,
                "failed": {k: failed.get(k) for k in ("label", "reason", "category", "charged", "uncertain", "at")}
                if failed else None}

    @app.get("/cr-workspace/presenter-choice/<slug>")
    def cr_workspace_presenter_choice(slug):
        try:
            local_access()
            return protect(jsonify(slug=slug, **presenter_choices(voice_job(slug))))
        except Exception as error:
            return failure(error)

    @app.post("/cr-workspace/presenter-choice/<slug>")
    def cr_workspace_presenter_choice_pick(slug):
        try:
            local_access()
            import cr_media
            job = voice_job(slug)
            if ((getattr(studio, "JOBS", {}).get(job.name) or {}).get("state")) in {"running", "queued"}:
                return protect(jsonify(error="This video is still being made. Choose once it has stopped.")), 409
            pick = str((request.get_json(silent=True) or {}).get("choice") or "")
            if pick not in {"skip", "again"}:
                return protect(jsonify(error="Choose to finish without the presenter, or to try it again.")), 400
            target = job / cr_media.PRESENTER_CHOICE
            if pick == "skip":
                target.write_text(json.dumps({"skip": True}), encoding="utf-8")
                message = "This video finishes without its presenter. Resume it to finish it; nothing more is paid."
            else:
                target.unlink(missing_ok=True)
                if (job / cr_media.PRESENTER_FAILED).is_file():
                    cr_media.retry_presenter(job)
                message = "This video tries its presenter again. Resume it to carry on; everything else is reused."
            return protect(jsonify(saved=True, choice=pick, message=message))
        except ValueError as error:
            if "Unavailable" in str(error):
                return failure(error)
            return protect(jsonify(error=str(error))), 400
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
            if job is None or not job.is_dir() or not in_output(job):
                return protect(jsonify(slug=slug, project_id=None, available_for_resume=False)), 404
            marker = job / "cr-project.json"
            if marker.resolve().parent != job:
                raise ValueError("Unavailable project.")
            saved = _read(marker)
            project_id = saved.get("id")
            valid = isinstance(project_id, str) and bool(_PROJECT.fullmatch(project_id))
            live = getattr(studio, "JOBS", {}).get(slug) or {}
            # a saved "running" with no live job is stale: that video's Studio closed mid-run, so it can resume
            state = _text(cr_recover.effective_state(studio, slug, saved), 32)
            spec = saved.get("job_spec") if isinstance(saved.get("job_spec"), dict) else {}
            available = (valid and saved.get("brain_mode") == "claudecode"
                         and saved.get("purpose", "video") == "video"
                         and state not in {"done", "queued", "running", "held"})
            qc = _read(job / "sound_qc.json") if (job / "sound_qc.json").is_file() else {}
            sound = [{"state": _text(r.get("state"), 8), "text": _text(r.get("text"), 200)}
                     for r in (qc.get("lines") or []) if isinstance(r, dict)][:4]
            return protect(jsonify(slug=slug, project_id=project_id if valid else None,
                available_for_resume=bool(available), title=_text(live.get("title") or saved.get("title"), 300),
                style=_text(spec.get("style") or saved.get("style"), 140), status=state,
                link="/editor/" + quote(slug, safe=""), sound_check=sound))
        except Exception as error:
            return failure(error)
