"""Deterministic critic: every candidate reply is checked before it can go out.

Hard fails (candidate rejected):
  - date/plan words when no plan exists in THIS thread ("still good for
    tonight", "breaking my heart", "flaky type" ...), stale or past days,
    tonight/tomorrow after midnight, contradicting the agreed day
  - Alex's biography or any fact about you that isn't in your fact sheet
  - neediness, pedestalising, pickup lines, assistant-speak, apologies
  - emoji/punctuation abuse, repeated lines, over-length, sexual content above
    the current ceiling (her light, culture, your caps)
Soft penalties rank the survivors (length vs her investment, question streaks,
ask-backs, "our date" overuse, sentence count).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from . import lexicon as L
from .facts import forbidden_bio_patterns
from .models import PLAN_STATES
from .moves import BY_ID, Ctx
from .timeparse import find_day_time_refs, has_relative_day_words, local

EMOJI_RX = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤❣]"
)
EMOTICON_RX = re.compile(r"(?:(?<=\s)|^)(?:;\)|:\)|=\)|;-\)|:-\)|:D|;D|:P|;P|<3)(?=\s|$)")
FORBIDDEN_EMOJI = set("🥺✨🥰😍💕💖💗💞😘🤗🌸💐🥹😻💓💘")
FORBIDDEN_EMOJI_RX = re.compile("❤️|❤|💕|💖|💗|💞|🥺|✨|🥰|😍|😘|🤗|🥹|💘|💓")

PLAN_PHRASES = re.compile(
    r"\bstill (good|on|down|set) for\b|\bsee (you|ya) (tonight|tomorrow|tmrw|then|at|later tonight)\b|\bpick(ed)? out a cute outfit\b|"
    r"\bexcited to meet you (tonight|tomorrow)\b|\bon the way\b|\bhop in the shower\b|\bmy address\b|\bwhen you'?re (ready|close|here)\b",
    re.I,
)
TAKEAWAY_PHRASES = {
    "breaking": re.compile(r"breaking my (heart|❤)", re.I),
    "flaky": re.compile(r"\bflak(e)?y\b", re.I),
    "nervous": re.compile(r"too nervous", re.I),
    "difficult": re.compile(r"difficult to make plans|silly games", re.I),
    "stop_trying": re.compile(r"stop trying|not into this", re.I),
}
PICKUP = re.compile(r"\bare you (a|an) [\w ]{1,20}\?? (because|cause|cuz)\b|\bdid it hurt\b|\bis your name \w+\b|\bcause you'?ve got\b|\bif you were a\b", re.I)
PEDESTAL = re.compile(
    r"\b(it'?s an hono(u)?r|hono(u)?red to|the (cutest|prettiest|hottest|most beautiful) (girl|woman|one)|let me (buy|take) you|would you let me|"
    r"are there other (guys|boys|men)|out of my league|i'?m obsessed|can'?t stop thinking about you|you'?re (so |literally )?perfect|"
    r"you'?re way too (hot|pretty|good) for)\b",
    re.I,
)
NEEDY = re.compile(
    r"\b(why (are|r) (you|u) ignoring|are (you|u) ignoring|did i do something|is it something i said|i thought we|why didn'?t (you|u) (reply|answer|text)|"
    r"please (reply|answer|respond)|i miss(ed)? you|wish you were here|hello\?|you there\?|still there\?|just checking in)\b",
    re.I,
)
ASSISTANT = re.compile(
    r"\b(i understand how you feel|that'?s (totally |completely )?valid|i appreciate you sharing|thank you for sharing|thanks for sharing|"
    r"i hope you'?re doing well|feel free to|no pressure|i respect (that|your)|as an ai|i'?d be happy to|great question|"
    r"i'?m here for you|take all the time you need|i totally get it|communication is key)\b",
    re.I,
)
APOLOGY = re.compile(r"\b(sorry|my bad|apologi[sz]e|i didn'?t mean)\b", re.I)
SELF_BOY = re.compile(r"\b(cute|nice|good|internet|sweet) (boy|boys)\b|\bboys like me\b", re.I)
ASKBACK = re.compile(r"(how about you|what about you|and you|\bwbu|\bhbu|\band u)\s*\??\s*$", re.I)
FAB_POSSESSIONS = re.compile(r"\bmy (dog|husky|puppy|pup|cat|kitty|balcony|patio|terrace|rooftop|jacuzzi|hot tub|sauna|podcast|car|boat|yacht|roommates?|girlfriend|wife|kids?|son|daughter|pool)\b", re.I)
JOB_CLAIM = re.compile(r"\bi'?m (a|an) (male )?(stripper|sexologist|doctor|lawyer|pilot|model|millionaire|surgeon|ceo|dentist|firefighter|cop|nurse|coach|youtuber)\b", re.I)
HEIGHT_CLAIM = re.compile(r"\b\d'\s?\d{1,2}\b|\b\d(\.\d)?\s?(ft|feet)\b|\b1\d\d\s?cm\b", re.I)
LIVE_ALONE = re.compile(r"\b(i live alone|no roommates?|just me at my place)\b", re.I)
MY_PLACE = re.compile(r"\b(my place|come over|cum over|at mine|my apartment|my flat|swing by)\b", re.I)
CUMING = re.compile(r"\bcum+ing\b", re.I)
WEEKDAY_RX = re.compile(r"\b(mon|tues|wednes|thurs|fri|satur|sun)day\b", re.I)


@dataclass
class Verdict:
    ok: bool
    score: float
    fails: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def postprocess(text: str, settings: dict) -> str:
    t = (text or "").strip()
    t = re.sub(r"^\s*(me|alex|him|you|reply)\s*[:\-]\s*", "", t, flags=re.I)
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'“”":
        t = t[1:-1].strip()
    t = t.strip("“”\"")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"!{2,}", "!", t)
    if settings.get("strip_trailing_period", True):
        if t.endswith(".") and not t.endswith("..") and len(t.split()) <= 12 and t.count(".") == 1:
            t = t[:-1]
    return t.strip()


def emoji_count(text: str) -> int:
    return len(EMOJI_RX.findall(text)) + len(EMOTICON_RX.findall(" " + text + " "))


def check(text: str, move_id: str, ctx: Ctx, my_recent: list[str]) -> Verdict:
    s = ctx.s
    fails: list[str] = []
    notes: list[str] = []
    pen = 0.0
    low = text.lower()
    words = len(text.split())
    move = BY_ID.get(move_id)
    max_words = move.max_words if move else 14
    fam = move.family if move else ""

    if not text or not text.strip():
        return Verdict(False, -1e9, ["empty"])
    if re.search(r"[{}\[\]]|move_id|\bas an ai\b|\bJSON\b", text):
        fails.append("formatting/system leak")

    # --- facts / fabrication ---------------------------------------------
    for pat in ctx.facts.get("banned_phrases") or []:
        if pat and pat.lower() in low:
            fails.append(f"banned phrase: {pat}")
    for pat in forbidden_bio_patterns(ctx.facts):
        if re.search(pat, text, re.I):
            fails.append(f"Alex-bio / unbacked fact: /{pat}/")
    pet = ctx.facts.get("pet") or {}
    feat = (ctx.facts.get("place_feature") or "").lower()
    for m in FAB_POSSESSIONS.finditer(text):
        thing = m.group(1).lower()
        if thing in ("dog", "husky", "puppy", "pup", "cat", "kitty"):
            if not pet.get("has_pet") or (thing in ("cat", "kitty") and "cat" not in (pet.get("type") or "").lower() and pet.get("type")):
                fails.append(f"claims a pet ({thing}) not in your facts")
        elif thing in ("balcony", "patio", "terrace", "rooftop", "jacuzzi", "hot tub", "sauna", "pool"):
            if thing not in feat:
                fails.append(f"claims a {thing} not in your facts")
        elif thing.startswith("roommate"):
            if not ctx.facts.get("roommates"):
                fails.append("mentions roommates not in your facts")
        elif thing == "car":
            if not ctx.facts.get("car"):
                fails.append("claims a car not in your facts")
        else:
            fails.append(f"claims '{m.group(0)}' (not in your facts)")
    jm = JOB_CLAIM.search(text)
    if jm and jm.group(3).lower() not in (ctx.facts.get("work_line") or "").lower():
        fails.append(f"job claim not in facts: {jm.group(0)}")
    if HEIGHT_CLAIM.search(text) and not ctx.facts.get("height"):
        fails.append("height claim without a height in your facts")
    if LIVE_ALONE.search(text) and ctx.facts.get("roommates"):
        fails.append("says you live alone but you have roommates")
    if MY_PLACE.search(text) and not ctx.facts.get("own_place"):
        fails.append("invites to your place but you can't host")
    if CUMING.search(text) and not ctx.facts.get("use_cuming_typo"):
        fails.append("'cuming' joke is off in your facts")

    # --- plan / date consistency -----------------------------------------
    ds = s.date.state
    if PLAN_PHRASES.search(text) and ds not in PLAN_STATES and move_id not in ("accept_her_proposal", "hard_close", "logistics_reply"):
        fails.append(f"talks as if a date is set, but date state is {ds}")
    if TAKEAWAY_PHRASES["breaking"].search(text) and not (s.date.had_plan and ds in ("RESCHEDULING", "FLAKED")):
        fails.append("'breaking my heart' only when she cancels an agreed plan")
    if TAKEAWAY_PHRASES["flaky"].search(text) and move_id != "flaky_type":
        fails.append("'flaky' line outside the flaky_type move")
    if TAKEAWAY_PHRASES["nervous"].search(text) and move_id != "solicit_concern":
        fails.append("'too nervous' outside its move")
    if TAKEAWAY_PHRASES["difficult"].search(text) and move_id != "difficult_to_plan":
        fails.append("'difficult to make plans' outside its move")
    if TAKEAWAY_PHRASES["stop_trying"].search(text) and move_id not in ("difficult_to_plan", "final_appeal"):
        fails.append("'stop trying' takeaway outside its move")
    her_hour = local(s.now, s.her_tz).hour
    if has_relative_day_words(text) and 0 <= her_hour < 5:
        fails.append("uses tonight/tomorrow after midnight her time (use a weekday name)")
    refs = [r for r in find_day_time_refs(text, s.now, s.her_tz) if r.day is not None]
    if s.date.when is not None and fam == "confirm":
        plan_day = local(s.date.when, s.her_tz).date()
        if any(r.day != plan_day for r in refs):
            fails.append("mentions a different day than the agreed plan")
    if WEEKDAY_RX.search(text) and s.date.when is not None and fam in ("confirm",):
        pass

    # --- tone / anti-patterns ---------------------------------------------
    if PICKUP.search(text):
        fails.append("pickup line")
    if PEDESTAL.search(text):
        fails.append("pedestalising")
    if NEEDY.search(text):
        fails.append("needy")
    if ASSISTANT.search(text):
        fails.append("assistant-speak")
    if APOLOGY.search(text) and not re.search(r"sorry to hear", text, re.I) and fam not in ("sincere",):
        fails.append("apology")
    if SELF_BOY.search(text):
        fails.append("calls yourself a boy")
    if "hey trouble" in low and ctx.is_ig and s.is_first_contact:
        fails.append("'Hey trouble' doesn't work as an Instagram cold DM")
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) >= 6 and sum(ch.isupper() for ch in letters) / len(letters) > 0.6:
        fails.append("shouting caps")

    # --- form ---------------------------------------------------------------
    ec = emoji_count(text)
    if ec > 1:
        fails.append(f"{ec} emojis (max 1)")
    if FORBIDDEN_EMOJI_RX.search(text):
        fails.append("cute/feminine emoji")
    if re.search(r"\?\?|\?!|!\?", text) or text.count("!") > 1:
        fails.append("punctuation stacking")
    laughs = len(L.LAUGH.findall(text))
    if laughs > 1:
        fails.append("more than one lol/haha")
    if text.count("?") >= 2:
        fails.append("more than one question")
    if words > max_words * 1.6 + 2:
        fails.append(f"too long ({words} words, max {max_words})")
    elif words > max_words:
        pen += (words - max_words) * 2.5
        notes.append("a bit long")

    # --- sexual ceiling --------------------------------------------------
    lvl = L.sexual_level(text)
    if lvl > ctx.sexual_ceiling:
        fails.append(f"sexual level {lvl} above ceiling {ctx.sexual_ceiling} (light={ctx.light})")

    # --- repetition -----------------------------------------------------------
    for prev in my_recent[-12:]:
        if prev and difflib.SequenceMatcher(None, prev.lower(), low).ratio() >= 0.82:
            fails.append("repeats something you already sent")
            break
    if L.FRAME_PRESUPPOSE.search(text):
        if s.our_date_uses_recent >= 2:
            fails.append("'our date' overused")
        elif s.our_date_uses_recent == 1:
            pen += 15
            notes.append("'our date' used recently")

    # --- soft style penalties -------------------------------------------
    if s.investment == "low" and words > s.her_avg_words * 2 + 6:
        pen += 10
        notes.append("longer than her investment warrants")
    if s.my_question_streak >= 2 and text.strip().endswith("?") and fam not in ("close", "confirm"):
        pen += 12
        notes.append("third question in a row")
    if ASKBACK.search(text) and s.her_last is not None and "?" not in s.her_last.text and s.my_last is not None and "?" in s.my_last.text:
        pen += 15
        notes.append("asks back after she answered")
    sentences = len([x for x in re.split(r"[.!?]+", text) if x.strip()])
    if sentences > 3:
        pen += 6 * (sentences - 3)
        notes.append("too many sentences")
    if 3 <= words <= 10:
        pen -= 4
    if ec == 1 and fam in ("close", "logistics", "confirm") and not re.search(r";\)|😉", text):
        pen += 3

    score = 100 + (move.priority / 5 if move else 0) - pen
    return Verdict(ok=not fails, score=score, fails=fails, notes=notes)
