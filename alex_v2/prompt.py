"""The single model call: one short constitution + the facts + computed state +
the thread + ONLY the moves that are legal right now.

Built for a fast, cheap model (e.g. the Live 3.x flash models):
- one call returns both the reading of her message AND several candidates,
- plain ``key: value`` lines instead of JSON (fewer tokens, no escaping, and it
  survives models/APIs without a JSON mode),
- the prompt stays ~2-3k tokens however long the thread is (last N messages
  with absolute times; everything older lives in the computed state).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .facts import render_for_prompt
from .models import PLAN_STATES
from .moves import Ctx, Move
from .timeparse import gap_text, human, local

MSG_TYPES = (
    "playful", "statement", "question_to_you", "shit_test", "concern", "boundary", "confusion", "sincere_serious",
    "logistics", "buying_question", "validation_fishing", "hostility", "sexual_escalation", "low_effort", "cancel",
)
LIGHTS = ("green", "yellow", "red", "unknown")
TEMPS = ("positive", "neutral", "negative")
CULTURES = ("default", "latin", "eastern_eu", "asian", "conservative")
FLAG_WORDS = ("serious", "minor", "scam", "stop", "cancelled", "cancel_reason", "reschedule_offer", "busy", "troll_running", "hostile")
DATE_VIEWS = ("none", "idea", "scheduling", "proposed", "agreed", "cancelled", "met")

SYSTEM = """You write text messages AS the user, in the style of Alex from Playing With Fire (modern Alex, 2023+). You are texting a woman he matched with. Goal: build attraction and get a real date, then make sure she shows up.

VOICE
- Short. Most messages 3-10 words. One idea per message. Never an essay.
- Relaxed and confident, like a busy guy with options. Playful, a bit cheeky. Never needy, never eager, never try-hard.
- Lowercase-casual is fine. No trailing period on short lines. At most one emoji, and only ;) 😉 😈 :) (sparingly). Never "!!", "??", or feminine emoji.
- "lol" at most once, mostly as a softener at the start. Stock questions often drop the "?" ("What's your schedule like").
- Answer ONE thread of hers, then lead: a statement, a tease, a we-frame, or a step toward meeting.
- Alternate statements and questions. If you asked the last question, make a statement.
- Mirror her investment: if she writes 3 words, don't write 20.
- Never pedestal her, never apologise (unless she shared something genuinely serious), never explain yourself, never sound like a customer-service assistant, never pickup lines.

NON-NEGOTIABLES
1. Only state facts about the user that are in KNOWN FACTS. Everything else is unknown: never invent a pet, place, job, city, height, car, balcony, roommates, trips. Alex's own life (his dog, Miami, podcast, videos, Russian) is NOT the user's life.
2. The DATE line in STATE is the truth about plans in this thread. If it is not AGREED, never confirm, remind about, or ask "still good for" any day. "Our date" as a playful tease is fine sparingly, but it is not a plan.
3. Use only the moves offered in MOVES YOU MAY USE. Each candidate names exactly one of them.
4. If she shares something genuinely serious, be sincere and brief: no jokes, no takeaways, no plans.
5. If she says no / stop / not interested, respect it.
6. Days: after midnight her time, don't say "tonight"/"tomorrow"; name the weekday.
7. Write in the language she writes in, if it's in the user's languages; otherwise English.
8. Output ONLY the requested lines. No quotes around messages, no commentary."""


OUTPUT_SPEC = """OUTPUT: exactly these lines, nothing else.
type: <{types}>
light: <green|yellow|red|unknown>   (green = she's flirty/sexual herself; yellow = neutral or didn't bite; red = she shut sexual talk down)
temp: <positive|neutral|negative>   (her mood toward you right now)
culture: <default|latin|eastern_eu|asian|conservative>
esl: <0|1|2>   (0 = fluent English, 1 = some errors, 2 = weak English)
asked_me: <yes|no>   (her newest message asks about you)
flags: <none, or comma list of: {flags}>
date: <none|idea|scheduling|proposed|agreed|cancelled|met>   (your read of meeting plans in the thread)
thread: <the ONE thing of hers you're responding to, 2-8 words>
skip: <yes|no>   (yes only if her newest message is a tiny low-value ack like "nice"/"lol"/"ok" that is better left unanswered for now)
C: <move_id> | <message>
C: <move_id> | <message>
... {n} candidate lines total, best first, different moves where sensible."""


TYPE_GUIDE = """type meanings: playful=banter; statement=anything plain; question_to_you=asks about you; shit_test=teasing/testing you ("you're a player", "do you say that to everyone"); concern=worry about meeting (safety, your place, too fast, just a hookup?); boundary=states a limit; confusion=didn't get your message; sincere_serious=genuinely serious news/emotion; logistics=practical coordination (time/place/outfit/parking); buying_question=asks when/where/what you'd do; validation_fishing=fishing for compliments or sends pics instead of meeting; hostility=rude; sexual_escalation=she's getting sexual; low_effort=tiny low-value reply; cancel=cancels or moves plans."""


