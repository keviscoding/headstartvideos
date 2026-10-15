"""The Style Profile: what makes a channel recognisable, from measurement and watching.

One schema for every niche. A footage documentary, a stick-figure explainer, a
manga recap and an AI-avatar channel are all described on the same axes:
pictures (what they are, how they look, who recurs), motion, edit pace, on-screen
text and graphics, sound (music, effects, ambience), voice and structure (hook,
calls to action). Settings are derived from the profile later, in code, so a new
niche never needs a new special case here.

Every axis records where it came from ("measured" on this computer, "watched" by
Gemini, or both) and a confidence: high when the measurement and the watching
agree and the references agree with each other, low when they don't, or when
only one source saw it. Low-confidence axes become questions for the creator.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

SCHEMA = 1

# Gemini's motion vocabulary in the three classes the measurement can see
MOTION_CLASS = {"still": "still", "zoom_in": "camera", "zoom_out": "camera", "pan": "camera", "shake": "camera",
                "animated": "moving", "live_action": "moving", "motion_graphic": "moving", "cutout_move": "moving"}
# picture sources by how a studio makes them
SOURCE_FAMILY = {
    "ai_image": "generated", "painting": "generated", "drawing": "generated", "stick_figure": "generated",
    "cutout": "generated", "ai_video": "generated_motion",
    "live_footage": "footage", "archival_footage": "footage", "stock_footage": "footage", "news_clip": "footage",
    "bodycam_cctv": "footage", "interview": "footage",
    "archival_photo": "photo", "screenshot_web": "document", "document": "document",
    "map": "graphic", "chart": "graphic", "motion_graphic": "graphic", "text_card": "graphic", "meme_gif": "meme",
    "3d_render": "3d", "manga_panel": "panel", "presenter": "presenter", "avatar": "presenter", "other": "other"}
ORDINAL = {"none": 0, "sparse": 1, "moderate": 2, "dense": 3}


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _shares(weights: dict) -> dict:
    total = sum(weights.values())
    if total <= 0:
        return {}
    return {k: round(v / total, 3) for k, v in sorted(weights.items(), key=lambda kv: -kv[1]) if v / total >= 0.01}


def _majority(values: list, default=""):
    values = [v for v in values if isinstance(v, str) and v.strip()]
    return Counter(v.strip().lower() for v in values).most_common(1)[0][0] if values else default


def _norm_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ,'-]", " ", str(text or "").lower())).strip()


def _dedupe(items: list, limit: int = 12) -> list:
    seen, out = set(), []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        key = " ".join(sorted(set(_norm_phrase(item).split()) - {"the", "a", "an", "and", "of", "on", "with"}))
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
        if len(out) >= limit:
            break
    return out


_STOP = {"the", "a", "an", "and", "of", "on", "with", "in", "to", "for", "by", "its", "is", "are", "or", "against",
         "set", "paired", "featuring", "style", "aesthetic", "look"}


def _words(text: str) -> set:
    return {w for w in _norm_phrase(text).replace(",", " ").split() if len(w) > 2 and w not in _STOP}


def _similar(statements: list) -> float:
    """Mean word overlap between every pair of descriptions (1 = identical words, 0 = nothing shared)."""
    sets = [_words(x) for x in statements if _words(x)]
    if len(sets) < 2:
        return 1.0 if sets else 0.0
    pairs = [(a, b) for i, a in enumerate(sets) for b in sets[i + 1:]]
    return sum(len(a & b) / len(a | b) for a, b in pairs) / len(pairs)


def _agreement(a: dict, b: dict) -> float | None:
    """1 when two share distributions match, 0 when they don't overlap."""
    if not a or not b:
        return None
    keys = set(a) | set(b)
    return round(1 - 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys), 3)


def _axis(value, confidence, sources, **extra):
    return {"value": value, "confidence": round(max(0.0, min(1.0, confidence)), 2), "from": sources, **extra}


# ── one reference ───────────────────────────────────────────────────────────────────────────────────────────

TALKING = re.compile(r"\b(talk|speak|address|explain|lectur|tell|gestur|emphatic)\w*", re.I)
TO_CAMERA = re.compile(r"\b(?:to|into|at|addressing|address(?:es)?|facing)\s+(?:the\s+)?(?:camera|lens|viewer|audience)s?\b",
                       re.I)
