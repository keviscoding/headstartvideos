"""ChannelRecipe Studio inside Claude Code: the `studio` tool server (MCP over stdio, standard library only).

Claude Code starts this with Studio's own Python (the `studio` entry in .mcp.json, written by cr_claude_code.configure).
Every tool calls Studio's own local routes on http://127.0.0.1:7860 with the local token from .cr-runtime.json, so
each check, gate and receipt the Studio page uses applies here too. Nothing here renders, reads keys or spends by
itself: paid work starts only through make_videos and clone_style, which Claude Code asks the creator to approve
(permissions.ask in .claude/settings.json), and make_videos starts nothing until the cost was shown (its quote_id).
delete_video and delete_style are asked too; they move the item into Studio's trash (30 days), never delete at once.

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
_SAMPLE = re.compile(r"sm_[a-f0-9]{24}\Z")
_LEARNED = re.compile(r"cr_clone_[a-z0-9_-]{1,40}\Z")
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

def _runtime() -> dict:
    try:
        data = json.loads((HOME / ".cr-runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _token() -> str:
    return str(_runtime().get("token") or "")


def output_root():
    """Where the running Studio's videos go (its engine's OUTPUT_ROOT: CHANNELRECIPE_OUTPUT moves it), or None."""
    return str(_runtime().get("output") or "") or None


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


def other_folder() -> str:
    """The folder of the Studio answering on this computer when it isn't this one ('' when it is, or when an older
    Studio can't say). Two copies of Studio can't run at once: the tools here must not act on another's projects."""
    try:
        running = str(call("GET", "/cr-claude-code/home", timeout=10).get("home") or "")
    except Refused:
        return ""
    try:
        return "" if not running or Path(running).resolve() == HOME.resolve() else running
    except OSError:
        return ""


def same_studio() -> None:
    elsewhere = other_folder()
    if elsewhere:
        raise Refused(f"The Studio running on this computer is the copy in \"{elsewhere}\", not this folder. Open "
                      "Claude Code in that folder to use it (your projects and settings are there), or close it "
                      "and start this folder's launcher. Keep one Studio folder: a second copy hides your projects.")


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


def clip_link(clip) -> str:
    url = clip.get("url") if isinstance(clip, dict) and isinstance(clip.get("url"), str) else ""
    if url.startswith("/f/video/"):
        start, end = clip.get("start") or 0, clip.get("end") or 0
        return link(url) + (f"#t={start:g},{end:g}" if end else "")
    return url if url.startswith("https://") else ""


def style_card(row: dict, thumb: str = "") -> dict:
    block = cloud_block(row)
    return {"id": row["name"], "name": row.get("label") or row["name"],
            "description": one_line(row.get("tagline") or row.get("description")),
            "family": row.get("family") or "", "kind": kind(row), "mine": bool(row.get("custom")),
            "renders_on": ["this_computer"] + ([] if block else ["studio_cloud"]),
            "cloud_note": block, "media_cost": one_line(row.get("cost"), 220) if row.get("cost") else "",
            "short_seconds": row.get("short_seconds"), "needs_panels": bool(row.get("panels")),
            "thumbnail_path": thumb, "preview_url": link(row["preview_url"]) if row.get("preview_url") else "",
            # a few moving seconds of the style (cr_recipe_clips): the gallery plays it on hover; a link to show them
            "moving_preview_url": clip_link(row.get("preview_clip")),
            # burned-in captions by the style's own design (false: none, like its channel; captions=true adds them)
            "captions": row.get("captions", True) is not False,
            # its recurring character (options.character / character_picture change it for a video)
            "character": one_line(row.get("character"), 300) if isinstance(row.get("character"), str) else "",
            # the language its videos speak by its own file (options.language makes one in another)
            "language": language_name(row.get("language") or "en")}


def language_name(lang_id) -> str:
    import cr_language                     # standard library only, like this file
    return cr_language.name(lang_id)


def language_of(cat: dict, row: dict, options: dict) -> str:
    """The language these videos speak, as an id: options.language (a name like Spanish, its own name like Español,
    or a code like es), else the one the creator chose last in Create (Studio's default), else the style's own."""
    import cr_language
    try:
        asked = cr_language.clean(options.get("language"), {"language": row.get("language") or "en"})
    except ValueError as error:
        raise Refused(str(error)) from None
    if kind(row) == "short" and not row.get("any_language"):
        # a ready-made Shorts recipe whose pipeline doesn't take a language yet speaks its own (cr_language.SHORTS_NOTE):
        # asked for another, it says so before anything starts; Studio's default for other videos never applies to it
        own = row.get("language") or "en"
        if asked and asked != own:
            raise Refused(cr_language.shorts_refusal({"format": "short", "language": own}, asked,
                                                     row.get("label") or "This Shorts recipe", bool(row.get("ranking"))))
        return own
    return asked or cat.get("default_language") or row.get("language") or "en"


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


