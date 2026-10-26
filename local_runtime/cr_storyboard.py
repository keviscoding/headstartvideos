"""A sound storyboard for each scored video: the music follows the story beat by beat.

Good story channels steer attention with sound: a cold open with no drums, a
drop on the promise, a new texture for each story section, a riser into every
reveal, the music cut for a beat before a punchline, a sub-drop and silence at
an act turn, a swell on the payoff (the owner's music brief, 2026-10-15). After
the narration is recorded, Claude reads the timed narration and the channel's
sound and writes the storyboard once per video (job/sound/storyboard.json):

  sections  one per story section (25 to 45 s): mood, job and what the music
            does; the score is written section by section from them
  events    risers, hits, sub-drops, whooshes, silences and swells at exact
            narration times, thinned to the channel's effect density

Effects land through the mixer's own cues; silences and swells shape the music
bed. A style without a per-video score is untouched.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

KINDS = ("riser", "hit", "sub_drop", "whoosh", "silence", "swell")
BIG = ("hit", "sub_drop")
GAP_S = {"sparse": 18.0, "moderate": 11.0, "dense": 7.0}
GAIN = {"riser": -9.0, "hit": -7.0, "sub_drop": -6.0, "whoosh": -9.0}

PROMPT = """You are the sound designer of a YouTube video. Music is how the viewer soaks into the story: it is always
there, it follows the story beat by beat, and it steers attention with risers, hits, drops and silences.

THE CHANNEL'S SOUND: [SOUND]

THE NARRATION, with the second each sentence starts:
[LINES]

THE VIDEO IS [TOTAL] SECONDS LONG.

