"""Look at reference videos before a style study, and propose a recipe.

The first step of style learning, and the only one before the creator decides:
download a few minutes of each approved reference, measure them on this
computer (cuts, motion, light, sound), have Gemini watch them through
ChannelRecipe, build one Style Profile and map it to Studio settings. The
creator sees what Studio saw, with every setting explained, before any Claude
work or paid picture starts.

No Claude, no Kie: the measuring is free and ChannelRecipe pays for the
watching within the plan's daily allowance. Runs as its own process
(`python cr_style_look.py --look lk_...`) so a long measurement never holds
up the studio page. A finished look is reused for the same references.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HOME = Path(__file__).resolve().parent
SCHEMA = 1
PHASES = {
    "queued": (0, "Waiting to start."),
    "download": (10, "Downloading a few minutes of each reference."),
    "measure": (35, "Measuring cuts, motion, light and sound."),
    "watch": (60, "Gemini is watching the references."),
    "profile": (88, "Putting together what makes the channel recognisable."),
    "done": (100, "Here's what Studio saw."),
    "failed": (100, "Studio couldn't look at these references."),
}
_ID = re.compile(r"lk_[a-f0-9]{24}\Z")


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def folder(home, look_id: str) -> Path:
    if not isinstance(look_id, str) or not _ID.fullmatch(look_id):
        raise ValueError("Choose a saved reference look.")
    import cr_style_clone
    root = cr_style_clone._folder(home) / "looks"
    path = root / look_id
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Keep reference looks in this studio's output folder.")
    return path


def _references(home, preview_id, reference_ids) -> list:
    """The approved references from a saved preview (the same checks as a study)."""
    import cr_style_clone
    if not isinstance(preview_id, str) or not re.fullmatch(r"pv_[a-f0-9]{64}", preview_id):
        raise ValueError("Preview your YouTube references first.")
    saved = cr_style_clone._read(cr_style_clone._folder(home) / ".previews" / (preview_id + ".json"))
    if not saved or not 0 <= time.time() - saved.get("created_at", 0) <= 86400:
        raise ValueError("Preview the references again; that preview is more than a day old.")
    if (not isinstance(reference_ids, list) or not 1 <= len(reference_ids) <= cr_style_clone.MAX_REFERENCES
            or len(set(reference_ids)) != len(reference_ids) or any(not isinstance(x, str) for x in reference_ids)):
        raise ValueError("Choose one to three different reference videos.")
    rows = {row["id"]: row for row in saved["candidates"]}
    if any(x not in rows or not rows[x].get("eligible") for x in reference_ids):
        raise ValueError("Choose videos from your preview that can be studied.")
    return [rows[x] for x in reference_ids]


def start(home, preview_id, reference_ids, *, spawn=None) -> dict:
    """Start looking (or return the look already made for these references)."""
    refs = _references(home, preview_id, reference_ids)
    look_id = "lk_" + hashlib.sha256(json.dumps(sorted(r["id"] for r in refs)).encode()).hexdigest()[:24]
    path = folder(home, look_id)
    saved = _read(path / "look.json")
    fresh = saved.get("state") == "done" and time.time() - saved.get("finished_at", 0) < 30 * 86400 \
        and not _unjudged_3d(saved.get("profile") or {})
    running = saved.get("state") == "running" and time.time() - saved.get("updated_at", 0) < 900
    if not (fresh or running):
        _write(path / "look.json", {"schema": SCHEMA, "look_id": look_id, "state": "running", "phase": "queued",
                                    "references": refs, "started_at": time.time(), "updated_at": time.time()})
        (spawn or _spawn)(home, look_id)
    return status(home, look_id)


def _unjudged_3d(profile: dict) -> bool:
    """A 3D look saved before Gemini judged its people's faces (Studio 72 and older): watched again, so a designed cast
    (@ifologyfilms' dot-eyed sphere heads) never goes back onto faceless mannequins from an old reading of its words."""
    pictures = profile.get("pictures") or {}
    families = ((pictures.get("sources") or {}).get("families") or {})
    judged = ((pictures.get("people_3d") or {}).get("value") or {})
    return float(families.get("3d") or 0) >= 0.2 and not judged


def _styles(home) -> dict:
    return {Path(p).stem: _read(p) for p in glob.glob(str(Path(home) / "styles" / "*.json"))
            if not Path(p).stem.startswith(("_", "cr_clone_"))}


def _spawn(home, look_id):
    log = folder(home, look_id) / "look.log"
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    handle = os.fdopen(descriptor, "w", encoding="utf-8")
    subprocess.Popen([sys.executable, str(Path(home) / "cr_style_look.py"), "--look", look_id], cwd=str(home),
                     stdout=handle, stderr=subprocess.STDOUT)


def status(home, look_id) -> dict:
    saved = _read(folder(home, look_id) / "look.json")
    if not saved:
        raise RuntimeError("That reference look wasn't found. Look at the references again.")
    phase = saved.get("phase", "queued")
    percent, message = PHASES.get(phase, PHASES["queued"])
    if saved.get("state") == "running" and time.time() - saved.get("updated_at", 0) > 900:
        saved.update(state="failed", phase="failed", error="Looking at the references stopped. Try again.")
    out = {"look_id": look_id, "state": saved.get("state"), "phase": phase, "percent": percent,
           "message": saved.get("error") or saved.get("message") or message,
           "references": [{"id": r["id"], "title": r.get("title", ""), "channel": r.get("channel", "")}
                          for r in saved.get("references", [])]}
    if saved.get("state") == "done":
        mapping = saved.get("mapping") or {}
        import cr_style_map
        if mapping.get("router") != cr_style_map.ROUTER:
            try:                               # planned again by this Studio, as the study will plan it
                mapping = cr_style_map.plan_style(saved.get("profile") or {}, _styles(home))
            except Exception:                  # noqa: BLE001 - a recipe since removed: the saved plan is shown
                pass
        out.update(profile=saved.get("profile"), mapping=mapping, sheets=saved.get("sheets", []),
                   not_watched=saved.get("not_watched", []), not_measured=saved.get("not_measured", []),
                   shorts=bool(saved.get("references")) and all(r.get("short") for r in saved["references"]))
        try:
            out["similar"] = similar(home, look_id, saved.get("profile") or {}, base=mapping.get("base") or "",
                                     shorts=out["shorts"])
        except Exception:                      # noqa: BLE001 - a suggestion never stands in the way of the look
            out["similar"] = []
    return out


def similar(home, look_id, profile, *, base="", shorts=False) -> list:
    """The 2-3 installed styles closest to what Studio saw (cr_style_map.similar), each with its name, a picture of it,
    a link to use it and whether a few seconds of it can be drawn on the creator's topic first (cr_style_sample).
    Suggestions only: the study is offered the same either way."""
    import cr_presentation
    import cr_style_map
    import cr_style_sample
    from urllib.parse import quote
    styles, profiles = {}, {}
    for path in sorted(glob.glob(str(Path(home) / "styles" / "*.json"))):
        style = _read(path)
        if style:
            styles[Path(path).stem] = style
            seen = learned_profile(home, style)
            if seen:
                profiles[Path(path).stem] = seen
    out = []
    for row in cr_style_map.similar(profile, styles, profiles=profiles, shorts=shorts, base=base, look_id=look_id):
        style = styles[row["id"]]
        named = cr_presentation.RECIPES.get(row["id"])
        out.append({**row, "name": named[0] if named else str(style.get("label") or row["id"]),
                    "description": str(named[2] if named else style.get("tagline") or style.get("description") or "")[:240],
                    "preview_url": "/cr-workspace/preview/recipe/" + quote(row["id"], safe="") + ".jpg",
                    "use_url": "/?cr-preset=" + quote(row["id"], safe=""),
                    "sample": cr_style_sample.offer(style, name=row["id"], profile=profiles.get(row["id"]))})
    return out


def learned_profile(home, style) -> dict:
    """What Studio saw of the channel a learned style was studied from (its look's profile), {} for any other style."""
    learned = (((style or {}).get("cr_style_clone") or {}).get("learned_v2") or {}).get("look_id")
    if not learned or not _ID.fullmatch(str(learned)):
        return {}
    return _read(folder(home, learned) / "look.json").get("profile") or {}


# ── the work ───────────────────────────────────────────────────────────────────────────────────────────────

BOT_CHECK = re.compile(r"not a bot|sign in to confirm|429|too many requests", re.I)


def _lost_note(ref, lost, total) -> str:
    """What the creator reads when parts of a reference couldn't be downloaded: its cuts, motion and sound then come
    from the other parts and references only (Gemini's watching runs on ChannelRecipe's server and is unaffected)."""
    name = ref.get("title") or ref["id"]
    why = ("YouTube asked this computer to sign in; it usually lets downloads through again within an hour"
           if any(BOT_CHECK.search(e) for _, _, e in lost) else "the download failed")
    part = "none of it" if len(lost) >= total else f"{len(lost)} of its {total} parts"
    return f"{name}: {part} could be measured ({why})"


def _download(video_id, a, b, dest: Path, timeout=420) -> Path:
    import cr_style_clone
    command = cr_style_clone._ytdlp_command()
    if not command:
        raise RuntimeError("Studio's YouTube helper is missing.")
    out = dest.with_suffix("")
    done = subprocess.run(command + ["-q", "--no-warnings", "--no-playlist", "-f",
                                     "bv*[height<=480][height>=360]+ba/b[height<=480]/bv*+ba/b",
                                     "--merge-output-format", "mp4", "--download-sections", f"*{a}-{b}",
                                     "--force-keyframes-at-cuts", "-o", f"{out}.%(ext)s",
                                     "https://www.youtube.com/watch?v=" + video_id],
                          capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    got = sorted((Path(p) for p in glob.glob(glob.escape(str(out)) + ".*") if p.endswith((".mp4", ".mkv", ".webm"))),
                 key=lambda p: -p.stat().st_size)
    if not got or got[0].stat().st_size < 50_000:
        raise RuntimeError((done.stderr or "no file").strip().splitlines()[-1][:200] if done.stderr else "no file")
    return got[0]


def _stills(sections, measured, folder: Path, video_id: str, per_video: int = 4):
    """A few sharp frames of the reference (960 px), from the middle of long shots, for the style samples."""
    picks = []
    for (a, file), m in zip(sections, measured):
        shots = sorted((s for s in m.get("shots") or [] if s["t1"] - s["t0"] >= 1.5),
                       key=lambda s: -(s["t1"] - s["t0"]))
        picks += [(file, (s["t0"] + s["t1"]) / 2 - a) for s in shots[:per_video]]
    folder.mkdir(parents=True, exist_ok=True)
    step = max(1, len(picks) // per_video)
    for i, (file, t) in enumerate(picks[::step][:per_video]):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(file), "-frames:v", "1",
                        "-vf", "scale=960:-2", "-q:v", "3", str(folder / f"{video_id}_{i}.jpg")],
                       capture_output=True, timeout=60)


def _vertical(folder: Path):
    """The share of the look's frames that are portrait (a Shorts channel: 1.0), or None without frames."""
    from PIL import Image
    sizes = []
    for f in sorted(Path(folder).glob("*.jpg")):
        try:
            with Image.open(f) as im:
                sizes.append(im.size)
        except OSError:
            continue
    return round(sum(1 for w, h in sizes if h > w * 1.1) / len(sizes), 2) if sizes else None


def _transcript(video_id, work):
    try:
        import youtube
        return youtube.transcript(video_id, work)
    except Exception:                                   # noqa: BLE001 - captions are a help, not a requirement
        return []


def run(home, look_id, *, call=None, download=_download, transcript=_transcript, measure=None, now=time.time):
    """Do the looking. Every step that can fail says so in the look and the rest carries on."""
    import cr_style_map
    import cr_style_measure
    import cr_style_profile
    import cr_style_watch
    if call is None:
        import cr_bridge
        call = cr_bridge.call
    measure = measure or cr_style_measure.measure
    path = folder(home, look_id)
    saved = _read(path / "look.json")
    log_lines = []

    def progress(phase, message=""):
        saved.update(phase=phase, message=message, updated_at=now())
        _write(path / "look.json", saved)

    def log(text):
        log_lines.append(str(text))
        print(text, flush=True)

    refs = saved["references"]
    work = path / "work"
    built, unmeasured = [], []
    watched = {}

    def watch_one(ref, captions):
        watched[ref["id"]] = cr_style_watch.watch(ref["id"], ref.get("duration_seconds") or ref.get("duration") or 0,
                                                 call=call, log=log, transcript=captions)
    threads = []
    try:
        for ref in refs:
            vid, duration = ref["id"], float(ref.get("duration_seconds") or ref.get("duration") or 0)
            progress("download", f"Reading the captions of {ref.get('title') or vid}.")
            captions = transcript(vid, work)
            thread = threading.Thread(target=watch_one, args=(ref, captions), daemon=True)
            thread.start()
            threads.append(thread)
            spans = [w for w in cr_style_watch.windows(duration)]           # what Gemini watches, minus promotions
            sections, lost = [], []
            for a, b in spans:
                progress("download", f"Downloading {_mmss(a)}-{_mmss(b)} of {ref.get('title') or vid}.")
                for attempt in (1, 2):
                    try:
                        sections.append((a, download(vid, a, b, work / "media" / f"{vid}_{a}_{b}.mp4")))
                        break
                    except Exception as exc:                           # noqa: BLE001
                        log(f"  download {vid} {_mmss(a)}-{_mmss(b)}: {exc}")
                        if attempt == 2 or BOT_CHECK.search(str(exc)):
                            lost.append((a, b, str(exc)))
                            break
            if lost:
                unmeasured.append(_lost_note(ref, lost, len(spans)))
            progress("measure", f"Measuring {ref.get('title') or vid}.")
            measured = [measure(file, start=a, keyframes=True) for a, file in sections]
            _stills(sections, measured, path / "frames", vid)
            frames = [f for m in measured for f in m.pop("_keyframes", [])]
            sheet = None
            if frames:
                step = max(1, len(frames) // 24)
                sheet = cr_style_measure.keyframe_sheet(frames[::step][:24], path / "sheets" / f"{vid}.jpg")
            words = sum(len(str(t).split()) for _, t in captions) if captions else 0
            # words per minute of VIDEO (pauses included), the way a script's length is planned
            speech = sum(float(m.get("seconds") or 0) for m in measured)
            spoken = [(t, w) for t, w in captions if any(a <= t < a + float(m.get("seconds") or 0)
                                                         for a, m in zip([s[0] for s in sections], measured))]
            built.append({"id": vid, "title": ref.get("title", ""), "channel": ref.get("channel", ""),
                          "duration": duration, "measures": cr_style_measure.combine(measured),
                          "sheet": str(sheet) if sheet else "",
                          "transcript": {"words": sum(len(str(w).split()) for _, w in spoken), "seconds": speech}
                          if spoken and speech else {"words": 0, "seconds": 0},
                          "caption_lines": len(captions), "caption_words": words,
                          "excerpt": " ".join(str(w) for t, w in captions if t < 150)[:1500]})
        progress("watch", "Gemini is watching the references.")
        for thread in threads:
            thread.join(timeout=cr_style_watch.WAIT_S + 120)
        for ref in built:
            ref["watch"] = watched.get(ref["id"]) or {"windows": [], "missing": ["not watched"]}
        progress("profile")
        profile = cr_style_profile.build(built)
        tall = _vertical(path / "frames")
        if tall is not None:                                   # a Shorts channel's videos are made vertical too
            profile.setdefault("format", {})["vertical"] = {"value": tall, "confidence": 0.95, "from": ["measured"]}
        mapping = cr_style_map.plan_style(profile, _styles(home))
        _write(path / "references.json", {"references": built})
        saved.update(state="done", phase="done", profile=profile, mapping=mapping, finished_at=now(),
                     sheets=[{"id": r["id"], "file": Path(r["sheet"]).name} for r in built if r.get("sheet")],
                     not_watched=[m for r in built for m in (r["watch"].get("missing") or [])],
                     not_measured=unmeasured,
                     excerpts={r["id"]: r["excerpt"] for r in built}, message="")
        progress("done")
    except Exception as exc:                                           # noqa: BLE001
        traceback.print_exc()
        saved.update(state="failed", phase="failed", error="Studio couldn't look at these references ("
                     + str(exc)[:200] + "). Nothing was charged; try again or choose other videos.")
        _write(path / "look.json", saved)
    return status(home, look_id)


def _mmss(t) -> str:
    t = int(round(float(t)))
    return f"{t // 60}:{t % 60:02d}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Look at approved reference videos before a style study.")
    parser.add_argument("--look", required=True)
    args = parser.parse_args(argv)
    import cr_bridge
    run(HOME, args.look, call=cr_bridge.call)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
