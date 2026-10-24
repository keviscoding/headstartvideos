"""Animated 2D characters: a channel's drawn cast rigged as vector puppets and animated in code.

A @DartAnimates clone (stick-figure fights animated frame by frame) came out as AI stills with camera moves: the look
was right and nothing moved. Channels like it are made here instead, the 2D sibling of the designed 3D cast:

- casting: Claude designs every character the script needs as a puppet (body colour, hair, blindfold or hat,
  markings, gloves, a weapon, four arms...) in the channel's own conventions, and the places the story happens;
- plates: one background picture per place, drawn in the channel's look with nobody in it;
- direction: Claude choreographs the narration, shot by shot, from a fixed library of moves and effects, timed to
  the words as the voice says them;
- rendering: the kit (rig, moves, effects, camera; Studio's animated2d/ folder) draws every frame in Chromium, and
  each strike, dash and blast leaves its sound mark for Studio's sound design.

The engine runs it as a visuals plugin (look.visuals = "cr_animated2d"): voice, captions, music and the final mix
stay the engine's own. Nothing here is substituted silently: a missing kit, a broken plan or a failed render stops
the video with the reason, and saved work is reused on the next attempt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = "animated2d"                     # the kit's folder in the Studio (private: shipped by the release builder)
KIT_FILES = ("rig.js", "draw.js", "moves.js", "fx.js", "film.js")
PROMPTS = ("cast.txt", "director.txt")
VISUALS = "cr_animated2d"
SCHEMA = 1
FPS = 30
CHUNK_S = 60.0                         # narration a director call choreographs at once (at most)
PLATE_USD = 0.02                       # one background plate on the creator's Kie account
SAME_LOOK = ("STYLE REFERENCE: the reference picture is another place in this same animation. Draw the new place in "
             "exactly its drawing style: the same line weight, flat colours, palette, light, blur and level of detail. "
             "Do not copy its layout. ")
SOUNDS = "file:sfx/animated2d/"        # the kit's fight sounds, installed under assets/sfx/animated2d
SOUND_FILES = {"punch": "punch", "punch_heavy": "punch_heavy", "kick": "kick", "sub_hit": "sub_hit", "block": "block",
               "dash": "dash", "jump": "jump", "land": "land", "body_fall": "body_fall", "explosion": "explosion",
               "beam": "beam", "energy_throw": "energy_throw", "charge": "charge", "charge_short": "charge_short",
               "roar_boom": "roar_boom", "teleport": "teleport", "cast": "cast", "cloth": "cloth", "whoosh": "whoosh"}

MOVES = ("place", "enter", "exit", "stand", "guard", "walk", "run", "dash", "teleport", "jump", "float", "jab", "cross",
         "hook", "uppercut", "kick", "high_kick", "fly_kick", "slam", "slash", "combo", "block", "dodge", "charge",
         "blast", "orb", "sign", "point", "cross_arms", "victory", "kneel", "get_up", "hit", "hit_body", "knockback",
         "launched")
FX = ("flash", "shake", "focus", "aura", "lightning", "impact_frame")
FRAMINGS = ("wide", "two", "close", "medium")
CAMERA = ("static", "push", "pull", "pan", "crash")
HAIR = ("none", "short", "spiky_up", "spiky_back", "messy", "mohawk", "long", "ponytail", "bun", "afro", "side_swept")
EYES = ("none", "blindfold", "bandage", "dots", "angry", "glare", "glasses", "shades", "visor")
HEADWEAR = ("none", "fedora", "cap", "headband", "crown", "horns", "hood", "helmet")
MARKS = ("cheek_stripes", "forehead_line", "extra_eyes", "scar", "mask", "stripe")
WEAPONS = ("none", "sword", "katana", "staff")

STICK = re.compile(r"\b(stick[- ]?(figures?|m[ae]n|people|nodes|animation|fight\w*)|stickm[ae]n|pivot|limb puppets?|"
                   r"jointed (silhouettes?|figures?))\b", re.I)
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
COLOUR_WORDS = [("teal", "#3fb59e"), ("cyan", "#4cc3d9"), ("turquoise", "#3fbfb0"), ("mint", "#7fe0b0"),
                ("green", "#5cbf62"), ("lime", "#9ccc3c"), ("red", "#e0625a"), ("crimson", "#c8323c"),
                ("salmon", "#ee8a78"), ("pink", "#ee8fb0"), ("magenta", "#d04fa8"), ("purple", "#8e6bd0"),
                ("lavender", "#a9a3d6"), ("violet", "#8a5fd0"), ("blue", "#4f86d8"), ("navy", "#2e4a8a"),
                ("orange", "#ef8a3c"), ("yellow", "#efc93c"), ("gold", "#d9a93c"), ("brown", "#8a5a3c"),
                ("white", "#eeeeee"), ("black", "#1d1d1f"), ("grey", "#9a9da3"), ("gray", "#9a9da3")]


class AnimationStopped(RuntimeError):
    """The animated video stopped for a reason the creator can read; its saved work is kept."""


# ── the kit ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def kit_dir(home=None) -> Path:
    return Path(home or HERE) / KIT


def available(home=None) -> bool:
    """The kit is installed in this Studio (Studio 102 and newer ship it)."""
    folder = kit_dir(home)
    return all((folder / f).is_file() for f in KIT_FILES + PROMPTS)


def kit_js(home=None) -> str:
    folder = kit_dir(home)
    return "".join((folder / f).read_text(encoding="utf-8") + "\n" for f in KIT_FILES)


def _prompt(name: str, home=None) -> str:
    path = kit_dir(home) / name
    if not path.is_file():
        raise AnimationStopped("Studio's animated 2D kit is missing. Update Studio before making this video.")
    return path.read_text(encoding="utf-8")


# ── from a learned profile ──────────────────────────────────────────────────────────────────────────────────────────

def _v(axis, default=None):
    return axis.get("value", default) if isinstance(axis, dict) else default


def _words(profile: dict) -> str:
    pics = profile.get("pictures") or {}
    art = pics.get("art_style") or {}
    parts = [str(_v(art, "") or "")] + [str(x) for x in (art.get("alternatives") or [])]
    parts += [str(a.get("look") or "") for a in (art.get("looks") or [])]
    parts += [str(x) for x in (profile.get("recognisable") or [])]
    parts += [str(c.get("look") or "") for c in (_v(pics.get("characters"), []) or []) if isinstance(c, dict)]
    return ". ".join(p for p in parts if p)


def fits(profile: dict) -> tuple:
    """(True, what was seen) when the channel's drawn characters are the kind the kit rigs: stick figures (round heads,
    limbs as thick strokes), whatever they wear; else (False, "")."""
    sources = _v((profile.get("pictures") or {}).get("sources"), {}) or {}
    stick = float(sources.get("stick_figure") or 0)
    words = _words(profile)
    seen = [m.group(0).lower() for m in STICK.finditer(words)]
    if stick >= 0.25 or seen:
        named = [w for w in dict.fromkeys(x.replace("-", " ") for x in seen) if w not in ("stick figure", "stick figures")]
        return True, "stick figures" + (f": {', '.join(named[:2])}" if named else "")
    return False, ""


def colour_of(text: str, default: str = "") -> str:
    """The first colour a description names, as #rrggbb ("red stick figure" -> a flat red)."""
    text = str(text or "")
    m = HEX.search(text)
    if m:
        return m.group(0).lower()
    low = text.lower()
    hits = [(low.find(w), hexv) for w, hexv in COLOUR_WORDS if re.search(r"\b" + w + r"\b", low)]
    return min(hits)[1] if hits else default


