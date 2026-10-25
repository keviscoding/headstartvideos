"""A finished video whose picture freezes while the voice goes on is repaired from its segments, or stops; it is never
published frozen (a customer's 10-minute video froze from 5:15 on every try, 2026-10-25). Real ffmpeg, tiny frames."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_freeze

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")

# A stand-in for the engine's assembly, run as module code so its calls go through the module the way make_video's
# do (the hooks replace module attributes). The final mux is the engine's own loop: three muxes while the picture
# stops moving, then the last mux is kept.
ENGINE = r'''
import subprocess, time
from pathlib import Path
FPS, VIDEO_W, VIDEO_H, SECONDS_PER_SLOT = 10, 160, 90, 5.0
HERE = Path(".")
LOG, MUXES, RENDERS = [], [], []
BAD = set()          # slots whose renders come out a still that runs 40 s (a broken segment)
ALWAYS_BAD = set()   # ...on every render, not only the first
def log(msg): LOG.append(msg)
def _audio_dur(p):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0
def _concat_quote(p): return str(p).replace("'", "'\\''")
def _render_astro_segments(plan, job, force, white_fade=0.0, style=""):
    seg_dir = Path(job) / "segments"; seg_dir.mkdir(exist_ok=True)
    out = []
    for i, (kind, path, leak, dur) in enumerate(plan):
        seg = seg_dir / f"seg_{i:03d}.mp4"
        if not seg.exists() or force:
            RENDERS.append(i)
            broken = i in ALWAYS_BAD or (i in BAD and RENDERS.count(i) == 1)
            src = (f"color=c=0x{(i * 40) % 255:02x}6080:s={VIDEO_W}x{VIDEO_H}:r={FPS}:d=40" if broken else
                   f"testsrc2=s={VIDEO_W}x{VIDEO_H}:r={FPS}:d={dur}")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-c:v", "libx264", "-preset",
                            "ultrafast", "-pix_fmt", "yuv420p", "-r", str(FPS), str(seg)], check=True)
        out.append(seg)
    return out
def _concat_segments(seg_paths, out):
    lst = Path(out).parent / "_seglist.txt"
    lst.write_text("".join(f"file '{_concat_quote(p)}'\n" for p in seg_paths), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(out)], check=True)
def _picture_stalls(path, hold_s=12.0):
    raise AssertionError("the hook answers this")
def _final_mux(job, mp3, srt, burn_subs, add_qr=False, vignette=False):
    total = _audio_dur(mp3)
    for attempt in range(3):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "_visual.mp4", "-i", str(mp3), "-map", "0:v", "-map",
                        "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-r", str(FPS), "-c:a", "aac", "-t",
                        f"{total:.3f}", "_video_tmp.mp4"], check=True, cwd=str(job))
        MUXES.append(attempt)
        if _picture_stalls(Path(job) / "_video_tmp.mp4") is None:
            break
    else:
        log("the picture still stops early after three tries - keeping the last mux")
    (Path(job) / "_video_tmp.mp4").replace(Path(job) / "video.mp4")
def assemble(job, mp3, slots):
    plan = [("photo", f"/pictures/scene_{i:02d}.jpg", False, 5.0) for i in range(slots)]
    segs = _render_astro_segments(plan, job, False, white_fade=0.0, style="documentary")
    _concat_segments(segs, Path(job) / "_visual.mp4")
    _final_mux(job, mp3, Path(job) / "subs.srt", True)
    return Path(job) / "video.mp4"
'''


@pytest.fixture
def engine(tmp_path):
    module = types.ModuleType("fake_engine")
    exec(compile(ENGINE, "fake_engine", "exec"), module.__dict__)
    cr_freeze.install(module)
    job = tmp_path / "job"
    job.mkdir()
    mp3 = job / "audio.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=60", "-c:a", "aac", str(mp3)], check=True)
    module.job, module.mp3 = job, mp3
    yield module
    cr_freeze._jobs.clear()


def frozen(video):
    spans, _ = cr_freeze.freezes(video)
    return spans


def test_a_broken_segment_is_made_again_and_the_film_finishes_moving(engine):
    """The customer's case: one part's own picture is broken (a still that runs far past its slot), so the picture
    stands still from there while the voice goes on. Muxing again can't change that; making that part again does."""
    engine.BAD.add(2)
    video = engine.assemble(engine.job, engine.mp3, 12)
    assert video.is_file() and frozen(video) == []
    assert engine.RENDERS.count(2) == 2, "the broken part was rendered again"
    assert sorted(set(engine.RENDERS)) == list(range(12)) and len(engine.RENDERS) == 13, "and only that part"
    assert len(engine.MUXES) == 2
    assert any("froze at 0:10" in line and "part 3 of 12" in line for line in engine.LOG), engine.LOG
    assert not (engine.job / cr_freeze.REPORT).exists() and not (engine.job / cr_freeze.FROZEN).exists()


