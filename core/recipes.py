"""
Recipe/template system for different video production workflows.
Each recipe defines a pipeline type, required API keys, and default settings.
"""

from __future__ import annotations
from config import PEXELS_KEY, GEMINI_KEY, HEYGEN_KEY


RECIPES = {
    "broll_only": {
        "pipeline": "standard",
        "label": "B-Roll Only",
        "description": "Full B-roll video from script + voiceover. "
                       "Images sourced from Wikimedia/Pexels with Ken Burns effects.",
        "requires_keys": ["GEMINI_KEY"],
        "optional_keys": ["PEXELS_KEY"],
        "inputs": ["voiceover", "script"],
        "settings": ["swap_rate", "style"],
    },
    "broll_cinematic": {
        "pipeline": "cinematic",
        "label": "Cinematic B-Roll",
        "description": "AI-directed cinematic B-roll with stock video, images, "
                       "AI art, and text overlays. LLM plans each scene, VLM verifies.",
        "requires_keys": ["GEMINI_KEY"],
        "optional_keys": ["PEXELS_KEY"],
        "inputs": ["voiceover", "script"],
        "settings": ["swap_rate", "style"],
    },
    "avatar_plus_broll": {
        "pipeline": "avatar",
        "label": "Avatar + Illustrations",
        "description": "AI avatar talking head (HeyGen) interleaved with "
                       "AI-generated or stock illustration B-roll.",
        "requires_keys": ["HEYGEN_KEY", "GEMINI_KEY"],
        "optional_keys": ["PEXELS_KEY"],
        "inputs": ["script", "avatar_id", "voice_id"],
        "settings": ["swap_rate", "style", "avatar_ratio", "use_ai_images"],
    },
    "animated_explainer": {
        "pipeline": "explainer",
        "label": "Animated Explainer",
        "description": "AI-illustrated explainer documentary with consistent "
                       "hand-drawn art style, word-level visual timing, and "
                       "background mood shifts. Cheapest recipe (~$0.034/scene).",
        "requires_keys": ["GEMINI_KEY"],
        "optional_keys": [],
        "inputs": ["voiceover", "script"],
        "settings": ["style"],
    },
}


def get_recipe(name: str) -> dict:
    """Get a recipe by name. Raises KeyError if not found."""
    if name not in RECIPES:
        raise KeyError(f"Unknown recipe '{name}'. Available: {list(RECIPES.keys())}")
    return RECIPES[name]


def validate_keys(recipe_name: str) -> tuple[bool, list[str]]:
    """
    Check if all required API keys for a recipe are available.
    Returns (all_ok, list_of_missing_keys).
    """
    recipe = get_recipe(recipe_name)
    key_values = {
        "GEMINI_KEY": GEMINI_KEY,
        "PEXELS_KEY": PEXELS_KEY,
        "HEYGEN_KEY": HEYGEN_KEY,
    }

    missing = []
    for key_name in recipe["requires_keys"]:
        if not key_values.get(key_name):
            missing.append(key_name)

    return len(missing) == 0, missing


def list_recipes() -> list[dict]:
    """Return all recipes with their validation status."""
    result = []
    for name, recipe in RECIPES.items():
        ok, missing = validate_keys(name)
        result.append({
            "name": name,
            "label": recipe["label"],
            "description": recipe["description"],
            "available": ok,
            "missing_keys": missing,
        })
    return result
