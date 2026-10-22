"""ChannelRecipe Studio inside Claude Code: the `studio` tool server (MCP over stdio, standard library only).

Claude Code starts this with Studio's own Python (the `studio` entry in .mcp.json, written by cr_claude_code.configure).
Every tool calls Studio's own local routes on http://127.0.0.1:7860 with the local token from .cr-runtime.json, so
each check, gate and receipt the Studio page uses applies here too. Nothing here renders, reads keys or spends by
itself: paid work starts only through make_videos and clone_style, which Claude Code asks the creator to approve
(permissions.ask in .claude/settings.json), and make_videos starts nothing until the cost was shown (its quote_id).

    .venv/bin/python cr_studio_tools.py            what Claude Code runs
    .venv/bin/python cr_studio_tools.py --check    one setup check, printed (for support)
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:7860"
# Links for the creator: Claude Code (and the desktop app's Browser pane) open http://localhost links.
PUBLIC = "http://localhost:7860"
STATE = HOME / ".cr-claude-code"
SERVER = {"name": "channelrecipe-studio", "version": "1.0.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
BUSY = "Finish the current studio request"
MAX_TITLES = 5
QUOTE_SECONDS = 30 * 60
CLOSED = ("Studio isn't running. Open it with its launcher: double-click \"Start on Mac.command\" (Mac) or "
          "\"Start on Windows.cmd\" (Windows) in the ChannelRecipe studio folder, keep that window open, and try "
          "again in a minute.")
_SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}\Z")
_STYLE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}\Z")
_PICK = re.compile(r"pk_[a-f0-9]{24}\Z")
_LOOK = re.compile(r"lk_[a-f0-9]{24}\Z")
_OPERATION = re.compile(r"op_[a-f0-9]{24}\Z")
# What only this computer makes for now (cr_cloud.blocked says the same to the Studio page).
LOCAL_ONLY = "renders on this computer for now"


class Closed(RuntimeError):
    """Studio is not running (nothing answers on port 7860)."""


class Refused(RuntimeError):
    """Studio answered with an error the creator should read."""
    def __init__(self, message, status=None, data=None):
        super().__init__(message)
        self.status, self.data = status, data or {}


# ── talking to Studio ─────────────────────────────────────────────────────────────────────────────────────────

def _token() -> str:
    try:
        return str(json.loads((HOME / ".cr-runtime.json").read_text(encoding="utf-8")).get("token") or "")
    except (OSError, ValueError):
        return ""


def call(method: str, path: str, body=None, *, timeout: float = 60, wait_busy: float = 75):
    """One of Studio's own routes. A "finish the current studio request" answer (the page's own refreshes take
    Studio's work lock for a moment) is waited out for up to wait_busy seconds instead of failing."""
    deadline = time.time() + wait_busy
    while True:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(BASE + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if method != "GET":
            request.add_header("X-CR-Local-Token", _token())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(8_000_000)
                status = response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(2_000_000), error.code
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, (ConnectionRefusedError, ConnectionResetError)) or "refused" in str(reason).lower():
                raise Closed(CLOSED) from None
            if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                raise Refused("Studio is busy and didn't answer in time. Try again in a moment.") from None
            raise Closed(CLOSED) from None
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {"error": raw[:300].decode("utf-8", "replace")}
        if status == 409 and BUSY in str(payload.get("error") or "") and time.time() < deadline:
            time.sleep(3)
            continue
        if status >= 400:
            message = str(payload.get("error") or f"Studio answered {status}.")
            if status == 403 and "Refresh" in message:
                message = "Studio restarted since this chat began. Try again: the tools read its new local key."
            raise Refused(message, status, payload)
        return payload


def link(path: str) -> str:
    return PUBLIC + path


def _quote_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=1), encoding="utf-8")
    temporary.replace(path)


# ── the catalogue ─────────────────────────────────────────────────────────────────────────────────────────────

def catalog() -> dict:
    """The recipe catalogue, each row with Studio Cloud's own answer for it (`cloud_note`, '' when it can render
    there) and its small pictures, from /cr-claude-code/styles."""
    data = call("GET", "/cr-workspace/catalog", timeout=90)
    try:
        cards = {c.get("id"): c for c in call("GET", "/cr-claude-code/styles", timeout=120).get("styles") or []}
    except Refused:
        cards = {}
    for row in data.get("channels") or []:
        card = cards.get(row.get("name"))
        if card is not None:
            row["cloud_note"] = str(card.get("cloud_note") or "")
            row["small_picture"] = str(card.get("jpg") or "")
    return data


def cloud_block(row: dict) -> str:
    """Why this recipe can't render on Studio Cloud yet ('' when it can): Studio Cloud's own rule, as the catalogue
    row carries it. Studio checks again when the video starts."""
    if "cloud_note" in row:
        return row["cloud_note"]
    if row.get("panels"):
        return "This recipe reads your own panels, which you choose on the Studio page."
    return "This recipe " + LOCAL_ONLY + "."                       # an older Studio: this computer, to be safe


def kind(row: dict) -> str:
    return "short" if row.get("short_seconds") else "video"