def _grey(hexv: str) -> float:
    try:
        return sum(int(hexv[i:i + 2], 16) for i in (1, 3, 5)) / 765
    except (ValueError, TypeError):
        return 0.5


def _darker(hexv: str, k: float) -> str:
    try:
        return "#" + "".join(f"{int(int(hexv[i:i + 2], 16) * (1 - k)):02x}" for i in (1, 3, 5))
    except (ValueError, TypeError):
        return hexv


def _chroma(hexv: str) -> float:
    try:
        r, g, b = (int(hexv[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (ValueError, TypeError):
        return 0.0
    return max(r, g, b) - min(r, g, b)


def from_profile(profile: dict) -> dict:
    """The channel's animation conventions as the kit reads them (look.animated2d): figure size, background blur,
    outlines, impact frames, its palette, and its recurring characters in words."""
    words = _words(profile)
    low = words.lower()
    palette = [c.get("hex") for c in (_v((profile.get("look") or {}).get("palette"), []) or [])
               if isinstance(c, dict) and HEX.fullmatch(str(c.get("hex") or ""))]
    # the stand-in sky and ground come from the palette's near-neutral colours (a fighter's red is no sky)
    greys = sorted([h for h in palette if _chroma(h) < 0.12], key=_grey)
    light = [h for h in greys if 0.5 <= _grey(h) <= 0.92]
    dark = [h for h in greys if 0.2 <= _grey(h) < 0.5]
    light = (light * 2)[-2:] if light else ["#9aa1aa", "#c3c7cc"]
    dark = (dark * 2)[-2:] if dark else ["#474b51", "#5c6067"]
    if dark[0] == dark[1]:
        dark = [_darker(dark[0], 0.25), dark[1]]
    cast = []
    for c in _v((profile.get("pictures") or {}).get("characters"), []) or []:
        if isinstance(c, dict) and c.get("look"):
            cast.append({"name": str(c.get("name") or "")[:40], "look": str(c["look"])[:240],
                         "role": str(c.get("role") or "character"), "color": colour_of(c["look"])})
    return {"schema": SCHEMA, "art": str(_v((profile.get("pictures") or {}).get("art_style"), "") or "")[:400],
            "figure": 0.36, "blur": 5 if re.search(r"blur", low) else 2,
            "outline": {"color": "#141414", "width": 0.012} if re.search(r"\boutlin", low) else None,
            "impact_frames": bool(re.search(r"impact frame|monochrome (impact|flash)|high-contrast", low)) or True,
            "sky": [light[0], light[-1]], "floor": [dark[-1], dark[0]], "palette": palette[:8],
            "effects": [m.group(0) for m in re.finditer(r"(neon |glowing |energy )?(auras?|speed lines|smears?|"
                                                          r"motion blur|impact frames?|lightning|sparks|blood)", low)][:6],
            "cast": cast[:6]}


def puppet_from_words(name: str, look: str, colour: str) -> dict:
    """A recurring character the channel was watched to have ("Red stick figure with boxing gloves", "White stick
    figure wearing a fedora with a black band") as a puppet, from its words."""
    low = str(look or "").lower()
    hair_colour = re.search(r"(\w+)[- ](spiky |long |short )?hair", low)
    p = {"name": name or "lead", "color": colour_of(look, colour)}
    if re.search(r"spik", low):
        p["hair"] = {"style": "spiky_up", "color": colour_of(hair_colour.group(1) if hair_colour else "", "#f4f6f8")}
    elif re.search(r"ponytail", low):
        p["hair"] = {"style": "ponytail", "color": colour_of(hair_colour.group(1) if hair_colour else "", "#2a2a2a")}
    elif re.search(r"long hair", low):
        p["hair"] = {"style": "long", "color": colour_of(hair_colour.group(1) if hair_colour else "", "#2a2a2a")}
    for word, kind in (("fedora", "fedora"), ("cap", "cap"), ("headband", "headband"), ("crown", "crown"), ("horn", "horns"),
                       ("hood", "hood"), ("helmet", "helmet")):
        if re.search(r"\b" + word, low):
            p["headwear"] = {"kind": kind, "color": "#f2f2f2" if kind == "fedora" else "#cc2a2a", "band": "#151515"}
            break
    for word, kind in (("blindfold", "blindfold"), ("glasses", "glasses"), ("shades", "shades"), ("sunglasses", "shades")):
        if word in low:
            p["eyes"] = {"style": kind, "color": "#151515"}
    if "glove" in low:
        p["gloves"] = {"color": "#1d1d1f"}
    if "boot" in low:
        p["boots"] = {"color": "#2e6fb5" if "blue" in low else "#1d1d1f"}
    for word in ("sword", "katana", "staff"):
        if word in low:
            p["weapon"] = {"kind": word}
    for part in ("scarf", "cape", "belt"):
        if part in low:
            p[part] = {"color": "#c8323c"}
    return p


def test_film(spec: dict) -> dict:
    """The likeness check's test shots, drawn by the kit in the channel's conventions: two of its characters squaring
    up (wide), a punch landing (two shot), a power-up (close). The place is the kit's drawn stand-in in the channel's
    palette: a video draws its own backgrounds."""
    cast_words = [c for c in spec.get("cast") or [] if c.get("look")]
    palette = [h for h in spec.get("palette") or [] if _grey(h) not in (0, 1)] + ["#e0625a", "#3fb59e"]
    a = puppet_from_words(cast_words[0]["name"] if cast_words else "a", cast_words[0]["look"] if cast_words else "", "#e0625a")
    b = puppet_from_words(cast_words[1]["name"] if len(cast_words) > 1 else "b", cast_words[1]["look"] if len(cast_words) > 1
                          else "", "#3fb59e" if a["color"] != "#3fb59e" else "#4f86d8")
    if b["color"] == a["color"]:
        b["color"] = next((h for h in palette if h != a["color"]), "#4f86d8")
    cast = clean_cast({"a": a, "b": b})
    return {"duration": 6.0, "style": {"figure": spec.get("figure", 0.36), "blur": spec.get("blur", 5),
                                       "sky": spec.get("sky"), "floor": spec.get("floor"), "outline": spec.get("outline")},
            "cast": cast, "sets": {"place": {}},
            "shots": [{"start": 0, "end": 1.5, "set": "place", "framing": "wide", "move": "static", "cast": []},
                      {"start": 1.5, "end": 3.6, "set": "place", "framing": "two", "move": "static", "cast": []},
                      {"start": 3.6, "end": 6, "set": "place", "framing": "close:a", "move": "static", "cast": []}],
            "actions": [{"at": 0, "who": "a", "move": "place", "x": -1.6, "face": "right"},
                        {"at": 0, "who": "b", "move": "place", "x": 1.6, "face": "left"},
                        {"at": 1.6, "who": "a", "move": "dash", "target": "b"},
                        {"at": 2.05, "who": "a", "move": "cross", "target": "b"},
                        {"at": 3.7, "who": "a", "move": "charge", "dur": 2.2}],
            "stills": [0.9, 2.27, 5.4]}


def render_stills(spec: dict, out_dir: Path, *, home=None, prefix: str = "r0_") -> list:
    """The film's stills at spec["stills"] seconds, as JPEGs: [paths]."""
    from playwright.sync_api import sync_playwright
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    W, H = spec.get("width", 1920), spec.get("height", 1080)
    tmp = Path(tempfile.mkdtemp(prefix="cr_a2d_"))
    try:
        html = tmp / "film.html"
        html.write_text(page(spec, home), encoding="utf-8")
        out = []
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-gpu", "--force-color-profile=srgb"])
            pg = browser.new_page(viewport={"width": W, "height": H})
            pg.goto(html.as_uri())
            pg.wait_for_function("window.__ready===true", timeout=120000)
            err = pg.evaluate("window.__err")
            if err:
                raise AnimationStopped("The animated 2D kit could not draw the test shots: " + str(err)[:300])
            for k, t in enumerate(spec.get("stills") or []):
                dest = out_dir / f"{prefix}{k}.jpg"
                pg.evaluate("(t)=>window.renderFrame(t)", float(t))
                pg.screenshot(path=str(dest), type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": W, "height": H})
                out.append(dest)
            browser.close()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── checking what Claude wrote ──────────────────────────────────────────────────────────────────────────────────────

def _json(text: str):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [0])
    try:
        return json.loads(text[start:])
    except ValueError:
        end = max(text.rfind("}"), text.rfind("]"))
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            raise AnimationStopped("Claude's animation plan was incomplete. Its answer is saved; nothing else was "
                                   "drawn in its place.") from None


def _hex(value, default):
    value = str(value or "").strip()
    return value.lower() if HEX.fullmatch(value) else colour_of(value, default)


def _one(value, allowed, default):
    value = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return value if value in allowed else default


def clean_cast(raw) -> dict:
    """{id: puppet} with only what the kit draws; ids are short lowercase words."""
    out = {}
    rows = raw.items() if isinstance(raw, dict) else ((r.get("id"), r) for r in raw or [] if isinstance(r, dict))
    for key, c in rows:
        if not isinstance(c, dict):
            continue
        ident = re.sub(r"[^a-z0-9_]", "", str(key or c.get("name") or "").lower().replace(" ", "_"))[:24]
        if not ident or ident in out:
            continue
        hair = c.get("hair") if isinstance(c.get("hair"), dict) else {"style": c.get("hair")}
        eyes = c.get("eyes") if isinstance(c.get("eyes"), dict) else {"style": c.get("eyes")}
        hw = c.get("headwear") if isinstance(c.get("headwear"), dict) else {"kind": c.get("headwear")}
        wp = c.get("weapon") if isinstance(c.get("weapon"), dict) else {"kind": c.get("weapon")}
        p = {"name": str(c.get("name") or ident)[:40], "color": _hex(c.get("color"), "#e8e8e8"),
             "hair": {"style": _one(hair.get("style"), HAIR, "none"), "color": _hex(hair.get("color"), "#222222")},
             "eyes": {"style": _one(eyes.get("style"), EYES, "none"), "color": _hex(eyes.get("color"), "#151515")},
             "headwear": {"kind": _one(hw.get("kind"), HEADWEAR, "none"), "color": _hex(hw.get("color"), "#f2f2f2"),
                          "band": _hex(hw.get("band"), "#151515")},
             "marks": [{"kind": _one(m.get("kind") if isinstance(m, dict) else m, MARKS, ""),
                        "color": _hex((m or {}).get("color") if isinstance(m, dict) else "", "#1a1a1a")}
                       for m in (c.get("marks") or [])[:4]],
             "weapon": {"kind": _one(wp.get("kind"), WEAPONS, "none"), "color": _hex(wp.get("color"), "#d8dde3")},
             "aura": {"color": _hex((c.get("aura") or {}).get("color") if isinstance(c.get("aura"), dict)
                                    else c.get("aura"), _hex(c.get("color"), "#ffffff"))},
             "extra_arms": bool(c.get("extra_arms")),
             "height": max(0.7, min(1.4, float(c.get("height") or 1)))}
        p["marks"] = [m for m in p["marks"] if m["kind"]]
        for part in ("gloves", "boots", "belt", "scarf", "cape"):
            v = c.get(part)
            if isinstance(v, dict) and v.get("color"):
                p[part] = {"color": _hex(v["color"], "#222222")}
            elif isinstance(v, str) and v.strip() and v.strip().lower() not in ("none", "no", "false"):
                p[part] = {"color": _hex(v, "#222222")}
        out[ident] = p
    if not out:
        raise AnimationStopped("Claude's cast for this video was empty. Its answer is saved; nothing was drawn.")
    return out


def clean_sets(raw) -> dict:
    out = {}
    rows = raw.items() if isinstance(raw, dict) else ((r.get("id"), r) for r in raw or [] if isinstance(r, dict))
    for key, s in rows:
        if not isinstance(s, dict):
            continue
        ident = re.sub(r"[^a-z0-9_]", "", str(key or "").lower().replace(" ", "_"))[:24]
        if not ident or ident in out or len(out) >= 6:
            continue
        sky = [_hex(x, "") for x in (s.get("sky") or [])][:2]
        floor = [_hex(x, "") for x in (s.get("floor_colors") or s.get("floor") or [])][:2]
        out[ident] = {"prompt": str(s.get("prompt") or s.get("description") or ident.replace("_", " "))[:600],
                      **({"sky": sky} if len(sky) == 2 and all(sky) else {}),
                      **({"floor_colors": floor} if len(floor) == 2 and all(floor) else {})}
    if not out:
        out["street"] = {"prompt": "an empty city street with flat buildings"}
    return out


def clean_plan(raw, cast: dict, sets: dict, t0: float, t1: float) -> dict:
    """The director's shots, actions and effects inside this stretch of the narration (seconds from its start),
    with only known moves, characters and places; shots cover the whole stretch with no gap."""
    raw = raw if isinstance(raw, dict) else {}
    dur = round(t1 - t0, 3)

    def at(v, default=0.0):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return default
        x = x - t0 if x >= t0 - 0.05 and t0 > 0 else x
        return round(max(0.0, min(dur, x)), 3)

    first_set = next(iter(sets))
    shots = []
    for s in raw.get("shots") or []:
        if not isinstance(s, dict):
            continue
        framing = str(s.get("framing") or "wide").lower()
        kind, _, who = framing.partition(":")
        if kind not in FRAMINGS:
            kind, who = "wide", ""
        if who and who not in cast:
            who = ""
        if kind in ("close", "medium") and not who:
            who = next(iter(cast))
        shots.append({"start": at(s.get("start")), "set": s.get("set") if s.get("set") in sets else
                      (shots[-1]["set"] if shots else first_set), "framing": kind + (":" + who if who else ""),
                      "move": _one(s.get("move"), CAMERA, "static"),
                      "cast": [c for c in (s.get("cast") or []) if c in cast][:4]})
    shots.sort(key=lambda s: s["start"])
    if not shots or shots[0]["start"] > 0:
        shots.insert(0, {"start": 0.0, "set": shots[0]["set"] if shots else first_set, "framing": "wide",
                         "move": "static", "cast": []})
    merged = []
    for s in shots:
        if merged and s["start"] - merged[-1]["start"] < 0.6:
            merged[-1] = s if s["start"] == merged[-1]["start"] else merged[-1]
            continue
        merged.append(s)
    for a, b in zip(merged, merged[1:] + [None]):
        a["end"] = b["start"] if b else dur
    actions = []
    for a in raw.get("actions") or []:
        if not isinstance(a, dict) or a.get("who") not in cast:
            continue
        move = str(a.get("move") or "").lower().strip()
        if move not in MOVES:
            continue
        row = {"at": at(a.get("at")), "who": a["who"], "move": move}
        if a.get("target") in cast and a.get("target") != a["who"]:
            row["target"] = a["target"]
        for key in ("x", "to", "dur", "distance", "hits"):
            if isinstance(a.get(key), (int, float)) and not isinstance(a.get(key), bool):
                row[key] = round(float(a[key]), 3)
        if "x" in row:
            row["x"] = max(-5.0, min(5.0, row["x"]))
        if "to" in row:
            row["to"] = max(-5.0, min(5.0, row["to"]))
        if a.get("face") in ("left", "right"):
            row["face"] = a["face"]
        if move in ("blast", "orb", "charge") and HEX.fullmatch(str(a.get("color") or "").strip()):
            row["color"] = str(a["color"]).strip().lower()
        if a.get("pose"):
            row["pose"] = str(a["pose"])[:20]
        actions.append(row)
    placed = {a["who"] for a in actions if a["move"] in ("place", "enter")}
    for i, ident in enumerate(cast):
        if ident not in placed:
            actions.insert(0, {"at": 0.0, "who": ident, "move": "place", "x": -2.0 if i % 2 == 0 else 2.0,
                               "face": "right" if i % 2 == 0 else "left"})
    fx = []
    for f in raw.get("fx") or []:
        if isinstance(f, dict) and f.get("kind") in FX:
            row = {"kind": f["kind"], "at": at(f.get("at"))}
            if f.get("on") in cast:
                row["on"] = f["on"]
            for key in ("dur", "amount"):
                if isinstance(f.get(key), (int, float)):
                    row[key] = float(f[key])
            if f.get("color"):
                row["color"] = _hex(f["color"], "#ffffff")
            fx.append(row)
    return {"shots": merged, "actions": actions, "fx": fx, "duration": dur}


# ── the narration on the voice's own clock ──────────────────────────────────────────────────────────────────────────

def narration(engine, job: Path, srt: Path) -> list:
    """[(start, end, text, [(word, start)])] for every subtitle line, its words timed as the voice says them."""
    entries = engine._parse_srt_full(Path(srt).read_text(encoding="utf-8"))
    try:
        clock = engine._word_clock(Path(job), Path(srt)) or {}
    except Exception:                                   # noqa: BLE001 - the lines' own times still hold
        clock = {}
    out = []
    for i, row in enumerate(entries):
        a, b, text = float(row[0]), float(row[1]), str(row[2])
        words = text.split()
        times = clock.get(i) or []
        if len(times) != len(words):
            span = max(0.01, b - a)
            times = [(a + span * k / max(1, len(words)), 0) for k in range(len(words))]
        out.append((a, b, text, [(w, round(float(t[0]), 2)) for w, t in zip(words, times)]))
    return out


def chunks(lines: list, total: float, size: float = CHUNK_S) -> list:
    """[(start, end, lines)]: the narration in as few stretches as fit `size` seconds, of even length, each starting
    and ending between lines (a stub of a few seconds would restart the scene for nothing)."""
    count = max(1, math.ceil(total / size))
    target = total / count
    out, cur, start = [], [], 0.0
    for line in lines:
        if cur and line[0] - start >= target * 0.92 and len(out) < count - 1:
            out.append((start, line[0], cur))
            cur, start = [], line[0]
        cur.append(line)
    out.append((start, total, cur))
    return [c for c in out if c[1] - c[0] > 0.2]


def beats_text(lines: list) -> str:
    return "\n".join(f"[{a:.2f}-{b:.2f}] {text}\n    words: " + " ".join(f"{w}@{t:.2f}" for w, t in words)
                     for a, b, text, words in lines)


# ── the plugin: prepare (while the voice records) and assemble ──────────────────────────────────────────────────────

def _spec(engine, style: str) -> dict:
    look = ((engine.STYLE_INFO.get(style) or {}).get("look") or {})
    spec = look.get("animated2d") if isinstance(look.get("animated2d"), dict) else {}
    return {**from_profile({}), **spec}


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def _model(kind: str) -> str:
    import cr_claude
    return cr_claude.VISUAL_MODEL if kind == "director" else cr_claude.GENERAL_MODEL


def cast_and_sets(engine, script: str, job: Path, style: str, title: str = "") -> dict:
    """The video's characters as puppets and its places, designed once from the script (casting.json)."""
    folder = Path(job) / "animated2d"
    spec = _spec(engine, style)
    key = _digest({"script": script, "spec": spec, "prompt": _prompt("cast.txt"), "brief": engine._extra_block()})
    saved = _read(folder / "casting.json")
    if saved.get("key") == key:
        return saved
    reply = folder / "casting-reply.txt"
    if reply.is_file() and _read(folder / "casting-request.json").get("key") == key:
        text = reply.read_text(encoding="utf-8")
    else:
        engine.step("Animated 2D: designing the cast and the places")
        prompt = (_prompt("cast.txt").replace("[TITLE]", title or (Path(job).name.split("--")[0].replace("-", " ")))
                  .replace("[SCRIPT]", script[:24000]).replace("[STYLE]", json.dumps(spec, ensure_ascii=False))
                  .replace("[CREATOR]", engine._extra_block() or "none")
                  .replace("[VOCABULARY]", json.dumps({"hair": HAIR, "eyes": EYES, "headwear": HEADWEAR, "marks": MARKS,
                                                       "weapon": WEAPONS})))
        _write(folder / "casting-request.json", {"key": key})
        text = engine.claude(prompt, _model("cast"), max_tokens=6000, provider="claudecode")
        reply.parent.mkdir(parents=True, exist_ok=True)
        reply.write_text(str(text), encoding="utf-8")
    raw = _json(text)
    out = {"key": key, "cast": clean_cast(raw.get("cast")), "sets": clean_sets(raw.get("sets"))}
    _write(folder / "casting.json", out)
    return out


def plate_prompt(set_: dict, spec: dict) -> str:
    art = spec.get("art") or "flat 2D vector animation"
    return (f"{set_['prompt']}. A background plate for a 2D stick-figure animation, in this style: {art}. "
            "EMPTY: nobody in it, no people, no stick figures, no characters, no animals, no text, no logos. "
            "Seen straight on from eye level, wide 16:9. The ground is a flat, level surface across the bottom fifth "
            "of the picture with nothing standing on it in the middle. Simple flat shapes and flat colours, soft depth, "
            "calm enough for characters to fight in front of it.")


def plates(engine, job: Path, casting: dict, spec: dict, *, upload=None) -> dict:
    """{set id: picture path}: one plate per place, drawn once on the creator's image account."""
    folder = Path(job) / "animated2d" / "plates"
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    todo = []
    for ident, s in casting["sets"].items():
        dest = folder / f"{ident}-{_digest(plate_prompt(s, spec))[:10]}.jpg"
        out[ident] = dest
        if not (dest.is_file() and dest.stat().st_size > 5000):
            todo.append((ident, s, dest))
    if todo:
        engine.step(f"Animated 2D: drawing {len(todo)} background plate(s) (about ${PLATE_USD * len(todo):.2f})")
        first = next(iter(out.values()))
        ref = []

        def draw(row):
            ident, s, dest = row
            if ref:
                engine.image_gen(SAME_LOOK + plate_prompt(s, spec), dest, label=f"plate {ident}", ref_urls=ref)
            else:
                engine.image_gen(plate_prompt(s, spec), dest, label=f"plate {ident}")
        # the first place is drawn alone; every other place copies its drawing style, so a cut between places
        # never changes how the world is drawn
        errors = []
        if not (first.is_file() and first.stat().st_size > 5000):
            errors += [e for e in [_try(draw, todo[0])] if e]
            todo = todo[1:]
        if todo and first.is_file():
            try:                                        # to the creator's own image account, like every reference
                import cr_media
                ref.append((upload or (lambda path: cr_media.upload(path, task="images")))(first))
            except Exception as error:                  # noqa: BLE001 - drawn from the words alone then
                engine.log(f"animated 2D: the first plate couldn't be shared as a style reference ({str(error)[:80]})")
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(todo)))) as ex:
            errors += [e for e in ex.map(lambda r: _try(draw, r), todo) if e]
        if errors:
            # a place whose plate could not be drawn is shown as the kit's drawn stand-in in its colours, and said
            engine.step(f"Animated 2D: {len(errors)} plate(s) could not be drawn ({errors[0][:120]}); those places use "
                        "the kit's drawn background")
    return {k: v for k, v in out.items() if v.is_file() and v.stat().st_size > 5000}