def test_a_part_that_stays_broken_stops_the_video_instead_of_publishing_it_frozen(engine):
    engine.ALWAYS_BAD.add(2)
    with pytest.raises(RuntimeError) as stopped:
        engine.assemble(engine.job, engine.mp3, 12)
    message = str(stopped.value)
    assert "froze at 0:10" in message and "part 3 of 12" in message and "scene_02.jpg" in message
    assert "Resume saved project" in message
    assert len(engine.MUXES) == 3, "the engine's three muxes, with a repair before the second and the third"
    assert not (engine.job / "video.mp4").exists(), "a frozen video is never left where Resume reads it as finished"
    assert (engine.job / cr_freeze.FROZEN).is_file()
    report = json.loads((engine.job / cr_freeze.REPORT).read_text(encoding="utf-8"))
    assert report["freezes"][0]["broken"] and 2 in report["freezes"][0]["parts"]
    # Resume: the film is assembled again (no cached frozen video to hand back), and when the part renders right
    # this time (whatever broke it was passing) the repair makes it again and the video finishes
    engine.ALWAYS_BAD.clear()
    muxes = len(engine.MUXES)
    assert frozen(engine.assemble(engine.job, engine.mp3, 12)) == []
    assert len(engine.MUXES) == muxes + 2 and not (engine.job / cr_freeze.REPORT).exists()


def test_a_still_shot_inside_its_own_slot_is_kept_without_muxing_again(engine):
    """A still picture held longer than the check's 12 s, inside one slot, is how that shot looks: kept, one mux."""
    plan = [("photo", "/pictures/a.jpg", False, 5.0), ("photo", "/pictures/still.jpg", False, 15.0),
            ("photo", "/pictures/b.jpg", False, 5.0)]
    seg_dir = engine.job / "segments"
    seg_dir.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=10:d=15", "-c:v",
                    "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(seg_dir / "seg_001.mp4")], check=True)
    segs = engine._render_astro_segments(plan, engine.job, False)
    engine._concat_segments(segs, engine.job / "_visual.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=25", "-c:a", "aac",
                    str(engine.job / "short.m4a")], check=True)
    engine._final_mux(engine.job, engine.job / "short.m4a", engine.job / "subs.srt", True)
    assert (engine.job / "video.mp4").is_file()
    assert len(engine.MUXES) == 1 and engine.RENDERS.count(1) == 0
    assert any("inside one shot" in line and "still.jpg" in line for line in engine.LOG), engine.LOG


def test_freezes_are_classified_against_cuts_holds_and_the_voice():
    bounds = cr_freeze.bounds_of([5, 5, 40, 5])                      # part 3 runs 40 s instead of its 5
    planned = cr_freeze.bounds_of([5, 5, 5, 5])
    rows = cr_freeze.classify([(10.0, 50.0)], bounds, 55.0, 55.0, planned, suspects=[2])
    assert rows[0]["broken"] and rows[0]["why"] == "cut" and rows[0]["parts"] == [2]
    held = cr_freeze.classify([(5.5, 19.0)], cr_freeze.bounds_of([5, 15, 5]), 25.0, 25.0)
    assert not held[0]["broken"] and held[0]["parts"] == [1]
    long_hold = cr_freeze.classify([(5.0, 40.0)], cr_freeze.bounds_of([5, 40, 5]), 50.0, 50.0)
    assert long_hold[0]["broken"] and long_hold[0]["why"] == "held"
    short = cr_freeze.classify([], cr_freeze.bounds_of([5, 5]), 7.0, 30.0)
    assert short[0]["why"] == "short" and short[0]["start"] == 7.0 and short[0]["parts"] == [1]
    # a picture made in one piece: only a long freeze to the end is broken
    assert not cr_freeze.classify([(30.0, 45.0)], [], 60.0, 60.0)[0]["broken"]
    assert cr_freeze.classify([(10.0, 600.0)], [], 600.0, 600.0)[0]["why"] == "end"


def test_a_repair_never_pays_for_anything(monkeypatch, tmp_path):
    """The steps before the engine's render loop that can pay (stills set in motion, depth cut-outs, kit transition
    redraws) are switched off while a segment is made again, and switched back on after."""
    animate = types.ModuleType("animate")
    animate.ensure = lambda *a, **k: (_ for _ in ()).throw(AssertionError("paid"))
    kits = types.ModuleType("kits")
    kits.fix_transitions = lambda *a, **k: (_ for _ in ()).throw(AssertionError("paid"))
    monkeypatch.setitem(sys.modules, "animate", animate)
    monkeypatch.setitem(sys.modules, "kits", kits)
    engine = types.SimpleNamespace(HERE=tmp_path, _depth_dlc=lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    before = (animate.ensure, kits.fix_transitions, engine._depth_dlc)
    with cr_freeze.local_only(engine):
        assert engine._depth_dlc() == (None, set()) and animate.ensure() == 0 and kits.fix_transitions() == 0
    assert (animate.ensure, kits.fix_transitions, engine._depth_dlc) == before
