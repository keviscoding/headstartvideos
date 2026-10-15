"""The sound storyboard a scored video follows (cr_storyboard)."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_storyboard as sb  # noqa: E402

SRT = """1
00:00:00,500 --> 00:00:02,000
London, 1820s.

2
00:00:02,200 --> 00:00:04,000
You are five years old,

3
00:00:04,100 --> 00:00:06,000
and your job is the chimney.

4
00:00:30,000 --> 00:00:33,000
The master takes everything.

5
00:00:58,000 --> 00:01:00,000
Yeah, you're not getting paid.
"""
LOOK = {"sound": {"music": True, "music_rel_db": -24, "density": "sparse",
                  "score": {"genres": ["dark ambient synth"], "moods": ["tense"], "beat": "none"}}}


def job_with(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "subs.srt").write_text(SRT)
    return job


def test_the_narration_is_read_as_timed_sentences(tmp_path):
    assert sb.sentences(job_with(tmp_path)) == [(0.5, "London, 1820s."), (2.2, "You are five years old, and your job is the chimney."),
                                                (30.0, "The master takes everything."), (58.0, "Yeah, you're not getting paid.")]


def test_the_storyboard_is_held_to_its_rules(tmp_path):
    raw = {"sections": [{"t": 0, "mood": "tense", "role": "cold open", "music": "sub drone, clock ticks, no drums"},
                        {"t": 5, "mood": "too soon"}, {"t": 29.5, "mood": "driving", "role": "hook drop", "music": "drums land"}],
           "events": [{"t": 29.9, "kind": "hit"}, {"t": 33, "kind": "sub_drop"}, {"t": 50, "kind": "riser"},
                      {"t": 57.8, "kind": "silence"}, {"t": 10, "kind": "laser"}, {"t": 0.2, "kind": "hit"}]}
    board = sb.clean(raw, 70.0, [0.5, 2.2, 30.0, 58.0], "sparse")
    assert [s["t"] for s in board["sections"]] == [0.0, 30.0], "sections 20 s+ apart, on sentence starts"
    kinds = [(e["t"], e["kind"]) for e in board["events"]]
    assert kinds == [(29.9, "hit"), (50.0, "riser"), (57.8, "silence")], "no second big hit within 8 s; sparse spacing"
    assert sb.cues(board) == [(29.9, "hit", -7.0), (50.0, "riser", -9.0)]
    assert "drums land" in sb.hint(board, 30.0)


def test_a_storyboard_is_written_once_and_turns_the_music(tmp_path):
    job = job_with(tmp_path)
    asked = []
    answer = {"sections": [{"t": 0, "mood": "tense", "role": "cold open", "music": "no drums"},
                           {"t": 30, "mood": "driving", "role": "hook drop", "music": "drums"}],
              "events": [{"t": 30, "kind": "hit", "why": "the promise"}]}
    board = sb.make(lambda p: asked.append(p) or json.dumps(answer), job, LOOK, 62.0, log=lambda m: None)
    assert board["sections"][1]["t"] == 30.0 and "dark ambient synth" in asked[0] and "[30.0] The master" in asked[0]
    assert sb.make(lambda p: (_ for _ in ()).throw(AssertionError("asked twice")), job, LOOK, 62.0) == board
    sb.moods(job, board)
    assert json.loads((job / "moods.json").read_text()) == [{"t": 0.0, "mood": "tense"}, {"t": 30.0, "mood": "driving"}]


def test_silences_and_swells_shape_the_music_bed(tmp_path):
    bed = tmp_path / "_bed.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=12", "-ac", "2", str(bed)], check=True)
    out = sb.shape(bed, {"events": [{"t": 6.0, "kind": "silence"}, {"t": 1.0, "kind": "riser"}]})
    def level(path, start):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", "0.2", "-i", str(path), "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True)
        return float(r.stderr.split("mean_volume:")[1].split("dB")[0])
    assert level(out, 5.7) < level(out, 3.0) - 15, "the music drops out just before the line"
    assert abs(level(out, 9.5) - level(out, 3.0)) < 1, "and comes back"
    assert sb.shape(bed, {"events": []}) == bed
