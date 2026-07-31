"""
In-app daily Niche Finder cron.

When CRON_SECRET is set on the web app, a background loop claims one hunt per
UTC day and starts the same Fly/web pipeline as the external cron endpoint.
No DigitalOcean scheduled job required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone


# Run shortly after this UTC hour so US mornings still see fresh data.
CRON_UTC_HOUR = 12


def utc_day_key(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def maybe_start_daily_niche_hunt(*, start_hunt) -> dict | None:
    """
    If today's slot is free and we're past CRON_UTC_HOUR, claim + start hunt.

    start_hunt: callable(**kwargs) -> job_id  (usually server._start_niche_hunt)
    Returns status dict or None if skipped.
    """
    import config
    from core.niche_daily_keywords import daily_cron_keywords
    from webapp.database import claim_daily_niche_cron, finish_daily_niche_cron

    if not (getattr(config, "CRON_SECRET", "") or "").strip():
        return {"skipped": "CRON_SECRET not set"}
    if not (getattr(config, "YOUTUBE_API_KEY", "") or "").strip():
        return {"skipped": "YOUTUBE_API_KEY missing"}

    now = datetime.now(timezone.utc)
    if now.hour < CRON_UTC_HOUR:
        return {"skipped": f"before {CRON_UTC_HOUR}:00 UTC"}

    day = utc_day_key(now)
    if not claim_daily_niche_cron(day):
        return {"skipped": "already ran today", "day": day}

    keywords = daily_cron_keywords(when=now)
    try:
        job_id = start_hunt(
            keywords=keywords,
            max_per_keyword=12,
            max_channels=60,
            min_recent_avg_views=0,
            max_subscribers=150_000,
            scroll_count=20,
            max_video_age_days=180,
            trigger="cron",
            user_id=None,
        )
    except Exception as e:
        # Release is intentional non-event — leave the claim so we don't hammer
        # retries all day on a broken spawn; ops can admin-run.
        print(f"[niche_cron] start failed after claim {day}: {e}")
        return {"error": str(e), "day": day}

    finish_daily_niche_cron(day, job_id=job_id, keywords=keywords)
    print(f"[niche_cron] started job={job_id} day={day} keywords={keywords}")
    return {"job_id": job_id, "day": day, "keywords": keywords}


async def run_daily_niche_cron_loop(*, start_hunt, interval_sec: int = 300) -> None:
    """Background loop — check every `interval_sec` (default 5 min)."""
    # Stagger first check so boot + GTA seed aren't competing.
    await asyncio.sleep(45)
    while True:
        try:
            maybe_start_daily_niche_hunt(start_hunt=start_hunt)
        except Exception as e:
            print(f"[niche_cron] loop error: {e}")
        await asyncio.sleep(max(60, int(interval_sec)))
