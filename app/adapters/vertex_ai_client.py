"""
Vertex AI Video Generation Adapter
Explicit Vertex AI path for text-to-video and image-to-video.
"""

from __future__ import annotations

from typing import Optional, Dict, Any
import inspect

from google import genai

try:
    from google.genai.types import Image, GenerateVideosConfig  # type: ignore
except Exception:  # pragma: no cover - fallback for older SDKs
    from google.genai import types as genai_types  # type: ignore

    Image = getattr(genai_types, "Image", None)
    GenerateVideosConfig = None  # type: ignore

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_VERTEX_MODEL = "veo-3.1-generate-001"
_DEFAULT_VERTEX_FAST_MODEL = "veo-3.1-fast-generate-001"


class VertexAIClient:
    """Singleton adapter for Vertex AI Veo video generation."""

    _instance: Optional["VertexAIClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._settings = get_settings()
        self._client: Optional[genai.Client] = None
        self._initialized = True
        logger.info("vertex_ai_client_initialized")

    def submit_text_video(
        self,
        *,
        prompt: str,
        correlation_id: str,
        aspect_ratio: str,
        duration_seconds: int,
        output_gcs_uri: Optional[str] = None,
        model: Optional[str] = None,
        use_fast_model: bool = False,
    ) -> Dict[str, Any]:
        client = self._get_client()
        model_name = model or (_DEFAULT_VERTEX_FAST_MODEL if use_fast_model else _DEFAULT_VERTEX_MODEL)
        config = self._build_generate_config(
            aspect_ratio=aspect_ratio,
            output_gcs_uri=output_gcs_uri,
            duration_seconds=duration_seconds,
        )
        self._log_request(
            correlation_id=correlation_id,
            model=model_name,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            output_gcs_uri=output_gcs_uri,
            has_image=False,
        )
        try:
            operation = client.models.generate_videos(  # type: ignore[attr-defined]
                model=model_name,
                prompt=prompt,
                config=config,
            )
        except AttributeError as exc:
            raise ValidationError(
                "google-genai SDK is missing video generation support.",
                {"error": str(exc)},
            ) from exc
        return self._normalize_operation(operation, model_name)

    def submit_image_video(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        mime_type: str,
        correlation_id: str,
        aspect_ratio: str,
        duration_seconds: int,
        output_gcs_uri: Optional[str] = None,
        model: Optional[str] = None,
        use_fast_model: bool = False,
    ) -> Dict[str, Any]:
        if Image is None:
            raise ValidationError(
                "google-genai SDK is missing image support for video generation.",
                {"error": "Image type unavailable"},
            )
        client = self._get_client()
        model_name = model or (_DEFAULT_VERTEX_FAST_MODEL if use_fast_model else _DEFAULT_VERTEX_MODEL)
        config = self._build_generate_config(
            aspect_ratio=aspect_ratio,
            output_gcs_uri=output_gcs_uri,
            duration_seconds=duration_seconds,
        )
        image = Image(imageBytes=image_bytes, mimeType=mime_type)  # type: ignore[call-arg]
        self._log_request(
            correlation_id=correlation_id,
            model=model_name,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            output_gcs_uri=output_gcs_uri,
            has_image=True,
            image_bytes_len=len(image_bytes),
            image_mime_type=mime_type,
        )
        try:
            operation = client.models.generate_videos(  # type: ignore[attr-defined]
                model=model_name,
                prompt=prompt,
                image=image,
                config=config,
            )
        except AttributeError as exc:
            raise ValidationError(
                "google-genai SDK is missing video generation support.",
                {"error": str(exc)},
            ) from exc
        return self._normalize_operation(operation, model_name)

    def check_operation_status(
        self,
        *,
        operation_id: str,
        correlation_id: str,
    ) -> Dict[str, Any]:
        client = self._get_client()
        if not hasattr(client, "operations"):
            raise ValidationError(
                "google-genai SDK is missing operation polling support.",
                {"operation_id": operation_id},
            )
        try:
            operation = client.operations.get(operation_id)  # type: ignore[attr-defined]
        except Exception as exc:
            raise ValidationError(
                "Vertex AI operation polling failed.",
                {"operation_id": operation_id, "error": str(exc)},
            ) from exc

        done = bool(getattr(operation, "done", False))
        video_uri = self._extract_video_uri(operation)
        return {
            "operation_id": operation_id,
            "done": done,
            "status": "completed" if done else "processing",
            "video_uri": video_uri,
            "provider": "vertex_ai",
        }

    def _get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client
        self._ensure_configured()
        try:
            self._client = genai.Client(
                vertexai=True,
                project=self._settings.vertex_ai_project_id,
                location=self._settings.vertex_ai_location,
            )
        except Exception as exc:
            raise ValidationError(
                "Vertex AI authentication unavailable. Configure ADC or a service account.",
                {"error": str(exc)},
            ) from exc
        return self._client

    def _ensure_configured(self) -> None:
        if not self._settings.vertex_ai_enabled:
            raise ValidationError(
                "Vertex AI is not enabled for this environment.",
                {"vertex_ai_enabled": self._settings.vertex_ai_enabled},
            )
        if not self._settings.vertex_ai_project_id:
            raise ValidationError(
                "Vertex AI project ID is required.",
                {"vertex_ai_project_id": self._settings.vertex_ai_project_id},
            )

    def _build_generate_config(
        self,
        *,
        aspect_ratio: str,
        output_gcs_uri: Optional[str],
        duration_seconds: int,
    ) -> Any:
        payload: Dict[str, Any] = {
            "aspect_ratio": aspect_ratio,
        }
        if output_gcs_uri:
            payload["output_gcs_uri"] = output_gcs_uri
        if duration_seconds:
            payload["duration_seconds"] = duration_seconds
        if GenerateVideosConfig is None:
            return payload
        try:
            allowed = set(inspect.signature(GenerateVideosConfig).parameters)
            payload = {k: v for k, v in payload.items() if k in allowed}
        except (TypeError, ValueError):
            pass
        return GenerateVideosConfig(**payload)  # type: ignore[arg-type]

    def _normalize_operation(self, operation: Any, model_name: str) -> Dict[str, Any]:
        operation_id = (
            getattr(operation, "name", None)
            or getattr(operation, "id", None)
            or getattr(operation, "operation", None)
        )
        if not operation_id and isinstance(operation, dict):
            operation_id = operation.get("name") or operation.get("id") or operation.get("operation")
        if not operation_id:
            raise ValidationError(
                "Vertex AI response missing operation identifier.",
                {"operation": str(operation)},
            )
        return {
            "operation_id": operation_id,
            "status": "submitted",
            "done": False,
            "provider": "vertex_ai",
            "provider_model": model_name,
        }

    def _extract_video_uri(self, operation: Any) -> Optional[str]:
        response = getattr(operation, "response", None) or getattr(operation, "result", None)
        if response is None:
            return None
        generated = getattr(response, "generated_videos", None) or getattr(response, "generatedVideos", None)
        if not generated:
            return None
        first = generated[0]
        video = getattr(first, "video", None) or getattr(first, "Video", None)
        if video is None:
            return None
        return getattr(video, "uri", None) or getattr(video, "URI", None)

    def _log_request(
        self,
        *,
        correlation_id: str,
        model: str,
        prompt: str,
        aspect_ratio: str,
        duration_seconds: int,
        output_gcs_uri: Optional[str],
        has_image: bool,
        image_bytes_len: Optional[int] = None,
        image_mime_type: Optional[str] = None,
    ) -> None:
        logger.info(
            "vertex_ai_video_submission",
            correlation_id=correlation_id,
            model=model,
            prompt_length=len(prompt),
            prompt_preview=prompt[:400],
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            output_gcs_uri=output_gcs_uri,
            has_image=has_image,
            image_bytes_len=image_bytes_len,
            image_mime_type=image_mime_type,
        )


def get_vertex_ai_client() -> VertexAIClient:
    """Get Vertex AI client singleton."""
    return VertexAIClient()
