"""The art director: one Claude vision step that writes what only words can carry.

After the references have been measured and watched, a style still needs a
writer: the exact picture prompt every still is drawn with, the recurring cast
in words an image model can repeat, how pictures move, and the channel's voice
for the script writer and the editor. Claude looks at frames from the
references and reads the Style Profile, then answers in a fixed JSON shape.

Its answer is checked here (lengths, no channel or artist names, no copying of
the reference's words) and turned into style settings. If the call fails, the
study keeps the mapping's own settings and says so; nothing is invented.
"""
from __future__ import annotations

import json
import re

ART_PROMPT = """You are the art director of a faceless YouTube channel that is about to make videos in a style
learned from reference videos. Look at the attached frames (one contact sheet per reference video) and read what
was measured and watched below. Then write the style down so that an image generator, an image-to-video model, a
script writer and an editor can make NEW videos about NEW topics that look and sound like this channel, without
copying its footage, its characters' names, its words or its identity.

THE CREATOR'S VIDEO: [TITLE]
THE CREATOR'S BRIEF: [BRIEF]
THE RECIPE IT IS BUILT ON: [BASE]

WHAT STUDIO MEASURED AND WATCHED (JSON):
[PROFILE]

HOW THE REFERENCE NARRATION SOUNDS (an excerpt of its captions, for tone only, never to reuse):
[EXCERPT]

The channel will make videos about ANY topic. Separate what belongs to the CHANNEL (how things are drawn, lit,
coloured and framed in every video) from what belongs to the reference video's own story (its era, country,
landscape, costumes, buildings and their colours). Write only the channel's style; never let one story's setting
or palette become the channel's. With several references, keep what they share.

Return ONE JSON object:
{
 "description": "one sentence for the recipe card: what this channel makes and how it looks (no channel names)",
 "tagline": "at most eight words",
 "still_suffix": "60 to 110 words appended to EVERY picture prompt: the medium, line, shading, palette, light, camera
   and finish of the pictures, and how any words that appear in them are lettered (for example bold outlined comic
   letters, marker handwriting, a clean sans-serif), specific enough that every picture of a video matches. Describe
   style only, never subject matter. [POLISH]",
 "avoid": "15 to 40 words: what the pictures must never look like (the usual mistakes for this style)",
 "cast": "ONLY characters who return in EVERY video of the channel (a host, a mascot, a narrator character):
   'THE CAST — ' then each in drawable words (body, face, hair, clothes, one distinguishing item), with a new name.
   Never the people of the reference video's own story (they belong to that one video). Usually empty.",
 "people": "one or two sentences: how ANY person in this channel is drawn (body, head, face, proportions), so new
   characters in new stories look like they belong. Every person is whole in the frame or one clear close-up; never
   people shown only as hands, arms or a back (image models draw those as lone limbs)",
 "mascot": {"name": "a new name", "look": "drawable words", "role": "host or explainer"} or null. When a character
   in the profile is marked promoted, the creator wants every video to keep a recurring character like them: design
   that mascot the way they are drawn, keep their look only where it is not tied to that one story (never its costume,
   uniform, era clothing or props), and give them one distinguishing item (a cap, a scarf, glasses),
 "video_motion": "25 to 50 words: how a picture of this style may move when it is animated (what moves, how much,
   what never changes); for cut-out styles say the figures slide as flat cut-outs",
 "script_voice": ["3 to 6 short lines for the script writer: point of view (you / I / they), tone, sentence length,
   humour, how facts are delivered, how each video opens and ends"],
 "editor_notes": ["3 to 6 short lines for the editor: what each kind of sentence is shown with, when graphics or
   text appear, where the edit breathes"],
 "craft": "story (one person's or one event's story, told in order) or teaching (the viewer learns how something
   works or what to do)",
 "broll": ["6 to 10 short stock-footage searches (2 to 4 words each) for the generic b-roll this channel cuts to
   between its specific shots, in its subject area and mood (for a money channel: 'counting cash close up', 'bank
   building exterior'); never names, never the reference video's own story"],
 "hook_notes": "one or two sentences: how the first 30 seconds run",
 "body_notes": "one or two sentences: how the edit runs after the hook"
}
Write in English. Never name the reference channel, its creator, any artist or any studio; describe instead."""

CANDID = ("The pictures are photographs: write them as everyday phone-camera frames (deep focus, natural mixed light, "
          "unstaged framing, mild noise), never glossy, studio-lit or cinematic. Where the profile calls the light "
          "commercial, studio or crisp, ignore it: AI photographs that look that perfect read as fake.")
# Words that make an image model draw glossy, perfect photographs; a candid style never carries them.
_GLOSSY = re.compile(r"\b(commercial|studio|professional|glossy|polished|cinematic|dramatic|crisp|flawless|high-end|"
                     r"editorial|advertising)[ -]+(?:(?:product|food|kitchen|supermarket|warehouse)\s+)?(lighting|light|"
                     r"lit|look|finish|photography|photos?|focus|clarity|quality|grade|style)\b", re.I)
