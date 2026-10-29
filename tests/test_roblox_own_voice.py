"""Roblox Story Shorts narrated by the creator's own recording (the private Shorts source in data/frontier/shorts, where
this checkout has it). Primo (Discord, 2026-10-29) pitches the Adam voice 1.3x in CapCut and uploads that take: Studio
hands it to make_roblox as CR_RBX_OWN_VOICE (cr_shorts). The recording IS the voice: Whisper times its words, the
director's shots are cut on exactly those words, nothing is sped up or tightened, the Short is as long as the
recording, and no voice is made. Stand-ins only: no model, voice, Whisper or render runs."""
import ast
import json
import re
import sys
import types
from pathlib import Path

import pytest

SHORTS = Path(__file__).resolve().parents[1] / "data" / "frontier" / "shorts"
ROBLOX = SHORTS / "roblox" / "make_roblox.py"
pytestmark = pytest.mark.skipif(not ROBLOX.is_file(), reason="the private Shorts source is not in this checkout")

FUNCTIONS = {"own_sig", "own_reset", "own_heard", "target_words", "own_block", "fit_to_recording", "norm", "voice",
             "timeline"}
CONSTANTS = {"OWN_FILES", "OWN_MARK"}


@pytest.fixture
def align(monkeypatch):
    monkeypatch.syspath_prepend(str(SHORTS / "hoodie" / "lib"))
    sys.modules.pop("align", None)
    import align
    return align


def roblox(tmp_path, align, own="", heard=None, seconds=0.0, ran=None, lang="en"):
    """make_roblox's own functions (never run as a whole: that needs Claude, a voice and the renderer). heard: what the
    stand-in Whisper hears in the recording; ran: the commands run (the stand-in ffmpeg writes voice.wav)."""
    tree = ast.parse(ROBLOX.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in n.targets)]
    assert {n.name for n in body} == FUNCTIONS, "make_roblox's own-recording functions moved: check this test"
    assert len(consts) == len(CONSTANTS)
    ran = [] if ran is None else ran

    def run(cmd, **kw):
        ran.append(cmd)
        Path(cmd[-1]).write_bytes(b"RIFF")                     # voice.wav, as ffmpeg would leave it
        return types.SimpleNamespace(returncode=0, stderr="")
    voicefit = types.SimpleNamespace(SR=44100, words=lambda path: list(heard or []),
                                     _load=lambda path, sr=44100: [0.0] * int(seconds * 44100))
    space = {"os": __import__("os"), "re": re, "json": json, "Path": Path, "OUT": tmp_path, "OWN_VOICE": own,
             "FF": "ffmpeg", "LANG_CODE": lang, "SECONDS": 55, "WPS": 4.35, "log": lambda *a: None, "align": align, "voicefit": voicefit,
             "subprocess": types.SimpleNamespace(run=run),
             "tts": lambda *a: (_ for _ in ()).throw(AssertionError("no voice is made for a recording"))}
    exec(compile(ast.Module(body=consts + body, type_ignores=[]), str(ROBLOX), "exec"), space)
    return types.SimpleNamespace(**space)


HEARD = [(0.4, 0.6, "In"), (0.6, 0.8, "this"), (0.8, 1.2, "world,"), (1.2, 1.7, "everyone"), (1.7, 2.0, "has"),
         (2.0, 2.2, "a"), (2.2, 2.7, "number."), (3.0, 3.3, "Mine"), (3.3, 3.5, "was"), (3.5, 4.1, "zero.")]


def test_the_recording_is_heard_once_as_recorded_and_planned_on_its_own_words(tmp_path, align):
    take = tmp_path / "adam-pitched.mp3"
    take.write_bytes(b"my pitched take")
    ran = []
    rb = roblox(tmp_path, align, str(take), HEARD, seconds=4.9, ran=ran)
    h = rb.own_heard()
    assert h["text"] == "In this world, everyone has a number. Mine was zero." and h["seconds"] == 4.9
    assert len(ran) == 1 and ran[0][:4] == ["ffmpeg", "-v", "error", "-y"] and ran[0][-1] == str(tmp_path / "voice.wav")
    assert not any("atempo" in str(a) or "rubberband" in str(a) for a in ran[0]), "the creator's pitch and pace are kept"
    rb.own_heard()
    assert len(ran) == 1, "heard once, kept in own_heard.json"
    assert rb.target_words() == 10
    block = rb.own_block()
    assert "THE NARRATION IS ALREADY RECORDED" in block and "EXACTLY the words below" in block
    assert block.endswith("In this world, everyone has a number. Mine was zero.")
    assert roblox(tmp_path, align).own_block() == "" and roblox(tmp_path, align).target_words() == int(55 * 4.35)


def test_a_recording_with_no_words_stops_with_what_to_do(tmp_path, align):
    take = tmp_path / "music-only.mp3"
    take.write_bytes(b"no voice")
    with pytest.raises(SystemExit, match="Whisper heard no narration in your recording"):
        roblox(tmp_path, align, str(take), [], seconds=20).own_heard()


