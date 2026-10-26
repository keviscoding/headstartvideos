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
KIT_FILES = ("core.js", "rig.js", "draw.js", "moves.js", "fx.js", "film.js", "bind.js")
EXAMPLE = "example-stick.js"             # the kit's own stick fights (styles from before renderers)
EXAMPLE_OWN = "example-own.js"          # what Claude is shown: a renderer that draws everything itself
PROMPTS = ("cast.txt", "director.txt", "build.txt", "AUTHORING.md", EXAMPLE_OWN)
VISUALS = "cr_animated2d"
VISUALS3D = "cr_animated3d"            # the same plugin for a 3D channel's renderer (cr_animated3d.py)
PLUGINS = (VISUALS, VISUALS3D)
# The 3D kit (Studio 105): the same contract, a WebGL page. Its runtime, example and guide are private like the 2D kit's;
# the page reads three.js and Studio's own 3D engines from this Studio folder through a read-only origin, nothing else.
KIT3D = "animated3d"
KIT3D_FILES = ("core3d.js", "example3d.js", "AUTHORING.md")
THREE_DIR = Path("threed") / "engine" / "vendor" / "three"
KIT_ORIGIN = "https://kit.local"
KIT_ROOTS = {"three": THREE_DIR, "kit": Path(KIT3D), "engine": Path("threed") / "engine",
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
            cast.append({"name": str(c.get("name") or "")[:40], "look": str(c["look"])[:240],
                         "role": str(c.get("role") or "character"), "color": colour_of(c["look"])})
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
            "moving": round(float(motion.get("moving") or 0), 2), "cast": cast[:6]}


# ── the page a renderer runs in ─────────────────────────────────────────────────────────────────────────────────────

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
            "window.renderFrame=(t)=>{try{ctx.save();R.frame(ctx,t,state,Kit);ctx.restore();}"
            "catch(e){window.__err='at '+t.toFixed(2)+' s: '+String(e&&e.stack||e).slice(0,1500);throw e;}};"
            "window.__marks=Kit._marks;window.__ready=true;}"
            "catch(e){window.__err=String(e&&e.stack||e).slice(0,2000);window.__ready=true;}})();"
            % ("true" if mode == "manifest" else "false", _safe_script(json.dumps(spec))))
    return ("<!doctype html><html><head><meta charset=utf-8>"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP}\">"
            "<style>html,body{margin:0;background:#000;overflow:hidden}</style></head>"
            f"<body><canvas id=c width={int(spec.get('width', 1920))} height={int(spec.get('height', 1080))}></canvas>"
            f"<script>{catch}</script><script>{_safe_script(kit_js(home))}</script>"
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
            "window.renderFrame=(t)=>{try{view.overlay.clearRect(0,0,view.width,view.height);R.frame(view,t,state,Kit);}"
            "catch(e){window.__err='at '+t.toFixed(2)+' s: '+String(e&&e.stack||e).slice(0,1500);throw e;}};"
            "window.__marks=Kit._marks;}window.__ready=true;}"
            "catch(e){if(!window.__err)window.__err=String(e&&e.stack||e).slice(0,2000);window.__ready=true;}"
            % ("true" if mode == "manifest" else "false", _safe_script(json.dumps(spec)), W, H))
    return ("<!doctype html><html><head><meta charset=utf-8>"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP3D}\">"
            f"<script type=\"importmap\">{imports}</script>"
            "<style>html,body{margin:0;background:#000;overflow:hidden}canvas{position:absolute;left:0;top:0}</style>"
            f"</head><body><canvas id=c width={W} height={H}></canvas><canvas id=o width={W} height={H}></canvas>"
            f"<script>{catch}</script>"
            f"<script type=\"module\" src=\"{KIT_ORIGIN}/kit/core3d.js\"></script>"
            f"<script type=\"module\">{_safe_script(code)}\n</script>"
            f"<script type=\"module\">{boot}</script></body></html>")


