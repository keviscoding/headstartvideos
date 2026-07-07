"""
Script Studio -- Claude-powered content generation.

Functions for channel analysis, viral video ideas, title generation,
and full script writing. Users provide their own Claude API key.
"""

from __future__ import annotations

STRATEGIST_SYSTEM_PROMPT = """\
You are a YouTube channel strategist and content analyst. You study channels \
deeply -- their titles, topics, posting patterns, view counts, and scripts -- \
to identify what makes their content succeed.

Your job is to analyze the channel data provided and give actionable insights:
- What topics and formats get the most views?
- What title patterns drive clicks?
- What storytelling techniques do the scripts use?
- What makes this channel's content unique vs competitors?

Be specific and data-driven. Reference actual titles and view counts."""


IDEAS_SYSTEM_PROMPT = """\
You are a viral YouTube content strategist. Based on the channel data and \
analysis provided, generate video ideas that would perform well RIGHT NOW.

Rules:
- Ideas must fit the channel's established niche and audience
- Prioritize topics that are trending or timely THIS WEEK
- Each idea should have a clear hook that makes viewers click
- Consider what has worked before (high-view topics) and what gaps exist
- For each idea, explain WHY it would go viral (timeliness, controversy, \
curiosity gap, etc.)

Format each idea as:
IDEA: [concept]
WHY IT WORKS: [reasoning]
ANGLE: [specific angle/hook to take]"""


TITLES_SYSTEM_PROMPT = """\
You are a YouTube title optimization expert. You craft titles that maximize \
click-through rate while accurately representing the content.

Rules:
- Study the channel's existing title patterns from the data provided
- Match the tone and style (formal, casual, clickbait-y, etc.)
- Use proven title formulas: curiosity gaps, numbers, "How/Why/What" openers
- Keep titles under 60 characters when possible
- Each title should create an irresistible urge to click
- Generate 5 title options ranked by predicted CTR"""


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


_MODEL_FALLBACKS = [
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
) -> str:
    """Make a single Claude API call with automatic model fallback."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    models_to_try = [model] if model else []
    models_to_try.extend(m for m in _MODEL_FALLBACKS if m not in models_to_try)

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


def _format_channel_data(channel_data: dict) -> str:
    """Format channel data into a readable string for Claude."""
    parts = []

    videos = channel_data.get("videos", [])
    if videos:
        parts.append("=== CHANNEL VIDEOS (by view count) ===")
        sorted_vids = sorted(videos, key=lambda v: v.get("views", 0), reverse=True)
        for v in sorted_vids[:30]:
            views = v.get("views", "N/A")
            title = v.get("title", "Untitled")
            parts.append(f"  [{views:>10} views] {title}")

    transcripts = channel_data.get("transcripts", [])
    if transcripts:
        parts.append("\n=== SAMPLE TRANSCRIPTS ===")
        for t in transcripts[:3]:
            title = t.get("title", "Untitled")
            text = t.get("text", "")[:2000]
            parts.append(f"\n--- {title} ---\n{text}\n")

    meta = channel_data.get("metadata", {})
    if meta:
        parts.append("\n=== CHANNEL METADATA ===")
        for k, v in meta.items():
            parts.append(f"  {k}: {v}")

    return "\n".join(parts) if parts else "No channel data provided."


def analyze_channel(
    channel_data: dict,
    api_key: str,
    model: str = "",
) -> str:
    """Analyze a channel's content strategy and patterns."""
    formatted = _format_channel_data(channel_data)
    user_msg = (
        "Analyze this YouTube channel's content strategy in detail. "
        "What works, what doesn't, what patterns do you see?\n\n"
        f"{formatted}"
    )
    return _call_claude(STRATEGIST_SYSTEM_PROMPT, user_msg, api_key, model, max_tokens=4096)


def generate_ideas(
    channel_data: dict,
    api_key: str,
    num_ideas: int = 7,
    model: str = "",
    analysis: str = "",
) -> str:
    """Generate viral video ideas based on channel data."""
    formatted = _format_channel_data(channel_data)
    context = f"\n\nPrevious channel analysis:\n{analysis}" if analysis else ""
    user_msg = (
        f"Generate {num_ideas} video ideas that would go viral THIS WEEK "
        f"for this channel.\n\n{formatted}{context}"
    )
    return _call_claude(IDEAS_SYSTEM_PROMPT, user_msg, api_key, model, max_tokens=4096)


def generate_titles(
    video_idea: str,
    channel_data: dict,
    api_key: str,
    model: str = "",
) -> str:
    """Generate 5 viral title options for a video idea."""
    formatted = _format_channel_data(channel_data)
    user_msg = (
        f"Generate 5 title options for this video idea:\n\n"
        f"IDEA: {video_idea}\n\n"
        f"Match the title style of this channel:\n{formatted}"
    )
    return _call_claude(TITLES_SYSTEM_PROMPT, user_msg, api_key, model, max_tokens=2048)


def generate_script(
    title: str,
    video_idea: str,
    channel_data: dict,
    api_key: str,
    target_length_min: int = 8,
    model: str = "",
) -> str:
    """Generate a full video script."""
    formatted = _format_channel_data(channel_data)
    user_msg = (
        f"Write a complete YouTube script for:\n"
        f"Title: {title}\n"
        f"Concept: {video_idea}\n"
        f"Target length: {target_length_min} minutes\n\n"
        f"Match the voice and style of this channel:\n{formatted}"
    )
    return _call_claude(SCRIPT_SYSTEM_PROMPT, user_msg, api_key, model, max_tokens=8192)