def floors(plate_paths: dict) -> dict:
    return {k: floor_line(v) for k, v in plate_paths.items()}


def floor_line(path) -> float:
    """Where a plate's ground meets what stands behind it, as a share of its height (0.62 to 0.9; 0.8 when nothing
    stands out): the strongest change between rows in its lower half. The fighters stand on that line."""
    try:
        import numpy as np
        from PIL import Image
        img = np.asarray(Image.open(path).convert("RGB").resize((320, 180)), dtype=np.float32)
    except Exception:                                   # noqa: BLE001 - an unreadable plate keeps the asked-for floor
        return 0.8
    rows = img.mean(axis=1)                             # (180, 3): each row's mean colour
    best, at = 0.0, 0.8
    for y in range(int(180 * 0.6), int(180 * 0.92)):
        above, below = rows[max(0, y - 8):y].mean(axis=0), rows[y:y + 8].mean(axis=0)
        jump = float(np.abs(above - below).sum())
        if jump > best:
            best, at = jump, y / 180
    return round(at, 3) if best >= 24 else 0.8


def _try(fn, row):
    try:
        fn(row)
        return ""
    except Exception as error:                          # noqa: BLE001 - reported by the caller
        return f"{type(error).__name__}: {error}"


def direct(engine, job: Path, casting: dict, spec: dict, part: tuple, index: int, previous: str, title: str) -> dict:
    """One stretch of narration choreographed by Claude (director-NNN.json), checked."""
    t0, t1, lines = part
    folder = Path(job) / "animated2d"
    template = _prompt("director.txt")
    beats = beats_text(lines)
    key = _digest({"beats": beats, "cast": casting["cast"], "sets": casting["sets"], "spec": spec, "prompt": template,
                   "previous": previous, "t": [t0, t1]})
    reply = folder / f"director-{index:03d}.txt"
    request = folder / f"director-{index:03d}.json"
    if reply.is_file() and _read(request).get("key") == key:
        text = reply.read_text(encoding="utf-8")
    else:
        engine.step(f"Animated 2D: choreographing {t0:.0f}-{t1:.0f} s")
        prompt = (template.replace("[TITLE]", title).replace("[STYLE]", json.dumps(spec, ensure_ascii=False))
                  .replace("[CAST]", json.dumps(casting["cast"], ensure_ascii=False))
                  .replace("[SETS]", json.dumps({k: v["prompt"] for k, v in casting["sets"].items()}, ensure_ascii=False))
                  .replace("[START]", f"{t0:.2f}").replace("[END]", f"{t1:.2f}").replace("[BEATS]", beats)
                  .replace("[PREVIOUS]", previous or "This is the opening: place everyone first.")
                  .replace("[CREATOR]", engine._extra_block() or "none"))
        _write(request, {"key": key})
        text = engine.claude(prompt, _model("director"), max_tokens=12000, provider="claudecode")
        reply.write_text(str(text), encoding="utf-8")
    return clean_plan(_json(text), casting["cast"], casting["sets"], t0, t1)


