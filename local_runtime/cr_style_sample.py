"""Try a style first: a few seconds of it on the creator's own topic, before paying for a study.

The cheapest real way Studio makes seconds in a style: two pictures drawn the way the style's videos draw theirs (its
picture words, its style drawing and character as references, its picture model), one line read by the voice set up
for videos, and a slow Ken Burns move over each picture, rendered on this computer. About $0.05 on the creator's own
accounts (two pictures at about $0.02 and one line of voice); drawn only after the creator approves, and the same
request is never paid twice (Studio's media receipts, and one sample per style, topic and line).

A style whose videos aren't made of pictures (real footage, 3D scenes, animation drawn in code, a Shorts pipeline,
the creator's own panels) can't be shown this way: offer() says what a real sample of it would need, and the frame of
it the page already shows (its latest video, or its reference) stands in. A suggestion only: nothing here stands in the
way of a study.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path

PICTURES = 2
PICTURE_USD = 0.02
VOICE_USD = 0.01                          # one line of about twenty words
COST_USD = round(PICTURES * PICTURE_USD + VOICE_USD, 2)
SECONDS = 5.0
MAX_TOPIC = 200
MAX_LINE = 240
# the words the engine puts before every still drawn against a style drawing (make_video.STYLE_REF_NOTE)
STYLE_REF_NOTE = ("STYLE REFERENCE: the first reference image shows the exact drawing style to copy \u2014 the same kind "
                  "of line, the same hand, the same imperfections, the same background and the same amount of empty "
                  "space. Do NOT copy its subject; draw the subject described below in that style. ")
SHOTS = ("The opening picture of a video about {topic}: the subject shown plainly and completely, a wide view.",
         "The next picture of the same video about {topic}: a closer, telling moment.")
_ID = re.compile(r"sm_[a-f0-9]{24}\Z")
_STYLE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}\Z")
_jobs = set()
_lock = threading.Lock()


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def offer(style: dict) -> dict:
    """Whether a few seconds of this style can be drawn on the creator's topic, its cost, and, when it can't, what
    a real sample of it would need."""
    look = style.get("look") if isinstance(style.get("look"), dict) else {}
    on = (style.get("features") or {}).get("on") if isinstance(style.get("features"), dict) else None
    on = on if isinstance(on, list) else []
    pictures = "ai_images" in on or bool((style.get("pacing") or {}).get("photo_every_s")) or bool(look.get("style_image"))
    why = ""
    if isinstance(style.get("shorts"), dict) or style.get("format") == "short":
        why = ("Its Shorts are made by their own pipeline from a script, so a real sample is a whole Short (Create "
               "makes one).")
    elif look.get("visuals") == "cr_animated2d":
        why = ("Its characters are animated in code: a real sample needs Claude Code to direct a scene to a narrated "
               "line (a few minutes of Claude, drawn on this computer).")
    elif look.get("visuals") or look.get("panels"):
        why = ("Its scenes are drawn by its own graphics kit or from your own panels, so a real sample is a scene of "
               "a video made with it.")
    elif "threed" in on and "ai_images" not in on:
        why = ("Its scenes are rendered in 3D on this computer: a real sample needs a 3D scene directed and rendered "
               "for your topic (a few minutes of Claude and this computer, no picture costs).")
    elif not pictures:
        why = ("Its videos are cut from real footage found for each script, so a real sample needs a whole video's "
               "footage search.")
    return {"can": not why, "pictures": PICTURES, "seconds": SECONDS, "cost_usd": COST_USD if not why else 0.0,
            "why_not": why}


def sample_id(style: str, topic: str, line: str) -> str:
    return "sm_" + hashlib.sha256(json.dumps([style, topic, line]).encode("utf-8")).hexdigest()[:24]


def folder(home, ident: str) -> Path:
    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise ValueError("Choose a saved style sample.")
    root = (Path(home) / "output" / "cr-style-samples").resolve()
    path = root / ident
    if not path.resolve().is_relative_to(root):
        raise RuntimeError("Keep style samples in this studio's output folder.")
    return path


def status(home, ident: str) -> dict:
    path = folder(home, ident)
    saved = _read(path / "sample.json")
    if not saved:
        raise RuntimeError("That sample wasn't found. Ask for it again.")
    with _lock:
        running = ident in _jobs
    state = saved.get("state")
    if state == "running" and not running:
        state = "failed"                                 # Studio closed while drawing: what was paid stays saved
        saved.setdefault("message", "The sample stopped when Studio closed. Ask for it again: nothing paid is paid twice.")
    video = path / "sample.mp4"
    return {"sample_id": ident, "state": state, "style": saved.get("style"), "topic": saved.get("topic"),
            "line": saved.get("line"), "message": saved.get("message", ""), "cost_usd": saved.get("cost_usd"),
            "credits": saved.get("credits"), "voiced": saved.get("voiced"),
            "video": str(video) if state == "done" and video.is_file() else "",
            "video_url": f"/cr-style-clone/sample/{ident}/video.mp4" if state == "done" and video.is_file() else ""}


def _first_character(look: dict) -> str:
    cast = re.sub(r"^THE CAST\s*[—-]\s*", "", str(look.get("character") or "").strip())
    return re.split(r"\n|(?<=\.)\s+(?=[A-Z][\w' -]{1,40}(?: \([^)]*\))?:)", cast)[0].strip()[:600] if cast else ""


def prompts(style: dict, topic: str, *, referenced: bool) -> list:
    """The two pictures' words: the shot, the style's recurring character and its own picture words (still_suffix),
    as its videos ask for theirs."""
    look = style.get("look") if isinstance(style.get("look"), dict) else {}
    cast = _first_character(look)
    suffix = " " + str(look.get("still_suffix") or "").strip()
    return [((STYLE_REF_NOTE if referenced else "") + shot.format(topic=topic)
             + (" The main character, who appears in every picture: " + cast if cast else "") + suffix).strip()
            for shot in SHOTS]


def _duration(path) -> float:
    try:
        done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                               "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True,
                              timeout=30, encoding="utf-8", errors="replace")
        return float(done.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def render(stills: list, voice, out: Path, *, run=subprocess.run) -> Path:
    """The pictures with a slow Ken Burns move each (in, then out), crossfaded, under the voice: 1280x720 H.264."""
    length = max(SECONDS, (_duration(voice) + 0.6) if voice else 0.0)
    each = length / len(stills) + 0.5
    frames = int(round(each * 30))
    command = ["ffmpeg", "-v", "error", "-y"]
    for still in stills:
        command += ["-loop", "1", "-framerate", "30", "-t", f"{each:.2f}", "-i", str(still)]   # a frame per frame out
    if voice:
        command += ["-i", str(voice)]
    chains = []
    for k in range(len(stills)):
        zoom = f"1+0.08*on/{frames}" if k % 2 == 0 else f"1.08-0.08*on/{frames}"
        chains.append(f"[{k}:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                      f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1280x720:fps=30,"
                      f"setsar=1[s{k}]")
    last, offset = "s0", each - 0.5
    for k in range(1, len(stills)):
        chains.append(f"[{last}][s{k}]xfade=transition=fade:duration=0.5:offset={offset:.2f}[x{k}]")
        last, offset = f"x{k}", offset + each - 0.5
    chains.append(f"[{last}]format=yuv420p[v]")
    command += ["-filter_complex", ";".join(chains), "-map", "[v]"]
    if voice:
        command += ["-map", f"{len(stills)}:a", "-c:a", "aac", "-b:a", "160k"]
    command += ["-t", f"{length:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-movflags", "+faststart", str(out)]
    done = run(command, capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
    if getattr(done, "returncode", 1) != 0 or not Path(out).is_file():
        raise RuntimeError("the pictures couldn't be put together into a video on this computer ("
                           + ((getattr(done, "stderr", "") or "").strip().splitlines() or ["FFmpeg failed"])[-1][:160]
                           + ")")
    return Path(out)


def _finish(still: Path, look: dict):
    """A style whose photos look like phone frames (look.stills.realism) gets that finish, as its videos' stills do."""
    preset = str(((look.get("stills") or {}) if isinstance(look.get("stills"), dict) else {}).get("realism") or "")
    if preset and still.is_file():
        try:
            import realism                                     # the engine's own finish (installed with Studio)
            realism.degrade(still, preset)
        except Exception:                                      # noqa: BLE001 - the picture as drawn still shows the style
            pass


