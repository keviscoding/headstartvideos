"""
Unit tests for Frontier Atlas image generation.

Tests the Whop Frontier create→poll pattern for Atlas image generation.
"""

import base64
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def test_atlas_image_create_poll_success():
    """
    Test that _generate_atlas_image uses Whop's create→poll pattern.
    
    Flow:
    1. POST /api/v1/model/generateImage → prediction_id
    2. GET /api/v1/model/predictions/{id} → status: processing
    3. GET /api/v1/model/predictions/{id} → status: succeeded, output: base64
    """
    from core.frontier_atlas import _generate_atlas_image
    
    # Create a minimal 1x1 PNG in base64
    minimal_png = (
        b'\x89PNG\r\n\x1a\n'  # PNG signature
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x02\x00\x00\x00\x90wS\xde'
        b'\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    b64_png = base64.b64encode(minimal_png).decode('utf-8')
    
    # Mock create response
    mock_create_response = Mock()
    mock_create_response.status_code = 200
    mock_create_response.json.return_value = {
        "id": "pred_12345",
    }
    
    # Mock poll responses (processing → succeeded)
    mock_poll_processing = Mock()
    mock_poll_processing.status_code = 200
    mock_poll_processing.json.return_value = {
        "id": "pred_12345",
        "status": "processing",
    }
    
    mock_poll_success = Mock()
    mock_poll_success.status_code = 200
    mock_poll_success.json.return_value = {
        "id": "pred_12345",
        "status": "succeeded",
        "output": b64_png,
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.png"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create_response) as mock_post:
                with patch('requests.get', side_effect=[mock_poll_processing, mock_poll_success]) as mock_get:
                    _generate_atlas_image(
                        prompt="Test prompt",
                        output_path=output_path,
                        model="gpt-image-2",
                    )
        
        # Verify create was called
        mock_post.assert_called_once()
        assert "generateImage" in mock_post.call_args[0][0]
        
        # Verify poll was called (at least once, may be more if processing)
        assert mock_get.call_count >= 1
        
        # Verify PNG was written
        assert output_path.exists()
        assert output_path.stat().st_size > 0
        
        # Verify it's valid PNG data
        png_data = output_path.read_bytes()
        assert png_data.startswith(b'\x89PNG')


def test_atlas_image_output_as_list():
    """
    Test handling of output as list of base64 strings.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    minimal_png = (
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x02\x00\x00\x00\x90wS\xde'
        b'\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    b64_png = base64.b64encode(minimal_png).decode('utf-8')
    
    mock_create_response = Mock()
    mock_create_response.status_code = 200
    mock_create_response.json.return_value = {"id": "pred_12345"}
    
    mock_poll_success = Mock()
    mock_poll_success.status_code = 200
    mock_poll_success.json.return_value = {
        "id": "pred_12345",
        "status": "succeeded",
        "output": [b64_png],  # Output as array
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.png"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create_response):
                with patch('requests.get', return_value=mock_poll_success):
                    _generate_atlas_image(
                        prompt="Test prompt",
                        output_path=output_path,
                        model="gpt-image-2",
                    )
        
        assert output_path.exists()
        assert output_path.read_bytes() == minimal_png


def test_atlas_image_prediction_failed():
    """
    Test that _generate_atlas_image raises error when prediction fails.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    mock_create_response = Mock()
    mock_create_response.status_code = 200
    mock_create_response.json.return_value = {"id": "pred_12345"}
    
    mock_poll_failed = Mock()
    mock_poll_failed.status_code = 200
    mock_poll_failed.json.return_value = {
        "id": "pred_12345",
        "status": "failed",
        "error": "GPU out of memory",
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.png"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create_response):
                with patch('requests.get', return_value=mock_poll_failed):
                    with pytest.raises(RuntimeError, match="Atlas prediction failed"):
                        _generate_atlas_image(
                            prompt="Test prompt",
                            output_path=output_path,
                            model="gpt-image-2",
                        )


def test_atlas_image_no_prediction_id():
    """
    Test that _generate_atlas_image raises error when create returns no ID.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    mock_create_response = Mock()
    mock_create_response.status_code = 200
    mock_create_response.json.return_value = {}  # No ID
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.png"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create_response):
                with pytest.raises(RuntimeError, match="No prediction ID"):
                    _generate_atlas_image(
                        prompt="Test prompt",
                        output_path=output_path,
                        model="gpt-image-2",
                    )
