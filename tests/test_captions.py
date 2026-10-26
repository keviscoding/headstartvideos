"""Captions a creator asked for are burned in, or the video says plainly why not (local_runtime/cr_captions.py).

Customer ALEX (Studio 104): "Burn captions into the video" was on, the log said "subtitles: 0 lines + 0 kinetic" and
the finished video had no captions. The pinned engine's _build_burn_ass (byte-identical in the source zip and the
installed Studio) drops every caption when the style says captions.off, and its own-ElevenLabs voice writes an empty
subs.srt when ElevenLabs sends no alignment. The fake engine below follows the engine's own rules for both."""
import json
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_captions
import cr_media

SRT = "".join(f"{i + 1}\n00:00:{10 + i * 3:02d},000 --> 00:00:{12 + i * 3:02d},000\nLine {i + 1} of the story\n\n"
              for i in range(5))


def fake_engine(lines):
    """make_video's caption rules: captions.off empties the cues; muted cues are dropped; one Dialogue per cue."""
    engine = SimpleNamespace(STYLE_CAPTIONS={}, log=lines.append, muxed=[])

    def build(srt, out_ass, mute_ranges=None, center=False, style="", captions=True):
        cap = engine.STYLE_CAPTIONS.get(style) or {}
        if cap.get("off"):
            captions = False
        cues = re.findall(r"(\d\d):(\d\d):(\d\d),\d+ -->", Path(srt).read_text(encoding="utf-8")) if captions else []
        kept, dropped, events = 0, 0, []
        for h, m, s in cues:
            t = int(h) * 3600 + int(m) * 60 + int(s)
            if any(a <= t <= b for a, b in mute_ranges or []):
                dropped += 1
                continue
            events.append(f"Dialogue: 0,0:00:{t:02d}.00,0:00:{t + 2:02d}.00,Def,,0,0,0,,line")
            kept += 1
        events.append("Dialogue: 0,0:00:01.00,0:00:02.00,OvlRule,,0,0,0,,a label")
        Path(out_ass).write_text("[Events]\n" + "\n".join(events) + "\n", encoding="utf-8")
        engine.log(f"subtitles: {kept} lines + 0 kinetic" + (f", {dropped} suppressed under graphics scenes" if dropped else ""))
        return Path(out_ass)

    def final(job, mp3, srt, burn_subs, add_qr=False, **kwargs):
        engine.muxed.append(burn_subs)
        if burn_subs and Path(srt).exists():
            engine._build_burn_ass(srt, Path(job) / "subs_burn.ass", [], False, (Path(job) / "style.txt").read_text()
                                   if (Path(job) / "style.txt").exists() else "", captions=burn_subs)
        return Path(job) / "final.mp4"

    words = [SimpleNamespace(word=w, start=0.5 * i, end=0.5 * i + 0.4) for i, w in enumerate(
        "The vault opened at midnight. Nobody saw it.".split())]
    engine._build_burn_ass, engine._final_mux = build, final
    engine._get_whisper = lambda: SimpleNamespace(transcribe=lambda *a, **k: ([SimpleNamespace(words=words)], None))
    engine._srt_ts = lambda t: f"00:00:{int(t):02d},{int(round((t % 1) * 1000)):03d}"
    engine._script_words_into_srt = lambda srt, text: 0
    cr_captions.install(engine)
    return engine


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_media, "narration_audio", lambda path: [0.0])
    folder = tmp_path / "video"
    folder.mkdir()
    (folder / "audio.mp3").write_bytes(b"narration")
    (folder / "script.txt").write_text("The vault opened at midnight. Nobody saw it.", encoding="utf-8")
    return folder


def asked(job, on=True):
    (job / "run_spec.json").write_text(json.dumps({"burn_subs": on, **({"cr_captions": "on"} if on else {})}))


def captions_lines(lines):
    return [line for line in lines if line.startswith("Captions:")]


def test_a_style_without_captions_gets_them_when_the_creator_asks(job):
    """Alex's video: a cloned style with captions.off and Create's switch on: 'subtitles: 0 lines'. Asked: burned in."""
    lines = []
    engine = fake_engine(lines)
    engine.STYLE_CAPTIONS["cr_clone_wargame"] = {"off": True, "size": 70}
    (job / "subs.srt").write_text(SRT, encoding="utf-8")
    (job / "style.txt").write_text("cr_clone_wargame")
    asked(job)
    engine._final_mux(job, job / "audio.mp3", job / "subs.srt", True)
    assert "subtitles: 5 lines + 0 kinetic" in lines
    assert captions_lines(lines) == ["Captions: on as asked (5 lines), though this style normally has none."]
    assert engine.STYLE_CAPTIONS["cr_clone_wargame"] == {"off": True, "size": 70}, "the style itself is unchanged"
    assert json.loads((job / "cr-captions.json").read_text()) == {"asked": True, "lines": 5}


