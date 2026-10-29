"""The length chosen in Create is the length delivered: a customer chose 2 minutes in a cloned style, twice, and got
about one minute (JP, Discord, 2026-10-29). The script was planned at the style's learned 120 words a minute, the
narrator reads about 160, and a draft from 70 % of the target passed. cr_length plans at the narrator's real pace,
lengthens a script that still came out short, and measures each narration for the next video."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_length  # noqa: E402


def make_engine(tmp_path, *, style_info=None, wpm=None, draft_words=None, answers=()):
    """A stand-in for make_video with the same seams: generate_script reads STYLE_WPM, writes script.txt and calls
    the module's _factchecked by name; generate_voiceover writes audio.mp3."""
    engine = types.ModuleType("fake_make_video")
    engine.WPM = 140
    engine.STYLE_WPM = dict(wpm or {})
    engine.STYLE_INFO = dict(style_info or {})
    engine.STYLE_LANGUAGE = {}
    engine.SCRIPT_BODY_MODEL, engine.SCRIPT_PROVIDER = "opus", "claudecode"
    engine.logs, engine.asks, engine.targets, engine.checked = [], [], [], []
    engine.durations = {}
    queue = list(answers)
    engine.log = engine.logs.append
    engine._lang_prompt = lambda prompt, style: prompt
    engine._clean_script = lambda text: text.strip()
    engine._looks_like_refusal = lambda text: text.startswith("I can't")

    def claude(prompt, model, max_tokens=16000, provider=None):
        engine.asks.append(prompt)
        return queue.pop(0)
    engine.claude = claude

    def _factchecked(job, script, style, title, force=False):
        engine.checked.append(script)
        return script
    engine._factchecked = _factchecked

    def generate_script(title, minutes, job, force, style=""):
        target = int(minutes * float(engine.STYLE_WPM.get(style) or engine.WPM))
        engine.targets.append(target)
        script = " ".join(["word"] * (draft_words if draft_words is not None else target))
        (Path(job) / "script.txt").write_text(script, encoding="utf-8")
        return engine._factchecked(job, script, style, title, force)
    engine.generate_script = generate_script

    def generate_voiceover(script, job, force, avatar_min=0, style=""):
        if force or not (Path(job) / "audio.mp3").exists():        # the engine reuses a voice already made
            (Path(job) / "audio.mp3").write_bytes(b"x" * (len(script) + 1))
        return Path(job) / "audio.mp3", Path(job) / "subs.srt"
    engine.generate_voiceover = generate_voiceover
    engine._audio_dur = lambda path: engine.durations.get("audio", 0.0)
    return engine


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_length, "STORE", tmp_path / ".cr-voice-pace.json")
    monkeypatch.setattr(cr_length, "voice_key", lambda engine, job, style: "kie|gemini|Charon|en")
    monkeypatch.setattr(cr_length, "_language_factor", lambda engine, style, learned_style: 1.0)
    cr_length._asked.clear()


def job_dir(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    return job


CLONE = {"cr_clone_jp": {"cr_style_clone": {"visual_mode": "reference_only"}}}


def test_jps_two_minutes_in_a_cloned_style_are_planned_at_the_narrators_pace(tmp_path):
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120})
    cr_length.install(engine)
    engine.generate_script("What if", 2, job_dir(tmp_path), False, style="cr_clone_jp")
    assert engine.targets == [320]                      # 2 x 160, not 2 x 120 = 240 (read in about 90 s)
    assert engine.STYLE_WPM == {"cr_clone_jp": 120}     # the style's own table is left as it was
    assert any("about 320 words" in line for line in engine.logs)


def test_a_learned_style_measured_fast_is_not_written_long_either(tmp_path):
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 194})
    cr_length.install(engine)
    engine.generate_script("What if", 2, job_dir(tmp_path), False, style="cr_clone_jp")
    assert engine.targets == [320]


def test_a_built_in_recipe_keeps_its_own_pace_until_its_narrator_is_measured(tmp_path):
    engine = make_engine(tmp_path, wpm={"history": 144})
    cr_length.install(engine)
    engine.generate_script("Rome", 5, job_dir(tmp_path), False, style="history")
    assert engine.targets == [720]
    assert not any(line.startswith("Length:") for line in engine.logs)


