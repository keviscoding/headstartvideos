"""
Test that PyAV metadata_errors parameter is handled correctly.

Reproduces PYTHON-FASTAPI-4B/4C: TypeError when av.open doesn't accept metadata_errors
"""
import inspect


def test_av_metadata_errors_support():
    """Verify that the installed av version accepts metadata_errors parameter."""
    try:
        import av
    except ImportError:
        print("⚠ av not installed, skipping test")
        return
    
    # Try to check signature (may not work on all av versions)
    try:
        sig = inspect.signature(av.open)
        has_param = 'metadata_errors' in sig.parameters
        print(f"av {av.__version__} signature inspection: metadata_errors = {has_param}")
    except (ValueError, TypeError):
        # Signature not inspectable (e.g. built-in function in av 18+)
        print(f"av {av.__version__} signature not inspectable (this is OK)")
    
    # Actually test by calling it with a minimal file
    import tempfile
    import os
    
    # Create a minimal valid audio file
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        wav_path = f.name
    
    try:
        # Write a minimal WAV file (44 bytes header + silence)
        with open(wav_path, 'wb') as f:
            # RIFF header
            f.write(b'RIFF')
            f.write((36).to_bytes(4, 'little'))
            f.write(b'WAVE')
            # fmt chunk
            f.write(b'fmt ')
            f.write((16).to_bytes(4, 'little'))
            f.write((1).to_bytes(2, 'little'))   # PCM
            f.write((1).to_bytes(2, 'little'))   # 1 channel
            f.write((16000).to_bytes(4, 'little'))  # sample rate
            f.write((32000).to_bytes(4, 'little'))  # byte rate
            f.write((2).to_bytes(2, 'little'))   # block align
            f.write((16).to_bytes(2, 'little'))  # bits per sample
            # data chunk
            f.write(b'data')
            f.write((0).to_bytes(4, 'little'))
        
        # Test with metadata_errors parameter (what faster-whisper uses)
        try:
            container = av.open(wav_path, metadata_errors="ignore")
            container.close()
            print(f"✓ av {av.__version__} accepts metadata_errors parameter")
        except TypeError as e:
            if 'metadata_errors' in str(e):
                raise AssertionError(
                    f"av {av.__version__} does NOT accept metadata_errors parameter. "
                    f"This will break faster-whisper 1.2.1. "
                    f"Ensure av>=12.0.0,<19.0.0 is installed. Error: {e}"
                )
            raise
    finally:
        os.unlink(wav_path)
    
    print("✓ PASS: av supports metadata_errors parameter")


def test_segmenter_transcribe_local_error_handling():
    """Verify _transcribe_local provides clear error on metadata_errors TypeError."""
    from core.segmenter import _transcribe_local
    import inspect
    
    # Check that the function has error handling for TypeError
    source = inspect.getsource(_transcribe_local)
    
    assert "except TypeError" in source, (
        "_transcribe_local should have TypeError exception handling"
    )
    assert "metadata_errors" in source, (
        "_transcribe_local should mention metadata_errors in error handling"
    )
    
    print("✓ PASS: _transcribe_local has metadata_errors error handling")


if __name__ == "__main__":
    test_av_metadata_errors_support()
    test_segmenter_transcribe_local_error_handling()
    print("\n✓ All PyAV metadata_errors tests passed")
