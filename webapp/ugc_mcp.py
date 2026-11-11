"""channelrecipe.com/ugc-mcp/<key>/mcp: the UGC creators' ChannelRecipe connector for Claude (owner, 2026-11-03).

Our paid UGC creators film "connect ChannelRecipe to Claude and type this" walkthroughs. They add this URL as a custom
connector in claude.ai, type the prompt from their creator kit (/ugc/<key>/), and Claude shows ChannelRecipe's
dashboards inline (MCP Apps: one ui:// page draws every tool's card) ending in a REAL finished ChannelRecipe video for
that topic, a different one each time. It is a sandbox: no sign-in, no billing, and it never starts a render or spends
anything. The tools answer from the sandbox's strategies.json on the CDN (big-frontier/faceless-ugc/sandbox, the
workspace, not this repo), kept in memory for ten minutes, so the web server only ever serves cached data.

Separate from the real ChannelRecipe MCP (mcp_server.py): its own stateless Streamable HTTP endpoint, written out here
because it needs none of the SDK's sessions or auth. The <key> is one of UGC_SANDBOX_KEY's, as for the creator kit."""

from __future__ import annotations

import json
import random
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

CDN = "https://channelrecipe-media.sfo3.cdn.digitaloceanspaces.com"
DATA_URL = CDN + "/public/ugc-sandbox/strategies.json"
DATA_TTL = 600.0                       # the CDN's own max-age
UI_URI = "ui://channelrecipe/studio.html"
UI_MIME = "text/html;profile=mcp-app"
UI_FILE = Path(__file__).resolve().parent / "ugc_mcp_app.html"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
VERSION = "1.0.0"

INSTRUCTIONS = (
    "ChannelRecipe makes finished faceless YouTube videos: it copies a channel's style and makes a whole video in it. "
    "This is ChannelRecipe's creator demo workspace: it plays real videos ChannelRecipe has already made, so results "
    "show in seconds and nothing is rendered or charged. "
    "When the user asks for a video like a channel (\"make me a GTA 6 video like Glooby\"): call clone_channel with "
    "the channel, then make_video with their idea and the same channel. When they paste a video's link and ask for one "
    "like it: call clone_channel with that link, then make_video with the same link as the channel. For ranking Shorts call rank_clips. To clip a stream, podcast or long video into Shorts call clip_video (with the link or the name). To find "
    "what's going viral in a niche call find_viral_channels. "
    "Each tool shows its own card with the work and the video. After a tool, reply in one short, upbeat sentence; "
    "don't repeat what the card shows. When a video is ready, end by pointing them to channelrecipe.com."
)
BRAND = " Make yours at channelrecipe.com."       # every result names the site (owner, 2026-11-03)

# what a request names -> the sandbox topic (strategies.json topic ids). Channel names and the topics' own titles are
# matched too; these are the words people actually type.
WORDS = {
    "gta6": ("gta", "grand theft", "vice city", "leonida", "lucia", "rockstar"),
    "roblox": ("roblox", "brookhaven", "obby", "blox"),
    "history": ("history", "historical", "roman", "rome", "medieval", "victorian", "ancient", "peasant", "gladiator",
                "viking", "plague", "great fire", "1348", "empire", "middle ages"),
    "money": ("money", "bank", "savings", "finance", "rich", "debt", "overdraft", "inflation", "economy", "invest",
              "billionaire", "tax", "salary"),
    "dark": ("scary", "horror", "creepy", "haunted", "dangerous", "deadly", "killer", "mystery", "abandoned", "motel",
             "dark"),
    "whatif": ("what if", "3d", "disaster", "titanic", "submarine", "sank", "asteroid", "volcano", "tsunami", "orca",
               "prehistoric"),
    "business": ("business", "company", "brand", "ceo", "startup", "blackberry", "nokia", "documentary"),
    "essays": ("psychology", "lonely", "loneliness", "forgotten", "mind", "brain", "essay", "why we", "why you feel"),
    "comedy": ("wake up as", "animal", "sloth", "shrimp", "octopus", "stomach", "yawn", "facts"),
    "rank-cats": ("cat", "cats", "kitten", "kitty"),
    "rank-dogs": ("dog", "dogs", "puppy", "puppies"),
    "rank-sharks": ("shark", "sharks"),
    "clip-gta6": ("kai", "cenat", "gta", "stream", "streamer", "live reaction", "trailer"),
    "kids": ("nursery", "rhyme", "rhymes", "kids song", "kids songs", "toddler", "toddlers", "lullaby", "cocomelon",
             "humpty", "wheels on the", "baby shark", "children s song", "for kids"),
    "highway": ("karma", "instant karma", "highway", "reckless", "road rage", "semi truck", "bad driver", "drivers"),
    "fruit": ("fruit", "fruits", "veggie", "veggies", "banana", "strawberry", "watermelon", "mango", "pineapple"),
    "zack": ("how it works", "how does", "how do", "what happens", "inside your", "medical", "stitches", "injection",
             "your body"),
}


