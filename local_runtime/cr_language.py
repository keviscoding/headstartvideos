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
# it on the card. offered: every font Studio ships draws all of its letters (checked glyph by glyph, 2026-10-27), so
# Create and make_videos offer it for any recipe. Lilita One (the comic kit) and Share Tech Mono (DOSSIER's teletype)
# lack Polish, Czech, Romanian, Hungarian and Turkish letters (ą ě ș ő ğ...): those languages are known, so a style
# that speaks one keeps it, but not offered until those fonts have them. Vietnamese and Cyrillic: most fonts lack them.
LANGUAGES = (
    {"id": "en", "offered": True, "name": "English", "native": "English", "engine": "English",
     "cue": "link on screen", "label": "Scan the code, or use the link below"},
    {"id": "es", "offered": True, "name": "Spanish", "native": "Español", "engine": "Spanish (español)",
     "cue": "enlace en pantalla", "label": "Escanea el código o usa el enlace de abajo"},
    {"id": "pt", "offered": True, "name": "Portuguese", "native": "Português", "engine": "Portuguese (português)",
     "cue": "link na tela", "label": "Escaneie o código ou use o link abaixo"},
    {"id": "fr", "offered": True, "name": "French", "native": "Français", "engine": "French (français)",
     "cue": "lien à l'écran", "label": "Scannez le code ou utilisez le lien ci-dessous"},
    {"id": "de", "offered": True, "name": "German", "native": "Deutsch", "engine": "German (Deutsch)",
     "cue": "Link im Bild", "label": "Scanne den Code oder nutze den Link unten"},
    {"id": "it", "offered": True, "name": "Italian", "native": "Italiano", "engine": "Italian (italiano)",
     "cue": "link sullo schermo", "label": "Inquadra il codice o usa il link qui sotto"},
    {"id": "nl", "offered": True, "name": "Dutch", "native": "Nederlands", "engine": "Dutch (Nederlands)",
     "cue": "link in beeld", "label": "Scan de code of gebruik de link hieronder"},
    {"id": "pl", "offered": False, "name": "Polish", "native": "Polski", "engine": "Polish (polski)",
     "cue": "link na ekranie", "label": "Zeskanuj kod lub użyj linku poniżej"},
    {"id": "ro", "offered": False, "name": "Romanian", "native": "Română", "engine": "Romanian (română)",
     "cue": "linkul de pe ecran", "label": "Scanează codul sau folosește linkul de mai jos"},
    {"id": "cs", "offered": False, "name": "Czech", "native": "Čeština", "engine": "Czech (čeština)",
     "cue": "odkaz na obrazovce", "label": "Naskenuj kód nebo použij odkaz níže"},
    {"id": "sv", "offered": True, "name": "Swedish", "native": "Svenska", "engine": "Swedish (svenska)",
     "cue": "länken på skärmen", "label": "Skanna koden eller använd länken nedan"},
    {"id": "da", "offered": True, "name": "Danish", "native": "Dansk", "engine": "Danish (dansk)",
     "cue": "linket på skærmen", "label": "Scan koden, eller brug linket nedenfor"},
    {"id": "no", "offered": True, "name": "Norwegian", "native": "Norsk", "engine": "Norwegian (norsk)",
     "cue": "lenken på skjermen", "label": "Skann koden, eller bruk lenken nedenfor"},
    {"id": "fi", "offered": True, "name": "Finnish", "native": "Suomi", "engine": "Finnish (suomi)",
     "cue": "linkki ruudulla", "label": "Skannaa koodi tai käytä alla olevaa linkkiä"},
    {"id": "hu", "offered": False, "name": "Hungarian", "native": "Magyar", "engine": "Hungarian (magyar)",
     "cue": "link a képernyőn", "label": "Olvasd be a kódot, vagy használd a lenti linket"},
    {"id": "tr", "offered": False, "name": "Turkish", "native": "Türkçe", "engine": "Turkish (Türkçe)",
     "cue": "ekrandaki bağlantı", "label": "Kodu tara ya da aşağıdaki bağlantıyı kullan"},
    {"id": "id", "offered": True, "name": "Indonesian", "native": "Bahasa Indonesia", "engine": "Indonesian (Bahasa Indonesia)",
     "cue": "tautan di layar", "label": "Pindai kodenya atau pakai tautan di bawah"},
    {"id": "tl", "offered": True, "name": "Filipino", "native": "Filipino", "engine": "Filipino (Tagalog)",
     "cue": "link sa screen", "label": "I-scan ang code o gamitin ang link sa ibaba"},
)
BY_ID = {row["id"]: row for row in LANGUAGES}
# The ready-made Shorts recipes (cr_shorts: Ranking, 3D, photo and Roblox Shorts) write, voice and caption in their own
# pipelines, whose prompts say nothing about a language yet: until they do, they speak their recipe's own language,
# and a Short asked for another one is refused before anything starts rather than made in English.
SHORTS_NOTE = ("{recipe} speaks {own} for now: its Shorts are written, voiced and captioned by their own pipeline. "
               "Make it in {own}, or pick a video recipe or a style written in code for a video in {asked}.")
