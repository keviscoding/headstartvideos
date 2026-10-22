"""Designed 3D: a channel whose 3D characters are DESIGNED (a face, a signature head and body) rebuilt by Studio's own
3D engine from a cast spec, instead of drawn as pictures or swapped for faceless mannequins.

A customer cloned @ifologyfilms (round black heads with white face marks, bright jackets, stick legs, bright low-poly
sets) and got mannequins; Studio 72-75 drew the scenes with an image model instead, which looked close (6/10) but never
moved like the channel. Studio 77's engine has a designed-cast kit (threed/engine/lib/designed): under the look preset
"designed" it builds every person from the channel's ARCHETYPE (head, face marks, body, limbs, shoes, rim light,
outline, palette) and each character's outfit, on the house rig, so every 3D action works, and it brings the
channel's kind of sets (a room, a street, downtown, an athletics track, a park).

Here: does the kit's body plan fit a channel's cast (fits), the cast spec from what Studio watched (from_profile; the
art director's own spec, cr_style_art, is checked by clean), what the engine reads of it (archetype), the director's
lines about the cast (cast_lines), stamping a video's scenes (stamp) and the likeness check's test scenes (test_video).
Pure data: no model, no network, no files except reading whether the engine has the kit.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

PRESET = "designed"
PLANS = ("orb",)
SETS = ("designed_park", "designed_track", "designed_street", "designed_room", "designed_city")
TOPS = ("jacket", "hoodie", "track", "tshirt", "sweater", "labcoat", "suit", "dress", "chef", "coverall")
HATS = ("cap", "beret", "beanie", "hardhat", "chef", "crown", "band", "none")
FACE_SETS = ("ring", "dot", "bean")
DETAILS = ("collar", "hood", "zip", "buttons", "patch", "pocket", "hem", "zigzag", "cuffs", "stripes", "tie")
EXPRESSIONS = ("neutral", "happy", "smile", "laugh", "excited", "surprised", "shocked", "scared", "worried", "sad",
               "angry", "determined", "thinking", "confused", "sleepy", "tired", "wink", "smug", "disgusted", "pain",
               "dizzy", "love", "calm")
KIT = "threed/engine/lib/designed/index.js"

# the orb plan: a plain ball head (no hair, no nose: a black or white sphere with flat face marks) on stick limbs or a
# simple jacketed body (@ifologyfilms and stick-figure 3D channels). Big, round or bobble heads alone say nothing: chibi
# kids, caricatures and vinyl toys have them too.
ORB = re.compile(r"\b((sphere|spherical|orb|ball|marble|bowling[- ]ball)[- ](shaped |like )?heads?|heads? (spheres?|orbs?|balls?)|"
                 r"(stick|skinny|thin|noodle[- ]thin|spindly)( black| white)? (legs|limbs)|"
                 r"heads?( are| like| as)?( (smooth|glossy|shiny|matte|solid|plain|jet|ink|pitch|black|white|dark|"
                 r"charcoal)[ ,-]*){0,4} ?(spheres?|orbs?|balls?|bowling balls?)|"
                 r"(black|jet[- ]black|pitch[- ]black|ink[- ]black|charcoal|solid black)([- ][a-z]+){0,2}[- ]heads?|(black|orb|ball)[- ]headed|stick ?(figures?|m[ae]n|people|bod(y|ies)|characters?)|"
                 r"stickm[ae]n)\b", re.I)
# a designed cast the orb plan would get wrong: blocky avatars, clay, puppets, chibi, caricatures, realistic faces,
# robots, animals and creatures, figures with hair and noses
OTHER = re.compile(r"\b(roblox|minecraft|lego|minifig\w*|blocky|boxy (heads?|avatars?|bodies|figures?)|block heads?|cube[- ]?headed|cuboid|cylinder heads?|"
                   r"cylindrical heads?|voxel|clay|plasticine|claymation|putty|felt(ed)?|wool|puppets?|plush\w*|anime|vtuber|"
                   r"vrm|chibi|kawaii|funko|vinyl|caricatur\w*|bobble ?heads?|photoreal\w*|realistic|metahumans?|"
                   r"digital (humans?|doubles?)|skin pores|subsurface|dinosaurs?|t\.? ?rex|animals?|creatures?|monsters?|"
                   r"fur|furry|feathers?|robots?|visors?|capsule[- ]bodied|bean[- ]shaped|jellybeans?|toddlers?|"
                   r"hair|hairstyles?|ponytails?|beards?|stubble|noses?|googly|irises|lashes|ears)\b", re.I)
_NEG = re.compile(r"\b(no|not|never|without|nor|lacking)\b(\W+[\w'-]+){0,3}\W*$", re.I)

COLOURS = {
    "light blue": "#7DB4E6", "sky blue": "#7DB4E6", "dark blue": "#22305A", "navy": "#22305A", "royal blue": "#2F5BA8",
    "dark grey": "#4A4D55", "dark gray": "#4A4D55", "light grey": "#C8C6C0", "light gray": "#C8C6C0",
    "dark green": "#2E6B3A", "lime": "#8CC43C", "mustard": "#D9A31E", "maroon": "#7A2030", "burgundy": "#7A2030",
    "yellow": "#E8B320", "gold": "#E2B23E", "orange": "#EE7A28", "red": "#D2382D", "crimson": "#B0202C",
    "pink": "#E86FA5", "purple": "#7A4FB0", "violet": "#7A4FB0", "lilac": "#A98BD0", "blue": "#2F5BA8",
    "teal": "#1FA5A0", "turquoise": "#1FA5A0", "cyan": "#1FA5A0", "green": "#3E8C4A", "olive": "#6E6A3C",
    "brown": "#7A5232", "tan": "#C9A272", "beige": "#D8C8A8", "khaki": "#B49F6E", "cream": "#EFE6CF",
    "white": "#F2F0EA", "grey": "#8D9096", "gray": "#8D9096", "silver": "#B8BDC5", "black": "#1D1E22",
}
_COLOUR = re.compile(r"\b(" + "|".join(sorted(map(re.escape, COLOURS), key=len, reverse=True)) + r")\b", re.I)
GARMENTS = [(r"lab ?coats?|doctor'?s coat", "labcoat"), (r"track ?(jacket|suit)|tracksuit", "track"),
            (r"hood(ie|ed (top|jacket|sweatshirt))s?", "hoodie"), (r"sweat ?shirts?|sweaters?|jumpers?|pullovers?|"
            r"long[- ]sleeve(d)? (shirt|top)|cardigans?", "sweater"), (r"t[- ]?shirts?|tees?|polo|shirts?|tops?|blouses?",
            "tshirt"), (r"suits?|blazers?|tuxedos?", "suit"), (r"dress(es)?|gowns?|robes?", "dress"),
            (r"aprons?|chef'?s? (jacket|whites|uniform)", "chef"), (r"overalls?|coveralls?|jumpsuits?|space ?suits?", "coverall"),
            (r"jackets?|coats?|parkas?|vests?|uniforms?|blouson|bomber", "jacket")]
HAT_WORDS = [(r"baseball caps?|caps?|visors?", "cap"), (r"berets?|flat caps?", "beret"), (r"beanies?|woolly hats?|knit hats?|"
             r"toques?", "beanie"), (r"hard ?hats?|helmets?", "hardhat"), (r"chef'?s? hats?|toques? blanches?", "chef"),
             (r"crowns?|tiaras?", "crown"), (r"headbands?|bandanas?", "band")]
DEFAULT_LOOK = {"top": "jacket", "color": "#E8B320", "hat": None, "glasses": False}


def engine_ready(home) -> bool:
    """This Studio's 3D engine has the designed-cast kit (Studio 77+)."""
    try:
        return (Path(home) / KIT).is_file()
    except (OSError, TypeError):
        return False