# pictures that are drawn: a drawn character talking is a mascot or a story, never presenter time on video
DRAWN_SOURCES = {"drawing", "painting", "stick_figure", "cutout", "manga_panel", "3d_render", "motion_graphic",
                 "text_card", "meme_gif", "chart", "map", "document", "screenshot_web"}


def talking_share(log: dict, seconds: float):
    """Share of a watched window in which a presenter talks on video: a moving shot of a named person talking to
    camera. Pictures of the presenter doing things are not talking time. None when the window has no shot list."""
    shots = [s for s in (log or {}).get("shots") or [] if isinstance(s, dict)]
    if not shots:
        return None
    def talks(s):
        source, what = str(s.get("source") or "").lower(), str(s.get("what") or "")
        if str(s.get("motion") or "").lower() in ("still", "zoom_in", "zoom_out", "pan", "ken_burns"):
            return False
        if source in ("presenter", "avatar"):                 # the model names the host's own shots
            return bool(TALKING.search(what) or TO_CAMERA.search(what))
        if source in DRAWN_SOURCES:
            return False
        # filmed or generated video of a named person: only talking TO the viewer is a presenter (people talking
        # to each other in footage are the story)
        return bool(s.get("who")) and bool(TO_CAMERA.search(what))
    talk = sum(max(0.2, _num(s.get("len_s"), 1.0)) for s in shots if talks(s))
    logged = sum(max(0.2, _num(s.get("len_s"), 1.0)) for s in shots)
    # a share of the time the model logged (a log that stops early still reads its share right)
    return round(min(1.0, talk / max(1.0, min(seconds, logged) if logged else seconds)), 3)


