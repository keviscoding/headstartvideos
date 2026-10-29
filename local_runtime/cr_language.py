"""The language a video speaks: its script, its voice, its captions and every word on screen.

The owner, 2026-10-27: "people want different languages ... Like Spanish, especially during the voiceover." The
private engine already makes a channel in any language its style file names (styles/<name>.json "language", read
into make_video.STYLE_LANGUAGE): the outline and script prompts open with "write your entire output in <language>"
(_lang_prompt), and the graphics, maps, figures, chapters and description ask _job_language, which reads the same
table. It was built for a Czech channel; every Studio recipe says English and nothing let a creator choose.

Now a creator picks the language per video (Create's Language, make_videos options.language). The choice rides the
run's spec (cr_language, written to run_spec.json), and at the run's start it is kept in the video's folder
(cr-language.json), so a Resume or a repair, whose specs come from the saved project, speaks the same language. For
that run the engine's table says the chosen language for the video's style, so every prompt that already follows the
style's language follows the video's. The voice reads whatever the script says (Kie's Gemini voices and ElevenLabs'
current models are multilingual), the speech-timing model is told the language instead of guessing it, and the parts
Studio itself writes (the call to action's spoken cue and card, the closing subscribe and comment scenes, a coded
style's on-screen words) come in the same language.

A video asked nothing speaks its style's own language, as before. Create starts at Studio's own default when the
creator chose one (kept in cr-preferences.json), else at the style's language.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import wraps
from pathlib import Path

HOME = Path(__file__).resolve().parent
PREFS = "cr-preferences.json"          # Studio's own choices (cr_animated2d.PREFS): the default language for new videos
DEFAULT = "video_language"
ASKED = "cr_language"                  # run_spec.json: the language chosen for this video ("es")
STICKY = "cr-language.json"            # that choice, kept with the video for its later runs (Resume, repairs)

# The languages every step can make: Claude writes them, Kie's Gemini voices and ElevenLabs read them and the speech
# model times them. id: the speech model's code. engine: the name the engine's language table and prompts use (its
# figure formats key off it). cue: the words a call-to-action mention says ("link on screen"), label: the line under
# it on the card. pace: words a second against English, for the Shorts' words-a-second targets (longer words are
# fewer a second: Spanish narrators say about as many syllables a second as English ones, in longer words;
# estimates from published speech-rate measurements, the voice fitter stretches the rest). offered: every font Studio
# ships draws all of its letters (checked glyph by glyph, 2026-10-27), so
# Create and make_videos offer it for any recipe. Lilita One (the comic kit) and Share Tech Mono (DOSSIER's teletype)
# lack Polish, Czech, Romanian, Hungarian and Turkish letters (ą ě ș ő ğ...): those languages are known, so a style
# that speaks one keeps it, but not offered until those fonts have them. Vietnamese and Cyrillic: most fonts lack them.
LANGUAGES = (
    {"id": "en", "offered": True, "pace": 1.0, "name": "English", "native": "English", "engine": "English",
     "cue": "link on screen", "label": "Scan the code, or use the link below"},
    {"id": "es", "offered": True, "pace": 0.85, "name": "Spanish", "native": "Español", "engine": "Spanish (español)",
     "cue": "enlace en pantalla", "label": "Escanea el código o usa el enlace de abajo"},
    {"id": "pt", "offered": True, "pace": 0.85, "name": "Portuguese", "native": "Português",
     "engine": "Portuguese (português)",
     "cue": "link na tela", "label": "Escaneie o código ou use o link abaixo"},
    {"id": "fr", "offered": True, "pace": 0.95, "name": "French", "native": "Français", "engine": "French (français)",
     "cue": "lien à l'écran", "label": "Scannez le code ou utilisez le lien ci-dessous"},
    {"id": "de", "offered": True, "pace": 0.8, "name": "German", "native": "Deutsch", "engine": "German (Deutsch)",
     "cue": "Link im Bild", "label": "Scanne den Code oder nutze den Link unten"},
    {"id": "it", "offered": True, "pace": 0.85, "name": "Italian", "native": "Italiano",
     "engine": "Italian (italiano)",
     "cue": "link sullo schermo", "label": "Inquadra il codice o usa il link qui sotto"},
    {"id": "nl", "offered": True, "pace": 0.9, "name": "Dutch", "native": "Nederlands",
     "engine": "Dutch (Nederlands)",
     "cue": "link in beeld", "label": "Scan de code of gebruik de link hieronder"},
    {"id": "pl", "offered": False, "pace": 0.8, "name": "Polish", "native": "Polski", "engine": "Polish (polski)",
     "cue": "link na ekranie", "label": "Zeskanuj kod lub użyj linku poniżej"},
    {"id": "ro", "offered": False, "pace": 0.85, "name": "Romanian", "native": "Română",
     "engine": "Romanian (română)",
     "cue": "linkul de pe ecran", "label": "Scanează codul sau folosește linkul de mai jos"},
    {"id": "cs", "offered": False, "pace": 0.85, "name": "Czech", "native": "Čeština", "engine": "Czech (čeština)",
     "cue": "odkaz na obrazovce", "label": "Naskenuj kód nebo použij odkaz níže"},
    {"id": "sv", "offered": True, "pace": 0.9, "name": "Swedish", "native": "Svenska", "engine": "Swedish (svenska)",
     "cue": "länken på skärmen", "label": "Skanna koden eller använd länken nedan"},
    {"id": "da", "offered": True, "pace": 0.9, "name": "Danish", "native": "Dansk", "engine": "Danish (dansk)",
     "cue": "linket på skærmen", "label": "Scan koden, eller brug linket nedenfor"},
    {"id": "no", "offered": True, "pace": 0.9, "name": "Norwegian", "native": "Norsk", "engine": "Norwegian (norsk)",
     "cue": "lenken på skjermen", "label": "Skann koden, eller bruk lenken nedenfor"},
    {"id": "fi", "offered": True, "pace": 0.7, "name": "Finnish", "native": "Suomi", "engine": "Finnish (suomi)",
     "cue": "linkki ruudulla", "label": "Skannaa koodi tai käytä alla olevaa linkkiä"},
    {"id": "hu", "offered": False, "pace": 0.75, "name": "Hungarian", "native": "Magyar",
     "engine": "Hungarian (magyar)",
     "cue": "link a képernyőn", "label": "Olvasd be a kódot, vagy használd a lenti linket"},
    {"id": "tr", "offered": False, "pace": 0.75, "name": "Turkish", "native": "Türkçe", "engine": "Turkish (Türkçe)",
     "cue": "ekrandaki bağlantı", "label": "Kodu tara ya da aşağıdaki bağlantıyı kullan"},
    {"id": "id", "offered": True, "pace": 0.8, "name": "Indonesian", "native": "Bahasa Indonesia",
     "engine": "Indonesian (Bahasa Indonesia)",
     "cue": "tautan di layar", "label": "Pindai kodenya atau pakai tautan di bawah"},
    {"id": "tl", "offered": True, "pace": 0.8, "name": "Filipino", "native": "Filipino",
     "engine": "Filipino (Tagalog)",
     "cue": "link sa screen", "label": "I-scan ang code o gamitin ang link sa ibaba"},
    # Hindi (customer Swapnil, 2026-10-29): written in Devanagari, the script Gemini's hi-IN voices and Whisper's "hi"
    # read and write (a Hinglish script in Latin letters would be read with English sounds and transcribed back in
    # Devanagari, so its captions would never match). No shipped Latin font has a Devanagari letter: Studio ships its
    # own (CR Devanagari, local_runtime/fonts), the captions are burned in it (DEVANAGARI_STYLE) and Pillow's text is
    # shaped by cr_script_text. pace: Hindi says the same thing in about 7 per cent more words than English (separate
    # postpositions and auxiliaries: the IIT Bombay English-Hindi corpus, 20.7M English and 22.2M Hindi words), at a
    # similar speaking time; the narrator's measured pace takes over after two videos (cr_length).
    {"id": "hi", "offered": True, "pace": 1.05, "name": "Hindi", "native": "हिंदी", "engine": "Hindi (हिंदी)",
     "script": "deva", "cue": "स्क्रीन पर लिंक", "label": "कोड स्कैन करें या नीचे दिया लिंक इस्तेमाल करें"},
)
BY_ID = {row["id"]: row for row in LANGUAGES}
# The ready-made Shorts recipes (cr_shorts: Ranking, 3D, photo and Roblox Shorts) write, voice and caption in their own
# pipelines. A pipeline that reads the language (CR_LANGUAGE, CR_LANGUAGE_CODE, CR_LANGUAGE_PACE) is listed here; the
# others speak their recipe's own language, and a Short asked for another one is refused before anything starts rather
# than made in English. Roblox Story Shorts first (a Max customer asked for them in Spanish, 2026-10-29).
SHORTS_WITH_LANGUAGES = {"roblox"}
SHORTS_NOTE = ("{recipe} speaks {own} for now: its Shorts are written, voiced and captioned by their own pipeline. "
               "Make it in {own}, or pick a video recipe or a style written in code for a video in {asked}.")
# Ranking Shorts already speak the language their topic is typed in (its search plan reads it from the topic)
RANKING_NOTE = ("Ranking Shorts speak the language you type the topic in: leave Language as it is and type the topic "
                "in {asked} for a Short in {asked}.")


def short_pipeline(info) -> str:
    """A ready-made Shorts recipe's pipeline ("roblox", "ranking", "zack3d", "photo2d"), "" for any other style."""
    if not (isinstance(info, dict) and info.get("format") == "short"):
        return ""
    return str((info.get("shorts") or {}).get("pipeline") or "zack3d")