def _hex(value, default=None):
    s = str(value or "").strip().lower()
    if s in COLOURS:
        return COLOURS[s]
    if re.fullmatch(r"#?[0-9a-f]{6}", s):
        return ("#" + s.lstrip("#")).upper()
    return default


def _words(profile) -> str:
    """Everything Studio watched about how the channel's pictures and people look."""
    pics = profile.get("pictures") or {}
    art = pics.get("art_style") or {}
    parts = [str(art.get("value") or "")] + [str(x) for x in art.get("alternatives") or []]
    parts += [str(a.get("look") or "") for a in art.get("looks") or [] if isinstance(a, dict)]
    parts += [str(c.get("look") or "") for c in (pics.get("characters") or {}).get("value") or [] if isinstance(c, dict)]
    parts += [str(x) for x in profile.get("recognisable") or []]
    return ". ".join(p for p in parts if p).lower()


def _found(rx, words: str) -> list:
    """The matches of rx in these words, phrase by phrase, leaving out the negated ones ("no hair or nose")."""
    out = []
    for phrase in re.split(r"[.;:()\n]|\s[-–—]\s", words):
        out += [m.group(0).lower() for m in rx.finditer(phrase) if not _NEG.search(phrase[:m.start()])]
    return list(dict.fromkeys(out))