def film_spec(plan: dict, casting: dict, spec: dict, plate_paths: dict) -> dict:
    sets = {}
    for ident, s in casting["sets"].items():
        row = {k: v for k, v in s.items() if k in ("sky", "floor_colors")}
        if ident in plate_paths:
            row["image"] = str(plate_paths[ident])
            row["floor"] = floor_line(plate_paths[ident])
        sets[ident] = row
    style = {"figure": spec.get("figure", 0.36), "blur": spec.get("blur", 5), "outline": spec.get("outline"),
             "impact_frames": spec.get("impact_frames", True), "sky": spec.get("sky"), "floor": spec.get("floor")}
    return {"duration": plan["duration"], "style": {k: v for k, v in style.items() if v is not None},
            "cast": casting["cast"], "sets": sets, "shots": plan["shots"], "actions": plan["actions"], "fx": plan["fx"]}


def ending(plan: dict) -> str:
    """Where everyone is when a stretch ends, for the next stretch's director."""
    last = {}
    for a in plan["actions"]:
        last[a["who"]] = a
    shot = plan["shots"][-1] if plan["shots"] else {}
    return ("At the end of the previous stretch the place was " + str(shot.get("set") or "?") + "; last moves: "
            + "; ".join(f"{who} {a['move']}" + (f" at {a['target']}" if a.get("target") else "") for who, a in last.items())
            + ". Place everyone again at the start of this stretch where it makes sense.")


