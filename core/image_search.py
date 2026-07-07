"""
Multi-source image search: Wikimedia Commons + Pexels.
All searches run in parallel via asyncio + httpx.

Architecture: ALWAYS search both sources for every slot. The source_hint
only affects ranking, never search filtering. This prevents the system
from returning nothing when one source has zero results for a query.
"""

from __future__ import annotations
import asyncio
import httpx
from dataclasses import dataclass, field
from config import WIKIMEDIA_API, WIKIMEDIA_USER_AGENT, PEXELS_API, PEXELS_KEY


@dataclass
class ImageResult:
    url: str
    thumb_url: str
    width: int
    height: int
    source: str
    title: str = ""
    license: str = ""
    score: float = 0.0


async def search_wikimedia(
    client: httpx.AsyncClient, query: str, limit: int = 8
) -> list[ImageResult]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"File: {query}",
        "gsrlimit": str(limit),
        "gsrnamespace": "6",
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "1920",
    }
    try:
        resp = await client.get(WIKIMEDIA_API, params=params, timeout=12)
        if resp.status_code != 200:
            print(f"  [wikimedia] HTTP {resp.status_code} for query: {query[:50]}")
            return []
        data = resp.json()
    except Exception as e:
        print(f"  [wikimedia] Error for query '{query[:50]}': {e}")
        return []

    results: list[ImageResult] = []
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        mime = info.get("mime", "")
        if not mime.startswith("image/"):
            continue
        results.append(ImageResult(
            url=info.get("url", ""),
            thumb_url=info.get("thumburl") or info.get("url", ""),
            width=info.get("width", 0),
            height=info.get("height", 0),
            source="wikimedia",
            title=page.get("title", ""),
            license=(info.get("extmetadata") or {})
                .get("LicenseShortName", {}).get("value", ""),
        ))
    return results


async def search_pexels(
    client: httpx.AsyncClient, query: str, limit: int = 8,
    color: str | None = None,
) -> list[ImageResult]:
    if not PEXELS_KEY:
        return []
    headers = {"Authorization": PEXELS_KEY}
    params = {
        "query": query,
        "per_page": str(limit),
        "orientation": "landscape",
    }
    if color:
        params["color"] = color
    try:
        resp = await client.get(
            PEXELS_API, params=params, headers=headers, timeout=12
        )
        data = resp.json()
    except Exception:
        return []

    results: list[ImageResult] = []
    for photo in data.get("photos", []):
        src = photo.get("src", {})
        results.append(ImageResult(
            url=src.get("original", ""),
            thumb_url=src.get("large2x") or src.get("large", ""),
            width=photo.get("width", 0),
            height=photo.get("height", 0),
            source="pexels",
            title=photo.get("alt", ""),
        ))
    return results


async def search_images(
    query: str,
    source_hint: str = "any",
    fallback_query: str | None = None,
) -> list[ImageResult]:
    async with httpx.AsyncClient(
        headers={"User-Agent": WIKIMEDIA_USER_AGENT},
        verify=True,
    ) as client:
        # Always search both sources
        tasks = [
            search_wikimedia(client, query),
            search_pexels(client, query),
        ]
        all_results = await asyncio.gather(*tasks)
        merged = [r for batch in all_results for r in batch]

        if len(merged) < 2 and fallback_query:
            fb_tasks = [
                search_pexels(client, fallback_query),
                search_wikimedia(client, fallback_query),
            ]
            fallback_results = await asyncio.gather(*fb_tasks)
            merged.extend(r for batch in fallback_results for r in batch)

        return merged


STYLE_TO_PEXELS_COLOR = {
    "cinematic_dark": "black",
    "historical_bw": "black",
    "modern_color": None,
    "neutral": None,
}