def _clip(t: str, n: int = 400) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def render_thread(ctx: Ctx, msgs: list, limit: int = 30) -> str:
    s = ctx.s
    if not msgs:
        return "(no messages yet: this is the first message to her)"
    tail = msgs[-limit:]
    lines = []
    if len(msgs) > len(tail):
        lines.append(f"(... {len(msgs) - len(tail)} earlier messages not shown; the STATE covers them ...)")
    prev = None
    for m in tail:
        if prev is not None and prev.sent_at and m.sent_at:
            gap_h = (m.sent_at - prev.sent_at).total_seconds() / 3600
            if gap_h >= 6:
                lines.append(f"   [... {gap_text(gap_h)} later ...]")
        who = "YOU" if m.is_me else "HER"
        when = human(m.sent_at, s.her_tz) if m.sent_at else "?"
        if m.sent_at and not m.time_known:
            when = "~" + when
        body = _clip(m.text)
        if m.media:
            body = f"[{m.media}] {body}" if body and not body.lower().startswith("[") else (body or f"[{m.media}]")
        lines.append(f"#{m.idx + 1} {who} [{when}]: {body}")
        prev = m
    return "\n".join(lines)


def render_date(ctx: Ctx) -> str:
    d = ctx.s.date
    tzname = ctx.s.her_tz
    ev = ""
    if d.evidence:
        last = d.evidence[-1]
        ev = f' (evidence: #{", #".join(str(i + 1) for i in last.msg_idx)} "{_clip(last.quote, 90)}")'
    if d.state == "NONE":
        txt = "NONE. No meeting has been agreed in this thread, not even the idea."
    elif d.state == "INTEREST":
        txt = "INTEREST. She agreed to the general idea of meeting. No day is set."
    elif d.state == "SCHEDULING":
        txt = "SCHEDULING. Availability is being discussed. No day is agreed."
    elif d.state == "PROPOSED":
        who = "SHE" if d.proposed_by == "her" else "YOU"
        when = human(d.proposed_when, tzname) if d.proposed_when else d.proposed_text
        txt = f"PROPOSED by {who}: {when or 'a specific time'}. Not agreed yet."
    elif d.state in PLAN_STATES:
        when = local(d.when, tzname) if d.when else None
        wtxt = when.strftime("%A %d %b") + (when.strftime(" %H:%M") if d.hour_known else " (time not fixed)") if when else d.when_text
        rel = ""
        if when:
            days = (when.date() - local(ctx.s.now, tzname).date()).days
            rel = {0: " = TODAY", 1: " = TOMORROW"}.get(days, f" = in {days} days")
        extra = {"REMINDED": " (reminder done)", "CONFIRMED_DAYOF": " (she confirmed today)", "EN_ROUTE": " (she's on the way)"}.get(d.state, "")
        txt = f"AGREED: {wtxt}{rel}{extra}."
    elif d.state == "MET":
        txt = "MET. You two already met in person."
    elif d.state == "RESCHEDULING":
        txt = "RESCHEDULING. She cancelled an agreed plan with a reason and a new time is open."
    elif d.state == "FLAKED":
        txt = "FLAKED. She cancelled an agreed plan without a reason / new time."
    else:
        txt = d.state
    if d.expired_plan:
        txt += " An older plan in this thread has PASSED or gone stale; it is not current (re-soft-close if you want to meet)."
    return txt + ev


def render_state(ctx: Ctx) -> str:
    s = ctx.s
    inc = s.incidents
    out = [f"- stage: {s.stage}; {s.exchanges} back-and-forths so far"]
    lat = f", usually replies in ~{s.her_median_latency_h:g}h" if s.her_median_latency_h is not None else ""
    out.append(f"- her investment: {s.investment} ({s.investment_trend}); avg {s.her_avg_words:g} words{lat}")
    out.append(f"- DATE: {render_date(ctx)}")
    bits = []
    if inc.ignored_plan_asks:
        bits.append(f"{inc.ignored_plan_asks} of your plan asks ignored 2+ days")
    cancels = inc.flakes_with_reason + inc.flakes_no_reason
    if cancels:
        bits.append(f"she cancelled an agreed plan {cancels}x ({inc.flakes_with_reason} with a reason"
                    + (f", {inc.reschedules_by_her}x offering another time" if inc.reschedules_by_her else "") + ")")
    if bits:
        out.append("- history: " + "; ".join(bits))
    if s.her_unanswered >= 2:
        out.append(f"- she sent {s.her_unanswered} messages in a row: answer ONE thread, ignore the rest")
    if s.my_question_streak >= 2:
        out.append(f"- your last {s.my_question_streak} messages were questions: make a statement now")
    if s.our_date_uses_recent:
        out.append(f"- you already used an 'our date' tease {s.our_date_uses_recent}x recently: don't lean on it again")
    if s.date.state == "NONE" and s.positive_run >= 5 and s.her_unanswered <= 1:
        out.append(f"- {s.positive_run} warm replies from her since you last moved toward meeting: it's time to soft close")
    if s.troll_active:
        out.append("- a joke/troll is running: land it and move forward")
    if ctx.flags.get("reopen_after_skip"):
        out.append("- you deliberately left her last tiny message unanswered for a while: don't answer it, open something fresh and light")
    if not s.times_known:
        out.append("- message times are approximate")
    return "\n".join(out)


