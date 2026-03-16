"""
Cloudflare R2 storage adapter for generated video assets.
Per Constitution § VI: Adapterize specialists.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _strip_slashes(value: str) -> str:
    return value.strip().strip("/")


class StorageClient:
    """Singleton storage client backed by Cloudflare R2."""

    _instance: Optional["StorageClient"] = None

    def __new__(cls) -> "StorageClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        settings = get_settings()
        self.bucket_name = settings.cloudflare_r2_bucket_name
        self.public_base_url = settings.cloudflare_r2_public_base_url.rstrip("/")
        self.object_prefix = _strip_slashes(settings.cloudflare_r2_video_prefix)

        endpoint_url = settings.cloudflare_r2_endpoint_url or (
            f"https://{settings.cloudflare_r2_account_id}.r2.cloudflarestorage.com"
        )

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=settings.cloudflare_r2_region,
            aws_access_key_id=settings.cloudflare_r2_access_key_id,
            aws_secret_access_key=settings.cloudflare_r2_secret_access_key,
        )
        self._http_client = httpx.Client(timeout=60.0, follow_redirects=True)
        self._initialized = True

        logger.info(
            "storage_client_initialized",
            storage_provider="cloudflare_r2",
            bucket_name=self.bucket_name,
            public_base_url=self.public_base_url,
            object_prefix=self.object_prefix,
        )

    def upload_video(
        self,
        *,
        video_bytes: bytes,
        file_name: str,
        correlation_id: Optional[str] = None,
        content_type: str = "video/mp4",
    ) -> Dict[str, Any]:
        """Upload raw video bytes to Cloudflare R2."""
        object_key = self._build_object_key(file_name)

        try:
            logger.info(
                "storage_upload_starting",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                file_name=file_name,
                object_key=object_key,
                size_bytes=len(video_bytes),
            )

            self.client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=video_bytes,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )

            result = {
                "storage_provider": "cloudflare_r2",
                "storage_key": object_key,
                "url": self._build_public_url(object_key),
                "thumbnail_url": None,
                "file_path": object_key,
                "size": len(video_bytes),
                "file_type": content_type,
            }

            logger.info(
                "storage_video_uploaded",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                object_key=object_key,
                url=result["url"],
                size_bytes=len(video_bytes),
            )
            return result

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "storage_upload_failed",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                file_name=file_name,
                object_key=object_key,
                size_bytes=len(video_bytes),
                error=str(exc),
            )
            raise

    def upload_video_from_url(
        self,
        *,
        video_url: str,
        file_name: str,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download a public video URL and store the bytes in Cloudflare R2."""
        try:
            logger.info(
                "storage_url_ingest_starting",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                file_name=file_name,
                source_url=video_url[:200],
            )
            response = self._http_client.get(video_url)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "storage_url_ingest_failed",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                file_name=file_name,
                source_url=video_url[:200],
                error=str(exc),
            )
            raise

        content_type = response.headers.get("content-type", "video/mp4").split(";")[0].strip()
        return self.upload_video(
            video_bytes=response.content,
            file_name=file_name,
            correlation_id=correlation_id,
            content_type=content_type or "video/mp4",
        )

    def _build_object_key(self, file_name: str) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", file_name).strip("-") or "video.mp4"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prefix = f"{self.object_prefix}/" if self.object_prefix else ""
        return f"{prefix}{timestamp}_{safe_name}"

    def _build_public_url(self, object_key: str) -> str:
        return f"{self.public_base_url}/{object_key}"


def get_storage_client() -> StorageClient:
    """Return singleton Cloudflare R2 storage client."""
    return StorageClient()
