"""Style learning: Gemini watches a reference video for a Studio style study.

Studio measures a reference on the customer's computer (cuts, motion, light, the
shape of the sound). What numbers can't say, Gemini watches straight from the
YouTube link with ChannelRecipe's own key, so customers need no Google account:
what kind of picture each shot is and how it's drawn, who keeps coming back,
what moves, what the music and sound effects do, what makes the channel
recognisable.

Bounded so a study can't run up the bill:
- only Studio plans, with a daily number of watches per account;
- fixed window lengths;
- a global daily spend cap (past it, studies continue with the free measurements);
- a shared cache: one window of a public video is watched once for everyone.
Every call records its tokens and cost.

A watch takes longer than the hosting's 100 s request limit, so it runs as a job:
POST starts it (or returns the cached log), GET polls it.

ChannelRecipe's admins can also have a video Studio made watched (watch-upload): a light copy goes to Gemini's file
service and is logged with the very same instructions, so a test video is compared with its references shot by shot.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from webapp import database as db
from webapp import plans
from webapp.frontier_studio import is_admin

PROMPT_VERSION = 4
MAX_WINDOW_S = 200
BUSY_WAITS_S = (15, 45, 90)                     # retries when Gemini is busy (the job has time; Studio is polling)
# what the creator reads when Gemini's answer can't be read twice over (never the parser's own words: a customer saw
# "Expecting ',' delimiter: line 6 column 27 (char 523)", 2026-10-27)
UNREADABLE = "Gemini's notes on this part of the video came back unreadable, even when asked a second time"
UNANSWERED = "Gemini gave no notes on this part of the video"
STALE_S = 900
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
UPLOAD_BASE = "https://generativelanguage.googleapis.com/upload/v1beta/files"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024             # a light copy of a test video (360p), not a finished master
UPLOAD_MIMES = ("video/mp4", "video/webm", "video/quicktime")
# A day's watches per account. A study watches up to three references: the opening, the middle, and a call to action.
DAILY_WATCHES = {"daily": 20, "studio_pro": 40, "studio_max": 60}
_ready = set()
_ready_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="style-watch")
_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")

SOURCES = ("ai_image", "ai_video", "live_footage", "archival_footage", "archival_photo", "stock_footage",
           "news_clip", "bodycam_cctv", "interview", "screenshot_web", "document", "map", "chart", "3d_render",
           "drawing", "painting", "stick_figure", "cutout", "manga_panel", "motion_graphic", "text_card",
           "meme_gif", "presenter", "avatar", "other")
MOTIONS = ("still", "zoom_in", "zoom_out", "pan", "shake", "animated", "live_action", "motion_graphic",
           "cutout_move")

WATCH_PROMPT = """You are logging the PRODUCTION STYLE of this YouTube video between [A] and [B], so that a studio
can make new videos in the same style about other topics. Do not summarise the story; describe how it is made.
Watch every shot and listen to the whole soundtrack, including what plays quietly under the voice.

