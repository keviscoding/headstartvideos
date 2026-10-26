"""Captions a creator asked for are burned in, or the video says plainly why not.

Customer ALEX (Studio 104, 2026-10-26): "Burn captions into the video" was on, the log said "subtitles: 0 lines + 0
kinetic" and the finished video had no captions. Two causes in the private engine (make_video.py, read only):

- `_build_burn_ass` drops every caption when the style says `look.captions.off`, whatever the creator chose. Twelve
  recipes ship that way, and a cloned style gets it when its channel shows subtitles less than 30% of the time
  (cr_style_map._text), yet Create's switch was on for every recipe. Folio and Vox collage recipes switch them off
  in their own settings the same way.
- `_eleven_tts_one` (the creator's own ElevenLabs key) writes subs.srt from ElevenLabs' character alignment. An
  answer without one leaves subs.srt empty with no error, and `generate_voiceover` reuses a saved audio.mp3 without
  checking its subs.srt.

Now:
- Create's switch starts at the recipe's own choice (cr_workspace catalog `captions`) and says so. Turning it on for
  a recipe without captions asks for them: the run carries cr_captions="on" (cr_submission, kept in run_spec.json),
  and they are burned in with the recipe's other caption settings.
- A narration with no word timings is timed on this computer (the engine's Whisper, free) right after the voice
  step, and again before the final mux.
- A video that should carry captions but ends up with none (no words could be timed, or every line fell on a
  graphics scene, where captions are never drawn) logs one plain "Captions: ..." line, which the progress card
  and Claude Code's progress tool show, and keeps cr-captions.json beside the video.
"""
from __future__ import annotations

import json
import re
from functools import wraps
from pathlib import Path

ASKED = "cr_captions"                 # run_spec.json: "on" when the creator turned captions on for this video
RECORD = "cr-captions.json"           # what the final mux burned in, and why none when none
_CUE = re.compile(r"(\d\d):(\d\d):(\d\d)[,.](\d+)\s*-->\s*(\d\d):(\d\d):(\d\d)[,.](\d+)")


def style_default(info) -> bool:
    """Whether a recipe burns captions by its own design (Create's switch starts here): not when its look says
    captions.off, nor a Folio recipe without folio.captions, nor a Vox collage recipe whose vox.subtitles is off
    (Vox's own defaults have none)."""
    look = (info or {}).get("look") if isinstance(info, dict) else None
    look = look if isinstance(look, dict) else {}
    if isinstance(look.get("captions"), dict) and look["captions"].get("off"):
        return False
    visuals = str(look.get("visuals") or "")
    if visuals == "folio":
        return bool((look.get("folio") or {}).get("captions"))
    if visuals == "vox":
        vox = look.get("vox")
        return isinstance(vox, dict) and bool(vox) and vox.get("subtitles", True) is not False
    return True