def fits(profile) -> tuple:
    """("orb", evidence) when the designed-cast kit's ball-headed body plan fits this channel's people, else ("", why)."""
    words = _words(profile)
    orb, other = _found(ORB, words), _found(OTHER, words)
    if orb and len(orb) > len(other):
        return "orb", ", ".join(orb[:3])
    if other:
        return "", f"its characters have {', '.join(other[:2])}, which Studio's designed-cast kit doesn't build yet"
    return "", "its characters aren't ball heads on stick limbs, the one design Studio's 3D engine builds so far"


def _part_colour(part: str, span) -> str:
    """The colour word nearest before (else after) a garment word in one comma part of a look."""
    before = [m for m in _COLOUR.finditer(part) if m.end() <= span[0]]
    if before:
        return COLOURS[before[-1].group(1).lower()]
    after = [m for m in _COLOUR.finditer(part) if m.start() >= span[1]]
    return COLOURS[after[0].group(1).lower()] if after else ""


def character_look(text: str) -> dict:
    """One character's outfit from watched words ("black ball head, yellow baseball cap, green hoodie, black skinny
    limbs") -> {"top", "color", "hat", "glasses"}; {} when the words name no garment."""
    out = {}
    for part in re.split(r"[,;]|\band\b|\bwith\b", str(text or "").lower()):
        part = part.strip()
        if not part or re.search(r"\b(heads?|face|eyes?|mouth|legs?|limbs?|arms?|pants|trousers|jeans|shoes?|"
                                 r"sneakers?|boots?|socks?|gloves?|hands?)\b", part) and not any(
                re.search(r"\b(" + w + r")\b", part) for w, _ in GARMENTS + HAT_WORDS):
            continue
        if re.search(r"\b(glasses|spectacles|goggles)\b", part):
            out["glasses"] = True
        hat = next(((k, m) for w, k in HAT_WORDS for m in [re.search(r"\b(" + w + r")\b", part)] if m), None)
        if hat and "hat" not in out:
            out["hat"] = {"kind": hat[0], "color": _part_colour(part, hat[1].span()) or "#22305A"}
            continue
        top = next(((k, m) for w, k in GARMENTS for m in [re.search(r"\b(" + w + r")\b", part)] if m), None)
        if top and "top" not in out:
            out["top"], out["color"] = top[0], _part_colour(part, top[1].span()) or DEFAULT_LOOK["color"]
    return out


