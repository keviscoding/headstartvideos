"""A score per video: one cue per section in the channel's sound, the better take by measured shape, never resubmitted."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_score as score  # noqa: E402

LOOK = {"sound": {"music": True, "score": {"genres": ["dark synth drone"], "moods": ["tense", "ominous"],
                                           "instruments": ["sub bass", "piano"]}}}


@pytest.fixture
def kie(monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "test-kie-key")


def test_only_styles_that_ask_for_a_score_get_one(kie, monkeypatch):
    assert score.wanted(LOOK)["genres"] == ["dark synth drone"]
    assert score.wanted({"sound": {"music": False, "score": {}}}) == {}
    assert score.wanted({"sound": {}}) == {}
    monkeypatch.delenv("KIE_API_KEY")
    assert score.wanted(LOOK) == {}, "no Kie key, no score: the channel's usual music plays"


def test_each_cue_is_asked_for_in_the_channels_sound_with_its_job_in_the_story():
    cfg = LOOK["sound"]["score"]
    opening = score.style_text(cfg, "dramatic", "open")
    assert opening.startswith("dark synth drone; sub bass, piano; tense, dramatic")
    assert "cold open" in opening and "instrumental" in opening
    assert "steady underscore" in score.style_text(cfg, "quiet", "body")
    assert "soft laid-back beat" in score.style_text(dict(cfg, beat="soft"), "calm", "body")
    assert "beatless" in score.style_text(dict(cfg, beat="none"), "calm", "body")
    assert score.roles(4) == ["open", "body", "body", "end"] and score.roles(1) == ["open"]
    avoid = score.avoid_text({"genres": ["lofi hip-hop"], "instruments": ["acoustic guitar"]})
    assert "vocals" in avoid and "folk" in avoid and "acoustic guitar" not in avoid and "lo-fi" not in avoid


def test_the_better_take_is_chosen_by_its_measured_shape():
    rising = [-40, -38, -35, -30, -25, -20, -18, -15, -12]
    flat = [-20] * 9
    assert score.fit(rising, "open", 9) > score.fit(flat, "open", 9)
    assert score.fit(flat, "body", 9) > score.fit(rising, "body", 9)
    assert score.fit(flat, "body", 9) > score.fit(flat + [-20] * 30, "body", 9), "a take far from its length cuts badly"


def response(payload, code=200):
    return SimpleNamespace(status_code=code, content=b"x", json=lambda: payload)


def test_a_cue_is_submitted_once_and_collected_from_its_receipt(kie, tmp_path, monkeypatch):
    posts, polls = [], []

    def post(url, headers=None, json=None, timeout=None):
        posts.append(json)
        return response({"code": 200, "data": {"taskId": "task_1"}})

    def get(url, headers=None, params=None, timeout=None):
        polls.append(params)
        result = {"data": [{"audio_url": "https://x/a.mp3"}, {"audio_url": "https://x/b.mp3"}]}
        return response({"data": {"state": "success", "resultJson": __import__("json").dumps(result)}})
    monkeypatch.setattr(score.requests, "get", lambda url, timeout=None: SimpleNamespace(content=b"mp3" + url.encode()))
    dest = tmp_path / "01_score"
    takes = score.generate({"model": "V6", "style": "x"}, dest, post=post, get=get, sleep=lambda s: None,
                           log=lambda m: None)
    assert [t.name for t in takes] == ["01_score_take1.mp3", "01_score_take2.mp3"]
    assert posts[0]["model"] == score.MODEL and posts[0]["input"]["model"] == "V6"
    again = score.generate({"model": "V6", "style": "x"}, dest, post=lambda *a, **k: pytest.fail("resubmitted"),
                           get=get, sleep=lambda s: None, log=lambda m: None)
    assert again == takes


def test_an_uncertain_submission_is_never_sent_again(kie, tmp_path):
    def post(url, headers=None, json=None, timeout=None):
        return response({"code": 500, "msg": "server error"}, 500)
    with pytest.raises(RuntimeError, match="no task ID"):
        score.generate({"model": "V6"}, tmp_path / "01_score", post=post, get=None, sleep=lambda s: None,
                       log=lambda m: None)
    with pytest.raises(RuntimeError, match="uncertain"):
        score.generate({"model": "V6"}, tmp_path / "01_score", post=lambda *a, **k: pytest.fail("resubmitted"),
                       get=None, sleep=lambda s: None, log=lambda m: None)


def test_a_refused_cue_is_recorded_as_failed(kie, tmp_path):
    def post(url, headers=None, json=None, timeout=None):
        return response({"code": 402, "msg": "insufficient credits"}, 200)
    with pytest.raises(RuntimeError, match="402"):
        score.generate({"model": "V6"}, tmp_path / "01_score", post=post, get=None, sleep=lambda s: None,
                       log=lambda m: None)
    with pytest.raises(RuntimeError, match="failed before"):
        score.generate({"model": "V6"}, tmp_path / "01_score", post=post, get=None, sleep=lambda s: None,
                       log=lambda m: None)


def tone_file(path, seconds, rising):
    t = np.arange(int(seconds * 8000)) / 8000
    level = np.linspace(0.02, 0.5, len(t)) if rising else np.full(len(t), 0.2)
    x = (level * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", "8000", "-ac", "1", "-i", "-", str(path)],
                   input=x.tobytes(), check=True)
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Local FFmpeg is unavailable")
def test_a_whole_score_is_planned_cached_and_handed_to_studios_bed(kie, tmp_path, monkeypatch):
    calls, beds = [], []
    sections = [(0.0, 20.0, "dramatic", "Intro"), (20.0, 50.0, "quiet", "Part one"), (50.0, 70.0, "", "End")]

    def fake_generate(inputs, dest, log=print):
        calls.append(inputs)
        return [tone_file(dest.with_name(dest.stem + "_take1.mp3"), inputs["duration"], False),
                tone_file(dest.with_name(dest.stem + "_take2.mp3"), inputs["duration"], True)]
    fake_epidemic = SimpleNamespace(XFADE_S=1.5, _sections=lambda job, total: sections,
                                    _bed=lambda job, plan, total: beds.append(plan) or job / "sound" / "_bed.flac")
    monkeypatch.setitem(sys.modules, "epidemic_sound", fake_epidemic)
    bed = score.ensure_music(tmp_path, LOOK, 70.0, log=lambda m: None, generate_fn=fake_generate)
    assert bed == tmp_path / "sound" / "_bed.flac"
    assert [c["duration"] for c in calls] == [22, 32, 22]
    plan = beds[0]
    assert [p["role"] for p in plan] == ["open", "body", "end"]
    assert plan[0]["file"].endswith("_take2.mp3"), "the cold open takes the take that rises"
    assert plan[1]["file"].endswith("_take1.mp3"), "the underscore takes the even take"
    score.ensure_music(tmp_path, LOOK, 70.0, log=lambda m: None, generate_fn=lambda *a, **k: pytest.fail("regenerated"))
    assert len(beds) == 2


def test_a_failed_score_leaves_the_usual_music(kie, tmp_path, monkeypatch):
    fake_epidemic = SimpleNamespace(XFADE_S=1.5, _sections=lambda job, total: [(0.0, 30.0, "", "Intro")],
                                    _bed=lambda *a: pytest.fail("no bed without cues"))
    monkeypatch.setitem(sys.modules, "epidemic_sound", fake_epidemic)

    def broken(inputs, dest, log=print):
        raise RuntimeError("insufficient credits")
    assert score.ensure_music(tmp_path, LOOK, 30.0, log=lambda m: None, generate_fn=broken) is None


def test_install_scores_only_styles_that_ask(kie, monkeypatch):
    seen = []
    fake_epidemic = SimpleNamespace(ensure_music=lambda job, look, total, log=print: seen.append("original") or "own")
    monkeypatch.setitem(sys.modules, "epidemic_sound", fake_epidemic)
    monkeypatch.setattr(score, "ensure_music", lambda job, look, total, log=print: "scored")
    score.install()
    assert fake_epidemic.ensure_music("/job", LOOK, 10) == "scored"
    assert fake_epidemic.ensure_music("/job", {"sound": {}}, 10) == "own" and seen == ["original"]
    score.install()
    assert fake_epidemic.ensure_music._cr_score is True


def test_the_effects_library_fills_a_video_once_for_styles_that_ask(tmp_path, monkeypatch):
    import cr_sfx_library as lib
    library = tmp_path / "library"
    library.mkdir()
    for name in ("whoosh_1.wav", "whoosh_2.wav", "hit_1.wav", "notes.txt"):
        (library / name).write_bytes(b"RIFF")
    job = tmp_path / "job"
    assert lib.fill(job, log=lambda m: None, library=library) == 3
    assert sorted(p.name for p in (job / "sound" / "sfx").iterdir()) == ["hit_1.wav", "whoosh_1.wav", "whoosh_2.wav"]
    (library / "pop_1.wav").write_bytes(b"RIFF")
    assert lib.fill(job, log=lambda m: None, library=library) == 3, "a video keeps the kit it started with"


def test_the_library_never_replaces_a_creators_epidemic_kit(monkeypatch):
    import cr_sfx_library as lib
    calls = []
    fake = SimpleNamespace(available=lambda: False, settings=lambda look: {},
                           prepare_sfx=lambda style, look, job, log=print: calls.append("epidemic") or 0)
    monkeypatch.setitem(sys.modules, "epidemic_sound", fake)
    monkeypatch.setattr(lib, "fill", lambda job, log=print: calls.append("library") or 30)
    lib.install()
    asks = {"sound": {"library": True}}
    assert fake.settings(asks) == {"sfx": True, "library": True} and fake.settings({"sound": {}}) == {}
    assert fake.prepare_sfx("s", asks, "/job") == 30 and calls == ["library"]
    fake.available = lambda: True
    fake.prepare_sfx("s", asks, "/job")
    assert calls == ["library", "epidemic"]


def test_pictures_that_show_a_physical_action_get_their_sound_sparsely(tmp_path):
    import cr_sfx_library as lib
    suffix = " Flat vector style on warm parchment tones."
    frames = [{"prompt": "A guard kicks the door open, splinters flying" + suffix},
              {"prompt": "The narrator stares at a table" + suffix},
              {"prompt": "She hands over three gold coins"},
              {"prompt": "A clerk unrolls a long scroll"}]
    (tmp_path / "astro_scenes.json").write_text(json.dumps(frames))
    plan = [("photo", tmp_path / "images" / f"scene_{i:02d}.jpg", False, 3.0) for i in range(4)]
    sparse = lib.story_cues(tmp_path, plan, {"sound": {"density": "sparse"}, "still_suffix": suffix})
    assert sparse == [(0.15, "hit", -4.0), (9.15, "paper", -6.0)], "8 s apart at most, and nothing for staring"
    moderate = lib.story_cues(tmp_path, plan, {"sound": {"density": "moderate"}, "still_suffix": suffix})
    assert [c for _, c, _ in moderate] == ["hit", "cash"]
