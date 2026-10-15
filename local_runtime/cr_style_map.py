"""From a Style Profile to Studio settings, in code.

The profile says what a channel looks and sounds like on the same axes for every
niche. This module turns it into a recipe: which installed recipe to build on,
and what to change in it so the learned channel, not the base, decides the
pictures, characters, pace, motion, look, captions, sound, voice, presenter and
calls to action. Every setting carries the reason it was chosen and where that
came from, for the review screen.

What only a writer can do (the exact picture prompt, the cast's words, the
channel's description and script voice) is handed to the study's art director
step as notes; what needs pictures (a style reference, a character sheet) is
listed under "needs" and made after the creator approves the cost.

Nothing here calls a model or a paid service.
"""
from __future__ import annotations

import copy
import re

SCHEMA = 1

# The recipes a learned style can be built on, by what their machinery is for. Order matters: the first that fits wins.
BASES = {
    "nocturne": "3D reconstructions cut against real archive footage",
    "3d": "scenes rebuilt in real-time 3D",
    "avatar-premium": "a presenter on camera and candid pictures drawn for the exact words",
    "stories": "a new drawn or generated picture for every line of the story",
    "documentary": "real footage, documents, maps and graphics",
    "jung": "a calm mix of footage, generated pictures and words on screen",
}
FALLBACK = ("documentary", "jung", "stories")

# Rough costs for the review screen (Kie, October 2026); real costs show in the render log.
PICTURE_USD = 0.02          # one generated still
CLIP_USD = 0.12             # one 5-second image-to-video clip
PRESENTER_USD_S = 0.0375    # one second of talking presenter (Kling Standard on Kie)

# The engine's own lists (lookreg); a value outside them falls back to the base's
GRADE_WORDS = {"natural": "natural colour", "clean709": "clean and bright", "bleach_bypass": "dark and desaturated",
               "kodachrome_warm": "warm film colour", "cold_steel": "cold steel blue", "tungsten_night": "dark, warm night",
               "teal_steel": "dark teal", "golden_hour": "golden light", "mono_silver": "black and white",
               "archive_sepia": "sepia archive"}
PUSH_WORDS = {"none": "no camera move", "slow_in": "slow push in", "slow_out": "slow pull out",
              "drift_lateral": "slow sideways drift", "handheld_micro": "slight handheld sway",
              "alternate": "push in, then pull out"}
GRADES = ("natural", "clean709", "bleach_bypass", "kodachrome_warm", "cold_steel", "tungsten_night", "teal_steel",
          "golden_hour", "mono_silver", "archive_sepia")
VIBES = ("serious_documentary", "tense_investigation", "true_crime_dark", "cold_war_espionage", "tech_pulse",
         "business_rise_fall", "epic_history", "disaster_dread", "sports_drive", "mystery_drone", "nostalgic_retro",
         "uplifting_science", "gaming_synth", "regime_oppressive")

# Gemini TTS voices (the Kie narrator customers use): gender and character, for a suggestion the creator can change
VOICES = {
    "Charon": ("male", "informative"), "Iapetus": ("male", "clear"), "Algenib": ("male", "gravelly"),
    "Rasalgethi": ("male", "informative"), "Alnilam": ("male", "firm"), "Orus": ("male", "firm"),
    "Schedar": ("male", "even"), "Sadaltager": ("male", "knowledgeable"), "Umbriel": ("male", "easy-going"),
    "Achird": ("male", "friendly"), "Zubenelgenubi": ("male", "casual"), "Enceladus": ("male", "breathy"),
    "Algieba": ("male", "smooth"), "Puck": ("male", "upbeat"), "Fenrir": ("male", "excitable"),
    "Sadachbia": ("male", "lively"),
    "Kore": ("female", "firm"), "Gacrux": ("female", "mature"), "Sulafat": ("female", "warm"),
    "Vindemiatrix": ("female", "gentle"), "Achernar": ("female", "soft"), "Erinome": ("female", "clear"),
    "Despina": ("female", "smooth"), "Aoede": ("female", "breezy"), "Callirrhoe": ("female", "easy-going"),
    "Zephyr": ("female", "bright"), "Autonoe": ("female", "bright"), "Leda": ("female", "youthful"),
    "Laomedeia": ("female", "upbeat"), "Pulcherrima": ("female", "forward"),
}
_TONE_VOICE = [  # (words in the watched tone/age, male voice, female voice)
    (("older", "gravel", "grizzled", "weathered", "experienced"), "Algenib", "Gacrux"),
    (("authoritative", "knowledge", "expert", "instruct"), "Sadaltager", "Kore"),
    (("casual", "convers", "chatty", "dry", "gritty", "direct", "satiric", "sarcas", "deadpan"), "Zubenelgenubi",
     "Callirrhoe"),
    (("calm", "empath", "gentle", "soft", "reflect", "slow", "sombre", "somber"), "Enceladus", "Vindemiatrix"),
    (("warm", "storytell", "narrat"), "Charon", "Sulafat"),
    (("energetic", "excit", "upbeat", "fast", "hype", "lively"), "Puck", "Laomedeia"),
    (("serious", "cinematic", "measured", "objective", "investig", "documentary"), "Iapetus", "Erinome"),
]


def _v(axis, default=None):
    return axis.get("value", default) if isinstance(axis, dict) else default


def _get(profile, *path, default=None):
    cur = profile
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _has_words(text, *words):
    text = str(text or "").lower()
    return any(w in text for w in words)


PICTURE_MODEL = "nano-banana-2-1"                # Kie image model for styles drawn against a style drawing


