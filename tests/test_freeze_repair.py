"""A finished video whose picture freezes while the voice goes on is repaired from its segments, or stops; it is never
published frozen (a customer's 10-minute video froze from 5:15 on every try while the segments under it moved,
2026-10-25). A stillness the segments share is how those shots look and is kept: the check can't see a change under
about 2% of the frame (review, 2026-10-26). Real ffmpeg, tiny frames."""
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
BAD = set()          # slots whose first render plays fine alone, but the picture joined from it stands still from there
POISON = set()       # ...those segment files
FROZEN_MUX = []      # [second]: every mux's picture stands still from there while the segments under it move
def log(msg): LOG.append(msg)
def _audio_dur(p):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0
def _concat_quote(p): return str(p).replace("'", "'\\''")
def _hold(path, at):
    # the picture stands still from `at` to its end (the customer's froze from 5:15), its length and sound unchanged
    path = Path(path)
    tmp = path.with_name("_held" + path.suffix)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf",
                    f"trim=end={at + 0.05},tpad=stop_mode=clone:stop_duration={_audio_dur(path) - at + 1}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", f"{_audio_dur(path):.3f}",
                    str(tmp)], check=True)
    tmp.replace(path)
def _render_astro_segments(plan, job, force, white_fade=0.0, style=""):
    seg_dir = Path(job) / "segments"; seg_dir.mkdir(exist_ok=True)
    out = []
    for i, (kind, path, leak, dur) in enumerate(plan):
        seg = seg_dir / f"seg_{i:03d}.mp4"
        if not seg.exists() or force:
            RENDERS.append(i)
            (POISON.add if i in BAD and RENDERS.count(i) == 1 else POISON.discard)(str(seg))
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"testsrc2=s={VIDEO_W}x{VIDEO_H}:r={FPS}:d={dur}", "-c:v", "libx264", "-preset",
                            "ultrafast", "-pix_fmt", "yuv420p", "-r", str(FPS), str(seg)], check=True)
        out.append(seg)
    return out
def _concat_segments(seg_paths, out):
    lst = Path(out).parent / "_seglist.txt"
    lst.write_text("".join(f"file '{_concat_quote(p)}'\n" for p in seg_paths), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(out)], check=True)
    bad = [k for k, p in enumerate(seg_paths) if str(p) in POISON]
    if bad:
        _hold(out, sum(_audio_dur(p) for p in seg_paths[:bad[0]]))
def _picture_stalls(path, hold_s=12.0):
    raise AssertionError("the hook answers this")
def _final_mux(job, mp3, srt, burn_subs, add_qr=False, vignette=False):
    total = _audio_dur(mp3)
    for attempt in range(3):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "_visual.mp4", "-i", str(mp3), "-map", "0:v", "-map",
                        "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-r", str(FPS), "-c:a", "aac", "-t",
                        f"{total:.3f}", "_video_tmp.mp4"], check=True, cwd=str(job))
        if FROZEN_MUX:
            _hold(Path(job) / "_video_tmp.mp4", FROZEN_MUX[0])
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


def test_a_picture_frozen_over_moving_segments_is_made_again_and_finishes_moving(engine):
    """The customer's case: the segments move, the picture joined from them stands still from one part to the end
    while the voice goes on. Muxing again can't change that; making the parts under it again and joining them does."""
    engine.BAD.add(2)
    video = engine.assemble(engine.job, engine.mp3, 12)
    assert video.is_file() and frozen(video) == []
    assert sorted(engine.RENDERS[12:]) == list(range(2, 12)), "the parts under the freeze, and only those, made again"
    assert len(engine.MUXES) == 2
    assert any("froze at 0:10" in line and "part 3 of 12" in line for line in engine.LOG), engine.LOG
    assert not (engine.job / cr_freeze.REPORT).exists() and not (engine.job / cr_freeze.FROZEN).exists()


