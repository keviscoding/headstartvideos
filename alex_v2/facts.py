"""Your fact sheet: the ONLY source of facts about you that v2 may use.

Alex's lines are full of Alex's life (his husky Rhaegar, his Brickell
balcony, his podcast, being Russian, "sexologist"). v2 uses his MOVES, never
his biography. Any line that needs a fact you haven't filled in here is not
allowed, and the critic rejects replies that mention Alex's life.

Edit ``<data_dir>/facts.json`` (or use the Alex v2 page). Unknown = empty.
"""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

from .settings import data_dir

TEMPLATE: dict[str, Any] = {
    "first_name": "",
    "age": None,
    "height": "",                       # e.g. "6'0" / "183cm"
    "city": "",                         # e.g. "London"
    "neighbourhood": "",                # e.g. "Shoreditch"
    "timezone": "Europe/London",
    "languages": ["English"],           # languages you can actually text in
    "cultures_with_real_ties": [],      # countries you've genuinely lived in / spent time in
    "work_line": "",                    # truthful "what I'm finishing up", e.g. "finishing up some work"
    "activities": ["gym"],              # truthful things you do, e.g. ["gym", "boxing"]
    "pet": {"has_pet": False, "type": "", "name": ""},
    "own_place": True,                  # can she come to yours?
    "roommates": False,                 # never claim to live alone if True
    "place_feature": "",                # e.g. "balcony", "rooftop terrace" ("" = don't mention)
    "drinks_at_home": ["wine"],         # what you actually have in
    "near_bars": [],                    # [{"name": "The Wharf", "walk_min": 5}]
    "free_evenings": ["Thu", "Fri", "Sat"],
    "constraints": "",                  # e.g. "early starts Mon-Wed"
    "travel": {"away_now": False, "back_on": "", "leaving_on": ""},
    "tattoos": False,
    "car": False,                       # you have a car (never claim one otherwise)
    "instagram": "",                    # handle you're happy to share ("" = don't offer)
    "profile_gate": "flirty",           # vanilla | flirty | sexual (how sexual YOUR profile/bio is)
    "bio_text": "",                     # your actual dating bio
    "photo_notes": "",                  # what your photos show (helps openers/callbacks)
    "sexual_comfort": 60,               # 0..100 personal ceiling
    "use_cuming_typo": False,           # Alex's "Cuming" arrival joke
    "banned_phrases": [],               # anything you never want sent
    "notes_for_v2": "",                 # anything else true about you
}

# Alex's biography: never allowed unless it's also true of you.
ALEX_BIO_TERMS: dict[str, str] = {
    r"\brhaegar\b": "_never",
    r"\bhusky\b": "pet.type",
    r"\bbrickell\b": "neighbourhood",
    r"\bsexologist\b": "_never",
    r"\bsex (and|&) dating coach\b": "_never",
    r"\bpodcast": "work_line",
    r"\b(finishing|finished|working on) (up )?(a |some |new )?videos?\b": "work_line",
    r"\bbelarus\b": "cultures_with_real_ties",
    r"\brussian?\b": "languages",
    r"\bredbar\b": "near_bars",
    r"\badikkt\b": "near_bars",
    r"\be11even\b": "_never",
    r"\bthe w\b": "near_bars",
    r"\bjacuzzi\b": "place_feature",
    r"\bsauna\b": "place_feature",
    r"\bbalcony\b": "place_feature",
    r"\bpatio\b": "place_feature",
    r"\bgringo espa[nñ]ol\b": "languages",
    r"\bmiami\b": "city",
    r"\bmale stripp(er|ing)\b": "_never",
    r"\bonlyfans\b": "_never",
}


def facts_path():
    return data_dir() / "facts.json"


def load() -> dict[str, Any]:
    path = facts_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    out = copy.deepcopy(TEMPLATE)
    for k, v in data.items():
        if k in out:
            out[k] = v
    return out


def save(data: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in data.items() if k in TEMPLATE}
    merged = {**load(), **clean}
    tmp = facts_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    os.replace(tmp, facts_path())
    return merged


