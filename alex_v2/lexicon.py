"""Deterministic detectors (regex lexicons).

These are cheap and predictable. They drive the date state machine, the
guardrails and the critic. The model's own analysis is layered on top; it
may only make the engine MORE careful, never unlock a move these
detectors rule out.
"""

from __future__ import annotations

import re
from typing import Any

I = re.I


def _rx(p: str) -> re.Pattern:
    return re.compile(p, I)


# --- planning -------------------------------------------------------------

MEET_WORDS = (
    r"get together|grab (a |some )?(drink|drinks|wine|coffee|bite|food|dinner)|meet( up)?|hang( out)?|link up|split (a|that|the)?\s*bottle|"
    r"celebrate|go out|come over|cum over|come by|swing by|come join|join me|see (you|each other)|chill|drinks?|wine|coffee|"
    r"a date|our (first )?date|rendezvous|pick you up|come through|pull up"
)
SOFT_CLOSE = _rx(
    rf"\b(we should|we'?ll have to|let'?s|lets|we could|you should|would be fun to|how about we|wanna|want to|down to)\b[^?.!]{{0,60}}\b({MEET_WORDS})\b"
    r"|\bsometime soon\b|\bone of these days\b"
    r"|\bwhen are we (hanging out|meeting|getting together|going)\b"
    r"|\b(you should|come) (come )?(over|by|join)\b"
)
# presuppositions that are FRAME, not plans
FRAME_PRESUPPOSE = _rx(r"\bour (little )?(date|romance|love|rendezvous|first date|double date)\b|\bfor our date\b")
SCHEDULE_ASK = _rx(
    r"what'?s your schedule|whats your schedule|what is your schedule|schedule (like|looking)|when (are|r) (you|u) free|"
    r"what (days|nights|evenings|day|night|evening)s? (are|r|work|is) (you|u|good|best)|when works|what works for you|"
    r"(are|r) (you|u) free|you free\b|free (tonight|tomorrow|tmrw|this|next|on|after)|what are you (doing|up to) (tonight|tomorrow|tmrw|this weekend|on|later)|"
    r"wyd (tonight|tomorrow|tmrw|this weekend|later)|how'?s (mon|tue|wed|thu|fri|sat|sun|tonight|tomorrow|tmrw)|what time (works|are you free|r u free|will you be)"
)
ACCEPT = _rx(
    r"^\W*(yes+|yess+|yeah+|yea+|yep|yup|ya|yas|sure|ok+|okay|okk|k+|sounds (good|great|fun|perfect|like a plan|amazing)|perfect|deal|definitely|def|"
    r"for sure|absolutely|i'?m down|im down|down|let'?s do it|lets do it|why not|of course|ofc|would be nice|that works|works|works for me|great|cool|"
    r"claro|s[ií]|obvio|dale|bet|i'?d love (that|to)|love to|count me in|i'?m in|im in|done|agreed|good|oki|okie|yes please|🙌|👍|😉)\b"
)
ACCEPT_ANYWHERE = _rx(r"\b(sounds good|that works|works for me|i'?m down|let'?s do it|perfect|see you (then|there|at|tonight|tomorrow)|it'?s a date|deal)\b")
DECLINE = _rx(
    r"\b(can'?t|cannot|won'?t be able|not gonna (make|be able)|(have|need|gotta) to cancel|cancel(l?ing)?|rain ?check|reschedule|something came up|"
    r"not tonight|another (time|day|night)|next time|not (feeling|feel) (well|good|great|it)|i'?m sick|im sick|feel(ing)? sick|got sick|not today|"
    r"i'?ll pass|busy (tonight|today|that night)|push (it|this) back|move (it|this) to|won'?t make it|can'?t make it|not free|i have plans|"
    r"have plans|made other plans|not anymore|no longer|have to work|working late|work late|got called in|can we (do|move|push)|rather do|won'?t work)\b"
)
DECLINE_REASON = _rx(
    r"\b(because|bc|cuz|cause|sick|work|working|shift|family|emergency|headache|migraine|tired|exhausted|kids?|son|daughter|babysit|flu|covid|"
    r"period|exam|class|school|hospital|doctor|car|flight|visiting|in town|friend'?s|birthday|funeral|dentist|meeting|deadline|mom|dad|sister|brother)\b"
)
RESCHEDULE_OFFER = _rx(
    r"\b(reschedule|another (day|time|night)|how about|what about|can we do|instead|next week|later (this|in the) week|rain ?check|"
    r"(mon|tues|wednes|thurs|fri|satur|sun)day (instead|works|is better|would be better|then)|let me know when|make it up)\b"
)
CONFIRM_Q = _rx(r"\bstill (good|on|down|up|set|free)( for (tonight|today|tomorrow|tmrw|later|this))?\b|\bare we still on\b|\bwe still on\b")
EN_ROUTE = _rx(
    r"\b(omw|on my way|leaving (now|soon|in|shortly)|heading (over|your way|out|there)|eta\b|be there in|\d+\s?min(s|utes)? (away|out)|"
    r"i'?m here|im here|i'?m outside|im outside|downstairs|in the uber|ordering (the|an|my) uber|calling the uber|parked|just parked|at the gate|pulling up)\b"
)
MET = _rx(
    r"\b(that was (fun|fire|amazing|great|awesome|so fun|🔥)|had (a|such a) (great|good|fun|amazing|lovely) time|last night was|made it home|"
    r"got home|get home safe|home safe|thanks for (tonight|last night|the wine|having me)|tonight was fun|i had fun)\b|🔥🔥"
)
BUSY = _rx(r"\b(so busy|busy|crazy week|swamped|hectic|working a lot|work a lot|no time|slammed|don'?t have time)\b")

