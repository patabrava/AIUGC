"""
Video Prompt Audit Trail
Records the exact prompt sent to video generation providers for debugging.
"""

from typing import Optional

from app.adapters.supabase_client import get_supabase
from app.core.logging import get_logger

logger = get_logger(__name__)


def record_prompt_audit(
    *,
    post_id: str,
    operation_id: str,
    provider: str,
    prompt_text: str,
    negative_prompt: Optional[str],
    prompt_path: str,
    aspect_ratio: str,
    resolution: str,
    requested_seconds: int,
    correlation_id: str,
    batch_id: Optional[str] = None,
    seed: Optional[int] = None,
) -> None:
    """Record the prompt sent to a video provider for audit/debugging.

    Non-blocking: logs a warning on failure but does not raise.
    """
    row = {
        "post_id": post_id,
        "batch_id": batch_id,
        "operation_id": operation_id,
        "provider": provider,
        "prompt_text": prompt_text,
        "negative_prompt": negative_prompt,
        "prompt_path": prompt_path,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "requested_seconds": requested_seconds,
        "correlation_id": correlation_id,
    }
    if seed is not None:
        row["seed"] = seed
    try:
        supabase = get_supabase().client
        supabase.table("video_prompt_audit").insert(row).execute()
        logger.info(
            "prompt_audit_recorded",
            post_id=post_id,
            operation_id=operation_id,
            prompt_path=prompt_path,
            prompt_length=len(prompt_text),
        )
    except Exception as e:
        logger.warning(
            "prompt_audit_failed",
            post_id=post_id,
            operation_id=operation_id,
            error=str(e),
        )
