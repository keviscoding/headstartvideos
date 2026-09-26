"""The move library: Alex's moves, each with the conditions that license it.

This replaces the Flash-compressed playbook. The model never sees the whole
library, only the handful of moves whose preconditions hold right now, with
modern-Alex examples of each. That is what stops a line that is perfect in
one situation (e.g. "If you're too nervous, I'd understand") from firing in
another (a receptive girl, TGB73).

Two kinds of conditions:
  pre(ctx)  - deterministic, checked BEFORE the model call (state, timers, facts)
  post(ctx) - checked AFTER, using the model's reading of her last message
              (message type, sexual light, temperature). A candidate whose move
              fails post() is thrown away.

Sources are PWF transcript ids (TG = Text Game, TB = Text Breakdown,
TGB = Text Game Breakdown); see the notes file for the full evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from . import lexicon as L
from .facts import near_bar
from .models import PLAN_STATES, State
from .timeparse import local

APP_PLATFORMS = {"tinder", "bumble", "hinge", "feeld", "okcupid", "raya", "happn", "badoo", "grindr", "boo", "app"}


# ---------------------------------------------------------------------------
# Context passed to every condition
# ---------------------------------------------------------------------------

@dataclass
class Ctx:
    s: State
    facts: dict[str, Any]
    settings: dict[str, Any]
    trigger: str                          # "reply" (her message is newest) | "followup" (mine is newest)
    used: set[str] = field(default_factory=set)
    a: dict[str, Any] = field(default_factory=dict)   # model analysis (empty before the call)
    flags: dict[str, Any] = field(default_factory=dict)

    # --- convenience -------------------------------------------------------
    @property
    def ht(self) -> dict[str, Any]:
        return self.s.her_msg_heuristics or {}

    @property
    def ds(self) -> str:
        return self.s.date.state

    @property
    def plan(self) -> bool:
        return self.ds in PLAN_STATES

    def _plan_day_delta(self) -> int | None:
        if not self.s.date.when:
            return None
        d_plan = local(self.s.date.when, self.s.her_tz).date()
        d_now = local(self.s.now, self.s.her_tz).date()
        return (d_plan - d_now).days

    @property
    def plan_today(self) -> bool:
        return self.plan and self._plan_day_delta() == 0

    @property
    def plan_tomorrow(self) -> bool:
        return self.plan and self._plan_day_delta() == 1

    @property
    def hours_to_plan(self) -> float | None:
        if not self.s.date.when:
            return None
        return (self.s.date.when - self.s.now).total_seconds() / 3600

    @property
    def her_local_hour(self) -> int:
        return local(self.s.now, self.s.her_tz).hour

    @property
    def her_type(self) -> str:
        t = self.a.get("her_msg_type")
        if t:
            return t
        h = self.ht
        if h.get("decline"):
            return "cancel"
        if h.get("asks_when") or h.get("asks_where"):
            return "buying_question"
        if h.get("asks_about_me"):
            return "question_to_you"
        if h.get("low_effort"):
            return "low_effort"
        return "statement"

    @property
    def light(self) -> str:
        return self.a.get("sexual_light", "unknown")

    @property
    def temp(self) -> str:
        return self.a.get("temperature", "neutral")

    @property
    def esl(self) -> int:
        try:
            return int(self.a.get("esl_level", 0) or 0)
        except Exception:
            return 0

    @property
    def implicit_only(self) -> bool:
        culture = self.a.get("culture_mode", "default")
        return culture in ("latin", "eastern_eu", "asian", "conservative") or self.esl >= 2 or self.facts.get("profile_gate") == "vanilla"

    @property
    def is_app(self) -> bool:
        return (self.s.platform or "").lower() in APP_PLATFORMS or self.s.origin == "app_match"

    @property
    def is_ig(self) -> bool:
        return (self.s.platform or "").lower() in ("instagram", "ig") or self.s.origin in ("ig_cold_dm", "ig_follow_back")

    @property
    def serious(self) -> bool:
        return bool(self.a.get("serious_event")) or self.her_type == "sincere_serious" or bool(self.s.risk.get("serious"))

    @property
    def sexual_ceiling(self) -> int:
        cap = min(int(self.settings.get("sexual_cap", 60)), int(self.facts.get("sexual_comfort", 60) or 60))
        if self.implicit_only:
            cap = min(cap, 45)
        if self.light == "red":
            cap = min(cap, 25)
        elif self.light == "yellow":
            cap = min(cap, 45)
        elif self.light in ("unknown",):
            cap = min(cap, 45)
        return cap

    def warm(self) -> bool:
        return self.temp != "negative" and not self.ht.get("negative")

    def my_last_was_plan_ask(self) -> bool:
        m = self.s.my_last
        return bool(m and (L.SOFT_CLOSE.search(m.text) or L.SCHEDULE_ASK.search(m.text) or L.CONFIRM_Q.search(m.text)
                           or re.search(r"\bhow'?s\s+\w+day|\?\s*$", m.text) and re.search(r"(night|evening|tonight|tomorrow|\d)", m.text, re.I)))

    def _is_today(self, m) -> bool:
        if not m or not m.sent_at:
            return False
        return local(m.sent_at, self.s.her_tz).date() == local(self.s.now, self.s.her_tz).date()

    @property
    def my_last_today(self) -> bool:
        return self._is_today(self.s.my_last)

    @property
    def her_last_today(self) -> bool:
        return self._is_today(self.s.her_last)

    @property
    def quiet_for_h(self) -> float:
        vals = [v for v in (self.s.since_my_last_h, self.s.since_her_last_h) if v is not None]
        return min(vals) if vals else 0.0


# ---------------------------------------------------------------------------
# Move definition
# ---------------------------------------------------------------------------

def _true(_: Ctx) -> bool:
    return True


@dataclass
class Move:
    id: str
    family: str
    when: str                    # shown to the model: the situation this is for
    how: str                     # shown to the model: how to do it
    examples: list[str]
    pre: Callable[[Ctx], bool] = _true
    post: Callable[[Ctx], bool] = _true
    priority: int = 50
    max_words: int = 14
    presupposes_plan: bool = False
    followup: bool = False       # can be sent when SHE hasn't replied (a timer move)
    sources: str = ""
    reactive: bool = False       # answers a specific TYPE of message from her (licensed by the model's reading)

    def card(self, ctx: Ctx, compact: bool = False) -> str:
        exs = [fill(e, ctx) for e in self.examples]
        exs = [e for e in exs if e]
        if compact:
            ex = " / ".join(f'"{e}"' for e in exs[:4])
            return f"[{self.id}] {self.when} {self.how} Max {self.max_words} words. e.g. {ex}"
        ex = "\n".join(f'    - "{e}"' for e in exs)
        return (f"[{self.id}] ({self.family}) WHEN: {self.when}\n  HOW: {self.how}\n  MAX WORDS: {self.max_words}\n"
                f"  ALEX EXAMPLES (adapt, don't copy blindly):\n{ex}")


def fill(example: str, ctx: Ctx) -> str:
    """Fill {slots} from your facts. Examples whose slots are empty are dropped."""
    f = ctx.facts
    pet = f.get("pet") or {}
    slots = {
        "activity": (f.get("activities") or [""])[0],
        "drink": (f.get("drinks_at_home") or [""])[0],
        "place_feature": f.get("place_feature", ""),
        "near_bar": near_bar(f),
        "pet": pet.get("type", "") if pet.get("has_pet") else "",
        "pet_name": pet.get("name", "") if pet.get("has_pet") else "",
        "work_line": f.get("work_line", ""),
        "first_name": f.get("first_name", ""),
        "height": f.get("height", ""),
    }
    needed = re.findall(r"\{(\w+)\}", example)
    for n in needed:
        if not slots.get(n):
            return ""
    for n in needed:
        example = example.replace("{" + n + "}", str(slots[n]))
    return example


# ---------------------------------------------------------------------------
# Condition helpers
# ---------------------------------------------------------------------------

def reply(c: Ctx) -> bool:
    return c.trigger == "reply"


def followup(c: Ctx) -> bool:
    return c.trigger == "followup"


def not_serious(c: Ctx) -> bool:
    return not c.serious


def playful_ok(c: Ctx) -> bool:
    return c.her_type not in ("sincere_serious", "concern", "confusion", "hostility", "cancel", "boundary") and not c.serious


def type_is(*types: str) -> Callable[[Ctx], bool]:
    return lambda c: c.her_type in types


def used(mid: str) -> Callable[[Ctx], bool]:
    return lambda c: mid in c.used


def real_reason_now(c: Ctx) -> bool:
    """Her newest message gives a real reason (work, sick, family...): sincerity, never a takeaway (TGB25f)."""
    return c.trigger == "reply" and bool(c.ht.get("decline_reason") or c.a.get("cancel_has_reason"))


def plan_ask_in_tail(c: Ctx) -> bool:
    """One of my unanswered tail messages asked to meet / proposed a time."""
    return any(L.SOFT_CLOSE.search(t) or L.SCHEDULE_ASK.search(t) or L.CONFIRM_Q.search(t) for t in (c.s.my_tail or []))


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------

MOVES: list[Move] = [
    # ---------------- openers ----------------
    Move(
        "open_reply_to_her_opener", "opener",
        "She messaged first and you haven't replied yet.",
        "Short and confident. If her opener is a test/tease, pass it playfully (agree & exaggerate, flip it, or a short direct answer). "
        "If it's 'hey/how are you', answer in a few words plus one hook. Never an interview question.",
        ["Ah, I was waiting for you to message me ;)", "Guilty", "There's a first for everything",
         "Good, just finished a big {activity}. You?", "Hola. Hablas ingles?"],
        pre=lambda c: reply(c) and c.s.is_first_contact and c.s.she_opened,
        priority=90, max_words=12, sources="TB83 TG66 TG33",
    ),
    Move(
        "open_photo_observation", "opener",
        "First message to her; you know something specific from her photos/bio.",
        "One unique, playful observation about something in her photos (not her body, not the obvious thing every guy mentions). 1 sentence.",
        ["Ah, got your outfit all picked out already", "Save some wine for me 😉", "I like your majestic poses",
         "Wow, armed & dangerous. I'm intrigued", "Ah Yerba Mate, my favorite 😉"],
        pre=lambda c: c.s.n_msgs == 0 or (c.s.is_first_contact and not c.s.she_opened),
        priority=80, max_words=10, sources="TGB112 TG66 TB92",
    ),
    Move(
        "open_simple", "opener",
        "First message; nothing specific to riff on.",
        "A tried short opener. On Instagram cold DMs use 'Hey, I like your style 👋' (never 'Hey trouble').",
        ["Hey, I like your style 👋", "You seem like potentially my type", "Hey trouble"],
        pre=lambda c: c.s.n_msgs == 0 or (c.s.is_first_contact and not c.s.she_opened),
        priority=70, max_words=8, sources="TGB97 TGB112 TB92",
    ),

    # ---------------- vibing ----------------
    Move(
        "answer_and_pivot", "vibe",
        "She said something and none of the specific moves below fit better.",
        "Reply to ONE thread of hers (not all of them), briefly, then add a statement, tease or we-frame that moves things forward. "
        "If you asked the last question, don't ask another; make a statement. Match her energy and length.",
        ["Adulting I see", "Sounds like my typical Friday", "Ah, a local", "You had a big day there",
         "Damn girl, they work you hard", "Ah, you're getting nice and toned for our date I see"],
        pre=lambda c: reply(c) and not c.s.is_first_contact,
        post=lambda c: c.her_type not in ("sincere_serious", "hostility"),
        priority=40, max_words=14, sources="TG57 TGB49 TG66",
    ),
    Move(
        "busy_value_soft_close", "vibe",
        "She asked how your day is / what you're up to, and no date exists yet.",
        "Say what you're doing (something true from your facts) and tie it to 'our date'. Playful presupposition; it is NOT a real plan. "
        "Use at most once per phase.",
        ["Good, just finished a big {activity}. Looking nice & fit for our date",
         "Pretty good, just finished {work_line}. You?"],
        pre=lambda c: reply(c) and c.ht.get("asks_about_me") and c.ds in ("NONE", "INTEREST") and c.s.our_date_uses_recent == 0
        and bool((c.facts.get("activities") or [None])[0] or c.facts.get("work_line")),
        post=lambda c: c.her_type not in ("sincere_serious", "hostility", "confusion", "concern") and c.temp != "negative",
        priority=72, max_words=14, sources="TG81 TGB25a TGB82",
    ),
    Move(
        "tease_frame", "vibe",
        "Banter is flowing and she's playful.",
        "One playful device: we-frame ('Look at us...'), playful misinterpretation, judge frame ('I'll be the judge of that'), "
        "exaggeration ('Is that in Africa?'), attire role-play, or a cold read. Keep it light; then move forward next.",
        ["Look at us, making cute nicknames already", "How many did you make me? 😉", "I'll be the judge of that",
         "Is that in Africa?", "Confident, are we", "Depends on the vibe...", "I'm imagining you in a very professional, classy outfit now 😉",
         "You strike me as quite open minded & adventurous"],
        pre=lambda c: reply(c) and not c.s.is_first_contact,
        post=lambda c: playful_ok(c) and c.temp != "negative" and c.esl < 2,
        priority=48, max_words=12, sources="TB98 TGB91 TG81 TG72",
    ),
    Move(
        "where_from", "vibe",
        "Early in the chat and you don't know her background.",
        "Relevant to her (people love talking about their roots). Only once.",
        ["Are you from here originally?", "Ah, where'd you grow up?"],
        pre=lambda c: reply(c) and c.s.exchanges <= 10 and "where_from" not in c.used,
        post=playful_ok,
        priority=38, max_words=8, sources="TGB97 TGB103",
    ),
    Move(
        "screen_looking_for", "vibe",
        "A few exchanges in; you don't know what she wants; your profile is flirty/sexual.",
        "Direct but light screening question, once. If she answers 'fun/see where it goes', go straight to a soft close next.",
        ["What are ya looking for on here", "So what brings you on here?"],
        pre=lambda c: reply(c) and c.s.exchanges >= 3 and not c.is_ig and c.facts.get("profile_gate") in ("flirty", "sexual")
        and "screen_looking_for" not in c.used and c.ds in ("NONE",),
        post=playful_ok,
        priority=42, max_words=8, sources="TGB97 TGB55 TG54",
    ),
    Move(
        "troll_start", "vibe",
        "She's playful and teasing, English is easy for her, and no troll is running.",
        "An obviously absurd, fictional answer she can't take literally. NEVER a fake job, relationship status, or anything she might "
        "believe (a literal-read troll caused a flake in TG90). Plan to end it within 2 exchanges.",
        ["Christian Mingle ofcourse", "Dj Khaled's house. He threw me a surprise party", "I gave up serial killing for Lent",
         "Jesus"],
        pre=lambda c: reply(c) and not c.s.troll_active and c.s.exchanges >= 2,
        post=lambda c: playful_ok(c) and c.esl == 0 and c.temp == "positive" and not c.implicit_only,
        priority=35, max_words=10, sources="TGB91 TGB64 TG90",
    ),
    Move(
        "troll_close", "vibe",
        "A troll/role-play has run 2+ exchanges.",
        "Drop the stick and move forward, leaving a little curiosity for the date.",
        ["Perhaps I'll show you on our date ;)", "I'll tell you all about it over a drink", "Correct ;)"],
        pre=lambda c: reply(c) and c.s.troll_active,
        priority=76, max_words=10, sources="TGB91 TGB64",
    ),

    # ---------------- her tests / concerns ----------------
    Move(
        "pass_shit_test", "test",
        "She's teasing/testing you (congruence test), not asking a real question.",
        "Emotional, playful, unbothered. Pick ONE: agree & exaggerate, flip it back, a short direct answer, or the polar opposite. "
        "No justification, no logic, no apology.",
        ["Guilty", "Not at all", "Yes, confidence comes from consistent results", "Nope.", "Ew. I hate sex",
         "I'm actually a shy virgin", "Are you doubting my skills?", "Nah, you're not ready for my pick up lines yet"],
        pre=reply,
        post=type_is("shit_test"),
        priority=85, max_words=10, sources="TG33 TGB34 TG93 TB74",
        reactive=True,
    ),
    Move(
        "answer_concern", "test",
        "She raised a real concern or boundary (safety, meeting at your place, too fast, age, 'not sleeping with you', expectations, real?).",
        "Plain reassurance + a touch of humour + keep moving toward the meet. Short. Never defensive, never a lecture. "
        "Safety at your place -> offer a bar near you first. 'Not sleeping with you' -> 'No expectations on my end'. "
        "'Only a hookup?' as a question -> it depends on the connection; as an accusation -> make her question the assumption.",
        ["Stranger danger? Lol. I understand. We can meet at {near_bar} first",
         "Stranger danger? Lol. I understand. We can meet at a bar near me first",
         "No expectations on my end. I like to meet and see how the chemistry is",
         "And when exactly did I say I was looking for just a hookup?",
         "Age is just a number. It always comes down to the person, not the year on their driver's license",
         "I appreciate the honesty. But no, I don't think it's moving fast at all",
         "Yes. You as well I hope"],
        pre=reply,
        post=type_is("concern", "boundary"),
        priority=88, max_words=30, sources="015 TGB61 TB62 TG60",
        reactive=True,
    ),
    Move(
        "clarify", "test",
        "She's confused by your last message.",
        "Clarify simply and move on. No game, no sarcasm.",
        ["Joke", "I thought it was obvious I was kidding lol", "I mean the drinks, not the dog"],
        pre=reply, post=type_is("confusion"), priority=86, max_words=14, sources="TG33 TG90",
        reactive=True,
    ),
    Move(
        "sincere_support", "sincere",
        "She shared something genuinely serious (illness, loss, a real problem) or apologised sincerely with a real reason.",
        "Sincerity for sincerity. Brief empathy proportional to the severity, maybe one curious question. No takeaway, no troll, no plans, no sexual content.",
        ["Ah damn. Are you ok?", "Sorry to hear that. Just out of curiosity, what's been going on?", "Oh shit, sorry to hear that. Hope you feel better soon"],
        pre=reply, post=lambda c: c.serious or c.her_type == "sincere_serious",
        priority=95, max_words=18, sources="TGB25f TGB73",
        reactive=True,
    ),
    Move(
        "deny_validation", "vibe",
        "She's fishing for validation (unsolicited pics, 'in case you forgot I'm hot') rather than moving toward meeting.",
        "A tiny amount of playful validation or a playful deny, then steer to meeting. Never gush.",
        ["You are?", "Not bad. I'd prefer to see that in real life", "Yea, I'd prefer to see that in 3D"],
        pre=reply, post=type_is("validation_fishing"), priority=80, max_words=10, sources="TB59 TGB70",
        reactive=True,
    ),
    Move(
        "unbothered", "vibe",
        "She's being rude or hostile.",
        "Unbothered, short, never argue, never insult back. Often best to say almost nothing.",
        ["Ofcourse not...", "Read a book", "Lol"],
        pre=reply, post=type_is("hostility"), priority=70, max_words=6, sources="TB59 TGB88",
        reactive=True,
    ),

    # ---------------- closing ----------------
    Move(
        "soft_close", "close",
        "Banter is warm, she's invested enough, and no meeting idea is agreed yet (or the old one went stale).",
        "Get her to agree to the GENERAL IDEA of meeting. No day, no time, no place yet. Easy to say yes to.",
        ["We should get together sometime soon", "We should split a bottle sometime soon", "Good. We should get together sometime soon then",
         "We should celebrate", "I'll tell you all about it over a bottle of {drink}"],
        pre=lambda c: c.ds in ("NONE",) or (c.ds == "INTEREST" and (c.s.date.expired_plan or any("stale" in n or "re-close" in n or "fresh soft close" in n for n in c.s.date.notes))),
        post=lambda c: c.her_type not in ("sincere_serious", "hostility", "confusion", "concern", "boundary", "cancel") and c.temp != "negative",
        priority=66, max_words=12, sources="TGB97 TGB88 TGB76",
    ),
    Move(
        "schedule_ask", "close",
        "She agreed to the idea of meeting (or asked 'when?') but no day is set.",
        "Find out her availability. Ask about her schedule, or offer two options using weekday names. Don't pull a random night out of thin air.",
        ["What's your schedule like?", "Soon. What's your schedule like", "Are you free Thursday or Friday night?"],
        pre=lambda c: (c.ds == "INTEREST" and not c.s.date.expired_plan) or c.ht.get("asks_when"),
        post=lambda c: c.her_type not in ("sincere_serious", "hostility", "cancel"),
        priority=74, max_words=10, sources="TGB67 TGB97",
    ),
    Move(
        "hard_close", "close",
        "You know her schedule (or she offered days) and nothing specific is agreed.",
        "Lock a specific night she is actually free (not one she'd have to squeeze you into). Ask, don't assume: 'How's 9?'. "
        "Use weekday names, not 'tonight/tomorrow', if it's after midnight for her.",
        ["How's Thursday, say 9?", "Cool, let's do Friday night. How's 9?", "How's Thurs or Fri night?"],
        pre=lambda c: c.ds in ("SCHEDULING",) or (c.ds == "PROPOSED" and c.s.date.proposed_by == "me"),
        post=lambda c: c.her_type not in ("sincere_serious", "hostility"),
        priority=78, max_words=12, sources="TGB67 TGB97 TG90",
    ),
    Move(
        "accept_her_proposal", "close",
        "She proposed a specific day/time.",
        "Accept crisply (if it works for you) and lock it. No extra questions.",
        ["Perfect. Thursday it is ;)", "Yep that works", "Sounds good. Say 9?"],
        pre=lambda c: c.ds in ("PROPOSED", "RESCHEDULING") and c.s.date.proposed_by == "her",
        priority=90, max_words=8, sources="TG72 TG87",
    ),
    Move(
        "likelihood_check", "close",
        "She flaked before and now proposes a date several days away.",
        "Playfully make her commit.",
        ["Hypothetically speaking, if we do Friday, what's the likelihood you'll show up?"],
        pre=lambda c: c.ds == "PROPOSED" and c.s.date.proposed_by == "her" and (c.s.incidents.flakes_no_reason + c.s.incidents.flakes_with_reason) >= 1
        and c.s.date.proposed_when is not None and (c.s.date.proposed_when - c.s.now) > timedelta(days=2),
        priority=86, max_words=16, sources="TG96",
    ),
    Move(
        "same_night_close", "close",
        "She's very invested (or asked when) and it's still early enough in her evening.",
        "Try for tonight, casually. If she can't, don't push; schedule another night.",
        ["You feeling spontaneous tonight?", "Good. What are you doing tonight?"],
        pre=lambda c: reply(c) and c.ds in ("NONE", "INTEREST") and c.s.exchanges >= 6 and 12 <= c.her_local_hour <= 21
        and (c.s.investment == "high" or c.ht.get("asks_when")),
        post=lambda c: c.her_type not in ("sincere_serious", "hostility", "cancel", "concern") and c.temp == "positive",
        priority=60, max_words=8, sources="TGB34-11 TGB112-3 TGB76",
    ),
    Move(
        "place_suggest", "logistics",
        "A day is agreed/proposed and the place isn't settled (or she asks where).",
        "Your place first if you can host; if she's hesitant, a bar near you. Never a place near her by default.",
        ["We can split a bottle on my {place_feature}", "Let's meet at {near_bar}. It's a super chill spot",
         "If you'd feel more comfortable we can meet at {near_bar} first", "Come over, I'll open a bottle of {drink}"],
        pre=lambda c: c.ds in ("PROPOSED", "AGREED", "REMINDED", "CONFIRMED_DAYOF") and (c.ht.get("asks_where") or not c.s.date.place_hint)
        and (bool(c.facts.get("own_place")) or bool(near_bar(c.facts))),
        post=lambda c: c.her_type not in ("sincere_serious", "hostility"),
        priority=70, max_words=14, sources="TG60 TB89 TGB97",
    ),
    Move(
        "move_to_number", "close",
        "You're on a dating app, there's some investment, and you haven't got her number.",
        "Move off the app (text/WhatsApp, not Instagram).",
        ["Do you have WhatsApp? I'm not on here much", "Shoot me your number for romance arrangement purposes", "Text is better. Shoot me your number"],
        pre=lambda c: c.is_app and c.s.exchanges >= 4 and (c.ds != "NONE" or c.s.investment != "low" or c.s.positive_run >= 3),
        post=lambda c: c.her_type not in ("sincere_serious", "hostility", "cancel", "concern"),
        priority=58, max_words=12, sources="TGB61 TG66 TB86",
    ),
    Move(
        "logistics_reply", "logistics",
        "She asked a practical question (time, where, what to wear, parking) or is coordinating.",
        "Crisp, practical answer that keeps the plan moving. No entertainment needed.",
        ["Perfect", "Yep, 9 works", "Sounds good. Text me when you're on the way", "Nothing 😉 kidding. Something comfy"],
        pre=reply, post=type_is("logistics", "buying_question"), priority=84, max_words=14, sources="TG72 TG87",
        reactive=True,
    ),

    # ---------------- date set: maintenance & confirmation ----------------
    Move(
        "keep_warm", "confirm",
        "A date is agreed 2+ days out and you haven't talked in a while.",
        "A light touch so she doesn't think you forgot. Not about logistics.",
        ["Hey, how's your week going?", "Good, just finished a big {activity}. Looking nice & fit for our date"],
        pre=lambda c: followup(c) and c.plan and c.hours_to_plan is not None and c.hours_to_plan > 30 and c.quiet_for_h >= 36
        and "keep_warm" not in c.used,
        priority=55, followup=True, max_words=12, sources="TG90",
    ),
    Move(
        "reminder_night_before", "confirm",
        "The date is tomorrow and it was set a few days ago.",
        "Playful reminder.",
        ["Pick out a cute outfit for tomorrow yet?", "Excited to meet you tomorrow 😉"],
        pre=lambda c: c.plan_tomorrow and c.ds == "AGREED" and "reminder_night_before" not in c.used
        and (reply(c) or 17 <= c.her_local_hour <= 21),
        priority=70, followup=True, presupposes_plan=True, max_words=10, sources="TGB34-5 TGB97 TGB70",
    ),
    Move(
        "dayof_warmup", "confirm",
        "It's the day of the date and you haven't messaged today.",
        "Open the day with something light before confirming.",
        ["Hola linda", "Hey, happy Friday", "Yo sexy"],
        pre=lambda c: followup(c) and c.plan_today and c.ds in ("AGREED", "REMINDED") and not c.my_last_today
        and "dayof_warmup" not in c.used and c.her_local_hour >= 10,
        priority=72, followup=True, presupposes_plan=True, max_words=6, sources="TGB103 TGB97",
    ),
    Move(
        "dayof_confirm", "confirm",
        "Day of the date; she answered your warm-up (or didn't for 3h).",
        "Confirm plainly.",
        ["Hey, still good for tonight?", "Still good for tonight?"],
        pre=lambda c: c.plan_today and c.ds in ("AGREED", "REMINDED") and "dayof_confirm" not in c.used and (
            (reply(c) and (c.her_last_today or c.my_last_today))
            or (followup(c) and c.my_last_today and (c.s.since_my_last_h or 0) >= 3)
        ),
        priority=92, followup=True, presupposes_plan=True, max_words=7, sources="TGB97 TGB34-5",
    ),
    Move(
        "text_when_otw", "confirm",
        "She just confirmed tonight.",
        "Lock the arrival protocol.",
        ["Perfect. Gonna hop in the shower, text me when you're on the way", "Perfect. Text me when you're on the way"],
        pre=lambda c: reply(c) and c.ds == "CONFIRMED_DAYOF" and "text_when_otw" not in c.used,
        priority=90, presupposes_plan=True, max_words=13, sources="TG54 TB71 TGB34-5",
    ),
    Move(
        "en_route_reply", "logistics",
        "She's on the way / arriving.",
        "Short, warm, practical.",
        ["Cool, see ya soon ;)", "Perfect. See ya soon"],
        pre=lambda c: reply(c) and c.ds == "EN_ROUTE",
        priority=90, presupposes_plan=True, max_words=8, sources="TG87 TG72",
    ),
    Move(
        "post_date_callback", "post_date",
        "The date happened (she said it was fun / made it home) and you haven't followed up.",
        "Same or next day. A callback to something from the date if you know one; otherwise simple. Then later, soft close again.",
        ["That was 🔥", "That was fun", "Make it home ok?"],
        pre=lambda c: c.ds == "MET" and "post_date_callback" not in c.used and (reply(c) or (c.s.since_her_last_h or 0) >= 8),
        priority=80, followup=True, max_words=10, sources="TG99 TG24",
    ),

    # ---------------- flakes / reschedules ----------------
    Move(
        "reschedule_accept", "flake",
        "She cancelled with a real reason (first time), especially if she offered another time.",
        "Easy-going, no guilt, no neediness. Offer or accept another time.",
        ["Sure no worries. Hope nothing bad happened", "It's cool. Feel better", "No worries. How's Thursday or Friday night instead?"],
        pre=lambda c: reply(c) and c.ds in ("RESCHEDULING", "FLAKED") and c.ht.get("decline_reason")
        and (c.s.incidents.flakes_no_reason + c.s.incidents.flakes_with_reason) <= 1,
        post=lambda c: c.her_type in ("cancel", "sincere_serious", "statement", "buying_question") or bool(c.a.get("cancelled")),
        priority=92, max_words=14, sources="TG90 TGB70",
    ),
    Move(
        "breaking_heart_reschedule", "flake",
        "She cancelled/moved a plan that was actually agreed, for a light reason.",
        "Playful 'Breaking my heart' then straight to new options. Only valid because a real plan existed.",
        ["Breaking my heart. How's Thursday or Friday night?", "Breaking my ❤️"],
        pre=lambda c: reply(c) and c.ds in ("RESCHEDULING", "FLAKED") and c.s.date.had_plan,
        post=lambda c: not c.serious and c.her_type in ("cancel", "statement"),
        priority=82, max_words=10, sources="TGB103 TGB76",
    ),
    Move(
        "solicit_concern", "takeaway",
        "She's not responding to PLANS: a same-day confirm went unanswered for 3h+, or she ignored your plan ask AND the mild "
        "nudge after it, or she cancelled an agreed plan with no reason and no new time.",
        "The first-line takeaway that surfaces what's holding her back. Only in exactly this situation, never on a receptive girl. "
        "If she answers 'who said I'm nervous', be direct and unbothered: 'Well, you keep disappearing on me'.",
        ["If you're too nervous, I'd understand"],
        pre=lambda c: (
            (followup(c) and c.s.times_known and c.s.since_my_last_h is not None and (
                (c.plan_today and c.my_last_was_plan_ask() and c.s.since_my_last_h >= 3)
                or (c.s.my_unanswered >= 2 and plan_ask_in_tail(c) and c.s.since_my_last_h >= 24 * 3)
            ))
            or (reply(c) and c.ds == "FLAKED" and not c.ht.get("decline_reason") and not c.ht.get("reschedule_offer"))
        ) and "solicit_concern" not in c.used,
        post=lambda c: not c.serious and c.her_type not in ("sincere_serious",) and not real_reason_now(c),
        priority=80, followup=True, max_words=8, sources="TG81-4 TGB73 TG96 TG90 TB74",
    ),
    Move(
        "busy_reframe", "takeaway",
        "She says she's busy in response to your plans.",
        "Non-bitchy reframe: busy is a matter of priorities.",
        ["I get it, I'm busy too. But I'm sure we can both find a few hours if we wanted to"],
        pre=lambda c: reply(c) and c.ht.get("busy") and c.s.my_plan_asks >= 1 and "busy_reframe" not in c.used,
        post=lambda c: not c.serious,
        priority=83, max_words=20, sources="TB74 TGB70 TG90",
    ),
    Move(
        "difficult_to_plan", "takeaway",
        "There's a PATTERN (2+ ignored plan asks / flakes / reschedules).",
        "Neutral, unbothered call-out. Never after a single silence.",
        ["Are you always this difficult to make plans with?", "Is there a reason you're playing these silly games?"],
        pre=lambda c: (c.s.incidents.ignored_plan_asks + c.s.incidents.flakes_no_reason) >= 1
        and (c.s.incidents.ignored_plan_asks + c.s.incidents.flakes_no_reason + c.s.incidents.flakes_with_reason) >= 2
        and "difficult_to_plan" not in c.used,
        post=lambda c: not c.serious and c.her_type not in ("sincere_serious", "concern") and not real_reason_now(c),
        priority=78, followup=True, max_words=10, sources="TB74 TG72 TG90",
    ),
    Move(
        "flaky_type", "takeaway",
        "She broke a real plan without reason and 'too nervous' didn't work, or she has flaked 2+ times.",
        "The strongest takeaway. Never when no plan was ever agreed.",
        ["I genuinely didn't take you for the flaky type"],
        pre=lambda c: c.s.date.had_plan and (
            (c.s.incidents.flakes_no_reason >= 1 and "solicit_concern" in c.used)
            or (c.s.incidents.flakes_no_reason + c.s.incidents.flakes_with_reason) >= 2
        ) and "flaky_type" not in c.used,
        post=lambda c: not c.serious and c.her_type not in ("sincere_serious", "concern") and not real_reason_now(c),
        priority=79, followup=True, max_words=10, sources="TG81-5 TB74 TG90",
    ),

    # ---------------- re-engaging (timers) ----------------
    Move(
        "reengage_mild", "reengage",
        "She hasn't replied to your last message for 2-3+ days.",
        "One short, light re-engage matched to what you last sent: after an opener -> 'Don't be shy' / 'A woman of few words I see'; "
        "after a plan ask -> 'Don't think too hard now' / 'Don't worry, it's not a trick question'; after banter -> "
        "'Thinking very hard I see' / 'Are you always this talkative?'. Never needy, never 'hello?'.",
        ["Don't be shy", "A woman of few words I see", "Don't think too hard now", "Don't worry, it's not a trick question",
         "Thinking very hard I see", "Are you always this talkative?"],
        pre=lambda c: followup(c) and not c.plan and c.s.my_unanswered == 1 and c.s.since_my_last_h is not None and c.s.since_my_last_h >= 44 and c.s.times_known,
        priority=60, followup=True, max_words=8, sources="TB74 TG96 TG66 TGB55",
    ),
    Move(
        "reengage_second", "reengage",
        "She's ignored two of your messages for about a week (and 'too nervous' doesn't apply).",
        "The next rung, still unbothered: 'Or not' / 'I guess not'. No questions about why she vanished.",
        ["Or not", "I guess not", "Did we... break up?"],
        pre=lambda c: followup(c) and not c.plan and c.s.my_unanswered == 2 and c.s.since_my_last_h is not None and c.s.since_my_last_h >= 24 * 4 and c.s.times_known,
        priority=55, followup=True, max_words=6, sources="TB74 TGB55 TGB67",
    ),
    Move(
        "reengage_sweep", "reengage",
        "The chat has been dead for a week+ (dormant lead).",
        "One cheap, fun, low-pressure message. No guilt, no questions about why she vanished.",
        ["Did we... break up?", "Good news! We should celebrate", "Hey stranger. How's your week going?"],
        pre=lambda c: followup(c) and not c.plan and c.s.my_unanswered >= 3 and c.s.since_my_last_h is not None
        and c.s.since_my_last_h >= 24 * float(c.settings.get("sweep_days", 10)) and c.s.times_known
        and c.flags.get("sweeps_sent", 0) < int(c.settings.get("max_sweeps", 3)),
        priority=45, followup=True, max_words=10, sources="TB71 TG72 TGB67",
    ),
    Move(
        "final_appeal", "takeaway",
        "Months of her dodging plans while wanting attention (validation-seeker pattern).",
        "One genuine, calm appeal. If ignored, walk away.",
        ["I've been trying to make plans with you for a while now. If you want more interest from me, you'll have to earn it. "
         "A good start would be a bit of effort to actually meet up"],
        pre=lambda c: reply(c) and (c.s.incidents.ignored_plan_asks + c.s.incidents.flakes_no_reason) >= 3 and "final_appeal" not in c.used,
        post=type_is("validation_fishing", "low_effort", "statement"),
        priority=65, max_words=40, sources="TGB70 TG90",
    ),

    # ---------------- sexual ----------------
    Move(
        "innuendo", "sexual",
        "The vibe is flirty and you want to add sexual tension without saying anything explicit.",
        "Imply, never state: double meaning / plausible deniability. If she doesn't bite, drop it (yellow light) and move to logistics.",
        ["Oh I'm sure I can find a way to keep you awake", "I'll hook you up with a massage, if ya play your cards right",
         "Very innocent things ofcourse", "5 stars on Yelp", "I'll be the judge of that"],
        pre=lambda c: reply(c) and c.s.exchanges >= 3 and c.facts.get("profile_gate") != "vanilla",
        post=lambda c: playful_ok(c) and c.light in ("green", "unknown") and c.temp != "negative",
        priority=50, max_words=14, sources="TB68 TGB31 TB83",
    ),
    Move(
        "sexual_mirror", "sexual",
        "She is escalating sexually herself (green light).",
        "Match her level (never go more than a notch above), short, confident, then bring it back to meeting within a message or two.",
        ["Mm I bet", "I like that. I get the feeling we're gonna have a good time", "Only one way to find out"],
        pre=lambda c: reply(c) and c.ht.get("sexual_level", 0) >= 25,
        post=lambda c: c.light == "green" and not c.implicit_only and c.her_type in ("sexual_escalation", "statement", "question_to_you", "playful"),
        priority=62, max_words=16, sources="TGB31 TG84 TB56",
        reactive=True,
    ),
    Move(
        "sexual_to_meet", "close",
        "The chat got sexual and she's into it; time to convert it into a meet.",
        "Bring the energy back to meeting.",
        ["I get the feeling we're gonna have a good time then", "Only one way to find out. What's your schedule like?",
         "Good. You feeling spontaneous tonight?"],
        pre=lambda c: reply(c) and c.ds in ("NONE", "INTEREST", "SCHEDULING") and c.s.exchanges >= 3
        and c.ht.get("sexual_level", 0) >= 25 and c.facts.get("profile_gate") != "vanilla",
        post=lambda c: c.light == "green" and c.her_type in ("sexual_escalation", "statement", "playful"),
        priority=70, max_words=12, sources="TG84 TGB31",
    ),

    # ---------------- safety ----------------
    Move(
        "scam_decline", "safety",
        "She's asking for money / verification fees / pushing links.",
        "One unbothered line (or nothing). Never pay, never click.",
        ["I'm good. But I'm down to link up", "Lol I'm not trying to get a bunch of pics. The internet is full of them"],
        pre=lambda c: bool(c.s.risk.get("scam")) and "scam_decline" not in c.used,
        priority=99, max_words=14, sources="TG63",
    ),
]

BY_ID = {m.id: m for m in MOVES}

# Regexes that reveal a move was already used (by v2, v1 or you manually).
USED_PATTERNS: dict[str, re.Pattern] = {
    "solicit_concern": re.compile(r"too nervous", re.I),
    "flaky_type": re.compile(r"flak(e)?y type", re.I),
    "difficult_to_plan": re.compile(r"difficult to make plans|silly games", re.I),
    "busy_reframe": re.compile(r"few hours if we", re.I),
    "screen_looking_for": re.compile(r"looking for on here|brings you on here", re.I),
    "where_from": re.compile(r"from here originally|where'?d you grow up", re.I),
    "reminder_night_before": re.compile(r"cute outfit for tomorrow|excited to meet you tomorrow", re.I),
    "dayof_confirm": L.CONFIRM_Q,
    "text_when_otw": re.compile(r"when you'?re on the way|text (me )?when (on|you'?re)", re.I),
    "final_appeal": re.compile(r"trying to make plans with you for", re.I),
    "post_date_callback": re.compile(r"that was (fun|🔥|fire)|make it home", re.I),
}
