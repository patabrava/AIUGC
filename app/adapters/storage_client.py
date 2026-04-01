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
from botocore.exceptions import ClientError

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
        self.image_prefix = _strip_slashes(getattr(settings, "cloudflare_r2_image_prefix", "flow-forge/images"))

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

    def upload_image(
        self,
        *,
        image_bytes: bytes,
        file_name: str,
        correlation_id: Optional[str] = None,
        content_type: str = "image/png",
    ) -> Dict[str, Any]:
        """Upload raw image bytes to Cloudflare R2."""
        object_key = self._build_object_key(file_name, prefix=self.image_prefix)

        try:
            logger.info(
                "storage_image_upload_starting",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                file_name=file_name,
                object_key=object_key,
                size_bytes=len(image_bytes),
            )

            self.client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=image_bytes,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )

            result = {
                "storage_provider": "cloudflare_r2",
                "storage_key": object_key,
                "url": self._build_public_url(object_key),
                "thumbnail_url": None,
                "file_path": object_key,
                "size": len(image_bytes),
                "file_type": content_type,
            }

            logger.info(
                "storage_image_uploaded",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                object_key=object_key,
                url=result["url"],
                size_bytes=len(image_bytes),
            )
            return result

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "storage_image_upload_failed",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                file_name=file_name,
                object_key=object_key,
                size_bytes=len(image_bytes),
                error=str(exc),
            )
            raise

    def ensure_image(
        self,
        *,
        image_bytes: bytes,
        object_key: str,
        correlation_id: Optional[str] = None,
        content_type: str = "image/png",
    ) -> Dict[str, Any]:
        """Ensure an image exists at a fixed Cloudflare R2 object key."""
        normalized_key = _strip_slashes(object_key)

        try:
            self.client.head_object(Bucket=self.bucket_name, Key=normalized_key)
            logger.info(
                "storage_image_already_present",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                object_key=normalized_key,
            )
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code") or "")
            if error_code not in {"404", "NoSuchKey", "NotFound"}:
                logger.exception(
                    "storage_image_head_failed",
                    correlation_id=correlation_id,
                    storage_provider="cloudflare_r2",
                    object_key=normalized_key,
                    error=str(exc),
                )
                raise

            logger.info(
                "storage_fixed_image_upload_starting",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                object_key=normalized_key,
                size_bytes=len(image_bytes),
            )
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=normalized_key,
                Body=image_bytes,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )
            logger.info(
                "storage_fixed_image_uploaded",
                correlation_id=correlation_id,
                storage_provider="cloudflare_r2",
                object_key=normalized_key,
                size_bytes=len(image_bytes),
            )

        return {
            "storage_provider": "cloudflare_r2",
            "storage_key": normalized_key,
            "url": self._build_public_url(normalized_key),
            "thumbnail_url": None,
            "file_path": normalized_key,
            "size": len(image_bytes),
            "file_type": content_type,
        }

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

    def download_video(self, *, video_url: str, correlation_id: str) -> bytes:
        """Download video bytes from a URL (R2 CDN or presigned)."""
        logger.info(
            "storage_download_start",
            correlation_id=correlation_id,
            url=video_url[:80],
        )
        response = self._http_client.get(video_url)
        response.raise_for_status()
        logger.info(
            "storage_download_done",
            correlation_id=correlation_id,
            size=len(response.content),
        )
        return response.content

    def _build_object_key(self, file_name: str, *, prefix: Optional[str] = None) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", file_name).strip("-") or "video.mp4"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        object_prefix = _strip_slashes(prefix or self.object_prefix)
        key_prefix = f"{object_prefix}/" if object_prefix else ""
        return f"{key_prefix}{timestamp}_{safe_name}"

    def _build_public_url(self, object_key: str) -> str:
        return f"{self.public_base_url}/{object_key}"


def get_storage_client() -> StorageClient:
    """Return singleton Cloudflare R2 storage client."""
    return StorageClient()
