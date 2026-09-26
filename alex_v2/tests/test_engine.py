"""End-to-end regression suite: the bugs from the diagnosis, run through the
whole engine with a scripted model. Each test names the failure it guards."""

from datetime import timedelta

import pytest

from alex_v2 import engine, facts, lexicon as L, settings, store
from alex_v2.tests.fake_llm import FakeLLM, out
from alex_v2.tests.helpers import lon, thread
from alex_v2.timeparse import local

LON = "Europe/London"


@pytest.fixture(autouse=True)
def _v2_on():
    settings.save({"mode": "on", "honor_timing": False})
    facts.save({"first_name": "Kev", "city": "London", "activities": ["gym"], "own_place": True, "roommates": False})
    yield


def run(spec, start, now=None, llm=None, **kw):
    raw, last = thread(spec, start)
    now = now or (last + timedelta(minutes=2))
    kw.setdefault("thread_id", "t1")
    kw.setdefault("her_tz", LON)
    d = engine.decide(raw, now=now, llm=llm, **kw)
    return d, raw, last


# ---------------------------------------------------------------------------
# the handoff bug family: dates that don't exist
# ---------------------------------------------------------------------------

def test_wednesday_bug_stale_plan_is_not_confirmed():
    """A plan for last Wednesday + her new annoying opener: never 'Still good for Wednesday?'."""
    spec = [
        ("me", "We should get together sometime soon"),
        ("her", "yes we should"),
        ("me", "How's Wednesday, say 9?"),
        ("her", "Perfect"),
        ("her", "ugh you're so annoying lol", 60 * 24 * 4),   # Friday, 2 days after the Wednesday
    ]
    llm = FakeLLM(out([("dayof_confirm", "Still good for Wednesday?"),
                       ("reminder_night_before", "Excited to meet you tomorrow 😉"),
                       ("pass_shit_test", "Guilty")], type="shit_test", date="agreed"))
    d, _, _ = run(spec, lon(2026, 9, 21, 19), llm=llm)
    assert d.state["date_state"] not in ("AGREED", "REMINDED", "CONFIRMED_DAYOF")
    assert "dayof_confirm" not in d.debug["menu"] and "reminder_night_before" not in d.debug["menu"]
    assert d.should_send and d.text == "Guilty"
    bad = [c for c in d.debug["candidates"] if c["move"] in ("dayof_confirm", "reminder_night_before")]
    assert bad and all(not c["ok"] for c in bad)


def test_annoying_opener_never_treated_as_date():
    llm = FakeLLM(out([("open_reply_to_her_opener", "Still good for tonight?"),
                       ("open_reply_to_her_opener", "Guilty")], type="shit_test"))
    d, _, _ = run([("her", "you look like you'd be annoying")], lon(2026, 9, 23, 20), llm=llm)
    assert d.state["date_state"] == "NONE"
    assert d.text == "Guilty"


def test_no_plan_no_breaking_heart_or_flaky():
    spec = [("me", "We should grab a drink sometime"), ("her", "maybe, work is crazy this week")]
    llm = FakeLLM(out([("answer_and_pivot", "Breaking my heart"),
                       ("answer_and_pivot", "I genuinely didn't take you for the flaky type"),
                       ("answer_and_pivot", "Damn girl, they work you hard")]))
    d, _, _ = run(spec, lon(2026, 9, 22, 19), llm=llm)
    assert d.text == "Damn girl, they work you hard"
    fails = {c["text"]: c["fails"] for c in d.debug["candidates"]}
    assert any("breaking" in f for f in fails["Breaking my heart"])
    assert any("flaky" in f for f in fails["I genuinely didn't take you for the flaky type"])


