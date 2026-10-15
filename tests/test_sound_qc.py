"""The measured sound check on finished videos (cr_sound_qc)."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_sound_qc as qc  # noqa: E402


def test_the_verdict_compares_with_the_references():
    lines = qc.verdict({"lufs": -14.2, "true_peak": -1.3, "pauses_filled": 0.6, "bed_in_pauses_db": -22.0},
                       {"pauses_filled": 0.62, "bed_in_pauses_db": -21.0})
    assert [r["state"] for r in lines] == ["ok", "ok"] and "the references: 62%" in lines[1]["text"]
    quiet = qc.verdict({"lufs": -19.0, "true_peak": -3.0, "pauses_filled": 0.05, "bed_in_pauses_db": -48.0},
                       {"pauses_filled": 0.9, "bed_in_pauses_db": -18.0})
    assert [r["state"] for r in quiet] == ["check", "check"] and "14 LUFS" in quiet[0]["text"]


def test_a_finished_video_is_measured_once(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=6:r=10", "-f", "lavfi",
                    "-i", "sine=f=330:d=6", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(job / "video.mp4")],
                   check=True)
    got = qc.check(job, log=lambda m: None)
    assert got["lufs"] is not None and json.loads((job / "sound_qc.json").read_text())["lines"]
    assert qc.check(job, log=lambda m: (_ for _ in ()).throw(AssertionError("measured twice"))) == got
