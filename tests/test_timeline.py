"""A clone of a channel with no narrator makes music-only videos timed to a timeline (cr_timeline, CityEons 2026-11-02)."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
sys.path.insert(0, str(Path(__file__).parent))
import cr_timeline as tl  # noqa: E402

RAW = {"place": {"name": "Manila", "country": "Philippines", "lat": 14.59, "lon": 120.98, "span_km": 20},
       "years": {"from": "1500", "to": "2026"},
       "eras": [{"at": 0, "mood": "curious", "title": "Before the Spanish"},
                {"at": 30, "mood": "tense", "title": "Intramuros"},
                {"at": 35, "mood": "dramatic", "title": "too close: dropped"}],
       "beats": [{"at": 12, "year": "1571", "headline": "INTRAMUROS", "text": "Legazpi founds the walled city.",
                  "state": "Spanish Empire", "labels": ["Intramuros", "Pasig River"], "camera": "low over the river"},
                 {"at": 0, "year": "MANILA TODAY", "headline": "500 YEARS", "text": "c. 14 million people."},
                 {"at": 13.5, "year": "1572", "headline": "too soon after 1571: carried by it"},
                 {"at": 40, "year": "1945", "headline": "THE BATTLE OF MANILA", "text": "A month of fighting."},
                 {"at": 58.5, "year": "2026", "headline": "a stub at the end"}]}


def engine(style_audio="music_only", replies=()):
    replies = list(replies)
    log, steps = [], []
    eng = SimpleNamespace(
        STYLE_INFO={"cr_clone_city": {"look": {"audio": style_audio, "visuals": "cr_animated3d",
                                               "sound": {"score": {"genres": ["cinematic orchestral"]}}}},
                    "history": {"look": {}}},
        log=log.append, step=steps.append, _extra_block=lambda: "",
        claude=lambda prompt, model, max_tokens=0, provider="": (replies.pop(0) if replies else json.dumps(RAW)),
        _audio_dur=lambda p: 60.0)
    eng.logged, eng.steps = log, steps
    return eng


def test_the_timeline_covers_the_whole_video_in_order_with_no_gaps_or_stubs():
    t = tl.clean(RAW, 60)
    assert t["seconds"] == 60 and t["place"]["name"] == "Manila"
    beats = t["beats"]
    assert [b["year"] for b in beats] == ["MANILA TODAY", "1571", "1945"], "too close and the end stub are dropped"
    assert beats[0]["at"] == 0 and beats[-1]["until"] == 60
    assert all(a["until"] == b["at"] for a, b in zip(beats, beats[1:])), "no gaps, no overlaps"
    assert [e["title"] for e in t["eras"]] == ["Before the Spanish", "Intramuros"]
    assert t["eras"][-1]["until"] == 60


def test_a_long_era_is_split_so_every_music_cue_fits_suno():
    t = tl.clean({"beats": [{"at": 0, "year": "1600"}], "eras": [{"at": 0, "mood": "quiet"}]}, 600)
    assert len(t["eras"]) == 2 and all(e["until"] - e["at"] <= tl.MAX_ERA_S for e in t["eras"])
    with pytest.raises(ValueError):
        tl.clean({"beats": []}, 60)


def test_each_beat_reads_as_one_line_with_what_the_screen_shows():
    said = tl.line({"year": "1571", "headline": "INTRAMUROS", "text": "Legazpi founds the walled city.",
                    "state": "Spanish Empire", "labels": ["Intramuros", "Pasig River"], "camera": "low over the river"})
    assert said.startswith("1571 | INTRAMUROS | Legazpi founds the walled city.")
    assert "state: Spanish Empire" in said and "labels: Intramuros, Pasig River" in said and "camera: low" in said


def test_the_script_step_writes_the_timeline_once_and_a_resume_reuses_it(tmp_path):
    eng = engine()
    text = tl.write(eng, "Watch MANILA Grow for 500 Years", 1, tmp_path, "cr_clone_city")
    saved = json.loads((tmp_path / "timeline.json").read_text())
    assert saved["seconds"] == 60 and len(saved["beats"]) == 3
    assert text == (tmp_path / "script.txt").read_text() and "THE BATTLE OF MANILA" in text
    eng.claude = lambda *a, **k: pytest.fail("a resume never asks Claude again")
    assert tl.write(eng, "Watch MANILA Grow for 500 Years", 1, tmp_path, "cr_clone_city") == text
    assert any("Timeline" in s for s in eng.steps)


def test_the_beats_stand_in_for_the_narration_lines(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps(tl.clean(RAW, 60)))
    rows = tl.lines(tmp_path)
    assert [r[0] for r in rows] == [0.0, 12.0, 40.0] and rows[-1][1] == 60
    a, b, text, words = rows[1]
    assert text.startswith("1571 | INTRAMUROS") and words[0] == ("1571", 12.0) and all(a <= t < b for _, t in words)


def test_the_cues_and_their_word_clock_never_send_music_to_speech_recognition(tmp_path):
    timeline = tl.clean(RAW, 60)
    srt = tmp_path / "subs.srt"
    srt.write_text(tl.subtitles(timeline))
    text = srt.read_text()
    assert text.startswith("1\n00:00:00,000 --> 00:00:12,000\nMANILA TODAY 500 YEARS\n")
    assert "00:00:40,000 --> 00:01:00,000\n1945 THE BATTLE OF MANILA" in text
    clock = tl.word_clock(srt, timeline)
    assert clock["srt"] == hashlib.sha1(srt.read_bytes()).hexdigest()[:16], "the engine's _word_clock cache key"
    assert len(clock["cues"]["1"]) == 2 and clock["cues"]["1"][0][0] == 12.0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg cuts the score to length")
def test_the_voice_step_is_the_score_cut_to_the_timeline(tmp_path, monkeypatch):
    import cr_score
    (tmp_path / "timeline.json").write_text(json.dumps(tl.clean(RAW, 60)))
    bed = tmp_path / "sound" / "_bed.flac"
    bed.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=50",
                    str(bed)], check=True)
    asked = {}

    def ensure_music(job, look, total, log=print, *, sections=None, generate_fn=None):
        asked.update(look=look, total=total, sections=sections)
        return bed
    monkeypatch.setattr(cr_score, "ensure_music", ensure_music)
    eng = engine()
    eng._audio_dur = lambda p: float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
        capture_output=True, text=True).stdout.strip() or 0)
    mp3, srt = tl.voiceover(eng, tmp_path, "cr_clone_city")
    assert asked["total"] == 60 and [s[2] for s in asked["sections"]] == ["curious", "tense"]
    assert asked["look"]["sound"]["score"]["lead"] is True, "the music carries the video alone"
    assert abs(eng._audio_dur(mp3) - 60) < 0.3, "padded to the timeline's length"
    assert (tmp_path / "words.json").is_file() and srt.read_text().count("-->") == 3
    monkeypatch.setattr(cr_score, "ensure_music", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="Kie.ai"):
        tl.voiceover(eng, tmp_path, "cr_clone_city", force=True)


def test_only_a_music_only_style_takes_the_timeline_path(tmp_path, monkeypatch):
    eng = engine()
    called = []
    eng.generate_script = lambda title, minutes, job, force, style="": called.append(("script", style)) or "narrated"
    eng.generate_voiceover = lambda script, job, force=False, *a, **k: called.append(("voice", k.get("style"))) or ("m", "s")
    tl.install(eng)
    tl.install(eng)                                              # once only
    assert eng.generate_script("A story", 1, tmp_path, False, style="history") == "narrated"
    assert eng.generate_voiceover("narrated", tmp_path, False, 0, style="history") == ("m", "s")
    assert called == [("script", "history"), ("voice", "history")]
    monkeypatch.setattr(tl, "voiceover", lambda engine, job, style, force=False: ("score.mp3", "beats.srt"))
    assert "MANILA" in eng.generate_script("Watch MANILA Grow", 1, tmp_path, False, style="cr_clone_city")
    assert eng.generate_voiceover("x", tmp_path, False, 0, style="cr_clone_city") == ("score.mp3", "beats.srt")
    assert len(called) == 2, "the narrated path was never touched"


def test_a_clone_of_a_channel_with_no_narrator_is_music_only_unless_the_creator_adds_one():
    from test_style_map import STYLES, axis, profile
    import cr_style_map as cm
    import cr_style_profile as prof
    quiet = profile({"generated": 0.9, "graphic": 0.1}, format__narration=axis("none"),
                    sound__music=axis("orchestral", level="score", bed_in_pauses_db=-12, moods=["epic"],
                                      genres=["cinematic orchestral"]))
    asked = [q for q in prof.questions(quiet) if q["id"] == "voice"]
    assert asked and asked[0]["options"][0] == prof.MUSIC_ONLY
    plan = cm.plan_style(quiet, STYLES)
    styled = cm.apply(STYLES[plan["base"]], plan)
    assert styled["look"]["audio"] == "music_only"
    assert styled["look"]["sound"]["score"]["lead"] is True and styled["look"]["sound"]["music"] is True
    narrated = cm.plan_style(quiet, STYLES, answers={"voice": prof.ADD_NARRATOR})
    assert "audio" not in (cm.apply(STYLES[narrated["base"]], narrated).get("look") or {})
    talking = profile({"generated": 0.9}, format__narration=axis("voiceover"))
    assert not [q for q in prof.questions(talking) if q["id"] == "voice"]


def test_a_music_only_quote_pays_for_music_not_a_voice():
    import cr_studio_tools as tools
    cat = {"prices": {"voice": {"per_min": 0.05}}}
    coded = {"coded_media": {"plates_usd": 0.12, "music_usd_per_piece": 0.12, "piece_seconds": 35}}
    narrated = tools.media_estimate(cat, {"name": "cr_clone_story", **coded}, 8, {})
    music = tools.media_estimate(cat, {"name": "cr_clone_city", "audio": "music_only", **coded}, 8, {})
    assert music["voice"]["provider"] == "none"
    assert music["per_video_usd"] == round(0.12 + 4 * 0.12, 2), "a scored section about every 2.5 minutes, no voice"
    assert narrated["per_video_usd"] > music["per_video_usd"]


def test_the_director_reads_beats_not_narration_and_the_film_carries_the_clock(tmp_path, monkeypatch):
    import cr_animated2d as a2d
    monkeypatch.setattr(a2d, "_prompt", lambda name, home=None: "DIRECTOR [BEATS]")
    monkeypatch.setattr(a2d, "earlier", lambda name, home=None: [])
    monkeypatch.setattr(a2d, "clean_plan", lambda plan, *a, **k: {"duration": 10, "shots": [], "actions": [], "fx": []})
    asked = []
    eng = SimpleNamespace(step=lambda s: None, _extra_block=lambda: "",
                          claude=lambda prompt, model, max_tokens=0, provider="": asked.append(prompt) or "{}")
    part = (0.0, 10.0, [(0.0, 10.0, "1571 | INTRAMUROS", [("1571", 0.0)])])
    casting = {"cast": {}, "sets": {}}
    a2d.direct(eng, tmp_path, casting, {}, part, 0, "", "A story", {})
    assert asked[-1].startswith("DIRECTOR"), "a narrated video's director is as before"
    (tmp_path / "timeline.json").write_text(json.dumps(tl.clean(RAW, 60)))
    a2d.direct(eng, tmp_path, casting, {}, part, 1, "", "Watch MANILA Grow", {})
    assert asked[-1].startswith("THIS CHANNEL HAS NO NARRATOR") and asked[-1].endswith("DIRECTOR " + a2d.beats_text(part[2]))
    plain = a2d.film_spec({"duration": 10, "shots": [], "actions": [], "fx": []}, casting, {}, {})
    assert "clock" not in plain and "timeline" not in plain
    timed = a2d.film_spec({"duration": 10, "shots": [], "actions": [], "fx": []}, casting, {}, {},
                          extra={"clock": {"t0": 120.0, "total": 540.0}, "timeline": {"beats": []}})
    assert timed["clock"] == {"t0": 120.0, "total": 540.0} and timed["timeline"] == {"beats": []}


def test_a_timeline_video_is_filmed_from_the_air_with_no_crowds():
    """The Manila test (2026-11-02): the director's close shots and crowds put pill-shaped people before every place."""
    import cr_animated2d as a2d
    plan = {"duration": 10, "actions": [], "fx": [],
            "shots": [{"start": 0, "set": "spanish_manila", "framing": "close:fort", "move": "push", "crowd": 40},
                      {"start": 4, "set": "spanish_manila", "framing": "wide", "move": "pan"},
                      {"start": 6, "set": "spanish_manila", "framing": "low:city_hall", "move": "push"},
                      {"start": 8, "set": "spanish_manila", "framing": "over:fort", "move": "static"}],
            "sets": {"bay_battle": {"prompt": "the bay", "crowd": 30}}}
    out = a2d.aerial(plan)
    assert [s["framing"] for s in out["shots"]] == ["wide"] * 4 and out["shots"][0]["move"] == "push"
    assert out["shots"][0]["crowd"] == 0 and out["sets"]["bay_battle"]["crowd"] == 0
    casting = {"cast": {}, "sets": {"spanish_manila": {"prompt": "walled city", "crowd": 25}}}
    film = a2d.film_spec(out, casting, {}, {}, extra={"timeline": {"beats": []}})
    assert "crowd" not in film["sets"]["spanish_manila"]
    assert a2d.film_spec(out, casting, {}, {})["sets"]["spanish_manila"]["crowd"] == 25, "a narrated video is as before"


