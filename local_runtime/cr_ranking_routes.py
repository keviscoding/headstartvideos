"""The Clip Board for Ranking Shorts: find a topic's best moments (or add your own clips), tick and order the
countdown, then make the Short from exactly those clips.

The finding runs in the Ranking Shorts pipeline's own worker (shorts/ranking/board.py), one process per board, its
progress in the board's progress.log. Boards live in output/ranking-boards/<id>/: board.json (every moment found,
best first), choice-<stamp>.json (each countdown the creator made a Short from; choice.json is the latest),
uploads/ (their own clips), worker.json (the worker running now). Making the Short goes through the normal /api/run
with the board's id and its choice file in the recipe's [[ranking=...]] marker, so it queues, shows and recovers like
any other video, and a later pick on the same board never changes a Short already queued.

A worker is watched by a small guard (this file, run as a script): it lives in its own process group, so Stop ends
the worker with everything it started (yt-dlp, ffmpeg), and it ends that group the moment the Studio that started it
is gone, so a crashed or closed Studio never leaves a search spending clip watches unseen. Workers run below normal
priority, with UTF-8 text on every platform.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HOME = Path(__file__).resolve().parent
BOARD_ID = re.compile(r"[a-z0-9-]{1,80}")
CLIP_NAME = re.compile(r"[A-Za-z0-9_.-]{1,120}")
VIDEO_TYPES = {".mp4", ".mov", ".webm", ".m4v"}
MAX_CLIP_BYTES = 400 * 1024 * 1024
MAX_CLIPS = 20
LINKISH = re.compile(r"\s*(https?://|www\.)", re.I)


def boards_root(home: Path) -> Path:
    return home / "output" / "ranking-boards"


def new_board_id(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:48] or "board"
    return f"{slug}-{secrets.token_hex(3)}"


def board_dir(home: Path, board_id: str) -> Path:
    if not BOARD_ID.fullmatch(board_id or ""):
        raise ValueError("That Clip Board could not be found.")
    path = boards_root(home) / board_id
    if not path.is_dir():
        raise ValueError("That Clip Board could not be found.")
    return path


def clean_link(url: str) -> str:
    """A YouTube link as the one video it shows: a link copied inside a playlist or a Mix carries list=, and yt-dlp
    then fetches the whole playlist (the card gets another video's title, and every entry is one more request toward
    YouTube's bot check). Other links pass as they are."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not (host == "youtu.be" or host.endswith("youtube.com")) or parts.path.rstrip("/") == "/playlist":
        return url
    keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k in ("v", "t")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(keep), ""))


def summary(path: Path, running: bool) -> dict:
    """What the page shows: the topic, whether it is working, its last progress lines, and every moment."""
    moments = []
    if (path / "board.json").is_file():
        try:
            moments = json.loads((path / "board.json").read_text(encoding="utf-8"))
        except ValueError:
            moments = []
    log = (path / "progress.log").read_text(encoding="utf-8", errors="replace").splitlines()[-6:] \
        if (path / "progress.log").is_file() else []
    keep = ("id", "vid", "title", "channel", "views", "type", "score", "label", "what", "payoff_is", "why", "payoff",
            "cut", "framing", "quality", "own", "dur", "linked", "picked_by_you")
    cards = []
    for m in moments if isinstance(moments, list) else []:
        card = {k: m.get(k) for k in keep if k in m}
        if m.get("file"):                                       # a clip on this computer: the page previews the file
            card["clip"] = Path(m["file"]).name
        cards.append(card)
    stopped = [line for line in log if " stopped " in line]
    return {"id": path.name, "topic": (path / "topic.txt").read_text(encoding="utf-8").strip()
            if (path / "topic.txt").is_file() else "", "running": running,
            "progress": [re.sub(r"^\d\d:\d\d:\d\d ", "", line) for line in log], "moments": cards,
            "stopped": stopped[-1].split(" stopped ", 1)[1] if stopped and not running else ""}


