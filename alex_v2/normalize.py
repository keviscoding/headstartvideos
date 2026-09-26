"""Turn whatever the scraper produced into clean ``Msg`` objects.

Accepts wingman ``Message`` objects (speaker/text/time_label/timestamp),
dicts, or (speaker, text[, time]) tuples. Resolves every message to an
absolute UTC time once, persists that resolution per thread, and never
re-resolves it later (a "Yesterday" label read three days later would
otherwise move).

Note on wingman's ``Message.timestamp``: in the Aug-24 code it is the time
the message was INGESTED (time.time() at scrape), not when it was sent. By
default it is treated as "seen at" (an upper bound), not as the send time.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .models import Msg
from .timeparse import parse_time_label

UTC = timezone.utc

_ME = {"me", "user", "you", "alex", "assistant", "self", "sent", "outgoing", "mine", "out", "right", "guy", "him"}
_HER = {"them", "her", "she", "girl", "match", "received", "incoming", "in", "left", "contact", "other"}


def _get(item: Any, *keys: str) -> Any:
    for k in keys:
        if isinstance(item, dict):
            if k in item and item[k] not in (None, ""):
                return item[k]
        else:
            v = getattr(item, k, None)
            if v not in (None, ""):
                return v
    return None


def _speaker(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in _ME:
        return "me"
    if s in _HER:
        return "her"
    if s.startswith("me") or s.startswith("you"):
        return "me"
    return "her"


def _as_dt(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, (int, float)):
        if v <= 0:
            return None
        if v > 1e12:  # ms
            v = v / 1000.0
        return datetime.fromtimestamp(v, tz=UTC)
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        except Exception:
            return None
    return None


def _media(text: str, item: Any) -> str:
    m = _get(item, "media", "media_type", "attachment")
    if m:
        return str(m)[:20]
    low = text.lower().strip()
    if re.fullmatch(r"[\[(]?(image|photo|picture|img|gif|video|voice (memo|message|note)|audio|sticker)[^\])]*[\])]?", low):
        return "image" if "image" in low or "photo" in low or "picture" in low or "img" in low else low.split()[0].strip("[(")
    return ""


def _norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower())


def normalize_messages(
    raw: Iterable[Any],
    *,
    now: datetime,
    user_tz: str,
    stored_times: Optional[dict[str, str]] = None,
    timestamps: str = "seen",
) -> tuple[list[Msg], dict[str, str]]:
    """Return (messages, newly_resolved_times).

    ``timestamps``: how to read a numeric ``timestamp`` field.
      "seen" (default): it's when the scraper saw the message (upper bound).
      "sent": it's the real send time.
    A ``sent_at`` field is always treated as the real send time.
    """
    stored_times = dict(stored_times or {})
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    items = []
    for it in raw:
        if isinstance(it, (list, tuple)):
            it = {"speaker": it[0], "text": it[1], "time": it[2] if len(it) > 2 else ""}
        text = str(_get(it, "text", "body", "content", "message") or "").strip()
        if not text:
            continue
        items.append(it)

    msgs: list[Msg] = []
    seen_counts: dict[str, int] = {}
    day_context: Optional[datetime] = None
    new_times: dict[str, str] = {}

    for idx, it in enumerate(items):
        text = str(_get(it, "text", "body", "content", "message") or "").strip()
        spk = _speaker(_get(it, "speaker", "sender", "from", "role", "author", "who"))
        base = f"{spk}|{_norm_text(text)}"
        occ = seen_counts.get(base, 0)
        seen_counts[base] = occ + 1
        fp = hashlib.sha1(f"{base}|{occ}".encode()).hexdigest()[:16]
        label = str(_get(it, "time_label", "time", "label", "date_label") or "").strip()

        sent_at: Optional[datetime] = None
        known = False
        if fp in stored_times:
            sent_at = _as_dt(stored_times[fp])
            known = sent_at is not None
        if sent_at is None:
            explicit = _as_dt(_get(it, "sent_at", "sent_time", "datetime", "date"))
            if explicit:
                sent_at, known = explicit, True
        seen_at = _as_dt(_get(it, "seen_at", "scraped_at")) or (
            _as_dt(_get(it, "timestamp", "ts")) if timestamps == "seen" else None
        )
        if sent_at is None and timestamps == "sent":
            ts = _as_dt(_get(it, "timestamp", "ts"))
            if ts:
                sent_at, known = ts, True
        if sent_at is None and label:
            ref = seen_at or now
            dt, has_clock = parse_time_label(label, ref, user_tz)
            if dt is not None:
                if has_clock:
                    sent_at, known = dt, True
                    day_context = dt
                else:
                    day_context = dt
        if sent_at is None and day_context is not None:
            # inside a day-separator block but without a clock: approximate
            sent_at, known = day_context, False
        if sent_at is not None and sent_at > now + timedelta(minutes=5):
            sent_at, known = now, False
        msgs.append(Msg(idx=len(msgs), speaker=spk, text=text, fp=fp, sent_at=sent_at,
                        time_known=known, label=label, media=_media(text, it)))
        if known and fp not in stored_times:
            new_times[fp] = sent_at.isoformat()

    _fill_gaps(msgs, now)
    return msgs, new_times


def _fill_gaps(msgs: list[Msg], now: datetime) -> None:
    """Give unknown messages an approximate time and enforce ordering."""
    if not msgs:
        return
    # forward fill
    last: Optional[datetime] = None
    for m in msgs:
        if m.sent_at is None:
            if last is not None:
                m.sent_at = last
        else:
            if last is not None and m.sent_at < last - timedelta(hours=1) and not m.time_known:
                m.sent_at = last
            last = m.sent_at if (last is None or m.sent_at >= last) else last
    # back fill leading unknowns with the first known time
    first_known = next((m.sent_at for m in msgs if m.sent_at is not None), None)
    for m in msgs:
        if m.sent_at is None:
            m.sent_at = first_known
    # if nothing known at all, the tail is "now" and earlier messages are unknown
    if all(m.sent_at is None for m in msgs):
        msgs[-1].sent_at = now


def times_reliable(msgs: list[Msg]) -> bool:
    """True when enough real times exist to apply wait-time rules."""
    if not msgs:
        return False
    tail = msgs[-4:]
    return sum(1 for m in tail if m.time_known) >= max(1, len(tail) // 2)
