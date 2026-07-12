"""
Concept Segmenter -- Word-level visual concept extraction for animated explainer videos.

Unlike the cinematic scene_planner.py (which works at sentence level), this module
segments scripts into fine-grained visual concepts mapped to exact word timestamps.
Each concept gets an illustration prompt in a consistent hand-drawn art style.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field

from config import GEMINI_KEY, GEMINI_TEXT_MODEL


@dataclass
class Concept:
    id: int
    text: str
    start_sec: float
    end_sec: float
    duration_sec: float
    illustration_prompt: str
    background_mood: str = "warm_earth"
    has_character: bool = True
    cut_style: str = "crossfade"


BACKGROUND_MOODS = {
    "warm_earth": "warm beige and brown tones, tan background",
    "cool_blue": "cool blue-gray tones, muted blue background",
    "nature_green": "olive green tones, muted green background",
    "dark_serious": "dark brown and gray tones, somber dark background",
    "clean_white": "clean white to light gray background",
    "golden_warm": "warm golden and amber tones, sunset warmth",
    "dusty_rose": "muted rose and mauve tones, gentle warmth",
}

MIN_CONCEPT_DURATION = 1.5
MAX_CONCEPT_DURATION = 7.0
HOOK_CUTOFF_SEC = 30.0

CONCEPT_SEGMENTER_PROMPT = """\
You are a visual concept planner for animated explainer videos in the style of \
simple hand-drawn cartoon illustrations (stick figure documentaries).

You will receive a script with EVERY WORD indexed and timestamped like this:
  [0:0.00] If [1:0.36] you've [2:0.58] ever [3:0.72] taken [4:1.08] a [5:1.30] DNA

Each marker is [word_index:start_seconds]. Your job is to split the script into \
VISUAL CONCEPTS, specifying the EXACT word indices where each concept starts \
and ends.

CRITICAL RULES:

RULE 1 — WORD-INDEX PRECISION:
You MUST specify start_word_idx and end_word_idx for each concept. These are \
the indices from the word data (the numbers before the colon in each marker). \
The concept covers words from start_word_idx through end_word_idx (inclusive).

The visual MUST appear at the EXACT moment the first word of each concept is \
spoken. Use the timestamps to pick semantically meaningful cut points — where \
the visual idea changes.

Do NOT default to sentence boundaries. One sentence often contains 2-4 visual \
concepts. Cut at the exact word where a new visual idea begins.

Example: "they reject modern technology, choosing horse-drawn buggies over \
cars and candlelight over electricity"
→ Concept A (start_word_idx for "they" to end_word_idx for "cars"): stick figure \
  next to horse-drawn buggy with an X over a car
→ Concept B (start_word_idx for "and" to end_word_idx for "electricity"): candle \
  glowing vs electric lightbulb with an X

RULE 2 — PACING (TWO ZONES):

HOOK ZONE (first 30 seconds):
- FAST cuts: 1.5–3 seconds per concept
- This is the hook — visuals must change rapidly to grab the viewer
- Aim for 10-15 concepts in the first 30 seconds
- Every distinct noun, action, or idea gets its OWN illustration
- When in doubt, split into more concepts

BODY ZONE (after 30 seconds):
- Natural pacing: 2–6 seconds per concept
- Let the pacing follow the narration's rhythm
- Faster cuts for lists/enumerations, slower for deep explanations
- Dense info = more concepts, slow storytelling = fewer concepts
- NEVER let a single concept exceed 7 seconds

Do NOT use a fixed duration. The concept length comes from what's being said, \
not from an arbitrary timer.

RULE 3 — ILLUSTRATION PROMPTS:
Write prompts for simple hand-drawn cartoon illustrations.
- Describe what the illustration SHOWS, not what the narration says
- Use visual metaphors for abstract concepts
- ALWAYS describe the COMPOSITION: what is on the left, right, center
- ALWAYS center the main subject in the frame
- Keep the illustration simple — one clear focal point per concept
- Prompts must be SHORT (under 150 chars). Be concise.