class Plan:
    """Collects overrides and the reasons for them."""

    def __init__(self):
        self.overrides, self.remove, self.settings, self.needs, self.notes = {}, [], [], [], []
        self.motion_cutout = False
        self.candid_photo = False

    def set(self, path: str, value, *, area: str, label: str, why: str, source: str = "", confidence: float = None,
            show=None):
        cur = self.overrides
        keys = path.split(".")
        for key in keys[:-1]:
            cur = cur.setdefault(key, {})
        cur[keys[-1]] = value
        row = {"area": area, "label": label, "value": value if show is None else show, "why": why, "path": path}
        if source:
            row["from"] = source
        if confidence is not None:
            row["confidence"] = round(confidence, 2)
        self.settings.append(row)

    def drop(self, path: str, why: str):
        self.remove.append({"path": path, "why": why})


# ── which recipe to build on ────────────────────────────────────────────────────────────────────────────────

def _families(profile) -> dict:
    return _get(profile, "pictures", "sources", "families", default={}) or {}


def _presenter_kind(profile) -> str:
    """"talking" for a person (or photoreal avatar) who talks to camera, "mascot" for a drawn host, "" for none."""
    share = float(_v(_get(profile, "format", "presenter"), 0) or 0)
    who = _get(profile, "format", "presenter", "who", default=[]) or []
    drawn = any(_has_words(p.get("look"), "cartoon", "drawn", "stick", "cutout", "cut-out", "icon", "mascot", "2d",
                           "illustrat", "doodle", "vector") for p in who)
    sources = _v(_get(profile, "pictures", "sources"), {}) or {}
    talking = share >= 0.03 or (sources.get("presenter", 0) + sources.get("avatar", 0)) >= 0.04
    if talking and not drawn:
        return "talking"
    # a host is on screen often; a brand's logo character that appears for a moment is not the channel's mascot
    cast = {str(c.get("name", "")).lower(): c.get("screen_share", 0) for c in
            _v(_get(profile, "pictures", "characters"), []) or []}
    if talking or any(cast.get(str(p.get("name", "")).lower(), 0) >= 0.12 for p in who):
        return "mascot"
    return ""


def choose_base(profile: dict, installed) -> tuple:
    """(recipe name, why) for this profile, from the recipes installed here."""
    fam = _families(profile)
    generated = fam.get("generated", 0) + fam.get("generated_motion", 0)
    real = fam.get("footage", 0) + fam.get("photo", 0) + fam.get("document", 0)
    picks = []
    if fam.get("3d", 0) >= 0.3:
        picks.append(("nocturne" if fam.get("footage", 0) >= 0.15 else "3d",
                      f"{round(fam['3d'] * 100)}% of the pictures are 3D scenes"))
    if _presenter_kind(profile) == "talking":
        picks.append(("avatar-premium", "a presenter talks to camera between the pictures"))
    if fam.get("panel", 0) >= 0.5:
        picks.append(("stories", "comic panels on screen the whole time (drawn in the learned style until your own "
                                 "panels can be uploaded)"))
    if generated >= 0.5:
        picks.append(("stories", f"{round(generated * 100)}% of the pictures are drawn or generated"))
    if real >= 0.4 or (real >= 0.2 and real + fam.get("graphic", 0) >= 0.5 and generated < 0.3):
        picks.append(("documentary", f"{round(real * 100)}% of the pictures are real footage, photos or documents"
                      + (f", {round(fam.get('graphic', 0) * 100)}% motion graphics" if fam.get("graphic", 0) >= 0.2
                         else "")))
    picks.append(("jung", "a mix of footage, generated pictures and words on screen"))
    names = installed if isinstance(installed, (set, list, tuple, dict)) else ()
    for name, why in picks:
        if name in names:
            return name, why
    for name in FALLBACK:
        if name in names:
            return name, "the closest recipe installed here"
    raise ValueError("No base recipe is installed. Update Studio before learning a style.")


# ── the settings ───────────────────────────────────────────────────────────────────────────────────────────