# --- her questions --------------------------------------------------------

ASKS_ABOUT_ME = _rx(
    r"\b(how('?s| is| was) (your|ur) (day|night|week|weekend|morning|evening|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|how are (you|u)|"
    r"hbu|wbu|and you\b|and u\b|what about you|what are you (up to|doing)|wyd|what r u up to|how r u|what have you been up to|you\?$)"
)
ASKS_WHEN = _rx(
    r"\b(when (is|are|should|do|can|will|was) (the|our|we|this|that|it|you|u)\b|so when|when are we|when is this date|tell me when|"
    r"when (tho|though|then)\b|when r (we|u)\b|what day\b|which day\b)|\bwhen\s*\?"
)
ASKS_WHERE = _rx(
    r"\b(where (are|r) we (going|meeting)|where (should|do) we meet|where do (you|u) live|what part of town|where (are|r) (you|u) (located|based)|"
    r"your address|what'?s the address|address\?|which (bar|place|building))\b"
)
QUESTION = _rx(r"\?|^\s*(what|how|why|when|where|who|which|do|does|did|are|is|can|could|would|will|have|should|wyd|hbu|wbu)\b")

# --- guardrails -----------------------------------------------------------

MINOR = _rx(
    r"\b(i'?m|im|i am|turning|just turned)\s*(1[0-7]|thirteen|fourteen|fifteen|sixteen|seventeen)\b|\b(1[0-7])\s*(yo|y/o|years old)\b|"
    r"\b(high ?school|middle school|junior high|my mom won'?t let me|grade ?(7|8|9|10|11|12)|7th grade|8th grade|9th grade|10th grade|11th grade|12th grade|"
    r"underage|not 18|under 18)\b"
)
STOP = _rx(
    r"\b(stop (texting|messaging|contacting) me|leave me alone|(i'?m |im |i am )?not interested( in you|,? sorry|\s*[.!]*\s*$)|don'?t (text|message|contact) me|unmatch(ing)?|blocking you|"
    r"please stop|go away|lose my number|i have a (boyfriend|bf)|i'?m (married|engaged)|fuck off)\b"
)
SCAM = _rx(
    r"\b(cash ?app|zelle|venmo me|paypal me|gift ?cards?|send (me )?\$\d*|onlyfans|only fans|verification fee|verify (you|that you)(\'?re| are) (real|not)|"
    r"allowance|ppm|pay per meet|sugar daddy|t\.me/|whatsapp me at|premium snap|financial help|help me pay|rent money|buy me (a )?(gift|something))\b"
)
SERIOUS = _rx(
    r"\b(passed away|died|dying|funeral|in (the )?hospital|cancer|miscarriage|assault(ed)?|raped|suicid\w*|self[- ]harm|depress(ed|ion)|panic attack|"
    r"lost my (job|mom|dad|mother|father|brother|sister|grandma|grandpa)|car (accident|crash)|emergency room|\ber\b visit)\b"
)
CALL_REQUEST = _rx(r"\b(facetime|face time|video call|video chat|call me|can i call|give me a call|voice (note|memo|message)|let'?s talk on the phone|phone call)\b")
PHOTO_REQUEST = _rx(r"\b(send (me )?(a )?(pic|pics|picture|photo|selfie|another pic)|more pics|pic of you|show me (a )?(pic|photo)|what do you look like)\b")
HOSTILE = _rx(r"\b(fuck you|loser|creep(y)?|weirdo|pathetic|idiot|dumbass|ugly|disgusting|pervert|incel|get a life|you'?re (so )?(gross|annoying|boring))\b")

