"""
YouTube resources (prompts, sauce, paid guides) served from the Resources page.

When adding a new resource:
1. For file downloads: drop the file in webapp/resource_files/ and set filename
2. For paid doc unlocks: set credit_cost + unlock_url (no file required)
3. Append an entry below with today's date (YYYY-MM-DD)
4. Set is_new=True on the new one; set older ones to False (or leave
   is_new unset and rely on NEW_WINDOW_DAYS)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

# Outside /static so downloads go through the auth-gated API only.
RESOURCE_FILES = Path(__file__).resolve().parent / "resource_files"

# Resources newer than this many days get a New badge automatically.
NEW_WINDOW_DAYS = 30

# Newest first.
RESOURCES: list[dict] = [
    {
        "id": "3d-kids-animation-pro-system",
        "title": "Pro System: 3D Kids Animation (Private Guide)",
        "tagline": "The advanced playbook we don't put in public videos: cheap production at scale.",
        "description": (
            "A private AI 3D animation strategy guide built for creators who want to outcompete "
            "in this niche. Inside: how to produce animations as cheaply as they get, how to ship "
            "100+ animations a month for a fraction of typical costs, how we do ideation that "
            "actually gets views, and how to optimize trust so viewers stick. This is the advanced "
            "version of the strategy, not the free public tips."
        ),
        "date": "2026-07-23",
        "kind": "guide",
        "credit_cost": 55,
        "unlock_url": (
            "https://docs.google.com/document/d/1IgE1drM8H80Z2vdzQv4rPtY3t3Pn4SDcuFirsWfzkPs/edit?usp=sharing"
        ),
        "is_new": True,
    },
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
        "credit_cost": 0,
        "is_new": False,
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


def list_resources(*, today: date | None = None, unlocked_ids: set[str] | None = None) -> list[dict]:
    today = today or date.today()
    unlocked_ids = unlocked_ids or set()
    out = []
    for item in RESOURCES:
        credit_cost = int(item.get("credit_cost") or 0)
        unlock_url = (item.get("unlock_url") or "").strip()
        filename = (item.get("filename") or "").strip()
        if filename:
            path = RESOURCE_FILES / filename
            if not path.is_file():
                continue
        elif not unlock_url:
            continue
        rid = item["id"]
        unlocked = credit_cost <= 0 or rid in unlocked_ids
        out.append({
            "id": rid,
            "title": item["title"],
            "tagline": item.get("tagline") or "",
            "description": item.get("description") or "",
            "date": item["date"],
            "kind": item.get("kind") or "resource",
            "is_new": is_resource_new(item, today=today),
            "requires_plan": False,
            "credit_cost": credit_cost,
            "unlocked": unlocked,
            "is_paid": credit_cost > 0,
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