def test_the_narrators_measured_pace_plans_every_style(tmp_path):
    cr_length.record("kie|gemini|Charon|en", 300, 120.0)    # 150 a minute
    cr_length.record("kie|gemini|Charon|en", 340, 120.0)    # 170 a minute
    assert cr_length.measured("kie|gemini|Charon|en") == (160.0, 2)
    engine = make_engine(tmp_path, wpm={"documentary": 120})
    cr_length.install(engine)
    engine.generate_script("Ships", 3, job_dir(tmp_path), False, style="documentary")
    assert engine.targets == [480]
    assert engine.STYLE_WPM == {"documentary": 120}


def test_one_measurement_is_not_enough_and_odd_ones_are_not_kept(tmp_path):
    assert cr_length.record("k", 117, 63.0) is not None      # 111 a minute: a real (slow) reading, kept
    assert cr_length.measured("k") == (None, 1)
    assert cr_length.record("k", 30, 10.0) is None           # too short to measure a pace
    assert cr_length.record("k", 500, 60.0) is None          # 500 a minute is no reading pace
    assert cr_length.measured("k") == (None, 1)


def test_another_language_scales_the_pace(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_length, "_language_factor", lambda engine, style, learned_style: 0.9)
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120})
    cr_length.install(engine)
    engine.generate_script("Qué pasaría", 2, job_dir(tmp_path), False, style="cr_clone_jp")
    assert engine.targets == [288]


def test_a_short_draft_is_lengthened_before_the_fact_check(tmp_path):
    longer = " ".join(["more"] * 300)
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120}, draft_words=170, answers=[longer])
    cr_length.install(engine)
    job = job_dir(tmp_path)
    out = engine.generate_script("What if", 2, job, False, style="cr_clone_jp")
    assert out == longer and engine.checked == [longer]      # the fact-check reads the added lines too
    assert (job / "script.txt").read_text(encoding="utf-8") == longer
    assert "about 320 spoken words" in engine.asks[0] and "it is now 170 words" in engine.asks[0]


def test_a_script_near_its_length_is_left_alone(tmp_path):
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120}, draft_words=290)
    cr_length.install(engine)
    engine.generate_script("What if", 2, job_dir(tmp_path), False, style="cr_clone_jp")
    assert engine.asks == []


@pytest.mark.parametrize("mark", ["script.pasted", "audio.mp3"])
def test_a_pasted_script_or_one_already_voiced_is_never_lengthened(tmp_path, mark):
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120}, draft_words=100)
    cr_length.install(engine)
    job = job_dir(tmp_path)
    (job / mark).write_text("x", encoding="utf-8")
    engine.generate_script("What if", 2, job, False, style="cr_clone_jp")
    assert engine.asks == []


def test_a_rewrite_that_drops_chapters_runs_long_or_refuses_is_not_kept(tmp_path):
    draft = "## Start | calm\n" + " ".join(["word"] * 150)
    job = job_dir(tmp_path)
    engine = make_engine(tmp_path, answers=[" ".join(["w"] * 300),                 # chapter line lost
                                            "## Start | calm\n" + " ".join(["w"] * 600)])   # far too long
    out = cr_length.lengthen(engine, job, draft, "x", 320)
    assert out == draft and len(engine.asks) == 2
    engine = make_engine(tmp_path, answers=["I can't help with that.", "## Start | calm\n" + " ".join(["w"] * 310)])
    out = cr_length.lengthen(engine, job, draft, "x", 320)
    assert cr_length.spoken_words(out) == 310 and out.startswith("## Start")


def test_a_claude_error_keeps_the_script_as_written(tmp_path):
    engine = make_engine(tmp_path)

    def broken(*a, **k):
        raise RuntimeError("usage limit")
    engine.claude = broken
    draft = " ".join(["word"] * 100)
    assert cr_length.lengthen(engine, job_dir(tmp_path), draft, "x", 320) == draft
    assert any("kept the script as written" in line for line in engine.logs)


def test_the_narration_is_measured_and_a_short_one_is_said(tmp_path):
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120}, draft_words=290)
    cr_length.install(engine)
    job = job_dir(tmp_path)
    script = engine.generate_script("What if", 2, job, False, style="cr_clone_jp")
    engine.durations["audio"] = 87.0                         # 290 words in 87 s: 200 a minute
    engine.generate_voiceover(script, job, False, 0, "cr_clone_jp")
    assert cr_length._load()["kie|gemini|Charon|en"] == [200.0]
    assert any("1:27 for a 2-minute video" in line for line in engine.logs)
    engine.generate_voiceover(script, job, False, 0, "cr_clone_jp")   # cached voice (same file): not counted twice
    engine.logs.clear()
    assert cr_length._load()["kie|gemini|Charon|en"] == [200.0]


