"""A finished video whose picture freezes while the voice goes on is never handed over as finished.

The engine's final pass (make_video._final_mux) muxes again, up to three times, when the picture stops moving
(_picture_stalls): it takes the freeze for a read of the joined picture that timed out halfway. A customer's
10-minute video froze from 5:15 to its end on every try (2026-10-25), and after the third the frozen mux was kept
and published: the fault was in the picture under it, which muxing again can't change.

Here every freeze the check finds is laid over the segments the picture was joined from. A freeze inside one shot
for less than HELD_MAX_S is that shot holding still (a still picture, a short clip holding its last frame): it is
kept, without the engine's two pointless re-muxes. A freeze that runs across a cut (the picture should have changed
there), holds one shot longer than HELD_MAX_S, or a picture that ends before the voice, is broken: the segments
under it are deleted and rendered again from the pictures and clips already on disk, the film is joined again and
muxed again; the second time the join is re-encoded whole. If the third mux still freezes, the video stops with a
plain message naming the part, and the frozen file is set aside (video_frozen.mp4), so Resume assembles it again
instead of finding it "cached". Nothing paid is made again: only local segment renders are repeated.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

HOLD_S = 12.0              # the engine's own check: nothing in a finished video legitimately stands still this long...
HELD_MAX_S = 30.0          # ...but one shot may hold its picture up to this long (a still, a clip's last frame)
CROSS_S = 1.0              # a freeze that runs this far past a cut crosses it
SHORT_S = 2.0              # a picture this much shorter than the voice ends early
REPAIRS = ("render", "reencode")   # what is tried after the first and the second frozen mux
REPORT = "cr-freeze.json"
FROZEN = "video_frozen.mp4"
_SEGMENT = re.compile(r"seg_(\d{3})(?:_cin)?\.mp4\Z")
_lock = threading.RLock()
_jobs: dict = {}


def _key(job) -> str:
    return os.path.normcase(os.path.abspath(str(job)))


def _stamp(path):
    try:
        st = Path(path).stat()
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def _state(job) -> dict:
    with _lock:
        return _jobs.setdefault(_key(job), {})


def clock(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def freezes(path, hold_s: float = HOLD_S) -> tuple:
    """([(start, end)], picture seconds) of a video: every stretch the picture stands still for hold_s or longer, by
    the engine's own measure (frames shrunk and blurred so tape noise and a small label do not count as movement),
    in the same single decoding pass. A freeze still running at the end has no end of its own: it ends with the
    picture. (None, 0.0) when the video could not be read."""
    try:
        p = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf",
                            f"scale=64:-2,boxblur=2,freezedetect=n=0.01:d={hold_s:.1f}", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    except (subprocess.TimeoutExpired, OSError):
        return None, 0.0
    err = p.stderr or ""
    clocks = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    length = (int(clocks[-1][0]) * 3600 + int(clocks[-1][1]) * 60 + float(clocks[-1][2])) if clocks else 0.0
    starts = [float(x) for x in re.findall(r"freeze_start: ([0-9.]+)", err)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([0-9.]+)", err)]
    spans = [(s, ends[i] if i < len(ends) else max(length, s)) for i, s in enumerate(starts)]
    return spans, length


def durations(paths: list) -> list:
    """Each file's length in seconds (0.0 when unreadable), probed side by side."""
    def one(p):
        for attempt in range(3):
            try:
                r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(p)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=60)
                return float((r.stdout or "").strip().splitlines()[0])
            except (ValueError, IndexError, OSError, subprocess.TimeoutExpired):
                time.sleep(1 + attempt)
        return 0.0
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(one, paths))


def bounds_of(lengths: list) -> list:
    """[(start, end)] of each joined file on the film's clock."""
    out, t = [], 0.0
    for d in lengths:
        out.append((t, t + max(0.0, float(d or 0))))
        t += max(0.0, float(d or 0))
    return out


def classify(spans: list, bounds: list, length: float, voice: float = 0.0, planned: list = None,
             suspects=()) -> list:
    """[{"start", "end", "parts", "broken", "why"}] for the freezes of a finished mux. bounds: where the joined files
    really sit; planned: where the plan put them (a cut belongs at each); suspects: files whose length is not their
    slot's. parts: the joined files under the freeze (and the suspects, when it is broken). Broken: it runs across a
    cut (the picture should have changed there), holds one shot longer than HELD_MAX_S, or the picture ends before
    the voice. Without the joined files (a pipeline that renders its picture in one piece) only a freeze of more than
    a minute to the end, or a picture shorter than the voice, is broken."""
    cut_times = sorted({a for a, _ in bounds[1:]} | {a for a, _ in (planned or [])[1:]})
    rows = []
    for s, e in spans:
        parts = [i for i, (a, b) in enumerate(bounds) if a < e - 0.05 and b > s + 0.05]
        if any(s + CROSS_S < c < e - CROSS_S for c in cut_times):
            why = "cut"
        elif bounds and e - s > HELD_MAX_S:
            why = "held"
        elif not bounds and e >= length - 1.0 and e - s > 60.0:
            why = "end"
        else:
            why = ""
        rows.append({"start": s, "end": e, "parts": sorted(set(parts) | set(suspects)) if why else parts,
                     "broken": bool(why), "why": why})
    if voice and length and length < voice - SHORT_S:
        parts = [i for i, (a, b) in enumerate(bounds) if b > length - 0.05]
        rows.append({"start": length, "end": voice, "parts": sorted(set(parts) | set(suspects)), "broken": True,
                     "why": "short"})
    return rows


