"""Plain data types shared across alex_v2.

Everything the engine reasons about is explicit here: messages with
absolute times, the per-thread state (with evidence), and the final
Decision the autopilot acts on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@dataclass
class Msg:
    idx: int                      # position in the thread (0 = oldest)
    speaker: str                  # "me" | "her"
    text: str
    fp: str                       # stable fingerprint (survives rescans)
    sent_at: Optional[datetime] = None   # aware UTC; None when unknown
    time_known: bool = False      # True when sent_at came from a real label/timestamp
    label: str = ""               # raw time label from the scraper (audit only)
    media: str = ""               # "", "image", "video", "audio", "gif", "link"

    @property
    def is_me(self) -> bool:
        return self.speaker == "me"

    @property
    def is_her(self) -> bool:
        return self.speaker == "her"

    @property
    def words(self) -> int:
        return len(self.text.split())


# ---------------------------------------------------------------------------
# Date state machine
# ---------------------------------------------------------------------------

DATE_STATES = (
    "NONE",            # nothing agreed, not even the idea of meeting
    "INTEREST",        # she accepted the idea ("we should get together sometime soon" -> yes)
    "SCHEDULING",      # schedules being exchanged
    "PROPOSED",        # a specific day/time was proposed by someone, not yet accepted
    "AGREED",          # both sides accepted the same day(/time)
    "REMINDED",        # reminder sent + acknowledged (date >48h out)
    "CONFIRMED_DAYOF", # "still good for tonight?" -> yes
    "EN_ROUTE",        # leaving now / eta / here
    "MET",             # evidence the date happened
    "RESCHEDULING",    # she cancelled with reason and wants another time
    "FLAKED",          # she cancelled without reason / no-show
)

DATE_RANK = {s: i for i, s in enumerate(DATE_STATES)}
# States in which a concrete plan exists for a specific day.
PLAN_STATES = {"AGREED", "REMINDED", "CONFIRMED_DAYOF", "EN_ROUTE"}


@dataclass
class Evidence:
    state: str
    msg_idx: list[int]
    quote: str


@dataclass
class DateInfo:
    state: str = "NONE"
    when: Optional[datetime] = None        # aware UTC of the planned meet (hour defaulted if unknown)
    when_text: str = ""                    # e.g. "Thursday 9pm"
    hour_known: bool = False
    place_hint: str = ""                   # "mine" | "hers" | "bar" | "" (best effort)
    evidence: list[Evidence] = field(default_factory=list)
    expired_plan: bool = False             # a plan existed but its time passed / decayed
    proposed_by: str = ""                  # "me" | "her" when state == PROPOSED
    proposed_when: Optional[datetime] = None
    proposed_text: str = ""
    had_plan: bool = False                 # a plan was agreed at some point in this thread
    outcome_unknown: bool = False          # a confirmed date passed with no messages: did it happen?
    outcome_when: Optional[datetime] = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Thread state
# ---------------------------------------------------------------------------

@dataclass
class Incidents:
    ignored_plan_asks: int = 0      # my plan asks that got silence >= 48h
    ignored_reengages: int = 0      # my consecutive unanswered messages (current streak)
    flakes_with_reason: int = 0
    flakes_no_reason: int = 0
    reschedules_by_her: int = 0
    reschedules_by_me: int = 0
    no_shows: int = 0
    busy_excuses: int = 0


@dataclass
class State:
    thread_id: str
    now: datetime
    her_tz: str
    my_tz: str
    platform: str
    origin: str
    n_msgs: int
    n_her: int
    n_me: int
    times_known: bool
    last_speaker: str                      # "me" | "her" | ""
    her_last: Optional[Msg]
    my_last: Optional[Msg]
    since_her_last_h: Optional[float]      # hours since her last message
    since_my_last_h: Optional[float]
    my_unanswered: int                     # my consecutive messages at the tail
    her_unanswered: int                    # her consecutive messages at the tail (she double texted)
    date: DateInfo
    incidents: Incidents
    investment: str                        # low | medium | high
    investment_trend: str                  # rising | flat | falling
    her_avg_words: float
    her_questions_recent: int
    her_median_latency_h: Optional[float]
    stage: str
    exchanges: int                         # her<->me turn switches
    positive_run: int                      # consecutive "warm" exchanges since last close attempt
    my_plan_asks: int                      # how many times I've tried to set something up
    soft_close_attempted: bool
    last_close_idx: int
    our_date_uses_recent: int              # "our date"-type presuppositions in my last 10 msgs
    my_question_streak: int                # my consecutive messages that were plain questions
    her_msg_heuristics: dict[str, Any]     # cheap classifier on her last message
    risk: dict[str, Any]                   # guardrail signals
    her_local_now: str                     # human readable
    is_first_contact: bool                 # no messages from me yet
    she_opened: bool
    troll_active: bool = False
    closed: bool = False
    my_tail: list = field(default_factory=list)   # texts of my unanswered messages at the tail (oldest first)

    def to_dict(self) -> dict:
        d = asdict(self)
        # datetimes -> iso for logging
        def conv(o):
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: conv(v) for k, v in o.items()}
            if isinstance(o, list):
                return [conv(v) for v in o]
            return o
        return conv(d)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    action: str                        # "send" | "wait" | "handoff" | "none"
    text: Optional[str] = None
    messages: list[str] = field(default_factory=list)
    send_after_s: int = 0              # advisory delay before sending
    due_at: Optional[datetime] = None  # when the pending message becomes sendable
    follow_up_at: Optional[datetime] = None
    follow_up_reason: str = ""
    move_id: str = ""
    reason: str = ""
    handoff_reason: str = ""
    suggested_text: str = ""           # for handoffs
    arm: str = "v2"
    trace_id: str = ""
    state: dict = field(default_factory=dict)
    debug: dict = field(default_factory=dict)

    @property
    def should_send(self) -> bool:
        return self.action == "send" and bool(self.text)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("due_at", "follow_up_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        return d
