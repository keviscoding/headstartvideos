"""Videos in Hindi (customer Swapnil, 2026-10-29: "Add support for Hindi (India) language. (I needed it badly)").

Hindi is written in Devanagari, which no Latin font draws and which Pillow can't shape on its own. What is checked here:
the language row and its words; Devanagari word keys for the engine's word matching (captions spelled as the script);
Studio's own Devanagari font (CR Devanagari) and its release path; the captions told to burn in it; Pillow's text
shaped by HarfBuzz (cr_script_text); Roblox Story Shorts handed the font and the writing rule. Fakes only: nothing is
paid for, no model is called and nothing is rendered beyond a few in-memory Pillow images."""
import ast
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
sys.path.insert(0, str(ROOT))
import cr_language
import cr_script_text

FONTS = ROOT / "local_runtime" / "fonts"
LATIN_FONT = ROOT / "frontier-gfx-kit" / "fonts" / "Inter-Bold.ttf"      # a Latin font with no Devanagari, as the engine's
ENGINE = Path("/Users/workcomputer/Downloads/ChannelRecipe studio")       # the owner's installed Studio (read only)
PRIVATE = ROOT / "data" / "frontier" / "shorts"


# ── the language ───────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said", ["hi", "Hindi", "hindi", "हिंदी", "हिन्दी", "hi-IN", "Hindi (हिंदी)"])
def test_hindi_is_understood_however_it_is_written(said):
    assert cr_language.clean(said) == "hi"


def test_hindi_is_offered_written_in_devanagari_with_its_own_words():
    row = cr_language.BY_ID["hi"]
    assert row["offered"] and cr_language.script("hi") == "deva" and cr_language.script("es") == "latn"
    assert {"id": "hi", "name": "Hindi", "native": "हिंदी", "offered": True} in cr_language.choices()
    assert cr_language.pace("hi") == 1.05
    assert cr_language.cue("hi") == "स्क्रीन पर लिंक" and cr_language.card_label("hi").startswith("कोड स्कैन")
    copy = cr_language.scene_copy("hi")
    assert copy[("subscribe", False)]["value"] == "सब्सक्राइब करें"
    assert re.search(cr_language.SCENES["hi"]["subscribe"], "चैनल को सब्सक्राइब करें।")
    assert re.search(cr_language.SCENES["hi"]["comment"], "कमेंट में बताइए")
    assert cr_language.CHAPTER["hi"] == ("अध्याय", "अध्याय") and cr_language.HEADINGS["hi"][2] == "स्रोत"


def test_every_prompt_asks_for_devanagari_not_hinglish_and_full_stops():
    block, rule = cr_language.language_block("hi"), cr_language.screen_rule("hi")
    for text in (block, rule):
        assert "LANGUAGE" in text and "Hindi" in text
        assert "Devanagari" in text and "no Hinglish" in text and "not the danda" in text
    assert "Devanagari" not in cr_language.language_block("es") and cr_language.script_rule("es") == ""
    assert cr_language.language_block("en") == "" and cr_language.screen_rule("en") == ""


def test_devanagari_words_get_plain_letter_keys_that_forgive_spelling():
    """The engine keys words by a-z: a Devanagari word had no key at all."""
    latin = cr_language.latin
    assert latin("हिंदी") == latin("हिन्दी") == "hindi"
    assert latin("क्या") == "kya" and latin("ज़िंदगी") == latin("जिंदगी")      # nukta folds away
    assert latin("गाँव") == latin("गांव") and latin("१२३") == "123" and latin("सच।") == "sc."
    assert latin("Café 2026") == "Café 2026", "other text is left as it is"
    assert cr_language._fold("हिंदी") == cr_language._fold("Hindi") == "hindi"


# ── Studio's Devanagari font ───────────────────────────────────────────────────────────────────────────────────────

def test_cr_devanagari_draws_hindi_and_every_offered_latin_language():
    ttlib = pytest.importorskip("fontTools.ttLib")
    letters = "अआइईउऊएऐओऔकखगघचछजझटठडढणतथदधनपफबभमयरलवशषसहक्षज्ञड़ढ़ािीुूृेैोौंँः़्।०१२३४५६७८९₹"
    latin = "AZaz09áéíóúñ¿¡üãõâêôçàèëîïùûÿœŒäöÜßåæøÆØ.,!?:;'\"-()%"
    for style in ("Regular", "Bold"):
        font = ttlib.TTFont(FONTS / f"CRDevanagari-{style}.ttf")
        cmap = font.getBestCmap()
        assert not [c for c in letters + latin if ord(c) not in cmap], style
        assert font["name"].getDebugName(1) == "CR Devanagari" and font["name"].getDebugName(2) == style
        order = font.getGlyphOrder()
        assert all(cmap.get(cr_script_text.PUA + gid) == name for gid, name in enumerate(order) if gid), \
            "every glyph is reachable at U+F0000 + its id (how Pillow draws a shaped glyph)"
        assert "Open Font License" in font["name"].getDebugName(13)
    licence = (FONTS / "OFL-NotoSans.txt").read_text(encoding="utf-8")
    assert "Noto Project Authors" in licence and "SIL OPEN FONT LICENSE Version 1.1" in licence


