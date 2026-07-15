"""
Free YouTube resources (prompts, sauce) served from the Resources page.

When adding a new resource:
1. Drop the file in webapp/resource_files/
2. Append an entry below with today's date (YYYY-MM-DD)
3. Set is_new=True on the new one; set older ones to False (or leave
   is_new unset and rely on NEW_WINDOW_DAYS)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

# Outside /static so downloads go through the auth-gated API only.
RESOURCE_FILES = Path(__file__).resolve().parent / "resource_files"

# Resources newer than this many days get a ★ New badge automatically.
NEW_WINDOW_DAYS = 30

# Newest first.
RESOURCES: list[dict] = [
    {
        "id": "blue-ocean-niche-prompt",
        "title": "Blue Ocean Niche Prompt",
        "tagline": "Stop copy-pasting viral channels. Combine niches into your own blue ocean.",
        "description": (
            "Most people see a viral video and clone it. This prompt does the opposite: "
            "it uses winning channel data + live research to bend niches together and invent "
            "adjacent, unsaturated territory — your own blue ocean, not someone else's red one. "
            "Attach titles/transcripts/outliers, paste today's date, get a ranked idea slate."
        ),
        "date": "2026-07-15",
        "filename": "2026-07-15-blue-ocean-niche-prompt.txt",
        "download_name": "blue-ocean-niche-prompt.txt",
        "kind": "prompt",
        # Explicit New until the next resource ships (also covered by NEW_WINDOW_DAYS).
        "is_new": True,
    },
]


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def is_resource_new(item: dict, *, today: date | None = None) -> bool:
    if item.get("is_new") is True:
        return True
    if item.get("is_new") is False:
        return False
    d = _parse_date(item.get("date") or "")
    if not d:
        return False
    today = today or date.today()
    return d >= today - timedelta(days=NEW_WINDOW_DAYS)


def list_resources(*, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for item in RESOURCES:
        path = RESOURCE_FILES / item["filename"]
        if not path.is_file():
            continue
        out.append({
            "id": item["id"],
            "title": item["title"],
            "tagline": item.get("tagline") or "",
            "description": item.get("description") or "",
            "date": item["date"],
            "kind": item.get("kind") or "resource",
            "is_new": is_resource_new(item, today=today),
            "requires_plan": False,  # account only — never card/trial
        })
    return out


def get_resource(resource_id: str) -> dict | None:
    for item in RESOURCES:
        if item["id"] == resource_id:
            return item
    return None


def resource_file_path(item: dict) -> Path:
    return RESOURCE_FILES / item["filename"]


def any_new_resources(*, today: date | None = None) -> bool:
    return any(r.get("is_new") for r in list_resources(today=today))
