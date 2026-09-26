"""Policy: guardrails -> timing -> the menu of moves that are legal right now.

The model only ever sees moves whose deterministic preconditions hold. After
the model answers, ``allowed_after`` re-checks each candidate's move against
the model's own reading of her message (sincere? shit test? sexual light?),
so a takeaway can never go out in reply to a sincere message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .models import PLAN_STATES
from .moves import BY_ID, MOVES, USED_PATTERNS, Ctx, Move

MENU_SIZE = 6          # forward-moving moves shown to the model (reactive answers are always all offered)

ADDRESS_RX = re.compile(r"\b(your address|what'?s the address|address\?|where do (you|u) live|which (building|apartment|unit)|what'?s your (place|unit))\b", re.I)


@dataclass
class PolicyResult:
    action: str = "generate"          # "generate" | "wait" | "none" | "handoff"
    reason: str = ""
    menu: list[Move] = field(default_factory=list)
    handoff_after: bool = False       # generate a suggestion, but hand it to you instead of sending
    handoff_reason: str = ""
    follow_up_at: Optional[datetime] = None
    follow_up_reason: str = ""
    close_thread: bool = False


def used_moves(msgs_me_texts: list[str], sent_log_moves: list[str]) -> set[str]:
    used = set(sent_log_moves)
    for mid, rx in USED_PATTERNS.items():
        if any(rx.search(t) for t in msgs_me_texts):
            used.add(mid)
    return used


def decide_policy(ctx: Ctx, sends_today: int) -> PolicyResult:
    s = ctx.s
    st = ctx.settings

    # --- hard stops -------------------------------------------------------
    if s.closed:
        return PolicyResult(action="none", reason="thread closed")
    if s.risk.get("minor"):
        return PolicyResult(action="handoff", reason="possible minor", handoff_reason="Possible minor (age/school signals). v2 will not reply. Please check and unmatch if so.", close_thread=True)
    if s.risk.get("stop"):
        return PolicyResult(action="none", reason="she asked to stop / not interested", close_thread=True)
    if s.risk.get("scam"):
        if "scam_decline" in ctx.used:
            return PolicyResult(action="none", reason="transactional/scam thread", close_thread=True)
        return PolicyResult(action="generate", reason="scam signals: one unbothered line then close", menu=[BY_ID["scam_decline"]], close_thread=True)
    if sends_today >= int(st.get("max_sends_per_thread_per_day", 8)):
        return PolicyResult(action="wait", reason="daily send cap for this thread reached")

    # --- a confirmed date passed in silence: only you know if it happened --
    d = s.date
    if d.outcome_unknown and d.outcome_when is not None and ctx.flags.get("outcome_asked") != d.outcome_when.isoformat():
        return PolicyResult(action="handoff", reason="confirmed date passed with no messages",
                            handoff_reason="The date was confirmed and its time has passed, but nothing in the chat says whether it happened. "
                            "Please send the next message yourself (e.g. a post-date callback, or a reschedule if she didn't show).")

    # --- follow-up (she hasn't replied / timers) ------------------------
    if ctx.trigger == "followup":
        from .timing import next_followup
        due, why = next_followup(ctx)
        if why == "walk_away":
            return PolicyResult(action="none", reason="no replies after double, triple and sweeps: walking away (no blowout)", close_thread=True)
        if due is None:
            return PolicyResult(action="wait", reason="nothing due", follow_up_at=None)
        if due > s.now:
            return PolicyResult(action="wait", reason=f"next: {why}", follow_up_at=due, follow_up_reason=why)
        menu = [m for m in MOVES if m.followup and _safe_pre(m, ctx)]
        if why == "sweep":
            menu = [m for m in menu if m.id in ("reengage_sweep",)] or menu
        if not menu:
            return PolicyResult(action="wait", reason=f"{why} due but no legal follow-up move", follow_up_at=None)
        menu.sort(key=lambda m: -m.priority)
        return PolicyResult(action="generate", reason=f"follow-up due: {why}", menu=menu[:MENU_SIZE], follow_up_reason=why)

    # --- reply ------------------------------------------------------------
    her_text = s.her_last.text if s.her_last else ""
    res = PolicyResult(action="generate", reason="reply to her newest message")
    if s.risk.get("serious") and st.get("handoff_serious", True):
        res.handoff_after = True
        res.handoff_reason = "She shared something serious. Suggested sincere reply below; please send it yourself."
        res.menu = [BY_ID["sincere_support"]]
        return res
    if s.risk.get("call_request") and st.get("handoff_calls", True):
        res.handoff_after = True
        res.handoff_reason = "She wants a call/FaceTime/voice note. Suggested text below if you'd rather reply by text."
    elif s.risk.get("photo_request") and st.get("handoff_photos", True):
        res.handoff_after = True
        res.handoff_reason = "She asked for a photo. Send one of your good pre-tested pics yourself (never a random one)."
    elif ADDRESS_RX.search(her_text) and st.get("handoff_address", True):
        res.handoff_after = True
        res.handoff_reason = "She's asking for your address/unit. Please send it yourself."

    legal = [m for m in MOVES if _safe_pre(m, ctx)]
    legal.sort(key=lambda m: -m.priority)
    reactive = [m for m in legal if m.reactive]
    forward = [m for m in legal if not m.reactive]
    top = forward[:MENU_SIZE]
    pivot = BY_ID["answer_and_pivot"]
    if not s.is_first_contact and pivot not in top:
        if len(top) >= MENU_SIZE:
            top[-1] = pivot
        else:
            top.append(pivot)
    res.menu = reactive + top
    return res


def _safe_pre(m: Move, ctx: Ctx) -> bool:
    try:
        return bool(m.pre(ctx))
    except Exception:
        return False


def allowed_after(move_id: str, ctx: Ctx) -> tuple[bool, str]:
    """Re-check a candidate's move with the model's analysis filled in."""
    m = BY_ID.get(move_id)
    if m is None:
        return False, f"unknown move {move_id}"
    try:
        if not m.pre(ctx):
            return False, f"{move_id}: preconditions no longer hold"
        if not m.post(ctx):
            return False, f"{move_id}: not valid for her message ({ctx.her_type}, light={ctx.light}, temp={ctx.temp})"
    except Exception as exc:
        return False, f"{move_id}: check error {exc}"
    if m.presupposes_plan and ctx.s.date.state not in PLAN_STATES:
        return False, f"{move_id}: presupposes a plan but none exists"
    return True, ""
