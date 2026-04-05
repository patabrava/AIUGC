from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.adapters.vertex_ai_client as vertex_module
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


def test_submit_text_video_posts_vertex_rest_payload():
    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "operation-123"}
    mock_response.raise_for_status.return_value = None

    mock_credentials = SimpleNamespace(token="token", expired=False)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response

    with patch("app.adapters.vertex_ai_client.VertexSettings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.google.auth.default", return_value=(mock_credentials, None)), \
        patch("app.adapters.vertex_ai_client.Request"), \
        patch("app.adapters.vertex_ai_client.httpx.Client", return_value=mock_http):
        client = _fresh_client()
        result = client.submit_text_video(
            prompt="A product spins on a table.",
            correlation_id="corr-1",
            aspect_ratio="9:16",
            duration_seconds=8,
        )

    assert result["status"] == "submitted"
    assert result["operation_id"] == "operation-123"
    mock_http.post.assert_called_once()
    call_kwargs = mock_http.post.call_args.kwargs
    assert call_kwargs["json"]["parameters"]["aspectRatio"] == "9:16"
    assert call_kwargs["json"]["parameters"]["durationSeconds"] == 8


def test_submit_image_video_accepts_image_bytes():
    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "operation-456"}
    mock_response.raise_for_status.return_value = None

    mock_credentials = SimpleNamespace(token="token", expired=False)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response

    with patch("app.adapters.vertex_ai_client.VertexSettings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.google.auth.default", return_value=(mock_credentials, None)), \
        patch("app.adapters.vertex_ai_client.Request"), \
        patch("app.adapters.vertex_ai_client.httpx.Client", return_value=mock_http):
        client = _fresh_client()
        result = client.submit_image_video(
            prompt="A cinematic reveal.",
            image_bytes=b"fake-bytes",
            mime_type="image/jpeg",
            correlation_id="corr-2",
            aspect_ratio="16:9",
            duration_seconds=8,
        )

    assert result["status"] == "submitted"
    assert result["operation_id"] == "operation-456"
    call_kwargs = mock_http.post.call_args.kwargs
    assert call_kwargs["json"]["instances"][0]["image"]["bytesBase64Encoded"]
    assert call_kwargs["json"]["instances"][0]["image"]["mimeType"] == "image/jpeg"


def test_submit_video_extension_uses_gcs_uri_and_storage_uri():
    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "operation-vertex-ext"}
    mock_response.raise_for_status.return_value = None

    mock_credentials = SimpleNamespace(token="token", expired=False)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response

    with patch("app.adapters.vertex_ai_client.VertexSettings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.google.auth.default", return_value=(mock_credentials, None)), \
        patch("app.adapters.vertex_ai_client.Request"), \
        patch("app.adapters.vertex_ai_client.httpx.Client", return_value=mock_http):
        client = _fresh_client()
        result = client.submit_video_extension(
            prompt="Continue the scene.",
            video_uri="gs://bucket/input/base.mp4",
            video_mime_type="video/mp4",
            correlation_id="corr-ext",
            aspect_ratio="9:16",
            duration_seconds=7,
            output_gcs_uri="gs://bucket/output/",
        )

    assert result["status"] == "submitted"
    assert result["operation_id"] == "operation-vertex-ext"
    call_kwargs = mock_http.post.call_args.kwargs
    assert call_kwargs["json"]["instances"][0]["video"]["gcsUri"] == "gs://bucket/input/base.mp4"
    assert call_kwargs["json"]["instances"][0]["video"]["mimeType"] == "video/mp4"
    assert call_kwargs["json"]["parameters"]["durationSeconds"] == 7
    assert call_kwargs["json"]["parameters"]["storageUri"] == "gs://bucket/output/"


def test_check_operation_status_returns_video_uri():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "done": True,
        "response": {
            "videos": [
                {
                    "gcsUri": "gs://bucket/result.mp4",
                }
            ]
        },
    }
    mock_response.raise_for_status.return_value = None

    mock_credentials = SimpleNamespace(token="token", expired=False)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response

    with patch("app.adapters.vertex_ai_client.VertexSettings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.google.auth.default", return_value=(mock_credentials, None)), \
        patch("app.adapters.vertex_ai_client.Request"), \
        patch("app.adapters.vertex_ai_client.httpx.Client", return_value=mock_http):
        client = _fresh_client()
        status = client.check_operation_status(
            operation_id="projects/test-project/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/operation-789",
            correlation_id="corr-3",
        )

    assert status["done"] is True
    assert status["status"] == "completed"
    assert status["video_uri"] == "gs://bucket/result.mp4"
    assert mock_http.post.call_args.kwargs["json"] == {
        "operationName": "projects/test-project/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/operation-789"
    }


def test_submit_requires_vertex_config_enabled():
    with patch("app.adapters.vertex_ai_client.VertexSettings", return_value=_settings(enabled=False)):
        client = _fresh_client()
        with pytest.raises(ValidationError):
            client.submit_text_video(
                prompt="No config.",
                correlation_id="corr-4",
                aspect_ratio="16:9",
                duration_seconds=8,
            )


def test_vertex_client_loads_vertex_settings_from_shared_env(monkeypatch):
    class FakeVertexSettings:
        vertex_ai_enabled = True
        vertex_ai_project_id = "shared-project"
        vertex_ai_location = "europe-west4"

    monkeypatch.setattr(vertex_module, "VertexSettings", lambda: FakeVertexSettings())
    client = _fresh_client()
    assert client._settings.vertex_ai_project_id == "shared-project"
    assert client._settings.vertex_ai_location == "europe-west4"
