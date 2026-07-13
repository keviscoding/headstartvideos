"""
Gemini-powered video analyzer for understanding YouTube niche B-roll patterns.
Feeds a YouTube URL to Gemini, which watches the video and extracts a structured
NicheProfile describing the B-roll strategy, visual style, and example queries.

Requires a working Google Gemini project (ALLOW_GOOGLE_GEMINI=1). Atlas chat
models are text-only and cannot watch YouTube URLs.
"""

from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from config import GEMINI_KEY, GEMINI_TEXT_MODEL

# After one project denial, skip further Google calls in this process.
_gemini_video_denied: str | None = None


class NicheAnalysisUnavailable(RuntimeError):
    """Expected ops condition — not a product bug. Safe to surface as HTTP 503."""


def google_video_analyze_enabled() -> bool:
    return (os.getenv("ALLOW_GOOGLE_GEMINI", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _mark_gemini_denied(reason: str) -> None:
    global _gemini_video_denied
    _gemini_video_denied = reason
    print(f"[analyzer] Gemini video analyze disabled: {reason}")


@dataclass
class NicheProfile:
    niche_name: str = ""
    recipe: str = "broll_only"          # broll_only | avatar_plus_broll | unsupported
    broll_type: str = "stock_photo"     # stock_photo | archival | ai_generated | mixed
    default_swap_rate: str = "medium"   # fast | medium | slow
    visual_style: dict = field(default_factory=lambda: {
        "era": "modern",
        "tone": "neutral",
        "palette": "natural",
        "grain": "clean",
    })
    avatar_config: dict | None = None   # {"tool": "heygen", "ratio": 0.6, ...}
    automatable_pct: int = 0
    sample_queries: list[dict] = field(default_factory=list)
    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def save(self, path: str | Path):
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> NicheProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)


ANALYSIS_PROMPT = """\
You are a YouTube video production analyst. Watch this video carefully and analyze \
its B-roll strategy, visual style, and production techniques.

Respond with a JSON object (no markdown fences) containing:

{
  "niche_name": "short descriptive name for this niche/genre",
  "recipe": "broll_only" or "avatar_plus_broll" or "unsupported",
  "broll_type": "stock_photo" or "archival" or "ai_generated" or "mixed",
  "default_swap_rate": "fast" (2-4s) or "medium" (5-10s) or "slow" (10-20s),
  "visual_style": {
    "era": "the time period feel (e.g. modern, 1970s, retro, futuristic)",
    "tone": "emotional tone (e.g. dark, warm, dramatic, corporate, playful)",
    "palette": "color palette (e.g. cool blues, warm earth tones, desaturated, vibrant)",
    "grain": "texture quality (e.g. clean, film grain, vintage, sharp)"
  },
  "avatar_config": null or {
    "tool": "the avatar tool if identifiable (heygen, d-id, synthesia, unknown)",
    "ratio": 0.0-1.0 (fraction of video that is avatar vs b-roll),
    "position": "where the avatar appears (full_frame, corner, side_by_side)"
  },
  "automatable_pct": 0-100 (what percentage of this video's visuals our system could generate),
  "sample_queries": [
    {
      "timestamp_approx": "MM:SS",
      "narration_text": "what is being said at this moment",
      "subject": "the specific subject (person, place, concept)",
      "era": "time period for this image",
      "tone": "mood/feeling to convey",
      "format_hint": "type of image (press photo, candid, illustration, stock, diagram)",
      "composed_query": "the full search query built from these attributes"
    }
  ],
  "notes": "any important observations about this niche's production style"
}

RULES:
- "recipe" should be "avatar_plus_broll" if the video uses an AI talking-head avatar
- "recipe" should be "broll_only" if it's purely images/footage with voiceover
- "recipe" should be "unsupported" if the video needs real camera footage, live action, etc.
- Generate 5-10 sample_queries covering different moments in the video
- Each sample_query should decompose the visual into attributes (subject, era, tone, format)
- The composed_query should combine attributes into an effective image search string
- Be specific about visual style -- don't say "cinematic" when you mean "desaturated warm tones with shallow depth of field"
- For automatable_pct, consider: can stock/archival/AI images replace the original B-roll convincingly?"""


def analyze_video(
    youtube_url: str,
    analyze_minutes: float = 5.0,
) -> NicheProfile:
    """
    Use Gemini to watch a YouTube video and extract a NicheProfile.
    The video must be public. Uses Gemini's YouTube URL feature.
    """
    if _gemini_video_denied:
        raise NicheAnalysisUnavailable(
            "Niche video analysis is temporarily unavailable (Google Gemini access denied). "
            "Pick a recipe manually for now — cooking still works."
        )
    if not google_video_analyze_enabled():
        raise NicheAnalysisUnavailable(
            "Niche video analysis needs a working Google Gemini project "
            "(Atlas cannot watch YouTube). Pick a recipe manually — cooking still works."
        )
    if not GEMINI_KEY:
        raise NicheAnalysisUnavailable(
            "Niche video analysis needs GEMINI_KEY. "
            "Pick a recipe manually for now — cooking still works."
        )

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_KEY)

    # Normalize URL — Gemini needs a standard youtube.com/watch?v= or youtu.be/ URL.
    url = youtube_url.strip()
    if "youtube.com/shorts/" in url:
        vid_id = url.split("youtube.com/shorts/")[1].split("?")[0].split("&")[0]
        url = f"https://www.youtube.com/watch?v={vid_id}"
    elif "youtu.be/" in url:
        vid_id = url.split("youtu.be/")[1].split("?")[0].split("&")[0]
        url = f"https://www.youtube.com/watch?v={vid_id}"
    elif "youtube.com" in url and "watch" not in url and "/v/" not in url:
        raise ValueError(
            "Please provide a YouTube video URL (e.g. youtube.com/watch?v=...), "
            "not a channel or playlist URL."
        )

    prompt = ANALYSIS_PROMPT
    if analyze_minutes < 10:
        prompt += (
            f"\n\nFocus on the first {analyze_minutes:.0f} minutes of the video. "
            "That should be enough to understand the B-roll patterns."
        )

    try:
        response = client.models.generate_content(
            model=GEMINI_TEXT_MODEL,
            contents=types.Content(
                parts=[
                    types.Part(
                        file_data=types.FileData(file_uri=url)
                    ),
                    types.Part(text=prompt),
                ]
            ),
        )
    except Exception as e:
        err = str(e)
        if any(x in err for x in ("PERMISSION_DENIED", "denied access", "403")):
            _mark_gemini_denied(err[:160])
            raise NicheAnalysisUnavailable(
                "Niche video analysis is temporarily unavailable (Google Gemini access denied). "
                "Pick a recipe manually for now — cooking still works."
            ) from None
        raise

    text = (getattr(response, "text", None) or "").strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise ValueError(f"Could not parse Gemini response as JSON: {text[:300]}")

    profile = NicheProfile(
        niche_name=data.get("niche_name", "unknown"),
        recipe=data.get("recipe", "broll_only"),
        broll_type=data.get("broll_type", "mixed"),
        default_swap_rate=data.get("default_swap_rate", "medium"),
        visual_style=data.get("visual_style", {}),
        avatar_config=data.get("avatar_config"),
        automatable_pct=data.get("automatable_pct", 0),
        sample_queries=data.get("sample_queries", []),
        notes=data.get("notes", ""),
    )

    print(f"[analyzer] Niche: {profile.niche_name}")
    print(f"[analyzer] Recipe: {profile.recipe}")
    print(f"[analyzer] B-roll type: {profile.broll_type}")
    print(f"[analyzer] Swap rate: {profile.default_swap_rate}")
    print(f"[analyzer] Automatable: {profile.automatable_pct}%")
    print(f"[analyzer] Sample queries: {len(profile.sample_queries)}")

    return profile