def test_every_place_in_a_mapped_timeline_video_is_the_map_seen_from_the_air():
    """The Manila re-test (2026-11-03): a place described as "the mouth of the Pasig River" was drawn as a separate
    room (a grey wall, a tree and a ship in close-up) by a renderer that draws only city/aerial places on the map."""
    import cr_animated2d as a2d
    casting = {"cast": {}, "sets": {"river_mouth": {"prompt": "the mouth of the Pasig River, a wooden fort", "inside": True},
                                    "skyline": {"prompt": "Aerial view of Makati at dusk"}}}
    plan = {"duration": 10, "shots": [], "actions": [], "fx": []}
    film = a2d.film_spec(plan, casting, {}, {}, extra={"timeline": {"beats": []}, "map": {"buildings": []}})
    river = film["sets"]["river_mouth"]
    assert river["aerial"] is True and river["inside"] is False
    assert river["prompt"].startswith("Aerial view over the city's map: ") and "Pasig River" in river["prompt"]
    assert film["sets"]["skyline"]["prompt"] == "Aerial view of Makati at dusk", "not prefixed twice"
    no_map = a2d.film_spec(plan, casting, {}, {}, extra={"timeline": {"beats": []}})
    assert "aerial" not in no_map["sets"]["river_mouth"], "a music-only channel without a map keeps its places"
    assert "aerial" not in a2d.film_spec(plan, casting, {}, {})["sets"]["river_mouth"]
