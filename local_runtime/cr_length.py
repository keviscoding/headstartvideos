"""A video is as long as the creator chose in Create: the script is planned at the pace its narrator really reads.

A customer (JP, Discord, 2026-10-29) chose 2 minutes for a video in a style he had cloned, twice, and got about one
minute both times. The engine writes roughly `minutes x words-a-minute` words (make_video.generate_script, its
STYLE_WPM table, filled from the style's pacing.wpm), accepts a draft from 70 % of that, and for a film of 3 minutes or
less only ever shortens one that came out long. A cloned style's pacing.wpm is not the narrator's pace at all: it is
either copied from the base recipe (documentary says 120) or measured on the source channel's captions per minute of
VIDEO, pauses and music included, then clamped to 110-210. Studio's narrators (Kie's Gemini voices) read about 160 words
a minute: the median of 16 narrations on the owner's Studio, 138 to 173. So 2 minutes x 120 = 240 words, written a
little short (anything from 168 passes) and read at 160, is a 63 to 90 second video.

Three things, all in this module:
- the word target uses the pace the chosen narrator was measured at on this Studio's earlier videos (words heard per
  minute of its narration, per voice and language); with no measurement yet, a learned style uses Studio's narrator
  pace (PACE_DEFAULT) instead of the reference's, and a built-in recipe keeps its own tuned pace, as before; a video in
  another language scales either by that language's pace (cr_language.pace);
- a script that still comes out well short of the target (under SHORT of it) is lengthened by Claude once, before the
  fact-check, so the added lines are checked too; a pasted script, or one a voice was already recorded from, is never
  touched;
- after the voice, the narration's real pace is kept for the next video (.cr-voice-pace.json in the Studio folder,
  kept by updates like every .cr-* file), and a narration far shorter than the length chosen says so in the log.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import threading
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
STORE = HOME / ".cr-voice-pace.json"
PACE_DEFAULT = 160.0          # words a minute Studio's narrators read at (median of 16 narrations, 138-173)
PACE_LIMITS = (120.0, 200.0)  # a measured narrator pace is used only inside these
SAMPLE_LIMITS = (90.0, 240.0) # a narration outside these is not a reading pace (pauses, a test clip, a bad timing)
SAMPLE_MIN_S = 20.0           # shorter narrations measure their silences more than their pace
SAMPLES_NEEDED = 2            # measured narrations before a voice's own pace is trusted
SAMPLES_KEPT = 12
SHORT = 0.85                  # a script under this share of its target is lengthened once
LENGTHEN_MAX_WORDS = 2400     # longer scripts (about 15 minutes) are written in parts; a full rewrite risks cutting
ROOM = 1.10                   # a lengthened script never goes past this share of its target
TOO_SHORT = 0.8               # a narration under this share of the minutes chosen is reported in the log

_lock = threading.Lock()
_asked: dict = {}             # job folder -> {"minutes", "target", "pace"} for the run in this worker


def spoken_words(text: str) -> int:
    """Words the narrator reads: chapter lines ("## Title | mood") and clip handoffs ("[[clip: ...]]") don't count."""
    return sum(len(line.split()) for line in str(text or "").splitlines() if not _marker(line))


def _marker(line: str) -> bool:
    s = line.strip()
    return bool(re.match(r"#{1,3}\s*\S", s)) or "[[clip:" in s.lower()


def _markers(text: str) -> int:
    return sum(1 for line in str(text or "").splitlines() if _marker(line))


def learned(info) -> bool:
    """A style Studio learned from a channel (Style Lab, a clone): its pacing.wpm is the reference's, not a voice's."""
    return isinstance(info, dict) and bool(info.get("cr_style_clone"))


def _language(engine, style: str) -> str:
    try:
        import cr_language
        chosen = cr_language.current()
    except ImportError:
        chosen = ""
    if chosen:
        return chosen
    return str(((getattr(engine, "STYLE_LANGUAGE", {}) or {}).get(style)) or "en").strip().lower() or "en"


def _language_factor() -> float:
    """Words a minute in the video's chosen language against English (1.0 for English or the style's own)."""
    try:
        import cr_language
        chosen = cr_language.current()
        return float(cr_language.pace(chosen)) if chosen else 1.0
    except (ImportError, ValueError, TypeError):
        return 1.0