def watched_summary(watch: dict) -> dict:
    """Aggregate Gemini's shot logs of one reference into shares and lists (counted here, not by the model).
    A window watched only for a call to action adds its promotion, never its pictures (a sponsor read isn't the
    channel's style)."""
    windows = [w for w in (watch or {}).get("windows", []) if isinstance(w.get("log"), dict)]
    style_windows = [w for w in windows if w.get("purpose") != "cta"] or windows
    logs = [w["log"] for w in style_windows]
    if not logs:
        return {}
    shots = [s for lg in logs for s in lg.get("shots") or [] if isinstance(s, dict)]
    src, fam, mot, cls, txt, aud, art, who, pol = (defaultdict(float) for _ in range(9))
    for s in shots:
        ln = max(0.2, _num(s.get("len_s"), 1.0))
        source = str(s.get("source") or "other").lower()
        src[source] += ln
        fam[SOURCE_FAMILY.get(source, "other")] += ln
        motion = str(s.get("motion") or "").lower()
        if motion:
            mot[motion] += ln
            cls[MOTION_CLASS.get(motion, "moving")] += ln
        txt[str(s.get("text") or "none").lower()] += ln
        aud[str(s.get("audio") or "narration").lower()] += ln
        style = s.get("style") or s.get("art")                  # prompt v1 called it "art" and mixed in content
        if style:
            art[_norm_phrase(style)] += ln
        if s.get("who"):
            who[_norm_phrase(s["who"])] += ln
        if s.get("polish"):
            pol[str(s["polish"]).lower()] += ln
    total = sum(max(0.2, _num(s.get("len_s"), 1.0)) for s in shots) or 1.0
    recurring = []
    for lg in logs:
        for r in lg.get("recurring") or []:
            if isinstance(r, dict) and r.get("name"):
                share = who.get(_norm_phrase(r["name"]), 0.0) / total
                recurring.append({"name": str(r["name"])[:60], "look": str(r.get("look") or "")[:300],
                                  "role": str(r.get("role") or "character")[:20], "screen_share": round(share, 3)})
    music = [lg.get("music") or {} for lg in logs]
    sfx = [lg.get("sfx") or {} for lg in logs]
    voice = [lg.get("voice") or {} for lg in logs]
    look = [lg.get("look") or {} for lg in logs]
    amb = [_ambience(lg.get("ambience")) for lg in logs]
    presenter = [lg.get("presenter") for lg in logs if isinstance(lg.get("presenter"), dict)]
    # presenter time by window: the opening, and the rest of the video as every later window samples it (the
    # call-to-action window included: a presenter often holds the product there, and that is screen time too).
    # Counted from the shot list (seconds the presenter talks on video), not the model's own share, which also
    # counted pictures of the presenter doing things.
    opening_p, later = None, []
    for w in windows:
        pr, span = w["log"].get("presenter"), w.get("span") or [0, 0]
        share = talking_share(w["log"], max(1.0, _num(span[1]) - _num(span[0])))
        if share is None:
            if not isinstance(pr, dict):
                continue
            share = _num(pr.get("on_camera_share"), None)
        if span[0] == 0:
            opening_p = share
        else:
            later.append((share or 0.0, max(1.0, _num(span[1]) - _num(span[0]))))
    later_p = sum(v * w for v, w in later) / sum(w for _, w in later) if later else None
    out = {
        "shots_logged": len(shots), "seconds_logged": round(total, 1),
        "narration": _majority([lg.get("narration") for lg in logs], "voiceover"),
        "sources": _shares(src), "families": _shares(fam), "motion": _shares(mot), "motion_class": _shares(cls),
        "text": _shares(txt), "audio": _shares(aud), "polish_shots": _shares(pol),
        "polish": _majority([(lg.get("polish") or {}).get("overall") for lg in logs
                             if isinstance(lg.get("polish"), dict)]) or (max(pol, key=pol.get) if pol else ""),
        "polish_notes": _dedupe([(lg.get("polish") or {}).get("notes") for lg in logs
                                 if isinstance(lg.get("polish"), dict)], 2),
        "art": [{"look": k, "share": round(v / total, 3)} for k, v in sorted(art.items(), key=lambda kv: -kv[1])[:6]],
        "art_style": _dedupe([lg.get("art_style") for lg in logs], 3),
        "lettering": _majority([lg.get("lettering") for lg in logs]),
        "recurring": _merge_people(recurring),
        "presenter": {"on_camera": _mean([_num(p.get("on_camera_share")) for p in presenter]),
                      "in_pictures": _mean([_num(p.get("pictures_share")) for p in presenter]),
                      "opening": opening_p, "middle": later_p}
                     if presenter else {},
        "music": {"share": round(sum(_num(m.get("share")) for m in music) / len(music), 3),
                  "role": _majority([m.get("role") for m in music], "none"),
                  "genres": _dedupe([m.get("genre") for m in music], 3),
                  "moods": _dedupe([x for m in music for x in m.get("moods") or []], 8),
                  "instruments": _dedupe([x for m in music for x in m.get("instruments") or []], 8),
                  "under_voice": _majority([m.get("under_voice") for m in music], "none"),
                  "beat": _majority([m.get("beat") for m in music if m.get("beat")]),
                  "changes": _dedupe([m.get("changes") for m in music], 2)},
        "sfx": {"density": _ordinal_mean([s.get("density") for s in sfx]),
                "kinds": _dedupe([x for s in sfx for x in s.get("kinds") or []], 10),
                "on": _dedupe([s.get("on") for s in sfx], 2)},
        "ambience": {"present": any(a["present"] for a in amb),
                     "share": _mean([a["share"] for a in amb if a["present"]]) or 0.0,
                     "kinds": _dedupe([k for a in amb for k in a["kinds"]], 6),
                     "where": _dedupe([a["where"] for a in amb], 2)},
        "voice": {k: _majority([v.get(k) for v in voice]) for k in ("gender", "age", "accent", "tone", "pace")},
        "grade": _majority([lk.get("grade") for lk in look]),
        "grain": _majority([lk.get("grain") for lk in look]),
        "filter": _dedupe([lk.get("filter") for lk in look], 3),
        "text_style": _dedupe([lg.get("text_style") for lg in logs], 3),
        "graphics": _dedupe([x for lg in logs for x in lg.get("graphics") or []], 10),
        # every window counts here, the call-to-action one most of all
        "cta": [c for w in windows for c in (w["log"].get("cta") or []) if isinstance(c, dict)][:8],
        "hook": _dedupe([lg.get("hook") for lg in logs], 1),
        "breathing": _dedupe([lg.get("breathing") for lg in logs], 3),
        "recognisable": _dedupe([x for lg in logs for x in lg.get("recognisable") or []], 8),
        "audio_events": _event_rates(logs, total),
    }
    return out


_NOT_AMBIENCE = re.compile(r"^(quiet |faint |subtle |low )?(room tone|silence|none|hum|mic hum|room hum)$", re.I)


