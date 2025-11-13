"""
Video Generation Polling Worker
Runs on Railway, polls VEO/Sora operations and updates Supabase.
Per Constitution § III: Deterministic Execution
Per Constitution § IX: Observable Implementation
"""

import time
import sys
import os
from typing import List, Dict, Any, Union

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.adapters.supabase_client import get_supabase
from app.adapters.sora_client import get_sora_client
from app.adapters.imagekit_client import get_imagekit_client
from app.core.logging import configure_logging, get_logger
from app.core.config import get_settings

try:  # pragma: no cover - allow worker to run without google-genai on Python 3.9
    from app.adapters.veo_client import get_veo_client  # type: ignore
    _veo_available = True
    _veo_import_error = None
except Exception as import_error:  # noqa: BLE001
    get_veo_client = None  # type: ignore
    _veo_available = False
    _veo_import_error = import_error

# Configure logging
configure_logging()
logger = get_logger(__name__)

if not _veo_available:
    logger.warning(
        "veo_client_unavailable",
        error=str(_veo_import_error),
        message="VEO polling disabled; continuing with available providers"
    )

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
            if not _veo_available or get_veo_client is None:
                logger.warning(
                    "veo_poll_skipped",
                    post_id=post_id,
                    provider=provider,
                    reason="VEO client unavailable on this runtime"
                )
                return

            _handle_veo_video(post, operation_id, correlation_id)
        elif provider in {"sora_2", "sora_2_pro"}:
            _handle_sora_video(post, operation_id, correlation_id)
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


def _handle_veo_video(post: Dict[str, Any], operation_id: str, correlation_id: str) -> None:
    post_id = post["id"]
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

        video_uri = video_data["video_uri"]

        settings = get_settings()

        if settings.use_url_based_upload:
            try:
                logger.info(
                    "attempting_url_based_upload",
                    post_id=post_id,
                    correlation_id=correlation_id,
                    video_uri_preview=video_uri[:100]
                )

                download_url = veo_client.get_video_download_url(
                    video_uri=video_uri,
                    correlation_id=correlation_id
                )

                _store_completed_video(
                    post_id=post_id,
                    provider="veo_3_1",
                    video_source=download_url,
                    correlation_id=correlation_id,
                    provider_metadata=video_data,
                    existing_metadata=post.get("video_metadata") or {}
                )

                return

            except Exception as url_upload_error:
                logger.warning(
                    "url_upload_failed_using_bytes_fallback",
                    post_id=post_id,
                    correlation_id=correlation_id,
                    error=str(url_upload_error)
                )

        video_bytes = veo_client.download_video(
            video_uri=video_uri,
            correlation_id=correlation_id
        )

        _store_completed_video(
            post_id=post_id,
            provider="veo_3_1",
            video_source=video_bytes,
            correlation_id=correlation_id,
            provider_metadata=video_data,
            existing_metadata=post.get("video_metadata") or {}
        )
    else:
        _mark_processing(post_id, correlation_id, operation_id)


def _handle_sora_video(post: Dict[str, Any], operation_id: str, correlation_id: str) -> None:
    post_id = post["id"]
    provider = post.get("video_provider", "sora_2")
    sora_client = get_sora_client()

    status_result = sora_client.check_video_status(
        video_id=operation_id,
        correlation_id=correlation_id
    )

    status = status_result.get("status", "queued")
    progress = status_result.get("progress")

    logger.debug(
        "sora_status_polled",
        post_id=post_id,
        correlation_id=correlation_id,
        status=status,
        progress=progress
    )

    if status == "completed":
        video_bytes = sora_client.download_video(
            video_id=operation_id,
            correlation_id=correlation_id,
        )

        _store_completed_video(
            post_id=post_id,
            provider=provider,
            video_source=video_bytes,
            correlation_id=correlation_id,
            provider_metadata=status_result,
            existing_metadata=post.get("video_metadata") or {}
        )
    elif status in {"failed", "cancelled"}:
        raise ValueError(f"Sora video failed with status {status}")
    else:
        new_status = "processing" if status in {"in_progress", "processing"} else "submitted"
        supabase = get_supabase().client
        supabase.table("posts").update({
            "video_status": new_status,
            "video_metadata": {
                **(post.get("video_metadata") or {}),
                "provider": provider,
                "progress": progress,
                "provider_status": status,
            }
        }).eq("id", post_id).execute()