# Ranking Shorts already speak the language their topic is typed in (its search plan reads it from the topic)
RANKING_NOTE = ("Ranking Shorts speak the language you type the topic in: leave Language as it is and type the topic "
                "in {asked} for a Short in {asked}.")


def shorts_refusal(info, lang_id, recipe="This Shorts recipe", ranking=None) -> str:
    """Why a ready-made Shorts recipe can't make this language ("" when it can: its own language, or none asked).
    ranking: whether it is Ranking Shorts (else read from its file)."""
    if not lang_id or not (isinstance(info, dict) and info.get("format") == "short"):
        return ""
    own = style_language(info)
    if lang_id == own:
        return ""
    if ranking is None:
        ranking = str((info.get("shorts") or {}).get("pipeline") or "") == "ranking"
    return (RANKING_NOTE if ranking else SHORTS_NOTE).format(recipe=recipe, own=name(own), asked=name(lang_id))
_current = {"id": ""}                  # the language of the run in this worker process (one video per process)


def _fold(text) -> str:
    """Lower case without accents: "Español", "espanol" and "ESPAÑOL" are the same word."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
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
            "given.\n\n")


def language_block(lang_id=None) -> str:
    """The line every viewer-facing prompt gets in a video asked for a language other than English ("" otherwise)."""
    row = find(lang_id or _current["id"])
    if not row or row["id"] == "en":
        return ""
    n = row["name"]
    return (f"\n\nLANGUAGE OF THIS VIDEO: {n}. Every word the viewer hears or reads is in {n}: the narration, titles, "
            f"headlines, labels, captions, cards, quotes and any text drawn on screen. These stay in English: "
            "image-generation prompts, stock-footage and picture search queries, tool names, ids and JSON keys.\n")


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
}


# The chapter cards of cinema recipes (cinema.WORDS, keyed by the language's name: English, Czech, Polish, Slovak and
# German only) and the YouTube description's section headings, which the creator pastes under the video.
CHAPTER = {"es": ("CAPÍTULO", "Capítulo"), "pt": ("CAPÍTULO", "Capítulo"), "fr": ("CHAPITRE", "Chapitre"),
           "de": ("KAPITEL", "Kapitel"), "it": ("CAPITOLO", "Capitolo"), "nl": ("HOOFDSTUK", "Hoofdstuk"),
           "sv": ("KAPITEL", "Kapitel"), "da": ("KAPITEL", "Kapitel"), "no": ("KAPITTEL", "Kapittel"),
           "fi": ("LUKU", "Luku"), "id": ("BAB", "Bab"), "tl": ("KABANATA", "Kabanata"), "pl": ("ROZDZIAŁ", "Rozdział"),
           "cs": ("KAPITOLA", "Kapitola"), "ro": ("CAPITOLUL", "Capitolul"), "hu": ("FEJEZET", "Fejezet"),
           "tr": ("BÖLÜM", "Bölüm")}
HEADINGS = {"es": ("IMÁGENES", "CAPÍTULOS", "FUENTES"), "pt": ("IMAGENS", "CAPÍTULOS", "FONTES"),
            "fr": ("IMAGES", "CHAPITRES", "SOURCES"), "de": ("BILDMATERIAL", "KAPITEL", "QUELLEN"),
            "it": ("FILMATI", "CAPITOLI", "FONTI"), "nl": ("BEELDMATERIAAL", "HOOFDSTUKKEN", "BRONNEN"),
            "sv": ("BILDMATERIAL", "KAPITEL", "KÄLLOR"), "da": ("OPTAGELSER", "KAPITLER", "KILDER"),
            "no": ("OPPTAK", "KAPITLER", "KILDER"), "fi": ("KUVAMATERIAALI", "LUVUT", "LÄHTEET"),
            "id": ("CUPLIKAN", "BAB", "SUMBER"), "tl": ("MGA FOOTAGE", "MGA KABANATA", "MGA PINAGMULAN")}


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


def install(engine):
    """For a video asked to speak a language: its style speaks it for the whole run, every prompt that shapes what the
    viewer sees or hears says it, the speech model is told it, and the closing scenes come in it. A video asked nothing
    runs exactly as before."""
    if getattr(engine, "_cr_language", False):
        return
    engine._cr_language = True
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
            try:
                return _start(title, minutes, *args, **kwargs)
            finally:
                _current["id"] = ""
                _put_back()
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
