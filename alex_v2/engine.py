"""Alex v2 engine: one decision per call.

    decision = await decide_async(messages, thread_id=..., platform="hinge")
    if decision.should_send:
        send(decision.text)
        mark_sent(thread_id, decision.text)

Pipeline (see README): normalise -> state (evidence-based date machine) ->
policy (guardrails, timers, legal moves) -> ONE model call (reading of her
message + candidates) or a canon template -> model reading can only RESTRICT
-> deterministic critic -> best candidate -> human timing -> log.

Actions:
  send     send ``text`` now (then call ``mark_sent``)
  wait     nothing to send yet; call again at ``due_at`` (a reply is written and
           scheduled) or ``follow_up_at`` (next timer). ``text`` shows the
           scheduled reply, if any. Do NOT send it early.
  handoff  a human should handle this (``handoff_reason``; ``suggested_text``).
           Neither v1 nor v2 should send on this thread until the chat moves.
  none     nothing to do (closed, paused, walked away, she said stop).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import difflib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from . import critic
from . import facts as F
from . import llm as LLM
from . import prompt as P
from . import settings as S
from . import store as ST
from . import templates as T
from . import trace as TR
from .models import PLAN_STATES, Decision, Msg, State
from .moves import BY_ID, Ctx
from .normalize import normalize_messages
from .policy import allowed_after, decide_policy, used_moves
from .state import build_state
from .timeparse import human, local
from .timing import _rng, apply_quiet_hours, reply_due

UTC = timezone.utc
HANDOUT_GRACE = timedelta(minutes=30)
PENDING_MAX_AGE = timedelta(hours=8)     # a scheduled reply older than this is rewritten, never sent stale
MAX_HANDOUTS = 2
RANK_BONUS = (8, 5, 2)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _parse(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return _aware(v)
    try:
        return _aware(datetime.fromisoformat(str(v)))
    except Exception:
        return None


def _loose(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def _same_text(a: str, b: str) -> bool:
    la, lb = _loose(a), _loose(b)
    if not la or not lb:
        return (a or "").strip() == (b or "").strip()
    return la == lb or difflib.SequenceMatcher(None, la, lb).ratio() >= 0.88


@dataclass
class Cand:
    move_id: str
    text: str
    raw_text: str
    ok: bool
    score: float
    fails: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source: str = "model"

    def to_log(self) -> dict[str, Any]:
        return {"move": self.move_id, "text": self.text, "ok": self.ok, "score": round(self.score, 1),
                "fails": self.fails, "notes": self.notes, "source": self.source}


def _state_summary(s: State) -> dict[str, Any]:
    d = s.date
    return {
        "stage": s.stage,
        "date_state": d.state,
        "date_when": human(d.when, s.her_tz) if d.when else "",
        "date_evidence": [{"state": e.state, "msgs": [i + 1 for i in e.msg_idx], "quote": e.quote} for e in d.evidence[-3:]],
        "date_notes": d.notes[-3:],
        "expired_plan": d.expired_plan,
        "investment": f"{s.investment} ({s.investment_trend})",
        "exchanges": s.exchanges,
        "my_unanswered": s.my_unanswered,
        "since_her_last_h": s.since_her_last_h,
        "since_my_last_h": s.since_my_last_h,
        "incidents": {k: v for k, v in s.incidents.__dict__.items() if v},
        "times_known": s.times_known,
        "her_local_now": s.her_local_now,
        "risk": {k: v for k, v in s.risk.items() if v and k != "times_reliable"},
    }


# ---------------------------------------------------------------------------
# sent-message bookkeeping
# ---------------------------------------------------------------------------

def _record_sent(store: dict[str, Any], entry: dict[str, Any], state_exchanges: Optional[int] = None) -> None:
    flags = store["flags"]
    log = store["sent_log"]
    if entry.get("trace_id") and any(e.get("trace_id") == entry["trace_id"] for e in log[-20:]):
        return
    log.append(entry)
    del log[:-300]
    mid = entry.get("move_id") or ""
    if mid == "troll_start":
        flags["troll_active"] = True
        flags["troll_started_ex"] = state_exchanges or 0
    elif mid == "troll_close":
        flags["troll_active"] = False
    if mid == "reengage_sweep":
        flags["sweeps_sent"] = int(flags.get("sweeps_sent", 0)) + 1


def _reconcile_pending(store: dict[str, Any], msgs: list[Msg], now: datetime) -> None:
    """If our pending message now appears in the thread, it was sent."""
    pend = store.get("pending")
    if not pend:
        return
    idx = next((i for i, m in enumerate(msgs) if m.fp == pend.get("after_fp")), None)
    later = msgs[idx + 1:] if idx is not None else msgs[-6:]
    for m in later:
        if m.is_me and _same_text(m.text, pend.get("text", "")):
            at = _parse(pend.get("handed_out_at")) or (m.sent_at if m.time_known else None) or now
            _record_sent(store, {"text": pend["text"], "move_id": pend.get("move_id", ""), "trace_id": pend.get("trace_id", ""),
                                 "sent_at": at.isoformat(), "how": "seen_in_thread", "exact": bool(pend.get("handed_out_at"))})
            store["pending"] = None
            return


def _apply_sent_times(store: dict[str, Any], msgs: list[Msg]) -> None:
    """Our own messages: use the time we actually sent them when the scraper has no clock."""
    log = [(_loose(e.get("text", "")), _parse(e.get("sent_at"))) for e in store["sent_log"][-80:] if e.get("exact") and e.get("sent_at")]
    if not log:
        return
    used_times: set[str] = set()
    for m in reversed(msgs[-80:]):
        if not m.is_me or m.time_known:
            continue
        lm = _loose(m.text)
        for lt, at in reversed(log):
            if at is None or lt != lm or at.isoformat() in used_times:
                continue
            m.sent_at, m.time_known = at, True
            store["msg_times"][m.fp] = at.isoformat()
            used_times.add(at.isoformat())
            break


def _sends_today(store: dict[str, Any], her_tz: str, now: datetime) -> int:
    today = local(now, her_tz).date()
    n = 0
    for e in store["sent_log"][-50:]:
        at = _parse(e.get("sent_at"))
        if at and local(at, her_tz).date() == today:
            n += 1
    return n


# ---------------------------------------------------------------------------
# candidate evaluation
# ---------------------------------------------------------------------------

def _evaluate(text: str, move_id: str, ctx: Ctx, my_recent: list[str], menu_ids: set[str], source: str,
              rank: int = 9, date_doubt: bool = False) -> Cand:
    t = critic.postprocess(text, ctx.settings)
    fails: list[str] = []
    if move_id not in menu_ids:
        fails.append(f"move '{move_id}' was not offered")
    else:
        ok, why = allowed_after(move_id, ctx)
        if not ok:
            fails.append(why)
    v = critic.check(t, move_id if move_id in BY_ID else "answer_and_pivot", ctx, my_recent)
    fails += v.fails
    if date_doubt and ((BY_ID.get(move_id) and BY_ID[move_id].presupposes_plan) or critic.PLAN_PHRASES.search(t)):
        fails.append("the plan looks uncertain in the thread; not presupposing it")
    score = v.score + (RANK_BONUS[rank] if rank < len(RANK_BONUS) else 0)
    return Cand(move_id, t, text, not fails, score, fails, v.notes, source)


def _best(cands: list[Cand]) -> Optional[Cand]:
    ok = [c for c in cands if c.ok]
    return max(ok, key=lambda c: c.score) if ok else None


def _templated(move_id: str, ctx: Ctx, my_recent: list[str], menu_ids: set[str], seed: str, source: str) -> tuple[Optional[Cand], list[Cand]]:
    tried = []
    for line in T.lines_for(move_id, ctx, seed):
        c = _evaluate(line, move_id, ctx, my_recent, menu_ids | {move_id}, source)
        tried.append(c)
        if c.ok:
            return c, tried
    return None, tried


# ---------------------------------------------------------------------------
# the model's reading of her message may only restrict
# ---------------------------------------------------------------------------

def _apply_analysis(ctx: Ctx, a: dict[str, Any], pol, flags: dict[str, Any], st: dict[str, Any]) -> tuple[Optional[Decision], bool]:
    fl = set(a.get("flags") or [])
    s = ctx.s
    if "minor" in fl:
        flags["paused"] = True
        return Decision("handoff", handoff_reason="The model flagged a possible minor. v2 paused this thread; please check her age.",
                        reason="possible minor (model)"), False
    if "stop" in fl and a.get("her_msg_type") not in ("shit_test", "playful"):
        flags["paused"] = True
        return Decision("handoff", handoff_reason="She may have said she's not interested / to stop. v2 paused this thread. "
                        "Resume it on the Alex v2 page if that's wrong.", reason="possible stop (model)"), False
    if "scam" in fl and not s.risk.get("scam"):
        flags["paused"] = True
        return Decision("handoff", handoff_reason="Possible scam/bot (money, links, verification). v2 paused this thread.",
                        reason="possible scam (model)"), False
    if a.get("serious_event") and st.get("handoff_serious", True) and not pol.handoff_after:
        pol.handoff_after = True
        pol.handoff_reason = "She shared something serious. Suggested sincere reply below; please send it yourself."
    if ctx.trigger == "reply" and a.get("cancelled") and s.date.state in PLAN_STATES and not s.her_msg_heuristics.get("decline"):
        return Decision("handoff", handoff_reason=f"She may be cancelling or moving the plan ({s.date.when_text or 'agreed date'}). "
                        "v2's date tracker didn't catch it, so it won't guess. Please reply yourself.",
                        reason="date disagreement: model reads a cancel"), False
    if "troll_running" in fl and not flags.get("troll_active"):
        flags["troll_active"] = True
        flags["troll_started_ex"] = s.exchanges
    date_doubt = s.date.state in PLAN_STATES and a.get("date_view") in ("none", "idea", "cancelled")
    return None, date_doubt


def _skip_ok(ctx: Ctx, a: dict[str, Any], st: dict[str, Any], flags: dict[str, Any], now: datetime) -> bool:
    if not st.get("allow_skip", True) or ctx.trigger != "reply" or not a.get("skip"):
        return False
    s = ctx.s
    h = ctx.ht
    if not h.get("low_effort") or h.get("is_question") or h.get("asks_about_me"):
        return False
    if a.get("her_msg_type") not in ("low_effort", "statement", "playful"):
        return False
    if s.date.state in PLAN_STATES | {"PROPOSED", "SCHEDULING", "RESCHEDULING"}:
        return False
    if s.exchanges < 4 or s.her_unanswered != 1 or ctx.flags.get("reopen_after_skip"):
        return False
    last = _parse(flags.get("last_skip_at"))
    if last and now - last < timedelta(days=3):
        return False
    return True


# ---------------------------------------------------------------------------
# main entry points
# ---------------------------------------------------------------------------

async def decide_async(
    messages: list[Any],
    *,
    thread_id: str,
    contact_name: str = "",
    platform: str = "",
    origin: str = "",
    now: Optional[datetime] = None,
    her_tz: Optional[str] = None,
    llm: Optional[Callable[..., Any]] = None,
    her_profile: str = "",
    namespace: Optional[str] = None,
    arm: str = "v2",
    timestamps: str = "seen",
    dry_run: bool = False,
    explain_only: bool = False,
) -> Decision:
    t0 = time.monotonic()
    st = S.load()
    facts = F.load()
    ns = namespace or ("shadow" if arm == "shadow" else (st.get("namespace") or "live"))
    trace_id = TR.new_trace_id()
    now = _aware(now) or datetime.now(UTC)
    store = ST.load(thread_id, ns)
    flags = store["flags"]
    my_tz = facts.get("timezone") or "Europe/London"
    if her_tz:
        flags["her_tz"] = her_tz
    her_tz = her_tz or flags.get("her_tz") or my_tz

    msgs, new_times = normalize_messages(messages, now=now, user_tz=my_tz, stored_times=store["msg_times"], timestamps=timestamps)
    store["msg_times"].update(new_times)
    _reconcile_pending(store, msgs, now)
    _apply_sent_times(store, msgs)
    if msgs and msgs[-1].is_her and flags.get("sweeps_sent"):
        flags["sweeps_sent"] = 0
    tail_fp = msgs[-1].fp if msgs else ""
    if flags.get("needs_you") and flags["needs_you"].get("tail_fp") != tail_fp:
        flags.pop("needs_you", None)   # the chat moved on (you replied, or she wrote again)

    state = build_state(msgs, thread_id=thread_id, now=now, her_tz=her_tz, my_tz=my_tz,
                        platform=platform, origin=origin, flags=flags)
    if flags.get("troll_active") and state.exchanges - int(flags.get("troll_started_ex", 0)) > 5:
        flags["troll_active"] = False
        state.troll_active = False
    if arm == "v2" and ns == "live" and not dry_run:
        try:
            TR.observe(thread_id, "v2", msgs, state, now, ns)
        except Exception:
            pass

    log: dict[str, Any] = {"trace_id": trace_id, "thread_key": TR.thread_key(thread_id), "contact": contact_name,
                           "platform": platform, "namespace": ns, "arm": arm, "mode": st.get("mode"),
                           "her_last": (state.her_last.text[:200] if state.her_last else ""), "llm_calls": 0}

    def finish(dec: Decision) -> Decision:
        dec.arm = arm
        dec.trace_id = trace_id
        dec.state = _state_summary(state)
        if dec.action == "handoff":
            flags["needs_you"] = {"reason": dec.handoff_reason, "suggested": dec.suggested_text, "at": now.isoformat(),
                                  "tail_fp": tail_fp, "contact": contact_name, "her_last": log["her_last"]}
        nxt = dec.due_at if (dec.action == "wait" and dec.due_at) else dec.follow_up_at
        flags["next_check_at"] = nxt.isoformat() if nxt else None
        flags["contact"] = contact_name or flags.get("contact", "")
        flags["platform"] = platform or flags.get("platform", "")
        flags["last_decision"] = {"at": now.isoformat(), "action": dec.action, "reason": dec.reason[:200], "text": dec.text or dec.suggested_text}
        if not dry_run:
            ST.save(thread_id, store, ns)
        log.update({"action": dec.action, "reason": dec.reason, "move": dec.move_id, "text": dec.text or "",
                    "suggested": dec.suggested_text, "handoff_reason": dec.handoff_reason,
                    "due_at": dec.due_at.isoformat() if dec.due_at else None,
                    "follow_up_at": dec.follow_up_at.isoformat() if dec.follow_up_at else None,
                    "follow_up_reason": dec.follow_up_reason, "state": dec.state,
                    "latency_ms": int((time.monotonic() - t0) * 1000)})
        dec.debug.update({k: log.get(k) for k in ("menu", "analysis", "candidates", "model", "error", "llm_calls", "latency_ms", "trigger")})
        if not explain_only:
            try:
                TR.log_decision(log)
            except Exception:
                pass
        return dec

    # --- per-thread holds -------------------------------------------------
    if flags.get("paused"):
        return finish(Decision("none", reason="paused for this thread (resume on the Alex v2 page)"))
    if flags.get("needs_you"):
        nu = flags["needs_you"]
        return finish(Decision("handoff", handoff_reason=nu.get("reason", ""), suggested_text=nu.get("suggested", ""),
                               reason="still waiting for you on this thread"))

    # --- a reply already written and scheduled ---------------------------
    pend = store.get("pending")
    if pend and not explain_only:
        created = _parse(pend.get("created_at"))
        stale = created is not None and now - created > PENDING_MAX_AGE and not pend.get("handed_out_at")
        if pend.get("after_fp") == tail_fp and arm != "shadow" and not stale:
            due = _parse(pend.get("due_at")) or now
            ho = _parse(pend.get("handed_out_at"))
            if ho is not None:
                if now - ho < HANDOUT_GRACE:
                    return finish(Decision("wait", text=pend["text"], move_id=pend.get("move_id", ""), follow_up_at=ho + HANDOUT_GRACE,
                                           reason="already handed out for sending; waiting to see it in the thread"))
                if int(pend.get("handouts", 0)) >= MAX_HANDOUTS:
                    store["pending"] = None
                    return finish(Decision("handoff", suggested_text=pend["text"], move_id=pend.get("move_id", ""),
                                           handoff_reason="v2 handed this message out twice but never saw it in the chat. Please check the thread.",
                                           reason="send not confirmed"))
            if now >= due:
                pend["handed_out_at"] = now.isoformat()
                pend["handouts"] = int(pend.get("handouts", 0)) + 1
                return finish(Decision("send", text=pend["text"], messages=[pend["text"]], move_id=pend.get("move_id", ""),
                                       due_at=due, reason="scheduled reply is due"))
            return finish(Decision("wait", text=pend["text"], move_id=pend.get("move_id", ""), due_at=due,
                                   send_after_s=int((due - now).total_seconds()), reason="reply written; waiting for a human-like moment"))
        store["pending"] = None   # superseded: she wrote again, or you replied yourself

    # --- context + policy ------------------------------------------------
    trigger = "reply" if (not msgs or msgs[-1].is_her) else "followup"
    log["trigger"] = trigger
    used = used_moves([m.text for m in msgs if m.is_me], [e.get("move_id", "") for e in store["sent_log"]])
    ctx = Ctx(state, facts, st, trigger, used, {}, dict(flags))

    if trigger == "reply" and state.her_last is not None and flags.get("skip_fp") == state.her_last.fp:
        until = _parse(flags.get("skip_until"))
        if until and now < until and not explain_only:
            return finish(Decision("wait", follow_up_at=until, reason="letting her low-value message sit for a while (canon), then reopening fresh"))
        ctx.flags["reopen_after_skip"] = True
    retry_until = _parse(flags.get("retry_after"))
    if retry_until and flags.get("retry_fp") == tail_fp and now < retry_until and not explain_only:
        return finish(Decision("wait", follow_up_at=retry_until, reason="last attempt produced nothing sendable; retrying later"))

    pol = decide_policy(ctx, _sends_today(store, her_tz, now))
    log["policy"] = pol.reason
    log["menu"] = [m.id for m in pol.menu]
    if pol.close_thread and pol.action != "generate":
        flags["closed"] = True
    if pol.action == "none":
        return finish(Decision("none", reason=pol.reason))
    if pol.action == "wait":
        return finish(Decision("wait", follow_up_at=pol.follow_up_at, follow_up_reason=pol.follow_up_reason, reason=pol.reason))
    if pol.action == "handoff":
        if state.date.outcome_unknown and state.date.outcome_when is not None:
            flags["outcome_asked"] = state.date.outcome_when.isoformat()
        return finish(Decision("handoff", handoff_reason=pol.handoff_reason, reason=pol.reason))

    menu_ids = {m.id for m in pol.menu}
    my_recent = [m.text for m in msgs if m.is_me][-12:]
    seed = f"{thread_id}|{tail_fp}"
    cands: list[Cand] = []
    chosen: Optional[Cand] = None
    analysis: dict[str, Any] = {}
    error = ""
    llm_obj = None

    # 1) canon template (timer follow-ups): free and exact
    top = pol.menu[0]
    if trigger == "followup" and st.get("template_followups", True) and top.id in T.TEMPLATES and not explain_only:
        chosen, tried = _templated(top.id, ctx, my_recent, menu_ids, seed, "template")
        cands += tried

    # 2) one model call
    if chosen is None:
        n = max(1, min(6, int(st.get("candidates", 4))))
        system, user = P.build(ctx, msgs, pol.menu, contact_name=contact_name, her_profile=her_profile,
                               followup_reason=pol.follow_up_reason, n_candidates=n,
                               context_messages=int(st.get("context_messages", 30)))
        if st.get("log_prompts") or ns.startswith("lab") or explain_only:
            log["prompt"] = {"system": system, "user": user}
        if explain_only:
            d = Decision("none", reason="explain only (no model call)")
            d.debug["system"], d.debug["user"] = system, user
            return finish(d)
        llm_obj = llm or LLM.make_llm(st)
        raw = ""
        try:
            raw = await LLM.call(llm_obj, system, user, st)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
        log["llm_calls"] += 1
        parsed = P.parse(raw)
        analysis = parsed["analysis"]
        ctx.a = analysis
        early, date_doubt = _apply_analysis(ctx, analysis, pol, flags, st) if raw else (None, False)
        log["analysis"] = analysis
        log["raw"] = raw[:1500]
        dv, ds_now = analysis.get("date_view"), state.date.state
        if dv and ((dv in ("agreed",) and ds_now not in PLAN_STATES and ds_now != "MET")
                   or (dv in ("none", "idea") and ds_now in PLAN_STATES)):
            log["date_disagreement"] = f"model reads '{dv}', tracker says {ds_now}"
        if early is not None:
            log["model"] = getattr(llm_obj, "last_model", "")
            return finish(early)
        if raw and _skip_ok(ctx, analysis, st, flags, now):
            r = _rng(thread_id, tail_fp, "skip")
            until = apply_quiet_hours(now + timedelta(hours=r.uniform(14, 26)), ctx, r)
            flags["skip_fp"] = state.her_last.fp if state.her_last else ""
            flags["skip_until"] = until.isoformat()
            flags["last_skip_at"] = now.isoformat()
            log["model"] = getattr(llm_obj, "last_model", "")
            return finish(Decision("wait", follow_up_at=until, reason="her message is a tiny ack; leaving it for a while, then opening something fresh"))
        for i, c in enumerate(parsed["candidates"][: n + 2]):
            cands.append(_evaluate(c["text"], c["move_id"], ctx, my_recent, menu_ids, "model", i, date_doubt))
        chosen = _best(cands)

        # 3) one repair call with the critic's feedback
        if chosen is None and st.get("repair_call", True) and raw:
            rejected = [(c.move_id, c.text, c.fails) for c in cands if not c.ok]
            user2 = P.repair_prompt(user, rejected, n)
            raw2 = ""
            try:
                raw2 = await LLM.call(llm_obj, system, user2, st)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
            log["llm_calls"] += 1
            parsed2 = P.parse(raw2)
            if not analysis.get("her_msg_type") and parsed2["analysis"].get("her_msg_type"):
                analysis = parsed2["analysis"]
                ctx.a = analysis
                log["analysis"] = analysis
                early, date_doubt = _apply_analysis(ctx, analysis, pol, flags, st)
                if early is not None:
                    return finish(early)
            for i, c in enumerate(parsed2["candidates"][: n + 2]):
                cands.append(_evaluate(c["text"], c["move_id"], ctx, my_recent, menu_ids, "repair", i, date_doubt))
            chosen = _best(cands)
        log["model"] = getattr(llm_obj, "last_model", "") or ""

    # 4) canon fallback when the model couldn't produce anything usable
    if chosen is None:
        for m in pol.menu:
            if m.id in T.TEMPLATES or m.id in T.FALLBACK_LINES:
                if not allowed_after(m.id, ctx)[0]:
                    continue
                c, tried = _templated(m.id, ctx, my_recent, menu_ids, seed, "template_fallback")
                cands += tried
                if c is not None:
                    chosen = c
                    break

    log["candidates"] = [c.to_log() for c in cands]
    log["error"] = error

    if chosen is None:
        best_failed = max(cands, key=lambda c: c.score) if cands else None
        fails = int(flags.get("gen_failures", 0)) + 1
        flags["gen_failures"] = fails
        time_sensitive = ctx.plan_today or state.date.state in ("PROPOSED", "RESCHEDULING", "EN_ROUTE", "CONFIRMED_DAYOF")
        if time_sensitive or fails >= 3:
            flags["gen_failures"] = 0
            why = f"model error: {error}" if error else "no candidate passed the checks"
            return finish(Decision("handoff", suggested_text=best_failed.text if best_failed else "",
                                   handoff_reason=f"v2 couldn't write a reply it trusts ({why}). Please reply yourself.",
                                   reason="generation failed"))
        retry = now + timedelta(minutes=20 * fails)
        flags["retry_after"] = retry.isoformat()
        flags["retry_fp"] = tail_fp
        return finish(Decision("wait", follow_up_at=retry, reason=f"nothing sendable yet ({'model error' if error else 'all candidates failed checks'}); retrying"))

    flags["gen_failures"] = 0
    flags.pop("retry_after", None)
    text, move_id = chosen.text, chosen.move_id
    why = f"{move_id} ({chosen.source})"
    if analysis.get("thread_to_answer"):
        why += f"; answering: {analysis['thread_to_answer']}"

    if pol.handoff_after:
        return finish(Decision("handoff", suggested_text=text, move_id=move_id, handoff_reason=pol.handoff_reason, reason=why))

    if arm == "shadow":
        d = Decision("none", suggested_text=text, move_id=move_id, reason="shadow: v1 sends; v2 would send this")
        d.due_at = reply_due(ctx, move_id) if trigger == "reply" else now
        return finish(d)

    due = reply_due(ctx, move_id) if trigger == "reply" else now
    store["pending"] = {"text": text, "move_id": move_id, "after_fp": tail_fp, "due_at": due.isoformat(),
                        "created_at": now.isoformat(), "trace_id": trace_id, "handed_out_at": None, "handouts": 0,
                        "kind": trigger, "followup_reason": pol.follow_up_reason}
    if due <= now + timedelta(seconds=5):
        store["pending"]["handed_out_at"] = now.isoformat()
        store["pending"]["handouts"] = 1
        return finish(Decision("send", text=text, messages=[text], move_id=move_id, due_at=due,
                               follow_up_reason=pol.follow_up_reason, reason=why))
    return finish(Decision("wait", text=text, move_id=move_id, due_at=due, send_after_s=int((due - now).total_seconds()),
                           follow_up_reason=pol.follow_up_reason, reason=why + "; scheduled at a human-like delay"))


def decide(messages: list[Any], **kw: Any) -> Decision:
    """Sync wrapper around :func:`decide_async` (safe to call from inside a running loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(decide_async(messages, **kw))
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, decide_async(messages, **kw)).result()


