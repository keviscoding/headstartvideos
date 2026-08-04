"""Download ranking clips from TikTok / YouTube / Instagram URLs.

Prefer Apify when APIFY_TOKEN is set (more reliable for TikTok/IG),
else fall back to yt-dlp. Output is a local mp4 ready to stage to Spaces.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx

MAX_IMPORT_BYTES = 120 * 1024 * 1024
MIN_IMPORT_BYTES = 1000
YTDLP_TIMEOUT_S = 120
APIFY_TIMEOUT_S = 150

_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)


def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if re.search(r"tiktok\.com|vm\.tiktok|vt\.tiktok", u):
        return "tiktok"
    if re.search(r"youtube\.com|youtu\.be", u):
        return "youtube"
    if "instagram.com" in u:
        return "instagram"
    return "other"


def clean_import_url(raw_url: str, platform: str) -> str:
    try:
        u = urlparse(raw_url.strip())
        if platform == "tiktok":
            return f"{u.scheme}://{u.netloc}{u.path}"
        if platform == "youtube":
            qs = parse_qs(u.query)
            if qs.get("v"):
                return f"https://www.youtube.com/watch?v={qs['v'][0]}"
            if "youtu.be" in (u.netloc or "").lower():
                vid = (u.path or "/").lstrip("/").split("/")[0]
                if vid:
                    return f"https://www.youtube.com/watch?v={vid}"
            m = re.search(r"/shorts/([A-Za-z0-9_-]+)", u.path or "")
            if m:
                return f"https://www.youtube.com/watch?v={m.group(1)}"
        return raw_url.strip()
    except Exception:
        return raw_url.strip()


def parse_import_urls(raw: str) -> list[str]:
    found = re.findall(r"https?://[^\s<>\"']+", raw or "")
    out: list[str] = []
    seen: set[str] = set()
    for u in found:
        u = re.sub(r"[),.;]+$", "", u)
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def short_url_label(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.netloc or "").replace("www.", "")
        path = (p.path or "").rstrip("/")
        tail = path.split("/")[-1] if path else ""
        label = f"{host}/{tail}" if tail else host
        return label[:40] or "Imported clip"
    except Exception:
        return "Imported clip"


def _apify_token() -> str:
    try:
        import config as _cfg
        return (getattr(_cfg, "APIFY_TOKEN", "") or os.getenv("APIFY_TOKEN") or "").strip()
    except Exception:
        return (os.getenv("APIFY_TOKEN") or "").strip()


def _score_media_url(candidate: str, key_hint: str = "") -> int:
    if not isinstance(candidate, str) or not re.match(r"^https?://", candidate, re.I):
        return -1
    if re.search(r"\.(jpg|jpeg|png|webp|gif|svg)(\?|$)", candidate, re.I):
        return -1
    if (
        re.search(r"tiktok\.com/@|instagram\.com/(p|reel)|youtube\.com/watch", candidate, re.I)
        and not re.search(r"\.(mp4|m3u8|webm)", candidate, re.I)
        and "api.apify.com" not in candidate
    ):
        return -1
    score = 10
    hint = f"{key_hint} {candidate}"
    if re.search(r"no[_-]?wm|nowm|nwm|without.?watermark|NoWatermark", hint, re.I):
        score += 50
    if "api.apify.com/v2/key-value-stores" in candidate:
        score += 40
    if re.search(r"\.mp4(\?|$)", candidate, re.I) or "contentType=video" in candidate:
        score += 20
    if re.search(
        r"tiktokcdn|muscdn|byteoversea|ibyteimg|cdninstagram|googlevideo",
        candidate,
        re.I,
    ):
        score += 15
    if re.search(r"watermark|wm=1|with_watermark", hint, re.I) and not re.search(
        r"no[_-]?wm|nowm|nwm", hint, re.I
    ):
        score -= 30
    return score


def pick_best_media_url(item: Any) -> tuple[str | None, bool]:
    best: str | None = None
    best_score = 0
    best_hint = ""

    def consider(candidate: Any, key_hint: str = "") -> None:
        nonlocal best, best_score, best_hint
        if not isinstance(candidate, str):
            return
        s = _score_media_url(candidate, key_hint)
        if s > best_score:
            best_score = s
            best = candidate
            best_hint = key_hint

    def walk(obj: Any, depth: int, key_hint: str = "") -> None:
        if obj is None or depth > 6:
            return
        if isinstance(obj, str):
            consider(obj, key_hint)
            return
        if isinstance(obj, list):
            for i, child in enumerate(obj[:40]):
                walk(child, depth + 1, key_hint)
            return
        if isinstance(obj, dict):
            for key, val in list(obj.items())[:50]:
                if re.search(r"url|addr|play|download|media|video|mp4|nwm|watermark|storage", str(key), re.I):
                    walk(val, depth + 1, str(key))

    walk(item, 0)
    if not best:
        return None, False
    wm_free = bool(re.search(r"no[_-]?wm|nowm|nwm|without.?watermark|NoWatermark", f"{best_hint} {best}", re.I))
    return best, wm_free


def _download_file(url: str, out_path: Path, *, referer: str | None = None) -> None:
    headers = {"User-Agent": _UA}
    if referer:
        headers["Referer"] = referer
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        total = 0
        with out_path.open("wb") as f:
            for chunk in resp.iter_bytes(65536):
                total += len(chunk)
                if total > MAX_IMPORT_BYTES:
                    raise RuntimeError("Downloaded video too large (over 120MB).")
                f.write(chunk)


def _run_apify_sync(token: str, actor_id: str, input_data: dict, timeout_ms: int = 150000) -> list:
    timeout_s = max(30, timeout_ms // 1000)
    url = (
        f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
        f"?token={token}&timeout={timeout_s}"
    )
    with httpx.Client(timeout=timeout_s + 20) as client:
        resp = client.post(url, json=input_data)
        resp.raise_for_status()
        items = resp.json()
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"{actor_id} returned no dataset items")
    return items


def _run_apify_wait(token: str, actor_id: str, input_data: dict, timeout_ms: int = 150000) -> dict:
    with httpx.Client(timeout=30.0) as client:
        start = client.post(
            f"https://api.apify.com/v2/acts/{actor_id}/runs?token={token}",
            json=input_data,
        )
        start.raise_for_status()
        run = (start.json() or {}).get("data") or {}
        run_id = run.get("id")
        if not run_id:
            raise RuntimeError(f"{actor_id} failed to start")

        deadline = time.time() + (timeout_ms / 1000)
        status = run.get("status")
        while time.time() < deadline:
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
            time.sleep(4)
            st = client.get(f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}")
            st.raise_for_status()
            run = (st.json() or {}).get("data") or {}
            status = run.get("status")
        if status != "SUCCEEDED":
            raise RuntimeError(f"{actor_id} ended with status {status}")

        items: list = []
        dataset_id = run.get("defaultDatasetId")
        if dataset_id:
            ds = client.get(f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}")
            ds.raise_for_status()
            items = ds.json() if isinstance(ds.json(), list) else []
        return {"items": items, "run": run}


def _try_apify_kv_video(token: str, run_meta: dict) -> str | None:
    kv_id = (run_meta or {}).get("defaultKeyValueStoreId")
    if not kv_id:
        return None
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(f"https://api.apify.com/v2/key-value-stores/{kv_id}/keys?token={token}")
        resp.raise_for_status()
        keys = ((resp.json() or {}).get("data") or {}).get("items") or []
    for k in keys:
        key = k.get("key") or ""
        ctype = k.get("contentType") or ""
        if re.search(r"\.mp4$", key, re.I) or re.search(r"video", ctype, re.I):
            return (
                f"https://api.apify.com/v2/key-value-stores/{kv_id}/records/"
                f"{quote(key, safe='')}?token={token}"
            )
    return None


def _download_via_apify(url: str, out_path: Path, platform: str) -> dict:
    token = _apify_token()
    if not token:
        raise RuntimeError("APIFY_TOKEN not configured")

    if platform == "tiktok":
        plans = [
            {
                "id": "clockworks~tiktok-video-scraper",
                "prefer_kv": True,
                "input": {
                    "postURLs": [url],
                    "shouldDownloadVideos": True,
                    "shouldDownloadCovers": False,
                    "shouldDownloadSubtitles": False,
                    "shouldDownloadSlideshowImages": False,
                },
            },
            {
                "id": "thenetaji~tiktok-video-downloader",
                "prefer_kv": False,
                "input": {
                    "urls": [{"url": url}],
                    "startUrls": [{"url": url}],
                    "quality": "best",
                    "format": "mp4",
                    "proxyConfiguration": {
                        "useApifyProxy": True,
                        "apifyProxyGroups": ["RESIDENTIAL"],
                    },
                },
            },
        ]
        last_err = "unknown"
        for plan in plans:
            try:
                media_url = None
                wm_free = False
                items: list = []
                run_meta = None
                if plan["prefer_kv"]:
                    waited = _run_apify_wait(token, plan["id"], plan["input"], APIFY_TIMEOUT_S * 1000)
                    items = waited.get("items") or []
                    run_meta = waited.get("run")
                    media_url = _try_apify_kv_video(token, run_meta or {})
                    if media_url:
                        wm_free = True
                else:
                    items = _run_apify_sync(token, plan["id"], plan["input"], APIFY_TIMEOUT_S * 1000)
                if not media_url and items:
                    media_url, wm_free = pick_best_media_url(items[0])
                if not media_url and run_meta:
                    media_url = _try_apify_kv_video(token, run_meta)
                if not media_url:
                    raise RuntimeError("no media URL in response")
                _download_file(media_url, out_path, referer="https://www.tiktok.com/")
                return {
                    "ok": True,
                    "platform": "tiktok",
                    "watermark_free": wm_free,
                    "source": f"apify:{plan['id']}",
                }
            except Exception as e:
                last_err = str(e)
                continue
        raise RuntimeError(last_err)

    if platform == "youtube":
        items = _run_apify_sync(
            token,
            "streamers~youtube-video-downloader",
            {"startUrls": [{"url": url}], "urls": [url]},
            APIFY_TIMEOUT_S * 1000,
        )
        media_url, wm_free = pick_best_media_url(items[0] if items else {})
        if not media_url:
            raise RuntimeError("no media URL from YouTube Apify actor")
        _download_file(media_url, out_path, referer="https://www.youtube.com/")
        return {
            "ok": True,
            "platform": "youtube",
            "watermark_free": True if wm_free is None else wm_free,
            "source": "apify:streamers~youtube-video-downloader",
        }

    if platform == "instagram":
        items = _run_apify_sync(
            token,
            "apify~instagram-scraper",
            {
                "directUrls": [url],
                "resultsType": "posts",
                "resultsLimit": 1,
                "addParentData": False,
            },
            APIFY_TIMEOUT_S * 1000,
        )
        media_url, _wm = pick_best_media_url(items[0] if items else {})
        if not media_url:
            raise RuntimeError("no media URL from Instagram Apify actor")
        _download_file(media_url, out_path, referer="https://www.instagram.com/")
        return {
            "ok": True,
            "platform": "instagram",
            "watermark_free": True,
            "source": "apify:apify~instagram-scraper",
        }

    raise RuntimeError(f"Apify not configured for platform {platform}")


def _download_via_ytdlp(url: str, out_path: Path, platform: str) -> dict:
    tmpl = str(out_path.with_suffix("")) + ".%(ext)s"
    # Clean any prior partials for this stem
    for p in out_path.parent.glob(out_path.stem + ".*"):
        if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".m4a", ".part"):
            try:
                p.unlink()
            except OSError:
                pass

    args = [
        "yt-dlp",
        url,
        "-o", tmpl,
        "-f",
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--no-check-certificates",
        "--no-warnings",
        "--socket-timeout", "30",
        "--user-agent", _UA,
    ]
    if platform == "youtube":
        args.extend(["--extractor-args", "youtube:player_client=ios,mweb"])
        cookies = (os.getenv("YOUTUBE_COOKIES_FILE") or "").strip()
        if cookies and Path(cookies).is_file():
            args.extend(["--cookies", cookies])
    if platform == "tiktok":
        args.extend([
            "--referer", "https://www.tiktok.com/",
            "--add-header", "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        ])

    try:
        subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
            timeout=YTDLP_TIMEOUT_S,
        )
    except FileNotFoundError as e:
        raise RuntimeError("yt-dlp is not installed on this server") from e
    except subprocess.CalledProcessError as e:
        err = ((e.stderr or e.stdout or "")[-300:]).strip() or "Download failed"
        raise RuntimeError(err) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Download timed out — try uploading the file instead.") from e

    candidates = sorted(
        out_path.parent.glob(out_path.stem + ".*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for c in candidates:
        if c.suffix.lower() in (".mp4", ".webm", ".mkv") and c.stat().st_size > MIN_IMPORT_BYTES:
            if c != out_path:
                if out_path.exists():
                    out_path.unlink()
                c.rename(out_path)
            return {
                "ok": True,
                "platform": platform,
                "watermark_free": True if platform == "youtube" else (False if platform == "tiktok" else None),
                "source": "yt-dlp",
            }
    raise RuntimeError("Download produced no video file")


def import_ranking_url(url: str, out_path: Path) -> dict:
    """Download `url` to `out_path` (mp4). Returns meta dict with platform/source."""
    raw = (url or "").strip()
    if not re.match(r"^https?://.+", raw, re.I):
        raise ValueError("Invalid URL")

    platform = detect_platform(raw)
    clean = clean_import_url(raw, platform)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    meta: dict[str, Any] = {"platform": platform, "watermark_free": None, "source": None}
    has_apify = bool(_apify_token())
    downloaded = False

    if platform in ("tiktok", "youtube", "instagram") and has_apify:
        try:
            result = _download_via_apify(clean, out_path, platform)
            if result.get("ok") and out_path.exists():
                downloaded = True
                meta.update({
                    "platform": result.get("platform") or platform,
                    "watermark_free": result.get("watermark_free"),
                    "source": result.get("source") or "apify",
                })
        except Exception as e:
            meta["apify_error"] = str(e)[:200]
            downloaded = False

    if not downloaded:
        try:
            result = _download_via_ytdlp(clean, out_path, platform)
            meta.update({
                "platform": result.get("platform") or platform,
                "watermark_free": result.get("watermark_free"),
                "source": result.get("source") or "yt-dlp",
            })
            downloaded = True
        except Exception as e:
            friendly = str(e)
            if not has_apify and platform in ("tiktok", "instagram"):
                friendly = (
                    f"Server import needs APIFY_TOKEN for reliable {platform} downloads. "
                    "Set it on the web app env, or download the video and upload the file."
                )
            elif re.search(r"Video not available|Unavailable|Private|login|403|unsupported|Instagram", friendly, re.I):
                friendly = (
                    f"Could not import this {platform} link (removed, private, or blocked). "
                    "Download it on your phone and upload the file."
                )
            elif len(friendly) > 180:
                friendly = friendly[:180]
            raise RuntimeError(friendly) from e

    if not out_path.exists():
        raise RuntimeError("Download failed — no output file")
    size = out_path.stat().st_size
    if size > MAX_IMPORT_BYTES:
        out_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded video too large (over 120MB). Try a shorter clip.")
    if size < MIN_IMPORT_BYTES:
        out_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file was empty. Try uploading the video directly.")

    meta["ok"] = True
    meta["bytes"] = size
    return meta