def _ambience(value) -> dict:
    """Prompt v2 answers {"present", "kinds", "share", "where"}; v1 answered with a phrase. A room's own tone is not
    environmental sound design, so it alone doesn't count."""
    if isinstance(value, dict):
        kinds = [k for k in value.get("kinds") or [] if isinstance(k, str) and k.strip()
                 and not _NOT_AMBIENCE.match(k.strip())]
        present = bool(kinds)
        return {"present": present, "kinds": kinds if present else [], "share": _num(value.get("share")) if present else 0.0,
                "where": str(value.get("where") or "") if present else ""}
    text = str(value or "").strip()
    present = bool(text) and text.lower() not in ("no", "n/a") and not _NOT_AMBIENCE.match(text)
    return {"present": present, "kinds": [text] if present else [], "share": 0.0, "where": ""}


def _merge_people(rows: list) -> list:
    by = {}
    for r in rows:
        key = _norm_phrase(r["name"])
        if key in by:
            by[key]["screen_share"] = max(by[key]["screen_share"], r["screen_share"])
            if len(r["look"]) > len(by[key]["look"]):
                by[key]["look"] = r["look"]
        else:
            by[key] = dict(r)
    return sorted(by.values(), key=lambda r: -r["screen_share"])[:6]


def _ordinal_mean(values: list) -> str:
    nums = [ORDINAL[v.strip().lower()] for v in values if isinstance(v, str) and v.strip().lower() in ORDINAL]
    if not nums:
        return ""
    m = round(sum(nums) / len(nums))
    return {v: k for k, v in ORDINAL.items()}[m]


def _event_rates(logs: list, seconds: float) -> dict:
    counts = Counter(str(e.get("kind") or "").lower() for lg in logs for e in lg.get("audio_events") or []
                     if isinstance(e, dict))
    minutes = max(seconds / 60.0, 0.1)
    return {k: round(v / minutes, 2) for k, v in counts.most_common() if k}


# ── combining references into one profile ───────────────────────────────────────────────────────────────────

def _grade_from(luma):
    if luma is None:
        return ""
    return "dark" if luma < 75 else "bright" if luma > 150 else "neutral"


def _consistency(values: list) -> float:
    """1 when every reference says the same, lower as they spread."""
    values = [v for v in values if v not in (None, "")]
    if len(values) < 2:
        return 0.75                                            # one reference: can't confirm
    return Counter(values).most_common(1)[0][1] / len(values)


def _mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 3) if values else None


def _mean_shares(rows: list) -> dict:
    rows = [r for r in rows if r]
    if not rows:
        return {}
    keys = set().union(*rows)
    return {k: round(sum(r.get(k, 0) for r in rows) / len(rows), 3)
            for k in sorted(keys, key=lambda k: -sum(r.get(k, 0) for r in rows))}