def _pictures(plan: Plan, profile: dict, base: str, base_style: dict):
    pics = profile.get("pictures") or {}
    art = pics.get("art_style") or {}
    polish = _v(pics.get("polish"), "")
    # never the base's own look: its character, its picture, its reference drawing, its sample video
    look = base_style.get("look") or {}
    for key, why in (("character_image", "the base recipe's character picture belongs to the base, not this channel"),
                     ("style_image", "the base recipe's style drawing belongs to the base"),
                     ("reference", "the base recipe's reference picture belongs to the base"),
                     ("sample", "the base recipe's sample video shows the base, not this channel")):
        if look.get(key):
            plan.drop("look." + key, why)
    if look.get("character"):
        plan.set("look.character", "", area="Pictures", label="Recurring cast",
                 why="the starting recipe's characters belong to that recipe, not to this channel", source="",
                 show="written for this channel by the art director")
    fam = _families(profile)
    generated = fam.get("generated", 0) + fam.get("generated_motion", 0)
    draws = generated >= 0.15                      # this channel's own pictures are drawn or generated, not filmed
    if art.get("value"):
        plan.notes.append({"for": "still_suffix", "art_style": art["value"], "alternatives": art.get("alternatives", []),
                           "polish": polish, "looks": art.get("looks", [])[:4]})
        if draws:
            plan.needs.append("style_image")
            # every picture copies the style drawing: Nano Banana 2.1 keeps the style and whole bodies where the
            # first Nano Banana copied the drawing's layout (half-blank frames, arms without a body), same price
            plan.set("look.image_model", PICTURE_MODEL, area="Pictures", label="Picture model",
                     why="follows the style drawing closely and draws whole people, at the same price per picture",
                     show="Nano Banana 2.1")
    # a cast is drawn only for generated pictures: real people in footage are filmed, not designed
    cast = [c for c in _v(pics.get("characters"), []) or [] if c.get("look")] if draws else []
    # Only a host or mascot returns in every video; the people of a reference's own story belong to that video.
    import cr_style_profile
    main = [c for c in cast if c.get("role") in ("mascot", "presenter")
            and float(c.get("screen_share") or 0) >= cr_style_profile.MASCOT_SHARE]
    if cast:
        plan.notes.append({"for": "cast", "characters": cast[:4]})
    if main:
        plan.needs.append("character_image")
    # Story placement stays: one picture per line, in order, never repeated. The engine asks for a main character
    # in every frame only when the style has a cast, and a learned style's cast is its host or mascot alone.
    photo = any(_has_words(a.get("look"), "photo", "realistic", "footage", "camera") for a in art.get("looks", [])) \
        or _has_words(art.get("value"), "photo", "realistic")
    # a designed picture (paper collage, cut-outs, charts, drawings, renders) is not a candid photograph, even when
    # it is made of photographic pieces: it keeps its crisp finish
    if _has_words(art.get("value"), "collage", "cutout", "cut-out", "paper", "graphic", "chart", "illustrat", "drawn",
                  "drawing", "render", "vector", "cartoon", "animat"):
        photo = False
    if photo and polish in ("clean", "candid") and draws:
        # glossy, evenly lit AI photographs read as fake; an everyday phone-camera frame reads as real
        polish = "candid"
    if polish == "candid":
        plan.notes.append({"for": "still_suffix", "polish_rule": "candid: an everyday phone-camera frame, natural "
                           "mixed light, unstaged framing, deep focus, mild noise; never cinematic or studio-lit"})
        if photo and draws:
            plan.candid_photo = True
            # words alone still come back too perfect: each photo is also made to look like a frame of a phone
            # video (softer capture, sensor noise, phone sharpening, compression; the engine's realism step)
            plan.set("look.stills.realism", "home_video", area="Pictures", label="Phone-camera finish",
                     why="the references' photos look like ordinary phone pictures, not glossy AI photographs",
                     source="watched", show="on")
    elif polish == "lo-fi":
        plan.notes.append({"for": "still_suffix", "polish_rule": "lo-fi: visibly degraded, compressed, VHS or webcam"})
    drawn = float(_v(_get(profile, "look", "drawn"), 0) or 0)
    if generated >= 0.5 and drawn >= 0.45:
        plan.set("look.dust", False, area="Look", label="Film dust", why="a clean drawn look carries no film dust",
                 source="measured")
        plan.set("look.vignette", False, area="Look", label="Dark vignette",
                 why="flat drawings keep their edges clean", source="measured")
        plan.set("look.encode", {"preset": "slow", "tune": "animation", "crf": 18}, area="Look",
                 label="Encoder", why="flat colour areas need the animation encoder to stay clean", source="measured")


def _pace(plan: Plan, profile: dict, base: str):
    edit = profile.get("edit") or {}
    med = edit.get("median_shot_s") or {}
    body = med.get("body") or _v(med)
    opening = med.get("opening")
    if not body:
        return
    conf = med.get("confidence", 0.7)
    if base in ("stories", "avatar-premium"):
        every = round(_clamp(float(body), 1.6, 12.0), 1)
        plan.set("pacing.photo_every_s", every, area="Pace", label="A new picture every",
                 why=f"the references change picture every {float(body):.1f} s (measured)", source="measured",
                 confidence=conf, show=f"{every} s")
    else:
        cut = round(_clamp(float(body), 1.5, 12.0), 1)
        plan.set("pacing.cut_s", cut, area="Pace", label="Footage cut length",
                 why=f"the references cut every {float(body):.1f} s after the opening (measured)", source="measured",
                 confidence=conf, show=f"{cut} s")
    if opening and body and float(opening) < float(body) * 0.75:
        plan.set("pacing.hook_notes", (f"The opening minute cuts faster than the rest: about every {float(opening):.1f} s "
                                       f"against {float(body):.1f} s later."),
                 area="Pace", label="Faster opening", why=f"opening shots {float(opening):.1f} s, later {float(body):.1f} s",
                 source="measured", confidence=conf)
    trans = _v(edit.get("transitions"), {}) or {}
    picks = []                                  # the engine's accent transitions: {"id", "at", "max"}
    if trans.get("fade", 0) >= 0.12 or trans.get("dissolve", 0) >= 0.1:
        picks.append({"id": "dip_black", "at": "chapter", "max": 6})
    if trans.get("flash", 0) >= 0.06:
        picks.append({"id": "flash_white", "at": "reveal", "max": 8, "min_gap_s": 20.0})
    if trans.get("whip", 0) >= 0.06:
        picks.append({"id": "whip_blur", "at": "sparse_any", "max": 10, "min_gap_s": 15.0})
    plan.set("look.fx.transitions", picks, area="Pace", label="Transitions",
             show=", ".join(t["id"].replace("_", " ") for t in picks) or "hard cuts",
             why=("hard cuts almost everywhere" if not picks else
                  ", ".join(f"{k} {round(v * 100)}%" for k, v in sorted(trans.items(), key=lambda kv: -kv[1])
                            if k not in ("start",) and v >= 0.05)), source="measured")
    if base in ("stories", "avatar-premium"):
        flashes = trans.get("flash", 0)
        plan.set("pacing.white_fade", 0.2 if flashes >= 0.1 else 0.0, area="Pace", label="White flash between pictures",
                 show="yes" if flashes >= 0.1 else "none",
                 why=f"{round(flashes * 100)}% of changes flash white", source="measured")


def _whiteboard(profile) -> bool:
    """The references are whiteboard animation: a hand draws the pictures live (as watched)."""
    words = " ".join([str(_v(_get(profile, "pictures", "art_style"), "") or "")]
                     + [str(x) for x in (_get(profile, "pictures", "art_style", "alternatives", default=[]) or [])]
                     + [str(x) for x in (profile.get("recognisable") or [])]).lower()
    return "whiteboard" in words or bool(re.search(r"\bhands?\b[\w\s-]{0,24}\b(draw|drawing|draws|sketch|sketching)\b",
                                                   words))


