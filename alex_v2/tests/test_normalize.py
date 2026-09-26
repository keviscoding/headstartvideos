from dataclasses import dataclass
from datetime import datetime, timezone

from alex_v2.normalize import normalize_messages
from alex_v2.timeparse import local, tz

UTC = timezone.utc
LON = "Europe/London"


@dataclass
class WMsg:  # shape of wingman.transcript.Message
    speaker: str
    text: str
    timestamp: float = 0.0
    time_label: str = ""


def test_wingman_message_objects_and_labels():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=tz(LON)).astimezone(UTC)
    raw = [
        WMsg("them", "Hey trouble 😈", now.timestamp(), "Wed 9:14 PM"),
        WMsg("me", "Hey you", now.timestamp(), ""),
        WMsg("them", "how's your day", now.timestamp(), "Yesterday 6:12 PM"),
    ]
    msgs, new = normalize_messages(raw, now=now, user_tz=LON)
    assert [m.speaker for m in msgs] == ["her", "me", "her"]
    assert local(msgs[0].sent_at, LON).strftime("%a %H:%M") == "Wed 21:14"
    # unknown middle message is forward-filled, not "now"
    assert msgs[1].sent_at == msgs[0].sent_at and not msgs[1].time_known
    assert msgs[2].time_known
    assert set(new) == {msgs[0].fp, msgs[2].fp}


def test_first_seen_resolution_is_sticky():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=tz(LON)).astimezone(UTC)
    raw = [{"speaker": "them", "text": "hi", "time": "Yesterday 6:12 PM"}]
    msgs, new = normalize_messages(raw, now=now, user_tz=LON)
    later = datetime(2026, 9, 28, 12, 0, tzinfo=tz(LON)).astimezone(UTC)
    msgs2, _ = normalize_messages(raw, now=later, user_tz=LON, stored_times=new)
    assert msgs2[0].sent_at == msgs[0].sent_at


def test_duplicate_texts_get_distinct_fingerprints():
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    msgs, _ = normalize_messages([("them", "lol"), ("me", "ok"), ("them", "lol")], now=now, user_tz=LON)
    assert msgs[0].fp != msgs[2].fp


def test_wingman_message_objects():
    """wingman.transcript.Message: speaker "me"/"them", time_label from the screenshot, timestamp = ingest time."""
    from dataclasses import dataclass
    from datetime import datetime, timezone

    from alex_v2.normalize import normalize_messages
    from alex_v2.tests.helpers import lon

    @dataclass
    class Message:
        speaker: str
        text: str
        reply_to: str = ""
        timestamp: float = 0.0
        time_label: str = ""

    seen = lon(2026, 9, 24, 10, 0)
    raw = [Message("them", "hey you", timestamp=seen.timestamp(), time_label="Yesterday 6:12 PM"),
           Message("me", "Hey trouble", timestamp=seen.timestamp(), time_label="Yesterday 6:40 PM"),
           Message("them", "haha hi", timestamp=seen.timestamp(), time_label="9:55 AM")]
    msgs, _ = normalize_messages(raw, now=seen, user_tz="Europe/London")
    assert [m.speaker for m in msgs] == ["her", "me", "her"]
    assert msgs[0].sent_at == lon(2026, 9, 23, 18, 12) and msgs[0].time_known
    assert msgs[2].sent_at == lon(2026, 9, 24, 9, 55)
    assert all(m.sent_at.tzinfo == timezone.utc for m in msgs) and isinstance(msgs[0].sent_at, datetime)
