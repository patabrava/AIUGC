"""Integration-style test for VEO URL ingestion into Cloudflare R2."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.adapters.veo_client import get_veo_client
from app.adapters.storage_client import get_storage_client
from app.core.logging import configure_logging, get_logger


configure_logging()
logger = get_logger(__name__)


@pytest.mark.integration
def test_veo_to_r2_url_flow():
    """Test end-to-end flow from VEO URL to Cloudflare R2 ingestion."""
    operation_id = os.getenv("TEST_VEO_OPERATION_ID")
    if not operation_id:
        pytest.skip("TEST_VEO_OPERATION_ID not set; skipping integration test")

    correlation_id = "test_veo_url_flow_001"

    veo_client = get_veo_client()
    storage_client = get_storage_client()

    status = veo_client.check_operation_status(
        operation_id=operation_id,
        correlation_id=correlation_id
    )

    assert status["done"], "VEO operation must be completed for this test"

    video_data = status.get("video_data")
    assert video_data and video_data.get("video_uri"), "Missing VEO video URI"

    download_url = veo_client.get_video_download_url(
        video_uri=video_data["video_uri"],
        correlation_id=correlation_id
    )

    result = storage_client.upload_video_from_url(
        video_url=download_url,
        file_name="test_veo_url_upload.mp4",
        correlation_id=correlation_id
    )

    logger.info("test_veo_url_upload_result", result=result)

    assert result["storage_key"], "Cloudflare R2 did not return storage_key"
    assert result["url"], "Cloudflare R2 did not return url"

    print(f"✅ Success! Video URL: {result['url']}")