def _motion(plan: Plan, profile: dict, base: str, minutes_hint: float = 10.0):
    motion = profile.get("motion") or {}
    kinds = _v(motion.get("kinds"), {}) or {}
    cam = _v(motion.get("camera"), {}) or {}
    classes = _v(motion.get("classes"), {}) or {}
    animated = kinds.get("animated", 0)
    cutout = kinds.get("cutout_move", 0)
    fam = _families(profile)
    generated = fam.get("generated", 0) + fam.get("generated_motion", 0)
    # How MUCH moves is measured (a model calls nearly every drawn shot "animated"); what kind of motion is watched.
    measured_moving = classes.get("moving")
    moving = measured_moving if measured_moving is not None else animated + cutout
    moving_generated = min(moving, generated)
    drawn = float(_v(_get(profile, "look", "drawn"), 0) or 0) >= 0.4 or _has_words(
        _v(_get(profile, "pictures", "art_style"), ""), "drawn", "drawing", "cartoon", "stick", "illustrat", "vector",
        "doodle", "comic", "whiteboard", "sketch", "manga", "manhwa", "webtoon", "paint")
    clips = 0.0
    if generated >= 0.3 and _whiteboard(profile):
        # a whiteboard channel's motion is the drawing itself: a hand draws each picture's lines on (cr_drawon)
        plan.set("look.reveal", "draw", area="Motion", label="Drawn on by hand",
                 why="the references draw each picture live, a hand tracing the lines with a marker (watched)",
                 source="watched", show="every picture draws itself on")
        clips = 0.0
    elif generated >= 0.3 and moving_generated >= 0.15:
        if drawn or cutout >= 0.08:
            # drawn figures move the way these channels move them: lifted off the background and slid, not AI video
            plan.motion_cutout = True
            plan.settings.append({"area": "Motion", "label": "Cut-out movement", "value": "on", "from": "measured",
                                  "why": f"{round(moving * 100)}% of the runtime moves; drawn figures move as cut-outs",
                                  "path": "features.on"})
            # as often as the references move, and any drawn figure or prop may be lifted (not only people)
            every = 2 if moving >= 0.3 else 3 if moving >= 0.15 else 4
            plan.set("look.photofx.depth_every", every, area="Motion", label="Pictures that move as cut-outs",
                     why=f"{round(moving * 100)}% of the references' runtime moves", source="measured",
                     show=f"one in {every}")
            plan.set("look.photofx.depth_subjects", "any", area="Motion", label="What lifts off the picture",
                     why="any drawn figure or prop can slide as a cut-out", source="measured", show="figures and props")
            clips = 1.0 if moving_generated >= 0.4 else 0.0
        else:
            clips = round(_clamp(moving_generated * 6, 1.0, 6.0), 1)
    moving_pictures = moving_generated
    if base in ("stories", "avatar-premium"):
        plan.set("pacing.clips_per_min", clips, area="Motion", label="Moving pictures",
                 why=("the references' pictures stay still, or move as cut-outs" if not clips else
                      f"{round(moving_pictures * 100)}% of the runtime moves (measured)"),
                 source="measured", show=f"{clips:g} a minute (about ${clips * CLIP_USD:.2f} a minute)" if clips else "off")
    elif clips:
        plan.set("look.animate", {"every": 2, "seconds": 5, "max": int(_clamp(clips * minutes_hint, 2, 12))},
                 area="Motion", label="Moving pictures", why=f"{round(moving_pictures * 100)}% of the runtime moves (measured)",
                 source="measured", show=f"about {clips:g} a minute")
    zin, zout = cam.get("zoom_in_share", 0), cam.get("zoom_out_share", 0)
    still_cam = classes.get("camera", 0)
    still = classes.get("still", 0)
    # of the pictures that are not moving on their own, how many the camera leaves alone (a channel that holds
    # its pictures still must not get a push on every one: the engine's default)
    held = still / (still + still_cam) if still + still_cam > 0 else 0.0
    mode = "alternate" if zout >= 0.15 or (zin and zout) else "in"
    if still + still_cam > 0:
        if held >= 0.97:
            value, show = "still", "held still"
        elif held >= 0.25:
            value = f"mix:{round(held, 2)}" + (":alternate" if mode == "alternate" else "")
            show = f"held still {round(held * 100)}% of the time, otherwise " + PUSH_WORDS[
                "alternate" if mode == "alternate" else "slow_in"]
        else:
            value, show = mode, PUSH_WORDS["alternate" if mode == "alternate" else "slow_in"]
        plan.set("look.zoom", value, area="Motion", label="Camera on still pictures", show=show,
                 why=f"the camera moves over {round(still_cam * 100)}% of the runtime and holds still over "
                     f"{round(still * 100)}%" + (", pushing in and pulling out" if mode == "alternate" else ""),
                 source="measured")
    if still_cam >= 0.08 or zin or zout:
        push = "alternate" if mode == "alternate" else "slow_in"
        if kinds.get("shake", 0) >= 0.1:
            push = "handheld_micro"
        elif kinds.get("pan", 0) > max(kinds.get("zoom_in", 0), kinds.get("zoom_out", 0)):
            push = "drift_lateral"
        plan.set("look.fx.push", push, area="Motion", label="Camera move on footage and stills", show=PUSH_WORDS[push],
                 why="the dominant camera move in the references", source="measured")


