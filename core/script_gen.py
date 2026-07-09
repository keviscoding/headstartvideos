"""
Script Studio -- Claude-powered content generation.

Functions for channel analysis, viral video ideas, title generation,
and full script writing. Users provide their own Claude API key.
"""

from __future__ import annotations

import json
import re


STRATEGIST_SYSTEM_PROMPT = """\
You are a YouTube channel strategist and content analyst. You study channels \
deeply -- their titles, topics, posting patterns, view counts, and scripts -- \
to identify what makes their content succeed.

Your job is to analyze the channel data provided and give actionable insights:
- What topics and formats get the most views?
- What title patterns drive clicks?
- What storytelling techniques do the scripts use?
- What makes this channel's content unique vs competitors?

Be specific and data-driven. Reference actual titles and view counts.
Keep the analysis under 600 words."""


IDEAS_SYSTEM_PROMPT = """\
You are a viral YouTube content strategist. Generate video ideas that fit the \
channel's niche and would perform well now.

Return ONLY a valid JSON array of strings. No markdown, no commentary, no keys.
Each string is one complete video idea (1-2 sentences max).

Example:
["Idea one here", "Idea two here", "Idea three here"]"""


TITLES_SYSTEM_PROMPT = """\
You are a YouTube title optimization expert. Craft titles that maximize CTR \
while matching the channel's style.

Return ONLY a valid JSON array of exactly 5 title strings. No markdown, no \
numbering, no CTR scores, no commentary.

Example:
["Title one", "Title two", "Title three", "Title four", "Title five"]

Rules:
- Match the channel's tone from the data provided
- Prefer under 70 characters
- No wrapping titles in quotes inside the JSON strings beyond normal punctuation"""


SCRIPT_SYSTEM_PROMPT = """\
You are an expert YouTube scriptwriter. You write scripts that keep viewers \
watching until the very end.

Based on the channel data and style analysis provided, write a complete \
video script that:
- Matches the channel's established voice, tone, and pacing
- Opens with a powerful hook in the first 10 seconds
- Uses pattern interrupts every 30-60 seconds to maintain retention
- Builds tension and curiosity throughout
- Ends with a satisfying conclusion and subtle call to action
- Is optimized for voiceover narration (no stage directions, just spoken words)
- Targets the specified video length

Write ONLY the script text -- no headers, timestamps, or formatting notes. \
Just the words the narrator will speak."""


# Fast path for ideas/titles; script can fall back to Sonnet if needed.
_FAST_MODELS = [
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-sonnet-4-20250514",
]

_SCRIPT_MODELS = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-20250514",
]