def short_speaks_any(info) -> bool:
    """Whether a ready-made Shorts recipe makes its Shorts in any offered language (its pipeline reads it)."""
    return short_pipeline(info) in SHORTS_WITH_LANGUAGES


def shorts_refusal(info, lang_id, recipe="This Shorts recipe", ranking=None) -> str:
    """Why a ready-made Shorts recipe can't make this language ("" when it can: its own language, none asked, or a
    pipeline that reads the language). ranking: whether it is Ranking Shorts (else read from its file)."""
    if not lang_id or not short_pipeline(info) or short_speaks_any(info):
        return ""
    own = style_language(info)
    if lang_id == own:
        return ""
    if ranking is None:
        ranking = short_pipeline(info) == "ranking"
    return (RANKING_NOTE if ranking else SHORTS_NOTE).format(recipe=recipe, own=name(own), asked=name(lang_id))
_current = {"id": ""}                  # the language of the run in this worker process (one video per process)


# ── Devanagari (Hindi) ─────────────────────────────────────────────────────────────────────────────────────────────
# The engine (and the Shorts) match words by folding them to a-z0-9 ("[^a-z0-9]" after _fold): a Devanagari word folds
# to nothing, so the script's spelling never reached the captions (_script_words_into_srt drops every script word
# whose key is empty, leaving the speech model's spelling) and the word clock matched nothing but position. latin()
# gives each Devanagari word a plain-letter key instead: a forgiving one (long and short vowels, anusvara and
# chandrabindu, nukta and virama fold together), because the speech model and the writer spell the same sounds
# differently (हैं / है, हिन्दी / हिंदी).
_DEVA = {}
for _chars, _key in (("\u0915\u0958", "k"), ("\u0916\u0959", "kh"), ("\u0917\u095a", "g"), ("\u0918", "gh"),
                     ("\u0919\u091e\u0923\u0928\u0929", "n"), ("\u091a", "c"), ("\u091b", "ch"),
                     ("\u091c\u095b", "j"), ("\u091d", "jh"), ("\u091f\u0924", "t"), ("\u0920\u0925", "th"),
                     ("\u0921\u0926\u095c", "d"), ("\u0922\u0927\u095d", "dh"), ("\u092a", "p"),
                     ("\u092b\u095e", "ph"), ("\u092c", "b"), ("\u092d", "bh"), ("\u092e", "m"),
                     ("\u092f\u095f", "y"), ("\u0930\u0931", "r"), ("\u0932\u0933\u0934", "l"), ("\u0935", "v"),
                     ("\u0936\u0937", "sh"), ("\u0938", "s"), ("\u0939", "h"),
                     ("\u0905\u0906\u093e", "a"), ("\u0907\u0908\u093f\u0940", "i"),
                     ("\u0909\u090a\u0941\u0942", "u"), ("\u090b\u0960\u0943\u0944", "ri"),
                     ("\u090f\u090d\u090e\u0947\u0945\u0946", "e"), ("\u0910\u0948", "ai"),
                     ("\u0913\u0911\u0912\u094b\u0949\u094a", "o"), ("\u0914\u094c", "au"),
                     ("\u0902\u0901", "n"), ("\u0903", "h"), ("\u093c\u094d\u093d\u0900", ""),
                     ("\u0964\u0965", "."), ("\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f", None)):
    for _i, _c in enumerate(_chars):
        _DEVA[ord(_c)] = str(_i) if _key is None else _key