def _look(plan: Plan, profile: dict):
    look = profile.get("look") or {}
    grade = look.get("grade") or {}
    value = _v(grade, "neutral")
    luma, sat = grade.get("luma"), grade.get("saturation")
    filters = " ".join(grade.get("filter") or []).lower()
    grain = _v(look.get("grain"), "none")
    if (sat is not None and sat < 0.06 and _has_words(filters, "black and white", "black-and-white", "monochrome", "b&w",
                                                     "greyscale", "grayscale", "desaturat")) \
            or (sat is not None and sat < 0.04 and value == "dark"):
        pick = "mono_silver" if value != "bright" else "clean709"     # a white, almost colourless look stays clean
    elif _has_words(filters, "sepia"):
        pick = "archive_sepia"
    elif value == "dark":
        pick = "bleach_bypass" if (sat or 0) < 0.25 else "teal_steel" if _has_words(filters, "teal", "cool", "blue") \
            else "tungsten_night"
    elif value == "bright":
        pick = "clean709"
    elif _has_words(filters, "warm", "golden", "amber"):
        pick = "kodachrome_warm"
    else:
        pick = "natural"
    if plan.candid_photo and pick in ("kodachrome_warm", "tungsten_night", "teal_steel"):
        # a phone photo is never graded like film; the phone-camera finish carries its look
        filters = "an everyday phone photo, never graded like film"
        pick = "natural"
    plan.set("look.fx.grade", pick, area="Look", label="Colour grade", show=GRADE_WORDS.get(pick, pick),
             why=f"{value} picture (brightness {round(luma) if luma is not None else '?'}/255)"
                 + (f", {grade['filter'][0]}" if grade.get("filter") else ""), source=",".join(grade.get("from", [])),
             confidence=grade.get("confidence"))
    # always written: a base recipe's own film finish (a soft vignette, a film weave) darkens and roughens a channel
    # whose references are clean, such as flat white drawings
    measured_grain = (look.get("grain") or {}).get("measured") or 0
    drawn = float(_v(look.get("drawn"), 0) or 0) >= 0.4
    finish = {"weave": grain == "heavy" or (grain == "light" and measured_grain >= 1.0 and not drawn),
              "vignette": "soft" if _has_words(filters, "vignette") else "none"}
    seen = [k for k in ("grain" if finish["weave"] else "", "vignette" if finish["vignette"] != "none" else "") if k]
    plan.set("look.fx.finish", finish, area="Look", label="Film finish", show=", ".join(seen) or "clean",
             why=("the references show " + " and ".join(seen)) if seen else "the references have no grain or vignette",
             source="watched")
    palette = [c["hex"] for c in _v(look.get("palette"), []) or [] if isinstance(c, dict) and c.get("hex")]
    accents = [h for h in palette if _saturation(h) >= 0.35 and _value(h) >= 0.35][:3]
    if accents:
        plan.set("look.accent", accents, area="Look", label="Accent colours", show={"colours": accents},
                 why="the most saturated colours of the measured palette", source="measured")


def _value(hex_color: str) -> float:
    try:
        return max(int(hex_color[i:i + 2], 16) for i in (1, 3, 5)) / 255
    except (ValueError, TypeError):
        return 0.0


def _saturation(hex_color: str) -> float:
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (ValueError, TypeError):
        return 0.0
    hi, lo = max(r, g, b), min(r, g, b)
    return 0.0 if hi == 0 else (hi - lo) / hi


def _text(plan: Plan, profile: dict):
    text = profile.get("text") or {}
    density = _v(text.get("density"), {}) or {}
    lettering = _v(text.get("lettering"), "")
    captions = density.get("caption", 0)
    if captions < 0.3:
        plan.set("look.captions.off", True, area="Text", label="Subtitles",
                 why=f"the references show subtitles on {round(captions * 100)}% of the runtime", source="watched",
                 show="off")
    else:
        plan.set("look.captions.off", False, area="Text", label="Subtitles",
                 why=f"subtitles on {round(captions * 100)}% of the runtime", source="watched", show="on")
    if lettering == "comic":
        # bold comic-book display letters for words on screen, an easy-to-read hand for subtitles
        plan.set("look.textfx.fonts.head", "Bangers", area="Text", label="Lettering",
                 why="the references letter their words like a comic book", source="watched", show="comic")
        plan.set("look.captions.font", "PatrickHand", area="Text", label="Subtitle lettering",
                 why="hand lettering that stays easy to read", source="watched", show="hand-lettered")
    elif lettering == "hand-drawn" or (lettering == "mixed" and density.get("handwritten", 0) >= 0.25):
        plan.set("look.textfx.fonts.head", "Caveat", area="Text", label="Lettering",
                 why="the references letter their words by hand", source="watched", show="hand-drawn")
        plan.set("look.captions.font", "PatrickHand", area="Text", label="Subtitle lettering",
                 why="the references letter their words by hand", source="watched", show="hand-drawn")
    style = _v(text.get("style"), []) or []
    if style:
        plan.notes.append({"for": "text", "text_style": style[:2], "graphics": (_v(text.get("graphics"), []) or [])[:6]})