def voice_key(engine, job, style: str) -> str:
    """Which narrator reads this video, the way cr_media.narrate picks it: the video's own voice, the style's narrator
    pinned when it started, the style's learned narrator, else the voice in Setup & accounts. A video the engine
    voices itself (narration not through Studio's accounts) is keyed by its style."""
    lang = _language(engine, style)
    try:
        import cr_media
        if cr_media.active("narration"):
            info = (getattr(engine, "STYLE_INFO", {}) or {}).get(style)
            chosen = (cr_media.job_voice(job) or cr_media._voice_in(Path(job) / cr_media.STYLE_VOICE_PIN)
                      or cr_media.style_voice(style, info))
            cfg = chosen if chosen.get("provider") == "kie" else cr_media.profile("narration")
            model = cr_media.narration_model(cfg["provider"], cfg.get("model"))
            voice = cfg.get("voice") or model.get("default_voice") or ""
            return f"{cfg['provider']}|{model.get('id', '')}|{voice}|{lang}"
    except Exception:                                   # noqa: BLE001 - an unknown narrator is keyed by its style
        pass
    return f"engine|{style}|{lang}"


def _load() -> dict:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def measured(key: str):
    """(words a minute, narrations measured) for this narrator, or (None, n) until SAMPLES_NEEDED were measured."""
    samples = [float(x) for x in (_load().get(key) or []) if isinstance(x, (int, float))]
    if len(samples) < SAMPLES_NEEDED:
        return None, len(samples)
    lo, hi = PACE_LIMITS
    return max(lo, min(hi, statistics.median(samples))), len(samples)