DEVANAGARI = re.compile("[\u0900-\u097F]")


def latin(text) -> str:
    """Devanagari letters as plain letters (a matching key, not a transliteration to show): "हिंदी" and "हिन्दी" are
    both "hindi". Every other character is left as it is."""
    text = str(text or "")
    return text.translate(_DEVA) if DEVANAGARI.search(text) else text


def script(lang_id) -> str:
    """The writing system a language's videos are in: "latn", or "deva" for Hindi."""
    row = find(lang_id)
    return str(row.get("script") or "latn") if row else "latn"


def _fold(text) -> str:
    """Lower case without accents: "Español", "espanol" and "ESPAÑOL" are the same word."""
    decomposed = unicodedata.normalize("NFKD", latin(text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def find(value) -> dict | None:
    """The language a value names: its id ("es"), its English name ("Spanish"), its own name ("Español") or an engine
    style's language ("Czech (čeština)", "en"). None when it names none of them."""
    if isinstance(value, dict):
        value = value.get("id")
    raw = str(value or "").strip()
    if not raw:
        return None
    word = _fold(raw.split("(")[0])
    for row in LANGUAGES:
        if word in (row["id"], _fold(row["name"]), _fold(row["native"]), _fold(row["engine"].split("(")[-1].rstrip(")"))):
            return row
    head = _fold(raw.split("-")[0].split("_")[0])           # "es-MX", "pt_BR"
    return BY_ID.get(head)


def clean(value, style_info=None) -> str:
    """A creator's choice of language as its id; "" when none was asked. Raises ValueError for one Studio can't make
    (one it knows but doesn't offer is fine for a style that speaks it already; style_info None: not checked here)."""
    if value in (None, ""):
        return ""
    row = find(value)
    if row is None or (style_info is not None and not row["offered"] and row["id"] != style_language(style_info)):
        raise ValueError("Choose a language from the list: " + ", ".join(r["name"] for r in LANGUAGES if r["offered"])
                         + ".")
    return row["id"]


def choices(also=()) -> list:
    """The languages Create and Claude Code offer, English first, and any in `also` (the recipes' own languages: a
    recipe that speaks Czech offers Czech)."""
    keep = {r["id"] for r in LANGUAGES if r["offered"]} | {x for x in also if x in BY_ID}
    return [{"id": r["id"], "name": r["name"], "native": r["native"], "offered": r["offered"]}
            for r in LANGUAGES if r["id"] in keep]


def name(lang_id) -> str:
    row = find(lang_id)
    return row["name"] if row else "English"


def style_language(info) -> str:
    """The language a style's videos speak by its own file (its "language"), as an id; "en" when it names none Studio
    knows."""
    raw = (info or {}).get("language") if isinstance(info, dict) else ""
    row = find(raw)
    return row["id"] if row else "en"


# ── Studio's default for new videos ──────────────────────────────────────────────────────────────────────────────

def _read(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def default(home=None) -> str:
    """Studio's default language for new videos, as the creator last chose it in Create ("" when never chosen: each
    style speaks its own)."""
    row = find(_read(Path(home or HOME) / PREFS).get(DEFAULT))
    return row["id"] if row else ""


def set_default(home, value) -> str:
    """Keep the creator's choice as Studio's default for new videos (a language offered for every recipe; a recipe's
    own one, like a Czech channel's, changes nothing). Returns the default's id."""
    lang = clean(value) or "en"
    path = Path(home or HOME) / PREFS
    if not BY_ID[lang]["offered"]:
        return default(home) or "en"
    data = _read(path)
    if data.get(DEFAULT) != lang:
        data[DEFAULT] = lang
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    return lang


def for_new_video(home, info) -> str:
    """The language a new video starts with when the creator says nothing: Studio's default, else its style's own."""
    return default(home) or style_language(info)


# ── one video's language ──────────────────────────────────────────────────────────────────────────────────────

def _asked(path) -> str:
    row = find(_read(path).get(ASKED))
    return row["id"] if row else ""


def of_job(job) -> str:
    """The language this video was asked to speak: this run's own choice, else the one kept with the video; "" when
    none was asked (it speaks its style's language)."""
    job = Path(job)
    return _asked(job / "run_spec.json") or _asked(job / STICKY)


def remember(job) -> str:
    """At a run's start: this run's choice is kept in the video's folder for its later runs. Returns the video's
    language ("" when none was asked)."""
    job = Path(job)
    this_run = _asked(job / "run_spec.json")
    if this_run and _asked(job / STICKY) != this_run:
        try:
            (job / STICKY).write_text(json.dumps({ASKED: this_run}), encoding="utf-8")
        except OSError:
            pass
    return this_run or _asked(job / STICKY)


def current() -> str:
    """The language of the video this worker is making ("" when it speaks its style's own, or none is running)."""
    return _current["id"]


def whisper_code() -> str:
    """What the speech-timing model is told the narration speaks ("" lets it detect the language, as before)."""
    return _current["id"]


def pace(lang_id) -> float:
    """Words a second in this language against English (1.0 for English or a language Studio doesn't know)."""
    row = find(lang_id)
    return float(row["pace"]) if row else 1.0


def cue(lang_id=None) -> str:
    """The words a call-to-action mention says in this language ("link on screen")."""
    row = find(lang_id or _current["id"]) or BY_ID["en"]
    return row["cue"]


def card_label(lang_id=None) -> str:
    row = find(lang_id or _current["id"]) or BY_ID["en"]
    return row["label"]


def screen_rule(lang_id=None) -> str:
    """One line for a prompt that writes words drawn on screen inside a fixed format (a coded style's director): the
    words in the video's language, the format's own names untouched. "" for English."""
    row = find(lang_id or _current["id"])
    if not row or row["id"] == "en":
        return ""
    return (f"LANGUAGE: the narration is in {row['name']}. Every word drawn on screen (labels, signs, captions, "
            f"titles, numbers written as words) is in {row['name']}. Action names, ids and JSON keys stay exactly as "
            "given." + script_rule(row["id"]) + "\n\n")


def script_rule(lang_id=None) -> str:
    """The writing-system rule for a language not written in Latin letters ("" for the others)."""
    row = find(lang_id or _current["id"])
    if not row or script(row["id"]) != "deva":
        return ""
    return (f" Write the {row['name']} in Devanagari (देवनागरी), never in Latin letters (no Hinglish): the voice reads "
            "Devanagari. Everyday English words Hindi speakers use are written in Devanagari as they are said "
            "(मोबाइल, इंटरनेट). End every sentence with a full stop (.), a question mark or an exclamation mark, not "
            "the danda (।): the narration is split into sentences on those marks.")


def language_block(lang_id=None) -> str:
    """The line every viewer-facing prompt gets in a video asked for a language other than English ("" otherwise)."""
    row = find(lang_id or _current["id"])
    if not row or row["id"] == "en":
        return ""
    n = row["name"]
    return (f"\n\nLANGUAGE OF THIS VIDEO: {n}. Every word the viewer hears or reads is in {n}: the narration, titles, "
            f"headlines, labels, captions, cards, quotes and any text drawn on screen. These stay in English: "
            "image-generation prompts, stock-footage and picture search queries, tool names, ids and JSON keys."
            + script_rule(row["id"]) + "\n")


def words(text) -> list:
    """A line's words, lower case, without the punctuation around them (¿¡ and quotes included), for matching
    spoken words against a cue."""
    return [w for w in (re.sub(r"^[\W_]+|[\W_]+$", "", t).casefold() for t in str(text or "").split()) if w]


# ── the closing scenes the engine draws when the narrator asks to subscribe or comment ─────────────────────────
# The engine ships English and Czech copy (make_video._CTA_COPY) and finds the ask by English and Czech words. In a
# video in another language it either found nothing (Spanish "suscríbete") or showed the English card (Polish
# "komentarz" matches its Czech "koment"). These are the same scenes in each language; a language without copy here
# gets no closing scene, never one in another language.
SCENES = {
    "es": {"subscribe": r"suscr[ií]b", "comment": r"\bcomentarios?\b|\bcomenta\b",
           "value": "SUSCRÍBETE", "title": "9 de cada 10 personas que ven esto no están suscritas",
           "subtitle": "Un clic. Así te encuentra el próximo vídeo.",
           "comment_title": "Cuéntamelo en los comentarios",
           "items": ["¿Qué parte te impactó más?", "¿Qué te llevas de esto?"],
           "comment_subtitle": "Los leo, y respondo."},
    "pt": {"subscribe": r"inscrev|inscri[cç]", "comment": r"\bcoment[aá]rios?\b|\bcomenta\b",
           "value": "INSCREVA-SE", "title": "9 em cada 10 pessoas que assistem a isto não são inscritas",
           "subtitle": "Um clique. É assim que o próximo vídeo te encontra.",
           "comment_title": "Me conta nos comentários",
           "items": ["Qual parte mais te marcou?", "O que você leva disso?"],
           "comment_subtitle": "Eu leio, e respondo."},
    "fr": {"subscribe": r"abonn", "comment": r"\bcommentaires?\b|\bcommente[zr]?\b",
           "value": "S'ABONNER", "title": "9 personnes sur 10 qui regardent ceci ne sont pas abonnées",
           "subtitle": "Un clic. C'est comme ça que la prochaine vidéo vous trouve.",
           "comment_title": "Dites-le-moi en commentaire",
           "items": ["Quel passage vous a le plus marqué ?", "Qu'est-ce que vous en retenez ?"],
           "comment_subtitle": "Je les lis, et je réponds."},
    "de": {"subscribe": r"abonn", "comment": r"\bkommentar",
           "value": "ABONNIEREN", "title": "9 von 10 Leuten, die das sehen, haben nicht abonniert",
           "subtitle": "Ein Klick. So findet dich das nächste Video.",
           "comment_title": "Schreib es mir in die Kommentare",
           "items": ["Welcher Teil hat dich am meisten getroffen?", "Was nimmst du daraus mit?"],
           "comment_subtitle": "Ich lese sie, und ich antworte."},
    "it": {"subscribe": r"iscriv|iscri[tz]", "comment": r"\bcommenti?\b|\bcommenta\b",
           "value": "ISCRIVITI", "title": "9 persone su 10 che guardano questo non sono iscritte",
           "subtitle": "Un clic. È così che il prossimo video ti trova.",
           "comment_title": "Dimmelo nei commenti",
           "items": ["Quale parte ti ha colpito di più?", "Cosa ti porti via da tutto questo?"],
           "comment_subtitle": "Li leggo, e rispondo."},
    "hi": {"subscribe": r"सब्सक्राइब|subscribe", "comment": r"कमेंट|कॉमेंट|टिप्पणी|comment",
           "value": "सब्सक्राइब करें", "title": "इसे देखने वाले 10 में से 9 लोगों ने सब्सक्राइब नहीं किया है",
           "subtitle": "बस एक क्लिक। ऐसे ही अगला वीडियो आप तक पहुँचता है।",
           "comment_title": "कमेंट में बताइए",
           "items": ["आपको सबसे ज़्यादा कौन-सा हिस्सा याद रहा?", "आप इससे क्या सीखकर जा रहे हैं?"],
           "comment_subtitle": "हर कमेंट पढ़ा जाता है, और जवाब भी मिलता है।"},
}


# The chapter cards of cinema recipes (cinema.WORDS, keyed by the language's name: English, Czech, Polish, Slovak and
# German only) and the YouTube description's section headings, which the creator pastes under the video.
CHAPTER = {"es": ("CAPÍTULO", "Capítulo"), "pt": ("CAPÍTULO", "Capítulo"), "fr": ("CHAPITRE", "Chapitre"),
           "de": ("KAPITEL", "Kapitel"), "it": ("CAPITOLO", "Capitolo"), "nl": ("HOOFDSTUK", "Hoofdstuk"),
           "sv": ("KAPITEL", "Kapitel"), "da": ("KAPITEL", "Kapitel"), "no": ("KAPITTEL", "Kapittel"),
           "fi": ("LUKU", "Luku"), "id": ("BAB", "Bab"), "tl": ("KABANATA", "Kabanata"), "pl": ("ROZDZIAŁ", "Rozdział"),
           "cs": ("KAPITOLA", "Kapitola"), "ro": ("CAPITOLUL", "Capitolul"), "hu": ("FEJEZET", "Fejezet"),
           "tr": ("BÖLÜM", "Bölüm"), "hi": ("अध्याय", "अध्याय")}
HEADINGS = {"es": ("IMÁGENES", "CAPÍTULOS", "FUENTES"), "pt": ("IMAGENS", "CAPÍTULOS", "FONTES"),
            "fr": ("IMAGES", "CHAPITRES", "SOURCES"), "de": ("BILDMATERIAL", "KAPITEL", "QUELLEN"),
            "it": ("FILMATI", "CAPITOLI", "FONTI"), "nl": ("BEELDMATERIAAL", "HOOFDSTUKKEN", "BRONNEN"),
            "sv": ("BILDMATERIAL", "KAPITEL", "KÄLLOR"), "da": ("OPTAGELSER", "KAPITLER", "KILDER"),
            "no": ("OPPTAK", "KAPITLER", "KILDER"), "fi": ("KUVAMATERIAALI", "LUVUT", "LÄHTEET"),
            "id": ("CUPLIKAN", "BAB", "SUMBER"), "tl": ("MGA FOOTAGE", "MGA KABANATA", "MGA PINAGMULAN"),
            "hi": ("फ़ुटेज", "अध्याय", "स्रोत")}


def description_headings(path, lang_id) -> bool:
    """youtube.txt's FOOTAGE, CHAPTERS and SOURCES headings in the video's language (the lines the viewer reads in
    the description; the notes for the creator stay as they are). True when it changed the file."""
    names = HEADINGS.get(str(lang_id or ""))
    try:
        text = Path(path).read_text(encoding="utf-8") if names else ""
    except OSError:
        return False
    if not text:
        return False
    swap = dict(zip(("FOOTAGE", "CHAPTERS", "SOURCES"), names))
    out = "\n".join(swap.get(line, line) for line in text.split("\n"))
    if out == text:
        return False
    Path(path).write_text(out, encoding="utf-8")
    return True


def scene_copy(lang_id) -> dict | None:
    """The engine's _CTA_COPY entries for this language, keyed as the engine reads them, or None when there are none.
    A Czech video keeps the engine's own Czech copy."""
    s = SCENES.get(str(lang_id or ""))
    if not s:
        return None
    return {("subscribe", False): {"template": "subscribe", "value": s["value"], "title": s["title"],
                                   "subtitle": s["subtitle"]},
            ("comment", False): {"template": "comment", "title": s["comment_title"],
                                 "items": [{"label": x} for x in s["items"]], "subtitle": s["comment_subtitle"]}}


# ── wiring into the engine ──────────────────────────────────────────────────────────────────────────────────────

def _log(engine, message: str) -> None:
    try:
        getattr(engine, "log", print)(message)
    except Exception:                                          # noqa: BLE001 - a progress line only
        pass


class _Languages(dict):
    """make_video.STYLE_LANGUAGE (style -> language): while a video asked for a language runs, every style reads as
    that language. The engine rebuilds this table in the middle of a video (a 3D section reloads the styles, an
    automatic look registers itself under a new name), and each rebuild writes into this same table, so the video's
    language holds to its end."""

    def __getitem__(self, key):
        row = BY_ID.get(_current["id"])
        return row["engine"] if row else super().__getitem__(key)

    def get(self, key, default=None):
        row = BY_ID.get(_current["id"])
        return row["engine"] if row else super().get(key, default)


_touched: list = []                    # (style dict, had a language, its language) to put back when the run ends


class _Styles(dict):
    """make_video.STYLE_INFO (style -> its file): while a video asked for a language runs, a style read from here says
    that language (the fact check writes its fixes, soundbites, chapters and 3D read it from the file); each one is
    put back when the run ends."""

    def _mark(self, info):
        row = BY_ID.get(_current["id"])
        if row and isinstance(info, dict) and info.get("language") != row["engine"]:
            _touched.append((info, "language" in info, info.get("language")))
            info["language"] = row["engine"]
        return info

    def __getitem__(self, key):
        return self._mark(super().__getitem__(key))

    def get(self, key, default=None):
        return self._mark(super().get(key, default))


def _put_back() -> None:
    while _touched:
        info, had, before = _touched.pop()
        if had:
            info["language"] = before
        else:
            info.pop("language", None)


# The captions and the engine's other libass text name Latin fonts (Montserrat, Inter...), which have no Devanagari.
# libass then asks the computer for a fallback: Kohinoor on a Mac, but the ffmpeg Studio installs on Windows may have
# no font provider to ask, and the fallback is a thin regular face beside a heavy Latin one. In a video written in
# Devanagari every subtitles= burn of the engine (they all add make_video._FONTSDIR) is told to draw its styles in
# Studio's own CR Devanagari, bold, from the same fonts folder (it has Latin letters too). An inline \fn in a label
# still picks its own font, with the computer's fallback.
DEVANAGARI_FONT = "CRDevanagari-Bold.ttf"
DEVANAGARI_STYLE = ":force_style='Fontname=CR Devanagari,Bold=1'"


def caption_fonts(engine, lang_id) -> str:
    """The engine's subtitles= font option for a video in this language: its own (fontsdir), plus the Devanagari font
    for a language written in it when the engine's fonts folder has that font."""
    base = getattr(engine, "_FONTSDIR", "") or ""
    folder = getattr(engine, "FONT_DIR", None)
    if (script(lang_id) == "deva" and base and DEVANAGARI_STYLE not in base and folder is not None
            and (Path(folder) / DEVANAGARI_FONT).is_file()):
        return base + DEVANAGARI_STYLE
    return base


def install(engine):
    """For a video asked to speak a language: its style speaks it for the whole run, every prompt that shapes what the
    viewer sees or hears says it, the speech model is told it, and the closing scenes come in it. A video asked nothing
    runs exactly as before."""
    if getattr(engine, "_cr_language", False):
        return
    engine._cr_language = True
    try:
        import cr_script_text                                 # Devanagari shaped wherever Pillow draws it
        cr_script_text.install()
    except Exception as e:                                    # noqa: BLE001 - Pillow's own drawing then
        _log(engine, f"Hindi text: Studio's text shaping couldn't start ({str(e)[:80]})")
    fold = getattr(engine, "_fold", None)
    if callable(fold) and not getattr(fold, "_cr_language", False):
        # the engine's word matching (captions spelled as the script, the word clock) keys words by a-z: a Devanagari
        # word gets its plain-letter key (latin) instead of none
        @wraps(fold)
        def fold_any(text, *args, **kwargs):
            return fold(latin(text), *args, **kwargs)
        fold_any._cr_language = True
        engine._fold = fold_any
    for table, kind in (("STYLE_LANGUAGE", _Languages), ("STYLE_INFO", _Styles)):
        now = getattr(engine, table, None)
        if isinstance(now, dict) and not isinstance(now, kind):
            setattr(engine, table, kind(now))               # the engine's functions read it as a module global

    for fn in ("run_custom", "run_pipeline"):
        start = getattr(engine, fn, None)
        if not callable(start):
            continue

        @wraps(start)
        def run(title, minutes=20, *args, _start=start, **kwargs):
            style = kwargs.get("style") or ""
            try:
                lang = remember(engine.job_dir(title, style))
            except Exception:                                  # noqa: BLE001 - the style's own language then
                lang = ""
            if not lang:
                return _start(title, minutes, *args, **kwargs)
            _current["id"] = lang
            _log(engine, f"Language: {BY_ID[lang]['name']} (the script, the voice, the captions and the words on "
                         "screen)")
            fonts = getattr(engine, "_FONTSDIR", None)
            if fonts is not None:
                engine._FONTSDIR = caption_fonts(engine, lang)
            try:
                return _start(title, minutes, *args, **kwargs)
            finally:
                _current["id"] = ""
                _put_back()
                if fonts is not None:
                    engine._FONTSDIR = fonts
        run._cr_language = True
        setattr(engine, fn, run)

    extra = getattr(engine, "_extra_block", None)
    if callable(extra):
        # the block the engine adds to every prompt that shapes what the viewer sees or hears (the scene director's
        # labels and cards, graphics, 3D, thumbnails, the script): the language goes with it, for the run only
        @wraps(extra)
        def extra_block(*args, **kwargs):
            return extra(*args, **kwargs) + language_block()
        extra_block._cr_language = True
        engine._extra_block = extra_block

    meta = getattr(engine, "generate_youtube_meta", None)
    if callable(meta):
        @wraps(meta)
        def generate_youtube_meta(title, script, srt, job, *args, **kwargs):
            out = meta(title, script, srt, job, *args, **kwargs)
            lang = _current["id"] or of_job(job)
            if lang and lang != "en":
                description_headings(Path(job) / "youtube.txt", lang)
            return out
        generate_youtube_meta._cr_language = True
        engine.generate_youtube_meta = generate_youtube_meta
    try:
        import cinema                                         # the engine's chapter cards, where this Studio has them
        for lang, pair in CHAPTER.items():
            cinema.WORDS.setdefault(BY_ID[lang]["engine"], pair)
    except Exception:                                         # noqa: BLE001 - no cinema recipes here: nothing to name
        pass

    scenes = getattr(engine, "_apply_cta_scenes", None)
    if callable(scenes) and isinstance(getattr(engine, "_CTA_COPY", None), dict):
        @wraps(scenes)
        def apply_cta_scenes(specs, chunks, style, job=None, *args, **kwargs):
            lang = _current["id"] or (of_job(job) if job else "")
            if not lang or lang in ("en", "cs"):
                return scenes(specs, chunks, style, job, *args, **kwargs)
            copy, s = scene_copy(lang), SCENES.get(lang)
            if not copy:
                return 0                                       # no closing scene rather than one in another language
            saved = (engine._CTA_COPY, getattr(engine, "_CTA_SUB_RE", None), getattr(engine, "_CTA_COM_RE", None))
            engine._CTA_COPY = {**engine._CTA_COPY, **copy}
            engine._CTA_SUB_RE = re.compile(s["subscribe"], re.I)
            engine._CTA_COM_RE = re.compile(s["comment"], re.I)
            try:
                return scenes(specs, chunks, style, job, *args, **kwargs)
            finally:
                engine._CTA_COPY, engine._CTA_SUB_RE, engine._CTA_COM_RE = saved
        apply_cta_scenes._cr_language = True
        engine._apply_cta_scenes = apply_cta_scenes
