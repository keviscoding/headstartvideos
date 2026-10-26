"""Channel check: "I already have a channel and it isn't getting views". Why, and what to do next.

Someone pastes their channel. We read its public numbers from YouTube (about 4 API units: the channel, its last
uploads, their stats), work out its niche from its titles, and compare it with the small channels breaking out in
that niche right now (the Niche Finder's catalogue, money_niche.proof_channels).

- Anyone sees the taste: their channel as a dashboard (views on each recent video, upload rhythm) and the single
  biggest thing holding it back.
- Signed in, they get every finding, the breakout channels to study, a step-by-step plan, five video ideas with a
  thumbnail idea each, and the recipe to make them in ChannelRecipe.

The findings are plain arithmetic on public numbers, so they're the same for everyone and cost nothing. The ideas
are the one paid call: shared per channel per day and inside money_niche's caps (per account, and a global daily
spend). Past a cap, the niche's own written ideas are used. A channel is read from YouTube at most once per 12 hours,
and the number of new channels read a day is capped too (CHANNEL_CHECK_DAILY).
"""
from __future__ import annotations

import json
import re
import secrets
import statistics
import threading
import time
from datetime import datetime, timezone

import requests

from webapp import database as db
from webapp import money_niche as mn

FRESH_S = 12 * 3600
VIDEOS = 15
_ready: set = set()
_lock = threading.Lock()


class CheckError(ValueError):
    pass