def _features(plan: Plan, profile: dict, base: str, base_style: dict):
    fam = _families(profile)
    sources = _v(_get(profile, "pictures", "sources"), {}) or {}
    graphics = " ".join(_v(_get(profile, "text", "graphics"), []) or []).lower()
    want = {
        "youtube": fam.get("footage", 0) - sources.get("stock_footage", 0) >= 0.1,
        "stock": sources.get("stock_footage", 0) >= 0.05,
        "ai_images": fam.get("generated", 0) + fam.get("generated_motion", 0) >= 0.1,
        "maps": sources.get("map", 0) >= 0.02 or "map" in graphics,
        "headlines": sources.get("screenshot_web", 0) + sources.get("document", 0) >= 0.05
                     or _has_words(graphics, "headline", "article", "newspaper"),
        "motion": fam.get("graphic", 0) >= 0.1,
        "threed": fam.get("3d", 0) >= 0.2,
        "avatar": _presenter_kind(profile) == "talking",
        "sound": True,
        "depth": plan.motion_cutout,
    }
    offered = list((base_style.get("features") or {}).get("offered") or [])
    on = [k for k, v in want.items() if v]
    reasons = {"youtube": "real footage", "stock": "stock footage", "ai_images": "generated pictures", "maps": "maps",
               "headlines": "articles and screenshots", "motion": "motion graphics", "threed": "3D scenes",
               "avatar": "a presenter on camera", "sound": "sound design", "depth": "cut-out movement"}
    plan.set("features.on", on, area="Options", label="Switched on",
             why="what the references show: " + ", ".join(reasons[k] for k in on), source="watched",
             show=", ".join(reasons[k] for k in on))
    plan.set("features.offered", list(dict.fromkeys(offered + on)), area="Options", label="Offered",
             why="the base recipe's options plus everything this channel uses", source="")
    # Options starts from these ticks; a creator's own change in Options still wins
    plan.overrides["features"]["channel_on"] = list(on)
    plan.drop("features.auto_own", "the learned channel decides its own switches")
    if base == "documentary" and fam:
        # the middle share is every footage slot: YouTube footage and stock share them (YouTube first), so all the
        # watched footage counts there, not only the stock part of it
        ai = round((fam.get("generated", 0) + fam.get("generated_motion", 0)) * 100)
        footage = round(fam.get("footage", 0) * 100)
        gfx = round(fam.get("graphic", 0) * 100)
        total = max(1, ai + footage + gfx)
        mix = [round(ai * 100 / total), round(footage * 100 / total), 0]
        mix[2] = 100 - mix[0] - mix[1]
        plan.set("pacing.mix", mix, area="Options", label="Picture mix (AI, footage, graphics)",
                 why="the share of each in the watched shots", source="watched", show=f"{mix[0]} / {mix[1]} / {mix[2]}")
    if fam.get("graphic", 0) >= 0.15 and base in ("stories", "avatar-premium"):
        plan.set("pacing.graphics", True, area="Options", label="Graphics between pictures",
                 why=f"{round(fam['graphic'] * 100)}% of the runtime is charts, maps or graphics", source="watched")
        plan.set("pacing.graphic_ratio", round(_clamp(fam["graphic"], 0.05, 0.5), 2), area="Options",
                 label="Graphics share", why="measured from the watched shots", source="watched")


def _real_clips(plan: Plan, profile: dict, base: str):
    share = float(_v(_get(profile, "edit", "source_clips"), 0) or 0)
    if base not in ("documentary", "nocturne", "jung"):
        return
    # a channel that opens on a real clip (a cold broadcast clip, raw bodycam) keeps its first seconds for footage,
    # however little of the rest plays with its own sound
    hook = " ".join(_v(_get(profile, "edit", "hook"), []) or []).lower()
    if share >= 0.3 or _has_words(hook, "clip", "footage", "bodycam", "raw", "broadcast", "news"):
        plan.set("pacing.cold_open_s", 6.0, area="Edit", label="Open on a real clip",
                 why="the references open on raw footage before the narration", source="watched")
    if share < 0.15:
        return
    quotes = int(_clamp(round(share * 24), 2, 16))      # about two clips per minute of a 10-minute video at 50%
    plan.set("look.soundbites", {"quotes": quotes, "max_s": 20.0, "soundups": 3, "montage": True}, area="Edit",
             label="Real clips with their own sound",
             why=f"real footage plays with its own sound {round(share * 100)}% of the runtime", source="watched",
             show=f"about {quotes} clips, up to 20 s each, opening on raw moments")