def test_a_style_without_captions_keeps_its_design_and_says_so_when_not_asked(job):
    lines = []
    engine = fake_engine(lines)
    engine.STYLE_CAPTIONS["history"] = {"off": True}
    (job / "subs.srt").write_text(SRT, encoding="utf-8")
    (job / "style.txt").write_text("history")
    asked(job, on=False)
    engine._final_mux(job, job / "audio.mp3", job / "subs.srt", True)      # an older front door's default
    assert "subtitles: 0 lines + 0 kinetic" in lines
    assert captions_lines(lines)[0].startswith("Captions: off. This style has none, like its channel.")


def test_a_narration_with_no_word_timings_is_timed_on_this_computer(job):
    """Own ElevenLabs without an alignment wrote an empty subs.srt: 'subtitles: 0 lines', silently."""
    lines = []
    engine = fake_engine(lines)
    (job / "subs.srt").write_text("", encoding="utf-8")
    engine._final_mux(job, job / "mix.mp3", job / "subs.srt", True)
    assert cr_captions.cue_count(job / "subs.srt") == 2                    # two sentences, timed by Whisper
    assert "Captions: the narration came back without word timings; timing it on this computer." in lines
    assert "subtitles: 2 lines + 0 kinetic" in lines and engine.muxed == [True]


def test_the_voice_step_times_an_empty_subtitle_file_at_once(job):
    lines = []
    engine = fake_engine(lines)
    (job / "subs.srt").write_text("", encoding="utf-8")
    cr_captions.after_voice(engine, "The vault opened at midnight. Nobody saw it.", (job / "audio.mp3", job / "subs.srt"))
    assert cr_captions.cue_count(job / "subs.srt") == 2
    good = (job / "subs.srt").read_text()
    cr_captions.after_voice(engine, "anything", (job / "audio.mp3", job / "subs.srt"))
    assert (job / "subs.srt").read_text() == good, "a timed file is left alone"


def test_words_that_cannot_be_timed_are_said_plainly(job):
    lines = []
    engine = fake_engine(lines)
    engine._get_whisper = lambda: SimpleNamespace(transcribe=lambda *a, **k: ([], None))
    (job / "subs.srt").write_text("", encoding="utf-8")
    engine._final_mux(job, job / "audio.mp3", job / "subs.srt", True)
    said = captions_lines(lines)
    assert said[-1] == ("Captions: none in this video: its narration has no timed words. The rest of the video is "
                        "finished.")
    assert json.loads((job / "cr-captions.json").read_text())["lines"] == 0


def test_captions_all_under_graphics_scenes_are_said_plainly(job):
    lines = []
    engine = fake_engine(lines)
    (job / "subs.srt").write_text(SRT, encoding="utf-8")
    engine._build_burn_ass(job / "subs.srt", job / "subs_burn.ass", [(0, 999)], False, "", captions=True)
    assert captions_lines(lines) == ["Captions: none in this video: every line fell on a graphics scene, where "
                                     "captions are not drawn. The rest of the video is finished."]


def test_no_subtitle_file_at_all_is_said_plainly(job):
    lines = []
    engine = fake_engine(lines)
    engine._get_whisper = lambda: SimpleNamespace(transcribe=lambda *a, **k: ([], None))
    (job / "audio.mp3").unlink()
    engine._final_mux(job, job / "mix.mp3", job / "subs.srt", True)
    assert captions_lines(lines)[-1].startswith("Captions: none in this video: its narration has no subtitle file.")


def test_a_recipe_that_switches_captions_off_itself_burns_them_when_asked(job):
    """Folio and Vox pass burn_subs=False to the final mux by their own settings; an asked video gets them."""
    lines = []
    engine = fake_engine(lines)
    (job / "subs.srt").write_text(SRT, encoding="utf-8")
    asked(job)
    engine._final_mux(job, job / "audio.mp3", job / "subs.srt", False)
    assert engine.muxed == [True] and "subtitles: 5 lines + 0 kinetic" in lines
    asked(job, on=False)
    engine._final_mux(job, job / "audio.mp3", job / "subs.srt", False)
    assert engine.muxed[-1] is False


