from datetime import datetime, timezone

from alex_v2.timeparse import find_day_time_refs, parse_clock, parse_time_label, local

UTC = timezone.utc
LON = "Europe/London"


def at(y, mo, d, h, mi=0):
    # London local -> utc
    from alex_v2.timeparse import tz
    return datetime(y, mo, d, h, mi, tzinfo=tz(LON)).astimezone(UTC)


def test_clock_basic():
    assert parse_clock("How's 9?") == (21, 0)
    assert parse_clock("say 9:30") == (21, 30)
    assert parse_clock("Lets do 930") == (21, 30)
    assert parse_clock("10pm works") == (22, 0)
    assert parse_clock("10am coffee") == (10, 0)
    assert parse_clock("I have 2 dogs") is None
    assert parse_clock("I'm 27, I like Brickell") is None
    assert parse_clock("5 min away") is None


def test_wednesday_is_resolved_against_send_time_not_now():
    # sent Monday 21 Sep 2026 at 18:00 -> "Wednesday" = 23 Sep
    sent = at(2026, 9, 21, 18)
    refs = find_day_time_refs("Wednesday works, say 8?", sent, LON)
    wd = [r for r in refs if r.kind == "weekday"][0]
    assert str(wd.day) == "2026-09-23"
    assert wd.hour == 20
    # the same text resolved from a message sent a week later is a different Wednesday
    refs2 = find_day_time_refs("Wednesday works", at(2026, 9, 28, 18), LON)
    assert str([r for r in refs2 if r.kind == "weekday"][0].day) == "2026-09-30"


def test_tomorrow_after_midnight_means_same_evening_context():
    # "tomorrow night?" sent at 00:45 Tuesday is ambiguous; we treat it as Tuesday night
    sent = at(2026, 9, 22, 0, 45)  # Tuesday 00:45 London
    r = [x for x in find_day_time_refs("tomorrow night?", sent, LON) if x.kind == "tomorrow"][0]
    assert str(r.day) == "2026-09-22"


def test_tonight_and_weekday_variants():
    sent = at(2026, 9, 25, 15)  # Friday 15:00
    kinds = {r.kind: r for r in find_day_time_refs("tonight or sat night?", sent, LON)}
    assert str(kinds["tonight"].day) == "2026-09-25"
    assert str(kinds["weekday"].day) == "2026-09-26"
    # ordinary words must not be read as weekdays
    assert not [r for r in find_day_time_refs("the sun was out", sent, LON) if r.kind == "weekday"]


def test_labels_resolve_against_seen_time():
    seen = at(2026, 9, 25, 12)  # Friday noon
    dt, clock = parse_time_label("Yesterday 6:12 PM", seen, LON)
    assert clock and local(dt, LON).strftime("%Y-%m-%d %H:%M") == "2026-09-24 18:12"
    dt, clock = parse_time_label("2:34 PM", seen, LON)
    # 14:34 today would be in the future relative to noon -> yesterday
    assert local(dt, LON).strftime("%Y-%m-%d %H:%M") == "2026-09-24 14:34"
    dt, clock = parse_time_label("Wed 9:14 PM", seen, LON)
    assert local(dt, LON).strftime("%Y-%m-%d %H:%M") == "2026-09-23 21:14"
    dt, clock = parse_time_label("Thu, Sep 25, 10:05 AM", seen, LON)
    assert local(dt, LON).strftime("%Y-%m-%d %H:%M") == "2026-09-25 10:05"
    dt, clock = parse_time_label("23/09/2026, 21:14", seen, LON)
    assert local(dt, LON).strftime("%Y-%m-%d %H:%M") == "2026-09-23 21:14"
    dt, clock = parse_time_label("Yesterday", seen, LON)
    assert not clock and local(dt, LON).strftime("%Y-%m-%d") == "2026-09-24"
