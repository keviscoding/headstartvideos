"""Try v2 on any conversation without touching real threads.

Paste a chat, one message per line:

    her: you look like trouble
    me: Guilty
    [Thu 21:14] her: haha so what do you do
    [2 days later]
    me: Don't think too hard now

Speakers: me/you/alex/him  vs  her/she/girl/them. An optional ``[time]``
prefix accepts "21:14", "Thu 21:14", "24/09 21:14", "Yesterday 9pm"...
``[N days later]`` / ``[3h later]`` lines insert a gap. Without times,
messages are spaced a few minutes apart ending just before "now".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .engine import decide_async
from .timeparse import parse_time_label

UTC = timezone.utc
_LINE = re.compile(r"^\s*(?:\[(?P<time>[^\]]+)\]\s*)?(?P<who>me|you|alex|him|i|her|she|girl|them|match)\s*(?:\((?P<time2>[^)]*)\))?\s*[:>\-]\s*(?P<text>.+?)\s*$", re.I)
_GAP = re.compile(r"^\s*\[\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>m|min|mins|minutes?|h|hrs?|hours?|d|days?|w|weeks?)\s*(later)?\s*\]\s*$", re.I)
_ME = {"me", "you", "alex", "him", "i"}


def parse_conversation(text: str, now: datetime, tzname: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pending_gap = timedelta(0)
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        g = _GAP.match(line)
        if g:
            n = float(g.group("n"))
            u = g.group("unit").lower()[0]
            pending_gap += timedelta(minutes=n) if u == "m" else timedelta(hours=n) if u == "h" else timedelta(days=n) if u == "d" else timedelta(weeks=n)
            continue
        m = _LINE.match(line)
        if not m:
            if rows:
                rows[-1]["text"] += "\n" + line.strip()
            continue
        label = (m.group("time") or m.group("time2") or "").strip()
        at: Optional[datetime] = None
        if label:
            dt, has_clock = parse_time_label(label, now, tzname)
            at = dt if dt is not None else None
        rows.append({"speaker": "me" if m.group("who").lower() in _ME else "her", "text": m.group("text"), "at": at, "gap": pending_gap})
        pending_gap = timedelta(0)
    if not rows:
        return []
    # fill times backwards from "now": 4 min per message unless told otherwise
    t = now - timedelta(minutes=2)
    for r in reversed(rows):
        if r["at"] is not None:
            t = r["at"]
        else:
            r["at"] = t
        t = t - timedelta(minutes=4) - r["gap"]
    # enforce order
    for a, b in zip(rows, rows[1:]):
        if b["at"] < a["at"]:
            b["at"] = a["at"] + timedelta(minutes=1)
    return [{"speaker": r["speaker"], "text": r["text"], "sent_at": r["at"].isoformat()} for r in rows]


async def run_lab(conversation: str, *, platform: str = "", her_tz: str = "Europe/London", now: Optional[datetime] = None,
                  call_model: bool = True, her_profile: str = "", llm=None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    msgs = parse_conversation(conversation, now, her_tz)
    d = await decide_async(msgs, thread_id=f"lab:{hash(conversation) & 0xFFFFFFFF:x}", platform=platform, her_tz=her_tz,
                           now=now, namespace="lab", dry_run=True, explain_only=not call_model, her_profile=her_profile, llm=llm)
    return {"messages": msgs, "decision": d.to_dict()}
