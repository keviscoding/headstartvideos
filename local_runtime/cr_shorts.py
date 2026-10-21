"""Shorts recipes: a vertical Short (15-60 s) from a topic, made by the Shorts pipelines in the Studio's shorts/ folder.

A style whose JSON says "format": "short" is a Shorts recipe. `install(engine)` wraps the engine's run_custom /
run_pipeline: those jobs go to the Shorts pipeline named by the style ("shorts": {"pipeline": "zack3d" | "photo2d" |
"ranking"}), every other job runs exactly as before. The ranking pipeline (Ranking Shorts: real viral clips counted
down to #1) finds its own clips and needs no asset pack; its options arrive as a [[ranking={...}]] marker. The finished Short is left in the job folder the way a long video is
(video.mp4, title.txt, script.txt, subs.srt, youtube.txt) and published by the engine itself, so Projects, the player,
reveal and re-render work unchanged.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

HOME = Path(__file__).resolve().parent
PIPELINES = {"zack3d": ("zack", "make_zack.py"), "photo2d": ("hoodie", "make_short.py"),
             "ranking": ("ranking", "make_ranking.py")}
PACKLESS = {"ranking"}                                       # pipelines that need no Shorts asset pack
# Pipelines that lower their own priority as they start (make_ranking and board.py call find.lower_priority). nice is
# relative on Mac and Linux: our nice 10 under their own os.nice(10) ran the Short at 19, the very lowest.
SELF_LOWERING = {"ranking"}
SECONDS = (15, 60)
KEEP_LINES = 2000                                            # of a run's output, for short/stopped.log


def shorts_root(home: Path = HOME) -> Path:
    """The Shorts pipelines: shipped as shorts/ in the Studio folder (CR_SHORTS_ROOT overrides, for testing)."""
    env = os.environ.get("CR_SHORTS_ROOT")
    return Path(env) if env else home / "shorts"


def is_short(info) -> bool:
    return isinstance(info, dict) and info.get("format") == "short"


MARK = re.compile(r"\[\[short_seconds=(\d{1,3})\]\]\s*")


RANKING = re.compile(r"\[\[ranking=(\{[^\]]{0,2000}\})\]\]\s*")
RANKING_KEYS = {"ranks": int, "commentary": bool, "booms": bool, "sfx": str, "captions": bool, "punch_ins": bool,
                "clean": bool, "voice": str, "handle": str, "board": str, "choice": str, "use_credits": bool}
# Names that become paths in make_ranking (output/ranking-boards/<board>/<choice>): nothing else gets through.
RANKING_NAMES = {"board": re.compile(r"[a-z0-9-]{1,80}"), "choice": re.compile(r"choice-[A-Za-z0-9-]{1,80}\.json")}


def split_extra(extra: str):
    """The Create page sends the Short's length as a marker at the front of the extra instructions (the run route keeps
    only known on/off features): (seconds or None, the instructions without it)."""
    m = MARK.search(extra or "")
    rest = RANKING.sub("", MARK.sub("", extra or ""))
    return (int(m.group(1)) if m else None), rest.strip()


def ranking_options(extra: str) -> dict:
    """A Ranking Short's options from its [[ranking={...}]] marker: only known keys of the right kind."""
    m = RANKING.search(extra or "")
    try:
        raw = json.loads(m.group(1)) if m else {}
    except ValueError:
        raw = {}
    out = {}
    for key, kind in RANKING_KEYS.items():
        value = raw.get(key) if isinstance(raw, dict) else None
        if value is None or isinstance(value, bool) != (kind is bool):
            continue
        if kind is int and isinstance(value, int):
            out[key] = max(3, min(10, value))
        elif kind is str and isinstance(value, str):
            if key in RANKING_NAMES and not RANKING_NAMES[key].fullmatch(value):
                continue
            out[key] = value[:300]
        elif kind is bool:
            out[key] = value
    return out


def ranking_voice(style: str) -> str:
    """The narrator voice the creator chose for this recipe in Create → Choose voice with ElevenLabs connected
    (cr_voice_library: the engine's voice pick), as the pipeline's voice option ("eleven:<voice id>"); "" for none."""
    try:
        import voices                                            # the engine's voice picks (voice_picks.json)
        pick = voices.picks().get(style) or {}
    except Exception:                                            # noqa: BLE001 - no pick: the recipe's own narrator
        return ""
    vid = str(pick.get("voice_id") or "")
    return "eleven:" + vid if pick.get("provider") == "elevenlabs" and re.fullmatch(r"[A-Za-z0-9]{16,32}", vid) else ""