def one_line(text, limit=160) -> str:
    text = " ".join(str(text or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return (first if len(first) <= limit else first[:limit - 1].rstrip() + "…")


def find_style(rows: list, wanted: str) -> dict:
    wanted = str(wanted or "").strip()
    for row in rows:
        if row.get("name") == wanted:
            return row
    low = wanted.lower()
    named = [row for row in rows if str(row.get("label") or "").lower() == low]
    if len(named) == 1:
        return named[0]
    near = [row for row in rows if low and low in (str(row.get("label") or "") + " " + row["name"]).lower()]
    if len(near) == 1:
        return near[0]
    options = ", ".join(f"{r['name']} ({r.get('label')})" for r in (near or rows)[:12])
    raise Refused(f"No style called '{wanted}'. Use a style id from list_styles, for example: {options}.")


def thumbnail(row: dict) -> str:
    """A local picture of the style: its preview frame (a style reference, the creator's own video or a clone's
    reference), cached under .cr-claude-code/thumbs; else its recipe artwork. '' when neither exists."""
    small = row.get("small_picture")
    if isinstance(small, str) and small and Path(small).is_file():
        return small                                               # Studio already made it (/cr-claude-code/styles)
    url = row.get("preview_url")
    if isinstance(url, str) and url.startswith("/cr-workspace/preview/recipe/"):
        version = re.sub(r"[^a-f0-9]", "", urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("v", [""])[0])
        target = STATE / "thumbs" / f"{row['name']}-{version[:16] or 'latest'}.jpg"
        if target.is_file() and target.stat().st_size > 0:
            return str(target)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(BASE + url, timeout=45) as response:
                    data = response.read(4_000_000)
                if data[:2] == b"\xff\xd8":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    for old in target.parent.glob(row["name"] + "-*.jpg"):
                        old.unlink(missing_ok=True)
                    target.write_bytes(data)
                    return str(target)
                break
            except urllib.error.HTTPError as error:
                if error.code != 503:
                    break
                time.sleep(1.5)
            except (urllib.error.URLError, OSError):
                break
    art = row.get("art_url")
    if isinstance(art, str) and art.startswith("/cr-workspace/art/"):
        path = HOME / "workspace_art" / art.rsplit("/", 1)[-1]
        if path.is_file():
            return str(path)
    return ""


def style_card(row: dict, thumb: str = "") -> dict:
    block = cloud_block(row)
    return {"id": row["name"], "name": row.get("label") or row["name"],
            "description": one_line(row.get("tagline") or row.get("description")),
            "family": row.get("family") or "", "kind": kind(row), "mine": bool(row.get("custom")),
            "renders_on": ["this_computer"] + ([] if block else ["studio_cloud"]),
            "cloud_note": block, "media_cost": one_line(row.get("cost"), 220) if row.get("cost") else "",
            "short_seconds": row.get("short_seconds"), "needs_panels": bool(row.get("panels")),
            "thumbnail_path": thumb, "preview_url": link(row["preview_url"]) if row.get("preview_url") else ""}


def new_pick() -> dict:
    return call("POST", "/cr-gallery/picks", {})


# ── money ─────────────────────────────────────────────────────────────────────────────────────────────────────

def features_for(row: dict, overrides=None) -> dict:
    offered = ((row.get("features") or {}).get("offered")) or []
    on = ((row.get("features") or {}).get("on"))
    on = on if isinstance(on, list) else offered
    out = {key: key in on for key in offered}
    for key, value in (overrides or {}).items():
        if key not in out:
            raise Refused(f"'{key}' isn't an option of this style. Its options: {', '.join(offered) or 'none'}.")
        out[key] = bool(value)
    if "depth" in out and out.get("ai_images") is False:
        out["depth"] = False                                       # depth lifts AI pictures: none, no depth
    return out


def media_estimate(cat: dict, row: dict, minutes: int, features: dict) -> dict:
    prices = cat.get("prices") or {}
    per = prices.get("features") or {}
    voice = float(((prices.get("voice") or {}).get("per_min")) or 0)
    total = voice + sum(float((per.get(k) or {}).get("per_min") or 0) for k, on in features.items() if on)
    return {"per_video_usd": round(total * minutes, 2), "recipe_note": one_line(row.get("cost"), 260),
            "note": "Media runs on your own accounts (pictures, voice, footage watching). Claude's writing and "
                    "directing run on your Claude Code plan, not billed per video."}


def quote_for(cat: dict, row: dict, *, titles: int, minutes: int, where: str, features: dict) -> dict:
    media = media_estimate(cat, row, minutes, features)
    out = {"style": row["name"], "style_name": row.get("label"), "titles": titles, "minutes": minutes,
           "where": where, "media": media}
    if where == "studio_cloud":
        block = cloud_block(row)
        if block:
            raise Refused(block + " Choose where='this_computer', or a style whose renders_on includes studio_cloud.")
        cloud = call("GET", "/cr-claude-code/quote?" + urllib.parse.urlencode(
            {"minutes": minutes, "style": row["name"], "features": json.dumps(features)}), timeout=40)
        if not cloud.get("available"):
            hint = " Choose where='this_computer'." if cloud.get("blocked") else ""
            raise Refused((cloud.get("why") or "Studio Cloud isn't available on this account.") + hint)
        expected, most = cloud["expected_cents"] / 100, cloud["max_cents"] / 100
        out["cloud"] = {"machine": cloud.get("label"), "expected_minutes": cloud.get("expected_minutes"),
                        "expected_usd_each": round(expected, 2), "reserved_usd_each": round(most, 2),
                        "balance_usd": cloud.get("balance_usd"), "topup_url": cloud.get("topup_url"),
                        "note": "Each video reserves its most before its machine starts and pays only for the "
                                "seconds it used."}
        out["expected_total_usd"] = round((expected + media["per_video_usd"]) * titles, 2)
        out["most_reserved_usd"] = round(most * titles, 2)
        if (cloud.get("balance_usd") or 0) < most * titles:
            out["short_by_usd"] = round(most * titles - (cloud.get("balance_usd") or 0), 2)
    else:
        out["expected_total_usd"] = round(media["per_video_usd"] * titles, 2)
        out["this_computer"] = "Renders on this computer, one video at a time; no machine to pay for."
    key = json.dumps([row["name"], titles, minutes, where, sorted(k for k, v in features.items() if v)])
    out["quote_id"] = "q_" + hashlib.sha256(key.encode()).hexdigest()[:16]
    quotes = _read(STATE / "quotes.json")
    quotes = {k: v for k, v in quotes.items() if time.time() - v < QUOTE_SECONDS}
    quotes[out["quote_id"]] = time.time()
    _write(STATE / "quotes.json", quotes)
    out["summary"] = summary(out)
    return out


def summary(q: dict) -> str:
    n, each = q["titles"], " each" if q["titles"] > 1 else ""
    usd = q["media"]["per_video_usd"]
    media = f"media about ${usd:.2f}{each}" if usd >= 0.01 else "media a few cents"
    if q["where"] == "studio_cloud":
        c = q["cloud"]
        text = (f"{n} × {q['minutes']}-min {q['style_name']} on Studio Cloud: machine about "
                f"${c['expected_usd_each']:.2f}{each} (up to ${c['reserved_usd_each']:.2f}{each} reserved, only what "
                f"it uses is charged), {media}; about ${q['expected_total_usd']:.2f} in all. Cloud balance "
                f"${c['balance_usd']:.2f}.")
        if q.get("short_by_usd"):
            text += f" Short by ${q['short_by_usd']:.2f}: top up at {c['topup_url']}."
        return text
    return (f"{n} × {q['minutes']}-min {q['style_name']} on this computer: {media}; about "
            f"${q['expected_total_usd']:.2f} in all, plus Claude Code usage on your plan.")


# ── the tools ─────────────────────────────────────────────────────────────────────────────────────────────────

def tool_setup_check(args):
    try:
        status = call("GET", "/cr-status", timeout=60)
    except Closed:
        return {"running": False, "message": CLOSED,
                "launcher": str(HOME / ("Start on Windows.cmd" if os.name == "nt" else "Start on Mac.command"))}
    readiness = status.get("readiness") or {}
    claude = status.get("claude_code") or {}
    try:
        cloud = call("GET", "/cr-cloud", timeout=30)
    except Refused:
        cloud = {"available": False, "why": "Studio Cloud's status didn't load."}
    blockers = readiness.get("blockers") or []
    only_verify = bool(blockers) and all("Verify both Claude models" in b for b in blockers)
    return {"running": True, "studio_version": str(status.get("local_version") or "").replace("5.3-admin.", ""),
            "ready": bool(readiness.get("ready")), "blockers": blockers,
            "fixes_itself": "make_videos runs the tiny daily Claude model check itself." if only_verify else "",
            "setup_page": link("/cr-setup"),
            "claude_code": {"signed_in": bool(claude.get("authenticated")),
                            "models_verified": bool(claude.get("models_verified")),
                            "usage_limit_reached": bool((claude.get("allowance") or {}).get("blocked"))},
            "studio_cloud": {"available": bool(cloud.get("available")), "balance_usd": cloud.get("balance_usd"),
                             "why": cloud.get("why") or "", "topup_url": cloud.get("topup_url")},
            "gallery_url": link("/cr-gallery")}


def tool_list_styles(args):
    cat = catalog()
    rows = list(cat.get("channels") or [])
    want = str(args.get("kind") or "all")
    query = str(args.get("search") or "").strip().lower()
    if want == "videos":
        rows = [r for r in rows if kind(r) == "video"]
    elif want == "shorts":
        rows = [r for r in rows if kind(r) == "short"]
    elif want == "mine":
        rows = [r for r in rows if r.get("custom")]
    elif want == "cloud":
        rows = [r for r in rows if not cloud_block(r)]
    if query:
        rows = [r for r in rows if query in " ".join(str(r.get(k) or "") for k in
                                                       ("name", "label", "tagline", "description", "family")).lower()]
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        thumbs = list(pool.map(thumbnail, rows))
    cards = [style_card(r, t) for r, t in zip(rows, thumbs)]
    out = {"count": len(cards), "styles": cards}
    try:
        pick = new_pick()
        out.update(pick_id=pick["pick_id"], gallery_url=link("/cr-gallery?pick=" + pick["pick_id"]),
                   how_to_pick="Open gallery_url (in the Claude desktop app, in the Browser pane beside the chat; in a "
                               "terminal, in the creator's browser with open_in_studio). When they press Use this "
                               "style, get_style_pick(pick_id) returns it.")
    except Refused:
        out["gallery_url"] = link("/cr-gallery")
    return out


def tool_get_style_pick(args):
    pick = str(args.get("pick_id") or "")
    if not _PICK.fullmatch(pick):
        raise Refused("Use the pick_id that list_styles returned.")
    wait = max(0.0, min(float(args.get("wait_seconds") or 0), 100.0))
    deadline = time.time() + wait
    while True:
        result = call("GET", "/cr-gallery/picks/" + pick + "?claim=1", timeout=20)
        if result.get("state") != "waiting" or time.time() >= deadline:
            return result
        time.sleep(2)


def tool_quote(args):
    cat = catalog()
    row = find_style(cat.get("channels") or [], args.get("style"))
    titles = max(1, min(int(args.get("titles") or 1), MAX_TITLES))
    minutes = 1 if kind(row) == "short" else max(1, min(int(args.get("minutes") or 10), 60))
    where = where_of(args)
    return quote_for(cat, row, titles=titles, minutes=minutes, where=where,
                     features=features_for(row, (args.get("options") or {}).get("features")))


def where_of(args) -> str:
    where = str(args.get("where") or "this_computer")
    if where not in ("this_computer", "studio_cloud"):
        raise Refused("Choose where='this_computer' or where='studio_cloud'.")
    return where


def video_payload(row: dict, titles: list, minutes: int, features: dict, options: dict) -> dict:
    """What the Studio page's Create button sends (cr_workspace.js payload()), with its defaults."""
    if row.get("panels"):
        raise Refused(f"{row.get('label')} reads your own manga panels, which you choose on the Studio page: "
                      f"open_in_studio(page='create').")
    extra = str(options.get("extra_instructions") or "").strip()
    thumbs = options.get("thumbnails", True) is not False
    description = options.get("description", True) is not False
    steps = ["script", "voiceover", "images", "pexels", "video"] + (["thumbnail"] if thumbs else [])
    p = {"title": "\n".join(titles), "style": row["name"], "minutes": minutes, "steps": steps,
         "script": str(options.get("script") or "").strip(), "extra": extra, "byo_audio": "",
         "burn_subs": options.get("captions", True) is not False, "force": False,
         "thumb_count": max(1, min(int(options.get("thumbnail_count") or 2), 4)),
         "desc": description, "chapters": 5 if description else 0, "sources": 3 if description else 0,
         "features": features, "render_backend": "original", "character": "", "character_image": "",
         "avatar_id": "", "avatar_s": 8, "avatar_count": 1, "offer": "", "offer_plugs": 0, "panels": "",
         "cta": None, "pictures_per_min": None, "threed_look": ""}
    if p["script"] and len(titles) > 1:
        raise Refused("Your own script goes with one title.")
    if kind(row) == "short":
        seconds = max(15, min(int(options.get("short_seconds") or row.get("short_seconds") or 25), 60))
        if row.get("ranking"):
            r = options.get("ranking") or {}
            ranking = {"ranks": max(3, min(int(r.get("ranks") or 5), 10)),
                       "commentary": r.get("narrator_lines", True) is not False,
                       "booms": r.get("vine_booms", True) is not False,
                       "sfx": r.get("meme_sounds") if r.get("meme_sounds") in ("full", "light", "off") else "full",
                       "punch_ins": r.get("punch_ins", True) is not False, "clean": bool(r.get("clean")),
                       "captions": r.get("captions", True) is not False, "use_credits": False}
            p["extra"] = f"[[ranking={json.dumps(ranking, separators=(',', ':'))}]] " + p["extra"]
        p.update(minutes=1, extra=f"[[short_seconds={seconds}]] " + p["extra"])
    if row.get("auto_style"):
        p["auto_style"] = bool(options.get("adaptive"))
    return p


def tool_make_videos(args):
    titles = [str(t).strip() for t in (args.get("titles") or []) if str(t).strip()]
    if not titles:
        raise Refused("Give at least one title.")
    if len(titles) > MAX_TITLES:
        raise Refused(f"Make up to {MAX_TITLES} videos at once.")
    if any(len(t) > 280 or "\n" in t for t in titles):
        raise Refused("Keep each title on one line and under 280 characters. Put guidance in options.extra_instructions.")
    options = args.get("options") or {}
    if not isinstance(options, dict):
        raise Refused("options must be an object.")
    cat = catalog()
    row = find_style(cat.get("channels") or [], args.get("style"))
    where = where_of(args)
    minutes = 1 if kind(row) == "short" else max(1, min(int(args.get("minutes") or 10), 60))
    features = features_for(row, options.get("features"))
    issued = _read(STATE / "quotes.json").get(str(args.get("quote_id") or ""))
    q = quote_for(cat, row, titles=len(titles), minutes=minutes, where=where, features=features)
    if args.get("quote_id") != q["quote_id"] or not issued or time.time() - issued > QUOTE_SECONDS:
        return {"started": False, "quote": q,
                "next": "Nothing started. Tell the creator this cost, and when they agree call make_videos again with "
                        f"the same choices and quote_id='{q['quote_id']}'."}
    if q.get("short_by_usd"):
        raise Refused(f"Your Studio Cloud balance is ${q['cloud']['balance_usd']:.2f} and these videos reserve up to "
                      f"${q['most_reserved_usd']:.2f}. Top up at {q['cloud']['topup_url']}, or make them on this "
                      "computer.")
    payload = video_payload(row, titles, minutes, features, options)
    record = _read(STATE / "jobs.json")
    if where == "studio_cloud":
        call("POST", "/cr-cloud/start", payload, timeout=60)
        record.setdefault("cloud", []).append({"titles": titles, "style": row["name"], "at": time.time()})
        _write(STATE / "jobs.json", record)
        return {"started": True, "where": "studio_cloud", "titles": titles, "style": row["name"], "quote": q["summary"],
                "progress_id": "cloud",
                "next": "Studio writes the script and makes the voice and pictures here with your Claude Code, then a "
                        "ChannelRecipe machine renders. Keep Studio open. Check with progress(id='cloud') every few "
                        "minutes (don't wait in a loop); Studio shows a notification when it's done."}
    try:
        result = call("POST", "/api/run", payload, timeout=120)
    except Refused as error:
        blockers = (error.data or {}).get("blockers") or []
        if blockers and all("Verify both Claude models" in b for b in blockers):
            call("POST", "/cr-claude/setup", {"action": "verify"}, timeout=180)
            result = call("POST", "/api/run", payload, timeout=120)
        elif blockers:
            raise Refused(" ".join(blockers) + f" Setup: {link('/cr-setup')}", error.status, error.data) from None
        else:
            raise
    queued = [{"id": "video:" + (q_["slug"] if isinstance(q_, dict) else str(q_)),
               "title": q_.get("title") if isinstance(q_, dict) else str(q_)} for q_ in result.get("queued") or []]
    record.setdefault("local", []).extend({"slug": v["id"][6:], "title": v["title"], "at": time.time()} for v in queued)
    _write(STATE / "jobs.json", record)
    return {"started": True, "where": "this_computer", "videos": queued, "quote": q["summary"],
            "next": "Studio makes them one at a time. Check with progress(id) every few minutes (don't wait in a "
                    "loop); Studio shows a notification when each one is done. open_in_studio(page='projects') "
                    "shows them on the Studio page."}


def _job_status(slug: str) -> dict:
    status = call("GET", "/api/status/" + _quote_path(slug), timeout=20)
    out = {"id": "video:" + slug, "state": status.get("state"), "done": bool(status.get("done")),
           "percent": round(100 * float(status.get("progress") or 0)), "error": status.get("error") or "",
           "last_lines": [line for line in (status.get("lines") or [])[-6:] if line.strip()]}
    if status.get("video"):
        out.update(watch_url=link("/f/video/" + _quote_path(slug)), project_url=link("/editor/" + _quote_path(slug)))
    return out


def tool_progress(args):
    ident = str(args.get("id") or "").strip()
    if ident.startswith("video:") or (ident and _SLUG.fullmatch(ident) and not
                                       (_LOOK.fullmatch(ident) or _OPERATION.fullmatch(ident) or ident == "cloud")):
        return _job_status(ident.removeprefix("video:"))
    if ident == "cloud":
        return cloud_progress()
    if _LOOK.fullmatch(ident):
        return look_progress(ident)
    if _OPERATION.fullmatch(ident):
        return clone_progress(ident)
    if ident:
        raise Refused("Use an id from make_videos (video:…, cloud) or a style clone (lk_…, op_…).")
    queue = call("GET", "/api/queue", timeout=20)
    jobs = []
    for job in queue.get("jobs") or []:
        row = {"id": "video:" + str(job.get("slug")), "title": job.get("title"), "state": job.get("state")}
        if job.get("state") == "running":
            row.update(percent=round(100 * float(job.get("progress") or 0)), minutes_so_far=round((job.get("elapsed")
                       or 0) / 60), last_line=job.get("line") or "")
        if job.get("error"):
            row["error"] = job["error"]
        jobs.append(row)
    out = {"this_computer": jobs, "busy": bool(queue.get("busy"))}
    try:
        cloud = cloud_progress()
        if cloud.get("state") not in (None, "", "idle") or cloud.get("videos"):
            out["studio_cloud"] = cloud
    except Refused:
        pass
    try:
        activity = call("GET", "/cr-workspace/activity", timeout=30)
        clones = [{"id": op["operation_id"], "style": op.get("label") or op.get("title"), "state": op.get("state"),
                   "percent": op.get("percent")} for op in activity.get("operations") or []
                  if op.get("state") not in ("done",)]
        if clones:
            out["style_clones"] = clones
    except Refused:
        pass
    return out


def cloud_progress() -> dict:
    d = call("GET", "/cr-cloud/progress", timeout=60)
    out = {"id": "cloud", "state": d.get("state") or "idle", "title": d.get("title") or "",
           "message": d.get("error") or d.get("message") or "", "machine": d.get("farm") or "",
           "balance_usd": d.get("balance_usd"), "last_lines": [x for x in (d.get("lines") or [])[-8:] if x.strip()]}
    out["videos"] = [{"title": v.get("title"), "slug": v.get("slug"),
                      "download_url": link(f"/api/farm/file/{_quote_path(v['slug'])}/{_quote_path(v['video'])}")
                      if v.get("video") else "", "megabytes": round((v.get("bytes") or 0) / 1e6, 1)}
                     for v in d.get("videos") or []]
    return out


def look_progress(look_id: str) -> dict:
    d = call("GET", "/cr-style-clone/look/" + look_id, timeout=30)
    out = {"id": look_id, "state": d.get("state"), "percent": d.get("percent"), "message": d.get("message"),
           "references": d.get("references")}
    if d.get("state") == "done":
        m = d.get("mapping") or {}
        route = m.get("route") or {}
        out.update(questions=[{"id": q.get("id"), "ask": q.get("ask"), "options": q.get("options")}
                              for q in m.get("questions") or []],
                   suggested_base=m.get("base"), because=m.get("base_why"), made_with=route.get("engine"),
                   medium_gap=route.get("medium_gap") or "", cost_note=m.get("cost") if isinstance(m.get("cost"), str)
                   else "", not_watched=d.get("not_watched") or [],
                   next="Ask the creator these questions (their first option is Studio's guess), then call clone_style "
                        "with look_id, a style name, an example title and their answers.")
    return out


def clone_progress(operation: str) -> dict:
    d = call("GET", f"/cr-style-clone/{operation}/status", timeout=30)
    keep = ("operation_id", "state", "phase", "percent", "message", "elapsed_seconds", "style_ready", "preset_name",
            "needs_manual_recovery")
    out = {k: d[k] for k in keep if k in d}
    out["page"] = link("/cr-style-clone?operation=" + operation)
    if d.get("state") == "done" and d.get("style_ready"):
        out["next"] = ("The style is ready: it appears in list_styles under kind='mine'. Its samples and character "
                       "can be set on the page.")
    return out


def tool_list_videos(args):
    limit = max(1, min(int(args.get("limit") or 20), 60))
    rows = call("GET", "/cr-claude-code/videos", timeout=30).get("videos") or []
    out = {"this_computer": [{
        "title": r.get("title"), "id": "video:" + r["slug"], "style": r.get("style"),
        "made": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("mtime") or 0)),
        "megabytes": round((r.get("size") or 0) / 1e6, 1), "file": r.get("path") or "",
        "watch_url": link("/f/video/" + _quote_path(r["slug"])),
        "project_url": link("/editor/" + _quote_path(r["slug"]))} for r in rows[:limit]]}
    if args.get("include_cloud", True) is not False:
        try:
            cloud = cloud_progress()
            if cloud.get("videos"):
                out["studio_cloud"] = cloud["videos"]
        except Refused:
            pass
    return out


