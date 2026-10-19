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
from pathlib import Path

HOME = Path(__file__).resolve().parent
PIPELINES = {"zack3d": ("zack", "make_zack.py"), "photo2d": ("hoodie", "make_short.py"),
             "ranking": ("ranking", "make_ranking.py")}
PACKLESS = {"ranking"}                                       # pipelines that need no Shorts asset pack
SECONDS = (15, 60)


def shorts_root(home: Path = HOME) -> Path:
    """The Shorts pipelines: shipped as shorts/ in the Studio folder (CR_SHORTS_ROOT overrides, for testing)."""
    env = os.environ.get("CR_SHORTS_ROOT")
    return Path(env) if env else home / "shorts"


def is_short(info) -> bool:
    return isinstance(info, dict) and info.get("format") == "short"


MARK = re.compile(r"\[\[short_seconds=(\d{1,3})\]\]\s*")


RANKING = re.compile(r"\[\[ranking=(\{[^\]]{0,2000}\})\]\]\s*")
RANKING_KEYS = {"ranks": int, "commentary": bool, "booms": bool, "sfx": str, "captions": bool, "punch_ins": bool,
                "clean": bool, "voice": str, "handle": str, "board": str, "use_credits": bool}


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
            out[key] = value[:300]
        elif kind is bool:
            out[key] = value
    return out


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
        h, r = divmod(max(0.0, t), 3600)
        m, s = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}"
    out, i, n = [], 0, 1
    while i < len(words):
        chunk = words[i:i + 3] if len(words) - i != 4 else words[i:i + 2]
        out.append(f"{n}\n{ts(chunk[0][0])} --> {ts(chunk[-1][1])}\n{' '.join(w[2] for w in chunk)}\n")
        i, n = i + len(chunk), n + 1
    path.write_text("\n".join(out), encoding="utf-8")


# the pipelines' step lines, in the words Studio's progress view knows (cr_workspace.js phaseCopy)
STEPS = [(r"^(finding|picking)", "Finding the clips: searching for the moments that rank"),
         (r"^watching", "Watching candidate clips for their best moments"),
         (r"^writing", "Writing the script: the narrator's lines"),
         (r"^sound design", "Designing the sound: vine booms, punch-ins and sound effects"),
         (r"^director", "Writing the script: planning the story, sets and shots"),
         (r"^(sets \+ props|props)", "Building the sets and the story's props"),
         (r"^voice", "Creating narration voice"),
         (r"^(scenes|scene pictures|render)", "Building visual scenes"),
         (r"^edit", "final render: cutting the shots together"),
         (r"^(captions|sound)", "Timing narration and subtitles, music and sound effects")]


def _say(sink, engine, line: str):
    line = line.rstrip()
    if not line:
        return
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
    seconds = seconds_of(info, features, extra)
    ranking = ranking_options(extra)
    extra = split_extra(extra)[1]
    job = Path(engine.job_dir(title, style))
    job.mkdir(parents=True, exist_ok=True)
    if hasattr(engine, "_unpublish"):
        engine._unpublish(job, title)                            # a re-render of the same title starts from its own work
    work = job / "short"
    work.mkdir(exist_ok=True)
    (job / "title.txt").write_text(title + "\n", encoding="utf-8")
    (job / "style.txt").write_text(style + "\n", encoding="utf-8")           # the sound check reads the recipe from here
    env ={**os.environ, "CR_STUDIO": str(HOME), "CR_SHORTS_ROOT": str(root), "PYTHONUNBUFFERED": "1"}
    if extra:
        env["CR_SHORTS_EXTRA"] = str(extra)[:4000]
    tone = str((info.get("shorts") or {}).get("tone") or "").strip().lower()
    if tone:
        env["CR_SHORTS_TONE"] = tone                             # dark: true crime / horror (the director's dark-story rules)
    if pipe == "ranking":
        env["CR_RANKING"] = json.dumps(ranking)
    args = [sys.executable, str(root / folder / script), str(HOME), str(work), title]
    args += (["auto", str(seconds)] if pipe == "photo2d" else [str(seconds)])
    _say(sink, engine, f"Short: {info.get('label') or style}, " + (f"{ranking.get('ranks', 5)} ranks" if pipe == "ranking"
                                                                    else f"about {seconds} seconds"))
    proc = subprocess.Popen(args, cwd=str(root / folder), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    tail = []
    for line in proc.stdout:
        tail = (tail + [line])[-40:]
        if re.match(r"^\d\d:\d\d:\d\d ", line) or "done:" in line:   # the pipeline's own progress lines
            _say(sink, engine, line[9:] if re.match(r"^\d\d:\d\d:\d\d ", line) else line)
    if proc.wait() != 0 or not (work / "short.mp4").is_file():
        raise RuntimeError("The Short stopped: " + "".join(tail)[-900:].strip())
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