def planned_lengths(engine, st: dict) -> list:
    """Each joined file's slot in the plan it was rendered from, or None when that plan is not this join's."""
    render, segs = st.get("render") or {}, st.get("segs") or []
    plan = render.get("plan") or []
    if not plan or len(plan) != len(segs):
        return None
    try:
        if all(len(e) >= 4 for e in plan):
            return [float(e[3]) for e in plan]
        slot = render["args"][0] if render.get("args") else render.get("kwargs", {}).get(
            "slot_dur", getattr(engine, "SECONDS_PER_SLOT", 15.0))
        return [float(slot)] * len(plan)
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def _probe_streams(paths: list) -> list:
    def one(p):
        try:
            return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=codec_name,width,height,pix_fmt,r_frame_rate,time_base", "-of", "csv=p=0",
                                   str(p)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=60).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(one, paths))


def mixed(paths: list) -> bool:
    """Whether the files differ in picture size, frame rate, codec or time base: a join that copies their streams
    then needs every decoder to cope with the change mid-stream, and a re-encoded join does not."""
    return len({s for s in _probe_streams(paths) if s}) > 1


@contextmanager
def local_only(engine):
    """A segment rendered again uses only what is on disk. The steps before the engine's render loop that can pay for
    something are skipped for it: animate.ensure (stills set in motion on a video model; a still already in motion
    keeps its saved clip), photofx's depth lift (subjects cut out; the still gets the plain move) and the kits'
    transition redraws."""
    saved = []

    def swap(owner, name, value):
        if owner is not None and hasattr(owner, name):
            saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
    swap(engine, "_depth_dlc", lambda *a, **k: (None, set()))
    here = Path(getattr(engine, "HERE", "") or ".")
    for module, name in (("animate", "ensure"), ("kits", "fix_transitions")):
        mod = sys.modules.get(module)
        if mod is None and (here / f"{module}.py").is_file():
            try:
                mod = importlib.import_module(module)
            except Exception:                                  # noqa: BLE001 - not importable: the engine can't call it
                mod = None
        swap(mod, name, (lambda *a, **k: 0))
    try:
        yield
    finally:
        for owner, name, value in reversed(saved):
            setattr(owner, name, value)


def _where(st: dict, i: int) -> str:
    """What part i of the film shows, in words: its picture, clip or scene from the plan, else its file."""
    plan, segs = st.get("plan") or [], st.get("segs") or []
    try:
        kind, path = plan[i][0], Path(str(plan[i][1])).name
    except (IndexError, TypeError):
        return Path(str(segs[i])).name if i < len(segs) else ""
    words = {"video": "footage clip", "photo": "picture", "motion": "scene"}.get(str(kind), "part")
    return f"{words} {path}"


def _log(engine, msg: str) -> None:
    try:
        getattr(engine, "log", print)(msg)
    except Exception:                                          # noqa: BLE001 - a log line only
        pass


def _rerender(engine, job: Path, st: dict, parts: list) -> list:
    """The joined list with `parts` rendered again by the engine's own renderer, from what is on disk: their segment
    files go and the renderer makes only the missing ones. A part that is not one of this job's segments (an xmind
    page, a vox page) is left as it is. Returns the indices made again."""
    render = st.get("render")
    segs = list(st.get("segs") or [])
    if not render or len(segs) != len(render["plan"]):
        return []
    seg_dir = (job / "segments").resolve()
    again = []
    for i in parts:
        p = Path(str(segs[i]))
        m = _SEGMENT.match(p.name)
        if not m or int(m.group(1)) != i or p.parent.resolve() != seg_dir:
            continue
        for name in (f"seg_{i:03d}.mp4", f"seg_{i:03d}_cin.mp4"):
            (seg_dir / name).unlink(missing_ok=True)
        again.append(i)
    if not again:
        return []
    with local_only(engine):
        fresh = render["fn"](render["plan"], job, False, *render["args"], **render["kwargs"])
    for i in again:
        # the plain segment, without a cinema treatment over it: the most basic render of that slot
        segs[i] = fresh[i]
    st["segs"] = segs
    return again


