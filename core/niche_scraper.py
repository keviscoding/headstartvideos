"""
YouTube search scroll scraper — ViewHunt-style discovery.

Opens real youtube.com/results pages, scrolls like an operator, scrapes
long-form result cards from the DOM. Does NOT use youtube.search().list
for discovery ranking (that waters down what YouTube actually surfaces).

Hard rules:
  - Ignore any result video older than MAX_VIDEO_AGE_DAYS (default 180 / 6 months)
  - Skip Shorts / reel tiles
  - Never touches cook jobs / video-creation workers
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote_plus

ProgressCb = Callable[[str], None]

MAX_VIDEO_AGE_DAYS = 180  # 6 months — drop legacy music / dead virals
DEFAULT_SCROLL_COUNT = 20
SCROLL_DELAY_SEC = 1.6
MIN_DURATION_SEC = 240  # long-form only (≥ 4 min)

# Broad probes that work in manual YouTube tests (adjectives / glue words)
# mixed with niche phrases — not an exclusive list.
SCROLL_KEYWORDS = [
    "worse",
    "is",
    "why",
    "how",
    "never",
    "always",
    "secret",
    "forbidden",
    "untold",
    "what happened to",
    "history documentary explained",
    "true story folktale",
    "forbidden history mysteries",
    "dark history facts",
    "sci fi stories HFY",
    "christian prayer night",
    "HOA revenge story",
    "personal finance explained",
    "stoic habits self improvement",
    "war documentary full movie",
    "reddit story narrated",
    "true crime documentary",
    "bible prophecy explained",
    "ancient civilization mystery",
    "horror story narrated long",
]


def parse_views(text: str) -> int:
    if not text:
        return 0
    t = text.lower().replace(",", "").replace("views", "").replace("view", "").strip()
    m = re.search(r"([\d.]+)\s*([kmb])?", t)
    if not m:
        digits = re.sub(r"[^\d]", "", t)
        return int(digits) if digits else 0
    n = float(m.group(1))
    suf = (m.group(2) or "").lower()
    if suf == "k":
        n *= 1_000
    elif suf == "m":
        n *= 1_000_000
    elif suf == "b":
        n *= 1_000_000_000
    return int(n)


def parse_duration_badge(text: str) -> int:
    """HH:MM:SS or MM:SS → seconds."""
    if not text:
        return 0
    parts = [p for p in re.findall(r"\d+", text.strip())]
    if not parts:
        return 0
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 1:
        return parts[0]
    return 0


def parse_relative_age_days(text: str) -> float | None:
    """
    Parse YouTube relative dates: '3 days ago', '2 months ago', '1 year ago', 'Streamed 5 hours ago'.
    Returns age in days, or None if unparseable.
    """
    if not text:
        return None
    t = text.lower().strip()
    t = re.sub(r"^(streamed|premiered)\s+", "", t)
    if "just now" in t or "second" in t or "minute" in t or "hour" in t:
        return 0.0
    m = re.search(r"(\d+)\s*(day|week|month|year)s?\s*ago", t)
    if not m:
        if "yesterday" in t:
            return 1.0
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "day":
        return float(n)
    if unit == "week":
        return float(n * 7)
    if unit == "month":
        return float(n * 30)
    if unit == "year":
        return float(n * 365)
    return None


def _channel_id_from_url(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"/channel/(UC[\w-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/@([\w.-]+)", url)
    if m:
        return "@" + m.group(1)
    return url.rstrip("/").split("/")[-1]


def _search_url(keyword: str) -> str:
    # Prefer long videos via YouTube filter chip encoded in sp=
    # EgIYAg == "Long" duration filter (commonly used; DOM still filtered).
    q = quote_plus(keyword)
    return f"https://www.youtube.com/results?search_query={q}&sp=EgIYAg%253D%253D"


def scrape_keyword_search(
    keyword: str,
    *,
    scroll_count: int = DEFAULT_SCROLL_COUNT,
    max_age_days: int = MAX_VIDEO_AGE_DAYS,
    min_duration_sec: int = MIN_DURATION_SEC,
    progress: ProgressCb | None = None,
) -> list[dict[str, Any]]:
    """
    Scroll one YouTube search page and return fresh long-form video hits.
    Each hit: video_id, title, channel_name, channel_url, channel_id,
              view_count, duration_sec, age_days, thumbnail, published_label
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is required for niche scroll discovery. "
            "Install: pip install playwright && playwright install chromium"
        ) from e

    def _log(msg: str) -> None:
        print(f"[niche_scraper] {msg}")
        if progress:
            progress(msg)

    url = _search_url(keyword)
    hits: list[dict[str, Any]] = []
    _log(f"Opening search: {keyword}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2500)

            # Consent / cookie banners (best-effort)
            for sel in (
                "button:has-text('Accept all')",
                "button:has-text('Accept')",
                "button:has-text('I agree')",
                "tp-yt-paper-button:has-text('Accept')",
            ):
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=800):
                        btn.click(timeout=1500)
                        page.wait_for_timeout(800)
                        break
                except Exception:
                    pass

            last_count = 0
            stagnant = 0
            max_scrolls = max(3, int(scroll_count))
            for i in range(1, max_scrolls + 1):
                page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                page.wait_for_timeout(int(SCROLL_DELAY_SEC * 1000))
                count = page.locator("ytd-video-renderer").count()
                if i == 1 or i % 5 == 0 or i == max_scrolls:
                    _log(f"  scroll {i}/{max_scrolls} — {count} video cards")
                if count <= last_count:
                    stagnant += 1
                    if stagnant >= 3:
                        _log(f"  reached end of results (~{count} cards)")
                        break
                else:
                    stagnant = 0
                last_count = count

            raw = page.evaluate(
                """() => {
                  const out = [];
                  const items = document.querySelectorAll('ytd-video-renderer');
                  for (const video of items) {
                    try {
                      const titleEl = video.querySelector('a#video-title')
                        || video.querySelector('h3 a')
                        || video.querySelector('a[title]');
                      const channelEl = video.querySelector('ytd-channel-name a')
                        || video.querySelector('a[href*="/@"]')
                        || video.querySelector('a[href*="/channel/"]');
                      const metaSpans = video.querySelectorAll('#metadata-line span, .inline-metadata-item');
                      const meta = Array.from(metaSpans).map(s => (s.textContent || '').trim()).filter(Boolean);
                      const durEl = video.querySelector('ytd-thumbnail-overlay-time-status-renderer span, #time-status span, span.ytd-thumbnail-overlay-time-status-renderer');
                      const thumbEl = video.querySelector('img');
                      const href = titleEl?.href || '';
                      if (!href || href.includes('/shorts/')) continue;
                      out.push({
                        title: (titleEl?.title || titleEl?.textContent || '').trim(),
                        videoUrl: href,
                        channelName: (channelEl?.textContent || '').trim(),
                        channelUrl: channelEl?.href || '',
                        meta,
                        durationText: (durEl?.textContent || '').trim(),
                        thumbnail: thumbEl?.src || '',
                      });
                    } catch (e) {}
                  }
                  return out;
                }"""
            )
        finally:
            context.close()
            browser.close()

    for item in raw or []:
        title = item.get("title") or ""
        video_url = item.get("videoUrl") or ""
        channel_name = item.get("channelName") or ""
        channel_url = item.get("channelUrl") or ""
        if not title or not video_url or not channel_name:
            continue

        m = re.search(r"[?&]v=([\w-]{11})", video_url)
        if not m:
            m = re.search(r"/shorts/([\w-]{11})", video_url)
            if m:
                continue  # shorts
            continue
        video_id = m.group(1)

        duration_sec = parse_duration_badge(item.get("durationText") or "")
        if duration_sec and duration_sec < min_duration_sec:
            continue

        meta = item.get("meta") or []
        views_text = next((x for x in meta if "view" in x.lower()), "")
        age_text = next(
            (x for x in meta if "ago" in x.lower() or "yesterday" in x.lower()),
            "",
        )
        age_days = parse_relative_age_days(age_text)
        if age_days is None:
            # No date → skip (can't prove freshness)
            continue
        if age_days > max_age_days:
            continue

        hits.append(
            {
                "video_id": video_id,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "channel_name": channel_name,
                "channel_url": channel_url,
                "channel_id": _channel_id_from_url(channel_url),
                "view_count": parse_views(views_text),
                "duration_sec": duration_sec,
                "age_days": age_days,
                "published_label": age_text,
                "thumbnail": item.get("thumbnail") or "",
                "source_keyword": keyword,
            }
        )

    _log(f"  kept {len(hits)} fresh long-form hits (≤{max_age_days}d) for '{keyword}'")
    return hits


def scrape_keywords(
    keywords: list[str],
    *,
    scroll_count: int = DEFAULT_SCROLL_COUNT,
    max_age_days: int = MAX_VIDEO_AGE_DAYS,
    progress: ProgressCb | None = None,
) -> list[dict[str, Any]]:
    """Scroll-scrape many keywords; return deduped video hits."""
    kws = [k.strip() for k in keywords if k and str(k).strip()]
    if not kws:
        kws = list(SCROLL_KEYWORDS)
    all_hits: list[dict[str, Any]] = []
    seen_vids: set[str] = set()
    for i, kw in enumerate(kws):
        if progress:
            progress(f"Scrolling search ({i + 1}/{len(kws)}): {kw}")
        try:
            batch = scrape_keyword_search(
                kw,
                scroll_count=scroll_count,
                max_age_days=max_age_days,
                progress=progress,
            )
        except Exception as e:
            print(f"[niche_scraper] keyword '{kw}' failed: {e}")
            if progress:
                progress(f"Search failed for '{kw}': {e}")
            continue
        for h in batch:
            vid = h.get("video_id")
            if not vid or vid in seen_vids:
                continue
            seen_vids.add(vid)
            all_hits.append(h)
        time.sleep(0.8)
    return all_hits
