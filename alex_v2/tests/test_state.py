from datetime import timedelta

from alex_v2.tests.helpers import lon, state_for
from alex_v2.timeparse import local

LON = "Europe/London"
MON = lon(2026, 9, 21, 18)  # Monday 21 Sep 2026 18:00


def test_annoying_opener_is_not_a_date():
    s, _ = state_for([("her", "ugh another guy with a dog pic 🙄")], MON)
    assert s.date.state == "NONE"
    assert s.stage == "opening"


def test_soft_close_accepted_is_interest_only():
    s, _ = state_for([
        ("me", "Hey trouble"), ("her", "hey you"),
        ("me", "We should split a bottle sometime soon"), ("her", "Sounds fun 😉"),
    ], MON)
    assert s.date.state == "INTEREST"
    assert s.stage == "closing"


def test_full_close_to_agreed_with_absolute_time():
    s, _ = state_for([
        ("me", "We should split a bottle sometime soon"), ("her", "for sure"),
        ("me", "What's your schedule like"), ("her", "I'm free Thursday or Friday after 6"),
        ("me", "How's Thursday, say 9?"), ("her", "Perfect"),
    ], MON)
    assert s.date.state == "AGREED"
    when = local(s.date.when, LON)
    assert when.strftime("%a %d %H:%M") == "Thu 24 21:00"


def test_our_date_presupposition_never_creates_a_date():
    s, _ = state_for([
        ("her", "how's your day going?"),
        ("me", "Good, just finished a big workout. Looking nice & fit for our date"),
        ("her", "haha when is this date?"),
    ], MON)
    assert s.date.state == "INTEREST"  # she's asking when = interest, not agreed


def test_stale_wednesday_plan_expires():
    spec = [
        ("me", "We should grab a drink sometime soon"), ("her", "yes!"),
        ("me", "How's Wednesday, say 8?"), ("her", "Wednesday works"),
    ]
    s, _ = state_for(spec, MON, now=lon(2026, 10, 2, 15))  # Friday of the NEXT week
    assert s.date.state == "INTEREST"
    assert s.date.expired_plan


def test_cancel_with_reason_and_reschedule_then_new_agreement():
    s, _ = state_for([
        ("me", "We should grab a drink sometime soon"), ("her", "definitely"),
        ("me", "How's Wednesday, say 8?"), ("her", "Wednesday works"),
        ("her", "I'm so sorry I have to work late, can we do Friday instead?", 60 * 20),
        ("me", "Sure, Friday works"),
    ], MON)
    assert s.date.state == "AGREED"
    assert local(s.date.when, LON).strftime("%a") == "Fri"
    assert s.incidents.reschedules_by_her == 1 and s.incidents.flakes_with_reason == 1


def test_cancel_without_reason_is_a_flake():
    s, _ = state_for([
        ("me", "We should grab a drink sometime soon"), ("her", "sure"),
        ("me", "How's tomorrow night, say 9?"), ("her", "sounds good"),
        ("her", "I can't sorry", 60 * 18),
    ], MON)
    assert s.date.state == "FLAKED"
    assert s.incidents.flakes_no_reason == 1


def test_counter_time_then_accept():
    s, _ = state_for([
        ("me", "We should get together sometime soon"), ("her", "would be nice"),
        ("me", "How's Thursday, say 9?"), ("her", "8 maybe?"), ("me", "Yep that works"),
    ], MON)
    assert s.date.state == "AGREED"
    assert local(s.date.when, LON).strftime("%a %H:%M") == "Thu 20:00"


def test_dayof_confirm():
    tue = lon(2026, 9, 22, 10)
    s, _ = state_for([
        ("me", "We should get together sometime soon"), ("her", "yes"),
        ("me", "How's tomorrow night, say 9?"), ("her", "perfect"),
        ("me", "Hola linda", 60 * 24), ("her", "hiii"),
        ("me", "Hey still good for tonight?"), ("her", "Yes!"),
    ], tue)
    assert s.date.state == "CONFIRMED_DAYOF"


def test_unanswered_plan_ask_counts_as_incident():
    s, _ = state_for([
        ("me", "Hey trouble"), ("her", "hey"),
        ("me", "We should grab a drink sometime soon"),
    ], MON, now=MON + timedelta(days=3))
    assert s.incidents.ignored_plan_asks >= 1
    assert s.my_unanswered == 1