def _call_claude(
    system_prompt: str,
    user_message: str,
    api_key: str,
    model: str = "",
    max_tokens: int = 4096,
    models: list[str] | None = None,
) -> str:
    """Make a single Claude API call with automatic model fallback."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    fallbacks = models or _FAST_MODELS
    models_to_try = [model] if model else []
    models_to_try.extend(m for m in fallbacks if m not in models_to_try)

    last_err = None
    for m in models_to_try:
        try:
            response = client.messages.create(
                model=m,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return response.content[-1].text
        except anthropic.NotFoundError as e:
            print(f"[script_gen] Model {m} not available, trying next...")
            last_err = e
            continue

    raise last_err or RuntimeError("No Claude model available")


def _format_channel_data(channel_data: dict | None, *, compact: bool = False) -> str:
    """Format channel data into a readable string for Claude.

    compact=True (ideas/titles): top titles + short meta only — much faster.
    compact=False (analysis/script): include short transcript samples.
    """
    if not channel_data or not isinstance(channel_data, dict):
        return (
            "No channel data provided. Generate strong, general YouTube ideas "
            "suitable for a faceless/automation-style channel. Prefer proven "
            "niches and clear hooks."
        )

    parts = []
    videos = channel_data.get("videos", [])
    if videos:
        parts.append("=== TOP VIDEOS (by views) ===")
        sorted_vids = sorted(videos, key=lambda v: v.get("views", 0) or 0, reverse=True)
        limit = 12 if compact else 25
        for v in sorted_vids[:limit]:
            views = v.get("views", "N/A")
            title = v.get("title", "Untitled")
            parts.append(f"  [{views} views] {title}")

    if not compact:
        transcripts = channel_data.get("transcripts", [])
        if transcripts:
            parts.append("\n=== SAMPLE TRANSCRIPTS (trimmed) ===")
            for t in transcripts[:2]:
                title = t.get("title", "Untitled")
                text = t.get("text", "")[:900]
                parts.append(f"\n--- {title} ---\n{text}\n")

    meta = channel_data.get("metadata", {})
    if meta:
        parts.append("\n=== CHANNEL METADATA ===")
        for k in ("channel_name", "subscriber_count", "video_count", "description"):
            if k in meta and meta[k]:
                val = str(meta[k])
                if k == "description":
                    val = val[:280]
                parts.append(f"  {k}: {val}")

    hint = channel_data.get("topic_hint") or channel_data.get("niche")
    if hint:
        parts.append(f"\n=== TOPIC / NICHE HINT ===\n  {hint}")

    return "\n".join(parts) if parts else (
        "No channel data provided. Generate strong, general YouTube ideas "
        "suitable for a faceless/automation-style channel."
    )


def _extract_json_array(text: str) -> list | None:
    """Pull a JSON array out of a model response (tolerates fences / prose)."""
    if not text:
        return None
    raw = text.strip()
    # Strip markdown fences
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[[\s\S]*\]", raw)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _clean_title(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^#+\s*", "", s)
    s = re.sub(r"^\d+[\).\:\-]\s*", "", s)
    s = s.strip("*_ \t\"'")
    # Drop metadata / commentary lines (not real titles)
    low = s.lower()
    if not s or len(s) < 8:
        return ""
    if low.startswith((
        "predicted", "why it ", "why this", "angle:", "ctr",
        "hits the", "idea:", "format each", "rules:",
    )):
        return ""
    if "predicted ctr" in low or s.startswith("##"):
        return ""
    return s


def parse_titles_response(text: str, limit: int = 5) -> list[str]:
    """Parse model output into a clean list of title strings."""
    arr = _extract_json_array(text)
    titles: list[str] = []
    if arr:
        for item in arr:
            if isinstance(item, str):
                t = _clean_title(item)
            elif isinstance(item, dict):
                t = _clean_title(str(item.get("title") or item.get("text") or ""))
            else:
                t = ""
            if t and t not in titles:
                titles.append(t)
            if len(titles) >= limit:
                return titles

    # Fallback: only numbered title lines (## 1. Title / 1) Title)
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(
            r'^(?:#+\s*)?(\d+)[\).\:\-]\s+\**"?(.+?)"?\**\s*$',
            line,
        )
        if not m:
            continue
        t = _clean_title(m.group(2))
        if t and t not in titles:
            titles.append(t)
        if len(titles) >= limit:
            break
    return titles[:limit]


def parse_ideas_response(text: str, limit: int = 7) -> list[str]:
    """Parse model output into a clean list of idea strings."""
    arr = _extract_json_array(text)
    ideas: list[str] = []
    if arr:
        for item in arr:
            if isinstance(item, str):
                idea = item.strip()
            elif isinstance(item, dict):
                idea = (
                    item.get("idea")
                    or item.get("concept")
                    or item.get("title")
                    or ""
                ).strip()
                why = (item.get("why") or item.get("why_it_works") or "").strip()
                if idea and why:
                    idea = f"{idea} — {why}"
            else:
                idea = ""
            if idea and idea not in ideas:
                ideas.append(idea)
            if len(ideas) >= limit:
                return ideas

    # Fallback: IDEA: blocks
    blocks = re.split(r"(?i)(?:^|\n)\s*(?:IDEA\s*\d*\s*:|##\s*IDEA\s*\d*\s*:)", text or "")
    for block in blocks[1:]:
        first = block.strip().split("\n")[0].strip().strip("*\"'")
        if first and first not in ideas:
            ideas.append(first)
        if len(ideas) >= limit:
            break
    if ideas:
        return ideas[:limit]

    # Last resort: non-empty lines that look like real ideas
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#*- ").strip()
        low = line.lower()
        if len(line) < 20:
            continue
        if low.startswith(("why", "angle", "predicted", "format", "rules")):
            continue
        if line not in ideas:
            ideas.append(line)
        if len(ideas) >= limit:
            break
    return ideas[:limit]


def analyze_channel(
    channel_data: dict | None,
    api_key: str,
    model: str = "",
) -> str:
    """Analyze a channel's content strategy and patterns."""
    if not channel_data:
        raise ValueError("Fetch channel data first, then run analysis.")
    formatted = _format_channel_data(channel_data, compact=False)
    user_msg = (
        "Analyze this YouTube channel's content strategy in detail. "
        "What works, what doesn't, what patterns do you see?\n\n"
        f"{formatted}"
    )
    return _call_claude(
        STRATEGIST_SYSTEM_PROMPT, user_msg, api_key, model,
        max_tokens=2048, models=_FAST_MODELS,
    )


def generate_ideas(
    channel_data: dict | None,
    api_key: str,
    num_ideas: int = 7,
    model: str = "",
    analysis: str = "",
) -> str:
    """Generate viral video ideas based on channel data (optional)."""
    formatted = _format_channel_data(channel_data, compact=True)
    # Keep analysis short — full essays slow Haiku down a lot
    context = ""
    if analysis:
        context = f"\n\nChannel analysis (summary):\n{analysis[:1200]}"
    user_msg = (
        f"Generate exactly {num_ideas} video ideas as a JSON array of strings.\n\n"
        f"{formatted}{context}"
    )
    return _call_claude(
        IDEAS_SYSTEM_PROMPT, user_msg, api_key, model,
        max_tokens=1500, models=_FAST_MODELS,
    )


def generate_titles(
    video_idea: str,
    channel_data: dict | None,
    api_key: str,
    model: str = "",
) -> str:
    """Generate 5 viral title options for a video idea."""
    if not (video_idea or "").strip():
        raise ValueError("Enter a video idea first, then generate titles.")
    formatted = _format_channel_data(channel_data, compact=True)
    user_msg = (
        f"Generate exactly 5 title options as a JSON array of strings for:\n\n"
        f"IDEA: {video_idea}\n\n"
        f"Channel style reference:\n{formatted}"
    )
    return _call_claude(
        TITLES_SYSTEM_PROMPT, user_msg, api_key, model,
        max_tokens=800, models=_FAST_MODELS,
    )


def generate_script(
    title: str,
    video_idea: str,
    channel_data: dict | None,
    api_key: str,
    target_length_min: int = 8,
    model: str = "",
) -> str:
    """Generate a full video script."""
    if not (title or "").strip():
        raise ValueError("Enter a video title first, then write the script.")
    formatted = _format_channel_data(channel_data, compact=False)
    user_msg = (
        f"Write a complete YouTube script for:\n"
        f"Title: {title}\n"
        f"Concept: {video_idea or title}\n"
        f"Target length: {target_length_min} minutes\n\n"
        f"Match the voice and style of this channel:\n{formatted}"
    )
    return _call_claude(
        SCRIPT_SYSTEM_PROMPT, user_msg, api_key, model,
        max_tokens=8192, models=_SCRIPT_MODELS,
    )
