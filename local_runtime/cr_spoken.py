"""Only spoken words reach the voice: a sound cue the script writer slipped into the narration is taken out.

A 1-minute story in a cloned style had "Growth sound." in its script, between two spoken sentences, and the narrator
read it aloud (2026-10-26). The engine's _clean_script already takes out the model's preamble, notes and markdown;
this adds one careful rule on top of it. A sentence is a cue only when it is short (one or two words) and names a
sound effect ("Growth sound.", "Whoosh SFX."), or when it says "sound effect" or "SFX" anywhere. Longer sentences
about a sound ("A strange sound came from the hull.") are the story and stay.
"""
from __future__ import annotations

import re
import unicodedata

_SENTENCE = re.compile(r"[^.!?]+[.!?]+[\"'”’)]*\s*|[^.!?]+$")
_CUE_WORD = re.compile(r"(?i)^(?:sound|sounds|sfx|jingle|stinger|chime)$")
_CUE_ANYWHERE = re.compile(r"(?i)\b(?:sound effects?|sfx)\b")


def is_cue(sentence: str) -> bool:
    words = re.findall(r"[A-Za-z'’-]+", sentence)
    if not words:
        return False
    if _CUE_ANYWHERE.search(sentence) and len(words) <= 6:
        return True
    return len(words) <= 2 and bool(_CUE_WORD.match(words[-1]))


def strip_cues(text: str) -> str:
    """The script with sound-cue sentences taken out; paragraphs and everything else exactly as they were."""
    paragraphs = re.split(r"(\n\s*\n)", str(text or ""))
    kept = []
    for piece in paragraphs:
        if not piece.strip() or re.fullmatch(r"\n\s*\n", piece):
            kept.append(piece)
            continue
        sentences = _SENTENCE.findall(piece)
        spoken = [s for s in sentences if not is_cue(s)]
        kept.append("".join(spoken).strip() if len(spoken) != len(sentences) else piece)
    out = "".join(kept)
    return re.sub(r"\n\s*\n(\s*\n)+", "\n\n", out).strip()


_LEAD_IN = re.compile(r"[^\n]{3,200}:[\s*_\"'”’»)]*")
# the word for the script itself in a lead-in, accents folded: es guion/narracion, pt roteiro/narracao, fr script/
# texte, de Skript/Sprechertext, it copione/sceneggiatura/testo, nl/sv/da/no manus, fi kasikirjoitus, id naskah, tl iskrip,
# hi स्क्रिप्ट/पटकथा (by their cr_language.latin keys: skript, ptktha)
_SCRIPT_WORD = re.compile(r"\b(?:guion|guiones|libreto|narracion|roteiro|narracao|script|scripts|texte|skript|"
                          r"sprechertext|copione|sceneggiatura|testo|draaiboek|manus|manuset|manuskript|kasikirjoitus|"
                          r"naskah|iskrip|ptktha)\b")


def strip_lead_in(text: str) -> str:
    """A video in another language (cr_language): the writer's own lead-in ("Aquí tienes el guion:", "Voici le
    script :") is taken out. The engine's cleaner knows only English ones, so the narrator read it aloud. A lead-in is
    the first paragraph, one short line (at most 16 words) that ends in a colon and names the script itself; a story's
    own opening ("Esto es lo que pasó:") stays."""
    try:
        import cr_language
        if cr_language.current() in ("", "en"):
            return text
    except ImportError:
        return text
    parts = re.split(r"(\n\s*\n)", str(text or "").lstrip(), maxsplit=1)
    first = parts[0].strip()
    first_key = cr_language.latin(first)                 # Hindi: "यह रही स्क्रिप्ट:" is keyed "yh rhi skript:"
    folded = "".join(c for c in unicodedata.normalize("NFKD", first_key.casefold()) if not unicodedata.combining(c))
    if (len(parts) == 3 and "\n" not in first and _LEAD_IN.fullmatch(first) and len(first.split()) <= 16
            and _SCRIPT_WORD.search(folded)):
        return parts[2].lstrip()
    return text


def install(engine):
    """Wrap the engine's script cleaner (make_video._clean_script) once."""
    original = getattr(engine, "_clean_script", None)
    if original is None or getattr(original, "_cr_spoken", False):
        return

    def _clean_script(text):
        return strip_cues(strip_lead_in(original(text)))
    _clean_script._cr_spoken = True
    engine._clean_script = _clean_script
