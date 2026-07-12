"""
LLM-powered search query generation for B-roll segments.
Takes script segments and generates optimal image search queries using
visual-attribute decomposition: each segment is broken into subject, era,
tone, and format -- then composed into a targeted search string.
Uses Gemini Flash for cost efficiency (~$0.01 per video).
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass
from config import GEMINI_KEY, GEMINI_TEXT_MODEL


@dataclass
class SlotQuery:
    slot_id: int
    # Visual attribute decomposition
    subject: str = ""       # "Henry Kissinger", "rotary telephone", "dark alley"
    era: str = ""           # "1970s", "medieval", "modern", "futuristic"
    tone: str = ""          # "serious, authoritative", "playful, warm"
    format_hint: str = ""   # "press photo, official portrait", "stock, abstract"
    # Composed queries (built from attributes)
    primary_query: str = ""
    fallback_query: str = ""
    source_hint: str = "any"    # wikimedia | stock | any
    entity_type: str = "mood"   # person | place | event | object | mood
    style_hint: str = "neutral" # historical_bw | cinematic_dark | modern_color | neutral


SYSTEM_PROMPT = """\
You generate image search queries for documentary-style B-roll video segments.

YOUR TECHNIQUE: VISUAL ATTRIBUTE DECOMPOSITION.
Instead of guessing one search string, you DECOMPOSE each segment into 4 visual \
attributes, then COMPOSE a search query from those attributes. Each attribute pulls \
search results in a different direction, creating a highly targeted intersection.

THE 4 ATTRIBUTES:
1. SUBJECT — the specific person, place, thing, or concept. For named entities, \
use their actual name. For generic concepts, describe the visual subject.
2. ERA — the time period the image should feel like it's from. "1970s", "1950s \
Hollywood", "Victorian era", "modern 2020s", "ancient Rome", "futuristic".
3. TONE — the emotional/aesthetic feel. "serious formal", "playful candid", \
"dark dramatic", "warm nostalgic", "clinical corporate".
4. FORMAT — the type of photograph/image. "press photo", "official portrait", \
"publicity still", "candid snapshot", "stock photography", "archival document", \
"vintage illustration", "aerial view".

BEFORE YOU START — THREE STEPS TO ANCHOR THE ENTIRE SCRIPT:

STEP 1 — DETERMINE THE STORY. Read the full script and identify the SPECIFIC \
story, event, or topic being told. This is your NARRATIVE ANCHOR. Every single \
segment must be understood in the context of this story. Examples:
  - "Apollo 11 moon landing, July 1969"
  - "Henry Kissinger's role in Cold War diplomacy, 1969-1977"
  - "The rise of Tesla and Elon Musk, 2003-2024"

STEP 2 — DETERMINE THE ERA. Identify the time period. ALL images must feel like \
they are FROM that era. A story about the 1969 moon landing needs 1960s NASA \
archival photography, not modern stock photos of space.

STEP 3 — DETERMINE THE KEY ENTITIES. List the real people, places, organizations, \
and events mentioned in the script. These are your search anchors. Every segment \
that mentions or relates to one of these entities MUST include the entity name AND \
the story context in the query.

THE NARRATIVE ANCHOR RULE — THIS IS THE MOST IMPORTANT RULE:
Every query must be anchored to the specific story. Generic descriptions are NEVER \
acceptable when the script is telling a specific story.

BAD (no story context):
  "three astronauts capsule space" — could be ANY space mission
  "engineers working late office blueprints" — could be any engineers
  "man on television news anchor broadcast" — could be any broadcast

GOOD (anchored to Apollo 11):
  "Apollo 11 astronauts Armstrong Aldrin Collins crew 1969" — specifically Apollo 11
  "NASA Apollo Mission Control Houston engineers 1960s" — NASA during Apollo
  "Walter Cronkite CBS moon landing broadcast 1969" — Cronkite covering Apollo 11

Even when the script uses vague language ("a man walked on the moon"), YOU know \
from the full script that this is about Apollo 11. So the query MUST say "Apollo 11" \
or "Neil Armstrong moon landing 1969", NOT "man walking on moon."

EXAMPLE — Script about the Apollo 11 moon landing:

Segment: "But behind that moment was a decade of relentless work."
→ subject: "NASA Apollo program development" (NOT "people working hard")
→ era: "1960s"
→ tone: "determined, industrious"
→ format: "archival photograph"
→ query: "NASA Apollo program development 1960s archival photograph"

Segment: "Three astronauts climbed into a capsule no bigger than a car."
→ subject: "Apollo 11 crew Armstrong Aldrin Collins command module" (NOT "astronauts capsule")
→ era: "1969"
→ tone: "heroic, tense"
→ format: "NASA archival photograph"
→ query: "Apollo 11 astronauts crew command module 1969 NASA photograph"

Segment: "They built the Saturn V rocket, the most powerful machine ever constructed."
→ subject: "Saturn V rocket Apollo" (NOT "big rocket")
→ era: "1960s"
→ tone: "awe, engineering marvel"
→ format: "NASA archival photograph, launch"
→ query: "Saturn V rocket Apollo NASA 1960s launch photograph"

CRITICAL RULES:

1. NAMED ENTITIES: If the script mentions a real person, place, or event, the \
subject MUST include the actual name AND the story context. "Neil Armstrong" in \
an Apollo 11 script → "Neil Armstrong Apollo 11 moon landing 1969".

2. IMPLICIT REFERENCES: When the script says "he" or "the astronauts" or "the \
rocket" — figure out WHO or WHAT from context and use the real name. "He stepped \
off the ladder" → subject is "Neil Armstrong", not "a man on a ladder."

