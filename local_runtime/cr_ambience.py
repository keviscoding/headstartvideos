"""The sound of each place: ambience laid under a story's pictures, scene by scene.

Many channels put the air of the place under the voice: rain on a rainy street,
wind over an empty landscape, a crowd in a market. Studio could already lay one
recording under a whole video (look.sound.ambience: {"file": ..., "db": ...});
this module lets a learned style ask for ambience by scene instead
({"scenes": true, "db": ..., "default": "<kind>"}): each picture's storyboard
prompt picks a recording from ChannelRecipe's ambience library (assets/sfx/cr/
ambience/<kind>_<n>.mp3, generated once and shipped with Studio), a place
carries over the following pictures until another one is shown, and changes
crossfade. The track never ducks under the voice; it is the room the story
happens in.
"""
from __future__ import annotations

import json
import re
import subprocess
import zlib
from pathlib import Path

import numpy as np

HOME = Path(__file__).resolve().parent
LIBRARY = HOME / "assets" / "sfx" / "cr" / "ambience"
SR = 48000
FADE_S = 1.2                  # crossfade between two places
CARRY_S = 20.0                # a place is still heard this long after the last picture that showed it

# What a picture shows -> the place it sounds like. First match wins, so specific places come before general ones.
PLACES = [
    ("underwater", r"\bunder ?water\b|\bsubmerged\b|\bseabed\b|\bocean floor\b|\bdiver\b"),
    ("spaceship", r"\bspace ?ship\b|\bspacecraft\b|\bspace station\b|\bcockpit of a (?:rocket|shuttle)\b"),
    ("cabin", r"\b(?:air ?plane|aircraft|jet|airliner|flight) (?:cabin|interior|cockpit)\b|\bcockpit\b|\bpassengers? (?:seated|in their seats)\b"),
    ("battle", r"\bbattle ?field\b|\btrenches?\b|\bartillery\b|\bexplosions?\b|\bsoldiers? (?:fighting|charging|firing)\b|\bwar ?zone\b"),
    ("storm", r"\bthunder ?storm\b|\bstorm\b|\blightning\b|\bthunder\b"),
    ("rain", r"\brain(?:y|ing|drops?|fall)?\b|\bdownpour\b|\bdrizzl"),
    ("police", r"\bpolice (?:car|cruiser|radio)\b|\bsquad car\b|\bpatrol car\b|\bdispatch"),
    ("prison", r"\bprison\b|\bjail\b|\bcell ?block\b|\b(?:prison|jail) cell\b|\binmates?\b|\bcorrectional\b"),
    ("hospital", r"\bhospital\b|\bward\b|\bsurgery\b|\boperating (?:room|table)\b|\bicu\b|\bclinic\b"),
    ("tavern", r"\btavern\b|\binn\b|\bpub\b|\bsaloon\b|\bbar counter\b"),
    ("market", r"\bmarket(?:place)?\b|\bbazaar\b|\bstalls?\b|\bmerchants?\b|\bvendors?\b"),
    ("crowd", r"\bcrowd(?:ed|s)?\b|\baudience\b|\bstadium\b|\bspectators\b|\bcourtroom\b|\bassembly hall\b"),
    ("office", r"\boffice\b|\bcubicle\b|\bnewsroom\b|\btrading floor\b|\bcall cent(?:er|re)\b"),
    ("cave", r"\bcave(?:rn)?s?\b|\bmine shaft\b|\bunderground tunnel\b|\bcatacomb"),
    ("fire", r"\bfire ?place\b|\bcamp ?fire\b|\bbonfire\b|\bhearth\b|\bburning (?:logs|wood)\b|\bforge\b"),
    ("ocean", r"\bocean\b|\bbeach\b|\bshore(?:line)?\b|\bwaves?\b|\bsea\b|\bharbou?r\b|\bdeck of (?:a|the) ship\b"),
    ("river", r"\briver(?:bank)?\b|\bstream\b|\bcreek\b|\bwaterfall\b|\bbrook\b"),
    ("desert", r"\bdesert\b|\bdunes?\b|\bsahara\b|\barid\b|\bwasteland\b"),
    ("night", r"\bat night\b|\bnight ?time\b|\bmoonlit\b|\bunder (?:the|a) (?:moon|stars)\b|\bmidnight\b"),
    ("forest", r"\bforest\b|\bwoods?\b|\bjungle\b|\btrees? (?:all around|surround)|\bwoodland\b|\bgarden\b|\bpark\b"),
    ("farm", r"\bfarm(?:house|land|yard)?\b|\bfields?\b|\bbarn\b|\bvillage\b|\bcountryside\b|\bmeadow\b|\bpasture\b"),
    ("city", r"\bcity\b|\bstreets?\b|\btraffic\b|\bdowntown\b|\bavenue\b|\bsidewalk\b|\bcrossroads\b|\btown square\b"),
    ("wind", r"\bmountain(?:top|s)?\b|\bcliff\b|\bplains?\b|\bwindswept\b|\bopen landscape\b|\bhorizon\b|\btundra\b|\bwind\b"),
    ("room", r"\bkitchen\b|\bbedroom\b|\bliving room\b|\bclassroom\b|\blibrary\b|\bhallway\b|\bstudy\b|\bindoors?\b|\binterior\b|\broom\b"),
]
# what a reference's ambience was called (watched) -> the library's kind
WATCHED = [(kind, re.compile(rx, re.I)) for kind, rx in [
    ("rain", r"rain"), ("storm", r"thunder|storm"), ("wind", r"wind|gust|breeze"), ("forest", r"bird|forest|leaves|jungle"),
    ("night", r"cricket|insect|night"), ("city", r"traffic|street|city|horn"), ("crowd", r"crowd|murmur|chatter|cafeteria"),
    ("police", r"police|radio|dispatch|siren"), ("office", r"office|keyboard"), ("ocean", r"wave|ocean|sea|surf"),
    ("river", r"river|stream|water flow"), ("fire", r"\bfire\b|fireplace|campfire"),
    ("battle", r"battle|explosion|gunfire"), ("cave", r"drip|cave"), ("prison", r"prison|cell"),
    ("hospital", r"hospital|monitor|beep"), ("cabin", r"cabin|aircraft|engine"), ("room", r"room tone|hum|fan|air con")]]


