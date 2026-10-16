"""The sound of each place under a story's pictures (cr_ambience)."""
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_ambience as amb  # noqa: E402

LOOK = {"still_suffix": " Flat 2D cartoon, soft city light.",
        "sound": {"ambience": {"scenes": True, "db": -18.0, "default": ""}}}


def job_with(tmp_path, prompts):
    job = tmp_path / "job"
    (job / "images").mkdir(parents=True)
    (job / "astro_scenes.json").write_text(json.dumps([{"text": "t", "prompt": p + LOOK["still_suffix"]} for p in prompts]))
    return job, [("photo", str(job / "images" / f"scene_{i:02d}.jpg"), False, 4.0) for i in range(len(prompts))]


def tone(path: Path, seconds=3.0, sr=48000):
    x = (np.random.default_rng(1).standard_normal((int(seconds * sr), 2)) * 0.1 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(sr), w.writeframes(x.tobytes())


def test_each_picture_brings_its_place_and_a_place_carries_over_the_next_pictures(tmp_path):
    job, plan = job_with(tmp_path, ["A rainy street at dusk, a man runs for cover",
                                    "Close-up of the man's worried face",
                                    "Inside a crowded market, merchants shouting",
                                    "A quiet kitchen table with a letter"])
    assert amb.scenes(job, plan, LOOK) == [(0.0, 8.0, "rain"), (8.0, 12.0, "market"), (12.0, 16.0, "room")]
    assert amb.place_of("Wide shot of the city", " soft city light") == "city"
    assert amb.place_of("A portrait of a man" + LOOK["still_suffix"], LOOK["still_suffix"]) == "", \
        "the style's own words never choose a place"


def test_the_track_is_laid_from_the_library_at_the_styles_level(tmp_path):
    job, plan = job_with(tmp_path, ["A rainy street", "A rainy street again", "An empty desert at noon"])
    lib = tmp_path / "lib"
    lib.mkdir()
    tone(lib / "rain_1.wav")
    out = amb.build(job, plan, LOOK, 12.0, library=lib, log=lambda m: None)
    with wave.open(str(out)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2) / 32767
        assert abs(w.getnframes() / w.getframerate() - 12.0) < 0.01
    rms = lambda a: 20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-9)
    assert -40 < rms(x[48000 * 2:48000 * 6]) < -34, "about 18 dB under the library's level"
    assert rms(x[48000 * 9:]) < -90, "no desert recording in this library: silence, not a wrong place"
    assert amb.build(job, plan, {"sound": {}}, 12.0, library=lib) is None, "only styles that ask"


def test_watched_ambience_names_map_to_library_kinds():
    assert amb.watched_kind(["distant birds"]) == "forest"
    assert amb.watched_kind(["police radio crackle"]) == "police"
    assert amb.watched_kind(["something unheard of"]) == ""
