"""
Image ranking and selection with entity verification, context matching,
and tonal filtering. Picks the best image per B-roll slot from search results.

Scoring layers:
1. Resolution & orientation (basic quality)
2. Junk filter (known bad patterns)
3. Subject relevance: does the title contain the person/entity name?
4. Context matching: does the title match the ACTION, not just the person?
   "Kennedy Congress speech" > "Kennedy Oval Office child"
5. Tonal mismatch: penalize gravesite images for inspiring segments
6. Source preference: slight wikimedia bonus for entities, pexels for mood
"""

from __future__ import annotations
import re
from core.image_search import ImageResult


JUNK_KEYWORDS = [
    "diagram", "chart", "graph", "logo", "flag icon", "svg icon",
    "screenshot", "table", "spreadsheet", "contact sheet", "photo contact",
    "thumbnail", "stub", "placeholder", "NLGRF photo contact",
    "ford a", "ford b",
    "signature", "autograph", "handwriting", "typed letter",
    "press release", "document scan", "text page",
]

TONAL_NEGATIVES = {
    "inspiring": ["grave", "gravesite", "cemetery", "funeral", "death", "memorial", "tomb", "burial", "coffin", "obituary", "autopsy", "morgue"],
    "triumphant": ["grave", "gravesite", "cemetery", "funeral", "death", "memorial", "tomb", "burial"],
    "hopeful": ["grave", "cemetery", "funeral", "death", "memorial", "tomb"],
    "confident": ["grave", "cemetery", "funeral", "death", "memorial"],
    "heroic": ["grave", "cemetery", "funeral", "death"],
    "joyful": ["grave", "cemetery", "funeral", "death", "sad", "mourning"],
    "determined": ["grave", "cemetery", "funeral", "death"],
    "awe": ["grave", "cemetery", "funeral", "death"],
    "emotional": ["grave", "cemetery", "funeral"],
}


def _extract_name_words(subject: str) -> list[str]:
    """Extract proper name words (3+ chars, capitalized) from the subject."""
    title_skip = {"the", "president", "dr", "mr", "mrs", "sir", "general",
                  "senator", "captain", "king", "queen", "prince", "pope"}
    words = []
    for w in subject.split():
        clean = re.sub(r'[^a-zA-Z]', '', w)
        if clean and len(clean) >= 3 and clean[0].isupper() and clean.lower() not in title_skip:
            words.append(clean)
    return words


def _extract_context_words(subject: str) -> list[str]:
    """
    Extract meaningful context/action words from the subject.
    These describe WHAT the entity is doing or WHERE, not who they are.
    e.g., from "President Kennedy speaking Congress 1961" -> ["speaking", "congress"]
    """
    skip = {"the", "a", "an", "in", "on", "at", "of", "for", "and", "or",
            "from", "to", "with", "by", "is", "was", "his", "her", "their"}
    words = []
    for w in subject.split():
        clean = re.sub(r'[^a-zA-Z]', '', w).lower()
        if clean and len(clean) >= 4 and clean not in skip:
            # Skip words that look like proper names (already handled by name matching)
            original = re.sub(r'[^a-zA-Z]', '', w)
            if original and original[0].isupper():
                continue
            words.append(clean)
    return words


def _subject_relevance(title: str, subject: str, entity_type: str) -> float:
    """
    Score how well the image title matches the intended subject.
    Returns a score from -5.0 (clear mismatch) to +6.0 (strong match).
    """
    title_lower = title.lower()

    if entity_type == "person":
        name_words = _extract_name_words(subject)

        if name_words:
            matches = []
            for i, n in enumerate(name_words):
                if re.search(r'\b' + re.escape(n.lower()) + r'\b', title_lower):
                    matches.append(i)

            if len(matches) >= 2:
                return 6.0

            if len(matches) == 1 and len(name_words) >= 2:
                # Only one name part matched. If it's the LAST name (surname),
                # it's a decent match. If it's just a first name like "John",
                # it's likely a false positive -- too many people named John.
                matched_idx = matches[0]
                if matched_idx == len(name_words) - 1:
                    return 3.0  # Last name matched (e.g., "Kennedy")
                else:
                    return 0.0  # Only first name matched (e.g., "John")

            if len(matches) == 1:
                return 3.0

            return -5.0  # Wrong person entirely

    elif entity_type in ("event", "named_entity"):
        key_words = [w.lower() for w in _extract_name_words(subject)[:5]]
        if not key_words:
            key_words = [w for w in subject.lower().split()[:5] if len(w) >= 3]
        matches = sum(1 for w in key_words if w in title_lower)
        ratio = matches / max(len(key_words), 1)
        if ratio >= 0.5:
            return 5.0 if entity_type == "named_entity" else 4.0
        elif ratio >= 0.25:
            return 2.0 if entity_type == "named_entity" else 1.0
        return -2.0 if entity_type == "named_entity" else -1.0

    elif entity_type == "place":
        key_words = [w.lower() for w in _extract_name_words(subject)[:3]]
        matches = sum(1 for w in key_words if w in title_lower)
        if matches >= 1:
            return 3.0
        return -0.5

    elif entity_type == "object":
        key_words = subject.lower().split()[:3]
        key_words = [w for w in key_words if len(w) >= 4]
        matches = sum(1 for w in key_words if w in title_lower)
        if matches >= 1:
            return 2.0
        return 0.0

    return 0.0