def seconds_of(info: dict, features, extra: str = "") -> int:
    want = split_extra(extra)[0]
    if want is None and isinstance(features, dict):
        want = features.get("short_seconds")
    try:
        want = int(want) if want is not None else int((info.get("shorts") or {}).get("seconds") or 25)
    except (TypeError, ValueError):
        want = 25
    return max(SECONDS[0], min(SECONDS[1], want))


def ready(home: Path = HOME) -> list:
    """What is missing before a pack-based Short (3D, photo) can be made on this computer ([] when ready)."""
    root, missing = shorts_root(home), []
    for name, (folder, script) in PIPELINES.items():
        if name in PACKLESS:
            continue
        if not (root / folder / script).is_file():
            missing.append("The Shorts pipelines are not installed in this Studio.")
            break
    if not (root / "rbx" / "r3d" / "engine" / "skins" / "rbx" / "index.json").is_file():
        missing.append("The Shorts asset pack (people, animals, sets) is not downloaded yet.")
    return missing


PACK_INDEX = ("rbx", "r3d", "engine", "skins", "rbx", "index.json")   # the last file unpacked: present = the pack is whole


def ensure_pack(sink=None, engine=None, home: Path = HOME) -> None:
    """The Shorts asset pack (people, animals, motion, sounds; about 400 MB): fetched from ChannelRecipe and unpacked into
    shorts/ the first time a Short is made on this computer. Later Shorts find it in place."""
    import zipfile
    root = shorts_root(home)
    if root.joinpath(*PACK_INDEX).is_file():
        return
    import cr_update
    target = home / ".cr-shorts-pack.zip"
    try:
        base, key = cr_update._settings(home)
        _say(sink, engine, "First Short on this computer: downloading the Shorts pack (about 400 MB, only once)")
        cr_update.download(base, key, target, path="/api/studio/shorts-pack")
    except cr_update.UpdateStopped as error:
        target.unlink(missing_ok=True)
        why = re.sub(r"Nothing was changed; (\w)", lambda m: m.group(1).upper(), str(error)).replace("Nothing was changed. ", "")
        raise RuntimeError("The Shorts pack couldn't be downloaded. " + why) from None
    _say(sink, engine, "Unpacking the Shorts pack")
    index = "/".join(PACK_INDEX)
    with zipfile.ZipFile(target) as pack:
        members = pack.infolist()
        for m in members:                                        # nothing may land outside shorts/
            parts = Path(m.filename).parts
            if m.filename.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
                raise RuntimeError("The Shorts pack looks damaged. Try again.")
        for m in sorted(members, key=lambda m: m.filename == index):   # the index last: an interrupted unpack is redone
            pack.extract(m, root)
    target.unlink(missing_ok=True)
    _say(sink, engine, "Shorts pack ready")


def _srt(words: list, path: Path):
    """captions of 2-3 words a line from the voice's word timings"""
    def ts(t):
        ms = int(round(max(0.0, float(t)) * 1000))              # whole milliseconds (10.279 had printed as 10,278)
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        return f"{h:02d}:{m:02d}:{ms // 1000:02d},{ms % 1000:03d}"
    chunks, i = [], 0
    while i < len(words):
        chunk = words[i:i + 3] if len(words) - i != 4 else words[i:i + 2]
        chunks.append(chunk)
        i += len(chunk)
    out = []
    for n, chunk in enumerate(chunks):                          # a caption ends where the next begins: aligned words
        start, end = float(chunk[0][0]), float(chunk[-1][1])    # can overlap by a tenth of a second, and overlapping
        if n + 1 < len(chunks):                                 # cues flicker or stack in some players
            end = min(end, float(chunks[n + 1][0][0]) - 0.001)
        end = max(end, start + 0.05)
        out.append(f"{n + 1}\n{ts(start)} --> {ts(end)}\n{' '.join(w[2] for w in chunk)}\n")
    path.write_text("\n".join(out), encoding="utf-8")