PAGES = {"create": "/", "projects": "/#projects", "gallery": "/cr-gallery", "setup": "/cr-setup",
         "style_clone": "/cr-style-clone", "clip_board": "/cr-ranking"}


def tool_open_in_studio(args):
    page = str(args.get("page") or "projects")
    ident = str(args.get("id") or "").strip()
    slug = ident.removeprefix("video:")
    if page in ("video", "project", "folder"):
        if not _SLUG.fullmatch(slug):
            raise Refused("Give the video's id from list_videos.")
        if page == "folder":
            call("POST", "/api/reveal/" + _quote_path(slug), {}, timeout=20)
            return {"opened": True, "where": "the video's folder on this computer"}
        url = link(("/f/video/" if page == "video" else "/editor/") + _quote_path(slug))
    elif page == "style_clone" and _OPERATION.fullmatch(ident):
        url = link("/cr-style-clone?operation=" + ident)
    elif page == "gallery" and _PICK.fullmatch(ident):
        url = link("/cr-gallery?pick=" + ident)
    elif page in PAGES:
        url = link(PAGES[page])
    else:
        raise Refused("Choose page: video, project, folder, " + ", ".join(PAGES) + ".")
    opened = False
    if args.get("open_browser"):
        opened = open_url(url)
    return {"url": url, "opened_in_browser": opened,
            "tip": "" if opened else "In the Claude desktop app, open this in the Browser pane beside the chat; in a "
                                     "terminal, call again with open_browser=true."}