def reencode_join(engine, seg_paths: list, out: Path) -> None:
    """The segments joined into one freshly encoded picture (sizes, frame rates and time stamps made one), written
    beside `out` and swapped in only when it is as long as the segments."""
    fps = int(getattr(engine, "FPS", 30) or 30)
    w, h = int(getattr(engine, "VIDEO_W", 1920) or 1920), int(getattr(engine, "VIDEO_H", 1080) or 1080)
    quote = getattr(engine, "_concat_quote", lambda p: str(p).replace("'", "'\\''"))
    lst = out.parent / "_seglist_fix.txt"
    lst.write_text("".join(f"file '{quote(p)}'\n" for p in seg_paths), encoding="utf-8")
    tmp = out.with_name(out.stem + "_fix" + out.suffix)
    want = sum(durations(seg_paths))
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-an",
                        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                               f"setsar=1,fps={fps},format=yuv420p",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                        "-color_trc", "bt709", "-r", str(fps), str(tmp)],
                       check=True, capture_output=True, timeout=max(1800, int(want * 4)))
        got = (durations([tmp]) or [0.0])[0]
        if want and got < want - 1.0:
            raise RuntimeError(f"the joined picture came out {got:.0f}s long instead of {want:.0f}s")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
        lst.unlink(missing_ok=True)


def repair(engine, job: Path, st: dict, rows: list, step: str) -> tuple:
    """One repair of the joined picture under the broken freezes: (the step it took, what was done in words)."""
    out = Path(st.get("visual") or job / "_visual.mp4")
    parts = sorted({i for r in rows if r["broken"] for i in r["parts"]})
    if step == "render":
        again = _rerender(engine, job, st, parts)
        if again:
            segs = st["segs"]
            if mixed(segs):
                reencode_join(engine, segs, out)
                return step, f"{len(again)} part(s) made again and the film joined again (re-encoded: its parts differ)"
            fix = out.with_name(out.stem + "_fix" + out.suffix)
            st["original_concat"](segs, fix)
            fix.replace(out)
            return step, f"{len(again)} part(s) made again and the film joined again"
    # nothing here could be rendered again, or that was tried: the join itself, re-encoded whole
    reencode_join(engine, st["segs"], out)
    return "reencode", "the film joined again, re-encoded whole"


def describe(rows: list, st: dict) -> str:
    """The broken freeze in plain words: when it starts and which part of the film it starts in."""
    row = next(r for r in rows if r["broken"])
    segs, start = st.get("segs") or [], row["start"]
    where = ""
    if row["parts"]:
        # a part longer or shorter than its slot is what moved everything after it: it is the one to name
        i = next((k for k in row["parts"] if k in (st.get("suspects") or ())), row["parts"][0])
        b = st.get("bounds") or []
        name = _where(st, i)
        span = f", {clock(b[i][0])} to {clock(b[i][1])}" if i < len(b) else ""
        where = f" It starts in part {i + 1} of {len(segs)}{span}" + (f" ({name})" if name else "") + "."
    what = (f"The picture ends at {clock(start)} while the voice goes on to {clock(row['end'])}." if row["why"] == "short"
            else f"The picture froze at {clock(start)} and stayed still for {row['end'] - start:.0f} seconds while "
                 f"the voice went on.")
    return what + where


