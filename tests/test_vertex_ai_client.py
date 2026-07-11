from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.adapters.vertex_ai_client as vertex_module
from app.adapters.vertex_ai_client import VertexAIClient
from app.core.errors import ValidationError
from app.features.posts.prompt_builder import VEO_NEGATIVE_PROMPT


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
    assert call_kwargs["headers"]["x-goog-user-project"] == "test-project"


def test_submit_text_video_uses_quota_project_on_google_auth():
    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "operation-123"}
    mock_response.raise_for_status.return_value = None

    mock_credentials = SimpleNamespace(token="token", expired=False)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response

    with patch("app.adapters.vertex_ai_client.VertexSettings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.google.auth.default", return_value=(mock_credentials, None)) as mock_default, \
        patch("app.adapters.vertex_ai_client.Request"), \
        patch("app.adapters.vertex_ai_client.httpx.Client", return_value=mock_http):
        client = _fresh_client()
        client.submit_text_video(
            prompt="A product spins on a table.",
            correlation_id="corr-1",
            aspect_ratio="9:16",
            duration_seconds=8,
        )

    assert mock_default.call_args.kwargs["quota_project_id"] == "test-project"


def test_get_credentials_wraps_with_quota_project_when_available():
    wrapped_credentials = SimpleNamespace(token="token", expired=False)
    mock_credentials = SimpleNamespace(
        token="token",
        expired=False,
        with_quota_project=MagicMock(return_value=wrapped_credentials),
    )

    with patch("app.adapters.vertex_ai_client.VertexSettings", return_value=_settings()), \
        patch("app.adapters.vertex_ai_client.google.auth.default", return_value=(mock_credentials, None)), \
        patch("app.adapters.vertex_ai_client.Request"):
        client = _fresh_client()
        creds = client._get_credentials()

    assert creds is wrapped_credentials
    mock_credentials.with_quota_project.assert_called_once_with("test-project")


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


def test_submit_image_video_accepts_negative_prompt():
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
        client.submit_image_video(
            prompt="A locked talking-head continuation.",
            image_bytes=b"fake-bytes",
            mime_type="image/jpeg",
            correlation_id="corr-2",
            aspect_ratio="9:16",
            duration_seconds=8,
            negative_prompt="different room, camera zoom, changed wardrobe",
        )

    params = mock_http.post.call_args.kwargs["json"]["parameters"]
    assert params["negativePrompt"] == "different room, camera zoom, changed wardrobe"


def test_submit_text_video_accepts_reference_images():
    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "operation-reference-images"}
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
            prompt="The same actor speaks in a kitchen.",
            correlation_id="corr-ref",
            aspect_ratio="9:16",
            duration_seconds=8,
            reference_images=[
                {"mime_type": "image/png", "data_base64": "ZnJvbnQ="},
                {"mime_type": "image/jpeg", "data_base64": "cHJvZmlsZQ=="},
            ],
        )

    assert result["operation_id"] == "operation-reference-images"
    call_kwargs = mock_http.post.call_args.kwargs
    assert call_kwargs["json"]["instances"][0]["referenceImages"] == [
        {
            "image": {
                "bytesBase64Encoded": "ZnJvbnQ=",
                "mimeType": "image/png",
            },
            "referenceType": "asset",
        },
        {
            "image": {
                "bytesBase64Encoded": "cHJvZmlsZQ==",
                "mimeType": "image/jpeg",
            },
            "referenceType": "asset",
        },
    ]


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


def test_vertex_text_payload_includes_seed_and_negative_prompt():
    client = VertexAIClient()
    payload = client._build_request_payload(
        prompt="Starte das Video.",
        aspect_ratio="9:16",
        duration_seconds=8,
        output_gcs_uri="gs://bucket/output/",
        negative_prompt=VEO_NEGATIVE_PROMPT,
        seed=12345,
    )

    params = payload["parameters"]
    assert params["negativePrompt"] == VEO_NEGATIVE_PROMPT
    assert "burned-in subtitles" in params["negativePrompt"]
    assert "speech transcription overlays" in params["negativePrompt"]
    assert params["seed"] == 12345