def from_profile(profile) -> dict:
    """The cast spec from what Studio watched (before, or without, the art director's own)."""
    words = _words(profile)
    plan = fits(profile)[0] or "orb"
    head = "#070708"
    if re.search(r"\bwhite (ball |round |sphere |orb )?heads?\b", words) and not re.search(r"\bblack (ball |round )?heads?\b", words):
        head = "#F2F0EA"
    pics = profile.get("pictures") or {}
    style_words = " ".join([str((pics.get("art_style") or {}).get("value") or "")] + [str(x) for x in profile.get("recognisable") or []]).lower()
    face = "dot" if re.search(r"\b(dot|dotted) eyes\b", style_words) and not re.search(r"\b(ring|circle) eyes|pupils\b", style_words) else "ring"
    chars = [c for c in (((profile.get("pictures") or {}).get("characters") or {}).get("value") or []) if isinstance(c, dict)]
    chars.sort(key=lambda c: -float(c.get("screen_share") or 0))
    cast = []
    for c in chars:
        look = character_look(c.get("look"))
        if look.get("top"):
            row = {"name": re.sub(r"[^\w' -]", "", str(c.get("name") or "character"))[:40] or "character",
                   "role": "lead" if not cast else "character", **look}
            if not any(r.get("top") == row["top"] and r.get("color") == row["color"] for r in cast):
                cast.append(row)
        if len(cast) >= 4:
            break
    if not cast:
        cast = [{"name": "lead", "role": "lead", **DEFAULT_LOOK}]
    palette = list(dict.fromkeys([r["color"] for r in cast] + ["#E8B320", "#D2382D", "#3E8C4A", "#2F5BA8", "#EE7A28",
                                                                  "#7A4FB0", "#1FA5A0", "#F2F0EA"]))[:10]
    sets = [s for s, w in (("designed_room", r"\b(room|bedroom|interior|indoor|house|home|office|lab|school|class)"),
                           ("designed_street", r"\b(street|suburb\w*|neighbou?rhood|houses?|town)"),
                           ("designed_city", r"\b(city|urban|downtown|buildings?|skyline)"),
                           ("designed_track", r"\b(track|stadium|sports?|field|running)"),
                           ("designed_park", r"\b(park|grass|outdoor|nature|lawn|tree)")) if re.search(w, words)]
    return clean({"plan": plan, "head": {"color": head}, "face": {"set": face, "color": "#FFFFFF" if head != "#F2F0EA" else "#141416"},
                  "cast": cast, "palette": palette, "sets": sets or list(SETS), "from": "watched"})