def test_a_mux_that_stays_frozen_over_moving_segments_stops_the_video_instead_of_publishing_it(engine):
    engine.FROZEN_MUX.append(10.0)
    with pytest.raises(RuntimeError) as stopped:
        engine.assemble(engine.job, engine.mp3, 12)
    message = str(stopped.value)
    assert "froze at 0:10" in message and "part 3 of 12" in message and "scene_02.jpg" in message
    assert "Resume saved project" in message
    assert len(engine.MUXES) == 3, "the engine's three muxes, with a repair before the second and the third"
    assert not (engine.job / "video.mp4").exists(), "a frozen video is never left where Resume reads it as finished"
    assert (engine.job / cr_freeze.FROZEN).is_file()
    report = json.loads((engine.job / cr_freeze.REPORT).read_text(encoding="utf-8"))
    assert report["freezes"][0]["broken"] and report["freezes"][0]["source"] == "moves"
    assert 2 in report["freezes"][0]["parts"]
    # Resume: the film is assembled again (no cached frozen video to hand back), and when whatever froze it has
    # passed, it finishes
    engine.FROZEN_MUX.clear()
    muxes = len(engine.MUXES)
    assert frozen(engine.assemble(engine.job, engine.mp3, 12)) == []
    assert len(engine.MUXES) == muxes + 1 and not (engine.job / cr_freeze.REPORT).exists()


W, H = 480, 270       # the check shrinks every frame to 64 px wide: these are the review's 1920 px cases, scaled


def _encode(args, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
                    "yuv420p", "-r", "10", str(out)], check=True)


def _stick_figures(seg_dir):
    """Two 10 s fight scenes: a small figure moving 150 px/s (at 1920) on white, a caption bar changing width."""
    for i, (x0, way) in enumerate(((40, 1), (300, -1))):
        figure = (f"drawbox=x='{x0}+{way}*t*37.5':y=110:w=2:h=40:color=black:t=fill,"
                  f"drawbox=x='{x0 - 6}+{way}*t*37.5':y=100:w=14:h=12:color=black:t=2,"
                  f"drawbox=x=140:y=240:w='120+t*12':h=14:color=0x202020:t=fill")
        _encode(["-f", "lavfi", "-i", f"color=white:s={W}x{H}:r=10:d=10", "-vf", figure], seg_dir / f"seg_{i:03d}.mp4")
    return [10.0, 10.0]


def _starfields(seg_dir):
    """Two dark starfield shots, each with the engine's 0.15 zoom."""
    for i in range(2):
        stars = seg_dir / f"stars_{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={W * 2}x{H * 2}:d=1",
                        "-vf", f"geq=lum='if(gt(random({i + 1}),0.997),230,0)':cb=128:cr=128", "-frames:v", "1",
                        str(stars)], check=True)
        _encode(["-loop", "1", "-i", str(stars), "-vf", f"zoompan=z='1+0.15*on/100':d=100:x='iw/2-(iw/zoom/2)':"
                 f"y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=10", "-t", "10"], seg_dir / f"seg_{i:03d}.mp4")
    return [10.0, 10.0]


def _mindmap_pan(seg_dir):
    """One 40 s shot panning over a mindmap at 15 px/s (at 1920): a hold longer than HELD_MAX_S, by the check."""
    page = seg_dir / "map.png"
    boxes = ",".join(f"drawbox=x={40 + k * 110}:y={60 + (k % 3) * 60}:w=80:h=30:color=0x3355aa:t=2" for k in range(6))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=0xf4f1ea:s={W + 200}x{H}:d=1", "-vf",
                    boxes + f",drawbox=x=0:y=134:w={W + 200}:h=2:color=0x888888:t=fill", "-frames:v", "1", str(page)],
                   check=True)
    _encode(["-loop", "1", "-i", str(page), "-vf", f"crop={W}:{H}:x='min(t*3.75\\,200)':y=0", "-t", "40"],
            seg_dir / "seg_000.mp4")
    return [40.0]


@pytest.mark.parametrize("shots", [_stick_figures, _starfields, _mindmap_pan], ids=["stick-figures", "starfields",
                                                                                   "mindmap-pan"])