# --- sexual content scale (0..100) ---------------------------------------

_SEX_LEVELS: list[tuple[int, re.Pattern]] = [
    (90, _rx(r"\b(cock|dick|pussy|clit|cum(ming)? (in|on|inside)|blow ?job|bj\b|eat (you|your pussy) out|fuck(ing)? (you|me|her)|pound(ing)?|nudes?|tits|boobs|wet for|horny|naked|69\b)\b")),
    (70, _rx(r"\b(orgasm\w*|sex\b|sexting|in bed|bend you over|choke|choking|spank(ing)? you|handcuff(ed)? (you|to)|ride (you|me)|moan\w*|kinky|bdsm|dominate|lingerie)\b")),
    (45, _rx(r"\b(spank\w*|handcuffs?|naughty|massage oil|booty massage|coconut oil|trouble maker|bad girl|good girl|wild side|daddy|tease|turn(s|ed)? (me|you) on|sexy)\b")),
    (25, _rx(r"\b(cuddle\w*|kiss\w*|cute|hot|gorgeous|beautiful|body|booty|ass)\b")),
]


def sexual_level(text: str) -> int:
    for lvl, rx in _SEX_LEVELS:
        if rx.search(text or ""):
            return lvl
    return 0


# --- misc -----------------------------------------------------------------

LAUGH = _rx(r"\b(lol|lmao|lmfao|haha\w*|hehe\w*|jaja\w*|rofl)\b|😂|🤣")
NEGATIVE = _rx(r"\b(no\b|nope|nah|not really|meh|whatever|ugh|annoying|cringe|weird|wtf|idk|k\.?$|lame|boring|pass)\b|🙄|😒")
APOLOGY_HER = _rx(r"\b(sorry|my bad|apologi[sz]e)\b")
LOW_EFFORT_WORDS = 2


def heuristics(text: str) -> dict[str, Any]:
    t = text or ""
    return {
        "n_questions": t.count("?") or (1 if QUESTION.search(t) else 0),
        "is_question": bool(QUESTION.search(t)),
        "asks_about_me": bool(ASKS_ABOUT_ME.search(t)),
        "asks_when": bool(ASKS_WHEN.search(t)),
        "asks_where": bool(ASKS_WHERE.search(t)),
        "busy": bool(BUSY.search(t)),
        "decline": bool(DECLINE.search(t)),
        "decline_reason": bool(DECLINE_REASON.search(t)),
        "reschedule_offer": bool(RESCHEDULE_OFFER.search(t)),
        "accept": bool(ACCEPT.search(t)) or bool(ACCEPT_ANYWHERE.search(t)),
        "low_effort": len(t.split()) <= LOW_EFFORT_WORDS and "?" not in t,
        "sexual_level": sexual_level(t),
        "laughing": bool(LAUGH.search(t)),
        "negative": bool(NEGATIVE.search(t)),
        "apology": bool(APOLOGY_HER.search(t)),
        "en_route": bool(EN_ROUTE.search(t)),
        "met": bool(MET.search(t)),
        "words": len(t.split()),
    }


def risk_flags(text: str) -> dict[str, bool]:
    t = text or ""
    return {
        "minor": bool(MINOR.search(t)),
        "stop": bool(STOP.search(t)),
        "scam": bool(SCAM.search(t)),
        "serious": bool(SERIOUS.search(t)),
        "call_request": bool(CALL_REQUEST.search(t)),
        "photo_request": bool(PHOTO_REQUEST.search(t)),
        "hostile": bool(HOSTILE.search(t)),
        "address_request": bool(ASKS_WHERE.search(t)),
    }