def open_url(url: str) -> bool:
    if not url.startswith(PUBLIC + "/"):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False, timeout=15)
        elif os.name == "nt":
            os.startfile(url)                                      # noqa: S606 - a localhost Studio page
        else:
            subprocess.run(["xdg-open", url], check=False, timeout=15)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def tool_cloud_balance(args):
    d = call("GET", "/cr-cloud?fresh=1", timeout=30)
    return {"available": bool(d.get("available")), "balance_usd": d.get("balance_usd"), "why": d.get("why") or "",
            "topup_url": d.get("topup_url"), "top_up_amounts_usd": d.get("chunks_usd"),
            "machines": d.get("machines"),
            "note": "Each cloud video reserves its most before it starts and pays only for what it used."}


def tool_clone_style_look(args):
    url = str(args.get("youtube_url") or "").strip()
    if not url:
        raise Refused("Give a YouTube channel or video link.")
    preview = call("POST", "/cr-style-clone/preview", {"url": url}, timeout=150)
    eligible = [c for c in preview.get("candidates") or [] if c.get("eligible")]
    wanted = [str(x) for x in args.get("reference_ids") or []]
    if wanted:
        chosen = [c["id"] for c in eligible if c["id"] in wanted]
        if len(chosen) != len(set(wanted)):
            raise Refused("Choose reference_ids from the eligible videos: " +
                          ", ".join(f"{c['id']} ({c['title'][:60]})" for c in eligible[:12]))
    else:
        chosen = [c["id"] for c in sorted(eligible, key=lambda c: -int(c.get("views") or 0))[:3]]
    if not chosen:
        raise Refused("None of these videos can be studied (they must be between one minute and 24 hours long).")
    look = call("POST", "/cr-style-clone/look", {"preview_id": preview["preview_id"], "reference_ids": chosen},
                timeout=60)
    looks = _read(STATE / "looks.json")
    looks[look["look_id"]] = {"preview_id": preview["preview_id"], "reference_ids": chosen, "url": url, "at": time.time()}
    _write(STATE / "looks.json", dict(sorted(looks.items(), key=lambda kv: -kv[1]["at"])[:40]))
    return {"look_id": look["look_id"], "state": look.get("state"), "message": look.get("message"),
            "studying": [{"id": c["id"], "title": c["title"], "views": c.get("views"),
                          "minutes": round((c.get("duration_seconds") or 0) / 60)} for c in eligible if c["id"] in chosen],
            "other_videos": [{"id": c["id"], "title": c["title"]} for c in eligible if c["id"] not in chosen][:9],
            "next": ("Studio already looked at these: progress(id=look_id) has its questions for the creator."
                     if look.get("state") == "done" else
                     "Studio is looking at them (free, a few minutes). Check with progress(id=look_id); when it's "
                     "done it has questions for the creator.")}