# Chromium with the GPU (WebGL), as Studio's 3D engine renders: Metal on a Mac, Chromium's own ANGLE elsewhere
GL_ARGS = ["--enable-gpu", "--ignore-gpu-blocklist", "--force-color-profile=srgb", "--hide-scrollbars", "--mute-audio",
           "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
           "--disable-backgrounding-occluded-windows"] + (["--use-angle=metal"] if sys.platform == "darwin" else [])


class _Sandbox:
    """A headless Chromium page with no way out: offline, every request refused, the page's own CSP."""

    def __init__(self, html: str, width: int, height: int, timeout_ms: int = 60000, *, gl: bool = False, home=None):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        root = Path(home or HERE)
        try:
            self.browser = self._pw.chromium.launch(args=GL_ARGS if gl else ["--disable-gpu", "--force-color-profile=srgb",
                                                                              "--font-render-hinting=none"])
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
_SPREAD = ("(t)=>{window.renderFrame(t);const o=document.createElement('canvas');"
           "o.width=64;o.height=36;const x=o.getContext('2d');"
           "for(const id of ['c','o']){const c=document.getElementById(id);if(c)x.drawImage(c,0,0,64,36);}"
           "const d=x.getImageData(0,0,64,36).data,n=d.length/4;let s=0,q=0;"
           "for(let i=0;i<d.length;i+=4){const l=0.3*d[i]+0.59*d[i+1]+0.11*d[i+2];s+=l;q+=l*l;}"
           "const m=s/n;return Math.sqrt(Math.max(0,q/n-m*m));}")


def sweep(spec: dict, code: str, *, home=None, step: float = SWEEP_STEP) -> list:
    """Every step seconds of the scene drawn and measured (no pictures saved): [(t, spread)], where spread is how much
    the frame's brightness varies (under 4: nothing on screen). The frame review before a real render."""
    W, H = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    duration = float(spec.get("duration") or 0)
    box = _Sandbox(page(spec, code, home), W, H, gl=dims_of(spec) == 3, home=home)
    try:
        err = box.error()
        if err:
            raise AnimationStopped("The animation renderer could not start: " + err[:600])
        out, t = [], 0.0
        while t < duration:
            try:
                out.append((round(t, 3), float(box.page.evaluate(_SPREAD, t))))
            except Exception as error:                      # noqa: BLE001 - said in the renderer's words
                said = box.error()
                raise AnimationStopped("The animation renderer stopped while drawing "
                                       + (said or str(error).splitlines()[0])[:600]) from None
            t += step
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


def render(spec: dict, out: Path, code: str, *, workers: int = 2, home=None, log=print) -> list:
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
        log(f"animated 2D: {n} frames in {time.time() - started:.0f} s")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [(round(float(t), 3), sound_file(name), float(db)) for t, name, db in marks if sound_file(name)]


def sound_file(name: str) -> str:
    """A sound mark as Studio's mixer names it: the kit's own recording, else one of Studio's shared cues."""
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


