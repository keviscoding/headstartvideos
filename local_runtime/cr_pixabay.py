"""Stock footage from Pixabay with the creator's own free key, for a Studio without a Pexels key.

Pexels paused issuing new API keys (a creator saw "New API key issuance is currently paused", 2026-11-03), so a new
creator's videos had no stock b-roll. Pixabay gives a free key with every account (pixabay.com/api/docs, read
2026-11-03: 100 requests per 60 s per key, searches cached for 24 hours, images downloaded rather than hotlinked,
videos and photos both searchable, content free to use in monetised videos).

install(engine) changes nothing when the creator has a Pexels key. Without one but with PIXABAY_API_KEY, the engine
gets a stand-in key and its one search function (_pexels_get, used for every stock video and photo search) is
answered from Pixabay, in the shape Pexels answers: {"videos": [{id, duration, video_files: [{file_type, link,
width, height}]}]} and {"photos": [{id, src: {large2x, large, original}}]}. Ids are prefixed "pixabay-" so they never
meet a Pexels id in the engine's used-clips registry. Nothing goes through ChannelRecipe.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
KEY_NAME = "PIXABAY_API_KEY"
STAND_IN = "pixabay"                                    # the engine's PEXELS_API_KEY while Pixabay answers
CACHE = HOME / ".cr-pixabay-cache"
CACHE_SECONDS = 24 * 3600                               # Pixabay's terms: cache requests for 24 hours
VIDEOS = "https://pixabay.com/api/videos/"
PHOTOS = "https://pixabay.com/api/"
SIZES = ("large", "medium", "small", "tiny")
CREDIT = "Stock footage and photos from Pixabay (pixabay.com)."


def key(env=None) -> str:
    """The creator's Pixabay key: the environment, then Studio's .env read fresh (Setup saves it there)."""
    env = os.environ if env is None else env
    found = str(env.get(KEY_NAME) or "").strip()
    if found:
        return found
    try:
        for line in (HOME / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            name, _, value = line.strip().partition("=")
            if name.strip() == KEY_NAME:
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _cached(url: str, params: dict, get, now: float) -> dict:
    ask = hashlib.sha256(json.dumps([url, sorted(params.items())]).encode()).hexdigest()[:24]
    path = CACHE / f"{ask}.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if now - float(saved.get("at") or 0) < CACHE_SECONDS:
            return saved["data"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    r = get(url, params={**params, "key": key()}, timeout=30)
    if r.status_code != 200:
        return {}
    data = r.json()
    try:
        CACHE.mkdir(exist_ok=True)
        path.write_text(json.dumps({"at": now, "data": data}), encoding="utf-8")
    except OSError:
        pass
    return data


def _ask(params: dict) -> dict:
    """Pexels' search parameters in Pixabay's words (query <= 100 characters, 3-200 a page)."""
    out = {"q": str(params.get("query") or "")[:100], "safesearch": "true",
           "per_page": max(3, min(200, int(params.get("per_page") or 15))), "page": max(1, int(params.get("page") or 1))}
    return out


def videos(hits: list, landscape: bool) -> dict:
    out = []
    for h in hits or []:
        files = []
        for size in SIZES:
            f = (h.get("videos") or {}).get(size) or {}
            if f.get("url") and f.get("width"):
                files.append({"file_type": "video/mp4", "link": f["url"], "width": f["width"], "height": f.get("height")})
        if not files or (landscape and all((f.get("height") or 0) > f["width"] for f in files)):
            continue
        out.append({"id": f"pixabay-{h.get('id')}", "duration": h.get("duration"), "video_files": files,
                    "url": h.get("pageURL", "")})
    return {"videos": out}


def photos(hits: list) -> dict:
    out = []
    for h in hits or []:
        big = h.get("largeImageURL") or h.get("webformatURL")
        if big:
            out.append({"id": f"pixabay-{h.get('id')}", "src": {"large2x": big, "large": h.get("webformatURL") or big,
                                                                "original": big}, "url": h.get("pageURL", "")})
    return {"photos": out}


def search(url: str, params: dict, *, get=None, now=None) -> dict:
    """The engine's _pexels_get(url, params), answered from Pixabay. {} on any failure (the engine skips stock)."""
    if get is None:
        import requests
        get = requests.get
    now = time.time() if now is None else now
    try:
        ask = _ask(params)
        landscape = str(params.get("orientation") or "") == "landscape"
        if "/videos/" in str(url):
            return videos(_cached(VIDEOS, ask, get, now).get("hits"), landscape)
        if landscape:
            ask["orientation"] = "horizontal"
        return photos(_cached(PHOTOS, {**ask, "image_type": "photo"}, get, now).get("hits"))
    except Exception:                                   # noqa: BLE001 - stock is optional: the video goes on
        return {}


def install(engine) -> bool:
    """Answer the engine's stock searches from Pixabay when it has no Pexels key and the creator has a Pixabay key."""
    if getattr(engine, "PEXELS_API_KEY", "x") or not key():
        return False
    engine.PEXELS_API_KEY = STAND_IN
    engine._pexels_get = search
    return True