def test_the_release_puts_studios_fonts_beside_the_engines():
    from scripts import build_frontier_admin as builder
    shipped = {name for _, name in builder.runtime_resources(ROOT)}
    assert {"assets/fonts/CRDevanagari-Regular.ttf", "assets/fonts/CRDevanagari-Bold.ttf",
            "assets/fonts/OFL-NotoSans.txt"} <= shipped
    req = (ROOT / "local_runtime" / "studio-requirements.txt").read_text(encoding="utf-8")
    assert 'uharfbuzz>=0.39; python_version >= "3.10"' in req
    assert any("uharfbuzz>=0.39" in new for _, new in builder.ENGINE_PATCHES["requirements.txt"])


# ── the engine's captions and word matching in a Hindi video ─────────────────────────────────────────────────────

def fake_engine(job, fonts_dir, seen):
    engine = types.SimpleNamespace(STYLE_LANGUAGE={"documentary": "English"}, log=lambda line: None,
                                   job_dir=lambda title, style="": job, FONT_DIR=fonts_dir,
                                   _FONTSDIR=":fontsdir=/studio/assets/fonts", _fold=lambda t: (t or "").lower())

    def run_custom(title, minutes=20, *args, **kwargs):
        seen.append({"fonts": engine._FONTSDIR, "fold": engine._fold("हिंदी")})
    engine.run_custom = run_custom
    return engine


def test_a_hindi_video_burns_every_caption_in_cr_devanagari_and_only_that_video(tmp_path):
    job, fonts = tmp_path / "job", tmp_path / "fonts"
    job.mkdir()
    fonts.mkdir()
    (fonts / cr_language.DEVANAGARI_FONT).write_bytes((FONTS / cr_language.DEVANAGARI_FONT).read_bytes())
    seen = []
    engine = fake_engine(job, fonts, seen)
    cr_language.install(engine)
    for lang in ("hi", "es"):
        (job / "run_spec.json").write_text(json.dumps({"cr_language": lang}), encoding="utf-8")
        engine.run_custom("t", 8, style="documentary")
    assert seen[0]["fonts"] == ":fontsdir=/studio/assets/fonts:force_style='Fontname=CR Devanagari,Bold=1'"
    assert seen[1]["fonts"] == ":fontsdir=/studio/assets/fonts", "a Spanish video keeps its style's fonts"
    assert engine._FONTSDIR == ":fontsdir=/studio/assets/fonts", "put back after the run"
    assert seen[0]["fold"] == "hindi", "the engine's word keys read Devanagari"
    (fonts / cr_language.DEVANAGARI_FONT).unlink()
    assert cr_language.caption_fonts(engine, "hi") == ":fontsdir=/studio/assets/fonts", \
        "no font in the engine's folder: libass falls back to the computer's, never to a name it can't find"
    engine._FONTSDIR = ""
    assert cr_language.caption_fonts(engine, "hi") == "", "no fontsdir at all: nothing to add to"


SRT_NAMES = {"_script_words_into_srt", "_parse_srt_full", "_fold", "_strip_md", "_no_audio_tags", "_srt_ts"}
SRT_VALUES = {"_AUDIO_TAG", "_FOLD_EXTRA"}


def real_srt_engine():
    source = ENGINE / "make_video.py"
    if not source.is_file():
        pytest.skip("The owner's pinned private engine is not installed on this computer.")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in SRT_NAMES)
            or (isinstance(n, ast.Assign) and {getattr(t, "id", "") for t in n.targets} & SRT_VALUES)]
    assert {n.name for n in body if isinstance(n, ast.FunctionDef)} == SRT_NAMES, "the engine's functions moved"
    engine = types.ModuleType("make_video")
    engine.re, engine.Path = re, Path
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), engine.__dict__)
    return engine


