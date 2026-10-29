"""Roblox Story Shorts in the creator's language (the private Shorts source in data/frontier/shorts, where this
checkout has it). A Max customer asked for Roblox Story Shorts in Spanish (2026-10-29): Studio hands the pipeline
CR_LANGUAGE, CR_LANGUAGE_CODE and CR_LANGUAGE_PACE (cr_shorts); the director writes the narration and every word on
screen in it, with an English gloss per shot for the staging rules; Gemini reads it first on Kie; Whisper times it in
that language; captions drop Spanish marks too. Stand-ins only: no model, voice or render runs."""
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

FUNCTIONS = {"language_block", "said", "said_all", "voice_engines", "norm", "ass", "word_in"}


def roblox(tmp_path, lang="", code="en", keys=None, kie=True):
    """make_roblox's own functions (never run as a whole: that needs Claude, a voice and the renderer)"""
    tree = ast.parse(ROBLOX.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    assert {n.name for n in body} == FUNCTIONS, "make_roblox's language functions moved: check this test"
    space = {"os": __import__("os"), "re": re, "json": json, "LANG": lang, "LANG_CODE": code, "OUT": tmp_path,
             "CAP_LOW": [], "_keys": lambda: dict(keys or {}), "_kie_ready": lambda: kie}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(ROBLOX), "exec"), space)
    return types.SimpleNamespace(**space)


def test_an_english_short_is_asked_exactly_as_before(tmp_path):
    rb = roblox(tmp_path)
    assert rb.language_block() == "" and rb.language_block("stager") == ""
    source = ROBLOX.read_text(encoding="utf-8")
    assert source.count("prompt += language_block()") == 1 and 'prompt += language_block("stager")' in source


def test_the_director_writes_the_narration_and_the_words_on_screen_in_spanish(tmp_path):
    block = roblox(tmp_path, "Spanish", "es").language_block()
    assert "## LANGUAGE: Spanish" in block
    assert 'Write "title", "narration" and every shot\'s "text" in natural spoken Spanish' in block
    assert "every word drawn on screen in Spanish: notices" in block and "prints on hats and shirts" in block
    assert 'quote its words exactly as written in Spanish' in block
    assert "Everything else stays in English" in block and 'every "sfx" sound' in block
    assert 'Give every shot one more field, "en": the same line in plain English' in block
    stager = roblox(tmp_path, "Spanish", "es").language_block("stager")
    assert 'An "on" word is a word of THAT shot\'s "text", exactly as written in Spanish' in stager
    assert "A notice or sign you add is written in Spanish" in stager


def test_the_staging_rules_read_the_english_gloss_and_timing_reads_the_spoken_words(tmp_path):
    """Crowds, faces, falls, clocks and props are staged from English words ("everyone", "screamed", "gravity came
    back"): in another language they read each shot's English gloss. A shot's word count (the cuts) and its "on" words
    stay the spoken line's."""
    rb = roblox(tmp_path, "Spanish", "es")
    shot = {"text": "Todo el mundo gritó.", "en": "Everyone screamed."}
    assert rb.said(shot) == "Everyone screamed." and rb.said({"text": "Everyone screamed."}) == "Everyone screamed."
    plan = {"narration": "Todo el mundo gritó. Nadie corrió.",
            "shots": [shot, {"text": "Nadie corrió.", "en": "Nobody ran."}]}
    assert rb.said_all(plan) == "Everyone screamed. Nobody ran."
    assert rb.said_all({"narration": "In this world.", "shots": [{"text": "In this world."}]}) == "In this world."
    source = ROBLOX.read_text(encoding="utf-8")
    assert 's["text"].lower()' not in source, "every staging rule reads said(s)"
    assert source.count("said(s).lower()") >= 17 and "narr_l = said_all(plan).lower()" in source
    assert 'n = len(s["text"].split())' in source, "the cuts still count the spoken words"
    assert 'plan["shots"][k]["text"].lower()' in source, "a stager's \"on\" word is checked against the spoken line"