def _credits(path: Path):
    """What the paid tasks of a sample charged, as Kie reported it (credits), from their receipts."""
    total, known = 0.0, False
    for receipt in sorted((path / ".cr-tasks").glob("*.json")):
        credits = _read(receipt).get("credits")
        if isinstance(credits, (int, float)) and not isinstance(credits, bool):
            total, known = total + float(credits), True
    return round(total, 2) if known else None


def start(home, style_name: str, style: dict, topic: str, line: str = "", *, generate, host, voice=None,
          make=render, thread=True) -> dict:
    """Draw the sample (the caller has the creator's approval of its cost). `generate(prompt, dest, label,
    ref_urls=[...])` draws one picture, `host(path)` puts a reference online, `voice(text, dest)` reads the line and
    returns the audio file (None when no voice is set up). A finished or running sample is returned as it is."""
    if not isinstance(style_name, str) or not _STYLE.fullmatch(style_name) or not isinstance(style, dict):
        raise ValueError("Choose an installed style.")
    can = offer(style)
    if not can["can"]:
        raise ValueError(can["why_not"])
    topic = " ".join(str(topic or "").split())[:MAX_TOPIC]
    line = " ".join(str(line or "").split())[:MAX_LINE] or topic
    if len(topic) < 3:
        raise ValueError("Say what your video is about, in a few words.")
    ident = sample_id(style_name, topic, line)
    path = folder(home, ident)
    saved = _read(path / "sample.json")
    with _lock:
        made = ident in _jobs or (saved.get("state") == "done" and (path / "sample.mp4").is_file())
        if not made:
            _jobs.add(ident)
    if made:
        return status(home, ident)
    _write(path / "sample.json", {"state": "running", "style": style_name, "topic": topic, "line": line,
                                  "cost_usd": COST_USD, "message": "Drawing two pictures in this style.",
                                  "started_at": time.time()})

    def work():
        try:
            import cr_character
            look = style.get("look") if isinstance(style.get("look"), dict) else {}
            refs = [host(p) for p in (cr_character._asset(home, look.get(k)) for k in ("style_image", "character_image"))
                    if p is not None]
            stills = []
            referenced = cr_character._asset(home, look.get("style_image")) is not None and bool(refs)
            for k, words in enumerate(prompts(style, topic, referenced=referenced)):
                still = path / f"still_{k}.jpg"
                if not still.is_file():
                    generate(words, still, f"style sample {k + 1}", ref_urls=refs)
                    _finish(still, look)
                stills.append(still)
            _write(path / "sample.json", {**_read(path / "sample.json"), "message": "Reading the line and moving the pictures."})
            audio = voice(line, path / "voice") if voice else None
            make(stills, audio, path / "sample.mp4")
            _write(path / "sample.json", {**_read(path / "sample.json"), "state": "done", "voiced": bool(audio),
                                          "credits": _credits(path), "finished_at": time.time(),
                                          "message": "Here are a few seconds in this style on your topic."
                                          + ("" if audio else " (No voice: set up a Kie voice in Setup & accounts to hear it.)")})
        except BaseException as error:                      # noqa: BLE001 - said to the creator, never retried alone
            _write(path / "sample.json", {**_read(path / "sample.json"), "state": "failed", "credits": _credits(path),
                                          "message": "The sample stopped: " + str(error)[:200]
                                          + ". What was already paid for is kept; asking again reuses it."})
        finally:
            with _lock:
                _jobs.discard(ident)
    if thread:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()
    return status(home, ident)


def kie_voice(text, dest) -> Path | None:
    """The line read by the narration voice set up for videos (Kie or Atlas), or None when none is set up there."""
    import cr_media
    if not cr_media.active("narration"):
        return None
    cfg = cr_media.profile("narration")
    model = cr_media.narration_model(cfg["provider"], cfg.get("model"))
    voice = cfg.get("voice") or model["default_voice"]
    out = Path(dest).with_suffix("." + model["format"])
    cr_media.run("narration", cr_media.narration_input(text, voice, model["id"]), out, model=model["id"])
    return out