Return ONE JSON object with exactly these keys:
{
 "narration": "voiceover | presenter_on_camera | avatar | mixed | none",
 "shots": [   // every shot in order; a shot ends at any cut or transition
   {"t": "m:ss", "len_s": 2.5,
    "source": "<one of: [SOURCES]>",
    "style": "how the picture is MADE, never what it shows: medium, line, shading, colour, rendering (e.g.
              'watercolour wash, soft edges', 'low-poly 3D, flat pastel', 'grainy bodycam', 'flat vector, bold
              outlines', 'black-and-white manga ink')",
    "what": "what it shows, at most six words",
    "polish": "cinematic | clean | candid | lo-fi",
    "motion": "<one of: [MOTIONS]>",
    "text": "none | caption | label | headline | quote | handwritten | kinetic | data | cta",
    "who": "short stable name of a recurring character or presenter in this shot, or empty",
    "audio": "narration | source_audio | music_only | ambience | silence"}
 ],
 "audio_events": [{"t": "m:ss", "kind": "music_in | music_change | music_out | music_drop | riser | hit | whoosh |
                   foley | ambience | source_clip | silence", "what": "a few words"}],
 "music": {"share": 0.0, "role": "story score | ambient bed | lofi | upbeat | none",
           "genre": "e.g. lofi hip-hop, ambient piano, dark synth drone, cinematic orchestral, acoustic folk, upbeat
                     electronic",
           "beat": "none | soft | driving",
           "moods": ["..."], "instruments": ["..."], "under_voice": "loud | moderate | quiet | none",
           "changes": "how often and where the music changes"},
 "sfx": {"density": "none | sparse | moderate | dense", "kinds": ["..."], "on": "what triggers them"},
 "ambience": {"present": false, "kinds": ["rain, wind, birds, forest, city, crowd, room tone, fire, waves ..."],
              "share": 0.0, "where": "under the voice, in pauses, or on empty shots"},
 "voice": {"gender": "...", "age": "young | adult | older", "accent": "...", "tone": "...",
           "pace": "slow | moderate | fast"},
 "look": {"grade": "dark | neutral | bright", "filter": "colour treatment, overlays, texture", "grain": "none | light |
          heavy", "palette": ["#RRGGBB"]},
 "polish": {"overall": "cinematic | clean | candid | lo-fi", "notes": "what makes the pictures look that way"},
 "art_style": "one or two sentences: the picture style a new video must keep, specific enough to prompt an image
               generator (medium, line, shading, colour, how characters are drawn, backgrounds, camera and light)",
 "lettering": "hand-drawn | comic | typeset | mixed | none",
 "text_style": "how on-screen words look and move, or empty",
 "graphics": ["recurring graphic devices: maps, charts, highlighted documents, logos, timelines, counters ..."],
 "recurring": [{"name": "...", "look": "how to draw them consistently", "role": "mascot | presenter | character"}],
 "people_3d": "faceless | designed | no_people | none",
 "presenter": {"on_camera_share": 0.0, "pictures_share": 0.0},
 "cta": [{"t": "m:ss", "kind": "sponsor | own product | ebook or pdf | course | qr code | link | subscribe",
          "what": "what is promoted and how it is shown (cover, QR code, link, price, the host holding it)"}],
 "hook": "how the first seconds grab attention, if [A] is 0:00; else empty",
 "breathing": "where the edit pauses (music swells, empty shots, silence) and how long",
 "recognisable": ["the 3 to 6 things that make this channel instantly recognisable, most important first"]
}
Definitions: "polish" is how finished the picture looks: cinematic = dramatic light, shallow focus, graded; clean =
evenly lit, sharp, staged like an advert; candid = everyday phone-camera look, natural indoor or daylight, unstaged
framing, a little soft or noisy; lo-fi = visibly degraded, compressed, VHS or webcam. "presenter.on_camera_share" is
the fraction of the window where a presenter or avatar talks to camera; "pictures_share" where they appear in other
pictures. "cta" lists every spoken or shown promotion. "music.share" is the fraction of the window with audible music,
however quiet; "music.beat" is whether it has drums or a pulse (lofi hip-hop has a soft beat, ambient pads and drones
have none). "lettering" is how the words on screen LOOK, whether drawn by hand or set in a font that imitates it:
hand-drawn = handwriting, marker, chalk or brush letters; comic = bold comic-book display letters (outlined, bouncy,
uneven) and speech bubbles; typeset = an ordinary clean font set straight; mixed = both kinds often. "people_3d" is
how the people and creatures in the 3D-rendered pictures are built, judged by their FACES: faceless = every human
figure's face is blank, with no eyes, mouth or face markings, even when it has hair, glasses, a hat, a helmet or
clothes (mannequins, dummies, smooth grey or white figures); designed = most figures have a face of any kind (eyes
or a mouth, even tiny dots, slits, a decal or an emoticon, or a realistic CGI face), or are not human mannequins at all:
a ball, block or toy-shaped head, a game, toy or cartoon avatar, clay, felt or puppet figures, animals, creatures or
objects with faces; no_people = 3D pictures with no people or creatures; none = no 3D pictures. Use the
vocabularies exactly. If something is absent, use an empty list, "none", false, 0 or an empty string; never invent."""


def prompt(start: float, end: float) -> str:
    return (WATCH_PROMPT.replace("[A]", _mmss(start)).replace("[B]", _mmss(end))
            .replace("[SOURCES]", " | ".join(SOURCES)).replace("[MOTIONS]", " | ".join(MOTIONS)))


def _mmss(t: float) -> str:
    t = int(round(t))
    return f"{t // 60}:{t % 60:02d}"


def _sleep(seconds: float):
    time.sleep(seconds)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def model() -> str:
    return (os.getenv("GEMINI_STYLE_MODEL") or "gemini-3.8-flash").strip()


def _key() -> str:
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def cost(tokens_in: int, tokens_out: int) -> float:
    """Dollars for one call, from the configured per-million prices (input includes video and audio)."""
    return (tokens_in * _env_float("GEMINI_STYLE_PRICE_IN", 0.75)
            + tokens_out * _env_float("GEMINI_STYLE_PRICE_OUT", 3.75)) / 1e6


def initialize():
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            db.schema_lock(conn, "style_watch")                  # two containers boot together: one at a time
            conn.execute("""CREATE TABLE IF NOT EXISTS style_watch_jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, video_id TEXT NOT NULL,
                start_s INTEGER NOT NULL, end_s INTEGER NOT NULL, prompt_version INTEGER NOT NULL,
                model TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0, cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                cached INTEGER NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_style_watch_window ON style_watch_jobs"
                         "(video_id,start_s,end_s,prompt_version,model,status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_style_watch_user ON style_watch_jobs(user_id,created_at)")
        _ready.add(key)


def _daily_limit(user: dict) -> int | None:
    if is_admin(user):
        return None
    return DAILY_WATCHES.get(plans.tier_of(user.get("plan")), 0)


def _spent_today(conn) -> float:
    row = conn.execute(db._q("SELECT COALESCE(SUM(cost_usd),0) AS s FROM style_watch_jobs WHERE created_at>?"),
                       (time.time() - 86400,)).fetchone()
    return float(row["s"] or 0)


def _row(job_id: str) -> dict | None:
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM style_watch_jobs WHERE id=?"), (job_id,)).fetchone()
    return dict(row) if row else None


def public(row: dict, admin: bool = False) -> dict:
    out = {"job": row["id"], "status": row["status"], "video_id": row["video_id"],
           "start": row["start_s"], "end": row["end_s"], "model": row["model"], "cached": bool(row["cached"])}
    if admin:                                   # what the call cost, for tuning windows and prompts
        out["usage"] = {"tokens_in": row.get("tokens_in", 0), "tokens_out": row.get("tokens_out", 0),
                        "cost_usd": round(float(row.get("cost_usd") or 0), 5)}
    if row["status"] == "done":
        out["log"] = json.loads(row["result_json"] or "{}")
    if row["status"] == "failed":
        out["error"] = row["error"] or "Gemini could not watch this part of the video."
    return out


def _parse(text: str) -> dict:
    """Gemini's JSON answer as a dict, mended for free where it can be (line breaks inside its strings, a comma before a
    closing bracket). Raises ValueError for anything else: the caller asks Gemini once more."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    a, b = text.find("{"), text.rfind("}")
    tries = [text] + ([text[a:b + 1]] if 0 <= a < b else [])
    for attempt in tries + [re.sub(r",\s*([}\]])", r"\1", t) for t in tries]:
        try:
            value = json.loads(attempt, strict=False)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Gemini's answer was not a JSON object")


class WatchFailed(ValueError):
    """Gemini answered, and billed the whole video's tokens, but the answer can't be used (blocked, cut off, not a JSON
    object). The tokens ride along so the spend ledger records what Google charged: a failed watch recorded at $0
    let the daily spend cap undercount exactly when answers went wrong."""

    def __init__(self, message: str, tokens_in: int = 0, tokens_out: int = 0):
        super().__init__(message)
        self.tokens_in, self.tokens_out = int(tokens_in or 0), int(tokens_out or 0)


def upload(data: bytes, mime: str, *, post=None, get=None, delete=None) -> dict:
    """A video on Gemini's file service, ready to watch: {"uri", "name"}. The caller deletes it with delete_file(name)
    once watched: Google keeps an upload for two days against a per-project storage quota, so every clip left there
    brought the day when every upload watch fails for every customer closer. A file that never became readable is
    deleted here."""
    post, get = post or requests.post, get or requests.get
    head = {"x-goog-api-key": _key()}
    res = post(UPLOAD_BASE, headers={**head, "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                                     "X-Goog-Upload-Header-Content-Length": str(len(data)),
                                     "X-Goog-Upload-Header-Content-Type": mime},
               json={"file": {"display_name": "studio-review"}}, timeout=(15, 60))
    url = (getattr(res, "headers", None) or {}).get("X-Goog-Upload-URL") or \
        (getattr(res, "headers", None) or {}).get("x-goog-upload-url")
    if res.status_code != 200 or not url:
        raise RuntimeError(f"Gemini upload HTTP {res.status_code}")
    res = post(url, headers={**head, "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
               data=data, timeout=(15, 300))
    if res.status_code != 200:
        raise RuntimeError(f"Gemini upload HTTP {res.status_code}")
    file = res.json().get("file") or {}
    name = str(file.get("name") or "")
    try:
        for _ in range(100):
            if file.get("state") == "ACTIVE" and file.get("uri"):
                return {"uri": file["uri"], "name": name}
            if file.get("state") == "FAILED" or not file.get("name"):
                raise RuntimeError("Gemini could not read this video")
            _sleep(3)
            file = get(f"{GEMINI_BASE}/{file['name']}", headers=head, timeout=(15, 60)).json()
        raise RuntimeError("Gemini took too long to read this video")
    except Exception:
        delete_file(name, delete=delete)
        raise


def upload_file(data: bytes, mime: str, *, post=None, get=None) -> str:
    """A video on Gemini's file service (kept there for two days), ready to watch: its URI."""
    return upload(data, mime, post=post, get=get)["uri"]


def delete_file(name: str, *, delete=None) -> bool:
    """An upload removed from Gemini's file service ("files/abc123"). Never raises: a leftover file expires in two
    days anyway, and a watch must not fail because its clean-up did."""
    if not name or not str(name).startswith("files/"):
        return False
    try:
        res = (delete or requests.delete)(f"{GEMINI_BASE}/{name}", headers={"x-goog-api-key": _key()}, timeout=(5, 20))
        return getattr(res, "status_code", 0) in (200, 204, 404)
    except Exception:                                          # noqa: BLE001 - clean-up only
        return False


def watch_once(video_id: str, start: int, end: int, *, post=None, file_uri: str = "",
               mime: str = "video/mp4", question: str = "", fps: int | None = None,
               low_res: bool | None = None) -> tuple[dict, int, int]:
    """One Gemini watch of a YouTube window, or of an uploaded video's (file_uri): (log, input tokens, output tokens).
    An admin's own `question` (research on a format) replaces the style instructions and looks closer. `fps` and
    `low_res` override how closely it looks (Ranking Shorts scouts many clips cheaply, then looks closely at a few)."""
    post = post or requests.post
    source = ({"fileUri": file_uri, "mimeType": mime} if file_uri else
              {"fileUri": f"https://www.youtube.com/watch?v={video_id}", "mimeType": "video/*"})
    config = {"responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}
    if (not question) if low_res is None else low_res:
        config["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
    body = {"contents": [{"parts": [
                {"fileData": source,
                 "videoMetadata": {"startOffset": f"{start}s", "endOffset": f"{end}s",
                                   "fps": fps or (4 if question else 2)}},
                {"text": question or prompt(start, end)}]}],
            "generationConfig": config}
    return _answer(post, body, BUSY_WAITS_S, (15, 600))


def _generate(post, body: dict, waits: tuple, timeout: tuple) -> tuple:
    """One Gemini answer, asked again while Google says it is busy: (text, input tokens, output tokens). A refusal
    raises RuntimeError with Google's reason."""
    for wait in waits + (None,):
        res = post(f"{GEMINI_BASE}/models/{model()}:generateContent", headers={"x-goog-api-key": _key()},
                   json=body, timeout=timeout)
        # Google's "high demand" answers are brief; nothing was generated or billed, so wait and ask again
        if res.status_code not in (429, 500, 503) or wait is None:
            break
        _sleep(wait)
    if res.status_code != 200:
        detail = ""
        try:
            detail = str((res.json().get("error") or {}).get("message") or "")[:200]
        except ValueError:
            pass
        raise RuntimeError(f"Gemini HTTP {res.status_code}{': ' + detail if detail else ''}")
    data = res.json()
    usage = data.get("usageMetadata") or {}
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    tokens_in = int(usage.get("promptTokenCount") or 0)
    tokens_out = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    return text, tokens_in, tokens_out


def _answer(post, body: dict, waits: tuple, timeout: tuple, again_within_s: float | None = None) -> tuple:
    """Gemini's answer as a dict: (answer, input tokens, output tokens), every attempt's tokens counted. An answer that
    can't be read (a missing comma, cut off) is asked for once more, unless the first took longer than again_within_s;
    still unreadable, or blocked (no answer at all), WatchFailed says so plainly and carries what Google billed."""
    spent_in = spent_out = 0
    started = time.monotonic()
    for attempt in range(2):
        try:
            text, tokens_in, tokens_out = _generate(post, body, waits, timeout)
        except RuntimeError:
            if not attempt:
                raise
            break                                   # the second ask was refused: the first answer was billed all the same
        spent_in, spent_out = spent_in + tokens_in, spent_out + tokens_out
        if not text.strip():                        # blocked: asked again it says the same, and is billed again
            raise WatchFailed(UNANSWERED, spent_in, spent_out)
        try:
            return _parse(text), spent_in, spent_out
        except ValueError:
            if again_within_s is not None and time.monotonic() - started > again_within_s:
                break
    raise WatchFailed(UNREADABLE, spent_in, spent_out)


_QUESTIONS: dict[str, str] = {}


# ── likeness: new pictures judged against a channel's own frames (Studio's style clone, before it saves) ──────────
COMPARE_VERSION = 2
MAX_COMPARE_IMAGES = 7                           # reference frames, and candidates (a hidden control among them)
MAX_COMPARE_BYTES = 1_500_000                    # one 960 px JPEG
COMPARE_WAITS_S = (5, 20)                        # the call is synchronous: Studio waits on it, the gateway cuts at ~100 s
COMPARE_AGAIN_S = 40                             # an unreadable answer is asked again only if it took less
COMPARE_PROMPT = """You judge whether NEW pictures match a YouTube channel's visual style. The REFERENCE images are real
frames from the channel. The CANDIDATE images were made by a studio to look like that channel: they show NEW scenes
and subjects on purpose, so a different subject, setting or story is never a fault. Judge only the STYLE.
[CONTEXT]
Score how well the candidates, taken together, match the references, each 0 to 10 (10 = indistinguishable):
- medium: the same kind of picture (3D render, 2D drawing, photograph, collage...) with the same finish: shading,
  materials, edges, texture, level of detail.
- characters: people drawn with the same DESIGN (head shape and colour, face, eyes and mouth, body, limbs,
  proportions, clothing style and colours). If neither the references nor the candidates show people, 10.
- sets: places and props built the same way (shapes, simplicity, detail, scale).
- palette: the colours, their saturation and brightness.
- light: lighting, shadows, contrast, time of day feel.
- camera: framing, lens and composition habits.
Then "overall" 0 to 10: would a regular viewer believe these candidates are frames from this channel?
Also score EACH candidate on its own, in order, in every one of those areas and overall.
List "dealbreakers": the big mismatches a viewer would notice at once (for example "the people are realistic humans
but the channel's characters have round solid black heads with simple white faces"), at most 4, empty when none.
List "fixes": concrete changes to the picture prompt that would close the gap (name what to add, change or drop),
at most 5. Never name the channel, its creator or a studio.
Return ONLY JSON: {"medium": 0, "characters": 0, "sets": 0, "palette": 0, "light": 0, "camera": 0, "overall": 0,
 "candidates": [{"medium": 0, "characters": 0, "sets": 0, "palette": 0, "light": 0, "camera": 0, "overall": 0,
 "why": "a few words"}], "dealbreakers": [], "fixes": []}"""


def _image_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def compare_once(references: list, candidates: list, context: str = "", *, post=None) -> tuple:
    """One Gemini judgement of candidate pictures against reference frames: (verdict, input tokens, output tokens)."""
    post = post or requests.post
    parts = []
    for label, group in (("REFERENCE", references), ("CANDIDATE", candidates)):
        for i, data in enumerate(group, 1):
            parts += [{"text": f"{label} {i}:"},
                      {"inlineData": {"mimeType": _image_mime(data), "data": base64.b64encode(data).decode()}}]
    parts.append({"text": COMPARE_PROMPT.replace("[CONTEXT]", (context or "").strip()[:600])})
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}
    # asked again only when the first answer came quickly: Studio waits on this call and the gateway cuts at ~100 s
    return _answer(post, body, COMPARE_WAITS_S, (15, 90), again_within_s=COMPARE_AGAIN_S)


def _score(value) -> float:
    try:
        return max(0.0, min(10.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def compare(user: dict, references: list, candidates: list, context: str = "", *, post=None) -> dict:
    """Studio's likeness check: decoded and checked here, counted with the style watches (the same daily allowance
    and the same global daily spend cap), answered at once."""
    if not _key():
        raise HTTPException(503, "Style checking isn't available right now.")
    refs, cands = [], []
    for group, out in ((references, refs), (candidates, cands)):
        if not isinstance(group, list) or not 1 <= len(group) <= MAX_COMPARE_IMAGES:
            raise HTTPException(400, f"Send 1 to {MAX_COMPARE_IMAGES} pictures of each kind.")
        for item in group:
            try:
                data = base64.b64decode(str(item), validate=True)
            except ValueError:
                raise HTTPException(400, "A picture could not be read.") from None
            if not data or len(data) > MAX_COMPARE_BYTES or not _image_mime(data):
                raise HTTPException(400, "Send JPEG, PNG or WebP pictures under 1.5 MB.")
            out.append(data)
    initialize()
    now = time.time()
    digest = hashlib.sha256(b"".join(refs + cands) + (context or "").encode()).hexdigest()[:24]
    with db._conn() as conn:
        limit = _daily_limit(user)
        if limit is not None:
            used = conn.execute(db._q("""SELECT COUNT(*) AS n FROM style_watch_jobs WHERE user_id=? AND cached=0
                AND created_at>?"""), (user["id"], now - 86400)).fetchone()["n"]
            if used >= limit:
                raise HTTPException(402, "You've used today's style checks. Try again tomorrow.")
        if _spent_today(conn) >= _env_float("STYLE_WATCH_DAILY_USD", 15.0):
            raise HTTPException(503, "Style checking is busy today. Try again later.")
        job = "sc_" + secrets.token_hex(10)
        conn.execute(db._q("""INSERT INTO style_watch_jobs (id,user_id,video_id,start_s,end_s,prompt_version,model,
            status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'running',?,?)"""),
            (job, user["id"], "cmp_" + digest, 0, 0, 1000 + COMPARE_VERSION, model(), now, now))
    try:
        verdict, tin, tout = compare_once(refs, cands, context, post=post)
    except Exception as exc:                                  # noqa: BLE001 - said to Studio, which goes on without it
        tin, tout = getattr(exc, "tokens_in", 0), getattr(exc, "tokens_out", 0)
        with db._conn() as conn:
            conn.execute(db._q("""UPDATE style_watch_jobs SET status='failed', error=?, tokens_in=?, tokens_out=?,
                cost_usd=?, updated_at=? WHERE id=?"""), (str(exc)[:300], tin, tout, cost(tin, tout), time.time(), job))
        raise HTTPException(503, "Style checking couldn't answer right now. Try again in a little while.") from None
    spent = cost(tin, tout)
    areas = ("medium", "characters", "sets", "palette", "light", "camera", "overall")
    out = {k: _score(verdict.get(k)) for k in areas}
    # each candidate in every area, so Studio decides on its own drawn pictures and never on the hidden control
    out["candidates"] = [{**{k: _score(c.get(k)) for k in areas if c.get(k) is not None},
                          "overall": _score(c.get("overall")), "why": str(c.get("why") or "")[:160]}
                         for c in (verdict.get("candidates") or [])[:len(cands)] if isinstance(c, dict)]
    out["dealbreakers"] = [str(x)[:240] for x in verdict.get("dealbreakers") or [] if str(x).strip()][:4]
    out["fixes"] = [str(x)[:240] for x in verdict.get("fixes") or [] if str(x).strip()][:5]
    with db._conn() as conn:
        conn.execute(db._q("""UPDATE style_watch_jobs SET status='done', result_json=?, tokens_in=?, tokens_out=?,
            cost_usd=?, updated_at=? WHERE id=?"""), (json.dumps(out), tin, tout, spent, time.time(), job))
    return {**out, "version": COMPARE_VERSION, "cost_usd": round(spent, 4)}


class CompareRequest(BaseModel):
    references: list = Field(default_factory=list)
    candidates: list = Field(default_factory=list)
    context: str = Field("", max_length=600)


def _run(job_id: str, data: bytes = b"", mime: str = "video/mp4"):
    row = _row(job_id)
    question = _QUESTIONS.pop(job_id, "")
    if not row or row["status"] != "running":
        return
    try:
        uri = upload_file(data, mime) if data else ""
        log, tin, tout = watch_once(row["video_id"], row["start_s"], row["end_s"], file_uri=uri, mime=mime,
                                    question=question)
        values = ("done", json.dumps(log), "", tin, tout, cost(tin, tout))
    except Exception as exc:                                    # noqa: BLE001 - recorded on the job
        print(f"[style-watch] {row['video_id']} {row['start_s']}-{row['end_s']}: {exc}", flush=True)
        tin, tout = getattr(exc, "tokens_in", 0), getattr(exc, "tokens_out", 0)    # an unreadable answer was billed
        values = ("failed", "", str(exc)[:300], tin, tout, cost(tin, tout))
    with db._conn() as conn:
        conn.execute(db._q("""UPDATE style_watch_jobs SET status=?, result_json=?, error=?, tokens_in=?,
            tokens_out=?, cost_usd=?, updated_at=? WHERE id=?"""), (*values, time.time(), job_id))


def start(user: dict, video_id: str, start_s: float, end_s: float, *, submit=None) -> dict:
    """Return a finished cached log, the account's running job for this window, or start a new watch."""
    initialize()
    if not _VIDEO.fullmatch(video_id or ""):
        raise HTTPException(400, "Choose a YouTube video to watch.")
    a, b = int(max(0, start_s)), int(end_s)
    if not 10 <= b - a <= MAX_WINDOW_S:
        raise HTTPException(400, f"Watch between 10 and {MAX_WINDOW_S} seconds at a time.")
    if not _key():
        raise HTTPException(503, "Style watching isn't available right now. The study continues with its own measurements.")
    now, name = time.time(), model()
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("SELECT pg_advisory_xact_lock(726346120)")
        same = conn.execute(db._q("""SELECT * FROM style_watch_jobs WHERE video_id=? AND start_s=? AND end_s=?
            AND prompt_version=? AND model=? AND (status='done' OR (status='running' AND updated_at>?))
            ORDER BY status='done' DESC, created_at DESC LIMIT 1"""),
            (video_id, a, b, PROMPT_VERSION, name, now - STALE_S)).fetchone()
        if same and same["status"] == "done":
            job = "sw_" + secrets.token_hex(10)
            conn.execute(db._q("""INSERT INTO style_watch_jobs (id,user_id,video_id,start_s,end_s,prompt_version,model,
                status,result_json,cached,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'done',?,1,?,?)"""),
                (job, user["id"], video_id, a, b, PROMPT_VERSION, name, same["result_json"], now, now))
            return public({**dict(same), "id": job, "cached": 1}, is_admin(user))
        if same:                                                # someone is watching this window right now
            return public(dict(same), is_admin(user))
        limit = _daily_limit(user)
        if limit is not None:
            used = conn.execute(db._q("""SELECT COUNT(*) AS n FROM style_watch_jobs WHERE user_id=? AND cached=0
                AND created_at>?"""), (user["id"], now - 86400)).fetchone()["n"]
            if used >= limit:
                # 402, not 429: Studio retries a 429 within seconds, and this limit lasts until tomorrow
                raise HTTPException(402, "You've used today's style watching. The study continues with its own "
                                         "measurements, or try again tomorrow.")
        if _spent_today(conn) >= _env_float("STYLE_WATCH_DAILY_USD", 15.0):
            raise HTTPException(503, "Style watching is busy today. The study continues with its own measurements.")
        job = "sw_" + secrets.token_hex(10)
        conn.execute(db._q("""INSERT INTO style_watch_jobs (id,user_id,video_id,start_s,end_s,prompt_version,model,
            status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'running',?,?)"""),
            (job, user["id"], video_id, a, b, PROMPT_VERSION, name, now, now))
    (submit or _pool.submit)(_run, job)
    return public(_row(job), is_admin(user))


def start_upload(user: dict, data: bytes, mime: str, start_s: float, end_s: float, *, submit=None,
                 question: str = "") -> dict:
    """An admin's video (a Studio test render, or a reference being researched) watched with the reference
    instructions or the admin's own question; the same file, window and question is watched once. Returns the job,
    as start() does."""
    initialize()
    if not is_admin(user):
        raise HTTPException(403, "Watching an uploaded video is for ChannelRecipe's team.")
    if mime not in UPLOAD_MIMES or not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"Send an MP4, WebM or MOV copy under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    a, b = int(max(0, start_s)), int(end_s)
    if not 5 <= b - a <= MAX_WINDOW_S:
        raise HTTPException(400, f"Watch between 5 and {MAX_WINDOW_S} seconds at a time.")
    if not _key():
        raise HTTPException(503, "Style watching isn't available right now.")
    video_id, now, name = "file:" + hashlib.sha256(data).hexdigest()[:20], time.time(), model()
    # prompt_version is an integer column: a question gets its own number, far above the style prompt's versions
    version = (10_000 + int(hashlib.sha256(question.encode()).hexdigest()[:7], 16)) if question else PROMPT_VERSION
    with db._conn() as conn:
        same = conn.execute(db._q("""SELECT * FROM style_watch_jobs WHERE video_id=? AND start_s=? AND end_s=?
            AND prompt_version=? AND model=? AND (status='done' OR (status='running' AND updated_at>?))
            ORDER BY status='done' DESC, created_at DESC LIMIT 1"""),
            (video_id, a, b, version, name, now - STALE_S)).fetchone()
        if same:
            return public(dict(same), True)
        if _spent_today(conn) >= _env_float("STYLE_WATCH_DAILY_USD", 15.0):
            raise HTTPException(503, "Style watching is busy today.")
        job = "sw_" + secrets.token_hex(10)
        conn.execute(db._q("""INSERT INTO style_watch_jobs (id,user_id,video_id,start_s,end_s,prompt_version,model,
            status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'running',?,?)"""),
            (job, user["id"], video_id, a, b, version, name, now, now))
    if question:
        _QUESTIONS[job] = question
    (submit or _pool.submit)(_run, job, data, mime)
    return public(_row(job), True)


def get(user: dict, job_id: str) -> dict:
    initialize()
    row = _row(job_id) if isinstance(job_id, str) and job_id.startswith("sw_") else None
    # Any Studio account may poll a job ID it was given: a window another account started first is shared, and a
    # log describes a public video's style only (public() never returns who started it).
    if not row:
        raise HTTPException(404, "That style watch wasn't found.")
    if row["status"] == "running" and time.time() - row["updated_at"] > STALE_S:
        with db._conn() as conn:
            conn.execute(db._q("UPDATE style_watch_jobs SET status='failed', error=?, updated_at=? WHERE id=? AND status='running'"),
                         ("The watch stopped before it finished. Start it again.", time.time(), job_id))
        row = _row(job_id)
    return public(row, is_admin(user))


class WatchRequest(BaseModel):
    video_id: str
    start: float
    end: float


class UploadWatchRequest(BaseModel):
    data_b64: str
    mime: str = "video/mp4"
    start: float = 0
    end: float
    question: str = Field("", max_length=20000)    # an admin's own research question instead of the style log


def make_router(access) -> APIRouter:
    """`access` is the Studio dependency (a Studio plan, by session or Studio key)."""
    router = APIRouter(prefix="/api/studio/style", tags=["ChannelRecipe Studio"])

    @router.post("/watch")
    def post_watch(req: WatchRequest, user=Depends(access)):
        return start(user, req.video_id, req.start, req.end)

    @router.post("/watch-upload")
    def post_watch_upload(req: UploadWatchRequest, user=Depends(access)):
        if not is_admin(user):
            raise HTTPException(403, "Watching an uploaded video is for ChannelRecipe's team.")
        if len(req.data_b64) > MAX_UPLOAD_BYTES * 4 // 3 + 8:
            raise HTTPException(400, f"Send a copy under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        try:
            data = base64.b64decode(req.data_b64, validate=True)
        except ValueError:
            raise HTTPException(400, "The video copy could not be read.") from None
        return start_upload(user, data, req.mime, req.start, req.end, question=req.question.strip())

    @router.get("/watch/{job_id}")
    def get_watch(job_id: str, user=Depends(access)):
        return get(user, job_id)

    @router.post("/compare")
    def post_compare(req: CompareRequest, user=Depends(access)):
        return compare(user, req.references, req.candidates, req.context)

    return router