def test_the_real_engine_spells_hindi_captions_as_the_script(tmp_path):
    """The captions' words come from the speech model and are then spelled as the script spells them. Every
    Devanagari word folded to nothing, so the script's words were all dropped and the speech model's spellings were
    burned in. With Devanagari keys the script's own words come back, word for word."""
    engine = real_srt_engine()
    srt = tmp_path / "subs.srt"
    heard = ("1\n00:00:00,000 --> 00:00:02,000\nक्या आप जानते है की हिन्दी\n\n"
             "2\n00:00:02,000 --> 00:00:04,000\nदुनिया की तीसरी सबसे बड़ी भाषा है\n")
    script = "क्या आप जानते हैं कि हिंदी दुनिया की तीसरी सबसे बड़ी भाषा है."
    srt.write_text(heard, encoding="utf-8")
    assert engine._script_words_into_srt(srt, script) == 0, "as it was: no script word had a key"
    assert "हिन्दी" in srt.read_text(encoding="utf-8")
    cr_language.install(engine)
    srt.write_text(heard, encoding="utf-8")
    assert engine._script_words_into_srt(srt, script) >= 1
    cues = engine._parse_srt_full(srt.read_text(encoding="utf-8"))
    assert [c[2] for c in cues] == ["क्या आप जानते हैं कि हिंदी", "दुनिया की तीसरी सबसे बड़ी भाषा है."]
    assert [c[:2] for c in cues] == [(0.0, 2.0), (2.0, 4.0)], "the timing stays the speech model's"


# ── Pillow's text: shaped by HarfBuzz ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def shaped():
    pytest.importorskip("uharfbuzz")
    from PIL import ImageFont
    cr_script_text.uninstall()
    assert cr_script_text.install()
    yield ImageFont
    cr_script_text.uninstall()


def test_hindi_is_shaped_not_drawn_letter_by_letter(shaped):
    path = cr_script_text.font_file(True)
    glyphs, advance = cr_script_text.shape("कि", path, 100)
    assert len(glyphs) == 2 and glyphs[0][1] < glyphs[1][1], "the i sign is reordered before its consonant"
    assert glyphs[0][0] != glyphs[1][0]
    conjunct, _ = cr_script_text.shape("क्या", path, 100)
    assert len(conjunct) < len("क्या"), "क् + य joins into one conjunct (no visible virama)"
    assert advance > 0


def test_a_latin_font_asked_for_hindi_draws_and_measures_cr_devanagari(shaped):
    from PIL import Image, ImageDraw
    latin = shaped.truetype(str(LATIN_FONT), 64)
    text = "क्या आप जानते हैं? हिंदी में 5 रहस्य"
    line = cr_script_text._Line(text, latin)
    assert line.path.name == "CRDevanagari-Bold.ttf", "a bold Latin font asks for the bold Devanagari"
    assert latin.getlength(text) == pytest.approx(line.advance)
    im = Image.new("RGB", (1400, 200), "black")
    d = ImageDraw.Draw(im)
    assert d.textlength(text, font=latin) == pytest.approx(line.advance)
    box = d.textbbox((700, 100), text, font=latin, anchor="mm")
    assert box[0] == pytest.approx(700 - line.advance / 2, abs=40) and box[1] < 100 < box[3]
    d.text((700, 100), text, font=latin, fill="white", anchor="mm", stroke_width=4, stroke_fill="red")
    ink = im.getbbox()
    assert ink and abs((ink[0] + ink[2]) / 2 - 700) < 30, "centred where it was asked to be"
    raw = im.tobytes()
    pixels = [raw[i:i + 3] for i in range(0, len(raw), 3)]
    reds = sum(1 for p in pixels if p[0] > 200 and p[1] < 60)
    whites = sum(1 for p in pixels if min(p) > 200)
    assert reds > 500 and whites > 2000, "the stroke under the letters, the letters on top"


def test_text_without_devanagari_is_pillows_own(shaped):
    from PIL import Image, ImageDraw
    latin = shaped.truetype(str(LATIN_FONT), 40)
    a = Image.new("L", (600, 80))
    ImageDraw.Draw(a).text((10, 10), "Five secrets of Mumbai", font=latin, fill=255)
    cr_script_text.uninstall()
    b = Image.new("L", (600, 80))
    ImageDraw.Draw(b).text((10, 10), "Five secrets of Mumbai", font=latin, fill=255)
    assert a.tobytes() == b.tobytes()
    assert latin.getlength("Five secrets") == shaped.truetype(str(LATIN_FONT), 40).getlength("Five secrets")


def test_multiline_hindi_lines_up_like_pillows_own(shaped):
    from PIL import Image, ImageDraw
    latin = shaped.truetype(str(LATIN_FONT), 48)
    im = Image.new("L", (900, 300))
    d = ImageDraw.Draw(im)
    one = d.textbbox((20, 20), "पहली पंक्ति", font=latin)
    two = d.multiline_textbbox((20, 20), "पहली पंक्ति\nदूसरी पंक्ति", font=latin, spacing=10)
    assert two[3] > one[3] + 40 and two[1] == pytest.approx(one[1], abs=1)
    d.multiline_text((20, 20), "पहली पंक्ति\nदूसरी पंक्ति", font=latin, fill=255, spacing=10)
    assert im.getbbox()[3] > one[3] + 30