def asked(job) -> bool:
    """Whether the creator asked for captions on this video (Create's switch on, or make_videos captions=true)."""
    try:
        spec = json.loads((Path(job) / "run_spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(spec, dict) and spec.get(ASKED) == "on"


def cue_count(srt) -> int:
    """Subtitle lines with words and a real duration (what the engine's burn-in builder can draw)."""
    try:
        text = Path(srt).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    count = 0
    for block in re.split(r"\n\s*\n", text.strip()):
        m = _CUE.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        words = " ".join(line for line in block.splitlines() if "-->" not in line and not line.strip().isdigit())
        if words.strip() and end > start:
            count += 1
    return count


def _log(engine, message: str) -> None:
    try:
        getattr(engine, "log", print)(message)
    except Exception:                                          # noqa: BLE001 - a progress line only
        pass


def ensure_timing(engine, script: str, audio, srt) -> int:
    """subs.srt with words in it: a narration that came back without word timings (an empty or missing subs.srt) is
    timed on this computer from its audio, spelled as the script. Returns the lines it now holds (0: none could be
    made; the video goes on, and the final mux says so if captions were wanted)."""
    have = cue_count(srt)
    if have or not Path(audio).is_file():
        return have
    import cr_media
    _log(engine, "Captions: the narration came back without word timings; timing it on this computer.")
    try:
        cr_media._time_narration(engine, Path(audio), Path(srt), script or "")
    except (Exception, cr_media.AccountStop) as error:         # AccountStop is a BaseException
        _log(engine, f"Captions: the narration's words couldn't be timed ({str(error)[:160]}).")
        return cue_count(srt)
    return cue_count(srt)


def _script(job: Path) -> str:
    try:
        return (job / "script.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _drawn(ass) -> int:
    """Caption events in a burn-in file: every Dialogue line except the on-screen text labels (the Ovl* styles)."""
    try:
        lines = Path(ass).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    count = 0
    for line in lines:
        if line.startswith("Dialogue:"):
            fields = line.split(",", 4)
            if len(fields) > 3 and not fields[3].strip().startswith("Ovl"):
                count += 1
    return count


def _record(job: Path, **values) -> None:
    try:
        (job / RECORD).write_text(json.dumps(values), encoding="utf-8")
    except OSError:
        pass


def install(engine) -> None:
    """Wrap the engine's burn-in builder and final mux (in Studio and in each video's worker)."""
    build, final = getattr(engine, "_build_burn_ass", None), getattr(engine, "_final_mux", None)
    if not callable(build) or not callable(final) or getattr(build, "_cr_captions", False):
        return

    @wraps(build)
    def build_burn_ass(srt, out_ass, mute_ranges=None, center=False, style="", captions=True, *args, **kwargs):
        job = Path(out_ass).parent
        table = getattr(engine, "STYLE_CAPTIONS", None)
        cap = table.get(style) if isinstance(table, dict) else None
        off = isinstance(cap, dict) and bool(cap.get("off"))
        lifted = bool(captions and off and asked(job))
        if lifted:
            table[style] = {k: v for k, v in cap.items() if k != "off"}       # asked for: this video's captions on
        try:
            out = build(srt, out_ass, mute_ranges, center, style, captions, *args, **kwargs)
        finally:
            if lifted:
                table[style] = cap
        if not captions:
            return out
        if off and not lifted:
            _record(job, asked=False, lines=0, why="style")
            _log(engine, "Captions: off. This style has none, like its channel. Turn on Burn captions into the "
                         "video in Create to add them to a video.")
            return out
        drawn = _drawn(out if out else out_ass)
        if drawn:
            _record(job, asked=True, lines=drawn)
            if lifted:
                _log(engine, f"Captions: on as asked ({drawn} lines), though this style normally has none.")
            return out
        why = ("its narration has no timed words" if not cue_count(srt) else
               "every line fell on a graphics scene, where captions are not drawn")
        _record(job, asked=True, lines=0, why=why)
        _log(engine, f"Captions: none in this video: {why}. The rest of the video is finished.")
        return out
    build_burn_ass._cr_captions = True

    @wraps(final)
    def final_mux(job, mp3, srt, burn_subs=None, *args, **kwargs):
        job, srt = Path(job), Path(srt)
        if burn_subs is None:
            burn_subs = kwargs.pop("burn_subs", False)
        want = bool(burn_subs) or asked(job)
        if want and not cue_count(srt) and srt.parent == job and srt.name == "subs.srt":
            narration = job / "audio.mp3"
            ensure_timing(engine, _script(job), narration if narration.is_file() else mp3, srt)
        result = final(job, mp3, srt, want, *args, **kwargs)
        if want and not srt.is_file():
            # no subtitle file at all: the engine burns nothing and says nothing
            _record(job, asked=True, lines=0, why="no subtitle file")
            _log(engine, "Captions: none in this video: its narration has no subtitle file. The rest of the video "
                         "is finished.")
        return result
    final_mux._cr_captions = True

    engine._build_burn_ass, engine._final_mux = build_burn_ass, final_mux


def after_voice(engine, script: str, out) -> None:
    """Right after the engine's own voice step (own ElevenLabs, Algrow, ai33): an empty subs.srt is timed here, so
    the scenes, chapters and captions that read it all have its words."""
    try:
        audio, srt = out
    except (TypeError, ValueError):
        return
    if srt is None:
        return
    ensure_timing(engine, script, audio, srt)