def _store_completed_video(
    *,
    post_id: str,
    provider: str,
    video_source: Union[bytes, str],
    correlation_id: str,
    provider_metadata: Dict[str, Any],
    existing_metadata: Dict[str, Any],
) -> None:
    imagekit_client = get_imagekit_client()

    upload_method = "url" if isinstance(video_source, str) else "bytes"
    upload_start = time.monotonic()

    if upload_method == "url":
        upload_result = imagekit_client.upload_video_from_url(
            video_url=video_source,
            file_name=f"post_{post_id}.mp4",
            correlation_id=correlation_id
        )
    else:
        upload_result = imagekit_client.upload_video(
            video_bytes=video_source,
            file_name=f"post_{post_id}.mp4",
            correlation_id=correlation_id
        )

    upload_duration = time.monotonic() - upload_start

    size_bytes = upload_result.get("size")
    if size_bytes is None and isinstance(video_source, bytes):
        size_bytes = len(video_source)

    logger.info(
        "video_upload_performance",
        post_id=post_id,
        correlation_id=correlation_id,
        provider=provider,
        upload_method=upload_method,
        upload_duration_seconds=upload_duration,
        video_size_bytes=size_bytes
    )

    supabase = get_supabase().client
    merged_metadata = {
        **existing_metadata,
        "imagekit_file_id": upload_result["file_id"],
        "size_bytes": size_bytes,
        "provider": provider,
        "file_path": upload_result["file_path"],
        "thumbnail_url": upload_result.get("thumbnail_url"),
        "provider_metadata": provider_metadata,
        "upload_method": upload_method,
    }

    supabase.table("posts").update({
        "video_status": "completed",
        "video_url": upload_result["url"],
        "video_metadata": merged_metadata,
    }).eq("id", post_id).execute()

    logger.info(
        "video_completed",
        post_id=post_id,
        correlation_id=correlation_id,
        provider=provider,
        video_url=upload_result["url"],
        size_bytes=size_bytes,
        upload_method=upload_method,
        upload_duration_seconds=upload_duration
    )
    
    # Check if all videos in batch are complete and transition to S6_QA
    _check_and_transition_batch_to_qa(post_id, correlation_id)


def _mark_processing(post_id: str, correlation_id: str, operation_id: str) -> None:
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


def _check_and_transition_batch_to_qa(post_id: str, correlation_id: str) -> None:
    """
    Check if all videos in batch are complete and transition to S6_QA.
    Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA transition.
    Per Constitution § VII: State Machine Discipline with explicit guards.
    """
    try:
        supabase = get_supabase().client
        
        # Get batch_id from post
        post_response = supabase.table("posts").select("batch_id").eq("id", post_id).execute()
        if not post_response.data:
            logger.warning("post_not_found_for_qa_check", post_id=post_id)
            return
        
        batch_id = post_response.data[0]["batch_id"]
        
        # Get batch state
        batch_response = supabase.table("batches").select("state").eq("id", batch_id).execute()
        if not batch_response.data:
            logger.warning("batch_not_found_for_qa_check", batch_id=batch_id)
            return
        
        current_state = batch_response.data[0]["state"]
        
        # Only transition from S5_PROMPTS_BUILT
        if current_state != "S5_PROMPTS_BUILT":
            logger.debug(
                "batch_not_in_prompts_built_state",
                batch_id=batch_id,
                current_state=current_state,
                message="Skipping S6_QA transition check"
            )
            return
        
        # Get all posts in batch
        posts_response = supabase.table("posts").select("id, video_status").eq("batch_id", batch_id).execute()
        posts = posts_response.data
        
        if not posts:
            logger.warning("no_posts_in_batch", batch_id=batch_id)
            return
        
        # Check if all videos are completed
        total_posts = len(posts)
        completed_videos = sum(1 for p in posts if p.get("video_status") == "completed")
        
        logger.debug(
            "batch_qa_transition_check",
            batch_id=batch_id,
            correlation_id=correlation_id,
            total_posts=total_posts,
            completed_videos=completed_videos
        )
        
        if completed_videos == total_posts:
            # All videos complete - transition to S6_QA
            supabase.table("batches").update({
                "state": "S6_QA"
            }).eq("id", batch_id).execute()
            
            logger.info(
                "batch_transitioned_to_qa",
                batch_id=batch_id,
                correlation_id=correlation_id,
                previous_state="S5_PROMPTS_BUILT",
                new_state="S6_QA",
                total_posts=total_posts,
                message="All videos completed - batch ready for QA review"
            )
        else:
            logger.debug(
                "batch_qa_transition_pending",
                batch_id=batch_id,
                correlation_id=correlation_id,
                completed=completed_videos,
                total=total_posts,
                remaining=total_posts - completed_videos
            )
    
    except Exception as e:
        logger.exception(
            "batch_qa_transition_check_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e)
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
