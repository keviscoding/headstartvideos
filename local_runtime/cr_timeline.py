"""Videos of a channel with no narrator (a 3D city growing over centuries, music only): a timeline instead of a script.

A clone of such a channel (cr_style_map: the references' narration is "none") has look.audio "music_only". Its videos
are timed to a written timeline, not to a voice (the owner, 2026-11-02: CityEons "is buildable by ChannelRecipe today,
they just have to clone it"; a creator asked for it on Discord):

- the script step: Claude writes timeline.json, beats on the video's own clock (the year on the counter, the event card,
  the ruler, the population, the places labelled, what is built, the camera) and eras for the music; script.txt is its
  text, so the description, the cast and the places read it as they read a script;
- the voice step: the score (cr_score, Suno on the creator's Kie account, one cue per era, the music carrying the video)
  is audio.mp3, cut to the video's length, and subs.srt has one cue per beat (words.json beside it, so nothing is ever
  transcribed);
- the coded route (cr_animated2d.assemble) directs the renderer beat by beat and gives it the timeline (Kit.scene), so a
  city keeps growing across the minutes it draws one at a time.

Every other video is untouched: these wrappers pass straight through unless the style asks for music only."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

MUSIC_ONLY = "music_only"
FILE = "timeline.json"
MIN_BEAT_S = 3.0
MAX_ERA_S = 330.0                      # one Suno cue is at most 360 s, with room for its crossfade


def look_of(engine, style: str) -> dict:
    look = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}).get("look")
    return look if isinstance(look, dict) else {}


def music_only(engine, style: str) -> bool:
    return look_of(engine, style).get("audio") == MUSIC_ONLY


# ── the timeline ───────────────────────────────────────────────────────────────────────────────────────────────

PROMPT = """You write the timeline of one video for a YouTube channel that has NO narrator: its videos are music and
pictures, and everything the viewer learns is on screen. Write it like the channel's best videos.