RULE 4 — VISUAL IMPACT HIERARCHY:
Pick the most UNIQUE and IDENTIFYING visual from each phrase.
- "horse-drawn buggies over cars" → the buggy (unique), not the car (generic)
- "African kingdoms and trade" → kingdom buildings (specific)

RULE 5 — BACKGROUND MOOD:
Available moods (use for variety, shift with topic changes):
- "warm_earth" — beige/tan (neutral, everyday)
- "cool_blue" — blue/gray (somber, oceanic)
- "nature_green" — olive green (growth, nature)
- "dark_serious" — dark brown (conflict, danger)
- "clean_white" — white/gray (concepts, clarity)
- "golden_warm" — golden/amber (achievement, celebration)
- "dusty_rose" — rose/mauve (culture, community)

Don't repeat the same mood more than 3 concepts in a row.

RULE 6 — CHARACTER PRESENCE:
has_character=true for ~70% of scenes. false for maps, diagrams, wide shots.

RULE 7 — CUT STYLE:
- "crossfade" — within same topic
- "hard_cut" — topic change, dramatic shift

RESPOND WITH ONLY a JSON array. No other text.

Each object:
{{
  "id": <int starting from 0>,
  "start_word_idx": <int, word index where this concept starts>,
  "end_word_idx": <int, word index where this concept ends (inclusive)>,
  "text": "<the script words covered>",
  "illustration_prompt": "<short: what to draw, composition, focal point>",
  "background_mood": "<mood name>",
  "has_character": <true|false>,
  "cut_style": "<crossfade|hard_cut>"
}}
"""


def _format_words_with_timestamps(words: list[dict]) -> str:
    """Format word list with per-word timestamps and indices for precise alignment.

    Every word gets its index and start timestamp so the LLM can make exact cuts.
    Format: [idx:start_sec] word
    """
    parts = []
    for i, w in enumerate(words):
        parts.append(f'[{i}:{w["start"]:.2f}]')
        parts.append(w["word"])
    return " ".join(parts)


def _parse_concepts_json(raw: str) -> list[dict]:
    """Extract JSON array from LLM response."""
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        text = match.group()
    else:
        text = raw

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fixed = re.sub(r',\s*([}\]])', r'\1', text)
    fixed = re.sub(r'//[^\n]*', '', fixed)
    fixed = re.sub(r'/\*.*?\*/', '', fixed, flags=re.DOTALL)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    objects = []
    for m in re.finditer(r'\{[^{}]*\}', text, re.DOTALL):
        try:
            obj = json.loads(m.group())
            objects.append(obj)
        except json.JSONDecodeError:
            try:
                fixed_obj = re.sub(r',\s*}', '}', m.group())
                objects.append(json.loads(fixed_obj))
            except json.JSONDecodeError:
                continue

    if objects:
        return objects

    raise ValueError(f"Could not parse concept JSON: {text[:300]}")


def segment_into_concepts(
    script: str,
    all_words: list[dict],
    style_preset: str = "default",
    niche_hint: str = "",
    lite_mode: bool = False,
) -> list[Concept]:
    """
    Segment a script into visual concepts using word-level timestamps.

    Args:
        script: full script text
        all_words: word timestamps from faster-whisper [{"word", "start", "end"}, ...]
        style_preset: art style preset name
        niche_hint: optional hint about the video's niche/topic
        lite_mode: fewer concepts (trial) — less illustration COGS/latency

    Returns:
        list of Concept objects with exact timing and illustration prompts
    """
    from core.atlas_llm import generate_text, has_atlas

    if not GEMINI_KEY and not has_atlas():
        raise ValueError("ATLASCLOUD_KEY or GEMINI_KEY required for concept segmentation")
    if not all_words:
        raise ValueError("No word timestamps provided")

    words_formatted = _format_words_with_timestamps(all_words)

    total_duration = all_words[-1]["end"] - all_words[0]["start"]

    hook_dur = min(HOOK_CUTOFF_SEC, total_duration)
    body_dur = max(0, total_duration - hook_dur)
    # Lite: longer body shots → fewer AI images (biggest cost lever)
    if lite_mode:
        hook_concepts = max(2, int(hook_dur / 3.5))
        body_concepts = max(1, int(body_dur / 7.0)) if body_dur > 0 else 0
    else:
        hook_concepts = max(3, int(hook_dur / 2.5))
        body_concepts = max(1, int(body_dur / 4.0)) if body_dur > 0 else 0
    target_concepts = hook_concepts + body_concepts

    context = ""
    if niche_hint:
        context = f"\nVIDEO TOPIC: {niche_hint}\n"

    user_msg = (
        f"FULL SCRIPT:\n{script}\n\n"
        f"WORD-LEVEL TIMESTAMPS:\n{words_formatted}\n\n"
        f"Total duration: {total_duration:.1f}s\n"
        f"HOOK ZONE (0-{hook_dur:.0f}s): ~{hook_concepts} concepts "
        f"(fast cuts, 1.5-3s each)\n"
        f"BODY ZONE ({hook_dur:.0f}s-{total_duration:.0f}s): ~{body_concepts} concepts "
        f"(natural pacing, 2-6s each)\n"
        f"Total target: ~{target_concepts} concepts\n"
        f"{context}\n"
        f"Segment into visual concepts now. JSON array only."
    )

    prompt = CONCEPT_SEGMENTER_PROMPT

    print(f"[concept_segmenter] Segmenting {total_duration:.1f}s script into ~{target_concepts} concepts...")

    concept_dicts = None
    last_error = None

    for attempt in range(3):
        try:
            import config as _cfg
            model = getattr(_cfg, "CONCEPT_SEGMENTER_MODEL", GEMINI_TEXT_MODEL)
            raw = generate_text(
                prompt + "\n\n" + user_msg,
                model=model,
                max_tokens=8192,
            )
            concept_dicts = _parse_concepts_json(raw)
            break
        except Exception as e:
            last_error = e
            print(f"  [concept_segmenter] Attempt {attempt + 1} failed: {e}")

    if concept_dicts is None:
        raise ValueError(f"Concept segmentation failed after 3 attempts: {last_error}")

    concepts = _build_concepts(concept_dicts, all_words)
    concepts = _enforce_duration_constraints(concepts)

    print(f"[concept_segmenter] Final: {len(concepts)} concepts, "
          f"avg {sum(c.duration_sec for c in concepts)/len(concepts):.1f}s, "
          f"moods: {_mood_summary(concepts)}")

    return concepts


def _build_concepts(concept_dicts: list[dict], all_words: list[dict]) -> list[Concept]:
    """Build Concept objects from parsed JSON using word indices for precise timing."""
    if not all_words:
        return []

    audio_start = all_words[0]["start"]
    audio_end = all_words[-1]["end"]
    n_words = len(all_words)

    concepts: list[Concept] = []

    for i, cd in enumerate(concept_dicts):
        start_idx = cd.get("start_word_idx")
        end_idx = cd.get("end_word_idx")

        if start_idx is not None and end_idx is not None:
            start_idx = max(0, min(int(start_idx), n_words - 1))
            end_idx = max(0, min(int(end_idx), n_words - 1))
            if end_idx < start_idx:
                continue
            start = all_words[start_idx]["start"]
            end = all_words[end_idx]["end"]
        else:
            start = float(cd.get("start_sec", 0))
            end = float(cd.get("end_sec", 0))
            start = max(start, audio_start)
            end = min(end, audio_end)
            start = _snap_to_nearest_word(start, all_words)
            end = _snap_to_nearest_word(end, all_words, prefer_end=True)

        if end <= start:
            continue
        if end - start < 1.0:
            continue

        mood = cd.get("background_mood", "warm_earth")
        if mood not in BACKGROUND_MOODS:
            mood = "warm_earth"

        concepts.append(Concept(
            id=i,
            text=cd.get("text", ""),
            start_sec=round(start, 3),
            end_sec=round(end, 3),
            duration_sec=round(end - start, 3),
            illustration_prompt=cd.get("illustration_prompt", ""),
            background_mood=mood,
            has_character=cd.get("has_character", True),
            cut_style=cd.get("cut_style", "crossfade"),
        ))

    if not concepts:
        return []

    concepts.sort(key=lambda c: c.start_sec)

    for i in range(1, len(concepts)):
        if concepts[i].start_sec < concepts[i - 1].end_sec:
            concepts[i].start_sec = concepts[i - 1].end_sec
            concepts[i].duration_sec = concepts[i].end_sec - concepts[i].start_sec

    concepts = [c for c in concepts if c.duration_sec >= 1.0]

    # Close gaps: extend each concept's end to the next concept's start.
    # The previous image stays on screen through any audio pause, which
    # keeps total clip duration equal to total audio duration.
    for i in range(len(concepts) - 1):
        if concepts[i].end_sec < concepts[i + 1].start_sec:
            concepts[i].end_sec = concepts[i + 1].start_sec
            concepts[i].duration_sec = concepts[i].end_sec - concepts[i].start_sec

    if concepts and concepts[-1].end_sec < audio_end - 0.1:
        concepts[-1].end_sec = audio_end
        concepts[-1].duration_sec = concepts[-1].end_sec - concepts[-1].start_sec

    if concepts and concepts[0].start_sec > audio_start + 0.1:
        concepts[0].start_sec = audio_start
        concepts[0].duration_sec = concepts[0].end_sec - concepts[0].start_sec

    for i, c in enumerate(concepts):
        c.id = i

    return concepts


def _snap_to_nearest_word(
    target_sec: float, words: list[dict], prefer_end: bool = False
) -> float:
    """Snap a timestamp to the nearest word boundary (fallback for legacy format)."""
    best_dist = float("inf")
    best_time = target_sec

    for w in words:
        t = w["end"] if prefer_end else w["start"]
        dist = abs(t - target_sec)
        if dist < best_dist:
            best_dist = dist
            best_time = t

    return best_time


def _enforce_duration_constraints(concepts: list[Concept]) -> list[Concept]:
    """Split oversized concepts and merge undersized ones."""
    result: list[Concept] = []

    for c in concepts:
        if c.duration_sec > MAX_CONCEPT_DURATION:
            n_chunks = max(2, round(c.duration_sec / 3.5))
            chunk_dur = c.duration_sec / n_chunks
            for j in range(n_chunks):
                start = c.start_sec + j * chunk_dur
                end = start + chunk_dur if j < n_chunks - 1 else c.end_sec
                result.append(Concept(
                    id=0,
                    text=c.text,
                    start_sec=start,
                    end_sec=end,
                    duration_sec=end - start,
                    illustration_prompt=c.illustration_prompt,
                    background_mood=c.background_mood,
                    has_character=c.has_character,
                    cut_style="crossfade" if j > 0 else c.cut_style,
                ))
        else:
            result.append(c)

    merged: list[Concept] = []
    for c in result:
        in_hook = c.start_sec < HOOK_CUTOFF_SEC
        min_dur = MIN_CONCEPT_DURATION if not in_hook else 1.2
        if c.duration_sec < min_dur and merged:
            prev = merged[-1]
            prev.end_sec = c.end_sec
            prev.duration_sec = prev.end_sec - prev.start_sec
            if c.illustration_prompt and not prev.illustration_prompt:
                prev.illustration_prompt = c.illustration_prompt
        else:
            merged.append(c)

    for i, c in enumerate(merged):
        c.id = i

    return merged


def _mood_summary(concepts: list[Concept]) -> str:
    """Summary of mood distribution for logging."""
    counts: dict[str, int] = {}
    for c in concepts:
        counts[c.background_mood] = counts.get(c.background_mood, 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
