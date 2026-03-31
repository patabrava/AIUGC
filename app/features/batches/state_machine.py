"""Batch state reconciliation for the prompt -> video -> QA pipeline."""

from __future__ import annotations

import json
from typing import Any, Optional

from app.adapters.supabase_client import get_supabase
from app.core.logging import get_logger
from app.core.states import BatchState
from app.core.video_profiles import VIDEO_STATUS_CAPTION_COMPLETED

logger = get_logger(__name__)


def _normalize_seed_data(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def reconcile_batch_video_pipeline_state(
    *,
    batch_id: str,
    correlation_id: str,
    supabase_client=None,
) -> Optional[str]:
    """Advance stale batches through prompt/video milestones based on active post state."""
    supabase = supabase_client or get_supabase().client

    batch_response = supabase.table("batches").select("state").eq("id", batch_id).execute()
    if not batch_response.data:
        logger.warning(
            "batch_not_found_for_video_pipeline_reconcile",
            batch_id=batch_id,
            correlation_id=correlation_id,
        )
        return None

    current_state = batch_response.data[0].get("state")
    if current_state not in {
        BatchState.S4_SCRIPTED.value,
        BatchState.S5_PROMPTS_BUILT.value,
        BatchState.S6_QA.value,
    }:
        return current_state

    posts_response = (
        supabase.table("posts")
        .select("id, video_prompt_json, video_status, seed_data")
        .eq("batch_id", batch_id)
        .execute()
    )
    posts = posts_response.data or []
    if not posts:
        logger.warning(
            "no_posts_for_video_pipeline_reconcile",
            batch_id=batch_id,
            correlation_id=correlation_id,
        )
        return current_state

    active_posts = []
    for post in posts:
        seed_data = _normalize_seed_data(post.get("seed_data"))
        if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
            continue
        active_posts.append(post)

    if not active_posts:
        logger.warning(
            "no_active_posts_for_video_pipeline_reconcile",
            batch_id=batch_id,
            correlation_id=correlation_id,
        )
        return current_state

    prompts_ready = all(post.get("video_prompt_json") for post in active_posts)
    videos_ready = all(post.get("video_status") == VIDEO_STATUS_CAPTION_COMPLETED for post in active_posts)

    if current_state == BatchState.S4_SCRIPTED.value and prompts_ready:
        supabase.table("batches").update({"state": BatchState.S5_PROMPTS_BUILT.value}).eq("id", batch_id).execute()
        current_state = BatchState.S5_PROMPTS_BUILT.value
        logger.info(
            "batch_transitioned_to_prompts_built",
            batch_id=batch_id,
            correlation_id=correlation_id,
            new_state=current_state,
            active_posts=len(active_posts),
        )

    if current_state == BatchState.S5_PROMPTS_BUILT.value and videos_ready:
        supabase.table("batches").update({"state": BatchState.S6_QA.value}).eq("id", batch_id).execute()
        current_state = BatchState.S6_QA.value
        logger.info(
            "batch_transitioned_to_qa",
            batch_id=batch_id,
            correlation_id=correlation_id,
            new_state=current_state,
            active_posts=len(active_posts),
        )

    logger.debug(
        "batch_video_pipeline_reconciled",
        batch_id=batch_id,
        correlation_id=correlation_id,
        state=current_state,
        prompts_ready=prompts_ready,
        videos_ready=videos_ready,
        active_posts=len(active_posts),
    )
    return current_state
