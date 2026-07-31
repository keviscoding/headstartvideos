"""
Daily keyword packs for Niche Finder cron.

Only common everyday English words (the kind you type into YouTube that surface
many niches). No niche-name phrases like "true crime story". Each UTC day gets
a different deterministic subset so cron feels spontaneous but stays idempotent.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

# Large pool of common English search probes — rotate a subset each day.
# Keep these short, everyday words (and a few ultra-common two-word phrases).
SIMPLE_PROBES = [
    # User-style probes
    "worse", "is", "why", "how", "never", "always", "secret", "forbidden",
    "untold", "actually", "finally", "exposed", "truth", "mistake", "warning",
    "illegal", "hidden", "strange", "weird", "crazy", "insane", "shocking",
    "banned", "deleted", "leaked", "explained", "versus", "before", "after",
    "really", "still", "almost", "barely", "suddenly", "quietly", "loudly",
    "nobody", "everybody", "someone", "something", "nothing", "everything",
    "because", "unless", "until", "without", "inside", "outside", "between",
    "against", "toward", "across", "behind", "beyond", "beneath", "within",
    "forgot", "remember", "believe", "decide", "change", "become", "happen",
    "start", "stop", "keep", "leave", "return", "follow", "choose", "refuse",
    "accept", "reject", "ignore", "notice", "realize", "pretend", "admit",
    "deny", "prove", "doubt", "trust", "betray", "forgive", "revenge",
    "jealous", "lonely", "afraid", "brave", "angry", "calm", "guilty",
    "innocent", "honest", "liar", "rich", "poor", "famous", "unknown",
    "powerful", "weak", "broken", "fixed", "lost", "found", "missing",
    "stolen", "bought", "sold", "free", "expensive", "cheap", "rare",
    "common", "ancient", "modern", "future", "past", "today", "tonight",
    "yesterday", "tomorrow", "forever", "nearly", "quietly", "slowly",
    "quickly", "barely", "hardly", "mostly", "partly", "totally", "deeply",
    "what", "when", "where", "which", "who", "whom", "whose", "whether",
    "while", "during", "since", "though", "although", "however", "instead",
    "rather", "maybe", "perhaps", "probably", "certainly", "definitely",
    "obviously", "clearly", "simply", "basically", "literally", "exactly",
    "almost", "already", "again", "anymore", "anywhere", "everywhere",
    "somewhere", "nowhere", "somehow", "anyhow", "anyway", "anymore",
    "cannot", "could", "should", "would", "might", "must", "shall",
    "won't", "don't", "didn't", "doesn't", "isn't", "aren't", "wasn't",
    "weren't", "hasn't", "haven't", "hadn't", "couldn't", "shouldn't",
    "wouldn't", "mustn't", "needn't", "ought", "used", "going", "getting",
    "making", "taking", "giving", "leaving", "coming", "looking", "feeling",
    "thinking", "knowing", "saying", "telling", "asking", "trying", "needing",
    "wanting", "hoping", "wishing", "fearing", "hating", "loving", "missing",
    "waiting", "watching", "hearing", "seeing", "finding", "losing", "winning",
    "failing", "falling", "rising", "breaking", "fixing", "building", "ending",
    "beginning", "opening", "closing", "calling", "sending", "bringing",
    "holding", "letting", "putting", "pulling", "pushing", "turning", "moving",
    "staying", "running", "walking", "sitting", "standing", "sleeping", "waking",
    "eating", "drinking", "working", "playing", "learning", "teaching", "helping",
    "hurting", "healing", "saving", "spending", "paying", "owing", "owning",
    "sharing", "keeping", "hiding", "showing", "proving", "testing", "checking",
    "counting", "measuring", "guessing", "wondering", "worrying", "caring",
    "matter", "means", "seems", "appears", "remains", "becomes", "happens",
    "works", "fails", "starts", "stops", "ends", "begins", "continues",
    "changes", "stays", "goes", "comes", "leaves", "returns", "arrives",
    "disappears", "appears", "exists", "survives", "dies", "lives", "grows",
    "shrinks", "expands", "collapses", "explodes", "vanishes", "remains",
    "worst", "best", "better", "harder", "easier", "faster", "slower",
    "bigger", "smaller", "older", "younger", "newer", "stronger", "weaker",
    "smarter", "dumber", "safer", "riskier", "darker", "brighter", "louder",
    "quieter", "closer", "farther", "deeper", "higher", "lower", "longer",
    "shorter", "sooner", "later", "earlier", "first", "last", "next", "final",
    "only", "every", "each", "any", "some", "none", "both", "either", "neither",
    "other", "another", "same", "different", "similar", "opposite", "wrong",
    "right", "true", "false", "real", "fake", "normal", "strange", "usual",
    "unusual", "possible", "impossible", "likely", "unlikely", "certain",
    "uncertain", "ready", "unready", "able", "unable", "willing", "unwilling",
    "allowed", "forbidden", "legal", "illegal", "public", "private", "personal",
    "official", "unofficial", "known", "unknown", "famous", "infamous",
    "popular", "unpopular", "important", "useless", "useful", "useless",
    "dangerous", "safe", "risky", "harmless", "helpful", "harmful", "kind",
    "cruel", "gentle", "harsh", "soft", "hard", "easy", "difficult", "simple",
    "complex", "clear", "unclear", "obvious", "hidden", "visible", "invisible",
    "open", "closed", "locked", "unlocked", "full", "empty", "half", "complete",
    "incomplete", "finished", "unfinished", "done", "undone", "over", "under",
    "above", "below", "beside", "nearby", "distant", "local", "foreign",
    "domestic", "global", "national", "personal", "shared", "alone", "together",
    "alone", "lonely", "crowded", "quiet", "noisy", "busy", "idle", "active",
    "passive", "early", "late", "on time", "too late", "too early", "just now",
    "right now", "not yet", "so far", "by now", "at once", "at last", "at least",
    "at most", "for once", "for good", "for now", "from now", "until then",
    "what if", "how come", "why not", "who else", "where else", "how else",
    "no one", "any one", "every one", "some one", "each other", "one another",
]


def _stable_shuffle(items: list[str], *, salt: str) -> list[str]:
    def key(word: str) -> str:
        return hashlib.sha256(f"{salt}:{word}".encode()).hexdigest()

    return sorted(items, key=key)


def daily_cron_keywords(
    *,
    when: datetime | None = None,
    count: int = 50,
) -> list[str]:
    """
    Different everyday-English pack each UTC day.

    Packs slide through a fixed shuffle so consecutive days don't share
    keywords (until the pool wraps). Cron scrolls each probe to the end of
    YouTube results and keeps every channel discovered (no hard channel cap).
    """
    dt = when or datetime.now(timezone.utc)
    day = dt.strftime("%Y-%m-%d")
    # Deduplicate while preserving order of first occurrence
    seen: set[str] = set()
    pool: list[str] = []
    for w in SIMPLE_PROBES:
        key = w.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        pool.append(w.strip())
    want = max(8, min(int(count or 50), len(pool)))
    fixed = _stable_shuffle(pool, salt="simple:v2")
    # Day ordinal → non-overlapping window (wraps after pool/want days)
    day_ord = dt.toordinal()
    offset = (day_ord * want) % len(fixed)
    return [fixed[(offset + i) % len(fixed)] for i in range(want)]