3. TIME PERIOD CONSISTENCY: Every image must match the era of the story.

4. source_hint:
   - "wikimedia" = for named entities AND for era-appropriate archival imagery. \
USE THIS for any real event, person, place, or historical content.
   - "stock" = ONLY for mood/abstract content where era doesn't matter
   - "any" = when either could work

5. primary_query: Compose from STORY CONTEXT + subject + era + format.

6. fallback_query: THIS IS CRITICAL. The fallback is used when the primary \
query returns no good results. It should describe the VISUAL CONCEPT the \
viewer should see, without relying on specific entity names. Think of it as: \
"if I can't find a photo of the exact person, what visual would work instead?"
Examples:
  - Primary: "Walter Cronkite CBS news broadcast Apollo 11 1969"
  - Fallback: "television news anchor broadcasting 1960s studio vintage"
  - Primary: "John F Kennedy speaking Congress 1961 speech"
  - Fallback: "president speaking before Congress podium 1960s government"
  - Primary: "families watching moon landing television 1969"
  - Fallback: "family watching television set 1960s living room vintage"
The fallback must ALWAYS keep the era and the visual concept, but drop \
the specific entity name. It should work on Pexels as a stock photo search.

7. entity_type: person | place | event | object | mood

8. style_hint: "historical_bw" for anything pre-1980s, "cinematic_dark" for \
modern dark docs, "modern_color" for contemporary, "neutral" if unsure.

9. subject: MUST include the ACTION CONTEXT, not just the name. \
If the script says "Kennedy speaking before Congress," the subject is \
"President Kennedy speaking Congress address 1961", not just "Kennedy." \
If it says "Cronkite took off his glasses on live television," the subject is \
"Walter Cronkite CBS television broadcast glasses reaction." \
The action context is what lets the system distinguish between different \
images of the same person.

Return ONLY a JSON array, one object per segment. No markdown fences."""


def generate_queries(
    segments: list[dict],
    full_script: str = "",
    niche_profile: dict | None = None,
) -> list[SlotQuery]:
    """
    Generate search queries for each segment using Gemini Flash.
    segments: list of {"id": int, "text": str}
    niche_profile: optional NicheProfile dict with sample_queries for few-shot learning
    """
    from core.atlas_llm import generate_text, has_atlas

    if not GEMINI_KEY and not has_atlas():
        raise ValueError("ATLASCLOUD_KEY or GEMINI_KEY required for query generation")

    segment_list = "\n".join(
        f"Segment {s['id']}: \"{s['text']}\""
        for s in segments
    )

    few_shot = ""
    if niche_profile and niche_profile.get("sample_queries"):
        examples = niche_profile["sample_queries"][:5]
        few_shot = "\n\nHere are example queries from a reference video in this niche:\n"
        for ex in examples:
            few_shot += (
                f"  Narration: \"{ex.get('narration_text', '')}\"\n"
                f"  → subject: \"{ex.get('subject', '')}\"\n"
                f"  → era: \"{ex.get('era', '')}\"\n"
                f"  → tone: \"{ex.get('tone', '')}\"\n"
                f"  → format: \"{ex.get('format_hint', '')}\"\n"
                f"  → query: \"{ex.get('composed_query', '')}\"\n\n"
            )
        style = niche_profile.get("visual_style", {})
        if style:
            few_shot += (
                f"Visual style for this niche: era={style.get('era', 'modern')}, "
                f"tone={style.get('tone', 'neutral')}, "
                f"palette={style.get('palette', 'natural')}, "
                f"grain={style.get('grain', 'clean')}\n"
            )

    user_prompt = f"""Full script context:
\"\"\"{full_script}\"\"\"
{few_shot}
Segments to generate queries for:
{segment_list}

Generate a JSON array with one object per segment. Each object must have:
- slot_id (int)
- subject (str) — the specific visual subject
- era (str) — time period for this image
- tone (str) — emotional/aesthetic feel
- format_hint (str) — type of photograph/image
- primary_query (str) — composed from subject + era + tone + format
- fallback_query (str) — broader, keeping era + format
- source_hint ("wikimedia" | "stock" | "any")
- entity_type ("person" | "place" | "event" | "object" | "mood")
- style_hint ("historical_bw" | "cinematic_dark" | "modern_color" | "neutral")"""

    text = generate_text(
        SYSTEM_PROMPT + "\n\n" + user_prompt,
        model=GEMINI_TEXT_MODEL,
        max_tokens=8192,
    ).strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            items = json.loads(match.group())
        else:
            raise ValueError(f"Could not parse LLM response as JSON: {text[:200]}")

    queries = [
        SlotQuery(
            slot_id=item.get("slot_id", i),
            subject=item.get("subject", ""),
            era=item.get("era", ""),
            tone=item.get("tone", ""),
            format_hint=item.get("format_hint", ""),
            primary_query=item.get("primary_query", ""),
            fallback_query=item.get("fallback_query", ""),
            source_hint=item.get("source_hint", "any"),
            entity_type=item.get("entity_type", "mood"),
            style_hint=item.get("style_hint", "neutral"),
        )
        for i, item in enumerate(items)
    ]

    for q in queries:
        print(f"  [query] Slot {q.slot_id}: "
              f"subject=\"{q.subject}\", era=\"{q.era}\", "
              f"hint={q.source_hint}, type={q.entity_type}, "
              f"q=\"{q.primary_query}\"")

    return queries
