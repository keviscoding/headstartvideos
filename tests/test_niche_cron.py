"""Scheduled discovery stays bounded, independent, and idempotent across replicas."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

from fastapi import HTTPException
import pytest

import config
from core.niche_daily_keywords import daily_cron_keywords
from webapp import database as db
from webapp import niche_cron as cron


@pytest.fixture
def scheduled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CRON_SECRET", "test-secret")
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cron.db")
    db._ensure_niche_cron_table()
    calls = []

    def start(**kwargs):
        calls.append(kwargs)
        return f"job-{len(calls)}"

    return calls, start


def at(day=6, hour=0, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def test_two_slots_per_day_keep_original_budgets_and_rotate_searches(scheduled):
    calls, start = scheduled
    for day in (6, 7):
        for hour in (0, 12):
            result = cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at(day, hour))
            assert result["job_id"]
            assert db.get_daily_niche_cron(result["slot"])["job_id"] == result["job_id"]
            repeated = cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at(day, hour + 11, 59))
            assert repeated["skipped"] == "already ran this slot"
    assert len(calls) == 4
    for call in calls:
        assert call["trigger"] == "cron"
        assert call["max_subscribers"] == 300_000
        assert call["discovery_settings"] == {
            "profile": "balanced", "time_budget_seconds": 600, "target_channels": 50,
            "candidate_cap": 600, "search_cap": 100, "api_cap": 2000,
        }
        assert len(call["keywords"]) == len(set(call["keywords"])) == 50
    for previous, current in zip(calls, calls[1:]):
        assert set(previous["keywords"]).isdisjoint(current["keywords"])


def test_legacy_daily_claim_prevents_extra_noon_run_after_deployment(scheduled):
    calls, start = scheduled
    assert db.claim_daily_niche_cron("2026-10-06")
    db.finish_daily_niche_cron("2026-10-06", job_id="old-daily-job", keywords=[])
    result = cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at(hour=16))
    assert result["skipped"] == "already ran this slot"
    assert not calls
    assert cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at(day=7))["job_id"]
    assert len(calls) == 1


def test_downtime_only_runs_current_slot_without_catching_up_missed_hunts(scheduled):
    calls, start = scheduled
    result = cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at(hour=22))
    assert result["slot"] == "2026-10-06"
    assert not db.get_daily_niche_cron("2026-10-06:00")
    assert len(calls) == 1


def test_concurrent_web_replicas_start_one_worker_per_slot(scheduled):
    calls, start = scheduled
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(
            lambda _: cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at()), range(6),
        ))
    assert len(calls) == 1
    assert sum("job_id" in result for result in results) == 1


def test_busy_hunt_defers_slot_until_worker_is_free(scheduled):
    calls, start = scheduled

    def busy(**kwargs):
        raise HTTPException(409, "Discovery is already running.")

    result = cron.maybe_start_daily_niche_hunt(start_hunt=busy, now=at())
    assert result["skipped"] == "discovery already running"
    assert not db.get_daily_niche_cron("2026-10-06:00")
    assert cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at(minute=5))["job_id"]
    assert len(calls) == 1


def test_failed_spawn_does_not_hammer_fly_but_next_slot_can_run(scheduled):
    calls, start = scheduled
    attempts = []

    def broken(**kwargs):
        attempts.append(kwargs)
        raise HTTPException(503, "Cloud discovery worker could not start.")

    assert "error" in cron.maybe_start_daily_niche_hunt(start_hunt=broken, now=at())
    assert "skipped" in cron.maybe_start_daily_niche_hunt(start_hunt=broken, now=at(minute=5))
    assert len(attempts) == 1
    assert cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at(hour=12))["job_id"]
    assert len(calls) == 1


def test_completed_claim_cannot_be_released(scheduled):
    _, start = scheduled
    result = cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at())
    db.release_daily_niche_cron(result["slot"])
    assert db.get_daily_niche_cron(result["slot"])["job_id"] == result["job_id"]


@pytest.mark.parametrize("missing", ["CRON_SECRET", "YOUTUBE_API_KEY"])
def test_missing_credentials_do_not_claim_or_spawn(scheduled, monkeypatch, missing):
    calls, start = scheduled
    monkeypatch.setattr(config, missing, "")
    assert "skipped" in cron.maybe_start_daily_niche_hunt(start_hunt=start, now=at())
    assert not calls
    assert not db.get_daily_niche_cron("2026-10-06:00")


def test_slot_and_keyword_pack_use_utc_even_for_local_timezone_inputs():
    local = datetime(2026, 10, 7, 0, 30, tzinfo=timezone(timedelta(hours=1)))
    assert cron.utc_day_key(local) == "2026-10-06"
    assert cron.utc_slot_key(local) == "2026-10-06"
    assert daily_cron_keywords(when=local, slot=1) == daily_cron_keywords(when=at(hour=23, minute=30), slot=1)
    assert cron.utc_slot_key(at(hour=11, minute=59)) == "2026-10-06:00"
    assert cron.utc_slot_key(at(hour=12)) == "2026-10-06"


def test_cron_does_blocking_db_and_fly_work_outside_web_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    threads = []
    sleeps = []

    def check(**kwargs):
        threads.append(threading.get_ident())

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(cron, "maybe_start_daily_niche_hunt", check)
    monkeypatch.setattr(cron.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cron.run_daily_niche_cron_loop(start_hunt=lambda: None))
    assert sleeps == [45, 300]
    assert len(threads) == 1 and threads[0] != main_thread