def tool_clone_style(args):
    look_id = str(args.get("look_id") or "")
    saved = _read(STATE / "looks.json").get(look_id)
    if not _LOOK.fullmatch(look_id) or not saved:
        raise Refused("Start with clone_style_look(youtube_url) and use its look_id.")
    label, title = str(args.get("style_name") or "").strip(), str(args.get("example_title") or "").strip()
    if not label or not title:
        raise Refused("Give the new style a name and an example video title.")
    look = call("GET", "/cr-style-clone/look/" + look_id, timeout=30)
    if look.get("state") != "done":
        raise Refused("Studio is still looking at the references: check progress(id=look_id) first.")
    answers = {str(k): str(v) for k, v in (args.get("answers") or {}).items()}
    plan_body = {"answers": answers}
    if args.get("base_style"):
        plan_body["base_style"] = str(args["base_style"])
    mapping = call("POST", f"/cr-style-clone/look/{look_id}/plan", plan_body, timeout=60).get("mapping") or {}
    visual = str(args.get("visual_mode") or "mixed")
    payload = {"preview_id": saved["preview_id"], "reference_ids": saved["reference_ids"], "look_id": look_id,
               "answers": answers, "label": label[:80], "title": title[:280],
               "base_style": str(args.get("base_style") or mapping.get("base") or "documentary"),
               "brief": str(args.get("brief") or "")[:4000], "budget_minutes": 22, "max_calls": 40,
               "minutes": max(1, min(int(args.get("example_minutes") or 10), 60)),
               "visual_mode": visual if visual in ("mixed", "reference_only") else "mixed",
               "acknowledge_costs": True, "accept_medium": bool(args.get("accept_medium")),
               "attempt_id": secrets.token_hex(16)}
    gap = (mapping.get("route") or {}).get("medium_gap") or ""
    if gap and payload["visual_mode"] != "reference_only" and not payload["accept_medium"]:
        return {"started": False, "medium_gap": gap,
                "next": "Read this to the creator. If they accept it, call clone_style again with accept_medium=true."}
    status = call("POST", "/cr-style-clone/start", payload, timeout=120)
    return {"started": True, "id": status.get("operation_id"), "state": status.get("state"),
            "message": status.get("message"), "page": link("/cr-style-clone?operation=" + str(status.get("operation_id"))),
            "next": "The study runs on your Claude Code for about 15 to 30 minutes. Check with progress(id) now and "
                    "then; Studio shows a notification when the style is ready."}