def initialize() -> None:
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with db._conn() as conn:
            db.schema_lock(conn, "channel_check")                  # two containers boot together: one at a time
            conn.execute("""CREATE TABLE IF NOT EXISTS channel_checks (
                id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, data_json TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_checks_channel ON channel_checks (channel_id, created_at)")
            conn.execute("""CREATE TABLE IF NOT EXISTS channel_check_ideas (
                channel_id TEXT NOT NULL, day TEXT NOT NULL, ideas_json TEXT NOT NULL,
                PRIMARY KEY (channel_id, day))""")
        _ready.add(key)


# ---- reading the channel from YouTube -----------------------------------------------------------------------

def normalise(raw: str) -> str:
    """A channel link, @handle or UC id as a URL core.channel_data understands."""
    raw = (raw or "").strip()
    if not raw or len(raw) > 300:
        raise CheckError("Paste your channel link, like youtube.com/@yourchannel.")
    if re.fullmatch(r"@?[\w.-]{3,100}", raw) and not raw.startswith("UC"):
        return "https://www.youtube.com/@" + raw.lstrip("@")
    if raw.startswith("UC") or "youtube.com" in raw or "youtu.be" in raw:
        return raw if raw.startswith(("http", "UC")) else "https://" + raw
    raise CheckError("That doesn't look like a YouTube channel. Paste a link like youtube.com/@yourchannel.")


def _seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def fetch(url: str, api_key: str) -> dict:
    """The channel's public numbers and its last uploads. Raises CheckError for a channel we can't read."""
    from core.channel_data import _extract_channel_id
    from googleapiclient.discovery import build
    try:
        cid = _extract_channel_id(normalise(url), api_key)
        yt = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
        items = yt.channels().list(part="snippet,statistics,contentDetails", id=cid).execute().get("items") or []
        if not items:
            raise CheckError("We couldn't find that channel. Check the link and try again.")
        ch = items[0]
        uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        listed = yt.playlistItems().list(part="snippet,contentDetails", playlistId=uploads,
                                         maxResults=VIDEOS).execute().get("items") or []
        ids = [i["contentDetails"]["videoId"] for i in listed]
        stats = {v["id"]: v for v in (yt.videos().list(part="statistics,contentDetails", id=",".join(ids))
                                       .execute().get("items") or [])} if ids else {}
    except CheckError:
        raise
    except ValueError as e:
        raise CheckError(str(e)) from None
    except Exception as e:                                          # noqa: BLE001 - YouTube said no
        print(f"[channel-check] YouTube read failed for {url!r}: {e}")
        raise CheckError("We couldn't read that channel from YouTube. Check the link, or try again in a minute.") from None
    sn, st = ch.get("snippet") or {}, ch.get("statistics") or {}
    thumbs = sn.get("thumbnails") or {}
    videos = []
    for item in listed:
        vid = item["contentDetails"]["videoId"]
        s = stats.get(vid) or {}
        vs, vsn = s.get("statistics") or {}, item.get("snippet") or {}
        seconds = _seconds((s.get("contentDetails") or {}).get("duration") or "")
        videos.append({"id": vid, "title": vsn.get("title") or "", "url": f"https://www.youtube.com/watch?v={vid}",
                       "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                       "views": int(vs.get("viewCount") or 0), "likes": int(vs.get("likeCount") or 0),
                       "comments": int(vs.get("commentCount") or 0), "seconds": seconds,
                       "short": 0 < seconds <= 180, "published_at": vsn.get("publishedAt") or ""})
    return {"channel_id": cid, "name": sn.get("title") or "", "url": f"https://www.youtube.com/channel/{cid}",
            "handle": sn.get("customUrl") or "",
            "avatar": ((thumbs.get("medium") or thumbs.get("default") or {}).get("url") or ""),
            "subscribers": int(st.get("subscriberCount") or 0), "total_views": int(st.get("viewCount") or 0),
            "video_count": int(st.get("videoCount") or 0), "started": sn.get("publishedAt") or "",
            "hidden_subscribers": bool(st.get("hiddenSubscriberCount")), "videos": videos}


# ---- what the numbers say -----------------------------------------------------------------------------------

def _age_days(stamp: str, now: float) -> float | None:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when.timestamp()) / 86400


def detect_niche(channel: dict) -> dict | None:
    """The quiz niche whose words show up most in the channel's name and titles."""
    text = " ".join([channel.get("name", "")] + [v["title"] for v in channel["videos"]]).lower()
    best, best_hits = None, 0
    for niche in mn.NICHES:
        hits = sum(text.count(w.lower()) for w in niche.get("keywords") or [])
        if hits > best_hits:
            best, best_hits = niche, hits
    return best


def _fmt(n: float) -> str:
    n = float(n or 0)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M".replace(".0M", "M")
    if n >= 1e3:
        return f"{n / 1e3:.0f}K" if n >= 1e4 else f"{n / 1e3:.1f}K".replace(".0K", "K")
    return str(int(n))


def diagnose(channel: dict, peers: list[dict], *, now: float | None = None) -> dict:
    """Findings from the channel's own public numbers, worst first, and the numbers behind them."""
    now = now or time.time()
    videos = channel["videos"]
    findings = []
    if not videos:
        return {"findings": [{"id": "no_videos", "level": "bad", "title": "No public videos yet",
                              "detail": "Everything starts with the first upload. Pick a niche and make video one."}],
                "stats": {}}
    ages = [a for a in (_age_days(v["published_at"], now) for v in videos) if a is not None]
    gaps = [b - a for a, b in zip(ages, ages[1:]) if b >= a]
    median_gap = statistics.median(gaps) if gaps else None
    last_age = min(ages) if ages else None
    long_videos = [v for v in videos if not v["short"]] or videos
    views = [v["views"] for v in long_videos]
    median_views = statistics.median(views)
    shorts_share = sum(1 for v in videos if v["short"]) / len(videos)
    peer_views = statistics.median([p["recent_avg_views"] for p in peers]) if peers else None
    peer_pace = None
    peer_rates = [p.get("uploads_per_month") for p in peers if p.get("uploads_per_month")]
    if peer_rates:
        peer_pace = statistics.median(peer_rates)

    if last_age is not None and last_age > 30:
        findings.append({"id": "quiet", "level": "bad", "title": f"You haven't posted in {int(last_age)} days",
                         "detail": "YouTube tests new uploads with your audience first. A channel that goes quiet "
                                   "loses that test, and the next upload starts colder."})
    elif median_gap is not None and median_gap > 10:
        pace = f" Breakout channels in your niche post about {peer_pace:.0f} a month." if peer_pace else ""
        findings.append({"id": "rhythm", "level": "warn",
                         "title": f"Your uploads are about {median_gap:.0f} days apart",
                         "detail": "Most small channels break out only after a steady run of uploads." + pace})
    elif median_gap is not None:
        findings.append({"id": "rhythm", "level": "good", "title": "You upload steadily",
                         "detail": f"About every {max(1, round(median_gap))} days. Keep that rhythm."})

    if peer_views:
        ratio = median_views / peer_views if peer_views else 0
        detail = (f"Your usual video gets {_fmt(median_views)} views. Small channels breaking out in your niche "
                  f"get about {_fmt(peer_views)} per recent video, with similar subscriber counts.")
        if ratio < 0.1:
            findings.append({"id": "gap", "level": "bad", "title": "The same niche gets 10× more views elsewhere",
                             "detail": detail + " The topic works: the packaging (idea, title, thumbnail, first 30 "
                                                "seconds) is where the gap is."})
        elif ratio < 0.5:
            findings.append({"id": "gap", "level": "warn", "title": "You're behind the channels breaking out",
                             "detail": detail})
        else:
            findings.append({"id": "gap", "level": "good", "title": "You're on par with the breakout channels",
                             "detail": detail})

    best = max(long_videos, key=lambda v: v["views"])
    if median_views > 0 and best["views"] >= 3 * median_views:
        findings.append({"id": "hit", "level": "good",
                         "title": f"One video did {best['views'] / median_views:.0f}× your usual",
                         "detail": f"“{best['title']}” got {_fmt(best['views'])} views. Your audience told you what "
                                   "they want: make the next videos around that idea.",
                         "video": {"title": best["title"], "url": best["url"], "thumbnail": best["thumbnail"],
                                   "views": best["views"]}})

    recent, older = [v["views"] for v in videos[:5]], [v["views"] for v in videos[5:10]]
    if len(older) >= 3 and statistics.median(older) > 0:      # medians: one hit video isn't a trend
        change = statistics.median(recent) / statistics.median(older) - 1
        if change <= -0.3:
            findings.append({"id": "trend", "level": "warn", "title": f"Views are down {abs(change):.0%} lately",
                             "detail": "Your last 5 videos got fewer views than the 5 before. Compare their topics "
                                       "and thumbnails with the ones that worked."})
        elif change >= 0.3:
            findings.append({"id": "trend", "level": "good", "title": f"Views are up {change:.0%} lately",
                             "detail": "Your last 5 videos beat the 5 before. Whatever changed, keep doing it."})

    if 0.25 <= shorts_share <= 0.75:
        findings.append({"id": "mix", "level": "warn", "title": "Half Shorts, half long videos",
                         "detail": "Shorts viewers and long-video viewers rarely overlap. Pick the format your best "
                                   "videos use and give it most of your uploads."})

    long_titles = [v for v in videos if len(v["title"]) > 70]
    if len(long_titles) >= max(2, len(videos) // 3):
        findings.append({"id": "titles", "level": "warn", "title": "Many titles get cut off on phones",
                         "detail": "Phones show about 60 to 70 characters. Put the hook in the first few words."})

    order = {"bad": 0, "warn": 1, "good": 2}
    findings.sort(key=lambda f: order[f["level"]])
    return {"findings": findings, "stats": {
        "median_views": int(median_views), "peer_views": int(peer_views) if peer_views else None,
        "median_gap_days": round(median_gap, 1) if median_gap is not None else None,
        "last_upload_days": int(last_age) if last_age is not None else None,
        "shorts_share": round(shorts_share, 2), "best": {"title": best["title"], "views": best["views"]}}}


def plan(findings: list[dict], niche: dict | None) -> list[str]:
    """A step-by-step plan from the findings (the paid ideas add to it; this works without them)."""
    ids = {f["id"]: f for f in findings}
    steps = []
    if "quiet" in ids or ("rhythm" in ids and ids["rhythm"]["level"] != "good"):
        steps.append("Set a rhythm you can keep for 8 weeks (one video a week is enough) and batch-make two before "
                     "you publish the first.")
    if "hit" in ids:
        steps.append(f"Make three videos around the idea behind “{ids['hit'].get('video', {}).get('title', 'your best video')}”.")
    if "gap" in ids and ids["gap"]["level"] != "good":
        steps.append("Study the last three videos of each breakout channel below: the idea, the title, the thumbnail "
                     "and the first 30 seconds. Copy the shape, not the content.")
    if "mix" in ids:
        steps.append("Pick one format (Shorts or long videos) for the next two months.")
    if "titles" in ids:
        steps.append("Rewrite your next titles so the hook is in the first five words.")
    steps.append("Make each video in one consistent look so viewers recognise you in the feed"
                 + (f": {niche['look'].lower()}." if niche else "."))
    steps.append("After 10 uploads, keep what beat your average and drop what didn't.")
    return steps[:6]


# ---- storage, taste and full result -------------------------------------------------------------------------

def _uncached_today() -> int:
    with db._conn() as conn:
        return int(conn.execute(db._q("SELECT COUNT(*) AS n FROM channel_checks WHERE created_at>?"),
                                (time.time() - 86400,)).fetchone()["n"])


def check(url: str, *, api_key: str, daily_cap: int = 2000, fetcher=None, now: float | None = None) -> dict:
    """Read (or reuse) a channel and work out its result: {id, result}."""
    initialize()
    now = now or time.time()
    fetcher = fetcher or fetch
    if not api_key:
        raise CheckError("Channel checks aren't available right now. Try the niche quiz instead.")
    m = re.search(r"/channel/(UC[\w-]{20,})", url or "")
    if m:                                                           # a known channel id: reuse without asking YouTube
        with db._conn() as conn:
            row = conn.execute(db._q("SELECT id, data_json FROM channel_checks WHERE channel_id=? AND created_at>? "
                                     "ORDER BY created_at DESC LIMIT 1"), (m.group(1), now - FRESH_S)).fetchone()
        if row:
            return {"id": row["id"], "result": json.loads(row["data_json"]), "cached": True}
    if _uncached_today() >= daily_cap:
        raise CheckError("Channel checks are busy today. Try again tomorrow, or take the niche quiz now.")
    channel = fetcher(url, api_key)
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT id, data_json FROM channel_checks WHERE channel_id=? AND created_at>? "
                                 "ORDER BY created_at DESC LIMIT 1"), (channel["channel_id"], now - FRESH_S)).fetchone()
    if row:
        return {"id": row["id"], "result": json.loads(row["data_json"]), "cached": True}
    niche = detect_niche(channel)
    peers = mn.proof_channels(niche) if niche else []
    found = diagnose(channel, peers, now=now)
    result = {"channel": channel, "niche": ({"id": niche["id"], "name": niche["name"], "pitch": niche["pitch"]}
                                           if niche else None),
              "peers": peers, **found, "plan": plan(found["findings"], niche),
              "board": mn.board(niche) if niche else [], "looks": mn.looks(niche) if niche else [],
              "angles": mn.angles(niche) if niche else [], "ypp_note": mn.YPP_NOTE}
    cid = "cc_" + secrets.token_urlsafe(8)
    with db._conn() as conn:
        conn.execute(db._q("INSERT INTO channel_checks (id, channel_id, data_json, created_at) VALUES (?,?,?,?)"),
                     (cid, channel["channel_id"], json.dumps(result), now))
    return {"id": cid, "result": result, "cached": False}