def coded_usd(row: dict, minutes: float) -> float:
    """A style drawn by its own renderer: its background pictures plus a piece of its own music every ~35 s (the
    catalog's coded_media), which a quote once left out ("$0.00 in all" for a video that paid ~$0.40)."""
    coded = row.get("coded_media") if isinstance(row.get("coded_media"), dict) else {}
    if not coded:
        return 0.0
    pieces = max(1, -(-int(round(float(minutes) * 60)) // max(10, int(coded.get("piece_seconds") or 35))))
    return float(coded.get("plates_usd") or 0) + pieces * float(coded.get("music_usd_per_piece") or 0)


def picture_usd(cat: dict) -> float:
    """One AI picture on the creator's image account at their Setup model (the catalog's picture_price), else the
    engine's estimate."""
    for value in ((cat.get("picture_price") or {}).get("usd"), (cat.get("prices") or {}).get("picture")):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
    return 0.02


def thumbnail_count(row: dict, options: dict) -> int:
    """How many YouTube thumbnails a video makes: options.thumbnails=false makes none (Swapnil, 2026-10-29: "save some
    Kie credits"); a Short makes none either way."""
    if kind(row) == "short" or (options or {}).get("thumbnails", True) is False:
        return 0
    try:
        return max(1, min(int((options or {}).get("thumbnail_count") or 2), 4))
    except (TypeError, ValueError):
        return 2


def media_estimate(cat: dict, row: dict, minutes: int, features: dict, thumbnails: int = 0) -> dict:
    prices = cat.get("prices") or {}
    per = prices.get("features") or {}
    voice = float(((prices.get("voice") or {}).get("per_min")) or 0)
    fish = cat.get("narration_voice") if isinstance(cat.get("narration_voice"), dict) else {}
    fish_reads = bool(fish) and not row.get("ranking") and (row.get("shorts") or {}).get("pipeline") not in ("ranking",
                                                                                                             "roblox")
    if fish_reads:
        voice = float(fish.get("usd_per_min") or 0)               # the creator's own Fish Audio key reads it
    total = voice + sum(float((per.get(k) or {}).get("per_min") or 0) for k, on in features.items() if on)
    each = total * minutes + coded_usd(row, minutes)
    if kind(row) == "short" and isinstance(row.get("short_cost_usd"), (int, float)):
        each = float(row["short_cost_usd"])                        # the recipe's own estimate for one Short
    # the YouTube thumbnails: one AI picture each on the image account, none when the creator turns them off
    thumbs = {"count": thumbnails, "usd": round(thumbnails * picture_usd(cat), 2), "offered": kind(row) != "short",
              "note": "Each YouTube thumbnail is one AI picture on your image account. options.thumbnails=false "
                      "makes none and saves that." if thumbnails else "No YouTube thumbnails for these videos."}
    each += thumbs["usd"]
    out = {"per_video_usd": round(each, 2), "thumbnails": thumbs, "recipe_note": one_line(row.get("cost"), 260),
           "note": "Media runs on your own accounts (pictures, voice, footage watching). Claude's writing and "
                   "directing run on your Claude Code plan, not billed per video."}
    if fish_reads:
        out["voice"] = {"provider": "fish", "model": fish.get("model"), "usd_per_min": fish.get("usd_per_min"),
                        "note": f"Narration: Fish Audio {fish.get('model')} on the creator's own Fish Audio API "
                                f"balance, ${float(fish.get('usd_per_million_bytes') or 0):g} a million bytes of "
                                f"script (about ${float(fish.get('usd_per_min') or 0) * minutes:.2f} for {minutes} "
                                "min)."}
    return out


def quote_for(cat: dict, row: dict, *, titles: int, minutes: int, where: str, features: dict,
              thumbnails: int = 0) -> dict:
    media = media_estimate(cat, row, minutes, features, thumbnails)
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
        if cloud.get("byo"):
            # the creator's own Modal account: Modal bills the machine time to them; no ChannelRecipe balance
            out["cloud"] = {"machine": cloud.get("label"), "own_account": "Modal",
                            "expected_minutes": cloud.get("expected_minutes"),
                            "expected_usd_each": round(expected, 2), "most_usd_each": round(most, 2),
                            "first_setup": "" if cloud.get("deployed") else
                            "The first cloud video also sets up Studio's machines on their Modal account (about 5 "
                            "to 10 minutes, once).",
                            "note": "Billed by Modal to the creator's own account, per second; each machine stops by "
                                    "itself at its time limit."}
            out["expected_total_usd"] = round((expected + media["per_video_usd"]) * titles, 2)
            out["most_reserved_usd"] = round(most * titles, 2)
        else:
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
        out["this_computer"] = ("Renders on this computer, one video at a time: while one is made, these wait in "
                                "Studio's queue and start by themselves. No machine to pay for.")
        line = queue_line()
        if line["before"]:
            out["queue"] = {"videos_before_these": line["before"], "on_hold": line["on_hold"],
                            "note": "They join Studio's queue behind the videos already there."}
        # the disk holds each video's working folder: one that can't fit is said with the cost, not after it; the
        # videos are made one at a time, so a batch only warns about the ones after the first (and the queue's)
        import cr_machine
        threed = bool(features.get("threed"))
        free = cr_machine.free_disk_gb(HOME, output_root())
        out["disk"] = {"needs_gb": cr_machine.need_gb(minutes, 1, threed),
                       "comfortable_gb": cr_machine.comfortable_gb(minutes, titles, threed), "free_gb": free}
        room, warning = cr_machine.disk_check(free, minutes, titles, threed, ahead=line["ahead_gb"])
        if room:
            out["disk"]["problem"] = room
        elif warning:
            out["disk"]["warning"] = warning
    check = row.get("frame_check") if isinstance(row.get("frame_check"), dict) else None
    if check:
        # a coded style's frame check (cr_animated2d): Claude looks at each minute's frames before they are drawn and
        # re-plans a minute once when it finds something clear: Claude time on their plan, no money, said with the cost
        lo, hi = check.get("per_minute") or (1.0, 2.5)
        span = max(1, minutes if kind(row) != "short" else 1)
        out["frame_check"] = {"on": bool(check.get("on", True)), "adds_minutes": [max(1, round(lo * span)), max(2, round(hi * span))],
                              "note": "Before each minute is drawn, Claude looks at its frames and re-plans it once when it "
                                      "shows the wrong place, broken words or a frozen picture: Claude time on your plan, "
                                      "no money. make_videos options.frame_check=false skips it for a video."}
    if features.get("youtube") and kind(row) != "short":
        # Research footage watches YouTube videos before the video is made: how long that adds, said with the cost
        import cr_watch
        b = cr_watch.budget(minutes)
        out["footage"] = {"videos_watched_at_most": b["videos"], "adds_minutes": b["typical_minutes"],
                          "stops_watching_after_minutes": b["stop_minutes"], "note": cr_watch.estimate_text(minutes)
                          + " Turn off the youtube feature (options.features) for a faster video without real footage."}
    key = json.dumps([row["name"], titles, minutes, where, sorted(k for k, v in features.items() if v)]
                     + ([thumbnails] if thumbnails != 2 else []))       # the usual 2 keeps quote ids as they were
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
    t = q["media"].get("thumbnails") or {}
    if t.get("count"):
        media += f" (including {t['count']} YouTube thumbnail{'' if t['count'] == 1 else 's'}, about ${t['usd']:.2f})"
    elif t.get("offered"):
        media += " (no YouTube thumbnails)"
    footage = q.get("footage")
    # the footage is watched on this computer wherever the video renders
    watching = (f" Research footage adds about {footage['adds_minutes'][0]} to {footage['adds_minutes'][1]} minutes a "
                f"video (it watches up to {footage['videos_watched_at_most']} YouTube videos)." if footage else "")
    check = q.get("frame_check") or {}
    if check.get("on"):
        lo, hi = check["adds_minutes"]
        watching += (f" The frame check adds about {lo} to {hi} minutes of Claude time a video (Claude looks at each "
                     "minute's frames before they are drawn; options.frame_check=false skips it).")
    if q["where"] == "studio_cloud" and q["cloud"].get("own_account"):
        c = q["cloud"]
        text = (f"{n} × {q['minutes']}-min {q['style_name']} on Studio Cloud (their own Modal account): machine about "
                f"${c['expected_usd_each']:.2f}{each} on their Modal bill (at most ${c['most_usd_each']:.2f}{each}), "
                f"{media}; about ${q['expected_total_usd']:.2f} in all.")
        if c.get("first_setup"):
            text += " " + c["first_setup"]
        return text + watching
    if q["where"] == "studio_cloud":
        c = q["cloud"]
        text = (f"{n} × {q['minutes']}-min {q['style_name']} on Studio Cloud: machine about "
                f"${c['expected_usd_each']:.2f}{each} (up to ${c['reserved_usd_each']:.2f}{each} reserved, only what "
                f"it uses is charged), {media}; about ${q['expected_total_usd']:.2f} in all. Cloud balance "
                f"${c['balance_usd']:.2f}.")
        if q.get("short_by_usd"):
            text += f" Short by ${q['short_by_usd']:.2f}: top up at {c['topup_url']}."
        return text + watching
    disk = q.get("disk") or {}
    room = (" " + (disk.get("problem") or disk.get("warning"))) if disk.get("problem") or disk.get("warning") else ""
    return (f"{n} × {q['minutes']}-min {q['style_name']} on this computer: {media}; about "
            f"${q['expected_total_usd']:.2f} in all, plus Claude Code usage on your plan." + watching + room)


# ── the tools ─────────────────────────────────────────────────────────────────────────────────────────────────

# ── the queue (cr_queue): Studio makes one video at a time; more wait their turn ──────────────────────────────────

def queue_state(ahead=False) -> dict:
    """Studio's /cr-queue: what's on hold and why ({} from a Studio that can't say)."""
    try:
        return call("GET", "/cr-queue" + ("?ahead=1" if ahead else ""), timeout=60)
    except Refused:
        return {}


def queue_line() -> dict:
    """How many videos are before a new one (being made, waiting, on hold) and what they still need on the disk."""
    try:
        jobs = call("GET", "/api/queue", timeout=20).get("jobs") or []
    except Refused:
        jobs = []
    live = [j for j in jobs if j.get("state") in ("running", "queued", "held")]
    state = queue_state(ahead=True) if live else {}
    return {"before": len(live), "on_hold": bool(state.get("held")), "ahead_gb": float(state.get("ahead_gb") or 0)}


def queue_row(job: dict) -> dict:
    """One video of /api/queue as the tools say it: its place in line, or why it's on hold."""
    row = {"id": "video:" + str(job.get("slug")), "title": job.get("title"), "state": job.get("state")}
    if job.get("state") == "running":
        row.update(percent=round(100 * float(job.get("progress") or 0)), minutes_so_far=round((job.get("elapsed")
                   or 0) / 60), last_line=job.get("line") or "")
    elif job.get("state") == "queued":
        if job.get("position"):
            row.update(position=job["position"], videos_before_it=job.get("ahead", job["position"] - 1))
    elif job.get("state") == "held":
        row["on_hold_because"] = job.get("reason") or ""
    if job.get("error"):
        row["error"] = job["error"]
    return row


HOLD_NOTE = ("These videos are on hold: something every video needs stopped (on_hold_because says what, and what to "
             "do). Nothing of theirs was made or paid for. Tell the creator in plain words. When they've fixed it and "
             "want to go on, call carry_on_queue (Studio checks each one's setup again; Claude Code asks them to "
             "approve); carry_on_queue(remove=true) takes them out of the queue instead. Never carry on for them.")


def tool_queue(args):
    """Studio's queue: the video being made, the ones waiting (in order) and the ones on hold, with why."""
    jobs = call("GET", "/api/queue", timeout=20).get("jobs") or []
    rows = [queue_row(j) for j in jobs if j.get("state") in ("running", "queued", "held")]
    state = queue_state()
    out = {"videos": rows, "waiting": sum(r["state"] == "queued" for r in rows),
           "on_hold": sum(r["state"] == "held" for r in rows)}
    if state.get("held"):
        out["hold"] = {"reason": state.get("reason") or "", "videos": len(state["held"]),
                       "can_use_credits": state.get("code") == "out_of_watches" or bool(state.get("credits"))}
        out["next"] = HOLD_NOTE + (" Ranking Shorts held because the month's clip watches ran out can go on with "
                                   "carry_on_queue(use_credits=true), 0.02 credits a watch, only if the creator says "
                                   "so." if out["hold"]["can_use_credits"] else "")
    elif rows:
        out["next"] = ("Studio makes them one at a time, by itself: the creator doesn't need to wait or press anything. "
                       "Check again in a few minutes, not in a loop; Studio shows a notification when each is done.")
    else:
        out["next"] = "Nothing is being made or waiting."
    return out


def tool_carry_on_queue(args):
    """Carry on (or Remove) the videos on hold, as the Studio page's notice does."""
    same_studio()
    state = queue_state()
    if not state.get("held"):
        return {"carried_on": 0, "message": "Nothing is on hold."}
    if args.get("remove") is True:
        got = call("POST", "/cr-queue/drop", {}, timeout=30)
        return {"removed": got.get("removed", 0),
                "message": "Taken out of the queue. Nothing of theirs was made or paid for; make them again any time."}
    credits = args.get("use_credits") is True
    if credits and state.get("code") != "out_of_watches" and not state.get("credits"):
        raise Refused("use_credits is only for Ranking Shorts held because the month's clip watches ran out.")
    got = call("POST", "/cr-queue/resume", {"use_credits": credits}, timeout=240)
    still = [{"id": "video:" + h["slug"], "title": h.get("title"), "on_hold_because": h.get("reason") or ""}
             for h in got.get("held") or []]
    out = {"carried_on": got.get("resumed", 0), "still_on_hold": still}
    if got.get("checking") and not out["carried_on"]:
        out["next"] = "Studio is already checking them (a Carry on from the Studio page). Check queue in a moment."
        return out
    out["next"] = ("Back in the queue: Studio makes them one at a time, by itself. " if out["carried_on"] else "") + (
        "Some are still on hold: Studio checked their setup again and they can't start yet (on_hold_because). Tell "
        "the creator what to fix." if still else "Check queue or progress now and then.")
    return out


def only_models_check(data) -> bool:
    """The models' daily check is the only thing in the way (readiness key "models"; older Studios: its words)."""
    data = data or {}
    failing = [c for c in data.get("checks") or [] if isinstance(c, dict) and c.get("ready") is False]
    if failing:
        return all(c.get("key") == "models" for c in failing)
    blockers = data.get("blockers") or []
    return bool(blockers) and all("Verify both Claude models" in str(b) or "Verify my Claude models" in str(b)
                                  for b in blockers)


def tool_setup_check(args):
    try:
        status = call("GET", "/cr-status", timeout=60)
    except Closed:
        return {"running": False, "message": CLOSED,
                "launcher": str(HOME / ("Start on Windows.cmd" if os.name == "nt" else "Start on Mac.command"))}
    elsewhere = other_folder()
    if elsewhere:
        return {"running": True, "this_folder": False, "running_folder": elsewhere,
                "message": f"The Studio running on this computer is the copy in \"{elsewhere}\", not this folder. "
                           "Open Claude Code in that folder to make videos (your projects and settings are there), "
                           "or close that Studio's launcher window and start this folder's launcher instead."}
    readiness = status.get("readiness") or {}
    claude = status.get("claude_code") or {}
    try:
        cloud = call("GET", "/cr-cloud", timeout=30)
    except Refused:
        cloud = {"available": False, "why": "Studio Cloud's status didn't load."}
    blockers = readiness.get("blockers") or []
    speech = status.get("speech_model") or {}
    only_verify = bool(blockers) and only_models_check(readiness)
    return {"running": True, "studio_version": str(status.get("local_version") or "").replace("5.3-admin.", ""),
            "ready": bool(readiness.get("ready")), "blockers": blockers,
            "fixes_itself": "make_videos runs the tiny daily Claude model check itself." if only_verify else "",
            # a customer still on the old API route (an API account as Studio's brain): Studio makes every video
            # with Claude Code now; the switch is one button in setup (it also checks their Claude models)
            **({"old_api_route": {"on": True, "next_video_switches_by_itself":
                                  bool((status.get("api_route") or {}).get("ready_to_switch")),
                                  "fix": "Tell the creator Studio now makes every video with Claude Code on their "
                                         "Claude plan, and ask them to press Switch to Claude Code (recommended) "
                                         "on the setup page, step 2 (setup_page). Their API key stays saved."}}
               if status.get("brain_mode") and status.get("brain_mode") != "claudecode" else {}),
            "setup_page": link("/cr-setup"),
            "graphics_browser": {"ready": bool(status.get("browser")), "why": status.get("browser_reason") or "",
                                 "fix": "" if status.get("browser") else
                                 # a full disk, too many open files or a busy computer: a reinstall won't help
                                 "Don't call fix_graphics_browser: reinstalling won't help. Tell the creator what "
                                 "'why' says (never delete their files for them), then call setup_check again."
                                 if (status.get("can_fix") or {}).get("browser") is False else
                                 "Call fix_graphics_browser: Studio installs it itself. Don't install it from this "
                                 "shell; Claude Code's shell can be sandboxed, and Studio wouldn't see it."},
            "video_tools": tool_summary(status, "ffmpeg", "fix_ffmpeg"),
            "node": {**tool_summary(status, "node", "fix_node"),
                     "blocks_every_video": not status.get("node", True),
                     "note": "Only videos that download from YouTube (Research footage, Ranking Shorts) need Node.js "
                             "22 or newer; Studio refuses those before anything is paid until fix_node has run."
                             if status.get("node", True) and not status.get("node_youtube", True) else ""},
            "network": network_summary(status.get("network") or {}),
            "computer": computer_summary(status.get("computer")),
            "speech_model": {"ready": bool(speech.get("ready")), "state": speech.get("state") or "",
                             "why": speech.get("reason") or "",
                             "fix": "" if speech.get("ready") else
                             "Studio downloads it by itself while it's 'downloading' or 'checking': call setup_check "
                             "again in a minute. Otherwise call fix_speech_model (Studio downloads and checks it "
                             "itself; never install it from this shell)."},
            "claude_code": {"installed": bool(claude.get("installed")), "signed_in": bool(claude.get("authenticated")),
                            "fix": "" if claude.get("installed") else
                            "Call fix_claude_cli: Studio runs Anthropic's official Claude Code installer itself. Never "
                            "install Claude Code from this shell. Then the creator signs in once (setup_page, step 2: "
                            "Open the sign-in window).",
                            "models_verified": bool(claude.get("models_verified")),
                            "usage_limit_reached": bool((claude.get("allowance") or {}).get("blocked"))},
            "studio_cloud": {"available": bool(cloud.get("available")), "balance_usd": cloud.get("balance_usd"),
                             "why": cloud.get("why") or "", "topup_url": cloud.get("topup_url"),
                             **({"own_account": "Modal", "setup_page": link("/cr-setup#cloud-render")}
                                if cloud.get("byo") else {})},
            "gallery_url": link("/cr-gallery")}


def tool_summary(status: dict, key: str, fix: str) -> dict:
    """FFmpeg or Node.js: ready, why not, and the fix tool that installs it in Studio's own folder."""
    # Node.js: ready for YouTube downloads (22 or newer); an older one stops only the videos that use YouTube
    ready = bool(status.get("node_youtube", status.get("node")) if key == "node" else status.get(key))
    can_fix = bool((status.get("can_fix") or {}).get(key, True))
    return {"ready": ready, "why": "" if ready else str(status.get(key + "_reason") or ""),
            "fix": "" if ready else (f"Call {fix}: Studio downloads it into its own folder (pinned version, checksum "
                                     "checked, no password). Never install it from this shell: Studio wouldn't see "
                                     "it, and Homebrew or winget would ask for the creator's password."
                                     if can_fix else "Studio can't install it on this computer: help the creator "
                                     "install it with their system's package manager, then call setup_check.")}


def computer_summary(pc) -> dict:
    """What this computer has (memory, cores, free disk) and where it's short of what Studio uses. Never a stop by
    itself: make_videos checks the disk against each video and refuses one that can't fit, saying how much to free."""
    if not pc:
        return {"checked": False}
    return {"checked": True, "memory_gb": pc.get("memory_gb"), "cores": pc.get("cores"),
            "free_disk_gb": pc.get("free_disk_gb"),
            "disk_needed_gb": {"10_minute_video": pc.get("ten_minute_video_gb"),
                               "10_minute_3d_video": pc.get("ten_minute_3d_video_gb")},
            "disk_comfortable_gb": {"10_minute_video": pc.get("ten_minute_comfortable_gb"),
                                    "10_minute_3d_video": pc.get("ten_minute_3d_comfortable_gb")},
            "short_of": pc.get("short") or [], "notes": pc.get("notes") or [],
            "what_to_do": "Tell the creator these notes in plain words before a long video: free disk space (empty "
                          "the Bin or Recycle Bin, delete old downloads) or close other apps. Never delete their files "
                          "yourself." if pc.get("notes") else ""}


def network_summary(net: dict) -> dict:
    """Which services this computer can't reach, in plain words: `blocked` stops videos (with what to do), `goes_around`
    are blocked hosts Studio reaches through channelrecipe.com, `youtube_only` stops only recipes that use YouTube."""
    rows = [r for r in net.get("hosts") or [] if not r.get("reachable") and r.get("needed", True)]
    pick = lambda flag: [{"host": r["host"], "for": r.get("for", ""), "message": r.get("message", "")}
                         for r in rows if r.get(flag)]
    local = net.get("local_block") or {}
    return {"ready": net.get("ready", True) is not False, "checking": bool(net.get("checking") and not rows),
            "blocked": pick("blocking"), "goes_around": pick("fallback"), "youtube_only": pick("warning"),
            "on_this_computer": {"suspected": bool(local.get("suspected")), "antivirus": local.get("antivirus") or "",
                                 "allow_programs": local.get("programs") or [], "message": local.get("message") or ""},
            "message": net.get("message") or "",
            "what_to_do": "Tell the creator in plain words which service is blocked and what to do (allow it in their "
                          "firewall, antivirus or VPN, or try another network such as a phone hotspot), then call "
                          "setup_check again. Hosts under goes_around need nothing: Studio already works around them. "
                          "When on_this_computer.suspected, the block is an antivirus or firewall on this computer, "
                          "not the network: walk them through allowing allow_programs and 127.0.0.1:7860 in it (it is "
                          "named when Studio found it running). Never suggest switching it off."
                          if rows or local.get("suspected") else ""}


def tool_fix_speech_model(args):
    """Studio downloads its speech-timing model (again, when it won't load) and loads it once, in the background."""
    try:
        state = call("POST", "/cr-setup/fix-speech-model", {}, timeout=60)
    except Closed:
        return {"running": False, "message": CLOSED}
    return {"state": state.get("state"), "message": "Studio is downloading and checking its speech-timing model (about "
            "140 MB, a minute or two). Call setup_check in a minute; speech_model.ready turns true when it works."}


def _fix(path: str, what: str, check: str) -> dict:
    try:
        state = call("POST", path, {}, timeout=60)
    except Closed:
        return {"running": False, "message": CLOSED}
    if state.get("state") == "failed":
        return {"state": "failed", "message": state.get("message") or "It stopped. Call setup_check for details."}
    return {"state": state.get("state"), "message": f"Studio is installing {what} in its own folder, in the background "
            f"(a minute or two). Call setup_check in a minute; {check} turns true when it works. If it says failed, "
            "read its message to the creator."}


def tool_fix_ffmpeg(args):
    """Studio downloads FFmpeg + ffprobe (pinned, SHA-256 checked) into its own folder and puts them first on PATH."""
    return _fix("/cr-setup/fix-ffmpeg", "FFmpeg", "video_tools.ready")


def tool_fix_node(args):
    """Studio downloads Node.js (pinned, checked against nodejs.org's SHASUMS256.txt) into its own folder."""
    return _fix("/cr-setup/fix-node", "Node.js", "node.ready")


def tool_fix_claude_cli(args):
    """Studio runs Anthropic's official Claude Code installer, then tells the creator how to sign in once."""
    try:
        state = call("POST", "/cr-setup/fix-claude", {}, timeout=60)
    except Closed:
        return {"running": False, "message": CLOSED}
    return {"state": state.get("state"), "message": (state.get("message") or "") if state.get("state") in (
        "done", "failed") else "Studio is running Anthropic's official Claude Code installer (a minute or two). Call "
        "setup_check in a couple of minutes: claude_code.installed turns true. Then the creator signs in once: on "
        "Studio's setup page (setup_page), step 2, Open the sign-in window, and they sign in in their browser with "
        "their Claude Pro or Max account. Never type or see their password; never sign in for them."}


def tool_fix_graphics_browser(args):
    """Studio installs (or reinstalls) its graphics browser in its own environment, in the background."""
    try:
        state = call("POST", "/cr-setup/fix-browser", {}, timeout=60)
    except Closed:
        return {"running": False, "message": CLOSED}
    return {"state": state.get("state"), "message": "Studio is installing its graphics browser (a few minutes). "
            "Call setup_check in a couple of minutes; graphics_browser.ready turns true when it works."
            if state.get("state") == "installing" else "Done: call setup_check to confirm."}


def tool_list_styles(args):
    same_studio()
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
    if cat.get("languages"):
        # what make_videos options.language can be, and the one Create starts at (the creator's last choice)
        out["languages"] = [l.get("name") for l in cat["languages"] if isinstance(l, dict)]
        out["default_language"] = language_name(cat.get("default_language")) if cat.get("default_language") else \
            "each style's own (its card's language)"
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
    same_studio()
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
    options = args.get("options") or {}
    if not isinstance(options, dict):
        raise Refused("options must be an object.")
    character_choice(row, options, where)
    q = quote_for(cat, row, titles=titles, minutes=minutes, where=where, features=features_for(row, options.get("features")),
                  thumbnails=thumbnail_count(row, options))
    if character_note(row, options):
        q["character"] = character_note(row, options)
    return q


def where_of(args) -> str:
    where = str(args.get("where") or "this_computer")
    if where not in ("this_computer", "studio_cloud"):
        raise Refused("Choose where='this_computer' or where='studio_cloud'.")
    return where


def video_payload(row: dict, titles: list, minutes: int, features: dict, options: dict, language: str = "") -> dict:
    """What the Studio page's Create button sends (cr_workspace.js payload()), with its defaults. language: the
    language the videos speak (language_of)."""
    if row.get("panels"):
        raise Refused(f"{row.get('label')} reads your own manga panels, which you choose on the Studio page: "
                      f"open_in_studio(page='create').")
    extra = str(options.get("extra_instructions") or "").strip()
    thumbs = options.get("thumbnails", True) is not False
    description = options.get("description", True) is not False
    steps = ["script", "voiceover", "images", "pexels", "video"] + (["thumbnail"] if thumbs else [])
    p = {"title": "\n".join(titles), "style": row["name"], "minutes": minutes, "steps": steps,
         "script": str(options.get("script") or "").strip(), "extra": extra, "byo_audio": "",
         # captions: the style's own choice unless asked (some styles have none, like their channel: list_styles'
         # captions false); captions=true burns them in anyway
         "burn_subs": (options["captions"] is not False) if "captions" in options else row.get("captions", True) is not False,
         "cr_captions": "on" if options.get("captions") is True and row.get("captions") is False else "",
         "force": False,
         "thumb_count": max(1, min(int(options.get("thumbnail_count") or 2), 4)),
         "desc": description, "chapters": 5 if description else 0, "sources": 3 if description else 0,
         "features": features, "render_backend": "original", "character": "", "character_image": "",
         "avatar_id": "", "avatar_s": 8, "avatar_count": 1, "offer": "", "offer_plugs": 0, "panels": "",
         "cta": None, "pictures_per_min": None, "threed_look": "",
         # Create's Quicker 3D review: one 3D review round (cr_threed_look), for styles with 3D scenes only
         "threed_quick": options.get("quicker_3d_review") is True and bool((features or {}).get("threed"))}
    if row.get("frame_check") and isinstance(options.get("frame_check"), bool):
        p["frame_check"] = options["frame_check"]                   # a coded style's frame check, for this video only
    if language:
        p["language"] = language            # its script, voice, captions and words on screen (cr_language)
    voice = str(options.get("voice") or "").strip()
    if voice:
        found = _FISH_ID.search(voice)
        if not found:
            raise Refused("options.voice takes a Fish Audio voice id or fish.audio voice link (from voices()).")
        p["cr_voice"] = {"provider": "fish", "voice": found.group(1).lower()}   # these videos only (cr_fish)
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


CHARACTER_PICTURE = (".png", ".jpg", ".jpeg", ".webp")


def character_choice(row: dict, options: dict, where: str) -> tuple:
    """(words, picture path) from options.character and options.character_picture, checked before anything starts."""
    words = str(options.get("character") or "").strip()
    picture = str(options.get("character_picture") or "").strip()
    if not (words or picture):
        return "", ""
    use = row.get("character_picture") or {}
    if kind(row) == "short" and use.get("use") != "avatar":
        raise Refused("Shorts keep their own host. A character of your own works in the long-video styles"
                      " and Roblox Story Shorts (a picture of your avatar).")
    if kind(row) == "short" and words and not picture:
        raise Refused("Roblox Story Shorts build the main character from a picture of their avatar: give "
                      "character_picture (words alone go in extra).")
    if use.get("use") == "none" and (words or picture):
        raise Refused(use.get("say") or "This style can't use a character of your own.")
    if row.get("visual_policy") == "reference_only":
        raise Refused(f"{row.get('label') or row['name']} draws every scene with its learned animation kit, so its "
                      "character can't be changed for one video.")
    if len(words) > 4000:
        raise Refused("Keep the character's description under 4,000 characters.")
    if picture:
        if where == "studio_cloud":
            raise Refused("A character picture of your own renders on this computer for now: use where='this_computer'.")
        path = Path(picture).expanduser()
        if not path.is_file() or path.suffix.lower() not in CHARACTER_PICTURE:
            raise Refused("character_picture must be a PNG, JPG or WebP picture on this computer.")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise Refused("The character picture is over 20 MB. Export a smaller one.")
    return words, picture


def character_note(row: dict, options: dict) -> str:
    """What a new character adds to a quote: one drawn picture when the style carries a character picture."""
    words, picture = str(options.get("character") or "").strip(), str(options.get("character_picture") or "").strip()
    use = row.get("character_picture") or {}
    if (words or picture) and use.get("use") in ("avatar", "partial"):
        return str(use.get("say") or "")                       # a 3D cast: what the picture will and won't do
    if picture:
        return "Your picture replaces the style's character in every picture of these videos."
    if words and row.get("character_image"):
        return ("Studio draws your character once in this style's look (one picture, about $0.02, reused for the same "
                "words) and keeps them in every picture of these videos.")
    return "Your words describe the character in every picture." if words else ""


def apply_character(payload: dict, words: str, picture: str) -> dict:
    """The job spec's character and character_image: a picture is saved in Studio first (assets/characters)."""
    if picture:
        import base64
        path = Path(picture).expanduser()
        saved = call("POST", "/cr-workspace/character", {"image_base64": base64.b64encode(path.read_bytes()).decode(),
                                                         "filename": path.name, "description": words}, timeout=60)
        row = saved.get("character") or {}
        payload.update(character=row.get("text") or words, character_image=row.get("rel") or "")
    elif words:
        payload["character"] = words
    return payload


def tool_make_videos(args):
    same_studio()
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
    words, picture = character_choice(row, options, where)
    language = language_of(cat, row, options)
    if where == "studio_cloud" and str(options.get("voice") or "").strip():
        raise Refused("A video's own Fish Audio voice (options.voice) works on this computer for now: use "
                      "where='this_computer', or choose the voice for the style with choose_voice.")
    if where == "studio_cloud" and language != (row.get("language") or "en"):
        raise Refused(f"A video in {language_name(language)} renders on this computer for now: use "
                      "where='this_computer'.")
    if where == "this_computer" and args.get("new_attempt") is not True:
        early = video_payload(row, titles, minutes, features, options)
        try:
            found = call("POST", "/cr-workspace/resume-first", {"titles": titles, "style": row["name"],
                                                                 "minutes": early["minutes"], "extra": early["extra"]},
                         timeout=30)
        except Refused:
            found = {}                                   # /api/run asks the same again before anything starts
        if found.get("matches"):
            return resume_first_answer(found["matches"], found.get("others") or [])
    issued = _read(STATE / "quotes.json").get(str(args.get("quote_id") or ""))
    q = quote_for(cat, row, titles=len(titles), minutes=minutes, where=where, features=features,
                  thumbnails=thumbnail_count(row, options))
    if character_note(row, options):
        q["character"] = character_note(row, options)
    if args.get("quote_id") != q["quote_id"] or not issued or time.time() - issued > QUOTE_SECONDS:
        return {"started": False, "quote": q,
                "next": "Nothing started. Tell the creator this cost, and when they agree call make_videos again with "
                        f"the same choices and quote_id='{q['quote_id']}'."}
    if q.get("short_by_usd"):
        raise Refused(f"Your Studio Cloud balance is ${q['cloud']['balance_usd']:.2f} and these videos reserve up to "
                      f"${q['most_reserved_usd']:.2f}. Top up at {q['cloud']['topup_url']}, or make them on this "
                      "computer.")
    payload = apply_character(video_payload(row, titles, minutes, features, options, language), words, picture)
    if args.get("new_attempt") is True:
        payload["new_attempt"] = True                    # the creator insisted on a brand-new video
    if options.get("carry_on_after_usage_reset") is True:
        payload["carry_on_after_reset"] = True           # their up-front choice: on by itself after Claude's reset
    record = _read(STATE / "jobs.json")
    if where == "studio_cloud":
        call("POST", "/cr-cloud/start", payload, timeout=60)
        record.setdefault("cloud", []).append({"titles": titles, "style": row["name"], "at": time.time()})
        _write(STATE / "jobs.json", record)
        return {"started": True, "where": "studio_cloud", "titles": titles, "style": row["name"], "quote": q["summary"],
                "progress_id": "cloud",
                "next": "Studio writes the script and makes the voice and pictures here with your Claude Code, then a "
                        + ("machine on their own Modal account" if q["cloud"].get("own_account") else
                           "ChannelRecipe machine") + " renders. Keep Studio open. Check with progress(id='cloud') every few "
                        "minutes (don't wait in a loop); Studio shows a notification when it's done."}
    try:
        result = call("POST", "/api/run", payload, timeout=120)
    except Refused as error:
        blockers = (error.data or {}).get("blockers") or []
        if (error.data or {}).get("resume_first"):
            return resume_first_answer(error.data["resume_first"], error.data.get("others") or [])
        if blockers and only_models_check(error.data):
            try:
                # two tiny checks, up to 2 min each; a check that stopped before runs again only on the creator's
                # press of Verify my Claude models (auto)
                call("POST", "/cr-claude/setup", {"action": "verify", "auto": True}, timeout=300)
            except Refused as stopped:
                raise Refused(f"{stopped} Setup: {link('/cr-setup')}", stopped.status, stopped.data) from None
            result = call("POST", "/api/run", payload, timeout=120)
        elif blockers:
            raise Refused(" ".join(blockers) + f" Setup: {link('/cr-setup')}", error.status, error.data) from None
        else:
            raise
    queued = [queued_video(q_) for q_ in result.get("queued") or []]
    record.setdefault("local", []).extend({"slug": v["id"][6:], "title": v["title"], "at": time.time()} for v in queued)
    _write(STATE / "jobs.json", record)
    held = [v for v in queued if v.get("on_hold_because")]
    before = (queued[0].get("videos_before_it") or 0) if queued else 0
    said = (HOLD_NOTE if held else
            f"They're in Studio's queue behind {before} video{'s' if before != 1 else ''} and start by themselves, one "
            "at a time: the creator doesn't need to wait. " if before else "Studio makes them one at a time. ")
    return {"started": True, "where": "this_computer", "videos": queued, "quote": q["summary"],
            "language": language_name(language),
            # what Studio said with the start (a disk with little to spare; switched to Claude Code): tell them
            **({"tell_them": result["warnings"]} if result.get("warnings") else {}),
            "next": said + ("" if held else "Check with progress(id) every few minutes (don't wait in a loop); Studio "
                            "shows a notification when each one is done. open_in_studio(page='projects') shows them "
                            "on the Studio page.")}


def queued_video(q_) -> dict:
    """One video make_videos or resume_video queued: its id, and its place in line or why it's on hold."""
    if not isinstance(q_, dict):
        return {"id": "video:" + str(q_), "title": str(q_)}
    out = {"id": "video:" + str(q_.get("slug")), "title": q_.get("title")}
    if q_.get("held"):
        out["on_hold_because"] = q_["held"]
    elif q_.get("position"):
        out.update(position=q_["position"], videos_before_it=q_.get("ahead", q_["position"] - 1))
    return out


def resume_first_answer(matches: list, others: list) -> dict:
    """make_videos met titles that already stopped with their work saved: resuming reuses it, starting over pays again."""
    return {"started": False,
            "resume_first": [{k: m.get(k) for k in ("id", "title", "stopped")} for m in matches],
            **({"other_titles": others} if others else {}),
            "next": "Nothing started. These titles already have a stopped video with its work saved (script, voice, "
                    "pictures). Tell the creator and offer to carry each on instead: check progress(id) (a stopped "
                    "Claude step or uncertain paid task needs recover_video), then resume_video(id), which reuses "
                    "everything already made. Only if they insist on starting over, call make_videos again with "
                    "new_attempt=true: that is a brand-new video and pays again for every step."
                    + (" The other titles have no stopped video: make them with make_videos as usual." if others
                       else "")}


def _job_status(slug: str) -> dict:
    try:
        status = call("GET", "/api/status/" + _quote_path(slug), timeout=20)
    except Refused as error:
        if error.status != 404:
            raise
        # Studio restarted since this video stopped: its live job is gone, its saved folder says what happened
        status = {"state": "stopped", "done": True, "error": "", "lines": []}
    out = {"id": "video:" + slug, "state": status.get("state"), "done": bool(status.get("done")),
           "percent": round(100 * float(status.get("progress") or 0)), "error": status.get("error") or "",
           "last_lines": [line for line in (status.get("lines") or [])[-6:] if line.strip()]}
    # Research footage: how far its YouTube watching is (cr_watch says "watched N of M videos" as it goes; once the
    # video has moved on, newer lines have pushed it out of the last ten)
    watched = next((m for m in (re.search(r"YouTube: watched (\d+) of (\d+) videos", line or "")
                                for line in reversed((status.get("lines") or [])[-10:])) if m), None)
    if watched and not out["done"]:
        out["watching_clips"] = {"watched": int(watched.group(1)), "of": int(watched.group(2)),
                                 "note": "Still watching YouTube videos for its footage; it stops by itself at its "
                                         "budget and carries on with the clips it found."}
    if out["state"] in ("queued", "held"):
        try:
            row = next((j for j in call("GET", "/api/queue", timeout=20).get("jobs") or [] if j.get("slug") == slug), None)
        except Refused:
            row = None
        if row:
            out.update({k: v for k, v in queue_row(row).items() if k in ("position", "videos_before_it",
                                                                          "on_hold_because")})
        out["note"] = HOLD_NOTE if out["state"] == "held" else ("Waiting in Studio's queue: it starts by itself when "
                                                                "the videos before it finish.")
    # captions the video should carry but doesn't, and why (cr_captions logs one "Captions: ..." line at the final mux)
    said = next((line for line in reversed(status.get("lines") or []) if str(line).strip().startswith("Captions: none")),
                None)
    if said:
        out["captions_note"] = str(said).strip()
    # footage watching that moved to ChannelRecipe's watcher, or stopped, because Kie's watcher wasn't answering (cr_watch)
    moved = [str(line).strip() for line in status.get("lines") or []
             if re.search(r"Kie's video watcher isn't answering|footage watching stopped for this video|"
                          r"ChannelRecipe's watcher watched", str(line))]
    if moved:
        out["footage_note"] = " ".join(dict.fromkeys(moved))[:600]
    if status.get("video"):
        out.update(watch_url=link("/f/video/" + _quote_path(slug)), project_url=link("/editor/" + _quote_path(slug)))
    elif out["done"] or out["state"] in ("error", "stopped"):
        try:
            voice = call("GET", "/cr-workspace/voice/" + _quote_path(slug), timeout=20)
        except Refused:
            voice = {}
        if voice.get("stopped_on_voice") and not voice.get("has_voice"):
            out["stopped_on"] = "voice"
            out["voice_failed"] = voice.get("failed")
            out["voice_choices"] = [{k: c.get(k) for k in ("id", "label", "ready", "note")} for c in voice.get("choices") or []]
            out["voice_note"] = ("The voice service couldn't record this video's voice. Ask the creator which way to carry "
                                 "on (only the ready choices work now), then resume_video(id, voice=<choice id>). For "
                                 "their own recording, show them the script (script_file) to read, then pass the "
                                 "recording's path as recording. An ElevenLabs key goes in Setup & accounts, never in chat.")
            if voice.get("script_file"):
                out["script_file"] = voice["script_file"]
        try:
            presenter = call("GET", "/cr-workspace/presenter-choice/" + _quote_path(slug), timeout=20)
        except Refused:
            presenter = {}
        if presenter.get("stopped_on_presenter"):
            out["stopped_on"] = "presenter"
            out["presenter_failed"] = presenter.get("failed")
            out["presenter_choices"] = [{k: c.get(k) for k in ("id", "label", "ready", "note")}
                                        for c in presenter.get("choices") or []]
            out["presenter_note"] = ("The presenter service couldn't make this video's presenter; the rest of the video "
                                     "is finished and saved. Ask the creator: finish it without the presenter, or try "
                                     "the presenter again in a few minutes (read them the choice's note, which says "
                                     "whether that could pay twice). Then resume_video(id, presenter=<choice id>).")
        if not _recovery(slug, out) and status.get("state") == "stopped" and not out.get("stopped_on"):
            raise Refused("There's no video with that id. Check progress() for the ids.", 404)
    return out


RECOVERY_NOTE = ("Tell the creator in plain words what stopped it: what_to_tell_them, each stuck item's reason, and "
                 "whether their Claude usage or money may already have gone on it. Never approve for them. When they "
                 "say yes, recover_video(id, key=recovery_key) allows exactly these attempts and carries the video on, "
                 "reusing everything else (Claude Code asks them to approve too). When usage_resets is still ahead: "
                 "recover_video(id, key=recovery_key, when='after_reset') has Studio carry it on by itself after the "
                 "reset; when='now' with added_usage=true only if they added Claude usage. Do the fix_first items "
                 "first. When "
                 "free_to_resume is true, resume_video(id) carries it on and nothing is paid twice.")


def _recovery(slug: str, out: dict) -> bool:
    """A stopped video's Claude steps and paid media tasks that need the creator's yes (cr_recover.diagnose)."""
    try:
        r = call("GET", "/cr-workspace/recovery/" + _quote_path(slug), timeout=60)
    except Refused:
        return False
    if r.get("finished"):
        # Studio restarted since it finished: done, with its links
        out.update(state="done", done=True, watch_url=link("/f/video/" + _quote_path(slug)),
                   project_url=link("/editor/" + _quote_path(slug)))
        return True
    steps = [{"step": s.get("step"), "error_code": s.get("error_code") or s.get("status"), "reason": s.get("reason"),
              "claude_usage_already_used": s.get("spent"), "note": s.get("spent_note"),
              "needs_approval": bool(s.get("needs_approval")),
              **({"usage_resets": s["reset_label"]} if s.get("reset_label") else {}),
              **({"fix_first": s["fix_first"]} if s.get("fix_first") else {})}
             for s in r.get("claude_stages") or []]
    media = [{"task": m.get("task"), "provider": m.get("provider"), "state": m.get("state"), "reason": m.get("reason"),
              "trying_again_can_pay_twice": bool(m.get("pays_twice")), "free": bool(m.get("free")),
              "needs_approval": bool(m.get("needs_approval")), **({"do_first": m["do_first"]} if m.get("do_first")
                                                                  else {})}
             for m in r.get("media") or []]
    if r.get("stopped_on") in ("claude_stage", "media") and not out.get("stopped_on"):
        out["stopped_on"] = r["stopped_on"]
    if steps:
        out["stuck_claude_steps"] = steps
        # one line per kind of stopped step with its count ("3D picture review, round 1: 3 calls stopped at ...")
        out["stuck_claude_summary"] = [g.get("line") for g in r.get("groups") or [] if g.get("line")]
    if (r.get("claude_use") or {}).get("line"):
        out["claude_so_far"] = r["claude_use"]["line"]      # Studio's own count, never the plan's quota
    if media:
        out["stuck_media"] = media
    out.update(needs_approval=bool(r.get("needs_approval")), free_to_resume=bool(r.get("free_to_resume")),
               what_to_tell_them=r.get("what_to_tell_them") or "",
               carry_on_after_usage_reset=bool(r.get("carry_on_after_reset")))
    if r.get("key"):
        out["recovery_key"] = r["key"]
    if r.get("fix_first"):
        out["fix_first"] = list(dict.fromkeys(r["fix_first"]))       # nothing carries it on until these are done
    if r.get("do_first"):
        out["do_first"] = list(r["do_first"])                         # then resume_video carries it on, free
    if r.get("reset_label"):
        out.update(usage_resets=r["reset_label"], usage_reset_passed=bool(r.get("reset_passed")))
    if r.get("stopped_at") and not out.get("error"):
        out["stopped"] = r["stopped_at"]
    auto = r.get("auto") or {}
    out["carries_on_by_itself"] = {"would": bool(auto.get("safe")), "why": auto.get("why") or "",
                                   "times_so_far": auto.get("count", 0), "at_most": auto.get("limit", 2)}
    if out.get("stopped_on") in ("claude_stage", "media") or steps or media:
        out["recovery_note"] = RECOVERY_NOTE
    return True


def tool_progress(args):
    ident = str(args.get("id") or "").strip()
    if ident.startswith("video:") or (ident and _SLUG.fullmatch(ident) and not
                                       (_LOOK.fullmatch(ident) or _OPERATION.fullmatch(ident) or _SAMPLE.fullmatch(ident)
                                        or ident == "cloud")):
        return _job_status(ident.removeprefix("video:"))
    if ident == "cloud":
        return cloud_progress()
    if _LOOK.fullmatch(ident):
        return look_progress(ident)
    if _OPERATION.fullmatch(ident):
        return clone_progress(ident)
    if _SAMPLE.fullmatch(ident):
        return sample_progress(ident)
    if ident:
        raise Refused("Use an id from make_videos (video:…, cloud), a style clone (lk_…, op_…) or a style sample (sm_…).")
    queue = call("GET", "/api/queue", timeout=20)
    jobs = [queue_row(job) for job in queue.get("jobs") or []]
    out = {"this_computer": jobs, "busy": bool(queue.get("busy"))}
    if any(j["state"] == "held" for j in jobs):
        out["hold_note"] = HOLD_NOTE
    try:
        live = {j["id"] for j in jobs}
        stopped = [{"id": "video:" + r["slug"], "title": r.get("title"), "style": r.get("style"), "state": "stopped",
                    "stopped": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("mtime") or 0))}
                   for r in call("GET", "/cr-workspace/stopped", timeout=30).get("projects") or []]
        stopped = [r for r in stopped if r["id"] not in live]
        if stopped:
            out["stopped"] = stopped
            out["stopped_note"] = "Videos that stopped before finishing. resume_video(id) carries one on, reusing its saved work."
    except Refused:
        pass
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


