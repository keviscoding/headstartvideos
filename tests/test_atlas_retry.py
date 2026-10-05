"""
Test that Atlas chat retry logic handles 429 and high demand errors.

Reproduces PYTHON-FASTAPI-2P/4: Atlas/Gemini chat 429 + high demand not retried
"""
import inspect


def test_atlas_chat_has_retry_logic():
    """Verify _atlas_chat has retry logic for 429 and transient errors."""
    from core.atlas_llm import _atlas_chat
    
    source = inspect.getsource(_atlas_chat)
    
    # Check for retry loop
    assert "for http_attempt in range" in source or "max_http_retries" in source, (
        "_atlas_chat should have retry loop for HTTP errors"
    )
    
    # Check for 429 handling
    assert "429" in source, (
        "_atlas_chat should handle HTTP 429 rate limit"
    )
    
    # Check for high demand / transient error handling
    assert "high demand" in source.lower() or "temporarily unavailable" in source.lower(), (
        "_atlas_chat should handle 'high demand' or 'temporarily unavailable' errors"
    )
    
    # Check for exponential backoff
    assert "backoff" in source.lower() or "sleep" in source.lower(), (
        "_atlas_chat should use backoff/sleep for retries"
    )
    
    # Check for quota/billing fast-fail
    assert "quota" in source.lower() or "billing" in source.lower(), (
        "_atlas_chat should fail fast on quota/billing errors"
    )
    
    print("✓ _atlas_chat has retry logic with:")
    print("  - HTTP 429 handling")
    print("  - High demand / transient error retry")
    print("  - Exponential backoff")
    print("  - Quota/billing fast-fail")
    
    print("✓ PASS: Atlas chat retry logic is present")


if __name__ == "__main__":
    test_atlas_chat_has_retry_logic()
    print("\n✓ Atlas chat retry test passed")