def load(cid: str) -> dict | None:
    if not isinstance(cid, str) or not re.fullmatch(r"cc_[A-Za-z0-9_-]{6,20}", cid):
        return None
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT data_json FROM channel_checks WHERE id=?"), (cid,)).fetchone()
    return json.loads(row["data_json"]) if row else None


def teaser(cid: str, result: dict) -> dict:
    """Before sign-in: their channel as a dashboard, their niche, and the single biggest finding."""
    ch = result["channel"]
    worst = result["findings"][0] if result["findings"] else None
    return {"id": cid, "gated": True,
            "channel": {k: ch[k] for k in ("name", "url", "avatar", "subscribers", "total_views", "video_count",
                                           "hidden_subscribers")},
            "videos": [{k: v[k] for k in ("title", "url", "thumbnail", "views", "short", "seconds", "published_at")}
                       for v in ch["videos"]],
            "niche": result["niche"], "headline": worst and {k: worst[k] for k in ("level", "title", "detail")},
            "more": max(0, len(result["findings"]) - 1), "peers_count": len(result["peers"]),
            "stats": result.get("stats", {}), "board": result.get("board", [])[:4]}


def public(cid: str, result: dict, user: dict | None, *, ideas=None) -> dict:
    if not user:
        return teaser(cid, result)
    got = ideas if ideas is not None else ideas_for(cid, result, user)
    return {**teaser(cid, result), "gated": False, "findings": result["findings"], "peers": result["peers"],
            "plan": result["plan"], "looks": result["looks"], "angles": result["angles"],
            "board": result.get("board", []), "ypp_note": result["ypp_note"],
            "ideas": got["ideas"], "ideas_source": got["source"]}


