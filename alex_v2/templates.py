"""Canon follow-ups that need no model call.

Timer-driven follow-ups (double/triple texts, day-of confirm, the takeaway
ladder) are Alex's fixed lines; the skill is WHEN, not wording. Sending them
from templates makes them free, instant, and impossible to paraphrase into
something off-canon ("hey just checking in!"). The critic still checks them
(e.g. never repeat a line she already got).
"""

from __future__ import annotations

import random
import re
from typing import Callable

from . import lexicon as L
from .moves import Ctx
from .timeparse import local, weekday_name


def _my_last_kind(c: Ctx) -> str:
    m = c.s.my_last
    if not m:
        return "none"
    t = m.text
    if c.s.n_her == 0 or (c.s.n_me == 1 and c.s.her_last is None):
        return "opener"
    if L.SOFT_CLOSE.search(t) or L.SCHEDULE_ASK.search(t) or L.CONFIRM_Q.search(t) or re.search(r"\bhow'?s \w+(day)?\b.*\b(\d|night|evening)", t, re.I):
        return "plan"
    return "banter"


def _dayof_confirm(c: Ctx) -> list[str]:
    d = c.s.date
    word = "tonight"
    if d.when is not None and d.hour_known and local(d.when, c.s.her_tz).hour < 17:
        word = "today"
    return [f"Still good for {word}?", f"Hey, still good for {word}?"]


def _dayof_warmup(c: Ctx) -> list[str]:
    day = weekday_name(local(c.s.now, c.s.her_tz).date())
    lines = [f"Hey, happy {day}", f"Happy {day} ;)"]
    if c.light == "green":
        lines.append("Yo sexy")
    return lines


def _reminder(c: Ctx) -> list[str]:
    if c.s.date.had_plan and c.ds == "AGREED" and any(e.state == "MET" for e in c.s.date.evidence):
        return ["Excited to see you tomorrow 😉"]
    return ["Pick out a cute outfit for tomorrow yet?", "Excited to meet you tomorrow 😉"]


def _reengage_mild(c: Ctx) -> list[str]:
    kind = _my_last_kind(c)
    if kind == "opener":
        return ["Don't be shy", "A woman of few words I see"]
    if kind == "plan":
        lines = ["Don't think too hard now"]
        last = c.s.my_last.text if c.s.my_last else ""
        if "?" in last or L.SCHEDULE_ASK.search(last):
            lines.append("Don't worry, it's not a trick question")
        return lines
    return ["Thinking very hard I see", "Are you always this talkative?"]


def _reengage_second(c: Ctx) -> list[str]:
    last = c.s.my_last.text if c.s.my_last else ""
    if "?" in last or L.SOFT_CLOSE.search(last) or L.SCHEDULE_ASK.search(last):
        return ["Or not", "I guess not"]
    return ["Did we... break up?"]


def _sweep(c: Ctx) -> list[str]:
    n = int(c.flags.get("sweeps_sent", 0))
    order = [["Did we... break up?"], ["Hey stranger. How's your week going?"], ["Good news! We should celebrate"]]
    return order[n % len(order)] + [x[0] for x in order if x[0] not in order[n % len(order)]]


TEMPLATES: dict[str, Callable[[Ctx], list[str]]] = {
    "dayof_confirm": _dayof_confirm,
    "dayof_warmup": _dayof_warmup,
    "reminder_night_before": _reminder,
    "reengage_mild": _reengage_mild,
    "reengage_second": _reengage_second,
    "reengage_sweep": _sweep,
    "solicit_concern": lambda c: ["If you're too nervous, I'd understand"],
    "flaky_type": lambda c: ["I genuinely didn't take you for the flaky type"],
    "difficult_to_plan": lambda c: ["Are you always this difficult to make plans with?"],
}

# Used only when the model call fails for a context-dependent follow-up.
FALLBACK_LINES: dict[str, list[str]] = {
    "keep_warm": ["Hey, how's your week going?"],
    "post_date_callback": ["That was fun"],
}


def lines_for(move_id: str, ctx: Ctx, seed: str) -> list[str]:
    fn = TEMPLATES.get(move_id)
    lines = fn(ctx) if fn else list(FALLBACK_LINES.get(move_id, []))
    if len(lines) > 1:
        # stable variety per thread: rotate the list by a seeded offset (keeps the canon's first choice most likely)
        r = random.Random(seed)
        if r.random() < 0.35:
            k = r.randrange(1, len(lines))
            lines = lines[k:] + lines[:k]
    return lines