def build(references: list) -> dict:
    """references: [{"id", "title", "channel", "duration", "measures" (cr_style_measure.combine), "watch"
    (cr_style_watch.watch), "transcript": optional {"words", "seconds"}}] -> Style Profile."""
    refs = [dict(r, seen=watched_summary(r.get("watch") or {})) for r in references]
    measured = [r.get("measures") or {} for r in refs]
    seen = [r["seen"] for r in refs]
    has_m = any(m.get("edit") for m in measured)
    has_w = any(s for s in seen)
    profile = {"schema": SCHEMA, "references": [{"id": r.get("id"), "title": r.get("title", ""),
                                                 "channel": r.get("channel", ""), "duration": r.get("duration"),
                                                 "measured": bool((r.get("measures") or {}).get("edit")),
                                                 "watched_windows": len((r.get("watch") or {}).get("windows", [])),
                                                 "not_watched": (r.get("watch") or {}).get("missing", [])}
                                                for r in refs]}

    # pictures: what they are, how they look, who comes back
    families = _mean_shares([s.get("families") for s in seen])
    sources = _mean_shares([s.get("sources") for s in seen])
    art = [a for s in seen for a in s.get("art") or []]
    art_style = _dedupe([x for s in seen for x in s.get("art_style") or []], 4)
    top_art = sorted(art, key=lambda a: -a["share"])[:6]
    # one picture style throughout: every window's (and reference's) own description of it says much the same
    # (one reference is one channel: its windows describe the same style in different words)
    per_ref = [" ".join(s.get("art_style") or []) for s in seen if s and s.get("art_style")]
    one_style = bool(per_ref) and (len(per_ref) < 2 or _similar(per_ref) >= 0.2)
    profile["pictures"] = {
        "sources": _axis(sources, _consistency([max(s.get("sources") or {"": 0}, key=(s.get("sources") or {"": 0}).get)
                                                 for s in seen if s]) if has_w else 0, ["watched"] if has_w else [],
                         families=families),
        "art_style": _axis(art_style[0] if art_style else "", 0.8 if one_style else 0.5 if art_style else 0,
                           ["watched"] if art_style else [], alternatives=art_style[1:], looks=top_art,
                           consistent=one_style),
        "polish": _axis(_majority([s.get("polish") for s in seen if s]), 0.6 if has_w else 0,
                        ["watched"] if has_w else [], shots=_mean_shares([s.get("polish_shots") for s in seen]),
                        notes=_dedupe([n for s in seen for n in s.get("polish_notes") or []], 2),
                        note="cinematic, clean (advert-like), candid (everyday phone camera) or lo-fi"),
        "characters": _axis([p for s in seen for p in s.get("recurring") or []][:6],
                            0.7 if any(s.get("recurring") for s in seen) else 0.4 if has_w else 0,
                            ["watched"] if has_w else []),
    }
    lumas = [((m.get("look") or {}).get("luma")) for m in measured]
    grades_m = [_grade_from(x) for x in lumas if x is not None]
    grades_w = [s.get("grade") for s in seen if s.get("grade")]
    grade = _majority(grades_m, "") or _majority(grades_w, "neutral")      # measured brightness decides
    agree = sum(1 for g in grades_w if g == _majority(grades_m, g)) / len(grades_w) if grades_w and grades_m else 0.5
    profile["look"] = {
        "grade": _axis(grade, 0.5 * _consistency(grades_m + grades_w) + 0.5 * agree,
                       (["measured"] if grades_m else []) + (["watched"] if grades_w else []),
                       luma=_mean(lumas), measured_grades=grades_m,
                       dark_share=_mean([(m.get("look") or {}).get("dark_share") for m in measured]),
                       saturation=_mean([(m.get("look") or {}).get("saturation") for m in measured]),
                       filter=_dedupe([f for s in seen for f in s.get("filter") or []], 3)),
        "grain": _axis(_majority([s.get("grain") for s in seen]) or ("light" if (_mean([(m.get("look") or {}).get(
                       "grain") for m in measured]) or 0) > 1.0 else "none"), 0.6, ["measured", "watched"],
                       measured=_mean([(m.get("look") or {}).get("grain") for m in measured])),
        "palette": _axis([c for m in measured for c in (m.get("look") or {}).get("palette", [])][:8],
                         0.8 if has_m else 0, ["measured"] if has_m else []),
        "drawn": _axis(_mean([(m.get("look") or {}).get("flat") for m in measured]), 0.6 if has_m else 0,
                       ["measured"] if has_m else [], note="share of flat-colour areas: high for drawings and graphics"),
    }

    # motion: measured shares of still / camera / moving, checked against what Gemini saw move
    m_motion = _mean_shares([(m.get("edit") or {}).get("motion_share") for m in measured])
    w_motion = _mean_shares([s.get("motion_class") for s in seen])
    agreement = _agreement(m_motion, w_motion)
    profile["motion"] = {
        "classes": _axis(m_motion or w_motion, (agreement if agreement is not None else 0.5) if m_motion else 0.5,
                         (["measured"] if m_motion else []) + (["watched"] if w_motion else []),
                         watched=w_motion, agreement=agreement),
        "kinds": _axis(_mean_shares([s.get("motion") for s in seen]), 0.6 if has_w else 0,
                       ["watched"] if has_w else []),
        "camera": _axis(next(((m.get("edit") or {}).get("camera") for m in measured
                              if (m.get("edit") or {}).get("camera")), {}), 0.7 if has_m else 0,
                        ["measured"] if has_m else []),
        "pops_per_min": _axis(_mean([(m.get("edit") or {}).get("pops_per_min") for m in measured]),
                              0.7 if has_m else 0, ["measured"] if has_m else []),
    }

    # edit pace: measured (Gemini samples two frames a second and misses quick cuts)
    def pace(part):
        return _mean([((m.get(part) or {}).get("median_shot_s")) for m in measured])
    medians = [((m.get("edit") or {}).get("median_shot_s")) for m in measured]
    spread = (max(medians) / max(min(medians), 0.1)) if len([x for x in medians if x]) > 1 else 1.0
    profile["edit"] = {
        "median_shot_s": _axis(pace("edit"), 0.9 if spread < 1.6 else 0.6 if spread < 2.5 else 0.4,
                               ["measured"] if has_m else [], opening=pace("opening"), body=pace("body"),
                               per_reference=medians),
        "cuts_per_min": _axis(_mean([(m.get("edit") or {}).get("cuts_per_min") for m in measured]),
                              0.8 if has_m else 0, ["measured"] if has_m else []),
        "transitions": _axis(_mean_shares([(m.get("edit") or {}).get("transitions") for m in measured]),
                             0.7 if has_m else 0, ["measured"] if has_m else []),
        "hook": _axis(_dedupe([h for s in seen for h in s.get("hook") or []], 2), 0.6 if has_w else 0,
                      ["watched"] if has_w else []),
        "breathing": _axis(_dedupe([b for s in seen for b in s.get("breathing") or []], 3), 0.5 if has_w else 0,
                           ["watched"] if has_w else [],
                           long_pauses_per_min=_mean([((m.get("sound") or {}).get("pauses_per_min") or {}).get("1.5")
                                                      for m in measured])),
        "source_clips": _axis(_mean([(s.get("audio") or {}).get("source_audio", 0) for s in seen if s]),
                              0.6 if has_w else 0, ["watched"] if has_w else [],
                              note="share of runtime where real footage plays with its own sound"),
    }

    # on-screen words and graphics
    profile["text"] = {
        "density": _axis(_mean_shares([s.get("text") for s in seen]), 0.6 if has_w else 0, ["watched"] if has_w else []),
        "style": _axis(_dedupe([t for s in seen for t in s.get("text_style") or []], 3), 0.6 if has_w else 0,
                       ["watched"] if has_w else []),
        "graphics": _axis(_dedupe([g for s in seen for g in s.get("graphics") or []], 10), 0.6 if has_w else 0,
                          ["watched"] if has_w else []),
        "lettering": _axis(_majority([s.get("lettering") for s in seen if s]), 0.6 if has_w else 0,
                           ["watched"] if has_w else [], note="hand-drawn, typeset, mixed or none"),
    }

    # sound: music, effects, ambience. How LOUD the music sits is measured (a listening model called a bed 32 dB
    # under the voice "well balanced"); what it sounds like (role, moods, instruments) is watched.
    snd = [m.get("sound") or {} for m in measured]
    # Each reference's music is judged on its own: averaging decibels across a video with a bed and one that is
    # silent between sentences made a channel that uses music read as "none". When they disagree, the music the
    # creator can hear is kept and a question asks which way to go.
    per_ref = [music_level(s.get("bed_in_pauses_db"), s.get("filled_pauses_share"),
                           (s.get("pause_fill") or {}).get("tonal")) for s in snd]
    with_music = [s for s, lv in zip(snd, per_ref) if lv in ("score", "bed")]
    music_disagrees = bool(with_music) and len(with_music) < len([lv for lv in per_ref if lv is not None])
    pool = with_music if music_disagrees else snd
    fill = _mean([s.get("filled_pauses_share") for s in pool])
    bed = _mean([s.get("bed_in_pauses_db") for s in pool])
    tonal = _mean([(s.get("pause_fill") or {}).get("tonal") for s in pool])
    w_share = _mean([(s.get("music") or {}).get("share") for s in seen if s])
    level = music_level(bed, fill, tonal)
    measured_music = None if level is None else level in ("score", "bed")
    music_conf = 0.5 if w_share is not None else 0.3
    if measured_music is not None and w_share is not None:
        music_conf = 0.9 if measured_music == (w_share >= 0.4) else 0.55   # measured level wins a disagreement
    elif measured_music is not None:
        music_conf = 0.7
    if music_disagrees:
        music_conf = min(music_conf, 0.6)
    role = _majority([(s.get("music") or {}).get("role") for s in seen if s], "")
    if level in ("none", "quiet"):
        role = "none" if level == "none" else "quiet background"
    elif level and role in ("", "none"):
        role = "story score" if level == "score" else "background music"
    profile["sound"] = {
        "music": _axis(role or ("story score" if measured_music else "none"),
                       music_conf, (["measured"] if fill is not None else []) + (["watched"] if has_w else []), level=level,
                       share=w_share, bed_in_pauses_db=bed, filled_pauses=fill, tonal_in_pauses=tonal,
                       genres=_dedupe([x for s in seen for x in (s.get("music") or {}).get("genres") or []], 4),
                       moods=_dedupe([x for s in seen for x in (s.get("music") or {}).get("moods") or []], 8),
                       instruments=_dedupe([x for s in seen for x in (s.get("music") or {}).get("instruments") or []], 8),
                       under_voice=_majority([(s.get("music") or {}).get("under_voice") for s in seen if s]),
                       per_reference=per_ref, references_disagree=music_disagrees,
                       beat=_majority([(s.get("music") or {}).get("beat") for s in seen if s and
                                       (s.get("music") or {}).get("beat")]),
                       changes=_dedupe([x for s in seen for x in (s.get("music") or {}).get("changes") or []], 2)),
        "sfx": _axis(_ordinal_mean([(s.get("sfx") or {}).get("density") for s in seen if s]), 0.6 if has_w else 0,
                     ["watched"] if has_w else [],
                     kinds=_dedupe([x for s in seen for x in (s.get("sfx") or {}).get("kinds") or []], 10),
                     on=_dedupe([x for s in seen for x in (s.get("sfx") or {}).get("on") or []], 2),
                     booms_per_min=_mean([s.get("booms_per_min") for s in snd]),
                     hits_in_pauses_per_min=_mean([s.get("hits_in_pauses_per_min") for s in snd])),
        "ambience": _ambience_axis(seen, snd, has_w),
    }
    words = sum(_num((r.get("transcript") or {}).get("words")) for r in refs)
    secs = sum(_num((r.get("transcript") or {}).get("seconds")) for r in refs)
    profile["voice"] = _axis({k: _majority([(s.get("voice") or {}).get(k) for s in seen if s])
                              for k in ("gender", "age", "accent", "tone", "pace")},
                             0.6 if has_w else 0, ["watched"] if has_w else [],
                             wpm=round(words / (secs / 60)) if words and secs else None)
    profile["format"] = {
        "narration": _axis(_majority([s.get("narration") for s in seen if s], "voiceover"),
                           _consistency([s.get("narration") for s in seen if s]) if has_w else 0.3,
                           ["watched"] if has_w else []),
        "presenter": _presenter_axis(seen, has_w, [_num(r.get("duration"), 0) for r in refs]),
        "cta": _axis([c for s in seen for c in s.get("cta") or []][:8], 0.6 if has_w else 0,
                     ["watched"] if has_w else [],
                     kinds=sorted({str(c.get("kind") or "").lower() for s in seen for c in s.get("cta") or []
                                   if c.get("kind")})),
    }
    profile["recognisable"] = _dedupe([x for s in seen for x in s.get("recognisable") or []], 8)
    profile["questions"] = questions(profile)
    return profile