def tool_resume_video(args):
    same_studio()
    slug = str(args.get("id") or "").strip().removeprefix("video:")
    if not _SLUG.fullmatch(slug):
        raise Refused("Give the stopped video's id from progress().")
    saved = call("GET", "/cr-workspace/project/" + _quote_path(slug), timeout=30)
    if not saved.get("available_for_resume") or not saved.get("project_id"):
        raise Refused(f"\"{saved.get('title') or slug}\" can't be resumed: it's {saved.get('status') or 'not a stopped video'}. "
                      "Check progress().")
    voice = str(args.get("voice") or "").strip()
    if voice:
        call("POST", "/cr-workspace/voice/" + _quote_path(slug),
             {"choice": voice, "recording": str(args.get("recording") or "").strip()}, timeout=180)
    presenter = str(args.get("presenter") or "").strip()
    if presenter:
        call("POST", "/cr-workspace/presenter-choice/" + _quote_path(slug), {"choice": presenter}, timeout=60)
    try:
        # a free resume only (cr_recover): one that would stop again at a step needing the creator's yes is refused;
        # while another video is made it joins Studio's queue like any Resume (cr_queue) and starts in its turn
        result = call("POST", "/cr-workspace/recovery/" + _quote_path(slug), {"only_if_free": True}, timeout=180)
    except Refused as error:
        if error.status == 404:                                    # an older Studio: the plain resume
            result = call("POST", "/cr-submit-project", {"project_id": saved["project_id"]}, timeout=120)
        elif (error.data or {}).get("recovery"):
            return {"resumed": False, "id": "video:" + slug, "title": saved.get("title"), "why": str(error),
                    "next": "Check progress(id): it lists the stopped steps and what trying them again involves. Tell "
                            "the creator, and only after they say yes call recover_video(id, key=recovery_key)."}
        else:
            raise
    where = queue_place(result)
    return {"resumed": True, "id": "video:" + slug, "title": saved.get("title"), **({"voice": voice} if voice else {}),
            **({"presenter": presenter} if presenter else {}), **where,
            "queued": [q.get("slug") if isinstance(q, dict) else q for q in result.get("queued") or []],
            "next": queue_next(where) +
                    "It carries on from where it stopped and reuses everything already made (script, pictures, "
                    "voice parts). Check progress(id) now and then; Studio shows a notification when it's done."}