# ---- five video ideas with thumbnails (the one paid call) ----------------------------------------------------

_STOP = set("a an the of in on to for and or but is are was were be it its this that with without how why what when who "
             "your you my i we they he she his her their from at by as into than then just about after before do does did "
             "not no never every most more one two like if live lived would could really".split())


def _hook(title: str) -> str:
    """The two strongest words of a title, for the thumbnail's big text."""
    words = [w for w in re.sub(r"[^A-Za-z0-9' ]", " ", title).split() if w.lower() not in _STOP]
    return " ".join(words[:2]).upper()


def _fallback_ideas(result: dict) -> list[dict]:
    """The niche's own ideas when the model is off or capped: its four angles and one more of its titles."""
    niche = mn.BY_ID.get((result.get("niche") or {}).get("id", ""))
    if not niche:
        return []
    titles = [a["title"] for a in mn.angles(niche)]
    titles += [t for t in niche["titles"] if t not in titles][:5 - len(titles)]
    return [{"title": t, "thumbnail": f"One character mid-reaction in your usual look, big text “{_hook(t)}”."}
            for t in titles[:5]]


def _ask(result: dict, *, post=None) -> tuple[list[dict], int, int]:
    post = post or requests.post
    ch = result["channel"]
    ranked = sorted(ch["videos"], key=lambda v: v["views"], reverse=True)
    lines = lambda vs: "\n".join(f"- {v['title']} ({v['views']} views)" for v in vs)
    peer_titles = [v["title"] for p in result["peers"] for v in p.get("recent", []) if v.get("title")][:8]
    prompt = (f"A YouTube channel called \"{ch['name']}\""
              + (f" in the niche \"{result['niche']['name']}\"" if result.get("niche") else "")
              + f" with {ch['subscribers']} subscribers wants more views.\nIts best recent videos:\n{lines(ranked[:3])}\n"
              f"Its weakest recent videos:\n{lines(ranked[-3:])}\n"
              + ("Titles working right now for small channels in the same niche (tone only, don't copy):\n- "
                 + "\n- ".join(peer_titles) + "\n" if peer_titles else "")
              + "Suggest 5 next videos that build on what already worked for this channel. For each: a title under "
              "70 characters (specific, curiosity-driven, honest, no emojis, no ALL CAPS) and a thumbnail idea in one "
              'sentence (subject, emotion, at most 3 words of text). Return JSON: {"ideas": [{"title": "...", '
              '"thumbnail": "..."}]}')
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}
    res = post(f"{mn.GEMINI_BASE}/models/{mn.model()}:generateContent", headers={"x-goog-api-key": mn._key()},
               json=body, timeout=(10, 25))
    if res.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {res.status_code}")
    data = res.json()
    usage = data.get("usageMetadata") or {}
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", "".join(p.get("text", "") for p in parts if not p.get("thought")).strip())
    ideas = [{"title": re.sub(r"\s+", " ", str(i.get("title") or "")).strip(),
              "thumbnail": re.sub(r"\s+", " ", str(i.get("thumbnail") or "")).strip()}
             for i in (json.loads(text).get("ideas") or []) if isinstance(i, dict)]
    ideas = [i for i in ideas if 8 <= len(i["title"]) <= 100 and i["thumbnail"]][:5]
    if len(ideas) < 5:
        raise ValueError("fewer than five usable ideas")
    tokens_out = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    return ideas, int(usage.get("promptTokenCount") or 0), tokens_out


