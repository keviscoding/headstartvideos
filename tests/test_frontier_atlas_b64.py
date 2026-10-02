"""
Unit tests for Frontier Atlas image generation.

Tests the PROVEN Whop Frontier create→poll→download pattern.
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest


def test_atlas_create_poll_download_success():
    """
    Test the PROVEN Mac path: create → poll → download from URL.
    
    Response shapes (exact):
    - Create: {code: 200, data: {id: "pred_xxx"}}
    - Poll: {data: {status: "succeeded", outputs: ["https://...jpg"]}}
    - Download: JPEG bytes
    """
    from core.frontier_atlas import _generate_atlas_image
    
    # Mock create response
    mock_create = Mock()
    mock_create.status_code = 200
    mock_create.json.return_value = {
        "code": 200,
        "data": {"id": "pred_12345"}
    }
    
    # Mock poll response (succeeded)
    mock_poll = Mock()
    mock_poll.status_code = 200
    mock_poll.json.return_value = {
        "data": {
            "status": "succeeded",
            "outputs": ["https://example.com/still.jpg"]
        }
    }
    
    # Mock download response (minimal JPEG)
    minimal_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF' + b'\x00' * 1024
    mock_download = Mock()
    mock_download.status_code = 200
    mock_download.content = minimal_jpeg
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.jpg"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create):
                with patch('requests.get', side_effect=[mock_poll, mock_download]):
                    _generate_atlas_image(
                        prompt="Test prompt",
                        output_path=output_path,
                    )
        
        # Verify JPEG was written
        assert output_path.exists()
        assert output_path.stat().st_size > 1000
        assert output_path.read_bytes().startswith(b'\xff\xd8\xff')


def test_atlas_poll_processing_then_success():
    """
    Test polling with processing → succeeded transition.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    mock_create = Mock()
    mock_create.status_code = 200
    mock_create.json.return_value = {"code": 200, "data": {"id": "pred_12345"}}
    
    # First poll: processing
    mock_poll_processing = Mock()
    mock_poll_processing.status_code = 200
    mock_poll_processing.json.return_value = {
        "data": {"status": "processing"}
    }
    
    # Second poll: succeeded
    mock_poll_success = Mock()
    mock_poll_success.status_code = 200
    mock_poll_success.json.return_value = {
        "data": {
            "status": "succeeded",
            "outputs": ["https://example.com/still.jpg"]
        }
    }
    
    minimal_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF' + b'\x00' * 1024
    mock_download = Mock()
    mock_download.status_code = 200
    mock_download.content = minimal_jpeg
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.jpg"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create):
                with patch('requests.get', side_effect=[mock_poll_processing, mock_poll_success, mock_download]):
                    with patch('time.sleep'):  # Skip sleep delays
                        _generate_atlas_image(
                            prompt="Test prompt",
                            output_path=output_path,
                        )
        
        assert output_path.exists()


def test_atlas_prediction_failed():
    """
    Test that failed predictions raise RuntimeError.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    mock_create = Mock()
    mock_create.status_code = 200
    mock_create.json.return_value = {"code": 200, "data": {"id": "pred_12345"}}
    
    mock_poll_failed = Mock()
    mock_poll_failed.status_code = 200
    mock_poll_failed.json.return_value = {
        "data": {
            "status": "failed",
            "error": "GPU out of memory"
        }
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.jpg"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create):
                with patch('requests.get', return_value=mock_poll_failed):
                    with patch('time.sleep'):
                        with pytest.raises(RuntimeError, match="failed"):
                            _generate_atlas_image(
                                prompt="Test prompt",
                                output_path=output_path,
                            )


def test_atlas_create_missing_data_id():
    """
    Test that create response without data.id raises error.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    mock_create = Mock()
    mock_create.status_code = 200
    mock_create.json.return_value = {"code": 200}  # No data.id
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.jpg"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create):
                with pytest.raises(RuntimeError, match="prediction id"):
                    _generate_atlas_image(
                        prompt="Test prompt",
                        output_path=output_path,
                    )


def test_atlas_no_outputs():
    """
    Test that succeeded with no outputs raises error.
    """
    from core.frontier_atlas import _generate_atlas_image
    
    mock_create = Mock()
    mock_create.status_code = 200
    mock_create.json.return_value = {"code": 200, "data": {"id": "pred_12345"}}
    
    mock_poll = Mock()
    mock_poll.status_code = 200
    mock_poll.json.return_value = {
        "data": {
            "status": "succeeded",
            "outputs": []  # Empty outputs
        }
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test_still.jpg"
        
        with patch('core.frontier_atlas.ATLASCLOUD_KEY', 'test-key'):
            with patch('requests.post', return_value=mock_create):
                with patch('requests.get', return_value=mock_poll):
                    with patch('time.sleep'):
                        with pytest.raises(RuntimeError, match="no outputs"):
                            _generate_atlas_image(
                                prompt="Test prompt",
                                output_path=output_path,
                            )