# ── MCP plumbing ──────────────────────────────────────────────────────────────────────────────────────────────

def _schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


_OPTIONS = {"type": "object", "description": (
    "Optional. captions (default true), thumbnails (default true), thumbnail_count 1-4, description (YouTube "
    "description with chapters and sources, default true), extra_instructions (creative direction), script (your "
    "own narration, one title only), features {key: true/false} to switch the style's options (keys from quote), "
    "short_seconds 15-60 for Shorts, ranking {ranks 3-10, narrator_lines, vine_booms, meme_sounds full|light|off, "
    "punch_ins, captions} for Ranking Shorts.")}
_WHERE = {"type": "string", "enum": ["this_computer", "studio_cloud"],
          "description": "this_computer (free machine, one video at a time) or studio_cloud (a ChannelRecipe machine "
                         "renders it, paid from the cloud balance; videos render side by side)."}
_FREE = {"readOnlyHint": True, "openWorldHint": False}

TOOLS = {
    "setup_check": (tool_setup_check, "Is ChannelRecipe Studio running and ready to make videos? Also Claude Code's "
                    "status and the Studio Cloud balance. Free. Call this first.", _schema(), _FREE),
    "list_styles": (tool_list_styles, "The video styles (recipes) installed in Studio, with a one-line description, "
                    "id, a local thumbnail picture, where each can render, and gallery_url: a visual picker page with a "
                    "pick_id to read the creator's choice back with get_style_pick. Free.",
                    _schema({"kind": {"type": "string", "enum": ["all", "videos", "shorts", "mine", "cloud"]},
                             "search": {"type": "string", "description": "Words to look for, e.g. 'history'."}}), _FREE),
    "get_style_pick": (tool_get_style_pick, "The style the creator chose on the gallery page (pick_id from "
                       "list_styles). wait_seconds (up to 100) waits for their click. Free.",
                       _schema({"pick_id": {"type": "string"}, "wait_seconds": {"type": "number"}}, ["pick_id"]), _FREE),
    "quote": (tool_quote, "What a batch of videos costs before anything starts: media on the creator's accounts and, "
              "for studio_cloud, the machine time (expected and the most it reserves) against the cloud balance. "
              "Returns quote_id, which make_videos needs. Free.",
              _schema({"style": {"type": "string"}, "minutes": {"type": "integer", "minimum": 1, "maximum": 60},
                       "titles": {"type": "integer", "minimum": 1, "maximum": MAX_TITLES}, "where": _WHERE,
                       "options": _OPTIONS}, ["style"]), _FREE),
    "make_videos": (tool_make_videos, "Start making up to 5 videos in one style, on this computer or on Studio Cloud. "
                    "Returns ids at once and never waits for a render. Spends money: first get the cost with quote "
                    "(or call without quote_id to get it), tell the creator, then call with quote_id.",
                    _schema({"titles": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                        "maxItems": MAX_TITLES},
                             "style": {"type": "string", "description": "A style id from list_styles."},
                             "minutes": {"type": "integer", "minimum": 1, "maximum": 60,
                                         "description": "Length of each video (Shorts use options.short_seconds)."},
                             "where": _WHERE, "options": _OPTIONS,
                             "quote_id": {"type": "string", "description": "From quote, for these same choices."}},
                            ["titles", "style", "where"]),
                    {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "progress": (tool_progress, "How the creator's videos and style clones are coming along. id: video:<slug>, "
                 "cloud, lk_… (a style look) or op_… (a style study); none for everything. Free. Check every few "
                 "minutes, not in a tight loop.", _schema({"id": {"type": "string"}}), _FREE),
    "list_videos": (tool_list_videos, "Finished videos: on this computer (file path, watch link, project link) and on "
                    "Studio Cloud (download link). Free.",
                    _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 60},
                             "include_cloud": {"type": "boolean"}}), _FREE),
    "open_in_studio": (tool_open_in_studio, "A Studio link: page=video (watch; id=video:…), project (editor), folder "
                       "(show the file in Finder/Explorer), gallery (id=pick_id for the style picker), create, "
                       "projects, setup, style_clone (id=op_…), clip_board. Returns the URL; open_browser=true opens "
                       "it in the creator's browser (use that in a terminal; in the desktop app use the Browser pane).",
                       _schema({"page": {"type": "string"}, "id": {"type": "string"},
                                "open_browser": {"type": "boolean"}}), {"readOnlyHint": True}),
    "cloud_balance": (tool_cloud_balance, "The Studio Cloud balance, top-up link and machine prices. Free.",
                      _schema(), _FREE),
    "clone_style_look": (tool_clone_style_look, "Step 1 of cloning a style from a YouTube channel or video: Studio "
                         "lists its videos and looks at up to 3 (the most viewed, or reference_ids). Free (no "
                         "Claude). Then progress(look_id) until done: it brings questions for the creator.",
                         _schema({"youtube_url": {"type": "string"},
                                  "reference_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
                                 ["youtube_url"]), {"readOnlyHint": False, "destructiveHint": False}),
    "clone_style": (tool_clone_style, "Step 2: study the looked-at references and build a new reusable style. Uses "
                    "the creator's Claude Code for 15-30 minutes and one of their plan's style clones. Needs look_id, a "
                    "style_name, an example_title and the creator's answers to the look's questions.",
                    _schema({"look_id": {"type": "string"}, "style_name": {"type": "string"},
                             "example_title": {"type": "string"},
                             "answers": {"type": "object", "additionalProperties": {"type": "string"}},
                             "base_style": {"type": "string"}, "visual_mode": {"type": "string",
                                                                               "enum": ["mixed", "reference_only"]},
                             "brief": {"type": "string"}, "example_minutes": {"type": "integer"},
                             "accept_medium": {"type": "boolean"}},
                            ["look_id", "style_name", "example_title"]),
                    {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
}


def run_tool(name: str, args: dict) -> dict:
    entry = TOOLS.get(name)
    if entry is None:
        return _result({"error": f"Unknown tool {name}."}, error=True)
    try:
        return _result(entry[0](args if isinstance(args, dict) else {}))
    except (Closed, Refused) as error:
        return _result({"error": str(error)}, error=True)
    except (TypeError, ValueError) as error:
        return _result({"error": f"Check the arguments: {error}"[:400]}, error=True)
    except Exception as error:                                     # noqa: BLE001 - one tool never stops the server
        return _result({"error": f"Something went wrong: {type(error).__name__}. Studio's work is unchanged."},
                       error=True)


def _result(data: dict, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=1, ensure_ascii=False)}],
            "structuredContent": data, "isError": error}


def handle(message: dict):
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        return None                                                # notifications need no answer
    params = message.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                  "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
                  "instructions": "ChannelRecipe Studio's own tools: make videos in any installed style, on this "
                                  "computer or Studio Cloud, clone styles from YouTube, and watch finished videos. "
                                  "Start with setup_check. Never read .env, .mcp.json or .cr-* files."}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": s, "annotations": a}
                            for n, (_, d, s, a) in TOOLS.items()]}
    elif method == "tools/call":
        result = run_tool(str(params.get("name") or ""), params.get("arguments") or {})
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def serve(stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            batch = message if isinstance(message, list) else [message]
            replies = [r for r in (handle(m) for m in batch if isinstance(m, dict)) if r is not None]
            reply = replies if isinstance(message, list) else (replies[0] if replies else None)
        if reply:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        print(json.dumps(run_tool("setup_check", {})["structuredContent"], indent=1))
        return 0
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
