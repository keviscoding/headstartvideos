"""Decision log + A/B outcome tracking.

- ``<data_dir>/logs/decisions.jsonl``: one line per v2 decision (what it saw,
  the menu, every candidate with the critic's verdict, what it chose, why,
  latency, model). This is what the Alex v2 page shows.
- ``<data_dir>/logs/outcomes.json``: per thread, per arm: how many of our
  messages got a reply within 72h, the furthest date state reached, flakes.
  Updated from the thread itself every time v2 (or ``integration.observe``
  for v1 threads) looks at it, so both arms are measured the same way.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .models import DATE_RANK, Msg, State
from .settings import data_dir

UTC = timezone.utc
_LOCK = threading.RLock()
MAX_LOG_BYTES = 25 * 1024 * 1024


def _logs() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def thread_key(thread_id: str) -> str:
    return hashlib.sha1(thread_id.encode("utf-8")).hexdigest()[:12]


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def log_decision(entry: dict[str, Any]) -> None:
    path = _logs() / "decisions.jsonl"
    entry = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with _LOCK:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            os.replace(path, path.with_suffix(".1.jsonl"))
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def recent_decisions(limit: int = 100, thread: str = "", namespace: str = "") -> list[dict[str, Any]]:
    path = _logs() / "decisions.jsonl"
    if not path.exists():
        return []
    with _LOCK:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4 * 1024 * 1024))
            data = f.read().decode("utf-8", errors="ignore")
    out = []
    for line in reversed(data.splitlines()):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if thread and thread not in (e.get("thread_key"), e.get("thread_id")):
            continue
        if namespace and e.get("namespace") != namespace:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Outcomes (A/B)
# ---------------------------------------------------------------------------

def _outcomes_path() -> Path:
    return _logs() / "outcomes.json"


def _load_outcomes() -> dict[str, Any]:
    p = _outcomes_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_outcomes(d: dict[str, Any]) -> None:
    p = _outcomes_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, default=str))
    os.replace(tmp, p)


def observe(thread_id: str, arm: str, msgs: list[Msg], state: Optional[State], now: datetime, namespace: str = "live") -> None:
    """Update this thread's outcome record from what's visible in the thread."""
    if namespace not in ("live",) or arm not in ("v1", "v2"):
        return
    key = thread_key(thread_id)
    with _LOCK:
        d = _load_outcomes()
        rec = d.get(key) or {"arm": arm, "since": now.isoformat(), "max_date": "NONE", "max_rank": 0}
        if rec.get("arm") != arm:
            # the thread switched arms (mode change): start a fresh record for the new arm
            rec = {"arm": arm, "since": now.isoformat(), "max_date": "NONE", "max_rank": 0}
        since = _parse(rec.get("since")) or now
        mine = [m for m in msgs if m.is_me and m.sent_at and m.sent_at >= since - timedelta(minutes=1)]
        eligible = answered = 0
        for m in mine:
            nxt = next((x for x in msgs[m.idx + 1:] if x.is_her), None)
            if nxt is not None and nxt.sent_at and m.sent_at and (nxt.sent_at - m.sent_at) <= timedelta(hours=72):
                eligible += 1
                answered += 1
            elif (now - m.sent_at) >= timedelta(hours=72):
                eligible += 1
        rec["my_msgs"] = len(mine)
        rec["eligible"] = eligible
        rec["answered_72h"] = answered
        if state is not None:
            rank = DATE_RANK.get(state.date.state, 0)
            if state.date.state in ("RESCHEDULING", "FLAKED"):
                rank = DATE_RANK["AGREED"]  # a plan existed
            if rank > rec.get("max_rank", 0):
                rec["max_rank"] = rank
                rec["max_date"] = state.date.state if state.date.state not in ("RESCHEDULING", "FLAKED") else "AGREED"
            rec["flakes"] = state.incidents.flakes_no_reason + state.incidents.flakes_with_reason
            rec["closed"] = state.closed
        rec["last"] = now.isoformat()
        d[key] = rec
        _save_outcomes(d)


def _parse(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def ab_summary() -> dict[str, Any]:
    d = _load_outcomes()
    arms: dict[str, dict[str, Any]] = {}
    for rec in d.values():
        a = arms.setdefault(rec.get("arm", "?"), {"threads": 0, "eligible": 0, "answered_72h": 0, "reached_interest": 0,
                                                    "reached_agreed": 0, "reached_confirmed": 0, "reached_met": 0, "flakes": 0})
        a["threads"] += 1
        a["eligible"] += rec.get("eligible", 0)
        a["answered_72h"] += rec.get("answered_72h", 0)
        r = rec.get("max_rank", 0)
        a["reached_interest"] += r >= DATE_RANK["INTEREST"]
        a["reached_agreed"] += r >= DATE_RANK["AGREED"]
        a["reached_confirmed"] += r >= DATE_RANK["CONFIRMED_DAYOF"]
        a["reached_met"] += r >= DATE_RANK["MET"]
        a["flakes"] += rec.get("flakes", 0) or 0
    for a in arms.values():
        n = max(1, a["threads"])
        a["reply_rate"] = round(a["answered_72h"] / a["eligible"], 3) if a["eligible"] else None
        a["pct_interest"] = round(a["reached_interest"] / n, 3)
        a["pct_agreed"] = round(a["reached_agreed"] / n, 3)
        a["pct_met"] = round(a["reached_met"] / n, 3)
    return arms


def reset_outcomes() -> None:
    with _LOCK:
        _save_outcomes({})


def timer() -> float:
    return time.monotonic()
