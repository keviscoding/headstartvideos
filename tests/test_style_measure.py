"""Local style measurement: cuts and transitions, what moves in a shot, pop-ons, light and the shape of the sound."""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_style_measure as m  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Local FFmpeg is unavailable")
cv2 = pytest.importorskip("cv2")


def picture(path, seed, dark=False):
    """A detailed picture (blocks of colour with edges) so motion and cuts are visible."""
    from PIL import Image
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 90 if dark else 255, (18, 32, 3), dtype=np.uint8)
    Image.fromarray(small).resize((640, 360), Image.NEAREST).save(path)
    return path


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def clip(tmp, name, kind, seed, seconds=3, dark=False):
    img = picture(tmp / f"{name}.png", seed, dark)
    out = tmp / f"{name}.mp4"
    if kind == "still":
        vf = "scale=640:360,fps=30"
    elif kind == "zoom":
        vf = (f"zoompan=z='1+0.004*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={seconds * 30}:s=640x360:fps=30")
    elif kind == "pops":   # a still layout where two labels appear
        vf = ("scale=640:360,fps=30,drawbox=x=40:y=40:w=160:h=60:color=white:t=fill:enable='gte(t,1)',"
              "drawbox=x=420:y=260:w=160:h=60:color=yellow:t=fill:enable='gte(t,2)'")
    if kind == "moving":
        ffmpeg("-f", "lavfi", "-i", f"life=s=640x360:mold=10:r=30:ratio=0.3:seed={seed}:death_color=#204060:life_color=#e0c080",
               "-t", str(seconds), "-pix_fmt", "yuv420p", str(out))
    else:
        ffmpeg("-loop", "1", "-i", str(img), "-vf", vf, "-t", str(seconds), "-pix_fmt", "yuv420p", str(out))
    return out


def join(tmp, clips, name="joined.mp4"):
    out = tmp / name
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]
    chain = "".join(f"[{i}:v]" for i in range(len(clips)))
    ffmpeg(*inputs, "-filter_complex", f"{chain}concat=n={len(clips)}:v=1:a=0[v]", "-map", "[v]", "-pix_fmt", "yuv420p",
           str(out))
    return out


def test_hard_cuts_split_shots_and_each_shot_says_what_moves(tmp_path):
    video = join(tmp_path, [clip(tmp_path, "a", "still", 1), clip(tmp_path, "b", "zoom", 2),
                            clip(tmp_path, "c", "moving", 3)])
    result = m.measure(video)
    shots = result["shots"]
    assert len(shots) == 3, shots
    assert [s["kind"] for s in shots] == ["still", "camera", "moving"]
    assert [s["in"] for s in shots] == ["start", "cut", "cut"]
    assert abs(shots[1]["t0"] - 3.0) <= 0.2 and abs(shots[2]["t0"] - 6.0) <= 0.2
    assert shots[1]["zoom"] > 0, "the zoompan pushes in"
    edit = result["edit"]
    assert edit["shots"] == 3 and 2.5 <= edit["median_shot_s"] <= 3.5
    assert set(edit["motion_share"]) == {"still", "camera", "moving"}


def test_a_fade_through_black_and_a_dissolve_are_told_apart_from_cuts(tmp_path):
    a, b, c = (clip(tmp_path, n, "still", s) for n, s in (("a", 4), ("b", 5), ("c", 6)))
    faded = tmp_path / "fade.mp4"
    ffmpeg("-i", str(a), "-i", str(b), "-i", str(c), "-filter_complex",
           "[0:v][1:v]xfade=transition=fadeblack:duration=1:offset=2[ab];"
           "[ab][2:v]xfade=transition=fade:duration=1.2:offset=4[v]", "-map", "[v]", "-pix_fmt", "yuv420p", str(faded))
    kinds = [s["in"] for s in m.measure(faded)["shots"]]
    assert kinds == ["start", "fade", "dissolve"], kinds


def test_labels_popping_onto_a_still_layout_count_as_graphic_motion(tmp_path):
    result = m.measure(clip(tmp_path, "p", "pops", 7, seconds=4))
    shot = result["shots"][0]
    assert len(result["shots"]) == 1 and shot["kind"] == "still" and shot["pops"] == 2
    assert result["edit"]["still_with_pops_share"] == 1.0


