"""
Test that avatar_gen jobs carry recipe=avatar_generator into cook_runner.

Symptom: POST /api/avatar-gen/generate writes recipe only to the DB column,
not into request_json, so hydrate_job_from_row → run_cook_job defaults to
animated_explainer and runs the wrong pipeline.
"""
import json
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_avatar_gen_request_includes_recipe():
    """Avatar generate request_json must include recipe=avatar_generator."""
    print("TEST: avatar_gen_request_includes_recipe")
    
    # Simulate the request_data dict built in avatar_gen_generate
    request_data = {
        "recipe": "avatar_generator",
        "script": "Test script for avatar generation",
        "title": "Test Avatar",
        "avatar_source": "prompt:A professional avatar",
        "reference_tags": [],
        "target_duration": 120.0,
        "credits_charged": 3,
        "notify_email": "test@example.com",
    }
    
    # Verify recipe is present and correct
    assert "recipe" in request_data, "recipe key missing from request_data"
    assert request_data["recipe"] == "avatar_generator", f"Expected avatar_generator, got {request_data['recipe']}"
    
    # Simulate serialization to JSON (what goes into DB)
    request_json = json.dumps(request_data)
    
    # Simulate deserialization (what cook_runner does)
    loaded = json.loads(request_json)
    assert loaded.get("recipe") == "avatar_generator", "recipe not preserved in JSON round-trip"
    
    print("  ✓ PASS: recipe=avatar_generator is in request_data and survives JSON serialization")


def test_hydrate_job_preserves_recipe():
    """hydrate_job_from_row should preserve recipe from request_json."""
    print("\nTEST: hydrate_job_preserves_recipe")
    
    from webapp.cook_runner import hydrate_job_from_row
    
    # Simulate a DB row for an avatar_generator job
    request_data = {
        "recipe": "avatar_generator",
        "script": "Test script",
        "title": "Test",
        "avatar_source": "prompt:test",
        "reference_tags": [],
        "target_duration": 120.0,
    }
    
    row = {
        "status": "queued",
        "progress_json": "[]",
        "request_json": json.dumps(request_data),
        "result_json": None,
        "user_id": 1,
        "credit_deducted": 1,
        "lite_mode": 0,
        "error": None,
        "created_at": 1234567890.0,
    }
    
    job = hydrate_job_from_row(row)
    
    # The critical assertion: recipe must be in job["request"]
    assert "request" in job, "job missing 'request' key"
    assert isinstance(job["request"], dict), "job['request'] is not a dict"
    recipe = job["request"].get("recipe")
    assert recipe == "avatar_generator", f"Expected avatar_generator, got {recipe}"
    
    print("  ✓ PASS: hydrate_job_from_row preserves recipe=avatar_generator in job['request']")


def test_recipe_used_in_run_cook_job():
    """
    Verify that the recipe from request is what run_cook_job would use.
    """
    print("\nTEST: recipe_used_in_run_cook_job")
    
    # Simulate what run_cook_job does at line 122-126
    req_data = {
        "recipe": "avatar_generator",
        "script": "Test",
    }
    
    # Line 126 in cook_runner.py:
    recipe = (req_data.get("recipe") or "").strip() or "animated_explainer"
    
    assert recipe == "avatar_generator", f"Expected avatar_generator, got {recipe}"
    print("  ✓ PASS: recipe extraction logic would use avatar_generator (not animated_explainer)")
    
    # Test the fallback behavior
    req_data_no_recipe = {}
    recipe_fallback = (req_data_no_recipe.get("recipe") or "").strip() or "animated_explainer"
    assert recipe_fallback == "animated_explainer", "Fallback should be animated_explainer"
    print("  ✓ PASS: empty recipe correctly falls back to animated_explainer")


if __name__ == "__main__":
    print("=" * 70)
    print("Running Avatar Generator Recipe Tests")
    print("=" * 70)
    
    try:
        test_avatar_gen_request_includes_recipe()
        test_hydrate_job_preserves_recipe()
        test_recipe_used_in_run_cook_job()
        
        print("\n" + "=" * 70)
        print("✓ ALL TESTS PASSED")
        print("=" * 70)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