# the pipelines' step lines, in the words Studio's progress view knows (cr_workspace.js phaseCopy). The ranking
# pipeline's step words are fixed by its contract: finding, picking, watching, looking, writing, sound design, voice,
# edit, captions, sound, note, done:.
STEPS = [(r"^(finding|picking)", "Finding the clips: searching for the moments that rank"),
         (r"^watching", "Watching candidate clips for their best moments"),
         (r"^looking", "Watching the picks closely for their best seconds"),
         (r"^writing", "Writing the script: the narrator's lines"),
         (r"^sound design", "Designing the sound: vine booms, punch-ins and sound effects"),
         (r"^director", "Writing the script: planning the story, sets and shots"),
         (r"^(sets \+ props|props)", "Building the sets and the story's props"),
         (r"^voice", "Creating narration voice"),
         (r"^(scenes|scene pictures|render)", "Building visual scenes"),
         # text removal is the edit's long part (minutes a clip): its own words, so a frozen-looking render reads right
         (r"^edit\b.*\b(clean|declutter|paint)", "final render: taking burned-in text off the clips"),
         (r"^edit", "final render: cutting the shots together"),
         (r"^(captions|sound)", "Timing narration and subtitles, music and sound effects")]
NOTE = re.compile(r"^note\b[:\s]*")


def _say(sink, engine, line: str):
    line = line.rstrip()
    if not line:
        return
    if NOTE.match(line.strip()):                                  # something the creator should know, as it is
        line = "Note: " + NOTE.sub("", line.strip())
    for pat, words in STEPS:
        if re.match(pat, line.strip()):
            line = f"{words} ({line.strip()})"
            break
    for fn in (sink, getattr(engine, "log", None)):
        if callable(fn):
            try:
                fn(line)
                return
            except Exception:                                    # noqa: BLE001 - progress must never stop the Short
                continue
    print(line, flush=True)


def child_env(**extra) -> dict:
    """The environment of a Shorts pipeline process (a Short, or a Clip Board worker). UTF-8 everywhere: on Windows a
    child's files and pipes default to the cp1252 code page, so the ranking pipeline died at import (lines_bank.json
    holds emoji), then on the first emoji label it printed and on every picked.json, plan.json and edit.json it wrote.
    PYTHONIOENCODING too, because it outranks UTF-8 mode for the pipes when a creator has it set."""
    return {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8:replace",
            **{k: str(v) for k, v in extra.items()}}


def low_priority() -> dict:
    """Popen arguments for a pipeline process: below normal priority on Windows (it paints, encodes and transcribes on
    every core for minutes; the Studio and the creator's computer stay responsive). Its ffmpeg and Claude children
    inherit it, and a priority class is absolute, so a pipeline that lowers itself too stays below normal. POSIX gets
    nice() through lower_priority() once the process exists."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)}
    return {}


def lower_priority(proc) -> None:
    """nice 10 for a pipeline process on Mac and Linux that does not lower itself (never one in SELF_LOWERING, nor a
    Clip Board worker: nice adds up), set before it has started any work of its own."""
    if os.name == "nt" or not hasattr(os, "setpriority"):
        return
    try:
        os.setpriority(os.PRIO_PROCESS, proc.pid, max(10, os.getpriority(os.PRIO_PROCESS, proc.pid)))
    except OSError:                                              # already gone, or not ours to change: run as it is
        pass


_JS = {"ok": False}


def js_runtime_missing(then: str = "make the Short again") -> str:
    """'' when yt-dlp can solve YouTube's JavaScript challenge on this computer, else what to tell the creator. Every
    YouTube download needs a JavaScript runtime (Deno, Node.js 22 or newer, Bun); without one a Short found out only
    at the pick downloads, after it had paid for its scouting and close looks. yt-dlp's own checks decide, so its
    minimum versions move with it; when they can't be asked, or the creator set the runtimes themselves (YTDLP_ARGS),
    nothing is refused and yt-dlp says what is wrong as before."""
    if _JS["ok"] or "js-runtimes" in os.environ.get("YTDLP_ARGS", ""):
        return ""
    try:
        from yt_dlp.utils import _jsruntime as jsr
        kinds = [jsr.DenoJsRuntime, jsr.NodeJsRuntime, jsr.BunJsRuntime, jsr.QuickJsRuntime]
        need = int(jsr.NodeJsRuntime.MIN_SUPPORTED_VERSION[0])
        seen = {}
        for kind in kinds:
            info = kind().info                                   # runs `<runtime> --version`; None when absent
            if info is not None and info.supported:
                _JS["ok"] = True                                 # a runtime never goes away while the Studio runs
                return ""
            if info is not None:
                seen[info.name] = info.version
    except Exception:                                            # noqa: BLE001 - can't tell: never refuse on a guess
        return ""
    if "node" in seen:
        return (f"The YouTube downloader needs Node.js {need} or newer, and this computer has Node.js {seen['node']}. "
                f"Update Node.js from nodejs.org, restart the Studio and {then}. Nothing was watched or charged.")
    return (f"The YouTube downloader needs a JavaScript runtime that this computer doesn't have. Install Node.js {need} "
            f"or newer from nodejs.org, restart the Studio and {then}. Nothing was watched or charged.")


def needs_youtube(ranking: dict, home: Path = HOME) -> bool:
    """Will this Ranking Short download clips from YouTube? A topic Short does. A Clip Board countdown does when a pick
    is a YouTube moment; the creator's own clips and the links the board downloaded are files already."""
    board = ranking.get("board")
    if not board:
        return True
    try:
        snapshot = home / "output" / "ranking-boards" / board / (ranking.get("choice") or "choice.json")
        picks = json.loads(snapshot.read_text(encoding="utf-8")).get("picks") or []
    except (OSError, ValueError, AttributeError):
        return False                                             # make_ranking says what is wrong with the countdown
    return any(isinstance(p, dict) and not p.get("file") for p in picks)