def queue_place(result: dict) -> dict:
    """A resumed video's place in Studio's queue (cr_queue): position and videos before it, or why it's on hold."""
    placed = [queued_video(q) for q in (result or {}).get("queued") or []]
    first = placed[0] if placed else {}
    return {k: first[k] for k in ("position", "videos_before_it", "on_hold_because") if k in first}


def queue_next(where: dict) -> str:
    return (HOLD_NOTE + " " if where.get("on_hold_because") else
            "It waits in Studio's queue and starts by itself when the videos before it finish. "
            if where.get("videos_before_it") else "")


def tool_recover_video(args):
    """The creator's one yes for a stopped video's stuck Claude steps and uncertain paid media tasks."""
    same_studio()
    slug = str(args.get("id") or "").strip().removeprefix("video:")
    if not _SLUG.fullmatch(slug):
        raise Refused("Give the stopped video's id from progress().")
    when = str(args.get("when") or "now")
    if when not in ("now", "after_reset"):
        raise Refused("Choose when='now' or when='after_reset'.")
    # the recovery key progress(id) showed: only what the creator was told is approved (cr_recover refuses without it)
    body = {"acknowledge_costs": True, "when": when, "added_usage": args.get("added_usage") is True,
            "key": str(args.get("key") or "")}
    try:
        result = call("POST", "/cr-workspace/recovery/" + _quote_path(slug), body, timeout=240)
    except Refused as error:
        data = error.data or {}
        recovery = data.get("recovery") or {}
        out = {"recovered": False, "id": "video:" + slug, "why": str(error)}
        if recovery:
            out["what_to_tell_them"] = recovery.get("what_to_tell_them") or ""
            if recovery.get("key"):
                out["recovery_key"] = recovery["key"]
        if data.get("approved"):
            out["approved_before_it_stopped"] = data["approved"]
        out["next"] = ("Nothing more was approved. Tell the creator why, check progress(id) again, and ask before "
                       "trying again.")
        return out
    approved = result.get("approved") or {}
    where = queue_place(result)
    out = {"recovered": True, "id": "video:" + slug, "message": result.get("message") or "",
           "approved_claude_steps": len(approved.get("approved_stages") or []),
           "media_tasks_sent_again": len(approved.get("set_aside") or []), **where}
    if result.get("scheduled"):
        out.update(recovered=False, scheduled=True,
                   next="Studio carries it on by itself after Claude's usage resets. Keep Studio open; check "
                        "progress(id) after the reset.")
    else:
        out["next"] = (queue_next(where) + "It carries on from where it stopped and reuses everything already made. "
                       "Check progress(id) now and then; Studio shows a notification when it's done.")
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
    if d.get("state") == "running" and d.get("left_s"):
        out["minutes_left"] = max(1, round(float(d["left_s"]) / 60))       # measured from the look's steps so far
    if d.get("state") == "done":
        m = d.get("mapping") or {}
        route = m.get("route") or {}
        out.update(questions=[{"id": q.get("id"), "ask": q.get("ask"), "options": q.get("options")}
                              for q in m.get("questions") or []],
                   suggested_base=m.get("base"), because=m.get("base_why"), made_with=route.get("engine"),
                   how_made=route.get("why") or "",
                   medium_gap=route.get("medium_gap") or "", cost_note=m.get("cost") if isinstance(m.get("cost"), str)
                   else "", not_watched=d.get("not_watched") or [], shorts=bool(d.get("shorts")),
                   similar_styles=similar_styles(d.get("similar")),
                   # example titles built from the channel's own titles (no model call): offer them for example_title
                   title_ideas=[str(t) for t in d.get("title_ideas") or []][:5],
                   **({"shorts_note": SHORTS_NOTE} if d.get("shorts") else {}),
                   next="Ask the creator these questions (their first option is Studio's guess), then call clone_style "
                        "with look_id, a style name, an example title (title_ideas has a few from the channel's own "
                        "titles to offer; they can change any) and their answers. The 'made' question decides "
                        "how the videos are made, so ask it first, in plain words; a 'kit' question (asked when the "
                        "look is partly 3D) decides whether the code draws in 2D or builds real 3D: ask it next and never "
                        "answer it for them. Written in code (recommended) has "
                        "their Claude Code write the channel's own renderer during the study (usually 10 to 25 "
                        "minutes of their Claude usage for the whole study, at most 46). Its videos then cost their "
                        "voice, plus at most a "
                        "few background pictures, and the renderer can be improved later with style_lab. Instead of "
                        "code gives Studio's ready-made pictures or 3D engine, which won't look or move exactly like the "
                        "channel. Say both before they choose. A medium_gap is something Studio can't make yet: read it "
                        "to the creator before anything is paid. " + SIMILAR_NEXT)

    return out


