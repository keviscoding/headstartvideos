"""
Derive ranked niche subjects from niche_channels recent/popular titles.

No LLM — cheap clustering so MCP discovery stays free to serve.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

# Themes people "live inside" for GTA-style niches (and useful elsewhere).
_THEME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("money / economy", re.compile(r"\b(econom|money|cash|rpm|business|stock|invest|wealth|rich|poor|wage|salary|bank)\b", re.I)),
    ("police / wanted", re.compile(r"\b(police|cop|wanted|arrest|jail|prison|fbi|swat)\b", re.I)),
    ("cars / vehicles", re.compile(r"\b(car|cars|vehicle|vehicles|garage|racing|drive|driving|supercar|motorcycle|bike)\b", re.I)),
    ("property / housing", re.compile(r"\b(propert|house|housing|apartment|mansion|real estate|buy a home)\b", re.I)),
    ("editions / preorder", re.compile(r"\b(edition|pre[- ]?order|deluxe|collector|standard edition|which version)\b", re.I)),
    ("map / world", re.compile(r"\b(map|vice city|leonida|open world|city|island)\b", re.I)),
    ("characters / story", re.compile(r"\b(character|protagonist|story|trailer|lucia|jason|plot)\b", re.I)),
    ("weapons / combat", re.compile(r"\b(weapon|gun|combat|shoot|heist)\b", re.I)),
    ("multiplayer / online", re.compile(r"\b(online|multiplayer|gta online|friends|crew)\b", re.I)),
    ("release / launch", re.compile(r"\b(release|launch|delay|date|coming|november|trailer\s*\d)\b", re.I)),
]


def _parse_videos(raw) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    return [v for v in raw if isinstance(v, dict) and (v.get("title") or "").strip()]


def _clean_title(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"\s*[\|\-–—]\s*GTA\s*6.*$", "", t, flags=re.I)
    t = re.sub(r"\s*[\|\-–—]\s*.{0,40}$", "", t) if len(t) > 80 else t
    return re.sub(r"\s+", " ", t).strip()


def _theme_for(title: str) -> str:
    for label, pat in _THEME_PATTERNS:
        if pat.search(title):
            return label
    # Fallback: first ~8 words as a soft subject
    words = re.findall(r"[A-Za-z0-9']+", title)
    if len(words) >= 4:
        return " ".join(words[:8])
    return title[:60] or "untitled"


def list_subjects_from_channels(
    channels: list[dict],
    *,
    limit: int = 5,
) -> list[dict]:
    """
    Rank subjects by evidence (sum of view_counts across matching titles).
    Each item: subject, evidence_views, example_titles, channels[].
    """
    buckets: dict[str, dict] = {}
    for ch in channels:
        name = ch.get("channel_name") or ch.get("channel_id") or "channel"
        recent = _parse_videos(ch.get("recent_videos") or ch.get("recent_videos_json"))
        popular = _parse_videos(ch.get("popular_videos") or ch.get("popular_videos_json"))
        seen_titles: set[str] = set()
        for v in recent + popular:
            title = _clean_title(str(v.get("title") or ""))
            if not title or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            theme = _theme_for(title)
            key = theme.lower()
            bucket = buckets.setdefault(
                key,
                {
                    "subject": theme,
                    "evidence_views": 0,
                    "example_titles": [],
                    "channels": {},
                },
            )
            views = int(v.get("view_count") or 0)
            bucket["evidence_views"] += views
            if len(bucket["example_titles"]) < 4 and title not in bucket["example_titles"]:
                bucket["example_titles"].append(title)
            ch_entry = bucket["channels"].setdefault(
                name,
                {"channel": name, "recent_avg_views": ch.get("recent_avg_views") or 0, "titles": []},
            )
            if len(ch_entry["titles"]) < 2:
                ch_entry["titles"].append(title)

    ranked = sorted(
        buckets.values(),
        key=lambda b: (b["evidence_views"], len(b["channels"])),
        reverse=True,
    )
    out = []
    for b in ranked[: max(1, limit)]:
        out.append(
            {
                "subject": b["subject"],
                "evidence_views": b["evidence_views"],
                "example_titles": b["example_titles"],
                "channels": list(b["channels"].values())[:6],
                "channel_count": len(b["channels"]),
            }
        )
    return out
