"""Caption worker — polls for caption_pending posts and burns captions."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

# Keep script execution import-safe (python workers/caption_worker.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.adapters.deepgram_client import DeepgramError, get_deepgram_client
from app.adapters.caption_renderer import burn_captions, CaptionRendererError
from app.adapters.caption_aligner import align_transcript_to_script
from app.adapters.storage_client import get_storage_client
from app.adapters.supabase_client import get_supabase
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.features.batches.state_machine import reconcile_batch_video_pipeline_state
from app.core.video_profiles import (
    VIDEO_STATUS_CAPTION_PENDING,
    VIDEO_STATUS_CAPTION_PROCESSING,
    VIDEO_STATUS_CAPTION_COMPLETED,
    VIDEO_STATUS_CAPTION_FAILED,
    get_caption_pollable_statuses,
)

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 10
CAPTION_IDLE_BACKOFF_SECONDS = int(os.getenv("CAPTION_IDLE_BACKOFF_SECONDS", "45"))
MAX_CAPTION_RETRIES = 3
CAPTION_BATCH_LIMIT = 5
CAPTION_POLL_SELECT_FIELDS = "id,batch_id,video_url,video_metadata,seed_data"


def _caption_worker_sleep_seconds(rows_seen_count: int) -> int:
    return CAPTION_IDLE_BACKOFF_SECONDS if rows_seen_count == 0 else POLL_INTERVAL_SECONDS


def poll_caption_pending() -> int:
    supabase = get_supabase().client
    statuses = list(get_caption_pollable_statuses())
    result = (
        supabase.table("posts")
        .select(CAPTION_POLL_SELECT_FIELDS)
        .in_("video_status", statuses)
        .limit(CAPTION_BATCH_LIMIT)
        .execute()
    )
    posts = result.data or []
    if not posts:
        return 0
    logger.info("caption_poll_found", count=len(posts))
    rows_seen_count = 0
    for post in posts:
        rows_seen_count += 1
        try:
            _process_caption_post(post)
        except Exception:
            logger.exception("caption_post_error", post_id=post.get("id"))
    return rows_seen_count


def _process_caption_post(post: dict[str, Any]) -> None:
    post_id = post["id"]
    batch_id = post.get("batch_id")
    correlation_id = f"caption_{post_id}"
    video_url = post.get("video_url", "")
    existing_metadata = post.get("video_metadata") or {}

    supabase = get_supabase().client
    storage = get_storage_client()
    deepgram = get_deepgram_client()

    supabase.table("posts").update({
        "video_status": VIDEO_STATUS_CAPTION_PROCESSING,
    }).eq("id", post_id).execute()

    video_tmp_path = None
    output_path = None

    try:
        logger.info("caption_download_start", correlation_id=correlation_id)
        video_bytes = storage.download_video(video_url=video_url, correlation_id=correlation_id)

        transcript = deepgram.transcribe(audio_bytes=video_bytes, correlation_id=correlation_id)

        # Align Deepgram transcription to the known script to fix misspellings
        seed_data = post.get("seed_data") or {}
        original_script = seed_data.get("script") or seed_data.get("dialog_script") or ""
        if original_script and transcript.words:
            transcript = align_transcript_to_script(
                transcript=transcript,
                script=original_script,
            )
            logger.info(
                "caption_transcript_aligned",
                correlation_id=correlation_id,
                post_id=post_id,
                aligned_word_count=len(transcript.words),
            )

        if not transcript.words:
            logger.warning("caption_empty_transcript", correlation_id=correlation_id, post_id=post_id)
            _mark_caption_completed(
                post_id=post_id, existing_metadata=existing_metadata,
                caption_metadata={}, correlation_id=correlation_id,
            )
            _check_batch_caption_complete(batch_id, correlation_id)
            return

        video_fd, video_tmp_path = tempfile.mkstemp(suffix=".mp4")
        os.close(video_fd)
        with open(video_tmp_path, "wb") as f:
            f.write(video_bytes)

        output_path = burn_captions(
            video_path=video_tmp_path, transcript=transcript, correlation_id=correlation_id,
        )

        with open(output_path, "rb") as f:
            captioned_bytes = f.read()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        file_name = f"captioned_{timestamp}_{post_id}.mp4"

        upload_result = storage.upload_video(
            video_bytes=captioned_bytes, file_name=file_name, correlation_id=correlation_id,
        )

        caption_metadata = {
            "caption_video_url": upload_result["url"],
            "caption_video_key": upload_result["storage_key"],
            "caption_video_size": upload_result.get("size"),
            "captioned_at": datetime.now(timezone.utc).isoformat(),
            "caption_word_count": len(transcript.words),
        }

        _mark_caption_completed(
            post_id=post_id, existing_metadata=existing_metadata,
            caption_metadata=caption_metadata, correlation_id=correlation_id,
        )
        _check_batch_caption_complete(batch_id, correlation_id)

    except (DeepgramError, CaptionRendererError) as exc:
        _handle_caption_failure(
            post_id=post_id, existing_metadata=existing_metadata,
            error=exc, correlation_id=correlation_id,
        )

    finally:
        for path in (video_tmp_path, output_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _mark_caption_completed(*, post_id, existing_metadata, caption_metadata, correlation_id):
    supabase = get_supabase().client
    merged = {**existing_metadata, **caption_metadata}
    supabase.table("posts").update({
        "video_status": VIDEO_STATUS_CAPTION_COMPLETED,
        "video_metadata": merged,
    }).eq("id", post_id).execute()
    logger.info("caption_completed", correlation_id=correlation_id, post_id=post_id)


def _handle_caption_failure(*, post_id, existing_metadata, error, correlation_id):
    supabase = get_supabase().client
    retry_count = existing_metadata.get("caption_retry_count", 0) + 1
    transient = getattr(error, "transient", False)

    if transient and retry_count < MAX_CAPTION_RETRIES:
        merged = {**existing_metadata, "caption_retry_count": retry_count}
        supabase.table("posts").update({
            "video_status": VIDEO_STATUS_CAPTION_PENDING,
            "video_metadata": merged,
        }).eq("id", post_id).execute()
        logger.warning("caption_retry_scheduled", correlation_id=correlation_id, retry_count=retry_count, error=str(error))
    else:
        merged = {
            **existing_metadata,
            "caption_retry_count": retry_count,
            "caption_error": str(error),
            "caption_failed_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("posts").update({
            "video_status": VIDEO_STATUS_CAPTION_FAILED,
            "video_metadata": merged,
        }).eq("id", post_id).execute()
        logger.error("caption_failed_permanently", correlation_id=correlation_id, retry_count=retry_count, error=str(error))


def _check_batch_caption_complete(batch_id, correlation_id):
    if not batch_id:
        return
    reconcile_batch_video_pipeline_state(
        batch_id=batch_id,
        correlation_id=correlation_id,
    )


def main():
    configure_logging()
    settings = get_settings()
    logger.info("caption_worker_starting", environment=settings.environment)
    while True:
        try:
            rows_seen_count = poll_caption_pending()
        except KeyboardInterrupt:
            logger.info("caption_worker_shutdown")
            break
        except Exception:
            logger.exception("caption_worker_poll_error")
            rows_seen_count = 1
        time.sleep(_caption_worker_sleep_seconds(rows_seen_count))


if __name__ == "__main__":
    main()
