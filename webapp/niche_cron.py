"""
In-app twice-daily Niche Finder cron.

When CRON_SECRET is set on the web app, a background loop claims one hunt per
12-hour UTC slot and starts the lightweight Fly/web pipeline.
No DigitalOcean scheduled job required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone


CRON_UTC_HOURS = (0, 12)


def _utc_now(now: datetime | None = None) -> datetime:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_day_key(now: datetime | None = None) -> str:
    dt = _utc_now(now)
    return dt.strftime("%Y-%m-%d")


def utc_slot_key(now: datetime | None = None) -> str:
    dt = _utc_now(now)
    day = utc_day_key(dt)
    # Keep the historical day key for noon: deploying after today's daily run
    # must not launch it again. Midnight is the new, independent slot.
    return day if dt.hour >= CRON_UTC_HOURS[1] else f"{day}:00"


def maybe_start_daily_niche_hunt(*, start_hunt, now: datetime | None = None) -> dict:
    """
    Claim and start the current midnight/noon slot, at most once per slot.
    After downtime, only the current slot is eligible; missed slots do not pile up.

    start_hunt: callable(**kwargs) -> job_id  (usually server._start_niche_hunt)
    Returns a status dict, including skipped/failed claims.
    """
    import config
    from core.niche_daily_keywords import daily_cron_keywords
    from webapp.database import (
        claim_daily_niche_cron, finish_daily_niche_cron, release_daily_niche_cron,
    )

    if not (getattr(config, "CRON_SECRET", "") or "").strip():
        return {"skipped": "CRON_SECRET not set"}
    if not (getattr(config, "YOUTUBE_API_KEY", "") or "").strip():
        return {"skipped": "YOUTUBE_API_KEY missing"}

    now = _utc_now(now)
    day = utc_day_key(now)
    slot = utc_slot_key(now)
    if not claim_daily_niche_cron(slot):
        return {"skipped": "already ran this slot", "day": day, "slot": slot}

    keywords = daily_cron_keywords(when=now, count=50, slot=int(now.hour >= CRON_UTC_HOURS[1]))
    try:
        job_id = start_hunt(
            keywords=keywords,
            max_per_keyword=0,
            max_channels=0,
            min_recent_avg_views=0,
            max_subscribers=300_000,
            scroll_count=5,
            max_video_age_days=180,
            trigger="cron",
            user_id=None,
            discovery_settings={"profile":"balanced","time_budget_seconds":600,
                "target_channels":50,"candidate_cap":600,"search_cap":100,"api_cap":2000},
        )
    except Exception as e:
        if getattr(e, "status_code", None) == 409:
            # A manual hunt may own the worker. Try this slot at the next check
            # instead of starting another machine or losing the scheduled run.
            release_daily_niche_cron(slot)
            return {"skipped": "discovery already running", "day": day, "slot": slot}
        # Keep failed-spawn claims so a broken configuration cannot repeatedly
        # launch workers. The next slot is independent; ops can also admin-run.
        print(f"[niche_cron] start failed after claim {slot}: {e}")
        return {"error": str(e), "day": day, "slot": slot}

    finish_daily_niche_cron(slot, job_id=job_id, keywords=keywords)
    print(f"[niche_cron] started job={job_id} slot={slot} keywords={keywords}")
    return {"job_id": job_id, "day": day, "slot": slot, "keywords": keywords}


async def run_daily_niche_cron_loop(*, start_hunt, interval_sec: int = 300) -> None:
    """Background loop — check every `interval_sec` (default 5 min)."""
    poll_interval = max(60, int(interval_sec))
    print(f"[niche_cron] schedule=00:00,12:00 UTC interval={poll_interval}s")
    # Stagger first check so boot + GTA seed aren't competing.
    await asyncio.sleep(45)
    while True:
        try:
            # Database and Fly HTTP calls must not block web requests/cooking.
            await asyncio.to_thread(maybe_start_daily_niche_hunt, start_hunt=start_hunt)
        except Exception as e:
            print(f"[niche_cron] loop error: {e}")
        await asyncio.sleep(poll_interval)