def test_vertex_extension_payload_includes_seed_and_negative_prompt():
    client = VertexAIClient()
    payload = client._build_extension_request_payload(
        prompt="Weiterer Satz.",
        video_uri="gs://bucket/input.mp4",
        video_mime_type="video/mp4",
        aspect_ratio="9:16",
        duration_seconds=7,
        output_gcs_uri="gs://bucket/output/",
        negative_prompt="music bed, background voices",
        seed=12345,
    )

    params = payload["parameters"]
    assert params["negativePrompt"] == "music bed, background voices"
    assert params["seed"] == 12345


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


def test_vertex_gemini_client_singleton_is_thread_safe(monkeypatch):
    import app.adapters.vertex_gemini_client as gemini_module

    class FakeSettings:
        vertex_ai_enabled = True
        vertex_ai_project_id = "test-project"
        vertex_ai_location = "us-central1"
        vertex_gemini_model = "gemini-2.5-flash"
        vertex_gemini_image_model = "gemini-2.5-flash-image"

    monkeypatch.setattr(gemini_module, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(gemini_module.VertexGeminiClient, "_instance", None)
    monkeypatch.setattr(gemini_module, "_vertex_gemini_client", None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: gemini_module.get_vertex_gemini_client(), range(32)))

    assert len({id(client) for client in clients}) == 1


def test_generate_grounded_research_returns_text_and_chunks(monkeypatch):
    """Grounded research must surface groundingMetadata so callers can resolve redirects."""
    from app.adapters.vertex_gemini_client import VertexGeminiClient

    fake_response_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                "Forschungsdossier: Test\n\n"
                                "Quelle: https://vertexaisearch.cloud.google.com/"
                                "grounding-api-redirect/AAA"
                            )
                        }
                    ]
                },
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://vertexaisearch.cloud.google.com/"
                                       "grounding-api-redirect/AAA",
                                "title": "Tagesschau",
                            }
                        },
                        {
                            "web": {
                                "uri": "https://vertexaisearch.cloud.google.com/"
                                       "grounding-api-redirect/BBB",
                                "title": "BMAS",
                            }
                        },
                    ]
                },
            }
        ]
    }

    class _FakeResponse:
        status_code = 200

        def json(self):
            return fake_response_payload

        @property
        def text(self):
            return ""

    class _FakeHttpClient:
        def post(self, *args, **kwargs):
            return _FakeResponse()

    client = VertexGeminiClient()
    client._initialized = True
    client._http_client = _FakeHttpClient()
    monkeypatch.setattr(client, "_ensure_configured", lambda: None)
    monkeypatch.setattr(client, "_build_headers", lambda include_json=False: {})

    result = client.generate_grounded_research(prompt="topic")

    assert isinstance(result, dict)
    assert result["text"].startswith("Forschungsdossier:")
    assert len(result["grounding_chunks"]) == 2
    assert result["grounding_chunks"][0] == {
        "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA",
        "title": "Tagesschau",
    }
    assert result["grounding_chunks"][1] == {
        "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB",
        "title": "BMAS",
    }


def test_vertex_gemini_image_generation_preserves_ordered_reference_images(monkeypatch):
    import base64

    from app.adapters.vertex_gemini_client import VertexGeminiClient

    captured = {}
    client = VertexGeminiClient()

    def _fake_post_generate_content(**kwargs):
        captured.update(kwargs)
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(b"output").decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_post_generate_content", _fake_post_generate_content)

    client.generate_image(
        prompt="Compose the actor in the room.",
        model="gemini-3-pro-image-preview",
        aspect_ratio="9:16",
        input_images=[
            {"mime_type": "image/png", "image_bytes": b"actor-front"},
            {"mime_type": "image/jpeg", "image_bytes": b"actor-three-quarter"},
            {"mime_type": "image/png", "image_bytes": b"location"},
        ],
    )

    parts = captured["payload"]["contents"][0]["parts"]
    assert parts == [
        {"text": "Compose the actor in the room."},
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": base64.b64encode(b"actor-front").decode("ascii"),
            }
        },
        {
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": base64.b64encode(b"actor-three-quarter").decode("ascii"),
            }
        },
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": base64.b64encode(b"location").decode("ascii"),
            }
        },
    ]