Write the sound storyboard. Return ONE JSON object:
{
 "sections": [{"t": <second it starts>, "mood": "one or two words", "role": "cold open | hook drop | explain |
   tension | wonder | serious | release | act turn | finale", "music": "what the music does here in a producer's words:
   instruments, energy, drums or none, what changes from the section before"}],
 "events": [{"t": <second>, "kind": "riser | hit | sub_drop | whoosh | silence | swell", "why": "a few words"}]
}
RULES
- Sections run 25 to 45 seconds, in order, the first at 0, each starting where a sentence starts. The first is a cold
  open with tension and no drums; the promise of the video lands with the first drop; each new story section gets a
  new texture; serious or harmful moments strip the rhythm; the last section builds to a finish.
- A riser ends exactly on a reveal (t is the reveal). A hit or sub_drop lands on a reveal, a big number or an act turn;
  never two big ones within 8 seconds. An act turn: a sub_drop, then the new section starts after a beat.
- silence: the music drops out for a beat just before an ironic, deadpan or punchline sentence (t = that sentence's
  start). swell: the music rises in the pause after a payoff. whoosh: a big jump in place or time.
- Effects density for this channel: [DENSITY]. Never put a sound on a word for its own sake.
- Use only the times given. The music matches the channel's sound above; never name artists or channels."""


def _srt(text: str) -> list:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        rows = block.strip().splitlines()
        if len(rows) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", rows[1])
        if m:
            h, mi, s, ms = (int(x) for x in m.groups())
            out.append((h * 3600 + mi * 60 + s + ms / 1000.0, " ".join(rows[2:]).strip()))
    return out


def sentences(job) -> list:
    """[(second, sentence)] on the voice's clock."""
    try:
        cues = _srt((Path(job) / "subs.srt").read_text(encoding="utf-8"))
    except OSError:
        return []
    out, start, buf = [], None, []
    for t, text in cues:
        if start is None:
            start = t
        buf.append(text)
        if re.search(r"[.!?…][\"')\]]*$", text):
            out.append((round(start, 1), " ".join(buf)))
            start, buf = None, []
    if buf:
        out.append((round(start, 1), " ".join(buf)))
    return out


def sound_words(look: dict) -> tuple:
    snd = (look or {}).get("sound") or {}
    score = snd.get("score") or {}
    parts = []
    if score.get("genres"):
        parts.append("music: " + ", ".join(score["genres"][:2]))
    if score.get("moods"):
        parts.append("moods: " + ", ".join(score["moods"][:5]))
    if score.get("instruments"):
        parts.append("instruments: " + ", ".join(score["instruments"][:5]))
    if score.get("beat"):
        parts.append(f"beat: {score['beat']}")
    level = snd.get("music_rel_db")
    if level is not None:
        parts.append("a quiet bed under the voice" if level <= -22 else "a present score under the voice")
    density = str(snd.get("density") or ("moderate" if snd.get("library") else "sparse"))
    return "; ".join(parts) or "cinematic documentary underscore", density


def _json(text: str) -> dict:
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", str(text or "").strip())
    try:
        value = json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        try:
            value = json.loads(text[a:b + 1]) if 0 <= a < b else {}
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


def clean(raw: dict, total: float, starts: list, density: str) -> dict:
    """The storyboard held to its rules: sections 20 s+ apart on sentence starts, events on the timeline, big
    hits 8 s+ apart, effects thinned to the channel's density."""
    snap = lambda t: min(starts, key=lambda s: abs(s - t)) if starts else t
    sections = []
    for s in raw.get("sections") or []:
        try:
            t = 0.0 if not sections else snap(float(s.get("t")))
        except (TypeError, ValueError):
            continue
        if sections and (t - sections[-1]["t"] < 20.0 or t > total - 15.0):
            continue
        sections.append({"t": round(t, 2), "mood": " ".join(str(s.get("mood") or "").split())[:30],
                         "role": " ".join(str(s.get("role") or "").split())[:20],
                         "music": " ".join(str(s.get("music") or "").split())[:220]})
    gap = GAP_S.get(density, 11.0)
    events, last_fx, last_big = [], -99.0, -99.0
    for e in sorted((e for e in raw.get("events") or [] if isinstance(e, dict)), key=lambda e: float(e.get("t") or 0)):
        try:
            t = float(e.get("t"))
        except (TypeError, ValueError):
            continue
        kind = str(e.get("kind") or "").lower()
        if kind not in KINDS or not 1.0 <= t <= total - 1.0:
            continue
        if kind in BIG and t - last_big < 8.0:
            continue
        if kind in GAIN and t - last_fx < gap:
            continue
        events.append({"t": round(t, 2), "kind": kind, "why": " ".join(str(e.get("why") or "").split())[:80]})
        if kind in GAIN:
            last_fx = t
        if kind in BIG:
            last_big = t
    return {"sections": sections or [{"t": 0.0, "mood": "", "role": "explain", "music": ""}], "events": events}


def make(ask, job, look: dict, total: float, log=print) -> dict:
    """The storyboard for this video (cached), or {} when it can't be written."""
    job = Path(job)
    out = job / "sound" / "storyboard.json"
    if out.is_file():
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            pass
    lines = sentences(job)
    if not lines:
        return {}
    words, density = sound_words(look)
    prompt = (PROMPT.replace("[SOUND]", words).replace("[DENSITY]", f"{density} (about one effect every "
              f"{GAP_S.get(density, 11.0):.0f} seconds at most)").replace("[TOTAL]", f"{total:.0f}")
              .replace("[LINES]", "\n".join(f"[{t:.1f}] {s}" for t, s in lines)[:60000]))
    try:
        board = clean(_json(ask(prompt)), total, [t for t, _ in lines], density)
    except Exception as e:                                             # noqa: BLE001 - the score stands without it
        log(f"  storyboard: not written ({type(e).__name__}: {str(e)[:100]})")
        return {}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(board, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"  storyboard: {len(board['sections'])} music section(s), {len(board['events'])} sound event(s)")
    return board


def moods(job, board: dict):
    """The storyboard's sections as the music's mood turns (job/moods.json), keeping the script's own."""
    path = Path(job) / "moods.json"
    try:
        mine = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except ValueError:
        mine = []
    mine = [m for m in mine if isinstance(m, dict)]
    for s in board.get("sections") or []:
        if s.get("mood") and all(abs(float(m.get("t") or 0) - s["t"]) >= 20 for m in mine):
            mine.append({"t": s["t"], "mood": s["mood"]})
    path.write_text(json.dumps(sorted(mine, key=lambda m: float(m.get("t") or 0)), indent=1), encoding="utf-8")


def hint(board: dict, t0: float) -> str:
    """What the storyboard asks the music to do in the section starting at t0."""
    rows = [s for s in board.get("sections") or [] if abs(s["t"] - t0) < 12]
    if not rows:
        return ""
    s = rows[0]
    return "; ".join(x for x in (s.get("role"), s.get("music")) if x)


def cues(board: dict, look: dict = None) -> list:
    """[(second, sound, dB)] for the mixer: risers end on their reveal, hits and drops land on it. A style drawn by its
    own renderer makes its own effects (the channel's sounds, in code): Studio's stock riser, hit, sub-drop and whoosh
    never play over it (the owner, 2026-10-26: no "generic slop sound effects... the one that goes braam"); its
    storyboard still shapes the music with its silences and swells."""
    if isinstance(look, dict) and look.get("visuals") in ("cr_animated2d", "cr_animated3d"):
        return []
    return [(e["t"], e["kind"], GAIN[e["kind"]]) for e in board.get("events") or [] if e["kind"] in GAIN]


def shape(bed: Path, board: dict) -> Path:
    """The music bed with the storyboard's silences (out for a beat) and swells (up 4 dB in a pause)."""
    events = [e for e in board.get("events") or [] if e["kind"] in ("silence", "swell")]
    if not events or not bed or not Path(bed).is_file():
        return bed
    terms = []
    for e in events:
        t = e["t"]
        if e["kind"] == "silence":                      # down over 0.15 s before the line, back over 0.6 s after
            a, b = t - 0.45, t + 0.35
            terms.append(f"(1-0.92*clip((t-({a - 0.15:.2f}))/0.15,0,1)*clip(({b + 0.6:.2f}-t)/0.6,0,1))")
        else:                                           # +4 dB over the pause after the payoff
            a, b = t, t + 3.0
            terms.append(f"(1+0.58*clip((t-{a:.2f})/0.5,0,1)*clip(({b:.2f}-t)/1.0,0,1))")
    out = Path(bed).with_name(Path(bed).stem + "_shaped" + Path(bed).suffix)
    expr = "*".join(terms)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(bed), "-af", f"volume='{expr}':eval=frame", str(out)],
                   check=True, capture_output=True, timeout=1800)
    return out