def test_your_own_recording_is_not_measured(tmp_path):
    engine = make_engine(tmp_path)
    job = job_dir(tmp_path)
    (job / "voice.own").write_text("", encoding="utf-8")
    (job / "audio.mp3").write_bytes(b"x")
    engine.durations["audio"] = 120.0
    cr_length.after_voice(engine, job, " ".join(["w"] * 300), "x", None)
    assert cr_length._load() == {}


def test_spoken_words_skip_chapter_and_clip_lines():
    assert cr_length.spoken_words("## One | calm\nTwo words.\n[[clip: interview 0:10]]\nthree more here") == 5


def test_install_wraps_once(tmp_path):
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120})
    cr_length.install(engine)
    wrapped = engine.generate_script
    cr_length.install(engine)
    assert engine.generate_script is wrapped


def test_the_narrator_key_follows_the_video_voice_choice(tmp_path, monkeypatch):
    monkeypatch.undo()                                       # the real voice_key, with a stand-in cr_media
    media = types.SimpleNamespace(
        active=lambda task: True, STYLE_VOICE_PIN="cr-style-voice.json",
        job_voice=lambda job: {"provider": "kie", "model": "gemini-tts", "voice": "Puck"},
        _voice_in=lambda path: {}, style_voice=lambda style, info: {},
        profile=lambda task: {"provider": "kie", "model": "gemini-tts", "voice": "Kore"},
        narration_model=lambda provider, model: {"id": model, "default_voice": "Kore"})
    monkeypatch.setitem(sys.modules, "cr_media", media)
    engine = make_engine(tmp_path)
    assert cr_length.voice_key(engine, tmp_path, "s").startswith("kie|gemini-tts|Puck|")
    media.job_voice = lambda job: {}
    assert cr_length.voice_key(engine, tmp_path, "s").startswith("kie|gemini-tts|Kore|")
    media.active = lambda task: False
    assert cr_length.voice_key(engine, tmp_path, "s").startswith("engine|s|")
    media.active = lambda task: True
    media.job_voice = lambda job: {"provider": "original"}             # the engine's own voice, chosen for this video
    assert cr_length.voice_key(engine, tmp_path, "s").startswith("engine|s|")
    media.job_voice = lambda job: {"provider": "own", "file": "take.mp3"}
    assert cr_length.voice_key(engine, tmp_path, "s") == ""
    media.active = lambda task: False
    media.job_voice = lambda job: {"provider": "kie", "model": "gemini-tts", "voice": "Puck"}
    assert cr_length.voice_key(engine, tmp_path, "s").startswith("kie|gemini-tts|Puck|")   # read by Kie all the same


def test_studio_installs_it_around_the_studio_voiceover():
    """cr_media's voiceover narrates through Studio's accounts without calling the engine's voiceover: cr_length must
    wrap it from outside, or no Kie narration is ever measured."""
    source = (Path(__file__).parents[1] / "local_runtime" / "cr_media.py").read_text(encoding="utf-8")
    assert "cr_length.install(engine)" in source
    assert source.index("cr_length.install(engine)") > source.index("engine.generate_voiceover = voiceover")


def test_a_voiceover_that_never_calls_the_engines_is_still_measured(tmp_path):
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120}, draft_words=290)
    engine_voice = engine.generate_voiceover

    def studio_voice(script, job, force=False, avatar_min=0, style=""):     # cr_media.make_voiceover's shape
        (Path(job) / "audio.mp3").write_bytes(b"kie")
        return Path(job) / "audio.mp3", Path(job) / "subs.srt"
    engine.generate_voiceover = studio_voice
    cr_length.install(engine)
    job = job_dir(tmp_path)
    script = engine.generate_script("What if", 2, job, False, style="cr_clone_jp")
    engine.durations["audio"] = 116.0
    engine.generate_voiceover(script, job, False, 0, "cr_clone_jp")
    assert cr_length._load()["kie|gemini|Charon|en"] == [150.0]
    assert engine_voice is not engine.generate_voiceover