def record(key: str, words: int, seconds: float) -> float | None:
    """Keep one narration's reading pace for its narrator; None when it isn't one worth keeping."""
    if not key or not words or seconds < SAMPLE_MIN_S:
        return None
    wpm = words / (seconds / 60.0)
    if not SAMPLE_LIMITS[0] <= wpm <= SAMPLE_LIMITS[1]:
        return None
    with _lock:
        data = _load()
        data[key] = ([float(x) for x in (data.get(key) or []) if isinstance(x, (int, float))] + [round(wpm, 1)])[
            -SAMPLES_KEPT:]
        tmp = STORE.with_name(STORE.name + f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(STORE)
        except OSError:
            tmp.unlink(missing_ok=True)
            return None
    return wpm


def pace_for(engine, job, style: str) -> tuple:
    """(words a minute to plan this script at, why) for the narrator who will read it."""
    info = (getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}
    own = float((getattr(engine, "STYLE_WPM", {}) or {}).get(style) or getattr(engine, "WPM", 140) or 140)
    heard, n = measured(voice_key(engine, job, style))
    if heard:
        return round(heard, 1), f"this narrator's own pace, measured on {n} earlier videos"
    factor = _language_factor()
    if learned(info):
        return round(PACE_DEFAULT * factor, 1), (f"Studio's narrators' pace (the learned {own:.0f} is the source "
                                                 "channel's, pauses and music included)")
    return round(own * factor, 1), "this recipe's pace" + (" in the video's language" if factor != 1.0 else "")


def _voiced(job: Path) -> bool:
    return (job / "audio.mp3").exists() or any(job.glob("audio_p*.mp3"))


def lengthen(engine, job, script: str, style: str, target: int, force: bool = False) -> str:
    """The script, lengthened once by Claude when it came out under SHORT of its target (else as it was)."""
    job = Path(job)
    if (not target or target > LENGTHEN_MAX_WORDS or (job / "script.pasted").exists()
            or (_voiced(job) and not force)):
        return script
    have = spoken_words(script)
    if not have or have >= SHORT * target:
        return script
    log = getattr(engine, "log", print)
    log(f"Length: the script came out at {have} words and this length needs about {target}. Claude lengthens it "
        f"once (on your Claude plan, no money)...")
    best, marks = script, _markers(script)
    for _ in range(2):
        now = spoken_words(best)
        ask = ("Lengthen this narration to about " + str(target) + " spoken words, never more than "
               + str(int(target * ROOM)) + "; count them (it is now " + str(now) + " words: add about "
               + str(target - now) + "). Keep its voice, its opening line and its final line, and every sentence "
               "already there (you may extend or join them). Add substance the story is missing: a concrete detail, "
               "an example, a consequence or a step it skipped, in the same register; no filler, no repetition, no "
               "new claims you are not sure of. Keep every line that starts with '## ' or '[[clip:' exactly as it is, "
               "in its place: they are not narration and do not count as words. Return only the narration, no notes."
               "\n\nNARRATION:\n" + best)
        try:
            raw = engine.claude(engine._lang_prompt(ask, style), engine.SCRIPT_BODY_MODEL,
                                max_tokens=max(4000, int(target * 3)), provider=engine.SCRIPT_PROVIDER)
            cand = engine._clean_script(raw)
        except Exception as e:                          # noqa: BLE001 - a length pass is never worth the video
            log(f"Length: kept the script as written ({type(e).__name__}: {str(e)[:100]})")
            break
        got = spoken_words(cand)
        if (not engine._looks_like_refusal(cand) and now < got <= target * (ROOM + 0.15)
                and _markers(cand) >= marks):
            best = cand
        if spoken_words(best) >= SHORT * target:
            break
    if best is not script:
        (job / "script.txt").write_text(best, encoding="utf-8")
        log(f"Length: script lengthened from {have} to {spoken_words(best)} words (target {target}).")
    return best


def _clock(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def after_voice(engine, job, script: str, style: str, before) -> None:
    """Keep the narration's real pace for its narrator; say plainly when it is far shorter than the length chosen."""
    job = Path(job)
    mp3 = job / "audio.mp3"
    if (job / "voice.own").exists() or not mp3.exists():
        return
    stat = mp3.stat()
    if before == (stat.st_size, stat.st_mtime_ns):
        return                                          # the voice was already there (a Resume): measured then
    seconds = float(engine._audio_dur(mp3) or 0)
    words = spoken_words(script)
    record(voice_key(engine, job, style), words, seconds)
    asked = _asked.get(str(job)) or {}
    minutes = float(asked.get("minutes") or 0)
    if minutes and seconds and not (job / "script.pasted").exists() and seconds < TOO_SHORT * minutes * 60:
        engine.log(f"Length: the narration runs {_clock(seconds)} for a {minutes:g}-minute video ({words} words at "
                   f"{words / (seconds / 60):.0f} a minute). Studio now plans this narrator's scripts at that pace.")


def install(engine):
    """Wrap the engine's script writer, fact-check and voiceover once."""
    if getattr(engine, "_cr_length", False) or not hasattr(engine, "generate_script"):
        return
    engine._cr_length = True
    original_script = engine.generate_script
    original_checked = getattr(engine, "_factchecked", None)
    original_voice = getattr(engine, "generate_voiceover", None)

    def generate_script(title, minutes, job, force, style=""):
        table = getattr(engine, "STYLE_WPM", None)
        if not isinstance(table, dict):
            return original_script(title, minutes, job, force, style=style)
        had, before = style in table, table.get(style)
        try:
            pace, why = pace_for(engine, job, style)
        except Exception:                               # noqa: BLE001 - the engine's own pace, as before
            pace, why = float(before or getattr(engine, "WPM", 140)), "this recipe's pace"
        target = int(float(minutes or 0) * pace)
        _asked[str(job)] = {"minutes": float(minutes or 0), "target": target, "pace": pace, "at": time.time()}
        if abs(pace - float(before or getattr(engine, "WPM", 140))) >= 1:
            engine.log(f"Length: {minutes} min at about {pace:.0f} words a minute ({why}): about {target} words.")
            table[style] = pace
        try:
            return original_script(title, minutes, job, force, style=style)
        finally:
            if had:
                table[style] = before
            else:
                table.pop(style, None)
            (_asked.get(str(job)) or {})["target"] = 0  # the fact-check outside a script run lengthens nothing

    engine.generate_script = generate_script

    if original_checked is not None:
        def _factchecked(job, script, style, title, force=False):
            target = (_asked.get(str(job)) or {}).get("target") or 0
            if target:
                try:
                    script = lengthen(engine, job, script, style, target, force)
                except Exception as e:                  # noqa: BLE001 - a length pass is never worth the video
                    engine.log(f"Length: kept the script as written ({type(e).__name__}: {str(e)[:100]})")
            return original_checked(job, script, style, title, force)
        engine._factchecked = _factchecked

    if original_voice is not None:
        def generate_voiceover(script, job, force, *args, **kwargs):
            style = kwargs.get("style", args[1] if len(args) > 1 else "")
            mp3 = Path(job) / "audio.mp3"
            try:
                st = mp3.stat()
                before = (st.st_size, st.st_mtime_ns)
            except OSError:
                before = None
            out = original_voice(script, job, force, *args, **kwargs)
            try:
                after_voice(engine, job, script, style, before)
            except Exception:                           # noqa: BLE001 - a measurement is never worth the video
                pass
            return out
        engine.generate_voiceover = generate_voiceover