def test_a_stillness_the_segments_share_is_how_they_look_and_the_video_publishes(engine, shots):
    """The review's false stops (2026-10-26): by the check's own measure these pictures stand still across their cut,
    or for longer than one shot may hold, and so do the segments they are made of. Kept, muxed once, published."""
    seg_dir = engine.job / "segments"
    seg_dir.mkdir()
    lengths = shots(seg_dir)
    plan = [("motion", f"/scenes/shot_{i}.json", False, d) for i, d in enumerate(lengths)]
    segs = engine._render_astro_segments(plan, engine.job, False)
    engine._concat_segments(segs, engine.job / "_visual.mp4")
    voice = engine.job / "voice.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=d={sum(lengths)}", "-c:a", "aac",
                    str(voice)], check=True)
    engine._final_mux(engine.job, voice, engine.job / "subs.srt", True)
    video = engine.job / "video.mp4"
    assert video.is_file() and not (engine.job / cr_freeze.REPORT).exists()
    assert frozen(video), "the check does see it stand still (else this case proves nothing)"
    assert len(engine.MUXES) == 1 and engine.RENDERS == [], "kept as it is: nothing made again, no pointless re-mux"
    assert any("so are the shots under it" in line and "kept" in line for line in engine.LOG), engine.LOG


def test_a_picture_whose_segments_cant_be_looked_at_publishes_as_it_always_did(engine):
    """A picture rendered in one piece (no joined segments to look at), frozen for more than a minute to its end: the
    engine's own answer (muxed again, three times, then the last mux kept), never a stop."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=80", "-c:v",
                    "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(engine.job / "_visual.mp4")],
                   check=True)
    engine._hold(engine.job / "_visual.mp4", 10.0)
    voice = engine.job / "voice.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=80", "-c:a", "aac", str(voice)],
                   check=True)
    engine._final_mux(engine.job, voice, engine.job / "subs.srt", True)
    assert (engine.job / "video.mp4").is_file() and not (engine.job / cr_freeze.REPORT).exists()
    assert len(engine.MUXES) == 3 and engine.RENDERS == []
    assert any("couldn't be looked at" in line for line in engine.LOG), engine.LOG


def test_segments_that_cant_be_read_decide_nothing(tmp_path):
    st = {"segs": [tmp_path / "gone_0.mp4", tmp_path / "gone_1.mp4"], "bounds": cr_freeze.bounds_of([10, 10])}
    assert cr_freeze.source_still(st, 0.0, 20.0) is None
    assert cr_freeze.source_still({"segs": [], "bounds": []}, 0.0, 20.0) is None


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


def test_freezes_are_classified_against_cuts_holds_the_voice_and_their_own_segments():
    bounds = cr_freeze.bounds_of([5, 5, 40, 5])                      # part 3 runs 40 s instead of its 5
    planned = cr_freeze.bounds_of([5, 5, 5, 5])
    moves, still, unknown = (lambda s, e: False), (lambda s, e: True), (lambda s, e: None)
    rows = cr_freeze.classify([(10.0, 50.0)], bounds, 55.0, 55.0, planned, suspects=[2], still=moves)
    assert rows[0]["broken"] and rows[0]["why"] == "cut" and rows[0]["source"] == "moves" and rows[0]["parts"] == [2]
    rows = cr_freeze.classify([(10.0, 50.0)], bounds, 55.0, 55.0, planned, suspects=[2], still=still)
    assert not rows[0]["broken"] and rows[0]["source"] == "still", "its segments stand still there too: kept"
    for check in (unknown, None):
        rows = cr_freeze.classify([(10.0, 50.0)], bounds, 55.0, 55.0, planned, suspects=[2], still=check)
        assert not rows[0]["broken"] and rows[0]["source"] == "unknown", "can't be looked at: never a stop"
    held = cr_freeze.classify([(5.5, 19.0)], cr_freeze.bounds_of([5, 15, 5]), 25.0, 25.0, still=moves)
    assert not held[0]["broken"] and held[0]["parts"] == [1] and held[0]["why"] == ""
    long_hold = cr_freeze.classify([(5.0, 40.0)], cr_freeze.bounds_of([5, 40, 5]), 50.0, 50.0, still=moves)
    assert long_hold[0]["broken"] and long_hold[0]["why"] == "held"
    short = cr_freeze.classify([], cr_freeze.bounds_of([5, 5]), 7.0, 30.0)
    assert short[0]["why"] == "short" and short[0]["broken"] and short[0]["start"] == 7.0 and short[0]["parts"] == [1]
    # a picture made in one piece: only a long freeze to the end is looked at, and there is nothing to look at it with
    assert not cr_freeze.classify([(30.0, 45.0)], [], 60.0, 60.0)[0]["broken"]
    end = cr_freeze.classify([(10.0, 600.0)], [], 600.0, 600.0, still=lambda s, e: None)[0]
    assert end["why"] == "end" and end["source"] == "unknown" and not end["broken"]


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
