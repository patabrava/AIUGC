"""
ImageKit Upload Adapter
ImageKit CDN integration for video storage.
Per Constitution § VI: Adapterize specialists
Per Constitution § XIV: Security & Compliance
"""

from typing import Optional, Dict, Any
import base64
from imagekitio import ImageKit
from imagekitio.models.UploadFileRequestOptions import UploadFileRequestOptions

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ImageKitClient:
    """
    Singleton adapter for ImageKit API.
    Per Constitution § VI: Wrap external services in thin adapters.
    """
    
    _instance: Optional['ImageKitClient'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        settings = get_settings()
        self.client = ImageKit(
            private_key=settings.imagekit_private_key,
            public_key=settings.imagekit_public_key,
            url_endpoint=settings.imagekit_url_endpoint
        )
        self._initialized = True
        logger.info("imagekit_client_initialized")
    
    def upload_video(
        self,
        video_bytes: bytes,
        file_name: str,
        folder: str = "/flow-forge/videos",
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload video to ImageKit CDN.
        Returns URL and metadata.
        
        Per Constitution § IX: Structured logging with correlation IDs.
        Per Constitution § XIV: No secret leakage in logs.
        
        Args:
            video_bytes: Video file bytes
            file_name: Name for the uploaded file
            folder: ImageKit folder path (default: /flow-forge/videos)
            correlation_id: Optional correlation ID for tracking
            
        Returns:
            Dict with file_id, url, thumbnail_url, file_path, size, file_type
            
        Raises:
            Exception: If upload fails
        """
        try:
            # Per ImageKit docs: file parameter accepts binary, base64, or url
            # Use base64 encoding for reliable video upload
            video_base64 = base64.b64encode(video_bytes).decode('utf-8')
            
            logger.info(
                "imagekit_upload_starting",
                correlation_id=correlation_id,
                file_name=file_name,
                size_bytes=len(video_bytes),
                base64_length=len(video_base64)
            )
            
            options = UploadFileRequestOptions(
                folder=folder,
                use_unique_file_name=True,
                tags=["flow-forge", "ugc-video"]
            )
            
            result = self.client.upload_file(
                file=video_base64,
                file_name=file_name,
                options=options
            )
            
            logger.info(
                "imagekit_video_uploaded",
                correlation_id=correlation_id,
                file_id=result.file_id,
                url=result.url,
                size_bytes=len(video_bytes),
                folder=folder
            )
            
            return {
                "file_id": result.file_id,
                "url": result.url,
                "thumbnail_url": result.thumbnail_url,
                "file_path": result.file_path,
                "size": len(video_bytes),  # Use actual bytes length, not ImageKit's metadata size
                "file_type": result.file_type
            }
        
        except Exception as e:
            logger.exception(
                "imagekit_upload_failed",
                correlation_id=correlation_id,
                file_name=file_name,
                size_bytes=len(video_bytes),
                error=str(e)
            )
            raise


def get_imagekit_client() -> ImageKitClient:
    """
    Get ImageKit client singleton.
    Per Constitution § VI: Use dependency injection or explicit factories.
    """
    return ImageKitClient()
