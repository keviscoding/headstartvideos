"""The Clip Board for Ranking Shorts: find a topic's best moments (or add your own clips), tick and order the
countdown, then make the Short from exactly those clips.

The finding runs in the Ranking Shorts pipeline's own worker (shorts/ranking/board.py), one process per board, its
progress in the board's progress.log. Boards live in output/ranking-boards/<id>/: board.json (every moment found,
best first), choice.json (the creator's countdown), uploads/ (their own clips). Making the Short goes through the
normal /api/run with the board's id in the recipe's [[ranking=...]] marker, so it queues, shows and recovers like
any other video.
"""
from __future__ import annotations

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
BOARD_ID = re.compile(r"[a-z0-9-]{1,80}")
CLIP_NAME = re.compile(r"[A-Za-z0-9_.-]{1,120}")
VIDEO_TYPES = {".mp4", ".mov", ".webm", ".m4v"}
MAX_CLIP_BYTES = 400 * 1024 * 1024


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
            "cut", "framing", "quality", "own", "dur")
    cards = []
    for m in moments if isinstance(moments, list) else []:
        card = {k: m.get(k) for k in keep if k in m}
        if m.get("own") and m.get("file"):
            card["clip"] = Path(m["file"]).name
        cards.append(card)
    stopped = [line for line in log if " stopped " in line]
    return {"id": path.name, "topic": (path / "topic.txt").read_text(encoding="utf-8").strip()
            if (path / "topic.txt").is_file() else "", "running": running,
            "progress": [re.sub(r"^\d\d:\d\d:\d\d ", "", line) for line in log], "moments": cards,
            "stopped": stopped[-1].split(" stopped ", 1)[1] if stopped and not running else ""}


def install(studio, *, token, home=None):
    from flask import Response, jsonify, request, send_file
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

    def running(board_id: str) -> bool:
        with lock:
            proc = workers.get(board_id)
            return bool(proc and proc.poll() is None)

    def start(path: Path, args: list, use_credits: bool = False):
        with lock:
            proc = workers.get(path.name)
            if proc and proc.poll() is None:
                raise RuntimeError("This board is still working. Wait for it to finish.")
            out = open(path / "progress.log", "a", encoding="utf-8")
            env = {**os.environ, "CR_STUDIO": str(home), "PYTHONUNBUFFERED": "1",
                   "CR_RANKING": json.dumps({"use_credits": bool(use_credits)})}
            workers[path.name] = subprocess.Popen([sys.executable, str(pipeline()), str(home), str(path), *args],
                                                  cwd=str(pipeline().parent), env=env, stdout=out,
                                                  stderr=subprocess.STDOUT)
            out.close()

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

    @app.post("/cr-ranking/boards/<board_id>/clips")
    def cr_ranking_add_clips(board_id):
        try:
            local_access(True)
            path = board_dir(home, board_id)
            files = request.files.getlist("clips")
            if not files:
                raise ValueError("Choose one or more video clips.")
            uploads = path / "uploads"
            uploads.mkdir(exist_ok=True)
            saved = []
            for f in files[:20]:
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
        """The countdown in play order (the last is #1): saved for make_ranking.py."""
        try:
            local_access(True)
            path = board_dir(home, board_id)
            ids = (request.get_json(silent=True) or {}).get("picks")
            if not isinstance(ids, list) or not 3 <= len(ids) <= 10 or len(set(map(str, ids))) != len(ids):
                raise ValueError("Pick between 3 and 10 different clips.")
            moments = {str(m.get("id")): m for m in json.loads((path / "board.json").read_text(encoding="utf-8"))}
            picks = [moments[str(i)] for i in ids if str(i) in moments]
            if len(picks) != len(ids):
                raise ValueError("Some of those clips are no longer on this board. Refresh and pick again.")
            (path / "choice.json").write_text(json.dumps({"picks": picks}, indent=1, ensure_ascii=False), encoding="utf-8")
            return no_store(jsonify(ok=True, board=board_id, topic=(path / "topic.txt").read_text(encoding="utf-8").strip()))
        except Exception as error:                                  # noqa: BLE001
            return failed(error)