# ---------------------------------------------------------------------------------------------------------------------
# the sandbox data (cached; a CDN miss keeps the last copy)

_data: dict[str, Any] = {"at": 0.0, "value": None}
_about: OrderedDict[str, tuple[float, str]] = OrderedDict()   # a pasted link -> (when, its title and channel), recent ones
_about_lock = threading.Lock()
ABOUT_S, ABOUT_FAIL_S, ABOUT_KEEP = 4.0, 600.0, 512           # the oEmbed wait; a failed look is asked again after this
_data_lock = threading.Lock()


def _fetch() -> dict:
    import httpx
    r = httpx.get(DATA_URL, params={"h": int(time.time() // DATA_TTL)}, timeout=6.0)
    r.raise_for_status()
    return r.json()


def data() -> dict:
    if _data["value"] is not None and time.time() - _data["at"] < DATA_TTL:
        return _data["value"]
    with _data_lock:
        if _data["value"] is None or time.time() - _data["at"] >= DATA_TTL:
            try:
                _data["value"] = _fetch()
            except Exception as e:
                print(f"[ugc-mcp] strategies.json: {e}")
                if _data["value"] is None:
                    raise
            _data["at"] = time.time()                    # a failed refresh retries in ten minutes, on the old copy
    return _data["value"]


def _method(d: dict, mid: str) -> dict:
    return next((m for m in d.get("methods") or [] if m.get("id") == mid), {"topics": []})


def _ready(m: dict) -> list[dict]:
    return [] if m.get("soon") else [t for t in m.get("topics") or [] if t.get("outputs")]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _words(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", (s or "").lower()) + " "


def find_channel(d: dict, asked: str) -> tuple[dict, dict] | None:
    """The scouted channel the user named (a name, @handle or channel link) -> (its best ref, its topic)."""
    q = (asked or "").strip()
    if not q:
        return None
    url = re.search(r"youtube\.com/channel/([A-Za-z0-9_-]+)", q)
    handle = _norm(re.sub(r"^.*youtube\.com/", "", q).lstrip("@").split("/")[0].split("?")[0])
    best = None
    for t in _method(d, "clone").get("topics") or []:
        for r in t.get("refs") or []:
            name = _norm(r.get("channel", ""))
            if url and url.group(1) in (r.get("channel_url") or ""):
                return r, t
            if name and handle and (name == handle or (len(name) >= 4 and name in handle)
                                    or (len(handle) >= 4 and handle in name)):
                score = (name == handle, r.get("views", 0))
                if best is None or score > best[0]:
                    best = (score, r, t)
    return (best[1], best[2]) if best else None


# a pasted video link (YouTube watch/shorts/youtu.be, an Instagram reel or post, a TikTok video) -> its id
_LINK_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/|/reels?/|/p/|/video/)([A-Za-z0-9_-]{6,})")


def find_link(d: dict, *texts: str) -> tuple[dict, dict] | None:
    """A pasted video link -> (its channel's best ref, its topic), so "make me a video like this" makes that video's
    kind (the hub for the owner, 2026-11-09: never a GTA 6 video for a cartoon's link). Known ids: the topic's own
    `link` and `reels` (the viral reels that show its videos), its scouted refs and their channels' uploads."""
    ids = {m.group(1) for t in texts if t for m in _LINK_ID.finditer(t)}
    if not ids:
        return None
    channels = d.get("channels") or {}
    for t in _method(d, "clone").get("topics") or []:
        refs = t.get("refs") or []
        own = {m.group(1) for m in _LINK_ID.finditer(str(t.get("link") or ""))} | set(t.get("reels") or [])
        if ids & own:
            return (refs[0] if refs else {"channel": t.get("pin") or t["name"], "channel_url": ""}), t
        for r in refs:
            if r.get("id") in ids or ids & {u.get("id") for u in channels.get(r.get("channel_url") or "", [])}:
                return r, t
    return None


_URL = re.compile(r"https?://\S+|(?:www\.)?(?:youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/\S+", re.I)


def _no_links(s: str) -> str:
    """The words of a request without its links: a link's own text says nothing of its topic (2026-11-11: "shorts" in
    every youtube.com/shorts/ link matched '3D "how it works" Shorts', so every unknown Short became Zack D. Films)."""
    return _URL.sub(" ", s or "")


def _oembed(url: str) -> dict | None:
    """The public oEmbed answer for a YouTube or TikTok video link (its title and channel; no key, one small GET)."""
    import requests
    if re.search(r"youtube\.com|youtu\.be", url, re.I):
        api, params = "https://www.youtube.com/oembed", {"format": "json", "url": url}
    elif re.search(r"tiktok\.com", url, re.I):
        api, params = "https://www.tiktok.com/oembed", {"url": url}
    else:
        return None                                   # (Instagram's oEmbed needs a Facebook app token)
    r = requests.get(api, params=params, timeout=ABOUT_S, headers={"User-Agent": "ChannelRecipe/1.0"})
    r.raise_for_status()
    return r.json()


def link_about(*texts: str) -> tuple[str, str]:
    """A pasted video link's (title, channel), read once and kept (a few hundred recent links); ("", "") for no link, an
    Instagram one, or one that can't be read (asked again after ABOUT_FAIL_S)."""
    m = next((m for x in texts if x for m in _URL.finditer(x)), None)
    if not m:
        return "", ""
    url = m.group(0).rstrip(").,]>\"'")
    url = url if url.lower().startswith("http") else "https://" + url
    with _about_lock:
        got = _about.get(url)
        if got and (got[1] or time.time() - got[0] < ABOUT_FAIL_S):
            _about.move_to_end(url)
            return tuple(got[1].split("\n", 1)) if got[1] else ("", "")
    try:
        o = _oembed(url) or {}
        said = f"{o.get('title') or ''}\n{o.get('author_name') or ''}" if o.get("title") else ""
    except Exception as e:                            # noqa: BLE001 - unknown, as before: Claude asks which kind
        print(f"[ugc-mcp] oembed {url[:80]}: {type(e).__name__}")
        said = ""
    with _about_lock:
        _about[url] = (time.time(), said)
        while len(_about) > ABOUT_KEEP:
            _about.popitem(last=False)
    return tuple(said.split("\n", 1)) if said else ("", "")


# words in topic names that say nothing about the topic ("Best shark moments" is about sharks; a Short's "#shorts")
PLAIN = {"best", "funniest", "moments", "video", "videos", "stories", "story", "channels", "channel", "explained",
         "places", "make", "about", "facts", "what", "your", "with", "that", "this", "from", "shorts", "short",
         "youtube", "reels", "tiktok"}


def _sig(s: str) -> set[str]:
    return {w for w in _words(s).split() if len(w) > 3 and w not in PLAIN}


def find_topic(d: dict, mid: str, *texts: str) -> dict | None:
    """The topic of method `mid` a request is about: its words, its videos' titles, its channels' names (never a link's
    own text: _no_links)."""
    blob = _words(" ".join(_no_links(t) for t in texts if t))
    scored = []
    for t in _method(d, mid).get("topics") or []:
        score = sum(3 for w in WORDS.get(t["id"], ()) if f" {w} " in blob)
        score += sum(2 for w in _sig(t.get("name", "")) if f" {w} " in blob)
        for o in t.get("outputs") or []:
            score += sum(1 for w in _sig(o.get("title", "")) if len(w) > 4 and f" {w} " in blob)
        score += sum(4 for r in t.get("refs") or [] if len(_norm(r.get("channel", ""))) >= 4
                     and _norm(r.get("channel", "")) in _norm(blob))
        if score:
            scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]


# ---------------------------------------------------------------------------------------------------------------------
# a different video every time: the last one shown per creator key and topic, shared by every web container

_last_mem: dict[str, int] = {}
_table_ready = False


def _db_last(bucket: str, new: int | None = None) -> int | None:
    global _table_ready
    from webapp.database import _conn, _q, schema_lock
    with _conn() as conn:
        cur = conn.cursor()
        if not _table_ready:
            schema_lock(conn, "ugc_mcp")
            cur.execute("CREATE TABLE IF NOT EXISTS ugc_mcp_picks (bucket TEXT PRIMARY KEY, last INTEGER, at REAL)")
            _table_ready = True
        if new is None:
            cur.execute(_q("SELECT last FROM ugc_mcp_picks WHERE bucket = ?"), (bucket,))
            row = cur.fetchone()
            return None if row is None else int(dict(row)["last"])
        cur.execute(_q("INSERT INTO ugc_mcp_picks (bucket, last, at) VALUES (?, ?, ?) ON CONFLICT (bucket) DO UPDATE "
                       "SET last = excluded.last, at = excluded.at"), (bucket, new, time.time()))
        return new


def named(outs: list[dict], idea: str) -> dict | None:
    """The ready-made video an idea names (most of its title's words), e.g. an idea chip on clone_channel's card."""
    asked = _sig(idea)
    fit = lambda title: len(_sig(title) & asked) / max(1, len(_sig(title)))
    score = lambda o: max(fit(x) for x in [o["title"], *(o.get("titles") or [])])
    best = max(outs, key=score)
    return best if score(best) >= 0.6 else None


def pick(bucket: str, n: int) -> int:
    """A random index in range(n), never the one this bucket got last time (when there is a choice)."""
    try:
        last = _db_last(bucket)
    except Exception as e:
        print(f"[ugc-mcp] picks table: {e}")
        last = _last_mem.get(bucket)
    choices = [i for i in range(n) if i != last] or list(range(n))
    i = random.choice(choices)
    _last_mem[bucket] = i
    try:
        _db_last(bucket, i)
    except Exception:
        pass
    return i


# ---------------------------------------------------------------------------------------------------------------------
# the tools

def _short(n: float) -> str:
    n = float(n or 0)
    if n >= 1e6:
        return f"{n / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{round(n / 1e3)}K" if n >= 1e3 else str(int(n))


def _mmss(s: float) -> str:
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


def _ref(r: dict) -> dict:
    return {k: r.get(k) for k in ("id", "title", "views", "channel", "channel_url", "seconds", "url")}


def _video(o: dict) -> dict:
    return {k: o.get(k) for k in ("video", "poster", "title", "frames", "seconds", "tall", "script")}


# Many creators film the same outcome (owner, 2026-11-03: GTA 6 farming), so no two runs look alike: a title from the
# output's `titles` variants (never this key's last one for that video), a random start point that still leaves 25 s
# to play, and a random handful of its scene frames. The last titles are remembered per web container (best effort).
_last_title: OrderedDict[str, str] = OrderedDict()


def _title(o: dict, key: str) -> str:
    names = [t for t in (o.get("titles") or []) if isinstance(t, str) and t.strip()] or [o.get("title") or ""]
    slot = f"{key}:{o.get('video')}"
    title = random.choice([t for t in names if t != _last_title.get(slot)] or names)
    _last_title[slot] = title
    _last_title.move_to_end(slot)
    while len(_last_title) > 4096:
        _last_title.popitem(last=False)
    return title


def _take(o: dict, key: str) -> dict:
    v = _video(o)
    v["title"] = _title(o, key)
    secs = float(o.get("clip_seconds") or o.get("seconds") or 0)   # the file that plays is the video's first 90 s
    v["start"] = round(random.uniform(0, secs - 25), 1) if secs >= 30 else 0
    frames = list(o.get("frames") or [])
    if len(frames) > 4:                                  # 4-6 of them, in story order
        keep = sorted(random.sample(range(len(frames)), random.randint(4, len(frames))))
        v["frames"] = [frames[i] for i in keep]
    return v


def _menu(d: dict, mid: str) -> list[str]:
    return [t["name"] for t in _ready(_method(d, mid))]


def _which_kind(d: dict, title: str = "") -> dict:
    """A pasted link whose kind the demo can't tell: Claude asks them which kind, never a guess (2026-11-11: every unknown
    Short became Zack D. Films for a creator: GTA 6, kids, karma)."""
    names = _menu(d, "clone")
    text = ((f"Couldn't tell which kind of video \"{title}\" is. " if title else "Couldn't tell which kind of video that "
             "link is. ") + "Ask them in one short line which kind they want, then call clone_channel with that kind: "
            + ", ".join(names) + ".")
    return {"text": text, "ui": {"view": "note", "title": "Which kind of video?", "text": text, "options": names}}


def link_topic(d: dict, *texts: str) -> tuple[dict | None, dict | None, str] | None:
    """An unknown pasted link, by what the video is (its oEmbed title and channel): (a scouted channel's ref, its topic,
    the title) or (None, the topic by the title's words, the title); (None, None, title) when it can't be told; None for
    no readable link."""
    title, author = link_about(*texts)
    if not title:
        return None
    hit = find_channel(d, author) if author else None
    if hit:
        return hit[0], hit[1], title
    return None, find_topic(d, "clone", title, *texts), title


def _not_here(d: dict, mid: str, what: str) -> dict:
    names = _menu(d, mid)
    text = (f"{what} isn't in ChannelRecipe's demo workspace yet. Ready-made here: {', '.join(names)}."
            if names else f"{what} isn't in ChannelRecipe's demo workspace yet.")
    return {"text": text, "ui": {"view": "note", "title": what, "text": text, "options": names}}


def find_viral_channels(d: dict, args: dict, key: str) -> dict:
    niche = str(args.get("niche") or "").strip()
    t = find_topic(d, "clone", niche) or (find_channel(d, niche) or (None, None))[1]
    if not t or not t.get("refs"):
        return _not_here(d, "clone", niche or "That niche")
    refs = [_ref(r) for r in t["refs"]]
    lines = [f"{r['channel']}: \"{r['title']}\" ({_short(r['views'])} views)" for r in refs]
    return {"text": f"Going viral this month in {t['name']} (YouTube, most-viewed): " + "; ".join(lines) + ".",
            "ui": {"view": "viral", "topic": t["name"], "pin": t.get("pin"), "refs": refs}}


def clone_channel(d: dict, args: dict, key: str) -> dict:
    asked = str(args.get("channel") or "").strip()
    hit = find_channel(d, asked) or find_link(d, asked)
    if hit:
        ref, t = hit
        exact = True
    else:
        seen = link_topic(d, asked) if _URL.search(asked) else None   # (an unknown link: what the video is)
        if seen and seen[0]:
            ref, t, exact = seen[0], seen[1], True
        else:
            t = seen[1] if seen else find_topic(d, "clone", asked)
            ref, exact = ((t or {}).get("refs") or [None])[0], False
            if not t and _URL.search(asked):
                return _which_kind(d, seen[2] if seen else "")
    if not t or not ref:
        return _not_here(d, "clone", f"The channel \"{asked}\"" if asked else "That channel")
    url = ref.get("channel_url") or ""
    uploads = [u for u in (d.get("channels") or {}).get(url, []) if u.get("id")]
    for r in t.get("refs") or []:                      # its videos the scout found, if the upload list missed them
        if r.get("channel_url") == url and r["id"] not in {u["id"] for u in uploads}:
            uploads.append({"id": r["id"], "title": r["title"], "views": r["views"]})
    uploads.sort(key=lambda u: u.get("views", 0), reverse=True)
    total = sum(int(u.get("views") or 0) for u in uploads)
    top = uploads[0] if uploads else {"title": ref.get("title"), "views": ref.get("views")}
    lens = [r["seconds"] for r in t.get("refs") or [] if r.get("channel_url") == url and r.get("seconds")]
    ideas = [o["title"] for o in t.get("outputs") or []]
    stats = {"videos": len(uploads), "views": total, "avg": round(total / len(uploads)) if uploads else 0,
             "top": {"title": top.get("title"), "views": top.get("views")},
             "length": round(sum(lens) / len(lens)) if lens else 0}
    shown = random.sample(uploads, min(len(uploads), random.randint(4, 6))) if len(uploads) > 4 else uploads[:]
    random.shuffle(ideas)
    lead = (f"Style learned: {ref['channel']}" if exact else
            f"Closest channel in ChannelRecipe's library: {ref['channel']}")
    text = (f"{lead} ({t['name']}). Studied {stats['videos']} uploads, {_short(total)} views in all; top video "
            f"\"{stats['top']['title']}\" ({_short(stats['top']['views'])}). Style: " + "; ".join(t.get("look") or [])
            + "." + (f" Ready to make in this style: {'; '.join(ideas)}." if ideas else
                     " Its sample videos are still being made, so make_video can't show one in this style yet."))
    return {"text": text, "ui": {"view": "clone", "channel": ref["channel"], "channel_url": url, "exact": exact,
                                 "topic": t["name"], "uploads": shown, "stats": stats,
                                 "look": t.get("look") or [], "ideas": ideas}}


def make_video(d: dict, args: dict, key: str) -> dict:
    idea = str(args.get("idea") or "").strip()
    style = str(args.get("channel") or "").strip()
    hit = (find_channel(d, style) if style else None) or find_link(d, style, idea)
    seen = link_topic(d, style, idea) if not hit and _URL.search(f"{style} {idea}") else None
    if seen and seen[0]:
        hit = (seen[0], seen[1])
    t = (hit[1] if hit else None) or (seen[1] if seen else None) or find_topic(d, "clone", idea, style)
    if not t and _URL.search(f"{style} {idea}"):
        return _which_kind(d, seen[2] if seen else "")
    if not t:
        return _not_here(d, "clone", f"\"{idea}\"" if idea else "That video")
    outs = t.get("outputs") or []
    if not outs:
        text = (f"ChannelRecipe's {t['name']} sample videos are still being made for this demo workspace. "
                "Try another style for now: " + ", ".join(_menu(d, "clone")) + ".")
        return {"text": text, "ui": {"view": "note", "title": t["name"], "text": text, "options": _menu(d, "clone")}}
    o = named(outs, idea) or outs[pick(f"{key}:make:{t['id']}", len(outs))]
    chan = hit[0]["channel"] if hit else None
    v = _take(o, key)
    text = (f"Finished: \"{v['title']}\" ({_mmss(o.get('seconds'))}, {t['name']}), a real ChannelRecipe video, "
            f"playing in the card. Video: {o['video']}. (Demo workspace: it plays a video ChannelRecipe made earlier "
            "for this niche, so no new render was started.)")
    return {"text": text, "ui": {"view": "make", "topic": t["name"], "channel": chan, "idea": idea,
                                 "video": v}}


def rank_clips(d: dict, args: dict, key: str) -> dict:
    asked = str(args.get("topic") or "").strip()
    t = find_topic(d, "ranking", asked)
    if not t or not t.get("outputs"):
        return _not_here(d, "ranking", f"Ranking \"{asked}\"" if asked else "That ranking")
    o = t["outputs"][pick(f"{key}:rank:{t['id']}", len(t["outputs"]))]
    found = [{k: c.get(k) for k in ("id", "title", "views")} for c in t.get("clips") or []]
    clips = random.sample(found, min(len(found), random.randint(9, 12)))      # a different handful each run
    top = sorted(clips, key=lambda c: c.get("views") or 0, reverse=True)[:5]
    v = _take(o, key)
    text = (f"Ranking Short ready: \"{v['title']}\" ({_mmss(o.get('seconds'))}). Found {len(clips)} viral clips "
            f"(top one {_short(top[0]['views']) if top else '?'} views), picked each one's best moment and counted "
            f"down the top 5. Video: {o['video']}. (Demo workspace: it plays a ranking Short ChannelRecipe made "
            "earlier, so no new render was started.)")
    return {"text": text, "ui": {"view": "rank", "topic": t["name"], "clips": clips, "top": top, "video": v}}


def _yt_id(s: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", s or "")
    return m.group(1) if m else ""


def clip_video(d: dict, args: dict, key: str) -> dict:
    """A long video (link or name) -> its 10 viral moments as captioned vertical clips. Each clipping output is one
    SOURCE the hub clipped for real (strategies.json: `source` + `clips`); a random 6-10 of its clips show each run."""
    asked = str(args.get("video") or args.get("url") or "").strip()
    sources = [(t, o) for t in _ready(_method(d, "clipping")) for o in t["outputs"] if o.get("clips")]
    if not sources:
        return _not_here(d, "clipping", "Clipping")
    vid, blob = _yt_id(asked), _words(asked)
    exact = [(t, o) for t, o in sources if vid and vid == _yt_id((o.get("source") or {}).get("url", ""))]
    named_ = [(t, o) for t, o in sources
              if len(_norm((o.get("source") or {}).get("channel", ""))) >= 3
              and _norm((o.get("source") or {}).get("channel", "")) in _norm(asked)]
    t = find_topic(d, "clipping", asked)
    pool = exact or named_ or [(tt, o) for tt, o in sources if t and tt is t] or sources
    t, o = pool[pick(f"{key}:clip:{pool[0][0]['id']}", len(pool))] if len(pool) > 1 else pool[0]
    src = {k: (o.get("source") or {}).get(k) for k in ("title", "channel", "url", "seconds", "views", "thumb")}
    src["title"] = src["title"] or o.get("title")
    every = [c for c in o["clips"] if c.get("video")]
    shown = random.sample(every, min(len(every), random.randint(6, 10)))
    clips = [{**{k: c.get(k) for k in ("video", "poster", "start", "end", "seconds")}, "title": _title(c, key)}
             for c in shown]
    text = (f"Clipped: {len(clips)} viral clips from \"{src['title']}\""
            + (f" ({src['channel']}, {_mmss(src['seconds'])})" if src.get("channel") and src.get("seconds") else "")
            + ", vertical and captioned, ready to post: " + "; ".join(f"\"{c['title']}\"" for c in clips) + ". "
            f"(Demo workspace: these are clips ChannelRecipe cut earlier from that video, so nothing new was cut.)")
    return {"text": text, "ui": {"view": "clips", "topic": t["name"], "source": src, "found": len(every),
                                 "clips": clips}}


_RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
TOOLS = [
    {"name": "find_viral_channels", "title": "Find viral channels", "fn": find_viral_channels,
     "description": "Find faceless YouTube channels going viral right now in a niche (this month's most-viewed "
                    "videos). Use it when the user asks what's working or which channel to copy.",
     "inputSchema": {"type": "object", "properties": {
         "niche": {"type": "string", "description": "The niche or topic, e.g. \"GTA 6\", \"history\", \"Roblox stories\""}},
         "required": ["niche"]}},
    {"name": "clone_channel", "title": "Clone a channel's style", "fn": clone_channel,
     "description": "Study a YouTube channel's style (its uploads, views, pacing and look) so make_video can copy it. "
                    "Accepts a channel name, @handle or channel link, or a video's link (to make videos like it). Call it "
                    "before make_video when the user names a channel or pastes a video's link.",
     "inputSchema": {"type": "object", "properties": {
         "channel": {"type": "string", "description": "Channel name, @handle, channel link or a video's link"}},
         "required": ["channel"]}},
    {"name": "make_video", "title": "Make a video", "fn": make_video,
     "description": "Make a finished faceless YouTube video for an idea, in a cloned channel's style when one is "
                    "given. Shows the script, voice, scenes and edit, then the finished video.",
     "inputSchema": {"type": "object", "properties": {
         "idea": {"type": "string", "description": "What the video is about, in the user's words"},
         "channel": {"type": "string", "description": "The channel whose style to copy (from clone_channel), or the "
                                                     "video link the user pasted, if any"}},
         "required": ["idea"]}},
    {"name": "rank_clips", "title": "Make a ranking Short", "fn": rank_clips,
     "description": "Make a viral ranking Short: finds the most-viewed clips for a topic, picks each one's best "
                    "moment and counts down the top 5.",
     "inputSchema": {"type": "object", "properties": {
         "topic": {"type": "string", "description": "What to rank, e.g. \"funniest cat moments\""}},
         "required": ["topic"]}},
    {"name": "clip_video", "title": "Clip a stream into viral clips", "fn": clip_video, "needs": "clipping",
     "description": "Clip a long video (a stream, podcast or YouTube video, e.g. a Kai Cenat stream) into 10 viral "
                    "clips: watches the whole thing, finds the moments that go viral, and cuts them into vertical, "
                    "captioned Shorts ready to post. Takes the video's link or just its name.",
     "inputSchema": {"type": "object", "properties": {
         "video": {"type": "string", "description": "The video's link, or its name (e.g. \"Kai Cenat GTA 6 stream\")"}},
         "required": ["video"]}},
]


def _tools(d: dict | None) -> list[dict]:
    """The tools with a demo behind them (clipping appears once the sandbox has real clipping outputs)."""
    out = []
    for t in TOOLS:
        if t.get("needs") and not (d and _ready(_method(d, t["needs"]))):
            continue
        out.append({"name": t["name"], "title": t["title"], "description": t["description"],
                    "inputSchema": t["inputSchema"], "annotations": _RO,
                    "_meta": {"ui": {"resourceUri": UI_URI}, "ui/resourceUri": UI_URI}})
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the page Claude draws the cards with

def _ui_html() -> str:
    return UI_FILE.read_text(encoding="utf-8")


UI_META = {"ui": {"csp": {"resourceDomains": [CDN, "https://i.ytimg.com", "https://fonts.googleapis.com",
                                              "https://fonts.gstatic.com"], "connectDomains": []},
                  "prefersBorder": True}}


# ---------------------------------------------------------------------------------------------------------------------
# JSON-RPC

class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def call(method: str, params: dict, key: str, speed: str = "") -> dict:
    if method == "initialize":
        asked = str((params or {}).get("protocolVersion") or "")
        return {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False},
                                 "extensions": {"io.modelcontextprotocol/ui": {}}},
                "serverInfo": {"name": "channelrecipe", "title": "ChannelRecipe", "version": VERSION,
                               "websiteUrl": "https://channelrecipe.com"},
                "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        try:
            d = data()
        except Exception:
            d = None
        return {"tools": _tools(d)}
    if method == "resources/list":
        return {"resources": [{"uri": UI_URI, "name": "ChannelRecipe Studio", "mimeType": UI_MIME,
                               "description": "ChannelRecipe's dashboards for its tools", "_meta": UI_META}]}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "prompts/list":
        return {"prompts": []}
    if method == "resources/read":
        if (params or {}).get("uri") != UI_URI:
            raise RpcError(-32002, "Resource not found")
        return {"contents": [{"uri": UI_URI, "mimeType": UI_MIME, "text": _ui_html(), "_meta": UI_META}]}
    if method == "tools/call":
        name = (params or {}).get("name")
        args = (params or {}).get("arguments") or {}
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None or not isinstance(args, dict):
            raise RpcError(-32602, f"Unknown tool: {name}")
        try:
            d = data()
        except Exception:
            return {"content": [{"type": "text", "text": "ChannelRecipe's library didn't answer. Try again in a "
                                                         "minute."}], "isError": True}
        if tool.get("needs") and not _ready(_method(d, tool["needs"])):
            raise RpcError(-32602, f"Unknown tool: {name}")
        out = tool["fn"](d, args, key)
        ui = {**out["ui"], "speed": speed}
        return {"content": [{"type": "text", "text": out["text"] + BRAND}], "structuredContent": ui}
    raise RpcError(-32601, f"Method not found: {method}")


def handle(body: bytes, key: str, speed: str = "") -> tuple[int, Any]:
    """One POST: (status, JSON body or None). Notifications and responses get 202 with no body."""
    try:
        msg = json.loads(body or b"null")
    except Exception:
        return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    batch = isinstance(msg, list)
    replies = []
    for m in (msg if batch else [msg]):
        if not isinstance(m, dict) or m.get("jsonrpc") != "2.0":
            replies.append({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}})
            continue
        if "method" not in m or "id" not in m:          # a notification, or a client's answer: nothing to say back
            continue
        try:
            replies.append({"jsonrpc": "2.0", "id": m["id"],
                            "result": call(m["method"], m.get("params") or {}, key, speed)})
        except RpcError as e:
            replies.append({"jsonrpc": "2.0", "id": m["id"], "error": {"code": e.code, "message": e.message}})
        except Exception as e:
            print(f"[ugc-mcp] {m.get('method')}: {e!r}")
            replies.append({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32603, "message": "Internal error"}})
    if not replies:
        return 202, None
    return 200, (replies if batch else replies[0])
