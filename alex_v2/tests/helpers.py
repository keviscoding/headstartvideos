from datetime import datetime, timedelta, timezone

from alex_v2.normalize import normalize_messages
from alex_v2.state import build_state
from alex_v2.timeparse import tz

UTC = timezone.utc
LON = "Europe/London"


def lon(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=tz(LON)).astimezone(UTC)


def thread(spec, start):
    """spec: list of (speaker, text, minutes_after_previous)."""
    out = []
    t = start
    for i, item in enumerate(spec):
        spk, text = item[0], item[1]
        gap = item[2] if len(item) > 2 else 5
        if i:
            t = t + timedelta(minutes=gap)
        out.append({"speaker": spk, "text": text, "sent_at": t.isoformat()})
    return out, t


def state_for(spec, start, now=None, **kw):
    raw, last = thread(spec, start)
    now = now or (last + timedelta(minutes=2))
    msgs, _ = normalize_messages(raw, now=now, user_tz=LON)
    return build_state(msgs, thread_id="t1", now=now, her_tz=LON, my_tz=LON, **kw), msgs