def mark_sent(thread_id: str, text: str, *, sent_at: Optional[datetime] = None, namespace: Optional[str] = None,
              move_id: str = "") -> None:
    """Tell v2 a message went out (exact send time keeps every timer right)."""
    st = S.load()
    ns = namespace or st.get("namespace") or "live"
    store = ST.load(thread_id, ns)
    at = _aware(sent_at) or datetime.now(UTC)
    pend = store.get("pending")
    entry = {"text": text, "move_id": move_id, "trace_id": "", "sent_at": at.isoformat(), "how": "mark_sent", "exact": True}
    if pend and _same_text(pend.get("text", ""), text):
        entry["move_id"] = move_id or pend.get("move_id", "")
        entry["trace_id"] = pend.get("trace_id", "")
        store["pending"] = None
    _record_sent(store, entry)
    ST.save(thread_id, store, ns)


def thread_control(thread_id: str, action: str, namespace: Optional[str] = None) -> dict[str, Any]:
    """pause | resume | reopen | clear_handoff | clear_pending for one thread."""
    st = S.load()
    ns = namespace or st.get("namespace") or "live"
    store = ST.load(thread_id, ns)
    fl = store["flags"]
    if action == "pause":
        fl["paused"] = True
    elif action == "resume":
        fl["paused"] = False
        fl.pop("needs_you", None)
    elif action == "reopen":
        fl["closed"] = False
        fl["paused"] = False
        fl["sweeps_sent"] = 0
    elif action == "clear_handoff":
        fl.pop("needs_you", None)
    elif action == "clear_pending":
        store["pending"] = None
    ST.save(thread_id, store, ns)
    return fl


def due_threads(now: Optional[datetime] = None, namespace: str = "live") -> list[dict[str, Any]]:
    """Threads v2 wants looked at now (a scheduled reply or a follow-up timer is due)."""
    now = _aware(now) or datetime.now(UTC)
    out = []
    for data in ST.list_threads(namespace):
        fl = data.get("flags") or {}
        if fl.get("paused") or fl.get("closed") or fl.get("needs_you"):
            continue
        nxt = _parse(fl.get("next_check_at"))
        if nxt is not None and nxt <= now:
            out.append({"thread_id": data.get("thread_id"), "contact": fl.get("contact", ""), "platform": fl.get("platform", ""),
                        "due": nxt.isoformat(), "pending": bool(data.get("pending"))})
    return out
