"""ChannelRecipe Studio inside Claude Code: the folder's Claude Code settings, the style gallery and done notifications.

- configure(home) registers the `studio` tool server (cr_studio_tools.py, run by Studio's own Python) in .mcp.json next
  to the ChannelRecipe server, and merges .claude/settings.json: both servers approved, the free tools allowed, the
  paid ones (make_videos, clone_style) and the deletes (delete_video, delete_style) always asked, and the key files
  never read. Studio runs it on every start and cr_update after every update. It never copies a key anywhere;
  .mcp.json keeps its other entries untouched.
- install(studio, ...) adds the style gallery (/cr-gallery), whose "Use this style" records a pick that Claude reads
  back with get_style_pick, plus the read-only routes the tools need (finished videos with their files, a cloud quote).
- Watcher sends a desktop notification when a video (on this computer or Studio Cloud) or a style study finishes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
SERVER = "studio"
FREE_TOOLS = ("setup_check", "fix_graphics_browser", "fix_speech_model", "list_styles", "get_style_pick", "quote", "progress", "list_videos", "open_in_studio",
              "cloud_balance", "clone_style_look", "product_ideas", "products", "edit_style", "style_lab")
PAID_TOOLS = ("make_videos", "clone_style", "build_product", "resume_video", "style_sample")
# a video or a learned style moved into Studio's trash (cr_trash): the creator approves each one
DESTRUCTIVE_TOOLS = ("delete_video", "delete_style")
# ChannelRecipe's hosted niche database (the account's `channelrecipe` server beside `studio`): reading it is free
NICHE_TOOLS = ("list_niches", "list_niche_subjects", "list_niche_channels")
NEVER_READ = ("Read(./.env)", "Read(./.mcp.json)", "Read(./.cr-connection.json)", "Read(./.cr-runtime.json)")
PICK_SECONDS = 2 * 3600
PORT = 7860
POINTER = Path.home() / ".channelrecipe" / "studio-home.json"     # how the ChannelRecipe plugin finds this Studio
_PICK = re.compile(r"pk_[a-f0-9]{24}\Z")
_STYLE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}\Z")


# ── the folder's Claude Code configuration ────────────────────────────────────────────────────────────────────

def python_path(home: Path) -> str:
    """Studio's own Python, by absolute path (Claude Code starts the tool server with it)."""
    for candidate in (home / ".venv" / "Scripts" / "python.exe", home / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _load(path: Path):
    """The JSON object in path, {} when there is none, or None when it can't be read (left alone, never replaced)."""
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _save(path: Path, value: dict, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".cr-tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    if private:
        temporary.chmod(0o600)
    temporary.replace(path)


def _add(items, wanted) -> list:
    items = [x for x in items if isinstance(x, str)] if isinstance(items, list) else []
    return items + [x for x in wanted if x not in items]


def folder_settings(settings=None) -> dict:
    """The folder's .claude/settings.json: both servers approved, the free tools and niche reads allowed, the paid
    tools and the deletes always asked, the key files never read. Merged into what is there; the download ships it
    as is."""
    settings = dict(settings or {})
    settings["enabledMcpjsonServers"] = _add(settings.get("enabledMcpjsonServers"), ["channelrecipe", SERVER])
    permissions = dict(settings.get("permissions")) if isinstance(settings.get("permissions"), dict) else {}
    paid = [f"mcp__{SERVER}__{n}" for n in PAID_TOOLS + DESTRUCTIVE_TOOLS]
    free = [f"mcp__{SERVER}__{n}" for n in FREE_TOOLS] + [f"mcp__channelrecipe__{n}" for n in NICHE_TOOLS]
    # a paid tool or a delete is always asked, even when someone allowed the whole server
    permissions["allow"] = [x for x in _add(permissions.get("allow"), free) if x not in paid]
    permissions["ask"] = _add(permissions.get("ask"), paid)
    permissions["deny"] = _add(permissions.get("deny"), NEVER_READ)
    settings["permissions"] = permissions
    return settings


def first_run_servers() -> dict:
    """The download's .mcp.json: the studio tools from the very first Claude Code session, through the folder's
    Node launcher (Studio has no Python of its own yet). configure() replaces it with Studio's Python once Studio runs."""
    return {"mcpServers": {SERVER: {"type": "stdio", "command": "node",
                                    "args": ["${CLAUDE_PROJECT_DIR:-.}/cr_studio_launch.js"],
                                    "env": {"PYTHONIOENCODING": "utf-8"}}}}


def configure(home=None) -> dict:
    """Register the studio tool server and merge the folder's Claude Code settings. Returns what changed."""
    home = Path(home or HOME).resolve()
    changed = []
    servers_file = home / ".mcp.json"
    data = _load(servers_file)
    if data is not None:
        servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
        entry = {"type": "stdio", "command": python_path(home), "args": [str(home / "cr_studio_tools.py")],
                 "env": {"PYTHONIOENCODING": "utf-8"}}
        if servers.get(SERVER) != entry:
            servers[SERVER] = entry
            data["mcpServers"] = servers
            _save(servers_file, data, private=True)          # the ChannelRecipe entry beside it holds the account key
            changed.append(".mcp.json")
    settings_file = home / ".claude" / "settings.json"
    settings = _load(settings_file)
    if settings is not None:
        before = json.dumps(settings, sort_keys=True)
        settings = folder_settings(settings)
        if json.dumps(settings, sort_keys=True) != before:
            _save(settings_file, settings)
            changed.append(".claude/settings.json")
    try:
        if _load(POINTER) != {"home": str(home), "port": PORT}:
            _save(POINTER, {"home": str(home), "port": PORT})
    except OSError:
        pass
    return {"changed": changed}


# ── desktop notifications ─────────────────────────────────────────────────────────────────────────────────────

def notify(title: str, message: str, *, run=subprocess.Popen) -> bool:
    """A desktop notification (macOS Notification Centre, a Windows tray balloon). Best effort, never raises."""
    if os.environ.get("CR_NOTIFY", "1") == "0":
        return False
    title, message = " ".join(str(title).split())[:120], " ".join(str(message).split())[:240]
    try:
        if sys.platform == "darwin":
            script = (f"display notification {json.dumps(message)} with title {json.dumps(title)} "
                      f"subtitle \"ChannelRecipe Studio\" sound name \"Glass\"")
            run(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if os.name == "nt":
            quote = lambda s: "'" + s.replace("'", "''") + "'"         # noqa: E731 - PowerShell single quotes
            script = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
                      "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
                      f"$n.BalloonTipTitle = {quote(title)}; $n.BalloonTipText = {quote(message)}; $n.Visible = $true; "
                      "$n.ShowBalloonTip(8000); Start-Sleep -Seconds 9; $n.Dispose()")
            run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
    except (OSError, ValueError):
        pass
    return False


class Watcher:
    """Notices when a video or a style study finishes and says so on the desktop. Polls Studio's own state."""

    def __init__(self, studio, home: Path, *, notify_fn=notify, cloud=None):
        self.studio, self.home, self.notify = studio, Path(home), notify_fn
        self.cloud = cloud
        self.seen: dict = {}
        self.cloud_state = None
        self.studies: dict = {}

    def jobs(self) -> dict:
        jobs = getattr(self.studio, "JOBS", {}) or {}
        out = {}
        for slug, job in list(jobs.items()):
            if isinstance(job, dict) and not job.get("timeline"):
                out[slug] = (str(job.get("state") or ""), str(job.get("title") or slug), str(job.get("error") or ""))
        return out

    def tick(self) -> list:
        said = []
        for slug, (state, title, error) in self.jobs().items():
            before = self.seen.get(slug)
            self.seen[slug] = state
            if before in ("queued", "running") and state != before:
                if state == "done":
                    said.append(("Your video is ready", title))
                elif state == "error":
                    said.append(("Your video stopped", f"{title}: {error}" if error else title))
        cloud = self.cloud_task()
        if cloud is not None:
            state, title = cloud
            if self.cloud_state == "rendering" and state == "done":
                said.append(("Your cloud video is ready", title or "Studio Cloud finished rendering."))
            elif self.cloud_state in ("preparing", "starting", "rendering") and state == "failed":
                said.append(("Your cloud video stopped", title or "Studio Cloud stopped. Studio shows why."))
            self.cloud_state = state
        for operation, (state, label, change) in self.study_states().items():
            before = self.studies.get(operation)
            self.studies[operation] = state
            if before in ("running", "prepared") and state in ("done", "pending"):
                said.append(("Your style change is ready to compare" if change else "Your new style is ready", label))
            elif before in ("running", "prepared") and state == "failed":
                said.append(("Your style change stopped" if change else "Your style study stopped", label))
        for title, message in said:
            self.notify(title, message)
        return said

    def cloud_task(self):
        """Studio Cloud's state, refreshed from the farm when a render is under way (the Projects page's own poll)."""
        cloud = self.cloud
        if cloud is None:
            try:
                import cr_cloud as cloud
            except Exception:                                      # noqa: BLE001
                return None
        task = getattr(cloud, "TASK", None)
        if not isinstance(task, dict):
            return None
        if task.get("state") == "rendering":
            farm = getattr(self.studio, "FARM", None)
            if isinstance(farm, dict) and farm.get("state") in ("done", "failed", "idle"):
                try:
                    self.studio.app.test_client().get("/cr-cloud/progress", base_url="http://127.0.0.1:7860")
                except Exception:                                  # noqa: BLE001 - best effort
                    pass
        return str(task.get("state") or ""), str(task.get("title") or "")

    def study_states(self) -> dict:
        root = self.home / "output" / "cr-style-clone"
        out = {}
        try:
            folders = sorted(root.glob("op_*"), key=lambda p: p.stat().st_mtime, reverse=True)[:6]
        except OSError:
            return out
        for folder in folders:
            try:
                saved = json.loads((folder / "operation.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            state = {"complete": "done", "complete_local": "pending"}.get(saved.get("state"), saved.get("state"))
            label = str(((saved.get("spec") or {}).get("label")) or "Your learned style")[:80]
            out[folder.name] = (str(state or ""), label, saved.get("renderer_improve") is True)
        return out

    def run(self, every: float = 10.0) -> None:
        while True:
            try:
                self.tick()
            except Exception:                                      # noqa: BLE001 - notifications never stop Studio
                pass
            time.sleep(every)

    def start(self) -> None:
        if os.environ.get("CR_NOTIFY", "1") != "0":
            threading.Thread(target=self.run, daemon=True, name="cr-notify").start()


# ── the style gallery and the tools' read-only routes ─────────────────────────────────────────────────────────

class Picks:
    """One-time picks: list_styles opens one, the gallery records the creator's choice, get_style_pick reads it.
    Kept in memory (a restarted Studio asks for a new picker)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows: dict = {}

    def open(self) -> str:
        pick = "pk_" + secrets.token_hex(12)
        with self.lock:
            now = time.time()
            self.rows = {k: v for k, v in self.rows.items() if now - v["at"] < PICK_SECONDS}
            self.rows[pick] = {"at": now, "state": "waiting"}
        return pick

    def get(self, pick: str, claim: bool = False) -> dict:
        with self.lock:
            row = self.rows.get(pick)
            if row is None or time.time() - row["at"] > PICK_SECONDS:
                return {"pick_id": pick, "state": "expired",
                        "message": "This picker has expired. Call list_styles for a new one."}
            if row["state"] == "chosen" and claim:              # Claude has read it: the choice is final
                row["read"] = True
            return {"pick_id": pick, **{k: v for k, v in row.items() if k != "at"}}

    def choose(self, pick: str, choice: dict) -> dict:
        with self.lock:
            row = self.rows.get(pick)
            if row is None or time.time() - row["at"] > PICK_SECONDS:
                raise LookupError("This picker has expired. Ask Claude for the styles again.")
            if row.get("read"):
                raise PermissionError("Claude already has your choice. Ask it to show the styles again to change it.")
            row.update(state="chosen", chosen_at=time.time(), **choice)
            return {k: v for k, v in row.items() if k != "at"}


def install(studio, token: str, home=None) -> dict:
    """The gallery page, its pick routes, and the read-only routes the studio tools use."""
    from flask import Response, jsonify, request
    home = Path(home or HOME).resolve()
    app = studio.app
    picks = studio._cr_picks = getattr(studio, "_cr_picks", None) or Picks()

    def local_access():
        if request.host not in {"127.0.0.1:7860", "localhost:7860"} or request.headers.get("Origin") not in {
                None, "http://127.0.0.1:7860", "http://localhost:7860"}:
            raise PermissionError("Open the studio on localhost.")

    def protect(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def failure(error):
        if isinstance(error, PermissionError):
            return protect(jsonify(error=str(error))), 403
        if isinstance(error, LookupError):
            return protect(jsonify(error=str(error))), 404
        if isinstance(error, ValueError):
            return protect(jsonify(error=str(error))), 400
        app.logger.error("Claude Code request %s failed", request.path, exc_info=error)
        return protect(jsonify(error="Studio couldn't answer this. Your work is unchanged.")), 503

    @app.get("/cr-gallery")
    def cr_gallery_page():
        try:
            local_access()
            pick = request.args.get("pick", "")
            template = (home / "cr_gallery.html").read_text(encoding="utf-8")
            text = template.replace("__LOCAL_TOKEN_JSON__", json.dumps(token)).replace(
                "__PICK_JSON__", json.dumps(pick if _PICK.fullmatch(pick) else ""))
            response = protect(Response(text, mimetype="text/html"))
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-CR-Workspace"] = "1"            # a finished page: no setup banner injected
            return response
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    @app.post("/cr-gallery/picks")
    def cr_gallery_open_pick():
        try:
            local_access()                                         # the local token is checked for every POST
            return protect(jsonify(pick_id=picks.open()))
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    @app.get("/cr-gallery/picks/<pick>")
    def cr_gallery_pick(pick):
        try:
            local_access()
            if not _PICK.fullmatch(pick):
                raise ValueError("Unknown picker.")
            return protect(jsonify(picks.get(pick, claim=request.args.get("claim") == "1")))
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    @app.post("/cr-gallery/picks/<pick>")
    def cr_gallery_choose(pick):
        try:
            local_access()
            if not _PICK.fullmatch(pick):
                raise ValueError("Unknown picker.")
            body = request.get_json(silent=True) or {}
            if body.get("action") == "clone":
                choice = {"action": "clone", "youtube_url": str(body.get("youtube_url") or "")[:300]}
            else:
                style = str(body.get("style") or "")
                if not _STYLE.fullmatch(style):
                    raise ValueError("Choose a style.")
                rows = {r.get("name"): r for r in catalog_rows(studio)}
                if style not in rows:
                    raise ValueError("That style isn't installed.")
                row = rows[style]
                choice = {"action": "use", "style": style, "name": row.get("label") or style,
                          "where": body.get("where") if body.get("where") in ("this_computer", "studio_cloud") else ""}
            return protect(jsonify(picks.choose(pick, choice)))
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    @app.get("/cr-claude-code/home")
    def cr_claude_code_home():
        """Which Studio folder this is, so the tools of another copy on this computer can say so."""
        try:
            local_access()
            return protect(jsonify(home=str(home)))
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    @app.get("/cr-claude-code/videos")
    def cr_claude_code_videos():
        """Finished videos (the engine's own /api/jobs) with each one's file on this computer."""
        try:
            local_access()
            client = app.test_client()
            rows = (client.get("/api/jobs", base_url="http://127.0.0.1:7860").get_json(silent=True) or {}).get("jobs") or []
            out = []
            for row in rows:
                slug = str(row.get("slug") or "")
                path = ""
                try:
                    found = studio.mv.find_job(slug)
                    video = studio.mv.final_video(Path(found)) if found is not None else None
                    if video is not None and Path(video).is_file() and Path(video).resolve().is_relative_to(home):
                        path = str(Path(video).resolve())
                except Exception:                                  # noqa: BLE001 - the file path is a bonus
                    path = ""
                out.append({**{k: row.get(k) for k in ("slug", "title", "style", "mtime", "size", "desc")},
                            "path": path})
            return protect(jsonify(videos=out))
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    @app.get("/cr-claude-code/styles")
    def cr_claude_code_styles():
        """The styles as the Claude Code panes draw them: one card each, with small PNG and JPEG pictures on disk."""
        try:
            local_access()
            client = app.test_client()
            data = client.get("/cr-workspace/catalog", base_url="http://127.0.0.1:7860").get_json(silent=True) or {}
            rows = [r for r in data.get("channels") or [] if isinstance(r, dict) and _STYLE.fullmatch(str(r.get("name")))]
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(4) as pool:
                pictures = list(pool.map(lambda r: small_pictures(app, home, r), rows))
            return protect(jsonify(styles=[{**card(r), **p} for r, p in zip(rows, pictures)],
                                   prices=data.get("prices") or {}))
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    @app.post("/cr-claude-code/combine-preview")
    def cr_claude_code_combine_preview():
        """Two or three YouTube channels (or videos) listed as one preview, so one style can be learned from both."""
        try:
            local_access()                                         # the local token is checked for every POST
            body = request.get_json(silent=True) or {}
            urls = [str(u).strip() for u in body.get("urls") or [] if str(u).strip()]
            return protect(jsonify(combine_preview(home, urls)))
        except (RuntimeError, ValueError) as error:                 # YouTube couldn't list one, or a bad link
            return protect(jsonify(error=str(error))), 409 if isinstance(error, RuntimeError) else 400
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    @app.get("/cr-claude-code/quote")
    def cr_claude_code_quote():
        """What Studio Cloud would charge for a video of these minutes in this style (the server's own quote)."""
        try:
            local_access()
            import cr_cloud
            import requests
            minutes = max(1, min(int(request.args.get("minutes") or 10), 60))
            style = request.args.get("style", "")
            features = json.loads(request.args.get("features") or "{}")
            if not _STYLE.fullmatch(style) or not isinstance(features, dict):
                raise ValueError("Choose a style.")
            info = cr_cloud.status(home=home)
            if not info.get("available"):
                return protect(jsonify(available=False, why=info.get("why") or "Studio Cloud isn't available."))
            rows = {r.get("name"): r for r in catalog_rows(studio)}
            if style not in rows:
                raise ValueError("That style isn't installed.")
            why = cloud_note({**rows[style], "features": {"on": [k for k, v in features.items() if v]}})
            if why:
                return protect(jsonify(available=False, why=why, blocked=True))
            machine = "gpu" if cr_cloud.wants_gpu({"style": style, "features": features}) else "cpu"
            base, key = cr_cloud._connection(home)
            r = requests.post(base + "/api/studio/cloud/quote", json={"minutes": minutes, "machine": machine},
                              headers={"Authorization": "Bearer " + key}, timeout=20)
            if not r.ok:
                return protect(jsonify(available=False, why="channelrecipe.com couldn't price this right now."))
            return protect(jsonify(available=True, balance_usd=info.get("balance_usd"), topup_url=info.get("topup_url"),
                                   **r.json()))
        except Exception as error:                                 # noqa: BLE001
            return failure(error)

    studio._cr_watcher = Watcher(studio, home)                   # started by cr_studio.main, never in tests
    return {"gallery": "/cr-gallery", "watcher": studio._cr_watcher}


def one_line(text, limit=160) -> str:
    text = " ".join(str(text or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def cloud_note(row: dict) -> str:
    """Why a recipe renders on this computer only ('' when Studio Cloud can render it). Studio Cloud's own rule
    (cr_cloud.blocked) decides, so the gallery, the tools and the panels follow it whenever it changes."""
    name = str(row.get("name") or "")
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    on = features.get("on") if isinstance(features.get("on"), list) else features.get("offered") or []
    try:
        import cr_cloud
        why = cr_cloud.blocked({"style": name, "features": {key: True for key in on},
                                "panels": "panels" if row.get("panels") else ""})
    except Exception:                                          # noqa: BLE001 - when in doubt, this computer
        why = "Renders on this computer for now."
    if not why and row.get("panels"):
        why = "Reads your own panels, chosen on the Studio page."
    return why


def card(row: dict) -> dict:
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    return {"id": row["name"], "name": str(row.get("label") or row["name"]),
            "description": one_line(row.get("tagline") or row.get("description")),
            "family": str(row.get("family") or ""), "kind": "short" if row.get("short_seconds") else "video",
            "mine": bool(row.get("custom")), "cloud_note": cloud_note(row), "cost": one_line(row.get("cost"), 200),
            "short_seconds": row.get("short_seconds"),
            "features": {"offered": list(features.get("offered") or []),
                         "on": list(features.get("on") if isinstance(features.get("on"), list)
                                    else features.get("offered") or [])},
            "preview_label": str(row.get("preview_label") or ""),
            "preview_url": row.get("preview_url") if isinstance(row.get("preview_url"), str) else "",
            "preview_video_url": row.get("preview_video_url") if isinstance(row.get("preview_video_url"), str) else "",
            "art_url": row.get("art_url") if isinstance(row.get("art_url"), str) else ""}


def small_pictures(app, home: Path, row: dict) -> dict:
    """A style's picture as a small PNG (terminal panes) and JPEG (desktop panes), made once per preview version
    under .cr-claude-code/thumbs; its recipe artwork (SVG) when it has no preview."""
    url = row.get("preview_url")
    out = {"png": "", "jpg": "", "art": ""}
    art = row.get("art_url")
    if isinstance(art, str) and art.startswith("/cr-workspace/art/"):
        path = home / "workspace_art" / art.rsplit("/", 1)[-1]
        out["art"] = str(path) if path.is_file() else ""
    if not (isinstance(url, str) and url.startswith("/cr-workspace/preview/recipe/")):
        return out
    version = re.sub(r"[^a-f0-9]", "", url.partition("?v=")[2])[:16] or "latest"
    folder = home / ".cr-claude-code" / "thumbs"
    png, jpg = folder / f"{row['name']}-{version}-s.png", folder / f"{row['name']}-{version}-s.jpg"
    if not (png.is_file() and jpg.is_file()):
        try:
            data = b""
            for _ in range(4):
                answer = app.test_client().get(url, base_url="http://127.0.0.1:7860")
                if answer.status_code == 503:
                    time.sleep(1)
                    continue
                data = answer.get_data() if answer.status_code == 200 else b""
                break
            if data[:2] != b"\xff\xd8":
                return out
            import io
            from PIL import Image
            picture = Image.open(io.BytesIO(data)).convert("RGB")
            picture.thumbnail((320, 180))
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob(row["name"] + "-*-s.*"):
                old.unlink(missing_ok=True)
            picture.save(png, "PNG", optimize=True)
            picture.save(jpg, "JPEG", quality=72, optimize=True)
        except Exception:                                          # noqa: BLE001 - a card without a picture still shows
            return out
    out.update(png=str(png), jpg=str(jpg))
    return out


def combine_preview(home: Path, urls: list, *, preview=None) -> dict:
    """One saved preview holding the videos of two or three YouTube channels or videos, so the existing look and
    study take references from all of them (each candidate keeps its channel). Lists only: no model is asked. The
    Studio page combines them the same way (cr_style_clone.preview_many)."""
    import cr_style_clone
    if not 2 <= len(urls) <= 3 or len(set(urls)) != len(urls):
        raise ValueError("Give two or three different YouTube channels or videos to combine.")
    return cr_style_clone.preview_many(home, list(urls), preview=preview)


def catalog_rows(studio) -> list:
    client = studio.app.test_client()
    data = client.get("/cr-workspace/catalog", base_url="http://127.0.0.1:7860").get_json(silent=True) or {}
    return data.get("channels") or []


def main(argv=None) -> int:
    """`python cr_claude_code.py configure` (cr_update runs it after an update)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["configure"]:
        print(json.dumps(configure()))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
