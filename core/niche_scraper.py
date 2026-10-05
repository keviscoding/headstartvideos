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
DEFAULT_SCROLL_COUNT = 80
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
    compact = re.search(r"(\d+)\s*(mo|[hdwmy])\s*ago", t)
    if compact:
        return float(compact[1]) * {
            "h": 1 / 24, "d": 1, "w": 7, "m": 1 / 1440, "mo": 30, "y": 365,
        }[compact[2]]
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


def _search_url(keyword: str, *, use_duration_filter: bool = True) -> str:
    # Prefer long videos via YouTube filter chip encoded in sp=
    # EgIYAg == "Long" duration filter (commonly used; DOM still filtered).
    q = quote_plus(keyword)
    url = f"https://www.youtube.com/results?search_query={q}"
    return url + "&sp=EgIYAg%253D%253D" if use_duration_filter else url


def _parse_search_cards(raw, keyword, max_age_days, min_duration_sec):
    hits = []
    for item in raw or []:
        title = item.get("title") or ""
        video_url = item.get("videoUrl") or ""
        channel_name = item.get("channelName") or ""
        channel_url = item.get("channelUrl") or ""
        if not title or not video_url:
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

        meta = [part.strip() for x in (item.get("meta") or []) for part in re.split(r"[•·]", x) if part.strip()]
        views_text = next((x for x in meta if "view" in x.lower()), "")
        if not views_text:
            views_text = next((x for x in meta if re.fullmatch(r"[\d,.]+\s*[KMBkmb]?", x)), "")
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

    return hits


def scrape_keyword_search(
    keyword: str,
    *,
    scroll_count: int = DEFAULT_SCROLL_COUNT,
    max_age_days: int = MAX_VIDEO_AGE_DAYS,
    min_duration_sec: int = MIN_DURATION_SEC,
    progress: ProgressCb | None = None,
    page=None,
    max_results: int = 0,
    deadline: float | None = None,
    use_duration_filter: bool = True,
) -> list[dict[str, Any]]:
    """Read public search cards; optionally reuse a browser and bound the work."""
    def _log(msg):
        if progress:
            progress(msg)

    if deadline is not None and time.monotonic() >= deadline:
        return []
    if page is None:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = browser.new_context(locale="en-US", viewport={"width": 1400, "height": 900})
                return scrape_keyword_search(
                    keyword, scroll_count=scroll_count, max_age_days=max_age_days,
                    min_duration_sec=min_duration_sec, progress=progress,
                    page=context.new_page(), max_results=max_results, deadline=deadline,
                    use_duration_filter=use_duration_filter,
                )
            finally:
                browser.close()

    _log(f"Opening search: {keyword}")
    timeout = min(30000, max(1, int((deadline - time.monotonic()) * 1000))) if deadline else 60000
    page.goto(_search_url(keyword, use_duration_filter=use_duration_filter), wait_until="domcontentloaded", timeout=timeout)
    page.wait_for_timeout(min(1500, max(0, int((deadline - time.monotonic()) * 1000))) if deadline else 2500)
    for label in ("Reject all", "Accept all"):
        btn = page.get_by_role("button", name=label, exact=True)
        if btn.count() and btn.first.is_visible():
            btn.first.click(timeout=2000)
            break

    raw = []
    last_count = 0
    stagnant = 0
    # Scroll until YouTube stops loading new cards (or hit the ceiling).
    max_scrolls = max(1 if max_results else 10, int(scroll_count))
    for i in range(1, max_scrolls + 1):
        if deadline is not None and time.monotonic() >= deadline:
            break
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        remaining_ms = int(max(0, deadline - time.monotonic()) * 1000) if deadline else 1600
        page.wait_for_timeout(min(int(SCROLL_DELAY_SEC * 1000), remaining_ms))
        raw = page.evaluate(
            r"""() => {
              const out = [];
              const items = document.querySelectorAll('ytd-video-renderer, yt-lockup-view-model');
              for (const video of items) {
                try {
                  const titleEl = video.querySelector('a#video-title')
                    || video.querySelector('h3 a')
                    || video.querySelector('a[href*="/watch?v="]');
                  const channelEl = video.querySelector('ytd-channel-name a')
                    || video.querySelector('a[href*="/@"]')
                    || video.querySelector('a[href*="/channel/"]');
                  const metaSpans = video.querySelectorAll('#metadata-line span, .inline-metadata-item');
                  const meta = Array.from(metaSpans).map(s => (s.textContent || '').trim()).filter(Boolean);
                  if (!meta.length) meta.push(...(video.innerText || '').split('\n'));
                  const timeMatch = (video.innerText || '').match(/\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b/);
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
                    durationText: (durEl?.textContent || '').trim() || (timeMatch ? timeMatch[0] : ''),
                    thumbnail: thumbEl?.src || '',
                  });
                } catch (e) {}
              }
              return out;
            }"""
        )
        count = len(raw)
        if max_results and len(_parse_search_cards(raw, keyword, max_age_days, min_duration_sec)) >= max_results:
            break
        if i == 1 or i % 5 == 0 or i == max_scrolls:
            _log(f"  scroll {i}/{max_scrolls} — {count} video cards")
        if count <= last_count:
            stagnant += 1
            if stagnant >= 4:
                _log(f"  reached end of results (~{count} cards)")
                break
        else:
            stagnant = 0
        last_count = count


    hits = _parse_search_cards(raw, keyword, max_age_days, min_duration_sec)
    if max_results:
        hits = hits[:max_results]
    _log(f"Kept {len(hits)} fresh long-form videos for {keyword!r}")
    return hits


def scrape_keywords(
    keywords: list[str],
    *,
    scroll_count: int = DEFAULT_SCROLL_COUNT,
    max_age_days: int = MAX_VIDEO_AGE_DAYS,
    progress: ProgressCb | None = None,
    max_per_keyword: int = 0,
    max_videos: int = 0,
    deadline: float | None = None,
    use_duration_filter: bool = True,
    reuse_browser: bool = True,
) -> list[dict[str, Any]]:
    """Reuse one browser per hunt, deduplicate, and stop at work/time budgets."""
    from contextlib import ExitStack

    kws = list(dict.fromkeys(str(k).strip() for k in keywords if str(k).strip())) or list(SCROLL_KEYWORDS)
    all_hits = []
    seen_vids = set()
    with ExitStack() as resources:
        page = None
        if reuse_browser and (deadline is None or time.monotonic() < deadline):
            from playwright.sync_api import sync_playwright
            p = resources.enter_context(sync_playwright())
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            resources.callback(browser.close)
            context = browser.new_context(locale="en-US", viewport={"width": 1400, "height": 900})
            page = context.new_page()
        for i, kw in enumerate(kws):
            if deadline is not None and time.monotonic() >= deadline:
                break
            if max_videos and len(all_hits) >= max_videos:
                break
            if progress:
                progress(f"Searching ({i + 1}/{len(kws)}): {kw}")
            try:
                batch = scrape_keyword_search(
                    kw, scroll_count=scroll_count, max_age_days=max_age_days,
                    progress=progress, page=page, max_results=max_per_keyword,
                    deadline=deadline, use_duration_filter=use_duration_filter,
                )
            except Exception as e:
                if progress:
                    progress(f"Search failed for {kw!r}: {type(e).__name__}")
                continue
            for hit in batch:
                vid = hit.get("video_id")
                if not vid or vid in seen_vids:
                    continue
                seen_vids.add(vid)
                all_hits.append(hit)
                if max_videos and len(all_hits) >= max_videos:
                    break
    return all_hits
