"""Disambiguate animal noun phrases in the original subject checker.

Some human role words are also animal modifiers. A ``worker bee`` is a bee,
not a request for a human worker. This narrow adapter leaves the original
head-noun matching, geometry checks and visual reviews in charge of proving
that the animal is present. It never adds subjects or approves a rendered shot.
"""
from __future__ import annotations

import functools
import importlib
import re

# Ordinary animal head nouns, not scenes, titles, species-specific rendering
# recipes or alternate human aliases. Unknown nouns keep the original result.
ANIMAL_HEADS = frozenset("""
ant bee beetle butterfly moth wasp termite fly insect spider scorpion worm
snail slug crab shrimp prawn lobster fish shark ray eel octopus squid
bird pigeon dove crow raven eagle hawk falcon owl duck goose swan penguin
parrot chicken turkey quail peacock ostrich emu
cat dog wolf fox lion tiger leopard bear bat rat mouse mole rabbit hare
horse donkey mule cow cattle goat sheep pig boar deer camel llama alpaca
elephant rhinoceros hippopotamus monkey ape gorilla chimpanzee lemur
dolphin whale seal otter walrus turtle tortoise lizard snake crocodile
alligator frog toad
""".split())

# Only inspect the subject's noun phrase. Following descriptions may name
# other things ("worker with a bee" is still a worker).
_TAIL = re.compile(r"\b(?:with|without|in|on|at|from|into|onto|over|under|above|"
                   r"below|behind|beneath|beside|near|that|which|who|whose|"
                   r"for|of|to|by|than|like|as)\b", re.I)
_COORDINATORS = frozenset({"and", "or", "nor", "versus", "vs"})


def _animal_modifier_phrase(text, storyboard):
    prefix = _TAIL.split(str(text or ""), maxsplit=1)[0]
    tokens = storyboard._toks(prefix)
    if len(tokens) < 2 or any(t in _COORDINATORS for t in tokens):
        return False
    head = tokens[-1]
    if head not in ANIMAL_HEADS or storyboard._head(text) != head:
        return False
    # Adjacency matters: "worker bee" is a compound, while "worker holding
    # a bee" must not lose its human requirement. Use the original alias
    # vocabulary rather than broadening which words count as people.
    aliases = (storyboard.THINGS.get("people") or {}).get("aka") or []
    before = tokens[:-1]
    for alias in aliases:
        words = storyboard._toks(alias)
        if words and len(words) <= len(before) and before[-len(words):] == words:
            return True
    return False


def install(engine=None):
    """Wrap the shared original checker once; keep source and boards untouched."""
    try:
        storyboard = importlib.import_module("threed").SB
    except (ImportError, AttributeError):
        return None
    original = getattr(storyboard, "_canon", None)
    if (not callable(original) or not callable(getattr(storyboard, "_toks", None))
            or not callable(getattr(storyboard, "_head", None))
            or not isinstance(getattr(storyboard, "THINGS", None), dict)):
        return None
    if getattr(original, "__cr_subject_identity__", False):
        return original

    @functools.wraps(original)
    def canonical(text):
        result = original(text)
        # Never discard a known distinct canonical (snake, vehicle, weapon,
        # place, etc.). Only the ambiguous human-only answer is corrected.
        if result == ["people"] and _animal_modifier_phrase(text, storyboard):
            return []  # The original _terms_of now falls back to the noun head.
        return result

    canonical.__cr_subject_identity__ = True
    storyboard._canon = canonical
    return canonical