def watched_kind(names: list) -> str:
    """The library kind that best matches the ambience a reference was heard to have ("" when none does)."""
    for name in names or []:
        for kind, rx in WATCHED:
            if rx.search(str(name)):
                return kind
    return ""


def wanted(look: dict) -> dict:
    snd = (look or {}).get("sound") if isinstance(look, dict) else None
    amb = snd.get("ambience") if isinstance(snd, dict) else None
    return amb if isinstance(amb, dict) and amb.get("scenes") and not amb.get("off") else {}


def files(library: Path = None) -> dict:
    """{kind: [recordings]} in the library."""
    out = {}
    folder = library or LIBRARY
    if folder.is_dir():
        for path in sorted([*folder.glob("*.mp3"), *folder.glob("*.wav")]):
            match = re.fullmatch(r"([a-z]+)_\d+", path.stem)
            if match:
                out.setdefault(match.group(1), []).append(path)
    return out


def place_of(prompt: str, suffix: str = "") -> str:
    """The place a storyboard prompt shows, from what it shows (the style's own words removed), or ""."""
    shown = str(prompt or "")
    if suffix:
        shown = shown.replace(suffix, "")
    shown = shown[:600].lower()
    for kind, rx in PLACES:
        if re.search(rx, shown):
            return kind
    return ""


