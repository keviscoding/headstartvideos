"""
Concept Segmenter -- Word-level visual concept extraction for animated explainer videos.

Unlike the cinematic scene_planner.py (which works at sentence level), this module
segments scripts into fine-grained visual concepts mapped to exact word timestamps.
Each concept gets an illustration prompt in a consistent hand-drawn art style.
"""

from __future__ import annotations
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    section_topic: str = ""


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

RULE 3 — ILLUSTRATION PROMPTS (MOST IMPORTANT):
Write prompts for simple hand-drawn cartoon ILLUSTRATIONS — never captions.

HARD BANS:
- NEVER put spoken narration, quotes, or full sentences in the prompt
- NEVER ask for writing, labels, titles, captions, signs with words, chalkboards
  with writing, speech bubbles with words, or readable clock numerals
  (use blank dials / hourglasses / unmarked gauges instead)
- NEVER describe only an abstract action with no setting
  (bad: "stick figure floating" / "person falling")
- NEVER request clothed humans, mannequins, or multiple different characters —
  only the same stick figure (or no character)

REQUIRED in EVERY illustration_prompt:
1. SETTING — the concrete place/topic of THIS beat, anchored to the video's
   ongoing subject (if the paragraph is about the Moon, show the Moon; if
   about Earth's core, show a cutaway of Earth — not a blank void)
2. ACTION — what the stick figure (if any) is DOING
3. 2–3 PROPS / landmarks that make the beat readable without audio
   (ladder, tunnel through Earth, Moon crater, gravity arrows as SHAPES
   not text, core glow, spacesuit, etc.)
4. COMPOSITION — what is left / center / right

Keep prompts SHORT but dense (under 180 chars). Style:
  "Stick figure falling through tunnel cut through Earth cross-section,
   mantle layers visible, glowing core below, rocks flying, centered"

NOT: "As you plummet deeper something strange happens to gravity"
NOT: any prompt that quotes the narration

If the beat is abstract (velocity/zero/math), CONVERT it into a physical
metaphor with props (e.g. figure slowing as it exits a tunnel, sandglass,
motion lines fading) — never a caption card.

RULE 3b — SECTION CONTEXT:
Before writing body-zone prompts, infer the current SECTION TOPIC from the
surrounding script (e.g. "falling through Earth", "on the Moon", "at the
core"). Every illustration in that section MUST visually include that topic's
setting/props so consecutive images feel like one continuous story, not
random unrelated doodles.

RULE 4 — VISUAL IMPACT HIERARCHY:
Pick the most UNIQUE and IDENTIFYING visual from each phrase.
- "horse-drawn buggies over cars" → the buggy (unique), not the car (generic)
- "African kingdoms and trade" → kingdom buildings (specific)
- "float endlessly" while discussing the Moon → figure floating ABOVE THE MOON
  with craters and stars — never a figure floating in empty beige space

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
  "illustration_prompt": "<setting + action + 2-3 props + composition; NO spoken words>",
  "section_topic": "<short ongoing topic e.g. falling through Earth / on the Moon>",
  "background_mood": "<mood name>",
  "has_character": <true|false>,
  "cut_style": "<crossfade|hard_cut>"
}}
"""


def _format_words_with_timestamps(words: list[dict], offset: int = 0) -> str:
    """Format word list with per-word timestamps and indices for precise alignment.

    Every word gets its index and start timestamp so the LLM can make exact cuts.
    Format: [idx:start_sec] word

    `offset` keeps indices global when only a window of the script is shown, so
    returned indices need no translation back to the full word list.
    """
    parts = []
    for i, w in enumerate(words):
        parts.append(f'[{i + offset}:{w["start"]:.2f}]')
        parts.append(w["word"])
    return " ".join(parts)


def _format_words_compact(words: list[dict], offset: int = 0) -> str:
    """Denser timestamp block for retries when the full list is huge."""
    parts = []
    for i, w in enumerate(words):
        parts.append(f"{i + offset}:{w['start']:.1f}:{w['word']}")
    return " ".join(parts)


# Segment long scripts in time windows so the LLM JSON isn't truncated mid-video.
# A single 8192-token pass over a 20 min script routinely returned ~12 concepts
# covering only the first minute; the rest of the audio then collapsed onto one
# stretched concept (see _backfill_uncovered_spans).
_CONCEPT_WINDOW_SEC = 120.0
_CONCEPT_WINDOW_MAX_WORDS = 340
# Kept modest so a long script can't trip Atlas rate limits mid-cook.
_SEGMENT_WINDOW_WORKERS = 4
# Uncovered stretches longer than this get deterministic beats instead of being
# absorbed by a neighbouring concept.
_MIN_BACKFILL_SEC = 5.0


def _word_windows(
    all_words: list[dict],
    *,
    window_sec: float = _CONCEPT_WINDOW_SEC,
    max_words: int = _CONCEPT_WINDOW_MAX_WORDS,
) -> list[tuple[int, int]]:
    """Return (start_idx, end_idx) half-open windows over the word list."""
    n = len(all_words)
    if n == 0:
        return []
    if n <= max_words:
        span = float(all_words[-1]["end"]) - float(all_words[0]["start"])
        if span <= window_sec * 1.25:
            return [(0, n)]

    windows: list[tuple[int, int]] = []
    i = 0
    while i < n:
        start_t = float(all_words[i]["start"])
        j = i + 1
        while j < n:
            span = float(all_words[j - 1]["end"]) - start_t
            if (j - i) >= max_words or span >= window_sec:
                break
            j += 1
        if j <= i:
            j = min(n, i + 1)
        windows.append((i, j))
        i = j
    return windows


def _fallback_concept_dicts(
    all_words: list[dict],
    *,
    target_sec: float = 4.0,
    hook_sec: float = HOOK_CUTOFF_SEC,
    niche_hint: str = "",
    index_offset: int = 0,
) -> list[dict]:
    """
    Deterministic segmentation when Atlas returns empty / unusable JSON.
    Groups words into ~target_sec beats (faster in the hook).
    """
    if not all_words:
        return []
    target_sec = max(2.0, float(target_sec or 4.0))
    hook_sec = max(5.0, float(hook_sec or HOOK_CUTOFF_SEC))
    topic = (niche_hint or "this topic").strip() or "this topic"

    out: list[dict] = []
    start_idx = 0
    n = len(all_words)
    while start_idx < n:
        start_t = float(all_words[start_idx]["start"])
        desired = 2.5 if start_t < hook_sec else target_sec
        end_idx = start_idx
        while end_idx + 1 < n:
            nxt_end = float(all_words[end_idx + 1]["end"])
            if nxt_end - start_t >= desired and end_idx > start_idx:
                break
            end_idx += 1
        for j in range(end_idx, min(n - 1, end_idx + 6)):
            w = str(all_words[j].get("word") or "")
            if w.endswith((".", "!", "?", ",", ";")):
                end_idx = j
                break
        text = " ".join(
            str(all_words[i].get("word") or "") for i in range(start_idx, end_idx + 1)
        ).strip()
        beat = re.sub(r"\s+", " ", text)[:80]
        out.append({
            "start_word_idx": start_idx + index_offset,
            "end_word_idx": end_idx + index_offset,
            "text": text,
            "illustration_prompt": (
                f"Stick figure scene about {topic}: visual metaphor for '{beat}'. "
                f"Clear setting + 2 props. No text."
            ),
            "background_mood": "warm_earth",
            "has_character": True,
            "cut_style": "crossfade",
            "section_topic": topic,
        })
        start_idx = end_idx + 1
    return out


def _covered_word_flags(concept_dicts: list[dict], n_words: int) -> list[bool]:
    """Mark which words any concept claims, for coverage checks."""
    covered = [False] * n_words
    for cd in concept_dicts:
        s, e = cd.get("start_word_idx"), cd.get("end_word_idx")
        if s is None or e is None:
            continue
        try:
            s, e = int(s), int(e)
        except (TypeError, ValueError):
            continue
        s = max(0, min(s, n_words - 1))
        e = max(0, min(e, n_words - 1))
        if e < s:
            continue
        for k in range(s, e + 1):
            covered[k] = True
    return covered


def coverage_ratio(concept_dicts: list[dict], all_words: list[dict]) -> float:
    """Fraction of words claimed by at least one concept."""
    n = len(all_words)
    if n == 0:
        return 1.0
    return sum(_covered_word_flags(concept_dicts, n)) / n


def _backfill_uncovered_spans(
    concept_dicts: list[dict],
    all_words: list[dict],
    *,
    niche_hint: str = "",
    target_sec: float = 4.0,
) -> list[dict]:
    """Generate beats for stretches the LLM skipped.

    Without this, _build_concepts extends the final concept all the way to the
    end of the audio and _enforce_duration_constraints then splits that one
    concept into hundreds of chunks that all share a single illustration_prompt
    — the "same picture repeats for fifteen minutes" failure users reported.
    """
    n = len(all_words)
    if n == 0:
        return concept_dicts

    covered = _covered_word_flags(concept_dicts, n)
    out = list(concept_dicts)
    spans = 0
    i = 0
    while i < n:
        if covered[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and not covered[j + 1]:
            j += 1
        span_sec = float(all_words[j]["end"]) - float(all_words[i]["start"])
        if span_sec >= _MIN_BACKFILL_SEC:
            out.extend(_fallback_concept_dicts(
                all_words[i:j + 1],
                target_sec=target_sec,
                hook_sec=HOOK_CUTOFF_SEC,
                niche_hint=niche_hint,
                index_offset=i,
            ))
            spans += 1
        i = j + 1

    if spans:
        print(f"[concept_segmenter] Backfilled {spans} uncovered span(s) "
              f"the model skipped ({len(out) - len(concept_dicts)} extra beats)")
    return out


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


def _segmenter_model() -> str:
    """Configured segmenter model, mapped onto an Atlas-prefixed id when needed."""
    import config as _cfg

    model = getattr(_cfg, "CONCEPT_SEGMENTER_MODEL", GEMINI_TEXT_MODEL)
    if model and not model.startswith(("google/", "openai/", "anthropic/")):
        atlas_default = getattr(_cfg, "ATLAS_TEXT_MODEL", None) or "google/gemini-3.1-flash-lite"
        if "gemini" in model.lower():
            model = atlas_default
    return model


def _segment_window_concept_dicts(
    excerpt: str,
    window_words: list[dict],
    *,
    index_offset: int,
    window_label: str,
    window_target: int,
    hook_dur: float,
    context: str = "",
) -> list[dict]:
    """Segment one time window. Returns [] when the LLM stays unusable.

    Word indices in the prompt are global, so results need no re-indexing.
    """
    from core.atlas_llm import generate_text

    w_start = float(window_words[0]["start"])
    w_end = float(window_words[-1]["end"])
    words_formatted = _format_words_with_timestamps(window_words, index_offset)
    zone = (
        "HOOK ZONE — fast cuts, 1.5-3s each"
        if w_start < hook_dur else "BODY ZONE — natural pacing, 2-6s each"
    )
    last_error = None

    for attempt in range(3):
        try:
            max_tok = 8192 if attempt == 0 else 12288
            words_block = words_formatted
            if attempt >= 1 and len(words_formatted) > 12000:
                words_block = _format_words_compact(window_words, index_offset)
            raw = generate_text(
                CONCEPT_SEGMENTER_PROMPT + "\n\n" + (
                    f"SCRIPT EXCERPT ({window_label}):\n{excerpt}\n\n"
                    f"WORD-LEVEL TIMESTAMPS:\n{words_block}\n\n"
                    f"This excerpt spans {w_start:.0f}s–{w_end:.0f}s "
                    f"({w_end - w_start:.0f}s of audio). {zone}.\n"
                    f"Target: ~{window_target} concepts for this excerpt.\n"
                    f"You MUST cover every word from index {index_offset} to "
                    f"{index_offset + len(window_words) - 1} with no gaps.\n"
                    f"{context}\n"
                    f"Segment into visual concepts now. JSON array only."
                ),
                model=_segmenter_model(),
                max_tokens=max_tok,
                temperature=0.2 if attempt == 0 else 0.4,
            )
            parsed = _parse_concepts_json(raw)
            if parsed:
                return parsed
            last_error = "empty concept array"
        except Exception as e:
            last_error = e
        print(f"  [concept_segmenter] {window_label} attempt {attempt + 1} failed: {last_error}")

    return []


def segment_into_concepts(
    script: str,
    all_words: list[dict],
    style_preset: str = "default",
    niche_hint: str = "",
    lite_mode: bool = False,
    hq_mode: bool = False,
) -> list[Concept]:
    """
    Segment a script into visual concepts using word-level timestamps.

    Args:
        script: full script text
        all_words: word timestamps from faster-whisper [{"word", "start", "end"}, ...]
        style_preset: art style preset name
        niche_hint: optional hint about the video's niche/topic
        lite_mode: fewer concepts (trial) — less illustration COGS/latency
        hq_mode: slightly longer body shots for GPT Image 2 cooks

    Returns:
        list of Concept objects with exact timing and illustration prompts
    """
    from core.atlas_llm import has_atlas

    if not GEMINI_KEY and not has_atlas():
        raise ValueError("ATLASCLOUD_KEY or GEMINI_KEY required for concept segmentation")
    if not all_words:
        raise ValueError("No word timestamps provided")

    total_duration = all_words[-1]["end"] - all_words[0]["start"]

    hook_dur = min(HOOK_CUTOFF_SEC, total_duration)
    body_dur = max(0, total_duration - hook_dur)
    # Lite: longer body shots → fewer AI images (biggest cost lever)
    if lite_mode:
        hook_concepts = max(2, int(hook_dur / 3.5))
        body_concepts = max(1, int(body_dur / 7.0)) if body_dur > 0 else 0
    elif hq_mode:
        # Slightly longer body shots so GPT Image 2 COGS stays sane at 12 min.
        hook_concepts = max(3, int(hook_dur / 2.8))
        body_concepts = max(1, int(body_dur / 5.5)) if body_dur > 0 else 0
    else:
        hook_concepts = max(3, int(hook_dur / 2.5))
        body_concepts = max(1, int(body_dur / 4.0)) if body_dur > 0 else 0
    target_concepts = hook_concepts + body_concepts

    context = ""
    if niche_hint:
        context = f"\nVIDEO TOPIC: {niche_hint}\n"

    fallback_target = 7.0 if lite_mode else (5.5 if hq_mode else 4.0)
    windows = _word_windows(all_words)

    print(f"[concept_segmenter] Segmenting {total_duration:.1f}s script into "
          f"~{target_concepts} concepts across {len(windows)} window(s)...")

    def _segment_one(w_i: int, lo: int, hi: int) -> list[dict]:
        window_words = all_words[lo:hi]
        w_start = float(window_words[0]["start"])
        w_end = float(window_words[-1]["end"])
        label = f"window {w_i + 1}/{len(windows)} ({w_start:.0f}s–{w_end:.0f}s)"
        # Pro-rate the concept target so each window keeps the intended pacing.
        share = (w_end - w_start) / total_duration if total_duration > 0 else 1.0
        window_target = max(2, round(target_concepts * share))
        excerpt = " ".join(str(w.get("word") or "") for w in window_words).strip()

        window_dicts = _segment_window_concept_dicts(
            excerpt,
            window_words,
            index_offset=lo,
            window_label=label,
            window_target=window_target,
            hook_dur=hook_dur,
            context=context,
        )
        if not window_dicts:
            print(f"  [concept_segmenter] {label}: LLM unusable — timed fallback")
            return _fallback_concept_dicts(
                window_words,
                target_sec=fallback_target,
                hook_sec=HOOK_CUTOFF_SEC,
                niche_hint=niche_hint,
                index_offset=lo,
            )
        print(f"  [concept_segmenter] {label}: {len(window_dicts)} concepts")
        return window_dicts

    # Windows are independent (indices are global), so segment them in parallel.
    # Sequential calls added minutes to long cooks, which risked pushing them
    # past the cook timeout — the very hang we're trying to eliminate.
    if len(windows) == 1:
        per_window = [_segment_one(0, *windows[0])]
    else:
        workers = min(_SEGMENT_WINDOW_WORKERS, len(windows))
        per_window = [[] for _ in windows]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_segment_one, w_i, lo, hi): w_i
                for w_i, (lo, hi) in enumerate(windows)
            }
            for fut in as_completed(futures):
                w_i = futures[fut]
                lo, hi = windows[w_i]
                try:
                    per_window[w_i] = fut.result()
                except Exception as e:
                    print(f"  [concept_segmenter] window {w_i + 1} raised ({e}) — timed fallback")
                    per_window[w_i] = _fallback_concept_dicts(
                        all_words[lo:hi],
                        target_sec=fallback_target,
                        hook_sec=HOOK_CUTOFF_SEC,
                        niche_hint=niche_hint,
                        index_offset=lo,
                    )

    concept_dicts: list[dict] = [cd for chunk in per_window for cd in chunk]

    # Any stretch the model silently dropped gets real beats rather than being
    # swallowed by a neighbour and duplicated into hundreds of identical frames.
    concept_dicts = _backfill_uncovered_spans(
        concept_dicts,
        all_words,
        niche_hint=niche_hint,
        target_sec=fallback_target,
    )
    covered = coverage_ratio(concept_dicts, all_words)
    if covered < 0.98:
        print(f"[concept_segmenter] WARNING: only {covered:.1%} of words covered "
              f"after backfill — expect stretched visuals")

    concepts = _build_concepts(concept_dicts, all_words)
    concepts = _enforce_duration_constraints(concepts)
    concepts = _enrich_illustration_context(concepts, niche_hint=niche_hint, script=script)

    print(f"[concept_segmenter] Final: {len(concepts)} concepts, "
          f"avg {sum(c.duration_sec for c in concepts)/len(concepts):.1f}s, "
          f"moods: {_mood_summary(concepts)}")

    return concepts


def _looks_like_narration(prompt: str) -> bool:
    p = (prompt or "").strip()
    if not p:
        return True
    # Spoken-sentence smells: long, starts with capital + many words, quote marks
    if p.startswith('"') or p.startswith("'"):
        return True
    words = p.split()
    if len(words) >= 10 and p[:1].isupper() and p.endswith((".", "!", "?", ",")):
        return True
    narration_starts = (
        "as you ", "but as ", "when you ", "if you ", "this ", "the narrator",
        "you would ", "you will ", "something strange",
    )
    low = p.lower()
    return any(low.startswith(s) for s in narration_starts)


def _enrich_illustration_context(
    concepts: list[Concept],
    *,
    niche_hint: str = "",
    script: str = "",
) -> list[Concept]:
    """Ensure each prompt carries setting/topic props; rewrite narration leaks."""
    running_topic = (niche_hint or "").strip()
    enriched: list[Concept] = []
    for c in concepts:
        topic = (c.section_topic or running_topic or "").strip()
        if c.section_topic:
            running_topic = c.section_topic.strip() or running_topic
        prompt = (c.illustration_prompt or "").strip()
        if _looks_like_narration(prompt) or not prompt:
            # Build a visual metaphor from the beat text + topic
            beat = re.sub(r"\s+", " ", (c.text or "")[:90]).strip()
            if topic:
                prompt = (
                    f"Stick figure in scene about {topic}: visual metaphor for "
                    f"'{beat}'. Show setting + 2 concrete props. No text."
                )
            else:
                prompt = (
                    f"Stick figure visual metaphor for '{beat}'. "
                    f"Include clear setting and 2 props. No text."
                )
        elif topic:
            # Soft-anchor topic if missing from prompt
            if topic.lower() not in prompt.lower():
                prompt = f"{prompt.rstrip('.')}. Setting: {topic}."
        enriched.append(Concept(
            id=c.id,
            text=c.text,
            start_sec=c.start_sec,
            end_sec=c.end_sec,
            duration_sec=c.duration_sec,
            illustration_prompt=prompt[:220],
            background_mood=c.background_mood,
            has_character=c.has_character,
            cut_style=c.cut_style,
            section_topic=topic,
        ))
    return enriched


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
            section_topic=(cd.get("section_topic") or "").strip(),
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
                    section_topic=c.section_topic,
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