def task_line(ctx: Ctx, followup_reason: str = "") -> str:
    s = ctx.s
    if not s.n_msgs:
        return "Write your FIRST message to her."
    if ctx.trigger == "followup":
        gap = gap_text(s.since_my_last_h or 0)
        what = {
            "double_text": f"She hasn't replied to your last message for {gap}. Write ONE follow-up.",
            "triple_text": f"She has ignored your last {s.my_unanswered} messages ({gap} since the last). Write ONE follow-up.",
            "sweep": f"The chat has been dead for {gap}. Write ONE light re-engage.",
            "keep_warm": "Your date is a few days away and it's been quiet. Write ONE light touch (not logistics).",
            "reminder_night_before": "The date is tomorrow. Write ONE playful reminder.",
            "dayof_warmup": "It's the day of the date. Write ONE light warm-up message.",
            "dayof_confirm": "It's the day of the date. Write ONE plain confirm.",
            "post_date_callback": "You met. Write ONE follow-up after the date.",
        }.get(followup_reason, f"She hasn't replied for {gap}. Write ONE follow-up.")
        return what
    n = s.her_last.idx + 1 if s.her_last else s.n_msgs
    return f"Write your reply to her newest message (#{n})."


def _menu_lines(ctx: Ctx, menu: list[Move]) -> list[str]:
    reactive = [m for m in menu if m.reactive]
    forward = [m for m in menu if not m.reactive]
    out = ["MOVES YOU MAY USE (each candidate uses exactly one move_id from this list):"]
    if reactive:
        out.append("A) IF her newest message is one of these types, answer it with the matching move:")
        out += [m.card(ctx, compact=True) for m in reactive]
        out.append("B) Moves that move things forward (use these when A doesn't apply, or right after handling A):")
    out += [m.card(ctx) for m in forward]
    return out


def build(ctx: Ctx, msgs: list, menu: list[Move], *, contact_name: str = "", her_profile: str = "",
          followup_reason: str = "", n_candidates: int = 4, context_messages: int = 30) -> tuple[str, str]:
    s = ctx.s
    her_now = local(s.now, s.her_tz)
    hour_note = ""
    if 0 <= her_now.hour < 5:
        hour_note = " (after midnight for her: use weekday names, not tonight/tomorrow)"
    head = [
        f"NOW (her time): {her_now.strftime('%A %d %b %Y %H:%M')}{hour_note}. Platform: {s.platform or 'unknown'}.",
        "",
        render_for_prompt(ctx.facts),
        "",
        "ABOUT HER: " + (_clip(her_profile, 500) if her_profile else "nothing beyond the thread") + (f" (name: {contact_name})" if contact_name else ""),
        "",
        "STATE (computed from the whole thread; trust it):",
        render_state(ctx),
        "",
        "THREAD (oldest -> newest, her local times):",
        render_thread(ctx, msgs, context_messages),
        "",
        "TASK: " + task_line(ctx, followup_reason),
        "",
        *_menu_lines(ctx, menu),
        "",
        TYPE_GUIDE,
        "",
        OUTPUT_SPEC.format(types="|".join(MSG_TYPES), flags=", ".join(FLAG_WORDS), n=n_candidates),
    ]
    return SYSTEM, "\n".join(head)


def repair_prompt(user: str, rejected: list[tuple[str, str, list[str]]], n: int) -> str:
    lines = ["", "YOUR PREVIOUS CANDIDATES WERE REJECTED:"]
    for move_id, text, why in rejected[:6]:
        lines.append(f'- [{move_id}] "{_clip(text, 120)}" -> {"; ".join(why[:3])}')
    lines.append(f"Write {n} NEW candidates that fix these problems. Same output format (all lines).")
    return user + "\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Parsing the model's answer