def _num(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return round(min(hi, max(lo, v)), 4)


def _look(row) -> dict:
    """One character's outfit, checked."""
    row = row if isinstance(row, dict) else {}
    top = str(row.get("top") or "jacket").lower()
    out = {"top": top if top in TOPS else "jacket", "color": _hex(row.get("color"), DEFAULT_LOOK["color"])}
    if _hex(row.get("trim")):
        out["trim"] = _hex(row.get("trim"))
    hat = row.get("hat")
    if isinstance(hat, dict) and str(hat.get("kind") or "").lower() in HATS and str(hat.get("kind")).lower() != "none":
        out["hat"] = {"kind": str(hat["kind"]).lower(), "color": _hex(hat.get("color"), "#22305A")}
    if row.get("glasses") is True:
        out["glasses"] = True
    expr = str(row.get("expression") or "").lower()
    if expr in EXPRESSIONS:
        out["expression"] = expr
    return out


def clean(spec) -> dict:
    """A cast spec (the art director's, or from_profile's) with every value checked: a known plan, hex colours, sizes in
    range, known tops, hats, face sets and sets. Unknown keys are dropped; nothing missing is invented beyond defaults."""
    s = spec if isinstance(spec, dict) else {}
    head, face, limbs, shoes, toon = (s.get(k) if isinstance(s.get(k), dict) else {} for k in ("head", "face", "limbs", "shoes", "toon"))
    plan = str(s.get("plan") or "orb").lower()
    out = {
        "plan": plan if plan in PLANS else "orb",
        "head": {"size": _num(head.get("size"), 0.18, 0.34, 0.246), "color": _hex(head.get("color"), "#070708"),
                 "rim": _hex(head.get("rim"), "#F7E7C2"), "rim_width": _num(head.get("rim_width"), 0.0, 0.15, 0.09)},
        "face": {"set": face.get("set") if face.get("set") in FACE_SETS else "ring", "color": _hex(face.get("color"), "#FFFFFF"),
                 "size": _num(face.get("size"), 0.7, 1.4, 1.15)},
        "limbs": {"color": _hex(limbs.get("color"), "#0E0E10"), "thickness": _num(limbs.get("thickness"), 0.6, 2.0, 1.0),
                  "hand_color": _hex(limbs.get("hand_color"), _hex(limbs.get("color"), "#101013"))},
        "shoes": {"color": _hex(shoes.get("color"), "#F2F0EA")},
        "toon": {"outline": _num(toon.get("outline"), 0.0, 0.014, 0.009), "outline_color": _hex(toon.get("outline_color"), "#141416"),
                 "rim": _hex(toon.get("rim"), _hex(head.get("rim"), "#F7E7C2")), "rim_k": _num(toon.get("rim_k"), 0.0, 1.6, 1.0)},
        "palette": [c for c in (_hex(x) for x in (s.get("palette") or [])[:10] if isinstance(x, str)) if c] or
                   ["#E8B320", "#D2382D", "#3E8C4A", "#2F5BA8", "#EE7A28", "#7A4FB0", "#1FA5A0", "#F2F0EA"],
        "details": [d for d in (s.get("details") or ["collar", "buttons", "patch", "pocket", "cuffs", "hem"]) if d in DETAILS][:10],
        "cast": [], "sets": [x for x in (s.get("sets") or SETS) if x in SETS] or list(SETS),
        "height": _num(s.get("height"), 1.2, 2.0, 1.6), "from": str(s.get("from") or "")[:20],
    }
    for row in (s.get("cast") or [])[:6]:
        if not isinstance(row, dict):
            continue
        name = re.sub(r"[^\w' -]", "", str(row.get("name") or "character"))[:40].strip() or "character"
        out["cast"].append({"name": name, "role": "lead" if str(row.get("role") or "").lower() in ("lead", "main", "host",
                            "protagonist") else "character", **_look(row)})
    if out["cast"] and not any(r["role"] == "lead" for r in out["cast"]):
        out["cast"][0]["role"] = "lead"
    return out


ENGINE_KEYS = ("plan", "height", "head", "face", "limbs", "shoes", "toon", "palette", "details")


def archetype(spec) -> dict:
    """What the engine reads (scene look.designed): the body plan and its materials, never the cast list or sets."""
    s = clean(spec)
    return {k: copy.deepcopy(s[k]) for k in ENGINE_KEYS}


def designed_block(row) -> dict:
    """A cast row as a CastDef's `designed` block."""
    look = _look(row)
    return {k: v for k, v in look.items()}


def cast_lines(spec) -> str:
    """The director's lines about the channel's cast (its creative direction)."""
    s = clean(spec)
    if not s["cast"]:
        return ""
    rows = []
    for r in s["cast"]:
        block = designed_block(r)
        hat = f", a {block['hat']['color']} {block['hat']['kind']}" if block.get("hat") else ""
        rows.append(f"- {r['name']} ({'the main character' if r['role'] == 'lead' else 'returns often'}): a {block['color']} "
                    f"{block['top']}{hat}{', glasses' if block.get('glasses') else ''} -> \"designed\": "
                    + str(block).replace("'", '"').replace("True", "true"))
    return ("THE CHANNEL'S DESIGNED CAST (every person is built in the channel's design; give each CastDef its "
            "\"designed\" block; the main character of this video wears the main character's outfit in every scene):\n"
            + "\n".join(rows) + "\nThe channel films in these designed sets: " + ", ".join(s["sets"]) + ".")


# worlds the director may still name under the designed look, and the designed set that stands in for them
_WORLD = [(r"room|interior|house|home|bed|office|lab|kitchen|class|school|hospital", "designed_room"),
          (r"city|urban|downtown|avenue|metropolis", "designed_city"),
          (r"street|suburb|town|village|neighbou?rhood|road", "designed_street"),
          (r"stadium|track|sport|arena|race", "designed_track")]


def _world(world: dict) -> dict:
    biome = str(world.get("biome") or world.get("kind") or "")
    if biome.startswith("designed_") or biome in ("interior",):
        return world
    to = next((s for w, s in _WORLD if re.search(w, biome, re.I)), "designed_park")
    out = {k: v for k, v in world.items() if k not in ("biome", "kind")}
    out["biome"] = to
    return out


def stamp(video: dict, spec, lead: dict = None) -> dict:
    """A video.json (threed._video) made for the designed look: every scene carries the archetype (look.designed),
    stands in a designed set, and every CastDef has a designed block (the director's own, else the channel's main
    character for the first and the palette for the rest)."""
    s = clean(spec)
    arch = archetype(s)
    out = copy.deepcopy(video)
    lead_row = next((r for r in s["cast"] if r["role"] == "lead"), None)
    pal = s["palette"]
    for i, d in enumerate(out.get("cast") or []):
        if not isinstance(d, dict) or isinstance(d.get("designed"), dict):
            continue
        if i == 0 and lead_row:
            d["designed"] = designed_block(lead_row)
        elif not (d.get("colors") or {}).get("coat"):
            d["designed"] = {"color": pal[(i * 3) % len(pal)]}
    for scene in out.get("scenes") or []:
        look = scene.setdefault("look", {})
        if str(look.get("preset") or "") != PRESET:
            continue
        look["designed"] = copy.deepcopy(arch)
        rooms = [e for sec in ("structures", "objects") for e in scene.get(sec) or []
                 if isinstance(e, dict) and str(e.get("kind") or "").startswith("interior.")]
        if rooms:                                 # a realistic room of the documentary library: the designed room instead
            kind = str(rooms[0].get("kind"))
            scene["world"] = {"biome": "designed_room", "furnish": next(
                (f for w, f in _FURNISH if re.search(w, kind)), "living")}
            for sec in ("structures", "objects"):
                if isinstance(scene.get(sec), list):
                    scene[sec] = [e for e in scene[sec] if not (isinstance(e, dict) and str(e.get("kind") or "").startswith("interior."))]
        elif isinstance(scene.get("world"), dict) or "world" not in scene:
            scene["world"] = _world(scene.get("world") or {})
    return out


_FURNISH = [(r"office|study|hq|bunker", "office"), (r"operating|hospital|lab|surgery", "lab"), (r"kitchen", "kitchen"),
            (r"class|school", "classroom"), (r"bed|cell|small_room|cabin", "bedroom")]


def test_video(spec) -> dict:
    """The likeness check's three test shots, rendered by the engine in the channel's design: two characters talking
    in a bright room, one running across a wide outdoor scene seen from the side, a close shot reacting with surprise."""
    s = clean(spec)
    cast = s["cast"] or [{"name": "lead", "role": "lead", **DEFAULT_LOOK}]
    a = designed_block(cast[0])
    b = designed_block(cast[1]) if len(cast) > 1 else {"top": "jacket", "color": next((c for c in s["palette"] if c != a["color"]), "#D2382D")}
    defs = [{"id": "a", "name": "A", "designed": a}, {"id": "b", "name": "B", "designed": b}]
    look = {"time": "afternoon", "sky": "clear", "preset": PRESET, "designed": archetype(s)}
    keys = lambda p0, p1, l: {"move": "keys", "limits": "eye", "dof": 1.2,
                              "keys": [{"t": 0, "pos": p0, "look": l}, {"t": 3, "pos": p1, "look": l}]}
    room = "designed_room" if "designed_room" in s["sets"] else s["sets"][0]
    out = "designed_track" if "designed_track" in s["sets"] else "designed_park"
    return {"cast": defs, "scenes": [
        {"id": "talk", "dur": 3, "look": dict(look, time="morning"), "world": {"biome": room, "furnish": "living"},
         "cast": [{"ref": "a", "at": [-0.6, 0.2], "heading": 120, "action": "speak", "face": "smile"},
                  {"ref": "b", "at": [0.7, -0.3], "heading": 290, "action": "watch", "face": "happy"}],
         "camera": keys([0.4, 1.45, 2.8], [0.1, 1.45, 2.7], [0.0, 1.15, -0.1])},
        {"id": "run", "dur": 3, "look": look, "world": {"biome": out},
         "cast": [{"ref": "a", "at": [-6, 0], "heading": 90, "action": "run", "face": "determined", "path": [[-6, 0], [30, 0]]}],
         "camera": {"move": "follow", "target": "ref:a", "side": "left", "speed": "medium"}},
        {"id": "react", "dur": 3, "look": look, "world": {"biome": "designed_park"},
         "cast": [{"ref": "a", "at": [0, 0], "heading": 175, "action": "watch", "face": "surprised"}],
         "camera": keys([0.55, 1.3, 2.4], [0.2, 1.3, 2.1], [0, 1.2, 0])},
    ]}