# ── the worker's process: its own group, low priority, ended with the Studio ───────────────────────────────────────
def _alive(pid: int) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)     # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT: still running
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ended(pid: int) -> bool:
    """POSIX: has this process ended? A child of ours is reaped here (a zombie would still answer kill 0)."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == pid
    except ChildProcessError:
        return not _alive(pid)


def stop_tree(pid: int, wait=None) -> None:
    """End a worker's guard and everything in its process group (the guard leads its own group). `wait` says whether
    the guard has ended (Popen.poll); without one, it is checked by its pid."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15, check=False)
    else:
        ended = wait or (lambda: _ended(pid))
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            if sig == signal.SIGTERM:
                for _ in range(30):                             # give yt-dlp and ffmpeg 3 s to clean up
                    if ended():
                        break
                    time.sleep(0.1)
        ended()
    if wait is not None:
        wait()


def guard(argv: list) -> int:
    """`python cr_ranking_routes.py --guard STUDIO_PID -- COMMAND...`: run COMMAND, and end it (and what it started)
    as soon as the Studio with STUDIO_PID is gone."""
    parent, command = int(argv[0]), argv[argv.index("--") + 1:]
    proc = subprocess.Popen(command)
    try:
        while proc.poll() is None:
            if not _alive(parent) or (os.name != "nt" and os.getppid() != parent):
                print(time.strftime("%H:%M:%S") + " stopped The Studio closed, so this search stopped with it. "
                      "Watches already made are saved and won't be charged again.", flush=True)
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
                else:
                    os.killpg(os.getpgrp(), signal.SIGKILL)      # the whole group, this guard included
                return 1
            time.sleep(1)
        return proc.returncode
    except KeyboardInterrupt:
        return 130