def test_another_language_is_read_by_gemini_first_unless_the_creator_chose_an_elevenlabs_voice(tmp_path, monkeypatch):
    keys = {"ELEVENLABS_API_KEY": "el-key"}
    monkeypatch.delenv("CR_RBX_VOICE", raising=False)
    monkeypatch.delenv("CR_RBX_VOICE_ENGINE", raising=False)
    assert roblox(tmp_path, keys=keys).voice_engines() == ["eleven", "gemini"], "English: as before"
    assert roblox(tmp_path, "Spanish", "es", keys=keys).voice_engines() == ["gemini", "eleven"]
    assert roblox(tmp_path, "Spanish", "es", keys=keys, kie=False).voice_engines() == ["eleven"], "no Kie: ElevenLabs"
    monkeypatch.setenv("CR_RBX_VOICE", "abcdefghijklmnop1234")                  # Choose voice: their ElevenLabs voice
    assert roblox(tmp_path, "Spanish", "es", keys=keys).voice_engines() == ["eleven", "gemini"]


def test_spanish_words_match_with_or_without_their_accents(tmp_path):
    rb = roblox(tmp_path, "Spanish", "es")
    assert rb.norm("gritó") == rb.norm("grito") == "grito" and rb.norm("¡Corre!") == "corre"
    assert rb.norm("Año") == "ano" and rb.norm("don't") == "don't" and rb.norm("Mom,") == "mom"
    words = [(0.1, 0.4, "Nadie"), (0.4, 0.9, "gritó"), (0.9, 1.2, "más.")]
    assert rb.word_in(words, 0, 3, "grito") == 1 and rb.word_in(words, 0, 3, "mas") == 2
    source = ROBLOX.read_text(encoding="utf-8")
    assert '.strip(".,!?¿¡«»\\"\'")' in source, "a stager's ¡corre is the line's corre"


def test_captions_show_each_word_without_spanish_marks(tmp_path):
    rb = roblox(tmp_path, "Spanish", "es")
    out = rb.ass([(0.1, 0.4, "¿Qué?"), (0.5, 0.9, "¡Corre!"), (1.0, 1.4, "«Ahora»"), (1.5, 1.9, "3,5")], 3.0)
    lines = [l for l in Path(out).read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    assert [l.rsplit("}", 1)[1] for l in lines] == ["Qué", "Corre", "Ahora", "3,5"]


def test_the_words_a_second_follow_the_languages_pace(monkeypatch):
    source = ROBLOX.read_text(encoding="utf-8")
    assert 'WPS = float(os.environ.get("CR_RBX_WPS", "4.35")) * PACE' in source
    assert 'PACE = max(0.5, min(1.2, float(os.environ.get("CR_LANGUAGE_PACE") or 1)))' in source
    assert 'LANG_CODE = os.environ.get("CR_LANGUAGE_CODE", "").strip().lower() or "en"' in source


@pytest.fixture
def lib(monkeypatch):
    monkeypatch.syspath_prepend(str(SHORTS / "hoodie" / "lib"))
    for name in ("voicefit", "align"):
        sys.modules.pop(name, None)
    import align
    import voicefit
    return types.SimpleNamespace(voicefit=voicefit, align=align)


def test_whisper_times_the_narration_in_the_shorts_language(lib, monkeypatch):
    """voicefit.words timed every Short in English (language="en"): a Spanish narration heard as English loses its
    words. It now takes Studio's CR_LANGUAGE_CODE (zack, hoodie, roblox and dialogue all use it)."""
    asked = []

    class Model:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kwargs):
            asked.append(kwargs.get("language"))
            return [types.SimpleNamespace(words=[types.SimpleNamespace(start=0.0, end=0.3, word=" Hola")])], None
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(lib.voicefit, "_load", lambda path, sr=44100: [0.0] * 16000)
    monkeypatch.setattr(lib.voicefit, "_model", None)
    monkeypatch.delenv("CR_LANGUAGE_CODE", raising=False)
    assert lib.voicefit.words("voice.wav") == [(0.0, 0.3, "Hola")]
    monkeypatch.setenv("CR_LANGUAGE_CODE", "es")
    lib.voicefit.words("voice.wav")
    assert asked == ["en", "es"]


def test_the_scripts_spelling_keeps_whispers_timing_with_accents(lib):
    heard = [(0.0, 0.3, "La"), (0.3, 0.7, "boveda"), (0.7, 1.0, "esta"), (1.0, 1.4, "aqui.")]
    got = lib.align.align("La bóveda está aquí.", heard)
    assert [w for _, _, w in got] == ["La", "bóveda", "está", "aquí."]
    assert [round(a, 1) for a, _, _ in got] == [0.0, 0.3, 0.7, 1.0], "each word on its own heard time"
    assert lib.align.norm("Don't") == "don't" and lib.align.norm("café") == "cafe"
