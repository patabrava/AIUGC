from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.vertex_ai_client import VertexAIClient
from app.core.errors import ValidationError


def _settings(enabled: bool = True):
    return SimpleNamespace(
        vertex_ai_enabled=enabled,
        vertex_ai_project_id="test-project" if enabled else "",
        vertex_ai_location="us-central1",
    )


def _fresh_client():
    VertexAIClient._instance = None
    return VertexAIClient()


def test_submit_text_video_uses_vertex_genai_client():
    mock_operation = MagicMock()
    mock_operation.name = "operation-123"
    mock_models = MagicMock()
    mock_models.generate_videos.return_value = mock_operation
    mock_client = MagicMock()
    mock_client.models = mock_models

    with patch("app.adapters.vertex_ai_client.get_settings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.genai.Client", return_value=mock_client):
        client = _fresh_client()
        result = client.submit_text_video(
            prompt="A product spins on a table.",
            correlation_id="corr-1",
            aspect_ratio="9:16",
            duration_seconds=8,
            output_gcs_uri="gs://bucket/prefix",
        )

    assert result["status"] == "submitted"
    assert result["operation_id"] == "operation-123"
    mock_models.generate_videos.assert_called_once()


def test_submit_image_video_accepts_image_bytes():
    mock_operation = MagicMock()
    mock_operation.name = "operation-456"
    mock_models = MagicMock()
    mock_models.generate_videos.return_value = mock_operation
    mock_client = MagicMock()
    mock_client.models = mock_models

    with patch("app.adapters.vertex_ai_client.get_settings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.genai.Client", return_value=mock_client), \
        patch("app.adapters.vertex_ai_client.Image") as mock_image:
        mock_image.return_value = MagicMock()
        client = _fresh_client()
        result = client.submit_image_video(
            prompt="A cinematic reveal.",
            image_bytes=b"fake-bytes",
            mime_type="image/jpeg",
            correlation_id="corr-2",
            aspect_ratio="16:9",
            duration_seconds=8,
            output_gcs_uri="gs://bucket/prefix",
        )

    assert result["status"] == "submitted"
    assert result["operation_id"] == "operation-456"
    mock_image.assert_called_once_with(imageBytes=b"fake-bytes", mimeType="image/jpeg")
    mock_models.generate_videos.assert_called_once()


def test_check_operation_status_returns_video_uri():
    video = SimpleNamespace(uri="gs://bucket/result.mp4")
    generated = SimpleNamespace(video=video)
    response = SimpleNamespace(generated_videos=[generated])
    operation = SimpleNamespace(done=True, response=response)

    mock_client = MagicMock()
    mock_client.models = MagicMock()
    mock_client.operations.get.return_value = operation

    with patch("app.adapters.vertex_ai_client.get_settings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.genai.Client", return_value=mock_client):
        client = _fresh_client()
        status = client.check_operation_status(
            operation_id="operation-789",
            correlation_id="corr-3",
        )

    assert status["done"] is True
    assert status["status"] == "completed"
    assert status["video_uri"] == "gs://bucket/result.mp4"


def test_submit_requires_vertex_config_enabled():
    with patch("app.adapters.vertex_ai_client.get_settings", return_value=_settings(enabled=False)):
        client = _fresh_client()
        with pytest.raises(ValidationError):
            client.submit_text_video(
                prompt="No config.",
                correlation_id="corr-4",
                aspect_ratio="16:9",
                duration_seconds=8,
                output_gcs_uri="gs://bucket/prefix",
            )