# a video drawn by its style's own code has no timeline (cr_animated2d.NO_TIMELINE): never offer it one
NO_TIMELINE = ("Drawn by its style's own animation code, so the timeline can't edit it (each minute is one whole "
               "animation). To change how the style's videos look, use Improve this style on its study page or "
               "style_lab; to change this video, make it again with the changes in its brief.")
SHORTS_NOTE = ("These references are Shorts. Either way the style makes vertical Shorts: written in code (the default "
               "answer to 'made') with the channel's own captions; instead of code, with Studio's pictures drawn tall, "
               "captions above the Shorts buttons, and any graphics made for wide screens (maps, headlines, 3D "
               "scenes) whole across the middle of the frame: tell the creator before the paid study (their Shorts "
               "recipes, similar_styles kind='short', make vertical Shorts too).")
SIMILAR_NEXT = ("similar_styles are the styles they already have that come closest (suggestions only, never a reason not "
                "to study): before the paid study, mention them in a line each. They can use one now (make_videos with its "
                "id), try a few seconds of one on their topic first (style_sample: try_first.cost_usd on their own "
                "accounts, ask before), or go on with the study.")


def similar_styles(rows) -> list:
    """The installed styles closest to the looked-at channel (cr_style_look.similar), as the tools say them."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        sample = r.get("sample") or {}
        out.append({"id": r["id"], "name": r.get("name"), "closeness_percent": r.get("closeness"), "why": r.get("why"),
                    "kind": r.get("kind"), "yours": bool(r.get("mine")),
                    "the_study_builds_on_it": bool(r.get("recommended")),
                    "learned_from_these_videos": bool(r.get("same_references")),
                    "picture_url": link(r["preview_url"]) if r.get("preview_url") else "",
                    "try_first": ({"tool": "style_sample", "cost_usd": sample.get("cost_usd"),
                                   "seconds": sample.get("seconds")} if sample.get("can")
                                  else {"not_offered": sample.get("why_not") or ""})})
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
    elif d.get("metadata_recovery_available"):
        # its Claude work finished before it stopped: saved without repeating anything, never studied again
        out["can_save_completed"] = True
        out["next"] = ("This study's Claude work finished before it stopped" + (" (Studio was closed)" if
                       d.get("studio_closed") else "") + ". Open its page (open_in_studio page='style_clone' id=<this "
                       "op id>) and press Save completed style: it saves the style without repeating Claude, downloads "
                       "or rendering. Don't start a new study for it.")
    elif d.get("studio_closed"):
        # its worker died with a closed Studio (Studio 112 settles it): the stopped study no longer uses a clone
        out["studio_closed"] = True
        out["next"] = ("Studio was closed during this study, so it stopped; it no longer uses one of their style "
                       "clones. To study the channel again with the same links and choices, open its page "
                       "(open_in_studio page='style_clone' id=<this op id>) and press Study again: a new study, which "
                       "uses their Claude usage and a clone. Tell them to keep the ChannelRecipe launcher window open until it "
                       "finishes (closing the browser tab is fine).")
    renderer = d.get("renderer") or {}
    if renderer:
        # an animated style: Claude's renderer for the channel, scored against the channel's own frames
        out["animation"] = {k: renderer.get(k) for k in ("state", "overall", "bar", "version") if k in renderer}
        out["animation"]["what_to_tell_them"] = renderer.get("say") or ""
        if renderer.get("offer_pictures"):
            out["next"] = ("Read what_to_tell_them to the creator. If they would rather have pictures in the channel's "
                           "look, open the page (open_in_studio page='style_clone' id=<this op id>) and press Use "
                           "pictures instead; never choose for them.")
        if renderer.get("can_use_animation"):
            out["animation"]["can_use_animation"] = True
            out["next"] = ("This style draws pictures because Use pictures instead was chosen; its animation is kept. If "
                           "they want it back, open the page (open_in_studio page='style_clone' id=<this op id>) and "
                           "press Use Claude's animation again; never choose for them.")
    return out


def tool_claude_usage(args):
    """Studio's Claude use across every video (cr_claude_use.overall), or one video's with id."""
    vid = str(args.get("id") or "").strip()
    if vid:
        slug = vid.split(":", 1)[1] if vid.startswith("video:") else vid
        out = call("GET", "/cr-workspace/claude-use/" + _quote_path(slug), timeout=30)
        return {**out, "note": "What Studio counted for this video. Claude Code doesn't say how much of the plan's "
                               "allowance is left, so never give a percentage."}
    out = call("GET", "/cr-workspace/claude-use", timeout=60)
    limit = max(1, min(int(args.get("videos") or 10), 50))
    return {**out, "videos": (out.get("videos") or [])[:limit],
            "what_to_tell_them": (out.get("line") or "") + " " + (out.get("note") or "")}


def tool_list_videos(args):
    limit = max(1, min(int(args.get("limit") or 20), 60))
    rows = call("GET", "/cr-claude-code/videos", timeout=30).get("videos") or []
    out = {"this_computer": [{
        "title": r.get("title"), "id": "video:" + r["slug"], "style": r.get("style"),
        "made": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("mtime") or 0)),
        "megabytes": round((r.get("size") or 0) / 1e6, 1), "file": r.get("path") or "",
        "watch_url": link("/f/video/" + _quote_path(r["slug"])),
        **({"no_timeline": NO_TIMELINE} if r.get("coded") else
           {"project_url": link("/editor/" + _quote_path(r["slug"]))})} for r in rows[:limit]]}
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
    if d.get("byo"):
        return {"available": bool(d.get("available")), "own_account": "Modal", "why": d.get("why") or "",
                "machines": d.get("machines"), "setup_page": link("/cr-setup#cloud-render"),
                "note": "Studio Cloud renders on the creator's own Modal account and Modal bills them for the machine "
                        "time; there is no ChannelRecipe balance. Without a Modal account, setup_page's Render in the "
                        "cloud card shows them how to add one (never ask for the token in chat)."}
    return {"available": bool(d.get("available")), "balance_usd": d.get("balance_usd"), "why": d.get("why") or "",
            "topup_url": d.get("topup_url"), "top_up_amounts_usd": d.get("chunks_usd"),
            "machines": d.get("machines"),
            "note": "Each cloud video reserves its most before it starts and pays only for what it used."}


def pick_references(eligible: list, count: int = 3) -> list:
    """The most-viewed eligible videos; from a combined preview, taken in turn from each channel."""
    ranked = sorted(eligible, key=lambda c: -int(c.get("views") or 0))
    sources = sorted({c.get("source", 0) for c in ranked})
    queues = [[c for c in ranked if c.get("source", 0) == s] for s in sources]
    chosen = []
    while len(chosen) < count and any(queues):
        for queue in queues:
            if queue and len(chosen) < count:
                chosen.append(queue.pop(0)["id"])
    return chosen


def tool_clone_style_look(args):
    same_studio()
    urls = [str(u).strip() for u in (args.get("youtube_urls") or []) if str(u).strip()]
    url = str(args.get("youtube_url") or "").strip()
    if url and url not in urls:
        urls.insert(0, url)
    if not urls:
        raise Refused("Give a YouTube channel or video link (or two or three to combine).")
    if len(urls) > 3:
        raise Refused("Combine at most three channels or videos.")
    if len(urls) > 1:
        preview = call("POST", "/cr-claude-code/combine-preview", {"urls": urls}, timeout=400)
    else:
        preview = call("POST", "/cr-style-clone/preview", {"url": urls[0]}, timeout=150)
    url = urls[0]
    eligible = [c for c in preview.get("candidates") or [] if c.get("eligible")]
    wanted = [str(x) for x in args.get("reference_ids") or []]
    if wanted:
        chosen = [c["id"] for c in eligible if c["id"] in wanted]
        if len(chosen) != len(set(wanted)):
            raise Refused("Choose reference_ids from the eligible videos: " +
                          ", ".join(f"{c['id']} ({c['title'][:60]})" for c in eligible[:12]))
    else:
        chosen = pick_references(eligible)
    if not chosen:
        raise Refused("None of these videos can be studied (they must be between one minute and 24 hours long).")
    look = call("POST", "/cr-style-clone/look", {"preview_id": preview["preview_id"], "reference_ids": chosen},
                timeout=60)
    looks = _read(STATE / "looks.json")
    looks[look["look_id"]] = {"preview_id": preview["preview_id"], "reference_ids": chosen, "url": url, "at": time.time()}
    _write(STATE / "looks.json", dict(sorted(looks.items(), key=lambda kv: -kv[1]["at"])[:40]))
    similar = similar_styles(look.get("similar")) if look.get("state") == "done" else []
    return {"look_id": look["look_id"], "state": look.get("state"), "message": look.get("message"),
            "combining": preview.get("reference_urls") or [],
            **({"similar_styles": similar, "about_similar": SIMILAR_NEXT} if similar else {}),
            "studying": [{"id": c["id"], "title": c["title"], "channel": c.get("channel"), "views": c.get("views"),
                          "minutes": round((c.get("duration_seconds") or 0) / 60)} for c in eligible if c["id"] in chosen],
            "other_videos": [{"id": c["id"], "title": c["title"]} for c in eligible if c["id"] not in chosen][:9],
            "next": ("Studio already looked at these: progress(id=look_id) has its questions for the creator."
                     if look.get("state") == "done" else
                     "Studio is looking at them (free, a few minutes). Check with progress(id=look_id); when it's "
                     "done it has questions for the creator.")}


def tool_clone_style(args):
    same_studio()
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
    budget = args.get("budget_minutes", 22)
    if not isinstance(budget, int) or isinstance(budget, bool) or not 15 <= budget <= 30:
        raise Refused("budget_minutes is the Claude study budget: 15 to 30 minutes (22 unless a study ran out of time).")
    payload = {"preview_id": saved["preview_id"], "reference_ids": saved["reference_ids"], "look_id": look_id,
               "answers": answers, "label": label[:80], "title": title[:280],
               "base_style": str(args.get("base_style") or mapping.get("base") or "documentary"),
               "brief": str(args.get("brief") or "")[:4000], "budget_minutes": budget, "max_calls": 40,
               "minutes": max(1, min(int(args.get("example_minutes") or 10), 60)),
               "visual_mode": visual if visual in ("mixed", "reference_only") else "mixed",
               "acknowledge_costs": True, "accept_medium": bool(args.get("accept_medium")),
               "attempt_id": secrets.token_hex(16)}
    gap = (mapping.get("route") or {}).get("medium_gap") or ""
    if gap and payload["visual_mode"] != "reference_only" and not payload["accept_medium"]:
        return {"started": False, "medium_gap": gap, "how_made": (mapping.get("route") or {}).get("why") or "",
                "next": "Read this to the creator in plain words: it is what their videos will be. If they accept it, "
                        "call clone_style again with accept_medium=true. Never accept it for them."}
    status = call("POST", "/cr-style-clone/start", payload, timeout=120)
    return {"started": True, "id": status.get("operation_id"), "state": status.get("state"),
            "message": status.get("message"), "page": link("/cr-style-clone?operation=" + str(status.get("operation_id"))),
            **({"shorts_note": SHORTS_NOTE} if look.get("shorts") else {}),
            "next": "The study runs on your Claude Code for about 15 to 30 minutes. Check with progress(id) now and "
                    "then; Studio shows a notification when the style is ready."}


def _learned_style(wanted) -> str:
    wanted = str(wanted or "").strip()
    if _LEARNED.fullmatch(wanted):
        return wanted
    rows = [r for r in catalog().get("channels") or [] if r.get("custom")]
    if not rows:
        raise Refused("There are no learned styles yet: clone one with clone_style_look and clone_style.")
    name = find_style(rows, wanted)["name"]
    if not _LEARNED.fullmatch(name):
        raise Refused("Only styles learned from a channel can be edited here.")
    return name


def tool_edit_style(args):
    """A learned style's name, description, creative direction and Create's starting options: shown, or changed."""
    same_studio()
    style = _learned_style(args.get("style"))
    keys = ("name", "tagline", "description", "script_notes", "director_notes", "character", "forget_character_picture",
            "features_on", "pictures_per_min", "subtitles", "language")
    changes = {k: args[k] for k in keys if k in args}
    path = "/cr-style-clone/style/" + _quote_path(style)
    d = call("POST", path, {"changes": changes}, timeout=60) if changes else call("GET", path, timeout=30)
    features = d.get("features") or {}
    study = d.get("study") or {}
    out = {k: d.get(k) for k in ("style", "name", "tagline", "description", "script_notes", "director_notes", "character",
                                 "has_character_picture", "subtitles", "language", "versions_kept")}
    out.update(saved=bool(d.get("saved")), pictures_per_min=d.get("pictures"),
               options={"offered": [f.get("key") for f in features.get("offered") or []], "on": features.get("on") or []},
               study_again={"example_title": study.get("title") or "", "brief": study.get("brief") or "",
                            "base_style": study.get("base_style") or "", "studied": study.get("reference_urls") or []},
               page=link("/cr-style-clone?edit=" + _quote_path(style)),
               next=("Saved: their next videos in this style use it (the version before is kept on their computer). "
                     if d.get("saved") else "") +
                    "To change it, call edit_style again with only what changes (script_notes and director_notes are "
                    "one note per line, up to ten; features_on from options.offered; pictures_per_min within its "
                    "floor and ceiling). character changes the words for who appears in every picture: pass "
                    "forget_character_picture=true too when it's a different character, or the old picture keeps "
                    "coming back. To study it again with other videos, call clone_style_look with the new links, then "
                    "clone_style with a new style_name and study_again's example_title and brief: that makes a new "
                    "style and keeps this one.")
    return out


def sample_progress(ident: str) -> dict:
    d = call("GET", "/cr-style-clone/sample/" + ident + "/status", timeout=30)
    out = {k: d.get(k) for k in ("sample_id", "state", "style", "topic", "line", "message", "cost_usd", "credits",
                                 "voiced")}
    if d.get("state") == "done":
        out.update(file=d.get("video") or "", watch_url=link(d["video_url"]) if d.get("video_url") else "",
                   next="Show them the sample (the file, or watch_url in the Browser pane). If they like it, "
                        "make_videos with this style; if not, try another similar style or go on with the study.")
    return out


def tool_style_sample(args):
    """A few seconds of an installed style on the creator's topic, before a paid study (or a progress check)."""
    same_studio()
    ident = str(args.get("id") or "").strip()
    if ident:
        if not _SAMPLE.fullmatch(ident):
            raise Refused("Use the sample id style_sample returned (sm_…).")
        return sample_progress(ident)
    rows = catalog().get("channels") or []
    style = find_style(rows, args.get("style"))["name"]
    topic = str(args.get("topic") or "").strip()
    if not topic:
        raise Refused("Give the topic of a video they'd make, in a few words.")
    d = call("POST", "/cr-style-clone/sample", {"style": style, "topic": topic[:200],
                                                "line": str(args.get("line") or "")[:240], "acknowledge_costs": True},
             timeout=60)
    return {"id": d.get("sample_id"), "state": d.get("state"), "message": d.get("message"),
            "cost_usd": d.get("cost_usd"),
            "next": "Studio draws two pictures in the style, reads the line and moves over each picture (a minute or "
                    "two). Check progress(id) or style_sample(id=…); it gives the video's file when done."}