_GLOSSY_WORD = re.compile(r"\b(glossy|cinematic|polished|flawless|studio-lit|airbrushed)\b,?\s*", re.I)
LO_FI = "The pictures are visibly degraded: compressed, soft, VHS or webcam-like."
_FIELDS = ("description", "tagline", "still_suffix", "avoid", "cast", "people", "video_motion", "hook_notes",
           "body_notes")


def _compact(profile: dict, mapping: dict) -> dict:
    """The parts of the profile an art director needs, small enough to read in one go."""
    def v(*path):
        cur = profile
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        return cur
    return {
        "recognisable": profile.get("recognisable", [])[:8],
        "art_style": v("pictures", "art_style", "value"), "art_alternatives": v("pictures", "art_style", "alternatives"),
        "picture_finish": v("pictures", "polish", "value"), "picture_sources": v("pictures", "sources", "value"),
        "characters": [{k: c.get(k) for k in ("name", "look", "role", "screen_share", "promoted") if k in c}
                       for c in (v("pictures", "characters", "value") or [])[:5]],
        "grade": {k: v("look", "grade", k) for k in ("value", "luma", "filter")},
        "motion": v("motion", "classes", "value"), "motion_kinds": v("motion", "kinds", "value"),
        "median_shot_s": {k: v("edit", "median_shot_s", k) for k in ("value", "opening", "body")},
        "hook": v("edit", "hook", "value"), "breathing": v("edit", "breathing", "value"),
        "text_style": v("text", "style", "value"), "lettering": v("text", "lettering", "value"),
        "graphics": v("text", "graphics", "value"),
        "music": {k: v("sound", "music", k) for k in ("value", "level", "moods", "genres")},
        "sfx": {k: v("sound", "sfx", k) for k in ("value", "kinds", "on")},
        "voice": v("voice", "value"), "presenter_share": v("format", "presenter", "value"),
        "settings_chosen": [{"area": r["area"], "label": r["label"], "value": r["value"]}
                            for r in (mapping.get("settings") or []) if r.get("path") != "features.offered"][:30],
    }


def prompt(profile: dict, mapping: dict, *, title: str, brief: str, base: str, excerpt: str) -> str:
    polish = (((profile.get("pictures") or {}).get("polish") or {}).get("value") or "")
    rule = next((n.get("polish_rule") for n in mapping.get("notes") or [] if n.get("polish_rule")), "")
    polish_line = CANDID if rule.startswith("candid") or polish == "candid" else LO_FI if polish == "lo-fi" else ""
    return (ART_PROMPT.replace("[TITLE]", title.strip()[:280]).replace("[BRIEF]", (brief or "none").strip()[:1500])
            .replace("[BASE]", base).replace("[POLISH]", polish_line)
            .replace("[PROFILE]", json.dumps(_compact(profile, mapping), ensure_ascii=False)[:9000])
            .replace("[EXCERPT]", (excerpt or "no captions available").strip()[:1500]))


def _json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        try:
            value = json.loads(text[a:b + 1]) if 0 <= a < b else None
        except ValueError:
            value = None
    return value if isinstance(value, dict) else {}


def _clean(text, limit: int, banned: list) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) > limit:                                   # cut at the last full sentence, never mid-word
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("; "))
        text = cut[:end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0]
    for name in banned:
        if name and len(name) >= 4:
            text = re.sub(re.escape(name), "the reference", text, flags=re.I)
    return text


_NEGATED = re.compile(r"\b(no|not|never|without|nor|avoid)\s+(?:\w+[ -]+){0,2}$", re.I)
# people shown only in part: the picture model draws them as a lone hand or arm reaching into the frame
_PARTIAL = re.compile(r"\b(in part|partly|partially|disembodied|only (?:as |their )?(?:hands|arms)|such as (?:their )?hands"
                      r"|hands or (?:a |their )?backs?|(?:just|only) (?:a |their )?(?:hands?|arms?))\b", re.I)


def _whole_people(text: str) -> str:
    """The text without any sentence that asks for people shown in part (only hands, arms or a back)."""
    kept = [s for s in re.split(r"(?<=[.!?])\s+", text or "") if not _PARTIAL.search(s)]
    return " ".join(kept).strip()