def test_captions_switched_off_stay_off_with_no_note(job):
    lines = []
    engine = fake_engine(lines)
    (job / "subs.srt").write_text(SRT, encoding="utf-8")
    engine._final_mux(job, job / "audio.mp3", job / "subs.srt", False)
    assert engine.muxed == [False] and captions_lines(lines) == []


def test_installing_twice_wraps_once(job):
    engine = fake_engine([])
    build = engine._build_burn_ass
    cr_captions.install(engine)
    assert engine._build_burn_ass is build


@pytest.mark.parametrize("look,expected", [
    ({}, True), ({"captions": {"size": 80}}, True), ({"captions": {"off": True}}, False),
    ({"visuals": "folio", "folio": {}}, False), ({"visuals": "folio", "folio": {"captions": True}}, True),
    ({"visuals": "vox"}, False), ({"visuals": "vox", "vox": {"subtitles": False}}, False),
    ({"visuals": "vox", "vox": {"look": "collage"}}, True), ({"visuals": "cr_style_delivery"}, True)])
def test_each_recipe_says_whether_it_has_captions_by_design(look, expected):
    assert cr_captions.style_default({"look": look}) is expected
    assert cr_captions.style_default(None) is True


def test_turning_captions_on_is_kept_with_the_video(tmp_path):
    """Create's switch (or make_videos captions=true) reaches the video's run_spec.json as cr_captions='on'."""
    import cr_submission
    app = Flask("captions-submission")
    studio = SimpleNamespace(app=app, QUEUE=[], LOCK=threading.Lock(), JOBS={}, _ensure_dispatcher=lambda: None)
    namespace = {"request": request, "jsonify": jsonify, "QUEUE": studio.QUEUE, "JOBS": studio.JOBS,
                 "LOCK": studio.LOCK, "_ensure_dispatcher": studio._ensure_dispatcher}
    exec('''
def api_run():
    data = request.get_json(force=True)
    with LOCK:
        QUEUE.append({"slug": data["title"].lower(), "title": data["title"], "burn_subs": data.get("burn_subs", True)})
    _ensure_dispatcher()
    return jsonify(queued=[{"slug": data["title"].lower(), "title": data["title"]}])
''', namespace)
    app.add_url_rule("/api/run", "api_run", namespace["api_run"], methods=["POST"])
    cr_submission.install(studio)
    client = app.test_client()
    assert client.post("/api/run", json={"title": "On", "burn_subs": True}).status_code == 200
    assert client.post("/api/run", json={"title": "Off", "burn_subs": False}).status_code == 200
    assert client.post("/api/run", json={"title": "Default"}).status_code == 200
    by = {s["title"]: s for s in studio.QUEUE}
    assert by["On"]["cr_captions"] == "on" and "cr_captions" not in by["Off"] and "cr_captions" not in by["Default"]


def test_make_videos_starts_at_the_styles_own_captions():
    import cr_studio_tools as tools
    none = {"name": "history", "label": "History", "captions": False}
    some = {"name": "documentary", "label": "Documentary", "captions": True}
    assert tools.video_payload(none, ["A"], 5, {}, {})["burn_subs"] is False
    assert tools.video_payload(none, ["A"], 5, {}, {"captions": True})["burn_subs"] is True
    assert tools.video_payload(some, ["A"], 5, {}, {})["burn_subs"] is True
    assert tools.video_payload(some, ["A"], 5, {}, {"captions": False})["burn_subs"] is False
    assert tools.style_card({**none, "tagline": "x"})["captions"] is False


def test_progress_says_why_a_video_has_no_captions(monkeypatch):
    import cr_studio_tools as tools
    status = {"state": "done", "done": True, "progress": 1, "video": "final.mp4",
              "lines": ["subtitles: 0 lines + 0 kinetic",
                        "Captions: none in this video: its narration has no timed words. The rest of the video is finished.",
                        "final: video.mp4"]}
    monkeypatch.setattr(tools, "call", lambda method, path, body=None, **kw: status)
    out = tools._job_status("my-video")
    assert out["captions_note"].startswith("Captions: none in this video: its narration has no timed words")


def test_the_create_page_starts_captions_at_the_recipes_own_choice():
    page = (ROOT / "local_runtime/cr_workspace.js").read_text(encoding="utf-8")
    assert "recipe.captions !== false" in page and "captionsDrafts" in page
    assert "This style has no captions, like its channel." in page
    assert 'id="crw-captions-note"' in (ROOT / "local_runtime/cr_workspace.html").read_text(encoding="utf-8")