def test_the_look_reads_dark_pictures_as_dark(tmp_path):
    dark = m.measure(clip(tmp_path, "d", "still", 8, dark=True))["look"]
    light = m.measure(clip(tmp_path, "l", "still", 8))["look"]
    assert dark["luma"] < 60 < light["luma"] and dark["dark_share"] == 1.0
    assert dark["palette"] and dark["palette"][0]["hex"].startswith("#")


def tone(seconds, freq, level, sr=m.AUDIO_SR):
    t = np.arange(int(seconds * sr)) / sr
    return (level * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def write_wav(path, x):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(m.AUDIO_SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def test_music_filling_the_pauses_is_measured_against_the_voice(tmp_path, monkeypatch):
    # "speech" at 0.5 full scale, a music bed 14 dB under it that carries on through every pause
    voice = np.concatenate([tone(2, 220, 0.5), np.zeros(int(0.8 * m.AUDIO_SR), np.float32)] * 5)
    bed = tone(len(voice) / m.AUDIO_SR, 440, 0.5 * 10 ** (-14 / 20))
    path = write_wav(tmp_path / "scored.wav", voice + bed)
    monkeypatch.setattr(m, "speech_spans", lambda x: [(i * 2.8, i * 2.8 + 2) for i in range(5)])
    sound = m.measure_sound(path, 14)
    assert sound["narrated"] is True
    assert -17 <= sound["bed_in_pauses_db"] <= -11
    assert sound["filled_pauses_share"] == 1.0 and sound["silent_pauses_share"] == 0.0
    assert sound["pause_fill"]["tonal"] > 0.8
    assert sound["pauses_per_min"]["0.6"] > 15


def test_music_heard_in_only_some_pauses_is_measured_where_it_plays(tmp_path, monkeypatch):
    # music 30 dB under the voice in two of the five pauses, silence in the rest
    gap = int(0.8 * m.AUDIO_SR)
    parts = []
    for k in range(5):
        parts.append(tone(2, 220, 0.5))
        parts.append(tone(0.8, 440, 0.5 * 10 ** (-30 / 20))[:gap] if k in (1, 3) else np.zeros(gap, np.float32))
    path = write_wav(tmp_path / "some.wav", np.concatenate(parts))
    monkeypatch.setattr(m, "speech_spans", lambda x: [(i * 2.8, i * 2.8 + 2) for i in range(5)])
    sound = m.measure_sound(path, 14)
    assert sound["filled_pauses_share"] < 0.6 and sound["bed_in_pauses_db"] < -40, "the median over every pause is silence"
    assert -34 <= sound["bed_when_heard_db"] <= -26, "where the music plays, it is 30 dB under the voice"


def test_silent_pauses_are_measured_as_silent(tmp_path, monkeypatch):
    voice = np.concatenate([tone(2, 220, 0.5), np.zeros(int(0.8 * m.AUDIO_SR), np.float32)] * 5)
    path = write_wav(tmp_path / "dry.wav", voice)
    monkeypatch.setattr(m, "speech_spans", lambda x: [(i * 2.8, i * 2.8 + 2) for i in range(5)])
    sound = m.measure_sound(path, 14)
    assert sound["filled_pauses_share"] == 0.0 and sound["silent_pauses_share"] == 1.0


def test_without_a_voice_detector_the_numbers_it_would_give_are_left_out(tmp_path, monkeypatch):
    path = write_wav(tmp_path / "x.wav", tone(5, 220, 0.3))
    monkeypatch.setattr(m, "speech_spans", lambda x: None)
    sound = m.measure_sound(path, 5)
    assert "bed_in_pauses_db" not in sound and "speech_share" not in sound


def test_sections_combine_into_an_opening_and_a_body():
    def shot(t0, t1, kind="still"):
        return {"t0": t0, "t1": t1, "in": "cut", "kind": kind, "pops": 0}
    opening = {"start": 0, "seconds": 60, "shots": [shot(i * 2, i * 2 + 2) for i in range(30)],
               "look": {"luma": 40, "palette": []}, "sound": {"seconds": 60, "bed_in_pauses_db": -14}}
    later = {"start": 600, "seconds": 60, "shots": [shot(600 + i * 6, 606 + i * 6, "camera") for i in range(10)],
             "look": {"luma": 60, "palette": []}, "sound": {"seconds": 60, "bed_in_pauses_db": -18}}
    c = m.combine([opening, later])
    assert c["opening"]["median_shot_s"] == 2.0 and c["body"]["median_shot_s"] == 6.0
    assert c["look"]["luma"] == 50 and c["sound"]["bed_in_pauses_db"] == -16
    assert c["sections"] == [[0, 60], [600, 660]]