def split_refs(refs: list) -> tuple:
    """(shown, held): the channel's frames Claude sees (at most 6) and the last one or two, held out to set the bar."""
    held = refs[-2:] if len(refs) >= 7 else refs[-1:] if len(refs) >= 5 else []
    return [r for r in refs if r not in held][:6], held


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
          rounds: int = BUILD_ROUNDS, minutes: float = BUILD_MINUTES, names=()) -> dict:
    """Claude writes this channel's renderer; the kit draws its test shots; the judge scores them against the
    channel's frames; Claude revises it (at least once), until it reaches the channel's bar or the budget runs out.
    Saved in <folder>/renderer/ (each round's answer, the best renderer, its stills and build.json), so a recovery never
    repeats a Claude call. see(prompt, images) is one Claude vision call; judge(references, candidates) returns the
    judge's scores (style/compare); references: the channel's frames, spread over its videos (the last one or two are
    held out: the judge scores them on their own once, and the best sets the bar); names: the channel's title and
    handle, which a renderer never writes on screen."""
    out = Path(folder) / "renderer"
    out.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps({"spec": spec, "channel": channel, "api": KIT_API,
                                     "refs": [Path(r).name for r in references]}, sort_keys=True).encode()).hexdigest()[:20]
    saved = _read(out / "build.json")
    if saved.get("key") == key and saved.get("state") in ("passed", "below_bar", "failed"):
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
                      .replace("[IMAGES]", f"The first {len(shown)} pictures are the channel's own frames"
                               + (f"; the last {len(prev_stills)} are your previous test shots, in order." if prev_stills
                                  else ".")))
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
                stress = _stress_scene(scene, spec)
                try:
                    runs = blank_runs(sweep(stress, code, home=home), stress["duration"])
                except AnimationStopped as error:
                    runs, problems = [], [f"in your stress scene: {error}"]
                for a, b in runs[:3]:
                    problems.append(f"in your stress scene the picture went blank from {a:g} s to {b:g} s (after "
                                    f"{_before(stress, a)}): every action and effect must end with the picture back "
                                    "(a fade out holds black only until a later fade in)")
        row["problems"] = problems
        if problems:
            feedback = "Your last draft could not be used:\n- " + "\n- ".join(problems[:6])
            previous = code[:RENDERER_MAX]
            prev_stills = []
            rows.append(row)
            log(f"  renderer: draft {r + 1} failed: {problems[0][:160]}")
            continue
        try:
            got = judge([str(p) for p in shown], [str(p) for p in stills])
        except Exception as error:                          # noqa: BLE001 - an unjudged draft is still a working one
            got = {}
            log(f"  renderer: the judge could not score draft {r + 1} ({str(error)[:120]})")
        mine = [c if isinstance(c, dict) else {} for c in got.get("candidates") or []][:len(stills)]
        overall = round(sum(float(c.get("overall") or 0) for c in mine) / max(1, len(mine)), 2) if mine else 0.0
        characters = round(sum(float(c.get("characters") or 0) for c in mine) / max(1, len(mine)), 2) if mine else 0.0
        row.update(overall=overall, characters=characters, control=control, bar=bar,
                   dealbreakers=(got.get("dealbreakers") or [])[:6], fixes=(got.get("fixes") or [])[:6],
                   stills=[str(Path(p).relative_to(out)) for p in stills])
        rows.append(row)
        log(f"  renderer: draft {r + 1} scores {overall:g}/10 (the channel's own frame {row['control']}; bar {bar:g})")
        if best is None or overall > best["overall"]:
            best, best_code, best_scene = row, code, scene
        if overall >= bar and r + 1 >= MIN_ROUNDS:
            break
        feedback = (f"The judge compared your test shots with the channel's frames: {overall:g}/10 overall, characters "
                    f"{characters:g}/10 (the channel's own held-out frames score {control}; the bar is {bar:g})."
                    + (" That clears the bar: keep everything that works and make the shots closer still."
                       if overall >= bar else "") + "\nWhat still differs:\n- " + "\n- ".join(str(x) for x in (row["dealbreakers"] or ["(not said)"]))
                    + "\nThe judge's fixes:\n- " + "\n- ".join(str(x) for x in (row["fixes"] or ["(none)"])))
        previous, prev_stills = code[:RENDERER_MAX], stills
    if best is None:
        result = {"key": key, "state": "failed", "rounds": rows,
                  "why": "no draft of the renderer could draw its test shots" if rows else "Claude wrote no renderer"}
        _write(out / "build.json", result)
        return result
    (out / "renderer.js").write_text(best_code, encoding="utf-8")
    _write(out / "test-scene.json", _test_scene(best_scene, spec))
    try:
        found = manifest(best_code, home, dims_of(spec))
    except Exception as error:                              # noqa: BLE001 - it drew, so this is the browser's fault
        found = {}
        log(f"  renderer: its manifest could not be read ({str(error)[:120]})")
    result = {"key": key, "state": "passed" if best["overall"] >= best.get("bar", bar) else "below_bar",
              "round": best["round"], "overall": best["overall"], "characters": best["characters"],
              "bar": best.get("bar", bar), "control": best.get("control"), "rounds": rows,
              "sha256": hashlib.sha256(best_code.encode("utf-8")).hexdigest(), "manifest": found, "api": KIT_API,
              "stills": best.get("stills") or []}
    _write(out / "build.json", result)
    return result