def worker_record(path: Path) -> dict:
    try:
        got = json.loads((path / "worker.json").read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def busy(home=None) -> bool:
    """Is any Clip Board worker of this Studio working? (For an in-place restart: it would orphan the search.)"""
    root = boards_root(Path(home or HOME))
    for path in root.iterdir() if root.is_dir() else []:
        rec = worker_record(path)
        if rec.get("studio") == os.getpid() and _alive(rec.get("pid")):
            return True
    return False


def install(studio, *, token, home=None):
    from flask import Response, jsonify, request, send_file
    import atexit
    import cr_shorts
    home = Path(home or HOME).resolve()
    app = studio.app
    workers: dict[str, subprocess.Popen] = {}
    lock = threading.Lock()

    def local_access(mutation=False):
        if request.host not in {"127.0.0.1:7860", "localhost:7860"}:
            raise PermissionError("Open the studio on localhost.")
        if request.headers.get("Origin") not in {None, "http://127.0.0.1:7860", "http://localhost:7860"}:
            raise PermissionError("Open the studio on localhost.")
        if mutation and not secrets.compare_digest(request.headers.get("X-CR-Local-Token", ""), token):
            raise PermissionError("Refresh this page and try again.")

    def no_store(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def failed(error):
        if isinstance(error, PermissionError):
            return jsonify(error=str(error)), 403
        if isinstance(error, ValueError):
            return jsonify(error=str(error)), 400
        if isinstance(error, RuntimeError):
            return jsonify(error=str(error)), 409
        app.logger.error("Clip Board request %s failed", request.path, exc_info=error)
        return jsonify(error="The Clip Board could not complete this step. Your board is saved; try again."), 500

    def pipeline() -> Path:
        script = home / "shorts" / "ranking" / "board.py"
        if not script.is_file():
            raise RuntimeError("The Ranking Shorts pipeline is not installed in this Studio. Update the Studio.")
        return script

    def live_pid(path: Path):
        """The guard pid of the worker running on this board: one this Studio process started (kept in memory), or
        one it started before an in-place restart (worker.json; the Studio keeps its pid across that restart)."""
        proc = workers.get(path.name)
        if proc is not None:
            return proc.pid if proc.poll() is None else None
        rec = worker_record(path)
        if rec.get("studio") == os.getpid() and _alive(rec.get("pid")):
            return rec["pid"]
        return None

    def running(board_id: str) -> bool:
        with lock:
            try:
                return live_pid(boards_root(home) / board_id) is not None
            except OSError:
                return False

    def start(path: Path, args: list, use_credits: bool = False):
        with lock:
            if live_pid(path) is not None:
                raise RuntimeError("This board is still working. Wait for it to finish, or stop it.")
            script = pipeline()
            out = open(path / "progress.log", "a", encoding="utf-8")
            env = cr_shorts.child_env(CR_STUDIO=home, CR_RANKING=json.dumps({"use_credits": bool(use_credits)}))
            options = cr_shorts.low_priority()
            if os.name == "nt":
                options["creationflags"] = options.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True              # Stop ends the whole group, ffmpeg and yt-dlp too
            command = [sys.executable, str(Path(__file__).resolve()), "--guard", str(os.getpid()), "--",
                       sys.executable, str(script), str(home), str(path), *args]
            try:
                proc = subprocess.Popen(command, cwd=str(script.parent), env=env, stdout=out, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, **options)
            finally:
                out.close()
            cr_shorts.lower_priority(proc)
            workers[path.name] = proc
            try:
                (path / "worker.json").write_text(json.dumps({"pid": proc.pid, "studio": os.getpid(),
                                                              "started": time.time(), "args": args[:1]}), encoding="utf-8")
            except OSError:
                pass

    def stop_all():
        """The Studio is closing: no board keeps searching (and spending watches) without it."""
        with lock:
            for proc in list(workers.values()):
                if proc.poll() is None:
                    try:
                        stop_tree(proc.pid, wait=lambda p=proc: p.poll() is not None)
                    except Exception:                           # noqa: BLE001 - the guard ends it on its own too
                        pass
    atexit.register(stop_all)

    @app.get("/cr-ranking")
    def cr_ranking_page():
        try:
            local_access()
            template = (home / "cr_ranking.html").read_text(encoding="utf-8")
            return no_store(Response(template.replace("__LOCAL_TOKEN_JSON__", json.dumps(token)), mimetype="text/html"))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.get("/cr-ranking/allowance")
    def cr_ranking_allowance():
        try:
            local_access()
            import cr_bridge
            return no_store(jsonify(cr_bridge.call("ranking/allowance")))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.get("/cr-ranking/boards")
    def cr_ranking_boards():
        try:
            local_access()
            root = boards_root(home)
            rows = []
            for path in sorted(root.iterdir() if root.is_dir() else [], key=lambda p: -p.stat().st_mtime)[:12]:
                if path.is_dir() and BOARD_ID.fullmatch(path.name):
                    s = summary(path, running(path.name))
                    rows.append({"id": s["id"], "topic": s["topic"], "moments": len(s["moments"]), "running": s["running"]})
            return no_store(jsonify(boards=rows))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.post("/cr-ranking/boards")
    def cr_ranking_new_board():
        try:
            local_access(True)
            body = request.get_json(silent=True) or {}
            topic = re.sub(r"\s+", " ", str(body.get("topic") or "")).strip()[:120]
            if len(topic) < 3:
                raise ValueError("Say what you want to rank, for example Funniest Dog Moments.")
            if LINKISH.match(topic):
                raise ValueError("That looks like a link. Type what to rank here, for example Funniest Dog Moments; "
                                 "to use clips you have links to, choose Paste links.")
            path = boards_root(home) / new_board_id(topic)
            path.mkdir(parents=True)
            (path / "topic.txt").write_text(topic, encoding="utf-8")
            if body.get("find", True):
                start(path, ["find", topic], body.get("use_credits") is True)
            return no_store(jsonify(summary(path, running(path.name))))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.get("/cr-ranking/boards/<board_id>")
    def cr_ranking_board(board_id):
        try:
            local_access()
            return no_store(jsonify(summary(board_dir(home, board_id), running(board_id))))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.post("/cr-ranking/boards/<board_id>/stop")
    def cr_ranking_stop(board_id):
        """Stop this board's search or clip watching now: what it already watched stays saved (never paid twice)."""
        try:
            local_access(True)
            path = board_dir(home, board_id)
            with lock:
                pid = live_pid(path)
                proc = workers.get(board_id)
            if pid is not None:
                stop_tree(pid, wait=(lambda: proc.poll() is not None) if proc is not None else None)
                with open(path / "progress.log", "a", encoding="utf-8") as log:
                    log.write(time.strftime("%H:%M:%S") + " stopped You stopped this board. Watches already made are "
                              "saved and won't be charged again.\n")
            (path / "worker.json").unlink(missing_ok=True)
            return no_store(jsonify(summary(path, running(board_id))))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.post("/cr-ranking/boards/<board_id>/clips")
    def cr_ranking_add_clips(board_id):
        try:
            local_access(True)
            path = board_dir(home, board_id)
            if running(board_id):                               # before saving: no orphan uploads on a refusal
                raise RuntimeError("This board is still working. Wait for it to finish, or stop it.")
            if (request.content_length or 0) > MAX_CLIPS * MAX_CLIP_BYTES + 1024 * 1024:
                raise ValueError(f"Add up to {MAX_CLIPS} clips at a time, each under 400 MB.")
            files = request.files.getlist("clips")
            if not files:
                raise ValueError("Choose one or more video clips.")
            uploads = path / "uploads"
            uploads.mkdir(exist_ok=True)
            saved = []
            for f in files[:MAX_CLIPS]:
                ext = Path(f.filename or "").suffix.lower()
                if ext not in VIDEO_TYPES:
                    raise ValueError("Add MP4, MOV, WebM or M4V clips.")
                name = f"clip-{int(time.time())}-{secrets.token_hex(2)}{ext}"
                dest = uploads / name
                f.save(dest)
                if dest.stat().st_size > MAX_CLIP_BYTES:
                    dest.unlink()
                    raise ValueError("Keep each clip under 400 MB.")
                saved.append(str(dest))
            start(path, ["add", *saved], request.form.get("use_credits") == "1")
            return no_store(jsonify(summary(path, running(board_id))))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.post("/cr-ranking/boards/<board_id>/links")
    def cr_ranking_add_links(board_id):
        """Links the creator pasted (YouTube, TikTok, Instagram ...): the worker fetches and watches them."""
        try:
            local_access(True)
            path = board_dir(home, board_id)
            body = request.get_json(silent=True) or {}
            urls = [u.strip() for u in body.get("links") or [] if isinstance(u, str)]
            urls = [clean_link(u) for u in urls if re.fullmatch(r"https?://[^\s\"'<>]{4,600}", u)][:MAX_CLIPS]
            if not urls:
                raise ValueError("Paste one or more links to clips (one a line).")
            start(path, ["links", *dict.fromkeys(urls)], body.get("use_credits") is True)
            return no_store(jsonify(summary(path, running(board_id))))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.get("/cr-ranking/boards/<board_id>/clips/<name>")
    def cr_ranking_clip(board_id, name):
        try:
            local_access()
            if not CLIP_NAME.fullmatch(name or "") or name.startswith("."):
                raise ValueError("That clip could not be found.")
            clip = board_dir(home, board_id) / "uploads" / name
            if not clip.is_file():
                raise ValueError("That clip could not be found.")
            return no_store(send_file(clip, conditional=True))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)

    @app.post("/cr-ranking/boards/<board_id>/choice")
    def cr_ranking_choice(board_id):
        """The countdown in play order (the last is #1), saved as its own file for the Short about to be queued: a
        Short waiting behind another video reads exactly this countdown when it starts, whatever the creator picks on
        the board meanwhile. Its name goes into the Short's options ("choice"); choice.json keeps the latest."""
        try:
            local_access(True)
            path = board_dir(home, board_id)
            body = request.get_json(silent=True) or {}
            ids = body.get("picks")
            if not isinstance(ids, list) or not 3 <= len(ids) <= 10 or len(set(map(str, ids))) != len(ids):
                raise ValueError("Pick between 3 and 10 different clips.")
            try:
                board = json.loads((path / "board.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                board = []
            moments = {str(m.get("id")): m for m in board if isinstance(m, dict)}
            picks = [moments[str(i)] for i in ids if str(i) in moments]
            if len(picks) != len(ids):
                raise ValueError("Some of those clips are no longer on this board. Refresh and pick again.")
            topic = re.sub(r"\s+", " ", str(body.get("topic") or "")).strip()[:120]
            if len(topic) >= 3 and not LINKISH.match(topic):     # the creator named the countdown: the Short's title
                (path / "topic.txt").write_text(topic, encoding="utf-8")
            data = json.dumps({"picks": picks}, indent=1, ensure_ascii=False)
            name = f"choice-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.json"
            (path / name).write_text(data, encoding="utf-8")
            (path / "choice.json").write_text(data, encoding="utf-8")
            return no_store(jsonify(ok=True, board=board_id, choice=name,
                                    topic=(path / "topic.txt").read_text(encoding="utf-8").strip()))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--guard":
    raise SystemExit(guard(sys.argv[2:]))