def scenes(job, plan, look) -> list:
    """[(start, end, kind)] for a story's timeline: each picture's place, carried over the pictures after it."""
    try:
        frames = json.loads((Path(job) / "astro_scenes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    frames = frames if isinstance(frames, list) else []
    clips = len(list((Path(job) / "clip_src").glob("vsrc_*.jpg")))
    suffix = str((look or {}).get("still_suffix") or "")
    default = str(wanted(look).get("default") or "")
    out, t, current, seen_at = [], 0.0, default, -1e9
    for entry in plan or []:
        try:
            path, dur = str(entry[1]), float(entry[3])
        except (IndexError, TypeError, ValueError):
            continue
        index = None
        if (m := re.search(r"scene_(\d+)\.(?:jpg|png)$", path)):
            index = clips + int(m.group(1))
        elif (m := re.search(r"(?:clip|vsrc)_(\d+)\.(?:mp4|jpg)$", path)):
            index = int(m.group(1))
        frame = frames[index] if index is not None and 0 <= index < len(frames) and isinstance(frames[index], dict) else {}
        kind = place_of(frame.get("prompt"), suffix)
        if kind:
            current, seen_at = kind, t
        elif t - seen_at > CARRY_S:
            current = default                       # the place has not been shown for a while: back to the channel's air
        if out and out[-1][2] == current:
            out[-1] = (out[-1][0], t + dur, current)
        else:
            out.append((t, t + dur, current))
        t += dur
    return [s for s in out if s[2]]


def _load(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


def _fill(loop: np.ndarray, n: int, offset: int) -> np.ndarray:
    """n samples of a seamless loop, starting `offset` samples in (so two scenes of one place don't start alike)."""
    reps = int(np.ceil((n + offset) / max(1, len(loop)))) + 1
    return np.tile(loop, (reps, 1))[offset:offset + n]


def build(job, plan, look, total: float, *, library: Path = None, log=print):
    """job/sound/_ambience_scenes.wav at the style's level, or None when no picture shows a place we have."""
    cfg = wanted(look)
    kit = files(library)
    parts = [(a, b, k) for a, b, k in scenes(job, plan, look) if k in kit]
    if not cfg or not parts:
        return None
    db = float(cfg.get("db") if cfg.get("db") is not None else -16.0)
    n = int(total * SR)
    track = np.zeros((n, 2), np.float32)
    fade = int(FADE_S * SR)
    cache = {}
    for i, (a, b, kind) in enumerate(parts):
        recs = kit[kind]
        rec = recs[zlib.crc32(f"{Path(job).name}:{i}".encode()) % len(recs)]
        if rec not in cache:
            cache[rec] = _load(rec)
        s, e = max(0, int(a * SR) - fade // 2), min(n, int(b * SR) + fade // 2)
        if e <= s:
            continue
        seg = _fill(cache[rec], e - s, zlib.crc32(rec.name.encode() + bytes([i % 256])) % max(1, len(cache[rec])))
        env = np.ones(e - s, np.float32)
        k = min(fade, (e - s) // 2)
        if k:
            env[:k] = np.linspace(0, 1, k, dtype=np.float32)
            env[-k:] = np.linspace(1, 0, k, dtype=np.float32)
        track[s:e] += seg * env[:, None]
    track *= 10 ** (db / 20)
    out = Path(job) / "sound" / "_ambience_scenes.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(track, -1, 1) * 32767).astype("<i2")
    import wave
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    used = sorted({k for _, _, k in parts})
    log(f"sound: the sound of each place under the pictures ({', '.join(used)}) at {db:+.0f} dB")
    return out


_current = {"plan": None, "look": {}}          # the video being mixed (jobs run one per worker process)


def install(engine=None):
    """Let the mixer lay scene ambience for styles that ask (look.sound.ambience.scenes)."""
    try:
        import sfx
    except ImportError:
        return
    if getattr(sfx._ambience_bed, "_cr_scenes", False):
        return
    original_bed, original_mix = sfx._ambience_bed, sfx.mix

    def mix(engine, job, voice, plan, style, *args, **kwargs):
        try:
            look = sfx._v2_look(style) or {}
        except Exception:                                   # noqa: BLE001 - the mix itself decides what a style is
            look = {}
        _current.update(plan=plan, look=look if isinstance(look, dict) else {})
        try:
            return original_mix(engine, job, voice, plan, style, *args, **kwargs)
        finally:
            _current.update(plan=None, look={})

    def ambience_bed(job, cfg, total, log):
        if isinstance(cfg, dict) and cfg.get("scenes"):
            look = dict(_current["look"] or {})
            look["sound"] = dict(look.get("sound") or {}, ambience=cfg)
            try:
                return build(job, _current["plan"] or [], look, total, log=log)
            except (OSError, ValueError, subprocess.SubprocessError) as e:
                log(f"sound: no scene ambience ({type(e).__name__}: {str(e)[:80]})")
                return None
        return original_bed(job, cfg, total, log)
    ambience_bed._cr_scenes = True
    sfx._ambience_bed, sfx.mix = ambience_bed, mix