def tool_product_ideas(args):
    same_studio()
    about = str(args.get("about") or "").strip()
    channel = str(args.get("channel") or "").strip()
    if not about and not channel:
        raise Refused("Say what the channel or niche is about (a few lines), or give its YouTube link.")
    pack = call("POST", "/api/offers/pack", {"instructions": about[:3000], "channel": channel[:300],
                                              "language": str(args.get("language") or "English")[:40]},
                timeout=400).get("pack") or {}
    if not pack.get("ideas"):
        raise Refused("Studio couldn't come up with product ideas this time. Nothing was spent; try again.")
    _write(STATE / "last-pack.json", {"pack": pack, "about": about, "channel": channel, "at": time.time()})
    ideas = [{"number": n, **{k: i.get(k) for k in ("title", "promise", "kind", "format", "who", "size", "price_usd",
                                                     "why", "chapters", "risk") if i.get(k) is not None}}
             for n, i in enumerate(pack.get("ideas") or [], 1)]
    domains = [d for d in pack.get("domains") or [] if isinstance(d, (str, dict))]
    free = [d if isinstance(d, str) else d.get("domain") for d in domains if isinstance(d, str) or d.get("free")]
    taken = [d.get("domain") for d in domains if isinstance(d, dict) and not d.get("free")]
    return {"ideas": ideas, "channel_names": pack.get("names") or [], "free_domains": [d for d in free if d],
            "taken_domains": [d for d in taken if d],
            "pricing": pack.get("pricing") or {}, "funnel": pack.get("funnel") or {}, "launch": pack.get("launch") or [],
            "next": "Show the creator these. To build one: build_product(idea_number=…, domain=…, channel_name=…). "
                    "A domain is bought separately (about $11 a year on Vercel), never by Studio."}


def tool_build_product(args):
    same_studio()
    saved = _read(STATE / "last-pack.json")
    pack = saved.get("pack") or {}
    ideas = pack.get("ideas") or []
    if not ideas:
        raise Refused("Get product ideas first with product_ideas.")
    number = int(args.get("idea_number") or 0)
    if not 1 <= number <= len(ideas):
        raise Refused(f"Choose idea_number 1 to {len(ideas)} from the last product_ideas.")
    idea = ideas[number - 1]
    body = {"idea": idea, "niche": saved.get("about") or "", "channel": saved.get("channel") or "",
            "channel_name": str(args.get("channel_name") or "")[:80],
            "domain": str(args.get("domain") or "")[:60], "pack_file": str(pack.get("file") or "")}
    result = call("POST", "/api/products/build", body, timeout=120)
    return {"started": True, "id": result.get("slug"), "title": idea.get("title"),
            "cost": "About $0.30 of pictures on your image account (about 62 drawings) and about 20 Claude Code calls "
                    "on your plan.",
            "next": "It takes about an hour, one product at a time. Check with products(id) now and then: the book "
                    "PDF, landing page, Stripe checkout and launch guide land in output/offers/<id>/."}


def tool_products(args):
    slug = str(args.get("id") or "").strip()
    if slug:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", slug):
            raise Refused("Use a product id from products().")
        d = call("GET", "/api/products/build/" + slug, timeout=20)
        state = "building" if d.get("running") else "done" if d.get("code") == 0 else "stopped" if d.get("code") else "unknown"
        return {"id": slug, "state": state, "last_lines": d.get("lines") or []}
    rows = call("GET", "/api/products", timeout=30).get("products") or []
    keep = ("slug", "title", "kind", "status", "price", "domain", "folder", "lessons")
    return {"products": [{k: r.get(k) for k in keep if k in r} for r in rows[:20]],
            "next": "Your videos can sell a finished product: pick it under Your product on the Studio page."}


# ── the niche agent (/niche-agent): free YouTube reads, the niche database, the owner's teaching, saved ideas ──

def _niche():
    import cr_niche_agent
    return cr_niche_agent


def _youtube(fn):
    """A niche-agent YouTube read; its plain-words error for the creator."""
    agent = _niche()
    try:
        return fn(agent)
    except agent.YouTubeError as error:
        raise Refused(str(error)) from None


def tool_niche_teaching(args):
    agent = _niche()
    try:
        data = call("GET", "/cr-niche-agent/teaching", timeout=30)
    except (Closed, Refused):
        data = agent.local_teaching(HOME)                   # Studio closed: the saved or shipped copy
    examples = [e for e in data.get("examples") or [] if isinstance(e, dict)]
    kind = str(args.get("kind") or "")
    if kind in ("niche", "idea", "channel"):
        examples = [e for e in examples if e.get("kind") == kind]
    return {"rules": data.get("rules") or "", "examples": examples, "source": data.get("source") or "",
            "updated": data.get("updated"), "thresholds": agent.thresholds(data.get("rules") or ""),
            "next": "Follow these rules over your own judgement. Use the good examples as references and search "
                    "seeds; never propose a 'bad' one again without saying what changed."}


def tool_niche_breakouts(args):
    query = {k: args.get(k) for k in ("q", "max_age_days", "max_subs", "min_recent_avg", "min_ratio", "limit", "sort")
             if args.get(k) not in (None, "")}
    try:
        data = call("GET", "/cr-niche-agent/breakouts?" + _niche().breakout_query(query), timeout=60)
    except Closed:
        raise Refused("The niche database is read through Studio: start Studio with its launcher and try again. "
                      "The live YouTube tools (yt_search, yt_channel_stats) work without it.") from None
    data.setdefault("next", "These are seeds from ChannelRecipe's database, not proof (its numbers can be weeks old "
                            "and it keeps 4 recent videos a channel). Check the promising ones live with "
                            "yt_channel_stats before you use them.")
    return data


def tool_yt_search(args):
    queries = args.get("queries") or ([args["query"]] if args.get("query") else [])
    if isinstance(queries, str):
        queries = [queries]
    return _youtube(lambda agent: agent.search(
        list(queries), window=str(args.get("window") or "month"), sort=str(args.get("sort") or "views"),
        fmt="shorts" if str(args.get("format") or "") == "shorts" else "long", min_views=args.get("min_views"),
        per_query=int(args.get("per_query") or 30), home=HOME))


def tool_yt_channel_stats(args):
    channels = args.get("channels") or ([args["channel"]] if args.get("channel") else [])
    if isinstance(channels, str):
        channels = [channels]
    return _youtube(lambda agent: agent.channel_stats(
        list(channels), tab="shorts" if str(args.get("tab") or "") == "shorts" else "videos", home=HOME))


def tool_yt_look(args):
    return _youtube(lambda agent: agent.look_sheet(
        str(args.get("channel") or ""), args.get("video_ids") or [],
        tab="shorts" if str(args.get("tab") or "") == "shorts" else "videos", home=HOME))


def tool_save_ideas(args):
    ideas = args.get("ideas")
    try:
        result = _niche().save_ideas(ideas if isinstance(ideas, list) else [], home=HOME)
    except RuntimeError as error:                                 # another window is saving, or the file is stuck
        raise Refused(str(error)) from None
    return {**result, "where": "Create → Saved ideas in Studio, and ideas/ideas.json in this folder",
            "next": "Offer use_idea(id) for the one they like best: it fills Create in. It never starts a video."}


# ── narration voices (Choose voice) ───────────────────────────────────────────────────────────────────────────

_FISH_ID = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])")


def tool_voices(args):
    """The narration voice in use and, for a voice account with a library (Fish Audio, ElevenLabs on the creator's own
    key), one page of its voices: the creator's own, or the provider's library (search; a pasted fish.audio voice link
    finds that voice). Free: listing never reads anything aloud."""
    style = str(args.get("style") or "").strip()
    if style and not _STYLE.fullmatch(style):
        raise Refused("Unknown style id. Use an id from list_styles.")
    now = call("GET", "/cr-voices?" + urllib.parse.urlencode({"style": style}), timeout=30)
    if not now.get("connected"):
        return {"connected": False, "next": "No narration account is connected. The creator connects one in Setup & "
                                            "accounts (open_in_studio(page='setup')); keys go there, never in chat."}
    out = {"connected": True, "provider": now.get("provider"), "provider_label": now.get("provider_label"),
           "model": now.get("model_label") or now.get("model"), "voice": now.get("voice"),
           "voice_id": now.get("voice_id") or "", "style": now.get("style") or style}
    if not now.get("library"):
        out["voices"] = now.get("voices") or []
        out["next"] = "Choose voice on the Studio page plays a free sample of each (open_in_studio(page='create'))."
        return out
    source = "shared" if args.get("source") == "library" else "mine"
    query = urllib.parse.urlencode({"source": source, "search": str(args.get("search") or "")[:200],
                                    "page": max(1, min(int(args.get("page") or 1), 200))})
    got = call("GET", "/cr-voices/library?" + query, timeout=60)
    out.update(source="library" if source == "shared" else "mine", more=bool(got.get("more")),
               voices=[{k: v.get(k) for k in ("id", "name", "description", "accent", "preview", "mine") if v.get(k)}
                       for v in got.get("voices") or []][:30])
    if got.get("note"):
        out["note"] = got["note"]
    out["next"] = ("Each voice's preview is its own free sample (an audio link). To use one, choose_voice(voice_id, "
                   "style); for these videos only, make_videos options.voice=<voice id>.")
    return out