def test_a_script_an_earlier_run_wrote_is_not_lengthened_on_resume(tmp_path):
    """A Resume reuses script.txt, which split_chapters already turned into narration with its chapters placed by their
    paragraphs' first words: rewriting it there would lose the chapters (and the script the creator saw)."""
    engine = make_engine(tmp_path, style_info=CLONE, wpm={"cr_clone_jp": 120}, answers=[" ".join(["more"] * 300)])
    job = job_dir(tmp_path)
    cached = " ".join(["word"] * 170)
    (job / "script.txt").write_text(cached, encoding="utf-8")

    def resumed(title, minutes, job, force, style=""):                     # the engine's cached path
        return engine._factchecked(job, (Path(job) / "script.txt").read_text(encoding="utf-8"), style, title, force)
    engine.generate_script = resumed
    cr_length.install(engine)
    assert engine.generate_script("What if", 2, job, False, style="cr_clone_jp") == cached
    assert engine.asks == []


def test_mood_lines_count_as_markers_and_must_survive_in_order(tmp_path):
    assert cr_length.spoken_words("[[mood: tense]]\nfour words right here") == 4
    draft = "## One | calm\n" + " ".join(["a"] * 100) + "\n[[mood: tense]]\n" + " ".join(["b"] * 50)
    moved = "[[mood: tense]]\n## One | calm\n" + " ".join(["a"] * 300)                    # order changed
    dropped = "## One | calm\n" + " ".join(["a"] * 300)                                      # mood line lost
    kept = "## One  | calm\n" + " ".join(["a"] * 200) + "\n[[mood: tense]]\n" + " ".join(["b"] * 100)
    engine = make_engine(tmp_path, answers=[moved, dropped])
    assert cr_length.lengthen(engine, job_dir(tmp_path), draft, "x", 320) == draft
    engine = make_engine(tmp_path, answers=[kept])
    assert cr_length.lengthen(engine, tmp_path / "job", draft, "x", 320) == kept


def test_a_script_without_spaces_between_words_is_not_lengthened(tmp_path):
    engine = make_engine(tmp_path)
    draft = "。".join(["これは日本語の文章です"] * 40)
    assert cr_length.lengthen(engine, job_dir(tmp_path), draft, "x", 320) == draft
    assert engine.asks == []


def test_your_own_recording_chosen_after_an_ai_voice_stopped_is_not_measured(tmp_path):
    engine = make_engine(tmp_path)
    job = job_dir(tmp_path)
    (job / "cr-voice.json").write_text('{"provider": "own", "file": "take.mp3"}', encoding="utf-8")
    (job / "audio.mp3").write_bytes(b"x")
    engine.durations["audio"] = 120.0
    cr_length.after_voice(engine, job, " ".join(["w"] * 300), "x", None)
    assert cr_length._load() == {}


def test_a_style_that_speeds_its_voice_up_is_measured_and_planned_at_the_narrators_own_pace(tmp_path):
    fast = {"cr_clone_jp": {"cr_style_clone": {"visual_mode": "reference_only"}, "voice": {"speed": 1.25}}}
    engine = make_engine(tmp_path, style_info=fast, wpm={"cr_clone_jp": 120})
    job = job_dir(tmp_path)
    (job / "audio.mp3").write_bytes(b"x")
    engine.durations["audio"] = 96.0                       # 320 words in 96 s after a 1.25x speed-up: 200 heard
    cr_length.after_voice(engine, job, " ".join(["w"] * 320), "cr_clone_jp", None)
    assert cr_length._load()["kie|gemini|Charon|en"] == [160.0]  # the narrator reads 160 at its own speed
    cr_length.install(engine)
    other = tmp_path / "other"
    other.mkdir()
    engine.generate_script("What if", 2, other, False, style="cr_clone_jp")
    assert engine.targets == [400]                          # 2 x 160 x 1.25: the sped-up voice reads more a minute
    cr_length.record("kie|gemini|Charon|en", 340, 120.0)    # measured twice now: 160 and 170, median 165
    again = tmp_path / "again"
    again.mkdir()
    engine.generate_script("What if", 2, again, False, style="cr_clone_jp")
    assert engine.targets[-1] == int(2 * 165 * 1.25)


def test_a_damaged_pace_file_is_read_as_no_measurement(tmp_path):
    cr_length.STORE.write_text('{"k": 5, "j": {"a": 1}, "i": [150, true, "x", 170]}', encoding="utf-8")
    assert cr_length.measured("k") == (None, 0)
    assert cr_length.measured("j") == (None, 0)
    assert cr_length.measured("i") == (160.0, 2)
    assert cr_length.record("k", 300, 120.0) == 150.0
    assert cr_length._load()["k"] == [150.0]