def _build_wikimedia_queries(slot: dict) -> list[str]:
    """
    Build SHORT queries optimized for Wikimedia's basic search engine.
    Wikimedia works best with 2-5 word queries -- entity names, specific nouns.
    Also includes action/context words to distinguish between different photos
    of the same person (e.g., "Kennedy Congress" vs just "Kennedy").
    """
    subject = slot.get("subject", "").strip()
    era = slot.get("era", "").strip()
    entity_type = slot.get("entity_type", "mood")

    queries: list[str] = []

    if entity_type == "person":
        name_words = []
        context_words = []
        for w in subject.split():
            if w[0].isupper() if w else False:
                name_words.append(w)
            elif len(w) >= 4:
                context_words.append(w)

        if len(name_words) >= 2:
            # Name only (broadest)
            queries.append(" ".join(name_words[:2]))
            # Name + best context word (e.g., "Kennedy Congress")
            if context_words:
                queries.append(" ".join(name_words[:2]) + " " + context_words[0])
            # Name + era
            if era:
                queries.append(" ".join(name_words[:2]) + " " + era)

    if subject:
        words = subject.split()[:5]
        short_subject = " ".join(words)
        if short_subject not in queries:
            queries.append(short_subject)

    if subject and era:
        short = " ".join(subject.split()[:3]) + " " + era
        if short not in queries:
            queries.append(short)

    full_q = slot.get("query", "").strip()
    if full_q:
        trimmed = " ".join(full_q.split()[:6])
        if trimmed not in queries:
            queries.append(trimmed)

    return queries


def _build_pexels_queries(slot: dict) -> list[str]:
    """
    Build queries optimized for Pexels. Pexels handles longer, more
    descriptive queries and is better for concepts, moods, and generic visuals.
    """
    full_query = slot.get("query", "").strip()
    subject = slot.get("subject", "").strip()
    era = slot.get("era", "").strip()
    tone = slot.get("tone", "").strip()
    fallback = slot.get("fallback_query", "").strip()

    queries: list[str] = []

    if full_query:
        queries.append(full_query)

    if fallback and fallback != full_query:
        queries.append(fallback)

    # Concept-level query (strip entity names, keep the visual concept)
    if subject and era:
        # Remove proper nouns, keep descriptive words
        concept_words = []
        for w in subject.split():
            if not (w[0].isupper() if w else False) or len(w) <= 2:
                concept_words.append(w)
        if concept_words:
            concept = " ".join(concept_words) + " " + era
            if concept not in queries:
                queries.append(concept)

    return queries


async def search_batch(
    slots: list[dict],
) -> dict[int, list[ImageResult]]:
    """
    Search images for multiple slots in parallel.
    ALWAYS searches both Wikimedia and Pexels for every slot.
    source_hint only affects ranking, not search filtering.
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": WIKIMEDIA_USER_AGENT},
        verify=True,
    ) as client:
        async def _search_slot(slot: dict) -> tuple[int, list[ImageResult]]:
            style = slot.get("style_hint", "neutral")
            pexels_color = STYLE_TO_PEXELS_COLOR.get(style)

            tasks = []

            # ALWAYS search Wikimedia with short targeted queries
            wm_queries = _build_wikimedia_queries(slot)
            for wq in wm_queries[:3]:
                tasks.append(search_wikimedia(client, wq))

            # ALWAYS search Pexels with descriptive queries
            px_queries = _build_pexels_queries(slot)
            for pq in px_queries[:2]:
                tasks.append(search_pexels(client, pq, color=pexels_color))

            all_results = await asyncio.gather(*tasks)
            merged = [r for batch in all_results for r in batch]

            # Deduplicate by URL
            seen_urls: set[str] = set()
            deduped: list[ImageResult] = []
            for r in merged:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    deduped.append(r)

            return slot["id"], deduped

        results = await asyncio.gather(*[_search_slot(s) for s in slots])
        return dict(results)


async def download_image(url: str, path: str) -> bool:
    async with httpx.AsyncClient(
        headers={"User-Agent": WIKIMEDIA_USER_AGENT},
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.get(url, timeout=30)
            if resp.status_code == 200:
                with open(path, "wb") as f:
                    f.write(resp.content)
                return True
        except Exception:
            pass
    return False