TIMESTAMP = re.compile(r"^\d\d:\d\d:\d\d ")


def _crashed(lines: list) -> bool:
    """Did Python end the pipeline with an exception nobody handled? Its traceback then comes after the last progress
    line. (Only the last 40 lines were checked before, so a deep traceback's header fell outside them and its
    exception line read as the pipeline's own message.)"""
    head = step = -1
    for i, line in enumerate(lines):
        if "Traceback (most recent call last)" in line:
            head = i
        elif TIMESTAMP.match(line) or line.startswith("done:"):     # never a traceback's quoted source line
            step = i
    return head > step


def stop_message(work: Path, output: list) -> str:
    """Why a Short stopped, in the creator's words. The ranking pipeline writes work/error.json on every stop it
    understands ({code, message, action, saved}); anything else is the last line Python printed, never a traceback.
    What the pipeline printed (its last KEEP_LINES lines) goes to short/stopped.log for support."""
    try:
        (work / "stopped.log").write_text("".join(output), encoding="utf-8")
    except OSError:
        pass
    try:
        got = json.loads((work / "error.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = None
    if isinstance(got, dict) and str(got.get("message") or "").strip():
        message, action = str(got["message"]).strip(), str(got.get("action") or "").strip()
        return message if not action or action in message else f"{message} {action}"
    lines = [l.strip() for l in output if l.strip()]
    last = lines[-1] if lines else ""
    progress = bool(TIMESTAMP.match(last)) or "done:" in last
    if last and not progress and not _crashed(output):
        return last                                              # the pipeline's own message (SystemExit)
    last = TIMESTAMP.sub("", last)                               # an exception's last line, or the step it was on
    return ("The Short stopped unexpectedly" + (f" ({last[:300]})" if last else "") + ". Your progress is saved: make "
            "it again to carry on from where it stopped. The details are in the project's short/stopped.log.")


def run_short(engine, title: str, style: str, info: dict, *, features=None, sink=None, extra: str = "") -> Path:
    """Make the Short for `title` with the Shorts recipe `style`; returns the job folder."""
    pipe = str((info.get("shorts") or {}).get("pipeline") or "zack3d")
    if pipe not in PIPELINES:
        raise RuntimeError(f"Unknown Shorts pipeline {pipe!r}.")
    folder, script = PIPELINES[pipe]
    root = shorts_root()
    if pipe in PACKLESS:
        if not (root / folder / script).is_file():
            raise RuntimeError("The Ranking Shorts pipeline is not installed in this Studio. Update the Studio.")
    else:
        ensure_pack(sink, engine)
        blockers = ready()
        if blockers:
            raise RuntimeError(" ".join(blockers))
    if pipe == "ranking" and re.match(r"\s*(https?://|www\.)", title or "", re.I):
        raise RuntimeError("Type what to rank, for example Funniest Dog Moments, not a link. To make a Short from "
                           "clips you have links to, paste them on the Clip Board.")
    seconds = seconds_of(info, features, extra)
    ranking = ranking_options(extra)
    if pipe == "ranking" and not ranking.get("voice"):
        picked = ranking_voice(style)
        if picked:
            ranking["voice"] = picked                            # the voice chosen for this recipe in Choose voice
    extra = split_extra(extra)[1]
    if pipe == "ranking" and needs_youtube(ranking):
        missing = js_runtime_missing()
        if missing:                                              # before anything is searched, watched or paid for
            raise RuntimeError(missing)
    job = Path(engine.job_dir(title, style))
    job.mkdir(parents=True, exist_ok=True)
    if hasattr(engine, "_unpublish"):
        engine._unpublish(job, title)                            # a re-render of the same title starts from its own work
    published = False
    try:
        work = job / "short"
        work.mkdir(exist_ok=True)
        (work / "error.json").unlink(missing_ok=True)            # an earlier attempt's stop must not explain this one
        (job / "title.txt").write_text(title + "\n", encoding="utf-8")
        (job / "style.txt").write_text(style + "\n", encoding="utf-8")       # the sound check reads the recipe from here
        env = child_env(CR_STUDIO=HOME, CR_SHORTS_ROOT=root)
        if extra:
            env["CR_SHORTS_EXTRA"] = str(extra)[:4000]
        tone = str((info.get("shorts") or {}).get("tone") or "").strip().lower()
        if tone:
            env["CR_SHORTS_TONE"] = tone                         # dark: true crime / horror (the director's dark-story rules)
        if pipe == "ranking":
            env["CR_RANKING"] = json.dumps(ranking)
        args = [sys.executable, str(root / folder / script), str(HOME), str(work), title]
        args += (["auto", str(seconds)] if pipe == "photo2d" else [str(seconds)])
        _say(sink, engine, f"Short: {info.get('label') or style}, " + (f"{ranking.get('ranks', 5)} ranks" if pipe == "ranking"
                                                                        else f"about {seconds} seconds"))
        proc = subprocess.Popen(args, cwd=str(root / folder), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", **low_priority())
        if pipe not in SELF_LOWERING:
            lower_priority(proc)
        output = deque(maxlen=KEEP_LINES)                         # bounded: a run prints a few hundred lines
        for line in proc.stdout:
            output.append(line if len(line) <= 4000 else line[:4000] + "...\n")
            if TIMESTAMP.match(line) or "done:" in line:          # the pipeline's own progress lines
                _say(sink, engine, line[9:] if TIMESTAMP.match(line) else line)
        if proc.wait() != 0 or not (work / "short.mp4").is_file():
            raise RuntimeError(stop_message(work, list(output)))
        shutil.copyfile(work / "short.mp4", job / "video.mp4")
        plan = json.loads((work / "plan.json").read_text(encoding="utf-8")) if (work / "plan.json").is_file() else {}
        narration = plan.get("narration") or " ".join(l.get("text", "") for l in plan.get("lines") or [])
        (job / "script.txt").write_text(narration.strip() + "\n", encoding="utf-8")
        voice = json.loads((work / "voice.json").read_text(encoding="utf-8")) if (work / "voice.json").is_file() else {}
        if voice.get("words"):
            _srt(voice["words"], job / "subs.srt")
        name = plan.get("title") or title
        case = json.loads((work / "research.json").read_text(encoding="utf-8")) if (work / "research.json").is_file() else {}
        sources = "".join(f"\n{u}" for u in (case.get("sources") or [])[:8]) if case.get("real") else ""
        (job / "youtube.txt").write_text(f"{name}\n\n{narration.strip()}\n" + (f"\nSources:{sources}\n" if sources else "") + "\n#shorts\n", encoding="utf-8")
        if hasattr(engine, "publish_named_copy"):
            engine.publish_named_copy(job, title)
        published = True
    finally:
        # A re-render that stopped hands the creator's published video back to their output folder (_unpublish moved
        # it into the job; the engine's own run would put it back in its finally, which this path never reaches).
        if not published and hasattr(engine, "_republish_if_left"):
            try:
                engine._republish_if_left(job, title)
            except Exception:                                    # noqa: BLE001 - never mask why the Short stopped
                pass
    _say(sink, engine, "Short finished.")
    return job


def install(engine):
    """Shorts recipes take their own path; every other job runs the engine exactly as before."""
    for name in ("run_custom", "run_pipeline"):
        original = getattr(engine, name, None)
        if not callable(original) or getattr(original, "_cr_shorts", False):
            continue

        def wrapped(title, minutes=20, *args, _original=original, **kwargs):
            style = kwargs.get("style") or ""
            info = (getattr(engine, "STYLE_INFO", {}) or {}).get(style)
            if not is_short(info):
                return _original(title, minutes, *args, **kwargs)
            return run_short(engine, title, style, info, features=kwargs.get("features"), sink=kwargs.get("sink"),
                             extra=kwargs.get("extra") or "")
        wrapped._cr_shorts = True
        setattr(engine, name, wrapped)