def _sound(plan: Plan, profile: dict):
    sound = profile.get("sound") or {}
    music = sound.get("music") or {}
    level = music.get("level")
    bed = music.get("bed_in_pauses_db")
    snd = {}
    if level == "none":
        snd.update(music=False)
        why = "the references' pauses have no music (measured)"
    elif level in ("score", "bed", "quiet"):
        # the engine's bed level is against the narration's own loudness, a few dB under its peak. Music heard in
        # only some pauses is set from its level where it plays: the median over every pause is mostly silence then
        # (a recap channel read -43 dB, and a bed set from that was too quiet to hear in 96% of the pauses)
        heard, fill = music.get("bed_when_heard_db"), music.get("filled_pauses")
        intermittent = fill is not None and fill < 0.6
        if intermittent and heard is not None:
            at = heard
        elif intermittent:
            at = max(bed if bed is not None else -20, -34.0)   # profiles measured before the level where it plays
        else:
            at = bed if bed is not None else -20
        rel = round(_clamp(at + 4, -38, -4), 1)
        snd.update(music=True, music_rel_db=rel, duck_db=-6.0 if level == "score" else -8.0)
        why = f"music in the pauses about {abs(bed or 0):.0f} dB under the voice (measured)"
    else:
        why = "music level couldn't be measured; the recipe's own level is kept"
    if level in ("score", "bed", "quiet"):
        # music written for each video on the creator's Kie account, in the channel's own sound (cr_score)
        snd["score"] = {"genres": [g for g in (music.get("genres") or []) if g and g.lower() != "none"][:2],
                        "moods": [m for m in (music.get("moods") or [])][:5],
                        "instruments": [i for i in (music.get("instruments") or [])][:5]}
        if music.get("beat") in ("none", "soft", "driving"):
            snd["score"]["beat"] = music["beat"]
    moods = " ".join((music.get("moods") or []) + (music.get("genres") or [])).lower()
    vibe = _vibe(moods) if snd.get("music") is not False else ""
    if vibe:
        snd["vibe"] = vibe
    sfx = _v(sound.get("sfx"), "")
    snd["sfx_db"] = {"none": -60.0, "sparse": -24.0, "moderate": -19.0, "dense": -16.0}.get(sfx, -19.0)
    if sfx in ("sparse", "moderate", "dense"):
        snd["library"] = True                       # ChannelRecipe's recorded effects instead of synthesised ones
        snd["density"] = sfx                        # how often a picture's action gets its sound
    kinds = " ".join(_get(sound, "sfx", "kinds", default=[]) or []).lower()
    snd["sfx_flavor"] = ("analog_paper" if _has_words(kinds, "paper", "marker", "pen", "chalk", "pop") else
                         "tech_digital" if _has_words(kinds, "click", "ui", "glitch", "digital", "keyboard") else
                         "cinematic_subtle")
    if bed is not None and music.get("filled_pauses") is not None:
        # what the references measure, kept so every video's sound check can say how close it came (cr_sound_qc)
        snd["target"] = {"bed_in_pauses_db": round(float(bed), 1), "pauses_filled": round(float(music["filled_pauses"]), 2)}
    amb = sound.get("ambience") or {}
    if amb.get("present") and _v(amb, []):
        # the air of each place under the pictures (cr_ambience): heard throughout, the channel's usual place fills
        # the gaps; heard in places only, it plays only where a picture shows a place
        # no default place: what a reference was heard to have belongs to its story (a prison story's corridor);
        # each picture brings its own place
        snd["ambience"] = {"scenes": True, "db": -14.0 if snd.get("music") is False else -18.0, "default": ""}
        why += f"; the sound of each place under the pictures ({', '.join(_v(amb, [])[:3])})"
    plan.set("look.sound", snd, area="Sound", label="Music and effects",
             why=why + f"; effects {sfx or 'unknown'}" + (f"; mood {vibe.replace('_', ' ')}" if vibe else ""),
             source="measured,watched",
             show=("no music" if snd.get("music") is False else
                   f"music {snd.get('music_rel_db', 'as recipe')} dB" if snd.get("music") else "recipe default")
                  + f", effects {sfx or 'default'}" + (", the sound of each place" if snd.get("ambience") else ""))
    plan.notes.append({"for": "sound", "music_level": level, "bed_in_pauses_db": bed,
                       "genres": music.get("genres") or [], "moods": (music.get("moods") or [])[:6],
                       "instruments": (music.get("instruments") or [])[:6], "sfx": sfx,
                       "sfx_kinds": (_get(sound, "sfx", "kinds", default=[]) or [])[:8],
                       "ambience": _v(sound.get("ambience"), []) or []})


def _vibe(moods: str) -> str:
    table = [(("true crime", "crime", "murder"), "true_crime_dark"),
             (("investig", "tense", "suspense", "foreboding"), "tense_investigation"),
             (("ominous", "eerie", "mysterious", "mystery", "dark"), "mystery_drone"),
             (("lofi", "lo-fi", "nostalg", "retro", "chill", "laid"), "nostalgic_retro"),
             (("epic", "historical", "heroic", "orchestral"), "epic_history"),
             (("tech", "synth", "electronic", "driving", "analytical"), "tech_pulse"),
             (("uplift", "inspir", "hopeful", "curious", "wonder"), "uplifting_science"),
             (("business", "corporate", "upbeat", "money"), "business_rise_fall"),
             (("sombre", "somber", "reflective", "melanchol", "contemplat"), "serious_documentary")]
    for words, vibe in table:
        if any(w in moods for w in words):
            return vibe
    return ""


def _voice(plan: Plan, profile: dict):
    v = _v(profile.get("voice"), {}) or {}
    gender = (v.get("gender") or "").lower()
    gender = "female" if gender.startswith("f") else "male" if gender.startswith("m") else ""
    if not gender:
        return
    words = " ".join(str(v.get(k) or "") for k in ("age", "tone", "pace")).lower()
    pick = next(((m if gender == "male" else f) for keys, m, f in _TONE_VOICE if any(k in words for k in keys)),
                "Charon" if gender == "male" else "Kore")
    plan.set("voice.suggested", pick, area="Voice", label="Narrator",
             why=f"{v.get('age') or ''} {gender} voice, {v.get('tone') or 'neutral'} tone, {v.get('pace') or ''} pace"
                 .replace("  ", " ").strip(), source="watched", show=f"{pick} ({VOICES[pick][1]})")
    wpm = _get(profile, "voice", "wpm")
    if wpm:
        plan.set("pacing.wpm", float(_clamp(wpm, 110, 210)), area="Voice", label="Speaking pace",
                 why=f"the references speak about {wpm} words a minute (captions)", source="measured",
                 show=f"{wpm} words a minute")


def _presenter(plan: Plan, profile: dict, base: str, base_style: dict):
    if base != "avatar-premium":
        if _presenter_kind(profile) == "mascot":
            who = (_get(profile, "format", "presenter", "who", default=[]) or [{}])[0]
            plan.notes.append({"for": "cast", "mascot": who})
            plan.needs.append("character_image")
        return
    share = float(_v(_get(profile, "format", "presenter"), 0) or 0)
    per_min = round(_clamp(share * 60, 3.0, 24.0), 1)
    avatar = copy.deepcopy((base_style.get("look") or {}).get("avatar") or {})
    avatar["per_min"] = per_min
    plan.set("look.avatar", avatar, area="Presenter", label="Presenter on camera",
             why=f"the presenter talks to camera about {round(share * 100)}% of the time", source="watched",
             show=f"{per_min:g} s a minute (about {round(per_min / 60 * 100)}%, ${per_min * PRESENTER_USD_S:.2f} a minute)")
    plan.needs.append("presenter")