def test_without_the_shaper_hindi_letters_still_show_never_boxes(monkeypatch):
    monkeypatch.setattr(cr_script_text, "_shaper", lambda: None)
    glyphs, advance = cr_script_text.shape("हिंदी", cr_script_text.font_file(True), 50)
    assert [g[3] for g in glyphs] == list("हिंदी") and advance > 0


# ── Roblox Story Shorts in Hindi ─────────────────────────────────────────────────────────────────────────────────

def test_a_hindi_roblox_short_gets_the_devanagari_font_and_rule(tmp_path, monkeypatch):
    import cr_shorts
    root = tmp_path / "shorts"
    (root / "roblox").mkdir(parents=True)
    keys = ("CR_LANGUAGE", "CR_LANGUAGE_CODE", "CR_LANGUAGE_PACE", "CR_CAPTION_FONT", "CR_CAPTION_FONT_DIR",
            "CR_LANGUAGE_RULE")
    (root / "roblox" / "make_roblox.py").write_text(
        "import json, os, sys, pathlib\n"
        "work = pathlib.Path(sys.argv[2])\n"
        f"(work / 'seen.json').write_text(json.dumps({{k: os.environ.get(k) for k in {keys!r}}}), encoding='utf-8')\n"
        "(work / 'short.mp4').write_bytes(b'mp4')\n"
        "(work / 'plan.json').write_text(json.dumps({'title': 'x', 'narration': 'x'}))\n"
        "(work / 'voice.json').write_text(json.dumps({'words': [[0.1, 0.4, 'x']]}))\n", encoding="utf-8")
    monkeypatch.setenv("CR_SHORTS_ROOT", str(root))
    monkeypatch.setattr(cr_shorts, "ensure_pack", lambda *a, **k: None)
    monkeypatch.setattr(cr_shorts, "ready", lambda home=None: [])
    monkeypatch.setattr(cr_shorts, "ranking_voice", lambda style: "")
    job = tmp_path / "job"
    job.mkdir()
    (job / "run_spec.json").write_text(json.dumps({"cr_language": "hi"}), encoding="utf-8")

    class Engine:
        def job_dir(self, title, style):
            return str(job)
    info = {"format": "short", "label": "Roblox Story Shorts", "shorts": {"pipeline": "roblox", "seconds": 55}}
    cr_shorts.run_short(Engine(), "इस दुनिया में", "cr-shorts-roblox", info)
    seen = json.loads((job / "short" / "seen.json").read_text(encoding="utf-8"))
    assert seen["CR_LANGUAGE"] == "Hindi" and seen["CR_LANGUAGE_CODE"] == "hi" and seen["CR_LANGUAGE_PACE"] == "1.05"
    assert seen["CR_CAPTION_FONT"] == "CR Devanagari"
    assert (Path(seen["CR_CAPTION_FONT_DIR"]) / "CRDevanagari-Bold.ttf").is_file()
    assert "Devanagari" in seen["CR_LANGUAGE_RULE"]


@pytest.mark.skipif(not PRIVATE.is_dir(), reason="the private Shorts source is not in this worktree")
def test_the_roblox_pipeline_times_and_captions_hindi_words():
    sys.path.insert(0, str(PRIVATE / "hoodie" / "lib"))
    import align
    assert align.norm("हिंदी,") == "हिंदी" and align.norm("गाँव") == align.norm("गांव") and align.norm("Está") == "esta"
    words = align.align("मैं आज बाज़ार गया।", [(0, 0.3, "मैं"), (0.3, 0.6, "आज"), (0.6, 1.0, "बाजार"), (1.0, 1.3, "गया")])
    assert words == [(0, 0.3, "मैं"), (0.3, 0.6, "आज"), (0.6, 1.0, "बाज़ार"), (1.0, 1.3, "गया।")]
    source = (PRIVATE / "roblox" / "make_roblox.py").read_text(encoding="utf-8")
    assert 'CAPTION_FONT = os.environ.get("CR_CAPTION_FONT", "").strip() or "Arial"' in source
    assert 'f"Style: W,{CAPTION_FONT},92,' in source and "font_dir = CAPTION_FONT_DIR or" in source
    assert "।॥](?!\\d)" in source and source.count("+ LANG_RULE)") == 2


def test_a_writers_hindi_lead_in_is_never_read_aloud(monkeypatch):
    import cr_spoken
    monkeypatch.setitem(cr_language._current, "id", "hi")
    assert cr_spoken.strip_lead_in("यह रही वीडियो की स्क्रिप्ट:\n\nक्या आप जानते हैं?") == "क्या आप जानते हैं?"
    assert cr_spoken.strip_lead_in("पटकथा:\n\nसब बदल गया.") == "सब बदल गया."
    story = "उस रात यह हुआ था:\n\nसब बदल गया."
    assert cr_spoken.strip_lead_in(story) == story, "a story's own opening stays"
