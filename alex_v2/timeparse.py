"""Time handling: timezones, chat time-label parsing, relative-date resolution.

Two jobs:

1. ``parse_time_label`` turns scraper labels ("2:34 PM", "Yesterday 6:12 PM",
   "Wed", "Thu, Sep 25 at 10:05 PM", "23/09/2026, 21:14") into an absolute
   datetime, resolved against the moment the label was SEEN (not "now"), so a
   "Yesterday" label never drifts when the thread is re-read days later.

2. ``find_day_time_refs`` finds "tonight / tomorrow / Wednesday / this weekend /
   9pm / 930" inside a message and resolves them against THAT message's send
   time, in the sender's timezone. This is what stops last week's "Wednesday"
   from being read as next Wednesday.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

UTC = timezone.utc


def tz(name: str | None):
    if not name:
        return UTC
    if ZoneInfo is None:
        return UTC
    try:
        return ZoneInfo(name)
    except Exception:
        return UTC


def to_utc(dt: datetime, tzname: str | None = None) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz(tzname))
    return dt.astimezone(UTC)


def local(dt: datetime, tzname: str | None) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(tz(tzname))


def human(dt: Optional[datetime], tzname: str | None) -> str:
    if not dt:
        return "unknown time"
    d = local(dt, tzname)
    return d.strftime("%a %d %b %H:%M")


def gap_text(hours: float) -> str:
    if hours < 1:
        return f"{max(1, int(round(hours * 60)))} min"
    if hours < 36:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f} days"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

WEEKDAYS = {
    "monday": 0, "mon": 0, "lunes": 0,
    "tuesday": 1, "tue": 1, "tues": 1, "martes": 1,
    "wednesday": 2, "wed": 2, "weds": 2, "miercoles": 2, "miércoles": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "jueves": 3,
    "friday": 4, "fri": 4, "viernes": 4,
    "saturday": 5, "sat": 5, "sabado": 5, "sábado": 5,
    "sunday": 6, "sun": 6, "domingo": 6,
}
# Short forms that are also ordinary words are only accepted when followed by
# night/evening/morning/afternoon or a time ("sat night", "sun at 8").
_AMBIGUOUS_SHORT = {"sat", "sun", "wed", "mon", "fri"}

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_TONIGHT = r"(?:tonight|tonite|tn|this evening|later tonight|esta noche)"
_TODAY = r"(?:today|hoy)"
_TOMORROW = r"(?:tomorrow|tmrw|tmr|tmrow|tomorow|tomm?orrow|tmw|mañana|manana)"
_DAY_AFTER = r"(?:day after tomorrow|pasado mañana)"
_WEEKEND = r"(?:this weekend|the weekend|weekend)"
_NEXT_WEEK = r"(?:next week)"

_PART = r"(?:\s+(?:night|evening|eve|afternoon|morning|noche|tarde))"


@dataclass
class DayTimeRef:
    kind: str                 # "tonight" | "today" | "tomorrow" | "weekday" | "date" | "weekend" | "next_week" | "time_only"
    span: str                 # matched text
    day: Optional[date]       # local calendar day (in the sender's tz)
    hour: Optional[int] = None
    minute: int = 0
    part: str = ""            # night/evening/...

    def as_datetime(self, tzname: str, default_hour: int = 21) -> Optional[datetime]:
        if not self.day:
            return None
        h = self.hour if self.hour is not None else default_hour
        dt = datetime.combine(self.day, time(h, self.minute), tzinfo=tz(tzname))
        return dt.astimezone(UTC)


# ---------------------------------------------------------------------------
# Clock times inside messages ("9", "9pm", "9:30", "930", "10ish", "21:00")
# ---------------------------------------------------------------------------

_CLOCK_RE = re.compile(
    r"(?<![\d:/.])(?P<h>[01]?\d|2[0-3])(?:(?::|\.)(?P<m>[0-5]\d)|(?P<m2>[0-5]\d))?"
    r"\s*(?P<ap>a\.?m\.?|p\.?m\.?|am|pm|ish)?(?![\d/])",
    re.I,
)
_TIME_CONTEXT = re.compile(
    r"\b(?:at|say|around|about|by|after|before|like|from|til|till|until|how'?s|hows|how about|does|do|lets do|let's do|"
    r"ok|okay|maybe|or)\s*$",
    re.I,
)


def parse_clock(text: str, context_hint: bool = False) -> Optional[tuple[int, int]]:
    """Return (hour, minute) for the first plausible clock time in text.

    Bare numbers ("9") are only accepted when preceded by a time-ish word or
    when the whole text is short (a reply like "9?" or "Let's do 9"), to avoid
    reading "2 dogs" as 2am.
    """
    for m in _CLOCK_RE.finditer(text):
        h = int(m.group("h"))
        mm = m.group("m") or m.group("m2")
        ap = (m.group("ap") or "").lower().replace(".", "")
        before = text[: m.start()]
        after = text[m.end(): m.end() + 12].lower()
        has_colon_or_ap = bool(m.group("m")) or ap in ("am", "pm")
        bare_ok = (
            context_hint
            or bool(_TIME_CONTEXT.search(before))
            or len(text.strip()) <= 12
            or ap == "ish"
            or re.match(r"\s*(?:o'?clock|tonight|pm|am|ish)", after)
        )
        if m.group("m2") and not ap and len(m.group(0)) >= 3:
            # "930" / "1030"
            bare_ok = bare_ok or bool(_TIME_CONTEXT.search(before))
        if not (has_colon_or_ap or bare_ok):
            continue
        # reject things that are clearly not times
        if re.match(r"\s*(?:%|min|mins|minutes|hours|hrs|h\b|years|yrs|yo|kids|dogs|cats|x\b|k\b|lbs|kg|cm|ft|'|\")", after):
            continue
        minute = int(mm) if mm else 0
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        elif ap in ("", "ish") and 1 <= h <= 11:
            # dating context: a bare 1..11 means pm unless "morning"/"am" nearby
            if not re.search(r"\b(morning|am|brunch|breakfast|coffee)\b", text, re.I):
                h += 12
        return h % 24, minute
    return None


# ---------------------------------------------------------------------------
# Relative day references
# ---------------------------------------------------------------------------

def _next_weekday(ref: date, wd: int, ref_hour: int) -> date:
    delta = (wd - ref.weekday()) % 7
    if delta == 0 and ref_hour >= 20:
        # "Wednesday" said late on a Wednesday almost always means next week
        delta = 7
    return ref + timedelta(days=delta)


def find_day_time_refs(text: str, sent_at_utc: Optional[datetime], sender_tz: str) -> list[DayTimeRef]:
    """Find day/time references in ``text`` resolved against its send time."""
    if not text:
        return []
    low = text.lower()
    ref_local = local(sent_at_utc, sender_tz) if sent_at_utc else None
    ref_day = ref_local.date() if ref_local else None
    ref_hour = ref_local.hour if ref_local else 12
    # a message sent before 5am belongs to the previous "evening"
    if ref_local and ref_local.hour < 5:
        eff_day = ref_day - timedelta(days=1)
    else:
        eff_day = ref_day

    out: list[DayTimeRef] = []
    clock = parse_clock(text)

    def add(kind, span, day, part=""):
        r = DayTimeRef(kind=kind, span=span, day=day, part=part)
        if clock:
            r.hour, r.minute = clock
        elif part in ("morning",):
            r.hour = 10
        elif part in ("afternoon", "tarde"):
            r.hour = 15
        out.append(r)

    for m in re.finditer(rf"\b{_DAY_AFTER}\b", low):
        add("date", m.group(0), (eff_day + timedelta(days=2)) if eff_day else None)
    for m in re.finditer(rf"\b{_TONIGHT}\b", low):
        # "tonight" sent at 1am refers to the coming evening of the same calendar day
        d = ref_day if ref_local and ref_local.hour < 5 else eff_day
        add("tonight", m.group(0), d, "night")
    for m in re.finditer(rf"\b{_TODAY}\b", low):
        add("today", m.group(0), ref_day)
    for m in re.finditer(rf"\b{_TOMORROW}{_PART}?\b", low):
        if re.search(_DAY_AFTER, low):
            continue
        base = eff_day if eff_day else None
        add("tomorrow", m.group(0), (base + timedelta(days=1)) if base else None,
            (m.group(0).split()[-1] if " " in m.group(0) else ""))
    for m in re.finditer(r"\b(next\s+|this\s+)?(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b(" + _PART + r")?", low):
        word = m.group(2)
        part = (m.group(3) or "").strip()
        if word in _AMBIGUOUS_SHORT and not part:
            tail = low[m.end(): m.end() + 10]
            if not re.match(r"\s*(?:at|@|\d|or|and|\?|,|works|is|would|night|evening)", tail) and not re.search(r"\b(or|and)\s*$", low[: m.start()]):
                continue
        wd = WEEKDAYS[word]
        day = None
        if eff_day is not None:
            day = _next_weekday(eff_day, wd, ref_hour)
            if m.group(1) and m.group(1).strip() == "next" and (day - eff_day).days < 3:
                day = day + timedelta(days=7)
        add("weekday", m.group(0).strip(), day, part)
    for m in re.finditer(rf"\b{_WEEKEND}\b", low):
        day = None
        if eff_day is not None:
            day = _next_weekday(eff_day, 5, 0) if eff_day.weekday() < 5 else eff_day
        add("weekend", m.group(0), day)
    for m in re.finditer(rf"\b{_NEXT_WEEK}\b", low):
        day = None
        if eff_day is not None:
            day = eff_day + timedelta(days=(7 - eff_day.weekday()))
        add("next_week", m.group(0), day)
    # month-name dates: "Sep 30", "30 Sep", "September 30th"
    mon_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    for m in re.finditer(rf"\b(?:({mon_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?|(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({mon_names}))\b", low):
        mon = MONTHS[m.group(1) or m.group(4)]
        dd = int(m.group(2) or m.group(3))
        if ref_day and 1 <= dd <= 31:
            try:
                d = date(ref_day.year, mon, dd)
                if d < ref_day - timedelta(days=60):
                    d = date(ref_day.year + 1, mon, dd)
                add("date", m.group(0), d)
            except ValueError:
                pass
    # "the 4th"
    for m in re.finditer(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", low):
        dd = int(m.group(1))
        if ref_day and 1 <= dd <= 31:
            try:
                d = date(ref_day.year, ref_day.month, dd)
                if d < ref_day:
                    y, mo = (ref_day.year + (ref_day.month // 12), ref_day.month % 12 + 1)
                    d = date(y, mo, dd)
                add("date", m.group(0), d)
            except ValueError:
                pass
    if not out and clock:
        out.append(DayTimeRef(kind="time_only", span="", day=None, hour=clock[0], minute=clock[1]))
    return out


def has_relative_day_words(text: str) -> bool:
    return bool(re.search(rf"\b(?:{_TONIGHT}|{_TODAY}|{_TOMORROW})\b", text.lower()))


def weekday_name(d: date) -> str:
    return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][d.weekday()]


# ---------------------------------------------------------------------------
# Scraper time labels
# ---------------------------------------------------------------------------

_LABEL_CLOCK = re.compile(r"(\d{1,2})[:.](\d{2})\s*([ap]\.?m\.?)?", re.I)


def _label_clock(label: str) -> Optional[tuple[int, int]]:
    m = _LABEL_CLOCK.search(label)
    if not m:
        m2 = re.search(r"\b(\d{1,2})\s*([ap]m)\b", label, re.I)
        if not m2:
            return None
        h = int(m2.group(1)) % 12 + (12 if m2.group(2).lower() == "pm" else 0)
        return h, 0
    h, mi = int(m.group(1)), int(m.group(2))
    ap = (m.group(3) or "").lower().replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return h, mi


def parse_time_label(label: str, seen_at_utc: datetime, user_tz: str) -> tuple[Optional[datetime], bool]:
    """Resolve a scraper label against the moment it was seen.

    Returns (datetime_utc, has_clock). Labels without a day are assumed to be
    the most recent matching moment at or before ``seen_at``. Labels without a
    clock (day separators) return midnight-ish of that day with has_clock=False.
    """
    if not label or not label.strip():
        return None, False
    lab = label.strip()
    low = lab.lower()
    seen_local = local(seen_at_utc, user_tz)
    zone = tz(user_tz)
    clock = _label_clock(lab)

    day: Optional[date] = None
    # ISO timestamps
    try:
        iso = datetime.fromisoformat(lab.replace("Z", "+00:00"))
        return to_utc(iso, user_tz), True
    except Exception:
        pass
    # numeric dates: 23/09/2026, 9/23/26, 2026-09-23
    m = re.search(r"\b(\d{1,4})[/.-](\d{1,2})[/.-](\d{2,4})\b", lab)
    if m:
        a, b, c = m.groups()
        try:
            if len(a) == 4:
                day = date(int(a), int(b), int(c))
            else:
                y = int(c) + (2000 if len(c) == 2 else 0)
                first, second = int(a), int(b)
                # UK/EU default (dd/mm) unless impossible
                if first > 12:
                    day = date(y, second, first)
                elif second > 12:
                    day = date(y, first, second)
                else:
                    day = date(y, second, first)
        except ValueError:
            day = None
    if day is None:
        mon_names = "|".join(sorted(MONTHS, key=len, reverse=True))
        m = re.search(rf"\b({mon_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(\d{{4}}))?", low) or \
            re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({mon_names})\.?(?:,?\s*(\d{{4}}))?", low)
        if m:
            g = m.groups()
            if g[0].isdigit():
                dd, mon, yy = int(g[0]), MONTHS[g[1]], g[2]
            else:
                mon, dd, yy = MONTHS[g[0]], int(g[1]), g[2]
            y = int(yy) if yy else seen_local.year
            try:
                day = date(y, mon, dd)
                if not yy and day > seen_local.date():
                    day = date(y - 1, mon, dd)
            except ValueError:
                day = None
    if day is None:
        if re.search(r"\b(today|now|just now)\b", low):
            day = seen_local.date()
        elif re.search(r"\byesterday\b", low):
            day = seen_local.date() - timedelta(days=1)
        else:
            for word, wd in sorted(WEEKDAYS.items(), key=lambda kv: -len(kv[0])):
                if re.search(rf"\b{word}\b", low):
                    delta = (seen_local.weekday() - wd) % 7
                    if delta == 0:
                        delta = 7 if not clock else 0
                    day = seen_local.date() - timedelta(days=delta)
                    break
    if day is None and clock:
        day = seen_local.date()
        cand = datetime.combine(day, time(*clock), tzinfo=zone)
        if cand > seen_local + timedelta(minutes=5):
            day = day - timedelta(days=1)
    if day is None:
        return None, False
    if clock:
        dt = datetime.combine(day, time(*clock), tzinfo=zone)
        if dt > seen_local + timedelta(minutes=5) and "today" not in low:
            # a label can't be in the future relative to when we saw it
            dt = dt - timedelta(days=7) if re.search(r"[a-z]{3}", low) and not re.search(r"yesterday|today", low) else dt - timedelta(days=1)
        return dt.astimezone(UTC), True
    return datetime.combine(day, time(0, 0), tzinfo=zone).astimezone(UTC), False