def _ambience_axis(seen: list, snd: list, has_w: bool) -> dict:
    """Environmental sound (rain, wind, rooms): Gemini names it; noisy, untuned sound in the measured pauses backs it."""
    amb = [s.get("ambience") for s in seen if isinstance(s.get("ambience"), dict)]
    present = any(a.get("present") for a in amb)
    noisy = _mean([(x.get("pause_fill") or {}).get("noisy") for x in snd])
    conf = 0.5 if has_w else 0
    if present and noisy is not None:
        conf = 0.8 if noisy >= 0.1 else 0.55
    return _axis(_dedupe([k for a in amb for k in a.get("kinds") or []], 6) if present else [], conf,
                 (["watched"] if has_w else []) + (["measured"] if noisy is not None else []),
                 present=present, share=_mean([a.get("share") for a in amb if a.get("present")]),
                 where=_dedupe([w for a in amb for w in a.get("where") or []], 2), noisy_in_pauses=noisy)


def _presenter_axis(seen: list, has_w: bool, durations: list = ()) -> dict:
    """How much a presenter or avatar talks to camera over the whole video, and how often they appear in the other
    pictures. The opening is where a presenter talks most, so its share counts for its three minutes only and the
    middle window's share for the rest."""
    rows = [s.get("presenter") for s in seen if s.get("presenter")]
    whole = []
    for r, dur in zip([s.get("presenter") or {} for s in seen], list(durations) + [0] * len(seen)):
        if r.get("opening") is not None and r.get("middle") is not None and dur and dur > 180:
            whole.append((r["opening"] * 180 + r["middle"] * (dur - 180)) / dur)
    on_camera = _mean(whole) if whole else _mean([r.get("on_camera") for r in rows])
    if on_camera is None:                                      # prompt v1: count presenter shots instead
        on_camera = _mean([(s.get("sources") or {}).get("presenter", 0) + (s.get("sources") or {}).get("avatar", 0)
                           for s in seen if s])
    people = [p for s in seen for p in s.get("recurring") or [] if p.get("role") in ("presenter", "mascot")]
    return _axis(round(on_camera or 0.0, 3), 0.6 if has_w else 0, ["watched"] if has_w else [],
                 in_pictures=_mean([r.get("in_pictures") for r in rows]),
                 who=[{"name": p["name"], "look": p["look"], "role": p["role"]} for p in people[:2]])