def _context_relevance(title: str, subject: str) -> float:
    """
    Score how well the image matches the ACTION/CONTEXT, not just the entity.
    This distinguishes "JFK speaking Congress" from "JFK Oval Office child."
    Returns -2.0 to +4.0.
    """
    title_lower = title.lower()
    context_words = _extract_context_words(subject)

    if not context_words:
        return 0.0

    matches = 0
    for w in context_words:
        if w in title_lower:
            matches += 1

    if matches >= 2:
        return 4.0
    elif matches == 1:
        return 2.0
    elif len(context_words) >= 2:
        # Subject has clear context words but NONE matched: mild penalty.
        # This helps demote "Kennedy meets Ambassador" when looking for
        # "Kennedy addressing Congress."
        return -2.0
    return 0.0


def _era_mismatch(title: str, style_hint: str) -> float:
    """
    Penalize modern imagery when we need historical, and vice versa.
    E.g., picking a SpaceX rocket when searching for Saturn V.
    """
    title_lower = title.lower()

    modern_markers = ["spacex", "tesla", "2020", "2021", "2022", "2023",
                      "2024", "2025", "2026", "iphone", "digital", "modern"]
    vintage_markers = ["1950", "1960", "1970", "1940", "vintage", "archival",
                       "circa", "historical"]

    if style_hint == "historical_bw":
        for m in modern_markers:
            if m in title_lower:
                return -6.0
    elif style_hint == "modern_color":
        for m in vintage_markers:
            if m in title_lower:
                return -3.0
    return 0.0


def _tonal_mismatch(title: str, tone: str) -> float:
    """
    Detect tonal mismatches: e.g., showing a gravesite for an inspiring segment.
    Returns 0.0 if no mismatch, -8.0 for severe mismatch.
    """
    if not tone:
        return 0.0

    title_lower = title.lower()
    tone_lower = tone.lower()

    for tone_word, bad_keywords in TONAL_NEGATIVES.items():
        if tone_word in tone_lower:
            for kw in bad_keywords:
                if kw in title_lower:
                    return -8.0

    return 0.0


def score_image(
    img: ImageResult,
    style_hint: str = "neutral",
    entity_type: str = "mood",
    source_hint: str = "any",
    subject: str = "",
    tone: str = "",
) -> float:
    s = 0.0
    title_lower = img.title.lower()

    # --- Resolution ---
    if img.width >= 1920 and img.height >= 1080:
        s += 3.0
    elif img.width >= 1280:
        s += 1.5

    # --- Orientation ---
    if img.width > img.height:
        ratio = img.width / max(img.height, 1)
        if 1.5 <= ratio <= 2.0:
            s += 2.0
        elif ratio > 1.0:
            s += 1.0
    else:
        s -= 1.0

    # --- Junk filter ---
    for kw in JUNK_KEYWORDS:
        if kw in title_lower:
            s -= 5.0
            break

    # --- SUBJECT RELEVANCE ---
    if subject:
        s += _subject_relevance(img.title, subject, entity_type)

    # --- CONTEXT RELEVANCE ---
    if subject:
        s += _context_relevance(img.title, subject)

    # --- TONAL MISMATCH ---
    s += _tonal_mismatch(img.title, tone)

    # --- ERA MISMATCH ---
    s += _era_mismatch(img.title, style_hint)

    # --- Source preference (light touch -- subject relevance does the heavy lifting) ---
    if entity_type in ("person", "event", "named_entity"):
        if source_hint == "wikimedia":
            if img.source == "wikimedia":
                s += 2.0
            else:
                s -= 1.0
        elif source_hint == "any":
            if img.source == "wikimedia":
                s += 1.0

    elif entity_type == "place":
        if source_hint == "wikimedia":
            if img.source == "wikimedia":
                s += 1.5

    elif entity_type in ("object", "mood"):
        if source_hint == "stock":
            if img.source == "pexels":
                s += 2.0
        else:
            if img.source == "pexels":
                s += 0.5

    # --- Style matching ---
    if style_hint == "historical_bw":
        if any(w in title_lower for w in [
            "portrait", "historical", "vintage", "circa", "19", "photograph"
        ]):
            s += 1.5
    elif style_hint == "cinematic_dark":
        if img.source == "pexels":
            s += 0.5

    return s


def rank_and_pick(
    results: list[ImageResult],
    style_hint: str = "neutral",
    used_urls: set[str] | None = None,
    entity_type: str = "mood",
    source_hint: str = "any",
    subject: str = "",
    tone: str = "",
) -> ImageResult | None:
    """Score all results, penalize duplicates, return the best one."""
    if not results:
        return None

    used = used_urls or set()

    scored: list[tuple[float, ImageResult]] = []
    for img in results:
        s = score_image(img, style_hint, entity_type, source_hint, subject, tone)
        if img.thumb_url in used or img.url in used:
            s -= 15.0
        scored.append((s, img))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_img = scored[0]

    if best_score < -5:
        return None

    return best_img