THE VIDEO: [TITLE]
ITS LENGTH: exactly [SECONDS] seconds.
THE CHANNEL: [CHANNEL]
WHAT THE CREATOR ASKED FOR: [CREATOR]
[LANGUAGE]
Write a timeline of beats on the video's own clock, covering 0 to [SECONDS] s with no gaps. Each beat is what the screen
shows from its second on: the year on the counter ("C. 1600", "1626", "SEP 1664", "AUG 22 1776"), the event card if
one (a short bold headline and one or two plain sentences of real, checkable history: dates, names, numbers), who rules
(state and ruler), the population when known (marked c. when estimated), the places to label, what appears or changes
(built, burnt, flooded, planned), any moving thing (ships, troops, fireworks, smoke) and the camera (where it flies,
how high, what it frames). Be accurate: real places, real dates; when a figure is uncertain, say "c.". Never invent an
event.
PACE like the channel: open on the subject today and its span (the hook), rewind to the beginning, slow where the
story is (several seconds per decade early, a pause on each big date), faster through quiet growth, and end on a
resolution (today, a look back, a grand pull-back). Beats last at least [MIN] s; most last 5 to 15 s.
ERAS for the music: 2 to 8 stretches, each one mood ("quiet", "curious", "tense", "dramatic", "sad", "inspiring",
"triumphant", or a few words), each at most [ERA] s, covering 0 to [SECONDS] s in order.
THE PLACE: the real place the video is about (name, country, latitude and longitude of its historic centre, how many
kilometres across the camera's whole area), for the map.

Reply with JSON only:
{"place": {"name": "", "country": "", "lat": 0.0, "lon": 0.0, "span_km": 0},
 "years": {"from": "", "to": ""},
 "eras": [{"at": 0, "until": 0, "mood": "", "title": ""}],
 "beats": [{"at": 0, "until": 0, "year": "", "headline": "", "text": "", "state": "", "ruler": "", "population": "",
            "labels": [""], "build": "", "moving": "", "camera": ""}]}"""


def _json(text: str) -> dict:
    text = str(text or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    try:
        value = json.loads(match.group(0) if match else text)
    except ValueError:
        raise ValueError("The timeline came back unreadable.") from None
    return value if isinstance(value, dict) else {}


def _seconds(value, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def clean(raw: dict, total: float) -> dict:
    """The timeline on exactly [0, total]: beats in order, at least MIN_BEAT_S each, no gaps or overlaps (each runs to
    the next one's start, the last to the end); eras the same, at most MAX_ERA_S each (a longer one is split)."""
    total = float(total)
    beats = []
    for row in sorted((b for b in raw.get("beats") or [] if isinstance(b, dict)), key=lambda b: _seconds(b.get("at"))):
        at = min(_seconds(row.get("at")), total)
        if beats and at - beats[-1]["at"] < MIN_BEAT_S:
            continue                                    # too close to the one before: that one carries on
        beats.append({**{k: v for k, v in row.items() if k not in ("at", "until")}, "at": round(at, 2)})
    if not beats:
        raise ValueError("The timeline has no beats.")
    beats[0]["at"] = 0.0
    for here, after in zip(beats, beats[1:] + [None]):
        here["until"] = round(after["at"] if after else total, 2)
    if beats[-1]["until"] - beats[-1]["at"] < MIN_BEAT_S and len(beats) > 1:
        beats.pop()
        beats[-1]["until"] = round(total, 2)
    eras = []
    for row in sorted((e for e in raw.get("eras") or [] if isinstance(e, dict)), key=lambda e: _seconds(e.get("at"))):
        at = min(_seconds(row.get("at")), total)
        if eras and at - eras[-1]["at"] < 20:
            continue
        eras.append({"at": round(at, 2), "mood": str(row.get("mood") or "")[:60], "title": str(row.get("title") or "")[:80]})
    if not eras:
        eras = [{"at": 0.0, "mood": "", "title": ""}]
    eras[0]["at"] = 0.0
    out = []
    for here, after in zip(eras, eras[1:] + [None]):
        a, b = here["at"], (after["at"] if after else total)
        pieces = max(1, int(-(-(b - a) // MAX_ERA_S)))
        for k in range(pieces):
            out.append({**here, "at": round(a + (b - a) * k / pieces, 2), "until": round(a + (b - a) * (k + 1) / pieces, 2)})
    place = raw.get("place") if isinstance(raw.get("place"), dict) else {}
    return {"seconds": round(total, 2), "place": place, "years": raw.get("years") if isinstance(raw.get("years"), dict) else {},
            "eras": out, "beats": beats}


def line(beat: dict) -> str:
    """What one beat says on screen, as one plain line (the script's text, a subtitle cue, the director's beat)."""
    parts = [str(beat.get("year") or "").strip()]
    if beat.get("headline"):
        parts.append(str(beat["headline"]).strip())
    if beat.get("text"):
        parts.append(str(beat["text"]).strip())
    said = " | ".join(p for p in parts if p)
    extra = [f"{k}: {str(beat[k]).strip()}" for k in ("state", "ruler", "population", "build", "moving", "camera")
             if str(beat.get(k) or "").strip()]
    labels = [str(x).strip() for x in (beat.get("labels") or []) if str(x).strip()]
    if labels:
        extra.append("labels: " + ", ".join(labels[:8]))
    return said + ((" [" + "; ".join(extra) + "]") if extra else "")


def read(job) -> dict:
    try:
        value = json.loads((Path(job) / FILE).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("beats") else {}
    except (OSError, ValueError):
        return {}


def write(engine, title: str, minutes, job, style: str, force: bool = False) -> str:
    """timeline.json for this video (Claude, once; kept on Resume) and script.txt, its text."""
    job = Path(job)
    total = round(max(1.0, float(minutes or 1)) * 60, 2)
    saved = read(job)
    if saved and not force and abs(float(saved.get("seconds") or 0) - total) < 0.5:
        text = (job / "script.txt").read_text(encoding="utf-8") if (job / "script.txt").is_file() else ""
        if text:
            return text
    import cr_language
    language = cr_language.of_job(job) or cr_language.current()
    channel = json.dumps({k: v for k, v in look_of(engine, style).items() if k in ("animated2d", "pacing", "sound")},
                         ensure_ascii=False)[:6000]
    prompt = (PROMPT.replace("[TITLE]", str(title)).replace("[SECONDS]", f"{total:g}")
              .replace("[CHANNEL]", channel or "(its look)").replace("[MIN]", f"{MIN_BEAT_S:g}")
              .replace("[ERA]", f"{MAX_ERA_S:g}").replace("[CREATOR]", engine._extra_block() or "none")
              .replace("[LANGUAGE]", cr_language.screen_rule(language).strip()))
    engine.step("1/3  Timeline (Claude): no narrator, so the video is timed to its years and events")
    timeline = clean(_json(engine.claude(prompt, _model(), max_tokens=16000, provider="claudecode")), total)
    (job / FILE).write_text(json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
    text = "\n\n".join(line(b) for b in timeline["beats"])
    (job / "script.txt").write_text(text, encoding="utf-8")
    engine.log(f"timeline: {len(timeline['beats'])} beats, {len(timeline['eras'])} music sections, "
               f"{timeline['place'].get('name') or 'no place named'}")
    return text


def _model() -> str:
    import cr_animated2d
    return cr_animated2d._model("director")


def lines(job) -> list:
    """[(start, end, text, [(word, start)])], cr_animated2d.narration's shape, from the timeline's beats."""
    out = []
    for beat in read(job).get("beats") or []:
        a, b, text = float(beat["at"]), float(beat["until"]), line(beat)
        words = text.split()
        out.append((a, b, text, [(w, round(a + (b - a) * k / max(1, len(words)), 2)) for k, w in enumerate(words)]))
    return out


# ── the "voice": the score, and one cue per beat ────────────────────────────────────────────────────────────────

def _clock(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def subtitles(timeline: dict) -> str:
    """One cue per beat: the year and the headline (what a viewer reads first)."""
    rows = []
    for i, beat in enumerate(timeline["beats"], 1):
        said = " ".join(p for p in (str(beat.get("year") or "").strip(), str(beat.get("headline") or "").strip()) if p)
        rows.append(f"{i}\n{_clock(beat['at'])} --> {_clock(beat['until'])}\n{said or '...'}\n")
    return "\n".join(rows)


def word_clock(srt: Path, timeline: dict) -> dict:
    """words.json for this subtitle file (the engine's _word_clock cache): every cue's words spread over its beat, so
    the music is never sent to speech recognition."""
    cues = {}
    for i, beat in enumerate(timeline["beats"]):
        said = " ".join(p for p in (str(beat.get("year") or "").strip(), str(beat.get("headline") or "").strip()) if p)
        words = (said or "...").split()
        a, b = float(beat["at"]), float(beat["until"])
        cues[str(i)] = [[round(a + (b - a) * k / max(1, len(words)), 3), round(a + (b - a) * (k + 1) / max(1, len(words)), 3)]
                        for k in range(len(words))]
    return {"srt": hashlib.sha1(Path(srt).read_bytes()).hexdigest()[:16], "cues": cues}


def score_look(look: dict) -> dict:
    """The look the score is made for: the channel's own sound, the music carrying the video alone."""
    sound = dict(look.get("sound") or {})
    score = dict(sound.get("score") or {"genres": ["cinematic orchestral score"], "moods": [], "instruments": []})
    score.update(lead=True, on=True)
    sound.update(score=score, music=True)
    return {**look, "sound": sound}


def voiceover(engine, job, style: str, force: bool = False):
    """(audio.mp3, subs.srt) for a music-only video: the score cut to the timeline's length, a cue per beat."""
    job = Path(job)
    timeline = read(job)
    if not timeline:
        raise RuntimeError("This music-only video has no timeline yet. Make it again from the start.")
    total = float(timeline["seconds"])
    srt, mp3 = job / "subs.srt", job / "audio.mp3"
    srt.write_text(subtitles(timeline), encoding="utf-8")
    (job / "words.json").write_text(json.dumps(word_clock(srt, timeline)), encoding="utf-8")
    (job / "chapters.json").write_text(json.dumps(
        [{"t": e["at"], "title": e.get("title") or ""} for e in timeline["eras"] if e.get("title")], ensure_ascii=False),
        encoding="utf-8")
    import cr_score
    sections = [(e["at"], e["until"], e.get("mood") or "", e.get("title") or "") for e in timeline["eras"]]
    engine.step(f"2/3  Music: {len(sections)} section(s) scored for the timeline (Suno on Kie), no voice")
    bed = cr_score.ensure_music(job, score_look(look_of(engine, style)), total, engine.log, sections=sections)
    if not bed or not Path(bed).is_file():
        raise RuntimeError("This style's videos are music only, and the music couldn't be made (it needs your Kie.ai "
                           "key in setup and a few credits). Nothing else was spent; make the video again once Kie "
                           "answers.")
    if force or not mp3.is_file() or abs(engine._audio_dur(mp3) - total) > 0.5:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(bed), "-af",
                        f"apad,atrim=0:{total:.3f},afade=t=out:st={max(0.0, total - 3):.3f}:d=3",
                        "-ar", "44100", "-ac", "2", "-b:a", "192k", str(mp3)], check=True, capture_output=True)
    return mp3, srt


# What the study tells Claude while it writes such a channel's renderer (cr_style_clone, the renderer's brief)
RENDERER_NOTE = (
    "This channel has no narrator: its videos are music and pictures, timed to a written timeline. Every film your "
    "renderer draws (one stretch of about a minute) also carries Kit.scene.clock {t0, total}: where the stretch starts "
    "on the video's own clock and the video's length (the absolute time at your t is clock.t0 + t), and "
    "Kit.scene.timeline {place, years, eras, beats}: every beat {at, until, year, headline, text, state, ruler, "
    "population, labels, build, moving, camera} in seconds on that clock. A place channel (a city or region growing "
    "over the years) also gets Kit.scene.map, the real place in metres around its centre (x east, y north): water "
    "[{kind, ring, holes}] (the sea, rivers, lakes: lay the ground, then the water over it), streets [{kind, line}], "
    "buildings [{ring, height, base}] (footprints and real heights, biggest first), parks, landuse [{kind, ring}], "
    "landmarks and places [{name, kind, at}]. Draw from them: the real coastline and streets, buildings rising on their "
    "footprints as the years pass (the oldest core first, outward and upward with time, landmarks when the timeline "
    "names them), the channel's own year counter, cards and labels from the beats, and one camera flight that carries "
    "on across stretches (derive it from the absolute time, so stretch 2 starts where stretch 1 ended). With a map, "
    "every set in Kit.scene.sets is that map seen from the air (set.aerial is true): frame its place on the map, "
    "never as a separate room or close-up. Without a map "
    "or a timeline (the test shots), draw a convincing place of your own the same way.")


# ── the place's real map (cr_map_data) ──────────────────────────────────────────────────────────────────────────

CREDIT = "map.credit"


def map_of(engine, job) -> dict:
    """The timeline's place as a real map for the renderer (Kit.scene.map), fetched once and cached; {} when the
    timeline names no place with coordinates, or the map can't be had (the renderer then draws its own place)."""
    place = read(job).get("place") or {}
    if not isinstance(place, dict) or "lat" not in place or "lon" not in place:
        return {}
    import cr_map_data
    found = cr_map_data.map_for(Path(__file__).resolve().parent, place, log=engine.log)
    if found:
        (Path(job) / CREDIT).write_text(found.get("source") or cr_map_data.SOURCE, encoding="utf-8")
    return found


def credit(job) -> None:
    """The map data's credit at the end of the video's description (its licence asks for it), once."""
    job = Path(job)
    if not (job / CREDIT).is_file():
        return
    line = "Map data: " + (job / CREDIT).read_text(encoding="utf-8").strip()
    desc = job / "youtube.txt"
    text = desc.read_text(encoding="utf-8") if desc.is_file() else ""
    if line not in text:
        desc.write_text(text.rstrip() + ("\n\n" if text.strip() else "") + line + "\n", encoding="utf-8")


# ── the wrappers ───────────────────────────────────────────────────────────────────────────────────────────────

def install(engine):
    """Wrap the engine's script writer and voiceover once, outermost: a music-only style's video gets its timeline and
    score; every other video goes through exactly as before."""
    if getattr(engine, "_cr_timeline", False) or not hasattr(engine, "generate_script"):
        return
    engine._cr_timeline = True
    original_script, original_voice = engine.generate_script, getattr(engine, "generate_voiceover", None)

    def generate_script(title, minutes, job, force, style=""):
        if music_only(engine, style):
            return write(engine, title, minutes, job, style, force)
        return original_script(title, minutes, job, force, style=style)
    engine.generate_script = generate_script

    original_publish = getattr(engine, "publish_named_copy", None)
    if original_publish is not None:
        def publish_named_copy(job, title, fresh=""):
            try:
                credit(job)
            except OSError as e:
                engine.log(f"map credit: not added to the description ({e})")
            return original_publish(job, title, fresh)
        engine.publish_named_copy = publish_named_copy

    if original_voice is not None:
        def generate_voiceover(script, job, force=False, *args, **kwargs):
            style = kwargs.get("style", args[1] if len(args) > 1 else "")
            if music_only(engine, style):
                return voiceover(engine, job, style, force)
            return original_voice(script, job, force, *args, **kwargs)
        engine.generate_voiceover = generate_voiceover