def tool_choose_voice(args):
    """Use this voice for the creator's next videos (for a style when one is given): the same as Choose voice."""
    voice = str(args.get("voice_id") or "").strip()
    found = _FISH_ID.search(voice)
    voice = found.group(1).lower() if found else voice
    if not voice:
        raise Refused("Give a voice_id from voices().")
    body = {"voice_id": voice, "style": str(args.get("style") or "").strip(), "name": str(args.get("name") or "")[:90]}
    got = call("POST", "/cr-voices/select", body, timeout=60)
    return {"saved": bool(got.get("saved")), "voice": got.get("voice"), "message": got.get("message")}


def tool_list_ideas(args):
    rows = _niche().list_ideas(str(args.get("status") or ""), home=HOME)
    return {"ideas": rows[:60], "total": len(rows)}


def tool_use_idea(args):
    agent = _niche()
    ident = str(args.get("id") or "").strip()
    try:
        idea = agent.set_idea_status(ident, "used", home=HOME)
    except (LookupError, RuntimeError) as error:
        raise Refused(str(error)) from None
    url = link("/?idea=" + ident)
    try:
        same_studio()
        running = True
    except Closed:
        running = False
    opened = open_url(url) if running and args.get("open_browser") else False
    return {"idea": {k: idea.get(k) for k in ("id", "title", "style", "minutes", "format", "niche")},
            "create_url": url, "opened_in_browser": opened, "extra_instructions": agent.extra_instructions(idea),
            "next": ("Create opens with this title" + (", the style" if idea.get("style") else "")
                     + " and the idea's angle in Extra instructions. Nothing starts until the creator presses Create, "
                       "or until you quote it and they agree (make_videos)." if running else
                     "Studio isn't running: start it with its launcher, then open this link (Create → Saved ideas "
                     "has it too).")}


# ── deleting (into Studio's trash) ──────────────────────────────────────────────────────────────────────────────

def _same_name(given, actual) -> bool:
    """The name the creator approved is the item's own (spacing, case and curly quotes aside)."""
    def key(value):
        text = str(value or "")
        for curly, plain in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("—", "-"), ("–", "-")):
            text = text.replace(curly, plain)
        return " ".join(text.split()).casefold()
    return bool(key(given)) and key(given) == key(actual)


def _trashed(done: dict, page: str) -> dict:
    until = time.strftime("%d %b %Y", time.localtime(done.get("empties_at") or time.time() + 30 * 86400))
    return {"deleted": True, "title": done.get("title"), "moved_to_trash": done.get("goes") or [],
            "kept": done.get("kept") or [],
            "restore": f"It waits in Studio's trash until {until}, then Studio empties it for good. To bring it back "
                       f"before then: Recently deleted on the Studio page ({link(page)}), Restore.",
            "next": "Tell the creator in a line what went, what was kept and that it can be restored for 30 days."}


def tool_delete_video(args):
    same_studio()
    slug = str(args.get("id") or "").strip().removeprefix("video:")
    if not _SLUG.fullmatch(slug):
        raise Refused("Give the video's id from list_videos or progress() (video:…).")
    path = "/cr-workspace/delete/video/" + _quote_path(slug)
    plan = call("GET", path, timeout=60)
    if not _same_name(args.get("title"), plan.get("title")):
        raise Refused(f"video:{slug} is “{plan.get('title')}”, not “{args.get('title') or ''}”. Nothing was deleted: "
                      "check the video with list_videos and ask the creator again.")
    if plan.get("blocked"):
        raise Refused(f"“{plan.get('title')}” {plan['blocked']} Nothing was deleted.")
    return _trashed(call("POST", path, {}, timeout=120), "/#projects")


def tool_delete_style(args):
    same_studio()
    style = str(args.get("id") or "").strip()
    if not _STYLE.fullmatch(style) or not style.startswith("cr_clone_"):
        raise Refused("Only a style the creator cloned can be deleted (an id from list_styles(kind='mine'), cr_clone_…). "
                      "Studio's own recipes stay.")
    path = "/cr-workspace/delete/style/" + _quote_path(style)
    plan = call("GET", path, timeout=60)
    if not _same_name(args.get("name"), plan.get("title")):
        raise Refused(f"{style} is “{plan.get('title')}”, not “{args.get('name') or ''}”. Nothing was deleted: check "
                      "the style with list_styles and ask the creator again.")
    if plan.get("blocked"):
        raise Refused(f"{plan['blocked']} Nothing was deleted.")
    return _trashed(call("POST", path, {}, timeout=60), "/#styles")


# ── MCP plumbing ──────────────────────────────────────────────────────────────────────────────────────────────

def tool_style_lab(args):
    action, style = str(args.get("action") or "status"), str(args.get("style") or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", style):
        raise ValueError("style: one of the creator's style ids from list_styles (kind='mine')")
    if action == "status":
        return call("GET", f"/cr-style-lab/{style}", timeout=30)
    if action not in ("start", "preview", "check", "save"):
        raise ValueError("action: start, status, preview, check or save")
    body = {k: args[k] for k in ("fresh", "seconds") if k in args}
    # a preview draws in a real browser on this computer (a minute or two for 3D); a check waits for the judge
    return call("POST", f"/cr-style-lab/{style}/{action}", body, timeout=900 if action == "preview" else 300)


def _schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


_OPTIONS = {"type": "object", "description": (
    "Optional. captions (default: the style's own; a style whose captions is false has none, like its channel, and "
    "captions=true burns them in anyway), thumbnails (default true; false makes no YouTube thumbnails and saves their image credits, about $0.02 "
    "each at the default model: the quote drops by that), thumbnail_count 1-4, description (YouTube "
    "description with chapters and sources, default true), extra_instructions (creative direction), script (your "
    "own narration, one title only), features {key: true/false} to switch the style's options (keys from quote), "
    "short_seconds 15-60 for Shorts, quicker_3d_review (true: one 3D review round instead of three, about a quarter "
    "to a third less Claude time on a 3D video, fewer last fixes; Create's Quicker 3D review), frame_check (a style "
    "drawn in code: false skips the check where Claude looks at each minute's frames before they are drawn and "
    "re-plans it once when it finds something clear; default Studio's setting, quote's frame_check says its time), "
    "ranking {ranks 3-10, narrator_lines, vine_booms, meme_sounds full|light|off, "
    "punch_ins, captions} for Ranking Shorts. language: the language the video speaks, its script, voice, captions "
    "and words on screen (a name like Spanish, its own name like Español, or a code like es; list_styles' languages "
    "has them all; default: the one the creator chose last in Create, else the style's own, its card's language). "
    "Voice samples in Choose voice are in English; the voice reads the video in its language. "
    "voice: a Fish Audio voice id or fish.audio voice link (from voices()) that reads these videos only, when the "
    "creator connected their own Fish Audio key in Setup & accounts. "
    "character: words for the main character who appears in every picture "
    "(age, hair, face, clothes, one recognisable detail); they replace the style's own character for these videos, and "
    "on a style with a character picture Studio draws the new one once in the style's look (one picture, about $0.02). "
    "character_picture: the path of a picture of the character on this computer (PNG, JPG or WebP); it replaces the "
    "style's character picture. To change how a style's character looks, use these rather than extra_instructions: "
    "they replace the style's picture of its old character, which otherwise goes with every image, so the new "
    "character looks the same in every picture. carry_on_after_usage_reset (default false, only when the creator "
    "chooses it): if Claude's usage limit stops a video, Studio carries it on by itself after the reset Claude "
    "reported, trying that step once more on their Claude plan.")}
_WHERE = {"type": "string", "enum": ["this_computer", "studio_cloud"],
          "description": "this_computer (free machine, one video at a time: more wait in Studio's queue and start by "
                         "themselves) or studio_cloud (a machine on the creator's own Modal account renders it, billed "
                         "by Modal; videos render side by side)."}
_FREE = {"readOnlyHint": True, "openWorldHint": False}
_DELETES = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}