def test_the_directors_shots_are_cut_on_exactly_the_recordings_words(tmp_path, align):
    """A director that rewords, drops or adds a word can't put a caption or a cut on words the recording doesn't say."""
    take = tmp_path / "take.mp3"
    take.write_bytes(b"take")
    rb = roblox(tmp_path, align, str(take), HEARD, seconds=4.9)
    plan = {"narration": "In this world everyone has a number. Mine was zero!",
            "shots": [{"text": "In this world everyone", "camera": {"target": "me"}},
                      {"text": "has a NUMBER.", "cast": ["me"]},
                      {"text": "Mine was always zero!"},            # "always" was never said
                      {"text": "The end."}]}                        # nor was this: the shot goes
    out = rb.fit_to_recording(plan, HEARD)
    assert [s["text"] for s in out["shots"]] == ["In this world, everyone", "has a number.", "Mine was zero."]
    assert out["shots"][0]["camera"] == {"target": "me"} and out["shots"][1]["cast"] == ["me"], "the staging is kept"
    assert out["narration"] == "In this world, everyone has a number. Mine was zero."
    words = [w for s in out["shots"] for w in s["text"].split()]
    assert words == [w for _, _, w in HEARD], "every recorded word once, in order"


def test_the_recording_is_the_voice_and_the_short_is_as_long_as_it(tmp_path, align):
    take = tmp_path / "take.mp3"
    take.write_bytes(b"take")
    rb = roblox(tmp_path, align, str(take), HEARD, seconds=6.0)
    plan = rb.fit_to_recording({"shots": [{"text": "In this world, everyone has a number."},
                                          {"text": "Mine was zero."}]}, HEARD)
    v = rb.voice(plan)                                           # tts raises: no voice is made
    assert v["own"] is True and v["seconds"] == 6.0
    assert [w[2] for w in v["words"]] == plan["narration"].split()
    assert tuple(v["words"][0][:2]) == (0.4, 0.6), "Whisper's own times"
    shots, end = rb.timeline(plan, [tuple(w) for w in v["words"]])
    assert end == 6.0, "a pause after the last word is kept: the Short ends with the recording"
    assert shots[1]["t0"] == 3.0 and shots[0]["t0"] == 0.0


def test_another_narration_than_the_work_was_planned_on_starts_the_plan_over(tmp_path, align):
    take = tmp_path / "take.mp3"
    take.write_bytes(b"take one")
    # a Short first made with the AI voice, made again with a recording: the plan and voice were cut on the AI voice
    for name in ("plan.json", "voice.json", "voice_raw.wav", "voice_part00.mp3", "sets_built.json"):
        (tmp_path / name).write_text("{}")
    (tmp_path / "video.json").write_text("{}")
    rb = roblox(tmp_path, align, str(take), HEARD, seconds=4.9)
    rb.own_reset()
    assert not any((tmp_path / n).exists() for n in ("plan.json", "voice.json", "voice_raw.wav", "voice_part00.mp3",
                                                      "sets_built.json"))
    assert (tmp_path / "video.json").exists() and (tmp_path / rb.OWN_MARK).read_text().strip() == rb.own_sig()
    # the same recording again carries on from the saved work
    (tmp_path / "plan.json").write_text("{}")
    rb.own_reset()
    assert (tmp_path / "plan.json").exists()
    # another recording: planned again
    take.write_bytes(b"take two")
    rb.own_reset()
    assert not (tmp_path / "plan.json").exists()
    # back to the AI voice: planned again, and the mark goes
    (tmp_path / "plan.json").write_text("{}")
    roblox(tmp_path, align).own_reset()
    assert not (tmp_path / "plan.json").exists() and not (tmp_path / rb.OWN_MARK).exists()
    # an AI-voiced Short made again with the AI voice carries on as before
    (tmp_path / "plan.json").write_text("{}")
    roblox(tmp_path, align).own_reset()
    assert (tmp_path / "plan.json").exists()


def test_main_needs_no_voice_account_with_a_recording_and_plans_on_it():
    source = ROBLOX.read_text(encoding="utf-8")
    main = source[source.index("def main():"):]
    assert main.index("own_reset()") < main.index("if OWN_VOICE:") < main.index("raise SystemExit(VOICE_MISSING)")
    assert "elif not (OUT / \"voice.json\").exists()" in main, "the voice-account check is only for the AI voice"
    assert "plan = fit_to_recording(plan, own_heard()[\"words\"])" in source
    assert source.count("own_block()") >= 2 and source.count('.replace("{words}", str(target_words()))') == 2


def test_the_same_recording_in_another_language_is_heard_and_planned_again(tmp_path, align):
    """Whisper heard a Spanish take as English (Create's Language left on English): the creator corrects the language and
    makes the Short again. The old words must not be reused: the take is heard again and the plan starts over."""
    take = tmp_path / "toma.mp3"
    take.write_bytes(b"una toma")
    ran = []
    english = roblox(tmp_path, align, str(take), HEARD, seconds=4.9, ran=ran)
    english.own_reset()
    english.own_heard()
    (tmp_path / "plan.json").write_text("{}")
    assert english.own_sig() == roblox(tmp_path, align, str(take)).own_sig(), "English: the recording's own signature"
    spanish = roblox(tmp_path, align, str(take), HEARD, seconds=4.9, ran=ran, lang="es")
    assert spanish.own_sig() != english.own_sig()
    spanish.own_reset()
    assert not (tmp_path / "plan.json").exists() and not (tmp_path / "own_heard.json").exists()
    spanish.own_heard()
    assert len(ran) == 2, "heard again in Spanish"
