"""
Unit tests for Frontier Atlas image generation.

Tests the critical b64_json fallback path that was broken in the initial implementation.
"""

import base64
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def test_atlas_image_b64_json_response():
    """
    Test that _generate_atlas_image handles b64_json response format.
    
    Critical: Atlas returns data[0]["b64_json"] instead of data[0]["url"]
    in some cases. This test verifies the fallback path works correctly.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    # Create a minimal 1x1 PNG in base64
    # PNG signature + minimal IHDR/IDAT/IEND chunks
    minimal_png = (
        b'\x89PNG\r\n\x1a\n'  # PNG signature
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x02\x00\x00\x00\x90wS\xde'
        b'\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    b64_png = base64.b64encode(minimal_png).decode('utf-8')
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {
                "b64_json": b64_png,
                # No "url" key - this is the critical bug scenario
            }
        ]
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.png"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_response):
                _generate_atlas_image(
                    prompt="Test prompt",
                    output_path=output_path,
                    model="gpt-image-2",
                )
        
        # Verify PNG was written
        assert output_path.exists()
        assert output_path.stat().st_size > 0
        
        # Verify it's valid PNG data
        png_data = output_path.read_bytes()
        assert png_data.startswith(b'\x89PNG')


def test_atlas_image_url_response():
    """
    Test that _generate_atlas_image handles URL response format (preferred path).
    """
    from core.frontier_atlas import _generate_atlas_image
    
    minimal_png = (
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x02\x00\x00\x00\x90wS\xde'
        b'\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    
    mock_post_response = Mock()
    mock_post_response.status_code = 200
    mock_post_response.json.return_value = {
        "data": [
            {
                "url": "https://example.com/image.png",
            }
        ]
    }
    
    mock_get_response = Mock()
    mock_get_response.status_code = 200
    mock_get_response.content = minimal_png
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.png"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_post_response):
                with patch('requests.get', return_value=mock_get_response):
                    _generate_atlas_image(
                        prompt="Test prompt",
                        output_path=output_path,
                        model="gpt-image-2",
                    )
        
        # Verify PNG was written
        assert output_path.exists()
        assert output_path.stat().st_size > 0
        assert output_path.read_bytes() == minimal_png


def test_atlas_image_no_data_error():
    """
    Test that _generate_atlas_image raises error when response has no data.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": []  # Empty data array
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.png"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_response):
                with pytest.raises(RuntimeError, match="No data in Atlas response"):
                    _generate_atlas_image(
                        prompt="Test prompt",
                        output_path=output_path,
                        model="gpt-image-2",
                    )


def test_atlas_image_neither_url_nor_b64():
    """
    Test that _generate_atlas_image raises error when response has neither url nor b64_json.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {
                "unexpected_key": "unexpected_value"
            }
        ]
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.png"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_response):
                with pytest.raises(RuntimeError, match="No url or b64_json"):
                    _generate_atlas_image(
                        prompt="Test prompt",
                        output_path=output_path,
                        model="gpt-image-2",
                    )
