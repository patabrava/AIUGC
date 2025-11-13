"""
Tests for ImageKit URL-based upload.
Per Constitution § VIII: Whole-app testscripts.
"""

import pytest

from app.adapters.imagekit_client import get_imagekit_client


@pytest.mark.integration
def test_upload_video_from_url():
    """Verify ImageKit client can upload from a URL."""
    client = get_imagekit_client()

    # Use ImageKit-hosted demo video to ensure accessibility and size compliance
    test_url = "https://ik.imagekit.io/demo/sample-video.mp4"

    result = client.upload_video_from_url(
        video_url=test_url,
        file_name="test_url_upload.mp4",
        correlation_id="test_url_upload_001"
    )

    assert result["file_id"], "ImageKit did not return file_id"
    assert result["url"], "ImageKit did not return url"
    assert result["file_path"].startswith("/flow-forge/videos"), "Unexpected upload folder"