def _cta(plan: Plan, profile: dict, base: str):
    cta = _get(profile, "format", "cta", default={}) or {}
    kinds = set(cta.get("kinds") or [])
    own = kinds & {"own product", "ebook or pdf", "course", "qr code", "link"}
    if not own:
        return
    plan.set("look.offer", {"mention": True, "cta": True, "end_line": True, "at": [0.4, 0.7], "slug": ""},
             area="Calls to action", label="Promote your product",
             why="the references promote " + ", ".join(sorted(own)) + " during the video", source="watched",
             show="twice, around 40% and 70%, with its cover and QR code")
    plan.needs.append("product")


def answer(profile: dict, answers: dict) -> dict:
    """The profile as the creator corrected it in the review (a copy; the measured profile is kept as it was)."""
    p = copy.deepcopy(profile)
    answers = answers if isinstance(answers, dict) else {}
    pics, fmt = p.setdefault("pictures", {}), p.setdefault("format", {})
    if isinstance(answers.get("art_style"), str) and answers["art_style"].strip():
        art = pics.setdefault("art_style", {})
        art.update(value=answers["art_style"].strip(), consistent=True, confidence=1.0, **{"from": ["asked"]})
    host = str(answers.get("host") or "")
    chars = (pics.get("characters") or {}).get("value") or []
    if host.startswith("Yes") and not any(c.get("role") in ("mascot", "presenter") for c in chars):
        import cr_style_profile
        lead = cr_style_profile.lead_character(p)
        if lead:                                     # the character who carries the video becomes the channel's mascot
            lead.update(role="mascot", promoted=True)
    if host.startswith("No"):
        chars = pics.get("characters") or {}
        chars["value"] = [c for c in chars.get("value") or [] if c.get("role") not in ("mascot", "presenter")]
        presenter = fmt.get("presenter") or {}
        presenter.update(value=0.0, who=[])
    if str(answers.get("cta") or "") == "No":
        fmt["cta"] = {"value": [], "kinds": [], "confidence": 1.0, "from": ["asked"]}
    music = str(answers.get("music") or "")
    if music:
        level = "score" if music.startswith("A story score") else "bed" if music.startswith("Light") else "none"
        snd = p.setdefault("sound", {}).setdefault("music", {})
        snd.update(level=level, confidence=1.0, value="story score" if level == "score" else
                   "background music" if level == "bed" else "none",
                   bed_in_pauses_db=-16 if level == "score" else -28 if level == "bed" else None)
    grade = str(answers.get("grade") or "")
    if grade:
        g = p.setdefault("look", {}).setdefault("grade", {})
        g.update(value="dark" if grade.startswith("Dark") else "bright" if grade.startswith("Bright") else "neutral",
                 confidence=1.0)
    return p


def plan_style(profile: dict, styles: dict, *, base: str = None, minutes_hint: float = 10.0,
               answers: dict = None) -> dict:
    """The mapping for one profile. `styles` is the engine's installed styles {name: style dict}."""
    if answers:
        profile = answer(profile, answers)
        if str(answers.get("host") or "").startswith("Yes, I'll upload"):
            profile.setdefault("_wants", []).append("character_upload")
    chosen, why = (base, "chosen by you") if base else choose_base(profile, styles)
    if chosen not in styles:
        raise ValueError(f"The {chosen} recipe isn't installed here.")
    base_style = styles[chosen]
    plan = Plan()
    _pictures(plan, profile, chosen, base_style)
    _pace(plan, profile, chosen)
    _motion(plan, profile, chosen, minutes_hint)
    _look(plan, profile)
    _text(plan, profile)
    _features(plan, profile, chosen, base_style)
    _real_clips(plan, profile, chosen)
    _sound(plan, profile)
    _voice(plan, profile)
    _presenter(plan, profile, chosen, base_style)
    _cta(plan, profile, chosen)
    return {"schema": SCHEMA, "base": chosen, "base_why": why, "base_for": BASES.get(chosen, ""),
            "overrides": plan.overrides, "remove": plan.remove, "settings": plan.settings,
            "needs": list(dict.fromkeys(plan.needs)), "notes": plan.notes,
            "cost": cost(plan.overrides, chosen), "questions": list(profile.get("questions") or [])}


def cost(overrides: dict, base: str) -> dict:
    """A rough cost per minute of finished video from the pictures, moving pictures and presenter."""
    pacing = overrides.get("pacing") or {}
    every = pacing.get("photo_every_s")
    stills = (60.0 / every) if every else (2.5 if "ai_images" in ((overrides.get("features") or {}).get("on") or [])
                                         else 0.0)
    clips = pacing.get("clips_per_min") or 0.0
    presenter = ((overrides.get("look") or {}).get("avatar") or {}).get("per_min") or 0.0
    usd = stills * PICTURE_USD + clips * CLIP_USD + presenter * PRESENTER_USD_S
    return {"pictures_per_min": round(stills, 1), "clips_per_min": clips, "presenter_s_per_min": presenter,
            "usd_per_min": round(usd, 2),
            "note": "pictures, moving pictures and presenter on your Kie account; voice and music are extra"}


def _merge(dst: dict, src: dict):
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict) and key not in ("avatar", "soundbites"):
            _merge(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)


def apply(style: dict, mapping: dict) -> dict:
    """A copy of `style` with the mapping's overrides merged in and the base's own assets removed."""
    out = copy.deepcopy(style)
    for row in mapping.get("remove") or []:
        keys = row["path"].split(".")
        cur = out
        for key in keys[:-1]:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if isinstance(cur, dict):
            cur.pop(keys[-1], None)
    overrides = copy.deepcopy(mapping.get("overrides") or {})
    voice = overrides.pop("voice", {})
    _merge(out, overrides)
    if voice.get("suggested"):
        out.setdefault("cr_style_clone", {})["suggested_voice"] = voice["suggested"]
    if out.get("look", {}).get("placement") == "":
        out["look"].pop("placement", None)
    return out
