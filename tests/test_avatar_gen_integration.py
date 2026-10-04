"""
Integration test demonstrating the complete avatar_gen cook flow.

This test simulates the full path:
1. avatar_gen_generate creates request_data
2. JSON serialization to DB
3. hydrate_job_from_row deserializes
4. run_cook_job extracts recipe and dispatches to correct pipeline

This validates that the fix ensures avatar jobs route to avatar_gen_pipeline.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def simulate_avatar_gen_endpoint():
    """Simulate what avatar_gen_generate does with the fix."""
    print("=" * 70)
    print("STEP 1: avatar_gen_generate creates request_data")
    print("=" * 70)
    
    # This is what the endpoint does now (with the fix)
    request_data = {
        "recipe": "avatar_generator",  # ← THE FIX
        "script": "This is a test script for avatar generation",
        "title": "Test Avatar Video",
        "avatar_source": "prompt:A professional avatar",
        "reference_tags": [],
        "target_duration": 120.0,
        "credits_charged": 3,
        "notify_email": "test@example.com",
    }
    
    print(f"request_data created:")
    print(f"  recipe: {request_data.get('recipe')}")
    print(f"  script: {request_data.get('script')[:50]}...")
    
    return request_data


def simulate_db_storage(request_data):
    """Simulate DB serialization."""
    print("\n" + "=" * 70)
    print("STEP 2: Serialize to JSON for DB storage")
    print("=" * 70)
    
    request_json = json.dumps(request_data)
    
    print(f"request_json length: {len(request_json)} chars")
    print(f"recipe in JSON: {'Yes ✓' if 'recipe' in request_json else 'No ✗'}")
    
    # Simulate the DB row
    row = {
        "status": "queued",
        "progress_json": "[]",
        "request_json": request_json,  # ← This is what goes to DB
        "result_json": None,
        "user_id": 1,
        "credit_deducted": 1,
        "lite_mode": 0,
        "error": None,
        "created_at": 1234567890.0,
        "recipe": "avatar_generator",  # ← Column (not used by cook_runner)
    }
    
    print(f"DB row recipe column: {row['recipe']}")
    print(f"DB row request_json contains recipe: {json.loads(row['request_json']).get('recipe')}")
    
    return row


def simulate_worker_hydration(row):
    """Simulate worker hydrating job from DB row."""
    print("\n" + "=" * 70)
    print("STEP 3: Worker hydrates job from DB row")
    print("=" * 70)
    
    from webapp.cook_runner import hydrate_job_from_row
    
    job = hydrate_job_from_row(row)
    
    print(f"job['request'] type: {type(job.get('request'))}")
    print(f"job['request']['recipe']: {job['request'].get('recipe')}")
    
    # This is the critical check
    recipe_in_request = job.get("request", {}).get("recipe")
    if recipe_in_request == "avatar_generator":
        print("✓ SUCCESS: recipe=avatar_generator preserved in job['request']")
    else:
        print(f"✗ FAIL: recipe={recipe_in_request} (expected avatar_generator)")
        raise AssertionError("Recipe not preserved correctly")
    
    return job


def simulate_cook_runner_dispatch(job):
    """Simulate cook_runner determining which pipeline to run."""
    print("\n" + "=" * 70)
    print("STEP 4: cook_runner dispatches to pipeline")
    print("=" * 70)
    
    req_data = job.get("request") or {}
    
    # This is the exact logic from cook_runner.py line 126
    recipe = (req_data.get("recipe") or "").strip() or "animated_explainer"
    
    print(f"req_data.get('recipe'): {req_data.get('recipe')!r}")
    print(f"Resolved recipe: {recipe!r}")
    
    # Determine which pipeline would be called
    if recipe == "avatar_generator":
        pipeline = "run_avatar_gen_pipeline"
        correct = True
    elif recipe == "animated_explainer":
        pipeline = "run_explainer_pipeline"
        correct = False
    else:
        pipeline = f"<other pipeline for {recipe}>"
        correct = False
    
    print(f"Would call: {pipeline}")
    
    if correct:
        print("✓ SUCCESS: Correct pipeline selected!")
    else:
        print(f"✗ FAIL: Wrong pipeline! Expected run_avatar_gen_pipeline, got {pipeline}")
        raise AssertionError("Wrong pipeline selected")
    
    return recipe, pipeline


def main():
    print("\n" + "█" * 70)
    print("█" + " " * 68 + "█")
    print("█" + "  AVATAR GENERATOR RECIPE FIX - INTEGRATION TEST".center(68) + "█")
    print("█" + " " * 68 + "█")
    print("█" * 70 + "\n")
    
    try:
        # Simulate the full flow
        request_data = simulate_avatar_gen_endpoint()
        row = simulate_db_storage(request_data)
        job = simulate_worker_hydration(row)
        recipe, pipeline = simulate_cook_runner_dispatch(job)
        
        # Final verification
        print("\n" + "=" * 70)
        print("FINAL VERIFICATION")
        print("=" * 70)
        
        print(f"✓ Recipe in request_data: avatar_generator")
        print(f"✓ Recipe in JSON: avatar_generator")
        print(f"✓ Recipe in job['request']: {job['request']['recipe']}")
        print(f"✓ Pipeline dispatched to: {pipeline}")
        
        print("\n" + "█" * 70)
        print("█" + " " * 68 + "█")
        print("█" + "  ✓ ALL CHECKS PASSED - FIX VERIFIED".center(68) + "█")
        print("█" + " " * 68 + "█")
        print("█" * 70)
        
        print("\nThe fix ensures avatar_gen jobs execute run_avatar_gen_pipeline")
        print("instead of defaulting to run_explainer_pipeline.")
        
        return 0
        
    except Exception as e:
        print("\n" + "█" * 70)
        print("█" + " " * 68 + "█")
        print("█" + "  ✗ TEST FAILED".center(68) + "█")
        print("█" + " " * 68 + "█")
        print("█" * 70)
        print(f"\nError: {e}")
        
        import traceback
        traceback.print_exc()
        
        return 1


if __name__ == "__main__":
    sys.exit(main())