# ── rendering ───────────────────────────────────────────────────────────────────────────────────────────────────────

def page(spec: dict, home=None) -> str:
    spec = json.loads(json.dumps(spec))
    for s in (spec.get("sets") or {}).values():
        img = s.get("image")
        if img and not str(img).startswith("data:"):
            p = Path(img)
            s["image"] = "data:%s;base64,%s" % (mimetypes.guess_type(p.name)[0] or "image/jpeg",
                                               base64.b64encode(p.read_bytes()).decode())
    return ("<!doctype html><html><head><meta charset=utf-8><style>html,body{margin:0;background:#000}</style></head>"
            "<body><canvas id=c width=%d height=%d></canvas><script>%s\nwindow.__err='';"
            "window.addEventListener('error',e=>{window.__err=String(e.message)});"
            "(async()=>{try{const film=await loadFilm(%s);"
            "const ctx=document.getElementById('c').getContext('2d',{willReadFrequently:true});"
            "window.renderFrame=(t)=>drawFrame(ctx,film,t);window.__marks=film.marks;window.__ready=true;}"
            "catch(e){window.__err=String(e&&e.stack||e);window.__ready=true;}})();</script></body></html>"
            % (spec.get("width", 1920), spec.get("height", 1080), kit_js(home), json.dumps(spec)))