def attach(style: dict, folder, home) -> dict:
    """The study's renderer saved with the style: assets/styles/learned/<name>/renderer-v<N>.js (a new version only
    when the code changed), its hash, manifest and build verdict in look.animated2d.renderer. Metadata only."""
    built = _read(Path(folder) / "renderer" / "build.json")
    source = Path(folder) / "renderer" / "renderer.js"
    look = style.setdefault("look", {})
    spec = look.setdefault("animated2d", {})
    if built.get("state") not in ("passed", "below_bar") or not source.is_file():
        spec["renderer"] = {"state": built.get("state") or "missing", "why": built.get("why") or
                            "the study wrote no working renderer"}
        return style
    code = source.read_text(encoding="utf-8")
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if digest != built.get("sha256"):
        raise AnimationStopped("The study's renderer changed after it was judged. No other renderer was used.")
    name = re.sub(r"[^a-z0-9_-]", "", str(style.get("name") or ""))
    target = Path(home) / "assets" / "styles" / "learned" / name
    target.mkdir(parents=True, exist_ok=True)
    versions = sorted(target.glob("renderer-v*.js"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    same = next((p for p in versions if hashlib.sha256(p.read_bytes()).hexdigest() == digest), None)
    if same is None:
        number = (int(re.sub(r"\D", "", versions[-1].stem)) + 1) if versions else 1
        same = target / f"renderer-v{number}.js"
        same.write_text(code, encoding="utf-8")
    spec["renderer"] = {"file": same.relative_to(Path(home) / "assets").as_posix(), "sha256": digest,
                        "version": int(re.sub(r"\D", "", same.stem)), "api": KIT_API, "state": built["state"],
                        "overall": built.get("overall"), "bar": built.get("bar"), "control": built.get("control"),
                        "round": built.get("round"), "manifest": built.get("manifest") or {}}
    return style


def cost_note(style: dict) -> str:
    """What a video in this animated style costs, as its renderer makes it: voice only when it draws every place in
    code (plates.draw false), else the voice and one background picture a place."""
    found = ((((style.get("look") or {}).get("animated2d") or {}).get("renderer") or {}).get("manifest") or {})
    if (found.get("plates") or {}).get("draw") is False:
        if dims_of((style.get("look") or {}).get("animated2d")) == 3:
            return "Your voice only: every scene is rendered in 3D on this computer."
        return "Your voice only: the characters and every place are drawn on this computer."
    return ("Your voice, plus about $0.02 for each place's background picture (usually 2 to 4 a video). "
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
        return "This style has no 3D renderer yet. Study the channel again." if three else ""
    if saved.get("state") in ("failed", "missing"):
        return ("This style's study couldn't write a working animation for the channel. Open the style's study and "
                "choose Use pictures instead, or study the channel again.")
    path = Path(home or HERE) / "assets" / str(saved.get("file") or "")
    if not saved.get("file") or not path.is_file():
        return "This style's animation is missing from the Studio folder. Study the channel again."
    if hashlib.sha256(path.read_bytes()).hexdigest() != saved.get("sha256"):
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
    if saved and saved.get("state") == "failed":
        raise AnimationStopped("This style's study couldn't write a working animation renderer: "
                               + str(saved.get("why") or "") + ". Switch it to pictures on the style's page.")
    if dims_of(spec) == 3:
        raise AnimationStopped("This style has no 3D renderer yet. Study the channel again; nothing else is drawn in "
                               "its place.")
    code = example(home)
    return code, manifest(code, home)


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


def clean_cast(raw, traits: dict = None) -> dict:
    """{id: character} keeping only the traits the renderer declared; ids are short lowercase words."""
    traits = traits if isinstance(traits, dict) and traits else {"color": "colour"}
    out = {}
    rows = raw.items() if isinstance(raw, dict) else ((r.get("id"), r) for r in raw or [] if isinstance(r, dict))
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
        out[ident] = char
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
        out["place"] = {"prompt": "an empty, simple place in the channel's style"}
    return out


RESERVED = {"at", "who", "move", "target"}


def clean_plan(raw, cast: dict, sets: dict, t0: float, t1: float, man: dict = None) -> dict:
    """The director's shots, actions and effects inside this stretch of the narration (seconds from its start), with
    only the renderer's own actions, effects and framings, known characters and places; shots cover the stretch."""
    raw = raw if isinstance(raw, dict) else {}
    man = man or {}
    actions_ok = set((man.get("actions") or {}).keys()) | {"place", "enter", "exit"}
    fx_ok = set((man.get("fx") or {}).keys())
    framings = [str(f).split(":")[0] for f in (man.get("shots") or ["wide", "two", "close", "medium"])]
    dur = round(t1 - t0, 3)

    def at(v, default=0.0):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return default
        x = x - t0 if x >= t0 - 0.05 and t0 > 0 else x
        return round(max(0.0, min(dur, x)), 3)

    def extras(src, row):
        for k, v in src.items():
            if k in RESERVED or k in row or len(row) > 16 or not re.fullmatch(r"[a-z_]{1,24}", str(k)):
                continue
            if isinstance(v, bool) or isinstance(v, (int, float)):
                row[k] = v if isinstance(v, bool) else round(float(v), 3)
            elif isinstance(v, str) and len(v) <= 60:
                row[k] = v.strip()
        return row

    first_set = next(iter(sets))
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
        shots.append({"start": at(s.get("start")), "set": s.get("set") if s.get("set") in sets else
                      (shots[-1]["set"] if shots else first_set), "framing": kind + (":" + who if who else ""),
                      "move": str(s.get("move") or "static").lower() if str(s.get("move") or "static").lower() in CAMERA
                      else "static", "cast": [c for c in (s.get("cast") or []) if c in cast][:4]})
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
        extras(a, row)
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
            extras({k: v for k, v in f.items() if k not in ("kind", "on")}, row)
            if "color" in row:
                row["color"] = _hex(row["color"], "#ffffff")
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


def _style_renderer(engine, style: str) -> tuple:
    return renderer_for(engine.STYLE_INFO.get(style) or {})


def cast_and_sets(engine, script: str, job: Path, style: str, title: str = "", man: dict = None) -> dict:
    """The video's characters, in the traits its renderer declared, and its places, designed once (casting.json)."""
    folder = Path(job) / "animated2d"
    spec = _spec(engine, style)
    man = man if man is not None else _style_renderer(engine, style)[1]
    cast_spec = man.get("cast") or {}
    key = _digest({"script": script, "spec": spec, "prompt": _prompt("cast.txt"), "brief": engine._extra_block(),
                   "cast": cast_spec})
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
                  .replace("[DESCRIBE]", str(cast_spec.get("describe") or "the channel's characters"))
                  .replace("[TRAITS]", json.dumps(cast_spec.get("traits") or {"color": "colour"}, ensure_ascii=False)))
        _write(folder / "casting-request.json", {"key": key})
        text = engine.claude(prompt, _model("cast"), max_tokens=6000, provider="claudecode")
        reply.parent.mkdir(parents=True, exist_ok=True)
        reply.write_text(str(text), encoding="utf-8")
    raw = _json(text)
    out = {"key": key, "cast": clean_cast(raw.get("cast"), cast_spec.get("traits")), "sets": clean_sets(raw.get("sets"))}
    _write(folder / "casting.json", out)
    return out


def plate_prompt(set_: dict, spec: dict, man: dict = None) -> str:
    look = ((man or {}).get("plates") or {}).get("prompt") or spec.get("art") or "flat 2D vector animation"
    return (f"{set_['prompt']}. A background plate for a 2D animation, in this style: {look}. "
            "EMPTY: nobody in it, no people, no figures, no characters, no animals, no text, no logos. "
            "Seen straight on from eye level, wide 16:9. The ground is a flat, level surface across the bottom fifth "
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
        engine.step(f"Animated 2D: drawing {len(todo)} background plate(s) (about ${PLATE_USD * len(todo):.2f})")
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
                engine.log(f"animated 2D: the first plate couldn't be shared as a style reference ({str(error)[:80]})")
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(todo)))) as ex:
            errors += [e for e in ex.map(lambda r: _try(draw, r), todo) if e]
        if errors:
            engine.step(f"Animated 2D: {len(errors)} plate(s) could not be drawn ({errors[0][:120]}); those places use "
                        "the renderer's own drawn background")
    return {k: v for k, v in out.items() if v.is_file() and v.stat().st_size > 5000}