TOOLS = {
    "setup_check": (tool_setup_check, "Is ChannelRecipe Studio running and ready to make videos? Also Claude Code's "
                    "status and the Studio Cloud balance. Free. Call this first.", _schema(), _FREE),
    "fix_graphics_browser": (tool_fix_graphics_browser, "When setup_check says the graphics browser isn't ready: "
                             "Studio installs it itself, in its own environment (never install it from this shell). "
                             "Free; takes a few minutes in the background.", _schema(),
                             {"readOnlyHint": False, "openWorldHint": False}),
    "fix_ffmpeg": (tool_fix_ffmpeg, "When setup_check's video_tools isn't ready: Studio downloads FFmpeg and ffprobe "
                   "(a pinned build, its SHA-256 checked) into its own folder, no password. Never install FFmpeg from "
                   "this shell. Free; a minute or two in the background.", _schema(),
                   {"readOnlyHint": False, "openWorldHint": False}),
    "fix_node": (tool_fix_node, "When setup_check's node isn't ready (missing, or older than Node.js 22): Studio "
                 "downloads Node.js from nodejs.org (pinned, checksum checked) into its own folder, no password. Never "
                 "install Node.js from this shell. Free; a minute in the background.", _schema(),
                 {"readOnlyHint": False, "openWorldHint": False}),
    "fix_claude_cli": (tool_fix_claude_cli, "When setup_check's claude_code.installed is false (Studio needs the "
                       "claude command-line tool; the Claude desktop app alone isn't enough): Studio runs Anthropic's "
                       "official installer into the creator's home folder. Free; a minute or two. The creator then "
                       "signs in once on Studio's setup page.", _schema(),
                       {"readOnlyHint": False, "openWorldHint": True}),
    "fix_speech_model": (tool_fix_speech_model, "When setup_check says the speech-timing model isn't ready (and isn't "
                         "already downloading): Studio downloads it (about 140 MB) and checks it loads, by itself. "
                         "Free; a minute or two in the background.", _schema(),
                         {"readOnlyHint": False, "openWorldHint": False}),
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
                    "On this computer they join Studio's queue (even while another video is being made) and start "
                    "by themselves, one at a time; call it again for more, in other styles too. Returns ids and each "
                    "one's place in line at once and never waits for a render. Spends money: first get the cost with "
                    "quote (or call without quote_id to get it), tell the creator, then call with quote_id. A title "
                    "that already stopped with its work saved comes back as resume_first: offer to carry it on instead.",
                    _schema({"titles": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                        "maxItems": MAX_TITLES},
                             "style": {"type": "string", "description": "A style id from list_styles."},
                             "minutes": {"type": "integer", "minimum": 1, "maximum": 60,
                                         "description": "Length of each video (Shorts use options.short_seconds)."},
                             "where": _WHERE, "options": _OPTIONS,
                             "quote_id": {"type": "string", "description": "From quote, for these same choices."},
                             "new_attempt": {"type": "boolean", "description": (
                                 "Only when the creator insists on starting a title over that already stopped "
                                 "with its work saved (make_videos returns resume_first for those): a brand-new "
                                 "video that pays again for every step. Offer resume_video first.")}},
                            ["titles", "style", "where"]),
                    {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "queue": (tool_queue, "Studio's queue on this computer: the video being made, the ones waiting in order (position, "
              "videos_before_it) and the ones on hold, with why (on_hold_because) and what to do. Free.", _schema(),
              _FREE),
    "carry_on_queue": (tool_carry_on_queue, "The videos on hold after something every video needs stopped (Claude's "
                       "usage limit, a balance, a key, the disk): carry on with them once the creator fixed it and asks "
                       "(Studio checks each one's setup again first; one still blocked stays on hold, saying why), or "
                       "remove=true to take them out of the queue. use_credits=true only for Ranking Shorts held on "
                       "used-up clip watches, and only when the creator says so. Carrying on spends money: ask first.",
                       _schema({"remove": {"type": "boolean"}, "use_credits": {"type": "boolean"}}),
                       {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "progress": (tool_progress, "How the creator's videos and style clones are coming along. id: video:<slug>, "
                 "cloud, lk_… (a style look) or op_… (a style study); none for everything. Free. Check every few "
                 "minutes, not in a tight loop. A stopped video says what stopped it (stopped_on: voice, presenter, "
                 "claude_stage, media) and what carrying it on involves.", _schema({"id": {"type": "string"}}), _FREE),
    "claude_usage": (tool_claude_usage, "How much Claude Studio has used for the creator's videos: today, the last 7 "
                     "days and all time (calls, Claude minutes, tokens from Studio 115 on), and by video, the most "
                     "used first; id='video:<slug>' for one video. Claude Code never says how much of the plan's "
                     "allowance is left: say what was used, never a percentage of a limit. Their own chats and style "
                     "studies aren't counted. Free.",
                     _schema({"id": {"type": "string"}, "videos": {"type": "integer", "minimum": 1, "maximum": 50}}),
                     _FREE),
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
    "clone_style_look": (tool_clone_style_look, "Step 1 of cloning a style from a YouTube channel or video, or of "
                         "COMBINING two or three channels into one style (youtube_urls): Studio lists their videos (a channel "
                         "of Shorts: its Shorts) and looks at up to 3 (the most viewed, taken in turn from each channel, "
                         "or reference_ids). Free (no Claude). Then progress(look_id) until done: it brings questions "
                         "for the creator and similar_styles, the styles they already have that come closest.",
                         _schema({"youtube_url": {"type": "string"},
                                  "youtube_urls": {"type": "array", "items": {"type": "string"}, "maxItems": 3,
                                                   "description": "Two or three channels to combine into one style."},
                                  "reference_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 3}}),
                         {"readOnlyHint": False, "destructiveHint": False}),
    "product_ideas": (tool_product_ideas, "Digital product ideas for a niche or channel, before you even make a video: "
                      "ideas held against what 140+ channels sell, channel names, free .com domains, pricing, the "
                      "funnel and the launch order. One Claude Code call on the creator's plan (1-2 minutes); no "
                      "money spent.",
                      _schema({"about": {"type": "string", "description": "A few lines: the niche, the audience, "
                                                                          "the channel idea."},
                               "channel": {"type": "string", "description": "The channel's YouTube link, if any."},
                               "language": {"type": "string"}}),
                      {"readOnlyHint": False, "destructiveHint": False}),
    "build_product": (tool_build_product, "Build one of the last product_ideas into a finished product (about an "
                      "hour): an illustrated, fact-checked book PDF, a landing page with Stripe checkout and a launch "
                      "guide. Spends about $0.30 of pictures and ~20 Claude Code calls: tell the creator first.",
                      _schema({"idea_number": {"type": "integer", "minimum": 1},
                               "domain": {"type": "string", "description": "A domain the creator bought, if any."},
                               "channel_name": {"type": "string"}}, ["idea_number"]),
                      {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "products": (tool_products, "The creator's digital products (books, cookbooks) and how a build is going "
                 "(id=…). Free.", _schema({"id": {"type": "string"}}), _FREE),
    "resume_video": (tool_resume_video, "Carry on a video that stopped before finishing (progress() lists them under "
                     "stopped). Reuses everything already made, so only the rest is paid for. Tell the creator what "
                     "stopped it first (progress(id)). When progress(id) shows voice_choices (its voice service "
                     "failed), ask which they want and pass it as voice: the same voice again, Gemini through Kie, "
                     "their own ElevenLabs account, their own Fish Audio account, or their own recording (pass its "
                     "file path as recording). When it "
                     "shows presenter_choices (its AI presenter failed), ask and pass presenter: skip (finish without "
                     "the presenter) or again. Before again, read them the again choice's note: when "
                     "presenter_failed.uncertain is true, the last attempt got no clear answer and may have started, "
                     "so again can pay for a second presenter; pass again only after they say yes to that.",
                     _schema({"id": {"type": "string", "description": "video:<slug> from progress()."},
                              "presenter": {"type": "string", "enum": ["skip", "again"],
                                            "description": "Only for a video that stopped on its presenter: finish "
                                                           "without it (skip) or try it again (this video only; "
                                                           "can pay twice when presenter_failed.uncertain)."},
                              "voice": {"type": "string", "enum": ["again", "gemini-man", "gemini-woman", "elevenlabs",
                                                                   "fish", "own"],
                                        "description": "Only for a video that stopped on its voice: how to voice it now "
                                                       "(this video only)."},
                              "recording": {"type": "string", "description": "voice=own: the path of the creator's "
                                                                             "recording on this computer."}}, ["id"]),
                     {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "recover_video": (tool_recover_video, "The creator's one yes for a video that stopped on a Claude step or a paid "
                      "media task (progress(id) shows stopped_on claude_stage or media, with recovery_key): allows "
                      "exactly the attempts progress listed (each stuck Claude step once more on their Claude plan; a "
                      "paid task that got no clear answer sent again, which can pay twice) and carries the video on, "
                      "reusing every finished step, picture and voice. Tell them what stopped it and what it may "
                      "cost first (what_to_tell_them), and call this only after they say yes. key: recovery_key from "
                      "progress(id), so only what they were told is approved. when='after_reset' (a usage-limit stop): "
                      "Studio carries it on by itself after Claude's reported reset. added_usage=true: they added "
                      "Claude usage, so trying before the reported reset makes sense.",
                      _schema({"id": {"type": "string", "description": "video:<slug> from progress()."},
                               "key": {"type": "string", "description": "recovery_key from progress(id), so only what "
                                                                        "the creator was told is approved (\"\" "
                                                                        "when progress showed none)."},
                               "when": {"type": "string", "enum": ["now", "after_reset"]},
                               "added_usage": {"type": "boolean"}}, ["id", "key"]),
                      {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "edit_style": (tool_edit_style, "Show or change one of the creator's learned styles (from clone_style): its name, "
                   "tagline, description, creative direction for the script (script_notes) and for the pictures and "
                   "edit (director_notes), its recurring character, which options are on (features_on), pictures a "
                   "minute, subtitles and its language (the one its videos speak unless a video asks for another, "
                   "for example Spanish for a Spanish channel). Pass only what changes; with none it shows the style. Free: no study, no "
                   "money; the version before is kept. Its study_again has what to reuse to study it again with other "
                   "videos (a new style; this one stays).",
                   _schema({"style": {"type": "string", "description": "A learned style's id or name (list_styles kind='mine')."},
                            "name": {"type": "string"}, "tagline": {"type": "string"},
                            "description": {"type": "string", "description": "What the channel's videos are about."},
                            "script_notes": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                            "director_notes": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                            "character": {"type": "string"}, "forget_character_picture": {"type": "boolean"},
                            "features_on": {"type": "array", "items": {"type": "string"}},
                            "pictures_per_min": {"type": "number"}, "subtitles": {"type": "boolean"},
                            "language": {"type": "string", "description": "A language name (Spanish), its own name "
                                         "(Español) or a code (es); list_styles' languages has them all."}}, ["style"]),
                   {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    "style_sample": (tool_style_sample, "Try a style first: about 5 seconds of an installed style on the creator's "
                     "topic (two pictures drawn as its videos draw them, one line read by their voice, a slow move "
                     "over each). Spends about $0.05 on the creator's own accounts: ask first. Offered for picture "
                     "styles (clone_style_look's similar_styles say which, try_first). id=sm_… checks one.",
                     _schema({"style": {"type": "string"}, "topic": {"type": "string"},
                              "line": {"type": "string", "description": "What the narrator says (default: the topic)."},
                              "id": {"type": "string"}}),
                     {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "clone_style": (tool_clone_style, "Step 2: study the looked-at references and build a new reusable style. Uses "
                    "the creator's Claude Code for 15-30 minutes and one of their plan's style clones. Needs look_id, a "
                    "style_name, an example_title and the creator's answers to the look's questions. accept_medium: "
                    "only after the creator read and accepted the medium_gap this returns (what Studio can't make yet). "
                    "budget_minutes: the Claude study budget (15-30, default 22); raise it only when a study stopped "
                    "because its time ran out. A style learned from Shorts makes vertical Shorts.",
                    _schema({"look_id": {"type": "string"}, "style_name": {"type": "string"},
                             "example_title": {"type": "string"},
                             "answers": {"type": "object", "additionalProperties": {"type": "string"}},
                             "base_style": {"type": "string"}, "visual_mode": {"type": "string",
                                                                               "enum": ["mixed", "reference_only"]},
                             "brief": {"type": "string"}, "example_minutes": {"type": "integer"},
                             "budget_minutes": {"type": "integer", "minimum": 15, "maximum": 30},
                             "accept_medium": {"type": "boolean"}},
                            ["look_id", "style_name", "example_title"]),
                    {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "style_lab": (tool_style_lab, "Work on a style's renderer (the code that draws every video in an animated 2D or "
                  "3D style) like any code project, then plug it back in. start: a workspace folder with renderer.js, "
                  "scene.json (the test scene), the channel's own frames (refs/) and GUIDE.md (read it first). Edit "
                  "the files yourself, then preview: contract checks, test shots, a 5 s video and a stress sweep, on "
                  "this computer. check: the likeness judge scores the shots against the channel's frames and its "
                  "bar. save: the previewed renderer becomes the style's next version for every new video. No money "
                  "is spent; show the creator the shots and the video as you go.",
                  _schema({"action": {"type": "string", "enum": ["start", "status", "preview", "check", "save"]},
                           "style": {"type": "string", "description": "A style id from list_styles (kind='mine')."},
                           "fresh": {"type": "boolean", "description": "start: begin again from the saved style "
                                                                       "(the old workspace is kept aside)."},
                           "seconds": {"type": "number", "description": "preview: video length, 1-8 s (default 5)."}},
                          ["action", "style"]),
                  {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    "niche_teaching": (tool_niche_teaching, "The niche agent's rules and the owner's good/bad examples (niches, "
                       "channels, ideas), newest from ChannelRecipe, with the numbers every check uses. Read it first "
                       "in every /niche-agent run. kind filters the examples. Free.",
                       _schema({"kind": {"type": "string", "enum": ["", "niche", "idea", "channel"]}}), _FREE),
    "niche_breakouts": (tool_niche_breakouts, "ChannelRecipe's niche database (81K faceless long-form channels): "
                        "young channels with strong recent views, found by words in their titles. q: topic words "
                        "matched in channel names and video titles (the database has no topic labels yet). "
                        "max_age_days (default 90), max_subs (50000), min_recent_avg (10000), min_ratio (views ÷ subs), "
                        "sort (recent_avg|ratio|newest), limit (25, at most 50). Seeds, not proof: check them live "
                        "with yt_channel_stats. Free; needs Studio running.",
                        _schema({"q": {"type": "string"}, "max_age_days": {"type": "integer"},
                                 "max_subs": {"type": "integer"}, "min_recent_avg": {"type": "integer"},
                                 "min_ratio": {"type": "number"}, "sort": {"type": "string"},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), _FREE),
    "yt_search": (tool_yt_search, "YouTube's own filtered search, free (public pages, no key): up to 8 queries at "
                  "once, e.g. uploaded this month sorted by views. Keeps videos with min_views or more (default 30K "
                  "long-form, 300K Shorts) and groups them by channel. Dates are YouTube's 'x weeks ago' "
                  "(approximate). Cached 6 hours.",
                  _schema({"queries": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                           "window": {"type": "string", "enum": ["today", "week", "month", "year", "any"]},
                           "sort": {"type": "string", "enum": ["views", "relevance"]},
                           "format": {"type": "string", "enum": ["long", "shorts"]},
                           "min_views": {"type": "integer"},
                           "per_query": {"type": "integer", "minimum": 5, "maximum": 60}}, ["queries"]), _FREE),
    "yt_channel_stats": (tool_yt_channel_stats, "The outlier checks for up to 6 channels (a link, @handle, UC id, a "
                         "video link, or a name), free: subscribers, the exact first upload, uploads in 30 days, "
                         "median and recent average views, banger:flop, the best video's views ÷ subscribers, the "
                         "one-hit test, and pass/fail against the teaching's numbers, with the newest 12 videos. "
                         "tab=shorts reads Shorts (thresholds not yet confirmed by the owner). Cached 12 hours.",
                         _schema({"channels": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                                  "tab": {"type": "string", "enum": ["videos", "shorts"]}}, ["channels"]), _FREE),
    "yt_look": (tool_yt_look, "A channel's newest thumbnails (up to 6) in one picture on this computer: Read its "
                "path to look at it before you count a channel (AI slop? what is it made of? which style makes it?). "
                "Give channel, or video_ids. Free.",
                _schema({"channel": {"type": "string"},
                         "video_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                         "tab": {"type": "string", "enum": ["videos", "shorts"]}}), _FREE),
    "save_ideas": (tool_save_ideas, "Save video ideas for the creator (up to 20): they appear in Studio's Create "
                   "under Saved ideas. Each: title, niche, format (long|shorts), minutes, style (a list_styles id when "
                   "one fits), recipe (which style or clone makes it, in words), twist, thumbnail, risk, notes, and "
                   "proof {video_url, video_title, views, channel, channel_url, subs, views_per_sub, "
                   "channel_age_days, first_upload, recent_avg_views, median_views, banger_flop}. Free; writes "
                   "ideas/ideas.json in this folder.",
                   _schema({"ideas": {"type": "array", "items": {"type": "object"}, "minItems": 1, "maxItems": 20}},
                           ["ideas"]), {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    "voices": (tool_voices, "The narration voice in use and, when the creator's own Fish Audio or ElevenLabs key "
               "narrates, their voices: source='mine' (their own voices and the library voices they picked) or "
               "'library' (the provider's public library; search by words, or paste a fish.audio voice link). Each "
               "voice has a free preview link where the provider has one. Free.",
               _schema({"style": {"type": "string", "description": "A style id from list_styles: its chosen voice."},
                        "source": {"type": "string", "enum": ["mine", "library"]},
                        "search": {"type": "string"}, "page": {"type": "integer", "minimum": 1}}), _FREE),
    "choose_voice": (tool_choose_voice, "Use a voice from voices() for the creator's next videos (for that style when "
                     "style is given), as Create → Choose voice does. Only when the creator picked it. Free; no "
                     "video running.",
                     _schema({"voice_id": {"type": "string", "description": "A voice id from voices(), or a "
                                                                             "fish.audio voice link."},
                              "style": {"type": "string"}, "name": {"type": "string"}}, ["voice_id"]),
                     {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}),
    "list_ideas": (tool_list_ideas, "The creator's saved ideas (status saved, used or dismissed). Free.",
                   _schema({"status": {"type": "string", "enum": ["", "saved", "used", "dismissed"]}}), _FREE),
    "use_idea": (tool_use_idea, "Use a saved idea: Create opens with its title, style, length and angle filled in "
                 "(create_url; open_browser=true opens it in a terminal session). Never starts a video or spends: "
                 "making it still goes through quote and the creator's yes. Free.",
                 _schema({"id": {"type": "string", "description": "id_… from save_ideas or list_ideas."},
                          "open_browser": {"type": "boolean"}}, ["id"]),
                 {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}),
    "delete_video": (tool_delete_video,"Delete a finished or stopped video, only when the creator asks: it moves to "
                     "Studio's trash (the finished file, its description and thumbnails, its working files) and "
                     "Recently deleted on the Studio page restores it for 30 days. Their voice library, styles and "
                     "other videos stay. Refused while the video is being made. Tell the creator which video goes "
                     "first; Claude Code asks them to approve.",
                     _schema({"id": {"type": "string", "description": "video:<slug> from list_videos or progress()."},
                              "title": {"type": "string", "description": "The video's title as list_videos or "
                                                                         "progress() shows it. It must match the "
                                                                         "video, so the approval names what goes."}},
                             ["id", "title"]), _DELETES),
    "delete_style": (tool_delete_style, "Delete a style the creator cloned (list_styles kind='mine', id cr_clone_…), "
                     "only when they ask: its recipe moves to Studio's trash and Recently deleted restores it for 30 "
                     "days. Videos made with it, their voice library and the study's notes stay; their plan's style "
                     "clone count doesn't change. Studio's own recipes can't be deleted. Tell the creator which style "
                     "goes first; Claude Code asks them to approve.",
                     _schema({"id": {"type": "string", "description": "The style's id from list_styles (cr_clone_…)."},
                              "name": {"type": "string", "description": "The style's name as list_styles shows it. It "
                                                                        "must match the style, so the approval names "
                                                                        "what goes."}}, ["id", "name"]), _DELETES),
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