# ---------------------------------------------------------------------------

def _norm_choice(v: str, allowed: tuple[str, ...], default: str) -> str:
    v = (v or "").strip().lower().strip("<>\"' ")
    v = v.replace(" ", "_")
    if v in allowed:
        return v
    for a in allowed:
        if a in v:
            return a
    return default


def _yes(v: str) -> bool:
    return (v or "").strip().lower().startswith(("y", "true", "1"))


def parse(raw: str) -> dict[str, Any]:
    """Parse the model's answer (line format, JSON tolerated). Never raises."""
    raw = (raw or "").strip()
    raw = re.sub(r"^```\w*\s*|\s*```$", "", raw)
    out: dict[str, Any] = {"analysis": {}, "candidates": [], "raw": raw}
    if not raw:
        return out
    if raw.lstrip().startswith("{"):
        j = _parse_json(raw)
        if j:
            return _from_json(j, raw)
    kv: dict[str, str] = {}
    cands = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        m = re.match(r"^(?:C\d*|candidate\s*\d*)\s*[:.)]\s*(.+)$", line, re.I)
        if m:
            body = m.group(1)
            if "|" in body:
                mid, text = body.split("|", 1)
            elif "::" in body:
                mid, text = body.split("::", 1)
            else:
                continue
            mid = mid.strip().strip("[]<>\"' ").lower()
            text = text.strip()
            if text:
                cands.append({"move_id": mid, "text": text})
            continue
        m = re.match(r"^([a-z_ ]{2,20})\s*:\s*(.*)$", line, re.I)
        if m:
            kv[m.group(1).strip().lower().replace(" ", "_")] = m.group(2).strip()
    out["analysis"] = _analysis(kv)
    out["candidates"] = cands
    return out


def _analysis(kv: dict[str, str]) -> dict[str, Any]:
    flags_raw = (kv.get("flags") or "").lower()
    flags = {f for f in FLAG_WORDS if re.search(rf"\b{f}\b", flags_raw)}
    try:
        esl = int(re.sub(r"\D", "", kv.get("esl", "0")) or 0)
    except ValueError:
        esl = 0
    a = {
        "her_msg_type": _norm_choice(kv.get("type", ""), MSG_TYPES, ""),
        "sexual_light": _norm_choice(kv.get("light", ""), LIGHTS, "unknown"),
        "temperature": _norm_choice(kv.get("temp", kv.get("temperature", "")), TEMPS, "neutral"),
        "culture_mode": _norm_choice(kv.get("culture", ""), CULTURES, "default"),
        "esl_level": max(0, min(2, esl)),
        "she_asked_about_me": _yes(kv.get("asked_me", "")),
        "flags": sorted(flags),
        "date_view": _norm_choice(kv.get("date", ""), DATE_VIEWS, ""),
        "thread_to_answer": _clip(kv.get("thread", ""), 80),
        "skip": _yes(kv.get("skip", "")),
    }
    a["serious_event"] = "serious" in flags or a["her_msg_type"] == "sincere_serious"
    a["cancelled"] = "cancelled" in flags or a["her_msg_type"] == "cancel"
    a["cancel_has_reason"] = "cancel_reason" in flags
    a["offered_reschedule"] = "reschedule_offer" in flags
    a["troll_active"] = "troll_running" in flags
    return a


def _parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    txt = re.sub(r",\s*([}\]])", r"\1", m.group(0))
    try:
        return json.loads(txt)
    except Exception:
        return None


def _from_json(j: Any, raw: str) -> dict[str, Any]:
    if not isinstance(j, dict):
        return {"analysis": {}, "candidates": [], "raw": raw}
    a = j.get("analysis") or {k: v for k, v in j.items() if k != "candidates"}
    kv = {str(k).lower(): (", ".join(v) if isinstance(v, list) else str(v)) for k, v in a.items()}
    aliases = {"her_msg_type": "type", "sexual_light": "light", "temperature": "temp", "culture_mode": "culture",
               "esl_level": "esl", "she_asked_about_me": "asked_me", "date_view": "date", "thread_to_answer": "thread"}
    for long, short in aliases.items():
        if long in kv and short not in kv:
            kv[short] = kv[long]
    cands = []
    for c in j.get("candidates") or []:
        if isinstance(c, dict) and c.get("text"):
            cands.append({"move_id": str(c.get("move_id") or c.get("move") or "").strip().lower(), "text": str(c["text"]).strip()})
        elif isinstance(c, str) and "|" in c:
            mid, text = c.split("|", 1)
            cands.append({"move_id": mid.strip().lower(), "text": text.strip()})
    return {"analysis": _analysis(kv), "candidates": cands, "raw": raw}
