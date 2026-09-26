"""When to send.

PWF: neediness vs persistence is mostly WAIT TIME (TGB103, TG96).
- Replies: human, variable delays; fast only during live same-day logistics
  (slow replies lose same-night meets: TB59, TB80).
- Follow-ups: double text after 48-72h, triple after ~a week, then a sweep
  every ``sweep_days`` (TB71), capped by ``max_sweeps``.
- Date protocol: keep-warm if the date is 2+ days out, night-before reminder,
  day-of warm-up then confirm (TG90, TGB97).
- Quiet hours in HER timezone, unless she's texting at that hour.

All randomness is seeded by the thread + last message, so the schedule is
stable across repeated calls (the autopilot can poll without it moving).
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from .models import PLAN_STATES
from .timeparse import local, tz

UTC = timezone.utc

LIVE_MOVES = {"same_night_close", "en_route_reply", "text_when_otw", "logistics_reply", "dayof_confirm", "accept_her_proposal"}


def _rng(*parts: str) -> random.Random:
    seed = int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def _lognormal_minutes(r: random.Random, median_min: float, lo: float, hi: float) -> float:
    v = math.exp(r.gauss(math.log(median_min), 0.8))
    return max(lo, min(hi, v))


def _in_quiet(hour: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def apply_quiet_hours(due: datetime, ctx, r: random.Random) -> datetime:
    s = ctx.settings
    qs, qe = int(s.get("quiet_start", 1)), int(s.get("quiet_end", 8))
    her_tzname = ctx.s.her_tz
    due_local = local(due, her_tzname)
    if not _in_quiet(due_local.hour, qs, qe):
        return due
    # she's up and texting right now -> it's fine to answer
    her_last = ctx.s.her_last
    if her_last and her_last.sent_at and ctx.s.since_her_last_h is not None and ctx.s.since_her_last_h < 0.75:
        if _in_quiet(local(her_last.sent_at, her_tzname).hour, qs, qe):
            return due
    day = due_local.date()
    if due_local.hour >= qe and qs > qe:
        day = day + timedelta(days=1)
    wake = datetime.combine(day, time(qe, 0), tzinfo=tz(her_tzname)) + timedelta(minutes=r.randint(5, 80))
    return wake.astimezone(UTC)


def reply_due(ctx, move_id: str) -> datetime:
    """When a reply to her newest message should go out."""
    s = ctx.s
    now = s.now
    her_last = s.her_last
    r = _rng(s.thread_id, her_last.fp if her_last else "", move_id)
    if not ctx.settings.get("honor_timing", True):
        return now
    live = (ctx.plan_today and s.date.state in PLAN_STATES | {"EN_ROUTE"}) or (move_id in LIVE_MOVES and (ctx.plan_today or move_id == "same_night_close"))
    if live:
        target_min = r.uniform(1.0, 6.0)
    elif s.her_unanswered >= 2 or s.investment == "high":
        target_min = _lognormal_minutes(r, 20, 4, 120)
    else:
        target_min = _lognormal_minutes(r, 35, 6, 210)
    sent = her_last.sent_at if (her_last and her_last.sent_at and her_last.time_known) else now
    due = sent + timedelta(minutes=target_min)
    if due < now:
        due = now
    return apply_quiet_hours(due, ctx, r)


def _at_local(day, hour: int, minute: int, tzname: str) -> datetime:
    """``day`` at ``hour``:00 local plus ``minute`` minutes (minute may exceed 59)."""
    return (datetime.combine(day, time(hour, 0), tzinfo=tz(tzname)) + timedelta(minutes=minute)).astimezone(UTC)


def next_followup(ctx) -> tuple[Optional[datetime], str]:
    """When should v2 consider sending something although she hasn't replied?

    Returns (due_at, reason). reason names the timer; the move is still chosen
    by the policy (preconditions decide what is legal at that moment).
    """
    s = ctx.s
    st = ctx.settings
    if not st.get("followups", True) or s.closed:
        return None, ""
    now = s.now
    her_tzname = s.her_tz
    today = local(now, her_tzname).date()
    fp = s.my_last.fp if s.my_last else (s.her_last.fp if s.her_last else "")
    r = _rng(s.thread_id, fp, "followup")
    ds = s.date.state

    # --- date protocol ---------------------------------------------------
    if ctx.plan and ds in ("AGREED", "REMINDED") and s.date.when is not None:
        if ctx.plan_today:
            if not ctx.my_last_today:
                return max(now, _at_local(today, 11, r.randint(0, 90), her_tzname)), "dayof_warmup"
            if s.last_speaker == "me" and s.my_last and s.my_last.sent_at:
                return s.my_last.sent_at + timedelta(hours=3), "dayof_confirm"
            return None, ""
        plan_day = local(s.date.when, her_tzname).date()
        timers: list[tuple[datetime, str]] = [(_at_local(plan_day, 11, r.randint(0, 90), her_tzname), "dayof_warmup")]
        if ds == "AGREED" and "reminder_night_before" not in ctx.used:
            if not (ctx.plan_tomorrow and local(now, her_tzname).hour >= 21):
                rem = _at_local(plan_day - timedelta(days=1), 18, r.randint(15, 120), her_tzname)
                timers.append((max(now, rem), "reminder_night_before"))
        if ctx.hours_to_plan is not None and ctx.hours_to_plan > 48 and "keep_warm" not in ctx.used:
            last_contact = max([m.sent_at for m in (s.my_last, s.her_last) if m and m.sent_at] or [now])
            mid = last_contact + (s.date.when - last_contact) / 2
            due = max(last_contact + timedelta(hours=36), mid)
            if due < s.date.when - timedelta(hours=30):
                timers.append((apply_quiet_hours(due, ctx, r), "keep_warm"))
        return min(timers, key=lambda t: t[0])
    if ds == "MET" and "post_date_callback" not in ctx.used and s.last_speaker == "me":
        base = (s.date.when or (s.my_last.sent_at if s.my_last and s.my_last.sent_at else now)) + timedelta(hours=12)
        return apply_quiet_hours(max(base, now), ctx, r), "post_date_callback"

    # --- unanswered messages from me ------------------------------------
    if s.last_speaker != "me" or not s.my_last or not s.my_last.sent_at:
        return None, ""
    if not s.times_known:
        return None, ""
    n = s.my_unanswered
    base = s.my_last.sent_at
    if n == 1:
        hours = r.uniform(48, 60) if ctx.my_last_was_plan_ask() else r.uniform(56, 72)
        return apply_quiet_hours(base + timedelta(hours=hours), ctx, r), "double_text"
    if n == 2:
        return apply_quiet_hours(base + timedelta(days=r.uniform(5, 7)), ctx, r), "triple_text"
    sweeps = int(ctx.flags.get("sweeps_sent", 0))
    if sweeps < int(st.get("max_sweeps", 3)):
        days = float(st.get("sweep_days", 10)) * (1 + 0.5 * sweeps)
        return apply_quiet_hours(base + timedelta(days=days), ctx, r), "sweep"
    return None, "walk_away"