def ideas_for(cid: str, result: dict, user: dict, *, now: float | None = None, post=None) -> dict:
    initialize()
    now = now or time.time()
    channel_id, day = result["channel"]["channel_id"], mn._today(now)
    fallback = {"ideas": _fallback_ideas(result), "source": "catalogue"}
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT ideas_json FROM channel_check_ideas WHERE channel_id=? AND day=?"),
                           (channel_id, day)).fetchone()
    if row:
        return {"ideas": json.loads(row["ideas_json"]), "source": "model"}
    if not mn._key():
        return fallback
    mn.initialize()
    call = mn._reserve_call(user, "channel:" + channel_id, now)
    if not call:
        return fallback
    try:
        ideas, tin, tout = _ask(result, post=post)
        status, spent = "done", mn.cost(tin, tout)
    except Exception as e:                                          # noqa: BLE001 - the written ideas still work
        print(f"[channel-check] ideas for {channel_id} failed: {e}")
        ideas, tin, tout, status, spent = None, 0, 0, "failed", 0.0
    with db._conn() as conn:
        conn.execute(db._q("UPDATE money_niche_calls SET status=?, tokens_in=?, tokens_out=?, cost_usd=? WHERE id=?"),
                     (status, tin, tout, spent, call))
        if ideas:
            conn.execute(db._q("INSERT INTO channel_check_ideas (channel_id, day, ideas_json) VALUES (?,?,?) "
                               "ON CONFLICT(channel_id, day) DO NOTHING"), (channel_id, day, json.dumps(ideas)))
    return {"ideas": ideas, "source": "model"} if ideas else fallback
