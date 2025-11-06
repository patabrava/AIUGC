"""
Video Generation Polling Worker
Runs on Railway, polls VEO/Sora operations and updates Supabase.
Per Constitution § III: Deterministic Execution
Per Constitution § IX: Observable Implementation
"""

import time
import sys
import os
from typing import List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.adapters.supabase_client import get_supabase
from app.adapters.veo_client import get_veo_client
from app.adapters.imagekit_client import get_imagekit_client
from app.core.logging import configure_logging, get_logger

# Configure logging
configure_logging()
logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 10
MAX_RETRIES = 3


def poll_pending_videos():
    """
    Poll all posts with submitted/processing video status.
    Per Constitution § VIII: Test end-to-end in real environment.
    """
    try:
        supabase = get_supabase().client
        
        # Fetch posts awaiting video completion
        response = supabase.table("posts").select("*").in_(
            "video_status", ["submitted", "processing"]
        ).execute()
        
        posts = response.data
        logger.info("polling_videos", count=len(posts))
        
        for post in posts:
            process_video_operation(post)
    
    except Exception as e:
        logger.exception("poll_cycle_failed", error=str(e))


def process_video_operation(post: Dict[str, Any]):
    """
    Process single video operation.
    Per Constitution § X: Hypothesis-driven debugging with structured evidence.
    """
    post_id = post["id"]
    operation_id = post.get("video_operation_id")
    provider = post.get("video_provider")
    correlation_id = f"poll_{post_id}"
    
    if not operation_id or not provider:
        logger.warning(
            "missing_operation_data",
            post_id=post_id,
            has_operation_id=bool(operation_id),
            has_provider=bool(provider)
        )
        return
    
    try:
        if provider == "veo_3_1":
            veo_client = get_veo_client()
            status_result = veo_client.check_operation_status(
                operation_id=operation_id,
                correlation_id=correlation_id
            )
            
            if status_result["done"]:
                video_data = status_result.get("video_data")

                if not video_data or not video_data.get("video_uri"):
                    logger.error(
                        "video_data_missing_uri",
                        post_id=post_id,
                        correlation_id=correlation_id,
                        operation_id=operation_id
                    )
                    raise ValueError("Video data missing download URI")

                video_bytes = veo_client.download_video(
                    video_uri=video_data["video_uri"],
                    correlation_id=correlation_id
                )
                
                # Upload to ImageKit
                imagekit_client = get_imagekit_client()
                upload_result = imagekit_client.upload_video(
                    video_bytes=video_bytes,
                    file_name=f"post_{post_id}.mp4",
                    correlation_id=correlation_id
                )
                
                # Update post with completed status
                supabase = get_supabase().client
                supabase.table("posts").update({
                    "video_status": "completed",
                    "video_url": upload_result["url"],
                    "video_metadata": {
                        "imagekit_file_id": upload_result["file_id"],
                        "size_bytes": upload_result["size"],
                        "provider": provider,
                        "file_path": upload_result["file_path"],
                        "thumbnail_url": upload_result.get("thumbnail_url"),
                        "provider_metadata": video_data
                    }
                }).eq("id", post_id).execute()
                
                logger.info(
                    "video_completed",
                    post_id=post_id,
                    correlation_id=correlation_id,
                    video_url=upload_result["url"],
                    size_bytes=upload_result["size"]
                )
            else:
                # Still processing, update status
                supabase = get_supabase().client
                supabase.table("posts").update({
                    "video_status": "processing"
                }).eq("id", post_id).execute()
                
                logger.debug(
                    "video_still_processing",
                    post_id=post_id,
                    correlation_id=correlation_id,
                    operation_id=operation_id
                )
        
        else:
            logger.warning(
                "unsupported_provider",
                post_id=post_id,
                provider=provider
            )
    
    except Exception as e:
        logger.exception(
            "video_processing_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            error=str(e)
        )
        
        # Mark as failed after exception
        try:
            supabase = get_supabase().client
            supabase.table("posts").update({
                "video_status": "failed",
                "video_metadata": {
                    "error": str(e),
                    "provider": provider,
                    "operation_id": operation_id
                }
            }).eq("id", post_id).execute()
            
            logger.error(
                "video_marked_failed",
                post_id=post_id,
                correlation_id=correlation_id
            )
        except Exception as update_error:
            logger.exception(
                "failed_to_mark_video_failed",
                post_id=post_id,
                error=str(update_error)
            )


if __name__ == "__main__":
    logger.info("video_poller_started", poll_interval_seconds=POLL_INTERVAL_SECONDS)
    
    while True:
        try:
            poll_pending_videos()
        except KeyboardInterrupt:
            logger.info("video_poller_stopped_by_user")
            break
        except Exception as e:
            logger.exception("video_poller_unexpected_error", error=str(e))
        
        time.sleep(POLL_INTERVAL_SECONDS)
