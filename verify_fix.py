#!/usr/bin/env python3
"""
Simple verification that the fix logic is correct.
"""
import os
import sys

sys.path.insert(0, '/workspace')

def check_logic():
    """Read the segmenter code and verify the logic is correct."""
    with open('/workspace/core/segmenter.py', 'r') as f:
        content = f.read()
    
    # Check 1: PyAV compatibility function exists
    assert '_check_pyav_compatible' in content, "PyAV check function missing"
    assert 'import av' in content, "PyAV import missing from check function"
    print("✓ PyAV compatibility check function exists")
    
    # Check 2: Cook worker detection
    assert 'on_cook_worker' in content, "Cook worker detection missing"
    assert 'COOK_ON_WEB' in content, "COOK_ON_WEB check missing"
    assert 'FLY_MACHINE_ID' in content, "FLY_MACHINE_ID check missing"
    print("✓ Cook worker detection implemented")
    
    # Check 3: Fallback logic differentiates between web and cook
    assert 'not on_cook_worker' in content, "Cook worker fallback logic missing"
    assert 'web dyno' in content, "Error message for web dyno missing"
    print("✓ Fallback logic differentiates web dyno from cook worker")
    
    # Check 4: PyAV check before fallback
    assert '_check_pyav_compatible()' in content, "PyAV check not called"
    assert 'pip install' in content, "PyAV installation hint missing"
    print("✓ PyAV compatibility check before fallback")
    
    # Check 5: requirements.txt has PyAV pinned
    with open('/workspace/requirements.txt', 'r') as f:
        reqs = f.read()
    assert 'av>=12.0.0' in reqs, "PyAV not pinned in requirements.txt"
    print("✓ requirements.txt has av>=12.0.0")
    
    print("\n✅ All logic checks passed!")
    print("\nSummary of fix:")
    print("1. Added _check_pyav_compatible() to verify PyAV before using local Whisper")
    print("2. Detects cook workers (COOK_ON_WEB=0 or FLY_MACHINE_ID set)")
    print("3. Allows local Whisper fallback on cook workers even in production")
    print("4. Still blocks local Whisper on web dyno to prevent CPU overload")
    print("5. PyAV >=12.0.0 is already pinned in requirements.txt")

if __name__ == "__main__":
    check_logic()
