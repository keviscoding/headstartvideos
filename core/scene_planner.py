"""
Scene Planner -- LLM-based "DirectorScore" for cinematic B-roll.

Reads the full script with sentence timestamps and creates a per-scene
production plan: what asset type to use, what to search for, pacing hints,
and cut styles. Replaces simple keyword-based query generation with
narrative-aware visual planning.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field, asdict

from config import GEMINI_KEY, GEMINI_TEXT_MODEL


@dataclass
class Scene:
    id: int
    text: str
    start_sec: float
    end_sec: float
    duration_sec: float
    asset_type: str  # stock_video | stock_image | ai_image | text_overlay
    search_queries: list[str] = field(default_factory=list)
    ai_prompt: str = ""
    overlay_text: str = ""
    cut_style: str = "hard_cut"  # hard_cut | crossfade
    pacing: str = "normal"  # slow | normal | fast


SCENE_PLANNER_PROMPT = """\
You are a cinematic video director planning the visual composition for a \
narrated documentary-style video. You will receive a script broken into \
sentences with precise audio timestamps.

Your job is to group sentences into SCENES and decide the best visual \
approach for each scene. Think like a Netflix documentary editor.

CRITICAL RULES:

RULE 1 — VISUAL IMPACT HIERARCHY:
When a sentence contains multiple visual keywords, ALWAYS choose the most \
UNIQUE and IDENTIFYING visual, not the most generic one.

Example: "they reject modern technology, choosing horse-drawn buggies over \
cars and candlelight over electricity"
  BAD: search for "candle" (generic, could be any candle)
  GOOD: search for "Amish horse drawn buggy countryside" (unique, identifying)

The test: if you showed ONLY the visual (no audio), would the viewer know \
what the video is about? "A candle" = no. "An Amish buggy on a dirt road" = yes.

