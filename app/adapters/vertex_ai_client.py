"""
Vertex AI Video Generation Adapter
Uses Vertex AI REST API with the correct polling endpoint (:fetchPredictOperation).
The standard LRO operations endpoint returns 404 for Veo UUID-style operations;
fetchPredictOperation on the model path is the correct way to poll.
"""

from __future__ import annotations

import os
import base64
from copy import deepcopy
from typing import Optional, Dict, Any

import httpx
import google.auth
from google.auth.transport.requests import Request

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.config import resolve_google_application_credentials_path

logger = get_logger(__name__)

_DEFAULT_VERTEX_MODEL = "veo-3.1-generate-001"
_DEFAULT_VERTEX_FAST_MODEL = "veo-3.1-fast-generate-001"


class VertexSettings(BaseSettings):
    """Vertex-only settings loaded from the shared app .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    vertex_ai_enabled: bool = Field(default=False, validation_alias=AliasChoices("VERTEX_AI_ENABLED"))
    vertex_ai_project_id: str = Field(
        default="",
        validation_alias=AliasChoices("VERTEX_AI_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"),
    )
    vertex_ai_location: str = Field(default="us-central1", validation_alias=AliasChoices("VERTEX_AI_LOCATION"))
    google_application_credentials: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_APPLICATION_CREDENTIALS"),
    )
    google_application_credentials_json: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_APPLICATION_CREDENTIALS_JSON"),
    )


class VertexAIClient:
    """Singleton adapter for Vertex AI Veo video generation via REST."""

    _instance: Optional["VertexAIClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._settings = self._load_vertex_settings()
        self._http_client = httpx.Client(timeout=120.0, follow_redirects=True)
        self._credentials = None
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
        self._ensure_configured()
        model_name = model or (_DEFAULT_VERTEX_FAST_MODEL if use_fast_model else _DEFAULT_VERTEX_MODEL)

        payload = self._build_request_payload(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            output_gcs_uri=output_gcs_uri,
        )

        self._log_request(
            correlation_id=correlation_id,
            model=model_name,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            has_image=False,
        )

        return self._submit_to_vertex(
            model_name=model_name,
            payload=payload,
            correlation_id=correlation_id,
        )

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
        self._ensure_configured()
        model_name = model or (_DEFAULT_VERTEX_FAST_MODEL if use_fast_model else _DEFAULT_VERTEX_MODEL)

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        payload = self._build_request_payload(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            output_gcs_uri=output_gcs_uri,
            image_base64=image_b64,
            image_mime_type=mime_type,
        )

        self._log_request(
            correlation_id=correlation_id,
            model=model_name,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            has_image=True,
            image_bytes_len=len(image_bytes),
            image_mime_type=mime_type,
        )

        return self._submit_to_vertex(
            model_name=model_name,
            payload=payload,
            correlation_id=correlation_id,
        )

    def submit_video_extension(
        self,
        *,
        prompt: str,
        video_uri: str,
        video_mime_type: Optional[str] = None,
        correlation_id: str,
        aspect_ratio: str,
        duration_seconds: int,
        output_gcs_uri: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_configured()
        model_name = model or _DEFAULT_VERTEX_MODEL

        payload = self._build_extension_request_payload(
            prompt=prompt,
            video_uri=video_uri,
            video_mime_type=video_mime_type,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            output_gcs_uri=output_gcs_uri,
        )

        self._log_request(
            correlation_id=correlation_id,
            model=model_name,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            has_image=False,
            source_video_uri=video_uri,
        )

        return self._submit_to_vertex(
            model_name=model_name,
            payload=payload,
            correlation_id=correlation_id,
        )

    def check_operation_status(
        self,
        *,
        operation_id: str,
        correlation_id: str,
    ) -> Dict[str, Any]:
        self._ensure_configured()
        url = self._build_fetch_operation_url(operation_id)
        headers = self._build_headers(include_json=True)

        response = self._http_client.post(
            url,
            headers=headers,
            json={"operationName": operation_id},
        )
        response.raise_for_status()
        data = response.json()

        done = data.get("done", False)
        video_uri = self._extract_video_uri(data)

        if done:
            provider_error = data.get("error")
            if provider_error:
                logger.error(
                    "vertex_ai_operation_failed",
                    correlation_id=correlation_id,
                    operation_id=operation_id,
                    provider_error=provider_error,
                )
                return {
                    "operation_id": operation_id,
                    "done": True,
                    "status": "failed",
                    "video_uri": None,
                    "provider": "vertex_ai",
                    "error": provider_error,
                }

        return {
            "operation_id": operation_id,
            "done": done,
            "status": "completed" if done else "processing",
            "video_uri": video_uri,
            "provider": "vertex_ai",
        }

    def _submit_to_vertex(
        self,
        *,
        model_name: str,
        payload: Dict[str, Any],
        correlation_id: str,
    ) -> Dict[str, Any]:
        url = self._build_submit_url(model_name)
        headers = self._build_headers(include_json=True)

        logged_payload = self._payload_for_logging(payload)
        logger.info(
            "vertex_ai_rest_submit",
            correlation_id=correlation_id,
            url=url,
            request_payload=logged_payload,
        )

        response = self._http_client.post(url, headers=headers, json=payload)
        logger.info(
            "vertex_ai_rest_response",
            correlation_id=correlation_id,
            status_code=response.status_code,
            response_text=response.text[:500],
        )
        response.raise_for_status()
        data = response.json()

        operation_name = data.get("name")
        if not operation_name:
            raise ValidationError(
                "Vertex AI response missing operation name.",
                {"response": str(data)},
            )

        logger.info(
            "vertex_ai_video_submitted",
            correlation_id=correlation_id,
            operation_id=operation_name,
            model=model_name,
        )

        return {
            "operation_id": operation_name,
            "status": "submitted",
            "done": False,
            "provider": "vertex_ai",
            "provider_model": model_name,
        }

    def _build_request_payload(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        duration_seconds: int,
        output_gcs_uri: Optional[str] = None,
        image_base64: Optional[str] = None,
        image_mime_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        instance: Dict[str, Any] = {"prompt": prompt}
        if image_base64 and image_mime_type:
            instance["image"] = {
                "bytesBase64Encoded": image_base64,
                "mimeType": image_mime_type,
            }

        parameters: Dict[str, Any] = {
            "aspectRatio": aspect_ratio,
            "durationSeconds": duration_seconds,
        }
        if output_gcs_uri:
            parameters["storageUri"] = output_gcs_uri

        return {
            "instances": [instance],
            "parameters": parameters,
        }

    def _build_extension_request_payload(
        self,
        *,
        prompt: str,
        video_uri: str,
        video_mime_type: Optional[str] = None,
        aspect_ratio: str,
        duration_seconds: int,
        output_gcs_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        video_payload: Dict[str, Any] = {"gcsUri": video_uri}
        if video_mime_type:
            video_payload["mimeType"] = video_mime_type

        instance: Dict[str, Any] = {
            "prompt": prompt,
            "video": video_payload,
        }

        parameters: Dict[str, Any] = {
            "aspectRatio": aspect_ratio,
            "durationSeconds": duration_seconds,
        }
        if output_gcs_uri:
            parameters["storageUri"] = output_gcs_uri

        return {
            "instances": [instance],
            "parameters": parameters,
        }

    def _build_submit_url(self, model_name: str) -> str:
        project = self._settings.vertex_ai_project_id
        location = self._settings.vertex_ai_location
        return (
            f"https://{location}-aiplatform.googleapis.com/v1"
            f"/projects/{project}/locations/{location}"
            f"/publishers/google/models/{model_name}:predictLongRunning"
        )

    def _build_fetch_operation_url(self, operation_id: str) -> str:
        """Build the fetchPredictOperation URL for polling.
        
        Extracts the model name from the operation ID and uses the v1beta1
        fetchPredictOperation endpoint, which is the only way to poll
        Veo UUID-style operations.
        """
        location = self._settings.vertex_ai_location
        project = self._settings.vertex_ai_project_id
        
        # Extract model name from operation_id
        # Format: projects/{project}/locations/{location}/publishers/google/models/{model}/operations/{uuid}
        if "/models/" in operation_id:
            model_part = operation_id.split("/models/")[1].split("/operations/")[0]
        else:
            model_part = _DEFAULT_VERTEX_MODEL
        
        return (
            f"https://{location}-aiplatform.googleapis.com/v1beta1"
            f"/projects/{project}/locations/{location}"
            f"/publishers/google/models/{model_part}:fetchPredictOperation"
        )

    def _get_credentials(self):
        if self._credentials is None:
            adc_path = resolve_google_application_credentials_path(self._settings)
            if adc_path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = adc_path
            try:
                self._credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                    quota_project_id=self._settings.vertex_ai_project_id or None,
                )
            except google.auth.exceptions.DefaultCredentialsError as exc:
                raise ValidationError(
                    "No Google Cloud Application Default Credentials found. "
                    "Run `gcloud auth application-default login` or set GOOGLE_APPLICATION_CREDENTIALS.",
                    {"error": str(exc)},
                ) from exc
            quota_project_id = self._settings.vertex_ai_project_id or None
            if quota_project_id and hasattr(self._credentials, "with_quota_project"):
                self._credentials = self._credentials.with_quota_project(quota_project_id)
        if self._credentials.expired or not self._credentials.token:
            self._credentials.refresh(Request())
        return self._credentials

    def _build_headers(self, include_json: bool = False) -> Dict[str, str]:
        creds = self._get_credentials()
        headers = {
            "Authorization": f"Bearer {creds.token}",
        }
        quota_project_id = getattr(creds, "quota_project_id", None) or self._settings.vertex_ai_project_id
        if quota_project_id:
            headers["x-goog-user-project"] = quota_project_id
        if include_json:
            headers["Content-Type"] = "application/json"
        return headers

    def _payload_for_logging(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        safe_payload = deepcopy(payload)
        for instance in safe_payload.get("instances", []) or []:
            if not isinstance(instance, dict):
                continue
            image_payload = instance.get("image") or {}
            if not isinstance(image_payload, dict):
                continue
            inline_data = image_payload.get("bytesBase64Encoded")
            if inline_data:
                instance["image"]["bytesBase64Encoded"] = f"<redacted_base64:{len(str(inline_data))}_chars>"
        return safe_payload

    def _extract_video_uri(self, data: Dict[str, Any]) -> Optional[str]:
        """Extract video URI from fetchPredictOperation response."""
        response_obj = data.get("response", {})
        if isinstance(response_obj, dict):
            # Try generatedSamples (v1 format)
            generated_samples = response_obj.get("generatedSamples", [])
            if generated_samples and isinstance(generated_samples, list):
                first_sample = generated_samples[0]
                if isinstance(first_sample, dict):
                    video = first_sample.get("video", {})
                    if isinstance(video, dict):
                        uri = video.get("uri")
                        if uri:
                            return uri
                        gcs_uri = video.get("gcsUri")
                        if gcs_uri:
                            return gcs_uri
                        b64 = video.get("bytesBase64Encoded")
                        if b64:
                            return f"data:video/mp4;base64,{b64}"
            
            # Try videos (SDK format)
            videos = response_obj.get("videos", [])
            if videos and isinstance(videos, list):
                first_video = videos[0]
                if isinstance(first_video, dict):
                    uri = first_video.get("uri")
                    if uri:
                        return uri
                    gcs_uri = first_video.get("gcsUri")
                    if gcs_uri:
                        return gcs_uri
                    b64 = first_video.get("bytesBase64Encoded")
                    if b64:
                        return f"data:video/mp4;base64,{b64}"
        return None

    def _log_request(
        self,
        *,
        correlation_id: str,
        model: str,
        prompt: str,
        aspect_ratio: str,
        duration_seconds: int,
        has_image: bool,
        image_bytes_len: Optional[int] = None,
        image_mime_type: Optional[str] = None,
        source_video_uri: Optional[str] = None,
    ) -> None:
        logger.info(
            "vertex_ai_video_submission",
            correlation_id=correlation_id,
            model=model,
            prompt_length=len(prompt),
            prompt_preview=prompt[:400],
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            has_image=has_image,
            image_bytes_len=image_bytes_len,
            image_mime_type=image_mime_type,
            source_video_uri=source_video_uri,
        )

    def _load_vertex_settings(self) -> VertexSettings:
        try:
            return VertexSettings()
        except Exception as exc:
            raise ValidationError(
                "Vertex AI settings could not be loaded from the shared environment file.",
                {"error": str(exc)},
            ) from exc

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


def get_vertex_ai_client() -> VertexAIClient:
    """Get Vertex AI client singleton."""
    return VertexAIClient()
