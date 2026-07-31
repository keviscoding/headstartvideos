"""
Daily keyword packs for Niche Finder cron.

Each day picks a small spontaneous set of simple YouTube-search probes
so hunts stay cheap, varied, and keep filling niche_channels with fresh niches.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

# Ultra-simple glue / emotion words that surface many niches in manual YT tests.
SIMPLE_PROBES = [
    "worse",
    "why",
    "how",
    "never",
    "always",
    "secret",
    "forbidden",
    "untold",
    "actually",
    "finally",
    "exposed",
    "truth",
    "mistake",
    "warning",
    "illegal",
    "hidden",
    "strange",
    "weird",
    "crazy",
    "insane",
    "shocking",
    "banned",
    "deleted",
    "leaked",
    "what happened",
    "before after",
    "vs",
    "explained",
    "full story",
    "real reason",
]

# Light niche anchors — mixed in so we don't only get random glue results.
NICHE_ANCHORS = [
    "history documentary",
    "true crime story",
    "reddit story",
    "horror narrated",
    "bible prophecy",
    "personal finance",
    "stoic habits",
    "war documentary",
    "folktale story",
    "psychology facts",
    "geopolitics explained",
    "sci fi HFY",
    "HOA revenge",
    "football drama",
    "ancient mystery",
    "prayer night",
    "sleep story",
    "conspiracy explained",
    "celebrity drama",
    "survival story",
]


def _day_seed(when: datetime | None = None) -> str:
    dt = when or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _stable_shuffle(items: list[str], *, salt: str) -> list[str]:
    """Deterministic order for a given day — same day = same pack (idempotent cron)."""
    def key(word: str) -> str:
        return hashlib.sha256(f"{salt}:{word}".encode()).hexdigest()
    return sorted(items, key=key)


def daily_cron_keywords(
    *,
    when: datetime | None = None,
    simple_count: int = 8,
    niche_count: int = 4,
) -> list[str]:
    """
    Spontaneous-feeling daily pack: mostly simple probes + a few niche anchors.

    Cron with empty keywords should call this instead of dumping DEFAULT_KEYWORDS.
    """
    day = _day_seed(when)
    simple = _stable_shuffle(list(SIMPLE_PROBES), salt=f"simple:{day}")[: max(1, simple_count)]
    niches = _stable_shuffle(list(NICHE_ANCHORS), salt=f"niche:{day}")[: max(0, niche_count)]
    # Preserve order: simples first (breadth), then anchors
    out: list[str] = []
    for w in simple + niches:
        if w not in out:
            out.append(w)
    return out
