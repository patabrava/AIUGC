"""
VEO 3.1 Video Generation Adapter
Google AI VEO 3.1 API integration (REST fallback).
Per Constitution § VI: Adapterize specialists
Per Constitution § III: Deterministic Execution
"""

from typing import Optional, Dict, Any

import httpx
from google import genai

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class VeoClient:
    """
    Singleton adapter for Google VEO 3.1 API.
    Per Constitution § VI: Wrap LLM SDKs and video APIs in thin adapters.
    """
    
    _instance: Optional['VeoClient'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        settings = get_settings()
        self.client = genai.Client(api_key=settings.google_ai_api_key)
        self._api_key = settings.google_ai_api_key
        self._http_client = httpx.Client(timeout=30.0, follow_redirects=True)
        self._initialized = True
        logger.info("veo_client_initialized")
    
    def submit_video_generation(
        self,
        prompt: str,
        correlation_id: str,
        aspect_ratio: str,
        resolution: str,
        reference_images: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Submit video generation request to VEO 3.1.
        Returns operation ID for polling.
        
        Per Constitution § III: Idempotent operations with explicit correlation IDs.
        Per Constitution § IX: Structured logging with correlation IDs.
        
        Args:
            prompt: Text prompt for video generation
            correlation_id: Unique correlation ID for tracking
            aspect_ratio: Aspect ratio for video generation
            resolution: Resolution for video generation
            reference_images: Optional list of reference images
            
        Returns:
            Dict with operation_id, status, done flag
            
        Raises:
            Exception: If submission fails
        """
        try:
            # Note: veo-3.1-generate-preview REST API only accepts prompt
            # Config parameters (aspect_ratio, resolution, duration) are not supported in REST
            # They work in SDK clients but return 400 "config isn't supported" in REST
            payload: Dict[str, Any] = {
                "instances": [
                    {
                        "prompt": prompt
                    }
                ]
            }

            if reference_images:
                logger.warning(
                    "veo_reference_images_not_supported_rest",
                    correlation_id=correlation_id
                )

            logger.info(
                "veo_submission_request",
                correlation_id=correlation_id,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                prompt_length=len(prompt)
            )

            response = self._http_client.post(
                f"{_GEMINI_API_BASE}/models/veo-3.1-generate-preview:predictLongRunning",
                headers=self._build_headers(include_json=True),
                json=payload
            )
            logger.info(
                "veo_submission_raw_response",
                correlation_id=correlation_id,
                status_code=response.status_code,
                text=response.text
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "veo_submission_http_error",
                    correlation_id=correlation_id,
                    status_code=exc.response.status_code,
                    response_text=exc.response.text
                )
                raise

            try:
                data = response.json()
            except ValueError:
                logger.error(
                    "veo_submission_parse_error",
                    correlation_id=correlation_id,
                    response_text=response.text
                )
                raise
            operation_name = data.get("name")

            if not operation_name:
                raise ValueError("VEO submission response missing operation name")

            logger.info(
                "veo_video_submitted",
                correlation_id=correlation_id,
                operation_id=operation_name,
                prompt_length=len(prompt),
                has_reference_images=bool(reference_images)
            )

            return {
                "operation_id": operation_name,
                "status": "submitted",
                "done": False
            }

        except Exception as e:
            logger.exception(
                "veo_submission_failed",
                correlation_id=correlation_id,
                error=str(e)
            )
            raise
    
    def check_operation_status(
        self,
        operation_id: str,
        correlation_id: str
    ) -> Dict[str, Any]:
        """
        Poll VEO operation status.
        Returns status and video data if complete.
        
        Per Constitution § IX: Boundary logging with correlation IDs.
        
        Args:
            operation_id: VEO operation identifier
            correlation_id: Unique correlation ID for tracking
            
        Returns:
            Dict with operation_id, done flag, status, and optional video_data
            
        Raises:
            Exception: If status check fails
        """
        try:
            response = self._http_client.get(
                f"{_GEMINI_API_BASE}/{operation_id}",
                headers=self._build_headers()
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "veo_status_http_error",
                    correlation_id=correlation_id,
                    operation_id=operation_id,
                    status_code=exc.response.status_code,
                    response_text=exc.response.text
                )
                raise

            data = response.json()
            done = data.get("done", False)

            logger.info(
                "veo_status_raw_response",
                correlation_id=correlation_id,
                operation_id=operation_id,
                done=done,
                response_keys=list(data.keys()),
                has_response=bool(data.get("response"))
            )

            result = {
                "operation_id": operation_id,
                "done": done,
                "status": "completed" if done else "processing"
            }

            if done:
                video_samples = (
                    data.get("response", {})
                    .get("generateVideoResponse", {})
                    .get("generatedSamples", [])
                )

                logger.info(
                    "veo_status_video_samples",
                    correlation_id=correlation_id,
                    samples_count=len(video_samples) if video_samples else 0,
                    full_response=data
                )

                if video_samples:
                    sample = video_samples[0]
                    video_info = sample.get("video", {})
                    video_uri = video_info.get("uri")
                    
                    logger.info(
                        "veo_status_video_uri_extracted",
                        correlation_id=correlation_id,
                        video_uri=video_uri,
                        mime_type=video_info.get("mimeType")
                    )
                    
                    result["video_data"] = {
                        "video_uri": video_uri,
                        "mime_type": video_info.get("mimeType"),
                        "thumbnail_uri": sample.get("thumbnailUri")
                    }

            logger.info(
                "veo_status_checked",
                correlation_id=correlation_id,
                operation_id=operation_id,
                done=done
            )

            return result
        
        except Exception as e:
            logger.exception(
                "veo_status_check_failed",
                correlation_id=correlation_id,
                operation_id=operation_id,
                error=str(e)
            )
            raise
    
    def download_video(
        self,
        video_uri: str,
        correlation_id: str
    ) -> bytes:
        """
        Download generated video bytes from VEO.
        
        Per Constitution § IX: Structured logging.
        
        Args:
            video_uri: VEO video URI
            correlation_id: Unique correlation ID for tracking
            
        Returns:
            Video bytes
            
        Raises:
            Exception: If download fails
        """
        try:
            logger.info(
                "veo_download_starting",
                correlation_id=correlation_id,
                video_uri=video_uri
            )
            
            response = self._http_client.get(
                video_uri,
                headers=self._build_headers(),
                follow_redirects=True,
                timeout=60.0
            )
            
            logger.info(
                "veo_download_response_received",
                correlation_id=correlation_id,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                content_length=response.headers.get("content-length"),
                response_headers=dict(response.headers)
            )
            
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "veo_download_http_error",
                    correlation_id=correlation_id,
                    status_code=exc.response.status_code,
                    response_text=exc.response.text[:500]
                )
                raise
            
            video_bytes = response.content

            logger.info(
                "veo_video_downloaded",
                correlation_id=correlation_id,
                size_bytes=len(video_bytes),
                first_bytes=video_bytes[:100].hex() if len(video_bytes) >= 100 else video_bytes.hex()
            )
            
            return video_bytes
        
        except Exception as e:
            logger.exception(
                "veo_download_failed",
                correlation_id=correlation_id,
                error=str(e)
            )
            raise

    def _build_headers(self, include_json: bool = False) -> Dict[str, str]:
        headers = {
            "x-goog-api-key": self._api_key
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        return headers


def get_veo_client() -> VeoClient:
    """
    Get VEO client singleton.
    Per Constitution § VI: Use dependency injection or explicit factories.
    """
    return VeoClient()
