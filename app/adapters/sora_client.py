"""
SORA 2 Video Generation Adapter
OpenAI Sora 2 / Sora 2 Pro API integration.
Per Constitution § VI: Adapterize specialists.
Per Constitution § IX: Observable implementation with structured logging.
"""

from typing import Optional, Dict, Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SORA_API_BASE = "https://api.openai.com/v1"


class SoraClient:
    """
    Singleton adapter for OpenAI Sora video generation APIs.
    Handles submission, polling, and downloading of rendered assets.
    """

    _instance: Optional["SoraClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        settings = get_settings()
        self._api_key = settings.openai_api_key
        self._http_client = httpx.Client(
            base_url=_SORA_API_BASE,
            timeout=httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=None),
            follow_redirects=True,
        )
        self._initialized = True
        logger.info("sora_client_initialized")

    def submit_video_generation(
        self,
        *,
        prompt: str,
        correlation_id: str,
        model: str,
        seconds: str,
        size: Optional[str] = None,
        input_reference: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit a Sora video generation job.

        Args:
            prompt: The creative prompt describing the video.
            correlation_id: Correlation identifier for structured logs.
            model: Target Sora model identifier (e.g., sora-2, sora-2-pro).
            seconds: Desired duration in seconds.
            size: Optional resolution string (e.g., "1080x1920").
            input_reference: Optional image reference for the first frame.

        Returns:
            Dict containing video_id and provider metadata.
        """
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "seconds": seconds,
        }

        if size:
            payload["size"] = size
        if input_reference:
            payload["input_reference"] = input_reference

        logger.info(
            "sora_submission_request",
            correlation_id=correlation_id,
            model=model,
            seconds=seconds,
            size=size,
            prompt_length=len(prompt),
            prompt_preview=prompt[:400],
        )

        try:
            response = self._http_client.post(
                "/videos",
                headers=self._build_headers(include_json=True),
                json=payload,
            )
            logger.info(
                "sora_submission_raw_response",
                correlation_id=correlation_id,
                status_code=response.status_code,
                text=response.text[:500],
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "sora_submission_http_error",
                correlation_id=correlation_id,
                status_code=exc.response.status_code,
                response_text=exc.response.text,
                payload=payload,
            )
            raise
        except Exception:
            logger.exception(
                "sora_submission_failed",
                correlation_id=correlation_id,
                payload=payload,
            )
            raise

        video_id = data.get("id")
        if not video_id:
            raise ValueError("Sora submission response missing video id")

        logger.info(
            "sora_video_submitted",
            correlation_id=correlation_id,
            video_id=video_id,
            status=data.get("status"),
            progress=data.get("progress"),
        )

        return {
            "video_id": video_id,
            "status": data.get("status", "queued"),
            "model": data.get("model", model),
            "seconds": data.get("seconds", seconds),
            "size": data.get("size", size),
        }

    def check_video_status(self, *, video_id: str, correlation_id: str) -> Dict[str, Any]:
        """Poll the status of a Sora video generation job."""
        try:
            response = self._http_client.get(
                f"/videos/{video_id}",
                headers=self._build_headers(),
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "sora_status_http_error",
                correlation_id=correlation_id,
                video_id=video_id,
                status_code=exc.response.status_code,
                response_text=exc.response.text,
            )
            raise
        except Exception:
            logger.exception(
                "sora_status_check_failed",
                correlation_id=correlation_id,
                video_id=video_id,
            )
            raise

        status = data.get("status", "queued")
        progress = data.get("progress")

        logger.info(
            "sora_status_checked",
            correlation_id=correlation_id,
            video_id=video_id,
            status=status,
            progress=progress,
        )

        result: Dict[str, Any] = {
            "video_id": video_id,
            "status": status,
            "progress": progress,
            "model": data.get("model"),
            "seconds": data.get("seconds"),
            "size": data.get("size"),
            "error": data.get("error"),
        }

        return result

    def download_video(
        self,
        *,
        video_id: str,
        correlation_id: str,
        variant: str = "video",
    ) -> bytes:
        """
        Download rendered video bytes for a completed Sora job.

        Args:
            video_id: Sora video identifier.
            correlation_id: Correlation identifier for logging.
            variant: Which asset to download (video, thumbnail, spritesheet).
        """
        params = {"variant": variant} if variant else None

        try:
            response = self._http_client.get(
                f"/videos/{video_id}/content",
                headers=self._build_headers(binary=True),
                params=params,
            )
            logger.info(
                "sora_download_response",
                correlation_id=correlation_id,
                video_id=video_id,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                content_length=response.headers.get("content-length"),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "sora_download_http_error",
                correlation_id=correlation_id,
                video_id=video_id,
                status_code=exc.response.status_code,
                response_text=exc.response.text[:500],
            )
            raise
        except Exception:
            logger.exception(
                "sora_download_failed",
                correlation_id=correlation_id,
                video_id=video_id,
            )
            raise

        content = response.content
        logger.info(
            "sora_video_downloaded",
            correlation_id=correlation_id,
            video_id=video_id,
            size_bytes=len(content),
        )
        return content

    def _build_headers(self, include_json: bool = False, binary: bool = False) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        if binary:
            headers["Accept"] = "application/octet-stream"
        return headers


def get_sora_client() -> SoraClient:
    """Return singleton Sora client."""
    return SoraClient()
