"""Per-thread state, recomputed from THIS thread's own messages every call.

The centrepiece is the date state machine. A date only exists when:
  * someone proposed a specific day (or day+time) inside this thread, AND
  * the other side explicitly accepted it,
and it stops existing when its time passes, when it decays (a week+ without
confirmation), or when she cancels. "Our date" presuppositions ("looking nice
& fit for our date") are FRAME and never create a date.

Nothing here reads summaries, sticky notes, other threads or lab fixtures.
"""

from __future__ import annotations

import re

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import lexicon as L
from .models import DATE_RANK, PLAN_STATES, DateInfo, Evidence, Incidents, Msg, State
from .normalize import times_reliable
from .timeparse import find_day_time_refs, human, local, weekday_name

UTC = timezone.utc

PLAN_EXPIRY_H = 6            # a plan is over this long after its start time
AGREED_DECAY_DAYS = 7        # unconfirmed plans older than this need a re-close
PROPOSAL_STALE_H = 48        # a proposal nobody answered for 48h is dead
INTEREST_STALE_DAYS = 21
FLAKE_RECOVERY_DAYS = 7


HER_AVAIL_Q = re.compile(r"\b(what(?:'?s| are| r) (you|u) (doing|up to)|wyd|any plans)\b", re.I)


def _hours(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if not a or not b:
        return None
    return (b - a).total_seconds() / 3600.0


def _is_plan_ask(text: str) -> bool:
    return bool(L.SOFT_CLOSE.search(text) or L.SCHEDULE_ASK.search(text) or L.CONFIRM_Q.search(text))


def _future_refs(m: Msg, tzname: str):
    refs = find_day_time_refs(m.text, m.sent_at, tzname)
    out = []
    for r in refs:
        if r.day is None:
            continue
        sent_local_day = local(m.sent_at, tzname).date() if m.sent_at else None
        if sent_local_day and r.day < sent_local_day - timedelta(days=0):
            continue
        out.append(r)
    return out, refs


def _pick_ref(refs, text_of_acceptor: str):
    """If the acceptor named one of several options, pick that one."""
    low = text_of_acceptor.lower()
    for r in refs:
        if r.span and r.span.split()[0] in low:
            return r
    return refs[0] if refs else None


def compute_date(msgs: list[Msg], now: datetime, her_tz: str, my_tz: str, incidents: Incidents) -> DateInfo:
    info = DateInfo()
    state = "NONE"
    pending_idea: Optional[Msg] = None          # "we should get together sometime soon"
    pending_prop: Optional[tuple[Msg, list]] = None  # specific day proposal and its refs
    pending_confirm: Optional[Msg] = None
    when: Optional[datetime] = None
    hour_known = False
    when_text = ""
    last_plan_touch: Optional[datetime] = None
    schedule_asked_by_me = False

    def tz_of(m: Msg) -> str:
        return my_tz if m.is_me else her_tz

    for m in msgs:
        h = L.heuristics(m.text)
        future, allrefs = _future_refs(m, tz_of(m))
        is_frame_only = bool(L.FRAME_PRESUPPOSE.search(m.text)) and not L.SOFT_CLOSE.search(
            L.FRAME_PRESUPPOSE.sub("", m.text)
        )

        # --- her cancellation of an existing / proposed plan -------------
        other_day = bool(future) and when is not None and all(r.day != local(when, tz_of(m)).date() for r in future)
        is_cancel = h["decline"] or (
            state in PLAN_STATES and (h["apology"] or h["decline_reason"]) and (h["reschedule_offer"] or other_day)
        )
        if m.is_her and is_cancel and state in (PLAN_STATES | {"PROPOSED", "SCHEDULING"}) and not h["accept"]:
            had_plan = state in PLAN_STATES
            if h["reschedule_offer"] or future:
                state = "RESCHEDULING"
                if had_plan:
                    incidents.reschedules_by_her += 1
                    if h["decline_reason"]:
                        incidents.flakes_with_reason += 1
                    else:
                        incidents.flakes_no_reason += 1
                if future:
                    pending_prop = (m, future)
            else:
                state = "FLAKED" if had_plan else "INTEREST"
                if had_plan:
                    if h["decline_reason"]:
                        incidents.flakes_with_reason += 1
                    else:
                        incidents.flakes_no_reason += 1
            info.evidence.append(Evidence("cancel", [m.idx], m.text[:120]))
            last_plan_touch = m.sent_at
            when = when if state == "RESCHEDULING" else None
            pending_confirm = None
            continue

        # --- my reschedule of an agreed plan ------------------------------
        if m.is_me and h["decline"] and state in PLAN_STATES:
            incidents.reschedules_by_me += 1
            state = "RESCHEDULING"
            if future:
                pending_prop = (m, future)
            last_plan_touch = m.sent_at
            continue

        # --- confirm / en route / met ------------------------------------
        if m.is_me and L.CONFIRM_Q.search(m.text) and state in PLAN_STATES:
            pending_confirm = m
        if m.is_her and pending_confirm is not None and h["accept"] and state in PLAN_STATES:
            if when is None or local(m.sent_at, her_tz).date() == local(when, her_tz).date():
                state = "CONFIRMED_DAYOF"
                info.evidence.append(Evidence("CONFIRMED_DAYOF", [pending_confirm.idx, m.idx], m.text[:120]))
            pending_confirm = None
            last_plan_touch = m.sent_at
        if h["en_route"] and state in PLAN_STATES and (when is None or abs(_hours(m.sent_at, when) or 0) <= 14):
            state = "EN_ROUTE"
            info.evidence.append(Evidence("EN_ROUTE", [m.idx], m.text[:120]))
            last_plan_touch = m.sent_at
        if h["met"] and (state in PLAN_STATES or state == "EN_ROUTE") and (when is None or m.sent_at >= when - timedelta(hours=3)):
            state = "MET"
            info.evidence.append(Evidence("MET", [m.idx], m.text[:120]))
            last_plan_touch = m.sent_at
            pending_prop = None
            pending_idea = None
            continue

        # --- acceptance of a pending specific proposal --------------------
        if pending_prop is not None and m.speaker != pending_prop[0].speaker:
            prop_msg, prop_refs = pending_prop
            time_only = [r for r in allrefs if r.kind == "time_only" and r.hour is not None]
            if time_only and not future and prop_refs and prop_refs[0].day is not None:
                # counter-time for the same day ("8 maybe?") -> new proposal by the other side
                from .timeparse import DayTimeRef
                base = prop_refs[0]
                pending_prop = (m, [DayTimeRef(kind=base.kind, span=base.span, day=base.day,
                                               hour=time_only[0].hour, minute=time_only[0].minute)])
                state = "PROPOSED" if state not in PLAN_STATES else state
                last_plan_touch = m.sent_at
                continue
            named = [r for r in future if r.day in {x.day for x in prop_refs}]
            counter = [r for r in future if r.day not in {x.day for x in prop_refs}]
            if h["accept"] or named:
                chosen = _pick_ref(prop_refs, m.text) if not named else named[0]
                # a counter-time in the acceptance ("8 maybe?") keeps it at PROPOSED
                clock_counter = future and all(r.hour is not None for r in future) and named and named[0].hour != (chosen.hour if chosen else None)
                if counter and not h["accept"]:
                    pending_prop = (m, counter)
                    state = "PROPOSED"
                elif clock_counter and "?" in m.text:
                    pending_prop = (m, named)
                    state = "PROPOSED"
                else:
                    ref = named[0] if named and named[0].hour is not None else chosen
                    if ref is not None:
                        hr = ref.hour if ref.hour is not None else (chosen.hour if chosen else None)
                        when = ref.as_datetime(tz_of(prop_msg), default_hour=hr if hr is not None else 21)
                        hour_known = hr is not None
                        when_text = f"{weekday_name(ref.day)}" + (f" {hr % 12 or 12}{'pm' if hr >= 12 else 'am'}" if hr is not None else "")
                    state = "AGREED"
                    info.evidence.append(Evidence("AGREED", [prop_msg.idx, m.idx], f"{prop_msg.text[:80]} -> {m.text[:60]}"))
                    pending_prop = None
                    pending_idea = None
                    last_plan_touch = m.sent_at
                    continue
            elif m.is_her and not h["accept"] and not future and h["decline"]:
                pending_prop = None
                state = "INTEREST" if DATE_RANK.get(state, 0) >= DATE_RANK["INTEREST"] else state

        # --- acceptance of the idea -------------------------------------
        if pending_idea is not None and m.speaker != pending_idea.speaker and h["accept"] and not h["decline"]:
            if DATE_RANK[state] < DATE_RANK["INTEREST"] or state in ("MET", "FLAKED"):
                state = "INTEREST"
            info.evidence.append(Evidence("INTEREST", [pending_idea.idx, m.idx], f"{pending_idea.text[:80]} -> {m.text[:60]}"))
            pending_idea = None
            last_plan_touch = m.sent_at

        # --- her schedule info after my schedule ask ---------------------
        if m.is_her and schedule_asked_by_me and (allrefs or L.BUSY.search(m.text) or "free" in m.text.lower() or "off" in m.text.lower()):
            if DATE_RANK.get(state, 0) < DATE_RANK["SCHEDULING"] or state in ("MET", "FLAKED", "RESCHEDULING"):
                state = "SCHEDULING"
            schedule_asked_by_me = False
            last_plan_touch = m.sent_at

        # --- she asks what I'm doing / if I'm free, without naming a slot --
        # ("what are you up to this weekend?") = a buying signal, not a proposal
        if m.is_her and future and state not in PLAN_STATES and (
            HER_AVAIL_Q.search(m.text) or (L.SCHEDULE_ASK.search(m.text) and all(r.kind in ("weekend", "next_week") for r in future))
        ):
            if DATE_RANK.get(state, 0) < DATE_RANK["SCHEDULING"] or state in ("MET", "FLAKED", "RESCHEDULING"):
                state = "SCHEDULING"
            info.evidence.append(Evidence("SCHEDULING", [m.idx], m.text[:120]))
            last_plan_touch = m.sent_at
            continue

        # --- new proposals ------------------------------------------------
        plan_context = (
            L.SOFT_CLOSE.search(m.text) or L.SCHEDULE_ASK.search(m.text)
            or state in ("INTEREST", "SCHEDULING", "PROPOSED", "RESCHEDULING", "FLAKED") or state in PLAN_STATES
            or (m.is_her and L.ASKS_WHEN.search(m.text))
        )
        if future and plan_context and not is_frame_only and not L.CONFIRM_Q.search(m.text):
            if state in PLAN_STATES and when is not None and any(r.day == local(when, tz_of(m)).date() for r in future):
                pass  # restating the agreed day
            else:
                pending_prop = (m, future)
                if state not in PLAN_STATES:
                    state = "PROPOSED"
                last_plan_touch = m.sent_at
        elif L.SOFT_CLOSE.search(m.text) and not is_frame_only:
            pending_idea = m
            if m.is_her and DATE_RANK.get(state, 0) < DATE_RANK["INTEREST"]:
                # she proposes meeting herself ("when are we hanging out?") = interest
                state = "INTEREST"
                info.evidence.append(Evidence("INTEREST", [m.idx], m.text[:120]))
                pending_idea = None
                last_plan_touch = m.sent_at
        if m.is_me and L.SCHEDULE_ASK.search(m.text):
            schedule_asked_by_me = True
            if state == "NONE":
                pending_idea = pending_idea or m
        if m.is_her and L.ASKS_WHEN.search(m.text) and DATE_RANK.get(state, 0) < DATE_RANK["INTEREST"]:
            state = "INTEREST"
            info.evidence.append(Evidence("INTEREST", [m.idx], m.text[:120]))
            last_plan_touch = m.sent_at

    # --- expiry and decay against NOW ------------------------------------
    if state == "EN_ROUTE" and when is not None and now > when + timedelta(hours=PLAN_EXPIRY_H):
        # she was on her way / outside: the date almost certainly happened
        info.notes.append("she was on her way for the date: assuming it happened")
        info.evidence.append(Evidence("MET", [], "assumed: she was on her way"))
        state = "MET"
    elif state in PLAN_STATES and when is not None and now > when + timedelta(hours=PLAN_EXPIRY_H):
        if state == "CONFIRMED_DAYOF":
            info.notes.append(f"date on {human(when, her_tz)} was confirmed and has passed; unknown if it happened")
            info.outcome_unknown = True
            info.outcome_when = when
        else:
            info.notes.append(f"plan for {human(when, her_tz)} has passed with no sign it happened")
        info.expired_plan = True
        state, when = "INTEREST", None
    if state in ("AGREED", "REMINDED") and last_plan_touch and now - last_plan_touch > timedelta(days=AGREED_DECAY_DAYS) and when is None:
        info.notes.append("unconfirmed plan older than a week: re-close (she's at ~80%)")
        info.expired_plan = True
        state = "INTEREST"
    if state == "PROPOSED" and pending_prop is not None:
        age = _hours(pending_prop[0].sent_at, now)
        if age is not None and age > PROPOSAL_STALE_H:
            info.notes.append("proposal went unanswered for 48h+")
            if pending_prop[0].is_me:
                incidents.ignored_plan_asks += 1
            state = "INTEREST" if any(e.state == "INTEREST" for e in info.evidence) else "NONE"
    if state in ("FLAKED", "RESCHEDULING") and last_plan_touch and now - last_plan_touch > timedelta(days=FLAKE_RECOVERY_DAYS):
        info.notes.append("flake/reschedule is a week old: back to a fresh soft close")
        state = "INTEREST"
    if state == "INTEREST" and last_plan_touch and now - last_plan_touch > timedelta(days=INTEREST_STALE_DAYS):
        info.notes.append("interest is stale (3+ weeks): soft close again")

    info.had_plan = any(e.state in ("AGREED", "CONFIRMED_DAYOF", "MET") for e in info.evidence)
    if state == "PROPOSED" and pending_prop is not None:
        pm, prefs = pending_prop
        info.proposed_by = pm.speaker
        info.proposed_text = pm.text[:140]
        r0 = prefs[0] if prefs else None
        if r0 is not None and r0.day is not None:
            info.proposed_when = r0.as_datetime(tz_of(pm), default_hour=r0.hour if r0.hour is not None else 21)
    info.state = state
    info.when = when if state in PLAN_STATES or state == "MET" else None
    info.when_text = when_text if info.when else ""
    info.hour_known = hour_known if info.when else False
    return info


def _investment(her: list[Msg]) -> tuple[str, str, float, int]:
    if not her:
        return "low", "flat", 0.0, 0
    last = her[-5:]
    avg = sum(m.words for m in last) / len(last)
    qs = sum(1 for m in last if "?" in m.text)
    if avg >= 9 or qs >= 2:
        level = "high"
    elif avg <= 2.5 and qs == 0:
        level = "low"
    else:
        level = "medium"
    trend = "flat"
    if len(her) >= 6:
        a = sum(m.words for m in her[-3:]) / 3
        b = sum(m.words for m in her[-6:-3]) / 3
        if b and a > b * 1.3:
            trend = "rising"
        elif b and a < b * 0.7:
            trend = "falling"
    return level, trend, round(avg, 1), qs


def _latencies(msgs: list[Msg]) -> Optional[float]:
    vals = []
    for prev, cur in zip(msgs, msgs[1:]):
        if prev.is_me and cur.is_her and prev.time_known and cur.time_known:
            h = _hours(prev.sent_at, cur.sent_at)
            if h is not None and h >= 0:
                vals.append(h)
    return round(statistics.median(vals), 2) if vals else None


def _ignored_plan_asks(msgs: list[Msg], now: datetime, reliable: bool) -> int:
    if not reliable:
        return 0
    n = 0
    for i, m in enumerate(msgs):
        if not (m.is_me and _is_plan_ask(m.text)):
            continue
        nxt = next((x for x in msgs[i + 1:] if x.is_her), None)
        end = nxt.sent_at if nxt else now
        gap = _hours(m.sent_at, end)
        if gap is not None and gap >= 48:
            n += 1
    return n


def build_state(
    msgs: list[Msg],
    *,
    thread_id: str,
    now: datetime,
    her_tz: str,
    my_tz: str,
    platform: str = "",
    origin: str = "",
    flags: Optional[dict[str, Any]] = None,
) -> State:
    flags = flags or {}
    her = [m for m in msgs if m.is_her]
    me = [m for m in msgs if m.is_me]
    reliable = times_reliable(msgs)
    incidents = Incidents()
    date = compute_date(msgs, now, her_tz, my_tz, incidents)
    incidents.ignored_plan_asks += _ignored_plan_asks(msgs, now, reliable)
    incidents.busy_excuses = sum(1 for m in her if L.BUSY.search(m.text))

    her_last = her[-1] if her else None
    my_last = me[-1] if me else None
    my_unanswered = 0
    for m in reversed(msgs):
        if m.is_me:
            my_unanswered += 1
        else:
            break
    her_unanswered = 0
    for m in reversed(msgs):
        if m.is_her:
            her_unanswered += 1
        else:
            break
    incidents.ignored_reengages = max(0, my_unanswered - 1)

    level, trend, avg, qs = _investment(her)
    exchanges = sum(1 for a, b in zip(msgs, msgs[1:]) if a.speaker != b.speaker)

    # positive run since my last plan ask
    last_close_idx = -1
    for m in msgs:
        if m.is_me and _is_plan_ask(m.text):
            last_close_idx = m.idx
    run = 0
    for m in msgs[last_close_idx + 1:]:
        if m.is_her:
            hh = L.heuristics(m.text)
            if not hh["negative"] and (m.words >= 2 or hh["laughing"]):
                run += 1
            else:
                run = 0
    our_date_uses = sum(1 for m in me[-10:] if L.FRAME_PRESUPPOSE.search(m.text))
    q_streak = 0
    for m in reversed(me):
        if "?" in m.text and len(m.text.split()) <= 14 and not L.FRAME_PRESUPPOSE.search(m.text):
            q_streak += 1
        else:
            break

    her_h = L.heuristics(her_last.text) if her_last else {}
    risk = {k: False for k in L.risk_flags("")}
    for m in her[-3:]:
        for k, v in L.risk_flags(m.text).items():
            if v and (m is her_last or k in ("minor", "scam", "stop", "serious")):
                risk[k] = True
    risk["times_reliable"] = reliable

    # stage
    since_her = _hours(her_last.sent_at, now) if her_last else None
    since_me = _hours(my_last.sent_at, now) if my_last else None
    ds = date.state
    if flags.get("closed"):
        stage = "closed"
    elif not me:
        stage = "opening"
    elif ds == "MET":
        stage = "post_date"
    elif ds in PLAN_STATES:
        stage = "date_set"
    elif ds in ("FLAKED", "RESCHEDULING"):
        stage = "flake_recovery"
    elif ds in ("PROPOSED", "SCHEDULING", "INTEREST"):
        stage = "closing"
    elif exchanges < 4:
        stage = "early"
    else:
        stage = "vibing"
    if stage not in ("closed", "date_set") and my_unanswered >= 1 and since_me is not None and since_me >= 24 * 7 and reliable:
        stage = "dormant"

    return State(
        thread_id=thread_id,
        now=now,
        her_tz=her_tz,
        my_tz=my_tz,
        platform=platform,
        origin=origin,
        n_msgs=len(msgs),
        n_her=len(her),
        n_me=len(me),
        times_known=reliable,
        last_speaker=msgs[-1].speaker if msgs else "",
        her_last=her_last,
        my_last=my_last,
        since_her_last_h=round(since_her, 2) if since_her is not None else None,
        since_my_last_h=round(since_me, 2) if since_me is not None else None,
        my_unanswered=my_unanswered,
        her_unanswered=her_unanswered,
        date=date,
        incidents=incidents,
        investment=level,
        investment_trend=trend,
        her_avg_words=avg,
        her_questions_recent=qs,
        her_median_latency_h=_latencies(msgs),
        stage=stage,
        exchanges=exchanges,
        positive_run=run,
        my_plan_asks=sum(1 for m in me if _is_plan_ask(m.text)),
        soft_close_attempted=any(L.SOFT_CLOSE.search(m.text) for m in me),
        last_close_idx=last_close_idx,
        our_date_uses_recent=our_date_uses,
        my_question_streak=q_streak,
        her_msg_heuristics=her_h,
        risk=risk,
        her_local_now=human(now, her_tz),
        is_first_contact=not me,
        she_opened=bool(msgs) and msgs[0].is_her,
        troll_active=bool(flags.get("troll_active")),
        closed=bool(flags.get("closed")),
        my_tail=[m.text for m in msgs[len(msgs) - my_unanswered:]] if my_unanswered else [],
    )