def _try(fn, row):
    try:
        fn(row)
        return ""
    except Exception as error:                          # noqa: BLE001 - reported by the caller
        return f"{type(error).__name__}: {error}"


def direct(engine, job: Path, casting: dict, spec: dict, part: tuple, index: int, previous: str, title: str,
           man: dict = None) -> dict:
    """One stretch of narration choreographed by Claude in the renderer's own actions (director-NNN.json), checked."""
    t0, t1, lines = part
    man = man or {}
    folder = Path(job) / "animated2d"
    template = _prompt("director.txt")
    beats = beats_text(lines)
    docs = {"actions": man.get("actions") or {}, "shots": man.get("shots") or [], "fx": man.get("fx") or {},
            "notes": man.get("notes") or ""}
    key = _digest({"beats": beats, "cast": casting["cast"], "sets": casting["sets"], "spec": spec, "prompt": template,
                   "previous": previous, "t": [t0, t1], "docs": docs})
    reply = folder / f"director-{index:03d}.txt"
    request = folder / f"director-{index:03d}.json"
    if reply.is_file() and _read(request).get("key") == key:
        text = reply.read_text(encoding="utf-8")
    else:
        engine.step(f"Animated 2D: choreographing {t0:.0f}-{t1:.0f} s")
        prompt = (template.replace("[TITLE]", title).replace("[STYLE]", json.dumps(spec, ensure_ascii=False))
                  .replace("[CAST]", json.dumps(casting["cast"], ensure_ascii=False))
                  .replace("[SETS]", json.dumps({k: v["prompt"] for k, v in casting["sets"].items()}, ensure_ascii=False))
                  .replace("[ACTIONS]", "\n".join(f"- {k}: {v}" for k, v in docs["actions"].items()))
                  .replace("[SHOTS]", ", ".join(str(s) for s in docs["shots"]) or "wide, two, close, medium")
                  .replace("[FX]", "\n".join(f"- {k}: {v}" for k, v in docs["fx"].items()) or "(none)")
                  .replace("[NOTES]", docs["notes"] or "(none)")
                  .replace("[MEDIUM]", "3D" if dims_of(spec) == 3 else "2D").replace("[PACE]", pace_words(spec))
                  .replace("[PLACE]", str(docs["actions"].get("place") or "{x, face: left|right}"))
                  .replace("[START]", f"{t0:.2f}").replace("[END]", f"{t1:.2f}").replace("[BEATS]", beats)
                  .replace("[PREVIOUS]", previous or "This is the opening: place everyone first.")
                  .replace("[CREATOR]", engine._extra_block() or "none"))
        _write(request, {"key": key})
        text = engine.claude(prompt, _model("director"), max_tokens=12000, provider="claudecode")
        reply.write_text(str(text), encoding="utf-8")
    return clean_plan(_json(text), casting["cast"], casting["sets"], t0, t1, man)