_engine = {"ask": None}


def board_for(job, look: dict, total: float, log=print) -> dict:
    """The video's storyboard: written on first use by whichever step needs it (effects or music), then cached."""
    import cr_score
    if not cr_score.wanted(look):
        return {}
    ask = _engine["ask"]
    path = Path(job) / "sound" / "storyboard.json"
    if path.is_file() or ask is None:
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except ValueError:
            return {}
    board = make(ask, job, look, total, log)
    if board:
        moods(job, board)
    return board


def install(engine):
    """Write a storyboard for scored videos; its effects join the mixer's cues."""
    _engine["ask"] = lambda prompt: engine.claude(prompt, engine.SCRIPT_MODEL, max_tokens=8000,
                                                  provider=getattr(engine, "SCRIPT_PROVIDER", None))
    try:
        import sfx
    except ImportError:
        return
    if getattr(sfx.cues, "_cr_storyboard", False):
        return
    original = sfx.cues

    def cues_(plan, job=None, style=None, look=None):
        out = original(plan, job, style, look)
        if job is None:
            return out
        the_look = look if isinstance(look, dict) else sfx._v2_look(style) if style else {}
        total = sum(float(e[3]) for e in plan or [] if len(e) > 3)
        board = board_for(job, the_look, total, getattr(engine, "log", print))
        return sorted(out + cues(board, the_look)) if board else out
    cues_._cr_storyboard = True
    sfx.cues = cues_