def render(spec: dict, out: Path, *, workers: int = 2, home=None, log=print) -> list:
    """The film to an mp4 (no sound) and its sound marks [(second, sound, dB)]. Frames are drawn in Chromium, a
    worker per share of the frames; a frame is a pure function of its time, so the shares never wait on each other."""
    from playwright.sync_api import sync_playwright
    W, H = spec.get("width", 1920), spec.get("height", 1080)
    n = max(1, int(round(float(spec["duration"]) * FPS)))
    tmp = Path(tempfile.mkdtemp(prefix="cr_a2d_"))
    html = tmp / "film.html"
    html.write_text(page(spec, home), encoding="utf-8")
    marks = []
    workers = max(1, min(workers, 4, n))

    def share(k):
        for attempt in range(3):
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(args=["--disable-gpu", "--force-color-profile=srgb",
                                                      "--font-render-hinting=none"])
                    pg = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
                    pg.goto(html.as_uri())
                    pg.wait_for_function("window.__ready===true", timeout=120000)
                    err = pg.evaluate("window.__err")
                    if err:
                        raise AnimationStopped("The animated 2D kit could not draw this film: " + str(err)[:300])
                    if k == 0:
                        marks.extend(pg.evaluate("window.__marks"))
                    for i in range(k, n, workers):
                        frame = tmp / f"f_{i:05d}.jpg"
                        if frame.is_file():
                            continue
                        pg.evaluate("(t)=>window.renderFrame(t)", i / FPS)
                        pg.screenshot(path=str(frame), type="jpeg", quality=93,
                                      clip={"x": 0, "y": 0, "width": W, "height": H})
                    err = pg.evaluate("window.__err")
                    browser.close()
                    if err:
                        raise AnimationStopped("The animated 2D kit stopped while drawing: " + str(err)[:300])
                    return
            except AnimationStopped:
                raise
            except Exception as error:                  # noqa: BLE001 - the browser driver is retried twice
                if attempt == 2:
                    raise AnimationStopped(f"Chromium stopped drawing the animation: {str(error)[:200]}") from None
                time.sleep(2 * (attempt + 1))

    try:
        started = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(share, range(workers)))
        import subprocess
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS), "-i", str(tmp / "f_%05d.jpg"),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-color_range", "tv", str(out)], check=True, capture_output=True)
        log(f"animated 2D: {n} frames in {time.time() - started:.0f} s")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [(round(float(t), 3), sound_file(name), float(db)) for t, name, db in marks if sound_file(name)]