def _candid(text: str) -> str:
    """A candid picture style without the words that make a model draw glossy, perfect photographs."""
    def plain(match):
        if _NEGATED.search(match.string[:match.start()]):
            return match.group(0)                       # "no studio light" already says it plainly
        words = match.group(0).lower()
        if "light" in words or words.endswith("lit"):
            return "natural light"
        if "focus" in words or "clarity" in words:
            return "deep focus"
        if words.endswith(("photo", "photos")):
            return "everyday photos"
        return "everyday photo look"
    text = _GLOSSY_WORD.sub("", _GLOSSY.sub(plain, text))
    for phrase in ("natural light", "deep focus", "everyday photo look"):        # say each once
        first = text.lower().find(phrase)
        if first >= 0:
            head, tail = text[:first + len(phrase)], text[first + len(phrase):]
            text = head + re.sub(r",?\s*" + phrase, "", tail, flags=re.I)
    return re.sub(r"\s+([,.;])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()


def check(answer: dict, *, banned: list, candid: bool = False) -> dict:
    """The art director's answer, cleaned: bounded lengths, the reference channel's names removed, and for a
    candid photographic style no glossy words in the picture prompt."""
    limits = {"cast": 1200, "still_suffix": 1400, "avoid": 500}
    out = {key: _clean(answer.get(key), limits.get(key, 700), banned) for key in _FIELDS}
    if candid:
        for key in ("still_suffix", "people", "description"):
            out[key] = _candid(out[key])
    for key in ("still_suffix", "people", "cast"):
        out[key] = _whole_people(out[key])
    words = len(out["still_suffix"].split())
    if words < 20:
        raise ValueError("the picture style came back too short to steer an image model")
    mascot = answer.get("mascot") if isinstance(answer.get("mascot"), dict) else None
    out["mascot"] = ({"name": _clean(mascot.get("name"), 40, banned), "look": _clean(mascot.get("look"), 400, banned),
                      "role": _clean(mascot.get("role"), 40, banned)} if mascot and mascot.get("look") else None)
    craft = str(answer.get("craft") or "").strip().lower()
    out["craft"] = "story" if craft.startswith("story") else "teaching" if craft.startswith("teach") else ""
    for key in ("script_voice", "editor_notes"):
        rows = answer.get(key) if isinstance(answer.get(key), list) else []
        out[key] = [_clean(x, 220, banned) for x in rows if str(x).strip()][:6]
    rows = answer.get("broll") if isinstance(answer.get("broll"), list) else []
    out["broll"] = [q for q in (_clean(x, 60, banned) for x in rows) if 2 <= len(q.split()) <= 6][:10]
    return out


# An image model dresses an archival picture with its own caption strip ("ALPINE CLUB, 1968" on a 1930s scene): every
# learned style's pictures carry only the words their scene asks for.
LETTERING = (" Lettering: only the words the scene itself names, on the thing they belong to; never an invented caption, "
             "date, title, place name or archive label.")


def overrides(art: dict) -> dict:
    """Style settings from a checked answer (deep-merged over the mapping's own)."""
    avoid = re.sub(r"^(no|never|avoid)[:\s]+", "", art.get("avoid") or "", flags=re.I).rstrip(".")
    people = (" People: " + art["people"].rstrip(".") + ".") if art.get("people") else ""
    look = {"still_suffix": " " + art["still_suffix"].rstrip(".") + "." + people + (f" Avoid: {avoid}." if avoid else "")
            + LETTERING}
    cast = art.get("cast", "")
    if art.get("mascot"):
        m = art["mascot"]
        cast = (cast + "\n" if cast else "THE CAST — ") + f"{m['name']} ({m['role'] or 'host'}): {m['look']}"
    if cast:
        look["character"] = cast
    if art.get("video_motion"):
        look["video_motion"] = art["video_motion"]
    out = {"look": look}
    if art.get("description"):
        out["description"] = art["description"]
    if art.get("tagline"):
        out["tagline"] = art["tagline"]
    if art.get("script_voice"):
        out["script_notes"] = art["script_voice"]
    if art.get("broll"):
        # the channel's own fallback b-roll searches (without them the engine falls back to a cosmic default pool)
        out["stock_queries"] = art["broll"]
    if art.get("editor_notes"):
        out["director_notes"] = art["editor_notes"]
    pacing = {k: art[k] for k in ("hook_notes", "body_notes") if art.get(k)}
    if pacing:
        out["pacing"] = pacing
    return out


def direct(see, profile: dict, mapping: dict, *, title: str, brief: str, base: str, excerpt: str, sheets: list,
           banned: list, log=print) -> dict:
    """Run the art director once. Returns {"art": checked answer, "overrides": {...}} or {} when it can't."""
    raw = see(prompt(profile, mapping, title=title, brief=brief, base=base, excerpt=excerpt), sheets)
    answer = _json(raw)
    if not answer:
        log("  art director: no usable answer; the learned settings are kept without written picture style")
        return {}
    rule = next((n.get("polish_rule") for n in mapping.get("notes") or [] if n.get("polish_rule")), "")
    polish = (((profile.get("pictures") or {}).get("polish") or {}).get("value") or "")
    try:
        art = check(answer, banned=banned, candid=rule.startswith("candid") or polish == "candid")
    except ValueError as exc:
        log(f"  art director: {exc}; the learned settings are kept without it")
        return {}
    log(f"  art director: picture style {len(art['still_suffix'].split())} words"
        + (", cast written" if art.get("cast") else "") + (", mascot designed" if art.get("mascot") else ""))
    return {"art": art, "overrides": overrides(art)}
