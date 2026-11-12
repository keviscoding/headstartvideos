"""The picture check on finished videos (cr_picture_qc): a video that came out black stops instead of publishing.
A Windows customer's cloned F1 documentary finished twice as "done", a black screen with the narration over it
(Studio 132, 2026-11-09)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_picture_qc as qc  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed here")


def _video(path: Path, parts: list, gop: int = 250) -> Path:
    """parts: [(seconds, "black" | "picture")]; a black part carries a burned caption line, as Studio's videos do.
    gop: frames between keyframes (25: one a second, so the check reads keyframes only)."""
    pieces = []
    for k, (seconds, kind) in enumerate(parts):
        piece = path.parent / f"part{k}.mp4"
        src = (f"color=c=black:s=640x360:r=25:d={seconds}" if kind == "black"
               else f"testsrc2=s=640x360:r=25:d={seconds}")
        caption = ",drawbox=x=200:y=300:w=240:h=14:color=white:t=fill" if kind == "black" else ""
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-vf", "format=yuv420p" + caption,
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop), str(piece)], check=True)
        pieces.append(piece)
    listing = path.parent / "parts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in pieces))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-f", "lavfi",
                    "-i", f"sine=f=220:d={sum(s for s, _ in parts)}", "-shortest", "-c:v", "copy", "-c:a", "aac",
                    str(path)], check=True)
    return path


class _Engine:
    def __init__(self):
        self.published, self.lines = [], []
        self.log = self.lines.append

    def publish_named_copy(self, job, title, fresh=""):
        self.published.append((Path(job).name, title, fresh))
        return Path(job) / "published.mp4"


def test_a_black_video_with_captions_is_set_aside_and_stops_with_the_reason(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    _video(job / "video.mp4", [(2, "picture"), (20, "black")], gop=25)
    (job / "plan.json").write_text(json.dumps([["video", "/j/yt_intro.mp4", False, 2.0],
                                               ["video", "/j/yt_lando_03.mp4", False, 10.0],
                                               ["video", "/j/yt_lando_07.mp4", False, 10.0]]))
    (job / "segments").mkdir()
    for k in range(3):                                   # what Studio 132 rendered: the last two came out black
        (job / "segments" / f"seg_{k:03d}.mp4").write_bytes(b"segment")
    engine = _Engine()
    qc.install(engine)
    with pytest.raises(RuntimeError, match=r"came out black for 0:2\d of its 0:22 .*Press Resume"):
        engine.publish_named_copy(job, "Is This the End of Lewis Hamilton at Ferrari?")
    assert engine.published == [], "a black video is never published"
    assert (job / "video.black.mp4").is_file() and not (job / "video.mp4").exists(), "Resume assembles it again"
    log = "\n".join(engine.lines)
    assert "yt_lando_03.mp4, yt_lando_07.mp4" in log and "yt_intro.mp4" not in log
    assert json.loads((job / "picture_qc.json").read_text())["frames"] >= 20, "read from its keyframes"
    marks = sorted(p.name for p in (job / "segments").glob("*.black"))
    assert marks == ["seg_001.black", "seg_002.black"], "Resume renders the black shots again, not the intro"


def test_a_video_with_its_picture_publishes_and_is_measured_once(tmp_path, monkeypatch):
    job = tmp_path / "job"
    job.mkdir()
    _video(job / "video.mp4", [(4, "black"), (14, "picture")])     # a dark opening card is the channel's own
    engine = _Engine()
    qc.install(engine)
    qc.install(engine)                                               # installed once, however often it's called
    engine.publish_named_copy(job, "A title")
    assert engine.published == [("job", "A title", "")]
    saved = json.loads((job / "picture_qc.json").read_text())
    assert saved["ok"] and 3 <= saved["black_s"] <= 6
    monkeypatch.setattr(qc, "frames", lambda *a, **k: pytest.fail("measured twice"))
    engine.publish_named_copy(job, "A title")
    assert len(engine.published) == 2


def test_a_check_that_cannot_measure_never_holds_a_video_back(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "video.mp4").write_bytes(b"not a video")
    engine = _Engine()
    qc.install(engine)
    engine.publish_named_copy(job, "A title")
    assert engine.published == [("job", "A title", "")]
    nothing = tmp_path / "empty"
    nothing.mkdir()
    engine.publish_named_copy(nothing, "Nothing rendered yet")      # no video yet: nothing to look at
    assert engine.published[-1] == ("empty", "Nothing rendered yet", "")


def test_black_stretches_are_timed_by_the_frames_around_them():
    rows = [(0.0, 0.5), (2.0, 0.01), (4.0, 0.0), (9.0, 0.4), (12.0, 0.02)]
    assert qc.black_stretches(rows, 15.0) == [(2.0, 9.0), (12.0, 15.0)]