def _field_text(facts: dict[str, Any], field: str) -> str:
    if field == "_never":
        return ""
    cur: Any = facts
    for part in field.split("."):
        cur = cur.get(part, "") if isinstance(cur, dict) else ""
    if isinstance(cur, list):
        return " ".join(json.dumps(x) if isinstance(x, dict) else str(x) for x in cur).lower()
    return str(cur or "").lower()


def forbidden_bio_patterns(facts: dict[str, Any]) -> list[str]:
    """Alex-bio regexes that are NOT backed by your facts (so must not appear)."""
    out = []
    for pat, field in ALEX_BIO_TERMS.items():
        backing = _field_text(facts, field)
        if field != "_never" and backing and re.search(pat, backing, re.I):
            continue
        out.append(pat)
    return out


def near_bar(facts: dict[str, Any]) -> str:
    bars = facts.get("near_bars") or []
    if bars and isinstance(bars[0], dict) and bars[0].get("name"):
        return bars[0]["name"]
    if bars and isinstance(bars[0], str):
        return bars[0]
    return ""


def render_for_prompt(facts: dict[str, Any]) -> str:
    """Compact, explicit fact list + what is UNKNOWN (never claim)."""
    known, unknown = [], []

    def add(label: str, value: Any, missing: str):
        if value in (None, "", [], False) and not isinstance(value, bool):
            unknown.append(missing)
        elif isinstance(value, bool):
            known.append(f"{label}: {'yes' if value else 'no'}")
        else:
            known.append(f"{label}: {value}")

    add("first name", facts.get("first_name"), "your name")
    add("age", facts.get("age"), "your age")
    add("height", facts.get("height"), "your height")
    loc = ", ".join(x for x in [facts.get("neighbourhood"), facts.get("city")] if x)
    add("lives in", loc, "where you live")
    add("languages", ", ".join(facts.get("languages") or []), "other languages")
    add("real ties to", ", ".join(facts.get("cultures_with_real_ties") or []), "any countries/cultures")
    add("what you can say you're finishing up", facts.get("work_line"), "your work details")
    add("activities", ", ".join(facts.get("activities") or []), "hobbies")
    pet = facts.get("pet") or {}
    if pet.get("has_pet"):
        known.append(f"pet: {pet.get('type') or 'a pet'}" + (f" named {pet['name']}" if pet.get("name") else ""))
    else:
        unknown.append("a pet (you have NO pet)")
    known.append(f"can host at your place: {'yes' if facts.get('own_place') else 'no'}")
    if facts.get("roommates"):
        known.append("you have roommates (never say you live alone)")
    if facts.get("place_feature"):
        known.append(f"your place has: {facts['place_feature']}")
    else:
        unknown.append("any balcony/patio/jacuzzi (don't mention one)")
    if facts.get("drinks_at_home"):
        known.append(f"drinks you have in: {', '.join(facts['drinks_at_home'])}")
    nb = near_bar(facts)
    add("bar near you", nb, "a specific bar name (say 'a bar near me')")
    if facts.get("free_evenings"):
        known.append(f"usually free: {', '.join(facts['free_evenings'])}")
    if facts.get("constraints"):
        known.append(f"constraints: {facts['constraints']}")
    tr = facts.get("travel") or {}
    if tr.get("away_now"):
        known.append(f"currently away, back on {tr.get('back_on') or 'unknown'}")
    if tr.get("leaving_on"):
        known.append(f"leaving town on {tr['leaving_on']}")
    known.append(f"tattoos: {'yes' if facts.get('tattoos') else 'no'}")
    if facts.get("instagram"):
        known.append(f"instagram you can share: {facts['instagram']}")
    if facts.get("notes_for_v2"):
        known.append(f"other true facts: {facts['notes_for_v2']}")
    txt = "KNOWN FACTS ABOUT YOU (only these may be stated):\n- " + "\n- ".join(known)
    if unknown:
        txt += "\nUNKNOWN / NOT TRUE (never claim or invent): " + "; ".join(unknown)
    return txt