def test_receptive_girl_never_gets_too_nervous():
    spec = [("her", "hey you"), ("me", "Hey trouble"), ("her", "haha trouble? I'm an angel"),
            ("me", "I'll be the judge of that"), ("her", "lol fair. I do love a good glass of wine though")]
    llm = FakeLLM(out([("solicit_concern", "If you're too nervous, I'd understand"),
                       ("soft_close", "We should split a bottle sometime soon")], type="playful"))
    d, _, _ = run(spec, lon(2026, 9, 22, 19), llm=llm)
    assert "solicit_concern" not in d.debug["menu"]
    assert d.text == "We should split a bottle sometime soon"


def test_our_date_tease_is_not_a_plan():
    spec = [("her", "how's your day going?"), ("me", "Good, just finished a big workout. Looking nice & fit for our date"),
            ("her", "haha our date? when is that")]
    llm = FakeLLM(out([("dayof_confirm", "Still good for tonight?"), ("schedule_ask", "Soon. What's your schedule like")],
                      type="buying_question"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.state["date_state"] in ("NONE", "INTEREST")
    assert d.text == "Soon. What's your schedule like"


def test_model_doubting_an_agreed_plan_blocks_presupposing_it():
    spec = [("me", "How's Friday, say 9?"), ("her", "Perfect"), ("her", "wait what's the plan for friday", 60)]
    llm = FakeLLM(out([("logistics_reply", "Still good for Friday?"), ("logistics_reply", "Drinks at mine, say 9")],
                      type="logistics", date="none"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.state["date_state"] == "AGREED"
    assert d.text == "Drinks at mine, say 9"


def test_model_reads_cancel_that_tracker_missed_hands_off():
    msg = "ugh my boss dumped a huge project on me for friday night"
    assert not L.DECLINE.search(msg)
    spec = [("me", "How's Friday, say 9?"), ("her", "Perfect"), ("her", msg, 60 * 20)]
    llm = FakeLLM(out([("answer_and_pivot", "Damn girl, they work you hard")], type="cancel", flags="cancelled"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.action == "handoff" and "cancel" in d.handoff_reason.lower()


# ---------------------------------------------------------------------------
# fabrication
# ---------------------------------------------------------------------------

def test_no_alex_bio_no_invented_pet_or_balcony():
    spec = [("her", "hey"), ("me", "Hey trouble"), ("her", "what are you up to tonight?")]
    llm = FakeLLM(out([("answer_and_pivot", "Just walked Rhaegar, my husky"),
                       ("answer_and_pivot", "Chilling on my balcony with some wine"),
                       ("answer_and_pivot", "Just finished a big gym session. You?")], asked_me="yes", type="question_to_you"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.text == "Just finished a big gym session. You?"
    fails = " ".join(" ".join(c["fails"]) for c in d.debug["candidates"] if not c["ok"])
    assert "rhaegar" in fails and "balcony" in fails


def test_roommates_never_live_alone():
    facts.save({"roommates": True})
    spec = [("her", "hey"), ("me", "Hey trouble"), ("her", "do you live alone?")]
    llm = FakeLLM(out([("answer_and_pivot", "I live alone, just me at my place"), ("answer_and_pivot", "Why, planning a visit?")],
                      asked_me="yes", type="question_to_you"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.text == "Why, planning a visit?"


# ---------------------------------------------------------------------------
# calibration: lights, culture, platform, profile
# ---------------------------------------------------------------------------

def test_yellow_light_blocks_innuendo_and_sexual_content():
    spec = [("her", "hey"), ("me", "Hey trouble"), ("her", "I'm tired, long day"), ("me", "Adulting I see"),
            ("her", "yeah need a massage honestly")]
    llm = FakeLLM(out([("innuendo", "I'll hook you up with a massage, if ya play your cards right"),
                       ("answer_and_pivot", "I want to bend you over and spank you"),
                       ("answer_and_pivot", "Sounds like you need a glass of wine")], light="yellow"))
    d, _, _ = run(spec, lon(2026, 9, 22, 20), llm=llm)
    assert d.text == "Sounds like you need a glass of wine"


def test_esl_latin_no_trolls():
    spec = [("her", "hola"), ("me", "Hola. Hablas ingles?"), ("her", "si a little haha. where you going tonight?"),
            ("me", "Ah, a local"), ("her", "haha what do you do for work")]
    llm = FakeLLM(out([("troll_start", "Dj Khaled's house. He threw me a surprise party"),
                       ("soft_close", "I'll tell you all about it over a bottle of wine ;)")],
                      type="question_to_you", culture="latin", esl="2", temp="positive"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.move_id == "soft_close"


def test_instagram_cold_dm_no_hey_trouble():
    llm = FakeLLM(out([("open_simple", "Hey trouble"), ("open_simple", "Hey, I like your style 👋")]))
    d = engine.decide([], thread_id="ig1", platform="instagram", origin="ig_cold_dm", llm=llm, now=lon(2026, 9, 22, 18), her_tz=LON)
    assert d.text == "Hey, I like your style 👋"


def test_vanilla_profile_menu_has_no_sexual_or_screening_moves():
    facts.save({"profile_gate": "vanilla"})
    spec = [("her", "hey"), ("me", "Hey trouble"), ("her", "haha hi"), ("me", "Ah, a local"), ("her", "you're kinda cute"),
            ("me", "I'll be the judge of that"), ("her", "lol ok")]
    llm = FakeLLM(out([("answer_and_pivot", "Look at us already")], type="playful"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    for mid in ("innuendo", "screen_looking_for", "sexual_to_meet"):
        assert mid not in d.debug["menu"]


def test_pick_one_thread_when_she_sends_several():
    spec = [("me", "Adulting I see"), ("her", "haha yes"), ("her", "also my cat just knocked my coffee over"),
            ("her", "and I have a work thing tomorrow ugh")]
    llm = FakeLLM(out([("answer_and_pivot", "Your cat has a rebellious streak")]))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert "answer ONE thread" in llm.last_user
    assert d.should_send


def test_after_midnight_no_tonight_tomorrow():
    spec = [("me", "We should get together sometime soon"), ("her", "yes! when?", 30)]
    llm = FakeLLM(out([("schedule_ask", "Are you free tomorrow night?"), ("schedule_ask", "Are you free Thursday or Friday night?")],
                      type="buying_question"))
    d, _, last = run(spec, lon(2026, 9, 23, 0, 50), llm=llm)
    assert local(last, LON).hour == 1
    assert d.text == "Are you free Thursday or Friday night?"


# ---------------------------------------------------------------------------
# guardrails
# ---------------------------------------------------------------------------

def test_minor_hands_off_without_model_call():
    llm = FakeLLM()
    d, _, _ = run([("her", "hey"), ("me", "Hey trouble"), ("her", "im 16 lol is that ok")], lon(2026, 9, 22, 18), llm=llm)
    assert d.action == "handoff" and llm.n == 0


def test_stop_closes_thread():
    llm = FakeLLM()
    d, raw, last = run([("me", "Hey trouble"), ("her", "please stop texting me")], lon(2026, 9, 22, 18), llm=llm)
    assert d.action == "none" and llm.n == 0
    d2 = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(days=5), llm=llm)
    assert d2.action == "none"


def test_scam_one_line_then_closed():
    llm = FakeLLM(out([("scam_decline", "I'm good. But I'm down to link up")]))
    spec = [("me", "Hey trouble"), ("her", "hey babe can you send me $50 on cashapp first")]
    d, raw, last = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.should_send and d.move_id == "scam_decline"
    engine.mark_sent("t1", d.text, sent_at=last + timedelta(minutes=3))
    raw2 = raw + [{"speaker": "me", "text": d.text, "sent_at": (last + timedelta(minutes=3)).isoformat()},
                  {"speaker": "her", "text": "pls babe", "sent_at": (last + timedelta(minutes=9)).isoformat()}]
    d2 = engine.decide(raw2, thread_id="t1", her_tz=LON, now=last + timedelta(minutes=10), llm=llm)
    assert d2.action == "none"


def test_serious_news_is_handed_off_with_sincere_suggestion():
    llm = FakeLLM(out([("sincere_support", "Oh shit, sorry to hear that. Are you ok?")], type="sincere_serious", flags="serious"))
    d, _, _ = run([("me", "Adulting I see"), ("her", "my dad passed away last night")], lon(2026, 9, 22, 18), llm=llm)
    assert d.action == "handoff" and "sorry to hear" in d.suggested_text.lower()
    assert d.debug["menu"] == ["sincere_support"]


def test_model_flags_minor_pauses_thread():
    llm = FakeLLM(out([("answer_and_pivot", "Ah, a local")], flags="minor"))
    d, raw, last = run([("me", "Hey trouble"), ("her", "haha my mom says I need to be home by 10")], lon(2026, 9, 22, 18), llm=llm)
    assert d.action == "handoff"
    d2 = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(hours=1), llm=llm)
    assert d2.action in ("none", "handoff") and llm.n == 1


def test_move_not_offered_is_rejected():
    llm = FakeLLM(out([("flaky_type", "I genuinely didn't take you for the flaky type"), ("answer_and_pivot", "Adulting I see")]))
    d, _, _ = run([("me", "Hey trouble"), ("her", "just got home from work")], lon(2026, 9, 22, 18), llm=llm)
    assert d.text == "Adulting I see"


# ---------------------------------------------------------------------------
# timing & follow-ups
# ---------------------------------------------------------------------------

def test_no_double_text_ten_minutes_later():
    llm = FakeLLM()
    d, _, last = run([("her", "haha ok"), ("me", "We should grab a drink sometime soon")], lon(2026, 9, 22, 18),
                     now=lon(2026, 9, 22, 18, 15), llm=llm)
    assert d.action == "wait" and llm.n == 0
    assert d.follow_up_at - last >= timedelta(hours=47)


def test_double_text_after_two_days_is_canon_template_without_model():
    spec = [("me", "We should get together sometime soon"), ("her", "yes!"), ("me", "What's your schedule like?")]
    llm = FakeLLM()
    raw, last = thread(spec, lon(2026, 9, 21, 19))
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(hours=66), llm=llm)
    assert llm.n == 0
    assert d.should_send and d.text in ("Don't think too hard now", "Don't worry, it's not a trick question")


def test_triple_text_after_ignored_nudge_is_too_nervous():
    spec = [("me", "We should get together sometime soon"), ("her", "yes!"), ("me", "What's your schedule like?"),
            ("me", "Don't think too hard now", 60 * 60)]
    raw, last = thread(spec, lon(2026, 9, 14, 19))
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(days=7, hours=1), llm=FakeLLM())
    assert d.should_send and d.text == "If you're too nervous, I'd understand"


def test_day_of_protocol_warmup_then_confirm_from_templates():
    spec = [("me", "How's Thursday, say 9?"), ("her", "Perfect"), ("me", "Thursday it is ;)")]
    raw, last = thread(spec, lon(2026, 9, 21, 19))                   # Monday
    llm = FakeLLM()
    t_warm = lon(2026, 9, 24, 13, 0)                                 # Thursday 13:00
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=t_warm, llm=llm)
    assert d.should_send and d.move_id == "dayof_warmup" and "Thursday" in d.text
    engine.mark_sent("t1", d.text, sent_at=t_warm)
    raw2 = raw + [{"speaker": "me", "text": d.text, "sent_at": t_warm.isoformat()}]
    d2 = engine.decide(raw2, thread_id="t1", her_tz=LON, now=t_warm + timedelta(hours=3, minutes=10), llm=llm)
    assert d2.should_send and d2.move_id == "dayof_confirm" and "tonight" in d2.text
    assert llm.n == 0


def test_reply_is_scheduled_then_sent_once_then_superseded():
    settings.save({"honor_timing": True})
    spec = [("me", "Hey trouble"), ("her", "haha hi, how's your week going?")]
    llm = FakeLLM(out([("answer_and_pivot", "Busy but good. Yours?")], asked_me="yes"),
                  out([("answer_and_pivot", "Ah, a local")]))
    raw, last = thread(spec, lon(2026, 9, 22, 15))
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(minutes=1), llm=llm)
    assert d.action == "wait" and d.text and d.due_at > last + timedelta(minutes=5)
    # polling before it's due: same text, no new model call
    d_mid = engine.decide(raw, thread_id="t1", her_tz=LON, now=d.due_at - timedelta(minutes=1), llm=llm)
    assert d_mid.action == "wait" and d_mid.text == d.text and llm.n == 1
    d_due = engine.decide(raw, thread_id="t1", her_tz=LON, now=d.due_at + timedelta(seconds=5), llm=llm)
    assert d_due.should_send and d_due.text == d.text
    # polled again right away (before the scraper shows it): never a double send
    d_again = engine.decide(raw, thread_id="t1", her_tz=LON, now=d.due_at + timedelta(seconds=30), llm=llm)
    assert d_again.action == "wait"
    # it shows up in the thread + she replies -> a fresh reply
    raw2 = raw + [{"speaker": "me", "text": d.text, "time_label": ""},
                  {"speaker": "her", "text": "good! I'm from Leeds originally", "sent_at": (d.due_at + timedelta(minutes=20)).isoformat()}]
    d3 = engine.decide(raw2, thread_id="t1", her_tz=LON, now=d.due_at + timedelta(minutes=21), llm=llm)
    assert d3.text == "Ah, a local" and llm.n == 2
    data = store.load("t1", "live")
    assert any(e["text"] == d.text for e in data["sent_log"])


def test_skip_tiny_ack_then_reopen_fresh():
    spec = [("her", "hey"), ("me", "Hey trouble"), ("her", "haha hi"), ("me", "Ah, a local"), ("her", "yep born and raised"),
            ("me", "Sounds like my typical Friday"), ("her", "lol")]
    llm = FakeLLM(out([("answer_and_pivot", "Ha")], type="low_effort", skip="yes"),
                  out([("soft_close", "We should get together sometime soon")]))
    raw, last = thread(spec, lon(2026, 9, 22, 18))
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(minutes=5), llm=llm)
    assert d.action == "wait" and d.follow_up_at > last + timedelta(hours=13)
    d2 = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(hours=2), llm=llm)
    assert d2.action == "wait" and llm.n == 1
    d3 = engine.decide(raw, thread_id="t1", her_tz=LON, now=d.follow_up_at + timedelta(minutes=1), llm=llm)
    assert d3.should_send and "deliberately left" in llm.last_user


# ---------------------------------------------------------------------------
# robustness
# ---------------------------------------------------------------------------

def test_unparseable_output_triggers_one_repair_call():
    llm = FakeLLM("sure! here's a reply: hey", out([("answer_and_pivot", "Adulting I see")]))
    d, _, _ = run([("me", "Hey trouble"), ("her", "just got home from work")], lon(2026, 9, 22, 18), llm=llm)
    assert d.text == "Adulting I see" and llm.n == 2


def test_model_errors_wait_then_hand_off():
    llm = FakeLLM(RuntimeError("503"), RuntimeError("503"), RuntimeError("503"))
    raw, last = thread([("me", "Hey trouble"), ("her", "just got home from work")], lon(2026, 9, 22, 18))
    now = last + timedelta(minutes=2)
    d1 = engine.decide(raw, thread_id="t1", her_tz=LON, now=now, llm=llm)
    assert d1.action == "wait"
    d_hold = engine.decide(raw, thread_id="t1", her_tz=LON, now=now + timedelta(minutes=5), llm=llm)
    assert d_hold.action == "wait" and llm.n == 1
    d2 = engine.decide(raw, thread_id="t1", her_tz=LON, now=d1.follow_up_at + timedelta(minutes=1), llm=llm)
    assert d2.action == "wait"
    d3 = engine.decide(raw, thread_id="t1", her_tz=LON, now=d2.follow_up_at + timedelta(minutes=1), llm=llm)
    assert d3.action == "handoff"


def test_shadow_arm_never_schedules():
    llm = FakeLLM(out([("answer_and_pivot", "Adulting I see")]))
    d, _, _ = run([("me", "Hey trouble"), ("her", "just got home from work")], lon(2026, 9, 22, 18), llm=llm, arm="shadow")
    assert d.action == "none" and d.suggested_text == "Adulting I see"
    assert store.load("t1", "live")["pending"] is None


def test_explain_only_returns_prompt_without_calling_model():
    llm = FakeLLM()
    d, _, _ = run([("me", "Hey trouble"), ("her", "just got home from work")], lon(2026, 9, 22, 18), llm=llm,
                  explain_only=True, dry_run=True)
    assert llm.n == 0 and "MOVES YOU MAY USE" in d.debug["user"] and "DATE: NONE" in d.debug["user"]


def test_ab_assignment_is_stable_and_split():
    s = settings.save({"mode": "ab", "ab_split": 0.5})
    arms = [settings.arm_for_thread(f"thread-{i}", s) for i in range(600)]
    assert 0.4 < arms.count("v2") / len(arms) < 0.6
    assert arms == [settings.arm_for_thread(f"thread-{i}", s) for i in range(600)]


# ---------------------------------------------------------------------------
# remaining cases from the plan's regression list (9.4)
# ---------------------------------------------------------------------------

def test_same_display_name_two_girls_no_state_bleed():
    """#2: two 'Sofia's. A plan with one never leaks into the other."""
    from alex_v2.integration import thread_id_for
    a = [("me", "How's Friday, say 9?"), ("her", "Perfect")]
    b = [("her", "you seem annoying"), ("me", "Guilty"), ("her", "lol")]
    ra, _ = thread(a, lon(2026, 9, 22, 18))
    rb, lb = thread(b, lon(2026, 9, 22, 18))
    ida, idb = thread_id_for("hinge", "Sofia", ra), thread_id_for("hinge", "Sofia", rb)
    assert ida != idb
    engine.decide(ra, thread_id=ida, her_tz=LON, now=lon(2026, 9, 22, 19), llm=FakeLLM(out([("answer_and_pivot", "x")])))
    d = engine.decide(rb, thread_id=idb, her_tz=LON, now=lb + timedelta(minutes=2),
                      llm=FakeLLM(out([("dayof_confirm", "Still good for Friday?"), ("pass_shit_test", "Not at all")], type="shit_test")))
    assert d.state["date_state"] == "NONE" and d.text == "Not at all"


def test_sincere_cancel_reason_no_troll_takeaway():
    """#6: she cancels with a real reason -> easy-going reschedule, never 'too nervous' / 'flaky'."""
    spec = [("me", "How's Friday, say 9?"), ("her", "Perfect"),
            ("her", "I'm so sorry I have to work late friday, can we do another day?", 60 * 24)]
    llm = FakeLLM(out([("flaky_type", "I genuinely didn't take you for the flaky type"),
                       ("solicit_concern", "If you're too nervous, I'd understand"),
                       ("reschedule_accept", "No worries. How's Saturday or Sunday night instead?")],
                      type="cancel", flags="cancelled, cancel_reason, reschedule_offer"))
    d, _, _ = run(spec, lon(2026, 9, 21, 18), llm=llm)
    assert d.state["date_state"] == "RESCHEDULING"
    assert d.move_id == "reschedule_accept"


def test_youre_kinda_mean_is_not_apologised_for():
    """#12"""
    spec = [("me", "Don't think too hard now"), ("her", "you're kinda mean aren't you")]
    llm = FakeLLM(out([("pass_shit_test", "Sorry, I didn't mean it like that"), ("pass_shit_test", "Not at all")], type="shit_test"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.text == "Not at all"


def test_troll_taken_seriously_is_clarified():
    """#13 (TG90): she took a joke literally -> clarify simply."""
    spec = [("her", "so what do you do"), ("me", "Male stripper"), ("her", "wait seriously?? that's kinda weird")]
    llm = FakeLLM(out([("tease_frame", "Only on weekends ;)"), ("clarify", "I thought it was obvious I was kidding lol")],
                      type="confusion", temp="negative"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.move_id == "clarify"


def test_date_today_silent_after_confirm():
    """#14: 30 min of silence -> nothing; 3h+ -> the time-sensitive takeaway."""
    spec = [("me", "How's Thursday, say 9?"), ("her", "Perfect"), ("me", "Thursday it is ;)")]
    raw, _ = thread(spec, lon(2026, 9, 21, 19))
    t_confirm = lon(2026, 9, 24, 14, 0)
    raw2 = raw + [{"speaker": "me", "text": "Hey, happy Thursday", "sent_at": lon(2026, 9, 24, 11, 30).isoformat()},
                  {"speaker": "me", "text": "Still good for tonight?", "sent_at": t_confirm.isoformat()}]
    llm = FakeLLM()
    d1 = engine.decide(raw2, thread_id="t1", her_tz=LON, now=t_confirm + timedelta(minutes=30), llm=llm)
    assert d1.action == "wait"
    d2 = engine.decide(raw2, thread_id="t1", her_tz=LON, now=t_confirm + timedelta(hours=3, minutes=5), llm=llm)
    assert d2.should_send and d2.text == "If you're too nervous, I'd understand"


def test_warm_run_without_close_prompts_a_soft_close():
    """#20 (TB77): after a run of warm replies with no move toward meeting, the prompt says soft close."""
    spec = [("her", "hey"), ("me", "Hey trouble"), ("her", "haha why trouble"), ("me", "You have that look"),
            ("her", "what look lol"), ("me", "The fun kind"), ("her", "hmm I like that answer"), ("me", "I thought you might"),
            ("her", "you're funny haha"), ("me", "I have my moments"), ("her", "haha clearly. I just got back from yoga")]
    llm = FakeLLM(out([("soft_close", "We should get together sometime soon")], type="statement"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert "time to soft close" in llm.last_user
    assert "soft_close" in d.debug["menu"] and d.move_id == "soft_close"


def test_stale_scheduled_reply_is_rewritten_not_sent():
    settings.save({"honor_timing": True})
    spec = [("me", "Hey trouble"), ("her", "haha hi")]
    llm = FakeLLM(out([("answer_and_pivot", "Ah, a local")]), out([("answer_and_pivot", "Look at us, already chatting")]))
    raw, last = thread(spec, lon(2026, 9, 22, 15))
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(minutes=1), llm=llm)
    assert d.action == "wait" and d.text == "Ah, a local"
    d2 = engine.decide(raw, thread_id="t1", her_tz=LON, now=last + timedelta(hours=10), llm=llm)
    assert llm.n == 2 and d2.text == "Look at us, already chatting"


def test_her_availability_question_is_scheduling_not_a_proposal():
    """'what are you up to this weekend?' is a buying signal: lock a specific night (hard close)."""
    spec = [("her", "hey"), ("me", "Hey trouble"), ("her", "haha hi"), ("me", "Ah, a local"),
            ("her", "so what are you up to this weekend?")]
    llm = FakeLLM(out([("accept_her_proposal", "Perfect. Saturday it is ;)"), ("hard_close", "Hopefully seeing you. How's Saturday, say 9?")],
                      type="buying_question"))
    d, _, _ = run(spec, lon(2026, 9, 22, 18), llm=llm)
    assert d.state["date_state"] == "SCHEDULING"
    assert d.move_id == "hard_close"


# ---------------------------------------------------------------------------
# date set: keep-warm, reminder, after the date
# ---------------------------------------------------------------------------

PLAN_SAT = [("me", "We should get together sometime soon"), ("her", "yes!"), ("me", "How's Saturday, say 9?"),
            ("her", "perfect"), ("me", "Saturday it is ;)")]


def test_date_set_quiet_chat_gets_keep_warm_never_a_reengage():
    raw, last = thread(PLAN_SAT, lon(2026, 9, 21, 19))            # Monday, date Saturday
    llm = FakeLLM(out([("keep_warm", "Hey, how's your week going?")]))
    d0 = engine.decide(raw, thread_id="t1", her_tz=LON, now=lon(2026, 9, 22, 12), llm=llm)
    assert d0.action == "wait" and d0.follow_up_reason == "keep_warm"
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=d0.follow_up_at + timedelta(minutes=1), llm=llm)
    assert "reengage_mild" not in d.debug["menu"]
    assert d.should_send and d.move_id == "keep_warm"


def test_reminder_is_scheduled_for_the_evening_before():
    raw, last = thread(PLAN_SAT, lon(2026, 9, 21, 19))
    engine.mark_sent("t1", "Hey, how's your week going?", move_id="keep_warm", sent_at=lon(2026, 9, 23, 13))
    raw2 = raw + [{"speaker": "me", "text": "Hey, how's your week going?", "sent_at": lon(2026, 9, 23, 13).isoformat()},
                  {"speaker": "her", "text": "good! busy", "sent_at": lon(2026, 9, 23, 14).isoformat()},
                  {"speaker": "me", "text": "Adulting I see", "sent_at": lon(2026, 9, 23, 15).isoformat()}]
    d = engine.decide(raw2, thread_id="t1", her_tz=LON, now=lon(2026, 9, 24, 10), llm=FakeLLM())
    assert d.action == "wait" and d.follow_up_reason == "reminder_night_before"
    assert local(d.follow_up_at, LON).date() == lon(2026, 9, 25, 12).date() and 18 <= local(d.follow_up_at, LON).hour <= 20
    d2 = engine.decide(raw2, thread_id="t1", her_tz=LON, now=d.follow_up_at + timedelta(minutes=1), llm=FakeLLM())
    assert d2.should_send and "tomorrow" in d2.text.lower()


def test_she_was_on_her_way_then_silence_means_post_date_callback():
    spec = PLAN_SAT + [("me", "Still good for tonight?", 60 * 24 * 5), ("her", "yes!"), ("me", "Perfect. Text me when you're on the way"),
                       ("her", "omw", 60 * 3), ("me", "Cool, see ya soon ;)")]
    raw, last = thread(spec, lon(2026, 9, 21, 19))
    llm = FakeLLM(out([("post_date_callback", "That was fun")]))
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=lon(2026, 9, 27, 11), llm=llm)
    assert d.state["date_state"] == "MET"
    assert d.should_send and d.move_id == "post_date_callback"


def test_confirmed_date_passed_in_silence_asks_you_once():
    spec = PLAN_SAT + [("me", "Still good for tonight?", 60 * 24 * 5), ("her", "yes!")]
    raw, last = thread(spec, lon(2026, 9, 21, 19))
    llm = FakeLLM()
    d = engine.decide(raw, thread_id="t1", her_tz=LON, now=lon(2026, 9, 27, 12), llm=llm)
    assert d.action == "handoff" and "happened" in d.handoff_reason
    engine.thread_control("t1", "clear_handoff")
    d2 = engine.decide(raw, thread_id="t1", her_tz=LON, now=lon(2026, 9, 27, 13), llm=FakeLLM(out([("answer_and_pivot", "x")])))
    assert d2.action != "handoff" or "happened" not in d2.handoff_reason
