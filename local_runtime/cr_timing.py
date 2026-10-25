"""Picture timing in the timeline editor: change when each picture appears against the voice, without prompting.

A customer (thatguygab, 2026-10-25): "Image generation and VO are on point. The problem is the timing. Prompting makes
it even longer to fix." The engine's timeline editor (/editor/<slug>: the private engine's editor.html and timeline.py)
can already move a cut: dragging a shot's edge sends its own `retime` op, which keeps the narration's clock, saves the
plan with an Undo snapshot and renders only the changed shots for the preview. But the edge is a 7-pixel strip that
only shows on hover, the cut lands wherever the mouse lets go, and nothing says which word is spoken there.

This add-on is loaded into that page (cr_timing.js and .css; the editor itself is not copied or changed) and sends
the same `retime` op through the editor's own op() call, so a timing change saves, previews, undoes and renders
exactly like the editor's own edits:

- a Timing panel on the selected picture: the words spoken around its start and its end (click one and the picture
  starts or ends on it), 0.1 s nudges, "At playhead", and Hear (the picture and voice across the cut);
- dragging an edge snaps to the nearest spoken word (Alt drags freely) and plays the word it lands on;
- the words written along the voice track, and keys: [ and ] at the playhead, Alt+arrows nudge the start
  (Alt+Shift+arrows the end).

The words come from the voice's own word clock (words_heard.json, which the engine's speech timing makes once), or,
for a video without it, from its captions with each word placed by its letters.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

HOME = Path(__file__).resolve().parent
_SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}\Z")
_SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d+)\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d+)")
MAX_WORDS = 40_000
ASSETS = {"timing.js": ("cr_timing.js", "application/javascript"), "timing.css": ("cr_timing.css", "text/css")}
TAGS = '<link rel="stylesheet" href="/cr-editor/timing.css"><script src="/cr-editor/timing.js" defer></script>'
LOCAL = ("127.0.0.1:7860", "localhost:7860")                   # Studio's own address; nothing else may read these


def _seconds(hours, minutes, seconds, fraction) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction) / (10 ** len(fraction))


def heard_words(job: Path) -> list:
    """[[word, start, end]] from the voice's own word clock, or [] when the video has none."""
    try:
        rows = json.loads((Path(job) / "words_heard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        try:
            word, start, end = str(row[0]).strip(), float(row[1]), float(row[2])
        except (TypeError, ValueError):
            continue
        if word and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end:
            out.append([word[:40], round(start, 3), round(end, 3)])
        if len(out) >= MAX_WORDS:
            break
    out.sort(key=lambda row: row[1])
    return out


def caption_words(job: Path) -> list:
    """[[word, start, end]] from the captions (subs.srt): each cue's words share its time by their letters."""
    try:
        text = (Path(job) / "subs.srt").read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        return []
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        at = next((n for n, line in enumerate(lines) if _SRT_TIME.search(line)), None)
        if at is None:
            continue
        g = _SRT_TIME.search(lines[at]).groups()
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        words = " ".join(lines[at + 1:]).split()
        if not words or end <= start:
            continue
        weights = [len(re.sub(r"\W", "", w)) + 1 for w in words]
        total, done = float(sum(weights)), 0.0
        for word, weight in zip(words, weights):
            a = start + (end - start) * done / total
            done += weight
            out.append([word[:40], round(a, 3), round(start + (end - start) * done / total, 3)])
            if len(out) >= MAX_WORDS:
                return out
    return out


def words(job: Path) -> dict:
    heard = heard_words(job)
    if heard:
        return {"source": "voice", "words": heard}
    return {"source": "captions", "words": caption_words(job)}


def with_addon(html: str) -> str:
    """The editor's page with the timing add-on loaded after its own script (once)."""
    if "/cr-editor/timing.js" in html:
        return html
    at = html.lower().rfind("</body>")
    return html[:at] + TAGS + html[at:] if at >= 0 else html + TAGS


def install(studio, *, home=None) -> bool:
    """Add the add-on's routes and load it into the editor page. False (nothing changed) on an engine without the
    timeline editor."""
    from flask import Response, jsonify, request, send_file
    app = studio.app
    home = Path(home or HOME).resolve()
    editors = [rule.endpoint for rule in app.url_map.iter_rules()
               if rule.rule == "/editor/<slug>" and "GET" in rule.methods]
    if len(editors) != 1:
        return False
    original = app.view_functions[editors[0]]

    def local_access():
        if request.host not in LOCAL or request.headers.get("Origin") not in {None, *("http://" + h for h in LOCAL)}:
            raise PermissionError("Open the studio on localhost.")

    def protect(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def editor_page(*args, **kwargs):
        response = app.make_response(original(*args, **kwargs))
        if response.status_code == 200 and response.mimetype == "text/html" and not response.direct_passthrough:
            response.set_data(with_addon(response.get_data(as_text=True)))
        return response

    app.view_functions[editors[0]] = editor_page

    @app.get("/cr-editor/<name>")
    def cr_editor_asset(name):
        try:
            local_access()
        except PermissionError as error:
            return protect(jsonify(error=str(error))), 403
        if name not in ASSETS:
            return protect(jsonify(error="Unknown editor file.")), 404
        filename, mimetype = ASSETS[name]
        path = home / filename
        if not path.is_file() or path.is_symlink() or path.resolve().parent != home:
            return protect(jsonify(error="Unknown editor file.")), 404
        return protect(send_file(path, mimetype=mimetype, conditional=False))

    @app.get("/cr-editor/words/<slug>")
    def cr_editor_words(slug):
        """The words of a video's narration with their times, for the timing panel and the voice track."""
        try:
            local_access()
        except PermissionError as error:
            return protect(jsonify(error=str(error))), 403
        if not _SLUG.fullmatch(slug) or slug in {".", ".."}:
            return protect(jsonify(error="Unavailable video.")), 400
        found = studio.mv.find_job(slug)
        job = Path(found).resolve() if found is not None else None
        if job is None or not job.is_dir() or not job.is_relative_to(home / "output"):
            return protect(jsonify(error="That video is not on this computer any more.")), 404
        return protect(jsonify(slug=slug, **words(job)))

    return True
