"""
Free YouTube resources (prompts, sauce) served from the Resources page.

When adding a new resource:
1. Drop the file in webapp/static/resources/
2. Append an entry below with today's date (YYYY-MM-DD)
3. Set is_new=True on the new one; set older ones to False (or leave
   is_new unset and rely on NEW_WINDOW_DAYS)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

STATIC_RESOURCES = Path(__file__).resolve().parent / "static" / "resources"

# Resources newer than this many days get a ★ New badge automatically.
NEW_WINDOW_DAYS = 30

# Newest first.
RESOURCES: list[dict] = [
    {
        "id": "whitespace-ideation-prompt",
        "title": "Whitespace Ideation Prompt",
        "tagline": "Carve original niches your audience already wants — not recycled niches.",
        "description": (
            "A free research-backed prompt that turns winning channel data into adjacent, "
            "unsaturated niche ideas. Attach your titles/transcripts/outliers, paste today's "
            "date, and get a ranked idea slate with evidence — not vibes."
        ),
        "date": "2026-07-15",
        "filename": "2026-07-15-whitespace-ideation-prompt.txt",
        "download_name": "whitespace-ideation-prompt.txt",
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
        path = STATIC_RESOURCES / item["filename"]
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
    return STATIC_RESOURCES / item["filename"]


def any_new_resources(*, today: date | None = None) -> bool:
    return any(r.get("is_new") for r in list_resources(today=today))