def music_level(bed_db, filled, tonal) -> str | None:
    """How present music is, from the measured pauses: "score" (a bed you always hear, 12-22 dB under the voice
    peak), "bed" (present, further back), "quiet" (faint) or "none". None when it couldn't be measured."""
    if bed_db is None or filled is None:
        return None
    if filled < 0.3 or bed_db < -45:
        return "none"
    if bed_db < -36 or (tonal is not None and tonal < 0.3):
        return "quiet"
    return "score" if bed_db >= -24 and filled >= 0.8 else "bed"


MASCOT_SHARE = 0.08          # a channel's host or mascot is on screen this often; a brand's logo character is not


def lead_character(profile: dict):
    """The character who carries the video (on screen at least 30% of the time), when the channel has no named
    host or mascot; else None."""
    chars = ((profile.get("pictures") or {}).get("characters") or {}).get("value") or []
    leads = [c for c in chars if isinstance(c, dict) and _num(c.get("screen_share")) >= 0.3 and c.get("look")]
    return max(leads, key=lambda c: _num(c.get("screen_share"))) if leads else None


def questions(profile: dict) -> list:
    """Short questions only where the profile is unsure or the creator has a real choice."""
    out = []
    pics = profile.get("pictures", {})
    if pics.get("art_style", {}).get("value") and not pics["art_style"].get("consistent"):
        out.append({"id": "art_style", "ask": "Your references use more than one picture style. Which should "
                    "every video keep?", "options": [pics["art_style"]["value"]] + pics["art_style"].get(
                        "alternatives", [])[:2]})
    people = [p for p in pics.get("characters", {}).get("value") or [] if p.get("role") in ("mascot", "presenter")
              and _num(p.get("screen_share")) >= MASCOT_SHARE]
    fam = (pics.get("sources") or {}).get("families") or {}
    drawn = _num(fam.get("generated")) + _num(fam.get("generated_motion")) >= 0.3
    # a lead character is offered as a mascot only when the pictures are drawn or generated (real people in footage
    # are the story's)
    lead = None if people or not drawn else lead_character(profile)
    if people:
        out.append({"id": "host", "ask": f"The channel has a recurring {people[0]['role']} ({people[0]['name']}). "
                    "Should your videos have one too?", "options": ["Yes, design one for my channel",
                                                                     "Yes, I'll upload mine", "No host"]})
    elif lead:
        # one character carries the whole video (an everyman like "Tom"): the channel's mascot, or this story's
        # hero? One reference can't show whether he returns, so the creator decides.
        out.append({"id": "host", "ask": f"{lead['name']} is on screen {round(lead['screen_share'] * 100)}% of the "
                    "time. Should every video keep one recurring character like that?",
                    "options": ["Yes, design one for my channel", "Yes, I'll upload mine",
                                "No, a new cast for each story"], "lead": lead["name"]})
    if set(profile.get("format", {}).get("cta", {}).get("kinds") or []) & {"own product", "ebook or pdf", "course",
                                                                              "qr code", "link"}:
        out.append({"id": "cta", "ask": "The reference promotes something in the video. Add your own calls to "
                    "action?", "options": ["Yes", "No"]})
    music = profile.get("sound", {}).get("music", {})
    if music.get("references_disagree"):
        level = music.get("level")
        out.append({"id": "music", "ask": "Some of your references have music under the voice and some have none. "
                    "What do you want?",
                    "options": (["A story score under the whole video", "Light music in places"] if level == "score"
                                else ["Light music in places", "A story score under the whole video"]) + ["No music"]})
    elif music.get("confidence", 1) < 0.5:
        out.append({"id": "music", "ask": "We couldn't tell how much music the channel uses. What do you want?",
                    "options": ["A story score under the whole video", "Light music in places", "No music"]})
    grade = profile.get("look", {}).get("grade", {})
    measured = [g for g in grade.get("measured_grades") or [] if g]
    if len(set(measured)) > 1:              # the references themselves differ; a model disagreeing is not a reason
        out.append({"id": "grade", "ask": "Your references differ in how dark they are. Which look?",
                    "options": ["Dark and cinematic", "Neutral", "Bright and clean"]})
    return out