def install(engine):
    """Trace a frozen final mux to its segments, repair them, and never let a frozen video through as finished."""
    if getattr(engine, "_cr_freeze", False) or not hasattr(engine, "_final_mux"):
        return
    engine._cr_freeze = True

    for name in ("_render_segments", "_render_astro_segments"):
        original = getattr(engine, name, None)
        if not callable(original):
            continue

        def rendered(plan, job, force, *args, _original=original, **kwargs):
            segs = _original(plan, job, force, *args, **kwargs)
            st = _state(job)
            st["render"] = {"fn": _original, "plan": list(plan), "args": args, "kwargs": kwargs}
            st["plan"] = list(plan)
            return segs
        setattr(engine, name, rendered)

    original_concat = getattr(engine, "_concat_segments", None)
    if callable(original_concat):
        def concat_segments(seg_paths, out, *args, **kwargs):
            result = original_concat(seg_paths, out, *args, **kwargs)
            # only the film's picture (vox and the previews join other things with it too)
            if Path(out).name == "_visual.mp4":
                st = _state(Path(out).parent)
                st.update(segs=[Path(str(p)) for p in seg_paths], visual=Path(out), bounds=None,
                          joined=_stamp(out), original_concat=lambda segs, dest: original_concat(segs, dest))
            return result
        engine._concat_segments = concat_segments

    def picture_stalls(path, hold_s: float = HOLD_S):
        """The engine's check (the second the picture stops moving, or None), answered from every freeze of the mux:
        a shot holding still is kept (None), a broken freeze is repaired before the engine muxes again."""
        path = Path(path)
        st = _state(path.parent)
        spans, length = freezes(path, hold_s)
        if spans is None:
            st["broken"] = None
            return None                          # unreadable: the engine's own answer for it
        segs = st.get("segs") or []
        if (spans or (st.get("voice") and length < st["voice"] - SHORT_S)) and segs and not st.get("bounds"):
            st["lengths"] = durations(segs)
            st["bounds"] = bounds_of(st["lengths"])
        planned = planned_lengths(engine, st) if segs else None
        suspects = [i for i, (got, want) in enumerate(zip(st.get("lengths") or [], planned or []))
                    if abs(got - want) > 1.0]
        st["suspects"] = suspects
        rows = classify(spans, st.get("bounds") or [], length, st.get("voice") or 0.0,
                        bounds_of(planned) if planned else None, suspects)
        broken = [r for r in rows if r["broken"]]
        for r in rows:
            if not r["broken"]:
                _log(engine, f"  picture: still for {r['end'] - r['start']:.0f} s at {clock(r['start'])}, inside one "
                             f"shot ({_where(st, r['parts'][0]) if r['parts'] else 'one piece'}): kept")
        st["broken"] = broken or None
        if not broken:
            return None
        st["rows"] = rows
        step = REPAIRS[st.get("repairs", 0)] if st.get("in_mux") and st.get("repairs", 0) < len(REPAIRS) else ""
        if step and segs:
            st["repairs"] = len(REPAIRS)              # whatever happens, this step is not tried twice
            _log(engine, "  picture: " + describe(rows, st) + " Making the parts under it again...")
            try:
                took, done = repair(engine, path.parent, st, rows, step)
                st["repairs"] = REPAIRS.index(took) + 1
                st.setdefault("tried", []).append(done)
                st.update(bounds=None, joined=_stamp(st.get("visual") or path.parent / "_visual.mp4"))
                _log(engine, f"  picture: {done}; muxing again")
            except Exception as e:                             # noqa: BLE001 - the engine muxes again regardless
                st["repairs"] = REPAIRS.index(step) + 1
                _log(engine, f"  picture: the repair didn't work ({type(e).__name__}: {str(e)[:160]})")
        return broken[0]["start"]

    engine._picture_stalls = picture_stalls
    original_mux = engine._final_mux

    def final_mux(job, mp3, *args, **kwargs):
        job = Path(job)
        st = _state(job)
        try:
            voice = float(engine._audio_dur(mp3)) if hasattr(engine, "_audio_dur") else 0.0
        except Exception:                                      # noqa: BLE001 - the length check then sits out
            voice = 0.0
        if st.get("segs") and st.get("joined") != _stamp(job / "_visual.mp4"):
            # the picture this mux reads was not joined here (a pipeline that renders it in one piece, after an
            # earlier join in this process): what that join recorded says nothing about it
            st.update(segs=[], render=None, plan=None, bounds=None, lengths=None)
        st.update(in_mux=True, repairs=0, broken=None, rows=None, voice=voice, tried=[])
        try:
            result = original_mux(job, mp3, *args, **kwargs)
        finally:
            st["in_mux"] = False
        if st.get("broken"):
            stop(engine, job, st)
        (job / REPORT).unlink(missing_ok=True)
        return result
    engine._final_mux = final_mux


def stop(engine, job: Path, st: dict) -> None:
    """The video stops instead of finishing frozen: the frozen file is set aside (Resume assembles the film again
    instead of finding it cached) and what froze is saved for support."""
    rows = st.get("rows") or [dict(r) for r in st.get("broken") or []]
    where = describe(rows, st)
    video = job / "video.mp4"
    try:
        if video.is_file():
            video.replace(job / FROZEN)
    except OSError:
        video.unlink(missing_ok=True)
    try:
        (job / REPORT).write_text(json.dumps({
            "at": int(time.time()), "message": where,
            "freezes": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in rows],
            "parts": [str(p) for p in (st.get("segs") or [])][:400]}, indent=1), encoding="utf-8")
    except OSError:
        pass
    tried = ("Studio made the parts under it again and joined the film again, and it still froze"
             if st.get("tried") else "Putting the film together again three times didn't change it")
    _log(engine, f"  picture: {where} {tried}: the video stops here (the frozen cut is kept as {FROZEN}).")
    raise RuntimeError(f"{where} {tried}, so Studio stopped instead of finishing a frozen video. Your pictures, voice "
                       "and footage are saved. Press Resume saved project to try once more; if it stops here again, "
                       "press Copy diagnostics in Setup & accounts and send it to us.")