def sound_file(name: str) -> str:
    """A kit sound mark as Studio's mixer names it: the kit's own recording, else one of Studio's shared cues."""
    if name in SOUND_FILES:
        return SOUNDS + SOUND_FILES[name] + ".mp3"
    return name if name in ("whoosh", "hit", "riser", "sub_drop", "braam") else ""


def _first_line(path: Path) -> str:
    try:
        return (Path(path).read_text(encoding="utf-8").splitlines() or [""])[0].strip()
    except OSError:
        return ""


def prepare(engine, script, job, style, force):
    """While the voice records: the kit is here, the cast and places are designed and their plates drawn."""
    if not available():
        raise AnimationStopped("Studio's animated 2D kit is missing. Update Studio before making this video.")
    job = Path(job)
    title = _first_line(job / "title.txt")
    casting = cast_and_sets(engine, script, job, style, title)
    plates(engine, job, casting, _spec(engine, style))


def assemble(engine, job, mp3, srt, style, force, burn_subs):
    job = Path(job)
    spec = _spec(engine, style)
    script = (job / "script.txt").read_text(encoding="utf-8") if (job / "script.txt").is_file() else ""
    title = _first_line(job / "title.txt")
    # the cast prepare() designed while the voice recorded (from the engine's own script text)
    saved = _read(job / "animated2d" / "casting.json")
    casting = saved if saved.get("cast") and saved.get("sets") else cast_and_sets(engine, script, job, style, title)
    plate_paths = plates(engine, job, casting, spec)
    total = engine._audio_dur(mp3)
    lines = narration(engine, job, srt)
    if not lines:
        raise AnimationStopped("The narration timing is empty, so nothing could be choreographed.")
    parts = chunks(lines, total)
    folder = job / "motion"
    folder.mkdir(exist_ok=True)
    timeline, segments, previous = [], [], ""
    for index, part in enumerate(parts):
        plan = direct(engine, job, casting, spec, part, index, previous, title)
        previous = ending(plan)
        film = film_spec(plan, casting, spec, plate_paths)
        _write(job / "animated2d" / f"film-{index:03d}.json", film)
        out = folder / f"a2d_{index:03d}_{_digest(film)[:8]}.mp4"
        if not (out.is_file() and out.stat().st_size > 10000) or force:
            engine.step(f"Animated 2D: drawing {part[1] - part[0]:.0f} s of animation ({index + 1} of {len(parts)})")
            marks = render(film, out, workers=max(1, min(3, int(getattr(engine, "MOTION_WORKERS", 2)) + 1)),
                           log=engine.log)
            _write(out.with_suffix(".sfx.json"), marks)
        timeline.append(("motion", str(out), False, round(part[1] - part[0], 6)))
        segments.append((part[0], str(out), round(part[1] - part[0], 6)))
    _write(job / "animated2d" / "delivery.json", {"schema": SCHEMA, "parts": len(parts), "seconds": total,
                                                 "plates": {k: str(v) for k, v in plate_paths.items()}})
    engine._save_motion_plan(job, timeline, segments)
    engine.step("Animated 2D: assembling the film")
    rendered = engine._render_astro_segments(timeline, job, force, white_fade=0.0, style=style)
    engine._concat_segments(rendered, job / "_visual.mp4")
    engine._final_mux(job, engine._with_sound_design(job, mp3, timeline, style), srt, burn_subs,
                      add_qr=False, vignette=False, no_dust_ranges=[(0.0, total)], sub_center=False, no_sub_ranges=[])
    engine._keep_clean_visual(job, style)
    return job / "video.mp4"
