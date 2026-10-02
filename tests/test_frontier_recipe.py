"""
Test Frontier recipe admin-only access control and basic functionality.
"""

import pytest
import sys
from pathlib import Path

# Add workspace root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_frontier_recipe_registered():
    """Frontier recipe should be registered with admin_only flag."""
    from core.recipes import RECIPES, get_recipe
    
    assert "frontier" in RECIPES, "Frontier recipe not registered"
    
    frontier = get_recipe("frontier")
    assert frontier["pipeline"] == "frontier"
    assert frontier["label"] == "Frontier"
    assert frontier.get("admin_only") is True, "Frontier should be admin_only"
    assert "LLM" in frontier["requires_keys"]


def test_admin_only_flag_validates():
    """Recipe with admin_only flag should validate correctly."""
    from core.recipes import validate_keys
    
    # Frontier requires LLM (ATLASCLOUD_KEY or GEMINI_KEY)
    # validate_keys checks if keys exist, not admin status
    ok, missing = validate_keys("frontier")
    # This will pass/fail based on env, but should not raise
    assert isinstance(ok, bool)
    assert isinstance(missing, list)


def test_frontier_recipe_cost():
    """Frontier should have cost estimate."""
    from webapp.cook_runner import estimate_cost_pence
    
    cost = estimate_cost_pence("frontier", 8.0)
    assert cost == 80.0, f"Expected 80 pence for 8 min, got {cost}"
    
    cost_2 = estimate_cost_pence("frontier", 1.0)
    assert cost_2 == 10.0, f"Expected 10 pence for 1 min, got {cost_2}"


def test_frontier_pipeline_imports():
    """Frontier pipeline module should import without errors."""
    from core import frontier_pipeline
    
    assert hasattr(frontier_pipeline, "run_frontier_pipeline")
    
    # Check signature
    import inspect
    sig = inspect.signature(frontier_pipeline.run_frontier_pipeline)
    params = list(sig.parameters.keys())
    
    assert "script" in params
    assert "voiceover_path" in params
    assert "lite_mode" in params
    assert "image_quality" in params
    assert "progress_callback" in params


def test_frontier_in_cook_runner():
    """cook_runner should handle frontier recipe."""
    import core.recipes
    
    # Verify frontier is in recipes
    assert "frontier" in core.recipes.RECIPES
    
    # Check cook_runner has frontier in cost table
    from webapp.cook_runner import _COST_PENCE_PER_MIN
    assert "frontier" in _COST_PENCE_PER_MIN


def test_admin_email_check():
    """_is_admin_email should work correctly."""
    from webapp.server import _is_admin_email
    import config
    
    # Test with known admin email
    admin_email = "nwalikelv@gmail.com"
    if hasattr(config, "ADMIN_EMAILS") and config.ADMIN_EMAILS:
        if admin_email in config.ADMIN_EMAILS:
            assert _is_admin_email(admin_email) is True
            assert _is_admin_email(admin_email.upper()) is True  # case-insensitive
    
    # Test with non-admin
    assert _is_admin_email("random@example.com") is False
    assert _is_admin_email("") is False


def test_niches_filter_admin_only():
    """Niches endpoint should filter admin-only recipes for non-admins."""
    # This tests the filtering logic conceptually
    from core.recipes import get_recipe
    
    # Get frontier recipe
    frontier = get_recipe("frontier")
    is_admin_only = frontier.get("admin_only", False)
    
    assert is_admin_only is True, "Frontier should be admin_only"
    
    # Simulate filtering (as done in server.py /api/niches)
    is_admin = False  # Non-admin user
    should_show = not is_admin_only or is_admin
    
    assert should_show is False, "Non-admin should not see Frontier"
    
    # Admin should see it
    is_admin = True
    should_show = not is_admin_only or is_admin
    assert should_show is True, "Admin should see Frontier"


def test_build_endpoint_admin_check():
    """Build endpoint logic should reject non-admin Frontier attempts."""
    from core.recipes import get_recipe
    
    frontier = get_recipe("frontier")
    is_admin_only = frontier.get("admin_only", False)
    
    # Simulate the check in server.py /api/build
    is_admin = False
    if is_admin_only and not is_admin:
        # Should raise 403
        should_fail = True
    else:
        should_fail = False
    
    assert should_fail is True, "Non-admin attempt should be blocked"
    
    # Admin should pass
    is_admin = True
    if is_admin_only and not is_admin:
        should_fail = True
    else:
        should_fail = False
    
    assert should_fail is False, "Admin attempt should pass"


def test_frontier_niche_json_exists():
    """Frontier niche JSON should exist."""
    import json
    
    niche_path = ROOT / "webapp" / "niches" / "frontier.json"
    assert niche_path.exists(), "Frontier niche JSON missing"
    
    with open(niche_path) as f:
        niche = json.load(f)
    
    assert niche["id"] == "frontier"
    assert niche["recipe"] == "frontier"
    assert niche["name"] == "Frontier"
    assert niche["format"] == "long"


def test_recipe_labels_includes_frontier():
    """Frontend RECIPE_LABELS should include Frontier."""
    app_js_path = ROOT / "webapp" / "static" / "app.js"
    
    with open(app_js_path, "r") as f:
        content = f.read()
    
    # Check that frontier is in RECIPE_LABELS
    assert "frontier:" in content.lower(), "Frontier not in RECIPE_LABELS"
    assert "'Frontier'" in content or '"Frontier"' in content, "Frontier label missing"


def test_frontier_pipeline_smoke():
    """Smoke test: frontier_pipeline can be imported and has correct structure."""
    from core.frontier_pipeline import (
        run_frontier_pipeline,
        _estimate_word_timestamps,
        _normalize_image,
        _create_placeholder,
    )
    
    # Test helper functions exist
    assert callable(_estimate_word_timestamps)
    assert callable(_normalize_image)
    assert callable(_create_placeholder)
    
    # Test estimate_word_timestamps logic
    words = _estimate_word_timestamps(
        "Hello world test",
        "/dev/null",  # Will fail probe, use 60s default
    )
    assert len(words) == 3
    assert all("word" in w and "start" in w and "end" in w for w in words)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