def film_spec(plan: dict, casting: dict, spec: dict, plate_paths: dict, words: list = None, size=None) -> dict:
    sets = {}
    for ident, s in casting["sets"].items():
        row = {k: v for k, v in s.items() if k in ("sky", "floor_colors", "prompt")}
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
    code, man = _style_renderer(engine, style)
    casting = cast_and_sets(engine, script, job, style, _first_line(job / "title.txt"), man)
    plates(engine, job, casting, _spec(engine, style), man=man)


def assemble(engine, job, mp3, srt, style, force, burn_subs):
    job = Path(job)
    spec = _spec(engine, style)
    code, man = _style_renderer(engine, style)
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
    timeline, segments, previous = [], [], ""
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:8]
    for index, part in enumerate(parts):
        plan = direct(engine, job, casting, spec, part, index, previous, title, man)
        previous = ending(plan)
        words = [{"w": w, "t": round(t - part[0], 3)} for _, _, _, ws in part[2] for w, t in ws]
        film = film_spec(plan, casting, spec, plate_paths, words,
                         size=(int(getattr(engine, "VIDEO_W", 1920)), int(getattr(engine, "VIDEO_H", 1080))))
        _write(job / "animated2d" / f"film-{index:03d}.json", film)
        out = folder / f"a2d_{index:03d}_{_digest([film, code_hash])[:8]}.mp4"
        if not (out.is_file() and out.stat().st_size > 10000) or force:
            # the frame review before the real render: a renderer fault that leaves the screen empty stops here,
            # before anything is drawn, never published
            runs = blank_runs(sweep(film, code), film["duration"])
            if runs:
                a, b = runs[0]
                raise AnimationStopped(
                    f"The channel's animation would show nothing from {_clock(part[0] + a)} to {_clock(part[0] + b)} of "
                    f"this video (after {_before(film, a)}): a fault in its renderer (version {man.get('version')}). "
                    "Nothing was drawn or published. Study the channel again, or choose Use pictures instead on the "
                    "style's page.")
            engine.step(f"Animated 2D: drawing {part[1] - part[0]:.0f} s of animation ({index + 1} of {len(parts)})")
            marks = render(film, out, code, workers=max(1, min(3, int(getattr(engine, "MOTION_WORKERS", 2)) + 1)),
                           log=engine.log)
            _write(out.with_suffix(".sfx.json"), marks)
        timeline.append(("motion", str(out), False, round(part[1] - part[0], 6)))
        segments.append((part[0], str(out), round(part[1] - part[0], 6)))
    _write(job / "animated2d" / "delivery.json", {"schema": SCHEMA, "parts": len(parts), "seconds": total,
                                                 "renderer": man.get("name"), "renderer_version": man.get("version"),
                                                 "plates": {k: str(v) for k, v in plate_paths.items()}})
    engine._save_motion_plan(job, timeline, segments)
    engine.step("Animated 2D: assembling the film")
    rendered = engine._render_astro_segments(timeline, job, force, white_fade=0.0, style=style)
    engine._concat_segments(rendered, job / "_visual.mp4")
    burn_subs = burn_subs and not spec.get("captions")      # the renderer draws the channel's own captions
    engine._final_mux(job, engine._with_sound_design(job, mp3, timeline, style), srt, burn_subs,
                      add_qr=False, vignette=False, no_dust_ranges=[(0.0, total)], sub_center=False, no_sub_ranges=[])
    engine._keep_clean_visual(job, style)
    return job / "video.mp4"
