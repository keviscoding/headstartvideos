"""Animated 2D channels: each channel's own renderer, written by Claude, drawn in code.

A @DartAnimates clone (stick-figure fights animated frame by frame) came out as AI stills with camera moves: the look
was right and nothing moved. Every 2D-animated channel is different (stick fighters, bean-shaped characters, paper
cut-outs), so no fixed engine draws them all. Instead:

- Build, once per style, during the study: the creator's Claude Code studies the reference frames and writes the
  channel's renderer, JavaScript on top of Studio's kit (animated2d/: a page runtime plus optional helpers: a skeleton
  and poses, effects, a whole fight engine and an example renderer). The kit draws test shots with it; Gemini judges
  them against the channel's own frames, and Claude revises it within a budget. The bar is the channel's own: its
  held-out frame's score by the same judge. Below the bar Studio says so and offers pictures instead.
- Save: the renderer goes into the learned style, versioned and hash-checked, so every video reuses it unchanged.
- Per video: Claude only casts (in the renderer's declared traits) and choreographs (in its declared actions), from
  the narration's own word timings; Studio draws every frame locally and mixes each sound mark the renderer leaves.

A renderer only ever runs in a headless page: about:blank, a strict content security policy (no network, no eval),
an offline browser context and every request blocked. It receives the scene, the narration's words and the
background pictures as data, and gives back pixels and sound marks. Nothing is substituted silently: a missing kit,
a renderer that changed on disk, an unreadable plan or a failed render stops the video with the reason, and saved
work is reused on the next attempt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = "animated2d"                     # the kit's folder in the Studio (private: shipped by the release builder)
KIT_API = 1                            # the page runtime's interface a renderer is written against
KIT_FILES = ("core.js", "rig.js", "draw.js", "moves.js", "fx.js", "film.js", "stage.js", "bind.js")
EXAMPLE = "example-stick.js"             # the kit's own stick fights (styles from before renderers)
EXAMPLE_OWN = "example-own.js"          # what Claude is shown: a renderer that draws everything itself
PROMPTS = ("cast.txt", "director.txt", "build.txt", "review.txt", "AUTHORING.md", EXAMPLE_OWN)
VISUALS = "cr_animated2d"
VISUALS3D = "cr_animated3d"            # the same plugin for a 3D channel's renderer (cr_animated3d.py)
PLUGINS = (VISUALS, VISUALS3D)
# The 3D kit (Studio 105): the same contract, a WebGL page. Its runtime, example and guide are private like the 2D kit's;
# the page reads three.js and Studio's own 3D engines from this Studio folder through a read-only origin, nothing else.
KIT3D = "animated3d"
KIT3D_FILES = ("core3d.js", "example3d.js", "AUTHORING.md")
THREE_DIR = Path("threed") / "engine" / "vendor" / "three"
KIT_ORIGIN = "https://kit.local"
KIT_ROOTS = {"three": THREE_DIR, "kit": Path(KIT3D), "a2d": Path(KIT), "engine": Path("threed") / "engine",
             "rbx": Path("shorts") / "rbx" / "r3d" / "engine"}
KIT_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json", ".png": "image/png",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".glb": "model/gltf-binary",
             ".gltf": "model/gltf+json", ".bin": "application/octet-stream", ".ktx2": "image/ktx2",
             ".woff2": "font/woff2", ".ttf": "font/ttf"}
CSP3D = ("default-src 'none'; script-src 'unsafe-inline' https://kit.local; style-src 'unsafe-inline'; "
         "img-src https://kit.local data: blob:; font-src https://kit.local data:; media-src data: blob:; "
         "connect-src https://kit.local; worker-src 'none'; frame-src 'none'")
SCHEMA = 1
FPS = 30
CHUNK_S = 60.0                         # narration a director call choreographs at once (at most)
PLATE_USD = 0.02                       # one background plate on the creator's Kie account
BUILD_ROUNDS = 4                       # renderer drafts at most (each one Claude vision call and one judged render)
MIN_ROUNDS = 2                         # a first draft over the bar is still revised once from the judge's notes
ISSUE_COST = 0.75                      # how much closer to the channel a draft must be to win while it can't yet draw
                                       # what every video needs (Studio 109's probes: any place, crowds, text, types)
BUILD_MINUTES = 24                     # and at most this long in all
RENDERER_MAX = 240_000                 # characters of renderer code
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
       "font-src data:; media-src data: blob:; connect-src 'none'; worker-src 'none'; frame-src 'none'")
FORBIDDEN = re.compile(r"(?<![\w$])(fetch|XMLHttpRequest|WebSocket|EventSource|importScripts|localStorage|sessionStorage|"
                       r"indexedDB|eval|Worker|SharedArrayBuffer)\b|navigator\s*\.\s*sendBeacon|document\s*\.\s*cookie|"
                       r"window\s*\.\s*open\b|new\s+Function\b|\bimport\s*\(|\brequire\s*\(|"
                       r"location\s*\.\s*(href|assign|replace)\b")
SAME_LOOK = ("STYLE REFERENCE: the reference picture is another place in this same animation. Draw the new place in "
             "exactly its drawing style: the same line weight, flat colours, palette, light, blur and level of detail. "
             "Do not copy its layout. ")
SOUNDS = "file:sfx/animated2d/"        # the kit's fight sounds, installed under assets/sfx/animated2d
SOUND_FILES = ("punch", "punch_heavy", "kick", "sub_hit", "block", "dash", "jump", "land", "body_fall", "explosion",
               "beam", "energy_throw", "charge", "charge_short", "roar_boom", "teleport", "cast", "cloth", "whoosh")
SHARED_CUES = ("whoosh", "whoosh_out", "hit", "riser", "sub_drop", "braam", "pop", "tap", "paper", "glitch", "sting",
               "ping", "click", "static", "crackle", "whip")
CAMERA = ("static", "push", "pull", "pan", "crash")
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
COLOUR_WORDS = [("teal", "#3fb59e"), ("cyan", "#4cc3d9"), ("turquoise", "#3fbfb0"), ("mint", "#7fe0b0"),
                ("green", "#5cbf62"), ("lime", "#9ccc3c"), ("red", "#e0625a"), ("crimson", "#c8323c"),
                ("salmon", "#ee8a78"), ("pink", "#ee8fb0"), ("magenta", "#d04fa8"), ("purple", "#8e6bd0"),
                ("lavender", "#a9a3d6"), ("violet", "#8a5fd0"), ("blue", "#4f86d8"), ("navy", "#2e4a8a"),
                ("orange", "#ef8a3c"), ("yellow", "#efc93c"), ("gold", "#d9a93c"), ("brown", "#8a5a3c"),
                ("white", "#eeeeee"), ("black", "#1d1d1f"), ("grey", "#9a9da3"), ("gray", "#9a9da3")]
STICK = re.compile(r"\b(stick[- ]?(figures?|m[ae]n|people|nodes|animation|fight\w*)|stickm[ae]n|pivot|limb puppets?|"
                   r"jointed (silhouettes?|figures?))\b", re.I)


class AnimationStopped(RuntimeError):
    """The animated video stopped for a reason the creator can read; its saved work is kept."""


# ── the kit ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def kit_dir(home=None) -> Path:
    return Path(home or HERE) / KIT


def available(home=None) -> bool:
    """The kit is installed in this Studio (Studio 103 and newer ship it; 104 the renderer platform)."""
    folder = kit_dir(home)
    return all((folder / f).is_file() for f in KIT_FILES + PROMPTS + (EXAMPLE,))


def animated(look) -> bool:
    """A style whose characters are animated by its own renderer (2D or 3D)."""
    return isinstance(look, dict) and look.get("visuals") in PLUGINS


def dims_of(spec) -> int:
    """2 or 3: which kit a scene, a style's spec or a renderer record is drawn with."""
    return 3 if isinstance(spec, dict) and spec.get("dims") == 3 else 2


# The engine's timeline editor is made for videos of many short shots. A video drawn by a style's own renderer is a few
# whole animations (motion/a2d_NNN_*.mp4, a stretch of narration each): no edit there redraws one, and a retime,
# delete or swap freezes or restarts it, while its re-render burns captions the renderer already drew. The owner
# (2026-10-27): hide it for these videos, say why, and point to what works. ("Redo this minute with a note" is next.)
NO_TIMELINE = ("This video was drawn by your style's own animation code, so the timeline can't edit it: each minute of "
               "it is one whole animation, and the timeline's edits can't redraw them. To change how your videos look, "
               "use Improve this style on the style's page. To change this video, make it again with your changes in "
               "its brief.")


def coded_video(job) -> bool:
    """A video drawn by a style's own renderer (Claude's code): its minutes are whole animations the timeline can't
    edit (NO_TIMELINE)."""
    job = Path(job)
    return (job / "animated2d" / "delivery.json").is_file() or any((job / "motion").glob("a2d_*.mp4"))


def kit_name(spec) -> str:
    """How a video's progress names its kind of animation: "Animated 3D" for a renderer drawn in three.js (a 3D style's
    log said "Animated 2D", 2026-10-27), else "Animated 2D". The work folder stays animated2d/ for videos already made."""
    return "Animated 3D" if dims_of(spec) == 3 else "Animated 2D"


def available3d(home=None) -> bool:
    """The 3D kit is installed, with the 2D kit's prompts and sounds it shares and the 3D engine's three.js."""
    root = Path(home or HERE)
    return available(home) and all((root / KIT3D / f).is_file() for f in KIT3D_FILES) and \
        (root / THREE_DIR / "three.module.js").is_file()


def _kit_file(root: Path, rel: str, html: str):
    """(bytes, content type) of what a 3D page asks the kit origin for, or (None, "") outside the kit's own roots."""
    rel = rel.split("?", 1)[0].split("#", 1)[0]
    if rel == "index.html":
        return html.encode("utf-8"), "text/html; charset=utf-8"
    head, _, rest = rel.partition("/")
    base = KIT_ROOTS.get(head)
    kind = KIT_TYPES.get(Path(rest).suffix.lower())
    if base is None or not rest or kind is None or ".." in rest.split("/"):
        return None, ""
    top = (root / base).resolve()
    path = (top / rest).resolve()
    if top not in path.parents or not path.is_file():
        return None, ""
    return path.read_bytes(), kind


def kit_js(home=None) -> str:
    folder = kit_dir(home)
    return "".join(re.sub(r"if \(typeof module[^\n]*\n", "", (folder / f).read_text(encoding="utf-8")) + "\n"
                   for f in KIT_FILES)


def _prompt(name: str, home=None) -> str:
    path = kit_dir(home) / name
    if not path.is_file():
        raise AnimationStopped("Studio's animated 2D kit is missing. Update Studio before making this video.")
    return path.read_text(encoding="utf-8")


def earlier(name: str, home=None) -> list:
    """The texts of a prompt's earlier versions the kit keeps (cast-108.txt beside cast.txt): a video started under an
    earlier Studio finds its saved answers by them, so a Resume never asks Claude again or pays for its pictures again."""
    stem, _, ext = name.rpartition(".")
    folder = kit_dir(home)
    return [p.read_text(encoding="utf-8") for p in sorted(folder.glob(f"{stem}-*.{ext}"))
            if re.fullmatch(re.escape(stem) + r"-\d+\." + re.escape(ext), p.name)]


def example(home=None) -> str:
    return _prompt(EXAMPLE, home)


def _prompt3d(name: str, home=None) -> str:
    path = Path(home or HERE) / KIT3D / name
    if not path.is_file():
        raise AnimationStopped("Studio's animated 3D kit is missing. Update Studio before making this video.")
    return path.read_text(encoding="utf-8")


def pace_words(spec: dict) -> str:
    """How often the director cuts: the channel's own measured shot length, else the usual 1.5 to 4 s."""
    shot = spec.get("shot_s") if isinstance(spec, dict) else None
    if not isinstance(shot, (int, float)) or shot <= 0:
        return "A new shot every 1.5 to 4 s, cut on a spoken word."
    lo, hi = max(0.7, round(shot * 0.7, 1)), max(1.2, round(shot * 1.7, 1))
    cuts = spec.get("cuts_per_min")
    return (f"A new shot every {lo:g} to {hi:g} s (the channel's shots last about {shot:g} s"
            + (f", {cuts:g} cuts a minute" if isinstance(cuts, (int, float)) else "") + "), cut on a spoken word.")


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
    """(True, how its characters were seen): Claude writes a renderer for any channel that animates drawn characters,
    so every one fits; the words say what kind (stick figures, or the channel's own art style)."""
    sources = _v((profile.get("pictures") or {}).get("sources"), {}) or {}
    seen = [m.group(0).lower() for m in STICK.finditer(_words(profile))]
    if float(sources.get("stick_figure") or 0) >= 0.25 or seen:
        named = [w for w in dict.fromkeys(x.replace("-", " ") for x in seen) if w not in ("stick figure", "stick figures")]
        return True, "stick figures" + (f": {', '.join(named[:2])}" if named else "")
    art = str(_v((profile.get("pictures") or {}).get("art_style"), "") or "").strip().rstrip(".")
    return True, (art[:90].lower() if art else "drawn characters")


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
    """What Studio saw of the channel, for its renderer and its videos (look.animated2d): its art style in words, its
    palette, background blur, recurring characters, effects, and stand-in sky and ground colours."""
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
            share = c.get("screen_share")
            cast.append({"name": str(c.get("name") or "")[:40], "look": str(c["look"])[:240],
                         "role": str(c.get("role") or "character"), "color": colour_of(c["look"]),
                         **({"share": round(float(share), 2)} if isinstance(share, (int, float)) and
                            not isinstance(share, bool) and 0 <= share <= 1 else {})})
    motion = _v((profile.get("motion") or {}).get("classes"), {}) or {}
    edit = profile.get("edit") or {}
    shot_s, cuts = _v(edit.get("median_shot_s"), edit.get("median_shot_s")), _v(edit.get("cuts_per_min"), edit.get("cuts_per_min"))
    pace = {"shot_s": round(float(shot_s), 2)} if isinstance(shot_s, (int, float)) and 0.3 <= shot_s <= 20 else {}
    if isinstance(cuts, (int, float)) and 0 < cuts <= 120:
        pace["cuts_per_min"] = round(float(cuts), 1)
    return {"schema": SCHEMA, **pace, "art": str(_v((profile.get("pictures") or {}).get("art_style"), "") or "")[:400],
            "recognisable": [str(x)[:160] for x in (profile.get("recognisable") or [])][:8],
            "figure": 0.36, "blur": 5 if re.search(r"blur", low) else 2,
            "outline": {"color": "#141414", "width": 0.012} if re.search(r"\boutlin", low) else None,
            "impact_frames": True, "sky": [light[0], light[-1]], "floor": [dark[-1], dark[0]], "palette": palette[:8],
            "effects": [m.group(0) for m in re.finditer(r"(neon |glowing |energy )?(auras?|speed lines|smears?|"
                                                          r"motion blur|impact frames?|lightning|sparks|blood)", low)][:6],
            "moving": round(float(motion.get("moving") or 0), 2), "cast": cast[:6], **_sounds_of(profile)}


def _sounds_of(profile: dict) -> dict:
    """{"sounds": {kinds, on, booms_per_min}}: the effects the channel's videos were heard to play, which its renderer
    makes in code; {} when the look heard none."""
    sfx = (profile.get("sound") or {}).get("sfx") or {}
    kinds = [str(k)[:60] for k in (sfx.get("kinds") or []) if str(k).strip()][:10]
    if not kinds and not isinstance(sfx.get("booms_per_min"), (int, float)):
        return {}
    out = {"kinds": kinds, "on": [str(k)[:120] for k in (sfx.get("on") or [])][:6]}
    if isinstance(sfx.get("booms_per_min"), (int, float)):
        out["booms_per_min"] = round(float(sfx["booms_per_min"]), 2)
    return {"sounds": out}


# Studio's stock cues that read as generic sound design (the owner, 2026-10-26: "not use generic slop sound effects...
# the bell sounds and the one that goes braam"): a renderer may use one only when the channel's own videos do
GENERIC = {"braam": ("braam", "boom", "impact", "trailer"), "sub_drop": ("boom", "drop", "bass", "sub"),
           "riser": ("riser", "rise", "build", "swell"), "sting": ("sting", "stinger", "orchestra hit"),
           "ping": ("ping", "bell", "ding", "chime", "notification")}


def sound_problems(code: str, names, heard: dict, out_dir: Path, *, home=None, dims: int = 2) -> list:
    """What to tell Claude about a renderer's sound: its own sounds that can't be made, names it plays that are
    nobody's, and Studio's stock cues this channel's videos don't use (heard: what the look heard, {"sounds": ...})."""
    try:
        files, problems = make_sounds(code, out_dir, home=home, dims=dims)
    except AnimationStopped as error:
        return [str(error)]
    unknown = sorted(n for n in set(names) if not sound_file(str(n), files))
    if unknown:
        problems.append("it plays sounds that are neither its own (sounds: {...}) nor Studio's: " + ", ".join(unknown[:8]))
    return problems + generic_sounds(set(names) - set(files), heard)


def generic_sounds(names, spec: dict) -> list:
    """What to tell Claude about stock cues its renderer plays that this channel's own videos don't ([] when none, or
    when Studio heard nothing of its sound to compare with)."""
    heard = (spec or {}).get("sounds") if isinstance((spec or {}).get("sounds"), dict) else None
    if not heard:
        return []
    words = " ".join(heard.get("kinds") or []).lower()
    booms = float(heard.get("booms_per_min") or 0)
    out = []
    for name in sorted(set(names) & set(GENERIC)):
        if any(w in words for w in GENERIC[name]) or (name in ("braam", "sub_drop") and booms >= 0.5):
            continue
        out.append(f"it plays Studio's stock \"{name}\" sound, which this channel's own videos don't use (its sounds: "
                   + (", ".join(heard.get("kinds") or []) or "none heard") + "): make the channel's own sounds in code "
                   "(sounds: {...}) and play those")
    return out


# ── the page a renderer runs in ─────────────────────────────────────────────────────────────────────────────────────

def _scene_json(spec: dict) -> str:
    """The scene as a script literal: every "<" in its words escaped (JSON has none outside its strings), so no word
    ("</SCRIPT>", "<!--") can end or comment out the page's script."""
    return json.dumps(spec).replace("<", "\\u003c")


def _safe_script(text: str) -> str:
    return str(text).replace("</script", "<\\/script").replace("<!--", "<\\!--")


_LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`')


def _squash(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def check_code(code: str, names=()) -> list:
    """What makes renderer code unfit to run before it ever reaches the page ([] when it is fine): too long, reaching
    for anything beyond the kit (the page's own policy blocks those too; this says so in plain words), or writing the
    channel's own name (names: its title and handle) on screen, as its watermark does in the frames Claude studies."""
    problems = []
    if not isinstance(code, str) or not code.strip():
        return ["the renderer is empty"]
    if len(code) > RENDERER_MAX:
        problems.append(f"the renderer is {len(code)} characters; keep it under {RENDERER_MAX}")
    for m in FORBIDDEN.finditer(code):
        problems.append(f"it uses {m.group(0)!r}, which a renderer may not: it runs offline with only the Kit")
        if len(problems) > 6:
            break
    if "Kit.renderer(" not in code:
        problems.append("it never calls Kit.renderer({...})")
    marks = [n for n in (_squash(x) for x in names or ()) if len(n) >= 4]
    for m in _LITERAL.finditer(code) if marks else ():
        if re.search(r"\bname\s*:\s*$", code[max(0, m.start() - 24):m.start()]):
            continue                                        # the renderer's own name ("dart-stick-fights") is not drawn
        if any(n in _squash(m.group(0)) for n in marks):
            problems.append(f"it writes the channel's own name on screen ({m.group(0)[:48]}): never draw its name, "
                            "handle, watermark or logo, even where its frames show them; the videos are the creator's")
            break
    return problems


# Words a renderer writes on the frame (or the 3D overlay), measured where they are drawn: the smallest font size on
# screen in pixels, with the transform applied (a customer's cloned graphics had "nearly every overlay too small to
# read at video size"), and since Studio 109 where each run of words lands: off the frame's edge, or over other words
# drawn in the same frame (the owner's Windows test: "DAY DAY 1" with the clock box printed over the number). Words
# drawn into textures or offscreen canvases (a place's sign) are not measured here; the kit's Kit.text reports words
# it had to cut to keep them in their box (window.__overflow).
TEXT_METER = ("window.__textMin=null;window.__t=0;window.__textIssues=[];(function(){const P=CanvasRenderingContext2D.prototype;"
              "let frameT=null,boxes=[];"
              "for(const k of ['fillText','strokeText']){const f=P[k];P[k]=function(s,x,y,maxW){try{const c=this.canvas;"
              "const str=String(s);if(c&&(c.id==='c'||c.id==='o')&&str.trim()){const m=/(\\d+(?:\\.\\d+)?)px/.exec(this.font||'');"
              "const t=this.getTransform();const sc=Math.sqrt(Math.abs(t.a*t.d-t.b*t.c));"
              "if(m){const px=parseFloat(m[1])*sc;"
              "if(px>0&&(!window.__textMin||px<window.__textMin.px))window.__textMin={px:Math.round(px*10)/10,"
              "text:str.slice(0,40),t:window.__t};}"
              "const mt=this.measureText(str);let l=mt.actualBoundingBoxLeft||0,r=mt.actualBoundingBoxRight||mt.width,"
              "a=mt.actualBoundingBoxAscent||0,d=mt.actualBoundingBoxDescent||0;"
              "if(maxW!==undefined&&mt.width>maxW&&mt.width>0){const q=maxW/mt.width;l*=q;r*=q;}"
              "const xs=[],ys=[];for(const [px,py] of [[x-l,y-a],[x+r,y-a],[x+r,y+d],[x-l,y+d]]){xs.push(t.a*px+t.c*py+t.e);"
              "ys.push(t.b*px+t.d*py+t.f);}const bb=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];"
              "const W=c.width,H=c.height,sl=Math.max(4,0.004*W),iss=window.__textIssues;"
              "if(iss.length<300){if(bb[0]<-sl||bb[1]<-sl||bb[2]>W+sl||bb[3]>H+sl)iss.push({kind:'off',text:str.slice(0,60),t:window.__t});"
              "if(frameT!==window.__t){frameT=window.__t;boxes=[];}"
              "if(str.trim().length>1){for(const o of boxes){if(o.s===str)continue;"
              "const ix=Math.min(bb[2],o.b[2])-Math.max(bb[0],o.b[0]),iy=Math.min(bb[3],o.b[3])-Math.max(bb[1],o.b[1]);"
              "if(ix>0&&iy>0){const mn=Math.min((bb[2]-bb[0])*(bb[3]-bb[1]),(o.b[2]-o.b[0])*(o.b[3]-o.b[1]));"
              "if(mn>0&&ix*iy>0.25*mn){iss.push({kind:'over',text:str.slice(0,60),other:o.s.slice(0,60),t:window.__t});break;}}}"
              "boxes.push({s:str,b:bb});}}}}catch(e){}return f.apply(this,arguments);};}})();")
MIN_TEXT = 0.026                       # of the frame's short side: 28 px at 1080, the least a phone viewer can read


def small_text(found: dict, spec: dict) -> str:
    """What to tell Claude when its smallest words on screen are too small to read ("" when they read), from the
    sweep's text measurement."""
    if not isinstance(found, dict) or not found.get("px"):
        return ""
    need = round(MIN_TEXT * min(int(spec.get("width") or 1920), int(spec.get("height") or 1080)))
    if float(found["px"]) >= need:
        return ""
    return (f"\"{found.get('text', '')}\" is drawn {found['px']:g} px tall at {float(found.get('t') or 0):g} s: too small "
            f"to read at video size on a phone. Make every word on screen at least {need} px (labels, numbers, notes, "
            "captions), or leave it out")


def page(spec: dict, code: str, home=None, mode: str = "render") -> str:
    """The sandboxed page: the kit, the renderer (its own script, so its errors are its own) and the bootstrap."""
    if dims_of(spec) == 3:
        return _page3d(spec, code, home, mode)
    spec = json.loads(json.dumps(spec))
    for s in (spec.get("sets") or {}).values():
        img = s.get("image") if isinstance(s, dict) else None
        if img and not str(img).startswith("data:"):
            p = Path(img)
            s["image"] = "data:%s;base64,%s" % (mimetypes.guess_type(p.name)[0] or "image/jpeg",
                                               base64.b64encode(p.read_bytes()).decode())
    # errors are caught from the first script on, so a renderer that throws while it loads says why
    catch = ("window.__err='';window.addEventListener('error',e=>{if(!window.__err)window.__err=String(e.message||e)"
             "+(e.lineno?' (line '+e.lineno+')':'')});")
    boot = ("(async()=>{try{if(window.__err)throw new Error(window.__err);"
            "if(!Kit._renderer)throw new Error('the renderer never called Kit.renderer({...})');"
            "window.__manifest=Kit.manifest();"
            "if(%s){window.__ready=true;return;}"
            "const scene=%s;await kitLoad(scene);"
            "const ctx=document.getElementById('c').getContext('2d',{willReadFrequently:true});"
            "const R=Kit._renderer;const state=R.prepare?await R.prepare(scene,Kit):null;"
            "window.renderFrame=(t)=>{try{window.__t=t;ctx.save();R.frame(ctx,t,state,Kit);ctx.restore();}"
            "catch(e){window.__err='at '+t.toFixed(2)+' s: '+String(e&&e.stack||e).slice(0,1500);throw e;}};"
            "window.__marks=Kit._marks;window.__ready=true;}"
            "catch(e){window.__err=String(e&&e.stack||e).slice(0,2000);window.__ready=true;}})();"
            % ("true" if mode == "manifest" else "false", _safe_script(_scene_json(spec))))
    return ("<!doctype html><html><head><meta charset=utf-8>"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP}\">"
            "<style>html,body{margin:0;background:#000;overflow:hidden}</style></head>"
            f"<body><canvas id=c width={int(spec.get('width', 1920))} height={int(spec.get('height', 1080))}></canvas>"
            f"<script>{catch}</script><script>{TEXT_METER}</script><script>{_safe_script(kit_js(home))}</script>"
            # the renderer in a scope of its own: its names never collide with the kit's (a renderer's own POSES
            # once stopped it loading); its line 1 stays line 1
            f"<script>(function(){{{_safe_script(code)}\n}})();</script>"
            f"<script>{boot}</script></body></html>")


def _embed(spec: dict) -> dict:
    spec = json.loads(json.dumps(spec))
    for s in (spec.get("sets") or {}).values():
        img = s.get("image") if isinstance(s, dict) else None
        if img and not str(img).startswith("data:"):
            p = Path(img)
            s["image"] = "data:%s;base64,%s" % (mimetypes.guess_type(p.name)[0] or "image/jpeg",
                                               base64.b64encode(p.read_bytes()).decode())
    return spec


def _page3d(spec: dict, code: str, home=None, mode: str = "render") -> str:
    """The 3D page (served at the kit origin): three.js by import map, the 3D kit's runtime, the renderer as its own
    module (its own scope), then the bootstrap; a WebGL canvas with a 2D overlay over it."""
    spec = _embed(spec)
    W, H = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    catch = ("window.__err='';window.addEventListener('error',e=>{if(!window.__err)window.__err=String(e.message||e)"
             "+(e.lineno?' (line '+e.lineno+')':'')});")
    imports = json.dumps({"imports": {"three": KIT_ORIGIN + "/three/three.module.js",
                                      "three/addons/": KIT_ORIGIN + "/three/addons/"}})
    boot = ("try{if(window.__err)throw new Error(window.__err);"
            "if(!window.Kit)throw new Error('the 3D kit did not load');"
            "if(!Kit._renderer)throw new Error('the renderer never called Kit.renderer({...})');"
            "window.__manifest=Kit.manifest();"
            "if(!%s){const scene=%s;await kitLoad(scene);"
            "const view=kitView(%d,%d);const R=Kit._renderer;const state=R.prepare?await R.prepare(scene,Kit):null;"
            "window.renderFrame=(t)=>{try{window.__t=t;view.overlay.clearRect(0,0,view.width,view.height);R.frame(view,t,state,Kit);}"
            "catch(e){window.__err='at '+t.toFixed(2)+' s: '+String(e&&e.stack||e).slice(0,1500);throw e;}};"
            "window.__marks=Kit._marks;}window.__ready=true;}"
            "catch(e){if(!window.__err)window.__err=String(e&&e.stack||e).slice(0,2000);window.__ready=true;}"
            % ("true" if mode == "manifest" else "false", _safe_script(_scene_json(spec)), W, H))
    return ("<!doctype html><html><head><meta charset=utf-8>"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP3D}\">"
            f"<script type=\"importmap\">{imports}</script>"
            "<style>html,body{margin:0;background:#000;overflow:hidden}canvas{position:absolute;left:0;top:0}</style>"
            f"</head><body><canvas id=c width={W} height={H}></canvas><canvas id=o width={W} height={H}></canvas>"
            f"<script>{catch}</script><script>{TEXT_METER}</script>"
            f"<script type=\"module\" src=\"{KIT_ORIGIN}/kit/core3d.js\"></script>"
            f"<script type=\"module\">{_safe_script(code)}\n</script>"
            f"<script type=\"module\">{boot}</script></body></html>")


# Chromium with the GPU (WebGL), as Studio's 3D engine renders: Metal on a Mac, Chromium's own ANGLE elsewhere, and on a
# Studio Cloud GPU machine the flags it sets for the 3D engine (F3D_CHROMIUM_ARGS: the NVIDIA L4 through Vulkan)
GL_ARGS = ["--enable-gpu", "--ignore-gpu-blocklist", "--force-color-profile=srgb", "--hide-scrollbars", "--mute-audio",
           "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
           "--disable-backgrounding-occluded-windows"] + (["--use-angle=metal"] if sys.platform == "darwin" else
                                                          os.environ.get("F3D_CHROMIUM_ARGS", "").split())
# no way out besides requests (which the page's CSP and the route refuse): WebRTC sends no UDP and no name is looked up
NO_NET_ARGS = ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--host-resolver-rules=MAP * ~NOTFOUND"]


class _Sandbox:
    """A headless Chromium page with no way out: offline, every request refused, the page's own CSP."""

    def __init__(self, html: str, width: int, height: int, timeout_ms: int = 60000, *, gl: bool = False, home=None):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        root = Path(home or HERE)
        try:
            self.browser = self._pw.chromium.launch(args=(GL_ARGS if gl else ["--disable-gpu", "--force-color-profile=srgb",
                                                                               "--font-render-hinting=none"])
                                                    + NO_NET_ARGS)
            self.context = self.browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                                    offline=True, java_script_enabled=True, accept_downloads=False)
            self.blocked = []

            def refuse(route):
                url = route.request.url
                if gl and url.startswith(KIT_ORIGIN + "/"):
                    body, kind = _kit_file(root, url[len(KIT_ORIGIN) + 1:], html)
                    if body is not None:                    # the kit's own files, read-only, from this Studio
                        route.fulfill(status=200, body=body, headers={"Content-Type": kind})
                        return
                self.blocked.append(url[:200])
                route.abort()
            self.context.route("**/*", refuse)
            self.page = self.context.new_page()
            self.page.set_default_timeout(timeout_ms)
            if gl:
                self.page.goto(KIT_ORIGIN + "/index.html", wait_until="load")
            else:
                self.page.set_content(html, wait_until="load")
            self.page.wait_for_function("window.__ready===true", timeout=120000)
        except BaseException:
            self.close()
            raise
        self.width, self.height = width, height

    def error(self) -> str:
        return str(self.page.evaluate("window.__err") or "")

    def draw(self, t: float):
        """Frame t, or AnimationStopped with the renderer's own error."""
        try:
            self.page.evaluate("(t)=>window.renderFrame(t)", float(t))
        except Exception as error:                          # noqa: BLE001 - said in the renderer's words
            said = ""
            try:
                said = self.error()
            except Exception:                               # noqa: BLE001 - the page itself is gone
                pass
            raise AnimationStopped("The animation renderer stopped while drawing "
                                   + (said or str(error).splitlines()[0])[:600]) from None

    def close(self):
        try:
            self.browser.close()
        except Exception:                                   # noqa: BLE001 - closing is best effort
            pass
        try:
            self._pw.stop()
        except Exception:                                   # noqa: BLE001
            pass


SOUND_NAME = re.compile(r"[a-z_]{1,32}")
SOUND_MAX_S = 4                        # the page renders at most 4 s of each sound
MAX_SOUNDS = 24


_MAKE_SOUNDS = """async () => {
  const R = Kit._renderer, out = {};
  for (const [name, def] of Object.entries((R && R.sounds) || {})) {
    if (!/^[a-z_]{1,32}$/.test(name) || !def || typeof def.make !== 'function') { out[name] = {error: 'not {seconds, make(ctx, Kit)}'}; continue; }
    try {
      const secs = Math.max(0.05, Math.min(4, +def.seconds || 1)), rate = 44100;
      const ctx = new OfflineAudioContext(1, Math.round(secs * rate), rate);
      await def.make(ctx, Kit);
      const d = (await ctx.startRendering()).getChannelData(0);
      let peak = 0; for (let i = 0; i < d.length; i++) { const v = Math.abs(d[i]); if (v === v && v > peak) peak = v; }
      const g = peak > 1 ? 1 / peak : 1, pcm = new Int16Array(d.length);
      for (let i = 0; i < d.length; i++) pcm[i] = Math.max(-32767, Math.min(32767, Math.round((d[i] || 0) * g * 32767)));
      const b = new Uint8Array(pcm.buffer); let s = '';
      for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
      out[name] = {pcm: btoa(s), rate, peak};
    } catch (e) { out[name] = {error: String(e && e.message || e).slice(0, 200)}; }
  }
  return out;
}"""


def make_sounds(code: str, out_dir: Path, *, home=None, dims: int = 2) -> tuple:
    """The renderer's own sounds (its `sounds: {name: {seconds, make(ctx, Kit)}}`), each rendered once in the sandbox
    to out_dir/<name>.wav: ({name: path}, [problems in Claude's words])."""
    import wave
    out_dir = Path(out_dir)
    box = _Sandbox(page({"duration": 1, "dims": dims} if dims == 3 else {"duration": 1}, code, home, mode="manifest"),
                   320, 180, gl=dims == 3, home=home)
    try:
        err = box.error()
        if err:
            raise AnimationStopped("The animation renderer could not start: " + err[:400])
        made = box.page.evaluate(_MAKE_SOUNDS) or {}
    finally:
        box.close()
    files, problems = {}, []
    # the page's answer is the renderer's to shape (it can replace the page's own checks): every name, type and size
    # is checked again here before anything is written, and only inside out_dir
    for name, row in list(made.items() if isinstance(made, dict) else [])[:MAX_SOUNDS]:
        if not isinstance(name, str) or not SOUND_NAME.fullmatch(name) or not isinstance(row, dict):
            problems.append(f"its sound {str(name)[:40]!r} has a name Studio can't use (a-z and _, up to 32)")
            continue
        if row.get("error"):
            problems.append(f"its sound \"{name}\" could not be made: {str(row['error'])[:200]}")
            continue
        rate, pcm, peak = row.get("rate"), row.get("pcm"), row.get("peak")
        if (not isinstance(rate, int) or isinstance(rate, bool) or not 8000 <= rate <= 48000 or not isinstance(pcm, str)
                or len(pcm) > (SOUND_MAX_S * 48000 * 2) * 4 // 3 + 8 or not isinstance(peak, (int, float))):
            problems.append(f"its sound \"{name}\" came back in a form Studio can't use")
            continue
        try:
            data = base64.b64decode(pcm, validate=True)
        except ValueError:
            problems.append(f"its sound \"{name}\" came back in a form Studio can't use")
            continue
        if len(data) % 2 or len(data) > SOUND_MAX_S * rate * 2:
            problems.append(f"its sound \"{name}\" is longer than {SOUND_MAX_S} s")
            continue
        if float(peak) < 0.01:
            problems.append(f"its sound \"{name}\" came out silent")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{name}.wav"
        if path.resolve().parent != out_dir.resolve():
            continue
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(data)
        files[name] = path
    return files, problems


def style_sounds(code: str, style_name: str, home=None, dims: int = 2) -> dict:
    """{sound name: "file:..." mark}: a style's own sounds, rendered once per renderer version under
    assets/sfx/styles/<style>/<version>/ (where Studio's mixer reads "file:" marks)."""
    if "sounds" not in str(code):
        return {}
    name = re.sub(r"[^a-z0-9_-]", "", str(style_name or "style").lower())[:40] or "style"
    digest = hashlib.sha256(str(code).encode("utf-8")).hexdigest()[:10]
    root = Path(home or HERE) / "assets"
    folder = root / "sfx" / "styles" / name / digest
    done = _read(folder / "sounds.json")
    if not done:
        files, problems = make_sounds(code, folder, home=home, dims=dims)
        done = {"files": {k: v.name for k, v in files.items()}, "problems": problems}
        if files or problems:
            _write(folder / "sounds.json", done)
    return {k: "file:" + (folder / v).relative_to(root).as_posix() for k, v in (done.get("files") or {}).items()
            if (folder / v).is_file()}


def manifest(code: str, home=None, dims: int = 2) -> dict:
    """What a renderer says it can draw (its Kit.renderer manifest), read in the sandbox."""
    box = _Sandbox(page({"duration": 1, "dims": dims}, code, home, mode="manifest"), 320, 180, gl=dims == 3, home=home)
    try:
        err = box.error()
        if err:
            raise AnimationStopped("The animation renderer could not start: " + err[:400])
        return box.page.evaluate("window.__manifest") or {}
    finally:
        box.close()


def render_stills(spec: dict, out_dir: Path, code: str, *, home=None, prefix: str = "r0_") -> list:
    """The scene's stills at spec["stills"] seconds, drawn by this renderer: [JPEG paths]."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    W, H = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    box = _Sandbox(page(spec, code, home), W, H, gl=dims_of(spec) == 3, home=home)
    try:
        err = box.error()
        if err:
            raise AnimationStopped("The animation renderer could not draw its test shots: " + err[:600])
        out = []
        for k, t in enumerate(spec.get("stills") or []):
            dest = out_dir / f"{prefix}{k}.jpg"
            box.draw(t)
            box.page.screenshot(path=str(dest), type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": W, "height": H})
            out.append(dest)
        err = box.error()
        if err:
            raise AnimationStopped("The animation renderer stopped while drawing: " + err[:600])
        return out
    finally:
        box.close()


BLANK_RUN_S = 2.5                      # this long with nothing on screen, mid-film, is a fault in the renderer
SWEEP_STEP = 0.25                      # seconds between the frames a sweep looks at
FROZEN_S = 3.0                         # this long with nothing on screen changing at all is a frozen picture
STILL = 0.001                          # a frame-to-frame change under this (mean of 64x36 grey, 0-255) is none at all:
                                       # frames are exact, so a breathing figure or a slow push is never "frozen"
# a sampled frame: [how much its brightness varies (under 4: nothing on screen), how much it changed since the frame
# sampled before it, and a 16x9 thumbnail of it (to tell two shots apart)]
_SPREAD = ("(t)=>{window.renderFrame(t);const o=document.createElement('canvas');"
           "o.width=64;o.height=36;const x=o.getContext('2d');"
           "for(const id of ['c','o']){const c=document.getElementById(id);if(c)x.drawImage(c,0,0,64,36);}"
           "const d=x.getImageData(0,0,64,36).data,n=d.length/4,g=new Float32Array(n);let s=0,q=0;"
           "for(let i=0,j=0;i<d.length;i+=4,j++){const l=0.3*d[i]+0.59*d[i+1]+0.11*d[i+2];g[j]=l;s+=l;q+=l*l;}"
           "const m=s/n,p=window.__prevGrey;let ch=-1;if(p&&p.length===n){ch=0;for(let j=0;j<n;j++)ch+=Math.abs(g[j]-p[j]);ch/=n;}"
           "window.__prevGrey=g;const sig=[];for(let by=0;by<9;by++)for(let bx=0;bx<16;bx++){let a=0;"
           "for(let yy=0;yy<4;yy++)for(let xx=0;xx<4;xx++)a+=g[(by*4+yy)*64+bx*4+xx];sig.push(Math.round(a/16));}"
           "return [Math.sqrt(Math.max(0,q/n-m*m)),ch,sig];}")


def sweep(spec: dict, code: str, *, home=None, step: float = SWEEP_STEP, text: dict = None, marks: list = None,
          look: dict = None, shots: list = None, shot_dir=None) -> list:
    """Every step seconds of the scene drawn and measured (no pictures saved): [(t, spread)], where spread is how much
    the frame's brightness varies (under 4: nothing on screen). The frame review before a real render. text: filled
    with the smallest words drawn on screen ({px, text, t}), when given. look (Studio 109): filled with what else the
    review reads: "moves" [(t, change since the frame before)], "sigs" {t: 16x9 thumbnail}, "texts" (words off the
    frame or over other words), "overflow" (words Kit.text had to cut) and the renderer's "manifest". shots: seconds
    whose frames are saved as JPEGs into shot_dir (for the review Claude looks at): look["shots"] [(t, path)]."""
    W, H = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    duration = float(spec.get("duration") or 0)
    box = _Sandbox(page(spec, code, home), W, H, gl=dims_of(spec) == 3, home=home)
    want = sorted({round(float(x), 3) for x in shots or [] if 0 <= float(x) < duration})
    try:
        err = box.error()
        if err:
            raise AnimationStopped("The animation renderer could not start: " + err[:600])
        out, moves, sigs, saved = [], [], {}, []
        grid = {round(k * step, 3) for k in range(int(math.ceil(duration / step - 1e-9)))}
        for t in sorted(grid | set(want)):
            if t not in grid:                               # keep the grid's own chain of changes across this frame
                box.page.evaluate("window.__gridGrey=window.__prevGrey")
            try:
                spread, change, sig = box.page.evaluate(_SPREAD, t)
            except Exception as error:                      # noqa: BLE001 - said in the renderer's words
                said = box.error()
                raise AnimationStopped("The animation renderer stopped while drawing "
                                       + (said or str(error).splitlines()[0])[:600]) from None
            if t in want and shot_dir is not None:
                dest = Path(shot_dir) / f"t{t:07.2f}.jpg"
                dest.parent.mkdir(parents=True, exist_ok=True)
                box.page.screenshot(path=str(dest), type="jpeg", quality=88, clip={"x": 0, "y": 0, "width": W, "height": H})
                saved.append((t, dest))
            if t not in grid:                               # a frame between the sweep's own: not one of its samples
                box.page.evaluate("window.__prevGrey=window.__gridGrey")
                continue
            out.append((round(t, 3), float(spread)))
            if change >= 0:
                moves.append((round(t, 3), round(float(change), 3)))
            sigs[round(t, 3)] = sig
        if text is not None:
            text.update(box.page.evaluate("window.__textMin") or {})
        if marks is not None:
            marks.extend(box.page.evaluate("window.__marks") or [])
        if look is not None:
            look.update(moves=moves, sigs=sigs, shots=saved,
                        texts=box.page.evaluate("window.__textIssues||[]") or [],
                        overflow=box.page.evaluate("window.__overflow||[]") or [],
                        manifest=box.page.evaluate("window.__manifest||null") or {})
        return out
    finally:
        box.close()


def blank_runs(samples: list, duration: float, step: float = SWEEP_STEP) -> list:
    """[(start, end)] stretches with nothing on screen at least BLANK_RUN_S long, other than a fade out over the last
    3 seconds (the film's own ending)."""
    runs, start = [], None
    for t, spread in list(samples) + [(float(duration) + step, 99.0)]:
        if spread < 4:
            start = t if start is None else start
        elif start is not None:
            end = min(float(t), float(duration))
            if end - start >= BLANK_RUN_S and start < float(duration) - 3.0:
                runs.append((round(start, 2), round(end, 2)))
            start = None
    return runs


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _before(scene: dict, at: float) -> str:
    """The actions and effects that start in the 2 seconds before a moment, in words."""
    events = sorted((e for e in list(scene.get("fx") or []) + list(scene.get("actions") or [])
                     if isinstance(e, dict) and isinstance(e.get("at"), (int, float)) and at - 2.0 <= e["at"] <= at + 0.01),
                    key=lambda e: e["at"])
    near = [f"{e.get('kind') or e.get('move')}" + (f" {e['dir']}" if e.get("dir") else "") + f" at {float(e['at']):g} s"
            for e in events]
    return ", ".join(near[-6:]) or "nothing new"


def render(spec: dict, out: Path, code: str, *, workers: int = 2, home=None, log=print, own: dict = None) -> list:
    """The scene to an mp4 (no sound) and its sound marks [(second, sound, dB)]. A frame is a pure function of its
    time, so a worker per share of the frames draws them side by side."""
    W, H = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    n = max(1, int(round(float(spec["duration"]) * FPS)))
    tmp = Path(tempfile.mkdtemp(prefix="cr_a2d_"))
    html = page(spec, code, home)
    marks = []
    workers = max(1, min(workers, 4, n))

    def share(k):
        for attempt in range(3):
            box = None
            try:
                box = _Sandbox(html, W, H, gl=dims_of(spec) == 3, home=home)
                err = box.error()
                if err:
                    raise AnimationStopped("The animation renderer could not draw this video: " + err[:600])
                if k == 0:
                    marks.extend(box.page.evaluate("window.__marks") or [])
                for i in range(k, n, workers):
                    frame = tmp / f"f_{i:05d}.jpg"
                    if frame.is_file():
                        continue
                    box.draw(i / FPS)
                    box.page.screenshot(path=str(frame), type="jpeg", quality=93,
                                        clip={"x": 0, "y": 0, "width": W, "height": H})
                err = box.error()
                if err:
                    raise AnimationStopped("The animation renderer stopped while drawing: " + err[:600])
                return
            except AnimationStopped:
                raise
            except Exception as error:                  # noqa: BLE001 - the browser driver is retried twice
                if attempt == 2:
                    raise AnimationStopped(f"Chromium stopped drawing the animation: {str(error)[:200]}") from None
                time.sleep(2 * (attempt + 1))
            finally:
                if box is not None:
                    box.close()

    try:
        started = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(share, range(workers)))
        import subprocess
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS), "-i", str(tmp / "f_%05d.jpg"),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-color_range", "tv", str(out)], check=True, capture_output=True)
        log(f"{kit_name(spec)}: {n} frames in {time.time() - started:.0f} s")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [(round(float(t), 3), sound_file(name, own), float(db)) for t, name, db in marks if sound_file(name, own)]


def sound_file(name: str, own: dict = None) -> str:
    """A sound mark as Studio's mixer names it: the style's own sound (made in code), else the kit's recording, else
    one of Studio's shared cues."""
    if own and name in own:
        return own[name]
    if name in SOUND_FILES:
        return SOUNDS + name + ".mp3"
    return name if name in SHARED_CUES else ""


def _blank(path, limit: float = 4) -> bool:
    """A frame with almost nothing in it (one flat colour; limit 8 also catches a black frame between shots)."""
    try:
        from PIL import Image, ImageStat
        stat = ImageStat.Stat(Image.open(path).convert("L").resize((160, 90)))
        return stat.stddev[0] < limit
    except Exception:                                       # noqa: BLE001 - unreadable counts as blank
        return True


# ── building a channel's renderer (during the study) ───────────────────────────────────────────────────────────────

def _blocks(text: str) -> tuple:
    """(renderer code, test scene) from Claude's answer: one ```javascript block and one ```json block."""
    code = re.findall(r"```(?:javascript|js)\s*\n(.*?)```", str(text or ""), re.S)
    scene = re.findall(r"```json\s*\n(.*?)```", str(text or ""), re.S)
    try:
        parsed = json.loads(scene[-1]) if scene else {}
    except ValueError:
        parsed = {}
    return (max(code, key=len) if code else ""), (parsed if isinstance(parsed, dict) else {})


def _test_scene(scene: dict, spec: dict) -> dict:
    """The renderer's own test scene, bounded: at most 8 s and 4 stills, the channel's stand-in colours."""
    scene = json.loads(json.dumps(scene)) if isinstance(scene, dict) else {}
    scene.pop("stress", None)
    duration = max(1.0, min(8.0, float(scene.get("duration") or 6)))
    stills = [max(0.0, min(duration - 0.05, float(t))) for t in (scene.get("stills") or []) if isinstance(t, (int, float))]
    scene.update(duration=duration, stills=(stills or [duration * 0.2, duration * 0.5, duration * 0.85])[:4],
                 **_size(spec))
    style = scene.setdefault("style", {})
    for k in ("sky", "floor", "blur"):
        style.setdefault(k, spec.get(k))
    for s in (scene.get("sets") or {}).values():
        if isinstance(s, dict):
            s.pop("image", None)                            # test shots never fetch or carry pictures
    return scene


def _size(spec: dict) -> dict:
    """The size and kit a renderer's scenes are drawn at: the video's (1080x1920 for a Shorts channel), 2D or 3D."""
    out = {"width": int(spec.get("width") or 1920), "height": int(spec.get("height") or 1080)}
    if dims_of(spec) == 3:
        out["dims"] = 3
    return out


def _stress_scene(scene: dict, spec: dict) -> dict:
    """The renderer's stress scene (every action and effect, up to 30 s), else its test scene, bounded."""
    raw = scene.get("stress") if isinstance(scene, dict) and isinstance(scene.get("stress"), dict) else scene
    stress = json.loads(json.dumps(raw)) if isinstance(raw, dict) else {}
    stress.pop("stress", None)
    stress.update(duration=max(1.0, min(30.0, float(stress.get("duration") or 6))), **_size(spec))
    style = stress.setdefault("style", {})
    for k in ("sky", "floor", "blur"):
        style.setdefault(k, spec.get(k))
    for s in (stress.get("sets") or {}).values():
        if isinstance(s, dict):
            s.pop("image", None)
    return stress


# ── what every video needs, probed during the build (Studio 109) ────────────────────────────────────────────────────
# The owner's Windows test: the renderer only knew the places of the reference videos, so a bunker story was drawn in
# supermarket aisles; 4 people stood for "100 strangers"; board text ran off the board. Every draft is now also drawn
# in two places the channel never showed (one inside, one outside, with props), with a crowd, and with every text
# parameter at its longest: what comes out the same, empty or off its box goes back to Claude with the pictures.
PROBE_INSIDE = {"prompt": "a cramped underground bunker room with bunk beds and a steel hatch, harsh strip lights",
                "inside": True, "walls": "#7d8580", "sky": ["#2a2d33", "#3a3d44"], "floor_colors": ["#4f524f", "#5d605d"],
                "light": "harsh strip lights", "crowd": 0,
                "props": ["steel hatch", "bunk beds", "shelf of canned food", "water tank", "sign: CAPACITY 40"]}
PROBE_OUTSIDE = {"prompt": "a sunny beach at midday with palm trees and a lifeguard tower", "inside": False,
                 "sky": ["#5fb8f0", "#bfe6fb"], "floor_colors": ["#e8d49a", "#d9c17e"], "light": "bright day", "crowd": 0,
                 "props": ["palm tree", "lifeguard tower", "beach umbrella"]}
PROBE_CROWD = 40
# the same room with other props, and its crowd in another time: what comes out the same draws a few stock props and
# one kind of people whatever the script says (a customer's renderers, 2026-10-27: filing cabinets in a forest for a
# 1944 tunnel escape, modern helmeted infantry at Waterloo)
PROBE_PROPS = ["wooden shoring beams", "mine cart on rails", "oil lamp", "pickaxe", "sign: TUNNEL HARRY"]
PROBE_ERA = "June 1815, the Napoleonic Wars"
PROPS_SHOW = 1.0                       # other props that change the room's thumbnail less than this drew nothing new
ERA_SHOWS = 1.0                        # and another era that changes its crowd less than this dressed nobody for it
MIN_ACTIONS = 8                        # a renderer's own actions (besides place, enter and exit): every video is made of
MIN_FRAMINGS = 4                       # them, and of its framings; a customer's had walk, point and turn, and repeated
PLACES_APART = 6.0                     # two places whose thumbnails (64x36 RGB, 0-255) differ less than this look alike
CROWD_SHOWS = 1.0                      # a crowd that changes its place's thumbnail less than this drew nobody
PROBE_WORDS = ("Somebody has already done the math for every single person in this bunker and nobody likes the "
               "answer it gives")


def _thumb(path):
    from PIL import Image
    with Image.open(path) as im:
        small = im.convert("RGB").resize((64, 36))
        return list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())


def _apart(a, b) -> float:
    return sum(abs(x - y) for p, q in zip(a, b) for x, y in zip(p, q)) / max(1, 3 * len(a))


def _long_words(most: int) -> str:
    text = PROBE_WORDS
    while len(text) < most:
        text += " " + PROBE_WORDS
    return _cut(text, most)


PROBE_SAID = {"inside": "in an underground bunker room", "outside": "on a sunny beach", "crowd": "with a crowd",
              "era": "with that crowd in 1815", "props": "in the bunker room with other props (shoring, a mine cart, an "
                                                        "oil lamp, a pickaxe, a sign)"}


def probe_words(pictures) -> str:
    """The probes among a draft's pictures, in order, as Claude reads them ("in an underground bunker room, on a sunny
    beach, ..."), by their file names (inside_0.jpg)."""
    said = [PROBE_SAID[n] for n in (Path(str(p)).name.split("_")[0] for p in pictures) if n in PROBE_SAID]
    return ", ".join(said[:-1]) + (", then " if len(said) > 1 else "") + said[-1] if said else "(none)"


def text_probe(scene: dict, man: dict):
    """The scene with every declared text parameter at its longest (words up to its declared length, else 60), or
    None when no action or effect in it takes text."""
    scene = json.loads(json.dumps(scene))
    changed = []
    for key, field in (("actions", "move"), ("fx", "kind")):
        for row in scene.get(key) or []:
            if not isinstance(row, dict):
                continue
            for name, spec in types_for(man, key, row.get(field)).items():
                if spec.get("type") == "text":
                    row[name] = _long_words(int(spec.get("max") or 60))
                elif spec.get("type") == "texts":
                    row[name] = [_long_words(int(spec.get("max") or 40))] * 3
                else:
                    continue
                changed.append(f"{row.get(field)}.{name}")
    return (scene, sorted(set(changed))) if changed else (None, [])


def vocabulary_gaps(man: dict) -> str:
    """What a renderer's manifest lacks for a whole video: its own actions (besides place, enter and exit) and its
    framings, each counted against MIN_ACTIONS and MIN_FRAMINGS; "" when it has enough or declares none to read."""
    if not man:
        return ""
    actions = [a for a in vocabulary(man.get("actions")) if a not in ("place", "enter", "exit")]
    framings = sorted({str(f).split(":")[0] for f in man.get("shots") or [] if str(f).strip()})
    short = []
    if len(actions) < MIN_ACTIONS:
        short.append(f"{len(actions)} actions ({', '.join(actions[:6]) or 'none'})")
    if len(framings) < MIN_FRAMINGS:
        short.append(f"{len(framings)} framings ({', '.join(framings) or 'none'})")
    if not short:
        return ""
    return ("your renderer has only " + " and ".join(short) + ": every video is made of them, so its videos repeat "
            "themselves. Give it what stories need, drawn the channel's way: at least " + str(MIN_ACTIONS) + " actions "
            "(moving: walk, run, crawl, climb; doing: dig, carry, build, fight, fire, hide; reacting: fall, cheer, "
            "argue, talk; and the channel's own) and at least " + str(MIN_FRAMINGS) + " framings (wide, medium, close, "
            "ground level, top-down or map, an insert of a prop), with maps and boards drawn from their parameters, "
            "never one fixed picture")


def draws_own_places(man: dict) -> bool:
    """The renderer draws every place itself in code (plates: {draw: false}); otherwise Studio draws each place's
    picture from its words. The director's new places, the build's place probe and the frame check all go by this."""
    return ((man or {}).get("plates") or {}).get("draw") is False


def untyped(man: dict) -> list:
    """"action.param" for every parameter a renderer names without saying its type."""
    return [f"{name}.{param}" for kind in ("actions", "fx") for name, entry in vocabulary((man or {}).get(kind)).items()
            for param, spec in entry["params"].items() if spec.get("type") == "any"]


def probe(scene: dict, spec: dict, code: str, out: Path, *, home=None, man: dict = None, channel: dict = None,
          stress: dict = None) -> dict:
    """What a draft does with what every video needs: {"problems" (it can't draw at all: hard), "issues" (what to
    fix, in Claude's words), "pictures" [(what, path)]}. Its test scene is drawn at its first still in a bunker room,
    on a beach and with other props (unless Studio draws each place's picture: plates.draw true), and with a crowd,
    today and in 1815; its stress scene with every text parameter at its longest; its manifest counted for a whole
    video's actions and framings."""
    out = Path(out)
    man, channel = man or {}, channel or {}
    base = _test_scene(scene, spec)
    base["stills"] = base["stills"][:1]
    problems, issues, pictures = [], [], {}

    def drawn(name, place, crowd=None):
        variant = json.loads(json.dumps(base))
        variant["sets"] = {k: {**place, **({"crowd": crowd} if crowd is not None else {})} for k in (base.get("sets") or {"place": {}})}
        wide = "wide" in [str(f).split(":")[0] for f in (man.get("shots") or [])]
        for sh in variant.get("shots") or []:
            if isinstance(sh, dict):
                sh.pop("crowd", None)
                if wide:                                    # the place itself in view, not a close-up of a face
                    sh["framing"] = "wide"
                if sh.get("set") not in variant["sets"]:
                    sh["set"] = next(iter(variant["sets"]))
        try:
            pictures[name] = render_stills(variant, out, code, home=home, prefix=name + "_")[0]
        except AnimationStopped as error:
            problems.append(f"drawn in a place your test scene never had ({place['prompt']}), it stopped: {error}")
    own_places = draws_own_places(man) if man else not uses_plates(code)    # no manifest read: its code says
    drawn("inside", PROBE_INSIDE)
    if own_places:
        drawn("outside", PROBE_OUTSIDE)
    people = bool(channel.get("cast") or (man.get("cast") or {}).get("traits"))
    if people:
        drawn("crowd", PROBE_INSIDE, PROBE_CROWD)
        drawn("era", {**PROBE_INSIDE, "era": PROBE_ERA}, PROBE_CROWD)
    if own_places:
        drawn("props", {**PROBE_INSIDE, "props": PROBE_PROPS})
    if problems:
        return {"problems": problems, "issues": [], "pictures": list(pictures.items())}
    thumbs = {k: _thumb(v) for k, v in pictures.items()}
    apart = own_places and "outside" in thumbs and _apart(thumbs["inside"], thumbs["outside"]) >= PLACES_APART
    places = "pictures" if not own_places else "any" if apart else "own"
    if own_places and "outside" in thumbs and not apart:
        issues.append("drawn in two places the channel never showed (an underground bunker room with bunk beds and a "
                      "steel hatch; a sunny beach with palm trees), your test scene came out the same in both (the "
                      "probe pictures are shown with your test shots): draw ANY place from its set's fields (prompt, "
                      "inside, walls, floor_colors, sky, light, props) in the channel's style, never only its usual "
                      "places (Kit.place.draw / Kit.room help)")
    if "crowd" in thumbs and _apart(thumbs["inside"], thumbs["crowd"]) < CROWD_SHOWS:
        issues.append(f"a place with crowd: {PROBE_CROWD} drew nobody more: draw a set's crowd (or a shot's) as that many "
                      "background extras in the channel's figure style, smaller in depth (Kit.place.crowd gives slots)")
    elif "era" in thumbs and _apart(thumbs["crowd"], thumbs["era"]) < ERA_SHOWS:
        issues.append(f"the same crowded room with era: \"{PROBE_ERA}\" came out the same as today's: every video is "
                      "set in its own time, so draw its people (clothes, headwear, weapons, kit), vehicles, buildings and "
                      "props for the set's era (a Napoleonic soldier in a shako with a musket, never a modern helmet), "
                      "in the channel's style")
    if "props" in thumbs and _apart(thumbs["inside"], thumbs["props"]) < PROPS_SHOW:
        issues.append("the same room with other props (" + ", ".join(PROBE_PROPS[:4]) + ") came out the same: draw "
                      "each prop from its words (its shape, size and colour; a sign with its words), never a stock "
                      "prop in its place, and never the same few whatever the set lists (Kit.sketch helps)")
    thin = vocabulary_gaps(man)
    if thin:
        issues.append(thin)
    if stress:
        long, which = text_probe(stress, man)
        if long:
            look = {}
            try:
                sweep(long, code, home=home, look=look)
            except AnimationStopped as error:
                problems.append(f"with long words in its text parameters ({', '.join(which[:6])}), it stopped: {error}")
            said = frame_notes({"texts": look.get("texts"), "overflow": look.get("overflow")}, {"duration": long["duration"]})
            if said:
                issues.append(f"with each text parameter at its longest ({', '.join(which[:6])}): " + "; ".join(said[:4])
                              + ". Keep every word in its box at a readable size (Kit.text.draw), lay rows out from "
                              "measured widths, or declare a shorter text:N")
    loose = untyped(man)
    if loose:
        issues.append("these parameters say no type: " + ", ".join(loose[:12]) + ". Give every action and effect "
                      "{does, params, adds}: \"number\" for a number (Studio turns \"DAY 1\" into 1), \"text:N\" for "
                      "words its box can show, and in adds what your code writes itself (a DAY label, a unit)")
    seen = [c for c in channel.get("cast") or [] if isinstance(c, dict) and isinstance(c.get("share"), (int, float))
            and c["share"] >= 0.5]
    if seen and not recurring(man):
        issues.append(f"the channel's frames show {seen[0].get('name') or 'one character'} ({str(seen[0].get('look'))[:120]}) "
                      f"in {round(seen[0]['share'] * 100)}% of its shots: if the channel keeps that character in every "
                      "video (a mascot, a host, the \"you\" of its stories), declare it in manifest.cast.recurring")
    return {"problems": problems, "issues": issues, "pictures": list(pictures.items()), "places": places}


def split_refs(refs: list) -> tuple:
    """(shown, held): the channel's frames Claude sees (at most 6) and the last one or two, held out to set the bar."""
    held = refs[-2:] if len(refs) >= 7 else refs[-1:] if len(refs) >= 5 else []
    return [r for r in refs if r not in held][:6], held


PLATES_ON = re.compile(r"plates\s*:\s*\{[^{}]*\bdraw\s*:\s*true\b")


def uses_plates(code: str) -> bool:
    """The renderer composites its characters over a picture Studio draws for each place (plates: {draw: true, ...}):
    its test shots then show a stand-in background, which the judge is told to set aside."""
    return bool(PLATES_ON.search(str(code or "")))


BUILT = ("passed", "below_bar", "unjudged", "failed")   # a build's verdicts; "unjudged": the judge never answered


def unscored(row) -> bool:
    """A draft (or a build) the likeness judge gave no score: its call failed (today's style checks used up, the
    server busy), never a likeness of 0. A customer's two studies of 2026-10-27 were saved as 0/10 that way."""
    return not isinstance((row or {}).get("overall"), (int, float))


def _rank(row) -> float:
    """How a draft ranks: its likeness (an unscored draft under every scored one), less ISSUE_COST while it can't yet
    draw what every video needs."""
    return (-10.0 if unscored(row) else float(row["overall"])) - ISSUE_COST * bool(row.get("issues"))


def bar_for(control) -> float:
    """The likeness a channel's renderer must reach: judged against its own frames by the same judge, a little under
    what the channel's own held-out frame scores (every channel differs; so does the judge's reading of it)."""
    if not isinstance(control, (int, float)) or control <= 0:
        return 5.5                                          # the channel's own frames score 6 to 7 with this judge
    return round(max(5.0, min(7.5, float(control) - 1.5)), 2)


def _own_rounds(out: Path, key: str):
    """Round files belong to the build they were written for (renderer/key): Claude's drafts for an earlier spec (a 3D
    channel once briefed with the 2D kit) are moved aside, never reused as this build's answers. A build from before
    the key file is known by its control.json, written before its first round."""
    stamp = out / "key"
    had = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else str(_read(out / "control.json").get("key") or "")
    old = [p for p in out.iterdir() if p.name.startswith("round-")] if had != key else []
    if old:
        aside, n = out / f"old-{(had or 'unkeyed')[:8]}", 1
        while aside.exists():
            aside, n = out / f"old-{(had or 'unkeyed')[:8]}-{n}", n + 1
        aside.mkdir()
        for p in old:
            p.rename(aside / p.name)
    stamp.write_text(key, encoding="utf-8")


def build(folder, spec: dict, channel: dict, *, see, judge, references: list, home=None, log=print,
          rounds: int = BUILD_ROUNDS, minutes: float = BUILD_MINUTES, names=(), brief: str = "") -> dict:
    """Claude writes this channel's renderer; the kit draws its test shots; the judge scores them against the
    channel's frames; Claude revises it (at least once), until it reaches the channel's bar or the budget runs out.
    Saved in <folder>/renderer/ (each round's answer, the best renderer, its stills and build.json), so a recovery never
    repeats a Claude call. see(prompt, images) is one Claude vision call; judge(references, candidates) returns the
    judge's scores (style/compare); references: the channel's frames, spread over its videos (the last one or two are
    held out: the judge scores them on their own once, and the best sets the bar); names: the channel's title and
    handle, which a renderer never writes on screen; brief: what the creator asked the study for, in their words (a
    customer's "ground-level battle shots with detailed units" never reached the renderer, which drew only maps).
    The brief is not part of the build's key: a study's brief never changes, and a resumed build keeps its drafts."""
    out = Path(folder) / "renderer"
    out.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps({"spec": spec, "channel": channel, "api": KIT_API,
                                     "refs": [Path(r).name for r in references]}, sort_keys=True).encode()).hexdigest()[:20]
    saved = _read(out / "build.json")
    if saved.get("key") == key and saved.get("state") in BUILT:
        return saved
    _own_rounds(out, key)
    refs = [Path(r) for r in references if Path(r).is_file()]
    if len(refs) < 3:
        result = {"key": key, "state": "failed", "why": "too few reference frames to build a renderer from"}
        _write(out / "build.json", result)
        return result
    shown, controls = split_refs(refs)
    # the bar: the same judge scores the channel's own held-out frames, on their own and once (an odd frame of the
    # channel, a host in front of a green screen, scores low; the best of two from different videos is its usual look)
    held = _read(out / "control.json")
    if held.get("key") != key:
        held = {"key": key, "frames": [p.name for p in controls], "scores": []}
        if controls:
            try:
                got = judge([str(p) for p in shown], [str(p) for p in controls])
                held["scores"] = [round(float((c if isinstance(c, dict) else {}).get("overall") or 0), 2)
                                  for c in (got.get("candidates") or [])][:len(controls)]
            except Exception as error:                      # noqa: BLE001 - without it the usual bar stands
                log(f"  renderer: the judge could not score the channel's own frames ({str(error)[:120]})")
        _write(out / "control.json", held)
    control = max(held["scores"]) if [x for x in held.get("scores") or [] if x > 0] else None
    bar = bar_for(control)
    template = _prompt("build.txt", home)
    if dims_of(spec) == 3:
        guide, sample = _prompt3d("AUTHORING.md", home), _prompt3d("example3d.js", home)
    else:
        guide = _prompt("AUTHORING.md", home)
        # a neutral example (the contract, never a look to copy), and the optional fight engine in one line
        sample = (_prompt(EXAMPLE_OWN, home) + "\n\n// ── optional: the kit's fight engine as a whole renderer (characters "
                  "on tracks, strikes and reactions, camera); a fight channel can start from it and replace its look ──\n"
                  + example(home))
    deadline = time.monotonic() + minutes * 60
    rows, best, best_code, best_scene, previous, feedback, prev_stills = [], None, "", {}, "", "", []
    named = ", ".join(dict.fromkeys(str(n) for n in names or () if str(n).strip())) or "its name"
    for r in range(rounds):
        if time.monotonic() > deadline and r:
            log("  renderer: the build's time is up")
            break
        reply = out / f"round-{r + 1}.txt"
        if reply.is_file():
            text = reply.read_text(encoding="utf-8")
        else:
            prompt = (template.replace("[AUTHORING]", guide).replace("[EXAMPLE]", sample)
                      .replace("[CHANNEL]", json.dumps(channel, ensure_ascii=False, indent=1))
                      .replace("[ROUND]", f"{r + 1} of {rounds}").replace("[BAR]", f"{bar:g}")
                      .replace("[PREVIOUS]", previous or "(none yet: this is the first draft)")
                      .replace("[FEEDBACK]", feedback or "(none yet)").replace("[NAMES]", named)
                      .replace("[BRIEF]", str(brief or "").strip()[:1500] or "(nothing more than the channel itself)")
                      .replace("[IMAGES]", f"The first {len(shown)} pictures are the channel's own frames"
                               + (f"; the last {len(prev_stills)} are your previous test shots, in order (any after "
                                  "the test shots are Studio's probes: your test scene's first still "
                                  + probe_words(prev_stills) + ")." if prev_stills else ".")))
            log(f"  renderer: Claude writes draft {r + 1}")
            text = see(prompt, [str(p) for p in shown] + [str(p) for p in prev_stills])
            if not text:
                log("  renderer: Claude gave no answer")
                break
            reply.write_text(str(text), encoding="utf-8")
        code, scene = _blocks(text)
        problems = check_code(code, names)
        row = {"round": r + 1, "problems": problems}
        stills = []
        if not problems:
            try:
                stills = render_stills(_test_scene(scene, spec), out / f"round-{r + 1}", code, home=home,
                                       prefix="shot_")
            except AnimationStopped as error:
                problems = [str(error)]
            except Exception as error:                      # noqa: BLE001 - the browser itself failed
                problems = [f"the test shots could not be drawn ({type(error).__name__}: {str(error)[:200]})"]
            if stills and all(_blank(p) for p in stills):
                problems.append("every test shot came out as one flat colour: nothing was drawn")
            if not problems:
                # Studio's own frame review: the stress scene (every action and effect) drawn frame by frame
                stress, words, played = _stress_scene(scene, spec), {}, []
                try:
                    runs = blank_runs(sweep(stress, code, home=home, text=words, marks=played), stress["duration"])
                except AnimationStopped as error:
                    runs, problems = [], [f"in your stress scene: {error}"]
                if small_text(words, stress):
                    problems.append("in your stress scene " + small_text(words, stress))
                problems += sound_problems(code, {m[1] for m in played}, channel, out / f"round-{r + 1}" / "sounds",
                                           home=home, dims=dims_of(spec))
                for a, b in runs[:3]:
                    problems.append(f"in your stress scene the picture went blank from {a:g} s to {b:g} s (after "
                                    f"{_before(stress, a)}): every action and effect must end with the picture back "
                                    "(a fade out holds black only until a later fade in)")
                if not problems:
                    try:
                        found_now = manifest(code, home, dims_of(spec))
                        probed = probe(scene, spec, code, out / f"round-{r + 1}" / "probe", home=home, man=found_now,
                                       channel=channel, stress=stress)
                    except AnimationStopped as error:
                        probed = {"problems": [str(error)], "issues": [], "pictures": []}
                    problems += probed["problems"]
                    row["issues"] = probed["issues"]
                    row["places"] = probed.get("places")
                    probes = [p for _, p in probed["pictures"]]
                    row["probes"] = [str(Path(p).relative_to(out)) for p in probes]
        row["problems"] = problems
        if problems:
            feedback = "Your last draft could not be used:\n- " + "\n- ".join(problems[:6])
            previous = code[:RENDERER_MAX]
            prev_stills = []
            rows.append(row)
            log(f"  renderer: draft {r + 1} failed: {problems[0][:160]}")
            continue
        try:
            got = judge([str(p) for p in shown], [str(p) for p in stills],
                        **({"plates": True} if uses_plates(code) else {}))
            unjudged = "" if got.get("candidates") else "the judge returned no scores"
        except Exception as error:                          # noqa: BLE001 - an unjudged draft is still a working one
            got, unjudged = {}, str(error)[:200] or type(error).__name__
            log(f"  renderer: the judge could not score draft {r + 1} ({unjudged[:120]})")
        mine = [c if isinstance(c, dict) else {} for c in got.get("candidates") or []][:len(stills)]
        overall = round(sum(float(c.get("overall") or 0) for c in mine) / max(1, len(mine)), 2) if mine else None
        characters = round(sum(float(c.get("characters") or 0) for c in mine) / max(1, len(mine)), 2) if mine else None
        row.update(overall=overall, characters=characters, control=control, bar=bar,
                   dealbreakers=(got.get("dealbreakers") or [])[:6], fixes=(got.get("fixes") or [])[:6],
                   stills=[str(Path(p).relative_to(out)) for p in stills],
                   **({"unjudged": unjudged or "no answer"} if not mine else {}))
        rows.append(row)
        issues = row.get("issues") or []
        log(f"  renderer: draft {r + 1} "
            + (f"scores {overall:g}/10 (the channel's own frame {row['control']}; bar {bar:g})" if mine
               else "has no likeness score (the judge didn't answer)")
            + (f"; {len(issues)} thing(s) every video needs still to fix" if issues else ""))
        # a draft that can't yet draw what every video needs (any place, crowds, words that fit, typed parameters)
        # ranks below one that can, unless it is clearly closer to the channel; of two unscored drafts the later one,
        # which had the probes' advice, wins a tie
        if best is None or _rank(row) > _rank(best) or (unscored(row) and unscored(best) and _rank(row) >= _rank(best)):
            best, best_code, best_scene = row, code, scene
        if mine and overall >= bar and r + 1 >= MIN_ROUNDS:   # the probes' advice went back at least once (MIN_ROUNDS);
            break                                           # it ranks the drafts but never keeps the study going
        if not mine and not issues and r + 1 >= MIN_ROUNDS:
            log("  renderer: without the judge's scores another draft has nothing to aim at; this one draws what every "
                "video needs")
            break
        if not mine:
            feedback = ("The likeness judge could not score your test shots this time (" + row["unjudged"][:160] + "). "
                        "Compare your test shots with the channel's frames yourself: keep what works and change what "
                        "differs.")
        else:
            feedback = (f"The judge compared your test shots with the channel's frames: {overall:g}/10 overall, "
                        f"characters {characters:g}/10 (the channel's own held-out frames score {control}; the bar is "
                        f"{bar:g})." + (" That clears the bar: keep everything that works and make the shots closer "
                                        "still." if overall >= bar else "")
                        + "\nWhat still differs:\n- " + "\n- ".join(str(x) for x in (row["dealbreakers"] or ["(not said)"]))
                        + "\nThe judge's fixes:\n- " + "\n- ".join(str(x) for x in (row["fixes"] or ["(none)"])))
        feedback += ("\nWhat every video needs that this draft doesn't do yet (Studio's own probes):\n- "
                     + "\n- ".join(issues) if issues else "")
        previous, prev_stills = code[:RENDERER_MAX], stills + [Path(out / p) for p in row.get("probes") or []]
    if best is None:
        result = {"key": key, "state": "failed", "rounds": rows,
                  "why": "no draft of the renderer could draw its test shots" if rows else "Claude wrote no renderer"}
        _write(out / "build.json", result)
        return result
    (out / "renderer.js").write_text(best_code, encoding="utf-8")
    _write(out / "test-scene.json", _test_scene(best_scene, spec))
    _write(out / "stress-scene.json", _stress_scene(best_scene, spec))
    try:
        found = manifest(best_code, home, dims_of(spec))
    except Exception as error:                              # noqa: BLE001 - it drew, so this is the browser's fault
        found = {}
        log(f"  renderer: its manifest could not be read ({str(error)[:120]})")
    if found:                                               # how its author calls each action: the director's model
        found["examples"] = examples_from(best_scene.get("stress") if isinstance(best_scene, dict) else None, best_scene)
        if best.get("places") == "any":                     # it draws any place: a wrong place can be re-planned
            found["places"] = "any"
    state = "unjudged" if unscored(best) else "passed" if best["overall"] >= best.get("bar", bar) else "below_bar"
    result = {"key": key, "state": state,
              "round": best["round"], "overall": best["overall"], "characters": best["characters"],
              "bar": best.get("bar", bar), "control": best.get("control"), "rounds": rows,
              "sha256": hashlib.sha256(best_code.encode("utf-8")).hexdigest(), "manifest": found, "api": KIT_API,
              "stills": best.get("stills") or [], "issues": best.get("issues") or [],
              **({"why": "the likeness check couldn't run: " + str(best.get("unjudged") or "no answer")}
                 if state == "unjudged" else {})}
    _write(out / "build.json", result)
    return result


def attach(style: dict, folder, home) -> dict:
    """The study's renderer saved with the style: assets/styles/learned/<name>/renderer-v<N>.js (a new version only
    when the code changed), its hash, manifest and build verdict in look.animated2d.renderer. Metadata only."""
    built = _read(Path(folder) / "renderer" / "build.json")
    source = Path(folder) / "renderer" / "renderer.js"
    look = style.setdefault("look", {})
    spec = look.setdefault("animated2d", {})
    if built.get("state") not in ("passed", "below_bar", "unjudged") or not source.is_file():
        spec["renderer"] = {"state": built.get("state") or "missing", "why": built.get("why") or
                            "the study wrote no working renderer"}
        return style
    code = source.read_text(encoding="utf-8")
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if digest != built.get("sha256"):
        raise AnimationStopped("The study's renderer changed after it was judged. No other renderer was used.")
    same = _keep(style, code, digest, home)
    spec["renderer"] = {"file": same.relative_to(Path(home) / "assets").as_posix(), "sha256": digest,
                        "version": int(re.sub(r"\D", "", same.stem)), "api": KIT_API, "dims": dims_of(spec),
                        "state": built["state"],
                        "overall": built.get("overall"), "bar": built.get("bar"), "control": built.get("control"),
                        "round": built.get("round"), "manifest": built.get("manifest") or {},
                        **({"issues": built["issues"]} if built.get("issues") else {}),
                        **({"why": built["why"]} if built.get("state") == "unjudged" and built.get("why") else {})}
    return style


def _keep(style: dict, code: str, digest: str, home) -> Path:
    """assets/styles/learned/<name>/renderer-v<N>.js holding this code: the version already there, else a new one."""
    name = re.sub(r"[^a-z0-9_-]", "", str(style.get("name") or ""))
    target = Path(home) / "assets" / "styles" / "learned" / name
    target.mkdir(parents=True, exist_ok=True)
    versions = sorted(target.glob("renderer-v*.js"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    same = next((p for p in versions if code_sha256(p) == digest), None)
    if same is None:
        number = (int(re.sub(r"\D", "", versions[-1].stem)) + 1) if versions else 1
        same = target / f"renderer-v{number}.js"
        same.write_text(code, encoding="utf-8", newline="")
    return same


def code_sha256(path: Path) -> str:
    """A saved renderer's hash as its code reads, the way renderer_for checks it: Windows line ends (a renderer saved
    before Studio 109 was written with them) read as the code's own, so a style is never "changed on disk" just for
    being saved on Windows. "" when the file can't be read as text."""
    try:
        return hashlib.sha256(Path(path).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    except (OSError, UnicodeDecodeError):
        return ""


def carry(style: dict, record: dict, home) -> dict:
    """An improved copy of a coded style keeps its source's renderer as last saved (Style Lab's saves included), copied
    into its own folder unchanged (the hash is checked): look.animated2d.renderer. A source without a working renderer
    passes on its state, so the copy is refused the same way and never drawn with anything else."""
    spec = style.setdefault("look", {}).setdefault("animated2d", {})
    record = dict(record or {})
    if not record.get("file"):
        spec["renderer"] = {"state": str(record.get("state") or "missing"),
                            "why": str(record.get("why") or "the style it improves has no working renderer")}
        return style
    source = Path(home) / "assets" / str(record["file"])
    code = source.read_text(encoding="utf-8") if source.is_file() else ""
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if not code or digest != record.get("sha256"):
        raise AnimationStopped("The renderer of the style being improved is missing or changed on disk. Nothing else "
                               "was used in its place; study the channel again or restore the style.")
    same = _keep(style, code, digest, home)
    spec["renderer"] = {**record, "file": same.relative_to(Path(home) / "assets").as_posix(),
                        "version": int(re.sub(r"\D", "", same.stem)), "carried_from": str(record["file"])}
    return style


SCORE_USD = 0.12                       # one piece of the channel's own music: two Suno takes on the creator's Kie account


PLATES_PER_VIDEO = 6                   # the most places a video's casting designs (clean_sets keeps 6): quoted in full
SCORE_PIECE_S = 35                     # the sound storyboard writes a piece of music every 25 to 45 s


def media_usd(style: dict) -> dict:
    """What a video in a style pays on the creator's accounts beyond its voice and its options' own prices, for quotes:
    {"plates_usd": a coded style's background pictures, "music_usd_per_piece", "piece_seconds"}. Any style with its
    own music score (cr_score, picture styles included) pays for it; {} when there is nothing else to pay."""
    look = (style or {}).get("look") or {}
    sound = look.get("sound") if isinstance(look.get("sound"), dict) else {}
    score = sound.get("score")
    scored = isinstance(score, dict) and score.get("on") is not False and sound.get("music") is not False
    coded = look.get("visuals") in PLUGINS
    if not coded and not scored:
        return {}
    found = ((look.get("animated2d") or {}).get("renderer") or {}).get("manifest") or {} if coded else {}
    drawn = coded and (found.get("plates") or {}).get("draw") is not False
    return {"plates_usd": round(PLATES_PER_VIDEO * PLATE_USD, 2) if drawn else 0.0,
            "music_usd_per_piece": SCORE_USD if scored else 0.0, "piece_seconds": SCORE_PIECE_S}


def cost_note(style: dict) -> str:
    """What a video in this animated style costs, as its renderer makes it: voice only when it draws every place in
    code (plates.draw false), else the voice and one background picture a place; plus its own music score when the
    style has one (cr_score)."""
    look = style.get("look") or {}
    found = (((look.get("animated2d") or {}).get("renderer") or {}).get("manifest") or {})
    music = (f", plus its own music, about ${SCORE_USD:.2f} a piece (one or two a short video)"
             if media_usd(style).get("music_usd_per_piece") else "")
    if (found.get("plates") or {}).get("draw") is False:
        if dims_of(look.get("animated2d")) == 3:
            return f"Your voice{music or ' only'}: every scene is rendered in 3D on this computer."
        return f"Your voice{music or ' only'}: the characters and every place are drawn on this computer."
    return (f"Your voice{music}, plus about $0.02 for each place's background picture (up to {PLATES_PER_VIDEO} a video). "
            "The characters are animated on this computer.")


def renderer_problem(info: dict, home=None) -> str:
    """Why a style's videos can't be animated now ("" when they can), without starting a browser: for readiness."""
    spec = ((info or {}).get("look") or {}).get("animated2d") or {}
    three = dims_of(spec) == 3
    if not (available3d(home) if three else available(home)):
        return ("This style renders its characters in 3D with Studio's 3D kit, which this Studio doesn't have. Update "
                "Studio." if three else "This style animates its characters with Studio's animation kit, which this "
                "Studio doesn't have. Update Studio.")
    saved = spec.get("renderer") if isinstance(spec.get("renderer"), dict) else None
    if not saved:
        if three:
            return "This style has no 3D renderer yet. Study the channel again."
        return ("This style is written in code but has no renderer saved with it. Open the style's study and choose "
                "Use pictures instead, or study the channel again." if coded(info) else "")
    if saved.get("state") in ("failed", "missing") or not saved.get("file"):
        return ("This style's study couldn't write a working animation for the channel. Open the style's study and "
                "choose Use pictures instead, or study the channel again.")
    path = Path(home or HERE) / "assets" / str(saved.get("file") or "")
    if not saved.get("file") or not path.is_file():
        return "This style's animation is missing from the Studio folder. Study the channel again."
    if code_sha256(path) != saved.get("sha256"):
        return "This style's animation changed on disk since it was saved. Study the channel again."
    return ""


def renderer_for(info: dict, home=None) -> tuple:
    """(code, manifest) a style's videos are drawn with: its own saved renderer, unchanged (the hash is checked);
    a style learned before Studio 104 keeps the kit's stick-fight renderer it was made with."""
    spec = ((info or {}).get("look") or {}).get("animated2d") or {}
    saved = spec.get("renderer") if isinstance(spec.get("renderer"), dict) else None
    if saved and saved.get("file"):
        path = Path(home or HERE) / "assets" / str(saved["file"])
        if not path.is_file():
            raise AnimationStopped("This style's animation renderer is missing from the Studio folder. Study the style "
                                   "again, or switch it to pictures; nothing else was drawn in its place.")
        code = path.read_text(encoding="utf-8")
        if hashlib.sha256(code.encode("utf-8")).hexdigest() != saved.get("sha256"):
            raise AnimationStopped("This style's animation renderer changed on disk since it was saved. Study the style "
                                   "again; a changed renderer is never used.")
        return code, saved.get("manifest") or manifest(code, home, dims_of(spec))
    if saved is not None:
        raise AnimationStopped("This style's study couldn't write a working animation renderer"
                               + (": " + str(saved["why"]) if saved.get("why") else "")
                               + ". Switch it to pictures on the style's page; nothing else is drawn in its place.")
    if dims_of(spec) == 3:
        raise AnimationStopped("This style has no 3D renderer yet. Study the channel again; nothing else is drawn in "
                               "its place.")
    if coded(info):
        raise AnimationStopped("This style is written in code but has no renderer saved with it. Switch it to pictures "
                               "on the style's page or study the channel again; nothing else is drawn in its place.")
    code = example(home)                    # a 2D style from before Studio 104: the kit's stick fights it was made with
    return code, manifest(code, home)


def coded(info: dict) -> bool:
    """A style planned as written in code (Studio 107+): its pictures come only from its own renderer."""
    route = (((info or {}).get("cr_style_clone") or {}).get("learned_v2") or {}).get("route") or {}
    return route.get("coded") is True


# ── checking what Claude writes per video ──────────────────────────────────────────────────────────────────────────

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


def _option(value, options):
    value = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return value if value in [str(o).lower() for o in options] else None


def clean_trait(spec, value):
    """A cast trait as the renderer declared it: "colour" (#rrggbb, words allowed), "bool", "number", "text", a list
    of options, a list holding one sub-spec (a list of up to 4 such things) or a dict of sub-traits. None when
    unusable."""
    if spec in ("colour", "color"):
        v = _hex(value, "")
        return v or None
    if spec == "bool":
        if value is None or value == "":
            return None
        return bool(value) if isinstance(value, (bool, int)) else str(value).lower() in ("true", "yes")
    if spec == "number":
        try:
            return round(max(-1000.0, min(1000.0, float(value))), 4)
        except (TypeError, ValueError):
            return None
    if spec == "text":
        text = str(value or "").strip()
        return text[:120] if text else None
    if isinstance(spec, list) and spec and isinstance(spec[0], (dict, list)):
        rows = value if isinstance(value, list) else []
        out = [clean_trait(spec[0], v) for v in rows[:4]]
        return [v for v in out if v not in (None, {}, [])] or None
    if isinstance(spec, list):
        return _option(value, spec)
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            value = {next(iter(spec)): value} if value not in (None, "", "none", False) else {}
        out = {k: clean_trait(sub, value.get(k)) for k, sub in spec.items()}
        out = {k: v for k, v in out.items() if v is not None}
        return out or None
    return None


# ── the renderer's vocabulary, exactly (Studio 109) ─────────────────────────────────────────────────────────────────
# The owner's Windows test: the HUD read "DAY DAY 1" and "18°C°" because the renderer wrote "DAY" and "°C" itself and
# the director sent them too; nothing said a parameter's format, and the plan was never checked against it. Every
# action and effect now says its parameters' types ({does, params, adds}, or "{day: number}: ..." in a plain string),
# and a renderer's own test scenes show how its author calls each one (examples); every plan is checked against both.

PARAM_KINDS = ("number", "text", "colour", "color", "bool", "id", "set", "numbers", "texts", "sketch")
_PARAM = re.compile(r"\s*(number|text|colou?r|bool|id|set|numbers|texts|sketch)\s*(?::\s*(\d{1,4}))?\b", re.I)
TEXT_MAX = 80                          # an untyped or unbounded text parameter keeps at most this many characters
SKETCH_KINDS = ("line", "bars", "tally", "pie", "fraction", "list", "checklist", "calendar", "number", "clock", "arrow",
                "cross", "warning", "text")


def param_spec(raw) -> dict:
    """One parameter's type as a renderer declared it: {"type": number|text|colour|bool|id|set|numbers|texts|sketch|
    options|any, "max": characters for text, "options": [...], "hint": its words}."""
    if isinstance(raw, list):
        opts = [str(o).strip().lower() for o in raw if isinstance(o, (str, int, float)) and str(o).strip()][:24]
        return {"type": "options", "options": opts} if opts else {"type": "any"}
    if isinstance(raw, dict):
        kind = str(raw.get("type") or "").lower()
        out = param_spec(f"{kind}:{raw['max']}" if raw.get("max") and kind in ("text", "texts") else kind or "any")
        return {**out, "hint": str(raw.get("hint") or raw.get("does") or "")[:80]} if out["type"] != "any" else out
    text = str(raw or "").strip()
    m = _PARAM.match(text)
    if m:
        kind = m.group(1).lower().replace("color", "colour")
        out = {"type": kind}
        if m.group(2) and kind in ("text", "texts"):
            out["max"] = max(1, min(400, int(m.group(2))))
        hint = re.sub(r"^[-\u2013]\s+", "", text[m.end():].strip(" ,;:"))
        if hint.startswith("(") and hint.endswith(")"):
            hint = hint[1:-1].strip()
        return {**out, "hint": hint[:80]} if hint else out
    if "|" in text and len(text) < 160 and re.fullmatch(r"[\w\s|'-]+", text):
        return {"type": "options", "options": [o.strip().strip("'").lower() for o in text.split("|") if o.strip()][:24]}
    return {"type": "any", "hint": text[:80]} if text else {"type": "any"}


def _signature(doc: str) -> dict:
    """{param: spec} from a plain-string doc's leading "{a, b: number, c: left|right}" ({} when it has none)."""
    m = re.match(r"\s*\{([^{}]*)\}", str(doc or ""))
    if not m:
        return {}
    out = {}
    for item in m.group(1).split(","):
        name, _, kind = item.partition(":")
        name = name.strip().strip("'\"")
        if re.fullmatch(r"[a-z_]{1,24}", name or ""):
            out[name] = param_spec(kind) if kind.strip() else {"type": "any"}
    return out


def vocabulary(table) -> dict:
    """{name: {"does", "params" {param: spec}, "adds"}} for a manifest's actions or fx, whichever way each is written:
    {does, params, adds} (Studio 109) or a plain string (its leading {...} names the parameters)."""
    out = {}
    for name, entry in (table.items() if isinstance(table, dict) else []):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,31}", str(name)):
            continue
        if isinstance(entry, dict):
            params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
            out[name] = {"does": str(entry.get("does") or entry.get("doc") or entry.get("description") or "")[:600],
                         "params": {str(k): param_spec(v) for k, v in list(params.items())[:16]
                                    if re.fullmatch(r"[a-z_]{1,24}", str(k)) and k not in RESERVED},
                         "adds": str(entry.get("adds") or "")[:200]}
        else:
            out[name] = {"does": str(entry or "")[:600], "params": {k: v for k, v in _signature(entry).items()
                                                                    if k not in RESERVED}, "adds": ""}
    return out


def _plain(value) -> bool:
    return isinstance(value, (str, int, float, bool)) or (isinstance(value, (list, dict)) and len(json.dumps(value)) <= 400)


def examples_from(*scenes) -> dict:
    """{"actions": {move: its first call's parameters}, "fx": {kind: ...}}: how a renderer's author calls each action
    and effect in its own test and stress scenes, the director's model for the parameters' form."""
    out = {"actions": {}, "fx": {}}
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        for key, field, skip in (("actions", "move", ("at", "who", "move")), ("fx", "kind", ("at", "kind"))):
            for row in scene.get(key) or []:
                name = row.get(field) if isinstance(row, dict) else None
                if isinstance(name, str) and name not in out[key] and len(out[key]) < 60:
                    out[key][name] = {k: v for k, v in row.items() if k not in skip and _plain(v)}
    return out


def types_for(man: dict, kind: str, name: str) -> dict:
    """{param: spec} an action (kind "actions") or effect ("fx") takes: as declared, and where it isn't, as its
    author's own example calls it (a number there is a number here)."""
    found = dict((vocabulary((man or {}).get(kind)).get(name) or {}).get("params") or {})
    example = (((man or {}).get("examples") or {}).get(kind) or {}).get(name) or {}
    for k, v in example.items() if isinstance(example, dict) else ():
        if k in RESERVED or (found.get(k) or {}).get("type", "any") != "any":
            continue
        if isinstance(v, bool):
            found[k] = {"type": "bool", "soft": True}
        elif isinstance(v, (int, float)):
            found[k] = {"type": "number", "soft": True}
        elif isinstance(v, list) and v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            found[k] = {"type": "numbers", "soft": True}
    return found


_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?|-?\.\d+")


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 4) if math.isfinite(float(value)) else None
    m = _NUMBER.search(str(value or ""))
    if not m:
        return None
    try:
        return round(float(m.group(0).replace(",", "")), 4)
    except ValueError:
        return None


def _cut(text, most: int) -> str:
    """Words kept whole: at most `most` characters, cut at the last space that fits (no ellipsis)."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= most:
        return text
    head = text[:most + 1]
    cut = head.rfind(" ")
    return (head[:cut] if cut >= max(1, most // 2) else text[:most]).rstrip(" ,;:-")


def coerce(spec: dict, value, cast: dict = None, sets: dict = None):
    """A parameter's value in the type its renderer declared (None when it can't be one): "DAY 1" for a number is 1,
    "18°C" is 18; text keeps whole words within its length."""
    kind = (spec or {}).get("type", "any")
    if value is None:
        return None
    if (spec or {}).get("soft"):                            # guessed from one example call: only when it fits
        got = coerce({"type": kind}, value, cast, sets)
        return got if got is not None else coerce({"type": "any"}, value, cast, sets)
    if kind == "number":
        return _number(value)
    if kind == "text":
        if isinstance(value, (dict, list)):
            return None
        text = _cut(value if not isinstance(value, float) else f"{value:g}", int(spec.get("max") or TEXT_MAX))
        return text or None
    if kind == "colour":
        return _hex(value, "") or None
    if kind == "bool":
        return clean_trait("bool", value)
    if kind == "options":
        return _option(value, spec.get("options") or [])
    if kind == "id":
        return value if isinstance(value, str) and value in (cast or {}) else None
    if kind == "set":
        return value if isinstance(value, str) and value in (sets or {}) else None
    if kind == "numbers":
        rows = value if isinstance(value, list) else _NUMBER.findall(str(value))
        out = [n for n in (_number(v) for v in rows[:12]) if n is not None]
        return out or None
    if kind == "texts":
        rows = value if isinstance(value, list) else [value]
        out = [_cut(v, int(spec.get("max") or 40)) for v in rows[:8] if isinstance(v, (str, int, float)) and str(v).strip()]
        return out or None
    if kind == "sketch":
        return clean_sketch(value)
    # untyped: what plans always carried (a number, a yes/no, short words, kept whole)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 3)
    if isinstance(value, str):
        return _cut(value, 60) or None
    return None


def clean_sketch(value):
    """What a board or screen shows (Kit.sketch): {kind, values, trend, count, part, whole, items, done, text, time,
    dir}, every field bounded; a plain string is shown as words."""
    if isinstance(value, str):
        return {"kind": "text", "text": _cut(value, 60)} if value.strip() else None
    if not isinstance(value, dict):
        return None
    out = {}
    kind = str(value.get("kind") or "").lower()
    if kind in SKETCH_KINDS:
        out["kind"] = kind
    for k in ("count", "part", "whole"):
        n = _number(value.get(k)) if value.get(k) is not None else None
        if n is not None:
            out[k] = max(-1e6, min(1e6, n))
    vals = coerce({"type": "numbers"}, value.get("values")) if value.get("values") is not None else None
    if vals:
        out["values"] = vals
    items = coerce({"type": "texts", "max": 40}, value.get("items")) if value.get("items") is not None else None
    if items:
        out["items"] = items
    if isinstance(value.get("done"), list):
        out["done"] = [bool(x) if isinstance(x, bool) else None for x in value["done"][:8]]
    for k, most in (("trend", 10), ("text", 60), ("time", 8), ("dir", 8)):
        if isinstance(value.get(k), (str, int, float)) and str(value[k]).strip():
            out[k] = _cut(value[k], most)
    return out or None


def recurring(man: dict) -> dict:
    """{id: character} the renderer declares as the channel's own recurring characters (manifest.cast.recurring)."""
    rows = ((man or {}).get("cast") or {}).get("recurring")
    return {str(k): v for k, v in (rows.items() if isinstance(rows, dict) else []) if isinstance(v, dict)}


def recurring_words(man: dict, spec: dict) -> str:
    """The channel's recurring characters, for the casting and the director: the renderer's own (exact traits), else
    the ones the look saw in most of the channel's frames (a mascot, a host), in words."""
    own = recurring(man)
    if own:
        return json.dumps(own, ensure_ascii=False) + " (the renderer draws them exactly so; keep these ids and traits)"
    seen = []
    for c in (spec or {}).get("cast") or []:
        if not isinstance(c, dict) or not c.get("look"):
            continue
        share = c.get("share")
        main = isinstance(share, (int, float)) and share >= 0.3
        if main or re.search(r"mascot|host|narrator|protagonist|main|recurring|you\b", str(c.get("role") or ""), re.I):
            seen.append(f"{c.get('name') or 'a character'}: {c['look']}"
                        + (f" (in {round(share * 100)}% of the channel's frames)" if isinstance(share, (int, float)) else ""))
    return ("; ".join(seen[:3]) + ". Draw them with the traits above as close to this look as they allow.") if seen else \
        "none (this channel has no character in every video)"


def clean_cast(raw, traits: dict = None, keep: dict = None) -> dict:
    """{id: character} keeping only the traits the renderer declared; ids are short lowercase words. keep: the
    renderer's recurring characters (manifest.cast.recurring), added when the casting left one out and marked
    "recurring" (the owner's Windows test: the channel's red-cap mascot was in the study's shots, not the video)."""
    traits = traits if isinstance(traits, dict) and traits else {"color": "colour"}
    out = {}
    rows = list(raw.items() if isinstance(raw, dict) else ((r.get("id"), r) for r in raw or [] if isinstance(r, dict)))
    keep = keep if isinstance(keep, dict) else {}
    missing = [(k, v) for k, v in keep.items() if re.sub(r"[^a-z0-9_]", "", str(k).lower())[:24] not in
               {re.sub(r"[^a-z0-9_]", "", str(key or (c or {}).get("name") or "").lower().replace(" ", "_"))[:24]
                for key, c in rows if isinstance(c, dict)}]
    rows = missing + rows
    for key, c in rows:
        if not isinstance(c, dict) or len(out) >= 8:
            continue
        ident = re.sub(r"[^a-z0-9_]", "", str(key or c.get("name") or "").lower().replace(" ", "_"))[:24]
        if not ident or ident in out:
            continue
        char = {"name": str(c.get("name") or ident)[:40]}
        for k, spec in traits.items():
            v = clean_trait(spec, c.get(k))
            if v is not None:
                char[k] = v
        if "height" in char and isinstance(char["height"], (int, float)):
            char["height"] = max(0.6, min(1.6, char["height"]))
        if keep and ident in {re.sub(r"[^a-z0-9_]", "", str(k).lower())[:24] for k in keep}:
            char["recurring"] = True
        out[ident] = char
    if not out:
        raise AnimationStopped("Claude's cast for this video was empty. Its answer is saved; nothing was drawn.")
    return out


LIGHT_MAX = 40
ERA_MAX = 60                           # a set's time: "June 1815, the Napoleonic Wars", "1944, a German POW camp", "today"


def clean_sets(raw, most: int = 6, taken=()) -> dict:
    """{id: place}: what each place is, for its picture or for the renderer that draws it (Studio 109: inside or out,
    its walls, sky, floor, light, props by their words and its crowd, so a bunker is drawn as a bunker)."""
    out = {}
    rows = raw.items() if isinstance(raw, dict) else ((r.get("id"), r) for r in raw or [] if isinstance(r, dict))
    for key, s in rows:
        if not isinstance(s, dict):
            continue
        ident = re.sub(r"[^a-z0-9_]", "", str(key or "").lower().replace(" ", "_"))[:24]
        if not ident or ident in out or ident in taken or len(out) >= most:
            continue
        sky = [_hex(x, "") for x in (s.get("sky") or [])][:2]
        floor = [_hex(x, "") for x in (s.get("floor_colors") or s.get("floor") or [])][:2]
        row = {"prompt": str(s.get("prompt") or s.get("description") or ident.replace("_", " "))[:600],
               **({"sky": sky} if len(sky) == 2 and all(sky) else {}),
               **({"floor_colors": floor} if len(floor) == 2 and all(floor) else {})}
        if isinstance(s.get("inside"), bool):
            row["inside"] = s["inside"]
        walls = _hex(s.get("walls"), "") if s.get("walls") else ""
        if walls:
            row["walls"] = walls
        if isinstance(s.get("light"), str) and s["light"].strip():
            row["light"] = _cut(s["light"], LIGHT_MAX)
        if isinstance(s.get("era"), str) and s["era"].strip():
            # what its people wear and carry, its vehicles and buildings: a Waterloo script was drawn with modern
            # helmeted infantry, a 1944 tunnel escape with filing cabinets in a forest (2026-10-27)
            row["era"] = _cut(s["era"], ERA_MAX)
        props = [_cut(x, 48) for x in (s.get("props") if isinstance(s.get("props"), list) else [])
                 if isinstance(x, (str, int, float)) and str(x).strip()][:8]
        if props:
            row["props"] = props
        crowd = _number(s.get("crowd")) if s.get("crowd") is not None else None
        if crowd is not None:
            row["crowd"] = int(max(0, min(200, crowd)))
        out[ident] = row
    if not out and not taken:
        out["place"] = {"prompt": "an empty, simple place in the channel's style"}
    return out


RESERVED = {"at", "who", "move", "target"}


def clean_plan(raw, cast: dict, sets: dict, t0: float, t1: float, man: dict = None, new_sets: int = 0) -> dict:
    """The director's shots, actions and effects inside this stretch of the narration (seconds from its start), with
    only the renderer's own actions, effects and framings, known characters and places; shots cover the stretch.
    Every parameter is checked against its type (the renderer's declared params, else its author's examples); new_sets:
    how many places the director may add (a renderer that draws every place itself), returned under "sets"."""
    raw = raw if isinstance(raw, dict) else {}
    man = man or {}
    actions_ok = set((man.get("actions") or {}).keys()) | {"place", "enter", "exit"}
    fx_ok = set((man.get("fx") or {}).keys())
    framings = [str(f).split(":")[0] for f in (man.get("shots") or ["wide", "two", "close", "medium"])]
    dur = round(t1 - t0, 3)
    added = clean_sets(raw.get("sets"), most=max(0, int(new_sets)), taken=set(sets)) if new_sets and raw.get("sets") else {}
    places = {**sets, **added}

    def at(v, default=0.0):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return default
        x = x - t0 if x >= t0 - 0.05 and t0 > 0 else x
        return round(max(0.0, min(dur, x)), 3)

    def extras(src, row, types):
        for k, v in src.items():
            if k in RESERVED or k in row or len(row) > 16 or not re.fullmatch(r"[a-z_]{1,24}", str(k)):
                continue
            got = coerce(types.get(k) or {"type": "any"}, v, cast, places)
            if got is not None:
                row[k] = got
        return row

    first_set = next(iter(places))
    shots = []
    for s in raw.get("shots") or []:
        if not isinstance(s, dict):
            continue
        framing = str(s.get("framing") or framings[0]).lower()
        kind, _, who = framing.partition(":")
        if kind not in framings:
            kind, who = framings[0], ""
        if who and who not in cast:
            who = ""
        if kind in ("close", "medium") and not who:
            who = next(iter(cast))
        row = {"start": at(s.get("start")), "set": s.get("set") if s.get("set") in places else
               (shots[-1]["set"] if shots else first_set), "framing": kind + (":" + who if who else ""),
               "move": str(s.get("move") or "static").lower() if str(s.get("move") or "static").lower() in CAMERA
               else "static", "cast": [c for c in (s.get("cast") or []) if c in cast][:4]}
        crowd = _number(s.get("crowd")) if s.get("crowd") is not None else None
        if crowd is not None:
            row["crowd"] = int(max(0, min(200, crowd)))
        shots.append(row)
    shots.sort(key=lambda s: s["start"])
    if not shots or shots[0]["start"] > 0:
        shots.insert(0, {"start": 0.0, "set": shots[0]["set"] if shots else first_set, "framing": framings[0],
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
        if not isinstance(a, dict) or a.get("who") not in cast or len(actions) >= 400:
            continue
        move = str(a.get("move") or "").lower().strip()
        if move not in actions_ok:
            continue
        row = {"at": at(a.get("at")), "who": a["who"], "move": move}
        if a.get("target") in cast and a.get("target") != a["who"]:
            row["target"] = a["target"]
        extras(a, row, types_for(man, "actions", move))
        for key in ("x", "to"):
            if isinstance(row.get(key), float):
                row[key] = max(-5.0, min(5.0, row[key]))
        if "color" in row:
            row["color"] = _hex(row["color"], "") or row.pop("color")
        actions.append(row)
    placed = {a["who"] for a in actions if a["move"] in ("place", "enter")}
    for i, ident in enumerate(cast):
        if ident not in placed:
            actions.insert(0, {"at": 0.0, "who": ident, "move": "place", "x": -2.0 if i % 2 == 0 else 2.0,
                               "face": "right" if i % 2 == 0 else "left"})
    fx = []
    for f in raw.get("fx") or []:
        if isinstance(f, dict) and f.get("kind") in fx_ok and len(fx) < 120:
            row = {"kind": f["kind"], "at": at(f.get("at"))}
            if f.get("on") in cast:
                row["on"] = f["on"]
            extras({k: v for k, v in f.items() if k not in ("kind", "on")}, row, types_for(man, "fx", f["kind"]))
            if "color" in row:
                row["color"] = _hex(row["color"], "#ffffff")
            fx.append(row)
    out = {"shots": merged, "actions": actions, "fx": fx, "duration": dur}
    if added:
        out["sets"] = {k: v for k, v in added.items() if any(sh["set"] == k for sh in merged)}
    return out


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


def _style_renderer(engine, style: str, job=None) -> tuple:
    """(code, manifest) a video is drawn with. With its job folder, the version it started with: pinned in
    animated2d/renderer.js (+ renderer.json, its hash and manifest) at the first call, so a renderer saved later (from
    Style Lab) changes only new videos; a resumed video keeps its cast, backdrops and quote."""
    if job is None:
        return renderer_for(engine.STYLE_INFO.get(style) or {})
    folder = Path(job) / "animated2d"
    pin, path = _read(folder / "renderer.json"), folder / "renderer.js"
    if pin.get("sha256") and path.is_file():
        code = path.read_text(encoding="utf-8")
        if hashlib.sha256(code.encode("utf-8")).hexdigest() != pin["sha256"]:
            raise AnimationStopped("This video's renderer changed on disk after the video started. Nothing else was "
                                   "drawn in its place.")
        return code, pin.get("manifest") or {}
    code, man = renderer_for(engine.STYLE_INFO.get(style) or {})
    saved = ((((engine.STYLE_INFO.get(style) or {}).get("look") or {}).get("animated2d") or {}).get("renderer") or {})
    if isinstance(man, dict) and not man.get("examples"):
        found = author_examples(engine.STYLE_INFO.get(style) or {}, style, code)
        if found["actions"] or found["fx"]:
            man = {**man, "examples": found}
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    _write(folder / "renderer.json", {"sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                                      "file": str(saved.get("file") or "the kit's stick fights"),
                                      "version": saved.get("version"), "manifest": man})
    return code, man


def author_examples(info: dict, style: str, code: str, home=None) -> dict:
    """How a renderer saved before Studio 109 calls its own actions: the test and stress scenes its author wrote with
    this exact code, in the study that built it (its winning round's answer) or the Style Lab workspace that saved it.
    {"actions": {}, "fx": {}} when neither holds this code."""
    root, sha = Path(home or HERE), hashlib.sha256(str(code).encode("utf-8")).hexdigest()
    scenes = []
    op = str(((info or {}).get("cr_style_clone") or {}).get("operation_id") or "")
    if re.fullmatch(r"op_[a-f0-9]{24}", op):
        folder = root / "output" / "cr-style-clone" / op / "renderer"
        built = _read(folder / "build.json")
        if built.get("sha256") == sha:
            reply = folder / f"round-{built.get('round')}.txt"
            scene = _blocks(reply.read_text(encoding="utf-8"))[1] if reply.is_file() else {}
            scenes += [scene.get("stress"), scene, _read(folder / "test-scene.json")]
    lab = root / "style-lab" / re.sub(r"[^a-z0-9_-]", "", str(style or ""))[:80]
    try:
        same = (lab / "renderer.js").is_file() and \
            hashlib.sha256((lab / "renderer.js").read_text(encoding="utf-8").encode("utf-8")).hexdigest() == sha
    except (OSError, UnicodeDecodeError):
        same = False
    if same:
        scene = _read(lab / "scene.json")
        scenes += [scene.get("stress"), scene]
    return examples_from(*[x for x in scenes if isinstance(x, dict)])


def cast_and_sets(engine, script: str, job: Path, style: str, title: str = "", man: dict = None) -> dict:
    """The video's characters, in the traits its renderer declared, and its places, designed once (casting.json)."""
    folder = Path(job) / "animated2d"
    spec = _spec(engine, style)
    man = man if man is not None else _style_renderer(engine, style, job)[1]
    cast_spec = man.get("cast") or {}
    asked = {"script": script, "prompt": _prompt("cast.txt"), "brief": engine._extra_block(), "cast": cast_spec}
    # the renderer's record is not part of the cast: a renderer saved mid-video never re-casts it (or re-draws its
    # backdrops); the older key, which hashed it, still finds a cast designed before
    bare = {k: v for k, v in spec.items() if k != "renderer"}
    key = _digest({**asked, "spec": bare})
    # a casting made under an earlier Studio (its cast.txt, or a key that hashed the renderer record) is this video's
    # own: a Resume after an update reuses it and the pictures drawn for its places, never paying for them again
    known = {key, _digest({**asked, "spec": spec})} | {_digest({**asked, "prompt": old, "spec": s2})
                                                     for old in earlier("cast.txt") for s2 in (bare, spec)}
    saved = _read(folder / "casting.json")
    if saved.get("key") in known:
        return saved
    reply = folder / "casting-reply.txt"
    if reply.is_file() and _read(folder / "casting-request.json").get("key") in known:
        text = reply.read_text(encoding="utf-8")
    else:
        engine.step(f"{kit_name(spec)}: designing the cast and the places")
        prompt = (_prompt("cast.txt").replace("[TITLE]", title or (Path(job).name.split("--")[0].replace("-", " ")))
                  .replace("[SCRIPT]", script[:24000]).replace("[STYLE]", json.dumps(spec, ensure_ascii=False))
                  .replace("[CREATOR]", engine._extra_block() or "none")
                  .replace("[MEDIUM]", "3D" if dims_of(spec) == 3 else "2D")
                  .replace("[RECURRING]", recurring_words(man, spec))
                  .replace("[DESCRIBE]", str(cast_spec.get("describe") or "the channel's characters"))
                  .replace("[TRAITS]", json.dumps(cast_spec.get("traits") or {"color": "colour"}, ensure_ascii=False)))
        _write(folder / "casting-request.json", {"key": key})
        text = engine.claude(prompt, _model("cast"), max_tokens=6000, provider="claudecode")
        reply.parent.mkdir(parents=True, exist_ok=True)
        reply.write_text(str(text), encoding="utf-8")
    raw = _json(text)
    # the channel's recurring characters are in every video, unless the creator asked for something else
    keep = {} if (engine._extra_block() or "").strip() else recurring(man)
    out = {"key": key, "cast": clean_cast(raw.get("cast"), cast_spec.get("traits"), keep), "sets": clean_sets(raw.get("sets"))}
    _write(folder / "casting.json", out)
    return out


def plate_prompt(set_: dict, spec: dict, man: dict = None) -> str:
    """The words a place's background picture is drawn from: the renderer's own plates.prompt (its look) over the
    place's words, framed for its kit (a 2D stage, or a backdrop 3D characters are composited over) and the video's
    shape (a Shorts channel's plates are vertical)."""
    look = ((man or {}).get("plates") or {}).get("prompt") or spec.get("art") or "flat 2D vector animation"
    if set_.get("era"):                                     # a place of its time (only a set that says one: the
        set_ = {**set_, "prompt": f"{set_['prompt']} ({set_['era']})"}     # plates of a video begun before keep theirs
    tall = int(spec.get("height") or 1080) > int(spec.get("width") or 1920)
    shape = "tall 9:16 (vertical)" if tall else "wide 16:9"
    empty = "EMPTY: nobody in it, no people, no figures, no characters, no animals, no text, no logos. "
    if dims_of(spec) == 3:
        return (f"{set_['prompt']}. A background plate that 3D characters will be composited over, in this style: "
                f"{look}. {empty}Seen from where the characters' camera stands, {shape}, with clear ground in the lower "
                "part of the picture for them to stand on.")
    return (f"{set_['prompt']}. A background plate for a 2D animation, in this style: {look}. {empty}"
            f"Seen straight on from eye level, {shape}. The ground is a flat, level surface across the bottom fifth "
            "of the picture with nothing standing on it in the middle. Simple shapes, soft depth, calm enough for "
            "characters to act in front of it.")


def floor_line(path) -> float:
    """Where a plate's ground meets what stands behind it, as a share of its height (0.62 to 0.9; 0.8 when nothing
    stands out): the strongest change between rows in its lower half."""
    try:
        import numpy as np
        from PIL import Image
        img = np.asarray(Image.open(path).convert("RGB").resize((320, 180)), dtype=np.float32)
    except Exception:                                   # noqa: BLE001 - an unreadable plate keeps the asked-for floor
        return 0.8
    rows = img.mean(axis=1)
    best, at = 0.0, 0.8
    for y in range(int(180 * 0.6), int(180 * 0.92)):
        above, below = rows[max(0, y - 8):y].mean(axis=0), rows[y:y + 8].mean(axis=0)
        jump = float(np.abs(above - below).sum())
        if jump > best:
            best, at = jump, y / 180
    return round(at, 3) if best >= 24 else 0.8


def plates(engine, job: Path, casting: dict, spec: dict, *, upload=None, man: dict = None) -> dict:
    """{set id: picture path}: one plate per place, drawn once on the creator's image account (a renderer that draws
    its own places in code declares plates: {"draw": false} and none is drawn)."""
    if ((man or {}).get("plates") or {}).get("draw") is False:
        return {}
    folder = Path(job) / "animated2d" / "plates"
    folder.mkdir(parents=True, exist_ok=True)
    out, todo = {}, []
    for ident, s in casting["sets"].items():
        dest = folder / f"{ident}-{_digest(plate_prompt(s, spec, man))[:10]}.jpg"
        out[ident] = dest
        if not (dest.is_file() and dest.stat().st_size > 5000):
            todo.append((ident, s, dest))
    if todo:
        engine.step(f"{kit_name(spec)}: drawing {len(todo)} background plate(s) (about ${PLATE_USD * len(todo):.2f})")
        first = next(iter(out.values()))
        ref = []

        def draw(row):
            ident, s, dest = row
            if ref:
                engine.image_gen(SAME_LOOK + plate_prompt(s, spec, man), dest, label=f"plate {ident}", ref_urls=ref)
            else:
                engine.image_gen(plate_prompt(s, spec, man), dest, label=f"plate {ident}")
        # the first place is drawn alone; every other place copies its drawing style
        errors = []
        if not (first.is_file() and first.stat().st_size > 5000):
            errors += [e for e in [_try(draw, todo[0])] if e]
            todo = todo[1:]
        if todo and first.is_file():
            try:                                        # to the creator's own image account, like every reference
                import cr_media
                ref.append((upload or (lambda path: cr_media.upload(path, task="images")))(first))
            except Exception as error:                  # noqa: BLE001 - drawn from the words alone then
                engine.log(f"{kit_name(spec)}: the first plate couldn't be shared as a style reference ({str(error)[:80]})")
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(todo)))) as ex:
            errors += [e for e in ex.map(lambda r: _try(draw, r), todo) if e]
        if errors:
            engine.step(f"{kit_name(spec)}: {len(errors)} plate(s) could not be drawn ({errors[0][:120]}); those places use "
                        "the renderer's own drawn background")
    return {k: v for k, v in out.items() if v.is_file() and v.stat().st_size > 5000}


def _try(fn, row):
    try:
        fn(row)
        return ""
    except Exception as error:                          # noqa: BLE001 - reported by the caller
        return f"{type(error).__name__}: {error}"


def _type_words(spec: dict) -> str:
    kind = spec.get("type", "any")
    words = {"number": "a bare number", "colour": "#rrggbb", "bool": "true or false", "id": "a cast id",
             "set": "a set id", "numbers": "a list of numbers", "sketch": "{kind, values, trend, count, part, whole, "
             "items, done, text, time, dir}"}.get(kind, "")
    if kind == "text":
        words = f"text, at most {spec.get('max') or TEXT_MAX} characters"
    elif kind == "texts":
        words = f"a list of short texts, each at most {spec.get('max') or 40} characters"
    elif kind == "options":
        words = " | ".join(spec.get("options") or [])
    hint = spec.get("hint") or ""
    return (words + (f" ({hint})" if hint and words else hint)).strip()


def vocabulary_lines(man: dict, kind: str) -> list:
    """The director's list of a renderer's actions or effects: each one's parameters with their exact types, what the
    renderer writes by itself around them, and how its author calls it."""
    field = "move" if kind == "actions" else "kind"
    examples = ((man or {}).get("examples") or {}).get(kind) or {}
    lines = []
    for name, entry in vocabulary((man or {}).get(kind)).items():
        params = types_for(man, kind, name)
        sig = ", ".join(f"{k}: {_type_words(v)}" if _type_words(v) else k for k, v in params.items())
        does = re.sub(r"^\s*\{[^{}]*\}\s*:?\s*", "", entry["does"]) if entry["does"].lstrip().startswith("{") else entry["does"]
        line = f"- {name}" + (f" {{{sig}}}" if sig else "") + (f": {does}" if does else "")
        if entry["adds"]:
            line += f" [the renderer itself writes: {entry['adds']}; never send that]"
        if isinstance(examples.get(name), dict):
            sample = json.dumps({field: name, **examples[name]}, ensure_ascii=False)
            if len(sample) <= 260:
                line += f" e.g. {sample}"
        lines.append(line)
    return lines


NEW_SETS = 2                           # places a director may add to a stretch, for a renderer that draws its own places


def _places_words(sets: dict) -> str:
    return json.dumps({k: {f: v[f] for f in ("prompt", "era", "inside", "light", "props", "crowd") if f in v}
                       for k, v in sets.items()}, ensure_ascii=False)


def direct(engine, job: Path, casting: dict, spec: dict, part: tuple, index: int, previous: str, title: str,
           man: dict = None, fix: list = None, before: dict = None) -> dict:
    """One stretch of narration choreographed by Claude in the renderer's own actions (director-NNN.json), checked.
    fix: what the frame check found in the plan before (before), for the one re-plan (director-NNN-fix.*)."""
    t0, t1, lines = part
    man = man or {}
    folder = Path(job) / "animated2d"
    import cr_language
    # a video in another language: the words this stretch draws on screen are in it too (none of this for English, so
    # an English video's saved plans keep their key)
    template = cr_language.screen_rule(cr_language.of_job(job) or cr_language.current()) + _prompt("director.txt")
    beats = beats_text(lines)
    docs = {"actions": man.get("actions") or {}, "shots": man.get("shots") or [], "fx": man.get("fx") or {},
            "notes": man.get("notes") or "", "examples": man.get("examples") or {}}
    own_places = draws_own_places(man)
    review = ""
    if fix:
        review = ("\nA CHECK OF YOUR PLAN FOR THIS STRETCH (its frames, drawn) FOUND, at seconds on the narration's clock:\n- "
                  + "\n- ".join(str(x)[:400] for x in fix[:12])
                  + "\nRewrite the whole plan for this stretch so every one of these is fixed; keep what works.\nYOUR PLAN "
                  "WAS: " + json.dumps({k: (before or {}).get(k) for k in ("shots", "actions", "fx", "sets")
                                        if (before or {}).get(k)}, ensure_ascii=False)[:12000] + "\n")
    key = _digest({"beats": beats, "cast": casting["cast"], "sets": casting["sets"], "spec": spec, "prompt": template,
                   "previous": previous, "t": [t0, t1], "docs": docs, **({"fix": review} if fix else {})})
    # a stretch directed under an earlier Studio (its director.txt, no examples yet) keeps its saved answer on Resume
    known = {key} | ({_digest({"beats": beats, "cast": casting["cast"], "sets": casting["sets"], "spec": spec, "prompt": old,
                               "previous": previous, "t": [t0, t1],
                               "docs": {k: v for k, v in docs.items() if k != "examples"}})
                      for old in earlier("director.txt")} if not fix else set())
    name = f"director-{index:03d}" + ("-fix" if fix else "")
    reply = folder / f"{name}.txt"
    request = folder / f"{name}.json"
    if reply.is_file() and _read(request).get("key") in known:
        text = reply.read_text(encoding="utf-8")
    else:
        engine.step(f"{kit_name(spec)}: choreographing {t0:.0f}-{t1:.0f} s" + (" again, from the frame check" if fix else ""))
        prompt = (template.replace("[TITLE]", title).replace("[STYLE]", json.dumps(spec, ensure_ascii=False))
                  .replace("[CAST]", json.dumps(casting["cast"], ensure_ascii=False))
                  .replace("[RECURRING]", ", ".join(k for k, c in casting["cast"].items() if c.get("recurring"))
                           or recurring_words(man, spec))
                  .replace("[SETS]", _places_words(casting["sets"]))
                  .replace("[ACTIONS]", "\n".join(vocabulary_lines(man, "actions")))
                  .replace("[SHOTS]", ", ".join(str(s) for s in docs["shots"]) or "wide, two, close, medium")
                  .replace("[FX]", "\n".join(vocabulary_lines(man, "fx")) or "(none)")
                  .replace("[NOTES]", docs["notes"] or "(none)")
                  .replace("[MEDIUM]", "3D" if dims_of(spec) == 3 else "2D").replace("[PACE]", pace_words(spec))
                  .replace("[PLACE]", next((line[len("- place "):] for line in vocabulary_lines(man, "actions")
                                            if line.startswith("- place ")), "") or "{x, face: left|right}")
                  .replace("[NEWSETS]", ("NEW PLACES: when the narration goes somewhere none of the places above is (a "
                                         "hatch, a tunnel, the surface), add it under \"sets\" in your reply, at most "
                                         f"{NEW_SETS}, with a place's fields (prompt, era, inside, walls, sky, "
                                         "floor_colors, light, props, crowd), and use its id in the shots. The renderer "
                                         "draws any place, in its era.")
                           if own_places else "")
                  .replace("[NEWSETS_REPLY]", ',\n "sets": {}' if own_places else "")
                  .replace("[REVIEW]", review)
                  .replace("[START]", f"{t0:.2f}").replace("[END]", f"{t1:.2f}").replace("[BEATS]", beats)
                  .replace("[PREVIOUS]", previous or "This is the opening: place everyone first.")
                  .replace("[CREATOR]", engine._extra_block() or "none"))
        if fix and "[REVIEW]" not in template:
            prompt += "\n" + review
        _write(request, {"key": key})
        text = engine.claude(prompt, _model("director"), max_tokens=12000, provider="claudecode")
        reply.write_text(str(text), encoding="utf-8")
    return clean_plan(_json(text), casting["cast"], casting["sets"], t0, t1, man, new_sets=NEW_SETS if own_places else 0)


SET_FIELDS = ("sky", "floor_colors", "prompt", "inside", "walls", "light", "props", "crowd", "era")


def film_spec(plan: dict, casting: dict, spec: dict, plate_paths: dict, words: list = None, size=None) -> dict:
    sets = {}
    for ident, s in {**casting["sets"], **(plan.get("sets") or {})}.items():
        row = {k: v for k, v in s.items() if k in SET_FIELDS}
        if ident in plate_paths:
            row["image"] = str(plate_paths[ident])
            row["floor"] = floor_line(plate_paths[ident])
        sets[ident] = row
    style = {"figure": spec.get("figure", 0.36), "blur": spec.get("blur", 5), "outline": spec.get("outline"),
             "impact_frames": spec.get("impact_frames", True), "sky": spec.get("sky"), "floor": spec.get("floor")}
    film = {"duration": plan["duration"], **_size({**spec, **({"width": size[0], "height": size[1]} if size else {})}),
            "style": {k: v for k, v in style.items() if v is not None},
            "cast": casting["cast"], "sets": sets, "shots": plan["shots"], "actions": plan["actions"], "fx": plan["fx"],
            "words": words or []}
    if spec.get("captions"):
        film["captions"] = True
    return film


def ending(plan: dict) -> str:
    """Where everyone is when a stretch ends, for the next stretch's director."""
    last = {}
    for a in plan["actions"]:
        last[a["who"]] = a
    shot = plan["shots"][-1] if plan["shots"] else {}
    return ("At the end of the previous stretch the place was " + str(shot.get("set") or "?") + "; last moves: "
            + "; ".join(f"{who} {a['move']}" + (f" at {a['target']}" if a.get("target") else "") for who, a in last.items())
            + ". Place everyone again at the start of this stretch where it makes sense.")


# ── the frame check before a stretch is drawn (Studio 109) ─────────────────────────────────────────────────────────
# The owner's Windows test: nothing judged the finished video's frames; only the study's test shots were scored. Each
# stretch is now checked before it is drawn in full: its plan (holds, repeats, quiet stretches, the recurring cast),
# its frames swept in the sandbox (frozen pictures, words off the frame or over other words, words cut to fit, repeated
# looks) and a few of its frames looked at by Claude (the wrong place, stray things, text, missing people, meaning).
# What the director can fix is re-planned ONCE; what only the renderer can fix is recorded and said. Costs: the sweep
# is free; the look is one Claude vision call a stretch (a minute of narration at most) and the re-plan one director
# call, both Claude usage on the creator's own Claude Code, no money.

HOLD_S = 7.0                           # one shot longer than this (or 3 of the channel's own shots) holds a view too long
QUIET_S = 5.0                          # this long with no action and no effect at all: nothing happens on screen
SAME_LOOK = 3.0                        # two shots whose 16x9 thumbnails differ less than this (0-255) look the same
REVIEW_FRAMES = 8                      # moments of a stretch Claude looks at before it is drawn (one vision call)
REVIEW_WIDTH = 768                     # the width they are sent at


def _s(t: float) -> str:
    return f"{float(t):.1f} s"


PREFS = "cr-preferences.json"          # Studio's own choices, in its folder (an update replaces only shipped files)
FRAME_CHECK = "animation_frame_check"  # the Studio-wide default: on unless the creator switched it off
FRAME_CHECK_ASKED = "cr_frame_check"   # run_spec.json: "on" or "off" for one video (Create's switch, make_videos)
FRAME_CHECK_KEPT = "frame-check.json"  # that choice, kept in the video's animated2d/ folder for its Resumes
# Claude time the check adds per minute of video: one look a stretch (a minute at most), plus a re-plan when it finds
# something clear (owner's measurement still to come: the coordinator's estimate was 1.5 to 2.5 minutes)
FRAME_CHECK_MINUTES = (1.0, 2.5)
# what the free checks find that is clearly worth a re-plan (their words, from plan_notes and frame_notes)
CLEAR_NOTES = ("frozen picture", "drawn over", "run off the edge", "recurring character", "don't fit their box")
# REPEAT: a stretch that loops the same framing and action (the owner and a customer, 2026-10-27: "REPETITIVE");
# ERA and PROP (people or props of another time or place) are mostly the renderer's, said after the video
CLEAR_SEEN = ("PLACE", "TEXT", "MISSING", "REPEAT")


def frame_check_default(home=None) -> bool:
    """Studio's own setting: is a coded style's frame check on for new videos (default on)."""
    value = _read(Path(home or HERE) / PREFS).get(FRAME_CHECK)
    return value is not False


def set_frame_check(home, on: bool) -> bool:
    """Studio's setting for the frame check (Create's switch keeps it): on or off for every new video."""
    path = Path(home or HERE) / PREFS
    data = _read(path)
    data[FRAME_CHECK] = bool(on)
    _write(path, data)
    return bool(on)


def frame_check_for(job, home=None) -> bool:
    """Is this video's frame check on: its own choice (run_spec cr_frame_check, kept for its Resumes), else Studio's
    setting. A Resume or repair (whose spec comes from the saved project) keeps the choice the video was made with."""
    job = Path(job)
    asked = str(_read(job / "run_spec.json").get(FRAME_CHECK_ASKED) or "")
    kept = job / "animated2d" / FRAME_CHECK_KEPT
    if asked in ("on", "off"):
        if _read(kept).get(FRAME_CHECK_ASKED) != asked:
            _write(kept, {FRAME_CHECK_ASKED: asked})
        return asked == "on"
    remembered = str(_read(kept).get(FRAME_CHECK_ASKED) or "")
    return remembered == "on" if remembered in ("on", "off") else frame_check_default(home)


def can_show_places(man: dict) -> bool:
    """A wrong place can be re-planned: Studio draws each place's picture from its words, or the renderer passed the
    build's any-place probe (Studio 109). A renderer written before that draws only its own places whatever is asked."""
    return not draws_own_places(man) or (man or {}).get("places") == "any"


def replan_for(found: dict, man: dict) -> tuple:
    """(what the director is asked to fix, what only the renderer can): a re-plan only when the checks found something
    clear (a frozen picture, words over others or off the frame or cut, the recurring character missing; Claude saw
    the wrong place, broken words or someone missing); everything the director can fix goes with it then. A wrong place
    on a renderer that can't draw new places is the renderer's to fix, never a re-plan."""
    said = lambda r: (f"{_s(r['at'])}: " if r.get("at") is not None else "") + f"{r['kind'].lower()}: {r['what']}"  # noqa: E731
    seen = [r for r in found.get("seen") or [] if r.get("fix") == "director"]
    faults = []
    if not can_show_places(man):
        faults = [said(r) + " (this style's renderer draws only the places it was written with: Improve this style or "
                  "study the channel again)" for r in seen if r["kind"] == "PLACE"]
        seen = [r for r in seen if r["kind"] != "PLACE"]
    clear = any(any(k in n for k in CLEAR_NOTES) for n in found.get("notes") or []) or \
        any(r["kind"] in CLEAR_SEEN for r in seen)
    return ((list(found.get("notes") or []) + [said(r) for r in seen]) if clear else []), faults


def plan_notes(plan: dict, casting: dict, spec: dict, t0: float = 0.0) -> list:
    """What a stretch's plan does that the channel's best videos don't, in the director's terms (seconds on the
    narration's clock): a view held too long, a shot that repeats the one before, a stretch where nobody does
    anything, the channel's recurring character left out."""
    notes, shots = [], plan.get("shots") or []
    shot_s = spec.get("shot_s") if isinstance(spec, dict) else None
    hold = max(HOLD_S, 3 * shot_s) if isinstance(shot_s, (int, float)) and shot_s > 0 else HOLD_S
    for sh in shots:
        if sh["end"] - sh["start"] > hold:
            notes.append(f"{_s(t0 + sh['start'])} to {_s(t0 + sh['end'])}: one shot holds {sh['end'] - sh['start']:.0f} s "
                         "on the same view; cut on the words to another framing, place or person")
    for a, b in zip(shots, shots[1:]):
        if (a["set"], a["framing"], sorted(a["cast"]), a.get("crowd")) == (b["set"], b["framing"], sorted(b["cast"]), b.get("crowd")):
            notes.append(f"{_s(t0 + b['start'])}: this shot repeats the one before it (the same place, framing and people)")
    events = sorted({round(a["at"], 2) for a in plan.get("actions") or [] if a.get("move") != "place"} |
                    {round(f["at"], 2) for f in plan.get("fx") or []})
    marks = [0.0] + events + [float(plan.get("duration") or 0)]
    for a, b in zip(marks, marks[1:]):
        if b - a > QUIET_S:
            notes.append(f"{_s(t0 + a)} to {_s(t0 + b)}: nobody does anything for {b - a:.0f} s; give the people on "
                         "screen actions that show the words")
    rec = [k for k, c in (casting.get("cast") or {}).items() if isinstance(c, dict) and c.get("recurring")]
    if rec and float(plan.get("duration") or 0) > 12 and any(sh["cast"] for sh in shots) and \
            not any(set(sh["cast"]) & set(rec) for sh in shots):
        notes.append(f"{_s(t0)} to {_s(t0 + float(plan['duration']))}: the channel's recurring character "
                     f"({', '.join(rec)}) is in none of the shots")
    return notes


def frame_notes(look: dict, plan: dict, t0: float = 0.0, step: float = SWEEP_STEP) -> list:
    """What a stretch's swept frames show: nothing moving for seconds, words that run off the frame or over other
    words (seen in at least two sampled frames), words cut to fit their box, two shots that look the same."""
    notes = []
    moves = look.get("moves") or []
    start = None
    for t, change in list(moves) + [(float(plan.get("duration") or 0) + step, 99.0)]:
        if change < STILL:
            start = t - step if start is None else start
        elif start is not None:
            if t - step - start >= FROZEN_S:
                notes.append(f"{_s(t0 + start)} to {_s(t0 + t - step)}: nothing on screen moves (a frozen picture); "
                             "give someone an action there")
            start = None
    seen = {}
    for row in look.get("texts") or []:
        if isinstance(row, dict) and row.get("kind") in ("off", "over"):
            seen.setdefault((row["kind"], str(row.get("text")), str(row.get("other") or "")), []).append(_number(row.get("t")) or 0.0)
    for (kind, text, other), times in seen.items():
        if len(set(times)) < 2:
            continue
        where = _s(t0 + min(times))
        notes.append(f"{where}: the words \"{text}\" run off the edge of the frame; shorter words" if kind == "off" else
                     f"{where}: the words \"{text}\" are drawn over \"{other}\"; shorter words, or don't send what the "
                     "renderer writes itself")
    for row in (look.get("overflow") or [])[:4]:
        if isinstance(row, dict) and row.get("text"):
            notes.append(f"{_s(t0 + (_number(row.get('t')) or 0.0))}: the words \"{row['text']}\" don't fit their box at a "
                         "readable size; say it in fewer words")
    sigs = look.get("sigs") or {}
    if sigs:
        keys = sorted(sigs)

        def near(t):
            return sigs[min(keys, key=lambda k: abs(k - t))]
        shots = plan.get("shots") or []
        for a, b in zip(shots, shots[1:]):
            x, y = near((a["start"] + a["end"]) / 2), near((b["start"] + b["end"]) / 2)
            planned_apart = (a["set"], a["framing"]) != (b["set"], b["framing"])   # the same plan is said above
            if planned_apart and x and len(x) == len(y) and sum(abs(p - q) for p, q in zip(x, y)) / len(x) < SAME_LOOK:
                notes.append(f"{_s(t0 + b['start'])}: this shot looks the same as the one before it")
    return notes


def review_moments(plan: dict, most: int = REVIEW_FRAMES) -> list:
    """The seconds of a stretch Claude looks at: the middle of each shot, at most `most`, spread across the stretch."""
    mids = [round((sh["start"] + sh["end"]) / 2, 2) for sh in plan.get("shots") or [] if sh["end"] - sh["start"] > 0.3]
    if len(mids) > most:
        mids = [mids[round(k * (len(mids) - 1) / (most - 1))] for k in range(most)]
    return mids


def _moment_words(plan: dict, casting: dict, lines: list, t0: float, t: float) -> str:
    sh = next((x for x in plan.get("shots") or [] if x["start"] <= t < x["end"]), (plan.get("shots") or [{}])[-1])
    place = ({**casting.get("sets", {}), **(plan.get("sets") or {})}.get(sh.get("set")) or {})
    words = " ".join(w for _, _, _, ws in lines for w, wt in ws if abs(wt - (t0 + t)) <= 2.0)
    crowd = sh.get("crowd", place.get("crowd"))
    return (f"at {_s(t0 + t)}: shot in \"{sh.get('set')}\" ({str(place.get('prompt') or '')[:90]}), {sh.get('framing')}"
            + (f", about {', '.join(sh.get('cast') or [])}" if sh.get("cast") else "")
            + (f", crowd {crowd}" if crowd else "") + f". Words around it: \"{words[:240]}\"")


def look_review(engine, job: Path, index: int, plan: dict, casting: dict, spec: dict, part: tuple, title: str, man: dict,
                frames: list, key: str) -> list:
    """Claude looks at a stretch's frames before it is drawn (one vision call on the creator's Claude Code, saved in
    review-NNN.txt so a resumed video never asks twice): [{"at", "kind", "fix": director|renderer, "what"}]. [] when
    it saw nothing wrong, None when it couldn't look (no vision in this Studio, or the call failed: the video goes on)."""
    see = getattr(engine, "claude_vision", None)
    if not callable(see) or not frames:
        return None
    t0, t1, lines = part
    folder = Path(job) / "animated2d"
    reply, request = folder / f"review-{index:03d}.txt", folder / f"review-{index:03d}-request.json"
    if reply.is_file() and _read(request).get("key") == key:
        text = reply.read_text(encoding="utf-8")
    elif _read(request).get("key") == key:                  # asked before and stopped mid-call: never asked twice
        engine.log(f"{kit_name(spec)}: the frame look of this stretch was interrupted before; it isn't asked again")
        return None
    else:
        small = []
        try:
            from PIL import Image
            for t, path in frames:
                dest = Path(path).with_name(Path(path).stem + "-s.jpg")
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((REVIEW_WIDTH, REVIEW_WIDTH))
                    im.save(dest, quality=85)
                small.append((t, dest))
        except Exception:                                   # noqa: BLE001 - the full-size frames are sent then
            small = list(frames)
        moments = "\n".join(f"{k + 1}. " + _moment_words(plan, casting, lines, t0, t) for k, (t, _) in enumerate(small))
        prompt = (_prompt("review.txt").replace("[N]", str(len(small))).replace("[START]", f"{t0:.1f}")
                  .replace("[END]", f"{t1:.1f}").replace("[TITLE]", title or "")
                  .replace("[CAST]", json.dumps(casting.get("cast") or {}, ensure_ascii=False))
                  .replace("[RECURRING]", ", ".join(k for k, c in (casting.get("cast") or {}).items() if c.get("recurring"))
                           or recurring_words(man, spec))
                  .replace("[SETS]", _places_words({**casting.get("sets", {}), **(plan.get("sets") or {})}))
                  .replace("[MOMENTS]", moments))
        engine.step(f"{kit_name(spec)}: Claude looks at {len(small)} frames of {t0:.0f}-{t1:.0f} s before they are drawn")
        _write(request, {"key": key, "times": [t for t, _ in small]})
        try:
            text = see(prompt, [str(p) for _, p in small], max_tokens=3000)
        except KeyboardInterrupt:
            raise
        except BaseException as error:                      # noqa: BLE001 - the look is a check, never a stop
            engine.log(f"{kit_name(spec)}: the frame look could not run ({str(error)[:160]}); the video goes on")
            return None
        reply.write_text(str(text), encoding="utf-8")
    times = _read(request).get("times") or [t for t, _ in frames]
    try:
        found = _json(text)
    except AnimationStopped:
        return None
    out = []
    for row in (found.get("problems") if isinstance(found, dict) else None) or []:
        if not isinstance(row, dict) or not str(row.get("what") or "").strip():
            continue
        k = _number(row.get("moment"))
        at = times[int(k) - 1] if k is not None and 1 <= int(k) <= len(times) else None
        out.append({"at": round(t0 + at, 2) if at is not None else None, "kind": str(row.get("kind") or "")[:12].upper(),
                    "fix": "renderer" if str(row.get("fix") or "").lower().startswith("render") else "director",
                    "what": str(row["what"])[:300]})
    return out[:12]


def check_stretch(engine, job: Path, index: int, film: dict, plan: dict, code: str, man: dict, casting: dict, spec: dict,
                  part: tuple, title: str, *, key: str, look_at: bool = True) -> dict:
    """A stretch's plan and frames checked before it is drawn: {"notes" (free checks), "seen" (Claude's look),
    "director" (what a re-plan can fix), "renderer" (what only the renderer can)}. A renderer fault that leaves the
    screen empty stops the video here, before anything is drawn or published."""
    t0 = part[0]
    look = {}
    moments = review_moments(plan) if look_at else []
    samples = sweep(film, code, look=look, shots=moments, shot_dir=Path(job) / "animated2d" / f"review-{index:03d}")
    runs = blank_runs(samples, film["duration"])
    if runs:
        a, b = runs[0]
        raise AnimationStopped(
            f"The channel's animation would show nothing from {_clock(t0 + a)} to {_clock(t0 + b)} of "
            f"this video (after {_before(film, a)}): a fault in its renderer (version {man.get('version')}). "
            "Nothing was drawn or published. Study the channel again, or choose Use pictures instead on the "
            "style's page.")
    notes = plan_notes(plan, casting, spec, t0) + frame_notes(look, plan, t0)
    seen = look_review(engine, job, index, plan, casting, spec, part, title, man, look.get("shots") or [], key) \
        if look_at else None
    said = lambda r: (f"{_s(r['at'])}: " if r.get("at") is not None else "") + f"{r['kind'].lower()}: {r['what']}"  # noqa: E731
    return {"notes": notes, "seen": seen, "director": notes + [said(r) for r in seen or [] if r["fix"] == "director"],
            "renderer": [said(r) for r in seen or [] if r["fix"] == "renderer"]}


def _first_line(path: Path) -> str:
    try:
        return (Path(path).read_text(encoding="utf-8").splitlines() or [""])[0].strip()
    except OSError:
        return ""


def prepare(engine, script, job, style, force):
    """While the voice records: the style's renderer is checked, the cast and places are designed and the plates drawn."""
    if not available():
        raise AnimationStopped("Studio's animated 2D kit is missing. Update Studio before making this video.")
    job = Path(job)
    code, man = _style_renderer(engine, style, job)
    casting = cast_and_sets(engine, script, job, style, _first_line(job / "title.txt"), man)
    plates(engine, job, casting, _spec(engine, style), man=man)


def assemble(engine, job, mp3, srt, style, force, burn_subs):
    job = Path(job)
    spec = _spec(engine, style)
    code, man = _style_renderer(engine, style, job)       # the version this video started with
    script = (job / "script.txt").read_text(encoding="utf-8") if (job / "script.txt").is_file() else ""
    title = _first_line(job / "title.txt")
    # the cast prepare() designed while the voice recorded (from the engine's own script text)
    saved = _read(job / "animated2d" / "casting.json")
    casting = saved if saved.get("cast") and saved.get("sets") else cast_and_sets(engine, script, job, style, title, man)
    plate_paths = plates(engine, job, casting, spec, man=man)
    total = engine._audio_dur(mp3)
    lines = narration(engine, job, srt)
    if not lines:
        raise AnimationStopped("The narration timing is empty, so nothing could be choreographed.")
    parts = chunks(lines, total)
    folder = job / "motion"
    folder.mkdir(exist_ok=True)
    folder_a2d = job / "animated2d"
    timeline, segments, previous = [], [], ""
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:8]
    own = style_sounds(code, style, dims=dims_of(spec))      # the style's own effects, made in code once a version
    checked = []
    checking = frame_check_for(job)                         # Claude's look and the re-plan (on unless switched off)
    for index, part in enumerate(parts):
        plan = direct(engine, job, casting, spec, part, index, previous, title, man)
        words = [{"w": w, "t": round(t - part[0], 3)} for _, _, _, ws in part[2] for w, t in ws]
        size = (int(getattr(engine, "VIDEO_W", 1920)), int(getattr(engine, "VIDEO_H", 1080)))
        film = film_spec(plan, casting, spec, plate_paths, words, size=size)
        first = _digest([film, code_hash])
        record = folder_a2d / f"review-{index:03d}.json"
        rec = _read(record)
        if rec.get("key") == first and rec.get("final") in ("first", "fix") and not force:
            if rec["final"] == "fix":                       # checked before: the re-plan it chose (saved, not asked again)
                try:
                    fixed = direct(engine, job, casting, spec, part, index, previous, title, man, fix=rec.get("fix"),
                                   before=plan)
                    plan, film = fixed, film_spec(fixed, casting, spec, plate_paths, words, size=size)
                except AnimationStopped:                    # its saved answer no longer reads: the checked plan
                    pass
        else:
            out = folder / f"a2d_{index:03d}_{first[:8]}.mp4"
            if not (out.is_file() and out.stat().st_size > 10000) or force:
                found = check_stretch(engine, job, index, film, plan, code, man, casting, spec, part, title, key=first,
                                      look_at=checking)
                fix, faults = replan_for(found, man) if checking else ([], [])
                rec = {"key": first, "final": "first", "checked": checking, "notes": found["notes"],
                       "seen": found["seen"], "renderer": found["renderer"] + faults}
                if fix:
                    try:                                    # an unreadable re-plan, or one that trips a renderer fault:
                        fixed = direct(engine, job, casting, spec, part, index, previous, title, man,  # the checked
                                       fix=fix, before=plan)                                            # plan stands
                        again = film_spec(fixed, casting, spec, plate_paths, words, size=size)
                        after = check_stretch(engine, job, index, again, fixed, code, man, casting, spec, part, title,
                                              key=first, look_at=False)
                        plan, film = fixed, again
                        rec.update(final="fix", fix=fix, left=after["notes"])
                    except AnimationStopped as error:
                        rec.update(fix_refused=str(error)[:300])
                _write(record, rec)
        checked.append({"part": index, "from": round(part[0], 2), "replanned": rec.get("final") == "fix",
                        "found": len(rec.get("fix") or []), "left": rec.get("left") or [], "renderer": rec.get("renderer") or []})
        previous = ending(plan)
        _write(job / "animated2d" / f"film-{index:03d}.json", film)
        out = folder / f"a2d_{index:03d}_{_digest([film, code_hash])[:8]}.mp4"
        if not (out.is_file() and out.stat().st_size > 10000) or force:
            engine.step(f"{kit_name(spec)}: drawing {part[1] - part[0]:.0f} s of animation ({index + 1} of {len(parts)})")
            marks = render(film, out, code, workers=max(1, min(3, int(getattr(engine, "MOTION_WORKERS", 2)) + 1)),
                           log=engine.log, own=own)
            _write(out.with_suffix(".sfx.json"), marks)
        timeline.append(("motion", str(out), False, round(part[1] - part[0], 6)))
        segments.append((part[0], str(out), round(part[1] - part[0], 6)))
    faults = [r for c in checked for r in c["renderer"]]
    if faults:
        engine.step(f"{kit_name(spec)}: the frame check saw what this style's renderer itself draws wrong ("
                    + "; ".join(faults[:3])[:400] + "). The video goes on; Improve this style on the style's page can fix "
                    "the renderer for the next videos.")
    _write(job / "animated2d" / "delivery.json", {"schema": SCHEMA, "parts": len(parts), "seconds": total,
                                                 "renderer": man.get("name"), "renderer_version": man.get("version"),
                                                 "plates": {k: str(v) for k, v in plate_paths.items()},
                                                 "review": checked})
    engine._save_motion_plan(job, timeline, segments)
    engine.step(f"{kit_name(spec)}: assembling the film")
    rendered = engine._render_astro_segments(timeline, job, force, white_fade=0.0, style=style)
    engine._concat_segments(rendered, job / "_visual.mp4")
    burn_subs = burn_subs and not spec.get("captions")      # the renderer draws the channel's own captions
    engine._final_mux(job, engine._with_sound_design(job, mp3, timeline, style), srt, burn_subs,
                      add_qr=False, vignette=False, no_dust_ranges=[(0.0, total)], sub_center=False, no_sub_ranges=[])
    engine._keep_clean_visual(job, style)
    return job / "video.mp4"