RULE 2 — ASSET TYPE SELECTION:
  - "stock_video": for CONCRETE, filmable, GENERIC subjects (landscapes, cities, \
    nature, people walking, machinery, animals, transportation, buildings). Use \
    this ONLY when a stock videographer would have actually filmed this subject.
  - "stock_image": for SPECIFIC named people, historical events, archival photos, \
    documents, or when the script references a specific real photograph/artifact.
  - "ai_image": USE THIS for any of these situations:
    * Hyper-specific historical content (medieval manuscript illustrations, \
      ancient artifacts in specific settings)
    * Surreal or impossible imagery (plants that don't exist, alien star maps)
    * Scenes where the described visual is too specific for stock (e.g., \
      "women bathing in pools of green liquid" -- no stock library has this)
    * Abstract scientific concepts (DNA strands, genetic patterns)
    * Historical reconstructions (what a medieval scribe's desk looked like)
    * Any description involving "drawings of", "illustrations of", \
      "paintings of", "diagrams of" specific subjects
    ALWAYS provide ai_prompt even for stock scenes as fallback.
  - "text_overlay": ONLY for 1-2 high-impact words (a date, statistic, or \
    dramatic reveal). Max 1-2 per entire video.

  CRITICAL: Ask yourself "Would a stock videographer have filmed this exact \
  thing?" If NO, use ai_image. A stock library does NOT have footage of:
    - Medieval manuscript pages with specific illustrations
    - Hand-drawn astronomical diagrams from the 1400s
    - Women bathing in green liquid in medieval drawings
    - Alien-looking botanical illustrations on parchment
    - Ancient plumbing systems drawn on vellum

RULE 3 — SEARCH QUERIES (CRITICAL — CONTEXT, NOT ISOLATED NOUNS):
Write 2-3 queries optimized for Pexels/Pixabay stock search APIs.
  - NEVER search for a noun without its context. Always include the SETTING.
  - If script says "chimney-like structures called black smokers", search \
    "black smoker hydrothermal vent deep ocean", NOT just "black smokers"
  - If script says "better maps of Mars", search "Mars planet surface space", \
    NOT "maps" (which returns Renaissance cartographers)
  - If script is about deep ocean, EVERY query must include "deep sea" or \
    "underwater" or "ocean floor" — never pull surface/land footage
  - Good: "giant tube worms hydrothermal vent deep sea"
  - Bad: "tube worms" (returns garden earthworms)
  - Order from most specific to most general (fallback).

RULE 4 — AI IMAGE PROMPTS:
ALWAYS fill in ai_prompt for every scene, regardless of asset_type. This is the \
fallback visual if stock search returns nothing. Write a rich, cinematic prompt:
  - Include style: "photorealistic", "cinematic still frame", "moody"
  - Include lighting: "dramatic golden hour", "cold clinical lighting"
  - Include composition: "wide establishing shot", "close-up macro"
  - For MANUSCRIPTS/ARTIFACTS: "macro photography of an ancient weathered \
    parchment page featuring a medieval illustration of [subject], \
    photorealistic, cinematic lighting, shallow depth of field"
  - For HISTORICAL SCENES: "cinematic reconstruction of [scene], period-accurate \
    [era] setting, dramatic lighting, documentary photography style"
  - For ABSTRACT CONCEPTS: create VISUAL METAPHORS:
    "DNA studies" → "Cinematic macro shot of glowing DNA double helix strands \
    intertwined with old weathered wooden textures, dramatic blue lighting"
  - NEVER generate modern/digital-looking art for historical subjects. Always \
    specify "period-accurate", "medieval", "weathered", "ancient" as appropriate.

RULE 5 — SCENE DURATION & GROUPING (CRITICAL FOR PACING):
  - MAXIMUM scene duration is 6 seconds. NEVER create a scene longer than 7s.
  - MINIMUM scene duration is 3 seconds.
  - Sweet spot is 4-6 seconds per scene.
  - Cover EVERY sentence in the provided list — do not skip the middle or end.
  - Rough density: ~1 scene per 4-6s of audio (30s→5-7, 60s→10-15, 180s→30-45).
  - EVERY sentence that introduces a new visual concept MUST be its own scene.
  - If one sentence covers 10+ seconds of audio, it MUST be split into 2 scenes.
  - Dense scripts with many concepts need MORE scenes, not fewer.

RULE 5b — MEDIA MIX QUOTA:
  - Aim for a NATURAL MIX of stock footage and AI-generated imagery.
  - Stock (video+image) should be at least 40-50% of scenes.
  - ai_image can be 30-50% of scenes if the script describes many specific, \
    historical, or surreal visuals that stock media cannot cover.
  - At MOST 1-2 scenes can be text_overlay.
  - The goal is VISUAL ACCURACY over source type. A perfectly generated AI \
    image of a medieval manuscript is better than a wrong stock photo of \
    an Arabic book.

RULE 6 — CUT STYLE:
  - "crossfade": for smooth topic transitions within the same thread
  - "hard_cut": for dramatic reveals, topic shifts, or contrasts

RULE 7 — NO DEAD AIR:
Every second of the video MUST have compelling visual content. There is no \
acceptable scenario where the viewer sees a plain background or empty frame. \
If you can't think of a stock video, assign ai_image with a strong visual \
metaphor prompt.

RULE 8 — QUERY ENRICHMENT (CRITICAL):
Before writing search queries, identify the CORE SUBJECT of the entire video \
(e.g., "Amish community", "Apollo 11", "Tesla electric cars"). Then APPEND \
cultural/contextual keywords to EVERY search query to ensure accuracy.

Example for a video about the Amish:
  BAD: "horse drawn buggy moving" (could return British carriage tours)
  GOOD: "traditional Amish horse drawn buggy black carriage rural Pennsylvania"

The enrichment keywords should include: the specific culture/community, \
the geographic region, the time period, and any distinctive visual markers \
(e.g., "black carriage", "plain clothing", "white bonnets" for Amish).

RULE 9 — NAMED ENTITY RECOGNITION:
If the script names a SPECIFIC real-world artifact, document, person, place, \
or event (e.g., "The Voynich Manuscript", "The Mona Lisa", "The Declaration \
of Independence", "Walter Cronkite"), you MUST:
  - Use "stock_image" as asset_type (archival/public domain images exist)
  - Include the EXACT entity name in search_queries
  - Set search queries like: ["Voynich Manuscript pages", "Voynich Manuscript \
    botanical illustrations", "medieval manuscript pages"]
  - NEVER substitute a generic "old book" or "Arabic manuscript" for a named entity

RULE 10 — TEXT OVERLAY AS ACCENT, NOT REPLACEMENT:
Text overlays are ACCENT elements that ENHANCE visual scenes, not replace them. \
They should feel like a dramatic "chapter card" or "stat reveal" that appears \
for 2-3 seconds maximum between visual scenes. Think of it like a title card \
in a Netflix documentary -- it appears briefly, then cuts to footage.
  - overlay_text must be 1-3 POWERFUL words maximum (e.g., "300 YEARS", \
    "NEVER SEEN BEFORE", "1693")
  - The text should correspond to the most dramatic moment in the narration

{style_guidance}

RESPOND WITH ONLY a JSON array of scene objects. No other text.

Each scene object:
{{
  "id": <int>,
  "sentence_ids": [<list of sentence indices, 0-based>],
  "asset_type": "<stock_video|stock_image|ai_image|text_overlay>",
  "search_queries": ["<query1>", "<query2>", "<fallback_query>"],
  "ai_prompt": "<ALWAYS provide a cinematic image prompt as fallback>",
  "overlay_text": "<1-2 words ONLY if text_overlay, else empty string>",
  "cut_style": "<hard_cut|crossfade>",
  "pacing": "<slow|normal|fast>"
}}
"""


def _build_sentence_context(sentences_with_times: list[dict]) -> str:
    """Format sentences with timestamps for the LLM."""
    lines = []
    for i, s in enumerate(sentences_with_times):
        lines.append(
            f"[{i}] ({s['start']:.1f}s - {s['end']:.1f}s) {s['text']}"
        )
    return "\n".join(lines)


def _parse_scenes_json(raw: str) -> list[dict]:
    """Extract JSON array from LLM response, handling markdown fences and malformed JSON."""
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

    # Fix common LLM JSON mistakes: trailing commas, unquoted keys, comments
    fixed = re.sub(r',\s*([}\]])', r'\1', text)  # trailing commas
    fixed = re.sub(r'//[^\n]*', '', fixed)  # line comments
    fixed = re.sub(r'/\*.*?\*/', '', fixed, flags=re.DOTALL)  # block comments
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Last resort: extract individual objects and reconstruct array
    objects = []
    for m in re.finditer(r'\{[^{}]*\}', text, re.DOTALL):
        try:
            obj = json.loads(m.group())
            objects.append(obj)
        except json.JSONDecodeError:
            try:
                fixed_obj = re.sub(r',\s*}', '}', m.group())
                obj = json.loads(fixed_obj)
                objects.append(obj)
            except json.JSONDecodeError:
                continue

    if objects:
        return objects

    raise ValueError(f"Could not parse LLM scene plan as JSON: {text[:300]}")


# Plan long scripts in time windows so the LLM JSON isn't truncated mid-video
# (a single 8192-token plan for a 15–20 min script often only covered the open).
_PLAN_WINDOW_SEC = 150.0
_PLAN_WINDOW_MAX_SENTENCES = 36


def _sentence_windows(
    sentence_timestamps: list[dict],
    *,
    window_sec: float = _PLAN_WINDOW_SEC,
    max_sentences: int = _PLAN_WINDOW_MAX_SENTENCES,
) -> list[tuple[int, int]]:
    """Return (start_idx, end_idx) half-open windows over sentence_timestamps."""
    n = len(sentence_timestamps)
    if n == 0:
        return []
    if n <= max_sentences:
        audio_span = float(sentence_timestamps[-1]["end"]) - float(sentence_timestamps[0]["start"])
        if audio_span <= window_sec * 1.25:
            return [(0, n)]

    windows: list[tuple[int, int]] = []
    i = 0
    while i < n:
        start_t = float(sentence_timestamps[i]["start"])
        j = i + 1
        while j < n:
            span = float(sentence_timestamps[j - 1]["end"]) - start_t
            if (j - i) >= max_sentences or span >= window_sec:
                break
            j += 1
        if j <= i:
            j = min(n, i + 1)
        windows.append((i, j))
        i = j
    return windows


def _scene_dicts_to_scenes(
    scene_dicts: list[dict],
    sentence_timestamps: list[dict],
    *,
    index_offset: int = 0,
    used_sentence_ids: set[int] | None = None,
) -> list[Scene]:
    """Map LLM scene dicts onto absolute sentence timestamps."""
    if used_sentence_ids is None:
        used_sentence_ids = set()
    scenes: list[Scene] = []

    for sd in scene_dicts:
        sent_ids = sd.get("sentence_ids", [])
        if not sent_ids:
            continue

        abs_ids = []
        for sid in sent_ids:
            try:
                local = int(sid)
            except (TypeError, ValueError):
                continue
            abs_ids.append(local + index_offset)

        new_ids = [sid for sid in abs_ids if sid not in used_sentence_ids]
        if not new_ids:
            continue

        scene_texts = []
        start_sec = None
        end_sec = 0.0

        for sid in new_ids:
            if 0 <= sid < len(sentence_timestamps):
                st = sentence_timestamps[sid]
                scene_texts.append(st["text"])
                if start_sec is None:
                    start_sec = st["start"]
                end_sec = st["end"]
                used_sentence_ids.add(sid)

        if start_sec is None:
            continue

        duration = end_sec - start_sec
        if duration <= 0.5:
            continue

        scenes.append(Scene(
            id=sd.get("id", len(scenes)),
            text=" ".join(scene_texts),
            start_sec=start_sec,
            end_sec=end_sec,
            duration_sec=duration,
            asset_type=sd.get("asset_type", "stock_video"),
            search_queries=sd.get("search_queries", []) or [],
            ai_prompt=sd.get("ai_prompt", "") or "",
            overlay_text=sd.get("overlay_text", "") or "",
            cut_style=sd.get("cut_style", "hard_cut"),
            pacing=sd.get("pacing", "normal"),
        ))

    return scenes


def _fallback_scenes_for_range(
    sentence_timestamps: list[dict],
    start_idx: int,
    end_idx: int,
) -> list[Scene]:
    """Deterministic scenes when the LLM skips a stretch of the script."""
    scenes: list[Scene] = []
    i = start_idx
    while i < end_idx:
        st = sentence_timestamps[i]
        start = float(st["start"])
        end = float(st["end"])
        texts = [st["text"]]
        j = i + 1
        # Group short sentences up to ~5.5s so we don't explode clip count.
        while j < end_idx:
            nxt = sentence_timestamps[j]
            cand_end = float(nxt["end"])
            if cand_end - start > 5.5:
                break
            texts.append(nxt["text"])
            end = cand_end
            j += 1
        duration = end - start
        if duration > 0.5:
            clean = re.sub(r"[.,!?;:'\"()\-]", " ", " ".join(texts))
            words = [w for w in clean.split() if len(w) > 3][:6]
            query = " ".join(words) if words else " ".join(texts)[:60]
            scenes.append(Scene(
                id=0,
                text=" ".join(texts),
                start_sec=start,
                end_sec=end,
                duration_sec=duration,
                asset_type="stock_video",
                search_queries=[query, f"{query} documentary"],
                ai_prompt=(
                    f"Cinematic documentary still: {' '.join(texts)[:180]}. "
                    f"Photorealistic, dramatic lighting, 16:9."
                ),
                overlay_text="",
                cut_style="hard_cut",
                pacing="normal",
            ))
        i = j
    return scenes


def _fill_uncovered_sentences(
    scenes: list[Scene],
    sentence_timestamps: list[dict],
    used_sentence_ids: set[int],
) -> list[Scene]:
    """Ensure every narrated sentence becomes visual time (fixes short finals)."""
    if not sentence_timestamps:
        return scenes

    missing = [i for i in range(len(sentence_timestamps)) if i not in used_sentence_ids]
    if not missing:
        # Still close tiny timeline holes between planned scenes.
        return _fill_timeline_gaps(scenes, sentence_timestamps)

    print(
        f"[scene_planner] Filling {len(missing)}/{len(sentence_timestamps)} "
        f"uncovered sentences so video matches full voiceover"
    )
    filled = list(scenes)
    # Group contiguous missing indices
    run_start = missing[0]
    prev = missing[0]
    for idx in missing[1:] + [None]:
        if idx is not None and idx == prev + 1:
            prev = idx
            continue
        filled.extend(_fallback_scenes_for_range(sentence_timestamps, run_start, prev + 1))
        if idx is None:
            break
        run_start = prev = idx

    filled.sort(key=lambda s: s.start_sec)
    return _fill_timeline_gaps(filled, sentence_timestamps)


def _fill_timeline_gaps(
    scenes: list[Scene],
    sentence_timestamps: list[dict],
) -> list[Scene]:
    """Pad gaps between scenes so concat duration ≈ full audio length."""
    if not scenes or not sentence_timestamps:
        return scenes

    audio_start = float(sentence_timestamps[0]["start"])
    audio_end = float(sentence_timestamps[-1]["end"])
    ordered = sorted(scenes, key=lambda s: s.start_sec)
    out: list[Scene] = []
    cursor = audio_start

    def _sentences_between(t0: float, t1: float) -> tuple[int, int]:
        idxs = [
            i for i, st in enumerate(sentence_timestamps)
            if float(st["end"]) > t0 + 0.05 and float(st["start"]) < t1 - 0.05
        ]
        if not idxs:
            return 0, 0
        return idxs[0], idxs[-1] + 1

    for scene in ordered:
        if scene.start_sec > cursor + 0.4:
            a, b = _sentences_between(cursor, scene.start_sec)
            if b > a:
                out.extend(_fallback_scenes_for_range(sentence_timestamps, a, b))
            else:
                # No sentence mapping — stretch previous clip if we have one.
                gap = scene.start_sec - cursor
                if out and gap > 0.4:
                    out[-1].end_sec = scene.start_sec
                    out[-1].duration_sec = out[-1].end_sec - out[-1].start_sec
        out.append(scene)
        cursor = max(cursor, scene.end_sec)

    if cursor < audio_end - 0.4:
        a, b = _sentences_between(cursor, audio_end)
        if b > a:
            out.extend(_fallback_scenes_for_range(sentence_timestamps, a, b))
        elif out:
            out[-1].end_sec = audio_end
            out[-1].duration_sec = out[-1].end_sec - out[-1].start_sec

    for i, s in enumerate(out):
        s.id = i
    return out


def _plan_window_scene_dicts(
    prompt: str,
    script: str,
    window_sentences: list[dict],
    *,
    window_label: str,
) -> list[dict]:
    from core.atlas_llm import generate_text

    sentence_context = _build_sentence_context(window_sentences)
    user_msg = (
        f"SCRIPT EXCERPT ({window_label}):\n{script}\n\n"
        f"SENTENCES WITH TIMESTAMPS (indices are 0-based within this excerpt):\n"
        f"{sentence_context}\n\n"
        f"Plan scenes that cover EVERY sentence in this excerpt. JSON array only."
    )

    last_error = None
    for attempt in range(3):
        try:
            raw = generate_text(
                prompt + "\n\n" + user_msg,
                model=GEMINI_TEXT_MODEL,
                max_tokens=8192 if attempt == 0 else 12288,
            )
            return _parse_scenes_json(raw)
        except Exception as e:
            last_error = e
            print(f"  [scene_planner] {window_label} attempt {attempt + 1} failed: {e}")
    print(f"  [scene_planner] {window_label} LLM failed ({last_error}) — using fallback scenes")
    return []


def plan_scenes(
    script: str,
    sentence_timestamps: list[dict],
    niche_profile: dict | None = None,
    style_notes: str = "",
) -> list[Scene]:
    """
    Generate a DirectorScore: a per-scene production plan.

    Long scripts are planned in time windows so the model can't silently drop
    the back half (which produced ~4–5 min videos from 15–20 min voiceovers).
    """
    from core.atlas_llm import has_atlas

    if not GEMINI_KEY and not has_atlas():
        raise ValueError("ATLASCLOUD_KEY or GEMINI_KEY required for scene planning")
    if not sentence_timestamps:
        raise ValueError("No sentence timestamps provided for scene planning")

    style_guidance = ""
    if niche_profile:
        vs = niche_profile.get("visual_style", {})
        style_guidance = (
            f"VISUAL STYLE for this niche:\n"
            f"  Era: {vs.get('era', 'modern')}\n"
            f"  Tone: {vs.get('tone', 'neutral')}\n"
            f"  Palette: {vs.get('palette', 'natural')}\n"
            f"  Grain: {vs.get('grain', 'none')}\n"
        )
    if style_notes:
        style_guidance += f"\nAdditional direction:\n{style_notes}\n"

    prompt = SCENE_PLANNER_PROMPT.format(
        style_guidance=style_guidance if style_guidance else "No specific style constraints."
    )

    windows = _sentence_windows(sentence_timestamps)
    audio_dur = float(sentence_timestamps[-1]["end"]) - float(sentence_timestamps[0]["start"])
    print(
        f"[scene_planner] Generating DirectorScore for {len(sentence_timestamps)} "
        f"sentences ({audio_dur:.0f}s) in {len(windows)} window(s)..."
    )

    used_sentence_ids: set[int] = set()
    scenes: list[Scene] = []

    for w_i, (start_i, end_i) in enumerate(windows):
        window = sentence_timestamps[start_i:end_i]
        w_start = float(window[0]["start"])
        w_end = float(window[-1]["end"])
        label = f"window {w_i + 1}/{len(windows)} ({w_start:.0f}s–{w_end:.0f}s)"
        excerpt = " ".join(s["text"] for s in window)
        scene_dicts = _plan_window_scene_dicts(prompt, excerpt, window, window_label=label)
        if scene_dicts:
            chunk_scenes = _scene_dicts_to_scenes(
                scene_dicts,
                sentence_timestamps,
                index_offset=start_i,
                used_sentence_ids=used_sentence_ids,
            )
            scenes.extend(chunk_scenes)
            print(f"  [scene_planner] {label}: {len(chunk_scenes)} scenes")
        else:
            fallback = _fallback_scenes_for_range(sentence_timestamps, start_i, end_i)
            for idx in range(start_i, end_i):
                used_sentence_ids.add(idx)
            scenes.extend(fallback)
            print(f"  [scene_planner] {label}: fallback {len(fallback)} scenes")

    scenes = _fill_uncovered_sentences(scenes, sentence_timestamps, used_sentence_ids)

    core_subject, environment = _extract_context(script)
    print(f"[scene_planner] Core subject: \"{core_subject}\"")
    print(f"[scene_planner] Environment: \"{environment}\"")

    scenes = _enforce_constraints(scenes)
    scenes = _detect_unstockable(scenes, core_subject)
    scenes = _enrich_queries_with_context(scenes, core_subject, environment)
    scenes = _inject_reveal_scenes(scenes)

    # Constraints / text-overlay caps can shrink durations — restore full audio span.
    scenes = _fill_timeline_gaps(scenes, sentence_timestamps)
    for i, s in enumerate(scenes):
        s.id = i

    covered = sum(s.duration_sec for s in scenes)
    print(
        f"[scene_planner] Final {len(scenes)} scenes covering ~{covered:.0f}s "
        f"of {audio_dur:.0f}s audio: "
        f"{sum(1 for s in scenes if s.asset_type == 'stock_video')} video, "
        f"{sum(1 for s in scenes if s.asset_type == 'stock_image')} image, "
        f"{sum(1 for s in scenes if s.asset_type == 'ai_image')} AI, "
        f"{sum(1 for s in scenes if s.asset_type == 'text_overlay')} text"
    )

    return scenes


MIN_SCENE_DURATION = 3.0
MAX_SCENE_DURATION = 7.0
MAX_TEXT_OVERLAY_RATIO = 0.15
MAX_TEXT_OVERLAY_COUNT = 1


def _enforce_constraints(scenes: list[Scene]) -> list[Scene]:
    """
    Post-process scenes to enforce hard constraints the LLM might violate:
    1. Minimum scene duration (merge tiny scenes with neighbors)
    2. Text overlay quota (convert excess text_overlays to ai_image)
    3. Media mix balance (ensure enough real footage)
    """
    if not scenes:
        return scenes

    total_dur = sum(s.duration_sec for s in scenes)

    # --- Pre-filter: remove invalid scenes ---
    scenes = [s for s in scenes if s.duration_sec > 0.5]

    # --- Constraint 0: Split oversized scenes (MOST CRITICAL) ---
    # Run twice to catch any chunks that are still too long after first pass
    for _pass in range(2):
        split_scenes = []
        did_split = False
        for scene in scenes:
            if scene.duration_sec <= MAX_SCENE_DURATION:
                split_scenes.append(scene)
            else:
                chunks = _split_scene(scene)
                split_scenes.extend(chunks)
                did_split = True
                if _pass == 0:
                    print(f"  [constraint] Split scene {scene.id} ({scene.duration_sec:.1f}s) "
                          f"into {len(chunks)} sub-scenes")
        scenes = split_scenes
        if not did_split:
            break

    # --- Constraint 1: Minimum scene duration ---
    merged = []
    for scene in scenes:
        if scene.duration_sec < MIN_SCENE_DURATION and merged:
            prev = merged[-1]
            prev.text = prev.text + " " + scene.text
            prev.end_sec = scene.end_sec
            prev.duration_sec = prev.end_sec - prev.start_sec
            if not prev.search_queries and scene.search_queries:
                prev.search_queries = scene.search_queries
            if not prev.ai_prompt and scene.ai_prompt:
                prev.ai_prompt = scene.ai_prompt
        else:
            merged.append(scene)
    scenes = merged

    # --- Constraint 1b: Cap text overlay duration (max 4.5 seconds) ---
    MAX_TEXT_DUR = 4.5
    for s in scenes:
        if s.asset_type == "text_overlay" and s.duration_sec > MAX_TEXT_DUR:
            s.duration_sec = MAX_TEXT_DUR
            s.end_sec = s.start_sec + MAX_TEXT_DUR

    # --- Constraint 2: Text overlay quota ---
    text_scenes = [s for s in scenes if s.asset_type == "text_overlay"]
    text_dur = sum(s.duration_sec for s in text_scenes)

    if len(text_scenes) > MAX_TEXT_OVERLAY_COUNT:
        sorted_text = sorted(text_scenes, key=lambda s: s.duration_sec)
        for excess in sorted_text[MAX_TEXT_OVERLAY_COUNT:]:
            excess.asset_type = "ai_image"
            if not excess.ai_prompt:
                excess.ai_prompt = (
                    f"Cinematic documentary still: {excess.text[:150]}. "
                    f"Photorealistic, dramatic lighting, 16:9."
                )
            print(f"  [constraint] Converted scene {excess.id} from text_overlay to ai_image (quota)")

    if total_dur > 0 and text_dur / total_dur > MAX_TEXT_OVERLAY_RATIO:
        for s in scenes:
            if s.asset_type == "text_overlay" and s.duration_sec > 3:
                s.asset_type = "ai_image"
                if not s.ai_prompt:
                    s.ai_prompt = (
                        f"Cinematic visual metaphor: {s.text[:150]}. "
                        f"Moody, dramatic lighting, documentary style."
                    )
                print(f"  [constraint] Converted scene {s.id} from text_overlay to ai_image (duration ratio)")

    # --- Constraint 3: Media mix balance ---
    visual_count = sum(1 for s in scenes if s.asset_type in ("stock_video", "stock_image"))
    if len(scenes) > 2 and visual_count < len(scenes) * 0.5:
        for s in scenes:
            if s.asset_type == "text_overlay" and visual_count < len(scenes) * 0.5:
                s.asset_type = "stock_video"
                if not s.search_queries:
                    s.search_queries = [s.text[:60]]
                print(f"  [constraint] Promoted scene {s.id} to stock_video (media mix)")
                visual_count += 1

    # --- Constraint 4: Auto-generate queries for scenes that lack them ---
    STOP_WORDS = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "about", "like",
        "through", "after", "over", "between", "out", "against", "during",
        "that", "this", "these", "those", "it", "its", "they", "them",
        "their", "we", "our", "what", "which", "who", "whom", "how",
        "not", "no", "nor", "but", "and", "or", "so", "yet", "if",
    }
    for s in scenes:
        if s.asset_type in ("stock_video", "stock_image") and not s.search_queries:
            clean = re.sub(r'[.,!?;:\'"()\-]', ' ', s.text)
            words = clean.split()
            proper = [w for w in words if w[0:1].isupper() and w.lower() not in STOP_WORDS]
            content = [w for w in words if w.lower() not in STOP_WORDS and len(w) > 3]
            key_words = proper[:4] if proper else content[:5]
            query = " ".join(key_words) if key_words else s.text[:60]
            s.search_queries = [query]

        if s.asset_type == "ai_image" and not s.ai_prompt:
            s.ai_prompt = (
                f"Cinematic documentary still: {s.text[:150]}. "
                f"Photorealistic, dramatic lighting, 16:9 aspect ratio."
            )

    # Re-number scene IDs
    for i, s in enumerate(scenes):
        s.id = i

    return scenes


def _split_scene(scene: Scene) -> list[Scene]:
    """
    Split an oversized scene into sub-scenes of MAX_SCENE_DURATION or less.
    Uses clause/phrase boundaries for natural cuts within long sentences.
    """
    n_chunks = max(2, round(scene.duration_sec / 5.0))
    chunk_dur = scene.duration_sec / n_chunks

    phrases = _split_into_phrases(scene.text)
    if len(phrases) < n_chunks:
        words = scene.text.split()
        words_per_chunk = max(1, len(words) // n_chunks)
        phrases = []
        for i in range(n_chunks):
            start_w = i * words_per_chunk
            end_w = start_w + words_per_chunk if i < n_chunks - 1 else len(words)
            phrases.append(" ".join(words[start_w:end_w]))

    total_chars = sum(len(p) for p in phrases)
    if total_chars == 0:
        total_chars = 1

    chunks: list[Scene] = []
    current_phrases: list[str] = []
    current_start = scene.start_sec
    current_dur = 0.0

    for phrase in phrases:
        phrase_dur = (len(phrase) / total_chars) * scene.duration_sec

        if current_phrases and current_dur + phrase_dur > chunk_dur:
            end_sec = current_start + current_dur
            chunks.append(Scene(
                id=0,
                text=" ".join(current_phrases),
                start_sec=current_start,
                end_sec=end_sec,
                duration_sec=end_sec - current_start,
                asset_type=scene.asset_type,
                search_queries=[],
                ai_prompt="",
                overlay_text="",
                cut_style="crossfade",
                pacing=scene.pacing,
            ))
            current_start = end_sec
            current_phrases = []
            current_dur = 0.0

        current_phrases.append(phrase)
        current_dur += phrase_dur

    if current_phrases:
        chunks.append(Scene(
            id=0,
            text=" ".join(current_phrases),
            start_sec=current_start,
            end_sec=scene.end_sec,
            duration_sec=scene.end_sec - current_start,
            asset_type=scene.asset_type,
            search_queries=[],
            ai_prompt="",
            overlay_text="",
            cut_style="crossfade",
            pacing=scene.pacing,
        ))

    return chunks if chunks else [scene]


def _split_into_phrases(text: str) -> list[str]:
    """
    Split text into phrases at natural break points:
    sentences, commas, semicolons, 'and', 'but', 'or', em-dashes.
    """
    parts = re.split(r'(?<=[.!?])\s+|(?<=,)\s+|(?<=;)\s+|(?<=—)\s*|\s+(?=and\s)|\s+(?=but\s)|\s+(?=or\s)', text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]


UNSTOCKABLE_PATTERNS = [
    re.compile(r'drawings?\s+of\b', re.I),
    re.compile(r'illustrations?\s+of\b', re.I),
    re.compile(r'paintings?\s+of\b', re.I),
    re.compile(r'diagrams?\s+(?:of|that)\b', re.I),
    re.compile(r'manuscript\s+(?:pages?|illustrations?|drawings?)', re.I),
    re.compile(r'(?:plants?|flowers?|herbs?)\s+that\s+(?:do\s+not|don\'t)\s+exist', re.I),
    re.compile(r'(?:naked|nude)\s+(?:women|men|figures?|people)\s+(?:bathing|swimming)', re.I),
    re.compile(r'pools?\s+of\s+(?:green|blue|red|glowing)\s+liquid', re.I),
    re.compile(r'(?:alien|bizarre|unknown|impossible|fictional)\s+(?:plants?|creatures?|species)', re.I),
    re.compile(r'(?:hand-?drawn|hand-?written|medieval|ancient)\s+(?:star\s+)?(?:maps?|charts?|diagrams?)', re.I),
    re.compile(r'elaborate\s+(?:plumbing|machinery|contraptions?)', re.I),
    re.compile(r'(?:cryptic|unknown|undeciphered|mysterious)\s+(?:symbols?|script|language|text|writing)', re.I),
    re.compile(r'(?:weathered|ancient|old)\s+(?:parchment|vellum|scroll)', re.I),
]


def _detect_unstockable(scenes: list[Scene], core_subject: str) -> list[Scene]:
    """
    Detect scenes with hyper-specific, surreal, or historical content that
    stock media libraries cannot possibly have. Force these to ai_image.
    """
    for scene in scenes:
        if scene.asset_type in ("text_overlay", "ai_image"):
            continue

        for pattern in UNSTOCKABLE_PATTERNS:
            if pattern.search(scene.text):
                old_type = scene.asset_type
                scene.asset_type = "ai_image"
                if not scene.ai_prompt:
                    scene.ai_prompt = (
                        f"A page from an ancient medieval manuscript showing a "
                        f"hand-drawn illustration of: {scene.text[:150]}. "
                        f"The illustration is on aged, yellowed parchment with "
                        f"faded ink. Style: medieval manuscript illustration, NOT "
                        f"photorealistic. Think Voynich Manuscript or medieval "
                        f"bestiary art. Cinematic macro photography of the page."
                    )
                else:
                    if "photorealistic" in scene.ai_prompt.lower():
                        scene.ai_prompt = scene.ai_prompt.replace(
                            "photorealistic", "medieval manuscript illustration style"
                        ).replace(
                            "Photorealistic", "Medieval manuscript illustration style"
                        )
                print(f"  [unstockable] Scene {scene.id}: '{pattern.pattern[:40]}' "
                      f"matched -> ai_image (was {old_type})")
                break

    return scenes


def _extract_context(script: str, client=None) -> tuple[str, str]:
    """
    Extract core subject AND environment in a single LLM call (saves ~10s).
    Returns (core_subject, environment).
    """
    from core.atlas_llm import generate_text

    try:
        text = generate_text(
            (
                f"Read this script and extract TWO things. Reply in EXACTLY "
                f"this format (two lines, nothing else):\n"
                f"SUBJECT: <3-8 words for the core topic>\n"
                f"ENVIRONMENT: <3-6 comma-separated keywords for physical setting>\n\n"
                f"Examples:\n"
                f"  Moon landing script:\n"
                f"    SUBJECT: Apollo 11 NASA moon landing 1969\n"
                f"    ENVIRONMENT: space, lunar surface, mission control\n"
                f"  Deep ocean script:\n"
                f"    SUBJECT: Deep ocean hydrothermal vent life\n"
                f"    ENVIRONMENT: deep sea, underwater, dark, abyss\n"
                f"  Amish community script:\n"
                f"    SUBJECT: Amish community rural Pennsylvania America\n"
                f"    ENVIRONMENT: rural, farmland, countryside, rustic\n\n"
                f"SCRIPT:\n{script[:500]}\n\n"
                f"Extract now (two lines only):"
            ),
            model=GEMINI_TEXT_MODEL,
            max_tokens=256,
        ).strip()
        subject = ""
        environment = ""
        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("SUBJECT:"):
                subject = line.split(":", 1)[1].strip().strip('"\'')
            elif line.upper().startswith("ENVIRONMENT:"):
                environment = line.split(":", 1)[1].strip().strip('"\'')
        if not subject:
            subject = text.split("\n")[0].strip()[:60]
        if len(subject.split()) > 10:
            subject = " ".join(subject.split()[:8])
        if len(environment) > 100:
            environment = ", ".join(environment.split(",")[:6])
        return subject, environment
    except Exception as e:
        print(f"  [context] Extraction failed: {e}")
        words = script.split()[:30]
        nouns = [w for w in words if w[0].isupper() and len(w) > 2]
        subject = " ".join(nouns[:5]) if nouns else " ".join(words[:5])
        return subject, ""


def _enrich_queries_with_context(
    scenes: list[Scene], core_subject: str, environment: str = ""
) -> list[Scene]:
    """
    Append the core subject AND environment to search queries.
    This is CODE-LEVEL enforcement -- doesn't rely on the LLM remembering.
    """
    if not core_subject:
        return scenes

    subject_words = set(core_subject.lower().split())
    env_keywords = [kw.strip() for kw in environment.split(",") if kw.strip()]
    env_suffix = " ".join(env_keywords[:3]) if env_keywords else ""

    for scene in scenes:
        if scene.asset_type in ("stock_video", "stock_image"):
            enriched = []
            for q in scene.search_queries:
                query_words = set(q.lower().split())
                overlap = query_words & subject_words
                parts = [q]
                if len(overlap) < 2:
                    parts.append(core_subject)
                if env_suffix:
                    env_words = set(env_suffix.lower().split())
                    if len(query_words & env_words) < 1:
                        parts.append(env_suffix)
                enriched_q = " ".join(parts)
                if enriched_q != q:
                    print(f"  [enrich] Scene {scene.id}: \"{q}\" -> \"{enriched_q}\"")
                enriched.append(enriched_q)
            scene.search_queries = enriched

        if scene.ai_prompt:
            if core_subject.lower() not in scene.ai_prompt.lower():
                scene.ai_prompt = (
                    f"{scene.ai_prompt.rstrip('.')}. "
                    f"Context: this is about {core_subject}."
                )
            if env_suffix and env_suffix.lower() not in scene.ai_prompt.lower():
                scene.ai_prompt = (
                    f"{scene.ai_prompt.rstrip('.')}. "
                    f"Setting/atmosphere: {environment}."
                )

    return scenes


REVEAL_KEYWORDS = {
    "found", "discovered", "revealed", "unexpected", "challenges",
    "shocking", "secret", "hidden", "mystery", "never", "impossible",
    "changed", "transformed", "unknown", "unprecedented", "surprising",
    "breakthrough", "uncovered", "exposed", "overturned",
}


TEXT_INTERSTITIAL_KEYWORDS = {
    "never", "unbreakable", "impossible", "centuries", "mystery",
    "unknown", "shocking", "secret", "classified", "forbidden",
    "unprecedented", "unsolved", "ancient", "discovered",
}


def _inject_reveal_scenes(scenes: list[Scene]) -> list[Scene]:
    """
    Detect 'reveal/hook' sentences and ensure they use investigative visuals
    (ai_image) instead of generic stock footage. Also inject 1-2 text
    interstitials per minute for dramatic emphasis.
    """
    has_ai = any(s.asset_type == "ai_image" for s in scenes)

    for scene in scenes:
        words_lower = set(scene.text.lower().split())
        words_lower = {w.strip(".,!?;:'\"()-") for w in words_lower}

        reveal_hits = words_lower & REVEAL_KEYWORDS

        if reveal_hits and scene.asset_type == "stock_video":
            if not has_ai or len(reveal_hits) >= 2:
                scene.asset_type = "ai_image"
                if not scene.ai_prompt:
                    scene.ai_prompt = (
                        f"Dramatic cinematic visual for documentary reveal: "
                        f"{scene.text[:150]}. Dark moody lighting, sense of "
                        f"mystery and discovery, photorealistic."
                    )
                print(f"  [reveal] Scene {scene.id} switched to ai_image "
                      f"(hooks: {reveal_hits})")
                has_ai = True

    # Inject text interstitials for dramatic stats/dates/reveals (sparingly)
    total_dur = sum(s.duration_sec for s in scenes) if scenes else 0
    max_text_inserts = max(1, int(total_dur / 45))
    text_count = sum(1 for s in scenes if s.asset_type == "text_overlay")

    if text_count < max_text_inserts:
        candidates = []
        for scene in scenes:
            if scene.asset_type != "text_overlay" and scene.duration_sec >= 3.5:
                words_l = {w.strip(".,!?;:'\"()-") for w in scene.text.lower().split()}
                hits = words_l & TEXT_INTERSTITIAL_KEYWORDS

                has_number = bool(re.search(r'\d{2,}', scene.text))
                has_proper_noun = bool(re.search(r'[A-Z][a-z]+ [A-Z][a-z]+', scene.text))

                score = len(hits) + (2 if has_number else 0) + (1 if has_proper_noun else 0)
                if score > 1:
                    candidates.append((score, scene))

        candidates.sort(key=lambda x: -x[0])
        for _, scene in candidates[:max_text_inserts - text_count]:
            impact = _extract_impact_text(scene.text)
            if impact:
                scene.asset_type = "text_overlay"
                scene.overlay_text = impact
                print(f"  [interstitial] Scene {scene.id}: \"{impact}\" (text accent)")

    return scenes


def _extract_impact_text(text: str) -> str:
    """Extract 1-3 high-impact words from text for cinematic text overlays."""
    numbers = re.findall(r'\d[\d,]+(?:\s*(?:years?|centuries|decades|million|billion))?', text)
    if numbers:
        return numbers[0].upper()

    acronyms = re.findall(r'\b[A-Z]{2,}\b', text)
    acronyms = [a for a in acronyms if a not in {"THE", "AND", "BUT", "FOR"}]
    if acronyms:
        return acronyms[0]

    proper_nouns = re.findall(r'(?:the\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*', text)
    proper_nouns = [p for p in proper_nouns if len(p) > 3 and p.lower() not in {
        "the", "this", "that", "when", "what", "some", "they", "their"
    }]
    if proper_nouns:
        return proper_nouns[0].upper()

    impact_words = []
    for w in text.split():
        clean = w.strip(".,!?;:'\"()-").lower()
        if clean in TEXT_INTERSTITIAL_KEYWORDS or clean in REVEAL_KEYWORDS:
            impact_words.append(clean.upper())
    if impact_words:
        return " ".join(impact_words[:3])

    return ""
